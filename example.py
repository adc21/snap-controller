"""snap-controllerの使用例"""

import os, copy
from typing import List
from controller import snap_exec, Updater, UpdateConfig, Result, ResultConfig

# snap_exec: バッチファイルを作成して、SNAPを回します
# Updater: s8iファイルを書き換えます
# UpdateConfig: Updaterのコンフィグタイプ（使わなくてもOK）
# Result: 解析結果を取得します
# ResultConfig: Resultのコンフィグタイプ（使わなくてもOK）

# 初期設定
SNAP_WORK_DIR_PATH = "C:\\Users\\kakemoto\\kozosystem\\SNAPV8\\work"       # SNAPのworkフォルダへのパス !!! 使用しているパソコンのパスに書き換えてください !!!!
SNAP_INPUT_FILE_PATH = "example.s8i"                                    # 回したいSNAPファイルへのパス（テキストデータ(.i8i)で書き出したもの）

# 結果として参照したいデータを設定（設定できる値は、controller/result.pyのResultConfigDictを参照）
result_config: ResultConfig = [
    {
        "case_number": 1,           # ケースの何番目の結果を読み込むか（１からスタート）
        "filename": "Floor0.txt",   # ファイル名（パスではない）
        "line": 5,                  # 何行目を読み込むか（１からスタート）
        "row": 3,                   # lineの何列目を読み込むか（１からスタート）
    },
    {
        "case_number": 1,
        "filename": "Floor0.txt",
        "line": 5,
        "row": 7,
    },
]

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
    weight = 10 * float(result_list[0]) + float(result_list[1]) # この例では変位と加速度の両方を考慮した値を指標に用いる
    delta = weight - previous_weight

    if delta > 0:   # weightが増加したら解析終了
        print(f"Weightはc={previous_c}で最小となる可能性があります。")
        break

    previous_c = copy.copy(c)
    c += 0.05                                   # この例では減衰を0.05[kNs/mm]ずつ増加させていく

    # SNAPファイル（.s8i）を書き換える設定
    update_config: UpdateConfig = [
        {
            "category": "REM / 粘性/ｵｲﾙﾀﾞﾝﾊﾟｰ",   # どのカテゴリーを書き換えるか（.s8iファイルのカテゴリー部分の行をコピペ）
            "line": 1,                          # 何行目を書き換えるか（１からスタート） 
            "row": 9,                           # lineの何列目を書き換えるか（１からスタート） 
            "value": c,                         # 書き換える値
        },
    ]

    updater = Updater(SNAP_INPUT_FILE_PATH, update_config)
    file_path = updater.update()                # 書き換え後のファイルパスが返ってくる
