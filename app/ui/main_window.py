"""
app/ui/main_window.py
メインウィンドウ。

レイアウト:
  ┌──────────────────────────────────────────────┐
  │ MenuBar                                      │
  │ ToolBar                                      │
  ├──────────────────────────────────────────────┤
  │  CaseTable (左) │  ResultChartWidget (右)     │
  │                 │                            │
  ├──────────────────────────────────────────────┤
  │ LogWidget (下部)                              │
  ├──────────────────────────────────────────────┤
  │ StatusBar                                    │
  └──────────────────────────────────────────────┘
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout, QHBoxLayout,
    QWidget,
)

from app.models import AnalysisCaseStatus, Project
from app.models.case_template import TemplateManager
from app.services import AnalysisService
from app.services.autosave import AutoSaveService
from .case_table import CaseTableWidget
from .compare_chart_widget import CompareChartWidget
from .envelope_chart_widget import EnvelopeChartWidget
from .dashboard_widget import DashboardWidget
from .file_preview_widget import FilePreviewWidget
from .log_widget import LogWidget
from .radar_chart_widget import RadarChartWidget
from .ranking_widget import RankingWidget
from .result_chart_widget import ResultChartWidget
from .result_table_widget import ResultTableWidget
from .binary_result_widget import BinaryResultWidget
from .mode_shape_widget import ModeShapeWidget
from .hysteresis_widget import HysteresisWidget
from .transfer_function_widget import TransferFunctionWidget
from .model_info_widget import ModelInfoWidget
from .settings_dialog import load_settings
from .theme import ThemeManager
from .run_selection_widget import RunSelectionWidget
from .batch_queue_widget import BatchQueueWidget
from .welcome_widget import WelcomeWidget, add_recent_project
from .sidebar_widget import SidebarWidget
from .shortcut_help_dialog import ShortcutHelpDialog  # 改善⑨
from .step_nav_footer import StepNavFooter  # UX改善①新: ステップナビゲーションフッター
from .step4_summary_bar import Step4SummaryBar  # UX改善（新）: STEP4結果サマリーバー
from .step_hint_banner import StepHintBanner  # UX改善（新）: 初回ステップヒントバナー
from .error_guide_widget import ErrorGuideWidget  # UX改善③: 解析エラーガイダンスパネル

import logging
import qtawesome as qta

logger = logging.getLogger(__name__)

APP_NAME = "snap-controller"
ORG_NAME = "BAUES"
MAX_RECENT_MENU = 8


from .main_window_dialogs import _MainWindowDialogsMixin


class MainWindow(_MainWindowDialogsMixin, QMainWindow):
    """
    アプリケーションのメインウィンドウ。
    """

    def __init__(self) -> None:
        super().__init__()
        self._project: Optional[Project] = None
        self._service = AnalysisService()
        self._autosave = AutoSaveService(self)
        self._template_manager = TemplateManager()

        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._restore_settings()
        self._setup_tray_icon()  # 改善C: システムトレイ通知

        # 自動保存シグナル接続
        self._autosave.auto_saved.connect(
            lambda p: self.statusBar().showMessage(f"自動保存しました", 3000)
        )
        self._autosave.error_occurred.connect(
            lambda msg: self._log.append_line(f"[自動保存エラー] {msg}")
        )

        # 起動時はウェルカム画面を表示（空プロジェクトを裏で作成）
        self._project = Project()
        settings = load_settings()
        if settings.get("snap_exe_path"):
            self._project.snap_exe_path = settings["snap_exe_path"]
        if settings.get("snap_work_dir"):
            self._project.snap_work_dir = settings["snap_work_dir"]
        self._service.set_snap_exe_path(self._project.snap_exe_path)
        self._service.set_snap_work_dir(self._project.snap_work_dir)
        self._autosave.set_project(self._project)
        # 自動保存設定の復元
        autosave_enabled = settings.get("autosave_enabled", True)
        autosave_interval = settings.get("autosave_interval", 5)
        self._autosave.set_interval(autosave_interval)
        self._autosave.set_enabled(autosave_enabled)
        self._case_table.set_project(self._project)
        self._run_selection.set_project(self._project)
        self._run_selection.set_snap_exe_path(self._project.snap_exe_path or "")  # UX改善4
        self._main_stack.setCurrentIndex(0)  # ウェルカム画面

        # UX改善（スマートデフォルト）: SNAP実行ファイル未設定の場合に警告バナーを表示
        # 設定済みであれば警告を非表示にする。プロジェクトを開いた後も更新する。
        _snap_configured = bool(settings.get("snap_exe_path", ""))
        self._welcome.show_snap_warning(not _snap_configured)

        # 自動保存からの復旧チェック
        self._check_autosave_recovery()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 750)

        self._create_widgets()
        self._setup_welcome()
        step1 = self._build_step1()
        step2 = self._build_step2()
        step3, step3_split = self._build_step3()
        step4 = self._build_step4()
        self._assemble_layout(step1, step2, step3, step4, step3_split)
        self._setup_statusbar()
        self._connect_signals()

    # ---- _setup_ui サブメソッド群 ----

    def _create_widgets(self) -> None:
        """子ウィジェットを一括生成します。"""
        self._model_info = ModelInfoWidget()
        self._case_table = CaseTableWidget()
        self._chart = ResultChartWidget()
        self._compare_chart = CompareChartWidget()
        self._envelope_chart = EnvelopeChartWidget()
        self._radar_chart = RadarChartWidget()
        self._result_table = ResultTableWidget()
        self._binary_result = BinaryResultWidget()
        self._mode_shape_widget = ModeShapeWidget()
        self._hysteresis_widget = HysteresisWidget()
        self._transfer_function_widget = TransferFunctionWidget()
        self._ranking = RankingWidget()
        self._dashboard = DashboardWidget()
        self._file_preview = FilePreviewWidget()
        self._run_selection = RunSelectionWidget()
        self._batch_queue = BatchQueueWidget()
        self._log = LogWidget()

    def _setup_welcome(self) -> None:
        """ウェルカム画面を構築します。"""
        self._welcome = WelcomeWidget()
        self._welcome.newProjectRequested.connect(self._new_project_dialog)
        self._welcome.openProjectRequested.connect(self._open_project)
        self._welcome.recentProjectSelected.connect(self._open_recent_project)
        self._welcome.snapSettingsRequested.connect(self._open_settings)

    def _build_step1(self) -> QWidget:
        """STEP1: モデル設定を構築して返します。"""
        step1 = QWidget()
        step1_layout = QVBoxLayout(step1)
        step1_layout.setContentsMargins(0, 0, 0, 0)
        step1_layout.setSpacing(0)
        self._hint_banner_step1 = StepHintBanner(step_index=0)
        step1_layout.addWidget(self._hint_banner_step1)
        step1_layout.addWidget(self._model_info)
        step1_layout.addWidget(self._file_preview, stretch=1)
        self._step1_footer = StepNavFooter(
            show_back=False,
            next_label="ケースを設計する  (STEP2) →",
            next_primary=True,
        )
        self._step1_footer.set_next_enabled(False)
        self._step1_footer.set_next_hint(".s8i ファイルを読み込むと STEP2 へ進めます")
        self._step1_footer.setToolTip("STEP1でs8iファイルを読み込むとSTEP2へ進めます")
        self._step1_footer.nextRequested.connect(lambda: self._sidebar.set_current_step(1))
        step1_layout.addWidget(self._step1_footer)
        return step1

    def _build_step2(self) -> QWidget:
        """STEP2: ケース設計を構築して返します。"""
        step2 = QWidget()
        step2_layout = QVBoxLayout(step2)
        step2_layout.setContentsMargins(0, 0, 0, 0)
        step2_layout.setSpacing(0)
        self._hint_banner_step2 = StepHintBanner(step_index=1)
        step2_layout.addWidget(self._hint_banner_step2)
        step2_layout.addWidget(self._case_table)
        self._step2_footer = StepNavFooter(
            back_label="← モデル設定  (STEP1)",
            next_label="解析を実行する  (STEP3) →",
            next_primary=True,
        )
        self._step2_footer.set_next_enabled(False)
        self._step2_footer.set_next_hint("解析ケースを1件以上追加すると STEP3 へ進めます")
        self._step2_footer.setToolTip("STEP2で解析ケースを1件以上追加するとSTEP3へ進めます")
        self._step2_footer.backRequested.connect(lambda: self._sidebar.set_current_step(0))
        self._step2_footer.nextRequested.connect(lambda: self._sidebar.set_current_step(2))
        step2_layout.addWidget(self._step2_footer)
        return step2

    def _build_step3(self) -> tuple:
        """STEP3: 解析実行を構築して (step3_widget, step3_split) を返します。"""
        step3_widget = QWidget()
        step3_layout = QVBoxLayout(step3_widget)
        step3_layout.setContentsMargins(0, 0, 0, 0)
        step3_layout.setSpacing(0)
        self._hint_banner_step3 = StepHintBanner(step_index=2)
        step3_layout.addWidget(self._hint_banner_step3)

        # ケース設定確認トーストバナー
        self._case_readiness_toast = QFrame()
        self._case_readiness_toast.setFrameShape(QFrame.NoFrame)
        self._case_readiness_toast.setStyleSheet(
            "QFrame {"
            "  background-color: #fff8e1;"
            "  border: 1px solid #ffca28;"
            "  border-left: 4px solid #f57c00;"
            "  border-radius: 4px;"
            "  margin: 4px 4px 0px 4px;"
            "}"
        )
        _toast_row = QHBoxLayout(self._case_readiness_toast)
        _toast_row.setContentsMargins(10, 6, 10, 6)
        _toast_row.setSpacing(8)

        _toast_icon = QLabel("⚠")
        _toast_icon.setStyleSheet(
            "font-size: 14px; background: transparent; border: none;"
        )
        _toast_icon.setFixedWidth(20)
        _toast_row.addWidget(_toast_icon)

        self._case_readiness_toast_lbl = QLabel("")
        self._case_readiness_toast_lbl.setStyleSheet(
            "color: #e65100; font-size: 11px; background: transparent; border: none;"
        )
        self._case_readiness_toast_lbl.setWordWrap(True)
        self._case_readiness_toast_lbl.setTextFormat(Qt.RichText)
        _toast_row.addWidget(self._case_readiness_toast_lbl, stretch=1)

        _toast_back_btn = QPushButton("← STEP2 に戻って確認")
        _toast_back_btn.setFixedHeight(24)
        _toast_back_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 10px; padding: 2px 8px;"
            "  border: 1px solid #ffca28; border-radius: 3px;"
            "  background: #fff3e0; color: #e65100;"
            "}"
            "QPushButton:hover { background: #ffe0b2; }"
        )
        _toast_back_btn.clicked.connect(lambda: self._sidebar.set_current_step(1))
        _toast_row.addWidget(_toast_back_btn)

        _toast_close_btn = QPushButton("✕")
        _toast_close_btn.setFixedSize(20, 20)
        _toast_close_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 11px; background: transparent; border: none; color: #888;"
            "}"
            "QPushButton:hover { color: #333; }"
        )
        _toast_close_btn.clicked.connect(self._case_readiness_toast.hide)
        _toast_row.addWidget(_toast_close_btn)

        self._case_readiness_toast.hide()
        self._case_readiness_toast_timer = None
        step3_layout.addWidget(self._case_readiness_toast)

        step3_layout.addWidget(self._run_selection)

        # 解析エラーガイダンスパネル
        self._error_guide = ErrorGuideWidget()
        self._error_guide.openSettingsRequested.connect(self._open_settings)
        self._error_guide.editCaseRequested.connect(self._edit_case_by_id)
        step3_layout.addWidget(self._error_guide)

        step3_split = QSplitter(Qt.Vertical)
        step3_split.addWidget(self._batch_queue)
        step3_split.addWidget(self._log)
        step3_split.setStretchFactor(0, 2)
        step3_split.setStretchFactor(1, 1)

        step3_layout.addWidget(step3_split, stretch=1)
        self._step3_footer = StepNavFooter(
            back_label="← ケース設計  (STEP2)",
            next_label="結果を確認する  (STEP4) →",
            next_primary=True,
        )
        self._step3_footer.set_next_enabled(False)
        self._step3_footer.set_next_hint("解析を実行して結果が出ると STEP4 へ進めます")
        self._step3_footer.setToolTip("解析を実行して結果が出るとSTEP4へ進めます")
        self._step3_footer.backRequested.connect(lambda: self._sidebar.set_current_step(1))
        self._step3_footer.nextRequested.connect(lambda: self._sidebar.set_current_step(3))
        step3_layout.addWidget(self._step3_footer)
        return step3_widget, step3_split

    def _build_step4(self) -> QWidget:
        """STEP4: 結果・戦略を構築して返します。"""
        self._build_step4_result_tabs()

        step4 = QWidget()
        step4_layout = QVBoxLayout(step4)
        step4_layout.setContentsMargins(0, 0, 0, 0)
        step4_layout.setSpacing(0)

        self._step4_summary_bar = Step4SummaryBar()
        self._step4_summary_bar.bestCaseClicked.connect(self._on_summary_best_case_clicked)
        step4_layout.addWidget(self._step4_summary_bar)

        self._hint_banner_step4 = StepHintBanner(step_index=3)
        self._hint_banner_step4.tabShortcutRequested.connect(
            lambda idx: self._right_tabs.setCurrentIndex(idx)
        )
        step4_layout.addWidget(self._hint_banner_step4)

        self._step4_content_stack = QStackedWidget()
        self._step4_content_stack.addWidget(self._build_step4_empty_state())
        self._step4_content_stack.addWidget(self._right_tabs)
        step4_layout.addWidget(self._step4_content_stack, stretch=1)

        step4_layout.addWidget(self._build_step4_notes_panel())

        self._step4_footer = StepNavFooter(
            back_label="← 解析実行  (STEP3)",
            next_label="次のケースを設計する  (STEP2) →",
            next_primary=True,
        )
        self._step4_footer.backRequested.connect(lambda: self._sidebar.set_current_step(2))
        self._step4_footer.nextRequested.connect(self._go_plan_next_case)
        step4_layout.addWidget(self._step4_footer)
        return step4

    def _build_step4_result_tabs(self) -> None:
        """結果タブウィジェットを構築します。"""
        from PySide6.QtWidgets import QTabWidget as _QTabWidget

        self._binary_result.prepend_tab(self._compare_chart, "📊 応答値比較")
        self._binary_result.prepend_tab(self._hysteresis_widget, "🔄 履歴ループ")
        self._binary_result.prepend_tab(self._mode_shape_widget, "🏗 モード形状")
        self._binary_result.prepend_tab(self._transfer_function_widget, "〜 伝達関数")

        self._right_tabs = _QTabWidget()
        _tab_defs = [
            (self._dashboard,      "fa5s.chart-pie",       "ダッシュボード",   True,  1),
            (self._chart,          "fa5s.chart-line",      "解析結果",         True,  1),
            (self._binary_result,  "fa5s.exchange-alt",    "ケース比較",       True,  2),
            (self._envelope_chart,    "fa5s.ruler-combined",  "エンベロープ",     True,  1),
            (self._radar_chart,       "fa5s.spider",          "レーダーチャート", True,  2),
            (self._result_table,      "fa5s.table",           "結果テーブル",     True,  1),
            (self._ranking,           "fa5s.trophy",          "ランキング",       True,  1),
        ]
        self._tab_result_requirements: dict = {}
        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#333333"
        for idx, (widget, icon_name, label, req, min_r) in enumerate(_tab_defs):
            self._right_tabs.addTab(widget, qta.icon(icon_name, color=icon_color), label)
            self._tab_result_requirements[idx] = (req, min_r)

        self._update_result_tabs(result_count=0)

        _tab_guide_btn = QPushButton("📖 読み方")
        _tab_guide_btn.setFixedHeight(22)
        _tab_guide_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 10px; padding: 2px 10px;"
            "  border: 1px solid #90caf9; border-radius: 3px;"
            "  background: #e3f2fd; color: #1565c0;"
            "  margin: 2px 4px;"
            "}"
            "QPushButton:hover { background: #1976d2; color: white; }"
        )
        _tab_guide_btn.setToolTip(
            "このタブで確認できる内容と、結果の読み方を表示します。\n"
            "タブを切り替えると説明も切り替わります。"
        )
        _tab_guide_btn.clicked.connect(self._show_current_tab_guide)
        self._right_tabs.setCornerWidget(_tab_guide_btn, Qt.TopRightCorner)

    def _build_step4_empty_state(self) -> QWidget:
        """結果なし時の空状態ウィジェットを構築します。"""
        _s4_empty = QWidget()
        _s4_empty_layout = QVBoxLayout(_s4_empty)
        _s4_empty_layout.setAlignment(Qt.AlignCenter)

        _s4_empty_icon = QLabel()
        _s4_empty_icon.setPixmap(
            qta.icon("fa5s.chart-bar", color=("#555" if ThemeManager.is_dark() else "#bbb")).pixmap(72, 72)
        )
        _s4_empty_icon.setAlignment(Qt.AlignCenter)
        _s4_empty_layout.addWidget(_s4_empty_icon)

        _s4_empty_title = QLabel("解析結果がまだありません")
        _s4_empty_title_font = _s4_empty_title.font()
        _s4_empty_title_font.setPointSize(14)
        _s4_empty_title_font.setBold(True)
        _s4_empty_title.setFont(_s4_empty_title_font)
        _s4_empty_title.setAlignment(Qt.AlignCenter)
        _s4_empty_layout.addWidget(_s4_empty_title)

        _s4_empty_desc = QLabel(
            "STEP3 で解析を実行すると、ここに結果グラフ・比較チャート・ランキングなどが表示されます。\n"
            "少なくとも1件の解析ケースを実行してください。"
        )
        _s4_empty_desc.setAlignment(Qt.AlignCenter)
        _s4_empty_desc.setStyleSheet("color: gray; padding: 8px;")
        _s4_empty_desc.setWordWrap(True)
        _s4_empty_layout.addWidget(_s4_empty_desc)

        _s4_goto_btn = QPushButton("🚀  STEP3: 解析を実行する →")
        _s4_goto_btn.setMinimumHeight(42)
        _s4_goto_btn.setMaximumWidth(300)
        _s4_goto_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #1976d2; color: white;"
            "  font-size: 13px; font-weight: bold;"
            "  padding: 8px 20px; border-radius: 5px; border: none;"
            "}"
            "QPushButton:hover { background-color: #1565c0; }"
            "QPushButton:pressed { background-color: #0d47a1; }"
        )
        _s4_goto_btn.setToolTip("STEP3（解析実行）に移動してケースを実行します")
        _s4_goto_btn.clicked.connect(lambda: self._sidebar.set_current_step(2))
        _s4_goto_btn_row = QHBoxLayout()
        _s4_goto_btn_row.setAlignment(Qt.AlignCenter)
        _s4_goto_btn_row.addWidget(_s4_goto_btn)
        _s4_empty_layout.addLayout(_s4_goto_btn_row)

        return _s4_empty

    def _build_step4_notes_panel(self) -> QFrame:
        """解析戦略メモパネルを構築します。"""
        from PySide6.QtWidgets import QTextEdit as _QTextEdit

        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#333333"
        _notes_frame = QFrame()
        _notes_frame.setFrameShape(QFrame.StyledPanel)
        _notes_frame.setMaximumHeight(110)
        _notes_frame_layout = QVBoxLayout(_notes_frame)
        _notes_frame_layout.setContentsMargins(8, 4, 8, 4)
        _notes_frame_layout.setSpacing(2)
        _notes_header_row = QHBoxLayout()
        _notes_icon_lbl = QLabel()
        _notes_icon_lbl.setPixmap(
            qta.icon("fa5s.sticky-note", color=icon_color).pixmap(14, 14)
        )
        _notes_header_row.addWidget(_notes_icon_lbl)
        _notes_title_lbl = QLabel(
            "<b>解析戦略メモ</b>"
            "<span style='color:gray; font-size:10px;'>"
            "　次ラウンドに向けた気づきや方針をメモしておきましょう"
            "</span>"
        )
        _notes_title_lbl.setTextFormat(Qt.RichText)
        _notes_header_row.addWidget(_notes_title_lbl)
        _notes_header_row.addStretch()
        _notes_frame_layout.addLayout(_notes_header_row)
        self._strategy_notes_edit = _QTextEdit()
        self._strategy_notes_edit.setPlaceholderText(
            "例: Case3の制振効果が最も大きかった。次はVEダンパー基数を4→6に増やして"
            "加速度応答の低減を狙う。層間変形角はすでにOKなので変位側は余裕あり…"
        )
        self._strategy_notes_edit.setMaximumHeight(70)
        self._strategy_notes_edit.setToolTip(
            "解析結果を見た上での戦略・気づきをメモします。\n"
            "プロジェクトファイルに保存されるため、次回起動時も参照できます。"
        )
        self._strategy_notes_edit.textChanged.connect(self._on_strategy_notes_changed)
        _notes_frame_layout.addWidget(self._strategy_notes_edit)
        return _notes_frame

    def _assemble_layout(self, step1: QWidget, step2: QWidget,
                         step3: QWidget, step4: QWidget,
                         step3_split: QSplitter) -> None:
        """4ステップのウィジェットを全体レイアウトに組み立てます。"""
        self._workflow_stack = QStackedWidget()
        self._workflow_stack.addWidget(step1)
        self._workflow_stack.addWidget(step2)
        self._workflow_stack.addWidget(step3)
        self._workflow_stack.addWidget(step4)

        self._sidebar = SidebarWidget()
        self._sidebar.stepChanged.connect(self._workflow_stack.setCurrentIndex)
        self._sidebar.stepChanged.connect(self._on_sidebar_step_changed)

        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._sidebar)
        workspace_layout.addWidget(self._workflow_stack, stretch=1)

        self._main_stack = QStackedWidget()
        self._main_stack.addWidget(self._welcome)      # index 0
        self._main_stack.addWidget(workspace)           # index 1
        self.setCentralWidget(self._main_stack)
        self._v_splitter = step3_split

    def _setup_statusbar(self) -> None:
        """ステータスバーを構築します。"""
        self.statusBar().showMessage("準備完了")

        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(200)
        self._progress_bar.setMaximumHeight(16)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m")
        self._progress_bar.hide()
        self.statusBar().addPermanentWidget(self._progress_bar)

        self._case_info_label = QLabel()
        self._case_info_label.setStyleSheet("color: gray; margin-right: 8px; font-size: 11px;")
        self.statusBar().addPermanentWidget(self._case_info_label)

    def _connect_signals(self) -> None:
        """ウィジェット間のシグナル接続を行います。"""
        self._model_info.fileRequested.connect(self._load_s8i_file)
        self._model_info.fileDropped.connect(self._load_s8i_from_path)
        self._case_table.caseSelectionChanged.connect(self._on_case_selected)
        self._case_table.runRequested.connect(self._on_run_requested)
        self._case_table.projectModified.connect(self._update_title)
        self._case_table.projectModified.connect(self._run_selection.refresh)
        self._case_table.projectModified.connect(self._update_sidebar_badges)
        self._case_table.projectModified.connect(self._on_project_modified_groups)
        self._ranking.caseSelected.connect(self._on_case_selected)
        self._ranking.useAsStartingPointRequested.connect(self._on_use_ranking_case_as_base)
        self._dashboard.caseSelected.connect(self._on_case_selected)
        self._run_selection.runSelectedRequested.connect(self._run_selected_cases)
        self._run_selection.viewResultsRequested.connect(
            lambda: self._sidebar.set_current_step(3)
        )
        self._service.log_emitted.connect(self._log.append_line)
        self._service.case_finished.connect(self._on_analysis_finished)
        self._service.status_changed.connect(self.statusBar().showMessage)
        self._service.progress_updated.connect(self._on_progress_updated)
        self._service.batch_state_changed.connect(self._on_batch_state_changed)

        # バッチキューウィジェットのシグナル接続
        self._batch_queue.pauseRequested.connect(self._service.pause_batch)
        self._batch_queue.resumeRequested.connect(self._service.resume_batch)
        self._batch_queue.cancelRequested.connect(self._service.cancel_batch)
        self._service.case_finished.connect(self._on_batch_queue_case_finished)
        self._service.batch_state_changed.connect(self._on_batch_queue_state_changed)

    def _setup_menu(self) -> None:
        mb = self.menuBar()
        self._build_file_menu(mb)
        self._build_analysis_menu(mb)
        self._build_settings_menu(mb)
        self._build_help_menu(mb)

    def _build_file_menu(self, mb) -> None:
        file_menu = mb.addMenu("ファイル(&F)")

        act_new = QAction("新規プロジェクト(&N)", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self._new_project_dialog)
        file_menu.addAction(act_new)

        act_open = QAction("プロジェクトを開く(&O)…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open_project)
        file_menu.addAction(act_open)

        file_menu.addSeparator()

        act_save = QAction("保存(&S)", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self._save_project)
        file_menu.addAction(act_save)

        act_save_as = QAction("名前を付けて保存(&A)…", self)
        act_save_as.setShortcut(QKeySequence.SaveAs)
        act_save_as.triggered.connect(self._save_project_as)
        file_menu.addAction(act_save_as)

        file_menu.addSeparator()

        from .welcome_widget import get_recent_projects
        self._recent_menu = file_menu.addMenu("最近使ったプロジェクト(&R)")
        self._update_recent_menu()

        file_menu.addSeparator()

        act_export = QAction("結果をエクスポート(&E)…", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self._export_results)
        file_menu.addAction(act_export)

        act_report = QAction("HTMLレポート生成(&H)…", self)
        act_report.setShortcut("Ctrl+Shift+R")
        act_report.triggered.connect(self._generate_report)
        file_menu.addAction(act_report)

        file_menu.addSeparator()

        act_quit = QAction("終了(&Q)", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

    def _build_analysis_menu(self, mb) -> None:
        analysis_menu = mb.addMenu("解析(&A)")

        act_add = QAction("ケースを追加(&A)", self)
        act_add.triggered.connect(self._add_case)
        analysis_menu.addAction(act_add)

        act_sweep = QAction("パラメータスイープ(&W)…", self)
        act_sweep.setShortcut("Ctrl+W")
        act_sweep.triggered.connect(self._open_sweep_dialog)
        analysis_menu.addAction(act_sweep)

        act_criteria = QAction("目標性能基準(&C)…", self)
        act_criteria.setShortcut("Ctrl+T")
        act_criteria.triggered.connect(self._open_criteria_dialog)
        analysis_menu.addAction(act_criteria)

        act_catalog = QAction("ダンパーカタログ(&K)…", self)
        act_catalog.setShortcut("Ctrl+K")
        act_catalog.triggered.connect(self._open_damper_catalog)
        analysis_menu.addAction(act_catalog)

        act_optimize = QAction("ダンパー最適化(&O)…", self)
        act_optimize.setShortcut("Ctrl+Shift+O")
        act_optimize.triggered.connect(self._open_optimizer_dialog)
        analysis_menu.addAction(act_optimize)

        act_irdt = QAction("iRDT 設計ウィザード(&I)…", self)
        act_irdt.setShortcut("Ctrl+Shift+I")
        act_irdt.triggered.connect(self._open_irdt_wizard)
        analysis_menu.addAction(act_irdt)

        act_minimizer = QAction("ダンパー本数最小化(&M)…", self)
        act_minimizer.setShortcut("Ctrl+Shift+M")
        act_minimizer.triggered.connect(self._open_minimizer_dialog)
        analysis_menu.addAction(act_minimizer)

        act_injector = QAction("iRDT/iOD ダンパー挿入(&J)…", self)
        act_injector.setShortcut("Ctrl+Shift+J")
        act_injector.triggered.connect(self._open_damper_injector)
        analysis_menu.addAction(act_injector)

        act_compare = QAction("ケース詳細比較(&D)…", self)
        act_compare.setShortcut("Ctrl+D")
        act_compare.triggered.connect(self._open_case_compare)
        analysis_menu.addAction(act_compare)

        act_groups = QAction("グループ管理(&G)…", self)
        act_groups.triggered.connect(self._open_group_manager)
        analysis_menu.addAction(act_groups)

        analysis_menu.addSeparator()

        act_template = QAction("テンプレートから適用(&T)…", self)
        act_template.setShortcut("Ctrl+Shift+T")
        act_template.triggered.connect(self._open_template_dialog)
        analysis_menu.addAction(act_template)

        act_save_template = QAction("テンプレートとして保存…", self)
        act_save_template.triggered.connect(self._save_as_template)
        analysis_menu.addAction(act_save_template)

        analysis_menu.addSeparator()

        act_validate = QAction("入力チェック(&V)", self)
        act_validate.setShortcut("Ctrl+Shift+V")
        act_validate.triggered.connect(self._validate_selected)
        analysis_menu.addAction(act_validate)

        act_validate_all = QAction("全ケース入力チェック", self)
        act_validate_all.triggered.connect(self._validate_all)
        analysis_menu.addAction(act_validate_all)

        analysis_menu.addSeparator()

        act_run_selected = QAction("選択ケースを実行(&R)", self)
        act_run_selected.setShortcut("F5")
        act_run_selected.triggered.connect(self._run_selected)
        analysis_menu.addAction(act_run_selected)

        act_run_all = QAction("全ケースを実行(&L)", self)
        act_run_all.triggered.connect(self._run_all)
        analysis_menu.addAction(act_run_all)

    def _build_settings_menu(self, mb) -> None:
        settings_menu = mb.addMenu("設定(&S)")

        act_app_settings = QAction("アプリケーション設定(&P)…", self)
        act_app_settings.triggered.connect(self._open_settings)
        settings_menu.addAction(act_app_settings)

    def _build_help_menu(self, mb) -> None:
        help_menu = mb.addMenu("ヘルプ(&H)")

        act_shortcuts = QAction("キーボードショートカット一覧(&K)…", self)
        act_shortcuts.setShortcut("Ctrl+?")
        act_shortcuts.setToolTip("アプリ内のキーボードショートカットを一覧表示します  [Ctrl+?]")
        act_shortcuts.triggered.connect(self._show_shortcut_help)
        help_menu.addAction(act_shortcuts)

        help_menu.addSeparator()

        act_about = QAction("このアプリについて(&A)", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _setup_toolbar(self) -> None:
        tb = self.addToolBar("メイン")
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        icon_color = "#d4d4d4" if ThemeManager.is_dark() else "#333333"
        self._build_toolbar_actions(tb, icon_color)
        self._build_toolbar_progress(tb, icon_color)
        self._build_toolbar_shortcuts()

    def _build_toolbar_actions(self, tb, icon_color: str) -> None:
        """ツールバーのアクションボタンを構築します。"""
        act_add = QAction(qta.icon("fa5s.plus", color=icon_color), "ケース追加", self)
        act_add.setToolTip("新しい解析ケースをダイアログで作成します")
        act_add.triggered.connect(self._add_case)
        tb.addAction(act_add)

        tb.addSeparator()

        act_run = QAction(qta.icon("fa5s.play", color="#4CAF50"), "実行", self)
        act_run.setToolTip("選択ケースを解析実行します  [F5]")
        act_run.triggered.connect(self._run_selected)
        tb.addAction(act_run)

        tb.addSeparator()

        act_criteria = QAction(qta.icon("fa5s.bullseye", color=icon_color), "基準設定", self)
        act_criteria.setToolTip("目標性能基準を設定します  [Ctrl+T]")
        act_criteria.triggered.connect(self._open_criteria_dialog)
        tb.addAction(act_criteria)

        tb.addSeparator()

        act_save = QAction(qta.icon("fa5s.save", color=icon_color), "保存", self)
        act_save.setToolTip("プロジェクトを上書き保存します  [Ctrl+S]")
        act_save.triggered.connect(self._save_project)
        tb.addAction(act_save)

        tb.addSeparator()

        self._act_pause = QAction(qta.icon("fa5s.pause", color="#FF9800"), "一時停止", self)
        self._act_pause.setToolTip("バッチ実行を一時停止します")
        self._act_pause.triggered.connect(self._toggle_pause)
        self._act_pause.setEnabled(False)
        tb.addAction(self._act_pause)

        self._act_cancel = QAction(qta.icon("fa5s.stop", color="#F44336"), "キャンセル", self)
        self._act_cancel.setToolTip("バッチ実行をキャンセルします")
        self._act_cancel.triggered.connect(self._cancel_batch)
        self._act_cancel.setEnabled(False)
        tb.addAction(self._act_cancel)

        tb.addSeparator()

        act_help = QAction("❓ ヘルプ", self)
        act_help.setToolTip("キーボードショートカット一覧を表示します  [Ctrl+?]")
        act_help.triggered.connect(self._show_shortcut_help)
        tb.addAction(act_help)

        tb.addSeparator()

    def _build_toolbar_progress(self, tb, icon_color: str) -> None:
        """ツールバーのグローバル進捗インジケーターを構築します。"""
        self._global_progress_widget = QWidget()
        _gp_layout = QHBoxLayout(self._global_progress_widget)
        _gp_layout.setContentsMargins(4, 0, 8, 0)
        _gp_layout.setSpacing(4)

        self._global_progress_icon = QLabel()
        self._global_progress_icon.setPixmap(
            qta.icon("fa5s.tasks", color=icon_color).pixmap(14, 14)
        )
        _gp_layout.addWidget(self._global_progress_icon)

        self._global_progress_label = QLabel("解析: —")
        self._global_progress_label.setStyleSheet(
            "font-size: 11px; color: gray; min-width: 90px;"
        )
        self._global_progress_label.setToolTip(
            "全解析ケースの進捗状況（完了件数 / 合計件数）。\n"
            "現在表示しているステップに関わらず常時更新されます。\n"
            "クリックで STEP4（結果・戦略）へ移動できます。"
        )
        self._global_progress_label.setCursor(Qt.PointingHandCursor)
        self._global_progress_label.mousePressEvent = lambda _: self._navigate_to_step(3)
        _gp_layout.addWidget(self._global_progress_label)

        self._global_mini_bar = QProgressBar()
        self._global_mini_bar.setRange(0, 100)
        self._global_mini_bar.setValue(0)
        self._global_mini_bar.setMaximumWidth(80)
        self._global_mini_bar.setMaximumHeight(8)
        self._global_mini_bar.setTextVisible(False)
        self._global_mini_bar.setStyleSheet(
            "QProgressBar { border: 1px solid palette(mid); border-radius: 3px;"
            "  background-color: palette(base); }"
            "QProgressBar::chunk { background-color: #4CAF50; border-radius: 2px; }"
        )
        self._global_mini_bar.setToolTip("解析完了率")
        self._global_mini_bar.hide()
        _gp_layout.addWidget(self._global_mini_bar)

        tb.addWidget(self._global_progress_widget)

    def _build_toolbar_shortcuts(self) -> None:
        """Ctrl+1〜4 / Alt+1〜7 のキーボードショートカットを設定します。"""
        from PySide6.QtGui import QShortcut as _QShortcut
        _step_shortcuts = []
        for _step_idx in range(4):
            _sc = _QShortcut(QKeySequence(f"Ctrl+{_step_idx + 1}"), self)
            _sc.activated.connect(
                lambda _n=_step_idx: self._navigate_to_step(_n)
            )
            _step_shortcuts.append(_sc)
        self._step_shortcuts = _step_shortcuts

        _result_tab_shortcuts = []
        for _tab_idx in range(7):
            _sc = _QShortcut(QKeySequence(f"Alt+{_tab_idx + 1}"), self)
            _sc.activated.connect(
                lambda _n=_tab_idx: self._switch_result_tab(_n)
            )
            _result_tab_shortcuts.append(_sc)
        self._result_tab_shortcuts = _result_tab_shortcuts

    # ------------------------------------------------------------------
    # 改善C: システムトレイアイコン・通知
    # ------------------------------------------------------------------

    def _setup_tray_icon(self) -> None:
        """
        システムトレイアイコンを初期化します。

        解析実行中にウィンドウを最小化・他の作業をしていても、
        バッチ解析の完了・エラーをOSの通知バルーンで即座に確認できます。
        QSystemTrayIcon が利用できない環境（サポートなし）では無効化されます。
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray: Optional[QSystemTrayIcon] = None
            return

        try:
            # アプリアイコンを流用（qtawesome で生成）
            icon = qta.icon("fa5s.building", color="#1976d2")
            self._tray = QSystemTrayIcon(icon, self)
            self._tray.setToolTip("snap-controller — 解析支援ツール")
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
        except Exception:
            logger.debug("システムトレイアイコンの初期化に失敗", exc_info=True)
            self._tray = None

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """トレイアイコンのダブルクリックでウィンドウを前面に表示します。"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _tray_notify(self, title: str, message: str, icon_type=None) -> None:
        """
        システムトレイバルーン通知を表示します。

        Parameters
        ----------
        title : str
            通知タイトル
        message : str
            通知本文
        icon_type : QSystemTrayIcon.MessageIcon | None
            アイコン種別。None の場合は Information。
        """
        if self._tray is None or not self._tray.isVisible():
            return
        if icon_type is None:
            icon_type = QSystemTrayIcon.Information
        self._tray.showMessage(title, message, icon_type, 5000)

    # ------------------------------------------------------------------
    # Project operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Result Tabs state management
    # ------------------------------------------------------------------

    def _update_result_tabs(self, result_count: int) -> None:
        """
        解析結果の件数に応じて右パネルのタブを有効/無効にします。

        Parameters
        ----------
        result_count : int
            結果を持つケースの件数。
        """
        bar = self._right_tabs.tabBar()
        for idx, (req, min_r) in self._tab_result_requirements.items():
            if not req:
                # 常時有効なタブ
                self._right_tabs.setTabEnabled(idx, True)
                bar.setTabToolTip(idx, "")
            elif result_count >= min_r:
                # 条件を満たす場合は有効化
                self._right_tabs.setTabEnabled(idx, True)
                bar.setTabToolTip(idx, "")
            else:
                # 条件を満たさない場合は無効化
                self._right_tabs.setTabEnabled(idx, False)
                if min_r == 1:
                    tip = "解析を実行すると利用できます"
                else:
                    tip = f"解析済みケースが{min_r}件以上で利用できます"
                bar.setTabToolTip(idx, tip)

        # 現在のタブが無効化された場合、有効なタブに自動切替
        current = self._right_tabs.currentIndex()
        if not self._right_tabs.isTabEnabled(current):
            # 最初の有効なタブを探す
            for fallback in range(self._right_tabs.count()):
                if self._right_tabs.isTabEnabled(fallback):
                    self._right_tabs.setCurrentIndex(fallback)
                    break

        # UX改善①新: 結果件数に応じて空状態/タブを切り替え
        if hasattr(self, "_step4_content_stack"):
            self._step4_content_stack.setCurrentIndex(0 if result_count == 0 else 1)

        # UX改善（新）: STEP4 結果サマリーバーを更新
        if hasattr(self, "_step4_summary_bar") and self._project:
            self._step4_summary_bar.update_cases(self._project.cases)

    # ------------------------------------------------------------------
    # Setup Guide
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # UX改善④: STEP4 タブガイド
    # ------------------------------------------------------------------

    # 各結果タブの「読み方」説明テキスト（タブインデックス順）
    _TAB_GUIDE_TEXTS = [
        # 0: ダッシュボード
        (
            "📊 ダッシュボード",
            "全解析ケースの主要指標を一覧で確認できます。\n\n"
            "【見方】\n"
            "・各ケースのカードに最大層間変形角・最大加速度・最大変位が表示されます\n"
            "・性能基準を設定している場合、✅/❌ で合否をひと目で確認できます\n"
            "・カードをクリックすると「解析結果」タブで詳細グラフを確認できます\n\n"
            "【活用ヒント】\n"
            "3ケース以上あるときに最も有用です。最良ケースを素早く特定できます。"
        ),
        # 1: 解析結果
        (
            "📈 解析結果",
            "選択した1つのケースの解析結果グラフを確認できます。\n\n"
            "【見方】\n"
            "・層ごとの最大応答値（変位・速度・加速度・層間変形角）が棒グラフで表示されます\n"
            "・赤い破線は性能基準ライン（設定している場合）です\n"
            "・グラフ上部のドロップダウンで表示する地震波ケースを切り替えられます\n\n"
            "【活用ヒント】\n"
            "ケース一覧でケースを選択してからこのタブに切り替えると素早く確認できます。"
        ),
        # 2: ケース比較
        (
            "↔ ケース比較",
            "複数ケースの解析結果を並べて比較できます。\n\n"
            "【見方】\n"
            "・各ケースの最大応答値を棒グラフで並べて比較します\n"
            "・凡例の色はケース名に対応しています\n"
            "・グラフ上部で比較する指標（変位/速度/加速度/層間変形角）を選択できます\n\n"
            "【活用ヒント】\n"
            "「ベースライン（ダンパーなし）」を含めて比較すると制振効果が分かりやすくなります。"
        ),
        # 3: エンベロープ
        (
            "📐 エンベロープ",
            "複数地震波ケースの応答包絡線（エンベロープ）を確認できます。\n\n"
            "【見方】\n"
            "・各地震波ケースの最大応答を重ね合わせ、その包絡線を表示します\n"
            "・実線が各地震波の応答、太線が全波の最大値（包絡線）です\n\n"
            "【活用ヒント】\n"
            "複数地震波解析をしている場合に最も有用です。設計に用いる最大値を確認できます。"
        ),
        # 4: レーダーチャート
        (
            "🕸 レーダーチャート",
            "複数ケースのバランスを多軸で視覚的に比較できます。\n\n"
            "【見方】\n"
            "・各軸が異なる応答指標（変位・速度・加速度・層間変形角など）を表します\n"
            "・レーダーが小さいほど全指標が優れています\n"
            "・形が崩れているケースは特定の指標で性能が低い傾向があります\n\n"
            "【活用ヒント】\n"
            "「万能型」か「変位特化型」かなど、ダンパー設計のトレードオフを把握するのに最適です。"
        ),
        # 5: 結果テーブル
        (
            "📋 結果テーブル",
            "全ケースの数値結果を一覧テーブルで確認できます。\n\n"
            "【見方】\n"
            "・行がケース、列が指標（変位・速度・加速度・層間変形角・せん断力係数など）です\n"
            "・列ヘッダーをクリックするとその指標で並び替えできます\n"
            "・数値の上にマウスを乗せると詳細情報が表示されます\n\n"
            "【活用ヒント】\n"
            "Excel にコピーして設計検討書に使えます。右クリックメニューからコピーできます。"
        ),
        # 6: ランキング
        (
            "🏆 ランキング",
            "性能基準に基づいてケースをランキング表示します。\n\n"
            "【見方】\n"
            "・上位のケースが目標性能を最も満たしているケースです\n"
            "・ランキングスコアは設定した性能基準（層間変形角・加速度など）に基づきます\n"
            "・「基点として再設計」ボタンで上位ケースを元にしたケース追加ができます\n\n"
            "【活用ヒント】\n"
            "目標性能基準（Ctrl+T）を設定してから使うと、自動的に合否が判定されます。"
        ),
    ]

    def _show_current_tab_guide(self) -> None:
        """
        UX改善④: 現在アクティブな結果タブの「読み方ガイド」を表示します。

        タブインデックスに対応したガイドテキストを QMessageBox でポップアップ表示します。
        建築設計者が「このグラフで何を見ればよいか」を即座に把握できます。
        """
        if not hasattr(self, "_right_tabs"):
            return
        idx = self._right_tabs.currentIndex()
        if 0 <= idx < len(self._TAB_GUIDE_TEXTS):
            title, text = self._TAB_GUIDE_TEXTS[idx]
        else:
            return

        dlg = QMessageBox(self)
        dlg.setWindowTitle(f"{title} — 読み方ガイド")
        dlg.setText(f"<b>{title}</b>")
        dlg.setInformativeText(text)
        dlg.setStandardButtons(QMessageBox.Ok)
        dlg.setDefaultButton(QMessageBox.Ok)
        dlg.exec()

    def _on_summary_best_case_clicked(self, case_id: str) -> None:
        """
        UX改善（新）: STEP4 サマリーバーの「最良ケース」クリック時処理。

        該当ケースを解析結果タブで選択・表示します。
        """
        if not case_id or self._project is None:
            return
        # STEP4 の解析結果タブ (index=1) に切り替え
        if hasattr(self, '_right_tabs') and self._right_tabs.isTabEnabled(1):
            self._right_tabs.setCurrentIndex(1)
        # ケースを解析結果ウィジェットに表示
        if hasattr(self, '_chart'):
            case = self._project.get_case(case_id)
            if case:
                self._chart.set_case(case)
        self.statusBar().showMessage(
            f"🏆 最良ケース「{self._project.get_case(case_id).name if self._project.get_case(case_id) else ''}」の結果を表示しています",
            4000,
        )

    def _on_sidebar_step_changed(self, step: int) -> None:
        """サイドバーのステップが切り替わったときの処理。"""
        # STEP3（index=2）に切り替えるたびにチェックリストを最新化
        if step == 2:
            self._run_selection.refresh()
            # UX改善（第12回④）: ケース設定確認トーストバナーを更新
            self._show_case_readiness_toast()
        # UX改善③新: STEP4（index=3）へ切り替わったとき、最適なタブを自動選択
        elif step == 3:
            self._auto_select_result_tab()

        # UX改善（新）: 初回ステップ訪問時ヒントバナーを表示
        _hint_map = {
            0: getattr(self, '_hint_banner_step1', None),
            1: getattr(self, '_hint_banner_step2', None),
            2: getattr(self, '_hint_banner_step3', None),
            3: getattr(self, '_hint_banner_step4', None),
        }
        banner = _hint_map.get(step)
        if banner is not None:
            banner.show_if_first_visit()

    def _show_case_readiness_toast(self) -> None:
        """
        UX改善（第12回④）: STEP3 に遷移するたびにケース設定の問題を検出して
        オレンジ色のトーストバナーを表示します。

        検出内容:
        - デフォルト名（Case-NN / 新規ケース）のままのケース数
        - ダンパー設定が全くないケースが全件の場合（ベースラインのみ）
        問題なければバナーは非表示のままにします。
        バナーが表示された場合は 8 秒後に自動的に消えます。
        """
        if not hasattr(self, "_case_readiness_toast"):
            return
        if self._project is None:
            self._case_readiness_toast.hide()
            return

        import re as _re_mw
        _def_pat = _re_mw.compile(
            r"^(新規ケース|Case-?\d+|case-?\d+)$", _re_mw.IGNORECASE
        )
        cases = self._project.cases
        if not cases:
            self._case_readiness_toast.hide()
            return

        # 問題のあるケースを集計
        default_name_cases = [
            c for c in cases
            if _def_pat.match((c.name or "").strip()) or not (c.name or "").strip()
        ]
        no_damper_cases = [
            c for c in cases
            if not (c.damper_params and isinstance(c.damper_params, dict))
            and not (
                c.parameters and isinstance(c.parameters, dict)
                and c.parameters.get("_rd_overrides")
            )
        ]

        issues: list = []
        if default_name_cases:
            n = len(default_name_cases)
            names_preview = "、".join(
                f"「{c.name}」" for c in default_name_cases[:2]
            )
            if n > 2:
                names_preview += f" 他{n-2}件"
            issues.append(
                f"ケース名がデフォルトのままです: {names_preview}"
                "（後から内容がわかる名前に変更することをお勧めします）"
            )

        # 全件がベースラインの場合は警告（比較目的での制振検討の場合は意図的な場合もあるため弱い警告）
        if len(no_damper_cases) == len(cases) and len(cases) > 1:
            issues.append(
                "すべてのケースにダンパー設定変更がありません。"
                "比較検討をする場合はダンパーパラメータや配置を変更したケースを追加してください。"
            )

        if not issues:
            self._case_readiness_toast.hide()
            return

        # バナーにメッセージを設定して表示
        msg = "<br>".join(f"・{i}" for i in issues)
        self._case_readiness_toast_lbl.setText(
            f"<b>STEP3 に進む前に確認してください</b><br>{msg}"
        )
        self._case_readiness_toast.show()

        # 既存タイマーをキャンセル
        if self._case_readiness_toast_timer is not None:
            try:
                self._case_readiness_toast_timer.stop()
            except Exception:
                pass

        # 8秒後に自動非表示
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._case_readiness_toast.hide)
        timer.start(8000)
        self._case_readiness_toast_timer = timer

    def _auto_select_result_tab(self) -> None:
        """
        UX改善③新: STEP4 に切り替わったとき、完了ケース数に応じて
        最も適切な結果タブを自動的に選択します。

        選択ロジック:
          - 完了ケース 0件  → 何もしない（空状態ページが表示される）
          - 完了ケース 1件  → 「解析結果」タブ（index 1）
          - 完了ケース 2件  → 「ケース比較」タブ（index 2）
          - 完了ケース 3件以上 → 「ダッシュボード」タブ（index 0）

        ユーザーが手動でタブを変更していた場合でも、STEP4 に入るたびに
        状況にあった最良のタブから確認できます。
        """
        if self._project is None:
            return
        completed = [c for c in self._project.cases if c.result_summary]
        n = len(completed)
        if n == 0:
            return  # 空状態のまま

        # タブインデックス定義 (main_window _tab_defs の順番に対応)
        # 0: ダッシュボード, 1: 解析結果, 2: ケース比較, ...
        _TAB_DASHBOARD = 0
        _TAB_RESULT    = 1
        _TAB_COMPARE   = 2

        if n == 1:
            target_tab = _TAB_RESULT
        elif n == 2:
            target_tab = _TAB_COMPARE
        else:
            target_tab = _TAB_DASHBOARD

        # タブが有効な場合のみ切り替える
        if (0 <= target_tab < self._right_tabs.count()
                and self._right_tabs.isTabEnabled(target_tab)):
            self._right_tabs.setCurrentIndex(target_tab)
            tab_name = self._right_tabs.tabText(target_tab)
            self.statusBar().showMessage(
                f"✓ 解析結果 {n}件 — 「{tab_name}」タブを自動選択しました",
                3000,
            )

    def _update_setup_guide(self) -> None:
        """未使用 (Sidebar UIへ移行)"""
        pass

    def _update_sidebar_badges(self) -> None:
        """
        UX改善1: サイドバーの各ステップバッジをプロジェクト状態に合わせて更新します。

        STEP1: モデルファイル名（ロード済みの場合）
        STEP2: 総ケース数バッジ
        STEP3: 完了/合計件数バッジ
        STEP4: 完了件数バッジ

        UX改善（新）: 各ステップの完了状態インジケーター（✓/▶/○）も同時に更新します。
        """
        if self._project is None:
            for i in range(4):
                self._sidebar.update_badge(i, "")
                self._sidebar.set_step_state(i, "pending")
            return

        # STEP1: モデルファイル状態
        if self._project.s8i_path:
            model_name = Path(self._project.s8i_path).name
            self._sidebar.update_badge(0, f"✓ {model_name}")
            self._sidebar.set_step_state(0, "done")
        else:
            self._sidebar.update_badge(0, "モデル未選択")
            self._sidebar.set_step_state(0, "pending")

        # STEP2: ケース件数
        total_cases = len(self._project.cases)
        if total_cases > 0:
            self._sidebar.update_badge(1, f"{total_cases} ケース")
            self._sidebar.set_step_state(1, "done")
        else:
            self._sidebar.update_badge(1, "ケースなし")
            # モデルがロード済みならSTEP2はアクティブ（着手可能）、未ロードなら未着手
            if self._project.s8i_path:
                self._sidebar.set_step_state(1, "active")
            else:
                self._sidebar.set_step_state(1, "pending")

        # STEP3: 完了/合計件数
        from app.models import AnalysisCaseStatus
        completed = sum(1 for c in self._project.cases if c.result_summary)
        error = sum(
            1 for c in self._project.cases
            if hasattr(c, "status") and c.status and
            getattr(c.status, "name", "") == "ERROR"
        )
        if total_cases == 0:
            self._sidebar.update_badge(2, "")
            self._sidebar.set_step_state(2, "pending")
        elif completed == total_cases:
            self._sidebar.update_badge(2, f"{completed}/{total_cases}完了")
            self._sidebar.set_step_state(2, "done")
        elif error > 0:
            self._sidebar.update_badge(2, f"{completed}/{total_cases}完了 ⚠{error}エラー")
            self._sidebar.set_step_state(2, "active")
        elif completed > 0:
            self._sidebar.update_badge(2, f"{completed}/{total_cases}完了")
            self._sidebar.set_step_state(2, "active")
        else:
            self._sidebar.update_badge(2, f"0/{total_cases}完了")
            # ケースがあるなら実行可能（アクティブ）
            self._sidebar.set_step_state(2, "active")

        # STEP4: 完了件数
        if completed > 0:
            self._sidebar.update_badge(3, f"結果 {completed}件")
            self._sidebar.set_step_state(3, "done")
        else:
            self._sidebar.update_badge(3, "")
            self._sidebar.set_step_state(3, "pending")

        # UX改善④新: グローバル進捗インジケーターを更新
        self._update_global_progress()

        # UX改善②新: StepNavFooter の「次へ」ボタンをプロジェクト状態に合わせて制御
        # STEP1→STEP2: s8iファイルが読み込まれていると「次へ」が有効
        model_loaded = bool(self._project.s8i_path)
        if hasattr(self, "_step1_footer"):
            self._step1_footer.set_next_enabled(model_loaded)
            self._step1_footer.setToolTip(
                "" if model_loaded
                else "STEP1でs8iファイルを読み込むとSTEP2へ進めます"
            )
        # STEP2→STEP3: ケースが1件以上あると「次へ」が有効
        has_cases = total_cases > 0
        if hasattr(self, "_step2_footer"):
            self._step2_footer.set_next_enabled(has_cases)
            self._step2_footer.setToolTip(
                "" if has_cases
                else "STEP2で解析ケースを1件以上追加するとSTEP3へ進めます"
            )
        # STEP3→STEP4: 解析が完了しているケースがあると「次へ」が有効
        if hasattr(self, "_step3_footer"):
            self._step3_footer.set_next_enabled(completed > 0)
            self._step3_footer.setToolTip(
                "" if completed > 0
                else "解析を実行して結果が出るとSTEP4へ進めます"
            )

        # UX改善（新）: サイドバー下部のプロジェクト状態サマリーを更新
        if hasattr(self, "_sidebar") and hasattr(self._sidebar, "update_project_summary"):
            s8i_name = ""
            if self._project and self._project.s8i_path:
                s8i_name = Path(self._project.s8i_path).name
            self._sidebar.update_project_summary(
                s8i_name=s8i_name,
                case_count=total_cases,
                done_count=completed,
            )

    def _update_global_progress(self) -> None:
        """
        UX改善④新: ツールバーのグローバル解析進捗インジケーターを更新します。

        全解析ケースの完了数・合計数をツールバーのラベルとミニプログレスバーに反映します。
        現在のステップに関わらず常時更新され、解析の進行状況を一目で把握できます。
        """
        if not hasattr(self, "_global_progress_label"):
            return
        if self._project is None:
            self._global_progress_label.setText("解析: —")
            self._global_mini_bar.hide()
            return

        total = len(self._project.cases)
        if total == 0:
            self._global_progress_label.setText("解析: —")
            self._global_mini_bar.hide()
            return

        completed = sum(1 for c in self._project.cases if c.result_summary)
        error = sum(
            1 for c in self._project.cases
            if hasattr(c, "status") and c.status and
            getattr(c.status, "name", "") == "ERROR"
        )
        running = sum(
            1 for c in self._project.cases
            if hasattr(c, "status") and c.status and
            getattr(c.status, "name", "") == "RUNNING"
        )

        pct = int(completed / total * 100) if total > 0 else 0

        if running > 0:
            label_text = f"解析中 {completed}/{total}"
            self._global_progress_label.setStyleSheet(
                "font-size: 11px; color: #1976d2; min-width: 90px;"
            )
        elif completed == total and total > 0:
            label_text = f"✅ {completed}/{total}完了"
            self._global_progress_label.setStyleSheet(
                "font-size: 11px; color: #2e7d32; min-width: 90px;"
            )
        elif error > 0:
            label_text = f"⚠ {completed}/{total} ({error}エラー)"
            self._global_progress_label.setStyleSheet(
                "font-size: 11px; color: #e65100; min-width: 90px;"
            )
        else:
            label_text = f"解析 {completed}/{total}完了"
            self._global_progress_label.setStyleSheet(
                "font-size: 11px; color: gray; min-width: 90px;"
            )

        self._global_progress_label.setText(label_text)

        # ミニプログレスバーを更新
        self._global_mini_bar.setValue(pct)
        if running > 0:
            self._global_mini_bar.setStyleSheet(
                "QProgressBar { border: 1px solid palette(mid); border-radius: 3px;"
                "  background-color: palette(base); }"
                "QProgressBar::chunk { background-color: #1976d2; border-radius: 2px; }"
            )
        elif completed == total and total > 0:
            self._global_mini_bar.setStyleSheet(
                "QProgressBar { border: 1px solid palette(mid); border-radius: 3px;"
                "  background-color: palette(base); }"
                "QProgressBar::chunk { background-color: #4caf50; border-radius: 2px; }"
            )
        else:
            self._global_mini_bar.setStyleSheet(
                "QProgressBar { border: 1px solid palette(mid); border-radius: 3px;"
                "  background-color: palette(base); }"
                "QProgressBar::chunk { background-color: #4CAF50; border-radius: 2px; }"
            )
        self._global_mini_bar.show()

    def _edit_case_by_id(self, case_id: str) -> None:
        """
        UX改善③: 指定 case_id のケース編集ダイアログを開きます。
        エラーガイダンスパネルの「ケースを編集」ボタンから呼び出されます。
        """
        if self._project is None:
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        # STEP2（ケース設計）へ移動してからダイアログを開く
        self._sidebar.set_current_step(1)
        self._case_table.open_edit_dialog_for(case_id)

    def _go_plan_next_case(self) -> None:
        """
        UX改善⑤新: STEP4 フッターの「次のケースを設計する」ボタン処理。

        STEP2（ケース設計）へ移動し、新規ケース追加ダイアログを開くかを
        確認メッセージ付きで案内します。完了した結果を踏まえて次のケースを
        素早く設計するためのフィードバックループを支援します。
        """
        self._sidebar.set_current_step(1)  # STEP2へ
        self.statusBar().showMessage(
            "STEP2: 前の結果を参考に新しいケースを追加しましょう  [追加ボタン or Ctrl+A]",
            6000,
        )

    def _on_project_modified_groups(self) -> None:
        """
        UX改善⑤新: ケース変更（グループ追加・削除など）時に
        比較グラフのグループ別選択ドロップダウンを更新します。
        """
        if self._project is not None:
            self._compare_chart.set_case_groups(self._project.case_groups)

    def _navigate_to_step(self, step: int) -> None:
        """
        UX改善④新: Ctrl+1/2/3/4 でワークフローステップを直接切り替えます。

        ワークスペース画面が表示されている場合のみ動作します。
        ウェルカム画面表示中は無視します。

        Parameters
        ----------
        step : int
            切り替え先のステップインデックス（0〜3）。
        """
        # ウェルカム画面では動作しない
        if self._main_stack.currentIndex() != 1:
            return
        step_names = ["STEP1: モデル設定", "STEP2: ケース設計", "STEP3: 解析実行", "STEP4: 結果・戦略"]
        self._sidebar.set_current_step(step)
        if 0 <= step < len(step_names):
            self.statusBar().showMessage(
                f"✓ {step_names[step]}に移動しました  [Ctrl+{step + 1}]",
                3000,
            )

    def _switch_result_tab(self, index: int) -> None:
        """
        UX改善②新: Alt+1〜7 で STEP4 の結果タブを直接切り替えます。

        STEP4（結果・戦略）に移動してから、指定インデックスのタブを選択します。
        ウェルカム画面やタブ範囲外のインデックスでは動作しません。

        ショートカット対応:
            Alt+1 → ダッシュボード
            Alt+2 → 解析結果
            Alt+3 → ケース比較
            Alt+4 → エンベロープ
            Alt+5 → レーダーチャート
            Alt+6 → 結果テーブル
            Alt+7 → ランキング
        """
        if self._main_stack.currentIndex() != 1:
            return
        if index < 0 or index >= self._right_tabs.count():
            return
        # STEP4 に移動してからタブを切り替え
        self._sidebar.set_current_step(3)
        self._right_tabs.setCurrentIndex(index)
        tab_name = self._right_tabs.tabText(index)
        self.statusBar().showMessage(
            f"✓ 結果タブ「{tab_name}」を表示  [Alt+{index + 1}]",
            2500,
        )

    def _on_use_ranking_case_as_base(self, case_id: str) -> None:
        """
        UX改善①新: ランキングで選択したケースを複製して STEP2（ケース設計）へ移動します。

        「最良ケースを出発点として次ラウンドの改善を始める」というワークフローを
        1クリックで実現します。複製されたケースはケース名に「_Next」サフィックスが
        付加されるため、元ケースと容易に区別できます。
        """
        if self._project is None:
            return
        original = self._project.get_case(case_id)
        if original is None:
            return
        orig_name = original.name

        # ケースを複製
        clone = self._project.duplicate_case(case_id)
        if clone is None:
            return

        # 複製ケースに「_Next」サフィックスを付加（すでに付いている場合は番号を増やす）
        import re as _re
        base_name = _re.sub(r"_Next\d*$", "", orig_name)
        existing_names = {c.name for c in self._project.cases}
        next_name = f"{base_name}_Next"
        if next_name in existing_names:
            counter = 2
            while f"{base_name}_Next{counter}" in existing_names:
                counter += 1
            next_name = f"{base_name}_Next{counter}"
        clone.name = next_name

        self._project._touch()
        self._case_table.refresh()
        self._run_selection.refresh()
        self._update_title()
        self._update_sidebar_badges()

        # STEP2（ケース設計）に切り替え
        self._sidebar.set_current_step(1)

        self.statusBar().showMessage(
            f"ケース「{orig_name}」から「{next_name}」を複製しました。"
            "パラメータを変更して再解析してください。",
            7000,
        )

    def _on_strategy_notes_changed(self) -> None:
        """
        UX改善④新: 解析戦略メモの変更をプロジェクトに反映します。

        STEP4 のメモパネルへの入力をリアルタイムでプロジェクトオブジェクトに
        書き込みます。プロジェクト保存時に .snapproj ファイルに永続化されます。
        """
        if self._project is not None:
            self._project.strategy_notes = self._strategy_notes_edit.toPlainText()
            self._project._touch()

    def _on_setup_guide_step_clicked(self, step: int) -> None:
        """ガイドバーのステップがクリックされたときに対応するアクションを呼び出します。"""
        if step == 1:
            self._open_settings()
            self._update_setup_guide()
        elif step == 2:
            self._load_s8i_file()
            self._update_setup_guide()
        elif step == 3:
            self._add_case()
            self._update_setup_guide()
        elif step == 4:
            self._run_selected()

    def _check_autosave_recovery(self) -> None:
        """起動時に自動保存ファイルの存在をチェックして復旧を提案します。"""
        if self._autosave.has_autosave():
            reply = QMessageBox.question(
                self,
                "自動保存ファイルの検出",
                "前回のセッションから自動保存ファイルが見つかりました。\n"
                "復旧しますか？\n\n"
                "「はい」を選択すると自動保存から復旧します。\n"
                "「いいえ」を選択すると自動保存ファイルを破棄します。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                if self._autosave.restore_from_autosave():
                    self.statusBar().showMessage("自動保存から復旧しました")
                    self._log.append_line("=== 自動保存からプロジェクトを復旧しました ===")
                else:
                    QMessageBox.warning(
                        self, "警告", "自動保存ファイルからの復旧に失敗しました。"
                    )
            else:
                self._autosave.clean_autosave()

    def _new_project(self) -> None:
        """空のプロジェクトを作成します（確認なし）。"""
        self._project = Project()
        # アプリ設定から SNAP.exe デフォルトパスを引き継ぐ
        settings = load_settings()
        if settings.get("snap_exe_path"):
            self._project.snap_exe_path = settings["snap_exe_path"]
        if settings.get("snap_work_dir"):
            self._project.snap_work_dir = settings["snap_work_dir"]
        self._service.set_snap_exe_path(self._project.snap_exe_path)
        self._service.set_snap_work_dir(self._project.snap_work_dir)
        self._autosave.set_project(self._project)
        # s8i未読み込み状態にリセット（前プロジェクトの状態汚染を防ぐ）
        self._case_table.set_model_loaded(False)
        self._case_table.set_project(self._project)
        self._run_selection.set_project(self._project)
        self._model_info.set_model(None)
        self._chart.clear()
        self._log.clear()
        self._update_title()
        # ワークスペース表示に切替してSTEP1から開始
        self._main_stack.setCurrentIndex(1)
        self._sidebar.set_current_step(0)  # 常にSTEP1から開始
        self._update_sidebar_badges()
        self._update_setup_guide()
        # 新規プロジェクトは結果なし
        self._update_result_tabs(result_count=0)

    def _load_s8i_file(self) -> None:
        """入力ファイル (.s8i) を選択して読み込みます。"""
        if self._project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "SNAP 入力ファイルを選択",
            self._project.s8i_path or "",
            "SNAP 入力ファイル (*.s8i);;すべてのファイル (*)",
        )
        if not path:
            return
        try:
            old_s8i = self._project.s8i_path
            model = self._project.load_s8i(path)
            # ロード成功後すぐに状態を確定させる（後続処理で例外が起きても反映される）
            self._case_table.set_model_loaded(True)
            self._model_info.set_model(model)
            self._file_preview.load_file(path)
            # s8i が変更された場合は全ケースをリセット
            if old_s8i and old_s8i != path:
                self._reset_all_cases_for_new_s8i(path)
                self._log.append_line(
                    f"  [INFO] s8iファイルが変更されたため全ケースをリセットしました: {old_s8i} → {path}"
                )
            self._case_table.refresh()
            self._update_title()
            self._log.append_line(f"=== 入力ファイル読み込み: {path} ===")
            self._log.append_line(
                f"  モデル: {model.title or '(無題)'} | "
                f"節点{model.num_nodes} | 層{model.num_floors} | "
                f"ダンパー定義{len(model.damper_defs)}種 | "
                f"制振ブレース{len(model.damper_braces)}本 | "
                f"免制振装置{model.num_dampers}箇所（{model.total_damper_units}基）"
            )
            self._update_setup_guide()
            # s8iロード後にRunSelectionWidgetのチェックリストを更新
            self._run_selection.refresh()
            # UX改善1+2: バッジ更新 & STEP2（ケース設計）へ自動ナビゲート
            self._update_sidebar_badges()
            self._sidebar.set_current_step(1)
            # UX改善⑤新: 読み込んだファイルを最近使ったs8iファイル履歴に追加
            self._model_info.add_recent_s8i(path)
            self.statusBar().showMessage(
                f"✅ モデル読み込み完了: {Path(path).name}  "
                f"→ STEP2 でケースを追加してください",
                6000,
            )
        except Exception as e:
            QMessageBox.critical(
                self, "エラー",
                f"入力ファイルの読み込みに失敗しました:\n{e}"
            )

    def _load_s8i_from_path(self, path: str) -> None:
        """UX改善D: ドロップされた .s8i ファイルを直接読み込みます。"""
        if not path or self._project is None:
            return
        try:
            old_s8i = self._project.s8i_path
            model = self._project.load_s8i(path)
            # ロード成功後すぐに状態を確定させる（後続処理で例外が起きても反映される）
            self._case_table.set_model_loaded(True)
            self._model_info.set_model(model)
            self._file_preview.load_file(path)
            # s8i が変更された場合は全ケースをリセット
            if old_s8i and old_s8i != path:
                self._reset_all_cases_for_new_s8i(path)
                self._log.append_line(
                    f"  [INFO] s8iファイルが変更されたため全ケースをリセットしました: {old_s8i} → {path}"
                )
            self._case_table.refresh()
            self._update_title()
            self._log.append_line(f"=== [ドラッグ&ドロップ] 入力ファイル読み込み: {path} ===")
            self._update_setup_guide()
            self._run_selection.refresh()
            self._update_sidebar_badges()
            self._sidebar.set_current_step(1)
            # UX改善⑤新: 読み込んだファイルを最近使ったs8iファイル履歴に追加
            self._model_info.add_recent_s8i(path)
            self.statusBar().showMessage(
                f"✅ [D&D] モデル読み込み完了: {Path(path).name}  "
                f"({model.num_nodes}節点, {model.num_floors}層)",
                6000,
            )
        except Exception as e:
            QMessageBox.critical(
                self, "エラー",
                f"入力ファイルの読み込みに失敗しました:\n{e}"
            )

    def _refresh_analysis_widgets(self) -> None:
        """ModeShapeWidget と HysteresisWidget に最新のローダーを渡す。

        BinaryResultWidget が set_cases() 後に内部で SnapResultLoader を
        生成・保持しているため、そのエントリを取り出して渡す。
        """
        entries = [
            (e.name, e.loader)
            for e in self._binary_result._entries.values()
        ]
        self._mode_shape_widget.set_entries(entries)
        self._hysteresis_widget.set_entries(entries)
        self._transfer_function_widget.set_entries(entries)

    def _reset_all_cases_for_new_s8i(self, new_s8i_path: str) -> None:
        """
        s8i ファイルが変更された際に全ケースをリセットします。

        - 各ケースの model_path を新しい s8i パスに更新
        - ステータス・解析結果・出力ディレクトリをクリア
        - 結果表示ウィジェットをすべて初期化
        """
        if self._project is None:
            return
        for case in self._project.cases:
            case.model_path = new_s8i_path
            case.output_dir = ""
            case.reset()
            # result_path は dataclass 外で動的に付与される場合もある
            if hasattr(case, "result_path"):
                case.result_path = ""
        self._project._touch()  # type: ignore[attr-defined]
        # 結果ウィジェットをすべてクリア
        self._update_result_tabs(result_count=0)
        self._chart.clear()
        self._compare_chart.set_cases([])
        self._envelope_chart.set_cases([])
        self._radar_chart.set_cases([])
        self._result_table.set_cases([])
        self._binary_result.set_cases([])
        self._mode_shape_widget.set_entries([])
        self._hysteresis_widget.set_entries([])
        self._transfer_function_widget.set_entries([])
        self._ranking.set_cases([])
        self._dashboard.set_cases([])

    def _new_project_dialog(self) -> None:
        """変更確認後に新規プロジェクトを作成します。"""
        if not self._confirm_discard():
            return
        self._new_project()
        self.statusBar().showMessage("新規プロジェクトを作成しました")

    def _open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "snap-controller プロジェクト (*.snapproj);;すべてのファイル (*)",
        )
        if not path:
            return
        self._open_project_from_path(path)

    def _open_project_from_path(self, path: str) -> None:
        """パスからプロジェクトを開きます。"""
        try:
            self._project = Project.load(path)
            self._service.set_snap_exe_path(self._project.snap_exe_path)
            self._service.set_snap_work_dir(self._project.snap_work_dir)
            self._autosave.set_project(self._project)
            self._model_info.set_model(self._project.s8i_model)
            # UX改善②: プロジェクト読込時にモデルロード状態を反映
            self._case_table.set_model_loaded(self._project.has_s8i)
            self._case_table.set_project(self._project)
            self._run_selection.set_project(self._project)
            self._run_selection.set_snap_exe_path(self._project.snap_exe_path or "")
            self._chart.clear()
            self._chart.set_cases(self._project.cases)
            self._update_title()
            self._main_stack.setCurrentIndex(1)  # ワークスペース表示
            # 最近使ったプロジェクトに追加
            add_recent_project(path, self._project.name)
            self._update_recent_menu()
            self._update_setup_guide()
            # 既存の結果件数に応じてタブ有効化
            result_count = sum(
                1 for c in self._project.cases if c.result_summary
            )
            self._update_result_tabs(result_count)
            self._update_sidebar_badges()  # UX改善1: プロジェクト読込後にバッジ更新
            # UX改善⑤新: プロジェクト読込時にグループ情報を比較グラフに反映
            self._compare_chart.set_case_groups(self._project.case_groups)
            # UX改善④新: 解析戦略メモをテキストエリアに復元
            notes = getattr(self._project, "strategy_notes", "")
            self._strategy_notes_edit.blockSignals(True)
            self._strategy_notes_edit.setPlainText(notes)
            self._strategy_notes_edit.blockSignals(False)
            self.statusBar().showMessage(f"プロジェクトを開きました: {path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"ファイルを開けませんでした:\n{e}")

    def _open_recent_project(self, path: str) -> None:
        """最近使ったプロジェクトから開きます。"""
        if not self._confirm_discard():
            return
        self._open_project_from_path(path)

    def _save_project(self) -> bool:
        if self._project is None:
            return False
        if self._project.file_path is None:
            return self._save_project_as()
        try:
            # 保存前にバックアップを作成
            self._autosave.create_backup()
            self._project.save()
            # 正常保存後に自動保存ファイルをクリーン
            self._autosave.clean_autosave()
            self._update_title()
            add_recent_project(str(self._project.file_path), self._project.name)
            self._update_recent_menu()
            self.statusBar().showMessage(f"保存しました: {self._project.file_path}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{e}")
            return False

    def _save_project_as(self) -> bool:
        if self._project is None:
            return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            "名前を付けて保存",
            self._project.name + ".snapproj",
            "snap-controller プロジェクト (*.snapproj);;すべてのファイル (*)",
        )
        if not path:
            return False
        try:
            self._project.save(path)
            self._update_title()
            self.statusBar().showMessage(f"保存しました: {path}")
            return True
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{e}")
            return False

    # ------------------------------------------------------------------
    # Case operations
    # ------------------------------------------------------------------

    def _add_case(self) -> None:
        if self._project is None:
            return
        self._case_table.add_case()

    def _run_selected(self) -> None:
        if self._project is None:
            return
        case_ids = self._case_table.selected_case_ids()
        if not case_ids:
            QMessageBox.information(self, "情報", "実行するケースを選択してください。")
            return
        cases = [
            self._project.get_case(cid)
            for cid in case_ids
            if self._project.get_case(cid) is not None
        ]
        if not cases:
            return
        self._log.clear()
        # バッチキューウィジェットにセット
        self._batch_queue.set_batch(cases)
        if len(cases) == 1:
            self._batch_queue.on_case_started(cases[0].id)
            self._service.run_case(cases[0])
        else:
            self._service.run_all(cases)

    def _run_all(self) -> None:
        if self._project is None:
            return
        self._log.clear()
        cases = list(self._project.cases)
        self._batch_queue.set_batch(cases)
        self._service.run_all(cases)

    def _run_selected_cases(self, case_ids: list[str]) -> None:
        if self._project is None:
            return
        self._log.clear()
        cases = [self._project.get_case(cid) for cid in case_ids if self._project.get_case(cid)]
        if cases:
            self._batch_queue.set_batch(cases)
            self._service.run_all(cases)
            self.statusBar().showMessage(f"{len(cases)} 件のケースをバッチキューに登録しました。")

    def _on_run_requested(self, case_id: str) -> None:
        case = self._project.get_case(case_id) if self._project else None
        if case:
            self._log.clear()
            self._service.run_case(case)


    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_case_selected(self, case_id: str) -> None:
        if self._project is None:
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        if case.result_summary:
            self._chart.show_case(case)
        # モデルファイルがある場合、入力ファイルプレビューに表示
        if case.model_path:
            self._file_preview.load_file(case.model_path)
        # 複数選択時は比較チャートも更新
        selected_ids = self._case_table.selected_case_ids()
        if len(selected_ids) > 1:
            selected_cases = [
                self._project.get_case(cid)
                for cid in selected_ids
                if self._project.get_case(cid) is not None
                and self._project.get_case(cid).result_summary
            ]
            if selected_cases:
                self._compare_chart.set_cases(selected_cases)

        # 改善③: ステータスバーに選択ケースのサマリーを表示
        self._update_case_info_label(case)

    def _update_case_info_label(self, case) -> None:
        """ステータスバーの選択ケース情報ラベルを更新します。"""
        if case is None:
            self._case_info_label.setText("")
            return
        parts = [f"📌 {case.name}"]
        status_label = case.get_status_label() if hasattr(case, "get_status_label") else ""
        if status_label:
            parts.append(status_label)
        rs = case.result_summary or {}
        if rs.get("max_drift"):
            parts.append(f"層間変形角: {rs['max_drift']:.5f}")
        if rs.get("max_acc"):
            parts.append(f"加速度: {rs['max_acc']:.3f} m/s²")
        if rs.get("max_disp"):
            parts.append(f"最大変位: {rs['max_disp']:.4f} m")
        # 判定結果
        if self._project and rs:
            verdict = self._project.criteria.is_all_pass(rs)
            if verdict is True:
                parts.append("判定: ✅ OK")
            elif verdict is False:
                parts.append("判定: ❌ NG")
        self._case_info_label.setText("  |  ".join(parts))

    def _on_analysis_finished(self, case_id: str, success: bool) -> None:
        self._case_table.refresh()
        self._update_setup_guide()
        if self._project:
            result_count = sum(
                1 for c in self._project.cases if c.result_summary
            )
            self._update_result_tabs(result_count)
            self._compare_chart.set_cases(self._project.cases)
            self._compare_chart.set_criteria(self._project.criteria)
            # UX改善⑤新: 解析完了後にグループ情報を比較グラフに反映
            self._compare_chart.set_case_groups(self._project.case_groups)
            self._chart.set_criteria(self._project.criteria)
            self._chart.set_cases(self._project.cases)
            self._envelope_chart.set_cases(self._project.cases)
            self._envelope_chart.set_criteria(self._project.criteria)
            self._radar_chart.set_cases(self._project.cases)
            self._result_table.set_cases(self._project.cases)
            self._binary_result.set_cases(self._project.cases)
            self._refresh_analysis_widgets()
            self._ranking.set_cases(self._project.cases)
            self._ranking.set_criteria(self._project.criteria)
            self._ranking.set_case_groups(self._project.case_groups)
            self._dashboard.set_cases(self._project.cases)
        if success and self._project:
            case = self._project.get_case(case_id)
            if case:
                self._chart.show_case(case)
        # UX改善④新: グローバル進捗インジケーターを更新
        self._update_global_progress()

        # 改善C: 個別ケースの解析エラーをトレイ通知
        if not success and self._project:
            case = self._project.get_case(case_id)
            case_name = case.name if case else case_id
            self._tray_notify(
                "解析エラー",
                f"ケース「{case_name}」の解析中にエラーが発生しました。\nログを確認してください。",
                QSystemTrayIcon.Critical,
            )
            # UX改善③: 解析エラーガイダンスパネルを表示
            if hasattr(self, "_error_guide"):
                log_text = self._log.get_plain_text() if hasattr(self._log, "get_plain_text") else ""
                self._error_guide.show_for_case(
                    case_id=case_id,
                    case_name=case_name,
                    log_text=log_text,
                )
        elif success and hasattr(self, "_error_guide"):
            # 成功したらガイダンスを非表示に
            self._error_guide.hide()

    def _on_progress_updated(self, current: int, total: int) -> None:
        """解析進捗をプログレスバーに反映します。"""
        if total == 0 or current == total:
            self._progress_bar.hide()
        else:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
            self._progress_bar.show()
        # UX改善④新: グローバル進捗インジケーターも更新
        self._update_global_progress()

    def _on_batch_state_changed(self, running: bool) -> None:
        """バッチ実行の開始・終了に応じてUI状態を更新します。"""
        self._act_cancel.setEnabled(running)
        self._act_pause.setEnabled(running)
        if running:
            self._act_pause.setText("⏸ 一時停止")
            self._act_pause.setToolTip("バッチ実行を一時停止します")
            _BATCH_QUEUE_TAB = 9
            if self._right_tabs.isTabEnabled(_BATCH_QUEUE_TAB):
                self._right_tabs.setCurrentIndex(_BATCH_QUEUE_TAB)
            self._run_selection.hide_completion_banner()
        else:
            self._act_pause.setText("⏸ 一時停止")
            self._act_pause.setToolTip("バッチ実行を一時停止します")
            self._on_batch_finished_select_tab()
            self._on_batch_finished_navigate_step4()
            self._on_batch_finished_show_banner()
            self._on_batch_finished_tray_notify()

    def _on_batch_finished_select_tab(self) -> None:
        """解析完了時のタブ自動選択を文脈に応じて最適化する。"""
        if not (self._project and any(c.result_summary for c in self._project.cases)):
            self._update_sidebar_badges()
            return
        completed_n = sum(1 for c in self._project.cases if c.result_summary)
        _TAB_RESULT = 1
        _TAB_COMPARE = 2
        _TAB_DASHBOARD = 0
        _TAB_RANKING = 6
        _has_criteria = (
            self._project.criteria is not None
            and (
                getattr(self._project.criteria, "max_drift", None) is not None
                or getattr(self._project.criteria, "max_acc", None) is not None
            )
        )
        if completed_n == 1:
            _best_tab = _TAB_RESULT
        elif _has_criteria:
            _best_tab = _TAB_RANKING if self._right_tabs.isTabEnabled(_TAB_RANKING) else _TAB_COMPARE
        else:
            _best_tab = _TAB_COMPARE if self._right_tabs.isTabEnabled(_TAB_COMPARE) else _TAB_DASHBOARD
        if self._right_tabs.isTabEnabled(_best_tab):
            self._right_tabs.setCurrentIndex(_best_tab)
        self._update_sidebar_badges()

    def _on_batch_finished_navigate_step4(self) -> None:
        """解析完了時にSTEP4へ自動ナビゲートする（設定依存）。"""
        if not (self._project and any(c.result_summary for c in self._project.cases)):
            return
        completed_n = sum(1 for c in self._project.cases if c.result_summary)
        _auto_step4 = load_settings().get("auto_step4", True)
        if _auto_step4:
            self._sidebar.set_current_step(3)
            self.statusBar().showMessage(
                f"✅ 解析完了: {completed_n}件  "
                f"→ STEP4 で結果を確認してください",
                7000,
            )
        else:
            self.statusBar().showMessage(
                f"✅ 解析完了: {completed_n}件  "
                f"（STEP4「結果・戦略」タブで結果を確認できます）",
                7000,
            )

    def _on_batch_finished_show_banner(self) -> None:
        """解析完了バナーを表示する（ベストケース情報付き）。"""
        if not self._project:
            return
        completed_n = sum(1 for c in self._project.cases if c.result_summary)
        error_count = sum(
            1 for c in self._project.cases
            if c.status and hasattr(c.status, "name") and c.status.name == "ERROR"
        )
        if completed_n == 0:
            return
        best_case_info = ""
        try:
            completed_cases = [
                c for c in self._project.cases
                if c.result_summary and c.result_summary.get("max_drift") is not None
            ]
            if completed_cases:
                best = min(
                    completed_cases,
                    key=lambda c: c.result_summary.get("max_drift", float("inf"))
                )
                drift = best.result_summary.get("max_drift", 0)
                best_case_info = (
                    f"🏆 最良ケース（最小層間変形角）: "
                    f"<b>{best.name}</b>  —  {drift:.5f} rad"
                )
        except Exception:
            logger.debug("best case info formatting failed", exc_info=True)
            best_case_info = ""
        self._run_selection.show_completion_banner(
            completed_n, error_count, best_case_info
        )

    def _on_batch_finished_tray_notify(self) -> None:
        """バッチ解析完了をシステムトレイ通知する。"""
        if not self._project:
            return
        completed = sum(1 for c in self._project.cases if c.result_summary)
        error_count = sum(
            1 for c in self._project.cases
            if c.status and hasattr(c.status, "name") and c.status.name == "ERROR"
        )
        if completed == 0:
            return
        if error_count > 0:
            self._tray_notify(
                "解析完了（エラーあり）",
                f"{completed}件完了、{error_count}件エラー。\nログを確認してください。",
                QSystemTrayIcon.Warning,
            )
        else:
            self._tray_notify(
                "解析完了",
                f"{completed}件の解析が正常に完了しました。\n結果を確認してください。",
                QSystemTrayIcon.Information,
            )

    def _toggle_pause(self) -> None:
        """一時停止/再開を切り替えます。"""
        if self._service.is_paused:
            self._service.resume_batch()
            self._act_pause.setText("⏸ 一時停止")
            self._act_pause.setToolTip("バッチ実行を一時停止します")
        else:
            self._service.pause_batch()
            self._act_pause.setText("▶ 再開")
            self._act_pause.setToolTip("一時停止中のバッチ実行を再開します")

    def _cancel_batch(self) -> None:
        """バッチ実行をキャンセルします（確認ダイアログ付き）。"""
        reply = QMessageBox.question(
            self,
            "バッチキャンセル",
            "実行中のバッチをキャンセルしますか？\n"
            "現在実行中のケースの完了を待ってから停止します。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._service.cancel_batch()
            self._act_pause.setText("⏸ 一時停止")
            self._act_pause.setEnabled(False)
            self._act_cancel.setEnabled(False)
            self._batch_queue.on_batch_finished()

    def _on_batch_queue_case_finished(self, case_id: str, success: bool) -> None:
        """AnalysisService の case_finished をバッチキューに中継します。"""
        self._batch_queue.on_case_finished(case_id, success)

    def _on_batch_queue_state_changed(self, running: bool) -> None:
        """バッチ実行状態の変化をバッチキューウィジェットに通知します。"""
        if not running:
            self._batch_queue.on_batch_finished()
        elif self._service.is_paused:
            self._batch_queue.on_paused()

    def _update_recent_menu(self) -> None:
        """最近使ったプロジェクトメニューを更新します。"""
        from .welcome_widget import get_recent_projects
        self._recent_menu.clear()
        recents = get_recent_projects()
        if not recents:
            act = QAction("（なし）", self)
            act.setEnabled(False)
            self._recent_menu.addAction(act)
            return
        for entry in recents[:MAX_RECENT_MENU]:
            path = entry.get("path", "")
            name = entry.get("name", "")
            act = QAction(f"{name}  ({path})", self)
            act.setData(path)
            act.triggered.connect(lambda checked, p=path: self._open_recent_project(p))
            self._recent_menu.addAction(act)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "snap-controller について",
            "<h3>snap-controller</h3>"
            "<p>SNAPを利用した免振・制振装置設計支援ライブラリ</p>"
            "<p>バージョン 0.1.0</p>"
            "<p>© 2025 BAUES</p>",
        )

    def _show_shortcut_help(self) -> None:
        """改善⑨: キーボードショートカット一覧ダイアログを開きます。"""
        dlg = ShortcutHelpDialog(self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        """未保存変更がある場合に確認ダイアログを出します。True なら続行。"""
        if self._project and self._project.modified:
            reply = QMessageBox.question(
                self,
                "確認",
                "未保存の変更があります。破棄しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return reply == QMessageBox.Yes
        return True

    def _update_title(self) -> None:
        """ウィンドウタイトルをプロジェクト状態に合わせて更新します。"""
        if self._project is None:
            self.setWindowTitle(APP_NAME)
            return
        name = self._project.name or "無題プロジェクト"
        modified = " *" if getattr(self._project, "modified", False) else ""
        self.setWindowTitle(f"{name}{modified} — {APP_NAME}")

    def _restore_settings(self) -> None:
        """ウィンドウのジオメトリ・状態を QSettings から復元します。"""
        s = QSettings(ORG_NAME, APP_NAME)
        geom = s.value("geometry")
        state = s.value("windowState")
        if geom:
            self.restoreGeometry(geom)
        if state:
            self.restoreState(state)

    def closeEvent(self, event: QCloseEvent) -> None:
        """ウィンドウを閉じる前に未保存変更の確認とジオメトリ保存を行います。"""
        if not self._confirm_discard():
            event.ignore()
            return
        # ウィンドウ状態を保存
        s = QSettings(ORG_NAME, APP_NAME)
        s.setValue("geometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        self._autosave.stop()
        event.accept()