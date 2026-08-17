# R3 Recurrent θ0 Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** R3-A/R3-B の recurrent θ0候補を、sealed teacher data、sequence supervision、carry/reset comparison、atomic checkpointで検証・選択できる状態にする。

**Architecture:** 静的Gate 1からR3昇格を流用せず、`SpecialistModelV3` の既存GRUを唯一のrecurrent contractとして使う。sealed recordをstrictに再materializeし、episode単位のsequenceへ変換して、carry評価とreset-only ablationを同一checkpointで比較する。選択済みモデルだけをteacher-quality/sealed θ0工程へ渡す。

**Tech Stack:** Python 3.12、PyTorch、既存 `SpecialistModelV3`、pytest、JSON SHA-256 manifest。

## Global Constraints

- current-R2は静的baselineであり、recurrent θ0 candidateではない。
- R3-A/R3-Bは同一lane・component split・canonical semantic/STOP soft target・seed・max update budgetを使う。
- inputはsnapshot bytes、trusted permission、selected raw/content hash、episode/near-duplicate splitをruntime再検証する。
- forced sole STOP、synthetic/unattested record、split leakage、metric/manifest hash不一致はfail closedにする。
- R3 acceptance: laneごとの3-seed carry NLL/STOP NLLはreset-only+0.02以内、少なくとも1 laneで-0.01改善。R3-BはR3-Aよりcomplete NLL-0.01またはSTOP NLL-0.02でのみ選択する。
- commit、push、Kaggle提出、checkpoint promotionはユーザーの明示指示なしに行わない。

---

### Task 1: sealed recurrent sequence materializer

**Files:**

- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/representation_benchmark_v3.py`
- Test: `tests/meta_specialist/test_bc_trainer_v3.py`

**Interfaces:**

- Consumes: `gate1-input-<lane>.json` と `_read_gate_input_v3` / `_gate_steps_from_input_v3` のstrict authority。
- Produces: `RecurrentBCSequenceV3(lane, episode_id, component_id, partition, steps, burn_in)` と `materialize_recurrent_gate_sequences_v3(input_path, *, burn_in) -> tuple[RecurrentBCSequenceV3, ...]`。

- [ ] **Step 1: Write the failing sequence-boundary tests**

```python
def test_materialize_recurrent_sequences_carries_only_inside_episode():
    sequences = materialize_recurrent_gate_sequences_v3(sealed_input, burn_in=1)
    sequence = next(item for item in sequences if len(item.steps) >= 2)
    assert sequence.steps[0].episode_start is True
    assert all(not step.episode_start for step in sequence.steps[1:])
    assert {step.component_id for step in sequence.steps} == {sequence.component_id}

def test_materializer_rejects_rehashed_selected_line_or_partition_tamper():
    tampered = rewrite_self_hashed_input_with_changed_assignment(sealed_input)
    with pytest.raises(ValueError, match="split|raw line|coverage"):
        materialize_recurrent_gate_sequences_v3(tampered, burn_in=0)
```

- [ ] **Step 2: Run the tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_bc_trainer_v3.py -k 'recurrent_sequences or materializer'`

Expected: FAIL because `RecurrentBCSequenceV3` and the materializer do not exist.

- [ ] **Step 3: Implement strict sequence materialization**

```python
@dataclass(frozen=True)
class RecurrentBCSequenceV3:
    lane: str
    episode_id: str
    component_id: str
    partition: str
    steps: tuple[BCExampleV3, ...]
    burn_in: int

def materialize_recurrent_gate_sequences_v3(input_path: str | Path, *, burn_in: int) -> tuple[RecurrentBCSequenceV3, ...]:
    payload = _read_gate_input_v3(input_path)
    steps = _gate_steps_from_input_v3(payload)
    # Group only by the pinned record/episode order; reject a component or
    # partition change instead of silently sorting across boundaries.
```

Use the existing canonical loss rows and reconstructed `SpecialistStepInputV1` domain. Preserve every positive semantic and STOP mass; never derive a hard first-action label.

