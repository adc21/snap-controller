# BatchExecutor ガイド

## 概要

`BatchExecutor` は複数の SNAP 解析ジョブをバッチで実行・管理するエンジンです。以下の機能を提供します：

- **ジョブキュー管理**: ジョブの追加、削除、優先度変更
- **並列実行**: configurable worker count（通常は1）
- **自動リトライ**: 失敗時に指定回数まで再実行
- **ジョブ依存関係**: あるジョブが完了してから次を実行
- **実行制御**: 開始、一時停止、再開、キャンセル
- **統計・ログ**: 実行ログ、統計情報、コールバック

## クイックスタート

### 基本的な使用方法

```python
from controller import BatchExecutor, BatchConfig, Job

# 設定を作成
config = BatchConfig(
    max_workers=1,              # 同時実行ジョブ数
    max_retries=2,              # リトライ回数
    timeout_per_job=3600        # タイムアウト秒数
)

# 実行エンジンを作成
executor = BatchExecutor(config)

# ジョブを作成
job = Job(
    name="Analysis-01",
    input_file="/path/to/model.s8i",
    snap_exe="C:\\Program Files\\SNAP Ver.8\\Snap.exe",
    output_dir="/output/model"
)

# ジョブを追加
job_id = executor.add_job(job)

# バッチを実行（ブロッキング）
executor.start()

# 統計を取得
stats = executor.get_statistics()
print(f"成功率: {stats.success_rate:.1%}")
```

### 複数ジョブの一括追加

```python
jobs = [
    Job(
        name=f"Analysis-{i:02d}",
        input_file=f"/path/to/model_{i}.s8i",
        snap_exe=snap_exe_path,
        output_dir=f"/output/model_{i}"
    )
    for i in range(1, 11)  # 10個のジョブ
]

job_ids = executor.add_jobs(jobs)
executor.start()
```

## 依存関係の設定

複数のジョブ間に依存関係がある場合、あるジョブが完了した後に別のジョブを実行することができます。

```python
# ベース解析
base_job = Job(
    name="Base Analysis",
    input_file="/path/to/base.s8i",
    snap_exe=snap_exe_path,
    output_dir="/output/base"
)

# 変種解析（ベース解析に依存）
variant_job = Job(
    name="Variant Analysis",
    input_file="/path/to/variant.s8i",
    snap_exe=snap_exe_path,
    output_dir="/output/variant"
)

# 依存関係を設定
variant_job.depends_on = [base_job.id]

executor.add_job(base_job)
executor.add_job(variant_job)

# 実行時：base_job が完了してから variant_job が実行される
executor.start()
```

## 優先度の管理

デフォルトではジョブは追加順に実行されます。優先度を設定することで実行順序を変更できます。

```python
# ジョブ追加時に優先度を設定
job.priority = 5  # 値が小さいほど優先度が高い

# または、ジョブ追加後に変更
executor.set_job_priority(job_id, priority=3)
```

## コールバック関数

ジョブの開始・終了時や進捗更新時にコールバック関数を実行できます。

```python
def on_started(job):
    """ジョブ開始時"""
    print(f"ジョブ開始: {job.name}")

def on_finished(job):
    """ジョブ終了時"""
    if job.status.value == "completed":
        print(f"✓ {job.name} が完了しました")
    else:
        print(f"✗ {job.name} が失敗しました: {job.error_message}")

def on_batch_finished():
    """バッチ全体の完了時"""
    print("バッチ実行完了")
    stats = executor.get_statistics()
    print(f"成功率: {stats.success_rate:.1%}")

def on_progress(status):
    """進捗更新時"""
    print(f"Progress: {status.completed}/{status.total_jobs}")

executor.set_on_job_started(on_started)
executor.set_on_job_finished(on_finished)
executor.set_on_batch_finished(on_batch_finished)
executor.set_on_progress(on_progress)

executor.start()
```

## 非同期実行

`start()` はブロッキング呼び出しです。非同期実行する場合は `start_async()` を使用します。

```python
# バッチを非同期で開始
thread = executor.start_async()

# メインスレッドで他の処理を実行
while thread.is_alive():
    status = executor.get_status()
    print(f"実行中: {status.running}, 完了: {status.completed}")
    time.sleep(1)

# スレッド終了を待機
thread.join()

print("バッチ実行完了")
```

## 実行制御

### 一時停止と再開

```python
executor.start_async()

# 数秒後に一時停止
time.sleep(5)
executor.pause()

# 処理を再開
executor.resume()
```

### キャンセル

```python
# すべてのジョブをキャンセル
executor.cancel_all()

# 特定のジョブをキャンセル
executor.cancel_job(job_id)
```

## ステータス確認

### リアルタイムステータス

```python
status = executor.get_status()
print(f"Total: {status.total_jobs}")
print(f"Queued: {status.queued}")
print(f"Running: {status.running}")
print(f"Completed: {status.completed}")
print(f"Failed: {status.failed}")
print(f"Cancelled: {status.cancelled}")
```

### 統計情報

```python
stats = executor.get_statistics()
print(f"総実行時間: {stats.total_time:.1f} 秒")
print(f"ジョブあたり平均時間: {stats.avg_time_per_job:.1f} 秒")
print(f"成功率: {stats.success_rate:.1%}")
print(f"完了: {stats.total_jobs_completed}, 失敗: {stats.total_jobs_failed}")
```

### ジョブの詳細確認

