"""
controller/result.py
SNAP 出力結果ファイルを読み込み・パースするクラス。

SNAP は解析完了後にテキスト形式の結果ファイルを出力します。
Result クラスはこれらを読み込んで層ごとの応答値辞書に変換します。

対応する出力項目:
  - 最大応答相対変位 (max_disp)       [m]
  - 最大応答相対速度 (max_vel)         [m/s]
  - 最大応答絶対加速度 (max_acc)       [m/s²]
  - 最大層間変形 (max_story_disp)      [m]
  - 最大層間変形角 (max_story_drift)   [rad]
  - せん断力係数 (shear_coeff)
  - 最大転倒モーメント (max_otm)       [kN・m]
"""

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


class Result:
    """
    SNAP 結果ファイルパーサー。

    Usage::

        res = Result("path/to/result_dir")
        print(res.max_disp)    # {1: 0.012, 2: 0.025, ...}
        print(res.max_acc)
        df = res.to_dataframe()

    Attributes
    ----------
    max_disp : dict
        層番号 -> 最大相対変位 [m]
    max_vel : dict
        層番号 -> 最大相対速度 [m/s]
    max_acc : dict
        層番号 -> 最大絶対加速度 [m/s²]
    max_story_disp : dict
        層番号 -> 最大層間変形 [m]
    max_story_drift : dict
        層番号 -> 最大層間変形角 [rad]
    shear_coeff : dict
        層番号 -> せん断力係数
    max_otm : dict
        層番号 -> 最大転倒モーメント [kN・m]
    input_pga : float or None
        入力地震動の最大加速度 [m/s²]（Z=0 ノードの絶対加速度）
    base_otm : float or None
        基部（0層）の最大転倒モーメント [kN・m]（Z=0 の Story データ）
    """

    def __init__(self, result_dir: str) -> None:
        self.result_dir = Path(result_dir)
        self.max_disp: Dict[int, float] = {}
        self.max_vel: Dict[int, float] = {}
        self.max_acc: Dict[int, float] = {}
        self.max_story_disp: Dict[int, float] = {}
        self.max_story_drift: Dict[int, float] = {}
        self.shear_coeff: Dict[int, float] = {}
        self.max_otm: Dict[int, float] = {}
        self.input_pga: Optional[float] = None   # Z=0 の絶対加速度 = 入力PGA
        self.base_otm: Optional[float] = None    # Z=0 の転倒モーメント
        self._raw_text: str = ""
        self.parse_log: List[str] = []  # 診断ログ（解析サービスが参照）

        if self.result_dir.exists():
            self._parse()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all(self) -> Dict[str, Any]:
        """全応答項目を辞書にまとめて返します。"""
        return {
            "max_disp": self.max_disp,
            "max_vel": self.max_vel,
            "max_acc": self.max_acc,
            "max_story_disp": self.max_story_disp,
            "max_story_drift": self.max_story_drift,
            "shear_coeff": self.shear_coeff,
            "max_otm": self.max_otm,
            "input_pga": self.input_pga,   # 入力地震動最大加速度 [m/s²]
            "base_otm": self.base_otm,     # 基部（0層）転倒モーメント [kN・m]
        }

    def get_floor_count(self) -> int:
        """解析に含まれる層数を返します。"""
        if self.max_disp:
            return max(self.max_disp.keys())
        if self.max_acc:
            return max(self.max_acc.keys())
        return 0

    def to_dataframe(self):
        """
        pandas DataFrame 形式で結果を返します。
        pandas がインストールされていない場合は ImportError を送出します。
        """
        import pandas as pd  # noqa: import-outside-toplevel

        floors = sorted(set(
            list(self.max_disp) + list(self.max_vel) + list(self.max_acc)
        ))
        data = {
            "Floor": floors,
            "MaxDisp[m]": [self.max_disp.get(f, None) for f in floors],
            "MaxVel[m/s]": [self.max_vel.get(f, None) for f in floors],
            "MaxAcc[m/s2]": [self.max_acc.get(f, None) for f in floors],
            "MaxStoryDisp[m]": [self.max_story_disp.get(f, None) for f in floors],
            "MaxStoryDrift[rad]": [self.max_story_drift.get(f, None) for f in floors],
            "ShearCoeff": [self.shear_coeff.get(f, None) for f in floors],
            "MaxOTM[kNm]": [self.max_otm.get(f, None) for f in floors],
        }
        return pd.DataFrame(data)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse(self) -> None:
        """
        SNAP 出力ファイルを読み込んでパースします。

        Floor*.txt  : FloorResult  — 各フロアの最大変位・速度・加速度
        Story*.txt  : StoryResult  — 各層の層間変形・せん断力係数・転倒モーメント

        3Dモデル対応:
            1フロアに複数ノードが存在する場合(立体フレーム)、cols[0] はノード番号であり
            フロア番号ではありません。cols[1] の Z 高さ [m] でグループ化することで
            正確なフロア番号を算出します。XY両方向の最大値を採用します。
            Z 高さは浮動小数点誤差を避けるため 1mm 単位（小数3桁）に丸めてからキーにします。
        """
        self.parse_log = []
        floor_files = sorted(self.result_dir.glob("Floor*.txt"))
        story_files = sorted(self.result_dir.glob("Story*.txt"))
        self.parse_log.append(f"  [Result] 結果フォルダ: {self.result_dir}")
        self.parse_log.append(f"  [Result] Floor*.txt: {[f.name for f in floor_files]}")
        self.parse_log.append(f"  [Result] Story*.txt: {[f.name for f in story_files]}")

        # ----- FloorResult -----
        # ヘッダ: //No,Z1,Dx2,Dy3,Vx4,Vy5,Ax6,Ay7,RAx8,RAy9
        # 単位  : D=mm, V=mm/s, A=mm/s²
        #
        # Z 高さ[m] をキーとして各方向の最大値を集約します。
        # 同一 Z に複数ノードがある3Dモデルでも正しく集計できます。
        # Z は 1mm 精度（小数3桁）に丸め、浮動小数点の比較ずれを防ぎます。
        floor_z: Dict[float, Tuple[float, float, float]] = {}  # z -> (disp, vel, acc)
        row_count = 0

        pga_acc = 0.0  # Z=0 の最大絶対加速度（入力PGA）
        for fp in floor_files:
            for cols in self._iter_cols(fp):
                if len(cols) < 5:   # 最低限 No, Z, Dx, Vx, Ax が必要
                    continue
                z = round(self._safe_float(cols[1]), 3)  # 1mm 単位に丸め
                if z <= 0.0:
                    # Z=0（地盤ノード）の絶対加速度 = 入力地震動のPGA
                    ax = abs(self._safe_float(cols[6])) if len(cols) > 6 else 0.0
                    ay = abs(self._safe_float(cols[7])) if len(cols) > 7 else 0.0
                    pga_acc = max(pga_acc, ax, ay)
                    continue
                row_count += 1
                dx  = abs(self._safe_float(cols[2]))                           # Dx [mm]
                dy  = abs(self._safe_float(cols[3])) if len(cols) > 3 else 0.0 # Dy [mm]
                vx  = abs(self._safe_float(cols[4]))                           # Vx [mm/s]
                vy  = abs(self._safe_float(cols[5])) if len(cols) > 5 else 0.0 # Vy [mm/s]
                ax  = abs(self._safe_float(cols[6])) if len(cols) > 6 else 0.0 # Ax [mm/s²]
                ay  = abs(self._safe_float(cols[7])) if len(cols) > 7 else 0.0 # Ay [mm/s²]
                prev = floor_z.get(z, (0.0, 0.0, 0.0))
                floor_z[z] = (
                    max(prev[0], dx, dy),
                    max(prev[1], vx, vy),
                    max(prev[2], ax, ay),
                )

        self.parse_log.append(f"  [Result] Floor: データ行数={row_count}, Z高さ種別={sorted(floor_z.keys())}")

        # Z=0 の PGA を保存（mm/s² → m/s²）
        if pga_acc > 0.0:
            self.input_pga = round(pga_acc / 1000, 6)
            self.parse_log.append(f"  [Result] input_pga={self.input_pga} m/s²")

        # Z 高さ昇順でフロア番号 1..N を割り当て、mm→m 変換
        for floor_no, z in enumerate(sorted(floor_z), start=1):
            disp, vel, acc = floor_z[z]
            self.max_disp[floor_no] = round(disp / 1000, 6)
            self.max_vel[floor_no]  = round(vel  / 1000, 6)
            self.max_acc[floor_no]  = round(acc  / 1000, 6)

        self.parse_log.append(f"  [Result] max_disp={self.max_disp}")

        # ----- StoryResult -----
        # ヘッダ: //No,Z1,Sx2,Sy3,Qx4,Qy5,Cx6,Cy7,Mx8,My9,Drx10,Dry11,...
        # 単位  : S=mm, Q=kN, M=kN.m, Cx=無次元, Drx=無次元
        #
        # 同様に Z 高さでグループ化し XY両方向の最大値を採用します。
        story_z: Dict[float, Tuple[float, float, float, float]] = {}  # z -> (s, c, m, dr)
        story_row_count = 0
        base_otm_val = 0.0  # Z=0 の基部転倒モーメント

        for fp in story_files:
            for cols in self._iter_cols(fp):
                if len(cols) < 7:
                    continue
                z = round(self._safe_float(cols[1]), 3)
                if z <= 0.0:
                    # Z=0（基部）の転倒モーメントを取得
                    mx = abs(self._safe_float(cols[8]))  if len(cols) > 8 else 0.0
                    my = abs(self._safe_float(cols[9]))  if len(cols) > 9 else 0.0
                    base_otm_val = max(base_otm_val, mx, my)
                    continue
                story_row_count += 1
                sx  = abs(self._safe_float(cols[2]))                            # Sx [mm]
                sy  = abs(self._safe_float(cols[3]))  if len(cols) > 3 else 0.0 # Sy [mm]
                cx  = abs(self._safe_float(cols[6]))  if len(cols) > 6 else 0.0 # Cx せん断力係数
                cy  = abs(self._safe_float(cols[7]))  if len(cols) > 7 else 0.0 # Cy せん断力係数
                mx  = abs(self._safe_float(cols[8]))  if len(cols) > 8 else 0.0 # Mx 転倒モーメント [kN.m]
                my  = abs(self._safe_float(cols[9]))  if len(cols) > 9 else 0.0 # My 転倒モーメント [kN.m]
                drx = abs(self._safe_float(cols[10])) if len(cols) > 10 else 0.0 # Drx 層間変形角
                dry = abs(self._safe_float(cols[11])) if len(cols) > 11 else 0.0 # Dry
                prev = story_z.get(z, (0.0, 0.0, 0.0, 0.0))
                story_z[z] = (
                    max(prev[0], sx, sy),
                    max(prev[1], cx, cy),
                    max(prev[2], mx, my),
                    max(prev[3], drx, dry),
                )

        self.parse_log.append(f"  [Result] Story: データ行数={story_row_count}, Z高さ種別={sorted(story_z.keys())}")

        # Z=0 の基部転倒モーメントを保存
        if base_otm_val > 0.0:
            self.base_otm = round(base_otm_val, 2)
            self.parse_log.append(f"  [Result] base_otm={self.base_otm} kN・m")

        # Z 高さ昇順でフロア番号 1..N を割り当て
        for story_no, z in enumerate(sorted(story_z), start=1):
            s, c, m, dr = story_z[z]
            self.max_story_disp[story_no]  = round(s  / 1000, 6)  # mm → m
            self.shear_coeff[story_no]     = round(c,  6)
            self.max_otm[story_no]         = round(m,  2)
            self.max_story_drift[story_no] = round(dr, 6)

        self.parse_log.append(f"  [Result] max_story_drift={self.max_story_drift}")

    @staticmethod
    def _read_file(path: Path) -> str:
        for enc in ("shift_jis", "cp932", "utf-8"):
            try:
                return path.read_text(encoding=enc, errors="replace")
            except Exception:
                continue
        return ""

    @classmethod
    def _iter_cols(cls, path: Path) -> Iterator[List[str]]:
        """
        SNAP テキストファイルを行単位でパースし、カラムリストを yield します。

        - ``//`` で始まる行（コメント・ヘッダ）はスキップ
        - SNAP の出力は指数表記が ``0.00e+`` のように末尾省略されることがあるため
          正規化してから float 変換できるようにします
        """
        text = cls._read_file(path)
        if not text:
            return
        # 末尾が切れた指数表記を補完: "1.23e+" → "1.23e+00", "1.23e-" → "1.23e-00"
        text = re.sub(r'([eE][+\-])(?=\s|$)', r'\g<1>00', text)

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            cols = stripped.split()
            if len(cols) < 2:
                continue
            # 先頭カラムが数値でない行はスキップ
            try:
                float(cols[0])
            except ValueError:
                continue
            yield cols

    @classmethod
    def _iter_data_rows(cls, path: Path) -> Iterator[Tuple[int, List[str]]]:
        """
        後方互換のため残存。_iter_cols の結果を (floor_no, cols) 形式で返します。
        新規コードでは _iter_cols を直接使用してください。
        """
        for cols in cls._iter_cols(path):
            try:
                floor_no = int(float(cols[0]))
            except ValueError:
                continue
            yield floor_no, cols

    @staticmethod
    def _safe_float(s: str) -> float:
        """文字列を float に変換します。失敗した場合は 0.0 を返します。"""
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    @classmethod
    def from_mock(cls, floors: int = 5) -> "Result":
        """
        テスト・デモ用のモックデータを生成します。
        実際の SNAP がなくても UI の動作確認が可能です。
        """
        import math  # noqa

        res = cls.__new__(cls)
        res.result_dir = Path("mock")
        res._raw_text = ""

        scale = 1.0
        res.max_disp = {i: round(scale * 0.005 * i, 4) for i in range(1, floors + 1)}
        res.max_vel = {i: round(scale * 0.12 * math.sqrt(i), 4) for i in range(1, floors + 1)}
        res.max_acc = {i: round(scale * (2.5 + 0.5 * i), 4) for i in range(1, floors + 1)}
        res.max_story_disp = {i: round(scale * 0.003 * i, 4) for i in range(1, floors + 1)}
        res.max_story_drift = {i: round(scale * 0.003 * i / (3.0 * i), 6) for i in range(1, floors + 1)}
        res.shear_coeff = {i: round(scale * (0.3 - 0.02 * (i - 1)), 4) for i in range(1, floors + 1)}
        res.max_otm = {i: round(scale * 5000 * (floors - i + 1) * 3.0, 1) for i in range(1, floors + 1)}
        res.input_pga = round(scale * (2.5 + 0.5 * floors), 4)   # 入力 PGA [m/s²]
        res.base_otm = round(scale * 5000 * floors * 3.0, 1)      # 基部転倒モーメント [kN·m]
        res.parse_log = ["[from_mock] モックデータを生成しました"]

        return res
