# Meta Specialist PIMC/ISMCTS Search Teacher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the PIMC/ISMCTS Search Teacher Adapter (`teachers`) under `src/mage_ptcg/meta_specialist/` with strict hidden-information leak guards and root visit distribution targets, as specified in `docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/teachers.py` — Determinization sampling from redacted public state, search RNG derivation, PIMC root visit target computation, and leak audit guards.
- `tests/meta_specialist/test_meta_specialist_teachers.py` — Focused TDD tests for determinization reproducibility, root target generation, and hidden-information leak invariance.

---

## Tasks

### Task 1: Determinization & Search Teacher Target Computation (`teachers.py`)
- Implement `sample_determinization(redacted_public_observation, rng_seed)`.
- Implement `compute_pimc_root_targets(envelope, visit_counts)` returning normalized probability distributions over legal complete actions.
- Implement `assert_hidden_info_leak_free(teacher_fn, state1, state2)` guard for audit.

---

## Verification
- Unit tests in `tests/meta_specialist/test_meta_specialist_teachers.py`.
- Regression check via `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist -q`.
