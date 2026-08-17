# Meta Specialist Global Evaluation & Submission Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the Global Evaluation & Submission Selection pipeline (`global_evaluation`) in `src/mage_ptcg/meta_specialist/` as specified in `docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/global_evaluation.py` — SealedScenarioBank binding, Holm-Bonferroni alpha spending, Global Submission Race, and primary/backup candidate decision.
- `tests/meta_specialist/test_meta_specialist_global_evaluation.py` — Focused TDD tests for Holm alpha adjustment and primary/backup selection logic.

---

## Tasks

### Task 1: Holm-Bonferroni Alpha Adjustment (`global_evaluation.py`)
- Implement `adjust_p_values_holm(p_values)` for sequential multi-candidate evaluation.

### Task 2: Global Submission Race & Selection Decision (`global_evaluation.py`)
- Define `CandidateEvalResult` and `GlobalSubmissionDecision`.
- Select 1 `primary_bundle` and max 1 `backup_bundle` while validating band safety and candidate status.

---

## Verification
- Unit tests in `tests/meta_specialist/test_meta_specialist_global_evaluation.py`.
- Regression check via `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist -q`.
