"""snap-controllerの使用例"""

import copy
import math
from controller import snap_exec, Updater, UpdateConfig, Result, ResultConfig, CreateResultConfig

# snap_exec: バッチファイルを作成して、SNAPを回します
# Updater: s8iファイルを書き換えます
# UpdateConfig: Updaterのコンフィグタイプ（使わなくてもOK）
# Result: 解析結果を取得します
# ResultConfig: Resultのコンフィグタイプ（使わなくてもOK）

# 初期設定
# SNAPのworkフォルダへのパス !!! 使用しているパソコンのパスに書き換えてください !!!!
SNAP_WORK_DIR_PATH = "C:\\Users\\kakemoto\\kozosystem\\SNAPV8\\work"
# 回したいSNAPファイルへのパス（テキストデータ(.i8i)で書き出したもの）
SNAP_INPUT_FILE_PATH = "test_wind_6.s8i"

# 結果として参照したいデータを設定（設定できる値は、controller/result.pyのResultConfigDictを参照）
dis_result_config: ResultConfig = CreateResultConfig(36).getAllStoryDVAR("Dx", 39)
acc_result_config: ResultConfig = CreateResultConfig(36).getAllStoryDVAR("Ax", 39)

# 初期値
max_results = []
md = 115000
k = 345
dk = 10
dis_max = 1e10
acc_max = 1e10
file_path = SNAP_INPUT_FILE_PATH


def printList(outputs: list):
    for i in outputs:
        print(i)


def createUpdateConfig(n: int, k: float):
    config = []
    for i in range(n):
        config.append({
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": i + 1,
            "row": 4,
            "value": k,
        })

    return config


def update(k: float):
    # SNAPファイル（.s8i）を書き換える設定
    update_config: UpdateConfig = createUpdateConfig(52, k)

    updater = Updater(SNAP_INPUT_FILE_PATH, update_config)
    file_path = updater.update()                # 書き換え後のファイルパスが返ってくる
    return file_path


# この例ではbreakするまでループさせる
file_path = update(k)
for i in range(10):
    snap_exec(file_path)  # 解析を実行

    prev_dis_max = copy.copy(dis_max)
    dis_result = Result(file_path, SNAP_WORK_DIR_PATH, dis_result_config)   # Resultを初期化
    dis_result_list = dis_result.get()         # configに基づく結果を取得（リストで返ってくる）

    prev_acc_max = copy.copy(acc_max)
    acc_result = Result(file_path, SNAP_WORK_DIR_PATH, acc_result_config)   # Resultを初期化
    acc_result_list = acc_result.get()         # configに基づく結果を取得（リストで返ってくる）

    dis = []
    for x in dis_result_list:
        try:
            dis.append(abs(float(x)))
        except ValueError:
            dis.append(0)

    acc = []
    for x in acc_result_list:
        try:
            acc.append(abs(float(x)))
        except ValueError:
            acc.append(0)

    dis_max = max(dis)
    acc_max = max(acc)
    max_results.append({
        "id": i + 1,
        "k": k,
        "Td": 2 * math.pi * math.sqrt(md / (k * 1000)),
        "dis_max": dis_max,
        "acc_max": acc_max,
    })
    print("results")
    printList(max_results)

    previous_k = copy.copy(k)
    k += dk                                   # この例では剛性を3[kNs/mm]ずつ増加させていく

    file_path = update(k)

print("results")
printList(max_results)
