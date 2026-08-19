# Biohub – Cell Tracking During Development コンペ完全ガイド

> **最終確認:** 2026-08-19  
> **役割:** このリポジトリにおけるコンペ仕様・評価・データ理解の入口。迷ったらまずこの文書を読む。  
> **注意:** 開催中にデータ、metric、Code Competition 要件、期限が更新される可能性がある。重要な設計判断と最終提出前には Kaggle 公式ページと公式 baseline repository を再確認する。

---

## 1. 30秒で分かるコンペ概要

| 項目 | 内容 |
|---|---|
| コンペ | **Biohub – Cell Tracking During Development** |
| 主催 | Biohub |
| 種別 | Kaggle Research Code Competition |
| 課題 | 3D+time 顕微鏡画像から細胞を検出・追跡し、分裂を含む lineage graph を復元する |
| 入力 | OME-Zarr、基本軸 `(T, Z, Y, X)` |
| 正解 | GEFF tracking graph。**ground truth は sparse** |
| 出力 | cell node と temporal edge を表す `submission.csv` |
| 主評価 | adjusted edge Jaccard + `0.1 × division Jaccard` |
| node matching | 同一時刻、centroid 距離最大 **7 µm**、optimal bipartite assignment |
| 賞金総額 | **$60,000** |
| Entry deadline | **2026-09-22 23:59 UTC** |
| Final submission deadline | **2026-09-29 23:59 UTC** |

このコンペは単なる3D物体検出ではない。

```text
3D+time microscopy
      ↓
cell detection
      ↓
temporal association / linking
      ↓
normal continuation + cell division
      ↓
lineage graph
      ↓
official sparse-aware tracking metric
```

最終 score は edge 項が中心で、division 項の重みは 0.1。したがって、**まず node localization と linking を安定させ、その後 division を詰める**のが基本方針になる。

---

## 2. 何を予測するのか

### 2.1 Node: cell detection

各 timepoint の3D volume内で細胞中心を予測する。

```text
node_id, t, z, y, x
```

### 2.2 Edge: temporal linking

同一細胞を隣接時刻でつなぐ。

```text
cell(t) ─────→ same cell(t+1)
```

### 2.3 Division

細胞分裂は1 parentから2 childrenへの分岐。

```text
              ┌──→ child A
parent ───────┤
              └──→ child B
```

GT division は parent から2本の outgoing edge。metric では prediction node の outgoing edge が2本以上なら predicted fork として扱われる。

### 2.4 最終成果物

必要なのはフレーム独立の detection 集合ではなく、**時系列全体の有向 tracking graph**。

改善対象は大きく4層に分ける。

1. Detection
2. Association / linking
3. Division handling
4. Graph post-processing

---

## 3. データ形式

### 3.1 OME-Zarr image

公式 baseline の基本軸は:

```text
(T, Z, Y, X)
```

- `T`: time
- `Z`: depth
- `Y`: height
- `X`: width

公式 baseline が示す spatial scale:

```text
Z = 1.625 µm / pixel
Y = 0.40625 µm / pixel
X = 0.40625 µm / pixel
```

Z と XY は異方的。**voxel index の単純 Euclidean distance と物理距離を混同しない。**

特に注意すること:

- `(T,Z,Y,X)` の軸順を固定する
- voxel coordinate と physical coordinate を区別する
- crop / resize / augmentation 後の座標変換を追跡する
- 7 µm matching threshold を「7 voxel」と誤解しない

### 3.2 GEFF tracking graph

tracking 正解は `tracksdata` が扱う GEFF graph。

```text
nodes: (t, z, y, x)
edges: temporal relationship
fork:  cell division
```

ローカル想定 layout:

```text
data/
├── train/
│   ├── <dataset>.zarr
│   ├── <dataset>.geff
│   └── ...
└── test/
    ├── <dataset>.zarr
    └── ...
```

---

## 4. このコンペ最大の特殊性: sparse ground truth

**正解アノテーションは全細胞を覆っていない。**

したがって、次の考え方は誤り。

```text
annotation がない
      ↓
細胞ではない
      ↓
negative label
```

未注釈位置にも実在細胞があり得る。未注釈細胞を background とみなして強く罰すると、正しい prediction を学習時に壊す。

公式 baseline も sparse supervision を前提としており、GT edge が存在する部分を中心に学習する。

### 実験上の意味

- unannotated = negative としない
- dense detection 向け loss をそのまま移植しない
- hard negative mining は特に慎重に行う
- local validation は公式 metric を基準にする
- `GTにないprediction = FP` という自作評価を正本にしない
- final score だけでなく TP / FP / FN と predicted node count を見る

