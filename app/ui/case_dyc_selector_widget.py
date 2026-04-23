"""
app/ui/case_dyc_selector_widget.py
==================================

snap-controller ケースと SNAP 内 DYC サブケースを 2 階層ツリーで選択するウィジェット。

UI 再設計の核となる共通セレクタ。全結果タブから参照され、選択に応じて
表示対象が切り替わる。

階層:
    [ケース A]      ← snap-controller ケース
        ├ [D1 固有値解析]   ← SNAP の DYC サブケース
        ├ [D2 時刻歴応答]
        └ [D3 応答スペクトル]
    [ケース B]
        ├ [D1 固有値解析]
        └ [D2 時刻歴応答]

シグナル:
    selectionChanged(List[DycSelection])

    DycSelection は (case, dyc_index, result_dir) を持つ dataclass。
    dyc_index = -1 は「DYC なしのレガシーケース (case.result_summary のみ)」を意味する。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QLabel,
    QPushButton,
    QComboBox,
    QAbstractItemView,
)

from app.models import AnalysisCase


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------
@dataclass
class DycSelection:
    """結果表示の最小単位。1 つの (case, DYC) ペアに対応する。"""

    case: AnalysisCase
    dyc_index: int        # -1 = DYC 情報なしで case.result_summary を使う
    result_dir: Optional[Path] = None

    @property
    def display_name(self) -> str:
        """ケース名 + DYC 名の表示用ラベル。"""
        if self.dyc_index < 0:
            return self.case.name
        if 0 <= self.dyc_index < len(self.case.dyc_results):
            dr = self.case.dyc_results[self.dyc_index]
            case_no = dr.get("case_no", self.dyc_index + 1)
            dname = dr.get("case_name", f"D{case_no}")
            return f"{self.case.name} / D{case_no}:{dname}"
        return self.case.name

    @property
    def short_name(self) -> str:
        """凡例などで使う短い名前。"""
        if self.dyc_index < 0:
            return self.case.name
        if 0 <= self.dyc_index < len(self.case.dyc_results):
            dr = self.case.dyc_results[self.dyc_index]
            case_no = dr.get("case_no", self.dyc_index + 1)
            return f"{self.case.name}·D{case_no}"
        return self.case.name


# ---------------------------------------------------------------------------
# セレクタ本体
# ---------------------------------------------------------------------------
class CaseDycSelectorWidget(QWidget):
    """ケース × DYC の 2 階層チェックツリー。複数選択可能。"""

    # 選択が変わったとき（DycSelection のリスト）
    selectionChanged = Signal(list)

    # ケース単体クリック（単一ケース詳細表示用、case.id を送出）
    caseActivated = Signal(str)

    _ROLE_TYPE = Qt.UserRole + 1       # "case" | "dyc"
    _ROLE_CASE_ID = Qt.UserRole + 2    # str
    _ROLE_DYC_INDEX = Qt.UserRole + 3  # int
    _ROLE_RESULT_DIR = Qt.UserRole + 4 # str

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._suspend_signals = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """ケースリストを更新してツリーを再構築。前回の選択は可能な限り維持する。"""
        prev_keys = self._collect_checked_keys()
        self._cases = list(cases or [])
        self._rebuild_tree(prev_keys)
        self._emit_selection()

    def current_selections(self) -> List[DycSelection]:
        """現在のチェック状態から DycSelection リストを生成する。"""
        return self._build_selections()

    def selected_cases(self) -> List[AnalysisCase]:
        """チェックされているケース（DYC 単位ではなく親ケースで uniq）を返す。"""
        seen_ids = set()
        out: List[AnalysisCase] = []
        for sel in self._build_selections():
            if sel.case.id in seen_ids:
                continue
            seen_ids.add(sel.case.id)
            out.append(sel.case)
        return out

    def select_case(self, case_id: str, exclusive: bool = True) -> None:
        """指定ケース ID のみを選択する（詳細表示ジャンプ用）。"""
        self._suspend_signals = True
        self._tree.blockSignals(True)
        try:
            for i in range(self._tree.topLevelItemCount()):
                item = self._tree.topLevelItem(i)
                cid = item.data(0, self._ROLE_CASE_ID)
                match = (cid == case_id)
                if exclusive and not match:
                    self._cascade_parent_state(item, Qt.Unchecked)
                elif match:
                    self._cascade_parent_state(item, Qt.Checked)
                    item.setExpanded(True)
        finally:
            self._tree.blockSignals(False)
            self._suspend_signals = False
        self._emit_selection()

    def _cascade_parent_state(self, parent: QTreeWidgetItem, state) -> None:
        """親ケースのチェック状態を子 DYC 全てへ伝播。select_case / 全選択系で使う。"""
        if parent.childCount() == 0:
            if parent.flags() & Qt.ItemIsUserCheckable and parent.flags() & Qt.ItemIsEnabled:
                parent.setCheckState(0, state)
            return
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.flags() & Qt.ItemIsUserCheckable and child.flags() & Qt.ItemIsEnabled:
                child.setCheckState(0, state)
        # 親自身の状態は子から再計算
        self._sync_parent_from_children(parent)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        title = QLabel("解析ケース")
        title.setStyleSheet("font-weight: bold; padding: 2px;")
        layout.addWidget(title)

        hint = QLabel("チェックで比較対象を選択。ケースをチェックすると解析済み DYC 全てが選ばれます。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 10px; padding: 0 2px 4px 2px;")
        layout.addWidget(hint)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setUniformRowHeights(True)
        self._tree.setIndentation(14)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._tree, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_all = QPushButton("全選択")
        btn_all.setFixedHeight(24)
        btn_all.clicked.connect(self._select_all)
        btn_row.addWidget(btn_all)
        btn_none = QPushButton("全解除")
        btn_none.setFixedHeight(24)
        btn_none.clicked.connect(self._clear_all)
        btn_row.addWidget(btn_none)
        layout.addLayout(btn_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("DYC 絞込:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("すべて", "all")
        self._filter_combo.addItem("固有値解析のみ", "period")
        self._filter_combo.addItem("時刻歴のみ", "time_history")
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_combo, stretch=1)
        layout.addLayout(filter_row)

        self._status_label = QLabel("選択: 0 件")
        self._status_label.setStyleSheet("color: #555; font-size: 10px; padding: 2px 4px;")
        layout.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # ツリー構築
    # ------------------------------------------------------------------
    def _rebuild_tree(self, prev_keys: set) -> None:
        self._suspend_signals = True
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for case in self._cases:
                case_item = self._build_case_item(case)
                self._tree.addTopLevelItem(case_item)

                dyc_results = getattr(case, "dyc_results", []) or []
                selectable_children = 0

                if dyc_results:
                    for i, dr in enumerate(dyc_results):
                        child = self._build_dyc_item(case, i, dr)
                        case_item.addChild(child)
                        if child.flags() & Qt.ItemIsUserCheckable and child.flags() & Qt.ItemIsEnabled:
                            selectable_children += 1
                        key = (case.id, i)
                        if key in prev_keys:
                            child.setCheckState(0, Qt.Checked)
                    case_item.setExpanded(any(
                        (case.id, i) in prev_keys for i in range(len(dyc_results))
                    ))
                else:
                    # DYC 情報なし: ケース自身を「単一エントリ」として扱う
                    if (case.id, -1) in prev_keys and case.result_summary:
                        case_item.setCheckState(0, Qt.Checked)

                # 親ケースのチェック状態を子に応じて調整
                self._sync_parent_from_children(case_item)

                # 選択可能な子が 0 件かつ DYC なしかつ結果なし → ケース自体を無効化
                if not dyc_results and not case.result_summary:
                    case_item.setFlags(case_item.flags() & ~Qt.ItemIsEnabled)
                    case_item.setForeground(0, QBrush(QColor("#aaaaaa")))
            self._apply_filter()
        finally:
            self._tree.blockSignals(False)
            self._suspend_signals = False

    def _build_case_item(self, case: AnalysisCase) -> QTreeWidgetItem:
        has_result = bool(case.result_summary) or bool(case.dyc_results)
        status = case.get_status_label() if hasattr(case, "get_status_label") else ""
        label = case.name
        if not has_result:
            label = f"{case.name}  (結果なし)"
        elif status:
            label = f"{case.name}  [{status}]"

        item = QTreeWidgetItem([label])
        # AutoTristate は使わず、手動で _sync_parent_from_children により同期する
        # (AutoTristate は子の setCheckState を打ち消してしまう既知の不具合がある)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(0, Qt.Unchecked)
        item.setData(0, self._ROLE_TYPE, "case")
        item.setData(0, self._ROLE_CASE_ID, case.id)
        item.setData(0, self._ROLE_DYC_INDEX, -1)
        return item

    def _build_dyc_item(self, case: AnalysisCase, dyc_index: int, dr: dict) -> QTreeWidgetItem:
        case_no = dr.get("case_no", dyc_index + 1)
        case_name = dr.get("case_name", f"D{case_no}")
        run_flag = dr.get("run_flag", 0)
        has_result = dr.get("has_result", False)
        result_dir = dr.get("result_dir", "")

        base_label = f"D{case_no}: {case_name}"
        if not run_flag:
            label = f"{base_label}  (スキップ)"
        elif not has_result:
            label = f"{base_label}  (結果なし)"
        else:
            label = base_label

        item = QTreeWidgetItem([label])
        item.setData(0, self._ROLE_TYPE, "dyc")
        item.setData(0, self._ROLE_CASE_ID, case.id)
        item.setData(0, self._ROLE_DYC_INDEX, dyc_index)
        item.setData(0, self._ROLE_RESULT_DIR, result_dir)

        if has_result and run_flag:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
        else:
            # 選択不可: チェックボックスごと無効化
            item.setFlags(item.flags() & ~(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled))
            item.setForeground(0, QBrush(QColor("#aaaaaa")))

        tag = self._classify_dyc(case_name)
        if tag:
            item.setData(0, Qt.UserRole + 10, tag)
        return item

    @staticmethod
    def _classify_dyc(dyc_case_name: str) -> str:
        """DYC 名から種別を推定（固有値/時刻歴/その他）。フィルタ用。"""
        s = (dyc_case_name or "").lower()
        if "固有" in dyc_case_name or "period" in s or "eigen" in s:
            return "period"
        if "時刻歴" in dyc_case_name or "history" in s or "dynamic" in s or "hist" in s:
            return "time_history"
        return "other"

    # ------------------------------------------------------------------
    # イベント
    # ------------------------------------------------------------------
    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suspend_signals:
            return

        self._suspend_signals = True
        self._tree.blockSignals(True)
        try:
            kind = item.data(0, self._ROLE_TYPE)
            if kind == "case":
                # 親 → 子に伝播
                state = item.checkState(0)
                if state in (Qt.Checked, Qt.Unchecked):
                    for i in range(item.childCount()):
                        child = item.child(i)
                        if child.flags() & Qt.ItemIsUserCheckable and child.flags() & Qt.ItemIsEnabled:
                            child.setCheckState(0, state)
            elif kind == "dyc":
                parent = item.parent()
                if parent is not None:
                    self._sync_parent_from_children(parent)
        finally:
            self._tree.blockSignals(False)
            self._suspend_signals = False

        self._emit_selection()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        kind = item.data(0, self._ROLE_TYPE)
        if kind == "case":
            cid = item.data(0, self._ROLE_CASE_ID)
            if cid:
                self.caseActivated.emit(cid)

    def _sync_parent_from_children(self, parent: QTreeWidgetItem) -> None:
        """子のチェック状態から親の状態を再計算。"""
        n = parent.childCount()
        if n == 0:
            return
        checked = 0
        checkable = 0
        for i in range(n):
            child = parent.child(i)
            if not (child.flags() & Qt.ItemIsUserCheckable and child.flags() & Qt.ItemIsEnabled):
                continue
            checkable += 1
            if child.checkState(0) == Qt.Checked:
                checked += 1
        if checkable == 0:
            parent.setFlags(parent.flags() & ~Qt.ItemIsUserCheckable)
            return
        if checked == 0:
            parent.setCheckState(0, Qt.Unchecked)
        elif checked == checkable:
            parent.setCheckState(0, Qt.Checked)
        else:
            parent.setCheckState(0, Qt.PartiallyChecked)

    def _select_all(self) -> None:
        self._suspend_signals = True
        self._tree.blockSignals(True)
        try:
            for i in range(self._tree.topLevelItemCount()):
                parent = self._tree.topLevelItem(i)
                if not (parent.flags() & Qt.ItemIsEnabled):
                    continue
                if parent.childCount() == 0:
                    if parent.flags() & Qt.ItemIsUserCheckable:
                        parent.setCheckState(0, Qt.Checked)
                else:
                    for j in range(parent.childCount()):
                        child = parent.child(j)
                        if child.flags() & Qt.ItemIsUserCheckable and child.flags() & Qt.ItemIsEnabled:
                            child.setCheckState(0, Qt.Checked)
                    self._sync_parent_from_children(parent)
        finally:
            self._tree.blockSignals(False)
            self._suspend_signals = False
        self._emit_selection()

    def _clear_all(self) -> None:
        self._suspend_signals = True
        self._tree.blockSignals(True)
        try:
            for i in range(self._tree.topLevelItemCount()):
                parent = self._tree.topLevelItem(i)
                if parent.flags() & Qt.ItemIsUserCheckable:
                    parent.setCheckState(0, Qt.Unchecked)
                for j in range(parent.childCount()):
                    child = parent.child(j)
                    if child.flags() & Qt.ItemIsUserCheckable:
                        child.setCheckState(0, Qt.Unchecked)
        finally:
            self._tree.blockSignals(False)
            self._suspend_signals = False
        self._emit_selection()

    def _apply_filter(self) -> None:
        mode = self._filter_combo.currentData() if hasattr(self, "_filter_combo") else "all"
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            any_visible = parent.childCount() == 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                tag = child.data(0, Qt.UserRole + 10) or ""
                visible = (mode == "all") or (mode == tag)
                child.setHidden(not visible)
                if visible:
                    any_visible = True
            parent.setHidden(not any_visible and parent.childCount() > 0)

    # ------------------------------------------------------------------
    # 選択結果の収集
    # ------------------------------------------------------------------
    def _build_selections(self) -> List[DycSelection]:
        out: List[DycSelection] = []
        cases_by_id = {c.id: c for c in self._cases}
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            cid = parent.data(0, self._ROLE_CASE_ID)
            case = cases_by_id.get(cid)
            if case is None:
                continue
            if parent.childCount() == 0:
                # DYC なしのレガシーケース: 親自身のチェックを見る
                if parent.flags() & Qt.ItemIsUserCheckable and parent.checkState(0) == Qt.Checked:
                    out.append(DycSelection(case=case, dyc_index=-1))
                continue
            for j in range(parent.childCount()):
                child = parent.child(j)
                if not (child.flags() & Qt.ItemIsUserCheckable and child.flags() & Qt.ItemIsEnabled):
                    continue
                if child.checkState(0) != Qt.Checked:
                    continue
                dyc_idx = child.data(0, self._ROLE_DYC_INDEX)
                result_dir = child.data(0, self._ROLE_RESULT_DIR) or ""
                out.append(DycSelection(
                    case=case,
                    dyc_index=int(dyc_idx),
                    result_dir=Path(result_dir) if result_dir else None,
                ))
        return out

    def _collect_checked_keys(self) -> set:
        """現在チェックされている (case_id, dyc_index) の集合を返す。再構築時の復元に使う。"""
        keys = set()
        for sel in self._build_selections():
            keys.add((sel.case.id, sel.dyc_index))
        return keys

    def _emit_selection(self) -> None:
        if self._suspend_signals:
            return
        selections = self._build_selections()
        n_case = len({s.case.id for s in selections})
        n_dyc = len(selections)
        if n_dyc == 0:
            self._status_label.setText("選択: 0 件")
        elif n_dyc == n_case:
            self._status_label.setText(f"選択: {n_case} ケース")
        else:
            self._status_label.setText(f"選択: {n_case} ケース / {n_dyc} DYC")
        self.selectionChanged.emit(selections)
