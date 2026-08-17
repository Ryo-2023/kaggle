# Resource-aware META_TRAIN weighted deck halving v1（2026-08-13）

## 結論

新規候補2件を固定 parent/policy から生成し、既存 `opponents/**` と過去 final-sprint `deck.csv` の合法 multiset SHAを全走査して重複なしを確認した。ResourceGovernorの `1→2→4` warm-up は全て DONE/fault 0 だったが、META_TRAIN weighted48では両候補がparentを下回ったため、common24-96および384への昇格は停止する。

## Sealed manifest / candidate identity

- manifest: `runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v1-20260813/candidate_manifest.json`
- manifest SHA: `79201901bf3ce2f015cdc10f3479240ea76e3c1bfd934c9937daa00b1dd8193c`
- weighted subset SHA: `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- parent: `role-8c8c69dc792c913f`, Tomato native policy fixed

| candidate | mutation | multiset SHA | novelty |
|---|---|---|---|
| `7e04086d6ae11d35…` | `8 → 1159` | `988db04ce1d2f1199d76c0deee590e9b10ab2c4b9b652959786e4ceef305920f` | proven new |
| `870229eea61831ff…` | `1182 → 1244` | `7ef30a73eeaf869174b907ffe0435f3fc980d37724a8bcf3e569221d7e6d2cca` | proven new |

The weighted subset has 12 positive `META_TRAIN` rows and includes the required Aristo/Harukiharada targets. Lucifer and Plamen are `META_FINAL` in the sealed source manifest; their rows are explicitly excluded from weight updates. Held-out target IDs: `lucifer19_battlecore`, `plamen06_steel`.

## Resource warm-up

Artifact: `runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v1-20260813/warmup_telemetry.json`

- warm-up telemetry SHA: `c1a3708b4a63bbd93c47caec171825e45fcba1144a7a52edabca11c8a557dfc7`
- initial state: `normal`, safe workers `12`, GPU `1` with no compute process
- worker 1: 4/4 DONE, fault 0, 0.8925 games/s
- worker 2: 4/4 DONE, fault 0, 1.6915 games/s
- worker 4: 4/4 DONE, fault 0, 1.5575 games/s
- RSS / available memory were recorded before/after each ramp; no process was killed and no worker restart was needed (`recycle_games=16`).

## Weighted48 result

Artifact: `runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v1-20260813/weighted48_summary.json`

- summary SHA: `106d921f4a705c25b9b870f7893d680a1b69ed300d5b2b854d561bfb0825ede3`
- evaluator: 144 requested games (parent + 2 candidates × 48), all DONE/fault 0
- worker count: 12; throughput `17.1781 games/s`; available memory before/after `46342307840/45630087168` bytes

| arm | W-D-L | weighted score | delta vs parent | identity / seat gate |
|---|---:|---:|---:|---|
| parent | `30-0-18` | `0.621496` | `—` | pass; 24 seat0 + 24 seat1 |
| `7e04086d6ae1` | `28-0-20` | `0.583118` | `−3.838pt` | pass; 24 seat0 + 24 seat1 |
| `870229eea618` | `29-0-19` | `0.587624` | `−3.387pt` | pass; 24 seat0 + 24 seat1 |

Each arm has 48 unique game IDs and 48 unique seeds. The corrected companion artifact `weighted48_summary.md` records `faults=0` for both candidates; its SHA is `7f78e165ea2a011bca4f11fcde1141961b6e8c79c82ef37db32e9a4e41483fa8`.

## Gate / authority

- candidate status: `candidate_only`
- weighted-positive gate: not met for either candidate
- common24-96: **not started**
- 384 / 768 / training / longrun / promotion / submission: **NO-GO**
- `research_only=true`; execution, training, promotion, submission, and longrun authority are all false
- no production runner or existing performance artifact was edited; no commit, push, or Kaggle submission was performed

## Reproduction

The sealed root can be reloaded without regenerating candidates:

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_resource_aware_weighted_deck_halving_v1.py \
  --run-existing \
  --output runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v1-20260813
```

The destination is non-empty and no-clobber, so this command is intentionally expected to refuse a second publication. Use a new research-only root for any future run; this negative candidate pair must not be silently promoted or reused as a positive screen.
