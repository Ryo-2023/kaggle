---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-14
---

# Outcome-only alternating runtime 実候補接続 — Tomato policy × a73 deck / 96局

## 結論

新規 research-only alternating runtime を、実在する Tomato native policy と未固定の a73 deck candidate に接続し、candidate/control の2 armを同一 evaluator block で実行した。192局（各arm 96局）は全て DONE、fault 0、draw 0 だったが、candidate は 61/96 (63.5417%)、native control は 65/96 (67.7083%)で、candidate delta は **−4.1667ポイント**だった。seat 0 は同率、seat 1 は candidate が 56.25% で control 64.5833%を下回ったため、runtime の判定は `NOT_PROMOTABLE`、384局への自動昇格は行わない。

これは alternating runtime の実接続・同一 strata・workers=12 実行が成立した証拠であり、candidate の改善証拠ではない。既存 Tomato policy、root deck、pool、production evaluator、既存 run root は変更していない。training、promotion、longrun、submission、commit、pushは行っていない。

## 固定した入力

| 項目 | 内容 |
|---|---|
| policy | `opponents/tomatomato_archaludon/main.py` / SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` |
| candidate deck | a73 (`1245 -> 1152`) / SHA `90299c7daf9efca7f86248eb18f1c031131f8bc113e7256ca19873245252cc69` |
| control deck | `opponents/tomatomato_archaludon/deck.csv` / SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| config | empty env/biases, `min_score_gain=0`; config SHA `b9e36a08b3bfdccb56c22dbfce53979acb3b12c2ee40586406e06207888cb206` |
| pool | `opponents/pool_manifest.json` 経由、broad 24 IDs |
| stage | `POLICY_FIXED_SHORT`, 96局/arm、24 opponents × 2 seats × 2 repetitions |
| seed | candidate/control共通、`25000000..25000095` |
| workers | `12`, `worker_recycle_games=16` |
| authority | research-only; execute/training/promotion/submission/longrun は全て false |

## 実測結果

| arm | W-D-L-F | score | seat 0 | seat 1 |
|---|---:|---:|---:|---:|
| candidate `alternating-tomato-a73` | 61-0-35-0 | 63.5417% | 34/48 (70.8333%) | 27/48 (56.25%) |
| native control `alternating-tomato-parent` | 65-0-31-0 | 67.7083% | 34/48 (70.8333%) | 31/48 (64.5833%) |
| delta | −4 wins | −4.1667pt | 0pt | −8.3333pt |

同一 `(opponent_id, seat, repetition, seed)` strata を candidate/control で共有し、arm内 GID、seed universe、pairing、fault-inclusive denominatorを保持した。runtime summaryの判定は `NOT_PROMOTABLE`、`next_stage_games=null`。

## 成果物とSHA

- wrapper: `scripts/run_outcome_only_alternating_tomato_a73_v1.py` — SHA `775c8e7b199a17d1b9da12f40668c287fcc7050e0e5e017e3679a1fa1cb11347`
- runtime: `src/mage_ptcg/meta_specialist/outcome_only_alternating_runtime_v1.py` — SHA `9a06ba77a9e2b16ecced051b32463afd9c233139d41e4e49c887f712ffb99bda`
- runtime tests: `tests/meta_specialist/test_outcome_only_alternating_runtime_v1.py` — SHA `ac5260170f0493a3d0f88d28aa0f280b2d457c52f4f7691589f8fe93619c81ec`
- run root: `runs/final-sprint-autonomous/alternating-tomato-a73-96-20260814-v2/`
- stage manifest: SHA `2d255e15c1d54b135415080a4dfcf3decdff5a3f01922c3411a7ffd223b667d8`
- stage summary: SHA `26b160f5f5f4ee833a42568665cc1b4c3b259561d4d1ddd315902d593b9d3443`
- evaluator manifest: SHA `12f7c7fb941442beb18a18354de1a609367c9a6f1bf3869197c093b6525ffe49`
- evaluator ledger: SHA `0739fd3b78f96058e51048c629bcdf127d8fc7030cc38c6e46c52e776d13aefb`
- evaluator summary: SHA `a3a16601f0b4d510fe1a4ae4d27173be8155aca9c83b2b6c858b4149c0d90f23`

## 解釈と次のゲート

この候補は fresh 96 で明確に負けたため、同じ a73 × Tomato policy の384/768/longrunは起動しない。今回の主成果は、既存の synthetic-only fixture ではなく、native policy/deck と実 pool へ alternating runtime を接続し、同一 block・同一 strata・workers=12で再現可能に実行できることを確認した点にある。

次の候補を選ぶ場合は、既存 hard-negative と deck multiset 重複を除外した別候補を1件だけ選び、同じ `POLICY_FIXED_SHORT` 96局から開始する。positiveでも直ちに昇格せず、candidate/controlのseat差、opponent support、fault 0、paired seed一致を確認したうえで384へ進める。native action labels、teacher labels、private stateはこの経路へ投入しない。

