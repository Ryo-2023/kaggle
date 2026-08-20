# Strong Baseline v1 final review fixes

## Scope and outcome

This isolated worktree contains the final review-fix implementation for the
provenance and evaluation boundaries. No 100-frame inference, smoke inference,
Kaggle submission, commit, staging, or push was performed.

The exact upstream commit and checkpoint constants remain unchanged:

- source: `075fc5f5a52d11077f9dc2b074644618f26939e2`
- checkpoint: `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235`

## Changed files

- `src/biohub/strong_baseline/provenance.py` — clean tracked-source/index
  verification while permitting ignored generated predictions.
- `src/biohub/strong_baseline/manifest.py` — shared deterministic prediction
  manifest creation and validation.
- `src/biohub/strong_baseline/runner.py` — fail-closed provenance preflight,
  automatic official/harmonic manifests, CPU CUDA visibility guard, actual
  device checks, and receipt schema fields for artifact/notebook versions.
- `src/biohub/strong_baseline/evaluation.py` — persisted-manifest validation
  before any GT load and a recorded validation receipt.
- `tests/test_strong_baseline_provenance.py`,
  `tests/test_strong_baseline_runner.py`,
  `tests/test_strong_baseline_evaluation.py`,
  `tests/test_strong_baseline_harmonic.py` — RED/GREEN regression coverage.
- `tests/fixtures/strong_baseline_v1/` — sanitized tracked evidence fixtures
  for receipts, manifests, metrics, visual values, and public-source metadata.
- `tests/test_strong_baseline_result_report.py`,
  `tests/test_strong_baseline_visual_check.py` — fixture-backed fresh-clone
  report/visual checks plus an optional real-artifact derivation check.
- `docs/results/strong_baseline_v1.md` — automatic-manifest boundary,
  expected-vs-actual device labels, Kaggle artifact version `1`, organizer
  notebook version `331429261`, and explicit harmonic-source blocker wording.

## RED/GREEN evidence

The review tests were authored before the production changes. The initial host
RED attempt could not collect because host Python has neither `uv` nor the
project dependencies; no inference was started. Verification then ran in the
mandated existing `biohub-dev` container.

Focused GREEN:

```text
docker compose exec -T -w /workspace/.../scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run pytest -q tests/test_strong_baseline_*.py
54 passed, 1 warning
```

Final full GREEN:

```text
docker compose exec -T -w /workspace/.../scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run pytest -q
80 passed, 1 warning
```

Targeted lint:

```text
docker compose exec -T -w /workspace/.../scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run ruff check src/biohub/strong_baseline scripts/run_strong_baseline_v1.py tests/test_strong_baseline_*.py
All checks passed!
```

Fresh-clone simulation with
`BIOHUB_STRONG_BASELINE_ARTIFACTS=/private/tmp/strong-baseline-artifacts-absent`
passed `8 tests, 1 skipped`; the skip is the optional integration derivation
check, while all report/visual unit checks use tracked fixtures.

## Compatibility notes

Existing manifests remain readable: validation requires the persisted path,
directory digest, file count/bytes, and node/edge counts, while older manifests
may omit the new creation-time/action metadata. The GEFF directories were not
rewritten. New inference manifests are written beside predictions and include
creation time/action. Evaluation writes receipt fields only after successful
pre-GT validation.

`RunReceipt.device` remains available as the actual device for compatibility;
new JSON receipts also persist `expected_device` and `actual_device`.
CPU requests set `CUDA_VISIBLE_DEVICES=""` for the official subprocess and
the harmonic in-process model path and fail closed if a reported/model device
differs.

## Harmonic-source blocker

The ignored artifact tree contains only
`artifacts/strong_baseline_v1/harmonic_ilp/source_receipt.json`; the retained
full notebook JSON for `scriptVersionId=338569479` is absent. The tracked
fixture therefore preserves the exact recorded URL/version/digest/license
metadata and explicitly records `BLOCKED` rather than fabricating a source
cell or formula copy. An independently auditable verbatim source fixture and
parser/digest comparison still require recovery of that original JSON. The
existing measured harmonic GEFF was not rerun or modified.

The requested `task-3-report.md` file is not present in this isolated worktree,
so its reported truncated digest could not be edited without inventing a
target. The permanent strong-baseline report retains the complete official
directory digest from the existing manifest.
