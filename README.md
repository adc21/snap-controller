# snap-controller

任意形状立体フレームの弾塑性解析ソフト [SNAP Ver.8](https://www.kozo.co.jp/program/kozo/snap/index.html) を活用した、**免震・制振装置の設計・配置・計画検討を支援する Python ベースのデスクトップアプリ**です。

SNAP の自動バッチ実行機能（`/BD` フラグ）を利用して、複数の解析ケースを一括実行・比較し、最終的にはダンパーの最適化まで行うことを目指しています。

---

> [!WARNING]
> **このソフトウェアは現在 α 版です。**
>
> - 動作が不安定な箇所や、未実装・未検証の機能が含まれます
> - 予告なく仕様・インターフェースが変更される場合があります
> - 実務での使用は**自己責任**でお願いします。計算結果の正確性は保証されません
> - バグや不具合を見つけた場合は [Issues](../../issues) に報告いただけると助かります

---

## 特徴

- **GUIアプリ（PySide6）** — 一定の建築構造の知識があれば誰でも使えるデスクトップ UI
- **ウェルカム画面 & セットアップガイド** — 初回起動時にわかりやすい導入フローを提供
- **複数ケース管理** — ダンパー種別・パラメータを変えた複数ケースをテーブルで一括管理
- **ケーステンプレート** — よく使う設定をテンプレートとして保存・再利用
- **バッチ自動実行** — SNAP を `/BD` フラグでバッチ起動し、解析を自動化
- **バッチキュー管理** — 複数ケースの実行状況をキュー形式でリアルタイムに把握
- **複数地震波対応** — 複数の入力地震波を一括管理し、全波の最大値で評価
- **ダンパーカタログ** — ダンパー種別・パラメータをカタログとして管理・選択
- **ダンパー配置表示** — 建物モデルに対するダンパー配置をビジュアルで確認
- **結果グラフ比較** — 最大応答相対変位・相対速度・絶対加速度・層間変形角・せん断力係数・転倒モーメントをグラフで比較
- **エンベロープ比較グラフ** — 複数ケースのエンベロープを重ね描きして一目で比較
- **時刻歴応答グラフ** — 変位・速度・加速度の時刻歴波形を確認
- **レーダーチャート** — 複数の性能指標を1枚のレーダーチャートで可視化・比較
- **ケースランキング** — 指定した指標に基づいてケースを自動ランキング
- **感度分析** — パラメータ変化に対する応答の感度をグラフ化
- **パラメータスイープ** — 指定パラメータを自動変化させた複数ケースを一括生成・実行
- **目標性能基準** — 層間変形角・加速度などに目標値を設定し、達成状況を自動評価
- **ダンパー最適化** — 目標性能を指定し、最適なダンパーパラメータを自動探索
- **モデル情報表示** — 読み込んだ .s8i ファイルの建物情報をパネルで表示
- **入力バリデーション** — 解析実行前に入力値の整合性を自動チェック
- **デモ実行** — SNAP が手元になくてもモックデータで UI を確認可能
- **プロジェクト保存** — `.snapproj` ファイルで設定・ケース・結果を一括保存／復元
- **自動保存** — 指定間隔でプロジェクトを自動バックアップ（最大5世代）
- **テーマ設定** — ライト／ダークなどの UI テーマを切り替え可能

---

## スクリーンショット

> 解析ケース一覧・結果グラフ・ログパネルが1画面に集約されています。

---

## 必要な環境

| 要件 | バージョン |
|---|---|
| OS | Windows 10 / 11 |
| SNAP | Ver.8（バッチ実行対応版） |
| Python | 3.11 以上（EXE 化後は不要） |
| PySide6 | 6.5.0 以上 |

---

## インストール（開発者向け）

```bash
# リポジトリをクローン
git clone https://github.com/adc21/snap-controller.git
cd snap-controller

# pipenv で仮想環境を作成・依存ライブラリをインストール
pipenv install

# 仮想環境に入る
pipenv shell
```

> **pipenv がない場合**は `pip install pipenv` でインストールしてください。
> pip を直接使う場合は `pip install -r requirements.txt` でもインストールできます。

### 依存ライブラリ

| パッケージ | バージョン | 用途 |
|---|---|---|
| PySide6 | >=6.5.0 | GUI フレームワーク |
| matplotlib | >=3.7.0 | グラフ描画 |
| numpy | >=1.24.0 | 数値計算 |
| pandas | >=2.0.0 | データ処理 |
| pyqtdarktheme | >=2.1.0 | ダーク/ライトテーマ |
| qtawesome | >=1.2.0 | アイコン |
| openpyxl | >=3.1.0 | Excel 出力 |

---

## アプリの起動

```bash
python run_app.py
```

---

## 初回セットアップ

アプリ初回起動後、以下の手順で設定してください。

### 1. SNAP.exe のパスを設定

メニュー「設定」→「アプリケーション設定」を開き、以下を入力します。

| 設定項目 | 例 |
|---|---|
| デフォルト SNAP.exe | `C:\Program Files\SNAP Ver.8\Snap.exe` |
| SNAP work フォルダ | `C:\Users\xxx\kozosystem\SNAPV8\work` |

> **work フォルダ**は SNAP がインストールされたユーザーフォルダ内にある `SNAPV8\work` です。解析結果の読み取りに使用します。

### 2. .s8i ファイルを読み込む

ファイルメニュー「モデルファイルを開く」から、SNAPの入力ファイル（`.s8i`）を選択します。

### 3. 解析ケースを作成する

「＋ 追加」ボタンでケースを追加し、ケース名・ダンパーパラメータを設定します。テンプレートから作成することもできます。

### 4. 解析を実行する

ケースを選択して「▶ 実行」ボタン（または F5）を押すと解析が開始されます。

---

## 解析結果の確認

解析完了後、以下の結果値がグラフで確認・比較できます。

| 項目 | 単位 | ファイル |
|---|---|---|
| 最大応答相対変位 | m | `Floor*.txt` (Dx 列) |
| 最大応答相対速度 | m/s | `Floor*.txt` (Vx 列) |
| 最大応答絶対加速度 | m/s² | `Floor*.txt` (Ax 列) |
| 最大層間変形 | m | `Story*.txt` (Sx 列) |
| 最大層間変形角 | rad | `Story*.txt` (Drx 列) |
| せん断力係数 | — | `Story*.txt` (Cx 列) |
| 最大転倒モーメント | kN·m | `Story*.txt` (Mx 列) |

複数の地震波ケース（`Floor0.txt`, `Floor1.txt` ...）がある場合は全ケースの最大値を採用します。

---

## ファイル構成

```
snap-controller/
├── run_app.py                  # アプリ起動スクリプト
├── run_cli.py                  # CLI 実行スクリプト
├── requirements.txt
├── requirements_build.txt      # ビルド用依存ライブラリ
├── build.bat                   # EXE ビルドスクリプト
├── snap_controller.spec        # PyInstaller 設定
│
├── app/                        # デスクトップアプリ本体
│   ├── models/                 # データモデル
│   │   ├── analysis_case.py    # 解析ケース（1ケース = 1回の SNAP 解析）
│   │   ├── project.py          # プロジェクト（.snapproj ファイル管理）
│   │   ├── s8i_parser.py       # .s8i ファイルパーサー
│   │   ├── damper_catalog.py   # ダンパーカタログ
│   │   ├── earthquake_wave.py  # 入力地震波管理
│   │   ├── case_template.py    # ケーステンプレート
│   │   └── performance_criteria.py  # 目標性能基準
│   │
│   ├── services/               # ビジネスロジック
│   │   ├── analysis_service.py # SNAP 実行サービス（QThread）
│   │   ├── optimizer.py        # ダンパー最適化エンジン
│   │   ├── snap_evaluator.py   # 解析結果評価
│   │   ├── report_generator.py # 帳票出力
│   │   ├── autosave.py         # 自動保存
│   │   └── validation.py       # 入力チェック
│   │
│   └── ui/                     # PySide6 UI
│       ├── main_window.py           # メインウィンドウ
│       ├── welcome_widget.py        # ウェルカム画面
│       ├── setup_guide_widget.py    # セットアップガイド
│       ├── sidebar_widget.py        # サイドバー
│       ├── dashboard_widget.py      # ダッシュボード
│       ├── case_table.py            # ケース一覧テーブル
│       ├── case_edit_dialog.py      # ケース編集ダイアログ
│       ├── case_compare_dialog.py   # ケース詳細比較ダイアログ
│       ├── template_dialog.py       # ケーステンプレートダイアログ
│       ├── result_chart_widget.py   # 結果グラフ（個別ケース）
│       ├── compare_chart_widget.py  # ケース比較グラフ
│       ├── envelope_chart_widget.py # エンベロープ比較グラフ
│       ├── time_history_widget.py   # 時刻歴応答グラフ
│       ├── radar_chart_widget.py    # レーダーチャート
│       ├── result_table_widget.py   # 結果テーブル
│       ├── ranking_widget.py        # ケースランキング
│       ├── sensitivity_widget.py    # 感度分析
│       ├── batch_queue_widget.py    # バッチキュー管理
│       ├── run_selection_widget.py  # 解析実行選択
│       ├── damper_placement_widget.py  # ダンパー配置表示
│       ├── damper_catalog_dialog.py    # ダンパーカタログダイアログ
│       ├── earthquake_wave_dialog.py   # 地震波管理ダイアログ
│       ├── multi_wave_dialog.py        # 複数地震波設定
│       ├── model_info_widget.py        # モデル情報表示
│       ├── file_preview_widget.py      # ファイルプレビュー
│       ├── criteria_dialog.py          # 目標性能基準設定
│       ├── optimizer_dialog.py         # 最適化ダイアログ
│       ├── sweep_dialog.py             # パラメータスイープ
│       ├── settings_dialog.py          # アプリ設定ダイアログ
│       ├── validation_dialog.py        # 入力バリデーションダイアログ
│       ├── export_dialog.py            # 帳票出力ダイアログ
│       ├── shortcut_help_dialog.py     # キーボードショートカットヘルプ
│       ├── log_widget.py               # ログパネル
│       ├── snap_params.py              # SNAP パラメータ定義
│       ├── step_nav_footer.py          # ステップナビゲーション
│       └── theme.py                    # テーマ設定
│
└── controller/                 # SNAP 制御ライブラリ（旧来の API も維持）
    ├── snap_exec.py            # SNAP.exe 起動（/BD フラグ付き）
    ├── result.py               # 解析結果パーサー（Floor*.txt / Story*.txt）
    ├── updater.py              # .s8i ファイルパラメータ書き換え
    ├── executor.py             # バッチ実行エンジン（BatchExecutor）
    └── _path.py                # SNAP work ディレクトリパス管理
```

---

## controller ライブラリ API（Python スクリプトから直接使う場合）

```python
from controller import snap_exec, Updater, Result
from controller import BatchExecutor, BatchConfig, Job
```

### snap_exec — SNAP の実行

```python
from controller.snap_exec import snap_exec

snap_exec(
    snap_exe=r"C:\Program Files\SNAP Ver.8\Snap.exe",
    input_file=r"path\to\model.s8i",
    type_prefix="D",   # /BD フラグで自動解析実行
)
```

> SNAP Ver.8 では `/BD` フラグを付けることでバッチ実行（自動解析）が可能になります。このフラグがないと SNAP の GUI が開くだけで解析は実行されません。

### Updater — .s8i ファイルの書き換え

```python
from controller import Updater

upd = Updater("model.s8i")
upd.set_param("DAMPING", 0.05)
upd.write("model_modified.s8i")
```

### Result — 解析結果の読み取り

```python
from controller import Result

res = Result(r"C:\...\SNAPV8\work\model\D1")

print(res.max_disp)         # {2: 0.106, ...}  最大相対変位 [m]
print(res.max_vel)          # {2: 0.416, ...}  最大相対速度 [m/s]
print(res.max_acc)          # {2: 0.454, ...}  最大絶対加速度 [m/s²]
print(res.max_story_drift)  # {2: 0.0266, ...} 最大層間変形角 [rad]
print(res.shear_coeff)      # {2: 0.046, ...}  せん断力係数
print(res.max_otm)          # {2: 181.4, ...}  最大転倒モーメント [kN·m]
```

### BatchExecutor — バッチ実行エンジン

```python
from controller import BatchExecutor, BatchConfig, Job

config = BatchConfig(max_workers=1, max_retries=2)
executor = BatchExecutor(config)

job = Job(
    name="Case-01",
    input_file=r"path\to\model.s8i",
    snap_exe=r"C:\Program Files\SNAP Ver.8\Snap.exe",
    output_dir=r"path\to\output",
)
executor.add_job(job)
executor.set_on_job_finished(lambda j: print(f"完了: {j.name}"))
executor.start()
```

---

## SNAP 解析結果ファイルの形式

SNAP は解析完了後、work ディレクトリ内に以下の構造でファイルを出力します。

```
{work_dir}\
  {モデル名}\
    D1\           ← 解析ケース1の結果
      Floor0.txt  ← フロア応答（変位・速度・加速度）
      Story0.txt  ← 層間応答（層間変形・せん断力・転倒モーメント）
      Floor1.txt  ← 地震波ケース2のフロア応答（複数波の場合）
      Story1.txt
      ...
    D2\           ← 解析ケース2の結果（2回目実行時）
      ...
```

**Floor*.txt フォーマット**

```
//FloorResult[GrpNo:0]
//Unit:D:mm,V:mm/s,A:mm/s~2
//No,Z1,Dx2,Dy3,Vx4,Vy5,Ax6,Ay7,RAx8,RAy9
   2.00    4.00  1.06e+02  0.00e+00  4.16e+02  ...
```

**Story*.txt フォーマット**

```
//StoryResult[GrpNo:0]
//Unit:S:mm,Q:kN,M:kN.m
//No,Z1,Sx2,Sy3,Qx4,Qy5,Cx6,Cy7,Mx8,My9,Drx10,Dry11,...
   2.00    4.00  1.06e+02  0.00e+00  4.54e+01  ...
```

---

## EXE ファイルへのビルド

Python がインストールされていない環境でも動作する単一の `.exe` ファイルを作成できます。

```bash
# pipenv 環境の場合
pipenv install --dev
pipenv run pyinstaller snap_controller.spec

# または pip を直接使う場合
pip install -r requirements_build.txt
pyinstaller snap_controller.spec
```

または付属の `build.bat` をダブルクリックして実行してください（Python の有無チェック、クリーンビルド、パッケージインストールを自動で行います）。

```
build.bat
```

ビルド完了後、`dist\snap-controller.exe` が生成されます。このファイル1つを配布すれば、Python 未インストールの PC でも動作します。

> **初回起動について**: onefile モードのため、初回起動時は `%TEMP%` フォルダへの展開で数秒かかります。2回目以降はキャッシュが使われるため高速になります。

---

## 開発・技術情報

- **UI フレームワーク**: PySide6 (Qt for Python)
- **グラフ描画**: matplotlib (PySide6 バックエンド)
- **データ処理**: pandas / numpy
- **スレッド管理**: QThread（解析中も UI がフリーズしない設計）
- **設定永続化**: QSettings（Windows レジストリ）
- **プロジェクトファイル**: JSON 形式（`.snapproj`）
- **EXE ビルド**: PyInstaller（--onefile モード）

---

## ライセンス

MIT License

---

## 関連リンク

- [SNAP Ver.8 公式サイト](https://www.kozo.co.jp/program/kozo/snap/index.html)
- [GitHub リポジトリ](https://github.com/adc21/snap-controller)
