# Meta Specialist Census & Strength Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the isolated Census analysis and Opponent Strength Calibration pipeline (`meta_census` and `strength_calibration`) under `src/mage_ptcg/meta_specialist/` according to `docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/census.py` — Parse leaderboard snapshot data, classify deck archetypes, compute HHI/coverage, and publish `CensusManifest` and `MetaAnalysisManifest`.
- `src/mage_ptcg/meta_specialist/calibration.py` — Maintain reference panels, evaluate cross-play matchup matrices, compute confidence intervals and strength bands.
- `tests/meta_specialist/test_meta_specialist_census.py` — Focused TDD tests for census parsing and archetype classification.
- `tests/meta_specialist/test_meta_specialist_calibration.py` — Focused TDD tests for strength calibration and matchup matrix calculation.

---

## Tasks

### Task 1: Census Data Parsing and Archetype Classifier (`census.py`)
- Parse leaderboard snapshot CSV (`rank, team_id, submission_id, ...`).
- Categorize decks into the 5 core archetypes (`alakazam`, `grimmsnarl_froslass_munkidori`, `crustle_mega_kangaskhan`, `rocket_mewtwo_spidops`, `archaludon`) or `unclassified`.
- Compute band counts (Gold 1..22, Silver 23..305, Bronze 306..609).
- Generate content-addressed `CensusManifest` payload.

### Task 2: Opponent Strength Calibration (`calibration.py`)
- Define `CalibrationReferencePanelManifest` structure.
- Compute matchup win rates and Wilson 95% confidence intervals against reference panels.
- Assign strength bands (`high`, `middle`, `broad`, `ambiguous`) to opponents based on empirical performance.

---

## Verification
- Unit tests in `tests/meta_specialist/test_meta_specialist_census.py` and `test_meta_specialist_calibration.py`.
- Regression check via `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist -q`.
