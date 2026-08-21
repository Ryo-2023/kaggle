# Locked-tier touch log

Protocol P6 of `claude_lane_b_validation_design.md`. The locked tier is
`44b6_0113de3b` (50 GT edges) and `44b6_0b24845f` (49 GT edges). Both are
**Kaggle leaderboard test movies** whose ground truth is published under
`train/` — measuring them is a partial leaderboard readout, not validation.

Append one row per evaluation. Never delete rows. A locked-tier number may not
motivate a subsequent change; if it does, the sample is burned and becomes dev.

| UTC timestamp | git SHA | sample_id | method id | reason for the touch | result reported |
|---|---|---|---|---|---|
| 2026-08-21 (pre-protocol) | Codex `panel_runs_0b_*` | `44b6_0b24845f` | all four | four-method detector-fixed race; run before this protocol existed | 39/9/10 → 0.62622 (official), 40/10/9 → 0.62747 (harmonic), 37/8/12 → 0.60938 (mutual), 35/8/14 → 0.58141 (motion) |

## Pre-existing touches (recorded for honesty, predate this protocol)

Every result in `strong_baseline_v1.md`, `detector_fixed_association_race.md` and
`artifacts/detector_fixed_race/dev_full_auto_compact_timed/` was produced by
iterating on `44b6_0113de3b`. Those numbers were used to choose between
association methods, so under P6 that sample is already burned as a validation
signal. It is retained in the locked tier because it is a test movie, not
because it is still uncontaminated.


## Note on the 2026-08-21 touch

`44b6_0b24845f` was raced before this protocol was written, so it is recorded rather than
authorised. It is a leaderboard test movie. Its numbers are now part of the pooled
three-sample evidence in §4.7 of the design doc, which means **the harmonic-vs-official
verdict is partly built on a test movie**. Under P6 that sample is burned as an independent
validation signal. The dev tier (`44b6_0c582fdc`, `44b6_0db75fae`, `44b6_12dfb391`) remains
clean and should carry any future comparison.
