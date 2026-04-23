"""
app/ui/hysteresis_widget.py
===========================

ダンパー・バネ履歴ループ（ヒステリシス）表示ウィジェット。

Damper.hst または Spring.hst から時刻歴を読み込み、

  * 主ループ（サブ要素に応じた F–D / F–A / F–V ループ）
  * F–V ループ（副ループ）

の履歴ループをタブ切り替えで表示する。複数のダンパーを重ね描きして比較できる。

iOD（複合制振装置）対応
-----------------------
iOD ダンパーは fpr=4 の「全体」レコードと fpr=11 の「サブ要素パック」
レコードを両方出力する。サブ要素セレクターで以下を選択できる:

- 自動: レコードの fpr から推定（fpr=4→全体 F-D, fpr=11→スプリング F-D）
- 全体: 主スプリング成分の F-D (fpr=4 なら全体 F-D, fpr=11 なら f1/f2)
- スプリング: F-D 線形（fpr=11 のみ）
- 質量: F-A 線形（fpr=11 のみ, F = m·A）
- ダッシュポット: F-V ヒステリシス（fpr=11 のみ）

非 iOD ファイルではサブ要素セレクターを自動/全体に固定する。

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
    SUB_ELEMENT_AUTO,
    SUB_ELEMENT_WHOLE,
    SUB_ELEMENT_MASS,
    SUB_ELEMENT_DASHPOT,
    SUB_ELEMENT_LABELS,
    SUB_ELEMENT_PRIMARY_KIND,
    fetch_hysteresis_data,
    compute_peak_stats,
    is_iod_layout,
)


# 単位換算（SNAP 内部は SI、表示は mm/mm/s/mm/s² で統一）
_MM_SCALE = 1000.0
_FORCE_SCALE = 1.0  # SNAP の力は既に kN 相当想定

# サブ要素ごとの主軸ラベル
_PRIMARY_AXIS_LABELS: Dict[str, Tuple[str, str]] = {
    # sub_element: (axis_name, unit_label)
    SUB_ELEMENT_WHOLE: ("変位", "mm"),
    SUB_ELEMENT_MASS: ("加速度", "mm/s²"),
    SUB_ELEMENT_DASHPOT: ("速度", "mm/s"),
}


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


def _primary_array(data: Dict[str, np.ndarray]) -> np.ndarray:
    """サブ要素の主軸変数 (D / A / V) を mm 単位に変換して返す。"""
    x_kind = data.get("x_kind", "D")
    arr = data.get(x_kind, np.zeros(0))
    return arr * _MM_SCALE


def _velocity_array(data: Dict[str, np.ndarray]) -> np.ndarray:
    """F-V 補助ループ用の V 配列 (mm/s) を返す。"""
    return data.get("V", np.zeros(0)) * _MM_SCALE


def _primary_label(sub_element: str) -> Tuple[str, str]:
    """(軸名, 単位) を返す。"""
    return _PRIMARY_AXIS_LABELS.get(sub_element, ("変位", "mm"))


# ---------------------------------------------------------------------------
# メインウィジェット
# ---------------------------------------------------------------------------

class HysteresisWidget(QWidget):
    """ダンパー・バネ履歴ループウィジェット。"""

    _CATEGORIES = ["Damper", "Spring"]
    _SUB_ELEMENTS_ALL = [
        SUB_ELEMENT_AUTO,
        SUB_ELEMENT_WHOLE,
        SUB_ELEMENT_MASS,
        SUB_ELEMENT_DASHPOT,
    ]
    _SUB_ELEMENTS_NON_IOD = [SUB_ELEMENT_AUTO, SUB_ELEMENT_WHOLE]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._entries: List[Tuple[str, SnapResultLoader]] = []
        self._iod_detected = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # パブリック API
    # ------------------------------------------------------------------

    def set_entries(self, entries: List[Tuple[str, SnapResultLoader]]) -> None:
        self._entries = list(entries) if entries else []
        self._refresh()

    def set_dyc_selections(self, selections: list) -> None:
        from pathlib import Path
        entries: List[Tuple[str, SnapResultLoader]] = []
        for sel in selections or []:
            path = getattr(sel, "result_dir", None)
            if path is None:
                continue
            try:
                loader = SnapResultLoader(Path(path))
            except Exception as exc:
                logger.debug("Hysteresis: loader 生成失敗 %s: %s", path, exc)
                continue
            entries.append((sel.short_name, loader))
        self.set_entries(entries)

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

        ctrl.addSpacing(8)
        ctrl.addWidget(QLabel("サブ要素:"))
        self._sub_combo = QComboBox()
        self._sub_combo.setMinimumWidth(130)
        self._populate_sub_combo(iod=False)
        self._sub_combo.currentIndexChanged.connect(self._on_sub_element_changed)
        ctrl.addWidget(self._sub_combo)

        ctrl.addSpacing(8)
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

    def _populate_sub_combo(self, iod: bool) -> None:
        """iOD 判定に応じてサブ要素セレクターの項目を更新する。"""
        items = self._SUB_ELEMENTS_ALL if iod else self._SUB_ELEMENTS_NON_IOD
        current = self._sub_combo.currentData() if self._sub_combo.count() else SUB_ELEMENT_AUTO
        self._sub_combo.blockSignals(True)
        self._sub_combo.clear()
        for key in items:
            self._sub_combo.addItem(SUB_ELEMENT_LABELS[key], key)
        # 元の選択を保持（項目が無くなった場合は auto に戻る）
        idx = self._sub_combo.findData(current)
        self._sub_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._sub_combo.blockSignals(False)

    def _build_status_label(self) -> QLabel:
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#666; font-size:11px; padding:2px 4px;")
        return self._status_label

    def _build_record_pane(self) -> QVBoxLayout:
        left = QVBoxLayout()
        left.addWidget(QLabel("ダンパー / バネ（複数選択可）:"))
        self._record_list = QListWidget()
        self._record_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._record_list.setMinimumWidth(180)
        self._record_list.setMaximumWidth(260)
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
        self._chart_tabs.addTab(self._build_main_tab(), "主ループ（サブ要素依存）")
        self._chart_tabs.addTab(self._build_fv_tab(), "F–V ループ（速度）")
        self._chart_tabs.addTab(self._build_peak_tab(), "ピーク一覧")
        self._chart_tabs.currentChanged.connect(self._on_tab_changed)
        return self._chart_tabs

    def _build_main_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self._main_canvas = _MplCanvas()
        lay.addWidget(NavigationToolbar(self._main_canvas, w))
        lay.addWidget(self._main_canvas, stretch=1)
        self._main_info = QLabel("")
        self._main_info.setWordWrap(True)
        self._main_info.setStyleSheet("color:#444; font-size:11px; padding:4px;")
        lay.addWidget(self._main_info)
        return w

    def _build_fv_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self._fv_canvas = _MplCanvas()
        lay.addWidget(NavigationToolbar(self._fv_canvas, w))
        lay.addWidget(self._fv_canvas, stretch=1)
        self._fv_info = QLabel("")
        self._fv_info.setWordWrap(True)
        self._fv_info.setStyleSheet("color:#444; font-size:11px; padding:4px;")
        lay.addWidget(self._fv_info)
        return w

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

    def _on_sub_element_changed(self, *_) -> None:
        self._redraw_all()

    def _on_dt_changed(self, *_) -> None:
        self._redraw_all()

    def _on_selection_changed(self) -> None:
        self._redraw_all()

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 0:
            self._draw_main_loop()
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
        cat = self._cat_combo.currentData() or "Damper"

        if not self._entries:
            self._record_list.clear()
            self._main_canvas.show_message("ケースが選択されていません")
            self._fv_canvas.show_message("ケースが選択されていません")
            self._status_label.setText("")
            self._iod_detected = False
            self._populate_sub_combo(iod=False)
            return

        first_bc: Optional[BinaryCategory] = None
        for _, loader in self._entries:
            bc = loader.get(cat)
            if bc and bc.hst and bc.hst.header:
                first_bc = bc
                break

        if first_bc is None:
            self._record_list.clear()
            self._main_canvas.show_message(
                f"{cat}.hst が見つかりません\n"
                "解析を実行してダンパー / バネの履歴出力を有効にしてください"
            )
            self._fv_canvas.show_message(f"{cat}.hst なし")
            self._status_label.setText(f"{cat}.hst なし")
            self._iod_detected = False
            self._populate_sub_combo(iod=False)
            return

        # iOD 判定
        per_record_fpr = getattr(first_bc.hst.header, "per_record_fpr", None)
        self._iod_detected = (cat == "Damper") and is_iod_layout(per_record_fpr)
        self._populate_sub_combo(iod=self._iod_detected)

        n_rec = first_bc.num_records
        fpr_counts: Dict[int, int] = {}
        if per_record_fpr:
            for f in per_record_fpr:
                fpr_counts[int(f)] = fpr_counts.get(int(f), 0) + 1
        fpr_summary = ", ".join(f"fpr={k}×{v}" for k, v in sorted(fpr_counts.items())) \
            if fpr_counts else f"fpr={first_bc.hst.header.fields_per_record}"
        iod_tag = " [iOD 混在]" if self._iod_detected else ""
        self._status_label.setText(
            f"{cat}.hst: {n_rec} レコード / {fpr_summary}{iod_tag}"
        )

        self._record_list.blockSignals(True)
        self._record_list.clear()
        for i in range(n_rec):
            name = first_bc.record_name(i)
            # fpr 情報をラベルに付加
            fpr_i = int(per_record_fpr[i]) if per_record_fpr and i < len(per_record_fpr) \
                else first_bc.hst.header.fields_per_record
            suffix = f"  [fpr={fpr_i}]" if self._iod_detected else ""
            item = QListWidgetItem(f"{name}{suffix}")
            item.setData(Qt.UserRole, i)
            self._record_list.addItem(item)
        if n_rec > 0:
            self._record_list.item(0).setSelected(True)
        self._record_list.blockSignals(False)

        self._redraw_all()

    def _redraw_all(self) -> None:
        tab = self._chart_tabs.currentIndex()
        if tab == 0:
            self._draw_main_loop()
        elif tab == 1:
            self._draw_fv_loop()
        elif tab == 2:
            self._draw_peak_table()

    # ------------------------------------------------------------------
    # グラフ描画: 主ループ（サブ要素依存）
    # ------------------------------------------------------------------

    def _draw_main_loop(self) -> None:
        """サブ要素に応じた主履歴ループを描画する。

        全体/スプリング → F-D, 質量 → F-A, ダッシュポット → F-V
        """
        ax = self._main_canvas.ax
        ax.clear()

        data_list = self._collect_selected_data()
        sub = self._sub_combo.currentData() or SUB_ELEMENT_AUTO

        applied_data = [(lbl, d) for lbl, d in data_list if d.get("applies", True)]
        if not applied_data:
            if data_list:
                self._main_canvas.show_message(
                    "選択レコードにはこのサブ要素のデータがありません\n"
                    "（fpr=4 レコードは全体/自動のみ, fpr=11 のみが質量/ダッシュポット対応）",
                    color="orange",
                )
            else:
                self._main_canvas.show_message("データがありません")
            self._main_info.setText("")
            return

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        all_x: List[np.ndarray] = []
        all_F: List[np.ndarray] = []
        summaries: List[str] = []
        ref_sub = applied_data[0][1].get("sub_element", sub)
        axis_name, unit = _primary_label(ref_sub)

        for ci, (label, data) in enumerate(applied_data):
            c = colors[ci % len(colors)]
            x_arr = _primary_array(data)
            F = data["F"]
            if x_arr.size == 0:
                continue
            ax.plot(x_arr, F, linewidth=0.8, color=c, label=label, alpha=0.85)
            all_x.append(x_arr)
            all_F.append(F)

            stats = compute_peak_stats(data)
            x_kind = data.get("x_kind", "D")
            x_peak = stats.get(f"max_{x_kind}", 0.0) * _MM_SCALE
            summaries.append(
                f"{label}: |F|max={stats['max_F']:.4g} kN,  "
                f"|{axis_name}|max={x_peak:.4g} {unit}"
            )

        if not all_x:
            self._main_canvas.show_message("データがありません")
            self._main_info.setText("\n".join(summaries))
            return

        ax.axhline(0, color="#888", linewidth=0.6)
        ax.axvline(0, color="#888", linewidth=0.6)
        ax.set_xlabel(f"{axis_name} [{unit}]")
        ax.set_ylabel("荷重 F [kN]")
        title_map = {
            SUB_ELEMENT_WHOLE: "全体 応力-変形（F-D ループ）",
            SUB_ELEMENT_MASS: "質量 応力-加速度（F-A, F = m·A）",
            SUB_ELEMENT_DASHPOT: "ダッシュポット 応力-速度（F-V ループ）",
        }
        ax.set_title(title_map.get(ref_sub, "履歴ループ"))
        ax.grid(True, linestyle=":", alpha=0.4)
        _set_tight_axis(ax, np.concatenate(all_x), np.concatenate(all_F))
        if len(applied_data) > 1:
            ax.legend(fontsize=7, ncol=2)
        self._main_canvas.fig.tight_layout()
        self._main_canvas.draw()
        self._main_info.setText("\n".join(summaries))

    # ------------------------------------------------------------------
    # グラフ描画: F–V ループ（副ループ）
    # ------------------------------------------------------------------

    def _draw_fv_loop(self) -> None:
        """力–速度（F–V）履歴ループを描画する（副ループ）。"""
        ax = self._fv_canvas.ax
        ax.clear()

        data_list = self._collect_selected_data()
        applied_data = [(lbl, d) for lbl, d in data_list if d.get("applies", True)]
        if not applied_data:
            if data_list:
                self._fv_canvas.show_message(
                    "選択レコードにはこのサブ要素のデータがありません",
                    color="orange",
                )
            else:
                self._fv_canvas.show_message("データがありません")
            self._fv_info.setText("")
            return

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        all_V: List[np.ndarray] = []
        all_F: List[np.ndarray] = []
        summaries: List[str] = []
        any_derived = False

        for ci, (label, data) in enumerate(applied_data):
            V = _velocity_array(data)
            F = data["F"]
            if V.size == 0 or np.all(V == 0):
                summaries.append(f"{label}: 速度データなし")
                continue

            c = colors[ci % len(colors)]
            ax.plot(V, F, linewidth=0.8, color=c, label=label, alpha=0.85)
            all_V.append(V)
            all_F.append(F)
            stats = compute_peak_stats(data)
            v_peak = stats["max_V"] * _MM_SCALE
            derived = bool(data.get("v_derived"))
            any_derived = any_derived or derived
            tag = "  ※V=dD/dt" if derived else ""
            summaries.append(
                f"{label}: |F|max={stats['max_F']:.4g} kN,  "
                f"|V|max={v_peak:.4g} mm/s{tag}"
            )

        if not all_V:
            self._fv_canvas.show_message(
                "速度データが取得できませんでした",
                color="orange",
            )
            self._fv_info.setText("\n".join(summaries))
            return

        ax.axhline(0, color="#888", linewidth=0.6)
        ax.axvline(0, color="#888", linewidth=0.6)
        ax.set_xlabel("速度 V [mm/s]")
        ax.set_ylabel("荷重 F [kN]")
        title = "力–速度（F–V）履歴ループ"
        if any_derived:
            title += "（V は変位の数値微分）"
        ax.set_title(title)
        ax.grid(True, linestyle=":", alpha=0.4)
        _set_tight_axis(ax, np.concatenate(all_V), np.concatenate(all_F))
        if len(applied_data) > 1:
            ax.legend(fontsize=7, ncol=2)
        self._fv_canvas.fig.tight_layout()
        self._fv_canvas.draw()
        self._fv_info.setText("\n".join(summaries))

    # ------------------------------------------------------------------
    # ピーク一覧テーブル
    # ------------------------------------------------------------------

    def _draw_peak_table(self) -> None:
        cat = self._cat_combo.currentData() or "Damper"
        dt = float(self._dt_spin.value())
        sub = self._sub_combo.currentData() or SUB_ELEMENT_AUTO
        headers = ["ケース", "レコード", "サブ要素",
                   "max |F| [kN]", "max |D| [mm]",
                   "max |V| [mm/s]", "max |A| [mm/s²]",
                   "max |E|", "仕事量 ∮FdD"]

        rows: List[List[str]] = []
        for case_name, loader in self._entries:
            bc = loader.get(cat)
            if not bc or not bc.hst or bc.hst.header is None:
                continue
            n_rec = bc.hst.header.num_records
            for ri in range(n_rec):
                data = fetch_hysteresis_data(loader, cat, ri, dt, sub_element=sub)
                if data is None or not data.get("applies", True):
                    continue
                stats = compute_peak_stats(data)
                sub_label = SUB_ELEMENT_LABELS.get(data.get("sub_element", sub), "")
                rows.append([
                    case_name,
                    bc.record_name(ri),
                    sub_label,
                    f"{stats['max_F']:.4g}",
                    f"{stats['max_D']*_MM_SCALE:.4g}",
                    f"{stats['max_V']*_MM_SCALE:.4g}",
                    f"{stats['max_A']*_MM_SCALE:.4g}",
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
        cat = self._cat_combo.currentData() or "Damper"
        dt = float(self._dt_spin.value())
        sub = self._sub_combo.currentData() or SUB_ELEMENT_AUTO

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
                data = fetch_hysteresis_data(loader, cat, ri, dt, sub_element=sub)
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
