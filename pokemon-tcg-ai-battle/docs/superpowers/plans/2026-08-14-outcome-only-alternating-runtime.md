# Outcome-Only Alternating Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable research-only runtime that evaluates deck/policy candidates against a native control on a fixed META_TRAIN distribution and records successive-halving transitions.

**Architecture:** Add one focused runtime module above the existing candidate-game builder and parallel evaluator. The runtime owns candidate/control pairing, outcome projection, atomic run artifacts, and phase/stage contracts; the existing state bridge remains the authority for any future native-bound promotion decision.

**Tech Stack:** Python 3, dataclasses, canonical JSON/SHA-256, existing `EvaluationGameV1`, `build_native_candidate_games_v1`, `run_parallel_cabt_evaluation`, pytest.

## Global Constraints

- Production `main.py`, `agents/rule_agent.py`, submission archive, and existing run roots are unchanged.
- Native/local-eval assets are evaluation controls only; no native behavior labels or private observations enter a policy update.
- All new artifacts use `research_only=true` and all authority fields are `false`.
- Evaluation defaults are `workers=12` and `worker_recycle_games=16`; `workers=1` is only an explicit diagnostic override.
- Stages are exactly `96`, `384`, `768`, or `1536`; every candidate arm includes the native control arm.
- No commit, push, Kaggle submission, or automatic Champion change.

### Task 1: Candidate/control runtime contract

**Files:**
- Create: `src/mage_ptcg/meta_specialist/outcome_only_alternating_runtime_v1.py`
- Test: `tests/meta_specialist/test_outcome_only_alternating_runtime_v1.py`

**Interfaces:**
- `OutcomeOnlyCandidateSpecV1.from_mapping(payload) -> OutcomeOnlyCandidateSpecV1`
- `build_candidate_control_games_v1(candidate, native_control, pool_root, reference_ids, stage_games, base_seed, block_id) -> tuple[EvaluationGameV1, ...]`
- `summarize_candidate_control_rows_v1(rows, candidate, native_control, stage_games) -> dict[str, object]`

- [ ] Write failing tests for candidate identity validation, shared seed/seat/opponent strata, and exact authority fields.
- [ ] Run the focused test and confirm the module/API failure.
- [ ] Implement immutable candidate parsing and candidate/control game construction by delegating to `build_native_candidate_games_v1`.
- [ ] Implement WDL projection with fault-inclusive denominator and paired key checks.
- [ ] Run the focused tests and `py_compile`.

### Task 2: Stage executor and artifacts

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/outcome_only_alternating_runtime_v1.py`
- Test: `tests/meta_specialist/test_outcome_only_alternating_runtime_v1.py`

**Interfaces:**
- `run_alternating_stage_v1(..., execute: bool, workers: int = 12, worker_recycle_games: int = 16) -> dict[str, object]`
- `load_alternating_stage_v1(run_root) -> dict[str, object]`

- [ ] Add a dry-run test that writes no evaluator output and an execute test using `fixture_runner_v1`.
- [ ] Add sealed manifest/summary/ledger writes with no-clobber fresh-root semantics.
- [ ] Run candidate and native control games in one parallel evaluator call, preserving worker defaults.
- [ ] Re-load the artifacts and reject changed source/candidate/evaluator/seed identities.
- [ ] Run focused tests and `git diff --check`.

### Task 3: CLI and first real stage

**Files:**
- Create: `scripts/run_outcome_only_alternating_runtime_v1.py`
- Test: `tests/meta_specialist/test_run_outcome_only_alternating_runtime_v1.py`
- Create: `docs/evidence/autonomous-outcome-only-alternating-runtime-v1-20260814.md`

**Interfaces:**
- CLI accepts candidate/control JSON specs, pool/reference config, stage, base seed, output root, `--execute`, `--workers`, and `--worker-recycle-games`.
- Default mode materializes a sealed dry-run; `--execute` is required for CABT.

- [ ] Write RED tests for CLI dry-run, `--execute` path, output containment, and default workers=12.
- [ ] Implement CLI parsing and source SHA binding.
- [ ] Run one fresh 96-game candidate/control screen with workers=12; do not reuse an old output root.
- [ ] Classify `POSITIVE_CONTINUE`, `NOT_PROMOTABLE`, or `INVALID_FAULT` from raw ledger evidence.
- [ ] Record exact artifact SHA, command, worker settings, and next-stage decision in evidence/status/handoff.

### Task 4: Alternating handoff hook

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/outcome_only_alternating_runtime_v1.py`
- Test: `tests/meta_specialist/test_outcome_only_alternating_runtime_v1.py`

- [ ] Add phase-fixed state transition output that keeps policy SHA fixed in `DECK_FIXED_LONG` and deck SHA fixed in `POLICY_FIXED_SHORT`.
- [ ] Add `next_stage_games` only when the raw stage is positive, fault-free, and seat-stable.
- [ ] Write a checkpoint descriptor containing state/config/control SHA and a rollback reason for negative stages.
- [ ] Run the complete focused suite, docs validator, `py_compile`, and `git diff --check`.
