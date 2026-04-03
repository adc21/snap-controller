"""
run_app.py
snap-controller アプリケーションのエントリーポイント。

Windows で実行:
    python run_app.py

または run_app.bat をダブルクリック。

EXE スモークテスト（GUI を開かずに import チェックだけ実行）:
    snap-controller.exe --check
    python run_app.py --check
"""

import sys


def _run_check() -> None:
    """
    --check モード: GUI を表示せずに全モジュールの import を検証して終了する。

    EXE ビルド後のスモークテストで使用します。
    成功時は終了コード 0、失敗時は終了コード 1 を返します。
    """
    print("snap-controller import check starting...")
    failed: list[str] = []

    checks = [
        # 外部ライブラリ（PyInstaller の除外設定ミスを検出）
        "numpy",
        "pandas",
        "matplotlib",
        "matplotlib.backends.backend_qtagg",
        "pyparsing",
        "pyparsing.helpers",
        "html",           # pyparsing.helpers が必要とする標準ライブラリ
        "openpyxl",
        "qdarktheme",
        "qtawesome",
        # PySide6
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # コントローラ
        "controller",
        "controller.snap_exec",
        "controller.updater",
        "controller.result",
        "controller.executor",
        # アプリモデル
        "app.models",
        "app.models.analysis_case",
        "app.models.project",
        "app.models.s8i_parser",
        # アプリ UI（深い import チェーンを一括検証）
        "app.ui.theme",
        "app.ui.main_window",
    ]

    import importlib
    for mod in checks:
        try:
            importlib.import_module(mod)
            print(f"  OK  {mod}")
        except Exception as e:
            print(f"  NG  {mod}  ({type(e).__name__}: {e})")
            failed.append(mod)

    print()
    if failed:
        print(f"CHECK FAILED: {len(failed)} module(s) could not be imported.")
        for m in failed:
            print(f"  - {m}")
        sys.exit(1)
    else:
        print("CHECK OK: All modules imported successfully.")
        sys.exit(0)


def main() -> None:
    # --check フラグ: GUI を開かずにインポート検証だけ行う
    if "--check" in sys.argv:
        _run_check()
        return  # sys.exit() が呼ばれるので到達しないが念のため

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt  # noqa: F401

    from app.ui.main_window import MainWindow
    from app.ui.theme import ThemeManager

    app = QApplication(sys.argv)
    app.setApplicationName("snap-controller")
    app.setOrganizationName("BAUES")
    app.setStyle("Fusion")

    # テーマ適用（保存された設定 or auto 検出）
    theme_mode = ThemeManager.saved_mode()
    ThemeManager.apply(app, theme_mode)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
