import sys
sys.path.insert(0, r"C:/Users/keita/App/ADC/snap-controller")

from app.models.s8i_parser import parse_s8i

# Use a real s8i from SNAP's work folder
import os
candidates = [
    r"C:/Users/keita/App/ADC/snap-controller/example_model",
    r"C:/Users/keita/App/ADC/snap-controller/test",
    r"D:/Kakemoto/kozosystem/SNAPV8/work",
]
for base in candidates:
    if not os.path.isdir(base):
        continue
    for root, _, files in os.walk(base):
        for f in files:
            if f.endswith(".s8i"):
                path = os.path.join(root, f)
                print("Using:", path)
                model = parse_s8i(path)
                print(f"Total DYC cases: {len(model.dyc_cases)}")
                for c in model.dyc_cases:
                    wave = c.values[19] if len(c.values) > 19 else "?"
                    print(f"  D{c.case_no}: {c.name} run={c.run_flag} wave={wave!r}")
                # Try apply_impulse_mode
                if model.dyc_cases:
                    target = model.dyc_cases[0]
                    result = model.apply_impulse_mode(
                        target_case_no=target.case_no,
                        impulse_wave_name="IMPULSE_TEST",
                    )
                    print()
                    print("After apply_impulse_mode on case", target.case_no)
                    for c in model.dyc_cases:
                        wave = c.values[19] if len(c.values) > 19 else "?"
                        scale = c.values[20] if len(c.values) > 20 else "?"
                        print(f"  D{c.case_no}: run_flag={c.run_flag} wave={wave!r} scale={scale}")
                    assert result is not None
                    assert result.run_flag == 1
                    assert result.values[19] == "IMPULSE_TEST"
                    for c in model.dyc_cases:
                        if c.case_no != target.case_no:
                            assert c.run_flag == 0, f"Other case {c.case_no} should have run_flag=0, got {c.run_flag}"
                    print("OK")
                sys.exit(0)
print("No .s8i files found in work directory, cannot test")
