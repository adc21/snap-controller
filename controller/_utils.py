import re
from typing import Pattern, AnyStr
from .types import SnapDirPrefixType

def get_dir_regex(prefix: SnapDirPrefixType) -> Pattern[AnyStr]:
    return re.compile(f'{prefix}([0-9]+)')

