"""
app/ui/template_dialog.py
ケーステンプレート管理ダイアログ。

UX改善（第6回①）: ダブルクリック即適用 + カテゴリ色分けバッジ + テンプレート件数バッジ追加。
  - テンプレートリストをダブルクリックすると確認なしにすぐ適用（templateApplied発行→ダイアログ閉じる）
  - ビルトイン/ユーザーおよびカテゴリ（免震・制振・共通・その他）で行背景を色分け
  - ヘッダーにビルトイン件数・ユーザー件数バッジを表示し、テンプレート全体の構成を一目で把握
  - 「適用」ボタンをよりわかりやすく: 選択時は青で強調、未選択時はグレーアウト

テンプレートの一覧表示、選択適用、新規保存、削除を行います。

レイアウト:
  ┌──────────────────────────────────────────────┐
  │ [カテゴリフィルタ] [検索]                     │
  ├──────────────┬───────────────────────────────┤
  │ テンプレート │ 詳細プレビュー                │
  │ リスト       │ - 名前                        │
  │              │ - 説明                        │
  │              │ - パラメータ一覧              │
  ├──────────────┴───────────────────────────────┤
  │ [適用] [保存] [削除]           [閉じる]      │
  └──────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.case_template import (
    CaseTemplate,
    TemplateManager,
    get_builtin_templates,
)


class TemplateDialog(QDialog):
    """
    テンプレート管理ダイアログ。

    Signals
    -------
    templateApplied(CaseTemplate)
        テンプレートが適用された際に発信。
    """

    templateApplied = Signal(object)

    def __init__(
        self,
        template_manager: Optional[TemplateManager] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ケーステンプレート")
        self.setMinimumSize(700, 480)

        self._manager = template_manager or TemplateManager()
        self._builtins = get_builtin_templates()
        self._current_template: Optional[CaseTemplate] = None
        self._current_is_builtin: bool = False

        # UX改善（第6回①）: カテゴリ色分けマップ（ライト・ダーク兼用の薄い背景色）
        self._CATEGORY_BG: dict = {
            "免震":   "#e8f4f8",  # 水色
            "制振":   "#fff3e0",  # 橙色
            "共通":   "#f3e5f5",  # 紫色
            "その他": "#f5f5f5",  # グレー
        }
        self._CATEGORY_FG: dict = {
            "免震":   "#01579b",
            "制振":   "#bf360c",
            "共通":   "#4a148c",
            "その他": "#616161",
        }

        self._setup_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_filter_row())
        layout.addWidget(self._build_main_splitter(), stretch=1)
        layout.addLayout(self._build_button_row())

    def _build_filter_row(self) -> QHBoxLayout:
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("カテゴリ:"))
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("すべて")
        self._cat_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._cat_combo)

        filter_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("名前・タグで検索…")
        self._search_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._search_edit, stretch=1)

        self._builtin_badge = QLabel()
        self._builtin_badge.setStyleSheet(
            "background:#e3f2fd; color:#0d47a1; border-radius:8px;"
            "padding:1px 7px; font-size:10px; font-weight:bold;"
        )
        self._builtin_badge.setToolTip("ビルトインテンプレート件数")
        filter_row.addWidget(self._builtin_badge)

        self._user_badge = QLabel()
        self._user_badge.setStyleSheet(
            "background:#e8f5e9; color:#1b5e20; border-radius:8px;"
            "padding:1px 7px; font-size:10px; font-weight:bold;"
        )
        self._user_badge.setToolTip("ユーザーテンプレート件数")
        filter_row.addWidget(self._user_badge)
        return filter_row

    def _build_main_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_template_list())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _build_template_list(self) -> QListWidget:
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.setToolTip(
            "テンプレートをダブルクリックするとすぐに適用できます。\n"
            "シングルクリックで詳細を確認してから[選択ケースに適用]ボタンで適用します。"
        )
        return self._list

    def _build_detail_panel(self) -> QWidget:
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(8, 0, 0, 0)

        self._detail_name = QLabel()
        self._detail_name.setStyleSheet("font-size: 14px; font-weight: bold;")
        detail_layout.addWidget(self._detail_name)

        self._detail_desc = QLabel()
        self._detail_desc.setWordWrap(True)
        detail_layout.addWidget(self._detail_desc)

        self._detail_category = QLabel()
        detail_layout.addWidget(self._detail_category)

        self._detail_tags = QLabel()
        detail_layout.addWidget(self._detail_tags)

        params_group = QGroupBox("パラメータ")
        params_layout = QVBoxLayout(params_group)
        self._params_text = QTextEdit()
        self._params_text.setReadOnly(True)
        self._params_text.setMaximumHeight(200)
        params_layout.addWidget(self._params_text)
        detail_layout.addWidget(params_group)

        detail_layout.addStretch()
        return detail_widget

    def _build_button_row(self) -> QHBoxLayout:
        btn_row = QHBoxLayout()

        self._apply_btn = QPushButton("✅ 選択ケースに適用")
        self._apply_btn.setToolTip(
            "選択中のテンプレートを現在のケースに適用します\n"
            "（ダブルクリックでも即適用できます）"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        self._apply_btn.setEnabled(False)
        self._apply_btn.setMinimumHeight(32)
        btn_row.addWidget(self._apply_btn)

        self._delete_btn = QPushButton("削除")
        self._delete_btn.setToolTip("選択中のユーザーテンプレートを削除します")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._delete_btn)

        btn_row.addStretch()

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        return btn_row

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _refresh_list(self) -> None:
        """テンプレートリストを再構築します。"""
        self._list.clear()
        self._cat_combo.blockSignals(True)
        current_cat = self._cat_combo.currentText()
        self._cat_combo.clear()
        self._cat_combo.addItem("すべて")

        categories = set()
        all_templates = self._get_filtered_templates()

        # ビルトインテンプレート
        for tpl in self._builtins:
            categories.add(tpl.category)
        # ユーザーテンプレート
        for tpl in self._manager.list_all():
            categories.add(tpl.category)

        for cat in sorted(categories):
            self._cat_combo.addItem(cat)
        # 以前の選択を復元
        idx = self._cat_combo.findText(current_cat)
        if idx >= 0:
            self._cat_combo.setCurrentIndex(idx)
        self._cat_combo.blockSignals(False)

        # テンプレートリストを構築
        for tpl in all_templates:
            is_builtin = tpl in self._builtins
            prefix = "📦 " if is_builtin else "📝 "
            item = QListWidgetItem(f"{prefix}{tpl.name}")
            item.setData(Qt.UserRole, tpl)
            item.setData(Qt.UserRole + 1, is_builtin)
            # UX改善（第6回①）: カテゴリ色分け背景
            self._apply_category_color(item, tpl.category)
            self._list.addItem(item)

        # UX改善（第6回①）: 件数バッジ更新
        self._update_count_badges()

    def _get_filtered_templates(self) -> List[CaseTemplate]:
        """フィルタ条件に合うテンプレートを返します。"""
        cat_filter = self._cat_combo.currentText() if self._cat_combo.currentIndex() > 0 else None
        search_kw = self._search_edit.text().strip().lower()

        all_tpls: List[CaseTemplate] = list(self._builtins) + self._manager.list_all()
        filtered = []

        for tpl in all_tpls:
            if cat_filter and tpl.category != cat_filter:
                continue
            if search_kw:
                if not (search_kw in tpl.name.lower() or
                        search_kw in tpl.description.lower() or
                        any(search_kw in tag.lower() for tag in tpl.tags)):
                    continue
            filtered.append(tpl)

        return filtered

    def _on_filter_changed(self) -> None:
        """フィルタ条件が変更された際の処理。"""
        self._list.clear()
        for tpl in self._get_filtered_templates():
            is_builtin = tpl in self._builtins
            prefix = "📦 " if is_builtin else "📝 "
            item = QListWidgetItem(f"{prefix}{tpl.name}")
            item.setData(Qt.UserRole, tpl)
            item.setData(Qt.UserRole + 1, is_builtin)
            # UX改善（第6回①）: カテゴリ色分け背景
            self._apply_category_color(item, tpl.category)
            self._list.addItem(item)
        # UX改善（第6回①）: 件数バッジ更新
        self._update_count_badges()

    def _on_selection_changed(self, current: Optional[QListWidgetItem], _) -> None:
        """テンプレート選択変更時のハンドラ。"""
        if current is None:
            self._current_template = None
            self._current_is_builtin = False
            self._apply_btn.setEnabled(False)
            self._apply_btn.setStyleSheet("")  # UX改善（第6回①）: スタイルリセット
            self._delete_btn.setEnabled(False)
            self._clear_detail()
            return

        tpl = current.data(Qt.UserRole)
        is_builtin = current.data(Qt.UserRole + 1)
        self._current_template = tpl
        self._current_is_builtin = is_builtin
        self._apply_btn.setEnabled(True)
        # UX改善（第6回①）: 選択時に適用ボタンを青強調
        self._apply_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; "
            "font-weight: bold; border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        self._delete_btn.setEnabled(not is_builtin)
        self._show_detail(tpl, is_builtin)

    def _show_detail(self, tpl: CaseTemplate, is_builtin: bool) -> None:
        """テンプレートの詳細を表示します。"""
        source = "ビルトイン" if is_builtin else "ユーザー"
        self._detail_name.setText(tpl.name)
        self._detail_desc.setText(tpl.description or "(説明なし)")
        self._detail_category.setText(f"カテゴリ: {tpl.category}  |  種別: {source}")
        self._detail_tags.setText(
            f"タグ: {', '.join(tpl.tags)}" if tpl.tags else "タグ: (なし)"
        )

        # パラメータ表示
        lines = []
        if tpl.parameters:
            lines.append("【解析パラメータ】")
            for k, v in tpl.parameters.items():
                lines.append(f"  {k}: {v}")
        if tpl.damper_params:
            lines.append("【ダンパーパラメータ】")
            for k, v in tpl.damper_params.items():
                lines.append(f"  {k}: {v}")
        if not lines:
            lines.append("(パラメータ設定なし)")
        self._params_text.setText("\n".join(lines))

    def _clear_detail(self) -> None:
        """詳細表示をクリアします。"""
        self._detail_name.setText("")
        self._detail_desc.setText("")
        self._detail_category.setText("")
        self._detail_tags.setText("")
        self._params_text.clear()

    # ------------------------------------------------------------------
    # UX改善（第6回①）: ヘルパーメソッド
    # ------------------------------------------------------------------

    def _apply_category_color(self, item: QListWidgetItem, category: str) -> None:
        """
        UX改善（第6回①）: リストアイテムにカテゴリ別背景色を設定します。

        カテゴリ（免震/制振/共通/その他）に合った薄い背景色を付けることで、
        スクロールしながらでも分類をひと目で把握できます。
        """
        from PySide6.QtGui import QColor
        bg = self._CATEGORY_BG.get(category, "#f5f5f5")
        fg = self._CATEGORY_FG.get(category, "#333333")
        item.setBackground(QColor(bg))
        item.setForeground(QColor(fg))

    def _update_count_badges(self) -> None:
        """
        UX改善（第6回①）: ビルトイン件数・ユーザー件数バッジを更新します。

        現在のフィルター適用後の一覧に含まれる件数を表示し、
        どのくらいのテンプレートがあるかを一目で把握できます。
        """
        if not hasattr(self, "_builtin_badge") or not hasattr(self, "_user_badge"):
            return
        builtin_count = sum(
            1 for i in range(self._list.count())
            if self._list.item(i).data(Qt.UserRole + 1)
        )
        user_count = self._list.count() - builtin_count
        self._builtin_badge.setText(f"📦 ビルトイン {builtin_count}件")
        self._user_badge.setText(f"📝 ユーザー {user_count}件")
        self._user_badge.setVisible(user_count > 0)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_double_click(self, item: QListWidgetItem) -> None:
        """
        UX改善（第6回①）: ダブルクリックで選択テンプレートを即適用します。

        シングルクリックで詳細を確認してから[適用]ボタンを押すフローに加え、
        よく使うテンプレートはダブルクリック1操作で即適用できます。
        """
        tpl = item.data(Qt.UserRole)
        if tpl:
            self._current_template = tpl
            self.templateApplied.emit(tpl)
            self.accept()

    def _on_apply(self) -> None:
        """テンプレートを適用します。"""
        if self._current_template:
            self.templateApplied.emit(self._current_template)
            self.accept()

    def _on_delete(self) -> None:
        """ユーザーテンプレートを削除します。"""
        if self._current_template is None or self._current_is_builtin:
            return
        reply = QMessageBox.question(
            self,
            "確認",
            f"テンプレート「{self._current_template.name}」を削除しますか？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._manager.remove(self._current_template.name)
            self._refresh_list()
            self._clear_detail()

    @property
    def selected_template(self) -> Optional[CaseTemplate]:
        """選択中のテンプレート。"""
        return self._current_template


class SaveTemplateDialog(QDialog):
    """
    ケースからテンプレートを保存するダイアログ。

    Usage::

        dlg = SaveTemplateDialog(case=some_case, parent=self)
        if dlg.exec():
            template = dlg.get_template()
            manager.add(template)
    """

    def __init__(
        self,
        case=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("テンプレートとして保存")
        self.setMinimumWidth(400)

        self._case = case
        self._template: Optional[CaseTemplate] = None
        self._setup_ui()

        # ケースがある場合はデフォルト値を設定
        if case:
            self._name_edit.setText(f"{case.name} テンプレート")
            self._desc_edit.setText(f"ケース「{case.name}」のダンパー設定")

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("テンプレート名を入力…")
        form.addRow("名前:", self._name_edit)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("説明（任意）")
        form.addRow("説明:", self._desc_edit)

        self._cat_combo = QComboBox()
        self._cat_combo.setEditable(True)
        self._cat_combo.addItems(["共通", "免震", "制振", "その他"])
        form.addRow("カテゴリ:", self._cat_combo)

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("カンマ区切りでタグを入力…")
        form.addRow("タグ:", self._tags_edit)

        layout.addLayout(form)

        # パラメータプレビュー
        if self._case:
            preview_group = QGroupBox("保存されるパラメータ")
            preview_layout = QVBoxLayout(preview_group)
            preview_text = QTextEdit()
            preview_text.setReadOnly(True)
            preview_text.setMaximumHeight(150)
            lines = []
            if self._case.parameters:
                lines.append("【解析パラメータ】")
                for k, v in self._case.parameters.items():
                    lines.append(f"  {k}: {v}")
            if self._case.damper_params:
                lines.append("【ダンパーパラメータ】")
                for k, v in self._case.damper_params.items():
                    lines.append(f"  {k}: {v}")
            if not lines:
                lines.append("(パラメータなし)")
            preview_text.setText("\n".join(lines))
            preview_layout.addWidget(preview_text)
            layout.addWidget(preview_group)

        # ボタン
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_ok(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "警告", "テンプレート名を入力してください。")
            return

        tags_raw = self._tags_edit.text().strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

        self._template = CaseTemplate(
            name=name,
            description=self._desc_edit.text().strip(),
            category=self._cat_combo.currentText().strip() or "共通",
            parameters=dict(self._case.parameters) if self._case else {},
            damper_params=dict(self._case.damper_params) if self._case else {},
            tags=tags,
        )
        self.accept()

    def get_template(self) -> Optional[CaseTemplate]:
        """保存用テンプレートを返します。"""
        return self._template
