import sys
sys.path.insert(0, r"C:/Users/keita/App/ADC/snap-controller")

from app.services.impulse_wave_writer import write_impulse_wave, ImpulseWaveSpec
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "test_impulse.wv"
    spec = ImpulseWaveSpec(amax=1000.0, dt=0.01, num_points=8192, impulse_index=9, filename="TEST_IMP")
    p = write_impulse_wave(out, spec)
    with open(p, "rb") as f:
        raw = f.read()
    # verify CRLF
    print("CRLF count:", raw.count(b"\r\n"))
    text = raw.decode("ascii")
    lines = text.split("\r\n")
    # drop trailing empty
    lines = [l for l in lines if l != ""]
    print("Total non-empty lines:", len(lines))
    print("Header:")
    for line in lines[:10]:
        print(" ", line)
    nonzero = [(i, v) for i, v in enumerate(lines[10:]) if v != "0"]
    print("Nonzero (idx, val):", nonzero)
    print("Last 3:", lines[-3:])
    data_count = len(lines) - 10
    print("Data points:", data_count)
    assert data_count == 8192, f"Expected 8192 data points, got {data_count}"
    assert nonzero == [(9, "1000.000")], f"Expected impulse at idx 9, got {nonzero}"
    print("OK")
