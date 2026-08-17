# Plamen deck mutation `3f6451…` Tomato direct control (384)

## 結論

Plamen native policy を固定した mutation candidate `3f64513bf1c069b7e14c889b2c94150f7e0dd58697e9004c937871344668719f` と、現行 Archaludon BestKnown provisional の `tomatomato_archaludon` native pair を、同一 common24 reference / seat / repetition / seed schedule で直接比較した。候補は `274/384 = 71.3542%`、Tomato native は `275/384 = 71.6146%`（draw 1、score-rate は `71.7448%`）で、候補は Tomato に対して `-1 win / -0.3906pt`（score-rate比較）だった。fault は両arm 0、authority は全falseである。

この結果により候補は native Plamen parent との pooled1536 では `+1.6927pt` だったが、Global/Tomato BestKnown を超えていない。Tomato に対する第2 block（768局）は起動せず、候補は candidate-only で停止する。

## Artifact / SHA

- run root: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/common24-tomato-direct-384/`
- summary: `global_control_summary.json`
- summary SHA: `f5a8f077f111881b606821fc312f2aa663fd57394f10724bb131b5e4f87429ba`
- candidate manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- candidate deck CSV SHA: `faa7d275f5c7a963d7c7c2ffc3bb3dc8c04c731204ffb07eb58696cba152aa20`
- candidate deck multiset SHA: `650c413259e60ae4fa7c4e9eb12acd2c20a03e70ffa10d0fc36d8e348eccdd3d`
- candidate Plamen native policy SHA: `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`
- Tomato native policy/deck identities are recorded in the summary metadata.
- reference config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`

## Protocol integrity

- 24 opponent IDs × 2 seats × 8 repetitions = 384 games per arm.
- Candidate and Tomato arms share the exact 384-seed schedule (`14400000..14400383`).
- 768 ledger rows and 768 unique game IDs.
- Both arms cover all 24 opponent IDs and 192 games per seat.
- `faults=0`, all statuses `DONE`.
- `promotion_authority=false`, `training_authority=false`, `submission_authority=false`, `research_only=true` for every row.
- The run uses the new research-only `scripts/run_deck_mutation_global_control_v1.py`; production `main.py` and evaluator were not edited.

## Decision / next gate

No second Tomato block is justified because the first 384局 is not positive. The mutation remains useful as a behavior-diversity / local research artifact, but it is not `BestKnownArchaludon`, `GlobalBestKnown`, or a submission candidate. Package permission remains a separate fail-closed NO-GO.
