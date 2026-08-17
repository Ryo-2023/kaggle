# Native-Preserving Meta-Overfit Design

> status: research-only design; no execution, training, promotion, or submission authority

## Goal

既存の native `deck + agent` population を immutable な基準として保持し、
`META_TRAIN` の actor-visible rollout outcome だけから hard-negative を再加重し、
bounded public-state advantage を native policy に重ねる。deck mutation と policy update を
別armで交互評価し、native BestKnown を共通24 opponent protocolで再現的に超えた候補だけを
再開可能な longrun へ送る。

## Evidence-driven decision

- native common24 pooled1536 は Tomato 1107/1536 (72.0703%)、Lucifer 1103/1536、
  Plamen 1102/1536、fault0。
- Student v3 θ0/AWR は native Tomato にそれぞれ -61.458pt / -65.625pt であり、同じ
  set/AWR 学習を延長しない。
- guarded score-bias は96局のscreen改善が384局で -2.995ptへ反転した。
- Plamen mutation は親には正方向でも Tomato direct control で -0.391pt、Tomato policy
  interaction は +1.042ptに留まり、candidate-onlyとする。

従って次の候補は「native を捨てた学生モデル」ではなく、native fallback を必ず持つ
小さな action-conditioned update とする。

## Scope and non-goals

### In scope

1. public-state digest と stable action key による advantage table。
2. native-first policy wrapper。unknown、低support、非合法、非対応 selection は nativeへ
   exact fallbackする。
3. strict `META_TRAIN` hard-negative update。`META_DEV`/`META_FINAL` は重み更新にも候補
   選択にも使わない。
4. existing deck mutation generator と alternating optimizer の状態・SHA・rollback連携。
5. 96→384→768→1536 の共通24 protocol gate と longrun preflight。

### Out of scope

- native `main.py`、Rule v0、既存提出 archive の編集。
- local-eval-only asset を teacher/behavior source として使うこと。
- private hand、deck reveal、opponent private state を feature に入れること。
- Student v3 set model の再学習、R2D3/PSRO cold bootstrap、Kaggle submission。

## Architecture

### 1. Immutable asset and meta manifest

既存 `meta_distribution_v1` と ranking artifact を source SHA として固定する。各 iteration は
次を束ねる。

```text
native baseline (deck SHA, policy SHA, evaluator SHA)
meta manifest + schedule SHA
selected META_TRAIN opponent IDs and family caps
candidate deck/policy identity
previous iteration and rollback checkpoint
```

`META_DEV` は validation 専用、`META_FINAL` は最終確認専用で、iteration outcome に出現した
時点で fail-closed とする。

### 2. Public advantage table

新規 module `native_public_advantage_v1.py` は、既存 native wrapper と独立した純粋な
artifact/lookup責務を持つ。

```python
build_public_advantage_table_v1(
    *,
    source_rows_path: str | Path,
    meta_manifest_path: str | Path,
    baseline_policy_sha256: str,
    iteration: int,
    delta_cap: float = 0.25,
    min_support: int = 4,
) -> PublicAdvantageTableV1
build_native_public_advantage_policy_v1(
    *,
    native_agent: Callable[[dict[str, Any]], Sequence[int]],
    table: PublicAdvantageTableV1,
    baseline_policy_sha256: str,
    candidate_config_sha256: str,
) -> NativePreservingPolicyV1
```

入力 row は canonical `state_digest`, `action_key`, `opponent_id`, `seat`, `split`,
`outcome`, `weight` のみを許可する。`split` は `META_TRAIN` 固定、weight は有限正値、
state/action key は SHA または Stable ActionKey の closed schema とする。private情報名を
含む row key は拒否する。

state-action平均から state平均を引いた値を shrinkage し、`[-delta_cap, delta_cap]` に
clampする。support未満の action は未適用とする。table は canonical JSON/no-newlineで
SHAを持ち、catalog、meta manifest、native policy SHAをbindする。

初期実装は single-choice `MAIN` selection のみを上書き対象とし、multi-select、ordered
selection、duplicate/alias action は native fallbackする。これは性能上の制限ではなく、
stable action identity と合法性を閉じてから対象範囲を広げるための段階契約である。

### 3. Hard-negative curriculum

既存 `dynamic_meta_train_curriculum_v1` を再利用し、strict adapterから得た outcomeだけを
入力する。opponent weight は以下を deterministic に合成する。

```text
0.40 * normalized loss/hard-negative score
0.20 * seat imbalance correction
0.15 * under-exposure correction
0.15 * family diversity floor
0.10 * reliability (1 - fault rate)
```

per-opponent cap、family floor、quota合計、seed、previous iteration SHAをmanifestに保存し、
同じ入力から同じ scheduleを再生成できるようにする。fault row は性能勝ちとして数えず、
reliability penalty のみに使う。

### 4. Alternating deck/policy state

existing `alternating_meta_optimizer_v1` を orchestration contract として使用する。

- `POLICY_FIXED_SHORT`: deckを固定し、advantage table/policy configだけを変える。
- `DECK_FIXED_LONG`: policyを固定し、legal deck mutationだけを変える。
- 各stateに native baseline arm、candidate identity、meta manifest/schedule SHAを保持する。
- stageは `96, 384, 768, 1536` 以外を許可しない。
- candidateとnativeを同じ common24 strataで評価し、fault0・両seat・protocol一致を必須にする。

### 5. Longrun adapter

新規 iteration runner は初期状態では dry-run とし、実 executor は明示 adapter経由でのみ
渡す。各 iteration は次を atomic に保存する。

```text
iteration manifest
curriculum manifest
advantage table
candidate deck/policy identities
evaluation summaries and strict outcome ledger
checkpoint / previous checkpoint / rollback reason
```

以下をすべて満たさない限り `LONGRUN_READY` にしない。

- candidate が native より pooled +3pt 以上（384局で予備、768/1536で確認）
- 両seatの差が事前閾値以内、fault0、unknown fallback率を報告済み
- META_DEV clean、META_FINAL exposure0
- package entrypoint、qualified deck、CABT legality/runtime closureがSHA固定
- checkpoint/resume/rollbackを実データまたは同型fixtureで検証

## Failure handling

- table/source/meta SHA不一致は即時 reject。
- invalid action、unknown state、model例外、nonfiniteは握りつぶさず、native fallback対象と
  する例外だけを限定処理する。
- fallback coverageが事前下限を下回る、または片seatだけ改善する場合は candidate-only。
- native regressionが2 iteration連続したら longrun stateを自動停止し、直前checkpointへ
  rollbackする。
- 途中artifactは新規run rootにのみ書き、既存性能artifactを上書きしない。

## Verification and acceptance

### Unit / contract

- public/private feature boundary、canonical digest、stable action key、delta cap、support。
- exact native fallback、合法性、multi-select/ordered quarantine。
- heldout `META_DEV`/`META_FINAL` 混入拒否、duplicate/foreign record拒否。
- table/manifest/checkpoint SHA、atomic write、resume/rollback。

### Performance stages

1. synthetic で table と fallback の deterministic test。
2. Tomato native common24 96局 screen。
3. positive signal のみ384局、seed-disjoint block。
4. +3pt and seat/fault gates通過時のみ768、続いて1536。
5. deck mutation armは同一policy controlと別に評価し、交互stateへ反映。

### Deliverables

- machine-readable candidate/table/curriculum/iteration manifests と各SHA。
- evidence docに BestKnown、各arm、fault/seat/protocol、permission/package、longrun判定。
- `current_status.md` と `handoff.md` の追補。
- 実装・学習・CABT・提出を行ったかどうかを明示。

