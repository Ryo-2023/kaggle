# Locked-tier touch log

Protocol P6 of `claude_lane_b_validation_design.md`. The locked tier is
`44b6_0113de3b` (50 GT edges) and `44b6_0b24845f` (49 GT edges). Both are
**Kaggle leaderboard test movies** whose ground truth is published under
`train/` — measuring them is a partial leaderboard readout, not validation.

Append one row per evaluation. Never delete rows. A locked-tier number may not
motivate a subsequent change; if it does, the sample is burned and becomes dev.

| UTC timestamp | git SHA | sample_id | method id | reason for the touch | result reported |
|---|---|---|---|---|---|
| _(no touches logged under this protocol yet)_ | | | | | |

## Pre-existing touches (recorded for honesty, predate this protocol)

Every result in `strong_baseline_v1.md`, `detector_fixed_association_race.md` and
`artifacts/detector_fixed_race/dev_full_auto_compact_timed/` was produced by
iterating on `44b6_0113de3b`. Those numbers were used to choose between
association methods, so under P6 that sample is already burned as a validation
signal. It is retained in the locked tier because it is a test movie, not
because it is still uncontaminated.
