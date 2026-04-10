"""
controller/binary/result_loader.py
==================================

SNAP 解析結果フォルダを一括ロードする便利ラッパー。

解析フォルダには複数の .hst / .xbn / .stp ファイルが存在し、
それぞれが対応関係（例: Floor.hst と Floor.stp と Floor.xbn）を持ちます。
本クラスはそれらをまとめて扱い、UI から「Floor の時刻歴」「Story の最大値」
などにアクセスしやすくします。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .hst_reader import HstReader
from .xbn_reader import XbnReader
from .stp_reader import StpReader
from .period_xbn_reader import PeriodXbnReader


# 解析フォルダに存在しうる主要カテゴリ
_CATEGORIES: List[str] = [
    "Floor", "Story", "Node", "Column", "Beam",
    "Spring", "Rigid", "Damper", "Truss", "Energy",
    "MDFloor", "MDNode",  # モード応答値（Modal Displacement）
]


@dataclass
class BinaryCategory:
    """1 カテゴリ (例: Floor) の hst/xbn/stp バンドル。"""
    name: str
    hst: Optional[HstReader] = None
    xbn: Optional[XbnReader] = None
    stp: Optional[StpReader] = None

    @property
    def available(self) -> bool:
        return self.hst is not None or self.xbn is not None

    @property
    def num_records(self) -> int:
        if self.hst and self.hst.header:
            return self.hst.header.num_records
        if self.xbn:
            return self.xbn.num_records
        if self.stp:
            return self.stp.num_records
        return 0

    def record_name(self, index: int) -> str:
        if self.stp and 0 <= index < len(self.stp.names):
            return self.stp.names[index]
        return f"{self.name}[{index}]"


class SnapResultLoader:
    """SNAP 解析フォルダをスキャンして全カテゴリをまとめてロードします。

    Parameters
    ----------
    result_dir : str | Path
        解析結果フォルダ（.hst/.xbn/.stp/Period.xbn などが含まれる）
    dt : float
        .hst 時刻歴の時刻刻み [秒]。SNAP 側で指定された値を渡してください。
    """

    def __init__(self, result_dir: str | Path, dt: float = 0.005) -> None:
        self.result_dir = Path(result_dir)
        self.dt = dt
        self.categories: Dict[str, BinaryCategory] = {}
        self.period: Optional[PeriodXbnReader] = None
        self.errors: List[str] = []

        if self.result_dir.exists():
            self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        for cat in _CATEGORIES:
            bc = BinaryCategory(name=cat)
            hst_path = self.result_dir / f"{cat}.hst"
            xbn_path = self.result_dir / f"{cat}.xbn"
            stp_path = self.result_dir / f"{cat}.stp"
            # SNAP は Trus.xbn / Truss.stp のように一部カテゴリで
            # 4 文字名を使う場合があるので別名もチェック
            if cat == "Truss" and not xbn_path.exists():
                alt = self.result_dir / "Trus.xbn"
                if alt.exists():
                    xbn_path = alt

            try:
                if hst_path.exists():
                    bc.hst = HstReader(hst_path, dt=self.dt, lazy=True)
            except Exception as e:
                self.errors.append(f"{hst_path.name}: {e}")
            try:
                if xbn_path.exists():
                    bc.xbn = XbnReader(xbn_path)
            except Exception as e:
                self.errors.append(f"{xbn_path.name}: {e}")
            try:
                if stp_path.exists():
                    bc.stp = StpReader(stp_path)
            except Exception as e:
                self.errors.append(f"{stp_path.name}: {e}")

            if bc.available or bc.stp is not None:
                self.categories[cat] = bc

        # Period.xbn
        period_path = self.result_dir / "Period.xbn"
        if period_path.exists():
            try:
                self.period = PeriodXbnReader(period_path)
            except Exception as e:
                self.errors.append(f"Period.xbn: {e}")

    # ------------------------------------------------------------------
    @property
    def available_categories(self) -> List[str]:
        return list(self.categories.keys())

    def get(self, category: str) -> Optional[BinaryCategory]:
        return self.categories.get(category)

    def summary(self) -> str:
        lines = [f"SnapResultLoader: {self.result_dir}"]
        lines.append(f"  dt = {self.dt}")
        for name, bc in self.categories.items():
            parts = []
            if bc.hst:
                h = bc.hst.header
                if h:
                    parts.append(f"hst(steps={h.num_steps}, rec={h.num_records}, fpr={h.fields_per_record})")
            if bc.xbn:
                parts.append(f"xbn(rec={bc.xbn.num_records}, vpr={bc.xbn.values_per_record})")
            if bc.stp:
                parts.append(f"stp(names={len(bc.stp.names)})")
            lines.append(f"  {name:8s}: {'  '.join(parts) if parts else '(none)'}")
        if self.period:
            lines.append(f"  Period  : modes={self.period.num_modes}")
        if self.errors:
            lines.append("  errors:")
            for e in self.errors:
                lines.append(f"    - {e}")
        return "\n".join(lines)
