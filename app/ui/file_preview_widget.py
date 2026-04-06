"""
app/ui/file_preview_widget.py
入力ファイルプレビューウィジェット。

SNAP入力ファイル (.s8i) の内容をプレビュー表示します。
ファイル構造の確認、キーワード検索、パラメータ値の確認が可能です。

UX改善（第6回③）: ファイル構造クイックジャンプバー追加。
  ファイル読込後、主要なSNAPキーワードセクション（ELEMENT/NODE/DAMPING等）
  へのクイックジャンプボタンを自動生成します。
  - 存在するセクションのみボタンが表示され、クリックで先頭出現箇所に即ジャンプ
  - ボタンには「(N行)」の出現件数バッジを表示し、モデルの規模感を把握しやすくします
  - ファイル未読み込み時はバーを非表示にし、画面をすっきり保ちます

レイアウト:
  ┌──────────────────────────────────────────┐
  │ ファイルパス: [path]  [開く] [再読込]      │
  │ [検索: ________ ] [次へ] [前へ]           │
  │ ┌──────────────────────────────────────┐  │
  │ │ 1: TITLE "Model A"                   │  │
  │ │ 2: NODE 1 0.0 0.0 0.0               │  │
  │ │ 3: ELEMENT ...                       │  │
  │ │ ...                                  │  │
  │ └──────────────────────────────────────┘  │
  │ 行数: 1234  エンコーディング: Shift_JIS    │
  └──────────────────────────────────────────┘
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme import ThemeManager


# .s8i ファイルのキーワード（行頭に来る主要コマンド）
_S8I_KEYWORDS = [
    "TITLE", "NODE", "ELEMENT", "MATERIAL", "SECTION",
    "BOUNDARY", "LOAD", "CASE", "MASS", "DAMPING",
    "STEP", "DT", "TIME", "EARTHQUAKE", "WAVE",
    "SPRING", "LINK", "OUTPUT", "END", "COMMENT",
    "RESTRAINT", "FORCE", "MOMENT", "STATIC", "DYNAMIC",
    "MODAL", "RESPONSE", "SPECTRUM", "GROUND", "HINGE",
]


class _S8iHighlighter(QSyntaxHighlighter):
    """SNAP入力ファイル (.s8i) のシンプルなシンタックスハイライター。"""

    def __init__(self, document: QTextDocument, is_dark: bool = False) -> None:
        super().__init__(document)
        self._is_dark = is_dark
        self._setup_formats()

    def _setup_formats(self) -> None:
        # キーワード
        self._kw_fmt = QTextCharFormat()
        if self._is_dark:
            self._kw_fmt.setForeground(QColor("#569cd6"))  # blue
        else:
            self._kw_fmt.setForeground(QColor("#0000cc"))
        self._kw_fmt.setFontWeight(QFont.Bold)

        # コメント (COMMENT行, または * で始まる行)
        self._comment_fmt = QTextCharFormat()
        if self._is_dark:
            self._comment_fmt.setForeground(QColor("#6a9955"))  # green
        else:
            self._comment_fmt.setForeground(QColor("#008000"))

        # 数値
        self._number_fmt = QTextCharFormat()
        if self._is_dark:
            self._number_fmt.setForeground(QColor("#b5cea8"))
        else:
            self._number_fmt.setForeground(QColor("#098658"))

        # 文字列リテラル ("..." or '...')
        self._string_fmt = QTextCharFormat()
        if self._is_dark:
            self._string_fmt.setForeground(QColor("#ce9178"))
        else:
            self._string_fmt.setForeground(QColor("#a31515"))

    def highlightBlock(self, text: str) -> None:
        stripped = text.lstrip()

        # コメント行
        if stripped.startswith(("*", "COMMENT", "#", "!")):
            self.setFormat(0, len(text), self._comment_fmt)
            return

        # キーワード
        for kw in _S8I_KEYWORDS:
            if stripped.upper().startswith(kw):
                start = text.index(stripped[0]) if stripped else 0
                self.setFormat(start, len(kw), self._kw_fmt)
                break

        # 数値（浮動小数点、整数、科学表記）
        import re
        for match in re.finditer(r'(?<!\w)[-+]?(\d+\.?\d*([eE][-+]?\d+)?)', text):
            self.setFormat(match.start(), match.end() - match.start(), self._number_fmt)

        # 文字列リテラル
        for match in re.finditer(r'"[^"]*"|\'[^\']*\'', text):
            self.setFormat(match.start(), match.end() - match.start(), self._string_fmt)


class FilePreviewWidget(QWidget):
    """
    SNAP入力ファイルのプレビュー・検証ウィジェット。

    Public API
    ----------
    load_file(path)      — 指定パスのファイルをプレビュー表示
    clear()              — プレビューをクリア
    get_current_path()   — 現在読込中のファイルパス

    Signals
    -------
    fileLoaded(path: str)  — ファイル読込完了時
    """

    fileLoaded = Signal(str)

    # UX改善（第6回③）: クイックジャンプ対象セクション（アイコン, キーワード, ツールチップ）
    _JUMP_SECTIONS = [
        ("🏗", "ELEMENT",    "要素定義セクション（梁・柱・ブレース等）"),
        ("📍", "NODE",       "節点定義セクション（X/Y/Z座標）"),
        ("〰", "DAMPING",    "減衰定義セクション"),
        ("🌊", "EARTHQUAKE", "地震波読込セクション"),
        ("⚖", "MASS",       "質量定義セクション"),
        ("📏", "SECTION",    "断面定義セクション"),
        ("🔩", "MATERIAL",   "材料定義セクション"),
        ("📤", "OUTPUT",     "出力設定セクション"),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_path: Optional[str] = None
        self._highlighter: Optional[_S8iHighlighter] = None
        self._search_positions: list = []
        self._search_index: int = -1
        # UX改善（第6回③）: キーワード→行番号（0ベース）のマッピング
        self._section_line_map: dict = {}
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, path: str) -> bool:
        """指定パスのファイルを読み込んでプレビュー表示します。"""
        p = Path(path)
        if not p.is_file():
            self._editor.setPlainText(f"ファイルが見つかりません: {path}")
            self._status_label.setText("エラー: ファイルが見つかりません")
            return False

        # エンコーディング候補を試す
        content = None
        encoding_used = "unknown"
        for enc in ["shift_jis", "cp932", "utf-8", "utf-8-sig", "euc-jp", "latin-1"]:
            try:
                content = p.read_text(encoding=enc)
                encoding_used = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if content is None:
            self._editor.setPlainText("ファイルを読み込めませんでした。")
            self._status_label.setText("エラー: 読込失敗")
            return False

        self._current_path = path
        self._path_label.setText(str(p.name))
        self._path_label.setToolTip(str(p))

        # ハイライター更新
        is_dark = ThemeManager.is_dark()
        self._highlighter = _S8iHighlighter(self._editor.document(), is_dark)

        self._editor.setPlainText(content)

        line_count = content.count("\n") + 1
        self._status_label.setText(
            f"行数: {line_count}  |  エンコーディング: {encoding_used}  |  "
            f"サイズ: {p.stat().st_size:,} bytes"
        )

        # UX改善（第6回③）: クイックジャンプバーを更新
        self._build_jump_bar(content)

        self.fileLoaded.emit(path)
        return True

    def clear(self) -> None:
        """プレビューをクリアします。"""
        self._current_path = None
        self._editor.clear()
        self._path_label.setText("（ファイル未選択）")
        self._path_label.setToolTip("")
        self._status_label.setText("")
        self._search_edit.clear()
        self._search_positions.clear()
        self._search_index = -1
        # UX改善（第6回③）: ジャンプバーを隠す
        if hasattr(self, "_jump_bar_widget"):
            self._jump_bar_widget.hide()
        self._section_line_map.clear()

    def get_current_path(self) -> Optional[str]:
        return self._current_path

    def update_theme(self) -> None:
        """テーマ変更時にハイライトを再適用します。"""
        if self._current_path:
            is_dark = ThemeManager.is_dark()
            self._highlighter = _S8iHighlighter(self._editor.document(), is_dark)
            self._highlighter.rehighlight()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- ファイル行 ---
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("ファイル:"))
        self._path_label = QLabel("（ファイル未選択）")
        self._path_label.setStyleSheet("font-weight: bold;")
        file_row.addWidget(self._path_label, stretch=1)

        layout.addLayout(file_row)

        # --- UX改善（第6回③）: クイックジャンプバー ---
        # ファイル読込後に存在するセクションボタンを動的に生成するコンテナ
        self._jump_bar_widget = QWidget()
        jump_bar_inner = QHBoxLayout(self._jump_bar_widget)
        jump_bar_inner.setContentsMargins(0, 2, 0, 2)
        jump_bar_inner.setSpacing(4)

        jump_label = QLabel("ジャンプ:")
        jump_label.setStyleSheet("color: gray; font-size: 11px;")
        jump_bar_inner.addWidget(jump_label)

        # ボタンを格納するスクロールエリア（ボタンが多い場合に横スクロール）
        self._jump_scroll = QScrollArea()
        self._jump_scroll.setWidgetResizable(True)
        self._jump_scroll.setFixedHeight(34)
        self._jump_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._jump_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._jump_scroll.setFrameShape(QFrame.NoFrame)

        self._jump_btn_container = QWidget()
        self._jump_btn_layout = QHBoxLayout(self._jump_btn_container)
        self._jump_btn_layout.setContentsMargins(0, 0, 0, 0)
        self._jump_btn_layout.setSpacing(4)
        self._jump_btn_layout.addStretch()

        self._jump_scroll.setWidget(self._jump_btn_container)
        jump_bar_inner.addWidget(self._jump_scroll, stretch=1)

        self._jump_bar_widget.hide()  # 初期は非表示
        layout.addWidget(self._jump_bar_widget)

        # --- 検索行 ---
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("キーワードを入力...")
        self._search_edit.returnPressed.connect(self._search_next)
        search_row.addWidget(self._search_edit, stretch=1)

        btn_next = QPushButton("次へ")
        btn_next.setMaximumWidth(60)
        btn_next.clicked.connect(self._search_next)
        search_row.addWidget(btn_next)

        btn_prev = QPushButton("前へ")
        btn_prev.setMaximumWidth(60)
        btn_prev.clicked.connect(self._search_prev)
        search_row.addWidget(btn_prev)

        self._search_count_label = QLabel("")
        search_row.addWidget(self._search_count_label)

        layout.addLayout(search_row)

        # --- エディター ---
        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        # 等幅フォント
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.Monospace)
        self._editor.setFont(font)
        layout.addWidget(self._editor, stretch=1)

        # --- ステータス行 ---
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # UX改善（第6回③）: クイックジャンプバー
    # ------------------------------------------------------------------

    def _build_jump_bar(self, content: str) -> None:
        """
        UX改善（第6回③）: ファイル内容を解析してクイックジャンプボタンを構築します。

        存在するセクションのみボタンを表示し、クリックすると先頭出現箇所に
        エディタをスクロールします。ボタンには出現件数バッジも表示します。

        Parameters
        ----------
        content : str
            読み込んだファイル全文。
        """
        # 既存のボタンをクリア（stretchは残す）
        while self._jump_btn_layout.count() > 1:
            item = self._jump_btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._section_line_map.clear()
        lines = content.splitlines()

        # 各セクションの行番号と出現回数を収集
        section_data: dict = {}  # keyword -> {"first_line": int, "count": int}
        for line_no, line in enumerate(lines):
            stripped = line.strip().upper()
            for _icon, kw, _tip in self._JUMP_SECTIONS:
                if stripped.startswith(kw):
                    if kw not in section_data:
                        section_data[kw] = {"first_line": line_no, "count": 0}
                    section_data[kw]["count"] += 1

        if not section_data:
            self._jump_bar_widget.hide()
            return

        # 存在するセクションのみボタン生成
        for icon, kw, tooltip in self._JUMP_SECTIONS:
            if kw not in section_data:
                continue
            data = section_data[kw]
            count = data["count"]
            first_line = data["first_line"]
            self._section_line_map[kw] = first_line

            btn = QPushButton(f"{icon} {kw} ({count})")
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                "QPushButton { font-size: 10px; padding: 1px 7px; border-radius: 3px;"
                "  border: 1px solid #90caf9; background: #e3f2fd; color: #0d47a1; }"
                "QPushButton:hover { background: #bbdefb; }"
            )
            btn.setToolTip(f"{tooltip}\n行 {first_line + 1} へジャンプ（全 {count} 件）")
            # closure で first_line をキャプチャ
            btn.clicked.connect(
                lambda checked=False, fl=first_line: self._jump_to_line(fl)
            )
            self._jump_btn_layout.insertWidget(
                self._jump_btn_layout.count() - 1, btn
            )

        self._jump_bar_widget.show()

    def _jump_to_line(self, line_no: int) -> None:
        """
        UX改善（第6回③）: 指定行にエディタをスクロールします。

        Parameters
        ----------
        line_no : int
            0ベースの行番号。
        """
        block = self._editor.document().findBlockByLineNumber(line_no)
        if block.isValid():
            cursor = self._editor.textCursor()
            cursor.setPosition(block.position())
            self._editor.setTextCursor(cursor)
            self._editor.centerCursor()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_next(self) -> None:
        query = self._search_edit.text()
        if not query:
            return

        doc = self._editor.document()
        # 新しい検索なら位置リストを再構築
        if not self._search_positions or self._search_edit.text() != getattr(self, '_last_query', ''):
            self._search_positions.clear()
            self._search_index = -1
            self._last_query = query
            cursor = doc.find(query)
            while not cursor.isNull():
                self._search_positions.append(cursor.position())
                cursor = doc.find(query, cursor)

        if not self._search_positions:
            self._search_count_label.setText("見つかりません")
            return

        self._search_index = (self._search_index + 1) % len(self._search_positions)
        self._goto_search_pos()

    def _search_prev(self) -> None:
        if not self._search_positions:
            self._search_next()
            return

        self._search_index = (self._search_index - 1) % len(self._search_positions)
        self._goto_search_pos()

    def _goto_search_pos(self) -> None:
        if not self._search_positions or self._search_index < 0:
            return
        pos = self._search_positions[self._search_index]
        cursor = self._editor.textCursor()
        cursor.setPosition(pos)
        query = self._search_edit.text()
        cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.MoveAnchor, len(query))
        cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.KeepAnchor, len(query))
        self._editor.setTextCursor(cursor)
        self._editor.centerCursor()
        total = len(self._search_positions)
        idx = self._search_index + 1
        self._search_count_label.setText(f"{idx}/{total}")
