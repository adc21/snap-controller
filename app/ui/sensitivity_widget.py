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

UX改善（新）: 感度分析ガイドバナー + 最重要パラメータ自動ハイライト。
  ① 折りたたみ式「感度分析とは？」ガイドバナーをウィジェット上部に追加。
    「▶ 感度分析とは？」ボタンで展開し、相関係数の読み方・活用方法を説明します。
    初めてこのタブを開いたユーザーが操作に迷わないようにします。

  ② トルネード図の最重要パラメータ（|r|最大）をゴールド色でハイライト。
    最も応答値への影響が大きいパラメータのバーをゴールド（#FFD700）で強調し、
    バー右端に「⭐ 最重要」テキスト注釈を追加します。
    「どのパラメータを優先的に最適化すべきか」を一目で判断できます。

  ③ 感度統計テーブルの最重要行に「⭐ 最重要」マークを付与し、背景を金色に変更。
    数値だけでなく視覚的なマークで最重要パラメータを強調します。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor as _QColorSens
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
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

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager, MPL_STYLES

import logging

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "sans-serif"]
except Exception:
    logging.getLogger(__name__).debug("日本語フォント設定失敗")

logger = logging.getLogger(__name__)

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

        self._build_guide_banner(layout)
        self._build_ctrl_row(layout)
        self._build_chart_area(layout)
        self._build_stat_table(layout)
        self._build_suggest_panel(layout)

    def _build_guide_banner(self, layout: QVBoxLayout) -> None:
        self._guide_panel_visible = False
        guide_header = QFrame()
        guide_header.setStyleSheet(
            "QFrame { background-color: #e3f2fd; border: 1px solid #90caf9; border-radius: 4px; }"
        )
        _guide_header_row = QHBoxLayout(guide_header)
        _guide_header_row.setContentsMargins(8, 4, 8, 4)
        self._guide_toggle_btn = QPushButton("▶  感度分析とは？")
        self._guide_toggle_btn.setFlat(True)
        self._guide_toggle_btn.setStyleSheet(
            "QPushButton { color: #1565c0; font-size: 11px; font-weight: bold; "
            "text-align: left; background: transparent; border: none; }"
            "QPushButton:hover { color: #0d47a1; }"
        )
        self._guide_toggle_btn.clicked.connect(self._toggle_guide_panel)
        _guide_header_row.addWidget(self._guide_toggle_btn)
        _guide_header_row.addStretch()
        layout.addWidget(guide_header)

        self._guide_content = QFrame()
        self._guide_content.setStyleSheet(
            "QFrame { background-color: #e8f5e9; border: 1px solid #a5d6a7; "
            "border-top: none; border-radius: 0 0 4px 4px; }"
        )
        _guide_content_layout = QVBoxLayout(self._guide_content)
        _guide_content_layout.setContentsMargins(12, 6, 12, 8)
        _guide_content_layout.setSpacing(4)
        _guide_lines = [
            "<b>感度分析</b>は「どのパラメータが応答値に最も影響するか」を定量的に評価します。",
            "　<b>相関係数 r</b>（-1〜+1）: 絶対値が大きいほど影響が強い。正は「増やすと応答が増加」、負は「増やすと応答が減少」。",
            "　<b>トルネード図</b>: バーが長いパラメータほど重要。右（赤）= 正の相関、左（青）= 負の相関。",
            "　<b>活用方法</b>: |r| > 0.7 なら強い相関。最重要パラメータ（⭐）を優先的に最適化することで効率的に性能改善できます。",
        ]
        for line in _guide_lines:
            lbl = QLabel(line)
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 10px; color: #1b5e20; background: transparent;")
            _guide_content_layout.addWidget(lbl)
        self._guide_content.setVisible(False)
        layout.addWidget(self._guide_content)

    def _build_ctrl_row(self, layout: QVBoxLayout) -> None:
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

    def _build_chart_area(self, layout: QVBoxLayout) -> None:
        chart_splitter = QSplitter(Qt.Horizontal)

        tornado_widget = QWidget()
        tornado_layout = QVBoxLayout(tornado_widget)
        tornado_layout.setContentsMargins(0, 0, 0, 0)
        tornado_layout.addWidget(QLabel("感度トルネード図（相関係数）"))
        self._tornado_canvas = _MplCanvas(self)
        tornado_layout.addWidget(self._tornado_canvas)
        chart_splitter.addWidget(tornado_widget)

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

    def _build_stat_table(self, layout: QVBoxLayout) -> None:
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

    def _build_suggest_panel(self, layout: QVBoxLayout) -> None:
        suggest_header_row = QHBoxLayout()
        _suggest_hdr_lbl = QLabel("<b>📋 次ラウンド推奨パラメータ</b>")
        _suggest_hdr_lbl.setTextFormat(Qt.RichText)
        suggest_header_row.addWidget(_suggest_hdr_lbl)
        suggest_header_row.addStretch()

        self._suggest_btn = QPushButton("✨ 改善提案を生成")
        self._suggest_btn.setToolTip(
            "感度解析の結果から、次ラウンドで変更すべきパラメータと推奨変更量を自動計算します。\n"
            "最重要パラメータ（|r| 最大）の相関方向を参照して「増やす/減らす」の方向を提案します。"
        )
        self._suggest_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 10px; padding: 3px 10px;"
            "  border: 1px solid #90caf9; border-radius: 3px;"
            "  background: #e3f2fd; color: #1565c0;"
            "}"
            "QPushButton:hover { background: #bbdefb; }"
            "QPushButton:pressed { background: #90caf9; }"
        )
        self._suggest_btn.clicked.connect(self._generate_suggestions)
        suggest_header_row.addWidget(self._suggest_btn)

        layout.addLayout(suggest_header_row)

        self._suggest_panel = QFrame()
        self._suggest_panel.setFrameShape(QFrame.StyledPanel)
        self._suggest_panel.setStyleSheet(
            "QFrame {"
            "  background-color: #f3e5f5;"
            "  border: 1px solid #ce93d8;"
            "  border-radius: 4px;"
            "}"
        )
        _sp_layout = QVBoxLayout(self._suggest_panel)
        _sp_layout.setContentsMargins(10, 6, 10, 8)
        _sp_layout.setSpacing(4)

        _sp_title_row = QHBoxLayout()
        _sp_icon = QLabel("🔮")
        _sp_icon.setStyleSheet("font-size: 14px; background: transparent; border: none;")
        _sp_title_row.addWidget(_sp_icon)
        _sp_title = QLabel("<b>次ラウンドで試すべきパラメータ変更の提案</b>")
        _sp_title.setTextFormat(Qt.RichText)
        _sp_title.setStyleSheet("color: #4a148c; background: transparent; border: none;")
        _sp_title_row.addWidget(_sp_title)
        _sp_title_row.addStretch()
        _sp_layout.addLayout(_sp_title_row)

        self._suggest_content_lbl = QLabel(
            "「✨ 改善提案を生成」ボタンを押すと、感度解析結果に基づいた\n"
            "パラメータ変更の推奨が表示されます。"
        )
        self._suggest_content_lbl.setWordWrap(True)
        self._suggest_content_lbl.setStyleSheet(
            "color: #6a1b9a; font-size: 10px; background: transparent; border: none;"
        )
        self._suggest_content_lbl.setTextFormat(Qt.RichText)
        _sp_layout.addWidget(self._suggest_content_lbl)

        _sp_note = QLabel(
            "<i>※ 提案はあくまで参考値です。実際の設計判断はエンジニアが行ってください。</i>"
        )
        _sp_note.setTextFormat(Qt.RichText)
        _sp_note.setStyleSheet(
            "color: #9c27b0; font-size: 9px; background: transparent; border: none;"
        )
        _sp_layout.addWidget(_sp_note)

        self._suggest_panel.hide()
        layout.addWidget(self._suggest_panel)

    def _toggle_guide_panel(self) -> None:
        """UX改善（新）①: ガイドパネルの表示/非表示を切り替えます。"""
        self._guide_panel_visible = not self._guide_panel_visible
        self._guide_content.setVisible(self._guide_panel_visible)
        arrow = "▼" if self._guide_panel_visible else "▶"
        self._guide_toggle_btn.setText(f"{arrow}  感度分析とは？")

    def _generate_suggestions(self) -> None:
        """
        UX改善（第12回⑤）: 感度解析結果から次ラウンド推奨パラメータ変更を自動生成します。

        アルゴリズム:
        1. 現在選択中の応答指標について感度統計を取得する
        2. |r| が最大のパラメータを「最重要」と判定する
        3. 相関係数の符号から「増やすべきか / 減らすべきか」を判断する
           - r < 0 かつ応答値を減らしたい（制振効果向上）→ 増やす方向
           - r > 0 かつ応答値を減らしたい → 減らす方向
        4. 現在の平均値 ± 20% を推奨変更量として表示する
        5. |r| が低い（0.3 未満）パラメータは「影響小」と明示する

        データ不足（ケース2件未満）の場合は案内メッセージを表示します。
        """
        if not hasattr(self, "_suggest_panel"):
            return

        param_data, response_data, response_key = self._extract_param_response_data()

        if not param_data or not response_data or len(response_data) < 2:
            self._show_suggest_message(
                "⚠ 感度分析に必要なデータが不足しています。<br>"
                "完了済みケースが <b>2件以上</b> 必要です。<br>"
                "STEP3 で追加のケースを解析してからもう一度お試しください。"
            )
            return

        correlations = self._compute_all_correlations(param_data, response_data)

        if not correlations:
            self._show_suggest_message(
                "⚠ 有効な相関データがありません。<br>"
                "ダンパーパラメータを変えた複数のケースを解析してください。"
            )
            return

        correlations.sort(key=lambda t: abs(t[1]), reverse=True)

        idx = self._response_combo.currentIndex()
        resp_label = _RESPONSE_ITEMS[idx][1] if idx < len(_RESPONSE_ITEMS) else response_key
        resp_unit = _RESPONSE_ITEMS[idx][2] if idx < len(_RESPONSE_ITEMS) else ""

        lines = self._build_suggestion_lines(
            correlations, param_data, response_data, resp_label, resp_unit,
        )

        self._suggest_content_lbl.setText("<br>".join(lines))
        self._suggest_content_lbl.setTextFormat(Qt.RichText)
        self._suggest_panel.show()

    def _show_suggest_message(self, html: str) -> None:
        self._suggest_content_lbl.setText(html)
        self._suggest_content_lbl.setTextFormat(Qt.RichText)
        self._suggest_panel.show()

    def _compute_all_correlations(
        self, param_data: Dict[str, List[float]], response_data: List[float]
    ) -> List[Tuple[str, float, float, int]]:
        correlations: List[Tuple[str, float, float, int]] = []
        y = np.array(response_data)
        for param_key, values in param_data.items():
            if len(values) < 2:
                continue
            x = np.array(values)
            r, p = _compute_correlation(x, y)
            correlations.append((param_key, r, p, len(values)))
        return correlations

    def _build_suggestion_lines(
        self, correlations, param_data, response_data, resp_label, resp_unit,
    ) -> List[str]:
        lines: List[str] = [
            f"<b>対象指標:</b> {resp_label} [{resp_unit}]　"
            f"（完了ケース {len(response_data)}件の解析）",
            "",
        ]

        shown = 0
        for param_key, r, p, n_pts in correlations[:5]:
            values = param_data.get(param_key, [])
            lines.append(self._format_suggestion_line(param_key, r, values))
            shown += 1
            if shown >= 3:
                break

        if len(correlations) > 3:
            lines.append(
                f"<span style='color:#888;'>（他 {len(correlations)-3} パラメータは影響が小さいか、"
                f"データ点数不足のため省略）</span>"
            )

        lines.append("")
        lines.append(
            "<span style='color:#9c27b0; font-size:9px;'>"
            "※ 提案は相関係数に基づく統計的推定です。感度解析ケース数が少ない場合は精度が低くなります。"
            "最終的な設計判断はエンジニアが行ってください。</span>"
        )
        return lines

    @staticmethod
    def _classify_strength(abs_r: float) -> Tuple[str, str]:
        if abs_r >= 0.7:
            return "強い影響", "#b71c1c"
        if abs_r >= 0.4:
            return "中程度の影響", "#e65100"
        return "影響小（参考）", "#888"

    @staticmethod
    def _classify_direction(r: float) -> Tuple[str, str]:
        if r < -0.1:
            return "⬆ 増やす", "#1565c0"
        if r > 0.1:
            return "⬇ 減らす", "#c62828"
        return "↔ 効果は小さい", "#888"

    @staticmethod
    def _format_change_str(r: float, values: List[float]) -> str:
        if not values:
            return "（現在値不明）"
        current_avg = np.mean(values)
        change_20 = current_avg * 0.20
        if r < 0:
            recommended = current_avg + change_20
            return f"+20% → {recommended:.3g}"
        recommended = max(0, current_avg - change_20)
        return f"-20% → {recommended:.3g}"

    def _format_suggestion_line(
        self, param_key: str, r: float, values: List[float]
    ) -> str:
        abs_r = abs(r)
        strength, strength_color = self._classify_strength(abs_r)
        direction, dir_color = self._classify_direction(r)
        change_str = self._format_change_str(r, values)

        param_display = param_key
        if "." in param_key:
            parts_split = param_key.split(".", 1)
            param_display = f"{parts_split[0]}<br>　└ F{parts_split[1]}"

        avg_str = f"{np.mean(values):.3g}" if values else "?"
        return (
            f"<b style='color:{strength_color};'>⭐ {param_display}</b>　"
            f"|r| = {abs_r:.2f}（{strength}）<br>"
            f"　推奨: <b style='color:{dir_color};'>{direction}</b>　"
            f"現在平均 {avg_str} → {change_str}"
        )

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
                    logger.debug("パラメータ値の数値判定失敗: %s=%s", k, v)

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

        # ---- UX改善（新）②: 最重要パラメータ（|r|最大）のインデックス特定 ----
        # top は reversed (下から上) なので、最後の要素が最大|r|
        # top_n 番目のバー（最上位）が最重要 — インデックスは len(top)-1
        most_important_idx = len(top) - 1  # 最上位（トルネード図の一番上のバー）

        y_pos = range(len(labels))
        colors = []
        for i, r in enumerate(r_values):
            if i == most_important_idx:
                colors.append("#FFD700")  # ゴールド: 最重要
            else:
                colors.append(_COLOR_POSITIVE if r > 0 else _COLOR_NEGATIVE)

        bar_lws = [2.0 if i == most_important_idx else 0.0 for i in range(len(r_values))]
        bars = ax.barh(
            y_pos, r_values, color=colors,
            edgecolor=["#b8860b" if i == most_important_idx else "none" for i in range(len(r_values))],
            linewidth=bar_lws,
            height=0.6,
        )

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

        # バーにr値を表示 + 最重要には「⭐ 最重要」注釈を追加
        for i, (bar, r) in enumerate(zip(bars, r_values)):
            x_pos = bar.get_width()
            ha = "left" if r >= 0 else "right"
            offset = 0.02 if r >= 0 else -0.02
            ax.text(
                x_pos + offset, bar.get_y() + bar.get_height() / 2,
                f"{r:.3f}", va="center", ha=ha, fontsize=7,
            )
            # UX改善（新）②: 最重要パラメータに「⭐ 最重要」を注釈
            if i == most_important_idx:
                ax.text(
                    0, bar.get_y() + bar.get_height() / 2,
                    "⭐ 最重要",
                    va="center", ha="center",
                    fontsize=7, color="#7f5000", fontweight="bold",
                    zorder=10,
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

        # UX改善（新）③: 最重要パラメータは correlations[0]（|r|でソート済み）
        best_abs_r = abs(correlations[0][1]) if correlations else None

        for row_idx, (pk, r, p, n) in enumerate(correlations):
            row = self._stat_table.rowCount()
            self._stat_table.insertRow(row)

            is_most_important = (
                best_abs_r is not None
                and row_idx == 0
                and abs(r) >= 0.01  # ほぼゼロは最重要扱いしない
            )
            bg_color = _QColorSens("#fff9c4") if is_most_important else None  # 薄い黄色

            # パラメータ名（最重要には⭐マーク追加）
            pk_display = f"⭐ {pk}  [最重要]" if is_most_important else pk
            pk_item = QTableWidgetItem(pk_display)
            if is_most_important:
                from PySide6.QtGui import QFont as _QFontS
                f = _QFontS()
                f.setBold(True)
                pk_item.setFont(f)
                pk_item.setForeground(_QColorSens("#7f5000"))
            if bg_color:
                pk_item.setBackground(bg_color)
            self._stat_table.setItem(row, 0, pk_item)

            # 相関係数
            r_item = QTableWidgetItem(f"{r:.4f}")
            r_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if bg_color:
                r_item.setBackground(bg_color)
            self._stat_table.setItem(row, 1, r_item)

            # 影響度
            abs_item = QTableWidgetItem(f"{abs(r):.4f}")
            abs_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if bg_color:
                abs_item.setBackground(bg_color)
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
            if bg_color:
                dir_item.setBackground(bg_color)
            self._stat_table.setItem(row, 3, dir_item)

            # データ点数
            n_item = QTableWidgetItem(str(n))
            n_item.setTextAlignment(Qt.AlignCenter)
            if bg_color:
                n_item.setBackground(bg_color)
            self._stat_table.setItem(row, 4, n_item)
