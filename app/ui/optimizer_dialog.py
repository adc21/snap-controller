"""
app/ui/optimizer_dialog.py
ダンパー最適化ダイアログ。

ダンパーパラメータの自動最適化を実行するためのダイアログです。
目的関数・制約条件・パラメータ範囲を設定し、
グリッドサーチまたはランダムサーチで最適解を探索します。

レイアウト:
  ┌─────────────────────────────────────────────────────────────┐
  │ 目的関数: [max_drift ▼]  探索手法: [グリッドサーチ ▼]     │
  │ ダンパー種類: [オイルダンパー ▼]                           │
  ├─────────────────────────────────────────────────────────────┤
  │ パラメータ範囲設定                                         │
  │ ┌──────────┬──────┬──────┬──────┐                          │
  │ │パラメータ│最小値│最大値│刻み幅│                          │
  │ ├──────────┼──────┼──────┼──────┤                          │
  │ │Cd        │100   │1000  │100   │                          │
  │ │alpha     │0.1   │1.0   │0.1   │                          │
  │ └──────────┴──────┴──────┴──────┘                          │
  ├─────────────────────────────────────────────────────────────┤
  │ [▶ 最適化開始] [進捗バー]                                  │
  ├─────────────────────────────────────────────────────────────┤
  │ 結果テーブル                           │ 収束グラフ        │
  │ ┌──┬──────┬────────┬───────┐           │                    │
  │ │# │Cd    │drift   │判定   │           │ 📈               │
  │ │1 │500   │0.00321 │OK     │           │                    │
  │ │2 │400   │0.00345 │OK     │           │                    │
  │ └──┴──────┴────────┴───────┘           │                    │
  ├─────────────────────────────────────────────────────────────┤
  │ [最良解をケースに適用] [閉じる]                            │
  └─────────────────────────────────────────────────────────────┘

UX改善（第9回④）: 最適化完了後ベストソリューションサマリーカード追加。
  最適化が完了したとき、結果テーブルの上部に「🏆 最良解が見つかりました」カードを表示します。
  カードには最良パラメータ・目的関数値・制約満足数が大きく表示され、
  テーブルをスクロールしなくても最終結果を一目で把握できます。
  制約を満たす解が見つかった場合は緑、見つからなかった場合は黄色のカラーリングで
  状態を直感的に識別できます。
  `_best_summary_card` QFrame と `_update_best_summary_card()` メソッドを追加。

UX改善（新②）: 初回ユーザー向けガイドバナー + 推定試行数・時間インジケーター追加。

  ガイドバナー:
  - ダイアログ最上部に折りたたみ可能な「最適化とは？」説明パネルを追加。
  - 最適化の仕組み（指定パラメータを変えながら繰り返し解析して最良解を探す）を
    3行で説明し、初めてのユーザーが「何が起きているか」を理解できます。
  - 「▼ 最適化とは？」ボタンで展開/折りたたみを切り替えます。

  推定試行数インジケーター:
  - パラメータ範囲（最小・最大・刻み幅）が変わるたびに、
    グリッドサーチ時の推定試行数をリアルタイムで計算して表示します。
  - 「推定 X 回の解析を実行します（≈ Y分）」の形式で、
    1回30秒を仮定した所要時間の目安も同時に提示します。
  - 試行数が50超で橙色、200超で赤色に変わり、大量試行への注意を促します。
"""

from __future__ import annotations

import csv
import json
from typing import Any, Dict, List, Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from app.models import AnalysisCase
from app.models.performance_criteria import PerformanceCriteria
from app.services.optimizer import (
    DamperOptimizer,
    OptimizationCandidate,
    OptimizationConfig,
    OptimizationResult,
    ParameterRange,
    SensitivityResult,
    compute_sensitivity,
)
from app.services.snap_evaluator import create_snap_evaluator
from .theme import ThemeManager, MPL_STYLES

import logging
logger = logging.getLogger(__name__)

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    logger.debug("Japanese font not available, using default")
    pass

# 目的関数の選択肢 (key, label, unit)
_OBJECTIVE_ITEMS = [
    ("max_drift",       "最大層間変形角",     "rad"),
    ("max_acc",         "最大絶対加速度",     "m/s²"),
    ("max_disp",        "最大相対変位",       "m"),
    ("max_vel",         "最大相対速度",       "m/s"),
    ("max_story_disp",  "最大層間変形",       "m"),
    ("shear_coeff",     "せん断力係数",       "—"),
    ("max_otm",         "最大転倒モーメント", "kN·m"),
    ("peak_gain_db",    "伝達関数1次ピーク", "dB"),
]

