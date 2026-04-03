"""snap-controllerの使用例"""

import copy
from controller import snap_exec, Updater, UpdateConfig, Result, ResultConfig

# snap_exec: バッチファイルを作成して、SNAPを回します
# Updater: s8iファイルを書き換えます
# UpdateConfig: Updaterのコンフィグタイプ（使わなくてもOK）
# Result: 解析結果を取得します
# ResultConfig: Resultのコンフィグタイプ（使わなくてもOK）

# 初期設定
# SNAPのworkフォルダへのパス !!! 使用しているパソコンのパスに書き換えてください !!!!
SNAP_WORK_DIR_PATH = "D:\Kakemoto\kozosystem\SNAPV8\work"
# 回したいSNAPファイルへのパス（テキストデータ(.i8i)で書き出したもの）
SNAP_INPUT_FILE_PATH = "example_3D\example_3D.s8i"

# 結果として参照したいデータを設定（設定できる値は、controller/result.pyのResultConfigDictを参照）
result_config: ResultConfig = [
    {
        "case_number": 4,          # ケースの何番目の結果を読み込むか（１からスタート）
        "key": "最大絶対加速度",      # 取得したい結果のkey（controller/result.pyのResultConfigDictを参照）
        "direction": "X",          # 方向（X, Y, Zのいずれか）
    },
]  # type: ignore

# 初期値
previous_c = 0.1
c = previous_c
weight = 1e10
file_path = SNAP_INPUT_FILE_PATH

# この例ではbreakするまでループさせる
while True:
    snap_exec(file_path)  # 解析を実行

    result = Result(file_path, SNAP_WORK_DIR_PATH, result_config)   # Resultを初期化
    result_list = result.get()         # configに基づく結果を取得（リストで返ってくる）

    # 結果を受けて、次の解析の設定条件を変える（お好みで）
    previous_weight = float(copy.copy(weight))

    print("0:", result_list[0])
    break
