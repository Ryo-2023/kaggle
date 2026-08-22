# Lane G — reproducibility / provenance audit

Branch `claude/g-repro`. Audited read-only against the Codex worktree
(`scratch/strong-baseline-v1/...`, branch `codex/biohub-multi-method-race`) at commit
`4c58634`. Every number below was recomputed from persisted bytes; nothing is quoted
from a report.

## 1. Conclusion

- **No S0 from the cache rewrites.** The tree contains one genuine before/after pair,
  and the detector output is byte-identical across it. The capture rewrite did not move
  the numbers.
- **But `cache_hash` could never have told you that.** It hashes the whole manifest,
  including `provenance.elapsed_seconds`, so it changes when the machine is slower. It
  is a run identity, not a content digest, and it produced a false difference on exactly
  the pair where the answer mattered.
- **The five-sample panel is not detector-fixed across samples.** Four caches were built
  by one capture implementation, the development sample by an older one.
- **Three headline artifacts have no audit trail.** The `dev_full_auto*` races wrote four
  predictions into one directory; the shared manifest describes only the last.
- **`prediction_manifest_validated_before_gt` is a hardcoded `True`.** The underlying
  check is real but intra-call; nothing persisted can falsify the claim.

## 2. Is each result reproducible from its recorded provenance alone?

| Result | Backing artifact | Reproducible today? | What is missing |
|---|---|---|---|
| `strong_baseline_v1.md` — official 0.88379 / harmonic 0.92112 | `artifacts/strong_baseline_v1/{official_ilp,harmonic_ilp}/` | **Partly.** `run.json` carries full argv, `source_commit`, `checkpoint_sha256`, `device`. | No `requested_device`; no code identity for `harmonic.py`; `official_ilp` has no `source_receipt.json` while `harmonic_ilp` does. |
| `detector_fixed_association_race.md` — four methods on `44b6_0113de3b` | `artifacts/detector_fixed_race/dev_full_auto_compact_timed/` | **No.** | The cache's `adapter_source_sha256` (`24ac2cb6…`) is two commits behind HEAD; the association code has no recorded identity; `command` is nowhere; three of four prediction manifests were clobbered. |
| Five-sample panel results | `artifacts/detector_fixed_race/panel_runs*/` (20 runs) | **Partly.** Receipts join to a cache manifest by `cache_hash`, which supplies source commit, checkpoint, device. | `command` absent everywhere; association code identity absent; dev sample's cache built by a different capture implementation than the other four. |
| Harmonic reverse-weight sweep | `artifacts/detector_fixed_race/harmonic_sweep/*` | **Partly**, same gaps as the panel. | Additionally, `rw_0p20` writes its prediction as bare `harmonic_v1.geff` while `rw_0p10`/`rw_0p30` add a suffix, so the sweep arm is not recoverable from the filename. |
| `multi_method_benchmark_race.md` — blob_lap / cc_flow / motion_lap | `artifacts/multi_method_race_final/` | **No.** | `metrics.json` lives under `evaluation/<method>/` with no receipt beside it; the receipt is under `methods/<method>/` and still lacks `checkpoint_sha256`, `cache_digest`, `device`, `command`. |
| `performance_experiments/blob_lap_nms35` | same directory | **No.** | No receipt of any kind beside `metrics.json`. |

## 3. Findings

### S1 — the headline cache cannot be rebuilt by any code that exists

`adapter_source_sha256` is the SHA-256 of `upstream_adapter.py` alone. Mapping every
recorded value back to the branch:

| Cache | Sample | Adapter | Built at |
|---|---|---|---|
| `full_auto` | `44b6_0113de3b` | `24ac2cb6…` | `830ccab..b31dd76` |
| `panel_auto` | `44b6_0b24845f` | `e914af35…` | `8b03cd6..4c58634` |
| `panel_auto` | `44b6_0c582fdc` | `e914af35…` | `8b03cd6..4c58634` |
| `panel_auto` | `44b6_0db75fae` | `e914af35…` | `8b03cd6..4c58634` |
| `panel_auto` | `44b6_12dfb391` | `e914af35…` | `8b03cd6..4c58634` |
| `smoke_disk` | `44b6_0113de3b` | `bd7bfb7a…` | `19feb13` |
| `smoke_fix` | `44b6_0113de3b` | `fcec103b…` | **not in git** |
| `smoke_auto` | `44b6_0113de3b` | `104102a9…` | **not in git** |

