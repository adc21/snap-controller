"""
app/ui/shortcut_help_dialog.py
キーボードショートカット一覧ダイアログ。

改善⑨: Ctrl+? または ヘルプメニューから開けるショートカット一覧。
        アプリ内のすべてのキーボードショートカットをカテゴリ別に表示します。
        初めて使うユーザーが機能を発見しやすくなります。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ショートカット定義: (カテゴリ, キー表示文字列, 機能説明)
_SHORTCUTS = [
    # ---- ファイル操作 ----
    ("ファイル", "Ctrl+N",         "新規プロジェクトを作成"),
    ("ファイル", "Ctrl+O",         "プロジェクトを開く"),
    ("ファイル", "Ctrl+S",         "プロジェクトを保存"),
    ("ファイル", "Ctrl+Shift+S",   "名前を付けて保存"),
    ("ファイル", "Ctrl+E",         "結果をエクスポート"),
    ("ファイル", "Ctrl+Shift+R",   "HTML レポートを生成"),
    ("ファイル", "Ctrl+Q",         "アプリを終了"),

    # ---- 解析ケース操作（テーブル上） ----
    ("ケース操作", "Enter / ダブルクリック", "選択ケースを編集"),
    ("ケース操作", "F2",                    "選択ケースの名前をクイックリネーム（ダイアログなし）"),
    ("ケース操作", "Delete / Backspace",    "選択ケースを削除"),
    ("ケース操作", "F5",                    "選択ケースを解析実行"),
    ("ケース操作", "F6",                    "選択ケースをデモ実行（モックデータ）"),
    ("ケース操作", "Ctrl+D",                "選択ケースを複製"),
    ("ケース操作", "右クリック",             "コンテキストメニューを開く"),
    # ---- 結果テーブル ----
    ("結果テーブル", "Ctrl+C",              "選択行をクリップボードにコピー（Excel貼り付け用）"),
    ("結果テーブル", "右クリック",           "選択行コピー / 全行コピーのメニューを開く"),

    # ---- 解析機能 ----
    ("解析機能", "Ctrl+W",         "パラメータスイープダイアログを開く"),
    ("解析機能", "Ctrl+T",         "目標性能基準ダイアログを開く"),
    ("解析機能", "Ctrl+K",         "ダンパーカタログを開く"),
    ("解析機能", "Ctrl+Shift+E",   "地震波選択ダイアログを開く"),
    ("解析機能", "Ctrl+Shift+O",   "ダンパー最適化ダイアログを開く"),
    ("解析機能", "Ctrl+Shift+M",   "複数地震波一括解析ダイアログを開く"),
    ("解析機能", "Ctrl+Shift+T",   "テンプレートから適用"),
    ("解析機能", "Ctrl+Shift+V",   "選択ケースの入力チェック"),

    # ---- 表示・ナビゲーション ----
    ("表示・ナビゲーション", "Ctrl+?",  "このショートカット一覧を表示"),
    # UX改善④新: ステップナビゲーションショートカット
    ("表示・ナビゲーション", "Ctrl+1",  "STEP1: モデル設定 に移動"),
    ("表示・ナビゲーション", "Ctrl+2",  "STEP2: ケース設計 に移動"),
    ("表示・ナビゲーション", "Ctrl+3",  "STEP3: 解析実行 に移動"),
    ("表示・ナビゲーション", "Ctrl+4",  "STEP4: 結果・戦略 に移動"),
    # UX改善⑥新: ケース並び替え
    ("ケース操作", "↑ / ↓ ボタン（アクションバー）", "選択ケースを1つ上/下に移動して順序を変更"),
    # UX改善⑦新: メモインライン編集
    ("ケース操作", "メモ列をダブルクリック",            "メモだけを直接編集（全編集ダイアログを開かずに済む）"),

    # ---- 新UX改善機能（クリック操作） ----
    ("ケース操作", "全選択ボタン（フィルターバー）",      "表示中の全ケースを選択（絞り込み後も有効）"),
    ("ケース操作", "全解除ボタン（フィルターバー）",      "すべての選択を解除"),
    ("ケース操作", "右クリック → 🔄 状態をリセット",     "完了/エラーケースをPENDINGに戻して再実行可能にする"),
    ("結果グラフ", "⛶ 拡大ボタン（タイトル行）",          "現在の結果グラフを大きなダイアログで拡大表示"),
    ("比較グラフ", "完了のみボタン",                       "解析完了ケースだけをチェックして比較グラフに表示"),
    ("STEP1",     "▼ 履歴ボタン",                         "最近使ったs8iファイルから素早く再読込"),
]


class ShortcutHelpDialog(QDialog):
    """
    キーボードショートカット一覧を表示するダイアログ。

    カテゴリ別にショートカットを表示し、
    検索ボックスでリアルタイム絞り込みができます。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("キーボードショートカット一覧")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- タイトル ----
        title = QLabel("⌨  キーボードショートカット一覧")
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel("よく使う操作をキーボードで素早く実行できます。")
        subtitle.setStyleSheet("color: gray; margin-bottom: 4px;")
        layout.addWidget(subtitle)

        # ---- 検索ボックス ----
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍  機能名・キーで絞り込み…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_edit)

        # ---- テーブル ----
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["カテゴリ", "キー", "機能"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table)

        # ---- 件数ラベル ----
        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._count_label)

        # ---- 閉じるボタン ----
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        # 初期データ描画
        self._populate("")

    def _populate(self, filter_text: str) -> None:
        """ショートカット一覧をテーブルに描画します（フィルター適用）。"""
        text = filter_text.strip().lower()
        self._table.setRowCount(0)

        for category, key, description in _SHORTCUTS:
            if text and not any(
                text in s.lower() for s in (category, key, description)
            ):
                continue

            row = self._table.rowCount()
            self._table.insertRow(row)

            cat_item = QTableWidgetItem(category)
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsEditable)
            cat_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 0, cat_item)

            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            key_item.setTextAlignment(Qt.AlignCenter)
            key_font = QFont("Consolas", 10)
            key_font.setBold(True)
            key_item.setFont(key_font)
            # キーセルに色付け
            key_item.setBackground(
                self._key_bg_color()
            )
            self._table.setItem(row, 1, key_item)

            desc_item = QTableWidgetItem(description)
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 2, desc_item)

        total = self._table.rowCount()
        if text:
            self._count_label.setText(f"{total} 件が見つかりました")
        else:
            self._count_label.setText(f"全 {total} 件のショートカット")

    def _key_bg_color(self):
        """テーマに合わせたキーセルの背景色を返します。"""
        from PySide6.QtGui import QColor
        # ライト/ダーク問わず視認性の良い淡いグレー
        return QColor("#e8e8e8")

    def _on_search_changed(self, text: str) -> None:
        self._populate(text)