---

## 5. 公式評価 metric

公式定義の正本は Royer Lab の `metrics.md`。

### 5.1 Node matching

prediction node と GT node をまず対応付ける。

- timepoint-aware
- centroid distance 最大 **7 µm**
- optimal bipartite assignment
- 1 prediction node は高々1 GT nodeに対応

各 prediction が独立に nearest GT を取る greedy matching ではない。

### 5.2 Edge Jaccard

prediction edge が TP になる条件:

1. source prediction が GT source に match
2. target prediction が GT target に match
3. 対応した GT nodes 間に GT edge が存在

```text
edge_jaccard = TP / (TP + FP + FN)
```

sparse GT のため、評価根拠のない prediction edge は無条件に FP にはならず ignore される場合がある。

### 5.3 Node-count adjustment

「大量に node を出して sparse GT の穴を利用する」ことを抑えるため、node数 penalty がある。

```text
adjusted_jaccard
  = max(0, jaccard * (1 - a * (T_pred - T_true) / T_true))

a = 0.1
```

- `T_pred`: predicted node総数
- `T_true`: annotation数ではなく、total true node count の粗い推定値

つまり **over-detection は無料ではない**。

公式 metric 実装は開催中に修正される可能性があるため、数式の自己解釈より最新 evaluator の実行結果を優先する。

### 5.4 Division Jaccard

GT split の一点だけでなく局所 window を見る。

```text
grandparent
    ↓
dividing parent
   ↙  ↘
child  child
  ↓      ↓
grandchildren
```

predicted fork は GT split の1 timepoint前後でも、局所 topology が正しければ TP になり得る。

重要条件:

- parent側に有効な anchor がある
- 2つの distinct daughter branches を説明できる
- directed topology が正しい
- branch evidence が矛盾しない
- branch が merge / shared されていない
- GT division と predicted fork は one-to-one に割り当てられる

```text
division_jaccard = TP / (TP + FP + FN)
```

単なる `out_degree >= 2` だけでは division TP にならない。

### 5.5 Final score

```text
score = adjusted_edge_jaccard + 0.1 * division_jaccard
```

実務上の基本優先度:

```text
1. node localization を壊さない
2. edge / association を改善
3. predicted node count を適正化
4. division を改善
5. graph post-processing / ensemble
```

---

## 6. 公式 baseline

Royer Lab の公式 baseline は detection と linking をつないだ end-to-end 構成。

### Detection

`TemporalUNet3D`

- 3D U-Net
- temporal attention
- per-voxel features
- single-channel detection map
- local-max suppression で cell centers を取得

### Linking

`SimpleNodeTransformer`

- U-Net feature mapから detected node feature を pool
- cross-attention transformer
- `(t, t+1)` node pairを score

### Sparse supervision

- GT edge がある部分を学習に使用
- unannotated cells を通常の background negative として一律に扱わない

### baseline から考えられる改善方向

- stronger 3D / 4D representation
- better temporal features
- motion prior
- learned association cost
- assignment / graph optimization
- gap handling
- confidence calibration
- division-specific modeling
- model / seed ensemble

baseline をコピーして終わるのではなく、**detection と linking を別々に診断できる状態を作る**ことが重要。

---

## 7. Submission CSV

公式 conversion script の列:

```csv
id,dataset,row_type,node_id,t,z,y,x,source_id,target_id
```

### Node row

```text
row_type = node
node_id,t,z,y,x = 有効値
source_id,target_id = -1
```

### Edge row

```text
row_type = edge
source_id,target_id = 有効 node id
node_id,t,z,y,x = -1
```

公式 `geffs_to_csv.py` は node coordinates を integer に round して submission CSV に書き出す。

開発中は CSV を直接操作するより graph を正本にする。

```text
model output
    ↓
GEFF / graph
    ↓
local validation
    ↓
geffs_to_csv
    ↓
submission.csv
```

提出前には可能なら逆変換も行う。

```text
submission.csv
    ↓
csv_to_geffs
    ↓
graph validation
    ↓
local score / structural checks
```

---

## 8. Code Competition として考える

このコンペは Research Code Competition。最終的には **Kaggle Notebook が hidden test 上で確実に完走し、`submission.csv` を生成できること**が必要。

したがってモデル性能だけでなく以下も設計対象。

- offline execution
- dependency packaging
- model weight packaging
- inference runtime
- peak CPU/GPU memory
- Zarr I/O
- deterministic / reproducible pipeline
- hidden test の dataset数・shape変化への耐性
- failure時に silent empty submission を作らない validation

Kaggle の runtime / internet / external data などの Code Requirements は提出 Notebook を固定する直前に必ず公式ページで再確認する。

