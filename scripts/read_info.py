"""Read Info.txt with Shift-JIS encoding and search for CLI / NAP / S8I hints."""
import sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

p = Path(r'C:\Program Files\SNAP Ver.8\Info.txt')
text = p.read_bytes().decode('shift_jis', errors='replace')
lines = text.splitlines()

# Focus on s8i-related lines with context
print('=== s8i mentions with context ===')
for i, l in enumerate(lines):
    if 's8i' in l:
        start = max(0, i-1)
        end = min(len(lines), i+3)
        print(f'\n--- Line {i+1} ---')
        for j in range(start, end):
            print(f'  L{j+1}: {lines[j].strip()}')
