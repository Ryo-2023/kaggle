# Outcome-Weighted V4 BC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seal one bounded V4 research arm that weights actor-visible teacher decisions by the sealed episode outcome, then evaluate both training seeds against the matching Wave6 baseline without granting promotion authority.

**Architecture:** Add a pure outcome-weight helper with a max-normalized 3:1 win/loss ratio. Extend the research-only recurrent BC trainer with an explicit `RESEARCH_ONLY_OUTCOME_WEIGHTED` mode that accepts non-uniform episode weights while preserving the existing uniform mode. Extend `scripts/run_v4_qualified_teacher_snapshot_bc.py` with `--outcome-weighted`; it reads `teacher.value_target` from each sealed record, assigns one weight to every prefix of that episode, excludes test records, and records the exact objective/config hashes. No production runtime, semantic decoder, external policy, deck, or submission path changes.

**Tech Stack:** Python 3, PyTorch, existing `RecurrentBCSequenceV4`/`train_recurrent_bc_v4`, sealed JSONL snapshots, pytest, existing V4 fixed-six/shadow evaluators.

## Global Constraints

- Research-only artifacts have `promotion_authority=false`; no Champion change, Kaggle submission, commit, or push.
- The sealed snapshot's train/development/test episode split is immutable; test records never enter training or validation.
- The teacher outcome is read only from the sealed actor-visible record's `teacher.value_target`; no private engine state or new outcome inference is allowed.
- Outcome weights are finite, positive, max-normalized to `1.0`, and fixed at win `1.0`, draw `2/3`, loss `1/3`; no weight sweep.
- Existing `RESEARCH_ONLY_UNIFORM_WEIGHT` behavior remains unchanged and rejects non-uniform quality weights.
- The experiment uses the Lucifer19 snapshot, Wave6 seed-matched initialization, epochs `1`, learning rate `1e-4`, TBPTT `8`, burn-in `1`, CUDA `cuda:0`, and the existing fixed-six protocol.

---

### Task 1: Add and test the outcome-weight contract

**Files:**
- Create: `src/mage_ptcg/meta_specialist/outcome_weighted_v4.py`
- Test: `tests/meta_specialist/test_outcome_weighted_v4.py`

**Interfaces:**
- Produces `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4: str`.
- Produces `outcome_quality_weight_v4(value_target: object) -> float`.
- Produces `outcome_weight_summary_v4(targets: Sequence[float]) -> dict[str, object]` for run provenance.

- [x] **Step 1: Write failing contract tests.**

```python
def test_outcome_quality_weight_is_max_normalized() -> None:
    assert outcome_quality_weight_v4(1.0) == 1.0
    assert outcome_quality_weight_v4(0.0) == 2.0 / 3.0
    assert outcome_quality_weight_v4(-1.0) == 1.0 / 3.0

def test_outcome_quality_weight_rejects_nonfinite_or_unknown_targets() -> None:
    with pytest.raises(ValueError):
        outcome_quality_weight_v4("win")
    with pytest.raises(ValueError):
        outcome_quality_weight_v4(float("nan"))

def test_outcome_summary_is_deterministic() -> None:
    assert outcome_weight_summary_v4([1.0, -1.0, 0.0]) == {
        "targets": {"win": 1, "draw": 1, "loss": 1},
        "weights": {"win": 1.0, "draw": 2.0 / 3.0, "loss": 1.0 / 3.0},
        "ratio_win_to_loss": 3.0,
    }
```

- [x] **Step 2: Run the focused test and verify it fails.**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_outcome_weighted_v4.py`

Expected: FAIL because the module and functions do not exist.

- [x] **Step 3: Implement the pure helper.**

Validate `value_target` as a finite `int`/`float` excluding `bool`, accept only `-1.0`, `0.0`, and `1.0`, return the fixed mapping above, and build the summary with sorted fixed keys and no filesystem access.

- [x] **Step 4: Run the focused test and verify it passes.**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_outcome_weighted_v4.py`

Expected: all tests pass.

### Task 2: Make recurrent BC explicitly accept the new research mode

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/recurrent_bc_v4.py`
- Test: `tests/meta_specialist/test_recurrent_bc_v4.py`

**Interfaces:**
- `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4` is accepted by `_require_research_mode` and `train_recurrent_bc_v4`.
- `RESEARCH_ONLY_UNIFORM_WEIGHT` still requires every research-only step to have `quality_weight == 1.0`.
- Outcome mode accepts existing `RecurrentBCStepV4` quality weights in `(0, 1]` and uses them in the existing per-sequence normalization; no new gradient formula is introduced.

- [x] **Step 1: Add failing mode-contract tests.**

Create minimal valid V4 sequences from the existing test fixtures and assert that a sequence containing `quality_weight=1/3` is rejected under uniform mode and accepted by `_validate_sequences` under outcome mode. Also assert that the new constant appears in the trainer's explicit mode identity.

- [x] **Step 2: Run the focused test and verify it fails.**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_recurrent_bc_v4.py -k 'outcome or uniform'`

Expected: FAIL because the new mode is not accepted.

- [x] **Step 3: Implement the smallest mode extension.**

Import the new mode constant, let `_require_research_mode` return either explicit research mode, and gate the uniform `quality_weight == 1.0` assertion on the uniform mode. Keep all existing `supervision_weight`, reach-mass, finite-value, partition, and episode-boundary checks unchanged.

