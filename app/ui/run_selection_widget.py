"""
app/ui/run_selection_widget.py
解析実行ケース選択ウィジェット。

UX改善④ 第5回 (run_selection_widget.py):
  「エラーのみ選択（再実行）」ボタン + ケース状態ライブ集計ラベル追加。
  既存の「未実行を選択」は PENDING+ERROR を一括選択しますが、
  エラーケースだけを絞って再実行したい場面（原因修正後の確認実行）に
  「❌ エラーのみ選択」ボタンを追加します。
  また、ケースリストの下部にリアルタイム状態集計ラベルを追加します。
  「待機: X件 / 完了: Y件 / エラー: Z件 / 選択中: W件」を常時表示し、
  実行前に「今から何件走らせるのか」と「プロジェクト全体の進捗」を
  ひと目で把握できます。

UX改善（今回追加）②: 解析完了後の自動STEP4遷移カウントダウン。
  解析完了バナーに5秒カウントダウンを追加しました。
  タイマーが0になると自動的にSTEP4（結果・戦略）へ移動します。
  「キャンセル」ボタンまたはバナーの「✕」で自動遷移を停止できます。
  手動で「結果を確認する →」を押してもすぐに遷移できます。
  解析を走らせたまま他の作業をしていても、完了したら自動でSTEP4に誘導されます。

UX改善（第9回②）: 選択ケース推定所要時間バナー追加。
  実行ボタンの直上に「🕐 推定所要時間: 約X分（X件 × 約60秒/件）」を表示する
  ミニバナーを追加します。チェックされたケース数が変化するたびにリアルタイム更新されます。
  0件選択時は「ケースを選択してください」、1件以上で時間を表示します。
  `_est_time_banner` QFrame と `_update_est_time_banner()` メソッドを追加。

UX改善4: 解析実行前チェックリストパネルを上部に追加。
  解析に必要な設定が整っているかをチェックリスト形式で表示し、
  問題がある場合はその箇所へのリンクボタンを提供します。
  - s8iモデルファイル設定済み？
  - SNAP実行ファイル設定済み？
  - 解析ケースが存在する？
  すべてOKの場合のみ、実行ボタンが有効になります。

UX改善③新: 解析完了バナーを追加。
  バッチ解析が完了したとき、STEP3 内に目立つバナーを表示し
  「結果を確認する (STEP4) →」ボタンで素早く結果画面へ移動できます。
  ステータスバーのメッセージよりも視認性が高く、見逃しにくい設計です。

UX改善（新）: 完了バナーにベストケース情報を追加。
  解析完了バナーに「🏆 最良ケース: {ケース名}（最小層間変形角: X rad）」を
  表示することで、STEP4 に移動する前に最良ケースがどれかをひと目で把握できます。
  show_completion_banner() に best_case_info 引数を追加しました。

UX改善（新）: 実行ボタンのリアルタイム件数反映。
  チェックリストでケースを選択・解除するたびに、実行ボタンのラベルが
  「🚀 3件を解析する」のように選択数を反映してリアルタイムに更新されます。
  - 0件選択時: ボタンが無効化され「（ケースを選択してください）」と表示
  - 1件以上選択時: 「🚀 X件を解析する」と表示し実行ボタンが有効化
  何件走らせるかがボタンを見るだけで即座に分かり、操作ミスを防ぎます。
"""

from __future__ import annotations
from typing import Optional, List, Callable
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget,
    QListWidgetItem, QGroupBox, QMessageBox, QLabel, QFrame, QProgressBar
)
import qtawesome as qta
from .theme import ThemeManager

# UX改善②新: 自動遷移カウントダウン秒数
_AUTO_TRANSITION_SEC = 5


