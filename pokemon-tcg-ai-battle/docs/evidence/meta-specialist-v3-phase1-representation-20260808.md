# Meta Specialist v3 Phase 1 representation evidence

Date: 2026-08-08 (JST)

## Scope

This is the first implementation/benchmark slice of the Luna Max plan. It is
not the full teacher-corpus benchmark: the harness uses a deterministic,
synthetic 128-example supervised task so the topology, timing, and report
format can be exercised before spending GPU/CPU budget on the full corpus.

Implementation files:

- `src/mage_ptcg/meta_specialist/representation_v3.py`
- `src/mage_ptcg/meta_specialist/neural_model_v3.py`
- `src/mage_ptcg/meta_specialist/representation_benchmark_v3.py`
- `tests/meta_specialist/test_representation_v3.py`
- `tests/meta_specialist/test_representation_benchmark_v3.py`

The v1 focused regression tests also remain green: actor pool 74 passed,
trajectory collection CLI 37 passed, and trajectory trainer 19 passed/2
skipped.

After the temporary `tests` namespace isolation described in the Phase 0
evidence, the complete `tests/meta_specialist` suite finished with 1479
passed, 23 skipped, and 2 existing unknown-mark warnings in 93.35 seconds.
After the Phase 7–9 integration smoke compatibility tests were added, the
latest full suite is 1481 passed, 23 skipped, and 2 existing unknown-mark
warnings in 93.66 seconds.

## Contracts covered

- Typed `EntityTokenV3`, `ActionCandidateV3`, and `RelationalStateV3` schemas
  reject invalid ranges, dangling host/action references, and hidden entities
  carrying a card identity.
- The actor-visible v1 input has a boundary adapter. It preserves public
  Pokemon/attachment host edges and visible card multiplicity, while unresolved
  hidden endpoints remain unresolved; no serial or local action ID is restored.
- R3-A is a nonlinear zone-specific DeepSets encoder.
- R3-B is a two-block pre-norm relation-aware attention encoder with explicit
  host relation features and no positional/entity-serial embedding.
- The candidate encoder includes source/target relation vectors, action type,
  selection context/step, stable action ID hash, and numeric/categorical args.
- The policy has a one-layer GRU. `episode_start=True` discards a carried hidden
  state, and the model constructor preserves the caller's global Torch RNG.

## Relation/invariance test result

Command:

```text
PYTHONPATH=. pytest tests/meta_specialist/test_representation_v3.py -q
11 passed in 0.88s
```

Covered cases are active/bench swap, attachment host swap, owner swap, hidden
identity invariance, exchangeable bench permutation invariance, action
source/target binding, legal-action order/logit permutation, deterministic
seeded initialization, R3-A permutation invariance, v1 projection shape, and
GRU episode reset.

## Synthetic benchmark result

Command:

```text
PYTHONPATH=src python -c 'from mage_ptcg.meta_specialist.representation_benchmark_v3 import run_representation_benchmark_v3; print(run_representation_benchmark_v3(seed=5, samples=128, epochs=5))'
```

The benchmark trains a small linear head together with each encoder on the
synthetic task and evaluates on a held-out suffix. Values below are the actual
run (NLL is lower-is-better; top1/top3 are higher-is-better):

| candidate | NLL | top-1 | top-3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| R2 negative control | 0.7078 | 1.0000 | 1.0000 | 0.0456 | 0.0713 |
| R3-A zone DeepSets | 1.5035 | 0.3846 | 0.6154 | 0.7780 | 1.1333 |
| R3-B relation attention | 1.4856 | 0.4231 | 0.8462 | 0.7756 | 1.3142 |

## Gate 1 status

**Not yet passed.** The relation tests pass, but this synthetic benchmark is a
negative result for supervised selection: both v3 candidates trail the simple
R2 control on this particular task, and R3-B is substantially slower than the
control. This is not evidence that v3 is worse on the real teacher corpus—the
task is intentionally small and the R2 control is unusually well aligned with
the synthetic label—but it is a real warning that the representation cannot be
promoted based on relation tests alone.

