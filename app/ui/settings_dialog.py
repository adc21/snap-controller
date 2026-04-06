"""
app/ui/settings_dialog.py
アプリケーション設定ダイアログ。

QSettings を利用して以下の設定を永続化します:
  - SNAP.exe のデフォルトパス（新規ケース作成時に自動設定）
  - デモ実行時のデフォルト階数
  - グラフのデフォルトフォントサイズ
  - 解析完了時の通知サウンド ON/OFF
  - 解析完了後にSTEP4へ自動遷移するか（UX改善⑤）

UX改善③ 第5回 (settings_dialog.py):
  SNAP.exe / workフォルダのリアルタイム存在確認バッジ追加。
  パスが変更されるたびに右側のバッジラベルが即座に更新されます。
    - ファイル/フォルダが存在する場合: 緑「✓ 確認済み」
    - 空欄の場合: グレー「（未設定）」
    - 存在しないパスの場合: 赤「✗ 見つかりません」
  タイポや誤ったパス設定によるトラブルを設定直後に気付けます。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# QSettings キー定数
SETTINGS_ORG = "BAUES"
SETTINGS_APP = "snap-controller"

KEY_SNAP_EXE          = "defaults/snap_exe_path"
KEY_SNAP_WORK_DIR     = "defaults/snap_work_dir"
KEY_DEMO_FLOORS       = "defaults/demo_floors"
KEY_FONT_SIZE         = "ui/chart_font_size"
KEY_SOUND_NOTIFY      = "ui/sound_notify"
KEY_THEME             = "ui/theme"
KEY_AUTOSAVE_ENABLED  = "autosave/enabled"
KEY_AUTOSAVE_INTERVAL = "autosave/interval_minutes"
# UX改善⑤: 解析完了後に自動でSTEP4へ移動するか
KEY_AUTO_STEP4        = "ui/auto_step4_on_complete"

# テーマモード選択肢
_THEME_OPTIONS = [
    ("auto",  "自動（OS に合わせる）"),
    ("light", "ライト"),
    ("dark",  "ダーク"),
]


def load_settings() -> dict:
    """
    保存されたアプリ設定を辞書で返します。

    Returns
    -------
    dict
        {
          "snap_exe_path": str,
          "snap_work_dir": str,
          "demo_floors": int,
          "chart_font_size": int,
          "sound_notify": bool,
        }
    """
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    return {
        "snap_exe_path":      s.value(KEY_SNAP_EXE, ""),
        "snap_work_dir":      s.value(KEY_SNAP_WORK_DIR, ""),
        "demo_floors":        int(s.value(KEY_DEMO_FLOORS, 5)),
        "chart_font_size":    int(s.value(KEY_FONT_SIZE, 9)),
        "sound_notify":       s.value(KEY_SOUND_NOTIFY, False, type=bool),
        "theme":              s.value(KEY_THEME, "auto"),
        "autosave_enabled":   s.value(KEY_AUTOSAVE_ENABLED, True, type=bool),
        "autosave_interval":  int(s.value(KEY_AUTOSAVE_INTERVAL, 5)),
        # UX改善⑤: 解析完了後に自動でSTEP4へ移動するか（デフォルト: True）
        "auto_step4":         s.value(KEY_AUTO_STEP4, True, type=bool),
    }


def save_settings(snap_exe: str, snap_work_dir: str, demo_floors: int,
                  chart_font_size: int, sound_notify: bool,
                  theme: str = "auto",
                  autosave_enabled: bool = True,
                  autosave_interval: int = 5,
                  auto_step4: bool = True) -> None:
    """アプリ設定を QSettings に保存します。"""
    s = QSettings(SETTINGS_ORG, SETTINGS_APP)
    s.setValue(KEY_SNAP_EXE,          snap_exe)
    s.setValue(KEY_SNAP_WORK_DIR,     snap_work_dir)
    s.setValue(KEY_DEMO_FLOORS,       demo_floors)
    s.setValue(KEY_FONT_SIZE,         chart_font_size)
    s.setValue(KEY_SOUND_NOTIFY,      sound_notify)
    s.setValue(KEY_THEME,             theme)
    s.setValue(KEY_AUTOSAVE_ENABLED,  autosave_enabled)
    s.setValue(KEY_AUTOSAVE_INTERVAL, autosave_interval)
    # UX改善⑤
    s.setValue(KEY_AUTO_STEP4,        auto_step4)


class SettingsDialog(QDialog):
    """
    アプリケーション設定ダイアログ。

    OK を押すと QSettings に保存されます。
    MainWindow で changed シグナルや戻り値を使って設定を反映してください。

    Usage::

        dlg = SettingsDialog(parent=self)
        if dlg.exec():
            settings = dlg.get_settings()
            # settings["snap_exe_path"] を使うなど
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("アプリケーション設定")
        self.setMinimumWidth(480)
        self._current = load_settings()
        self._setup_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- SNAP デフォルト設定 ----
        snap_group = QGroupBox("SNAP 解析エンジン")
        snap_form = QFormLayout(snap_group)

        # UX改善③ 第5回: exe パス行 + リアルタイムバッジ
        exe_row = QHBoxLayout()
        self._exe_edit = QLineEdit()
        self._exe_edit.setPlaceholderText("SNAP.exe のフルパスを指定してください")
        exe_btn = QPushButton("…")
        exe_btn.setMaximumWidth(32)
        exe_btn.clicked.connect(self._browse_exe)
        exe_row.addWidget(self._exe_edit)
        exe_row.addWidget(exe_btn)
        snap_form.addRow("デフォルト SNAP.exe:", exe_row)

        # バッジラベル（行の次の行に配置）
        self._exe_badge = QLabel("（未設定）")
        self._exe_badge.setStyleSheet("color: #757575; font-size: 10px;")
        snap_form.addRow("", self._exe_badge)

        # UX改善③ 第5回: work dir 行 + リアルタイムバッジ
        work_row = QHBoxLayout()
        self._work_dir_edit = QLineEdit()
        self._work_dir_edit.setPlaceholderText("例: C:\\Users\\xxx\\kozosystem\\SNAPV8\\work")
        work_btn = QPushButton("…")
        work_btn.setMaximumWidth(32)
        work_btn.clicked.connect(self._browse_work_dir)
        work_row.addWidget(self._work_dir_edit)
        work_row.addWidget(work_btn)
        snap_form.addRow("SNAP work フォルダ:", work_row)

        self._work_badge = QLabel("（未設定）")
        self._work_badge.setStyleSheet("color: #757575; font-size: 10px;")
        snap_form.addRow("", self._work_badge)

        snap_form.addRow(
            QLabel(
                "<small>※ SNAP.exe と同じフォルダにある「work」フォルダを指定してください。<br>"
                "解析結果の読み込みに使用します。</small>"
            )
        )
        layout.addWidget(snap_group)

        # UX改善③ 第5回: テキスト変化でリアルタイム更新
        self._exe_edit.textChanged.connect(
            lambda text: self._refresh_path_badge(text, self._exe_badge, is_file=True)
        )
        self._work_dir_edit.textChanged.connect(
            lambda text: self._refresh_path_badge(text, self._work_badge, is_file=False)
        )

        # ---- デモ/解析設定 ----
        demo_group = QGroupBox("デモ・解析設定")
        demo_form = QFormLayout(demo_group)

        self._floors_spin = QSpinBox()
        self._floors_spin.setRange(1, 60)
        self._floors_spin.setSuffix(" 層")
        self._floors_spin.setToolTip("デモ実行時に使用するダミー建物の階数")
        demo_form.addRow("デモ実行 デフォルト階数:", self._floors_spin)
        layout.addWidget(demo_group)

        # ---- UI 設定 ----
        ui_group = QGroupBox("UI 設定")
        ui_form = QFormLayout(ui_group)

        self._theme_combo = QComboBox()
        for _, label in _THEME_OPTIONS:
            self._theme_combo.addItem(label)
        self._theme_combo.setToolTip("アプリケーションの外観テーマを選択します")
        ui_form.addRow("テーマ:", self._theme_combo)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(7, 18)
        self._font_spin.setSuffix(" pt")
        self._font_spin.setToolTip("グラフ内のフォントサイズ")
        ui_form.addRow("グラフ フォントサイズ:", self._font_spin)

        self._sound_check = QCheckBox("解析完了時にサウンドで通知する")
        ui_form.addRow(self._sound_check)

        # UX改善⑤: 解析完了後の自動STEP4遷移オプション
        self._auto_step4_check = QCheckBox("解析完了後に自動的に「STEP4: 結果・戦略」へ移動する")
        self._auto_step4_check.setToolTip(
            "チェックすると、バッチ解析が完了した際に自動的にSTEP4（結果確認）画面へ切り替わります。\n"
            "チェックを外すと、STEP3のまま待機します（手動でSTEP4へ移動します）。"
        )
        ui_form.addRow(self._auto_step4_check)

        ui_form.addRow(
            QLabel(
                "<small>※ テーマの変更はアプリケーションの再起動後に反映されます。</small>"
            )
        )
        layout.addWidget(ui_group)

        # ---- 自動保存設定 ----
        autosave_group = QGroupBox("自動保存")
        autosave_form = QFormLayout(autosave_group)

        self._autosave_check = QCheckBox("自動保存を有効にする")
        self._autosave_check.setToolTip(
            "一定間隔でプロジェクトの自動保存を行います。\n"
            "解析中のデータ損失を防ぎます。"
        )
        autosave_form.addRow(self._autosave_check)

        self._autosave_interval_spin = QSpinBox()
        self._autosave_interval_spin.setRange(1, 60)
        self._autosave_interval_spin.setSuffix(" 分")
        self._autosave_interval_spin.setToolTip("自動保存の間隔（分）")
        autosave_form.addRow("自動保存間隔:", self._autosave_interval_spin)

        self._autosave_check.toggled.connect(
            self._autosave_interval_spin.setEnabled
        )

        autosave_form.addRow(
            QLabel(
                "<small>※ 自動保存はプロジェクトが一度保存された後に有効になります。\n"
                "バックアップは最大 5 世代まで保持されます。</small>"
            )
        )
        layout.addWidget(autosave_group)

        # ---- ボタン ----
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        # リセットボタン
        reset_btn = btn_box.addButton("デフォルトに戻す", QDialogButtonBox.ResetRole)
        reset_btn.clicked.connect(self._reset_defaults)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """現在の設定を UI に反映します。"""
        self._exe_edit.setText(self._current["snap_exe_path"])
        self._work_dir_edit.setText(self._current.get("snap_work_dir", ""))
        # UX改善③ 第5回: 初期ロード時にバッジを更新
        self._refresh_path_badge(self._current["snap_exe_path"], self._exe_badge, is_file=True)
        self._refresh_path_badge(self._current.get("snap_work_dir", ""), self._work_badge, is_file=False)
        self._floors_spin.setValue(self._current["demo_floors"])
        self._font_spin.setValue(self._current["chart_font_size"])
        self._sound_check.setChecked(self._current["sound_notify"])
        # 自動保存
        self._autosave_check.setChecked(self._current.get("autosave_enabled", True))
        self._autosave_interval_spin.setValue(self._current.get("autosave_interval", 5))
        self._autosave_interval_spin.setEnabled(self._autosave_check.isChecked())
        # テーマコンボボックス
        theme_val = self._current.get("theme", "auto")
        for i, (key, _) in enumerate(_THEME_OPTIONS):
            if key == theme_val:
                self._theme_combo.setCurrentIndex(i)
                break
        # UX改善⑤: 自動STEP4遷移
        self._auto_step4_check.setChecked(self._current.get("auto_step4", True))

    def _on_ok(self) -> None:
        """設定を保存して閉じます。"""
        theme_key = _THEME_OPTIONS[self._theme_combo.currentIndex()][0]
        save_settings(
            snap_exe=self._exe_edit.text().strip(),
            snap_work_dir=self._work_dir_edit.text().strip(),
            demo_floors=self._floors_spin.value(),
            chart_font_size=self._font_spin.value(),
            sound_notify=self._sound_check.isChecked(),
            theme=theme_key,
            autosave_enabled=self._autosave_check.isChecked(),
            autosave_interval=self._autosave_interval_spin.value(),
            auto_step4=self._auto_step4_check.isChecked(),  # UX改善⑤
        )
        self.accept()

    def _reset_defaults(self) -> None:
        """設定をデフォルト値にリセットします。"""
        self._exe_edit.clear()
        self._work_dir_edit.clear()
        self._floors_spin.setValue(5)
        self._font_spin.setValue(9)
        self._sound_check.setChecked(False)
        self._theme_combo.setCurrentIndex(0)  # auto
        self._autosave_check.setChecked(True)
        self._autosave_interval_spin.setValue(5)
        self._auto_step4_check.setChecked(True)  # UX改善⑤: デフォルトは有効

    def get_settings(self) -> dict:
        """
        ダイアログが Accept された後の設定値を返します。
        exec() の結果が Accepted の場合のみ呼び出してください。
        """
        return load_settings()

    # ------------------------------------------------------------------
    # File browser
    # ------------------------------------------------------------------

    def _browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "SNAP.exe を選択",
            self._exe_edit.text(),
            "実行ファイル (*.exe);;すべてのファイル (*)"
        )
        if path:
            self._exe_edit.setText(path)

    def _browse_work_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "SNAP work フォルダを選択",
            self._work_dir_edit.text(),
        )
        if path:
            self._work_dir_edit.setText(path)

    # ------------------------------------------------------------------
    # UX改善③ 第5回: リアルタイムパスバッジ
    # ------------------------------------------------------------------

    @staticmethod
    def _refresh_path_badge(path_text: str, badge: QLabel, is_file: bool) -> None:
        """
        UX改善③ 第5回: パス入力内容に応じてバッジラベルのテキスト・色をリアルタイム更新。

        Parameters
        ----------
        path_text : str
            入力中のパス文字列。
        badge : QLabel
            更新対象のバッジラベル。
        is_file : bool
            True のとき「ファイルとして存在するか」、False のとき「ディレクトリとして存在するか」を確認します。
        """
        text = path_text.strip()
        if not text:
            badge.setText("（未設定）")
            badge.setStyleSheet("color: #757575; font-size: 10px;")
            return
        p = Path(text)
        if is_file:
            ok = p.is_file()
        else:
            ok = p.is_dir()
        if ok:
            badge.setText("✓ 確認済み — ファイル/フォルダが見つかりました")
            badge.setStyleSheet("color: #2e7d32; font-size: 10px; font-weight: bold;")
        else:
            badge.setText("✗ 見つかりません — パスを確認してください")
            badge.setStyleSheet("color: #c62828; font-size: 10px; font-weight: bold;")
