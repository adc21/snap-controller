"""
app/ui/criteria_dialog.py
目標性能基準設定ダイアログ。

各応答値の上限値を設定し、有効/無効を切り替えます。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
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
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)

        # --- 基準セット名 ---
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("基準名:"))
        self._name_edit = QLineEdit(self._criteria.name)
        name_row.addWidget(self._name_edit)
        layout.addLayout(name_row)

        # --- 説明 ---
        desc = QLabel(
            "<small>各応答値の上限値（許容値）を設定してください。\n"
            "チェックを入れた項目のみが判定対象になります。</small>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # --- 基準項目 ---
        group = QGroupBox("判定基準")
        form = QFormLayout(group)

        for item in self._criteria.items:
            row_layout = QHBoxLayout()

            cb = QCheckBox()
            cb.setChecked(item.enabled)
            row_layout.addWidget(cb)

            spin = QDoubleSpinBox()
            spin.setDecimals(item.decimals)
            spin.setRange(0.0, 1e12)
            spin.setSingleStep(10 ** (-item.decimals))
            if item.limit_value is not None:
                spin.setValue(item.limit_value)
            else:
                spin.setValue(0.0)
            spin.setSuffix(f"  {item.unit}")
            spin.setEnabled(item.enabled)
            row_layout.addWidget(spin, stretch=1)

            # チェックボックスとスピンボックス連動
            cb.toggled.connect(spin.setEnabled)

            container = QWidget()
            container.setLayout(row_layout)
            form.addRow(f"{item.label}:", container)

            self._rows.append((cb, spin, item))

        layout.addWidget(group)

        # --- プリセットボタン ---
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("プリセット:"))

        from PySide6.QtWidgets import QPushButton

        btn_large = QPushButton("大地震時 (1/100)")
        btn_large.setToolTip("最大層間変形角 1/100 rad")
        btn_large.clicked.connect(lambda: self._apply_preset(1 / 100))
        preset_row.addWidget(btn_large)

        btn_medium = QPushButton("中地震時 (1/200)")
        btn_medium.setToolTip("最大層間変形角 1/200 rad")
        btn_medium.clicked.connect(lambda: self._apply_preset(1 / 200))
        preset_row.addWidget(btn_medium)

        btn_strict = QPushButton("高性能 (1/300)")
        btn_strict.setToolTip("最大層間変形角 1/300 rad")
        btn_strict.clicked.connect(lambda: self._apply_preset(1 / 300))
        preset_row.addWidget(btn_strict)

        preset_row.addStretch()
        layout.addLayout(preset_row)

        # --- ボタンボックス ---
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _apply_preset(self, drift_limit: float) -> None:
        """プリセットを適用します（最大層間変形角のみ）。"""
        for cb, spin, item in self._rows:
            if item.key == "max_drift":
                cb.setChecked(True)
                spin.setValue(drift_limit)
                spin.setEnabled(True)
