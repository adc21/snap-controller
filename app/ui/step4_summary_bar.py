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
"""

from __future__ import annotations

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


class Step4SummaryBar(QFrame):
    """
    STEP4（結果・戦略）の上部に常時表示される結果サマリーバー。

    解析済みケースの中から最良ケース・最悪ケースを抽出し、
    主要指標（最大層間変形角）での比較を1行で表示します。

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
        self._setup_ui()
        self._show_empty()

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """サマリーバーのUIを構築します。"""
        is_dark = ThemeManager.is_dark()

        if is_dark:
            bar_bg = "#1e272e"
            border_color = "#37474f"
        else:
            bar_bg = "#f5f7fa"
            border_color = "#cfd8dc"

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

        # ---- 完了件数バッジ ----
        self._count_lbl = QLabel("解析結果: なし")
        count_font = QFont()
        count_font.setPointSize(9)
        self._count_lbl.setFont(count_font)
        self._count_lbl.setStyleSheet("color: gray;")
        outer.addWidget(self._count_lbl)

        # ---- セパレーター ----
        _sep1 = QFrame()
        _sep1.setFrameShape(QFrame.VLine)
        _sep1.setFrameShadow(QFrame.Sunken)
        _sep1.setStyleSheet("color: palette(mid);")
        outer.addWidget(_sep1)

        # ---- 最良ケース ----
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

        # ---- セパレーター ----
        _sep2 = QFrame()
        _sep2.setFrameShape(QFrame.VLine)
        _sep2.setFrameShadow(QFrame.Sunken)
        _sep2.setStyleSheet("color: palette(mid);")
        outer.addWidget(_sep2)

        # ---- 最悪ケース ----
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

        # ---- セパレーター ----
        _sep3 = QFrame()
        _sep3.setFrameShape(QFrame.VLine)
        _sep3.setFrameShadow(QFrame.Sunken)
        _sep3.setStyleSheet("color: palette(mid);")
        outer.addWidget(_sep3)

        # ---- 改善率 ----
        self._improve_lbl = QLabel("")
        self._improve_lbl.setStyleSheet("color: #80cbc4; font-size: 10px;")
        outer.addWidget(self._improve_lbl)

        outer.addStretch()

        # ---- 指標切り替えヒント ----
        _metric_hint = QLabel("基準指標: 最大層間変形角")
        _metric_hint.setStyleSheet("color: gray; font-size: 9px;")
        outer.addWidget(_metric_hint)

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

        # 最大層間変形角で最良/最悪を判断
        def _drift(c: AnalysisCase) -> Optional[float]:
            rs = c.result_summary
            for key in ("max_story_drift", "max_drift"):
                v = rs.get(key)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return None

        scored = [(c, _drift(c)) for c in completed if _drift(c) is not None]
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
        self._best_val_lbl.setText(f"層間変形角: {best_val:.4f} rad")

        # 最悪ケース
        _short_worst = worst_case.name if len(worst_case.name) <= 20 else worst_case.name[:18] + "…"
        self._worst_name_lbl.setText(_short_worst)
        self._worst_val_lbl.setText(f"層間変形角: {worst_val:.4f} rad")

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