# ダンパー種類に応じたデフォルトパラメータ範囲
_DAMPER_PARAM_PRESETS = {
    "オイルダンパー": [
        ParameterRange("Cd", "減衰係数 Cd", 100, 2000, 100),
        ParameterRange("alpha", "速度指数 α", 0.1, 1.0, 0.1),
    ],
    "鋼材ダンパー": [
        ParameterRange("Qy", "降伏荷重 Qy [kN]", 100, 1000, 50),
        ParameterRange("K1", "初期剛性 K1 [kN/m]", 10000, 200000, 10000),
    ],
    "粘性ダンパー": [
        ParameterRange("Ce", "減衰係数 Ce", 50, 1500, 50),
        ParameterRange("alpha", "速度指数 α", 0.8, 1.0, 0.05),
    ],
    "粘弾性ダンパー": [
        ParameterRange("Ce", "減衰係数 Ce", 20, 1000, 50),
        ParameterRange("alpha", "速度指数 α", 0.3, 0.8, 0.05),
    ],
    "免震装置（LRB）": [
        ParameterRange("Qd", "切片荷重 Qd [kN]", 50, 500, 50),
        ParameterRange("K2", "2次剛性 K2 [kN/mm]", 0.5, 5.0, 0.5),
    ],
    "iRDT（回転慣性ダンパー）": [
        ParameterRange("mu", "質量比 μ", 0.01, 0.10, 0.01),
        ParameterRange("zeta_d", "減衰定数 ζ_d", 0.01, 0.30, 0.01),
        ParameterRange("Cd", "減衰係数 Cd", 50, 2000, 50),
    ],
    "iOD（大質量型オイルダンパー）": [
        ParameterRange("mu", "質量比 μ", 0.01, 0.10, 0.01),
        ParameterRange("zeta_d", "減衰定数 ζ_d", 0.01, 0.30, 0.01),
        ParameterRange("Cd", "減衰係数 Cd", 50, 2000, 50),
    ],
}


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _ConvergenceCanvas(FigureCanvas):
    """収束グラフ用の matplotlib キャンバス（2段サブプロット対応）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(4, 5), tight_layout=True, facecolor=facecolor)
        ax_face = MPL_STYLES[theme]["axes.facecolor"]
        self.ax = self.fig.add_subplot(211)
        self.ax.set_facecolor(ax_face)
        self.ax2 = self.fig.add_subplot(212)
        self.ax2.set_facecolor(ax_face)
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


class OptimizerDialog(QDialog):
    """
    ダンパー最適化ダイアログ。

    最適化結果は result プロパティで取得できます。
    最良ケースの damper_params は best_params プロパティで取得できます。
    """

    def __init__(
        self,
        base_case: Optional[AnalysisCase] = None,
        criteria: Optional[PerformanceCriteria] = None,
        snap_exe_path: str = "",
        snap_work_dir: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._base_case = base_case
        self._criteria = criteria
        self._snap_exe_path = snap_exe_path
        self._snap_work_dir = snap_work_dir
        self._optimizer = DamperOptimizer()
        self._result: Optional[OptimizationResult] = None
        self._param_widgets: List[dict] = []
        self._convergence_history: List[float] = []

        self.setWindowTitle("ダンパー最適化")
        self.setMinimumWidth(900)
        self.setMinimumHeight(650)
        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def result(self) -> Optional[OptimizationResult]:
        return self._result

    @property
    def best_params(self) -> dict:
        if self._result and self._result.best:
            return dict(self._result.best.params)
        return {}

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- UX改善（新②）: 折りたたみ式ガイドバナー ----
        guide_toggle_row = QHBoxLayout()
        self._guide_toggle_btn = QPushButton("▶ 最適化とは？（初めての方へ）")
        self._guide_toggle_btn.setFlat(True)
        self._guide_toggle_btn.setStyleSheet(
            "QPushButton { color: #1565c0; font-size: 11px; text-align: left; }"
        )
        self._guide_toggle_btn.clicked.connect(self._toggle_guide_panel)
        guide_toggle_row.addWidget(self._guide_toggle_btn)
        guide_toggle_row.addStretch()
        layout.addLayout(guide_toggle_row)

        self._guide_panel = QWidget()
        self._guide_panel.setVisible(False)
        guide_panel_layout = QVBoxLayout(self._guide_panel)
        guide_panel_layout.setContentsMargins(8, 4, 8, 4)
        self._guide_panel.setStyleSheet(
            "QWidget { background: #e3f2fd; border-radius: 6px; }"
        )
        guide_text = QLabel(
            "<b>ダンパー最適化の仕組み</b><br>"
            "① 指定したパラメータの値を少しずつ変えながら、繰り返しSNAP解析を実行します。<br>"
            "② 各試行の解析結果（例: 最大層間変形角）を目的関数として評価し、最も小さい値を探します。<br>"
            "③ 全試行が終わると最良パラメータを「最良解をケースに適用」で既存ケースに反映できます。<br>"
            "<i>まずはダンパー種類・目的関数・パラメータ範囲を設定して「最適化を開始」してください。</i>"
        )
        guide_text.setWordWrap(True)
        guide_text.setStyleSheet("color: #0d47a1; font-size: 11px; padding: 4px;")
        guide_panel_layout.addWidget(guide_text)
        layout.addWidget(self._guide_panel)

        # ---- 設定セクション ----
        settings_group = QGroupBox("最適化設定")
        settings_layout = QVBoxLayout(settings_group)

        # 行1: 目的関数 + 手法
        row1 = QHBoxLayout()

        row1.addWidget(QLabel("目的関数:"))
        self._obj_combo = QComboBox()
        for key, label, unit in _OBJECTIVE_ITEMS:
            self._obj_combo.addItem(f"{label} [{unit}] を最小化")
        row1.addWidget(self._obj_combo)

        row1.addWidget(QLabel("探索手法:"))
        self._method_combo = QComboBox()
        self._method_combo.addItem("グリッドサーチ", "grid")
        self._method_combo.addItem("ランダムサーチ", "random")
        self._method_combo.addItem("ベイズ最適化 (Bayesian)", "bayesian")
        self._method_combo.addItem("遺伝的アルゴリズム (GA)", "ga")
        self._method_combo.addItem("焼きなまし法 (SA)", "sa")
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        row1.addWidget(self._method_combo)

        row1.addWidget(QLabel("反復数:"))
        self._iter_spin = QSpinBox()
        self._iter_spin.setRange(10, 10000)
        self._iter_spin.setValue(200)
        self._iter_spin.setEnabled(False)  # グリッドサーチでは無効
        row1.addWidget(self._iter_spin)

        row1.addStretch()
        settings_layout.addLayout(row1)

        # 複合目的関数パネル
        self._composite_check = QCheckBox("複合目的関数（重み付き和）")
        self._composite_check.toggled.connect(self._on_composite_toggled)
        settings_layout.addWidget(self._composite_check)

        self._composite_panel = QFrame()
        self._composite_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self._composite_panel.setVisible(False)
        composite_layout = QVBoxLayout(self._composite_panel)
        composite_layout.setContentsMargins(8, 4, 8, 4)
        composite_layout.addWidget(QLabel(
            "各応答値の重みを設定してください（0 = 不使用）。"
            "目的関数 = Σ(重み × 応答値) を最小化します。"
        ))
        self._weight_spins: list[dict] = []
        weight_grid = QHBoxLayout()
        for key, label, unit in _OBJECTIVE_ITEMS[:4]:  # 主要4項目
            col = QVBoxLayout()
            col.addWidget(QLabel(f"{label}"))
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 100.0)
            spin.setValue(0.0)
            spin.setSingleStep(0.1)
            spin.setDecimals(2)
            spin.setPrefix("w=")
            col.addWidget(spin)
            weight_grid.addLayout(col)
            self._weight_spins.append({"key": key, "label": label, "spin": spin})
        composite_layout.addLayout(weight_grid)
        settings_layout.addWidget(self._composite_panel)

        # 行2: ダンパー種類
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("ダンパー種類:"))
        self._damper_combo = QComboBox()
        for dtype in _DAMPER_PARAM_PRESETS.keys():
            self._damper_combo.addItem(dtype)
        self._damper_combo.currentTextChanged.connect(self._on_damper_type_changed)
        row2.addWidget(self._damper_combo)
        row2.addStretch()
        settings_layout.addLayout(row2)

        # パラメータ範囲テーブル
        param_group = QGroupBox("探索パラメータ範囲")
        self._param_layout = QVBoxLayout(param_group)
        self._on_damper_type_changed(self._damper_combo.currentText())
        settings_layout.addWidget(param_group)

        layout.addWidget(settings_group)

        # ---- UX改善（新②）: 推定試行数・時間インジケーター ----
        self._est_run_label = QLabel("")
        self._est_run_label.setStyleSheet("font-size: 11px; color: #1565c0; padding: 2px 4px;")
        layout.addWidget(self._est_run_label)

        # ---- 実行ボタン + 進捗 ----
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("最適化を開始")
        self._run_btn.setMinimumHeight(32)
        self._run_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        run_row.addWidget(self._run_btn)

        self._cancel_btn = QPushButton("キャンセル")
        self._cancel_btn.setEnabled(False)
        run_row.addWidget(self._cancel_btn)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(18)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        run_row.addWidget(self._progress_bar, stretch=1)

        self._progress_label = QLabel("")
        run_row.addWidget(self._progress_label)

        layout.addLayout(run_row)

        # ---- UX改善（第9回④）: ベストソリューションサマリーカード ----
        # 最適化完了後に _update_best_summary_card() で内容を設定して表示します。
        self._best_summary_card = QFrame()
        self._best_summary_card.setFrameShape(QFrame.StyledPanel)
        self._best_summary_card.setStyleSheet(
            "QFrame {"
            "  background-color: #e8f5e9;"
            "  border: 1px solid #66bb6a;"
            "  border-left: 5px solid #2e7d32;"
            "  border-radius: 4px;"
            "}"
        )
        _best_card_layout = QHBoxLayout(self._best_summary_card)
        _best_card_layout.setContentsMargins(12, 8, 12, 8)
        _best_card_layout.setSpacing(16)

        _bc_icon = QLabel("🏆")
        _bc_icon.setStyleSheet("font-size: 20px; background: transparent; border: none;")
        _bc_icon.setFixedWidth(28)
        _best_card_layout.addWidget(_bc_icon)

        _bc_text_col = QVBoxLayout()
        _bc_text_col.setSpacing(2)
        _bc_text_col.setContentsMargins(0, 0, 0, 0)

        self._bc_title_lbl = QLabel("<b>最良解が見つかりました</b>")
        self._bc_title_lbl.setStyleSheet(
            "color: #1b5e20; font-size: 12px; background: transparent; border: none;"
        )
        self._bc_title_lbl.setTextFormat(Qt.RichText)
        _bc_text_col.addWidget(self._bc_title_lbl)

        self._bc_params_lbl = QLabel("")
        self._bc_params_lbl.setStyleSheet(
            "color: #2e7d32; font-size: 10px; background: transparent; border: none;"
        )
        self._bc_params_lbl.setWordWrap(True)
        _bc_text_col.addWidget(self._bc_params_lbl)

        _best_card_layout.addLayout(_bc_text_col, stretch=1)

        self._bc_obj_lbl = QLabel("")
        self._bc_obj_lbl.setStyleSheet(
            "color: #1b5e20; font-size: 16px; font-weight: bold;"
            "  background: transparent; border: none;"
        )
        self._bc_obj_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._bc_obj_lbl.setMinimumWidth(100)
        _best_card_layout.addWidget(self._bc_obj_lbl)

        self._best_summary_card.hide()  # 最適化完了まで非表示
        layout.addWidget(self._best_summary_card)

        # ---- 結果セクション ----
        result_splitter = QSplitter(Qt.Horizontal)

        # 結果テーブル（左）
        table_group = QGroupBox("探索結果 (上位20)")
        table_layout = QVBoxLayout(table_group)

        self._result_table = QTableWidget(0, 5)
        self._result_table.setHorizontalHeaderLabels([
            "順位", "パラメータ", "目的関数値", "判定", "詳細"
        ])
        self._result_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._result_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.Stretch
        )
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.setAlternatingRowColors(True)
        table_layout.addWidget(self._result_table)

        self._result_summary = QLabel("")
        table_layout.addWidget(self._result_summary)

        result_splitter.addWidget(table_group)

        # 収束グラフ（右）
        chart_group = QGroupBox("収束グラフ")
        chart_layout = QVBoxLayout(chart_group)
        self._conv_canvas = _ConvergenceCanvas(self)
        chart_layout.addWidget(self._conv_canvas)
        result_splitter.addWidget(chart_group)

        result_splitter.setStretchFactor(0, 2)
        result_splitter.setStretchFactor(1, 1)
        layout.addWidget(result_splitter, stretch=1)

        # ---- ボタン ----
        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("最良解をケースに適用")
        self._apply_btn.setEnabled(False)
        btn_row.addWidget(self._apply_btn)

        self._export_csv_btn = QPushButton("CSV出力")
        self._export_csv_btn.setEnabled(False)
        self._export_csv_btn.setToolTip("探索結果をCSVファイルに出力します")
        btn_row.addWidget(self._export_csv_btn)

        self._sensitivity_btn = QPushButton("感度解析")
        self._sensitivity_btn.setEnabled(False)
        self._sensitivity_btn.setToolTip(
            "最適解周りのパラメータ感度を解析します（各パラメータを±20%変動）"
        )
        btn_row.addWidget(self._sensitivity_btn)

        self._pareto_btn = QPushButton("Pareto Front")
        self._pareto_btn.setEnabled(False)
        self._pareto_btn.setToolTip(
            "複合目的関数使用時のトレードオフ曲線を表示します"
        )
        btn_row.addWidget(self._pareto_btn)

        self._save_btn = QPushButton("結果保存")
        self._save_btn.setEnabled(False)
        self._save_btn.setToolTip("最適化結果をJSONファイルに保存します")
        btn_row.addWidget(self._save_btn)

        self._load_btn = QPushButton("結果読込")
        self._load_btn.setToolTip("保存済みの最適化結果をJSONファイルから読み込みます")
        btn_row.addWidget(self._load_btn)

        btn_row.addStretch()

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _connect_signals(self) -> None:
        self._run_btn.clicked.connect(self._start_optimization)
        self._cancel_btn.clicked.connect(self._cancel_optimization)
        self._apply_btn.clicked.connect(self._apply_best)
        self._export_csv_btn.clicked.connect(self._export_csv)
        self._sensitivity_btn.clicked.connect(self._run_sensitivity)
        self._pareto_btn.clicked.connect(self._show_pareto)
        self._save_btn.clicked.connect(self._save_result_json)
        self._load_btn.clicked.connect(self._load_result_json)
        self._optimizer.progress.connect(self._on_progress)
        self._optimizer.candidate_found.connect(self._on_candidate)
        self._optimizer.optimization_finished.connect(self._on_finished)
        # UX改善（新②）: パラメータ/手法変更時に推定試行数を更新
        self._method_combo.currentIndexChanged.connect(self._update_est_run_label)
        self._iter_spin.valueChanged.connect(self._update_est_run_label)
        self._damper_combo.currentTextChanged.connect(self._update_est_run_label)

    # ------------------------------------------------------------------
    # UX改善（新②）: ガイドパネル + 推定試行数
    # ------------------------------------------------------------------

    def _toggle_guide_panel(self) -> None:
        """「最適化とは？」ガイドパネルの表示/非表示を切り替えます。"""
        visible = self._guide_panel.isHidden()
        self._guide_panel.setVisible(visible)
        self._guide_toggle_btn.setText(
            "▼ 最適化とは？（初めての方へ）" if visible
            else "▶ 最適化とは？（初めての方へ）"
        )

    def _estimate_grid_runs(self) -> int:
        """グリッドサーチ時の推定試行数を計算します。"""
        total = 1
        for w in self._param_widgets:
            mn = w.get("min")
            mx = w.get("max")
            st = w.get("step")
            if mn is None or mx is None or st is None:
                continue
            try:
                min_v = mn.value()
                max_v = mx.value()
                step_v = st.value()
                if step_v > 0 and max_v > min_v:
                    import math
                    n = math.floor((max_v - min_v) / step_v) + 1
                    total *= max(1, n)
            except (ValueError, AttributeError, TypeError) as e:
                logger.debug("_estimate_grid_runs: skipping parameter (%s)", e)
        return total

    def _update_est_run_label(self) -> None:
        """推定試行数・所要時間ラベルを更新します。"""
        if not hasattr(self, "_est_run_label"):
            return
        method = self._method_combo.currentData() if hasattr(self, "_method_combo") else "grid"

        if method == "grid":
            n_runs = self._estimate_grid_runs()
            method_label = "グリッドサーチ"
        else:
            n_runs = self._iter_spin.value() if hasattr(self, "_iter_spin") else 200
            method_label = "ランダム/ベイズ"

        # 1回あたり30秒と仮定
        est_sec = n_runs * 30
        if est_sec < 60:
            time_str = f"約 {est_sec}秒"
        elif est_sec < 3600:
            time_str = f"約 {est_sec // 60}分"
        else:
            time_str = f"約 {est_sec // 3600}時間"

        if n_runs <= 20:
            color = "#2e7d32"
            icon = "✅"
        elif n_runs <= 50:
            color = "#1565c0"
            icon = "ℹ️"
        elif n_runs <= 200:
            color = "#e65100"
            icon = "⚠"
        else:
            color = "#b71c1c"
            icon = "🔴"

        self._est_run_label.setText(
            f"{icon} 推定 <b>{n_runs}</b> 回の解析を実行します（{method_label} | 所要時間目安: {time_str}）"
        )
        self._est_run_label.setStyleSheet(
            f"font-size: 11px; color: {color}; padding: 2px 4px;"
        )
        self._est_run_label.setTextFormat(
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.RichText
        )

    # ------------------------------------------------------------------
    # Parameter range widgets
    # ------------------------------------------------------------------

    @staticmethod
    def _clear_layout(layout) -> None:
        """レイアウト内の全ウィジェット・サブレイアウトを安全に削除します。

        無限ループ防止のため最大1000回のイテレーションで打ち切ります。
        """
        max_iters = 1000
        count = 0
        while layout.count() and count < max_iters:
            count += 1
            child = layout.takeAt(0)
            if child is None:
                break
            widget = child.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif child.layout() is not None:
                OptimizerDialog._clear_layout(child.layout())
        if count >= max_iters:
            logger.warning("_clear_layout: max iterations reached (%d)", max_iters)

    def _on_damper_type_changed(self, dtype: str) -> None:
        """ダンパー種類変更時にパラメータ範囲ウィジェットを更新します。"""
        # 既存ウィジェットを削除
        for w in self._param_widgets:
            for widget in w.values():
                if hasattr(widget, "deleteLater"):
                    widget.deleteLater()
        self._param_widgets.clear()

        # レイアウトをクリア
        self._clear_layout(self._param_layout)

        presets = _DAMPER_PARAM_PRESETS.get(dtype, [])

        # ヘッダー
        if presets:
            header = QHBoxLayout()
            for label, w in [("パラメータ", 120), ("最小値", 100),
                              ("最大値", 100), ("刻み幅", 100)]:
                lbl = QLabel(f"<b>{label}</b>")
                lbl.setMinimumWidth(w)
                header.addWidget(lbl)
            header.addStretch()
            self._param_layout.addLayout(header)

        for pr in presets:
            row = QHBoxLayout()
            widgets = {}

            label = QLabel(pr.label)
            label.setMinimumWidth(120)
            row.addWidget(label)
            widgets["label_widget"] = label

            min_spin = QDoubleSpinBox()
            min_spin.setDecimals(4)
            min_spin.setRange(-1e12, 1e12)
            min_spin.setValue(pr.min_val)
            min_spin.setMinimumWidth(100)
            row.addWidget(min_spin)
            widgets["min"] = min_spin

            max_spin = QDoubleSpinBox()
            max_spin.setDecimals(4)
            max_spin.setRange(-1e12, 1e12)
            max_spin.setValue(pr.max_val)
            max_spin.setMinimumWidth(100)
            row.addWidget(max_spin)
            widgets["max"] = max_spin

            step_spin = QDoubleSpinBox()
            step_spin.setDecimals(4)
            step_spin.setRange(0, 1e12)
            step_spin.setValue(pr.step)
            step_spin.setMinimumWidth(100)
            row.addWidget(step_spin)
            widgets["step"] = step_spin

            widgets["key"] = pr.key
            widgets["pr_label"] = pr.label
            row.addStretch()
            self._param_layout.addLayout(row)
            self._param_widgets.append(widgets)
            # UX改善（新②）: スピン変更時に推定試行数を更新
            min_spin.valueChanged.connect(self._update_est_run_label)
            max_spin.valueChanged.connect(self._update_est_run_label)
            step_spin.valueChanged.connect(self._update_est_run_label)

        # 初回ラベル更新
        self._update_est_run_label()

    def _on_composite_toggled(self, checked: bool) -> None:
        """複合目的関数チェックボックスのトグル処理。"""
        self._composite_panel.setVisible(checked)
        self._obj_combo.setEnabled(not checked)
        if checked and all(w["spin"].value() == 0 for w in self._weight_spins):
            # デフォルト: 現在選択中の目的関数に重み1.0を設定
            obj_idx = self._obj_combo.currentIndex()
            obj_key = _OBJECTIVE_ITEMS[obj_idx][0]
            for w in self._weight_spins:
                w["spin"].setValue(1.0 if w["key"] == obj_key else 0.0)

    def _on_method_changed(self, index: int) -> None:
        method = self._method_combo.currentData()
        self._iter_spin.setEnabled(method in ("random", "bayesian", "ga", "sa"))

    # ------------------------------------------------------------------
    # Optimization execution
    # ------------------------------------------------------------------

    def _build_config(self) -> OptimizationConfig:
        """現在のUI設定からOptimizationConfigを構築します。"""
        obj_idx = self._obj_combo.currentIndex()
        obj_key, obj_label, _ = _OBJECTIVE_ITEMS[obj_idx]

        params = []
        for w in self._param_widgets:
            pr = ParameterRange(
                key=w["key"],
                label=w["pr_label"],
                min_val=w["min"].value(),
                max_val=w["max"].value(),
                step=w["step"].value(),
            )
            params.append(pr)

        # 複合目的関数の重み
        objective_weights: dict = {}
        if self._composite_check.isChecked():
            for w in self._weight_spins:
                val = w["spin"].value()
                if val > 0:
                    objective_weights[w["key"]] = val
            if objective_weights:
                labels = [w["label"] for w in self._weight_spins if w["spin"].value() > 0]
                obj_label = "複合: " + " + ".join(labels)

        return OptimizationConfig(
            objective_key=obj_key,
            objective_label=obj_label,
            parameters=params,
            method=self._method_combo.currentData(),
            max_iterations=self._iter_spin.value(),
            criteria=self._criteria,
            damper_type=self._damper_combo.currentText(),
            base_case=self._base_case,
            objective_weights=objective_weights,
        )

    def _start_optimization(self) -> None:
        config = self._build_config()

        if not config.parameters:
            QMessageBox.warning(self, "設定エラー", "探索パラメータが設定されていません。")
            return

        # パラメータ範囲バリデーション
        errors: list[str] = []
        for pr in config.parameters:
            if pr.min_val >= pr.max_val:
                errors.append(
                    f"「{pr.label}」: 最小値({pr.min_val})が最大値({pr.max_val})以上です"
                )
            if config.method == "grid" and pr.step <= 0:
                errors.append(
                    f"「{pr.label}」: グリッドサーチの刻み幅が0以下です"
                )
            elif config.method == "grid" and pr.step > (pr.max_val - pr.min_val):
                errors.append(
                    f"「{pr.label}」: 刻み幅({pr.step})が探索範囲({pr.max_val - pr.min_val:.4g})より大きいです"
                )
        if errors:
            QMessageBox.warning(
                self, "パラメータ設定エラー",
                "以下の問題を修正してください:\n\n" + "\n".join(f"• {e}" for e in errors),
            )
            return

        # 複合目的関数の重みバリデーション
        if config.objective_weights:
            total_weight = sum(config.objective_weights.values())
            if total_weight <= 0:
                QMessageBox.warning(
                    self, "設定エラー",
                    "複合目的関数の重みの合計が0です。\n"
                    "少なくとも1つの目的関数に正の重みを設定してください。",
                )
                return

        # D-3: 大量試行時の事前警告ダイアログ
        n_runs = (self._estimate_grid_runs()
                  if config.method == "grid"
                  else self._iter_spin.value())
        if n_runs > 50:
            est_sec = n_runs * 30
            if est_sec < 3600:
                time_str = f"約 {est_sec // 60} 分"
            else:
                h = est_sec // 3600
                m = (est_sec % 3600) // 60
                time_str = f"約 {h} 時間 {m} 分"

            reply = QMessageBox.question(
                self, "計算時間の確認",
                f"推定 {n_runs} 回の解析を実行します。\n"
                f"所要時間の目安: {time_str}（1回あたり30秒と仮定）\n\n"
                f"続行しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # UIリセット
        self._result_table.setRowCount(0)
        self._convergence_history.clear()
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._apply_btn.setEnabled(False)
        self._export_csv_btn.setEnabled(False)
        self._sensitivity_btn.setEnabled(False)
        self._pareto_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._progress_bar.show()
        self._progress_bar.setValue(0)

        # グラフクリア
        self._conv_canvas.ax.clear()
        self._conv_canvas.ax.text(
            0.5, 0.5, "最適化を実行中...",
            ha="center", va="center",
            transform=self._conv_canvas.ax.transAxes,
            fontsize=10, color="gray"
        )
        self._conv_canvas.draw()

        # SNAP評価が利用可能か試みる
        evaluate_fn = None
        if self._base_case and self._snap_exe_path:
            snap_evaluator = create_snap_evaluator(
                snap_exe_path=self._snap_exe_path,
                base_case=self._base_case,
                param_ranges=config.parameters,
                log_callback=lambda msg: self._result_summary.setText(msg),
                snap_work_dir=self._snap_work_dir,
            )
            if snap_evaluator:
                evaluate_fn = snap_evaluator
                self._result_summary.setText(
                    "SNAP実行モードで最適化を実行中..."
                )
            else:
                # 具体的な原因を特定してログ + UI表示
                from pathlib import Path
                reasons: list[str] = []
                exe = self._snap_exe_path
                if not exe:
                    reasons.append("SNAP.exe パスが未設定")
                elif not Path(exe).exists():
                    reasons.append(f"SNAP.exe が存在しません: {exe}")
                model = getattr(self._base_case, "model_path", "")
                if not model:
                    reasons.append("モデルファイルが未設定")
                elif not Path(model).exists():
                    reasons.append(f"モデルファイルが存在しません: {model}")
                reason_str = "、".join(reasons) if reasons else "不明なエラー"
                logger.warning(
                    "SNAP評価モード不可 → モック評価にフォールバック: %s", reason_str
                )
                self._result_summary.setText(
                    f"モック評価モードで実行中（{reason_str}）"
                )
        else:
            missing: list[str] = []
            if not self._base_case:
                missing.append("解析ケース")
            if not self._snap_exe_path:
                missing.append("SNAP.exe パス")
            if missing:
                detail = "、".join(missing) + "が未設定"
            else:
                detail = ""
            logger.info("SNAP未設定 → モック評価モード: %s", detail)
            self._result_summary.setText(
                f"モック評価モードで実行中"
                + (f"（{detail}）" if detail else "")
            )

        self._optimizer.optimize(config, evaluate_fn=evaluate_fn)

    def _cancel_optimization(self) -> None:
        self._optimizer.cancel()
        self._cancel_btn.setEnabled(False)
        self._progress_label.setText("キャンセル中...")

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._progress_label.setText(message)

    def _on_candidate(self, candidate: OptimizationCandidate) -> None:
        if candidate.is_feasible:
            self._convergence_history.append(candidate.objective_value)

    def _on_finished(self, result: OptimizationResult) -> None:
        self._result = result
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.hide()
        self._progress_label.setText(
            f"完了: {result.elapsed_sec:.1f}秒, "
            f"{len(result.all_candidates)}点評価"
        )

        # 結果テーブルを更新
        self._populate_result_table(result)

        # 収束グラフを更新
        self._draw_convergence(result)

        # UX改善（第9回④）: ベストソリューションサマリーカードを更新
        self._update_best_summary_card(result)

        # サマリー
        if result.best:
            obj_label = result.config.objective_label if result.config else "目的関数"
            eval_tag = "[SNAP]" if result.evaluation_method == "snap" else "[モック]"
            self._result_summary.setText(
                f"{eval_tag} 最良解: {obj_label} = {result.best.objective_value:.6g}  |  "
                f"制約満足: {len(result.feasible_candidates)} / {len(result.all_candidates)} 点"
            )
            self._apply_btn.setEnabled(True)
            self._export_csv_btn.setEnabled(True)
            self._sensitivity_btn.setEnabled(True)
            self._save_btn.setEnabled(True)
            # Paretoボタンは複合目的関数使用時のみ有効
            if result.config and result.config.objective_weights:
                self._pareto_btn.setEnabled(True)
        else:
            self._result_summary.setText(
                "制約を満たす解が見つかりませんでした。"
                "パラメータ範囲や制約条件を見直してください。"
            )
            # NG結果でもCSV/JSON出力は許可（デバッグ用）
            if result.all_candidates:
                self._export_csv_btn.setEnabled(True)
                self._save_btn.setEnabled(True)

    def _update_best_summary_card(self, result: "OptimizationResult") -> None:
        """
        UX改善（第9回④）: ベストソリューションサマリーカードを最適化結果で更新します。

        最適化完了後、結果テーブルの上部カードに最良パラメータ・目的関数値・
        制約満足状況を表示します。制約を満たす解があれば緑、なければ黄色で表示します。

        Parameters
        ----------
        result : OptimizationResult
            最適化結果オブジェクト。
        """
        if not hasattr(self, "_best_summary_card"):
            return

        if not result.best:
            # 解が見つからなかった場合: 黄色カードで警告表示
            self._best_summary_card.setStyleSheet(
                "QFrame {"
                "  background-color: #fff8e1;"
                "  border: 1px solid #ffca28;"
                "  border-left: 5px solid #f57f17;"
                "  border-radius: 4px;"
                "}"
            )
            self._bc_title_lbl.setText("<b>⚠ 制約を満たす解が見つかりませんでした</b>")
            self._bc_title_lbl.setStyleSheet(
                "color: #e65100; font-size: 12px; background: transparent; border: none;"
            )
            self._bc_params_lbl.setText(
                "パラメータ範囲を広げるか、制約条件を緩和して再度お試しください。"
            )
            self._bc_params_lbl.setStyleSheet(
                "color: #bf360c; font-size: 10px; background: transparent; border: none;"
            )
            self._bc_obj_lbl.setText(
                f"{len(result.all_candidates)}点\n評価済み"
            )
            self._bc_obj_lbl.setStyleSheet(
                "color: #e65100; font-size: 13px; font-weight: bold;"
                "  background: transparent; border: none; text-align: right;"
            )
            self._best_summary_card.show()
            return

        # 最良解あり: 緑カードで表示
        obj_label = result.config.objective_label if result.config else "目的関数"
        feasible_count = len(result.feasible_candidates)
        total_count = len(result.all_candidates)

        # パラメータ文字列を構築
        param_strs = [f"{k} = {v:.4g}" for k, v in result.best.params.items()]
        params_text = "  /  ".join(param_strs)
        feasibility_text = f"制約満足: {feasible_count}/{total_count}点"

        self._best_summary_card.setStyleSheet(
            "QFrame {"
            "  background-color: #e8f5e9;"
            "  border: 1px solid #66bb6a;"
            "  border-left: 5px solid #2e7d32;"
            "  border-radius: 4px;"
            "}"
        )
        self._bc_title_lbl.setText(
            f"<b>🏆 最良解が見つかりました</b>  "
            f"<span style='font-size:10px; color:#388e3c;'>（{feasibility_text}）</span>"
        )
        self._bc_title_lbl.setStyleSheet(
            "color: #1b5e20; font-size: 12px; background: transparent; border: none;"
        )
        self._bc_params_lbl.setText(params_text)
        self._bc_params_lbl.setStyleSheet(
            "color: #2e7d32; font-size: 10px; background: transparent; border: none;"
        )
        self._bc_obj_lbl.setText(
            f"{obj_label}\n{result.best.objective_value:.5g}"
        )
        self._bc_obj_lbl.setStyleSheet(
            "color: #1b5e20; font-size: 14px; font-weight: bold;"
            "  background: transparent; border: none;"
        )
        self._best_summary_card.show()

    def _populate_result_table(self, result: OptimizationResult) -> None:
        """結果テーブルを上位20候補で更新します。"""
        self._result_table.setRowCount(0)
        ranked = result.ranked_candidates[:20]

        obj_key = result.config.objective_key if result.config else ""

        for rank, cand in enumerate(ranked):
            row = self._result_table.rowCount()
            self._result_table.insertRow(row)

            # 順位
            rank_item = QTableWidgetItem(str(rank + 1))
            rank_item.setTextAlignment(Qt.AlignCenter)
            if rank < 3:
                font = QFont()
                font.setBold(True)
                rank_item.setFont(font)
                colors = [QColor("#FFD700"), QColor("#C0C0C0"), QColor("#CD7F32")]
                rank_item.setForeground(colors[rank])
            self._result_table.setItem(row, 0, rank_item)

            # パラメータ
            param_strs = [f"{k}={v:.4g}" for k, v in cand.params.items()]
            self._result_table.setItem(row, 1, QTableWidgetItem(", ".join(param_strs)))

            # 目的関数値
            obj_item = QTableWidgetItem(f"{cand.objective_value:.6g}")
            obj_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._result_table.setItem(row, 2, obj_item)

            # 判定
            verdict = "OK" if cand.is_feasible else "NG"
            verdict_item = QTableWidgetItem(verdict)
            verdict_item.setTextAlignment(Qt.AlignCenter)
            verdict_item.setForeground(
                QColor("#2ca02c") if cand.is_feasible else QColor("#d62728")
            )
            self._result_table.setItem(row, 3, verdict_item)

            # 詳細（他の応答値）
            details = []
            for k, v in cand.response_values.items():
                if k != obj_key:
                    details.append(f"{k}={v:.4g}")
            self._result_table.setItem(
                row, 4, QTableWidgetItem(", ".join(details[:3]))
            )

    def _draw_convergence(self, result: OptimizationResult) -> None:
        """収束グラフ（上段: 収束履歴、下段: パラメータ空間探索）を描画します。"""
        ax = self._conv_canvas.ax
        ax2 = self._conv_canvas.ax2
        ax.clear()
        ax2.clear()

        if not result.all_candidates:
            ax.text(0.5, 0.5, "データなし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            ax2.set_visible(False)
            self._conv_canvas.draw()
            return

        ax2.set_visible(True)

        # --- 上段: 収束履歴 (feasible/infeasible 色分け) ---
        feasible_iters = []
        feasible_vals = []
        infeasible_iters = []
        infeasible_vals = []
        for i, c in enumerate(result.all_candidates, 1):
            if c.objective_value == float("inf"):
                continue
            if c.is_feasible:
                feasible_iters.append(i)
                feasible_vals.append(c.objective_value)
            else:
                infeasible_iters.append(i)
                infeasible_vals.append(c.objective_value)

        if not feasible_vals and not infeasible_vals:
            ax.text(0.5, 0.5, "有効なデータなし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            ax2.set_visible(False)
            self._conv_canvas.draw()
            return

        # infeasible を先に描画（背景）
        if infeasible_vals:
            ax.scatter(infeasible_iters, infeasible_vals,
                       s=6, alpha=0.2, color="#d62728", marker="x",
                       label="制約外 (NG)")

        # feasible
        if feasible_vals:
            ax.scatter(feasible_iters, feasible_vals,
                       s=6, alpha=0.4, color="#1f77b4", label="制約内 (OK)")

            # 累積最小値
            best_so_far = []
            current_best = float("inf")
            for v in feasible_vals:
                current_best = min(current_best, v)
                best_so_far.append(current_best)
            ax.plot(feasible_iters, best_so_far,
                    color="#ff7f0e", linewidth=2, label="累積最良値")

        obj_label = result.config.objective_label if result.config else "目的関数"
        ax.set_xlabel("評価回数", fontsize=7)
        ax.set_ylabel(obj_label, fontsize=7)
        ax.set_title("収束履歴", fontsize=9)
        ax.tick_params(labelsize=6)
        ax.legend(fontsize=6, loc="upper right")
        ax.grid(linestyle="--", alpha=0.3)

        # --- 下段: パラメータ空間探索の可視化 ---
        self._draw_param_space(ax2, result)

        self._conv_canvas.fig.tight_layout()
        self._conv_canvas.draw()

    def _draw_param_space(self, ax: Any, result: OptimizationResult) -> None:
        """パラメータ空間の探索分布を描画します。

        パラメータ数に応じて最適な可視化を選択:
        - 1パラメータ: パラメータ値 vs 目的関数の散布図
        - 2パラメータ: 2D散布図（色＝目的関数値）
        - 3パラメータ以上: 主要2パラメータの2D散布図
        """
        candidates = [c for c in result.all_candidates
                      if c.objective_value != float("inf")]
        if not candidates:
            ax.text(0.5, 0.5, "可視化データなし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            return

        param_keys = list(candidates[0].params.keys())
        if not param_keys:
            ax.text(0.5, 0.5, "パラメータなし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            return

        obj_vals = np.array([c.objective_value for c in candidates])
        feasible_mask = np.array([c.is_feasible for c in candidates])

        if len(param_keys) == 1:
            # 1パラメータ: 散布図
            p_key = param_keys[0]
            p_vals = np.array([c.params.get(p_key, 0) for c in candidates])

            # feasible / infeasible 色分け
            if np.any(feasible_mask):
                sc = ax.scatter(p_vals[feasible_mask], obj_vals[feasible_mask],
                                c=obj_vals[feasible_mask], cmap="viridis",
                                s=12, alpha=0.6, edgecolors="none")
                self._conv_canvas.fig.colorbar(sc, ax=ax, pad=0.02,
                                               aspect=20, shrink=0.8)
            if np.any(~feasible_mask):
                ax.scatter(p_vals[~feasible_mask], obj_vals[~feasible_mask],
                           s=8, alpha=0.2, color="#d62728", marker="x")

            # 最良解マーカー
            if result.best:
                ax.scatter([result.best.params.get(p_key, 0)],
                           [result.best.objective_value],
                           s=60, color="red", marker="*", zorder=10,
                           label="最良解")

            p_label = param_keys[0]
            for pr in (result.config.parameters if result.config else []):
                if pr.key == p_key:
                    p_label = pr.label
                    break
            obj_label = result.config.objective_label if result.config else "目的関数"
            ax.set_xlabel(p_label, fontsize=7)
            ax.set_ylabel(obj_label, fontsize=7)
            ax.set_title("パラメータ空間探索", fontsize=9)

        else:
            # 2パラメータ以上: 主要2パラメータの2D散布図
            pk0, pk1 = param_keys[0], param_keys[1]
            p0_vals = np.array([c.params.get(pk0, 0) for c in candidates])
            p1_vals = np.array([c.params.get(pk1, 0) for c in candidates])

            if np.any(feasible_mask):
                sc = ax.scatter(
                    p0_vals[feasible_mask], p1_vals[feasible_mask],
                    c=obj_vals[feasible_mask], cmap="viridis",
                    s=12, alpha=0.6, edgecolors="none",
                )
                self._conv_canvas.fig.colorbar(sc, ax=ax, pad=0.02,
                                               aspect=20, shrink=0.8)
            if np.any(~feasible_mask):
                ax.scatter(p0_vals[~feasible_mask], p1_vals[~feasible_mask],
                           s=8, alpha=0.2, color="#d62728", marker="x")

            # 最良解マーカー
            if result.best:
                ax.scatter([result.best.params.get(pk0, 0)],
                           [result.best.params.get(pk1, 0)],
                           s=60, color="red", marker="*", zorder=10,
                           label="最良解")

            # ラベル取得
            labels = {}
            for pr in (result.config.parameters if result.config else []):
                labels[pr.key] = pr.label
            ax.set_xlabel(labels.get(pk0, pk0), fontsize=7)
            ax.set_ylabel(labels.get(pk1, pk1), fontsize=7)
            ax.set_title("パラメータ空間探索（色 = 目的関数値）", fontsize=9)

        ax.tick_params(labelsize=6)
        ax.legend(fontsize=6, loc="upper right")
        ax.grid(linestyle="--", alpha=0.3)

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _apply_best(self) -> None:
        """最良解をケースに適用して閉じます。"""
        if self._result and self._result.best:
            self.accept()
        else:
            QMessageBox.information(self, "情報", "適用可能な最良解がありません。")

    def _export_csv(self) -> None:
        """探索結果をCSVファイルにエクスポートします。"""
        if not self._result or not self._result.all_candidates:
            QMessageBox.information(self, "情報", "エクスポートする結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "CSV出力先を選択", "optimization_results.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return

        ranked = self._result.ranked_candidates or self._result.all_candidates
        obj_key = self._result.config.objective_key if self._result.config else ""

        # ヘッダ構築: パラメータ名 + 目的関数 + 判定 + 応答値
        if ranked:
            param_keys = list(ranked[0].params.keys())
            response_keys = sorted({
                k for c in ranked for k in c.response_values.keys()
            })
        else:
            param_keys = []
            response_keys = []

        header = ["順位"] + param_keys + ["目的関数値", "判定"] + response_keys

        try:
            eval_label = "SNAP実解析" if self._result.evaluation_method == "snap" else "モック評価（デモ用）"
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([f"# 評価方式: {eval_label}"])
                writer.writerow(header)
                for rank, cand in enumerate(ranked):
                    row = [rank + 1]
                    row += [cand.params.get(k, "") for k in param_keys]
                    row.append(cand.objective_value)
                    row.append("OK" if cand.is_feasible else "NG")
                    row += [cand.response_values.get(k, "") for k in response_keys]
                    writer.writerow(row)

            QMessageBox.information(
                self, "CSV出力完了",
                f"{len(ranked)} 件の探索結果を出力しました。\n{path}",
            )
        except OSError as e:
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗しました:\n{e}")

    def _run_sensitivity(self) -> None:
        """最適解周りのパラメータ感度解析を実行し、結果ダイアログを表示します。"""
        if not self._result or not self._result.best or not self._result.config:
            return

        config = self._result.config
        best_params = self._result.best.params

        # 評価関数を取得（SNAP or モック）
        evaluate_fn = None
        if self._base_case and self._snap_exe_path:
            evaluate_fn = create_snap_evaluator(
                snap_exe_path=self._snap_exe_path,
                base_case=self._base_case,
                param_ranges=config.parameters,
                snap_work_dir=self._snap_work_dir,
            )
        if evaluate_fn is None:
            from app.services.optimizer import _mock_evaluate
            base = {}
            if config.base_case and config.base_case.result_summary:
                base = config.base_case.result_summary
            evaluate_fn = lambda params: _mock_evaluate(
                params, base, config.objective_key
            )

        try:
            sensitivity = compute_sensitivity(
                evaluate_fn=evaluate_fn,
                best_params=best_params,
                parameters=config.parameters,
                objective_key=config.objective_key,
            )
            sensitivity.objective_label = config.objective_label
        except Exception as exc:
            logger.warning("感度解析に失敗しました: %s", exc, exc_info=True)
            QMessageBox.warning(
                self, "感度解析エラー",
                f"感度解析の実行中にエラーが発生しました:\n{exc}",
            )
            return

        dlg = SensitivityDialog(sensitivity, parent=self)
        dlg.exec()

    def _show_pareto(self) -> None:
        """Pareto frontダイアログを表示します。"""
        if not self._result:
            return
        dlg = ParetoDialog(self._result, parent=self)
        dlg.exec()

    def _save_result_json(self) -> None:
        """最適化結果をJSONファイルに保存します。"""
        if not self._result:
            QMessageBox.information(self, "情報", "保存する結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "結果の保存先を選択", "optimization_result.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            self._result.save_json(path)
            QMessageBox.information(
                self, "保存完了",
                f"最適化結果を保存しました。\n{path}\n"
                f"({len(self._result.all_candidates)} 件の候補データ)",
            )
        except OSError as e:
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗しました:\n{e}")

    def _load_result_json(self) -> None:
        """JSONファイルから最適化結果を読み込み、ダイアログに反映します。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "結果ファイルを選択", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            result = OptimizationResult.load_json(path)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            QMessageBox.warning(self, "読込エラー", f"ファイルの読み込みに失敗しました:\n{e}")
            return

        if not result.all_candidates:
            QMessageBox.information(self, "情報", "候補データが含まれていません。")
            return

        # 読み込んだ結果をダイアログに反映
        self._result = result
        self._populate_result_table(result)
        self._draw_convergence(result)
        self._update_best_summary_card(result)

        # ボタン有効化
        self._export_csv_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        if result.best:
            self._apply_btn.setEnabled(True)
            self._sensitivity_btn.setEnabled(True)
        if result.config and result.config.objective_weights:
            self._pareto_btn.setEnabled(True)

        obj_label = result.config.objective_label if result.config else "目的関数"
        n_cands = len(result.all_candidates)
        n_feasible = len(result.feasible_candidates)
        self._result_summary.setText(
            f"読込完了: {n_cands}点, 制約満足: {n_feasible}点"
        )
        self._progress_label.setText(
            f"JSONから読込 ({result.elapsed_sec:.1f}秒の結果)"
        )


