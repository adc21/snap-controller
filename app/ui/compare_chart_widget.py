"""
app/ui/compare_chart_widget.py
複数ケース比較グラフウィジェット。

完了済みの解析ケースを選んで同一グラフに重ねて表示します。

レイアウト:
  ┌──────────────────────────────────────┐
  │ [表示項目コンボ]  [ケース選択リスト]  │
  │ matplotlib グラフ                    │
  └──────────────────────────────────────┘

UX改善:
  改善A: グラフ画像クリップボードコピーボタン（📋）を追加。
  改善B: Matplotlibナビゲーションツールバーを追加（ズーム・パン・保存）。
  UX改善⑤新: グループ別ケース一括選択ドロップダウンを追加。
         ケース選択エリアにグループ名ドロップダウンを配置し、
         選択したグループに属する全ケースをワンクリックでチェックできます。
         グループごとに結果を比較するワークフローを高速化します。
         set_case_groups(groups) で最新のグループ情報を受け取ります。

UX改善（新）: 最良ケース（現在の指標で最小値）の自動ゴールドハイライト。
         選択ケースのうち、現在表示中の指標で最小最大値を持つケース（最も
         応答が小さい = 最も有利なケース）を自動的にゴールド色・太線・
         スター記号でハイライトし、「🏆 最良: {ケース名}」の凡例を追加します。
         グラフを見ながら「どのケースが最もよいか」を即座に把握できます。

UX改善④新: 「完了のみ」クイック選択ボタンを追加。
         上部コントロール行の「全選択」「全解除」ボタンの隣に
         「完了のみ」ボタンを配置しました。クリックすると
         解析が完了しているケースだけをチェックします。
         「全選択」すると未完了ケースもチェックされてしまい
         エラーになるケースを避けたい場合に役立ちます。
"""

from __future__ import annotations

from io import BytesIO

from typing import List, Optional

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
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

from app.models import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria
from .theme import ThemeManager, MPL_STYLES

# 応答値の定義 (key, 日本語ラベル, 単位)
_RESPONSE_ITEMS = [
    ("max_disp",        "最大応答相対変位",    "m"),
    ("max_vel",         "最大応答相対速度",    "m/s"),
    ("max_acc",         "最大応答絶対加速度",  "m/s²"),
    ("max_story_disp",  "最大層間変形",        "m"),
    ("max_story_drift", "最大層間変形角",      "rad"),
    ("shear_coeff",     "せん断力係数",        "—"),
    ("max_otm",         "最大転倒モーメント",  "kN·m"),
]

# グラフ応答値キー → 性能基準キーのマッピング
_CHART_KEY_TO_CRITERIA_KEY = {
    "max_disp": "max_disp",
    "max_vel": "max_vel",
    "max_acc": "max_acc",
    "max_story_disp": "max_story_disp",
    "max_story_drift": "max_drift",
    "shear_coeff": "shear_coeff",
    "max_otm": "max_otm",
}

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


class _MplCanvas(FigureCanvas):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        self.fig = Figure(figsize=(6, 4), tight_layout=True, facecolor=facecolor)
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


