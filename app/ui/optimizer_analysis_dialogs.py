"""
app/ui/optimizer_analysis_dialogs.py
最適化結果の分析・可視化ダイアログ群。

optimizer_dialog.py から分離された分析専用ダイアログ:
- ComparisonDialog: 複数結果の比較
- SensitivityDialog: パラメータ感度解析
- ParetoDialog: Pareto front 可視化
- CorrelationDialog: パラメータ相関分析
- DiagnosticsDialog: 収束品質診断
- SobolDialog: Sobol グローバル感度解析
- HeatmapDialog: パラメータ空間ヒートマップ
"""

from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import os, matplotlib
if not os.environ.get("MPLBACKEND"):
    matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from app.services.optimizer import (
    ConvergenceDiagnostics,
    CorrelationResult,
    OptimizationResult,
    SensitivityResult,
    SobolResult,
)
from .theme import ThemeManager, MPL_STYLES

import logging
logger = logging.getLogger(__name__)


def _apply_mpl_theme() -> None:
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


# _OBJECTIVE_ITEMS は OptimizerDialog 側で定義されているが、
# ParetoDialog でも参照するため、ここにもコピーを持つ。
_OBJECTIVE_ITEMS = [
    ("max_drift",       "最大層間変形角",     "rad"),
    ("max_acc",         "最大絶対加速度",     "m/s²"),
    ("max_disp",        "最大相対変位",       "m"),
    ("max_vel",         "最大相対速度",       "m/s"),
    ("max_story_disp",  "最大層間変形",       "m"),
    ("shear_coeff",     "せん断力係数",       "—"),
    ("max_otm",         "最大転倒モーメント", "kN·m"),
    ("peak_gain_db",    "伝達関数1次ピーク", "dB"),
]


