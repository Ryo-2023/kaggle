# Outcome-only iteration-1 and bounded action screen Implementation Plan

> **For agentic workers:** This plan is executed inline with TDD; no commit, push, submission, or production-agent edits.

**Goal:** Derive a strict META_TRAIN-only hard-negative schedule from the sealed 384 candidate ledger and materialize up to two new bounded action candidates for paired 96-game screens.

**Architecture:** A read-only WDL schedule builder consumes only terminal WDL, opponent/family/seat/seed, and sealed identity fields from the 384 ledger. A separate candidate bridge reuses the sealed policy-fixed bridge factory, permits only bounded public action-type deltas, and emits exact candidate/control strata with authority false; evaluation starts only after strict reload and fault/seat gates.

**Tech Stack:** Python, canonical JSON/SHA-256 manifests, pytest, existing `EvaluationGameV1` and research-only Rule v0 action overlay.

## Global Constraints

- Do not edit `main.py`, `agents/rule_agent.py`, production evaluator, parent bridge, or prior performance artifacts.
- Do not use action labels, private fields, teacher behavior, synthetic opponents, META_FINAL, training, promotion, submission, or longrun authority.
- Use TDD RED→GREEN; all generated artifacts go to fresh run roots and are atomic/no-clobber.
- Candidate screen limit is two new bounded deltas; ATTACK+120 is not rerun.

### Task 1: Iteration-1 WDL schedule

Create a strict module/CLI/tests/evidence. Verify the 384 candidate ledger has exactly 768 rows, DONE/fault-free terminal WDL, candidate identity and evaluator SHA, 4×96 block and META_TRAIN opponent strata. Recompute deterministic hard-negative weights/quotas from candidate WDL only; reject action/private/teacher keys, heldout IDs, malformed rows, source SHA drift, and any authority true. Materialize `schedule.json` with source ledger SHA, formula/cap/floor/quota, 20 META_TRAIN IDs, heldout exposure zero, and strict reload.

### Task 2: bounded action candidate screen

Create new bridge module/CLI/tests/evidence that verifies iteration-1 schedule, reuses the existing Rule v0 action overlay, accepts at most two non-ATTACK candidate configs with absolute delta ≤120, and emits paired 96-game control/candidate sidecars using exact schedule strata. Reject unknown/unbounded deltas, heldout exposure, synthetic/local permission violations, identity drift, and execution authority. Only after GREEN and strict reload may the parent run the new 96-game screens; positive candidates alone may advance to 384.
