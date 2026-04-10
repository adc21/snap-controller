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

  [UX改善（新①）: 各タブにコンテキストガイドバナー追加]
  - 各タブの先頭に「このタブで何をするか」を一言で説明するガイドバナーを追加。
  - 初めて使うユーザーや操作に迷ったときに各タブの役割が即座にわかります。
  - カラーコーディングでタブの種類を視覚的に区別できます。

  [UX改善（新②）: ダンパー定義テーブルのパラメータヒントパネル追加]
  - ダンパー定義タブで行を選択すると、そのパラメータの工学的説明・典型値・
    設計上の注意点を下部のヒントパネルに表示します。
  - 「Ce」「α」などSNAP特有の記号の意味が即座に確認でき、入力ミスを防ぎます。

  [UX改善（第9回⑤）: 配置計画タブのフロア別配置ビジュアルバー追加]
  - 配置計画タブのテーブル上部にフロア別ダンパー配置数をバー形式で可視化するパネルを追加。
  - 各フロアの合計基数を横棒グラフ（QProgressBar風）で表示し、「F1: ██ 2本」と一覧表示。
  - SpinBoxの値が変わるたびにリアルタイム更新し、配置が偏っているフロアを即座に把握できます。
  - `_placement_visual_frame` QFrame と `_rebuild_placement_visual()` メソッドを追加。

  [UX改善（新③）: スマートケース名自動生成ボタン追加]
  - ケース名フィールドの右に「🔖 名前を自動生成」ボタンを追加します。
  - 設定されているダンパーパラメータの変更内容から、意味のある説明的な名前を自動生成します。
  - 例: 「OIL_Ce500_α0.40」「STEEL_Fy3000_K50000」「RD配置×2本」など
  - これにより「Case-01」のような無意味な名前ではなく、後から内容がわかる名前が付けられます。

  [UX改善（第11回①）: 「📋 変更差分サマリー」タブ追加]
  - ダイアログに第5のタブ「📋 変更差分」を追加します。
  - 保存ボタンを押す前に「どのパラメータを何から何に変えたか」を一覧できます。
  - 変更カテゴリ（基本設定/ダンパー定義/配置計画）、フィールド名、変更前値→変更後値、
    変化率（%）をテーブル形式で表示します。
  - 変更がない場合は「まだ変更がありません」のプレースホルダーを表示します。
  - タブに切り替えると自動で最新状態に更新されるため、常に正確な差分を確認できます。
  - `_make_diff_tab()` と `_update_diff_tab()` メソッドを追加。
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
    QMessageBox,
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

        # 追加ダンパー定義（コピーによる新規作成）
        self._extra_def_tables: Dict[str, QTableWidget] = {}   # (後方互換用、空)
        self._extra_defs_meta: List[Dict] = []                  # (後方互換用、空)

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

    def _make_tab_guide_banner(
        self,
        icon: str,
        text: str,
        bg: str = "#e3f2fd",
        border: str = "#90caf9",
        text_color: str = "#1565c0",
    ) -> QFrame:
        """
        UX改善（新①）: 各タブの先頭に表示するコンテキストガイドバナーを作成します。

        Parameters
        ----------
        icon : str
            バナー左端に表示する絵文字またはテキストアイコン。
        text : str
            ガイドテキスト（1〜2文程度）。
        bg : str
            背景色（CSSカラー文字列）。
        border : str
            左ボーダー色（CSSカラー文字列）。
        text_color : str
            テキスト色（CSSカラー文字列）。
        """
        frame = QFrame()
        frame.setFrameShape(QFrame.NoFrame)
        frame.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {bg};"
            f"  border-left: 3px solid {border};"
            f"  border-radius: 0px;"
            f"  margin: 0px;"
            f"}}"
        )
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet(
            "font-size: 16px; background: transparent; border: none;"
        )
        icon_lbl.setFixedWidth(22)
        h.addWidget(icon_lbl)

        text_lbl = QLabel(text)
        text_lbl.setStyleSheet(
            f"color: {text_color}; font-size: 11px;"
            "background: transparent; border: none;"
        )
        text_lbl.setWordWrap(True)
        h.addWidget(text_lbl, stretch=1)

        return frame

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
        self._tabs.addTab(self._make_diff_tab(),        "📋 変更差分")

        # タブを切り替えたときに変更差分タブを自動更新
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ---- ボタン ----
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


    # ────── Tab 1: 基本設定 ──────────────────

    def _make_basic_tab(self) -> QWidget:
        # UX改善（新①）: 外側コンテナで「ガイドバナー + フォーム」の構成にする
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        outer_layout.addWidget(self._make_tab_guide_banner(
            "⚙",
            "このケースの識別名を設定します。"
            "ダンパーパラメータや配置を変更しなければ、元の .s8i モデルと同じ条件で解析されます。"
            "まずはわかりやすいケース名を付けて、次のタブへ進みましょう。",
            bg="#e8f5e9", border="#66bb6a", text_color="#1b5e20",
        ))

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

        # UX改善（新③）: 「🔖 名前を自動生成」ボタンをケース名フィールドの右に配置
        _name_row_widget = QWidget()
        _name_row_layout = QHBoxLayout(_name_row_widget)
        _name_row_layout.setContentsMargins(0, 0, 0, 0)
        _name_row_layout.setSpacing(4)
        _name_row_layout.addWidget(self._name_edit)
        self._btn_gen_name = QPushButton("🔖 名前を自動生成")
        self._btn_gen_name.setFixedWidth(140)
        self._btn_gen_name.setToolTip(
            "設定したダンパーパラメータの内容から、後から見てわかりやすいケース名を自動生成します。\n"
            "例: 「OIL_Ce500_α0.40」「STEEL_Fy3000」「RD基数×2」など\n"
            "（現在のテキストボックスの内容は上書きされます）"
        )
        self._btn_gen_name.setStyleSheet(
            "QPushButton {"
            "  font-size: 10px; padding: 3px 8px;"
            "  border: 1px solid #90caf9; border-radius: 3px;"
            "  background-color: #e3f2fd; color: #1565c0;"
            "}"
            "QPushButton:hover { background-color: #bbdefb; }"
            "QPushButton:pressed { background-color: #90caf9; }"
        )
        self._btn_gen_name.clicked.connect(self._on_generate_smart_name)
        _name_row_layout.addWidget(self._btn_gen_name)
        form.addRow("ケース名:", _name_row_widget)

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

        outer_layout.addWidget(w)
        outer_layout.addStretch()
        return outer

    # ────── Tab 2: ダンパー定義 ──────────────

    def _make_def_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)

        # UX改善（新①）: タブガイドバナー
        outer_layout.addWidget(self._make_tab_guide_banner(
            "🔧",
            "ダンパーの物性値（減衰係数・降伏荷重など）を変更します。"
            "変更しなければ .s8i ファイルの元の値がそのまま使われます。"
            "行を選択すると下部にパラメータの説明が表示されます。",
            bg="#fff8e1", border="#ffca28", text_color="#e65100",
        ))

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

            # ボタンバー（k-DB 選択）
            btn_bar = QHBoxLayout()
            kdb_btn = QPushButton("🗄 k-DB から選択")
            kdb_btn.setToolTip("k-DB（構造部材データベース）からダンパーを選択してパラメータを自動入力します")
            kdb_btn.setStyleSheet(
                "QPushButton { background: #E3F2FD; color: #1565C0; border: 1px solid #90CAF9;"
                " padding: 3px 10px; border-radius: 3px; font-size: 12px; }"
                "QPushButton:hover { background: #BBDEFB; }"
            )
            kdb_btn.clicked.connect(lambda checked=False, d=ddef: self._open_kdb_browser(d))
            btn_bar.addWidget(kdb_btn)
            btn_bar.addStretch()
            grp_layout.addLayout(btn_bar)

            tbl = self._make_damper_def_table(ddef)
            self._damper_def_tables[ddef.name] = tbl
            grp_layout.addWidget(tbl)
            content_layout.addWidget(grp)

        content_layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        # ── UX改善（第12回①）: ダンパーパラメータ妥当性チェックバー ──────────────────
        # テーブルの値が変わるたびに「✅ すべて正常」「⚠ 要確認」「❌ 不正値あり」を
        # リアルタイム表示し、保存前にミスを発見できるようにします。
        self._param_validity_bar = QFrame()
        self._param_validity_bar.setFrameShape(QFrame.NoFrame)
        self._param_validity_bar.setStyleSheet(
            "QFrame {"
            "  background-color: #e8f5e9;"
            "  border: 1px solid #a5d6a7;"
            "  border-radius: 4px;"
            "  margin: 2px 0px;"
            "}"
        )
        _validity_row = QHBoxLayout(self._param_validity_bar)
        _validity_row.setContentsMargins(10, 4, 10, 4)
        _validity_row.setSpacing(6)

        self._validity_icon_lbl = QLabel("✅")
        self._validity_icon_lbl.setStyleSheet(
            "font-size: 14px; background: transparent; border: none;"
        )
        self._validity_icon_lbl.setFixedWidth(20)
        _validity_row.addWidget(self._validity_icon_lbl)

        self._validity_text_lbl = QLabel("すべてのパラメータが正常です（変更なし）")
        self._validity_text_lbl.setStyleSheet(
            "color: #1b5e20; font-size: 10px; background: transparent; border: none;"
        )
        self._validity_text_lbl.setWordWrap(False)
        _validity_row.addWidget(self._validity_text_lbl, stretch=1)

        # 「問題のある行にジャンプ」ボタン（問題あり時のみ表示）
        self._validity_jump_btn = QPushButton("⬆ 問題の行を確認")
        self._validity_jump_btn.setFixedHeight(20)
        self._validity_jump_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 10px; padding: 1px 8px;"
            "  border: 1px solid #ef9a9a; border-radius: 3px;"
            "  background: #ffebee; color: #c62828;"
            "}"
            "QPushButton:hover { background: #ffcdd2; }"
        )
        self._validity_jump_btn.hide()
        self._validity_jump_btn.clicked.connect(self._jump_to_invalid_param)
        _validity_row.addWidget(self._validity_jump_btn)

        # 初期状態では非表示（変更が入るまで出さない）
        self._param_validity_bar.hide()
        outer_layout.addWidget(self._param_validity_bar)

        # UX改善（新②）: パラメータ説明ヒントパネル（行選択時に更新）
        self._def_hint_panel = QFrame()
        self._def_hint_panel.setFrameShape(QFrame.StyledPanel)
        self._def_hint_panel.setStyleSheet(
            "QFrame {"
            "  background-color: #f3e5f5;"
            "  border: 1px solid #ce93d8;"
            "  border-radius: 4px;"
            "  margin: 4px;"
            "}"
        )
        self._def_hint_panel.setMaximumHeight(100)
        _hint_layout = QHBoxLayout(self._def_hint_panel)
        _hint_layout.setContentsMargins(10, 6, 10, 6)
        _hint_layout.setSpacing(8)

        _hint_icon = QLabel("💡")
        _hint_icon.setStyleSheet(
            "font-size: 16px; background: transparent; border: none;"
        )
        _hint_icon.setFixedWidth(22)
        _hint_layout.addWidget(_hint_icon)

        self._def_hint_label = QLabel(
            "↑ テーブルの行を選択すると、そのパラメータの工学的な説明がここに表示されます。"
        )
        self._def_hint_label.setStyleSheet(
            "color: #6a1b9a; font-size: 11px; background: transparent; border: none;"
        )
        self._def_hint_label.setWordWrap(True)
        self._def_hint_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        _hint_layout.addWidget(self._def_hint_label, stretch=1)

        outer_layout.addWidget(self._def_hint_panel)

        # 各テーブルの行選択シグナルをヒントパネル更新に接続
        for ddef_name, tbl in self._damper_def_tables.items():
            tbl.itemSelectionChanged.connect(
                lambda t=tbl, kw=ddef_name: self._on_def_table_row_selected(t, kw)
            )

        return outer

    def _on_def_table_row_selected(self, tbl: QTableWidget, keyword: str) -> None:
        """
        UX改善（新②）: ダンパー定義テーブルの行が選択されたとき、
        そのパラメータの説明をヒントパネルに表示します。

        Parameters
        ----------
        tbl : QTableWidget
            シグナルを発行したテーブル。
        keyword : str
            ダンパー定義のキーワード（例: "DVOD", "DSD"）。
        """
        if not hasattr(self, "_def_hint_label"):
            return
        selected = tbl.selectionModel().selectedRows()
        if not selected:
            self._def_hint_label.setText(
                "↑ テーブルの行を選択すると、そのパラメータの工学的な説明がここに表示されます。"
            )
            return

        row = selected[0].row()
        field_idx = row + 1  # 1-indexed

        # キーワードを参照するためにddef名からキーワードを取得
        ddef_kw = keyword
        # ddef_nameからkeywordを取得（s8iから）
        if self._s8i:
            for ddef in self._s8i.damper_defs:
                if ddef.name == keyword:
                    ddef_kw = ddef.keyword
                    break

        hints = _DAMPER_FIELD_HINTS.get(ddef_kw, {})
        hint_text = hints.get(field_idx)

        # フィールド名を取得
        field_labels = _get_damper_field_labels(ddef_kw)
        field_label = field_labels.get(field_idx, f"フィールド {field_idx}")

        if hint_text:
            self._def_hint_label.setText(
                f"<b>#{field_idx} {field_label.split('（')[0].split('(')[0].strip()}</b><br>"
                f"<span style='color:#4a148c;'>{hint_text.replace(chr(10), '<br>')}</span>"
            )
            self._def_hint_label.setTextFormat(Qt.RichText)
        else:
            self._def_hint_label.setText(
                f"<b>#{field_idx}</b>  {field_label}"
            )
            self._def_hint_label.setTextFormat(Qt.RichText)

    def _open_kdb_browser(self, ddef) -> None:
        """k-DB ブラウザを開き、選択したパラメータをダンパー定義テーブルに反映します。"""
        from app.ui.kdb_browser_dialog import KdbBrowserDialog

        # k-DB パスの決定（設定ファイル → デフォルト）
        kdb_dir = r"C:\Program Files (x86)\k-DB"
        try:
            from app.models.s8i_parser import get_kdb_dir_from_settings
            if get_kdb_dir_from_settings:
                kdb_dir = get_kdb_dir_from_settings() or kdb_dir
        except (ImportError, Exception):
            pass

        dlg = KdbBrowserDialog(
            self,
            kdb_dir=kdb_dir,
            filter_keyword=ddef.keyword,
        )

        # exec() でモーダル表示し、Accepted なら結果を取得して反映
        result = dlg.exec()
        if result != QDialog.Accepted:
            return

        snap_kw = dlg.applied_snap_keyword()
        snap_fields = dlg.applied_snap_fields()
        if not snap_kw or not snap_fields:
            return

        self._apply_kdb_params(ddef, snap_kw, snap_fields)

    def _apply_kdb_params(self, ddef, snap_kw: str, snap_fields: dict) -> None:
        """k-DB から選択したパラメータをダンパー定義テーブルに書き込みます。"""
        # 元定義テーブルと追加定義テーブルの両方を検索
        tbl = self._damper_def_tables.get(ddef.name)
        if tbl is None:
            tbl = self._extra_def_tables.get(ddef.name)
        if tbl is None:
            QMessageBox.warning(
                self, "適用エラー",
                f"ダンパー定義 '{ddef.name}' のテーブルが見つかりません。"
            )
            return

        field_labels = _get_damper_field_labels(snap_kw)
        field_units  = _get_damper_field_units(snap_kw)

        applied_count = 0

        # snap_fields の各フィールドを書き込む（field_idx は 1-indexed）
        for field_idx, val in snap_fields.items():
            row_idx = field_idx - 1  # 0-indexed 行番号

            # 必要なら行を追加（フィールドが .s8i 定義より多い場合）
            while tbl.rowCount() <= row_idx:
                new_row = tbl.rowCount()
                new_fidx = new_row + 1  # 1-indexed
                tbl.insertRow(new_row)

                no_item = QTableWidgetItem(str(new_fidx))
                no_item.setFlags(no_item.flags() & ~Qt.ItemIsEditable)
                no_item.setTextAlignment(Qt.AlignCenter)
                tbl.setItem(new_row, _DEF_COL_IDX, no_item)

                lbl_item = QTableWidgetItem(field_labels.get(new_fidx, ""))
                lbl_item.setFlags(lbl_item.flags() & ~Qt.ItemIsEditable)
                tbl.setItem(new_row, _DEF_COL_LABEL, lbl_item)

                orig_item = QTableWidgetItem("")
                orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
                orig_item.setForeground(QColor("#888888"))
                tbl.setItem(new_row, _DEF_COL_ORIG, orig_item)

                empty_val = QTableWidgetItem("")
                tbl.setItem(new_row, _DEF_COL_VALUE, empty_val)

                unit_item = QTableWidgetItem(field_units.get(new_fidx, ""))
                unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
                tbl.setItem(new_row, _DEF_COL_UNIT, unit_item)

            # 値を書き込む
            val_item = tbl.item(row_idx, _DEF_COL_VALUE)
            if val_item is None:
                val_item = QTableWidgetItem()
                tbl.setItem(row_idx, _DEF_COL_VALUE, val_item)
            if isinstance(val, str):
                val_item.setText(val)
            elif isinstance(val, float):
                val_item.setText(f"{val:.6g}")
            else:
                val_item.setText(str(val))
            applied_count += 1

        # 行が増えた場合は表示高さも更新
        tbl.setMaximumHeight(min(34 * tbl.rowCount() + 30, 500))

        # 変更バナー更新
        self._update_banner()

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
        # UX改善（第12回①）: パラメータ妥当性をリアルタイムチェック
        self._check_param_validity()

    def _check_param_validity(self) -> None:
        """
        UX改善（第12回①）: ダンパー定義テーブル内の全「現在の値」セルを検証し、
        妥当性チェックバーを更新します。

        検証内容:
        - 空文字列 → ❌ 不正値
        - 数値に変換できない値（文字列など）→ ❌ 不正値
        - 数値だが 0 以下（減衰係数・降伏荷重等は通常正値）→ ⚠ 要確認
        - 全て正常 → ✅ 表示

        問題がなく変更もない場合はバーを非表示にします。
        """
        if not hasattr(self, "_param_validity_bar"):
            return

        # 全テーブルを検査
        all_tables: list = []
        for tbl in self._damper_def_tables.values():
            all_tables.append(tbl)
        for tbl in self._extra_def_tables.values():
            all_tables.append(tbl)

        # 変更があるか確認
        any_changed = False
        errors: list = []    # ❌ 不正値
        warnings: list = []  # ⚠ 要確認（0以下）

        for tbl in all_tables:
            for r in range(tbl.rowCount()):
                val_item = tbl.item(r, _DEF_COL_VALUE)
                orig_item = tbl.item(r, _DEF_COL_ORIG)
                if val_item is None:
                    continue
                raw = val_item.text().strip()
                orig_raw = orig_item.text().strip() if orig_item else raw

                # 変更があるか
                if raw != orig_raw:
                    any_changed = True

                # 空文字チェック（元値も空の場合はスキップ＝元から空欄のフィールド）
                if raw == "":
                    if orig_raw == "":
                        continue  # 元から空欄のフィールドはエラー対象外
                    label_item = tbl.item(r, _DEF_COL_LABEL)
                    label = label_item.text() if label_item else f"行{r+1}"
                    errors.append(f"{label}: 空欄")
                    continue

                # 数値変換チェック（型番・名称など文字列フィールドはスキップ）
                try:
                    num = float(raw)
                except ValueError:
                    # 元の値も数値でなければ文字列フィールドとみなしスキップ
                    is_orig_str = True
                    if orig_raw:
                        try:
                            float(orig_raw)
                            is_orig_str = False
                        except ValueError:
                            is_orig_str = True
                    if is_orig_str:
                        continue  # 文字列フィールド → 数値チェック対象外
                    label_item = tbl.item(r, _DEF_COL_LABEL)
                    label = label_item.text() if label_item else f"行{r+1}"
                    errors.append(f"{label}: 「{raw[:8]}」は数値ではありません")
                    continue

                # 0以下チェック（0以下になると解析が発散しやすい）
                # ただし変更がない行は警告対象外
                if num <= 0 and raw != orig_raw:
                    label_item = tbl.item(r, _DEF_COL_LABEL)
                    label = label_item.text() if label_item else f"行{r+1}"
                    warnings.append(f"{label} = {raw}")

        # 変更がなければバーを非表示
        if not any_changed:
            self._param_validity_bar.hide()
            return

        # バーを表示してメッセージ更新
        self._param_validity_bar.show()
        if errors:
            # ❌ 不正値あり
            msg = "不正値あり: " + "、".join(errors[:2])
            if len(errors) > 2:
                msg += f" 他{len(errors)-2}件"
            self._validity_icon_lbl.setText("❌")
            self._validity_text_lbl.setText(msg)
            self._param_validity_bar.setStyleSheet(
                "QFrame {"
                "  background-color: #ffebee;"
                "  border: 1px solid #ef9a9a;"
                "  border-radius: 4px; margin: 2px 0px;"
                "}"
            )
            self._validity_text_lbl.setStyleSheet(
                "color: #b71c1c; font-size: 10px; background: transparent; border: none;"
            )
            self._validity_jump_btn.show()
        elif warnings:
            # ⚠ 0以下の値あり
            msg = "要確認: " + "、".join(warnings[:2])
            if len(warnings) > 2:
                msg += f" 他{len(warnings)-2}件（0以下の値は解析が不安定になる可能性があります）"
            else:
                msg += "（0以下の値は解析が不安定になる可能性があります）"
            self._validity_icon_lbl.setText("⚠")
            self._validity_text_lbl.setText(msg)
            self._param_validity_bar.setStyleSheet(
                "QFrame {"
                "  background-color: #fff8e1;"
                "  border: 1px solid #ffca28;"
                "  border-radius: 4px; margin: 2px 0px;"
                "}"
            )
            self._validity_text_lbl.setStyleSheet(
                "color: #e65100; font-size: 10px; background: transparent; border: none;"
            )
            self._validity_jump_btn.hide()
        else:
            # ✅ 全て正常
            n_changed = sum(
                1 for tbl in all_tables
                for r in range(tbl.rowCount())
                if (tbl.item(r, _DEF_COL_VALUE) and tbl.item(r, _DEF_COL_ORIG)
                    and tbl.item(r, _DEF_COL_VALUE).text().strip()
                    != tbl.item(r, _DEF_COL_ORIG).text().strip())
            )
            self._validity_icon_lbl.setText("✅")
            self._validity_text_lbl.setText(
                f"すべてのパラメータが正常です（{n_changed}件変更済み）"
            )
            self._param_validity_bar.setStyleSheet(
                "QFrame {"
                "  background-color: #e8f5e9;"
                "  border: 1px solid #a5d6a7;"
                "  border-radius: 4px; margin: 2px 0px;"
                "}"
            )
            self._validity_text_lbl.setStyleSheet(
                "color: #1b5e20; font-size: 10px; background: transparent; border: none;"
            )
            self._validity_jump_btn.hide()

    def _jump_to_invalid_param(self) -> None:
        """
        UX改善（第12回①）: 不正値のある行にスクロール・フォーカスします。
        問題のある最初のテーブル行を選択状態にします。
        """
        if not hasattr(self, "_damper_def_tables"):
            return
        all_tables: list = list(self._damper_def_tables.values()) + list(
            self._extra_def_tables.values()
        )
        for tbl in all_tables:
            for r in range(tbl.rowCount()):
                val_item = tbl.item(r, _DEF_COL_VALUE)
                if val_item is None:
                    continue
                raw = val_item.text().strip()
                if raw == "":
                    tbl.setCurrentCell(r, _DEF_COL_VALUE)
                    tbl.scrollToItem(val_item)
                    # ダンパー定義タブに切り替え
                    self._tabs.setCurrentIndex(1)
                    return
                try:
                    float(raw)
                except ValueError:
                    tbl.setCurrentCell(r, _DEF_COL_VALUE)
                    tbl.scrollToItem(val_item)
                    self._tabs.setCurrentIndex(1)
                    return

    # ────── Tab 3: 配置計画 ──────────────────

    def _make_placement_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        # UX改善（新①）: タブガイドバナー
        layout.addWidget(self._make_tab_guide_banner(
            "📐",
            "RD（免制振装置）ごとに「装置定義（ダンパー種類）」と「基数（本数）」を変更します。"
            "変更しない行は元の .s8i の配置のまま解析されます。"
            "行を選択すると下部に紐づくダンパー定義パラメータが表示されます。",
            bg="#fce4ec", border="#f48fb1", text_color="#880e4f",
        ))

        # 説明
        desc = QLabel(
            "<small><b>免制振装置 (RD)</b> の配置と基数を設定します。<br>"
            "「<b>基数(倍数)</b>」は同じ位置に設置するダンパーの本数です。<br>"
            "「<b>装置定義</b>」を変更すると下部パネルにそのダンパーのパラメータが表示されます。</small>"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # ---- UX改善（第9回⑤）: フロア別ダンパー配置ビジュアルバーパネル ----
        # SpinBox の値から各フロア（z_grid）の合計基数を集計してバー形式で表示します。
        # RD 定義が読み込まれると _rebuild_placement_visual() で内容が構築されます。
        self._placement_visual_frame = QFrame()
        self._placement_visual_frame.setFrameShape(QFrame.StyledPanel)
        self._placement_visual_frame.setStyleSheet(
            "QFrame {"
            "  background-color: palette(window);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "}"
        )
        self._placement_visual_frame.setMaximumHeight(72)
        _pv_outer = QHBoxLayout(self._placement_visual_frame)
        _pv_outer.setContentsMargins(8, 4, 8, 4)
        _pv_outer.setSpacing(4)

        _pv_title = QLabel("📊 フロア別配置:")
        _pv_title.setStyleSheet("font-size: 10px; color: palette(text);")
        _pv_title.setFixedWidth(90)
        _pv_outer.addWidget(_pv_title)

        self._placement_visual_content = QHBoxLayout()
        self._placement_visual_content.setSpacing(12)
        _pv_outer.addLayout(self._placement_visual_content, stretch=1)

        layout.addWidget(self._placement_visual_frame)

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

        # 利用可能なダンパー定義名リスト（元の定義 ＋ 追加定義）
        def_names = (
            [d.name for d in self._s8i.damper_defs]
            + [m["name"] for m in self._extra_defs_meta]
        ) if self._s8i else []

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
        # UX改善（第9回⑤）: テーブル初期化後にフロア別配置ビジュアルバーを更新
        self._rebuild_placement_visual()

    # ────── Tab 4: メモ ──────────────────────

    def _make_memo_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # UX改善（新①）: タブガイドバナー
        layout.addWidget(self._make_tab_guide_banner(
            "📝",
            "このケースの設計意図・変更内容・気づきをメモします。"
            "メモはプロジェクトファイルに保存され、後から参照できます。"
            "例: 「Ce を 500→600 に増やして加速度低減を狙う」など。",
            bg="#e8eaf6", border="#7986cb", text_color="#283593",
        ))

        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText(
            "このケースに関するメモを入力してください…\n"
            "（例: Ce=500, α=0.4, 2Fと3Fのみ設置）"
        )
        layout.addWidget(self._notes_edit)
        return w

    # ────── Tab 5: 変更差分サマリー ──────────

    def _make_diff_tab(self) -> QWidget:
        """
        UX改善（第11回①）: 変更差分サマリータブ。
        保存前に全変更点をテーブル形式で一覧表示します。
        """
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ガイドバナー
        outer.addWidget(self._make_tab_guide_banner(
            "📋",
            "変更した全パラメータを一覧で確認できます。"
            "保存する前に意図した変更になっているか最終チェックしましょう。"
            "変更がない場合は「まだ変更がありません」と表示されます。",
            bg="#e0f2f1", border="#26a69a", text_color="#004d40",
        ))

        # プレースホルダーラベル（変更なし時）
        self._diff_empty_lbl = QLabel("📋  まだ変更がありません\n（ダンパー定義・配置計画・基本設定を変更すると、ここに差分が表示されます）")
        self._diff_empty_lbl.setAlignment(Qt.AlignCenter)
        self._diff_empty_lbl.setStyleSheet("color: #888; font-size: 13px; padding: 24px;")
        self._diff_empty_lbl.setWordWrap(True)

        # 変更差分テーブル
        self._diff_table = QTableWidget(0, 5)
        self._diff_table.setHorizontalHeaderLabels(["カテゴリ", "定義/要素", "フィールド", "変更前", "変更後"])
        self._diff_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._diff_table.verticalHeader().setVisible(False)
        self._diff_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._diff_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._diff_table.setAlternatingRowColors(True)
        self._diff_table.setStyleSheet("QTableWidget { font-size: 12px; }")

        # 変更件数ラベル
        self._diff_count_lbl = QLabel()
        self._diff_count_lbl.setStyleSheet("color: #555; font-size: 11px; padding: 4px 8px;")

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(8, 4, 8, 8)
        inner_layout.addWidget(self._diff_count_lbl)
        inner_layout.addWidget(self._diff_empty_lbl)
        inner_layout.addWidget(self._diff_table)

        outer.addWidget(inner)
        return w

    def _update_diff_tab(self) -> None:
        """
        UX改善（第11回①）: 変更差分タブの内容を最新状態に更新します。
        """
        if not hasattr(self, "_diff_table"):
            return

        rows: List[tuple] = []  # (category, def_name, field, orig, current, pct)

        # ── 基本設定の変更 ──
        if hasattr(self, "_name_edit"):
            new_name = self._name_edit.text().strip()
            if new_name != self._orig_name:
                rows.append(("⚙ 基本設定", "—", "ケース名", self._orig_name, new_name, None))
        if hasattr(self, "_out_edit"):
            new_out = self._out_edit.text().strip()
            if new_out != self._orig_output_dir:
                rows.append(("⚙ 基本設定", "—", "出力ディレクトリ", self._orig_output_dir or "（未設定）", new_out or "（未設定）", None))

        # ── ダンパー定義の変更 ──
        for ddef in (self._s8i.damper_defs if self._s8i else []):
            tbl = self._damper_def_tables.get(ddef.name)
            if not tbl:
                continue
            fl = _get_damper_field_labels(ddef.keyword)
            for r in range(tbl.rowCount()):
                vi = tbl.item(r, _DEF_COL_VALUE)
                oi = tbl.item(r, _DEF_COL_ORIG)
                if vi and oi and vi.text().strip() != oi.text().strip():
                    field_idx = r + 1
                    label = fl.get(field_idx, f"F{field_idx}")
                    orig_v = oi.text().strip()
                    new_v  = vi.text().strip()
                    pct: Optional[float] = None
                    try:
                        o_f, n_f = float(orig_v), float(new_v)
                        if o_f != 0:
                            pct = (n_f - o_f) / abs(o_f) * 100.0
                    except (ValueError, ZeroDivisionError):
                        pass
                    rows.append(("🔧 ダンパー定義", ddef.name, label, orig_v, new_v, pct))

        # ── 配置計画の変更 ──
        if self._s8i:
            for i, elem in enumerate(self._s8i.damper_elements):
                if i < len(self._rd_qty_spins):
                    new_qty = self._rd_qty_spins[i].value()
                    if new_qty != elem.quantity:
                        pct_qty = (new_qty - elem.quantity) / max(elem.quantity, 1) * 100.0
                        rows.append(("📐 配置計画", elem.name, "基数（倍数）",
                                     str(elem.quantity), str(new_qty), pct_qty))
                if i < len(self._rd_def_combos):
                    new_def = self._rd_def_combos[i].currentText()
                    if new_def != elem.damper_def_name:
                        rows.append(("📐 配置計画", elem.name, "装置定義",
                                     elem.damper_def_name, new_def, None))

        # テーブルを書き換え
        self._diff_table.setRowCount(len(rows))
        for r_idx, (cat, def_name, field, orig, new_val, pct) in enumerate(rows):
            # 変化率テキスト付き「変更後」表示
            if pct is not None:
                sign = "+" if pct >= 0 else ""
                pct_str = f"  ({sign}{pct:.1f}%)"
                is_improvement = pct < 0  # 応答低減方向
            else:
                pct_str = ""
                is_improvement = None

            def _make_item(text: str, fg: Optional[QColor] = None) -> QTableWidgetItem:
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                if fg:
                    item.setForeground(fg)
                return item

            self._diff_table.setItem(r_idx, 0, _make_item(cat))
            self._diff_table.setItem(r_idx, 1, _make_item(def_name))
            self._diff_table.setItem(r_idx, 2, _make_item(field))
            # 変更前セル（薄い赤）
            orig_item = _make_item(orig, QColor("#c62828"))
            orig_item.setBackground(QColor("#fff8f8"))
            self._diff_table.setItem(r_idx, 3, orig_item)
            # 変更後セル（変化率付き、薄い緑）
            new_item = _make_item(f"{new_val}{pct_str}", QColor("#1b5e20"))
            new_item.setBackground(QColor("#f1f8e9"))
            self._diff_table.setItem(r_idx, 4, new_item)

        # 件数ラベルと可視性を更新
        if rows:
            self._diff_count_lbl.setText(f"変更件数: {len(rows)} 件")
            self._diff_empty_lbl.hide()
            self._diff_table.show()
            self._diff_count_lbl.show()
        else:
            self._diff_count_lbl.hide()
            self._diff_empty_lbl.show()
            self._diff_table.hide()

    def _on_tab_changed(self, index: int) -> None:
        """タブ切り替え時に変更差分タブを自動更新します。"""
        # index 4 = 「📋 変更差分」タブ
        if index == 4:
            self._update_diff_tab()

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

    def _on_generate_smart_name(self) -> None:
        """
        UX改善（新③）: 「🔖 名前を自動生成」ボタン押下ハンドラ。

        現在のダンパーパラメータ変更・配置計画変更の内容を読み取り、
        意味のある説明的なケース名を生成してケース名フィールドに設定します。
        """
        name = self._build_smart_case_name()
        self._name_edit.setText(name)

    def _build_smart_case_name(self) -> str:
        """
        UX改善（新③）: ダンパーパラメータから説明的なケース名を生成します。

        変更されているダンパーパラメータ（Ce、Fy、α等）を読み取り、
        「DVOD_Ce500_α0.40」などの意味のある名前を生成します。

        Returns
        -------
        str
            生成されたケース名（重複しない連番付き）。
        """
        # ダンパー定義の変更を読み取る
        keyword_abbrev = {
            "DVOD": "OIL", "DSD": "STEEL", "DVHY": "HYST",
            "DVBI": "BILIN", "DVSL": "SLIDE", "DVFR": "FRIC",
            "DVTF": "VE", "DVMS": "TMD",
        }
        # フィールドごとの短い表示名（DVOD: {1: "Ce", 2: "α"} etc.）
        field_abbrev = {
            "DVOD": {1: "Ce", 2: "α", 3: "Fmax"},
            "DSD":  {1: "Fy", 2: "K", 3: "Kp"},
            "DVHY": {1: "Fy", 2: "K", 3: "Kp"},
            "DVBI": {1: "Fy", 2: "K", 3: "dy"},
            "DVFR": {1: "μ", 2: "N"},
            "DVTF": {1: "Kv", 2: "cv"},
            "DVMS": {1: "m", 2: "k"},
        }

        parts: List[str] = []

        # ── ダンパー定義タブで変更されている値を収集 ──
        if self._s8i and self._damper_def_tables:
            for ddef in self._s8i.damper_defs:
                tbl = self._damper_def_tables.get(ddef.name)
                if not tbl:
                    continue
                kw = ddef.keyword
                abbrevs = field_abbrev.get(kw, {})
                type_label = keyword_abbrev.get(kw, kw)
                changed_fields: List[str] = []
                for r in range(tbl.rowCount()):
                    vi = tbl.item(r, _DEF_COL_VALUE)
                    oi = tbl.item(r, _DEF_COL_ORIG)
                    if vi and oi and vi.text().strip() != oi.text().strip():
                        idx = r + 1
                        field_name = abbrevs.get(idx, f"F{idx}")
                        try:
                            fval = float(vi.text().strip())
                            # 小数がある場合は小数2桁まで、整数なら整数表示
                            if fval == int(fval):
                                val_str = str(int(fval))
                            else:
                                val_str = f"{fval:.2f}".rstrip("0")
                        except ValueError:
                            val_str = vi.text().strip()[:6]
                        changed_fields.append(f"{field_name}{val_str}")
                if changed_fields:
                    parts.append(f"{type_label}_{'_'.join(changed_fields[:2])}")

        # ── 配置計画タブで変更されている値を収集 ──
        if self._s8i:
            qty_changes = 0
            def_changes_rd = 0
            for i, elem in enumerate(self._s8i.damper_elements):
                if i < len(self._rd_qty_spins) and self._rd_qty_spins[i].value() != elem.quantity:
                    qty_changes += 1
                if i < len(self._rd_def_combos) and self._rd_def_combos[i].currentText() != elem.damper_def_name:
                    def_changes_rd += 1
            if qty_changes > 0 or def_changes_rd > 0:
                rd_part = "RD"
                if qty_changes > 0:
                    rd_part += f"基数×{qty_changes}箇所変更"
                if def_changes_rd > 0:
                    rd_part += f"定義変更{def_changes_rd}箇所"
                parts.append(rd_part)

        # ── 変更なしならデフォルト名 ──
        if not parts:
            return self._suggest_default_case_name()

        # ── ベース名を構築 ──
        base_name = "_".join(parts)
        # 長すぎる場合は切り詰め
        if len(base_name) > 40:
            base_name = base_name[:38] + "…"

        # ── 重複しない名前を生成 ──
        if base_name not in self._existing_names:
            return base_name
        idx = 2
        while f"{base_name}_{idx}" in self._existing_names:
            idx += 1
        return f"{base_name}_{idx}"

    def _save_to_case(self) -> None:
        """UIの現在値をケースデータモデルに保存します。

        SNAP 解析時の結果フォルダ分離のため、ケース名は他のケースと
        重複してはなりません。重複があれば自動採番して一意化します。
        """
        c = self._case
        desired_name = self._name_edit.text().strip() or "無名ケース"
        # 既存名との重複回避 (自ケース自身の旧名は除外)
        other_names = {n for n in self._existing_names if n != c.name}
        if desired_name in other_names:
            import re
            m = re.match(r"^(.*?)(?:\s*\((\d+)\))?\s*$", desired_name)
            base = m.group(1).strip() if m else desired_name
            n = 2
            while f"{base} ({n})" in other_names:
                n += 1
            desired_name = f"{base} ({n})"
        c.name       = desired_name
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

        # extra_defs は現在未使用（後方互換のため空リストを保持）
        c.extra_defs = []

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

    def _rebuild_placement_visual(self) -> None:
        """
        UX改善（第9回⑤）: フロア別ダンパー配置ビジュアルバーをリアルタイム更新します。

        s8i モデルの各 RD 要素の node_j.z_grid（フロア高さグリッド）を用いて
        各フロアの合計基数（SpinBox の現在値）を集計し、
        横バー + 数値形式（例: 「F3: ██ 2本」）で一覧表示します。
        RD 定義が存在しない場合はパネルを非表示にします。
        """
        if not hasattr(self, "_placement_visual_content"):
            return

        # 既存のウィジェットをクリア
        while self._placement_visual_content.count():
            item = self._placement_visual_content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not (self._s8i and self._s8i.damper_elements):
            self._placement_visual_frame.hide()
            return

        # フロアキー（z_grid）ごとに合計基数を集計
        floor_qty: dict = {}  # {z_label: total_qty}
        for row, elem in enumerate(self._s8i.damper_elements):
            qty = self._rd_qty_spins[row].value() if row < len(self._rd_qty_spins) else elem.quantity
            # z_grid をフロアキーとして使用（整数グリッド番号）
            node_j = None
            if self._s8i.nodes:
                node_j = self._s8i.nodes.get(elem.node_j)
            if node_j is not None and hasattr(node_j, "z_grid") and node_j.z_grid is not None:
                z = node_j.z_grid
                # z_grid は整数のこともあれば "Z2" のような文字列のこともある
                try:
                    key = f"F{int(z)}"
                except (ValueError, TypeError):
                    digits = "".join(c for c in str(z) if c.isdigit())
                    key = f"F{digits}" if digits else f"F{z}"
            else:
                key = f"RD{row + 1}"
            floor_qty[key] = floor_qty.get(key, 0) + qty

        if not floor_qty:
            self._placement_visual_frame.hide()
            return

        self._placement_visual_frame.show()

        max_qty = max(floor_qty.values()) if floor_qty else 1
        if max_qty == 0:
            max_qty = 1

        # フロアキーを昇順（数字順）でソート
        def _sort_key(k: str) -> tuple:
            digits = "".join(c for c in k if c.isdigit())
            return (int(digits) if digits else 0, k)

        sorted_floors = sorted(floor_qty.keys(), key=_sort_key)

        for floor_key in sorted_floors:
            qty = floor_qty[floor_key]
            # フロアラベル
            floor_lbl = QLabel(f"{floor_key}:")
            floor_lbl.setStyleSheet("font-size: 9px; color: palette(text); min-width: 28px;")
            # バー（QProgressBar風）
            from PySide6.QtWidgets import QProgressBar as _QPBar
            bar = _QPBar()
            bar.setRange(0, max_qty)
            bar.setValue(qty)
            bar.setMaximumHeight(12)
            bar.setMinimumWidth(40)
            bar.setMaximumWidth(80)
            bar.setTextVisible(False)
            bar.setStyleSheet(
                "QProgressBar { border: 1px solid palette(mid); border-radius: 3px;"
                "  background: palette(base); }"
                "QProgressBar::chunk { background: #1976d2; border-radius: 2px; }"
            )
            # 数値ラベル
            qty_lbl = QLabel(f"{qty}本")
            qty_lbl.setStyleSheet("font-size: 9px; color: palette(text); min-width: 24px;")

            # 1フロア分をまとめるレイアウト
            floor_col = QVBoxLayout()
            floor_col.setSpacing(0)
            floor_col.setContentsMargins(0, 0, 0, 0)
            top_row = QHBoxLayout()
            top_row.setSpacing(3)
            top_row.addWidget(floor_lbl)
            top_row.addWidget(bar)
            top_row.addWidget(qty_lbl)
            floor_col.addLayout(top_row)

            container = QWidget()
            container.setLayout(floor_col)
            self._placement_visual_content.addWidget(container)

        self._placement_visual_content.addStretch(1)

    def _on_qty_spin_changed(self, value: int) -> None:
        """SpinBox 変更時: 変更マークを更新します。"""
        spin = self.sender()
        row = spin.property("_row")
        if row is not None:
            self._update_rd_row_mark(row)
        self._update_banner()
        # UX改善（第9回⑤）: フロア別配置ビジュアルバーもリアルタイム更新
        self._rebuild_placement_visual()

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

        # --- タブ4: 変更差分 — 現在表示中なら自動更新 ---
        if hasattr(self, "_tabs") and self._tabs.currentIndex() == 4:
            self._update_diff_tab()

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
        "DIS":  "🏗️",
        "DISD": "🔄",
        "DVD":  "🌀",
        "DVED": "🟦",
        "DOD":  "💧",
        "DVHY": "🔄",
        "DVBI": "📐",
        "DVSL": "🔁",
        "DVFR": "🔧",
        "DVTF": "🌀",
        "DVMS": "⚖",
    }
    return badges.get(keyword, "⚙")


