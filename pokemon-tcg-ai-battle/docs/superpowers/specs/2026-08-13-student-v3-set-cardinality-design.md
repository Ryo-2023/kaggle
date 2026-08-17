# Generic Student v3 Set+Cardinality Design

## 目的

sealed derived-teacher decision を一件も黙って削除せず、unordered CABT selection の
`min_count <= selected_count <= max_count` と optional decline (`selected_count=0`) を
同一意味論で学習・推論できる generic policy を追加する。成果物は
`DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY` であり、BestKnown超過やhard BC性能を
主張しない。public-state value / AWR 接続前の self-owned θ0 初期値に限定する。

既存 Student v2、production agent、Champion は変更しない。V2 bridge は formal catalog
loader の状態語彙と不整合だった `SEALED` hardcode を除去し、`READY` を検証済みloader
結果として受け取る最小互換修正だけを行う。

## Architecture

1. **V3 source bridge**
   - derived teacher catalog、decision、policy、deck、permission、snapshot、raw recordを
     strict SHAで再検証する。
   - 1 decision = 1 JSONL row とし、`RuleBCExample.target_action_digests` をset target、
     その長さをcount targetとして保持する。
   - canonical `record_id` をdecision identityとして保持し、teacher catalogの
     `source_kind` / policy SHAとraw recordをcross-bindする。
   - unorderedのzero/variable/fixed multiを全て保持する。
   - ordered selectionまたはtarget ActionKey alias衝突が1件でもあれば全datasetを
     fail-closedにし、件数とselection schemaをmanifestへ記録する。
2. **GPU dataset**
   - state tensor、ragged legal-action tensor、legal mask、binary target-set mask、
     target_count、min_count、max_countをhash-bound `.pt` shardへ変換する。
   - episodeはteacher-stratified 5 split間を跨がない。
3. **Set+cardinality model**
   - shared state/action encoderとjoint action scorerを持つ。
   - masked action poolingとstate embeddingからcount logits `0..Kmax` を生成する。
   - lossはlegal actionだけのset BCEと、`min..max`だけを許可したcount CEの和。
4. **Runtime**
   - count logitsを現在の`min..max`へmaskし、argmaxした`k`を使用する。
   - action score上位k件を選び、同点はStable ActionKey digest、option index順。
   - `k=0`を明示的な空selectionとして返す。
   - ordered selectionはpointer headが無いため例外でfail-closedする。

## Data Contract

source schemaは`offline-scaleup-student-v3-set-source-v1`。各rowは次を必須とする。

- episode/split identity
- actor-visible `rule_bc_example`
- terminal outcomeとsealed duplicate-cap quality weight
- teacher policy/deck/catalog/snapshot SHA provenance
- `purpose=DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY`
- training/promotion/submission authority false
- canonical `record_id`（multi-selectもreplica化しない）

GPU dataset schemaは`offline-scaleup-gpu-set-dataset-v1`。各shardは次を持つ。

- `state: float32[N, STATE_FEATURE_DIM]`
- `actions: float32[M, ACTION_FEATURE_DIM]`
- `offsets: int64[N+1]`
- `target_set: bool[M]`
- `target_count/min_count/max_count: int64[N]`
- metadata（モデル入力にはしない）

source JSONL SHA、各shard SHA、feature schema、最大count class、episode leakageを
manifestへ固定する。

外部weight sidecarは`record_id`でtrain splitへ完全一致joinする。missing、duplicate、
extra（validation等の非trainを含む）、非finite、非positive weightはfail-closedとする。
sidecar無しは`THETA0_PRETRAIN`、有りは`AWR_FINE_TUNE`であり、summaryへcatalog / dataset /
sidecar SHA、weight mass、ESSを保存する。

## Model and Loss

`SetCardinalityRanker`はcandidate順序へ依存しない。

- state encoder: `STATE_FEATURE_DIM -> hidden`
- action encoder: `ACTION_FEATURE_DIM -> hidden`
- joint trunk: `state + action + state*action`
- action head: candidateごとのlogit
- pooled head: masked mean + masked max + stateからcount logits

set lossはcandidateごとのBCEをlegal countで正規化し、count lossは現在の
`min_count..max_count`以外を`-inf`へmaskしたcross entropyとする。非法count target、
empty legal set、ordered schema、non-finite lossは例外にする。

## Checkpoint Contract

checkpoint schemaは`offline-scaleup-student-v3-set-checkpoint-v1`。dataset manifest SHA、
catalog SHA、任意のweight sidecar SHA、model/training config SHA、purpose、objective kind、
epoch、model/optimizer state、best validation exact-set fidelityを
持つ。resume時はschema、dataset SHA、config SHA、purposeを完全一致させる。`best.pt`と
`last.pt`のSHAをtraining summaryへ記録し、runtimeはbest SHAを再検証してからloadする。

## Tests

- source rowからzero/variable/fixed multi targetをlossless変換
- ordered actual countがあればdataset全体を未生成
- legal count mask、set BCE、permutation invariance
- runtime k=0、variable/fixed multi、Stable ActionKey tie-break、exact legal indices
- malformed/ordered/non-finite fail-closed
- synthetic tiny-overfitでtotal/set/count lossが低下
- strict checkpoint/config/dataset SHA、resume
- CPU/GPU decode parity（CUDAが無い環境ではskip理由を明示）
- weight sidecarのtrain完全一致join、mass/ESS、非train weight拒否

実teacher性能学習、CABT、Champion変更、package、提出は本設計の範囲外。
2026-08-13のcollector integrity監査により、legacy omission ledgerを失いうる旧collectionは
学習入力NO-GOである。V3実装とsynthetic probeは継続するが、実6-teacher dataset生成は
collector v2でfresh再収集・resealされたcatalog SHAが発行されるまで行わない。
