# Biohub Strong Baseline v1 Design

## Goal

Run a provenance-pinned public Biohub tracking method on the real Kaggle train
sample `44b6_0113de3b.zarr`, write a prediction GEFF without using GT during
inference, and score that prediction with the vendored official evaluator.

Done requires a persisted prediction GEFF, all requested metric counts and
scores, a reproducible command, runtime/device measurements, and checkpoint and
source provenance in `docs/results/strong_baseline_v1.md`.

## Evidence and fixed inputs

- Image: `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.zarr`
  with shape `(100, 64, 256, 256)`, axes `(T, Z, Y, X)`, and spatial scale
  `(1.625, 0.40625, 0.40625)` micrometres.
- Evaluation-only GT:
  `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff`,
  containing 52 annotated nodes and 50 edges; its metadata estimates 25,755
  total nodes, so annotations are sparse.
- Official source: `royerlab/kaggle-cell-tracking-competition` at commit
  `075fc5f5a52d11077f9dc2b074644618f26939e2` (BSD-3-Clause).
- Official checkpoint distribution: Kaggle dataset
  `thibautgoldsborough/cellmot-baseline-artifacts`, version 1, as consumed by
  the organizer-authored baseline notebook version `331429261`.
- Local candidate checkpoint SHA-256:
  `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235`.
  It contains both `unet.*` and `transformer.*` state.
- Official metric: the existing vendored copy pinned to the same upstream
  commit. The files under `src/biohub/official_metrics/` remain unmodified.

## Selected approach

The primary reproducibility path is the fixed official implementation:

```text
OME-Zarr
  -> quantile normalization
  -> TemporalUNet3D detector
  -> physical-scale local maxima
  -> U-Net node features
  -> SimpleNodeTransformer association
  -> tracksdata ILP graph optimization
  -> prediction GEFF
  -> vendored official metric
```

The first successful run uses the upstream defaults recorded by the fixed
source, including `pool_kernel_um=3.0`; a local `config.json` value of `5.0` is
not trusted until the downloaded artifact is hash-compared. Inference reads
only the image and checkpoint. GT is opened only after the prediction GEFF has
been written and its hash recorded.

If the official path succeeds, the only planned strong-baseline delta is the
published Yusuke v18 bidirectional harmonic association: compute the reverse
association for each adjacent frame pair, align reverse logits to the forward
distribution as published, combine probabilities with reverse weight `0.20`,
then pass the fused candidate scores to the same ILP. Detection, sample,
checkpoint, thresholds, evaluator, and node set stay fixed. This controlled
delta is attempted only when the exact public implementation can be obtained;
it will not be recreated from the method name alone.

If the harmonic implementation or its compatible checkpoint cannot be
reproduced locally, the official TemporalUNet3D + SimpleNodeTransformer + ILP
run is the authorized fallback and still must complete the real-data metric
loop. Raunak v2, DeepCenter gating, local rankers, multi-seed ensembles, and
8-way TTA are excluded from v1 because they introduce multiple simultaneous
changes and several runtime-patched artifacts.

## Isolation and artifacts

Tracked work occurs on branch `feat/strong-baseline-v1` in the isolated
worktree under `scratch/strong-baseline-v1`. The dirty source checkout and its
one-pass files are read-only inputs.

Generated and downloaded files use the worktree-local ignored directory:

```text
artifacts/strong_baseline_v1/
  upstream/                  # fixed official source checkout
  inputs/                    # downloaded artifact receipts, not credentials
  official_ilp/
    prediction.geff
    metrics.json
    run.json
    inference.log
  harmonic_ilp/              # present only if exact public path is reproducible
    prediction.geff
    metrics.json
    run.json
    inference.log
```

`run.json` records source commit, Kaggle artifact version, checkpoint SHA-256,
full command, sample paths, configuration, start/end timestamps, wall time,
device, and success/failure status. Large data, checkpoints, predictions, and
logs remain outside Git. The human-readable result summary is tracked at
`docs/results/strong_baseline_v1.md`.

## Minimal project code

Do not copy model architecture or metric code into this repository. Add only:

- a source/artifact provenance verifier;
- a thin run wrapper that invokes the fixed upstream implementation for one
  explicit sample and never discovers GT during inference;
- a post-prediction evaluator that opens prediction and GT separately, uses
  `evaluate`, `node_recall`, `per_sample_metrics`, and `summarise`, and writes
  JSON;
- focused tests for provenance checks, inference/evaluation phase separation,
  metric serialization, and CLI validation.

The wrapper must fail loudly on missing files, hash mismatches, unsupported
shapes, absent image quantiles, empty prediction graphs, missing
`estimated_number_of_nodes`, or unavailable ILP support. It must not substitute
dummy data, silently disable ILP, or emit an empty result as success.

## Evaluation contract

The recorded result must include:

- prediction node and edge counts;
- Edge TP, FP, FN;
- Division TP, FP, FN;
- raw Edge Jaccard;
- Adjusted Edge Jaccard using GT `estimated_number_of_nodes`;
- Division Jaccard;
- Final Score = Adjusted Edge Jaccard + `0.1 * Division Jaccard`, dropping the
  division term only when the official summarizer does so;
- node recall as a diagnostic;
- wall time and CPU/GPU device.

Evaluation operates on a copy or on a freshly loaded prediction because the
official evaluator mutates the prediction graph with matching attributes.

## Verification

Run all Python, tests, inference, and evaluation inside the existing healthy
`biohub-dev` Ubuntu container. Baseline status before this work is 26 passing
tests and 24 pre-existing Ruff violations in pinned metric/visualizer files.
Verification therefore consists of:

1. focused tests and Ruff on newly added files;
2. the full pytest suite;
3. a short-frame smoke run that verifies image-only inference and GEFF output;
4. the complete 100-frame run on `44b6_0113de3b.zarr`;
5. structural GEFF reload and official evaluation;
6. result/document consistency checks against `metrics.json` and `run.json`;
7. an existing viewer sanity check without new GUI work.

No Kaggle submission, commit, push, container rebuild, large training run, or
hyperparameter sweep is part of this goal.
