"""
app/ui/result_chart_widget.py
解析結果グラフウィジェット。

matplotlib の FigureCanvas を PySide6 に埋め込み、
層別応答値を横棒グラフ（Y軸 = 層番号）で表示します。

タブ構成:
  Tab 0: グラフ表示   — 各応答値をコンボボックスで切替
  Tab 1: テキスト要約 — サマリーをプレーンテキストで表示

UX改善:
  改善A: グラフ画像クリップボードコピーボタン（📋）を追加。
         クリックでグラフをPNG画像としてクリップボードにコピー。
         Wordやメール等への貼り付けが一瞬で行える。
  改善B: Matplotlibナビゲーションツールバーを追加。
         ズーム・パン・ホームリセット・画像保存が可能になる。
  UX改善③新: 応答指標 ◄ / ► ナビゲーションボタンを追加。
         コンボボックスの両隣に「◄」「►」ボタンを配置し、
         クリックするだけで前/次の応答指標に切り替えられます。
         7種の指標（変位・速度・加速度・層間変形等）を
         ドロップダウンを開かずにテンポよく確認できます。
  UX改善③新2: グラフ拡大ポップアウトボタン（⛶ 拡大）を追加。
         タイトル行の「⛶ 拡大」ボタンをクリックすると、現在表示中の
         グラフを大きなダイアログウィンドウで拡大表示します。
         横棒グラフが小さくて読みにくいとき、層間変形角等の
         数値を確認したいときに役立ちます。
"""

from __future__ import annotations

from io import BytesIO
from typing import Dict, List, Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# matplotlib を PySide6 バックエンドで使用
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

# 日本語フォント設定（環境依存しないようフォールバック込み）
try:
    plt.rcParams["font.family"] = ["MS Gothic", "Meiryo", "IPAGothic", "sans-serif"]
except Exception:
    pass

from app.models import AnalysisCase
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


def _apply_mpl_theme() -> None:
    """matplotlib の rcParams に現在のテーマを適用します。"""
    theme = "dark" if ThemeManager.is_dark() else "light"
    for key, val in MPL_STYLES[theme].items():
        plt.rcParams[key] = val


class _MplCanvas(FigureCanvas):
    """matplotlib Figure を保持するキャンバス。"""

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
        self.updateGeometry()

    def clear_plot(self) -> None:
        self.ax.clear()
        self.draw()

    def apply_theme(self) -> None:
        """テーマ変更時にキャンバスの色を更新します。"""
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        self.fig.set_facecolor(MPL_STYLES[theme]["figure.facecolor"])
        self.ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])


