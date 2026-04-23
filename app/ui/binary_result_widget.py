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

from PySide6.QtCore import Qt
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

from app.ui.case_dyc_selector_widget import DycSelection

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

# ---------------------------------------------------------------------------
# ダンパー部材フィールド定義 (SNAP ツリー構造に基づく)
#   制振ブレース → 質量(主/付加) → ダッシュポット(主/付加1/2)
#   → スプリング(Voigt/Maxwell/Maxwell付加1/2/全体)
# ---------------------------------------------------------------------------
_DAMPER_FIELD_MAP: List[Tuple[int, str, str]] = [
    # 制振ブレース（取付け部）
    (0, "ブレース/応力", "kN"),
    (1, "ブレース/変形", "m"),
    (2, "ブレース/エネルギー", "kN·m"),
    # 質量（主）
    (3, "質量(主)/応力", "kN"),
    (4, "質量(主)/加速度", "m/s²"),
    (5, "質量(主)/エネルギー", "kN·m"),
    # 質量（付加）
    (6, "質量(付加)/応力", "kN"),
    (7, "質量(付加)/加速度", "m/s²"),
    (8, "質量(付加)/エネルギー", "kN·m"),
    # ダッシュポット（主）
    (9, "ダッシュポット(主)/応力", "kN"),
    (10, "ダッシュポット(主)/速度", "m/s"),
    (11, "ダッシュポット(主)/変形", "m"),
    (12, "ダッシュポット(主)/エネルギー", "kN·m"),
    # ダッシュポット（付加1）
    (13, "ダッシュポット(付加1)/応力", "kN"),
    (14, "ダッシュポット(付加1)/速度", "m/s"),
    (15, "ダッシュポット(付加1)/エネルギー", "kN·m"),
    # ダッシュポット（付加2）
    (16, "ダッシュポット(付加2)/応力", "kN"),
    (17, "ダッシュポット(付加2)/速度", "m/s"),
    (18, "ダッシュポット(付加2)/エネルギー", "kN·m"),
    # スプリング（Voigt）
    (19, "スプリング(Voigt)/応力", "kN"),
    (20, "スプリング(Voigt)/変形", "m"),
    (21, "スプリング(Voigt)/塑性率", ""),
    (22, "スプリング(Voigt)/累積塑性変形倍率", ""),
    (23, "スプリング(Voigt)/エネルギー", "kN·m"),
    (24, "スプリング(Voigt)/歪振幅頻度分布", ""),
    # スプリング（Maxwell）
    (25, "スプリング(Maxwell)/応力", "kN"),
    (26, "スプリング(Maxwell)/変形", "m"),
    (27, "スプリング(Maxwell)/エネルギー", "kN·m"),
    # スプリング（Maxwell付加1）
    (28, "スプリング(Maxwell付加1)/応力", "kN"),
    (29, "スプリング(Maxwell付加1)/変形", "m"),
    (30, "スプリング(Maxwell付加1)/エネルギー", "kN·m"),
    # スプリング（Maxwell付加2）
    (31, "スプリング(Maxwell付加2)/応力", "kN"),
    (32, "スプリング(Maxwell付加2)/変形", "m"),
    (33, "スプリング(Maxwell付加2)/エネルギー", "kN·m"),
    # スプリング（全体）
    (34, "スプリング(全体)/応力", "kN"),
    (35, "スプリング(全体)/変形", "m"),
    (36, "スプリング(全体)/エネルギー", "kN·m"),
]

