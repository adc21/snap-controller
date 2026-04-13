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

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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

from itertools import combinations

from app.models import AnalysisCase
from app.models.performance_criteria import PerformanceCriteria
from app.services.optimizer import (
    DamperOptimizer,
    OptimizationCandidate,
    OptimizationConfig,
    OptimizationResult,
    ParameterRange,
    compute_convergence_diagnostics,
)
from app.services.snap_evaluator import create_snap_evaluator
from .theme import ThemeManager, MPL_STYLES
from .optimizer_dialog_actions import _OptimizerResultActionsMixin
# 後方互換の再エクスポート（テストやプラグインが直接importする場合に対応）
from .optimizer_analysis_dialogs import (  # noqa: F401
    ComparisonDialog,
    CorrelationDialog,
    DiagnosticsDialog,
    HeatmapDialog as _HeatmapDialog,
    ParetoDialog,
    SensitivityDialog,
    SobolDialog,
)
from app.services.optimizer import (  # noqa: F401
    ConvergenceDiagnostics,
    CorrelationResult,
    SensitivityResult,
    SobolResult,
    compute_correlation_analysis,
    compute_sensitivity,
    compute_sobol_sensitivity,
    export_optimization_log,
)

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


class _CandidateDetailDialog(QDialog):
    """探索候補の全詳細を表示するダイアログ。"""

    def __init__(
        self,
        cand: OptimizationCandidate,
        config: Optional[OptimizationConfig],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"候補詳細 — 反復 #{cand.iteration}")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        # パラメータ
        param_group = QGroupBox("パラメータ")
        param_layout = QFormLayout(param_group)
        for k, v in cand.params.items():
            param_layout.addRow(f"{k}:", QLabel(f"{v:.6g}"))
        layout.addWidget(param_group)

        # 目的関数
        obj_group = QGroupBox("目的関数")
        obj_layout = QFormLayout(obj_group)
        obj_key = config.objective_key if config else ""
        obj_layout.addRow("目的関数キー:", QLabel(obj_key or "—"))
        obj_layout.addRow("目的関数値:", QLabel(f"{cand.objective_value:.6g}"))
        verdict_label = QLabel("OK" if cand.is_feasible else "NG (制約違反)")
        verdict_label.setStyleSheet(
            f"color: {'#2ca02c' if cand.is_feasible else '#d62728'}; font-weight: bold;"
        )
        obj_layout.addRow("判定:", verdict_label)
        layout.addWidget(obj_group)

        # 全応答値
        resp_group = QGroupBox("応答値一覧")
        resp_layout = QFormLayout(resp_group)
        for k, v in cand.response_values.items():
            label = QLabel(f"{v:.6g}")
            if k == obj_key:
                label.setStyleSheet("font-weight: bold;")
            resp_layout.addRow(f"{k}:", label)
        if not cand.response_values:
            resp_layout.addRow(QLabel("（応答値なし）"))
        layout.addWidget(resp_group)

        # 制約マージン
        if cand.constraint_margins:
            margin_group = QGroupBox("制約マージン（正=余裕, 負=違反）")
            margin_layout = QFormLayout(margin_group)
            for k, v in cand.constraint_margins.items():
                margin_label = QLabel(f"{v:+.6g}")
                if v < 0:
                    margin_label.setStyleSheet("color: #d62728; font-weight: bold;")
                else:
                    margin_label.setStyleSheet("color: #2ca02c;")
                margin_layout.addRow(f"{k}:", margin_label)
            layout.addWidget(margin_group)

        # 閉じるボタン
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)


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


class _NumericTableItem(QTableWidgetItem):
    """数値ソート対応の QTableWidgetItem。

    テキスト表示はそのまま維持しつつ、ソートでは数値を使って比較します。
    数値にパースできない場合は +inf として末尾にソートされます。
    """

    def __init__(self, text: str, sort_value: float | None = None):
        super().__init__(text)
        if sort_value is not None:
            self._sort_value = sort_value
        else:
            try:
                self._sort_value = float(text)
            except (ValueError, TypeError):
                self._sort_value = float("inf")

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumericTableItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


