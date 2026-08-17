# META_TRAIN population-bound cg alternating runtime — 2026-08-14

## 結論

102-row meta distributionを、`META_TRAIN`・evaluation-only・`local_eval_only`・`smoke_ok=true`に限定した上位24 opponent scheduleへ固定し、既存cg alternating evaluatorへ接続した。P1 `cg-lethal-target-v1`はP0 `root-cg-self-owned-v1`を96局で上回ったが、seed-disjoint 384局では差が+0.7813ptへ縮小し、P1 seat gap 9.90ptのため`NOT_PROMOTABLE`。768/longrun、training、teacher、promotion、submissionは起動していない。

## Schedule / contract

- schedule: `runs/final-sprint-autonomous/cg-population-schedule-top24-v1-20260814/schedule.json`
- schema: `meta-specialist-cg-population-schedule-v1`
- split: `META_TRAIN` only; 24 refs; `META_DEV`/`META_FINAL` excluded
- source meta manifest SHA: `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- schedule file SHA: `d9b59a3ed3cb07f3845a5b32999ec86898d7fdec07b2e7bbb6a728948e25c7c3`
- evaluation-only: true; behavior_allowed: false; teacher_labels_saved: false; authority: all false

The wrapper reuses `CgPackageSpecV1`, `build_cg_pair_games_v1`, paired strata, and `run_parallel_cabt_evaluation`. It binds candidate/control package identity, schedule SHA, pool SHA, evaluator SHA, stage, base seed, workers, and recycle policy into no-clobber sidecars.

## Results

| stage | P1 candidate | P0 control | delta | status |
|---|---:|---:|---:|---|
| 96 | 21-0-75 (21.8750%) | 13-0-83 (13.5417%) | +8.3333pt | screen; control seat gap 6.25pt |
| 384 | 69-0-315 (17.9688%) | 66-0-318 (17.1875%) | +0.7813pt | `NOT_PROMOTABLE`; P1 seat gap 9.90pt |

Both stages used the same 24 opponent IDs, both seats, paired opponent×seat×repetition×seed strata, and fault-inclusive denominators. The 96 stage had 192/192 DONE/fault0; the 384 stage had 768/768 DONE/fault0. Workers were 12, recycle16 at 96, recycle64 at 384. Seeds were disjoint (`40700000` and `40710000`).

Artifacts:

- 96 summary SHA: `8509aec24cbadc8cbd3ca9701562fe623b299438d4c2bb2539f03a8846af2d98`
- 96 manifest-complete SHA: `72c09e1bc53ef3fbe4fda2e3a702a6c352624c283542348e3a701b6dded17c99`
- 96 population sidecar SHA: `be021eebb5e7be5b5ac6891ba6c10d3c3938b9e548777d3820546896d3188172`
- 384 summary SHA: `9511184b415242a7a45a49cf67b7bf5a0bb053ccd1a553a64becba6d189803f2`
- 384 manifest-complete SHA: `1adad10fba50263c215f2c68118eb5a35670fb86b586b9e23cafdcc25d44c867`
- 384 population sidecar SHA: `ceb5286db418083a55244b3ee4d5d2128f7fa549969a818c2de381733eaefe8a`

## Implementation / verification

- schedule module: `src/mage_ptcg/meta_specialist/cg_population_alternating_v1.py` SHA `780e2cfaa7b5046b525ab23b8fc47161d7b2df9c8b78d6139d0948c23ce2b85f`
- stage runner: `scripts/run_cg_population_alternating_v1.py` SHA `212b05353242b640d03676edc049b101a8df7b791f1a9cc430163755673c6a14`
- tests: `tests/meta_specialist/test_cg_population_alternating_v1.py` SHA `a78579c6308b10777f416414995e8aea6bcbb2502319e269f6000d815f4aa0ad`
- bounded loop/checkpoint module: `src/mage_ptcg/meta_specialist/cg_population_loop_v1.py` SHA `f2f99fc4524b4b5ef6665aeda42fb2a9c2dd24a2c7fbc22c90e829307c0a9062`
- loop tests: `tests/meta_specialist/test_cg_population_loop_v1.py` SHA `7837f77bf317b73f611878ae167d880b1f27439b71bc624c7a38715ec220578f`
- focused schedule/runner tests: 4 passed; loop/checkpoint tests: 5 passed; py_compile and strict reload passed; docs validation and diff-check are required before handoff.

The loop module is a bounded checkpoint state machine only. It stops on any decision other than exact `POSITIVE_CONTINUE`, records a rollback pointer, and cannot grant training/longrun authority. A dry-run checkpoint was materialized at `runs/final-sprint-autonomous/cg-population-loop-dryrun-20260814-v1/` (checkpoint SHA `dbe625240247489aa867938304ab57c59da863f1c48303daa762cdb7b1d3676b`).

## Decision

The population connection is operational, but the 96-point uplift did not reproduce at 384. Keep P1 as research-only parent and do not promote, submit, train, or start a longer stage. Future work must use a new P1 observed-failure hypothesis and the same workers12 → 96 → 384 gate; existing candidates are not to be blindly retried.
