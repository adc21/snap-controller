# SNAP制御ソフト レコード形式リファレンス

## Record Format Definitions - 制振・免震装置関連

### 1. RD - 制振ブレース (Damper Brace)
**Page: 64**

| 順序 | 項目 | 説明 | 省略値 |
|------|------|------|--------|
| 1 | 名称 | 15文字以内 | |
| 2 | 1 | | |
| 3 | 節点 | 2 | [節点(ND)]名称 | |
| | | | | |
| 4 | 種別 | 0～4の数字<br/>0: 関材/座摩ダンパー<br/>1: 粘性/オイルダンパー<br/>2: オイルダンパー<br/>3: 粘性ダンパー<br/>4: 粘弾性ダンパー | 0 |
| 5 | 装置 | 名称 | [免震制振装置-鋼材/摩擦ダンパー(DSD)], [免震制振装置-粘性/オイルダンパー(DVOD)], [免震制振装置-オイルダンパー(DOD)], [免震制振装置-粘性ダンパー(DVD))のいずれかの名称 | |
| 6 | | 装置剛性 | | 0.0 |
| 7 | | 取付け剛性 | | 0.0 |
| 8 | | アスペクト比 | | 0.0 |
| 9 | 付加重量 | 1 | 節点1に付加する重量 | 0.0 |
| 10 | | 2 | 節点2に付加する重量 | 0.0 |
| 11 | 倍数 | | | 1 |
| 12 | 方向 | 0～2の数字<br/>0: TX<br/>1: TY<br/>2: TZ | 0 |
| 13 | 曲げ変形 | 0かりの数字<br/>0: 含む<br/>1: 除く | 0 |
| 14 | 座標系 | 種別 | 0～2の数字<br/>0: 基準座標系<br/>1: 部材座標系<br/>2: 局部座標系 | 0 |
| 15 | | 局部座標 | [局部座標(LC)]名称 | |
| 16 | グループ | | 0～4の数字<br/>0: なし<br/>1: 1<br/>2: 2<br/>3: 3<br/>4: 4 | |
| 17 | 出力 | | 0かりの数字<br/>0: しない<br/>1: する | 1 |
| 18 | 断面計算 | 計算 | 0かりの数字<br/>0: しない<br/>1: する | 1 |
| 19 | 断面計算 | 部材長を諸侵係数 | | 0.0 |
| 20 | | 応力の削減率 | | 1.0 |

**Note:** (bRaceDamper)

---

### 2. SR - スプリング (Spring/Damper Brace)
**Page: 60**

| 順序 | 項目 | 説明 | 省略値 |
|------|------|------|--------|
| 1 | 節点1 | | | |
| 2 | 節点2 | | [節点(ND)]名称 | |
| | | | | |
| 3 | 剛性 | TX | | 0.0 |
| 4 | | TY | | 0.0 |
| 5 | | TZ | | 0.0 |
| 6 | | RX | | 0.0 |
| 7 | | RY | | 0.0 |
| 8 | | RZ | | 0.0 |
| 9 | 復元力特性 | 単軸バネ | [単軸バネモデル-スプリング(USR)]名称 | |
| 10 | 座標系 | 種別 | 0～2の数字<br/>0: 基準座標系<br/>1: 部材座標系<br/>2: 局部座標系 | 0 |
| 11 | | 局部座標 | [局部座標(LC)]名称 | |
| 12 | 履歴出力 | | 0かりの数字<br/>0: しない<br/>1: する | 0 |
| 13 | 出力 | | 0かりの数字<br/>0: しない<br/>1: する | 1 |
| 14 | 初期荷重析 | | 0かりの数字<br/>0: しない<br/>1: する | 1 |
| 15 | 減衰 | TX | | 0.0 |
| 16 | | TY | | 0.0 |
| 17 | | TZ | | 0.0 |
| 18 | | RX | | 0.0 |
| 19 | | RY | | 0.0 |
| 20 | | RZ | | 0.0 |

**Note:** (SpRing)

---

