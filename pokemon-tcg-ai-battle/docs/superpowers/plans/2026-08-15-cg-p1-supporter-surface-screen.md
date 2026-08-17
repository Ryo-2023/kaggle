# CG P1 Supporter Surface Screen Implementation Plan

> **For agentic workers:** Execute the tasks inline in this session with review checkpoints. The repository forbids commit/push without explicit user authorization, so this plan intentionally ends at verified uncommitted artifacts.

**Goal:** Add three hash-bound, research-only Supporter priority overlays to the fixed P1 cg policy and evaluate them through the existing paired CABT screen without changing BestKnown, Champion, deck, or submission state.

**Architecture:** A new candidate module renders each overlay by appending a small wrapper to the immutable P1 `main.py`. A thin screen adapter monkey-patches the existing P1 paired runner exactly as the v2 adapter does, so candidate/control strata, worker lease, package materialization, and summary contracts remain shared. The screen is diagnostic on reused meta; only a future fresh/unused-meta gate can promote a candidate.

**Tech Stack:** Python 3.11+, `pytest`, `cg.api`, existing `run_cg_p1_variant_screen_v1.py`, existing `parallel_cabt_evaluator_v1.py`.

## Global Constraints

- Keep P1 source SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` immutable.
- Use only actor-visible state: own hand count, public turn marker, `supporterPlayed`, and visible opponent active HP.
- Candidate and control use identical opponent/seat/seed strata; workers remain 12 and research authority remains false.
- Do not modify `deck.csv`, production `main.py`, Champion, submission package, or Kaggle external state.
- A positive reused-meta screen is diagnostic only; no CEM update, deck mutation, promotion, or submission follows automatically.

---

### Task 1: Define and test the Supporter candidate surface

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v3.py`
- Create: `tests/meta_specialist/test_cg_p1_policy_candidate_v3.py`

**Interfaces:**
- `VARIANT_IDS: tuple[str, ...]` contains `cg-p1-lillie-early-v1`, `cg-p1-boss-ko-v1`, and `cg-p1-carmine-lowhand-v1`.
- `render_p1_variant_source_v3(candidate_id: str) -> str` returns compilable source or raises `ValueError`.
- `materialize_p1_variant_package_v3(source_package, output_package, candidate_id) -> dict[str, object]` copies the fixed package and binds source/deck/policy hashes.

- [x] **Step 1: Write the failing test**

  Assert the exact variant tuple, compile every rendered source, require the research marker and each condition marker, and assert an unknown ID raises `ValueError`.

- [x] **Step 2: Run the focused test and verify RED**

  Run: `PYTHONPATH=src python -m pytest -q tests/meta_specialist/test_cg_p1_policy_candidate_v3.py`

  Expected: collection/import failure because the v3 module does not exist.

- [x] **Step 3: Implement the minimal hash-bound module**

  Append one `_play_score` wrapper per variant to the fixed P1 source:

  - Lillie: when `supporterPlayed` is false and `turn <= 2`, add `8000` to a legal Lillie PLAY score.
  - Boss: when `supporterPlayed` is false and visible opponent active HP is in `1..150`, add `12000` to a legal Boss's Orders PLAY score.
  - Carmine: when `supporterPlayed` is false, `turn >= 3`, and own visible `hand` count is at most `4`, add `6000` to a legal Carmine PLAY score.

  Every unsupported/malformed state returns the previously computed P1 score; source and package hashes must fail closed on mismatch.

- [x] **Step 4: Run the focused test and verify GREEN**

  Run the same pytest command. Expected: all tests pass with no warnings.

---

### Task 2: Add the v3 paired-screen adapter

**Files:**
- Create: `scripts/run_cg_p1_policy_candidate_v3_screen_v1.py`
- Create: `tests/meta_specialist/test_run_cg_p1_policy_candidate_v3_screen_v1.py`

**Interfaces:**
- The adapter exports `VARIANT_IDS`, `run_p1_variant_screen`, and `main`.
- CLI accepts the existing runner's `--candidate-id`, `--source-package`, `--config`, `--output`, `--base-seed`, `--workers`, `--games-per-opponent-seat`, and `--worker-recycle-games` arguments.

- [x] **Step 1: Write the failing test**

  Assert the exact v3 IDs, invoke `--help` through the file path (not stdin), and assert the existing budget validator accepts 2 games/opponent/seat with workers 12 and recycle 16.

- [x] **Step 2: Run the focused test and verify RED**

  Run: `PYTHONPATH=src python -m pytest -q tests/meta_specialist/test_run_cg_p1_policy_candidate_v3_screen_v1.py`

  Expected: import failure because the v3 adapter and module do not exist.

- [x] **Step 3: Implement the thin adapter**

  Import the v3 materializer and patch the existing runner's `VARIANT_IDS` and materializer reference. Do not duplicate arena or evaluator logic.

- [x] **Step 4: Run the focused test and verify GREEN**

  Run the same pytest command. Expected: all tests pass.

---

### Task 3: Run the bounded paired screen and seal evidence

**Files:**
- Create: `runs/final-sprint-autonomous/cg-p1-supporter-surface-screen-20260815/` (runner artifacts only)
- Modify: `docs/evidence/cg-p1-local-cem-and-carmine-tempo-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

- [x] **Step 1: Compile and smoke each candidate package**

  Use the v3 adapter's materializer and existing cg package verifier; reject any candidate with a source-hash mismatch, illegal deck, import failure, or fault.

- [x] **Step 2: Execute the research-only screen**

  Run each candidate separately with the fixed broad-24 config, `--base-seed 49620000`, `--games-per-opponent-seat 2`, `--workers 12`, and `--worker-recycle-games 16`. Never pipe the runner through `tee`.

- [x] **Step 3: Apply the stop gate**

  Record candidate/control wins, score delta, seat gap, faults, paired-strata equality, and `fresh_unused_meta=0`. Do not run independent confirmation unless a candidate is positive, fault-free, and seat-safe; if confirmation is run, label it reused-meta diagnostic and do not promote.

- [x] **Step 4: Update evidence and status**

  Record candidate policy SHA, archive/package SHA, run manifest SHA, exact command, result, and next gate. Keep P1/BestKnown/Champion/production/submission unchanged.

- [x] **Step 5: Verify the handoff**

  Run:

  ```bash
  PYTHONPATH=src python -m pytest -q tests/meta_specialist/test_cg_p1_policy_candidate_v3.py tests/meta_specialist/test_run_cg_p1_policy_candidate_v3_screen_v1.py
  python scripts/docs/validate_docs.py
  git diff --check
  ```

  Expected: focused tests pass, docs validator reports 13 canonical documents, and diff check is clean. Do not commit or push.
