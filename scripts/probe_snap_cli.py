"""Probe SNAP.exe for undocumented CLI flags (NAP->s8i conversion).

Safety:
- Uses a copy in tmp/nap_cli_test, never touches the original.
- Short timeout per flag (10s). SNAP is a GUI app, so we expect window to
  appear; we kill it after timeout and inspect any side-effects.
- Snapshot file list before/after each invocation to detect generated files.
"""
import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SNAP_EXE = Path(r'C:\Program Files\SNAP Ver.8\Snap.exe')
TEST_DIR = Path(r'C:\Users\keita\App\ADC\snap-controller\tmp\nap_cli_test')
NAP_FILE = TEST_DIR / 'example_3D.NAP'
S8I_FILE = TEST_DIR / 'example_3D.s8i'

# Baseline s8i content hash to detect whether NAP→s8i overwrote it
import hashlib


def snapshot_dir(d: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime) for p in d.iterdir() if p.is_file()}


def run_flag(args: list, timeout: float = 8.0) -> dict:
    """Run SNAP.exe with given args, kill after timeout. Return info dict."""
    before = snapshot_dir(TEST_DIR)
    s8i_hash_before = hashlib.md5(S8I_FILE.read_bytes()).hexdigest() if S8I_FILE.exists() else None

    cmd = [str(SNAP_EXE)] + args
    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='shift_jis',
            errors='replace',
            cwd=str(TEST_DIR),
        )
    except Exception as e:
        return {'cmd': cmd, 'error': f'spawn failed: {e}'}

    stdout_data = ''
    exited_cleanly = False
    try:
        stdout_data, _ = proc.communicate(timeout=timeout)
        exited_cleanly = True
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        # Kill the process tree (SNAP may spawn children)
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
        except Exception:
            pass
        try:
            stdout_data, _ = proc.communicate(timeout=3)
        except Exception:
            pass
        rc = None

    elapsed = time.time() - t0

    after = snapshot_dir(TEST_DIR)
    new_files = {k: v for k, v in after.items() if k not in before}
    modified = {k: (before.get(k), v) for k, v in after.items() if k in before and before[k] != v}
    deleted = {k: v for k, v in before.items() if k not in after}

    s8i_hash_after = hashlib.md5(S8I_FILE.read_bytes()).hexdigest() if S8I_FILE.exists() else None
    s8i_changed = (s8i_hash_before != s8i_hash_after)

    return {
        'cmd': cmd,
        'rc': rc,
        'elapsed': round(elapsed, 2),
        'exited_cleanly': exited_cleanly,
        'stdout_preview': stdout_data[:800] if stdout_data else '',
        'new_files': new_files,
        'modified': list(modified.keys()),
        'deleted': list(deleted.keys()),
        's8i_changed': s8i_changed,
    }


# Reset s8i copy before we start so probe results are clean.
# (s8i is our "canary" — if a flag overwrites it, we know conversion happened.)
# Make s8i slightly different from the original so we can detect rewrite.
orig_s8i = Path(r'C:\Users\keita\App\ADC\snap-controller\example_model\example_3D\example_3D.s8i')
shutil.copy2(orig_s8i, S8I_FILE)
# Append a canary comment so we can detect overwrite
with S8I_FILE.open('ab') as f:
    f.write(b'\r\nREM / CANARY_PROBE_MARKER\r\n')
canary_size = S8I_FILE.stat().st_size

print(f'SNAP.exe: {SNAP_EXE}')
print(f'Test dir: {TEST_DIR}')
print(f'NAP: {NAP_FILE.name} ({NAP_FILE.stat().st_size:,} bytes)')
print(f's8i (with canary): {S8I_FILE.name} ({canary_size:,} bytes)')
print()

# Remove any stale s8i variant with different name
for f in TEST_DIR.glob('example_3D_*.s8i'):
    f.unlink()
for f in TEST_DIR.glob('*.s8i'):
    if f.name != 'example_3D.s8i':
        f.unlink()

probes = [
    # Universal help flags
    ['/?'],
    ['-?'],
    ['/H'],
    ['/HELP'],
    ['--help'],
    ['-h'],
    # Try conversion-related single letters with NAP input
    ['/T', str(NAP_FILE)],   # T for text?
    ['/E', str(NAP_FILE)],   # E for export?
    ['/X', str(NAP_FILE)],   # X for export?
    ['/O', str(NAP_FILE)],   # O for output?
    ['/S', str(NAP_FILE)],   # S for save?
    ['/C', str(NAP_FILE)],   # C for convert?
    # Try combinations mimicking /B
    ['/BT', str(NAP_FILE)],  # Batch-Text?
    ['/BE', str(NAP_FILE)],
    ['/BX', str(NAP_FILE)],
    ['/BS', str(NAP_FILE)],
    # Try extension-based
    ['/S8I', str(NAP_FILE)],
    ['/TEXT', str(NAP_FILE)],
    ['/EXPORT', str(NAP_FILE)],
    ['/CONVERT', str(NAP_FILE)],
    ['/OUTPUT', str(NAP_FILE)],
    ['/SAVE', str(NAP_FILE)],
    # Baseline: just open the NAP (expected: GUI window)
    [str(NAP_FILE)],
]

for args in probes:
    print(f'--- Probe: {args} ---')
    result = run_flag(args, timeout=8.0)
    print(f'  rc={result["rc"]}, elapsed={result["elapsed"]}s, exited_cleanly={result["exited_cleanly"]}')
    if result.get('stdout_preview'):
        print(f'  stdout: {result["stdout_preview"][:300]}')
    if result['new_files']:
        print(f'  NEW FILES: {list(result["new_files"].keys())}')
    if result['modified']:
        print(f'  MODIFIED: {result["modified"]}')
    if result['deleted']:
        print(f'  DELETED: {result["deleted"]}')
    if result['s8i_changed']:
        print('  *** s8i content changed (canary removed)! ***')
        # Regenerate canary for next probe
        shutil.copy2(orig_s8i, S8I_FILE)
        with S8I_FILE.open('ab') as f:
            f.write(b'\r\nREM / CANARY_PROBE_MARKER\r\n')
    print()
    # Small delay between runs to let SNAP fully die
    time.sleep(1)

print('=== Probe complete ===')
