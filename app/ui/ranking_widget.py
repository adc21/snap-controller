"""
app/ui/ranking_widget.py
結果ランキング・ソートウィジェット。

UX改善（第10回④）: 「総合勝利数」バッジ列を追加。
  ランキングテーブルに「総合」列を追加し、各ケースが全7応答指標のうち
  何指標で1位（最小値）を獲得しているかを「X/7」形式で表示します。
  - 1位が多いケース → 緑系でハイライト
  - 1位が0のケース → グレー表示
  全体的に優秀なケースを見極める「バランス視点」の評価基準を提供します。
  `_calc_wins_per_case()` スタティックメソッドを追加。
  `_refresh()` にて各行の「総合」列にバッジを設定。

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

UX改善（新）: 1位ケース強調バナー + 基準クリア率バッジ。
  テーブルの上部にコントロール行の直下に「🏆 最良ケース」バナーを配置し、
  現在のソート指標で1位のケース名・値を即座に確認できます。
  また、性能基準が設定されている場合は「クリア率: OK X件 / N件（XX%）」バッジを
  バナー右側に表示します。「どのケースが全ての基準を満たすか」を一目で把握できます。
  バナーは完了ケースがない場合または1ケース以下の場合は非表示になります。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
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

        # ---- UX改善（新）: 1位ケース強調バナー ----
        self._best_banner = QFrame()
        self._best_banner.setFrameShape(QFrame.StyledPanel)
        self._best_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #fffde7;"
            "  border: 1px solid #fdd835;"
            "  border-radius: 4px;"
            "}"
        )
        self._best_banner.setMaximumHeight(36)
        _banner_row = QHBoxLayout(self._best_banner)
        _banner_row.setContentsMargins(10, 4, 10, 4)
        _banner_row.setSpacing(8)
        self._best_banner_label = QLabel("")
        self._best_banner_label.setStyleSheet("color: #7f6000; font-size: 11px; font-weight: bold; background: transparent;")
        _banner_row.addWidget(self._best_banner_label)
        _banner_row.addStretch()
        self._clearrate_label = QLabel("")
        self._clearrate_label.setStyleSheet("color: #2e7d32; font-size: 11px; font-weight: bold; background: transparent;")
        _banner_row.addWidget(self._clearrate_label)
        self._best_banner.setVisible(False)
        layout.addWidget(self._best_banner)

        # --- ランキングテーブル ---
        # UX改善（第10回④）: 「総合」列を追加 (col=6)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "順位", "ケース名", "グループ", "値", "判定", "基準との差", "総合"
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        for col in [0, 2, 3, 4, 5, 6]:
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        # 「総合」列のヘッダーにツールチップを設定
        self._table.horizontalHeaderItem(6) if self._table.columnCount() > 6 else None
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

        completed = self._collect_completed_cases()
        if not completed:
            self._summary_label.setText("完了済みケースがありません")
            if hasattr(self, "_best_banner"):
                self._best_banner.setVisible(False)
            return

        idx = self._sort_combo.currentIndex()
        key, label, unit, fmt, _ = _RANKING_ITEMS[idx]
        ascending = self._order_combo.currentIndex() == 0

        sorted_cases = self._sort_cases(completed, key, ascending)
        limit_value = self._lookup_limit_value(key)
        wins_map = self._calc_wins_per_case(completed)

        ok_count, ng_count = self._populate_ranking_rows(
            sorted_cases, key, fmt, unit, limit_value, wins_map
        )

        total = len(sorted_cases)
        best = sorted_cases[0] if sorted_cases else None
        best_val = best.result_summary.get(key) if best else None
        self._update_summary_label(total, best, best_val, key, fmt, ok_count, ng_count)
        self._update_best_banner(best, best_val, key, fmt, unit, total, ok_count, ng_count)

    def _collect_completed_cases(self) -> List[AnalysisCase]:
        completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        if self._group_filter and self._case_groups:
            group_ids = set(self._case_groups.get(self._group_filter, []))
            completed = [c for c in completed if c.id in group_ids]
        return completed

    @staticmethod
    def _sort_cases(
        completed: List[AnalysisCase], key: str, ascending: bool
    ) -> List[AnalysisCase]:
        def sort_key(c: AnalysisCase) -> float:
            v = c.result_summary.get(key)
            return v if v is not None else float("inf")
        return sorted(completed, key=sort_key, reverse=not ascending)

    def _lookup_limit_value(self, key: str) -> Optional[float]:
        if not self._criteria:
            return None
        for item in self._criteria.items:
            if item.key == key and item.enabled and item.limit_value is not None:
                return item.limit_value
        return None

    def _populate_ranking_rows(
        self,
        sorted_cases: List[AnalysisCase],
        key: str,
        fmt: str,
        unit: str,
        limit_value: Optional[float],
        wins_map: dict,
    ) -> tuple:
        ok_count = 0
        ng_count = 0
        for rank, case in enumerate(sorted_cases):
            row = self._table.rowCount()
            self._table.insertRow(row)
            val = case.result_summary.get(key)

            self._set_rank_item(row, rank)
            self._set_name_item(row, case, rank)
            self._set_group_item(row, case)
            self._set_value_item(row, val, fmt, unit)
            verdict_text = self._set_verdict_item(row, case, val)
            if verdict_text == "OK":
                ok_count += 1
            elif verdict_text == "NG":
                ng_count += 1
            self._set_diff_item(row, val, limit_value)
            self._set_wins_item(row, wins_map.get(case.id, 0))
        return ok_count, ng_count

    def _set_rank_item(self, row: int, rank: int) -> None:
        rank_item = QTableWidgetItem(f"{rank + 1}")
        rank_item.setTextAlignment(Qt.AlignCenter)
        if rank < 3:
            rank_item.setForeground(_MEDAL_COLORS[rank])
            font = QFont()
            font.setBold(True)
            rank_item.setFont(font)
        self._table.setItem(row, 0, rank_item)

    def _set_name_item(self, row: int, case: AnalysisCase, rank: int) -> None:
        name_item = QTableWidgetItem(case.name)
        name_item.setData(Qt.UserRole, case.id)
        if rank < 3:
            font = QFont()
            font.setBold(True)
            name_item.setFont(font)
        self._table.setItem(row, 1, name_item)

    def _set_group_item(self, row: int, case: AnalysisCase) -> None:
        group_name = ""
        for gname, cids in self._case_groups.items():
            if case.id in cids:
                group_name = gname
                break
        self._table.setItem(row, 2, QTableWidgetItem(group_name))

    def _set_value_item(self, row: int, val, fmt: str, unit: str) -> None:
        val_text = fmt.format(val) if val is not None else "N/A"
        val_item = QTableWidgetItem(f"{val_text} {unit}")
        val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._table.setItem(row, 3, val_item)

    def _set_verdict_item(self, row: int, case: AnalysisCase, val) -> str:
        verdict_text = ""
        if self._criteria and val is not None:
            verdict = self._criteria.is_all_pass(case.result_summary)
            if verdict is True:
                verdict_text = "OK"
            elif verdict is False:
                verdict_text = "NG"
        verdict_item = QTableWidgetItem(verdict_text)
        verdict_item.setTextAlignment(Qt.AlignCenter)
        if verdict_text == "OK":
            verdict_item.setForeground(QColor("#2ca02c"))
        elif verdict_text == "NG":
            verdict_item.setForeground(QColor("#d62728"))
        self._table.setItem(row, 4, verdict_item)
        return verdict_text

    def _set_diff_item(self, row: int, val, limit_value: Optional[float]) -> None:
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

    def _set_wins_item(self, row: int, wins: int) -> None:
        total_metrics = len(_RANKING_ITEMS)
        wins_item = QTableWidgetItem(f"{wins}/{total_metrics}")
        wins_item.setTextAlignment(Qt.AlignCenter)
        wins_item.setToolTip(
            f"全{total_metrics}指標のうち {wins} 指標で最小値（1位）\n"
            "すべての指標で1位に近いほど総合的に優秀なケースです"
        )
        if wins >= 4:
            wins_item.setForeground(QColor("#2ca02c"))
            font = QFont()
            font.setBold(True)
            wins_item.setFont(font)
        elif wins == 0:
            wins_item.setForeground(QColor("#9e9e9e"))
        else:
            wins_item.setForeground(QColor("#ff7f0e"))
        self._table.setItem(row, 6, wins_item)

    def _update_summary_label(
        self,
        total: int,
        best: Optional[AnalysisCase],
        best_val,
        key: str,
        fmt: str,
        ok_count: int,
        ng_count: int,
    ) -> None:
        summary_parts = [f"表示ケース: {total}"]
        if best and best_val is not None:
            summary_parts.append(f"最良値: {fmt.format(best_val)} ({best.name})")
        if ok_count or ng_count:
            summary_parts.append(f"判定: OK {ok_count} / NG {ng_count}")
        self._summary_label.setText("  |  ".join(summary_parts))

    def _update_best_banner(
        self,
        best,
        best_val,
        key: str,
        fmt: str,
        unit: str,
        total: int,
        ok_count: int,
        ng_count: int,
    ) -> None:
        """
        UX改善（新）: 1位ケース強調バナーと基準クリア率バッジを更新します。

        Parameters
        ----------
        best : AnalysisCase | None
            1位のケース
        best_val : float | None
            1位ケースの選択指標の値
        key, fmt, unit : str
            現在のソート指標
        total : int
            表示中の全ケース数
        ok_count, ng_count : int
            OK/NG 判定の件数
        """
        if best is None or total < 1:
            self._best_banner.setVisible(False)
            return

        # 1位ケース情報
        val_str = fmt.format(best_val) if best_val is not None else "—"
        banner_text = f"🏆  最良ケース: {best.name}   {val_str} {unit}"
        self._best_banner_label.setText(banner_text)
        self._best_banner_label.setToolTip(
            f"現在のソート指標「{key}」で最も良い値を持つケースです。\n"
            "このケースを基点に次の解析戦略を立てることをお勧めします。\n"
            "下部の「🔁 このケースを基点に再設計」ボタンも活用してください。"
        )

        # 基準クリア率
        if self._criteria and (ok_count + ng_count) > 0:
            judged = ok_count + ng_count
            rate = ok_count / judged * 100
            rate_str = f"基準クリア率: {ok_count} / {judged} 件 （{rate:.0f}%）"
            self._clearrate_label.setText(rate_str)
            if rate >= 80:
                self._clearrate_label.setStyleSheet(
                    "color: #2e7d32; font-size: 11px; font-weight: bold; background: transparent;"
                )
            elif rate >= 50:
                self._clearrate_label.setStyleSheet(
                    "color: #ef6c00; font-size: 11px; font-weight: bold; background: transparent;"
                )
            else:
                self._clearrate_label.setStyleSheet(
                    "color: #c62828; font-size: 11px; font-weight: bold; background: transparent;"
                )
            self._clearrate_label.setVisible(True)
        else:
            self._clearrate_label.setVisible(False)

        self._best_banner.setVisible(True)

    @staticmethod
    def _calc_wins_per_case(cases: List[AnalysisCase]) -> dict:
        """
        UX改善（第10回④）: 各ケースの「総合勝利数」を計算します。

        全7応答指標について「最小値を持つケース（複数同値は全員1位）」を
        特定し、{case_id: 勝利指標数} の辞書を返します。

        Parameters
        ----------
        cases : list of AnalysisCase
            集計対象の完了済みケース一覧。

        Returns
        -------
        dict
            {case_id: wins_count} の辞書。
        """
        wins: dict = {c.id: 0 for c in cases}
        for metric_key, _, _, _, _ in _RANKING_ITEMS:
            # この指標で有効な値を持つケースだけを対象にする
            valid = [
                (c, float(v))
                for c in cases
                if (v := c.result_summary.get(metric_key)) is not None
            ]
            if not valid:
                continue
            min_val = min(v for _, v in valid)
            # 同率1位を含む全ケースにカウント
            for c, v in valid:
                if v == min_val:
                    wins[c.id] = wins.get(c.id, 0) + 1
        return wins

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
