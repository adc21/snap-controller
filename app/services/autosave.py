"""
app/services/autosave.py
自動保存・バックアップサービス。

一定間隔でプロジェクトの自動保存とバックアップを行い、
長時間の解析中やアプリクラッシュ時のデータ損失を防ぎます。

機能:
  - タイマーベースの自動保存（デフォルト 5 分間隔）
  - バックアップファイルのローテーション（最大 5 世代）
  - クラッシュ復旧用の一時保存ファイル管理
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.project import Project


# バックアップ保存先のサブフォルダ名
_BACKUP_DIR_NAME = ".snap-controller-backups"
# 自動保存用の一時ファイル名
_AUTOSAVE_SUFFIX = ".autosave"
# デフォルトの自動保存間隔（ミリ秒）
_DEFAULT_INTERVAL_MS = 5 * 60 * 1000  # 5 分
# 最大バックアップ世代数
_MAX_BACKUPS = 5


class AutoSaveService(QObject):
    """
    プロジェクトの自動保存・バックアップサービス。

    Parameters
    ----------
    parent : QObject, optional
        親オブジェクト。

    Signals
    -------
    auto_saved(str)
        自動保存が実行された際に保存先パスを発信。
    backup_created(str)
        バックアップが作成された際にパスを発信。
    error_occurred(str)
        保存エラーが発生した際にメッセージを発信。
    """

    auto_saved = Signal(str)
    backup_created = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._enabled: bool = True
        self._interval_ms: int = _DEFAULT_INTERVAL_MS

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_project(self, project: Optional[Project]) -> None:
        """監視対象のプロジェクトを設定します。"""
        self._project = project
        if project and self._enabled:
            self._timer.start(self._interval_ms)
        else:
            self._timer.stop()

    def set_enabled(self, enabled: bool) -> None:
        """自動保存の有効/無効を切り替えます。"""
        self._enabled = enabled
        if enabled and self._project:
            self._timer.start(self._interval_ms)
        else:
            self._timer.stop()

    def set_interval(self, minutes: int) -> None:
        """
        自動保存間隔を設定します。

        Parameters
        ----------
        minutes : int
            自動保存間隔（分）。最小 1 分。
        """
        self._interval_ms = max(1, minutes) * 60 * 1000
        if self._timer.isActive():
            self._timer.start(self._interval_ms)

    @property
    def is_enabled(self) -> bool:
        """自動保存が有効かどうか。"""
        return self._enabled

    @property
    def interval_minutes(self) -> int:
        """自動保存間隔（分）。"""
        return self._interval_ms // 60_000

    def save_now(self) -> bool:
        """
        即座に自動保存を実行します。

        Returns
        -------
        bool
            保存が成功した場合 True。
        """
        return self._do_autosave()

    def create_backup(self) -> Optional[str]:
        """
        現在のプロジェクトのバックアップを作成します。

        Returns
        -------
        str or None
            バックアップファイルパス。失敗時は None。
        """
        return self._do_backup()

    def get_autosave_path(self) -> Optional[Path]:
        """
        自動保存ファイルのパスを返します（存在するかは不問）。

        Returns
        -------
        Path or None
            自動保存ファイルのパス。プロジェクトが未保存の場合は None。
        """
        if self._project and self._project.file_path:
            return self._project.file_path.with_suffix(
                self._project.file_path.suffix + _AUTOSAVE_SUFFIX
            )
        return None

    def has_autosave(self) -> bool:
        """自動保存ファイルが存在するかどうか。"""
        path = self.get_autosave_path()
        return path is not None and path.exists()

    def clean_autosave(self) -> None:
        """自動保存ファイルを削除します（正常終了時に呼ぶ）。"""
        path = self.get_autosave_path()
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                logger.debug("自動保存ファイル削除失敗: %s", path)

    def get_backup_dir(self) -> Optional[Path]:
        """バックアップディレクトリのパスを返します。"""
        if self._project and self._project.file_path:
            return self._project.file_path.parent / _BACKUP_DIR_NAME
        return None

    def list_backups(self) -> list[dict]:
        """
        バックアップファイルの一覧を返します（新しい順）。

        Returns
        -------
        list of dict
            各辞書は {"path": str, "timestamp": str, "size_kb": float} を含む。
        """
        backup_dir = self.get_backup_dir()
        if not backup_dir or not backup_dir.exists():
            return []

        backups = []
        stem = self._project.file_path.stem if self._project and self._project.file_path else ""
        for f in sorted(backup_dir.glob(f"{stem}_backup_*"), reverse=True):
            stat = f.stat()
            backups.append({
                "path": str(f),
                "timestamp": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "size_kb": round(stat.st_size / 1024, 1),
            })
        return backups

    def restore_from_autosave(self) -> bool:
        """
        自動保存ファイルからプロジェクトを復旧します。

        Returns
        -------
        bool
            復旧に成功した場合 True。
        """
        path = self.get_autosave_path()
        if path is None or not path.exists():
            return False

        try:
            # 自動保存ファイルを本体にコピー
            original = self._project.file_path
            shutil.copy2(str(path), str(original))
            # 自動保存ファイルを削除
            path.unlink()
            return True
        except Exception as e:
            self.error_occurred.emit(f"自動保存からの復旧に失敗: {e}")
            return False

    def shutdown(self) -> None:
        """サービスを停止します。"""
        self._timer.stop()
        self.clean_autosave()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_timer(self) -> None:
        """タイマーイベントハンドラ。"""
        if self._project and self._project.modified:
            self._do_autosave()

    def _do_autosave(self) -> bool:
        """自動保存を実行します。"""
        if self._project is None:
            return False

        # プロジェクトが一度も保存されていない場合はスキップ
        if self._project.file_path is None:
            return False

        autosave_path = self.get_autosave_path()
        if autosave_path is None:
            return False

        try:
            # 一時保存ファイルとして保存（本体は上書きしない）
            import json
            data = {
                "version": "2.0",
                "name": self._project.name,
                "snap_exe_path": self._project.snap_exe_path,
                "s8i_path": self._project.s8i_path,
                "created_at": self._project.created_at,
                "updated_at": datetime.now().isoformat(),
                "cases": [c.to_dict() for c in self._project.cases],
                "criteria": self._project.criteria.to_dict(),
                "case_groups": self._project.case_groups,
            }
            autosave_path.parent.mkdir(parents=True, exist_ok=True)
            with open(autosave_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.auto_saved.emit(str(autosave_path))
            return True
        except Exception as e:
            self.error_occurred.emit(f"自動保存に失敗: {e}")
            return False

    def _do_backup(self) -> Optional[str]:
        """バックアップを作成します。"""
        if self._project is None or self._project.file_path is None:
            return None

        if not self._project.file_path.exists():
            return None

        backup_dir = self.get_backup_dir()
        if backup_dir is None:
            return None

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = self._project.file_path.stem
            suffix = self._project.file_path.suffix
            backup_name = f"{stem}_backup_{timestamp}{suffix}"
            backup_path = backup_dir / backup_name

            shutil.copy2(str(self._project.file_path), str(backup_path))

            # ローテーション: 古いバックアップを削除
            self._rotate_backups(backup_dir, stem)

            self.backup_created.emit(str(backup_path))
            return str(backup_path)
        except Exception as e:
            self.error_occurred.emit(f"バックアップの作成に失敗: {e}")
            return None

    def _rotate_backups(self, backup_dir: Path, stem: str) -> None:
        """古いバックアップを削除して世代数を維持します。"""
        backups = sorted(
            backup_dir.glob(f"{stem}_backup_*"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[_MAX_BACKUPS:]:
            try:
                old_backup.unlink()
            except OSError:
                logger.debug("古いバックアップ削除失敗: %s", old_backup)
