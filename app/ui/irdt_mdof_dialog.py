"""
app/ui/irdt_mdof_dialog.py

iRDT最適解 - 多質点系ダイアログ。

adc-tools の `IRDTMdofOptParamsView` を PySide6 に移植したもの。
モード同調方式で多質点系 iRDT の各層ダンパーパラメータを計算します。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.irdt import (
    eigen_analysis,
    irdt_opt_param_mdof,
    trim_number_array,
)


_MODE_STIFFNESS = "stiffness"
_MODE_VECTOR = "vector"

# デフォルト初期値 (adc-tools と一致)
_DEFAULT_M = [10000.0, 10000.0]
_DEFAULT_K = [10_000_000.0, 10_000_000.0]
_DEFAULT_VECTOR = [1.0, 1.0]
_DEFAULT_MD = [1000.0, 1000.0]
_DEFAULT_T0 = 1.0


class IRDTMdofDialog(QDialog):
    """
    iRDT 最適解 - 多質点系 (MDOF) ダイアログ。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("iRDT最適解 - 多質点系")
        self.resize(900, 680)

        self._input_mode = _MODE_STIFFNESS
        self._t0 = _DEFAULT_T0

        self._build_ui()
        self._connect_signals()
        self._populate_defaults()
        self._recompute()

    # ---- UI 構築 ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ヘッダー: 入力モード選択 + 層数 + (vector時) 固有周期
        header = QGroupBox("設定")
        hform = QFormLayout(header)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("層剛性から計算", _MODE_STIFFNESS)
        self._mode_combo.addItem("固有ベクトルを直接入力", _MODE_VECTOR)
        hform.addRow("入力タイプ", self._mode_combo)

        self._n_spin = QSpinBox()
        self._n_spin.setRange(1, 50)
        self._n_spin.setValue(len(_DEFAULT_M))
        hform.addRow("層数", self._n_spin)

        self._t0_edit = QLineEdit(f"{_DEFAULT_T0:g}")
        self._t0_edit.setValidator(QDoubleValidator(0.0, 100.0, 6))
        self._t0_edit.setAlignment(Qt.AlignRight)
        self._t0_label = QLabel("固有周期 [s]")
        hform.addRow(self._t0_label, self._t0_edit)

        root.addWidget(header)

        # 入力テーブル
        self._input_table = QTableWidget(0, 3)
        self._input_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._input_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._input_table.verticalHeader().setDefaultSectionSize(24)
        root.addWidget(self._input_table)

        # サマリ
        summary = QGroupBox("最適値")
        sform = QFormLayout(summary)
        self._lbl_mu = QLabel("-")
        self._lbl_gamma = QLabel("-")
        self._lbl_h = QLabel("-")
        sform.addRow("有効質量比 μ [-]", self._lbl_mu)
        sform.addRow("振動数比 γ [-]", self._lbl_gamma)
        sform.addRow("減衰定数 h [-]", self._lbl_h)
        root.addWidget(summary)

        # 結果テーブル (列構成は入力モードに応じて変化)
        self._result_table = QTableWidget(0, 4)
        self._apply_result_columns()
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._result_table.verticalHeader().setDefaultSectionSize(24)
        root.addWidget(self._result_table)

        # ボタン
        btn_row = QHBoxLayout()
        btn_reset = QPushButton("リセット")
        btn_reset.clicked.connect(self._on_reset)
        btn_row.addWidget(btn_reset)
        self._btn_copy = QPushButton("クリップボードへコピー")
        self._btn_copy.clicked.connect(self._on_copy_clipboard)
        btn_row.addWidget(self._btn_copy)
        self._btn_csv = QPushButton("CSV出力")
        self._btn_csv.clicked.connect(self._on_export_csv)
        btn_row.addWidget(self._btn_csv)
        btn_row.addStretch(1)
        btn_close = QDialogButtonBox(QDialogButtonBox.Close)
        btn_close.rejected.connect(self.reject)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

    # ---- シグナル ------------------------------------------------------
    def _connect_signals(self) -> None:
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._n_spin.valueChanged.connect(self._on_n_changed)
        self._t0_edit.editingFinished.connect(self._recompute)
        self._input_table.itemChanged.connect(lambda _: self._recompute())

    def _on_mode_changed(self, _idx: int) -> None:
        self._input_mode = self._mode_combo.currentData()
        self._update_table_headers()
        self._apply_result_columns()
        self._t0_label.setVisible(self._input_mode == _MODE_VECTOR)
        self._t0_edit.setVisible(self._input_mode == _MODE_VECTOR)
        self._populate_defaults(preserve_rows=False)
        self._recompute()

    def _on_n_changed(self, n: int) -> None:
        self._resize_input_rows(n)
        self._recompute()

    def _on_reset(self) -> None:
        self._mode_combo.setCurrentIndex(0)
        self._input_mode = _MODE_STIFFNESS
        self._n_spin.setValue(len(_DEFAULT_M))
        self._t0_edit.setText(f"{_DEFAULT_T0:g}")
        self._populate_defaults(preserve_rows=False)
        self._recompute()

    # ---- テーブル操作 --------------------------------------------------
    def _update_table_headers(self) -> None:
        if self._input_mode == _MODE_STIFFNESS:
            self._input_table.setHorizontalHeaderLabels(
                ["質量 m [ton]", "剛性 k [kN/m]", "ダンパー質量 md [ton]"]
            )
        else:
            self._input_table.setHorizontalHeaderLabels(
                ["質量 m [ton]", "固有ベクトル φ [-]", "ダンパー質量 md [ton]"]
            )

    def _populate_defaults(self, preserve_rows: bool = True) -> None:
        self._input_table.blockSignals(True)
        try:
            self._update_table_headers()
            n = self._n_spin.value()
            if not preserve_rows:
                self._input_table.setRowCount(0)
            self._resize_input_rows(n)

            # 既存行が空ならデフォルト値で埋める
            for row in range(n):
                m_val = _DEFAULT_M[row] if row < len(_DEFAULT_M) else _DEFAULT_M[-1]
                md_val = _DEFAULT_MD[row] if row < len(_DEFAULT_MD) else _DEFAULT_MD[-1]
                if self._input_mode == _MODE_STIFFNESS:
                    col2 = _DEFAULT_K[row] if row < len(_DEFAULT_K) else _DEFAULT_K[-1]
                else:
                    col2 = _DEFAULT_VECTOR[row] if row < len(_DEFAULT_VECTOR) else _DEFAULT_VECTOR[-1]
                self._ensure_item(row, 0, m_val)
                self._ensure_item(row, 1, col2)
                self._ensure_item(row, 2, md_val)
        finally:
            self._input_table.blockSignals(False)

    def _ensure_item(self, row: int, col: int, value: float) -> None:
        item = self._input_table.item(row, col)
        if item is None:
            item = QTableWidgetItem(f"{value:g}")
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._input_table.setItem(row, col, item)
        elif not item.text().strip():
            item.setText(f"{value:g}")

    def _resize_input_rows(self, n: int) -> None:
        self._input_table.setRowCount(n)
        self._input_table.setVerticalHeaderLabels([str(i + 1) for i in range(n)])

    def _read_column(self, col: int) -> List[float]:
        n = self._input_table.rowCount()
        result: List[float] = []
        for row in range(n):
            item = self._input_table.item(row, col)
            if item is None:
                result.append(0.0)
                continue
            try:
                result.append(float(item.text()))
            except (TypeError, ValueError):
                result.append(0.0)
        return result

    # ---- 計算 ---------------------------------------------------------
    def _recompute(self) -> None:
        ms_raw = self._read_column(0)
        col2 = self._read_column(1)
        mds_raw = self._read_column(2)

        if self._input_mode == _MODE_STIFFNESS:
            ms = trim_number_array(ms_raw)
            ks = trim_number_array(col2)
            n = min(len(ms), len(ks))
            if n <= 0:
                self._clear_results()
                return
            omegas, vectors = eigen_analysis(ms[:n], ks[:n])
            if len(omegas) == 0:
                self._clear_results()
                return
            # 1次モードを使用
            w0 = float(omegas[0])
            phi = vectors[:, 0].tolist() if vectors.shape[1] > 0 else [1.0] * n
            # 結果テーブルの周期/ベクトルは全モード分表示
            periods = [(2.0 * math.pi / w) if w > 0 else 0.0 for w in omegas]
            mode_vectors = [vectors[:, i].tolist() for i in range(vectors.shape[1])]
        else:
            # vector モード: t0 と固有ベクトルから w0 を決定
            try:
                t0 = float(self._t0_edit.text())
            except (TypeError, ValueError):
                t0 = _DEFAULT_T0
            if t0 <= 0:
                self._clear_results()
                return
            ms = trim_number_array(ms_raw)
            vector_input = trim_number_array(col2, null_to_zero=True)
            n = min(len(ms), len(vector_input))
            if n <= 0:
                self._clear_results()
                return
            w0 = 2.0 * math.pi / t0
            phi = vector_input[:n]
            periods = [t0]
            mode_vectors = [phi]

        mds = mds_raw[:n]
        # 全ての md を 0 以上として保持 (trim しない: 0 でも結果を計算可能)

        res = irdt_opt_param_mdof(w0, ms[:n], phi, mds)

        self._lbl_mu.setText(self._fmt(res.mu, 6))
        self._lbl_gamma.setText(self._fmt(res.gamma, 6))
        self._lbl_h.setText(self._fmt(res.h, 6))

        self._fill_result_table(periods, mode_vectors, res.cd, res.kb, n)

    def _apply_result_columns(self) -> None:
        """結果テーブルの列構成を入力モードに応じて切り替えます。

        - stiffness: 固有周期 / １次モード固有ベクトル / cd / kb (4 列)
        - vector   : cd / kb (2 列, 固有周期と固有ベクトルはユーザー入力済のため省略)
        """
        if self._input_mode == _MODE_STIFFNESS:
            self._result_table.setColumnCount(4)
            self._result_table.setHorizontalHeaderLabels(
                [
                    "固有周期 [s]",
                    "１次モード固有ベクトル [-]",
                    "ダンパー最適減衰係数 cd [kNs/m]",
                    "ダンパー最適支持部材剛性 kb [kN/m]",
                ]
            )
        else:
            self._result_table.setColumnCount(2)
            self._result_table.setHorizontalHeaderLabels(
                [
                    "ダンパー最適減衰係数 cd [kNs/m]",
                    "ダンパー最適支持部材剛性 kb [kN/m]",
                ]
            )

    def _fill_result_table(
        self,
        periods: Sequence[float],
        mode_vectors: Sequence[Sequence[float]],
        cds: Sequence[float],
        kbs: Sequence[float],
        n_layers: int,
    ) -> None:
        """
        adc-tools Results.tsx と同じレイアウト:
          - stiffness モード: n 行, 各行 i = 「i 次モードの周期」「1 次モード固有ベクトルの i 成分」
            「i 層の cd」「i 層の kb」。
          - vector モード   : n 行, 各行 i = 「i 層の cd」「i 層の kb」のみ。
        """
        self._result_table.setRowCount(n_layers)
        self._result_table.setVerticalHeaderLabels([str(i + 1) for i in range(n_layers)])

        if self._input_mode == _MODE_STIFFNESS:
            # 1 次モードの固有ベクトル成分を各層 (行) に割り当てる
            first_vec = mode_vectors[0] if len(mode_vectors) > 0 else [0.0] * n_layers
            for row in range(n_layers):
                # i 次モードの周期 (i = row)
                period_val = periods[row] if row < len(periods) else float("nan")
                vec_val = first_vec[row] if row < len(first_vec) else 0.0
                cd_val = cds[row] if row < len(cds) else float("nan")
                kb_val = kbs[row] if row < len(kbs) else float("nan")
                self._set_result_item(row, 0, period_val, 4)
                self._set_result_item(row, 1, vec_val, 4)
                self._set_result_item(row, 2, cd_val, 2)
                self._set_result_item(row, 3, kb_val, 2)
        else:
            # vector モード: cd / kb のみ
            for row in range(n_layers):
                cd_val = cds[row] if row < len(cds) else float("nan")
                kb_val = kbs[row] if row < len(kbs) else float("nan")
                self._set_result_item(row, 0, cd_val, 2)
                self._set_result_item(row, 1, kb_val, 2)

    def _set_result_item(self, row: int, col: int, value: float, decimals: int) -> None:
        text = self._fmt(value, decimals)
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._result_table.setItem(row, col, item)

    def _clear_results(self) -> None:
        self._lbl_mu.setText("—")
        self._lbl_gamma.setText("—")
        self._lbl_h.setText("—")
        self._result_table.setRowCount(0)

    @staticmethod
    def _fmt(value: float, decimals: int) -> str:
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return "—"
        return f"{value:.{decimals}f}"

    # ---- 出力 ---------------------------------------------------------
    def _build_export_rows(self, separator: str = "\t") -> str:
        """結果テーブルを TSV/CSV 文字列に整形します。"""
        headers = ["階"]
        for col in range(self._result_table.columnCount()):
            hdr = self._result_table.horizontalHeaderItem(col)
            headers.append(hdr.text() if hdr else f"col{col + 1}")
        lines = [separator.join(headers)]
        for row in range(self._result_table.rowCount()):
            cells = [str(row + 1)]
            for col in range(self._result_table.columnCount()):
                item = self._result_table.item(row, col)
                cells.append(item.text() if item else "")
            lines.append(separator.join(cells))
        summary = [
            f"μ{separator}{self._lbl_mu.text()}",
            f"γ{separator}{self._lbl_gamma.text()}",
            f"h{separator}{self._lbl_h.text()}",
        ]
        return "\n".join(lines + [""] + summary)

    def _on_copy_clipboard(self) -> None:
        if self._result_table.rowCount() == 0:
            return
        QApplication.clipboard().setText(self._build_export_rows(separator="\t"))

    def _on_export_csv(self) -> None:
        if self._result_table.rowCount() == 0:
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, "iRDT MDOF 結果をCSVに保存", "irdt_mdof.csv", "CSV (*.csv)"
        )
        if not path_str:
            return
        try:
            content = self._build_export_rows(separator=",")
            # UTF-8 BOM 付きで Excel 互換
            Path(path_str).write_text(content, encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.warning(self, "CSV保存失敗", f"書き込みに失敗しました:\n{exc}")
