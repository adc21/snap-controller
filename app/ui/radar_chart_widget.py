"""
app/ui/radar_chart_widget.py
レーダーチャート（スパイダーチャート）比較ウィジェット。

複数ケースの各応答値を正規化してレーダーチャートに重ねて表示します。
各軸は応答値（最大変位、最大加速度、層間変形角など）で、
値が小さいほど内側＝有利であることが一目でわかります。

レイアウト:
  ┌──────────────────────────────────────┐
  │ [正規化方法]  [ケース選択リスト]     │
  │ matplotlib レーダーチャート           │
  └──────────────────────────────────────┘

UX改善（新）: 総合スコアバナー + 最良ケース自動ゴールドハイライト。
  各ケースの「総合スコア」（全軸の正規化値の合計）を計算し、
  最小スコアのケース（全体的に最も有利なケース）を自動的に
  ゴールド色・太線・大きめのマーカーでハイライトします。
  グラフ下部に「🏆 総合最良: {ケース名}（スコア: X.XX）」バナーを表示し、
  複数の応答値を同時考慮した場合の最適ケースを直感的に把握できます。
  スコアが同率最良の場合は複数ケースをカンマ区切りで表示します。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager, MPL_STYLES

# レーダーチャートに使う応答値 (key, 短いラベル, 単位)
_RADAR_ITEMS = [
    ("max_disp",  "相対変位",      "m"),
    ("max_vel",   "相対速度",      "m/s"),
    ("max_acc",   "絶対加速度",    "m/s²"),
    ("max_drift", "層間変形角",    "rad"),
    ("max_shear", "せん断力係数",  "—"),
    ("max_otm",   "転倒ﾓｰﾒﾝﾄ",   "kN·m"),
]

# ケースごとのカラーサイクル
_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]

# 正規化方法
_NORM_METHODS = [
    ("max", "最大値で正規化（最大=1.0）"),
    ("first", "基準ケース比（最初のチェック=1.0）"),
]


def _apply_mpl_theme() -> None:
    """matplotlib の rcParams に現在のテーマを適用します。"""
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _RadarCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(5, 5), tight_layout=True, facecolor=facecolor)
        self.ax = self.fig.add_subplot(111, polar=True)
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        super().__init__(self.fig)
        self.setParent(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

    def apply_theme(self) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class RadarChartWidget(QWidget):
    """
    レーダーチャートで複数ケースの応答値を比較するウィジェット。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットして選択肢を更新します
    refresh()         — 現在の選択状態でグラフを再描画します
    update_theme()    — テーマ変更時に呼び出します
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_cases: List[AnalysisCase] = []
        self._checkboxes: List[tuple[QCheckBox, AnalysisCase]] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        self._all_cases = cases
        self._rebuild_checklist()
        self.refresh()

    def refresh(self) -> None:
        self._draw()

    def update_theme(self) -> None:
        self._canvas.apply_theme()
        self._draw()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- コントロール行 ---
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("正規化:"))
        self._norm_combo = QComboBox()
        for _, label in _NORM_METHODS:
            self._norm_combo.addItem(label)
        self._norm_combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._norm_combo)
        ctrl_row.addStretch()

        btn_all = QPushButton("全選択")
        btn_all.setMaximumWidth(64)
        btn_all.clicked.connect(self._select_all)
        ctrl_row.addWidget(btn_all)

        btn_none = QPushButton("全解除")
        btn_none.setMaximumWidth(64)
        btn_none.clicked.connect(self._deselect_all)
        ctrl_row.addWidget(btn_none)

        layout.addLayout(ctrl_row)

        # --- メイン: チェックリスト（左）+ レーダーチャート（右）---
        main_row = QHBoxLayout()

        group = QGroupBox("比較するケース")
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

        self._canvas = _RadarCanvas(self)
        main_row.addWidget(self._canvas, stretch=1)

        layout.addLayout(main_row, stretch=1)

        # ---- UX改善（新）: 総合スコアバナー ----
        # グラフ下部に最良ケース情報を常時表示する
        self._score_banner = QFrame()
        self._score_banner.setFrameShape(QFrame.StyledPanel)
        self._score_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #fff8e1;"
            "  border: 1px solid #f9a825;"
            "  border-radius: 4px;"
            "}"
        )
        self._score_banner.setMaximumHeight(36)
        _score_row = QHBoxLayout(self._score_banner)
        _score_row.setContentsMargins(10, 4, 10, 4)
        self._score_label = QLabel("")
        self._score_label.setStyleSheet("color: #7f5000; font-size: 11px; background: transparent;")
        _score_row.addWidget(self._score_label)
        self._score_banner.setVisible(False)
        layout.addWidget(self._score_banner)

        self._show_empty()

    # ------------------------------------------------------------------
    # Checklist
    # ------------------------------------------------------------------

    def _rebuild_checklist(self) -> None:
        for cb, _ in self._checkboxes:
            cb.deleteLater()
        self._checkboxes.clear()

        completed = [c for c in self._all_cases
                     if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary]

        if not completed:
            lbl = QLabel("<i>完了済みケースがありません</i>")
            lbl.setObjectName("_empty_label")
            self._check_layout.addWidget(lbl)
            return

        for i in range(self._check_layout.count() - 1, -1, -1):
            w = self._check_layout.itemAt(i).widget()
            if w and w.objectName() == "_empty_label":
                w.deleteLater()
                self._check_layout.removeItem(self._check_layout.itemAt(i))

        for i, case in enumerate(completed):
            color = _COLORS[i % len(_COLORS)]
            cb = QCheckBox(case.name)
            cb.setChecked(True)
            cb.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            cb.stateChanged.connect(self.refresh)
            self._check_layout.addWidget(cb)
            self._checkboxes.append((cb, case))

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

        # polar axes を再作成（clear だけだと polar 設定がリセットされることがある）
        self._canvas.fig.clear()
        self._canvas.ax = self._canvas.fig.add_subplot(111, polar=True)
        theme = "dark" if ThemeManager.is_dark() else "light"
        self._canvas.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])

        ax = self._canvas.ax

        if not selected:
            self._show_empty()
            return

        # 各ケースの応答値を収集
        labels = [label for _, label, _ in _RADAR_ITEMS]
        keys = [key for key, _, _ in _RADAR_ITEMS]
        n_vars = len(keys)

        case_values: List[tuple[AnalysisCase, int, List[Optional[float]]]] = []
        for case, color_idx in selected:
            vals = []
            for key in keys:
                v = case.result_summary.get(key)
                vals.append(v)
            case_values.append((case, color_idx, vals))

        # 正規化
        norm_method = _NORM_METHODS[self._norm_combo.currentIndex()][0]
        normalized = self._normalize(case_values, norm_method)

        if not normalized:
            self._show_empty("正規化可能なデータがありません")
            return

        # レーダーチャート描画
        angles = np.linspace(0, 2 * np.pi, n_vars, endpoint=False).tolist()
        angles += angles[:1]  # 閉じる

        ax.set_theta_offset(np.pi / 2)
        ax.set_theta_direction(-1)

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, fontsize=9)

        # ---- UX改善（新）: 総合スコア（全軸の正規化値の合計）を計算 ----
        # スコアが最小のケースが「最も多くの指標で有利」な総合最良ケース
        case_scores = []
        for case, color_idx, norm_vals in normalized:
            score = sum(norm_vals) / max(len(norm_vals), 1)
            case_scores.append((case, color_idx, norm_vals, score))

        # 最小スコアを特定
        best_score = min(s for _, _, _, s in case_scores) if case_scores else None

        for case, color_idx, norm_vals, score in case_scores:
            is_best = (best_score is not None and abs(score - best_score) < 1e-9)
            color = "#FFD700" if is_best else _COLORS[color_idx % len(_COLORS)]
            linewidth = 3.0 if is_best else 1.5
            markersize = 7 if is_best else 4
            alpha_fill = 0.18 if is_best else 0.06
            zorder = 5 if is_best else 3
            label_text = f"🏆 {case.name}（最良）" if is_best else case.name
            values = norm_vals + norm_vals[:1]  # 閉じる
            ax.plot(angles, values,
                    marker="o", markersize=markersize,
                    label=label_text,
                    color=color,
                    linewidth=linewidth,
                    zorder=zorder)
            ax.fill(angles, values, alpha=alpha_fill, color=color)

        ax.set_title("応答値レーダーチャート", fontsize=10, pad=20)
        ax.legend(fontsize=7, loc="upper right",
                  bbox_to_anchor=(1.3, 1.1))
        ax.tick_params(labelsize=8)

        # Y軸のグリッド
        ax.set_ylim(0, None)
        ax.grid(True, alpha=0.3)

        self._canvas.fig.tight_layout()
        self._canvas.draw()

        # ---- UX改善（新）: 総合スコアバナーを更新 ----
        self._update_score_banner(case_scores)

    def _update_score_banner(
        self,
        case_scores: List[tuple],
    ) -> None:
        """
        UX改善（新）: 総合スコアバナーを更新します。

        各ケースのスコア（全軸の正規化値の平均）でランキングし、
        最良ケース（スコア最小）をバナーに表示します。

        Parameters
        ----------
        case_scores : list of (case, color_idx, norm_vals, score)
        """
        if not case_scores:
            self._score_banner.setVisible(False)
            return

        sorted_by_score = sorted(case_scores, key=lambda t: t[3])
        best_score = sorted_by_score[0][3]
        best_cases = [c for c, _, _, s in sorted_by_score if abs(s - best_score) < 1e-9]

        # スコアランキングを3位まで表示
        rank_parts = []
        shown_scores = []
        for case, _, _, score in sorted_by_score:
            # 同スコアをまとめる
            if not any(abs(score - s) < 1e-9 for s in shown_scores):
                rank = len(shown_scores) + 1
                same = [c.name for c, _, _, s in sorted_by_score if abs(s - score) < 1e-9]
                label = "🏆 " if rank == 1 else ("🥈 " if rank == 2 else "🥉 ")
                rank_parts.append(f"{label}{', '.join(same)}（{score:.3f}）")
                shown_scores.append(score)
            if len(shown_scores) >= 3:
                break

        banner_text = "総合スコア（小さいほど良い）: " + "  /  ".join(rank_parts)
        if len(sorted_by_score) > len(shown_scores):
            remaining = len(sorted_by_score) - sum(
                1 for _, _, _, s in sorted_by_score
                if any(abs(s - sh) < 1e-9 for sh in shown_scores[:3])
            )
            if remaining > 0:
                banner_text += f"  … 他{remaining}件"

        self._score_label.setText(banner_text)
        self._score_label.setToolTip(
            "各ケースの「総合スコア」は全応答値指標の正規化値（0〜1）の平均です。\n"
            "0に近いほど全指標において良好なパフォーマンスを示します。\n"
            "🏆 は最もスコアの小さい（全体的に最も有利な）ケースです。"
        )
        self._score_banner.setVisible(True)

    def _normalize(
        self,
        case_values: List[tuple[AnalysisCase, int, List[Optional[float]]]],
        method: str,
    ) -> List[tuple[AnalysisCase, int, List[float]]]:
        """
        応答値を正規化します。

        Parameters
        ----------
        case_values : list of (case, color_idx, [val1, val2, ...])
        method : "max" or "first"

        Returns
        -------
        list of (case, color_idx, [normalized_val1, ...])
        """
        keys = [key for key, _, _ in _RADAR_ITEMS]
        n_vars = len(keys)

        if method == "max":
            # 各軸の最大値で割る
            max_per_axis = [0.0] * n_vars
            for _, _, vals in case_values:
                for i, v in enumerate(vals):
                    if v is not None and abs(v) > max_per_axis[i]:
                        max_per_axis[i] = abs(v)

            result = []
            for case, cidx, vals in case_values:
                normed = []
                for i, v in enumerate(vals):
                    if v is not None and max_per_axis[i] > 0:
                        normed.append(v / max_per_axis[i])
                    else:
                        normed.append(0.0)
                result.append((case, cidx, normed))
            return result

        elif method == "first":
            # 最初のケースの値で割る
            if not case_values:
                return []
            base_vals = case_values[0][2]
            result = []
            for case, cidx, vals in case_values:
                normed = []
                for i, v in enumerate(vals):
                    base = base_vals[i]
                    if v is not None and base is not None and abs(base) > 1e-12:
                        normed.append(v / base)
                    elif v is not None:
                        normed.append(1.0)
                    else:
                        normed.append(0.0)
                result.append((case, cidx, normed))
            return result

        return []

    def _show_empty(self, msg: str = "比較するケースを選択してください") -> None:
        self._canvas.fig.clear()
        ax = self._canvas.fig.add_subplot(111)
        ax.text(0.5, 0.5, msg,
                ha="center", va="center",
                transform=ax.transAxes,
                fontsize=11, color="gray")
        ax.set_axis_off()
        self._canvas.ax = ax
        self._canvas.draw()
        # UX改善（新）: 空状態ではバナーを非表示に
        if hasattr(self, "_score_banner"):
            self._score_banner.setVisible(False)
