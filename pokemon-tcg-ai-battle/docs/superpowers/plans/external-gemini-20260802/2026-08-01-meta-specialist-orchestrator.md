# Meta Specialist Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the Workflow Orchestrator (`orchestrator`) in `src/mage_ptcg/meta_specialist/` to execute end-to-end pipeline steps and generate `CandidateSetManifest` as specified in `docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/orchestrator.py` — Orchestrates pipeline phases (Census, Qualification, Search, Curriculum, Global Eval) and outputs overall candidate manifests.
- `tests/meta_specialist/test_meta_specialist_orchestrator.py` — Focused TDD tests for pipeline stage execution and manifest tracking.

---

## Tasks

### Task 1: Pipeline Orchestrator & Candidate Manifest Tracking (`orchestrator.py`)
- Implement `MetaSpecialistOrchestrator` to track active candidate lanes (`registered_unqualified`, `qualified_not_trained`, `trained_champion`, `withdrawn`).
- Implement `run_pipeline_phase(phase_name)`.

---

## Verification
- Unit tests in `tests/meta_specialist/test_meta_specialist_orchestrator.py`.
- Regression check via `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist -q`.
