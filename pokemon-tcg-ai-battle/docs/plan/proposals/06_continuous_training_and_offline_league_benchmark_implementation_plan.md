---
project: MAGE-PTCG
document_status: implemented
canonical_source: git
initial_source: git
initial_sync_date: 2026-07-30
implementation_date: 2026-07-30
language: ja
title: 06｜継続学習・オフラインリーグベンチマーク｜実装案
---

# 継続学習・オフラインリーグベンチマーク実装案

## 1. 目的

本書は、[設計案](06_continuous_training_and_offline_league_benchmark_plan.md)を現在のO5／O6／R2D3／PSRO実装へ接続するための責務、Artifact、schema、CLI、Gate、テストを定める。

実装の中心は新しい学習アルゴリズムではない。既存機能を次の9契約で接続する。

1. `OpponentCatalogSnapshot`
2. `PopulationSnapshot`
3. `ExperienceCollectionManifest`
4. `ReplayDatasetVersion`
5. `PublishedCheckpoint`
6. `RuntimePolicy`
7. `BenchmarkManifest`
8. `EvaluationResult`
9. `PopulationTransition`

Champion、`main.py`、`deck.csv`、Rule Agent v0、Kaggle提出経路は変更しない。

## 2. 深度と前提

本作業はR2である。複数module、長時間process、評価統計、外部source、checkpoint identityを接続するが、提出APIやActorInformationViewの意味論は変更しない。

| 前提 | 確度 | 根拠／確認方法 |
|---|---|---|
| 現行R2D3 checkpointはpopulation／Replay／training identityへ束縛できる | 確実 | `r2d3/checkpoint.py` schema v2 |
| 現行PER priority復元は同一Replay indexのstrict resume向けである | 確実 | `replay.py::load_priority_state`がindex identity完全一致を要求 |
| online collection provenanceとReplay partitionはあるがsealed dataset versionはない | 確実 | `r2d3/online_collection.py` |
| 現行R2D3 checkpointはLR scheduler stateを保存しない | 確実 | `r2d3/checkpoint.py` payload |
| 資格済みTeam／dev assetをCABTで実行できる | 確実 | submitted opponent snapshotと既存smoke |
| remote refをcheckoutせずinventoryできる | 確実 | O6／`opponent_ingest` |
| 任意R2D3 checkpointをO5 CLIへ渡せる | 誤り | O5 candidate registryは固定artifact中心 |
| cabt engine RNGをschedule seedで固定できる | 現状否定 | 2026-07-30 local capability evidence |
| Kaggle提出ごとの`μ`／`σ`をAPI取得できる | 未検証 | read-only Capability Probeが必要 |

## 3. 既存実装の再利用

| 既存実装 | 再利用する責務 | 変更方針 |
|---|---|---|
| `opponent_ingest` | Git ref inventory、deck正規化、static audit、quarantine | source watcherから呼ぶ。自動approvalは追加しない |
| `mage_ptcg.opponents` | runtime closure、permission、隔離実行、privacy gate | qualified native runtimeの実行境界にする |
| `submitted_opponents.py` | asset registry、identity component、4-way split | stable lineageとappend-only role ledgerを追加 |
| `build_deck_opponent_pool.py` | remote／public exact deckの統合 | Catalog importerへ変換し、raw sourceは不変 |
| O5 benchmark | versioned manifest、resume、統計集計 | manifest envelopeと集計関数を再利用 |
| `league.actual_runner` | seat swap、schedule、atomic resume | per-member deckとruntime specを扱えるadapterを追加 |
| R2D3 checkpoint／Replay | model、optimizer、RNG、PER durability | publish eventとepoch transitionを追加 |
| `r2d3/online_collection.py` | frozen mixture、collection provenance、offline／online partition | Experience Generatorのrecord契約へ再利用し、Replay Sealerを追加 |
| `r2d3/psro.py` | payoffからmeta strategy、expansion guard | 外側のPSRO Population Managerから呼び、learner内へ埋め込まない |
| R2D3 performance controller | resource probe、15 stage、holdout Gate | finite experimentとして維持し、continuous controllerから直接importしない |
| `analyze_leaderboard_decks.py` | leaderboard／public Episode deck取得 | network intake jobとして分離し、cacheをGit外へ置く |

既存script内のprivate関数を新daemonからimportしない。R2D3 runtime loadやscore集計など再利用が必要な部分だけ、`src/`配下の公開moduleへ最小抽出する。

## 4. module構成

```text
src/mage_ptcg/continuous_league/
├── __init__.py
├── contracts.py             # schema、canonical hash、typed error
├── catalog.py               # immutable opponent catalog snapshot
├── role_ledger.py           # append-stable split assignment
├── benchmark.py             # Anchor／Rolling／Out-of-Training manifest
├── experience.py            # collection job、raw episode chunk
├── replay_sealer.py         # chunk検証、ReplayDatasetVersion発行
├── candidate_runtime.py     # runtime policy -> CPU argmax Agent
├── checkpoint_stream.py     # publish／discover／verify
├── evaluation.py            # job作成、実行、集計
├── scheduler.py             # priority、dedupe、backpressure、lease
├── population_epoch.py      # proposal、transition、rollback
├── psro_manager.py          # payoff -> mixture／best-response request
├── calibration.py           # offline vector -> online rating
├── report.py                # JSON／Markdown summary
└── controller.py            # event loop、state transitionのみ

scripts/continuous_league.py  # 単一CLI entrypoint
configs/continuous_league/
├── default.yaml
├── source_permissions.yaml
├── benchmark_anchor_v1.yaml
└── population_sampling_v1.yaml
```

`controller.py`はremote code、cabt、Torch modelをin-processでロードしない。job subprocessの終了statusとArtifactだけを読む。

## 5. Artifact layout

長時間ArtifactはGit管理外のdurable rootへ置く。`/tmp`を既定にしない。

