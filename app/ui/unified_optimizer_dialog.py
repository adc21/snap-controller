"""
app/ui/unified_optimizer_dialog.py
統合最適化ダイアログ。

ダンパーパラメータ（物理値）と基数を同時に最適化するダイアログ。
.s8i に定義済みのダンパーを起点に、チェックボックスで変数を選択し、
1〜2目的の最適化を実行する。

レイアウト:
  ┌──────────────────┬───────────────────────────────────────┐
  │  [左ペイン]       │  [右ペイン]                           │
  │  パラメータ選択    │  パレートフロント / 収束プロット       │
  │  目的関数(最大2)  │  ───────────────────────────────────   │
  │  制約条件         │  候補詳細パネル                       │
  │  探索手法         │                                       │
  │  [▶開始] [■停止]  │                                       │
  ├──────────────────┴───────────────────────────────────────┤
  │ 進捗バー                                                 │
  └──────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
from app.models.s8i_parser import parse_s8i, DamperDefinition
from app.services.optimizer import (
    DamperOptimizer,
    OptimizationCandidate,
    OptimizationConfig,
    OptimizationResult,
    ParameterRange,
)
from app.services.snap_evaluator import build_floor_rd_map, create_unified_evaluator
from app.services.optimizer_analytics import (
    compute_convergence_diagnostics,
    compute_correlation_analysis,
    compute_sensitivity,
    compute_sobol_sensitivity,
    export_optimization_log,
)
from .damper_field_data import get_damper_field_labels, get_damper_field_units
from .optimizer_analysis_dialogs import (
    ComparisonDialog,
    CorrelationDialog,
    DiagnosticsDialog,
    HeatmapDialog,
    ParetoDialog,
    SensitivityDialog,
    SobolDialog,
)
from .theme import ThemeManager, MPL_STYLES

logger = logging.getLogger(__name__)

# 目的関数の選択肢 (key, label, unit)
_OBJECTIVE_ITEMS = [
    ("max_drift", "最大層間変形角", "rad"),
    ("max_acc", "最大絶対加速度", "m/s²"),
    ("max_disp", "最大相対変位", "m"),
    ("max_vel", "最大相対速度", "m/s"),
    ("shear_coeff", "せん断力係数", "—"),
    ("max_otm", "最大転倒モーメント", "kN·m"),
    ("total_damper_count", "総ダンパー本数", "本"),
]

# 探索手法 (key, label, multi_objective_ok)
_METHOD_ITEMS = [
    ("grid", "グリッドサーチ", False),
    ("random", "ランダムサーチ", False),
    ("lhs", "ラテン超方格 (LHS)", False),
    ("bayesian", "ベイズ最適化", False),
    ("ga", "遺伝的アルゴリズム (GA)", False),
    ("sa", "焼きなまし法 (SA)", False),
    ("de", "差分進化 (DE)", False),
    ("nsga2", "NSGA-II (多目的)", True),
]


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    logger.debug("日本語フォント設定失敗、デフォルトフォントを使用")


class UnifiedOptimizerDialog(QDialog):
    """統合最適化ダイアログ: パラメータ+基数の同時最適化。"""

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

        # 最適化エンジン
        self._optimizer = DamperOptimizer()
        self._result: Optional[OptimizationResult] = None
        self._candidates: List[OptimizationCandidate] = []
        self._start_time: float = 0.0
        self._avg_eval_sec: float = 30.0  # 1回あたりの平均評価時間（動的更新）

        # .s8i 解析結果
        self._damper_defs: List[DamperDefinition] = []
        self._floor_rd_map: Dict[str, List[int]] = {}
        self._current_quantities: Dict[str, int] = {}
        self._floor_keys: List[str] = []

        # パラメータウィジェットの参照
        self._field_rows: List[Dict[str, Any]] = []  # 物理パラメータ行
        self._floor_rows: List[Dict[str, Any]] = []  # 基数パラメータ行

        self.setWindowTitle("統合最適化")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 800)

        self._parse_model()
        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def result(self) -> Optional[OptimizationResult]:
        return self._result

    @property
    def best_params(self) -> dict:
        if self._result and self._result.best:
            return dict(self._result.best.params)
        return {}

    def build_case_overrides(self) -> tuple:
        """best_params を AnalysisCase 互換形式に変換。

        Returns
        -------
        (damper_params, rd_overrides) : tuple
            damper_params: {def_name: {1-indexed_str: value_str}}
            rd_overrides: {row_idx_str: {"quantity": int}}
        """
        params = self.best_params
        if not params:
            return {}, {}

        dd = self._selected_damper_def()
        def_name = dd.name if dd else ""

        # 物理パラメータ → damper_params
        damper_params: Dict[str, Dict[str, str]] = {}
        for key, val in params.items():
            if key.startswith("field_"):
                try:
                    idx_0 = int(key.replace("field_", ""))
                    idx_1 = str(idx_0 + 1)  # damper_params は 1-indexed
                    if def_name:
                        damper_params.setdefault(def_name, {})[idx_1] = str(val)
                except (ValueError, TypeError):
                    logger.debug("field key 変換失敗: %s", key)

        # 基数パラメータ → _rd_overrides
        rd_overrides: Dict[str, Dict[str, Any]] = {}
        for key, val in params.items():
            if key.startswith("floor_count_"):
                floor_key = key.replace("floor_count_", "")
                rd_indices = self._floor_rd_map.get(floor_key, [])
                qty = int(round(val))
                if rd_indices:
                    # 基数を各RD要素に均等分配（端数は先頭に加算）
                    n_elems = len(rd_indices)
                    per_elem = qty // n_elems
                    remainder = qty - per_elem * n_elems
                    for i, row_idx in enumerate(rd_indices):
                        q = per_elem + (1 if i < remainder else 0)
                        rd_overrides[str(row_idx)] = {"quantity": q}

        return damper_params, rd_overrides

    # ------------------------------------------------------------------
    # .s8i 解析
    # ------------------------------------------------------------------
    def _parse_model(self) -> None:
        """ベースケースの .s8i を解析してダンパー情報を取得。"""
        if not self._base_case or not self._base_case.model_path:
            return
        try:
            model = parse_s8i(self._base_case.model_path)
            self._damper_defs = model.damper_defs
        except Exception as e:
            logger.warning("s8i 解析失敗: %s", e)
            return

        if not self._damper_defs:
            return

        try:
            frm, qty, keys = build_floor_rd_map(self._base_case.model_path)
            self._floor_rd_map = frm
            self._current_quantities = qty
            self._floor_keys = keys
        except Exception as e:
            logger.warning("floor_rd_map 構築失敗: %s", e)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 左ペイン
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        self._build_damper_selector(left_layout)
        self._build_param_table(left_layout)
        self._build_floor_table(left_layout)
        self._build_objectives(left_layout)
        self._build_constraints(left_layout)
        self._build_method_section(left_layout)
        self._build_advanced_options(left_layout)
        self._build_run_controls(left_layout)
        left_layout.addStretch()

        # 左ペインをスクロール可能にする
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 右ペイン
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)
        self._build_plot(right_layout)
        self._build_detail_panel(right_layout)

        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        root.addWidget(splitter, 1)
        self._build_progress_bar(root)

    # ---- 左ペイン ----

    def _build_damper_selector(self, layout: QVBoxLayout) -> None:
        """ダンパー定義選択コンボボックス。"""
        group = QGroupBox("ダンパー定義")
        gl = QHBoxLayout(group)
        gl.addWidget(QLabel("対象:"))
        self._def_combo = QComboBox()
        for dd in self._damper_defs:
            self._def_combo.addItem(
                f"{dd.name} ({dd.keyword})", dd.name
            )
        gl.addWidget(self._def_combo, 1)
        layout.addWidget(group)

    def _build_param_table(self, layout: QVBoxLayout) -> None:
        """物理パラメータ選択テーブル。"""
        group = QGroupBox("物理パラメータ")
        gl = QVBoxLayout(group)

        self._param_table = QTableWidget()
        self._param_table.setColumnCount(6)
        self._param_table.setHorizontalHeaderLabels(
            ["", "フィールド", "現在値", "下限", "上限", "単位"]
        )
        header = self._param_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self._param_table.verticalHeader().setVisible(False)
        self._param_table.setMaximumHeight(220)

        gl.addWidget(self._param_table)
        layout.addWidget(group)

        self._populate_param_table()

    def _populate_param_table(self) -> None:
        """選択中のダンパー定義の物理パラメータをテーブルに表示。"""
        self._field_rows.clear()
        self._param_table.setRowCount(0)

        dd = self._selected_damper_def()
        if dd is None:
            return

        labels = get_damper_field_labels(dd.keyword)
        units = get_damper_field_units(dd.keyword)

        # 値に数値を持つフィールドのみ表示 (idx 1〜)
        for field_idx_1based, label_text in sorted(labels.items()):
            # values は 0-based, labels は 1-based
            val_idx = field_idx_1based  # values[0]=name, values[1]=field1, ...
            if val_idx >= len(dd.values):
                continue

            raw_val = dd.values[val_idx]
            try:
                current = float(raw_val)
            except (ValueError, TypeError):
                continue

            # 種別・番号・コード・フラグ系は最適化対象外
            _skip_keywords = (
                "種別", "k-DB", "番号", "型番", "モデル",
                "考慮", "初期解析", "疲労損傷", "重量種別",
                "計算", "しない", "する",
            )
            if any(kw in label_text for kw in _skip_keywords):
                continue

            unit = units.get(field_idx_1based, "")
            row = self._param_table.rowCount()
            self._param_table.insertRow(row)

            # チェックボックス
            cb = QCheckBox()
            self._param_table.setCellWidget(row, 0, cb)

            # フィールド名
            name_item = QTableWidgetItem(label_text)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 1, name_item)

            # 現在値
            val_item = QTableWidgetItem(f"{current:g}")
            val_item.setFlags(val_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 2, val_item)

            # 下限
            lo = self._suggest_bound(current, lower=True)
            lo_spin = QDoubleSpinBox()
            lo_spin.setDecimals(4)
            lo_spin.setRange(-1e9, 1e9)
            lo_spin.setValue(lo)
            self._param_table.setCellWidget(row, 3, lo_spin)

            # 上限
            hi = self._suggest_bound(current, lower=False)
            hi_spin = QDoubleSpinBox()
            hi_spin.setDecimals(4)
            hi_spin.setRange(-1e9, 1e9)
            hi_spin.setValue(hi)
            self._param_table.setCellWidget(row, 4, hi_spin)

            # 単位
            unit_item = QTableWidgetItem(unit)
            unit_item.setFlags(unit_item.flags() & ~Qt.ItemIsEditable)
            self._param_table.setItem(row, 5, unit_item)

            self._field_rows.append({
                "cb": cb,
                "field_idx_1based": field_idx_1based,
                "val_idx_0based": val_idx,
                "label": label_text,
                "current": current,
                "lo_spin": lo_spin,
                "hi_spin": hi_spin,
                "unit": unit,
            })

    def _build_floor_table(self, layout: QVBoxLayout) -> None:
        """ダンパー基数テーブル。"""
        group = QGroupBox("ダンパー基数")
        gl = QVBoxLayout(group)

        self._floor_table = QTableWidget()
        self._floor_table.setColumnCount(5)
        self._floor_table.setHorizontalHeaderLabels(
            ["", "階", "現在基数", "下限", "上限"]
        )
        header = self._floor_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self._floor_table.verticalHeader().setVisible(False)
        self._floor_table.setMaximumHeight(160)

        gl.addWidget(self._floor_table)
        layout.addWidget(group)

        self._populate_floor_table()

    def _populate_floor_table(self) -> None:
        """フロア基数をテーブルに表示。"""
        self._floor_rows.clear()
        self._floor_table.setRowCount(0)

        for fk in self._floor_keys:
            qty = self._current_quantities.get(fk, 0)
            row = self._floor_table.rowCount()
            self._floor_table.insertRow(row)

            cb = QCheckBox()
            self._floor_table.setCellWidget(row, 0, cb)

            fk_item = QTableWidgetItem(fk)
            fk_item.setFlags(fk_item.flags() & ~Qt.ItemIsEditable)
            self._floor_table.setItem(row, 1, fk_item)

            qty_item = QTableWidgetItem(str(qty))
            qty_item.setFlags(qty_item.flags() & ~Qt.ItemIsEditable)
            self._floor_table.setItem(row, 2, qty_item)

            lo_spin = QSpinBox()
            lo_spin.setRange(0, 100)
            lo_spin.setValue(max(0, qty - 2))
            self._floor_table.setCellWidget(row, 3, lo_spin)

            hi_spin = QSpinBox()
            hi_spin.setRange(0, 100)
            hi_spin.setValue(qty + 4)
            self._floor_table.setCellWidget(row, 4, hi_spin)

            self._floor_rows.append({
                "cb": cb,
                "floor_key": fk,
                "current": qty,
                "lo_spin": lo_spin,
                "hi_spin": hi_spin,
            })

    def _build_objectives(self, layout: QVBoxLayout) -> None:
        """目的関数（最大2つ）の設定。"""
        group = QGroupBox("目的関数")
        gl = QFormLayout(group)

        self._obj1_combo = QComboBox()
        for key, label, unit in _OBJECTIVE_ITEMS:
            self._obj1_combo.addItem(f"{label} ({unit})", key)
        gl.addRow("目的関数 1:", self._obj1_combo)

        row2 = QHBoxLayout()
        self._obj2_enabled = QCheckBox("有効")
        self._obj2_combo = QComboBox()
        for key, label, unit in _OBJECTIVE_ITEMS:
            self._obj2_combo.addItem(f"{label} ({unit})", key)
        self._obj2_combo.setCurrentIndex(len(_OBJECTIVE_ITEMS) - 1)  # total_damper_count
        self._obj2_combo.setEnabled(False)
        row2.addWidget(self._obj2_enabled)
        row2.addWidget(self._obj2_combo, 1)
        gl.addRow("目的関数 2:", row2)

        layout.addWidget(group)

    def _build_constraints(self, layout: QVBoxLayout) -> None:
        """制約条件パネル。PerformanceCriteria の有効項目を自動読込。"""
        group = QGroupBox("制約条件")
        gl = QFormLayout(group)

        self._constraint_widgets: Dict[str, QDoubleSpinBox] = {}

        if self._criteria and self._criteria.items:
            # PerformanceCriteria の有効項目から制約を構築
            for item in self._criteria.items:
                if not item.enabled or item.limit_value is None:
                    continue
                spin = QDoubleSpinBox()
                spin.setDecimals(item.decimals)
                spin.setRange(0, 1e6)
                spin.setValue(item.limit_value)
                gl.addRow(f"{item.label} ({item.unit}) <", spin)
                self._constraint_widgets[item.key] = spin
        else:
            # デフォルト制約（criteria未設定時）
            defaults = [
                ("max_drift", 0.005, "最大層間変形角 <", 6),
                ("shear_coeff", 0.30, "せん断力係数 <", 4),
            ]
            for key, val, label, decimals in defaults:
                spin = QDoubleSpinBox()
                spin.setDecimals(decimals)
                spin.setRange(0, 10)
                spin.setValue(val)
                gl.addRow(label, spin)
                self._constraint_widgets[key] = spin

        layout.addWidget(group)

    def _build_method_section(self, layout: QVBoxLayout) -> None:
        """探索手法・反復数。"""
        group = QGroupBox("探索設定")
        gl = QFormLayout(group)

        self._method_combo = QComboBox()
        for key, label, _ in _METHOD_ITEMS:
            self._method_combo.addItem(label, key)
        gl.addRow("手法:", self._method_combo)

        self._iter_spin = QSpinBox()
        self._iter_spin.setRange(5, 10000)
        self._iter_spin.setValue(50)
        gl.addRow("反復数:", self._iter_spin)

        self._estimate_label = QLabel("")
        gl.addRow("", self._estimate_label)

        layout.addWidget(group)

    def _build_advanced_options(self, layout: QVBoxLayout) -> None:
        """折りたたみ式の詳細設定パネル。"""
        self._adv_toggle = QCheckBox("詳細設定を表示")
        self._adv_toggle.setChecked(False)
        layout.addWidget(self._adv_toggle)

        self._adv_widget = QWidget()
        adv = QVBoxLayout(self._adv_widget)
        adv.setContentsMargins(8, 0, 0, 0)

        self._build_seed_option(adv)
        self._build_parallel_option(adv)
        self._build_checkpoint_option(adv)
        self._build_robust_option(adv)

        self._adv_widget.setVisible(False)
        layout.addWidget(self._adv_widget)

    def _build_seed_option(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self._seed_check = QCheckBox("乱数シード:")
        self._seed_check.setToolTip(
            "整数を指定すると再現性のある結果を得られます。"
        )
        row.addWidget(self._seed_check)
        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 999999)
        self._seed_spin.setValue(42)
        self._seed_spin.setFixedWidth(80)
        self._seed_spin.setEnabled(False)
        row.addWidget(self._seed_spin)
        row.addStretch()
        self._seed_check.toggled.connect(self._seed_spin.setEnabled)
        layout.addLayout(row)

    def _build_parallel_option(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.addWidget(QLabel("並列評価数:"))
        self._parallel_spin = QSpinBox()
        self._parallel_spin.setRange(1, 16)
        self._parallel_spin.setValue(1)
        self._parallel_spin.setFixedWidth(60)
        self._parallel_spin.setToolTip(
            "グリッド/ランダム/LHSで複数候補を同時評価。\n"
            "SNAP解析時は4〜8が目安。"
        )
        row.addWidget(self._parallel_spin)
        row.addWidget(QLabel("  タイムアウト:"))
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 3600)
        self._timeout_spin.setValue(300)
        self._timeout_spin.setSuffix(" 秒")
        self._timeout_spin.setSingleStep(30)
        self._timeout_spin.setFixedWidth(100)
        row.addWidget(self._timeout_spin)
        row.addStretch()
        layout.addLayout(row)

    def _build_checkpoint_option(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self._checkpoint_check = QCheckBox("チェックポイント自動保存")
        self._checkpoint_check.setToolTip(
            "最適化中に一定間隔で中間結果を自動保存します。"
        )
        row.addWidget(self._checkpoint_check)
        row.addWidget(QLabel("間隔:"))
        self._checkpoint_interval_spin = QSpinBox()
        self._checkpoint_interval_spin.setRange(5, 1000)
        self._checkpoint_interval_spin.setValue(10)
        self._checkpoint_interval_spin.setFixedWidth(60)
        self._checkpoint_interval_spin.setSuffix(" 回")
        row.addWidget(self._checkpoint_interval_spin)
        row.addStretch()
        layout.addLayout(row)

    def _build_robust_option(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        self._robust_check = QCheckBox("ロバスト最適化")
        self._robust_check.setToolTip(
            "パラメータ摂動付きで複数回評価し最悪ケースで最適化。\n"
            "製造誤差に頑健な設計解を探索します。"
        )
        row.addWidget(self._robust_check)
        row.addWidget(QLabel("サンプル数:"))
        self._robust_samples_spin = QSpinBox()
        self._robust_samples_spin.setRange(1, 20)
        self._robust_samples_spin.setValue(3)
        self._robust_samples_spin.setFixedWidth(50)
        row.addWidget(self._robust_samples_spin)
        row.addWidget(QLabel("摂動幅:"))
        self._robust_delta_spin = QDoubleSpinBox()
        self._robust_delta_spin.setRange(0.01, 0.30)
        self._robust_delta_spin.setValue(0.05)
        self._robust_delta_spin.setSingleStep(0.01)
        self._robust_delta_spin.setDecimals(2)
        self._robust_delta_spin.setFixedWidth(70)
        row.addWidget(self._robust_delta_spin)
        row.addStretch()
        layout.addLayout(row)

    def _build_run_controls(self, layout: QVBoxLayout) -> None:
        """開始/停止ボタン。"""
        hl = QHBoxLayout()
        self._start_btn = QPushButton("最適化開始")
        self._start_btn.setMinimumHeight(36)
        self._stop_btn = QPushButton("停止")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setMinimumHeight(36)
        hl.addWidget(self._start_btn)
        hl.addWidget(self._stop_btn)
        layout.addLayout(hl)

    # ---- 右ペイン ----

    def _build_plot(self, layout: QVBoxLayout) -> None:
        """パレートフロント / 収束プロット。"""
        _apply_mpl_theme()

        # 軸セレクタ行（ユーザーが解析中でも X/Y を自由に切り替え可能）
        axis_row = QHBoxLayout()
        axis_row.addWidget(QLabel("X軸:"))
        self._xaxis_combo = QComboBox()
        axis_row.addWidget(self._xaxis_combo)
        axis_row.addSpacing(12)
        axis_row.addWidget(QLabel("Y軸:"))
        self._yaxis_combo = QComboBox()
        axis_row.addWidget(self._yaxis_combo)
        axis_row.addStretch()
        self._populate_axis_combos()
        self._xaxis_combo.currentIndexChanged.connect(self._on_axis_changed)
        self._yaxis_combo.currentIndexChanged.connect(self._on_axis_changed)
        layout.addLayout(axis_row)

        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._canvas, 2)

        self._scatter_feasible = None
        self._scatter_infeasible = None
        self._scatter_pareto = None
        self._scatter_best = None

    def _populate_axis_combos(self) -> None:
        """軸セレクタの選択肢を構築する。

        項目:
          - 自動 (目的関数設定に応じて収束/パレートを自動選択)
          - 反復番号
          - 応答値一覧 (_OBJECTIVE_ITEMS)
        """
        # X軸: 自動 / 反復番号 / 応答値
        self._xaxis_combo.addItem("自動", "auto")
        self._xaxis_combo.addItem("反復番号", "iteration")
        for key, label, unit in _OBJECTIVE_ITEMS:
            unit_str = f" [{unit}]" if unit and unit != "—" else ""
            self._xaxis_combo.addItem(f"{label}{unit_str}", key)

        # Y軸: 自動 / 応答値 (反復番号は通常Y軸にしない)
        self._yaxis_combo.addItem("自動", "auto")
        self._yaxis_combo.addItem("反復番号", "iteration")
        for key, label, unit in _OBJECTIVE_ITEMS:
            unit_str = f" [{unit}]" if unit and unit != "—" else ""
            self._yaxis_combo.addItem(f"{label}{unit_str}", key)

    def _on_axis_changed(self) -> None:
        """X/Y軸セレクタの変更ハンドラ (最適化中でも呼ばれる)。"""
        self._update_plot()

    def _build_detail_panel(self, layout: QVBoxLayout) -> None:
        """候補詳細パネル。"""
        group = QGroupBox("候補詳細")
        gl = QVBoxLayout(group)
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setMaximumHeight(180)
        gl.addWidget(self._detail_text)

        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("この候補を .s8i に適用")
        self._apply_btn.setEnabled(False)
        btn_row.addWidget(self._apply_btn)

        self._export_btn = QPushButton("CSV エクスポート")
        self._export_btn.setEnabled(False)
        btn_row.addWidget(self._export_btn)
        gl.addLayout(btn_row)

        btn_row2 = QHBoxLayout()
        self._save_json_btn = QPushButton("結果保存")
        self._save_json_btn.setEnabled(False)
        self._save_json_btn.setToolTip("最適化結果をJSONに保存")
        btn_row2.addWidget(self._save_json_btn)

        self._load_json_btn = QPushButton("結果読込")
        self._load_json_btn.setToolTip("JSONから結果を読み込みプロットに表示")
        btn_row2.addWidget(self._load_json_btn)

        self._save_plot_btn = QPushButton("画像保存")
        self._save_plot_btn.setEnabled(False)
        self._save_plot_btn.setToolTip("プロットを画像ファイルとして保存")
        btn_row2.addWidget(self._save_plot_btn)
        gl.addLayout(btn_row2)

        # 分析ボタン行
        self._build_analysis_buttons(gl)

        layout.addWidget(group)

    def _build_progress_bar(self, layout: QVBoxLayout) -> None:
        """進捗バー。"""
        hl = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress_label = QLabel("")
        hl.addWidget(self._progress, 1)
        hl.addWidget(self._progress_label)
        layout.addLayout(hl)

    # ------------------------------------------------------------------
    # シグナル接続
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)
        self._apply_btn.clicked.connect(self._on_apply)
        self._export_btn.clicked.connect(self._on_export_csv)
        self._save_json_btn.clicked.connect(self._on_save_json)
        self._load_json_btn.clicked.connect(self._on_load_json)
        self._save_plot_btn.clicked.connect(self._on_save_plot)

        # 分析ボタン
        self._sensitivity_btn.clicked.connect(self._on_run_sensitivity)
        self._sobol_btn.clicked.connect(self._on_run_sobol)
        self._diagnostics_btn.clicked.connect(self._on_show_diagnostics)
        self._correlation_btn.clicked.connect(self._on_show_correlation)
        self._heatmap_btn.clicked.connect(self._on_show_heatmap)
        self._pareto_btn.clicked.connect(self._on_show_pareto)
        self._log_btn.clicked.connect(self._on_export_log)
        self._html_report_btn.clicked.connect(self._on_export_html_report)
        self._comparison_btn.clicked.connect(self._on_show_comparison)

        self._obj2_enabled.toggled.connect(self._on_obj2_toggled)
        self._def_combo.currentIndexChanged.connect(self._on_damper_def_changed)
        self._iter_spin.valueChanged.connect(self._update_estimate)
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        self._adv_toggle.toggled.connect(self._adv_widget.setVisible)

        self._optimizer.progress.connect(self._on_progress)
        self._optimizer.candidate_found.connect(self._on_candidate)
        self._optimizer.optimization_finished.connect(self._on_finished)

        # matplotlib クリックイベント
        self._canvas.mpl_connect("pick_event", self._on_pick)

        self._update_estimate()

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------
    def _on_obj2_toggled(self, checked: bool) -> None:
        self._obj2_combo.setEnabled(checked)
        self._on_method_changed()

    def _on_damper_def_changed(self) -> None:
        self._populate_param_table()
        self._populate_floor_table()

    def _on_method_changed(self) -> None:
        is_multi = self._obj2_enabled.isChecked()
        method_key = self._method_combo.currentData()

        if is_multi and method_key != "nsga2":
            # 多目的の場合は NSGA-II に強制切替
            for i in range(self._method_combo.count()):
                if self._method_combo.itemData(i) == "nsga2":
                    self._method_combo.setCurrentIndex(i)
                    break

        self._update_estimate()

    def _update_estimate(self) -> None:
        n = self._iter_spin.value()
        total_sec = n * self._avg_eval_sec
        self._estimate_label.setText(
            f"推定 {n} 回の評価 (約 {self._format_duration(total_sec)})"
        )

    def _on_start(self) -> None:
        """最適化を開始。"""
        params = self._collect_parameters()
        if not params:
            QMessageBox.warning(self, "パラメータ未選択",
                                "最適化するパラメータを1つ以上チェックしてください。")
            return

        config = self._build_config(params)
        evaluator = self._create_evaluator(params)

        if evaluator is None:
            QMessageBox.warning(
                self, "評価関数エラー",
                "SNAP評価関数を構築できませんでした。\n"
                "モデルパスとSNAP.exeの設定を確認してください。"
            )
            return

        # UI 状態リセット
        self._candidates.clear()
        self._selected_candidate: Optional[OptimizationCandidate] = None
        self._start_time = time.time()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._apply_btn.setEnabled(False)
        self._detail_text.clear()
        self._progress.setValue(0)

        # プロット初期化
        self._init_plot(config)

        # 最適化実行
        self._optimizer.optimize(config, evaluator)

    def _on_stop(self) -> None:
        self._optimizer.cancel()
        self._stop_btn.setEnabled(False)

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        elapsed = time.time() - self._start_time
        eta_str = ""
        if current > 0 and total > 0 and self._start_time > 0:
            self._avg_eval_sec = elapsed / current
            remaining = self._avg_eval_sec * (total - current)
            eta_str = f"  残り {self._format_duration(remaining)}"
        self._progress_label.setText(
            f"{current}/{total}  経過 {self._format_duration(elapsed)}{eta_str}"
        )

    def _on_candidate(self, cand: OptimizationCandidate) -> None:
        """候補が1つ評価されるたびに呼ばれる。"""
        self._candidates.append(cand)
        self._update_plot()

    def _on_finished(self, result: OptimizationResult) -> None:
        self._result = result
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        has_cands = bool(self._candidates)
        self._export_btn.setEnabled(has_cands)
        self._save_json_btn.setEnabled(has_cands)
        self._save_plot_btn.setEnabled(has_cands)

        if result.best:
            self._show_candidate_detail(result.best)
            self._apply_btn.setEnabled(True)

        self._enable_analysis_buttons()

        elapsed = time.time() - self._start_time
        feasible_count = sum(1 for c in self._candidates if c.is_feasible)
        self._progress_label.setText(
            f"完了 ({len(self._candidates)} 候補, "
            f"実行可能 {feasible_count}, "
            f"{self._format_duration(elapsed)})"
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """秒数を人間が読みやすい時間文字列に変換。"""
        s = int(seconds)
        if s < 60:
            return f"{s}秒"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}分{s}秒"
        h, m = divmod(m, 60)
        return f"{h}時間{m}分"

    def _on_pick(self, event) -> None:
        """プロット上のクリックで候補を選択。"""
        if not event.ind:
            return
        idx = event.ind[0]

        # feasible/infeasible どちらのアーティストかで候補リストのインデックスを特定
        artist = event.artist
        if artist == self._scatter_feasible:
            feasible = [c for c in self._candidates if c.is_feasible]
            if idx < len(feasible):
                self._show_candidate_detail(feasible[idx])
                self._selected_candidate = feasible[idx]
                self._apply_btn.setEnabled(True)
        elif artist == self._scatter_infeasible:
            infeasible = [c for c in self._candidates if not c.is_feasible]
            if idx < len(infeasible):
                self._show_candidate_detail(infeasible[idx])
                self._selected_candidate = infeasible[idx]
                self._apply_btn.setEnabled(True)
        elif artist == self._scatter_pareto:
            pareto = self._compute_pareto_front()
            if idx < len(pareto):
                self._show_candidate_detail(pareto[idx])
                self._selected_candidate = pareto[idx]
                self._apply_btn.setEnabled(True)

    def _on_apply(self) -> None:
        """選択した候補のパラメータをベースケースに適用。"""
        cand = getattr(self, "_selected_candidate", None)
        if cand is None and self._result and self._result.best:
            cand = self._result.best
        if cand is None:
            return

        reply = QMessageBox.question(
            self, "候補適用",
            "選択した候補のパラメータを新しいケースとして追加しますか？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.accept()

    def _on_export_csv(self) -> None:
        """全候補をCSVにエクスポート。"""
        if not self._candidates:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "CSV エクスポート", "optimization_results.csv",
            "CSV ファイル (*.csv)",
        )
        if not path:
            return

        try:
            self._write_csv(path)
            QMessageBox.information(
                self, "エクスポート完了",
                f"{len(self._candidates)} 候補を CSV に保存しました。\n{path}",
            )
        except Exception as e:
            QMessageBox.critical(self, "エクスポートエラー", str(e))

    def _write_csv(self, path: str) -> None:
        """候補データをCSVファイルに書き込む。"""
        import csv

        if not self._candidates:
            return

        # ヘッダ構築
        param_keys = list(self._candidates[0].params.keys())
        response_keys = [k for k in self._candidates[0].response_values.keys()]

        # パラメータキーのラベルマッピングを構築
        label_map = self._build_param_label_map()

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)

            header = ["#", "feasible"]
            header += [label_map.get(k, k) for k in param_keys]
            header += response_keys
            header += ["objective_value"]
            writer.writerow(header)

            for i, cand in enumerate(self._candidates):
                row = [cand.iteration, "OK" if cand.is_feasible else "NG"]
                row += [cand.params.get(k, "") for k in param_keys]
                row += [cand.response_values.get(k, "") for k in response_keys]
                row += [cand.objective_value]
                writer.writerow(row)

    def _build_param_label_map(self) -> Dict[str, str]:
        """パラメータキー→日本語ラベルのマッピングを構築。"""
        label_map: Dict[str, str] = {}
        for row_info in self._field_rows:
            key = f"field_{row_info['val_idx_0based']}"
            label_map[key] = row_info["label"]
        for row_info in self._floor_rows:
            key = f"floor_count_{row_info['floor_key']}"
            label_map[key] = f"{row_info['floor_key']} 基数"
        return label_map

    # ------------------------------------------------------------------
    # パラメータ収集
    # ------------------------------------------------------------------
    def _collect_parameters(self) -> List[ParameterRange]:
        """チェックされたパラメータを ParameterRange リストに変換。"""
        params: List[ParameterRange] = []
        skipped: List[str] = []

        dd = self._selected_damper_def()

        # 物理パラメータ
        for row_info in self._field_rows:
            if not row_info["cb"].isChecked():
                continue
            lo = row_info["lo_spin"].value()
            hi = row_info["hi_spin"].value()
            if lo >= hi:
                skipped.append(f"{row_info['label']} (下限{lo} >= 上限{hi})")
                continue
            # キー: field_{0-based index}
            key = f"field_{row_info['val_idx_0based']}"
            step = self._suggest_step(lo, hi)
            params.append(ParameterRange(
                key=key,
                label=row_info["label"],
                min_val=lo,
                max_val=hi,
                step=step,
                is_integer=False,
                is_floor_count=False,
            ))

        # 基数パラメータ
        for row_info in self._floor_rows:
            if not row_info["cb"].isChecked():
                continue
            lo = row_info["lo_spin"].value()
            hi = row_info["hi_spin"].value()
            if lo >= hi:
                skipped.append(f"{row_info['floor_key']} 基数 (下限{lo} >= 上限{hi})")
                continue
            key = f"floor_count_{row_info['floor_key']}"
            params.append(ParameterRange(
                key=key,
                label=f"{row_info['floor_key']} 基数",
                min_val=float(lo),
                max_val=float(hi),
                step=1.0,
                is_integer=True,
                is_floor_count=True,
            ))

        if skipped:
            logger.warning(
                "範囲不正のためスキップされたパラメータ: %s",
                ", ".join(skipped),
            )

        return params

    def _build_config(self, params: List[ParameterRange]) -> OptimizationConfig:
        """UI設定から OptimizationConfig を構築。"""
        obj1_key = self._obj1_combo.currentData()
        obj1_label = self._obj1_combo.currentText()
        method = self._method_combo.currentData()
        max_iter = self._iter_spin.value()

        constraints = {}
        for key, spin in self._constraint_widgets.items():
            constraints[key] = spin.value()

        dd = self._selected_damper_def()
        damper_type = dd.keyword if dd else ""

        config = OptimizationConfig(
            objective_key=obj1_key,
            objective_label=obj1_label,
            parameters=params,
            constraints=constraints,
            method=method,
            max_iterations=max_iter,
            criteria=self._criteria,
            damper_type=damper_type,
            base_case=self._base_case,
            snap_timeout=self._timeout_spin.value(),
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
            random_seed=(
                self._seed_spin.value()
                if self._seed_check.isChecked()
                else None
            ),
        )

        # 2目的の場合: objective_weights を設定
        if self._obj2_enabled.isChecked():
            obj2_key = self._obj2_combo.currentData()
            config.method = "nsga2"
            config.objective_weights = {
                obj1_key: 1.0,
                obj2_key: 1.0,
            }

        return config

    def _create_evaluator(self, params: List[ParameterRange]):
        """create_unified_evaluator で SNAP 評価関数を構築。"""
        if not self._base_case:
            return None

        dd = self._selected_damper_def()
        def_name = dd.name if dd else ""

        def log_cb(msg: str) -> None:
            logger.info(msg)

        return create_unified_evaluator(
            snap_exe_path=self._snap_exe_path,
            base_case=self._base_case,
            param_ranges=params,
            log_callback=log_cb,
            snap_work_dir=self._snap_work_dir,
            timeout=self._timeout_spin.value(),
            damper_def_name=def_name,
        )

    # ------------------------------------------------------------------
    # プロット
    # ------------------------------------------------------------------
    def _init_plot(self, config: OptimizationConfig) -> None:
        """プロットを初期化。"""
        _apply_mpl_theme()
        self._ax.clear()

        is_multi = self._obj2_enabled.isChecked()
        if is_multi:
            obj1_label = self._obj1_combo.currentText()
            obj2_label = self._obj2_combo.currentText()
            self._ax.set_xlabel(obj1_label, fontsize=10)
            self._ax.set_ylabel(obj2_label, fontsize=10)
            self._ax.set_title("パレートフロント", fontsize=12)
        else:
            self._ax.set_xlabel("反復", fontsize=10)
            self._ax.set_ylabel(config.objective_label, fontsize=10)
            self._ax.set_title("収束プロット", fontsize=12)

        self._scatter_feasible = None
        self._scatter_infeasible = None
        self._scatter_pareto = None
        self._scatter_best = None
        self._canvas.draw()

    def _update_plot(self) -> None:
        """候補データでプロットを更新。

        軸セレクタが両方「自動」の場合は目的関数設定に応じた
        既定の収束/パレートプロットを描画する。
        どちらかがカスタム指定されていればユーザー指定軸で描画する。
        """
        if not self._candidates:
            return

        self._ax.clear()

        x_key = self._xaxis_combo.currentData() if hasattr(self, "_xaxis_combo") else "auto"
        y_key = self._yaxis_combo.currentData() if hasattr(self, "_yaxis_combo") else "auto"
        is_multi = self._obj2_enabled.isChecked()

        if x_key == "auto" and y_key == "auto":
            if is_multi:
                self._update_pareto_plot()
            else:
                self._update_convergence_plot()
        else:
            # カスタム軸: auto 側は既定を補完 (X=iteration, Y=obj1)
            resolved_x = x_key if x_key != "auto" else ("iteration" if not is_multi else self._obj1_combo.currentData())
            resolved_y = y_key if y_key != "auto" else (self._obj1_combo.currentData() if not is_multi else self._obj2_combo.currentData())
            self._update_custom_axis_plot(resolved_x, resolved_y)

        try:
            self._fig.tight_layout()
        except (ValueError, MemoryError):
            logger.debug("tight_layout 失敗")
        self._canvas.draw()

    # --------------- 軸値抽出ヘルパー ---------------
    def _axis_label(self, key: str) -> str:
        """軸キーから表示ラベル (単位込み) を返す。"""
        if key == "iteration":
            return "反復番号"
        for k, label, unit in _OBJECTIVE_ITEMS:
            if k == key:
                unit_str = f" [{unit}]" if unit and unit != "—" else ""
                return f"{label}{unit_str}"
        return key

    @staticmethod
    def _candidate_axis_value(cand: OptimizationCandidate, key: str) -> float:
        """候補から軸キーの値を抽出 (iteration / response_values 両対応)。"""
        if key == "iteration":
            return float(cand.iteration)
        return float(cand.response_values.get(key, float("inf")))

    def _update_custom_axis_plot(self, x_key: str, y_key: str) -> None:
        """ユーザー指定のX/Y軸で feasible/infeasible の散布図を描画。

        軸が両方とも目的関数 (obj1/obj2) に一致する場合は
        パレートフロント・最良解マーカーも重ねて表示する。
        """
        feasible = [c for c in self._candidates if c.is_feasible]
        infeasible = [c for c in self._candidates if not c.is_feasible]

        def _xy(cands, xk, yk):
            xs, ys = [], []
            for c in cands:
                xv = self._candidate_axis_value(c, xk)
                yv = self._candidate_axis_value(c, yk)
                if xv < float("inf") and yv < float("inf"):
                    xs.append(xv)
                    ys.append(yv)
            return xs, ys

        if infeasible:
            xs, ys = _xy(infeasible, x_key, y_key)
            self._scatter_infeasible = self._plot_scatter_layer(
                xs, ys, "gray", "x", 0.4, "infeasible",
            )

        if feasible:
            xs, ys = _xy(feasible, x_key, y_key)
            self._scatter_feasible = self._plot_scatter_layer(
                xs, ys, "#1f77b4", "o", 0.6, "feasible",
            )

        # 2目的設定で軸が (obj1, obj2) と一致する場合のみパレートフロントを重ねる
        obj1_key = self._obj1_combo.currentData()
        obj2_key = self._obj2_combo.currentData() if self._obj2_enabled.isChecked() else None
        axis_matches_objectives = (
            obj2_key is not None
            and {x_key, y_key} == {obj1_key, obj2_key}
        )
        if axis_matches_objectives:
            pareto = self._compute_pareto_front()
            if pareto:
                px, py = _xy(pareto, x_key, y_key)
                self._scatter_pareto = self._plot_scatter_layer(
                    px, py, "orange", "o", 1.0, "パレートフロント",
                    s=80, edgecolors="black", linewidths=0.8, zorder=5,
                )
                best = min(pareto, key=lambda c: c.response_values.get(obj1_key, float("inf")))
                bx = self._candidate_axis_value(best, x_key)
                by = self._candidate_axis_value(best, y_key)
                self._scatter_best = self._ax.scatter(
                    [bx], [by], c="red", marker="*", s=200,
                    edgecolors="black", linewidths=0.8, label="最良解", zorder=10,
                )

        # 1目的設定で Y が目的関数値、X が反復番号の場合は最良値推移ラインを重ねる
        if x_key == "iteration" and not self._obj2_enabled.isChecked() and y_key == obj1_key:
            pairs = sorted(
                (c.iteration, c.response_values.get(obj1_key, float("inf")))
                for c in feasible
            )
            if pairs:
                best_so_far = []
                cur = float("inf")
                for _, v in pairs:
                    cur = min(cur, v)
                    best_so_far.append(cur)
                self._ax.plot(
                    [it for it, _ in pairs], best_so_far,
                    "r-", linewidth=1.5, alpha=0.8, label="最良値",
                )

        self._ax.set_xlabel(self._axis_label(x_key), fontsize=10)
        self._ax.set_ylabel(self._axis_label(y_key), fontsize=10)
        self._ax.set_title(f"{self._axis_label(x_key)} vs {self._axis_label(y_key)}", fontsize=12)
        self._ax.legend(fontsize=8, loc="upper right")

    def _plot_scatter_layer(
        self,
        xs: list,
        ys: list,
        color: str,
        marker: str,
        alpha: float,
        label: str,
        **kwargs,
    ):
        """散布図レイヤーを描画してアーティストを返す。"""
        if not xs:
            return None
        return self._ax.scatter(
            xs, ys, c=color, marker=marker, alpha=alpha,
            label=label, picker=True, pickradius=5, **kwargs,
        )

    def _update_convergence_plot(self) -> None:
        """1目的: 収束プロット。"""
        obj_key = self._obj1_combo.currentData()

        feasible = [(c.iteration, c.response_values.get(obj_key, float("inf")))
                     for c in self._candidates if c.is_feasible]
        infeasible = [(c.iteration, c.response_values.get(obj_key, float("inf")))
                       for c in self._candidates if not c.is_feasible]

        if infeasible:
            xs, ys = zip(*infeasible)
            self._scatter_infeasible = self._plot_scatter_layer(
                list(xs), list(ys), "gray", "x", 0.5, "infeasible",
            )

        if feasible:
            xs, ys = zip(*feasible)
            self._scatter_feasible = self._plot_scatter_layer(
                list(xs), list(ys), "#1f77b4", "o", 0.7, "feasible",
            )

            # 最良値の推移ライン
            best_so_far = []
            current_best = float("inf")
            for _, val in sorted(feasible):
                current_best = min(current_best, val)
                best_so_far.append(current_best)
            sorted_iters = sorted(x for x, _ in feasible)
            if len(sorted_iters) == len(best_so_far):
                self._ax.plot(sorted_iters, best_so_far, "r-", linewidth=1.5,
                              alpha=0.8, label="最良値")

        self._ax.set_xlabel("反復", fontsize=10)
        obj_label = self._obj1_combo.currentText()
        self._ax.set_ylabel(obj_label, fontsize=10)
        self._ax.set_title("収束プロット", fontsize=12)
        self._ax.legend(fontsize=8, loc="upper right")

    def _update_pareto_plot(self) -> None:
        """2目的: パレートフロント散布図。"""
        obj1_key = self._obj1_combo.currentData()
        obj2_key = self._obj2_combo.currentData()

        feasible = [c for c in self._candidates if c.is_feasible]
        infeasible = [c for c in self._candidates if not c.is_feasible]

        if infeasible:
            xs = [c.response_values.get(obj1_key, float("inf")) for c in infeasible]
            ys = [c.response_values.get(obj2_key, float("inf")) for c in infeasible]
            self._scatter_infeasible = self._plot_scatter_layer(
                xs, ys, "gray", "x", 0.4, "infeasible",
            )

        if feasible:
            xs = [c.response_values.get(obj1_key, float("inf")) for c in feasible]
            ys = [c.response_values.get(obj2_key, float("inf")) for c in feasible]
            self._scatter_feasible = self._plot_scatter_layer(
                xs, ys, "#1f77b4", "o", 0.5, "feasible",
            )

        # パレートフロント
        pareto = self._compute_pareto_front()
        if pareto:
            px = [c.response_values.get(obj1_key, float("inf")) for c in pareto]
            py = [c.response_values.get(obj2_key, float("inf")) for c in pareto]
            self._scatter_pareto = self._plot_scatter_layer(
                px, py, "orange", "o", 1.0, "パレートフロント",
                s=80, edgecolors="black", linewidths=0.8, zorder=5,
            )

        # 最良解 (パレートの中で obj1 最小)
        if pareto:
            best = min(pareto, key=lambda c: c.response_values.get(obj1_key, float("inf")))
            bx = best.response_values.get(obj1_key, 0)
            by = best.response_values.get(obj2_key, 0)
            self._scatter_best = self._ax.scatter(
                [bx], [by], c="red", marker="*", s=200,
                edgecolors="black", linewidths=0.8, label="最良解", zorder=10,
            )

        obj1_label = self._obj1_combo.currentText()
        obj2_label = self._obj2_combo.currentText()
        self._ax.set_xlabel(obj1_label, fontsize=10)
        self._ax.set_ylabel(obj2_label, fontsize=10)
        self._ax.set_title("パレートフロント", fontsize=12)
        self._ax.legend(fontsize=8, loc="upper right")

    def _compute_pareto_front(self) -> List[OptimizationCandidate]:
        """feasible 候補からパレート非劣解を抽出。"""
        if not self._obj2_enabled.isChecked():
            return []

        obj1_key = self._obj1_combo.currentData()
        obj2_key = self._obj2_combo.currentData()

        feasible = [c for c in self._candidates if c.is_feasible]
        if not feasible:
            return []

        # 目的関数値を取得（minimization 前提）
        points = []
        for c in feasible:
            v1 = c.response_values.get(obj1_key, float("inf"))
            v2 = c.response_values.get(obj2_key, float("inf"))
            if v1 < float("inf") and v2 < float("inf"):
                points.append((v1, v2, c))

        if not points:
            return []

        # 非劣解の抽出
        pareto = []
        for i, (v1i, v2i, ci) in enumerate(points):
            dominated = False
            for j, (v1j, v2j, _) in enumerate(points):
                if i == j:
                    continue
                if v1j <= v1i and v2j <= v2i and (v1j < v1i or v2j < v2i):
                    dominated = True
                    break
            if not dominated:
                pareto.append(ci)

        return pareto

    # ------------------------------------------------------------------
    # 候補詳細表示
    # ------------------------------------------------------------------
    def _show_candidate_detail(self, cand: OptimizationCandidate) -> None:
        """候補詳細をテキストパネルに表示。"""
        label_map = self._build_param_label_map()

        lines = [f"候補 #{cand.iteration}"]
        lines.append("━" * 36)

        lines.append("パラメータ:")
        for key, val in cand.params.items():
            display = label_map.get(key, key)
            if key.startswith("floor_count_"):
                lines.append(f"  {display} = {int(val)} 本")
            else:
                lines.append(f"  {display} = {val:.6g}")

        # 応答値ラベルマッピング
        resp_labels = {k: lbl for k, lbl, _ in _OBJECTIVE_ITEMS}

        lines.append("")
        lines.append("応答値:")
        for key, val in cand.response_values.items():
            mark = ""
            if key in (self._constraint_widgets or {}):
                limit = self._constraint_widgets[key].value()
                if val <= limit:
                    mark = " OK"
                else:
                    mark = " NG"
            disp = resp_labels.get(key, key)
            lines.append(f"  {disp}: {val:.6g}{mark}")

        if cand.constraint_margins:
            lines.append("")
            min_margin = min(cand.constraint_margins.values())
            lines.append(f"最小マージン: {min_margin:+.1%}")

        self._detail_text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # JSON 保存 / 読込 / 画像保存
    # ------------------------------------------------------------------
    def _on_save_json(self) -> None:
        """最適化結果をJSONに保存。"""
        if not self._result:
            QMessageBox.information(self, "情報", "保存する結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "結果の保存先を選択", "unified_optimization_result.json",
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
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗:\n{e}")

    def _on_load_json(self) -> None:
        """JSONファイルから結果を読み込みプロットに表示。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "結果ファイルを選択", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            result = OptimizationResult.load_json(path)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            QMessageBox.warning(self, "読込エラー", f"ファイルの読み込みに失敗:\n{e}")
            return

        if not result.all_candidates:
            QMessageBox.information(self, "情報", "候補データが含まれていません。")
            return

        self._result = result
        self._candidates = list(result.all_candidates)

        # プロット再描画
        if result.config:
            self._init_plot(result.config)
        self._update_plot()

        # ボタン有効化
        self._export_btn.setEnabled(True)
        self._save_json_btn.setEnabled(True)
        self._save_plot_btn.setEnabled(True)
        if result.best:
            self._show_candidate_detail(result.best)
            self._apply_btn.setEnabled(True)

        self._enable_analysis_buttons()

        n_cands = len(result.all_candidates)
        feasible_count = len(result.feasible_candidates)
        self._progress_label.setText(
            f"JSON読込: {n_cands} 候補, 実行可能 {feasible_count}"
        )

    def _on_save_plot(self) -> None:
        """プロット画像を保存。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "画像の保存先を選択", "unified_optimization_plot.png",
            "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)",
        )
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=150, bbox_inches="tight")
            QMessageBox.information(self, "保存完了", f"画像を保存しました。\n{path}")
        except (OSError, ValueError) as e:
            logger.warning("画像保存に失敗: %s", e)
            QMessageBox.warning(self, "エラー", f"画像保存に失敗:\n{e}")

    # ------------------------------------------------------------------
    # 分析ボタン
    # ------------------------------------------------------------------
    def _build_analysis_buttons(self, layout: QVBoxLayout) -> None:
        """感度解析・収束診断などの分析ボタン行を構築。"""
        lbl = QLabel("分析")
        f = lbl.font()
        f.setBold(True)
        lbl.setFont(f)
        lbl.setStyleSheet("color: gray;")
        layout.addWidget(lbl)

        row1 = QHBoxLayout()
        self._sensitivity_btn = QPushButton("感度解析")
        self._sensitivity_btn.setEnabled(False)
        self._sensitivity_btn.setToolTip("最良解周りのパラメータ感度をOAT法で解析")
        row1.addWidget(self._sensitivity_btn)

        self._sobol_btn = QPushButton("Sobol解析")
        self._sobol_btn.setEnabled(False)
        self._sobol_btn.setToolTip("Sobolグローバル感度解析（交互作用込み）")
        row1.addWidget(self._sobol_btn)

        self._diagnostics_btn = QPushButton("収束診断")
        self._diagnostics_btn.setEnabled(False)
        self._diagnostics_btn.setToolTip("収束品質スコア・推奨アクション表示")
        row1.addWidget(self._diagnostics_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._correlation_btn = QPushButton("相関分析")
        self._correlation_btn.setEnabled(False)
        self._correlation_btn.setToolTip("パラメータ間の相関行列ヒートマップ")
        row2.addWidget(self._correlation_btn)

        self._heatmap_btn = QPushButton("空間ヒートマップ")
        self._heatmap_btn.setEnabled(False)
        self._heatmap_btn.setToolTip("パラメータ空間の探索密度を可視化")
        row2.addWidget(self._heatmap_btn)

        self._pareto_btn = QPushButton("Pareto Front")
        self._pareto_btn.setEnabled(False)
        self._pareto_btn.setToolTip("多目的結果のParetoフロント表示")
        row2.addWidget(self._pareto_btn)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._log_btn = QPushButton("評価ログ")
        self._log_btn.setEnabled(False)
        self._log_btn.setToolTip("全評価履歴をCSVに出力")
        row3.addWidget(self._log_btn)

        self._html_report_btn = QPushButton("HTMLレポート")
        self._html_report_btn.setEnabled(False)
        self._html_report_btn.setToolTip("最適化結果をHTMLレポートとして出力")
        row3.addWidget(self._html_report_btn)

        self._comparison_btn = QPushButton("結果比較")
        self._comparison_btn.setToolTip("複数JSON結果の比較表示")
        row3.addWidget(self._comparison_btn)
        layout.addLayout(row3)

    def _enable_analysis_buttons(self) -> None:
        """結果に応じて分析ボタンを有効化。"""
        has_result = self._result is not None
        has_best = has_result and self._result.best is not None
        has_config = has_result and self._result.config is not None
        n_cands = len(self._candidates)
        n_params = 0
        if has_config:
            n_params = len(self._result.config.parameters)

        self._sensitivity_btn.setEnabled(has_best and has_config)
        self._sobol_btn.setEnabled(has_config and n_params >= 1)
        self._diagnostics_btn.setEnabled(has_result and n_cands >= 3)
        self._correlation_btn.setEnabled(
            has_result and n_cands >= 3 and n_params >= 2
        )
        self._heatmap_btn.setEnabled(
            has_result and has_config and n_params >= 2 and n_cands >= 3
        )
        self._pareto_btn.setEnabled(has_result and n_cands >= 1)
        self._log_btn.setEnabled(has_result and n_cands >= 1)
        self._html_report_btn.setEnabled(has_result and n_cands >= 1)

    # ------------------------------------------------------------------
    # 分析アクション
    # ------------------------------------------------------------------
    def _on_run_sensitivity(self) -> None:
        """最良解周りの OAT 感度解析を実行。"""
        if not self._result or not self._result.best or not self._result.config:
            return

        config = self._result.config
        best_params = self._result.best.params
        evaluate_fn = self._get_or_create_evaluator(config)

        try:
            sensitivity = compute_sensitivity(
                evaluate_fn=evaluate_fn,
                best_params=best_params,
                parameters=config.parameters,
                objective_key=config.objective_key,
            )
            sensitivity.objective_label = config.objective_label
        except Exception as exc:
            logger.warning("感度解析に失敗: %s", exc, exc_info=True)
            QMessageBox.warning(self, "感度解析エラー", str(exc))
            return

        dlg = SensitivityDialog(sensitivity, parent=self)
        dlg.exec()

    def _on_run_sobol(self) -> None:
        """Sobol グローバル感度解析を実行。"""
        if not self._result or not self._result.config:
            return

        config = self._result.config
        n_params = len(config.parameters)
        n_base = 64
        n_evals = n_base * (2 * n_params + 2)

        reply = QMessageBox.question(
            self, "Sobol感度解析",
            f"Sobol分散ベース感度解析を実行します。\n\n"
            f"パラメータ数: {n_params}\n"
            f"推定評価回数: {n_evals} 回\n\n"
            f"実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        evaluate_fn = self._get_or_create_evaluator(config)

        try:
            sobol = compute_sobol_sensitivity(
                evaluate_fn=evaluate_fn,
                parameters=config.parameters,
                objective_key=config.objective_key,
                n_samples=n_base,
                objective_label=config.objective_label,
            )
        except Exception as exc:
            logger.warning("Sobol解析に失敗: %s", exc, exc_info=True)
            QMessageBox.warning(self, "Sobol解析エラー", str(exc))
            return

        dlg = SobolDialog(sobol, parent=self)
        dlg.exec()

    def _on_show_diagnostics(self) -> None:
        """収束品質診断ダイアログを表示。"""
        if not self._result:
            return
        diag = compute_convergence_diagnostics(self._result)
        if diag is None:
            QMessageBox.information(
                self, "診断不可", "候補数が不足しているため診断できません。"
            )
            return
        dlg = DiagnosticsDialog(diag, parent=self)
        dlg.exec()

    def _on_show_correlation(self) -> None:
        """パラメータ相関分析ダイアログを表示。"""
        if not self._result:
            return
        corr = compute_correlation_analysis(self._result)
        if corr is None:
            QMessageBox.information(
                self, "相関分析",
                "候補数またはパラメータ数が不足しています。\n"
                "（3候補以上・2パラメータ以上が必要）",
            )
            return
        dlg = CorrelationDialog(corr, parent=self)
        dlg.exec()

    def _on_show_heatmap(self) -> None:
        """パラメータ空間ヒートマップを表示。"""
        if not self._result or not self._result.config:
            return
        if len(self._result.config.parameters) < 2:
            QMessageBox.information(
                self, "情報", "ヒートマップには2パラメータ以上必要です。"
            )
            return
        dlg = HeatmapDialog(self._result, parent=self)
        dlg.exec()

    def _on_show_pareto(self) -> None:
        """Pareto front ダイアログを表示。"""
        if not self._result:
            return
        dlg = ParetoDialog(self._result, parent=self)
        dlg.exec()

    def _on_export_log(self) -> None:
        """全評価履歴をCSVログとして出力。"""
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "評価ログ出力先を選択", "optimization_log.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            export_optimization_log(self._result, path)
            QMessageBox.information(
                self, "評価ログ出力完了",
                f"全{len(self._result.all_candidates)}件の評価履歴を出力:\n{path}",
            )
        except Exception as exc:
            logger.exception("評価ログ出力エラー")
            QMessageBox.warning(self, "出力エラー", str(exc))

    def _on_export_html_report(self) -> None:
        """最適化結果をHTMLレポートとして出力。"""
        if not self._result or not self._result.all_candidates:
            QMessageBox.information(self, "情報", "レポート出力する結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "HTMLレポート出力先を選択", "unified_optimization_report.html",
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return

        try:
            from app.services.report_generator import generate_optimization_report
            generate_optimization_report(
                result=self._result,
                output_path=path,
                include_charts=True,
                title="統合最適化レポート",
            )
            QMessageBox.information(
                self, "HTMLレポート出力完了",
                f"最適化レポートを出力しました。\n{path}",
            )
        except Exception as exc:
            logger.exception("HTMLレポート出力エラー")
            QMessageBox.warning(
                self, "エラー",
                f"レポートの出力に失敗しました:\n{exc}",
            )

    def _on_show_comparison(self) -> None:
        """結果比較ダイアログを表示。"""
        dlg = ComparisonDialog(parent=self)
        dlg.exec()

    def _get_or_create_evaluator(self, config: OptimizationConfig):
        """感度解析等で使う評価関数を構築。SNAP接続時は実解析、未接続時はモック。"""
        if self._base_case and self._snap_exe_path:
            evaluate_fn = create_unified_evaluator(
                snap_exe_path=self._snap_exe_path,
                base_case=self._base_case,
                param_ranges=config.parameters,
                snap_work_dir=self._snap_work_dir,
                timeout=config.snap_timeout,
                damper_def_name=(
                    self._selected_damper_def().name
                    if self._selected_damper_def() else ""
                ),
            )
            if evaluate_fn is not None:
                return evaluate_fn

        from app.services.optimizer import _mock_evaluate
        base = {}
        if config.base_case and config.base_case.result_summary:
            base = config.base_case.result_summary
        return lambda params: _mock_evaluate(
            params, base, config.objective_key
        )

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """ダイアログ終了時にワーカースレッドを安全に停止。"""
        self._optimizer.cancel()
        super().closeEvent(event)

    def _selected_damper_def(self) -> Optional[DamperDefinition]:
        """選択中のダンパー定義を返す。"""
        name = self._def_combo.currentData()
        if name is None:
            return None
        for dd in self._damper_defs:
            if dd.name == name:
                return dd
        return None

    @staticmethod
    def _suggest_bound(current: float, lower: bool) -> float:
        """現在値から上限/下限を自動提案。"""
        if current == 0:
            return -1.0 if lower else 1.0
        abs_val = abs(current)
        margin = abs_val * 0.5
        if lower:
            return max(0, current - margin)
        else:
            return current + margin

    @staticmethod
    def _suggest_step(lo: float, hi: float) -> float:
        """範囲から適切なステップ幅を推定。"""
        span = hi - lo
        if span <= 0:
            return 1.0
        # 約10分割を目安
        step = span / 10.0
        # 切りのいい値に丸める
        if step >= 100:
            return round(step / 100) * 100
        elif step >= 10:
            return round(step / 10) * 10
        elif step >= 1:
            return round(step)
        elif step >= 0.1:
            return round(step, 1)
        else:
            return round(step, 2)
