"""
controller/snap_exec.py
SNAP.exe を呼び出すユーティリティ関数。

SNAP は Windows 専用ソフトウェアのため、このモジュールは
Windows 環境での実行を前提としています。
"""

import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional


def snap_exec(
    snap_exe: str,
    input_file: str,
    *,
    type_prefix: str = "D",
    timeout: Optional[int] = None,
    stdout_callback: Optional[Callable[[str], None]] = None,
    on_finish: Optional[Callable[[int], None]] = None,
) -> subprocess.CompletedProcess:
    """
    SNAP.exe を同期実行します。

    Parameters
    ----------
    snap_exe : str
        SNAP.exe のフルパス。
    input_file : str
        解析入力ファイル (.s8i) のフルパス。
    type_prefix : str, optional
        SNAP バッチ実行フラグのプレフィックス（デフォルト "D"）。
        SNAP を自動実行するために /B{type_prefix} フラグを渡します。
    timeout : int, optional
        タイムアウト秒数（None で無制限）。
    stdout_callback : callable, optional
        標準出力の各行を受け取るコールバック関数。
        signature: ``callback(line: str) -> None``
    on_finish : callable, optional
        終了時に呼ばれるコールバック関数。
        signature: ``callback(returncode: int) -> None``

    Returns
    -------
    subprocess.CompletedProcess

    Raises
    ------
    FileNotFoundError
        SNAP.exe または入力ファイルが見つからない場合。
    subprocess.TimeoutExpired
        タイムアウト超過時。
    """
    exe_path = Path(snap_exe)
    inp_path = Path(input_file)

    if not exe_path.exists():
        raise FileNotFoundError(f"SNAP.exe が見つかりません: {exe_path}")
    if not inp_path.exists():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {inp_path}")

    # /B{type_prefix} フラグで SNAP をバッチモード（自動解析実行）で起動する
    cmd = [str(exe_path), f"/B{type_prefix}", str(inp_path)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="shift_jis",
        errors="replace",
        cwd=str(inp_path.parent),
    )

    output_lines = []
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip("\n")
            output_lines.append(line)
            if stdout_callback:
                stdout_callback(line)

        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise

    rc = proc.returncode
    if on_finish:
        on_finish(rc)

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=rc,
        stdout="\n".join(output_lines),
    )


def snap_exec_async(
    snap_exe: str,
    input_file: str,
    *,
    type_prefix: str = "D",
    timeout: Optional[int] = None,
    stdout_callback: Optional[Callable[[str], None]] = None,
    on_finish: Optional[Callable[[int], None]] = None,
) -> threading.Thread:
    """
    SNAP.exe を別スレッドで非同期実行します。
    Qt の UI スレッドをブロックしないために使います。

    Returns
    -------
    threading.Thread
        開始済みのスレッドオブジェクト。
    """
    def _run():
        snap_exec(
            snap_exe,
            input_file,
            type_prefix=type_prefix,
            timeout=timeout,
            stdout_callback=stdout_callback,
            on_finish=on_finish,
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
