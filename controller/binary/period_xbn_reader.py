"""
controller/binary/period_xbn_reader.py
======================================

SNAP ``Period.xbn`` 固有値解析結果リーダー（マルチモード対応版）。

既存の ``app/models/period_reader.py`` は 80 バイト / 2 モード固定の
初期実装でした。本モジュールは実サンプル (example_3D/D4, 620 バイト, 10 モード)
の観測結果に基づき、任意モード数に対応したパーサーとして書き直したものです。

ファイル形式
------------

::

    offset  size          content
    ----------------------------------------------------------------
    0       16 (4 int32) ファイルヘッダ
                         int[0]: magic
                         int[1]: 1 など（用途不明）
                         int[2]: internal_count
                         int[3]: num_modes
    16      40            モード番号配列 (10 × int32 - 常に 10 枠確保?)
    56      4             パディング (0.0 float)
    60      14 × 4        モード 1 の レコード (14 float)
    ...     ...           モード k のレコード
    ...     4             末尾パディング 1 float

モードレコード 14 float の内訳は構造形式により異なる。

**立体フレーム (3D) レイアウト (TTL[0]=0)**::

    [0] padding
    [1] padding
    [2] β_X   参加係数 (X 方向モード)
    [3] β_Y   参加係数 (Y 方向モード)
    [4] β_Z   参加係数 (Z 方向モード)
    [5] β_RX  参加係数 (回転 X)
    [6] β_RY  参加係数 (回転 Y)
    [7] T     固有周期 [秒]
    [8] ω     角振動数 [rad/s]
    [9] padding（減衰定数 ζ が入る SNAP バージョンもありうる）
    [10] PM_X 参加質量比 X [%]
    [11] PM_Y 参加質量比 Y [%]
    [12] PM_Z 参加質量比 Z [%]
    [13] PM_R 参加質量比 回転 [%]

**平面フレーム (planar) レイアウト (TTL[0]=1)**::

    [0] padding
    [1] β     参加係数（平面方向 1 DOF 分のみ）
    [2..6] padding (0)
    [7] T     固有周期 [秒]
    [8] ω     角振動数 [rad/s]
    [9] PM    参加質量比 [%]
    [10..13] padding (0)

    ※ T / ω の位置は 3D と同一。β / PM の位置だけ異なる。
    平面モデルの解析方向は EVC.解析方向 で X (=1) / Y (=2) を決めるが、
    本リーダーは便宜上 X キーに格納する（UI 表示互換のため）。

β のどれが有効かでモードの支配方向を判定できます。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ModeInfo:
    """1 モード分の固有値情報。"""
    mode_no: int
    period: float                # 固有周期 [s]
    omega: float                 # 角振動数 [rad/s]
    beta: Dict[str, float] = field(default_factory=dict)   # 参加係数 (direction -> β)
    pm: Dict[str, float] = field(default_factory=dict)     # 参加質量比 (%)
    raw: List[float] = field(default_factory=list)         # 14 float 生データ

    @property
    def frequency(self) -> float:
        return 1.0 / self.period if self.period > 0 else 0.0

    @property
    def dominant_direction(self) -> str:
        """最大参加係数方向（X/Y/Z/RX/RY）。"""
        if not self.beta:
            return "?"
        return max(self.beta.items(), key=lambda kv: abs(kv[1]))[0]

    def to_line(self) -> str:
        dom = self.dominant_direction
        pm_total = sum(abs(v) for v in self.pm.values())
        return (
            f"Mode {self.mode_no:2d}: T={self.period:7.4f}s  "
            f"f={self.frequency:6.3f}Hz  ω={self.omega:7.3f}  "
            f"dom={dom}  PM_total={pm_total:6.2f}%"
        )


class PeriodXbnReader:
    """Period.xbn マルチモード固有値リーダー。

    Attributes
    ----------
    num_modes : int
        固有モード数
    modes : list[ModeInfo]
        各モードの詳細情報
    """

    HEADER_FLOATS = 16            # 16 byte header + 12 * 4 mode number area (≒ 14 floats)
    MODE_RECORD_SIZE = 14         # 14 floats per mode
    DIRS = ("X", "Y", "Z", "RX", "RY")

    def __init__(
        self,
        period_file: str | Path,
        structure_type: Optional[int] = None,
        planar_direction: str = "X",
    ) -> None:
        """
        Parameters
        ----------
        period_file : path
            Period.xbn へのパス。
        structure_type : int, optional
            s8i TTL[0] の構造形式。0=立体フレーム, 1=平面フレーム。
            None を渡すとバイナリから自動検出する。
        planar_direction : str
            平面モデル時、参加係数/参加質量比を格納する方向キー ("X" or "Y")。
            EVC.解析方向 (1=X, 2=Y) を元に指定するのが望ましい。省略時は X。
        """
        self.period_file = Path(period_file)
        self.structure_type = structure_type
        self.planar_direction = planar_direction.upper() if planar_direction else "X"
        self.magic: int = 0
        self.num_modes: int = 0
        self.modes: List[ModeInfo] = []
        # 実際に採用したレイアウト (1=planar, 0=3D)。auto の場合は検出結果が入る。
        self.layout_is_planar: bool = False

        if self.period_file.exists():
            self._parse()

    # ------------------------------------------------------------------
    def _parse(self) -> None:
        with open(self.period_file, "rb") as f:
            data = f.read()
        n = len(data) // 4
        if n < 16:
            return
        floats = list(struct.unpack(f"<{n}f", data))
        ints = list(struct.unpack(f"<{n}i", data))

        self.magic = ints[0]
        # int[3] には num_modes が入る（実観測）
        self.num_modes = max(0, ints[3])
        if self.num_modes == 0:
            return

        # モードレコード開始オフセット（float 単位）を自動検出。
        # 実観測では offset=14 から 14 float ごとにレコードが並ぶ。
        # 他バージョン対応のため、先頭から走査して
        # 「連続する正の周期値を含むオフセット」を探す。
        start, rec_size, t_pos, w_pos = self._detect_mode_layout(floats)
        if start < 0:
            return

        # レイアウト判定: 構造形式 (TTL[0]) が明示されていればそれを採用、
        # 未指定なら最初のモードの β 分布から自動検出する。
        if self.structure_type is not None:
            is_planar = (self.structure_type == 1)
        else:
            is_planar = self._is_planar_layout(floats, start, rec_size, t_pos)
        self.layout_is_planar = is_planar

        for m in range(self.num_modes):
            s = start + m * rec_size
            e = s + rec_size
            if e > n:
                break
            rec = floats[s:e]

            T = rec[t_pos]
            omega = rec[w_pos]
            # 妥当性チェック: 2π / ω ≒ T （緩め: 10%）
            if T > 0 and omega > 0:
                ratio = (2 * 3.141592653589793 / omega) / T
                if abs(ratio - 1.0) > 0.10:
                    continue

            if is_planar:
                beta, pm = self._extract_planar(rec, w_pos, rec_size)
            else:
                beta, pm = self._extract_3d(rec, t_pos, w_pos, rec_size)

            mode = ModeInfo(
                mode_no=m + 1,
                period=float(T),
                omega=float(omega),
                beta={k: float(v) for k, v in beta.items()},
                pm={k: float(v) for k, v in pm.items()},
                raw=[float(x) for x in rec],
            )
            self.modes.append(mode)

    # ------------------------------------------------------------------
    def _is_planar_layout(
        self, floats: List[float], start: int, rec_size: int, t_pos: int
    ) -> bool:
        """β の分布から平面レイアウトかを推定する。

        立体: β は rec[2..6] に格納される → t_pos-5 .. t_pos-1 に非ゼロ値。
        平面: β は rec[1] に単一値のみ格納される → rec[2..6] が全ゼロ、rec[1] が非ゼロ。

        少なくとも 1 モードで rec[2..6] に非ゼロが現れれば立体、
        すべてのチェック対象モードで rec[2..6]=0 かつ rec[1]≠0 なら平面。
        """
        beta3d_start = max(0, t_pos - 5)
        beta3d_end = t_pos  # exclusive
        planar_idx = max(0, t_pos - 6)  # 3D で言う "pad" の位置 ≒ 平面の β
        check_modes = min(self.num_modes, 3)
        saw_planar = False
        for m in range(check_modes):
            s = start + m * rec_size
            e = s + rec_size
            if e > len(floats):
                break
            rec = floats[s:e]
            if any(abs(rec[i]) > 1e-30 for i in range(beta3d_start, beta3d_end) if i < rec_size):
                return False  # 3D β 位置に値がある → 立体
            if planar_idx < rec_size and abs(rec[planar_idx]) > 1e-30:
                saw_planar = True
        return saw_planar

    # ------------------------------------------------------------------
    def _extract_3d(
        self, rec: List[float], t_pos: int, w_pos: int, rec_size: int
    ) -> tuple:
        """立体レイアウト (3D) の β と PM を取り出す。"""
        beta_start = max(0, t_pos - 5)
        beta_keys = ("X", "Y", "Z", "RX", "RY")
        beta = {}
        for ki, k in enumerate(beta_keys):
            idx = beta_start + ki
            beta[k] = rec[idx] if idx < rec_size else 0.0

        # PM は ω の後の float群。SNAPバージョンによりパディング数が異なるため
        # w_pos+2 を優先（実観測: raw[10]がPM_X）、次に w_pos+1, +3, +4 を試す。
        pm_keys = ("X", "Y", "Z", "R")
        pm: Dict[str, float] = {}
        pm_found = False

        beta_vals = [abs(beta.get(k, 0.0)) for k in ("X", "Y", "Z")]
        dominant_beta_idx = beta_vals.index(max(beta_vals)) if max(beta_vals) > 0 else -1

        best_pm: Optional[Dict[str, float]] = None
        for pm_offset in (2, 1, 3, 4):
            pm_start = w_pos + pm_offset
            if pm_start + len(pm_keys) - 1 >= rec_size:
                continue
            candidates = [rec[pm_start + ki] for ki in range(len(pm_keys))]
            if all(0.0 <= c <= 200.0 for c in candidates):
                candidate_pm = {k: float(candidates[ki]) for ki, k in enumerate(pm_keys)}
                pm_xyz = [candidate_pm.get(k, 0.0) for k in ("X", "Y", "Z")]
                dominant_pm_idx = pm_xyz.index(max(pm_xyz)) if max(pm_xyz) > 0 else -1
                if dominant_beta_idx >= 0 and dominant_pm_idx == dominant_beta_idx:
                    pm = candidate_pm
                    pm_found = True
                    break
                elif best_pm is None:
                    best_pm = candidate_pm
        if not pm_found:
            pm = best_pm or {k: 0.0 for k in pm_keys}
        return beta, pm

    # ------------------------------------------------------------------
    def _extract_planar(
        self, rec: List[float], w_pos: int, rec_size: int
    ) -> tuple:
        """平面レイアウト (1 DOF) の β と PM を取り出す。

        β は rec[w_pos - 7] 近辺の単一値、PM は rec[w_pos + 1]。
        w_pos=8 の場合: β は rec[1]、PM は rec[9]。

        格納先の方向キーは ``self.planar_direction`` (X または Y) に従う。
        UI 側は常に X/Y/Z/RX/RY キーを参照するため、他方向はゼロで埋める。
        """
        beta_idx = max(0, w_pos - 7)  # w_pos=8 → 1, w_pos=7 → 0
        beta_val = float(rec[beta_idx]) if beta_idx < rec_size else 0.0
        pm_idx = w_pos + 1
        pm_val = float(rec[pm_idx]) if pm_idx < rec_size else 0.0
        # レンジ外（負 or 非現実的）は 0 にクリップ
        if not (0.0 <= pm_val <= 200.0):
            pm_val = 0.0

        beta = {k: 0.0 for k in ("X", "Y", "Z", "RX", "RY")}
        pm = {k: 0.0 for k in ("X", "Y", "Z", "R")}
        direction = self.planar_direction if self.planar_direction in ("X", "Y") else "X"
        beta[direction] = beta_val
        pm[direction] = pm_val
        return beta, pm

    # ------------------------------------------------------------------
    def _detect_mode_layout(
        self, floats: List[float]
    ) -> tuple:
        """モードレコードの開始オフセット・レコードサイズ・T/ω 位置を検出する。

        Returns
        -------
        (start, rec_size, t_pos, w_pos) : tuple
            start   : 先頭モードレコードの float インデックス (-1 = 検出失敗)
            rec_size: 1 モードあたりの float 数
            t_pos   : レコード内で T (固有周期) が格納される相対インデックス
            w_pos   : レコード内で ω (角振動数) が格納される相対インデックス
        """
        import math
        TWO_PI = 2 * math.pi
        n = len(floats)

        # 試行するレコードサイズ候補
        # 14 を最優先（標準フォーマット）、次に大きいサイズ → 小さいサイズの順
        size_candidates = [14] + [s for s in range(12, 25) if s != 14] + [11, 10, 9, 8]
        # T, ω のオフセット候補 (位置ペア)
        tw_candidates = [
            (7, 8), (6, 7), (5, 6), (3, 4), (1, 2), (0, 1),
        ]

        for rec_size in size_candidates:
            for t_pos, w_pos in tw_candidates:
                if t_pos >= rec_size or w_pos >= rec_size:
                    continue
                # PM が入る余地があるかチェック（w_pos + 1 以降に最低 2 float 必要）
                if w_pos + 2 >= rec_size:
                    continue
                # ヘッダ直後から走査
                for start in range(4, min(n - rec_size * max(self.num_modes, 1), 60)):
                    rec = floats[start:start + rec_size]
                    T = rec[t_pos]
                    om = rec[w_pos]
                    if not (0.005 < T < 200 and 0.03 < om < 1300):
                        continue
                    if abs((TWO_PI / om) - T) / T > 0.05:
                        continue
                    # 次のモードで確認（num_modes >= 2 なら）
                    if self.num_modes >= 2:
                        s2 = start + rec_size
                        if s2 + rec_size > n:
                            continue
                        rec2 = floats[s2:s2 + rec_size]
                        T2 = rec2[t_pos]
                        om2 = rec2[w_pos]
                        if not (0.005 < T2 < 200 and 0.03 < om2 < 1300):
                            continue
                        if abs((TWO_PI / om2) - T2) / T2 > 0.05:
                            continue
                        # 2 次モードは 1 次より周期が短いはず
                        if T2 >= T * 1.5:
                            continue
                    return start, rec_size, t_pos, w_pos

        return -1, self.MODE_RECORD_SIZE, 7, 8

    # 後方互換 alias（旧コードが呼んでいる場合）
    def _detect_mode_start(self, floats: List[float]) -> int:
        start, _, _, _ = self._detect_mode_layout(floats)
        return start

    # ------------------------------------------------------------------
    @property
    def periods(self) -> Dict[int, float]:
        return {m.mode_no: m.period for m in self.modes}

    @property
    def frequencies(self) -> Dict[int, float]:
        return {m.mode_no: m.frequency for m in self.modes}

    @property
    def participation_mass(self) -> Dict[int, float]:
        return {m.mode_no: sum(abs(v) for v in m.pm.values()) for m in self.modes}

    def to_string(self) -> str:
        lines = [
            f"固有値解析結果: {self.period_file.name}",
            f"  num_modes = {self.num_modes}",
        ]
        for m in self.modes:
            lines.append("  " + m.to_line())
        if not self.modes:
            lines.append("  (モードデータなし)")
        return "\n".join(lines)

    def summary(self) -> str:
        return self.to_string()
