# snap-controller

任意形状立体フレームの弾塑性解析ソフト[SNAP ver.8](https://www.kozo.co.jp/program/kozo/snap/index.html)をPythonで動かして、パラメトリックに解析を行うためのライブラリ

## 概要
SNAPがバージョン8にアップデートされ、バッチファイルから解析を回すことができるようになりました。
snap-controllerは、これを利用してPythonからパラメトリックな計算をするために、

1. Pythonからバッチファイルを実行
2. 解析結果の取得
3. SNAPファイルの書き換え

といった作業を支援するためのライブラリです。

## 必要なもの
- SNAP version8
- Python3.8+

## API
| 名称         | 内容                                 | 備考 |
| ------------ | ------------------------------------ | ---- |
| snap_exec    | バッチファイルを作成して、SNAPを回す |      |
| Updater      | s8iファイルの書き換え                |      |
| UpdateConfig | Updaterのコンフィグタイプ            |      |
| Result       | 解析結果の取得                       |      |
| ResultConfig | Resultのコンフィグタイプ             |      |

## 使い方

1. このリポジトリをクローンや、zipでダウンロード(ダウンロードはこちら　https://github.com/adc21/snap-controller/releases)
2. controllerフォルダを自分のプロジェクトのルートディレクトリに追加
3. 以下のように呼び出す

```
from controller import snap_exec, Updater, UpdateConfig, Result, ResultConfig
```

詳しくは以下の[example.py](https://github.com/adc21/snap-controller/blob/main/example.py)を参照してください。

## example.pyの使い方
snap-controllerを使用したコードの例として、[example.py](https://github.com/adc21/snap-controller/blob/main/example.py)を用意しています。

使い方は、

1. example.pyファイル内の `SNAP_WORK_DIR_PATH` を、使用しているパソコンのパスに書き換える。
2. snap-controllerのルートディレクトリで、

```
python example.py
```
