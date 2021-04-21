import os
import re
from os.path import isdir, join
from typing import TypedDict, Union, List
from .types import SnapDirPrefixType
from .utils import get_dir_regex

class SnapPath:
    def __init__(self, work_dir: str, snap_filename: str):
        self.work_dir = work_dir
        self.snap_filename = snap_filename
        self.result_dir = f"{self.work_dir}\{os.path.splitext(self.snap_filename)[0]}"

    def get_last_case_result_dir_path(self, prefix: SnapDirPrefixType = "D") -> Union[str, None]:
        dirs = [d for d in os.listdir(self.result_dir) if isdir(join(self.result_dir, d))]
        p = get_dir_regex(prefix)
        numbers = p.findall(", ".join(dirs))

        if len(numbers) == 0:
            return None

        return f"{self.result_dir}\{prefix}{str(max([int(x) for x in numbers]))}"

    def get_last_case_result_file_path(self, filename: str, dir_prefix: SnapDirPrefixType = "D") -> Union[str, None]:
        latest_dir = self.get_last_case_result_dir_path(dir_prefix)

        if not latest_dir:
            return None

        return f"{latest_dir}\{filename}"