```text
<artifact_root>/
├── controller/
│   ├── lease.json
│   ├── event_log.jsonl
│   ├── queue.json
│   ├── progress_summary.json
│   └── scheduler_health.json
├── sources/
│   ├── snapshots/<source_snapshot_hash>/
│   └── quarantine/<asset_id>/
├── catalog/
│   ├── role_ledger.json
│   └── snapshots/<catalog_hash>.json
├── populations/
│   └── <population_hash>.json
├── experience/
│   ├── collections/<collection_manifest_hash>/
│   │   └── chunks/<chunk_hash>.jsonl
│   └── sealed_replay/<replay_dataset_version>/
│       ├── manifest.json
│       └── partitions/
├── benchmarks/
│   └── <benchmark_id>/<manifest_hash>.json
├── training/
│   └── <campaign_id>/epochs/<epoch_id>/
│       ├── epoch_manifest.json
│       ├── transition_manifest.json
│       └── checkpoints/
├── checkpoint_events/
│   └── <training_checkpoint_id>.json
├── runtime_policies/
│   └── <runtime_policy_id>/
│       ├── manifest.json
│       └── model_weights.pt
├── evaluations/
│   └── <manifest_hash>/<runtime_policy_id>/
│       ├── job.json
│       ├── games.jsonl
│       ├── result.json
│       └── status.json
├── calibration/
│   └── <calibrator_hash>/
├── psro/
│   ├── payoff_snapshots/
│   └── best_response_requests/
└── reports/
    ├── latest.json
    └── latest.md
```

同一IDの異内容は拒否し、同一内容の再書込みはidempotentとする。可変aliasは`latest.json`だけに限定し、実行入力には使わない。

## 6. 共通identity

### 6.1 `logical_agent_id`

logical Agentをbranch commitから独立して識別する。

```text
team:<namespace>/<agent-name>
dev:<snapshot-root>/opponents/<name>
public-deck:<canonical-deck-hash>
self:<campaign-id>/<checkpoint-hash>
stress:<stress-kind>/<version>
```

同じAgentの更新commitは同じ`logical_agent_id`と`lineage_root_id`を持ち、別`asset_version_id`を持つ。

### 6.2 hash

canonical JSONはUTF-8、key sort、NaN／Infinity禁止、pathの相対化を共通契約にする。hash domainを必ず分ける。

| Artifact | domain |
|---|---|
| Catalog | `continuous-league-catalog-v1` |
| Population | `continuous-league-population-v1` |
| Collection | `continuous-league-experience-collection-v1` |
| Sealed Replay | `continuous-league-replay-dataset-v1` |
| Benchmark | `continuous-league-benchmark-v1` |
| Training checkpoint event | `continuous-league-checkpoint-event-v1` |
| Runtime policy | `continuous-league-runtime-policy-v1` |
| Evaluation result | `continuous-league-evaluation-v1` |
| Transition | `continuous-league-population-transition-v1` |

### 6.3 training checkpointとruntime policy

`training_checkpoint_id`は学習再開のidentityであり、model／target／optimizer／scheduler／RNG／PER state／population／Replay version／stepを含む。`runtime_policy_id`は対戦時の挙動identityであり、model state／architecture／state encoder／action encoder／action mode／distributional Q reduction／tie-break／deck／runtime configだけを含む。

model state hashはTorch保存fileのbyte列ではなく、parameter名順のdtype、shape、contiguous tensor bytesから計算する。`model_weights_file_sha256`はArtifact破損検出用として別に持ち、Torch serialization metadataの差で同一policyが別IDにならないようにする。

Evaluatorは`runtime_policy_id + benchmark_manifest_hash`で重複排除する。同じmodel weightsでもdeck、encoder、action mode、tie-breakが異なれば別policyであり、optimizerやReplayだけが異なって挙動が同一なら同じpolicyである。

## 7. `OpponentCatalogSnapshot`

### 7.1 entry schema

```json
{
  "schema": "opponent-catalog-entry-v1",
  "logical_agent_id": "dev:origin-dev/opponents/meta_1_cddce6bc",
  "asset_version_id": "asset-...",
  "lineage_root_id": "lineage-...",
  "source_kind": "DEV_SNAPSHOT_NATIVE",
  "source_ref": "origin/dev",
  "source_commit": "a4b1f2...",
  "source_path": "opponents/meta_1_cddce6bc/main.py",
  "source_blob_sha256": "...",
  "deck_hash": "...",
  "deck_archetype_id": "...",
  "deck_blob_sha256": "...",
  "policy_hash": "...",
  "policy_lineage_id": "...",
  "runtime_closure_hash": "...",
  "entrypoint": "main.py:agent",
  "fidelity": "NATIVE",
  "permission_scopes": ["evaluation", "training_data_generation"],
  "qualification": {
    "status": "QUALIFIED",
    "cabt_version": "...",
    "games": 8,
    "seat_coverage": [0, 1],
    "illegal": 0,
    "exception": 0,
    "timeout": 0,
    "evidence_hash": "..."
  },
  "supersedes_asset_version_id": null
}
```

`qualification.status`は`DISCOVERED`、`QUARANTINED`、`REVIEW_REQUIRED`、`QUALIFIED_EVAL`、`QUALIFIED_TRAINING`、`REJECTED`のいずれかとする。評価資格だけでtrainingへ流さない。

Catalog上の階層は`deck_hash → policy_hash／policy_lineage_id → opponent_instance_id`とする。同一deckへ複数policy bindingを作ってもdeck coverageは1として数える。

### 7.2 Catalog snapshot

```json
{
  "schema": "opponent-catalog-snapshot-v1",
  "created_at": "...",
  "source_snapshot_hashes": ["..."],
  "entries": ["asset-version-id-1", "asset-version-id-2"],
  "role_ledger_hash": "...",
  "catalog_hash": "..."
}
```

表示時刻は`catalog_hash`から除外する。member orderは`asset_version_id`でsortする。

