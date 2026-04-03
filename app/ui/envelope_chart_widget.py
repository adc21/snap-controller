"""
app/ui/envelope_chart_widget.py
エンベロープ（包絡）チャートウィジェット。

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
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

from app.models import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria
from .theme import ThemeManager, MPL_STYLES

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
        self._combo = QComboBox()
        for _, label, unit in _RESPONSE_ITEMS:
            self._combo.addItem(f"{label}  [{unit}]")
        self._combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._combo)

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

        # 初期表示
        self._show_empty()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_criteria_toggle(self, state: int) -> None:
        self._show_criteria = bool(state)
        self.refresh()

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
            pass

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

        # 個別ケース線（オプション）
        if self._individual_cb.isChecked():
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

        # 包絡領域（fill_betweenx で水平方向に塗りつぶし）
        ax.fill_betweenx(
            floors, mins, maxs,
            alpha=0.3, color="#1f77b4",
            label="最小〜最大 範囲",
        )

        # 最小・最大の境界線
        ax.plot(mins, floors, color="#1f77b4", linewidth=1.0,
                linestyle=":", alpha=0.7, label="最小")
        ax.plot(maxs, floors, color="#1f77b4", linewidth=1.0,
                linestyle=":", alpha=0.7, label="最大")

        # 平均線
        ax.plot(means, floors, color="#d62728", linewidth=2.0,
                marker="o", markersize=4, label="平均")

        ax.set_xlabel(f"{label}  [{unit}]", fontsize=9)
        ax.set_ylabel("層", fontsize=9)
        ax.set_title(
            f"エンベロープ — {label}  ({len(completed)} ケース)",
            fontsize=10,
        )
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.4)
        ax.set_yticks(floors)

        # --- 性能基準線 ---
        if self._show_criteria and self._criteria is not None:
            criteria_key = _CHART_KEY_TO_CRITERIA_KEY.get(key)
            if criteria_key:
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

        ax.legend(fontsize=8, loc="lower right")
        self._canvas.fig.tight_layout()
        self._canvas.draw()

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