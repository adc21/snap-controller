"""
app/ui/envelope_chart_widget.py
エンベロープ（包絡）チャートウィジェット。

UX改善（第10回⑤）: 応答指標 ◄/► ナビゲーションボタン + 最大応答ケース強調表示追加。
  コンボボックスの両隣に ◄ / ► ボタンを追加し、ドロップダウンを開かずに
  前後の応答指標へ素早く切り替えられるようにします（result_chart_widget と
  統一したデザイン言語）。
  また、エンベロープグラフの下部に「最大応答発生ケース」ラベルを追加し、
  現在の指標で最も高い値を出したケース名・層・値を常時表示します。
  設計者が「誰が最も危ない結果を出したか」を視覚化から即座に把握できます。

全完了ケースの応答値の最小・最大範囲を塗りつぶし領域で表示し、
平均値を折れ線で重ねます。応答値のばらつきを一目で把握できます。

レイアウト:
  ┌──────────────────────────────────────┐
  │ [表示項目コンボ]  [基準線CB]          │
  │ matplotlib グラフ（塗りつぶし領域）   │
  └──────────────────────────────────────┘

UX改善:
  改善A: グラフ画像クリップボードコピーボタン（📋）を追加。
  改善B: Matplotlibナビゲーションツールバーを追加（ズーム・パン・保存）。

UX改善（新④）: 最大応答発生層（危険層）自動ハイライト + アノテーション追加。
  全ケースの最大応答値が最も大きい層（危険層）を自動的に特定し、
  グラフ上に水平破線と「🔴 最大応答: X層」のテキスト注釈を表示します。
  また、グラフ下部のサマリーラベルに危険層と最大エンベロープ値を常時表示します。
  構造設計者はどの層が最も厳しい条件下に置かれているかを一目で把握でき、
  ダンパー配置の優先層を素早く判断できます。
  - 最大応答は「maxs」配列（全ケースの最大値）で判定します
  - 危険層の水平線は赤色破線（-.）で表示
  - グラフ下にサマリーラベルを追加（_critical_floor_label）
"""

from __future__ import annotations

from io import BytesIO

from typing import Dict, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "sans-serif"]
except Exception:
    logging.getLogger(__name__).debug("日本語フォント設定失敗")

import logging

from app.models import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria
from .theme import ThemeManager, MPL_STYLES

logger = logging.getLogger(__name__)

def _get_floor0_value(key: str, result_data: dict) -> float:
    """0層（地盤面）にプロットする値を返します。"""
    if key == "max_acc":
        pga = result_data.get("input_pga")
        return float(pga) if pga is not None else 0.0
    if key == "max_otm":
        base = result_data.get("base_otm")
        return float(base) if base is not None else 0.0
    return 0.0


# 応答値の定義 (key, 日本語ラベル, 単位)
_RESPONSE_ITEMS = [
    ("max_disp",        "最大応答相対変位",    "m"),
    ("max_vel",         "最大応答相対速度",    "m/s"),
    ("max_acc",         "最大応答絶対加速度",  "m/s²"),
    ("max_story_disp",  "最大層間変形",        "m"),
    ("max_story_drift", "最大層間変形角",      "rad"),
    ("shear_coeff",     "せん断力係数",        "—"),
    ("max_otm",         "最大転倒モーメント",  "kN·m"),
]

# グラフ応答値キー → 性能基準キーのマッピング
_CHART_KEY_TO_CRITERIA_KEY = {
    "max_disp": "max_disp",
    "max_vel": "max_vel",
    "max_acc": "max_acc",
    "max_story_disp": "max_story_disp",
    "max_story_drift": "max_drift",
    "shear_coeff": "shear_coeff",
    "max_otm": "max_otm",
}