- [ ] **Step 4: Add padding and burn-in loss-mask tests**

```python
def test_padding_and_burn_in_neither_add_loss_nor_change_post_burn_hidden():
    batch = make_recurrent_batch_v3((sequence_a, sequence_b), burn_in=1)
    assert batch.loss_mask[:, 0].sum().item() == 0
    assert batch.padding_mask[1, -1].item() is False
```

- [ ] **Step 5: Run Task 1 tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_bc_trainer_v3.py`

Expected: PASS.

---

### Task 2: full-corpus recurrent split and selection manifest

**Files:**

- Create: `src/mage_ptcg/meta_specialist/recurrent_dataset_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Test: `tests/meta_specialist/test_recurrent_dataset_v3.py`

**Interfaces:**

- Consumes: sealed teacher root, snapshot index, trusted permission, and a fixed `qualification_time_utc`.
- Produces: `build_recurrent_selection_manifest_v3(...) -> Path` and `materialize_recurrent_selection_v3(...) -> tuple[RecurrentBCSequenceV3, ...]`. The root manifest pins a streamable selection index file SHA rather than a memory-resident JSON array; the index pins every full-corpus qualified record ID, component split, line/content hashes, and partition.

- [ ] **Step 1: Write the failing full-corpus split tests**

```python
def test_full_corpus_manifest_is_component_disjoint_and_reproducible(tmp_path):
    manifest = build_recurrent_selection_manifest_v3(root, lane="alakazam", output_path=tmp_path / "selection.json")
    assert manifest["split"]["overlap_counters"] == {"episode_overlap": 0, "near_duplicate_overlap": 0}
    assert manifest["records_total"] > 32
    assert (tmp_path / manifest["selection_index_path"]).is_file()
    assert read_recurrent_selection_manifest_v3(tmp_path / "selection.json") == manifest

def test_recurrent_selection_rejects_unqualified_or_untrusted_record(tmp_path):
    with pytest.raises(ValueError, match="qualified|permission"):
        build_recurrent_selection_manifest_v3(tampered_root, lane="alakazam", output_path=tmp_path / "selection.json")
```

- [ ] **Step 2: Run the tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_recurrent_dataset_v3.py`

Expected: FAIL because no full-corpus recurrent manifest builder exists.

- [ ] **Step 3: Implement streaming qualification and closed split**

```python
def build_recurrent_selection_manifest_v3(root: str | Path, *, lane: str, qualification_time_utc: str,
                                          output_path: str | Path) -> dict[str, object]:
    # Validate every closed shard byte first. Stream qualified metadata to a
    # disk-backed spool, union integer row IDs by episode/non-ubiquitous near
    # key, and stream a JSONL selection index. The small root manifest pins
    # that index's file SHA and every authority hash.
```

Reject whole-manifest construction when a physical shard differs from the snapshot or the computed split has any episode/near-duplicate overlap. Do not use an arbitrary `limit`, record-count/byte cap, or first-N slice.

- [ ] **Step 4: Add runtime re-materialization test**

```python
def test_reader_rejects_rehashed_manifest_when_a_pinned_raw_line_changed(tmp_path):
    manifest = build_recurrent_selection_manifest_v3(...)
    mutate_one_dataset_line_after_manifest(root)
    with pytest.raises(ValueError, match="raw line|snapshot"):
        materialize_recurrent_selection_v3(manifest_path)
```

- [ ] **Step 5: Run Task 2 tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_recurrent_dataset_v3.py tests/meta_specialist/test_bc_trainer_v3.py`

Expected: PASS.

---

### Task 3: recurrent R3 training and carry/reset evaluator

**Files:**

- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/neural_model_v3.py`
- Create: `src/mage_ptcg/meta_specialist/recurrent_gate_v3.py`
- Test: `tests/meta_specialist/test_bc_trainer_v3.py`
- Test: `tests/meta_specialist/test_recurrent_gate_v3.py`

**Interfaces:**

- Consumes: an anchored full-corpus recurrent selection manifest through a revalidating, streamable sequence iterator and candidate name `R3-A|R3-B`.  A corpus-wide `tuple[RecurrentBCSequenceV3, ...]` is permitted only for bounded fixtures and must not be used by an actual lane run.
- Produces: `train_recurrent_r3_v3(...) -> RecurrentTrainingResultV3` and `evaluate_carry_vs_reset_v3(model, sequences, ...) -> RecurrentGateMetricsV3`.

- [ ] **Step 1: Write failing hidden-carry and reset-ablation tests**

```python
def test_carry_evaluation_passes_hidden_to_second_step_but_reset_ablation_does_not():
    model = SpyRecurrentR3()
    evaluate_carry_vs_reset_v3(model, sequences, device=torch.device("cpu"))
    assert model.hidden_inputs_for_carry[1] is not None
    assert model.hidden_inputs_for_reset[1] is None

def test_complete_soft_target_includes_stop_mass_and_excludes_forced_sole_stop():
    metrics = evaluate_carry_vs_reset_v3(model, fixture_sequences, device=torch.device("cpu"))
    assert metrics.carry_stop_nll is not None
    assert metrics.forced_sole_stop_rows == 0
```

- [ ] **Step 2: Run the tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_recurrent_gate_v3.py`

Expected: FAIL because recurrent evaluator symbols do not exist.

- [ ] **Step 3: Implement sequence train/evaluate loop**

```python
def evaluate_carry_vs_reset_v3(model, sequences, *, device: torch.device) -> RecurrentGateMetricsV3:
    carry = _evaluate_sequences_v3(model, sequences, carry_hidden=True, device=device)
    reset = _evaluate_sequences_v3(model, sequences, carry_hidden=False, device=device)
    return RecurrentGateMetricsV3(carry_complete_nll=carry.complete_nll, reset_complete_nll=reset.complete_nll,
                                  carry_stop_nll=carry.stop_nll, reset_stop_nll=reset.stop_nll,
                                  non_reset_hidden_steps=carry.non_reset_hidden_steps)
```

Use `SpecialistModelV3.forward_v3` with `episode_start` from the sequence, detach hidden state only at documented truncated-BPTT boundaries, and apply the loss mask after burn-in. Save best validation state and actual parameter delta.

The full-corpus runner must stream one sequence/bounded batch at a time from the pinned index, preserving physical episode order.  It must not retain all decoded model states, `BCExampleV3` instances, or `RecurrentBCSequenceV3` instances across an epoch.  Reopen/revalidate the authority and index before each train/evaluation pass; a noncontiguous reappearance of an episode is an input error, never two silently independent sequences.

- [ ] **Step 4: Add early-stop and real-update tests**

```python
def test_training_records_best_epoch_history_and_real_parameter_delta():
    result = train_recurrent_r3_v3(..., max_epochs=4, patience=2, min_delta=10.0)
    assert result.parameter_delta_l1 > 0
    assert result.stop_reason == "patience"
    assert len(result.history) == result.epochs
```

