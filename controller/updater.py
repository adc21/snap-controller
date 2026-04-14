"""
controller/updater.py
SNAP 入力ファイル (.s8i) のパラメータを読み書きするクラス。

SNAP の .s8i ファイルは行ベースのテキスト形式です。
各行は「キー: 値」または固定列幅の数値データで構成されます。
Updater はキーワード検索によってパラメータを特定し上書きします。
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Updater:
    """
    .s8i ファイルのパラメータ更新クラス。

    Usage::

        upd = Updater("path/to/model.s8i")
        upd.set_param("DAMPING", 0.05)
        upd.set_param("DT", 0.01)
        upd.write("path/to/output.s8i")
    """

    def __init__(self, filepath: str) -> None:
        self.source_path = Path(filepath)
        self._lines: List[str] = []
        self._pending: Dict[str, Any] = {}

        if self.source_path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_param(self, keyword: str, value: Any) -> None:
        """
        キーワードに対応するパラメータを設定します。
        write() を呼ぶまで実際のファイルは変更されません。

        Parameters
        ----------
        keyword : str
            .s8i 内で検索するキーワード文字列（大文字小文字無視）。
        value : Any
            置換後の値。
        """
        self._pending[keyword.upper()] = value

    def set_params(self, params: Dict[str, Any]) -> None:
        """複数パラメータをまとめて設定します。"""
        for key, val in params.items():
            self.set_param(key, val)

    def get_param(self, keyword: str) -> Optional[str]:
        """
        ファイル内のキーワードに対応する現在の値を返します。
        見つからない場合は None を返します。
        """
        pattern = re.compile(
            rf"^\s*{re.escape(keyword)}\s*=?\s*(.+?)$", re.IGNORECASE
        )
        for line in self._lines:
            m = pattern.match(line)
            if m:
                return m.group(1).strip()
        return None

    def write(self, output_path: Optional[str] = None) -> Path:
        """
        変更を適用してファイルを書き出します。

        Parameters
        ----------
        output_path : str, optional
            書き出し先のパス。省略時はソースファイルを上書きします。

        Returns
        -------
        Path
            書き出したファイルのパス。
        """
        dest = Path(output_path) if output_path else self.source_path

        updated_lines = list(self._lines)
        applied_keys: set = set()

        for i, line in enumerate(updated_lines):
            for keyword, value in self._pending.items():
                if keyword not in applied_keys:
                    pattern = re.compile(
                        rf"^(\s*{re.escape(keyword)}\s*=?\s*)",
                        re.IGNORECASE,
                    )
                    m = pattern.match(line)
                    if m:
                        updated_lines[i] = f"{m.group(1)}{value}\n"
                        applied_keys.add(keyword)
                        break

        # 未適用のパラメータを末尾に追記
        for keyword, value in self._pending.items():
            if keyword not in applied_keys:
                updated_lines.append(f"{keyword} = {value}\n")

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="shift_jis", errors="replace") as f:
            f.writelines(updated_lines)

        self._pending.clear()
        return dest

    def copy_to(self, dest_path: str) -> "Updater":
        """
        ソースファイルを別パスにコピーし、そのパスを操作する新たな
        Updater を返します。パラメトリック解析の準備に便利です。
        """
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source_path, dest)
        new_upd = Updater(str(dest))
        new_upd._pending = dict(self._pending)
        return new_upd

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """ファイルを読み込んで行リストに格納します。"""
        for enc in ("shift_jis", "utf-8", "cp932"):
            try:
                with open(self.source_path, "r", encoding=enc, errors="replace") as f:
                    self._lines = f.readlines()
                return
            except Exception:
                logger.debug("エンコード %s で読み込み失敗: %s", enc, self.source_path)
                continue
        raise IOError(f"ファイルを読み込めませんでした: {self.source_path}")
