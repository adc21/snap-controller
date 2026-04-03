"""
app/ui/setup_guide_widget.py
プロジェクト進行ステップガイドバー。

初めてのユーザーや設定が不完全な状態のユーザーが
「何をすべきか」を一目で理解できるよう、以下4ステップを
メインウィンドウ上部に常時表示します:

  STEP 1: SNAPパス設定
  STEP 2: モデルファイル読込
  STEP 3: ケース追加
  STEP 4: 解析実行

各ステップの完了状態は外部から update() メソッドで通知します。
クリックするとそのステップのアクションが呼び出されます。
すべてのステップが完了したらウィジェット自体を非表示にします。
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _StepButton(QWidget):
    """
    1ステップを表す小さなボタン風ウィジェット。

    状態:
    - done: チェックマーク付きで緑表示
    - active: 強調表示（次に行うべきステップ）
    - inactive: 薄いグレー表示
    """

    clicked = Signal()

    def __init__(
        self,
        number: int,
        label: str,
        description: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._number = number
        self._label = label
        self._description = description
        self._done = False
        self._active = False
        self._setup_ui()
        self._update_style()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # ステップ番号 / チェックマーク
        self._icon_label = QLabel()
        icon_font = QFont()
        icon_font.setPointSize(14)
        icon_font.setBold(True)
        self._icon_label.setFont(icon_font)
        self._icon_label.setFixedWidth(24)
        self._icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._icon_label)

        # ラベルと説明
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        self._main_label = QLabel(self._label)
        main_font = QFont()
        main_font.setPointSize(9)
        main_font.setBold(True)
        self._main_label.setFont(main_font)
        text_layout.addWidget(self._main_label)

        self._desc_label = QLabel(self._description)
        desc_font = QFont()
        desc_font.setPointSize(7)
        self._desc_label.setFont(desc_font)
        text_layout.addWidget(self._desc_label)

        layout.addLayout(text_layout)

        # クリック可能にする
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"STEP {self._number}: {self._label} — クリックして実行")
        self.setMinimumWidth(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_state(self, done: bool, active: bool) -> None:
        self._done = done
        self._active = active
        self._update_style()

    def _update_style(self) -> None:
        if self._done:
            self._icon_label.setText("✓")
            bg = "#d4edda"
            fg = "#155724"
            border = "#c3e6cb"
            desc_fg = "#1e7e34"
        elif self._active:
            self._icon_label.setText(str(self._number))
            bg = "#cce5ff"
            fg = "#004085"
            border = "#b8daff"
            desc_fg = "#0056b3"
        else:
            self._icon_label.setText(str(self._number))
            bg = "transparent"
            fg = "#999999"
            border = "#dddddd"
            desc_fg = "#aaaaaa"

        self.setStyleSheet(f"""
            _StepButton, QWidget#stepBtn {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
        """)
        # ラベル色
        self._main_label.setStyleSheet(f"color: {fg};")
        self._desc_label.setStyleSheet(f"color: {desc_fg};")
        self._icon_label.setStyleSheet(f"color: {fg};")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._done:
            self.clicked.emit()
        super().mousePressEvent(event)


class SetupGuideWidget(QFrame):
    """
    メインウィンドウ上部に表示するセットアップ進行ガイドバー。

    Signals
    -------
    stepClicked(step_number: int)
        ユーザーがステップをクリックしたときに発火します。
        呼び出し元でステップ番号に応じたアクションを実行してください。
    """

    stepClicked = Signal(int)

    _STEPS = [
        (1, "SNAPパス設定",   "SNAP.exe の場所を指定"),
        (2, "モデル読込",     ".s8i ファイルを選択"),
        (3, "ケース追加",     "ダンパー条件を定義"),
        (4, "解析実行",       "SNAP を起動して計算"),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMaximumHeight(72)
        self._step_widgets: List[_StepButton] = []
        self._completed = [False, False, False, False]
        self._setup_ui()
        self._refresh()

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        title = QLabel("セットアップガイド")
        title_font = QFont()
        title_font.setPointSize(8)
        title.setFont(title_font)
        title.setStyleSheet("color: gray;")
        outer.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(4)

        for i, (num, label, desc) in enumerate(self._STEPS):
            btn = _StepButton(num, label, desc)
            step_num = num  # capture for lambda
            btn.clicked.connect(lambda n=step_num: self.stepClicked.emit(n))
            self._step_widgets.append(btn)
            row.addWidget(btn)

            # → 矢印（最後以外）
            if i < len(self._STEPS) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet("color: #aaaaaa; font-size: 14px;")
                arrow.setAlignment(Qt.AlignCenter)
                arrow.setFixedWidth(18)
                row.addWidget(arrow)

        # 閉じるボタン
        row.addStretch()
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setToolTip("ガイドを非表示にする")
        close_btn.setFlat(True)
        close_btn.setStyleSheet("color: gray; font-size: 12px;")
        close_btn.clicked.connect(self._on_dismiss)
        row.addWidget(close_btn)

        outer.addLayout(row)

    def update_state(
        self,
        snap_configured: bool,
        model_loaded: bool,
        has_cases: bool,
        has_results: bool,
    ) -> None:
        """
        各ステップの完了状態を更新して再描画します。

        Parameters
        ----------
        snap_configured : bool
            SNAP.exe パスが設定済みか。
        model_loaded : bool
            .s8i ファイルが読み込み済みか。
        has_cases : bool
            解析ケースが1件以上あるか。
        has_results : bool
            解析結果が1件以上あるか。
        """
        self._completed = [
            snap_configured,
            model_loaded,
            has_cases,
            has_results,
        ]
        self._refresh()

        # すべて完了したら非表示
        if all(self._completed):
            self.hide()
        else:
            self.show()

    def _refresh(self) -> None:
        """ステップウィジェットの状態を更新します。"""
        # 最初の未完了ステップが「active」
        first_incomplete = next(
            (i for i, done in enumerate(self._completed) if not done),
            None,
        )
        for i, btn in enumerate(self._step_widgets):
            done = self._completed[i]
            active = (i == first_incomplete)
            btn.set_state(done=done, active=active)

    def _on_dismiss(self) -> None:
        """ガイドを一時的に非表示にします（このセッション限り）。"""
        self.hide()
