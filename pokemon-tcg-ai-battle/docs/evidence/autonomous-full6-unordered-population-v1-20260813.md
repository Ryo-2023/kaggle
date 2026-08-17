# Full6 unordered population descriptor — 2026-08-13

## 結論

既存のFull6 blocked bridgeとrepair descriptorを上書きせず、`FULL6_UNORDERED_POPULATION_V1` の研究専用descriptorを新規生成した。36,684 decisionのうちunordered setとして扱える36,680件を明示し、ordered `5:34` 4件を `QUARANTINED_ORDERED_UNSUPPORTED` として保持した。non-ubiquitous near-duplicateのcross-split componentは1件のsource identityまでbindしたが、全raw reproduction、component assignment、ordered record identityのmaterializationは未完了である。したがって `performance_training_ready=false`、`ready_for_training=false`、`ready_for_behavior=false` のままであり、学習・CABT・behavior collectionへ接続しない。

## Primary artifact

| artifact | path | file SHA-256 | semantic SHA |
|---|---|---|---|
| Full6 unordered descriptor | `runs/final-sprint-autonomous/full6-unordered-population-v1/manifest.json` | `1ffae9d91451ba89350588f226cc74183a80ecab6ff4c6acd44301873bb605a2` | `bd0c5ec276b641f9ef74caadfdc40972bfe52d279cbe8045bdd84162dc6b7434` |
| blocked Full6 bridge (unchanged) | `runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-full6/bridge-manifest.json` | `0639f01c61cd016a4b8b12cfa5b0f675c07ace4552a19a796048f95e45c85c6f` | `bbd6fc7d7a78fb8dd736908699103551d4cad0a06fc1223c4547db50a05f36dc` |
| Tomato clean bridge (unchanged control) | `runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-tomato/bridge-manifest.json` | `8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983` | `3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0` |
| prior Full6 repair descriptor (unchanged) | `runs/final-sprint-autonomous/student-v3-full6-repair-v1/manifest.json` | `a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7` | `f5c50c93e33e95bb815154ba6c60a4f34271a17f647bfdc9b016cc2509e840f2` |
| derived teacher catalog (unchanged) | `runs/final-sprint-autonomous/derived-teacher-catalog-v2b/catalog.json` | `8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4` | `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e` |

## Closed coverage and quarantine

| field | value |
|---|---:|
| source decisions | 36,684 |
| unordered-set decisions | 36,680 |
| ordered quarantine | 4 |
| ordered schema | `5:34` (4) |
| quarantine status | `QUARANTINED_ORDERED_UNSUPPORTED` |
| ordered identities/target sequences materialized | no |
| silent drop | no |

The descriptor preserves the exact blocked bridge facts: `ordered_selection_requires_pointer_head` is the only unsupported reason, and the 36,680 count is not treated as a publishable dataset. No partial JSONL or GPU shard was written (`published_rows=0`, `partial_dataset_published=false`).

## Split closure

The source bridge reports one non-ubiquitous near-duplicate split intersection:

```text
5a996ab25264020f3a776c00489771e41b1bfbd2a0cff63eb0c907a8953e80ed
```

The planned algorithm is `episode-nonubiquitous-near-duplicate-connected-component-majority-v1`. The descriptor records `assignment_materialized=false`, `output_non_ubiquitous_cross_count=null`, and `closure_verified=false`; it therefore does not claim that the leakage has been repaired. The existing Full6 raw bridge and repair manifest remain byte-identical.

## Permission matrix

All six catalog teachers are represented in the machine-readable `permission_matrix`. The matrix deliberately separates permissions:

- training-local record / derived-weight use: `true` where the catalog permits `training-local` derived weights;
- behavior policy or direct teacher behavior labels: `false` for every row;
- derivative action labels: `false` (the descriptor is not a behavior/on-policy collection authorization);
- copied teacher code and deck submission: `false` for every row;
- top-level training, behavior, promotion, submission, and external execution authority: all `false`.

`teacher_usage_boundary=local_eval_only` remains bound for every native teacher asset. Training-local derived-weight permission is not promoted to behavior permission or submission permission.

## Readiness / next gate

```yaml
raw_reproduction_complete: false
ordered_quarantine_materialized: false
component_split_materialized: false
performance_training_ready: false
ready_for_training: false
ready_for_behavior: false
```

The next safe gate is an explicit, time-bounded raw reproduction that materializes the four ordered record IDs/target sequences and the complete connected-component assignment, followed by strict reload and independent permission review. Until then, Full6 remains descriptor-only. No CABT, training, AWR, behavior collection, longrun, package promotion, or submission was started.

## Implementation and verification

| file | SHA-256 |
|---|---|
| `src/mage_ptcg/meta_specialist/full6_unordered_population_v1.py` | `d305c1c91e211be52920db39a39935b69fea593eed07b03091d161e1221d7a8d` |
| `scripts/build_full6_unordered_population_v1.py` | `e73d97dcd6a656a297b9fe97d6b92391fed8026b447d5de25a7227ba8472f651` |
| `tests/meta_specialist/test_full6_unordered_population_v1.py` | `bc2f6e41a4f6543509a800089c150e2f80e1f4e63cb1d435bdec0e6053ee30a3` |

Commands:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_full6_unordered_population_v1.py
4 passed in 0.67s

PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_full6_unordered_population_v1.py \
  tests/meta_specialist/test_student_v3_full6_repair_v1.py
# 9 passed in 0.54s

PYTHONPATH=.:src .venv/bin/python scripts/build_full6_unordered_population_v1.py \
  --repo-root . \
  --blocked-full6-bridge runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-full6/bridge-manifest.json \
  --tomato-clean-bridge runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-tomato/bridge-manifest.json \
  --repair-manifest runs/final-sprint-autonomous/student-v3-full6-repair-v1/manifest.json \
  --output-manifest runs/final-sprint-autonomous/full6-unordered-population-v1/manifest.json
# output semantic SHA bd0c5ec276b641f9ef74caadfdc40972bfe52d279cbe8045bdd84162dc6b7434

PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/full6_unordered_population_v1.py \
  scripts/build_full6_unordered_population_v1.py
# exit 0
```

The focused tests also verify semantic-SHA tamper rejection and no-overwrite behavior. Existing raw source/artifacts were not modified.
