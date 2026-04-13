"""
app/ui/log_widget.py
ログ表示ウィジェット。

解析実行中の標準出力・エラーをリアルタイムで表示します。

UX改善（第7回④）: 「📥 ログを保存」ボタン追加。
  ヘッダーの「クリア」「コピー」ボタン横に「📥 保存」ボタンを追加します。
  クリックすると QFileDialog が開き、タイムスタンプ付きのテキストファイル
  （例: snap_log_20260403_153012.txt）として保存できます。
  - フィルタリング前の全ログを保存します（現在の絞り込み状態に関わらず）
  - 「[コピー]」と同様に全エントリを「[HH:MM:SS] テキスト」形式で出力
  - 保存完了後にステータスバー代わりのボタンラベルを2秒間「✓ 保存しました」に変更


改善⑦: エラー/警告フィルターボタンを追加。
        「全て / エラーのみ / 警告以上」でログを絞り込めます。
        件数バッジでエラー・警告の発生数を常時確認できます。

UX改善③: テキスト検索バーを追加。
        ログエリアの上部に検索フィールドを配置し、入力したキーワードに
        一致する行だけをリアルタイムで絞り込めます。
        ケース名やエラー内容を素早く見つけるのに役立ちます。
        Ctrl+F でフォーカスが移動し、Esc でクリアできます。

UX改善（新⑤）: 自動スクロール on/off トグルボタンを追加。
        ログが追加されるたびに最下部へ自動スクロールする挙動を
        ワンクリックで停止・再開できます。
        解析実行中に過去のログを遡って確認したいときに、
        自動スクロールのせいで画面が強制移動してしまう問題を解消します。
        ⬇ アイコンのトグルボタン（ON=有効・青、OFF=灰色）で制御し、
        ウィンドウ右上に常時表示されます。

UX改善（今回追加）: ログパネル折りたたみボタン。
  ヘッダーバー左端の「▼/▶」ボタンでログ内容（検索バー＋テキストエリア）を
  折りたたみ・展開できます。
  - 折りたたみ中もログの記録は継続されます
  - 展開したときは自動スクロールが ON なら最下部にジャンプします
  - STEP3（解析実行中）に結果チャートを大きく使いたい場合など、
    ログを一時的に隠して画面スペースを確保できます
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import ThemeManager, LOG_STYLES

# ログレベルと色のマッピング（ライト / ダーク共通で視認性の良い色）
_LEVEL_COLORS = {
    "[ERROR]": "#ef5350",
    "[WARN]":  "#ff9800",
    "=== ":    "#569cd6",
    "完了":     "#4ec9b0",
    "エラー":  "#ef5350",
}

# ---- 改善⑦: ログレベル定数 ----
_LVL_INFO  = "INFO"
_LVL_WARN  = "WARN"
_LVL_ERROR = "ERROR"

def _detect_level(line: str) -> str:
    """行テキストからログレベルを判定します。"""
    if "[ERROR]" in line or "エラー" in line:
        return _LVL_ERROR
    if "[WARN]" in line:
        return _LVL_WARN
    return _LVL_INFO


class LogWidget(QWidget):
    """
    実行ログを表示するウィジェット。

    append_line(line) スロットでログを追記できます。

    改善⑦: フィルターボタン（全て/エラーのみ/警告以上）と
    エラー・警告件数バッジを追加しました。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # 改善⑦: 全行を level 付きで保持
        self._all_lines: List[dict] = []  # [{"ts": str, "text": str, "level": str, "color": str}]
        self._filter_level = _LVL_INFO    # 現在のフィルター: INFO=全表示, WARN=警告以上, ERROR=エラーのみ
        # UX改善③: テキスト検索キーワード
        self._search_text: str = ""
        # UX改善（新⑤）: 自動スクロールフラグ（デフォルトON）
        self._auto_scroll: bool = True
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_line(self, line: str) -> None:
        """1行のログテキストを追記します（スレッドセーフ）。"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = self._detect_color(line)
        level = _detect_level(line)

        # 改善⑦: 全ラインを記憶
        self._all_lines.append({"ts": timestamp, "text": line, "level": level, "color": color})

        # 現在のフィルターに合致する行のみ表示
        if self._line_passes_filter(level, line):
            self._append_to_text(timestamp, line, color)

        # 改善⑦: バッジカウントを更新
        self._update_badges()

    def clear(self) -> None:
        """ログをクリアします。"""
        self._all_lines.clear()
        self._text.clear()
        self._update_badges()

    def get_plain_text(self) -> str:
        """
        UX改善③: 現在のログ全行をプレーンテキストとして返します。

        ErrorGuideWidget がエラー種別を推定するために使用します。

        Returns
        -------
        str
            全ログ行を改行で結合したテキスト。
        """
        return "\n".join(entry["text"] for entry in self._all_lines)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addLayout(self._build_header())
        layout.addWidget(self._build_log_content())

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(4)

        self._collapse_btn = QPushButton("▼")
        self._collapse_btn.setCheckable(True)
        self._collapse_btn.setChecked(True)
        self._collapse_btn.setFixedSize(22, 22)
        self._collapse_btn.setToolTip(
            "ログパネルを折りたたむ / 展開する\n\n"
            "▼（展開）: ログ内容を表示します\n"
            "▶（折りたたみ）: ログ内容を隠してヘッダーバーのみ表示します。\n"
            "  画面スペースを節約したいときに便利です。\n"
            "  ログの記録は折りたたみ中も継続されます。"
        )
        self._collapse_btn.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 1px 4px; border-radius: 3px; }"
            "QPushButton:checked { color: palette(text); }"
        )
        self._collapse_btn.toggled.connect(self._on_collapse_toggled)
        header.addWidget(self._collapse_btn)

        header.addWidget(QLabel("<b>実行ログ</b>"))

        self._build_filter_buttons(header)
        self._build_badge_labels(header)
        header.addStretch()
        self._build_action_buttons(header)

        return header

    def _build_filter_buttons(self, header: QHBoxLayout) -> None:
        self._btn_all = QPushButton("全て")
        self._btn_all.setCheckable(True)
        self._btn_all.setChecked(True)
        self._btn_all.setToolTip("全てのログを表示")
        self._btn_all.setFixedHeight(22)
        self._btn_all.clicked.connect(lambda: self._set_filter(_LVL_INFO))

        self._btn_warn = QPushButton("⚠ 警告以上")
        self._btn_warn.setCheckable(True)
        self._btn_warn.setToolTip("警告・エラーのみ表示")
        self._btn_warn.setFixedHeight(22)
        self._btn_warn.clicked.connect(lambda: self._set_filter(_LVL_WARN))

        self._btn_error = QPushButton("✖ エラーのみ")
        self._btn_error.setCheckable(True)
        self._btn_error.setToolTip("エラーのみ表示")
        self._btn_error.setFixedHeight(22)
        self._btn_error.clicked.connect(lambda: self._set_filter(_LVL_ERROR))

        for btn in (self._btn_all, self._btn_warn, self._btn_error):
            btn.setStyleSheet("""
                QPushButton { font-size: 11px; padding: 1px 6px; border-radius: 3px; }
                QPushButton:checked { background-color: #1976d2; color: white; }
            """)
            header.addWidget(btn)

    def _build_badge_labels(self, header: QHBoxLayout) -> None:
        self._badge_error = QLabel()
        self._badge_error.setStyleSheet(
            "background-color: #ef5350; color: white; border-radius: 8px;"
            "padding: 1px 6px; font-size: 10px; font-weight: bold;"
        )
        self._badge_error.setToolTip("エラー件数")
        self._badge_error.hide()

        self._badge_warn = QLabel()
        self._badge_warn.setStyleSheet(
            "background-color: #ff9800; color: white; border-radius: 8px;"
            "padding: 1px 6px; font-size: 10px; font-weight: bold;"
        )
        self._badge_warn.setToolTip("警告件数")
        self._badge_warn.hide()

        header.addWidget(self._badge_error)
        header.addWidget(self._badge_warn)

    def _build_action_buttons(self, header: QHBoxLayout) -> None:
        self._btn_autoscroll = QPushButton("⬇ 自動スクロール")
        self._btn_autoscroll.setCheckable(True)
        self._btn_autoscroll.setChecked(True)
        self._btn_autoscroll.setFixedHeight(22)
        self._btn_autoscroll.setToolTip(
            "自動スクロール ON/OFF\n\n"
            "ON（青）: 新しいログが追加されるたびに最下部へ自動スクロールします。\n"
            "OFF（灰）: 自動スクロールを停止します。過去のログを遡って確認するときに\n"
            "           使います。解析実行中でも画面が強制移動しません。"
        )
        self._btn_autoscroll.setStyleSheet("""
            QPushButton {
                font-size: 11px; padding: 1px 8px; border-radius: 3px;
            }
            QPushButton:checked {
                background-color: #1976d2; color: white;
            }
            QPushButton:!checked {
                background-color: palette(mid); color: palette(shadow);
            }
        """)
        self._btn_autoscroll.toggled.connect(self._on_autoscroll_toggled)
        header.addWidget(self._btn_autoscroll)

        btn_clear = QPushButton("クリア")
        btn_clear.clicked.connect(self.clear)
        header.addWidget(btn_clear)

        btn_copy = QPushButton("コピー")
        btn_copy.clicked.connect(self._copy_all)
        header.addWidget(btn_copy)

        self._btn_save = QPushButton("📥 保存")
        self._btn_save.setFixedHeight(22)
        self._btn_save.setToolTip(
            "ログをテキストファイルとして保存します。\n\n"
            "・保存形式: [HH:MM:SS] ログ行 のプレーンテキスト\n"
            "・ファイル名は現在日時から自動生成されます\n"
            "・フィルタリング前の全ログが保存されます"
        )
        self._btn_save.setStyleSheet("QPushButton { font-size: 11px; padding: 1px 6px; }")
        self._btn_save.clicked.connect(self._save_log)
        header.addWidget(self._btn_save)

    def _build_log_content(self) -> "QWidget":
        from PySide6.QtWidgets import QWidget as _QWidget, QVBoxLayout as _QVBoxLayout
        self._log_content = _QWidget()
        _content_layout = _QVBoxLayout(self._log_content)
        _content_layout.setContentsMargins(0, 0, 0, 0)
        _content_layout.setSpacing(2)

        _content_layout.addLayout(self._build_search_bar())

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        font = QFont("Consolas", 9)
        if not font.exactMatch():
            font = QFont("Courier New", 9)
        self._text.setFont(font)
        self._apply_theme_style()
        _content_layout.addWidget(self._text)

        return self._log_content

    def _build_search_bar(self) -> QHBoxLayout:
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.setContentsMargins(0, 0, 0, 0)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("font-size: 11px;")
        search_row.addWidget(search_icon)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("ログを検索…  (Ctrl+F でフォーカス / Esc でクリア)")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedHeight(22)
        self._search_edit.setStyleSheet("QLineEdit { font-size: 11px; }")
        self._search_edit.setToolTip(
            "入力したキーワードに一致するログ行だけを表示します。\n"
            "Ctrl+F: フォーカス移動　Esc: クリア"
        )
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_edit)

        self._search_count_lbl = QLabel("")
        self._search_count_lbl.setStyleSheet("color: gray; font-size: 10px; min-width: 60px;")
        search_row.addWidget(self._search_count_lbl)

        sc_search = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_search.activated.connect(self._focus_search)

        sc_esc = QShortcut(QKeySequence(Qt.Key_Escape), self._search_edit)
        sc_esc.setContext(Qt.WidgetShortcut)
        sc_esc.activated.connect(self._clear_search)

        return search_row

    # ------------------------------------------------------------------
    # 改善⑦: フィルター制御
    # ------------------------------------------------------------------

    def _set_filter(self, level: str) -> None:
        """フィルターレベルを変更してログを再描画します。"""
        self._filter_level = level
        # ボタンのチェック状態を同期
        self._btn_all.setChecked(level == _LVL_INFO)
        self._btn_warn.setChecked(level == _LVL_WARN)
        self._btn_error.setChecked(level == _LVL_ERROR)
        # 再描画
        self._redraw_filtered()

    def _line_passes_filter(self, line_level: str, line_text: str = "") -> bool:
        """行がレベルフィルターおよびテキスト検索に一致するか判定します。"""
        # レベルフィルター
        if self._filter_level == _LVL_WARN:
            if line_level not in (_LVL_WARN, _LVL_ERROR):
                return False
        elif self._filter_level == _LVL_ERROR:
            if line_level != _LVL_ERROR:
                return False
        # UX改善③: テキスト検索フィルター（大文字小文字を無視）
        if self._search_text and self._search_text not in line_text.lower():
            return False
        return True

    def _redraw_filtered(self) -> None:
        """フィルター条件（レベル + テキスト検索）に基づいてテキストエリアを再描画します。"""
        self._text.clear()
        match_count = 0
        for entry in self._all_lines:
            if self._line_passes_filter(entry["level"], entry["text"]):
                self._append_to_text(entry["ts"], entry["text"], entry["color"])
                match_count += 1
        # UX改善③: 検索一致件数を更新
        self._update_search_count(match_count)

    def _update_badges(self) -> None:
        """エラー・警告件数バッジを更新します。"""
        error_count = sum(1 for e in self._all_lines if e["level"] == _LVL_ERROR)
        warn_count  = sum(1 for e in self._all_lines if e["level"] == _LVL_WARN)

        if error_count > 0:
            self._badge_error.setText(f"✖ {error_count}")
            self._badge_error.show()
        else:
            self._badge_error.hide()

        if warn_count > 0:
            self._badge_warn.setText(f"⚠ {warn_count}")
            self._badge_warn.show()
        else:
            self._badge_warn.hide()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _append_to_text(self, timestamp: str, line: str, color: str) -> None:
        """テキストエリアに1行追記します。"""
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = cursor.charFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(f"[{timestamp}] {line}\n")
        self._text.setTextCursor(cursor)
        # UX改善（新⑤）: 自動スクロールが ON のときだけ最下部へスクロール
        if self._auto_scroll:
            self._text.ensureCursorVisible()

    # ------------------------------------------------------------------
    # UX改善（新⑤）: 自動スクロール制御
    # ------------------------------------------------------------------

    def _on_collapse_toggled(self, expanded: bool) -> None:
        """
        UX改善（新）: ログパネルの折りたたみ / 展開を切り替えます。

        折りたたみ状態でも append_line() によるログ記録は継続されます。
        展開したときは自動スクロールが有効な場合に最下部にジャンプします。

        Parameters
        ----------
        expanded : bool
            True のとき展開（ログ内容を表示）、False のとき折りたたみ。
        """
        if hasattr(self, "_log_content"):
            self._log_content.setVisible(expanded)
        if hasattr(self, "_collapse_btn"):
            self._collapse_btn.setText("▼" if expanded else "▶")
            self._collapse_btn.setToolTip(
                "クリックしてログパネルを折りたたみます" if expanded
                else "クリックしてログパネルを展開します"
            )
        if expanded and self._auto_scroll:
            # 展開時に最下部へスクロール
            self._text.verticalScrollBar().setValue(
                self._text.verticalScrollBar().maximum()
            )

    def _on_autoscroll_toggled(self, checked: bool) -> None:
        """
        UX改善（新⑤）: 自動スクロールのON/OFFを切り替えます。

        Parameters
        ----------
        checked : bool
            True のとき自動スクロールを有効にします。
        """
        self._auto_scroll = checked
        if checked:
            # ON に戻したとき、最下部までスクロールして最新ログを表示
            self._text.verticalScrollBar().setValue(
                self._text.verticalScrollBar().maximum()
            )

    def _apply_theme_style(self) -> None:
        """現在のテーマに応じてログエリアのスタイルを設定します。"""
        theme = "dark" if ThemeManager.is_dark() else "light"
        styles = LOG_STYLES[theme]
        self._text.setStyleSheet(
            f"background-color: {styles['background']}; "
            f"color: {styles['foreground']};"
        )

    def update_theme(self) -> None:
        """テーマ変更時に呼び出してスタイルを更新します。"""
        self._apply_theme_style()

    @staticmethod
    def _detect_color(line: str) -> str:
        for keyword, color in _LEVEL_COLORS.items():
            if keyword in line:
                return color
        theme = "dark" if ThemeManager.is_dark() else "light"
        return LOG_STYLES[theme]["default_color"]

    def _copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication
        # 改善⑦: 全ライン（フィルター前）をコピー
        lines = [f"[{e['ts']}] {e['text']}" for e in self._all_lines]
        QApplication.clipboard().setText("\n".join(lines))

    def _save_log(self) -> None:
        """
        UX改善（第7回④）: ログをテキストファイルとして保存します。

        QFileDialog を開き、ユーザーが指定した場所にタイムスタンプ付きで保存します。
        保存完了後はボタンラベルを2秒間「✓ 保存しました」に変更してフィードバックします。
        """
        from datetime import datetime as _dt
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtCore import QTimer as _QTimer
        import os

        if not self._all_lines:
            return  # 空ログは保存しない

        # デフォルトファイル名: snap_log_YYYYMMDD_HHMMSS.txt
        default_name = _dt.now().strftime("snap_log_%Y%m%d_%H%M%S.txt")

        path, _ = QFileDialog.getSaveFileName(
            self,
            "ログを保存",
            default_name,
            "テキストファイル (*.txt);;全てのファイル (*)",
        )
        if not path:
            return  # キャンセル

        try:
            lines = [f"[{e['ts']}] {e['text']}" for e in self._all_lines]
            content = "\n".join(lines)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            # 保存成功フィードバック
            if hasattr(self, "_btn_save"):
                self._btn_save.setText("✓ 保存しました")
                _QTimer.singleShot(2000, lambda: self._btn_save.setText("📥 保存"))
        except OSError as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "保存エラー", f"ログの保存に失敗しました:\n{exc}")

    # ------------------------------------------------------------------
    # UX改善③: テキスト検索
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        """検索テキスト変更時にログを再描画します。"""
        self._search_text = text.strip().lower()
        self._redraw_filtered()

    def _focus_search(self) -> None:
        """Ctrl+F: 検索バーにフォーカスを移動します。"""
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def _clear_search(self) -> None:
        """Esc: 検索バーをクリアして全行を表示します。"""
        self._search_edit.clear()
        # clear() は textChanged を発火するので自動的に再描画される

    def _update_search_count(self, count: int) -> None:
        """検索一致件数ラベルを更新します。"""
        if not hasattr(self, '_search_count_lbl'):
            return
        if not self._search_text:
            self._search_count_lbl.setText("")
        else:
            total = len(self._all_lines)
            if count == 0:
                self._search_count_lbl.setText(
                    f"<span style='color:#ef5350;'>0/{total}</span>"
                )
            else:
                self._search_count_lbl.setText(
                    f"<b style='color:#4caf50;'>{count}/{total}</b>"
                )
