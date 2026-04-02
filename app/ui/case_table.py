"""
app/ui/case_table.py
解析ケース一覧テーブルウィジェット。

ケースの追加・削除・複製・実行要求・編集をここから行います。

UX改善①新: フィルターバーに「全選択/全解除」ボタン追加。
         テキスト検索・ステータスフィルターの右側に配置し、
         表示中のケースをワンクリックで全選択・全解除できます。
         絞り込み後の複数ケースを一括削除・一括実行する際に便利です。
         ステータスフィルター使用中もヘッダーバッジに件数を表示します。

UX改善②新: コンテキストメニューに「状態をリセット（再実行可能に）」アクション追加。
         完了済みまたはエラーのケースを PENDING に戻します。
         同じパラメータで再解析したいとき、編集ダイアログを開かずに
         右クリック一発でリセットできます。

改善⑧: 行ホバー時にケースのパラメータ・結果をリッチHTMLツールチップで表示。
        マウスオーバーするだけで編集ダイアログを開かずに概要を確認できます。

UX改善A: ステータスフィルタードロップダウン追加。
         テキスト検索バーの右側に「全て/待機中/実行中/完了/エラー」の
         ドロップダウンを追加し、状態別の絞り込みをワンクリックで実行できます。

UX改善B: グループ別行カラーコーディング。
         同じグループに属するケースの「グループ」列セルを同じアクセントカラーで
         色付けすることで、グループ構造を直感的に把握できます。

UX改善C: 複数ケース選択時の件数バッジ表示と一括削除の強化。
         複数ケースを選択したとき、アクションバーに「3件選択中」のバッジを
         表示し、まとめて削除できるようにします。

UX改善②: F2キーでケース名クイックリネーム。
         重いCaseEditDialogを開かずに、F2キーまたはコンテキストメニュー
         「名前を変更…」からケース名だけをその場でテキスト入力できます。

UX改善③: ケーステーブル列幅の永続化。
         列幅の変更をQSettingsに自動保存し、次回起動時に復元します。
         毎回デフォルト幅にリセットされる煩わしさを解消します。

UX改善⑥新: ケースの並び順変更ボタン（↑上へ / ↓下へ）。
         アクションバーに「↑ 上へ」「↓ 下へ」ボタンを追加し、
         選択ケースをワンクリックで隣のケースと入れ替えて順序を変更できます。
         パラメータスタディの優先順位や実行順序の整理に役立ちます。

UX改善⑦新: メモ列ダブルクリックで直接インライン編集。
         メモ列（最右列）をダブルクリックすると、重い CaseEditDialog を
         開かずに小さな入力ダイアログでメモだけを素早く更新できます。
         それ以外の列のダブルクリックは従来通り全編集ダイアログを開きます。

UX改善③新: 解析完了進捗プログレスバー。
         ヘッダーとフィルターの間にスリムなプログレスバーを追加し、
         「X/N件 完了」を視覚的に一目で把握できます。
         解析が進むたびに自動更新され、全完了で緑に変わります。

UX改善⑧新: 性能基準判定テキストマーク（✅/⚠/❌）。
         性能基準が設定されており解析結果がある場合、「最大層間変形角」「最大加速度」
         列の数値テキストの先頭に ✅/⚠/❌ マークを付与します。
         背景色だけでなくテキストにもマークを出すことで、印刷時やモノクロ表示でも
         合否が一目で分かります。また、右クリックメニューにも判定情報が表示されます。

UX改善⑨新: アクションバーに「複製して編集」ボタンを追加。
         Ctrl+Shift+D のショートカットとコンテキストメニューには既に「複製して編集」
         機能がありましたが、アクションバーにボタンを追加することで機能を発見しやすく
         します。「複製」ボタンの隣に配置し、押すと複製後すぐに編集ダイアログが開きます。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.models import AnalysisCase, AnalysisCaseStatus, Project
from .case_edit_dialog import CaseEditDialog
from .theme import ThemeManager, STATUS_COLORS

import qtawesome as qta

# テーブル列定義（「判定」列は削除）
_COLUMNS = ["ケース名", "グループ", "モデルファイル", "状態", "最大層間変形角", "最大加速度", "メモ"]
_COL_NAME = 0
_COL_GROUP = 1
_COL_MODEL = 2
_COL_STATUS = 3
_COL_DRIFT = 4
_COL_ACC = 5
_COL_NOTES = 6

# UX改善A: ステータスフィルター選択肢 (表示ラベル, 内部ステータス名 or "")
_STATUS_FILTER_ITEMS = [
    ("全て",   ""),
    ("⏳ 待機中", "PENDING"),
    ("▶ 実行中", "RUNNING"),
    ("✅ 完了",  "COMPLETED"),
    ("❌ エラー", "ERROR"),
]

# UX改善B: グループ別アクセントカラーパレット（ライト用・ダーク用）
_GROUP_COLORS_LIGHT = [
    QColor("#e3f2fd"),  # 水色
    QColor("#f3e5f5"),  # 薄紫
    QColor("#e8f5e9"),  # 薄緑
    QColor("#fff8e1"),  # 薄黄
    QColor("#fce4ec"),  # 薄ピンク
    QColor("#e0f7fa"),  # 薄シアン
    QColor("#f9fbe7"),  # 薄黄緑
    QColor("#ede7f6"),  # 薄ラベンダー
]
_GROUP_COLORS_DARK = [
    QColor("#1a3a5c"),  # 濃い水色
    QColor("#3b1f4a"),  # 濃い紫
    QColor("#1b3a2a"),  # 濃い緑
    QColor("#3a3000"),  # 濃い黄
    QColor("#4a1520"),  # 濃いピンク
    QColor("#003a3e"),  # 濃いシアン
    QColor("#2d3a00"),  # 濃い黄緑
    QColor("#2a1f4a"),  # 濃いラベンダー
]


def _get_status_color(status: AnalysisCaseStatus) -> QColor:
    """現在のテーマに応じたステータス背景色を返します。"""
    theme = "dark" if ThemeManager.is_dark() else "light"
    status_name = status.name  # e.g. "PENDING", "RUNNING", etc.
    return STATUS_COLORS[theme].get(status_name, QColor("transparent"))


class CaseTableWidget(QWidget):
    """
    解析ケース一覧を表示するテーブルウィジェット。

    Signals
    -------
    caseSelectionChanged(case_id: str)
        選択されたケースの ID を通知します。
    runRequested(case_id: str)
        ユーザーがケースの実行を要求したときに発火します。
    """

    caseSelectionChanged = Signal(str)
    runRequested = Signal(str)
    projectModified = Signal()  # 改善⑤: ケース追加・編集・削除・複製・グループ変更時に発火

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._project: Optional[Project] = None
        # UX改善B: グループ名→カラーインデックスのマッピング（グループ追加順で割り当て）
        self._group_color_map: dict[str, int] = {}
        # UX改善②: s8iモデルロード状態を保持（追加ボタン有効/無効制御に使用）
        self._model_loaded: bool = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        """表示するプロジェクトを設定します。"""
        self._project = project
        self.refresh()

    def set_model_loaded(self, loaded: bool) -> None:
        """
        UX改善②: s8iモデルのロード状態を通知します。

        モデルが読み込まれていない場合は「追加」ボタンを無効化し、
        ユーザーにSTEP1でのモデル読み込みを促します。
        モデルロード後は自動的にボタンを有効化します。
        """
        self._model_loaded = loaded
        self._refresh_add_button_state()

    def refresh(self) -> None:
        """テーブルを再描画します。"""
        if self._project is None:
            self._table.setRowCount(0)
            return
        self._populate(self._project.cases)

    def add_case(self) -> None:
        """編集ダイアログを開いて新規ケースを追加します。"""
        if self._project is None:
            return
        # UX改善②: モデル未ロード時はガイダンスダイアログを表示してブロック
        if not self._model_loaded:
            QMessageBox.information(
                self,
                "s8iモデルを先に読み込んでください",
                "解析ケースを追加する前に、\nSTEP1 でSNAP入力ファイル (.s8i) を読み込んでください。\n\n"
                "モデルを読み込むと、ダンパー定義や配置情報を参照しながら\nケースを設定できるようになります。",
            )
            return
        case = AnalysisCase()
        s8i = self._project.s8i_model if self._project else None
        dlg = CaseEditDialog(case, s8i_model=s8i, parent=self)
        if dlg.exec():
            self._project.add_case(case)
            self.refresh()
            # 追加したケースを選択状態にする
            for row in range(self._table.rowCount()):
                if self._table.item(row, _COL_NAME) and \
                   self._table.item(row, _COL_NAME).data(Qt.UserRole) == case.id:
                    self._table.selectRow(row)
                    break
            self.projectModified.emit()  # 改善⑤

    def selected_case_id(self) -> Optional[str]:
        """現在選択されているケースの ID を返します。"""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, _COL_NAME)
        return item.data(Qt.UserRole) if item else None

    def selected_case_ids(self) -> list[str]:
        """選択されている全ケースの ID リストを返します。"""
        rows = set()
        for idx in self._table.selectedIndexes():
            rows.add(idx.row())
        ids = []
        for row in sorted(rows):
            item = self._table.item(row, _COL_NAME)
            if item:
                cid = item.data(Qt.UserRole)
                if cid:
                    ids.append(cid)
        return ids

    def _refresh_add_button_state(self) -> None:
        """
        UX改善②: 「追加」ボタンの有効/無効をモデルロード状態に合わせて更新します。

        s8iモデルが読み込まれている場合のみボタンを有効化します。
        ツールチップも状態に合わせて切り替えます。
        """
        loaded = self._model_loaded
        # ヘッダーの「追加」ボタン
        if hasattr(self, '_btn_add_header'):
            self._btn_add_header.setEnabled(loaded)
            if loaded:
                self._btn_add_header.setToolTip("新しい解析ケースを追加します")
            else:
                self._btn_add_header.setToolTip(
                    "STEP1でs8iファイルを読み込むと有効になります"
                )
        # 空状態の「最初のケースを追加する」ボタン
        if hasattr(self, '_empty_add_btn'):
            self._empty_add_btn.setEnabled(loaded)
            if loaded:
                self._empty_add_btn.setText("＋ 最初のケースを追加する")
                self._empty_add_btn.setToolTip("")
            else:
                self._empty_add_btn.setText("⬅ STEP1でモデルを読み込んでください")
                self._empty_add_btn.setToolTip(
                    "STEP1（モデル設定）でs8iファイルを読み込むと、\nここからケースを追加できるようになります。"
                )

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ヘッダー
        header = QHBoxLayout()
        self._header_label = QLabel("<b>解析ケース</b>")
        header.addWidget(self._header_label)
        header.addStretch()

        self._btn_add_header = QPushButton("追加")
        self._btn_add_header.setIcon(qta.icon("fa5s.plus", color="white"))
        self._btn_add_header.setStyleSheet("QPushButton { font-weight: bold; padding: 4px 12px; }")
        self._btn_add_header.clicked.connect(self.add_case)
        # UX改善②: 初期状態は無効（モデルロード後に有効化）
        self._btn_add_header.setEnabled(False)
        self._btn_add_header.setToolTip(
            "STEP1でs8iファイルを読み込むと有効になります"
        )
        header.addWidget(self._btn_add_header)

        btn_del = QPushButton("削除")
        btn_del.setIcon(qta.icon("fa5s.trash-alt", color="#F44336"))
        btn_del.setStyleSheet("QPushButton { font-weight: bold; padding: 4px 12px; }")
        btn_del.clicked.connect(self._delete_selected)
        header.addWidget(btn_del)

        layout.addLayout(header)

        # ---- UX改善③新: 解析完了進捗プログレスバー ----
        self._completion_bar = QProgressBar()
        self._completion_bar.setMaximumHeight(7)
        self._completion_bar.setTextVisible(False)
        self._completion_bar.setRange(0, 100)
        self._completion_bar.setValue(0)
        self._completion_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 3px;
                background-color: palette(mid);
                margin: 0px 0px 2px 0px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
        """)
        self._completion_bar.setToolTip("完了した解析ケースの割合（完了件数 / 全件数）")
        layout.addWidget(self._completion_bar)

        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#333333"

        # ---- 改善①: 検索/フィルターバー ----
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 2, 0, 2)
        filter_icon = QLabel()
        filter_icon.setPixmap(qta.icon("fa5s.search", color=icon_color).pixmap(16, 16))
        filter_row.addWidget(filter_icon)
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("ケース名・グループ・メモで絞り込み…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter_edit)

        # ---- UX改善A: ステータスフィルタードロップダウン ----
        status_icon = QLabel()
        status_icon.setPixmap(qta.icon("fa5s.filter", color=icon_color).pixmap(14, 14))
        status_icon.setToolTip("ステータスで絞り込み")
        filter_row.addWidget(status_icon)
        self._status_filter = QComboBox()
        self._status_filter.setMaximumWidth(120)
        self._status_filter.setToolTip(
            "解析状態でケースを絞り込みます。\n"
            "「全て」を選ぶとすべてのケースを表示します。"
        )
        for label, _ in _STATUS_FILTER_ITEMS:
            self._status_filter.addItem(label)
        self._status_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._status_filter)

        # ---- UX改善①新: 全選択/全解除ボタン ----
        _sel_style = "QPushButton { font-size: 11px; padding: 1px 5px; }"
        btn_sel_all = QPushButton("全選択")
        btn_sel_all.setMaximumWidth(52)
        btn_sel_all.setFixedHeight(22)
        btn_sel_all.setToolTip(
            "表示中の全ケースを選択します\n"
            "絞り込み中はフィルター後のケースだけを選択します"
        )
        btn_sel_all.setStyleSheet(_sel_style)
        btn_sel_all.clicked.connect(self._table.selectAll)
        filter_row.addWidget(btn_sel_all)

        btn_desel_all = QPushButton("全解除")
        btn_desel_all.setMaximumWidth(52)
        btn_desel_all.setFixedHeight(22)
        btn_desel_all.setToolTip("すべての選択を解除します")
        btn_desel_all.setStyleSheet(_sel_style)
        btn_desel_all.clicked.connect(self._table.clearSelection)
        filter_row.addWidget(btn_desel_all)

        layout.addLayout(filter_row)

        # スタックウィジェット（空状態 / テーブル切り替え）
        self._stack = QStackedWidget()

        # ---- 空状態ガイダンス (index 0) ----
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setAlignment(Qt.AlignCenter)

        empty_icon = QLabel()
        empty_icon.setPixmap(qta.icon("fa5s.clipboard-list", color=icon_color).pixmap(64, 64))
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_icon)

        empty_title = QLabel("解析ケースがありません")
        empty_title_font = QFont()
        empty_title_font.setPointSize(13)
        empty_title_font.setBold(True)
        empty_title.setFont(empty_title_font)
        empty_title.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_title)

        empty_desc = QLabel(
            "「＋ 追加」ボタンまたはメニューの「解析 → ケースを追加」で\n"
            "新しい解析ケースを作成できます。\n"
            "パラメータスイープで一括生成することも可能です。"
        )
        empty_desc.setAlignment(Qt.AlignCenter)
        empty_desc.setStyleSheet("color: gray; padding: 8px;")
        empty_desc.setWordWrap(True)
        empty_layout.addWidget(empty_desc)

        self._empty_add_btn = QPushButton("＋ 最初のケースを追加する")
        self._empty_add_btn.setMinimumHeight(40)
        self._empty_add_btn.setMaximumWidth(280)
        self._empty_add_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                font-weight: bold;
                padding: 8px 16px;
                border: 2px solid palette(mid);
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            QPushButton:disabled {
                color: palette(mid);
                border-color: palette(mid);
            }
        """)
        # UX改善②: 初期状態は無効（モデルロード後に有効化）
        self._empty_add_btn.setEnabled(False)
        self._empty_add_btn.setToolTip(
            "STEP1でs8iファイルを読み込むと有効になります"
        )
        self._empty_add_btn.clicked.connect(self.add_case)
        empty_btn_layout = QHBoxLayout()
        empty_btn_layout.setAlignment(Qt.AlignCenter)
        empty_btn_layout.addWidget(self._empty_add_btn)
        empty_layout.addLayout(empty_btn_layout)

        self._stack.addWidget(empty_widget)  # index 0: 空状態

        # ---- テーブル (index 1) ----
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_NAME, QHeaderView.ResizeToContents
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        # UX改善5: 列ヘッダークリックでソート（case_id は UserRole に保存されるので安全）
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().setToolTip(
            "列ヘッダーをクリックするとその列でソートできます。\n"
            "再度クリックで昇順/降順が切り替わります。"
        )
        self._table.setToolTip(
            "キーボードショートカット:\n"
            "  Enter     — 選択ケースを編集\n"
            "  Delete    — 選択ケースを削除\n"
            "  F5        — 選択ケースを実行\n"
            "  Ctrl+D    — 選択ケースを複製\n"
            "  右クリック — コンテキストメニュー"
        )
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        # 改善⑧: ホバーツールチップ用イベントフィルターをビューポートに設置
        self._table.viewport().installEventFilter(self)
        self._table.setMouseTracking(True)
        self._table.viewport().setMouseTracking(True)
        self._stack.addWidget(self._table)  # index 1: テーブル

        # UX改善③: 列幅変更時に自動保存
        self._table.horizontalHeader().sectionResized.connect(self._save_column_widths)
        # UX改善③: 起動時に列幅を復元
        self._restore_column_widths()

        # ---- キーボードショートカット ----
        # Enter: 選択ケースを編集
        sc_enter = QShortcut(QKeySequence(Qt.Key_Return), self._table)
        sc_enter.setContext(Qt.WidgetShortcut)
        sc_enter.activated.connect(self._shortcut_edit)

        sc_enter2 = QShortcut(QKeySequence(Qt.Key_Enter), self._table)
        sc_enter2.setContext(Qt.WidgetShortcut)
        sc_enter2.activated.connect(self._shortcut_edit)

        # Delete/Backspace: 選択ケースを削除
        sc_del = QShortcut(QKeySequence(Qt.Key_Delete), self._table)
        sc_del.setContext(Qt.WidgetShortcut)
        sc_del.activated.connect(self._delete_selected)

        sc_bs = QShortcut(QKeySequence(Qt.Key_Backspace), self._table)
        sc_bs.setContext(Qt.WidgetShortcut)
        sc_bs.activated.connect(self._delete_selected)

        # F5: 選択ケースを実行
        sc_run = QShortcut(QKeySequence(Qt.Key_F5), self._table)
        sc_run.setContext(Qt.WidgetShortcut)
        sc_run.activated.connect(self._shortcut_run)

        # Ctrl+D: 選択ケースを複製
        sc_dup = QShortcut(QKeySequence("Ctrl+D"), self._table)
        sc_dup.setContext(Qt.WidgetShortcut)
        sc_dup.activated.connect(self._shortcut_duplicate)

        # UX改善②新: Ctrl+Shift+D: 複製して編集
        sc_dup_edit = QShortcut(QKeySequence("Ctrl+Shift+D"), self._table)
        sc_dup_edit.setContext(Qt.WidgetShortcut)
        sc_dup_edit.activated.connect(self._shortcut_duplicate_and_edit)

        # UX改善②: F2: ケース名クイックリネーム
        sc_rename = QShortcut(QKeySequence(Qt.Key_F2), self._table)
        sc_rename.setContext(Qt.WidgetShortcut)
        sc_rename.activated.connect(self._shortcut_rename)

        layout.addWidget(self._stack)

        # ---- 改善②: インラインアクションバー ----
        action_frame = QFrame()
        action_frame.setFrameShape(QFrame.StyledPanel)
        action_layout = QHBoxLayout(action_frame)
        action_layout.setContentsMargins(4, 4, 4, 4)
        action_layout.setSpacing(6)

        self._btn_run = QPushButton(" 実行")
        self._btn_run.setIcon(qta.icon("fa5s.play", color="#4CAF50"))
        self._btn_run.setToolTip("選択したケースを解析実行します  [F5]")
        self._btn_run.setEnabled(False)
        self._btn_run.clicked.connect(self._shortcut_run)
        action_layout.addWidget(self._btn_run)

        self._btn_edit = QPushButton(" 編集")
        self._btn_edit.setIcon(qta.icon("fa5s.edit", color=icon_color))
        self._btn_edit.setToolTip("選択したケースのパラメータを編集します  [Enter]")
        self._btn_edit.setEnabled(False)
        self._btn_edit.clicked.connect(self._shortcut_edit)
        action_layout.addWidget(self._btn_edit)

        self._btn_dup = QPushButton(" 複製")
        self._btn_dup.setIcon(qta.icon("fa5s.copy", color=icon_color))
        self._btn_dup.setToolTip("選択したケースを複製して新しいケースを作成します  [Ctrl+D]")
        self._btn_dup.setEnabled(False)
        self._btn_dup.clicked.connect(self._shortcut_duplicate)
        action_layout.addWidget(self._btn_dup)

        # UX改善⑨新: 「複製して編集」ボタンをアクションバーに追加
        # Ctrl+Shift+D とコンテキストメニューには既に機能があったが、
        # 視認性の高いボタンを追加して機能を発見しやすくします。
        self._btn_dup_edit = QPushButton(" 複製して編集")
        self._btn_dup_edit.setIcon(qta.icon("fa5s.clone", color="#1976d2"))
        self._btn_dup_edit.setToolTip(
            "選択ケースを複製してすぐに編集ダイアログを開きます  [Ctrl+Shift+D]\n\n"
            "使い方:\n"
            "  1. 既存ケースを選択\n"
            "  2. このボタンをクリック\n"
            "  3. 複製されたケースの設定ダイアログが開く\n"
            "  4. パラメータを変更して「OK」\n\n"
            "最良ケースをベースに少しだけパラメータを変えたいときに便利です。"
        )
        self._btn_dup_edit.setEnabled(False)
        self._btn_dup_edit.clicked.connect(self._shortcut_duplicate_and_edit)
        action_layout.addWidget(self._btn_dup_edit)

        # UX改善⑥新: 並び順変更ボタン（↑上へ / ↓下へ）
        self._btn_move_up = QPushButton("↑")
        self._btn_move_up.setToolTip(
            "選択ケースを1つ上に移動します\n"
            "解析実行順やケースの優先順位を整理できます"
        )
        self._btn_move_up.setFixedWidth(32)
        self._btn_move_up.setEnabled(False)
        self._btn_move_up.clicked.connect(self._move_case_up)
        action_layout.addWidget(self._btn_move_up)

        self._btn_move_down = QPushButton("↓")
        self._btn_move_down.setToolTip(
            "選択ケースを1つ下に移動します\n"
            "解析実行順やケースの優先順位を整理できます"
        )
        self._btn_move_down.setFixedWidth(32)
        self._btn_move_down.setEnabled(False)
        self._btn_move_down.clicked.connect(self._move_case_down)
        action_layout.addWidget(self._btn_move_down)

        action_layout.addStretch()

        self._action_hint = QLabel("ケースを選択してください")
        self._action_hint.setStyleSheet("color: gray; font-size: 11px;")
        self._action_hint.setTextFormat(Qt.RichText)  # UX改善C: リッチテキスト対応
        action_layout.addWidget(self._action_hint)

        layout.addWidget(action_frame)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 改善⑧: ホバーツールチップ
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """ビューポートのToolTipイベントをインターセプトしてリッチツールチップを表示します。"""
        if obj is self._table.viewport() and event.type() == QEvent.ToolTip:
            pos: QPoint = event.pos()
            index = self._table.indexAt(pos)
            if index.isValid():
                item = self._table.item(index.row(), _COL_NAME)
                if item:
                    case_id = item.data(Qt.UserRole)
                    if case_id and self._project:
                        case = self._project.get_case(case_id)
                        if case:
                            tip_html = self._build_tooltip_html(case)
                            QToolTip.showText(
                                self._table.viewport().mapToGlobal(pos),
                                tip_html,
                                self._table.viewport(),
                            )
                            return True
            QToolTip.hideText()
            return True
        return super().eventFilter(obj, event)

    def _build_tooltip_html(self, case) -> str:
        """ケース情報をHTML形式のツールチップ文字列に変換します。"""
        import os

        lines = []
        lines.append(
            "<div style='font-family: Meiryo, sans-serif; font-size: 12px; "
            "min-width: 260px; max-width: 400px;'>"
        )
        # ---- ケース名 ----
        lines.append(
            f"<b style='font-size:13px;'>{case.name}</b>"
        )
        lines.append("<hr style='margin:4px 0;'>")

        # ---- 基本情報 ----
        lines.append("<table style='border-spacing: 2px 1px;'>")
        status_label = case.get_status_label() if hasattr(case, "get_status_label") else str(case.status)
        model_name = os.path.basename(case.model_path) if case.model_path else "（未設定）"
        lines.append(f"<tr><td style='color:gray;'>状態:</td><td><b>{status_label}</b></td></tr>")
        lines.append(f"<tr><td style='color:gray;'>モデル:</td><td>{model_name}</td></tr>")
        if case.notes:
            lines.append(f"<tr><td style='color:gray;'>メモ:</td><td>{case.notes[:60]}</td></tr>")
        lines.append("</table>")

        # ---- 解析結果サマリー ----
        rs = case.result_summary
        if rs:
            lines.append("<hr style='margin:4px 0;'>")
            lines.append("<b style='font-size:11px; color:gray;'>解析結果</b>")
            lines.append("<table style='border-spacing: 2px 1px;'>")
            _result_labels = [
                ("max_disp",        "最大相対変位",    "m"),
                ("max_vel",         "最大相対速度",    "m/s"),
                ("max_acc",         "最大絶対加速度",  "m/s²"),
                ("max_story_drift", "最大層間変形角",  "rad"),
                ("max_story_disp",  "最大層間変形",    "m"),
                ("shear_coeff",     "せん断力係数",    "—"),
                ("max_otm",         "最大転倒ﾓｰﾒﾝﾄ", "kN·m"),
            ]
            for key, label, unit in _result_labels:
                val = rs.get(key)
                if val is not None:
                    try:
                        val_str = f"{float(val):.4g} {unit}"
                    except (TypeError, ValueError):
                        val_str = str(val)
                    lines.append(
                        f"<tr><td style='color:gray;'>{label}:</td>"
                        f"<td style='text-align:right; padding-left:8px;'>{val_str}</td></tr>"
                    )
            lines.append("</table>")

        # ---- ダンパーパラメータ ----
        dp = case.damper_params
        if dp:
            lines.append("<hr style='margin:4px 0;'>")
            lines.append("<b style='font-size:11px; color:gray;'>ダンパーパラメータ変更</b>")
            lines.append("<table style='border-spacing: 2px 1px;'>")
            for def_name, overrides in list(dp.items())[:3]:
                if isinstance(overrides, dict) and overrides:
                    vals_str = ", ".join(f"[{k}]={v}" for k, v in list(overrides.items())[:4])
                    lines.append(
                        f"<tr><td style='color:gray;'>{def_name}:</td>"
                        f"<td style='padding-left:6px;'>{vals_str}</td></tr>"
                    )
            if len(dp) > 3:
                lines.append(f"<tr><td colspan='2' style='color:gray;'>… 他{len(dp)-3}定義</td></tr>")
            lines.append("</table>")

        lines.append(
            "<p style='color:gray; font-size:10px; margin-top:4px;'>"
            "Enterキーまたはダブルクリックで編集</p>"
        )
        lines.append("</div>")
        return "".join(lines)

    def _on_filter_changed(self, text: str) -> None:
        """フィルターテキスト変更時にテーブルを再描画します。"""
        self.refresh()

    def _get_filter_text(self) -> str:
        """現在のフィルターテキストを返します（小文字）。"""
        return self._filter_edit.text().strip().lower()

    def _get_status_filter(self) -> str:
        """UX改善A: 現在選択中のステータスフィルター（空文字=全て）を返します。"""
        idx = self._status_filter.currentIndex()
        if 0 <= idx < len(_STATUS_FILTER_ITEMS):
            return _STATUS_FILTER_ITEMS[idx][1]
        return ""

    def _case_matches_filter(self, case, group_name: str) -> bool:
        """ケースがフィルターテキスト・ステータスフィルターに一致するか判定します。"""
        # UX改善A: ステータスフィルタードロップダウンによる絞り込み
        status_filter = self._get_status_filter()
        if status_filter and case.status.name != status_filter:
            return False
        # テキスト検索
        text = self._get_filter_text()
        if not text:
            return True
        targets = [
            case.name.lower(),
            group_name.lower(),
            case.notes.lower() if case.notes else "",
            case.get_status_label().lower() if hasattr(case, "get_status_label") else "",
        ]
        return any(text in t for t in targets)

    def _populate(self, cases: list) -> None:
        # UX改善5: 行挿入中はソートを一時停止してパフォーマンスと一貫性を確保
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        # ヘッダーラベルに件数を表示
        total_count = len(cases)

        # UX改善③新: 完了進捗プログレスバーを更新
        if total_count > 0:
            completed_count = sum(
                1 for c in cases if c.status.name == "COMPLETED"
            )
            pct = int(completed_count / total_count * 100)
            self._completion_bar.setValue(pct)
            # 全件完了で青に、未完了は緑で表示
            if completed_count == total_count:
                self._completion_bar.setStyleSheet("""
                    QProgressBar { border: none; border-radius: 3px;
                                   background-color: palette(mid);
                                   margin: 0px 0px 2px 0px; }
                    QProgressBar::chunk { background-color: #1976d2;
                                          border-radius: 3px; }
                """)
            else:
                self._completion_bar.setStyleSheet("""
                    QProgressBar { border: none; border-radius: 3px;
                                   background-color: palette(mid);
                                   margin: 0px 0px 2px 0px; }
                    QProgressBar::chunk { background-color: #4CAF50;
                                          border-radius: 3px; }
                """)
            self._completion_bar.setToolTip(
                f"解析完了: {completed_count} / {total_count} ケース  ({pct}%)\n"
                "全件完了すると青に変わります"
            )
        else:
            self._completion_bar.setValue(0)
            self._completion_bar.setToolTip("解析ケースがありません")

        if total_count == 0:
            self._header_label.setText("<b>解析ケース</b>")
            self._stack.setCurrentIndex(0)  # 空状態ガイダンスを表示
            self._table.setSortingEnabled(True)
            return

        # グループ名マップを事前構築
        group_of: dict = {}
        if self._project:
            for gname, cids in self._project.case_groups.items():
                for cid in cids:
                    group_of[cid] = gname

        # フィルタリング
        filtered_cases = [
            c for c in cases
            if self._case_matches_filter(c, group_of.get(c.id, ""))
        ]
        filtered_count = len(filtered_cases)

        # UX改善①新: テキスト検索またはステータスフィルターが有効な場合に件数バッジを表示
        _any_filter_active = bool(self._get_filter_text() or self._get_status_filter())

        if filtered_count == 0 and total_count > 0:
            # フィルター結果が0件 → テーブルを表示するが行なし
            if _any_filter_active:
                self._header_label.setText(
                    f"<b>解析ケース</b>　"
                    f"<span style='color:orange;font-weight:normal;'>0件（{total_count}件中）</span>"
                )
                self._stack.setCurrentIndex(1)
                self._table.setSortingEnabled(True)  # UX改善5
                return

        if _any_filter_active:
            # UX改善①新: フィルター件数バッジ（テキスト or ステータスフィルター両対応）
            self._header_label.setText(
                f"<b>解析ケース</b>　"
                f"<span style='color:gray;font-weight:normal;'>"
                f"{filtered_count}件表示 / {total_count}件中</span>"
            )
        else:
            self._header_label.setText(
                f"<b>解析ケース</b>　"
                f"<span style='color:gray;font-weight:normal;'>({total_count}件)</span>"
            )
        self._stack.setCurrentIndex(1)  # テーブルを表示
        for case in filtered_cases:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._set_row(row, case)
        # UX改善5: 全行挿入後にソートを再有効化
        self._table.setSortingEnabled(True)

    def _set_row(self, row: int, case: AnalysisCase) -> None:
        import os

        def make_item(text: str, case_id: str = "") -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if case_id:
                item.setData(Qt.UserRole, case_id)
            return item

        color = _get_status_color(case.status)

        name_item = make_item(case.name, case.id)

        # グループ名を取得
        group_name = ""
        if self._project:
            for gname, cids in self._project.case_groups.items():
                if case.id in cids:
                    group_name = gname
                    break
        group_item = make_item(group_name)

        # UX改善B: グループ別アクセントカラーをグループ列セルに適用
        if group_name:
            if group_name not in self._group_color_map:
                # 新しいグループに色を割り当て（パレットを循環使用）
                self._group_color_map[group_name] = len(self._group_color_map)
            color_idx = self._group_color_map[group_name] % len(_GROUP_COLORS_LIGHT)
            is_dark = ThemeManager.is_dark()
            group_color = (_GROUP_COLORS_DARK if is_dark else _GROUP_COLORS_LIGHT)[color_idx]
            group_item.setBackground(group_color)
            group_item.setToolTip(f"グループ: {group_name}")

        model_item = make_item(os.path.basename(case.model_path) if case.model_path else "（未設定）")
        status_item = make_item(case.get_status_label())

        drift_val = case.result_summary.get("max_drift") if case.result_summary else None
        acc_val = case.result_summary.get("max_acc") if case.result_summary else None

        drift_item = make_item(
            f"{drift_val:.5f}" if drift_val is not None else ""
        )
        acc_item = make_item(
            f"{acc_val:.3f}" if acc_val is not None else ""
        )
        notes_item = make_item(case.notes)

        all_items = [name_item, group_item, model_item, status_item,
                     drift_item, acc_item, notes_item]
        for item in all_items:
            item.setBackground(color)

        # UX改善④新: 解析結果セルに性能基準との対比でOK/NG色を適用
        # ケースが完了済みかつ値がある場合のみ、基準値との比較で色付けする
        # ステータス色（薄い背景）の上に、より濃い色で上書きして視認性を高める
        if case.result_summary and self._project and hasattr(self._project, "criteria"):
            criteria = self._project.criteria
            is_dark = ThemeManager.is_dark()
            _OK_COLOR  = QColor("#1b5e20") if is_dark else QColor("#c8e6c9")  # 深緑 / 薄緑
            _NG_COLOR  = QColor("#b71c1c") if is_dark else QColor("#ffcdd2")  # 深赤 / 薄赤
            _WARN_COLOR = QColor("#4e3b00") if is_dark else QColor("#fff9c4")  # 深黄 / 薄黄

            # criteria.items から key→CriterionItem のマップを構築
            criteria_map = {}
            if hasattr(criteria, "items"):
                for ci in criteria.items:
                    criteria_map[ci.key] = ci

            def _apply_criteria_color(item: QTableWidgetItem, value: float,
                                      key: str, fmt: str, label: str, unit: str) -> None:
                """値と基準を比較してセルに色・テキストマーク・ツールチップを設定します。

                UX改善⑧新: 背景色だけでなくテキストの先頭にも ✅/⚠/❌ マークを付与し、
                印刷時やモノクロ表示でも合否が一目で判断できるようにします。
                """
                ci = criteria_map.get(key)
                if ci and ci.enabled and ci.limit_value and ci.limit_value > 0:
                    ratio = value / ci.limit_value
                    val_str = f"{value:{fmt}}"
                    lim_str = f"{ci.limit_value:{fmt}}"
                    if ratio <= 1.0:
                        item.setText(f"✅ {val_str}")
                        item.setBackground(_OK_COLOR)
                        item.setToolTip(
                            f"{label}: {val_str} {unit}\n"
                            f"基準値: {lim_str} {unit}\n"
                            f"充足率: {ratio:.1%}  ✅ OK（基準クリア）"
                        )
                    elif ratio <= 1.2:
                        item.setText(f"⚠ {val_str}")
                        item.setBackground(_WARN_COLOR)
                        item.setToolTip(
                            f"{label}: {val_str} {unit}\n"
                            f"基準値: {lim_str} {unit}\n"
                            f"充足率: {ratio:.1%}  ⚠ 基準近傍（要注意）"
                        )
                    else:
                        item.setText(f"❌ {val_str}")
                        item.setBackground(_NG_COLOR)
                        item.setToolTip(
                            f"{label}: {val_str} {unit}\n"
                            f"基準値: {lim_str} {unit}\n"
                            f"充足率: {ratio:.1%}  ❌ NG（基準超過）"
                        )

            if drift_val is not None:
                _apply_criteria_color(drift_item, drift_val,
                                      "max_drift", ".5f", "最大層間変形角", "rad")
            if acc_val is not None:
                _apply_criteria_color(acc_item, acc_val,
                                      "max_acc", ".3f", "最大絶対加速度", "m/s²")

        self._table.setItem(row, _COL_NAME, name_item)
        self._table.setItem(row, _COL_GROUP, group_item)
        self._table.setItem(row, _COL_MODEL, model_item)
        self._table.setItem(row, _COL_STATUS, status_item)
        self._table.setItem(row, _COL_DRIFT, drift_item)
        self._table.setItem(row, _COL_ACC, acc_item)
        self._table.setItem(row, _COL_NOTES, notes_item)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_edit = menu.addAction("編集…")
        # UX改善②: 名前を変更（F2キーと同じ操作）
        act_rename = menu.addAction("名前を変更…  [F2]")
        act_rename.setToolTip("ケース名だけをすばやく変更します（重いダイアログを開かずに済みます）")
        # UX改善⑦新: メモを直接編集
        act_edit_notes = menu.addAction("メモを編集…")
        act_edit_notes.setToolTip("メモだけを軽量ダイアログで素早く編集します（メモ列のダブルクリックと同じ操作）")
        act_dup = menu.addAction("複製  [Ctrl+D]")
        # UX改善②新: 複製して編集
        act_dup_edit = menu.addAction("複製して編集…  [Ctrl+Shift+D]")
        act_dup_edit.setToolTip("選択ケースを複製し、すぐに編集ダイアログを開きます")
        act_run = menu.addAction("実行")
        menu.addSeparator()

        # グループサブメニュー
        group_menu = menu.addMenu("グループに追加")
        act_new_group = group_menu.addAction("+ 新規グループ…")
        group_actions = {}
        if self._project:
            for gname in sorted(self._project.case_groups.keys()):
                act = group_menu.addAction(gname)
                group_actions[act] = gname
        group_menu.addSeparator()
        act_remove_group = group_menu.addAction("グループから除外")

        menu.addSeparator()
        # UX改善②新: 状態リセットアクション
        act_reset = menu.addAction("🔄 状態をリセット（再実行可能に）")
        act_reset.setToolTip(
            "解析状態を PENDING（未実行）に戻します。\n"
            "完了済みケースを同じパラメータで再実行したいときに使います。\n"
            "解析結果サマリーはそのまま保持されます。"
        )
        menu.addSeparator()
        act_del = menu.addAction("削除")

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        case_id = self.selected_case_id()
        if case_id is None:
            return

        if action == act_edit:
            self._edit_case(case_id)
        elif action == act_rename:
            self._rename_case(case_id)
        elif action == act_edit_notes:
            self._edit_notes_inline(case_id)
        elif action == act_dup:
            self._duplicate_case(case_id)
        elif action == act_dup_edit:
            self._duplicate_and_edit_case(case_id)
        elif action == act_run:
            self.runRequested.emit(case_id)
        elif action == act_reset:
            self._reset_case_status(case_id)
        elif action == act_del:
            self._delete_case(case_id)
        elif action == act_new_group:
            self._add_to_new_group(case_id)
        elif action == act_remove_group:
            self._remove_from_group(case_id)
        elif action in group_actions:
            self._add_to_group(case_id, group_actions[action])

    # ------------------------------------------------------------------
    # Keyboard shortcut handlers
    # ------------------------------------------------------------------

    def _shortcut_edit(self) -> None:
        """Enter キーで選択ケースを編集します。"""
        case_id = self.selected_case_id()
        if case_id:
            self._edit_case(case_id)

    def _shortcut_run(self) -> None:
        """F5 キーで選択ケースの実行をリクエストします。"""
        case_id = self.selected_case_id()
        if case_id:
            self.runRequested.emit(case_id)

    def _shortcut_duplicate_and_edit(self) -> None:
        """Ctrl+Shift+D で選択ケースを複製してすぐに編集ダイアログを開きます。"""
        case_id = self.selected_case_id()
        if case_id:
            self._duplicate_and_edit_case(case_id)

    def _shortcut_duplicate(self) -> None:
        """Ctrl+D で選択ケースを複製します。"""
        case_id = self.selected_case_id()
        if case_id:
            self._duplicate_case(case_id)

    def _shortcut_rename(self) -> None:
        """UX改善②: F2 キーで選択ケースの名前をクイックリネームします。"""
        case_id = self.selected_case_id()
        if case_id:
            self._rename_case(case_id)

    def _reset_case_status(self, case_id: str) -> None:
        """
        UX改善②新: ケースの状態を PENDING（未実行）にリセットします。

        完了済みまたはエラーのケースを再実行可能な状態に戻します。
        解析結果サマリーはそのまま保持されるため、過去の結果も参照できます。
        コンテキストメニューの「🔄 状態をリセット」から呼び出せます。
        """
        if self._project is None:
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        # PENDING 以外（完了・エラー・実行中）のケースのみリセット対象
        if case.status == AnalysisCaseStatus.PENDING:
            return
        case.status = AnalysisCaseStatus.PENDING
        case.return_code = None
        # result_summary は保持（過去の結果を参照したい場合があるため）
        self._project._touch()  # type: ignore[attr-defined]
        self.refresh()
        self.projectModified.emit()

    def _rename_case(self, case_id: str) -> None:
        """
        UX改善②: ケース名だけをシンプルなInputDialogで変更します。

        重い CaseEditDialog を開かずに名前だけをすばやく変更できます。
        F2 キーまたはコンテキストメニューの「名前を変更…」から呼び出せます。
        """
        if self._project is None:
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        new_name, ok = QInputDialog.getText(
            self,
            "ケース名の変更",
            "新しいケース名を入力してください:",
            text=case.name,
        )
        if ok and new_name.strip():
            case.name = new_name.strip()
            self._project._touch()  # type: ignore[attr-defined]
            self.refresh()
            self.projectModified.emit()
            # リネームしたケースを再選択
            for row in range(self._table.rowCount()):
                item = self._table.item(row, _COL_NAME)
                if item and item.data(Qt.UserRole) == case_id:
                    self._table.selectRow(row)
                    break

    # ------------------------------------------------------------------
    # UX改善⑥新: ケース並び順変更
    # ------------------------------------------------------------------

    def _move_case_up(self) -> None:
        """
        UX改善⑥新: 選択ケースを1つ上に移動します。

        project.cases リストの順序を入れ替え、解析実行順・一覧表示順を
        ユーザーが自由に整理できるようにします。
        """
        if self._project is None:
            return
        case_id = self.selected_case_id()
        if not case_id:
            return
        cases = self._project.cases
        idx = next((i for i, c in enumerate(cases) if c.id == case_id), -1)
        if idx <= 0:
            return
        cases[idx], cases[idx - 1] = cases[idx - 1], cases[idx]
        self._project._touch()  # type: ignore[attr-defined]
        self.refresh()
        self.projectModified.emit()
        # 移動後も同じケースを選択状態に維持
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_NAME)
            if item and item.data(Qt.UserRole) == case_id:
                self._table.selectRow(row)
                break

    def _move_case_down(self) -> None:
        """
        UX改善⑥新: 選択ケースを1つ下に移動します。

        project.cases リストの順序を入れ替え、解析実行順・一覧表示順を
        ユーザーが自由に整理できるようにします。
        """
        if self._project is None:
            return
        case_id = self.selected_case_id()
        if not case_id:
            return
        cases = self._project.cases
        idx = next((i for i, c in enumerate(cases) if c.id == case_id), -1)
        if idx < 0 or idx >= len(cases) - 1:
            return
        cases[idx], cases[idx + 1] = cases[idx + 1], cases[idx]
        self._project._touch()  # type: ignore[attr-defined]
        self.refresh()
        self.projectModified.emit()
        # 移動後も同じケースを選択状態に維持
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_NAME)
            if item and item.data(Qt.UserRole) == case_id:
                self._table.selectRow(row)
                break

    # ------------------------------------------------------------------
    # UX改善③: 列幅永続化
    # ------------------------------------------------------------------

    _SETTINGS_ORG = "BAUES"
    _SETTINGS_APP = "snap-controller"
    _SETTINGS_KEY_HEADER = "ui/case_table_header_state"

    def _save_column_widths(self) -> None:
        """UX改善③: 現在の列幅をQSettingsに保存します。"""
        s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        state = self._table.horizontalHeader().saveState()
        s.setValue(self._SETTINGS_KEY_HEADER, state)

    def _restore_column_widths(self) -> None:
        """UX改善③: QSettingsから列幅を復元します。"""
        s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        state = s.value(self._SETTINGS_KEY_HEADER)
        if state:
            # 保存済みの状態を適用（列数が変わっている場合はスキップ）
            try:
                self._table.horizontalHeader().restoreState(state)
            except Exception:
                pass  # 互換性エラーは無視してデフォルト幅を使用

    def _on_double_click(self, index: QModelIndex) -> None:
        """
        ダブルクリック処理。

        UX改善⑦新: メモ列（_COL_NOTES）のダブルクリックは
        軽量な入力ダイアログでメモを直接編集します。
        それ以外の列のダブルクリックは従来通り全編集ダイアログを開きます。
        """
        case_id = self.selected_case_id()
        if not case_id:
            return
        if index.column() == _COL_NOTES:
            self._edit_notes_inline(case_id)
        else:
            self._edit_case(case_id)

    def _edit_notes_inline(self, case_id: str) -> None:
        """
        UX改善⑦新: メモ列を直接編集するための軽量ダイアログ。

        重い CaseEditDialog を開かずに、小さな QInputDialog でメモだけを
        素早く更新できます。メモ列をダブルクリックまたはコンテキストメニュー
        「メモを編集…」から呼び出せます。
        """
        if self._project is None:
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        new_notes, ok = QInputDialog.getText(
            self,
            "メモを編集",
            f"ケース「{case.name}」のメモ:",
            text=case.notes or "",
        )
        if ok:
            # ok=True でも内容が変わっていなければ保存不要だが、
            # 空にしてOKを押した場合も保存する（メモを消したい操作）
            case.notes = new_notes
            self._project._touch()  # type: ignore[attr-defined]
            self.refresh()
            self.projectModified.emit()
            # 編集後もケースを選択状態に維持
            for row in range(self._table.rowCount()):
                item = self._table.item(row, _COL_NAME)
                if item and item.data(Qt.UserRole) == case_id:
                    self._table.selectRow(row)
                    break

    def _on_selection_changed(self) -> None:
        case_id = self.selected_case_id()
        has_selection = case_id is not None
        # 改善②: アクションバーのボタン有効/無効を更新
        self._btn_run.setEnabled(has_selection)
        self._btn_edit.setEnabled(has_selection)
        self._btn_dup.setEnabled(has_selection)
        # UX改善⑨新: 「複製して編集」ボタンも同様に制御
        self._btn_dup_edit.setEnabled(has_selection)

        # UX改善C: 複数選択時の件数バッジ表示
        selected_ids = self.selected_case_ids()
        multi_count = len(selected_ids)
        if multi_count >= 2:
            # 複数選択中 → 件数バッジを表示し、編集・複製を無効化（一括削除は有効）
            self._action_hint.setText(
                f"<b style='color:#1976d2;'>{multi_count}件選択中</b>"
                f"　— Delete/Backspaceで一括削除"
            )
            self._btn_edit.setEnabled(False)      # 編集は1件のみ対象
            self._btn_dup.setEnabled(False)       # 複製は1件のみ対象
            self._btn_dup_edit.setEnabled(False)  # 複製して編集も1件のみ対象
            # UX改善⑥新: 複数選択時は並び替えボタンを無効化
            self._btn_move_up.setEnabled(False)
            self._btn_move_down.setEnabled(False)
        elif has_selection and self._project:
            case = self._project.get_case(case_id)
            if case:
                status_label = case.get_status_label() if hasattr(case, "get_status_label") else ""
                self._action_hint.setText(f"選択中: {case.name}  [{status_label}]")
            else:
                self._action_hint.setText("")
            # UX改善⑥新: 並び替えボタンの有効/無効をケース位置で制御
            cases = self._project.cases
            idx = next((i for i, c in enumerate(cases) if c.id == case_id), -1)
            self._btn_move_up.setEnabled(idx > 0)
            self._btn_move_down.setEnabled(0 <= idx < len(cases) - 1)
        else:
            self._action_hint.setText("ケースを選択してください")
            self._btn_move_up.setEnabled(False)
            self._btn_move_down.setEnabled(False)
        if case_id:
            self.caseSelectionChanged.emit(case_id)

    def _edit_case(self, case_id: str) -> None:
        if self._project is None:
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        s8i = self._project.s8i_model if self._project else None
        dlg = CaseEditDialog(case, s8i_model=s8i, parent=self)
        if dlg.exec():
            self._project._touch()
            self.refresh()
            self.projectModified.emit()  # 改善⑤

    def _duplicate_case(self, case_id: str) -> None:
        if self._project is None:
            return
        self._project.duplicate_case(case_id)
        self.refresh()
        self.projectModified.emit()  # 改善⑤

    def _duplicate_and_edit_case(self, case_id: str) -> None:
        """
        UX改善②新: ケースを複製してすぐに編集ダイアログを開きます。

        複製ケースのパラメータを即座に変更して新しいバリアントを作るワークフローを
        サポートします。Ctrl+Shift+D またはコンテキストメニューから呼び出せます。
        """
        if self._project is None:
            return
        # 元ケースを確認
        original = self._project.get_case(case_id)
        if original is None:
            return
        # 複製を実行
        self._project.duplicate_case(case_id)
        self.refresh()
        self.projectModified.emit()

        # 末尾に追加された複製ケースを取得（IDが一番最後のケース）
        if not self._project.cases:
            return
        new_case = self._project.cases[-1]

        # 複製されたケースをテーブル上で選択状態にする
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_NAME)
            if item and item.data(Qt.UserRole) == new_case.id:
                self._table.selectRow(row)
                break

        # 編集ダイアログを即座に開く
        s8i = self._project.s8i_model if self._project else None
        dlg = CaseEditDialog(new_case, s8i_model=s8i, parent=self)
        if dlg.exec():
            self._project._touch()
            self.refresh()
            self.projectModified.emit()

    def _delete_selected(self) -> None:
        # UX改善C: 複数選択時は一括削除をサポート
        selected_ids = self.selected_case_ids()
        if not selected_ids:
            return
        if len(selected_ids) >= 2:
            reply = QMessageBox.question(
                self,
                "確認",
                f"選択した {len(selected_ids)} 件のケースをまとめて削除しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes and self._project:
                for cid in selected_ids:
                    self._project.remove_case(cid)
                    for gname, cids in list(self._project.case_groups.items()):
                        if cid in cids:
                            cids.remove(cid)
                self.refresh()
                self.projectModified.emit()
        else:
            self._delete_case(selected_ids[0])

    def _delete_case(self, case_id: str) -> None:
        if self._project is None:
            return
        reply = QMessageBox.question(
            self,
            "確認",
            "選択したケースを削除しますか？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._project.remove_case(case_id)
            # グループからも除外
            for gname in list(self._project.case_groups.keys()):
                if case_id in self._project.case_groups[gname]:
                    self._project.case_groups[gname].remove(case_id)
                    if not self._project.case_groups[gname]:
                        del self._project.case_groups[gname]
            self.refresh()
            self.projectModified.emit()  # 改善⑤

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def _add_to_new_group(self, case_id: str) -> None:
        """新規グループを作成してケースを追加します。"""
        if self._project is None:
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "新規グループ", "グループ名を入力してください:"
        )
        if ok and name.strip():
            name = name.strip()
            self._add_to_group(case_id, name)

    def _add_to_group(self, case_id: str, group_name: str) -> None:
        """ケースをグループに追加します。"""
        if self._project is None:
            return
        # 既存グループから除外
        for gname in list(self._project.case_groups.keys()):
            if case_id in self._project.case_groups[gname]:
                self._project.case_groups[gname].remove(case_id)
                if not self._project.case_groups[gname]:
                    del self._project.case_groups[gname]
        # 新しいグループに追加
        if group_name not in self._project.case_groups:
            self._project.case_groups[group_name] = []
        self._project.case_groups[group_name].append(case_id)
        self._project._touch