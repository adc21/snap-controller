"""
app/ui/dyd_override_widget.py
DYD 応答解析条件 — 履歴結果の出力指定オーバーライドウィジェット。

デフォルトは「元のs8iの設定を使用」。
変更すると全解析ケースに一括で適用されます。
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
)

from app.models.s8i_parser import DydRecord


# (field_index, label) — DydRecord.HISTORY_OUTPUT_FIELDS / LABELS と対応
_HISTORY_FIELDS = list(zip(
    DydRecord.HISTORY_OUTPUT_FIELDS,
    DydRecord.HISTORY_OUTPUT_LABELS,
))


class DydOverrideWidget(QGroupBox):
    """DYD 履歴結果の出力指定を制御するウィジェット。

    Signals
    -------
    overrides_changed(overrides: dict | None)
        None = デフォルト（元のs8i設定）、dict = {field_idx: 0or1, ...}
    """

    overrides_changed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__("履歴結果の出力指定（DYD）", parent)
        self._checkboxes: Dict[int, QCheckBox] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 12, 8, 8)

        # 「元のs8iの設定を使用」チェックボックス
        self._use_default_cb = QCheckBox("元のs8iの設定を使用（デフォルト）")
        self._use_default_cb.setChecked(True)
        self._use_default_cb.toggled.connect(self._on_default_toggled)
        layout.addWidget(self._use_default_cb)

        # 一括ボタン行
        btn_row = QHBoxLayout()
        btn_all = QPushButton("すべて出力する")
        btn_all.setFixedHeight(24)
        btn_all.clicked.connect(self._select_all)
        btn_none = QPushButton("すべて出力しない")
        btn_none.setFixedHeight(24)
        btn_none.clicked.connect(self._deselect_all)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 個別チェックボックス（2列レイアウト）
        self._fields_row1 = QHBoxLayout()
        self._fields_row2 = QHBoxLayout()
        col1 = QVBoxLayout()
        col2 = QVBoxLayout()
        for i, (idx, label) in enumerate(_HISTORY_FIELDS):
            cb = QCheckBox(label)
            cb.toggled.connect(self._on_field_changed)
            self._checkboxes[idx] = cb
            if i < 6:
                col1.addWidget(cb)
            else:
                col2.addWidget(cb)
        self._fields_row1.addLayout(col1)
        self._fields_row1.addLayout(col2)
        layout.addLayout(self._fields_row1)

        # 初期状態: デフォルトON → 個別チェックボックス無効
        self._set_fields_enabled(False)

    def _on_default_toggled(self, checked: bool) -> None:
        self._set_fields_enabled(not checked)
        self._emit_overrides()

    def _set_fields_enabled(self, enabled: bool) -> None:
        for cb in self._checkboxes.values():
            cb.setEnabled(enabled)

    def _select_all(self) -> None:
        self._use_default_cb.setChecked(False)
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        self._use_default_cb.setChecked(False)
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _on_field_changed(self) -> None:
        self._emit_overrides()

    def _emit_overrides(self) -> None:
        if self._use_default_cb.isChecked():
            self.overrides_changed.emit(None)
        else:
            overrides = {idx: (1 if cb.isChecked() else 0)
                         for idx, cb in self._checkboxes.items()}
            self.overrides_changed.emit(overrides)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_overrides(self) -> Optional[Dict[int, int]]:
        """現在のオーバーライド設定を返します。None = デフォルト。"""
        if self._use_default_cb.isChecked():
            return None
        return {idx: (1 if cb.isChecked() else 0)
                for idx, cb in self._checkboxes.items()}

    def set_overrides(self, overrides: Optional[Dict[int, int]]) -> None:
        """外部からオーバーライド設定を復元します。"""
        if overrides is None:
            self._use_default_cb.setChecked(True)
            for cb in self._checkboxes.values():
                cb.setChecked(False)
        else:
            self._use_default_cb.setChecked(False)
            for idx, cb in self._checkboxes.items():
                cb.setChecked(bool(overrides.get(idx, 0)))
