"""snap-controllerの使用例"""

import copy
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
SNAP_INPUT_FILE_PATH = "test_irdt.s8i"

# 結果として参照したいデータを設定（設定できる値は、controller/result.pyのResultConfigDictを参照）
dis_result_config: ResultConfig = CreateResultConfig(10).getAllStoryDVAR("Dx", 39)
acc_result_config: ResultConfig = CreateResultConfig(10).getAllStoryDVAR("Ax", 39)

# 初期値
max_results = []
k = 30
dk = 3
dis_max = 1e10
acc_max = 1e10
file_path = SNAP_INPUT_FILE_PATH

def printList(outputs: list):
    for i in outputs:
        print(i)

# この例ではbreakするまでループさせる
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
        "dis_max": dis_max,
        "acc_max": acc_max,
    })
    print("results")
    printList(max_results)

    previous_k = copy.copy(k)
    k += dk                                   # この例では剛性を3[kNs/mm]ずつ増加させていく

    # SNAPファイル（.s8i）を書き換える設定
    update_config: UpdateConfig = [
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 17,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 18,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 19,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 20,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 21,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 22,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 23,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 24,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 25,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 26,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 27,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 28,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 29,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 30,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 31,
            "row": 3,
            "value": k,
        },
        {
            "category": "REM / ｽﾌﾟﾘﾝｸﾞ",
            "line": 32,
            "row": 3,
            "value": k,
        },
    ]

    updater = Updater(SNAP_INPUT_FILE_PATH, update_config)
    file_path = updater.update()                # 書き換え後のファイルパスが返ってくる

print("results")
printList(max_results)
