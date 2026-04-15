"""
app/ui/irdt_placement_proposal_dialog.py

iRDT 自動配置の提案ダイアログ。

`IRDTMdofDialog` で計算した最適値を元に、追加するダンパー定義と
RD 要素 (配置) の候補を表示し、ユーザーの確認後に s8i へ書き出します。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.damper_injector import DamperInjector, DamperInsertSpec

logger = logging.getLogger(__name__)


_COLS = [
    "配置",         # 0: チェックボックス (def_only の逆)
    "層",           # 1: floor_name
    "定義名",       # 2: def_name
    "節点 I",       # 3: node_i
    "節点 J",       # 4: node_j
    "基数",         # 5: quantity
    "md [kN·s²/m]",  # 6: mass
    "cd [kN·s/m]",   # 7: damping
    "kb [kN/m]",    # 8: spring
    "ストローク [m]", # 9: stroke
]


class IrdtPlacementProposalDialog(QDialog):
    """
    iRDT 自動配置の提案ダイアログ。

    Parameters
    ----------
    base_s8i_path : str
        元の .s8i ファイルパス。
    specs : list of DamperInsertSpec
        提案する配置仕様 (ユーザーが編集できる)。
    output_s8i_path : str, optional
        出力先 .s8i。省略時はダイアログで選択。
    project : Project, optional
        プロジェクトインスタンス。指定時は書き込み後に新ケースとして追加。
    base_case : AnalysisCase, optional
        元ケース。新ケース生成に利用される。
    parent : QWidget, optional
    """

    def __init__(
        self,
        base_s8i_path: str,
        specs: List[DamperInsertSpec],
        output_s8i_path: Optional[str] = None,
        project=None,  # type: ignore[no-untyped-def]
        base_case=None,  # type: ignore[no-untyped-def]
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("iRDT 解析ケースの追加")
        self.resize(1050, 580)

        self._base_s8i_path = base_s8i_path
        self._output_s8i_path = output_s8i_path or self._default_output_path(base_s8i_path)
        self._project = project
        self._base_case = base_case
        self._injected_specs: List[DamperInsertSpec] = []
        self._last_result = None  # type: ignore[var-annotated]

        self._build_ui()
        self._populate_specs(specs)

    # ---- UI 構築 ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        info = QLabel(
            "以下の内容で iRDT ダンパーを配置した新しい解析ケースをプロジェクトに追加します。\n"
            "表の値は編集可能です。\n"
            "「配置」チェックを外した行は、ダンパー定義 (DVMS) のみ追加され、"
            "RD 要素 (配置) は作成されません。"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        # 出力先 (内部的に生成される .s8i。通常は変更不要)
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("生成する .s8i:"))
        self._out_edit = QLineEdit(self._output_s8i_path)
        self._out_edit.setToolTip(
            "新ケース用に生成される .s8i ファイルのパスです。\n"
            "通常はデフォルトのままで問題ありません。"
        )
        out_row.addWidget(self._out_edit, stretch=1)
        btn_browse = QPushButton("参照...")
        btn_browse.clicked.connect(self._on_browse)
        out_row.addWidget(btn_browse)
        root.addLayout(out_row)

        # テーブル
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        root.addWidget(self._table, stretch=1)

        # 全行の配置 ON/OFF 切替
        batch_row = QHBoxLayout()
        self._batch_check = QCheckBox("すべての行を「定義のみ追加」モードにする")
        self._batch_check.toggled.connect(self._on_batch_def_only_toggled)
        batch_row.addWidget(self._batch_check)
        batch_row.addStretch(1)
        root.addLayout(batch_row)

        # 新規ケースとして追加 (プロジェクト連携が有効な場合のみ)
        can_add_case = self._project is not None and self._base_case is not None
        self._add_case_check = QCheckBox("新しい解析ケースとしてプロジェクトに追加")
        self._add_case_check.setChecked(can_add_case)
        self._add_case_check.setEnabled(can_add_case)
        if not can_add_case:
            self._add_case_check.setToolTip(
                "プロジェクト/ケース情報が無いためケース追加は利用できません。"
                " .s8i 生成のみ実行されます。"
            )
        root.addWidget(self._add_case_check)

        # ボタン
        btn_box = QDialogButtonBox()
        btn_ok = btn_box.addButton(
            "この内容で解析ケースを追加", QDialogButtonBox.AcceptRole
        )
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = btn_box.addButton(QDialogButtonBox.Cancel)
        btn_cancel.clicked.connect(self.reject)
        root.addWidget(btn_box)

    # ---- テーブル操作 --------------------------------------------------
    def _populate_specs(self, specs: List[DamperInsertSpec]) -> None:
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(specs))
            for row, spec in enumerate(specs):
                self._set_checkbox(row, 0, checked=not spec.def_only)
                self._set_cell(row, 1, spec.floor_name)
                self._set_cell(row, 2, spec.def_name)
                self._set_cell(row, 3, str(spec.node_i))
                self._set_cell(row, 4, str(spec.node_j))
                self._set_cell(row, 5, str(spec.quantity))
                self._set_cell(row, 6, f"{spec.mass_kN_s2_m:.2f}")
                self._set_cell(row, 7, f"{spec.damping_kN_s_m:.2f}")
                self._set_cell(row, 8, f"{spec.spring_kN_m:.2f}")
                self._set_cell(row, 9, f"{spec.stroke_m:.2f}")
        finally:
            self._table.blockSignals(False)

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = QTableWidgetItem(text)
        if col in (3, 4, 5, 6, 7, 8, 9):
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._table.setItem(row, col, item)

    def _set_checkbox(self, row: int, col: int, checked: bool) -> None:
        # QTableWidgetItem as checkbox
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._table.setItem(row, col, item)

    def _on_batch_def_only_toggled(self, checked: bool) -> None:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked if checked else Qt.Checked)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "出力 .s8i ファイル", self._out_edit.text(), "SNAP モデル (*.s8i)"
        )
        if path:
            self._out_edit.setText(path)

    # ---- 結果 ----------------------------------------------------------
    def _read_specs_from_table(self) -> List[DamperInsertSpec]:
        specs: List[DamperInsertSpec] = []
        for row in range(self._table.rowCount()):
            try:
                chk = self._table.item(row, 0)
                placed = (chk is not None and chk.checkState() == Qt.Checked)
                floor_name = self._cell_text(row, 1)
                def_name = self._cell_text(row, 2) or f"IRDT{row + 1}"
                node_i = int(float(self._cell_text(row, 3) or "0"))
                node_j = int(float(self._cell_text(row, 4) or "0"))
                quantity = int(float(self._cell_text(row, 5) or "1"))
                md = float(self._cell_text(row, 6) or "0")
                cd = float(self._cell_text(row, 7) or "0")
                kb = float(self._cell_text(row, 8) or "0")
                stroke = float(self._cell_text(row, 9) or "0.3")
            except (TypeError, ValueError):
                continue
            specs.append(DamperInsertSpec(
                damper_type="iRDT",
                def_name=def_name,
                floor_name=floor_name,
                node_i=node_i,
                node_j=node_j,
                quantity=quantity,
                mass_kN_s2_m=md,
                spring_kN_m=kb,
                damping_kN_s_m=cd,
                stroke_m=stroke,
                def_only=not placed,
            ))
        return specs

    def _cell_text(self, row: int, col: int) -> str:
        item = self._table.item(row, col)
        return item.text().strip() if item else ""

    def _on_accept(self) -> None:
        output_path = self._out_edit.text().strip()
        if not output_path:
            QMessageBox.warning(self, "エラー", "出力先の .s8i ファイルパスを入力してください。")
            return
        specs = self._read_specs_from_table()
        if not specs:
            QMessageBox.warning(self, "エラー", "挿入するダンパーがありません。")
            return

        injector = DamperInjector()
        try:
            result = injector.inject(
                base_s8i_path=self._base_s8i_path,
                specs=specs,
                output_s8i_path=output_path,
                base_case=self._base_case,
            )
        except Exception as exc:
            logger.exception("ダンパー挿入失敗")
            QMessageBox.critical(self, "挿入失敗", f"例外が発生しました:\n{exc}")
            return

        if not result.success:
            QMessageBox.warning(self, "挿入失敗", result.message)
            return

        # プロジェクトに新ケースとして追加
        added = False
        if self._add_case_check.isChecked() and result.new_case is not None and self._project is not None:
            try:
                self._project.add_case(result.new_case)
                added = True
            except Exception:
                logger.exception("プロジェクトへのケース追加失敗")

        self._injected_specs = specs
        self._last_result = result

        msg = result.message
        if result.warnings:
            msg += "\n\n警告:\n" + "\n".join(f"  - {w}" for w in result.warnings)
        if added:
            msg += f"\n\n新しい解析ケース '{result.new_case.name}' を追加しました。"
        title = "ケース追加完了" if added else "iRDT 挿入完了"
        QMessageBox.information(self, title, msg)
        self.accept()

    # ---- 公開 API -----------------------------------------------------
    def injected_specs(self) -> List[DamperInsertSpec]:
        return list(self._injected_specs)

    def result_info(self):  # type: ignore[no-untyped-def]
        return self._last_result

    # ---- ユーティリティ -----------------------------------------------
    @staticmethod
    def _default_output_path(base_path: str) -> str:
        p = Path(base_path)
        if not p.name:
            return base_path
        # `with_stem` は Python 3.9+ だが、環境によっては利用できない場合が
        # あるため `with_name` で等価処理を行う。
        new_name = f"{p.stem}_iRDT{p.suffix}"
        return str(p.with_name(new_name))