- [ ] **Step 5: Run Task 2 tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_bc_trainer_v3.py tests/meta_specialist/test_recurrent_gate_v3.py`

Expected: PASS.

---

### Task 3.5: sealed full-corpus stream adapter

**Files:**

- Modify: `src/mage_ptcg/meta_specialist/recurrent_dataset_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/recurrent_gate_v3.py`
- Test: `tests/meta_specialist/test_recurrent_dataset_v3.py`
- Test: `tests/meta_specialist/test_recurrent_gate_v3.py`

**Contract:**

- `stream_recurrent_selection_v3(manifest_path, *, expected_manifest_file_sha256, burn_in, partition)` must verify the external raw manifest SHA before parsing, then rerun the Task 2 authority/index/split reproduction before yielding any episode sequence.  It yields one physical episode at a time and never builds a corpus-wide sequence list.
- `sealed_recurrent_sequence_source_v3(...)` is the only production source accepted by Task 3.  It holds the pinned manifest path/SHA and calls the Task 2 stream for each pass; an arbitrary `production=True` factory is not a valid authority.
- Tests must reject a rehashed manifest whose raw file SHA differs from the supplied anchor, a changed raw line/index, an eager tuple factory, or a train/validation overlap before the first optimizer update.

### Task 3.6: run-local frozen-index receipt

**Files:**

- Modify: `src/mage_ptcg/meta_specialist/recurrent_dataset_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/recurrent_gate_v3.py`
- Test: `tests/meta_specialist/test_recurrent_dataset_v3.py`
- Test: `tests/meta_specialist/test_recurrent_gate_v3.py`

**Contract:**

- At lane/job start, reproduce the entire Task 2 selection index once, compare it byte-for-byte to the sealed sidecar, then atomically write a run-local frozen index and an externally SHA-pinned preflight receipt.  The receipt binds lane, source manifest raw SHA, snapshot/teacher/index SHA, frozen-index SHA, command identity, and the closed chunk set.
- Every train/eval pass reopens the receipt/index by pinned descriptor, validates manifest/snapshot/teacher/frozen-index/chunk physical SHA, and lockstep requalifies the raw corpus against the frozen index before yielding.  It does not recompute connected components or assignments per pass.
- The input file descriptor is opened once and `fstat`-checked before/after consumption; EOF digest/count mismatch, raw-line/permission/episode/partition mismatch, receipt/index replacement, or a missing external receipt anchor must fail closed.
- No prepared feature cache, decoded-state cache, corpus-wide tuple, or mutable run-local sidecar is allowed.

### Task 4: sealed recurrent R3 Gate and deterministic selection

**Files:**

- Create: `src/mage_ptcg/meta_specialist/recurrent_gate_v3.py`
- Create: `scripts/run_meta_specialist_v3_recurrent_gate.py`
- Test: `tests/meta_specialist/test_recurrent_gate_v3.py`

**Interfaces:**

- Consumes: two anchored full-corpus recurrent selection manifests, `seeds=(7,17,29)`, fixed training budget, CUDA/CPU device.
- Produces: `recurrent-gate-result-v3-<device>.json`, `recurrent-gate-selection-v3-<device>.json`, and `verify_recurrent_gate_anchor_v3(...)`.

- [ ] **Step 1: Write failing selection-rule tests**

```python
def test_candidate_is_rejected_when_one_lane_carry_exceeds_reset_by_more_than_point_02():
    result = make_metrics(alakazam=(0.40, 0.41), archaludon=(0.50, 0.53))
    assert select_recurrent_r3_v3(result).status == "BLOCKED"

def test_r3b_requires_preregistered_nll_or_stop_margin_over_r3a():
    assert select_recurrent_r3_v3(metrics_without_margin).preferred == "R3-A"
    assert select_recurrent_r3_v3(metrics_with_stop_margin).preferred == "R3-B"
```

- [ ] **Step 2: Run the tests to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_recurrent_gate_v3.py -k 'select or anchor'`

Expected: FAIL because no recurrent Gate selection exists.

- [ ] **Step 3: Implement the closed 12-cell Gate**

```python
def run_recurrent_gate_v3(*, lane_inputs, seeds=(7, 17, 29), max_epochs, patience, min_delta, burn_in, device, output_dir):
    # 2 lanes × R3-A/R3-B × 3 seeds; each row contains carry and reset-only
    # metrics, shared input/split hashes, actual updates, CUDA evidence, and
    # a best-checkpoint hash.
```

Validate exact matrix completeness, lane-shared data/budget, finite metrics, per-lane STOP evidence, non-reset hidden steps, and all numeric selection margins. Write results atomically and create an adjacent selection manifest containing result path/file SHA/result SHA; the public anchor verifier must require caller-provided expected SHA values.

