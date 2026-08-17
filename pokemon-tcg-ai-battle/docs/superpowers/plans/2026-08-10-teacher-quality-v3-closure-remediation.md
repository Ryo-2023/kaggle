# Teacher-quality v3 Closure Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seal the complete import/runtime closure used by the actual frozen teacher-quality panel and reject pre-existing non-regular final evidence artifacts before any calibration game can run.

**Architecture:** Extend the existing FD-pinned `SourceSnapshotV3` authority rather than introducing a second source root. The snapshot will contain the root `cg` package, and every isolated import/attempt child will execute with a cwd derived from the inherited sealed root descriptor. Evidence publication remains relative to `_OutputRootV3.descriptor`, but atomic replacement is permitted only when the existing destination is absent or a regular file.

**Tech Stack:** Python 3.12, `os.open`/`dir_fd`/`O_NOFOLLOW`, `/proc/self/fd`, `subprocess`, pytest.

## Global Constraints

- Do not run CABT games, calibration, the 384-game campaign, GPU training, Kaggle submission, `git commit`, or `git push` in this plan.
- Preserve all unrelated dirty and untracked workspace changes; edit only the files listed by each task.
- The actual frozen nine-policy panel, not a replacement fixture that omits real imports, is the import-closure oracle.
- Child imports and games must not read source, `deck.csv`, or optional card data through the original worktree cwd.
- The source snapshot and output directory remain capability-pinned by open directory descriptors; path strings are diagnostic labels only.
- Existing symlink, directory, FIFO, socket, or other non-regular output leaves fail closed and are never replaced.
- Every behavioral fix is TDD: first reproduce RED, then implement the minimum change, then run focused and combined regression suites.
- New Japanese documentation and reports are written in Japanese; code identifiers and existing English test style remain unchanged.

---

