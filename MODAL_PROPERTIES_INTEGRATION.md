# 固有値解析結果表示機能の実装

## 実装完了内容

### 1. **`app/models/period_reader.py`** ✅
SNAP の Period.xbn ファイルを解析し、固有周期・周波数・参加質量比を抽出します。

**主要クラス:**
```python
class PeriodReader:
    periods: Dict[int, float]              # モード番号 -> 固有周期 [秒]
    frequencies: Dict[int, float]          # モード番号 -> 固有周波数 [Hz]
    participation_mass: Dict[int, float]   # モード番号 -> 参加質量比 [%]
```

**使用例:**
```python
from app.models.period_reader import PeriodReader

reader = PeriodReader("/path/to/Period.xbn")
print(reader.periods)              # {1: 3.9738, 2: 1.5811}
print(reader.frequencies)          # {1: 0.2516, 2: 0.6325}
print(reader.participation_mass)   # {1: 100.0}
```

---

### 2. **`app/ui/modal_properties_widget.py`** ✅
複数ケースの固有値解析結果を比較表示するUIウィジェット。

**主要メソッド:**
- `set_cases(cases)` - ケースリストをセット
- `set_result_dir(result_dir)` - 結果フォルダパスを指定
- `refresh()` - テーブルを再描画

**表示形式:**
| ケース | モード1 周期 | モード1 周波数 | モード1 参加質量比 | モード2 周期 | ... |
|--------|-------------|---------------|--------------------|-------------|-----|
| D1     | 3.9738      | 0.2516        | 100.00             | 1.5811      | ... |
| DA     | 3.8120      | 0.2620        | 95.50              | 1.5210      | ... |

---

## 統合方法

### Step 1: 最もシンプルな統合（推奨）

`app/ui/main_window.py` または表示対象のウィジェットで：

```python
from app.ui.modal_properties_widget import ModalPropertiesWidget

# ウィジェットを作成
self.modal_widget = ModalPropertiesWidget()

# タブウィジェットに追加（結果表示タブの兄弟として）
self.result_tabs.addTab(self.modal_widget, "🌊 固有値解析結果")

# ケースリストをセット（Period.xbn は各 AnalysisCase の output_dir から自動検索）
self.modal_widget.set_cases(project.cases)
```

**これだけです！** AnalysisCase の `output_dir` と `model_path` から自動的に Period.xbn を検索します。

### Step 2: 動的更新

解析完了時またはケース選択時に：

```python
# ケースリストを更新（Period.xbn は自動検索）
self.modal_widget.set_cases(self.project.cases)

# 表を再描画
self.modal_widget.refresh()
```

### Step 3: 結果フォルダのパス検索順序

Period.xbn は以下の順序で自動検索されます：

1. `case.output_dir/case.name/Period.xbn`
2. `case.output_dir/Period.xbn`
3. `モデルパスのディレクトリ/case.name/Period.xbn`
4. `モデルパスのディレクトリ/Period.xbn`

**例:**
- ケース名: `D1`
- モデルパス: `C:\users\keita\App\ADC\snap-controller\example_model\example_shear\example_shear.s8i`
- output_dir: `D:\Kakemoto\kozosystem\SNAPV8\work`

→ `D:\Kakemoto\kozosystem\SNAPV8\work\D1\Period.xbn` を自動検索

### Step 4: 後方互換性（オプション）

既に他の方法で結果フォルダパスを把握している場合は、以下でも対応：

```python
# 直接パスを指定（古い方法）
self.modal_widget.set_result_dir("D:\\Kakemoto\\kozosystem\\SNAPV8\\work")
```

---

## 期待される表示例

### Period.xbn から読み取られるデータ（D1ケース）
```
モード 1: T=3.9738秒, f=0.2516Hz, PM=100.00%
モード 2: T=1.5811秒, f=0.6325Hz, PM=0.00%
```

### テーブル表示
```
┌──────┬─────────────────┬──────────────────┐
│ケース │ モード1         │ モード2          │
│      │ 周期   周波数  %  │ 周期   周波数  % │
├──────┼─────────────────┼──────────────────┤
│D1    │3.9738  0.2516 100│1.5811  0.6325 0  │
│DA    │3.8120  0.2620  95│1.5210  0.6570 5  │
└──────┴─────────────────┴──────────────────┘
```

---

## ファイル構成

```
app/
├── models/
│   └── period_reader.py          ✅ 実装済み
└── ui/
    └── modal_properties_widget.py  ✅ 実装済み
```

---

## トラブルシューティング

### Period.xbn が見つからない場合
- 解析が完了していることを確認
- SNAP の出力フォルダ設定を確認
- Period.xbn ファイルがケースフォルダに存在するか確認

### テーブルにデータが表示されない場合
1. `set_result_dir()` でパスが正しく指定されているか確認
2. `set_cases()` で完了済みケース（status="COMPLETED"）を指定しているか確認
3. デバッグ: `modal_widget._period_data` の内容を確認

### Period.xbn のバイナリ形式が異なる場合
- `period_reader.py` の `_extract_modal_properties()` メソッド内のインデックスを調整してください
- float[12], float[13], float[14] の値を確認

---

## 今後の拡張可能性

1. **グラフ化:**  matplotlib で固有周期の推移をプロット
2. **統計情報:** モード数、平均周期、周波数帯域など
3. **フィルター:** 特定モードの周期範囲で絞り込み
4. **エクスポート:** Period.xbn データを CSV で出力

