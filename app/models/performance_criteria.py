"""
app/models/performance_criteria.py
目標性能基準のデータモデル。

各応答値に対して上限値（許容値）を設定し、
解析ケースの結果が基準を満たすかどうかを判定します。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# 判定対象の応答値 (key, ラベル, 単位, デフォルト上限, 小数桁)
CRITERIA_ITEMS: List[Tuple[str, str, str, Optional[float], int]] = [
    ("max_drift",       "最大層間変形角",      "rad",   1 / 200,   6),
    ("max_acc",         "最大絶対加速度",      "m/s²",  None,      3),
    ("max_disp",        "最大相対変位",        "m",     None,      5),
    ("max_vel",         "最大相対速度",        "m/s",   None,      4),
    ("max_story_disp",  "最大層間変形",        "m",     None,      5),
    ("shear_coeff",     "せん断力係数",        "—",     None,      4),
    ("max_otm",         "最大転倒モーメント",  "kN·m",  None,      1),
]


@dataclass
class CriterionItem:
    """1つの応答値に対する目標基準。"""
    key: str
    label: str
    unit: str
    enabled: bool = False
    limit_value: Optional[float] = None
    decimals: int = 4

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CriterionItem":
        return cls(**data)


@dataclass
class PerformanceCriteria:
    """
    目標性能基準セット。

    Attributes
    ----------
    name : str
        基準セット名 (例: "大地震時", "中地震時")
    items : list of CriterionItem
        各応答値の目標基準。
    """
    name: str = "デフォルト基準"
    items: List[CriterionItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.items:
            self.items = [
                CriterionItem(
                    key=key, label=label, unit=unit,
                    enabled=(default is not None),
                    limit_value=default,
                    decimals=decimals,
                )
                for key, label, unit, default, decimals in CRITERIA_ITEMS
            ]

    def evaluate(self, result_summary: Dict[str, Any]) -> Dict[str, Optional[bool]]:
        """
        結果サマリーを判定します。

        Parameters
        ----------
        result_summary : dict
            AnalysisCase.result_summary

        Returns
        -------
        dict
            {key: True(合格) / False(不合格) / None(判定不可)}
        """
        verdicts: Dict[str, Optional[bool]] = {}
        for item in self.items:
            if not item.enabled or item.limit_value is None:
                verdicts[item.key] = None
                continue
            val = result_summary.get(item.key)
            if val is None:
                verdicts[item.key] = None
            else:
                verdicts[item.key] = (val <= item.limit_value)
        return verdicts

    def is_all_pass(self, result_summary: Dict[str, Any]) -> Optional[bool]:
        """
        全項目が合格かどうかを返します。

        Returns
        -------
        True: 全項目合格, False: 1つ以上不合格, None: 判定対象なし
        """
        verdicts = self.evaluate(result_summary)
        checked = [v for v in verdicts.values() if v is not None]
        if not checked:
            return None
        return all(checked)

    def get_summary_text(self, result_summary: Dict[str, Any]) -> str:
        """判定結果のテキストサマリーを返します。"""
        verdicts = self.evaluate(result_summary)
        lines = []
        for item in self.items:
            if not item.enabled:
                continue
            v = verdicts.get(item.key)
            val = result_summary.get(item.key)
            mark = "✓" if v is True else ("✗" if v is False else "—")
            val_str = f"{val:.{item.decimals}f}" if val is not None else "N/A"
            limit_str = f"{item.limit_value:.{item.decimals}f}" if item.limit_value is not None else "N/A"
            lines.append(f"  {mark} {item.label}: {val_str} / 基準 {limit_str}")
        if not lines:
            return "判定基準が設定されていません"
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerformanceCriteria":
        items = [CriterionItem.from_dict(d) for d in data.get("items", [])]
        return cls(name=data.get("name", "デフォルト基準"), items=items)