### 3. DVOD - 粘性/オイルダンパー (Device Viscous/Oil Damper)
**Page: 114**

| 順序 | 項目 | 説明 | 省略値 |
|------|------|------|--------|
| 1 | 名称 | | 15文字以内 | |
| 2 | | 種別 | 0: 使用しない<br/>52: 免震用オイルダンパー<br/>53: 免震用粘性ダンパー<br/>72: 制振用オイルダンパー<br/>73: 制振用粘性ダンパー | 0 |
| 3 | k-DB | 会社 | k-DBの会社名番号 | |
| 4 | | 製品 | k-DBの製品種別番号のの下3桁 | |
| 5 | | 型番 | k-DBの型番 | |
| 6 | | 減衰モデル | 0～5の数字<br/>0: ダッシュポット単体<br/>1: Voigt型<br/>2: Maxwell型<br/>3: ダッシュポットと質量<br/>4: 質量単体<br/>5: 回転方向ダッシュポット | 0 |
| 7 | | 質量(t) | | 0.0 |
| 8 | 装置特性 | | 種別 | 0～3の数字<br/>0: 線形弾性型 (EL1)<br/>1: バイリニア逆行型 (EL2)<br/>2: トリリニア逆行型 (EL3)<br/>3: 曲線型 (EF1) | 0 |
| 9 | | | C0 | | 0.0 |
| 10 | | ダッシュポット特性 | Fc | | 0.0 |
| 11 | | | Fv | | 0.0 |
| 12 | | | Vs | | 0.0 |
| 13 | | | α | | 0.0 |
| 14 | | | β | | 0.0 |
| 15 | | 剛性 | | | 0.0 |
| 16 | | 取付け剛性 | | | 0.0 |
| 17 | | 装置点き | | | 0.0 |
| 18 | 重量 | 種別 | 0かりの数字<br/>0: 単位長さ当重量<br/>1: 重量 | 0 |
| 19 | | 重量 | | | 0.0 |
| 20 | 変動係数 | 下限 | 温度 | | 0.0 |
| 21 | | | ε | | 1.0 |
| 22 | | | 温度 | | 0.0 |
| 23 | | 上限 | ε | | 1.0 |

**Note:** (Device Viscous/Oil Damper)

---

### 4. DSD - 鋼材/摩擦ダンパー (Device Steel Damper)
**Page: 112-113**

| 順序 | 項目 | 説明 | 省略値 |
|------|------|------|--------|
| 1 | 名称 | | 15文字以内 | |
| 2 | | 種別 | 0～3の数字<br/>0: 使用しない<br/>1: ブレース<br/>2: 間柱<br/>3: 摩擦ダンパー | 0 |
| 3 | k-DB | 会社 | k-DBの会社名番号 | |
| 4 | | 製品 | k-DBの製品種別番号のの下3桁 | |
| 5 | | 型番 | k-DBの型番 | |
| 6 | | 降伏の変形 | | 0かりの数字<br/>0: 考慮しない<br/>1: 考慮する | 0 |
| 7 | | | 種別 | 0～7の数字<br/>0: 岡性低減型A(BL2)<br/>1: 線仏荷体岡剛制度ダンパー(AL(Y)2)<br/>2: 線仏荷体岡剛制度ダンパー(BL(Y)3)<br/>3: Tanaka-Garny型(RD4)<br/>4: 厚力変動履歴型ダンパー(VHD)<br/>5: 鋼筋両方是動低比型(K2)<br/>6: 溝形鋼構造型(MCB)<br/>7: 標準トリリニアモデル(TL3)<br/>8: 修正 Menegotto-Pinto型(MP3) | 0 |
| 8 | 装置特性 | K0 | | | 0.0 |
| 9 | | Fe | | | 0.0 |
| 10 | | | Fy | | | 0.0 |
| 11 | | | Fu | | | 0.0 |
| 12 | | | α | | | 0.0 |
| 13 | | | β | | | 0.0 |
| 14 | | | P1 | | | 0.0 |
| 15 | | | P2 | | | 0.0 |
| 16 | | | P3 | | | 0.0 |
| 17 | | | P4 | | | 0.0 |
| 18 | | | d | | | 0.0 |
| 19 | | | 剛性 | | | 0.0 |
| 20 | 取付け | F | | | 0.0 |
| 21 | | α | | | 0.0 |
| 22 | | d | | | 0.0 |
| 23 | | 装置点き | | | 0.0 |
| 24 | 重量 | 種別 | 0かりの数字<br/>0: 単位長さ当重量<br/>1: 重量 | 0 |
| 25 | | 重量 | | | 0.0 |
| 26 | 初期荷重 | 計算 | 0かりの数字<br/>0: しない<br/>1: する | 0 |
| 27 | 被労域值障害 | 低圧点き | | | 0.0 |
| 28 | | 疲労曲線 | P1 | | | 20.48 |
| 29 | | | P2 | | | -0.49 |
| 30 | | 頭度解析割め幅 | | | 0.05 |
| 31 | 初期荷重 | 計算 | 0かりの数字<br/>0: しない<br/>1: する | 1 |
| 32 | 減衰 | | | | 0.0 |

