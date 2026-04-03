"""
app/ui/ranking_widget.py
結果ランキング・ソートウィジェット。

完了済みケースを指定した応答値でランキング表示します。
目標性能基準の判定結果も合わせて表示します。

レイアウト:
  ┌─────────────────────────────────────────────────┐
  │ [ソート項目コンボ] [昇順/降順] [グループフィルタ]│
  ├─────────────────────────────────────────────────┤
  │ 🥇 Case3 — max_drift: 0.00321 (OK)             │
  │ 🥈 Case1 — max_drift: 0.00456 (OK)             │
  │ 🥉 Case2 — max_drift: 0.00789 (NG)             │
  │ 4. Case4 — max_drift: 0.01234 (NG)             │
  └─────────────────────────────────────────────────┘

UX改善①新: ランキングから「このケースを基点に再設計」ボタン。
  ランキングテーブルでケースを選択すると、ウィジェット下部に
  「🔁 このケースを基点に再設計 (STEP2へ)」ボタンが有効化されます。
  押すと useAsStartingPointRequested シグナルが発火し、
  呼び出し側（MainWindow）でケースの複製 → STEP2への遷移が実行されます。
  「最良ケースをベースに次ラウンドを計画する」というワークフローを
  1クリックで実現します。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria
from .theme import ThemeManager

# ランキング対象の応答値 (key, ラベル, 単位, フォーマット, 説明)
_RANKING_ITEMS = [
    ("max_drift",       "最大層間変形角",      "rad",   "{:.6f}",  "小さいほど良い"),
    ("max_acc",         "最大絶対加速度",      "m/s²",  "{:.3f}",  "小さいほど良い"),
    ("max_disp",        "最大相対変位",        "m",     "{:.5f}",  "小さいほど良い"),
    ("max_vel",         "最大相対速度",        "m/s",   "{:.4f}",  "小さいほど良い"),
    ("max_story_disp",  "最大層間変形",        "m",     "{:.5f}",  "小さいほど良い"),
    ("shear_coeff",     "せん断力係数",        "—",     "{:.4f}",  "小さいほど良い"),
    ("max_otm",         "最大転倒モーメント",  "kN·m",  "{:.1f}",  "小さいほど良い"),
]

_MEDAL_COLORS = {
    0: QColor("#FFD700"),  # 金
    1: QColor("#C0C0C0"),  # 銀
    2: QColor("#CD7F32"),  # 銅
}


class RankingWidget(QWidget):
    """
    完了済みケースをランキング表示するウィジェット。

    Signals
    -------
    caseSelected(case_id: str)
        ランキング行がクリックされたときに発火します。
    """

    caseSelected = Signal(str)
    # UX改善①新: 選択ケースを基点に再設計するよう外部に要求するシグナル
    useAsStartingPointRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._criteria: Optional[PerformanceCriteria] = None
        self._group_filter: str = ""  # 空文字 = 全表示
        self._case_groups: dict = {}
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        self._cases = cases
        self._refresh()

    def set_criteria(self, criteria: PerformanceCriteria) -> None:
        self._criteria = criteria
        self._refresh()

    def set_case_groups(self, groups: dict) -> None:
        """ケースグループ辞書を設定します。"""
        self._case_groups = groups
        self._update_group_filter_combo()
        self._refresh()

    def update_theme(self) -> None:
        self._refresh()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- コントロール行 ---
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("ソート項目:"))
        self._sort_combo = QComboBox()
        for _, label, unit, _, desc in _RANKING_ITEMS:
            self._sort_combo.addItem(f"{label} [{unit}]  ({desc})")
        self._sort_combo.currentIndexChanged.connect(self._refresh)
        ctrl_row.addWidget(self._sort_combo)

        ctrl_row.addWidget(QLabel("順序:"))
        self._order_combo = QComboBox()
        self._order_combo.addItems(["昇順 (小さい順)", "降順 (大きい順)"])
        self._order_combo.currentIndexChanged.connect(self._refresh)
        ctrl_row.addWidget(self._order_combo)

        ctrl_row.addWidget(QLabel("グループ:"))
        self._group_combo = QComboBox()
        self._group_combo.addItem("すべて")
        self._group_combo.currentTextChanged.connect(self._on_group_changed)
        ctrl_row.addWidget(self._group_combo)

        ctrl_row.addStretch()
        layout.addLayout(ctrl_row)

        # --- ランキングテーブル ---
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "順位", "ケース名", "グループ", "値", "判定", "基準との差"
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        for col in [0, 2, 3, 4, 5]:
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        # --- サマリー ---
        self._summary_label = QLabel("")
        layout.addWidget(self._summary_label)

        # --- UX改善①新: 「基点として再設計」ボタン ---
        redesign_row = QHBoxLayout()
        self._btn_use_as_base = QPushButton("🔁  このケースを基点に再設計  (STEP2へ)")
        self._btn_use_as_base.setToolTip(
            "選択中のケースを複製し、ケース設計(STEP2)に切り替えます。\n"
            "最良ケースを出発点として次ラウンドの改善を始めるワークフローに最適です。\n\n"
            "操作手順:\n"
            "  1. ランキング表でケースを選択\n"
            "  2. このボタンをクリック\n"
            "  3. STEP2 で複製ケースのパラメータを調整\n"
            "  4. 再度 STEP3 で解析実行"
        )
        self._btn_use_as_base.setEnabled(False)
        self._btn_use_as_base.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                padding: 6px 18px;
                background-color: #1976d2;
                color: white;
                border-radius: 4px;
                border: none;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1565c0;
            }
            QPushButton:pressed {
                background-color: #0d47a1;
            }
            QPushButton:disabled {
                background-color: palette(mid);
                color: palette(shadow);
            }
        """)
        self._btn_use_as_base.clicked.connect(self._on_use_as_base)
        redesign_row.addStretch()
        redesign_row.addWidget(self._btn_use_as_base)
        layout.addLayout(redesign_row)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_group_filter_combo(self) -> None:
        """グループフィルターコンボを更新します。"""
        current = self._group_combo.currentText()
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        self._group_combo.addItem("すべて")
        for gname in sorted(self._case_groups.keys()):
            self._group_combo.addItem(gname)
        # 元の選択を復元
        idx = self._group_combo.findText(current)
        if idx >= 0:
            self._group_combo.setCurrentIndex(idx)
        self._group_combo.blockSignals(False)

    def _on_group_changed(self, text: str) -> None:
        self._group_filter = "" if text == "すべて" else text
        self._refresh()

    def _refresh(self) -> None:
        self._table.setRowCount(0)

        # 完了済みケースを抽出
        completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]

        # グループフィルタ
        if self._group_filter and self._case_groups:
            group_ids = set(self._case_groups.get(self._group_filter, []))
            completed = [c for c in completed if c.id in group_ids]

        if not completed:
            self._summary_label.setText("完了済みケースがありません")
            return

        # ソート
        idx = self._sort_combo.currentIndex()
        key, label, unit, fmt, _ = _RANKING_ITEMS[idx]
        ascending = self._order_combo.currentIndex() == 0

        def sort_key(c: AnalysisCase) -> float:
            v = c.result_summary.get(key)
            return v if v is not None else float("inf")

        sorted_cases = sorted(completed, key=sort_key, reverse=not ascending)

        # 基準値を取得
        limit_value = None
        if self._criteria:
            for item in self._criteria.items:
                if item.key == key and item.enabled and item.limit_value is not None:
                    limit_value = item.limit_value
                    break

        # テーブルに表示
        ok_count = 0
        ng_count = 0
        for rank, case in enumerate(sorted_cases):
            row = self._table.rowCount()
            self._table.insertRow(row)

            val = case.result_summary.get(key)

            # 順位
            rank_text = f"{rank + 1}"
            rank_item = QTableWidgetItem(rank_text)
            rank_item.setTextAlignment(Qt.AlignCenter)
            if rank < 3:
                rank_item.setForeground(_MEDAL_COLORS[rank])
                font = QFont()
                font.setBold(True)
                rank_item.setFont(font)
            self._table.setItem(row, 0, rank_item)

            # ケース名
            name_item = QTableWidgetItem(case.name)
            name_item.setData(Qt.UserRole, case.id)
            if rank < 3:
                font = QFont()
                font.setBold(True)
                name_item.setFont(font)
            self._table.setItem(row, 1, name_item)

            # グループ
            group_name = ""
            for gname, cids in self._case_groups.items():
                if case.id in cids:
                    group_name = gname
                    break
            self._table.setItem(row, 2, QTableWidgetItem(group_name))

            # 値
            val_text = fmt.format(val) if val is not None else "N/A"
            val_item = QTableWidgetItem(f"{val_text} {unit}")
            val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, 3, val_item)

            # 判定
            verdict_text = ""
            if self._criteria and val is not None:
                verdict = self._criteria.is_all_pass(case.result_summary)
                if verdict is True:
                    verdict_text = "OK"
                    ok_count += 1
                elif verdict is False:
                    verdict_text = "NG"
                    ng_count += 1
            verdict_item = QTableWidgetItem(verdict_text)
            verdict_item.setTextAlignment(Qt.AlignCenter)
            if verdict_text == "OK":
                verdict_item.setForeground(QColor("#2ca02c"))
            elif verdict_text == "NG":
                verdict_item.setForeground(QColor("#d62728"))
            self._table.setItem(row, 4, verdict_item)

            # 基準との差
            diff_text = ""
            if limit_value is not None and val is not None:
                diff = val - limit_value
                ratio = (diff / limit_value * 100) if limit_value != 0 else 0
                diff_text = f"{diff:+.6f} ({ratio:+.1f}%)"
            diff_item = QTableWidgetItem(diff_text)
            diff_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if limit_value is not None and val is not None:
                if val <= limit_value:
                    diff_item.setForeground(QColor("#2ca02c"))
                else:
                    diff_item.setForeground(QColor("#d62728"))
            self._table.setItem(row, 5, diff_item)

        # サマリー
        total = len(sorted_cases)
        best = sorted_cases[0] if sorted_cases else None
        best_val = best.result_summary.get(key) if best else None
        summary_parts = [f"表示ケース: {total}"]
        if best and best_val is not None:
            summary_parts.append(f"最良値: {fmt.format(best_val)} ({best.name})")
        if ok_count or ng_count:
            summary_parts.append(f"判定: OK {ok_count} / NG {ng_count}")
        self._summary_label.setText("  |  ".join(summary_parts))

    def _on_selection_changed(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            # UX改善①新: 選択解除時はボタンを無効化
            self._btn_use_as_base.setEnabled(False)
            return
        item = self._table.item(row, 1)
        if item:
            case_id = item.data(Qt.UserRole)
            if case_id:
                self.caseSelected.emit(case_id)
                # UX改善①新: ケース選択時はボタンを有効化
                self._btn_use_as_base.setEnabled(True)
        else:
            self._btn_use_as_base.setEnabled(False)

    def _on_use_as_base(self) -> None:
        """
        UX改善①新: 選択ケースを基点として再設計するよう要求します。

        ランキング表で選択中のケースの ID を useAsStartingPointRequested シグナルで通知し、
        MainWindow 側でケースの複製と STEP2 への切り替えを実行させます。
        """
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 1)
        if item:
            case_id = item.data(Qt.UserRole)
            if case_id:
                self.useAsStartingPointRequested.emit(case_id)
