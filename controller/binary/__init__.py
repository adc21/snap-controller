"""
controller.binary
=================

SNAP のバイナリ出力ファイル（.hst 時刻歴 / .xbn 最大値・固有値 / .stp 構造定義）
を読み込むためのパーサー群。

SNAP は解析完了後、以下のバイナリファイルを解析フォルダへ出力します:

  Floor.hst / Story.hst / Node.hst / Column.hst / Beam.hst / Spring.hst
  Rigid.hst / Damper.hst / Energy.hst
      → 時刻歴応答データ（各ステップごとの全レコード値）

  Floor.xbn / Story.xbn / Node.xbn / Column.xbn / Beam.xbn / Spring.xbn
  Rigid.xbn / Damper.xbn / Period.xbn / MDFloor.xbn / MDNode.xbn / Trus.xbn
      → 最大値・固有値などの集約結果

  Floor.stp / Story.stp / Node.stp / ...（各 .hst/.xbn に対応する構造定義）
      → レコード名（"21F", "R5" 等）など付随メタ情報

本パッケージは実サンプル (example_3D/D4) の観測結果からフォーマットを
リバースエンジニアリングしており、下記を公開 API として提供します:

  - HstReader  : .hst ファイルから時刻歴配列を取得
  - XbnReader  : .xbn ファイルから集約値配列を取得
  - StpReader  : .stp ファイルからレコード名一覧を取得
  - PeriodXbnReader : Period.xbn 専用（マルチモード対応）
  - SnapResultLoader : 結果フォルダ一括ロード + 便利アクセサ

Notes
-----
リバースエンジニアリングベースのため、未知のレコードフォーマットや特殊な
SNAP バージョンでは正しく読み取れない可能性があります。その場合でも、
ヘッダ情報（num_steps / step_size / num_records）は取得できるので、
デバッグ用のロウダンプ機能を併せて提供しています。
"""

from .stp_reader import StpReader
from .hst_reader import HstReader, HstHeader
from .xbn_reader import XbnReader
from .period_xbn_reader import PeriodXbnReader, ModeInfo
from .result_loader import SnapResultLoader, BinaryCategory
from .mode_analysis import estimate_mdfloor_structure, get_mdfloor_mode_series
from .hysteresis_analysis import (
    fetch_hysteresis_data, compute_peak_stats,
    FIELD_FORCE, FIELD_DISP, FIELD_VEL, FIELD_ENERGY,
)

__all__ = [
    "StpReader",
    "HstReader",
    "HstHeader",
    "XbnReader",
    "PeriodXbnReader",
    "ModeInfo",
    "SnapResultLoader",
    "BinaryCategory",
    "estimate_mdfloor_structure",
    "get_mdfloor_mode_series",
    "fetch_hysteresis_data",
    "compute_peak_stats",
    "FIELD_FORCE",
    "FIELD_DISP",
    "FIELD_VEL",
    "FIELD_ENERGY",
]
