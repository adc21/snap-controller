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

UX改善（第4回）①: バッチ完了後「次のアクション」誘導バナー追加。
  `on_batch_finished()` 完了時に成功/エラー件数に応じた色付きバナーを表示し、
  ユーザーが次に何をすべきかを具体的に案内します。
  - 全成功時: 緑バナー「✅ X件完了！→ 結果比較で各ケースを比較しましょう」
  - エラーあり: 赤セクション「❌ Y件エラー → ログタブでエラー内容を確認してください」
  - 混合時: 両方のメッセージを縦に並べて表示
  `_update_next_action_banner()` メソッドと `_next_action_banner` QFrame を追加。

UX改善（新）: 実行中ケースの速度インジケーター追加。
  実行中ケースの経過時間に応じてセル背景色と警告アイコンを変化させます。
  - 正常 (<{_SLOW_WARNING_SEC}秒): 通常の黄色背景
  - 遅い (≥{_SLOW_WARNING_SEC}秒): オレンジ背景 + 「⏳ 遅い」テキスト接頭辞
  - 要注意 (≥{_VERY_SLOW_SEC}秒): 赤背景 + 「🐢 要確認」接頭辞
  さらに、非常に遅いケースがある場合はヘッダーの ETA ラベル隣に
  「⚠ 解析が長時間かかっています」警告テキストを追加表示します。
  `_SLOW_WARNING_SEC`, `_VERY_SLOW_SEC` 定数と `_slow_warning_lbl` QLabel を追加。
  `_refresh_table()` および `_update_eta()` を拡張。
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

