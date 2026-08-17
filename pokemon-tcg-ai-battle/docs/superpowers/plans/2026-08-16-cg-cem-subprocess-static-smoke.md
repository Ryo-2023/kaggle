# CG CEM Subprocess Static Smoke Implementation Plan

> **For agentic workers:** この計画は同一セッションでinline実行する。ユーザーの明示許可なしにcommit、push、Champion変更、production変更、Kaggle提出を行わない。

**Goal:** CEM親プロセスからnative `cg` engineを完全に隔離し、static smoke後も複数spawn workerでfault-free CABT評価を実行できるようにする。

**Architecture:** CEM親は候補packageのcompile・hash・JSON契約だけを確認する。`cg.api`を必要とするruntime smokeは専用subprocessへ渡し、終了コードとJSON結果だけを親へ返す。subprocess終了後に既存のspawn-based `parallel_cabt_evaluator_v1`を起動し、native global stateを親とworkerの間で共有しない。

**Tech Stack:** Python 3.12、`subprocess.run`、既存の`parallel_cabt_evaluator_v1`、pytest、CABT native `cg` runtime。

## Global Constraints

- 対象はresearch-only CEM runnerだけとし、production `main.py`、Champion、提出package、Kaggle送信を変更しない。
- heavy CABT runはmain coordinatorだけが起動し、同一ファイルの同時編集は行わない。
- candidate／controlのpolicy、deck、opponent、seed、split、fault分母は既存CEM契約から変更しない。
- subprocessのstdout／stderrはboundedに保存し、秘密情報や大規模生ログをdocsへ貼り付けない。
- 検証はまず最小再現（8 games、2 workers）で行い、その後CEM retryへ拡大する。

### Task 1: subprocess static-smoke contract

**Files:**
- Create: `scripts/run_cg_static_smoke_v1.py`
- Modify: `scripts/run_cg_p1_cem_v1.py`
- Test: `tests/meta_specialist/test_run_cg_p1_cem_v1.py`

**Interfaces:**
- `run_cg_static_smoke_v1.py` consumes `--candidate-package`, `--control-package`, and `--output`; produces an atomic JSON report with `status`, `candidate_main_sha256`, `control_main_sha256`, `candidate_agent_contract`, and bounded `stderr_tail`.
- `run_cg_p1_cem_v1._static_smoke(candidate_package, control_package)` remains the caller-facing function but invokes the subprocess helper and raises a descriptive error for nonzero exit or malformed report.

- [ ] **Step 1: Write the failing regression test**

  Add a test that monkeypatches `subprocess.run` and asserts `_static_smoke` passes `start_new_session=True`, a clean environment with thread caps, `--candidate-package`, `--control-package`, and a unique output path; assert the parent test process has no `cg` module loaded after the call.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run:
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_run_cg_p1_cem_v1.py -k static_smoke`

  Expected: FAIL because the current `_static_smoke` imports `arena._load_candidate` in the parent and does not invoke a subprocess.

- [ ] **Step 3: Implement the helper and parent bridge**

  The helper must import the candidate only inside its own process, call the existing candidate loader and control loader, execute the same `agent({"select": None})` contract check, and write a new JSON report with `os.replace`. The parent bridge must use `sys.executable`, `-I`, `-S` only if the helper can explicitly restore the repository `PYTHONPATH`; otherwise use the current interpreter with a minimal `PYTHONPATH=.:src`. It must pass `check=False`, a bounded timeout of 120 seconds, and preserve only the final 8 KiB of stderr in the report. No native module may be imported by the parent bridge.

- [ ] **Step 4: Run the focused regression and package tests**

  Run the focused test plus the existing CEM/package suite. Expected: all pass, and the parent process remains free of `cg` modules after `_static_smoke`.

### Task 2: minimal native boundary reproduction

**Files:**
- Modify: `tests/meta_specialist/test_run_cg_p1_cem_v1.py`
- Create: `docs/evidence/cg-cem-static-smoke-boundary-20260816.md`

**Interfaces:**
- The test builds the existing role-separated v4 candidate and calls the real subprocess smoke before an 8-game, 2-worker evaluator block. It records only paths and aggregate results.

- [ ] **Step 1: Add a subprocess-before-evaluator integration test**

  Use the promoted role-separated v4 pool and candidate-00 package; assert static smoke returns `status=PASS`, then call `run_parallel_cabt_evaluation` with the existing paired games and assert 8 requested, 8 completed, 0 faults.

- [ ] **Step 2: Run the test before applying any further change**

  The test must first reproduce the old behavior if the bridge still imports native `cg` in the parent. After Task 1 it should pass; record the exact evaluator summary SHA and worker count in the evidence file.

### Task 3: bounded CEM retry and gate decision

**Files:**
- Create: `docs/evidence/cg-self-owned-role-separated-v4-cem-retry-20260816.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Reuse the sealed role-separated v4 split and promoted pool without changing source hashes or holdout assignments.
- Run one generation with the normal ResourceGovernor decision, independent re-evaluation, positive-delta gate, and no DEV/FINAL read unless a candidate passes all independent gates.

- [ ] **Step 1: Execute one bounded retry**

  Use a new output root and campaign seed; never overwrite the earlier incomplete or low-worker artifacts. Abort fail-closed if any requested row faults or if native worker startup is still contaminated.

- [ ] **Step 2: Classify the result**

  A candidate is eligible only when independent delta is positive, seat-safe, opponent×seat-safe, and fault-free. Otherwise preserve P1 center and do not call `cg_bestknown_loop_v1.py`.

- [ ] **Step 3: Update evidence and status**

  Record source/policy/deck/split SHA, requested/completed/fault rows, independent result, holdout exposure, and the unchanged BestKnown label. Include the `ono-` provenance caveat only as identity provenance, never as public source authorship.

## Verification checklist

- [ ] `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_run_cg_p1_cem_v1.py tests/meta_specialist/test_opponent_pool_v1.py`
- [ ] `PYTHONPATH=.:src .venv/bin/python -m py_compile scripts/run_cg_static_smoke_v1.py scripts/run_cg_p1_cem_v1.py`
- [ ] `PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py`
- [ ] `git diff --check`
- [ ] `ps` confirms no active CEM/smoke process before handoff.
