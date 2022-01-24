from typing import TypedDict, Union, List, Optional, Literal
from .file import File
from .types import TypePrefix
from .logger import logger


class ResultConfigDict(TypedDict):
    case_number: int    # Start from 1
    filename: str
    line: int   # Start from 1
    row: int    # Start from 1
    delimiter: Optional[str]
    type_prefix: Optional[TypePrefix]   # D: 応答解析, L: 増分解析 Default to D


ResultConfig = List[ResultConfigDict]


class CreateResultConfig():
    def __init__(self, case: int) -> None:
        self.case = case

    def getAllStoryDVAR(self, resultType: Literal["Dx", "Dy", "Vx", "Vy", "Ax", "Ay", "Rx", "Ry"], story: int) -> ResultConfig:
        result_config: ResultConfig = []

        if resultType == "Dx":
            row = 3
        elif resultType == "Dy":
            row = 4
        elif resultType == "Vx":
            row = 5
        elif resultType == "Vy":
            row = 6
        elif resultType == "Ax":
            row = 7
        elif resultType == "Ay":
            row = 8
        elif resultType == "Rx":
            row = 9
        elif resultType == "Ry":
            row = 10
        else:
            raise ValueError("タイプが正しくありません。")

        for i in range(story):
            result_config.append({
                "case_number": self.case,
                "filename": "Floor0.txt",
                "line": i + 5,
                "row": row,
            })

        return result_config


class Result(File):
    def __init__(self, file_path: str, work_dir: str, config: ResultConfig, debug: bool = True):
        super().__init__(file_path)
        self.work_dir = work_dir
        self.config = config
        self.debug = debug

    def get(self) -> List[any]:
        result_list = []
        for i, c in enumerate(self.config):
            type_prefix = "D" if not "type_prefix" in c else c["type_prefix"]
            path = f"{self.work_dir}\{self.base}\{type_prefix}{c['case_number']}\{c['filename']}"

            with open(path, "r", encoding="shift_jis") as result_file:
                data = result_file.readlines()
                line_number = 0
                for line in data:
                    line_number += 1
                    if c["line"] == line_number:
                        if line:
                            text_list = line.split(None if not "delimiter" in c else c["delimiter"])

                            if self.debug:
                                logger.info(f"result target line list:  {text_list}")

                            value = text_list[c["row"] - 1]
                            result_list.append(value)
                            break

                        else:
                            raise ValueError(f"no data found config number {i}")

        return result_list
