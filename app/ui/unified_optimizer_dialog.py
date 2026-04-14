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

import time
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
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
from app.models.s8i_parser import parse_s8i, DamperDefinition
from app.services.optimizer import (
    DamperOptimizer,
    OptimizationCandidate,
    OptimizationConfig,
    OptimizationResult,
    ParameterRange,
)
from app.services.snap_evaluator import build_floor_rd_map, create_unified_evaluator
from .damper_field_data import get_damper_field_labels, get_damper_field_units
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
    pass


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
        self._build_run_controls(left_layout)
        left_layout.addStretch()

        # 右ペイン
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)
        self._build_plot(right_layout)
        self._build_detail_panel(right_layout)

        splitter.addWidget(left)
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

            # 種別・番号系（整数の種類コードなど）はスキップ
            if field_idx_1based <= 5 and current == int(current):
                # 種別, k-DB番号, 型番, モデル等は最適化対象外
                if "種別" in label_text or "k-DB" in label_text or "番号" in label_text:
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
        """制約条件パネル。"""
        group = QGroupBox("制約条件")
        gl = QFormLayout(group)

        self._constraint_widgets: Dict[str, QDoubleSpinBox] = {}

        defaults = {
            "max_drift": (0.01, "最大層間変形角 <"),
            "shear_coeff": (0.30, "せん断力係数 <"),
        }

        if self._criteria:
            if self._criteria.max_drift:
                defaults["max_drift"] = (self._criteria.max_drift, "最大層間変形角 <")
            if self._criteria.max_shear_coeff:
                defaults["shear_coeff"] = (self._criteria.max_shear_coeff, "せん断力係数 <")

        for key, (val, label) in defaults.items():
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(0, 10)
            spin.setValue(val)
            gl.addRow(f"{label}", spin)
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
        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._canvas, 2)

        self._scatter_feasible = None
        self._scatter_infeasible = None
        self._scatter_pareto = None
        self._scatter_best = None

    def _build_detail_panel(self, layout: QVBoxLayout) -> None:
        """候補詳細パネル。"""
        group = QGroupBox("候補詳細")
        gl = QVBoxLayout(group)
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setMaximumHeight(180)
        gl.addWidget(self._detail_text)

        self._apply_btn = QPushButton("この候補を .s8i に適用")
        self._apply_btn.setEnabled(False)
        gl.addWidget(self._apply_btn)

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
        self._obj2_enabled.toggled.connect(self._on_obj2_toggled)
        self._def_combo.currentIndexChanged.connect(self._on_damper_def_changed)
        self._iter_spin.valueChanged.connect(self._update_estimate)
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)

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
        sec_per_eval = 30  # SNAP 1回の想定
        total_sec = n * sec_per_eval
        mins = total_sec // 60
        self._estimate_label.setText(
            f"推定 {n} 回の評価 (約 {mins} 分)"
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
        elapsed_str = time.strftime("%M:%S", time.gmtime(elapsed))
        if current > 0:
            remaining = elapsed / current * (total - current)
            remain_str = time.strftime("%M:%S", time.gmtime(remaining))
        else:
            remain_str = "--:--"
        self._progress_label.setText(
            f"{current}/{total}  経過 {elapsed_str}  残り {remain_str}"
        )

    def _on_candidate(self, cand: OptimizationCandidate) -> None:
        """候補が1つ評価されるたびに呼ばれる。"""
        self._candidates.append(cand)
        self._update_plot()

    def _on_finished(self, result: OptimizationResult) -> None:
        self._result = result
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

        if result.best:
            self._show_candidate_detail(result.best)
            self._apply_btn.setEnabled(True)

        elapsed = time.time() - self._start_time
        elapsed_str = time.strftime("%M:%S", time.gmtime(elapsed))
        feasible_count = sum(1 for c in self._candidates if c.is_feasible)
        self._progress_label.setText(
            f"完了 ({len(self._candidates)} 候補, "
            f"実行可能 {feasible_count}, "
            f"{elapsed_str})"
        )

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

    # ------------------------------------------------------------------
    # パラメータ収集
    # ------------------------------------------------------------------
    def _collect_parameters(self) -> List[ParameterRange]:
        """チェックされたパラメータを ParameterRange リストに変換。"""
        params: List[ParameterRange] = []

        dd = self._selected_damper_def()

        # 物理パラメータ
        for row_info in self._field_rows:
            if not row_info["cb"].isChecked():
                continue
            lo = row_info["lo_spin"].value()
            hi = row_info["hi_spin"].value()
            if lo >= hi:
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
            snap_timeout=300,
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

        def log_cb(msg: str) -> None:
            logger.info(msg)

        return create_unified_evaluator(
            snap_exe_path=self._snap_exe_path,
            base_case=self._base_case,
            param_ranges=params,
            log_callback=log_cb,
            snap_work_dir=self._snap_work_dir,
            timeout=300,
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
        """候補データでプロットを更新。"""
        if not self._candidates:
            return

        is_multi = self._obj2_enabled.isChecked()
        self._ax.clear()

        if is_multi:
            self._update_pareto_plot()
        else:
            self._update_convergence_plot()

        self._fig.tight_layout()
        self._canvas.draw()

    def _update_convergence_plot(self) -> None:
        """1目的: 収束プロット。"""
        obj_key = self._obj1_combo.currentData()

        feasible = [(c.iteration, c.response_values.get(obj_key, float("inf")))
                     for c in self._candidates if c.is_feasible]
        infeasible = [(c.iteration, c.response_values.get(obj_key, float("inf")))
                       for c in self._candidates if not c.is_feasible]

        if infeasible:
            xs, ys = zip(*infeasible)
            self._scatter_infeasible = self._ax.scatter(
                xs, ys, c="gray", marker="x", alpha=0.5,
                label="infeasible", picker=True, pickradius=5,
            )

        if feasible:
            xs, ys = zip(*feasible)
            self._scatter_feasible = self._ax.scatter(
                xs, ys, c="#1f77b4", marker="o", alpha=0.7,
                label="feasible", picker=True, pickradius=5,
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
            self._scatter_infeasible = self._ax.scatter(
                xs, ys, c="gray", marker="x", alpha=0.4,
                label="infeasible", picker=True, pickradius=5,
            )

        if feasible:
            xs = [c.response_values.get(obj1_key, float("inf")) for c in feasible]
            ys = [c.response_values.get(obj2_key, float("inf")) for c in feasible]
            self._scatter_feasible = self._ax.scatter(
                xs, ys, c="#1f77b4", marker="o", alpha=0.5,
                label="feasible", picker=True, pickradius=5,
            )

        # パレートフロント
        pareto = self._compute_pareto_front()
        if pareto:
            px = [c.response_values.get(obj1_key, float("inf")) for c in pareto]
            py = [c.response_values.get(obj2_key, float("inf")) for c in pareto]
            self._scatter_pareto = self._ax.scatter(
                px, py, c="orange", marker="o", s=80, edgecolors="black",
                linewidths=0.8, label="パレートフロント", picker=True, pickradius=5,
                zorder=5,
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
        lines = [f"候補 #{cand.iteration}"]
        lines.append("━" * 36)

        lines.append("パラメータ:")
        for key, val in cand.params.items():
            if key.startswith("floor_count_"):
                fk = key.replace("floor_count_", "")
                lines.append(f"  {fk} = {int(val)} 本")
            else:
                lines.append(f"  {key} = {val:.6g}")

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
            lines.append(f"  {key}: {val:.6g}{mark}")

        if cand.constraint_margins:
            lines.append("")
            min_margin = min(cand.constraint_margins.values())
            lines.append(f"最小マージン: {min_margin:+.1%}")

        self._detail_text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------
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
