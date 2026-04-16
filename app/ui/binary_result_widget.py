"""
app/ui/binary_result_widget.py
==============================

SNAP バイナリ結果ビューア（種類別・自動表示・マルチケース比較版）

解析ケースを複数選択すると、SNAP の結果フォルダから .hst/.xbn/.stp/Period.xbn を
自動ロードし、解析の種類ごとにタブで比較表示します。

タブ:
    🌊 固有値解析     — Period.xbn (ケースごとに行を色分け)
    📈 時刻歴応答     — Floor.hst  (各階の変位/速度/加速度、ケース重ね描き)
    🏢 層応答         — Story.hst  (層間変形/せん断力/転倒モーメント)
    🛡 ダンパー履歴   — Damper.hst (履歴ループ/時刻歴)
    🧩 バネ履歴       — Spring.hst
    📊 最大応答値     — .xbn
    ⚡ エネルギー     — Energy.hst

結果フォルダの探索順 (各ケースについて):
    1. case.binary_result_dir        （解析時に保存された実パス）
    2. case.dyc_results[*]["result_dir"]
    3. case.result_path / case.output_dir
    4. case.result_path/{dyc.name} サブフォルダ
    5. model_path のあるフォルダ
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import numpy as np

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QStackedWidget,
)

import os, matplotlib
if not os.environ.get("MPLBACKEND"):
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

from controller.binary import SnapResultLoader
from controller.binary.result_loader import BinaryCategory
from controller.binary.hysteresis_analysis import energy_field_index

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 既知フィールドマップ（リバースエンジニアリング結果に基づく暫定）
# ---------------------------------------------------------------------------
_FLOOR_QUANTITIES: List[tuple] = [
    ("相対変位", 0, "m"),
    ("相対速度", 4, "m/s"),
    ("絶対加速度", 6, "m/s²"),
    ("応答量 f8", 8, ""),
    ("応答量 f9", 9, ""),
    ("応答量 f10", 10, ""),
]

_STORY_QUANTITIES: List[tuple] = [
    ("層間変形", 0, "m"),
    ("層間変形角", 3, "rad"),
    ("せん断力", 6, "kN"),
    ("せん断力係数", 7, ""),
    ("転倒モーメント", 9, "kN·m"),
]

_DAMPER_FIELDS = {
    "荷重 F": 0,
    "変位 D": 1,
}

_SPRING_FIELDS = {
    "荷重 F": 0,
    "変位 D": 1,
}

_NODE_QUANTITIES: List[tuple] = [
    ("変位", 0, "m"),
    ("応答量 f1", 1, ""),
    ("応答量 f2", 2, ""),
    ("応答量 f3", 3, ""),
    ("速度", 4, "m/s"),
    ("応答量 f5", 5, ""),
    ("加速度", 6, "m/s²"),
]

_MEMBER_FIELDS = {
    "荷重 F": 0,
    "変位 D": 1,
}


# ---------------------------------------------------------------------------
# 各ケースのロード結果を保持するエントリ
# ---------------------------------------------------------------------------
@dataclass
class _CaseEntry:
    name: str
    path: Path
    loader: SnapResultLoader


class _MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=6.5, height=3.8, dpi=100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.fig.tight_layout()

    def show_message(self, msg: str, color: str = "gray"):
        self.ax.clear()
        self.ax.text(0.5, 0.5, msg, ha="center", va="center",
                     transform=self.ax.transAxes, color=color, fontsize=11)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw()


def _set_tight_ylim(ax, y_arr: np.ndarray, margin_ratio: float = 0.10) -> None:
    """y データに合わせて ax の ylim を設定する。極小値にも対応したスケーリング。"""
    if y_arr.size == 0:
        return
    y_min, y_max = float(y_arr.min()), float(y_arr.max())
    max_abs = max(abs(y_min), abs(y_max))
    if max_abs < 1e-30:
        # 全ゼロ — デフォルトの軸範囲をそのまま使う
        return
    y_range = y_max - y_min
    if y_range < max_abs * 1e-10:
        # 実質フラットラインの場合: 値の ±20% をマージンにする
        margin = max_abs * 0.20
    else:
        margin = y_range * margin_ratio
    ax.set_ylim(y_min - margin, y_max + margin)


def _empty_panel(msg: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setAlignment(Qt.AlignCenter)
    lab = QLabel(msg)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet("color: #888; font-size: 13px; padding: 40px;")
    lay.addWidget(lab)
    return w


# ---------------------------------------------------------------------------
# メインウィジェット
# ---------------------------------------------------------------------------
class BinaryResultWidget(QWidget):
    """SNAP バイナリ結果を種類別・マルチケース比較表示するウィジェット。"""

    # 左パネルのケース選択が変わったときに AnalysisCase リストを送出
    cases_selected = Signal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List = []
        # ロード済みケース: id(case) -> _CaseEntry
        self._entries: Dict[int, _CaseEntry] = {}
        # 現在アクティブな（選択中の）ケース
        self._active_entries: List[_CaseEntry] = []

        self._setup_ui()
        self._show_empty_state()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)
        outer.addLayout(self._build_top_bar())
        body = self._build_body()
        outer.addLayout(body, stretch=1)

    def _build_top_bar(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.addWidget(QLabel("比較ケース:"))
        top.addWidget(QLabel("（Ctrl / Shift で複数選択）"))
        top.addStretch(0)

        top.addWidget(QLabel("dt [s]:"))
        self._dt_spin = QDoubleSpinBox()
        self._dt_spin.setRange(0.00001, 1.0)
        self._dt_spin.setDecimals(5)
        self._dt_spin.setValue(0.005)
        self._dt_spin.setSingleStep(0.001)
        self._dt_spin.valueChanged.connect(self._on_dt_changed)
        top.addWidget(self._dt_spin)

        btn_reload = QPushButton("再読込")
        btn_reload.clicked.connect(self._reload)
        top.addWidget(btn_reload)
        return top

    def _build_case_list_panel(self) -> QVBoxLayout:
        left = QVBoxLayout()
        self._case_list = QListWidget()
        self._case_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._case_list.setMinimumWidth(220)
        self._case_list.setMaximumWidth(300)
        self._case_list.itemSelectionChanged.connect(self._on_case_selection_changed)
        left.addWidget(QLabel("解析ケース"))
        left.addWidget(self._case_list, stretch=1)

        btn_all = QPushButton("全選択")
        btn_all.clicked.connect(self._select_all_cases)
        left.addWidget(btn_all)
        return left

    def _build_result_tabs(self) -> QTabWidget:
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tab_stacks: dict = {}

        self._period_content = self._build_period_tab()
        self._tab_stacks["period"] = self._make_stack(self._period_content)
        self._tabs.addTab(self._tab_stacks["period"], "🌊 固有値解析")

        self._floor_content, self._floor_widgets = self._build_timehistory_tab(
            title="各階応答時刻歴",
            record_label="階",
            quantities=_FLOOR_QUANTITIES,
        )
        self._tab_stacks["floor"] = self._make_stack(self._floor_content)
        self._tabs.addTab(self._tab_stacks["floor"], "📈 時刻歴応答")

        self._story_content, self._story_widgets = self._build_timehistory_tab(
            title="層応答時刻歴",
            record_label="層",
            quantities=_STORY_QUANTITIES,
        )
        self._tab_stacks["story"] = self._make_stack(self._story_content)
        self._tabs.addTab(self._tab_stacks["story"], "🏢 層応答")

        self._damper_content, self._damper_widgets = self._build_hysteresis_tab(
            record_label="ダンパー",
            fields=_DAMPER_FIELDS,
        )
        self._tab_stacks["damper"] = self._make_stack(self._damper_content)
        self._tabs.addTab(self._tab_stacks["damper"], "🛡 ダンパー履歴")

        self._spring_content, self._spring_widgets = self._build_hysteresis_tab(
            record_label="バネ",
            fields=_SPRING_FIELDS,
        )
        self._tab_stacks["spring"] = self._make_stack(self._spring_content)
        self._tabs.addTab(self._tab_stacks["spring"], "🧩 バネ履歴")

        # DYD 履歴出力で追加される結果タブ
        self._node_content, self._node_widgets = self._build_timehistory_tab(
            title="節点応答時刻歴",
            record_label="節点",
            quantities=_NODE_QUANTITIES,
        )
        self._tab_stacks["node"] = self._make_stack(self._node_content)
        self._tabs.addTab(self._tab_stacks["node"], "📌 節点応答")

        self._beam_content, self._beam_widgets = self._build_hysteresis_tab(
            record_label="はり",
            fields=_MEMBER_FIELDS,
        )
        self._tab_stacks["beam"] = self._make_stack(self._beam_content)
        self._tabs.addTab(self._tab_stacks["beam"], "🔗 はり応答")

        self._column_content, self._column_widgets = self._build_hysteresis_tab(
            record_label="柱",
            fields=_MEMBER_FIELDS,
        )
        self._tab_stacks["column"] = self._make_stack(self._column_content)
        self._tabs.addTab(self._tab_stacks["column"], "🏛 柱応答")

        self._truss_content, self._truss_widgets = self._build_hysteresis_tab(
            record_label="トラス",
            fields=_MEMBER_FIELDS,
        )
        self._tab_stacks["truss"] = self._make_stack(self._truss_content)
        self._tabs.addTab(self._tab_stacks["truss"], "⚙ トラス応答")

        self._maxvals_content, self._maxvals_widgets = self._build_maxvals_tab()
        self._tab_stacks["maxvals"] = self._make_stack(self._maxvals_content)
        self._tabs.addTab(self._tab_stacks["maxvals"], "📊 最大応答値")

        self._energy_content, self._energy_widgets = self._build_energy_tab()
        self._tab_stacks["energy"] = self._make_stack(self._energy_content)
        self._tabs.addTab(self._tab_stacks["energy"], "⚡ エネルギー")
        return self._tabs

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.addLayout(self._build_case_list_panel())

        right = QVBoxLayout()
        self._status_label = QLabel("解析ケースを選択してください。")
        self._status_label.setStyleSheet(
            "color: #555; padding: 4px; background: #f5f5f5; border-radius: 4px;"
        )
        self._status_label.setWordWrap(True)
        right.addWidget(self._status_label)
        right.addWidget(self._build_result_tabs(), stretch=1)
        body.addLayout(right, stretch=1)
        return body

    def _make_stack(self, content: QWidget) -> QStackedWidget:
        stack = QStackedWidget()
        stack.addWidget(content)
        stack.addWidget(_empty_panel("データがありません"))
        stack.setCurrentIndex(1)
        return stack

    def _set_tab_state(self, key: str, has_data: bool) -> None:
        stack = self._tab_stacks.get(key)
        if stack is not None:
            stack.setCurrentIndex(0 if has_data else 1)

    # ------------------------------------------------------------------
    # Public helper: 外部ウィジェットをタブ先頭に挿入（ケース比較統合用）
    # ------------------------------------------------------------------
    def prepend_tab(self, widget: QWidget, label: str) -> None:
        """外部ウィジェットをタブ先頭（index 0）として挿入する。

        main_window から CompareChartWidget を統合するために使用。
        呼び出し側は widget の set_cases / set_criteria などを
        引き続き直接管理してよい。
        """
        self._tabs.insertTab(0, widget, label)
        self._tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # 各タブビルダ
    # ------------------------------------------------------------------
    def _build_period_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        lab = QLabel("固有値解析結果 — Period.xbn (ケースごとに色分け)")
        lab.setStyleSheet("color:#555; padding:2px;")
        lay.addWidget(lab)

        # 上下スプリッタ：テーブル + グラフ
        splitter = QSplitter(Qt.Vertical)

        # --- 上：数値テーブル ---
        self._period_table = QTableWidget()
        self._period_table.setAlternatingRowColors(True)
        self._period_table.horizontalHeader().setStretchLastSection(True)
        self._period_table.setMinimumHeight(120)
        splitter.addWidget(self._period_table)

        # --- 下：グラフエリア（サブタブで切り替え）---
        chart_tabs = QTabWidget()
        chart_tabs.setDocumentMode(True)

        # --- グラフ 1: 固有周期 ---
        w_period = QWidget()
        lay_period = QVBoxLayout(w_period)
        lay_period.setContentsMargins(2, 2, 2, 2)
        self._period_canvas = _MplCanvas(height=3.0)
        lay_period.addWidget(NavigationToolbar(self._period_canvas, w_period))
        lay_period.addWidget(self._period_canvas, stretch=1)
        chart_tabs.addTab(w_period, "固有周期")

        # --- グラフ 2: 参加質量比（累積） ---
        w_pm = QWidget()
        lay_pm = QVBoxLayout(w_pm)
        lay_pm.setContentsMargins(2, 2, 2, 2)
        self._pm_canvas = _MplCanvas(height=3.0)
        lay_pm.addWidget(NavigationToolbar(self._pm_canvas, w_pm))
        lay_pm.addWidget(self._pm_canvas, stretch=1)
        chart_tabs.addTab(w_pm, "参加質量比（累積）")

        # --- グラフ 3: 刺激関数 β ---
        w_beta = QWidget()
        lay_beta = QVBoxLayout(w_beta)
        lay_beta.setContentsMargins(2, 2, 2, 2)
        self._beta_canvas = _MplCanvas(height=3.0)
        lay_beta.addWidget(NavigationToolbar(self._beta_canvas, w_beta))
        lay_beta.addWidget(self._beta_canvas, stretch=1)
        chart_tabs.addTab(w_beta, "刺激関数 β")

        # --- グラフ 4: モード形状 (MDFloor.xbn) ---
        w_mdfloor = QWidget()
        lay_mdfloor = QVBoxLayout(w_mdfloor)
        lay_mdfloor.setContentsMargins(2, 2, 2, 2)
        ctrl_md = QHBoxLayout()
        ctrl_md.addWidget(QLabel("成分:"))
        self._mdfloor_field_combo = QComboBox()
        self._mdfloor_field_combo.setMinimumWidth(120)
        ctrl_md.addWidget(self._mdfloor_field_combo)
        ctrl_md.addSpacing(16)
        ctrl_md.addWidget(QLabel("ケース:"))
        self._mdfloor_case_combo = QComboBox()
        self._mdfloor_case_combo.setMinimumWidth(140)
        ctrl_md.addWidget(self._mdfloor_case_combo)
        ctrl_md.addStretch(1)
        lay_mdfloor.addLayout(ctrl_md)
        self._mdfloor_canvas = _MplCanvas(height=3.0)
        lay_mdfloor.addWidget(NavigationToolbar(self._mdfloor_canvas, w_mdfloor))
        lay_mdfloor.addWidget(self._mdfloor_canvas, stretch=1)
        self._mdfloor_field_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_period_mode_shapes()
        )
        self._mdfloor_case_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_period_mode_shapes()
        )
        chart_tabs.addTab(w_mdfloor, "🏗 モード形状")

        splitter.addWidget(chart_tabs)
        splitter.setSizes([200, 300])

        lay.addWidget(splitter, stretch=1)
        return w

    def _build_timehistory_tab(self, *, title: str, record_label: str,
                               quantities: List[tuple]) -> tuple:
        w = QWidget()
        lay = QVBoxLayout(w)
        lab = QLabel(title)
        lab.setStyleSheet("color:#555; padding:2px;")
        lay.addWidget(lab)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel(f"{record_label}:"))
        rec_combo = QComboBox()
        rec_combo.setMinimumWidth(140)
        ctrl.addWidget(rec_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("応答量:"))
        qty_combo = QComboBox()
        for name, idx, unit in quantities:
            label = f"{name}" + (f" [{unit}]" if unit else "")
            qty_combo.addItem(label, idx)
        ctrl.addWidget(qty_combo)
        ctrl.addStretch(1)
        lay.addLayout(ctrl)

        canvas = _MplCanvas()
        toolbar = NavigationToolbar(canvas, w)
        lay.addWidget(toolbar)
        lay.addWidget(canvas, stretch=1)

        peak_label = QLabel("")
        peak_label.setStyleSheet("color:#333; padding:4px;")
        peak_label.setWordWrap(True)
        lay.addWidget(peak_label)

        widgets = {
            "rec_combo": rec_combo,
            "qty_combo": qty_combo,
            "canvas": canvas,
            "peak_label": peak_label,
            "quantities": quantities,
            "category_name": None,  # set later (Floor/Story)
        }
        rec_combo.currentIndexChanged.connect(
            lambda *_: self._redraw_timehistory(widgets)
        )
        qty_combo.currentIndexChanged.connect(
            lambda *_: self._redraw_timehistory(widgets)
        )
        return w, widgets

    def _build_hysteresis_tab(self, *, record_label: str, fields: dict) -> tuple:
        w = QWidget()
        lay = QVBoxLayout(w)
        lab = QLabel(f"{record_label} の荷重–変形履歴ループおよび時刻歴")
        lab.setStyleSheet("color:#555; padding:2px;")
        lay.addWidget(lab)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel(f"{record_label}:"))
        rec_combo = QComboBox()
        rec_combo.setMinimumWidth(140)
        ctrl.addWidget(rec_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("表示:"))
        mode_combo = QComboBox()
        mode_combo.addItem("履歴ループ (F–D)", "loop")
        mode_combo.addItem("荷重 時刻歴", "force_time")
        mode_combo.addItem("変位 時刻歴", "disp_time")
        mode_combo.addItem("累積エネルギー 時刻歴", "energy_time")
        ctrl.addWidget(mode_combo)
        ctrl.addStretch(1)
        lay.addLayout(ctrl)

        canvas = _MplCanvas()
        toolbar = NavigationToolbar(canvas, w)
        lay.addWidget(toolbar)
        lay.addWidget(canvas, stretch=1)

        info_label = QLabel("")
        info_label.setStyleSheet("color:#333; padding:4px;")
        info_label.setWordWrap(True)
        lay.addWidget(info_label)

        widgets = {
            "rec_combo": rec_combo,
            "mode_combo": mode_combo,
            "canvas": canvas,
            "info_label": info_label,
            "fields": fields,
            "category_name": None,  # Damper / Spring
        }
        rec_combo.currentIndexChanged.connect(
            lambda *_: self._redraw_hysteresis(widgets)
        )
        mode_combo.currentIndexChanged.connect(
            lambda *_: self._redraw_hysteresis(widgets)
        )
        return w, widgets

    def _build_maxvals_tab(self) -> tuple:
        w = QWidget()
        lay = QVBoxLayout(w)
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("カテゴリ:"))
        cat_combo = QComboBox()
        cat_combo.setMinimumWidth(160)
        ctrl.addWidget(cat_combo)
        ctrl.addStretch(1)
        lay.addLayout(ctrl)

        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(table, stretch=1)

        widgets = {"cat_combo": cat_combo, "table": table}
        cat_combo.currentIndexChanged.connect(
            lambda *_: self._redraw_maxvals(widgets)
        )
        return w, widgets

    def _build_energy_tab(self) -> tuple:
        w = QWidget()
        lay = QVBoxLayout(w)
        lab = QLabel("Energy.hst 時刻歴（ケース重ね描き）")
        lab.setStyleSheet("color:#555; padding:2px;")
        lay.addWidget(lab)

        canvas = _MplCanvas()
        toolbar = NavigationToolbar(canvas, w)
        lay.addWidget(toolbar)
        lay.addWidget(canvas, stretch=1)

        info_label = QLabel("")
        info_label.setStyleSheet("color:#333; padding:4px;")
        info_label.setWordWrap(True)
        lay.addWidget(info_label)

        return w, {"canvas": canvas, "info_label": info_label}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_cases(self, cases: List) -> None:
        """AnalysisCase のリストを受け取り、リスト左パネルに表示します。"""
        self._cases = cases or []
        # 既存キャッシュはクリア（ケース構成が変わった可能性）
        self._entries.clear()
        self._active_entries = []

        self._case_list.blockSignals(True)
        self._case_list.clear()

        loaded_idx: List[int] = []
        for i, c in enumerate(self._cases):
            dirs = self._resolve_case_dirs(c)
            label = getattr(c, "name", None) or getattr(c, "id", f"case{i}")
            if not dirs:
                item = QListWidgetItem(f"{label}  (結果未検出)")
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                item.setForeground(Qt.gray)
                self._case_list.addItem(item)
                continue

            path = dirs[0]
            try:
                loader = SnapResultLoader(path, dt=float(self._dt_spin.value()))
            except Exception as e:
                item = QListWidgetItem(f"{label}  (読み込み失敗)")
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                item.setForeground(Qt.red)
                item.setToolTip(str(e))
                self._case_list.addItem(item)
                continue

            entry = _CaseEntry(name=label, path=path, loader=loader)
            self._entries[id(c)] = entry

            tags = []
            if loader.period:
                tags.append("固有値")
            if loader.get("Floor") and loader.get("Floor").hst:
                tags.append("時刻歴")
            if loader.get("Damper") and loader.get("Damper").hst:
                tags.append("ダンパー")
            suffix = f"  [{'/'.join(tags)}]" if tags else ""
            item = QListWidgetItem(f"{label}{suffix}")
            item.setData(Qt.UserRole, id(c))
            item.setToolTip(str(path))
            self._case_list.addItem(item)
            loaded_idx.append(self._case_list.count() - 1)

        self._case_list.blockSignals(False)

        # 最初に見つかったものをデフォルト選択
        if loaded_idx:
            self._case_list.setCurrentRow(loaded_idx[0])
            # trigger refresh
            self._on_case_selection_changed()
        else:
            self._show_empty_state()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _select_all_cases(self) -> None:
        for i in range(self._case_list.count()):
            item = self._case_list.item(i)
            if item.flags() & Qt.ItemIsSelectable:
                item.setSelected(True)

    def _on_case_selection_changed(self) -> None:
        selected = self._case_list.selectedItems()
        self._active_entries = []
        selected_cases = []
        for it in selected:
            cid = it.data(Qt.UserRole)
            if cid is None:
                continue
            entry = self._entries.get(cid)
            if entry:
                self._active_entries.append(entry)
            # 対応する AnalysisCase を復元
            for c in self._cases:
                if id(c) == cid:
                    selected_cases.append(c)
                    break

        # 選択ケースを外部に通知（CompareChartWidget 等が受信）
        self.cases_selected.emit(selected_cases)

        if not self._active_entries:
            self._show_empty_state()
            return

        parts = [f"{len(self._active_entries)} ケース選択中"]
        for e in self._active_entries[:4]:
            parts.append(f"{e.name}: {e.path}")
        if len(self._active_entries) > 4:
            parts.append(f"… 他 {len(self._active_entries) - 4} ケース")
        self._status_label.setText("\n".join(parts))

        self._refresh_all_tabs()

    def _on_dt_changed(self, *_) -> None:
        dt = float(self._dt_spin.value())
        for e in self._entries.values():
            e.loader.dt = dt
            for bc in e.loader.categories.values():
                if bc.hst:
                    bc.hst.dt = dt
        self._refresh_all_tabs()

    def _reload(self) -> None:
        # ケース全再読込
        self.set_cases(self._cases)

    # ------------------------------------------------------------------
    # 全タブ更新
    # ------------------------------------------------------------------
    def _show_empty_state(self) -> None:
        for key in self._tab_stacks:
            self._set_tab_state(key, False)
        if not self._cases:
            self._status_label.setText("解析ケースがありません。")
        elif not self._entries:
            self._status_label.setText(
                "選択されたケースに SNAP バイナリ結果フォルダが見つかりません。\n"
                "解析を実行済みのケースを選択してください。"
            )
        else:
            self._status_label.setText("左のリストからケースを選択してください。")

    def _refresh_all_tabs(self) -> None:
        if not self._active_entries:
            self._show_empty_state()
            return
        self._floor_widgets["category_name"] = "Floor"
        self._story_widgets["category_name"] = "Story"
        self._damper_widgets["category_name"] = "Damper"
        self._spring_widgets["category_name"] = "Spring"
        self._node_widgets["category_name"] = "Node"
        self._beam_widgets["category_name"] = "Beam"
        self._column_widgets["category_name"] = "Column"
        self._truss_widgets["category_name"] = "Truss"

        self._refresh_period_tab()
        self._refresh_timehistory_tab("floor", self._floor_widgets)
        self._refresh_timehistory_tab("story", self._story_widgets)
        self._refresh_hysteresis_tab("damper", self._damper_widgets)
        self._refresh_hysteresis_tab("spring", self._spring_widgets)
        self._refresh_timehistory_tab("node", self._node_widgets)
        self._refresh_hysteresis_tab("beam", self._beam_widgets)
        self._refresh_hysteresis_tab("column", self._column_widgets)
        self._refresh_hysteresis_tab("truss", self._truss_widgets)
        self._refresh_maxvals_tab()
        self._refresh_energy_tab()

    # ------------------------------------------------------------------
    # Period
    # ------------------------------------------------------------------
    def _refresh_period_tab(self) -> None:
        case_modes, rows = self._collect_period_data()
        if not rows:
            self._set_tab_state("period", False)
            return
        self._set_tab_state("period", True)

        self._populate_period_table(case_modes, rows)

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        case_names = list(case_modes.keys())
        max_modes = max((len(v) for v in case_modes.values()), default=0)

        self._draw_period_bars(case_modes, case_names, max_modes, colors)
        self._draw_cumulative_pm(case_modes, case_names, max_modes, colors)
        self._draw_beta_excitation(case_modes, case_names, max_modes, colors)
        self._refresh_period_mode_shapes()

    def _collect_period_data(self) -> Tuple[Dict[str, list], List[Tuple[str, object]]]:
        case_modes: Dict[str, list] = {}
        rows: List[Tuple[str, object]] = []
        for e in self._active_entries:
            if not e.loader.period or not e.loader.period.modes:
                continue
            for m in e.loader.period.modes:
                rows.append((e.name, m))
            case_modes[e.name] = list(e.loader.period.modes)
        return case_modes, rows

    def _populate_period_table(
        self,
        case_modes: Dict[str, list],
        rows: List[Tuple[str, object]],
    ) -> None:
        headers = ["ケース", "モード", "周期 T [s]", "振動数 f [Hz]",
                   "ω [rad/s]", "支配方向",
                   "β_X", "β_Y", "β_Z", "β_RX", "β_RY",
                   "有効質量 X [%]", "有効質量 Y [%]",
                   "有効質量 Z [%]", "有効質量 R [%]",
                   "累積PM_X [%]", "累積PM_Y [%]"]
        self._period_table.clear()
        self._period_table.setRowCount(len(rows))
        self._period_table.setColumnCount(len(headers))
        self._period_table.setHorizontalHeaderLabels(headers)

        cum_pm: Dict[str, Dict[int, Tuple[float, float]]] = {}
        for cname, modes in case_modes.items():
            cx, cy = 0.0, 0.0
            cum_pm[cname] = {}
            for m in sorted(modes, key=lambda x: x.mode_no):
                cx += abs(m.pm.get("X", 0))
                cy += abs(m.pm.get("Y", 0))
                cum_pm[cname][m.mode_no] = (cx, cy)

        for i, (case_name, m) in enumerate(rows):
            cpx, cpy = cum_pm.get(case_name, {}).get(m.mode_no, (0.0, 0.0))
            vals = [
                case_name,
                str(m.mode_no),
                f"{m.period:.4f}",
                f"{m.frequency:.4f}",
                f"{m.omega:.4f}",
                m.dominant_direction,
                f"{m.beta.get('X', 0):.4f}",
                f"{m.beta.get('Y', 0):.4f}",
                f"{m.beta.get('Z', 0):.4f}",
                f"{m.beta.get('RX', 0):.4f}",
                f"{m.beta.get('RY', 0):.4f}",
                f"{m.pm.get('X', 0):.2f}",
                f"{m.pm.get('Y', 0):.2f}",
                f"{m.pm.get('Z', 0):.2f}",
                f"{m.pm.get('R', 0):.2f}",
                f"{cpx:.2f}",
                f"{cpy:.2f}",
            ]
            for j, v in enumerate(vals):
                self._period_table.setItem(i, j, QTableWidgetItem(v))
        self._period_table.resizeColumnsToContents()

    def _draw_period_bars(
        self,
        case_modes: Dict[str, list],
        case_names: List[str],
        max_modes: int,
        colors: list,
    ) -> None:
        ax = self._period_canvas.ax
        ax.clear()
        if max_modes > 0:
            x = np.arange(max_modes)
            width = 0.8 / max(len(case_names), 1)
            for ci, (cname, modes) in enumerate(case_modes.items()):
                periods = [m.period for m in sorted(modes, key=lambda m: m.mode_no)]
                xi = x[:len(periods)] + (ci - len(case_names) / 2 + 0.5) * width
                ax.bar(xi, periods, width=width * 0.9,
                       label=cname, color=colors[ci % len(colors)], alpha=0.8)
            ax.set_xlabel("モード番号")
            ax.set_ylabel("固有周期 T [s]")
            ax.set_title("固有周期")
            ax.set_xticks(x)
            ax.set_xticklabels([str(i + 1) for i in range(max_modes)])
            ax.grid(True, axis="y", linestyle=":", alpha=0.5)
            if len(case_names) > 1:
                ax.legend(fontsize=8)
        self._period_canvas.fig.tight_layout()
        self._period_canvas.draw()

    def _draw_cumulative_pm(
        self,
        case_modes: Dict[str, list],
        case_names: List[str],
        max_modes: int,
        colors: list,
    ) -> None:
        ax2 = self._pm_canvas.ax
        ax2.clear()
        if max_modes > 0:
            for ci, (cname, modes) in enumerate(case_modes.items()):
                sorted_modes = sorted(modes, key=lambda m: m.mode_no)
                mode_nos = [m.mode_no for m in sorted_modes]
                cx_vals: List[float] = []
                cy_vals: List[float] = []
                cx, cy = 0.0, 0.0
                for m in sorted_modes:
                    cx += abs(m.pm.get("X", 0))
                    cy += abs(m.pm.get("Y", 0))
                    cx_vals.append(cx)
                    cy_vals.append(cy)
                c = colors[ci % len(colors)]
                ax2.plot(mode_nos, cx_vals,
                         label=f"{cname} X" if len(case_names) > 1 else "X方向",
                         color=c, marker="o", linewidth=1.5)
                ax2.plot(mode_nos, cy_vals,
                         label=f"{cname} Y" if len(case_names) > 1 else "Y方向",
                         color=c, linestyle="--", marker="s", linewidth=1.5)
            ax2.axhline(90, color="red", linestyle=":", linewidth=1, alpha=0.7,
                        label="90% 基準")
            ax2.set_xlabel("モード番号")
            ax2.set_ylabel("累積参加質量比 [%]")
            ax2.set_title("累積参加質量比 (X・Y方向)")
            ax2.set_ylim(0, 110)
            ax2.grid(True, linestyle=":", alpha=0.5)
            ax2.legend(fontsize=8)
        self._pm_canvas.fig.tight_layout()
        self._pm_canvas.draw()

    def _draw_beta_excitation(
        self,
        case_modes: Dict[str, list],
        case_names: List[str],
        max_modes: int,
        colors: list,
    ) -> None:
        ax3 = self._beta_canvas.ax
        ax3.clear()
        if max_modes > 0:
            for ci, (cname, modes) in enumerate(case_modes.items()):
                sorted_modes = sorted(modes, key=lambda m: m.mode_no)
                mode_nos = [m.mode_no for m in sorted_modes]
                beta_x = [m.beta.get("X", 0) for m in sorted_modes]
                beta_y = [m.beta.get("Y", 0) for m in sorted_modes]
                c = colors[ci % len(colors)]
                ax3.bar(
                    [n - 0.22 * (len(case_names) - 1) / 2 + ci * 0.22 for n in mode_nos],
                    beta_x, width=0.2,
                    label=f"{cname} β_X" if len(case_names) > 1 else "β_X (X方向)",
                    color=c, alpha=0.8)
                ax3.bar(
                    [n + 0.22 * (len(case_names) - 1) / 2 - ci * 0.22 for n in mode_nos],
                    beta_y, width=0.2,
                    label=f"{cname} β_Y" if len(case_names) > 1 else "β_Y (Y方向)",
                    color=c, alpha=0.5, hatch="/")
            ax3.axhline(0, color="#888", linewidth=0.7)
            ax3.set_xlabel("モード番号")
            ax3.set_ylabel("刺激関数 β")
            ax3.set_title("刺激関数 β（X・Y方向）— 絶対値が大きいモードが支配的")
            ax3.set_xticks(list(range(1, max_modes + 1)))
            ax3.grid(True, axis="y", linestyle=":", alpha=0.5)
            ax3.legend(fontsize=8)
        self._beta_canvas.fig.tight_layout()
        self._beta_canvas.draw()

    # ------------------------------------------------------------------
    # モード形状 (MDFloor.xbn)
    # ------------------------------------------------------------------
    def _refresh_period_mode_shapes(self) -> None:
        """MDFloor.xbn を使って固有モード形状（各階の変形）を図化する。"""
        canvas = self._mdfloor_canvas

        entries_with_md = self._collect_mdfloor_entries()
        if not entries_with_md:
            canvas.show_message(
                "MDFloor.xbn が見つかりません\n"
                "（SNAPの出力設定でMDFloor出力を有効にしてください）",
                color="gray",
            )
            return

        xbn = entries_with_md[0][1].xbn
        field_labels = xbn.field_labels()
        self._update_mdfloor_combos(entries_with_md, field_labels, xbn.values_per_record)

        field_idx = self._mdfloor_field_combo.currentData() or 0
        selected_case_name = self._mdfloor_case_combo.currentData()
        target_entries = [
            (e, bc) for e, bc in entries_with_md if e.name == selected_case_name
        ] or entries_with_md[:1]

        self._plot_mode_shapes(canvas, target_entries, field_idx, field_labels, xbn.num_records)

    def _collect_mdfloor_entries(self) -> List[tuple]:
        entries: List[tuple] = []
        for e in self._active_entries:
            bc = e.loader.get("MDFloor")
            if bc and bc.xbn and bc.xbn.records is not None:
                entries.append((e, bc))
        return entries

    def _update_mdfloor_combos(self, entries_with_md, field_labels, n_fields: int) -> None:
        # ケースコンボ
        self._mdfloor_case_combo.blockSignals(True)
        prev_case = self._mdfloor_case_combo.currentData()
        self._mdfloor_case_combo.clear()
        for e, _ in entries_with_md:
            self._mdfloor_case_combo.addItem(e.name, e.name)
        idx_c = next(
            (i for i in range(self._mdfloor_case_combo.count())
             if self._mdfloor_case_combo.itemData(i) == prev_case), 0
        )
        self._mdfloor_case_combo.setCurrentIndex(idx_c)
        self._mdfloor_case_combo.blockSignals(False)

        # フィールドコンボ
        self._mdfloor_field_combo.blockSignals(True)
        prev_field = self._mdfloor_field_combo.currentData()
        if self._mdfloor_field_combo.count() != n_fields:
            self._mdfloor_field_combo.clear()
            for i in range(n_fields):
                lbl = field_labels[i] if i < len(field_labels) else f"f{i}"
                self._mdfloor_field_combo.addItem(lbl, i)
        if prev_field is not None and 0 <= prev_field < n_fields:
            self._mdfloor_field_combo.setCurrentIndex(prev_field)
        self._mdfloor_field_combo.blockSignals(False)

    def _plot_mode_shapes(self, canvas, target_entries, field_idx: int,
                          field_labels, n_records_default: int) -> None:
        ax = canvas.ax
        ax.clear()
        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]

        for ci, (e, bc) in enumerate(target_entries):
            xbn_data = bc.xbn
            if xbn_data.records is None or field_idx >= xbn_data.values_per_record:
                continue
            vals = xbn_data.records[:, field_idx].astype(float)
            floors = list(range(len(vals)))
            c = colors[ci % len(colors)]
            ax.plot(vals, floors, "o-", color=c, linewidth=1.8,
                    markersize=5, label=e.name)

        n_recs = target_entries[0][1].xbn.num_records if target_entries else n_records_default
        stp_names_first = (
            target_entries[0][1].stp.names if target_entries and target_entries[0][1].stp else []
        )
        if stp_names_first and len(stp_names_first) >= n_recs:
            ax.set_yticks(range(n_recs))
            ax.set_yticklabels(stp_names_first[:n_recs], fontsize=7)
        else:
            ax.set_yticks(range(n_recs))
            ax.set_yticklabels([str(i + 1) for i in range(n_recs)], fontsize=7)

        ax.axvline(0, color="#888", linewidth=0.8, linestyle="--")
        field_lbl = field_labels[field_idx] if field_idx < len(field_labels) else f"f{field_idx}"
        ax.set_xlabel(f"振幅  [{field_lbl}]")
        ax.set_ylabel("階 (記録番号)")
        ax.set_title(f"モード形状 — {field_lbl}")
        ax.grid(True, axis="x", linestyle=":", alpha=0.5)
        ax.invert_yaxis()
        if len(target_entries) > 1:
            ax.legend(fontsize=8)
        try:
            canvas.fig.tight_layout()
        except (MemoryError, ValueError):
            logger.debug("tight_layout失敗 (mode shapes)")
        canvas.draw()

    # ------------------------------------------------------------------
    # 時刻歴 (Floor/Story)
    # ------------------------------------------------------------------
    def _first_category(self, cat_name: str) -> Optional[BinaryCategory]:
        for e in self._active_entries:
            bc = e.loader.get(cat_name)
            if bc and bc.hst:
                return bc
        return None

    def _refresh_timehistory_tab(self, key: str, widgets: dict) -> None:
        cat_name = widgets["category_name"]
        first = self._first_category(cat_name)
        if first is None:
            self._set_tab_state(key, False)
            return
        self._set_tab_state(key, True)

        rec_combo: QComboBox = widgets["rec_combo"]
        prev = rec_combo.currentData()
        rec_combo.blockSignals(True)
        rec_combo.clear()
        n = first.num_records
        for i in range(n):
            rec_combo.addItem(first.record_name(i), i)
        # 以前の選択を維持、なければ最上階
        if prev is not None and 0 <= prev < n:
            rec_combo.setCurrentIndex(prev)
        elif n > 0:
            rec_combo.setCurrentIndex(n - 1)
        rec_combo.blockSignals(False)
        self._redraw_timehistory(widgets)

    def _redraw_timehistory(self, widgets: dict) -> None:
        canvas: _MplCanvas = widgets["canvas"]
        peak_label: QLabel = widgets["peak_label"]
        quantities = widgets["quantities"]
        cat_name = widgets["category_name"]

        if not self._active_entries or cat_name is None:
            canvas.show_message("データがありません")
            peak_label.setText("")
            return

        rec_idx = widgets["rec_combo"].currentData()
        if rec_idx is None:
            return
        qty_idx = widgets["qty_combo"].currentIndex()
        if qty_idx < 0 or qty_idx >= len(quantities):
            return
        qty_name, field_idx, unit = quantities[qty_idx]

        ax = canvas.ax
        ax.clear()
        dt = float(self._dt_spin.value())

        plotted = 0
        peak_summaries: List[str] = []
        all_y_vals: List[np.ndarray] = []
        for e in self._active_entries:
            bc = e.loader.get(cat_name)
            if not bc or not bc.hst or bc.hst.header is None:
                continue
            if rec_idx >= bc.hst.header.num_records:
                continue
            if field_idx >= bc.hst.header.fields_per_record:
                continue
            try:
                bc.hst.dt = dt
                t = bc.hst.times()
                y = bc.hst.time_series(rec_idx, field_idx)
            except Exception:
                logger.debug("時刻歴データ読込失敗: %s", e.name, exc_info=True)
                continue
            ax.plot(t, y, linewidth=0.9, label=e.name)
            plotted += 1
            if y.size:
                all_y_vals.append(y)
                p = int(np.argmax(np.abs(y)))
                peak_summaries.append(
                    f"{e.name}: max={float(y[p]):+.4g}{unit} @ t={float(t[p]):.2f}s"
                )

        if plotted == 0:
            canvas.show_message("このレコード/応答量はどのケースにも存在しません")
            peak_label.setText("")
            return

        first_bc = self._first_category(cat_name)
        rec_name = first_bc.record_name(rec_idx) if first_bc else f"rec{rec_idx}"
        ax.set_title(f"{rec_name} / {qty_name}")
        ax.set_xlabel("時間 [s]")
        ax.set_ylabel(f"{qty_name}" + (f" [{unit}]" if unit else ""))
        ax.grid(True, linestyle=":", alpha=0.5)
        if plotted > 1:
            ax.legend(loc="best", fontsize=8)

        # Y軸をデータ範囲にフィットさせる（値が小さくても軸が広がりすぎないようにする）
        if all_y_vals:
            _set_tight_ylim(ax, np.concatenate(all_y_vals))

        canvas.fig.tight_layout()
        canvas.draw()

        peak_label.setText("  |  ".join(peak_summaries))

    # ------------------------------------------------------------------
    # 履歴 (Damper/Spring)
    # ------------------------------------------------------------------
    def _refresh_hysteresis_tab(self, key: str, widgets: dict) -> None:
        cat_name = widgets["category_name"]
        first = self._first_category(cat_name)
        if first is None:
            self._set_tab_state(key, False)
            return

        # fields_per_record が 0 の場合は hst が壊れているか形式不明
        if first.hst and first.hst.header and first.hst.header.fields_per_record <= 0:
            self._set_tab_state(key, True)
            h = first.hst.header
            canvas: _MplCanvas = widgets["canvas"]
            canvas.show_message(
                f"{cat_name}.hst を読み取れませんでした。\n"
                f"step_size={h.step_size}, num_records={h.num_records}\n"
                f"step_size が (step_header + num_records × N) の形式に割り切れません。\n"
                "「再読込」ボタンで再試行するか、dt 値を確認してください。",
                color="red",
            )
            widgets["info_label"].setText(
                f"[診断] {cat_name}.hst: step_size={h.step_size}, "
                f"num_records={h.num_records}, fields_per_record が未確定"
            )
            return

        self._set_tab_state(key, True)

        rec_combo: QComboBox = widgets["rec_combo"]
        prev = rec_combo.currentData()
        rec_combo.blockSignals(True)
        rec_combo.clear()
        n = first.num_records
        for i in range(n):
            rec_combo.addItem(first.record_name(i), i)
        if prev is not None and 0 <= prev < n:
            rec_combo.setCurrentIndex(prev)
        elif n > 0:
            rec_combo.setCurrentIndex(0)
        rec_combo.blockSignals(False)
        self._redraw_hysteresis(widgets)

    def _redraw_hysteresis(self, widgets: dict) -> None:
        canvas: _MplCanvas = widgets["canvas"]
        info_label: QLabel = widgets["info_label"]
        fields: dict = widgets["fields"]
        cat_name = widgets["category_name"]

        if not self._active_entries or cat_name is None:
            canvas.show_message("データがありません")
            info_label.setText("")
            return

        rec_idx = widgets["rec_combo"].currentData()
        if rec_idx is None:
            return
        mode = widgets["mode_combo"].currentData() or "loop"

        ax = canvas.ax
        ax.clear()
        dt = float(self._dt_spin.value())

        plotted, summaries, loop_x, loop_y, time_y = self._draw_hysteresis_entries(
            ax, cat_name, rec_idx, mode, fields, dt
        )

        if plotted == 0:
            self._show_hysteresis_empty(canvas, info_label, summaries)
            return

        first_bc = self._first_category(cat_name)
        rec_name = first_bc.record_name(rec_idx) if first_bc else f"rec{rec_idx}"
        if mode == "loop":
            self._apply_hysteresis_loop_axes(ax, rec_name, loop_x, loop_y)
        else:
            self._apply_hysteresis_time_axes(ax, rec_name, time_y)
        if plotted > 1:
            ax.legend(loc="best", fontsize=8)
        canvas.fig.tight_layout()
        canvas.draw()
        info_label.setText("  |  ".join(summaries))

    def _draw_hysteresis_entries(
        self,
        ax,
        cat_name: str,
        rec_idx: int,
        mode: str,
        fields: dict,
        dt: float,
    ) -> Tuple[int, List[str], List[np.ndarray], List[np.ndarray], List[np.ndarray]]:
        plotted = 0
        summaries: List[str] = []
        loop_x: List[np.ndarray] = []
        loop_y: List[np.ndarray] = []
        time_y: List[np.ndarray] = []
        for e in self._active_entries:
            bc = e.loader.get(cat_name)
            if not bc or not bc.hst or bc.hst.header is None:
                continue
            if rec_idx >= bc.hst.header.num_records:
                continue
            try:
                bc.hst.dt = dt
                t = bc.hst.times()
                F = bc.hst.time_series(rec_idx, fields["荷重 F"])
                D = bc.hst.time_series(rec_idx, fields["変位 D"])
            except Exception as ex:
                summaries.append(f"{e.name}: 読み取りエラー — {ex}")
                continue

            if mode == "loop":
                self._plot_hysteresis_loop(ax, e.name, D, F, loop_x, loop_y, summaries)
                plotted += 1
            else:
                ok = self._plot_hysteresis_time(
                    ax, e, bc, rec_idx, cat_name, mode, t, F, D, time_y, summaries
                )
                if ok:
                    plotted += 1
        return plotted, summaries, loop_x, loop_y, time_y

    @staticmethod
    def _plot_hysteresis_loop(
        ax,
        name: str,
        D: np.ndarray,
        F: np.ndarray,
        loop_x: List[np.ndarray],
        loop_y: List[np.ndarray],
        summaries: List[str],
    ) -> None:
        ax.plot(D, F, linewidth=0.7, label=name)
        loop_x.append(D)
        loop_y.append(F)
        try:
            e_abs = float(np.trapz(F, D))
        except Exception:
            logger.debug("エネルギー積分計算失敗: %s", name)
            e_abs = 0.0
        summaries.append(
            f"{name}: |F|max={float(np.max(np.abs(F))):.4g}, "
            f"|D|max={float(np.max(np.abs(D))):.4g}, ∮FdD≈{e_abs:.4g}"
        )

    @staticmethod
    def _plot_hysteresis_time(
        ax,
        e,
        bc,
        rec_idx: int,
        cat_name: str,
        mode: str,
        t: np.ndarray,
        F: np.ndarray,
        D: np.ndarray,
        time_y: List[np.ndarray],
        summaries: List[str],
    ) -> bool:
        if mode == "force_time":
            y = F
        elif mode == "disp_time":
            y = D
        else:
            try:
                e_idx = energy_field_index(
                    cat_name, bc.hst.header.fields_per_record
                )
                y = bc.hst.time_series(rec_idx, e_idx)
            except Exception:
                logger.debug("累積エネルギー読込失敗: %s", e.name)
                return False
        ax.plot(t, y, linewidth=0.9, label=e.name)
        if y.size:
            time_y.append(y)
            p = int(np.argmax(np.abs(y)))
            summaries.append(
                f"{e.name}: max={float(y[p]):+.4g} @ t={float(t[p]):.2f}s"
            )
        return True

    @staticmethod
    def _show_hysteresis_empty(canvas, info_label: QLabel, summaries: List[str]) -> None:
        err_detail = "\n".join(summaries) if summaries else ""
        canvas.show_message(
            "このレコードのデータが読み取れませんでした\n" + err_detail
            if err_detail else "どのケースにもこのレコードがありません",
            color="red" if err_detail else "gray",
        )
        info_label.setText(err_detail)

    @staticmethod
    def _apply_hysteresis_loop_axes(
        ax,
        rec_name: str,
        loop_x: List[np.ndarray],
        loop_y: List[np.ndarray],
    ) -> None:
        ax.set_xlabel("変位 D")
        ax.set_ylabel("荷重 F")
        ax.set_title(f"{rec_name} 履歴ループ")
        ax.axhline(0, color="#888", linewidth=0.5)
        ax.axvline(0, color="#888", linewidth=0.5)
        if loop_x and loop_y:
            _set_tight_ylim(ax, np.concatenate(loop_y))
            dx = np.concatenate(loop_x)
            dx_range = float(dx.max() - dx.min())
            m = dx_range * 0.1 if dx_range > 1e-15 else max(abs(float(dx.max())) * 0.2, 1e-6)
            ax.set_xlim(float(dx.min()) - m, float(dx.max()) + m)

    @staticmethod
    def _apply_hysteresis_time_axes(
        ax,
        rec_name: str,
        time_y: List[np.ndarray],
    ) -> None:
        ax.set_xlabel("時間 [s]")
        ax.set_title(f"{rec_name}")
        ax.grid(True, linestyle=":", alpha=0.5)
        if time_y:
            _set_tight_ylim(ax, np.concatenate(time_y))

    # ------------------------------------------------------------------
    # 最大値 (.xbn)
    # ------------------------------------------------------------------
    def _refresh_maxvals_tab(self) -> None:
        # 全選択ケースに存在するカテゴリを集計
        cats_set: List[str] = []
        for e in self._active_entries:
            for name, bc in e.loader.categories.items():
                if bc.xbn is not None and bc.xbn.records is not None:
                    if name not in cats_set:
                        cats_set.append(name)
        if not cats_set:
            self._set_tab_state("maxvals", False)
            return
        self._set_tab_state("maxvals", True)

        cat_combo: QComboBox = self._maxvals_widgets["cat_combo"]
        prev = cat_combo.currentData()
        cat_combo.blockSignals(True)
        cat_combo.clear()
        for name in cats_set:
            cat_combo.addItem(name, name)
        if prev and prev in cats_set:
            cat_combo.setCurrentIndex(cats_set.index(prev))
        cat_combo.blockSignals(False)
        self._redraw_maxvals(self._maxvals_widgets)

    def _redraw_maxvals(self, widgets: dict) -> None:
        table: QTableWidget = widgets["table"]
        table.clear()
        table.setRowCount(0)
        table.setColumnCount(0)
        cat_name = widgets["cat_combo"].currentData()
        if not cat_name or not self._active_entries:
            return

        # ケースごとに .xbn を積み上げ（ケース列を追加）
        rows = []
        labels: List[str] = []
        for e in self._active_entries:
            bc = e.loader.get(cat_name)
            if not bc or not bc.xbn or bc.xbn.records is None:
                continue
            rec = bc.xbn.records
            lb = bc.xbn.field_labels()
            if not labels:
                labels = lb
            for i in range(rec.shape[0]):
                row = [e.name, bc.record_name(i)]
                for j in range(rec.shape[1]):
                    row.append(f"{float(rec[i, j]):.4g}")
                rows.append(row)
        if not rows:
            return

        headers = ["ケース", "レコード"] + labels
        table.setRowCount(len(rows))
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                table.setItem(i, j, QTableWidgetItem(v))
        table.resizeColumnsToContents()

    # ------------------------------------------------------------------
    # エネルギー
    # ------------------------------------------------------------------
    def _refresh_energy_tab(self) -> None:
        has_any = False
        for e in self._active_entries:
            bc = e.loader.get("Energy")
            if bc and bc.hst and bc.hst.header is not None:
                has_any = True
                break
        if not has_any:
            self._set_tab_state("energy", False)
            return
        self._set_tab_state("energy", True)

        canvas: _MplCanvas = self._energy_widgets["canvas"]
        info_label: QLabel = self._energy_widgets["info_label"]
        ax = canvas.ax
        ax.clear()
        dt = float(self._dt_spin.value())

        plotted = 0
        for e in self._active_entries:
            bc = e.loader.get("Energy")
            if not bc or not bc.hst or bc.hst.header is None:
                continue
            try:
                bc.hst.dt = dt
                t = bc.hst.times()
                fpr = bc.hst.header.fields_per_record
                n_rec = bc.hst.header.num_records
                if n_rec >= 1 and fpr > 0:
                    # 代表として f0 を引く（全系列を重ねるとケース比較で混乱するので省略）
                    y = bc.hst.time_series(0, 0)
                else:
                    raw = bc.hst._raw
                    if raw is None:
                        continue
                    y = raw[:, 1] if raw.shape[1] > 1 else raw[:, 0]
            except Exception:
                logger.debug("エネルギーデータ読込失敗: %s", e.name, exc_info=True)
                continue
            if np.any(y):
                ax.plot(t, y, linewidth=0.9, label=e.name)
                plotted += 1

        if plotted == 0:
            canvas.show_message("Energy.hst にプロット可能な信号がありません")
            info_label.setText("")
            return

        ax.set_xlabel("時間 [s]")
        ax.set_ylabel("エネルギー")
        ax.set_title("Energy.hst (代表フィールド)")
        ax.grid(True, linestyle=":", alpha=0.5)
        if plotted > 1:
            ax.legend(loc="best", fontsize=8)
        canvas.fig.tight_layout()
        canvas.draw()
        info_label.setText(f"{plotted} ケース表示")

    # ------------------------------------------------------------------
    # ケースフォルダ解決
    # ------------------------------------------------------------------
    @staticmethod
    def _looks_like_result_dir(p: Path) -> bool:
        if not p.exists() or not p.is_dir():
            return False
        if (p / "Period.xbn").exists():
            return True
        if any(p.glob("*.hst")):
            return True
        if any(p.glob("*.xbn")):
            return True
        return False

    def _resolve_case_dirs(self, case) -> List[Path]:
        """AnalysisCase から実際の SNAP バイナリ結果フォルダ候補を列挙する。

        SNAP は通常 snap_work_dir/{model_stem}/D{N}/ に結果を書くため、
        解析実行時に `case.binary_result_dir` と
        `case.dyc_results[*]["result_dir"]` に実パスが保存されている。
        それらを優先的に使用する。
        """
        candidates: List[Path] = []

        def _add(p):
            if not p:
                return
            pp = Path(p)
            if pp not in candidates:
                candidates.append(pp)

        # 1. 解析実行時に保存された実パス
        _add(getattr(case, "binary_result_dir", None))

        # 2. dyc_results の個別結果フォルダ
        for dr in getattr(case, "dyc_results", []) or []:
            _add(dr.get("result_dir"))

        # 3. result_path / output_dir
        for attr in ("result_path", "output_dir"):
            v = getattr(case, attr, None)
            if not v:
                continue
            _add(v)
            p = Path(v)
            # サブフォルダ（D1/D2/... または dyc 名）
            if p.exists():
                for sub in p.iterdir():
                    if sub.is_dir() and (
                        sub.name.startswith("D") or sub.name == getattr(case, "name", "")
                    ):
                        _add(sub)

        # 4. model_path のあるフォルダ
        model_path = getattr(case, "model_path", None)
        if model_path:
            base = Path(model_path).parent
            _add(base)
            if base.exists():
                for sub in base.iterdir():
                    if sub.is_dir() and sub.name.startswith("D"):
                        _add(sub)
            name = getattr(case, "name", None)
            if name:
                _add(base / name)

        # 実在かつバイナリを含むものだけ返す
        return [p for p in candidates if self._looks_like_result_dir(p)]