def _apply_mpl_theme() -> None:
    """matplotlib の rcParams に現在のテーマを適用します。"""
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _MplCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(6, 4), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

    def apply_theme(self) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class EnvelopeChartWidget(QWidget):
    """
    全完了ケースの応答値の包絡（エンベロープ）を表示するウィジェット。

    Public API
    ----------
    set_cases(cases)      — 全ケースリストをセットします
    set_criteria(criteria) — 性能基準を設定します（基準線表示用）
    refresh()             — グラフを再描画します
    update_theme()        — テーマ変更に追従します
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_cases: List[AnalysisCase] = []
        self._criteria: Optional[PerformanceCriteria] = None
        self._show_criteria: bool = True
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """全ケースリストをセットして再描画します。"""
        self._all_cases = cases
        self.refresh()

    def set_criteria(self, criteria: Optional[PerformanceCriteria]) -> None:
        """目標性能基準を設定します。"""
        self._criteria = criteria
        self.refresh()

    def refresh(self) -> None:
        """現在のケースデータでグラフを再描画します。"""
        self._draw()

    def update_theme(self) -> None:
        """テーマ変更時にグラフの色を更新します。"""
        self._canvas.apply_theme()
        self._draw()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- コントロール行 ---
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("表示項目:"))

        # UX改善（第10回⑤）: ◄ 前の指標ボタン
        self._btn_prev_metric = QPushButton("◄")
        self._btn_prev_metric.setFixedSize(28, 24)
        self._btn_prev_metric.setToolTip(
            "前の応答指標を表示します（循環）"
        )
        self._btn_prev_metric.setStyleSheet("font-size: 11px; padding: 1px 4px;")
        self._btn_prev_metric.clicked.connect(self._prev_metric)
        ctrl_row.addWidget(self._btn_prev_metric)

        self._combo = QComboBox()
        for _, label, unit in _RESPONSE_ITEMS:
            self._combo.addItem(f"{label}  [{unit}]")
        self._combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._combo)

        # UX改善（第10回⑤）: ► 次の指標ボタン
        self._btn_next_metric = QPushButton("►")
        self._btn_next_metric.setFixedSize(28, 24)
        self._btn_next_metric.setToolTip(
            "次の応答指標を表示します（循環）"
        )
        self._btn_next_metric.setStyleSheet("font-size: 11px; padding: 1px 4px;")
        self._btn_next_metric.clicked.connect(self._next_metric)
        ctrl_row.addWidget(self._btn_next_metric)

        ctrl_row.addStretch()

        # 基準線チェックボックス
        self._criteria_cb = QCheckBox("基準線")
        self._criteria_cb.setChecked(True)
        self._criteria_cb.setToolTip("目標性能基準の上限値を表示")
        self._criteria_cb.stateChanged.connect(self._on_criteria_toggle)
        ctrl_row.addWidget(self._criteria_cb)

        # 個別ケース線チェックボックス
        self._individual_cb = QCheckBox("個別ケース")
        self._individual_cb.setChecked(False)
        self._individual_cb.setToolTip("各ケースの折れ線を薄く表示")
        self._individual_cb.stateChanged.connect(self.refresh)
        ctrl_row.addWidget(self._individual_cb)

        # 改善A: グラフ画像クリップボードコピーボタン
        btn_copy_chart = QPushButton("📋 コピー")
        btn_copy_chart.setToolTip("現在のグラフをクリップボードに画像コピーします（Word・メールへ貼り付け可）")
        btn_copy_chart.setMaximumWidth(80)
        btn_copy_chart.setFixedHeight(24)
        btn_copy_chart.setStyleSheet("font-size: 11px; padding: 1px 8px;")
        btn_copy_chart.clicked.connect(self._copy_chart_to_clipboard)
        ctrl_row.addWidget(btn_copy_chart)

        layout.addLayout(ctrl_row)

        # --- グラフ（ナビゲーションツールバー付き）---
        self._canvas = _MplCanvas(self)
        # 改善B: Matplotlibナビゲーションツールバー（ズーム・パン・ホーム・保存）
        self._nav_toolbar = NavigationToolbar(self._canvas, self)
        self._nav_toolbar.setMaximumHeight(30)
        layout.addWidget(self._nav_toolbar)
        layout.addWidget(self._canvas, stretch=1)

        # UX改善（新④）: 危険層サマリーラベル
        self._critical_floor_label = QLabel("")
        self._critical_floor_label.setStyleSheet(
            "color: #b71c1c; font-size: 11px; font-weight: bold; padding: 2px 4px;"
        )
        layout.addWidget(self._critical_floor_label)

        # UX改善（第10回⑤）: 最大応答発生ケースラベル
        self._worst_case_lbl = QLabel("")
        self._worst_case_lbl.setStyleSheet(
            "color: #e65100; font-size: 10px; padding: 2px 4px;"
            "background-color: #fff3e0; border-radius: 3px;"
        )
        self._worst_case_lbl.setVisible(False)
        layout.addWidget(self._worst_case_lbl)

        # 初期表示
        self._show_empty()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_criteria_toggle(self, state: int) -> None:
        self._show_criteria = bool(state)
        self.refresh()

    # UX改善（第10回⑤）: 前後指標ナビゲーション
    def _prev_metric(self) -> None:
        """◄ ボタン: 前の応答指標に切り替えます。"""
        n = self._combo.count()
        if n > 0:
            self._combo.setCurrentIndex((self._combo.currentIndex() - 1) % n)

    def _next_metric(self) -> None:
        """► ボタン: 次の応答指標に切り替えます。"""
        n = self._combo.count()
        if n > 0:
            self._combo.setCurrentIndex((self._combo.currentIndex() + 1) % n)

    def _copy_chart_to_clipboard(self) -> None:
        """現在のエンベロープグラフをPNG画像としてクリップボードにコピーします。"""
        try:
            buf = BytesIO()
            self._canvas.fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            img = QImage.fromData(buf.read(), "PNG")
            if not img.isNull():
                QApplication.clipboard().setImage(img)
                parent = self.parent()
                while parent is not None:
                    if hasattr(parent, "statusBar"):
                        parent.statusBar().showMessage("エンベロープグラフをクリップボードにコピーしました", 3000)
                        break
                    parent = parent.parent()
        except Exception:
            logger.debug("グラフコピー時のステータスバー更新失敗", exc_info=True)

    def _get_completed_cases(self) -> List[AnalysisCase]:
        """完了済みかつ結果を持つケースを返します。"""
        return [
            c for c in self._all_cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]

    def _collect_floor_data(
        self, cases: List[AnalysisCase], key: str
    ) -> Tuple[List[int], np.ndarray, np.ndarray, np.ndarray]:
        """
        全ケースの層別データを収集し、層番号・最小・最大・平均を返します。

        Returns
        -------
        floors : list of int
        mins : np.ndarray
        maxs : np.ndarray
        means : np.ndarray
        """
        # 全ケースの floor_dict を収集
        all_floor_dicts: List[Dict[int, float]] = []
        all_result_datas: List[dict] = []
        for case in cases:
            result_data = case.result_summary.get("result_data", {})
            fd = result_data.get(key, {})
            if not fd:
                scalar = case.result_summary.get(key)
                if scalar is not None:
                    fd = {1: scalar}
            if fd:
                all_floor_dicts.append(fd)
                all_result_datas.append(result_data)

        if not all_floor_dicts:
            return [], np.array([]), np.array([]), np.array([])

        # 全層番号の集合
        all_floors = sorted(set(f for fd in all_floor_dicts for f in fd.keys()))

        # 各層の値を配列にまとめる
        mins = []
        maxs = []
        means = []
        # 0層（地盤面）を常にプロット（指標ごとに適切な値を設定）
        if 0 not in all_floors:
            all_floors.insert(0, 0)
            for fd, rd in zip(all_floor_dicts, all_result_datas):
                fd[0] = _get_floor0_value(key, rd)

        for floor in all_floors:
            vals = [fd[floor] for fd in all_floor_dicts if floor in fd]
            if vals:
                mins.append(min(vals))
                maxs.append(max(vals))
                means.append(sum(vals) / len(vals))
            else:
                mins.append(0.0)
                maxs.append(0.0)
                means.append(0.0)

        return list(all_floors), np.array(mins), np.array(maxs), np.array(means)

    def _draw(self) -> None:
        ax = self._canvas.ax
        ax.clear()

        completed = self._get_completed_cases()
        if not completed:
            self._show_empty("完了済みケースがありません")
            return

        idx = self._combo.currentIndex()
        key, label, unit = _RESPONSE_ITEMS[idx]

        floors, mins, maxs, means = self._collect_floor_data(completed, key)
        if len(floors) == 0:
            self._show_empty("選択された応答値にデータがありません")
            return

        if self._individual_cb.isChecked():
            self._draw_individual_lines(ax, completed, key)

        self._draw_envelope_bands(ax, floors, mins, maxs, means)
        self._apply_axes_labels(ax, label, unit, len(completed), floors)
        self._draw_criteria_line(ax, key)
        self._draw_critical_floor_overlay(ax, completed, floors, maxs, key, unit)

        ax.legend(fontsize=8, loc="lower right")
        self._canvas.fig.tight_layout()
        self._canvas.draw()

    @staticmethod
    def _draw_individual_lines(ax, completed, key: str) -> None:
        for case in completed:
            result_data = case.result_summary.get("result_data", {})
            fd = result_data.get(key, {})
            if not fd:
                scalar = case.result_summary.get(key)
                if scalar is not None:
                    fd = {1: scalar}
            if fd:
                fls = sorted(fd.keys())
                vals = [fd[f] for f in fls]
                ax.plot(vals, fls, color="gray", alpha=0.25, linewidth=0.8)

    @staticmethod
    def _draw_envelope_bands(ax, floors, mins, maxs, means) -> None:
        ax.fill_betweenx(
            floors, mins, maxs,
            alpha=0.3, color="#1f77b4",
            label="最小〜最大 範囲",
        )
        ax.plot(mins, floors, color="#1f77b4", linewidth=1.0,
                linestyle=":", alpha=0.7, label="最小")
        ax.plot(maxs, floors, color="#1f77b4", linewidth=1.0,
                linestyle=":", alpha=0.7, label="最大")
        ax.plot(means, floors, color="#d62728", linewidth=2.0,
                marker="o", markersize=4, label="平均")

    @staticmethod
    def _apply_axes_labels(ax, label: str, unit: str, n_cases: int, floors) -> None:
        ax.set_xlabel(f"{label}  [{unit}]", fontsize=9)
        ax.set_ylabel("層", fontsize=9)
        ax.set_title(
            f"エンベロープ — {label}  ({n_cases} ケース)",
            fontsize=10,
        )
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.4)
        ax.set_yticks(floors)

    def _draw_criteria_line(self, ax, key: str) -> None:
        if not (self._show_criteria and self._criteria is not None):
            return
        criteria_key = _CHART_KEY_TO_CRITERIA_KEY.get(key)
        if not criteria_key:
            return
        for item in self._criteria.items:
            if (item.key == criteria_key and item.enabled
                    and item.limit_value is not None):
                ax.axvline(
                    x=item.limit_value,
                    color="red",
                    linestyle="--",
                    linewidth=1.5,
                    alpha=0.8,
                    label=f"基準: {item.limit_value:.4g}",
                )
                break

    def _draw_critical_floor_overlay(
        self, ax, completed, floors, maxs, key: str, unit: str
    ) -> None:
        if len(floors) == 0 or len(maxs) == 0:
            return
        non_zero_indices = [i for i, f in enumerate(floors) if f != 0]
        if not non_zero_indices:
            self._critical_floor_label.setText("")
            self._worst_case_lbl.setVisible(False)
            return

        critical_idx = max(non_zero_indices, key=lambda i: maxs[i])
        critical_floor = floors[critical_idx]
        critical_val = maxs[critical_idx]

        ax.axhline(
            y=critical_floor,
            color="#d32f2f",
            linestyle="-.",
            linewidth=1.2,
            alpha=0.75,
            label=f"最大応答層: {critical_floor}層",
            zorder=6,
        )
        x_range = ax.get_xlim()
        x_pos = x_range[0] + (x_range[1] - x_range[0]) * 0.72 if x_range[1] != x_range[0] else critical_val
        ax.annotate(
            f"🔴 最大応答: {critical_floor}層\n({critical_val:.4g} {unit})",
            xy=(critical_val, critical_floor),
            xycoords="data",
            xytext=(x_pos, critical_floor),
            textcoords="data",
            fontsize=7,
            color="#b71c1c",
            va="center",
            ha="center",
            bbox=dict(boxstyle="round,pad=0.3", fc="#ffebee", alpha=0.85, ec="#d32f2f", lw=0.8),
            arrowprops=dict(arrowstyle="->", color="#d32f2f", lw=0.7),
        )
        self._critical_floor_label.setText(
            f"🔴 最大応答発生層: {critical_floor}層  "
            f"（最大エンベロープ: {critical_val:.4g} {unit}）  "
            f"← ダンパー優先配置を検討してください"
        )
        self._update_worst_case_label(completed, critical_floor, key, unit)

    def _update_worst_case_label(self, completed, critical_floor, key: str, unit: str) -> None:
        worst_case_name = ""
        worst_case_val = -float("inf")
        for case in completed:
            result_data = case.result_summary.get("result_data", {})
            fd = result_data.get(key, {})
            if not fd:
                scalar = case.result_summary.get(key)
                if scalar is not None:
                    fd = {1: scalar}
            v = fd.get(critical_floor)
            if v is not None and float(v) > worst_case_val:
                worst_case_val = float(v)
                worst_case_name = case.name
        if worst_case_name:
            self._worst_case_lbl.setText(
                f"⚠ {critical_floor}層の最大応答ケース: 「{worst_case_name}」"
                f"  {worst_case_val:.4g} {unit}"
            )
            self._worst_case_lbl.setVisible(True)
        else:
            self._worst_case_lbl.setVisible(False)

    def _show_empty(self, message: str = "完了済みケースが増えると\nエンベロープを表示します") -> None:
        """空状態のメッセージをキャンバスに表示します。"""
        ax = self._canvas.ax
        ax.clear()
        ax.text(
            0.5, 0.5, message,
            ha="center", va="center",
            transform=ax.transAxes,
            fontsize=11, color="gray",
        )
        ax.set_axis_off()
        self._canvas.draw()
        # UX改善（新④）: 危険層ラベルをクリア
        if hasattr(self, "_critical_floor_label"):
            self._critical_floor_label.setText("")
        # UX改善（第10回⑤）: 最大応答ケースラベルもクリア
        if hasattr(self, "_worst_case_lbl"):
            self._worst_case_lbl.setVisible(False)