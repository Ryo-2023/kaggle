# Biohub Experiment Playbook

> **役割:** Biohub – Cell Tracking During Development で、比較可能な実験を継続的に回すための運用規約。  
> コンペ仕様そのものは [`COMPETITION_GUIDE.md`](COMPETITION_GUIDE.md)、AI共通ルールは [`../AGENTS.md`](../AGENTS.md) を参照する。

---

## 1. この文書の目的

このコンペでは、モデルを複雑にすることよりも、**何が効いたかを壊さず追跡できること**が重要。

実験は常に次のループで回す。

```text
Baselineを固定
    ↓
1つの仮説を立てる
    ↓
最小変更
    ↓
同条件で評価
    ↓
内訳を比較
    ↓
採用 / 棄却 / 保留
    ↓
次の仮説
```

禁止する状態:

```text
変更A + 変更B + 変更C
        ↓
score +0.02
        ↓
何が効いたか不明
```

---

## 2. 実験の基本単位

1 experiment = **1つの主仮説**。

例:

- detection thresholdを下げると edge recall が改善するか
- physical-distance priorを association score に加えると edge FP が減るか
- temporal contextを増やすと fast-moving cells の linking が改善するか
- division pruning が division FP を減らし、edge scoreを壊さないか

複数変更が不可分な場合は、その理由を実験記録に明記する。

---

## 3. Baseline の定義

新しい系統の実験を始める前に、比較対象を固定する。

最低限固定する項目:

```text
code commit / branch
model architecture
checkpoint
train split
validation split
seed
preprocessing
augmentation
post-processing
inference thresholds
evaluator version
```

Baselineは「以前なんとなく良かった設定」ではなく、**再実行可能な状態**であること。

---

## 4. 実験ID

推奨形式:

```text
YYYYMMDD-<area>-<short-name>-<nn>
```

例:

```text
20260820-link-distance-prior-01
20260820-det-threshold-sweep-01
20260821-div-fork-pruning-01
```

実験 directory や artifact 名にも同じIDを使う。

---

## 5. 実験前に書くこと

実験開始前に最低限これを決める。

```markdown
## Hypothesis
何を変えると、なぜ、どのmetricが改善すると考えるか。

## Change
baselineから何を変えるか。

## Fixed conditions
何を固定するか。

## Primary metric
この実験の採否を決める主metric。

## Guardrails
改善しても悪化させてはいけないmetric / runtime / memory。
```

### 良い仮説の例

> Association候補を7 µm近辺だけで切るのではなく、velocity-conditioned physical-distance costを追加すると、fast-moving cells のFNを増やさずに誤link FPを減らせる。

### 悪い仮説の例

> Transformerを強くする。

後者は「何が」「なぜ」「どこに効くか」が曖昧。

---

## 6. 必ず保存する結果

### 6.1 Score

```text
final_score
adjusted_edge_jaccard
edge TP
edge FP
edge FN
division_jaccard
division TP
division FP
division FN
```

### 6.2 Prediction量

```text
predicted node count
predicted edge count
predicted fork count
node count / expected true node count
```

### 6.3 Resource

```text
wall-clock runtime
inference runtime / dataset
peak RAM
peak VRAM（GPU時）
```

### 6.4 Provenance

```text
git commit
command
config
seed
split
checkpoint
artifact path
evaluator revision
```

---

## 7. scoreの読み方

Final scoreだけで採否を決めない。

### Pattern A: edge TP↑ / FPほぼ同じ

良い改善候補。link recallが上がっている可能性。

### Pattern B: edge TP↑ / FPも大幅↑

thresholdを緩めただけの可能性。node count penaltyも確認する。

### Pattern C: edge Jaccard↑ / node数過多

hidden testで分布が変わると危険。node count calibrationを確認する。

### Pattern D: division↑ / edge↓

division weightは0.1なので、多くの場合トータルでは割に合わない。

### Pattern E: average↑ / 特定datasetで崩壊

micro aggregationに隠れている可能性。dataset別結果を必ず見る。

---

## 8. Dataset別診断

各validation datasetについて最低限以下を保存する。

```text
sample name
edge TP / FP / FN
division TP / FP / FN
node count
runtime
```

score改善時にも「どこが改善したか」を見る。

分類例:

```text
small motion
large motion
high density
low density
many divisions
few divisions
weak signal
strong signal
```

正式なカテゴリが無ければ、可視化と統計から後でタグ付けしてよい。

---

## 9. Detection実験

主に見るもの:

```text
node localization
predicted node count
edge TP/FNへの波及
node-count adjustment
```

候補:

