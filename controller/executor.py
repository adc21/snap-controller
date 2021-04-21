import os
import time
import subprocess
from .types import TypePrefix

class Executor():
    def __init__(self):
        pass

def snap_exec(snap_file_path: str, snap_exe_path = "C:\Program Files\SNAP Ver.8\Snap.exe", bat_path = "run.bat", type_prefix: TypePrefix = "D"):
    with open(bat_path, "w", encoding="shift_jis") as bat_file:
        bat_file_content = f'"{snap_exe_path}" /B{type_prefix} {snap_file_path}'
        bat_file.writelines(bat_file_content)

    subprocess.call([bat_path])
