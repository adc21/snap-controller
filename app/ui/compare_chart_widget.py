"""
app/ui/compare_chart_widget.py
複数ケース比較グラフウィジェット。

BinaryResultWidget の左パネルで選択されたケースを同一グラフに重ねて表示します。
ケース選択は BinaryResultWidget 側で統一管理し、本ウィジェットは描画に専念します。

レイアウト:
  ┌──────────────────────────────────────┐
  │ [表示項目コンボ] [基準線] [コピー]    │
  │ [比較基準ケース]                      │
  │ matplotlib グラフ（フル幅）           │
  └──────────────────────────────────────┘
"""

from __future__ import annotations

from io import BytesIO

from typing import List, Optional

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
    """
    0層（地盤面）にプロットする値を返します。

    - max_acc: 入力地震動の最大加速度（PGA）
    - max_otm: 基部転倒モーメント
    - その他: 0.0（地盤面では変位・速度・層間変形 = 0）
    """
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

_RESPONSE_DESCRIPTIONS = {
    "max_disp": "📐 各層の地面に対する水平変位の最大値。",
    "max_vel": "💨 各層の地面に対する相対速度の最大値。",
    "max_acc": "⚡ 各層の絶対加速度の最大値。",
    "max_story_disp": "📏 上下の層間の相対水平変位の最大値。",
    "max_story_drift": "📐 層間変形 ÷ 層高 の最大値 [rad]。",
    "shear_coeff": "⚖ 各層のせん断力 ÷ その層より上の重量。",
    "max_otm": "🏗 建物基部の転倒モーメントの最大値 [kN·m]。",
}

_RESPONSE_GUIDELINES = {}

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

