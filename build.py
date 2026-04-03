import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 50)
print("  snap-controller Build")
print("=" * 50)
print()

print("[1/3] Installing packages...")
r = subprocess.run(
    [sys.executable, "-m", "pip", "install", "-r", "requirements_build.txt", "--quiet"],
    capture_output=False
)
if r.returncode != 0:
    print("ERROR: pip install failed")
    input("Press Enter to exit...")
    sys.exit(1)
print("Done.")
print()

print("[2/3] Running PyInstaller (5-10 min on first run)...")
r = subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "snap_controller.spec",
    "--noconfirm",
    "--workpath", "build_win",
    "--distpath", "dist_win",
])
print()
print(f"PyInstaller exit code: {r.returncode}")
print()

print("[3/3] Checking output...")
exe_path = os.path.join("dist_win", "snap-controller.exe")
if os.path.exists(exe_path):
    size_mb = os.path.getsize(exe_path) / 1024 / 1024
    print(f"SUCCESS! {exe_path} ({size_mb:.1f} MB)")
else:
    print("FAILED: dist_win\\snap-controller.exe not found")
    print()
    print("Check the error messages above.")

print()
input("Press Enter to exit...")
