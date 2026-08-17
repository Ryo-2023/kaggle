---
title: Autonomous deck mutation common-protocol confirmation
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 結論

23 opponentで先に得た plamen deck-mutation の positive signal を、native asset
rankingと同じ sealed broad-pool config（24 opponent IDs）へ載せ直した。candidate deckと
parent native deckを各384局、4 independent block、両seat・各opponent 8局で比較した。

| block | mutation candidate | parent native | delta |
|---:|---:|---:|---:|
| 1 | 277/384 = 72.1354% | 268/384 = 69.7917% | +2.3438pt |
| 2 | 274/384 = 71.3542% | 279/384 = 72.6563% | −1.3021pt |
| 3 | 288/384 = 75.0000% | 260/384 = 67.7083% | +7.2917pt |
| 4 | 260/384 = 67.7083% | 282/384 = 73.4375% | −5.7292pt |
| pooled 1536 | 1099/1536 = 71.5495% | 1089/1536 = 70.8984% | +0.6510pt |

全3,072 rowは `DONE`、fault 0、draw 0だった。候補は4 block合算で親nativeを10勝
上回ったが、block2とblock4では反転しており、強い再現性を示す結果ではない。また pooled
候補71.5495%は、同じcommon native rankingでtomatomato_archaludonが1536局で
72.0703%だった点推定を下回る。したがって、mutation candidateを
`EvaluationBestKnown`または`GlobalBestKnown`へ昇格させない。

## 一次artifact

- runner: `scripts/run_deck_mutation_common_protocol_v1.py`
- runner SHA: `82c9caa21c4401996cdc691c2e6807c37140c4041a96c349bb5a42bfbd616ace`
- test: `tests/meta_specialist/test_run_deck_mutation_common_protocol_v1.py`
- test SHA: `6db665b0090745e07e77025987fae989b0f4888923cbf8e4cc60d2344f934a76`
- candidate manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- reference config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`

| block | summary | ledger | evaluator manifest |
|---|---|---|---|
| 1 | `86992be532a77d5d2b0396c7199ca78d49a119804b7b56932db8e65c6c626f1d` | `2aee424bb0dcb841d1b4661c90c15eba013cd218772b8865033005ac67ce4779` | `68b5b11c40a7a289eb5760293969eec9dc9e3e3a53295237427b986fb284cd9c` |
| 2 | `6a2109b1c8921cf65626da42f9e0a8295fe588fe24f3fc92e9086401f0983e87` | `ad62a75a386a3847cdc1d4ac309519fe81917abb47ece37563be51f9f3f47819` | `68b5b11c40a7a289eb5760293969eec9dc9e3e3a53295237427b986fb284cd9c` |
| 3 | `c104d040da4e1205a3e6451545fb3dfdfda8d8072333eb4bc9acb21540feccc6` | `bced1785f9c8b91b94d8ee09a79182992f95cf142baab0cdac5688007ec8d7af` | `68b5b11c40a7a289eb5760293969eec9dc9e3e3a53295237427b986fb284cd9c` |
| 4 | `e8e3078209944540a4b3080055ddd60d765bc66c78be7aaa9ac30bec7b7a9b09` | `98b32aa5eaf3b5c1d5a117edbe91a0c2ca1ba4bd7de439e3bb9a2306e71d54f9` | `68b5b11c40a7a289eb5760293969eec9dc9e3e3a53295237427b986fb284cd9c` |

The runner uses synthetic arm IDs, so `plamen06_steel` remains a valid external opponent
for both candidate and parent; no self-play row is generated. The subject deck and parent
deck share the same native policy SHA, while their deck SHA differs. The engine does not
support a seed setter, so the blocks are independent stratified evaluations rather than
paired game replays.

## 判定

The earlier 23-opponent confirmation (`74.8302%` vs `72.8261%`) remains valid as a
bounded parent-relative diagnostic, but it is not directly comparable to the native
24-reference ranking. The four-block common-protocol rerun gives +0.6510pt pooled and
reverses in blocks 2 and 4. Keep the deck mutation as `candidate_only` / `research_only`, do
not run AWR or longrun from it, and do not alter BestKnown classification. Any next
promotion would require at least two further common-protocol blocks and package/permission
closure; those gates are currently false.
