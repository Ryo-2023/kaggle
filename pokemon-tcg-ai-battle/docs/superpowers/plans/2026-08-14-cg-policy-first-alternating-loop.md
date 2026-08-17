# cg Policy-First Alternating Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the self-owned `cg-lethal-target-v1 + root deck` as the frozen research parent, derive public-state policy refinements from observed failures, and connect a verified winning policy to deck-fixed/policy-fixed alternating evaluation without changing production or the submission branch.

**Architecture:** Keep the packaged P1 policy and root deck immutable. A read-only analyzer consumes only hash-bound public telemetry and terminal WDL ledgers, emits at most three explicit hypotheses, and a bounded candidate factory renders policy-only variants. A resource-aware evaluator runs paired P1-vs-candidate stages at 48→96→384→768; only a reproducible P2 unlocks the existing alternating runtime and later self-owned rollout adapter.

**Tech Stack:** Python 3, existing `parallel_cabt_evaluator_v1`, `cg_alternating_runtime_v1`, pytest, SHA-256 manifests, ResourceGovernor (`workers=12`, `recycle=16`; `recycle=64` at 384/768).

## Global Constraints

- P1 policy SHA is `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` and root deck SHA is `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`.
- Candidate inputs are public state/action/outcome only; reject private, teacher, native-behavior, hidden, and log-probability fields.
- Rule v0 exploration, blind retries of stopped candidates, native local_eval_only behavior collection, and V4 semantic expansion remain disabled.
- Candidates are research-only with training, promotion, submission, longrun, and external-execution authority false.
- Candidate/control must share opponent, seat, repetition, seed strata and evaluator identity; faults and invalid actions are never converted into wins/losses.
- Existing artifacts and production entrypoints are never overwritten; all outputs use fresh no-clobber roots.
- Do not commit, push, change Champion/default, or submit to Kaggle as part of this plan.

---

### Task 1: Freeze the independent P1 baseline and public-analysis input

**Files:**
- Read: `runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260814-v1/`
- Read: `runs/final-sprint-autonomous/cg-population-deck-fixed-384-20260814-v1/`
- Read: `src/mage_ptcg/meta_specialist/cg_alternating_runtime_v1.py`
- Create: `runs/final-sprint-autonomous/cg-p1-independent-768-20260814-v1/`

**Interfaces:**
- Consumes: sealed P1 package, broad META_TRAIN schedule, existing evaluator.
- Produces: a fresh 768-vs-control ledger with exact identity, paired strata, seat/opponent summaries, and a reproducible parent baseline.

- [ ] **Step 1: Verify source identity and schedule bytes**

Run:

```bash
PYTHONPATH=.:src .venv/bin/python - <<'PY'
from pathlib import Path
import hashlib, json
root = Path("runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260814-v1")
manifest = json.loads((root / "manifest-complete.json").read_text())
assert manifest["package_manifest"]["source_policy_sha256"] == "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
assert manifest["package_manifest"]["deck_sha256"] == "2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19"
print("P1 identity PASS")
PY
```

Expected: `P1 identity PASS`; no files are modified.

- [ ] **Step 2: Run one independent 768 paired block**

Use the existing P1-vs-control runner with a disjoint base seed, `workers=12`, `recycle=64`, and a fresh output root. Require both arms to complete all 768 games, fault count zero, unique game IDs, equal `(opponent, seat, repetition, seed)` keys, and no heldout exposure.

- [ ] **Step 3: Verify the baseline artifact**

Recompute WDL, seat, opponent, paired, and resource summaries from the ledger. Record whether P1 remains the research parent; do not promote it based on one block.

