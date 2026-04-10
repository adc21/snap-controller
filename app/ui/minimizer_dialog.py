"""ダンパー本数最小化ダイアログ。"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple, Dict

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.damper_count_minimizer import (
    EvaluateFn,
    MinimizationResult,
    MinimizationStep,
    minimize_damper_count,
)


class _MinimizerWorker(QThread):
    """バックグラウンドで最小化を実行するワーカースレッド。"""

    stepCompleted = Signal(object)
    finished_ = Signal(object)
    errorOccurred = Signal(str)

    def __init__(
        self,
        num_positions: int,
        evaluate_fn: EvaluateFn,
        strategy: str,
        candidate_mask: List[bool],
        required_indices: List[int],
        parent: Optional[QThread] = None,
    ) -> None:
        super().__init__(parent)
        self._num_positions = num_positions
        self._evaluate_fn = evaluate_fn
        self._strategy = strategy
        self._candidate_mask = candidate_mask
        self._required_indices = required_indices

    def _expand(self, active_pl: List[bool], active_indices: List[int]) -> List[bool]:
        full = [False] * self._num_positions
        for idx, ai in enumerate(active_indices):
            full[ai] = active_pl[idx]
        return full

    def run(self) -> None:
        try:
            ai = [i for i, c in enumerate(self._candidate_mask) if c]
            n_active = len(ai)
            active_req = [ai.index(r) for r in self._required_indices if r in ai]

            def wrapped_eval(pl: List[bool]) -> Tuple[Dict[str, float], bool, float]:
                return self._evaluate_fn(self._expand(pl, ai))

            def _on_step(step: MinimizationStep) -> None:
                step.placement = self._expand(step.placement, ai)
                self.stepCompleted.emit(step)

            kwargs: dict = {"required_positions": active_req}
            if self._strategy != "exhaustive":
                kwargs["progress_cb"] = _on_step

            result = minimize_damper_count(
                num_positions=n_active, evaluate_fn=wrapped_eval,
                strategy=self._strategy, **kwargs,
            )
            result.initial_placement = self._expand(result.initial_placement, ai)
            result.final_placement = self._expand(result.final_placement, ai)
            for step in result.history:
                if len(step.placement) == n_active:
                    step.placement = self._expand(step.placement, ai)
            self.finished_.emit(result)
        except Exception as exc:
            self.errorOccurred.emit(str(exc))


class MinimizerDialog(QDialog):
    """ダンパー本数最小化ダイアログ。"""

    minimizationCompleted = Signal(object)  # MinimizationResult

    def __init__(
        self,
        n_positions: int,
        position_labels: List[str],
        evaluate_fn: Optional[EvaluateFn] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._n_positions = n_positions
        self._position_labels = position_labels
        self._evaluate_fn = evaluate_fn
        self._worker: Optional[_MinimizerWorker] = None
        self._result: Optional[MinimizationResult] = None

        self.setWindowTitle("ダンパー本数最小化")
        self.setMinimumSize(560, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- 配置候補リスト ---
        cand_group = QGroupBox("配置候補リスト")
        cand_layout = QVBoxLayout(cand_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel("候補"), stretch=1)
        header.addWidget(QLabel("必須"), stretch=1)
        header.addWidget(QLabel("位置名"), stretch=3)
        scroll_layout.addLayout(header)

        self._cb_candidate: List[QCheckBox] = []
        self._cb_required: List[QCheckBox] = []

        for i in range(self._n_positions):
            row = QHBoxLayout()
            cb_cand = QCheckBox()
            cb_cand.setChecked(True)
            cb_req = QCheckBox()
            cb_req.setChecked(False)
            label = QLabel(self._position_labels[i] if i < len(self._position_labels) else f"位置{i}")
            row.addWidget(cb_cand, stretch=1)
            row.addWidget(cb_req, stretch=1)
            row.addWidget(label, stretch=3)
            scroll_layout.addLayout(row)
            self._cb_candidate.append(cb_cand)
            self._cb_required.append(cb_req)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        scroll.setMaximumHeight(200)
        cand_layout.addWidget(scroll)

        # 全選択/全解除ボタン
        btn_row = QHBoxLayout()
        btn_all = QPushButton("全選択")
        btn_none = QPushButton("全解除")
        btn_all.clicked.connect(lambda: self._set_all_candidates(True))
        btn_none.clicked.connect(lambda: self._set_all_candidates(False))
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        cand_layout.addLayout(btn_row)

        root.addWidget(cand_group)

        # 戦略選択
        strat_group = QGroupBox("戦略選択")
        strat_layout = QHBoxLayout(strat_group)
        strat_layout.addWidget(QLabel("探索戦略:"))
        self._combo_strategy = QComboBox()
        self._combo_strategy.addItem("greedy_remove (推奨)", "greedy_remove")
        self._combo_strategy.addItem("greedy_add", "greedy_add")
        self._combo_strategy.addItem("exhaustive (小規模向け)", "exhaustive")
        strat_layout.addWidget(self._combo_strategy, stretch=1)
        root.addWidget(strat_group)

        # 実行 + プログレス
        exec_layout = QHBoxLayout()
        self._btn_run = QPushButton("実行")
        self._btn_run.setFont(QFont("", -1, QFont.Weight.Bold))
        self._btn_run.clicked.connect(self._on_run)
        exec_layout.addWidget(self._btn_run)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setVisible(False)
        exec_layout.addWidget(self._progress, stretch=1)

        self._lbl_status = QLabel("")
        exec_layout.addWidget(self._lbl_status)
        root.addLayout(exec_layout)

        # 結果表示
        result_group = QGroupBox("結果")
        result_layout = QVBoxLayout(result_group)
        self._lbl_summary = QLabel("")
        self._lbl_summary.setWordWrap(True)
        result_layout.addWidget(self._lbl_summary)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["位置名", "配置", "必須"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        result_layout.addWidget(self._table)
        root.addWidget(result_group, stretch=1)

        # ボタン行
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._btn_close = QPushButton("閉じる")
        self._btn_close.clicked.connect(self.close)
        bottom.addWidget(self._btn_close)
        root.addLayout(bottom)

    def _set_all_candidates(self, checked: bool) -> None:
        for cb in self._cb_candidate:
            cb.setChecked(checked)

    def _on_run(self) -> None:
        if self._evaluate_fn is None:
            QMessageBox.warning(
                self,
                "評価関数未接続",
                "評価関数が接続されていません。\n"
                "解析ケースを設定してから実行してください。",
            )
            return

        candidate_mask = [cb.isChecked() for cb in self._cb_candidate]
        if not any(candidate_mask):
            QMessageBox.warning(self, "候補なし", "候補位置を1つ以上選択してください。")
            return

        required_indices = [
            i
            for i in range(self._n_positions)
            if self._cb_required[i].isChecked() and candidate_mask[i]
        ]

        strategy = self._combo_strategy.currentData()

        # exhaustive のサイズチェック
        n_active = sum(candidate_mask)
        if strategy == "exhaustive" and n_active > 12:
            QMessageBox.warning(
                self,
                "候補数超過",
                f"exhaustive 戦略は候補数12以下で使用してください（現在 {n_active}）。",
            )
            return

        self._btn_run.setEnabled(False)
        self._progress.setVisible(True)
        self._lbl_status.setText("実行中...")
        self._lbl_summary.setText("")
        self._table.setRowCount(0)

        self._worker = _MinimizerWorker(
            num_positions=self._n_positions,
            evaluate_fn=self._evaluate_fn,
            strategy=strategy,
            candidate_mask=candidate_mask,
            required_indices=required_indices,
            parent=self,
        )
        self._worker.stepCompleted.connect(self._on_step)
        self._worker.finished_.connect(self._on_finished)
        self._worker.errorOccurred.connect(self._on_error)
        self._worker.start()

    def _on_step(self, step: MinimizationStep) -> None:
        count = step.count
        action = step.action
        feasible = "OK" if step.is_feasible else "NG"
        margin = f"{step.margin:+.4f}"
        self._lbl_status.setText(
            f"[{action}] 本数={count}  基準={feasible}  余裕={margin}"
        )

    def _on_finished(self, result: MinimizationResult) -> None:
        self._result = result
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)

        feasible_text = "OK" if result.is_feasible else "NG"
        self._lbl_status.setText("完了")
        self._lbl_summary.setText(
            f"戦略: {result.strategy}　|　"
            f"最終本数: {result.final_count}　|　"
            f"基準充足: {feasible_text}　|　"
            f"マージン: {result.final_margin:+.4f}　|　"
            f"評価回数: {result.evaluations}"
        )

        # テーブル更新
        self._table.setRowCount(self._n_positions)
        required_set = {
            i
            for i in range(self._n_positions)
            if self._cb_required[i].isChecked()
        }
        for i in range(self._n_positions):
            label = (
                self._position_labels[i]
                if i < len(self._position_labels)
                else f"位置{i}"
            )
            placed = result.final_placement[i]

            item_name = QTableWidgetItem(label)
            item_placed = QTableWidgetItem("\u2713" if placed else "\u2717")
            item_placed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_req = QTableWidgetItem(
                "\u2713" if (placed and i in required_set) else ""
            )
            item_req.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self._table.setItem(i, 0, item_name)
            self._table.setItem(i, 1, item_placed)
            self._table.setItem(i, 2, item_req)

        self.minimizationCompleted.emit(result)
        self._worker = None

    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_run.setEnabled(True)
        self._lbl_status.setText("エラー")
        QMessageBox.critical(self, "実行エラー", msg)
        self._worker = None

    def result(self) -> Optional[MinimizationResult]:
        """最後の実行結果を返す。未実行なら None。"""
        return self._result
