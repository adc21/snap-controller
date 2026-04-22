"""NAP → s8i conversion via UIA menu clicks (no keystroke simulation).

Menu path (confirmed by UIA scan bl5eb4gos):
    メニュー バー → ﾌｧｲﾙ(F) → ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存(J),,,

Advantages over keyboard-based:
- No IME interference risk
- No dependency on window foreground state for key routing
- Menu item names are stable accessibility properties

Fallback to keyboard (Alt+F → J via VK codes) on UIA click failure.
"""
import io
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SNAP_EXE = r'C:\Program Files\SNAP Ver.8\Snap.exe'
TEST_DIR = Path(r'C:\Users\keita\App\ADC\snap-controller\tmp\nap_cli_test')
NAP_FILE = TEST_DIR / 'example_3D.NAP'
OUT_S8I = TEST_DIR / 'example_3D_uia.s8i'
SCREENSHOT_DIR = TEST_DIR / 'screenshots_uia'
SCREENSHOT_DIR.mkdir(exist_ok=True)

FILE_MENU_NAME = 'ﾌｧｲﾙ(F)'
SAVE_AS_TEXT_PREFIX = 'ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存'  # prefix match; full: "ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存(J),,,"


def log(msg: str):
    print(msg, flush=True)


def kill_snap():
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'Snap.exe'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def screenshot(name: str):
    try:
        from PIL import ImageGrab
        p = SCREENSHOT_DIR / f'{name}.png'
        ImageGrab.grab().save(str(p))
        log(f'   screenshot: {p.name}')
    except Exception as e:
        log(f'   screenshot failed: {e}')


kill_snap()
time.sleep(1)
if OUT_S8I.exists():
    OUT_S8I.unlink()

