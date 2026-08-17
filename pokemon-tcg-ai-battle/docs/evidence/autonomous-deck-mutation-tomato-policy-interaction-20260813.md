# Tomato policy × Plamen mutation deck interaction (384)

## 結論

`3f64513…` mutation deckへ `tomatomato_archaludon` の native policy を載せた candidate と、Tomato native deck + native policy control を同じ common24 protocol で比較した。candidate は `264/384 = 68.7500%`、Tomato native control は `260/384 = 67.7083%`、差は `+4 wins / +1.0417pt`、両arm fault 0 だった。これは policy/deck interaction の小さな正差であるが、事前gateの「約+3pt以上」には達しないため、second block（768局）は起動せず candidate-only で停止する。

Plamen native policyをmutation deckへ載せた同じ候補は、Tomato direct control 384局で `274/384` vs `275/384 + draw`（−0.3906pt）だった。従って今回の +1.04pt は Tomato policy による bounded interaction signal であり、BestKnown昇格、longrun、submissionを意味しない。

## Artifact / SHA

- run root: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/common24-tomato-policy-mutant-384/`
- summary: `global_control_summary.json`
- summary SHA: `ba6486331ec8171fa9848cd22e792b55496726b2b7e4efd5d1ba7cf897b41e4a`
- candidate manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- mutation deck CSV SHA: `faa7d275f5c7a963d7c7c2ffc3bb3dc8c04c731204ffb07eb58696cba152aa20`
- mutation deck multiset SHA: `650c413259e60ae4fa7c4e9eb12acd2c20a03e70ffa10d0fc36d8e348eccdd3d`
- Tomato native policy SHA (candidate and control): `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- Tomato native deck SHA (control): `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- reference config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`

## Protocol integrity

- 24 opponent IDs × 2 seats × 8 repetitions = 384 games per arm.
- Candidate and Tomato control share seeds `14600000..14600383` exactly.
- 768 ledger rows and 768 unique game IDs; all status `DONE`.
- Both arms cover 24 opponents and 192 games per seat.
- `faults=0`, `draws=0`, authority `(promotion, training, submission)=(false,false,false)` and `research_only=true` for every row.
- Candidate policy asset is explicitly `tomatomato_archaludon`; candidate deck remains mutation `3f6451…`. Production `main.py`, evaluator, Champion, package permission, and Kaggle submission were not changed.

## Decision

No Tomato interaction second block is justified by the pre-registered +3pt gate. Keep the artifact as a bounded interaction signal only. It is not `EvaluationBestKnown`, `BestKnownArchaludon`, `GlobalBestKnown`, `TrainingEligibleBestKnown`, or `SubmissionEligibleBestKnown`; package/permission remains fail-closed.
