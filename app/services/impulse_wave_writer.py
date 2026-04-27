"""
app/services/impulse_wave_writer.py
SNAP .wv 形式でインパルス波を書き出すサービス。

伝達関数最適化で用いるインパルス入力波形（既定 16384点 = 0.02s × 327.68s、
指定サンプル位置にのみ指定加速度、それ以外は0）を生成します。

SNAP .wv のテキストフォーマット（サンプル `20000001.wv` 参照）:

    VERSION="2021.3.12.0"
    FILENAME="<任意の表示名>"
    HPTYPE="0"
    DIRECTION="0"
    DT="<float>"
    UNITID="0"
    AMAX="<float>"
    VMAX="<float>"
    TIME="<float>"
    DATA
    <value1>
    <value2>
    ...

改行コードは CRLF、エンコーディングは ASCII。UNITID=0 は gal (cm/s^2)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


DEFAULT_NUM_POINTS = 16384  # 8192 × 2; 0.02 s × 16384 = 327.68 s
DEFAULT_IMPULSE_INDEX = 9  # 0-indexed, the 10th sample
DEFAULT_DT = 0.02
DEFAULT_VERSION = "2021.3.12.0"


@dataclass
class ImpulseWaveSpec:
    """インパルス波形の仕様。"""

    amax: float
    dt: float = DEFAULT_DT
    num_points: int = DEFAULT_NUM_POINTS
    impulse_index: int = DEFAULT_IMPULSE_INDEX
    filename: str = "IMPULSE"
    direction: int = 0
    unit_id: int = 0

    def validate(self) -> None:
        if self.num_points <= 0:
            raise ValueError(f"num_points must be positive, got {self.num_points}")
        if not (0 <= self.impulse_index < self.num_points):
            raise ValueError(
                f"impulse_index {self.impulse_index} out of range [0, {self.num_points})"
            )
        if self.dt <= 0:
            raise ValueError(f"dt must be positive, got {self.dt}")
        if self.amax == 0:
            raise ValueError("amax must be non-zero for impulse wave")


def write_impulse_wave(path: str | Path, spec: ImpulseWaveSpec) -> Path:
    """
    SNAP .wv 形式でインパルス波を書き出します。

    Parameters
    ----------
    path : str or Path
        出力先ファイルパス（.wv 拡張子推奨）。
    spec : ImpulseWaveSpec
        インパルス波の仕様。

    Returns
    -------
    Path
        書き出したファイルの Path。
    """
    spec.validate()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    total_time = spec.dt * spec.num_points
    lines: list[str] = [
        f'VERSION="{DEFAULT_VERSION}"',
        f'FILENAME="{spec.filename}"',
        'HPTYPE="0"',
        f'DIRECTION="{spec.direction}"',
        f'DT="{spec.dt}"',
        f'UNITID="{spec.unit_id}"',
        f'AMAX="{spec.amax:.3f}"',
        'VMAX="0.00"',
        f'TIME="{total_time:.6f}"',
        'DATA',
    ]
    # インパルス位置のみ amax、それ以外は 0
    for i in range(spec.num_points):
        if i == spec.impulse_index:
            lines.append(f"{spec.amax:.3f}")
        else:
            lines.append("0")

    # SNAP の .wv ファイルは CRLF 改行・ASCII
    text = "\r\n".join(lines) + "\r\n"
    out.write_bytes(text.encode("ascii"))
    logger.info(
        "Impulse wave written: %s (N=%d, dt=%g, amax=%g, idx=%d)",
        out, spec.num_points, spec.dt, spec.amax, spec.impulse_index,
    )
    return out


def make_impulse_filename(case_id: str, amax: float) -> str:
    """
    インパルス波用のユニークなファイル名（拡張子なし）を生成します。

    Parameters
    ----------
    case_id : str
        ベースとなるケース ID。
    amax : float
        最大加速度。

    Returns
    -------
    str
        例: "IMPULSE_<case_id_head>_a1000"
    """
    head = case_id[:8] if case_id else "X"
    return f"IMPULSE_{head}_a{int(abs(amax))}"
