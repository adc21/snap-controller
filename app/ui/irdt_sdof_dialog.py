"""
app/ui/irdt_sdof_dialog.py

iRDT最適解 - 1質点系ダイアログ。

adc-tools の `IRDTSdofOptParamsView` を PySide6 に移植したもの。
Den Hartog の最適設計式で 1 質点系の iRDT (慣性質量ダンパー) の最適値を計算し、
振動数比 vs. 変位応答倍率のグラフで油圧ダンパーとの比較を可視化します。
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.services.irdt import (
    amp_1dof,
    compute_sdof_result,
    kd_irdt_complex,
)


# adc-tools と同じ油圧ダンパー仕様
_OIL_FMAX = 2000.0        # [kN] 油圧ダンパー最大減衰力
_OIL_VMAX = 0.15          # [m/s] 油圧ダンパー最大速度
_OIL_COEF = round(_OIL_FMAX / _OIL_VMAX)   # = 13333 [kNs/m/基]
_ND_OIL = 2               # 油圧ダンパーの基数倍率 (nd × 2 で比較)


# デフォルト値 (adc-tools と一致)
_DEFAULTS = {
    "t0": 1.0,
    "m": 100000.0,
    "md": 1000.0,
    "cd": 1900.0,
    "kb": 44000.0,
    "nd": 5,
}


class IRDTSdofDialog(QDialog):
    """
    iRDT 最適解 - 1 質点系 (SDOF) ダイアログ。

    Usage
    -----
    dlg = IRDTSdofDialog(parent=self)
    dlg.exec()
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("iRDT最適解 - 1質点系")
        self.resize(1000, 700)

        self._auto_check: Optional[QCheckBox] = None
        self._t0_edit: Optional[QLineEdit] = None
        self._m_edit: Optional[QLineEdit] = None
        self._md_edit: Optional[QLineEdit] = None
        self._cd_edit: Optional[QLineEdit] = None
        self._kb_edit: Optional[QLineEdit] = None
        self._nd_edit: Optional[QLineEdit] = None

        self._build_ui()
        self._connect_signals()
        self._recompute()

    # ---- UI 構築 ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # 左: 入力フォーム + 結果パネル
        left = QVBoxLayout()
        left.addWidget(self._build_form_group())
        left.addWidget(self._build_results_group())
        left.addStretch(1)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMinimumWidth(360)
        left_widget.setMaximumWidth(420)
        root.addWidget(left_widget)

        # 右: 振動特性グラフ
        root.addWidget(self._build_chart_group(), stretch=1)

    def _build_form_group(self) -> QGroupBox:
        box = QGroupBox("入力値")
        form = QFormLayout(box)

        self._auto_check = QCheckBox("自動で最適値を計算")
        self._auto_check.setChecked(True)
        form.addRow(self._auto_check)

        self._t0_edit = self._make_double_edit(_DEFAULTS["t0"], 0.0, 10.0, decimals=4)
        self._m_edit = self._make_double_edit(_DEFAULTS["m"], 0.0, 1_000_000.0, decimals=2)
        self._md_edit = self._make_double_edit(_DEFAULTS["md"], 0.0, 10_000.0, decimals=2)
        self._cd_edit = self._make_double_edit(_DEFAULTS["cd"], 0.0, 10_000.0, decimals=2)
        self._kb_edit = self._make_double_edit(_DEFAULTS["kb"], 0.0, 1_000_000.0, decimals=2)
        self._nd_edit = self._make_int_edit(_DEFAULTS["nd"], 0, 1000)

        form.addRow("卓越周期 [s]", self._t0_edit)
        form.addRow("建物総質量 [ton]", self._m_edit)
        form.addRow("ダンパー質量 [ton/基]", self._md_edit)
        self._cd_row_label = QLabel("ダンパー減衰定数 [kNs/m/基]")
        form.addRow(self._cd_row_label, self._cd_edit)
        self._kb_row_label = QLabel("支持部材剛性 [kN/m/基]")
        form.addRow(self._kb_row_label, self._kb_edit)
        form.addRow("ダンパー基数 [基]", self._nd_edit)

        return box

    def _build_results_group(self) -> QGroupBox:
        box = QGroupBox("最適値")
        form = QFormLayout(box)

        self._lbl_mu = QLabel("-")
        self._lbl_cd = QLabel("-")
        self._lbl_hd = QLabel("-")
        self._lbl_kb = QLabel("-")
        self._lbl_td = QLabel("-")
        self._lbl_fd = QLabel("-")

        form.addRow("質量比 μ [-]", self._lbl_mu)
        form.addRow("最適減衰係数 cd [kNs/m/基]", self._lbl_cd)
        form.addRow("最適減衰定数 hd [%]", self._lbl_hd)
        form.addRow("最適支持部材剛性 kb [kN/m/基]", self._lbl_kb)
        form.addRow("最適ダンパー周期 Td [s]", self._lbl_td)
        form.addRow("最適ダンパー振動数 fd [Hz]", self._lbl_fd)

        return box

    def _build_chart_group(self) -> QGroupBox:
        box = QGroupBox("振動特性")
        layout = QVBoxLayout(box)

        self._fig = Figure(figsize=(6, 4.5), tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._canvas)

        btn_row = QHBoxLayout()
        btn_reset = QPushButton("リセット")
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)
        btn_close = QDialogButtonBox(QDialogButtonBox.Close)
        btn_close.rejected.connect(self.reject)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        return box

    # ---- ヘルパ -------------------------------------------------------
    @staticmethod
    def _make_double_edit(value: float, minimum: float, maximum: float, decimals: int = 4) -> QLineEdit:
        edit = QLineEdit()
        validator = QDoubleValidator(minimum, maximum, decimals)
        validator.setNotation(QDoubleValidator.StandardNotation)
        edit.setValidator(validator)
        edit.setText(f"{value:g}")
        edit.setAlignment(Qt.AlignRight)
        return edit

    @staticmethod
    def _make_int_edit(value: int, minimum: int, maximum: int) -> QLineEdit:
        edit = QLineEdit()
        edit.setValidator(QIntValidator(minimum, maximum))
        edit.setText(str(value))
        edit.setAlignment(Qt.AlignRight)
        return edit

    @staticmethod
    def _parse_float(edit: QLineEdit, default: float) -> float:
        try:
            return float(edit.text())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_int(edit: QLineEdit, default: int) -> int:
        try:
            return int(edit.text())
        except (TypeError, ValueError):
            return default

    # ---- シグナル ------------------------------------------------------
    def _connect_signals(self) -> None:
        self._auto_check.toggled.connect(self._on_auto_toggled)
        for edit in (self._t0_edit, self._m_edit, self._md_edit, self._nd_edit,
                     self._cd_edit, self._kb_edit):
            edit.editingFinished.connect(self._recompute)
        self._on_auto_toggled(self._auto_check.isChecked())

    def _on_auto_toggled(self, checked: bool) -> None:
        # 自動モード: cd, kb は非表示 (adc-tools と同じ挙動)
        self._cd_edit.setVisible(not checked)
        self._kb_edit.setVisible(not checked)
        self._cd_row_label.setVisible(not checked)
        self._kb_row_label.setVisible(not checked)
        self._recompute()

    def _on_reset(self) -> None:
        self._auto_check.setChecked(True)
        self._t0_edit.setText(f"{_DEFAULTS['t0']:g}")
        self._m_edit.setText(f"{_DEFAULTS['m']:g}")
        self._md_edit.setText(f"{_DEFAULTS['md']:g}")
        self._cd_edit.setText(f"{_DEFAULTS['cd']:g}")
        self._kb_edit.setText(f"{_DEFAULTS['kb']:g}")
        self._nd_edit.setText(str(_DEFAULTS["nd"]))
        self._recompute()

    # ---- 計算 + 表示 ---------------------------------------------------
    def _recompute(self) -> None:
        t0 = self._parse_float(self._t0_edit, _DEFAULTS["t0"])
        m = self._parse_float(self._m_edit, _DEFAULTS["m"])
        md = self._parse_float(self._md_edit, _DEFAULTS["md"])
        nd = self._parse_int(self._nd_edit, _DEFAULTS["nd"])
        cd_manual = self._parse_float(self._cd_edit, _DEFAULTS["cd"])
        kb_manual = self._parse_float(self._kb_edit, _DEFAULTS["kb"])

        res = compute_sdof_result(t0, m, md, nd)

        self._lbl_mu.setText(self._fmt(res.mu, 4))
        self._lbl_cd.setText(self._fmt(res.cd_opt, 2))
        self._lbl_hd.setText(self._fmt(res.hd_opt, 2))
        self._lbl_kb.setText(self._fmt(res.kb_opt, 2))
        self._lbl_td.setText(self._fmt(res.td_opt, 4))
        self._lbl_fd.setText(self._fmt(res.fd_opt, 4))

        # グラフ用 cd, kb (自動モードなら最適値、手動モードなら入力値)
        use_auto = self._auto_check.isChecked()
        cd = res.cd_opt if use_auto else cd_manual
        kb = res.kb_opt if use_auto else kb_manual

        self._draw_chart(t0, m, md, nd, cd, kb)

    @staticmethod
    def _fmt(value: float, decimals: int) -> str:
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return "—"
        return f"{value:.{decimals}f}"

    def _draw_chart(
        self,
        t0: float,
        m: float,
        md: float,
        nd: int,
        cd: float,
        kb: float,
    ) -> None:
        self._ax.clear()

        # 主系剛性 k = (2π/t0)² × m
        if t0 <= 0 or m <= 0:
            self._canvas.draw_idle()
            return
        w0 = 2.0 * math.pi / t0
        k = w0 ** 2 * m
        c_primary = 0.0  # adc-tools の比較設定: 主系減衰は 0

        # iRDT: kd = kdIRDT(md×nd, cd×nd, kb×nd, ω)
        if md > 0 and cd > 0 and kb > 0 and nd > 0 and not math.isnan(cd) and not math.isnan(kb):
            kd_fn = lambda w: kd_irdt_complex(md * nd, cd * nd, kb * nd, w)
            lambdas, amp_irdt = amp_1dof(m, c_primary, k, kd_fn=kd_fn)
            self._ax.plot(lambdas, amp_irdt, label="iRDT", color="#d62728", linewidth=2)

        # 油圧ダンパー 1 (nd 基, cOil)
        if nd > 0:
            lambdas, amp_oil1 = amp_1dof(m, _OIL_COEF * nd, k)
            self._ax.plot(lambdas, amp_oil1, label=f"油圧ダンパー ({nd} 基)", color="#1f77b4",
                          linewidth=1.5, linestyle="--")

        # 油圧ダンパー 2 (nd × 2 基)
        if nd > 0:
            lambdas, amp_oil2 = amp_1dof(m, _OIL_COEF * nd * _ND_OIL, k)
            self._ax.plot(lambdas, amp_oil2, label=f"油圧ダンパー ({nd * _ND_OIL} 基)",
                          color="#2ca02c", linewidth=1.5, linestyle=":")

        self._ax.set_xlabel("振動数比 ω/ω₀")
        self._ax.set_ylabel("変位応答倍率")
        self._ax.set_xlim(0, 2.0)
        self._ax.grid(True, alpha=0.3)
        self._ax.legend(loc="upper right")
        self._canvas.draw_idle()
