"""NAP → s8i 変換ユーティリティ。

SNAP.exe は NAP→s8i 変換用の CLI フラグを持たないため、GUI 自動化で
メニュー操作を再現する。経路:

    [ファイル(F)] → [テキストデータファイルに保存(J)] → 保存ダイアログ → 保存(S)

アプローチ:
- メニューバー項目 "ﾌｧｲﾙ(F)" は UIA から直接クリック (select)
- サブメニュー "ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存(J)" は SNAP の独自描画で UIA に露出しないため
  VK キーコード 'J' で選択 (IME を回避するため VK 直送)
- 保存ダイアログのファイル名はクリップボード + Ctrl+V で投入
  (WM_SETTEXT は ANSI 版が走ってパスが文字化けする / "次の文字は使えません"
  エラーを引き起こすため)
- 保存(S) ボタンは UIA からクリック

Windows 専用。pywin32 / pywinauto / Pillow が必要。
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SNAP_EXE = r"C:\Program Files\SNAP Ver.8\Snap.exe"

# UIA スキャンで確認済みのメニュー項目名 (半角カタカナ、接尾辞含む)
_FILE_MENU_NAME = "ﾌｧｲﾙ(F)"
_SAVE_AS_TEXT_PREFIX = "ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存"

# VK コード (WinAPI)
_VK_J = 0x4A
_VK_F = 0x46
_VK_A = 0x41
_VK_V = 0x56
_VK_MENU = 0x12  # Alt
_VK_CONTROL = 0x11


class NapConversionError(RuntimeError):
    """NAP→s8i 変換失敗時に送出される例外。"""


def convert_nap_to_s8i(
    nap_path: str | Path,
    out_s8i_path: str | Path,
    *,
    snap_exe: str = DEFAULT_SNAP_EXE,
    load_timeout: float = 30.0,
    save_timeout: float = 45.0,
    nap_load_wait: float = 12.0,
) -> Path:
    """.NAP ファイルを SNAP.exe の GUI 経由で .s8i に変換する。

    Parameters
    ----------
    nap_path
        入力 NAP ファイル。
    out_s8i_path
        出力 s8i ファイル。同名が既にあれば上書きされる (確認ダイアログに ``Y`` を送る)。
    snap_exe
        SNAP.exe のフルパス。
    load_timeout
        SNAP メインウィンドウ検出までのタイムアウト秒。
    save_timeout
        保存ボタンクリック後にファイルが生成されるまで待つ最大秒数。
    nap_load_wait
        NAP 読み込み完了を待つ固定秒数 (SNAP は完了通知が無いため)。

    Returns
    -------
    Path
        生成された s8i のパス。

    Raises
    ------
    NapConversionError
        変換のいずれかのステップで失敗した場合。
    FileNotFoundError
        SNAP.exe または NAP ファイルが見つからない場合。
    """
    nap = Path(nap_path)
    out = Path(out_s8i_path)
    exe = Path(snap_exe)

    if not exe.exists():
        raise FileNotFoundError(f"SNAP.exe が見つかりません: {exe}")
    if not nap.exists():
        raise FileNotFoundError(f"NAP ファイルが見つかりません: {nap}")

    # Windows 専用依存を関数内で import (Linux CI で import 時に落ちるのを防ぐ)
    try:
        from pywinauto import Application, Desktop
        from pywinauto.findwindows import find_elements
        from pywinauto.keyboard import send_keys
        import win32api
        import win32clipboard
        import win32con
        import win32gui
    except ImportError as e:
        raise NapConversionError(
            "NAP 変換には pywinauto / pywin32 が必要です。\n"
            "次を実行してインストールしてください:\n"
            "    pip install pywinauto pywin32\n"
            f"(詳細: {e})"
        ) from e

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        # 上書き確認ダイアログを減らすため事前削除 (失敗時は上書き確認を経由)
        try:
            out.unlink()
        except OSError:
            pass

    def _vk_press(vk: int, modifiers: tuple[int, ...] = ()) -> None:
        for m in modifiers:
            win32api.keybd_event(m, 0, 0, 0)
            time.sleep(0.02)
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        for m in reversed(modifiers):
            time.sleep(0.02)
            win32api.keybd_event(m, 0, win32con.KEYEVENTF_KEYUP, 0)

    def _kill_snap() -> None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "Snap.exe"],
                capture_output=True, timeout=10,
            )
        except Exception:
            logger.debug("taskkill 失敗は無視", exc_info=True)

    logger.info("NAP 変換開始: %s → %s", nap, out)

    try:
        # [1] SNAP 起動
        app = Application(backend="uia").start(
            f'"{exe}" "{nap}"', wait_for_idle=False,
        )

        # [2] メインウィンドウ検出
        main = None
        hwnd: Optional[int] = None
        deadline = time.time() + load_timeout
        while time.time() < deadline:
            for w in Desktop(backend="uia").windows():
                try:
                    if w.class_name() == "SNAPV8_MainFrame":
                        main = w
                        hwnd = w.handle
                        break
                except Exception:
                    continue
            if main:
                break
            time.sleep(0.5)
        if not main or hwnd is None:
            raise NapConversionError("SNAP メインウィンドウが見つかりません")
        logger.debug("main HWND=0x%X", hwnd)

        # [3] NAP 読み込み待機 + フォアグラウンド化
        time.sleep(nap_load_wait)
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
        except Exception:
            logger.debug("SetForegroundWindow 失敗", exc_info=True)

        # [4] ﾌｧｲﾙ(F) メニューを開く (UIA)
        file_menu_opened = False
        try:
            for d in main.descendants(control_type="MenuItem"):
                try:
                    if d.window_text() == _FILE_MENU_NAME:
                        d.select()
                        file_menu_opened = True
                        break
                except Exception:
                    continue
        except Exception:
            logger.debug("UIA File メニュー取得失敗", exc_info=True)

        if not file_menu_opened:
            # フォールバック: Alt+F
            logger.debug("UIA 失敗 → Alt+F フォールバック")
            _vk_press(_VK_F, modifiers=(_VK_MENU,))
        time.sleep(0.8)

        # [5] ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存(J) を選択
        # SNAP のサブメニューは UIA に露出しないので VK J を直接送る
        _vk_press(_VK_J)
        time.sleep(2.5)

        # [6] 保存ダイアログ検出
        save_hwnd = _find_owned_dialog(hwnd, win32gui, win32con)
        if not save_hwnd:
            # 稀に UIA select() でメニューが開かない場合があるので Alt+F+J を再送
            logger.warning("保存ダイアログ未検出 → Alt+F+J 再送")
            _vk_press(_VK_F, modifiers=(_VK_MENU,))
            time.sleep(0.8)
            _vk_press(_VK_J)
            time.sleep(2.5)
            save_hwnd = _find_owned_dialog(hwnd, win32gui, win32con)
        if not save_hwnd:
            raise NapConversionError("保存ダイアログが開きませんでした")
        logger.debug("save dialog HWND=0x%X", save_hwnd)

        # [7] ファイル名投入 (クリップボード→Ctrl+V)
        app_dlg = Application(backend="uia").connect(handle=save_hwnd)
        save_dlg = app_dlg.window(handle=save_hwnd)

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(out))
        finally:
            win32clipboard.CloseClipboard()

        edit = save_dlg.child_window(title="ファイル名:", control_type="Edit")
        edit.set_focus()
        time.sleep(0.3)
        _vk_press(_VK_A, modifiers=(_VK_CONTROL,))
        time.sleep(0.15)
        _vk_press(_VK_V, modifiers=(_VK_CONTROL,))
        time.sleep(0.5)

        save_btn = save_dlg.child_window(title="保存(S)", control_type="Button")
        save_btn.click_input()
        time.sleep(2.0)

        # [8] 上書き確認 (まだ残っていれば Y を送る)
        overwrite_hwnd = _find_overwrite_dialog(hwnd, save_hwnd, win32gui, win32con)
        if overwrite_hwnd:
            logger.debug("overwrite dialog HWND=0x%X", overwrite_hwnd)
            send_keys("y")

        # [9] ファイル生成待機
        deadline = time.time() + save_timeout
        while time.time() < deadline:
            if out.exists() and out.stat().st_size > 0:
                logger.info("NAP→s8i 変換成功: %s (%d bytes)", out, out.stat().st_size)
                return out
            time.sleep(0.5)

        raise NapConversionError(
            f"s8i ファイルが生成されませんでした: {out} "
            f"(タイムアウト {save_timeout:.0f}s)"
        )

    finally:
        time.sleep(0.5)
        _kill_snap()


def _find_owned_dialog(main_hwnd: int, win32gui, win32con) -> Optional[int]:
    """SNAP メインウィンドウに所有された #32770 ダイアログを 1 つ返す。"""
    found: list[int] = []

    def _cb(h, _lparam):
        if not win32gui.IsWindowVisible(h):
            return True
        if win32gui.GetClassName(h) == "#32770":
            if win32gui.GetWindow(h, win32con.GW_OWNER) == main_hwnd:
                found.append(h)
        return True

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None


def _find_overwrite_dialog(
    main_hwnd: int, exclude_hwnd: int, win32gui, win32con
) -> Optional[int]:
    """保存ダイアログ以外の、SNAP 所有の #32770 (上書き確認等) を返す。"""
    found: list[int] = []

    def _cb(h, _lparam):
        if h == exclude_hwnd or not win32gui.IsWindowVisible(h):
            return True
        if win32gui.GetClassName(h) == "#32770":
            if win32gui.GetWindow(h, win32con.GW_OWNER) == main_hwnd:
                found.append(h)
        return True

    win32gui.EnumWindows(_cb, None)
    return found[0] if found else None
