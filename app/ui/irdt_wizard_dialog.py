"""
app/ui/irdt_wizard_dialog.py
============================

iRDT（慣性質量ダンパー）最適設計ウィザードダイアログ。

既存の ``app/services/irdt_designer`` モジュールを UI に接続し、
定点理論に基づく iRDT の最適パラメータ算出・各層配分計画を
ステップバイステップで行う 5 段階ウィザードを提供します。

ステップ構成
------------
Step 1 : 対象モード選択
    Period.xbn から読み込んだ固有モード一覧を表示し、
    設計対象とするモードを選択します。

Step 2 : 質量比 μ 設定
    スライダー＋スピンボックスで総質量比 μ（0.01～0.20）を指定します。
    変更のたびに等価 SDOF 基準設計値（m_d, c_d, k_b）をリアルタイム更新します。

Step 3 : 各層質量 & 分布戦略の設定
    各層の質量を入力するテーブルと、
    層間モード変位比例 (interstory) / 振幅比例 (amplitude) / 均等 (uniform)
    の分布戦略をラジオボタンで選択します。

Step 4 : 設計結果プレビュー
    各層の慣性質量・支持剛性・粘性減衰係数の一覧テーブルと
    bar chart を表示します。

Step 5 : SNAP ケースとして保存
    設計結果テキストをメモに持つ新規 AnalysisCase を返します。
    ``accepted_case`` プロパティで取得可能です。

使い方
------
::

    dlg = IrdtWizardDialog(base_case=my_case, parent=self)
    if dlg.exec():
        new_case = dlg.accepted_case
        if new_case:
            project.add_case(new_case)
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from app.ui.theme import setup_matplotlib_fonts
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

try:
    setup_matplotlib_fonts()
except Exception:
    pass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.analysis_case import AnalysisCase
from app.services.irdt_designer import (
    IrdtPlacementPlan,
    IrdtParameters,
    design_irdt_sdof,
    design_irdt_placement,
    fixed_point_optimal,
)

# controller.binary はオプション（Period.xbn が存在しない場合は手入力）
try:
    from controller.binary.period_xbn_reader import PeriodXbnReader, ModeInfo
    _HAS_PERIOD_READER = True
except ImportError:
    _HAS_PERIOD_READER = False


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_MU_MIN = 0.005
_MU_MAX = 0.200
_MU_STEP = 0.001
_MU_SLIDER_SCALE = 1000   # スライダーは整数なので μ * scale で管理

_DIST_OPTIONS = [
    ("interstory", "層間モード変位比例（推奨）",
     "ダンパーが層間変位に比例した速度で仕事をするため最も効率的。"),
    ("amplitude",  "モード振幅比例",
     "各層の絶対振幅に比例して配分。"),
    ("uniform",    "均等配分",
     "全層に同量を配分。シンプルだが効率は低い。"),
]

_SHAPE_OPTIONS = [
    ("linear",    "線形（φ_k = k/N）",
     "最も単純な近似。低層建物に向く。"),
    ("sinusoidal","正弦波（φ_k = sin((2k-1)π/(2N+1))）",
     "片持ち梁理論の近似。一般的な多層建物に向く。"),
    ("uniform",   "均一（φ_k = 1.0）",
     "全層同じ振幅（剛体的変形）。免震層付き建物など。"),
]


# ---------------------------------------------------------------------------
# matplotlib ミニキャンバス
# ---------------------------------------------------------------------------

class _MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=5.0, height=3.5, dpi=96):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def clear(self):
        self.ax.clear()
        self.draw()

    def show_message(self, msg: str, color: str = "gray"):
        self.ax.clear()
        self.ax.text(0.5, 0.5, msg, ha="center", va="center",
                     transform=self.ax.transAxes, color=color,
                     fontsize=10, wrap=True)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw()


# ---------------------------------------------------------------------------
# ステップ共通ベースウィジェット
# ---------------------------------------------------------------------------

def _section(title: str) -> QGroupBox:
    """セクショングループボックスを作る小ヘルパー。"""
    g = QGroupBox(title)
    g.setStyleSheet(
        "QGroupBox { font-weight: bold; margin-top: 8px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 10px; }"
    )
    return g


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #999; font-size: 11px; padding: 2px 4px;")
    return lbl


# ---------------------------------------------------------------------------
# Step 1: モード選択
# ---------------------------------------------------------------------------

class _Step1ModeSelect(QWidget):
    """固有モード選択ページ。"""

    mode_selected = Signal(int)   # 選択モード番号（1 始まり）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._modes: List["ModeInfo"] = []
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(_hint(
            "Period.xbn から読み込んだ固有モード一覧を表示します。\n"
            "iRDT を設計する対象モードを選択してください（通常は1次モード）。"
        ))

        # --- 手動入力 vs 自動読み込み ---
        grp_src = _section("固有周期データ")
        src_lay = QVBoxLayout(grp_src)

        self._manual_period_cb = QRadioButton("固有周期を手動入力する")
        self._auto_period_cb   = QRadioButton("Period.xbn から自動読み込み（解析済みの場合）")
        self._auto_period_cb.setChecked(True)
        src_lay.addWidget(self._auto_period_cb)
        src_lay.addWidget(self._manual_period_cb)

        # 手動入力エリア
        manual_w = QWidget()
        mlay = QFormLayout(manual_w)
        mlay.setContentsMargins(16, 4, 4, 4)
        self._manual_period_spin = QDoubleSpinBox()
        self._manual_period_spin.setRange(0.01, 30.0)
        self._manual_period_spin.setValue(1.0)
        self._manual_period_spin.setSuffix(" s")
        self._manual_period_spin.setDecimals(4)
        mlay.addRow("固有周期 T₁ [s]:", self._manual_period_spin)
        self._manual_beta_spin = QDoubleSpinBox()
        self._manual_beta_spin.setRange(-99.0, 99.0)
        self._manual_beta_spin.setValue(1.2)
        self._manual_beta_spin.setDecimals(4)
        mlay.addRow("刺激係数 β_X:", self._manual_beta_spin)
        manual_w.setVisible(False)
        src_lay.addWidget(manual_w)

        self._manual_period_cb.toggled.connect(manual_w.setVisible)

        lay.addWidget(grp_src)

        # --- モード一覧テーブル ---
        grp_tbl = _section("固有モード一覧")
        tbl_lay = QVBoxLayout(grp_tbl)

        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumHeight(160)
        self._table.itemSelectionChanged.connect(self._on_selection)
        tbl_lay.addWidget(self._table)

        lay.addWidget(grp_tbl)

        self._status_lbl = QLabel("（モードデータ未ロード）")
        self._status_lbl.setStyleSheet("color:#888; font-size:11px;")
        lay.addWidget(self._status_lbl)

        lay.addStretch(1)

    # ---- パブリック ----

    def load_from_path(self, period_xbn_path: str) -> bool:
        """Period.xbn からモードデータを読み込む。"""
        if not _HAS_PERIOD_READER:
            return False
        try:
            reader = PeriodXbnReader(period_xbn_path)
            if not reader.modes:
                return False
            self._modes = sorted(reader.modes, key=lambda m: m.mode_no)
            self._populate_table()
            self._status_lbl.setText(
                f"Period.xbn から {len(self._modes)} モードをロード済み"
            )
            return True
        except Exception as e:
            self._status_lbl.setText(f"読み込みエラー: {e}")
            return False

    def set_modes(self, modes: list) -> None:
        self._modes = modes
        self._populate_table()

    def selected_mode_info(self) -> Optional[Tuple[int, float]]:
        """(mode_no, period) を返す。未選択時は None。"""
        if self._manual_period_cb.isChecked():
            return (1, self._manual_period_spin.value())
        rows = self._table.selectedItems()
        if not rows:
            if self._modes:
                return (self._modes[0].mode_no, self._modes[0].period)
            return None
        row = self._table.currentRow()
        if 0 <= row < len(self._modes):
            m = self._modes[row]
            return (m.mode_no, m.period)
        return None

    # ---- プライベート ----

    def _populate_table(self):
        headers = ["モード", "周期 T [s]", "振動数 f [Hz]",
                   "支配方向", "β_X", "β_Y", "PM_X [%]", "PM_Y [%]"]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(self._modes))
        for i, m in enumerate(self._modes):
            vals = [
                str(m.mode_no),
                f"{m.period:.4f}",
                f"{m.frequency:.4f}",
                m.dominant_direction,
                f"{m.beta.get('X', 0):.4f}",
                f"{m.beta.get('Y', 0):.4f}",
                f"{m.pm.get('X', 0):.2f}",
                f"{m.pm.get('Y', 0):.2f}",
            ]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(i, j, it)
        if self._modes:
            self._table.selectRow(0)

    def _on_selection(self):
        info = self.selected_mode_info()
        if info:
            self.mode_selected.emit(info[0])


# ---------------------------------------------------------------------------
# Step 2: 質量比 μ 設定
# ---------------------------------------------------------------------------

class _Step2MassRatio(QWidget):
    """質量比 μ 設定ページ。"""

    mu_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._period = 1.0
        self._total_mass = 1.0
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        lay.addWidget(_hint(
            "全層合計の質量比 μ = Σm_d / Σm_k を設定します。\n"
            "典型的な設計値は 0.02～0.10 です。"
        ))

        # μ スライダー
        grp_mu = _section("総質量比 μ")
        mu_lay = QVBoxLayout(grp_mu)

        slider_row = QHBoxLayout()
        self._mu_slider = QSlider(Qt.Horizontal)
        self._mu_slider.setRange(
            int(_MU_MIN * _MU_SLIDER_SCALE),
            int(_MU_MAX * _MU_SLIDER_SCALE),
        )
        self._mu_slider.setValue(int(0.05 * _MU_SLIDER_SCALE))
        slider_row.addWidget(QLabel(f"{_MU_MIN:.3f}"))
        slider_row.addWidget(self._mu_slider, stretch=1)
        slider_row.addWidget(QLabel(f"{_MU_MAX:.3f}"))
        mu_lay.addLayout(slider_row)

        spin_row = QHBoxLayout()
        spin_row.addStretch()
        spin_row.addWidget(QLabel("μ ="))
        self._mu_spin = QDoubleSpinBox()
        self._mu_spin.setRange(_MU_MIN, _MU_MAX)
        self._mu_spin.setValue(0.05)
        self._mu_spin.setSingleStep(_MU_STEP)
        self._mu_spin.setDecimals(4)
        self._mu_spin.setFixedWidth(90)
        spin_row.addWidget(self._mu_spin)
        mu_lay.addLayout(spin_row)

        lay.addWidget(grp_mu)

        # プレビュー: 等価 SDOF 設計値
        grp_prev = _section("等価 SDOF 基準設計値（参考）")
        prev_lay = QFormLayout(grp_prev)
        prev_lay.setLabelAlignment(Qt.AlignRight)

        self._lbl_period   = QLabel("—")
        self._lbl_f_opt    = QLabel("—")
        self._lbl_zeta_opt = QLabel("—")
        self._lbl_md       = QLabel("—")
        self._lbl_kb       = QLabel("—")
        self._lbl_cd       = QLabel("—")

        for label, widget in [
            ("対象モード周期 T [s]", self._lbl_period),
            ("最適周波数比 f_opt",   self._lbl_f_opt),
            ("最適減衰比 ζ_opt",     self._lbl_zeta_opt),
            ("慣性質量 m_d [kg]",    self._lbl_md),
            ("支持剛性 k_b [N/m]",   self._lbl_kb),
            ("粘性減衰 c_d [N·s/m]", self._lbl_cd),
        ]:
            prev_lay.addRow(label + ":", widget)

        lay.addWidget(grp_prev)
        lay.addStretch(1)

        # シグナル接続
        self._mu_slider.valueChanged.connect(self._slider_to_spin)
        self._mu_spin.valueChanged.connect(self._spin_to_slider)
        self._mu_spin.valueChanged.connect(self._refresh_preview)
        self._mu_spin.valueChanged.connect(lambda v: self.mu_changed.emit(v))

        self._refresh_preview(0.05)

    # ---- パブリック ----

    def set_context(self, period: float, total_mass: float):
        self._period = period
        self._total_mass = total_mass
        self._lbl_period.setText(f"{period:.4f}")
        self._refresh_preview(self._mu_spin.value())

    def mu(self) -> float:
        return self._mu_spin.value()

    # ---- プライベート ----

    def _slider_to_spin(self, val: int):
        self._mu_spin.blockSignals(True)
        self._mu_spin.setValue(val / _MU_SLIDER_SCALE)
        self._mu_spin.blockSignals(False)
        self._refresh_preview(val / _MU_SLIDER_SCALE)
        self.mu_changed.emit(val / _MU_SLIDER_SCALE)

    def _spin_to_slider(self, val: float):
        self._mu_slider.blockSignals(True)
        self._mu_slider.setValue(int(val * _MU_SLIDER_SCALE))
        self._mu_slider.blockSignals(False)

    def _refresh_preview(self, mu: float):
        try:
            f_opt, zeta_opt = fixed_point_optimal(mu)
            m_d = mu * self._total_mass
            omega_s = 2 * math.pi / max(self._period, 1e-9)
            omega_d = f_opt * omega_s
            k_b = m_d * omega_d ** 2
            c_d = 2 * zeta_opt * m_d * omega_d

            self._lbl_f_opt.setText(f"{f_opt:.4f}")
            self._lbl_zeta_opt.setText(f"{zeta_opt:.4f}")
            self._lbl_md.setText(f"{m_d:.3e}")
            self._lbl_kb.setText(f"{k_b:.3e}")
            self._lbl_cd.setText(f"{c_d:.3e}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Step 3: 各層質量 & 分布戦略
# ---------------------------------------------------------------------------

class _Step3FloorMass(QWidget):
    """各層質量と分布戦略の設定ページ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._n_floors = 5
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        lay.addWidget(_hint(
            "各層の質量を入力してください。\n"
            "質量がわからない場合は均等として 1000 [ton] などの仮値を使用できます。\n"
            "（ダンパーパラメータの比率は質量比のみに依存するため\n"
            "　相対値だけが正しければ比率は変わりません）"
        ))

        # 層数設定
        n_row = QHBoxLayout()
        n_row.addWidget(QLabel("層数:"))
        self._n_spin = QSpinBox()
        self._n_spin.setRange(1, 60)
        self._n_spin.setValue(5)
        self._n_spin.setFixedWidth(70)
        self._n_spin.valueChanged.connect(self._rebuild_table)
        n_row.addWidget(self._n_spin)
        n_row.addSpacing(20)
        n_row.addWidget(QLabel("デフォルト質量 [ton]:"))
        self._default_mass_spin = QDoubleSpinBox()
        self._default_mass_spin.setRange(1, 1e9)
        self._default_mass_spin.setValue(1000.0)
        self._default_mass_spin.setDecimals(1)
        self._default_mass_spin.setFixedWidth(110)
        n_row.addWidget(self._default_mass_spin)
        btn_fill = QPushButton("一括入力")
        btn_fill.setFixedWidth(70)
        btn_fill.clicked.connect(self._fill_default)
        n_row.addWidget(btn_fill)
        n_row.addStretch()
        lay.addLayout(n_row)

        # 各層質量テーブル
        grp_mass = _section("各層質量入力")
        mass_lay = QVBoxLayout(grp_mass)
        self._mass_table = QTableWidget()
        self._mass_table.setColumnCount(2)
        self._mass_table.setHorizontalHeaderLabels(["階", "質量 [ton]"])
        self._mass_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._mass_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._mass_table.setMaximumHeight(200)
        mass_lay.addWidget(self._mass_table)
        lay.addWidget(grp_mass)

        # モード形状の近似方法
        grp_shape = _section("モード形状近似")
        shape_lay = QVBoxLayout(grp_shape)
        self._shape_bg = QButtonGroup(self)
        for i, (key, label, hint) in enumerate(_SHAPE_OPTIONS):
            rb = QRadioButton(label)
            if i == 1:  # sinusoidal がデフォルト
                rb.setChecked(True)
            rb.setProperty("shape_key", key)
            self._shape_bg.addButton(rb)
            shape_lay.addWidget(rb)
            shape_lay.addWidget(_hint(f"  {hint}"))
        lay.addWidget(grp_shape)

        # 分布戦略
        grp_dist = _section("配分戦略")
        dist_lay = QVBoxLayout(grp_dist)
        self._dist_bg = QButtonGroup(self)
        for i, (key, label, hint) in enumerate(_DIST_OPTIONS):
            rb = QRadioButton(label)
            if i == 0:  # interstory がデフォルト
                rb.setChecked(True)
            rb.setProperty("dist_key", key)
            self._dist_bg.addButton(rb)
            dist_lay.addWidget(rb)
            dist_lay.addWidget(_hint(f"  {hint}"))
        lay.addWidget(grp_dist)

        lay.addStretch(1)

        self._rebuild_table(5)

    # ---- パブリック ----

    def masses_kg(self) -> List[float]:
        """各層の質量 [kg] を返す（テーブル入力値 × 1000）。"""
        result = []
        for i in range(self._mass_table.rowCount()):
            item = self._mass_table.item(i, 1)
            if item:
                try:
                    result.append(float(item.text()) * 1000.0)
                except ValueError:
                    result.append(1.0e6)
            else:
                result.append(1.0e6)
        return result

    def distribution(self) -> str:
        btn = self._dist_bg.checkedButton()
        return btn.property("dist_key") if btn else "interstory"

    def shape_mode(self) -> str:
        btn = self._shape_bg.checkedButton()
        return btn.property("shape_key") if btn else "sinusoidal"

    def build_mode_shape(self) -> List[float]:
        """近似モード形状ベクトルを返す（下階から順、最大値で正規化済み）。"""
        n = max(len(self.masses_kg()), 1)
        mode = self.shape_mode()
        if mode == "linear":
            phi = [(k + 1) / n for k in range(n)]
        elif mode == "sinusoidal":
            phi = [math.sin((2 * (k + 1) - 1) * math.pi / (2 * n + 1)) for k in range(n)]
        else:  # uniform
            phi = [1.0] * n
        amax = max(abs(v) for v in phi) if phi else 1.0
        return [v / amax for v in phi]

    def n_floors(self) -> int:
        return self._mass_table.rowCount()

    # ---- プライベート ----

    def _rebuild_table(self, n: int):
        prev = []
        for i in range(self._mass_table.rowCount()):
            item = self._mass_table.item(i, 1)
            prev.append(item.text() if item else "1000.0")

        self._mass_table.setRowCount(n)
        for i in range(n):
            # 階ラベル
            it_floor = QTableWidgetItem(f"{i + 1}F")
            it_floor.setFlags(Qt.ItemIsEnabled)
            it_floor.setTextAlignment(Qt.AlignCenter)
            self._mass_table.setItem(i, 0, it_floor)
            # 質量
            val = prev[i] if i < len(prev) else f"{self._default_mass_spin.value():.1f}"
            it_mass = QTableWidgetItem(val)
            it_mass.setTextAlignment(Qt.AlignCenter)
            self._mass_table.setItem(i, 1, it_mass)

    def _fill_default(self):
        default = self._default_mass_spin.value()
        n = self._n_spin.value()
        self._rebuild_table(n)
        for i in range(self._mass_table.rowCount()):
            self._mass_table.item(i, 1).setText(f"{default:.1f}")


