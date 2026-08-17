# Autonomous Meta Fine-Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定した native population と hash-bound meta schedule を起点に、native-preserving policy/deck candidate の successive-halving と再開可能な longrun を安全に実行できる研究経路を作る。

**Architecture:** 新規 research-only modules を `meta_distribution_v1`、`native_tuning_surface_v1`、`native_preserving_adapter_v1`、`alternating_meta_optimizer_v1`、`longrun_autonomous_v1` に分離する。既存 native source、production evaluator、entrypoint、Champion は触らず、既存 ranking artifact を入力として immutable manifest と候補 artifact を生成する。

**Tech Stack:** Python 3.12、dataclasses、JSON SHA-256 manifest、pytest、既存 CABT spawn evaluator、既存 `opponent_pool_v1`。

## Global Constraints

- `local_eval_only` を training/teacher/submission permission へ拡張しない。
- `META_TRAIN` は明示された `training_usable` のみ、`META_DEV`/`META_FINAL` は評価専用とする。
- native deck/policy の byte SHA を baseline として固定し、未知状態や不正候補は native fallback へ戻す。
- CABT engine seed setter がないため、block/seat/opponent の独立層化として扱う。
- 96→384→768→1536 の successive-halving と native baseline を必ず保存する。
- 既存の Lucifer hard-BC、tomato AWR、Rule prior、residual、V5 head、NLL-only sweep は再実行しない。
- commit、push、Kaggle submission、Champion/default変更を行わない。

---

### Task 1: Immutable meta distribution manifest

**Files:**
- Create: `src/mage_ptcg/meta_specialist/meta_distribution_v1.py`
- Create: `tests/meta_specialist/test_meta_distribution_v1.py`
- Create: `scripts/build_meta_distribution_manifest_v1.py`
- Create: `configs/meta_specialist/autonomous_meta_distribution_v1.json`
- Create: `docs/evidence/autonomous-meta-distribution-v1-20260813.md`

**Interfaces:**
- `build_meta_distribution_manifest_v1(census_path, ranking_paths, *, candidate_id, dev_ids, final_ids) -> MetaDistributionManifestV1`
- `save_meta_distribution_manifest_v1(manifest, path)` / `load_meta_distribution_manifest_v1(path, verify_sources=True)`
- `build_meta_schedule_v1(manifest, *, split, quota, require_training_permission) -> tuple[MetaScheduleRowV1, ...]`

- [ ] Write tests for source SHA binding, disjoint splits, component weights, hard-negative score, permission filtering, deterministic quotas, and corruption rejection.
- [ ] Run the focused test and verify RED because the module is absent.
- [ ] Implement closed dataclasses, ranking aggregation, top-meta/hard-negative/diversity weighting, split validation, and fail-closed loader.
- [ ] Add CLI that uses existing census/fast96/top3 artifacts without modifying them.
- [ ] Run focused tests, CLI build, JSON validation, and diff check.

### Task 2: Native tuning surface and fallback adapter

**Files:**
- Create: `src/mage_ptcg/meta_specialist/native_tuning_surface_v1.py`
- Create: `tests/meta_specialist/test_native_tuning_surface_v1.py`
- Create: `scripts/audit_native_tuning_surface_v1.py`
- Create: `src/mage_ptcg/meta_specialist/native_preserving_adapter_v1.py`
- Create: `tests/meta_specialist/test_native_preserving_adapter_v1.py`

**Interfaces:**
- `audit_native_pair_v1(asset_id, main_path, deck_path) -> NativeTuningSurfaceV1`
- `load_native_config_v1(surface, overrides) -> NativeConfigV1`
- `NativePreservingPolicyV1(native_callable, bounded_override, eligibility) -> agent(obs)`

- [ ] Test AST/regex extraction for constants, search budget, override hooks, deck SHA, and source SHA without editing source bytes.
- [ ] Test unknown/malformed state, out-of-range action, timeout, and ineligible override all route to native behavior.
- [ ] Implement read-only surface extraction and explicit config schema.
- [ ] Implement adapter that only applies bounded public-state overrides and retains native fallback.
- [ ] Run focused tests and produce surface evidence for tomato/Lucifer/plamen.

### Task 3: Direct policy bounded pilot

**Files:**
- Create: `scripts/run_native_policy_tuning_pilot_v1.py`
- Create: `tests/meta_specialist/test_run_native_policy_tuning_pilot_v1.py`
- Create: `configs/meta_specialist/native_policy_pilot_v1.json`

- [ ] Use fixed meta schedule and native baseline pair; generate only research copies and new output roots.
- [ ] Add successive-halving candidate generation from extracted parameter surfaces.
- [ ] Evaluate 96 then 384 games with native baseline and fault/seat/runtime gates.
- [ ] Persist candidate config, baseline SHA, schedule SHA, evaluator SHA, and outcome.
- [ ] Pivot to value-gated override only if direct tuning changes behavior and fails to improve; do not repeat hard-BC.

### Task 4: Deck mutation and alternating optimizer

**Files:**
- Create: `src/mage_ptcg/meta_specialist/alternating_meta_optimizer_v1.py`
- Create: `tests/meta_specialist/test_alternating_meta_optimizer_v1.py`
- Create: `scripts/run_alternating_meta_optimizer_v1.py`

- [ ] Test 60-card legality, 1/2/3-4 card mutation limits, exact multiset identity, and policy/deck separation.
- [ ] Implement short policy-fixed deck race and long deck-fixed policy race with atomic candidate records.
- [ ] Reuse existing sealed deck locks and evaluator provenance; keep native baseline in every race.
- [ ] Execute only after policy pilot has a measurable behavior change and no fault regression.

### Task 5: Longrun loop and final gate

**Files:**
- Create: `src/mage_ptcg/meta_specialist/longrun_autonomous_v1.py`
- Create: `tests/meta_specialist/test_longrun_autonomous_v1.py`
- Create: `scripts/run_autonomous_meta_finetune_longrun_v1.py`
- Create: `docs/evidence/autonomous-meta-finetuning-longrun-20260813.md`

- [ ] Test checkpoint/resume, atomic writes, rollback, stop after two native regressions, and META_FINAL isolation.
- [ ] Implement execute=false dry-run descriptor and execute=true fail-closed until LONGRUN_READY.
- [ ] After a native-over-BestKnown direction is reproduced on at least two blocks, start the actual longrun in a new run root.
- [ ] Record 25/50/75/100% checkpoints, evaluator result, schedule update, and package closure.
- [ ] Produce final report with BestKnown classes, top two submission candidates, and either `LONGRUN_STARTED` or `HARD_EXTERNAL_BLOCKER`.

