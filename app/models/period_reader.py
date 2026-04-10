"""
app/models/period_reader.py
SNAP Period.xbn ファイルリーダー（固有値解析結果）

本モジュールは互換 API を維持しつつ、実装を
``controller.binary.period_xbn_reader.PeriodXbnReader`` に委譲します。
新しい実装では任意モード数・多方向の参加係数/参加質量比に対応しています。

旧 API (periods / frequencies / participation_mass) は引き続き利用可能です。
追加情報（方向別 PM, 支配方向など）は ``PeriodReader.modes`` 経由で
取得できます。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from controller.binary.period_xbn_reader import PeriodXbnReader, ModeInfo
    _HAS_NEW_READER = True
except Exception:
    _HAS_NEW_READER = False
    PeriodXbnReader = None  # type: ignore
    ModeInfo = None  # type: ignore


class PeriodReader:
    """
    SNAP Period.xbn ファイルパーサー。

    Usage::

        reader = PeriodReader("path/to/Period.xbn")
        print(reader.periods)           # {1: 3.973835, 2: 1.581139, ...}
        print(reader.frequencies)       # {1: 0.252, 2: 0.633, ...}
        print(reader.participation_mass) # {1: 0.45, 2: 0.33, ...}

    Attributes
    ----------
    periods : Dict[int, float]
        モード番号 -> 固有周期 [秒]
    frequencies : Dict[int, float]
        モード番号 -> 固有周波数 [Hz]
    participation_mass : Dict[int, float]
        モード番号 -> 参加質量比 [%]
    """

    def __init__(self, period_file: str) -> None:
        self.period_file = Path(period_file)
        self.periods: Dict[int, float] = {}
        self.frequencies: Dict[int, float] = {}
        self.participation_mass: Dict[int, float] = {}
        self._raw_floats: List[float] = []
        self.modes: List["ModeInfo"] = []

        if self.period_file.exists():
            if _HAS_NEW_READER:
                # 新リーダーに委譲
                new = PeriodXbnReader(self.period_file)  # type: ignore
                self.modes = list(new.modes)
                for m in new.modes:
                    self.periods[m.mode_no] = m.period
                    self.frequencies[m.mode_no] = m.frequency
                    pm_total = sum(abs(v) for v in m.pm.values())
                    self.participation_mass[m.mode_no] = pm_total
            else:
                self._parse()

    def _parse(self) -> None:
        """
        Period.xbn ファイルをパースします。

        Period.xbn は Float32 配列です (80 bytes = 20 floats)
        """
        with open(self.period_file, "rb") as f:
            data = f.read()

        if len(data) < 20:
            return  # ファイルが小さすぎる

        # Float32 配列として解析 (全体をFloatとして読む)
        num_floats = len(data) // 4

        try:
            self._raw_floats = list(struct.unpack(f"<{num_floats}f", data))
        except struct.error:
            return

        # バイナリデータから固有周期と参加質量比を抽出
        self._extract_modal_properties()

    def _extract_modal_properties(self) -> None:
        """
        Float32 配列から固有周期と参加質量比を抽出します。

        Period.xbn のバイナリ構造（観測結果）:
        - Float[0-5]: 0.0 (パディング)
        - Float[6]: 有効なモード数 (1.0 など)
        - Float[7-11]: 0.0 (パディング)
        - Float[12-13]: 固有周期 [秒] (T1, T2, ...)
        - Float[14]: 参加質量比の合計 [%]
        - Float[15+]: 0.0 (パディング)
        """
        if len(self._raw_floats) < 15:
            return

        # Float[6] がモード数を示す (通常は 1.0, 2.0 など)
        num_modes = int(self._raw_floats[6]) if self._raw_floats[6] > 0 else 0

        # Float[12], Float[13], ... が固有周期（最大 2 モード分を抽出）
        period_indices = [12, 13]
        mode_num = 1

        for idx in period_indices:
            if idx < len(self._raw_floats) and self._raw_floats[idx] > 0.001:
                self.periods[mode_num] = self._raw_floats[idx]
                self.frequencies[mode_num] = 1.0 / self._raw_floats[idx]
                mode_num += 1

        # Float[14] が参加質量比の合計 [%]
        if len(self._raw_floats) > 14 and self._raw_floats[14] > 0:
            total_pm = self._raw_floats[14]
            # モード数に応じて按分 (簡易実装: 全体値として保存)
            self.participation_mass[1] = total_pm

    def get_all(self) -> Dict[str, Dict[int, float]]:
        """すべてのモード特性を辞書にまとめて返します。"""
        return {
            "periods": self.periods,
            "frequencies": self.frequencies,
            "participation_mass": self.participation_mass,
        }

    def to_string(self) -> str:
        """モード特性を文字列で返します。"""
        lines = ["固有値解析結果:"]

        if not self.periods:
            lines.append("  (データなし)")
            return "\n".join(lines)

        for mode_num in sorted(self.periods.keys()):
            period = self.periods[mode_num]
            freq = self.frequencies.get(mode_num, 0)
            pm = self.participation_mass.get(mode_num, 0)
            lines.append(
                f"  モード {mode_num}: T={period:.4f}s, f={freq:.4f}Hz, PM={pm:.2f}%"
            )

        return "\n".join(lines)
