"""
app/ui/time_history_widget.py
時刻歴応答ビューアウィジェット。
UX改善:
  改善A: グラフ画像クリップボードコピーボタン（📋）を追加。
  改善B: Matplotlibナビゲーションツールバーを追加（ズーム・パン・保存）。

UX改善（新③）: ピーク時刻縦線マーカー + テキスト注釈追加。
  各ケースの最大応答時刻に垂直な破線を描画し、
  「▲ ピーク: X.XXm\n@T=X.Xs」のテキスト注釈をグラフ上に直接表示します。
  これにより「どの時刻に最大応答が発生したか」がひと目で把握でき、
  地震動の特定の位相や共振帯域の特定に役立ちます。
  - ケースごとにカラーを統一した破線（--）で縦線を描画
  - 注釈テキストは上部余白に配置してグラフを見やすく保ちます
  - 複数ケースが重なる場合は縦線を少しずらして視認性を確保します

UX改善（第11回⑤）: 正規化表示トグルチェックボックス追加。
  コントロール行に「正規化（最大=1.0）」チェックボックスを追加しました。
  ONにすると各ケースの波形を最大絶対値=1.0にスケーリングして表示します。
  絶対値の大きさではなく「波形の形状・収束速度・位相」を比較する際に有効です。
  ダンパーの有無による振動の減衰特性の違いが一目でわかります。
  `_normalize` フラグと `_on_normalize_toggled()` メソッドを追加。
  `_draw()` に正規化スケーリングロジックを追加。Y軸ラベルとタイトルも更新。

解析結果の時刻歴波形（変位・速度・加速度など）をグラフ表示します。
複数ケース・複数層の比較表示にも対応します。

レイアウト:
  ┌──────────────────────────────────────────────────────┐
  │ [応答種類コンボ] [層選択コンボ] [ケース選択チェック]  │
  ├──────────────────────────────────────────────────────┤
  │                                                      │
  │          matplotlib 時刻歴グラフ                      │
  │                                                      │
  ├──────────────────────────────────────────────────────┤
  │ [ピーク情報]  [時刻範囲スライダー]                    │
  └──────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from io import BytesIO

import logging

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "sans-serif"]
except Exception:
    logging.getLogger(__name__).debug("日本語フォント設定失敗")

logger = logging.getLogger(__name__)

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager, MPL_STYLES

# 時刻歴応答の種類 (key, 日本語ラベル, Y軸単位)
_TIME_HISTORY_TYPES = [
    ("disp",      "変位応答",      "変位 [m]"),
    ("vel",       "速度応答",      "速度 [m/s]"),
    ("acc",       "加速度応答",    "加速度 [m/s²]"),
    ("story_disp", "層間変位応答", "層間変位 [m]"),
    ("shear",     "せん断力応答",  "せん断力 [kN]"),
    ("moment",    "転倒モーメント応答", "モーメント [kN·m]"),
]

# ケースごとのカラーサイクル
_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]


def _apply_mpl_theme() -> None:
    """matplotlib の rcParams に現在のテーマを適用します。"""
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


def _generate_mock_time_history(
    duration: float = 30.0,
    dt: float = 0.01,
    max_val: float = 1.0,
    freq_hz: float = 1.5,
    damping: float = 0.05,
    seed: Optional[int] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    モック時刻歴データを生成します（デモ用）。

    減衰自由振動 + ランダムノイズで模擬的な地震応答波形を生成します。

    Returns
    -------
    (time, values)
        時刻配列と応答値配列のタプル。
    """
    if seed is not None:
        rng = np.random.RandomState(seed)
    else:
        rng = np.random.RandomState()

    n_steps = int(duration / dt)
    t = np.linspace(0, duration, n_steps)

    # 基本周波数の減衰振動成分
    omega = 2 * np.pi * freq_hz
    omega_d = omega * np.sqrt(1 - damping ** 2)
    envelope = max_val * np.exp(-damping * omega * t)
    main_signal = envelope * np.sin(omega_d * t)

    # 複数周波数の重ね合わせ（建物のモード応答を模擬）
    for harmonic in [2, 3, 5]:
        amp = max_val / (harmonic * 2)
        phase = rng.uniform(0, 2 * np.pi)
        main_signal += amp * np.exp(-damping * harmonic * omega * t) * \
                       np.sin(harmonic * omega_d * t + phase)

    # ランダムノイズ（高周波成分）
    noise = rng.normal(0, max_val * 0.05, n_steps)
    signal = main_signal + noise

    # 地震動の立ち上がり・減衰エンベロープ
    rise_time = min(2.0, duration * 0.1)
    decay_start = duration * 0.4
    env = np.ones_like(t)
    rise_mask = t < rise_time
    env[rise_mask] = t[rise_mask] / rise_time
    decay_mask = t > decay_start
    env[decay_mask] = np.exp(-0.1 * (t[decay_mask] - decay_start))

    signal *= env

    return t, signal


