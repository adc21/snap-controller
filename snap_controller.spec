# -*- mode: python ; coding: utf-8 -*-
"""
snap_controller.spec
PyInstaller ビルド設定ファイル（--onefile モード）。

使い方:
    pyinstaller snap_controller.spec

または build.bat を実行してください。

【onefile について】
全依存ファイルを1つの .exe に梱包します。
配布は snap-controller.exe 1ファイルだけで完結します。
初回起動時は一時フォルダへの展開があるため数秒かかりますが、
2回目以降はキャッシュが使われるため高速になります。

【excludes の方針】
  - Python 標準ライブラリは一切除外しない（matplotlib/pyparsing 等が予期せず依存するため）
  - サードパーティも「確実に不要なもの」だけ除外する
  - 過去に問題になったもの: html, unittest, PIL → 除外すると連鎖クラッシュ
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# アプリルートディレクトリ
APP_ROOT = Path(SPECPATH)

# ---------------------------------------------------------------------------
# collect_all で依存関係が複雑なパッケージを完全収集する
# （手動で hiddenimports を列挙すると漏れが起きやすいため）
# ---------------------------------------------------------------------------
matplotlib_datas,  matplotlib_binaries,  matplotlib_hidden  = collect_all('matplotlib')
pyparsing_datas,   pyparsing_binaries,   pyparsing_hidden   = collect_all('pyparsing')
pil_datas,         pil_binaries,         pil_hidden         = collect_all('PIL')

a = Analysis(
    ['run_app.py'],
    pathex=[str(APP_ROOT)],
    binaries=(
        []
        + matplotlib_binaries
        + pyparsing_binaries
        + pil_binaries
    ),
    datas=(
        []
        + matplotlib_datas
        + pyparsing_datas
        + pil_datas
    ),
    hiddenimports=(
        matplotlib_hidden
        + pyparsing_hidden
        + pil_hidden
        + [
            # PySide6
            'PySide6.QtCore',
            'PySide6.QtGui',
            'PySide6.QtWidgets',
            'PySide6.QtPrintSupport',
            # pandas / numpy
            'pandas',
            'numpy',
            # テーマ・アイコン
            'qdarktheme',
            'qtawesome',
            'qtawesome.iconic_font',
            # Excel 出力
            'openpyxl',
            'openpyxl.styles',
            'openpyxl.utils',
            # コントローラ
            'controller',
            'controller.updater',
            'controller.result',
            'controller.snap_exec',
            'controller.executor',
            'controller._path',
            # アプリモデル
            'app',
            'app.models',
            'app.models.analysis_case',
            'app.models.project',
            'app.models.s8i_parser',
            'app.models.damper_catalog',
            'app.models.earthquake_wave',
            'app.models.case_template',
            'app.models.performance_criteria',
            # アプリサービス
            'app.services',
            'app.services.analysis_service',
            'app.services.optimizer',
            'app.services.snap_evaluator',
            'app.services.report_generator',
            'app.services.autosave',
            'app.services.validation',
            'app.services.transfer_function_service',
            'app.services.irdt_designer',
            # scipy（伝達関数解析で使用）
            'scipy',
            'scipy.signal',
            'scipy.optimize',
            # アプリ UI（全ウィジェット）
            'app.ui',
            'app.ui.main_window',
            'app.ui.welcome_widget',
            'app.ui.setup_guide_widget',
            'app.ui.sidebar_widget',
            'app.ui.dashboard_widget',
            'app.ui.case_table',
            'app.ui.case_edit_dialog',
            'app.ui.case_compare_dialog',
            'app.ui.template_dialog',
            'app.ui.result_chart_widget',
            'app.ui.compare_chart_widget',
            'app.ui.envelope_chart_widget',
            'app.ui.time_history_widget',
            'app.ui.radar_chart_widget',
            'app.ui.result_table_widget',
            'app.ui.ranking_widget',
            'app.ui.sensitivity_widget',
            'app.ui.batch_queue_widget',
            'app.ui.run_selection_widget',
            'app.ui.damper_placement_widget',
            'app.ui.damper_catalog_dialog',
            'app.ui.earthquake_wave_dialog',
            'app.ui.multi_wave_dialog',
            'app.ui.model_info_widget',
            'app.ui.file_preview_widget',
            'app.ui.criteria_dialog',
            'app.ui.optimizer_dialog',
            'app.ui.sweep_dialog',
            'app.ui.settings_dialog',
            'app.ui.validation_dialog',
            'app.ui.export_dialog',
            'app.ui.shortcut_help_dialog',
            'app.ui.log_widget',
            'app.ui.snap_params',
            'app.ui.step_nav_footer',
            'app.ui.theme',
            # 伝達関数・iRDT・ピーク最小化
            'app.ui.transfer_function_widget',
            'app.ui.binary_result_widget',
            'app.ui.unified_optimizer_dialog',
            'app.ui.irdt_wizard_dialog',
            'app.ui.peak_minimizer_dialog',
            'app.ui.dyd_override_widget',
            # k-DB 連携
            'app.models.kdb_reader',
            'app.ui.kdb_browser_dialog',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['_runtime_hook_onefile.py'],
    excludes=[
        # ---------------------------------------------------------------
        # ※ Python 標準ライブラリは絶対に除外しない。
        #   過去に html / unittest / PIL を除外してクラッシュが続いた教訓。
        #
        # 除外対象 = 「ビルド環境にインストールされておらず、
        #             かつこのアプリが一切使わないサードパーティ」のみ。
        # ---------------------------------------------------------------
        'tkinter',    # Qt アプリなので Tk は不要（PySide6 と競合リスクあり）
        'IPython',
        'jupyter',
        'notebook',
        'sklearn',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --onefile: binaries / zipfiles / datas を EXE に直接渡す（COLLECT 不要）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,     # ← onefile: ここに含める
    a.zipfiles,     # ← onefile: ここに含める
    a.datas,        # ← onefile: ここに含める
    exclude_binaries=False,   # ← onefile では False
    name='snap-controller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,               # UPX 圧縮（インストール済みの場合）
    upx_exclude=[],
    runtime_tmpdir=None,    # 一時展開先（None = %TEMP% 配下に自動生成）
    console=False,          # コンソールウィンドウを非表示
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)
