"""
app/ui/kdb_browser_dialog.py
k-DB（構造部材データベース）ブラウザダイアログ。

k-DB に登録されているダンパー・免震装置の製品一覧を閲覧し、
選択した製品の SNAP パラメータをケース編集ダイアログに反映します。

機能:
  - カテゴリツリー（種別 → メーカーシリーズ → 型番）
  - キーワード検索（型番・シリーズ名）
  - 選択製品の SNAP パラメータプレビュー
  - 「適用」ボタンでケース編集ダイアログに値を反映
  - k-DB インストールパスの設定
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QSortFilterProxyModel, QTimer
from PySide6.QtGui import QStandardItemModel, QStandardItem, QFont, QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
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
    QHeaderView,
    QFormLayout,
    QFileDialog,
    QMessageBox,
    QAbstractItemView,
    QFrame,
)

from app.models.kdb_reader import (
    KdbReader,
    KdbProduct,
    KdbRecord,
    SECTION_TO_SNAP,
    ISOLATOR_SECTION_TO_LABEL,
)


# ---------------------------------------------------------------------------
# カテゴリ表示設定
# ---------------------------------------------------------------------------

CATEGORY_DISPLAY = {
    "DVOD_damping": {
        "label": "制振用粘性/オイルダンパー（DVOD）",
        "icon": "💧",
        "color": "#1565C0",
        "description": "制振層に使用するオイルダンパー・粘性ダンパー（速度依存型）",
        "section_nums": {72, 73},
        "snap_keyword": "DVOD",
    },
    "DVOD_isolation": {
        "label": "免震用粘性/オイルダンパー（DVOD）",
        "icon": "🌊",
        "color": "#00796B",
        "description": "免震層に使用するオイルダンパー・粘性ダンパー",
        "section_nums": {52, 53},
        "snap_keyword": "DVOD",
    },
    "DISD": {
        "label": "免震用履歴型ダンパー（DISD）",
        "icon": "🔄",
        "color": "#795548",
        "description": "免震層に使用する鉛プラグ・低降伏点鋼材系履歴型ダンパー",
        "section_nums": {51},
        "snap_keyword": "DISD",
    },
    "DVD_damping": {
        "label": "制振用粘性ダンパー・減衰こま（DVD）",
        "icon": "🌀",
        "color": "#283593",
        "description": "オイレス工業型・カヤバシステム型などの粘性ダンパー（制振用）",
        "section_nums": {74},
        "snap_keyword": "DVD",
    },
    "DVD_isolation": {
        "label": "免震用粘性ダンパー・減衰こま（DVD）",
        "icon": "🌀",
        "color": "#00695C",
        "description": "免震層に使用する粘性ダンパー（減衰こま）",
        "section_nums": {54},
        "snap_keyword": "DVD",
    },
    "DVED": {
        "label": "制振用粘弾性ダンパー（DVED）",
        "icon": "🟦",
        "color": "#7B1FA2",
        "description": "横浜ゴム型・住友理工型・JFEシビル型などの粘弾性ダンパー",
        "section_nums": {75},
        "snap_keyword": "DVED",
    },
    "DSD": {
        "label": "鋼材ダンパー（DSD）",
        "icon": "🔩",
        "color": "#E65100",
        "description": "座屈補剛ブレース・低降伏点鋼材・摩擦ダンパーなど履歴型制振装置",
        "section_nums": {1, 2, 3, 4, 5},
        "snap_keyword": "DSD",
    },
    "DIS": {
        "label": "免震支承材（積層ゴム等）（DIS）",
        "icon": "🏗️",
        "color": "#6A1B9A",
        "description": "積層ゴム支承（NRB/HDR/LRB）・すべり系免震装置",
        "section_nums": set(range(100, 200)),
        "snap_keyword": "DIS",
    },
}

SNAP_FIELD_NAMES: Dict[str, Dict[int, str]] = {
    "DVOD": {
        1:  "種別",
        2:  "k-DB 会社番号",
        3:  "k-DB 製品番号",
        4:  "k-DB 型番",
        5:  "減衰モデル",
        7:  "装置特性種別",
        8:  "C0（減衰係数）[kN·s/m]",
        9:  "Fc（リリーフ力）[kN]",
        10: "Fy（最大減衰力）[kN]",
        11: "Ve（基準速度）[m/s]",
        12: "α（速度指数）",
        13: "β（温度依存指数）",
        14: "剛性 [kN/m]",
        15: "取付け剛性 [kN/m]",
        16: "装置高さ [m]",
    },
    "DISD": {
        1:  "種別",
        2:  "k-DB 会社番号",
        3:  "k-DB 製品番号",
        4:  "k-DB 型番",
        5:  "復元力特性種別",
        6:  "K0（初期剛性）[kN/m]",
        7:  "Qc [kN]",
        8:  "Qy（降伏荷重）[kN]",
        9:  "α（2次剛性比）",
        10: "β",
        11: "p1",
        12: "p2",
    },
    "DVD": {
        1:  "種別",
        2:  "k-DB 会社番号",
        3:  "k-DB 製品番号",
        4:  "k-DB 型番",
        5:  "種別（装置形式）",
        6:  "質量 [t]",
        7:  "せん断断面積 [mm²]",
        8:  "せん断間隔 [mm]",
        9:  "振動数",
        10: "荷重 [kN]",
    },
    "DVED": {
        1:  "種別",
        2:  "k-DB 会社番号",
        3:  "k-DB 製品番号",
        4:  "k-DB 型番",
        5:  "種別（装置形式）",
        6:  "粘弾性体面積 [mm²]",
        7:  "粘弾性体厚さ [mm]",
        8:  "振動数",
        9:  "すべり荷重 [kN]",
        10: "取付け剛性 [kN/m]",
        11: "最大ひずみ",
        12: "装置高さ [m]",
    },
    "DSD": {
        1:  "種別",
        2:  "k-DB 会社番号",
        3:  "k-DB 製品番号",
        4:  "k-DB 型番",
        5:  "剛域の変形",
        6:  "復元力特性種別",
        7:  "K0（初期剛性）[kN/m]",
        9:  "Fy（降伏荷重）[kN]",
        11: "α（2次剛性比）",
        22: "装置高さ [m]",
    },
    "DIS": {
        1:  "種別",
        2:  "k-DB 会社番号",
        3:  "k-DB 製品番号",
        4:  "k-DB 型番",
        8:  "Kh0（水平初期剛性）[kN/m]",
        16: "高減衰ゴム系 復元力特性",
        17: "Ke/Keq",
        18: "Ke",
        25: "減衰（鉛直）",
        26: "減衰（水平）",
    },
}


class KdbBrowserDialog(QDialog):
    """
    k-DB ブラウザダイアログ。

    Signals
    -------
    paramsSelected : (snap_keyword: str, snap_fields: dict)
        ユーザーが「適用」を押したときに発行されます。
        snap_fields は {フィールド番号(int): 値} の辞書です。
    """

    paramsSelected = Signal(str, dict)  # (snap_keyword, snap_fields)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        kdb_dir: str = r"C:\Program Files (x86)\k-DB",
        user_dir: Optional[str] = None,
        filter_keyword: Optional[str] = None,  # "DVOD" or "DSD" or None
    ) -> None:
        super().__init__(parent)
        self._kdb_dir = kdb_dir
        self._user_dir = user_dir
        self._reader: Optional[KdbReader] = None
        self._filter_keyword = filter_keyword
        self._selected_product: Optional[KdbProduct] = None
        self._selected_record: Optional[KdbRecord] = None
        self._all_pairs: List[Tuple[KdbProduct, KdbRecord]] = []
        # 適用結果を保持（exec() 後に取得可能）
        self._applied_snap_keyword: Optional[str] = None
        self._applied_snap_fields: Optional[Dict[int, Any]] = None

        title = "k-DB 部材データベース ブラウザ"
        if filter_keyword:
            title += f"  [{filter_keyword}]"
        self.setWindowTitle(title)
        self.setMinimumSize(1000, 650)
        self._setup_ui()
        QTimer.singleShot(100, self._load_kdb)

    # ------------------------------------------------------------------
    # Public getters（exec() 後に結果取得）
    # ------------------------------------------------------------------

    def applied_snap_keyword(self) -> Optional[str]:
        """適用ボタン押下時に選択されていた SNAP キーワードを返します。"""
        return self._applied_snap_keyword

    def applied_snap_fields(self) -> Optional[Dict[int, Any]]:
        """適用ボタン押下時に選択されていた SNAP フィールド辞書を返します。"""
        return self._applied_snap_fields

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)

        # ─── フィルタバナー（フィルタ有効時のみ表示） ──────────────────
        if self._filter_keyword:
            filter_banner = QFrame()
            filter_banner.setStyleSheet(
                "QFrame { background-color: #E3F2FD; border: 1px solid #90CAF9;"
                " border-radius: 4px; padding: 4px 8px; }"
            )
            fb_layout = QHBoxLayout(filter_banner)
            fb_layout.setContentsMargins(8, 4, 8, 4)
            fb_label = QLabel(
                f"🔍 <b>{self._filter_keyword}</b> タイプのダンパーのみ表示中"
            )
            fb_label.setStyleSheet("color: #1565C0; font-size: 12px;")
            fb_layout.addWidget(fb_label)
            fb_layout.addStretch()
            main_layout.addWidget(filter_banner)

        # ─── 上部ツールバー ───────────────────────────────────────────
        toolbar = self._build_toolbar()
        main_layout.addLayout(toolbar)

        # ─── 分割ペイン ────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        # 左: カテゴリツリー
        left_panel = self._build_left_panel()
        splitter.addWidget(left_panel)

        # 中央: 製品テーブル
        center_panel = self._build_center_panel()
        splitter.addWidget(center_panel)

        # 右: 詳細パネル
        right_panel = self._build_right_panel()
        splitter.addWidget(right_panel)

        splitter.setSizes([220, 440, 320])

        # ─── ボタンバー ────────────────────────────────────────────────
        btn_bar = QHBoxLayout()
        self._status_label = QLabel("k-DB を読み込み中...")
        btn_bar.addWidget(self._status_label)
        btn_bar.addStretch()

        self._apply_btn = QPushButton("✅ 適用してパラメータを設定")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #1565C0; color: white; font-weight: bold;"
            " padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        btn_bar.addWidget(self._apply_btn)

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_bar.addWidget(close_btn)

        main_layout.addLayout(btn_bar)

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        # 検索ボックス
        bar.addWidget(QLabel("🔍"))
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("型番・シリーズ名で検索...")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._on_search)
        bar.addWidget(self._search_box, stretch=1)

        bar.addSpacing(12)

        # k-DB パス設定
        self._path_label = QLabel(f"k-DB: {self._kdb_dir}")
        self._path_label.setStyleSheet("color: #555; font-size: 11px;")
        bar.addWidget(self._path_label)

        change_btn = QPushButton("📂 パス変更")
        change_btn.clicked.connect(self._on_change_path)
        bar.addWidget(change_btn)

        return bar

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 4, 0)

        layout.addWidget(QLabel("<b>カテゴリ</b>"))

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(200)
        self._tree.currentItemChanged.connect(self._on_tree_selection)
        layout.addWidget(self._tree)

        return w

    def _build_center_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 0, 4, 0)

        self._table_label = QLabel("<b>製品一覧</b>")
        layout.addWidget(self._table_label)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["型番", "シリーズ", "C1/Fy", "α/β"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.selectionModel().selectionChanged.connect(self._on_table_selection)
        self._table.doubleClicked.connect(self._on_apply)
        layout.addWidget(self._table)

        return w

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 0, 0, 0)

        layout.addWidget(QLabel("<b>SNAP パラメータ</b>"))

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setMinimumWidth(280)
        layout.addWidget(self._detail_text)

        return w

    # ------------------------------------------------------------------
    # k-DB 読み込み
    # ------------------------------------------------------------------

    def _load_kdb(self) -> None:
        self._status_label.setText("⏳ k-DB を読み込み中...")
        try:
            self._reader = KdbReader(self._kdb_dir, self._user_dir)
            self._reader.load()
            self._all_pairs = self._reader.all_records_flat()
            self._status_label.setText(
                f"✅ {len(self._reader.products)} シリーズ / "
                f"{len(self._all_pairs)} 製品 読み込み完了"
            )
            self._populate_tree()
        except Exception as e:
            self._status_label.setText(f"❌ 読み込みエラー: {e}")
            QMessageBox.warning(
                self,
                "k-DB 読み込みエラー",
                f"k-DB の読み込みに失敗しました。\n\nパスを確認してください:\n{self._kdb_dir}\n\nエラー: {e}",
            )

    # ------------------------------------------------------------------
    # ツリー・テーブル構築
    # ------------------------------------------------------------------

    def _populate_tree(self) -> None:
        self._tree.clear()
        if not self._reader:
            return

        # 「すべて」ノード（フィルタ無効時のみ表示）
        if not self._filter_keyword:
            all_item = QTreeWidgetItem(["📋 すべて表示"])
            all_item.setData(0, Qt.UserRole, "ALL")
            self._tree.addTopLevelItem(all_item)

        # カテゴリごとにグループ化
        for cat_key, cat_info in CATEGORY_DISPLAY.items():
            section_set = cat_info["section_nums"]
            # フィルタが指定されている場合は snap_keyword で絞り込む
            if self._filter_keyword:
                cat_snap_kw = cat_info.get("snap_keyword", "")
                if cat_snap_kw != self._filter_keyword:
                    continue

            # このカテゴリに属する製品
            prods_in_cat = [
                p for p in self._reader.products
                if p.section_num in section_set
            ]
            if not prods_in_cat:
                continue

            # カテゴリトップレベルノード
            cat_label = f"{cat_info['icon']} {cat_info['label']}  ({sum(len(p.records) for p in prods_in_cat)})"
            cat_item = QTreeWidgetItem([cat_label])
            cat_item.setData(0, Qt.UserRole, ("CAT", cat_key))
            cat_item.setForeground(0, QColor(cat_info["color"]))
            font = QFont()
            font.setBold(True)
            cat_item.setFont(0, font)
            self._tree.addTopLevelItem(cat_item)

            # シリーズごとのサブノード
            for prod in prods_in_cat:
                series_label = f"  {prod.series_name}  ({len(prod.records)})"
                prod_item = QTreeWidgetItem([series_label])
                prod_item.setData(0, Qt.UserRole, ("SERIES", prod))
                cat_item.addChild(prod_item)

        if self._tree.topLevelItemCount() > 0:
            self._tree.topLevelItem(0).setSelected(True)
        self._show_all_records()

    def _show_all_records(self) -> None:
        """全レコードをテーブルに表示します（フィルタ有効時は絞り込み）。"""
        if self._filter_keyword:
            pairs = [(p, r) for p, r in self._all_pairs
                     if p.snap_keyword == self._filter_keyword]
        else:
            pairs = self._all_pairs
        self._populate_table(pairs)
        self._table_label.setText(f"<b>製品一覧</b>  {len(pairs)} 件")

    def _populate_table(self, pairs: List[Tuple[KdbProduct, KdbRecord]]) -> None:
        """指定したレコードペアをテーブルに表示します。"""
        self._table.setRowCount(0)
        self._table.setRowCount(len(pairs))

        for row, (prod, rec) in enumerate(pairs):
            # 型番
            num_item = QTableWidgetItem(rec.model_number)
            num_item.setData(Qt.UserRole, (prod, rec))
            self._table.setItem(row, 0, num_item)

            # シリーズ名（省略）
            series = prod.series_name[:25]
            self._table.setItem(row, 1, QTableWidgetItem(series))

            # C1/Fy
            c1 = rec.snap_fields.get(8)   # C0
            fy = rec.snap_fields.get(9)   # Fc/Fy
            if c1 is not None:
                val_str = f"C={c1:.0f}"
                if fy:
                    val_str += f"  Fy={fy:.0f}"
            elif fy is not None:
                val_str = f"Fy={fy:.0f}"
            else:
                val_str = "—"
            self._table.setItem(row, 2, QTableWidgetItem(val_str))

            # α/β
            alpha = rec.snap_fields.get(12)
            if alpha is not None:
                self._table.setItem(row, 3, QTableWidgetItem(f"{alpha:.4f}"))
            else:
                self._table.setItem(row, 3, QTableWidgetItem("—"))

        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(2)
        self._table.resizeColumnToContents(3)

    # ------------------------------------------------------------------
    # イベントハンドラー
    # ------------------------------------------------------------------

    def _on_tree_selection(self, current: QTreeWidgetItem, previous) -> None:
        if not current:
            return
        data = current.data(0, Qt.UserRole)

        if data == "ALL":
            self._show_all_records()
        elif isinstance(data, tuple) and data[0] == "CAT":
            cat_key = data[1]
            section_set = CATEGORY_DISPLAY[cat_key]["section_nums"]
            pairs = [
                (p, r)
                for p, r in self._all_pairs
                if p.section_num in section_set
            ]
            self._populate_table(pairs)
            self._table_label.setText(f"<b>製品一覧</b>  {len(pairs)} 件")
        elif isinstance(data, tuple) and data[0] == "SERIES":
            prod: KdbProduct = data[1]
            pairs = [(prod, r) for r in prod.records]
            self._populate_table(pairs)
            self._table_label.setText(
                f"<b>{prod.series_name}</b>  {len(pairs)} 件"
            )

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._selected_record = None
            self._selected_product = None
            self._apply_btn.setEnabled(False)
            return

        item = self._table.item(rows[0].row(), 0)
        if not item:
            return
        prod, rec = item.data(Qt.UserRole)
        self._selected_product = prod
        self._selected_record = rec
        self._apply_btn.setEnabled(True)
        self._update_detail_view(prod, rec)

    def _on_search(self, query: str) -> None:
        if not self._reader:
            return
        q = query.strip()
        if not q:
            self._show_all_records()
            return
        results = self._reader.search(q)
        # フィルタが有効な場合は snap_keyword で絞り込む
        if self._filter_keyword:
            results = [(p, r) for p, r in results if p.snap_keyword == self._filter_keyword]
        self._populate_table(results)
        self._table_label.setText(f"<b>検索結果</b>  {len(results)} 件")

    def _on_apply(self) -> None:
        if not self._selected_record or not self._selected_product:
            return
        snap_kw = self._selected_product.snap_keyword
        snap_fields = dict(self._selected_record.snap_fields)
        # 結果を保持（exec() 後に getter で取得可能）
        self._applied_snap_keyword = snap_kw
        self._applied_snap_fields = snap_fields
        # 後方互換: シグナルも発行
        self.paramsSelected.emit(snap_kw, snap_fields)
        self.accept()

    def _on_change_path(self) -> None:
        new_dir = QFileDialog.getExistingDirectory(
            self,
            "k-DB インストールフォルダを選択",
            self._kdb_dir,
        )
        if new_dir:
            self._kdb_dir = new_dir
            self._path_label.setText(f"k-DB: {new_dir}")
            self._reader = None
            self._all_pairs = []
            self._table.setRowCount(0)
            self._tree.clear()
            self._load_kdb()

    # ------------------------------------------------------------------
    # 詳細パネル更新
    # ------------------------------------------------------------------

    def _update_detail_view(self, prod: KdbProduct, rec: KdbRecord) -> None:
        html_parts = []

        # ヘッダー
        html_parts.append(
            f"<h3 style='color:#1565C0; margin:0'>{rec.model_number}</h3>"
        )
        if rec.model_name and rec.model_name != rec.model_number:
            html_parts.append(f"<p style='color:#555; margin:2px 0'>{rec.model_name}</p>")

        html_parts.append(f"<p style='color:#333; margin:4px 0'><b>シリーズ:</b> {prod.series_name}</p>")
        html_parts.append(f"<p style='color:#333; margin:2px 0'><b>カテゴリ:</b> {prod.category_label}</p>")
        if prod.certification_num:
            html_parts.append(
                f"<p style='color:#555; margin:2px 0'><b>認定番号:</b> {prod.certification_num}</p>"
            )

        html_parts.append("<hr style='margin:8px 0'>")

        # SNAP パラメータ表
        snap_kw = prod.snap_keyword or "DVOD"
        field_names = SNAP_FIELD_NAMES.get(snap_kw, {})

        html_parts.append(f"<p><b>SNAP パラメータ ({snap_kw})</b></p>")
        html_parts.append("<table style='width:100%; font-size:12px; border-collapse:collapse'>")
        html_parts.append(
            "<tr style='background:#e3f2fd'>"
            "<th style='padding:2px 4px; text-align:left'>フィールド</th>"
            "<th style='padding:2px 4px; text-align:right'>値</th>"
            "</tr>"
        )

        for fnum, fname in sorted(field_names.items()):
            val = rec.snap_fields.get(fnum)
            if val is None:
                continue
            if isinstance(val, float):
                val_str = f"{val:,.3f}" if abs(val) < 1e6 else f"{val:.3e}"
            else:
                val_str = str(val)
            bg = "#f5f5f5" if fnum % 2 == 0 else "white"
            html_parts.append(
                f"<tr style='background:{bg}'>"
                f"<td style='padding:2px 4px'>[{fnum}] {fname}</td>"
                f"<td style='padding:2px 4px; text-align:right; font-weight:bold'>{val_str}</td>"
                f"</tr>"
            )
        html_parts.append("</table>")

        # 補足情報
        if rec.extra:
            html_parts.append("<hr style='margin:8px 0'>")
            html_parts.append("<p><b>製品仕様</b></p>")
            html_parts.append("<table style='width:100%; font-size:11px; border-collapse:collapse'>")
            extra_labels = {
                "device_length_mm": "装置長 [mm]",
                "C1_kN_per_mm_per_s": "C1 [kN/(mm/s)]",
                "K1_kN_per_mm": "K1 [kN/mm]",
                "Kd_kN_per_mm": "Kd [kN/mm]",
                "Fy_kN": "Fy [kN]",
                "E_kN_per_mm2": "E [kN/mm²]",
                "outer_diameter_mm": "外径 [mm]",
                "area_mm2": "断面積 [mm²]",
                "rubber_height_mm": "ゴム総厚 [mm]",
                "Kh0_kN_per_mm": "Kh0 [kN/mm]",
            }
            for key, label in extra_labels.items():
                val = rec.extra.get(key)
                if val is None:
                    continue
                if isinstance(val, float):
                    val_str = f"{val:,.4g}"
                else:
                    val_str = str(val)
                html_parts.append(
                    f"<tr><td style='padding:1px 4px; color:#555'>{label}</td>"
                    f"<td style='padding:1px 4px; text-align:right'>{val_str}</td></tr>"
                )
            html_parts.append("</table>")

        self._detail_text.setHtml("".join(html_parts))