Two caches were built from source that was never committed. The development sample —
the one every headline number is quoted from — is the only full-length cache built by
the older implementation, so the five-sample panel aggregates across two detector code
versions while treating them as one fixed detector.

The hash also covers only `upstream_adapter.py`. Edits to `association.py`,
`prediction.py` or `harmonic.py` — the code that decides the compared numbers — leave no
trace in any receipt.

### S1 — `cache_hash` is a run identity, not a content digest

`smoke_fix` and `smoke_disk` are the same sample, the same four frames, the same
`image_sha256`, and different capture implementations:

| | `smoke_fix` | `smoke_disk` |
|---|---|---|
| adapter | `fcec103b…` | `bd7bfb7a…` (commit `19feb13`) |
| nodes / candidate edges | 897 / 151830 | 897 / 151830 |
| `node_digest` | `703885a91d7a…` | `703885a91d7a…` |
| `edge_digest` | `27c5e6a69c43…` | `27c5e6a69c43…` |
| feature conflicts | 453 | 453 |
| `elapsed_seconds` | 138.78 | 340.36 |
| **`cache_hash`** | **`e3be83de…`** | **`97eb6d16…`** |

The detector output is byte-identical; the digest that is supposed to certify that says
they differ. `cache_hash` covers `provenance.elapsed_seconds`,
`adapter_source_sha256` and the per-call counters, so it can never certify sameness and
raises a false alarm on every refactor.

`biohub.reproducibility.cache_identity` splits the manifest into `content_inputs`
(image, checkpoint, detector config, resolved device, pinned commit),
`content_outputs` (array digests and counts) and `run_metadata`. The invariant worth
asserting is **equal inputs ⇒ equal outputs**, on the content digests.

### S1 — the prediction manifest is per-directory, so three audit trails are gone

`write_prediction_manifest` targets `<parent>/prediction_manifest.json`. Any directory
holding more than one prediction keeps only the last writer's manifest.

Still broken on disk, never regenerated:
`dev_full_auto/`, `dev_full_auto_compact/`, `dev_full_auto_compact_timed/` — four GEFFs
each, one manifest each, all describing `motion_gated`. The GEFF bytes are intact (all
four directory digests still match `race_receipt.json`), but `official_ilp`,
`harmonic_v1` and `mutual_confidence` can no longer be re-validated: the recorded
`prediction_path` names a different prediction.

The newer `panel_runs_*` layout escapes this by writing one prediction per directory —
a convention, not a fix. The writer is unchanged.

### S1 — the GT-ordering claim is unfalsifiable as persisted

`prediction_manifest_validated_before_gt: True` is a literal. Across all 20 persisted
race receipts, `manifest_created_at` never reaches the metrics payload, so the ordering
cannot be rechecked from a saved receipt even in principle.

The replacement in `biohub.reproducibility.gt_guard` makes the ordering structural:
ground truth cannot be opened without a `PredictionPersistedToken`, which can only be
minted from a prediction whose manifest is already on disk, names that prediction, and
whose recorded digest still equals the bytes. The token is re-verified at the
ground-truth open, and the emitted receipt carries a digest and a creation timestamp a
third party can recheck instead of a constant.

### S2 — the two pipelines disagree about device provenance

`detector_fixed_race` resolves `auto` through `resolve_torch_device` and records
`requested_device='auto'` with `device='cpu'`. `strong_baseline/runner.py` defaults
`expected_device="cpu"`, never resolves, and its persisted `run.json` carries only
`device`. A receipt from each pipeline cannot be compared on that field without an alias
table, and the strong-baseline receipts cannot distinguish "CPU was selected" from "CPU
was the only thing attempted".

