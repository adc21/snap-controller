"""
app/ui/sidebar_widget.py
ワークフロー主導のステップバイステップ用サイドバー。

UX改善1: 各ステップボタンにプロジェクト状態バッジを表示。
  - STEP1: モデルロード状態（✓ またはファイル名）
  - STEP2: ケース件数バッジ（例: 3件）
  - STEP3: 実行状況バッジ（例: 2/5完了）
  - STEP4: 結果件数バッジ（例: 完了2件）
  update_badge(step_index, text) で外部から更新します。

UX改善（新）: ステップ完了状態ビジュアルインジケーター。
  set_step_state(index, "done"|"active"|"pending") で
  各ステップの左端に完了状態を示すカラーバーを表示します。
  - "done"   : 緑の ✓ バー（そのステップの設定が完了）
  - "active" : 青のインジケーター（現在作業中）
  - "pending": グレー（まだ未着手）
  これにより、ワークフロー上の「どこまで進んだか」が一目でわかります。

UX改善（新③）: 各ステップボタンに詳細ツールチップを追加。
  ボタン名だけでは「このステップで何をするか」「何が必要か」が
  分からないことがあります。各ボタンにマウスを乗せると：
  - そのステップの目的・できること
  - 前提条件（何を済ませてから来るべきか）
  - 操作のヒント（ショートカットキーなど）
  が表示されるようにしました。初めて使うユーザーの「迷い」を減らします。

UX改善（今回追加）: プロジェクト状態サマリーラベル。
  サイドバー最下部に現在のプロジェクト状態を1〜2行でコンパクトに表示します。
  update_project_summary(s8i_name, case_count, done_count) で
  main_window から随時更新されます。
  - 未読み込み: 「モデル未読み込み」（グレー）
  - 一部完了: 「📁 xxx.s8i\n✅ 3/5件完了」（青）
  - 全件完了: 「📁 xxx.s8i\n✅ 全5件完了」（緑・太字）
  どのステップにいても画面上部のバッジを見なくても状態を把握できます。
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QButtonGroup,
    QSizePolicy, QLabel, QFrame
)
import qtawesome as qta
from .theme import ThemeManager

# UX改善（新）: ステップ状態の定数
STEP_STATE_DONE    = "done"     # 完了（緑）
STEP_STATE_ACTIVE  = "active"   # 作業中（青）
STEP_STATE_PENDING = "pending"  # 未着手（グレー）

# 各状態の表示設定
_STATE_STYLES = {
    STEP_STATE_DONE: {
        "color": "#4caf50",
        "text": "✓",
        "tooltip": "設定済み・完了",
    },
    STEP_STATE_ACTIVE: {
        "color": "#1976d2",
        "text": "▶",
        "tooltip": "現在作業中",
    },
    STEP_STATE_PENDING: {
        "color": "#9e9e9e",
        "text": "○",
        "tooltip": "未着手",
    },
}


class SidebarWidget(QWidget):
    """
    ワークフロー主導のステップバイステップ用サイドバー。
    ステップの切り替えを通知します。

    UX改善1: update_badge(step_index, text) でバッジテキストを更新できます。
    バッジは各ステップボタンの下に小さく表示され、ユーザーが現在の
    プロジェクト状態をひと目で把握できます。
    """
    stepChanged = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setStyleSheet("""
            SidebarWidget {
                background-color: palette(base);
                border-right: 1px solid palette(mid);
            }
            QToolButton {
                border: none;
                text-align: left;
                padding: 12px 16px;
                font-size: 13px;
                font-weight: bold;
                border-radius: 6px;
                margin: 4px 8px 0px 8px;
            }
            QToolButton:hover {
                background-color: palette(alternate-base);
            }
            QToolButton:checked {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(0)

        # Title / Logo area
        title_label = QLabel("SNAP Controller")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: 900; margin-bottom: 16px;")
        layout.addWidget(title_label)

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        self._btn_group.idClicked.connect(self._on_step_clicked)

        steps = [
            ("STEP 1: モデル設定", "fa5s.building"),
            ("STEP 2: ケース設計", "fa5s.tools"),
            ("STEP 3: 解析実行", "fa5s.rocket"),
            ("STEP 4: 結果・戦略", "fa5s.chart-bar"),
        ]

        # UX改善（新③）: 各ステップボタンに表示する詳細ツールチップ
        _step_tooltips = [
            # STEP1
            "【STEP 1: モデル設定】\n"
            "解析に使う SNAP 入力ファイル (.s8i) を読み込みます。\n\n"
            "できること:\n"
            "  • s8i ファイルの選択・ドラッグ&ドロップ\n"
            "  • モデル概要（節点数・層数・ダンパー定義）の確認\n"
            "  • s8i ファイル内容のプレビュー\n"
            "  • 最近使ったファイルの再読み込み\n\n"
            "完了したら → STEP2 へ",

            # STEP2
            "【STEP 2: ケース設計】\n"
            "比較する解析ケースを複数設定します。\n\n"
            "できること:\n"
            "  • ケースの追加・複製・削除\n"
            "  • ダンパー種別・パラメータ・基数の変更\n"
            "  • グループ分けによるケース整理\n"
            "  • パラメータスイープで一括生成\n"
            "  • テンプレートの保存・適用\n\n"
            "前提: STEP1 で s8i ファイルを読み込んでいること\n"
            "完了したら → STEP3 へ",

            # STEP3
            "【STEP 3: 解析実行】\n"
            "設定したケースを SNAP で解析します。\n\n"
            "できること:\n"
            "  • 実行するケースのチェック選択\n"
            "  • 解析実行（F5 キーでも起動）\n"
            "  • 実行状況・進捗のリアルタイム確認\n"
            "  • 解析ログのフィルタリング・検索\n\n"
            "前提: STEP2 でケースが1件以上あること\n"
            "      SNAP 実行ファイル (Snap.exe) が設定済みであること\n"
            "完了したら → STEP4 へ",

            # STEP4
            "【STEP 4: 結果・戦略】\n"
            "解析結果を確認・比較して次の戦略を検討します。\n\n"
            "できること:\n"
            "  • ダッシュボードで全ケースの概要把握\n"
            "  • 複数ケースの応答値グラフ比較\n"
            "  • 層別応答分布の確認\n"
            "  • ランキングで最良ケースを特定\n"
            "  • 感度分析でパラメータ影響を評価\n"
            "  • 解析戦略メモの記録\n\n"
            "次のラウンドへ → STEP2 に戻って新ケースを設計",
        ]

        self._buttons = []
        self._badge_labels: list[QLabel] = []
        self._state_indicators: list[QLabel] = []  # UX改善（新）: 状態インジケーター
        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#333333"

        for idx, (label, icon_name) in enumerate(steps):
            # ---- UX改善（新）: ステップボタン + 状態インジケーターを横並びに ----
            btn_row_widget = QWidget()
            btn_row_layout = QHBoxLayout(btn_row_widget)
            btn_row_layout.setContentsMargins(0, 0, 4, 0)
            btn_row_layout.setSpacing(0)

            # ステップボタン
            btn = QToolButton()
            btn.setText(label)
            btn.setIcon(qta.icon(icon_name, color=icon_color, color_active="white"))
            btn.setIconSize(QSize(22, 22))
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setCheckable(True)
            # UX改善（新③）: ステップボタンに詳細ツールチップを設定
            btn.setToolTip(_step_tooltips[idx])
            self._btn_group.addButton(btn, idx)
            btn_row_layout.addWidget(btn, stretch=1)

            # UX改善（新）: 状態インジケーター（ボタン右端に固定幅のラベル）
            state_lbl = QLabel(_STATE_STYLES[STEP_STATE_PENDING]["text"])
            state_lbl.setFixedWidth(20)
            state_lbl.setAlignment(Qt.AlignCenter)
            state_lbl.setStyleSheet(
                f"color: {_STATE_STYLES[STEP_STATE_PENDING]['color']};"
                "font-size: 11px; font-weight: bold;"
            )
            state_lbl.setToolTip(_STATE_STYLES[STEP_STATE_PENDING]["tooltip"])
            btn_row_layout.addWidget(state_lbl)
            self._state_indicators.append(state_lbl)

            layout.addWidget(btn_row_widget)
            self._buttons.append(btn)

            # ---- UX改善1: バッジラベル（ボタン直下に小さく表示） ----
            badge = QLabel("")
            badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            badge.setStyleSheet(
                "color: #888888; font-size: 10px; padding-right: 16px; "
                "padding-bottom: 4px; margin: 0px 8px;"
            )
            badge.setVisible(False)  # バッジテキストがある時だけ表示
            layout.addWidget(badge)
            self._badge_labels.append(badge)

        layout.addStretch()

        # ---- セパレーターとヒント表示 ----
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: palette(mid); margin: 0 8px;")
        layout.addWidget(sep)

        hint = QLabel("①→②→③→④ の順に進めます")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888888; font-size: 10px; padding: 8px 12px 4px 12px;")
        layout.addWidget(hint)

        # UX改善（新）: プロジェクト状態サマリーラベル
        # update_project_summary() で外部から更新します。
        # プロジェクトが未読み込みの場合はグレーで案内テキストを表示し、
        # 読み込み後はファイル名・ケース数・完了数をコンパクトに1行表示します。
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: palette(mid); margin: 0 8px;")
        layout.addWidget(sep2)

        self._status_label = QLabel("モデル未読み込み")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            "color: #888888; font-size: 10px; padding: 4px 12px 8px 12px;"
        )
        self._status_label.setToolTip(
            "現在のプロジェクト状態を要約表示します。\n"
            "モデルファイル名 / ケース数 / 解析完了数 が表示されます。"
        )
        layout.addWidget(self._status_label)

        # デフォルトでSTEP 1を選択
        if self._buttons:
            self._buttons[0].setChecked(True)

    def _on_step_clicked(self, id: int) -> None:
        self.stepChanged.emit(id)

    def set_current_step(self, index: int) -> None:
        """外部からステップを変更する（例: ボタンなどを押したとき）"""
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
            # idClickedは自動発火しない仕様のためシグナルを手動で発行する
            self.stepChanged.emit(index)

    def update_badge(self, step_index: int, text: str) -> None:
        """
        UX改善1: 指定ステップのバッジテキストを更新します。

        Parameters
        ----------
        step_index : int
            ステップインデックス（0〜3）。
        text : str
            バッジに表示するテキスト。空文字の場合はバッジを非表示にします。
        """
        if 0 <= step_index < len(self._badge_labels):
            lbl = self._badge_labels[step_index]
            if text:
                lbl.setText(text)
                lbl.setVisible(True)
            else:
                lbl.setVisible(False)

    def update_project_summary(
        self,
        s8i_name: str = "",
        case_count: int = 0,
        done_count: int = 0,
    ) -> None:
        """
        UX改善（新）: サイドバー下部のプロジェクト状態サマリーを更新します。

        ワークフローのどこにいても「今どこまで進んでいるか」を
        1行で把握できるように、モデル名・ケース数・完了数を表示します。

        Parameters
        ----------
        s8i_name : str
            読み込んでいる .s8i ファイルのベース名。未読み込みなら空文字。
        case_count : int
            現在のプロジェクトにある解析ケース数。
        done_count : int
            そのうち解析が完了しているケース数。
        """
        if not hasattr(self, "_status_label"):
            return

        if not s8i_name:
            self._status_label.setText("モデル未読み込み")
            self._status_label.setStyleSheet(
                "color: #888888; font-size: 10px; padding: 4px 12px 8px 12px;"
            )
            return

        # ファイル名が長い場合は末尾を省略
        display_name = s8i_name if len(s8i_name) <= 18 else s8i_name[:15] + "…"

        if case_count == 0:
            status_text = f"📁 {display_name}\nケース: なし"
        elif done_count == 0:
            status_text = f"📁 {display_name}\nケース: {case_count}件（未解析）"
        elif done_count < case_count:
            status_text = f"📁 {display_name}\n✅ {done_count}/{case_count}件完了"
            self._status_label.setStyleSheet(
                "color: #1976d2; font-size: 10px; padding: 4px 12px 8px 12px;"
            )
            self._status_label.setText(status_text)
            return
        else:
            status_text = f"📁 {display_name}\n✅ 全{done_count}件完了"
            self._status_label.setStyleSheet(
                "color: #4caf50; font-size: 10px; font-weight: bold;"
                "padding: 4px 12px 8px 12px;"
            )
            self._status_label.setText(status_text)
            return

        self._status_label.setStyleSheet(
            "color: #888888; font-size: 10px; padding: 4px 12px 8px 12px;"
        )
        self._status_label.setText(status_text)

    def set_step_state(self, step_index: int, state: str) -> None:
        """
        UX改善（新）: ステップの完了状態を視覚的に示すインジケーターを更新します。

        各ステップボタンの右端にある小さな記号で「完了 / 作業中 / 未着手」を
        カラーコードで表示し、ユーザーがワークフローの進行状況を一目で把握できます。

        Parameters
        ----------
        step_index : int
            ステップインデックス（0〜3）。
        state : str
            "done"    完了（緑 ✓）
            "active"  作業中（青 ▶）
            "pending" 未着手（グレー ○）
        """
        if not (0 <= step_index < len(self._state_indicators)):
            return
        s = _STATE_STYLES.get(state, _STATE_STYLES[STEP_STATE_PENDING])
        lbl = self._state_indicators[step_index]
        lbl.setText(s["text"])
        lbl.setStyleSheet(
            f"color: {s['color']}; font-size: 11px; font-weight: bold;"
        )
        lbl.setToolTip(s["tooltip"])