## 8. append-only role ledger

現行の`random.shuffle(groups)`による全再分割は、新asset追加時に既存roleを動かしうる。常設運用では次へ置換する。

```json
{
  "schema": "opponent-role-ledger-v1",
  "split_seed": 71000,
  "assignments": [
    {
      "component_id": "component-...",
      "lineage_root_ids": ["..."],
      "policy_hashes": ["..."],
      "deck_hashes": ["..."],
      "role": "TRAINING_ACTIVE",
      "assigned_at_catalog_hash": "...",
      "assignment_reason": "HASH_QUOTA"
    }
  ]
}
```

### 8.1 assignment algorithm

1. policy、deck、lineage root、parent関係でconnected componentを構築する。
2. 既存assignmentをcomponentへ伝播する。
3. 異なる既存roleが一componentへmergeした場合は自動解決せず`ROLE_COLLISION`で停止する。
4. 未割当componentを`hash(split_seed, component_id)`順に並べる。
5. training／development／deck holdout／final holdoutの不足quotaへ順に割り当てる。
6. ledgerをappendしてhashを更新する。

既存assignmentの書換えはmigration manifestと人間承認を要求する。

## 9. `PopulationSnapshot`

```json
{
  "schema": "continuous-league-population-v1",
  "population_id": "population-...",
  "population_epoch": 3,
  "catalog_hash": "...",
  "members": [
    {
      "asset_version_id": "...",
      "logical_agent_id": "...",
      "component_id": "...",
      "runtime_closure_hash": "...",
      "deck_hash": "...",
      "policy_hash": "...",
      "bucket": "team_native",
      "sampling_weight": 0.0
    }
  ],
  "sampling": {
    "mode": "bucket_then_policy_hash_uniform",
    "bucket_weights": {
      "anchor": 0.2,
      "team_native": 0.3,
      "rolling_meta": 0.2,
      "historical_snapshot": 0.2,
      "stress": 0.1
    }
  },
  "semantic_feature_version": "...",
  "population_hash": "..."
}
```

上記weightは初期config例であり、実験結果なしに正典値へ固定しない。各bucket内はpolicy hash重複を除き、同一Agentのコピー数でweightが増えないようにする。

## 10. Experience collectionとReplay sealing

### 10.1 `ExperienceCollectionManifest`

```json
{
  "schema": "experience-collection-manifest-v1",
  "population_hash": "...",
  "behavior_runtime_policy_id": "...",
  "subject_deck_hash": "...",
  "sampling_config_hash": "...",
  "semantic_feature_version": "...",
  "target_games": 256,
  "collection_epoch": 12,
  "manifest_hash": "..."
}
```

Experience Generatorはmanifestを途中変更せず、完結したgameごとにraw episode chunkを追記する。各chunkはgame key、behavior policy、opponent instance、deck／policy／lineage／archetype exposure、terminal reason、ActionKey sequenceを持つ。learnerはraw chunkを直接読まない。

### 10.2 `ReplayDatasetVersion`

```json
{
  "schema": "replay-dataset-version-v1",
  "parent_versions": ["..."],
  "collection_manifest_hashes": ["..."],
  "accepted_chunk_hashes": ["..."],
  "rejected_chunks": {},
  "sequence_builder_config_hash": "...",
  "semantic_feature_version": "...",
  "population_hash": "...",
  "partitions": [
    {
      "partition_id": "epoch-3-collection-12",
      "record_count": 0,
      "manifest_hash": "..."
    }
  ],
  "replay_dataset_version": "..."
}
```

Replay Sealerはepisode完結性、legal ActionKey、behavior identity、feature version、terminal statusを検証してversionを発行する。新version発行後も親versionとraw chunkは変更しない。学習healthには`new_episodes_per_hour`、`sealed_sequences_per_hour`、`replay_age_distribution`、partition別sample率を出す。

### 10.3 R2D3とPSROの境界

- R2D3 learnerはtraining requestに指定されたpopulation、Replay version、sampling configでgradient updateする。
- PSRO managerはpayoff snapshotからmeta strategyを計算し、`best_response_request`またはpopulation proposalを発行する。
- PSRO managerはReplayをsealせず、optimizer／gradient loop／checkpoint保存を行わない。
- best-response完了後のpopulation追加は`PopulationTransition` Gateを通す。

## 11. `PublishedCheckpoint`

### 11.1 payload

```json
{
  "schema": "published-checkpoint-v1",
  "campaign_id": "r2d3-continuous-v1",
  "population_epoch": 3,
  "checkpoint_path": "training/.../r2d3-step-00150000.pt",
  "checkpoint_sha256": "...",
  "training_checkpoint_id": "...",
  "global_step": 150000,
  "epoch_step": 25000,
  "parent_training_checkpoint_id": "...",
  "population_hash": "...",
  "replay_dataset_version": "...",
  "training_identity_hash": "...",
  "semantic_feature_version": "...",
  "runtime_policy": {
    "runtime_policy_id": "...",
    "model_state_hash": "...",
    "model_weights_file_sha256": "...",
    "model_family": "r2d3",
    "model_config": {
      "recurrent_core": "gru",
      "hidden_size": 256
    },
    "state_encoder_version": "...",
    "action_encoder_version": "...",
    "action_mode": "argmax",
    "distributional_q_reduction": "expected_value",
    "legal_action_mask": "required",
    "recurrent_state_reset": "every_game",
    "recurrent_state_update": "every_decision",
    "tie_break": "lowest_legal_action_index",
    "deck_hash": "...",
    "runtime_config_hash": "..."
  },
  "published_at": "...",
  "event_hash": "..."
}
```

### 11.2 publish順序

