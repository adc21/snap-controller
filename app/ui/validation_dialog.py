"""
app/ui/validation_dialog.py
バリデーション結果表示ダイアログ。

解析実行前の入力チェック結果を一覧表示します。
エラー/警告/情報をレベル別に表示し、問題のあるフィールドを特定できます。

UX改善（第4回）③: 最優先修正ガイドカード追加。
  エラーが存在する場合、ツリー表示の上部に「今すぐ修正すべきこと」カードを表示します。
  最も重要なエラー（先頭エラー）の「修正提案」テキストを大きく表示し、
  複数のエラーがある場合は件数バッジを付けます。
  警告のみの場合は「警告確認」カードを黄色で表示。
  エラーなしの場合は「実行可能」カードを緑で表示。
  `_make_top_action_card()` メソッドを追加。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.validation import (
    ValidationLevel,
    ValidationMessage,
    ValidationResult,
)


class ValidationDialog(QDialog):
    """
    バリデーション結果ダイアログ。

    Parameters
    ----------
    result : ValidationResult
        バリデーション結果。
    case_name : str
        表示するケース名。
    parent : QWidget, optional
    """

    def __init__(
        self,
        result: ValidationResult,
        case_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self._case_name = case_name
        self.setWindowTitle("バリデーション結果")
        self.setMinimumSize(600, 400)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- サマリー ----
        summary = self._make_summary()
        layout.addWidget(summary)

        # UX改善（第4回）③: 最優先修正ガイドカード
        action_card = self._make_top_action_card()
        layout.addWidget(action_card)

        # ---- メッセージツリー ----
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["レベル", "カテゴリ", "メッセージ", "提案"])
        self._tree.setColumnWidth(0, 80)
        self._tree.setColumnWidth(1, 100)
        self._tree.setColumnWidth(2, 300)
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)

        self._populate_tree()
        layout.addWidget(self._tree, stretch=1)

        # ---- ボタン ----
        if self._result.has_errors:
            btn_box = QDialogButtonBox(QDialogButtonBox.Close)
            btn_box.rejected.connect(self.reject)

            # 「それでも実行」ボタンは表示しない（エラーは必須修正）
            layout.addWidget(QLabel(
                "<b style='color: red;'>エラーがあります。修正してから実行してください。</b>"
            ))
        else:
            btn_box = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            )
            btn_box.accepted.connect(self.accept)
            btn_box.rejected.connect(self.reject)
            if self._result.has_warnings:
                layout.addWidget(QLabel(
                    "<b style='color: orange;'>警告がありますが、実行は可能です。</b>"
                ))
            else:
                layout.addWidget(QLabel(
                    "<b style='color: green;'>問題なし。実行できます。</b>"
                ))

        layout.addWidget(btn_box)

    def _make_top_action_card(self) -> QWidget:
        """
        UX改善（第4回）③: 「今すぐ修正すべきこと」最優先ガイドカードを作成します。

        エラーの有無・件数に応じて3パターンのカードを表示します:
        1. エラーあり: 赤カード + 最初のエラーの修正提案を大きく表示
        2. 警告のみ:   黄カード + 警告内容の確認を促す
        3. 全てOK:    緑カード + 実行可能であることを明示
        """
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(4)

        errors = [m for m in self._result.messages if m.level == ValidationLevel.ERROR]
        warnings = [m for m in self._result.messages if m.level == ValidationLevel.WARNING]

        if errors:
            # エラーあり: 赤カード
            card.setStyleSheet(
                "QFrame { background-color: #ffebee; border: 1px solid #e53935; border-radius: 6px; }"
            )
            title_lbl = QLabel(
                f"🔴 <b>今すぐ修正してください</b>"
                + (f"　（エラー {len(errors)}件）" if len(errors) > 1 else "")
            )
            title_lbl.setStyleSheet("color: #b71c1c; font-size: 12px;")
            card_layout.addWidget(title_lbl)

            # 最初のエラーの修正提案を表示
            first_err = errors[0]
            msg_lbl = QLabel(f"<b>問題:</b> {first_err.message}")
            msg_lbl.setWordWrap(True)
            msg_lbl.setStyleSheet("color: #c62828; font-size: 11px;")
            card_layout.addWidget(msg_lbl)

            if first_err.suggestion:
                suggestion_lbl = QLabel(f"✏ <b>修正方法:</b> {first_err.suggestion}")
                suggestion_lbl.setWordWrap(True)
                suggestion_lbl.setStyleSheet("color: #333; font-size: 11px;")
                card_layout.addWidget(suggestion_lbl)

            if len(errors) > 1:
                more_lbl = QLabel(
                    f"他に <b>{len(errors) - 1}件</b>のエラーがあります。"
                    "下のリストで全て確認してください。"
                )
                more_lbl.setStyleSheet("color: #888; font-size: 10px;")
                card_layout.addWidget(more_lbl)

        elif warnings:
            # 警告のみ: 黄カード
            card.setStyleSheet(
                "QFrame { background-color: #fff8e1; border: 1px solid #fb8c00; border-radius: 6px; }"
            )
            title_lbl = QLabel(
                f"⚠ <b>警告を確認してください</b>　（警告 {len(warnings)}件）"
            )
            title_lbl.setStyleSheet("color: #e65100; font-size: 12px;")
            card_layout.addWidget(title_lbl)

            first_warn = warnings[0]
            msg_lbl = QLabel(f"<b>主な警告:</b> {first_warn.message}")
            msg_lbl.setWordWrap(True)
            msg_lbl.setStyleSheet("color: #bf360c; font-size: 11px;")
            card_layout.addWidget(msg_lbl)

            hint_lbl = QLabel("警告がありますが解析は実行できます。問題なければ「OK」で続行してください。")
            hint_lbl.setStyleSheet("color: #555; font-size: 10px;")
            card_layout.addWidget(hint_lbl)

        else:
            # 全てOK: 緑カード
            card.setStyleSheet(
                "QFrame { background-color: #e8f5e9; border: 1px solid #43a047; border-radius: 6px; }"
            )
            ok_lbl = QLabel("✅ <b>問題なし。このまま「OK」を押して解析を開始してください。</b>")
            ok_lbl.setStyleSheet("color: #1b5e20; font-size: 12px;")
            ok_lbl.setWordWrap(True)
            card_layout.addWidget(ok_lbl)

        return card

    def _make_summary(self) -> QWidget:
        """サマリー部分のウィジェットを作成。"""
        group = QGroupBox(f"バリデーション結果: {self._case_name}" if self._case_name else "バリデーション結果")
        h = QHBoxLayout(group)

        # エラー数
        err_lbl = QLabel(f"❌ エラー: {self._result.error_count}")
        if self._result.error_count > 0:
            err_lbl.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        else:
            err_lbl.setStyleSheet("color: gray; font-size: 14px;")
        h.addWidget(err_lbl)

        # 警告数
        warn_lbl = QLabel(f"⚠️ 警告: {self._result.warning_count}")
        if self._result.warning_count > 0:
            warn_lbl.setStyleSheet("color: orange; font-weight: bold; font-size: 14px;")
        else:
            warn_lbl.setStyleSheet("color: gray; font-size: 14px;")
        h.addWidget(warn_lbl)

        # 情報数
        info_lbl = QLabel(f"ℹ️ 情報: {self._result.info_count}")
        info_lbl.setStyleSheet("color: gray; font-size: 14px;")
        h.addWidget(info_lbl)

        h.addStretch()
        return group

    def _populate_tree(self) -> None:
        """メッセージをツリーに展開。"""
        level_colors = {
            ValidationLevel.ERROR: QColor(255, 200, 200),
            ValidationLevel.WARNING: QColor(255, 240, 200),
            ValidationLevel.INFO: QColor(220, 240, 255),
        }
        level_labels = {
            ValidationLevel.ERROR: "エラー",
            ValidationLevel.WARNING: "警告",
            ValidationLevel.INFO: "情報",
        }

        for msg in sorted(self._result.messages, key=lambda m: m.level.value):
            item = QTreeWidgetItem()
            item.setText(0, f"{msg.icon} {level_labels.get(msg.level, '')}")
            item.setText(1, msg.category)
            item.setText(2, msg.message)
            item.setText(3, msg.suggestion)

            bg = level_colors.get(msg.level)
            if bg:
                for col in range(4):
                    item.setBackground(col, bg)

            self._tree.addTopLevelItem(item)


class BatchValidationDialog(QDialog):
    """
    複数ケースの一括バリデーション結果ダイアログ。

    Parameters
    ----------
    results : dict
        {case_name: ValidationResult}
    parent : QWidget, optional
    """

    def __init__(
        self,
        results: Dict[str, ValidationResult],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._results = results
        self.setWindowTitle("一括バリデーション結果")
        self.setMinimumSize(700, 500)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # サマリー
        total_errors = sum(r.error_count for r in self._results.values())
        total_warnings = sum(r.warning_count for r in self._results.values())
        valid_count = sum(1 for r in self._results.values() if r.is_valid)
        total = len(self._results)

        summary_lbl = QLabel(
            f"<b>全 {total} ケース中 {valid_count} ケースが実行可能</b>"
            f"（エラー計 {total_errors}, 警告計 {total_warnings}）"
        )
        summary_lbl.setStyleSheet("font-size: 14px; padding: 8px;")
        layout.addWidget(summary_lbl)

        # ケースごとのツリー
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["ケース / メッセージ", "カテゴリ", "提案"])
        self._tree.setColumnWidth(0, 400)
        self._tree.setColumnWidth(1, 100)

        for case_name, result in self._results.items():
            status = "✅" if result.is_valid else "❌"
            case_item = QTreeWidgetItem([
                f"{status} {case_name} "
                f"(E:{result.error_count} W:{result.warning_count})"
            ])
            if result.has_errors:
                case_item.setBackground(0, QColor(255, 200, 200))

            for msg in result.messages:
                child = QTreeWidgetItem([
                    f"  {msg.icon} {msg.message}",
                    msg.category,
                    msg.suggestion,
                ])
                case_item.addChild(child)

            self._tree.addTopLevelItem(case_item)
            if result.has_errors:
                case_item.setExpanded(True)

        layout.addWidget(self._tree, stretch=1)

        # ボタン
        has_any_error = total_errors > 0
        if has_any_error:
            btn_box = QDialogButtonBox(QDialogButtonBox.Close)
            btn_box.rejected.connect(self.reject)
            layout.addWidget(QLabel(
                "<b style='color: red;'>エラーのあるケースがあります。"
                "修正してから実行してください。</b>"
            ))
        else:
            btn_box = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel
            )
            btn_box.accepted.connect(self.accept)
            btn_box.rejected.connect(self.reject)
            if total_warnings > 0:
                layout.addWidget(QLabel(
                    "<b style='color: orange;'>警告がありますが、全ケース実行可能です。</b>"
                ))

        layout.addWidget(btn_box)

    @property
    def all_valid(self) -> bool:
        return all(r.is_valid for r in self._results.values())
