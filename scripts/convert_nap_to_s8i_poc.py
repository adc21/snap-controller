"""PoC v3: NAP → s8i conversion — diagnostic screenshot version.

Captures screenshots at key points so we can see what's actually happening.
Uses pywinauto's own menu_select mechanism where possible.
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
OUT_S8I = TEST_DIR / 'example_3D_converted.s8i'
SCREENSHOT_DIR = TEST_DIR / 'screenshots'
SCREENSHOT_DIR.mkdir(exist_ok=True)


def kill_snap():
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'Snap.exe'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def log(msg: str):
    print(msg, flush=True)


def screenshot(name: str):
    """Take a full-screen screenshot."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        path = SCREENSHOT_DIR / f'{name}.png'
        img.save(str(path))
        log(f'   screenshot: {path.name}')
    except Exception as e:
        log(f'   screenshot failed: {e}')


kill_snap()
time.sleep(1)

if OUT_S8I.exists():
    OUT_S8I.unlink()

try:
    from pywinauto import Application, Desktop
    from pywinauto.keyboard import send_keys
    import win32gui
    import win32con

    log(f'NAP: {NAP_FILE}')
    log(f'OUT: {OUT_S8I}')

    log('\n[1] Launching SNAP.exe')
    app = Application(backend='uia').start(
        f'"{SNAP_EXE}" "{NAP_FILE}"',
        wait_for_idle=False,
    )

    log('[2] Waiting for main window...')
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
    log(f'  Main: "{main.window_text()}" HWND=0x{hwnd:X}')

    log('\n[2b] Waiting 12s for NAP load')
    time.sleep(12)
    screenshot('01_after_load')

    # Forcefully bring SNAP to foreground using win32 API
    log('\n[2c] Forcing SNAP to foreground via win32')
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.5)
        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg)
        log(f'   Foreground now: HWND=0x{fg:X} title="{fg_title}"')
    except Exception as e:
        log(f'   foreground failed: {e}')

    screenshot('02_focused')

    log('\n[3] Sending %fj via VK codes (bypasses IME)')
    import win32api

    def vk_press(vk, modifiers=()):
        """Press VK with modifiers. modifiers: list of VK to hold."""
        for m in modifiers:
            win32api.keybd_event(m, 0, 0, 0)
            time.sleep(0.02)
        win32api.keybd_event(vk, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        for m in reversed(modifiers):
            time.sleep(0.02)
            win32api.keybd_event(m, 0, win32con.KEYEVENTF_KEYUP, 0)

    VK_F = 0x46
    VK_J = 0x4A
    VK_MENU = 0x12  # Alt

    # Alt+F
    vk_press(VK_F, modifiers=[VK_MENU])
    time.sleep(1.0)
    screenshot('03_after_alt_f')

    # J (no modifier, after menu opened)
    vk_press(VK_J)
    time.sleep(3.0)
    screenshot('04_after_j')

    log('\n[5] Searching for save dialog (win32 #32770 owned by SNAP)')
    found_dialogs = []

    def enum_cb(h, lparam):
        if not win32gui.IsWindowVisible(h):
            return True
        cls = win32gui.GetClassName(h)
        title = win32gui.GetWindowText(h)
        if cls == '#32770':
            owner = win32gui.GetWindow(h, win32con.GW_OWNER)
            found_dialogs.append((h, title, owner))
        return True

    win32gui.EnumWindows(enum_cb, None)
    for (h, title, owner) in found_dialogs:
        log(f'   #32770: hwnd=0x{h:X} title="{title}" owner=0x{owner:X} (our hwnd=0x{hwnd:X})')
    save_hwnd = None
    for (h, title, owner) in found_dialogs:
        if owner == hwnd or '保存' in title or '名前' in title:
            save_hwnd = h
            break
    log(f'   save_hwnd = {hex(save_hwnd) if save_hwnd else None}')

    if save_hwnd:
        # Connect via pywinauto
        try:
            app_dlg = Application(backend='uia').connect(handle=save_hwnd)
            save_dlg = app_dlg.window(handle=save_hwnd)
            log(f'   Connected: "{save_dlg.window_text()}"')
            # Inspect children
            log('   Dialog controls:')
            for ch in save_dlg.descendants():
                try:
                    ct = ch.element_info.control_type
                    cn = ch.window_text()
                    cc = ch.class_name()
                    if ct in ('Edit', 'Button', 'ComboBox') or cn:
                        log(f'     [{ct}] "{cn}" class={cc}')
                except Exception:
                    pass

            log('\n[6] Setting filename via clipboard paste and clicking 保存')
            try:
                import win32clipboard

                # Put full path on clipboard as Unicode (CF_UNICODETEXT=13)
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(13, str(OUT_S8I))
                win32clipboard.CloseClipboard()
                log(f'   Clipboard set: {OUT_S8I}')

                # Find and focus the filename Edit
                edit = save_dlg.child_window(title='ファイル名:', control_type='Edit')
                edit_hwnd = edit.handle
                log(f'   Edit hwnd=0x{edit_hwnd:X}')
                edit.set_focus()
                time.sleep(0.3)

                # Ctrl+A to select any existing text, Ctrl+V to paste
                VK_A = 0x41
                VK_V = 0x56
                VK_CONTROL = 0x11
                vk_press(VK_A, modifiers=[VK_CONTROL])
                time.sleep(0.15)
                vk_press(VK_V, modifiers=[VK_CONTROL])
                time.sleep(0.5)
                screenshot('05a_path_set')

                # Click the 保存(S) button
                save_btn = save_dlg.child_window(title='保存(S)', control_type='Button')
                save_btn.click_input()
                log('   Clicked 保存(S)')
            except Exception as e:
                log(f'   filename/click err: {e}')
                log(traceback.format_exc())

            time.sleep(3)
            screenshot('05b_after_save')
        except Exception as e:
            log(f'   dialog handling err: {e}')
            log(traceback.format_exc())
    else:
        log('   !! No save dialog found')

    log('\n[7] Overwrite confirmation')
    time.sleep(2)
    found2 = []
    def enum_ow(h, lparam):
        if not win32gui.IsWindowVisible(h):
            return True
        cls = win32gui.GetClassName(h)
        if cls == '#32770':
            owner = win32gui.GetWindow(h, win32con.GW_OWNER)
            title = win32gui.GetWindowText(h)
            if owner == hwnd and ('確認' in title or 'SNAP' in title or title == ''):
                found2.append(h)
        return True
    win32gui.EnumWindows(enum_ow, None)
    overwrite_hwnd = found2[0] if found2 else None
    if overwrite_hwnd:
        log(f'   Overwrite dialog HWND=0x{overwrite_hwnd:X}')
        send_keys('y')  # Alt+Y → はい(Y)
    screenshot('06_final')

    log('\n[8] Waiting for output file')
    deadline = time.time() + 45
    while time.time() < deadline:
        if OUT_S8I.exists() and OUT_S8I.stat().st_size > 0:
            log(f'   SUCCESS: {OUT_S8I.stat().st_size:,} bytes')
            break
        time.sleep(0.5)
    else:
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