class CompareChartWidget(QWidget):
    """
    複数ケースを重ねて比較するグラフウィジェット。

    Public API
    ----------
    set_cases(cases)  — 全ケースリストをセットして選択肢を更新します
    refresh()         — 現在の選択状態でグラフを再描画します
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_cases: List[AnalysisCase] = []
        self._checkboxes: List[tuple[QCheckBox, AnalysisCase]] = []
        self._criteria: Optional[PerformanceCriteria] = None
        self._show_criteria: bool = True
        # UX改善⑤新: グループ別選択用のグループ情報
        self._case_groups: dict = {}
        # UX改善④: ケースリスト絞り込みテキスト
        self._case_filter_text: str = ""
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_criteria(self, criteria: Optional[PerformanceCriteria]) -> None:
        """目標性能基準を設定します。比較グラフに基準線が表示されます。"""
        self._criteria = criteria
        self.refresh()

    def set_cases(self, cases: List[AnalysisCase]) -> None:
        """全ケースリストをセットし、完了済みケースのチェックリストを更新します。"""
        self._all_cases = cases
        self._rebuild_checklist()
        self.refresh()

    def set_case_groups(self, groups: dict) -> None:
        """
        UX改善⑤新: ケースグループ情報を設定し、グループ別選択ドロップダウンを更新します。

        Parameters
        ----------
        groups : dict
            {グループ名: [case_id, ...]} の辞書。
            Project.case_groups をそのまま渡せます。
        """
        self._case_groups = dict(groups) if groups else {}
        self._rebuild_group_combo()

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

        # --- 上部コントロール行 ---
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("表示項目:"))
        self._combo = QComboBox()
        for _, label, unit in _RESPONSE_ITEMS:
            self._combo.addItem(f"{label}  [{unit}]")
        self._combo.currentIndexChanged.connect(self.refresh)
        ctrl_row.addWidget(self._combo)
        ctrl_row.addStretch()

        # 基準線表示チェックボックス
        self._criteria_cb = QCheckBox("基準線")
        self._criteria_cb.setChecked(True)
        self._criteria_cb.setToolTip("目標性能基準の上限値をグラフに表示")
        self._criteria_cb.stateChanged.connect(self._on_criteria_toggle)
        ctrl_row.addWidget(self._criteria_cb)

        btn_all = QPushButton("全選択")
        btn_all.setMaximumWidth(64)
        btn_all.clicked.connect(self._select_all)
        ctrl_row.addWidget(btn_all)

        btn_none = QPushButton("全解除")
        btn_none.setMaximumWidth(64)
        btn_none.clicked.connect(self._deselect_all)
        ctrl_row.addWidget(btn_none)

        # UX改善④新: 完了ケースのみ選択ボタン
        btn_completed = QPushButton("完了のみ")
        btn_completed.setMaximumWidth(68)
        btn_completed.setToolTip(
            "解析が完了しているケースだけをチェックします\n"
            "エラーや未実行のケースを除いて比較したい場合に便利です"
        )
        btn_completed.setStyleSheet("font-size: 11px;")
        btn_completed.clicked.connect(self._select_completed_only)
        ctrl_row.addWidget(btn_completed)

        # 改善A: グラフ画像クリップボードコピーボタン
        btn_copy_chart = QPushButton("📋 コピー")
        btn_copy_chart.setToolTip("現在の比較グラフをクリップボードに画像コピーします（Word・メールへ貼り付け可）")
        btn_copy_chart.setMaximumWidth(80)
        btn_copy_chart.setFixedHeight(24)
        btn_copy_chart.setStyleSheet("font-size: 11px; padding: 1px 8px;")
        btn_copy_chart.clicked.connect(self._copy_chart_to_clipboard)
        ctrl_row.addWidget(btn_copy_chart)

        layout.addLayout(ctrl_row)

        # --- メインエリア: チェックリスト（左）+ グラフ右エリア（右）---
        main_row = QHBoxLayout()

        # ケース選択リスト
        group = QGroupBox("比較するケース")
        group.setMaximumWidth(220)
        group_layout = QVBoxLayout(group)

        # ---- UX改善④: ケース名絞り込みフィルター ----
        from PySide6.QtWidgets import QLineEdit as _QLineEdit
        case_filter_row = QHBoxLayout()
        case_filter_row.setContentsMargins(0, 0, 0, 0)
        case_filter_row.setSpacing(2)
        case_filter_lbl = QLabel("🔍")
        case_filter_lbl.setStyleSheet("font-size: 10px;")
        case_filter_row.addWidget(case_filter_lbl)
        self._case_filter_edit = _QLineEdit()
        self._case_filter_edit.setPlaceholderText("ケース名で絞り込み…")
        self._case_filter_edit.setClearButtonEnabled(True)
        self._case_filter_edit.setFixedHeight(20)
        self._case_filter_edit.setStyleSheet("QLineEdit { font-size: 10px; }")
        self._case_filter_edit.setToolTip(
            "ケース名でチェックボックスリストを絞り込みます。\n"
            "ケース数が多い場合に特定のケースをすばやく見つけられます。"
        )
        self._case_filter_edit.textChanged.connect(self._on_case_filter_changed)
        case_filter_row.addWidget(self._case_filter_edit)
        group_layout.addLayout(case_filter_row)

        # UX改善⑤新: グループ別一括選択ドロップダウン
        from PySide6.QtWidgets import QComboBox as _QComboBox
        group_filter_row = QHBoxLayout()
        group_filter_row.setContentsMargins(0, 0, 0, 0)
        group_filter_row.setSpacing(4)
        group_filter_lbl = QLabel("グループ:")
        group_filter_lbl.setStyleSheet("font-size: 10px; color: #888888;")
        group_filter_row.addWidget(group_filter_lbl)
        self._group_combo = _QComboBox()
        self._group_combo.setToolTip(
            "グループを選択すると、そのグループに属する\n"
            "完了済みケースを一括でチェックします。\n"
            "「すべて」を選ぶと全ケースを選択します。"
        )
        self._group_combo.setStyleSheet("font-size: 10px;")
        self._group_combo.addItem("（グループで選択）")
        self._group_combo.currentIndexChanged.connect(self._on_group_filter_changed)
        group_filter_row.addWidget(self._group_combo, stretch=1)
        group_layout.addLayout(group_filter_row)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._check_container = QWidget()
        self._check_layout = QVBoxLayout(self._check_container)
        self._check_layout.setAlignment(Qt.AlignTop)
        self._check_layout.setSpacing(2)
        self._scroll_area.setWidget(self._check_container)
        group_layout.addWidget(self._scroll_area)

        # UX改善E: 選択件数バッジ（「X件選択中 / Y件完了」）
        self._selection_badge = QLabel("")
        self._selection_badge.setAlignment(Qt.AlignCenter)
        self._selection_badge.setStyleSheet(
            "font-size: 10px; color: #888888; padding: 2px 4px;"
        )
        self._selection_badge.setTextFormat(Qt.RichText)
        group_layout.addWidget(self._selection_badge)

        main_row.addWidget(group)

        # グラフ（ナビゲーションツールバー付き）
        chart_area = QWidget()
        chart_area_layout = QVBoxLayout(chart_area)
        chart_area_layout.setContentsMargins(0, 0, 0, 0)
        chart_area_layout.setSpacing(0)
        self._canvas = _MplCanvas(self)
        # 改善B: Matplotlibナビゲーションツールバー（ズーム・パン・ホーム・保存）
        self._nav_toolbar = NavigationToolbar(self._canvas, self)
        self._nav_toolbar.setMaximumHeight(30)
        chart_area_layout.addWidget(self._nav_toolbar)
        chart_area_layout.addWidget(self._canvas)
        main_row.addWidget(chart_area, stretch=1)

        layout.addLayout(main_row, stretch=1)

        # 初期描画
        self._show_empty()

    # ------------------------------------------------------------------
    # Checklist management
    # ------------------------------------------------------------------

    def _rebuild_checklist(self) -> None:
        """完了済みケースのチェックボックスリストを再構築します。"""
        # 既存チェックボックスを削除
        for cb, _ in self._checkboxes:
            cb.deleteLater()
        self._checkboxes.clear()

        completed = [c for c in self._all_cases
                     if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary]

        if not completed:
            lbl = QLabel("<i>完了済みケースがありません</i>")
            lbl.setObjectName("_empty_label")
            self._check_layout.addWidget(lbl)
            self._selection_badge.setText("")  # UX改善E: バッジをクリア
            return

        # 空ラベルを削除
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
            # UX改善E: 状態変化のたびにバッジとグラフを更新
            cb.stateChanged.connect(self._on_checkbox_changed)
            self._check_layout.addWidget(cb)
            self._checkboxes.append((cb, case))

        # UX改善④: 初期フィルターを適用して表示/非表示を設定
        self._apply_case_filter()
        self._update_selection_badge()

    def _on_checkbox_changed(self) -> None:
        """UX改善E: チェックボックスの状態変化時にバッジとグラフを更新します。"""
        self._update_selection_badge()
        self.refresh()

    def _update_selection_badge(self) -> None:
        """UX改善E: 選択件数バッジ（「X件選択中 / Y件完了」）を更新します。"""
        total = len(self._checkboxes)
        selected = sum(1 for cb, _ in self._checkboxes if cb.isChecked())
        if total == 0:
            self._selection_badge.setText("")
            return
        if selected == 0:
            self._selection_badge.setText(
                f"<span style='color:#ef5350;'>0件選択中</span> / {total}件完了"
            )
        elif selected == total:
            self._selection_badge.setText(
                f"<b style='color:#4caf50;'>{selected}件すべて選択中</b>"
            )
        else:
            self._selection_badge.setText(
                f"<b style='color:#1976d2;'>{selected}件選択中</b> / {total}件完了"
            )

    # ------------------------------------------------------------------
    # UX改善④: ケース名フィルター
    # ------------------------------------------------------------------

    def _on_case_filter_changed(self, text: str) -> None:
        """ケース名フィルターテキスト変更時にチェックボックスの表示/非表示を更新します。"""
        self._case_filter_text = text.strip().lower()
        self._apply_case_filter()

    def _apply_case_filter(self) -> None:
        """
        UX改善④: フィルターテキストに基づいてチェックボックスの表示/非表示を切り替えます。

        空テキストの場合は全件表示。ケース名にキーワードが含まれる行のみ表示します。
        """
        ftext = self._case_filter_text
        for cb, case in self._checkboxes:
            if not ftext or ftext in case.name.lower():
                cb.setVisible(True)
            else:
                cb.setVisible(False)

    def _select_all(self) -> None:
        for cb, _ in self._checkboxes:
            cb.setChecked(True)
        self._update_selection_badge()

    def _deselect_all(self) -> None:
        for cb, _ in self._checkboxes:
            cb.setChecked(False)
        self._update_selection_badge()

    def _select_completed_only(self) -> None:
        """
        UX改善④新: 解析が完了しているケースだけをチェックします。

        「全選択」だとエラーや未実行ケースも含まれてしまうため、
        比較グラフに完了済みケースだけを表示したい場合に使います。
        完了ケースが1件もない場合はチェックされないケースのみになります。
        """
        for cb, case in self._checkboxes:
            cb.setChecked(case.status == AnalysisCaseStatus.COMPLETED)
        self._update_selection_badge()
        self.refresh()

    # ------------------------------------------------------------------
    # UX改善⑤新: グループ別ケース一括選択
    # ------------------------------------------------------------------

    def _rebuild_group_combo(self) -> None:
        """
        UX改善⑤新: グループ別選択コンボボックスの内容を再構築します。

        グループが存在する場合のみ、グループ名一覧を表示します。
        グループが1件もない場合はデフォルトのみ表示します。
        """
        self._group_combo.blockSignals(True)
        self._group_combo.clear()
        self._group_combo.addItem("（グループで選択）")
        for gname in sorted(self._case_groups.keys()):
            self._group_combo.addItem(gname)
        self._group_combo.blockSignals(False)
        # グループがない場合はコンボを無効化して視認性を下げる
        has_groups = bool(self._case_groups)
        self._group_combo.setEnabled(has_groups)
        self._group_combo.setToolTip(
            "グループを選択すると、そのグループに属する完了済みケースを一括でチェックします。"
            if has_groups
            else "ケースにグループを設定すると、ここでグループ別選択ができます。"
        )

    def _on_group_filter_changed(self, index: int) -> None:
        """
        UX改善⑤新: グループが選択されたとき、そのグループのケースだけをチェックします。

        「（グループで選択）」が選ばれた場合は何もしません（トリガー用プレースホルダー）。
        グループ名が選ばれた場合は、そのグループに属する完了済みケースのみをチェックし、
        その他のケースはアンチェックします。操作後はプレースホルダーに戻します。
        """
        selected_group = self._group_combo.currentText()
        if index == 0 or selected_group == "（グループで選択）":
            return  # プレースホルダー選択は無視

        # そのグループに属するケースIDのセット
        group_case_ids = set(self._case_groups.get(selected_group, []))

        # チェックボックスを更新
        changed = False
        for cb, case in self._checkboxes:
            new_state = case.id in group_case_ids
            if cb.isChecked() != new_state:
                cb.blockSignals(True)
                cb.setChecked(new_state)
                cb.blockSignals(False)
                changed = True

        if changed:
            self._update_selection_badge()
            self.refresh()

        # 選択後はプレースホルダーに戻す（次回も同じグループを再選択できるように）
        self._group_combo.blockSignals(True)
        self._group_combo.setCurrentIndex(0)
        self._group_combo.blockSignals(False)

    def _on_criteria_toggle(self, state: int) -> None:
        """基準線表示のオン・オフ切替。"""
        self._show_criteria = bool(state)
        self.refresh()

    # ------------------------------------------------------------------
    # 改善A: グラフ画像クリップボードコピー
    # ------------------------------------------------------------------

    def _copy_chart_to_clipboard(self) -> None:
        """現在の比較グラフをPNG画像としてクリップボードにコピーします。

        Word・PowerPoint・メールクライアントなど任意のアプリケーションに
        そのまま Ctrl+V で貼り付けることができます。
        """
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
                        parent.statusBar().showMessage("比較グラフをクリップボードにコピーしました", 3000)
                        break
                    parent = parent.parent()
        except Exception:
            pass

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

        idx = self._combo.currentIndex()
        key, label, unit = _RESPONSE_ITEMS[idx]

        # UX改善（新）: 最良ケース（指標の最大値が最小）を事前に特定する
        # 各ケースの指標最大値を計算し、最小のものを「最良ケース」とする
        case_max_values: dict = {}  # {case.id: max_value_across_floors}
        for case, _ in selected:
            result_data = case.result_summary.get("result_data", {})
            floor_dict = result_data.get(key, {})
            if not floor_dict:
                scalar = case.result_summary.get(key)
                if scalar is not None:
                    floor_dict = {1: scalar}
            if floor_dict:
                case_max_values[case.id] = max(floor_dict.values())

        best_case_id: Optional[str] = None
        if len(case_max_values) >= 2:
            # 2件以上のケースに有効データがある場合のみハイライト
            best_case_id = min(case_max_values, key=case_max_values.__getitem__)

        has_data = False
        for case, color_idx in selected:
            result_data = case.result_summary.get("result_data", {})
            floor_dict = result_data.get(key, {})
            if not floor_dict:
                scalar = case.result_summary.get(key)
                if scalar is not None:
                    floor_dict = {1: scalar}
            if not floor_dict:
                continue

            floors = sorted(floor_dict.keys())
            values = [floor_dict[f] for f in floors]

            # UX改善（新）: 最良ケースはゴールド・太線・スターでハイライト
            is_best = (best_case_id is not None and case.id == best_case_id)
            if is_best:
                plot_color = "#FFD700"   # ゴールド
                lw = 2.8
                mk = "*"
                mks = 10
                legend_label = f"🏆 {case.name}（最良）"
                zorder = 10  # 最前面に描画
            else:
                plot_color = _COLORS[color_idx % len(_COLORS)]
                lw = 1.5
                mk = "o"
                mks = 5
                legend_label = case.name
                zorder = 5

            ax.plot(values, floors,
                    marker=mk, markersize=mks,
                    label=legend_label,
                    color=plot_color,
                    linewidth=lw,
                    zorder=zorder)

            # UX改善（新）: 最良ケースの最大値にスターアノテーションを追加
            if is_best and values:
                max_val = max(values)
                max_floor = floors[values.index(max_val)]
                ax.annotate(
                    f"最良\n{max_val:.4g}",
                    xy=(max_val, max_floor),
                    xytext=(8, 4),
                    textcoords="offset points",
                    fontsize=7,
                    color="#FFD700",
                    fontweight="bold",
                    bbox=dict(
                        boxstyle="round,pad=0.2",
                        facecolor="#333333" if ThemeManager.is_dark() else "#fffde7",
                        edgecolor="#FFD700",
                        alpha=0.85,
                    ),
                )
            has_data = True

        if not has_data:
            self._show_empty("選択されたケースにデータがありません")
            return

        ax.set_xlabel(f"{label}  [{unit}]", fontsize=9)
        ax.set_ylabel("層", fontsize=9)
        ax.set_title(f"ケース比較 — {label}", fontsize=10)
        ax.tick_params(labelsize=8)
        ax.grid(linestyle="--", alpha=0.4)

        # --- 性能基準線のオーバーレイ ---
        self._draw_criteria_line(ax, key)

        ax.legend(fontsize=8, loc="best")
        # Y 軸を整数刻みにする
        y_ticks = sorted({f for case, _ in selected
                          for f in case.result_summary.get("result_data", {}).get(key, {}).keys()})
        if y_ticks:
            ax.set_yticks(y_ticks)
        self._canvas.fig.tight_layout()
        self._canvas.draw()

    def _draw_criteria_line(self, ax, chart_key: str) -> None:
        """現在の性能基準に基づいて、グラフ上に縦の基準線を描画します。"""
        if not self._show_criteria or self._criteria is None:
            return
        criteria_key = _CHART_KEY_TO_CRITERIA_KEY.get(chart_key)
        if criteria_key is None:
            return
        for item in self._criteria.items:
            if item.key == criteria_key and item.enabled and item.limit_value is not None:
                ax.axvline(
                    x=item.limit_value,
                    color="red",
                    linestyle="--",
                    linewidth=1.5,
                    alpha=0.8,
                    label=f"基準: {item.limit_value:.4g}",
                )
                break

    def _show_empty(self, msg: str = "比較するケースを選択してください") -> None:
        ax = self._canvas.ax
        ax.clear()
        ax.text(0.5, 0.5, msg,
                ha="center", va="center",
                transform=ax.transAxes,
                fontsize=11, color="gray")
        self._canvas.draw()
