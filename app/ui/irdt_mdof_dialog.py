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

    Parameters
    ----------
    parent : QWidget, optional
    project : Project, optional
        プロジェクトを渡すと「s8iから自動入力」と「s8iへダンパー挿入」が有効化される。
    log_callback : callable, optional
        SNAP 実行や挿入のログを受け取るコールバック。
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        project=None,  # type: ignore[no-untyped-def]
        log_callback=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("iRDT最適解 - 多質点系")
        self.resize(980, 720)

        self._input_mode = _MODE_STIFFNESS
        self._t0 = _DEFAULT_T0
        self._project = project
        self._log_callback = log_callback

        # 自動入力時に取得した層情報/モード情報を保持
        self._auto_fill_result = None  # type: ignore[assignment]
        self._selected_mode_no: int = 1
        # 層名 (自動入力で埋まる。手動モードでは空のまま)
        self._floor_names: List[str] = []

        self._build_ui()
        self._connect_signals()
        self._populate_defaults()
        self._recompute()

    # ---- UI 構築 ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addLayout(self._build_top_bar())
        root.addWidget(self._build_header_group())
        root.addWidget(self._build_input_table())
        root.addWidget(self._build_summary_group())
        root.addWidget(self._build_result_table())
        root.addLayout(self._build_bottom_buttons())

    def _build_top_bar(self) -> QHBoxLayout:
        """s8i 自動入力 / ダンパー挿入ボタンの行。"""
        top_bar = QHBoxLayout()
        self._btn_import = QPushButton("s8i から自動入力...")
        self._btn_import.setToolTip(
            "プロジェクトの s8i モデルと固有値解析結果から、各層の質量と"
            "固有ベクトルを自動入力します。\n"
            "解析結果が無い場合は SNAP を実行して取得します。"
        )
        self._btn_import.clicked.connect(self._on_auto_import)
        self._btn_import.setEnabled(self._project is not None)
        top_bar.addWidget(self._btn_import)

        self._lbl_import_status = QLabel("")
        self._lbl_import_status.setStyleSheet("color: #555;")
        top_bar.addWidget(self._lbl_import_status, stretch=1)

        self._btn_inject = QPushButton("s8i へダンパーを挿入...")
        self._btn_inject.setToolTip(
            "計算した最適値で iRDT ダンパーを s8i に追加します。"
        )
        self._btn_inject.clicked.connect(self._on_inject_damper)
        self._btn_inject.setEnabled(self._project is not None)
        top_bar.addWidget(self._btn_inject)
        return top_bar

    def _build_header_group(self) -> QGroupBox:
        """入力モード / 層数 / 固有周期 / 対象モード / 質量比配分 のフォーム。"""
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

        self._target_mode_combo = QComboBox()
        self._target_mode_combo.setEnabled(False)
        hform.addRow("制御対象モード", self._target_mode_combo)

        hform.addRow("目標質量比 μ [-]", self._build_mass_distribute_row())
        return header

    def _build_mass_distribute_row(self) -> QHBoxLayout:
        """目標 μ に基づく md 一括配分ボタンの行。"""
        mass_row = QHBoxLayout()
        self._mu_target_edit = QLineEdit("0.05")
        self._mu_target_edit.setValidator(QDoubleValidator(0.0, 1.0, 6))
        self._mu_target_edit.setAlignment(Qt.AlignRight)
        self._mu_target_edit.setMaximumWidth(100)
        mass_row.addWidget(self._mu_target_edit)
        self._btn_distribute_md = QPushButton("この質量比で各層に一様配分")
        self._btn_distribute_md.setToolTip(
            "指定した質量比 μ = Σmd / Σm に基づき、各層のダンパー質量 md を"
            "層質量に比例して配分します。"
        )
        self._btn_distribute_md.clicked.connect(self._on_distribute_md_from_mu)
        mass_row.addWidget(self._btn_distribute_md)
        mass_row.addStretch(1)
        return mass_row

    def _build_input_table(self) -> QTableWidget:
        """層別入力テーブル (質量/剛性 or ベクトル/md)。"""
        self._input_table = QTableWidget(0, 3)
        self._input_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._input_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._input_table.verticalHeader().setDefaultSectionSize(24)
        return self._input_table

    def _build_summary_group(self) -> QGroupBox:
        """最適値サマリ (μ / γ / h)。"""
        summary = QGroupBox("最適値")
        sform = QFormLayout(summary)
        self._lbl_mu = QLabel("-")
        self._lbl_gamma = QLabel("-")
        self._lbl_h = QLabel("-")
        sform.addRow("有効質量比 μ [-]", self._lbl_mu)
        sform.addRow("振動数比 γ [-]", self._lbl_gamma)
        sform.addRow("減衰定数 h [-]", self._lbl_h)
        return summary

    def _build_result_table(self) -> QTableWidget:
        """層別 cd/kb 等の結果テーブル。列構成は入力モードで変化。"""
        self._result_table = QTableWidget(0, 4)
        self._apply_result_columns()
        self._result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._result_table.verticalHeader().setDefaultSectionSize(24)
        return self._result_table

    def _build_bottom_buttons(self) -> QHBoxLayout:
        """リセット / コピー / CSV / 閉じる。"""
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
        return btn_row

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

    def _set_item_text(self, row: int, col: int, text: str) -> None:
        """入力テーブルセルを常に上書き (右寄せ)。"""
        item = self._input_table.item(row, col)
        if item is None:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._input_table.setItem(row, col, item)
        else:
            item.setText(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

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

    # ---- s8i 自動入力 -------------------------------------------------
    def _on_auto_import(self) -> None:
        """プロジェクトの s8i と固有値解析結果から入力を自動生成します。"""
        if self._project is None:
            QMessageBox.warning(self, "プロジェクト未設定",
                                "プロジェクトが読み込まれていません。")
            return

        from app.services.irdt_auto_fill import auto_fill_from_project

        # まず解析結果ありで試行、なければ実行を確認
        try:
            result = auto_fill_from_project(self._project, run_if_missing=False)
        except FileNotFoundError:
            ans = QMessageBox.question(
                self,
                "固有値解析が必要です",
                "プロジェクトに固有値解析結果 (Period.xbn) が見つかりません。\n"
                "SNAP を実行して固有値解析を取得しますか？\n"
                "(最初の解析ケースが使用されます)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if ans != QMessageBox.Yes:
                return
            try:
                result = auto_fill_from_project(
                    self._project, run_if_missing=True,
                    log_callback=self._log_callback,
                )
            except Exception as exc:
                QMessageBox.critical(self, "自動入力失敗",
                                     f"SNAP 実行中にエラー:\n{exc}")
                return
        except Exception as exc:
            QMessageBox.critical(self, "自動入力失敗", f"例外:\n{exc}")
            return

        self._apply_auto_fill(result)

    def _apply_auto_fill(self, result) -> None:  # type: ignore[no-untyped-def]
        """`AutoFillResult` をダイアログ入力欄に反映します。"""
        self._auto_fill_result = result
        floors = result.floors
        modes = result.modes
        if not floors:
            QMessageBox.warning(self, "自動入力失敗",
                                "s8i から層情報を取得できませんでした。")
            return

        # モード選択コンボを更新
        self._target_mode_combo.blockSignals(True)
        self._target_mode_combo.clear()
        for m in modes:
            label = (f"Mode {m.mode_no}: T={m.period:.4f}s "
                     f"(ω={m.omega:.3f}, dir={m.dominant_direction})")
            self._target_mode_combo.addItem(label, m.mode_no)
        self._target_mode_combo.setEnabled(bool(modes))
        self._target_mode_combo.blockSignals(False)
        try:
            self._target_mode_combo.currentIndexChanged.disconnect(
                self._on_target_mode_changed
            )
        except (TypeError, RuntimeError):
            pass
        self._target_mode_combo.currentIndexChanged.connect(
            self._on_target_mode_changed
        )

        # 層名を保存 (vertical header に利用)
        self._floor_names = [f.name for f in floors]

        # vector モードに切替えて周期 + 固有ベクトルを表示
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(1)  # vector
        self._input_mode = _MODE_VECTOR
        self._update_table_headers()
        self._apply_result_columns()
        self._t0_label.setVisible(True)
        self._t0_edit.setVisible(True)
        self._mode_combo.blockSignals(False)

        # 層数調整 (1次モードを既定)
        n = len(floors)
        self._n_spin.blockSignals(True)
        self._n_spin.setValue(n)
        self._n_spin.blockSignals(False)
        self._resize_input_rows(n)

        # 1 次モードで自動入力
        self._selected_mode_no = modes[0].mode_no if modes else 1
        self._fill_input_from_modes(result, self._selected_mode_no)

        status_parts = [f"層数={len(floors)}"]
        if modes:
            status_parts.append(f"モード数={len(modes)}")
        if result.source_case_name:
            status_parts.append(f"from '{result.source_case_name}'")
        self._lbl_import_status.setText("自動入力: " + ", ".join(status_parts))

        if result.warnings:
            QMessageBox.information(
                self, "自動入力の注意",
                "\n".join(result.warnings),
            )
        self._recompute()

    def _on_target_mode_changed(self, _idx: int) -> None:
        mode_no = self._target_mode_combo.currentData()
        if mode_no is None or self._auto_fill_result is None:
            return
        self._selected_mode_no = int(mode_no)
        self._fill_input_from_modes(self._auto_fill_result, self._selected_mode_no)
        self._recompute()

    def _fill_input_from_modes(self, result, mode_no: int) -> None:  # type: ignore[no-untyped-def]
        """指定モードの周期と固有ベクトルを入力テーブル/周期欄に反映します。

        s8i から取得した層質量・モード形状・推奨 md を、既存値を上書きして
        テーブルに反映する。md は常に `μ_target × mass` で再計算される。
        """
        mode = result.get_mode(mode_no)
        floors = result.floors
        n = len(floors)
        try:
            mu_tgt = float(self._mu_target_edit.text())
        except ValueError:
            mu_tgt = 0.05
        self._input_table.blockSignals(True)
        try:
            if mode is not None:
                self._t0_edit.setText(f"{mode.period:.6g}")
            for row in range(n):
                mass_val = floors[row].mass
                # mass 列は必ず上書き
                self._set_item_text(row, 0, f"{mass_val:g}")
                # 固有ベクトル: モードから取得。無ければ 1 で埋める。
                if mode is not None and row < len(mode.shape):
                    vec = mode.shape[row]
                else:
                    vec = 1.0
                # 第2列: vector モードなら固有ベクトル(上書き), stiffness モードなら
                # 剛性 (既存値を残す)
                if self._input_mode == _MODE_VECTOR:
                    self._set_item_text(row, 1, f"{vec:g}")
                else:
                    self._ensure_item(row, 1, _DEFAULT_K[0])
                # md 列: μ_target × mass で上書き
                md_default = max(0.0, mass_val * mu_tgt)
                self._set_item_text(row, 2, f"{md_default:g}")
            # 行ラベルを floor 名で更新
            self._input_table.setVerticalHeaderLabels(self._floor_names or [
                str(i + 1) for i in range(n)
            ])
        finally:
            self._input_table.blockSignals(False)

    def _on_distribute_md_from_mu(self) -> None:
        """目標質量比 μ に基づき各層の md を一様配分 (層質量比例) します。"""
        try:
            mu_target = float(self._mu_target_edit.text())
        except ValueError:
            QMessageBox.warning(self, "入力エラー", "目標質量比 μ は数値を入力してください。")
            return
        if mu_target < 0:
            QMessageBox.warning(self, "入力エラー", "μ は 0 以上にしてください。")
            return

        ms = self._read_column(0)
        n = self._input_table.rowCount()
        self._input_table.blockSignals(True)
        try:
            for row in range(n):
                m_val = ms[row] if row < len(ms) else 0.0
                md_val = max(0.0, m_val * mu_target)
                item = self._input_table.item(row, 2)
                if item is None:
                    item = QTableWidgetItem()
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self._input_table.setItem(row, 2, item)
                item.setText(f"{md_val:g}")
        finally:
            self._input_table.blockSignals(False)
        self._recompute()

    # ---- ダンパー挿入 -------------------------------------------------
    def _on_inject_damper(self) -> None:
        """計算結果を元に、ダンパー定義+配置を s8i に追加する提案ダイアログを開く。"""
        try:
            self._do_inject_damper()
        except Exception as exc:
            import logging, traceback
            logging.getLogger(__name__).exception("inject dialog failed")
            QMessageBox.critical(
                self, "ダンパー挿入エラー",
                f"ダイアログ表示中に例外が発生しました:\n{exc}\n\n"
                f"{traceback.format_exc(limit=4)}",
            )

    def _do_inject_damper(self) -> None:
        if self._project is None:
            QMessageBox.warning(self, "プロジェクト未設定",
                                "このダイアログはプロジェクト読み込み中のみ使用できます。")
            return
        s8i_path = getattr(self._project, "s8i_path", "") or ""
        if not s8i_path:
            QMessageBox.warning(
                self, "s8i ファイル未設定",
                "プロジェクトに s8i ファイルが読み込まれていません。\n"
                "メインウィンドウで .s8i モデルを読み込んでから再度お試しください。",
            )
            return

        # 自動入力が済んでいない場合は層情報を s8i から取り直す
        from app.services.irdt_auto_fill import (
            extract_floor_info,
            build_placement_specs,
        )
        from app.models.s8i_parser import parse_s8i

        model = getattr(self._project, "s8i_model", None)
        if model is None:
            try:
                model = parse_s8i(s8i_path)
            except Exception as exc:
                QMessageBox.critical(self, "s8i 読込失敗", str(exc))
                return
        # 最下層は「入力」からは除外するが「配置」では基礎節点を使うため
        # 別枠で保持しておく。
        if self._auto_fill_result is not None:
            floors = self._auto_fill_result.floors
            base_floor = self._auto_fill_result.base_floor
        else:
            # 自動入力を通っていない場合は s8i から取り直す
            floors_all = extract_floor_info(model, skip_base=False)
            if len(floors_all) >= 2:
                floors = floors_all[1:]
                base_floor = floors_all[0]
            else:
                floors = floors_all
                base_floor = None
        if not floors:
            QMessageBox.warning(self, "層情報なし",
                                "s8i から層情報を取得できませんでした。")
            return

        # 現在の入力から md/cd/kb を読む (計算結果テーブルの cd/kb 列)
        mds = self._read_column(2)  # 入力テーブルの md
        # 結果テーブルから cd, kb を取得
        cds: List[float] = []
        kbs: List[float] = []
        # vector モードでは結果テーブル列が [cd, kb], stiffness では [period, vec, cd, kb]
        cd_col = 0 if self._input_mode == _MODE_VECTOR else 2
        kb_col = 1 if self._input_mode == _MODE_VECTOR else 3
        for row in range(self._result_table.rowCount()):
            def _v(col: int) -> float:
                item = self._result_table.item(row, col)
                if item is None:
                    return float("nan")
                try:
                    return float(item.text())
                except ValueError:
                    return float("nan")
            cds.append(_v(cd_col))
            kbs.append(_v(kb_col))

        if not any(cd > 0 for cd in cds if not math.isnan(cd)):
            QMessageBox.warning(
                self, "最適値未計算",
                "有効な cd/kb が計算されていません。\n"
                "「s8i から自動入力...」で層質量/固有ベクトルを取り込むか、\n"
                "入力テーブルの m, md, k (またはベクトル) を確認してください。",
            )
            return

        specs = build_placement_specs(floors, mds, cds, kbs, base_floor=base_floor)
        if not specs:
            QMessageBox.warning(
                self, "配置候補なし",
                "挿入可能な層がありません。\n"
                f" 入力層数={len(mds)}, s8i 層数={len(floors)}。\n"
                "md > 0 の層があり、かつ最上層より下に配置候補があるか確認してください。",
            )
            return

        # 代表ケース (先頭)
        base_case = None
        cases = getattr(self._project, "cases", []) or []
        if cases:
            base_case = cases[0]

        from app.ui.irdt_placement_proposal_dialog import IrdtPlacementProposalDialog
        dlg = IrdtPlacementProposalDialog(
            base_s8i_path=self._project.s8i_path,
            specs=specs,
            project=self._project,
            base_case=base_case,
            parent=self,
        )
        dlg.exec()