Before declaring Gate 1, the next required experiment is a corpus-backed,
episode-group split benchmark using the actual legal-action labels, with
trainable encoder/head, rare-action and action-type metrics, and p50/p95
latency on the target environment. The current result is retained as a
negative-control artifact rather than hidden or overwritten.

## First real-record slice (still not Gate 1)

The loader was then exercised on the existing `t1-rocket` teacher records. It
revalidated each local record through the v2 parser, rebuilt the live v1 model
input, projected it to v3, and used the committed semantic action type as a
small supervised target. This is 128 records from a single lane, the
episode-group/near-duplicate split is not yet the final corpus split, and three
training epochs were used as a smoke run.

| candidate | NLL | top-1 | top-3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| R2 negative control | 2.0122 | 0.4231 | 0.5769 | 0.6868 | 0.7281 |
| R3-A zone DeepSets | 1.9903 | 0.4231 | 0.7308 | 14.4996 | 15.9525 |
| R3-B relation attention | 1.9829 | 0.4231 | 0.7308 | 14.4533 | 15.8047 |

This 3-epoch run includes a direct pooled-entity residual added after the first
one-epoch smoke. With a little more optimization, both v3 candidates now have
slightly lower NLL and higher top-3 recall than the R2 control on this slice,
but the sample is still one lane/128 records and the CPU p95 latency is about
22x the control. No Phase 2 promotion is claimed until this is resolved with an
episode-group split, rare-action metrics, and a properly tuned full-corpus run.

The prior one-epoch result is retained as an optimization-budget negative
control: R2 2.0797/0.4231/0.7308, R3-A 2.2872/0.1538/0.5769, and R3-B
2.3290/0.1538/0.7308 (NLL/top-1/top-3). It must not be mixed with the current
three-epoch result when selecting a mainline.

### Optimization-budget sensitivity

The synthetic harness was rerun with the same 128 examples and 20 epochs. R3-B
then reached NLL 0.1018/top-1 1.0000, while R3-A reached NLL 0.6184/top-1
0.6538; R2 reached NLL 0.0152/top-1 1.0000. R3-B can therefore learn the
relation-preserving task, but needs a larger optimization budget than the
one-epoch smoke. Its synthetic p95 latency was 8.59 ms versus R2 0.28 ms.
This separates an optimization-budget confound from the still-real latency
gap; the formal Gate 1 run must use equal early-stopping/compute rules rather
than comparing an undertrained v3 checkpoint with a converged control.

## Vectorized encoder follow-up (current bounded comparison)

After batching the entity-token projection, the same seed=7/3-epoch harness was
run on 128 usable records from each lane. These files supersede the earlier
512-record timing numbers for the current implementation state, but they remain
bounded one-lane slices and are not a formal Gate 1 result.

| lane | R2 NLL | R3-A NLL | R3-B NLL | R2 p95 ms | R3-A p95 ms | R3-B p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| alakazam | 1.820462 | 1.852043 | 1.864900 | 0.491 | 2.096 | 1.685 |
| archaludon | 1.517733 | 1.482217 | 1.476455 | 0.572 | 2.258 | 1.997 |
| grimmsnarl | 1.948337 | 2.020154 | 2.075685 | 0.459 | 2.395 | 2.263 |
| rocket | 2.079650 | 2.312241 | 2.367937 | 0.688 | 3.933 | 2.629 |

Archaludon is the only lane where R3-B NLL is lower than R2; its top-1 is still
lower. R3-B is worse on NLL for the other three lanes. The vectorization reduces
timing materially versus the prior unbatched implementation, but p95 remains
roughly 3–5x the R2 control. Therefore Gate 1 remains **not passed**. The next
formal run must use episode/near-duplicate connected-component splits, at least
three seeds, equal compute or validation early stopping, rare-action and
action-type metrics, and a target-environment latency budget.

Artifacts:

- `runs/meta-specialist-v3/phase1-alakazam-benchmark-vectorized-128.json`
- `runs/meta-specialist-v3/phase1-archaludon-benchmark-vectorized-128.json`
- `runs/meta-specialist-v3/phase1-grimmsnarl-benchmark-vectorized-128.json`
- `runs/meta-specialist-v3/phase1-rocket-benchmark-vectorized-128.json`
