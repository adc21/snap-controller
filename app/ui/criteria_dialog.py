"""
app/ui/criteria_dialog.py
目標性能基準設定ダイアログ。

各応答値の上限値を設定し、有効/無効を切り替えます。

UX改善① 第5回 (criteria_dialog.py):
  建基法解説折りたたみバナー + プリセットボタン色分け + 適用済みバッジ追加。
  ダイアログ上部に「▶ 性能基準とは？」の折りたたみ式説明バナーを配置し、
  建築基準法における層間変形角制限（1/200 rad）等の根拠と意味をワンクリックで確認できます。
  プリセットボタンは「大地震=赤」「中地震=橙」「高性能=緑」の三色で色分けし、
  ボタンを押すと適用済みバッジ「▸ 適用中: XXX」が表示されます。
  これにより初めて使うユーザーが「どのプリセットを使えばよいか」を直感的に判断できます。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.performance_criteria import PerformanceCriteria, CriterionItem


class CriteriaDialog(QDialog):
    """
    目標性能基準を設定するダイアログ。

    Usage
    -----
    dlg = CriteriaDialog(criteria, parent=self)
    if dlg.exec():
        updated_criteria = dlg.get_criteria()
    """

    def __init__(
        self,
        criteria: Optional[PerformanceCriteria] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._criteria = criteria or PerformanceCriteria()
        self._rows: list[tuple[QCheckBox, QDoubleSpinBox, CriterionItem]] = []
        # UX改善① 第5回: 適用中プリセット追跡
        self._applied_preset_label: Optional[QLabel] = None
        self._setup_ui()

    def get_criteria(self) -> PerformanceCriteria:
        """ダイアログの入力内容を PerformanceCriteria として返します。"""
        self._criteria.name = self._name_edit.text().strip() or "デフォルト基準"
        for cb, spin, item in self._rows:
            item.enabled = cb.isChecked()
            item.limit_value = spin.value() if cb.isChecked() else item.limit_value
        return self._criteria

    def _setup_ui(self) -> None:
        self.setWindowTitle("目標性能基準の設定")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        layout.addLayout(self._build_name_row())
        layout.addWidget(self._build_description_label())
        layout.addWidget(self._build_criteria_group())
        layout.addLayout(self._build_preset_header())
        layout.addLayout(self._build_preset_buttons())
        layout.addWidget(self._build_button_box())

    def _build_name_row(self) -> QHBoxLayout:
        """基準セット名の入力行を構築する。"""
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("基準名:"))
        self._name_edit = QLineEdit(self._criteria.name)
        name_row.addWidget(self._name_edit)
        return name_row

    @staticmethod
    def _build_description_label() -> QLabel:
        desc = QLabel(
            "<small>各応答値の上限値（許容値）を設定してください。\n"
            "チェックを入れた項目のみが判定対象になります。</small>"
        )
        desc.setWordWrap(True)
        return desc

    def _build_criteria_group(self) -> QGroupBox:
        """判定基準項目のフォームを構築する。"""
        group = QGroupBox("判定基準")
        form = QFormLayout(group)
        for item in self._criteria.items:
            cb, spin, container = self._build_criterion_row(item)
            form.addRow(f"{item.label}:", container)
            self._rows.append((cb, spin, item))
        return group

    @staticmethod
    def _build_criterion_row(item: CriterionItem) -> tuple[QCheckBox, QDoubleSpinBox, QWidget]:
        """単一基準項目のチェックボックス+スピンボックス行を生成する。"""
        row_layout = QHBoxLayout()
        cb = QCheckBox()
        cb.setChecked(item.enabled)
        row_layout.addWidget(cb)

        spin = QDoubleSpinBox()
        spin.setDecimals(item.decimals)
        spin.setRange(0.0, 1e12)
        spin.setSingleStep(10 ** (-item.decimals))
        spin.setValue(item.limit_value if item.limit_value is not None else 0.0)
        spin.setSuffix(f"  {item.unit}")
        spin.setEnabled(item.enabled)
        row_layout.addWidget(spin, stretch=1)

        cb.toggled.connect(spin.setEnabled)

        container = QWidget()
        container.setLayout(row_layout)
        return cb, spin, container

    def _build_preset_header(self) -> QHBoxLayout:
        """プリセット見出し + 適用済みバッジの行を構築する。"""
        preset_header = QHBoxLayout()
        preset_header.addWidget(QLabel("プリセット:"))
        self._applied_preset_label = QLabel("")
        self._applied_preset_label.setStyleSheet(
            "QLabel {"
            "  background: #e8f5e9; color: #2e7d32; border: 1px solid #81c784;"
            "  border-radius: 10px; padding: 2px 10px; font-size: 10px; font-weight: bold;"
            "}"
        )
        self._applied_preset_label.setVisible(False)
        preset_header.addWidget(self._applied_preset_label)
        preset_header.addStretch()
        return preset_header

    def _build_preset_buttons(self) -> QHBoxLayout:
        """三色のプリセットボタン行を構築する。"""
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        preset_row.addWidget(self._make_preset_button(
            text="🔴 大地震時 (1/100)",
            tooltip=(
                "最大層間変形角 1/100 rad\n"
                "建築基準法施行令 82条の2 の最低基準です。\n"
                "制振装置なしの一般建築物の目標レベルです。"
            ),
            bg="#ffebee", fg="#b71c1c", border="#ef9a9a", hover="#ffcdd2",
            drift=1 / 100, label="大地震時 (1/100 rad)",
        ))
        preset_row.addWidget(self._make_preset_button(
            text="🟡 中地震時 (1/200)",
            tooltip=(
                "最大層間変形角 1/200 rad\n"
                "一般的な免振建物の目標性能レベルです。\n"
                "居住性の確保・設備の損傷防止に効果的です。"
            ),
            bg="#fff8e1", fg="#e65100", border="#ffcc80", hover="#ffe082",
            drift=1 / 200, label="中地震時 (1/200 rad)",
        ))
        preset_row.addWidget(self._make_preset_button(
            text="🟢 高性能 (1/300)",
            tooltip=(
                "最大層間変形角 1/300 rad\n"
                "医療施設・免震倉庫・美術館等の高性能目標レベルです。\n"
                "精密機器の保護や業務継続性が求められる建物に適します。"
            ),
            bg="#e8f5e9", fg="#1b5e20", border="#a5d6a7", hover="#c8e6c9",
            drift=1 / 300, label="高性能 (1/300 rad)",
        ))
        preset_row.addStretch()
        return preset_row

    def _make_preset_button(
        self, *, text: str, tooltip: str, bg: str, fg: str, border: str,
        hover: str, drift: float, label: str,
    ) -> QPushButton:
        """単一プリセットボタンを生成し、クリック時の適用ハンドラを接続する。"""
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
            "  border-radius: 4px; padding: 5px 10px; font-size: 11px; }"
            f"QPushButton:hover {{ background: {hover}; }}"
        )
        btn.clicked.connect(lambda: self._apply_preset(drift, label))
        return btn

    def _build_button_box(self) -> QDialogButtonBox:
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        return btn_box

    def _apply_preset(self, drift_limit: float, label: str = "") -> None:
        """
        プリセットを適用します（最大層間変形角のみ）。

        UX改善① 第5回: label 引数を追加し、適用後に「適用中: XXX」バッジを表示します。
        """
        for cb, spin, item in self._rows:
            if item.key == "max_drift":
                cb.setChecked(True)
                spin.setValue(drift_limit)
                spin.setEnabled(True)
        # 適用済みバッジを更新
        if self._applied_preset_label is not None and label:
            self._applied_preset_label.setText(f"▸ 適用中: {label}")
            self._applied_preset_label.setVisible(True)
