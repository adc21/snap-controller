"""iRDT 設計ウィザードダイアログ（定点理論ベース）。"""

from __future__ import annotations

import math
from typing import List, Optional

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSlider,
    QDoubleSpinBox,
    QRadioButton,
    QButtonGroup,
    QGroupBox,
    QTextEdit,
    QWidget,
    QAbstractItemView,
    QLineEdit,
)

from app.services.irdt_designer import (
    IrdtPlacementPlan,
    fixed_point_optimal,
    design_irdt_placement,
)
from controller.binary.period_xbn_reader import PeriodXbnReader


class IrdtWizardDialog(QDialog):
    """iRDT 設計ウィザードダイアログ。"""

    designCompleted = Signal(object)

    _STEP_TITLES = [
        "ステップ 1/4 — 対象モード選択",
        "ステップ 2/4 — 質量比設定",
        "ステップ 3/4 — 配分戦略",
        "ステップ 4/4 — 設計結果プレビュー",
    ]

    def __init__(
        self,
        period_reader: Optional[PeriodXbnReader] = None,
        floor_masses: Optional[List[float]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._reader = period_reader
        self._floor_masses = floor_masses or []
        self._placement_plan: Optional[IrdtPlacementPlan] = None

        self.setWindowTitle("iRDT 設計ウィザード（定点理論）")
        self.setMinimumWidth(720)
        self.setMinimumHeight(520)
        self._setup_ui()
        self._connect_signals()
        self._update_nav_buttons()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # ステップタイトル
        self._title_label = QLabel()
        self._title_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; padding: 4px 0;"
        )
        root.addWidget(self._title_label)

        # ページスタック
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_page_mode_select())
        self._stack.addWidget(self._build_page_mass_ratio())
        self._stack.addWidget(self._build_page_distribution())
        self._stack.addWidget(self._build_page_result())
        root.addWidget(self._stack, 1)

        # ナビゲーションボタン
        nav = QHBoxLayout()
        nav.addStretch()
        self._btn_back = QPushButton("戻る")
        self._btn_next = QPushButton("次へ")
        self._btn_close = QPushButton("閉じる")
        nav.addWidget(self._btn_back)
        nav.addWidget(self._btn_next)
        nav.addWidget(self._btn_close)
        root.addLayout(nav)

        self._stack.setCurrentIndex(0)
        self._title_label.setText(self._STEP_TITLES[0])

    def _build_page_mode_select(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        if self._reader and self._reader.modes:
            layout.addWidget(QLabel("固有値解析結果からモードを選択してください:"))
            self._mode_table = QTableWidget()
            self._mode_table.setColumnCount(5)
            self._mode_table.setHorizontalHeaderLabels(
                ["モード", "周期 [s]", "振動数 [Hz]", "主方向", "参加質量比 [%]"]
            )
            self._mode_table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows
            )
            self._mode_table.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
            )
            self._mode_table.setEditTriggers(
                QAbstractItemView.EditTrigger.NoEditTriggers
            )
            self._mode_table.setRowCount(len(self._reader.modes))
            for row, m in enumerate(self._reader.modes):
                pm_total = sum(abs(v) for v in m.pm.values())
                self._mode_table.setItem(
                    row, 0, QTableWidgetItem(str(m.mode_no))
                )
                self._mode_table.setItem(
                    row, 1, QTableWidgetItem(f"{m.period:.4f}")
                )
                self._mode_table.setItem(
                    row, 2, QTableWidgetItem(f"{m.frequency:.3f}")
                )
                self._mode_table.setItem(
                    row, 3, QTableWidgetItem(m.dominant_direction)
                )
                self._mode_table.setItem(
                    row, 4, QTableWidgetItem(f"{pm_total:.2f}")
                )
            self._mode_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            self._mode_table.selectRow(0)
            layout.addWidget(self._mode_table, 1)
            self._manual_input_widget = None
        else:
            layout.addWidget(
                QLabel("固有値解析データがありません。手動で入力してください。")
            )
            self._mode_table = None
            manual = QGroupBox("手動入力")
            ml = QHBoxLayout(manual)
            ml.addWidget(QLabel("固有周期 T [s]:"))
            self._manual_period = QLineEdit("1.0")
            ml.addWidget(self._manual_period)
            ml.addWidget(QLabel("等価質量 M [kg]:"))
            self._manual_mass = QLineEdit("1.0e6")
            ml.addWidget(self._manual_mass)
            layout.addWidget(manual)
            self._manual_input_widget = manual

        return page

    def _build_page_mass_ratio(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("質量比 μ = m_d / M_s を設定してください:"))

        row = QHBoxLayout()
        self._mu_slider = QSlider(Qt.Orientation.Horizontal)
        self._mu_slider.setRange(5, 150)  # 0.005 ~ 0.150 (x1000)
        self._mu_slider.setValue(20)      # 0.020
        self._mu_slider.setTickInterval(5)
        self._mu_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        row.addWidget(self._mu_slider, 1)

        self._mu_spin = QDoubleSpinBox()
        self._mu_spin.setRange(0.005, 0.150)
        self._mu_spin.setSingleStep(0.001)
        self._mu_spin.setDecimals(3)
        self._mu_spin.setValue(0.020)
        row.addWidget(self._mu_spin)
        layout.addLayout(row)

        # 最適値表示
        opt_group = QGroupBox("Den Hartog 定点理論 — 最適値")
        opt_layout = QVBoxLayout(opt_group)
        self._opt_label = QLabel()
        self._opt_label.setStyleSheet("font-family: monospace; font-size: 12px;")
        opt_layout.addWidget(self._opt_label)
        layout.addWidget(opt_group)

        layout.addStretch()
        self._update_optimal_display()
        return page

    def _build_page_distribution(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("各層への配分戦略を選択してください:"))

        self._dist_group = QButtonGroup(page)
        strategies = [
            ("interstory", "層間変位 (interstory)",
             "層間モード変位 Δφ(k) の二乗に比例して配分します。\n"
             "iRDT の仕事量は層間速度の二乗に比例するため、最も効率的な配分です。"),
            ("amplitude", "モード振幅 (amplitude)",
             "モード振幅 φ(k) の二乗に比例して配分します。"),
            ("uniform", "均等 (uniform)",
             "全層に均等に配分します。"),
        ]
        for i, (key, label, desc) in enumerate(strategies):
            rb = QRadioButton(label)
            rb.setProperty("dist_key", key)
            if i == 0:
                rb.setChecked(True)
            self._dist_group.addButton(rb, i)
            layout.addWidget(rb)
            hint = QLabel(desc)
            hint.setStyleSheet(
                "color: #666; font-size: 11px; margin-left: 24px; margin-bottom: 8px;"
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)

        layout.addStretch()
        return page

    def _build_page_result(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("設計結果:"))

        self._result_table = QTableWidget()
        self._result_table.setColumnCount(6)
        self._result_table.setHorizontalHeaderLabels(
            ["層", "φ(k)", "Δφ(k)", "m_d [kg]", "c_d [N·s/m]", "k_b [N/m]"]
        )
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self._result_table, 1)

        self._summary_text = QTextEdit()
        self._summary_text.setReadOnly(True)
        self._summary_text.setMaximumHeight(160)
        self._summary_text.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self._summary_text)

        self._btn_apply = QPushButton("設計を確定して適用")
        self._btn_apply.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; padding: 6px 16px; }"
        )
        layout.addWidget(self._btn_apply, alignment=Qt.AlignmentFlag.AlignRight)

        return page

    def _connect_signals(self) -> None:
        self._btn_back.clicked.connect(self._go_back)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_close.clicked.connect(self.reject)
        self._btn_apply.clicked.connect(self._apply_design)

        self._mu_slider.valueChanged.connect(self._on_slider_changed)
        self._mu_spin.valueChanged.connect(self._on_spin_changed)

    def _go_back(self) -> None:
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._title_label.setText(self._STEP_TITLES[idx - 1])
            self._update_nav_buttons()

    def _go_next(self) -> None:
        idx = self._stack.currentIndex()
        if idx < self._stack.count() - 1:
            if idx == 2:
                # 結果ページに進む前に計算を実行
                self._compute_placement()
            self._stack.setCurrentIndex(idx + 1)
            self._title_label.setText(self._STEP_TITLES[idx + 1])
            self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        idx = self._stack.currentIndex()
        self._btn_back.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < self._stack.count() - 1)

    # -- Mass ratio sync --

    def _on_slider_changed(self, value: int) -> None:
        mu = value / 1000.0
        self._mu_spin.blockSignals(True)
        self._mu_spin.setValue(mu)
        self._mu_spin.blockSignals(False)
        self._update_optimal_display()

    def _on_spin_changed(self, value: float) -> None:
        self._mu_slider.blockSignals(True)
        self._mu_slider.setValue(int(round(value * 1000)))
        self._mu_slider.blockSignals(False)
        self._update_optimal_display()

    def _update_optimal_display(self) -> None:
        mu = self._mu_spin.value()
        f_opt, zeta_opt = fixed_point_optimal(mu)
        self._opt_label.setText(
            f"質量比     μ     = {mu:.4f}\n"
            f"最適周波数比 f_opt = {f_opt:.6f}\n"
            f"最適減衰比   ζ_opt = {zeta_opt:.6f}"
        )

    # -- Selected mode / input helpers --

    def _selected_mode_no(self) -> int:
        if self._mode_table is not None:
            rows = self._mode_table.selectionModel().selectedRows()
            if rows:
                return int(self._mode_table.item(rows[0].row(), 0).text())
        return 1

    def _selected_period(self) -> float:
        if self._reader and self._reader.modes:
            mode_no = self._selected_mode_no()
            for m in self._reader.modes:
                if m.mode_no == mode_no:
                    return m.period
        # 手動入力
        try:
            return float(self._manual_period.text())
        except (ValueError, AttributeError):
            return 1.0

    def _effective_masses(self) -> List[float]:
        if self._floor_masses:
            return list(self._floor_masses)
        # 手動入力からフォールバック（単層 SDOF）
        try:
            total = float(self._manual_mass.text())
        except (ValueError, AttributeError):
            total = 1.0e6
        return [total]

    def _selected_distribution(self) -> str:
        btn = self._dist_group.checkedButton()
        if btn:
            return btn.property("dist_key")
        return "interstory"

    def _compute_placement(self) -> None:
        masses = self._effective_masses()
        period = self._selected_period()
        mu = self._mu_spin.value()
        distribution = self._selected_distribution()
        mode_no = self._selected_mode_no()

        n = len(masses)
        # モード形推定（sin 近似）
        mode_shape = [
            math.sin((2 * (k + 1) - 1) * math.pi / (2 * n + 1))
            for k in range(n)
        ]

        self._placement_plan = design_irdt_placement(
            masses=masses,
            mode_shape=mode_shape,
            target_period=period,
            total_mass_ratio=mu,
            target_mode=mode_no,
            distribution=distribution,
        )
        self._populate_result()

    def _populate_result(self) -> None:
        plan = self._placement_plan
        if plan is None:
            return

        # テーブル
        floors = plan.floor_plan
        self._result_table.setRowCount(len(floors))
        for row, a in enumerate(floors):
            self._result_table.setItem(row, 0, QTableWidgetItem(str(a.floor)))
            self._result_table.setItem(
                row, 1, QTableWidgetItem(f"{a.mode_amplitude:+.4f}")
            )
            self._result_table.setItem(
                row, 2, QTableWidgetItem(f"{a.inter_story_mode:+.4f}")
            )
            self._result_table.setItem(
                row, 3, QTableWidgetItem(f"{a.inertance:.3e}")
            )
            self._result_table.setItem(
                row, 4, QTableWidgetItem(f"{a.damping:.3e}")
            )
            self._result_table.setItem(
                row, 5, QTableWidgetItem(f"{a.support_stiffness:.3e}")
            )

        # サマリ
        self._summary_text.setPlainText(plan.summary_text())

    def _apply_design(self) -> None:
        if self._placement_plan is not None:
            self.designCompleted.emit(self._placement_plan)
            self.accept()

    @property
    def placement_plan(self) -> Optional[IrdtPlacementPlan]:
        return self._placement_plan
