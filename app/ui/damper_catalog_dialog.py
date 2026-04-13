"""
app/ui/damper_catalog_dialog.py
ダンパーカタログダイアログ。

制振・免震装置のカタログから仕様を選択して、
解析ケースに適用するためのダイアログです。

UX改善（第6回②）: カテゴリ選択時の説明バナー追加。
  ツリーのカテゴリヘッダー（オイルダンパー・鋼材ダンパー等）を選択したとき、
  右側詳細パネルの上部にそのダンパー種別の特徴・用途・SNAP キーワードを
  バナー形式で説明します。
  - 建築構造の知識が少ないユーザーでも、各ダンパーの違いと用途が直感的にわかります
  - バナーはカテゴリ色で色分けし、「よく使われる場面」や「特徴」を簡潔に説明
"""

from __future__ import annotations

from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
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

        # UX改善（第6回②）: カテゴリ説明バナー（カテゴリ選択時のみ表示）
        self._cat_banner = QFrame()
        self._cat_banner.setFrameShape(QFrame.StyledPanel)
        self._cat_banner.setStyleSheet(
            "QFrame { background:#e3f2fd; border:1px solid #90caf9; border-radius:6px; margin:4px 0; }"
        )
        cat_banner_layout = QVBoxLayout(self._cat_banner)
        cat_banner_layout.setContentsMargins(10, 6, 10, 6)
        cat_banner_layout.setSpacing(3)

        self._cat_banner_title = QLabel()
        self._cat_banner_title.setStyleSheet(
            "font-size:13px; font-weight:bold; color:#0d47a1; background:transparent;"
        )
        cat_banner_layout.addWidget(self._cat_banner_title)

        self._cat_banner_desc = QLabel()
        self._cat_banner_desc.setWordWrap(True)
        self._cat_banner_desc.setStyleSheet(
            "font-size:11px; color:#1a237e; background:transparent;"
        )
        cat_banner_layout.addWidget(self._cat_banner_desc)

        self._cat_banner_meta = QLabel()
        self._cat_banner_meta.setStyleSheet(
            "font-size:10px; color:#3949ab; background:transparent;"
        )
        cat_banner_layout.addWidget(self._cat_banner_meta)

        self._cat_banner.hide()
        right_layout.addWidget(self._cat_banner)

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

    # UX改善（第6回②）: カテゴリ別詳細説明テキスト
    _CATEGORY_DETAILS: dict = {
        "oil": {
            "color_bg": "#e3f2fd",
            "color_border": "#90caf9",
            "color_title": "#0d47a1",
            "color_text": "#1a237e",
            "usage": "中高層ビルの制振補強・新築制振設計に広く使用",
            "features": "速度依存型: 変位よりも速度に応じて減衰力を発揮。\n"
                        "応答加速度の低減に優れる。リリーフ機構で過大力を制御できる。",
            "typical_range": "減衰係数Cd: 100〜2000 kN/(m/s)^α, 速度指数α: 0.3〜1.0",
        },
        "steel": {
            "color_bg": "#fce4ec",
            "color_border": "#ef9a9a",
            "color_title": "#880e4f",
            "color_text": "#4a148c",
            "usage": "コスト重視の制振補強・中小規模建物に採用例多数",
            "features": "履歴型: 変位に依存して鉄が塑性変形しエネルギーを吸収。\n"
                        "シンプルな構造でメンテナンスコストが低い。",
            "typical_range": "降伏変位: 1〜10 mm, 降伏荷重: 100〜2000 kN",
        },
        "viscous": {
            "color_bg": "#e8f5e9",
            "color_border": "#a5d6a7",
            "color_title": "#1b5e20",
            "color_text": "#2e7d32",
            "usage": "超高層ビル・長周期建物の制振に多用",
            "features": "粘性流体（シリコン等）のせん断抵抗を利用。\n"
                        "低速域から安定した減衰力。温度依存性あり。",
            "typical_range": "減衰係数: 50〜5000 kN/(m/s)",
        },
        "viscoelastic": {
            "color_bg": "#fff3e0",
            "color_border": "#ffcc80",
            "color_title": "#e65100",
            "color_text": "#bf360c",
            "usage": "小中規模建物・免震層と併用するケースも",
            "features": "粘弾性体の剛性（ばね）と減衰（粘性）を同時に持つ。\n"
                        "周波数特性に注意が必要。",
            "typical_range": "貯蔵剛性: 0.1〜10 kN/mm, 損失係数: 0.5〜2.0",
        },
        "tuned_mass": {
            "color_bg": "#f3e5f5",
            "color_border": "#ce93d8",
            "color_title": "#4a148c",
            "color_text": "#6a1b9a",
            "usage": "超高層・タワー・煙突など特定振動モード制御に",
            "features": "付加質量を建物固有周期に同調させて共振制御。\n"
                        "特定の卓越周期への効果が高い。チューニング精度が重要。",
            "typical_range": "質量比: 0.5〜3%, 同調周波数比: 0.95〜1.05",
        },
        "isolator": {
            "color_bg": "#e0f2f1",
            "color_border": "#80cbc4",
            "color_title": "#004d40",
            "color_text": "#00695c",
            "usage": "重要施設・医療機関・戸建〜超高層まで幅広く適用",
            "features": "建物を地盤から絶縁。長周期化により入力地震力を大幅低減。\n"
                        "上部構造はほぼ剛体的に動く。免震層の変位管理が重要。",
            "typical_range": "固有周期: 3〜5 s, 減衰定数: 15〜30%",
        },
    }

    def _on_tree_selection(self, current: QTreeWidgetItem, previous) -> None:
        if current is None:
            self._clear_detail()
            return
        spec_id = current.data(0, Qt.UserRole)
        if spec_id is None:
            # UX改善（第6回②）: カテゴリヘッダー選択時はカテゴリ説明バナーを表示
            # カテゴリ名をツリー表示名から逆引き
            cat_key = self._find_cat_key_from_label(current.text(0))
            if cat_key:
                self._show_category_banner(cat_key)
            else:
                self._clear_detail()
            return
        spec = self._catalog.get_by_id(spec_id)
        if spec:
            self._cat_banner.hide()  # 個別仕様表示時はバナーを隠す
            self._show_detail(spec)
            self._selected_spec = spec
            self._apply_btn.setEnabled(True)
        else:
            self._clear_detail()

    def _find_cat_key_from_label(self, label_text: str) -> str:
        """
        UX改善（第6回②）: ツリー表示ラベルからカテゴリキーを逆引きします。
        例: "💧 オイルダンパー" → "oil"
        """
        for key, info in DAMPER_CATEGORIES.items():
            cat_label = f"{info.get('icon', '')} {info.get('label', key)}"
            if label_text.strip() == cat_label.strip():
                return key
        return ""

    def _show_category_banner(self, cat_key: str) -> None:
        """
        UX改善（第6回②）: カテゴリ説明バナーを表示します。

        ツリーのカテゴリヘッダーが選択されたとき、そのダンパー種別の
        特徴・用途・典型的なパラメータ範囲をバナーで説明します。
        """
        if not hasattr(self, "_cat_banner"):
            return

        cat_info = DAMPER_CATEGORIES.get(cat_key, {})
        detail = self._CATEGORY_DETAILS.get(cat_key, {})

        icon = cat_info.get("icon", "")
        label = cat_info.get("label", cat_key)
        desc = cat_info.get("description", "")
        features = detail.get("features", "")
        usage = detail.get("usage", "")
        typical_range = detail.get("typical_range", "")
        snap_kw = cat_info.get("snap_keyword", "")

        # バナー色設定
        bg = detail.get("color_bg", "#e3f2fd")
        border = detail.get("color_border", "#90caf9")
        title_color = detail.get("color_title", "#0d47a1")
        text_color = detail.get("color_text", "#1a237e")

        self._cat_banner.setStyleSheet(
            f"QFrame {{ background:{bg}; border:1px solid {border}; "
            f"border-radius:6px; margin:4px 0; }}"
        )
        self._cat_banner_title.setStyleSheet(
            f"font-size:13px; font-weight:bold; color:{title_color}; background:transparent;"
        )
        self._cat_banner_desc.setStyleSheet(
            f"font-size:11px; color:{text_color}; background:transparent;"
        )
        self._cat_banner_meta.setStyleSheet(
            f"font-size:10px; color:{text_color}; background:transparent;"
        )

        self._cat_banner_title.setText(f"{icon} {label}  —  {desc}")
        body_lines = []
        if features:
            body_lines.append(f"【特徴】 {features}")
        if usage:
            body_lines.append(f"【主な用途】 {usage}")
        self._cat_banner_desc.setText("\n".join(body_lines))

        meta_parts = []
        if snap_kw:
            meta_parts.append(f"SNAPキーワード: {snap_kw}")
        if typical_range:
            meta_parts.append(f"代表パラメータ範囲: {typical_range}")
        self._cat_banner_meta.setText("  |  ".join(meta_parts))

        self._cat_banner.show()
        # 個別仕様エリアはクリア
        self._apply_btn.setEnabled(False)
        self._selected_spec = None
        self._lbl_name.setText("-")
        self._lbl_category.setText("-")
        self._lbl_keyword.setText("-")
        self._lbl_manufacturer.setText("-")
        self._lbl_desc.setText(f"← ツリーから個別のダンパー仕様を選んでください")
        self._lbl_tags.setText("-")
        self._param_table.setRowCount(0)

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
        from app.ui.damper_field_data import get_damper_field_labels
        field_labels = get_damper_field_labels(spec.snap_keyword)

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
        # UX改善（第6回②）: カテゴリバナーも隠す
        if hasattr(self, "_cat_banner"):
            self._cat_banner.hide()
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
