"""
app/ui/damper_catalog_dialog.py
ダンパーカタログダイアログ。

制振・免震装置のカタログから仕様を選択して、
解析ケースに適用するためのダイアログです。
"""

from __future__ import annotations

from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QFormLayout,
)

from app.models.damper_catalog import (
    DamperCatalog,
    DamperSpec,
    DAMPER_CATEGORIES,
    get_catalog,
)


class DamperCatalogDialog(QDialog):
    """
    ダンパーカタログ選択ダイアログ。

    カテゴリツリーでダンパー種類を閲覧し、
    パラメータの詳細を確認してから適用できます。

    Signals
    -------
    specSelected : DamperSpec
        ダンパー仕様が選択された際に発行されます。
    """

    specSelected = Signal(object)  # DamperSpec

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        catalog: Optional[DamperCatalog] = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog or get_catalog()
        self._selected_spec: Optional[DamperSpec] = None
        self.setWindowTitle("ダンパーカタログ")
        self.setMinimumSize(900, 600)
        self._setup_ui()
        self._populate_tree()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- 検索バー ----
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("ダンパー名、タグ、説明で検索…")
        self._search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self._search_edit)

        self._category_filter = QComboBox()
        self._category_filter.addItem("全カテゴリ", "")
        for key, info in DAMPER_CATEGORIES.items():
            self._category_filter.addItem(
                f"{info['icon']} {info['label']}", key
            )
        self._category_filter.currentIndexChanged.connect(self._on_filter_changed)
        search_row.addWidget(self._category_filter)
        layout.addLayout(search_row)

        # ---- メインスプリッター ----
        splitter = QSplitter(Qt.Horizontal)

        # 左: カテゴリツリー
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["ダンパーカタログ"])
        self._tree.setMinimumWidth(280)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        splitter.addWidget(self._tree)

        # 右: 詳細パネル
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # ダンパー情報
        self._info_group = QGroupBox("ダンパー情報")
        info_layout = QFormLayout(self._info_group)

        self._lbl_name = QLabel("-")
        self._lbl_name.setStyleSheet("font-weight: bold; font-size: 14px;")
        info_layout.addRow("名称:", self._lbl_name)

        self._lbl_category = QLabel("-")
        info_layout.addRow("カテゴリ:", self._lbl_category)

        self._lbl_keyword = QLabel("-")
        info_layout.addRow("SNAP キーワード:", self._lbl_keyword)

        self._lbl_manufacturer = QLabel("-")
        info_layout.addRow("メーカー:", self._lbl_manufacturer)

        self._lbl_desc = QLabel("-")
        self._lbl_desc.setWordWrap(True)
        info_layout.addRow("説明:", self._lbl_desc)

        self._lbl_tags = QLabel("-")
        self._lbl_tags.setWordWrap(True)
        info_layout.addRow("タグ:", self._lbl_tags)

        right_layout.addWidget(self._info_group)

        # パラメータテーブル
        param_group = QGroupBox("デフォルトパラメータ")
        param_layout = QVBoxLayout(param_group)

        self._param_table = QTableWidget()
        self._param_table.setColumnCount(5)
        self._param_table.setHorizontalHeaderLabels([
            "#", "項目", "値", "範囲", "単位"
        ])
        self._param_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._param_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch
        )
        self._param_table.verticalHeader().setVisible(False)
        param_layout.addWidget(self._param_table)

        right_layout.addWidget(param_group, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter, stretch=1)

        # ---- ボタン ----
        btn_layout = QHBoxLayout()
        self._apply_btn = QPushButton("ケースに適用")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        btn_layout.addWidget(self._apply_btn)

        btn_layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        btn_layout.addWidget(btn_box)
        layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def _populate_tree(self, filter_category: str = "", search_text: str = "") -> None:
        """カタログからツリーを構築します。"""
        self._tree.clear()
        categories_used = {}

        # フィルタされた仕様を取得
        if search_text:
            specs = self._catalog.search(search_text)
        else:
            specs = self._catalog.all_specs

        if filter_category:
            specs = [s for s in specs if s.category == filter_category]

        for spec in specs:
            cat = spec.category
            if cat not in categories_used:
                cat_info = DAMPER_CATEGORIES.get(cat, {})
                cat_label = f"{cat_info.get('icon', '')} {cat_info.get('label', cat)}"
                cat_item = QTreeWidgetItem([cat_label])
                cat_item.setData(0, Qt.UserRole, None)
                cat_item.setExpanded(True)
                self._tree.addTopLevelItem(cat_item)
                categories_used[cat] = cat_item

            parent = categories_used[cat]
            spec_item = QTreeWidgetItem([spec.name])
            spec_item.setData(0, Qt.UserRole, spec.id)
            if spec.is_custom:
                spec_item.setText(0, f"{spec.name} [カスタム]")
            parent.addChild(spec_item)

        self._tree.expandAll()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_tree_selection(self, current: QTreeWidgetItem, previous) -> None:
        if current is None:
            self._clear_detail()
            return
        spec_id = current.data(0, Qt.UserRole)
        if spec_id is None:
            # カテゴリヘッダーが選択された
            self._clear_detail()
            return
        spec = self._catalog.get_by_id(spec_id)
        if spec:
            self._show_detail(spec)
            self._selected_spec = spec
            self._apply_btn.setEnabled(True)
        else:
            self._clear_detail()

    def _on_search(self, text: str) -> None:
        cat = self._category_filter.currentData() or ""
        self._populate_tree(filter_category=cat, search_text=text)

    def _on_filter_changed(self, idx: int) -> None:
        cat = self._category_filter.currentData() or ""
        text = self._search_edit.text()
        self._populate_tree(filter_category=cat, search_text=text)

    def _on_apply(self) -> None:
        if self._selected_spec:
            self.specSelected.emit(self._selected_spec)
            self.accept()

    # ------------------------------------------------------------------
    # Detail panel
    # ------------------------------------------------------------------

    def _show_detail(self, spec: DamperSpec) -> None:
        """ダンパー仕様の詳細を表示します。"""
        self._lbl_name.setText(spec.name)

        cat_info = DAMPER_CATEGORIES.get(spec.category, {})
        self._lbl_category.setText(
            f"{cat_info.get('icon', '')} {cat_info.get('label', spec.category)}"
        )
        self._lbl_keyword.setText(spec.snap_keyword)
        self._lbl_manufacturer.setText(spec.manufacturer or "-")
        self._lbl_desc.setText(spec.description or "-")
        self._lbl_tags.setText(", ".join(spec.tags) if spec.tags else "-")

        # パラメータテーブル更新
        from app.ui.case_edit_dialog import _get_damper_field_labels
        field_labels = _get_damper_field_labels(spec.snap_keyword)

        params = spec.parameters
        ranges = spec.param_ranges
        self._param_table.setRowCount(len(params))

        for row, (idx, val) in enumerate(sorted(params.items(), key=lambda x: int(x[0]))):
            # #
            idx_item = QTableWidgetItem(idx)
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 0, idx_item)

            # 項目名
            label = field_labels.get(int(idx), "")
            rng = ranges.get(idx, {})
            if not label and rng:
                label = rng.get("label", "")
            label_item = QTableWidgetItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 1, label_item)

            # 値
            val_item = QTableWidgetItem(str(val))
            val_item.setFlags(val_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 2, val_item)

            # 範囲
            if idx in ranges:
                r = ranges[idx]
                range_text = f"{r.get('min', '')} ~ {r.get('max', '')}"
            else:
                range_text = "-"
            range_item = QTableWidgetItem(range_text)
            range_item.setFlags(range_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 3, range_item)

            # 単位
            unit = rng.get("unit", "-") if rng else "-"
            unit_item = QTableWidgetItem(unit)
            unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 4, unit_item)

    def _clear_detail(self) -> None:
        """詳細パネルをクリアします。"""
        self._lbl_name.setText("-")
        self._lbl_category.setText("-")
        self._lbl_keyword.setText("-")
        self._lbl_manufacturer.setText("-")
        self._lbl_desc.setText("-")
        self._lbl_tags.setText("-")
        self._param_table.setRowCount(0)
        self._selected_spec = None
        self._apply_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_spec(self) -> Optional[DamperSpec]:
        """選択されたダンパー仕様を返します。"""
        return self._selected_spec