1. checkpointを`.tmp`へ保存する。
2. Torch load、schema、training identity、finite値、stepを別processで検証する。
3. file `fsync`、atomic replace、directory `fsync`を行う。
4. SHA-256から`training_checkpoint_id`を確定する。
5. model stateだけをmodel-only artifactへexportし、canonical model state hashとruntime manifestから`runtime_policy_id`を確定する。
6. `runtime_policies/<runtime_policy_id>/`へweightsとmanifestをatomic publishする。
7. event JSONを一時fileへ書く。
8. event fileとdirectoryを`fsync`し、最後に確定する。

`checkpoint_path`はartifact rootからの相対pathとし、hash計算から絶対pathを除外する。

### 11.3 retention

次を保存する。

- 各epochの開始／終了
- Anchor best
- Rolling Meta best
- 最新3件
- L3／L4評価済み
- population transitionの親

削除候補はmanifestを生成して人間確認後に処理する。初期実装では自動削除しない。

## 12. candidate runtime

O5の静的`CANDIDATE_ARTIFACT_REGISTRY`だけでは任意checkpointを評価できない。任意checkpointからmodel-only RuntimePolicy packageをexportし、それを読むR2D3 runtime loaderを公開moduleへ抽出する。Evaluatorはoptimizer／Replayを含むtraining checkpointを直接ロードしない。

```python
class CandidateRuntime(Protocol):
    runtime_policy_id: str
    deck_hash: str
    action_mode: str

    def prepare(self) -> None: ...
    def act(self, observation: dict[str, object]) -> list[int]: ...
    def close(self) -> None: ...
```

### 12.1 R2D3 adapter

- `PublishedCheckpoint.runtime_policy.model_config`以外からarchitectureを推測しない。
- CPUへ`map_location`し、Torch threadを1へ固定する。
- checkpoint hash、runtime policy ID、feature versionを照合する。
- `model.eval()`、epsilon 0、dropout無効、mixed precision無効を固定する。
- distributional Qは`expected_value`へreduceし、legal action maskを必須にする。
- recurrent stateはgame開始時にresetし、各decision後に一回だけ更新する。
- Q同値時は最小のlegal action indexを選ぶ。
- action候補はcabtが提示した合法ActionKeyだけを使う。
- evaluationは`argmax`を必須とする。
- unsupported selection、mapping error、NaN／Inf Qを評価faultとして試合別に記録する。
- model load失敗時にRule v0へsilent fallbackしない。

submission runtimeと同じ前処理を利用できないcheckpointは`EVAL_INCOMPATIBLE`とし、別モデルへ置換しない。

## 13. `BenchmarkManifest`

```json
{
  "schema": "continuous-league-benchmark-manifest-v1",
  "benchmark_id": "anchor-v1",
  "benchmark_kind": "ANCHOR",
  "benchmark_version": "1.0.0",
  "catalog_hash": "...",
  "exposure_snapshot_hash": "...",
  "members": [
    {
      "asset_version_id": "...",
      "opponent_instance_id": "...",
      "component_id": "...",
      "role": "DEVELOPMENT_EVAL",
      "runtime_closure_hash": "...",
      "deck_hash": "...",
      "deck_archetype_id": "...",
      "policy_hash": "...",
      "policy_lineage_id": "...",
      "exposure_cohort": "NOVEL_DECK_KNOWN_ARCHETYPE",
      "source_weight": 0.1
    }
  ],
  "candidate_deck_hash": "...",
  "baseline_runtime_policy_ids": ["rule-agent-v0"],
  "cabt_version": "...",
  "rng_pairing_mode": "SEAT_BLOCKED_UNSEEDED",
  "seat_policy": "BALANCED",
  "game_budget": 384,
  "games_per_component_min": 16,
  "schedule_hash": "...",
  "manifest_hash": "..."
}
```

`SEAT_BLOCKED_UNSEEDED`ではseed fieldをscheduler ordering用にだけ使い、engine乱数固定を主張しない。

Anchor manifestはmember不足でも別相手で埋めない。Rolling manifestはsnapshot時刻とstalenessを持つ。

`ExposureSnapshot`はcandidateの学習に使用したReplay dataset version、model selectionに使ったevaluation result、開発者へ公開したreportから、opponent instance／deck／archetype／policy／lineage／deck-policy pairの集合を作る。Benchmark build時にsnapshotを固定し、各memberを`EXACT_KNOWN`、`KNOWN_DECK_NOVEL_POLICY`、`NOVEL_DECK_KNOWN_POLICY`、`NOVEL_DECK_KNOWN_ARCHETYPE`、`NOVEL_ARCHETYPE`、`FULLY_UNTOUCHED`へ分類する。report公開済みの反復development相手は`FULLY_UNTOUCHED`へ戻さない。

## 14. evaluation job

### 14.1 job schema

```json
{
  "schema": "continuous-league-evaluation-job-v1",
  "job_id": "eval-...",
  "level": "L2_STANDARD",
  "checkpoint_event_hash": "...",
  "training_checkpoint_id": "...",
  "runtime_policy_id": "...",
  "benchmark_manifest_hash": "...",
  "baseline_runtime_policy_ids": ["rule-agent-v0"],
  "priority": 30,
  "resource_profile": "cpu-eval-4",
  "created_at": "...",
  "status": "PENDING"
}
```

### 14.2 schedule

schedule rowは次を持つ。

- logical block ID
- game key
- candidate／baseline
- runtime policy ID
- opponent instance／asset version
- candidate／opponent deck hash
- candidate seat
- repetition index
- execution order
- schedule seed

game keyは`benchmark_id × runtime_policy_id × subject_deck_id × opponent_instance_id × seat × repetition_index × execution_block`で作る。schedule seedはordering用でありidentityへ含めない。candidateとbaselineを同じlogical blockへ交互に入れ、seatと試合数を揃える。cabt RNGが非制御であるためgame resultを一対一対応させない。

### 14.3 runtime

`league.actual_runner`のresumeとscheduleを利用しつつ、次を追加する。

