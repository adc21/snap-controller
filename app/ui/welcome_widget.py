"""
app/ui/welcome_widget.py
ウェルカム画面 & 最近使ったプロジェクト。

アプリ起動時やプロジェクトが開かれていない時に表示される画面です。
最近使ったプロジェクトのリスト、クイックアクション、ヒントを提供します。

UX改善①: シングルクリック選択 + 「開く」ボタン追加。
  - 最近のプロジェクトをシングルクリックするだけで「開く」ボタンが有効化されます。
  - 「開く」ボタンを押すか、Enterキー / ダブルクリックで即座に開けます。
  - プロジェクトファイルが見つからない場合は「開く」ボタンを自動無効化します。

UX改善（新④）: ワークフロー概要カードを追加。
  「ヒント:」の1行テキストを廃止し、4ステップの概要を視覚的なカードで表示します。
  各カードにはステップ番号・タイトル・何をするかの簡単な説明が含まれており、
  初めてアプリを起動したユーザーが「何をすればいいか」を即座に理解できます。
  カードはウェルカム画面下部に横並びで表示され、常に参照できます。

UX改善（スマートデフォルト）: SNAP実行ファイル未設定時の警告バナー。
  Snap.exe のパスが設定されていない場合、ウェルカム画面の最上部に
  目立つ黄色の警告バナーを表示します。
  「⚙ 設定を開く」ボタンからすぐに設定ダイアログへ誘導するため、
  「プロジェクトを作って解析しようとしたら SNAP が動かなかった」
  という挫折ポイントを未然に防ぎます。
  - show_snap_warning(show: bool) で表示/非表示を外部から制御できます。
  - snapSettingsRequested シグナルで設定ダイアログ開放を外部に委譲します。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QSettings, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# 最近使ったプロジェクト管理
# ---------------------------------------------------------------------------

SETTINGS_ORG = "BAUES"
SETTINGS_APP = "snap-controller"
KEY_RECENT_PROJECTS = "recent_projects"
MAX_RECENT = 10


def get_recent_projects() -> List[dict]:
    """
    最近使ったプロジェクトのリストを返します。

    Returns
    -------
    list of dict
        [{"path": str, "name": str, "last_opened": str}, ...]
    """
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    raw = s.value(KEY_RECENT_PROJECTS, [])
    if not raw or not isinstance(raw, list):
        return []
    # パスの存在確認付きでフィルタ
    result = []
    for item in raw:
        if isinstance(item, dict) and "path" in item:
            result.append(item)
        elif isinstance(item, str):
            result.append({"path": item, "name": Path(item).stem, "last_opened": ""})
    return result[:MAX_RECENT]


def add_recent_project(path: str, name: str = "") -> None:
    """最近使ったプロジェクトを追加/更新します。"""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    recents = get_recent_projects()

    # 既存エントリを削除（最新に移動するため）
    recents = [r for r in recents if r.get("path") != path]

    # 先頭に追加
    entry = {
        "path": path,
        "name": name or Path(path).stem,
        "last_opened": datetime.now().isoformat(),
    }
    recents.insert(0, entry)

    # 最大数に制限
    recents = recents[:MAX_RECENT]
    s.setValue(KEY_RECENT_PROJECTS, recents)


def remove_recent_project(path: str) -> None:
    """最近使ったプロジェクトから削除します。"""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    recents = get_recent_projects()
    recents = [r for r in recents if r.get("path") != path]
    s.setValue(KEY_RECENT_PROJECTS, recents)


def clear_recent_projects() -> None:
    """最近使ったプロジェクトをすべてクリアします。"""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    s.setValue(KEY_RECENT_PROJECTS, [])


# ---------------------------------------------------------------------------
# WelcomeWidget
# ---------------------------------------------------------------------------

class WelcomeWidget(QWidget):
    """
    ウェルカム画面ウィジェット。

    Signals
    -------
    newProjectRequested
        新規プロジェクト作成がリクエストされた。
    openProjectRequested
        プロジェクトを開くがリクエストされた。
    recentProjectSelected(str)
        最近のプロジェクトが選択された（パスを引数に持つ）。
    """

    newProjectRequested = Signal()
    openProjectRequested = Signal()
    recentProjectSelected = Signal(str)
    # UX改善（スマートデフォルト）: 設定ダイアログを開くよう外部に要求
    snapSettingsRequested = Signal()
    # UX改善⑤: デモ体験を開始するよう外部に要求
    demoRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignCenter)

        # ---- UX改善（スマートデフォルト）: SNAP未設定警告バナー ----
        # 画面最上部に固定表示。show_snap_warning() で外部から制御する。
        self._snap_warning_banner = QFrame()
        self._snap_warning_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #fff8e1;"
            "  border: 1px solid #f9a825;"
            "  border-radius: 0px;"
            "}"
        )
        self._snap_warning_banner.setMaximumHeight(44)
        _snap_warn_layout = QHBoxLayout(self._snap_warning_banner)
        _snap_warn_layout.setContentsMargins(16, 6, 16, 6)
        _snap_warn_layout.setSpacing(10)

        _warn_icon = QLabel("⚠️")
        _warn_icon.setStyleSheet("font-size: 16px; background: transparent;")
        _snap_warn_layout.addWidget(_warn_icon)

        _warn_text = QLabel(
            "<b>SNAP 実行ファイル（Snap.exe）が設定されていません。</b>"
            "　解析を実行する前に設定してください。"
        )
        _warn_text.setTextFormat(Qt.RichText)
        _warn_text.setStyleSheet("color: #7f5000; font-size: 11px; background: transparent;")
        _snap_warn_layout.addWidget(_warn_text, stretch=1)

        _snap_warn_btn = QPushButton("⚙ 設定を開く")
        _snap_warn_btn.setStyleSheet(
            "QPushButton {"
            "  color: #7f5000; font-size: 11px; padding: 3px 12px;"
            "  border: 1px solid #f9a825; border-radius: 4px;"
            "  background: transparent; font-weight: bold;"
            "}"
            "QPushButton:hover { background-color: #f9a825; color: #3e2000; }"
        )
        _snap_warn_btn.setToolTip(
            "「設定」ダイアログを開きます。\n"
            "「SNAP 実行ファイル」欄で Snap.exe のパスを指定してください。"
        )
        _snap_warn_btn.clicked.connect(self.snapSettingsRequested.emit)
        _snap_warn_layout.addWidget(_snap_warn_btn)

        # デフォルト非表示（show_snap_warning() で外部から制御）
        self._snap_warning_banner.setVisible(False)
        main_layout.addWidget(self._snap_warning_banner)

        # ---- コンテナ ----
        container = QWidget()
        container.setMaximumWidth(700)
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(20)

        # ---- ヘッダー ----
        header = QLabel("snap-controller")
        header.setAlignment(Qt.AlignCenter)
        header_font = QFont()
        header_font.setPointSize(24)
        header_font.setBold(True)
        header.setFont(header_font)
        container_layout.addWidget(header)

        subtitle = QLabel("SNAP 免振・制振装置設計支援ツール")
        subtitle.setAlignment(Qt.AlignCenter)
        sub_font = QFont()
        sub_font.setPointSize(12)
        subtitle.setFont(sub_font)
        subtitle.setStyleSheet("color: gray;")
        container_layout.addWidget(subtitle)

        container_layout.addSpacing(10)

        # ---- クイックアクション ----
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(16)

        btn_new = self._make_action_button(
            "新規プロジェクト",
            "新しい解析プロジェクトを作成します。\n作成後に STEP1 で .s8i ファイルを読み込んでください。 (Ctrl+N)",
            self.newProjectRequested.emit,
            shortcut_hint="Ctrl+N",
        )
        actions_layout.addWidget(btn_new)

        btn_open = self._make_action_button(
            "プロジェクトを開く (.snapproj)",
            "以前に保存したプロジェクトファイル (.snapproj) を開きます。\n"
            "解析ケース・設定・結果がすべて復元されます。 (Ctrl+O)",
            self.openProjectRequested.emit,
            shortcut_hint="Ctrl+O",
        )
        actions_layout.addWidget(btn_open)

        container_layout.addLayout(actions_layout)

        # ---- UX改善⑤: デモ体験ボタン ----
        # SNAP が未インストールでも「モックデータ」で全ワークフローを体験できます。
        # 初めてのユーザーがアプリの動作を確認するための入口として機能します。
        _demo_frame = QFrame()
        _demo_frame.setFrameShape(QFrame.StyledPanel)
        _demo_frame.setStyleSheet(
            "QFrame {"
            "  background-color: #e8f5e9;"
            "  border: 1px solid #a5d6a7;"
            "  border-radius: 6px;"
            "  padding: 2px;"
            "}"
        )
        _demo_layout = QHBoxLayout(_demo_frame)
        _demo_layout.setContentsMargins(14, 8, 14, 8)
        _demo_layout.setSpacing(12)

        _demo_icon = QLabel("🚀")
        _demo_icon.setStyleSheet("font-size: 18px; background: transparent;")
        _demo_layout.addWidget(_demo_icon)

        _demo_text_col = QVBoxLayout()
        _demo_text_col.setSpacing(1)
        _demo_title = QLabel("<b>まずデモで体験してみる</b>")
        _demo_title.setStyleSheet("color: #1b5e20; font-size: 12px; background: transparent;")
        _demo_title.setTextFormat(Qt.RichText)
        _demo_text_col.addWidget(_demo_title)
        _demo_desc = QLabel(
            "SNAP や .s8i ファイルがなくても、モックデータで①〜④ の全ワークフローを体験できます。"
        )
        _demo_desc.setStyleSheet("color: #2e7d32; font-size: 10px; background: transparent;")
        _demo_desc.setWordWrap(True)
        _demo_text_col.addWidget(_demo_desc)
        _demo_layout.addLayout(_demo_text_col, stretch=1)

        _demo_btn = QPushButton("デモを開始する →")
        _demo_btn.setFixedHeight(34)
        _demo_btn.setMinimumWidth(140)
        _demo_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 12px; font-weight: bold; padding: 6px 16px;"
            "  background-color: #43a047; color: white;"
            "  border: none; border-radius: 5px;"
            "}"
            "QPushButton:hover { background-color: #388e3c; }"
            "QPushButton:pressed { background-color: #2e7d32; }"
        )
        _demo_btn.setToolTip(
            "モックデータを使ってアプリの全機能をデモ体験します。\n\n"
            "・SNAP 実行ファイルや .s8i ファイルは不要です\n"
            "・3つのサンプルケースが自動生成され、仮想の解析結果が表示されます\n"
            "・実際の解析は行われませんが、UI と結果表示の使い方を確認できます"
        )
        _demo_btn.clicked.connect(self.demoRequested.emit)
        _demo_layout.addWidget(_demo_btn)

        container_layout.addWidget(_demo_frame)

        container_layout.addSpacing(10)

        # ---- 最近使ったプロジェクト ----
        recent_label = QLabel("最近使ったプロジェクト")
        recent_font = QFont()
        recent_font.setPointSize(13)
        recent_font.setBold(True)
        recent_label.setFont(recent_font)
        container_layout.addWidget(recent_label)

        # UX改善①: ヒントテキストを追加
        recent_hint = QLabel("クリックで選択、ダブルクリックまたは「開く」ボタンで開きます")
        recent_hint.setStyleSheet("color: gray; font-size: 10px; padding-bottom: 2px;")
        container_layout.addWidget(recent_hint)

        self._recent_list = QListWidget()
        self._recent_list.setMinimumHeight(200)
        self._recent_list.setAlternatingRowColors(True)
        self._recent_list.setSpacing(2)
        self._recent_list.itemDoubleClicked.connect(self._on_recent_double_click)
        # UX改善①: シングルクリックで「開く」ボタンの有効/無効を更新
        self._recent_list.currentItemChanged.connect(self._on_recent_selection_changed)
        container_layout.addWidget(self._recent_list)

        # UX改善①: ボタン行に「開く」ボタンを追加
        btn_row = QHBoxLayout()
        # 「開く」ボタン（シングルクリック選択後に押せる）
        self._open_btn = QPushButton("開く (.snapproj)")
        self._open_btn.setToolTip(
            "選択したプロジェクトファイル (.snapproj) を開きます。\n"
            "解析ケース・設定・結果がすべて復元されます。\n"
            "（ダブルクリックでも開けます）\n\n"
            "※ SNAP の入力ファイル (.s8i) を読み込む場合は、\n"
            "  新規プロジェクト作成後に STEP1 の「.s8i ファイルを読み込む」を使用してください。"
        )
        self._open_btn.setEnabled(False)  # 選択されるまで無効
        self._open_btn.setDefault(True)   # Enterキーで発火
        self._open_btn.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                padding: 4px 16px;
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
            QPushButton:enabled {
                background-color: palette(highlight);
                color: palette(highlighted-text);
                border-color: palette(highlight);
            }
            QPushButton:disabled {
                color: palette(dark);
            }
        """)
        self._open_btn.clicked.connect(self._on_open_selected)
        btn_row.addWidget(self._open_btn)
        btn_row.addStretch()
        self._clear_btn = QPushButton("履歴をクリア")
        self._clear_btn.setMaximumWidth(120)
        self._clear_btn.clicked.connect(self._on_clear_recent)
        btn_row.addWidget(self._clear_btn)
        container_layout.addLayout(btn_row)

        # ---- UX改善（新④）: ワークフロー概要カード ----
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        container_layout.addWidget(separator)

        workflow_title = QLabel("基本的な使い方")
        wf_font = QFont()
        wf_font.setPointSize(11)
        wf_font.setBold(True)
        workflow_title.setFont(wf_font)
        workflow_title.setStyleSheet("padding: 4px 0px 6px 0px;")
        container_layout.addWidget(workflow_title)

        # 4ステップカードを横並びで表示
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)

        _workflow_steps = [
            {
                "number": "①",
                "title": "モデル設定",
                "color": "#1976d2",
                "lines": [
                    "SNAPの入力ファイル",
                    "(.s8i) を選択",
                    "ダンパー定義・節点",
                    "情報を確認",
                ],
            },
            {
                "number": "②",
                "title": "ケース設計",
                "color": "#7b1fa2",
                "lines": [
                    "ダンパー種別・",
                    "基数・パラメータを",
                    "複数ケースで設定",
                    "テンプレート活用可",
                ],
            },
            {
                "number": "③",
                "title": "解析実行",
                "color": "#f57c00",
                "lines": [
                    "実行するケースを",
                    "チェックして一括実行",
                    "進捗をリアルタイム",
                    "モニタリング",
                ],
            },
            {
                "number": "④",
                "title": "結果・戦略",
                "color": "#2e7d32",
                "lines": [
                    "応答グラフを比較",
                    "最良ケースを特定",
                    "次回解析の戦略を",
                    "メモしてループ",
                ],
            },
        ]

        for step in _workflow_steps:
            card = QFrame()
            card.setFrameShape(QFrame.StyledPanel)
            card.setStyleSheet(
                f"QFrame {{"
                f"  border: 2px solid {step['color']};"
                f"  border-radius: 6px;"
                f"  padding: 4px;"
                f"}}"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 6, 8, 6)
            card_layout.setSpacing(4)

            # ステップ番号 + タイトル
            header_lbl = QLabel(
                f"<span style='font-size:18px; font-weight:900; color:{step['color']};'>"
                f"{step['number']}"
                f"</span>"
                f"<span style='font-size:12px; font-weight:bold;'> {step['title']}</span>"
            )
            header_lbl.setTextFormat(Qt.RichText)
            card_layout.addWidget(header_lbl)

            # 説明行
            for line in step["lines"]:
                lbl = QLabel(line)
                lbl.setStyleSheet("color: gray; font-size: 10px;")
                card_layout.addWidget(lbl)

            cards_row.addWidget(card)

        container_layout.addLayout(cards_row)

        main_layout.addWidget(container)

    def _make_action_button(
        self, text: str, tooltip: str, callback, shortcut_hint: str = ""
    ) -> QWidget:
        """クイックアクションボタンを作成（ショートカットヒント付き）。"""
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)

        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setMinimumHeight(48)
        btn.setMinimumWidth(200)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn.setStyleSheet("""
            QPushButton {
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border: 2px solid palette(mid);
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        btn.clicked.connect(callback)
        container_layout.addWidget(btn)

        if shortcut_hint:
            hint_label = QLabel(shortcut_hint)
            hint_label.setAlignment(Qt.AlignCenter)
            hint_font = QFont()
            hint_font.setPointSize(9)
            hint_label.setFont(hint_font)
            hint_label.setStyleSheet("color: gray; padding: 0;")
            container_layout.addWidget(hint_label)

        return container

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_snap_warning(self, show: bool) -> None:
        """
        UX改善（スマートデフォルト）: SNAP未設定警告バナーの表示/非表示を切り替えます。

        Parameters
        ----------
        show : bool
            True: SNAP実行ファイルが設定されていないため警告を表示する。
            False: 設定済みのため警告を隠す。
        """
        if hasattr(self, "_snap_warning_banner"):
            self._snap_warning_banner.setVisible(show)

    def refresh(self) -> None:
        """最近使ったプロジェクトリストを更新します。"""
        self._recent_list.clear()
        # UX改善①: リスト更新時は「開く」ボタンを無効化（再選択が必要）
        self._open_btn.setEnabled(False)
        recents = get_recent_projects()

        if not recents:
            item = QListWidgetItem("（最近のプロジェクトはありません）")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            item.setForeground(Qt.gray)
            self._recent_list.addItem(item)
            self._clear_btn.setEnabled(False)
            return

        self._clear_btn.setEnabled(True)
        for entry in recents:
            path = entry.get("path", "")
            name = entry.get("name", Path(path).stem)
            last_opened = entry.get("last_opened", "")

            # 存在チェック
            exists = Path(path).exists()
            prefix = "" if exists else "[見つかりません] "

            # 日時整形
            date_str = ""
            if last_opened:
                try:
                    dt = datetime.fromisoformat(last_opened)
                    date_str = dt.strftime("%Y/%m/%d %H:%M")
                except (ValueError, TypeError):
                    date_str = last_opened

            display = f"{prefix}{name}"
            if date_str:
                display += f"  ({date_str})"
            display += f"\n{path}"

            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, path)
            if not exists:
                item.setForeground(Qt.gray)
            self._recent_list.addItem(item)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_recent_double_click(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path and Path(path).exists():
            self.recentProjectSelected.emit(path)

    # UX改善①: シングルクリック選択時の「開く」ボタン有効化
    def _on_recent_selection_changed(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        """選択項目が変わったとき「開く」ボタンの有効/無効を更新します。"""
        if current is None:
            self._open_btn.setEnabled(False)
            return
        path = current.data(Qt.UserRole)
        # ファイルが存在する場合のみ「開く」を有効化
        exists = path and Path(path).exists()
        self._open_btn.setEnabled(bool(exists))
        if not exists and path:
            self._open_btn.setToolTip("このファイルは見つかりません:\n" + str(path))
        else:
            self._open_btn.setToolTip(
                "選択したプロジェクトを開きます\n"
                "（ダブルクリックでも開けます）"
            )

    # UX改善①: 「開く」ボタンクリック処理
    def _on_open_selected(self) -> None:
        """「開く」ボタンが押されたとき、選択中のプロジェクトを開きます。"""
        item = self._recent_list.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if path and Path(path).exists():
            self.recentProjectSelected.emit(path)

    def _on_clear_recent(self) -> None:
        clear_recent_projects()
        self.refresh()
