"""
app/ui/case_compare_dialog.py
2ケース詳細比較ダイアログ。

選択した2つのケースの応答値を並べて比較し、
差分・変化率をハイライト表示します。
層別データのオーバーレイグラフも表示します。

レイアウト:
  ┌─────────────────────────────────────────────────────────────┐
  │ ケースA: [Case1 ▼]           ケースB: [Case2 ▼]           │
  ├──────────────────────┬────────────────────────────────────── │
  │ 比較テーブル          │ オーバーレイグラフ                   │
  │ ┌──────┬──────┬──────┐│ 応答値: [max_drift ▼]               │
  │ │項目  │CaseA │CaseB ││                                      │
  │ │drift │0.003 │0.005 ││     ▬ Case A                         │
  │ │acc   │3.2   │2.8   ││     ▬ Case B                         │
  │ │diff  │+60%  │      ││                                      │
  │ └──────┴──────┴──────┘│                                      │
  ├──────────────────────┴──────────────────────────────────────┤
  │ パラメータ差分                                               │
  │ パラメータ | CaseA | CaseB | 差分                           │
  ├─────────────────────────────────────────────────────────────┤
  │                                               [閉じる]      │
  └─────────────────────────────────────────────────────────────┘

UX改善（新①）: 総合改善判定サマリーカード追加。
  ケース選択行の直下に「総合判定カード」を追加しました。
  カードにはケースA→Bで改善した指標数・悪化した指標数・同等の指標数を
  ピクトグラム付きで要約し、最大改善率を強調表示します。
  「ケースBは全体的に良いか悪いか」をひと目で把握でき、
  詳細テーブルを1行ずつ確認する前の概要として機能します。
  - ✅ X指標改善 / ⚠ Y指標悪化 / ➡ Z指標同等
  - 「最大改善: 最大層間変形角 -XX.X%」の最優秀改善指標を表示
  - サマリーカードは _refresh() が呼ばれるたびに自動更新されます
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager, MPL_STYLES

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

# 応答値の定義 (key, 日本語ラベル, 単位, フォーマット)
_RESPONSE_ITEMS = [
    ("max_disp",        "最大応答相対変位",    "m",     "{:.5f}"),
    ("max_vel",         "最大応答相対速度",    "m/s",   "{:.4f}"),
    ("max_acc",         "最大応答絶対加速度",  "m/s²",  "{:.3f}"),
    ("max_drift",       "最大層間変形角",      "rad",   "{:.6f}"),
    ("max_shear",       "せん断力係数",        "—",     "{:.4f}"),
    ("max_otm",         "最大転倒ﾓｰﾒﾝﾄ",     "kN·m",  "{:.1f}"),
]

# 層別グラフ用
_FLOOR_RESPONSE_ITEMS = [
    ("max_disp",        "最大応答相対変位",    "m"),
    ("max_vel",         "最大応答相対速度",    "m/s"),
    ("max_acc",         "最大応答絶対加速度",  "m/s²"),
    ("max_story_disp",  "最大層間変形",        "m"),
    ("max_story_drift", "最大層間変形角",      "rad"),
    ("shear_coeff",     "せん断力係数",        "—"),
    ("max_otm",         "最大転倒ﾓｰﾒﾝﾄ",     "kN·m"),
]


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _MplCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(5, 5), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


class CaseCompareDialog(QDialog):
    """
    2ケース詳細比較ダイアログ。

    Parameters
    ----------
    cases : list of AnalysisCase
        完了済みケースのリスト。
    initial_a : AnalysisCase, optional
        初期選択のケースA。
    initial_b : AnalysisCase, optional
        初期選択のケースB。
    parent : QWidget, optional
        親ウィジェット。
    """

    def __init__(
        self,
        cases: List[AnalysisCase],
        initial_a: Optional[AnalysisCase] = None,
        initial_b: Optional[AnalysisCase] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._completed = [
            c for c in cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        self._initial_a = initial_a
        self._initial_b = initial_b

        self.setWindowTitle("ケース詳細比較")
        self.setMinimumWidth(1000)
        self.setMinimumHeight(700)
        self._setup_ui()
        self._connect_signals()
        self._refresh()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ケース選択行
        select_row = QHBoxLayout()
        select_row.addWidget(QLabel("<b>ケースA:</b>"))
        self._combo_a = QComboBox()
        self._combo_a.setMinimumWidth(200)
        select_row.addWidget(self._combo_a)

        select_row.addWidget(QLabel("  ⇔  "))

        select_row.addWidget(QLabel("<b>ケースB:</b>"))
        self._combo_b = QComboBox()
        self._combo_b.setMinimumWidth(200)
        select_row.addWidget(self._combo_b)

        select_row.addStretch()

        swap_btn = QPushButton("入替 ⇌")
        swap_btn.setToolTip("ケースAとBを入れ替えます")
        swap_btn.clicked.connect(self._swap_cases)
        select_row.addWidget(swap_btn)

        layout.addLayout(select_row)

        # UX改善（新①）: 総合改善判定サマリーカード
        self._summary_card = QFrame()
        self._summary_card.setFrameShape(QFrame.StyledPanel)
        self._summary_card.setStyleSheet(
            "QFrame { border-radius: 6px; padding: 4px; }"
        )
        self._summary_card.setMaximumHeight(64)
        summary_card_layout = QHBoxLayout(self._summary_card)
        summary_card_layout.setContentsMargins(10, 4, 10, 4)
        self._summary_improved_lbl = QLabel("✅ — 改善")
        self._summary_improved_lbl.setStyleSheet("color: #2e7d32; font-weight: bold; font-size: 12px;")
        self._summary_worsened_lbl = QLabel("⚠ — 悪化")
        self._summary_worsened_lbl.setStyleSheet("color: #b71c1c; font-weight: bold; font-size: 12px;")
        self._summary_equal_lbl = QLabel("➡ — 同等")
        self._summary_equal_lbl.setStyleSheet("color: #555; font-size: 12px;")
        self._summary_best_lbl = QLabel("")
        self._summary_best_lbl.setStyleSheet("color: #1565c0; font-size: 11px;")
        summary_card_layout.addWidget(self._summary_improved_lbl)
        summary_card_layout.addSpacing(16)
        summary_card_layout.addWidget(self._summary_worsened_lbl)
        summary_card_layout.addSpacing(16)
        summary_card_layout.addWidget(self._summary_equal_lbl)
        summary_card_layout.addSpacing(24)
        summary_card_layout.addWidget(self._summary_best_lbl)
        summary_card_layout.addStretch()
        layout.addWidget(self._summary_card)

        # コンボボックスにケースを追加
        for case in self._completed:
            self._combo_a.addItem(case.name, case.id)
            self._combo_b.addItem(case.name, case.id)

        # 初期選択
        if self._initial_a:
            idx = self._combo_a.findData(self._initial_a.id)
            if idx >= 0:
                self._combo_a.setCurrentIndex(idx)
        if self._initial_b:
            idx = self._combo_b.findData(self._initial_b.id)
            if idx >= 0:
                self._combo_b.setCurrentIndex(idx)
        elif len(self._completed) > 1:
            self._combo_b.setCurrentIndex(1)

        # メインスプリッター: テーブル（左） + グラフ（右）
        main_splitter = QSplitter(Qt.Horizontal)

        # 比較テーブル（左）
        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)

        table_layout.addWidget(QLabel("<b>応答値比較</b>"))
        self._response_table = QTableWidget(0, 5)
        self._response_table.setHorizontalHeaderLabels([
            "応答値", "ケースA", "ケースB", "差分", "変化率"
        ])
        self._response_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._response_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._response_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._response_table.setAlternatingRowColors(True)
        self._response_table.verticalHeader().setVisible(False)
        table_layout.addWidget(self._response_table)

        # パラメータ差分テーブル
        table_layout.addWidget(QLabel("<b>パラメータ差分</b>"))
        self._param_table = QTableWidget(0, 4)
        self._param_table.setHorizontalHeaderLabels([
            "パラメータ", "ケースA", "ケースB", "差分"
        ])
        self._param_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._param_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._param_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._param_table.setAlternatingRowColors(True)
        self._param_table.verticalHeader().setVisible(False)
        self._param_table.setMaximumHeight(200)
        table_layout.addWidget(self._param_table)

        main_splitter.addWidget(table_panel)

        # グラフ（右）
        chart_panel = QWidget()
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(0, 0, 0, 0)

        chart_ctrl = QHBoxLayout()
        chart_ctrl.addWidget(QLabel("<b>層別比較グラフ</b>"))
        chart_ctrl.addStretch()
        chart_ctrl.addWidget(QLabel("表示項目:"))
        self._chart_combo = QComboBox()
        for _, label, unit in _FLOOR_RESPONSE_ITEMS:
            self._chart_combo.addItem(f"{label} [{unit}]")
        self._chart_combo.currentIndexChanged.connect(self._draw_overlay)
        chart_ctrl.addWidget(self._chart_combo)
        chart_layout.addLayout(chart_ctrl)

        self._canvas = _MplCanvas(self)
        chart_layout.addWidget(self._canvas)

        # 差分バーチャート
        chart_layout.addWidget(QLabel("<b>応答値差分（ケースB − ケースA）</b>"))
        self._diff_canvas = _MplCanvas(self)
        self._diff_canvas.fig.set_size_inches(5, 2.5)
        chart_layout.addWidget(self._diff_canvas)

        main_splitter.addWidget(chart_panel)

        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 1)
        layout.addWidget(main_splitter, stretch=1)

        # ボタン行
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _connect_signals(self) -> None:
        self._combo_a.currentIndexChanged.connect(self._refresh)
        self._combo_b.currentIndexChanged.connect(self._refresh)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _swap_cases(self) -> None:
        idx_a = self._combo_a.currentIndex()
        idx_b = self._combo_b.currentIndex()
        self._combo_a.blockSignals(True)
        self._combo_b.blockSignals(True)
        self._combo_a.setCurrentIndex(idx_b)
        self._combo_b.setCurrentIndex(idx_a)
        self._combo_a.blockSignals(False)
        self._combo_b.blockSignals(False)
        self._refresh()

    def _get_selected_cases(self) -> Tuple[Optional[AnalysisCase], Optional[AnalysisCase]]:
        case_a = case_b = None
        id_a = self._combo_a.currentData()
        id_b = self._combo_b.currentData()
        for c in self._completed:
            if c.id == id_a:
                case_a = c
            if c.id == id_b:
                case_b = c
        return case_a, case_b

    def _refresh(self) -> None:
        case_a, case_b = self._get_selected_cases()
        self._populate_response_table(case_a, case_b)
        self._populate_param_table(case_a, case_b)
        self._draw_overlay()
        self._draw_diff_chart(case_a, case_b)

    # ------------------------------------------------------------------
    # Response Table
    # ------------------------------------------------------------------

    def _populate_response_table(
        self,
        case_a: Optional[AnalysisCase],
        case_b: Optional[AnalysisCase],
    ) -> None:
        self._response_table.setRowCount(0)

        if case_a is None or case_b is None:
            return

        summary_a = case_a.result_summary or {}
        summary_b = case_b.result_summary or {}

        theme = "dark" if ThemeManager.is_dark() else "light"
        color_better = QColor("#1b3a1b") if theme == "dark" else QColor("#e8f5e9")
        color_worse = QColor("#4a1a1a") if theme == "dark" else QColor("#ffebee")

        for key, label, unit, fmt in _RESPONSE_ITEMS:
            row = self._response_table.rowCount()
            self._response_table.insertRow(row)

            # 応答値名
            self._response_table.setItem(row, 0, QTableWidgetItem(f"{label} [{unit}]"))

            val_a = summary_a.get(key)
            val_b = summary_b.get(key)

            # ケースA
            if val_a is not None:
                item_a = QTableWidgetItem(fmt.format(val_a))
                item_a.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                item_a = QTableWidgetItem("—")
                item_a.setTextAlignment(Qt.AlignCenter)
            self._response_table.setItem(row, 1, item_a)

            # ケースB
            if val_b is not None:
                item_b = QTableWidgetItem(fmt.format(val_b))
                item_b.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                item_b = QTableWidgetItem("—")
                item_b.setTextAlignment(Qt.AlignCenter)
            self._response_table.setItem(row, 2, item_b)

            # 差分・変化率
            if val_a is not None and val_b is not None:
                diff = val_b - val_a
                diff_item = QTableWidgetItem(f"{diff:+.5g}")
                diff_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

                # 一般に応答値は小さい方が良い → 差分が正なら悪化
                if diff > 0:
                    diff_item.setBackground(color_worse)
                elif diff < 0:
                    diff_item.setBackground(color_better)
                self._response_table.setItem(row, 3, diff_item)

                if abs(val_a) > 1e-15:
                    pct = (val_b - val_a) / abs(val_a) * 100
                    pct_item = QTableWidgetItem(f"{pct:+.1f}%")
                    pct_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if pct > 0:
                        pct_item.setBackground(color_worse)
                    elif pct < 0:
                        pct_item.setBackground(color_better)
                    self._response_table.setItem(row, 4, pct_item)
                else:
                    self._response_table.setItem(row, 4, QTableWidgetItem("—"))
            else:
                self._response_table.setItem(row, 3, QTableWidgetItem("—"))
                self._response_table.setItem(row, 4, QTableWidgetItem("—"))

        # UX改善（新①）: サマリーカードを更新
        self._update_summary_card(case_a, case_b)

    # ------------------------------------------------------------------
    # UX改善（新①）: 総合改善判定サマリーカード更新
    # ------------------------------------------------------------------

    def _update_summary_card(
        self,
        case_a: Optional[AnalysisCase],
        case_b: Optional[AnalysisCase],
    ) -> None:
        """
        ケースA→Bの総合改善状況をサマリーカードに反映します。

        応答値（小さい方が良い）ごとに改善/悪化/同等を判定し、
        件数と最大改善率をカードに表示します。
        """
        if case_a is None or case_b is None:
            self._summary_improved_lbl.setText("✅ — 改善")
            self._summary_worsened_lbl.setText("⚠ — 悪化")
            self._summary_equal_lbl.setText("➡ — 同等")
            self._summary_best_lbl.setText("")
            return

        summary_a = case_a.result_summary or {}
        summary_b = case_b.result_summary or {}

        n_improved = 0
        n_worsened = 0
        n_equal = 0
        best_pct: Optional[float] = None
        best_label: str = ""

        for key, label, unit, _ in _RESPONSE_ITEMS:
            val_a = summary_a.get(key)
            val_b = summary_b.get(key)
            if val_a is None or val_b is None:
                continue
            if abs(val_a) < 1e-15:
                continue
            pct = (val_b - val_a) / abs(val_a) * 100
            if pct < -0.5:   # 0.5% 超の改善を「改善」とみなす
                n_improved += 1
                if best_pct is None or pct < best_pct:
                    best_pct = pct
                    best_label = label
            elif pct > 0.5:  # 0.5% 超の悪化を「悪化」とみなす
                n_worsened += 1
            else:
                n_equal += 1

        # ラベル更新
        self._summary_improved_lbl.setText(f"✅ {n_improved}指標改善")
        self._summary_worsened_lbl.setText(f"⚠ {n_worsened}指標悪化")
        self._summary_equal_lbl.setText(f"➡ {n_equal}指標同等")

        if best_pct is not None:
            self._summary_best_lbl.setText(
                f"最大改善: {best_label} {best_pct:+.1f}%"
            )
        else:
            self._summary_best_lbl.setText("")

        # カードの背景色をケースBの優劣に応じて変える
        theme = "dark" if ThemeManager.is_dark() else "light"
        if n_improved > n_worsened:
            bg = "#1b3a1b" if theme == "dark" else "#e8f5e9"
        elif n_worsened > n_improved:
            bg = "#4a1a1a" if theme == "dark" else "#ffebee"
        else:
            bg = "#2a2a2a" if theme == "dark" else "#f5f5f5"
        self._summary_card.setStyleSheet(
            f"QFrame {{ border-radius: 6px; padding: 4px; background: {bg}; }}"
        )

    # ------------------------------------------------------------------
    # Parameter Table
    # ------------------------------------------------------------------

    def _populate_param_table(
        self,
        case_a: Optional[AnalysisCase],
        case_b: Optional[AnalysisCase],
    ) -> None:
        self._param_table.setRowCount(0)

        if case_a is None or case_b is None:
            return

        def _flatten(params: dict) -> dict:
            flat = {}
            for k, v in params.items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        flat[f"{k}.{k2}"] = str(v2)
                else:
                    flat[k] = str(v)
            return flat

        params_a = _flatten({**case_a.parameters, **(case_a.damper_params or {})})
        params_b = _flatten({**case_b.parameters, **(case_b.damper_params or {})})

        all_keys = sorted(set(params_a.keys()) | set(params_b.keys()))

        theme = "dark" if ThemeManager.is_dark() else "light"
        color_diff = QColor("#4a4500") if theme == "dark" else QColor("#fffde7")

        for key in all_keys:
            val_a = params_a.get(key, "—")
            val_b = params_b.get(key, "—")

            row = self._param_table.rowCount()
            self._param_table.insertRow(row)

            self._param_table.setItem(row, 0, QTableWidgetItem(key))
            self._param_table.setItem(row, 1, QTableWidgetItem(val_a))
            self._param_table.setItem(row, 2, QTableWidgetItem(val_b))

            # 差分
            if val_a != val_b:
                try:
                    diff = float(val_b) - float(val_a)
                    diff_item = QTableWidgetItem(f"{diff:+.5g}")
                except (ValueError, TypeError):
                    diff_item = QTableWidgetItem("変更あり")
                diff_item.setBackground(color_diff)
                font = QFont()
                font.setBold(True)
                diff_item.setFont(font)
            else:
                diff_item = QTableWidgetItem("同一")
            diff_item.setTextAlignment(Qt.AlignCenter)
            self._param_table.setItem(row, 3, diff_item)

    # ------------------------------------------------------------------
    # Overlay Chart (floor-wise)
    # ------------------------------------------------------------------

    def _draw_overlay(self) -> None:
        ax = self._canvas.ax
        ax.clear()

        case_a, case_b = self._get_selected_cases()
        if case_a is None or case_b is None:
            ax.text(
                0.5, 0.5, "2ケースを選択してください",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="gray",
            )
            self._canvas.draw()
            return

        idx = self._chart_combo.currentIndex()
        key, label, unit = _FLOOR_RESPONSE_ITEMS[idx]

        rd_a = case_a.result_summary.get("result_data", {}).get(key, {})
        rd_b = case_b.result_summary.get("result_data", {}).get(key, {})

        if not rd_a and not rd_b:
            # スカラー値のみ
            val_a = case_a.result_summary.get(key)
            val_b = case_b.result_summary.get(key)
            if val_a is not None:
                rd_a = {1: val_a}
            if val_b is not None:
                rd_b = {1: val_b}

        if not rd_a and not rd_b:
            ax.text(
                0.5, 0.5, "この項目のデータがありません",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=11, color="gray",
            )
            self._canvas.draw()
            return

        all_floors = sorted(set(rd_a.keys()) | set(rd_b.keys()))

        # ケースA
        if rd_a:
            floors_a = sorted(rd_a.keys())
            vals_a = [rd_a[f] for f in floors_a]
            ax.plot(
                vals_a, floors_a,
                marker="o", markersize=6, linewidth=2,
                color="#1f77b4", label=f"A: {case_a.name}",
            )

        # ケースB
        if rd_b:
            floors_b = sorted(rd_b.keys())
            vals_b = [rd_b[f] for f in floors_b]
            ax.plot(
                vals_b, floors_b,
                marker="s", markersize=6, linewidth=2,
                color="#d62728", label=f"B: {case_b.name}",
            )

        ax.set_xlabel(f"{label} [{unit}]", fontsize=9)
        ax.set_ylabel("層", fontsize=9)
        ax.set_title(f"層別比較 — {label}", fontsize=10)
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.4)
        ax.legend(fontsize=8)
        if all_floors:
            ax.set_yticks(all_floors)

        self._canvas.fig.tight_layout()
        self._canvas.draw()

    # ------------------------------------------------------------------
    # Diff Bar Chart
    # ------------------------------------------------------------------

    def _draw_diff_chart(
        self,
        case_a: Optional[AnalysisCase],
        case_b: Optional[AnalysisCase],
    ) -> None:
        ax = self._diff_canvas.ax
        ax.clear()

        if case_a is None or case_b is None:
            self._diff_canvas.draw()
            return

        summary_a = case_a.result_summary or {}
        summary_b = case_b.result_summary or {}

        labels = []
        pct_diffs = []
        colors = []

        for key, label, _, _ in _RESPONSE_ITEMS:
            val_a = summary_a.get(key)
            val_b = summary_b.get(key)
            if val_a is not None and val_b is not None and abs(val_a) > 1e-15:
                pct = (val_b - val_a) / abs(val_a) * 100
                labels.append(label)
                pct_diffs.append(pct)
                colors.append("#d62728" if pct > 0 else "#2ca02c")

        if not labels:
            ax.text(
                0.5, 0.5, "差分データなし",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color="gray",
            )
            self._diff_canvas.draw()
            return

        y_pos = range(len(labels))
        bars = ax.barh(y_pos, pct_diffs, color=colors, height=0.5, edgecolor="none")
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("変化率 (%)", fontsize=8)
        ax.axvline(x=0, color="gray", linewidth=0.5)
        ax.tick_params(labelsize=7)
        ax.grid(axis="x", linestyle="--", alpha=0.3)
        ax.set_title(f"B({case_b.name}) vs A({case_a.name}) 変化率", fontsize=9)

        # バーに値を表示
        for bar, pct in zip(bars, pct_diffs):
            x_pos = bar.get_width()
            ha = "left" if pct >= 0 else "right"
            offset = 0.5 if pct >= 0 else -0.5
            ax.text(
                x_pos + offset, bar.get_y() + bar.get_height() / 2,
                f"{pct:+.1f}%", va="center", ha=ha, fontsize=7,
            )

        self._diff_canvas.fig.tight_layout()
        self._diff_canvas.draw()
