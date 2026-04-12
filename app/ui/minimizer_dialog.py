"""ダンパー本数最小化ダイアログ。

.s8iファイルのダンパー本数(quantity)を自動的に変更しながら
SNAPをループ実行し、性能基準を満たす最小本数を探索する。
12種のアルゴリズムから選択可能。
"""

from __future__ import annotations

import csv
import logging
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QFont, QStandardItem
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.services.damper_count_minimizer import (
    EvaluateFn,
    EvaluationResult,
    MinimizationResult,
    MinimizationStep,
    STRATEGIES,
    STRATEGY_CATEGORIES,
    minimize_damper_count,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ワーカースレッド
# ---------------------------------------------------------------------------


class _CancelledError(Exception):
    """ユーザーによる中止を示す例外。"""


class _MinimizerWorker(QThread):
    """バックグラウンドで最小化を実行するワーカースレッド。"""

    stepCompleted = Signal(object)  # MinimizationStep
    finished_ = Signal(object)     # MinimizationResult
    errorOccurred = Signal(str)

    def __init__(
        self,
        floor_keys: List[str],
        max_quantities: Dict[str, int],
        initial_quantities: Dict[str, int],
        evaluate_fn: EvaluateFn,
        strategy: str,
        max_iterations: int = 200,
        extra_kwargs: Optional[Dict] = None,
        parent: Optional[QThread] = None,
    ) -> None:
        super().__init__(parent)
        self._floor_keys = floor_keys
        self._max_quantities = max_quantities
        self._initial_quantities = initial_quantities
        self._evaluate_fn = evaluate_fn
        self._strategy = strategy
        self._max_iterations = max_iterations
        self._extra_kwargs = extra_kwargs or {}
        self._stop_requested = False

    def request_stop(self) -> None:
        """中止リクエスト。次の progress_cb 呼び出し時に例外で停止する。"""
        self._stop_requested = True

    def _progress_cb(self, step: MinimizationStep) -> None:
        """進捗コールバック。中止リクエスト時は例外で探索ループを脱出する。"""
        if self._stop_requested:
            raise _CancelledError("ユーザーにより中止されました")
        self.stepCompleted.emit(step)

    def run(self) -> None:
        try:
            kwargs: dict = {}
            if self._strategy in ("ga", "sa", "pso", "de", "random"):
                kwargs["max_iterations"] = self._max_iterations
            if self._strategy in ("ga", "pso", "de"):
                kwargs["population_size"] = min(30, max(10, self._max_iterations // 3))
            if self._strategy == "bayesian":
                kwargs["max_iterations"] = self._max_iterations
                kwargs["n_initial"] = min(10, self._max_iterations // 3)
            # extra_kwargs でユーザー指定パラメータを上書き
            kwargs.update(self._extra_kwargs)

            result = minimize_damper_count(
                floor_keys=self._floor_keys,
                max_quantities=self._max_quantities,
                evaluate_fn=self._evaluate_fn,
                strategy=self._strategy,
                initial_quantities=self._initial_quantities,
                progress_cb=self._progress_cb,
                **kwargs,
            )
            self.finished_.emit(result)
        except _CancelledError:
            self.errorOccurred.emit("中止しました")
        except Exception as exc:
            self.errorOccurred.emit(str(exc))


# ---------------------------------------------------------------------------
# メインダイアログ
# ---------------------------------------------------------------------------


class MinimizerDialog(QDialog):
    """ダンパー本数最小化ダイアログ。"""

    minimizationCompleted = Signal(object)  # MinimizationResult

    def __init__(
        self,
        floor_keys: List[str],
        current_quantities: Dict[str, int],
        max_quantities: Dict[str, int],
        evaluate_fn: Optional[EvaluateFn] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._floor_keys = floor_keys
        self._current_quantities = current_quantities
        self._max_quantities = max_quantities
        self._evaluate_fn = evaluate_fn
        self._is_snap = evaluate_fn is not None
        self._worker: Optional[_MinimizerWorker] = None
        self._result: Optional[MinimizationResult] = None

        # リアルタイムプロット用データ
        self._plot_counts: List[int] = []
        self._plot_margins: List[float] = []
        self._plot_feasible: List[bool] = []
        self._plot_summaries: List[Dict[str, float]] = []

        self.setWindowTitle("ダンパー本数最小化")
        self.setMinimumSize(900, 650)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # === 上部: 設定パネル ===
        settings_layout = QHBoxLayout()

        # 左: 階別現在本数テーブル
        floor_group = QGroupBox("ダンパー配置（.s8iから読取）")
        floor_layout = QVBoxLayout(floor_group)
        self._floor_table = QTableWidget()
        self._floor_table.setColumnCount(3)
        self._floor_table.setHorizontalHeaderLabels(["階", "現在本数", "上限"])
        self._floor_table.setRowCount(len(self._floor_keys))
        self._floor_table.horizontalHeader().setStretchLastSection(True)
        self._floor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._floor_table.setMaximumHeight(180)

        for i, fk in enumerate(self._floor_keys):
            self._floor_table.setItem(i, 0, QTableWidgetItem(fk))
            qty = self._current_quantities.get(fk, 0)
            self._floor_table.setItem(i, 1, QTableWidgetItem(str(qty)))
            max_q = self._max_quantities.get(fk, qty)
            self._floor_table.setItem(i, 2, QTableWidgetItem(str(max_q)))

        floor_layout.addWidget(self._floor_table)
        total_label = QLabel(
            f"合計: {sum(self._current_quantities.values())}本  "
            f"（{len(self._floor_keys)}階）"
        )
        total_label.setStyleSheet("font-weight: bold;")
        floor_layout.addWidget(total_label)
        settings_layout.addWidget(floor_group, stretch=1)

        # 右: アルゴリズム選択 + パラメータ
        algo_group = QGroupBox("探索設定")
        algo_layout = QVBoxLayout(algo_group)

        # アルゴリズム選択（カテゴリ別グループ表示）
        algo_layout.addWidget(QLabel("探索戦略:"))
        self._combo_strategy = QComboBox()
        model = self._combo_strategy.model()
        first_selectable_idx = -1
        for cat_name, cat_keys in STRATEGY_CATEGORIES.items():
            # カテゴリヘッダー（選択不可）
            header_item = QStandardItem(f"── {cat_name} ──")
            header_item.setEnabled(False)
            f = header_item.font()
            f.setBold(True)
            header_item.setFont(f)
            model.appendRow(header_item)
            # 戦略アイテム
            for key in cat_keys:
                label = STRATEGIES[key]
                self._combo_strategy.addItem(f"  {label}", key)
                if first_selectable_idx < 0:
                    first_selectable_idx = model.rowCount() - 1
        if first_selectable_idx >= 0:
            self._combo_strategy.setCurrentIndex(first_selectable_idx)
        algo_layout.addWidget(self._combo_strategy)

        # 反復回数
        iter_layout = QHBoxLayout()
        iter_layout.addWidget(QLabel("最大反復数:"))
        self._iter_spin = QSpinBox()
        self._iter_spin.setRange(10, 5000)
        self._iter_spin.setValue(100)
        iter_layout.addWidget(self._iter_spin)
        algo_layout.addLayout(iter_layout)

        # 評価方式表示
        eval_layout = QHBoxLayout()
        eval_layout.addWidget(QLabel("評価方式:"))
        self._lbl_eval_mode = QLabel()
        if self._is_snap:
            self._lbl_eval_mode.setText("SNAP実解析")
            self._lbl_eval_mode.setStyleSheet("color: #4caf50; font-weight: bold;")
        else:
            self._lbl_eval_mode.setText("未接続")
            self._lbl_eval_mode.setStyleSheet("color: #f44336; font-weight: bold;")
        eval_layout.addWidget(self._lbl_eval_mode)
        algo_layout.addLayout(eval_layout)

        # 詳細パラメータパネル（戦略ごとに表示/非表示切替）
        self._adv_group = QGroupBox("詳細パラメータ")
        adv_layout = QVBoxLayout(self._adv_group)

        # 集団サイズ (GA, PSO, DE)
        pop_row = QHBoxLayout()
        pop_row.addWidget(QLabel("集団サイズ:"))
        self._spin_pop = QSpinBox()
        self._spin_pop.setRange(5, 200)
        self._spin_pop.setValue(30)
        self._spin_pop.setToolTip("GA/PSO/DEの集団サイズ（大きいほど探索精度↑、計算時間↑）")
        pop_row.addWidget(self._spin_pop)
        adv_layout.addLayout(pop_row)
        self._pop_row_widgets = [pop_row.itemAt(i).widget() for i in range(pop_row.count()) if pop_row.itemAt(i).widget()]

        # 初期温度 (SA)
        temp_row = QHBoxLayout()
        temp_row.addWidget(QLabel("初期温度:"))
        self._spin_temp = QDoubleSpinBox()
        self._spin_temp.setRange(1.0, 10000.0)
        self._spin_temp.setValue(100.0)
        self._spin_temp.setDecimals(0)
        self._spin_temp.setToolTip("SA初期温度（高いほど序盤の探索範囲が広い）")
        temp_row.addWidget(self._spin_temp)
        adv_layout.addLayout(temp_row)
        self._temp_row_widgets = [temp_row.itemAt(i).widget() for i in range(temp_row.count()) if temp_row.itemAt(i).widget()]

        # 初期サンプル数 (Bayesian)
        init_row = QHBoxLayout()
        init_row.addWidget(QLabel("初期サンプル:"))
        self._spin_n_initial = QSpinBox()
        self._spin_n_initial.setRange(3, 100)
        self._spin_n_initial.setValue(10)
        self._spin_n_initial.setToolTip("ベイズ最適化の初期ランダムサンプル数")
        init_row.addWidget(self._spin_n_initial)
        adv_layout.addLayout(init_row)
        self._init_row_widgets = [init_row.itemAt(i).widget() for i in range(init_row.count()) if init_row.itemAt(i).widget()]

        # DE適応F/CR (jDE)
        self._chk_de_adaptive = QCheckBox("自己適応 F/CR (jDE)")
        self._chk_de_adaptive.setChecked(True)
        self._chk_de_adaptive.setToolTip("個体ごとにF, CRを自動調整（Brest et al. 2006）")
        adv_layout.addWidget(self._chk_de_adaptive)

        algo_layout.addWidget(self._adv_group)

        # 戦略切替時にパネルを更新
        self._combo_strategy.currentIndexChanged.connect(self._on_strategy_changed)
        self._on_strategy_changed()

        algo_layout.addStretch()
        settings_layout.addWidget(algo_group, stretch=1)
        root.addLayout(settings_layout)

        # === 実行ボタン + 中止ボタン + プログレス ===
        exec_layout = QHBoxLayout()
        self._btn_run = QPushButton("実行")
        self._btn_run.setFont(QFont("", -1, QFont.Weight.Bold))
        self._btn_run.clicked.connect(self._on_run)
        exec_layout.addWidget(self._btn_run)

        self._btn_cancel = QPushButton("中止")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        exec_layout.addWidget(self._btn_cancel)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        exec_layout.addWidget(self._progress, stretch=1)

        self._lbl_status = QLabel("")
        exec_layout.addWidget(self._lbl_status)
        root.addLayout(exec_layout)

        # === 結果エリア（タブ: チャート / テーブル） ===
        result_group = QGroupBox("結果")
        result_layout = QVBoxLayout(result_group)

        self._lbl_summary = QLabel("")
        self._lbl_summary.setWordWrap(True)
        result_layout.addWidget(self._lbl_summary)

        self._tabs = QTabWidget()

        # タブ1: リアルタイムチャート
        chart_widget = QWidget()
        chart_layout = QVBoxLayout(chart_widget)
        self._fig = Figure(figsize=(8, 4))
        self._canvas = FigureCanvas(self._fig)
        chart_layout.addWidget(self._canvas)
        self._tabs.addTab(chart_widget, "本数 vs 応答")

        # タブ2: 層別応答チャート
        floor_chart_widget = QWidget()
        floor_chart_layout = QVBoxLayout(floor_chart_widget)
        self._fig_floor = Figure(figsize=(8, 4))
        self._canvas_floor = FigureCanvas(self._fig_floor)
        floor_chart_layout.addWidget(self._canvas_floor)
        self._tabs.addTab(floor_chart_widget, "層別応答")

        # タブ3: 建物立面ダイアグラム
        elev_widget = QWidget()
        elev_layout = QVBoxLayout(elev_widget)
        self._fig_elev = Figure(figsize=(4, 6))
        self._canvas_elev = FigureCanvas(self._fig_elev)
        elev_layout.addWidget(self._canvas_elev)
        self._tabs.addTab(elev_widget, "立面図")

        # タブ4: 結果テーブル
        self._table = QTableWidget()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tabs.addTab(self._table, "最終配置")

        result_layout.addWidget(self._tabs)
        root.addWidget(result_group, stretch=1)

        # === ボタン行 ===
        bottom = QHBoxLayout()
        self._btn_csv = QPushButton("CSV出力")
        self._btn_csv.setEnabled(False)
        self._btn_csv.clicked.connect(self._export_csv)
        bottom.addWidget(self._btn_csv)
        self._btn_copy = QPushButton("結果コピー")
        self._btn_copy.setEnabled(False)
        self._btn_copy.clicked.connect(self._copy_result)
        bottom.addWidget(self._btn_copy)
        bottom.addStretch()
        self._btn_close = QPushButton("閉じる")
        self._btn_close.clicked.connect(self.close)
        bottom.addWidget(self._btn_close)
        root.addLayout(bottom)

    # -------------------------------------------------------------------
    # 戦略切替
    # -------------------------------------------------------------------

    def _on_strategy_changed(self) -> None:
        """選択された戦略に応じて詳細パラメータの表示/非表示を切り替える。"""
        strategy = self._combo_strategy.currentData()
        has_pop = strategy in ("ga", "pso", "de")
        has_temp = strategy == "sa"
        has_init = strategy == "bayesian"
        has_de_adaptive = strategy == "de"
        # 反復回数はメタヒューリスティック系のみ
        has_iter = strategy in ("ga", "sa", "pso", "de", "random", "bayesian")

        for w in self._pop_row_widgets:
            w.setVisible(has_pop)
        for w in self._temp_row_widgets:
            w.setVisible(has_temp)
        for w in self._init_row_widgets:
            w.setVisible(has_init)
        self._chk_de_adaptive.setVisible(has_de_adaptive)
        self._iter_spin.setEnabled(has_iter)

        # 詳細グループ全体: 何も表示項目がなければ非表示
        self._adv_group.setVisible(has_pop or has_temp or has_init or has_de_adaptive)

    def _collect_extra_kwargs(self, strategy: str) -> dict:
        """UI上の詳細パラメータを kwargs として収集する。"""
        kw: dict = {}
        if strategy in ("ga", "pso", "de"):
            kw["population_size"] = self._spin_pop.value()
        if strategy == "sa":
            kw["initial_temp"] = self._spin_temp.value()
        if strategy == "bayesian":
            kw["n_initial"] = self._spin_n_initial.value()
        if strategy == "de":
            kw["adaptive"] = self._chk_de_adaptive.isChecked()
        return kw

    # -------------------------------------------------------------------
    # 実行
    # -------------------------------------------------------------------

    def _on_run(self) -> None:
        if self._evaluate_fn is None:
            QMessageBox.warning(
                self, "評価関数未接続",
                "評価関数が接続されていません。\n"
                "解析ケースを設定してから実行してください。",
            )
            return

        strategy = self._combo_strategy.currentData()

        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setVisible(True)
        self._lbl_status.setText("実行中...")
        self._lbl_summary.setText("")
        self._table.setRowCount(0)

        # プロットデータリセット
        self._plot_counts.clear()
        self._plot_margins.clear()
        self._plot_feasible.clear()
        self._plot_summaries.clear()
        self._fig.clear()
        self._fig_floor.clear()
        self._fig_elev.clear()
        self._canvas.draw()
        self._canvas_floor.draw()
        self._canvas_elev.draw()

        # 戦略固有パラメータを収集
        extra_kwargs = self._collect_extra_kwargs(strategy)

        self._worker = _MinimizerWorker(
            floor_keys=self._floor_keys,
            max_quantities=self._max_quantities,
            initial_quantities=self._current_quantities,
            evaluate_fn=self._evaluate_fn,
            strategy=strategy,
            max_iterations=self._iter_spin.value(),
            extra_kwargs=extra_kwargs,
            parent=self,
        )
        self._worker.stepCompleted.connect(self._on_step)
        self._worker.finished_.connect(self._on_finished)
        self._worker.errorOccurred.connect(self._on_error)
        self._worker.start()

    def _on_step(self, step: MinimizationStep) -> None:
        """各ステップのリアルタイム更新。"""
        feasible_text = "OK" if step.is_feasible else "NG"
        self._lbl_status.setText(
            f"[{step.action}] 合計={step.total_count}本  "
            f"基準={feasible_text}  マージン={step.worst_margin:+.4f}"
        )

        # プロットデータ蓄積
        self._plot_counts.append(step.total_count)
        self._plot_margins.append(step.worst_margin)
        self._plot_feasible.append(step.is_feasible)
        self._plot_summaries.append(dict(step.summary))

        # リアルタイムチャート更新（10ステップごと or 少ないデータ）
        if len(self._plot_counts) <= 20 or len(self._plot_counts) % 5 == 0:
            self._update_realtime_chart()

    def _on_cancel(self) -> None:
        """中止ボタン押下。"""
        if self._worker is not None:
            self._worker.request_stop()
            self._btn_cancel.setEnabled(False)
            self._lbl_status.setText("中止要求中...")

    def _on_finished(self, result: MinimizationResult) -> None:
        self._result = result
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._btn_csv.setEnabled(True)
        self._btn_copy.setEnabled(True)

        feasible_text = "OK" if result.is_feasible else "NG"
        eval_tag = "[SNAP]" if self._is_snap else "[モック]"
        self._lbl_status.setText("完了")
        self._lbl_summary.setText(
            f"{eval_tag} 戦略: {STRATEGIES.get(result.strategy, result.strategy)}  |  "
            f"最終合計: {result.final_count}本  |  "
            f"基準充足: {feasible_text}  |  "
            f"マージン: {result.final_margin:+.4f}  |  "
            f"評価回数: {result.evaluations}"
        )

        # 最終チャート更新
        self._update_realtime_chart()
        self._update_floor_chart(result)
        self._update_elevation_diagram(result)
        self._populate_result_table(result)

        self.minimizationCompleted.emit(result)
        self._worker = None

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        is_cancel = "中止" in msg
        self._lbl_status.setText("中止" if is_cancel else "エラー")
        if not is_cancel:
            QMessageBox.critical(self, "実行エラー", msg)
        self._worker = None

    # -------------------------------------------------------------------
    # チャート描画
    # -------------------------------------------------------------------

    def _update_realtime_chart(self) -> None:
        """本数 vs マージンのリアルタイムチャート。"""
        self._fig.clear()
        if not self._plot_counts:
            self._canvas.draw_idle()
            return

        try:
            ax1, ax2 = self._fig.subplots(1, 2)

            # 左: 合計本数 vs 評価番号
            iters = list(range(len(self._plot_counts)))
            ax1.plot(iters, self._plot_counts, "-", color="#1976d2", linewidth=1.5)
            for i, (it, c, f) in enumerate(zip(iters, self._plot_counts, self._plot_feasible)):
                color = "#4caf50" if f else "#f44336"
                marker = "o" if f else "x"
                ax1.plot(it, c, marker, color=color, markersize=4)
            ax1.set_xlabel("評価番号")
            ax1.set_ylabel("合計本数")
            ax1.set_title("本数推移", fontsize=10)
            ax1.grid(True, alpha=0.3)

            # 右: マージン推移
            ax2.plot(iters, self._plot_margins, "s-", color="#ff9800",
                     linewidth=1.2, markersize=3)
            ax2.axhline(0, color="#888", linestyle="--", linewidth=0.8)
            ax2.fill_between(iters, self._plot_margins, 0, alpha=0.1,
                             where=[m >= 0 for m in self._plot_margins], color="#4caf50")
            ax2.fill_between(iters, self._plot_margins, 0, alpha=0.1,
                             where=[m < 0 for m in self._plot_margins], color="#f44336")
            ax2.set_xlabel("評価番号")
            ax2.set_ylabel("マージン")
            ax2.set_title("マージン推移", fontsize=10)
            ax2.grid(True, alpha=0.3)

            self._fig.tight_layout()
        except Exception:
            logger.warning("リアルタイムチャートの描画に失敗", exc_info=True)
        self._canvas.draw_idle()

    def _update_floor_chart(self, result: MinimizationResult) -> None:
        """層別応答チャート（最終結果）。"""
        self._fig_floor.clear()
        if not result.history:
            self._canvas_floor.draw()
            return

        # 最終ステップの応答値を取得
        final_step = result.history[-1]
        if not final_step.summary:
            self._canvas_floor.draw()
            return

        try:
            ax = self._fig_floor.add_subplot(111)

            # 各階の本数を棒グラフ
            floors = list(result.final_quantities.keys())
            counts = [result.final_quantities.get(f, 0) for f in floors]
            colors = ["#1976d2" if c > 0 else "#ccc" for c in counts]

            x = range(len(floors))
            ax.bar(x, counts, color=colors, edgecolor="#333", linewidth=0.5)
            ax.set_xticks(list(x))
            ax.set_xticklabels(floors, fontsize=8)
            ax.set_ylabel("ダンパー本数")
            ax.set_title("最終配置（階別）", fontsize=10)
            ax.grid(True, alpha=0.3, axis="y")

            from matplotlib.ticker import MaxNLocator
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))

            self._fig_floor.tight_layout()
        except Exception:
            logger.warning("層別チャートの描画に失敗", exc_info=True)
        self._canvas_floor.draw()

    def _update_elevation_diagram(self, result: MinimizationResult) -> None:
        """建物立面ダイアグラム: ダンパー配置を建物断面図風に可視化。"""
        self._fig_elev.clear()
        if not result.final_quantities:
            self._canvas_elev.draw()
            return

        try:
            import matplotlib.patches as patches

            ax = self._fig_elev.add_subplot(111)

            # 階をソート（数値順）
            floors = sorted(
                result.final_quantities.keys(),
                key=lambda k: int("".join(c for c in k if c.isdigit()) or "0"),
            )
            n_floors = len(floors)
            if n_floors == 0:
                self._canvas_elev.draw()
                return

            max_count = max(result.final_quantities.get(f, 0) for f in floors)
            max_count = max(max_count, 1)

            floor_h = 1.0  # 各階の高さ
            bldg_w = 4.0   # 建物幅
            x_center = 0.0

            for i, fk in enumerate(floors):
                y_base = i * floor_h
                count = result.final_quantities.get(fk, 0)
                initial = result.initial_quantities.get(fk, 0)

                # 床スラブ（水平線）
                ax.plot(
                    [x_center - bldg_w / 2, x_center + bldg_w / 2],
                    [y_base, y_base],
                    color="#555", linewidth=1.5,
                )

                # 柱（左右の縦線）
                ax.plot(
                    [x_center - bldg_w / 2, x_center - bldg_w / 2],
                    [y_base, y_base + floor_h],
                    color="#888", linewidth=1.0,
                )
                ax.plot(
                    [x_center + bldg_w / 2, x_center + bldg_w / 2],
                    [y_base, y_base + floor_h],
                    color="#888", linewidth=1.0,
                )

                # ダンパーを×印で表現（階の中央に横並び）
                if count > 0:
                    # ダンパー間隔を均等配分
                    damper_w = bldg_w * 0.7
                    if count == 1:
                        x_positions = [x_center]
                    else:
                        x_positions = [
                            x_center - damper_w / 2 + j * damper_w / (count - 1)
                            for j in range(count)
                        ]

                    y_mid = y_base + floor_h / 2
                    sz = floor_h * 0.2
                    for xp in x_positions:
                        # ×印でダンパー表現
                        ax.plot(
                            [xp - sz, xp + sz], [y_mid - sz, y_mid + sz],
                            color="#d32f2f", linewidth=1.5,
                        )
                        ax.plot(
                            [xp + sz, xp - sz], [y_mid - sz, y_mid + sz],
                            color="#d32f2f", linewidth=1.5,
                        )

                # 階ラベル（左側）
                ax.text(
                    x_center - bldg_w / 2 - 0.3,
                    y_base + floor_h / 2,
                    fk,
                    ha="right", va="center", fontsize=8,
                )

                # 本数ラベル（右側）
                diff = count - initial
                diff_str = f" ({diff:+d})" if diff != 0 else ""
                color = "#d32f2f" if count > 0 else "#999"
                ax.text(
                    x_center + bldg_w / 2 + 0.3,
                    y_base + floor_h / 2,
                    f"{count}本{diff_str}",
                    ha="left", va="center", fontsize=8, color=color,
                )

            # 屋上スラブ
            ax.plot(
                [x_center - bldg_w / 2, x_center + bldg_w / 2],
                [n_floors * floor_h, n_floors * floor_h],
                color="#555", linewidth=2.0,
            )

            # 地盤面
            ax.fill_between(
                [x_center - bldg_w / 2 - 1.0, x_center + bldg_w / 2 + 1.0],
                -0.3, 0,
                color="#8d6e63", alpha=0.3,
            )
            ax.plot(
                [x_center - bldg_w / 2 - 1.0, x_center + bldg_w / 2 + 1.0],
                [0, 0],
                color="#5d4037", linewidth=2.0,
            )

            total = sum(result.final_quantities.get(f, 0) for f in floors)
            total_init = sum(result.initial_quantities.get(f, 0) for f in floors)
            ax.set_title(
                f"ダンパー配置立面図 (合計 {total}本, 初期 {total_init}本)",
                fontsize=10,
            )
            ax.set_xlim(x_center - bldg_w / 2 - 2.0, x_center + bldg_w / 2 + 2.5)
            ax.set_ylim(-0.5, n_floors * floor_h + 0.5)
            ax.set_aspect("equal")
            ax.axis("off")

            self._fig_elev.tight_layout()
        except Exception:
            logger.warning("立面ダイアグラムの描画に失敗", exc_info=True)
        self._canvas_elev.draw()

    def _populate_result_table(self, result: MinimizationResult) -> None:
        """結果テーブル: 各階の本数 + 変化量。"""
        floors = sorted(result.final_quantities.keys(),
                        key=lambda k: int("".join(c for c in k if c.isdigit()) or "0"))

        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["階", "最終本数", "初期本数", "変化"])
        self._table.setRowCount(len(floors))
        self._table.horizontalHeader().setStretchLastSection(True)

        for i, fk in enumerate(floors):
            final = result.final_quantities.get(fk, 0)
            initial = result.initial_quantities.get(fk, 0)
            diff = final - initial

            self._table.setItem(i, 0, QTableWidgetItem(fk))
            self._table.setItem(i, 1, QTableWidgetItem(str(final)))
            self._table.setItem(i, 2, QTableWidgetItem(str(initial)))

            diff_item = QTableWidgetItem(f"{diff:+d}" if diff != 0 else "0")
            diff_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 3, diff_item)

        # 合計行
        row = len(floors)
        self._table.setRowCount(row + 1)
        total_item = QTableWidgetItem("合計")
        total_item.setFont(QFont("", -1, QFont.Weight.Bold))
        self._table.setItem(row, 0, total_item)
        self._table.setItem(row, 1, QTableWidgetItem(
            str(sum(result.final_quantities.values()))))
        self._table.setItem(row, 2, QTableWidgetItem(
            str(sum(result.initial_quantities.values()))))
        diff_total = sum(result.final_quantities.values()) - sum(result.initial_quantities.values())
        self._table.setItem(row, 3, QTableWidgetItem(f"{diff_total:+d}"))

    # -------------------------------------------------------------------
    # エクスポート
    # -------------------------------------------------------------------

    def _export_csv(self) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV出力", "minimizer_result.csv",
            "CSV (*.csv);;すべて (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                eval_tag = "SNAP" if self._is_snap else "モック"
                writer.writerow([f"# ダンパー本数最小化結果 (評価: {eval_tag})"])
                writer.writerow([f"# 戦略: {STRATEGIES.get(self._result.strategy, self._result.strategy)}"])
                writer.writerow([f"# 最終合計: {self._result.final_count}本"])
                writer.writerow([f"# マージン: {self._result.final_margin:+.4f}"])
                writer.writerow([f"# 評価回数: {self._result.evaluations}"])
                writer.writerow([])

                # 最終配置
                writer.writerow(["階", "最終本数", "初期本数", "変化"])
                for fk in sorted(self._result.final_quantities.keys(),
                                 key=lambda k: int("".join(c for c in k if c.isdigit()) or "0")):
                    final = self._result.final_quantities.get(fk, 0)
                    initial = self._result.initial_quantities.get(fk, 0)
                    writer.writerow([fk, final, initial, final - initial])

                # ステップ履歴
                if self._result.history:
                    writer.writerow([])
                    writer.writerow(["# ステップ履歴"])
                    writer.writerow(["ステップ", "操作", "合計本数", "判定", "マージン", "備考"])
                    for step in self._result.history:
                        writer.writerow([
                            step.iteration, step.action, step.total_count,
                            "OK" if step.is_feasible else "NG",
                            f"{step.worst_margin:+.4f}", step.note,
                        ])
            QMessageBox.information(self, "CSV出力", f"保存しました:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "CSV出力エラー", str(exc))

    def _copy_result(self) -> None:
        if self._result is None:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._result.summary_text())
        self._lbl_status.setText("クリップボードにコピーしました")

    def result(self) -> Optional[MinimizationResult]:
        return self._result
