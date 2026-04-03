"""
app/ui/dashboard_widget.py
プロジェクトサマリーダッシュボードウィジェット。

全ケースの概要を一目で確認できるダッシュボードです。
ヒートマップ、最良/最悪ケース表示、統計情報を提供します。

レイアウト:
  ┌──────────────────────────────────────────────────────────┐
  │ プロジェクトサマリーダッシュボード                       │
  ├──────────────┬──────────────┬──────────────┬─────────────┤
  │ 全ケース数   │ 完了ケース   │ 最良ケース   │ 最悪ケース  │
  │     12       │     10       │  Case-03     │  Case-07    │
  ├──────────────┴──────────────┴──────────────┴─────────────┤
  │ 応答値ヒートマップ                                       │
  │ ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┐       │
  │ │      │max_d │max_v │max_a │drift │shear │ OTM  │       │
  │ │Case1 │ ██   │ ██   │ ██   │ ██   │ ██   │ ██   │       │
  │ │Case2 │ ██   │ ██   │ ██   │ ██   │ ██   │ ██   │       │
  │ └──────┴──────┴──────┴──────┴──────┴──────┴──────┘       │
  ├──────────────────────────────────────────────────────────┤
  │ 応答値分布（箱ひげ図）                                   │
  └──────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

import qtawesome as qta

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager, MPL_STYLES

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

# 応答値の定義 (key, 日本語ラベル, 単位)
_RESPONSE_ITEMS = [
    ("max_disp",        "最大相対変位",    "m"),
    ("max_vel",         "最大相対速度",    "m/s"),
    ("max_acc",         "最大絶対加速度",  "m/s²"),
    ("max_drift",       "最大層間変形角",  "rad"),
    ("max_shear",       "せん断力係数",    "—"),
    ("max_otm",         "最大転倒ﾓｰﾒﾝﾄ",  "kN·m"),
]


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _StatCard(QFrame):
    """統計カード: ラベルと値を表示する小さなフレーム。"""

    clicked = Signal(str)  # case_id

    def __init__(
        self,
        title: str,
        value: str = "—",
        subtitle: str = "",
        color: str = "#1f77b4",
        icon_name: str = "fa5s.chart-bar",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._case_id: str = ""
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(96)
        self.setMaximumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        self.setStyleSheet(f"""
            _StatCard {{
                background-color: palette(base);
                border: 1px solid palette(mid);
                border-radius: 8px;
            }}
            _StatCard:hover {{
                border: 1px solid {color};
                background-color: palette(alternate-base);
            }}
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setPixmap(qta.icon(icon_name, color=color).pixmap(32, 32))
        icon_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        main_layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        text_layout.addWidget(self._title_label)

        self._value_label = QLabel(value)
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        self._value_label.setFont(font)
        text_layout.addWidget(self._value_label)

        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setStyleSheet("color: gray; font-size: 10px;")
        text_layout.addWidget(self._subtitle_label)

        text_layout.addStretch()
        main_layout.addLayout(text_layout)
        main_layout.addStretch()

    def set_value(self, value: str, subtitle: str = "", case_id: str = "") -> None:
        self._value_label.setText(value)
        self._subtitle_label.setText(subtitle)
        self._case_id = case_id

    def mousePressEvent(self, event) -> None:
        if self._case_id:
            self.clicked.emit(self._case_id)
        super().mousePressEvent(event)


