# Resource-aware META_TRAIN weighted deck halving v2（2026-08-13）

## 結論

直前の負候補2件を再実行せず、全既存 `opponents/**` と過去 final-sprint `deck.csv` のmultiset SHAを除外して新規1-card childを2件選定した。ResourceGovernorの1→2→4→8→12 warm-upは全段DONE/fault 0。META_TRAIN weighted48では両候補がparentを上回ったため、common24-96を実施した。common24では `b92a…` のみparent比+2.083pt、`510f…` は−5.208ptで停止した。384/longrunはこのlaneでは起動していない。

## Manifest / identity

- root: `runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v2-20260813/`
- manifest SHA: `4efca96cba35abadc7d123f50c56911fd5cc522695a8603d05433f9ed18996ab`
- weighted subset SHA: `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- parent: `role-8c8c69dc792c913f`, Tomato native policy fixed

| candidate | mutation | multiset SHA | prior duplicate |
|---|---|---|---|
| `510f5bb05224b5eb…` | `1185 → 8` | `48ce922677fd0c3c975ca8228fb5a2fd741195b348379f81346edb8caa656900` | no |
| `b92a3b55c5fa3485…` | `1185 → 1159` | `f75bfb9fdac9cb1c846c53fc4cffd1487605e3077be93bd0c4715df952e71ec3` | no |

The sealed subset has 12 positive `META_TRAIN` rows and includes Aristo/Harukiharada. `lucifer19_battlecore` and `plamen06_steel` are `META_FINAL` and remain excluded from weight updates. They are retained only as held-out hard-negative diagnostics; no teacher/learning exposure is created.

## Resource warm-up

Artifact: `warmup_telemetry.json`  
SHA: `cd2ea7d3ceef4cae6ce4d4440a7c8757d3aacfed1e920dd8ea0b947a72552253`

| workers | games | faults | throughput (games/s) | memory available before→after (bytes) |
|---:|---:|---:|---:|---:|
| 1 | 4/4 | 0 | 1.3401 | 46746497024→46562136064 |
| 2 | 4/4 | 0 | 1.5909 | 46534037504→46430240768 |
| 4 | 4/4 | 0 | 1.5973 | 46432428032→46076854272 |
| 8 | 4/4 | 0 | 1.7235 | 46079770624→45986754560 |
| 12 | 4/4 | 0 | 1.6068 | 45948157952→46035542016 |

No process was killed; `recycle_games=16`, observed worker restarts 0. Governor state was `normal`, safe worker cap 12, GPU count 1 with no compute process.

## Weighted48

Artifact: `weighted48_summary.json`  
SHA: `f334710692b75d4e4b49ff1d93045d12ff61e2e6c3e15fe1d0a86b8b89c60952`

144 requested games (parent + 2 candidates × 48), all DONE/fault 0. Workers 12; throughput `16.8595 games/s`; available memory `46026969088→45884424192` bytes; RSS `34582528→35004416` bytes.

| arm | W-D-L | weighted score | delta vs parent | seat / identity |
|---|---:|---:|---:|---|
| parent | `27-0-21` | `0.565485` | — | 24+24, unique GID/seed |
| `510f…` | `29-1-18` | `0.611051` | `+4.557pt` | 24+24, unique GID/seed |
| `b92a…` | `36-0-12` | `0.746219` | `+18.073pt` | 24+24, unique GID/seed |

Corrected companion MD `weighted48_summary.md` records `faults=0`; its SHA is `699de6672e5d1f7857efdf9367da0c64ded149d03348a7a90f69d3c606a24b42`.

## common24 evaluation-only guardrail

Artifact: `common24_summary.json`  
SHA: `5d3cdf48b4c38aebf344b345755db4cfb33a0e989456121d7676f3a679d0c76d`

288 requested games (parent + 2 candidates × 96), all DONE/fault 0. Parent was `65/96=67.7083%`.

| arm | W-D-L | score | delta vs parent | gate |
|---|---:|---:|---:|---|
| parent | `65-0-31` | `0.677083` | — | control |
| `510f…` | `60-0-36` | `0.625000` | `−5.208pt` | candidate-only / stop |
| `b92a…` | `67-0-29` | `0.697917` | `+2.083pt` | positive common24; no auto-384 |

Common24 telemetry: workers 12, throughput `16.1950 games/s`, available memory `45865345024→45888806912` bytes, RSS `35663872→36421632` bytes, faults 0, observed restarts 0.

## Gate / authority

- both candidates remain `candidate_only`
- `510f…`: negative at common24; local search branch stopped
- `b92a…`: bounded common24 positive, but no 384/longrun auto-start under this lane’s gate
- `research_only=true`; execution/training/promotion/submission/longrun authority all false
- no production runner, prior artifact, Champion, commit, push, or Kaggle submission was changed

## SHA

After correcting the generated fault display, `weighted48_summary.md` SHA must be recorded as the exact bytes below before handoff. No JSON/ledger bytes are changed by the correction.
