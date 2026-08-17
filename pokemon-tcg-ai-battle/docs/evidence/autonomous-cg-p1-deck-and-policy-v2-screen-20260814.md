# Autonomous CG P1 deck/policy v2 screen — 2026-08-14

## 結論

P1 `cg-lethal-target-v1` を固定した新規 deck interaction 3件と、root deckを固定した新規 policy variant 2件を実行した。全評価は workers=12、同一 broad24、両seat、paired opponent×seat×repetition×seed、authority false、DONE/fault0 である。96局で一時的に正差が出た候補も384局で再現しなかったため、P1 parent、Champion、SubmissionEligibleBestKnown、production default、longrun、promotion、training、submissionは変更しない。

## Deck interaction（P1 policy fixed）

| candidate | 96 candidate | 96 control | 96 delta | 384 candidate | 384 control | 判定 |
|---|---:|---:|---:|---:|---:|---|
| Dusk→Petrel | 17/96 | 20/96 | −3.125pt | — | — | STOP |
| Dusk→Hilda | 19/96 | 14/96 | +5.208pt | 62/384 | 80/384 (+1D) | −4.8177pt / STOP |
| Dusk→Bloodmoon | 14/96 | 16/96 | −2.083pt | — | — | STOP |
| Dusk→Explorer | 18/96 | 23/96 | −5.208pt | — | — | STOP |

Candidate package smokeはPetrel 2/2、Hilda 2/2、Bloodmoon 2/2、Explorer 2/2でDONE/fault0/illegal0。P1 policy SHAは全候補で `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、deck SHAはそれぞれ Petrel `d95415b8672f90a34112ff322a315d28a1e7d3d5fffb1135d91ed7510edac83f`、Hilda `779755eae5baa0894616cdf025ab838207b66d84f641e0db86d75c53001fb284`、Bloodmoon `ce7e51d84ab02d85a2ddcafcdd4d1d17fec3692d53f0c78444cd048db929706d`、Explorer `a3755cf993a59d242b14fd5bcac4d0b5dc62d409c545cb202cf3db3f013b945d`。

Summary SHAは Petrel96 `f4f1169b0951cf2f42f6d13c90bd3f7c53c72660c4bf890e78d8856627264152`、Hilda96 `2b67a316b7795887890dfc9284d4c5e2939ed7f557d97bbd70f7dc46b2c2bae5`、Hilda384 `83c344307a5f09293e78441e3194720405b67f57ea4e4c2d5471582f804eac8b`、Bloodmoon96 `3d50c95d99d68f5afe89a2f11b6143d8ab8c58e5042b5c70238588d0625acaf2`、Explorer96 `5b99f59bd54373f0ed66d60627df498a4c590958bcb49298fa195af82a03a652`。

## Policy variants（root deck fixed）

新規 TDD module `cg_p1_policy_candidate_v2.py` は、P1 source SHAを再検証し、public stateの bounded overlayだけを追加する。

- `cg-p1-search-priority-v3`: Mega Lucarioが未観測のsearch contextで Dusk Ball / Premium Power / Poké Pad に +12000。
- `cg-p1-gust-ko-v3`: visible opponent active HPが1–150で、Boss's Ordersがlegal PLAY候補なら +12000。

clean-room package smokeは両候補2/2 DONE/fault0/illegal0。search variantは96で17/96対P1 control12/96、+5.2083pt、candidate seat gap 2.08pt。gust variantは17/96対17/96、0pt。search variantの384 confirmationは62/384対P1 control70/384、−2.0833pt、両arm384 DONE/fault0、seat gap candidate5.21pt/control1.04ptでSTOPした。

Policy source/module SHAは v2 module `50a5bf036362358d515cfccce73be6bde3e2b99a1ea3058a003ca3bb6f5cf835`、test `ccd9a998e3bab82e7362f6586b38accaddd778af430b6dbf44ed116c2e8931b0`、search package manifest `a25119a9e89346b58cc0fa7af6ae1d3b2c07b4d17af508421aaf568d2d213f2e`、gust package manifest `a1f5b04d3326e1e8c1f4358a18da3a4086f865060bb12f586e809fb3051e5ce6`。Search96 summary `c7e15beabb6452ff9c0bfaf46af47c1b1992b56990ad2eaa2ec9ad8ddc6a2cb4`、gust96 summary `88190da9072c87b87535b18009678f47497cf04b2da71d836c40ccba92cd3206`、search384 summary `8df7ae43185fded2eb9292ec6dcdd9c35391cbdbe6dd5ad21271c01a9ddcfc83`。

## 判定と次の gate

この laneでは 96局の単発 positive を昇格根拠にしない。Hilda、search-priorityは384で反転したため同一候補の再実行をしない。次は新しい observed failureまたは新しい package identityを生成し、runtime smoke→workers=12/96→positiveのみ384→再現時768の順に進める。既存 P1/P0 telemetryの strict paired prefix は operation difference 0で候補なしだったため、同じ telemetryの再解析は行わない。active processなし。
