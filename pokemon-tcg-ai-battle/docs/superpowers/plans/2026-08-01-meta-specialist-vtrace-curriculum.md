# Meta Specialist V-Trace Learner & Curriculum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the Recurrent V-Trace learner adapter and multi-phase curriculum manager (`vtrace_learner` and `curriculum`) in `src/mage_ptcg/meta_specialist/` as specified in `docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/vtrace.py` — Compute V-trace targets (rhos, c_ts, target values) for complete-action trajectories with clipping and mask safety.
- `src/mage_ptcg/meta_specialist/curriculum.py` — Manage curriculum phases (`broad`, `middle`, `high`, `all-band consolidation`), preserving rehearsal floors for prior bands and tracking lineage.
- `tests/meta_specialist/test_meta_specialist_vtrace.py` — Focused TDD tests for V-trace target computation and numerical stability.
- `tests/meta_specialist/test_meta_specialist_curriculum.py` — Focused TDD tests for curriculum phase transitions and opponent mixture ratios.

---

## Tasks

### Task 1: Recurrent V-Trace Target Computation (`vtrace.py`)
- Implement `compute_vtrace_targets(behaviour_log_probs, target_log_probs, rewards, values, bootstrap_value, gamma, rho_clip, c_clip)`.
- Support sequence unrolls and ensure strict numerical bounds.

### Task 2: Multi-Phase Curriculum Manager (`curriculum.py`)
- Define `CurriculumPhase` enum (`BROAD`, `MIDDLE`, `HIGH`, `CONSOLIDATION`).
- Build `CurriculumManager` to manage opponent sampling weights and transition criteria.

---

## Verification
- Unit tests in `tests/meta_specialist/test_meta_specialist_vtrace.py` and `test_meta_specialist_curriculum.py`.
- Regression check via `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist -q`.
