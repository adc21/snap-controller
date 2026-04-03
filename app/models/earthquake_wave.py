"""
app/models/earthquake_wave.py
地震波データモデル。

入力地震波の選択・管理を行うためのデータモデルです。
建築構造設計で一般的に使用される地震波（告示波、観測波等）を
カタログとして管理し、解析ケースに紐づけます。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class EarthquakeWave:
    """
    1つの地震波データを表すデータクラス。

    Attributes
    ----------
    id : str
        一意識別子。
    name : str
        表示名（例: "El Centro NS 1940"）。
    category : str
        カテゴリ（"observed", "synthetic", "告示波", "site_specific"）。
    description : str
        説明テキスト。
    file_path : str
        地震波ファイルのパス。
    direction : str
        入力方向（"X", "Y", "Z", "XY"）。
    scale_factor : float
        倍率。
    max_acc : float
        最大加速度 [cm/s²]（参考値）。
    duration : float
        継続時間 [sec]（参考値）。
    dt : float
        時間刻み [sec]。
    source : str
        データ出典。
    is_builtin : bool
        組み込みデータかどうか。
    """

    id: str = ""
    name: str = ""
    category: str = ""
    description: str = ""
    file_path: str = ""
    direction: str = "X"
    scale_factor: float = 1.0
    max_acc: float = 0.0
    duration: float = 0.0
    dt: float = 0.01
    source: str = ""
    is_builtin: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EarthquakeWave":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# カテゴリ定義
# ---------------------------------------------------------------------------

WAVE_CATEGORIES = {
    "observed": {
        "label": "観測波",
        "description": "過去の地震で観測された地震波",
        "icon": "📡",
    },
    "synthetic": {
        "label": "模擬地震波（告示波）",
        "description": "建築基準法告示に基づく模擬地震波",
        "icon": "📐",
    },
    "site_specific": {
        "label": "サイト波",
        "description": "敷地固有の検討用地震波",
        "icon": "📍",
    },
    "custom": {
        "label": "カスタム",
        "description": "ユーザー定義の地震波",
        "icon": "📂",
    },
}


# ---------------------------------------------------------------------------
# 組み込み地震波カタログ
# ---------------------------------------------------------------------------

def _builtin_waves() -> List[EarthquakeWave]:
    """
    建築構造設計で一般的に使用される地震波のカタログを返します。
    """
    waves = []

    # ====== 観測波（レベル2相当） ======
    waves.append(EarthquakeWave(
        id="el_centro_ns",
        name="El Centro NS 1940",
        category="observed",
        description="1940年 Imperial Valley地震（M6.9）El Centro観測所 NS成分。"
                    "建築構造の動的解析で最も広く使用される代表的観測波。",
        max_acc=341.7,
        duration=53.76,
        dt=0.02,
        source="Imperial Valley Earthquake, May 18, 1940",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="el_centro_ew",
        name="El Centro EW 1940",
        category="observed",
        description="1940年 Imperial Valley地震 El Centro観測所 EW成分。",
        max_acc=210.1,
        duration=53.76,
        dt=0.02,
        source="Imperial Valley Earthquake, May 18, 1940",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="taft_ns",
        name="Taft NS 1952",
        category="observed",
        description="1952年 Kern County地震（M7.3）Taft観測所 NS成分。"
                    "El Centroと並び動的解析の標準入力波。",
        max_acc=175.9,
        duration=54.38,
        dt=0.02,
        source="Kern County Earthquake, July 21, 1952",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="taft_ew",
        name="Taft EW 1952",
        category="observed",
        description="1952年 Kern County地震 Taft観測所 EW成分。",
        max_acc=152.7,
        duration=54.38,
        dt=0.02,
        source="Kern County Earthquake, July 21, 1952",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="hachinohe_ns",
        name="八戸 NS 1968",
        category="observed",
        description="1968年 十勝沖地震（M7.9）八戸港湾 NS成分。"
                    "日本の動的解析で標準的に使用される代表的観測波。",
        max_acc=225.0,
        duration=50.0,
        dt=0.02,
        source="十勝沖地震, 1968年5月16日",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="hachinohe_ew",
        name="八戸 EW 1968",
        category="observed",
        description="1968年 十勝沖地震 八戸港湾 EW成分。",
        max_acc=183.0,
        duration=50.0,
        dt=0.02,
        source="十勝沖地震, 1968年5月16日",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kobe_ns",
        name="JMA神戸 NS 1995",
        category="observed",
        description="1995年 兵庫県南部地震（M7.3）JMA神戸海洋気象台 NS成分。"
                    "直下型地震の代表的入力波。パルス性地震動。",
        max_acc=818.0,
        duration=30.0,
        dt=0.02,
        source="兵庫県南部地震, 1995年1月17日",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kobe_ew",
        name="JMA神戸 EW 1995",
        category="observed",
        description="1995年 兵庫県南部地震 JMA神戸海洋気象台 EW成分。",
        max_acc=617.0,
        duration=30.0,
        dt=0.02,
        source="兵庫県南部地震, 1995年1月17日",
        is_builtin=True,
    ))

    # ====== 告示波（模擬地震波） ======
    waves.append(EarthquakeWave(
        id="kokujihado_1",
        name="告示波 第1種地盤 (極めて稀)",
        category="synthetic",
        description="建築基準法施行令告示 極めて稀に発生する地震動レベル。"
                    "第1種地盤（岩盤・硬質地盤）用の模擬地震波。",
        max_acc=800.0,
        duration=60.0,
        dt=0.01,
        source="建築基準法施行令 告示1461号",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kokujihado_2",
        name="告示波 第2種地盤 (極めて稀)",
        category="synthetic",
        description="建築基準法施行令告示 極めて稀に発生する地震動レベル。"
                    "第2種地盤（普通地盤）用の模擬地震波。",
        max_acc=800.0,
        duration=60.0,
        dt=0.01,
        source="建築基準法施行令 告示1461号",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kokujihado_3",
        name="告示波 第3種地盤 (極めて稀)",
        category="synthetic",
        description="建築基準法施行令告示 極めて稀に発生する地震動レベル。"
                    "第3種地盤（軟弱地盤）用の模擬地震波。",
        max_acc=800.0,
        duration=60.0,
        dt=0.01,
        source="建築基準法施行令 告示1461号",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kokujihado_rare_1",
        name="告示波 第1種地盤 (稀に発生)",
        category="synthetic",
        description="建築基準法施行令告示 稀に発生する地震動レベル。"
                    "第1種地盤用。極めて稀の約1/5のレベル。",
        max_acc=160.0,
        duration=60.0,
        dt=0.01,
        source="建築基準法施行令 告示1461号",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kokujihado_rare_2",
        name="告示波 第2種地盤 (稀に発生)",
        category="synthetic",
        description="建築基準法施行令告示 稀に発生する地震動レベル。"
                    "第2種地盤用。",
        max_acc=160.0,
        duration=60.0,
        dt=0.01,
        source="建築基準法施行令 告示1461号",
        is_builtin=True,
    ))

    waves.append(EarthquakeWave(
        id="kokujihado_rare_3",
        name="告示波 第3種地盤 (稀に発生)",
        category="synthetic",
        description="建築基準法施行令告示 稀に発生する地震動レベル。"
                    "第3種地盤用。",
        max_acc=160.0,
        duration=60.0,
        dt=0.01,
        source="建築基準法施行令 告示1461号",
        is_builtin=True,
    ))

    return waves


# ---------------------------------------------------------------------------
# EarthquakeWaveCatalog クラス
# ---------------------------------------------------------------------------

class EarthquakeWaveCatalog:
    """
    地震波カタログの管理クラス。
    組み込み地震波 + ユーザー追加地震波の統合管理を行います。
    """

    def __init__(self) -> None:
        self._waves: List[EarthquakeWave] = _builtin_waves()

    @property
    def all_waves(self) -> List[EarthquakeWave]:
        """全地震波のリスト。"""
        return list(self._waves)

    def get_by_id(self, wave_id: str) -> Optional[EarthquakeWave]:
        """IDで地震波を検索。"""
        return next((w for w in self._waves if w.id == wave_id), None)

    def get_by_category(self, category: str) -> List[EarthquakeWave]:
        """カテゴリでフィルタリング。"""
        return [w for w in self._waves if w.category == category]

    def search(self, query: str) -> List[EarthquakeWave]:
        """テキスト検索。"""
        q = query.lower()
        return [
            w for w in self._waves
            if q in w.name.lower()
            or q in w.description.lower()
            or q in w.category.lower()
            or q in w.source.lower()
        ]

    def add_custom(self, wave: EarthquakeWave) -> None:
        """カスタム地震波を追加。"""
        wave.is_builtin = False
        self._waves = [w for w in self._waves if w.id != wave.id]
        self._waves.append(wave)

    def remove_custom(self, wave_id: str) -> bool:
        """カスタム地震波を削除（組み込みは削除不可）。"""
        before = len(self._waves)
        self._waves = [
            w for w in self._waves
            if not (w.id == wave_id and not w.is_builtin)
        ]
        return len(self._waves) < before

    def get_categories(self) -> List[Dict[str, Any]]:
        """利用可能なカテゴリ一覧を返す。"""
        result = []
        for key, info in WAVE_CATEGORIES.items():
            count = len(self.get_by_category(key))
            result.append({
                "key": key,
                "label": info["label"],
                "description": info["description"],
                "icon": info["icon"],
                "count": count,
            })
        return result

    def save_custom(self, path: str) -> None:
        """カスタム地震波をJSONに保存。"""
        custom = [w.to_dict() for w in self._waves if not w.is_builtin]
        fp = Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"custom_waves": custom}, f, ensure_ascii=False, indent=2)

    def load_custom(self, path: str) -> int:
        """カスタム地震波をJSONから読込。追加した数を返す。"""
        fp = Path(path)
        if not fp.exists():
            return 0
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for item in data.get("custom_waves", []):
            wave = EarthquakeWave.from_dict(item)
            wave.is_builtin = False
            self.add_custom(wave)
            count += 1
        return count


# ---------------------------------------------------------------------------
# グローバルインスタンス
# ---------------------------------------------------------------------------

_global_catalog: Optional[EarthquakeWaveCatalog] = None


def get_wave_catalog() -> EarthquakeWaveCatalog:
    """グローバル地震波カタログを返します。"""
    global _global_catalog
    if _global_catalog is None:
        _global_catalog = EarthquakeWaveCatalog()
    return _global_catalog
