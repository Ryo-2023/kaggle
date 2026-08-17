# Offline Training v1 — 統合契約（Gemini support 連携用）

作成日: 2026-07-17 / branch: `integration/offline-training-v1`

本書は Claude 側 P0 本体が自然に成立させる schema と拡張点を明文化する。Gemini branch は参照しない。相手の統計評価・registry・hard-state mining は、以下の入力 schema と versioning 規則に従えば非破壊で連携できる。

## versioning と未知フィールド方針

- すべての永続 artifact は `schema_version`（または `schema`）を先頭に持つ。消費側は完全一致を要求してよいが、上位互換のため未知の追加フィールドは無視する（reject しない）。必須フィールドの欠落は fail-closed とする。
- hash フィールド（`dataset_hash`, `feature_schema_hash`, `model_hash`, `manifest_hash`, `checksum`）は canonical JSON（`ensure_ascii=False, sort_keys=True, separators=(",",":")`）の SHA-256。消費側は再計算で検証できる。
- 破壊的変更時は `schema_version` を更新し、旧版を無断で読み替えない。

## run manifest schema（`runs/offline-training-v1/<run-id>/run_manifest.json`）

`schema_version = offline-training-v1-manifest-v1`。fsync 付き atomic write。

必須: `run_id`, `git_commit`, `config_hash`, `environment_hash`, `current_phase`, `phase_statuses`（`collect|build-dataset|train|evaluate|screen|export|package|verify` → `PENDING|RUNNING|COMPLETE|SKIPPED|INTERRUPTED|FAILED_RETRYABLE|FAILED_FINAL`）, `dataset_hash`, `feature_schema_hash`, `teacher_id`, `model_purpose`, `model_hash`, `best_checkpoint`, `last_checkpoint`, `package_hash`, `resume_count`, `error_summary`, `created_at`, `updated_at`。

## collection per-game schema（既存 collector 契約に従属）

collection は `mage_ptcg.dataops.collector.collect_actual_dataset` が生成する。per-game 記録は `private_dataset/games/rows_g<i>.jsonl` と `binds_g<i>.jsonl`、集約 `public_summary.json`。本体はこれに `collection_source ∈ {fixture, actual}` と `actual_cabt ∈ {ACTUAL_CABT_RUN, ACTUAL_CABT_NOT_RUN}` を付す。private binding（`private_bindings.jsonl`）は trainer 入力ではなく、公開してはならない。

## dataset record schema（canonical shard）

- shard: `shard-NNNNN.jsonl.gz`、record は既存 `RuleBCExample`（`schema_version = rule-bc-v1`）。1 record = 1 decision。episode 同一性は `source_id`（redacted）。candidate は `legal_actions`（各 `digest` + `payload`）。teacher target は `target_action_digests`。
- 各 shard meta（`dataset_manifest.json.shards[]`）: `schema = offline-training-v1-shard-v1`, `name`, `sha256`, `record_count`, `episode_count`, `decision_count`, `candidate_count`, `min_episode_id`, `max_episode_id`, `source_game_hashes`。
- dataset manifest: `schema_version = offline-training-v1-dataset-v1`。`dataset_hash`, `feature_schema_hash`, `feature_dimension`(96), `split_seed`, `split_fractions`, `split_assignment`（episode→split, whole-episode）, `split_episode_counts`, `normalization`（train-only `mean`/`std`/`count`）, `duplicate_conflict_count`, `quarantined_identities`, `teacher_id`, `trainer_id`, `source_collection_hash`, `manifest_hash`。

## hard-state mining 入力 schema

hard-state mining は canonical shard を stream 消費できる。1 decision あたり:

- `example_id`（決定同一性）, `source_id`（episode）, `selection_type`, `candidate_digests`（Stable ActionKey digest 列）, `target_indices`（teacher 選択の legal index）, `min_count`。
- 「hard」判定に使える派生量は本体の評価（`evaluation/evaluation.json`）が持つ per-type/per-candidate-count top-1 と、runtime score（pure-Python `mage_ptcg.offline_training.export.score_candidates`）で再計算可能。相手の非公開情報は入力に含めない。

## evaluation per-game / summary schema

- screening per-game（`evaluation/screening.json.per_game[]`）: `game_index`, `seat`, `seed`, `decisions`, `legal_actions`, `fallback_count`, `invalid`, `crash`, `timeout`。
- screening summary: `schema_version = offline-training-v1-screening-v1`, `harness`, `actual_cabt`, `games`, `seat_balance`, `wins/losses/draws/overall_win_rate`（fixture では `null`）, `legal_action_rate`, `fallback_rate`, `verdict ∈ {PROMISING_CHALLENGER, NOT_PROMISING, INVALID_SCREENING, INSUFFICIENT_EVIDENCE}`。
- evaluation summary（`evaluation/evaluation.json`）: `neural_student_v1` と `linear_student_v0` の `top1/top3/mrr/nll/legal_action_rate` と breakdown、`neural_minus_linear_top1`。

## model export schema（package 同梱モデル）

`schema_version = offline-training-v1-neural-export-v1`。torch/numpy 非依存で読める JSON。フィールド: `architecture`（`input_dim`, `hidden_dims`, `activation`）, `feature_schema_version`, `feature_schema_hash`, `feature_dimension`, `normalization`（`mean`/`std`）, `layers`（`weight`/`bias` の nested list）, `dataset_hash`, `config_hash`, `teacher_id`, `model_purpose ∈ {NEURAL_ACTUAL_TRAINED, NEURAL_FIXTURE_SMOKE}`, `fallback_policy = rule-agent-v0`, `model_hash`。

## registry 連携点

registry は「eligible な artifact」だけを performance 評価へ入れるべきである。連携キー:

- `model_hash`（export の内容 hash）, `feature_schema_hash`, `dataset_hash`, `model_purpose`。
- `NEURAL_FIXTURE_SMOKE` は performance 非適格（fixture 由来）。`NEURAL_ACTUAL_TRAINED` は actual collection かつ collector の `performance_eligible=true` の場合のみ本体が付与する。
- package manifest（`package/neural-student-v1/manifest.json`）: `package_identity = neural-student-v1-rule-v0-fallback`, `model_hash`, `feature_schema_hash`, `archive_sha256`, `files[]`（path/sha256/size）, `build_commit`, `clean_room`。

## 不変条件

- Champion は Rule Agent v0。本 package は default agent を変更しない。Promotion は NO_DECISION。
- ActorInformationView に相手の非公開情報を含めない。Stable ActionKey を行動同一性とする。cabt の合法手判定を hard truth とする。