class SensitivityDialog(QDialog):
    """パラメータ感度解析結果を表示するダイアログ。

    - トルネードチャート: パラメータ別の感度指標ランキング
    - 感度曲線: 各パラメータの変動に対する目的関数の変化
    """

    def __init__(
        self,
        result: SensitivityResult,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("パラメータ感度解析")
        self.setMinimumSize(700, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ヘッダ
        header = QLabel(
            f"目的関数: {self._result.objective_label or self._result.objective_key}  |  "
            f"基準値: {self._result.base_objective:.6g}"
        )
        header.setStyleSheet("font-weight: bold; font-size: 13px; padding: 4px;")
        layout.addWidget(header)

        # 上下分割: トルネードチャート + 感度曲線
        splitter = QSplitter(Qt.Vertical)

        # トルネードチャート
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        ax_face = MPL_STYLES[theme]["axes.facecolor"]

        self._tornado_fig = Figure(figsize=(6, 3), tight_layout=True, facecolor=facecolor)
        self._tornado_ax = self._tornado_fig.add_subplot(111)
        self._tornado_ax.set_facecolor(ax_face)
        tornado_canvas = FigureCanvas(self._tornado_fig)
        splitter.addWidget(tornado_canvas)

        # 感度曲線
        self._curve_fig = Figure(figsize=(6, 3), tight_layout=True, facecolor=facecolor)
        self._curve_ax = self._curve_fig.add_subplot(111)
        self._curve_ax.set_facecolor(ax_face)
        curve_canvas = FigureCanvas(self._curve_fig)
        splitter.addWidget(curve_canvas)

        layout.addWidget(splitter, stretch=1)

        # ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._draw_tornado()
        self._draw_curves()

    def _draw_tornado(self) -> None:
        """トルネードチャート（感度指標の水平棒グラフ）を描画します。"""
        ax = self._tornado_ax
        ax.clear()

        ranked = self._result.ranked_entries
        if not ranked:
            ax.text(0.5, 0.5, "感度データなし", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11, color="gray")
            return

        labels = [e.label for e in ranked]
        values = [e.sensitivity_index * 100 for e in ranked]  # %表示

        y_pos = np.arange(len(labels))
        colors = []
        for v in values:
            if v >= 10:
                colors.append("#e74c3c")  # 高感度: 赤
            elif v >= 5:
                colors.append("#f39c12")  # 中感度: 橙
            else:
                colors.append("#3498db")  # 低感度: 青

        bars = ax.barh(y_pos, values, color=colors, height=0.6, edgecolor="none")
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels)
        ax.set_xlabel("感度指標 [%]")
        ax.set_title("パラメータ感度ランキング", fontsize=11)
        ax.invert_yaxis()

        # 値ラベル
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}%", va="center", fontsize=9,
            )

        self._tornado_fig.tight_layout()

    def _draw_curves(self) -> None:
        """各パラメータの感度曲線（変動率 vs 目的関数値）を描画します。"""
        ax = self._curve_ax
        ax.clear()

        entries = self._result.ranked_entries
        if not entries:
            ax.text(0.5, 0.5, "感度データなし", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11, color="gray")
            return

        colors_cycle = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
                        "#1abc9c", "#e67e22", "#34495e"]
        base_obj = self._result.base_objective

        for i, entry in enumerate(entries):
            if not entry.variations or not entry.objective_values:
                continue
            color = colors_cycle[i % len(colors_cycle)]
            pct_vals = [v * 100 for v in entry.variations]

            # 正規化: ベース値からの変化率
            if base_obj != 0:
                norm_obj = [(o / base_obj - 1.0) * 100 for o in entry.objective_values]
            else:
                norm_obj = entry.objective_values

            ax.plot(pct_vals, norm_obj, "o-", color=color, label=entry.label,
                    markersize=4, linewidth=1.5)

        ax.axhline(0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.axvline(0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.set_xlabel("パラメータ変動率 [%]")
        ax.set_ylabel("目的関数変化率 [%]")
        ax.set_title("パラメータ感度曲線", fontsize=11)
        ax.legend(fontsize=8, loc="best")

        self._curve_fig.tight_layout()


class ParetoDialog(QDialog):
    """複合目的関数使用時のPareto front（トレードオフ曲線）可視化ダイアログ。

    各候補の個別目的関数値を2D散布図で表示し、
    制約満足/不満足を色分けして最良解を強調表示する。
    2目的の場合は直接散布図、3目的以上はペアプロットを表示。
    """

    def __init__(
        self,
        result: OptimizationResult,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("Pareto Front — 目的関数トレードオフ")
        self.setMinimumSize(750, 550)

        layout = QVBoxLayout(self)

        # 目的関数のキーと重みを取得
        config = result.config
        if not config or not config.objective_weights:
            layout.addWidget(QLabel("複合目的関数が使用されていません。"))
            return

        self._obj_keys: List[str] = list(config.objective_weights.keys())
        self._obj_labels: Dict[str, str] = {}
        for key, label, _ in _OBJECTIVE_ITEMS:
            if key in config.objective_weights:
                self._obj_labels[key] = label
        # フォールバック
        for key in self._obj_keys:
            if key not in self._obj_labels:
                self._obj_labels[key] = key

        n_obj = len(self._obj_keys)
        if n_obj < 2:
            layout.addWidget(QLabel("トレードオフ表示には2つ以上の目的関数が必要です。"))
            return

        # 説明ラベル
        weight_strs = [
            f"{self._obj_labels[k]}(w={config.objective_weights[k]:.2g})"
            for k in self._obj_keys
        ]
        desc = QLabel(f"目的関数: {' × '.join(weight_strs)}")
        desc.setStyleSheet("font-size: 11px; color: gray; padding: 4px;")
        layout.addWidget(desc)

        # チャートの構築
        if n_obj == 2:
            n_rows, n_cols = 1, 1
        else:
            # ペアプロット: C(n,2) 個のサブプロット
            n_pairs = n_obj * (n_obj - 1) // 2
            n_cols = min(n_pairs, 3)
            n_rows = (n_pairs + n_cols - 1) // n_cols

        is_dark = ThemeManager.is_dark()
        bg = "#2b2b2b" if is_dark else "#ffffff"
        fg = "#cccccc" if is_dark else "#333333"

        fig = Figure(figsize=(5 * n_cols, 4 * n_rows), facecolor=bg)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)

        self._draw_pareto(fig, is_dark, bg, fg)

        fig.tight_layout(pad=2.0)
        canvas.draw()

        # 閉じるボタン
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _draw_pareto(
        self, fig: Figure, is_dark: bool, bg: str, fg: str,
    ) -> None:
        """散布図を描画する。"""
        config = self._result.config
        keys = self._obj_keys
        n_obj = len(keys)
        candidates = self._result.all_candidates
        best = self._result.best

        # データ抽出
        feasible_data = {k: [] for k in keys}
        infeasible_data = {k: [] for k in keys}
        for cand in candidates:
            target = feasible_data if cand.is_feasible else infeasible_data
            for k in keys:
                target[k].append(cand.response_values.get(k, float("nan")))

        best_data = {}
        if best:
            for k in keys:
                best_data[k] = best.response_values.get(k, float("nan"))

        # ペアプロット生成
        pairs = []
        for i in range(n_obj):
            for j in range(i + 1, n_obj):
                pairs.append((keys[i], keys[j]))

        for idx, (kx, ky) in enumerate(pairs):
            ax = fig.add_subplot(
                len(pairs) // min(len(pairs), 3) + (1 if len(pairs) % min(len(pairs), 3) else 0),
                min(len(pairs), 3),
                idx + 1,
                facecolor=bg,
            )

            # infeasible (灰色)
            if infeasible_data[kx]:
                ax.scatter(
                    infeasible_data[kx], infeasible_data[ky],
                    c="#999999", alpha=0.3, s=20, label="制約不満足",
                    edgecolors="none",
                )

            # feasible (青)
            if feasible_data[kx]:
                ax.scatter(
                    feasible_data[kx], feasible_data[ky],
                    c="#2196F3", alpha=0.6, s=30, label="制約満足",
                    edgecolors="none",
                )

            # Pareto front をハイライト（非劣解の抽出）
            pareto_x, pareto_y = self._extract_pareto_front(
                feasible_data[kx], feasible_data[ky]
            )
            if pareto_x:
                sorted_pairs = sorted(zip(pareto_x, pareto_y))
                px = [p[0] for p in sorted_pairs]
                py = [p[1] for p in sorted_pairs]
                ax.plot(px, py, "o-", color="#FF9800", markersize=5,
                        linewidth=1.5, label="Pareto front", alpha=0.8)

            # 最良解 (星マーカー)
            if best_data:
                ax.scatter(
                    [best_data[kx]], [best_data[ky]],
                    marker="*", c="#FF5722", s=200, zorder=5,
                    label="最良解", edgecolors="white", linewidths=0.5,
                )

            ax.set_xlabel(self._obj_labels.get(kx, kx), color=fg, fontsize=9)
            ax.set_ylabel(self._obj_labels.get(ky, ky), color=fg, fontsize=9)
            ax.tick_params(colors=fg, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(fg)
            ax.legend(fontsize=7, loc="best")

    @staticmethod
    def _extract_pareto_front(
        xs: List[float], ys: List[float],
    ) -> tuple:
        """2目的の非劣解（Pareto front）を抽出する。

        両目的を最小化する前提で、他のどの点にも両方の目的で
        支配されない点の集合を返す。
        """
        if not xs or not ys:
            return [], []
        import numpy as np
        points = np.array(list(zip(xs, ys)))
        # NaN除去
        valid = ~np.isnan(points).any(axis=1)
        points = points[valid]
        if len(points) == 0:
            return [], []

        pareto_mask = np.ones(len(points), dtype=bool)
        for i in range(len(points)):
            if not pareto_mask[i]:
                continue
            for j in range(len(points)):
                if i == j or not pareto_mask[j]:
                    continue
                # j が i を支配するか
                if (points[j] <= points[i]).all() and (points[j] < points[i]).any():
                    pareto_mask[i] = False
                    break

        pareto_pts = points[pareto_mask]
        return pareto_pts[:, 0].tolist(), pareto_pts[:, 1].tolist()
