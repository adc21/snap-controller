"""
controller/binary/stp_reader.py
===============================

SNAP の ``.stp`` ファイル（構造定義）から各レコード名を抽出するリーダー。

ファイル形式（観測ベース、example_3D/D4 からリバース）
-----------------------------------------------------

ヘッダ (16 バイト / 4 × int32, little-endian)::

    int[0]  : magic/version 値（ファイルごとに異なる）
    int[1]  : 2 など（用途不明。2D/3D フラグか？）
    int[2]  : 内部カウント（レコードヘッダ長の目安）
    int[3]  : num_records（レコード数）

続いて ``num_records`` 個のレコードが並びます。各レコードは::

    - 8 バイト : レコード名 (shift_jis, 末尾 NUL/空白埋め)
    - 残り     : 座標や最大寸法などの浮動小数点データ（可変長）

レコード長はファイル種別で異なる (Floor.stp: 16 バイト,
Story.stp: 可変, ...) ため、本リーダーは「先頭 8 バイト × num_records を
順に読む」動作は行わず、ファイル全体からレコード名らしき 8 バイト列
（印字可能な ASCII で始まり、NUL で終わるもの）を抽出する方針を採ります。

これにより、Floor.stp / Story.stp / Rigid.stp / Node.stp など記号名が
プレーンに並ぶファイルで安全にレコード名を取得できます。
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import List, Optional


class StpReader:
    """SNAP .stp ファイルからレコード名一覧を取得するパーサー。"""

    NAME_LENGTH = 8

    def __init__(self, stp_file: str | Path) -> None:
        self.stp_file = Path(stp_file)
        self.magic: int = 0
        self.flag: int = 0
        self.internal_count: int = 0
        self.num_records: int = 0
        self.names: List[str] = []
        self._raw: bytes = b""

        if self.stp_file.exists():
            self._parse()

    # ------------------------------------------------------------------
    def _parse(self) -> None:
        with open(self.stp_file, "rb") as f:
            self._raw = f.read()

        if len(self._raw) < 16:
            return

        header = struct.unpack("<4i", self._raw[:16])
        self.magic, self.flag, self.internal_count, self.num_records = header

        self.names = self._extract_names_fixed_stride()
        if len(self.names) != self.num_records:
            # 可変長レコードのファイルはスキャン方式へフォールバック
            scanned = self._extract_names_scan()
            if len(scanned) > len(self.names):
                self.names = scanned

    # ------------------------------------------------------------------
    def _extract_names_fixed_stride(self) -> List[str]:
        """(file_size - 16) / num_records が整数の場合、固定幅でレコードを読む。

        Floor.stp / Story.stp / Rigid.stp など、固定長レコードで
        名前が先頭 8 バイトに入るファイルに対応します。
        """
        if self.num_records <= 0:
            return []
        body = self._raw[16:]
        # 候補ストライド (小さい順): 16, 24, 32, 40, 48, 64 と
        # (body_size // num_records) をあわせて試す
        candidates: List[int] = [8, 16, 24, 32, 40, 48, 64, 80, 96]
        if len(body) % self.num_records == 0:
            candidates.append(len(body) // self.num_records)

        for stride in sorted(set(candidates)):
            if stride < self.NAME_LENGTH:
                continue
            if stride * self.num_records > len(body):
                continue
            names: List[str] = []
            ok = True
            for i in range(self.num_records):
                chunk = body[i * stride:i * stride + self.NAME_LENGTH]
                decoded = self._decode_name(chunk)
                if decoded is None:
                    ok = False
                    break
                names.append(decoded)
            if ok and len(names) == self.num_records:
                return names
        return []

    # ------------------------------------------------------------------
    def _extract_names_scan(self) -> List[str]:
        """レコード名らしい 8 バイト列をファイル全体からスキャンして取得。

        Floor.stp / Story.stp では、レコードは 16 バイト以降から並ぶため、
        ヘッダ終端 (offset 16) からスキャンして印字可能 ASCII で始まり、
        長さ 8 バイトで NUL / 空白終端の文字列を拾います。
        """
        names: List[str] = []
        data = self._raw[16:]

        # 8 バイト境界で走査
        idx = 0
        while idx + self.NAME_LENGTH <= len(data) and len(names) < self.num_records:
            chunk = data[idx:idx + self.NAME_LENGTH]
            decoded = self._decode_name(chunk)
            if decoded is not None:
                names.append(decoded)
                # 次レコードへ: 最小間隔 16 バイト、実寸法はファイルごと
                # 可変なので、スキャンで次の名前候補を探す
                idx += 1
            else:
                idx += 1
        return names

    @classmethod
    def _decode_name(cls, chunk: bytes) -> Optional[str]:
        """8 バイトから妥当なレコード名を抽出（None=名前ではない）。"""
        if len(chunk) != cls.NAME_LENGTH:
            return None
        # 先頭バイトは印字可能 ASCII (英数字) であること
        first = chunk[0]
        if not (0x30 <= first <= 0x7a):  # '0' ～ 'z'
            return None
        # 印字可能 + NUL/空白のみで構成されていること
        valid_chars = 0
        for b in chunk:
            if b == 0 or b == 0x20:
                continue
            if 0x21 <= b <= 0x7e:
                valid_chars += 1
            else:
                return None
        if valid_chars == 0:
            return None
        try:
            text = chunk.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None
        # NUL 以降を切る
        nul = text.find("\x00")
        if nul >= 0:
            text = text[:nul]
        name = text.strip()
        if not name:
            return None
        # "F", "R"+数字 など簡易妥当性チェック
        if not re.match(r"^[0-9A-Za-z_\-\.]+$", name):
            return None
        return name

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """デバッグ用サマリ。"""
        lines = [
            f"StpReader: {self.stp_file.name}",
            f"  size     : {len(self._raw)} bytes",
            f"  magic    : 0x{self.magic:08x}",
            f"  flag     : {self.flag}",
            f"  num_rec  : {self.num_records}",
            f"  names[{len(self.names)}]: {', '.join(self.names[:10])}"
            + ("..." if len(self.names) > 10 else ""),
        ]
        return "\n".join(lines)
