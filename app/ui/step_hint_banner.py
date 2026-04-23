"""
app/ui/step_hint_banner.py
初回ステップ訪問時ヒントバナー。

UX改善（新）: 各ステップを初めて訪れたとき、そのステップで何をすべきか・
何ができるかを一行でガイドするバナーを表示します。

「段階的開示」の考え方に基づき、ユーザーが初めてそのステップに足を踏み入れた
タイミングでだけ適切な情報を提供します。同じヒントを何度も表示してうるさく
ならないよう、QSettings に「既読」フラグを記録し、一度閉じたら再表示しません。

各ステップのヒント例:
  STEP1: 「まずは .s8i ファイルを読み込みましょう。ドラッグ&ドロップでも読み込めます。」
  STEP2: 「ケースを追加して解析条件を設定します。テンプレートカードから始めると簡単です。」
  STEP3: 「上のチェックリストが ✅ になったら「X件を解析する」ボタンが有効になります。」
  STEP4: 「推奨閲覧順のクイックリンクをバナーに表示します。」

UX改善④: STEP4 バナーにタブ直接ナビゲーションボタンを追加。
  STEP4 の初回ヒントバナーに「→ ダッシュボード」「→ ケース比較」「→ ランキング」
  のショートカットボタンを追加し、クリックするだけでそのタブに飛べるようにします。
  tabShortcutRequested(tab_index) シグナルを emit するので、外部で
  right_tabs.setCurrentIndex() と接続してください。

使い方:
  banner = StepHintBanner(step_index=0)
  layout.addWidget(banner)
  banner.show_if_first_visit()  # 初回訪問時のみ自動表示
  # STEP4 の場合はタブ遷移シグナルを接続する:
  banner.tabShortcutRequested.connect(lambda idx: right_tabs.setCurrentIndex(idx))
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


_SETTINGS_ORG = "BAUES"
_SETTINGS_APP = "snap-controller"

# 各ステップのヒントテキスト定義
_STEP_HINTS = [
    {
        "icon": "💡",
        "text": "STEP1: .s8i ファイルを読み込む（ドラッグ&ドロップ可）",
        "color": "#1976d2",
        "bg": "#e3f2fd",
        "bg_dark": "#0d2e52",
        "key": "hint_shown_step1",
    },
    {
        "icon": "💡",
        "text": "STEP2: 「＋ 追加」またはテンプレートからケースを追加",
        "color": "#7b1fa2",
        "bg": "#f3e5f5",
        "bg_dark": "#2e1540",
        "key": "hint_shown_step2",
    },
    {
        "icon": "💡",
        "text": "STEP3: チェックリストが揃ったら実行ボタンを押す",
        "color": "#f57c00",
        "bg": "#fff3e0",
        "bg_dark": "#3e2000",
        "key": "hint_shown_step3",
    },
]


class StepHintBanner(QWidget):
    """
    初回ステップ訪問時にのみ表示されるヒントバナー。

    show_if_first_visit() を呼び出すと、QSettings を参照して
    初回のみ自動表示し、以後は非表示のままにします。

    Signals
    -------
    dismissed
        ユーザーがバナーを閉じたときに発火します。
    tabShortcutRequested(int)
        UX改善④: STEP4 バナーのタブショートカットボタンが押されたとき、
        そのタブのインデックスを引数として発火します。
        right_tabs.setCurrentIndex() と接続してください。
    """

    dismissed = Signal()
    # UX改善④: タブ直接ナビゲーション用シグナル
    tabShortcutRequested = Signal(int)

    def __init__(
        self,
        step_index: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._step_index = step_index
        self._hint = _STEP_HINTS[step_index] if 0 <= step_index < len(_STEP_HINTS) else None
        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._setup_ui()
        self.hide()  # デフォルトは非表示

    def _setup_ui(self) -> None:
        """バナーのUIを構築します。"""
        if not self._hint:
            return

        from .theme import ThemeManager
        is_dark = ThemeManager.is_dark()
        bg = self._hint["bg_dark"] if is_dark else self._hint["bg"]
        color = self._hint["color"]

        # UX改善④: STEP4 にタブショートカットがある場合は少し高くする
        tab_shortcuts = self._hint.get("tab_shortcuts", [])
        max_height = 46 if tab_shortcuts else 42
        self.setMaximumHeight(max_height)

        self.setStyleSheet(
            f"StepHintBanner, QWidget {{"
            f"  background-color: {bg};"
            f"  border-bottom: 2px solid {color};"
            f"}}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        # アイコン
        icon_lbl = QLabel(self._hint["icon"])
        icon_lbl.setStyleSheet(f"font-size: 14px; color: {color}; background: transparent;")
        icon_lbl.setFixedWidth(20)
        layout.addWidget(icon_lbl)

        # ヒントテキスト
        text_lbl = QLabel(self._hint["text"])
        text_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; background: transparent;"
        )
        text_lbl.setWordWrap(False)
        layout.addWidget(text_lbl)

        # UX改善④: タブショートカットボタン（STEP4 のみ）
        if tab_shortcuts:
            for btn_label, tab_idx, btn_tooltip in tab_shortcuts:
                shortcut_btn = QPushButton(btn_label)
                shortcut_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  color: {color}; font-size: 10px; padding: 2px 10px;"
                    f"  border: 1px solid {color}; border-radius: 3px;"
                    f"  background: transparent; font-weight: bold;"
                    f"}}"
                    f"QPushButton:hover {{ background-color: {color}; color: white; }}"
                )
                shortcut_btn.setToolTip(btn_tooltip)
                # キャプチャ用に tab_idx をデフォルト引数で固定
                shortcut_btn.clicked.connect(
                    lambda checked=False, idx=tab_idx: self.tabShortcutRequested.emit(idx)
                )
                layout.addWidget(shortcut_btn)

        layout.addStretch(1)

        # 「わかりました」閉じるボタン
        ok_btn = QPushButton("✓ わかりました")
        ok_btn.setStyleSheet(
            f"QPushButton {{"
            f"  color: {color}; font-size: 10px; padding: 3px 10px;"
            f"  border: 1px solid {color}; border-radius: 3px;"
            f"  background: transparent;"
            f"}}"
            f"QPushButton:hover {{ background-color: {color}; color: white; }}"
        )
        ok_btn.setToolTip("閉じる（次回以降非表示）")
        ok_btn.clicked.connect(self._on_dismiss)
        layout.addWidget(ok_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_if_first_visit(self) -> None:
        """誘導ヒントは常時非表示（フィードバックにより無効化）。"""
        return

    def reset_hint(self) -> None:
        """
        ヒントの表示フラグをリセットします（次回訪問時に再表示されます）。
        主にデバッグ/テスト用です。
        """
        if self._hint:
            self._settings.setValue(self._hint["key"], False)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_dismiss(self) -> None:
        """「わかりました」ボタン押下時: バナーを閉じて既読フラグを保存します。"""
        if self._hint:
            self._settings.setValue(self._hint["key"], True)
        self.hide()
        self.dismissed.emit()
