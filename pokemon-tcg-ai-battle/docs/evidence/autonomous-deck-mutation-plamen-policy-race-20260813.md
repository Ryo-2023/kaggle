# Autonomous Meta-Fine-Tuning: plamen deck-fixed policy race

作成日: 2026-08-13 JST

## 結論

plamen06_steel の native policy を、4 block の deck mutation confirmation で
positive だった固定候補 deckへ接続し、`USE_SEARCH` の native/default と
`USE_SEARCH=0` を同一研究用 runnerで比較した。両armは各368局、合計736局で
全て `DONE`、fault 0、271勝97敗だった。

| arm | 局数 | W/D/L | score |
|---|---:|---:|---:|
| native/default | 368 | 271/0/97 | 73.6413% |
| `USE_SEARCH=0` | 368 | 271/0/97 | 73.6413% |

この block では policy knob の差は観測されなかった。これは「二つの policy が
常に同一」と証明する結果ではなく、engine seed setter が無い独立評価であり、
policy armにも異なる base seed を割り当てたため、今回の outcome が同数だった
という bounded result である。評価対象の source は同じ native policy SHAで、
`USE_SEARCH` は候補 module の import 時に実際に切り替わっていることを直接確認
した。従って、現時点では search knob を採用する根拠も、rejectするほどの差もない。

## 閉包・一次artifact

- runner: `scripts/run_deck_mutation_policy_race_v1.py`
- runner SHA: `71bd0c06608c756e2c911a7e6b3ff1e2388acd89c3339abbe9262ed447101926`
- candidate manifest: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json`
- candidate manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- race output: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/`
- `policy_race_summary.json` SHA: `e941429da0252c9dd79f95ba294c7ba68d3eb3e8e9acbe12c71a3a1426a93f65`
- ledger SHA: `9dd25ee1fbf13c3a314c83b51015c4e2f32ac6254d4806f8d20291bbfb725bf7`
- evaluator implementation SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- source policy SHA: `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`

The race uses 23 non-self opponent IDs, both seats, eight repetitions per
opponent-seat, and `max_steps=2000`. The candidate deck SHA is the same for both
arms; only the hash-bound environment/config arm differs. All authority flags remain
false (`training`, `promotion`, `submission`, `longrun`). This is a local research
diagnostic, not a submission or behavior-permission grant.

## 直接 import 検証

The native loader was checked in a fresh Python process for all three configurations:

| env | `USE_SEARCH` | `_SEARCH_OK` | `ENABLE_SEARCH` |
|---|---:|---:|---:|
| `{}` | `True` | `True` | `True` |
| `{"USE_SEARCH":"0"}` | `False` | `True` | `True` |
| `{"USE_SEARCH":"1"}` | `True` | `True` | `True` |

Therefore the equal aggregate score must be interpreted as a no-difference observation
for this independent block, not as evidence that the environment variable was ignored.

## 判定

The positive deck mutation remains the active candidate (`bounded_confirmation_positive`)
because it exceeded its parent native deck in four independent 368-game blocks (pooled
74.8302% vs 72.8261%). The policy race is `policy_no_difference_observed`; no policy
variant is promoted. The candidate still has `research_only=true` and the parent asset's
`local_eval_only`/authority boundary, so `LONGRUN_READY`, training, package submission,
and BestKnown promotion remain blocked until a permissioned, package-closed policy/deck
identity is available.
