# 継続 checkpoint benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 各継続学習 checkpoint を固定 512 局 benchmark で非同期評価し、再開可能な時系列成績を保存する。

**Architecture:** 既存の checkpoint event と controller を利用する。controller は event を間引かず task worker へ渡し、task worker は既存 CABT evaluator の result を履歴 writer に渡す。履歴 writer は JSONL を正として summary JSON を再生成する。

**Tech Stack:** Python 3.12、pytest、既存 continuous league / CABT。

## Global Constraints

- 新規 Git branch、Kaggle 提出、外部データ取得、学習実験の起動を行わない。
- 学習器は評価待ちをしない。checkpoint event と Runtime Policy の既存契約を変更しない。
- 512 局 benchmark と 1,024 局再確認 benchmark は別 manifest とし、採用や sealed holdout 消費を自動化しない。
- queue は checkpoint を黙って supersede しない。上限超過は fail-closed とする。

---

### Task 1: checkpoint 評価履歴の永続化

**Files:**
- Create: `src/mage_ptcg/continuous_league/evaluation_history.py`
- Test: `tests/test_continuous_league_evaluation_history.py`

**Interfaces:**
- Produce `record_checkpoint_evaluation(history_root, *, training_checkpoint_id, training_step, evaluation_result) -> dict[str, object]`.
- Produce `load_checkpoint_evaluation_summary(history_root) -> dict[str, object]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_recording_complete_result_creates_idempotent_history_and_summary(tmp_path):
    first = record_checkpoint_evaluation(tmp_path, training_checkpoint_id=CHECKPOINT, training_step=10_000, evaluation_result=COMPLETE_RESULT)
    second = record_checkpoint_evaluation(tmp_path, training_checkpoint_id=CHECKPOINT, training_step=10_000, evaluation_result=COMPLETE_RESULT)
    assert first["recorded"] is True
    assert second["recorded"] is False
    assert load_checkpoint_evaluation_summary(tmp_path)["latest_complete"]["training_step"] == 10_000
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_continuous_league_evaluation_history.py`

Expected: FAIL because `evaluation_history` does not exist.

- [ ] **Step 3: Write the minimal implementation**

```python
def record_checkpoint_evaluation(history_root: Path, *, training_checkpoint_id: str, training_step: int, evaluation_result: Mapping[str, Any]) -> dict[str, Any]:
    # validate immutable IDs, append one JSONL row, then atomically rebuild summary
```

- [ ] **Step 4: Add fail-closed cases and verify GREEN**

Add tests for benchmark/exposure mixing and a malformed JSONL history. Run the focused module and expect PASS.

### Task 2: controller queue preserves every checkpoint

**Files:**
- Modify: `src/mage_ptcg/continuous_league/scheduler.py`
- Modify: `src/mage_ptcg/continuous_league/controller.py`
- Modify: `src/mage_ptcg/continuous_league/cli.py`
- Test: `tests/test_continuous_league_contracts.py`

**Interfaces:**
- `DurableScheduler(..., max_pending_evaluations: int | None = None)` means no checkpoint task cap.
- `controller --max-pending-evaluations 0` means unlimited; a positive value rejects a new task instead of superseding one.

- [ ] **Step 1: Write the failing controller test**

```python
def test_controller_enqueues_every_checkpoint_without_superseding(tmp_path):
    # write five checkpoint events and run discovery
    assert pending_visible_task_count(controller) == 5
```

- [ ] **Step 2: Run it and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_continuous_league_contracts.py -k every_checkpoint`

Expected: FAIL because the existing scheduler supersedes intermediate evaluations.

- [ ] **Step 3: Implement no-loss queue semantics**

Preserve `None` as an unlimited pending count. Reject a new task at a configured positive cap without changing existing task state. Include `training_step` in the `VISIBLE_EVALUATION` payload.

- [ ] **Step 4: Run focused scheduler/controller tests and verify GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_continuous_league_contracts.py`

Expected: PASS.

### Task 3: task worker records the checkpoint curve

**Files:**
- Modify: `src/mage_ptcg/continuous_league/cli.py`
- Modify: `configs/continuous_league/task_worker.example.yaml`
- Test: `tests/test_continuous_league_cli.py`

**Interfaces:**
- Optional task-worker config `checkpoint_evaluation_history_root` enables durable history output.
- A history-enabled task requires `training_checkpoint_id` and integer `training_step` in its request payload.

- [ ] **Step 1: Write the failing task-worker integration test**

```python
def test_task_worker_writes_checkpoint_history_after_evaluation(tmp_path: Path):
    result = _cmd_task_worker(request, config)
    assert result["checkpoint_evaluation"]["training_step"] == 10_000
```

- [ ] **Step 2: Run it and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_continuous_league_cli.py -k checkpoint_history`

Expected: FAIL because task-worker result has no checkpoint history.

- [ ] **Step 3: Implement the wiring**

After `run_evaluation`, call `record_checkpoint_evaluation` only when the config is supplied. Return the history row in the task result. Keep existing task-worker configurations backwards compatible.

- [ ] **Step 4: Run focused CLI tests and verify GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_continuous_league_cli.py tests/test_continuous_league_evaluation_history.py`

Expected: PASS.

### Task 4: fixed 512/1,024 benchmark operations

**Files:**
- Create: `configs/continuous_league/benchmark_512.example.yaml`
- Create: `configs/continuous_league/benchmark_1024.example.yaml`
- Modify: `docs/runbooks/continuous-league.md`
- Modify: `docs/plan/design/06_continuous_league_benchmark_and_training_specification.md`
- Test: `tests/test_continuous_league_cli.py`

**Interfaces:**
- Example benchmark files use four explicit opponent IDs supplied by the generated catalog; the repetition counts yield 512 or 1,024 scheduled games after both seats are counted.
- The runbook gives controller and task-worker commands without `tee` or automatic Kaggle submission.

- [ ] **Step 1: Write a failing schedule-size test**

```python
def test_checkpoint_benchmark_specs_declare_their_expected_game_budget():
    assert benchmark_game_budget(spec_512) == 512
    assert benchmark_game_budget(spec_1024) == 1024
```

- [ ] **Step 2: Run it and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_continuous_league_cli.py -k checkpoint_benchmark_specs`

Expected: FAIL because the files do not exist.

- [ ] **Step 3: Add examples and operational documentation**

Use a four-opponent fixed Anchor set, 64 repetitions for 512 games and 128 for 1,024 games. Document that runtime policy IDs are discovered from the learner event stream and that 1,024-game evaluation is an explicit human-triggered recheck.

- [ ] **Step 4: Verify documentation and targeted regression suite**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_continuous_league_evaluation_history.py \
  tests/test_continuous_league_contracts.py \
  tests/test_continuous_league_cli.py \
  tests/test_continuous_league_learning.py \
  tests/test_continuous_league_cabt.py
.venv/bin/python scripts/docs/validate_docs.py
git diff --check
```

Expected: all tests and document validation pass.
