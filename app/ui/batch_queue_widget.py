"""
app/ui/batch_queue_widget.py
バッチ実行キュー管理ウィジェット。

複数ケースの解析実行状況をリアルタイム表示し、
ETA推定、優先度変更、一時停止・再開・キャンセルのUIを提供します。

機能:
  - 実行キューのリアルタイム表示（状態アイコン付き）
  - ケース毎の実行時間表示
  - バッチ全体のETA（推定完了時刻）
  - ドラッグ&ドロップによるキュー順序変更
  - 優先度の上げ下げ（コンテキストメニュー）
  - 一時停止 / 再開 / キャンセルボタン
  - 実行完了サマリー
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QLabel, QProgressBar,
    QMenu, QAbstractItemView, QFrame,
)

from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    AnalysisCaseStatus.PENDING: "⏳",
    AnalysisCaseStatus.RUNNING: "▶️",
    AnalysisCaseStatus.COMPLETED: "✅",
    AnalysisCaseStatus.ERROR: "❌",
}

_STATUS_COLORS = {
    AnalysisCaseStatus.PENDING: QColor("#f0f0f0"),
    AnalysisCaseStatus.RUNNING: QColor("#fff3cd"),
    AnalysisCaseStatus.COMPLETED: QColor("#d4edda"),
    AnalysisCaseStatus.ERROR: QColor("#f8d7da"),
}


class BatchQueueWidget(QWidget):
    """
    バッチ実行キュー管理ウィジェット。

    Signals
    -------
    priorityChanged(case_ids: list)
        キューの順序が変更されたときに発火。
    pauseRequested()
        一時停止ボタンが押されたとき。
    resumeRequested()
        再開ボタンが押されたとき。
    cancelRequested()
        キャンセルボタンが押されたとき。
    """

    priorityChanged = Signal(list)
    pauseRequested = Signal()
    resumeRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._start_times: Dict[str, float] = {}  # case_id -> start_time
        self._elapsed_times: Dict[str, float] = {}  # case_id -> elapsed_sec
        self._batch_start: Optional[float] = None
        self._is_running = False
        self._is_paused = False

        self._setup_ui()
        self._setup_timer()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ---- ヘッダ（サマリー） ----
        header = QFrame()
        header.setFrameShape(QFrame.StyledPanel)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)

        self._lbl_status = QLabel("バッチ未実行")
        self._lbl_status.setStyleSheet("font-weight: bold; font-size: 13px;")
        header_layout.addWidget(self._lbl_status)

        header_layout.addStretch()

        self._lbl_eta = QLabel("")
        self._lbl_eta.setStyleSheet("color: #666; font-size: 12px;")
        header_layout.addWidget(self._lbl_eta)

        layout.addWidget(header)

        # ---- 進捗バー ----
        self._progress = QProgressBar()
        self._progress.setMaximumHeight(18)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m ケース完了")
        self._progress.hide()
        layout.addWidget(self._progress)

        # ---- テーブル ----
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "状態", "ケース名", "優先度", "実行時間", "結果概要"
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.resizeSection(0, 50)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.resizeSection(2, 60)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.resizeSection(3, 90)
        h.setSectionResizeMode(4, QHeaderView.Stretch)

        self._table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(self._table, stretch=1)

        # ---- ボタンバー ----
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._btn_pause = QPushButton("⏸ 一時停止")
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_pause.setEnabled(False)
        btn_layout.addWidget(self._btn_pause)

        self._btn_resume = QPushButton("▶ 再開")
        self._btn_resume.clicked.connect(self._on_resume)
        self._btn_resume.setEnabled(False)
        btn_layout.addWidget(self._btn_resume)

        self._btn_cancel = QPushButton("⏹ キャンセル")
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setStyleSheet("color: #c0392b;")
        btn_layout.addWidget(self._btn_cancel)

        btn_layout.addStretch()

        # サマリーラベル
        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet("color: #555; font-size: 11px;")
        btn_layout.addWidget(self._lbl_summary)

        layout.addLayout(btn_layout)

    def _setup_timer(self) -> None:
        """1秒ごとにETA・経過時間を更新。"""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_eta)
        self._timer.setInterval(1000)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_batch(self, cases: List[AnalysisCase]) -> None:
        """バッチ実行するケースのリストをセットします。"""
        self._cases = list(cases)
        self._start_times.clear()
        self._elapsed_times.clear()
        self._batch_start = time.time()
        self._is_running = True
        self._is_paused = False
        self._refresh_table()
        self._update_buttons()
        self._progress.setMaximum(len(cases))
        self._progress.setValue(0)
        self._progress.show()
        self._lbl_status.setText(f"バッチ実行中: 0 / {len(cases)} ケース")
        self._timer.start()

    def on_case_started(self, case_id: str) -> None:
        """ケースの実行が開始されたときに呼びます。"""
        self._start_times[case_id] = time.time()
        self._refresh_table()

    def on_case_finished(self, case_id: str, success: bool) -> None:
        """ケースの実行が完了したときに呼びます。"""
        if case_id in self._start_times:
            elapsed = time.time() - self._start_times[case_id]
            self._elapsed_times[case_id] = elapsed

        completed = sum(
            1 for c in self._cases
            if c.status in (AnalysisCaseStatus.COMPLETED, AnalysisCaseStatus.ERROR)
        )
        self._progress.setValue(completed)
        self._lbl_status.setText(f"バッチ実行中: {completed} / {len(self._cases)} ケース")
        self._refresh_table()

    def on_batch_finished(self) -> None:
        """バッチ全体が完了したときに呼びます。"""
        self._is_running = False
        self._is_paused = False
        self._timer.stop()
        self._update_buttons()

        completed = sum(1 for c in self._cases if c.status == AnalysisCaseStatus.COMPLETED)
        errors = sum(1 for c in self._cases if c.status == AnalysisCaseStatus.ERROR)
        total = len(self._cases)

        elapsed_total = time.time() - self._batch_start if self._batch_start else 0
        self._lbl_status.setText(f"バッチ完了: {completed}/{total} 成功, {errors} エラー")
        self._lbl_eta.setText(f"合計時間: {self._format_time(elapsed_total)}")
        self._lbl_summary.setText(
            f"平均: {self._format_time(elapsed_total / total if total else 0)}/ケース"
        )
        self._refresh_table()

    def on_paused(self) -> None:
        """一時停止状態になったときに呼びます。"""
        self._is_paused = True
        self._update_buttons()
        self._lbl_status.setText(
            self._lbl_status.text().replace("実行中", "一時停止中")
        )

    def on_resumed(self) -> None:
        """再開されたときに呼びます。"""
        self._is_paused = False
        self._update_buttons()
        self._lbl_status.setText(
            self._lbl_status.text().replace("一時停止中", "実行中")
        )

    def clear(self) -> None:
        """表示をクリアします。"""
        self._cases.clear()
        self._start_times.clear()
        self._elapsed_times.clear()
        self._batch_start = None
        self._is_running = False
        self._is_paused = False
        self._timer.stop()
        self._table.setRowCount(0)
        self._progress.hide()
        self._lbl_status.setText("バッチ未実行")
        self._lbl_eta.setText("")
        self._lbl_summary.setText("")
        self._update_buttons()

    def get_queue_order(self) -> List[str]:
        """現在のキュー順序（case_id リスト）を返します。"""
        return [c.id for c in self._cases if c.status == AnalysisCaseStatus.PENDING]

    # ------------------------------------------------------------------
    # テーブル更新
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        self._table.setRowCount(len(self._cases))
        for row, case in enumerate(self._cases):
            # 状態アイコン
            icon_item = QTableWidgetItem(_STATUS_ICONS.get(case.status, "?"))
            icon_item.setTextAlignment(Qt.AlignCenter)
            icon_item.setFlags(icon_item.flags() & ~Qt.ItemIsEditable)
            bg = _STATUS_COLORS.get(case.status)
            if bg:
                icon_item.setBackground(bg)
            self._table.setItem(row, 0, icon_item)

            # ケース名
            name_item = QTableWidgetItem(case.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            if bg:
                name_item.setBackground(bg)
            self._table.setItem(row, 1, name_item)

            # 優先度
            priority_item = QTableWidgetItem(str(row + 1))
            priority_item.setTextAlignment(Qt.AlignCenter)
            priority_item.setFlags(priority_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 2, priority_item)

            # 実行時間
            elapsed = self._elapsed_times.get(case.id)
            if elapsed is not None:
                time_str = self._format_time(elapsed)
            elif case.id in self._start_times:
                running_time = time.time() - self._start_times[case.id]
                time_str = f"{self._format_time(running_time)}…"
            else:
                time_str = "—"
            time_item = QTableWidgetItem(time_str)
            time_item.setTextAlignment(Qt.AlignCenter)
            time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 3, time_item)

            # 結果概要
            summary = ""
            if case.status == AnalysisCaseStatus.COMPLETED and case.result_summary:
                drift = case.result_summary.get("max_drift")
                if drift is not None:
                    summary = f"層間変形角: {drift:.6f}"
            elif case.status == AnalysisCaseStatus.ERROR:
                summary = "エラー"
            summary_item = QTableWidgetItem(summary)
            summary_item.setFlags(summary_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 4, summary_item)

    # ------------------------------------------------------------------
    # ETA 推定
    # ------------------------------------------------------------------

    def _update_eta(self) -> None:
        """1秒ごとに呼ばれるETA更新。"""
        if not self._is_running or not self._cases:
            return

        # 実行中のケースの経過時間を更新
        self._refresh_table()

        # 完了済みケースの平均実行時間からETAを推定
        completed_times = list(self._elapsed_times.values())
        if not completed_times:
            self._lbl_eta.setText("ETA: 計算中…")
            return

        avg_time = sum(completed_times) / len(completed_times)
        remaining = sum(
            1 for c in self._cases
            if c.status in (AnalysisCaseStatus.PENDING, AnalysisCaseStatus.RUNNING)
        )
        # 実行中のケースは既に部分的に経過しているので差し引く
        running_elapsed = 0
        for c in self._cases:
            if c.status == AnalysisCaseStatus.RUNNING and c.id in self._start_times:
                running_elapsed = time.time() - self._start_times[c.id]
                break

        eta_sec = max(0, avg_time * remaining - running_elapsed)
        total_elapsed = time.time() - self._batch_start if self._batch_start else 0

        self._lbl_eta.setText(
            f"経過: {self._format_time(total_elapsed)} | "
            f"残り推定: {self._format_time(eta_sec)} "
            f"(平均 {self._format_time(avg_time)}/ケース)"
        )

    # ------------------------------------------------------------------
    # コンテキストメニュー
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._cases):
            return

        case = self._cases[row]
        if case.status != AnalysisCaseStatus.PENDING:
            return  # 実行待ちのケースのみ操作可能

        menu = QMenu(self)

        act_up = menu.addAction("優先度を上げる ↑")
        act_up.setEnabled(row > 0)
        act_up.triggered.connect(lambda: self._move_case(row, row - 1))

        act_down = menu.addAction("優先度を下げる ↓")
        act_down.setEnabled(row < len(self._cases) - 1)
        act_down.triggered.connect(lambda: self._move_case(row, row + 1))

        menu.addSeparator()

        act_top = menu.addAction("最優先に移動 ⇈")
        act_top.triggered.connect(lambda: self._move_case(row, 0))

        act_bottom = menu.addAction("最後に移動 ⇊")
        act_bottom.triggered.connect(lambda: self._move_case(row, len(self._cases) - 1))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _move_case(self, from_row: int, to_row: int) -> None:
        """キュー内のケースを移動します。"""
        if from_row == to_row:
            return
        case = self._cases.pop(from_row)
        self._cases.insert(to_row, case)
        self._refresh_table()
        self.priorityChanged.emit(self.get_queue_order())

    # ------------------------------------------------------------------
    # ボタンハンドラ
    # ------------------------------------------------------------------

    def _on_pause(self) -> None:
        self.pauseRequested.emit()

    def _on_resume(self) -> None:
        self.resumeRequested.emit()

    def _on_cancel(self) -> None:
        self.cancelRequested.emit()

    def _update_buttons(self) -> None:
        self._btn_pause.setEnabled(self._is_running and not self._is_paused)
        self._btn_resume.setEnabled(self._is_paused)
        self._btn_cancel.setEnabled(self._is_running or self._is_paused)

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _format_time(seconds: float) -> str:
        """秒数を mm:ss 形式に変換。"""
        if seconds < 0:
            return "0:00"
        m, s = divmod(int(seconds), 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
