"""Inspect SNAP.exe UI (v3) — scan UIA deep tree for MenuItems.

SNAP uses custom-drawn menus (main.menu() returns None on win32).
UIA should still expose them as accessibility elements. This script
walks the full UIA tree looking for menu-like items.
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
LOG_FILE = TEST_DIR / 'snap_menu_inspect.log'


def kill_snap():
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'Snap.exe'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


LOG_FILE.write_text('', encoding='utf-8')


def log(msg: str):
    print(msg)
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(msg + '\n')


kill_snap()
time.sleep(1)

try:
    from pywinauto import Application, Desktop

    log('=== Starting SNAP.exe (UIA backend) ===')
    app = Application(backend='uia').start(
        f'"{SNAP_EXE}" "{NAP_FILE}"',
        wait_for_idle=False,
    )

    time.sleep(8)

    # Find main window via Desktop
    main = None
    for w in Desktop(backend='uia').windows():
        try:
            if w.class_name() == 'SNAPV8_MainFrame':
                main = w
                break
        except Exception:
            pass

    if not main:
        log('!!! Main window not found')
    else:
        log(f'Main window: "{main.window_text()}"')

        # Walk the full tree looking for items with text containing key keywords
        keywords = ['ファイル', 'テキスト', '保存', 'File', 'Save']
        log(f'\n=== Scanning for elements matching {keywords} ===')

        def walk(element, path='', depth=0, max_depth=6):
            if depth > max_depth:
                return
            try:
                info = element.element_info
                name = info.name or ''
                ctrl_type = info.control_type or ''
                class_name = info.class_name or ''
                if any(kw in name for kw in keywords):
                    log(f'  {path} [{ctrl_type}] name="{name}" class="{class_name}"')
                children = element.children()
                for i, child in enumerate(children):
                    walk(child, f'{path}/{i}', depth + 1, max_depth)
            except Exception:
                pass

        walk(main)

        log('\n=== MenuItem elements across desktop (global scan) ===')
        from pywinauto.findwindows import find_elements
        try:
            menu_items = find_elements(
                backend='uia',
                control_type='MenuItem',
                top_level_only=False,
            )
            log(f'MenuItem count: {len(menu_items)}')
            for m in menu_items[:80]:
                try:
                    log(f'  "{m.name}" parent={m.parent.name if m.parent else "?"}')
                except Exception:
                    pass
        except Exception as e:
            log(f'find_elements failed: {e}')

        # Try also: find "Menu" control_type
        log('\n=== Menu elements (global) ===')
        try:
            menus = find_elements(
                backend='uia',
                control_type='Menu',
                top_level_only=False,
            )
            log(f'Menu count: {len(menus)}')
            for m in menus[:20]:
                try:
                    log(f'  Menu name="{m.name}" class={m.class_name}')
                except Exception:
                    pass
        except Exception as e:
            log(f'Menu find failed: {e}')

        # Try sending Alt+F to open file menu, then inspect
        log('\n=== Sending Alt+F to activate File menu ===')
        try:
            main.set_focus()
            time.sleep(0.5)
            from pywinauto.keyboard import send_keys
            send_keys('%F')  # Alt+F
            time.sleep(1)

            # Now look for popup menus
            log('\n  After Alt+F: top-level windows =')
            for w in Desktop(backend='uia').windows():
                try:
                    t = w.window_text()
                    c = w.class_name()
                    if t or c != '':
                        log(f'    "{t}" class={c}')
                except Exception:
                    pass

            # Re-scan MenuItems
            log('\n  MenuItems after Alt+F:')
            menu_items = find_elements(
                backend='uia',
                control_type='MenuItem',
                top_level_only=False,
            )
            for m in menu_items[:80]:
                try:
                    log(f'    "{m.name}"')
                except Exception:
                    pass

            # Close the menu
            send_keys('{ESC}')
        except Exception as e:
            log(f'  Alt+F send failed: {e}')
            log(traceback.format_exc())

except Exception as e:
    log(f'\n!!! Exception: {e}')
    log(traceback.format_exc())

finally:
    log('\n=== Cleanup ===')
    kill_snap()
    log('Done.')
