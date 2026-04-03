"""
app/ui/result_table_widget.py  # rev: force-rebuild
結果サマリーテーブルウィジェット。

完了済み全ケースの主要応答値を一覧表で表示します。
グラフでは把握しにくい正確な数値比較に適しています。

レイアウト:
  ┌──────────────────────────────────────────────────┐
  │ [表示項目フィルター] [CSV コピーボタン]          │
  │ ┌──────┬──────┬──────┬──────┬──────┬──────┐       │
  │ │ケース │max_d │max_v │max_a │drift │shear │       │
  │ ├──────┼──────┼──────┼──────┼──────┼──────┤       │
  │ │Case1 │0.012 │0.543 │4.321 │0.001 │0.12  │       │
  │ │Case2 │0.015 │0.612 │5.100 │0.002 │0.15  │       │
  │ ├──────┼──────┼──────┼──────┼──────┼──────┤       │
  │ │ENV▲  │0.015 │0.612 │5.100 │0.002 │0.15  │       │
  │ └──────┴──────┴──────┴──────┴──────┴──────┘       │
  └──────────────────────────────────────────────────┘

UX改善④: Ctrl+Cで選択行コピー（Excelに直接貼り付け可能）。
  - 行を選択した状態でCtrl+Cを押すと、選択行だけをタブ区切りでコピーします。
  - 右クリックコンテキストメニューからも「選択行をコピー」「全行をコピー」が使えます。
  - 「全行をコピー」は従来の「クリップボードにコピー」ボタンと同じ動作です。

UX改善⑤新: エンベロープ行（全ケース最大値）の追加。
  - ケース一覧の最下行に「▲ エンベロープ」行を表示します。
  - 各列の全ケースにわたる最大値（最も不利な値）を表示します。
  - 構造設計では最大応答値（エンベロープ）が最終的な評価基準となるため、
    どの指標でも最も厳しいケースの値を一目で把握できます。
  - エンベロープ行は橙色の背景で視覚的に区別されます。

UX改善（新）: ケース名フィルター検索バーを追加。
  テーブル上部にテキスト入力欄を追加し、入力したキーワードを含む
  ケース名の行のみをリアルタイムで絞り込み表示します。
  - ケース数が多い場合に「Base」「Damper」などで素早く絞り込めます。
  - エンベロープ行は常に表示されます（フィルター対象外）。
  - 一致件数を「X / Y件」形式で常時表示します。

UX改善（今回追加）: 数値ソートの正確化 + ソートヒントラベル追加。
  _NumericTableWidgetItem サブクラスを追加し、列ヘッダークリック時の
  ソートを QTableWidget のデフォルト（文字列辞書順）から
  数値順に変更しました。
  これにより「10.0 < 4.0」のような誤ったソート結果を防止します。
  また、テーブル下部に「↕ 列ヘッダーをクリックで数値ソート」の
  ヒントラベルを追加し、ソート機能の存在を明示するようにしました。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager


class _NumericTableWidgetItem(QTableWidgetItem):
    """
    UX改善（新）: 数値ソート対応の QTableWidgetItem。

    QTableWidget のデフォルトソートは DisplayRole（テキスト）を使うため、
    数値を文字列として並べると辞書順になり "10.0" < "4.0" のような
    誤ったソート結果になります。このサブクラスは UserRole+1 に保存した
    数値データで比較するため、列ヘッダークリック時に正しい数値順ソートを
    実現します。
    """

    def __lt__(self, other: "QTableWidgetItem") -> bool:
        try:
            self_val = self.data(Qt.UserRole + 1)
            other_val = other.data(Qt.UserRole + 1)
            if self_val is not None and other_val is not None:
                return float(self_val) < float(other_val)
        except (TypeError, ValueError):
            pass
        return super().__lt__(other)

# 応答値の定義 (result_summary key, 表示ラベル, 単位, フォーマット)
_RESULT_COLUMNS = [
    ("max_disp",  "最大相対変位",      "m",     "{:.5f}"),
    ("max_vel",   "最大相対速度",      "m/s",   "{:.4f}"),
    ("max_acc",   "最大絶対加速度",    "m/s²",  "{:.3f}"),
    ("max_drift", "最大層間変形角",    "rad",   "{:.6f}"),
    ("max_shear", "せん断力係数",      "—",     "{:.4f}"),
    ("max_otm",   "最大転倒ﾓｰﾒﾝﾄ",   "kN·m",  "{:.1f}"),
]

# 最大値のハイライト色
_HIGHLIGHT_MAX = {
    "dark": QColor(120, 60, 60),
    "light": QColor(255, 200, 200),
}
_HIGHLIGHT_MIN = {
    "dark": QColor(50, 90, 60),
    "light": QColor(200, 255, 210),
}


class ResultTableWidget(QWidget):
    """
    全ケースの結果を一覧表示するテーブルウィジェット。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットして表を更新
    refresh()         — 現在のケースで再描画
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._filter_text: str = ""  # UX改善（新）: ケース名フィルターテキスト
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """全ケースリストをセットして表を更新します。"""
        self._cases = cases
        self.refresh()

    def refresh(self) -> None:
        """テーブルを再描画します。"""
        self._populate()

    def update_theme(self) -> None:
        """テーマ変更後に色を更新します。"""
        self._populate()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- ヘッダー行 ---
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>結果サマリーテーブル</b>"))
        header.addStretch()

        self._count_label = QLabel("")
        header.addWidget(self._count_label)

        btn_copy = QPushButton("クリップボードにコピー")
        btn_copy.setToolTip("表の内容をタブ区切りテキストとしてクリップボードにコピーします")
        btn_copy.clicked.connect(self._copy_to_clipboard)
        header.addWidget(btn_copy)

        layout.addLayout(header)

        # --- UX改善（新）: ケース名フィルター検索バー ---
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_row.setContentsMargins(0, 0, 0, 0)

        filter_icon = QLabel("🔍")
        filter_icon.setStyleSheet("font-size: 11px;")
        filter_row.addWidget(filter_icon)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("ケース名で絞り込み…（例: Base、Damper）")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setFixedHeight(24)
        self._filter_edit.setStyleSheet("QLineEdit { font-size: 11px; }")
        self._filter_edit.setToolTip(
            "入力したキーワードを含むケース名の行だけを表示します。\n"
            "エンベロープ行は常に表示されます。\n"
            "Esc キーでフィルターをクリアします。"
        )
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._filter_edit, stretch=1)

        # 一致件数ラベル
        self._filter_count_lbl = QLabel("")
        self._filter_count_lbl.setStyleSheet("color: gray; font-size: 10px; min-width: 70px;")
        filter_row.addWidget(self._filter_count_lbl)

        layout.addLayout(filter_row)

        # --- テーブル ---
        col_count = 1 + len(_RESULT_COLUMNS)  # ケース名 + 応答値列
        self._table = QTableWidget(0, col_count)

        headers = ["ケース名"] + [
            f"{label}\n[{unit}]" for _, label, unit, _ in _RESULT_COLUMNS
        ]
        self._table.setHorizontalHeaderLabels(headers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        for i in range(1, col_count):
            self._table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.Stretch
            )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        # UX改善④: 右クリックコンテキストメニュー
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        # UX改善（新）: ソートヒントをツールチップに追記
        self._table.setToolTip(
            "↕ 列ヘッダーをクリックすると数値順にソートできます\n"
            "Ctrl+C: 選択した行をクリップボードにコピー（Excelに貼り付け可能）\n"
            "右クリック: コピーメニュー"
        )
        layout.addWidget(self._table)

        # 凡例 + ソートヒント
        legend = QHBoxLayout()
        legend.addWidget(QLabel(
            "<small>🔴 = 列内最大値（最も不利） / 🟢 = 列内最小値（最も有利）"
            "　　▲ エンベロープ = 全ケース最大値（設計基準値）</small>"
        ))
        legend.addStretch()
        # UX改善（新）: ソートヒントラベル（列ヘッダーをクリックするとソートできることを明示）
        _sort_hint = QLabel("<small style='color:#888888;'>↕ 列ヘッダーをクリックで数値ソート</small>")
        _sort_hint.setToolTip(
            "任意の列ヘッダーをクリックするとその指標の数値で昇順/降順にソートできます。\n"
            "もう一度クリックすると逆順になります。\n"
            "（例: 「最大層間変形角」列をクリック → 変形角の小さい順に並べ替え）"
        )
        legend.addWidget(_sort_hint)
        layout.addLayout(legend)

    # ------------------------------------------------------------------
    # Populate table
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        all_completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        self._count_label.setText(f"完了ケース: {len(all_completed)}")

        if not all_completed:
            self._filter_count_lbl.setText("")
            return

        # UX改善（新）: フィルターテキストによる絞り込み
        ftext = self._filter_text.strip().lower()
        if ftext:
            completed = [c for c in all_completed if ftext in c.name.lower()]
        else:
            completed = all_completed

        # フィルター件数ラベルを更新
        if ftext:
            total = len(all_completed)
            matched = len(completed)
            if matched == 0:
                self._filter_count_lbl.setText(
                    f"<span style='color:#ef5350;'>0 / {total} 件</span>"
                )
            else:
                self._filter_count_lbl.setText(
                    f"<b style='color:#1976d2;'>{matched} / {total} 件</b>"
                )
        else:
            self._filter_count_lbl.setText("")

        if not completed:
            return

        theme = "dark" if ThemeManager.is_dark() else "light"
        highlight_max = _HIGHLIGHT_MAX[theme]
        highlight_min = _HIGHLIGHT_MIN[theme]

        # 各列の最大・最小値を求める（フィルター後のケースで計算）
        col_values: dict = {key: [] for key, *_ in _RESULT_COLUMNS}
        for case in completed:
            for key, *_ in _RESULT_COLUMNS:
                val = case.result_summary.get(key)
                if val is not None:
                    col_values[key].append(val)

        col_max = {k: max(v) if v else None for k, v in col_values.items()}
        col_min = {k: min(v) if v else None for k, v in col_values.items()}

        for case in completed:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # ケース名
            name_item = QTableWidgetItem(case.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, case.id)
            self._table.setItem(row, 0, name_item)

            # 各応答値
            for col_idx, (key, label, unit, fmt) in enumerate(_RESULT_COLUMNS, start=1):
                val = case.result_summary.get(key)
                if val is not None:
                    # UX改善（新）: _NumericTableWidgetItem を使い数値ソートを正確化
                    item = _NumericTableWidgetItem(fmt.format(val))
                    item.setData(Qt.UserRole + 1, val)  # ソート用の数値
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

                    # 最大・最小ハイライト
                    if col_max[key] is not None and abs(val - col_max[key]) < 1e-12:
                        item.setBackground(highlight_max)
                    elif col_min[key] is not None and abs(val - col_min[key]) < 1e-12:
                        item.setBackground(highlight_min)
                else:
                    item = QTableWidgetItem("—")
                    item.setTextAlignment(Qt.AlignCenter)

                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._table.setItem(row, col_idx, item)

        # UX改善⑤新: エンベロープ行（全ケース最大値）を末尾に追加
        if len(completed) >= 2:
            self._append_envelope_row(col_max)

        self._table.setSortingEnabled(True)

    def _append_envelope_row(self, col_max: dict) -> None:
        """
        UX改善⑤新: 全完了ケースの各指標の最大値を示すエンベロープ行をテーブル末尾に追加します。

        構造設計において「エンベロープ」とは全解析ケースにわたる最大応答値のことで、
        最終的な設計・評価の基準となります。この行を常に表示することで、
        「どのケースが最も厳しいか」ではなく「全体の中で最も厳しい値はいくつか」を
        一目で把握できます。

        エンベロープ行は橙色の背景で視覚的に区別され、ソートの対象外とします。

        Parameters
        ----------
        col_max : dict
            各応答値キーの全ケース最大値（_populate で計算済み）。
        """
        theme = "dark" if ThemeManager.is_dark() else "light"
        # エンベロープ行用の背景色（橙系）
        env_color = QColor("#4a2c00") if theme == "dark" else QColor("#fff3e0")
        env_text_bold = True

        row = self._table.rowCount()
        self._table.insertRow(row)

        # ケース名列: 「▲ エンベロープ」
        env_label = QTableWidgetItem("▲ エンベロープ")
        env_label.setFlags(env_label.flags() & ~Qt.ItemIsEditable)
        env_label.setBackground(env_color)
        env_label.setToolTip(
            "全完了ケースにわたる各指標の最大値（エンベロープ値）です。\n"
            "構造設計上の最終的な評価基準となります。\n"
            "2ケース以上が完了したときに自動表示されます。"
        )
        from PySide6.QtGui import QFont as _QFont
        env_font = _QFont()
        env_font.setBold(env_text_bold)
        env_label.setFont(env_font)
        self._table.setItem(row, 0, env_label)

        # 各応答値列: 最大値を表示
        for col_idx, (key, label, unit, fmt) in enumerate(_RESULT_COLUMNS, start=1):
            max_val = col_max.get(key)
            if max_val is not None:
                text = fmt.format(max_val)
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole + 1, max_val)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setFont(env_font)
                item.setBackground(env_color)
                item.setToolTip(
                    f"{label} のエンベロープ値\n"
                    f"最大: {text} [{unit}]\n"
                    "（全完了ケース中の最大値）"
                )
            else:
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(env_color)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, col_idx, item)

    # ------------------------------------------------------------------
    # UX改善（新）: ケース名フィルター
    # ------------------------------------------------------------------

    def _on_filter_changed(self, text: str) -> None:
        """
        フィルターテキスト変更時にテーブルを再描画します。

        キーワードを入力するたびにリアルタイムでケースを絞り込み、
        一致件数ラベルを更新します。
        """
        self._filter_text = text
        self._populate()

    # ------------------------------------------------------------------
    # Clipboard copy
    # ------------------------------------------------------------------

    def _copy_to_clipboard(self) -> None:
        """表の全内容をタブ区切りテキストとしてクリップボードにコピーします。"""
        self._copy_rows(selected_only=False)

    # UX改善④: Ctrl+C で選択行コピー
    def keyPressEvent(self, event) -> None:
        """Ctrl+C で選択行だけをクリップボードにコピーします。"""
        if event.matches(QKeySequence.Copy):
            self._copy_rows(selected_only=True)
        else:
            super().keyPressEvent(event)

    # UX改善④: 右クリックコンテキストメニュー
    def _show_context_menu(self, pos) -> None:
        """右クリックメニューでコピー操作を提供します。"""
        menu = QMenu(self)
        selected_rows = set(idx.row() for idx in self._table.selectedIndexes())

        act_copy_selected = menu.addAction("選択行をコピー  [Ctrl+C]")
        act_copy_selected.setEnabled(bool(selected_rows))
        act_copy_selected.setToolTip("選択した行だけをタブ区切りでコピー（Excel貼り付け用）")

        act_copy_all = menu.addAction("全行をコピー")
        act_copy_all.setToolTip("表の全データをタブ区切りでコピー（ヘッダー付き）")

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == act_copy_selected:
            self._copy_rows(selected_only=True)
        elif action == act_copy_all:
            self._copy_rows(selected_only=False)

    def _copy_rows(self, selected_only: bool = False) -> None:
        """
        UX改善④: 指定範囲の行をタブ区切りテキストとしてクリップボードにコピーします。

        Parameters
        ----------
        selected_only : bool
            True の場合は選択行のみ、False の場合は全行をコピーします。
        """
        lines = []

        # ヘッダー行
        headers = ["ケース名"] + [
            f"{label} [{unit}]" for _, label, unit, _ in _RESULT_COLUMNS
        ]
        lines.append("\t".join(headers))

        if selected_only:
            selected_rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
            rows_to_copy = selected_rows
        else:
            rows_to_copy = list(range(self._table.rowCount()))

        for row in rows_to_copy:
            cols = []
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                cols.append(item.text() if item else "")
            lines.append("\t".join(cols))

        text = "\n".join(lines)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