# ---------------------------------------------------------------------------
# Step 4: 設計結果プレビュー
# ---------------------------------------------------------------------------

class _Step4Preview(QWidget):
    """設計結果プレビューページ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan: Optional[IrdtPlacementPlan] = None
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        splitter = QSplitter(Qt.Horizontal)

        # 左: 結果テーブル + サマリー
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(4, 4, 4, 4)

        left_lay.addWidget(QLabel("各層 iRDT パラメータ"))
        self._result_table = QTableWidget()
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.setMinimumHeight(160)
        left_lay.addWidget(self._result_table)

        self._summary_text = QTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setMaximumHeight(130)
        self._summary_text.setFont(QFont("Courier New", 9))
        left_lay.addWidget(self._summary_text)

        splitter.addWidget(left_w)

        # 右: bar chart
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(4, 4, 4, 4)
        right_lay.addWidget(QLabel("各層 慣性質量 m_d 分布"))
        self._canvas = _MplCanvas(height=4.0)
        right_lay.addWidget(NavigationToolbar(self._canvas, right_w))
        right_lay.addWidget(self._canvas, stretch=1)
        splitter.addWidget(right_w)
        splitter.setSizes([400, 350])

        lay.addWidget(splitter, stretch=1)

    # ---- パブリック ----

    def set_plan(self, plan: IrdtPlacementPlan) -> None:
        self._plan = plan
        self._update_table(plan)
        self._update_chart(plan)
        self._summary_text.setPlainText(plan.summary_text())

    def plan(self) -> Optional[IrdtPlacementPlan]:
        return self._plan

    # ---- プライベート ----

    def _update_table(self, plan: IrdtPlacementPlan):
        headers = [
            "階", "φ(k)", "Δφ(k)", "μ_eff",
            "m_d [ton]", "k_b [kN/m]", "c_d [kN·s/m]",
        ]
        self._result_table.setColumnCount(len(headers))
        self._result_table.setHorizontalHeaderLabels(headers)
        self._result_table.setRowCount(len(plan.floor_plan))
        for i, a in enumerate(plan.floor_plan):
            vals = [
                f"{a.floor}F",
                f"{a.mode_amplitude:+.4f}",
                f"{a.inter_story_mode:+.4f}",
                f"{a.mass_ratio_effective:.5f}",
                f"{a.inertance / 1000:.3f}",
                f"{a.support_stiffness / 1000:.3f}",
                f"{a.damping / 1000:.3f}",
            ]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignCenter)
                self._result_table.setItem(i, j, it)

    def _update_chart(self, plan: IrdtPlacementPlan):
        ax = self._canvas.ax
        ax.clear()

        floors = [a.floor for a in plan.floor_plan]
        md_ton = [a.inertance / 1000.0 for a in plan.floor_plan]

        ax.barh(floors, md_ton, color="#4C8FF5", alpha=0.85, height=0.7)
        ax.set_xlabel("慣性質量 m_d [ton]")
        ax.set_ylabel("階")
        ax.set_title(
            f"iRDT 各層慣性質量配分  "
            f"(モード {plan.target_mode}  T={plan.target_period:.3f}s)"
        )
        ax.invert_yaxis()
        ax.grid(True, axis="x", linestyle=":", alpha=0.5)
        self._canvas.fig.tight_layout()
        self._canvas.draw()


# ---------------------------------------------------------------------------
# Step 5: 保存設定
# ---------------------------------------------------------------------------

class _Step5Save(QWidget):
    """SNAPケースとして保存する設定ページ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        lay.addWidget(_hint(
            "設計結果を新規解析ケースのメモとして保存します。\n"
            "ケース名を確認し、「完了」をクリックしてください。"
        ))

        grp = _section("保存設定")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)

        self._name_edit = QLineEdit()
        self._name_edit.setText("iRDT設計案 (定点理論)")
        self._name_edit.setPlaceholderText("ケース名を入力…")
        form.addRow("ケース名:", self._name_edit)

        self._base_model_label = QLabel("（ベースケースなし）")
        self._base_model_label.setStyleSheet("color:#888; font-size:11px;")
        form.addRow("ベースモデル:", self._base_model_label)

        lay.addWidget(grp)

        grp2 = _section("設計結果プレビュー（メモに記録されます）")
        lay2 = QVBoxLayout(grp2)
        self._notes_text = QTextEdit()
        self._notes_text.setReadOnly(True)
        self._notes_text.setFont(QFont("Courier New", 9))
        self._notes_text.setMinimumHeight(200)
        lay2.addWidget(self._notes_text)
        lay.addWidget(grp2)

        lay.addStretch(1)

    # ---- パブリック ----

    def case_name(self) -> str:
        return self._name_edit.text().strip() or "iRDT設計案"

    def set_base_model_path(self, path: str):
        self._base_model_label.setText(path or "（ベースケースなし）")

    def set_notes(self, text: str):
        self._notes_text.setPlainText(text)