try:
    from pywinauto import Application, Desktop
    from pywinauto.findwindows import find_elements
    from pywinauto.keyboard import send_keys
    import win32gui
    import win32con
    import win32api
    import win32clipboard

    def vk_press(vk, modifiers=()):
        for m in modifiers:
            win32api.keybd_event(m, 0, 0, 0)
            time.sleep(0.02)
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        for m in reversed(modifiers):
            time.sleep(0.02)
            win32api.keybd_event(m, 0, win32con.KEYEVENTF_KEYUP, 0)

    log(f'NAP: {NAP_FILE}')
    log(f'OUT: {OUT_S8I}')

    # [1] Launch SNAP
    log('\n[1] Launching SNAP.exe')
    app = Application(backend='uia').start(
        f'"{SNAP_EXE}" "{NAP_FILE}"', wait_for_idle=False,
    )

    # [2] Locate main window
    log('[2] Waiting for main window')
    main = None
    hwnd = None
    deadline = time.time() + 30
    while time.time() < deadline:
        for w in Desktop(backend='uia').windows():
            try:
                if w.class_name() == 'SNAPV8_MainFrame':
                    main = w
                    hwnd = w.handle
                    break
            except Exception:
                pass
        if main:
            break
        time.sleep(0.5)
    if not main:
        raise RuntimeError('Main not found')
    log(f'   Main HWND=0x{hwnd:X}')

    # [3] Wait for NAP load + foreground
    log('\n[3] Wait 12s for NAP load + foreground')
    time.sleep(12)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception as e:
        log(f'   foreground err: {e}')
    screenshot('01_loaded')

    # [4] UIA: click ﾌｧｲﾙ(F) menu bar item
    log(f'\n[4] UIA click: "{FILE_MENU_NAME}"')
    file_menu_clicked = False
    try:
        # Search within SNAP main window only (avoid cross-app menu pollution)
        file_item = None
        for d in main.descendants(control_type='MenuItem'):
            try:
                if d.window_text() == FILE_MENU_NAME:
                    file_item = d
                    break
            except Exception:
                pass
        if file_item is None:
            raise RuntimeError(f'{FILE_MENU_NAME} not found under main')
        log(f'   Found ﾌｧｲﾙ MenuItem')
        # Invoke via UIA select() (opens submenu) or click_input()
        try:
            file_item.select()
            log('   select() OK')
        except Exception:
            file_item.click_input()
            log('   click_input() OK')
        file_menu_clicked = True
        time.sleep(0.8)
        screenshot('02_file_open')
    except Exception as e:
        log(f'   UIA click ﾌｧｲﾙ failed: {e}')

    # Fallback: Alt+F via VK codes
    if not file_menu_clicked:
        log('   Fallback: Alt+F via VK')
        vk_press(0x46, modifiers=[0x12])  # Alt+F
        time.sleep(1.0)
        screenshot('02_file_open_fallback')

    # [5] UIA: click ﾃｷｽﾄﾃﾞｰﾀﾌｧｲﾙに保存(J) submenu item
    log(f'\n[5] UIA click submenu starting with: "{SAVE_AS_TEXT_PREFIX}"')
    submenu_clicked = False
    try:
        # Submenus often hosted in a separate popup window; use global find_elements
        menu_items = find_elements(
            backend='uia', control_type='MenuItem', top_level_only=False,
        )
        target = None
        for m in menu_items:
            try:
                if m.name and m.name.startswith(SAVE_AS_TEXT_PREFIX):
                    target = m
                    break
            except Exception:
                pass
        if target is None:
            raise RuntimeError('save-as-text MenuItem not found in global scan')
        log(f'   Found: "{target.name}"')

        # Wrap the ElementInfo so we can click
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.controls.uiawrapper import UIAWrapper
        wrapped = UIAWrapper(target)
        try:
            wrapped.select()
            log('   select() OK')
        except Exception:
            wrapped.click_input()
            log('   click_input() OK')
        submenu_clicked = True
        time.sleep(2.5)
        screenshot('03_submenu_clicked')
    except Exception as e:
        log(f'   UIA submenu click failed: {e}')
        log(traceback.format_exc())

    # Fallback: press J via VK
    if not submenu_clicked:
        log('   Fallback: J via VK')
        vk_press(0x4A)  # J
        time.sleep(2.5)
        screenshot('03_submenu_clicked_fallback')

    # [6] Handle save dialog (same approach as keyboard PoC: clipboard paste)
    log('\n[6] Locating save dialog (#32770 owned by SNAP)')
    save_hwnd = None
    found_dialogs = []

    def enum_cb(h, lparam):
        if not win32gui.IsWindowVisible(h):
            return True
        if win32gui.GetClassName(h) == '#32770':
            owner = win32gui.GetWindow(h, win32con.GW_OWNER)
            if owner == hwnd:
                found_dialogs.append(h)
        return True

    win32gui.EnumWindows(enum_cb, None)
    if found_dialogs:
        save_hwnd = found_dialogs[0]
        log(f'   save_hwnd=0x{save_hwnd:X}')
    else:
        raise RuntimeError('Save dialog not found')

    # [7] Fill filename via clipboard + Ctrl+V, click 保存(S)
    log('\n[7] Fill filename via clipboard, click 保存(S)')
    app_dlg = Application(backend='uia').connect(handle=save_hwnd)
    save_dlg = app_dlg.window(handle=save_hwnd)

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(13, str(OUT_S8I))  # CF_UNICODETEXT = 13
    win32clipboard.CloseClipboard()

    edit = save_dlg.child_window(title='ファイル名:', control_type='Edit')
    edit.set_focus()
    time.sleep(0.3)

    VK_A, VK_V, VK_CONTROL = 0x41, 0x56, 0x11
    vk_press(VK_A, modifiers=[VK_CONTROL])
    time.sleep(0.15)
    vk_press(VK_V, modifiers=[VK_CONTROL])
    time.sleep(0.5)
    screenshot('04_filename_pasted')

    save_btn = save_dlg.child_window(title='保存(S)', control_type='Button')
    save_btn.click_input()
    log('   保存(S) clicked')
    time.sleep(2.5)
    screenshot('05_after_save')

    # [8] Overwrite confirmation (if any)
    log('\n[8] Overwrite dialog check')
    overwrite_hwnd = None

    def enum_ow(h, lparam):
        if not win32gui.IsWindowVisible(h):
            return True
        if win32gui.GetClassName(h) == '#32770':
            if win32gui.GetWindow(h, win32con.GW_OWNER) == hwnd:
                title = win32gui.GetWindowText(h)
                if '確認' in title or title == '' or 'SNAP' in title:
                    # Skip the save-as dialog we already closed
                    if h != save_hwnd:
                        nonlocal_holder.append(h)
        return True

    nonlocal_holder = []
    win32gui.EnumWindows(enum_ow, None)
    if nonlocal_holder:
        overwrite_hwnd = nonlocal_holder[0]
        log(f'   overwrite hwnd=0x{overwrite_hwnd:X}')
        send_keys('y')
    screenshot('06_final')

    # [9] Wait for file
    log('\n[9] Waiting for output file')
    deadline = time.time() + 45
    success = False
    while time.time() < deadline:
        if OUT_S8I.exists() and OUT_S8I.stat().st_size > 0:
            log(f'   SUCCESS: {OUT_S8I.stat().st_size:,} bytes')
            success = True
            break
        time.sleep(0.5)
    if not success:
        log('   FAIL: no output')

except Exception as e:
    log(f'\n!!! Exception: {e}')
    log(traceback.format_exc())

finally:
    time.sleep(1)
    kill_snap()
    log('\n=== Done ===')
    if OUT_S8I.exists():
        log(f'RESULT: {OUT_S8I.stat().st_size:,} bytes')
    else:
        log('RESULT: no output file')