# ケースごとのカラーサイクル
_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]


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
        """テーマ変更時にキャンバスの色を更新します。"""
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class CompareChartWidget(QWidget):
    """
    複数ケースを重ねて比較するグラフウィジェット。

    ケース選択は親の BinaryResultWidget 左パネルで行い、
    本ウィジェットは選択済みケースのグラフ描画に専念します。

    Public API
    ----------
    set_active_cases(cases) — 表示するケースを外部から設定します
    set_cases(cases)        — 全ケースリストをセット（ベースライン候補用）
    set_criteria(criteria)  — 性能基準を設定します
    refresh()               — 現在の状態でグラフを再描画します
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_cases: List[AnalysisCase] = []
        self._active_cases: List[AnalysisCase] = []
        self._criteria: Optional[PerformanceCriteria] = None
        self._show_criteria: bool = True
        self._baseline_case_id: Optional[str] = None
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_criteria(self, criteria: Optional[PerformanceCriteria]) -> None:
        """目標性能基準を設定します。比較グラフに基準線が表示されます。"""
        self._criteria = criteria
        self.refresh()

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """全ケースリストをセットします（ベースライン候補の更新用）。"""
        self._all_cases = cases
        self._update_baseline_combo()

    def set_active_cases(self, cases: List[AnalysisCase]) -> None:
        """外部（BinaryResultWidget）から表示するケースを設定します。"""
        self._active_cases = [
            c for c in (cases or [])
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        self._update_baseline_combo()
        self.refresh()

    def refresh(self) -> None:
        """現在の選択状態でグラフを再描画します。"""
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

        layout.addLayout(self._build_ctrl_row())
        self._build_metric_desc(layout)
        self._build_baseline_row(layout)
        self._update_metric_description(0)
        self._build_main_area(layout)

        # 初期描画（空状態パネルを表示）
        self._chart_stack.setCurrentIndex(0)

    def _build_ctrl_row(self) -> QHBoxLayout:
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("表示項目:"))
        self._combo = QComboBox()
        for _, label, unit in _RESPONSE_ITEMS:
            self._combo.addItem(f"{label}  [{unit}]")
        self._combo.currentIndexChanged.connect(self.refresh)
        self._combo.currentIndexChanged.connect(self._update_metric_description)
        ctrl_row.addWidget(self._combo)
        ctrl_row.addStretch()

        self._criteria_cb = QCheckBox("基準線")
        self._criteria_cb.setChecked(True)
        self._criteria_cb.setToolTip("目標性能基準の上限値をグラフに表示")
        self._criteria_cb.stateChanged.connect(self._on_criteria_toggle)
        ctrl_row.addWidget(self._criteria_cb)

        btn_copy_chart = QPushButton("📋 コピー")
        btn_copy_chart.setToolTip("現在の比較グラフをクリップボードに画像コピーします（Word・メールへ貼り付け可）")
        btn_copy_chart.setMaximumWidth(80)
        btn_copy_chart.setFixedHeight(24)
        btn_copy_chart.setStyleSheet("font-size: 11px; padding: 1px 8px;")
        btn_copy_chart.clicked.connect(self._copy_chart_to_clipboard)
        ctrl_row.addWidget(btn_copy_chart)

        return ctrl_row

    def _build_metric_desc(self, layout: QVBoxLayout) -> None:
        self._metric_desc_label = QLabel()
        self._metric_desc_label.setWordWrap(True)
        self._metric_desc_label.setStyleSheet(
            "color: #888888; font-size: 10px; padding: 2px 6px 2px 6px;"
            "background-color: palette(alternate-base); border-radius: 3px;"
        )
        self._metric_desc_label.setTextFormat(Qt.PlainText)
        self._update_metric_description(0)
        layout.addWidget(self._metric_desc_label)

    def _build_baseline_row(self, layout: QVBoxLayout) -> None:
        baseline_row = QHBoxLayout()
        baseline_row.setSpacing(6)
        _baseline_lbl = QLabel("📌 比較基準ケース:")
        _baseline_lbl.setStyleSheet("font-size: 11px; color: #555;")
        baseline_row.addWidget(_baseline_lbl)
        self._baseline_combo = QComboBox()
        self._baseline_combo.setMaximumWidth(220)
        self._baseline_combo.setToolTip(
            "選択したケースをベースラインとして、\n"
            "各ケースの凡例に改善率（例: -23.5%）を追加表示します。\n"
            "「なし（改善率非表示）」を選ぶと凡例ラベルは通常表示になります。"
        )
        self._baseline_combo.addItem("なし（改善率非表示）", userData=None)
        self._baseline_combo.currentIndexChanged.connect(self._on_baseline_changed)
        baseline_row.addWidget(self._baseline_combo)
        baseline_row.addStretch()
        layout.addLayout(baseline_row)

    def _build_chart_area(self) -> QWidget:
        from PySide6.QtWidgets import QStackedWidget as _QSW

        chart_area = QWidget()
        chart_area_layout = QVBoxLayout(chart_area)
        chart_area_layout.setContentsMargins(0, 0, 0, 0)
        chart_area_layout.setSpacing(0)
        self._canvas = _MplCanvas(self)
        self._nav_toolbar = NavigationToolbar(self._canvas, self)
        self._nav_toolbar.setMaximumHeight(30)
        chart_area_layout.addWidget(self._nav_toolbar)

        self._chart_stack = _QSW()

        # 空状態パネル
        _empty_panel = QWidget()
        _ep_layout = QVBoxLayout(_empty_panel)
        _ep_layout.setAlignment(Qt.AlignCenter)
        _ep_layout.setSpacing(8)

        _ep_icon = QLabel("📊")
        _ep_icon.setAlignment(Qt.AlignCenter)
        _ep_icon.setStyleSheet("font-size: 48px; padding: 8px;")
        _ep_layout.addWidget(_ep_icon)

        self._empty_panel_title = QLabel("比較するケースを選択してください")
        _title_font = self._empty_panel_title.font()
        _title_font.setPointSize(13)
        _title_font.setBold(True)
        self._empty_panel_title.setFont(_title_font)
        self._empty_panel_title.setAlignment(Qt.AlignCenter)
        _ep_layout.addWidget(self._empty_panel_title)

        _ep_desc = QLabel(
            "← 左の解析ケースリストで\n"
            "ケースを選択するとグラフが表示されます。\n\n"
            "「全選択」ボタンで全ケースを一括選択できます。"
        )
        _ep_desc.setAlignment(Qt.AlignCenter)
        _ep_desc.setWordWrap(True)
        _ep_desc.setStyleSheet("color: gray; font-size: 11px; padding: 4px 24px;")
        _ep_layout.addWidget(_ep_desc)

        self._chart_stack.addWidget(_empty_panel)   # index 0: 空状態
        self._chart_stack.addWidget(self._canvas)   # index 1: グラフ

        chart_area_layout.addWidget(self._chart_stack, stretch=1)
        return chart_area

    def _build_main_area(self, layout: QVBoxLayout) -> None:
        layout.addWidget(self._build_chart_area(), stretch=1)

    # ------------------------------------------------------------------
    # UX改善（新①）: 指標説明ラベル更新
    # ------------------------------------------------------------------

    def _update_metric_description(self, index: int = -1) -> None:
        """
        UX改善（新①）: コンボで選択中の応答値指標の説明をラベルに反映します。
        UX改善④（新）: 建築基準値ガイドラインラベルも同時に更新します。

        指標名・単位だけでは分かりにくい建築的意義を1〜2行で補足することで、
        SNAP の専門用語に不慣れなユーザーでも迷わず指標を選べるようにします。
        """
        if not hasattr(self, "_metric_desc_label"):
            return
        idx = self._combo.currentIndex()
        if 0 <= idx < len(_RESPONSE_ITEMS):
            key = _RESPONSE_ITEMS[idx][0]
            desc = _RESPONSE_DESCRIPTIONS.get(key, "")
            self._metric_desc_label.setText(desc)
            self._metric_desc_label.setVisible(bool(desc))

        else:
            self._metric_desc_label.setVisible(False)

    # ------------------------------------------------------------------
    # ベースラインケース選択 + 改善率凡例表示
    # ------------------------------------------------------------------

    def _update_baseline_combo(self) -> None:
        """
        アクティブケースリストに合わせてベースラインコンボボックスを更新します。
        既存の選択（ケースID）を維持します。
        """
        if not hasattr(self, "_baseline_combo"):
            return
        prev_id = self._baseline_combo.currentData()
        self._baseline_combo.blockSignals(True)
        self._baseline_combo.clear()
        self._baseline_combo.addItem("なし（改善率非表示）", userData=None)
        for case in self._active_cases:
            self._baseline_combo.addItem(f"📌 {case.name}", userData=case.id)
        # 以前の選択を復元
        restored = False
        if prev_id is not None:
            for i in range(self._baseline_combo.count()):
                if self._baseline_combo.itemData(i) == prev_id:
                    self._baseline_combo.setCurrentIndex(i)
                    restored = True
                    break
        if not restored:
            self._baseline_combo.setCurrentIndex(0)
        self._baseline_combo.blockSignals(False)
        self._baseline_case_id = self._baseline_combo.currentData()

    def _on_baseline_changed(self, _: int) -> None:
        """ベースラインケース変更時にグラフを再描画します。"""
        self._baseline_case_id = self._baseline_combo.currentData() if hasattr(self, "_baseline_combo") else None
        self._draw()

    def _on_criteria_toggle(self, state: int) -> None:
        """基準線表示のオン・オフ切替。"""
        self._show_criteria = bool(state)
        self.refresh()

    # ------------------------------------------------------------------
    # 改善A: グラフ画像クリップボードコピー
    # ------------------------------------------------------------------

    def _copy_chart_to_clipboard(self) -> None:
        """現在の比較グラフをPNG画像としてクリップボードにコピーします。

        Word・PowerPoint・メールクライアントなど任意のアプリケーションに
        そのまま Ctrl+V で貼り付けることができます。
        """
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
                        parent.statusBar().showMessage("比較グラフをクリップボードにコピーしました", 3000)
                        break
                    parent = parent.parent()
        except Exception:
            logger.debug("グラフコピー時のステータスバー更新失敗", exc_info=True)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self) -> None:
        selected = [(case, i) for i, case in enumerate(self._active_cases)]

        ax = self._canvas.ax
        ax.clear()

        if not selected:
            self._show_empty()
            return

        # UX改善（新）: データあり → canvas ページに切替
        if hasattr(self, "_chart_stack"):
            self._chart_stack.setCurrentIndex(1)

        idx = self._combo.currentIndex()
        key, label, unit = _RESPONSE_ITEMS[idx]

        case_max_values, best_case_id = self._compute_best_case(selected, key)
        baseline_id = getattr(self, "_baseline_case_id", None)
        baseline_max: Optional[float] = case_max_values.get(baseline_id) if baseline_id else None

        has_data = False
        for case, color_idx in selected:
            floors, values = self._extract_floor_data(case, key)
            if floors is None:
                continue

            improve_suffix = self._compute_improve_suffix(
                case, baseline_id, baseline_max, case_max_values)
            plot_style = self._resolve_plot_style(
                case, color_idx, best_case_id, baseline_id, improve_suffix)

            ax.plot(values, floors,
                    marker=plot_style["mk"], markersize=plot_style["mks"],
                    label=plot_style["legend"], color=plot_style["color"],
                    linewidth=plot_style["lw"], zorder=plot_style["zorder"])

            self._annotate_peak(ax, floors, values, color_idx,
                                plot_style["color"], plot_style["is_best"])
            has_data = True

        if not has_data:
            self._show_empty("選択されたケースにデータがありません")
            return

        self._finalize_axes(ax, selected, key, label, unit)

    # -- _draw サブメソッド群 ------------------------------------------

    @staticmethod
    def _compute_best_case(selected, key):
        """各ケースの指標最大値を計算し、最良ケースIDを特定する。"""
        case_max_values: dict = {}
        for case, _ in selected:
            result_data = case.result_summary.get("result_data", {})
            floor_dict = result_data.get(key, {})
            if not floor_dict:
                scalar = case.result_summary.get(key)
                if scalar is not None:
                    floor_dict = {1: scalar}
            if floor_dict:
                case_max_values[case.id] = max(floor_dict.values())
        best_case_id = None
        if len(case_max_values) >= 2:
            best_case_id = min(case_max_values, key=case_max_values.__getitem__)
        return case_max_values, best_case_id

    @staticmethod
    def _extract_floor_data(case, key):
        """ケースから階別データを抽出し (floors, values) を返す。データなしなら (None, None)。"""
        result_data = case.result_summary.get("result_data", {})
        floor_dict = result_data.get(key, {})
        if not floor_dict:
            scalar = case.result_summary.get(key)
            if scalar is not None:
                floor_dict = {1: scalar}
        if not floor_dict:
            return None, None
        if 0 not in floor_dict:
            floor0_val = _get_floor0_value(key, result_data)
            floor_dict = {0: floor0_val, **floor_dict}
        floors = sorted(floor_dict.keys())
        values = [floor_dict[f] for f in floors]
        return floors, values

    @staticmethod
    def _compute_improve_suffix(case, baseline_id, baseline_max, case_max_values):
        """ベースライン比の改善率サフィックス文字列を計算する。"""
        if baseline_max is None or baseline_max == 0 or case.id == baseline_id:
            return ""
        case_max = case_max_values.get(case.id)
        if case_max is None:
            return ""
        pct = (case_max - baseline_max) / abs(baseline_max) * 100.0
        sign = "+" if pct >= 0 else ""
        return f" ({sign}{pct:.1f}%)"

    @staticmethod
    def _resolve_plot_style(case, color_idx, best_case_id, baseline_id, improve_suffix):
        """ケースの描画スタイル(色・線幅・マーカー・凡例ラベル)を決定する。"""
        is_best = (best_case_id is not None and case.id == best_case_id)
        is_baseline = (baseline_id is not None and case.id == baseline_id)
        if is_best:
            return dict(color="#FFD700", lw=2.8, mk="*", mks=10, zorder=10,
                        legend=f"🏆 {case.name}（最良）{improve_suffix}",
                        is_best=True)
        if is_baseline:
            return dict(color=_COLORS[color_idx % len(_COLORS)], lw=2.2, mk="D",
                        mks=6, zorder=8, legend=f"📌 {case.name}（基準）",
                        is_best=False)
        return dict(color=_COLORS[color_idx % len(_COLORS)], lw=1.5, mk="o",
                    mks=5, zorder=5, legend=f"{case.name}{improve_suffix}",
                    is_best=False)

    @staticmethod
    def _annotate_peak(ax, floors, values, color_idx, plot_color, is_best):
        """ケースの最大応答値にアノテーションを描画する。"""
        if not values:
            return
        max_val = max(values)
        max_floor = floors[values.index(max_val)]
        if is_best:
            ax.annotate(
                f"最良\n{max_val:.4g}", xy=(max_val, max_floor),
                xytext=(8, 4), textcoords="offset points",
                fontsize=7, color="#FFD700", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2",
                          facecolor="#333333" if ThemeManager.is_dark() else "#fffde7",
                          edgecolor="#FFD700", alpha=0.85))
        else:
            y_offset = 6 if (color_idx % 2 == 0) else -14
            ax.annotate(
                f"{max_val:.4g}", xy=(max_val, max_floor),
                xytext=(-2, y_offset), textcoords="offset points",
                fontsize=7, color=plot_color,
                bbox=dict(boxstyle="round,pad=0.15",
                          facecolor="#2b2b2b" if ThemeManager.is_dark() else "#ffffff",
                          edgecolor=plot_color, alpha=0.75),
                arrowprops=dict(arrowstyle="-", color=plot_color, alpha=0.5, lw=0.8))

    def _finalize_axes(self, ax, selected, key, label, unit):
        """軸ラベル・タイトル・凡例・基準線を設定し描画を確定する。"""
        ax.set_xlabel(f"{label}  [{unit}]", fontsize=9)
        ax.set_ylabel("層", fontsize=9)
        ax.set_title(f"ケース比較 — {label}", fontsize=10)
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.4)
        self._draw_criteria_line(ax, key)
        ax.legend(fontsize=8, loc="best")
        y_ticks = sorted({f for case, _ in selected
                          for f in case.result_summary.get("result_data", {}).get(key, {}).keys()})
        if y_ticks:
            ax.set_yticks(y_ticks)
        self._canvas.fig.tight_layout()
        self._canvas.draw()

    def _draw_criteria_line(self, ax, chart_key: str) -> None:
        """現在の性能基準に基づいて、グラフ上に縦の基準線を描画します。"""
        if not self._show_criteria or self._criteria is None:
            return
        criteria_key = _CHART_KEY_TO_CRITERIA_KEY.get(chart_key)
        if criteria_key is None:
            return
        for item in self._criteria.items:
            if item.key == criteria_key and item.enabled and item.limit_value is not None:
                ax.axvline(
                    x=item.limit_value,
                    color="red",
                    linestyle="--",
                    linewidth=1.5,
                    alpha=0.8,
                    label=f"基準: {item.limit_value:.4g}",
                )
                break

    def _show_empty(self, msg: str = "← 左のリストでケースを選択してください") -> None:
        """
        UX改善（新）: 空状態を matplotlib のグレーテキストではなく
        視認性の高い Qt パネルで表示します。
        msg が指定された場合はパネルのタイトルテキストを更新します。
        """
        if hasattr(self, "_empty_panel_title"):
            self._empty_panel_title.setText(msg)
        if hasattr(self, "_chart_stack"):
            self._chart_stack.setCurrentIndex(0)  # 空状態パネルへ切替
        else:
            # フォールバック（初期化前に呼ばれた場合）
            ax = self._canvas.ax
            ax.clear()
            ax.text(0.5, 0.5, msg, ha="center", va="center",
                    transform=ax.transAxes, fontsize=11, color="gray")
            self._canvas.draw()
