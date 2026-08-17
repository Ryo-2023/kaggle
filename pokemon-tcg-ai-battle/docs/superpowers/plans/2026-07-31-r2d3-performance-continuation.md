# R2D3 性能変更時の継続実験 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v15 の完走済み R2D3 checkpoint を hash 検証付きで v16 に引き継ぎ、停止した validation から再開可能にする。

**Architecture:** 新設する `--continue-from-artifact` は親 artifact の immutable evidence を検証し、v16 側に継続 provenance と必要な replay/checkpoint を materialize する。通常の `--resume` は v16 の source identity を維持し、継続 lineage がある場合は imported checkpoint の hash も再検証する。

**Tech Stack:** Python 3、pytest、PyTorch checkpoint、JSON artifact manifest。

## Global Constraints

- Kaggle submission、commit、push は実行しない。
- parent artifact と既消費 holdout を変更しない。
- 互換性不明時は fail-closed し、新規学習へ暗黙に fallback しない。
- 本件は final-step checkpoint の validation 再開に限定し、未完了 checkpoint の training 継続は実装しない。

---

### Task 1: 継続 artifact の親検証契約

**Files:**
- Modify: `tests/test_submitted_opponents_r2d3.py`
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`

**Interfaces:**
- Produces: `Controller.continue_from_parent() -> dict[str, object]`
- Produces: `continuation_manifest.json` with parent path, hashes, imported stages, and checkpoint provenance.

- [x] **Step 1: Write the failing test**

```python
def test_continuation_rejects_parent_without_final_checkpoint(tmp_path: Path) -> None:
    module = _performance_module()
    controller = _continuation_controller(module, tmp_path)
    with pytest.raises(RuntimeError, match="continuation checkpoint"):
        controller.continue_from_parent()
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: FAIL because `continue_from_parent` does not exist.

- [x] **Step 3: Write minimal implementation**

```python
def continue_from_parent(self) -> dict[str, Any]:
    parent = self.continuation_parent()
    checkpoint = self._validated_parent_checkpoint(parent)
    return {"parent": str(parent), "checkpoint": str(checkpoint)}
```

Validate parent source/deck/population/semantic identities and required files before returning.

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: PASS.

### Task 2: Final checkpoint import and inherited-stage execution

**Files:**
- Modify: `tests/test_submitted_opponents_r2d3.py`
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`

**Interfaces:**
- Consumes: a verified parent from `continue_from_parent()`.
- Produces: `Controller.import_parent_continuation() -> None` and a v16-local checkpoint file.

- [x] **Step 1: Write the failing test**

```python
def test_continuation_imports_final_checkpoint_and_marks_prior_stages_inherited(tmp_path: Path) -> None:
    module = _performance_module()
    controller, parent = _prepared_continuation(module, tmp_path)
    controller.import_parent_continuation()
    assert (controller.artifact / "continuation_manifest.json").is_file()
    assert (controller.artifact / "checkpoints/psro-best-response-seed0/r2d3-step-000020.pt").is_file()
    assert controller.inherited_stage("full_training")
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: FAIL because import and inherited-stage APIs do not exist.

- [x] **Step 3: Write minimal implementation**

Copy only `replay.json`, `psro_online_replay.json`, the final training checkpoint, curve, and stage output evidence required by `psro_best_response`; write all parent hashes into a durable continuation manifest. Make `run_stage` skip inherited PASS stages without running them.

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: PASS.

### Task 3: Imported final checkpoint loading and resume validation

**Files:**
- Modify: `tests/test_submitted_opponents_r2d3.py`
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

**Interfaces:**
- Consumes: `continuation_manifest.json`.
- Produces: a `train()` result with `resumed=True` and `resumed_from_step == updates` for a validated imported final checkpoint.

- [x] **Step 1: Write the failing test**

```python
def test_continuation_allows_only_a_final_step_import(tmp_path: Path) -> None:
    module = _performance_module()
    controller = _continuation_controller(module, tmp_path)
    with pytest.raises(RuntimeError, match="final-step"):
        controller._validate_imported_checkpoint(step=19, updates=20)
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: FAIL because final-step validation does not exist.

- [x] **Step 3: Write minimal implementation**

Allow the imported checkpoint only when its stored step equals the current stage update count. Load it against the copied replay/population hashes, preserve its historical training identity in the continuation manifest, and have later `--resume` revalidate that manifest. Record the v15-to-v16 continuation procedure and the absence of any Kaggle submission.

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: PASS.

### Task 4: Regression verification

**Files:**
- Verify: `tests/test_submitted_opponents_r2d3.py`
- Verify: `docs/status/current_status.md`
- Verify: `docs/status/handoff.md`

- [x] **Step 1: Run focused continuation tests**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k continuation`

Expected: PASS.

- [x] **Step 2: Run full focused module tests**

Run: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py`

Expected: PASS with any existing environment-dependent skip reported separately.

- [x] **Step 3: Validate documentation and patch hygiene**

Run: `.venv/bin/python scripts/docs/validate_docs.py && git diff --check`

Expected: documentation validation succeeds and no whitespace errors are reported.

- [x] **Step 4: Do not commit**

The repository policy requires explicit user authorization for `git commit`; leave the verified diff uncommitted.
