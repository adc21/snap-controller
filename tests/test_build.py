"""
tests/test_build.py
EXE ビルド後の検証テスト（Windows 専用）。

build.bat / build.py でビルドした後に以下で実行してください:

    pytest tests/test_build.py -v

Linux / macOS では全テストが自動的にスキップされます。

テスト内容:
  1. EXE ファイルが存在するか
  2. EXE のファイルサイズが適切か（PySide6 + matplotlib の最小サイズを確認）
  3. EXE が --check フラグで正常起動・終了するか（スモークテスト）
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

# EXE の期待パス（build.bat の出力先）
EXE_PATH = Path(__file__).parent.parent / "dist_win" / "snap-controller.exe"

# EXE の最小許容サイズ（PySide6 + matplotlib 同梱のため通常 80MB 超）
MIN_SIZE_MB = 50

# --check モードのタイムアウト（秒）
CHECK_TIMEOUT_SEC = 60


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _skip_if_not_windows():
    return pytest.mark.skipif(
        sys.platform != "win32",
        reason="EXE は Windows 環境でのみ実行可能です",
    )


# ---------------------------------------------------------------------------
# テスト本体
# ---------------------------------------------------------------------------

@_skip_if_not_windows()
class TestExeExists:
    """EXE ファイルの存在確認。"""

    def test_exe_file_exists(self):
        """dist_win/snap-controller.exe が存在する。"""
        assert EXE_PATH.exists(), (
            f"EXE が見つかりません: {EXE_PATH}\n"
            "build.bat を実行してビルドしてください。"
        )

    def test_exe_is_file(self):
        """対象パスがディレクトリではなくファイルである。"""
        assert EXE_PATH.is_file(), f"{EXE_PATH} はファイルではありません"


@_skip_if_not_windows()
class TestExeSize:
    """EXE ファイルサイズの妥当性確認。"""

    def test_exe_size_above_minimum(self):
        """
        EXE サイズが最低ラインを超えている。

        PySide6 + matplotlib を正しく同梱すると通常 80〜150 MB 程度になります。
        50 MB を下回る場合、重要なパッケージが欠落している可能性があります。
        """
        assert EXE_PATH.exists(), f"EXE が見つかりません: {EXE_PATH}"
        size_mb = EXE_PATH.stat().st_size / (1024 * 1024)
        assert size_mb >= MIN_SIZE_MB, (
            f"EXE サイズが小さすぎます: {size_mb:.1f} MB "
            f"(最低 {MIN_SIZE_MB} MB 必要)\n"
            "依存ライブラリが正しく同梱されていない可能性があります。"
        )

    def test_exe_size_below_maximum(self):
        """
        EXE サイズが上限を超えていない（異常な肥大化の検出）。

        400 MB を超える場合、不要なパッケージが含まれている可能性があります。
        """
        assert EXE_PATH.exists(), f"EXE が見つかりません: {EXE_PATH}"
        size_mb = EXE_PATH.stat().st_size / (1024 * 1024)
        assert size_mb <= 400, (
            f"EXE サイズが大きすぎます: {size_mb:.1f} MB\n"
            "snap_controller.spec の excludes を見直してください。"
        )


@_skip_if_not_windows()
class TestExeSmokeCheck:
    """
    EXE スモークテスト（--check フラグを使った起動確認）。

    run_app.py の --check モードを利用して、GUI を表示せずに
    全モジュールの import が成功するかを検証します。

    これにより PyInstaller の hiddenimports 漏れ・excludes 誤設定を
    ビルド直後に検出できます。
    """

    def test_check_flag_exits_zero(self):
        """
        --check で起動して終了コード 0 が返ること。

        終了コード 0 = 全モジュールが正常に import できた。
        終了コード 1 = import 失敗（NG モジュール名が stdout に出力される）。
        """
        assert EXE_PATH.exists(), f"EXE が見つかりません: {EXE_PATH}"

        result = subprocess.run(
            [str(EXE_PATH), "--check"],
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SEC,
        )

        # 失敗時は stdout/stderr を表示して原因特定しやすくする
        assert result.returncode == 0, (
            f"EXE --check が失敗しました (returncode={result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

    def test_check_flag_output_contains_ok(self):
        """--check 成功時に 'CHECK OK' が出力されること。"""
        assert EXE_PATH.exists(), f"EXE が見つかりません: {EXE_PATH}"

        result = subprocess.run(
            [str(EXE_PATH), "--check"],
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SEC,
        )

        assert "CHECK OK" in result.stdout, (
            f"'CHECK OK' が stdout に見つかりません\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

    def test_check_flag_no_ng_modules(self):
        """--check 成功時に '  NG  ' が出力されないこと。"""
        assert EXE_PATH.exists(), f"EXE が見つかりません: {EXE_PATH}"

        result = subprocess.run(
            [str(EXE_PATH), "--check"],
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SEC,
        )

        ng_lines = [
            line for line in result.stdout.splitlines()
            if line.strip().startswith("NG")
        ]
        assert not ng_lines, (
            f"import に失敗したモジュールがあります:\n"
            + "\n".join(ng_lines)
        )

    def test_check_completes_within_timeout(self):
        """--check が {CHECK_TIMEOUT_SEC} 秒以内に完了すること。"""
        assert EXE_PATH.exists(), f"EXE が見つかりません: {EXE_PATH}"

        start = time.monotonic()
        try:
            subprocess.run(
                [str(EXE_PATH), "--check"],
                capture_output=True,
                text=True,
                timeout=CHECK_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            pytest.fail(
                f"--check が {elapsed:.1f} 秒経過してもタイムアウトしました。"
            )
        elapsed = time.monotonic() - start
        # 参考情報として出力
        print(f"\n--check 完了時間: {elapsed:.2f} 秒")
