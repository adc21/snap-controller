"""
app/models/case_template.py
ケーステンプレートのデータモデル。

よく使うダンパー構成やパラメータ設定をテンプレートとして保存・読込し、
繰り返し行う解析パターンの効率化を支援します。

テンプレートは JSON ファイル (.snaptemplate) として個別に保存され、
アプリのデフォルトテンプレートディレクトリまたは任意の場所で管理できます。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# デフォルトテンプレート保存先
_TEMPLATE_DIR_NAME = "snap-controller-templates"


@dataclass
class CaseTemplate:
    """
    ケーステンプレート。

    Attributes
    ----------
    name : str
        テンプレート名。
    description : str
        テンプレートの説明。
    category : str
        カテゴリ（例: "免震", "制振", "共通"）。
    parameters : dict
        .s8i パラメータ辞書テンプレート。
    damper_params : dict
        ダンパーパラメータ辞書テンプレート。
    tags : list of str
        検索用タグ。
    created_at : str
        作成日時 (ISO 8601)。
    updated_at : str
        更新日時 (ISO 8601)。
    """

    name: str = "新規テンプレート"
    description: str = ""
    category: str = "共通"
    parameters: Dict[str, Any] = field(default_factory=dict)
    damper_params: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """JSON 保存用辞書に変換します。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CaseTemplate":
        """辞書から CaseTemplate を復元します。"""
        data = dict(data)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: str) -> Path:
        """
        テンプレートを .snaptemplate ファイルに保存します。

        Parameters
        ----------
        path : str
            保存先のファイルパス。

        Returns
        -------
        Path
            保存されたファイルパス。
        """
        self.updated_at = datetime.now().isoformat()
        fp = Path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return fp

    @classmethod
    def load(cls, path: str) -> "CaseTemplate":
        """
        .snaptemplate ファイルからテンプレートを読み込みます。

        Parameters
        ----------
        path : str
            ファイルパス。

        Returns
        -------
        CaseTemplate
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ======================================================================
# テンプレートマネージャ
# ======================================================================


class TemplateManager:
    """
    テンプレートの一元管理を行うマネージャ。

    テンプレートディレクトリ内のすべてのテンプレートファイルを
    スキャンして一覧管理します。

    Parameters
    ----------
    template_dir : str or Path, optional
        テンプレート保存先ディレクトリ。
        省略時はユーザーのホームディレクトリ配下のデフォルトディレクトリ。
    """

    def __init__(self, template_dir: Optional[str] = None) -> None:
        if template_dir:
            self._dir = Path(template_dir)
        else:
            self._dir = Path.home() / _TEMPLATE_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)
        self._templates: Dict[str, CaseTemplate] = {}
        self._file_map: Dict[str, Path] = {}
        self.refresh()

    @property
    def template_dir(self) -> Path:
        """テンプレートディレクトリのパス。"""
        return self._dir

    def refresh(self) -> None:
        """テンプレートディレクトリを再スキャンします。"""
        self._templates.clear()
        self._file_map.clear()
        for fp in self._dir.glob("*.snaptemplate"):
            try:
                tpl = CaseTemplate.load(str(fp))
                key = fp.stem
                self._templates[key] = tpl
                self._file_map[key] = fp
            except Exception:
                logger.debug("テンプレート読込失敗: %s", fp)
                continue

    def list_all(self) -> List[CaseTemplate]:
        """全テンプレートのリストを返します（名前順）。"""
        return sorted(self._templates.values(), key=lambda t: t.name)

    def get_by_category(self, category: str) -> List[CaseTemplate]:
        """カテゴリでフィルタしたテンプレートリストを返します。"""
        return [t for t in self._templates.values() if t.category == category]

    def get_categories(self) -> List[str]:
        """全カテゴリ名のリストを返します。"""
        cats = set(t.category for t in self._templates.values())
        return sorted(cats)

    def search(self, keyword: str) -> List[CaseTemplate]:
        """名前・説明・タグからキーワード検索します。"""
        kw = keyword.lower()
        results = []
        for t in self._templates.values():
            if (kw in t.name.lower() or
                kw in t.description.lower() or
                any(kw in tag.lower() for tag in t.tags)):
                results.append(t)
        return results

    def add(self, template: CaseTemplate) -> Path:
        """
        テンプレートを保存・追加します。

        Parameters
        ----------
        template : CaseTemplate

        Returns
        -------
        Path
            保存されたファイルパス。
        """
        # ファイル名はテンプレート名から生成（安全な文字に変換）
        safe_name = "".join(
            c if c.isalnum() or c in "-_ " else "_"
            for c in template.name
        ).strip().replace(" ", "_")
        if not safe_name:
            safe_name = "template"
        fp = self._dir / f"{safe_name}.snaptemplate"
        # 同名ファイルが存在する場合はサフィックス追加
        counter = 1
        while fp.exists():
            fp = self._dir / f"{safe_name}_{counter}.snaptemplate"
            counter += 1
        template.save(str(fp))
        key = fp.stem
        self._templates[key] = template
        self._file_map[key] = fp
        return fp

    def update(self, template: CaseTemplate) -> Optional[Path]:
        """
        既存テンプレートを更新します（名前で検索して上書き保存）。

        Returns
        -------
        Path or None
            更新に成功した場合のファイルパス。
        """
        for key, t in self._templates.items():
            if t.name == template.name:
                fp = self._file_map[key]
                template.save(str(fp))
                self._templates[key] = template
                return fp
        return None

    def remove(self, template_name: str) -> bool:
        """
        テンプレートを削除します。

        Parameters
        ----------
        template_name : str
            削除するテンプレート名。

        Returns
        -------
        bool
            削除に成功した場合 True。
        """
        for key, t in list(self._templates.items()):
            if t.name == template_name:
                fp = self._file_map[key]
                try:
                    fp.unlink()
                except OSError:
                    return False
                del self._templates[key]
                del self._file_map[key]
                return True
        return False

    @staticmethod
    def from_case(case, name: str = "", description: str = "",
                  category: str = "共通") -> CaseTemplate:
        """
        AnalysisCase からテンプレートを生成します。

        Parameters
        ----------
        case : AnalysisCase
            元にするケース。
        name : str
            テンプレート名。省略時はケース名から生成。
        description : str
            テンプレートの説明。
        category : str
            カテゴリ。

        Returns
        -------
        CaseTemplate
        """
        return CaseTemplate(
            name=name or f"{case.name} テンプレート",
            description=description or f"ケース「{case.name}」から作成",
            category=category,
            parameters=dict(case.parameters),
            damper_params=dict(case.damper_params),
        )


# ======================================================================
# ビルトインテンプレート
# ======================================================================

def get_builtin_templates() -> List[CaseTemplate]:
    """
    ビルトイン（組み込み）テンプレートのリストを返します。

    免震・制振設計でよく使われる代表的な構成を提供します。
    """
    return [
        CaseTemplate(
            name="オイルダンパー標準構成",
            description="線形オイルダンパーの標準的なパラメータ設定",
            category="制振",
            parameters={},
            damper_params={
                "type": "油圧ダンパー（線形）",
                "Cd": 500.0,
                "alpha": 1.0,
            },
            tags=["オイルダンパー", "線形", "制振"],
        ),
        CaseTemplate(
            name="オイルダンパー バイリニア構成",
            description="バイリニア型オイルダンパーの標準パラメータ",
            category="制振",
            parameters={},
            damper_params={
                "type": "油圧ダンパー（バイリニア）",
                "Cd1": 300.0,
                "Cd2": 100.0,
                "Vr": 0.05,
                "alpha": 0.3,
            },
            tags=["オイルダンパー", "バイリニア", "制振"],
        ),
        CaseTemplate(
            name="鋼材ダンパー標準構成",
            description="履歴型鋼材ダンパーの標準パラメータ",
            category="制振",
            parameters={},
            damper_params={
                "type": "鋼材ダンパー",
                "Fy": 200.0,
                "Ke": 50000.0,
                "Kp_ratio": 0.01,
            },
            tags=["鋼材ダンパー", "履歴型", "制振"],
        ),
        CaseTemplate(
            name="粘性壁ダンパー標準構成",
            description="粘性体壁ダンパーの標準パラメータ",
            category="制振",
            parameters={},
            damper_params={
                "type": "粘性壁",
                "Cd": 800.0,
                "Ke": 100000.0,
            },
            tags=["粘性壁", "制振"],
        ),
        CaseTemplate(
            name="免震（積層ゴム + ダンパー）",
            description="免震構造の基本構成: 積層ゴム支承 + オイルダンパー",
            category="免震",
            parameters={},
            damper_params={
                "type": "免震（積層ゴム＋減衰）",
                "Kb": 2000.0,
                "Cd": 400.0,
                "alpha": 1.0,
            },
            tags=["免震", "積層ゴム", "オイルダンパー"],
        ),
        CaseTemplate(
            name="免震（すべり支承 + ダンパー）",
            description="すべり支承による免震構造とオイルダンパーの組み合わせ",
            category="免震",
            parameters={},
            damper_params={
                "type": "免震（すべり支承＋減衰）",
                "mu": 0.05,
                "Cd": 300.0,
            },
            tags=["免震", "すべり支承", "摩擦"],
        ),
    ]