- normalization
- augmentation
- temporal context
- receptive field
- heatmap target design
- local-max suppression
- confidence threshold
- NMS radius
- sub-voxel refinement

### 注意

sparse GTなので、未注釈cellをnegativeとしてprecisionを計算する自作指標を正本にしない。

---

## 10. Linking実験

このコンペの中心。

主に見るもの:

```text
edge TP / FP / FN
motion distance
association ambiguity
track fragmentation
```

候補:

- physical-distance cost
- velocity prior
- learned node feature
- temporal attention
- pairwise transformer
- assignment algorithm
- mutual consistency
- confidence pruning
- gap closing

### 推奨diagnostic

可能なら次を分離して評価する。

```text
A. predicted detections + predicted linking
B. fixed / oracle-like detections + predicted linking
C. predicted detections + simple linking
```

これにより「検出が悪い」のか「associationが悪い」のかを切り分ける。

---

## 11. Division実験

edge系が安定してから着手する。

見るもの:

```text
division TP / FP / FN
fork timing
branch separation
merge / shared-child error
edge scoreへの副作用
```

候補:

- fork confidence
- daughter compatibility
- parent-state feature
- temporal tolerance
- local topology check
- child-branch merge rejection

Final scoreではdivisionの重みが小さいので、**edgeを壊すdivision改善は原則採用しない**。

---

## 12. Post-processing実験

post-processingはモデル変更と混ぜない。

候補:

- impossible displacement pruning
- edge confidence pruning
- conflict resolution
- track fragment cleanup
- gap closing
- graph optimization
- fork pruning

threshold sweepをする場合も、validationに過適合しないよう範囲を事前に決める。

---

## 13. Sweepのルール

ハイパーパラメータ sweep は「良い数字が出るまで回す」ではなく、仮説に基づく範囲を先に決める。

例:

```text
threshold ∈ {0.20, 0.30, 0.40, 0.50}
```

記録する:

- 全pointの結果
- 選択理由
- bestだけでなくcurve
- compute cost

Public LBでthresholdを細かく合わせない。

---

## 14. Seedの扱い

小さな差を採用するときはseed依存を確認する。

目安:

- 大差: 1 seedでscreen → 追加確認
- 小差: 複数seed確認
- 最終候補: 少なくとも再実行可能性を確認

「best seedだけをbaselineと比較」は禁止。

---

## 15. Public Leaderboardの扱い

Public LBはvalidationではなく**外部sanity check**。

使い方:

```text
local hypothesis
    ↓
local CVで採否
    ↓
十分に強い候補だけLB確認
```

避ける:

```text
LBを見る
 ↓
thresholdを微調整
 ↓
LBを見る
 ↓
さらに微調整
```

これはPublic subsetへのoverfitになる。

---

## 16. 採用基準

変更を採用する基本条件:

1. primary metricが改善
2. guardrail metricを大きく壊さない
3. runtime / memoryが本番制約に収まる
4. 改善理由が説明可能
5. 再現可能
6. 特定sample / seedだけの偶然でない

### 採用

```text
ADOPT
```

baselineを更新し、次実験の比較対象にする。

### 棄却

```text
REJECT
```

コードを無理に残さない。失敗理由は記録する。

### 保留

```text
HOLD
```

追加検証が必要。採用済みとして扱わない。

---

## 17. 実験ログの推奨テンプレート

```markdown
# <experiment-id>

## Hypothesis

## Baseline
- commit:
- config:
- checkpoint:
- split:
- seed:

## Change

## Command

## Results
| metric | baseline | candidate | delta |
|---|---:|---:|---:|
| final score | | | |
| adjusted edge Jaccard | | | |
| edge TP | | | |
| edge FP | | | |
| edge FN | | | |
| division Jaccard | | | |
| predicted nodes | | | |
| runtime | | | |

## Dataset-level findings

## Interpretation

## Decision
ADOPT / REJECT / HOLD

## Next action
```

---

## 18. AI Agentへの指示

Codex / Claude等は次を守る。

- baselineを確認せず大規模変更しない
- 実験前にhypothesisを明文化する
- 1 experiment 1主仮説
- scoreだけでなく内訳を報告する
- 改善していない結果も隠さない
- 実行していない結果を推測で埋めない
- Public LBを最適化目標にしない
- sparse GT前提を壊すloss / metricを勝手に導入しない
- official evaluatorの変更を検知したら評価の再計算影響を報告する

---

## 19. 最終方針

> **強いKaggle開発は「アイデア数」ではなく、「比較可能な実験を高速に回し、間違った方向を早く捨てられること」で作る。Biohubでは特に sparse GT・linking・node-count penalty を分解して診断する。**