### S2 — a persisted prediction is named after its method, not its sample

Every one of the 20+ persisted predictions has a method-name stem
(`harmonic_v1.geff`, `official_ilp.geff`, …). Packaging derives the submission's
`dataset` column from the stem, so all five samples' `harmonic_v1` predictions collapse
onto one `dataset` value. The sample id exists only in the parent directory name, which
packaging does not read. No metric can catch this: the local score is computed from the
GEFF directly and is unaffected.

### S3 — nothing seeds anything

No `torch.manual_seed`, no `torch.set_num_threads`, no
`use_deterministic_algorithms` anywhere in `src/biohub`. Determinism is assumed rather
than pinned, and no receipt records a seed or a `torch` version.

### S3 — manifest schemas diverge between evaluators

`detector_fixed_race` requires `ground_truth_included=false`; the strong-baseline
manifests omit the field entirely, so the same file cannot be validated by both. The
former stores repo-relative paths and the latter absolute ones, and
`validate_prediction_manifest` resolves the recorded path against the process CWD — so
a race manifest only validates when run from the repository root.

## 4. What was built

`src/biohub/reproducibility/`

| Module | Role |
|---|---|
| `digest.py` | Independent re-derivation of the prediction-directory digest and the cache hash. Deliberately does not call the code that produced them. |
| `gt_guard.py` | Mint-only persistence token; ground truth is unopenable without one. Re-verifies bytes at the open and emits a self-checking receipt. |
| `cache_identity.py` | Splits a cache manifest into content inputs, content outputs and run metadata. |
| `receipts.py` | Receipt completeness, detector invariance, method sensitivity, device consistency, prediction identity. |

`tests/` — 116 tests, all passing:

| File | Tests | Asserts |
|---|---:|---|
| `test_reproducibility_gt_ordering.py` | 23 | ordering is structural, tokens unforgeable, timestamps usable |
| `test_reproducibility_detector_invariance.py` | 11 | one detector digest per race; the digest covers the detector config |
| `test_reproducibility_cache_identity.py` | 16 | the rewrite preserved output; `cache_hash` cannot show it |
| `test_reproducibility_method_sensitivity.py` | 16 | determinism; methods differ; recorded digests match bytes |
| `test_reproducibility_receipt_completeness.py` | 29 | required fields, device consistency, manifest divergence |
| `test_reproducibility_artifact_tree.py` | 9 | sweeps every receipt on disk, so new runs are audited too |
| `test_reproducibility_submission_identity.py` | 12 | a prediction's identity is its sample, not its method |

`scripts/mutation_check_reproducibility.py` disables one guard at a time and requires the
test that should notice to go red: **13/13 mutations caught**.

## 5. Recommended changes, ranked

1. Name the prediction manifest after the prediction
   (`<method>.geff.manifest.json`), so a sibling write cannot destroy it.
2. Record a content-addressable detector identity — the pair
   `(node_digest, edge_digest)` — and assert **equal inputs ⇒ equal outputs** across
   caches. Stop using `cache_hash` for sameness claims.
3. Propagate `manifest_created_at` into `metrics.json` and replace
   `prediction_manifest_validated_before_gt: True` with the digest and the two
   timestamps, so the claim can be rechecked.
4. Hash the association code (`association.py`, `prediction.py`, `harmonic.py`) into
   every race receipt; `adapter_source_sha256` covers only the capture module.
5. Record the CLI argv in `race_receipt.json`. It is the one required fact missing from
   every detector-fixed receipt.
6. Rebuild the development sample's cache with the current capture implementation, or
   state in every panel table that one of five samples used older code.
7. Name predictions `<sample_id>__<method>.geff` before any submission is packaged.
8. Give `strong_baseline/runner.py` the same `resolve_torch_device` path as
   `detector_fixed_race`, and record `requested_device` alongside `device`.
