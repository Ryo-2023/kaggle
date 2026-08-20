# Biohub Strong Baseline v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce a provenance-pinned TemporalUNet3D + SimpleNodeTransformer + ILP tracker on one real Kaggle train volume and obtain official metric values from its prediction GEFF.

**Architecture:** Keep RoyerLab model and inference code at upstream commit `075fc5f5a52d11077f9dc2b074644618f26939e2` in an ignored artifact checkout. Add a small project package for provenance validation, image-only inference orchestration, and a separate official-evaluation phase; attempt the published harmonic reverse association only after the official ILP run completes.

**Tech Stack:** Python 3.11, PyTorch CPU, OME-Zarr v3, tracksdata/GEFF, ilpy + PySCIPOpt, vendored RoyerLab metrics, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-20-strong-baseline-v1-design.md`

## Global Constraints

- Run Python, tests, inference, and evaluation only inside the existing `biohub-dev` Ubuntu container.
- Do not rebuild or recreate the container.
- Use the real image `44b6_0113de3b.zarr`; GT is evaluation-only.
- Pin upstream source to `075fc5f5a52d11077f9dc2b074644618f26939e2` and checkpoint artifact to Kaggle dataset version 1.
- Do not modify `src/biohub/official_metrics/`, existing visualizer files, the source checkout, or one-pass artifacts.
- Keep downloads, checkpoints, predictions, and logs under ignored `artifacts/strong_baseline_v1/`.
- Fail rather than silently disable ILP, accept a hash mismatch, or substitute dummy output.
- Do not commit, push, submit to Kaggle, train a model, or run a parameter sweep; no Git mutation beyond the already-created branch/worktree is authorized.

---

### Task 1: Provenance and evaluation boundary

**Files:**
- Create: `src/biohub/strong_baseline/__init__.py`
- Create: `src/biohub/strong_baseline/provenance.py`
- Create: `src/biohub/strong_baseline/evaluation.py`
- Create: `tests/test_strong_baseline_provenance.py`
- Create: `tests/test_strong_baseline_evaluation.py`

**Interfaces:**
- Produces: `verify_source(root: Path, expected_commit: str) -> None`
- Produces: `verify_sha256(path: Path, expected: str) -> str`
- Produces: `evaluate_prediction(prediction_path: Path, gt_path: Path, scale: tuple[float, float, float], max_distance: float = 7.0) -> dict[str, int | float]`
- Consumes: existing `biohub.official_metrics.metrics` and GEFF/Zarr readers only.

- [ ] **Step 1: Write failing provenance tests**

```python
def test_verify_sha256_rejects_mismatch(tmp_path: Path) -> None:
    checkpoint = tmp_path / "model.pth"
    checkpoint.write_bytes(b"weights")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_sha256(checkpoint, "0" * 64)


def test_verify_source_requires_exact_commit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="upstream commit"):
        verify_source(tmp_path, OFFICIAL_COMMIT)
```

- [ ] **Step 2: Run the tests and verify they fail because the package does not exist**

Run:

```bash
docker exec -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub-dev \
  uv run pytest -q tests/test_strong_baseline_provenance.py
