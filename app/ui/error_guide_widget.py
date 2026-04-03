"""
app/ui/error_guide_widget.py
解析エラー時の「よくある原因と解決策」ガイダンスパネル。

UX改善③（段階的開示）: 解析がエラー終了したとき、ログエリアの上部に
構造化されたガイダンスパネルを自動表示します。

エラーの種類に応じた原因・解決策を箇条書きで提示し、
ユーザーが「ログを読んで自力で判断する」ことを求めずに、
次のアクションを明確に示します。

表示例:
  ┌──────────────────────────────────────────────────────┐
  │ ⚠ 解析エラーが発生しました — よくある原因と解決策    │
  │                                                      │
  │ 1. SNAP 実行ファイルのパスが正しいか確認             │
  │    [⚙ 設定を開く]                                    │
  │ 2. .s8i ファイルが他のプログラムで開かれていないか   │
  │ 3. 出力ディレクトリへの書き込み権限があるか           │
  │ 4. ダンパーパラメータが物理的に有効な範囲か確認      │
  │                                             [閉じる] │
  └──────────────────────────────────────────────────────┘

show_for_case(case: AnalysisCase) を呼ぶとパネルが表示されます。
ログ末尾のキーワードから推定エラー種別を判定し、関連する原因を先頭に表示します。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import qtawesome as qta
from .theme import ThemeManager


# ─────────────────────────────────────────────────────────────────────────────
# よくある原因と解決策の定義
# ─────────────────────────────────────────────────────────────────────────────

_COMMON_CAUSES = [
    {
        "id": "snap_exe",
        "title": "SNAP 実行ファイルのパスが正しくない",
        "detail": "「Snap.exe」が見つからない、またはパスに全角文字・スペースが含まれています。",
        "action_label": "⚙ 設定を開く",
        "action": "open_settings",
        "keywords": ["snap.exe", "snap が見つかりません", "execut", "no such file"],
    },
    {
        "id": "s8i_locked",
        "title": ".s8i ファイルが他のプログラムで開かれている",
        "detail": "SNAP や Excel などが同じファイルを開いていると競合が発生します。他のアプリを閉じてから再実行してください。",
        "action_label": None,
        "action": None,
        "keywords": ["permission", "アクセス拒否", "locked", "used by another"],
    },
    {
        "id": "output_dir",
        "title": "出力ディレクトリへの書き込み権限がない",
        "detail": "ネットワークドライブや保護フォルダへの書き込みが拒否されています。ローカルの書き込み可能なフォルダを指定してください。",
        "action_label": None,
        "action": None,
        "keywords": ["output", "write", "書き込み", "permission denied"],
    },
    {
        "id": "param_range",
        "title": "ダンパーパラメータが物理的に無効な値になっている",
        "detail": "ダンパー定義の Cd・α・剛性 などが 0 または極端な値になっていないか確認してください。",
        "action_label": "✏ ケースを編集",
        "action": "edit_case",
        "keywords": ["parameter", "パラメータ", "invalid", "nan", "infinity"],
    },
    {
        "id": "model_error",
        "title": ".s8i モデルに構文エラーがある",
        "detail": "SNAP 入力ファイルの書式が正しくない可能性があります。SNAP を直接起動してエラーメッセージを確認してください。",
        "action_label": None,
        "action": None,
        "keywords": ["syntax", "parse error", "モデル", "unexpected"],
    },
    {
        "id": "timeout",
        "title": "解析がタイムアウトした",
        "detail": "入力地震波の刻み幅が細かすぎるか、解析時間が長すぎます。解析条件（DT・解析ステップ数）を確認してください。",
        "action_label": None,
        "action": None,
        "keywords": ["timeout", "time out", "タイムアウト"],
    },
]

# 常に表示するデフォルト（キーワードで一致しなかった場合に表示）
_DEFAULT_CAUSES = [_COMMON_CAUSES[0], _COMMON_CAUSES[1], _COMMON_CAUSES[3]]


def _detect_causes(log_text: str) -> list:
    """ログテキストからエラー種別を推定し、関連する原因リストを返します。"""
    if not log_text:
        return _DEFAULT_CAUSES

    lower = log_text.lower()
    matched = []
    remaining = []

    for cause in _COMMON_CAUSES:
        if any(kw in lower for kw in cause["keywords"]):
            matched.append(cause)
        else:
            remaining.append(cause)

    # マッチしたものを先頭に、残りを後ろに（合計最大4件）
    return (matched + remaining)[:4]


# ─────────────────────────────────────────────────────────────────────────────
# ウィジェット
# ─────────────────────────────────────────────────────────────────────────────


class ErrorGuideWidget(QFrame):
    """
    解析エラー時のガイダンスパネル。

    解析がエラーで終了したとき、ログエリアの上部に自動表示されます。
    ログ内容からエラー種別を推定し、最も関連性の高い原因・解決策を先頭に表示します。

    Signals
    -------
    openSettingsRequested
        「設定を開く」ボタンが押されたときに発火します。
    editCaseRequested(case_id: str)
        「ケースを編集」ボタンが押されたときに発火します。
    """

    openSettingsRequested = Signal()
    editCaseRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_case_id: str = ""
        self._setup_ui()
        self.hide()

    def _setup_ui(self) -> None:
        is_dark = ThemeManager.is_dark()
        if is_dark:
            bg = "#3b1f1f"
            border = "#c62828"
            title_color = "#ef9a9a"
            body_color = "#ffcdd2"
        else:
            bg = "#fff3f3"
            border = "#ef5350"
            title_color = "#c62828"
            body_color = "#333333"

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"ErrorGuideWidget, QFrame {{"
            f"  background-color: {bg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 6px;"
            f"  margin: 4px;"
            f"}}"
        )
        self.setMaximumHeight(220)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(6)

        # ---- タイトル行 ----
        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        icon_lbl = QLabel()
        try:
            icon_lbl.setPixmap(
                qta.icon("fa5s.exclamation-triangle", color=title_color).pixmap(16, 16)
            )
        except Exception:
            icon_lbl.setText("⚠")

        title_row.addWidget(icon_lbl)

        title_lbl = QLabel("<b>解析エラーが発生しました ─ よくある原因と解決策</b>")
        title_lbl.setStyleSheet(f"color: {title_color}; font-size: 12px; background: transparent;")
        title_lbl.setTextFormat(Qt.RichText)
        title_row.addWidget(title_lbl, stretch=1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { border: none; background: transparent;"
            f"  color: {title_color}; font-size: 12px; font-weight: bold; }}"
            "QPushButton:hover { background: rgba(0,0,0,0.1); border-radius: 3px; }"
        )
        close_btn.setToolTip("このガイダンスを閉じます")
        close_btn.clicked.connect(self.hide)
        title_row.addWidget(close_btn)

        main_layout.addLayout(title_row)

        # セパレータ
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {border}; max-height: 1px;")
        main_layout.addWidget(sep)

        # ---- 原因リストエリア（動的に更新） ----
        self._causes_container = QWidget()
        self._causes_container.setStyleSheet("background: transparent;")
        self._causes_layout = QVBoxLayout(self._causes_container)
        self._causes_layout.setContentsMargins(0, 0, 0, 0)
        self._causes_layout.setSpacing(3)
        main_layout.addWidget(self._causes_container, stretch=1)

        # ストア
        self._body_color = body_color
        self._title_color = title_color

    def show_for_case(self, case_id: str, case_name: str, log_text: str = "") -> None:
        """
        指定ケースのエラーに対応したガイダンスを表示します。

        Parameters
        ----------
        case_id : str
            エラーが発生したケースの ID。
        case_name : str
            ケース名（表示用）。
        log_text : str
            ログテキスト（エラー種別推定に使用）。省略可能。
        """
        self._current_case_id = case_id

        # 原因を推定
        causes = _detect_causes(log_text)

        # 既存のウィジェットをクリア
        while self._causes_layout.count():
            item = self._causes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 原因リストを構築
        for i, cause in enumerate(causes):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.setContentsMargins(4, 0, 0, 0)

            bullet = QLabel(f"<b>{i + 1}.</b>")
            bullet.setFixedWidth(18)
            bullet.setStyleSheet(f"color: {self._title_color}; background: transparent;")
            bullet.setTextFormat(Qt.RichText)
            row.addWidget(bullet)

            text_col = QVBoxLayout()
            text_col.setSpacing(0)

            title_lbl = QLabel(f"<b>{cause['title']}</b>")
            title_lbl.setStyleSheet(
                f"color: {self._body_color}; font-size: 11px; background: transparent;"
            )
            title_lbl.setTextFormat(Qt.RichText)
            text_col.addWidget(title_lbl)

            if cause.get("detail"):
                detail_lbl = QLabel(cause["detail"])
                detail_lbl.setStyleSheet(
                    f"color: {self._body_color}; font-size: 10px; background: transparent;"
                )
                detail_lbl.setWordWrap(True)
                text_col.addWidget(detail_lbl)

            row.addLayout(text_col, stretch=1)

            # アクションボタン（あれば）
            if cause.get("action_label") and cause.get("action"):
                action_btn = QPushButton(cause["action_label"])
                action_btn.setFixedHeight(22)
                action_btn.setMinimumWidth(100)
                action_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  font-size: 10px; padding: 2px 8px;"
                    f"  border: 1px solid {self._title_color}; border-radius: 3px;"
                    f"  background: transparent; color: {self._title_color};"
                    f"}}"
                    f"QPushButton:hover {{ background: {self._title_color}; color: white; }}"
                )
                _action = cause["action"]
                _cid = self._current_case_id
                if _action == "open_settings":
                    action_btn.clicked.connect(self.openSettingsRequested.emit)
                elif _action == "edit_case":
                    action_btn.clicked.connect(
                        lambda checked=False, cid=_cid: self.editCaseRequested.emit(cid)
                    )
                row.addWidget(action_btn)

            # 行ウィジェットに追加
            row_widget = QWidget()
            row_widget.setStyleSheet("background: transparent;")
            row_widget.setLayout(row)
            self._causes_layout.addWidget(row_widget)

        self._causes_layout.addStretch()
        self.show()
