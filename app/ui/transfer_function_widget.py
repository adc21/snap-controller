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

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QCheckBox,
    QPushButton,
)

from controller.binary.result_loader import SnapResultLoader


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

        # ---- 上部コントロールバー ----
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("📊 周波数応答ビューア"))
        ctrl.addStretch(1)

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

        btn_refresh = QPushButton("更新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self._refresh)
        ctrl.addWidget(btn_refresh)

        root.addLayout(ctrl)

        # ---- ピーク情報ラベル ----
        self._peak_label = QLabel("")
        self._peak_label.setStyleSheet(
            "color:#333; font-size:11px; padding:2px 4px; "
            "background:#f0f4ff; border-radius:3px;"
        )
        root.addWidget(self._peak_label)

        # ---- ステータス ----
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#666; font-size:11px; padding:2px 4px;")
        root.addWidget(self._status_label)

        # ---- チャートエリア ----
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
    # メイン描画
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """FFT を計算してプロットする。"""
        ax = self._canvas.ax
        ax.clear()

        if not self._entries:
            self._canvas.show_message("ケースが選択されていません")
            self._peak_label.setText("")
            self._status_label.setText("")
            return

        cat = self._cat_combo.currentText()
        rec_idx = self._rec_combo.currentData()
        field_idx = self._field_combo.currentData()

        if not cat or rec_idx is None or field_idx is None:
            self._canvas.show_message("カテゴリ / レコード / 成分を選択してください")
            self._peak_label.setText("")
            self._status_label.setText("")
            return

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        use_log = self._log_check.isChecked()
        peak_texts: List[str] = []
        plotted = 0

        for ci, (case_name, loader) in enumerate(self._entries):
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

            # FFT 振幅スペクトル
            Y = rfft(y)
            freqs = rfftfreq(n, d=dt)
            amplitude = np.abs(Y) * (2.0 / n)

            # DC 成分を除外して表示（index 1 以降）
            freqs = freqs[1:]
            amplitude = amplitude[1:]

            if len(freqs) == 0:
                continue

            # ピーク検出
            peak_idx = int(np.argmax(amplitude))
            peak_freq = float(freqs[peak_idx])
            peak_amp = float(amplitude[peak_idx])

            c = colors[ci % len(colors)]
            ax.plot(freqs, amplitude, color=c, linewidth=0.9,
                    label=case_name, alpha=0.85)
            # ピークマーカー
            ax.plot(peak_freq, peak_amp, "v", color=c, markersize=7)

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

        # 軸設定
        if use_log:
            ax.set_yscale("log")

        rec_name = self._rec_combo.currentText()
        field_name = self._field_combo.currentText()
        ax.set_xlabel("周波数 [Hz]")
        ax.set_ylabel("|H(f)|")
        ax.set_title(f"周波数応答 — {cat} / {rec_name} / {field_name}")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.set_xlim(left=0)
        if plotted > 1:
            ax.legend(fontsize=8)
        elif plotted == 1:
            ax.legend(fontsize=8)
        self._canvas.fig.tight_layout()
        self._canvas.draw()

        # ピーク情報
        self._peak_label.setText("  |  ".join(peak_texts))
        self._status_label.setText(
            f"{plotted} ケースをプロット  "
            f"[{cat} / rec={rec_idx} / field={field_idx}]"
        )
