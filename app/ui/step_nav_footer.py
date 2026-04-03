"""
app/ui/step_nav_footer.py
ワークフローステップのナビゲーションフッター。

UX改善①新: 各ステップのコンテンツ下部に「← 戻る」「次へ →」ボタンを追加し、
ユーザーがサイドバーを使わなくても自然にステップを進められるようにします。

ボタンのラベルは呼び出し側で設定し、具体的な次のアクションをわかりやすく示します。
例:
  STEP1: 「← 戻る」 / 「ケースを設計する (STEP2) →」
  STEP2: 「← モデル設定 (STEP1)」 / 「解析を実行する (STEP3) →」
  STEP3: 「← ケース設計 (STEP2)」 / 「結果を確認する (STEP4) →」

UX改善（スマートデフォルト）: 「次へ」ボタンが無効なとき「なぜ進めないか」を
インラインヒントラベルで表示します。ツールチップよりも視認性が高く、
「何をすれば次へ進めるか」を即座に把握できます。
  set_next_hint(text) でヒントテキストを設定し、
  set_next_enabled(False) のタイミングで自動的に表示されます。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

import qtawesome as qta
from .theme import ThemeManager


class StepNavFooter(QWidget):
    """
    ステップナビゲーションフッター。

    Signals
    -------
    backRequested
        「← 戻る」ボタンが押されたときに発火。
    nextRequested
        「次へ →」ボタンが押されたときに発火。
    """

    backRequested = Signal()
    nextRequested = Signal()

    def __init__(
        self,
        back_label: str = "← 戻る",
        next_label: str = "次へ →",
        show_back: bool = True,
        show_next: bool = True,
        next_primary: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Parameters
        ----------
        back_label : str
            「戻る」ボタンのテキスト。
        next_label : str
            「次へ」ボタンのテキスト。
        show_back : bool
            「戻る」ボタンを表示するか（STEP1 では不要なので False にする）。
        show_next : bool
            「次へ」ボタンを表示するか（STEP4 では不要なので False にする）。
        next_primary : bool
            「次へ」ボタンをプライマリスタイルで強調するか。
        """
        super().__init__(parent)
        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#444444"

        # セパレーターライン
        frame = QFrame(self)
        frame.setFrameShape(QFrame.HLine)
        frame.setFrameShadow(QFrame.Sunken)
        frame.setStyleSheet("color: palette(mid);")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # UX改善: 「次へ」ボタンが無効な場合に表示するヒントテキスト
        self._next_hint_text: str = ""

        # 「← 戻る」ボタン
        self._btn_back = QPushButton(back_label)
        self._btn_back.setIcon(qta.icon("fa5s.chevron-left", color=icon_color))
        self._btn_back.setStyleSheet(
            "QPushButton { padding: 5px 14px; font-size: 12px; }"
        )
        self._btn_back.clicked.connect(self.backRequested.emit)
        self._btn_back.setVisible(show_back)
        layout.addWidget(self._btn_back)

        layout.addStretch()

        # UX改善: 「次へ進むには」インラインヒントラベル（無効時のみ表示）
        self._hint_label = QLabel()
        self._hint_label.setStyleSheet(
            "color: #1976d2;"
            "font-size: 11px;"
            "background: transparent;"
            "padding: 2px 8px;"
        )
        self._hint_label.setVisible(False)
        layout.addWidget(self._hint_label)

        # 「次へ →」ボタン
        self._btn_next = QPushButton(next_label)
        self._btn_next.setIcon(qta.icon("fa5s.chevron-right", color="#ffffff" if next_primary else icon_color))
        self._btn_next.setLayoutDirection(self._btn_next.layoutDirection())
        # アイコンを右側に表示するため setLayoutDirection で RTL にする
        from PySide6.QtCore import Qt as _Qt
        self._btn_next.setLayoutDirection(_Qt.RightToLeft)

        if next_primary:
            self._btn_next.setStyleSheet(
                "QPushButton {"
                "  background-color: #1976d2;"
                "  color: white;"
                "  padding: 6px 18px;"
                "  font-size: 12px;"
                "  font-weight: bold;"
                "  border-radius: 4px;"
                "  border: none;"
                "}"
                "QPushButton:hover {"
                "  background-color: #1565c0;"
                "}"
                "QPushButton:pressed {"
                "  background-color: #0d47a1;"
                "}"
                "QPushButton:disabled {"
                "  background-color: palette(mid);"
                "  color: palette(shadow);"
                "}"
            )
        else:
            self._btn_next.setStyleSheet(
                "QPushButton { padding: 5px 14px; font-size: 12px; }"
            )
        self._btn_next.clicked.connect(self.nextRequested.emit)
        self._btn_next.setVisible(show_next)
        layout.addWidget(self._btn_next)

    def set_next_enabled(self, enabled: bool) -> None:
        """
        「次へ」ボタンの有効/無効を切り替えます。

        UX改善: 無効化するとき、set_next_hint() で設定したヒントテキストを
        インラインラベルに表示します。有効化したら自動的に非表示にします。
        """
        self._btn_next.setEnabled(enabled)
        # ヒントが設定されている場合のみ表示/非表示を切り替える
        if not enabled and self._next_hint_text:
            self._hint_label.setText(f"💡 {self._next_hint_text}")
            self._hint_label.setVisible(True)
        else:
            self._hint_label.setVisible(False)

    def set_next_hint(self, text: str) -> None:
        """
        UX改善: 「次へ」ボタンが無効なときに表示するヒントテキストを設定します。

        ヒントは「次へ進むには何をすればよいか」を簡潔に示します。
        例: "s8iファイルを読み込むとSTEP2へ進めます"

        Parameters
        ----------
        text : str
            ヒントテキスト（「💡 」プレフィックスは自動付与されます）。
        """
        self._next_hint_text = text
        # 現在ボタンが無効なら即座に表示を更新する
        if not self._btn_next.isEnabled() and text:
            self._hint_label.setText(f"💡 {text}")
            self._hint_label.setVisible(True)
        elif not text:
            self._hint_label.setVisible(False)

    def set_back_enabled(self, enabled: bool) -> None:
        """「戻る」ボタンの有効/無効を切り替えます。"""
        self._btn_back.setEnabled(enabled)

    def update_labels(self, back_label: str = "", next_label: str = "") -> None:
        """ボタンラベルを動的に更新します。"""
        if back_label:
            self._btn_back.setText(back_label)
        if next_label:
            self._btn_next.setText(next_label)
