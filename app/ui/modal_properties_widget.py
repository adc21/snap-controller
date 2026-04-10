"""
app/ui/modal_properties_widget.py
固有値解析結果表示ウィジェット。

複数ケースの固有周期、固有周波数、参加質量比を
一覧表で比較表示します。

レイアウト:
  ┌────────────────────────────────────────────────────┐
  │ [CSVコピー]                                        │
  │ ┌─────┬──────────────────┬──────────────────────┐  │
  │ │ケース│ モード1          │ モード2          │  │
  │ │     │ 周期   周波数  %  │ 周期   周波数  %  │  │
  │ ├─────┼──────────────────┼──────────────────────┤  │
  │ │D1   │3.974s  0.252Hz  │1.581s  0.633Hz  │  │
  │ │DA   │3.812s  0.262Hz  │1.521s  0.657Hz  │  │
  │ └─────┴──────────────────┴──────────────────────┘  │
  └────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Dict, Optional, List
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QMessageBox,
)

from app.models import AnalysisCase
from app.models.period_reader import PeriodReader
from .theme import ThemeManager


class ModalPropertiesWidget(QWidget):
    """
    複数ケースの固有値解析結果（モード特性）を比較表示します。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットして表を更新
    refresh()         — 現在のケースで再描画
    set_result_dir(result_dir) — 結果フォルダのパスを指定
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._result_dir: Optional[str] = None
        self._period_data: Dict[str, Dict] = {}  # case_id -> {periods, frequencies, ...}
        self._setup_ui()

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """ケースリストをセットして表を更新します。"""
        self._cases = cases
        self.refresh()

    def set_result_dir(self, result_dir: str) -> None:
        """解析結果フォルダのパスを指定します。（後方互換性用）"""
        self._result_dir = result_dir
        self.refresh()

    def refresh(self) -> None:
        """現在のケースで表を再描画します。"""
        self._load_period_data()
        self._populate_table()

    # ------------------------------------------------------------------
    # Internal Methods
    # ------------------------------------------------------------------

    def _load_period_data(self) -> None:
        """全ケースの Period.xbn ファイルを読み込みます。

        Period.xbn の場所は、各 AnalysisCase の output_dir（またはモデルパスと同じディレクトリ）
        内のケース名フォルダから検索します。

        検索順序:
        1. case.output_dir/case.name/Period.xbn
        2. case.output_dir/Period.xbn
        3. モデルパスのディレクトリ/case.name/Period.xbn
        4. モデルパスのディレクトリ/Period.xbn
        """
        self._period_data.clear()

        print(f"[Modal] Loading period data for {len(self._cases)} cases")

        for case in self._cases:
            print(f"[Modal] Case: {case.name}, status: {case.status.name}")
            if case.status.name != "COMPLETED":
                print(f"[Modal]   Skipped (status={case.status.name})")
                continue

            period_file = None

            # 検索パターンを試す
            candidates = []

            # パターン1: case.result_path/Period.xbn（最優先）
            if hasattr(case, 'result_path') and case.result_path:
                candidates.append(Path(case.result_path) / "Period.xbn")

            # パターン2: output_dir/case.name/Period.xbn
            if case.output_dir:
                candidates.append(Path(case.output_dir) / case.name / "Period.xbn")
                candidates.append(Path(case.output_dir) / "Period.xbn")

            # パターン3: モデルパスのディレクトリ/case.name/Period.xbn
            if case.model_path:
                model_dir = Path(case.model_path).parent
                candidates.append(model_dir / case.name / "Period.xbn")
                candidates.append(model_dir / "Period.xbn")

            # 後方互換性: self._result_dir を使用
            if self._result_dir:
                candidates.append(Path(self._result_dir) / case.name / "Period.xbn")
                candidates.append(Path(self._result_dir) / "Period.xbn")

            print(f"[Modal]   Searching in {len(candidates)} locations:")
            # 候補の中から最初に存在するファイルを使用
            for candidate in candidates:
                exists = candidate.exists()
                print(f"[Modal]     {candidate}: {exists}")
                if exists:
                    period_file = candidate
                    break

            if not period_file:
                print(f"[Modal]   Period.xbn not found")
                continue

            try:
                print(f"[Modal]   Loading from: {period_file}")
                reader = PeriodReader(str(period_file))
                data = reader.get_all()
                self._period_data[case.id] = data
                print(f"[Modal]   Loaded successfully: {len(data.get('periods', {}))} modes")
            except Exception as e:
                # ファイル読み込みエラーはスキップ
                print(f"[Modal] Error loading Period.xbn for {case.name}: {e}")

    def _populate_table(self) -> None:
        """テーブルにデータを入力します。"""
        self._table.setRowCount(0)
        self._table.setColumnCount(0)

        print(f"[Modal] _populate_table: period_data count = {len(self._period_data)}")
        print(f"[Modal] Cases count = {len(self._cases)}")

        if not self._period_data:
            # データなしメッセージを表示
            print(f"[Modal] No period data available")
            self._table.insertRow(0)
            self._table.insertColumn(0)
            item = QTableWidgetItem("固有値解析結果がありません")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(0, 0, item)
            return

        # モード数を取得（全ケースの最大値）
        max_modes = 0
        for data in self._period_data.values():
            if data.get("periods"):
                max_modes = max(max_modes, len(data["periods"]))

        if max_modes == 0:
            # データなしメッセージ
            self._table.insertRow(0)
            self._table.insertColumn(0)
            item = QTableWidgetItem("モード情報がありません")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(0, 0, item)
            return

        # テーブル構成: ケース名 + モード情報（周期、周波数、参加質量比）
        # 列数: 1 (ケース名) + 3*モード数 (周期、周波数、%)
        num_cols = 1 + 3 * max_modes
        self._table.setColumnCount(num_cols)

        # ヘッダー設定
        headers = ["ケース名"]
        for mode_num in range(1, max_modes + 1):
            headers.append(f"モード{mode_num}\n周期 [s]")
            headers.append(f"モード{mode_num}\n周波数 [Hz]")
            headers.append(f"モード{mode_num}\n参加質量比 [%]")

        self._table.setHorizontalHeaderLabels(headers)

        # ケース順に行を追加
        row = 0
        for case in self._cases:
            if case.id not in self._period_data:
                continue

            self._table.insertRow(row)

            # ケース名セル
            case_item = QTableWidgetItem(case.name)
            case_item.setFlags(case_item.flags() & ~Qt.ItemIsEditable)
            case_item.setBackground(QColor(100, 100, 100) if ThemeManager.is_dark() else QColor(200, 200, 200))
            self._table.setItem(row, 0, case_item)

            # モード情報セル
            data = self._period_data[case.id]
            periods = data.get("periods", {})
            frequencies = data.get("frequencies", {})
            pm = data.get("participation_mass", {})

            col = 1
            for mode_num in range(1, max_modes + 1):
                # 周期
                period_val = periods.get(mode_num)
                if period_val is not None:
                    period_text = f"{period_val:.4f}"
                    period_item = QTableWidgetItem(period_text)
                    period_item.setFlags(period_item.flags() & ~Qt.ItemIsEditable)
                    self._table.setItem(row, col, period_item)
                col += 1

                # 周波数
                freq_val = frequencies.get(mode_num)
                if freq_val is not None:
                    freq_text = f"{freq_val:.4f}"
                    freq_item = QTableWidgetItem(freq_text)
                    freq_item.setFlags(freq_item.flags() & ~Qt.ItemIsEditable)
                    self._table.setItem(row, col, freq_item)
                col += 1

                # 参加質量比
                pm_val = pm.get(mode_num)
                if pm_val is not None:
                    pm_text = f"{pm_val:.2f}"
                    pm_item = QTableWidgetItem(pm_text)
                    pm_item.setFlags(pm_item.flags() & ~Qt.ItemIsEditable)
                    self._table.setItem(row, col, pm_item)
                col += 1

            row += 1

        # 列幅の自動調整
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _copy_to_clipboard(self) -> None:
        """テーブル全体をタブ区切りでクリップボードにコピーします。"""
        lines = []

        # ヘッダー行
        headers = []
        for col in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(col)
            headers.append(item.text() if item else "")
        lines.append("\t".join(headers))

        # データ行
        for row in range(self._table.rowCount()):
            row_data = []
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                row_data.append(item.text() if item else "")
            lines.append("\t".join(row_data))

        # クリップボードにコピー
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText("\n".join(lines))

        # メッセージ表示
        QMessageBox.information(
            self,
            "コピー完了",
            f"{self._table.rowCount()}行 × {self._table.columnCount()}列をクリップボードにコピーしました。\n"
            "Excelに貼り付けて利用できます。"
        )

    def _setup_ui(self) -> None:
        """UI を構築します。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ツールバー
        toolbar_layout = QHBoxLayout()
        toolbar_layout.addStretch()

        copy_btn = QPushButton("📋 クリップボードにコピー")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        toolbar_layout.addWidget(copy_btn)

        layout.addLayout(toolbar_layout)

        # テーブル
        self._table = QTableWidget()
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # ヘルプテキスト
        help_label = QLabel(
            "💡 各ケースの固有周期（固有振動周期）、固有周波数、参加質量比を表示します。\n"
            "固有周期が短いほど剛性が高く、長いほど柔軟な構造を示します。"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #999999; font-size: 11px;")
        layout.addWidget(help_label)
