"""
app/ui/sensitivity_widget.py
パラメータ感度分析ウィジェット。

完了済みケースのパラメータと応答値の関係を分析し、
トルネード図（感度バーチャート）と散布図で可視化します。

各パラメータが応答値に与える影響度を把握するための
ツールです。パラメータスイープの結果分析に最適です。

レイアウト:
  ┌───────────────────────────────────────────────────┐
  │ 応答値: [max_drift ▼]                             │
  ├──────────────────────┬──────────────────────────── │
  │ トルネード図          │ 散布図                     │
  │ ▓▓▓ Cd = 0.85        │    *  *                    │
  │ ▓▓  α  = 0.42        │  *    *                    │
  │ ▓   Qy = 0.21        │  *  *                      │
  ├──────────────────────┴────────────────────────────┤
  │ 感度統計テーブル                                   │
  │ パラメータ | 相関係数 | 影響度 | p値              │
  └───────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager, MPL_STYLES

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

# 応答値の定義 (key, 日本語ラベル, 単位)
_RESPONSE_ITEMS = [
    ("max_drift",       "最大層間変形角",     "rad"),
    ("max_acc",         "最大絶対加速度",     "m/s²"),
    ("max_disp",        "最大相対変位",       "m"),
    ("max_vel",         "最大相対速度",       "m/s"),
    ("max_shear",       "せん断力係数",       "—"),
    ("max_otm",         "最大転倒ﾓｰﾒﾝﾄ",    "kN·m"),
]

# トルネード図の色
_COLOR_POSITIVE = "#d62728"   # 増加方向 (赤)
_COLOR_NEGATIVE = "#1f77b4"   # 減少方向 (青)


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


def _compute_correlation(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """ピアソン相関係数と簡易p値を計算。"""
    n = len(x)
    if n < 3:
        return 0.0, 1.0

    x_std = np.std(x)
    y_std = np.std(y)
    if x_std < 1e-15 or y_std < 1e-15:
        return 0.0, 1.0

    r = np.corrcoef(x, y)[0, 1]
    if np.isnan(r):
        return 0.0, 1.0

    # t検定による簡易p値
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t_stat = r * np.sqrt((n - 2) / (1 - r * r))
        # 簡易的にp値を推定（t分布の近似）
        p = 2.0 * np.exp(-0.5 * t_stat * t_stat) if abs(t_stat) < 10 else 0.0

    return float(r), float(p)


class _MplCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(5, 4), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def apply_theme(self) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class SensitivityWidget(QWidget):
    """
    パラメータ感度分析ウィジェット。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットして分析を更新
    refresh()         — 現在のケースで再描画
    update_theme()    — テーマ変更時にグラフ色を更新
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cases: List[AnalysisCase] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        self._cases = cases
        self.refresh()

    def refresh(self) -> None:
        self._analyze_and_draw()

    def update_theme(self) -> None:
        self._tornado_canvas.apply_theme()
        self._scatter_canvas.apply_theme()
        self.refresh()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # コントロール行
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("<b>パラメータ感度分析</b>"))
        ctrl_row.addStretch()

        ctrl_row.addWidget(QLabel("対象応答値:"))
        self._response_combo = QComboBox()
        for _, label, unit in _RESPONSE_ITEMS:
            self._response_combo.addItem(f"{label} [{unit}]")
        self._response_combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._response_combo)

        layout.addLayout(ctrl_row)

        # グラフエリア: トルネード（左） + 散布図（右）
        chart_splitter = QSplitter(Qt.Horizontal)

        # トルネード図
        tornado_widget = QWidget()
        tornado_layout = QVBoxLayout(tornado_widget)
        tornado_layout.setContentsMargins(0, 0, 0, 0)
        tornado_layout.addWidget(QLabel("感度トルネード図（相関係数）"))
        self._tornado_canvas = _MplCanvas(self)
        tornado_layout.addWidget(self._tornado_canvas)
        chart_splitter.addWidget(tornado_widget)

        # 散布図
        scatter_widget = QWidget()
        scatter_layout = QVBoxLayout(scatter_widget)
        scatter_layout.setContentsMargins(0, 0, 0, 0)

        scatter_ctrl = QHBoxLayout()
        scatter_ctrl.addWidget(QLabel("パラメータ:"))
        self._param_combo = QComboBox()
        self._param_combo.currentIndexChanged.connect(self._draw_scatter)
        scatter_ctrl.addWidget(self._param_combo)
        scatter_ctrl.addStretch()
        scatter_layout.addLayout(scatter_ctrl)

        self._scatter_canvas = _MplCanvas(self)
        scatter_layout.addWidget(self._scatter_canvas)
        chart_splitter.addWidget(scatter_widget)

        chart_splitter.setStretchFactor(0, 1)
        chart_splitter.setStretchFactor(1, 1)
        layout.addWidget(chart_splitter, stretch=2)

        # 感度統計テーブル
        layout.addWidget(QLabel("<b>感度統計</b>"))
        self._stat_table = QTableWidget(0, 5)
        self._stat_table.setHorizontalHeaderLabels([
            "パラメータ", "相関係数 r", "影響度 |r|", "方向", "データ点数"
        ])
        self._stat_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._stat_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._stat_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._stat_table.setAlternatingRowColors(True)
        self._stat_table.verticalHeader().setVisible(False)
        self._stat_table.setMaximumHeight(180)
        layout.addWidget(self._stat_table, stretch=0)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _extract_param_response_data(
        self,
    ) -> Tuple[Dict[str, List[float]], List[float], str]:
        """
        完了ケースからパラメータ値と応答値を抽出。

        Returns
        -------
        param_data : dict
            {param_key: [values...]}
        response_data : list
            応答値リスト（param_data と同じ順序）
        response_key : str
            選択中の応答値キー
        """
        idx = self._response_combo.currentIndex()
        response_key = _RESPONSE_ITEMS[idx][0]

        completed = [
            c for c in self._cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]

        if not completed:
            return {}, [], response_key

        # 数値パラメータを全ケースから収集
        all_param_keys: Dict[str, int] = {}
        for case in completed:
            params = {**case.parameters, **(case.damper_params or {})}
            # damper_params が nested dict の場合はフラット化
            flat = {}
            for k, v in params.items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        flat[f"{k}.{k2}"] = v2
                else:
                    flat[k] = v
            for k, v in flat.items():
                try:
                    float(v)
                    all_param_keys[k] = all_param_keys.get(k, 0) + 1
                except (ValueError, TypeError):
                    pass

        # 2ケース以上で出現するパラメータのみ対象
        valid_params = [k for k, cnt in all_param_keys.items() if cnt >= 2]

        param_data: Dict[str, List[float]] = {k: [] for k in valid_params}
        response_data: List[float] = []

        for case in completed:
            resp_val = case.result_summary.get(response_key)
            if resp_val is None:
                continue

            params = {**case.parameters, **(case.damper_params or {})}
            flat = {}
            for k, v in params.items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        flat[f"{k}.{k2}"] = v2
                else:
                    flat[k] = v

            # このケースが全 valid_params に値を持つかチェック
            has_all = True
            row_values = {}
            for pk in valid_params:
                val = flat.get(pk)
                if val is None:
                    has_all = False
                    break
                try:
                    row_values[pk] = float(val)
                except (ValueError, TypeError):
                    has_all = False
                    break

            if has_all:
                response_data.append(resp_val)
                for pk in valid_params:
                    param_data[pk].append(row_values[pk])

        # ばらつきのないパラメータを除外
        filtered_param_data = {}
        for k, vals in param_data.items():
            if len(vals) >= 2 and np.std(vals) > 1e-15:
                filtered_param_data[k] = vals

        return filtered_param_data, response_data, response_key

    def _analyze_and_draw(self) -> None:
        param_data, response_data, response_key = self._extract_param_response_data()

        # パラメータコンボボックスを更新
        current_param = self._param_combo.currentText()
        self._param_combo.blockSignals(True)
        self._param_combo.clear()
        for pk in param_data.keys():
            self._param_combo.addItem(pk)
        # 以前の選択を復元
        idx = self._param_combo.findText(current_param)
        if idx >= 0:
            self._param_combo.setCurrentIndex(idx)
        self._param_combo.blockSignals(False)

        # 相関係数を計算
        correlations: List[Tuple[str, float, float, int]] = []
        y = np.array(response_data)
        for pk, vals in param_data.items():
            x = np.array(vals)
            r, p = _compute_correlation(x, y)
            correlations.append((pk, r, p, len(vals)))

        # |r| でソート
        correlations.sort(key=lambda t: abs(t[1]), reverse=True)

        self._draw_tornado(correlations, response_key)
        self._draw_scatter()
        self._populate_stat_table(correlations)

    # ------------------------------------------------------------------
    # Tornado Chart
    # ------------------------------------------------------------------

    def _draw_tornado(
        self,
        correlations: List[Tuple[str, float, float, int]],
        response_key: str,
    ) -> None:
        ax = self._tornado_canvas.ax
        ax.clear()

        if not correlations:
            ax.text(
                0.5, 0.5,
                "感度分析には\n数値パラメータが異なる\n2ケース以上が必要です",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color="gray",
            )
            self._tornado_canvas.draw()
            return

        # 上位10パラメータを表示
        top_n = min(10, len(correlations))
        top = correlations[:top_n]
        top.reverse()  # 下から大きい順に

        labels = [t[0] for t in top]
        r_values = [t[1] for t in top]

        y_pos = range(len(labels))
        colors = [_COLOR_POSITIVE if r > 0 else _COLOR_NEGATIVE for r in r_values]

        bars = ax.barh(y_pos, r_values, color=colors, edgecolor="none", height=0.6)

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("相関係数 r", fontsize=9)

        # 応答値ラベルを取得
        resp_label = response_key
        for key, label, _ in _RESPONSE_ITEMS:
            if key == response_key:
                resp_label = label
                break
        ax.set_title(f"パラメータ感度 — {resp_label}", fontsize=10)

        # ゼロライン
        ax.axvline(x=0, color="gray", linewidth=0.5)
        ax.set_xlim(-1.1, 1.1)
        ax.grid(axis="x", linestyle="--", alpha=0.3)
        ax.tick_params(labelsize=8)

        # バーにr値を表示
        for bar, r in zip(bars, r_values):
            x_pos = bar.get_width()
            ha = "left" if r >= 0 else "right"
            offset = 0.02 if r >= 0 else -0.02
            ax.text(
                x_pos + offset, bar.get_y() + bar.get_height() / 2,
                f"{r:.3f}", va="center", ha=ha, fontsize=7,
            )

        # 凡例
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor=_COLOR_POSITIVE, label="正の相関（増加）"),
            Patch(facecolor=_COLOR_NEGATIVE, label="負の相関（減少）"),
        ]
        ax.legend(handles=legend_elements, fontsize=7, loc="lower right")

        self._tornado_canvas.fig.tight_layout()
        self._tornado_canvas.draw()

    # ------------------------------------------------------------------
    # Scatter Plot
    # ------------------------------------------------------------------

    def _draw_scatter(self) -> None:
        ax = self._scatter_canvas.ax
        ax.clear()

        param_key = self._param_combo.currentText()
        if not param_key:
            ax.text(
                0.5, 0.5, "パラメータを選択してください",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color="gray",
            )
            self._scatter_canvas.draw()
            return

        param_data, response_data, response_key = self._extract_param_response_data()

        if param_key not in param_data or not response_data:
            ax.text(
                0.5, 0.5, "データなし",
                ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color="gray",
            )
            self._scatter_canvas.draw()
            return

        x = np.array(param_data[param_key])
        y = np.array(response_data)

        ax.scatter(x, y, c="#1f77b4", s=30, alpha=0.7, edgecolor="white", linewidth=0.5)

        # 回帰直線
        if len(x) >= 2 and np.std(x) > 1e-15:
            coeffs = np.polyfit(x, y, 1)
            x_line = np.linspace(x.min(), x.max(), 50)
            y_line = np.polyval(coeffs, x_line)
            ax.plot(x_line, y_line, color="#d62728", linewidth=1.5,
                    linestyle="--", alpha=0.7, label=f"y = {coeffs[0]:.4g}x + {coeffs[1]:.4g}")
            ax.legend(fontsize=7)

        resp_label = response_key
        for key, label, unit in _RESPONSE_ITEMS:
            if key == response_key:
                resp_label = f"{label} [{unit}]"
                break

        ax.set_xlabel(param_key, fontsize=9)
        ax.set_ylabel(resp_label, fontsize=9)
        ax.set_title(f"{param_key} vs {resp_label.split('[')[0].strip()}", fontsize=10)
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.3)

        self._scatter_canvas.fig.tight_layout()
        self._scatter_canvas.draw()

    # ------------------------------------------------------------------
    # Statistics Table
    # ------------------------------------------------------------------

    def _populate_stat_table(
        self,
        correlations: List[Tuple[str, float, float, int]],
    ) -> None:
        self._stat_table.setRowCount(0)

        for pk, r, p, n in correlations:
            row = self._stat_table.rowCount()
            self._stat_table.insertRow(row)

            # パラメータ名
            self._stat_table.setItem(row, 0, QTableWidgetItem(pk))

            # 相関係数
            r_item = QTableWidgetItem(f"{r:.4f}")
            r_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._stat_table.setItem(row, 1, r_item)

            # 影響度
            abs_item = QTableWidgetItem(f"{abs(r):.4f}")
            abs_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._stat_table.setItem(row, 2, abs_item)

            # 方向
            if abs(r) < 0.1:
                direction = "影響小"
            elif r > 0:
                direction = "正（増加）"
            else:
                direction = "負（減少）"
            dir_item = QTableWidgetItem(direction)
            dir_item.setTextAlignment(Qt.AlignCenter)
            self._stat_table.setItem(row, 3, dir_item)

            # データ点数
            n_item = QTableWidgetItem(str(n))
            n_item.setTextAlignment(Qt.AlignCenter)
            self._stat_table.setItem(row, 4, n_item)
