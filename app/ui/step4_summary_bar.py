"""
app/ui/step4_summary_bar.py
STEP4 結果サマリーバー。

UX改善（新）: STEP4（結果・戦略）の上部に常時表示されるコンパクトなサマリーバー。
全解析タブをまたいで「今どの状態にいるか」を一目で把握できます。

表示内容:
  - 完了ケース数 / 全ケース数
  - 最良ケース名（最小層間変形角ベース）と数値
  - 最悪ケース名と数値
  - 最良・最悪の改善率（%）

使い方:
  bar = Step4SummaryBar()
  bar.update_cases(project.cases)  # 解析完了後に呼ぶ

なぜ必要か:
  STEP4 にはダッシュボード・比較チャート・ランキングなど複数タブがあり、
  ユーザーはタブを切り替えながら結果を確認します。
  各タブ内に結果は表示されますが、「どのケースが最も良いか」という
  一番知りたい答えをどのタブでも見られるようにするため、
  常時表示のサマリーバーを設けました。
  全タブ共通のヘッダーとして機能し、タブ切り替えごとに再確認が不要になります。

UX改善（第10回①）: 応答指標切り替えボタン（◄/►）追加。
  サマリーバー右端に ◄ / ► ボタンを追加し、最良・最悪ケースの比較に使う
  応答指標をクリックするだけで切り替えられるようにしました。
  対応指標: 最大層間変形角 / 最大相対変位 / 最大絶対加速度 / 最大相対速度
           / 最大層間変形 / せん断力係数 / 最大転倒モーメント
  指標が変わるたびに最良・最悪ケースの再判定と表示更新を行います。
  これによりタブを切り替えることなく「変位では誰が最良か」
  「加速度では誰が最良か」をすぐに確認できます。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models import AnalysisCase, AnalysisCaseStatus
from .theme import ThemeManager

logger = logging.getLogger(__name__)

# UX改善（第10回①）: 切り替え可能な応答指標リスト
# (result_summary キー, 表示ラベル, 単位, フォーマット文字列)
_METRIC_DEFS = [
    ("max_story_drift", "最大層間変形角",      "rad",   "{:.4f}"),
    ("max_disp",        "最大応答相対変位",    "m",     "{:.4f}"),
    ("max_acc",         "最大応答絶対加速度",  "m/s²",  "{:.3f}"),
    ("max_vel",         "最大応答相対速度",    "m/s",   "{:.4f}"),
    ("max_story_disp",  "最大層間変形",        "m",     "{:.4f}"),
    ("shear_coeff",     "せん断力係数",        "—",     "{:.4f}"),
    ("max_otm",         "最大転倒モーメント",  "kN·m",  "{:.1f}"),
]


class Step4SummaryBar(QFrame):
    """
    STEP4（結果・戦略）の上部に常時表示される結果サマリーバー。

    解析済みケースの中から最良ケース・最悪ケースを抽出し、
    選択した応答指標での比較を1行で表示します。

    UX改善（第10回①）: ◄/► ボタンで応答指標を切り替えられます。

    Signals
    -------
    bestCaseClicked(case_id: str)
        「最良ケース」ラベルをクリックしたときに発火します。
    """

    bestCaseClicked = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self._best_case_id: str = ""
        # UX改善（第10回①）: 現在選択中の指標インデックス
        self._metric_index: int = 0
        # 直近の cases キャッシュ（指標切り替え時の再描画用）
        self._cached_cases: List[AnalysisCase] = []
        self._setup_ui()
        self._show_empty()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """サマリーバーのUIを構築します。"""
        is_dark = ThemeManager.is_dark()
        bar_bg = "#1e272e" if is_dark else "#f5f7fa"
        border_color = "#37474f" if is_dark else "#cfd8dc"
        self.setStyleSheet(
            f"Step4SummaryBar, QFrame {{"
            f"  background-color: {bar_bg};"
            f"  border-bottom: 1px solid {border_color};"
            f"}}"
        )
        self.setMaximumHeight(56)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 6)
        outer.setSpacing(16)

        self._build_count_badge(outer)
        self._add_separator(outer)
        self._build_best_case_section(outer)
        self._add_separator(outer)
        self._build_worst_case_section(outer)
        self._add_separator(outer)
        self._build_improve_section(outer)
        outer.addStretch()
        self._build_metric_nav(outer)
        self._refresh_metric_label()

    def _add_separator(self, layout: QHBoxLayout) -> None:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: palette(mid);")
        layout.addWidget(sep)

    def _build_count_badge(self, outer: QHBoxLayout) -> None:
        self._count_lbl = QLabel("解析結果: なし")
        count_font = QFont()
        count_font.setPointSize(9)
        self._count_lbl.setFont(count_font)
        self._count_lbl.setStyleSheet("color: gray;")
        outer.addWidget(self._count_lbl)

    def _build_best_case_section(self, outer: QHBoxLayout) -> None:
        best_col = QVBoxLayout()
        best_col.setSpacing(1)
        best_col.setContentsMargins(0, 0, 0, 0)

        _best_hdr = QLabel("🏆 最良ケース")
        _best_hdr.setStyleSheet("color: #66bb6a; font-size: 9px; font-weight: bold;")
        best_col.addWidget(_best_hdr)

        self._best_name_btn = QPushButton("—")
        self._best_name_btn.setFlat(True)
        self._best_name_btn.setStyleSheet(
            "QPushButton {"
            "  color: #4caf50; font-size: 11px; font-weight: bold;"
            "  text-align: left; padding: 0; border: none; background: transparent;"
            "}"
            "QPushButton:hover { color: #81c784; text-decoration: underline; }"
        )
        self._best_name_btn.setToolTip("クリックするとこのケースの解析結果に移動します")
        self._best_name_btn.clicked.connect(
            lambda: self.bestCaseClicked.emit(self._best_case_id) if self._best_case_id else None
        )
        best_col.addWidget(self._best_name_btn)
        outer.addLayout(best_col)

        self._best_val_lbl = QLabel("")
        self._best_val_lbl.setStyleSheet("color: #a5d6a7; font-size: 10px;")
        outer.addWidget(self._best_val_lbl)

    def _build_worst_case_section(self, outer: QHBoxLayout) -> None:
        worst_col = QVBoxLayout()
        worst_col.setSpacing(1)
        worst_col.setContentsMargins(0, 0, 0, 0)

        _worst_hdr = QLabel("📉 最悪ケース")
        _worst_hdr.setStyleSheet("color: #ef9a9a; font-size: 9px; font-weight: bold;")
        worst_col.addWidget(_worst_hdr)

        self._worst_name_lbl = QLabel("—")
        self._worst_name_lbl.setStyleSheet("color: #ef9a9a; font-size: 11px;")
        worst_col.addWidget(self._worst_name_lbl)
        outer.addLayout(worst_col)

        self._worst_val_lbl = QLabel("")
        self._worst_val_lbl.setStyleSheet("color: #ef9a9a; font-size: 10px;")
        outer.addWidget(self._worst_val_lbl)

    def _build_improve_section(self, outer: QHBoxLayout) -> None:
        self._improve_lbl = QLabel("")
        self._improve_lbl.setStyleSheet("color: #80cbc4; font-size: 10px;")
        outer.addWidget(self._improve_lbl)

    def _build_metric_nav(self, outer: QHBoxLayout) -> None:
        self._metric_label = QLabel()
        self._metric_label.setStyleSheet(
            "color: #90a4ae; font-size: 9px; padding: 0 4px;"
        )
        outer.addWidget(self._metric_label)

        nav_btn_style = (
            "QPushButton {"
            "  font-size: 10px; border: 1px solid #546e7a; border-radius: 3px;"
            "  background: transparent; color: #90a4ae;"
            "}"
            "QPushButton:hover { background: #546e7a; color: white; }"
        )

        self._btn_prev_metric = QPushButton("◄")
        self._btn_prev_metric.setFixedSize(22, 22)
        self._btn_prev_metric.setStyleSheet(nav_btn_style)
        self._btn_prev_metric.setToolTip("前の応答指標に切り替えます")
        self._btn_prev_metric.clicked.connect(self._prev_metric)
        outer.addWidget(self._btn_prev_metric)

        self._btn_next_metric = QPushButton("►")
        self._btn_next_metric.setFixedSize(22, 22)
        self._btn_next_metric.setStyleSheet(nav_btn_style)
        self._btn_next_metric.setToolTip("次の応答指標に切り替えます")
        self._btn_next_metric.clicked.connect(self._next_metric)
        outer.addWidget(self._btn_next_metric)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_cases(self, cases: List[AnalysisCase]) -> None:
        """
        解析ケース一覧を受け取り、サマリーバーを更新します。

        完了ケースがない場合は「解析結果なし」を表示します。

        Parameters
        ----------
        cases : list of AnalysisCase
            プロジェクト内の全解析ケース。
        """
        # UX改善（第10回①）: ケースをキャッシュして指標切り替え時に再利用
        self._cached_cases = list(cases)
        self._redraw_with_current_metric()

    def _redraw_with_current_metric(self) -> None:
        """現在選択中の指標で最良/最悪ケースを再計算して表示を更新します。"""
        cases = self._cached_cases
        completed = [
            c for c in cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        total = len(cases)
        done_count = len(completed)

        if done_count == 0:
            self._show_empty()
            self._count_lbl.setText(
                f"解析完了: 0 / {total} 件"
                if total > 0 else "解析結果: なし"
            )
            return

        # 完了件数
        self._count_lbl.setText(f"完了: {done_count} / {total} 件")
        self._count_lbl.setStyleSheet("color: #80cbc4; font-size: 9px;")

        # UX改善（第10回①）: 現在選択中の指標で最良/最悪を判断
        metric_key, metric_label, metric_unit, metric_fmt = _METRIC_DEFS[self._metric_index]

        def _get_val(c: AnalysisCase) -> Optional[float]:
            rs = c.result_summary
            # max_story_drift は max_drift とも呼ばれる場合がある
            for key in (metric_key, "max_drift" if metric_key == "max_story_drift" else metric_key):
                v = rs.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        logger.debug("サマリー値変換失敗: %s=%s", key, v)
            return None

        scored = [(c, _get_val(c)) for c in completed if _get_val(c) is not None]
        if not scored:
            self._show_empty()
            self._count_lbl.setText(f"完了: {done_count} / {total} 件（指標データなし）")
            return

        scored.sort(key=lambda x: x[1])
        best_case, best_val = scored[0]
        worst_case, worst_val = scored[-1]
        self._best_case_id = best_case.id

        # 最良ケース
        _short_name = best_case.name if len(best_case.name) <= 20 else best_case.name[:18] + "…"
        self._best_name_btn.setText(_short_name)
        self._best_val_lbl.setText(
            f"{metric_label}: {metric_fmt.format(best_val)} {metric_unit}"
        )

        # 最悪ケース
        _short_worst = worst_case.name if len(worst_case.name) <= 20 else worst_case.name[:18] + "…"
        self._worst_name_lbl.setText(_short_worst)
        self._worst_val_lbl.setText(
            f"{metric_label}: {metric_fmt.format(worst_val)} {metric_unit}"
        )

        # 改善率（最悪→最良で何%改善したか）
        if worst_val > 0 and len(scored) > 1:
            improvement = (worst_val - best_val) / worst_val * 100.0
            self._improve_lbl.setText(
                f"最良/最悪の差: {improvement:.1f}% 改善"
            )
            self._improve_lbl.setStyleSheet(
                "color: #80cbc4; font-size: 10px; font-weight: bold;"
            )
        else:
            self._improve_lbl.setText("")

    def _show_empty(self) -> None:
        """データなし状態にリセットします。"""
        self._best_case_id = ""
        self._best_name_btn.setText("—")
        self._best_val_lbl.setText("")
        self._worst_name_lbl.setText("—")
        self._worst_val_lbl.setText("")
        self._improve_lbl.setText("")
        self._count_lbl.setStyleSheet("color: gray; font-size: 9px;")

    # ------------------------------------------------------------------
    # UX改善（第10回①）: 指標切り替えハンドラ
    # ------------------------------------------------------------------

    def _prev_metric(self) -> None:
        """◄ ボタン: 前の応答指標に切り替えます。"""
        self._metric_index = (self._metric_index - 1) % len(_METRIC_DEFS)
        self._refresh_metric_label()
        self._redraw_with_current_metric()

    def _next_metric(self) -> None:
        """► ボタン: 次の応答指標に切り替えます。"""
        self._metric_index = (self._metric_index + 1) % len(_METRIC_DEFS)
        self._refresh_metric_label()
        self._redraw_with_current_metric()

    def _refresh_metric_label(self) -> None:
        """現在の指標名ラベルを更新します。"""
        if not hasattr(self, "_metric_label"):
            return
        _, label, _, _ = _METRIC_DEFS[self._metric_index]
        idx_disp = self._metric_index + 1
        total = len(_METRIC_DEFS)
        self._metric_label.setText(f"指標: {label}  ({idx_disp}/{total})")
        self._metric_label.setToolTip(
            "◄/► ボタンで応答指標を切り替えられます\n"
            + "\n".join(f"  {i+1}. {m[1]}" for i, m in enumerate(_METRIC_DEFS))
        )
