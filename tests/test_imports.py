"""
tests/test_imports.py
モジュールインポートのスモークテスト。

EXE ビルド前に「全モジュールが問題なく import できるか」を検証します。
PyInstaller の hiddenimports 漏れ・excludes の誤設定を事前に検出するのが目的です。

実行方法:
    pytest tests/test_imports.py -v

Qt（PySide6）が使えない環境（Linux CI 等）では UI テストは自動スキップされます。
"""

import importlib
import sys
import pytest


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _qt_available() -> bool:
    """PySide6 が import できる環境かどうかを返す。"""
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _import_ok(module_name: str) -> tuple[bool, str]:
    """
    指定モジュールを import し、成功/失敗と理由を返す。

    Returns:
        (ok: bool, reason: str)
    """
    try:
        importlib.import_module(module_name)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# controller パッケージ
# ---------------------------------------------------------------------------

class TestControllerImports:
    """controller/ 以下の全モジュールが import できることを確認する。"""

    MODULES = [
        "controller",
        "controller.snap_exec",
        "controller.updater",
        "controller.result",
        "controller.executor",
        "controller._path",
        "controller._utils",
        "controller.file",
        "controller.logger",
        "controller.types",
    ]

    @pytest.mark.parametrize("module", MODULES)
    def test_import(self, module):
        ok, reason = _import_ok(module)
        assert ok, f"import '{module}' failed — {reason}"


# ---------------------------------------------------------------------------
# app.models パッケージ（Qt 不要）
# ---------------------------------------------------------------------------

class TestAppModelsImports:
    """app/models/ 以下の全モデルが import できることを確認する。"""

    MODULES = [
        "app.models",
        "app.models.analysis_case",
        "app.models.project",
        "app.models.s8i_parser",
        "app.models.earthquake_wave",
        "app.models.performance_criteria",
    ]

    @pytest.mark.parametrize("module", MODULES)
    def test_import(self, module):
        ok, reason = _import_ok(module)
        assert ok, f"import '{module}' failed — {reason}"


# ---------------------------------------------------------------------------
# app.services パッケージ（Qt 依存のため PySide6 が使える環境のみ）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestAppServicesImports:
    """app/services/ の全モジュールが import できることを確認する（Qt 必須）。"""

    MODULES = [
        "app.services",
        "app.services.snap_evaluator",
        "app.services.optimizer",
        "app.services.validation",
        "app.services.report_generator",
        "app.services.autosave",
        "app.services.analysis_service",
    ]

    @pytest.mark.parametrize("module", MODULES)
    def test_import(self, module):
        ok, reason = _import_ok(module)
        assert ok, f"import '{module}' failed — {reason}"


# ---------------------------------------------------------------------------
# 外部依存ライブラリ（requirements.txt 全パッケージ）
# ---------------------------------------------------------------------------

class TestThirdPartyImports:
    """
    requirements.txt に記載された全ライブラリが利用可能かを確認する。

    EXE に同梱されるべきパッケージが揃っているかの簡易チェックです。
    """

    MODULES = [
        ("numpy", "numpy"),
        ("pandas", "pandas"),
        ("matplotlib", "matplotlib"),
        ("matplotlib._fontconfig_pattern", "matplotlib._fontconfig_pattern"),
        ("matplotlib.colors", "matplotlib.colors"),
        ("pyparsing", "pyparsing"),
        ("pyparsing.helpers", "pyparsing.helpers"),
        ("pyparsing.testing", "pyparsing.testing"),
        ("PIL", "PIL"),
        ("PIL.Image", "PIL.Image"),
        ("openpyxl", "openpyxl"),
        ("openpyxl.styles", "openpyxl.styles"),
        ("openpyxl.utils", "openpyxl.utils"),
    ]

    @pytest.mark.parametrize("label,module", MODULES)
    def test_import(self, label, module):
        ok, reason = _import_ok(module)
        assert ok, f"import '{module}' failed — {reason}"

    def test_pyparsing_uses_html(self):
        """
        pyparsing.helpers が html モジュールを使えることを確認する。

        EXE ビルド時に html を excludes に入れると
        『No module named html』で即クラッシュする既知の問題を検出します。
        """
        import html  # noqa: F401 — Python 標準ライブラリ
        import pyparsing.helpers  # noqa: F401
        # ここまで通れば OK

    def test_pyparsing_testing_uses_unittest(self):
        """
        pyparsing.testing が unittest を使えることを確認する。

        EXE ビルド時に unittest を excludes に入れると
        『No module named unittest』で即クラッシュする既知の問題を検出します。
        """
        import unittest  # noqa: F401 — Python 標準ライブラリ
        import pyparsing.testing  # noqa: F401
        # ここまで通れば OK

    def test_matplotlib_colors_uses_pil(self):
        """
        matplotlib.colors が PIL を使えることを確認する。

        EXE ビルド時に PIL を excludes に入れると
        『No module named PIL』で即クラッシュする既知の問題を検出します。
        """
        import PIL  # noqa: F401
        import matplotlib.colors  # noqa: F401
        # ここまで通れば OK


# ---------------------------------------------------------------------------
# Qt / PySide6（表示環境がある場合のみ実行）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestPySide6Imports:
    """PySide6 の主要モジュールが import できることを確認する。"""

    MODULES = [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtPrintSupport",
    ]

    @pytest.mark.parametrize("module", MODULES)
    def test_import(self, module):
        ok, reason = _import_ok(module)
        assert ok, f"import '{module}' failed — {reason}"

    def test_qdarktheme_import(self):
        ok, reason = _import_ok("qdarktheme")
        assert ok, f"qdarktheme import failed — {reason}"

    def test_qtawesome_import(self):
        ok, reason = _import_ok("qtawesome")
        assert ok, f"qtawesome import failed — {reason}"

    def test_matplotlib_pyside6_backend(self):
        """matplotlib の PySide6 バックエンドが使えることを確認する。"""
        ok, reason = _import_ok("matplotlib.backends.backend_qtagg")
        assert ok, f"matplotlib PySide6 backend import failed — {reason}"


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestAppUIImports:
    """
    app/ui/ の主要ウィジェットが import できることを確認する。

    matplotlib が pyparsing 経由で html を要求するため、
    このテストが失敗する場合は snap_controller.spec の excludes を見直してください。
    """

    CORE_MODULES = [
        "app.ui",
        "app.ui.theme",
        "app.ui.snap_params",
        "app.ui.main_window",
        "app.ui.welcome_widget",
        "app.ui.dashboard_widget",
        "app.ui.case_table",
        "app.ui.result_chart_widget",
        "app.ui.compare_chart_widget",
        "app.ui.case_compare_dialog",
    ]

    @pytest.mark.parametrize("module", CORE_MODULES)
    def test_import(self, module):
        ok, reason = _import_ok(module)
        assert ok, f"import '{module}' failed — {reason}"