class OptimizerDialog(_OptimizerResultActionsMixin, QDialog):
    """
    ダンパー最適化ダイアログ。

    最適化結果は result プロパティで取得できます。
    最良ケースの damper_params は best_params プロパティで取得できます。

    結果エクスポート・分析・設定プリセット操作は
    _OptimizerResultActionsMixin (optimizer_dialog_actions.py) に分離されています。
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
        self._opt_start_time: float = 0.0  # 最適化開始時刻 (time.time)
        self._avg_eval_sec: float = 30.0  # 1回あたりの平均評価時間（動的更新）

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
        self._build_guide_banner(layout)
        self._build_settings_section(layout)
        self._build_advanced_options(layout)
        self._build_run_controls(layout)
        self._build_summary_card(layout)
        self._build_result_section(layout)
        self._build_button_rows(layout)

    # -- UI sub-builders ------------------------------------------------

    def _build_guide_banner(self, layout: QVBoxLayout) -> None:
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

    def _build_settings_section(self, layout: QVBoxLayout) -> None:
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
        self._method_combo.addItem("LHS (ラテン超方格)", "lhs")
        self._method_combo.addItem("ベイズ最適化 (Bayesian)", "bayesian")
        self._method_combo.addItem("遺伝的アルゴリズム (GA)", "ga")
        self._method_combo.addItem("焼きなまし法 (SA)", "sa")
        self._method_combo.addItem("差分進化 (DE)", "de")
        self._method_combo.addItem("多目的最適化 (NSGA-II)", "nsga2")
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        row1.addWidget(self._method_combo)

        self._method_rec_btn = QPushButton("💡 おすすめ")
        self._method_rec_btn.setToolTip("パラメータ空間のサイズに基づいて最適な探索手法を推薦します")
        self._method_rec_btn.setFixedWidth(90)
        self._method_rec_btn.clicked.connect(self._show_method_recommendation)
        row1.addWidget(self._method_rec_btn)

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
        param_group_layout = QVBoxLayout(param_group)
        self._param_layout = QVBoxLayout()
        param_group_layout.addLayout(self._param_layout)
        self._on_damper_type_changed(self._damper_combo.currentText())

        # 設定保存/読込ボタン
        config_btn_row = QHBoxLayout()
        config_btn_row.addStretch()
        self._save_config_btn = QPushButton("設定保存")
        self._save_config_btn.setFixedWidth(80)
        self._save_config_btn.setToolTip(
            "現在のパラメータ範囲・目的関数・探索手法の設定を\n"
            "JSONファイルに保存します（プリセットとして再利用可能）"
        )
        self._save_config_btn.clicked.connect(self._save_config_preset)
        config_btn_row.addWidget(self._save_config_btn)
        self._load_config_btn = QPushButton("設定読込")
        self._load_config_btn.setFixedWidth(80)
        self._load_config_btn.setToolTip(
            "保存済みのパラメータ設定をJSONファイルから読み込みます"
        )
        self._load_config_btn.clicked.connect(self._load_config_preset)
        config_btn_row.addWidget(self._load_config_btn)
        param_group_layout.addLayout(config_btn_row)

        settings_layout.addWidget(param_group)

        layout.addWidget(settings_group)

    def _build_advanced_options(self, layout: QVBoxLayout) -> None:
        # ---- 推定試行数・時間インジケーター ----
        self._est_run_label = QLabel("")
        self._est_run_label.setStyleSheet("font-size: 11px; color: #1565c0; padding: 2px 4px;")
        layout.addWidget(self._est_run_label)

        # ---- ウォームスタート ----
        warm_row = QHBoxLayout()
        self._warm_start_cb = QCheckBox("ウォームスタート（前回結果を初期値に利用）")
        self._warm_start_cb.setToolTip(
            "保存済みの最適化結果を読み込み、その上位解を初期値として\n"
            "新しい最適化を開始します（ベイズ/GA/SAで有効）"
        )
        warm_row.addWidget(self._warm_start_cb)
        self._warm_start_path_label = QLabel("")
        self._warm_start_path_label.setStyleSheet("color: #666; font-size: 10px;")
        warm_row.addWidget(self._warm_start_path_label, stretch=1)
        self._warm_start_browse_btn = QPushButton("参照...")
        self._warm_start_browse_btn.setFixedWidth(60)
        self._warm_start_browse_btn.setEnabled(False)
        warm_row.addWidget(self._warm_start_browse_btn)
        layout.addLayout(warm_row)

        self._warm_start_candidates: List[OptimizationCandidate] = []

        # ---- 制約ペナルティ重み ----
        penalty_row = QHBoxLayout()
        self._penalty_cb = QCheckBox("制約ペナルティ法")
        self._penalty_cb.setToolTip(
            "制約違反に比例したペナルティを目的関数に加算し、\n"
            "制約境界付近の探索を改善します（GA/SA/ベイズで有効）"
        )
        penalty_row.addWidget(self._penalty_cb)
        penalty_row.addWidget(QLabel("ペナルティ重み:"))
        self._penalty_spin = QDoubleSpinBox()
        self._penalty_spin.setRange(0.1, 1000.0)
        self._penalty_spin.setValue(10.0)
        self._penalty_spin.setSingleStep(5.0)
        self._penalty_spin.setDecimals(1)
        self._penalty_spin.setEnabled(False)
        self._penalty_spin.setFixedWidth(80)
        penalty_row.addWidget(self._penalty_spin)
        penalty_row.addStretch()
        self._penalty_cb.toggled.connect(self._penalty_spin.setEnabled)
        layout.addLayout(penalty_row)

        # ---- ベイズ獲得関数 ----
        acq_row = QHBoxLayout()
        acq_row.addWidget(QLabel("獲得関数:"))
        self._acq_combo = QComboBox()
        self._acq_combo.addItem("Expected Improvement (EI)", "ei")
        self._acq_combo.addItem("Probability of Improvement (PI)", "pi")
        self._acq_combo.addItem("Upper Confidence Bound (UCB)", "ucb")
        self._acq_combo.setFixedWidth(260)
        self._acq_combo.setToolTip(
            "ベイズ最適化で使用する獲得関数を選択します。\n"
            "EI: 探索と利用のバランスが良い汎用的な選択（推奨）\n"
            "PI: 利用寄りで収束が速いが局所解に陥りやすい\n"
            "UCB: κ で探索度合いを直接制御。高次元で有効"
        )
        self._acq_combo.currentIndexChanged.connect(self._on_acq_changed)
        acq_row.addWidget(self._acq_combo)
        acq_row.addWidget(QLabel("κ:"))
        self._acq_kappa_spin = QDoubleSpinBox()
        self._acq_kappa_spin.setRange(0.1, 10.0)
        self._acq_kappa_spin.setValue(2.0)
        self._acq_kappa_spin.setSingleStep(0.5)
        self._acq_kappa_spin.setDecimals(1)
        self._acq_kappa_spin.setFixedWidth(60)
        self._acq_kappa_spin.setEnabled(False)
        self._acq_kappa_spin.setToolTip(
            "UCB の探索パラメータ κ。\n"
            "1.0: 利用寄り、2.0: バランス（推奨）、3.0: 探索寄り"
        )
        acq_row.addWidget(self._acq_kappa_spin)
        acq_row.addStretch()
        self._acq_row_widget = QWidget()
        self._acq_row_widget.setLayout(acq_row)
        self._acq_row_widget.setVisible(False)  # ベイズ選択時のみ表示
        layout.addWidget(self._acq_row_widget)

        # ---- GA適応的突然変異 ----
        ga_row = QHBoxLayout()
        self._ga_adaptive_cb = QCheckBox("適応的突然変異率")
        self._ga_adaptive_cb.setChecked(False)
        self._ga_adaptive_cb.setToolTip(
            "世代が進むにつれて突然変異率を線形減衰させ、\n"
            "序盤は探索（高突然変異率）・終盤は利用（低突然変異率）を重視します。\n"
            "交叉率も逆方向に増加させて終盤の局所精錬を促進します。"
        )
        ga_row.addWidget(self._ga_adaptive_cb)
        ga_row.addStretch()
        self._ga_row_widget = QWidget()
        self._ga_row_widget.setLayout(ga_row)
        self._ga_row_widget.setVisible(False)  # GA選択時のみ表示
        layout.addWidget(self._ga_row_widget)

        # ---- 乱数シード ----
        seed_row = QHBoxLayout()
        self._seed_check = QCheckBox("乱数シード:")
        self._seed_check.setChecked(False)
        self._seed_check.setToolTip(
            "整数を指定すると再現性のある結果を得られます。\n"
            "同じシードで同じ設定を実行すると同一の結果になります。\n"
            "構造設計のレビューや結果の再現性確認に有用です。"
        )
        seed_row.addWidget(self._seed_check)
        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 999999)
        self._seed_spin.setValue(42)
        self._seed_spin.setFixedWidth(80)
        self._seed_spin.setEnabled(False)
        seed_row.addWidget(self._seed_spin)
        seed_row.addStretch()
        self._seed_check.toggled.connect(self._seed_spin.setEnabled)
        seed_widget = QWidget()
        seed_widget.setLayout(seed_row)
        layout.addWidget(seed_widget)

        # ---- 並列評価 ----
        parallel_row = QHBoxLayout()
        parallel_row.addWidget(QLabel("並列評価数:"))
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setRange(1, 16)
        self._parallel_spin.setValue(1)
        self._parallel_spin.setFixedWidth(60)
        self._parallel_spin.setToolTip(
            "グリッドサーチ/ランダムサーチで複数候補を同時評価します。\n"
            "SNAP実行時は4〜8が目安です。モック評価では1で十分です。"
        )
        parallel_row.addWidget(self._parallel_spin)
        parallel_row.addWidget(QLabel("（SNAP解析を並列化。1=逐次）"))

        parallel_row.addWidget(QLabel("    タイムアウト:"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 3600)
        self._timeout_spin.setValue(300)
        self._timeout_spin.setSuffix(" 秒")
        self._timeout_spin.setSingleStep(30)
        self._timeout_spin.setFixedWidth(100)
        self._timeout_spin.setToolTip(
            "SNAP 1回実行あたりのタイムアウト（秒）。\n"
            "大規模モデルでは 600〜1200 に設定してください。\n"
            "デフォルト: 300秒（5分）"
        )
        parallel_row.addWidget(self._timeout_spin)

        parallel_row.addStretch()
        layout.addLayout(parallel_row)

        # ---- チェックポイント自動保存 ----
        checkpoint_row = QHBoxLayout()
        self._checkpoint_check = QCheckBox("チェックポイント自動保存")
        self._checkpoint_check.setChecked(False)
        self._checkpoint_check.setToolTip(
            "最適化中に一定間隔で中間結果をJSONファイルに自動保存します。\n"
            "アプリクラッシュ時のデータ損失を防ぎます。"
        )
        checkpoint_row.addWidget(self._checkpoint_check)
        checkpoint_row.addWidget(QLabel("間隔:"))
        self._checkpoint_interval_spin = QSpinBox()
        self._checkpoint_interval_spin.setRange(5, 1000)
        self._checkpoint_interval_spin.setValue(10)
        self._checkpoint_interval_spin.setFixedWidth(60)
        self._checkpoint_interval_spin.setSuffix(" 回")
        checkpoint_row.addWidget(self._checkpoint_interval_spin)
        checkpoint_row.addStretch()
        layout.addLayout(checkpoint_row)

        # ---- ロバスト最適化 ----
        robust_row = QHBoxLayout()
        self._robust_check = QCheckBox("ロバスト最適化")
        self._robust_check.setChecked(False)
        self._robust_check.setToolTip(
            "各候補をパラメータ摂動付きで複数回評価し、最悪ケースで最適化します。\n"
            "製造誤差やモデル不確実性に対して頑健な設計解を見つけます。\n"
            "評価回数は (1+サンプル数) 倍になるため計算時間が増加します。"
        )
        robust_row.addWidget(self._robust_check)
        robust_row.addWidget(QLabel("サンプル数:"))
        self._robust_samples_spin = QSpinBox()
        self._robust_samples_spin.setRange(1, 20)
        self._robust_samples_spin.setValue(3)
        self._robust_samples_spin.setFixedWidth(50)
        robust_row.addWidget(self._robust_samples_spin)
        robust_row.addWidget(QLabel("摂動幅:"))
        self._robust_delta_spin = QDoubleSpinBox()
        self._robust_delta_spin.setRange(0.01, 0.30)
        self._robust_delta_spin.setValue(0.05)
        self._robust_delta_spin.setSingleStep(0.01)
        self._robust_delta_spin.setSuffix(" (5%)")
        self._robust_delta_spin.setFixedWidth(90)
        self._robust_delta_spin.setDecimals(2)
        robust_row.addWidget(self._robust_delta_spin)
        robust_row.addStretch()
        layout.addLayout(robust_row)

        # ---- コスト重み付き最適化 ----
        cost_row = QHBoxLayout()
        self._cost_check = QCheckBox("コスト重み付き")
        self._cost_check.setChecked(False)
        self._cost_check.setToolTip(
            "ダンパーパラメータにコスト係数を設定し、\n"
            "応答最小化とコスト最小化を同時に考慮します。\n"
            "目的関数 = 応答値 + コスト重み × Σ(係数×パラメータ値)"
        )
        cost_row.addWidget(self._cost_check)
        cost_row.addWidget(QLabel("重み:"))
        self._cost_weight_spin = QDoubleSpinBox()
        self._cost_weight_spin.setRange(0.0001, 10.0)
        self._cost_weight_spin.setValue(0.01)
        self._cost_weight_spin.setSingleStep(0.001)
        self._cost_weight_spin.setDecimals(4)
        self._cost_weight_spin.setFixedWidth(90)
        self._cost_weight_spin.setEnabled(False)
        self._cost_weight_spin.setToolTip(
            "応答値に対するコスト項の重み。\n"
            "小さい値ほど応答優先、大きい値ほどコスト優先。"
        )
        cost_row.addWidget(self._cost_weight_spin)
        self._cost_edit_btn = QPushButton("係数設定...")
        self._cost_edit_btn.setFixedWidth(90)
        self._cost_edit_btn.setEnabled(False)
        self._cost_edit_btn.setToolTip("各パラメータのコスト係数を設定します")
        self._cost_edit_btn.clicked.connect(self._edit_cost_coefficients)
        cost_row.addWidget(self._cost_edit_btn)
        self._cost_label = QLabel("")
        self._cost_label.setStyleSheet("color: gray; font-size: 11px;")
        cost_row.addWidget(self._cost_label)
        cost_row.addStretch()
        self._cost_check.toggled.connect(self._cost_weight_spin.setEnabled)
        self._cost_check.toggled.connect(self._cost_edit_btn.setEnabled)
        self._cost_coefficients: Dict[str, float] = {}
        layout.addLayout(cost_row)

        # ---- 多波エンベロープ最適化 ----
        envelope_row = QHBoxLayout()
        self._envelope_check = QCheckBox("多波エンベロープ")
        self._envelope_check.setChecked(False)
        self._envelope_check.setToolTip(
            "複数の地震波で同時に評価し、全波形の最大応答（エンベロープ）\n"
            "を最小化します。全波形で制約を満足する解を探索します。\n"
            "評価回数は波数倍になるため計算時間が増加します。"
        )
        envelope_row.addWidget(self._envelope_check)
        self._envelope_mode_combo = QComboBox()
        self._envelope_mode_combo.addItem("最大値（保守側）", "max")
        self._envelope_mode_combo.addItem("平均値", "mean")
        self._envelope_mode_combo.setFixedWidth(120)
        self._envelope_mode_combo.setEnabled(False)
        envelope_row.addWidget(self._envelope_mode_combo)
        self._envelope_info_label = QLabel("(波形未設定)")
        self._envelope_info_label.setStyleSheet("color: gray; font-size: 11px;")
        envelope_row.addWidget(self._envelope_info_label)
        envelope_row.addStretch()
        self._envelope_check.toggled.connect(self._envelope_mode_combo.setEnabled)
        self._envelope_wave_cases: List[Any] = []  # list of AnalysisCase
        layout.addLayout(envelope_row)

    def _build_run_controls(self, layout: QVBoxLayout) -> None:
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

    def _build_summary_card(self, layout: QVBoxLayout) -> None:
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

        self._best_summary_card.hide()
        layout.addWidget(self._best_summary_card)

    def _build_result_section(self, layout: QVBoxLayout) -> None:
        result_splitter = QSplitter(Qt.Horizontal)

        # 結果テーブル（左）
        table_group = QGroupBox("探索結果 (上位20)")
        table_layout = QVBoxLayout(table_group)

        self._result_table = QTableWidget(0, 6)
        self._result_table.setHorizontalHeaderLabels([
            "順位", "パラメータ", "目的関数値", "判定", "最小マージン", "詳細"
        ])
        self._result_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._result_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.Stretch
        )
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setSortingEnabled(True)
        self._result_table.horizontalHeader().setSortIndicatorShown(True)
        table_layout.addWidget(self._result_table)

        self._result_summary = QLabel("")
        table_layout.addWidget(self._result_summary)

        result_splitter.addWidget(table_group)

        # 収束グラフ（右）
        chart_group = QGroupBox("収束グラフ")
        chart_layout = QVBoxLayout(chart_group)
        self._conv_canvas = _ConvergenceCanvas(self)
        chart_layout.addWidget(self._conv_canvas)
        self._save_plot_btn = QPushButton("画像保存")
        self._save_plot_btn.setEnabled(False)
        self._save_plot_btn.setToolTip(
            "収束グラフをPNG/SVG画像として保存します"
        )
        chart_layout.addWidget(self._save_plot_btn)
        result_splitter.addWidget(chart_group)

        result_splitter.setStretchFactor(0, 2)
        result_splitter.setStretchFactor(1, 1)
        layout.addWidget(result_splitter, stretch=1)

    def _build_button_rows(self, layout: QVBoxLayout) -> None:
        analysis_row = QHBoxLayout()
        analysis_label = QLabel("分析:")
        analysis_label.setStyleSheet("font-weight: bold; color: #888;")
        analysis_row.addWidget(analysis_label)

        self._sensitivity_btn = QPushButton("感度解析")
        self._sensitivity_btn.setEnabled(False)
        self._sensitivity_btn.setToolTip(
            "最適解周りのパラメータ感度を解析します（各パラメータを±20%変動）"
        )
        analysis_row.addWidget(self._sensitivity_btn)

        self._sobol_btn = QPushButton("Sobol解析")
        self._sobol_btn.setEnabled(False)
        self._sobol_btn.setToolTip(
            "Sobol分散ベースグローバル感度解析（交互作用を含む一次/全次指標）"
        )
        analysis_row.addWidget(self._sobol_btn)

        self._pareto_btn = QPushButton("Pareto Front")
        self._pareto_btn.setEnabled(False)
        self._pareto_btn.setToolTip(
            "複合目的関数使用時のトレードオフ曲線を表示します"
        )
        analysis_row.addWidget(self._pareto_btn)

        self._correlation_btn = QPushButton("相関分析")
        self._correlation_btn.setEnabled(False)
        self._correlation_btn.setToolTip(
            "上位候補のパラメータ間の相関を分析します（相関行列ヒートマップ）"
        )
        analysis_row.addWidget(self._correlation_btn)

        self._diagnostics_btn = QPushButton("収束診断")
        self._diagnostics_btn.setEnabled(False)
        self._diagnostics_btn.setToolTip(
            "探索の品質を診断し、再実行の必要性や推奨アクションを表示します"
        )
        analysis_row.addWidget(self._diagnostics_btn)

        self._heatmap_btn = QPushButton("空間ヒートマップ")
        self._heatmap_btn.setEnabled(False)
        self._heatmap_btn.setToolTip(
            "パラメータ空間の探索密度と目的関数値を2Dヒートマップで可視化します"
        )
        analysis_row.addWidget(self._heatmap_btn)
        analysis_row.addStretch()
        layout.addLayout(analysis_row)

        # 行2: 出力・保存
        export_row = QHBoxLayout()
        export_label = QLabel("出力:")
        export_label.setStyleSheet("font-weight: bold; color: #888;")
        export_row.addWidget(export_label)

        self._export_csv_btn = QPushButton("CSV出力")
        self._export_csv_btn.setEnabled(False)
        self._export_csv_btn.setToolTip("探索結果をCSVファイルに出力します")
        export_row.addWidget(self._export_csv_btn)

        self._log_export_btn = QPushButton("評価ログ")
        self._log_export_btn.setEnabled(False)
        self._log_export_btn.setToolTip(
            "全評価履歴をCSVログとして出力します（審査・規制文書用）"
        )
        export_row.addWidget(self._log_export_btn)

        self._report_btn = QPushButton("HTMLレポート")
        self._report_btn.setEnabled(False)
        self._report_btn.setToolTip(
            "最適化結果をHTMLレポートとして出力します（設定・最良解・収束グラフ含む）"
        )
        export_row.addWidget(self._report_btn)

        self._save_btn = QPushButton("結果保存")
        self._save_btn.setEnabled(False)
        self._save_btn.setToolTip("最適化結果をJSONファイルに保存します")
        export_row.addWidget(self._save_btn)

        self._load_btn = QPushButton("結果読込")
        self._load_btn.setToolTip("保存済みの最適化結果をJSONファイルから読み込みます")
        export_row.addWidget(self._load_btn)

        self._compare_btn = QPushButton("結果比較")
        self._compare_btn.setToolTip(
            "複数の最適化結果JSONを読み込み、パラメータ・収束曲線を比較します"
        )
        export_row.addWidget(self._compare_btn)

        self._copy_params_btn = QPushButton("最良パラメータコピー")
        self._copy_params_btn.setEnabled(False)
        self._copy_params_btn.setToolTip(
            "最良解のパラメータ値をクリップボードにコピーします"
        )
        export_row.addWidget(self._copy_params_btn)
        export_row.addStretch()
        layout.addLayout(export_row)

        # 行3: アクション
        action_row = QHBoxLayout()
        self._apply_btn = QPushButton("最良解を .s8i に適用")
        self._apply_btn.setEnabled(False)
        action_row.addWidget(self._apply_btn)
        action_row.addStretch()

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)

    def _connect_signals(self) -> None:
        self._run_btn.clicked.connect(self._start_optimization)
        self._cancel_btn.clicked.connect(self._cancel_optimization)
        self._apply_btn.clicked.connect(self._apply_best)
        self._export_csv_btn.clicked.connect(self._export_csv)
        self._sensitivity_btn.clicked.connect(self._run_sensitivity)
        self._sobol_btn.clicked.connect(self._run_sobol)
        self._pareto_btn.clicked.connect(self._show_pareto)
        self._save_btn.clicked.connect(self._save_result_json)
        self._load_btn.clicked.connect(self._load_result_json)
        self._warm_start_cb.toggled.connect(self._warm_start_browse_btn.setEnabled)
        self._warm_start_browse_btn.clicked.connect(self._browse_warm_start)
        self._compare_btn.clicked.connect(self._show_comparison)
        self._correlation_btn.clicked.connect(self._show_correlation)
        self._log_export_btn.clicked.connect(self._export_log)
        self._report_btn.clicked.connect(self._export_html_report)
        self._diagnostics_btn.clicked.connect(self._show_diagnostics)
        self._heatmap_btn.clicked.connect(self._show_heatmap)
        self._copy_params_btn.clicked.connect(self._copy_best_params)
        self._save_plot_btn.clicked.connect(self._save_convergence_plot)
        self._result_table.cellDoubleClicked.connect(self._show_candidate_detail)
        self._optimizer.progress.connect(self._on_progress)
        self._optimizer.candidate_found.connect(self._on_candidate)
        self._optimizer.optimization_finished.connect(self._on_finished)
        self._optimizer.checkpoint.connect(self._on_checkpoint)
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
        elif method == "nsga2":
            n_runs = self._iter_spin.value() if hasattr(self, "_iter_spin") else 200
            method_label = "NSGA-II (多目的)"
        else:
            n_runs = self._iter_spin.value() if hasattr(self, "_iter_spin") else 200
            method_label = "ランダム/ベイズ"

        # 実績値ベースの動的推定（初期値30秒、実行後は実測値を使用）
        per_eval = getattr(self, "_avg_eval_sec", 30.0)
        est_sec = int(n_runs * per_eval)
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

        # 推奨手法ヒント
        rec_method, _, _ = self._recommend_method()
        current_method = self._method_combo.currentData() if hasattr(self, "_method_combo") else "grid"
        rec_hint = ""
        if current_method != rec_method:
            for i in range(self._method_combo.count()):
                if self._method_combo.itemData(i) == rec_method:
                    rec_hint = f"  💡 推奨: {self._method_combo.itemText(i)}"
                    break

        self._est_run_label.setText(
            f"{icon} 推定 <b>{n_runs}</b> 回の解析を実行します"
            f"（{method_label} | 所要時間目安: {time_str}）{rec_hint}"
        )
        self._est_run_label.setStyleSheet(
            f"font-size: 11px; color: {color}; padding: 2px 4px;"
        )
        self._est_run_label.setTextFormat(
            __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.RichText
        )

    def _validate_param_ranges(self, _value: float = 0.0) -> None:
        """パラメータ範囲のリアルタイムバリデーション。

        min>=maxやstep問題をスピンボックスの背景色で即座にフィードバックします。
        """
        if not hasattr(self, "_param_widgets"):
            return
        method = (self._method_combo.currentData()
                  if hasattr(self, "_method_combo") else "grid")
        _ERR = "background-color: #ffcccc;"
        _OK = ""
        for w in self._param_widgets:
            try:
                mn = w["min"]
                mx = w["max"]
                st = w["step"]
                min_v = mn.value()
                max_v = mx.value()
                step_v = st.value()
                # min >= max チェック
                range_bad = min_v >= max_v
                mn.setStyleSheet(_ERR if range_bad else _OK)
                mx.setStyleSheet(_ERR if range_bad else _OK)
                # step チェック（グリッドサーチ時のみ）
                if method == "grid" and not range_bad:
                    step_bad = step_v <= 0 or step_v > (max_v - min_v)
                    st.setStyleSheet(_ERR if step_bad else _OK)
                else:
                    st.setStyleSheet(_OK)
            except (AttributeError, TypeError):
                pass

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
            # AO-1: リアルタイムバリデーション
            min_spin.valueChanged.connect(self._validate_param_ranges)
            max_spin.valueChanged.connect(self._validate_param_ranges)
            step_spin.valueChanged.connect(self._validate_param_ranges)

        # 初回ラベル更新
        self._update_est_run_label()
        self._validate_param_ranges()

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
        self._iter_spin.setEnabled(method in ("random", "lhs", "bayesian", "ga", "sa", "de", "nsga2"))
        self._acq_row_widget.setVisible(method == "bayesian")
        self._ga_row_widget.setVisible(method == "ga")
        self._validate_param_ranges()

    def _on_acq_changed(self, index: int) -> None:
        self._acq_kappa_spin.setEnabled(self._acq_combo.currentData() == "ucb")

    def _recommend_method(self) -> tuple[str, str, str]:
        """パラメータ空間に基づいて推奨手法を決定します。

        Returns
        -------
        tuple[str, str, str]
            (推奨手法data値, 推奨理由テキスト, 推奨反復数テキスト)
        """
        n_params = len(self._param_widgets)
        grid_runs = self._estimate_grid_runs()

        if n_params == 0:
            return "grid", "パラメータが未設定です。", ""

        # 複合目的関数が有効な場合は NSGA-II を推奨
        is_composite = (
            hasattr(self, "_composite_check")
            and self._composite_check.isChecked()
        )
        if is_composite:
            n_active = sum(
                1 for w in self._weight_spins if w["spin"].value() > 0
            )
            if n_active >= 2:
                rec_iter = min(max(n_params * 80, 200), 500)
                return (
                    "nsga2",
                    f"複合目的関数（{n_active}目的）が設定されています。"
                    "NSGA-II は真の多目的最適化アルゴリズムで、"
                    "パレートフロント（トレードオフ曲線）を直接求めます。"
                    "重み付き和の単一解ではなく、設計者が複数の最適解から選べます。",
                    f"推奨反復数: {rec_iter}回",
                )

        if grid_runs <= 50:
            return (
                "grid",
                f"パラメータ空間が小さい（{grid_runs}通り）ため、"
                "全探索のグリッドサーチが最適です。最適解の見落としがありません。",
                "",
            )
        elif grid_runs <= 500:
            return (
                "bayesian",
                f"パラメータ空間が中規模（グリッド{grid_runs}通り）です。"
                "ベイズ最適化は少ない試行数で有望な領域を集中探索でき、効率的です。"
                "空間充填が目的ならLHS（ラテン超方格）も選択肢です。",
                f"推奨反復数: {min(grid_runs // 2, 200)}回",
            )
        elif n_params <= 3:
            return (
                "bayesian",
                f"パラメータ空間が大きい（グリッド{grid_runs}通り）ですが、"
                f"パラメータ数が{n_params}個と少ないため、"
                "ベイズ最適化のガウス過程モデルが効果的に機能します。",
                f"推奨反復数: {min(grid_runs // 3, 300)}回",
            )
        else:
            return (
                "de",
                f"パラメータ数が多く（{n_params}個）、"
                f"探索空間が非常に大きい（グリッド{grid_runs}通り）ため、"
                "差分進化(DE)が適しています。"
                "連続パラメータに強く、GAより少ないチューニングで安定した性能を発揮します。"
                "離散パラメータが多い場合はGA も選択肢です。",
                f"推奨反復数: {min(max(n_params * 50, 200), 500)}回",
            )

    def _show_method_recommendation(self) -> None:
        """パラメータ空間に基づく手法推奨をダイアログで表示し、適用を提案します。"""
        rec_method, reason, iter_hint = self._recommend_method()

        # 現在選択中の手法名
        current_method = self._method_combo.currentData()
        current_label = self._method_combo.currentText()

        # 推奨手法名を取得
        rec_label = ""
        rec_index = 0
        for i in range(self._method_combo.count()):
            if self._method_combo.itemData(i) == rec_method:
                rec_label = self._method_combo.itemText(i)
                rec_index = i
                break

        msg = f"<b>推奨手法: {rec_label}</b><br><br>{reason}"
        if iter_hint:
            msg += f"<br><br>{iter_hint}"

        if current_method == rec_method:
            msg += "<br><br>✅ 現在の選択は推奨手法と一致しています。"
            QMessageBox.information(self, "手法推奨", msg)
        else:
            msg += f"<br><br>現在の選択「{current_label}」から変更しますか？"
            reply = QMessageBox.question(
                self, "手法推奨", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._method_combo.setCurrentIndex(rec_index)
                # 推奨反復数も適用
                if iter_hint and rec_method != "grid":
                    import re
                    m = re.search(r"(\d+)回", iter_hint)
                    if m:
                        self._iter_spin.setValue(int(m.group(1)))

    # ------------------------------------------------------------------
    # Convergence stagnation detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_stagnation(
        candidates: list,
        window_ratio: float = 0.15,
        min_window: int = 10,
    ) -> dict | None:
        """収束履歴から停滞区間を検出します。

        Parameters
        ----------
        candidates : list[OptimizationCandidate]
            全候補リスト（評価順）。
        window_ratio : float
            停滞判定ウィンドウ（全体の割合）。
        min_window : int
            最小ウィンドウサイズ。

        Returns
        -------
        dict | None
            停滞が検出された場合は情報辞書、なければ None。
            keys: stagnation_start, stagnation_length, best_at_stagnation,
                  total_evals, improvement_pct
        """
        feasible_vals = [
            c.objective_value for c in candidates
            if c.is_feasible and c.objective_value != float("inf")
        ]
        if len(feasible_vals) < min_window * 2:
            return None

        # 累積最小値を計算
        best_so_far = []
        current_best = float("inf")
        for v in feasible_vals:
            current_best = min(current_best, v)
            best_so_far.append(current_best)

        window = max(min_window, int(len(feasible_vals) * window_ratio))
        total = len(feasible_vals)

        # 末尾window区間で改善がなかったか
        if total <= window:
            return None

        tail_start = total - window
        tail_best = best_so_far[-1]
        pre_tail_best = best_so_far[tail_start]

        # 改善率
        if pre_tail_best == 0:
            return None
        improvement = abs(pre_tail_best - tail_best) / abs(pre_tail_best)

        if improvement < 1e-4:  # 0.01%未満の改善 = 停滞
            # 停滞開始点を遡って特定
            stag_start = tail_start
            for i in range(tail_start, 0, -1):
                if abs(best_so_far[i] - tail_best) / max(abs(tail_best), 1e-12) > 1e-4:
                    stag_start = i + 1
                    break
            else:
                stag_start = 0

            return {
                "stagnation_start": stag_start,
                "stagnation_length": total - stag_start,
                "best_at_stagnation": tail_best,
                "total_evals": total,
                "improvement_pct": improvement * 100,
            }

        return None

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

        # ウォームスタート候補
        warm = (
            list(self._warm_start_candidates)
            if self._warm_start_cb.isChecked() and self._warm_start_candidates
            else []
        )

        # 制約ペナルティ重み
        penalty_weight = (
            self._penalty_spin.value()
            if self._penalty_cb.isChecked()
            else 0.0
        )

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
            warm_start_candidates=warm,
            constraint_penalty_weight=penalty_weight,
            n_parallel=self._parallel_spin.value(),
            checkpoint_interval=(
                self._checkpoint_interval_spin.value()
                if self._checkpoint_check.isChecked()
                else 0
            ),
            robustness_samples=(
                self._robust_samples_spin.value()
                if self._robust_check.isChecked()
                else 0
            ),
            robustness_delta=(
                self._robust_delta_spin.value()
                if self._robust_check.isChecked()
                else 0.05
            ),
            cost_coefficients=(
                dict(self._cost_coefficients)
                if self._cost_check.isChecked() and self._cost_coefficients
                else {}
            ),
            cost_weight=(
                self._cost_weight_spin.value()
                if self._cost_check.isChecked()
                else 0.0
            ),
            envelope_mode=(
                self._envelope_mode_combo.currentData()
                if self._envelope_check.isChecked()
                else ""
            ),
            envelope_wave_names=[
                getattr(c, "name", f"wave_{i}")
                for i, c in enumerate(self._envelope_wave_cases)
            ] if self._envelope_check.isChecked() else [],
            acquisition_function=self._acq_combo.currentData(),
            acquisition_kappa=self._acq_kappa_spin.value(),
            ga_adaptive_mutation=self._ga_adaptive_cb.isChecked(),
            random_seed=(
                self._seed_spin.value()
                if self._seed_check.isChecked()
                else None
            ),
            snap_timeout=self._timeout_spin.value(),
        )

    def _edit_cost_coefficients(self) -> None:
        """各パラメータのコスト係数を設定するダイアログを表示。"""
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("コスト係数の設定")
        dlg.setMinimumWidth(300)
        form = QFormLayout(dlg)

        spins: Dict[str, QDoubleSpinBox] = {}
        for w in self._param_widgets:
            key = w["key"]
            label = w["pr_label"]
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 10000.0)
            spin.setDecimals(4)
            spin.setSingleStep(0.01)
            spin.setValue(self._cost_coefficients.get(key, 0.0))
            form.addRow(f"{label} ({key}):", spin)
            spins[key] = spin

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._cost_coefficients = {k: s.value() for k, s in spins.items() if s.value() > 0}
            n = len(self._cost_coefficients)
            self._cost_label.setText(f"{n}パラメータに係数設定済み" if n > 0 else "係数未設定")

    def set_envelope_cases(self, cases: list) -> None:
        """多波エンベロープ用の解析ケースリストを設定する。

        Parameters
        ----------
        cases : list of AnalysisCase
            各地震波に対応する解析ケースリスト。
        """
        self._envelope_wave_cases = list(cases)
        n = len(cases)
        if n > 0:
            names = [getattr(c, "name", f"wave_{i}") for i, c in enumerate(cases)]
            self._envelope_info_label.setText(f"({n}波: {', '.join(names[:3])}{'...' if n > 3 else ''})")
        else:
            self._envelope_info_label.setText("(波形未設定)")

    def _start_optimization(self) -> None:
        config = self._build_config()

        if not self._validate_config(config):
            return

        # D-3: 大量試行時の事前警告ダイアログ
        if not self._confirm_large_run(config):
            return

        self._reset_ui_for_optimization()

        evaluate_fn = self._create_evaluate_fn(config)

        self._optimizer.optimize(config, evaluate_fn=evaluate_fn)

    def _validate_config(self, config) -> bool:
        """パラメータ範囲と目的関数重みのバリデーション。Falseで中止。"""
        if not config.parameters:
            QMessageBox.warning(self, "設定エラー", "探索パラメータが設定されていません。")
            return False

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
            return False

        if config.objective_weights:
            total_weight = sum(config.objective_weights.values())
            if total_weight <= 0:
                QMessageBox.warning(
                    self, "設定エラー",
                    "複合目的関数の重みの合計が0です。\n"
                    "少なくとも1つの目的関数に正の重みを設定してください。",
                )
                return False

        return True

    def _confirm_large_run(self, config) -> bool:
        """試行数が多い場合に確認ダイアログを表示。Falseで中止。"""
        n_runs = (self._estimate_grid_runs()
                  if config.method == "grid"
                  else self._iter_spin.value())
        if n_runs <= 50:
            return True

        per_eval = getattr(self, "_avg_eval_sec", 30.0)
        est_sec = int(n_runs * per_eval)
        if est_sec < 3600:
            time_str = f"約 {est_sec // 60} 分"
        else:
            h = est_sec // 3600
            m = (est_sec % 3600) // 60
            time_str = f"約 {h} 時間 {m} 分"

        basis = (f"1回あたり{per_eval:.0f}秒（実測値）"
                 if per_eval != 30.0
                 else "1回あたり30秒と仮定")
        reply = QMessageBox.question(
            self, "計算時間の確認",
            f"推定 {n_runs} 回の解析を実行します。\n"
            f"所要時間の目安: {time_str}（{basis}）\n\n"
            f"続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _reset_ui_for_optimization(self) -> None:
        """最適化開始前のUI状態リセット。"""
        self._result_table.setRowCount(0)
        self._convergence_history.clear()
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._apply_btn.setEnabled(False)
        self._export_csv_btn.setEnabled(False)
        self._sensitivity_btn.setEnabled(False)
        self._sobol_btn.setEnabled(False)
        self._pareto_btn.setEnabled(False)
        self._correlation_btn.setEnabled(False)
        self._log_export_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._report_btn.setEnabled(False)
        self._diagnostics_btn.setEnabled(False)
        self._heatmap_btn.setEnabled(False)
        self._copy_params_btn.setEnabled(False)
        self._save_plot_btn.setEnabled(False)
        self._progress_bar.show()
        self._progress_bar.setValue(0)
        self._opt_start_time = time.time()

        self._conv_canvas.ax.clear()
        self._conv_canvas.ax.text(
            0.5, 0.5, "最適化を実行中...",
            ha="center", va="center",
            transform=self._conv_canvas.ax.transAxes,
            fontsize=10, color="gray"
        )
        self._conv_canvas.draw()

    def _create_evaluate_fn(self, config):
        """SNAP評価関数を構築する。利用不可時はNone（モック評価）を返す。"""
        if not (self._base_case and self._snap_exe_path):
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
            return None

        evaluate_fn = None

        # 多波エンベロープモード
        if config.envelope_mode and self._envelope_wave_cases:
            from app.services.snap_evaluator import MultiWaveEvaluator
            evaluators = []
            for i, wave_case in enumerate(self._envelope_wave_cases):
                wave_name = getattr(wave_case, "name", f"wave_{i}")
                ev = create_snap_evaluator(
                    snap_exe_path=self._snap_exe_path,
                    base_case=wave_case,
                    param_ranges=config.parameters,
                    log_callback=lambda msg: self._result_summary.setText(msg),
                    snap_work_dir=self._snap_work_dir,
                    timeout=config.snap_timeout,
                )
                if ev:
                    evaluators.append((wave_name, ev))
            if evaluators:
                evaluate_fn = MultiWaveEvaluator(
                    evaluators=evaluators,
                    aggregation=config.envelope_mode,
                    log_callback=lambda msg: self._result_summary.setText(msg),
                )
                n_waves = len(evaluators)
                self._result_summary.setText(
                    f"多波SNAP実行モード（{n_waves}波, {config.envelope_mode}）で最適化を実行中..."
                )

        # 単一波モード（エンベロープが無効 or 構築失敗時）
        if evaluate_fn is None:
            snap_evaluator = create_snap_evaluator(
                snap_exe_path=self._snap_exe_path,
                base_case=self._base_case,
                param_ranges=config.parameters,
                log_callback=lambda msg: self._result_summary.setText(msg),
                snap_work_dir=self._snap_work_dir,
                timeout=config.snap_timeout,
            )
            if snap_evaluator:
                evaluate_fn = snap_evaluator
                self._result_summary.setText(
                    "SNAP実行モードで最適化を実行中..."
                )

        if evaluate_fn is None:
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

        return evaluate_fn

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
        # ETA計算 + 動的評価時間学習
        eta_str = ""
        if current > 0 and total > 0 and self._opt_start_time > 0:
            elapsed = time.time() - self._opt_start_time
            self._avg_eval_sec = elapsed / current
            remaining = max(current, 1)
            eta_sec = elapsed / remaining * (total - current)
            elapsed_str = self._format_duration(elapsed)
            if current < total:
                eta_str = f" | 経過 {elapsed_str}, 残り {self._format_duration(eta_sec)}"
            else:
                eta_str = f" | 経過 {elapsed_str}"
        self._progress_label.setText(message + eta_str)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """秒数を人間が読みやすい時間文字列に変換します。"""
        s = int(seconds)
        if s < 60:
            return f"{s}秒"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}分{s}秒"
        h, m = divmod(m, 60)
        return f"{h}時間{m}分"

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

        # 結果テーブル・収束グラフ・サマリーカードを更新
        self._populate_result_table(result)
        self._draw_convergence(result)
        self._update_best_summary_card(result)

        # サマリーテキスト + ボタン有効化
        stagnation = self._detect_stagnation(result.all_candidates)
        self._result_summary.setText(
            self._build_result_summary_text(result, stagnation)
        )
        self._enable_result_buttons(result)

    def _build_result_summary_text(
        self,
        result: OptimizationResult,
        stagnation: Optional[dict],
    ) -> str:
        """最適化完了後のサマリーテキストを構築する。"""
        if not result.best:
            return (
                "制約を満たす解が見つかりませんでした。"
                "パラメータ範囲や制約条件を見直してください。"
            )
        obj_label = result.config.objective_label if result.config else "目的関数"
        eval_tag = "[SNAP]" if result.evaluation_method == "snap" else "[モック]"
        text = (
            f"{eval_tag} 最良解: {obj_label} = {result.best.objective_value:.6g}  |  "
            f"制約満足: {len(result.feasible_candidates)} / {len(result.all_candidates)} 点"
        )
        if result.evaluator_stats:
            cache_hits = result.evaluator_stats.get("cache_hits", 0)
            if cache_hits > 0:
                text += f"  |  キャッシュ: {cache_hits}hit"
        if result.robustness_stats:
            rate = result.robustness_stats.get("success_rate", 1.0) * 100
            text += f"  |  ロバスト摂動: {rate:.0f}%成功"
            if rate < 80:
                text += " ⚠ 信頼性低"
        if stagnation:
            stag_pct = stagnation["stagnation_length"] / stagnation["total_evals"] * 100
            text += (
                f"  |  ⚠ 停滞検出: 最後の{stagnation['stagnation_length']}回"
                f"（{stag_pct:.0f}%）で改善なし"
            )
        return text

    def _enable_result_buttons(self, result: OptimizationResult) -> None:
        """最適化結果に応じて分析・出力ボタンを有効化する。"""
        has_best = result.best is not None
        has_candidates = len(result.all_candidates) > 0
        multi_param = (
            result.config is not None
            and len(result.config.parameters) >= 2
            and len(result.all_candidates) >= 3
        )

        if has_best:
            self._apply_btn.setEnabled(True)
            self._sensitivity_btn.setEnabled(True)
            self._sobol_btn.setEnabled(True)
            self._copy_params_btn.setEnabled(True)

        if has_best or has_candidates:
            self._export_csv_btn.setEnabled(True)
            self._save_btn.setEnabled(True)
            self._report_btn.setEnabled(True)
            self._log_export_btn.setEnabled(True)
            self._diagnostics_btn.setEnabled(True)
            self._save_plot_btn.setEnabled(True)

        if multi_param:
            self._heatmap_btn.setEnabled(True)
            self._correlation_btn.setEnabled(True)

        if has_best and result.config and result.config.objective_weights:
            self._pareto_btn.setEnabled(True)

    def _on_checkpoint(self, intermediate: "OptimizationResult") -> None:
        """チェックポイントシグナルを受けて中間結果を自動保存する。"""
        if not self._checkpoint_check.isChecked():
            return
        try:
            import tempfile
            import os
            checkpoint_dir = os.path.join(tempfile.gettempdir(), "snap_optimizer_checkpoints")
            os.makedirs(checkpoint_dir, exist_ok=True)
            path = os.path.join(checkpoint_dir, "checkpoint_latest.json")
            intermediate.save_json(path)
            n = len(intermediate.all_candidates)
            logger.info("チェックポイント保存: %d点評価済み → %s", n, path)
        except Exception as e:
            logger.warning("チェックポイント保存失敗: %s", e)

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
            # 制約を満たす解なし: 赤カードで明確に警告 + 最も惜しい解を表示
            self._best_summary_card.setStyleSheet(
                "QFrame {"
                "  background-color: #ffebee;"
                "  border: 1px solid #ef5350;"
                "  border-left: 5px solid #c62828;"
                "  border-radius: 4px;"
                "}"
            )
            self._bc_title_lbl.setText(
                "<b>制約を満たす解が見つかりませんでした</b>"
            )
            self._bc_title_lbl.setStyleSheet(
                "color: #b71c1c; font-size: 12px; background: transparent; border: none;"
            )

            # 最も惜しい解（least infeasible）を表示
            least_inf = result.least_infeasible
            if least_inf and least_inf.objective_value < float("inf"):
                param_strs = [f"{k}={v:.4g}" for k, v in least_inf.params.items()]
                margin_strs = []
                for mk, mv in least_inf.constraint_margins.items():
                    if mv < 0 and mv > float("-inf"):
                        margin_strs.append(f"{mk}: {mv:+.4g}")
                detail = "  /  ".join(param_strs)
                if margin_strs:
                    detail += "\n制約違反: " + ", ".join(margin_strs)
                self._bc_params_lbl.setText(
                    f"最も惜しい解: {detail}\n"
                    "パラメータ範囲を広げるか、制約条件を緩和して再度お試しください。"
                )
            else:
                self._bc_params_lbl.setText(
                    "パラメータ範囲を広げるか、制約条件を緩和して再度お試しください。"
                )
            self._bc_params_lbl.setStyleSheet(
                "color: #b71c1c; font-size: 10px; background: transparent; border: none;"
            )
            self._bc_obj_lbl.setText(
                f"{len(result.all_candidates)}点\n評価済み"
            )
            self._bc_obj_lbl.setStyleSheet(
                "color: #c62828; font-size: 13px; font-weight: bold;"
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
        # 収束品質バッジを付加
        quality_badge = ""
        diag = compute_convergence_diagnostics(result)
        if diag:
            qc = diag.quality_label
            if diag.quality_score >= 80:
                qc_color = "#2e7d32"
            elif diag.quality_score >= 60:
                qc_color = "#1565c0"
            elif diag.quality_score >= 40:
                qc_color = "#e65100"
            else:
                qc_color = "#c62828"
            quality_badge = (
                f"  <span style='font-size:9px; color:{qc_color};'>"
                f"[収束: {qc} {diag.quality_score:.0f}点]</span>"
            )

        self._bc_title_lbl.setText(
            f"<b>最良解が見つかりました</b>  "
            f"<span style='font-size:10px; color:#388e3c;'>（{feasibility_text}）</span>"
            f"{quality_badge}"
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
        """結果テーブルを上位20候補で更新します。

        制約を満たす候補を優先表示し、残り枠に制約違反候補も表示します。
        制約違反候補は薄い背景色で視覚的に区別されます。
        列ヘッダークリックでインタラクティブにソート可能です。
        """
        # ソートを一時無効にしてから行を追加（挿入中のソート防止）
        self._result_table.setSortingEnabled(False)
        self._result_table.setRowCount(0)
        ranked = result.all_ranked_candidates[:20]

        obj_key = result.config.objective_key if result.config else ""
        n_feasible = len(result.feasible_candidates)

        for rank, cand in enumerate(ranked):
            row = self._result_table.rowCount()
            self._result_table.insertRow(row)

            # 順位（制約違反候補には「-」を表示）— 数値ソート対応
            if cand.is_feasible:
                rank_text = str(rank + 1)
                rank_sort = float(rank + 1)
            else:
                rank_text = "-"
                rank_sort = float("inf")
            rank_item = _NumericTableItem(rank_text, rank_sort)
            rank_item.setTextAlignment(Qt.AlignCenter)
            if cand.is_feasible and rank < 3:
                font = QFont()
                font.setBold(True)
                rank_item.setFont(font)
                colors = [QColor("#FFD700"), QColor("#C0C0C0"), QColor("#CD7F32")]
                rank_item.setForeground(colors[rank])
            self._result_table.setItem(row, 0, rank_item)

            # パラメータ
            param_strs = [f"{k}={v:.4g}" for k, v in cand.params.items()]
            self._result_table.setItem(row, 1, QTableWidgetItem(", ".join(param_strs)))

            # 目的関数値 — 数値ソート対応
            obj_item = _NumericTableItem(
                f"{cand.objective_value:.6g}", cand.objective_value
            )
            obj_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._result_table.setItem(row, 2, obj_item)

            # 判定（OK=0, NG=1 でソート）
            verdict = "OK" if cand.is_feasible else "NG"
            verdict_item = _NumericTableItem(
                verdict, 0.0 if cand.is_feasible else 1.0
            )
            verdict_item.setTextAlignment(Qt.AlignCenter)
            verdict_item.setForeground(
                QColor("#2ca02c") if cand.is_feasible else QColor("#d62728")
            )
            self._result_table.setItem(row, 3, verdict_item)

            # 最小マージン（制約余裕の最小値）— 数値ソート対応
            if cand.constraint_margins:
                min_margin = min(cand.constraint_margins.values())
                min_key = min(
                    cand.constraint_margins, key=cand.constraint_margins.get
                )
                margin_text = f"{min_margin:+.4g} ({min_key})"
                margin_item = _NumericTableItem(margin_text, min_margin)
                if min_margin >= 0:
                    margin_item.setForeground(QColor("#2ca02c"))
                else:
                    margin_item.setForeground(QColor("#d62728"))
            else:
                margin_item = _NumericTableItem("—", float("inf"))
            margin_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._result_table.setItem(row, 4, margin_item)

            # 詳細（他の応答値）— ダブルクリックで全詳細表示
            details = []
            for k, v in cand.response_values.items():
                if k != obj_key:
                    details.append(f"{k}={v:.4g}")
            summary = ", ".join(details[:3])
            if len(details) > 3:
                summary += f" (+{len(details) - 3})"
            self._result_table.setItem(
                row, 5, QTableWidgetItem(summary)
            )

            # 制約違反候補の行を薄い赤背景で表示
            if not cand.is_feasible:
                bg = QColor(214, 39, 40, 30)  # 薄い赤
                for col in range(6):
                    item = self._result_table.item(row, col)
                    if item:
                        item.setBackground(bg)

        # ソート再有効化（デフォルトは順位昇順）
        self._result_table.setSortingEnabled(True)
        self._result_table.sortByColumn(0, Qt.AscendingOrder)

    def _show_candidate_detail(self, row: int, _col: int) -> None:
        """結果テーブルの行をダブルクリック → 候補の全詳細を表示。"""
        if not self._result:
            return
        ranked = self._result.all_ranked_candidates[:20]
        if row < 0 or row >= len(ranked):
            return
        cand = ranked[row]
        _CandidateDetailDialog(cand, self._result.config, parent=self).exec()

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

        # 停滞区間のハイライト
        stagnation = self._detect_stagnation(result.all_candidates)
        if stagnation and feasible_iters:
            stag_start_idx = stagnation["stagnation_start"]
            # feasible_iters上のインデックスに対応する評価回数
            if stag_start_idx < len(feasible_iters):
                stag_x_start = feasible_iters[stag_start_idx]
                stag_x_end = feasible_iters[-1]
                ax.axvspan(
                    stag_x_start, stag_x_end,
                    alpha=0.08, color="#ff9800",
                    label=f"停滞区間 ({stagnation['stagnation_length']}回)",
                )

        obj_label = result.config.objective_label if result.config else "目的関数"
        ax.set_xlabel("評価回数", fontsize=7)
        ax.set_ylabel(obj_label, fontsize=7)
        ax.set_title("収束履歴", fontsize=9)
        ax.tick_params(labelsize=6)
        ax.legend(fontsize=6, loc="upper right")
        ax.grid(linestyle="--", alpha=0.3)

        # --- 下段: パラメータ空間探索の可視化 ---
        self._draw_param_space(ax2, result)

        try:
            self._conv_canvas.fig.tight_layout()
        except (MemoryError, ValueError):
            pass
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
    # 結果エクスポート・分析・設定プリセット操作は
    # _OptimizerResultActionsMixin (optimizer_dialog_actions.py) に分離。
    # ------------------------------------------------------------------