- candidateとopponentで異なるdeck path
- `OpponentRuntimeSpec`によるnative subprocess
- seat別status attribution
- candidate callback latency
- training checkpoint／runtime policy／runtime closure hash
- game resultのappend前検証

native opponentのstdout／stderrは上限を設け、raw logをsummaryへ貼らない。

## 15. `EvaluationResult`

```json
{
  "schema": "continuous-league-evaluation-result-v1",
  "job_id": "...",
  "training_checkpoint_id": "...",
  "runtime_policy_id": "...",
  "manifest_hash": "...",
  "status": "PASS",
  "games": 384,
  "decided_games": 384,
  "exposure_cohorts": {
    "EXACT_KNOWN": {
      "games": 128,
      "win_rate": 0.0,
      "interval_95": [0.0, 1.0]
    },
    "FULLY_UNTOUCHED": {
      "games": 64,
      "win_rate": 0.0,
      "interval_95": [0.0, 1.0]
    }
  },
  "aggregates": {
    "game_weighted": {},
    "opponent_equal": {},
    "worst_opponent": {}
  },
  "baseline_differences": {},
  "by_opponent": {},
  "by_component": {},
  "by_archetype": {},
  "by_source_kind": {},
  "by_seat": {},
  "by_execution_block": {},
  "safety": {
    "candidate_illegal": 0,
    "candidate_exception": 0,
    "candidate_timeout": 0,
    "failure_upper_95": 0.0
  },
  "latency": {},
  "reproducibility": {
    "schedule_complete": true,
    "identity_verified": true
  },
  "result_hash": "..."
}
```

`0.0`はschema例であり、未評価値には`null`とreason codeを使う。0 gameを0%へ変換しない。

### 15.1 aggregate実装

- per-opponent：W／L／DとWilson interval
- 全体：game-weighted、opponent-equal、worst opponent、archetype／seat別を併記
- candidate−baseline：Newcombe interval
- heterogeneous aggregate：componentをresampling unitとするblock bootstrap
- block診断：decided gameへ`candidate + opponent + seat + execution_block`のlogistic regression
- Rolling Meta：versionごとのraw値ではなく、同一version内の`candidate - fixed baseline`差を比較
- safety：Clopper–Pearsonまたはrule-of-three相当の上側95%限界
- rating：Anchor matrixに対するregularized Bradley–Terry

同一deckに複数policyを結び付けたinstanceは、opponent／policy集計では別に扱い、deck coverageでは一つにまとめる。集計関数はraw game rowsから再構成可能にする。保存済みsummaryだけを信頼しない。

### 15.2 sealed Promotion binding

現行holdout controllerの予約／使用済みmarkerを拡張し、`holdout_id`、`runtime_policy_id`、`benchmark_manifest_hash`、`reserved_at`、`consumed_at`、`status`、完走時の`result_hash`を一つのmarkerへ保存する。予約後にcheckpoint aliasやcandidateを差し替えない。中断後も同じholdoutを再実行しないが、Phase 0で複数sealed setや新しい秘匿基盤は導入しない。

## 16. scheduler

### 16.1 priority

| priority | job |
|---:|---|
| 0 | L0 contract failureの再現 |
| 10 | population transition bootstrap collection |
| 20 | L3 candidate |
| 30 | continuous experience collection／Replay sealing |
| 40 | L2 standard |
| 50 | L1 monitor |
| 60 | report／calibration rebuild |

L4 Promotionは通常queueへ入れず、人間の明示commandだけで起動する。

### 16.2 backpressure

- 同一runtime policy／manifest jobをdedupeする。
- L1 pendingが2件を超えたら、epoch境界、最新、best以外をskipする。
- L2 pending中に新checkpointが来てもrunning jobを中断しない。
- L3は同時1件。
- training GPU lease中はCPU runtimeだけを許可する。
- available memoryがconfig floor未満なら新規CABT workerを起動しない。
- collectorの最低worker枠を先に予約し、evaluation workerがexperience生成を枯らさないようにする。
- sealed Replayのageが上限を超えた場合はL1／L2を延期し、collectionを優先する。

skipは削除ではなく、reason、時刻、置換先checkpointをevent logへ残す。

## 17. `PopulationTransition`

### 17.1 proposal

```json
{
  "schema": "population-transition-v1",
  "transition_id": "transition-...",
  "parent_training_checkpoint_id": "...",
  "old_epoch": 3,
  "new_epoch": 4,
  "old_population_hash": "...",
  "new_population_hash": "...",
  "catalog_hash": "...",
  "added_components": ["..."],
  "removed_components": [],
  "updated_assets": [],
  "inheritance": {
    "model": "INHERIT",
    "target": "INHERIT_IF_COMPATIBLE_ELSE_SYNC_MODEL",
    "optimizer": "INHERIT_IF_PARAMETER_TOPOLOGY_MATCHES",
    "lr_scheduler": "RESET_FOR_NEW_EPOCH",
    "rng": "RESET_FROM_EPOCH_SEED",
    "replay_partitions": ["epoch-1", "epoch-2", "epoch-3"],
    "priority_state": "RESET_UNIFORM_THEN_TD_RECOMPUTE",
    "global_step": "CONTINUE",
    "epoch_step": "RESET_ZERO"
  },
  "bootstrap_collection": {
    "required": true,
    "minimum_games_per_added_component": 32,
    "both_seats": true
  },
  "epoch_seed": 71004,
  "semantic_feature_version": "...",
  "preflight_evidence_hash": "...",
  "status": "PROPOSED",
  "transition_hash": "..."
}
```

`minimum_games_per_added_component=32`は初期config例であり、qualification smokeとは別のtraining data collectionである。実測throughputとsequence品質を根拠に変更する。

### 17.2 Gate

transitionは次をすべて要求する。

- 親checkpointがpublish済みでload可能
- semantic feature version一致
- model／target／optimizerのparameter topology一致または明示的な非継承
- 新population全memberがtraining資格を持つ
- role leakageなし
- parent Replay全partitionのhash一致
- 新相手の両seat CABT smokeがfault 0
- Anchor L1が親checkpointで再構成可能
- disk／memory budget充足

