"""
app/ui/case_edit_dialog.py
解析ケース設定ダイアログ

改善内容:
  [バグ修正]
  - RD「基数（倍数）」を正しいフィールド(SNAP仕様 index=10)から読み書きするよう修正。
    旧実装は index=3（種別）を誤って基数として読んでいたため、変更が反映されなかった。

  [UX改善: タブ構成]
  - 配置計画タブ: SpinBox(基数) と ComboBox(装置定義) を直接テーブルセルに埋め込み、
    誤入力を防止し操作を直感的に。
  - 配置計画タブ: 行を選択すると下部パネルに紐づくダンパー定義パラメータを即時表示。
  - ダンパー定義タブ: 元の値 vs 変更後を並べて表示し、変更セルを色付き強調。
  - 変更サマリーバナーを上部に常時表示（変更がある場合のみ）。

  [UX改善: 変更バッジ]
  - 各タブに変更数バッジを表示。変更のあるタブが一目でわかります。
  - ウィンドウタイトルバーにも「*」プレフィックスと変更件数を表示します。

  [UX改善①: スマートタブフォーカス]
  - ダイアログを開いたとき、変更内容に応じて最適なタブを自動選択します。
    - ダンパー定義に変更あり → 「🔧 ダンパー定義」タブを開く
    - 配置計画に変更あり   → 「📐 配置計画」タブを開く
    - 変更なし（新規作成） → 「⚙ 基本設定」タブを開く（ケース名入力を促す）
  - 外部から initial_tab を指定することで任意のタブを最初に表示できます。

  [UX改善②: 変更バナーの詳細化]
  - 変更バナーに具体的なパラメータ名と変更前後の値を表示します。
    例: 「🔧 Ce: 500.0 → 600.0 / 📐 RD-1: 基数 1 → 2」

  [UX改善⑤: 変更なし保存の確認]
  - 新規ケースをデフォルトのまま（パラメータ変更なし）で保存しようとした場合、
    確認ダイアログを表示してユーザーに意図の確認を促します。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QTabWidget,
    QWidget,
)

from app.models import AnalysisCase
from app.models.s8i_parser import (
    S8iModel,
    DamperDefinition,
    DamperElement,
    _RD_DAMPER_TYPE_LABELS,
    _RD_QUANTITY_IDX,
    _RD_DEF_NAME_IDX,
)


# ─────────────────────────────────────────────
#  定数
# ─────────────────────────────────────────────

# RD テーブル列定義
_RD_COL_NO    = 0   # #
_RD_COL_NAME  = 1   # 名称
_RD_COL_NODES = 2   # 節点I → J
_RD_COL_TYPE  = 3   # ダンパー種別（種別ラベル）
_RD_COL_DEF   = 4   # 装置定義 (ComboBox)
_RD_COL_QTY   = 5   # 基数/倍数 (SpinBox)
_RD_COL_MARK  = 6   # 変更マーク

# ダンパー定義テーブル列
_DEF_COL_IDX    = 0  # フィールド #
_DEF_COL_LABEL  = 1  # 項目名
_DEF_COL_ORIG   = 2  # 元の値
_DEF_COL_VALUE  = 3  # 現在の値（編集可）
_DEF_COL_UNIT   = 4  # 単位/説明

# 変更マーク色
_COLOR_CHANGED   = QColor("#fffde7")  # 淡い黄色
_COLOR_OK        = QColor("#e8f5e9")  # 淡い緑
_COLOR_UNCHANGED = QColor("transparent")


class CaseEditDialog(QDialog):
    """
    解析ケース設定編集ダイアログ（リニューアル版）。

    Parameters
    ----------
    case : AnalysisCase
        編集するケース。
    s8i_model : S8iModel or None
        パース済みの .s8i モデル（ダンパー情報表示用）。
    parent : QWidget, optional
    """

    def __init__(
        self,
        case: AnalysisCase,
        s8i_model: Optional[S8iModel] = None,
        existing_names: Optional[set] = None,
        initial_tab: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Parameters
        ----------
        case : AnalysisCase
            編集するケース。
        s8i_model : S8iModel or None
            パース済みの .s8i モデル（ダンパー情報表示用）。
        existing_names : set or None
            既存ケース名セット（重複しない名前提案に使用）。
        initial_tab : int or None
            開いた直後に表示するタブのインデックス (0〜3)。
            None の場合は _auto_focus_tab() で自動決定します。
        parent : QWidget, optional
        """
        super().__init__(parent)
        self._case = case
        self._s8i = s8i_model
        # UX改善②: 既存ケース名セット（重複しない名前提案に使用）
        self._existing_names: set = existing_names or set()
        # UX改善①: 外部から指定されたタブインデックス（None なら自動）
        self._initial_tab: Optional[int] = initial_tab

        # 「基本設定」タブの変更バッジ用に元の値を記憶
        self._orig_name: str = case.name
        self._orig_output_dir: str = case.output_dir

        # SpinBox / ComboBox の参照を行インデックスで保持
        self._rd_qty_spins: List[QSpinBox] = []
        self._rd_def_combos: List[QComboBox] = []

        self.setWindowTitle(f"ケース設定 — {case.name}")
        self.setMinimumWidth(820)
        self.setMinimumHeight(620)
        self.resize(900, 680)

        self._setup_ui()
        self._load_from_case()
        # UX改善①: ロード完了後に最適なタブへ自動フォーカス
        self._auto_focus_tab()

    # ──────────────────────────────────────────
    # UI 構築
    # ──────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # ---- 変更サマリーバナー（初期は非表示） ----
        self._banner = QFrame()
        self._banner.setFrameShape(QFrame.StyledPanel)
        self._banner.setStyleSheet(
            "QFrame { background-color: #fff9c4; border: 1px solid #f9a825; border-radius: 4px; }"
        )
        banner_layout = QHBoxLayout(self._banner)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        self._banner_label = QLabel()
        self._banner_label.setStyleSheet("color: #e65100; font-weight: bold;")
        banner_layout.addWidget(QLabel("⚠"))
        banner_layout.addWidget(self._banner_label)
        banner_layout.addStretch()
        self._banner.hide()
        layout.addWidget(self._banner)

        # ---- タブ ----
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._make_basic_tab(),      "⚙ 基本設定")
        self._tabs.addTab(self._make_def_tab(),         "🔧 ダンパー定義")
        self._tabs.addTab(self._make_placement_tab(),  "📐 配置計画")
        self._tabs.addTab(self._make_memo_tab(),        "📝 メモ")

        # ---- ボタン ----
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


    # ────── Tab 1: 基本設定 ──────────────────

    def _make_basic_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例: Case-01_Ce500_alpha04")
        # UX改善⑤: ケース名変更時にタイトルバーをリアルタイム更新
        self._name_edit.textChanged.connect(self._on_name_edit_changed)
        # UX改善（新）: ケース名変更時にタブバッジも更新
        self._name_edit.textChanged.connect(lambda _: self._update_banner())
        form.addRow("ケース名:", self._name_edit)

        out_row = QHBoxLayout()
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("（省略時はモデルファイルと同じディレクトリ）")
        btn_browse = QPushButton("参照…")
        btn_browse.setFixedWidth(64)
        btn_browse.clicked.connect(self._browse_output_dir)
        out_row.addWidget(self._out_edit)
        out_row.addWidget(btn_browse)
        form.addRow("出力ディレクトリ:", out_row)
        # UX改善（新）: 出力ディレクトリ変更時にタブバッジも更新
        self._out_edit.textChanged.connect(lambda _: self._update_banner())

        # モデル情報表示（読み取り専用）
        if self._s8i:
            info_box = QGroupBox("モデル情報（読み取り専用）")
            info_form = QFormLayout(info_box)
            info_form.addRow("ファイル:", QLabel(_short_path(self._s8i.file_path)))
            info_form.addRow("タイトル:",  QLabel(self._s8i.title or "（なし）"))
            info_form.addRow("節点数:",    QLabel(str(self._s8i.num_nodes)))
            info_form.addRow("ダンパー配置数:", QLabel(str(self._s8i.num_dampers)))
            info_form.addRow("総基数:",    QLabel(str(self._s8i.total_damper_units)))
            form.addRow(info_box)

        return w

    # ────── Tab 2: ダンパー定義 ──────────────

    def _make_def_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)

        if not (self._s8i and self._s8i.damper_defs):
            outer_layout.addWidget(QLabel(
                "<i>.s8i ファイルにダンパー定義がありません。</i>"
            ))
            self._damper_def_tables: Dict[str, QTableWidget] = {}
            return outer

        outer_layout.addWidget(QLabel(
            "<small>各ダンパー定義 (DVOD/DSD 等) のパラメータを編集します。"
            "変更後の値は解析ケースごとに保存され、元の .s8i ファイルは変更されません。</small>"
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)

        self._damper_def_tables = {}
        for ddef in self._s8i.damper_defs:
            grp = QGroupBox()
            grp.setTitle(f"  {_type_badge(ddef.keyword)}  {ddef.display_label}")
            grp_layout = QVBoxLayout(grp)

            tbl = self._make_damper_def_table(ddef)
            self._damper_def_tables[ddef.name] = tbl
            grp_layout.addWidget(tbl)
            content_layout.addWidget(grp)

        content_layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        return outer

    def _make_damper_def_table(self, ddef: DamperDefinition) -> QTableWidget:
        """ダンパー定義の値を編集するテーブルを作成します。"""
        field_labels = _get_damper_field_labels(ddef.keyword)
        field_units  = _get_damper_field_units(ddef.keyword)

        vals = ddef.values
        num_fields = len(vals) - 1  # index 0 は名前

        tbl = QTableWidget(num_fields, 5)
        tbl.setHorizontalHeaderLabels(["#", "項目名", "元の値", "現在の値（変更可）", "単位/説明"])
        tbl.horizontalHeader().setSectionResizeMode(_DEF_COL_IDX,    QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(_DEF_COL_LABEL,  QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(_DEF_COL_ORIG,   QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setSectionResizeMode(_DEF_COL_VALUE,  QHeaderView.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(_DEF_COL_UNIT,   QHeaderView.Stretch)
        tbl.verticalHeader().setVisible(False)
        tbl.setMaximumHeight(min(34 * num_fields + 30, 380))
        tbl.setAlternatingRowColors(True)

        for i, val in enumerate(vals[1:]):
            idx = i + 1  # 1-indexed field position
            orig_val = val

            # --- # ---
            no_item = QTableWidgetItem(str(idx))
            no_item.setFlags(no_item.flags() & ~Qt.ItemIsEditable)
            no_item.setTextAlignment(Qt.AlignCenter)
            tbl.setItem(i, _DEF_COL_IDX, no_item)

            # --- 項目名 ---
            label_text = field_labels.get(idx, "")
            label_item = QTableWidgetItem(label_text)
            label_item.setFlags(label_item.flags() & ~Qt.ItemIsEditable)
            tbl.setItem(i, _DEF_COL_LABEL, label_item)

            # --- 元の値 ---
            orig_item = QTableWidgetItem(orig_val)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
            orig_item.setForeground(QColor("#888888"))
            tbl.setItem(i, _DEF_COL_ORIG, orig_item)

            # --- 現在の値（編集可）---
            val_item = QTableWidgetItem(orig_val)
            tbl.setItem(i, _DEF_COL_VALUE, val_item)

            # --- 単位 ---
            unit_item = QTableWidgetItem(field_units.get(idx, ""))
            unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
            unit_item.setForeground(QColor("#666666"))
            tbl.setItem(i, _DEF_COL_UNIT, unit_item)

        # 変更時に行を色付き強調するシグナル
        tbl.itemChanged.connect(lambda item: self._on_def_item_changed(tbl, item))
        return tbl

    def _on_def_item_changed(self, tbl: QTableWidget, item: QTableWidgetItem) -> None:
        """ダンパー定義値の変更時に行を強調します。"""
        if item.column() != _DEF_COL_VALUE:
            return
        row = item.row()
        orig_item = tbl.item(row, _DEF_COL_ORIG)
        if orig_item:
            changed = item.text().strip() != orig_item.text().strip()
            bg = _COLOR_CHANGED if changed else _COLOR_UNCHANGED
            for col in range(tbl.columnCount()):
                ci = tbl.item(row, col)
                if ci:
                    ci.setBackground(bg)
        self._update_banner()

    # ────── Tab 3: 配置計画 ──────────────────

    def _make_placement_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        # 説明
        desc = QLabel(
            "<small><b>免制振装置 (RD)</b> の配置と基数を設定します。<br>"
            "「<b>基数(倍数)</b>」は同じ位置に設置するダンパーの本数です。<br>"
            "「<b>装置定義</b>」を変更すると下部パネルにそのダンパーのパラメータが表示されます。</small>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # スプリッター（上: テーブル / 下: ディテールパネル）
        splitter = QSplitter(Qt.Vertical)

        # ---- RD テーブル ----
        self._rd_table = QTableWidget()
        self._rd_table.setColumnCount(7)
        self._rd_table.setHorizontalHeaderLabels([
            "#", "名称", "節点I → J（位置）",
            "ダンパー種別", "装置定義", "基数（倍数）", "変更"
        ])
        hdr = self._rd_table.horizontalHeader()
        hdr.setSectionResizeMode(_RD_COL_NO,    QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(_RD_COL_NAME,  QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(_RD_COL_NODES, QHeaderView.Stretch)
        hdr.setSectionResizeMode(_RD_COL_TYPE,  QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(_RD_COL_DEF,   QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(_RD_COL_QTY,   QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(_RD_COL_MARK,  QHeaderView.ResizeToContents)
        self._rd_table.verticalHeader().setVisible(False)
        self._rd_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rd_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._rd_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._rd_table.setAlternatingRowColors(True)
        self._rd_table.itemSelectionChanged.connect(self._on_rd_row_selected)
        self._rd_table.setMinimumHeight(180)

        splitter.addWidget(self._rd_table)

        # ---- ダンパー定義ディテールパネル ----
        self._detail_panel = self._make_detail_panel()
        splitter.addWidget(self._detail_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # テーブルを populate
        self._populate_rd_table()

        return w

    def _make_detail_panel(self) -> QGroupBox:
        grp = QGroupBox("選択中のダンパー定義パラメータ")
        layout = QVBoxLayout(grp)

        self._detail_hint = QLabel(
            "↑ RD 行を選択すると、紐づくダンパー定義のパラメータがここに表示されます。"
        )
        self._detail_hint.setStyleSheet("color: gray;")
        self._detail_hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._detail_hint)

        self._detail_content = QWidget()
        self._detail_content.hide()
        self._detail_content_layout = QVBoxLayout(self._detail_content)
        self._detail_content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._detail_content)

        return grp

    def _populate_rd_table(self) -> None:
        """RD テーブルを s8i モデルのデータで初期化します。"""
        self._rd_qty_spins.clear()
        self._rd_def_combos.clear()
        self._rd_table.setRowCount(0)

        if not (self._s8i and self._s8i.damper_elements):
            self._rd_table.setRowCount(1)
            item = QTableWidgetItem("（.s8i に RD 定義がありません）")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setForeground(QColor("gray"))
            self._rd_table.setItem(0, _RD_COL_NAME, item)
            return

        # 利用可能なダンパー定義名リスト（ComboBox 用）
        def_names = [d.name for d in self._s8i.damper_defs] if self._s8i else []

        for row, elem in enumerate(self._s8i.damper_elements):
            self._rd_table.insertRow(row)

            # --- # ---
            no_item = QTableWidgetItem(str(row + 1))
            no_item.setFlags(no_item.flags() & ~Qt.ItemIsEditable)
            no_item.setTextAlignment(Qt.AlignCenter)
            self._rd_table.setItem(row, _RD_COL_NO, no_item)

            # --- 名称 ---
            name_item = QTableWidgetItem(elem.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self._rd_table.setItem(row, _RD_COL_NAME, name_item)

            # --- 節点 I → J + 座標 ---
            node_info = self._node_location_text(elem.node_i, elem.node_j)
            node_item = QTableWidgetItem(node_info)
            node_item.setFlags(node_item.flags() & ~Qt.ItemIsEditable)
            node_item.setToolTip(self._node_coords_tooltip(elem.node_i, elem.node_j))
            self._rd_table.setItem(row, _RD_COL_NODES, node_item)

            # --- ダンパー種別 ---
            type_item = QTableWidgetItem(elem.damper_type_label)
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            type_item.setForeground(QColor("#1565c0"))
            self._rd_table.setItem(row, _RD_COL_TYPE, type_item)

            # --- 装置定義 (ComboBox) ---
            combo = QComboBox()
            combo.addItems(def_names)
            if elem.damper_def_name in def_names:
                combo.setCurrentText(elem.damper_def_name)
            elif def_names:
                combo.setCurrentIndex(0)
            combo.setProperty("_row", row)
            combo.currentTextChanged.connect(self._on_def_combo_changed)
            self._rd_def_combos.append(combo)
            self._rd_table.setCellWidget(row, _RD_COL_DEF, combo)

            # --- 基数（倍数）SpinBox ---
            spin = QSpinBox()
            spin.setMinimum(0)
            spin.setMaximum(999)
            spin.setValue(elem.quantity)  # 正しい index=10 から読んだ値
            spin.setToolTip(
                f"この位置に設置するダンパーの本数（倍数）。\n"
                f"元の値: {elem.quantity}"
            )
            spin.setProperty("_row", row)
            spin.valueChanged.connect(self._on_qty_spin_changed)
            self._rd_qty_spins.append(spin)
            self._rd_table.setCellWidget(row, _RD_COL_QTY, spin)

            # --- 変更マーク ---
            mark_item = QTableWidgetItem("")
            mark_item.setFlags(mark_item.flags() & ~Qt.ItemIsEditable)
            mark_item.setTextAlignment(Qt.AlignCenter)
            self._rd_table.setItem(row, _RD_COL_MARK, mark_item)

        self._rd_table.resizeRowsToContents()

    # ────── Tab 4: メモ ──────────────────────

    def _make_memo_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText(
            "このケースに関するメモを入力してください…\n"
            "（例: Ce=500, α=0.4, 2Fと3Fのみ設置）"
        )
        layout.addWidget(self._notes_edit)
        return w

    # ──────────────────────────────────────────
    # ロード / 保存
    # ──────────────────────────────────────────

    def _load_from_case(self) -> None:
        """ケースの保存済み設定をUIに反映します。"""
        c = self._case
        self._name_edit.setText(c.name)
        self._out_edit.setText(c.output_dir)
        self._notes_edit.setPlainText(c.notes)

        # ---- UX改善（スマートデフォルト）: 新規ケース時は名前・出力先を自動補完 ----
        # 「新規ケース」というデフォルト名のままの場合は、s8iファイル名を元に
        # 「{モデル名}_Case-01」のような具体的な名前を提案します。
        # 名前欄を全選択状態にするので、そのまま入力すれば上書きできます。
        if c.name == "新規ケース":
            suggested = self._suggest_default_case_name()
            self._name_edit.setText(suggested)
            self._name_edit.selectAll()
            self.setWindowTitle(f"ケース設定 — {suggested}")

        # 出力先ディレクトリは空欄のままにする
        # （解析サービスが「s8i親フォルダ / ケース名」を自動生成するため、ここで設定すると
        #   run_input == src となり shutil.SameFileError が発生する）

        # ---- ダンパー定義パラメータの変更値を反映 ----
        if c.damper_params and self._damper_def_tables:
            for def_name, overrides in c.damper_params.items():
                tbl = self._damper_def_tables.get(def_name)
                if not tbl or not isinstance(overrides, dict):
                    continue
                for idx_str, val in overrides.items():
                    row = int(idx_str) - 1  # 1-indexed → 0-indexed
                    if 0 <= row < tbl.rowCount():
                        tbl.blockSignals(True)
                        tbl.setItem(row, _DEF_COL_VALUE, QTableWidgetItem(str(val)))
                        tbl.blockSignals(False)
                        # 変更色を適用
                        orig_item = tbl.item(row, _DEF_COL_ORIG)
                        if orig_item and str(val) != orig_item.text():
                            for col in range(tbl.columnCount()):
                                ci = tbl.item(row, col)
                                if ci:
                                    ci.setBackground(_COLOR_CHANGED)

        # ---- RD 配置・基数の変更値を反映 ----
        rd_changes = c.parameters.get("_rd_overrides", {})
        for idx_str, changes in rd_changes.items():
            row = int(idx_str)
            if row >= len(self._rd_qty_spins):
                continue
            if "quantity" in changes:
                spin = self._rd_qty_spins[row]
                spin.blockSignals(True)
                spin.setValue(int(changes["quantity"]))
                spin.blockSignals(False)
            if "damper_def_name" in changes:
                combo = self._rd_def_combos[row]
                combo.blockSignals(True)
                combo.setCurrentText(changes["damper_def_name"])
                combo.blockSignals(False)
            # マーク更新
            self._update_rd_row_mark(row)

        self._update_banner()

    def _suggest_default_case_name(self) -> str:
        """
        UX改善②（スマートデフォルト）: 新規ケース作成時に推奨ケース名を自動生成します。

        s8iファイル名が利用可能な場合は「{モデル名}_Case-01」形式を返します。
        s8i未読み込みの場合は「Case-01」を返します。

        UX改善②追加: _existing_names を参照して重複しない名前を返します。
        既に「Case-01」が存在する場合は「Case-02」、「Case-03」… と自動的に採番します。

        Returns
        -------
        str
            重複しない推奨ケース名。
        """
        if self._s8i and self._s8i.file_path:
            from pathlib import Path as _Path
            stem = _Path(self._s8i.file_path).stem
            # ファイル名が長すぎる場合は短縮
            if len(stem) > 20:
                stem = stem[:20]
            base = f"{stem}_Case"
        else:
            base = "Case"

        # UX改善②: 既存ケース名と重複しない連番を生成
        idx = 1
        while f"{base}-{idx:02d}" in self._existing_names:
            idx += 1
        return f"{base}-{idx:02d}"

    def _save_to_case(self) -> None:
        """UIの現在値をケースデータモデルに保存します。"""
        c = self._case
        c.name       = self._name_edit.text().strip() or "無名ケース"
        c.output_dir = self._out_edit.text().strip()
        c.notes      = self._notes_edit.toPlainText()

        # ---- ダンパー定義パラメータの変更を保存 ----
        damper_param_overrides: Dict[str, Dict[str, str]] = {}
        if self._s8i:
            for ddef in self._s8i.damper_defs:
                tbl = self._damper_def_tables.get(ddef.name)
                if not tbl:
                    continue
                overrides: Dict[str, str] = {}
                for row in range(tbl.rowCount()):
                    val_item  = tbl.item(row, _DEF_COL_VALUE)
                    orig_item = tbl.item(row, _DEF_COL_ORIG)
                    if val_item and orig_item:
                        new_val  = val_item.text().strip()
                        orig_val = orig_item.text().strip()
                        if new_val != orig_val:
                            overrides[str(row + 1)] = new_val  # 1-indexed
                if overrides:
                    damper_param_overrides[ddef.name] = overrides
        c.damper_params = damper_param_overrides

        # ---- RD 配置・基数・装置定義の変更を保存 ----
        rd_overrides: Dict[str, Dict[str, Any]] = {}
        if self._s8i:
            for row, elem in enumerate(self._s8i.damper_elements):
                changes: Dict[str, Any] = {}

                # 基数（倍数）
                if row < len(self._rd_qty_spins):
                    new_qty = self._rd_qty_spins[row].value()
                    if new_qty != elem.quantity:
                        changes["quantity"] = new_qty

                # 装置定義名
                if row < len(self._rd_def_combos):
                    new_def = self._rd_def_combos[row].currentText()
                    if new_def != elem.damper_def_name:
                        changes["damper_def_name"] = new_def

                if changes:
                    rd_overrides[str(row)] = changes

        if rd_overrides:
            c.parameters["_rd_overrides"] = rd_overrides
        elif "_rd_overrides" in c.parameters:
            del c.parameters["_rd_overrides"]

    # ──────────────────────────────────────────
    # シグナルハンドラ
    # ──────────────────────────────────────────

    def _on_qty_spin_changed(self, value: int) -> None:
        """SpinBox 変更時: 変更マークを更新します。"""
        spin = self.sender()
        row = spin.property("_row")
        if row is not None:
            self._update_rd_row_mark(row)
        self._update_banner()

    def _on_def_combo_changed(self, text: str) -> None:
        """ComboBox 変更時: 変更マークと下部パネルを更新します。"""
        combo = self.sender()
        row = combo.property("_row")
        if row is not None:
            self._update_rd_row_mark(row)
            # 選択中の行ならディテールパネルも更新
            selected_rows = self._rd_table.selectionModel().selectedRows()
            if selected_rows and selected_rows[0].row() == row:
                self._show_detail_for_def(text)
        self._update_banner()

    def _on_rd_row_selected(self) -> None:
        """RD 行選択時: 下部パネルに装置定義パラメータを表示します。"""
        selected = self._rd_table.selectionModel().selectedRows()
        if not selected:
            self._detail_hint.show()
            self._detail_content.hide()
            return
        row = selected[0].row()
        if row < len(self._rd_def_combos):
            def_name = self._rd_def_combos[row].currentText()
            self._show_detail_for_def(def_name)

    def _show_detail_for_def(self, def_name: str) -> None:
        """指定したダンパー定義のパラメータを下部パネルに表示します。"""
        # 古いコンテンツを削除
        while self._detail_content_layout.count():
            item = self._detail_content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not (self._s8i and def_name):
            self._detail_hint.show()
            self._detail_content.hide()
            self._detail_panel.setTitle("ダンパー定義パラメータ")
            return

        ddef = self._s8i.get_damper_def(def_name)
        if not ddef:
            self._detail_hint.setText(f"装置定義「{def_name}」が見つかりません。")
            self._detail_hint.show()
            self._detail_content.hide()
            self._detail_panel.setTitle("ダンパー定義パラメータ")
            return

        self._detail_panel.setTitle(
            f"装置定義パラメータ: {ddef.display_label}"
        )

        # ケースに保存済みの変更値を取得
        overrides = self._case.damper_params.get(def_name, {}) if self._case.damper_params else {}
        # 今テーブルで編集中の値も取得
        if def_name in self._damper_def_tables:
            tbl = self._damper_def_tables[def_name]
            for r in range(tbl.rowCount()):
                vi = tbl.item(r, _DEF_COL_VALUE)
                oi = tbl.item(r, _DEF_COL_ORIG)
                if vi and oi and vi.text() != oi.text():
                    overrides[str(r + 1)] = vi.text()

        field_labels = _get_damper_field_labels(ddef.keyword)
        field_units  = _get_damper_field_units(ddef.keyword)

        # コンパクトなグリッドで表示（2列）
        grid_widget = QWidget()
        from PySide6.QtWidgets import QGridLayout
        grid = QGridLayout(grid_widget)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(8, 4, 8, 4)

        col = 0
        grid_row = 0
        for i, orig_val in enumerate(ddef.values[1:]):
            idx = i + 1
            label_text = field_labels.get(idx, f"フィールド{idx}")
            current_val = overrides.get(str(idx), orig_val)
            unit_text   = field_units.get(idx, "")
            changed = current_val != orig_val

            lbl = QLabel(f"<b>{label_text}</b>:")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            val_txt = current_val
            if changed:
                val_lbl = QLabel(
                    f"<span style='color:#d84315;font-weight:bold;'>{val_txt}</span>"
                    f" <span style='color:#aaa;font-size:9pt;'>(元: {orig_val})</span>"
                )
            else:
                val_lbl = QLabel(val_txt)

            if unit_text:
                val_lbl.setText(val_lbl.text() + f"  <span style='color:#888;'>[{unit_text}]</span>")

            grid.addWidget(lbl,     grid_row, col * 2)
            grid.addWidget(val_lbl, grid_row, col * 2 + 1)

            col += 1
            if col >= 2:
                col = 0
                grid_row += 1

        self._detail_content_layout.addWidget(grid_widget)
        note = QLabel(
            "<small style='color:gray;'>「ダンパー定義」タブで値を変更できます。"
            "変更はオレンジ色で表示されます。</small>"
        )
        note.setWordWrap(True)
        self._detail_content_layout.addWidget(note)

        self._detail_hint.hide()
        self._detail_content.show()

    def _update_rd_row_mark(self, row: int) -> None:
        """RD テーブルの行に変更マークを反映します。"""
        if not (self._s8i and row < len(self._s8i.damper_elements)):
            return
        elem = self._s8i.damper_elements[row]

        changed = False
        if row < len(self._rd_qty_spins):
            changed = changed or (self._rd_qty_spins[row].value() != elem.quantity)
        if row < len(self._rd_def_combos):
            changed = changed or (self._rd_def_combos[row].currentText() != elem.damper_def_name)

        mark_item = self._rd_table.item(row, _RD_COL_MARK)
        if mark_item:
            mark_item.setText("✎ 変更" if changed else "")
            mark_item.setForeground(QColor("#d84315") if changed else QColor("gray"))

        # 行全体の背景色
        for col in (_RD_COL_NO, _RD_COL_NAME, _RD_COL_NODES, _RD_COL_TYPE, _RD_COL_MARK):
            ci = self._rd_table.item(row, col)
            if ci:
                ci.setBackground(_COLOR_CHANGED if changed else _COLOR_UNCHANGED)

    def _update_banner(self) -> None:
        """
        UX改善②: 変更サマリーバナーを詳細情報付きで更新します。

        変更件数だけでなく、最初の2〜3件の具体的なパラメータ名と変更前後の値を
        バナーに表示します。例: 「🔧 Ce: 500.0 → 600.0 / 📐 RD-1: 基数 1 → 2」
        変更が多い場合は「（+N件）」と省略します。

        タイトルバーにも「*（変更中）」プレフィックスを表示します。
        """
        # ─────────── ダンパー定義の変更を収集 ───────────
        def_detail_parts: List[str] = []
        def_changes = 0
        field_labels_cache: Dict[str, Any] = {}
        for ddef in (self._s8i.damper_defs if self._s8i else []):
            tbl = self._damper_def_tables.get(ddef.name)
            if not tbl:
                continue
            fl = _get_damper_field_labels(ddef.keyword)
            field_labels_cache[ddef.name] = fl
            for r in range(tbl.rowCount()):
                vi = tbl.item(r, _DEF_COL_VALUE)
                oi = tbl.item(r, _DEF_COL_ORIG)
                if vi and oi and vi.text().strip() != oi.text().strip():
                    def_changes += 1
                    if len(def_detail_parts) < 2:
                        field_idx = r + 1
                        label = fl.get(field_idx, f"F{field_idx}")
                        def_detail_parts.append(
                            f"<b>{label}</b>: {oi.text()} → {vi.text()}"
                        )

        # ─────────── 配置計画の変更を収集 ───────────
        rd_detail_parts: List[str] = []
        rd_changes = 0
        if self._s8i:
            for i, elem in enumerate(self._s8i.damper_elements):
                qty_changed = (
                    i < len(self._rd_qty_spins)
                    and self._rd_qty_spins[i].value() != elem.quantity
                )
                def_changed = (
                    i < len(self._rd_def_combos)
                    and self._rd_def_combos[i].currentText() != elem.damper_def_name
                )
                if qty_changed or def_changed:
                    rd_changes += 1
                    if len(rd_detail_parts) < 1:
                        parts_inner = []
                        if qty_changed:
                            parts_inner.append(
                                f"基数 {elem.quantity} → {self._rd_qty_spins[i].value()}"
                            )
                        if def_changed:
                            parts_inner.append(
                                f"定義 {elem.damper_def_name} → "
                                f"{self._rd_def_combos[i].currentText()}"
                            )
                        rd_detail_parts.append(
                            f"<b>{elem.name}</b>: {', '.join(parts_inner)}"
                        )

        total = def_changes + rd_changes
        if total > 0:
            # 詳細テキストを組み立て
            banner_parts: List[str] = []
            shown = 0
            for p in def_detail_parts:
                banner_parts.append(f"🔧 {p}")
                shown += 1
            for p in rd_detail_parts:
                banner_parts.append(f"📐 {p}")
                shown += 1

            remaining = total - shown
            summary = "  /  ".join(banner_parts)
            if remaining > 0:
                summary += f"  <span style='color:#888;'>（+{remaining}件の変更）</span>"

            self._banner_label.setText(summary)
            self._banner_label.setTextFormat(Qt.RichText)
            self._banner.show()
            # タイトルバーに「*（変更あり）」プレフィックスを表示
            self.setWindowTitle(
                f"* ケース設定 — {self._case.name}  [{total}項目変更中]"
            )
        else:
            self._banner.hide()
            # 変更なし → 通常タイトルに戻す
            self.setWindowTitle(f"ケース設定 — {self._case.name}")

        # UX改善（新）: 変更があるタブに ● バッジを付けて変更箇所を一目で把握できるようにする
        # タブインデックス: 0=基本設定, 1=ダンパー定義, 2=配置計画, 3=メモ

        # --- タブ0: 基本設定（ケース名・出力ディレクトリの変更を検出）---
        basic_changes = 0
        if hasattr(self, "_name_edit") and self._name_edit.text().strip() != self._orig_name:
            basic_changes += 1
        if hasattr(self, "_out_edit") and self._out_edit.text().strip() != self._orig_output_dir:
            basic_changes += 1
        self._tabs.setTabText(
            0,
            f"⚙ 基本設定  ●  ({basic_changes})" if basic_changes > 0 else "⚙ 基本設定"
        )

        # --- タブ1: ダンパー定義 ---
        self._tabs.setTabText(
            1,
            f"🔧 ダンパー定義  ●  ({def_changes})" if def_changes > 0 else "🔧 ダンパー定義"
        )
        # --- タブ2: 配置計画 ---
        self._tabs.setTabText(
            2,
            f"📐 配置計画  ●  ({rd_changes})" if rd_changes > 0 else "📐 配置計画"
        )

    def _on_name_edit_changed(self, text: str) -> None:
        """
        UX改善⑤: ケース名フィールド変更時にタイトルバーを即時更新します。

        ユーザーがケース名を入力中でも、タイトルバーに現在の入力内容を
        リアルタイム反映することで、どのケースを編集しているかが常に明確になります。
        """
        display_name = text.strip() or self._case.name
        # 変更バナーが表示されている（＝パラメータ変更あり）かに応じてタイトルを切り替える
        if self._banner.isVisible():
            self.setWindowTitle(f"* ケース設定 — {display_name}  [変更中]")
        else:
            self.setWindowTitle(f"ケース設定 — {display_name}")

    # ──────────────────────────────────────────
    # ヘルパー
    # ──────────────────────────────────────────

    def _node_location_text(self, node_i: int, node_j: int) -> str:
        """節点 I→J の表示テキストを作成します（グリッド/フロア情報付き）。"""
        if not self._s8i:
            return f"{node_i} → {node_j}"
        ni = self._s8i.get_node(node_i)
        nj = self._s8i.get_node(node_j)

        def _nd_label(n, nid: int) -> str:
            if n is None:
                return str(nid)
            parts = [str(nid)]
            if n.x_grid or n.y_grid:
                grid_str = "/".join(filter(None, [n.x_grid, n.y_grid]))
                if grid_str:
                    parts.append(f"({grid_str})")
            if n.z_grid:
                parts.append(n.z_grid)
            return " ".join(parts)

        return f"{_nd_label(ni, node_i)}  →  {_nd_label(nj, node_j)}"

    def _node_coords_tooltip(self, node_i: int, node_j: int) -> str:
        """ツールチップ用の節点座標文字列を返します。"""
        if not self._s8i:
            return ""
        lines = []
        for nid, label in ((node_i, "節点I"), (node_j, "節点J")):
            n = self._s8i.get_node(nid)
            if n:
                lines.append(f"{label} ({nid}): X={n.x:.2f}, Y={n.y:.2f}, Z={n.z:.2f}")
            else:
                lines.append(f"{label} ({nid}): 座標不明")
        return "\n".join(lines)

    def _browse_output_dir(self) -> None:
        """出力ディレクトリを参照ダイアログで選択します。"""
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(
            self, "出力ディレクトリを選択", self._out_edit.text()
        )
        if d:
            self._out_edit.setText(d)

    # ──────────────────────────────────────────
    # Accept
    # ──────────────────────────────────────────

    def _auto_focus_tab(self) -> None:
        """
        UX改善①: ダイアログ開封時に最適なタブを自動選択します。

        外部から initial_tab が指定された場合はそれを優先します。
        指定なしの場合は、ケースの変更内容に基づいて最適なタブを選択します:
          - ダンパー定義に変更あり → タブ1 (🔧 ダンパー定義)
          - 配置計画に変更あり    → タブ2 (📐 配置計画)
          - 変更なし（新規含む）  → タブ0 (⚙ 基本設定) でケース名入力を促す
        """
        if self._initial_tab is not None:
            # 外部指定を優先
            if 0 <= self._initial_tab < self._tabs.count():
                self._tabs.setCurrentIndex(self._initial_tab)
            return

        # 変更内容を検査して最適なタブを決定
        has_damper_def_changes = bool(self._case.damper_params)
        has_rd_changes = bool(
            self._case.parameters.get("_rd_overrides") if self._case.parameters else False
        )

        if has_damper_def_changes and not has_rd_changes:
            # ダンパー定義のみ変更 → ダンパー定義タブ
            self._tabs.setCurrentIndex(1)
        elif has_rd_changes and not has_damper_def_changes:
            # 配置計画のみ変更 → 配置計画タブ
            self._tabs.setCurrentIndex(2)
        elif has_damper_def_changes and has_rd_changes:
            # 両方変更 → より変更数の多いタブへ
            def_count = sum(
                len(v) for v in self._case.damper_params.values()
                if isinstance(v, dict)
            )
            rd_count = len(self._case.parameters.get("_rd_overrides", {}))
            self._tabs.setCurrentIndex(1 if def_count >= rd_count else 2)
        else:
            # 変更なし（新規ケース等）→ 基本設定タブでケース名入力を促す
            self._tabs.setCurrentIndex(0)
            # ケース名フィールドにフォーカスを移動
            if hasattr(self, "_name_edit"):
                self._name_edit.setFocus()

    def _on_accept(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._name_edit.setFocus()
            self._tabs.setCurrentIndex(0)
            return
        # UX改善⑤: 新規ケースかつパラメータ変更なしの場合に確認ダイアログを表示
        self._cancel_accept = False
        self._check_no_changes_before_save()
        if self._cancel_accept:
            return  # ユーザーが「パラメータを変更する」を選択
        self._save_to_case()
        self.accept()

    def _check_no_changes_before_save(self) -> None:
        """
        UX改善⑤: 新規ケースをパラメータ変更なしで保存しようとした場合に確認ダイアログを表示します。

        変更なしのまま保存すると「ベースモデルと全く同じケース」が作られてしまい、
        比較意味を持たないケースが増える原因になります。
        この確認ダイアログで「本当に変更なしで保存しますか？」をユーザーに確認します。

        ただし:
        - 既存ケースの編集（orig_name が「新規ケース」でない）は対象外
        - 変更がある場合は対象外
        """
        from PySide6.QtWidgets import QMessageBox as _QMB
        # 新規ケースかどうかをチェック（元の名前が自動生成パターンかどうか）
        is_new_case = (
            self._orig_name == "新規ケース"
            or self._orig_name.endswith("_Case-01")
            or "_Case-" in self._orig_name
        )
        has_changes = (
            bool(self._case.damper_params)
            or bool(self._case.parameters.get("_rd_overrides") if self._case.parameters else False)
        )
        # ダイアログで編集した変更も確認
        def_changes = sum(
            1
            for ddef in (self._s8i.damper_defs if self._s8i else [])
            for tbl in [self._damper_def_tables.get(ddef.name)]
            if tbl
            for r in range(tbl.rowCount())
            if (tbl.item(r, _DEF_COL_VALUE) and tbl.item(r, _DEF_COL_ORIG)
                and tbl.item(r, _DEF_COL_VALUE).text() != tbl.item(r, _DEF_COL_ORIG).text())
        )
        rd_changes = 0
        if self._s8i:
            for i, elem in enumerate(self._s8i.damper_elements):
                if i < len(self._rd_qty_spins) and self._rd_qty_spins[i].value() != elem.quantity:
                    rd_changes += 1
                elif (i < len(self._rd_def_combos)
                      and self._rd_def_combos[i].currentText() != elem.damper_def_name):
                    rd_changes += 1
        if has_changes or def_changes > 0 or rd_changes > 0:
            return  # 変更あり → 確認不要

        if not is_new_case:
            return  # 既存ケースの再保存 → 確認不要

        if not self._s8i:
            return  # s8i 未読み込みの場合は確認不要（比較対象がない）

        # 確認ダイアログを表示（親ウィンドウへのブロッキングではない）
        msg = _QMB(self)
        msg.setWindowTitle("パラメータ変更なしの確認")
        msg.setIcon(_QMB.Question)
        msg.setText(
            "このケースはベースモデルとパラメータが同じです。\n\n"
            "ダンパーの種別・パラメータ・配置基数を変更しないと、\n"
            "他のケースと比較する意味がなくなります。"
        )
        msg.setInformativeText(
            "変更なしのまま保存しますか？\n\n"
            "「パラメータを変更する」を押すと「🔧 ダンパー定義」タブに戻ります。"
        )
        btn_save = msg.addButton("このまま保存する", _QMB.AcceptRole)
        btn_edit = msg.addButton("🔧 パラメータを変更する", _QMB.RejectRole)
        msg.setDefaultButton(btn_edit)
        msg.exec()
        if msg.clickedButton() == btn_edit:
            # ダンパー定義タブに切り替えてキャンセル（保存しない）
            self._tabs.setCurrentIndex(1 if self._s8i.damper_defs else 2)
            # _on_accept からの呼び出しを中断するために例外を使わず
            # accept() 前に return させるためフラグをセット
            self._cancel_accept = True


# ─────────────────────────────────────────────
#  ユーティリティ関数
# ─────────────────────────────────────────────

def _short_path(path: str) -> str:
    """長いパスを短縮して返します。"""
    import os
    try:
        parts = path.replace("\\", "/").split("/")
        return "/".join(parts[-3:]) if len(parts) > 3 else path
    except Exception:
        return path


def _type_badge(keyword: str) -> str:
    """キーワードに対応する絵文字バッジを返します。"""
    badges = {
        "DVOD": "💧",
        "DSD":  "🔩",
        "DVHY": "🔄",
        "DVBI": "📐",
        "DVSL": "🔁",
        "DVFR": "🔧",
        "DVTF": "🌀",
        "DVMS": "⚖",
    }
    return badges.get(keyword, "⚙")


def _get_damper_field_labels(keyword: str) -> Dict[int, str]:
    """
    ダンパー定義の各フィールドに対する説明ラベルを返します（1-indexed）。
    SNAP テキストデータ仕様に準拠。
    """
    if keyword == "DVOD":
        # 粘性/オイルダンパー (Device Viscous/Oil Damper)
        return {
            1:  "種別 (52:免震用油, 53:免震用粘, 72:制振用油, 73:制振用粘)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "減衰モデル (0:ダッシュポット単, 1:Voigt, 2:Maxwell, 3:D+M, 4:M, 5:回転)",
            6:  "質量 (t)",
            7:  "装置特性種別 (0:線形弾性EL1, 1:バイリニアEL2, 2:トリリニアEL3, 3:曲線EF1)",
            8:  "C0（ゼロ速度時剛性 / 減衰係数）",
            9:  "Fc（リリーフ力）",
            10: "Fv（最大ダンパー力）",
            11: "Vs（基準速度）",
            12: "α（速度指数）",
            13: "β（温度依存指数）",
            14: "剛性",
            15: "取付け剛性",
            16: "装置長",
            17: "重量種別 (0:単位長当, 1:重量)",
            18: "重量",
            19: "変動係数 下限温度",
            20: "変動係数 下限ε",
            21: "変動係数 上限温度",
            22: "変動係数 上限ε",
        }
    elif keyword == "DSD":
        # 鋼材/摩擦ダンパー (Device Steel Damper)
        return {
            1:  "種別 (0:未使用, 1:ブレース, 2:間柱, 3:摩擦)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "降伏変形考慮 (0:なし, 1:あり)",
            6:  "復元力特性種別 (0:BL2, 1:AL(Y)2, 2:BL(Y)3, 3:RD4, 4:VHD, 5:K2, 6:MCB, 7:TL3, 8:MP3)",
            7:  "K0（初期剛性）",
            8:  "Fe（弾性限界力）",
            9:  "Fy（降伏荷重）",
            10: "Fu（最大荷重）",
            11: "α（2次剛性比）",
            12: "β",
            13: "P1",
            14: "P2",
            15: "P3",
            16: "P4",
            17: "d",
            18: "剛性",
            19: "取付け F",
            20: "取付け α",
            21: "取付け d",
            22: "装置長",
            23: "重量種別 (0:単位長当, 1:重量)",
            24: "重量",
            25: "初期荷重計算 (0:なし, 1:あり)",
            26: "疲労閾値",
            27: "疲労曲線 P1",
            28: "疲労曲線 P2",
            29: "増分幅",
            30: "初期荷重計算2",
            31: "減衰",
        }
    elif keyword == "DVHY":
        return {
            1: "種別",
            2: "k-DB 会社番号",
            3: "k-DB 製品番号",
            4: "k-DB 型番",
            5: "復元力特性種別",
            6: "K0（初期剛性）",
            7: "Fy（降伏荷重）",
            8: "α（2次剛性比）",
            9: "装置長",
            10: "重量",
        }
    return {}


def _get_damper_field_units(keyword: str) -> Dict[int, str]:
    """各フィールドの単位・補足テキストを返します（1-indexed）。"""
    if keyword == "DVOD":
        return {
            8:  "kN/m または kN·s/m",
            9:  "kN",
            10: "kN",
            11: "m/s",
            12: "—（0〜1）",
            14: "kN/m",
            15: "kN/m",
            16: "m",
            18: "kN/m または kN",
        }
    elif keyword == "DSD":
        return {
            7:  "kN/m",
            8:  "kN",
            9:  "kN",
            10: "kN",
            11: "—（0〜1）",
        }
    return {}
