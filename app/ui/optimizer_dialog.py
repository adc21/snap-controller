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
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
from app.services.optimizer import (
    DamperOptimizer,
    OptimizationCandidate,
    OptimizationConfig,
    OptimizationResult,
    ParameterRange,
)
from app.services.snap_evaluator import create_snap_evaluator
from .theme import ThemeManager, MPL_STYLES

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
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
}


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _ConvergenceCanvas(FigureCanvas):
    """収束グラフ用の matplotlib キャンバス。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(4, 3), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._base_case = base_case
        self._criteria = criteria
        self._snap_exe_path = snap_exe_path
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
        btn_row.addStretch()

        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _connect_signals(self) -> None:
        self._run_btn.clicked.connect(self._start_optimization)
        self._cancel_btn.clicked.connect(self._cancel_optimization)
        self._apply_btn.clicked.connect(self._apply_best)
        self._optimizer.progress.connect(self._on_progress)
        self._optimizer.candidate_found.connect(self._on_candidate)
        self._optimizer.optimization_finished.connect(self._on_finished)

    # ------------------------------------------------------------------
    # Parameter range widgets
    # ------------------------------------------------------------------

    def _on_damper_type_changed(self, dtype: str) -> None:
        """ダンパー種類変更時にパラメータ範囲ウィジェットを更新します。"""
        # 既存ウィジェットを削除
        for w in self._param_widgets:
            for widget in w.values():
                if hasattr(widget, "deleteLater"):
                    widget.deleteLater()
        self._param_widgets.clear()

        # 新しいパラメータ行を追加
        while self._param_layout.count():
            child = self._param_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                while child.layout().count():
                    sub = child.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

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

    def _on_method_changed(self, index: int) -> None:
        method = self._method_combo.currentData()
        self._iter_spin.setEnabled(method in ("random", "bayesian"))

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

        return OptimizationConfig(
            objective_key=obj_key,
            objective_label=obj_label,
            parameters=params,
            method=self._method_combo.currentData(),
            max_iterations=self._iter_spin.value(),
            criteria=self._criteria,
            damper_type=self._damper_combo.currentText(),
            base_case=self._base_case,
        )

    def _start_optimization(self) -> None:
        config = self._build_config()

        if not config.parameters:
            QMessageBox.warning(self, "設定エラー", "探索パラメータが設定されていません。")
            return

        # UIリセット
        self._result_table.setRowCount(0)
        self._convergence_history.clear()
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._apply_btn.setEnabled(False)
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
            )
            if snap_evaluator:
                evaluate_fn = snap_evaluator
                self._result_summary.setText(
                    "SNAP実行モードで最適化を実行中..."
                )
            else:
                self._result_summary.setText(
                    "モック評価モードで最適化を実行中..."
                    "（SNAP.exe またはモデルファイルが見つかりません）"
                )
        else:
            self._result_summary.setText(
                "モック評価モードで最適化を実行中..."
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

        # サマリー
        if result.best:
            obj_label = result.config.objective_label if result.config else "目的関数"
            self._result_summary.setText(
                f"最良解: {obj_label} = {result.best.objective_value:.6g}  |  "
                f"制約満足: {len(result.feasible_candidates)} / {len(result.all_candidates)} 点"
            )
            self._apply_btn.setEnabled(True)
        else:
            self._result_summary.setText(
                "制約を満たす解が見つかりませんでした。"
                "パラメータ範囲や制約条件を見直してください。"
            )

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
        """収束グラフを描画します。"""
        ax = self._conv_canvas.ax
        ax.clear()

        if not result.all_candidates:
            ax.text(0.5, 0.5, "データなし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            self._conv_canvas.draw()
            return

        # 全候補の目的関数値の推移
        all_vals = [c.objective_value for c in result.all_candidates if c.is_feasible]
        if not all_vals:
            ax.text(0.5, 0.5, "制約を満たす候補なし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            self._conv_canvas.draw()
            return

        # 累積最小値
        best_so_far = []
        current_best = float("inf")
        for v in all_vals:
            current_best = min(current_best, v)
            best_so_far.append(current_best)

        iterations = list(range(1, len(all_vals) + 1))

        ax.scatter(iterations, all_vals,
                   s=4, alpha=0.3, color="#1f77b4", label="各候補")
        ax.plot(iterations, best_so_far,
                color="#d62728", linewidth=2, label="累積最良値")

        obj_label = result.config.objective_label if result.config else "目的関数"
        ax.set_xlabel("評価回数", fontsize=8)
        ax.set_ylabel(obj_label, fontsize=8)
        ax.set_title("収束履歴", fontsize=9)
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=7)
        ax.grid(linestyle="--", alpha=0.3)

        self._conv_canvas.fig.tight_layout()
        self._conv_canvas.draw()

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _apply_best(self) -> None:
        """最良解をケースに適用して閉じます。"""
        if self._result and self._result.best:
            self.accept()
        else:
            QMessageBox.information(self, "情報", "適用可能な最良解がありません。")