_DAMPER_LOOP_PAIRS: List[Tuple[int, int, str]] = [
    # (x_field, y_field, label) — x=変形/速度/加速度, y=応力
    (1, 0, "ブレース 応力–変形"),
    (4, 3, "質量(主) 応力–加速度"),
    (7, 6, "質量(付加) 応力–加速度"),
    (10, 9, "ダッシュポット(主) 応力–速度"),
    (14, 13, "ダッシュポット(付加1) 応力–速度"),
    (17, 16, "ダッシュポット(付加2) 応力–速度"),
    (20, 19, "スプリング(Voigt) 応力–変形"),
    (26, 25, "スプリング(Maxwell) 応力–変形"),
    (29, 28, "スプリング(Maxwell付加1) 応力–変形"),
    (32, 31, "スプリング(Maxwell付加2) 応力–変形"),
    (35, 34, "スプリング(全体) 応力–変形"),
]

# ---------------------------------------------------------------------------
# スプリング部材フィールド定義
# ---------------------------------------------------------------------------
_SPRING_FIELD_MAP: List[Tuple[int, str, str]] = [
    (0, "Voigt/応力", "kN"),
    (1, "Voigt/変形", "m"),
    (2, "Voigt/塑性率", ""),
    (3, "Voigt/累積塑性変形倍率", ""),
    (4, "Voigt/エネルギー", "kN·m"),
    (5, "Voigt/歪振幅頻度分布", ""),
    (6, "Maxwell/応力", "kN"),
    (7, "Maxwell/変形", "m"),
    (8, "Maxwell/エネルギー", "kN·m"),
    (9, "Maxwell付加1/応力", "kN"),
    (10, "Maxwell付加1/変形", "m"),
    (11, "Maxwell付加1/エネルギー", "kN·m"),
    (12, "Maxwell付加2/応力", "kN"),
    (13, "Maxwell付加2/変形", "m"),
    (14, "Maxwell付加2/エネルギー", "kN·m"),
    (15, "全体/応力", "kN"),
    (16, "全体/変形", "m"),
    (17, "全体/エネルギー", "kN·m"),
]

_SPRING_LOOP_PAIRS: List[Tuple[int, int, str]] = [
    (1, 0, "Voigt 応力–変形"),
    (7, 6, "Maxwell 応力–変形"),
    (10, 9, "Maxwell付加1 応力–変形"),
    (13, 12, "Maxwell付加2 応力–変形"),
    (16, 15, "全体 応力–変形"),
]

_NODE_QUANTITIES: List[tuple] = [
    ("変位", 0, "m"),
    ("応答量 f1", 1, ""),
    ("応答量 f2", 2, ""),
    ("応答量 f3", 3, ""),
    ("速度", 4, "m/s"),
    ("応答量 f5", 5, ""),
    ("加速度", 6, "m/s²"),
]

# ---------------------------------------------------------------------------
# はり・柱・トラス（単純部材）フィールド定義
# ---------------------------------------------------------------------------
_MEMBER_FIELD_MAP: List[Tuple[int, str, str]] = [
    (0, "荷重 F", "kN"),
    (1, "変位 D", "m"),
]

_MEMBER_LOOP_PAIRS: List[Tuple[int, int, str]] = [
    (1, 0, "荷重–変位"),
]