### Task 2: Build a public-state hypothesis analyzer and bounded candidate contract

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cg_p1_public_hypothesis_v1.py`
- Create: `scripts/build_cg_p1_public_hypotheses_v1.py`
- Create: `tests/meta_specialist/test_cg_p1_public_hypothesis_v1.py`
- Read: `src/mage_ptcg/meta_specialist/cg_public_telemetry_v1.py`
- Read: `src/mage_ptcg/meta_specialist/cg_p1_observed_failure_v1.py`

**Interfaces:**
- Consumes: hash-bound telemetry JSONL plus terminal WDL ledger.
- Produces: at most three records containing `observed_failure`, `hypothesis`, `exact_change`, `affected_state_predicate`, `risk`, `kill_condition`, source SHAs, and `authority=false`.

- [ ] **Step 1: Write RED tests**

Test that the analyzer rejects unknown/private fields, rejects mismatched policy/deck/evaluator SHA, caps candidates at three, and rejects a candidate whose predicate is not expressible using the public allowlist (`turn`, `option_type`, `attack_damage`, visible active `id/hp/maxHp`, visible energy/resource counts, and legal option count).

- [ ] **Step 2: Run the focused tests and observe the expected missing-module failure**

Run:

```bash
TMPDIR=$(mktemp -d /tmp/cg-hypothesis-test.XXXXXX) PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_cg_p1_public_hypothesis_v1.py
```

Expected: collection fails with `ModuleNotFoundError` for the new module.

- [ ] **Step 3: Implement the minimal analyzer**

Load and verify source SHAs before parsing. Aggregate only public fields and terminal outcomes. Require minimum support, paired candidate/control evidence, and a signed kill condition. Emit a deterministic semantic SHA and never emit a training or teacher label.

- [ ] **Step 4: Run RED→GREEN and reload the generated manifest**

Run the focused test again, then `py_compile`, reload the manifest from bytes, and run the docs validator. Expected: all focused tests pass and semantic SHA is stable across reload.

### Task 3: Execute at most three new policy-only P2 candidates

**Files:**
- Create: `scripts/run_cg_p1_public_hypothesis_screen_v1.py`
- Create: `tests/meta_specialist/test_cg_p1_public_hypothesis_screen_v1.py`
- Create: `docs/evidence/autonomous-cg-p1-public-hypothesis-screen-20260814.md`

**Interfaces:**
- Consumes: Task 2 candidate manifest and immutable P1 package.
- Produces: fresh smoke/weighted48/common24/384/768 roots and a fail-closed evidence report.

- [ ] **Step 1: Write RED tests for stage gates**

Cover invalid smoke, fault-inclusive denominator, paired key mismatch, seat collapse, candidate/control identity mismatch, and automatic stop after a negative stage.

- [ ] **Step 2: Run the focused tests to observe RED**

Use the same isolated `TMPDIR` command as Task 2; expected failure is missing runner behavior.

- [ ] **Step 3: Implement the runner by wrapping existing evaluator functions**

Do not modify `parallel_cabt_evaluator_v1` or production `cg` sources. Use ResourceGovernor admission, `workers=12`, `recycle=16` for 48/96 and `recycle=64` for 384/768. Preserve exact P1 control and stop at the first failed gate.

- [ ] **Step 4: Run smoke and weighted48 for each candidate**

Run candidates in parallel only when their output roots and seeds are disjoint. A candidate with any invalid/fault row is `INVALID/STOP`, not a scored loss.

- [ ] **Step 5: Run common24 and one 384 only for the strongest reproducible candidate**

Require positive delta, both-seat support, no fault/invalid, and exact paired strata at each stage. Update the research parent only if the candidate beats P1 at 384 and 768; never update Champion or SubmissionEligibleBestKnown here.

### Task 4: Connect a winning P2 to alternating deck-policy evaluation and the long-loop boundary

**Files:**
- Modify only if Task 3 produces a verified P2: `src/mage_ptcg/meta_specialist/cg_alternating_runtime_v1.py`
- Create: `scripts/run_cg_policy_deck_alternating_v2.py`
- Create: `tests/meta_specialist/test_cg_policy_deck_alternating_v2.py`
- Create: `docs/evidence/autonomous-cg-policy-deck-alternating-v2-20260814.md`

**Interfaces:**
- Consumes: verified P2 package and frozen root deck.
- Produces: `POLICY_FIXED_SHORT` deck candidates, then `DECK_FIXED_LONG` policy candidates, with rollback metadata and a no-training permission gate.

- [ ] **Step 1: Add RED tests for phase identity and rollback**

Reject simultaneous policy+deck changes, reject a missing rollback point, reject a non-reproducible parent, and preserve prior-best bytes after a failed phase.

- [ ] **Step 2: Implement the thin adapter**

Reuse `CgPackageSpecV1`, `validate_cg_pair_v1`, `build_cg_pair_games_v1`, and `run_parallel_cabt_evaluation`. Do not invent a second evaluator or silently enable training.

- [ ] **Step 3: Run the focused alternating smoke and package reload**

Expected: source/policy/deck/manifest hashes re-derive from bytes, rollback is atomic, and execution remains research-only unless all earlier gates are satisfied.

- [ ] **Step 4: Assess the self-owned rollout boundary**

If no issuered permission and public projection source are available, write a blocked readiness artifact only. Do not fabricate a permission manifest or convert native behavior into teacher labels. If a valid self-owned source is present, use the existing public-only collector contract and keep training authority explicit and separately gated.

## Verification and handoff

- Run focused tests for each task plus the nearest evaluator tests.
- Run `PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py`.
- Run `git diff --check` and `py_compile` for all new Python files.
- Update `docs/status/current_status.md`, `docs/status/handoff.md`, and the single full-history context pack only with verified results and SHA-256 values.
- Leave commit/push/Kaggle submission untouched unless a separate explicit submission action is requested.