# ---------------------------------------------------------------------------
# メインウィザードダイアログ
# ---------------------------------------------------------------------------

class IrdtWizardDialog(QDialog):
    """
    iRDT 最適設計ウィザードダイアログ。

    Parameters
    ----------
    base_case : AnalysisCase, optional
        ベースケース（Period.xbn のパスや質量情報を取得するために使用）。
    parent : QWidget, optional

    Attributes
    ----------
    accepted_case : AnalysisCase or None
        「完了」で閉じたときに設定される新規ケース。
    placement_plan : IrdtPlacementPlan or None
        最終的な iRDT 配置計画。
    """

    STEP_LABELS = [
        "Step 1\nモード選択",
        "Step 2\n質量比 μ",
        "Step 3\n層質量・戦略",
        "Step 4\nプレビュー",
        "Step 5\n保存",
    ]

    def __init__(
        self,
        base_case: Optional[AnalysisCase] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("iRDT 最適設計ウィザード（定点理論）")
        self.setMinimumSize(860, 640)
        self.resize(920, 700)

        self._base_case = base_case
        self.accepted_case: Optional[AnalysisCase] = None
        self.placement_plan: Optional[IrdtPlacementPlan] = None

        self._current_step = 0
        self._setup_ui()
        self._try_load_period_xbn()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ---- タイトルバー ----
        title = QLabel("iRDT 最適設計ウィザード — 定点理論（Den Hartog 1956）")
        title.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #4CF; padding: 6px 10px;"
        )
        root.addWidget(title)

        # ---- ステップインジケーター ----
        step_bar = QHBoxLayout()
        step_bar.setSpacing(0)
        self._step_btns: List[QPushButton] = []
        for i, label in enumerate(self.STEP_LABELS):
            btn = QPushButton(label)
            btn.setFixedHeight(48)
            btn.setCheckable(True)
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { border: none; border-bottom: 3px solid transparent;"
                " padding: 2px 4px; font-size: 10px; }"
                "QPushButton:checked { border-bottom: 3px solid #4CF; color: #4CF; }"
                "QPushButton:disabled { color: #555; }"
            )
            btn.clicked.connect(lambda _, idx=i: self._jump_to_step(idx))
            step_bar.addWidget(btn, stretch=1)
            self._step_btns.append(btn)
        root.addLayout(step_bar)

        # ---- ページエリア ----
        self._stack = QStackedWidget()
        self._step1 = _Step1ModeSelect()
        self._step2 = _Step2MassRatio()
        self._step3 = _Step3FloorMass()
        self._step4 = _Step4Preview()
        self._step5 = _Step5Save()

        for w in [self._step1, self._step2, self._step3, self._step4, self._step5]:
            self._stack.addWidget(w)
        root.addWidget(self._stack, stretch=1)

        # ---- ナビゲーションボタン ----
        nav = QHBoxLayout()
        self._btn_back = QPushButton("◀ 戻る")
        self._btn_back.setFixedWidth(100)
        self._btn_back.clicked.connect(self._go_back)
        self._btn_next = QPushButton("次へ ▶")
        self._btn_next.setFixedWidth(100)
        self._btn_next.setDefault(True)
        self._btn_next.clicked.connect(self._go_next)
        btn_cancel = QPushButton("キャンセル")
        btn_cancel.setFixedWidth(100)
        btn_cancel.clicked.connect(self.reject)
        nav.addWidget(btn_cancel)
        nav.addStretch()
        nav.addWidget(self._btn_back)
        nav.addWidget(self._btn_next)
        root.addLayout(nav)

        self._update_step_indicator()

    # ------------------------------------------------------------------
    # ナビゲーション
    # ------------------------------------------------------------------

    def _go_next(self) -> None:
        if not self._validate_current_step():
            return
        if self._current_step == len(self.STEP_LABELS) - 1:
            self._finish()
            return
        # Step 3→4 のときに計算実行
        if self._current_step == 2:
            if not self._compute_and_show_preview():
                return
        # Step 4→5 のときに保存メモを更新
        if self._current_step == 3:
            self._prepare_save_step()
        self._current_step += 1
        self._stack.setCurrentIndex(self._current_step)
        self._update_step_indicator()

    def _go_back(self) -> None:
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicator()

    def _jump_to_step(self, idx: int) -> None:
        if idx < self._current_step:
            self._current_step = idx
            self._stack.setCurrentIndex(idx)
            self._update_step_indicator()

    def _update_step_indicator(self) -> None:
        for i, btn in enumerate(self._step_btns):
            btn.setChecked(i == self._current_step)
            btn.setEnabled(i <= self._current_step)

        is_last = self._current_step == len(self.STEP_LABELS) - 1
        self._btn_next.setText("完了 ✓" if is_last else "次へ ▶")
        self._btn_back.setEnabled(self._current_step > 0)

    # ------------------------------------------------------------------
    # バリデーション
    # ------------------------------------------------------------------

    def _validate_current_step(self) -> bool:
        step = self._current_step
        if step == 0:
            info = self._step1.selected_mode_info()
            if info is None:
                QMessageBox.warning(self, "選択エラー", "モードを選択してください。")
                return False
            # Step2 に周期・総質量を伝える
            _, period = info
            masses = self._step3.masses_kg()
            total_mass = sum(masses) if masses else 1.0
            self._step2.set_context(period, total_mass)
        elif step == 2:
            masses = self._step3.masses_kg()
            if not masses or sum(masses) <= 0:
                QMessageBox.warning(self, "入力エラー", "有効な質量を入力してください。")
                return False
        return True

    # ------------------------------------------------------------------
    # 計算
    # ------------------------------------------------------------------

    def _compute_and_show_preview(self) -> bool:
        """Step3 の設定から設計を実行して Step4 に反映する。"""
        info = self._step1.selected_mode_info()
        if info is None:
            QMessageBox.critical(self, "エラー", "モード情報がありません。")
            return False
        mode_no, period = info
        mu = self._step2.mu()
        masses_kg = self._step3.masses_kg()
        mode_shape = self._step3.build_mode_shape()
        distribution = self._step3.distribution()

        try:
            plan = design_irdt_placement(
                masses=masses_kg,
                mode_shape=mode_shape,
                target_period=period,
                total_mass_ratio=mu,
                target_mode=mode_no,
                distribution=distribution,
            )
        except Exception as e:
            QMessageBox.critical(self, "計算エラー", f"設計計算に失敗しました:\n{e}")
            return False

        self.placement_plan = plan
        self._step4.set_plan(plan)
        return True

    def _prepare_save_step(self) -> None:
        plan = self._step4.plan()
        if plan is None:
            return
        self._step5.set_notes(plan.summary_text())
        base_path = self._base_case.model_path if self._base_case else ""
        self._step5.set_base_model_path(base_path)
        mode_info = self._step1.selected_mode_info()
        mode_no = mode_info[0] if mode_info else 1
        mu = self._step2.mu()
        dist = self._step3.distribution()
        self._step5._name_edit.setText(
            f"iRDT_Mode{mode_no}_μ{mu:.3f}_{dist[:5]}"
        )

    # ------------------------------------------------------------------
    # 完了
    # ------------------------------------------------------------------

    def _finish(self) -> None:
        plan = self._step4.plan()
        if plan is None:
            QMessageBox.warning(self, "未計算", "Step4 の計算が完了していません。")
            return

        name = self._step5.case_name()
        notes = self._step5._notes_text.toPlainText()
        mode_info = self._step1.selected_mode_info()
        mu = self._step2.mu()

        # AnalysisCase を構築
        case = AnalysisCase(
            name=name,
            notes=notes,
            model_path=self._base_case.model_path if self._base_case else "",
            snap_exe_path=self._base_case.snap_exe_path if self._base_case else "",
            output_dir=self._base_case.output_dir if self._base_case else "",
        )
        # iRDT パラメータを damper_params に格納
        case.damper_params = {
            "type": "iRDT",
            "design_method": "fixed_point_theory",
            "target_mode": mode_info[0] if mode_info else 1,
            "target_period": mode_info[1] if mode_info else plan.target_period,
            "total_mass_ratio_mu": mu,
            "distribution": self._step3.distribution(),
            "modal_mass": plan.modal_mass,
            "floor_plan": [
                {
                    "floor": a.floor,
                    "inertance_kg": a.inertance,
                    "damping_Ns_per_m": a.damping,
                    "support_stiffness_N_per_m": a.support_stiffness,
                }
                for a in plan.floor_plan
            ],
        }
        if plan.base_parameters:
            case.damper_params["base_sdof"] = plan.base_parameters.to_dict()

        self.accepted_case = case
        self.accept()

    # ------------------------------------------------------------------
    # Period.xbn 自動読み込み
    # ------------------------------------------------------------------

    def _try_load_period_xbn(self) -> None:
        """ベースケースから Period.xbn を読み込む（エラーは無視）。"""
        if self._base_case is None:
            return
        import os
        result_dir = self._base_case.output_dir or os.path.dirname(
            self._base_case.model_path
        )
        if not result_dir:
            return
        candidates = [
            os.path.join(result_dir, "Period.xbn"),
        ]
        for path in candidates:
            if os.path.exists(path):
                ok = self._step1.load_from_path(path)
                if ok:
                    break


__all__ = ["IrdtWizardDialog"]