- [x] **Step 4: Run the focused and regression tests.**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_recurrent_bc_v4.py tests/meta_specialist/test_outcome_weighted_v4.py`

Expected: all selected tests pass and existing uniform-mode tests remain green.

### Task 3: Add the snapshot materializer flag and provenance

**Files:**
- Modify: `scripts/run_v4_qualified_teacher_snapshot_bc.py`
- Test: `tests/meta_specialist/test_run_v4_qualified_teacher_snapshot_bc.py`

**Interfaces:**
- `_materialize_sequences(root, burn_in, exclude_empty_selection, outcome_weighted=False)` returns the same train/validation sequence types plus stats.
- `--outcome-weighted` selects the new trainer mode and fixed per-episode weights; without it, behavior is byte-for-byte uniform in objective semantics.
- Stats include `outcome_weight_policy`, `outcome_counts_by_partition`, and `outcome_weight_by_partition`.

- [x] **Step 1: Write failing materializer tests.**

Use a temporary sealed fixture or the existing snapshot fixture to assert that every projected step in a winning episode has weight `1.0`, every losing episode has `1/3`, draw has `2/3`, all prefixes of one record share the same weight, and the test partition is excluded.

- [x] **Step 2: Run the focused test and verify it fails.**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_run_v4_qualified_teacher_snapshot_bc.py -k outcome`

Expected: FAIL because the flag/helper path does not exist.

- [x] **Step 3: Implement materialization.**

Read `record["teacher"]["value_target"]` once per record, validate it with `outcome_quality_weight_v4`, pass that weight into every `RecurrentBCStepV4` for the record, keep empty-selection handling orthogonal, and record per-partition counts/weights. Reject a missing or inconsistent episode target rather than guessing.

- [x] **Step 4: Wire the CLI and trainer invocation.**

Set `mode=RESEARCH_ONLY_OUTCOME_WEIGHTED_V4` only when `--outcome-weighted` is present, include the flag, mode, helper version, summary, and exact objective/trainer hashes in `report.json`, and retain `promotion_authority=false`.

- [x] **Step 5: Run focused tests and static verification.**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_run_v4_qualified_teacher_snapshot_bc.py tests/meta_specialist/test_recurrent_bc_v4.py tests/meta_specialist/test_outcome_weighted_v4.py` and `.venv/bin/python -m py_compile scripts/run_v4_qualified_teacher_snapshot_bc.py src/mage_ptcg/meta_specialist/outcome_weighted_v4.py`.

Expected: all tests pass and compilation succeeds.

### Task 4: Execute the pre-registered two-seed pilot and evaluate it

**Files:**
- Create: `runs/meta-specialist-v4-qualified-lucifer19-48-outcome-weighted-bc-20260812/` (research artifacts only)
- Use: `scripts/measure_v4_checkpoint_strength.py`

**Interfaces:**
- Inputs are the sealed Lucifer19 snapshot index SHA `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3`, Wave6 seed0/1 checkpoints, subject deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`, and existing fixed-six protocol.
- Train exactly one epoch per seed on `cuda:0`, `lr=1e-4`, `TBPTT=8`, `burn-in=1`, `patience=0`, no test records.
- Evaluate each candidate and matching Wave6 baseline with 24 fixed-six games (2 games/seat/opponent), fault denominator intact.

- [x] **Step 1: Run the research training command with immutable output.**

Use the existing runner with `--outcome-weighted`, the exact Lucifer snapshot root, existing Wave6 hashes, and a new output root. Refuse to overwrite an existing output.

- [x] **Step 2: Verify training artifacts.**

Check report schema, mode, outcome counts/weights, test exclusion, 2 seeds, one epoch, fault-free completion, checkpoint file/tensor hashes, and objective identity before evaluation.

- [x] **Step 3: Run fixed-six candidate evaluations.**

Use the existing evaluator against `opponents/lucifer19_battlecore/deck.csv`, `--games-per-seat 2`, `--base-seed 10100000`, `--max-steps 2000`, and store one JSON per seed.

- [x] **Step 4: Apply the gate.** — 固定六不合格（seed0下振れ、seed1 seat1非悪化違反）のためshadow-Bへ進まない。

Continue to shadow-B only if both candidate seeds are at least their matching Wave6 baseline on fixed-six, both seats are non-degraded, and faults are zero. Otherwise mark the arm rejected and do not run shadow-B. If fixed-six passes, run shadow-B at 48 games/seed and require both seeds non-degraded plus aggregate improvement of at least 5 percentage points before considering any longer run.

### Task 5: Record the decision and update handoff/context

**Files:**
- Modify: `docs/evidence/performance-first-audit-20260812.md`
- Modify: `docs/status/chatgpt_context_pack_2026-08-12.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

- [x] **Step 1: Record exact hashes, counts, screen results, and gate status.**

State explicitly whether outcome-weighted BC improved fixed-six and whether it generalized; distinguish NLL changes from CABT results and keep deck identity caveats.

- [x] **Step 2: Run documentation and code verification.**

Run `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py`, `git diff --check`, focused pytest, and `py_compile`.

- [x] **Step 3: Leave the goal active unless the full performance objective is proven.**

Do not mark the mission complete unless a candidate beats the current best with both seeds and sufficient fixed-six/shadow evidence and is ready for a justified longrun. Do not commit, push, change Champion, or submit.

### Post-run correction note

The first report used a trainer implementation in which constant episode quality weights canceled between the loss numerator and normalization denominator. A focused gradient regression reproduced the cancellation. The live trainer was corrected to preserve episode quality in the gradient, and a separate immutable two-seed rerun was completed. The corrected arm still failed the fixed-six gate (seed0 12/24 vs Wave6 15/24; seed1 14/24 vs 10/24; seed0 seat1 4/12 vs 6/12), so no shadow-B or longrun follows. The old artifact remains historical and is not treated as evidence of effective outcome weighting.