# UX改善（新）: 実行中ケースの速度しきい値 [秒]
_SLOW_WARNING_SEC = 30   # これ以上かかるとオレンジ警告
_VERY_SLOW_SEC    = 90   # これ以上かかると赤・要確認

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

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_progress_bar())
        layout.addWidget(self._build_queue_table(), stretch=1)
        layout.addLayout(self._build_button_bar())
        layout.addWidget(self._build_next_action_banner())

    def _build_header(self) -> QFrame:
        """ステータス / ETA / 速度警告のヘッダ行。"""
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

        self._slow_warning_lbl = QLabel("")
        self._slow_warning_lbl.setStyleSheet(
            "color: #e65100; font-size: 11px; font-weight: bold;"
        )
        self._slow_warning_lbl.hide()
        header_layout.addWidget(self._slow_warning_lbl)
        return header

    def _build_progress_bar(self) -> QProgressBar:
        self._progress = QProgressBar()
        self._progress.setMaximumHeight(18)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m ケース完了")
        self._progress.hide()
        return self._progress

    def _build_queue_table(self) -> QTableWidget:
        """バッチキューの一覧テーブル (5列)。"""
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
        return self._table

    def _build_button_bar(self) -> QHBoxLayout:
        """一時停止 / 再開 / キャンセル + サマリーラベル。"""
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

        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet("color: #555; font-size: 11px;")
        btn_layout.addWidget(self._lbl_summary)
        return btn_layout

    def _build_next_action_banner(self) -> QFrame:
        """バッチ完了後の次アクション誘導バナー。デフォルト非表示。"""
        self._next_action_banner = QFrame()
        self._next_action_banner.setFrameShape(QFrame.StyledPanel)
        self._next_action_banner_layout = QVBoxLayout(self._next_action_banner)
        self._next_action_banner_layout.setContentsMargins(10, 6, 10, 6)
        self._next_action_banner_layout.setSpacing(4)
        self._next_action_banner.hide()
        return self._next_action_banner

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
        # UX改善（第4回）①: 次のアクション誘導バナーを表示
        self._update_next_action_banner(completed, errors, total)

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
        # 次のアクションバナーを非表示
        self._next_action_banner.hide()

    def get_queue_order(self) -> List[str]:
        """現在のキュー順序（case_id リスト）を返します。"""
        return [c.id for c in self._cases if c.status == AnalysisCaseStatus.PENDING]

    # ------------------------------------------------------------------
    # UX改善（第4回）①: 次のアクション誘導バナー
    # ------------------------------------------------------------------

    def _update_next_action_banner(self, completed: int, errors: int, total: int) -> None:
        """
        バッチ完了後にユーザーへ次のアクションを案内するバナーを表示します。

        成功ケースと失敗ケースの数に応じてメッセージと配色を変え、
        ユーザーが「解析後に何をすれば良いか」迷わないよう誘導します。

        Parameters
        ----------
        completed : int  成功したケース数
        errors    : int  エラーになったケース数
        total     : int  バッチ全体のケース数
        """
        # 既存の子ウィジェットをクリア
        while self._next_action_banner_layout.count():
            item = self._next_action_banner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # バナー全体の背景色
        if errors == 0:
            bg_color = "#e8f5e9"   # 全成功: 緑系
            border_color = "#43a047"
        elif completed == 0:
            bg_color = "#ffebee"   # 全エラー: 赤系
            border_color = "#e53935"
        else:
            bg_color = "#fff8e1"   # 混合: 黄系
            border_color = "#fb8c00"

        self._next_action_banner.setStyleSheet(
            f"QFrame {{ background-color: {bg_color}; "
            f"border: 1px solid {border_color}; border-radius: 6px; }}"
        )

        # タイトル行
        title_lbl = QLabel("<b>🎯 次のアクション</b>")
        title_lbl.setStyleSheet(f"color: {border_color}; font-size: 12px;")
        self._next_action_banner_layout.addWidget(title_lbl)

        # 成功メッセージ
        if completed > 0:
            ok_lbl = QLabel(
                f"✅ <b>{completed}件</b>の解析が完了しました。"
                "　→ <b>「結果比較」タブ</b>でグラフと数値を比較してみましょう。"
            )
            ok_lbl.setStyleSheet("color: #2e7d32; font-size: 11px;")
            ok_lbl.setWordWrap(True)
            self._next_action_banner_layout.addWidget(ok_lbl)

        # エラーメッセージ
        if errors > 0:
            err_lbl = QLabel(
                f"❌ <b>{errors}件</b>のエラーが発生しました。"
                "　→ <b>「ログ」タブ</b>でエラー詳細を確認し、"
                "ケース設定を見直してください。"
            )
            err_lbl.setStyleSheet("color: #c62828; font-size: 11px;")
            err_lbl.setWordWrap(True)
            self._next_action_banner_layout.addWidget(err_lbl)

        # 追加ヒント（完了ケースが2件以上の場合）
        if completed >= 2:
            hint_lbl = QLabel(
                "💡 複数ケースの解析が完了しています。"
                "「ダッシュボード」でヒートマップやランキングも確認できます。"
            )
            hint_lbl.setStyleSheet("color: #555; font-size: 10px;")
            hint_lbl.setWordWrap(True)
            self._next_action_banner_layout.addWidget(hint_lbl)

        self._next_action_banner.show()

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

            # 実行時間 + UX改善（新）: 速度インジケーター（実行中のみ）
            elapsed = self._elapsed_times.get(case.id)
            if elapsed is not None:
                # 完了済み: 通常表示
                time_str = self._format_time(elapsed)
                time_bg = None
            elif case.id in self._start_times:
                running_time = time.time() - self._start_times[case.id]
                # UX改善（新）: 速度に応じて警告アイコン・色を変化させる
                if running_time >= _VERY_SLOW_SEC:
                    time_str = f"🐢 {self._format_time(running_time)}"
                    time_bg = QColor("#ffcdd2")  # 赤系
                elif running_time >= _SLOW_WARNING_SEC:
                    time_str = f"⏳ {self._format_time(running_time)}"
                    time_bg = QColor("#ffe0b2")  # オレンジ系
                else:
                    time_str = f"{self._format_time(running_time)}…"
                    time_bg = None
            else:
                time_str = "—"
                time_bg = None
            time_item = QTableWidgetItem(time_str)
            time_item.setTextAlignment(Qt.AlignCenter)
            time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)
            if time_bg:
                time_item.setBackground(time_bg)
                time_item.setToolTip(
                    "この解析ケースは予想より長くかかっています。\n"
                    "SNAP の設定やモデル規模を確認してください。\n"
                    f"{'🐢 90秒超: 解析が止まっている可能性があります。' if time_bg == QColor('#ffcdd2') else '⏳ 30秒超: 少し時間がかかっています。'}"
                )
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

        # UX改善（新）: 実行中ケースが非常に遅い場合は速度警告ラベルを表示
        max_running_sec = 0.0
        for c in self._cases:
            if c.status == AnalysisCaseStatus.RUNNING and c.id in self._start_times:
                max_running_sec = max(max_running_sec, time.time() - self._start_times[c.id])
        if max_running_sec >= _VERY_SLOW_SEC:
            self._slow_warning_lbl.setText(
                f"🐢 {self._format_time(max_running_sec)} — 解析が長時間かかっています"
            )
            self._slow_warning_lbl.setStyleSheet("color: #c62828; font-size: 11px; font-weight: bold;")
            self._slow_warning_lbl.show()
        elif max_running_sec >= _SLOW_WARNING_SEC:
            self._slow_warning_lbl.setText(
                f"⏳ {self._format_time(max_running_sec)} — 解析に時間がかかっています"
            )
            self._slow_warning_lbl.setStyleSheet("color: #e65100; font-size: 11px;")
            self._slow_warning_lbl.show()
        else:
            self._slow_warning_lbl.hide()

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
