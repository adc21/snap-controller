"""iRDT 設計ウィザードダイアログ（定点理論ベース）。"""

from __future__ import annotations

import math
from pathlib import Path
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
    QFileDialog,
    QMessageBox,
    QFormLayout,
    QSpinBox,
    QScrollArea,
)

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.services.irdt_designer import (
    IrdtPlacementPlan,
    fixed_point_optimal,
    tvmd_optimal_damped,
    design_irdt_placement,
    design_irdt_sdof_extended,
    compute_frf_sdof,
    compute_frf_sdof_tvmd,
    compute_irdt_performance,
    sensitivity_analysis,
)
from app.services.damper_injector import DamperInjector, DamperInsertSpec
from controller.binary.period_xbn_reader import PeriodXbnReader


class IrdtWizardDialog(QDialog):
    """iRDT 設計ウィザードダイアログ。"""

    designCompleted = Signal(object)

    _STEP_TITLES = [
        "ステップ 1/5 — 対象モード選択",
        "ステップ 2/5 — 質量比設定",
        "ステップ 3/5 — 配分戦略",
        "ステップ 4/5 — 設計結果プレビュー",
        "ステップ 5/5 — SNAPケースとして保存",
    ]

    def __init__(
        self,
        period_reader: Optional[PeriodXbnReader] = None,
        floor_masses: Optional[List[float]] = None,
        base_s8i_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._reader = period_reader
        self._floor_masses = floor_masses or []
        self._base_s8i_path = base_s8i_path or ""
        self._placement_plan: Optional[IrdtPlacementPlan] = None
        self._saved_case = None

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
        self._stack.addWidget(self._build_page_save())
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

        # 設計理論の選択
        theory_group = QGroupBox("設計理論")
        theory_layout = QVBoxLayout(theory_group)
        self._theory_group = QButtonGroup(page)

        self._rb_den_hartog = QRadioButton(
            "Den Hartog 定点理論（古典、無減衰主構造仮定）"
        )
        self._rb_den_hartog.setChecked(True)
        self._theory_group.addButton(self._rb_den_hartog, 0)
        theory_layout.addWidget(self._rb_den_hartog)

        self._rb_extended = QRadioButton(
            "拡張定点理論（減衰主構造補正: Asami-Nishihara / Ikago）"
        )
        self._theory_group.addButton(self._rb_extended, 1)
        theory_layout.addWidget(self._rb_extended)

        # 主構造減衰比入力（拡張理論用）
        zs_row = QHBoxLayout()
        zs_row.addWidget(QLabel("  主構造減衰比 ζ_s:"))
        self._design_zs_spin = QDoubleSpinBox()
        self._design_zs_spin.setRange(0.001, 0.200)
        self._design_zs_spin.setSingleStep(0.005)
        self._design_zs_spin.setDecimals(3)
        self._design_zs_spin.setValue(0.020)
        self._design_zs_spin.setToolTip(
            "RC造: 0.02〜0.05, S造: 0.01〜0.02"
        )
        self._design_zs_spin.valueChanged.connect(self._update_optimal_display)
        zs_row.addWidget(self._design_zs_spin)
        zs_row.addStretch()
        theory_layout.addLayout(zs_row)

        self._theory_group.buttonClicked.connect(
            lambda _: self._update_optimal_display()
        )
        layout.addWidget(theory_group)

        # 最適値表示
        opt_group = QGroupBox("最適同調パラメータ")
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

        # --- 応答低減率 η の表示 ---
        self._eta_group = QGroupBox("応答低減率 η（制振効果の指標）")
        eta_layout = QHBoxLayout(self._eta_group)

        self._eta_label = QLabel()
        self._eta_label.setStyleSheet(
            "font-family: monospace; font-size: 13px; padding: 4px;"
        )
        eta_layout.addWidget(self._eta_label, 1)

        # 主構造減衰比の入力
        eta_param = QVBoxLayout()
        eta_param.addWidget(QLabel("主構造減衰比 ζ_s:"))
        self._primary_damping_spin = QDoubleSpinBox()
        self._primary_damping_spin.setRange(0.001, 0.200)
        self._primary_damping_spin.setSingleStep(0.005)
        self._primary_damping_spin.setDecimals(3)
        self._primary_damping_spin.setValue(0.020)
        self._primary_damping_spin.setToolTip(
            "主構造の減衰比（RC造: 0.02〜0.05, S造: 0.01〜0.02）"
        )
        self._primary_damping_spin.valueChanged.connect(self._update_eta_display)
        eta_param.addWidget(self._primary_damping_spin)
        eta_layout.addLayout(eta_param)

        layout.addWidget(self._eta_group)

        # --- FRF チャート ---
        self._frf_figure = Figure(figsize=(6, 2.5))
        self._frf_canvas = FigureCanvas(self._frf_figure)
        self._frf_canvas.setMinimumHeight(180)
        self._frf_canvas.setMaximumHeight(220)
        layout.addWidget(self._frf_canvas)

        # --- 配置テーブル ---
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
        self._summary_text.setMaximumHeight(120)
        self._summary_text.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self._summary_text)

        # --- 感度解析 (Tornado Chart) ---
        self._sensitivity_group = QGroupBox(
            "感度解析 — 質量比 μ を ±20% 変動"
        )
        sens_layout = QVBoxLayout(self._sensitivity_group)
        self._tornado_figure = Figure(figsize=(6, 2.0))
        self._tornado_canvas = FigureCanvas(self._tornado_figure)
        self._tornado_canvas.setMinimumHeight(140)
        self._tornado_canvas.setMaximumHeight(180)
        sens_layout.addWidget(self._tornado_canvas)
        layout.addWidget(self._sensitivity_group)

        self._btn_apply = QPushButton("設計を確定して適用")
        self._btn_apply.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; padding: 6px 16px; }"
        )
        layout.addWidget(self._btn_apply, alignment=Qt.AlignmentFlag.AlignRight)

        return page

    def _build_page_save(self) -> QWidget:
        """ステップ 5: SNAPケースとして保存。"""
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(
            QLabel("設計結果を .s8i ファイルに挿入してSNAPケースとして保存します。")
        )

        # ファイル設定
        file_group = QGroupBox("ファイル設定")
        file_form = QFormLayout(file_group)

        h_base = QHBoxLayout()
        self._save_base_path = QLineEdit(self._base_s8i_path)
        self._save_base_path.setPlaceholderText("元の .s8i ファイルを選択…")
        btn_browse_base = QPushButton("参照…")
        btn_browse_base.clicked.connect(self._browse_base_s8i)
        h_base.addWidget(self._save_base_path)
        h_base.addWidget(btn_browse_base)
        file_form.addRow("元モデル (.s8i):", h_base)

        h_out = QHBoxLayout()
        self._save_out_path = QLineEdit()
        self._save_out_path.setPlaceholderText("出力先 .s8i を指定…")
        btn_browse_out = QPushButton("参照…")
        btn_browse_out.clicked.connect(self._browse_out_s8i)
        h_out.addWidget(self._save_out_path)
        h_out.addWidget(btn_browse_out)
        file_form.addRow("出力ファイル (.s8i):", h_out)

        self._save_case_name = QLineEdit()
        self._save_case_name.setPlaceholderText("新規ケース名（省略時は自動生成）")
        file_form.addRow("ケース名:", self._save_case_name)

        layout.addWidget(file_group)

        # 節点マッピング
        node_group = QGroupBox("各層の節点マッピング")
        node_vlayout = QVBoxLayout(node_group)
        node_vlayout.addWidget(
            QLabel("各層のダンパー取付節点（始端 I・終端 J）を指定してください:")
        )

        self._node_scroll = QScrollArea()
        self._node_scroll.setWidgetResizable(True)
        self._node_container = QWidget()
        self._node_container_layout = QVBoxLayout(self._node_container)
        self._node_container_layout.setSpacing(4)
        self._node_container_layout.addStretch()
        self._node_scroll.setWidget(self._node_container)
        self._node_scroll.setMinimumHeight(150)
        node_vlayout.addWidget(self._node_scroll)

        layout.addWidget(node_group)

        # 保存結果ログ
        self._save_log = QTextEdit()
        self._save_log.setReadOnly(True)
        self._save_log.setMaximumHeight(100)
        self._save_log.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self._save_log)

        # 保存ボタン
        self._btn_save_snap = QPushButton("SNAPケースとして保存")
        self._btn_save_snap.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; padding: 6px 16px; }"
        )
        self._btn_save_snap.clicked.connect(self._save_as_snap_case)
        layout.addWidget(self._btn_save_snap, alignment=Qt.AlignmentFlag.AlignRight)

        self._node_rows: List[_NodeRow] = []
        return page

    def _browse_base_s8i(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "元 .s8i を選択", "", "SNAP input (*.s8i);;All files (*)"
        )
        if path:
            self._save_base_path.setText(path)
            # 出力パスを自動提案
            out = Path(path).with_stem(Path(path).stem + "_irdt")
            self._save_out_path.setText(str(out))

    def _browse_out_s8i(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "出力 .s8i を保存", "", "SNAP input (*.s8i);;All files (*)"
        )
        if path:
            self._save_out_path.setText(path)

    def _populate_node_rows(self) -> None:
        """配置計画に基づいて節点入力行を生成する。"""
        # 既存行をクリア
        for row in self._node_rows:
            row.deleteLater()
        self._node_rows.clear()

        if self._placement_plan is None:
            return

        for assignment in self._placement_plan.floor_plan:
            if assignment.inertance <= 0:
                continue
            row = _NodeRow(
                floor=assignment.floor,
                parent=self._node_container,
            )
            self._node_rows.append(row)
            insert_pos = self._node_container_layout.count() - 1
            self._node_container_layout.insertWidget(insert_pos, row)

    def _save_as_snap_case(self) -> None:
        """設計結果を DamperInjector で .s8i に挿入して保存。"""
        base_path = self._save_base_path.text().strip()
        out_path = self._save_out_path.text().strip()

        if not base_path:
            QMessageBox.warning(self, "エラー", "元モデル (.s8i) を指定してください。")
            return
        if not Path(base_path).exists():
            QMessageBox.warning(
                self, "エラー", f"ファイルが見つかりません:\n{base_path}"
            )
            return
        if not out_path:
            QMessageBox.warning(self, "エラー", "出力ファイルパスを指定してください。")
            return
        if self._placement_plan is None:
            QMessageBox.warning(self, "エラー", "設計結果がありません。前のステップに戻ってください。")
            return

        # IrdtFloorAssignment → DamperInsertSpec に変換
        specs: List[DamperInsertSpec] = []
        for row in self._node_rows:
            assignment = None
            for a in self._placement_plan.floor_plan:
                if a.floor == row.floor:
                    assignment = a
                    break
            if assignment is None or assignment.inertance <= 0:
                continue

            # 単位変換: irdt_designer は SI (kg, N·s/m, N/m)
            # DamperInsertSpec は kN·s²/m, kN/m, kN·s/m
            mass_kN_s2_m = assignment.inertance / 1000.0
            spring_kN_m = assignment.support_stiffness / 1000.0
            damping_kN_s_m = assignment.damping / 1000.0

            specs.append(DamperInsertSpec(
                damper_type="iRDT",
                def_name=f"IRDT{assignment.floor}",
                floor_name=f"{assignment.floor}F",
                node_i=row.node_i,
                node_j=row.node_j,
                quantity=1,
                mass_kN_s2_m=mass_kN_s2_m,
                spring_kN_m=spring_kN_m,
                damping_kN_s_m=damping_kN_s_m,
                stroke_m=0.3,
            ))

        if not specs:
            QMessageBox.warning(self, "エラー", "挿入するダンパー仕様がありません。")
            return

        self._save_log.clear()
        self._save_log.append("挿入開始…")

        injector = DamperInjector()
        case_name = self._save_case_name.text().strip() or None
        result = injector.inject(
            base_s8i_path=base_path,
            specs=specs,
            output_s8i_path=out_path,
            new_case_name=case_name,
        )

        for w in result.warnings:
            self._save_log.append(f"⚠ {w}")

        if result.success:
            self._save_log.append(result.message)
            self._save_log.append(f"→ 出力: {result.output_s8i_path}")
            self._saved_case = result.new_case
            self._placement_plan.saved_s8i_path = out_path  # type: ignore[attr-defined]
            self.designCompleted.emit(self._placement_plan)
            QMessageBox.information(
                self, "保存完了",
                f"SNAPケースとして保存しました:\n{result.output_s8i_path}"
            )
        else:
            self._save_log.append(f"✕ {result.message}")
            QMessageBox.critical(self, "保存失敗", result.message)

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
            elif idx == 3:
                # 保存ページに進む前に節点行を生成
                self._populate_node_rows()
                # ベースパスから出力パスを自動提案
                base = self._save_base_path.text().strip()
                if base and not self._save_out_path.text().strip():
                    out = Path(base).with_stem(Path(base).stem + "_irdt")
                    self._save_out_path.setText(str(out))
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

    def _use_extended_theory(self) -> bool:
        return self._theory_group.checkedId() == 1

    def _design_damping_ratio(self) -> float:
        if self._use_extended_theory():
            return self._design_zs_spin.value()
        return 0.0

    def _update_optimal_display(self) -> None:
        mu = self._mu_spin.value()
        f_dh, z_dh = fixed_point_optimal(mu)

        if self._use_extended_theory():
            zs = self._design_zs_spin.value()
            f_ext, z_ext = tvmd_optimal_damped(mu, zs)
            self._opt_label.setText(
                f"質量比     μ     = {mu:.4f}\n"
                f"主構造減衰比 ζ_s  = {zs:.3f}\n"
                f"─── 拡張定点理論（減衰補正） ───\n"
                f"最適周波数比 f_opt = {f_ext:.6f}  (古典: {f_dh:.6f})\n"
                f"最適減衰比   ζ_opt = {z_ext:.6f}  (古典: {z_dh:.6f})"
            )
        else:
            self._opt_label.setText(
                f"質量比     μ     = {mu:.4f}\n"
                f"─── Den Hartog 定点理論 ───\n"
                f"最適周波数比 f_opt = {f_dh:.6f}\n"
                f"最適減衰比   ζ_opt = {z_dh:.6f}"
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

        # 拡張理論が選択されている場合、基準パラメータを補正値で置換
        if self._use_extended_theory() and self._placement_plan.base_parameters is not None:
            modal_mass = self._placement_plan.modal_mass
            zs = self._design_damping_ratio()
            mu_modal = self._placement_plan.base_parameters.mass_ratio
            extended_params = design_irdt_sdof_extended(
                primary_mass=modal_mass,
                primary_period=period,
                mass_ratio=mu_modal,
                damping_ratio_primary=zs,
                note="拡張定点理論（減衰補正）による基準値",
            )
            self._placement_plan.base_parameters = extended_params

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

        # 応答低減率とFRFチャート
        self._update_eta_display()

    def _update_eta_display(self) -> None:
        """応答低減率 η の表示を更新する。"""
        plan = self._placement_plan
        if plan is None or plan.base_parameters is None:
            return

        zs = self._primary_damping_spin.value()
        perf = compute_irdt_performance(plan.base_parameters, damping_ratio_primary=zs)

        eta = perf["eta"]
        reduction = perf["reduction_pct"]

        # 効果の評価テキスト
        if reduction >= 60:
            effect = "非常に大きい"
        elif reduction >= 40:
            effect = "大きい"
        elif reduction >= 20:
            effect = "中程度"
        else:
            effect = "小さい"

        self._eta_label.setText(
            f"応答低減率  η = {eta:.3f}  "
            f"（ピーク応答を {reduction:.1f}% 低減）\n"
            f"制振効果: {effect}\n"
            f"制振なしピーク倍率: {perf['peak_bare']:.1f}  →  "
            f"制振ありピーク倍率: {perf['peak_tvmd']:.2f}"
        )

        self._update_frf_chart(zs)
        self._update_tornado_chart(zs)

    def _update_tornado_chart(self, damping_ratio_primary: float = 0.02) -> None:
        """感度解析トルネードチャートを更新する。"""
        plan = self._placement_plan
        if plan is None or plan.base_parameters is None:
            return

        params = plan.base_parameters
        mu = params.mass_ratio

        try:
            result = sensitivity_analysis(
                primary_mass=params.target_mass,
                primary_period=params.target_period,
                base_mass_ratio=mu,
                damping_ratio_primary=damping_ratio_primary,
                variation_pct=20.0,
                n_steps=5,
            )
        except Exception:
            return

        fig = self._tornado_figure
        fig.clear()
        ax = fig.add_subplot(111)

        mu_vals = result["mu_values"]
        red_vals = result["reduction_pct_values"]
        base_idx = result["base_index"]

        if not mu_vals:
            return

        base_red = red_vals[base_idx] if base_idx < len(red_vals) else red_vals[len(red_vals) // 2]

        colors = []
        for i, r in enumerate(red_vals):
            if i == base_idx:
                colors.append("#1565c0")
            elif r >= base_red:
                colors.append("#2e7d32")
            else:
                colors.append("#c62828")

        bars = ax.barh(
            range(len(mu_vals)),
            red_vals,
            color=colors,
            height=0.7,
            edgecolor="none",
        )

        ax.set_yticks(range(len(mu_vals)))
        ax.set_yticklabels([f"{m:.4f}" for m in mu_vals], fontsize=7)
        ax.set_xlabel("応答低減率 [%]", fontsize=8)
        ax.set_ylabel("質量比 μ", fontsize=8)
        ax.set_title("感度解析: μ ±20% 変動時の応答低減率", fontsize=9)
        ax.axvline(base_red, color="#1565c0", linestyle="--", linewidth=0.8, alpha=0.6)
        ax.tick_params(labelsize=7)
        ax.grid(True, axis="x", alpha=0.3)

        fig.tight_layout()
        self._tornado_canvas.draw()

    def _update_frf_chart(self, damping_ratio_primary: float = 0.02) -> None:
        """FRF チャートを更新する。"""
        plan = self._placement_plan
        if plan is None or plan.base_parameters is None:
            return

        params = plan.base_parameters
        mu = params.mass_ratio
        f_opt = params.frequency_ratio
        zd = params.damping_ratio

        fig = self._frf_figure
        fig.clear()
        ax = fig.add_subplot(111)

        r_bare, H_bare = compute_frf_sdof(
            damping_ratio=damping_ratio_primary,
            r_min=0.01, r_max=2.5, n_points=500,
        )
        r_tvmd, H_tvmd = compute_frf_sdof_tvmd(
            mass_ratio=mu,
            freq_ratio=f_opt,
            damping_ratio_tvmd=zd,
            damping_ratio_primary=damping_ratio_primary,
            r_min=0.01, r_max=2.5, n_points=500,
        )

        ax.semilogy(r_bare, H_bare, "b--", linewidth=1.2, label="制振なし (SDOF)")
        ax.semilogy(r_tvmd, H_tvmd, "r-", linewidth=1.5, label=f"iRDT付 (μ={mu:.3f})")

        ax.set_xlabel("振動数比 r = ω/ω_s", fontsize=9)
        ax.set_ylabel("|H(r)|", fontsize=9)
        ax.set_title("変位伝達関数 (FRF)", fontsize=10)
        ax.legend(fontsize=8, loc="upper right")
        ax.set_xlim(0, 2.5)
        ax.grid(True, alpha=0.3, which="both")
        ax.tick_params(labelsize=8)

        fig.tight_layout()
        self._frf_canvas.draw()

    def _apply_design(self) -> None:
        if self._placement_plan is not None:
            self.designCompleted.emit(self._placement_plan)
            self.accept()

    @property
    def placement_plan(self) -> Optional[IrdtPlacementPlan]:
        return self._placement_plan

    @property
    def saved_case(self):
        """保存後に生成された AnalysisCase（未保存時は None）。"""
        return self._saved_case


class _NodeRow(QWidget):
    """各層のダンパー取付節点（I, J）を入力する行ウィジェット。"""

    def __init__(
        self, floor: int, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._floor = floor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        layout.addWidget(QLabel(f"{floor}F:"))

        layout.addWidget(QLabel("節点I:"))
        self._node_i = QSpinBox()
        self._node_i.setRange(0, 99999)
        self._node_i.setValue(0)
        self._node_i.setMaximumWidth(80)
        self._node_i.setToolTip(f"{floor}層 始端節点番号")
        layout.addWidget(self._node_i)

        layout.addWidget(QLabel("J:"))
        self._node_j = QSpinBox()
        self._node_j.setRange(0, 99999)
        self._node_j.setValue(0)
        self._node_j.setMaximumWidth(80)
        self._node_j.setToolTip(f"{floor}層 終端節点番号")
        layout.addWidget(self._node_j)

        layout.addStretch()

    @property
    def floor(self) -> int:
        return self._floor

    @property
    def node_i(self) -> int:
        return self._node_i.value()

    @property
    def node_j(self) -> int:
        return self._node_j.value()
