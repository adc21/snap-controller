"""Analyze NAP vs s8i file differences (v2)."""
from pathlib import Path
import re

nap = Path(r'C:/Users/keita/App/ADC/snap-controller/example_model/example_3D/example_3D.NAP')
s8i = Path(r'C:/Users/keita/App/ADC/snap-controller/example_model/example_3D/example_3D.s8i')

nap_data = nap.read_bytes()
s8i_data = s8i.read_bytes()

# Search for any ASCII text markers that look like record headers
# Record headers look like "KEYWORD " or "KEYWORD\t" or "KEYWORD/"
# Or "KEYWORD /" style

# Find patterns with ASCII keywords
for marker in [b'REM', b'TTL', b'VER', b'ND', b'SNAP']:
    pos = nap_data.find(marker)
    if pos >= 0:
        ctx = nap_data[max(0,pos-8):pos+40]
        print(f'{marker!r} first at {pos:,}: {ctx!r}')
    else:
        print(f'{marker!r} not found')

# Try Shift-JIS decode with errors='replace' and find text regions
# How much of the NAP file is ASCII printable?
printable = sum(1 for b in nap_data if 32 <= b < 127 or b in (9, 10, 13))
print(f'\nNAP: {printable:,} / {len(nap_data):,} bytes are ASCII printable ({100*printable/len(nap_data):.1f}%)')

printable_s8i = sum(1 for b in s8i_data if 32 <= b < 127 or b in (9, 10, 13))
print(f's8i: {printable_s8i:,} / {len(s8i_data):,} bytes are ASCII printable ({100*printable_s8i/len(s8i_data):.1f}%)')

# Scan NAP for contiguous ASCII runs > 30 bytes (likely text sections)
print('\nASCII-like runs (>=50 printable bytes) in NAP (first 20):')
in_run = False
run_start = 0
runs = []
for i, b in enumerate(nap_data):
    if 32 <= b < 127 or b in (9, 10, 13):
        if not in_run:
            run_start = i
            in_run = True
    else:
        if in_run and i - run_start >= 50:
            runs.append((run_start, i - run_start))
        in_run = False

for i, (start, length) in enumerate(runs[:20]):
    sample = nap_data[start:start+80]
    print(f'  offset {start:,} len {length:,}: {sample!r}')

print(f'\nTotal ASCII runs >=50 bytes: {len(runs)}')

# Look at tail of NAP
print(f'\nNAP last 200 bytes:')
print(f'  hex: {nap_data[-200:].hex()[:200]}')
print(f'  repr: {nap_data[-200:]!r}')
