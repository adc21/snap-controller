"""
app/ui/transfer_function_widget.py
===================================

周波数応答（伝達関数）表示ウィジェット。

SNAP 解析の .hst 時刻歴データから FFT を算出し、
周波数 vs |H(f)| の振幅スペクトルをプロットする。

主な機能
--------
- Floor.hst 等の時刻歴から FFT 振幅スペクトルを計算
- 周波数 vs |H(f)| プロット（線形 / 対数スケール切替）
- 複数ケース重ね描き対応
- レコード（層）・成分セレクタ
- ピーク周波数・ピーク振幅のテキスト表示

使い方
------
::

    widget = TransferFunctionWidget(parent=main_widget)
    widget.set_entries([
        ("case_A", snap_result_loader_a),
        ("case_B", snap_result_loader_b),
    ])
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy.fft import rfft, rfftfreq
except ImportError:
    from numpy.fft import rfft, rfftfreq

import os, matplotlib
if not os.environ.get("MPLBACKEND"):
    matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

import logging

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "sans-serif"]
except Exception:
    logging.getLogger(__name__).debug("日本語フォント設定失敗")

logger = logging.getLogger(__name__)

import csv
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
)

from controller.binary.result_loader import SnapResultLoader
from app.services.transfer_function_service import (
    compute_snap_transfer_function,
    TransferFunctionResult,
)


# ---------------------------------------------------------------------------
# matplotlib キャンバス（軽量ラッパー）
# ---------------------------------------------------------------------------

class _MplCanvas(FigureCanvas):
    """Matplotlib Figure を PySide6 に埋め込む最小ラッパー。"""

    def __init__(self, parent=None, width: float = 8.0, height: float = 4.5,
                 dpi: int = 100) -> None:
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self.setParent(parent)

    def show_message(self, msg: str, color: str = "gray") -> None:
        """空状態メッセージを中央に表示する。"""
        self.ax.clear()
        self.ax.text(
            0.5, 0.5, msg,
            ha="center", va="center",
            transform=self.ax.transAxes,
            color=color, fontsize=10,
            wrap=True,
        )
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw()


# ---------------------------------------------------------------------------
# メインウィジェット
# ---------------------------------------------------------------------------

class TransferFunctionWidget(QWidget):
    """周波数応答（伝達関数）ウィジェット。

    BinaryResultWidget の prepend_tab() または
    MainWindow に直接 addTab() して利用する。

    使い方::

        w = TransferFunctionWidget()
        w.set_entries([("ケース名", snap_result_loader)])
    """

    # 対象カテゴリ（HSTを持つもの）
    _TARGET_CATEGORIES = ["Floor", "Story", "Damper", "Spring", "Node"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._entries: List[Tuple[str, SnapResultLoader]] = []
        self._reference_data: Optional[dict] = None  # {name, freqs, amplitude}
        self._setup_ui()

    # ------------------------------------------------------------------
    # パブリック API
    # ------------------------------------------------------------------

    def set_entries(self, entries: List[Tuple[str, SnapResultLoader]]) -> None:
        """ケース名とローダーのリストを設定してリフレッシュする。

        Parameters
        ----------
        entries : list of (name, SnapResultLoader)
        """
        self._entries = list(entries) if entries else []
        self._populate_combos()
        self._refresh()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        root.addLayout(self._build_control_bar())
        self._build_info_labels(root)
        self._build_chart_area(root)

    def _build_control_bar(self) -> QHBoxLayout:
        """上部コントロールバー（セレクタ + ボタン群）を構築。"""
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("📊 周波数応答ビューア"))
        ctrl.addStretch(1)
        self._build_selector_combos(ctrl)
        self._build_control_buttons(ctrl)
        return ctrl

    def _build_selector_combos(self, ctrl: QHBoxLayout) -> None:
        """カテゴリ/レコード/成分コンボ + 対数スケールチェックを構築。"""
        ctrl.addWidget(QLabel("カテゴリ:"))
        self._cat_combo = QComboBox()
        self._cat_combo.setMinimumWidth(90)
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        ctrl.addWidget(self._cat_combo)

        ctrl.addSpacing(8)
        ctrl.addWidget(QLabel("レコード:"))
        self._rec_combo = QComboBox()
        self._rec_combo.setMinimumWidth(120)
        self._rec_combo.currentIndexChanged.connect(self._on_selection_changed)
        ctrl.addWidget(self._rec_combo)

        ctrl.addSpacing(8)
        ctrl.addWidget(QLabel("成分:"))
        self._field_combo = QComboBox()
        self._field_combo.setMinimumWidth(80)
        self._field_combo.currentIndexChanged.connect(self._on_selection_changed)
        ctrl.addWidget(self._field_combo)

        ctrl.addSpacing(12)
        self._log_check = QCheckBox("対数スケール")
        self._log_check.setChecked(False)
        self._log_check.stateChanged.connect(self._on_selection_changed)
        ctrl.addWidget(self._log_check)

    def _build_control_buttons(self, ctrl: QHBoxLayout) -> None:
        """更新/SNAP伝達関数/基準/CSV出力ボタンを構築。"""
        btn_refresh = QPushButton("更新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self._refresh)
        ctrl.addWidget(btn_refresh)

        self._btn_snap_tf = QPushButton("SNAP伝達関数")
        self._btn_snap_tf.setFixedWidth(110)
        self._btn_snap_tf.setToolTip(
            "選択した入力レコード(1F)と出力レコードから\n"
            "Welch法ベースの伝達関数 H(f)=Y/X を計算"
        )
        self._btn_snap_tf.clicked.connect(self._compute_snap_transfer_function)
        ctrl.addWidget(self._btn_snap_tf)

        self._btn_set_ref = QPushButton("基準に設定")
        self._btn_set_ref.setFixedWidth(80)
        self._btn_set_ref.setToolTip(
            "現在表示中のFFTスペクトルを基準(リファレンス)として保存します。\n"
            "制振前の応答を基準にして、制振後との比較に使えます。"
        )
        self._btn_set_ref.clicked.connect(self._set_reference)
        ctrl.addWidget(self._btn_set_ref)

        self._btn_clear_ref = QPushButton("基準クリア")
        self._btn_clear_ref.setFixedWidth(80)
        self._btn_clear_ref.setToolTip("保存した基準データをクリアします")
        self._btn_clear_ref.clicked.connect(self._clear_reference)
        self._btn_clear_ref.setEnabled(False)
        ctrl.addWidget(self._btn_clear_ref)

        self._btn_export_csv = QPushButton("CSV出力")
        self._btn_export_csv.setFixedWidth(70)
        self._btn_export_csv.setToolTip(
            "表示中の周波数・振幅データをCSVファイルに出力します"
        )
        self._btn_export_csv.clicked.connect(self._export_csv)
        ctrl.addWidget(self._btn_export_csv)

    def _build_info_labels(self, root: QVBoxLayout) -> None:
        """ピーク情報 + ステータスラベルを構築。"""
        self._peak_label = QLabel("")
        self._peak_label.setStyleSheet(
            "color:#333; font-size:11px; padding:2px 4px; "
            "background:#f0f4ff; border-radius:3px;"
        )
        root.addWidget(self._peak_label)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#666; font-size:11px; padding:2px 4px;")
        root.addWidget(self._status_label)

    def _build_chart_area(self, root: QVBoxLayout) -> None:
        """matplotlibチャートエリア(キャンバス+ナビゲーションツールバー)を構築。"""
        chart_widget = QWidget()
        chart_layout = QVBoxLayout(chart_widget)
        chart_layout.setContentsMargins(2, 2, 2, 2)
        self._canvas = _MplCanvas(height=4.5)
        chart_layout.addWidget(NavigationToolbar(self._canvas, chart_widget))
        chart_layout.addWidget(self._canvas, stretch=1)
        root.addWidget(chart_widget, stretch=1)

    # ------------------------------------------------------------------
    # コンボボックス更新
    # ------------------------------------------------------------------

    def _populate_combos(self) -> None:
        """エントリからカテゴリ・レコード・フィールドコンボを構築する。"""
        self._cat_combo.blockSignals(True)
        prev_cat = self._cat_combo.currentText()
        self._cat_combo.clear()

        # 全エントリで利用可能なカテゴリを収集
        available: set = set()
        for _, loader in self._entries:
            for cat in self._TARGET_CATEGORIES:
                bc = loader.get(cat)
                if bc and bc.hst and bc.hst.header:
                    available.add(cat)

        for cat in self._TARGET_CATEGORIES:
            if cat in available:
                self._cat_combo.addItem(cat)

        # 以前の選択を復元
        idx = self._cat_combo.findText(prev_cat)
        if idx >= 0:
            self._cat_combo.setCurrentIndex(idx)
        self._cat_combo.blockSignals(False)

        self._update_record_field_combos()

    def _update_record_field_combos(self) -> None:
        """現在のカテゴリに基づきレコード・フィールドコンボを更新する。"""
        cat = self._cat_combo.currentText()

        self._rec_combo.blockSignals(True)
        self._field_combo.blockSignals(True)
        prev_rec = self._rec_combo.currentIndex()
        prev_field = self._field_combo.currentIndex()
        self._rec_combo.clear()
        self._field_combo.clear()

        if not cat or not self._entries:
            self._rec_combo.blockSignals(False)
            self._field_combo.blockSignals(False)
            return

        # 最初に見つかるローダーからレコード数・フィールド名を取得
        for _, loader in self._entries:
            bc = loader.get(cat)
            if bc and bc.hst and bc.hst.header:
                hst = bc.hst
                h = hst.header
                # レコードコンボ
                for ri in range(h.num_records):
                    name = bc.record_name(ri)
                    self._rec_combo.addItem(name, ri)
                # フィールドコンボ
                labels = hst.field_labels()
                for fi, lbl in enumerate(labels):
                    self._field_combo.addItem(lbl, fi)
                break

        # 以前の選択を復元
        if 0 <= prev_rec < self._rec_combo.count():
            self._rec_combo.setCurrentIndex(prev_rec)
        if 0 <= prev_field < self._field_combo.count():
            self._field_combo.setCurrentIndex(prev_field)

        self._rec_combo.blockSignals(False)
        self._field_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------

    def _on_category_changed(self, *_) -> None:
        self._update_record_field_combos()
        self._refresh()

    def _on_selection_changed(self, *_) -> None:
        self._refresh()

    # ------------------------------------------------------------------
    # 基準（リファレンス）データ管理
    # ------------------------------------------------------------------

    def _set_reference(self) -> None:
        """現在の先頭ケースのFFTデータを基準として保存する。"""
        if not self._entries:
            self._status_label.setText("基準に設定するケースがありません")
            return

        cat = self._cat_combo.currentText()
        rec_idx = self._rec_combo.currentData()
        field_idx = self._field_combo.currentData()

        if not cat or rec_idx is None or field_idx is None:
            return

        # 最初のケースのデータを基準として保存
        case_name, loader = self._entries[0]
        bc = loader.get(cat)
        if not bc or not bc.hst or not bc.hst.header:
            self._status_label.setText("基準データを取得できません")
            return

        hst = bc.hst
        h = hst.header
        if rec_idx >= h.num_records or field_idx >= h.fields_per_record:
            return

        try:
            hst.ensure_loaded()
            y = hst.time_series(rec_idx, field_idx)
        except Exception:
            self._status_label.setText("時刻歴データの読込に失敗しました")
            return

        if len(y) < 2:
            return

        n = len(y)
        dt = hst.dt
        Y = rfft(y)
        freqs = rfftfreq(n, d=dt)
        amplitude = np.abs(Y) * (2.0 / n)
        freqs = freqs[1:]
        amplitude = amplitude[1:]

        rec_name = self._rec_combo.currentText()
        field_name = self._field_combo.currentText()
        self._reference_data = {
            "name": f"基準: {case_name}",
            "freqs": freqs,
            "amplitude": amplitude,
            "label": f"{cat}/{rec_name}/{field_name}",
        }
        self._btn_clear_ref.setEnabled(True)
        self._status_label.setText(
            f"基準データを保存しました: {case_name} ({cat}/{rec_name}/{field_name})"
        )
        self._refresh()

    def _clear_reference(self) -> None:
        """基準データをクリアする。"""
        self._reference_data = None
        self._btn_clear_ref.setEnabled(False)
        self._status_label.setText("基準データをクリアしました")
        self._refresh()

    # ------------------------------------------------------------------
    # CSV エクスポート
    # ------------------------------------------------------------------

    def _collect_fft_data(self) -> List[Tuple[str, np.ndarray, np.ndarray]]:
        """現在の選択に基づくFFTデータを収集する。

        Returns
        -------
        list of (case_name, freqs, amplitude)
        """
        results: List[Tuple[str, np.ndarray, np.ndarray]] = []
        cat = self._cat_combo.currentText()
        rec_idx = self._rec_combo.currentData()
        field_idx = self._field_combo.currentData()

        if not cat or rec_idx is None or field_idx is None or not self._entries:
            return results

        for case_name, loader in self._entries:
            bc = loader.get(cat)
            if not bc or not bc.hst or not bc.hst.header:
                continue
            hst = bc.hst
            h = hst.header
            if rec_idx >= h.num_records or field_idx >= h.fields_per_record:
                continue
            try:
                hst.ensure_loaded()
                y = hst.time_series(rec_idx, field_idx)
            except Exception:
                continue
            if len(y) < 2:
                continue

            n = len(y)
            dt = hst.dt
            Y = rfft(y)
            freqs = rfftfreq(n, d=dt)
            amplitude = np.abs(Y) * (2.0 / n)
            freqs = freqs[1:]
            amplitude = amplitude[1:]
            if len(freqs) > 0:
                results.append((case_name, freqs, amplitude))

        return results

    def _export_csv(self) -> None:
        """表示中の周波数・振幅データをCSVファイルにエクスポートします。"""
        data = self._collect_fft_data()
        if not data:
            QMessageBox.information(self, "情報", "エクスポートするデータがありません。")
            return

        cat = self._cat_combo.currentText()
        rec_name = self._rec_combo.currentText()
        field_name = self._field_combo.currentText()
        default_name = f"fft_{cat}_{rec_name}_{field_name}.csv"
        # ファイル名に使えない文字を除去
        default_name = default_name.replace("/", "_").replace("\\", "_")

        path, _ = QFileDialog.getSaveFileName(
            self, "CSV出力先を選択", default_name,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                # ヘッダー行
                header = ["周波数 [Hz]"]
                for case_name, _, _ in data:
                    header.append(f"|H(f)| ({case_name})")
                writer.writerow(header)

                # 最長のデータに合わせる
                max_len = max(len(freqs) for _, freqs, _ in data)
                for i in range(max_len):
                    row = []
                    # 周波数は最初のケースから取得
                    if i < len(data[0][1]):
                        row.append(f"{data[0][1][i]:.6g}")
                    else:
                        row.append("")
                    for _, freqs, amplitude in data:
                        if i < len(amplitude):
                            row.append(f"{amplitude[i]:.6g}")
                        else:
                            row.append("")
                    writer.writerow(row)

            n_cases = len(data)
            n_points = max_len
            QMessageBox.information(
                self, "CSV出力完了",
                f"周波数応答データを出力しました。\n{path}\n"
                f"({n_cases} ケース, {n_points} データ点)",
            )
        except OSError as e:
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗しました:\n{e}")

    # ------------------------------------------------------------------
    # SNAP 伝達関数計算
    # ------------------------------------------------------------------

    def _compute_snap_transfer_function(self) -> None:
        """入力レコード(rec=0=1F)と選択出力レコードから伝達関数を計算して重ね描きする。"""
        if not self._entries:
            self._status_label.setText("ケースがありません")
            return

        cat = self._cat_combo.currentText()
        output_rec = self._rec_combo.currentData()
        field_idx = self._field_combo.currentData()

        if not cat or output_rec is None or field_idx is None:
            self._status_label.setText("カテゴリ/レコード/成分を選択してください")
            return

        ax = self._canvas.ax
        ax.clear()

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        use_log = self._log_check.isChecked()
        peak_texts: List[str] = []
        plotted = 0

        for ci, (case_name, loader) in enumerate(self._entries):
            tf = compute_snap_transfer_function(
                result_loader=loader,
                input_category=cat,
                input_record=0,
                input_field=field_idx,
                output_category=cat,
                output_record=output_rec,
                output_field=field_idx,
                freq_range=(0.1, 50.0),
            )
            if tf is None:
                continue

            c = colors[ci % len(colors)]
            # Convert dB to linear amplitude for consistency with FFT view
            amplitude = 10.0 ** (tf.gain_db / 20.0)
            ax.plot(tf.frequencies, amplitude, color=c, linewidth=0.9,
                    label=f"{case_name} H(f)", alpha=0.85)

            peak_idx = int(np.argmax(amplitude))
            peak_freq = float(tf.frequencies[peak_idx])
            peak_amp = float(amplitude[peak_idx])
            ax.plot(peak_freq, peak_amp, "v", color=c, markersize=7)

            peak_texts.append(
                f"{case_name}: f={peak_freq:.3f} Hz, |H|={peak_amp:.4g}, "
                f"{tf.peak_gain_db:.1f} dB"
            )
            plotted += 1

        if plotted == 0:
            self._canvas.show_message(
                "伝達関数を計算できませんでした\n"
                "入力レコード(1F)と出力レコードのデータを確認してください"
            )
            self._peak_label.setText("")
            self._status_label.setText("伝達関数計算失敗")
            return

        # 基準データのオーバーレイ（SNAP伝達関数モードでも表示）
        if self._reference_data is not None:
            ref = self._reference_data
            ax.plot(
                ref["freqs"], ref["amplitude"],
                color="gray", linewidth=1.2, linestyle="--",
                label=ref["name"], alpha=0.6,
            )

        if use_log:
            ax.set_yscale("log")

        rec_name = self._rec_combo.currentText()
        field_name = self._field_combo.currentText()
        ax.set_xlabel("周波数 [Hz]")
        ax.set_ylabel("|H(f)|")
        ax.set_title(f"伝達関数 — {cat}/{field_name}: 1F → {rec_name}")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.set_xlim(left=0)
        ax.legend(fontsize=8)
        self._canvas.fig.tight_layout()
        self._canvas.draw()

        self._peak_label.setText("  |  ".join(peak_texts))
        self._status_label.setText(
            f"伝達関数 {plotted} ケース  "
            f"[{cat} / 入力=rec0 / 出力=rec{output_rec} / field={field_idx}]"
        )

    # ------------------------------------------------------------------
    # メイン描画
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """FFT を計算してプロットする。"""
        ax = self._canvas.ax
        ax.clear()

        selection = self._prepare_refresh_selection()
        if selection is None:
            return
        cat, rec_idx, field_idx = selection

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        peak_texts: List[str] = []
        plotted = 0

        for ci, (case_name, loader) in enumerate(self._entries):
            fft_data = self._compute_case_fft(loader, cat, rec_idx, field_idx)
            if fft_data is None:
                continue
            freqs, amplitude, peak_freq, peak_amp = fft_data
            self._plot_case_fft(ax, freqs, amplitude, peak_freq, peak_amp,
                                case_name, colors[ci % len(colors)])
            peak_texts.append(
                f"{case_name}: f={peak_freq:.3f} Hz (T={1/peak_freq:.3f} s), "
                f"|H|={peak_amp:.4g}"
            )
            plotted += 1

        if plotted == 0:
            self._canvas.show_message(
                f"{cat}.hst のデータが見つかりません\n"
                "時刻歴解析を実行してください"
            )
            self._peak_label.setText("")
            self._status_label.setText(f"{cat}.hst データなし")
            return

        if self._plot_reference_overlay(ax):
            plotted += 1

        self._finalize_fft_axes(ax, cat)
        self._peak_label.setText("  |  ".join(peak_texts))
        self._status_label.setText(
            f"{plotted} ケースをプロット  "
            f"[{cat} / rec={rec_idx} / field={field_idx}]"
        )

    def _prepare_refresh_selection(self):
        """選択状態を検証し (cat, rec_idx, field_idx) を返す。不正なら None。"""
        if not self._entries:
            self._canvas.show_message("ケースが選択されていません")
            self._peak_label.setText("")
            self._status_label.setText("")
            return None
        cat = self._cat_combo.currentText()
        rec_idx = self._rec_combo.currentData()
        field_idx = self._field_combo.currentData()
        if not cat or rec_idx is None or field_idx is None:
            self._canvas.show_message("カテゴリ / レコード / 成分を選択してください")
            self._peak_label.setText("")
            self._status_label.setText("")
            return None
        return cat, rec_idx, field_idx

    @staticmethod
    def _compute_case_fft(loader, cat: str, rec_idx: int, field_idx: int):
        """1ケースのFFTを計算。 (freqs, amp, peak_freq, peak_amp) を返す。不可なら None。"""
        bc = loader.get(cat)
        if not bc or not bc.hst or not bc.hst.header:
            return None
        hst = bc.hst
        h = hst.header
        if rec_idx >= h.num_records or field_idx >= h.fields_per_record:
            return None
        try:
            hst.ensure_loaded()
            y = hst.time_series(rec_idx, field_idx)
        except Exception:
            return None
        if len(y) < 2:
            return None
        n = len(y)
        Y = rfft(y)
        freqs = rfftfreq(n, d=hst.dt)
        amplitude = np.abs(Y) * (2.0 / n)
        freqs = freqs[1:]
        amplitude = amplitude[1:]
        if len(freqs) == 0:
            return None
        peak_idx = int(np.argmax(amplitude))
        return freqs, amplitude, float(freqs[peak_idx]), float(amplitude[peak_idx])

    @staticmethod
    def _plot_case_fft(ax, freqs, amplitude, peak_freq: float, peak_amp: float,
                      case_name: str, color) -> None:
        ax.plot(freqs, amplitude, color=color, linewidth=0.9,
                label=case_name, alpha=0.85)
        ax.plot(peak_freq, peak_amp, "v", color=color, markersize=7)

    def _plot_reference_overlay(self, ax) -> bool:
        if self._reference_data is None:
            return False
        ref = self._reference_data
        ax.plot(
            ref["freqs"], ref["amplitude"],
            color="gray", linewidth=1.2, linestyle="--",
            label=ref["name"], alpha=0.6,
        )
        return True

    def _finalize_fft_axes(self, ax, cat: str) -> None:
        if self._log_check.isChecked():
            ax.set_yscale("log")
        rec_name = self._rec_combo.currentText()
        field_name = self._field_combo.currentText()
        ax.set_xlabel("周波数 [Hz]")
        ax.set_ylabel("|H(f)|")
        ax.set_title(f"周波数応答 — {cat} / {rec_name} / {field_name}")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.set_xlim(left=0)
        ax.legend(fontsize=8)
        self._canvas.fig.tight_layout()
        self._canvas.draw()