---

## 9. Validation 設計

### 原則

1. 公式 metric を使う
2. splitを固定する
3. related sequence の leakage を避ける
4. score内訳を保存する
5. node countを保存する
6. runごとの code/config/seed/checkpointを追跡する

### 最低限保存する値

```text
final_score
adjusted_edge_jaccard
edge TP / FP / FN
division_jaccard
division TP / FP / FN
predicted node count
expected true node count との比
runtime
peak memory
```

scoreが動いたら、最低でも次を切り分ける。

```text
Detection?
Association?
Node-count adjustment?
Division?
Specific dataset failure?
```

---

## 10. 推奨ロードマップ

### Stage 0 — I/O と evaluator

成功条件:

- OME-Zarrを正しい軸/scaleで読める
- GEFFを読める
- official metricをlocalで実行できる
- valid CSVを生成できる
- CSV↔GEFF round-tripが成立

### Stage 1 — official baseline reproduction

成功条件:

- baseline training / inference が動く
- local validation score が再現可能
- runtime / memory を測れる
- prediction graph を可視化できる

### Stage 2 — detection

候補:

- normalization
- augmentation
- receptive field
- temporal context
- local-max / NMS tuning
- confidence calibration
- sub-voxel refinement
- node-count calibration

### Stage 3 — linking

コンペの中心。

候補:

- physical distance prior
- velocity / motion prior
- learned appearance features
- cross-frame transformer
- mutual matching
- assignment optimization
- edge confidence pruning
- global consistency
- short gap handling

### Stage 4 — graph post-processing

- impossible edge removal
- displacement pruning
- short-track handling
- gap closing
- conflict resolution
- global optimization

post-processing は必ず独立 ablation する。

### Stage 5 — division

edgeが安定してから:

- fork candidate generation
- division confidence
- daughter compatibility
- temporal tolerance
- topology validation

### Stage 6 — ensemble / submission engineering

- multiple seeds
- detector / association blending
- robust thresholds
- offline dependency packaging
- runtime headroom
- final notebook rehearsal

---

## 11. よくある事故

| 事故 | なぜ危険か |
|---|---|
| sparse GT を dense GT と扱う | 正しい未注釈細胞まで negative にする |
| voxel と µm を混ぜる | matching / motion threshold が壊れる |
| `(T,Z,Y,X)` を誤る | 全座標・model input が壊れる |
| node を大量に出せばよいと思う | node-count adjustment がある |
| greedy matching を evaluator にする | official は optimal bipartite assignment |
| division を out-degree だけで判定 | official metric は局所 topology を見る |
| Public LB を validation にする | leaderboard overfit を起こす |
| 一度に多数の変更を入れる | 何が効いたか分からない |
| offline制約を最後まで無視 | Kaggle Notebookで本番だけ落ちる |
| 古い metric / data を使う | 開催中に evaluator / data 更新が起こり得る |

2026-08-19 時点でも Kaggle Discussion には metric patch、rescoring、training data version、CV/LB mismatch に関する議論がある。**古い notebook の数字を正典にしない。**

---

## 12. このリポジトリ内の文書

| 文書 | 役割 |
|---|---|
| `docs/COMPETITION_GUIDE.md` | コンペ仕様・データ・metric・全体戦略（この文書） |
| `docs/EXPERIMENT_PLAYBOOK.md` | 実験設計、比較、記録、採否判断 |
| `docs/SUBMISSION_CHECKLIST.md` | Kaggle提出前の検証 |
| `AGENTS.md` | Codex / Claude等の共通開発規約 |
| `README.md` | Docker開発環境の入口 |
| `data/README.md` | competition dataの扱い |

---

## 13. 公式参照先

最優先:

- Kaggle competition: https://www.kaggle.com/competitions/biohub-cell-tracking-during-development
- Official Royer Lab baseline: https://github.com/royerlab/kaggle-cell-tracking-competition
- Official metric: https://github.com/royerlab/kaggle-cell-tracking-competition/blob/main/metrics.md
- tracksdata: https://github.com/royerlab/tracksdata

重要な変更前・提出前に確認する順序:

1. Kaggle Overview / Evaluation / Rules / Code Requirements
2. Kaggle Discussion の pinned / organizer posts
3. official repository の `metrics.md` と recent changes

---

## 14. 一文でまとめる

> **Sparse annotation の3D+time microscopyに対し、適切な数のcell centerを検出し、物理距離と時系列特徴で正しくlinkし、divisionを含むlineage graphを作る。勝負の中心はedge qualityであり、公式sparse-aware metricとCode Competitionの実行制約まで含めて最適化する。**