**Note:** (Device Steel Damper)

---

## Summary Table

| Record Type | Japanese Name | English Name | Page | Fields |
|-------------|---------------|--------------|------|--------|
| RD | 制振ブレース | Damper Brace | 64 | 20 |
| SR | スプリング | Spring | 60 | 20 |
| DVOD | 粘性/オイルダンパー | Device Viscous/Oil Damper | 114 | 23 |
| DSD | 鋼材/摩擦ダンパー | Device Steel Damper | 112-113 | 32 |

---

## Key Field Types

### Type Codes (種別)

**RD - Damper Brace Types:**
- 0: 関材/座摩ダンパー (Steel/Friction Damper)
- 1: 粘性/オイルダンパー (Viscous/Oil Damper)
- 2: オイルダンパー (Oil Damper)
- 3: 粘性ダンパー (Viscous Damper)
- 4: 粘弾性ダンパー (Viscoelastic Damper)

**DVOD - Damping Model Types:**
- 0: ダッシュポット単体 (Dashpot alone)
- 1: Voigt型 (Voigt type)
- 2: Maxwell型 (Maxwell type)
- 3: ダッシュポットと質量 (Dashpot and mass)
- 4: 質量単体 (Mass alone)
- 5: 回転方向ダッシュポット (Rotational dashpot)

**DVOD - Device Characteristic Types (装置特性):**
- 0: 線形弾性型 (Linear elastic - EL1)
- 1: バイリニア逆行型 (Bilinear hysteresis - EL2)
- 2: トリリニア逆行型 (Trilinear hysteresis - EL3)
- 3: 曲線型 (Curved type - EF1)

**DSD - Device Characteristic Model Types:**
- 0: 岡性低減型A(BL2)
- 1: 線仏荷体岡剛制度ダンパー(AL(Y)2)
- 2: 線仏荷体岡剛制度ダンパー(BL(Y)3)
- 3: Tanaka-Garny型(RD4)
- 4: 厚力変動履歴型ダンパー(VHD)
- 5: 鋼筋両方是動低比型(K2)
- 6: 溝形鋼構造型(MCB)
- 7: 標準トリリニアモデル(TL3)
- 8: 修正 Menegotto-Pinto型(MP3)

### Direction Codes (方向)
- 0: TX (X translation)
- 1: TY (Y translation)
- 2: TZ (Z translation)

### Coordinate System Types (座標系 種別)
- 0: 基準座標系 (Reference coordinate system)
- 1: 部材座標系 (Member coordinate system)
- 2: 局部座標系 (Local coordinate system)

---

## Notes

1. Fields marked with reference names (e.g., [節点(ND)]名称) require references to other element definitions in the SNAP system
2. Default values (省略値) are assumed when fields are left blank
3. Some fields have hierarchical relationships (indicated by indentation in the tables)
4. Device reference (k-DB) fields require specific manufacturer/product code mappings
5. DVOD and DSD include detailed material behavior parameters for damper characteristic definition
