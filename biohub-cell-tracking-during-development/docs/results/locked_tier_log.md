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
| 2026-08-21 (pre-protocol) | Codex `dev_full_auto_compact_timed` | `44b6_0113de3b` | all four | four-method detector-fixed race on the historical dev movie | 46/2/4 → 0.88379 (official), 48/2/2 → 0.92112 (harmonic), 43/0/7 → 0.85983 (mutual), 42/2/8 → 0.80961 (motion) |

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


## Update 2026-08-21 — the full locked tier has now been raced

Both locked samples have been through a complete four-method race, and both sets of numbers
are inside the pooled five-sample evidence in §4.8 of the design doc. Under P6 **the entire
locked tier is burned as an independent validation signal**. Neither was authorised under
this protocol; both predate it.

Practical consequences:

- The `harmonic_v1` > `official_ilp` verdict (b=29, c=0) includes 3 edges of evidence from
  the two locked movies. It survives without them: dropping both leaves b=26, c=0 across the
  dev tier, still p < 1e-7. **The verdict does not depend on the burned samples**, which is
  the only reason it stands.
- There is no clean held-out sample left locally. The next genuinely independent
  confirmation must come from a movie not yet downloaded — see §7 item 1 of the design doc.
- Anyone reporting a leaderboard expectation should note that `44b6_0113de3b` (0.92112) and
  `44b6_0b24845f` (0.62747) are two of the four leaderboard movies, so those two numbers are
  a direct partial readout, not an estimate.
