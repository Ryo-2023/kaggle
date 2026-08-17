---
project: MAGE-PTCG
evidence: o1-competition-intelligence-preintegration-final
as_of: 2026-07-18
base: 6782e687a6bb667c3ca5343df9974352ddd7cd2c
final_head: 8d743c8
---

# O1 Pre-integration Verification Freeze

## Verdict

The detached O1 worktree passed complete, deterministic-sharded repository regression: **1305 passed, 0 failed, 0 skipped, 0 errors**. No canonical write, remote push, Kaggle submission, Champion/default change, or Promotion Gate change occurred.

The first O1-6 fixture-cycle audit found two real determinism/completeness defects: fixture provenance used clock/path-derived fields, and the cycle did not persist drift/promotion artifacts. Commit `8d743c8` adds explicit fixture provenance inputs and persists those artifacts. The two clean-root cycle hashes then matched exactly.

## Regression shards

| Group | Result |
|---|---:|
| `tests/competition_intelligence` | 266 passed |
| `tests/offline_training_v1_support` | 196 passed |
| `tests/orchestration` | 215 passed (including `test_overnight_mvp.py` in 25/13/13 deterministic node shards) |
| `tests/review` | 67 passed |
| `tests/unit` | 38 passed |
| `tests/*.py` | 523 passed |

Focused O1 command (`tests/competition_intelligence` plus runtime/CLI integration) passed **274**. Relevant adapter/package/probe/privacy/ActionKey compatibility selection passed **130**.

## Determinism fixture cycle

The clean-root cycle included fixture capability probe, public fixture ingest (`ARCHIVE` only), local fixture normalization, analysis, Intelligence/Meta Snapshot, self-drift, surrogate, fixed/rolling benchmarks, and restricted Promotion Report. Knowledge Snapshot was not used.

| Artifact | SHA-256 (both roots) |
|---|---|
| Intelligence Snapshot | `96c486b3a6e0cc1faf3ecc946658275a17d4976f3d7d0831c0e7fcda9162aa55` |
| Meta Snapshot | `a67e9b7799724983fb37f93669ec8f5a20ba9224d959d7f781ab709a43a0ff7e` |
| Drift | `8afa24ea54321fe3f1ec2f97ac557cf08518241808de2abb8e92a41ceee177a1` |
| Surrogate | `356ae1947f5288ad6b835272c57a5ff05042977f91ba0441677ef4cd4b9fe9bb` |
| Fixed benchmark | `9948619b87b4605ede632d56f86a4937d2914fc6ae6a9a1e298a6934df778ed9` |
| Rolling benchmark | `352970b64b8c67c8bfbef50147dfa5cddd897984d289ce9b40c322dea53fac15` |
| Promotion Report | `a7d46b3b28a84f2288db15ce683d1273d0514abc1f5309c9487e14ceec9c9d41` |

## Safety audit

- `PUBLIC_OTHER + TRAINING` and `REDISTRIBUTION` are rejected by SourceEnvelope, ingestion, snapshot selection, and Offline Training export tests.
- External action and Surrogate paths have no Student behavior-cloning import route; runtime isolation and package tests pass.
- Promotion reports reject `PROMOTED`; their only decisions are `NO_DECISION`, `REVIEW_REQUIRED`, and `INSUFFICIENT_EVIDENCE`.
- Protected-file Git blob mismatch count is 0; filesystem SHA-256 manifest is in the portable artifact bundle.
- Static checks passed: compileall, docs validator, diff check, conflict marker scan. Secret/path hits were only scanner implementation text and intentionally malicious test fixtures; no tracked `.venv`, credential, live Kaggle response, or absolute home path was committed.

## Portable integration material

`artifacts/o1-competition-intelligence-v1/` contains `checkpoint-complete.bundle`, `patches-complete/`, ordered commit list, diff stat, and protected manifests. The new local-only tag is `checkpoint/o1-competition-intelligence-v1-complete` at `8d743c8`.

Canonical integration is a human-only operation: verify the bundle, fetch it into a temporary local branch, cherry-pick the nine commits listed in `commit-list-complete.txt` oldest-first, resolve the documented status-file additions by retaining both sides, then rerun this regression before any PR. Do not merge/rebase/reset canonical directly.
