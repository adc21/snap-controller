@echo off
setlocal
cd /d %~dp0

echo 結果を build_log.txt に保存します...
echo.

(
echo ============================================
echo  snap-controller デバッグビルド
echo ============================================
echo 日時: %DATE% %TIME%
echo フォルダ: %CD%
echo.

echo --- Python 確認 ---
python --version
echo.

echo --- パッケージインストール ---
python -m pip install -r requirements_build.txt
echo.

echo --- インポートテスト ---
python -c "import PySide6; print('PySide6 OK:', PySide6.__version__)"
python -c "import qdarktheme; print('qdarktheme OK')"
python -c "import qtawesome; print('qtawesome OK')"
python -c "import matplotlib; print('matplotlib OK')"
python -c "import numpy; print('numpy OK')"
python -c "import pandas; print('pandas OK')"
python -c "import openpyxl; print('openpyxl OK')"
python -c "import PyInstaller; print('PyInstaller OK:', PyInstaller.__version__)"
echo.

echo --- アプリモジュールテスト ---
python -c "import sys; sys.path.insert(0,'.'); mods=['controller','app.models.analysis_case','app.ui.theme','app.ui.main_window']; [(__import__(m), print('OK:',m)) for m in mods]" 2>&1
echo.

echo --- ビルド実行 ---
if exist build rd /s /q build 2^>/dev/null
if exist dist  rd /s /q dist  2^>/dev/null
python -m PyInstaller snap_controller.spec --clean --noconfirm --log-level WARN
echo PyInstaller 終了コード: %ERRORLEVEL%
echo.

echo --- 結果確認 ---
if exist "dist\snap-controller.exe" (
    echo [成功] dist\snap-controller.exe が作成されました
    dir "dist\snap-controller.exe"
) else (
    echo [失敗] dist\snap-controller.exe が見つかりません
    if exist dist dir dist
)
) > build_log.txt 2>&1

echo 完了。build_log.txt を確認してください。
echo.
type build_log.txt
echo.
pause
