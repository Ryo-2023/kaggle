# Tomato native Team Rocket's Petrel support screen（2026-08-14）

## 結論

既存119 multiset と opponents 全deckを除外した未評価 Supporter surface を2件 screenした。`Team Rocket's Petrel`（1219）は META_TRAIN weighted48 で parent を上回らず、両候補とも `candidate-only / NO-GO`。common24、384、longrun、submission は起動していない。

| arm | W-D-L / 48 | weighted score | parent差 |
|---|---:|---:|---:|
| Tomato parent | 35-0-13 | 0.715549142 | — |
| Boss's Orders 1182→Petrel 1219 | 31-0-17 | 0.647951598 | −6.7598pt |
| Lillie's Determination 1227→Petrel 1219 | 34-0-14 | 0.701852174 | −1.3697pt |

## Artifact SHA-256

- root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1219-support-weighted-v1-20260814/`
- wrapper: `scripts/run_resource_aware_tomato_overlay_1219_support_weighted_v1.py` — `5256ce79aa8bdca77a6636d5af0b346deb70606775b9df1f6ac8cd787a32ef3e`
- candidate manifest: `cd16c14bf988a019945777567914f72de0f1419d300111687fcb70445d1624ab`
- weighted summary JSON: `d4982c677b822ad86a2e1a0abdfa571217440ae2d0de1f0d48ec695ff2066f28`
- weighted summary MD: `86ac6032932807262610400a7721a23e18eb87deeda66d996183803a81ad3460`
- final summary: `509e34f4a9296ecfc655b42728827a5a5cf11e8e343c9515a2d7f85aa5cf6d68`
- warmup telemetry: `dcb373430ed3dac878400a3177ccc4039a6645fc1562a518e1661bc67d63f2df`
- candidate deck SHAs: 1182→1219 `7e1429d75d5a5d13d61f58309f3c89edf228193e98e45f8e4c7efd5eaa62261a`; 1227→1219 `d54ab216f2f9e040e19bbf1dc44c438594952cd37d749e9e5b74351ca2c6235b`

## Integrity／authority

All 144 games were `DONE` with fault 0; each arm had seat24/24 and opponent12×4. Candidate/parent paired `(opponent, seat, repetition)` keys, seeds, game IDs, and denominators passed. ResourceGovernor admitted workers12, recycle16; warmup ramp 1/2/4/8/12 was fault-free with no kill/restart. Research-only authority is true; execution/training/promotion/submission/longrun are false. Production code and prior roots were not modified.

