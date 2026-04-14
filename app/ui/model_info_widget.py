"""
app/ui/model_info_widget.py
モデル情報パネル。

読み込んだ .s8i ファイルの概要を表示します:
  - タイトル、バージョン
  - 節点数、層数
  - ダンパー定義一覧
  - ダンパー装置数・合計基数

UX改善（新）: 層別ダンパー配置分布ミニチャート追加。
  ダンパー種別カードの下に、各層（フロア）に何基のダンパーが配置されているかを
  コンパクトな横棒グラフで常時表示します。
  - 各バーの長さと色がダンパー本数を視覚的に表します（最多層=濃い青、他=水色）
  - 「6F: ████████ 8基」のようなシンプルなレイアウトで識別しやすくします
  - ダンパーが0本の層は表示を省略して重要情報を目立たせます
  - モデル未読み込み時は非表示
  `_floor_chart_area` QFrame と `_rebuild_floor_chart()` メソッドを追加。

UX改善D: .s8iファイルのドラッグ&ドロップ対応。
  パネル上に .s8i ファイルをドロップすると、ファイルダイアログを開かずに
  直接ファイルを読み込めます。ドラッグ中は枠線を青くハイライトし、
  ユーザーに「ここにドロップできる」ことを視覚的に伝えます。
  fileDropped(path: str) シグナルでファイルパスを通知します。

UX改善（新）: s8iファイル外部変更検知ウォッチャー。
  読み込んだ .s8i ファイルがSNAPや外部エディタで変更された場合に、
  黄色の「更新バナー」を自動表示して再読込を促します。
  QFileSystemWatcher を利用して OS レベルのファイル変更イベントを検知します。
  ユーザーがファイルを編集し直しながら繰り返し解析するワークフローで
  「古いモデルのまま解析を続けてしまう」ミスを防ぎます。

UX改善⑤新: 最近使ったs8iファイルのクイックアクセスドロップダウン追加。
  「ファイルを読み込む…」ボタンの右に「▼ 履歴」ボタンを追加しました。
  クリックすると最近使ったs8iファイル（最大8件）のリストが表示され、
  ファイルダイアログを開かずにワンクリックで再読込できます。
  add_recent_s8i(path) で履歴に追加します（main_window.py から呼び出す）。
  履歴はQSettings に保存され、アプリを再起動しても保持されます。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QFileSystemWatcher, QSettings, QTimer
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from app.models.s8i_parser import S8iModel

_RECENT_S8I_KEY = "recent_s8i_files"
_RECENT_S8I_MAX = 8


class ModelInfoWidget(QWidget):
    """
    .s8i モデル情報を表示するパネル。

    Signals
    -------
    fileRequested()
        ユーザーがファイル読み込みボタンを押したときに発火。
    fileDropped(path: str)
        UX改善D: .s8i ファイルがドロップされたときにそのパスを通知します。
    """

    fileRequested = Signal()
    fileDropped = Signal(str)  # UX改善D: ドロップされたファイルパスを通知

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._model: Optional[S8iModel] = None
        self._watched_path: str = ""  # UX改善（新）: 現在監視中のファイルパス
        # UX改善D: ドラッグ&ドロップを有効化
        self.setAcceptDrops(True)
        # UX改善（新）: QFileSystemWatcher でファイル変更を監視
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.fileChanged.connect(self._on_file_changed)
        # 変更検知後に少し間を置いてバナーを表示（ファイルの書き込み完了を待つ）
        self._change_timer = QTimer(self)
        self._change_timer.setSingleShot(True)
        self._change_timer.setInterval(800)  # 800ms後に通知（書き込み中に誤検知しないよう）
        self._change_timer.timeout.connect(self._show_change_banner)
        self._setup_ui()

    def set_model(self, model: Optional[S8iModel]) -> None:
        """表示するモデルを設定します。"""
        self._model = model
        # UX改善（新）: ファイルウォッチャーを更新
        # 前のファイルの監視を停止
        if self._watched_path and self._watched_path in self._file_watcher.files():
            self._file_watcher.removePath(self._watched_path)
        self._watched_path = ""
        # 変更バナーを隠す（新しいファイルを読み込んだので最新状態）
        if hasattr(self, "_change_banner"):
            self._change_banner.hide()
        # 新しいファイルを監視開始
        if model and model.file_path:
            self._watched_path = model.file_path
            self._file_watcher.addPath(model.file_path)
        self._refresh()

    # ------------------------------------------------------------------
    # UX改善⑤新: 最近使ったs8iファイル履歴
    # ------------------------------------------------------------------

    def add_recent_s8i(self, path: str) -> None:
        """
        UX改善⑤新: 最近使ったs8iファイルリストにパスを追加します。

        同じパスがすでにリストにある場合は先頭に移動します。
        リストが _RECENT_S8I_MAX 件を超えた場合は末尾を削除します。
        履歴は QSettings に保存され、アプリ再起動後も保持されます。

        main_window.py の _load_s8i_from_path() からこのメソッドを呼び出してください。

        Parameters
        ----------
        path : str
            追加するs8iファイルの絶対パス。
        """
        if not path:
            return
        settings = QSettings("BAUES", "snap-controller")
        recent: list = list(settings.value(_RECENT_S8I_KEY, []) or [])
        # 既存エントリを除去して先頭に追加
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:_RECENT_S8I_MAX]
        settings.setValue(_RECENT_S8I_KEY, recent)
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        """
        UX改善⑤新: 最近使ったs8iファイルのドロップダウンメニューを再構築します。

        QSettings から履歴を読み込み、ファイルが存在するものだけを表示します。
        存在しないファイルは自動的にリストから除去します。
        """
        self._recent_menu.clear()
        settings = QSettings("BAUES", "snap-controller")
        recent: list = list(settings.value(_RECENT_S8I_KEY, []) or [])

        # 存在するファイルのみ保持
        import os as _os
        valid_recent = [p for p in recent if _os.path.isfile(p)]
        if len(valid_recent) != len(recent):
            # 存在しないファイルをQSettingsからも除去
            settings.setValue(_RECENT_S8I_KEY, valid_recent)

        if not valid_recent:
            act = self._recent_menu.addAction("（履歴なし）")
            act.setEnabled(False)
            if hasattr(self, "_recent_btn"):
                self._recent_btn.setToolTip(
                    "最近使ったs8iファイルの履歴はまだありません\n"
                    "ファイルを読み込むと履歴が蓄積されます"
                )
            return

        for p in valid_recent:
            basename = _os.path.basename(p)
            dirpart = _os.path.dirname(p)
            # ファイル名（親ディレクトリも表示して区別しやすく）
            display = f"{basename}  —  {dirpart}"
            act = self._recent_menu.addAction(display)
            act.setToolTip(p)
            # クロージャでパスをキャプチャ
            act.triggered.connect(lambda _checked=False, _p=p: self.fileDropped.emit(_p))

        self._recent_menu.addSeparator()
        act_clear = self._recent_menu.addAction("🗑 履歴をクリア")
        act_clear.setToolTip("最近使ったs8iファイルの履歴をすべて削除します")
        act_clear.triggered.connect(self._clear_recent_s8i)

    def _clear_recent_s8i(self) -> None:
        """UX改善⑤新: 最近使ったs8iファイルの履歴を全件削除します。"""
        settings = QSettings("BAUES", "snap-controller")
        settings.setValue(_RECENT_S8I_KEY, [])
        self._rebuild_recent_menu()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addLayout(self._build_header())
        layout.addWidget(self._build_change_banner())
        self._info_stack = QStackedWidget()
        self._info_stack.addWidget(self._build_empty_card())
        self._info_stack.addWidget(self._build_loaded_panel())
        layout.addWidget(self._info_stack)
        layout.addStretch()

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>入力モデル (.s8i)</b>"))
        header.addStretch()
        self._load_btn = QPushButton("📂 .s8i ファイルを読み込む…")
        self._load_btn.setToolTip(
            "SNAP の解析入力ファイル (.s8i) を読み込みます。\n"
            "モデルの節点・層・ダンパー定義などの構造情報が読み取られ、\n"
            "STEP2 でのケース設定・STEP3 での解析実行が可能になります。"
        )
        self._load_btn.clicked.connect(self.fileRequested.emit)
        header.addWidget(self._load_btn)

        self._recent_btn = QToolButton()
        self._recent_btn.setText("▼ 履歴")
        self._recent_btn.setToolTip(
            "最近使ったs8iファイルから素早く読み込みます\n"
            "（最大8件を保持。ファイルダイアログを開かずに再読込可能）"
        )
        self._recent_btn.setPopupMode(QToolButton.InstantPopup)
        self._recent_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._recent_menu = QMenu(self._recent_btn)
        self._recent_btn.setMenu(self._recent_menu)
        self._rebuild_recent_menu()
        header.addWidget(self._recent_btn)
        return header

    def _build_change_banner(self) -> QFrame:
        self._change_banner = QFrame()
        self._change_banner.setFrameShape(QFrame.StyledPanel)
        self._change_banner.setStyleSheet(
            "QFrame {"
            "  background-color: #fff8e1;"
            "  border: 1px solid #ff9800;"
            "  border-radius: 4px;"
            "}"
        )
        banner_h = QHBoxLayout(self._change_banner)
        banner_h.setContentsMargins(8, 4, 8, 4)
        banner_h.setSpacing(8)
        _warn_icon = QLabel("⚠")
        _warn_icon.setStyleSheet("font-size: 16px; color: #e65100; background: transparent; border: none;")
        banner_h.addWidget(_warn_icon)
        self._change_banner_text = QLabel(
            "<span style='color:#e65100; font-weight:bold;'>s8iファイルが更新されました</span>"
            "<span style='color:#555; font-size:11px;'>　― モデル情報が古くなっています</span>"
        )
        self._change_banner_text.setTextFormat(Qt.RichText)
        self._change_banner_text.setStyleSheet("background: transparent; border: none;")
        banner_h.addWidget(self._change_banner_text, stretch=1)
        _reload_btn = QPushButton("🔄 再読込")
        _reload_btn.setToolTip(
            "更新されたファイルをアプリに再読込します。\n"
            "現在のケース設定はそのまま保持されます。"
        )
        _reload_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #ff9800; color: white;"
            "  font-weight: bold; padding: 3px 10px;"
            "  border-radius: 3px; border: none; font-size: 11px;"
            "}"
            "QPushButton:hover { background-color: #f57c00; }"
        )
        _reload_btn.clicked.connect(self._on_reload_clicked)
        banner_h.addWidget(_reload_btn)
        _dismiss_btn = QPushButton("✕")
        _dismiss_btn.setFlat(True)
        _dismiss_btn.setFixedSize(18, 18)
        _dismiss_btn.setStyleSheet(
            "QPushButton { color: #999; background: transparent; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #333; }"
        )
        _dismiss_btn.setToolTip("このバナーを閉じる（ファイルの変更は無視されます）")
        _dismiss_btn.clicked.connect(self._change_banner.hide)
        banner_h.addWidget(_dismiss_btn)
        self._change_banner.hide()
        return self._change_banner

    def _build_empty_card(self) -> QFrame:
        empty_card = QFrame()
        empty_card.setStyleSheet("""
            QFrame {
                border: 2px dashed palette(mid);
                border-radius: 8px;
                padding: 12px;
            }
        """)
        empty_card_layout = QVBoxLayout(empty_card)
        empty_card_layout.setAlignment(Qt.AlignCenter)
        empty_card_layout.setSpacing(8)

        empty_icon = QLabel("\U0001f4c2")
        icon_font = QFont()
        icon_font.setPointSize(24)
        empty_icon.setFont(icon_font)
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_card_layout.addWidget(empty_icon)

        empty_msg = QLabel("SNAP入力ファイル (.s8i) を読み込んでください")
        empty_msg.setAlignment(Qt.AlignCenter)
        msg_font = QFont()
        msg_font.setPointSize(10)
        msg_font.setBold(True)
        empty_msg.setFont(msg_font)
        empty_card_layout.addWidget(empty_msg)

        empty_hint = QLabel(
            "SNAP の解析入力ファイル (.s8i) を読み込みます。\n"
            "モデル情報・ダンパー定義が表示され、STEP2 でのケース作成が可能になります。\n\n"
            "過去の作業を再開するには、メニュー「ファイル → プロジェクトを開く (.snapproj)」を使用してください。"
        )
        empty_hint.setAlignment(Qt.AlignCenter)
        empty_hint.setStyleSheet("color: gray;")
        empty_hint.setWordWrap(True)
        empty_card_layout.addWidget(empty_hint)
        return empty_card

    def _build_loaded_panel(self) -> QWidget:
        loaded_widget = QWidget()
        loaded_layout = QVBoxLayout(loaded_widget)
        loaded_layout.setContentsMargins(0, 0, 0, 0)

        self._summary_label = QLabel("")
        self._summary_label.setWordWrap(True)
        loaded_layout.addWidget(self._summary_label)

        self._toggle_btn = QPushButton("▼ ダンパー詳細定義を表示")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(False)
        self._toggle_btn.setStyleSheet("text-align: left; font-weight: bold; padding: 4px;")
        self._toggle_btn.clicked.connect(self._on_toggle_details)
        self._toggle_btn.hide()
        loaded_layout.addWidget(self._toggle_btn)

        self._damper_group = QGroupBox("ダンパー定義（架構 - 免制振装置）")
        dg_layout = QVBoxLayout(self._damper_group)
        self._damper_table = QTableWidget(0, 3)
        self._damper_table.setHorizontalHeaderLabels(["種類", "名称", "キーワード"])
        self._damper_table.horizontalHeader().setStretchLastSection(True)
        self._damper_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._damper_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._damper_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._damper_table.verticalHeader().setVisible(False)
        self._damper_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._damper_table.setMaximumHeight(140)
        dg_layout.addWidget(self._damper_table)
        self._damper_group.hide()
        loaded_layout.addWidget(self._damper_group)

        self._damper_cards_area = QWidget()
        self._damper_cards_layout = QHBoxLayout(self._damper_cards_area)
        self._damper_cards_layout.setContentsMargins(0, 4, 0, 4)
        self._damper_cards_layout.setSpacing(6)
        self._damper_cards_area.hide()
        loaded_layout.addWidget(self._damper_cards_area)

        self._floor_chart_area = QFrame()
        self._floor_chart_area.setFrameShape(QFrame.NoFrame)
        self._floor_chart_area.setStyleSheet("QFrame { margin: 0px; }")
        self._floor_chart_layout = QVBoxLayout(self._floor_chart_area)
        self._floor_chart_layout.setContentsMargins(0, 4, 0, 2)
        self._floor_chart_layout.setSpacing(1)
        self._floor_chart_area.hide()
        loaded_layout.addWidget(self._floor_chart_area)

        loaded_layout.addStretch()
        return loaded_widget

    def _on_toggle_details(self, checked: bool) -> None:
        self._damper_group.setVisible(checked)
        if checked:
            self._toggle_btn.setText("▲ ダンパー詳細定義を隠す")
        else:
            self._toggle_btn.setText("▼ ダンパー詳細定義を表示")

    def _refresh(self) -> None:
        m = self._model
        if m is None:
            self._info_stack.setCurrentIndex(0)  # CTAカードを表示
            self._damper_group.hide()
            if hasattr(self, "_damper_cards_area"):
                self._damper_cards_area.hide()
            if hasattr(self, "_floor_chart_area"):
                self._floor_chart_area.hide()
            if hasattr(self, '_toggle_btn'): self._toggle_btn.hide()
            if hasattr(self, '_load_btn'):
                self._load_btn.setText("📂 .s8i ファイルを読み込む…")
                self._load_btn.setToolTip(
                    "SNAP の解析入力ファイル (.s8i) を読み込みます。\n"
                    "モデルの節点・層・ダンパー定義などの構造情報が読み取られ、\n"
                    "STEP2 でのケース設定・STEP3 での解析実行が可能になります。"
                )
            return
        self._info_stack.setCurrentIndex(1)  # モデル情報を表示
        if hasattr(self, '_load_btn'):
            self._load_btn.setText("🔄 .s8i ファイルを変更…")
            self._load_btn.setToolTip(
                "現在読み込んでいる .s8i ファイルを別のファイルに変更します。\n"
                "モデルの節点・層・ダンパー定義などの構造情報が再読み取りされます。"
            )

        import os
        fname = os.path.basename(m.file_path) if m.file_path else "（不明）"

        lines = [
            f"<b>{m.title or '（無題）'}</b> — {fname}",
            f"SNAP ver.{m.version}" if m.version else "",
            f"節点数: {m.num_nodes}　|　層数: {m.num_floors}",
            f"ダンパー定義: {len(m.damper_defs)} 種　|　"
            f"制振ブレース (SR): {len(m.damper_braces)} 本　|　"
            f"免制振装置 (RD): {m.num_dampers} 箇所（合計 {m.total_damper_units} 基）",
        ]
        self._summary_label.setText("<br>".join(line for line in lines if line))

        # ダンパー定義テーブル
        self._damper_table.setRowCount(0)
        if m.damper_defs:
            self._toggle_btn.show()
            self._damper_group.setVisible(self._toggle_btn.isChecked())
            for ddef in m.damper_defs:
                row = self._damper_table.rowCount()
                self._damper_table.insertRow(row)
                from PySide6.QtWidgets import QTableWidgetItem
                type_labels = {
                    "DVOD": "粘性/オイル", "DSD": "鋼材", "DVHY": "履歴型",
                    "DVBI": "バイリニア", "DVSL": "すべり", "DVFR": "摩擦",
                    "DVTF": "粘弾性", "DVMS": "マスダンパー",
                }
                self._damper_table.setItem(row, 0, QTableWidgetItem(type_labels.get(ddef.keyword, ddef.keyword)))
                self._damper_table.setItem(row, 1, QTableWidgetItem(ddef.name))
                self._damper_table.setItem(row, 2, QTableWidgetItem(ddef.keyword))

        # UX改善⑤（新）: ダンパー種別カードグリッドを更新
        self._rebuild_damper_cards(m)
        # UX改善（新）: 層別ダンパー配置分布ミニチャートを更新
        self._rebuild_floor_chart(m)

    _KEYWORD_STYLES = {
        "DVOD": {"icon": "💧", "bg": "#e3f2fd", "border": "#42a5f5", "label": "粘性/オイル"},
        "DSD":  {"icon": "🔩", "bg": "#fce4ec", "border": "#ef9a9a", "label": "鋼材"},
        "DVHY": {"icon": "🔄", "bg": "#e8f5e9", "border": "#66bb6a", "label": "履歴型"},
        "DVBI": {"icon": "📐", "bg": "#fff8e1", "border": "#ffca28", "label": "バイリニア"},
        "DVSL": {"icon": "🔁", "bg": "#f3e5f5", "border": "#ce93d8", "label": "すべり"},
        "DVFR": {"icon": "🔧", "bg": "#fbe9e7", "border": "#ff8a65", "label": "摩擦"},
        "DVTF": {"icon": "🌀", "bg": "#e0f2f1", "border": "#4db6ac", "label": "粘弾性"},
        "DVMS": {"icon": "⚖",  "bg": "#f1f8e9", "border": "#aed581", "label": "マスダンパー"},
    }

    def _rebuild_damper_cards(self, model) -> None:
        """
        UX改善⑤（新）: ダンパー種別カードグリッドを再構築します。

        s8iモデルのダンパー定義リストからカードを作成し、
        ダンパーの種別・名称・配置箇所数・合計基数をコンパクトに表示します。

        Parameters
        ----------
        model : S8iModel
            読み込み済みのモデル。
        """
        if not hasattr(self, "_damper_cards_layout"):
            return

        self._clear_damper_cards()

        if not model or not model.damper_defs:
            if hasattr(self, "_damper_cards_area"):
                self._damper_cards_area.hide()
            return

        rd_counts = self._count_rd_by_def(model)
        for ddef in model.damper_defs:
            style = self._resolve_keyword_style(ddef.keyword)
            card = self._build_damper_card(ddef, style, rd_counts.get(ddef.name))
            self._damper_cards_layout.addWidget(card)

        self._damper_cards_layout.addStretch()
        self._damper_cards_area.show()

    def _clear_damper_cards(self) -> None:
        while self._damper_cards_layout.count():
            item = self._damper_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    @staticmethod
    def _count_rd_by_def(model) -> Dict[str, List[int]]:
        rd_counts: Dict[str, List[int]] = {}
        for elem in (model.damper_elements if model.damper_elements else []):
            key = elem.damper_def_name
            if key not in rd_counts:
                rd_counts[key] = [0, 0]
            rd_counts[key][0] += 1
            rd_counts[key][1] += max(1, elem.quantity)
        return rd_counts

    @classmethod
    def _resolve_keyword_style(cls, keyword: str) -> dict:
        return cls._KEYWORD_STYLES.get(keyword, {
            "icon": "⚙", "bg": "#f5f5f5", "border": "#bdbdbd", "label": keyword
        })

    def _build_damper_card(self, ddef, style: dict, counts: Optional[List[int]]) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {style['bg']};"
            f"  border: 1px solid {style['border']};"
            f"  border-radius: 6px;"
            f"}}"
            "QLabel { background: transparent; border: none; }"
        )
        card.setMinimumWidth(110)
        card.setMaximumWidth(160)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.setSpacing(2)

        card_layout.addLayout(self._build_damper_card_header(style))

        name_lbl = QLabel(ddef.name)
        name_lbl.setStyleSheet("font-size: 11px; font-weight: bold; color: #212121;")
        name_lbl.setWordWrap(False)
        card_layout.addWidget(name_lbl)

        card_layout.addWidget(self._build_damper_card_placement(counts))
        card_layout.addWidget(self._build_damper_card_keyword_badge(ddef.keyword, style))
        return card

    @staticmethod
    def _build_damper_card_header(style: dict) -> QHBoxLayout:
        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        icon_lbl = QLabel(style["icon"])
        icon_lbl.setStyleSheet("font-size: 14px;")
        header_row.addWidget(icon_lbl)
        type_lbl = QLabel(f"<b>{style['label']}</b>")
        type_lbl.setStyleSheet(f"font-size: 10px; color: {style['border']};")
        type_lbl.setTextFormat(Qt.RichText)
        header_row.addWidget(type_lbl)
        header_row.addStretch()
        return header_row

    @staticmethod
    def _build_damper_card_placement(counts: Optional[List[int]]) -> QLabel:
        if counts:
            lbl = QLabel(
                f"<span style='color:#555;font-size:10px;'>"
                f"配置: {counts[0]}箇所 / {counts[1]}基"
                f"</span>"
            )
        else:
            lbl = QLabel("<span style='color:#aaa;font-size:10px;'>（未配置）</span>")
        lbl.setTextFormat(Qt.RichText)
        return lbl

    @staticmethod
    def _build_damper_card_keyword_badge(keyword: str, style: dict) -> QLabel:
        kw_lbl = QLabel(keyword)
        kw_lbl.setStyleSheet(
            f"font-size: 9px; color: white; background-color: {style['border']};"
            "border-radius: 3px; padding: 1px 4px;"
        )
        kw_lbl.setAlignment(Qt.AlignLeft)
        return kw_lbl

    def _rebuild_floor_chart(self, model) -> None:
        """
        UX改善（新）: 層別ダンパー配置分布ミニチャートを再構築します。

        s8iモデルの damper_elements を読み取り、各ノードの z_grid から
        フロア（層）ごとのダンパー数を集計し、横棒グラフ形式で表示します。

        フロアの識別には節点Jの z_grid を使用します（上部節点 = 取付け先フロア）。
        z_grid が空の場合は「未定義」として扱います。

        Parameters
        ----------
        model : S8iModel
            読み込み済みのモデル。
        """
        if not hasattr(self, "_floor_chart_layout"):
            return

        self._clear_floor_chart_layout()

        if not model or not model.damper_elements:
            if hasattr(self, "_floor_chart_area"):
                self._floor_chart_area.hide()
            return

        floor_counts, floor_units = self._aggregate_floor_damper_counts(model)

        if not floor_counts:
            self._floor_chart_area.hide()
            return

        sorted_floors = sorted(floor_counts.keys(), key=self._floor_sort_key)
        max_count = max(floor_units.values()) if floor_units else 1

        title_lbl = QLabel("📊 層別ダンパー配置数")
        title_lbl.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #1565c0; padding: 2px 0;"
        )
        self._floor_chart_layout.addWidget(title_lbl)

        for floor_key in reversed(sorted_floors):
            row_widget = self._build_floor_bar_row(
                floor_key, floor_units, floor_counts, max_count, len(sorted_floors)
            )
            self._floor_chart_layout.addWidget(row_widget)

        total_units = sum(floor_units.values())
        total_lbl = QLabel(f"合計: {total_units}基 / {len(sorted_floors)}層に配置")
        total_lbl.setStyleSheet(
            "font-size: 9px; color: #555; padding-top: 2px;"
            "border-top: 1px solid #e0e0e0; margin-top: 2px;"
        )
        self._floor_chart_layout.addWidget(total_lbl)

        self._floor_chart_area.show()

    def _clear_floor_chart_layout(self) -> None:
        while self._floor_chart_layout.count():
            item = self._floor_chart_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    @staticmethod
    def _aggregate_floor_damper_counts(model) -> tuple:
        from collections import defaultdict
        floor_counts: dict = defaultdict(int)
        floor_units: dict = defaultdict(int)
        for elem in model.damper_elements:
            node_j_data = model.get_node(elem.node_j)
            if node_j_data and node_j_data.z_grid:
                floor_key = node_j_data.z_grid.strip()
            else:
                floor_key = f"N{elem.node_j}"
            floor_counts[floor_key] += 1
            floor_units[floor_key] += max(1, elem.quantity)
        return floor_counts, floor_units

    @staticmethod
    def _floor_sort_key(k: str):
        import re
        digits = re.sub(r"[^0-9.]", "", k)
        try:
            return (0, float(digits))
        except ValueError:
            return (1, k)

    @staticmethod
    def _pick_bar_colors(ratio: float, is_peak: bool) -> tuple:
        if is_peak:
            return "#1565c0", "#0d47a1"
        if ratio >= 0.7:
            return "#1976d2", "#1565c0"
        if ratio >= 0.4:
            return "#42a5f5", "#1976d2"
        return "#90caf9", "#1976d2"

    def _build_floor_bar_row(
        self, floor_key: str, floor_units: dict, floor_counts: dict,
        max_count: int, total_floors: int,
    ) -> QWidget:
        units = floor_units[floor_key]
        count = floor_counts[floor_key]
        ratio = units / max_count if max_count > 0 else 0
        is_peak = units == max_count
        bar_color, text_color = self._pick_bar_colors(ratio, is_peak)

        row_widget = QWidget()
        row_h = QHBoxLayout(row_widget)
        row_h.setContentsMargins(0, 0, 0, 0)
        row_h.setSpacing(4)

        floor_lbl = QLabel(floor_key)
        floor_lbl.setFixedWidth(40)
        floor_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        floor_lbl.setStyleSheet(f"font-size: 10px; color: {text_color}; font-weight: bold;")
        row_h.addWidget(floor_lbl)

        bar_max_width = 120
        bar_width = max(8, int(bar_max_width * ratio))
        bar_lbl = QLabel()
        bar_lbl.setFixedSize(bar_width, 12)
        bar_lbl.setStyleSheet(f"background-color: {bar_color}; border-radius: 3px;")
        bar_lbl.setToolTip(f"{floor_key}: {count}箇所 / {units}基")
        row_h.addWidget(bar_lbl)

        count_lbl = QLabel(f"{units}基")
        count_lbl.setStyleSheet(f"font-size: 10px; color: {text_color};")
        row_h.addWidget(count_lbl)

        if is_peak and total_floors > 1:
            peak_lbl = QLabel("← 最多")
            peak_lbl.setStyleSheet("font-size: 9px; color: #c62828; font-weight: bold;")
            row_h.addWidget(peak_lbl)

        row_h.addStretch()
        return row_widget

    # ------------------------------------------------------------------
    # UX改善D: ドラッグ&ドロップ対応
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        """ドラッグがパネル上に入ったとき: .s8i ファイルのみ受け入れます。"""
        mime = event.mimeData()
        if mime.hasUrls():
            urls = mime.urls()
            if any(url.toLocalFile().lower().endswith(".s8i") for url in urls):
                event.acceptProposedAction()
                # ビジュアルフィードバック: 枠線を青くハイライト
                self.setStyleSheet(
                    "ModelInfoWidget { border: 2px solid #1976d2; border-radius: 6px; }"
                )
                return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # type: ignore[override]
        """ドラッグがパネルから出たとき: ハイライトを解除します。"""
        self.setStyleSheet("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        """ドロップ時: .s8i ファイルのパスを fileDropped シグナルで通知します。"""
        self.setStyleSheet("")  # ハイライト解除
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if path.lower().endswith(".s8i"):
                    self.fileDropped.emit(path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    # ------------------------------------------------------------------
    # UX改善（新）: ファイル変更検知ウォッチャー
    # ------------------------------------------------------------------

    def _on_file_changed(self, path: str) -> None:
        """
        QFileSystemWatcher がファイル変更を検知したときに呼ばれます。

        ファイルが削除・上書き保存された可能性があるため、
        短いディレイを挟んでバナーを表示します。
        また、一部の OS ではファイル保存時に監視が解除されるため、
        再追加を試みます（存在する場合のみ）。
        """
        # ファイルの書き込みが完了するまで少し待ってからバナーを出す
        self._change_timer.start()
        # 一部 OS では保存後にウォッチが外れるので再登録を試みる
        import os as _os
        if path and _os.path.exists(path):
            if path not in self._file_watcher.files():
                self._file_watcher.addPath(path)

    def _show_change_banner(self) -> None:
        """ファイル変更を示すバナーを表示します。"""
        if self._model is None:
            return  # モデル未読み込み時は何もしない
        self._change_banner.show()

    def _on_reload_clicked(self) -> None:
        """
        「再読込」ボタンが押されたとき、現在のファイルパスで再読込を要求します。

        fileDropped シグナルを流用して、main_window.py の _load_s8i_from_path
        を呼び出します。バナーは set_model() が呼ばれた際に自動で非表示になります。
        """
        if self._watched_path:
            self.fileDropped.emit(self._watched_path)