### 17.3 実行

1. Trainerをsafe checkpointで停止する。
2. transition preflightを再検証する。
3. 新epoch directoryを作る。
4. 親checkpointを読んで新training identityを発行する。
5. modelを継承し、target／optimizerをmanifestの互換条件に従って移行する。
6. schedulerとRNGを新epoch用に初期化し、`global_step`を継続、`epoch_step`を0にする。
7. 旧Replay recordを継承するがpriorityを共通値へresetし、新Replay partitionを空で作る。
8. 追加componentを各両seatでbootstrap collectionし、Replay Sealerが新partitionを発行する。
9. bootstrap最小件数と新componentのsampling floorを確認する。
10. epoch manifestと`STARTED` markerをdurable書込みする。
11. Trainerを再開し、TD error更新に伴うpriority再計算率を保存する。

親checkpointは変更しない。途中失敗時は新epochを`FAILED_TRANSITION`とし、親epochからstrict resumeできる。

`strict resume`だけはmodel／target／optimizer／scheduler／RNG／PER priority／stepを完全復元する。population rolloverでは旧priorityをそのまま復元しない。実験で有効性を確認した場合に限り、別modeの`DECAY_THEN_LAZY_RECOMPUTE`をtransition manifestで選べる。

## 18. source intake

### 18.1 remote fetch

fetch jobは明示allow-listだけを更新する。

```yaml
remote_namespaces:
  - remote: origin
    refs:
      - refs/heads/agent/*
      - refs/heads/agents/*
      - refs/heads/dev
```

fetch後もworking treeとHEADを変更しない。新commitはsource snapshotへ保存し、active Catalogはqualification完了まで不変とする。

### 18.2 pre-approved policy

Team namespaceはpermission manifestで次を指定できる。

```yaml
namespace: origin/agents/*
allowed_scopes:
  - evaluation
  - training_data_generation
auto_qualification:
  static_audit: true
  cabt_smoke_max_games: 8
auto_population_proposal: true
auto_population_apply: false
```

`auto_population_apply`は初期値falseとする。運用実績後も、sealed holdout、Champion、submissionへは拡張しない。

### 18.3 Kaggle public deck

network jobは既存`analyze_leaderboard_decks.py`相当をlibrary化する。

- API credential値をlogへ出さない。
- cache TTLとsnapshot時刻をmanifestへ保存する。
- replay download失敗をrow単位で記録する。
- 60枚、Card ID、canonical hashを検証する。
- raw ReplayはGit外archiveへ保存する。
- deck-only Catalog entryを作る。
- local policy bindingを別entryとして生成する。

coverageは`unique deck_hash`、`unique policy_hash`、`unique opponent_instance_id`を別々に数える。同一deckへ4 policyをbindした場合は1 deck／4 policy／4 instanceであり、4 deckとは数えない。

## 19. calibration

### 19.1 observation schema

```json
{
  "schema": "kaggle-calibration-observation-v1",
  "submission_id": "...",
  "training_checkpoint_id": "...",
  "runtime_policy_id": "...",
  "offline_result_hashes": ["..."],
  "submitted_at": "...",
  "online_observed_at": "...",
  "episode_count": null,
  "skill_mu": null,
  "skill_sigma": null,
  "public_score": null,
  "meta_snapshot_hash": "...",
  "eligible": false,
  "ineligible_reason": "CAPABILITY_NOT_CONFIRMED"
}
```

### 19.2 model

初期実装は観測を記録するだけとし、独立したruntime policy groupが30件未満ではforecastを発行しない。条件を満たした後の最初のmodelはAnchor ratingからpublic scoreへのisotonic regressionとする。

- runtime policy group単位のleave-one-out
- 最低30 runtime policy group
- target欠測、Episode不足、`σ`過大を除外
- median absolute error、coverage、prediction intervalを保存
- calibrator input feature range外はextrapolateせず`OUT_OF_DOMAIN`

多変量modelは50 group以上かつ事前に定めたcross-validation改善がある場合だけ追加する。

## 20. CLI

```text
python scripts/continuous_league.py source fetch
python scripts/continuous_league.py source discover
python scripts/continuous_league.py source qualify --asset <id>
python scripts/continuous_league.py catalog build
python scripts/continuous_league.py benchmark build --kind anchor
python scripts/continuous_league.py experience collect --manifest <hash>
python scripts/continuous_league.py replay seal --collection <hash>
python scripts/continuous_league.py checkpoint publish --path <checkpoint>
python scripts/continuous_league.py evaluate run --checkpoint <hash> --benchmark <hash>
python scripts/continuous_league.py evaluate resume --job <id>
python scripts/continuous_league.py evaluate report --checkpoint <hash>
python scripts/continuous_league.py population propose --catalog <hash>
python scripts/continuous_league.py population apply --transition <hash>
python scripts/continuous_league.py psro propose-best-response --payoff <hash>
python scripts/continuous_league.py controller run
python scripts/continuous_league.py controller status
python scripts/continuous_league.py calibration probe
python scripts/continuous_league.py calibration fit
python scripts/continuous_league.py promotion evaluate --checkpoint <hash>
```

`promotion evaluate`はholdoutを予約するため、通常controllerから呼ばない。Kaggle submit commandは本CLIへ追加しない。

## 21. config

```yaml
schema: continuous-league-config-v1
artifact_root: /home/bfe-lab-ono/kaggle/handoff-artifacts/continuous-league-v1
trainer:
  checkpoint_updates: 5000
  checkpoint_interval_minutes: 60
collection:
  cpu_workers: 4
  games_per_chunk: 256
  minimum_new_episodes_per_hour: 64
  seal_after_chunks: 1
evaluation:
  cpu_workers: 4
  torch_threads_per_candidate: 1
  max_pending_l1: 2
  l1_games: 64
  l2_games: 384
  l3_games: 2048
  l2_update_interval: 25000
  l2_interval_hours: 6
resources:
  minimum_available_host_mb: 8192
  minimum_free_disk_gb: 50
  gpu_eval_requires_training_pause: true
source_refresh:
  remote_hours: 12
  kaggle_hours: 6
  use_last_qualified_on_failure: true
```

