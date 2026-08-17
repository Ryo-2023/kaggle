# Autonomous Meta-Fine-Tuning: plamen deck mutation confirmation

作成日: 2026-08-13 JST

## 結論

plamen06_steel native policyを固定し、deckだけを1/2-swap候補へ変更する
policy-fixed raceを実行した。8候補の92局screenは736/736 DONE、fault 0だった。
screen首位の2-swap候補
`aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9`
を親native deckと同条件の4独立368局blockで確認した。

| arm | block1 | block2 | block3 | block4 | pooled1472 |
|---|---:|---:|---:|---:|---:|
| mutation candidate | 269/368 = 73.0978% | 271/368 = 73.6413% | 278/368 = 75.5435% | 283/368 + 1 draw = 77.0380% | 1101W/1D/370L = 74.8302% |
| parent native | 255/368 = 69.2935% | 270/368 = 73.3696% | 270/368 = 73.3696% | 277/368 = 75.2717% | 1072W/0D/400L = 72.8261% |

候補のpooled deltaは **+2.0041pt**。blockごとのdeltaは +3.8043pt、+0.2717pt、
+2.1739pt、+1.7663ptで、4 block全てcandidate優位、全arm fault0だった。
これはdeck mutationのbounded positive confirmationであり、元plamen nativeを
候補deckが上回る強い証拠だが、まだsubmission権限・longrun権限・BestKnown昇格を
自動で与えない。

## 閉包と一次artifact

- candidate manifest: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json`
- manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- parent raw deck SHA: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- parent multiset SHA: `d0b36a40a383c262723a60b14a0785f99074cd7816f187a39214f0ec12cc5ae0`
- parent policy SHA: `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`
- candidate deck exact multiset SHA: manifest row内 `deck_multiset_sha256`
- native evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- mutation runner SHA: `7896b58e429e029b43bd60f98b9d1435e4efc008dd84b60e829b437df2306dfc`
- top-confirm helper SHA: `3d8d122f561dfcee64f3e2716d39e461d457c4cf1f6e19655015c094ff89dae1`

Screen summary:

- `runs/final-sprint-autonomous/deck-mutation-plamen-v1/screen-736/candidate_summaries.json`
- SHA `8bb22edf47ddffc70f763aa1969124adb0c30204a389958118906dafbc3deb37`
- W543/L193/fault0、screen首位76/92=82.6087%

Confirmation summaries:

- block1 `.../top-confirm-736/arm_summaries.json`, SHA `b347542057453a78c420fba0ed70a2b3c7d6ddbcd215248cc47093959a4ec7d1`
- block2 `.../top-confirm-736-block2/arm_summaries.json`, SHA `5aeac755dfa9d069dc44f6f0e6cf8dda833022bf35557063593bb9ad96420b43`
- block3 `.../top-confirm-736-block3/arm_summaries.json`, SHA `7f17835b96625a3d5dad66058aee90e28ed8d655680e4a7543bd76c42db21c1e`
- block4 `.../top-confirm-736-block4/arm_summaries.json`, SHA `708a8884548eac424fd68617f2d90b12b28f6e20b62f1b1c55a4dbae1cbd0f79`

各blockはcandidate/native各368局（23 opponent、両seat、各8局）、全てDONE、fault0。
engine seed setterは存在しないため、block間は独立層化であり、game-level pairedとは
呼ばない。authorityはpromotion/training/submission全てfalse。

## 判定

本候補は `candidate_only` から `bounded_confirmation_positive` へ進める。次は、
このdeckを固定してpolicy knob（plamen search budget等）を同じmeta poolで比較する
deck-fixed policy raceである。その後、META_DEV fixed split、native baseline超過、
fault0、seat安定、package closure、rollback/resume、manifest/schedule SHA一致を
longrun gateへ入力する。現時点ではAWR/value学習、longrun、promotion、submissionは
まだ起動していない。