# ---------------------------------------------------------------------------
# 各ケースのロード結果を保持するエントリ
# ---------------------------------------------------------------------------
@dataclass
class _CaseEntry:
    name: str
    path: Path
    loader: SnapResultLoader
    selection: Optional["DycSelection"] = None


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
    """SNAP バイナリ結果を種類別・マルチケース比較表示するウィジェット。

    ケース選択は外部の ``CaseDycSelectorWidget`` が担う。
    ``set_dyc_selections(selections)`` に ``DycSelection`` のリストを渡すと
    それぞれを独立した _CaseEntry として扱い、全結果タブを再描画する。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List = []
        # ロード済みケース: id(case) -> _CaseEntry
        self._entries: Dict[int, _CaseEntry] = {}
        # 現在アクティブな（選択中の）ケース
        self._active_entries: List[_CaseEntry] = []
        # 外部セレクタが一度でも set_dyc_selections を呼んだら True
        self._selections_externally_driven: bool = False

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
            field_map=_DAMPER_FIELD_MAP,
            loop_pairs=_DAMPER_LOOP_PAIRS,
        )
        self._tab_stacks["damper"] = self._make_stack(self._damper_content)
        self._tabs.addTab(self._tab_stacks["damper"], "🛡 ダンパー履歴")

        self._spring_content, self._spring_widgets = self._build_hysteresis_tab(
            record_label="バネ",
            field_map=_SPRING_FIELD_MAP,
            loop_pairs=_SPRING_LOOP_PAIRS,
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
            field_map=_MEMBER_FIELD_MAP,
            loop_pairs=_MEMBER_LOOP_PAIRS,
        )
        self._tab_stacks["beam"] = self._make_stack(self._beam_content)
        self._tabs.addTab(self._tab_stacks["beam"], "🔗 はり応答")

        self._column_content, self._column_widgets = self._build_hysteresis_tab(
            record_label="柱",
            field_map=_MEMBER_FIELD_MAP,
            loop_pairs=_MEMBER_LOOP_PAIRS,
        )
        self._tab_stacks["column"] = self._make_stack(self._column_content)
        self._tabs.addTab(self._tab_stacks["column"], "🏛 柱応答")

        self._truss_content, self._truss_widgets = self._build_hysteresis_tab(
            record_label="トラス",
            field_map=_MEMBER_FIELD_MAP,
            loop_pairs=_MEMBER_LOOP_PAIRS,
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

    def _build_body(self) -> QVBoxLayout:
        body = QVBoxLayout()
        self._status_label = QLabel("解析ケースを選択してください。")
        self._status_label.setStyleSheet(
            "color: #555; padding: 4px; background: #f5f5f5; border-radius: 4px;"
        )
        self._status_label.setWordWrap(True)
        body.addWidget(self._status_label)
        body.addWidget(self._build_result_tabs(), stretch=1)
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

        # モード形状は独立タブ (ModeShapeWidget) に統合済み。
        # ここには重複表示を置かない。

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

    def _build_hysteresis_tab(self, *, record_label: str,
                              field_map: List[Tuple[int, str, str]] = None,
                              loop_pairs: List[Tuple[int, int, str]] = None) -> tuple:
        w = QWidget()
        lay = QVBoxLayout(w)
        lab = QLabel(f"{record_label} の応答履歴")
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
        mode_combo.addItem("履歴ループ", "loop")
        mode_combo.addItem("時刻歴", "time")
        ctrl.addWidget(mode_combo)

        ctrl.addSpacing(8)
        qty_combo = QComboBox()
        qty_combo.setMinimumWidth(220)
        ctrl.addWidget(qty_combo)

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
            "qty_combo": qty_combo,
            "canvas": canvas,
            "info_label": info_label,
            "field_map": field_map or [],
            "loop_pairs": loop_pairs or [(1, 0, "荷重–変位")],
            "category_name": None,
            "_fpr": 0,  # fields_per_record cache
        }
        rec_combo.currentIndexChanged.connect(
            lambda *_: self._redraw_hysteresis(widgets)
        )
        mode_combo.currentIndexChanged.connect(
            lambda *_: self._on_hysteresis_mode_changed(widgets)
        )
        qty_combo.currentIndexChanged.connect(
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
        """AnalysisCase のリストを記憶するのみ。表示内容は set_dyc_selections() で決まる。

        互換: 外部セレクタがまだ何も送っていない状態で set_cases だけ呼ばれた場合は、
        各ケースの最初の DYC を自動選択して表示する（従来挙動の代替）。
        """
        self._cases = list(cases or [])
        self._entries.clear()
        self._active_entries = []

        if not self._selections_externally_driven:
            auto_sels = self._auto_selections_from_cases(self._cases)
            self._apply_selections(auto_sels)
        else:
            self._show_empty_state()

    def set_dyc_selections(self, selections: List[DycSelection]) -> None:
        """外部セレクタから (case, DYC) 選択を受け取り、結果タブを再描画する。"""
        self._selections_externally_driven = True
        self._apply_selections(selections or [])

    # ------------------------------------------------------------------
    # Selection → entries
    # ------------------------------------------------------------------
    def _auto_selections_from_cases(self, cases: List) -> List[DycSelection]:
        """外部セレクタが無いときに、ケース毎に最初の解析済み DYC を 1 件選ぶ。"""
        out: List[DycSelection] = []
        for c in cases:
            dyc_results = getattr(c, "dyc_results", []) or []
            picked = False
            for i, dr in enumerate(dyc_results):
                if dr.get("run_flag") and dr.get("has_result") and dr.get("result_dir"):
                    out.append(DycSelection(
                        case=c, dyc_index=i,
                        result_dir=Path(dr["result_dir"]),
                    ))
                    picked = True
                    break
            if not picked:
                # フォールバック: case.binary_result_dir / _resolve_case_dirs[0]
                dirs = self._resolve_case_dirs(c)
                if dirs:
                    out.append(DycSelection(
                        case=c, dyc_index=-1, result_dir=dirs[0],
                    ))
        return out

    def _apply_selections(self, selections: List[DycSelection]) -> None:
        """DycSelection のリストから _active_entries を再構築してタブを更新する。"""
        entries: List[_CaseEntry] = []
        failures: List[str] = []
        dt = float(self._dt_spin.value())

        for sel in selections:
            path = sel.result_dir
            if path is None:
                # DYC 情報に result_dir が無い場合は _resolve_case_dirs にフォールバック
                dirs = self._resolve_case_dirs(sel.case)
                if not dirs:
                    failures.append(f"{sel.display_name}: 結果フォルダ未検出")
                    continue
                path = dirs[0]
            try:
                loader = SnapResultLoader(Path(path), dt=dt)
            except Exception as e:
                failures.append(f"{sel.display_name}: {e}")
                continue
            entries.append(_CaseEntry(
                name=sel.short_name,
                path=Path(path),
                loader=loader,
                selection=sel,
            ))

        self._active_entries = entries
        # _entries も簡易的に同期（id ベース）
        self._entries = {id(e.selection.case): e for e in entries if e.selection}

        if not entries:
            msg = "表示可能な結果がありません。"
            if failures:
                msg = msg + "\n" + "\n".join(failures[:4])
            self._status_label.setText(msg)
            self._show_empty_state()
            return

        parts = [f"{len(entries)} 件選択中"]
        for e in entries[:4]:
            parts.append(f"{e.name}: {e.path}")
        if len(entries) > 4:
            parts.append(f"… 他 {len(entries) - 4} 件")
        if failures:
            parts.append(f"(失敗 {len(failures)} 件)")
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
        # アクティブな選択を復元して再ロード
        prev = [e.selection for e in self._active_entries if e.selection] if self._active_entries else []
        if prev:
            self._apply_selections(prev)
        elif self._cases:
            self._apply_selections(self._auto_selections_from_cases(self._cases))

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
                "左の解析ケースツリーで、表示したい DYC サブケースをチェックしてください。"
            )
        else:
            self._status_label.setText("左のツリーからケースを選択してください。")

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

        # rec_combo 更新
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

        # fields_per_record を取得して qty_combo を動的更新
        fpr = first.hst.header.fields_per_record if first.hst and first.hst.header else 2
        widgets["_fpr"] = fpr
        self._populate_qty_combo(widgets)
        self._redraw_hysteresis(widgets)

    # ------------------------------------------------------------------
    # qty_combo 動的更新
    # ------------------------------------------------------------------

    def _on_hysteresis_mode_changed(self, widgets: dict) -> None:
        """表示モード (履歴ループ/時刻歴) が変更されたとき qty_combo を再構築する。"""
        self._populate_qty_combo(widgets)
        self._redraw_hysteresis(widgets)

    def _populate_qty_combo(self, widgets: dict) -> None:
        """現在のモードと fields_per_record に基づいて応答量コンボを構築する。"""
        mode = widgets["mode_combo"].currentData()
        qty: QComboBox = widgets["qty_combo"]
        fpr = widgets.get("_fpr", 2)
        field_map: List[Tuple[int, str, str]] = widgets.get("field_map", [])
        loop_pairs: List[Tuple[int, int, str]] = widgets.get("loop_pairs", [])

        qty.blockSignals(True)
        prev = qty.currentData()
        qty.clear()

        if mode == "loop":
            for x_idx, y_idx, label in loop_pairs:
                if x_idx < fpr and y_idx < fpr:
                    qty.addItem(label, ("loop", x_idx, y_idx))
            # フォールバック: 既知ループペアが無い場合、f0–f1 を追加
            if qty.count() == 0 and fpr >= 2:
                qty.addItem("f1–f0", ("loop", 1, 0))
        else:
            # 時刻歴: 全フィールドを列挙
            label_dict = {idx: (lbl, unit) for idx, lbl, unit in field_map}
            for i in range(fpr):
                lbl, unit = label_dict.get(i, (f"f{i}", ""))
                display = f"{lbl}" + (f" [{unit}]" if unit else "")
                qty.addItem(display, ("time", i))

        # 以前の選択を復元
        if prev is not None:
            for i in range(qty.count()):
                if qty.itemData(i) == prev:
                    qty.setCurrentIndex(i)
                    break

        qty.blockSignals(False)

    # ------------------------------------------------------------------
    # 履歴描画
    # ------------------------------------------------------------------

    def _redraw_hysteresis(self, widgets: dict) -> None:
        canvas: _MplCanvas = widgets["canvas"]
        info_label: QLabel = widgets["info_label"]
        cat_name = widgets["category_name"]

        if not self._active_entries or cat_name is None:
            canvas.show_message("データがありません")
            info_label.setText("")
            return

        rec_idx = widgets["rec_combo"].currentData()
        if rec_idx is None:
            return

        qty_data = widgets["qty_combo"].currentData()
        if qty_data is None:
            return

        ax = canvas.ax
        ax.clear()
        dt = float(self._dt_spin.value())
        field_map: List[Tuple[int, str, str]] = widgets.get("field_map", [])
        label_dict = {idx: (lbl, unit) for idx, lbl, unit in field_map}

        mode_type = qty_data[0]  # "loop" or "time"

        if mode_type == "loop":
            _, x_idx, y_idx = qty_data
            plotted, summaries, all_x, all_y = self._draw_loop_entries(
                ax, cat_name, rec_idx, x_idx, y_idx, dt
            )
            if plotted == 0:
                self._show_hysteresis_empty(canvas, info_label, summaries)
                return
            first_bc = self._first_category(cat_name)
            rec_name = first_bc.record_name(rec_idx) if first_bc else f"rec{rec_idx}"
            x_lbl, x_unit = label_dict.get(x_idx, (f"f{x_idx}", ""))
            y_lbl, y_unit = label_dict.get(y_idx, (f"f{y_idx}", ""))
            ax.set_xlabel(f"{x_lbl}" + (f" [{x_unit}]" if x_unit else ""))
            ax.set_ylabel(f"{y_lbl}" + (f" [{y_unit}]" if y_unit else ""))
            ax.set_title(f"{rec_name}  {y_lbl}–{x_lbl}")
            ax.axhline(0, color="#888", linewidth=0.5)
            ax.axvline(0, color="#888", linewidth=0.5)
            if all_x and all_y:
                _set_tight_ylim(ax, np.concatenate(all_y))
                dx = np.concatenate(all_x)
                dx_range = float(dx.max() - dx.min())
                m = dx_range * 0.1 if dx_range > 1e-15 else max(
                    abs(float(dx.max())) * 0.2, 1e-6
                )
                ax.set_xlim(float(dx.min()) - m, float(dx.max()) + m)
        else:
            _, field_idx = qty_data
            plotted, summaries, all_y = self._draw_time_entries(
                ax, cat_name, rec_idx, field_idx, dt
            )
            if plotted == 0:
                self._show_hysteresis_empty(canvas, info_label, summaries)
                return
            first_bc = self._first_category(cat_name)
            rec_name = first_bc.record_name(rec_idx) if first_bc else f"rec{rec_idx}"
            f_lbl, f_unit = label_dict.get(field_idx, (f"f{field_idx}", ""))
            ax.set_xlabel("時間 [s]")
            ax.set_ylabel(f"{f_lbl}" + (f" [{f_unit}]" if f_unit else ""))
            ax.set_title(f"{rec_name} / {f_lbl}")
            ax.grid(True, linestyle=":", alpha=0.5)
            if all_y:
                _set_tight_ylim(ax, np.concatenate(all_y))

        if plotted > 1:
            ax.legend(loc="best", fontsize=8)
        canvas.fig.tight_layout()
        canvas.draw()
        info_label.setText("  |  ".join(summaries))

    def _draw_loop_entries(
        self,
        ax,
        cat_name: str,
        rec_idx: int,
        x_idx: int,
        y_idx: int,
        dt: float,
    ) -> Tuple[int, List[str], List[np.ndarray], List[np.ndarray]]:
        """各ケースの履歴ループを描画する。"""
        plotted = 0
        summaries: List[str] = []
        all_x: List[np.ndarray] = []
        all_y: List[np.ndarray] = []
        for e in self._active_entries:
            bc = e.loader.get(cat_name)
            if not bc or not bc.hst or bc.hst.header is None:
                continue
            if rec_idx >= bc.hst.header.num_records:
                continue
            if max(x_idx, y_idx) >= bc.hst.header.fields_per_record:
                continue
            try:
                bc.hst.dt = dt
                x = bc.hst.time_series(rec_idx, x_idx)
                y = bc.hst.time_series(rec_idx, y_idx)
            except Exception as ex:
                summaries.append(f"{e.name}: 読み取りエラー — {ex}")
                continue
            ax.plot(x, y, linewidth=0.7, label=e.name)
            all_x.append(x)
            all_y.append(y)
            plotted += 1
            try:
                e_abs = float(np.trapz(y, x))
            except Exception:
                e_abs = 0.0
            summaries.append(
                f"{e.name}: |Y|max={float(np.max(np.abs(y))):.4g}, "
                f"|X|max={float(np.max(np.abs(x))):.4g}, "
                f"\u222eFdX\u2248{e_abs:.4g}"
            )
        return plotted, summaries, all_x, all_y

    def _draw_time_entries(
        self,
        ax,
        cat_name: str,
        rec_idx: int,
        field_idx: int,
        dt: float,
    ) -> Tuple[int, List[str], List[np.ndarray]]:
        """各ケースの時刻歴を描画する。"""
        plotted = 0
        summaries: List[str] = []
        all_y: List[np.ndarray] = []
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
            except Exception as ex:
                summaries.append(f"{e.name}: 読み取りエラー — {ex}")
                continue
            ax.plot(t, y, linewidth=0.9, label=e.name)
            plotted += 1
            if y.size:
                all_y.append(y)
                p = int(np.argmax(np.abs(y)))
                summaries.append(
                    f"{e.name}: max={float(y[p]):+.4g} @ t={float(t[p]):.2f}s"
                )
        return plotted, summaries, all_y

    @staticmethod
    def _show_hysteresis_empty(canvas, info_label: QLabel, summaries: List[str]) -> None:
        err_detail = "\n".join(summaries) if summaries else ""
        canvas.show_message(
            "このレコードのデータが読み取れませんでした\n" + err_detail
            if err_detail else "どのケースにもこのレコードがありません",
            color="red" if err_detail else "gray",
        )
        info_label.setText(err_detail)

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