数値は初期値であり、host計測と実験記録を根拠に変更する。変更はconfig hashを変え、同じbenchmark versionへ混在させない。

## 22. progressと長時間運用

リポジトリ共通の長時間表示契約を適用する。

- TTY：単一`tqdm` bar
- 非TTY：10秒程度ごとのstage／completed／throughput／ETA／fault summary
- `progress_summary.json`：atomic update
- terminal message：stage開始、完了、fail-closed理由だけ
- `tee`、行単位progress log、複数barを使わない

controller、trainer、evaluatorは別health fileを持つ。controllerが停止してもtrainer processを無条件killせず、leaseとPID identityを確認する。

## 23. test strategy

### 23.1 contract

- canonical hashの入力順不変
- NaN／Infinity拒否
- 同一ID異内容拒否
- training checkpoint IDとruntime policy IDを独立計算
- optimizerだけ異なる同一runtime policyを評価jobでdedupe
- deck／encoder／action modeが異なるpolicyを別identityとして扱う
- evaluatorがtraining checkpointではなくmodel-only RuntimePolicy packageを読む
- path traversal、symlink、submodule、binary quarantine
- checkpoint部分書込みをdiscoverしない
- eventより先にcheckpointがdurableである

### 23.2 experience／Replay

- incomplete episode chunkをsealしない
- behavior runtime policy、population、feature version不一致を拒否
- 同じchunk集合から同じReplay dataset versionを生成
- raw chunk／親Replay versionを書換えない
- learnerが未sealed chunkを読まない
- collector停止をhealth faultとして検出
- PSRO managerがoptimizer／Replay stateを変更しない

### 23.3 split／leakage

- 同一policy、deck、lineage root、parent派生がsplitを跨がない
- transitive component
- 新asset追加で既存assignment不変
- component mergeによるrole collisionをfail closed
- 同一logical Agent新旧版のrole維持

### 23.4 evaluation

- candidate／opponent別deck
- 両seat同数
- candidate／baseline block同数
- game keyがseedに依存しない
- recurrent stateを毎game resetし、decisionごとに一回更新
- expected-value Q reduction、legal mask、lowest legal-index tie-break
- exact deck／policy／lineage／archetype exposure cohort分類
- 同一deckの複数policy bindingをdeck coverageで重複計上しない
- engine seedをpairedと表示しない
- faultのseat別attribution
- 0 decided gameはrate `null`
- interrupted games JSONLからexact resume
- raw row再集計とsummary一致
- per-opponent W／L／D、game-weighted／opponent-equal／worst集計一致
- 同一block内candidate−baseline差とRolling Meta baseline delta

### 23.5 scheduler

- duplicate event dedupe
- L1 backlog skip
- epoch終端checkpoint保持
- collection最低worker枠をevaluationが奪わない
- Replay age超過時にL1／L2を延期する
- memory／disk不足でjob非開始
- GPU lease中のGPU eval拒否

### 23.6 transition

- same population strict resume bit一致
- changed population strict resume拒否
- approved transitionでmodel継承、互換なoptimizer継承
- rolloverでold／new priorityを共通値へreset
- sample後にTD errorでpriority再計算
- added componentのbootstrap collection完了前にlearnerを再開しない
- 新partitionがsampling floorどおりに選ばれる
- strict resumeだけは旧priority、RNG、scheduler、stepを完全復元
- global step継続、epoch step reset
- semantic version drift拒否
- transition途中停止から親epoch rollback

### 23.7 privacy／permission

- opponent raw observationをparent logへ保存しない
- secret環境変数除去
- evaluation-only assetをReplayへ書かない
- training scopeなしassetをpopulation proposalが拒否
- public deckをnative policyと表示しない

### 23.8 actual CABT

最小実CABT Gateは次である。

1. Rule v0、Team Native、dev native、public deck＋local policyを各両seat1局
2. 任意R2D3 checkpointのCPU argmax 8局
3. 32局L1中断後resume
4. 新asset1件のepoch rollover後32局
5. candidate fault／illegal／timeout 0

smoke結果を性能証拠へ昇格しない。

## 24. 段階実装

### Phase 0：手動Standalone Benchmark

**変更**

- P0a：training checkpoint／runtime policy／benchmark／game key contract
- P0b：dynamic R2D3 CPU argmaxによる単一game
- P0c：資格済み固定population importerとper-opponent deck benchmark
- P0d：両seat schedule、JSONL resume、固定worker並列実行
- P0e：per-opponent W／L／D、区間、baseline差、aggregate
- P0f：raw rowsから再生成できるAnchor／exposure cohort report

**受入条件**

- 既存16 assetをinventoryで版固定するが、Phase 0 rosterは非sealed 14 asset＋Rule v0／Rule v1の16 opponent instanceとし、reserved holdout 2 assetを実行しない。
- 24 unique deckを版固定し、archetype／policy kind／deck-policy variant／seat／runtime資格／training exposureのcoverage matrixを出す。
- 任意のR2D3 checkpointをCPU argmaxで実行し、feature／encoder不一致は`EVAL_INCOMPATIBLE`で拒否する。
- Phase 0 rosterの16 opponentを各両seatで実行し、native assetでは各opponent固有deckを使用する。
- recurrent stateがgame間で残らず、各decisionで一回だけ更新される。
- 中断後にgame keyでexact resumeし、raw game rowsから同一summaryを再生成する。
- Rule v0対Rule v0のsanity runが完走し、candidate／opponent別fault attributionを確認する。
- O5、Rule v0、Championを変更しない。
- 自動source watch、population更新、epoch rollover、PSRO、calibration、sealed Promotionを実装範囲へ入れない。