# ─────────────────────────────────────────────
#  UX改善（新②）: ダンパーフィールドヒント辞書
#  各フィールドに対する工学的説明・典型値を定義します。
# ─────────────────────────────────────────────

_DAMPER_FIELD_HINTS: Dict[str, Dict[int, str]] = {
    "DVOD": {
        1:  (
            "【種別】ダンパーの用途を指定します。\n"
            "52: 免震用オイルダンパー / 53: 免震用粘性ダンパー\n"
            "72: 制振用オイルダンパー / 73: 制振用粘性ダンパー"
        ),
        5:  (
            "【減衰モデル】力学モデルの種類を指定します。\n"
            "0: ダッシュポット単体（単純） / 1: Voigt（バネ+ダッシュポット並列）\n"
            "2: Maxwell（バネ+ダッシュポット直列） / 3: D+M複合型\n"
            "通常はオイルダンパーに 0 または 1 を使います。"
        ),
        7:  (
            "【装置特性種別】力-変位（または力-速度）特性の形状を指定します。\n"
            "0: 線形弾性 / 1: バイリニア（2折れ線）/ 2: トリリニア / 3: 曲線型\n"
            "免震用オイルダンパーは 0（線形）が多く使われます。"
        ),
        8:  (
            "【C0 / 減衰係数】速度に対する抵抗力の比例定数 [kN·s/m] です。\n"
            "F = C₀ × V^α で決まる減衰力の基準値。\n"
            "典型値: 油圧系制振ダンパー 500〜5000 kN·s/m\n"
            "         免震用大型オイルダンパー 1000〜10000 kN·s/m"
        ),
        9:  (
            "【Fc / リリーフ力】ダンパーが最大で発生する力（カットオフ力）[kN] です。\n"
            "この力を超えるとリリーフバルブが開き、力が一定に保たれます。\n"
            "典型値: 制振ダンパー 100〜2000 kN / 免震用 500〜5000 kN"
        ),
        10: (
            "【Fv / 最大ダンパー力】ダンパーが発生できる絶対最大力 [kN] です。\n"
            "Fc（リリーフ力）≦ Fv となります。\n"
            "典型値: 制振ダンパー 200〜3000 kN"
        ),
        11: (
            "【Vs / 基準速度】α（速度指数）を適用する基準となる速度 [m/s] です。\n"
            "F = C₀ × (V/Vs)^α の形で用いられます。\n"
            "典型値: 0.01〜0.5 m/s（実装置の仕様書から取得）"
        ),
        12: (
            "【α / 速度指数】力-速度関係の非線形指数（無次元）です。\n"
            "α = 1.0: 線形粘性 / α < 1.0: 非線形（大変形時の力増大を抑制）\n"
            "典型値: オイルダンパー 0.3〜1.0\n"
            "        α = 0.3〜0.5 が制振設計でよく使われます。"
        ),
        14: (
            "【剛性】ダンパー本体の軸剛性 [kN/m] です（内部バネ成分）。\n"
            "Maxwell モデルの場合のみ有効です。\n"
            "通常は取付け剛性（F15）と組み合わせて評価します。"
        ),
        15: (
            "【取付け剛性】ダンパーを架構に取り付けるブレース・金具の剛性 [kN/m] です。\n"
            "直列に配置されるため、値が小さいと有効な減衰性能が低下します。\n"
            "通常はダンパー本体剛性の 10 倍以上が推奨されます。"
        ),
    },
    "DSD": {
        1:  (
            "【種別】鋼材ダンパーの形式を指定します。\n"
            "1: ブレース型 / 2: 間柱型 / 3: 摩擦型\n"
            "形式によって復元力特性の解釈が変わります。"
        ),
        6:  (
            "【復元力特性種別】履歴モデルの形状を指定します。\n"
            "0: BL2（バイリニア）/ 1: AL(Y)2（別降伏型）/ 2: BL(Y)3\n"
            "3: RD4 / 4: VHD / 5: K2 など\n"
            "一般的な低降伏点鋼材ダンパーには 0（BL2）または 2（BL(Y)3）を使います。"
        ),
        7:  (
            "【K0 / 初期剛性】弾性域での剛性 [kN/m] です。\n"
            "降伏前の変形に抵抗する剛性値。\n"
            "典型値: 低降伏点鋼ブレース 50000〜500000 kN/m"
        ),
        8:  (
            "【Fe / 弾性限界力】弾性限界を超える力 [kN] です（一部モデルのみ使用）。\n"
            "Fy（降伏荷重）より小さい場合があります。"
        ),
        9:  (
            "【Fy / 降伏荷重】ダンパーが降伏する力 [kN] です。\n"
            "これ以上の力が作用すると塑性変形（エネルギー吸収）が始まります。\n"
            "典型値: 低降伏点鋼材ブレース 50〜2000 kN\n"
            "設計では「Fy ≦ ダンパー設計力 ≦ Fu」を確認します。"
        ),
        10: (
            "【Fu / 最大荷重】ダンパーの最大耐力 [kN] です。\n"
            "Fy < Fu の関係が必要です。\n"
            "典型値: Fy の 1.2〜1.5 倍程度"
        ),
        11: (
            "【α / 2次剛性比】降伏後の剛性 / 初期剛性の比（無次元）です。\n"
            "α = 0: 完全弾塑性 / α > 0: ひずみ硬化あり\n"
            "典型値: 低降伏点鋼材 0.01〜0.05（ほぼ完全弾塑性）"
        ),
    },
}