- [ ] **Step 4: Test rehashed result and selection-manifest tampering**

```python
def test_external_anchor_rejects_rehashed_recurrent_latency_or_preferred_tamper(tmp_path):
    selection = read_recurrent_gate_selection_v3(selection_path)
    mutate_and_rehash_result(result_path, preferred="R3-B")
    with pytest.raises(ValueError, match="file SHA"):
        verify_recurrent_gate_anchor_v3(...)
```

- [ ] **Step 5: Run bounded CPU and CUDA Gate**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/run_meta_specialist_v3_recurrent_gate.py --lane-input alakazam=<manifest> --lane-input archaludon=<manifest> --max-epochs 4 --patience 2 --burn-in 1 --device cpu --output runs/meta-specialist-two-lane-readiness/recurrent-gate`

Then rerun the same command with `--device cuda:0`. Expected: exact 12-cell artifact and either a selected R3 candidate or an explicit blocked decision; never a fallback to current-R2.

---

### Task 5: θ0 checkpoint seal after recurrent Gate selection

**Files:**

- Create: `src/mage_ptcg/meta_specialist/theta0_manifest_v3.py`
- Modify: `scripts/run_meta_specialist_v3_bc.py`
- Test: `tests/meta_specialist/test_theta0_manifest_v3.py`

**Interfaces:**

- Consumes: anchored `recurrent-gate-selection-v3`, selected R3 checkpoint, teacher quality manifest, source/data/split hashes.
- Produces: `theta0-<lane>-<seed>.pt` and `theta0-<lane>-<seed>.json` with `seal_theta0_v3(...)` / `read_theta0_manifest_v3(...)`.

- [ ] **Step 1: Write failing atomic-reload test**

```python
def test_theta0_seal_reloads_in_new_process_and_binds_all_authorities(tmp_path):
    manifest = seal_theta0_v3(...)
    assert reload_checkpoint_in_fresh_process(manifest.checkpoint_path) == manifest.tensor_sha256
    assert manifest.recurrent_gate_result_file_sha256 == expected_gate_file_sha
```

- [ ] **Step 2: Run the test to verify RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_theta0_manifest_v3.py`

Expected: FAIL because θ0 seal symbols do not exist.

- [ ] **Step 3: Implement atomic checkpoint and manifest**

```python
def seal_theta0_v3(*, checkpoint_state, recurrent_selection_path, expected_selection_file_sha256,
                   teacher_manifest_path, source_files, output_dir) -> Theta0ManifestV3:
    # fsync temporary checkpoint, replace atomically, reload in a fresh Python
    # process, and bind every referenced file hash in the canonical manifest.
```

Refuse `BASELINE_RETAINED` static Gate selection, unanchored recurrent selection, unqualified teacher manifest, and any source path outside the explicit allowlist.

- [ ] **Step 4: Run Task 4 tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_theta0_manifest_v3.py`

Expected: PASS.

## Plan Self-Review

- Spec coverage: Tasks 1–4 cover strict recurrent input, full-corpus split, hidden carry/reset, full target/STOP metrics, fixed R3 selection margins, CUDA evidence, and external anchors. Task 5 covers θ0 atomic checkpoint/reload. Teacher-quality rederivation remains a separate prerequisite because existing data lacks current-pool provenance; no θ0 may be sealed until that prerequisite has its own approved plan.
- Placeholder scan: no `TODO`, `TBD`, or undefined deferred implementation steps are present.
- Type consistency: Task 1 produces `RecurrentBCSequenceV3`; Task 2 produces a full-corpus selection manifest; Task 3 consumes both and produces `RecurrentGateMetricsV3`; Task 4 consumes its metrics and emits the only allowed recurrent selection; Task 5 consumes that anchored selection.

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-08-09-r3-recurrent-gate.md`. Execute Tasks 1–4 with subagent-driven development and an independent review before beginning the separate teacher-quality prerequisite for Task 5. No commit is part of this plan without user authorization.