class _HeatmapCanvas(FigureCanvas):
    """ヒートマップ用の matplotlib キャンバス。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(8, 4), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def apply_theme(self) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class _BoxplotCanvas(FigureCanvas):
    """箱ひげ図用の matplotlib キャンバス。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(8, 3), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def apply_theme(self) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class DashboardWidget(QWidget):
    """
    プロジェクトサマリーダッシュボード。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットしてダッシュボードを更新
    refresh()         — 現在のケースで再描画
    update_theme()    — テーマ変更時にグラフ色を更新
    """

    caseSelected = Signal(str)  # case_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        self._cases = cases
        self.refresh()

    def refresh(self) -> None:
        self._update_stat_cards()
        self._draw_heatmap()
        self._draw_boxplot()

    def update_theme(self) -> None:
        self._heatmap_canvas.apply_theme()
        self._boxplot_canvas.apply_theme()
        self.refresh()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # タイトル
        title = QLabel("<b>プロジェクトサマリーダッシュボード</b>")
        title.setStyleSheet("font-size: 14px;")
        layout.addWidget(title)

        # 統計カード行
        card_row = QHBoxLayout()
        card_row.setSpacing(8)

        self._card_total = _StatCard("全ケース数", "0", "", "#1f77b4", "fa5s.list-ol")
        self._card_completed = _StatCard("完了ケース", "0", "", "#2ca02c", "fa5s.check-circle")
        self._card_best = _StatCard("最良ケース（層間変形角）", "—", "", "#ff7f0e", "fa5s.trophy")
        self._card_worst = _StatCard("最悪ケース（層間変形角）", "—", "", "#d62728", "fa5s.exclamation-triangle")

        self._card_best.clicked.connect(self.caseSelected)
        self._card_worst.clicked.connect(self.caseSelected)

        card_row.addWidget(self._card_total)
        card_row.addWidget(self._card_completed)
        card_row.addWidget(self._card_best)
        card_row.addWidget(self._card_worst)
        layout.addLayout(card_row)

        # ヒートマップ
        heatmap_label = QLabel("<b>応答値ヒートマップ</b>（正規化: 青=小, 赤=大）")
        heatmap_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(heatmap_label)

        self._heatmap_canvas = _HeatmapCanvas(self)
        layout.addWidget(self._heatmap_canvas, stretch=2)

        # 箱ひげ図
        boxplot_label = QLabel("<b>応答値の分布</b>（箱ひげ図）")
        boxplot_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(boxplot_label)

        self._boxplot_canvas = _BoxplotCanvas(self)
        layout.addWidget(self._boxplot_canvas, stretch=1)

    # ------------------------------------------------------------------
    # Stat cards
    # ------------------------------------------------------------------

    def _update_stat_cards(self) -> None:
        total = len(self._cases)
        completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        n_completed = len(completed)

        self._card_total.set_value(str(total), f"エラー: {sum(1 for c in self._cases if c.status == AnalysisCaseStatus.ERROR)}")
        self._card_completed.set_value(
            str(n_completed),
            f"実行待ち: {sum(1 for c in self._cases if c.status == AnalysisCaseStatus.PENDING)}"
        )

        if completed:
            # 最良: max_drift が最小
            best = min(completed, key=lambda c: c.result_summary.get("max_drift", float("inf")))
            worst = max(completed, key=lambda c: c.result_summary.get("max_drift", 0))

            best_val = best.result_summary.get("max_drift")
            worst_val = worst.result_summary.get("max_drift")

            self._card_best.set_value(
                best.name,
                f"層間変形角: {best_val:.6f} rad" if best_val is not None else "",
                case_id=best.id,
            )
            self._card_worst.set_value(
                worst.name,
                f"層間変形角: {worst_val:.6f} rad" if worst_val is not None else "",
                case_id=worst.id,
            )
        else:
            self._card_best.set_value("—", "完了ケースなし")
            self._card_worst.set_value("—", "完了ケースなし")

    # ------------------------------------------------------------------
    # Heatmap
    # ------------------------------------------------------------------

    def _draw_heatmap(self) -> None:
        ax = self._heatmap_canvas.ax
        ax.clear()

        completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]

        if not completed:
            ax.text(
                0.5, 0.5, "完了ケースがありません",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="gray",
            )
            self._heatmap_canvas.draw()
            return

        case_names = [c.name for c in completed]
        col_labels = [label for _, label, _ in _RESPONSE_ITEMS]

        # データ行列を構築
        data = np.full((len(completed), len(_RESPONSE_ITEMS)), np.nan)
        for i, case in enumerate(completed):
            for j, (key, _, _) in enumerate(_RESPONSE_ITEMS):
                val = case.result_summary.get(key)
                if val is not None:
                    data[i, j] = val

        # 列ごとに正規化 (0-1)
        norm_data = np.full_like(data, np.nan)
        for j in range(data.shape[1]):
            col = data[:, j]
            valid = col[~np.isnan(col)]
            if len(valid) > 0:
                vmin, vmax = valid.min(), valid.max()
                if vmax > vmin:
                    norm_data[:, j] = (col - vmin) / (vmax - vmin)
                else:
                    norm_data[:, j] = 0.5

        # ヒートマップ描画
        im = ax.imshow(
            norm_data, aspect="auto", cmap="RdYlBu_r",
            interpolation="nearest", vmin=0, vmax=1,
        )

        # 軸ラベル
        ax.set_xticks(range(len(col_labels)))
        ax.set_xticklabels(col_labels, fontsize=8, rotation=30, ha="right")
        ax.set_yticks(range(len(case_names)))
        ax.set_yticklabels(case_names, fontsize=8)

        # セル内に値を表示
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                val = data[i, j]
                if not np.isnan(val):
                    norm_val = norm_data[i, j]
                    # コントラストのあるテキスト色
                    text_color = "white" if (not np.isnan(norm_val) and norm_val > 0.6) else "black"
                    ax.text(
                        j, i, f"{val:.4g}",
                        ha="center", va="center",
                        fontsize=7, color=text_color,
                    )

        ax.set_title("応答値ヒートマップ（列ごとに正規化）", fontsize=10)
        self._heatmap_canvas.fig.tight_layout()
        self._heatmap_canvas.draw()

    # ------------------------------------------------------------------
    # Boxplot
    # ------------------------------------------------------------------

    def _draw_boxplot(self) -> None:
        ax = self._boxplot_canvas.ax
        ax.clear()

        completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]

        if len(completed) < 2:
            ax.text(
                0.5, 0.5, "箱ひげ図には2ケース以上の完了データが必要です",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="gray",
            )
            self._boxplot_canvas.draw()
            return

        # 各応答値のデータを収集
        labels = []
        box_data = []
        for key, label, unit in _RESPONSE_ITEMS:
            values = []
            for case in completed:
                val = case.result_summary.get(key)
                if val is not None:
                    values.append(val)
            if len(values) >= 2:
                # 正規化（各指標のスケールが異なるため）
                arr = np.array(values)
                vmin, vmax = arr.min(), arr.max()
                if vmax > vmin:
                    normed = (arr - vmin) / (vmax - vmin)
                else:
                    normed = np.zeros_like(arr)
                box_data.append(normed)
                labels.append(label)

        if not box_data:
            ax.text(
                0.5, 0.5, "表示可能なデータがありません",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="gray",
            )
            self._boxplot_canvas.draw()
            return

        ax.boxplot(box_data, vert=True, patch_artist=True,
                   boxprops=dict(facecolor="#1f77b4", alpha=0.5),
                   medianprops=dict(color="red", linewidth=2))
        ax.set_xticks(range(1, len(labels) + 1))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_ylabel("正規化値", fontsize=9)
        ax.set_title("応答値箱ひげ図（ケース間比較・正規化）", fontsize=10)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        self._boxplot_canvas.fig.tight_layout()
        self._boxplot_canvas.draw()