### Phase 1：Checkpoint Stream

**変更**

- Experience Generator
- Replay Sealer／ReplayDatasetVersion
- publish event
- L0／L1 scheduler
- backpressure

**受入条件**

- 連続10 checkpointで部分読込み0。
- learnerがsealed Replay versionだけを読み、新しいepisodeがcollectionから学習へ到達する。
- collector停止時にhealth faultを出し、固定Replayへの更新回数を環境適応量へ数えない。
- evaluator crash後もtrainerが継続する。
- queue遅延時にlatest／best／epoch終端が残る。

### Phase 2：Continuous Evaluation

**変更**

- L2、Bradley–Terry、forgetting report
- Rolling Meta manifest

**受入条件**

- Anchor versionを変えず3 checkpointを比較する。
- Rolling Meta version更新前後を共通anchorでlinkする。
- exact known／known deck novel policy／novel deck known policy／novel archetype／fully untouchedを別表示する。
- Rolling Metaは同一version内のcandidate−baseline deltaでversion間比較する。

### Phase 3：Population Epoch

**変更**

- transition manifest
- Replay partition
- stable role ledger

**受入条件**

- 新Team Native 1 componentを追加する。
- 親model／optimizerを継承して新epochを開始する。
- rolloverでは旧／新Replay priorityを共通値へresetし、TD errorによる再計算率を記録する。
- global stepを継続し、epoch step／scheduler／RNGを新epoch用に初期化する。
- 旧epoch strict resumeと新epoch transitionを区別する。
- Anchor forgetting Gateを通す。

### Phase 4：Automated Intake

**変更**

- allow-list remote fetch
- Kaggle public deck refresh
- auto proposal
- 独立したPSRO Population Managerとbest-response request

**受入条件**

- worktree／HEAD mutation 0。
- unapproved code execution 0。
- source更新失敗時に旧qualified snapshotで継続する。
- 新snapshotが旧Catalog／Benchmarkを上書きしない。
- PSRO managerがoptimizer／Replayを直接変更せず、PopulationTransitionを迂回しない。

### Phase 5：Calibration

**変更**

- Kaggle read-only Capability Probe
- observation registry
- isotonic calibrator

**受入条件**

- データ不足でforecastを出さない。
- 30 independent runtime policy group未満では観測記録だけを行う。
- leave-one-runtime-policy-group-out errorと区間を保存する。
- input range外でextrapolationを拒否する。

## 25. 実装後の運用Gate

常設controllerを開始する前に次を満たす。

- Phase 0／1 actual CABT PASS
- 24時間soakでcontroller／trainer／evaluatorの未回収process 0
- checkpoint、queue、resultの破損復旧テストPASS
- disk growthとretention見積りを記録
- source permission manifest確認
- protected file byte identity確認
- `git diff --check`
- focused testと全体testの結果記録

## 26. 完了とみなさない状態

- fixtureだけでnative remote対戦が0局
- Rule v0 deck-only相手だけでTeam Native policyが0件
- checkpoint loaderが固定registryの一モデルだけ
- scoreは出るがdeck／policy／lineage／archetype exposure cohortが分離されない
- raw experienceをsealせずlearnerが直接読む
- PSROがcontinuous learnerのoptimizer／Replayを直接変更する
- population fileを書き換えて旧checkpointをresumeする
- remote scanは動くがfetch／qualification／transitionが未接続
- calibration sample不足なのに予測値を表示する
- long-run smokeだけでChampionを変更する

## 27. 設計レビューの反映範囲

| 指摘 | 判定 | 反映 |
|---|---|---|
| experience生成とtraining data sealingが不足 | 採用 | `Experience Generator`と`Replay Sealer`を独立責務に追加 |
| R2D3とPSROの責務が混在 | 採用 | learnerと外側のPopulation Managerへ分離 |
| `seen`／`unseen`が粗い | 採用 | deck／policy／lineage／archetype／model-selection exposureへ分解 |
| training checkpointと実行policyが同一identity | 採用 | `training_checkpoint_id`と`runtime_policy_id`を分離 |
| population更新時の旧PER priority完全継承 | バグとして修正 | rollover既定をreset＋TD error再計算へ変更 |
| CABT非seed下の比較統計が不足 | 採用 | 同一block baseline、Newcombe、block bootstrap、補助logistic modelを追加 |
| Phase 0が広がりやすい | 採用 | 手動固定benchmarkだけに限定し、P0a〜P0fの順序と受入条件を明示 |
| sealed setを複数用意し、強い秘匿管理を追加 | Phase 0では不採用 | 現行one-time markerとcandidate bindingだけを維持し、複数set運用は実証後に再検討 |
| NaN時に別policyへ安全fallback | 不採用 | 性能測定を歪めるため評価faultとし、Rule v0へ置換しない |
| 複雑なKaggle校正を早期導入 | 先送り | まず観測だけを保存し、30 independent policy group未満ではforecastしない |

## 28. 実装対応

本計画のローカル実装は2026-07-30に完了した。運用入口は[Continuous League Runbook](../../runbooks/continuous-league.md)、packageは`src/mage_ptcg/continuous_league/`、CLIは`scripts/continuous_league.py`である。

実装済み範囲は、catalog／append-stable role ledger、exposure cohort、固定schedule、CABT collector／evaluator、immutable replay、strict resume、model-only RuntimePolicy、常駐learner、durable scheduler、subprocess controller、population rollover、SP-PSRO判断、source refresh、calibration、report、sealed markerである。

24時間soak、長時間production学習、30件の実Public score observation、Kaggle read-only API capabilityの実証は未実施である。これはコード未接続ではなく、時間または外部観測を必要とする運用Gateとして[25節](#25-実装後の運用gate)に残す。