```

Expected: import failure for `biohub.strong_baseline`.

- [ ] **Step 3: Implement exact source and checkpoint validation**

`verify_source` runs `git -C str(root) rev-parse HEAD`, rejects a non-Git path,
and compares the full 40-character commit. `verify_sha256` streams the file in
1 MiB chunks and returns the verified digest. Constants are:

```python
OFFICIAL_COMMIT = "075fc5f5a52d11077f9dc2b074644618f26939e2"
LOCAL_CHECKPOINT_SHA256 = "347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235"
```

- [ ] **Step 4: Write a failing evaluator unit test with small synthetic graphs**

Build two in-memory two-node/one-edge graphs, write them as temporary GEFFs,
set `geff.extra.estimated_number_of_nodes=2` on the GT, and assert:

```python
result = evaluate_prediction(pred_path, gt_path, scale=(1.625, 0.40625, 0.40625))
assert result["prediction_node_count"] == 2
assert result["prediction_edge_count"] == 1
assert result["edge_tp"] == 1
assert result["edge_fp"] == 0
assert result["edge_fn"] == 0
assert result["edge_jaccard"] == 1.0
assert result["adjusted_edge_jaccard"] == 1.0
assert result["final_score"] == 1.0
```

- [ ] **Step 5: Implement the evaluation phase**

Load prediction and GT with `td.graph.IndexedRXGraph.from_geff`, unwrap tuple
returns, call `evaluate`, then `node_recall`, `per_sample_metrics`, and
`summarise([row])`. Read the estimated total from
`zarr.open_group(gt_path).attrs["geff"]["extra"]["estimated_number_of_nodes"]`.
Reload the prediction inside this function so matching mutations never affect
the serialized artifact. Serialize non-finite division values as JSON `null`,
not non-standard `NaN`.

- [ ] **Step 6: Run focused tests and lint**

Run:

```bash
docker exec -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub-dev \
  uv run pytest -q tests/test_strong_baseline_provenance.py tests/test_strong_baseline_evaluation.py
docker exec -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub-dev \
  uv run ruff check src/biohub/strong_baseline tests/test_strong_baseline_*.py
```

Expected: all focused tests pass and targeted Ruff has zero findings.

### Task 2: Thin upstream runner with strict image/GT separation

**Files:**
- Create: `src/biohub/strong_baseline/runner.py`
- Create: `scripts/run_strong_baseline_v1.py`
- Create: `tests/test_strong_baseline_runner.py`

**Interfaces:**
- Consumes: Task 1 provenance checks.
- Produces: `InferenceRequest` dataclass containing upstream root, image stem,
  checkpoint, output directory, threshold, device expectation, and ILP costs.
- Produces: `run_official_inference(request: InferenceRequest) -> RunReceipt`.
- Produces: `run_official_smoke(request: InferenceRequest, max_frames: int = 2) -> RunReceipt`, which calls upstream `predict_video` directly and saves its graph through upstream helpers.
- Produces CLI subcommands `verify`, `smoke-official`, `infer-official`, and `evaluate`.

- [ ] **Step 1: Write failing request-validation tests**

```python
def test_inference_request_rejects_geff_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="image stem"):
        InferenceRequest(
            upstream_root=tmp_path / "upstream",
            image_stem=tmp_path / "sample.geff",
            checkpoint=tmp_path / "model.pth",
            output_dir=tmp_path / "output",
            expected_device="cpu",
        )


def test_inference_command_never_contains_gt(request: InferenceRequest) -> None:
    command = build_official_command(request)
    assert "--evaluate" not in command
    assert not any(str(part).endswith(".geff") for part in command)
    assert command[command.index("--debug-video") + 1].endswith("44b6_0113de3b")
