# Meta Specialist CLI Expansion & End-to-End Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand `mage_ptcg.meta_specialist.cli` to expose subcommands for census parsing, calibration, V-trace evaluation, curriculum management, and orchestrator status, and add an end-to-end integration test suite under `tests/meta_specialist/`.

---

## File Structure & Proposed Changes

- `src/mage_ptcg/meta_specialist/cli.py` — Add subcommands `parse-census`, `calibrate-strength`, `step-curriculum`, `run-global-eval`, and `show-orchestrator-status`.
- `scripts/run_meta_specialist_pipeline.py` — Standalone entrypoint script for running the full meta-specialist workflow.
- `tests/meta_specialist/test_meta_specialist_e2e_integration.py` — E2E integration tests verifying the full pipeline workflow.

---

## Tasks

### Task 1: CLI Subcommand Expansion (`cli.py`)
- Implement `parse-census`, `calibrate-strength`, `step-curriculum`, `run-global-eval`, and `show-orchestrator-status` in `cli.py`.

### Task 2: Pipeline Standalone Script (`scripts/run_meta_specialist_pipeline.py`)
- Implement non-submitting end-to-end pipeline runner script.

### Task 3: End-to-End Integration Tests (`test_meta_specialist_e2e_integration.py`)
- Test entire workflow from census data parsing to candidate selection and CLI execution.

---

## Verification
- Unit and E2E tests in `tests/meta_specialist/`.
- Full project pytest suite execution.
