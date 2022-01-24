import os
import subprocess
from typing import TypedDict, Union, List
from .file import File
from .logger import logger

class UpdateConfigDict(TypedDict):
    category: str   # Exp. "REM / 粘性/ｵｲﾙﾀﾞﾝﾊﾟｰ"
    line: int   # Start from 1
    row: int    # Start from 1
    value: Union[str, float, int]

UpdateConfig = List[UpdateConfigDict]

class Updater(File):
    def __init__(self, file_path: str, config: UpdateConfig, debug: bool = True):
        super().__init__(file_path)
        self.config = config
        self.debug = debug

    def get_new_file_path(self):
        i = 0
        while os.path.exists(f"{self.base}{i}{self.ext}"):
            i += 1

        return f"{self.parent_dir_path}{self.base}{i}{self.ext}"

    def update(self) -> str:
        with open(self.file_path, "r", encoding="shift_jis") as snap_file:
            data = snap_file.readlines()

            for i, c in enumerate(self.config):
                line_number = 0
                target_line_number = -1

                for line in data:
                    line_number += 1

                    if c["category"] in line:
                        target_line_number = line_number + c["line"]

                    if target_line_number == line_number:

                        if line:
                            split_text = line.split("/")
                            text_prefix = split_text[0].strip()
                            text = split_text[1].strip()
                            original_text_list = text.split(",")

                            if self.debug: logger.info(f"update target line list:  {original_text_list}")

                            rewrite_text_list = [c["value"] if i == c["row"] - 1 else x for i, x in enumerate(original_text_list)]

                            if self.debug: logger.info(f"updated target line list: {rewrite_text_list}")

                            rewrite_text = ",".join(str(x) for x in rewrite_text_list)
                            rewrite_line = f"{text_prefix} / {rewrite_text}\n"
                            data[target_line_number - 1] = rewrite_line
                            break

                        else:
                            raise ValueError(f"no data found for config index {i}")

        new_file = self.get_new_file_path()
        with open(new_file, "w", encoding="shift_jis") as out_file:
            out_file.writelines(data)

        return new_file