def _get_damper_field_labels(keyword: str) -> Dict[int, str]:
    """
    ダンパー定義の各フィールドに対する説明ラベルを返します（1-indexed）。
    SNAP テキストデータ仕様に準拠。
    """
    if keyword == "DVOD":
        # 粘性/オイルダンパー (Device Viscous/Oil Damper) — SNAP仕様 p.114
        return {
            1:  "種別 (0:未使用, 52:免震用オイル, 53:免震用粘性, 72:制振用オイル, 73:制振用粘性)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "減衰モデル (0:ダッシュポット単, 1:Voigt, 2:Maxwell, 3:D+M, 4:質量単, 5:回転方向)",
            6:  "質量 (t)",
            7:  "装置特性種別 (0:線形EL1, 1:バイリニア逆行EL2, 2:トリリニア逆行EL3, 3:曲線EF1)",
            8:  "C0（減衰係数）",
            9:  "Fc（リリーフ力）",
            10: "Fy（最大減衰力）",
            11: "Ve（基準速度）",
            12: "α（速度指数）",
            13: "β（温度依存指数）",
            14: "剛性",
            15: "取付け剛性",
            16: "装置高さ",
            17: "重量種別 (0:単位長さ重量, 1:重量)",
            18: "重量",
            19: "変動係数 下限温度",
            20: "変動係数 下限 τ",
            21: "変動係数 上限温度",
            22: "変動係数 上限 τ",
        }
    elif keyword == "DISD":
        # 免震用履歴型ダンパー (Device ISolated Damper) — SNAP仕様 p.111
        return {
            1:  "種別 (0:未使用, 51:免震用履歴型ダンパー)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "復元力特性種別 (0:BL2, 1:修正RO3, 2:標準TL3)",
            6:  "K0（初期剛性）",
            7:  "Qc",
            8:  "Qy（降伏荷重）",
            9:  "α（2次剛性比）",
            10: "β",
            11: "p1",
            12: "p2",
            13: "重量",
            14: "変動係数 下限温度",
            15: "変動係数 下限 τK0",
            16: "変動係数 下限 τQc",
            17: "変動係数 下限 τQy",
            18: "変動係数 上限温度",
            19: "変動係数 上限 τK0",
            20: "変動係数 上限 τQc",
            21: "変動係数 上限 τQy",
            22: "頭部付加曲げ分配率 Qh",
            23: "初期解析 (0:しない, 1:する)",
            24: "減衰",
        }
    elif keyword == "DSD":
        # 鋼材/摩擦ダンパー (Device Steel Damper) — SNAP仕様 p.112
        return {
            1:  "種別 (0:未使用, 1:ブレース, 2:間柱, 3:摩擦ダンパー)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "剛域の変形 (0:考慮しない, 1:考慮する)",
            6:  "種別 (0:BL2, 1:LY2, 2:LY3, 3:RO4, 4:VHD, 5:IK2, 6:MCB, 7:TL3, 8:MP3)",
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
            22: "装置高さ",
            23: "重量種別 (0:単位長さ重量, 1:重量)",
            24: "重量",
            25: "疲労損傷評価 計算 (0:しない, 1:する)",
            26: "疲労損傷評価 装置長さ",
            27: "疲労曲線 P1",
            28: "疲労曲線 P2",
            29: "頻度解析刻み幅",
            30: "初期解析 (0:しない, 1:する)",
            31: "減衰",
        }
    elif keyword == "DIS":
        # 免震支承材 (Device ISolator) — SNAP仕様 p.109
        return {
            1:  "種別 (101:NRB, 102:HDR, 103:LRB, 104:錫LRB, 105:鉄粉LRB, 121:弾性すべり, 122:剛すべり, 123:曲面すべり, 124:直動転がり)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "重量",
            6:  "変動係数 下限温度",
            7:  "変動係数 下限 τG,τK",
            8:  "変動係数 下限 τH,τQ",
            9:  "変動係数 下限 τU",
            10: "変動係数 上限温度",
            11: "変動係数 上限 τG,τK",
            12: "変動係数 上限 τH,τQ",
            13: "変動係数 上限 τU",
            14: "頭部付加曲げ Qh",
            15: "分配率 Pδ",
            16: "高減衰ゴム系・プラグ挿入型 復元力特性 (0:修正BL2, 1:修正HD2, 2:KA型, 3:修正TL3)",
            17: "Ke/Keq",
            18: "Ke",
            19: "すべり支承 Qd算出方法",
            20: "静止摩擦 倍率",
            21: "静止摩擦 回数",
            22: "圧縮耐力 考慮 (0:しない, 1:する)",
            23: "Pc",
            24: "減衰 鉛直",
            25: "減衰 水平",
        }
    elif keyword == "DOD":
        # オイルダンパー (Device Oil Damper) — SNAP仕様 p.115
        return {
            1:  "種別 (0:BDSD型ELS, 1:BDSV型ELB)",
            2:  "C0（減衰係数）",
            3:  "Fy（降伏荷重）",
            4:  "β（速度指数）",
            5:  "d",
            6:  "Vy",
            7:  "P1",
            8:  "P2",
            9:  "P3",
            10: "P4",
            11: "P5",
            12: "剛性",
            13: "取付け剛性",
            14: "装置高さ",
            15: "重量種別 (0:単位長さ重量, 1:重量)",
            16: "重量",
            17: "変動係数 下限 減衰1",
            18: "変動係数 下限 減衰2",
            19: "変動係数 下限 剛性",
            20: "変動係数 上限 減衰1",
            21: "変動係数 上限 減衰2",
            22: "変動係数 上限 剛性",
        }
    elif keyword == "DVD":
        # 粘性ダンパー (Device Viscous Damper) — SNAP仕様 p.116
        return {
            1:  "種別 (0:未使用, 54:免震用減衰こま, 74:制振用減衰こま)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "種別 (0:EFO型, 1:EO2型, 2:EAV型, 3:EAR型, 4:ELM型, 5:EO3型)",
            6:  "質量",
            7:  "せん断断面積",
            8:  "せん断間隔",
            9:  "振動数",
            10: "荷重",
            11: "P1",
            12: "P2",
            13: "P3",
            14: "P4",
            15: "P5",
            16: "剛性",
            17: "取付け剛性",
            18: "装置高さ",
            19: "重量種別 (0:単位長さ重量, 1:重量)",
            20: "重量",
            21: "温度 標準",
            22: "温度 下限",
            23: "温度 上限",
            24: "変動係数 下限 質量",
            25: "変動係数 下限 減衰",
            26: "変動係数 下限 剛性",
            27: "変動係数 下限 荷重",
            28: "変動係数 上限 質量",
            29: "変動係数 上限 減衰",
            30: "変動係数 上限 剛性",
            31: "変動係数 上限 荷重",
        }
    elif keyword == "DVED":
        # 粘弾性ダンパー (Device Visco-Elastic Damper) — SNAP仕様 p.118
        return {
            1:  "種別 (0:未使用, 75:制振用粘弾性ダンパー)",
            2:  "k-DB 会社番号",
            3:  "k-DB 製品番号",
            4:  "k-DB 型番",
            5:  "種別 (0:VEY, 1:VET, 2:VS1, 3:VS2, 4:VS3, 5:VS4, 6:VT2, 7:VE1, 8:VEH, 9:VEJ)",
            6:  "粘弾性体面積",
            7:  "粘弾性体厚さ",
            8:  "振動数",
            9:  "すべり荷重",
            10: "取付け剛性",
            11: "最大ひずみ",
            12: "装置高さ",
            13: "重量種別 (0:単位長さ重量, 1:重量)",
            14: "重量",
            15: "温度 標準",
            16: "温度 下限",
            17: "温度 上限",
            18: "変動係数 下限 減衰",
            19: "変動係数 下限 剛性",
            20: "変動係数 下限 荷重",
            21: "変動係数 上限 減衰",
            22: "変動係数 上限 剛性",
            23: "変動係数 上限 荷重",
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
            6:  "t",
            8:  "kN·s/mm",
            9:  "kN",
            10: "kN",
            11: "mm/s",
            12: "—（0〜1）",
            13: "—",
            14: "kN/mm",
            15: "kN/mm",
            16: "mm",
            18: "kN/mm または kN",
        }
    elif keyword == "DISD":
        return {
            6:  "kN/mm",
            7:  "kN",
            8:  "kN",
            9:  "—（0〜1）",
            13: "kN",
        }
    elif keyword == "DSD":
        return {
            7:  "kN/mm",
            8:  "kN",
            9:  "kN",
            10: "kN",
            11: "—（0〜1）",
            18: "kN/mm",
            22: "mm",
        }
    elif keyword == "DIS":
        return {
            5:  "kN",
            6:  "℃",
            10: "℃",
            24: "—（0〜1）",
            25: "—（0〜1）",
        }
    elif keyword == "DOD":
        return {
            2:  "kN·s/mm",
            3:  "kN",
            12: "kN/mm",
            13: "kN/mm",
            14: "mm",
        }
    elif keyword == "DVD":
        return {
            6:  "t",
            7:  "mm²",
            8:  "mm",
            10: "kN",
            16: "kN/mm",
            17: "kN/mm",
            18: "mm",
        }
    elif keyword == "DVED":
        return {
            6:  "mm²",
            7:  "mm",
            9:  "kN",
            10: "kN/mm",
            12: "mm",
        }
    return {}