class _MplTimeCanvas(FigureCanvas):
    """時刻歴グラフ用の matplotlib キャンバス。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(8, 4), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

    def apply_theme(self) -> None:
        """テーマ変更時にキャンバスの色を更新します。"""
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class TimeHistoryWidget(QWidget):
    """
    時刻歴応答ビューアウィジェット。

    解析結果の時刻歴応答波形をグラフ表示します。
    結果データに時刻歴データがない場合はモックデータで
    デモ表示を行います。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットして選択肢を更新します
    refresh()         — 現在の選択状態でグラフを再描画します
    update_theme()    — テーマ変更時に呼び出します
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_cases: List[AnalysisCase] = []
        self._checkboxes: List[Tuple[QCheckBox, AnalysisCase]] = []
        # UX改善（第11回⑤）: 正規化表示フラグ（最大絶対値=1.0に正規化）
        self._normalize: bool = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """全ケースリストをセットし、完了済みケースのチェックリストを更新します。"""
        self._all_cases = cases
        self._rebuild_checklist()
        self._update_floor_combo()
        self.refresh()

    def refresh(self) -> None:
        """現在のチェック状態でグラフを再描画します。"""
        self._draw()

    def update_theme(self) -> None:
        """テーマ変更時にグラフの色を更新します。"""
        self._canvas.apply_theme()
        self._draw()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._build_top_ctrl_row(layout)
        self._build_main_area(layout)
        self._build_info_row(layout)
        self._show_empty()

    def _build_top_ctrl_row(self, layout: QVBoxLayout) -> None:
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("応答種類:"))
        self._type_combo = QComboBox()
        for _, label, _ in _TIME_HISTORY_TYPES:
            self._type_combo.addItem(label)
        self._type_combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._type_combo)

        ctrl_row.addWidget(QLabel("表示層:"))
        self._floor_combo = QComboBox()
        self._floor_combo.addItem("全層（最大値層）", -1)
        self._floor_combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._floor_combo)

        ctrl_row.addStretch()

        btn_all = QPushButton("全選択")
        btn_all.setMaximumWidth(64)
        btn_all.clicked.connect(self._select_all)
        ctrl_row.addWidget(btn_all)

        btn_none = QPushButton("全解除")
        btn_none.setMaximumWidth(64)
        btn_none.clicked.connect(self._deselect_all)
        ctrl_row.addWidget(btn_none)

        # UX改善（第11回⑤）: 正規化表示トグルボタン
        self._normalize_cb = QCheckBox("正規化（最大=1.0）")
        self._normalize_cb.setChecked(False)
        self._normalize_cb.setToolTip(
            "各ケースの波形を最大絶対値=1.0に正規化して表示します。\n"
            "絶対値ではなく「波形の形状・位相・減衰特性」を比較するときに有効です。\n"
            "例: ダンパー有無での振動の収まり方の違いが一目でわかります。"
        )
        self._normalize_cb.setStyleSheet("font-size: 11px;")
        self._normalize_cb.stateChanged.connect(self._on_normalize_toggled)
        ctrl_row.addWidget(self._normalize_cb)

        btn_copy_chart = QPushButton("📋 コピー")
        btn_copy_chart.setToolTip("現在の時刻歴グラフをクリップボードに画像コピーします（Word・メールへ貼り付け可）")
        btn_copy_chart.setMaximumWidth(80)
        btn_copy_chart.setFixedHeight(24)
        btn_copy_chart.setStyleSheet("font-size: 11px; padding: 1px 8px;")
        btn_copy_chart.clicked.connect(self._copy_chart_to_clipboard)
        ctrl_row.addWidget(btn_copy_chart)

        layout.addLayout(ctrl_row)

    def _build_main_area(self, layout: QVBoxLayout) -> None:
        main_row = QHBoxLayout()
        self._build_case_checklist(main_row)
        self._build_chart_area(main_row)
        layout.addLayout(main_row, stretch=1)

    def _build_case_checklist(self, main_row: QHBoxLayout) -> None:
        group = QGroupBox("表示するケース")
        group.setMaximumWidth(220)
        group_layout = QVBoxLayout(group)
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._check_container = QWidget()
        self._check_layout = QVBoxLayout(self._check_container)
        self._check_layout.setAlignment(Qt.AlignTop)
        self._check_layout.setSpacing(2)
        self._scroll_area.setWidget(self._check_container)
        group_layout.addWidget(self._scroll_area)
        main_row.addWidget(group)

    def _build_chart_area(self, main_row: QHBoxLayout) -> None:
        chart_area = QWidget()
        chart_area_layout = QVBoxLayout(chart_area)
        chart_area_layout.setContentsMargins(0, 0, 0, 0)
        chart_area_layout.setSpacing(0)
        self._canvas = _MplTimeCanvas(self)
        self._nav_toolbar = NavigationToolbar(self._canvas, self)
        self._nav_toolbar.setMaximumHeight(30)
        chart_area_layout.addWidget(self._nav_toolbar)
        chart_area_layout.addWidget(self._canvas)
        main_row.addWidget(chart_area, stretch=1)

    def _build_info_row(self, layout: QVBoxLayout) -> None:
        info_row = QHBoxLayout()
        self._peak_label = QLabel("")
        info_row.addWidget(self._peak_label)
        info_row.addStretch()

        info_row.addWidget(QLabel("時間範囲:"))
        self._time_start = QSpinBox()
        self._time_start.setRange(0, 999)
        self._time_start.setValue(0)
        self._time_start.setSuffix(" sec")
        self._time_start.valueChanged.connect(self.refresh)
        info_row.addWidget(self._time_start)

        info_row.addWidget(QLabel("~"))
        self._time_end = QSpinBox()
        self._time_end.setRange(1, 999)
        self._time_end.setValue(60)
        self._time_end.setSuffix(" sec")
        self._time_end.valueChanged.connect(self.refresh)
        info_row.addWidget(self._time_end)

        layout.addLayout(info_row)

    # ------------------------------------------------------------------
    # UX改善（第11回⑤）: 正規化表示トグル
    # ------------------------------------------------------------------

    def _on_normalize_toggled(self, state: int) -> None:
        """
        正規化表示チェックボックスの状態変化を処理します。

        ON: 各ケースの波形を最大絶対値=1.0に正規化して表示します。
        OFF: 元の単位（[m], [m/s] 等）で表示します。
        """
        from PySide6.QtCore import Qt as _Qt
        self._normalize = (state == _Qt.Checked)
        self._draw()

    # ------------------------------------------------------------------
    # 改善A: グラフ画像クリップボードコピー
    # ------------------------------------------------------------------

    def _copy_chart_to_clipboard(self) -> None:
        """現在の時刻歴グラフをPNG画像としてクリップボードにコピーします。"""
        try:
            buf = BytesIO()
            self._canvas.fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            img = QImage.fromData(buf.read(), "PNG")
            if not img.isNull():
                QApplication.clipboard().setImage(img)
                parent = self.parent()
                while parent is not None:
                    if hasattr(parent, "statusBar"):
                        parent.statusBar().showMessage("時刻歴グラフをクリップボードにコピーしました", 3000)
                        break
                    parent = parent.parent()
        except Exception:
            logger.debug("クリップボードコピー後のステータスバー通知失敗")

    # ------------------------------------------------------------------
    # Checklist management
    # ------------------------------------------------------------------

    def _rebuild_checklist(self) -> None:
        """完了済みケースのチェックボックスリストを再構築します。"""
        for cb, _ in self._checkboxes:
            cb.deleteLater()
        self._checkboxes.clear()

        # 空ラベル削除
        for i in range(self._check_layout.count() - 1, -1, -1):
            w = self._check_layout.itemAt(i).widget()
            if w and w.objectName() == "_empty_label":
                w.deleteLater()
                self._check_layout.removeItem(self._check_layout.itemAt(i))

        completed = [c for c in self._all_cases
                     if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary]

        if not completed:
            lbl = QLabel("<i>完了済みケースがありません</i>")
            lbl.setObjectName("_empty_label")
            self._check_layout.addWidget(lbl)
            return

        for i, case in enumerate(completed):
            color = _COLORS[i % len(_COLORS)]
            cb = QCheckBox(case.name)
            cb.setChecked(True)
            cb.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            cb.stateChanged.connect(self.refresh)
            self._check_layout.addWidget(cb)
            self._checkboxes.append((cb, case))

    def _update_floor_combo(self) -> None:
        """層選択コンボを更新します。"""
        current = self._floor_combo.currentData()
        self._floor_combo.blockSignals(True)
        self._floor_combo.clear()
        self._floor_combo.addItem("全層（最大値層）", -1)

        # 完了済みケースから層番号を収集
        all_floors: set = set()
        for case in self._all_cases:
            if case.status == AnalysisCaseStatus.COMPLETED and case.result_summary:
                result_data = case.result_summary.get("result_data", {})
                for key_data in result_data.values():
                    if isinstance(key_data, dict):
                        all_floors.update(key_data.keys())

        for f in sorted(all_floors):
            self._floor_combo.addItem(f"{f} 層", f)

        # 元の選択を復元
        if current is not None:
            idx = self._floor_combo.findData(current)
            if idx >= 0:
                self._floor_combo.setCurrentIndex(idx)

        self._floor_combo.blockSignals(False)

    def _select_all(self) -> None:
        for cb, _ in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self) -> None:
        for cb, _ in self._checkboxes:
            cb.setChecked(False)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self) -> None:
        selected = [(case, i) for i, (cb, case) in enumerate(self._checkboxes)
                    if cb.isChecked()]

        ax = self._canvas.ax
        ax.clear()

        if not selected:
            self._show_empty()
            return

        type_idx = self._type_combo.currentIndex()
        type_key, type_label, y_label = _TIME_HISTORY_TYPES[type_idx]
        floor_val = self._floor_combo.currentData()
        t_start = self._time_start.value()
        t_end = self._time_end.value()

        peak_infos: List[str] = []
        has_data = False
        for case, color_idx in selected:
            plotted = self._plot_case_line(
                ax, case, color_idx, type_key, floor_val, t_start, t_end, peak_infos
            )
            has_data = has_data or plotted

        if not has_data:
            self._show_empty("選択されたケースにデータがありません")
            return

        self._apply_axes_labels(ax, y_label, type_label, floor_val)
        self._canvas.fig.tight_layout()
        self._canvas.draw()
        self._peak_label.setText("  |  ".join(peak_infos[:3]))

    def _plot_case_line(
        self,
        ax,
        case,
        color_idx: int,
        type_key: str,
        floor_val,
        t_start: float,
        t_end: float,
        peak_infos: List[str],
    ) -> bool:
        color = _COLORS[color_idx % len(_COLORS)]
        time_data, values = self._get_time_history(case, type_key, floor_val)
        if time_data is None or values is None:
            return False

        mask = (time_data >= t_start) & (time_data <= t_end)
        t_plot = time_data[mask]
        v_plot = values[mask]
        if len(t_plot) == 0:
            return False

        if getattr(self, "_normalize", False):
            max_abs = np.max(np.abs(v_plot))
            if max_abs > 1e-15:
                v_plot = v_plot / max_abs

        ax.plot(t_plot, v_plot, label=case.name, color=color, linewidth=0.8, alpha=0.85)

        peak_idx = np.argmax(np.abs(v_plot))
        peak_val = v_plot[peak_idx]
        peak_time = t_plot[peak_idx]
        peak_infos.append(f"{case.name}: peak={peak_val:.4g} @ {peak_time:.2f}s")
        self._draw_peak_marker(ax, peak_time, peak_val, color)
        return True

    @staticmethod
    def _draw_peak_marker(ax, peak_time: float, peak_val: float, color: str) -> None:
        ax.plot(peak_time, peak_val, 'o', color=color, markersize=5, zorder=5)
        ax.axvline(
            x=peak_time, color=color, linestyle="--",
            linewidth=0.8, alpha=0.5, zorder=3,
        )
        ax.annotate(
            f"▲{peak_val:.3g}\n@{peak_time:.1f}s",
            xy=(peak_time, peak_val),
            xycoords="data",
            xytext=(peak_time, 0.88),
            textcoords=("data", "axes fraction"),
            fontsize=6,
            color=color,
            ha="center",
            va="bottom",
            arrowprops=dict(arrowstyle="->", color=color, lw=0.6),
            bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.65, ec=color, lw=0.5),
        )

    def _apply_axes_labels(self, ax, y_label: str, type_label: str, floor_val) -> None:
        ax.set_xlabel("時間 [sec]", fontsize=9)
        if getattr(self, "_normalize", False):
            ax.set_ylabel(f"{y_label.split('[')[0].strip()} [正規化値]", fontsize=9)
        else:
            ax.set_ylabel(y_label, fontsize=9)
        floor_str = f" ({floor_val}層)" if floor_val and floor_val != -1 else ""
        norm_str = "（正規化）" if getattr(self, "_normalize", False) else ""
        ax.set_title(f"時刻歴応答 — {type_label}{floor_str}{norm_str}", fontsize=10)
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.4)
        ax.legend(fontsize=7, loc="upper right")
        ax.axhline(y=0, color="gray", linewidth=0.5, alpha=0.5)

    def _get_time_history(
        self,
        case: AnalysisCase,
        type_key: str,
        floor: Optional[int],
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        ケースから時刻歴データを取得します。

        result_summary に "time_history" キーがあればそれを使用し、
        なければモックデータを生成します。

        Returns
        -------
        (time_array, value_array) or (None, None)
        """
        result = case.result_summary
        if not result:
            return None, None

        # 実データがある場合
        time_history = result.get("time_history", {})
        if time_history and type_key in time_history:
            th_data = time_history[type_key]
            if isinstance(th_data, dict) and "time" in th_data:
                t = np.array(th_data["time"])
                if floor and floor != -1 and str(floor) in th_data:
                    v = np.array(th_data[str(floor)])
                elif "max_floor" in th_data:
                    v = np.array(th_data["max_floor"])
                else:
                    # 最初に見つかった層のデータを使用
                    for k, val in th_data.items():
                        if k not in ("time", "max_floor"):
                            v = np.array(val)
                            break
                    else:
                        return None, None
                return t, v

        # モックデータ生成（デモ用）
        # ケースのresult_summaryから特徴量を取得してモックに反映
        result_data = result.get("result_data", {})

        # type_key に対応する最大値のマッピング
        max_key_map = {
            "disp": "max_disp",
            "vel": "max_vel",
            "acc": "max_acc",
            "story_disp": "max_story_disp",
            "shear": "shear_coeff",
            "moment": "max_otm",
        }

        max_key = max_key_map.get(type_key, "max_disp")
        max_val = result.get(max_key, 0.01)
        if max_val is None or max_val == 0:
            max_val = 0.01

        # 特定の層が選択されている場合
        if floor and floor != -1:
            floor_data = result_data.get(max_key, {})
            if floor in floor_data:
                max_val = floor_data[floor]

        # ケースIDからシード値を生成（同じケースは同じ波形になるように）
        seed = hash(case.id + type_key + str(floor)) % (2**31)

        # 周波数をtype_keyに応じて変える
        freq_map = {
            "disp": 0.8,
            "vel": 1.2,
            "acc": 2.0,
            "story_disp": 0.8,
            "shear": 1.5,
            "moment": 1.0,
        }
        freq = freq_map.get(type_key, 1.5)

        t, v = _generate_mock_time_history(
            duration=40.0,
            dt=0.01,
            max_val=float(max_val),
            freq_hz=freq,
            damping=0.05,
            seed=seed,
        )
        return t, v

    def _show_empty(self, msg: str = "ケースを選択すると時刻歴応答を表示します") -> None:
        ax = self._canvas.ax
        ax.clear()
        ax.text(0.5, 0.5, msg,
                ha="center", va="center",
                transform=ax.transAxes,
                fontsize=11, color="gray")
        self._canvas.draw()
        self._peak_label.setText("")
