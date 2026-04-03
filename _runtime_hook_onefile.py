"""
_runtime_hook_onefile.py
PyInstaller onefile モード用ランタイムフック。

onefile では実行時に sys._MEIPASS に一時展開されます。
PySide6 プラグインパスをその一時ディレクトリに向けることで、
「Qt プラグインが見つからない」エラーを防ぎます。
"""

import os
import sys

# 一時展開ディレクトリのパスを取得
_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

# Qt がプラグイン（スタイル・プラットフォーム等）を探すパスを設定
os.environ.setdefault('QT_PLUGIN_PATH', os.path.join(_base, 'PySide6', 'plugins'))
os.environ.setdefault('QML2_IMPORT_PATH', os.path.join(_base, 'PySide6', 'qml'))
