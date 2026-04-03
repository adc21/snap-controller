"""
controller/executor.py
複数の SNAP 解析ジョブをバッチ実行するエンジン。

SNAP 解析は通常単一実行で時間がかかるため、このモジュールは
複数ジョブの並列実行、リトライ、依存関係解決、
キャンセル・一時停止機能を提供します。
"""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from . import snap_exec as snap_exec_module
from .logger import logger

# ============================================================================
# Legacy Executor (backward compatibility)
# ============================================================================


class Executor:
    """
    レガシー互換性のための空の Executor クラス。
    新しいコードは BatchExecutor を使用してください。
    """

    def __init__(self) -> None:
        pass


# ============================================================================
# Enums
# ============================================================================


class JobStatus(Enum):
    """ジョブの実行ステータス。"""

    QUEUED = "queued"  # キューに入っている
    RUNNING = "running"  # 実行中
    COMPLETED = "completed"  # 完了
    FAILED = "failed"  # 失敗
    CANCELLED = "cancelled"  # キャンセル
    RETRYING = "retrying"  # リトライ予定
    PAUSED = "paused"  # 一時停止中


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class Job:
    """
    SNAP バッチ実行の単一ジョブを表すクラス。

    Attributes
    ----------
    id : str
        ジョブのUUID（自動生成）
    name : str
        ジョブの名前（識別用）
    input_file : str
        入力ファイルパス (.s8i)
    snap_exe : str
        SNAP.exe のフルパス
    output_dir : str
        出力ディレクトリパス
    status : JobStatus
        現在のステータス
    priority : int
        優先度（デフォルト 0）。値が小さいほど優先度が高い
    max_retries : int
        失敗時の最大リトライ回数（デフォルト 2）
    retry_count : int
        現在のリトライ回数
    depends_on : List[str]
        依存するジョブのID リスト
    created_at : datetime
        ジョブ作成日時
    started_at : Optional[datetime]
        ジョブ開始日時
    finished_at : Optional[datetime]
        ジョブ終了日時
    return_code : Optional[int]
        プロセスの終了コード
    error_message : str
        エラーメッセージ
    result_summary : dict
        実行結果の要約
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    input_file: str = ""
    snap_exe: str = ""
    output_dir: str = ""
    status: JobStatus = JobStatus.QUEUED
    priority: int = 0
    max_retries: int = 2
    retry_count: int = 0
    depends_on: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    return_code: Optional[int] = None
    error_message: str = ""
    result_summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchConfig:
    """
    バッチ実行設定。

    Attributes
    ----------
    max_workers : int
        同時実行可能なジョブ数（デフォルト 1）。
        SNAP は通常単一スレッドで重いため、1 が推奨
    retry_on_failure : bool
        失敗時にリトライするか（デフォルト True）
    max_retries : int
        ジョブあたりの最大リトライ回数（デフォルト 2）
    timeout_per_job : Optional[int]
        ジョブあたりのタイムアウト秒数（None で無制限）
    stop_on_first_failure : bool
        最初の失敗でバッチ全体を停止するか（デフォルト False）
    """

    max_workers: int = 1
    retry_on_failure: bool = True
    max_retries: int = 2
    timeout_per_job: Optional[int] = None
    stop_on_first_failure: bool = False


@dataclass
class BatchStatus:
    """
    バッチ実行の全体ステータス。

    Attributes
    ----------
    total_jobs : int
        登録されたジョブ総数
    completed : int
        完了したジョブ数
    failed : int
        失敗したジョブ数
    running : int
        実行中のジョブ数
    queued : int
        キュー中のジョブ数
    cancelled : int
        キャンセルされたジョブ数
    paused : int
        一時停止中のジョブ数
    """

    total_jobs: int = 0
    completed: int = 0
    failed: int = 0
    running: int = 0
    queued: int = 0
    cancelled: int = 0
    paused: int = 0


@dataclass
class BatchStatistics:
    """
    バッチ実行統計。

    Attributes
    ----------
    total_time : float
        総実行時間（秒）
    avg_time_per_job : float
        ジョブあたりの平均実行時間（秒）
    success_rate : float
        成功率（0.0 - 1.0）
    total_jobs_completed : int
        完了したジョブ数
    total_jobs_failed : int
        失敗したジョブ数
    started_at : Optional[datetime]
        バッチ開始日時
    finished_at : Optional[datetime]
        バッチ終了日時
    """

    total_time: float = 0.0
    avg_time_per_job: float = 0.0
    success_rate: float = 0.0
    total_jobs_completed: int = 0
    total_jobs_failed: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


# ============================================================================
# BatchExecutor
# ============================================================================


class BatchExecutor:
    """
    複数の SNAP 解析ジョブを管理・実行するバッチ実行エンジン。

    機能:
    - ジョブキュー管理（追加、削除、優先度変更）
    - 並列実行（configurable worker count）
    - 自動リトライ（失敗時に指定回数まで再実行）
    - ジョブ依存関係（あるジョブが完了してから次を実行）
    - 実行ログ・統計
    - キャンセル・一時停止・再開

    Usage::

        config = BatchConfig(max_workers=1, max_retries=2)
        executor = BatchExecutor(config)

        job1 = Job(
            name="Analysis-1",
            input_file="/path/to/model1.s8i",
            snap_exe="/path/to/Snap.exe",
            output_dir="/output/model1"
        )
        job_id = executor.add_job(job1)

        executor.start()
        # ブロッキング呼び出し。すべてのジョブが完了するまで待機
        # または start_async() を使用して非ブロッキング実行も可能

        stats = executor.get_statistics()
        print(f"成功率: {stats.success_rate:.1%}")
    """

    def __init__(self, config: Optional[BatchConfig] = None) -> None:
        """
        BatchExecutor を初期化します。

        Parameters
        ----------
        config : BatchConfig, optional
            実行設定。None の場合はデフォルト設定を使用
        """
        self.config = config or BatchConfig()
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._is_running = False
        self._is_paused = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()

        # コールバック
        self._on_job_started: Optional[Callable[[Job], None]] = None
        self._on_job_finished: Optional[Callable[[Job], None]] = None
        self._on_batch_finished: Optional[Callable[[], None]] = None
        self._on_progress: Optional[Callable[[BatchStatus], None]] = None

        # 統計情報
        self._batch_started_at: Optional[datetime] = None
        self._batch_finished_at: Optional[datetime] = None

    # ========================================================================
    # ジョブ管理 API
    # ========================================================================

    def add_job(self, job: Job) -> str:
        """
        ジョブをバッチキューに追加します。

        Parameters
        ----------
        job : Job
            追加するジョブ

        Returns
        -------
        str
            ジョブID

        Raises
        ------
        ValueError
            ジョブのフィールドが不足している場合
        """
        if not job.input_file or not job.snap_exe:
            raise ValueError(
                "ジョブには input_file と snap_exe が必須です"
            )

        with self._lock:
            if job.id in self._jobs:
                raise ValueError(f"ジョブID {job.id} は既に存在します")

            # デフォルト値を設定
            if job.name == "":
                job.name = f"Job-{job.id[:8]}"
            if job.max_retries == 0:
                job.max_retries = self.config.max_retries

            self._jobs[job.id] = job
            logger.info(f"ジョブを追加しました: {job.name} (ID: {job.id})")

        return job.id

    def add_jobs(self, jobs: List[Job]) -> List[str]:
        """
        複数のジョブをバッチキューに追加します。

        Parameters
        ----------
        jobs : List[Job]
            追加するジョブのリスト

        Returns
        -------
        List[str]
            追加されたジョブIDのリスト
        """
        job_ids = []
        for job in jobs:
            try:
                job_id = self.add_job(job)
                job_ids.append(job_id)
            except ValueError as e:
                logger.warning(f"ジョブ追加エラー: {e}")

        return job_ids

    def remove_job(self, job_id: str) -> bool:
        """
        キューからジョブを削除します。
        実行中のジョブは削除できません。

        Parameters
        ----------
        job_id : str
            削除するジョブID

        Returns
        -------
        bool
            削除に成功した場合 True
        """
        with self._lock:
            if job_id not in self._jobs:
                logger.warning(f"ジョブID {job_id} が見つかりません")
                return False

            job = self._jobs[job_id]
            if job.status == JobStatus.RUNNING:
                logger.warning(
                    f"ジョブ {job_id} は実行中のため削除できません"
                )
                return False

            del self._jobs[job_id]
            logger.info(f"ジョブを削除しました: {job_id}")
            return True

    def get_job(self, job_id: str) -> Optional[Job]:
        """
        ジョブIDからジョブオブジェクトを取得します。

        Parameters
        ----------
        job_id : str
            ジョブID

        Returns
        -------
        Job or None
            ジョブオブジェクト（見つからない場合は None）
        """
        with self._lock:
            return self._jobs.get(job_id)

    def set_job_priority(self, job_id: str, priority: int) -> bool:
        """
        ジョブの優先度を変更します。

        Parameters
        ----------
        job_id : str
            ジョブID
        priority : int
            新しい優先度（値が小さいほど優先度が高い）

        Returns
        -------
        bool
            成功した場合 True
        """
        with self._lock:
            if job_id not in self._jobs:
                return False
            job = self._jobs[job_id]
            if job.status == JobStatus.RUNNING:
                logger.warning("実行中のジョブの優先度は変更できません")
                return False
            job.priority = priority
            logger.info(f"ジョブ {job_id} の優先度を {priority} に変更しました")
            return True

    # ========================================================================
    # 実行制御 API
    # ========================================================================

    def start(self) -> None:
        """
        バッチ実行を開始します。
        すべてのジョブが完了するまでブロッキングします。
        """
        logger.info("バッチ実行を開始します")
        self._batch_started_at = datetime.now()
        self._is_running = True
        self._stop_event.clear()
        self._pause_event.clear()

        try:
            self._execute_batch()
        finally:
            self._batch_finished_at = datetime.now()
            self._is_running = False
            if self._on_batch_finished:
                self._on_batch_finished()
            logger.info("バッチ実行が完了しました")

    def start_async(self) -> threading.Thread:
        """
        バッチ実行を非同期スレッドで開始します。

        Returns
        -------
        threading.Thread
            開始済みのスレッドオブジェクト
        """
        thread = threading.Thread(target=self.start, daemon=False)
        thread.start()
        return thread

    def pause(self) -> None:
        """
        バッチ実行を一時停止します。
        現在実行中のジョブは完了までを待ちます。
        """
        logger.info("バッチ実行を一時停止します")
        self._is_paused = True
        self._pause_event.clear()

    def resume(self) -> None:
        """
        一時停止したバッチ実行を再開します。
        """
        if not self._is_paused:
            logger.warning("実行は一時停止中ではありません")
            return
        logger.info("バッチ実行を再開します")
        self._is_paused = False
        self._pause_event.set()

    def cancel_all(self) -> None:
        """
        すべての保留中とキュー中のジョブをキャンセルします。
        実行中のジョブは完了までを待ちます。
        """
        logger.info("バッチ実行をキャンセルします")
        self._stop_event.set()

        with self._lock:
            for job in self._jobs.values():
                if job.status in (
                    JobStatus.QUEUED,
                    JobStatus.RETRYING,
                    JobStatus.PAUSED,
                ):
                    job.status = JobStatus.CANCELLED
                    logger.info(f"ジョブ {job.name} をキャンセルしました")

    def cancel_job(self, job_id: str) -> bool:
        """
        特定のジョブをキャンセルします。

        Parameters
        ----------
        job_id : str
            キャンセルするジョブID

        Returns
        -------
        bool
            キャンセルに成功した場合 True
        """
        with self._lock:
            if job_id not in self._jobs:
                return False

            job = self._jobs[job_id]
            if job.status == JobStatus.RUNNING:
                logger.warning(
                    f"実行中のジョブ {job_id} はキャンセルできません"
                )
                return False

            job.status = JobStatus.CANCELLED
            logger.info(f"ジョブ {job_id} をキャンセルしました")
            return True

    # ========================================================================
    # ステータス・統計 API
    # ========================================================================

    def get_status(self) -> BatchStatus:
        """
        バッチ実行の全体ステータスを取得します。

        Returns
        -------
        BatchStatus
            ステータスオブジェクト
        """
        with self._lock:
            status = BatchStatus()
            status.total_jobs = len(self._jobs)

            for job in self._jobs.values():
                if job.status == JobStatus.COMPLETED:
                    status.completed += 1
                elif job.status == JobStatus.FAILED:
                    status.failed += 1
                elif job.status == JobStatus.RUNNING:
                    status.running += 1
                elif job.status == JobStatus.QUEUED:
                    status.queued += 1
                elif job.status == JobStatus.CANCELLED:
                    status.cancelled += 1
                elif job.status == JobStatus.PAUSED:
                    status.paused += 1

            return status

    def get_statistics(self) -> BatchStatistics:
        """
        バッチ実行統計を取得します。

        Returns
        -------
        BatchStatistics
            統計情報オブジェクト
        """
        with self._lock:
            stats = BatchStatistics()
            stats.started_at = self._batch_started_at
            stats.finished_at = self._batch_finished_at

            if self._batch_started_at and self._batch_finished_at:
                stats.total_time = (
                    self._batch_finished_at - self._batch_started_at
                ).total_seconds()

            completed_jobs = []
            failed_count = 0

            for job in self._jobs.values():
                if job.status == JobStatus.COMPLETED:
                    completed_jobs.append(job)
                elif job.status == JobStatus.FAILED:
                    failed_count += 1

            stats.total_jobs_completed = len(completed_jobs)
            stats.total_jobs_failed = failed_count

            if completed_jobs:
                total_job_time = sum(
                    (job.finished_at - job.started_at).total_seconds()
                    for job in completed_jobs
                    if job.started_at and job.finished_at
                )
                stats.avg_time_per_job = total_job_time / len(completed_jobs)

            total = len(self._jobs)
            if total > 0:
                stats.success_rate = stats.total_jobs_completed / total

            return stats

    # ========================================================================
    # コールバック設定
    # ========================================================================

    def set_on_job_started(
        self, callback: Callable[[Job], None]
    ) -> None:
        """
        ジョブ開始時のコールバックを設定します。

        Parameters
        ----------
        callback : callable
            署名: ``callback(job: Job) -> None``
        """
        self._on_job_started = callback

    def set_on_job_finished(
        self, callback: Callable[[Job], None]
    ) -> None:
        """
        ジョブ終了時のコールバックを設定します。

        Parameters
        ----------
        callback : callable
            署名: ``callback(job: Job) -> None``
        """
        self._on_job_finished = callback

    def set_on_batch_finished(self, callback: Callable[[], None]) -> None:
        """
        バッチ完了時のコールバックを設定します。

        Parameters
        ----------
        callback : callable
            署名: ``callback() -> None``
        """
        self._on_batch_finished = callback

    def set_on_progress(
        self, callback: Callable[[BatchStatus], None]
    ) -> None:
        """
        進捗更新時のコールバックを設定します。

        Parameters
        ----------
        callback : callable
            署名: ``callback(status: BatchStatus) -> None``
        """
        self._on_progress = callback

    # ========================================================================
    # Internal Implementation
    # ========================================================================

    def _execute_batch(self) -> None:
        """バッチ実行のメインロジック。"""
        with ThreadPoolExecutor(
            max_workers=self.config.max_workers
        ) as executor:
            self._executor = executor
            futures: Dict[Any, str] = {}

            # トポロジカルソート（依存関係を解決）
            sorted_job_ids = self._topological_sort()

            # ジョブをキューに追加
            for job_id in sorted_job_ids:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if not job or job.status == JobStatus.CANCELLED:
                        continue

                # 依存関係が満たされるまで待機
                self._wait_for_dependencies(job_id)

                if self._stop_event.is_set():
                    break

                # 一時停止チェック
                while self._is_paused:
                    self._pause_event.wait(timeout=0.5)

                future = executor.submit(self._execute_job, job_id)
                futures[future] = job_id

            # すべてのジョブの完了を待機
            for future in as_completed(futures):
                if self._stop_event.is_set():
                    executor.shutdown(wait=False)
                    break

                if self._on_progress:
                    self._on_progress(self.get_status())

    def _execute_job(self, job_id: str) -> None:
        """単一ジョブを実行します。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return

        if job.status == JobStatus.CANCELLED:
            return

        # ジョブ開始
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()

        if self._on_job_started:
            self._on_job_started(job)

        logger.info(f"ジョブを開始します: {job.name}")

        try:
            # SNAP を実行
            result = snap_exec_module.snap_exec(
                snap_exe=job.snap_exe,
                input_file=job.input_file,
                timeout=self.config.timeout_per_job,
            )

            job.return_code = result.returncode
            job.result_summary = {
                "stdout_lines": len(result.stdout.splitlines()),
                "output_file": str(Path(job.input_file).stem),
            }

            if result.returncode == 0:
                job.status = JobStatus.COMPLETED
                logger.info(f"ジョブが完了しました: {job.name}")
            else:
                # リトライ判定
                if (
                    self.config.retry_on_failure
                    and job.retry_count < job.max_retries
                ):
                    job.status = JobStatus.RETRYING
                    job.retry_count += 1
                    job.error_message = (
                        f"終了コード {result.returncode} "
                        f"(リトライ {job.retry_count}/{job.max_retries})"
                    )
                    logger.warning(
                        f"ジョブが失敗しました（リトライします）: "
                        f"{job.name} - {job.error_message}"
                    )

                    # リトライ：再度実行キューに追加
                    self._execute_job(job_id)
                    return
                else:
                    job.status = JobStatus.FAILED
                    job.error_message = (
                        f"終了コード {result.returncode}"
                    )
                    logger.error(
                        f"ジョブが失敗しました: {job.name} - "
                        f"{job.error_message}"
                    )

                    if self.config.stop_on_first_failure:
                        self._stop_event.set()

        except Exception as e:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            logger.error(f"ジョブ実行エラー {job.name}: {e}")

            if self.config.stop_on_first_failure:
                self._stop_event.set()

        finally:
            job.finished_at = datetime.now()

            if self._on_job_finished:
                self._on_job_finished(job)

    def _topological_sort(self) -> List[str]:
        """
        ジョブの依存関係を解決し、トポロジカルソート順を返します。

        Returns
        -------
        List[str]
            ソート済みのジョブIDリスト
        """
        with self._lock:
            jobs = self._jobs.copy()

        # 入次数を計算
        in_degree: Dict[str, int] = {job_id: 0 for job_id in jobs}

        for job in jobs.values():
            for dep_id in job.depends_on:
                if dep_id in in_degree:
                    in_degree[job.id] += 1

        # キューを初期化
        queue = [
            job_id for job_id, degree in in_degree.items() if degree == 0
        ]
        queue.sort(
            key=lambda jid: (jobs[jid].priority, jobs[jid].created_at)
        )

        result = []
        edges: Dict[str, List[str]] = {job_id: [] for job_id in jobs}

        for job in jobs.values():
            for dep_id in job.depends_on:
                if dep_id in edges:
                    edges[dep_id].append(job.id)

        while queue:
            current_job_id = queue.pop(0)
            result.append(current_job_id)

            for dependent_id in edges[current_job_id]:
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

            queue.sort(
                key=lambda jid: (jobs[jid].priority, jobs[jid].created_at)
            )

        return result

    def _wait_for_dependencies(self, job_id: str) -> None:
        """
        ジョブの依存ジョブがすべて完了するまで待機します。

        Parameters
        ----------
        job_id : str
            対象ジョブID
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            depends_on = job.depends_on.copy()

        for dep_id in depends_on:
            while True:
                with self._lock:
                    dep_job = self._jobs.get(dep_id)
                    if not dep_job:
                        break
                    if dep_job.status in (
                        JobStatus.COMPLETED,
                        JobStatus.FAILED,
                        JobStatus.CANCELLED,
                    ):
                        break

                # 短い間隔で依存ジョブの状態をチェック
                threading.Event().wait(timeout=0.1)
