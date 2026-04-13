"""
app/ui/theme.py
テーママネージャー。

OS のダークモード設定を検出し、QPalette ベースでライト / ダーク切替を行います。
Fusion スタイルと組み合わせて使用します。

使い方::

    from app.ui.theme import ThemeManager

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    ThemeManager.apply(app)          # auto 検出
    ThemeManager.apply(app, "dark")  # 強制ダーク
"""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import Literal

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

import qdarktheme

logger = logging.getLogger(__name__)

ThemeMode = Literal["auto", "light", "dark"]

# QSettings キー
SETTINGS_ORG = "BAUES"
SETTINGS_APP = "snap-controller"
KEY_THEME = "ui/theme"

# pyqtdarktheme を使うため、手動の QPalette 色定義は最低限に抑えるか削除します
# （qdarktheme 側で適切に色が設定されるため不要になります）

# ケーステーブルのステータス背景色
STATUS_COLORS = {
    "light": {
        "PENDING":   QColor("#f5f5f5"),
        "RUNNING":   QColor("#fffde7"),
        "COMPLETED": QColor("#e8f5e9"),
        "ERROR":     QColor("#ffebee"),
    },
    "dark": {
        "PENDING":   QColor("#3c3c3c"),
        "RUNNING":   QColor("#4a4500"),
        "COMPLETED": QColor("#1b3a1b"),
        "ERROR":     QColor("#4a1a1a"),
    },
}

# ログウィジェットのスタイル
LOG_STYLES = {
    "light": {
        "background": "#ffffff",
        "foreground": "#1e1e1e",
        "default_color": "#333333",
    },
    "dark": {
        "background": "#1e1e1e",
        "foreground": "#d4d4d4",
        "default_color": "#d4d4d4",
    },
}

# matplotlib テーマ
MPL_STYLES = {
    "light": {
        "figure.facecolor": "#ffffff",
        "axes.facecolor":   "#ffffff",
        "axes.edgecolor":   "#333333",
        "axes.labelcolor":  "#333333",
        "text.color":       "#333333",
        "xtick.color":      "#333333",
        "ytick.color":      "#333333",
        "grid.color":       "#cccccc",
    },
    "dark": {
        "figure.facecolor": "#2d2d30",
        "axes.facecolor":   "#1e1e1e",
        "axes.edgecolor":   "#888888",
        "axes.labelcolor":  "#d4d4d4",
        "text.color":       "#d4d4d4",
        "xtick.color":      "#d4d4d4",
        "ytick.color":      "#d4d4d4",
        "grid.color":       "#444444",
    },
}


# ---------------------------------------------------------------------------
# OS ダークモード検出
# ---------------------------------------------------------------------------

def _detect_os_dark_mode() -> bool:
    """Windows のダークモード設定を検出します。"""
    system = platform.system()
    if system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return value == 0  # 0 = dark, 1 = light
        except Exception:
            logger.debug("Windowsダークモード検出失敗")
    elif system == "Darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip().lower() == "dark"
        except Exception:
            logger.debug("macOSダークモード検出失敗")
    return False


# ---------------------------------------------------------------------------
# ThemeManager
# ---------------------------------------------------------------------------

class ThemeManager:
    """アプリケーション全体のテーマを管理するクラス。"""

    _current: str = "light"  # "light" or "dark"

    @classmethod
    def current(cls) -> str:
        """現在のテーマ名を返します ("light" or "dark")。"""
        return cls._current

    @classmethod
    def is_dark(cls) -> bool:
        """現在ダークテーマかどうかを返します。"""
        return cls._current == "dark"

    @classmethod
    def apply(cls, app: QApplication, mode: ThemeMode = "auto") -> None:
        """
        アプリケーションにテーマを適用します。

        Parameters
        ----------
        app : QApplication
        mode : "auto" | "light" | "dark"
            "auto" の場合は OS のダークモード設定に従います。
        """
        if mode == "auto":
            use_dark = _detect_os_dark_mode()
        else:
            use_dark = (mode == "dark")

        cls._current = "dark" if use_dark else "light"
        
        # qdarktheme の適用
        qdarktheme.setup_theme(
            theme=cls._current,
            corner_shape="rounded",
            custom_colors={
                # 必要に応じてカスタムカラーを定義
                "[dark]": {
                    "primary": "#4C9EEB",
                },
                "[light]": {
                    "primary": "#0078D4",
                }
            }
        )

        # アプリ全体に少しモダンなフォントを適用する（オプション）
        font = QFont("Segoe UI", 9)
        app.setFont(font)

        # さらに独自の細かい QSS 調整があればここに追加
        # pyqtdarktheme が大部分をカバーするので、以前の QSS は削除し、
        # 必要最小限の調整だけを行います。
        app.setStyleSheet(app.styleSheet() + """
            QGroupBox {
                font-weight: bold;
                border: 1px solid palette(mid);
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
                left: 8px;
            }
        """)

    @classmethod
    def saved_mode(cls) -> ThemeMode:
        """QSettings に保存されたテーマモードを返します。"""
        s = QSettings(SETTINGS_ORG, SETTINGS_APP)
        mode = s.value(KEY_THEME, "auto")
        if mode in ("auto", "light", "dark"):
            return mode  # type: ignore
        return "auto"

    @classmethod
    def save_mode(cls, mode: ThemeMode) -> None:
        """テーマモードを QSettings に保存します。"""
        s = QSettings(SETTINGS_ORG, SETTINGS_APP)
        s.setValue(KEY_THEME, mode)