class ComparisonDialog(QDialog):
    """複数の最適化結果を比較するダイアログ。

    保存済みのJSONファイルを複数読み込み、最良解のパラメータ・目的関数値・
    計算時間などを一覧表示します。収束曲線のオーバーレイプロットも表示します。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("最適化結果の比較")
        self.setMinimumSize(800, 500)
        self._results: List[tuple] = []  # [(label, OptimizationResult), ...]
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ファイル追加ボタン
        top_row = QHBoxLayout()
        add_btn = QPushButton("結果ファイルを追加...")
        add_btn.clicked.connect(self._add_result_file)
        top_row.addWidget(add_btn)
        clear_btn = QPushButton("全クリア")
        clear_btn.clicked.connect(self._clear_all)
        top_row.addWidget(clear_btn)
        top_row.addStretch()
        layout.addLayout(top_row)

        # 比較テーブル
        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table, stretch=1)

        # 収束曲線オーバーレイ
        theme = "dark" if ThemeManager.is_dark() else "light"
        bg = MPL_STYLES[theme]["figure.facecolor"]
        ax_bg = MPL_STYLES[theme]["axes.facecolor"]
        self._conv_fig = Figure(figsize=(8, 3), tight_layout=True, facecolor=bg)
        self._conv_ax = self._conv_fig.add_subplot(111, facecolor=ax_bg)
        self._conv_canvas = FigureCanvas(self._conv_fig)
        layout.addWidget(self._conv_canvas, stretch=1)

        # 閉じるボタン
        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _add_result_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "比較する結果ファイルを選択", "",
            "JSON Files (*.json);;All Files (*)",
        )
        for path in paths:
            try:
                result = OptimizationResult.load_json(path)
                import os
                label = os.path.basename(path)
                self._results.append((label, result))
            except Exception as e:
                logger.warning("比較ファイル読込失敗: %s: %s", path, e)

        if self._results:
            self._refresh_table()
            self._refresh_convergence()

    def _clear_all(self) -> None:
        self._results.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        self._conv_ax.clear()
        self._conv_canvas.draw()

    def _refresh_table(self) -> None:
        headers = [
            "ファイル", "手法", "ダンパー種類", "評価方式",
            "評価数", "制約満足", "最良目的関数値",
            "計算時間(秒)",
        ]
        # パラメータ列を動的に追加
        all_param_keys: list[str] = []
        for _, r in self._results:
            if r.best:
                for k in r.best.params:
                    if k not in all_param_keys:
                        all_param_keys.append(k)
        headers += [f"Best:{k}" for k in all_param_keys]

        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(self._results))

        for row, (label, r) in enumerate(self._results):
            self._table.setItem(row, 0, QTableWidgetItem(label))
            method = r.config.method if r.config else "?"
            self._table.setItem(row, 1, QTableWidgetItem(method))
            dtype = r.config.damper_type if r.config else ""
            self._table.setItem(row, 2, QTableWidgetItem(dtype))
            self._table.setItem(row, 3, QTableWidgetItem(r.evaluation_method))
            self._table.setItem(row, 4, QTableWidgetItem(str(len(r.all_candidates))))
            self._table.setItem(row, 5, QTableWidgetItem(str(len(r.feasible_candidates))))

            if r.best:
                item = QTableWidgetItem(f"{r.best.objective_value:.6g}")
                self._table.setItem(row, 6, item)
            else:
                self._table.setItem(row, 6, QTableWidgetItem("N/A"))

            self._table.setItem(row, 7, QTableWidgetItem(f"{r.elapsed_sec:.1f}"))

            # パラメータ値
            for col_idx, pk in enumerate(all_param_keys):
                if r.best and pk in r.best.params:
                    val = r.best.params[pk]
                    self._table.setItem(row, 8 + col_idx, QTableWidgetItem(f"{val:.6g}"))

        self._table.resizeColumnsToContents()

    def _refresh_convergence(self) -> None:
        ax = self._conv_ax
        ax.clear()

        colors = ["#1565c0", "#e65100", "#2e7d32", "#6a1b9a", "#c62828", "#00838f"]
        for idx, (label, r) in enumerate(self._results):
            feasible_history = []
            best_so_far = float("inf")
            for c in r.all_candidates:
                if c.is_feasible:
                    best_so_far = min(best_so_far, c.objective_value)
                    feasible_history.append(best_so_far)
            if feasible_history:
                color = colors[idx % len(colors)]
                ax.plot(
                    range(1, len(feasible_history) + 1),
                    feasible_history,
                    color=color,
                    label=label,
                    linewidth=1.5,
                )

        if self._results:
            ax.set_xlabel("制約満足候補の累積数")
            ax.set_ylabel("累積最良値")
            ax.set_title("収束曲線の比較")
            ax.legend(fontsize=8, loc="upper right")
            ax.grid(True, alpha=0.3)

        self._conv_canvas.draw()


class SensitivityDialog(QDialog):
    """パラメータ感度解析結果を表示するダイアログ。

    - トルネードチャート: パラメータ別の感度指標ランキング
    - 感度曲線: 各パラメータの変動に対する目的関数の変化
    """

    def __init__(
        self,
        result: SensitivityResult,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("パラメータ感度解析")
        self.setMinimumSize(700, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ヘッダ
        header = QLabel(
            f"目的関数: {self._result.objective_label or self._result.objective_key}  |  "
            f"基準値: {self._result.base_objective:.6g}"
        )
        header.setStyleSheet("font-weight: bold; font-size: 13px; padding: 4px;")
        layout.addWidget(header)

        # 上下分割: トルネードチャート + 感度曲線
        splitter = QSplitter(Qt.Vertical)

        # トルネードチャート
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        ax_face = MPL_STYLES[theme]["axes.facecolor"]

        self._tornado_fig = Figure(figsize=(6, 3), tight_layout=True, facecolor=facecolor)
        self._tornado_ax = self._tornado_fig.add_subplot(111)
        self._tornado_ax.set_facecolor(ax_face)
        tornado_canvas = FigureCanvas(self._tornado_fig)
        splitter.addWidget(tornado_canvas)

        # 感度曲線
        self._curve_fig = Figure(figsize=(6, 3), tight_layout=True, facecolor=facecolor)
        self._curve_ax = self._curve_fig.add_subplot(111)
        self._curve_ax.set_facecolor(ax_face)
        curve_canvas = FigureCanvas(self._curve_fig)
        splitter.addWidget(curve_canvas)

        layout.addWidget(splitter, stretch=1)

        # ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._draw_tornado()
        self._draw_curves()

    def _draw_tornado(self) -> None:
        """トルネードチャート（感度指標の水平棒グラフ）を描画します。"""
        ax = self._tornado_ax
        ax.clear()

        ranked = self._result.ranked_entries
        if not ranked:
            ax.text(0.5, 0.5, "感度データなし", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11, color="gray")
            return

        labels = [e.label for e in ranked]
        values = [e.sensitivity_index * 100 for e in ranked]  # %表示

        y_pos = np.arange(len(labels))
        colors = []
        for v in values:
            if v >= 10:
                colors.append("#e74c3c")  # 高感度: 赤
            elif v >= 5:
                colors.append("#f39c12")  # 中感度: 橙
            else:
                colors.append("#3498db")  # 低感度: 青

        bars = ax.barh(y_pos, values, color=colors, height=0.6, edgecolor="none")
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels)
        ax.set_xlabel("感度指標 [%]")
        ax.set_title("パラメータ感度ランキング", fontsize=11)
        ax.invert_yaxis()

        # 値ラベル
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}%", va="center", fontsize=9,
            )

        try:
            self._tornado_fig.tight_layout()
        except (MemoryError, ValueError):
            logger.debug("tight_layout失敗")

    def _draw_curves(self) -> None:
        """各パラメータの感度曲線（変動率 vs 目的関数値）を描画します。"""
        ax = self._curve_ax
        ax.clear()

        entries = self._result.ranked_entries
        if not entries:
            ax.text(0.5, 0.5, "感度データなし", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11, color="gray")
            return

        colors_cycle = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
                        "#1abc9c", "#e67e22", "#34495e"]
        base_obj = self._result.base_objective

        for i, entry in enumerate(entries):
            if not entry.variations or not entry.objective_values:
                continue
            color = colors_cycle[i % len(colors_cycle)]
            pct_vals = [v * 100 for v in entry.variations]

            # 正規化: ベース値からの変化率
            if base_obj != 0:
                norm_obj = [(o / base_obj - 1.0) * 100 for o in entry.objective_values]
            else:
                norm_obj = entry.objective_values

            ax.plot(pct_vals, norm_obj, "o-", color=color, label=entry.label,
                    markersize=4, linewidth=1.5)

        ax.axhline(0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.axvline(0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.set_xlabel("パラメータ変動率 [%]")
        ax.set_ylabel("目的関数変化率 [%]")
        ax.set_title("パラメータ感度曲線", fontsize=11)
        ax.legend(fontsize=8, loc="best")

        try:
            self._curve_fig.tight_layout()
        except (MemoryError, ValueError):
            logger.debug("tight_layout失敗")


class ParetoDialog(QDialog):
    """複合目的関数使用時のPareto front（トレードオフ曲線）可視化ダイアログ。

    各候補の個別目的関数値を2D散布図で表示し、
    制約満足/不満足を色分けして最良解を強調表示する。
    2目的の場合は直接散布図、3目的以上はペアプロットを表示。
    """

    def __init__(
        self,
        result: OptimizationResult,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("Pareto Front — 目的関数トレードオフ")
        self.setMinimumSize(750, 550)

        layout = QVBoxLayout(self)

        # 目的関数のキーと重みを取得
        config = result.config
        if not config or not config.objective_weights:
            layout.addWidget(QLabel("複合目的関数が使用されていません。"))
            return

        self._obj_keys: List[str] = list(config.objective_weights.keys())
        self._obj_labels: Dict[str, str] = {}
        for key, label, _ in _OBJECTIVE_ITEMS:
            if key in config.objective_weights:
                self._obj_labels[key] = label
        # フォールバック
        for key in self._obj_keys:
            if key not in self._obj_labels:
                self._obj_labels[key] = key

        n_obj = len(self._obj_keys)
        if n_obj < 2:
            layout.addWidget(QLabel("トレードオフ表示には2つ以上の目的関数が必要です。"))
            return

        # 説明ラベル
        weight_strs = [
            f"{self._obj_labels[k]}(w={config.objective_weights[k]:.2g})"
            for k in self._obj_keys
        ]
        desc = QLabel(f"目的関数: {' × '.join(weight_strs)}")
        desc.setStyleSheet("font-size: 11px; color: gray; padding: 4px;")
        layout.addWidget(desc)

        # チャートの構築
        if n_obj == 2:
            n_rows, n_cols = 1, 1
        else:
            # ペアプロット: C(n,2) 個のサブプロット
            n_pairs = n_obj * (n_obj - 1) // 2
            n_cols = min(n_pairs, 3)
            n_rows = (n_pairs + n_cols - 1) // n_cols

        is_dark = ThemeManager.is_dark()
        bg = "#2b2b2b" if is_dark else "#ffffff"
        fg = "#cccccc" if is_dark else "#333333"

        fig = Figure(figsize=(5 * n_cols, 4 * n_rows), facecolor=bg)
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)

        self._draw_pareto(fig, is_dark, bg, fg)

        fig.tight_layout(pad=2.0)
        canvas.draw()

        # 閉じるボタン
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.reject)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _draw_pareto(
        self, fig: Figure, is_dark: bool, bg: str, fg: str,
    ) -> None:
        """散布図を描画する。"""
        config = self._result.config
        keys = self._obj_keys
        n_obj = len(keys)
        candidates = self._result.all_candidates
        best = self._result.best

        # データ抽出
        feasible_data = {k: [] for k in keys}
        infeasible_data = {k: [] for k in keys}
        for cand in candidates:
            target = feasible_data if cand.is_feasible else infeasible_data
            for k in keys:
                target[k].append(cand.response_values.get(k, float("nan")))

        best_data = {}
        if best:
            for k in keys:
                best_data[k] = best.response_values.get(k, float("nan"))

        # ペアプロット生成
        pairs = []
        for i in range(n_obj):
            for j in range(i + 1, n_obj):
                pairs.append((keys[i], keys[j]))

        for idx, (kx, ky) in enumerate(pairs):
            ax = fig.add_subplot(
                len(pairs) // min(len(pairs), 3) + (1 if len(pairs) % min(len(pairs), 3) else 0),
                min(len(pairs), 3),
                idx + 1,
                facecolor=bg,
            )

            # infeasible (灰色)
            if infeasible_data[kx]:
                ax.scatter(
                    infeasible_data[kx], infeasible_data[ky],
                    c="#999999", alpha=0.3, s=20, label="制約不満足",
                    edgecolors="none",
                )

            # feasible (青)
            if feasible_data[kx]:
                ax.scatter(
                    feasible_data[kx], feasible_data[ky],
                    c="#2196F3", alpha=0.6, s=30, label="制約満足",
                    edgecolors="none",
                )

            # Pareto front をハイライト（非劣解の抽出）
            pareto_x, pareto_y = self._extract_pareto_front(
                feasible_data[kx], feasible_data[ky]
            )
            if pareto_x:
                sorted_pairs = sorted(zip(pareto_x, pareto_y))
                px = [p[0] for p in sorted_pairs]
                py = [p[1] for p in sorted_pairs]
                ax.plot(px, py, "o-", color="#FF9800", markersize=5,
                        linewidth=1.5, label="Pareto front", alpha=0.8)

            # 最良解 (星マーカー)
            if best_data:
                ax.scatter(
                    [best_data[kx]], [best_data[ky]],
                    marker="*", c="#FF5722", s=200, zorder=5,
                    label="最良解", edgecolors="white", linewidths=0.5,
                )

            ax.set_xlabel(self._obj_labels.get(kx, kx), color=fg, fontsize=9)
            ax.set_ylabel(self._obj_labels.get(ky, ky), color=fg, fontsize=9)
            ax.tick_params(colors=fg, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(fg)
            ax.legend(fontsize=7, loc="best")

    @staticmethod
    def _extract_pareto_front(
        xs: List[float], ys: List[float],
    ) -> tuple:
        """2目的の非劣解（Pareto front）を抽出する。"""
        if not xs or not ys:
            return [], []
        points = np.array(list(zip(xs, ys)))
        # NaN除去
        valid = ~np.isnan(points).any(axis=1)
        points = points[valid]
        if len(points) == 0:
            return [], []

        pareto_mask = np.ones(len(points), dtype=bool)
        for i in range(len(points)):
            if not pareto_mask[i]:
                continue
            for j in range(len(points)):
                if i == j or not pareto_mask[j]:
                    continue
                # j が i を支配するか
                if (points[j] <= points[i]).all() and (points[j] < points[i]).any():
                    pareto_mask[i] = False
                    break

        pareto_pts = points[pareto_mask]
        return pareto_pts[:, 0].tolist(), pareto_pts[:, 1].tolist()


class CorrelationDialog(QDialog):
    """パラメータ相関分析結果を表示するダイアログ。

    相関行列ヒートマップと強い相関のサマリーを表示し、
    設計者がパラメータ間の相互作用を理解するのを支援します。
    """

    def __init__(
        self,
        correlation: CorrelationResult,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._correlation = correlation
        self.setWindowTitle("パラメータ相関分析")
        self.resize(700, 600)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # サマリーテキスト
        summary = QLabel(
            f"分析対象: 上位{self._correlation.n_candidates}候補  |  "
            f"パラメータ数: {len(self._correlation.param_keys)}"
        )
        summary.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(summary)

        # 強い相関の警告
        strong = self._correlation.strong_correlations
        if strong:
            strong_text = "強い相関 (|r| >= 0.5):\n"
            for e in sorted(strong, key=lambda x: abs(x.correlation), reverse=True):
                sign = "正" if e.correlation > 0 else "負"
                strong_text += f"  {e.label_x} ↔ {e.label_y}: r = {e.correlation:+.3f} ({sign}相関)\n"
            strong_label = QLabel(strong_text.strip())
            strong_label.setStyleSheet(
                "background-color: #FFF3E0; padding: 8px; border-radius: 4px; "
                "font-family: monospace;"
            )
            strong_label.setWordWrap(True)
            layout.addWidget(strong_label)
        else:
            no_corr = QLabel("パラメータ間に強い相関 (|r| >= 0.5) は検出されませんでした。")
            no_corr.setStyleSheet("color: #4CAF50; padding: 4px;")
            layout.addWidget(no_corr)

        # ヒートマップ
        fig = Figure(figsize=(6, 5))
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas, stretch=1)
        self._draw_heatmap(fig)

        # 閉じるボタン
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _draw_heatmap(self, fig: Figure) -> None:
        """相関行列ヒートマップを描画する。"""
        corr = self._correlation
        mat = np.array(corr.correlation_matrix)
        n = len(corr.param_labels)

        ax = fig.add_subplot(111)
        # カラーマップ: 青(-1) → 白(0) → 赤(+1)
        im = ax.imshow(
            mat, cmap="RdBu_r", vmin=-1, vmax=1,
            aspect="equal", interpolation="nearest",
        )
        fig.colorbar(im, ax=ax, label="相関係数 r", shrink=0.8)

        # ラベル
        ax.set_xticks(range(n))
        ax.set_yticks(range(n))
        ax.set_xticklabels(corr.param_labels, rotation=45, ha="right", fontsize=9)
        ax.set_yticklabels(corr.param_labels, fontsize=9)

        # セル内に相関係数を表示
        for i in range(n):
            for j in range(n):
                val = mat[i, j]
                color = "white" if abs(val) > 0.6 else "black"
                ax.text(
                    j, i, f"{val:.2f}",
                    ha="center", va="center", fontsize=10,
                    color=color, fontweight="bold" if abs(val) >= 0.5 else "normal",
                )

        ax.set_title("パラメータ相関行列", fontsize=12)
        try:
            fig.tight_layout()
        except (MemoryError, ValueError):
            logger.debug("tight_layout失敗")


class DiagnosticsDialog(QDialog):
    """収束品質診断結果を表示するダイアログ。

    探索の品質を5段階のスコアと具体的な推奨アクションで提示し、
    設計者が「もう一度回すべきか」を判断するのを支援します。
    """

    def __init__(
        self,
        diagnostics: ConvergenceDiagnostics,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._diag = diagnostics
        self.setWindowTitle("収束品質診断")
        self.resize(520, 480)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # 品質スコアカード
        score = self._diag.quality_score
        label = self._diag.quality_label
        if score >= 80:
            score_color = "#4CAF50"  # 緑
        elif score >= 60:
            score_color = "#2196F3"  # 青
        elif score >= 40:
            score_color = "#FF9800"  # 橙
        else:
            score_color = "#F44336"  # 赤

        score_card = QLabel(
            f"<div style='text-align:center;'>"
            f"<span style='font-size:36px; font-weight:bold; color:{score_color};'>"
            f"{score:.0f}</span>"
            f"<span style='font-size:14px; color:#888;'> / 100</span><br/>"
            f"<span style='font-size:16px; font-weight:bold; color:{score_color};'>"
            f"{label}</span>"
            f"</div>"
        )
        score_card.setStyleSheet(
            f"background-color: #1E1E1E; border: 2px solid {score_color}; "
            f"border-radius: 8px; padding: 16px; margin-bottom: 8px;"
        )
        layout.addWidget(score_card)

        # 指標テーブル
        metrics_text = (
            f"評価数: {self._diag.n_evaluations}  |  "
            f"制約満足: {self._diag.n_feasible} "
            f"({self._diag.feasibility_ratio*100:.1f}%)\n"
            f"空間カバー率: {self._diag.space_coverage*100:.1f}%  |  "
            f"最良解近傍密度: {self._diag.best_cluster_ratio*100:.1f}%\n"
            f"後半改善率: {self._diag.improvement_ratio*100:.2f}%  |  "
            f"末尾停滞: {'検出' if self._diag.stagnation_detected else 'なし'}"
        )
        metrics_label = QLabel(metrics_text)
        metrics_label.setStyleSheet(
            "font-family: monospace; font-size: 11px; padding: 8px; "
            "background-color: #2A2A2A; border-radius: 4px;"
        )
        layout.addWidget(metrics_label)

        # 推奨アクション
        rec_title = QLabel("推奨アクション:")
        rec_title.setStyleSheet("font-weight: bold; font-size: 13px; margin-top: 8px;")
        layout.addWidget(rec_title)

        for rec in self._diag.recommendations:
            rec_label = QLabel(f"  {rec}")
            rec_label.setWordWrap(True)
            rec_label.setStyleSheet("font-size: 12px; padding: 2px 8px;")
            layout.addWidget(rec_label)

        layout.addStretch()

        # 閉じるボタン
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class SobolDialog(QDialog):
    """Sobol グローバル感度解析結果を表示するダイアログ。

    - 棒グラフ: 一次指標 (S1) と全次指標 (ST) の比較
    - 交互作用指標: ST - S1 で他パラメータとの相互作用を可視化
    """

    def __init__(
        self,
        result: SobolResult,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._result = result
        self.setWindowTitle("Sobol グローバル感度解析")
        self.setMinimumSize(700, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ヘッダ
        header = QLabel(
            f"目的関数: {self._result.objective_label or self._result.objective_key}  |  "
            f"サンプル数: {self._result.n_samples}  |  "
            f"評価回数: {self._result.n_evaluations}"
        )
        header.setStyleSheet("font-weight: bold; font-size: 13px; padding: 4px;")
        layout.addWidget(header)

        desc = QLabel(
            "S1 (一次): パラメータ単独の寄与  |  "
            "ST (全次): 交互作用を含む全寄与  |  "
            "ST - S1 > 0: 他パラメータとの交互作用が大きい"
        )
        desc.setStyleSheet("font-size: 11px; color: gray; padding: 2px 4px;")
        layout.addWidget(desc)

        # チャート
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        ax_face = MPL_STYLES[theme]["axes.facecolor"]

        self._fig = Figure(figsize=(6, 4), tight_layout=True, facecolor=facecolor)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_facecolor(ax_face)
        canvas = FigureCanvas(self._fig)
        layout.addWidget(canvas, stretch=1)

        # ボタン
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._draw_chart()

    def _draw_chart(self) -> None:
        """S1 と ST の比較棒グラフを描画します。"""
        ax = self._ax
        ax.clear()

        ranked = self._result.ranked_by_total
        if not ranked:
            ax.text(0.5, 0.5, "感度データなし", ha="center", va="center",
                    transform=ax.transAxes, fontsize=11, color="gray")
            return

        labels = [e.label for e in ranked]
        s1_vals = [e.s1 for e in ranked]
        st_vals = [e.st for e in ranked]

        y_pos = np.arange(len(labels))
        bar_height = 0.35

        bars_st = ax.barh(
            y_pos - bar_height / 2, st_vals, bar_height,
            label="ST (全次)", color="#e74c3c", alpha=0.8, edgecolor="none",
        )
        bars_s1 = ax.barh(
            y_pos + bar_height / 2, s1_vals, bar_height,
            label="S1 (一次)", color="#3498db", alpha=0.8, edgecolor="none",
        )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels)
        ax.set_xlabel("感度指標")
        ax.set_title("Sobol 感度指標（一次 S1 / 全次 ST）", fontsize=11)
        ax.invert_yaxis()
        ax.legend(loc="lower right", fontsize=9)
        ax.set_xlim(0, max(max(st_vals, default=0), 0.1) * 1.2)

        # 値ラベル
        for bar, val in zip(bars_st, st_vals):
            if val > 0.01:
                ax.text(
                    bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{val:.3f}", va="center", fontsize=8,
                )
        for bar, val in zip(bars_s1, s1_vals):
            if val > 0.01:
                ax.text(
                    bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{val:.3f}", va="center", fontsize=8,
                )

        try:
            self._fig.tight_layout()
        except (MemoryError, ValueError):
            logger.debug("tight_layout失敗")


class HeatmapDialog(QDialog):
    """パラメータ空間の探索ヒートマップダイアログ。

    2パラメータペアごとに、探索された領域を2Dヒートマップ（ビン化平均）で
    可視化します。色が目的関数値を表し、探索されていない領域は灰色で表示されます。
    設計者がどの領域を重点的に探索したかを把握し、追加探索の判断に役立ちます。
    """

    def __init__(
        self, result: OptimizationResult, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("パラメータ空間ヒートマップ")
        self.resize(900, 700)

        layout = QVBoxLayout(self)

        # パラメータペア選択（3パラメータ以上の場合）
        params = result.config.parameters if result.config else []
        param_names = [p.key for p in params]
        param_labels = {p.key: p.label for p in params}
        pairs = list(combinations(param_names, 2))

        if len(pairs) > 1:
            selector_row = QHBoxLayout()
            selector_row.addWidget(QLabel("パラメータペア:"))
            self._pair_combo = QComboBox()
            for p1, p2 in pairs:
                self._pair_combo.addItem(
                    f"{param_labels.get(p1, p1)} vs {param_labels.get(p2, p2)}",
                    userData=(p1, p2),
                )
            selector_row.addWidget(self._pair_combo)
            selector_row.addStretch()
            layout.addLayout(selector_row)
            self._pair_combo.currentIndexChanged.connect(
                lambda: self._draw(result, param_labels)
            )
        else:
            self._pair_combo = None

        self._fig = Figure(figsize=(8, 6))
        self._canvas = FigureCanvas(self._fig)
        layout.addWidget(self._canvas)

        # 情報ラベル
        self._info_label = QLabel("")
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._result = result
        self._pairs = pairs
        self._draw(result, param_labels)

    def _draw(
        self,
        result: OptimizationResult,
        param_labels: dict[str, str],
    ) -> None:
        """選択されたパラメータペアのヒートマップを描画します。"""
        self._fig.clear()
        ax = self._fig.add_subplot(111)

        pair = self._current_pair()
        if pair is None:
            return
        p1_name, p2_name = pair
        p1_label = param_labels.get(p1_name, p1_name)
        p2_label = param_labels.get(p2_name, p2_name)

        x_arr, y_arr, z_arr = self._collect_heatmap_points(result, p1_name, p2_name)
        if x_arr is None or len(x_arr) < 3:
            msg = "有効データ不足" if x_arr is not None else "データ不足（3点以上必要）"
            self._show_empty_message(ax, msg)
            return

        n_bins, x_edges, y_edges, grid_mean, mask = self._bin_grid(x_arr, y_arr, z_arr)
        self._render_heatmap(
            ax, result, p1_name, p2_name, p1_label, p2_label,
            x_edges, y_edges, grid_mean,
        )
        self._update_heatmap_info(len(x_arr), n_bins, mask)

        try:
            self._fig.tight_layout()
        except (MemoryError, ValueError):
            logger.debug("tight_layout失敗")
        self._canvas.draw()

    def _current_pair(self):
        if self._pair_combo is not None:
            return self._pair_combo.currentData()
        return self._pairs[0] if self._pairs else None

    @staticmethod
    def _collect_heatmap_points(result: OptimizationResult, p1_name: str, p2_name: str):
        x_vals, y_vals, z_vals = [], [], []
        for cand in result.all_candidates:
            if p1_name in cand.params and p2_name in cand.params:
                x_vals.append(cand.params[p1_name])
                y_vals.append(cand.params[p2_name])
                z_vals.append(cand.objective_value)
        if len(x_vals) < 3:
            return None, None, None
        x_arr = np.array(x_vals)
        y_arr = np.array(y_vals)
        z_arr = np.array(z_vals)
        valid = np.isfinite(z_arr)
        return x_arr[valid], y_arr[valid], z_arr[valid]

    def _show_empty_message(self, ax, message: str) -> None:
        ax.text(
            0.5, 0.5, message,
            ha="center", va="center", transform=ax.transAxes, fontsize=14,
        )
        self._canvas.draw()

    @staticmethod
    def _bin_grid(x_arr, y_arr, z_arr):
        n_bins = min(20, max(5, int(np.sqrt(len(x_arr)))))
        x_edges = np.linspace(x_arr.min(), x_arr.max(), n_bins + 1)
        y_edges = np.linspace(y_arr.min(), y_arr.max(), n_bins + 1)
        grid_sum = np.zeros((n_bins, n_bins))
        grid_count = np.zeros((n_bins, n_bins))
        x_idx = np.clip(np.digitize(x_arr, x_edges) - 1, 0, n_bins - 1)
        y_idx = np.clip(np.digitize(y_arr, y_edges) - 1, 0, n_bins - 1)
        for xi, yi, zi in zip(x_idx, y_idx, z_arr):
            grid_sum[yi, xi] += zi
            grid_count[yi, xi] += 1
        grid_mean = np.full((n_bins, n_bins), np.nan)
        mask = grid_count > 0
        grid_mean[mask] = grid_sum[mask] / grid_count[mask]
        return n_bins, x_edges, y_edges, grid_mean, mask

    def _render_heatmap(
        self, ax, result, p1_name, p2_name, p1_label, p2_label,
        x_edges, y_edges, grid_mean,
    ) -> None:
        ax.set_facecolor("#e0e0e0")
        im = ax.pcolormesh(
            x_edges, y_edges, grid_mean,
            cmap="viridis_r", shading="flat",
        )
        cb = self._fig.colorbar(im, ax=ax, pad=0.02)
        obj_label = result.config.objective_label if result.config else "目的関数値"
        cb.set_label(obj_label, fontsize=9)
        if result.best and p1_name in result.best.params and p2_name in result.best.params:
            ax.plot(
                result.best.params[p1_name],
                result.best.params[p2_name],
                marker="*", markersize=18, color="#ff4444",
                markeredgecolor="white", markeredgewidth=1.5,
                zorder=10, label="最良解",
            )
            ax.legend(loc="upper right", fontsize=9)
        ax.set_xlabel(p1_label, fontsize=10)
        ax.set_ylabel(p2_label, fontsize=10)
        ax.set_title(
            f"探索ヒートマップ: {p1_label} × {p2_label}", fontsize=11
        )

    def _update_heatmap_info(self, n_points: int, n_bins: int, mask) -> None:
        explored = int(np.sum(mask))
        total_bins = n_bins * n_bins
        coverage_pct = explored / total_bins * 100
        self._info_label.setText(
            f"候補数: {n_points} | "
            f"ビン: {n_bins}×{n_bins}={total_bins} | "
            f"探索済み: {explored}ビン ({coverage_pct:.0f}%) | "
            f"灰色=未探索領域"
        )


# 後方互換エイリアス（旧名 _HeatmapDialog）
_HeatmapDialog = HeatmapDialog
