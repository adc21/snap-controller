"""
app/models/damper_catalog.py
ダンパーカタログ（制振・免震装置ライブラリ）。

建築構造で一般的に使用される制振・免震装置の種類とデフォルトパラメータを
カタログとして管理します。ユーザーはカタログからダンパーを選択して
ケースに適用できます。

カタログデータはJSONファイルとして保存・読込も可能です。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DamperSpec:
    """
    ダンパー仕様の1エントリ。

    Attributes
    ----------
    id : str
        一意識別子（例: "oil_standard_500"）。
    name : str
        表示名（例: "オイルダンパー 500kN"）。
    category : str
        カテゴリ（"oil", "viscous", "steel", "viscoelastic", "tuned_mass", "isolator"）。
    snap_keyword : str
        SNAP の定義キーワード（"DVOD", "DSD" 等）。
    description : str
        説明テキスト。
    manufacturer : str
        メーカー名（参考情報）。
    parameters : dict
        デフォルトパラメータ辞書。キーはフィールドインデックス（文字列）。
    param_ranges : dict
        パラメータの推奨範囲。{field_index: {"min": float, "max": float, "unit": str}}
    tags : list of str
        検索用タグ。
    is_custom : bool
        ユーザー追加のカスタム定義かどうか。
    """

    id: str = ""
    name: str = ""
    category: str = ""
    snap_keyword: str = ""
    description: str = ""
    manufacturer: str = ""
    parameters: Dict[str, str] = field(default_factory=dict)
    param_ranges: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    is_custom: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DamperSpec":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# カテゴリ定義
# ---------------------------------------------------------------------------

DAMPER_CATEGORIES = {
    "oil": {
        "label": "オイルダンパー",
        "description": "粘性流体の抵抗を利用した速度依存型ダンパー",
        "snap_keyword": "DVOD",
        "icon": "💧",
    },
    "steel": {
        "label": "鋼材ダンパー",
        "description": "鋼材の塑性変形を利用した履歴型ダンパー",
        "snap_keyword": "DSD",
        "icon": "🔩",
    },
    "viscous": {
        "label": "粘性ダンパー",
        "description": "粘性体のせん断抵抗を利用したダンパー",
        "snap_keyword": "DVOD",
        "icon": "🌊",
    },
    "viscoelastic": {
        "label": "粘弾性ダンパー",
        "description": "粘弾性体の剛性と減衰を利用したダンパー",
        "snap_keyword": "DVOD",
        "icon": "🧱",
    },
    "tuned_mass": {
        "label": "TMD（同調質量ダンパー）",
        "description": "付加質量の振動で建物の振動エネルギーを吸収",
        "snap_keyword": "DVOD",
        "icon": "⚖️",
    },
    "irdt": {
        "label": "iRDT（慣性質量ダンパー）",
        "description": "回転慣性質量とダッシュポット・バネの組合せによる同調型制振装置",
        "snap_keyword": "DVOD",
        "icon": "🔄",
    },
    "iod": {
        "label": "iOD（大質量型オイルダンパー）",
        "description": "慣性質量と粘性減衰を並列配置した質量系ダンパー",
        "snap_keyword": "DVOD",
        "icon": "⚙️",
    },
    "isolator": {
        "label": "免震装置",
        "description": "建物を地盤から絶縁して地震力を低減",
        "snap_keyword": "DVOD",
        "icon": "🏗️",
    },
}


# ---------------------------------------------------------------------------
# 組み込みカタログデータ
# ---------------------------------------------------------------------------

def _builtin_catalog() -> List[DamperSpec]:
    """
    組み込みのダンパーカタログを返します。
    実際のSNAPパラメータに対応した代表的な仕様を定義しています。
    """
    catalog = []

    # ====== オイルダンパー ======
    catalog.append(DamperSpec(
        id="oil_standard_300",
        name="オイルダンパー 300kN級",
        category="oil",
        snap_keyword="DVOD",
        description="中低層建物向け標準オイルダンパー。"
                    "減衰係数300kN/(m/s)^α程度。",
        manufacturer="一般",
        parameters={
            "1": "0",    # タイプ: 標準
            "2": "0",    # 方向
            "3": "0",    # 基本特性
            "4": "OD300",  # 名称
            "5": "0",    # 速度依存タイプ
            "6": "1",    # 有効
            "7": "500",  # リリーフ力 (kN)
            "8": "300",  # 減衰係数 Ce
            "9": "0.4",  # 速度指数 α
            "10": "0.2", # ストローク (m)
        },
        param_ranges={
            "7": {"min": 100, "max": 1500, "unit": "kN", "label": "リリーフ力"},
            "8": {"min": 50, "max": 2000, "unit": "kN/(m/s)^α", "label": "減衰係数 Ce"},
            "9": {"min": 0.1, "max": 1.0, "unit": "-", "label": "速度指数 α"},
            "10": {"min": 0.05, "max": 0.5, "unit": "m", "label": "ストローク"},
        },
        tags=["オイル", "速度依存", "中低層", "標準"],
    ))

    catalog.append(DamperSpec(
        id="oil_standard_500",
        name="オイルダンパー 500kN級",
        category="oil",
        snap_keyword="DVOD",
        description="中高層建物向け標準オイルダンパー。"
                    "減衰係数500kN/(m/s)^α程度。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "OD500",
            "5": "0",
            "6": "1",
            "7": "800",
            "8": "500",
            "9": "0.4",
            "10": "0.3",
        },
        param_ranges={
            "7": {"min": 200, "max": 2500, "unit": "kN", "label": "リリーフ力"},
            "8": {"min": 100, "max": 3000, "unit": "kN/(m/s)^α", "label": "減衰係数 Ce"},
            "9": {"min": 0.1, "max": 1.0, "unit": "-", "label": "速度指数 α"},
            "10": {"min": 0.05, "max": 0.6, "unit": "m", "label": "ストローク"},
        },
        tags=["オイル", "速度依存", "中高層", "標準"],
    ))

    catalog.append(DamperSpec(
        id="oil_standard_1000",
        name="オイルダンパー 1000kN級",
        category="oil",
        snap_keyword="DVOD",
        description="高層建物向け大容量オイルダンパー。"
                    "減衰係数1000kN/(m/s)^α程度。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "OD1000",
            "5": "0",
            "6": "1",
            "7": "1500",
            "8": "1000",
            "9": "0.3",
            "10": "0.4",
        },
        param_ranges={
            "7": {"min": 500, "max": 5000, "unit": "kN", "label": "リリーフ力"},
            "8": {"min": 200, "max": 5000, "unit": "kN/(m/s)^α", "label": "減衰係数 Ce"},
            "9": {"min": 0.1, "max": 1.0, "unit": "-", "label": "速度指数 α"},
            "10": {"min": 0.1, "max": 0.8, "unit": "m", "label": "ストローク"},
        },
        tags=["オイル", "速度依存", "高層", "大容量"],
    ))

    catalog.append(DamperSpec(
        id="oil_bilinear_500",
        name="バイリニア型オイルダンパー 500kN",
        category="oil",
        snap_keyword="DVOD",
        description="リリーフ付きバイリニア型オイルダンパー。"
                    "一定速度以上でリリーフ弁が開き力が頭打ちになる。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "ODB500",
            "5": "1",    # バイリニアタイプ
            "6": "1",
            "7": "500",
            "8": "500",
            "9": "1.0",  # 線形
            "10": "0.3",
        },
        param_ranges={
            "7": {"min": 100, "max": 3000, "unit": "kN", "label": "リリーフ力"},
            "8": {"min": 50, "max": 3000, "unit": "kN/(m/s)", "label": "減衰係数 Ce"},
            "9": {"min": 0.5, "max": 1.0, "unit": "-", "label": "速度指数 α"},
            "10": {"min": 0.05, "max": 0.6, "unit": "m", "label": "ストローク"},
        },
        tags=["オイル", "バイリニア", "リリーフ", "速度依存"],
    ))

    # ====== 鋼材ダンパー ======
    catalog.append(DamperSpec(
        id="steel_standard_200",
        name="鋼材ダンパー 200kN級",
        category="steel",
        snap_keyword="DSD",
        description="座屈拘束ブレース型鋼材ダンパー。"
                    "降伏荷重200kN程度。繰返し塑性変形で履歴エネルギーを吸収。",
        manufacturer="一般",
        parameters={
            "1": "0",     # タイプ: 標準
            "2": "0",     # 方向
            "3": "0",     # 基本特性
            "4": "SD200", # 名称
            "5": "1",     # 有効
            "6": "0",     # リリーフ力
            "7": "200",   # 降伏荷重 Qy (kN)
            "8": "50000", # 初期剛性 K (kN/m)
            "9": "0.15",  # ストローク (m)
            "10": "0.02", # 2次剛性比 α
        },
        param_ranges={
            "7": {"min": 50, "max": 1000, "unit": "kN", "label": "降伏荷重 Qy"},
            "8": {"min": 10000, "max": 200000, "unit": "kN/m", "label": "初期剛性 K"},
            "9": {"min": 0.05, "max": 0.4, "unit": "m", "label": "ストローク"},
            "10": {"min": 0.001, "max": 0.1, "unit": "-", "label": "2次剛性比 α"},
        },
        tags=["鋼材", "履歴", "座屈拘束ブレース", "変位依存"],
    ))

    catalog.append(DamperSpec(
        id="steel_standard_500",
        name="鋼材ダンパー 500kN級",
        category="steel",
        snap_keyword="DSD",
        description="座屈拘束ブレース型鋼材ダンパー。"
                    "降伏荷重500kN程度。中高層建物向け。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "SD500",
            "5": "1",
            "6": "0",
            "7": "500",
            "8": "100000",
            "9": "0.2",
            "10": "0.02",
        },
        param_ranges={
            "7": {"min": 100, "max": 2000, "unit": "kN", "label": "降伏荷重 Qy"},
            "8": {"min": 20000, "max": 500000, "unit": "kN/m", "label": "初期剛性 K"},
            "9": {"min": 0.05, "max": 0.5, "unit": "m", "label": "ストローク"},
            "10": {"min": 0.001, "max": 0.1, "unit": "-", "label": "2次剛性比 α"},
        },
        tags=["鋼材", "履歴", "座屈拘束ブレース", "変位依存", "中高層"],
    ))

    catalog.append(DamperSpec(
        id="steel_shear_panel_300",
        name="極低降伏点鋼パネルダンパー 300kN",
        category="steel",
        snap_keyword="DSD",
        description="極低降伏点鋼のせん断パネルダンパー。"
                    "早期に降伏し、安定した履歴特性を示す。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "SPD300",
            "5": "1",
            "6": "0",
            "7": "300",
            "8": "80000",
            "9": "0.15",
            "10": "0.01",
        },
        param_ranges={
            "7": {"min": 50, "max": 1500, "unit": "kN", "label": "降伏荷重 Qy"},
            "8": {"min": 15000, "max": 300000, "unit": "kN/m", "label": "初期剛性 K"},
            "9": {"min": 0.03, "max": 0.3, "unit": "m", "label": "ストローク"},
            "10": {"min": 0.001, "max": 0.05, "unit": "-", "label": "2次剛性比 α"},
        },
        tags=["鋼材", "履歴", "パネル", "極低降伏点鋼", "変位依存"],
    ))

    # ====== 粘性ダンパー ======
    catalog.append(DamperSpec(
        id="viscous_wall_200",
        name="粘性壁 200kN級",
        category="viscous",
        snap_keyword="DVOD",
        description="粘性体を鋼板間に挟んだ壁型ダンパー。"
                    "低速度域から安定した減衰力を発揮。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "VW200",
            "5": "0",
            "6": "1",
            "7": "300",
            "8": "200",
            "9": "1.0",   # 線形
            "10": "0.1",
        },
        param_ranges={
            "7": {"min": 50, "max": 1000, "unit": "kN", "label": "リリーフ力"},
            "8": {"min": 30, "max": 1500, "unit": "kN/(m/s)", "label": "減衰係数 Ce"},
            "9": {"min": 0.8, "max": 1.0, "unit": "-", "label": "速度指数 α"},
            "10": {"min": 0.02, "max": 0.3, "unit": "m", "label": "ストローク"},
        },
        tags=["粘性", "壁", "線形", "速度依存"],
    ))

    # ====== 粘弾性ダンパー ======
    catalog.append(DamperSpec(
        id="viscoelastic_standard_150",
        name="粘弾性ダンパー 150kN級",
        category="viscoelastic",
        snap_keyword="DVOD",
        description="粘弾性体（高減衰ゴム等）を利用したダンパー。"
                    "剛性と減衰の両方を発揮。風揺れにも有効。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "VE150",
            "5": "0",
            "6": "1",
            "7": "200",
            "8": "150",
            "9": "0.5",
            "10": "0.08",
        },
        param_ranges={
            "7": {"min": 30, "max": 800, "unit": "kN", "label": "リリーフ力"},
            "8": {"min": 20, "max": 1000, "unit": "kN/(m/s)^α", "label": "減衰係数 Ce"},
            "9": {"min": 0.3, "max": 0.8, "unit": "-", "label": "速度指数 α"},
            "10": {"min": 0.02, "max": 0.2, "unit": "m", "label": "ストローク"},
        },
        tags=["粘弾性", "高減衰ゴム", "風揺れ", "剛性付加"],
    ))

    # ====== iRDT（慣性質量ダンパー — 同調型） ======
    catalog.append(DamperSpec(
        id="irdt_standard",
        name="iRDT 標準型",
        category="irdt",
        snap_keyword="DVOD",
        description="回転慣性質量 m_d + 粘性減衰 c_d + 支持バネ k_b を並列配置した"
                    "同調質量ダンパー。定点理論による最適同調が可能。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "iRDT",
            "5": "0",
            "6": "1",
            "7": "0.02",    # 質量比 μ (= m_d / M_total)
            "8": "0.98",    # 固有振動数比 f (= ω_d / ω_1)
            "9": "0.05",    # 減衰定数 ζ_d
            "10": "10000",  # 支持バネ k_b (kN/m)
        },
        param_ranges={
            "7": {"min": 0.005, "max": 0.10, "unit": "-", "label": "質量比 μ"},
            "8": {"min": 0.80, "max": 1.20, "unit": "-", "label": "振動数比 f"},
            "9": {"min": 0.01, "max": 0.30, "unit": "-", "label": "減衰定数 ζ_d"},
            "10": {"min": 1000, "max": 100000, "unit": "kN/m", "label": "支持バネ k_b"},
        },
        tags=["iRDT", "慣性質量", "同調", "回転", "TMD型", "定点理論"],
    ))

    catalog.append(DamperSpec(
        id="irdt_high_mass",
        name="iRDT 高質量比型",
        category="irdt",
        snap_keyword="DVOD",
        description="質量比 μ=0.05〜0.10 で大きな慣性質量を利用する iRDT。"
                    "高層建物の1次モード制振に効果的。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "iRDT-H",
            "5": "0",
            "6": "1",
            "7": "0.07",
            "8": "0.96",
            "9": "0.08",
            "10": "30000",
        },
        param_ranges={
            "7": {"min": 0.03, "max": 0.15, "unit": "-", "label": "質量比 μ"},
            "8": {"min": 0.80, "max": 1.10, "unit": "-", "label": "振動数比 f"},
            "9": {"min": 0.03, "max": 0.20, "unit": "-", "label": "減衰定数 ζ_d"},
            "10": {"min": 5000, "max": 200000, "unit": "kN/m", "label": "支持バネ k_b"},
        },
        tags=["iRDT", "慣性質量", "高質量比", "高層", "定点理論"],
    ))

    # ====== iOD（大質量型オイルダンパー） ======
    catalog.append(DamperSpec(
        id="iod_standard",
        name="iOD 標準型（慣性質量+粘性減衰）",
        category="iod",
        snap_keyword="DVOD",
        description="慣性質量 m_d と粘性減衰 c_d を並列配置した大質量型オイルダンパー。"
                    "支持バネなし（k_b=0）でフローティングマス的に機能。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "iOD",
            "5": "0",
            "6": "1",
            "7": "0.03",   # 質量比 μ
            "8": "0.0",    # 固有振動数比 f（同調なし）
            "9": "0.10",   # 減衰定数 ζ_d
            "10": "0",     # 支持バネ k_b = 0
        },
        param_ranges={
            "7": {"min": 0.01, "max": 0.15, "unit": "-", "label": "質量比 μ"},
            "8": {"min": 0.0, "max": 0.5, "unit": "-", "label": "振動数比 f"},
            "9": {"min": 0.02, "max": 0.30, "unit": "-", "label": "減衰定数 ζ_d"},
            "10": {"min": 0, "max": 50000, "unit": "kN/m", "label": "支持バネ k_b"},
        },
        tags=["iOD", "大質量", "慣性質量", "粘性", "フローティング"],
    ))

    catalog.append(DamperSpec(
        id="iod_with_spring",
        name="iOD バネ付き型（iHGD相当）",
        category="iod",
        snap_keyword="DVOD",
        description="慣性質量 m_d + 粘性減衰 c_d + 支持バネ k_b を備えた大質量型オイルダンパー。"
                    "バネにより同調効果も得られるiRDTの変形。",
        manufacturer="一般",
        parameters={
            "1": "0",
            "2": "0",
            "3": "0",
            "4": "iHGD",
            "5": "0",
            "6": "1",
            "7": "0.04",
            "8": "0.95",
            "9": "0.12",
            "10": "15000",
        },
        param_ranges={
            "7": {"min": 0.01, "max": 0.15, "unit": "-", "label": "質量比 μ"},
            "8": {"min": 0.70, "max": 1.20, "unit": "-", "label": "振動数比 f"},
            "9": {"min": 0.02, "max": 0.30, "unit": "-", "label": "減衰定数 ζ_d"},
            "10": {"min": 1000, "max": 100000, "unit": "kN/m", "label": "支持バネ k_b"},
        },
        tags=["iOD", "iHGD", "大質量", "慣性質量", "バネ付き", "同調"],
    ))

    return catalog


# ---------------------------------------------------------------------------
# DamperCatalog クラス
# ---------------------------------------------------------------------------

class DamperCatalog:
    """
    ダンパーカタログの管理クラス。
    組み込みカタログ＋ユーザーカスタムカタログの統合管理を行います。
    """

    def __init__(self) -> None:
        self._specs: List[DamperSpec] = _builtin_catalog()
        self._custom_path: Optional[Path] = None

    @property
    def all_specs(self) -> List[DamperSpec]:
        """全ダンパー仕様のリスト。"""
        return list(self._specs)

    def get_by_id(self, spec_id: str) -> Optional[DamperSpec]:
        """IDで仕様を検索。"""
        return next((s for s in self._specs if s.id == spec_id), None)

    def get_by_category(self, category: str) -> List[DamperSpec]:
        """カテゴリでフィルタリング。"""
        return [s for s in self._specs if s.category == category]

    def get_by_keyword(self, keyword: str) -> List[DamperSpec]:
        """SNAPキーワードでフィルタリング。"""
        return [s for s in self._specs if s.snap_keyword == keyword]

    def search(self, query: str) -> List[DamperSpec]:
        """テキスト検索（名前、説明、タグ）。"""
        q = query.lower()
        results = []
        for spec in self._specs:
            if (q in spec.name.lower()
                or q in spec.description.lower()
                or any(q in tag.lower() for tag in spec.tags)
                or q in spec.category.lower()):
                results.append(spec)
        return results

    def add_custom(self, spec: DamperSpec) -> None:
        """ユーザーカスタム仕様を追加。"""
        spec.is_custom = True
        # 同じIDがあれば上書き
        self._specs = [s for s in self._specs if s.id != spec.id]
        self._specs.append(spec)

    def remove_custom(self, spec_id: str) -> bool:
        """カスタム仕様を削除。組み込みは削除不可。"""
        before = len(self._specs)
        self._specs = [
            s for s in self._specs
            if not (s.id == spec_id and s.is_custom)
        ]
        return len(self._specs) < before

    def get_categories(self) -> List[Dict[str, str]]:
        """利用可能なカテゴリ一覧を返す。"""
        result = []
        for key, info in DAMPER_CATEGORIES.items():
            count = len(self.get_by_category(key))
            result.append({
                "key": key,
                "label": info["label"],
                "description": info["description"],
                "icon": info["icon"],
                "count": count,
            })
        return result

    # ------------------------------------------------------------------
    # JSON 保存 / 読込（カスタム仕様のみ）
    # ------------------------------------------------------------------

    def save_custom(self, path: str) -> None:
        """カスタム仕様をJSONに保存。"""
        custom = [s.to_dict() for s in self._specs if s.is_custom]
        fp = Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"custom_dampers": custom}, f, ensure_ascii=False, indent=2)

    def load_custom(self, path: str) -> int:
        """カスタム仕様をJSONから読込。追加した数を返す。"""
        fp = Path(path)
        if not fp.exists():
            return 0
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for item in data.get("custom_dampers", []):
            spec = DamperSpec.from_dict(item)
            spec.is_custom = True
            self.add_custom(spec)
            count += 1
        return count

    def apply_to_case_params(self, spec: DamperSpec) -> Dict[str, str]:
        """
        カタログのダンパー仕様をケースのdamper_paramsに変換する。

        Returns
        -------
        dict
            {field_index: value} 形式のパラメータ辞書。
        """
        return dict(spec.parameters)

    @staticmethod
    def compute_irdt_snap_elements(
        total_mass: float,
        omega_1: float,
        mu: float,
        f_ratio: float,
        zeta_d: float,
    ) -> Dict[str, float]:
        """
        iRDT / iOD パラメータから SNAP の mass / dashpot / spring 要素値を算出。

        Parameters
        ----------
        total_mass : float
            建物の総質量 (t)
        omega_1 : float
            1次固有円振動数 (rad/s)
        mu : float
            質量比 m_d / M_total
        f_ratio : float
            振動数比 ω_d / ω_1 (iODで同調なしの場合 0)
        zeta_d : float
            減衰定数

        Returns
        -------
        dict
            {"mass_d": float, "dashpot_c": float, "spring_k": float}
            SNAP に定義する要素値 (t, kN·s/m, kN/m)
        """
        m_d = mu * total_mass
        omega_d = f_ratio * omega_1 if f_ratio > 0 else 0.0
        k_b = m_d * omega_d ** 2 if omega_d > 0 else 0.0
        c_d = 2.0 * zeta_d * m_d * omega_d if omega_d > 0 else 2.0 * zeta_d * m_d * omega_1
        return {"mass_d": m_d, "dashpot_c": c_d, "spring_k": k_b}


# ---------------------------------------------------------------------------
# グローバルインスタンス
# ---------------------------------------------------------------------------

_global_catalog: Optional[DamperCatalog] = None


def get_catalog() -> DamperCatalog:
    """グローバルダンパーカタログを返します。"""
    global _global_catalog
    if _global_catalog is None:
        _global_catalog = DamperCatalog()
    return _global_catalog