```python
job = executor.get_job(job_id)
print(f"Name: {job.name}")
print(f"Status: {job.status.value}")
print(f"Return Code: {job.return_code}")
print(f"Started: {job.started_at}")
print(f"Finished: {job.finished_at}")
print(f"Error: {job.error_message}")
```

## 設定オプション

### BatchConfig

| パラメータ | デフォルト | 説明 |
|-----------|---------|------|
| `max_workers` | 1 | 同時実行ジョブ数 |
| `retry_on_failure` | True | 失敗時にリトライするか |
| `max_retries` | 2 | ジョブあたりの最大リトライ回数 |
| `timeout_per_job` | None | ジョブあたりのタイムアウト秒数 |
| `stop_on_first_failure` | False | 最初の失敗でバッチ全体を停止するか |

### Job

| パラメータ | デフォルト | 説明 |
|-----------|---------|------|
| `name` | auto-generated | ジョブの名前 |
| `input_file` | 必須 | 入力ファイルパス (.s8i) |
| `snap_exe` | 必須 | SNAP.exe のフルパス |
| `output_dir` | 必須 | 出力ディレクトリパス |
| `priority` | 0 | 優先度（小さいほど優先度が高い） |
| `max_retries` | 2 | このジョブのリトライ回数 |
| `depends_on` | [] | 依存するジョブのIDリスト |

## ステータス値

### JobStatus

- `QUEUED`: キューに入っている
- `RUNNING`: 実行中
- `COMPLETED`: 完了（正常終了）
- `FAILED`: 失敗（すべてのリトライが失敗）
- `CANCELLED`: キャンセルされた
- `RETRYING`: リトライ予定
- `PAUSED`: 一時停止中

## エラーハンドリング

```python
# ジョブ追加時の検証
try:
    job_id = executor.add_job(job)
except ValueError as e:
    print(f"ジョブ追加エラー: {e}")

# 実行中のエラーはコールバックで処理
def on_finished(job):
    if job.status.value == "failed":
        print(f"エラー: {job.error_message}")
        print(f"リトライ回数: {job.retry_count}/{job.max_retries}")
```

## 実装例：フル機能

```python
from controller import BatchExecutor, BatchConfig, Job, JobStatus
from datetime import datetime
import time

def main():
    # 設定
    config = BatchConfig(
        max_workers=1,
        retry_on_failure=True,
        max_retries=2,
        timeout_per_job=3600,
        stop_on_first_failure=False
    )
    
    executor = BatchExecutor(config)
    
    # コールバック定義
    def on_started(job):
        print(f"[START] {job.name}")
    
    def on_finished(job):
        status = job.status.value.upper()
        elapsed = ""
        if job.started_at and job.finished_at:
            elapsed = f" ({(job.finished_at - job.started_at).total_seconds():.1f}s)"
        print(f"[{status}] {job.name}{elapsed}")
        if job.error_message:
            print(f"  → {job.error_message}")
    
    def on_progress(status):
        total = status.total_jobs
        comp = status.completed
        fail = status.failed
        run = status.running
        que = status.queued
        rate = comp / total * 100 if total > 0 else 0
        print(f"Progress: {comp}/{total} ({rate:.0f}%) | "
              f"Running: {run} | Failed: {fail}")
    
    def on_batch_finished():
        stats = executor.get_statistics()
        print("\n=== Batch Summary ===")
        print(f"Total Time: {stats.total_time:.1f}s")
        print(f"Avg per Job: {stats.avg_time_per_job:.1f}s")
        print(f"Success Rate: {stats.success_rate:.1%}")
    
    # コールバック設定
    executor.set_on_job_started(on_started)
    executor.set_on_job_finished(on_finished)
    executor.set_on_progress(on_progress)
    executor.set_on_batch_finished(on_batch_finished)
    
    # ジョブ作成
    snap_exe = "C:\\Program Files\\SNAP Ver.8\\Snap.exe"
    base_path = "/path/to/models"
    
    jobs = []
    for i in range(1, 6):
        job = Job(
            name=f"Model-{i:02d}",
            input_file=f"{base_path}/model_{i}.s8i",
            snap_exe=snap_exe,
            output_dir=f"/output/model_{i}",
            priority=i
        )
        jobs.append(job)
    
    # ジョブ追加と実行
    executor.add_jobs(jobs)
    executor.start()
    
    # 結果確認
    for job_id in [j.id for j in jobs]:
        job = executor.get_job(job_id)
        print(f"{job.name}: {job.status.value}")

if __name__ == "__main__":
    main()
```

## トラブルシューティング

### ジョブが実行されない

- `input_file` と `snap_exe` が設定されているか確認
- 依存関係のジョブが失敗していないか確認
- `executor.get_status()` でステータスを確認

### リトライが機能しない

- `config.retry_on_failure = True` に設定されているか確認
- `job.max_retries > 0` に設定されているか確認

### パフォーマンスが低い

- `max_workers` が大きすぎないか確認（SNAP は CPU 集約的）
- `timeout_per_job` が適切に設定されているか確認
- ディスク I/O のボトルネックがないか確認

## API リファレンス

詳細は `controller/executor.py` のドキュメンテーション文字列を参照してください。

```python
from controller import (
    BatchExecutor,      # メインエンジンクラス
    BatchConfig,        # 実行設定
    Job,                # ジョブ定義
    JobStatus,          # ジョブステータス Enum
    BatchStatus,        # バッチステータス
    BatchStatistics,    # 実行統計
)
```

