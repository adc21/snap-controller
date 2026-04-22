"""Search for more hints about S8I export from NAP in Info.txt and related files."""
import sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

p = Path(r'C:\Program Files\SNAP Ver.8\Info.txt')
text = p.read_bytes().decode('shift_jis', errors='replace')
lines = text.splitlines()

# Look for "テキストファイル出力", "ファイルに保存", "出力", "保存" near specific contexts
print('=== テキストﾌｧｲﾙ出力 / テキスト出力 mentions ===')
for i, l in enumerate(lines):
    if 'ﾃｷｽﾄﾌｧｲﾙ' in l or 'テキストファイル' in l or 'ﾃｷｽﾄﾃﾞｰﾀ' in l:
        print(f'  L{i+1}: {l.strip()[:120]}')

# Check for /B, /E, /R style flags in Info.txt
print('\n=== Command-line-like patterns ===')
import re
for pat in [r'/[A-Z][A-Za-z0-9_]{0,15}', r'-[A-Za-z]{2,20}']:
    seen = set()
    for i, l in enumerate(lines):
        for m in re.finditer(pat, l):
            tok = m.group()
            if tok not in seen and not any(c in tok for c in '/.,:;()'):
                seen.add(tok)
                if len(seen) < 30:
                    print(f'  {tok!r} at L{i+1}')
