import os

class File:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.basename = os.path.basename(self.file_path)
        self.parent_dir_path = self.file_path.split(self.basename)[0]
        self.base, self.ext = os.path.splitext(self.basename)
