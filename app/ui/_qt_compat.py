"""
app/ui/_qt_compat.py
オプショナルなQt依存ライブラリの互換シム。

qdarktheme / qtawesome がインストールされていないテスト環境でも
インポートが通るようにするフォールバックを提供します。
本番環境では実際のライブラリが使われます。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# qtawesome — アイコンライブラリ
# ---------------------------------------------------------------------------
try:
    import qtawesome as qta
    HAS_QTA = True
except ImportError:
    class _FakeQtawesome:
        """qtawesome が未インストール時のフォールバック。"""

        def icon(self, *args: object, **kwargs: object) -> object:
            """空の QIcon を返します。"""
            try:
                from PySide6.QtGui import QIcon
                return QIcon()
            except ImportError:
                return None  # type: ignore[return-value]

        def __getattr__(self, name: str) -> object:
            return self.icon

    qta = _FakeQtawesome()  # type: ignore[assignment]
    HAS_QTA = False

__all__ = ["qta", "HAS_QTA"]