class RunSelectionWidget(QWidget):
    """
    解析実行対象ケースを選択するウィジェット。

    UX改善4: 上部に事前チェックリストパネルを表示します。
    UX改善③新: 解析完了バナーを表示します。
    """

    runSelectedRequested = Signal(list)
    # 設定ダイアログを開くよう外部に要求するシグナル
    openSettingsRequested = Signal()
    openModelRequested = Signal()
    # UX改善③新: STEP4（結果確認）へ移動するよう外部に要求するシグナル
    viewResultsRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._project: Optional[object] = None
        self._snap_exe_path: str = ""
        # UX改善②新: 自動STEP4遷移カウントダウン用タイマー
        self._countdown_remaining: int = 0
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)  # 1秒ごとにカウント
        self._countdown_timer.timeout.connect(self._on_countdown_tick)
        self._setup_ui()

    def set_project(self, project) -> None:
        self._project = project
        self.refresh()

    def set_snap_exe_path(self, path: str) -> None:
        """UX改善4: SNAP実行ファイルパスを更新してチェックリストを再評価します。"""
        self._snap_exe_path = path or ""
        self._refresh_checklist()

    def refresh(self) -> None:
        self._list.clear()
        if not self._project:
            self._refresh_checklist()
            return
        for case in self._project.cases:
            status_symbol = ""
            if case.status.name == "PENDING":
                status_symbol = "⏳"
            elif case.status.name == "RUNNING":
                status_symbol = "▶️"
            elif case.status.name == "COMPLETED":
                status_symbol = "✅"
            elif case.status.name == "ERROR":
                status_symbol = "❌"

            # UX改善⑤新: 完了ケースは前回の結果サマリーをリスト項目に表示
            result_hint = ""
            tooltip_text = ""
            if case.status.name == "COMPLETED" and case.result_summary:
                rs = case.result_summary
                drift = rs.get("max_story_drift") or rs.get("max_drift")
                acc = rs.get("max_acc")
                parts = []
                if drift is not None:
                    try:
                        parts.append(f"変形角 {float(drift):.4f} rad")
                    except (TypeError, ValueError):
                        pass
                if acc is not None:
                    try:
                        parts.append(f"加速度 {float(acc):.2f} m/s²")
                    except (TypeError, ValueError):
                        pass
                if parts:
                    result_hint = "  [" + " / ".join(parts) + "]"
                # ツールチップに詳細を表示
                tooltip_lines = [f"【{case.name}】の前回解析結果:"]
                _key_labels = [
                    ("max_story_drift", "最大層間変形角 [rad]"),
                    ("max_drift",        "最大層間変形角 [rad]"),
                    ("max_acc",          "最大絶対加速度 [m/s²]"),
                    ("max_disp",         "最大相対変位 [m]"),
                    ("max_vel",          "最大相対速度 [m/s]"),
                    ("shear_coeff",      "せん断力係数 [—]"),
                    ("max_otm",          "最大転倒モーメント [kN·m]"),
                ]
                added_keys: set = set()
                for key, label in _key_labels:
                    if key in added_keys:
                        continue
                    val = rs.get(key)
                    if val is not None:
                        try:
                            tooltip_lines.append(f"  {label}: {float(val):.4g}")
                            added_keys.add(key)
                        except (TypeError, ValueError):
                            pass
                if len(tooltip_lines) > 1:
                    tooltip_text = "\n".join(tooltip_lines)
            elif case.status.name == "ERROR":
                tooltip_text = f"【{case.name}】解析中にエラーが発生しました。\nチェックしてもう一度実行できます。"
            elif case.status.name == "PENDING":
                tooltip_text = f"【{case.name}】未実行（実行待ち）"

            item = QListWidgetItem(f"{status_symbol} {case.name}{result_hint}")
            item.setData(Qt.UserRole, case.id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            if tooltip_text:
                item.setToolTip(tooltip_text)
            # デフォルト: 未実行・エラーのケースにチェック
            if case.status.name in ("PENDING", "ERROR"):
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self._list.addItem(item)
        self._refresh_checklist()
        self._update_error_panel()
        # UX改善④ 第5回: ケース状態集計ラベルを更新
        self._update_case_status_label()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ---- UX改善4: 事前チェックリストパネル ----
        check_group = QGroupBox("解析実行前チェック")
        check_layout = QVBoxLayout(check_group)
        check_layout.setSpacing(4)
        check_layout.setContentsMargins(8, 8, 8, 8)

        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#333333"

        # チェック1: s8iファイル
        self._chk_s8i = self._make_check_row(
            "① s8iモデルファイルが選択されています",
            check_layout,
        )
        # チェック2: SNAP実行ファイル
        self._chk_snap = self._make_check_row(
            "② SNAP実行ファイル (Snap.exe) が設定されています",
            check_layout,
        )
        # チェック3: ケース存在
        self._chk_cases = self._make_check_row(
            "③ 解析ケースが1件以上追加されています",
            check_layout,
        )

        layout.addWidget(check_group)

        # ---- UX改善③（新）: 全チェックOK時「準備完了」バナー ----
        # すべての前提条件が満たされたとき、目立つ緑バナーで
        # 「解析の準備ができました」とユーザーに伝えます。
        # ケースの実行状況（未実行/完了/エラー件数）も一目で確認できます。
        self._ready_banner = QFrame()
        self._ready_banner.setFrameShape(QFrame.StyledPanel)
        self._ready_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #e8f5e9;"
            "  border: 1px solid #66bb6a;"
            "  border-left: 4px solid #2e7d32;"
            "  border-radius: 4px;"
            "  margin: 0px 0px 2px 0px;"
            "}"
        )
        _ready_layout = QHBoxLayout(self._ready_banner)
        _ready_layout.setContentsMargins(10, 6, 10, 6)
        _ready_layout.setSpacing(8)

        _ready_icon = QLabel("✅")
        _ready_icon.setStyleSheet(
            "font-size: 16px; background: transparent; border: none;"
        )
        _ready_layout.addWidget(_ready_icon)

        _ready_col = QVBoxLayout()
        _ready_col.setSpacing(1)
        _ready_col.setContentsMargins(0, 0, 0, 0)

        _ready_title = QLabel("<b>解析の準備ができました</b>")
        _ready_title.setStyleSheet(
            "color: #1b5e20; font-size: 12px; background: transparent; border: none;"
        )
        _ready_title.setTextFormat(Qt.RichText)
        _ready_col.addWidget(_ready_title)

        self._ready_status_lbl = QLabel("")
        self._ready_status_lbl.setStyleSheet(
            "color: #388e3c; font-size: 10px; background: transparent; border: none;"
        )
        self._ready_status_lbl.setTextFormat(Qt.RichText)
        _ready_col.addWidget(self._ready_status_lbl)

        _ready_layout.addLayout(_ready_col, stretch=1)
        self._ready_banner.hide()  # 初期は非表示（前提条件未達のため）
        layout.addWidget(self._ready_banner)

        # ---- ケース選択エリア ----
        group = QGroupBox("解析実行対象の選択")
        g_layout = QVBoxLayout(group)
        g_layout.setSpacing(8)

        btn_row = QHBoxLayout()
        btn_all = QPushButton("全選択")
        btn_all.setToolTip("すべてのケースを選択します")
        btn_all.clicked.connect(lambda: self._set_all_checked(True))
        btn_none = QPushButton("選択解除")
        btn_none.setToolTip("すべてのケースの選択を解除します")
        btn_none.clicked.connect(lambda: self._set_all_checked(False))
        btn_pending = QPushButton("未実行を選択")
        btn_pending.setToolTip("PENDING / ERROR 状態のケースだけを選択します")
        btn_pending.clicked.connect(self._select_pending)

        # UX改善④ 第5回: エラーのみ選択ボタン
        btn_errors = QPushButton("❌ エラーのみ選択")
        btn_errors.setToolTip(
            "エラーが発生したケースだけを選択します。\n"
            "原因を修正したあとエラーケースだけを再実行したい場合に便利です。"
        )
        btn_errors.setStyleSheet(
            "QPushButton { color: #c62828; border: 1px solid #ef9a9a;"
            "  border-radius: 3px; padding: 2px 8px; font-size: 11px; }"
            "QPushButton:hover { background: #ffebee; }"
        )
        btn_errors.clicked.connect(self._select_errors_only)

        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addWidget(btn_pending)
        btn_row.addWidget(btn_errors)
        btn_row.addStretch()
        g_layout.addLayout(btn_row)

        self._list = QListWidget()
        self._list.setMaximumHeight(150)
        # UX改善（新）: チェック状態変化時にボタンラベルをリアルタイム更新
        self._list.itemChanged.connect(self._update_run_button_label)
        # UX改善④ 第5回: チェック変化時に集計ラベルも更新
        self._list.itemChanged.connect(self._update_case_status_label)
        # UX改善（第9回②）: チェック変化時に推定所要時間バナーも更新
        self._list.itemChanged.connect(self._update_est_time_banner)
        g_layout.addWidget(self._list)

        # UX改善④ 第5回: ケース状態ライブ集計ラベル
        self._case_status_label = QLabel("　")
        self._case_status_label.setTextFormat(Qt.RichText)
        self._case_status_label.setStyleSheet("font-size: 10px; padding: 1px 4px;")
        g_layout.addWidget(self._case_status_label)

        # ---- UX改善（第9回②）: 選択ケース推定所要時間バナー ----
        self._est_time_banner = QFrame()
        self._est_time_banner.setFrameShape(QFrame.NoFrame)
        self._est_time_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #e8eaf6;"
            "  border: 1px solid #9fa8da;"
            "  border-radius: 4px;"
            "  margin: 0px;"
            "}"
        )
        _est_time_layout = QHBoxLayout(self._est_time_banner)
        _est_time_layout.setContentsMargins(10, 4, 10, 4)
        _est_time_layout.setSpacing(6)

        _est_icon = QLabel("🕐")
        _est_icon.setStyleSheet("font-size: 13px; background: transparent; border: none;")
        _est_icon.setFixedWidth(20)
        _est_time_layout.addWidget(_est_icon)

        self._est_time_lbl = QLabel("ケースを選択すると推定所要時間が表示されます")
        self._est_time_lbl.setStyleSheet(
            "color: #283593; font-size: 10px; background: transparent; border: none;"
        )
        _est_time_layout.addWidget(self._est_time_lbl, stretch=1)

        g_layout.addWidget(self._est_time_banner)

        # ── UX改善（第12回③）: 実行前ケース種別内訳サマリーカード ──────────────────
        # 選択中のケースを「ダンパー変更あり / 配置変更あり / ベースライン / 要確認」に
        # 分類してコンパクトなカードで表示します。何を実行するかを事前に確認できます。
        self._pre_run_summary_frame = QFrame()
        self._pre_run_summary_frame.setFrameShape(QFrame.NoFrame)
        self._pre_run_summary_frame.setStyleSheet(
            "QFrame {"
            "  background-color: #fafafa;"
            "  border: 1px solid #e0e0e0;"
            "  border-radius: 4px;"
            "  margin: 0px 0px 2px 0px;"
            "}"
        )
        _pre_run_layout = QHBoxLayout(self._pre_run_summary_frame)
        _pre_run_layout.setContentsMargins(8, 4, 8, 4)
        _pre_run_layout.setSpacing(6)

        _pre_run_title = QLabel("実行内容:")
        _pre_run_title.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #555; "
            "background: transparent; border: none;"
        )
        _pre_run_layout.addWidget(_pre_run_title)

        self._pre_run_summary_lbl = QLabel("—")
        self._pre_run_summary_lbl.setStyleSheet(
            "font-size: 10px; color: #333; background: transparent; border: none;"
        )
        self._pre_run_summary_lbl.setWordWrap(False)
        _pre_run_layout.addWidget(self._pre_run_summary_lbl, stretch=1)

        self._pre_run_warn_lbl = QLabel("")
        self._pre_run_warn_lbl.setStyleSheet(
            "font-size: 10px; color: #c62828; background: transparent; border: none;"
        )
        self._pre_run_warn_lbl.hide()
        _pre_run_layout.addWidget(self._pre_run_warn_lbl)

        self._pre_run_summary_frame.hide()  # 初期は非表示（ケース選択前）
        g_layout.addWidget(self._pre_run_summary_frame)

        self._btn_run = QPushButton("🚀 選択したケースを解析実行")
        self._btn_run.setStyleSheet("font-weight: bold; padding: 6px; font-size: 14px;")
        self._btn_run.setToolTip(
            "チェックリストがすべて ✅ の場合のみ実行できます"
        )
        self._btn_run.clicked.connect(self._on_run_clicked)
        g_layout.addWidget(self._btn_run)

        layout.addWidget(group)

        # ---- UX改善③新: 解析完了バナー ----
        self._completion_banner = QFrame()
        self._completion_banner.setFrameShape(QFrame.StyledPanel)
        self._completion_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #1b5e20;"
            "  border: 2px solid #4caf50;"
            "  border-radius: 6px;"
            "  padding: 4px;"
            "}"
        )
        banner_layout = QHBoxLayout(self._completion_banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(10)

        icon_color_banner = "#ffffff"
        banner_icon = QLabel()
        banner_icon.setPixmap(
            qta.icon("fa5s.check-circle", color=icon_color_banner).pixmap(24, 24)
        )
        banner_layout.addWidget(banner_icon)

        _banner_text_col = QVBoxLayout()
        _banner_text_col.setSpacing(2)
        _banner_text_col.setContentsMargins(0, 0, 0, 0)

        self._banner_text = QLabel("解析が完了しました")
        self._banner_text.setStyleSheet(
            "color: #ffffff; font-size: 13px; font-weight: bold; background: transparent; border: none;"
        )
        _banner_text_col.addWidget(self._banner_text)

        # UX改善（新）: ベストケース情報ラベル（初期は非表示）
        self._banner_best_lbl = QLabel("")
        self._banner_best_lbl.setStyleSheet(
            "color: #c8e6c9; font-size: 11px; background: transparent; border: none;"
        )
        self._banner_best_lbl.setTextFormat(Qt.RichText)
        self._banner_best_lbl.hide()
        _banner_text_col.addWidget(self._banner_best_lbl)

        banner_layout.addLayout(_banner_text_col, stretch=1)

        self._btn_view_results = QPushButton("📊 結果を確認する  (STEP4) →")
        self._btn_view_results.setStyleSheet(
            "QPushButton {"
            "  background-color: #4caf50;"
            "  color: white;"
            "  font-size: 12px;"
            "  font-weight: bold;"
            "  padding: 6px 14px;"
            "  border-radius: 4px;"
            "  border: none;"
            "}"
            "QPushButton:hover {"
            "  background-color: #66bb6a;"
            "}"
        )
        self._btn_view_results.clicked.connect(self._on_view_results_clicked)
        banner_layout.addWidget(self._btn_view_results)

        # ---- UX改善②新: 自動遷移カウントダウンUI ----
        _countdown_col = QVBoxLayout()
        _countdown_col.setSpacing(2)
        _countdown_col.setContentsMargins(0, 0, 0, 0)

        self._countdown_label = QLabel(f"{_AUTO_TRANSITION_SEC}秒後に自動移動")
        self._countdown_label.setStyleSheet(
            "color: #a5d6a7; font-size: 10px; background: transparent; border: none;"
        )
        self._countdown_label.setAlignment(Qt.AlignCenter)
        _countdown_col.addWidget(self._countdown_label)

        self._countdown_bar = QProgressBar()
        self._countdown_bar.setRange(0, _AUTO_TRANSITION_SEC)
        self._countdown_bar.setValue(_AUTO_TRANSITION_SEC)
        self._countdown_bar.setMaximumHeight(4)
        self._countdown_bar.setTextVisible(False)
        self._countdown_bar.setStyleSheet(
            "QProgressBar { border: none; border-radius: 2px; background-color: rgba(0,0,0,40); }"
            "QProgressBar::chunk { background-color: #a5d6a7; border-radius: 2px; }"
        )
        _countdown_col.addWidget(self._countdown_bar)

        self._btn_cancel_auto = QPushButton("キャンセル")
        self._btn_cancel_auto.setFixedHeight(18)
        self._btn_cancel_auto.setStyleSheet(
            "QPushButton {"
            "  color: #a5d6a7; font-size: 10px;"
            "  background: transparent; border: 1px solid #a5d6a7;"
            "  border-radius: 3px; padding: 1px 6px;"
            "}"
            "QPushButton:hover { background-color: rgba(255,255,255,20); }"
        )
        self._btn_cancel_auto.setToolTip("自動遷移をキャンセルします")
        self._btn_cancel_auto.clicked.connect(self._cancel_countdown)
        _countdown_col.addWidget(self._btn_cancel_auto)

        self._countdown_widget = QWidget()
        self._countdown_widget.setLayout(_countdown_col)
        self._countdown_widget.hide()
        banner_layout.addWidget(self._countdown_widget)

        btn_close_banner = QPushButton("✕")
        btn_close_banner.setFlat(True)
        btn_close_banner.setFixedSize(20, 20)
        btn_close_banner.setStyleSheet(
            "QPushButton { color: #aaaaaa; font-size: 12px; background: transparent; border: none; }"
            "QPushButton:hover { color: white; }"
        )
        btn_close_banner.setToolTip("バナーを閉じる（自動遷移もキャンセル）")
        btn_close_banner.clicked.connect(self._close_banner)
        banner_layout.addWidget(btn_close_banner)

        self._completion_banner.hide()  # 初期状態は非表示
        layout.addWidget(self._completion_banner)

        # ---- UX改善（エラー診断パネル）: エラーケース診断ガイドパネル ----
        # エラー状態のケースが存在するとき、よくある原因と対策を折りたたみ形式で表示します。
        # ユーザーが「なぜ失敗したか」「何をすれば解決できるか」を一目で把握できます。
        self._error_panel = QFrame()
        self._error_panel.setFrameShape(QFrame.StyledPanel)
        self._error_panel.setStyleSheet(
            "QFrame {"
            "  background-color: #3e1c00;"
            "  border: 2px solid #d32f2f;"
            "  border-radius: 6px;"
            "}"
        )
        _ep_layout = QVBoxLayout(self._error_panel)
        _ep_layout.setContentsMargins(10, 8, 10, 8)
        _ep_layout.setSpacing(4)

        # ヘッダー行
        _ep_header = QHBoxLayout()
        _ep_header.setSpacing(6)

        _ep_icon = QLabel("⚠")
        _ep_icon.setStyleSheet("color: #f44336; font-size: 16px; font-weight: bold;")
        _ep_icon.setFixedWidth(20)
        _ep_header.addWidget(_ep_icon)

        self._error_title_lbl = QLabel(
            "<b style='color:#f44336;'>解析エラーが発生したケースがあります</b>"
        )
        self._error_title_lbl.setTextFormat(Qt.RichText)
        self._error_title_lbl.setStyleSheet("background: transparent;")
        _ep_header.addWidget(self._error_title_lbl, stretch=1)

        self._error_toggle_btn = QPushButton("▶ 原因と対策を見る")
        self._error_toggle_btn.setCheckable(True)
        self._error_toggle_btn.setChecked(False)
        self._error_toggle_btn.setStyleSheet(
            "QPushButton {"
            "  color: #ef9a9a; font-size: 11px; padding: 2px 8px;"
            "  background: transparent; border: 1px solid #ef9a9a; border-radius: 3px;"
            "}"
            "QPushButton:checked { background-color: rgba(239,154,154,0.15); }"
        )
        self._error_toggle_btn.clicked.connect(self._toggle_error_detail)
        _ep_header.addWidget(self._error_toggle_btn)
        _ep_layout.addLayout(_ep_header)

        # 折りたたみ診断エリア
        self._error_detail_widget = QWidget()
        self._error_detail_widget.setStyleSheet("background: transparent;")
        _ed_layout = QVBoxLayout(self._error_detail_widget)
        _ed_layout.setContentsMargins(4, 6, 4, 2)
        _ed_layout.setSpacing(5)

        _error_tips = [
            ("① SNAP.exe パスを確認",
             "「設定 → 全般設定」で Snap.exe の場所が正しく設定されているか確認してください。"),
            ("② s8i ファイルの場所を確認",
             "STEP1 で読み込んだ .s8i ファイルが移動・削除されていないか確認してください。"),
            ("③ 出力ディレクトリを確認",
             "ケース編集で出力ディレクトリが存在し書き込み権限があるか確認してください。"),
            ("④ パラメータ値を確認",
             "ケースを右クリック→「編集」で数値パラメータに不正な値がないか確認してください。"),
            ("⑤ ログで詳細を確認",
             "下部ログパネルの赤い行にエラーの詳細が表示されています。"),
        ]
        for _tip_ttl, _tip_txt in _error_tips:
            _tip_row = QHBoxLayout()
            _tip_row.setSpacing(8)
            _tip_row.setContentsMargins(0, 0, 0, 0)
            _ttl_lbl = QLabel(f"<b style='color:#ef9a9a;font-size:11px;'>{_tip_ttl}</b>")
            _ttl_lbl.setTextFormat(Qt.RichText)
            _ttl_lbl.setFixedWidth(150)
            _ttl_lbl.setStyleSheet("background: transparent;")
            _tip_row.addWidget(_ttl_lbl)
            _txt_lbl = QLabel(_tip_txt)
            _txt_lbl.setStyleSheet("color: #e0e0e0; font-size: 11px; background: transparent;")
            _txt_lbl.setWordWrap(True)
            _tip_row.addWidget(_txt_lbl, stretch=1)
            _ed_layout.addLayout(_tip_row)

        _ep_layout.addWidget(self._error_detail_widget)
        self._error_detail_widget.hide()

        self._error_panel.hide()  # エラーケースが存在するときだけ表示
        layout.addWidget(self._error_panel)

        # 初期チェック
        self._refresh_checklist()

    # ------------------------------------------------------------------
    # UX改善（エラー診断パネル）: エラーケース診断ガイドパネル制御
    # ------------------------------------------------------------------

    def _toggle_error_detail(self) -> None:
        """
        UX改善（エラー診断）: 診断詳細エリアの折りたたみ/展開を切り替えます。

        ▶/▼ テキストも合わせて更新します。
        """
        expanded = self._error_toggle_btn.isChecked()
        self._error_detail_widget.setVisible(expanded)
        self._error_toggle_btn.setText(
            "▼ 原因と対策を隠す" if expanded else "▶ 原因と対策を見る"
        )

    def _update_error_panel(self) -> None:
        """
        UX改善（エラー診断）: エラーケースの有無に応じて診断パネルを表示/非表示します。

        refresh() から呼び出されます。エラーケースが1件以上ある場合のみ表示します。
        """
        if not hasattr(self, '_error_panel'):
            return
        error_count = 0
        if self._project:
            error_count = sum(
                1 for c in self._project.cases
                if getattr(c, 'status', None) and c.status.name == "ERROR"
            )
        if error_count > 0:
            self._error_title_lbl.setText(
                f"<b style='color:#f44336;'>解析エラーが {error_count} 件発生しています</b>"
            )
            self._error_panel.show()
        else:
            self._error_panel.hide()

    def _make_check_row(self, text: str, parent_layout: QVBoxLayout) -> QLabel:
        """チェック行（アイコン + テキスト）を生成してレイアウトに追加します。"""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(f"⬜ {text}")
        lbl.setStyleSheet("font-size: 11px;")
        lbl.setWordWrap(True)
        row.addWidget(lbl, stretch=1)

        parent_layout.addLayout(row)
        return lbl

    # ------------------------------------------------------------------
    # UX改善③新: 解析完了バナー制御
    # ------------------------------------------------------------------

    def show_completion_banner(
        self, completed: int, errors: int, best_case_info: str = ""
    ) -> None:
        """
        解析完了バナーを表示します。

        Parameters
        ----------
        completed : int
            正常に完了したケースの件数。
        errors : int
            エラーになったケースの件数。
        best_case_info : str, optional
            UX改善（新）: ベストケース情報文字列。
            例: "🏆 最良ケース: Case3  （最小層間変形角: 0.00312 rad）"
            空文字の場合は非表示。
        """
        # UX改善（新）: ベストケース情報ラベルを更新
        if best_case_info:
            self._banner_best_lbl.setText(best_case_info)
            self._banner_best_lbl.show()
        else:
            self._banner_best_lbl.hide()

        if errors > 0:
            text = f"✅ {completed}件の解析が完了しました（⚠ {errors}件エラー）"
            self._completion_banner.setStyleSheet(
                "QFrame {"
                "  background-color: #4e3b00;"
                "  border: 2px solid #ff9800;"
                "  border-radius: 6px;"
                "  padding: 4px;"
                "}"
            )
            self._btn_view_results.setStyleSheet(
                "QPushButton {"
                "  background-color: #ff9800;"
                "  color: white;"
                "  font-size: 12px;"
                "  font-weight: bold;"
                "  padding: 6px 14px;"
                "  border-radius: 4px;"
                "  border: none;"
                "}"
                "QPushButton:hover {"
                "  background-color: #ffb74d;"
                "}"
            )
        else:
            text = f"✅ {completed}件の解析が完了しました！"
            self._completion_banner.setStyleSheet(
                "QFrame {"
                "  background-color: #1b5e20;"
                "  border: 2px solid #4caf50;"
                "  border-radius: 6px;"
                "  padding: 4px;"
                "}"
            )
            self._btn_view_results.setStyleSheet(
                "QPushButton {"
                "  background-color: #4caf50;"
                "  color: white;"
                "  font-size: 12px;"
                "  font-weight: bold;"
                "  padding: 6px 14px;"
                "  border-radius: 4px;"
                "  border: none;"
                "}"
                "QPushButton:hover {"
                "  background-color: #66bb6a;"
                "}"
            )
        self._banner_text.setText(text)
        self._completion_banner.show()
        # UX改善②新: 自動遷移カウントダウンを開始
        self._start_countdown()

    def hide_completion_banner(self) -> None:
        """解析完了バナーを非表示にします（次の解析開始前に呼びます）。"""
        self._cancel_countdown()
        self._completion_banner.hide()

    # ------------------------------------------------------------------
    # UX改善②新: 自動STEP4遷移カウントダウン
    # ------------------------------------------------------------------

    def _start_countdown(self) -> None:
        """
        UX改善②新: STEP4への自動遷移カウントダウンを開始します。

        解析完了バナー表示時に呼び出されます。
        _AUTO_TRANSITION_SEC 秒後に viewResultsRequested を発火して STEP4 へ移動します。
        「キャンセル」ボタンまたはバナーの「✕」で途中停止できます。
        """
        self._countdown_remaining = _AUTO_TRANSITION_SEC
        self._countdown_bar.setRange(0, _AUTO_TRANSITION_SEC)
        self._countdown_bar.setValue(_AUTO_TRANSITION_SEC)
        self._countdown_label.setText(f"{self._countdown_remaining}秒後に自動移動")
        self._countdown_widget.show()
        self._countdown_timer.start()

    def _cancel_countdown(self) -> None:
        """UX改善②新: 自動遷移カウントダウンをキャンセルします。"""
        self._countdown_timer.stop()
        self._countdown_widget.hide()

    def _on_countdown_tick(self) -> None:
        """UX改善②新: 1秒ごとに呼ばれるカウントダウンティック処理。"""
        self._countdown_remaining -= 1
        self._countdown_bar.setValue(self._countdown_remaining)
        if self._countdown_remaining <= 0:
            self._countdown_timer.stop()
            self._countdown_widget.hide()
            # STEP4 へ自動遷移
            self.viewResultsRequested.emit()
        else:
            self._countdown_label.setText(f"{self._countdown_remaining}秒後に自動移動")

    def _on_view_results_clicked(self) -> None:
        """「結果を確認する」ボタン押下時: カウントダウンを停止してすぐに遷移。"""
        self._cancel_countdown()
        self.viewResultsRequested.emit()

    def _close_banner(self) -> None:
        """バナー「✕」ボタン押下時: カウントダウンも停止してバナーを閉じる。"""
        self._cancel_countdown()
        self._completion_banner.hide()

    # ------------------------------------------------------------------
    # UX改善4: チェックリスト更新
    # ------------------------------------------------------------------

    def _refresh_checklist(self) -> None:
        """プロジェクト状態に基づいてチェックリストを更新します。"""
        # チェック1: s8iモデルファイル
        has_s8i = bool(self._project and getattr(self._project, "s8i_path", None))
        self._set_check_state(self._chk_s8i, has_s8i, "① s8iモデルファイルが選択されています")

        # チェック2: SNAP実行ファイル
        import os
        has_snap = bool(self._snap_exe_path and os.path.isfile(self._snap_exe_path))
        if not self._snap_exe_path:
            snap_msg = "② SNAP実行ファイル (Snap.exe) が未設定です"
        elif not os.path.isfile(self._snap_exe_path):
            snap_msg = "② SNAP実行ファイルが見つかりません（パスを確認してください）"
        else:
            snap_msg = "② SNAP実行ファイル (Snap.exe) が設定されています"
        self._set_check_state(self._chk_snap, has_snap, snap_msg)

        # チェック3: ケースが存在する
        has_cases = bool(self._project and self._project.cases)
        self._set_check_state(self._chk_cases, has_cases, "③ 解析ケースが1件以上追加されています")

        # 実行ボタンの有効/無効
        all_ok = has_s8i and has_snap and has_cases
        if not all_ok:
            self._btn_run.setEnabled(False)
            self._btn_run.setToolTip("上記チェックリストをすべて ✅ にしてから実行してください")
        else:
            self._btn_run.setToolTip("選択したケースを SNAP で解析実行します")
        # UX改善（新）: ボタンラベルにリアルタイム件数を反映
        self._update_run_button_label()

        # UX改善③（新）: 「準備完了」バナーを更新
        self._update_ready_banner(all_ok)

    def _set_check_state(self, label: QLabel, ok: bool, text: str) -> None:
        """チェックラベルの状態（✅/❌）とテキストを更新します。"""
        if ok:
            label.setText(f"✅ {text}")
            label.setStyleSheet("font-size: 11px; color: #2ca02c;")
        else:
            label.setText(f"❌ {text}")
            label.setStyleSheet("font-size: 11px; color: #d62728;")

    def _update_run_button_label(self, *_args) -> None:
        """
        UX改善（新）: チェックされているケース数に応じて実行ボタンのラベルを更新します。

        チェックリストの前提条件がOKの場合のみボタンを有効化します。
        0件選択時は「（ケースを選択してください）」、1件以上は「X件を解析する」と表示します。
        """
        checked_count = sum(
            1 for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        )

        # 前提チェック（s8i + SNAP exe + ケース存在）
        has_s8i = bool(self._project and getattr(self._project, "s8i_path", None))
        import os
        has_snap = bool(self._snap_exe_path and os.path.isfile(self._snap_exe_path))
        has_cases = bool(self._project and self._project.cases)
        prereqs_ok = has_s8i and has_snap and has_cases

        if not prereqs_ok:
            # 前提NG: ボタンはすでに無効化済みなのでラベルのみ更新
            self._btn_run.setText("🚀 選択したケースを解析実行")
            return

        if checked_count == 0:
            self._btn_run.setText("（ケースを選択してください）")
            self._btn_run.setEnabled(False)
        else:
            self._btn_run.setText(f"🚀  {checked_count} 件を解析する")
            self._btn_run.setEnabled(True)

        # UX改善（第12回③）: 実行前サマリーカードも更新
        self._update_pre_run_summary()

    def _update_pre_run_summary(self) -> None:
        """
        UX改善（第12回③）: 選択中のケースを種別分類してサマリーカードに表示します。

        分類:
        - 🔧 ダンパー変更あり: damper_params が設定されているケース
        - 📐 配置変更あり:  _rd_overrides が設定されているケース
        - 📊 ベースライン: パラメータ変更なし（基準モデルのまま）
        - ⚠ 要確認: ケース名がデフォルトのまま

        「要確認」が1件でもあれば警告ラベルを表示します。
        選択なし（0件）はカードを非表示にします。
        """
        if not hasattr(self, "_pre_run_summary_frame"):
            return

        # 選択ケースを収集
        selected_case_ids: List[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.checkState() == Qt.Checked:
                cid = item.data(Qt.UserRole)
                if cid:
                    selected_case_ids.append(cid)

        if not selected_case_ids:
            self._pre_run_summary_frame.hide()
            return

        # プロジェクトからケースオブジェクトを取得
        cases = []
        if self._project:
            for cid in selected_case_ids:
                c = self._project.get_case(cid)
                if c:
                    cases.append(c)

        if not cases:
            self._pre_run_summary_frame.hide()
            return

        # 分類カウント
        import re as _re_run
        _default_pat = _re_run.compile(
            r"^(新規ケース|Case-?\d+|case-?\d+)$", _re_run.IGNORECASE
        )
        count_damper = 0
        count_rd = 0
        count_baseline = 0
        count_warn_name = 0

        for case in cases:
            name = (case.name or "").strip()
            is_default = bool(_default_pat.match(name)) or name == ""
            if is_default:
                count_warn_name += 1

            has_damper = bool(
                case.damper_params and isinstance(case.damper_params, dict)
            )
            has_rd = False
            if case.parameters and isinstance(case.parameters, dict):
                has_rd = bool(case.parameters.get("_rd_overrides"))

            if has_damper:
                count_damper += 1
            elif has_rd:
                count_rd += 1
            else:
                count_baseline += 1

        # サマリーテキストを構築
        parts: List[str] = []
        if count_damper:
            parts.append(f"🔧 パラメータ変更: {count_damper}件")
        if count_rd:
            parts.append(f"📐 配置変更: {count_rd}件")
        if count_baseline:
            parts.append(f"📊 ベースライン: {count_baseline}件")
        summary = "  /  ".join(parts) if parts else f"{len(cases)}件"
        self._pre_run_summary_lbl.setText(summary)

        # 要確認警告
        if count_warn_name > 0:
            self._pre_run_warn_lbl.setText(
                f"⚠ デフォルト名のケース {count_warn_name}件（名前を変更することをお勧めします）"
            )
            self._pre_run_warn_lbl.show()
        else:
            self._pre_run_warn_lbl.hide()

        self._pre_run_summary_frame.show()

    def _update_ready_banner(self, all_ok: bool) -> None:
        """
        UX改善③（新）: 前提条件が全てOKのとき「解析の準備ができました」バナーを表示します。

        バナーにはケースの実行状況サマリー（未実行・完了・エラー件数）も表示し、
        「どのケースをこれから走らせるか」が一目でわかるようにします。

        Parameters
        ----------
        all_ok : bool
            s8i・SNAP・ケース存在の3条件が全てOKなら True。
        """
        if not hasattr(self, "_ready_banner"):
            return

        if not all_ok:
            self._ready_banner.hide()
            return

        # ケースの状態集計
        cases = list(self._project.cases) if self._project else []
        pending = sum(1 for c in cases if getattr(c.status, "name", "") == "PENDING")
        completed = sum(1 for c in cases if getattr(c.status, "name", "") == "COMPLETED")
        error = sum(1 for c in cases if getattr(c.status, "name", "") == "ERROR")
        running = sum(1 for c in cases if getattr(c.status, "name", "") == "RUNNING")
        total = len(cases)

        parts = []
        if pending > 0:
            parts.append(f"<span style='color:#e65100;'>⏳ 未実行 {pending}件</span>")
        if completed > 0:
            parts.append(f"<span style='color:#2e7d32;'>✅ 完了 {completed}件</span>")
        if error > 0:
            parts.append(f"<span style='color:#c62828;'>❌ エラー {error}件</span>")
        if running > 0:
            parts.append(f"<span style='color:#1565c0;'>▶ 実行中 {running}件</span>")

        status_text = "　/　".join(parts) if parts else f"合計 {total}件"
        if hasattr(self, "_ready_status_lbl"):
            self._ready_status_lbl.setText(status_text)

        self._ready_banner.show()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def _select_pending(self) -> None:
        if not self._project:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            case_id = item.data(Qt.UserRole)
            case = self._project.get_case(case_id)
            if case and case.status.name in ("PENDING", "ERROR"):
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)

    def _select_errors_only(self) -> None:
        """
        UX改善④ 第5回: エラーが発生したケースのみを選択します。

        PENDING / COMPLETED ケースはすべてチェックを外し、
        ERROR ケースのみチェックします。
        原因修正後のエラーケース再実行ワークフローを高速化します。
        """
        if not self._project:
            return
        for i in range(self._list.count()):
            item = self._list.item(i)
            case_id = item.data(Qt.UserRole)
            case = self._project.get_case(case_id)
            if case and case.status.name == "ERROR":
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)

    def _update_case_status_label(self, *_args) -> None:
        """
        UX改善④ 第5回: ケースリストの下部集計ラベルを更新します。

        「待機: X / 完了: Y / エラー: Z / 選択中: W件」をリアルタイムに表示します。
        ケースが存在しない場合は空白を表示します。
        """
        if not hasattr(self, "_case_status_label"):
            return
        if not self._project or not self._project.cases:
            self._case_status_label.setText("　")
            return
        cases = list(self._project.cases)
        pending = sum(1 for c in cases if getattr(c.status, "name", "") == "PENDING")
        completed = sum(1 for c in cases if getattr(c.status, "name", "") == "COMPLETED")
        error = sum(1 for c in cases if getattr(c.status, "name", "") == "ERROR")
        checked = sum(
            1 for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        )
        parts = []
        if pending:
            parts.append(f"<span style='color:#e65100;'>⏳ 待機 {pending}</span>")
        if completed:
            parts.append(f"<span style='color:#2e7d32;'>✅ 完了 {completed}</span>")
        if error:
            parts.append(f"<span style='color:#c62828;'>❌ エラー {error}</span>")
        summary = "　/　".join(parts) if parts else "ケースなし"
        selected_text = (
            f"　<b style='color:#1565c0;'>（{checked}件 選択中）</b>"
            if checked > 0 else ""
        )
        self._case_status_label.setText(summary + selected_text)

    def _update_est_time_banner(self, *_args) -> None:
        """
        UX改善（第9回②）: 選択ケース数に基づいて推定所要時間バナーを更新します。

        チェックされたケース数 × 約60秒/件 で推定時間を計算し、
        「🕐 推定所要時間: 約X分（X件 × 約60秒/件）」形式で表示します。
        """
        if not hasattr(self, "_est_time_lbl"):
            return
        checked = sum(
            1 for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.Checked
        )
        if checked == 0:
            self._est_time_lbl.setText("ケースを選択すると推定所要時間が表示されます")
            self._est_time_banner.setStyleSheet(
                "QFrame {"
                "  background-color: #e8eaf6;"
                "  border: 1px solid #9fa8da;"
                "  border-radius: 4px;"
                "}"
            )
        else:
            secs_per_case = 60
            total_secs = checked * secs_per_case
            if total_secs < 60:
                time_str = f"約{total_secs}秒"
            else:
                mins = total_secs // 60
                time_str = f"約{mins}分"
            self._est_time_lbl.setText(
                f"推定所要時間: {time_str}（{checked}件 × 約{secs_per_case}秒/件）"
            )
            # 件数に応じて色を変化させる（5件超で橙、15件超で赤）
            if checked > 15:
                bg, border, fg = "#ffebee", "#ef9a9a", "#b71c1c"
            elif checked > 5:
                bg, border, fg = "#fff3e0", "#ffcc80", "#e65100"
            else:
                bg, border, fg = "#e8f5e9", "#a5d6a7", "#1b5e20"
            self._est_time_banner.setStyleSheet(
                f"QFrame {{"
                f"  background-color: {bg};"
                f"  border: 1px solid {border};"
                f"  border-radius: 4px;"
                f"}}"
            )
            self._est_time_lbl.setStyleSheet(
                f"color: {fg}; font-size: 10px; background: transparent; border: none;"
            )

    def _on_run_clicked(self) -> None:
        selected_ids = []
        for i in range(self._list.count()):
            if self._list.item(i).checkState() == Qt.Checked:
                selected_ids.append(self._list.item(i).data(Qt.UserRole))
        if not selected_ids:
            QMessageBox.information(self, "情報", "実行するケースが選択されていません。")
            return
        self.runSelectedRequested.emit(selected_ids)
