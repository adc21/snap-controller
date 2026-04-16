"""
app/ui/hysteresis_widget.py
===========================

ダンパー・バネ履歴ループ（ヒステリシス）表示ウィジェット。

Damper.hst または Spring.hst から時刻歴を読み込み、

  * 力–変位（F–D）ループ
  * 力–速度（F–V）ループ

の 2 種類の履歴ループをタブ切り替えで表示する。
複数のダンパーを重ね描きして比較できる。

主な機能
--------
- F–D ループ（Force vs Displacement）
- F–V ループ（Force vs Velocity）  ← 既存タブにはなかった新機能
- 複数ダンパー（レコード）の重ね描き
- ピーク一覧テーブル（最大荷重・変位・速度・エネルギー）
- 複数ケース対応（ケース別に色分け）

使い方
------
::

    widget = HysteresisWidget(parent=self)
    widget.set_entries([
        ("ベースケース", snap_result_loader_0),
        ("ダンパーあり", snap_result_loader_1),
    ])
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

import matplotlib
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QHeaderView,
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QCheckBox,
)

from controller.binary.result_loader import SnapResultLoader, BinaryCategory
from controller.binary.hst_reader import HstReader


# ---------------------------------------------------------------------------
# フィールドインデックス定数・純粋ロジック（controller から再エクスポート）
# ---------------------------------------------------------------------------

from controller.binary.hysteresis_analysis import (
    FIELD_FORCE,
    FIELD_DISP,
    FIELD_VEL,
    FIELD_ENERGY,
    fetch_hysteresis_data,
    compute_peak_stats,
)


# ---------------------------------------------------------------------------
# matplotlib キャンバス（軽量ラッパー）
# ---------------------------------------------------------------------------

class _MplCanvas(FigureCanvas):
    """Matplotlib Figure を PySide6 に埋め込む最小ラッパー。"""

    def __init__(self, parent=None, width: float = 6.0, height: float = 3.8,
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
        )
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.draw()


def _set_tight_axis(ax, x_arr: np.ndarray, y_arr: np.ndarray,
                    margin: float = 0.10) -> None:
    """x / y 配列に合わせて軸範囲を設定する（極小値対応）。"""
    for arr, setter in ((x_arr, ax.set_xlim), (y_arr, ax.set_ylim)):
        if arr.size == 0:
            continue
        lo, hi = float(arr.min()), float(arr.max())
        span = hi - lo
        if span < max(abs(lo), abs(hi), 1e-30) * 1e-10:
            m = max(abs(lo), abs(hi)) * 0.20
        else:
            m = span * margin
        setter(lo - m, hi + m)


# ---------------------------------------------------------------------------
# データ取得ヘルパー
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# メインウィジェット
# ---------------------------------------------------------------------------

class HysteresisWidget(QWidget):
    """ダンパー・バネ履歴ループウィジェット。

    F–D ループと F–V ループをタブ切り替えで表示し、
    ピーク統計テーブルも提供する。

    使い方::

        w = HysteresisWidget()
        w.set_entries([("ケース名", snap_result_loader)])
    """

    _CATEGORIES = ["Damper", "Spring"]

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
        self._refresh()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        root.addLayout(self._build_control_row())
        root.addWidget(self._build_status_label())

        body = QHBoxLayout()
        body.addLayout(self._build_record_pane())
        body.addWidget(self._build_chart_tabs(), stretch=1)
        root.addLayout(body, stretch=1)

    def _build_control_row(self) -> QHBoxLayout:
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("🔄 履歴ループ（ヒステリシス）ビューア"))
        ctrl.addStretch(1)

        ctrl.addWidget(QLabel("カテゴリ:"))
        self._cat_combo = QComboBox()
        for cat in self._CATEGORIES:
            self._cat_combo.addItem(cat, cat)
        self._cat_combo.setMinimumWidth(100)
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        ctrl.addWidget(self._cat_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("dt [s]:"))
        self._dt_spin = QDoubleSpinBox()
        self._dt_spin.setRange(0.00001, 1.0)
        self._dt_spin.setDecimals(5)
        self._dt_spin.setValue(0.005)
        self._dt_spin.setSingleStep(0.001)
        self._dt_spin.setMinimumWidth(80)
        self._dt_spin.valueChanged.connect(self._on_dt_changed)
        ctrl.addWidget(self._dt_spin)

        btn_refresh = QPushButton("更新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self._refresh)
        ctrl.addWidget(btn_refresh)
        return ctrl

    def _build_status_label(self) -> QLabel:
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#666; font-size:11px; padding:2px 4px;")
        return self._status_label

    def _build_record_pane(self) -> QVBoxLayout:
        left = QVBoxLayout()
        left.addWidget(QLabel("ダンパー / バネ（複数選択可）:"))
        self._record_list = QListWidget()
        self._record_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._record_list.setMinimumWidth(150)
        self._record_list.setMaximumWidth(220)
        self._record_list.itemSelectionChanged.connect(self._on_selection_changed)
        left.addWidget(self._record_list, stretch=1)

        btn_all = QPushButton("全選択")
        btn_all.clicked.connect(self._select_all_records)
        btn_clear = QPushButton("全解除")
        btn_clear.clicked.connect(self._clear_record_selection)
        row = QHBoxLayout()
        row.addWidget(btn_all)
        row.addWidget(btn_clear)
        left.addLayout(row)
        return left

    def _build_chart_tabs(self) -> QTabWidget:
        self._chart_tabs = QTabWidget()
        self._chart_tabs.setDocumentMode(True)
        self._chart_tabs.addTab(self._build_fd_tab(), "F–D ループ（力–変位）")
        self._chart_tabs.addTab(self._build_fv_tab(), "F–V ループ（力–速度）")
        self._chart_tabs.addTab(self._build_peak_tab(), "ピーク一覧")
        self._chart_tabs.currentChanged.connect(self._on_tab_changed)
        return self._chart_tabs

    def _build_fd_tab(self) -> QWidget:
        w_fd = QWidget()
        lay_fd = QVBoxLayout(w_fd)
        lay_fd.setContentsMargins(2, 2, 2, 2)
        self._fd_canvas = _MplCanvas()
        lay_fd.addWidget(NavigationToolbar(self._fd_canvas, w_fd))
        lay_fd.addWidget(self._fd_canvas, stretch=1)
        self._fd_info = QLabel("")
        self._fd_info.setWordWrap(True)
        self._fd_info.setStyleSheet("color:#444; font-size:11px; padding:4px;")
        lay_fd.addWidget(self._fd_info)
        return w_fd

    def _build_fv_tab(self) -> QWidget:
        w_fv = QWidget()
        lay_fv = QVBoxLayout(w_fv)
        lay_fv.setContentsMargins(2, 2, 2, 2)
        self._fv_canvas = _MplCanvas()
        lay_fv.addWidget(NavigationToolbar(self._fv_canvas, w_fv))
        lay_fv.addWidget(self._fv_canvas, stretch=1)
        self._fv_info = QLabel("")
        self._fv_info.setWordWrap(True)
        self._fv_info.setStyleSheet("color:#444; font-size:11px; padding:4px;")
        lay_fv.addWidget(self._fv_info)
        return w_fv

    def _build_peak_tab(self) -> QWidget:
        w_peak = QWidget()
        lay_peak = QVBoxLayout(w_peak)
        lay_peak.setContentsMargins(2, 2, 2, 2)
        self._peak_table = QTableWidget()
        self._peak_table.setAlternatingRowColors(True)
        self._peak_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._peak_table.horizontalHeader().setStretchLastSection(True)
        self._peak_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        lay_peak.addWidget(self._peak_table)
        return w_peak

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------

    def _on_category_changed(self, *_) -> None:
        self._refresh()

    def _on_dt_changed(self, *_) -> None:
        self._redraw_all()

    def _on_selection_changed(self) -> None:
        self._redraw_all()

    def _on_tab_changed(self, idx: int) -> None:
        """タブ切り替え時に対応グラフを再描画する。"""
        if idx == 0:
            self._draw_fd_loop()
        elif idx == 1:
            self._draw_fv_loop()
        elif idx == 2:
            self._draw_peak_table()

    def _select_all_records(self) -> None:
        for i in range(self._record_list.count()):
            self._record_list.item(i).setSelected(True)

    def _clear_record_selection(self) -> None:
        self._record_list.clearSelection()

    # ------------------------------------------------------------------
    # リフレッシュ
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """カテゴリ変更時などに全体を更新する。"""
        cat = self._cat_combo.currentData() or "Damper"

        if not self._entries:
            self._record_list.clear()
            self._fd_canvas.show_message("ケースが選択されていません")
            self._fv_canvas.show_message("ケースが選択されていません")
            self._status_label.setText("")
            return

        # 最初の有効ケースからレコード名一覧を取得
        first_bc: Optional[BinaryCategory] = None
        for _, loader in self._entries:
            bc = loader.get(cat)
            if bc and bc.hst and bc.hst.header:
                first_bc = bc
                break

        if first_bc is None:
            self._record_list.clear()
            self._fd_canvas.show_message(
                f"{cat}.hst が見つかりません\n"
                "解析を実行してダンパー / バネの履歴出力を有効にしてください"
            )
            self._fv_canvas.show_message(f"{cat}.hst なし")
            self._status_label.setText(f"{cat}.hst なし")
            return

        n_rec = first_bc.num_records
        self._status_label.setText(
            f"{cat}.hst: {n_rec} レコード / "
            f"step_size={first_bc.hst.header.step_size}"
        )

        # レコードリスト更新
        self._record_list.blockSignals(True)
        self._record_list.clear()
        for i in range(n_rec):
            name = first_bc.record_name(i)
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, i)
            self._record_list.addItem(item)
        if n_rec > 0:
            self._record_list.item(0).setSelected(True)
        self._record_list.blockSignals(False)

        self._redraw_all()

    def _redraw_all(self) -> None:
        """現在のタブに応じて再描画する。"""
        tab = self._chart_tabs.currentIndex()
        if tab == 0:
            self._draw_fd_loop()
        elif tab == 1:
            self._draw_fv_loop()
        elif tab == 2:
            self._draw_peak_table()

    # ------------------------------------------------------------------
    # グラフ描画: F–D ループ
    # ------------------------------------------------------------------

    def _draw_fd_loop(self) -> None:
        """力–変位（F–D）履歴ループを描画する。"""
        ax = self._fd_canvas.ax
        ax.clear()

        data_list = self._collect_selected_data()
        if not data_list:
            self._fd_canvas.show_message("データがありません")
            self._fd_info.setText("")
            return

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        all_D: List[np.ndarray] = []
        all_F: List[np.ndarray] = []
        summaries: List[str] = []

        for ci, (label, data) in enumerate(data_list):
            c = colors[ci % len(colors)]
            ax.plot(data["D"], data["F"], linewidth=0.8, color=c,
                    label=label, alpha=0.85)
            all_D.append(data["D"])
            all_F.append(data["F"])
            stats = compute_peak_stats(data)
            summaries.append(
                f"{label}: |F|max={stats['max_F']:.4g},  "
                f"|D|max={stats['max_D']:.4g},  ∮FdD≈{stats['work']:.4g}"
            )

        ax.axhline(0, color="#888", linewidth=0.6)
        ax.axvline(0, color="#888", linewidth=0.6)
        ax.set_xlabel("変位 D")
        ax.set_ylabel("荷重 F")
        ax.set_title("力–変位（F–D）履歴ループ")
        ax.grid(True, linestyle=":", alpha=0.4)
        if all_D:
            _set_tight_axis(ax, np.concatenate(all_D), np.concatenate(all_F))
        if len(data_list) > 1:
            ax.legend(fontsize=7, ncol=2)
        self._fd_canvas.fig.tight_layout()
        self._fd_canvas.draw()
        self._fd_info.setText("\n".join(summaries))

    # ------------------------------------------------------------------
    # グラフ描画: F–V ループ（新機能）
    # ------------------------------------------------------------------

    def _draw_fv_loop(self) -> None:
        """力–速度（F–V）履歴ループを描画する。

        粘性ダンパーでは F ∝ V^α（速度依存型）の特性が現れるため、
        この図は減衰係数 C や速度指数 α の視覚的確認に利用できる。
        """
        ax = self._fv_canvas.ax
        ax.clear()

        data_list = self._collect_selected_data()
        if not data_list:
            self._fv_canvas.show_message("データがありません")
            self._fv_info.setText("")
            return

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        all_V: List[np.ndarray] = []
        all_F: List[np.ndarray] = []
        summaries: List[str] = []

        for ci, (label, data) in enumerate(data_list):
            V = data["V"]
            F = data["F"]
            if V.size == 0 or np.all(V == 0):
                # 速度データがゼロ（未出力）の場合はスキップ
                summaries.append(f"{label}: 速度データなし（SNAP 出力設定を確認）")
                continue

            c = colors[ci % len(colors)]
            ax.plot(V, F, linewidth=0.8, color=c, label=label, alpha=0.85)
            all_V.append(V)
            all_F.append(F)
            stats = compute_peak_stats(data)
            summaries.append(
                f"{label}: |F|max={stats['max_F']:.4g},  "
                f"|V|max={stats['max_V']:.4g}"
            )

        if not all_V:
            self._fv_canvas.show_message(
                "速度データが取得できませんでした\n"
                "（SNAP の出力設定で速度成分を有効にしてください）",
                color="orange",
            )
            self._fv_info.setText("\n".join(summaries))
            return

        ax.axhline(0, color="#888", linewidth=0.6)
        ax.axvline(0, color="#888", linewidth=0.6)
        ax.set_xlabel("速度 V  [m/s 等]")
        ax.set_ylabel("荷重 F")
        ax.set_title("力–速度（F–V）履歴ループ")
        ax.grid(True, linestyle=":", alpha=0.4)
        _set_tight_axis(ax, np.concatenate(all_V), np.concatenate(all_F))
        if len(data_list) > 1:
            ax.legend(fontsize=7, ncol=2)
        self._fv_canvas.fig.tight_layout()
        self._fv_canvas.draw()
        self._fv_info.setText("\n".join(summaries))

    # ------------------------------------------------------------------
    # ピーク一覧テーブル
    # ------------------------------------------------------------------

    def _draw_peak_table(self) -> None:
        """各レコード × ケースのピーク統計テーブルを更新する。"""
        cat = self._cat_combo.currentData() or "Damper"
        dt = float(self._dt_spin.value())
        headers = ["ケース", "レコード",
                   "max |F|", "max |D|", "max |V|", "max |E|", "仕事量 ∮FdD"]

        rows: List[List[str]] = []
        n_rec_max = max(
            (loader.get(cat).num_records
             for _, loader in self._entries
             if loader.get(cat) and loader.get(cat).hst),
            default=0,
        )
        for case_name, loader in self._entries:
            bc = loader.get(cat)
            if not bc or not bc.hst or bc.hst.header is None:
                continue
            n_rec = bc.hst.header.num_records
            for ri in range(n_rec):
                data = fetch_hysteresis_data(loader, cat, ri, dt)
                if data is None:
                    continue
                stats = compute_peak_stats(data)
                rows.append([
                    case_name,
                    bc.record_name(ri),
                    f"{stats['max_F']:.4g}",
                    f"{stats['max_D']:.4g}",
                    f"{stats['max_V']:.4g}",
                    f"{stats['max_E']:.4g}",
                    f"{stats['work']:.4g}",
                ])

        table = self._peak_table
        table.setRowCount(len(rows))
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(i, j, item)

    # ------------------------------------------------------------------
    # 選択レコードデータ収集
    # ------------------------------------------------------------------

    def _collect_selected_data(self) -> List[Tuple[str, Dict[str, np.ndarray]]]:
        """選択中のレコード × ケースのデータを収集して返す。

        Returns
        -------
        list of (label, data_dict)
            label: "ケース名 / レコード名"
            data_dict: {"t", "F", "D", "V", "E"}
        """
        cat = self._cat_combo.currentData() or "Damper"
        dt = float(self._dt_spin.value())

        selected_items = self._record_list.selectedItems()
        if not selected_items:
            return []
        rec_indices = [item.data(Qt.UserRole) for item in selected_items
                       if item.data(Qt.UserRole) is not None]
        if not rec_indices:
            return []

        results: List[Tuple[str, Dict[str, np.ndarray]]] = []
        for case_name, loader in self._entries:
            bc = loader.get(cat)
            if not bc:
                continue
            for ri in rec_indices:
                data = fetch_hysteresis_data(loader, cat, ri, dt)
                if data is None:
                    continue
                rec_label = bc.record_name(ri) if bc else f"rec{ri}"
                label = (
                    f"{case_name} / {rec_label}"
                    if len(self._entries) > 1
                    else rec_label
                )
                results.append((label, data))

        return results
