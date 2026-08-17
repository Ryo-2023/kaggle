# Meta Specialist Two-Timescale Deck Search & Mutation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement constrained deck mutation, core signature validation, and Successive Halving deck search (`deck_search`) in `src/mage_ptcg/meta_specialist/` as specified in `docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/deck_search.py` — Core-preserving deck mutation, Successive Halving arm selection, and `DeckGenomeManifest` generation.
- `tests/meta_specialist/test_meta_specialist_deck_search.py` — Focused TDD tests for deck mutation, 60-card validity, core preservation, and Successive Halving.

---

## Tasks

### Task 1: Core-Preserving Deck Mutation & Genome Tracking (`deck_search.py`)
- Implement `mutate_deck(parent_card_ids, spec, replacement_card_ids, mutation_type="flex")`.
- Validate exact 60-card count and core signature preservation.
- Generate `DeckGenomeManifest` payload.

### Task 2: Successive Halving Arm Selection (`deck_search.py`)
- Implement `select_successive_halving_arms(candidate_arms, round_num, num_to_keep)` preserving broad and random reserved slots.

---

## Verification
- Unit tests in `tests/meta_specialist/test_meta_specialist_deck_search.py`.
- Regression check via `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist -q`.
