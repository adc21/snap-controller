"""
run_cli.py
snap-controller CLI — GUIなしでプロジェクトの解析やモック実行を行うコマンドラインツール。

使い方:
    # プロジェクト情報の表示
    python run_cli.py info project.snapproj

    # 全ケースをモック実行して結果をCSV出力
    python run_cli.py run-mock project.snapproj --output results.csv

    # 全ケースをSNAP実行
    python run_cli.py run project.snapproj --snap-exe /path/to/SNAP.exe

    # 新規プロジェクトを作成
    python run_cli.py new-project output.snapproj --s8i model.s8i --snap-exe /path/to/SNAP.exe

    # 最適化をCLIで実行
    python run_cli.py optimize project.snapproj --method bayesian --iterations 50 --objective max_drift
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# PySide6 がロードできない環境（CLIのみ利用）では軽量モックを注入
# ---------------------------------------------------------------------------
def _ensure_qt_available():
    """PySide6 のインポートを試み、失敗したらモックを挿入します。"""
    try:
        from PySide6.QtCore import QObject  # noqa: F401
    except (ImportError, OSError):
        from unittest.mock import MagicMock

        class _FakeSignal:
            def __init__(self, *a, **kw): pass
            def emit(self, *a, **kw): pass
            def connect(self, *a, **kw): pass

        _qc = MagicMock()
        _qc.Signal = _FakeSignal
        _qc.QObject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
        _qc.QThread = type("QThread", (), {
            "__init__": lambda self, *a, **kw: None,
            "start": lambda self: None,
            "isRunning": lambda self: False,
            "wait": lambda self, *a: None,
            "terminate": lambda self: None,
        })
        _qc.QSettings = MagicMock
        _qc.Qt = MagicMock()
        _qc.QTimer = MagicMock

        sys.modules.setdefault("PySide6", MagicMock())
        sys.modules["PySide6.QtCore"] = _qc
        sys.modules.setdefault("PySide6.QtWidgets", MagicMock())
        sys.modules.setdefault("PySide6.QtGui", MagicMock())

_ensure_qt_available()


def _add_project_path_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project", help=".snapproj ファイルのパス")


def _load_project(path: str):
    """プロジェクトファイルを読み込みます。"""
    from app.models import Project
    return Project.load(path)


# ---------------------------------------------------------------------------
# info サブコマンド
# ---------------------------------------------------------------------------

def cmd_info(args: argparse.Namespace) -> int:
    """プロジェクト情報を表示します。"""
    proj = _load_project(args.project)
    print(f"プロジェクト名: {proj.name}")
    print(f"SNAP.exe: {proj.snap_exe_path or '(未設定)'}")
    print(f"入力ファイル: {proj.s8i_path or '(未設定)'}")
    print(f"ケース数: {len(proj.cases)}")
    print(f"完了済み: {len(proj.get_completed_cases())}")
    print(f"作成日時: {proj.created_at}")
    print(f"更新日時: {proj.updated_at}")

    if proj.cases:
        print("\n--- ケース一覧 ---")
        for i, case in enumerate(proj.cases, 1):
            print(f"  {i}. {case.name} [{case.get_status_label()}]")
            if case.result_summary:
                drift = case.result_summary.get("max_drift")
                acc = case.result_summary.get("max_acc")
                if drift is not None:
                    print(f"     最大層間変形角: {drift:.6f}")
                if acc is not None:
                    print(f"     最大加速度: {acc:.4f}")

    return 0


# ---------------------------------------------------------------------------
# run-mock サブコマンド
# ---------------------------------------------------------------------------

def cmd_run_mock(args: argparse.Namespace) -> int:
    """全ケースをモックデータで実行し、結果をCSVに出力します。"""
    proj = _load_project(args.project)
    floors = args.floors

    if not proj.cases:
        print("[WARN] ケースがありません。")
        return 1

    from controller.result import Result
    import random

    print(f"モック実行: {len(proj.cases)} ケース（{floors}階）")
    for i, case in enumerate(proj.cases, 1):
        from app.models import AnalysisCaseStatus
        case.status = AnalysisCaseStatus.RUNNING
        scale = round(0.7 + random.uniform(0.0, 0.6), 2)
        res = Result.from_mock(floors=floors)
        # スケールを適用
        for attr in ("max_disp", "max_vel", "max_acc",
                     "max_story_disp", "max_story_drift",
                     "shear_coeff", "max_otm"):
            original = getattr(res, attr)
            setattr(res, attr, {k: round(v * scale, 6) for k, v in original.items()})

        # result_summary に格納
        summary = {}
        if res.max_story_drift:
            summary["max_drift"] = max(res.max_story_drift.values())
        if res.max_acc:
            summary["max_acc"] = max(res.max_acc.values())
        if res.max_disp:
            summary["max_disp"] = max(res.max_disp.values())
        if res.max_vel:
            summary["max_vel"] = max(res.max_vel.values())
        if res.shear_coeff:
            summary["max_shear"] = max(res.shear_coeff.values())
        if res.max_otm:
            summary["max_otm"] = max(res.max_otm.values())
        summary["result_data"] = res.get_all()
        case.result_summary = summary
        case.status = AnalysisCaseStatus.COMPLETED
        case.return_code = 0
        print(f"  [{i}/{len(proj.cases)}] {case.name} (scale={scale:.2f}) ... 完了")

    # CSV出力
    if args.output:
        _export_csv(proj, args.output)
        print(f"\n結果を {args.output} に出力しました。")

    # プロジェクト保存
    if args.save:
        proj.save()
        print(f"プロジェクトを保存しました: {proj.file_path}")

    return 0


# ---------------------------------------------------------------------------
# run サブコマンド
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """全ケースをSNAPで実行します。"""
    proj = _load_project(args.project)
    snap_exe = args.snap_exe or proj.snap_exe_path

    if not snap_exe:
        print("[ERROR] SNAP.exe のパスが指定されていません。")
        print("  --snap-exe オプションで指定するか、プロジェクトに設定してください。")
        return 1

    if not Path(snap_exe).exists():
        print(f"[ERROR] SNAP.exe が見つかりません: {snap_exe}")
        return 1

    if not proj.cases:
        print("[WARN] ケースがありません。")
        return 1

    from app.models import AnalysisCaseStatus
    from app.models.s8i_parser import parse_s8i
    from controller.snap_exec import snap_exec
    from controller.result import Result
    import shutil
    import tempfile

    print(f"SNAP実行: {len(proj.cases)} ケース")
    print(f"SNAP.exe: {snap_exe}")
    print()

    for i, case in enumerate(proj.cases, 1):
        if case.status == AnalysisCaseStatus.COMPLETED and not args.force:
            print(f"  [{i}/{len(proj.cases)}] {case.name} ... スキップ（完了済み）")
            continue

        model_path = case.model_path or proj.s8i_path
        if not model_path or not Path(model_path).exists():
            print(f"  [{i}/{len(proj.cases)}] {case.name} ... スキップ（入力ファイルなし）")
            continue

        case.status = AnalysisCaseStatus.RUNNING
        print(f"  [{i}/{len(proj.cases)}] {case.name} ... 実行中")

        try:
            src = Path(model_path)
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_input = Path(tmp_dir) / src.name

                # パラメータ上書き
                has_overrides = bool(case.damper_params) or bool(
                    case.parameters.get("_rd_overrides")
                )
                if has_overrides:
                    model = parse_s8i(str(src))
                    if case.damper_params:
                        for def_name, overrides in case.damper_params.items():
                            ddef = model.get_damper_def(def_name)
                            if ddef:
                                for idx_str, new_val in overrides.items():
                                    idx = int(idx_str)
                                    if 0 <= idx < len(ddef.values):
                                        ddef.values[idx] = str(new_val)
                    model.write(str(tmp_input))
                else:
                    shutil.copy2(src, tmp_input)

                # 出力ディレクトリ
                out_dir = Path(case.output_dir) if case.output_dir else src.parent / case.name
                out_dir.mkdir(parents=True, exist_ok=True)
                case.result_path = str(out_dir)

                # SNAP 実行
                result = snap_exec(
                    snap_exe=snap_exe,
                    input_file=str(tmp_input),
                    stdout_callback=lambda line: None,
                )
                case.return_code = result.returncode

                # 出力ファイルをコピー
                for f in Path(tmp_dir).iterdir():
                    if f.suffix.lower() in (".out", ".txt", ".res", ".log"):
                        shutil.copy2(f, out_dir / f.name)

            # 結果パース
            res = Result(str(out_dir))
            summary = {}
            if res.max_story_drift:
                summary["max_drift"] = max(res.max_story_drift.values())
            if res.max_acc:
                summary["max_acc"] = max(res.max_acc.values())
            if res.max_disp:
                summary["max_disp"] = max(res.max_disp.values())
            if res.max_vel:
                summary["max_vel"] = max(res.max_vel.values())
            if res.shear_coeff:
                summary["max_shear"] = max(res.shear_coeff.values())
            if res.max_otm:
                summary["max_otm"] = max(res.max_otm.values())
            summary["result_data"] = res.get_all()
            case.result_summary = summary

            case.status = AnalysisCaseStatus.COMPLETED
            print(f"    完了 (終了コード {case.return_code})")

        except Exception as e:
            case.status = AnalysisCaseStatus.ERROR
            print(f"    エラー: {e}")

    # CSV出力
    if args.output:
        _export_csv(proj, args.output)
        print(f"\n結果を {args.output} に出力しました。")

    # プロジェクト保存
    proj.save()
    print(f"プロジェクトを保存しました: {proj.file_path}")

    return 0


# ---------------------------------------------------------------------------
# new-project サブコマンド
# ---------------------------------------------------------------------------

def cmd_new_project(args: argparse.Namespace) -> int:
    """新規プロジェクトを作成します。"""
    from app.models import Project, AnalysisCase

    proj = Project(name=args.name or Path(args.output).stem)
    if args.snap_exe:
        proj.snap_exe_path = args.snap_exe
    if args.s8i:
        proj.load_s8i(args.s8i)

    # ケースを自動追加
    for i in range(args.num_cases):
        case = proj.add_case()
        case.name = f"Case {i + 1}"

    proj.save(args.output)
    print(f"プロジェクトを作成しました: {args.output}")
    print(f"  名前: {proj.name}")
    print(f"  ケース数: {len(proj.cases)}")
    return 0


# ---------------------------------------------------------------------------
# optimize サブコマンド
# ---------------------------------------------------------------------------

def cmd_optimize(args: argparse.Namespace) -> int:
    """CLI上で最適化を実行します。"""
    proj = _load_project(args.project)

    if not proj.cases:
        print("[WARN] ケースがありません。")
        return 1

    from app.services.optimizer import (
        OptimizationConfig,
        ParameterRange,
        _OptimizationWorker,
        _mock_evaluate,
    )

    # パラメータ範囲のデフォルト設定（ダンパー共通パラメータ）
    parameters = [
        ParameterRange(key="Cd", label="減衰係数", min_val=100, max_val=2000, step=0),
        ParameterRange(key="alpha", label="速度指数", min_val=0.1, max_val=1.0, step=0),
    ]

    config = OptimizationConfig(
        objective_key=args.objective,
        objective_label=args.objective,
        parameters=parameters,
        method=args.method,
        max_iterations=args.iterations,
    )

    # ベースケースの result_summary を取得
    base_summary = {}
    completed = proj.get_completed_cases()
    if completed:
        base_summary = completed[0].result_summary

    def evaluate(params):
        return _mock_evaluate(params, base_summary, config.objective_key)

    print(f"最適化開始: {args.method} ({args.iterations} 回)")
    print(f"目的関数: {args.objective} を最小化")
    print()

    # 同期実行（QThread 不使用）
    worker = _OptimizationWorker(config, evaluate)

    start_time = time.time()
    if config.method == "grid":
        result = worker._run_grid_search(config)
    elif config.method == "random":
        result = worker._run_random_search(config)
    elif config.method == "bayesian":
        result = worker._run_bayesian_search(config)
    else:
        print(f"[ERROR] 未対応の手法: {config.method}")
        return 1

    result.elapsed_sec = time.time() - start_time
    result.config = config

    # 結果表示
    print(result.get_summary_text())

    # 結果をJSON出力
    if args.output:
        output_data = {
            "method": args.method,
            "iterations": len(result.all_candidates),
            "elapsed_sec": result.elapsed_sec,
            "best": None,
            "top_10": [],
        }
        if result.best:
            output_data["best"] = {
                "params": result.best.params,
                "objective_value": result.best.objective_value,
                "response_values": result.best.response_values,
            }
        for c in result.ranked_candidates[:10]:
            output_data["top_10"].append({
                "params": c.params,
                "objective_value": c.objective_value,
            })
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n結果を {args.output} に保存しました。")

    return 0


# ---------------------------------------------------------------------------
# CSV エクスポート
# ---------------------------------------------------------------------------

def _export_csv(proj, output_path: str) -> None:
    """完了済みケースの結果をCSVに出力します。"""
    from app.models import AnalysisCaseStatus

    completed = [c for c in proj.cases if c.status == AnalysisCaseStatus.COMPLETED]
    if not completed:
        print("[WARN] 完了済みケースがありません。CSV出力をスキップします。")
        return

    headers = [
        "ケース名", "max_drift", "max_acc", "max_disp",
        "max_vel", "max_shear", "max_otm",
    ]

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for case in completed:
            s = case.result_summary
            writer.writerow([
                case.name,
                s.get("max_drift", ""),
                s.get("max_acc", ""),
                s.get("max_disp", ""),
                s.get("max_vel", ""),
                s.get("max_shear", ""),
                s.get("max_otm", ""),
            ])


# ---------------------------------------------------------------------------
# メインエントリーポイント
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="snap-controller",
        description="snap-controller CLI — SNAP解析プロジェクトの管理・実行ツール",
    )
    subparsers = parser.add_subparsers(dest="command", help="サブコマンド")

    # info
    p_info = subparsers.add_parser("info", help="プロジェクト情報を表示")
    _add_project_path_arg(p_info)
    p_info.set_defaults(func=cmd_info)

    # run-mock
    p_mock = subparsers.add_parser("run-mock", help="全ケースをモックデータで実行")
    _add_project_path_arg(p_mock)
    p_mock.add_argument("--output", "-o", help="結果CSV出力先")
    p_mock.add_argument("--floors", type=int, default=5, help="モック階数 (default: 5)")
    p_mock.add_argument("--save", action="store_true", help="結果をプロジェクトに保存")
    p_mock.set_defaults(func=cmd_run_mock)

    # run
    p_run = subparsers.add_parser("run", help="全ケースをSNAPで実行")
    _add_project_path_arg(p_run)
    p_run.add_argument("--snap-exe", help="SNAP.exe のパス")
    p_run.add_argument("--output", "-o", help="結果CSV出力先")
    p_run.add_argument("--force", action="store_true", help="完了済みケースも再実行")
    p_run.set_defaults(func=cmd_run)

    # new-project
    p_new = subparsers.add_parser("new-project", help="新規プロジェクトを作成")
    p_new.add_argument("output", help="出力先 .snapproj パス")
    p_new.add_argument("--name", help="プロジェクト名")
    p_new.add_argument("--s8i", help=".s8i ファイルパス")
    p_new.add_argument("--snap-exe", help="SNAP.exe パス")
    p_new.add_argument("--num-cases", type=int, default=1, help="初期ケース数 (default: 1)")
    p_new.set_defaults(func=cmd_new_project)

    # optimize
    p_opt = subparsers.add_parser("optimize", help="ダンパーパラメータの最適化")
    _add_project_path_arg(p_opt)
    p_opt.add_argument("--method", choices=["grid", "random", "bayesian"], default="bayesian",
                       help="探索手法 (default: bayesian)")
    p_opt.add_argument("--iterations", type=int, default=50, help="反復数 (default: 50)")
    p_opt.add_argument("--objective", default="max_drift",
                       help="最小化する応答値 (default: max_drift)")
    p_opt.add_argument("--output", "-o", help="結果JSON出力先")
    p_opt.set_defaults(func=cmd_optimize)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