### Task 1: Seal `cg` and the child working directory for the actual frozen panel

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/teacher_quality_worker_v3.py`
- Modify: `tests/meta_specialist/test_teacher_quality_evidence_v3.py`
- Create: `.superpowers/sdd/2026-08-10-teacher-quality-v3-closure-remediation/task-1-report.md`

**Interfaces:**
- Consumes: `_resolve_required_snapshot_entries(plan)`, `SourceSnapshotV3.root_fd`, `_isolated_snapshot_bootstrap(root_fd)`, `validate_snapshot_policy_imports_v3(...)`, `run_teacher_quality_worker_v3(...)`.
- Produces: a source snapshot containing `cg/**` and isolated children whose cwd is the inherited snapshot-root capability for both preflight and actual attempt execution.

- [ ] **Step 1: Add an actual-panel closure regression that fails before CABT**

  Build the same plan-only input used by the current CLI, seal it, obtain `_snapshot_policy_import_paths_v3(plan, snapshot)`, and assert all nine actual frozen policies import in `validate_snapshot_policy_imports_v3`. Assert the manifest contains the required `cg` Python/native-library entries and that no `__pycache__`, `.pyc`, `.pyo`, or host-only path is present. The old implementation must fail with `ModuleNotFoundError: cg` represented by `snapshot policy import preflight failed`.

- [ ] **Step 2: Add cwd-poison regressions for import probe and attempt worker**

  Run the isolated child from a host directory containing poison `deck.csv`, `data/raw/extracted/EN_Card_Data.csv`, and an importable poison `cg`. In the frozen policy fixture, record/validate the cwd and data identity observed during import. Require cwd to resolve through `/proc/self/fd/<root_fd>` to sealed bytes, and require the poison markers to remain unread. Cover both `validate_snapshot_policy_imports_v3` and `run_teacher_quality_worker_v3`.

- [ ] **Step 3: Run the new tests and verify RED**

  Run:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
    tests/meta_specialist/test_teacher_quality_evidence_v3.py \
    -k 'actual_frozen_panel or sealed_cwd or cwd_poison or cg_closure'
  ```

  Expected: at least one actual-panel import failure for missing `cg` and at least one cwd assertion failure because the child inherits the host cwd.

- [ ] **Step 4: Extend the snapshot closure at the source**

  In `_resolve_required_snapshot_entries`, add the root `cg` tree through the same descriptor-relative `_tree_entries` path used for `src/mage_ptcg` and `agents`. Preserve cache/binary exclusion rules already enforced by `_tree_entries`; do not import `cg` from the live worktree at runtime and do not add a host `sys.path` fallback.

- [ ] **Step 5: Pin child cwd to the sealed root capability**

  Add a helper that derives `sealed_root = f"/proc/self/fd/{snapshot.root_fd}"`, validates the inherited descriptor, and supplies that exact path as `cwd=` to every isolated policy-import, engine-import, and attempt subprocess. Inside the worker bootstrap, verify `os.stat('.')` matches `os.fstat(root_fd)` before importing policy code. Do not use `snapshot.root` or the original repository cwd as runtime authority.

- [ ] **Step 6: Run focused and combined verification**

  Run:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
    tests/meta_specialist/test_teacher_quality_evidence_v3.py
  ```

  Also run the real frozen nine-policy non-CABT import probe and the sealed generic opponent bridge for teacher/Rule v0 × seat 0/1. Record hashes and pass/fail only; do not start CABT.

- [ ] **Step 7: Record the task report without committing**

  Write RED evidence, exact changed files, exact commands, outputs, actual-panel import result, cwd poison result, and remaining concerns to the task report. Run `python -m py_compile` on edited modules and `git diff --check --` on the listed files. Do not stage or commit.

---

### Task 2: Reject pre-existing non-regular final evidence leaves

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/teacher_quality_evidence_v3.py`
- Modify: `tests/meta_specialist/test_teacher_quality_evidence_v3.py`
- Create: `.superpowers/sdd/2026-08-10-teacher-quality-v3-closure-remediation/task-2-report.md`

**Interfaces:**
- Consumes: `_OutputRootV3.descriptor`, `_OutputRootV3.assert_current()`, `_OutputRootV3.atomic_write(name, raw)`.
- Produces: atomic evidence publication that rejects any existing non-regular destination leaf before creating or renaming the temporary file.

- [ ] **Step 1: Add exact symlink and non-regular RED regressions**

  Parameterize `result.json` and `manifest.json`. Pre-create each as a symlink to an outside sentinel and assert collection raises `ValueError`, the symlink still exists, and outside bytes remain unchanged. Add at least one directory or FIFO destination case using `_OutputRootV3.atomic_write` directly and require the same fail-closed behavior.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
    tests/meta_specialist/test_teacher_quality_evidence_v3.py \
    -k 'result_leaf_symlink or manifest_leaf_symlink or non_regular_output_leaf'
  ```

  Expected: the old implementation replaces the symlink name, so the test fails because no `ValueError` is raised and the symlink disappears.

- [ ] **Step 3: Add descriptor-relative destination validation**

  Before temporary-file creation, call `os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)`. Permit `FileNotFoundError`; permit an existing regular file; reject every other mode, including symlinks, with `ValueError`. Re-run `assert_current()` immediately before `os.replace()`, re-check the destination leaf, and reject if its absence/regular identity changed during the write so a concurrent non-regular substitution cannot be silently replaced.

- [ ] **Step 4: Run focused and combined verification**

  Run the Task 2 focused test, then:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
    tests/meta_specialist/test_teacher_quality_evidence_v3.py
  ```

  Confirm normal absent-leaf creation and regular-file replacement still pass, output-root path-swap tests still pass, and no outside sentinel changes.

- [ ] **Step 5: Record the task report without committing**

  Write RED/GREEN evidence, exact commands, test counts, file list, and concurrency assumptions to the task report. Run `python -m py_compile`, `git diff --check --`, and `git status --short`. Do not stage or commit.

---

### Task 3: Final Sol high integrity review and execution handoff

**Files:**
- Create: `.superpowers/sdd/2026-08-10-teacher-quality-v3-closure-remediation/final-review.md`
- Modify: `.superpowers/sdd/2026-08-10-teacher-quality-v3-closure-remediation/progress.md`

**Interfaces:**
- Consumes: Task 1 and Task 2 reports plus their exact current workspace files.
- Produces: an independent `READY_FOR_NON_CABT_PREFLIGHT` or `BLOCKED` verdict; it does not authorize calibration, the full campaign, GPU training, or submission.

- [ ] **Step 1: Give Sol high the exact review package**

  The reviewer must inspect the actual frozen nine-policy snapshot and run adversarial tests for missing imports, package collisions, host cwd/source/data poison, root descriptor/path replacement, existing result/manifest symlinks, and non-regular leaf substitution. It must not weaken tests to fixtures that omit real `cg` imports.

- [ ] **Step 2: Run the full non-CABT regression bundle**

  Run all teacher-quality worker/evidence tests, teacher-quality v2/v3 and theta0 tests, recurrent dataset v3/v4 tests, `scripts/docs/validate_docs.py`, `py_compile`, and `git diff --check`. No CABT or GPU command is allowed in this task.

- [ ] **Step 3: Record the gated handoff**

  If and only if Sol high reports no Critical/Important finding, record `READY_FOR_NON_CABT_PREFLIGHT` and list the next external blockers: actual teacher smoke status, approved performance-rule digest, actual calibration evidence, full 384-game evidence, full-corpus v4 preflight, CUDA recurrent Gate, theta0 seal, and short multi-seed pilot. Otherwise record `BLOCKED` with exact reproducer and do not advance.
