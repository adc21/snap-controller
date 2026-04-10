"""
app/ui/damper_injector_dialog.py
iRDT/iOD ダンパー挿入ダイアログ。

既存の .s8i ファイルに iRDT（慣性質量減衰型制振装置）または
iOD（大質量型オイルダンパー）を指定層に自動挿入し、
新しい解析ケースとして保存するためのダイアログ。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QSplitter,
        QTextEdit,
        QVBoxLayout,
        QWidget,
        QComboBox,
        QFrame,
    )
    _PYSIDE6_OK = True
except ImportError:
    _PYSIDE6_OK = False

from app.models.analysis_case import AnalysisCase
from app.services.damper_injector import DamperInjector, DamperInsertSpec, InjectionResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SpecRow — 1 行のダンパー仕様入力ウィジェット
# ---------------------------------------------------------------------------

class _SpecRow(QWidget):
    """1 件のダンパー仕様を入力するウィジェット行。"""

    def __init__(self, index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._index = index
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 種別
        self._type_combo = QComboBox()
        self._type_combo.addItems(["iRDT", "iOD"])
        self._type_combo.setToolTip("iRDT: 同調型（バネあり） / iOD: フローティング型（バネなし）")
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        layout.addWidget(QLabel("種別:"))
        layout.addWidget(self._type_combo)

        # 定義名
        self._def_name = QLineEdit(f"IRDT{self._index + 1}")
        self._def_name.setMaximumWidth(80)
        self._def_name.setToolTip("SNAP 定義名（英数字, 最大8文字）")
        layout.addWidget(QLabel("定義名:"))
        layout.addWidget(self._def_name)

        # 層名（参照用）
        self._floor_name = QLineEdit(f"F{self._index + 1}")
        self._floor_name.setMaximumWidth(50)
        self._floor_name.setToolTip("挿入する層名（参照用）")
        layout.addWidget(QLabel("層:"))
        layout.addWidget(self._floor_name)

        # 節点 I, J
        self._node_i = QSpinBox()
        self._node_i.setRange(0, 99999)
        self._node_i.setValue(0)
        self._node_i.setMaximumWidth(70)
        self._node_i.setToolTip("始端節点番号")
        layout.addWidget(QLabel("節点I:"))
        layout.addWidget(self._node_i)

        self._node_j = QSpinBox()
        self._node_j.setRange(0, 99999)
        self._node_j.setValue(0)
        self._node_j.setMaximumWidth(70)
        self._node_j.setToolTip("終端節点番号")
        layout.addWidget(QLabel("J:"))
        layout.addWidget(self._node_j)

        # 本数
        self._quantity = QSpinBox()
        self._quantity.setRange(1, 100)
        self._quantity.setValue(1)
        self._quantity.setMaximumWidth(55)
        self._quantity.setToolTip("配置本数（基数）")
        layout.addWidget(QLabel("本数:"))
        layout.addWidget(self._quantity)

        # 慣性質量
        self._mass = QDoubleSpinBox()
        self._mass.setRange(0.1, 99999.0)
        self._mass.setValue(100.0)
        self._mass.setSuffix(" kN·s²/m")
        self._mass.setDecimals(1)
        self._mass.setMinimumWidth(130)
        self._mass.setToolTip("慣性質量 m_d (kN·s²/m)")
        layout.addWidget(QLabel("m_d:"))
        layout.addWidget(self._mass)

        # 支持バネ
        self._spring = QDoubleSpinBox()
        self._spring.setRange(0.0, 9999999.0)
        self._spring.setValue(5000.0)
        self._spring.setSuffix(" kN/m")
        self._spring.setDecimals(0)
        self._spring.setMinimumWidth(120)
        self._spring.setToolTip("支持バネ k_b (kN/m)。iOD は 0")
        layout.addWidget(QLabel("k_b:"))
        layout.addWidget(self._spring)

        # 減衰係数
        self._damping = QDoubleSpinBox()
        self._damping.setRange(0.1, 99999.0)
        self._damping.setValue(200.0)
        self._damping.setSuffix(" kN·s/m")
        self._damping.setDecimals(1)
        self._damping.setMinimumWidth(130)
        self._damping.setToolTip("減衰係数 c_d (kN·s/m)")
        layout.addWidget(QLabel("c_d:"))
        layout.addWidget(self._damping)

        # ストローク
        self._stroke = QDoubleSpinBox()
        self._stroke.setRange(0.01, 10.0)
        self._stroke.setValue(0.3)
        self._stroke.setSuffix(" m")
        self._stroke.setDecimals(2)
        self._stroke.setMaximumWidth(90)
        self._stroke.setToolTip("最大ストローク (m)")
        layout.addWidget(QLabel("stroke:"))
        layout.addWidget(self._stroke)

        layout.addStretch()

    def _on_type_changed(self, damper_type: str) -> None:
        """iOD 選択時は支持バネを 0 にする。"""
        if damper_type == "iOD":
            self._spring.setValue(0.0)
            self._def_name.setText(f"IOD{self._index + 1}")
        else:
            self._spring.setValue(5000.0)
            self._def_name.setText(f"IRDT{self._index + 1}")

    def to_spec(self) -> DamperInsertSpec:
        """入力値から DamperInsertSpec を生成。"""
        return DamperInsertSpec(
            damper_type=self._type_combo.currentText(),
            def_name=self._def_name.text().strip() or f"D{self._index + 1}",
            floor_name=self._floor_name.text().strip(),
            node_i=self._node_i.value(),
            node_j=self._node_j.value(),
            quantity=self._quantity.value(),
            mass_kN_s2_m=self._mass.value(),
            spring_kN_m=self._spring.value(),
            damping_kN_s_m=self._damping.value(),
            stroke_m=self._stroke.value(),
        )


# ---------------------------------------------------------------------------
# DamperInjectorDialog — メインダイアログ
# ---------------------------------------------------------------------------

class DamperInjectorDialog(QDialog):
    """
    iRDT/iOD ダンパー挿入ダイアログ。

    使用例
    ------
    >>> dlg = DamperInjectorDialog(base_case=case, parent=window)
    >>> if dlg.exec():
    ...     new_case = dlg.accepted_case
    """

    def __init__(
        self,
        base_case: Optional[AnalysisCase] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._base_case = base_case
        self._accepted_case: Optional[AnalysisCase] = None
        self._spec_rows: List[_SpecRow] = []

        self.setWindowTitle("iRDT/iOD ダンパー挿入")
        self.setMinimumSize(1100, 600)
        self._build_ui()
        self._populate_from_case()

    @property
    def accepted_case(self) -> Optional[AnalysisCase]:
        """ダイアログ受諾後に生成された AnalysisCase。"""
        return self._accepted_case

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ---- ファイル設定 ----
        file_group = QGroupBox("ファイル設定")
        file_layout = QFormLayout(file_group)

        # ベース .s8i
        h_base = QHBoxLayout()
        self._base_path_edit = QLineEdit()
        self._base_path_edit.setPlaceholderText("元の .s8i ファイルを選択…")
        if self._base_case and self._base_case.model_path:
            self._base_path_edit.setText(self._base_case.model_path)
        btn_browse_base = QPushButton("参照…")
        btn_browse_base.clicked.connect(self._browse_base)
        h_base.addWidget(self._base_path_edit)
        h_base.addWidget(btn_browse_base)
        file_layout.addRow("元モデル (.s8i):", h_base)

        # 出力 .s8i
        h_out = QHBoxLayout()
        self._out_path_edit = QLineEdit()
        self._out_path_edit.setPlaceholderText("出力先 .s8i ファイルを指定…")
        btn_browse_out = QPushButton("参照…")
        btn_browse_out.clicked.connect(self._browse_output)
        h_out.addWidget(self._out_path_edit)
        h_out.addWidget(btn_browse_out)
        file_layout.addRow("出力ファイル (.s8i):", h_out)

        # ケース名
        self._case_name_edit = QLineEdit()
        self._case_name_edit.setPlaceholderText("新規ケース名（省略時は自動生成）")
        file_layout.addRow("新規ケース名:", self._case_name_edit)

        root.addWidget(file_group)

        # ---- ダンパー仕様リスト ----
        spec_group = QGroupBox("挿入ダンパー仕様")
        spec_vlayout = QVBoxLayout(spec_group)

        # ボタン行
        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ 仕様を追加")
        btn_add.clicked.connect(self._add_spec_row)
        btn_remove = QPushButton("- 末尾を削除")
        btn_remove.clicked.connect(self._remove_last_spec_row)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        spec_vlayout.addLayout(btn_row)

        # スクロール可能なリスト領域
        self._spec_scroll = QScrollArea()
        self._spec_scroll.setWidgetResizable(True)
        self._spec_container = QWidget()
        self._spec_container_layout = QVBoxLayout(self._spec_container)
        self._spec_container_layout.setSpacing(4)
        self._spec_container_layout.addStretch()
        self._spec_scroll.setWidget(self._spec_container)
        self._spec_scroll.setMinimumHeight(200)
        spec_vlayout.addWidget(self._spec_scroll)

        root.addWidget(spec_group)

        # ---- ログ出力 ----
        log_group = QGroupBox("実行ログ")
        log_layout = QVBoxLayout(log_group)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(120)
        log_layout.addWidget(self._log_edit)
        root.addWidget(log_group)

        # ---- ボタン ----
        btn_box = QDialogButtonBox()
        self._btn_inject = btn_box.addButton("挿入実行", QDialogButtonBox.AcceptRole)
        self._btn_inject.clicked.connect(self._do_inject)
        btn_cancel = btn_box.addButton("キャンセル", QDialogButtonBox.RejectRole)
        btn_cancel.clicked.connect(self.reject)
        root.addWidget(btn_box)

        # 初期仕様行を1件追加
        self._add_spec_row()

    def _populate_from_case(self) -> None:
        """ベースケース情報で初期値を補完。"""
        if self._base_case is None:
            return
        base = Path(self._base_case.model_path)
        if base.exists():
            # 出力パスを自動提案
            out = base.with_stem(base.stem + "_irdt")
            self._out_path_edit.setText(str(out))
        name = self._base_case.name + "_iRDT" if self._base_case.name else ""
        self._case_name_edit.setPlaceholderText(name or "新規ケース名（省略時は自動生成）")

    # ------------------------------------------------------------------
    # ファイル参照
    # ------------------------------------------------------------------

    def _browse_base(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "元 .s8i を選択", "", "SNAP input (*.s8i);;All files (*)"
        )
        if path:
            self._base_path_edit.setText(path)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "出力 .s8i を保存", "", "SNAP input (*.s8i);;All files (*)"
        )
        if path:
            self._out_path_edit.setText(path)

    # ------------------------------------------------------------------
    # 仕様行の追加・削除
    # ------------------------------------------------------------------

    def _add_spec_row(self) -> None:
        row = _SpecRow(index=len(self._spec_rows), parent=self._spec_container)
        self._spec_rows.append(row)
        # stretch の手前に挿入
        insert_pos = self._spec_container_layout.count() - 1
        self._spec_container_layout.insertWidget(insert_pos, row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        self._spec_container_layout.insertWidget(insert_pos + 1, sep)

    def _remove_last_spec_row(self) -> None:
        if not self._spec_rows:
            return
        row = self._spec_rows.pop()
        # 行ウィジェットとその直後のセパレータを削除
        idx = self._spec_container_layout.indexOf(row)
        if idx >= 0:
            # セパレータ（次のウィジェット）を先に削除
            sep_item = self._spec_container_layout.itemAt(idx + 1)
            if sep_item and sep_item.widget():
                sep_item.widget().deleteLater()
            row.deleteLater()

    # ------------------------------------------------------------------
    # 挿入実行
    # ------------------------------------------------------------------

    def _do_inject(self) -> None:
        """挿入処理を実行。"""
        base_path = self._base_path_edit.text().strip()
        out_path = self._out_path_edit.text().strip()

        # 入力チェック
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
        if not self._spec_rows:
            QMessageBox.warning(self, "エラー", "挿入するダンパー仕様を1件以上追加してください。")
            return

        specs = [row.to_spec() for row in self._spec_rows]

        # 重複定義名チェック
        seen_names = set()
        for s in specs:
            if s.def_name in seen_names:
                QMessageBox.warning(
                    self, "エラー",
                    f"定義名 '{s.def_name}' が重複しています。別の名前にしてください。"
                )
                return
            seen_names.add(s.def_name)

        case_name = self._case_name_edit.text().strip() or None

        self._log("挿入開始…")
        injector = DamperInjector()
        result = injector.inject(
            base_s8i_path=base_path,
            specs=specs,
            output_s8i_path=out_path,
            base_case=self._base_case,
            new_case_name=case_name,
        )

        # 警告表示
        for w in result.warnings:
            self._log(f"⚠ {w}")

        if result.success:
            self._log(result.message)
            self._log(f"→ 出力: {result.output_s8i_path}")
            self._accepted_case = result.new_case
            self.accept()
        else:
            self._log(f"✕ {result.message}")
            QMessageBox.critical(self, "挿入失敗", result.message)

    def _log(self, msg: str) -> None:
        """ログエリアにメッセージを追加。"""
        self._log_edit.append(msg)
        logger.info(msg)
