"""
app/ui/welcome_widget.py
ウェルカム画面 & 最近使ったプロジェクト。

アプリ起動時やプロジェクトが開かれていない時に表示される画面。
クイックアクション、最近のプロジェクト、SNAP未設定時の警告バナーを提供します。
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignCenter)

        self._build_snap_warning_banner(main_layout)

        container = QWidget()
        container.setMaximumWidth(700)
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(20)

        self._build_header(container_layout)
        self._build_quick_actions(container_layout)
        self._build_recent_projects(container_layout)
        self._build_recent_buttons(container_layout)

        main_layout.addWidget(container)

    def _build_snap_warning_banner(self, parent_layout: QVBoxLayout) -> None:
        """SNAP未設定警告バナーを構築。"""
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
        _snap_warn_btn.setToolTip("設定ダイアログを開く")
        _snap_warn_btn.clicked.connect(self.snapSettingsRequested.emit)
        _snap_warn_layout.addWidget(_snap_warn_btn)

        self._snap_warning_banner.setVisible(False)
        parent_layout.addWidget(self._snap_warning_banner)

    def _build_header(self, layout: QVBoxLayout) -> None:
        """ヘッダー（タイトル+サブタイトル）を構築。"""
        header = QLabel("snap-controller")
        header.setAlignment(Qt.AlignCenter)
        header_font = QFont()
        header_font.setPointSize(24)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        subtitle = QLabel("SNAP 免振・制振装置設計支援ツール")
        subtitle.setAlignment(Qt.AlignCenter)
        sub_font = QFont()
        sub_font.setPointSize(12)
        subtitle.setFont(sub_font)
        subtitle.setStyleSheet("color: gray;")
        layout.addWidget(subtitle)

        layout.addSpacing(10)

    def _build_quick_actions(self, layout: QVBoxLayout) -> None:
        """クイックアクションボタン（新規・開く）を構築。"""
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(16)

        btn_new = self._make_action_button(
            "新規プロジェクト",
            "新規プロジェクト作成 (Ctrl+N)",
            self.newProjectRequested.emit,
            shortcut_hint="Ctrl+N",
        )
        actions_layout.addWidget(btn_new)

        btn_open = self._make_action_button(
            "プロジェクトを開く (.snapproj)",
            ".snapproj を開く (Ctrl+O)",
            self.openProjectRequested.emit,
            shortcut_hint="Ctrl+O",
        )
        actions_layout.addWidget(btn_open)

        layout.addLayout(actions_layout)
        layout.addSpacing(10)

    def _build_recent_projects(self, layout: QVBoxLayout) -> None:
        """最近使ったプロジェクト一覧を構築。"""
        recent_label = QLabel("最近使ったプロジェクト")
        recent_font = QFont()
        recent_font.setPointSize(13)
        recent_font.setBold(True)
        recent_label.setFont(recent_font)
        layout.addWidget(recent_label)

        self._recent_list = QListWidget()
        self._recent_list.setMinimumHeight(200)
        self._recent_list.setAlternatingRowColors(True)
        self._recent_list.setSpacing(2)
        self._recent_list.itemDoubleClicked.connect(self._on_recent_double_click)
        self._recent_list.currentItemChanged.connect(self._on_recent_selection_changed)
        layout.addWidget(self._recent_list)

    def _build_recent_buttons(self, layout: QVBoxLayout) -> None:
        """最近使ったプロジェクトのボタン行を構築。"""
        btn_row = QHBoxLayout()

        self._open_btn = QPushButton("開く (.snapproj)")
        self._open_btn.setToolTip("選択中のプロジェクトを開く")
        self._open_btn.setEnabled(False)
        self._open_btn.setDefault(True)
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

        self._cleanup_btn = QPushButton("❌ 見つからない項目を削除")
        self._cleanup_btn.setMaximumWidth(210)
        self._cleanup_btn.setToolTip("存在しないパスの履歴を一括削除")
        self._cleanup_btn.setStyleSheet(
            "QPushButton {"
            "  font-size: 11px; padding: 3px 10px;"
            "  color: #c62828; border: 1px solid #ef9a9a; border-radius: 4px;"
            "}"
            "QPushButton:hover { background-color: #ffebee; }"
            "QPushButton:disabled { color: palette(dark); border-color: palette(mid); }"
        )
        self._cleanup_btn.clicked.connect(self._on_cleanup_missing)
        btn_row.addWidget(self._cleanup_btn)

        self._clear_btn = QPushButton("履歴をクリア")
        self._clear_btn.setMaximumWidth(120)
        self._clear_btn.clicked.connect(self._on_clear_recent)
        btn_row.addWidget(self._clear_btn)
        layout.addLayout(btn_row)

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
            # UX改善（第7回①）: プロジェクトがない場合はクリーンアップも無効
            if hasattr(self, "_cleanup_btn"):
                self._cleanup_btn.setEnabled(False)
            return

        self._clear_btn.setEnabled(True)

        # UX改善（第7回①）: 見つからないエントリが1件以上あればクリーンアップボタンを有効化
        if hasattr(self, "_cleanup_btn"):
            missing_count = sum(1 for r in recents if not Path(r.get("path", "")).exists())
            self._cleanup_btn.setEnabled(missing_count > 0)
            self._cleanup_btn.setText(
                f"❌ 見つからない項目を削除 ({missing_count}件)"
                if missing_count > 0
                else "❌ 見つからない項目を削除"
            )

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
            self._open_btn.setToolTip("ファイルが見つかりません:\n" + str(path))
        else:
            self._open_btn.setToolTip("選択中のプロジェクトを開く")

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

    # UX改善（第7回①）: 見つからないプロジェクトを一括削除
    def _on_cleanup_missing(self) -> None:
        """
        UX改善（第7回①）: 存在しないパスの履歴エントリを一括削除します。

        削除件数を「X件削除しました」のメッセージでユーザーにフィードバックし、
        リストを更新します。削除対象がない場合はボタンが無効のため呼ばれません。
        """
        from PySide6.QtWidgets import QMessageBox
        recents = get_recent_projects()
        missing = [r for r in recents if not Path(r.get("path", "")).exists()]
        valid = [r for r in recents if Path(r.get("path", "")).exists()]

        if not missing:
            return  # 削除対象なし

        # QSettings に有効エントリだけ書き戻す
        from PySide6.QtCore import QSettings as _QSettings
        s = _QSettings(SETTINGS_ORG, SETTINGS_APP)
        s.setValue(KEY_RECENT_PROJECTS, valid)

        # フィードバック
        QMessageBox.information(
            self,
            "削除完了",
            f"{len(missing)} 件の見つからないプロジェクトを履歴から削除しました。",
        )
        self.refresh()