class ResultChartWidget(QWidget):
    """
    解析結果を matplotlib グラフで表示するウィジェット。

    Public API
    ----------
    show_case(case)  — 1ケースの結果を表示します
    clear()          — 表示をクリアします
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_case: Optional[AnalysisCase] = None
        self._criteria: Optional[PerformanceCriteria] = None
        self._show_criteria: bool = True
        # DYC サブケース選択状態
        # None = case.result_summary を使用（DYC なし or 選択なし）
        # int  = case.dyc_results[n] を使用
        self._active_dyc_index: Optional[int] = None
        self._dyc_buttons: List[QPushButton] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_criteria(self, criteria: Optional[PerformanceCriteria]) -> None:
        """目標性能基準を設定します。グラフに基準線が表示されます。"""
        self._criteria = criteria
        self._update_chart()

    def show_case(self, case: AnalysisCase) -> None:
        """指定ケースの結果を表示します。"""
        self._current_case = case
        self._active_dyc_index = None
        self._title_label.setText(f"ケース: <b>{case.name}</b>")
        self._rebuild_dyc_selector(case)
        self._update_chart()
        self._update_text(case)
        # UX改善③新2: ケースが設定されたら拡大ボタンを有効化
        if hasattr(self, "_btn_popout"):
            self._btn_popout.setEnabled(bool(case.result_summary))

    def clear(self) -> None:
        """表示をクリアします。"""
        self._current_case = None
        self._active_dyc_index = None
        self._title_label.setText("<b>結果グラフ</b>")
        # UX改善③新2: クリア時は拡大ボタンを無効化
        if hasattr(self, "_btn_popout"):
            self._btn_popout.setEnabled(False)
        self._dyc_panel.setVisible(False)
        self._canvas.clear_plot()
        self._canvas.ax.text(
            0.5, 0.5,
            "ケースを選択すると結果を表示します",
            ha="center", va="center",
            transform=self._canvas.ax.transAxes,
            fontsize=11, color="gray",
        )
        self._canvas.draw()
        self._text.setPlainText("（ケースを選択すると結果を表示します）")

    def update_theme(self) -> None:
        """テーマ変更時にグラフの色を更新します。"""
        self._canvas.apply_theme()
        self._update_chart()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # タイトル行
        title_row = QHBoxLayout()
        self._title_label = QLabel("<b>結果グラフ</b>")
        title_row.addWidget(self._title_label)
        title_row.addStretch()

        # 基準線表示チェックボックス
        self._criteria_cb = QCheckBox("基準線")
        self._criteria_cb.setChecked(True)
        self._criteria_cb.setToolTip("目標性能基準の上限値をグラフに表示")
        self._criteria_cb.stateChanged.connect(self._on_criteria_toggle)
        title_row.addWidget(self._criteria_cb)

        # 応答値選択コンボボックス（UX改善③新: ◄ / ► ボタン付き）
        title_row.addWidget(QLabel("表示項目:"))

        # ◄ 前の指標ボタン
        self._btn_prev_item = QPushButton("◄")
        self._btn_prev_item.setFixedWidth(28)
        self._btn_prev_item.setFixedHeight(24)
        self._btn_prev_item.setToolTip(
            "前の応答指標を表示します\n"
            "（最大応答相対変位 → 最大転倒モーメント → … の順で循環）"
        )
        self._btn_prev_item.setStyleSheet("font-size: 11px; padding: 1px 4px;")
        self._btn_prev_item.clicked.connect(self._prev_response_item)
        title_row.addWidget(self._btn_prev_item)

        self._combo = QComboBox()
        for _, label, unit in _RESPONSE_ITEMS:
            self._combo.addItem(f"{label}  [{unit}]")
        self._combo.currentIndexChanged.connect(self._update_chart)
        title_row.addWidget(self._combo)

        # ► 次の指標ボタン
        self._btn_next_item = QPushButton("►")
        self._btn_next_item.setFixedWidth(28)
        self._btn_next_item.setFixedHeight(24)
        self._btn_next_item.setToolTip(
            "次の応答指標を表示します\n"
            "（最大応答相対変位 → 最大応答相対速度 → … の順で循環）"
        )
        self._btn_next_item.setStyleSheet("font-size: 11px; padding: 1px 4px;")
        self._btn_next_item.clicked.connect(self._next_response_item)
        title_row.addWidget(self._btn_next_item)

        # 改善A: グラフ画像クリップボードコピーボタン
        btn_copy_chart = QPushButton("📋 コピー")
        btn_copy_chart.setToolTip("現在のグラフをクリップボードに画像コピーします（Word・メールへ貼り付け可）")
        btn_copy_chart.setFixedHeight(24)
        btn_copy_chart.setStyleSheet("font-size: 11px; padding: 1px 8px;")
        btn_copy_chart.clicked.connect(self._copy_chart_to_clipboard)
        title_row.addWidget(btn_copy_chart)

        # UX改善③新2: グラフ拡大ポップアウトボタン
        self._btn_popout = QPushButton("⛶ 拡大")
        self._btn_popout.setToolTip(
            "現在のグラフを大きなウィンドウで拡大表示します\n"
            "（細かい数値や層ごとの傾向をより読みやすく確認できます）"
        )
        self._btn_popout.setFixedHeight(24)
        self._btn_popout.setMaximumWidth(68)
        self._btn_popout.setStyleSheet("font-size: 11px; padding: 1px 8px;")
        self._btn_popout.setEnabled(False)  # ケース選択前は無効
        self._btn_popout.clicked.connect(self._popout_chart)
        title_row.addWidget(self._btn_popout)

        layout.addLayout(title_row)

        # ---- DYC サブケース選択パネル ----
        # s8i 内の DYC ケースが複数ある場合に表示されるトグルボタン行
        self._dyc_panel = QFrame()
        self._dyc_panel.setFrameShape(QFrame.StyledPanel)
        self._dyc_panel.setStyleSheet("QFrame { background: transparent; }")
        dyc_panel_layout = QHBoxLayout(self._dyc_panel)
        dyc_panel_layout.setContentsMargins(4, 2, 4, 2)
        dyc_panel_layout.setSpacing(4)
        self._dyc_label = QLabel("s8i解析ケース:")
        self._dyc_label.setStyleSheet("font-size: 11px; color: gray;")
        dyc_panel_layout.addWidget(self._dyc_label)
        # スクロール可能なボタンエリア
        self._dyc_scroll = QScrollArea()
        self._dyc_scroll.setWidgetResizable(True)
        self._dyc_scroll.setMaximumHeight(40)
        self._dyc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._dyc_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dyc_scroll.setFrameShape(QFrame.NoFrame)
        self._dyc_btn_container = QWidget()
        self._dyc_btn_layout = QHBoxLayout(self._dyc_btn_container)
        self._dyc_btn_layout.setContentsMargins(0, 0, 0, 0)
        self._dyc_btn_layout.setSpacing(4)
        self._dyc_btn_layout.addStretch()
        self._dyc_scroll.setWidget(self._dyc_btn_container)
        dyc_panel_layout.addWidget(self._dyc_scroll, stretch=1)
        self._dyc_panel.setVisible(False)  # 初期は非表示
        layout.addWidget(self._dyc_panel)

        # タブ
        self._tabs = QTabWidget()

        # -- グラフタブ --
        chart_tab = QWidget()
        chart_layout = QVBoxLayout(chart_tab)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)
        self._canvas = _MplCanvas(self)
        # 改善B: Matplotlibナビゲーションツールバー（ズーム・パン・ホーム・保存）
        self._nav_toolbar = NavigationToolbar(self._canvas, self)
        self._nav_toolbar.setMaximumHeight(30)
        chart_layout.addWidget(self._nav_toolbar)
        chart_layout.addWidget(self._canvas)
        self._tabs.addTab(chart_tab, "グラフ")

        # -- テキストタブ --
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        text_layout.setContentsMargins(0, 0, 0, 0)
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlaceholderText("（ケースを選択すると結果を表示します）")
        text_layout.addWidget(self._text)
        self._tabs.addTab(text_tab, "テキスト")

        layout.addWidget(self._tabs)

        # 初期状態
        self.clear()

    def _on_criteria_toggle(self, state: int) -> None:
        """基準線表示のオン・オフ切替。"""
        self._show_criteria = bool(state)
        self._update_chart()

    # ------------------------------------------------------------------
    # UX改善③新: 応答指標ナビゲーション
    # ------------------------------------------------------------------

    def _prev_response_item(self) -> None:
        """
        UX改善③新: 現在表示中の応答指標の1つ前に切り替えます。

        インデックスが 0 の場合は末尾（最後の指標）に循環します。
        """
        idx = self._combo.currentIndex()
        n = self._combo.count()
        if n > 0:
            self._combo.setCurrentIndex((idx - 1) % n)

    def _next_response_item(self) -> None:
        """
        UX改善③新: 現在表示中の応答指標の次に切り替えます。

        インデックスが末尾の場合は先頭（最初の指標）に循環します。
        """
        idx = self._combo.currentIndex()
        n = self._combo.count()
        if n > 0:
            self._combo.setCurrentIndex((idx + 1) % n)

    # ------------------------------------------------------------------
    # DYC サブケース選択
    # ------------------------------------------------------------------

    def _rebuild_dyc_selector(self, case: AnalysisCase) -> None:
        """DYC サブケース選択ボタンを再構築します。"""
        # 既存ボタンをクリア
        for btn in self._dyc_buttons:
            btn.deleteLater()
        self._dyc_buttons.clear()
        # ストレッチも削除して再追加
        while self._dyc_btn_layout.count():
            item = self._dyc_btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        dyc_results = getattr(case, "dyc_results", [])
        if not dyc_results:
            self._dyc_panel.setVisible(False)
            return

        # DYCケースが存在する場合: ボタンを生成
        self._dyc_panel.setVisible(True)

        for i, dr in enumerate(dyc_results):
            case_no = dr.get("case_no", i + 1)
            case_name = dr.get("case_name", f"D{case_no}")
            run_flag = dr.get("run_flag", 0)
            has_result = dr.get("has_result", False)

            label = f"D{case_no}: {case_name}"
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setStyleSheet("font-size: 10px; padding: 0 6px;")

            if not run_flag:
                btn.setEnabled(False)
                btn.setToolTip(f"[スキップ] {label}  (run_flag=0)")
                btn.setStyleSheet("font-size: 10px; padding: 0 6px; color: gray;")
            elif has_result:
                btn.setToolTip(f"[解析済] {label}")
                btn.setChecked(self._active_dyc_index == i)
            else:
                btn.setEnabled(False)
                btn.setToolTip(f"[結果なし] {label}  (解析対象だが結果ファイル未検出)")
                btn.setStyleSheet("font-size: 10px; padding: 0 6px; color: orange;")

            # クロージャで i を固定
            def _make_slot(idx: int):
                def _slot(checked: bool) -> None:
                    self._on_dyc_button_clicked(idx, checked)
                return _slot

            btn.clicked.connect(_make_slot(i))
            self._dyc_btn_layout.addWidget(btn)
            self._dyc_buttons.append(btn)

        self._dyc_btn_layout.addStretch()

        # 最初のhas_result=Trueのケースを自動選択
        if self._active_dyc_index is None:
            for i, dr in enumerate(dyc_results):
                if dr.get("has_result") and dr.get("run_flag"):
                    self._set_active_dyc(i)
                    break

    def _on_dyc_button_clicked(self, idx: int, checked: bool) -> None:
        """DYCボタンがクリックされた時の処理。"""
        if checked:
            self._set_active_dyc(idx)
        else:
            # 既にアクティブなボタンを再クリック → デセレクト（main result_summary へ）
            self._active_dyc_index = None
            for b in self._dyc_buttons:
                b.setChecked(False)
            self._update_chart()
            if self._current_case:
                self._update_text(self._current_case)

    def _set_active_dyc(self, idx: int) -> None:
        """指定インデックスのDYCケースをアクティブにします。"""
        self._active_dyc_index = idx
        for i, btn in enumerate(self._dyc_buttons):
            btn.setChecked(i == idx)
        self._update_chart()
        if self._current_case:
            self._update_text(self._current_case)

    def _get_active_result_data(self) -> tuple:
        """
        現在アクティブな (result_data, summary, label) を返します。

        Returns: (result_data: dict, summary: dict, sub_label: str)
        """
        case = self._current_case
        if case is None:
            return {}, {}, ""

        dyc_results = getattr(case, "dyc_results", [])
        if dyc_results and self._active_dyc_index is not None:
            idx = self._active_dyc_index
            if 0 <= idx < len(dyc_results):
                dr = dyc_results[idx]
                rd = dr.get("result_data", {})
                rs = dr.get("result_summary", {})
                sub = f"D{dr.get('case_no','?')}: {dr.get('case_name','')}"
                return rd, rs, sub

        # フォールバック: case.result_summary
        rs = case.result_summary or {}
        rd = rs.get("result_data", {})
        return rd, rs, ""

    # ------------------------------------------------------------------
    # 改善A: グラフ画像クリップボードコピー
    # ------------------------------------------------------------------

    def _copy_chart_to_clipboard(self) -> None:
        """現在のグラフをPNG画像としてクリップボードにコピーします。

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
                # ステータスバーで通知（親ウィジェットを辿って MainWindow を探す）
                parent = self.parent()
                while parent is not None:
                    if hasattr(parent, "statusBar"):
                        parent.statusBar().showMessage("グラフ画像をクリップボードにコピーしました", 3000)
                        break
                    parent = parent.parent()
        except Exception as exc:
            pass  # コピー失敗は無視（ユーザー操作を妨げない）

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_chart(self) -> None:
        """コンボボックスの選択に応じてグラフを再描画します。"""
        ax = self._canvas.ax
        ax.clear()

        case = self._current_case
        if case is None or not case.result_summary:
            ax.text(0.5, 0.5, "データなし",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            self._canvas.draw()
            return

        idx = self._combo.currentIndex()
        key, label, unit = _RESPONSE_ITEMS[idx]

        # アクティブな result_data を取得（DYC サブケース or メイン）
        result_data, summary, sub_label = self._get_active_result_data()
        floor_dict: dict = result_data.get(key, {})

        # result_data にない場合はスカラー値のみある可能性
        if not floor_dict:
            scalar = summary.get(key)
            if scalar is not None:
                floor_dict = {1: scalar}

        if not floor_dict:
            ax.text(0.5, 0.5, "このケースに\nデータがありません",
                    ha="center", va="center",
                    transform=ax.transAxes, color="gray")
            self._canvas.draw()
            return

        floors = sorted(floor_dict.keys())
        values = [floor_dict[f] for f in floors]

        # 横棒グラフ（Y 軸 = 層番号）
        colors = plt.cm.Blues(np.linspace(0.4, 0.85, len(floors)))
        bars = ax.barh(
            [str(f) for f in floors],
            values,
            color=colors,
            edgecolor="steelblue",
            linewidth=0.5,
        )
        ax.bar_label(bars, fmt="%.4g", padding=3, fontsize=8)
        ax.set_xlabel(f"{label}  [{unit}]", fontsize=9)
        ax.set_ylabel("層", fontsize=9)
        # タイトルにDYCサブケース名も表示
        title = f"{case.name} — {label}"
        if sub_label:
            title += f"\n({sub_label})"
        ax.set_title(title, fontsize=10)
        ax.tick_params(labelsize=8)
        ax.grid(axis="x", linestyle="--", alpha=0.5)

        # --- 性能基準線のオーバーレイ ---
        self._draw_criteria_line(ax, key)

        self._canvas.fig.tight_layout()
        self._canvas.draw()

    def _popout_chart(self) -> None:
        """
        UX改善③新2: 現在のグラフを大きなダイアログウィンドウで拡大表示します。

        メインウィンドウに埋め込まれたグラフは小さくて細部が読みにくい場合があります。
        このボタンを押すことで、同じデータをより大きなキャンバスで確認できます。
        ダイアログはリサイズ可能で、matplotlib のナビゲーションツールバーも付属します。
        """
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        case = self._current_case
        if case is None or not case.result_summary:
            return

        idx = self._combo.currentIndex()
        key, label, unit = _RESPONSE_ITEMS[idx]
        result_data, summary, sub_label = self._get_active_result_data()
        floor_dict: dict = result_data.get(key, {})
        if not floor_dict:
            scalar = summary.get(key)
            if scalar is not None:
                floor_dict = {1: scalar}

        dlg = QDialog(self)
        dlg.setWindowTitle(f"拡大表示 — {case.name} : {label}")
        dlg.resize(820, 640)
        dlg.setSizeGripEnabled(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(8, 8, 8, 4)

        # 大きめのキャンバスを作成
        _apply_mpl_theme()
        theme = "dark" if ThemeManager.is_dark() else "light"
        facecolor = MPL_STYLES[theme]["figure.facecolor"]
        pop_fig = Figure(figsize=(9, 7), tight_layout=True, facecolor=facecolor)
        pop_ax = pop_fig.add_subplot(111)
        pop_ax.set_facecolor(MPL_STYLES[theme]["axes.facecolor"])
        pop_canvas = FigureCanvas(pop_fig)
        pop_canvas.setSizePolicy(
            pop_canvas.sizePolicy().horizontalPolicy(),
            pop_canvas.sizePolicy().verticalPolicy(),
        )
        pop_toolbar = NavigationToolbar(pop_canvas, dlg)
        layout.addWidget(pop_toolbar)
        layout.addWidget(pop_canvas, stretch=1)

        # グラフを描画
        if floor_dict:
            floors = sorted(floor_dict.keys())
            values = [floor_dict[f] for f in floors]
            colors = plt.cm.Blues(np.linspace(0.4, 0.85, len(floors)))
            bars = pop_ax.barh(
                [str(f) for f in floors],
                values,
                color=colors,
                edgecolor="steelblue",
                linewidth=0.7,
            )
            pop_ax.bar_label(bars, fmt="%.4g", padding=4, fontsize=10)
            pop_ax.set_xlabel(f"{label}  [{unit}]", fontsize=11)
            pop_ax.set_ylabel("層", fontsize=11)
            title = f"{case.name} — {label}"
            if sub_label:
                title += f"  ({sub_label})"
            pop_ax.set_title(title, fontsize=12, pad=10)
            pop_ax.tick_params(labelsize=10)
            pop_ax.grid(axis="x", linestyle="--", alpha=0.5)
            # 性能基準線
            self._draw_criteria_line(pop_ax, key)
        else:
            pop_ax.text(0.5, 0.5, "データなし", ha="center", va="center",
                        transform=pop_ax.transAxes, color="gray", fontsize=14)

        pop_fig.tight_layout()
        pop_canvas.draw()

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.accept)
        layout.addWidget(btns)
        dlg.exec()

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
                ax.legend(fontsize=8, loc="lower right")
                break

    def _update_text(self, case: AnalysisCase) -> None:
        """テキストタブのサマリーを更新します。"""
        result_data, summary, sub_label = self._get_active_result_data()

        if not summary and not result_data:
            self._text.setPlainText("（解析結果なし）")
            return

        header = f"=== {case.name} 解析結果サマリー ==="
        if sub_label:
            header += f"\n    [{sub_label}]"
        lines = [header, ""]

        for key, label, unit in _RESPONSE_ITEMS:
            val = summary.get(key)
            if val is not None:
                lines.append(f"  {label:<22}: {val:>12.5g}  [{unit}]")

        # 層別データ
        if result_data:
            lines.append("\n--- 層別応答値 ---")
            for key, label, unit in _RESPONSE_ITEMS:
                floor_dict = result_data.get(key, {})
                if not floor_dict:
                    continue
                lines.append(f"\n  {label}  [{unit}]:")
                for floor_no in sorted(floor_dict.keys()):
                    lines.append(f"    {floor_no}層: {floor_dict[floor_no]:>12.5g}")

        # DYC サブケース一覧（ケースに dyc_results がある場合）
        dyc_results = getattr(case, "dyc_results", [])
        if dyc_results:
            lines.append("\n--- s8i 解析ケース一覧 ---")
            for dr in dyc_results:
                flag_s = "解析する  " if dr.get("run_flag") else "解析しない"
                has_s = "✓結果あり" if dr.get("has_result") else "結果なし"
                lines.append(
                    f"  D{dr.get('case_no','?')}: {dr.get('case_name',''):<20} "
                    f"[{flag_s}] {has_s}"
                )

        self._text.setPlainText("\n".join(lines))