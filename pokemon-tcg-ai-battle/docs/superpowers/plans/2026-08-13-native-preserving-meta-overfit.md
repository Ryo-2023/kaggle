# Native-Preserving Meta-Overfit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** native policyを保持したbounded public-state advantage、hard-negative curriculum、deck-policy alternating stateを接続し、common24でnative超越候補を検証できる研究専用longrun前段を作る。

**Architecture:** immutable native baselineを常にcontrolとして保持する。新規advantage tableはMETA_TRAINのactor-visible outcomeだけから作り、single-choice MAINだけへbounded overrideを試み、その他はnativeへ戻す。既存dynamic curriculum、deck mutation、alternating optimizer、longrun gateを新規iteration adapterで束ねる。

**Tech Stack:** Python 3、既存 `src/mage_ptcg/meta_specialist` contracts、pytest、canonical JSON/SHA-256、既存common24 evaluator。外部依存を追加しない。

## Global Constraints

- native `main.py`、Rule v0、既存性能artifact、submission archiveは変更しない。
- `local_eval_only` は評価のみ。teacher/behavior/training/submission sourceへ使わない。
- private stateをfeatureへ入れず、CABT legalityをhard truthとする。
- 全新規authorityはfalse。CLIの既定はdry-runで、CABT/training/submissionを起動しない。
- 評価段階は96→384→768→1536のみ。各候補はnative control、両seat、fault0、common24 protocolを要求する。
- commit/push/Kaggle提出はユーザー明示なしでは実行しない。

---

### Task 1: Public advantage artifact contract

**Files:**
- Create: `src/mage_ptcg/meta_specialist/native_public_advantage_v1.py`
- Create: `tests/meta_specialist/test_native_public_advantage_v1.py`
- Create: `docs/evidence/autonomous-native-public-advantage-v1-20260813.md`

**Interfaces:**
- Consumes: strict JSONL rows with `state_digest`, `action_key`, `opponent_id`, `seat`, `split`, `outcome`, `weight`; a verified meta manifest and native policy SHA.
- Produces: `PublicAdvantageTableV1`, `build_public_advantage_table_v1(...)`, `build_native_public_advantage_policy_v1(...)`, canonical table SHA, coverage summary.

- [ ] **Step 1: Write failing tests** for deterministic state/action aggregation, positive/negative delta cap, insufficient support, META_DEV/FINAL rejection, private-key rejection, duplicate record rejection, and exact native fallback for unknown/malformed/multi-select/ordered input.
- [ ] **Step 2: Run `PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_public_advantage_v1.py` and confirm missing module/API failures.
- [ ] **Step 3: Implement canonical row validation and table construction.** Use `json.loads` duplicate-key rejection, finite numeric checks, exact split `META_TRAIN`, state/action digest validation, `delta_cap`, `min_support`, and domain-separated SHA over canonical no-newline bytes.
- [ ] **Step 4: Implement the native-first wrapper.** Call the native agent first; only single-choice `MAIN` rows with table support and a delta above the fixed margin may override. Reject non-legal or non-finite candidates and return the exact native action.
- [ ] **Step 5: Run the focused tests and record coverage/table SHA in the evidence doc.**

### Task 2: Strict hard-negative iteration adapter

**Files:**
- Create: `src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py`
- Create: `scripts/build_native_meta_overfit_iteration_v1.py`
- Create: `tests/meta_specialist/test_native_meta_overfit_iteration_v1.py`
- Create: `docs/evidence/autonomous-native-meta-overfit-iteration-v1-20260813.md`

**Interfaces:**
- Consumes: verified dynamic curriculum manifest, strict META_TRAIN outcome adapter, public advantage table, native baseline identity, optional legal deck candidate manifest.
- Produces: atomic iteration manifest containing source SHAs, candidate/table/curriculum identities, hard-negative statistics, authority false, and `ready_for_evaluation` boolean.

- [ ] **Step 1: Write failing tests** for META_TRAIN-only outcome admission, heldout rejection, family floor/cap, fault/seat weighting, source SHA binding, deterministic iteration seed, and all-authority-false enforcement.
- [ ] **Step 2: Run the focused test and confirm the adapter is absent or rejects the new contract.**
- [ ] **Step 3: Implement strict input binding** by reusing `verify_dynamic_curriculum_manifest_v1` and the existing common24 adapter loader; re-hash every source before producing the new manifest.
- [ ] **Step 4: Implement deterministic hard-negative weights** as bounded loss/seat/under-exposure/reliability/diversity terms; emit per-opponent statistics and zero exposure for DEV/FINAL.
- [ ] **Step 5: Implement atomic manifest write and strict reload**, including candidate/base policy/deck/evaluator identities and `ready_for_evaluation=false` until all gates are present.
- [ ] **Step 6: Run tests, `py_compile`, JSON reload, and docs validation.**

### Task 3: Alternating state bridge

