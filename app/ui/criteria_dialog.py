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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
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
        self._guide_panel: Optional[QWidget] = None
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

        # --- UX改善① 第5回: 建基法解説折りたたみバナー ---
        guide_toggle_btn = QPushButton("▶ 性能基準とは？（クリックで解説を表示）")
        guide_toggle_btn.setStyleSheet(
            "QPushButton {"
            "  background: #e3f2fd; border: 1px solid #90caf9;"
            "  border-radius: 4px; padding: 5px 10px;"
            "  color: #0d47a1; font-size: 11px; text-align: left;"
            "}"
            "QPushButton:hover { background: #bbdefb; }"
        )
        layout.addWidget(guide_toggle_btn)

        # 折りたたみ式パネル（初期は非表示）
        self._guide_panel = QFrame()
        self._guide_panel.setStyleSheet(
            "QFrame { background: #e8f4fd; border: 1px solid #90caf9; border-radius: 4px; }"
        )
        guide_layout = QVBoxLayout(self._guide_panel)
        guide_layout.setContentsMargins(12, 8, 12, 8)
        guide_layout.setSpacing(4)
        guide_lines = [
            ("📐 <b>層間変形角</b>（story drift angle）:", "#1a237e"),
            ("　建築基準法施行令 82条の2 により、<b>大地震時に 1/100 rad 以下</b>が必須です。", "#333"),
            ("　免振建物では 1/200〜1/300 を目標とするケースが多く、高性能な制振ほど小さくなります。", "#555"),
            ("", ""),
            ("🚀 <b>絶対加速度</b>（absolute acceleration）:", "#1a237e"),
            ("　居住性・設備・内容物の保護を目的に設定します。目安: 0.2〜0.4 G（≈2〜4 m/s²）。", "#555"),
            ("", ""),
            ("💡 <b>プリセットについて:</b>", "#1a237e"),
            ("　大地震時(1/100): 建基法最低限 ／ 中地震時(1/200): 一般的な免振目標 ／ 高性能(1/300): 医療・免震倉庫等", "#555"),
        ]
        for text, color in guide_lines:
            if not text:
                guide_layout.addSpacing(2)
                continue
            lbl = QLabel(text)
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
            guide_layout.addWidget(lbl)

        self._guide_panel.setVisible(False)
        layout.addWidget(self._guide_panel)

        def _toggle_guide():
            visible = not self._guide_panel.isVisible()
            self._guide_panel.setVisible(visible)
            guide_toggle_btn.setText(
                ("▼ 性能基準とは？（クリックで閉じる）" if visible
                 else "▶ 性能基準とは？（クリックで解説を表示）")
            )
            self.adjustSize()

        guide_toggle_btn.clicked.connect(_toggle_guide)

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

        # --- UX改善① 第5回: プリセットボタン（三色色分け + 適用バッジ） ---
        preset_header = QHBoxLayout()
        preset_header.addWidget(QLabel("プリセット:"))

        # 適用済みバッジ（プリセット選択後に表示）
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
        layout.addLayout(preset_header)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)

        # 大地震時 = 赤系（建基法最低限）
        btn_large = QPushButton("🔴 大地震時 (1/100)")
        btn_large.setToolTip(
            "最大層間変形角 1/100 rad\n"
            "建築基準法施行令 82条の2 の最低基準です。\n"
            "制振装置なしの一般建築物の目標レベルです。"
        )
        btn_large.setStyleSheet(
            "QPushButton { background: #ffebee; color: #b71c1c; border: 1px solid #ef9a9a;"
            "  border-radius: 4px; padding: 5px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #ffcdd2; }"
        )
        btn_large.clicked.connect(lambda: self._apply_preset(1 / 100, "大地震時 (1/100 rad)"))
        preset_row.addWidget(btn_large)

        # 中地震時 = 橙系（一般的な免振目標）
        btn_medium = QPushButton("🟡 中地震時 (1/200)")
        btn_medium.setToolTip(
            "最大層間変形角 1/200 rad\n"
            "一般的な免振建物の目標性能レベルです。\n"
            "居住性の確保・設備の損傷防止に効果的です。"
        )
        btn_medium.setStyleSheet(
            "QPushButton { background: #fff8e1; color: #e65100; border: 1px solid #ffcc80;"
            "  border-radius: 4px; padding: 5px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #ffe082; }"
        )
        btn_medium.clicked.connect(lambda: self._apply_preset(1 / 200, "中地震時 (1/200 rad)"))
        preset_row.addWidget(btn_medium)

        # 高性能 = 緑系（医療・免震倉庫等）
        btn_strict = QPushButton("🟢 高性能 (1/300)")
        btn_strict.setToolTip(
            "最大層間変形角 1/300 rad\n"
            "医療施設・免震倉庫・美術館等の高性能目標レベルです。\n"
            "精密機器の保護や業務継続性が求められる建物に適します。"
        )
        btn_strict.setStyleSheet(
            "QPushButton { background: #e8f5e9; color: #1b5e20; border: 1px solid #a5d6a7;"
            "  border-radius: 4px; padding: 5px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #c8e6c9; }"
        )
        btn_strict.clicked.connect(lambda: self._apply_preset(1 / 300, "高性能 (1/300 rad)"))
        preset_row.addWidget(btn_strict)

        preset_row.addStretch()
        layout.addLayout(preset_row)

        # --- ボタンボックス ---
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

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