```

- [ ] **Step 2: Run the test and verify failure from missing interfaces**

Run `uv run pytest -q tests/test_strong_baseline_runner.py` in the container.

- [ ] **Step 3: Implement a subprocess wrapper around the fixed official CLI**

The exact official invocation is:

```python
command = [
    sys.executable,
    str(request.upstream_root / "scripts/predict_unet_transformer.py"),
    "--data-dir", str(request.image_stem.parent),
    "--debug-video", str(request.image_stem),
    "--weights", str(request.checkpoint),
    "--method", "strong_baseline_v1_official_ilp",
    "--split", "0",
    "--det-threshold", "0.99",
    "--unet-batch-size", "1",
    "--use-ilp",
    "--ilp-edge-weight", "-1.0",
    "--ilp-appearance-weight", "0.1",
    "--ilp-disappearance-weight", "0.1",
    "--ilp-division-weight", "1.0",
]
```

Set `USER=strong_baseline_v1` and prepend
`str(request.upstream_root / "src")` to `PYTHONPATH`.
Do not pass `--evaluate`. Since upstream output is fixed beneath its own
`predictions/`, require the destination not to exist, run upstream in the
writable ignored checkout, then copy the resulting
`44b6_0113de3b.geff` directory to the requested output. Record stdout/stderr,
timestamps, elapsed seconds, `torch.cuda.is_available()`, full config, hashes,
and return code in `run.json`. Reject a missing or empty prediction graph.

- [ ] **Step 4: Implement CLI phase separation**

`infer-official` accepts no GT option. `evaluate` requires both `--prediction`
and `--ground-truth`, calls Task 1, and writes `metrics.json`. `verify` checks
source/checkpoint/image metadata and imports `tracksdata`, `ilpy`, and
`pyscipopt` without running inference. `smoke-official` loads the same model,
calls the fixed upstream `predict_video(model, dataset_path, device, config,
window_size=window_size, max_frames=2, unet_batch_size=1,
downsample=downsample)`, passes the result through upstream `build_graph` and
the same ILP settings, and writes to a separate `official_ilp_smoke` directory.

- [ ] **Step 5: Run focused tests and targeted lint**

Expected: tests pass, command snapshots contain no GT path, and targeted Ruff
has zero findings.

### Task 3: Acquire fixed public assets and complete official ILP run

**Files:**
- Generate only: `artifacts/strong_baseline_v1/upstream/`
- Generate only: `artifacts/strong_baseline_v1/inputs/source_receipt.json`
- Generate only: `artifacts/strong_baseline_v1/official_ilp/*`

**Interfaces:**
- Consumes: Task 2 CLI, original checkout's data and checkpoint as read-only paths.
- Produces: real `prediction.geff`, `run.json`, `metrics.json`, and `inference.log`.

- [ ] **Step 1: Clone and detach the official source at the fixed commit**

```bash
git clone https://github.com/royerlab/kaggle-cell-tracking-competition.git \
  artifacts/strong_baseline_v1/upstream
git -C artifacts/strong_baseline_v1/upstream checkout --detach \
  075fc5f5a52d11077f9dc2b074644618f26939e2
```

The tracked worktree remains on `feat/strong-baseline-v1`; only the ignored
upstream checkout is detached.

- [ ] **Step 2: Download Kaggle artifact version 1 and compare provenance**

Use Kaggle CLI inside `biohub-dev` to download
`thibautgoldsborough/cellmot-baseline-artifacts`, version 1, into
`artifacts/strong_baseline_v1/inputs/`. Never print or copy credential files.
Compare the artifact checkpoint/config to the existing local candidate. Use
the downloaded version if hashes differ; record both hashes and the selected
path. Record the artifact license as `Unknown` rather than inventing one.

- [ ] **Step 3: Run `verify` and a two-frame smoke inference**

The smoke uses the real image with upstream's `max_frames=2` path or a wrapper
option that passes this value without copying GT. It must produce a reloadable
GEFF and prove ILP imports/solve operate. Label smoke metrics as non-comparable
and do not report them as the experiment result.

- [ ] **Step 4: Run all 100 frames on CPU**

Invoke `infer-official` with the fixed parameters from Task 2. Do not alter the
detection threshold after seeing GT. Preserve the log even if the process
fails; diagnose and make only the smallest environment/compatibility fix.

- [ ] **Step 5: Evaluate only after the prediction hash is persisted**

Hash `prediction.geff`, then call the separate `evaluate` subcommand using
`44b6_0113de3b.geff`. Confirm `metrics.json` contains every metric named in the
design and that prediction node/edge counts equal a fresh GEFF reload.

### Task 4: Controlled published harmonic association attempt

**Files:**
- Create only if exact source is obtainable: `src/biohub/strong_baseline/harmonic.py`
- Create only if exact source is obtainable: `tests/test_strong_baseline_harmonic.py`
- Modify only if exact source is obtainable: `src/biohub/strong_baseline/runner.py`
- Modify only if exact source is obtainable: `scripts/run_strong_baseline_v1.py`
- Generate only: `artifacts/strong_baseline_v1/harmonic_ilp/*`

**Interfaces:**
- Consumes: the same model, detections, input, threshold, and ILP settings as Task 3.
- Produces: `harmonic_predict_edges(original_predict_edges, reverse_weight=0.20)` wrapper returning `(B, N_source, N_target)` logits.

- [ ] **Step 1: Obtain the exact Yusuke v18 notebook source and receipt**

Pin Kaggle script version `338569479`. Extract only the published logit
alignment and `harmonic_probability` formula. If the fixed version cannot be
obtained or its license/compatible checkpoint cannot be established, record
the concrete blocker and skip Steps 2–5; Task 3 remains the authorized Done
fallback.

- [ ] **Step 2: Write failing tensor-orientation and formula tests**

```python
forward = torch.tensor([[[2.0, 0.0], [0.0, 2.0]]])
reverse_native = torch.tensor([[[2.0, 0.0], [0.0, 2.0]]])
fused = fuse_harmonic_logits(forward, reverse_native, reverse_weight=0.20)
assert fused.shape == (1, 2, 2)
assert torch.isfinite(fused).all()
```

Also test a non-square `(1, 2, 3)` forward and `(1, 3, 2)` reverse so failure
to transpose the reverse tensor is caught.

- [ ] **Step 3: Implement the exact published wrapper**

Call the original model once as source-to-target and once with all feature,
coordinate, position, and mask arguments swapped. Transpose reverse axes 1 and
2, apply the published mean/std alignment and probability-space weighted
harmonic formula with weight `0.20`, and return log-probabilities compatible
with upstream's existing source-axis (`dim=0` after batch removal) softmax.

- [ ] **Step 4: Run focused tests and targeted lint**

Expected: formula fixtures and non-square orientation pass; no Ruff findings.

- [ ] **Step 5: Run the complete harmonic + same-ILP experiment**

Use the same sample, checkpoint, detections, threshold, and ILP costs as Task 3.
Persist and evaluate a separate GEFF. Report the exact delta for every metric;
do not call the result an improvement unless the measured final score is higher.

### Task 5: Result report, visual sanity check, and final verification

**Files:**
- Create: `docs/results/strong_baseline_v1.md`
- Create: `tests/test_strong_baseline_result_report.py`
- Modify only if needed for package exposure: `src/biohub/strong_baseline/__init__.py`

**Interfaces:**
- Consumes: Task 3 receipts and, when completed, Task 4 receipts.
- Produces: the user-requested permanent result record.

- [ ] **Step 1: Generate the report from receipts, not memory**

Include Method, Source/version/checkpoint, Input sample and GT, Execution
command, Environment, Detection result, Tracking result, Official metrics,
Baseline comparison, Visual sanity check, Problems/limitations, and Next
experiments. Copy numeric values from JSON and include artifact-relative paths.

- [ ] **Step 2: Run the existing viewer against the final GEFF**

Load raw image, prediction, and GT with scale `(1.625, 0.40625, 0.40625)` and
7 micrometres. Inspect at least one matched trajectory window and one error or
sparse-unmatched area. Record observations without treating unannotated cells
as negatives.

- [ ] **Step 3: Verify report/receipt consistency**

Add a small test or deterministic script that parses `metrics.json` and checks
the report's metric table values, checkpoint SHA, sample stem, source commit,
and prediction node/edge counts.

- [ ] **Step 4: Run final verification in the container**

```bash
uv run pytest -q
uv run ruff check src/biohub/strong_baseline scripts/run_strong_baseline_v1.py \
  tests/test_strong_baseline_*.py
```

Also reload the persisted final GEFF and rerun official evaluation once. Note
the 24 baseline Ruff violations separately; never claim repository-wide Ruff
passes unless a fresh full command actually does.

- [ ] **Step 5: Review final diff and artifact inventory**

Confirm only Strong Baseline files changed in the isolated worktree, no large
artifact is tracked, original checkout status is untouched, no GT path appears
in the inference receipt command, and no commit/push/submission was performed.
