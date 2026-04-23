"""
app/ui/mode_shape_widget.py
===========================

固有モード形状（固有ベクトル）表示ウィジェット。

Period.xbn の刺激係数・参加質量比を一覧テーブルで表示し、
MDFloor.xbn が利用可能な場合は各階の振幅（変形形状）を
折れ線グラフでプロットする。

主な機能
--------
- 刺激係数 β_X / β_Y / β_Z のモードごとバーチャート
- 固有周期・振動数・参加質量比の一覧テーブル
- MDFloor.xbn を使った階ごとのモード形状プロット
  * モード番号セレクタ（Mode 1, 2, 3…）
  * 成分セレクタ（Dx, Dy, …）
  * 複数ケース重ね描き対応
- BinaryResultWidget.prepend_tab() または MainWindow で単独タブとして追加可能

使い方
------
::

    widget = ModeShapeWidget(parent=main_widget)
    widget.set_entries([
        ("case_A", snap_result_loader_a),
        ("case_B", snap_result_loader_b),
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
    QPushButton,
    QHeaderView,
    QAbstractItemView,
)

from controller.binary.result_loader import SnapResultLoader
from controller.binary.period_xbn_reader import PeriodXbnReader, ModeInfo


# ---------------------------------------------------------------------------
# matplotlib キャンバス（軽量ラッパー）
# ---------------------------------------------------------------------------

class _MplCanvas(FigureCanvas):
    """Matplotlib Figure を PySide6 に埋め込む最小ラッパー。"""

    def __init__(self, parent=None, width: float = 6.0, height: float = 3.5,
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


def _empty_label(msg: str) -> QLabel:
    lab = QLabel(msg)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet("color: #888; font-size: 12px; padding: 30px;")
    return lab


# ---------------------------------------------------------------------------
# ModalDisplacementReader: MDFloor.xbn / MDNode.xbn 専用リーダー
# ---------------------------------------------------------------------------

from controller.binary.modal_displacement_reader import ModalDisplacementReader


# ---------------------------------------------------------------------------
# メインウィジェット
# ---------------------------------------------------------------------------

class ModeShapeWidget(QWidget):
    """固有モード形状ウィジェット。

    BinaryResultWidget の prepend_tab() または
    MainWindow に直接 addTab() して利用する。

    使い方::

        w = ModeShapeWidget()
        w.set_entries([("ケース名", snap_result_loader)])
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # (case_name, SnapResultLoader) リスト
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

    def set_dyc_selections(self, selections: list) -> None:
        """CaseDycSelectorWidget からの DycSelection リストを受け取り、
        各選択について SnapResultLoader を生成して set_entries() に渡す。
        """
        from pathlib import Path
        entries: List[Tuple[str, SnapResultLoader]] = []
        for sel in selections or []:
            path = getattr(sel, "result_dir", None)
            if path is None:
                continue
            try:
                loader = SnapResultLoader(Path(path))
            except Exception as exc:
                logger.debug("ModeShape: loader 生成失敗 %s: %s", path, exc)
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

        # ---- 上部コントロールバー ----
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("🏗 固有モード形状ビューア"))

        ctrl.addStretch(1)

        ctrl.addWidget(QLabel("モード:"))
        self._mode_combo = QComboBox()
        self._mode_combo.setMinimumWidth(90)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        ctrl.addWidget(self._mode_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("成分:"))
        self._dof_combo = QComboBox()
        self._dof_combo.setMinimumWidth(80)
        self._dof_combo.currentIndexChanged.connect(self._on_dof_changed)
        ctrl.addWidget(self._dof_combo)

        btn_refresh = QPushButton("更新")
        btn_refresh.setFixedWidth(60)
        btn_refresh.clicked.connect(self._refresh)
        ctrl.addWidget(btn_refresh)

        root.addLayout(ctrl)

        # ---- ステータス ----
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#666; font-size:11px; padding:2px 4px;")
        root.addWidget(self._status_label)

        # ---- メイン分割エリア（テーブル上 / チャート下）----
        splitter = QSplitter(Qt.Vertical)

        # --- 上：モード一覧テーブル ---
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumHeight(100)
        splitter.addWidget(self._table)

        # --- 下：チャートタブ ---
        self._chart_tabs = QTabWidget()
        self._chart_tabs.setDocumentMode(True)

        # タブ 1: 刺激係数 β
        w_beta = QWidget()
        lay_beta = QVBoxLayout(w_beta)
        lay_beta.setContentsMargins(2, 2, 2, 2)
        self._beta_canvas = _MplCanvas(height=3.0)
        lay_beta.addWidget(NavigationToolbar(self._beta_canvas, w_beta))
        lay_beta.addWidget(self._beta_canvas, stretch=1)
        self._chart_tabs.addTab(w_beta, "刺激係数 β")

        # タブ 2: モード形状（MDFloor.xbn）
        w_shape = QWidget()
        lay_shape = QVBoxLayout(w_shape)
        lay_shape.setContentsMargins(2, 2, 2, 2)
        self._shape_canvas = _MplCanvas(height=3.0)
        lay_shape.addWidget(NavigationToolbar(self._shape_canvas, w_shape))
        lay_shape.addWidget(self._shape_canvas, stretch=1)
        self._chart_tabs.addTab(w_shape, "モード形状（MDFloor）")

        splitter.addWidget(self._chart_tabs)
        splitter.setSizes([180, 340])
        root.addWidget(splitter, stretch=1)

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------

    def _on_mode_changed(self, *_) -> None:
        self._draw_shape_chart()

    def _on_dof_changed(self, *_) -> None:
        self._draw_shape_chart()

    # ------------------------------------------------------------------
    # リフレッシュ
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """全表示を更新する。"""
        if not self._entries:
            self._table.setRowCount(0)
            self._beta_canvas.show_message("ケースが選択されていません")
            self._shape_canvas.show_message("ケースが選択されていません")
            self._status_label.setText("")
            return

        # Period.xbn データを収集
        period_entries: List[Tuple[str, PeriodXbnReader]] = []
        for name, loader in self._entries:
            if loader.period and loader.period.modes:
                period_entries.append((name, loader.period))

        if not period_entries:
            self._table.setRowCount(0)
            self._beta_canvas.show_message(
                "Period.xbn が見つかりません\n固有値解析を実行してください"
            )
            self._shape_canvas.show_message("固有値データがありません")
            self._status_label.setText("Period.xbn が見つかりません")
            return

        self._status_label.setText(
            f"{len(period_entries)} ケースの固有値データをロード済み"
        )

        self._update_table(period_entries)
        self._update_mode_combo(period_entries)
        self._draw_beta_chart(period_entries)
        self._draw_shape_chart()

    def _update_table(
        self, period_entries: List[Tuple[str, PeriodXbnReader]]
    ) -> None:
        """モードプロパティ一覧テーブルを更新する。"""
        headers = [
            "ケース", "モード", "周期 T [s]", "振動数 f [Hz]",
            "ω [rad/s]", "支配方向",
            "β_X", "β_Y", "β_Z",
            "PM_X [%]", "PM_Y [%]", "累積PM_X [%]", "累積PM_Y [%]",
        ]
        rows: List[List[str]] = []
        for case_name, reader in period_entries:
            cx, cy = 0.0, 0.0
            for m in sorted(reader.modes, key=lambda x: x.mode_no):
                cx += abs(m.pm.get("X", 0.0))
                cy += abs(m.pm.get("Y", 0.0))
                rows.append([
                    case_name,
                    str(m.mode_no),
                    f"{m.period:.4f}",
                    f"{m.frequency:.4f}",
                    f"{m.omega:.4f}",
                    m.dominant_direction,
                    f"{m.beta.get('X', 0.0):.4f}",
                    f"{m.beta.get('Y', 0.0):.4f}",
                    f"{m.beta.get('Z', 0.0):.4f}",
                    f"{m.pm.get('X', 0.0):.2f}",
                    f"{m.pm.get('Y', 0.0):.2f}",
                    f"{cx:.2f}",
                    f"{cy:.2f}",
                ])

        self._table.setRowCount(len(rows))
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(i, j, item)

    def _update_mode_combo(
        self, period_entries: List[Tuple[str, PeriodXbnReader]]
    ) -> None:
        """モード選択コンボを更新する（最大モード数に合わせる）。"""
        max_modes = max(
            (len(reader.modes) for _, reader in period_entries), default=0
        )
        prev = self._mode_combo.currentData()
        self._mode_combo.blockSignals(True)
        self._mode_combo.clear()
        for i in range(max_modes):
            self._mode_combo.addItem(f"モード {i + 1}", i)
        if prev is not None and 0 <= prev < max_modes:
            self._mode_combo.setCurrentIndex(prev)
        self._mode_combo.blockSignals(False)

    def _draw_beta_chart(
        self, period_entries: List[Tuple[str, PeriodXbnReader]]
    ) -> None:
        """刺激係数 β の棒グラフを描く。"""
        ax = self._beta_canvas.ax
        ax.clear()

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        n_cases = len(period_entries)
        max_modes = max(
            (len(r.modes) for _, r in period_entries), default=0
        )
        if max_modes == 0:
            self._beta_canvas.show_message("刺激係数データがありません")
            return

        x = np.arange(1, max_modes + 1)
        bar_width = 0.35 / max(n_cases, 1)

        for ci, (case_name, reader) in enumerate(period_entries):
            sorted_modes = sorted(reader.modes, key=lambda m: m.mode_no)
            mode_nos = [m.mode_no for m in sorted_modes]
            beta_x = [m.beta.get("X", 0.0) for m in sorted_modes]
            beta_y = [m.beta.get("Y", 0.0) for m in sorted_modes]

            c = colors[ci % len(colors)]
            offset_x = (ci - n_cases / 2 + 0.5) * bar_width * 2 - bar_width * 0.5
            offset_y = offset_x + bar_width

            ax.bar(
                [n + offset_x for n in mode_nos],
                beta_x,
                width=bar_width,
                label=f"{case_name} β_X",
                color=c,
                alpha=0.85,
            )
            ax.bar(
                [n + offset_y for n in mode_nos],
                beta_y,
                width=bar_width,
                label=f"{case_name} β_Y",
                color=c,
                alpha=0.50,
                hatch="//",
            )

        ax.axhline(0, color="#888", linewidth=0.8)
        ax.set_xlabel("モード番号")
        ax.set_ylabel("刺激係数 β")
        ax.set_title("刺激係数 β — 絶対値が大きいモードが地震応答に支配的")
        ax.set_xticks(list(range(1, max_modes + 1)))
        ax.grid(True, axis="y", linestyle=":", alpha=0.5)
        if n_cases > 1 or True:  # 常に凡例を表示
            ax.legend(fontsize=8, ncol=min(n_cases * 2, 4))
        self._beta_canvas.fig.tight_layout()
        self._beta_canvas.draw()

    def _draw_shape_chart(self) -> None:
        """MDFloor.xbn を使って選択モードの固有形状（各階振幅）を描く。"""
        ax = self._shape_canvas.ax
        ax.clear()

        mode_idx: int = self._mode_combo.currentData() or 0
        prev_direction: Optional[str] = self._dof_combo.currentData()

        # MDFloor.xbn の ModalDisplacementReader を持つエントリを収集
        md_entries: List[Tuple[str, ModalDisplacementReader, Optional[List[str]]]] = []
        for case_name, loader in self._entries:
            bc = loader.get("MDFloor")
            if bc and bc.md and bc.md.data is not None and bc.md.num_items > 0:
                stp_names = (
                    list(bc.stp.names) if bc.stp and bc.stp.names else None
                )
                md_entries.append((case_name, bc.md, stp_names))

        if not md_entries:
            self._draw_shape_from_beta(ax, mode_idx)
            return

        # 利用可能な direction 一覧を最初のケースから取得
        first_md = md_entries[0][1]
        directions = first_md.available_directions()
        if not directions:
            directions = list(first_md.slot_map().values())[: first_md.dof_per_item]

        # DOF コンボ再構築 (direction ラベルをそのまま userData に入れる)
        self._dof_combo.blockSignals(True)
        self._dof_combo.clear()
        for lbl in directions:
            self._dof_combo.addItem(lbl, lbl)
        if prev_direction in directions:
            self._dof_combo.setCurrentIndex(directions.index(prev_direction))
            current_direction = prev_direction
        else:
            self._dof_combo.setCurrentIndex(0)
            current_direction = directions[0]
        self._dof_combo.blockSignals(False)

        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        plotted = 0
        n_items = 0
        last_stp_names: Optional[List[str]] = None
        for ci, (case_name, md, stp_names) in enumerate(md_entries):
            if not (0 <= mode_idx < md.num_modes):
                continue
            vals = md.mode_shape(mode_idx, current_direction)
            if vals.size == 0:
                continue
            n_items = max(n_items, vals.size)
            last_stp_names = stp_names if stp_names else last_stp_names
            floors = list(range(vals.size))

            c = colors[ci % len(colors)]
            ax.plot(vals, floors, "o-", color=c, linewidth=1.8,
                    markersize=4, label=case_name)
            plotted += 1

        if plotted == 0:
            self._shape_canvas.show_message("MDFloor.xbn データが空です")
            return

        # Y 軸ラベルを STP 名（あれば）に
        if last_stp_names and len(last_stp_names) >= n_items:
            ax.set_yticks(range(n_items))
            ax.set_yticklabels(last_stp_names[:n_items], fontsize=7)
        else:
            ax.set_yticks(range(n_items))
            ax.set_yticklabels([str(i + 1) for i in range(n_items)], fontsize=7)

        ax.axvline(0, color="#888", linewidth=0.8, linestyle="--")
        ax.set_xlabel(f"振幅  [{current_direction}]")
        ax.set_ylabel("階（下→上）")
        ax.set_title(f"固有モード形状 — モード {mode_idx + 1}  [{current_direction}]")
        ax.grid(True, axis="x", linestyle=":", alpha=0.5)
        ax.invert_yaxis()  # 最上階を上に表示
        if len(md_entries) > 1:
            ax.legend(fontsize=8)
        self._shape_canvas.fig.tight_layout()
        self._shape_canvas.draw()

    def _draw_shape_from_beta(self, ax, mode_idx: int) -> None:
        """MDFloor がない場合、Period.xbn の β 値のみで簡易モード表示する。"""
        # 各ケースの指定モードの β 値を棒グラフで比較
        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        items: List[Tuple[str, Dict[str, float]]] = []
        for case_name, loader in self._entries:
            if not loader.period or not loader.period.modes:
                continue
            m_list = sorted(loader.period.modes, key=lambda m: m.mode_no)
            if mode_idx < len(m_list):
                m = m_list[mode_idx]
                items.append((case_name, m.beta, m.period))

        if not items:
            ax.text(0.5, 0.5, "MDFloor.xbn なし\n（固有値解析でMDFloor出力を有効にしてください）",
                    ha="center", va="center", transform=ax.transAxes,
                    color="gray", fontsize=10)
            ax.set_xticks([])
            ax.set_yticks([])
            self._shape_canvas.draw()
            return

        directions = ["X", "Y", "Z"]
        x = np.arange(len(directions))
        bar_w = 0.7 / max(len(items), 1)
        for ci, (cname, beta, period) in enumerate(items):
            vals = [beta.get(d, 0.0) for d in directions]
            offset = (ci - len(items) / 2 + 0.5) * bar_w
            ax.bar(x + offset, vals, width=bar_w,
                   label=f"{cname} (T={period:.3f}s)",
                   color=colors[ci % len(colors)], alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([f"β_{d}" for d in directions])
        ax.axhline(0, color="#888", linewidth=0.8)
        ax.set_title(
            f"モード {mode_idx + 1}  刺激係数β（MDFloor.xbn 不在のため各方向β値を表示）"
        )
        ax.set_ylabel("刺激係数 β")
        ax.grid(True, axis="y", linestyle=":", alpha=0.5)
        if items:
            ax.legend(fontsize=8)
        self._shape_canvas.fig.tight_layout()
        self._shape_canvas.draw()
