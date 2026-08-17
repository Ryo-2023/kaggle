# V-trace learning-health repair evidence (2026-08-08)

## Scope

This run implements and exercises the four root-cause fixes identified from
`docs/evidence/vtrace-no-progress-20260807.md` and the Claude report:

1. sampled behavior probabilities use unperturbed logits, not Gumbel decode logits;
2. each game receives a deterministic but independent sampling seed;
3. V-trace health records importance ratio, continuation `c`, and opponent-stratified critic values;
4. the neural representation retains Pokémon zone, energy composition, attachment/pre-evolution bags,
   candidate endpoint Pokémon snapshots, categorical scalar semantics, and a critic-only opponent bucket.

The old checkpoint topology is rejected by `representation_version=2`; the v2 checkpoints below were
trained from the existing sealed teacher snapshots rather than silently loading incompatible v1 weights.

## Verification commands

Focused tests:

```text
PYTHONPATH=. pytest tests/meta_specialist/test_actor_pool_v1.py -q
PYTHONPATH=. pytest tests/meta_specialist/test_collect_trajectories_cli.py -q
PYTHONPATH=. pytest tests/meta_specialist/test_train_from_trajectories.py -q
PYTHONPATH=/tmp/meta-testpkg:$PWD:$PWD/src pytest /tmp/meta-testpkg/tests/meta_specialist/test_neural_model_v1.py -q
PYTHONPATH=/tmp/meta-testpkg:$PWD:$PWD/src pytest /tmp/meta-testpkg/tests/meta_specialist/test_neural_checkpoint_v1.py /tmp/meta-testpkg/tests/meta_specialist/test_neural_policy_v1.py /tmp/meta-testpkg/tests/meta_specialist/test_trajectory_target_equivalence_v1.py /tmp/meta-testpkg/tests/meta_specialist/test_trajectory_target_v1.py /tmp/meta-testpkg/tests/meta_specialist/test_value_head_gap_v1.py -q
```

Observed: actor pool `74 passed`; collection planner `37 passed`; trajectory training `19 passed, 2 skipped`;
neural model `14 passed`; checkpoint/policy/trajectory/value tests `19 passed, 12 skipped`.

## Executed 1–4 pipeline

### 1. v2 BC initialization

Command (the first attempted full shard-index run was stopped after confirming it would spend many minutes
revalidating 8–21 GB of shards; the bounded smoke uses the same sealed snapshots' `snapshot-0000.json`):

```text
PYTHONPATH=. python scripts/run_parallel_lanes.py --stage bc --lanes all \
  --run-prefix v2smoke \
  --snapshot-template 'runs/meta-specialist-teacher-records/t1-{lane}/snapshot-0000.json' \
  --max-steps 200 --examples-per-step 64 --microbatch-examples 16 \
  --checkpoint-interval-steps 100 --total-threads 16 --max-torch-threads 2 --eval-every-steps 0
```

All four lanes completed. Checkpoints:

- `runs/meta-specialist-bc-distill/v2smoke-alakazam/checkpoints/`
- `runs/meta-specialist-bc-distill/v2smoke-archaludon/checkpoints/`
- `runs/meta-specialist-bc-distill/v2smoke-grimmsnarl/checkpoints/`
- `runs/meta-specialist-bc-distill/v2smoke-rocket/checkpoints/`

### 2. independent sampled collection

```text
PYTHONPATH=. python scripts/run_parallel_lanes.py --stage rl-collect --lanes all \
  --run-prefix v2smoke --rl-games 8 --total-threads 16 --max-torch-threads 2
```

Collected records:

| lane | completed games | transitions | distinct per-game seeds |
|---|---:|---:|---:|
| Alakazam | 6 | 442 | 6 |
| Archaludon | 8 | 468 | 8 |
| Grimmsnarl | 8 | 451 | 8 |
| Rocket | 8 | 471 | 8 |

The two Alakazam faults are counted as faults by the collector and do not enter training.

### 3. V-trace training

```text
PYTHONPATH=. python scripts/run_parallel_lanes.py --stage rl-train --lanes all \
  --run-prefix v2smoke --max-steps 4 --checkpoint-interval-steps 4 \
  --total-threads 16 --max-torch-threads 2
```

The first scored step is the critical invariant check (current model equals behavior checkpoint):

| lane | transitions | mean log-prob shift | mean ratio | mean `c` | critic opponent strata |
|---|---:|---:|---:|---:|---:|
| Alakazam | 442 | `+3.27e-08` | `1.00000003` | `0.99999993` | 6 |
| Archaludon | 468 | `-4.24e-11` | `1.00000000` | `0.99999992` | 8 |
| Grimmsnarl | 451 | `+4.62e-10` | `1.00000000` | `0.99999992` | 8 |
| Rocket | 471 | `-1.08e-08` | `0.99999999` | `0.99999990` | 8 |

After four updates, mean `c` was still reported rather than hidden (Alakazam `0.908`, Archaludon `0.871`,
Grimmsnarl `0.903`, Rocket `0.829` on the last step). This makes cumulative trace attenuation visible and
prevents the old `dead_rho`-only false health signal.

Training summaries:

- `runs/meta-specialist-training/v2smoke-rl-alakazam/run_summary.json`
- `runs/meta-specialist-training/v2smoke-rl-archaludon/run_summary.json`
- `runs/meta-specialist-training/v2smoke-rl-grimmsnarl/run_summary.json`
- `runs/meta-specialist-training/v2smoke-rl-rocket/run_summary.json`

### 4. fixed holdout smoke evaluation

Each lane was measured against `kiyotah_lucario`, `skarin_dragapult`, and `sue124_alakazam`, one game per
seat (six games total). There were zero faults in every evaluation.

| lane | v2 BC score | v2 RL score | delta |
|---|---:|---:|---:|
| Alakazam | 0.167 | 0.333 | +0.167 |
| Archaludon | 0.167 | 0.000 | −0.167 |
| Grimmsnarl | 0.333 | 0.167 | −0.167 |
| Rocket | 0.167 | 0.667 | +0.500 |

These six-game values have very wide intervals and are not an improvement claim. They are a completed
end-to-end wiring check; larger fixed-schedule evaluation is required before selecting a checkpoint.

Evaluation artifacts:

- `runs/meta-specialist-strength/v2smoke-bc-<lane>.json`
- `runs/meta-specialist-strength/v2smoke-<lane>.json`

## Result

The original structural failures are removed: sampled trajectories now satisfy the behavior-policy
invariant at collection-time, exploration streams are independent, trace attenuation is directly measured,
critic strata are retained, and the model no longer discards the Pokémon/endpoint information required by
the teacher policy. The smoke experiment is intentionally not presented as statistically significant policy
improvement; it demonstrates that the repaired learning path runs to completion with auditable diagnostics.