**Files:**
- Create: `src/mage_ptcg/meta_specialist/native_meta_overfit_alternating_v1.py`
- Create: `tests/meta_specialist/test_native_meta_overfit_alternating_v1.py`
- Create: `docs/evidence/autonomous-native-meta-overfit-alternating-v1-20260813.md`

**Interfaces:**
- Consumes: iteration manifest, `CandidateStateV1`, deck mutation candidates, evaluation summaries.
- Produces: policy-fixed/deck-fixed candidate states, native control binding, successive-halving decision and rollback descriptor.

- [ ] **Step 1: Write failing tests** for phase invariants, exact stage sequence `(96,384,768,1536)`, candidate/native pair requirement, seat/fault gate, native regression stop-after-two, and checkpoint SHA mismatch rejection.
- [ ] **Step 2: Run the focused test to confirm RED.**
- [ ] **Step 3: Implement a thin adapter** around existing `alternating_meta_optimizer_v1`; do not duplicate its journal or authority logic. Add only the public advantage/table and iteration manifest bindings.
- [ ] **Step 4: Implement deterministic promotion/rollback decisions** without granting execute/training/promotion/submission/longrun authority.
- [ ] **Step 5: Run focused tests and inspect serialized state/rollback descriptors.**

### Task 4: Dry-run CLI and candidate materializer

**Files:**
- Modify: `scripts/build_native_meta_overfit_iteration_v1.py`
- Create: `tests/meta_specialist/test_build_native_meta_overfit_iteration_v1.py`
- Create: `docs/evidence/autonomous-native-meta-overfit-dryrun-v1-20260813.md`

**Interfaces:**
- Consumes: Tomato native baseline, existing common24 curriculum/adapter artifacts, one verified outcome table, optional deck mutation candidate.
- Produces: new run root with `DRY_RUN`, progress, iteration manifest, candidate table, and no evaluator/training/submission child process.

- [ ] **Step 1: Write failing CLI tests** for dry-run default, missing/forged SHA, output-root containment, and process-launch prohibition.
- [ ] **Step 2: Implement CLI argument parsing and dry-run materialization** with explicit `--execute` rejection unless a caller-supplied executor and all authorities are independently verified.
- [ ] **Step 3: Run a Tomato-only dry-run** against the current sealed artifacts; do not launch CABT or learning.
- [ ] **Step 4: Verify all paths, SHAs, authority flags, and process list; write evidence.**

### Task 5: Bounded Tomato common24 performance screen

**Files:**
- Create: `docs/evidence/autonomous-native-public-advantage-common24-screen-20260813.md`
- Create: `runs/final-sprint-autonomous/native-public-advantage-v1/...` (generated artifacts only; never overwrite existing roots)

**Interfaces:**
- Consumes: dry-run-verified candidate, immutable Tomato native baseline, existing common24 evaluator and reference config.
- Produces: candidate/native 96-game screen with fault/seat/opponent strata and an explicit next-stage decision.

- [ ] **Step 1: Require a verified iteration manifest and candidate identity before execution.**
- [ ] **Step 2: Run exactly one 96-game candidate/native screen with common24, both seats, fault-inclusive denominator.**
- [ ] **Step 3: Stop and classify if candidate is below native or fault/seat gate fails; do not launch 384.**
- [ ] **Step 4: If the preregistered +3pt signal and all safety gates pass, create a seed-disjoint 384 block; otherwise record NO-GO.**
- [ ] **Step 5: Reload artifacts, compute SHA, run focused evaluator/reconciler tests, and update status/handoff.**

### Task 6: Longrun readiness audit

**Files:**
- Create: `tests/meta_specialist/test_native_meta_overfit_longrun_gate_v1.py`
- Create: `docs/evidence/autonomous-native-meta-overfit-longrun-gate-v1-20260813.md`
- Modify only if required by the new adapter contract: `src/mage_ptcg/meta_specialist/longrun_autonomous_v1.py`

**Interfaces:**
- Consumes: candidate/native 384 result, clean META_DEV manifest, package/qualified-deck closure, checkpoint/rollback descriptors.
- Produces: machine-readable GO/NO-GO gate; default is NO-GO.

- [ ] **Step 1: Write failing tests** for each missing gate and for a fully synthetic passing gate.
- [ ] **Step 2: Implement only the missing binding checks; preserve existing fail-closed authority.**
- [ ] **Step 3: Run the gate on the actual candidate; expect NO-GO until the measured performance and package evidence exist.**
- [ ] **Step 4: Run the complete focused suite, docs validation, `py_compile`, and `git diff --check`; update status/handoff.**

---

## Review checkpoints

- After Task 1: public/private and native fallback contract review.
- After Task 2: META_TRAIN/DEV/FINAL and hard-negative weighting review.
- After Task 5: independent common24 performance review before any longrun attempt.
- Before Task 6 could ever return GO: package and permission review; no automatic submission.

