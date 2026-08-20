# Reproducibility fixtures

Two kinds of fixture live under `tests/`, and they are never mixed.

## `real_receipts/` — REAL, copied verbatim, never regenerated

Byte-for-byte copies of provenance receipts produced by actual runs. They are small
JSON records, not competition data, predictions, or checkpoints, so tracking them is
consistent with `AGENTS.md` §8. They exist so the invariant tests assert against what
the pipeline really wrote, not against a hand-written idealisation of it.

| Fixture file | Copied from (Codex worktree, branch `codex/biohub-multi-method-race`) |
|---|---|
| `dev_full_auto_compact_timed_race_receipt.json` | `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json` |
| `dev_full_auto_compact_timed_prediction_manifest.json` | `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/prediction_manifest.json` |
| `full_auto_cache_manifest.json` | `artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b/manifest.json` |
| `full_auto_cache_READY` | `artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b/READY` |
| `strong_baseline_v1_*_run.json` / `_metrics.json` / `_prediction_manifest.json` / `_source_receipt.json` | `artifacts/strong_baseline_v1/{official_ilp,harmonic_ilp}/` |

`strong_baseline_v1_official_ilp_source_receipt.json` is absent because the run never
wrote one; that absence is itself an audited finding, not a copy error.

## Synthetic fixtures — built inside the tests, labelled at the point of use

Every fixture the tests construct themselves is **synthetic**: made-up bytes and
made-up digests used only to prove that a guard fires. They carry no scientific
meaning and are never used to stand in for a measurement. Each builder is named
`synthetic_*` and documented as such, per `AGENTS.md` §8.
