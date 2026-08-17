# 95cc native-policy META_TRAIN neighborhood screen (2026-08-14)

## 結論

既存の強い研究資産である Tomato native policy（local evaluation only）と、先行確認済みの95cc deckを親に固定し、META_TRAIN上位12 opponentの重み付き頻度からnovelな1-card近傍を2件生成した。両候補はruntime smokeとweighted48を通過したが、common24では親と完全同率（67/96）となったため、384確認・768・longrun・promotion・submissionへは進めない。候補はcandidate-onlyであり、提出互換Championは変更しない。

## 固定した資産と境界

- 親deck: `runs/final-sprint-autonomous/deck-mutation-weighted-halving-v1-20260813/candidates/95cc2c77a31de5dc3a79b9cdffd5a7f81e0d4e42b05734ad36da453facc45145/deck.csv`
- 親deck SHA: `fa66263d4aa86e9d117629e3fb49b06ad7fc529f858ca4d64f2723eb156f17d3`
- 親deck multiset SHA: `cd85129919e02033a93d28543d00b705d75391487a1a0c0c050376f2abfc6961`
- 親policy: `opponents/tomatomato_archaludon/main.py`
- 親policy SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- native policy/deckのusage boundaryは`local_eval_only`。全authority flagはfalse、training/behavior/teacher/promotion/submission/longrunは未使用。
- META_TRAIN subsetは既存のsealed subsetを再利用し、candidate generationとevaluation weightingだけに使用した。既存deck multisetと重複しないことを再検証した。

## 候補

| candidate | mutation（card id） | deck SHA | multiset SHA |
|---|---|---|---|
| `7dd0a11d711376d927c7bde01428d8ee715ed9460da1ddee75a273f8a51c9912` | `8→6` | `134b4bf632524b6b272c4c54645c036f40c166c4c67450e0a8ea0782801bfd30` | `62c50c4d707721233efe915f88afc9edaa133a20ea5869dfd312d24b12b07af3` |
| `49e6c13cdbf9fcca56ac52bc6b972842ad68d650159a70ac800dafc62554c216` | `8→3` | `a1b2070cbec005342d3d1a04159c24e2f77f4508c04c2280b0582931ed1bf21c` | `35f0f04d6b743b342e070c2eb3d79469ac1774b8f7c727a91984d3babbafc1f7` |

All three arms were 60-card/known-vocabulary legal and used the same native policy identity. Runtime smoke used the same native engine with the parent and both candidates, two games per arm (both seats), and all six games were `DONE`/fault0.

## 実測

### weighted48

Fresh root: `runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-v1-20260814`

Workers=12, recycle=16, 12 META_TRAIN opponent IDs × 2 seats × 2 repetitions = 48 games/arm, 144 games total, all `DONE`/fault0. ResourceGovernor was `normal`, admitted 12 workers, and measured 18.1044 games/s.

| arm | W-D-L | weighted META score | delta vs parent |
|---|---:|---:|---:|
| parent | 31-0-17 | 0.6420337 | — |
| `8→6` | 32-0-16 | 0.6601390 | +1.8105pt |
| `8→3` | 34-0-14 | 0.7059470 | +6.3913pt |

### common24

Fresh root: `runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-common24-v1-20260814`

All 24 broad opponent IDs × 2 seats × 2 repetitions = 96 games/arm, 288 games total, all `DONE`/fault0. Candidate/control game strata, seeds, seat counts, and global game IDs passed their gates. All three arms were exactly 67-0-29 = 69.7917%; both candidate deltas were 0.0pt. Therefore no 384 confirmation was started.

## 一次artifact

- weighted manifest SHA `0ef180944aa0ec12c7cdff3021b5c97f976f6406e6554a99e3dfd5c750da13ee`
- weighted summary SHA `56d76e0f7b2c67dcf53cdb5d77cfbdab2afdb9f68ccf77f8a4c106617b108777`
- weighted summary Markdown SHA `85cf622678338a43a9f530afba314d28371a569c944b05c75aaa4e8dab3a7ac5`
- runtime smoke SHA `4c911bf0f35b1275b7799e401d66efc55ba9b362ed170c72b7a0267ea99feb67`
- common24 summary SHA `4f896ba94ed070342ac3bf772158d0ab0ffc60df451ec9da3b3efac112ed409b`
- common24 summary Markdown SHA `5edc7565fa7b8c42a9e64ff1786475c63d0035f22f9777566a1650b0c399b1cf`
- wrapper `scripts/run_meta_weighted_95cc_neighborhood_v1.py` SHA `42beaa6cef30f265961f360ff65fdc903d464981582227f15b02d6a8d4ad260f`
- common24 wrapper `scripts/run_meta_weighted_95cc_common24_v1.py` SHA `77bcdb5f705dcae5cd6d93e1cd8503b1b08da49b33ca25e1dade4a3b95a0eff6`
- focused test SHAs `25a6892e67f1e1ccdfe1cbdaa6b4be60c5ce6af69f66086757cf154f24a3896b` / `da52e40c3c6800f5f3ec0007a71a7fb16f1438b4fe252fb2364f7584c4b96ae5`

## 判定

weighted48の短期陽性はcommon24で再現しなかったため、この近傍はhard-negative寄りのcandidate-onlyとして保全する。同じ95cc候補・同じseedのblind retry、384/768/longrun、training、promotion、submissionは行わない。次の実性能ループは、別のnovel deck/policy surfaceをruntime smoke後にworkers=12でweighted48へ投入するか、permissioned catalog/portable closureが成立した別policy laneへ進む。

`py_compile`、focused tests（5 passed）、docs validator（13 canonical documents）、`git diff --check`を通過した。production `main.py`/agents、既存Champion、既存artifactは変更していない。

## v2 continuation (same parent, new generator/seed, 2026-08-14)

v1の候補を再実行せず、同じ95cc親からgenerator seed `23673000`で別のnovel候補2件を生成した。runtime smokeは親＋両候補の6局を全てDONE/fault0で通過した。weighted48（base seed `23674000`、workers12/recycle16、144局、全DONE/fault0）は親35-0-13、`1097→6`（candidate `95334313c78469d3373bf950070003202da9c577245faadc8027ba1996e6c083`）37-0-11でweighted 0.7795994（+3.2905pt）、対照候補はweighted −6.7798ptとなった。

positiveの`1097→6`だけをcommon24（base seed `23675000`、96局、workers12/recycle16）へ進めた。親69/96（71.875%）に対し候補67/96（69.7917%、−2.0833pt）、全192局DONE/fault0、paired/seed/GID/seat gate PASS。v2も384/768/longrun・training・promotion・submissionへは進めず、95cc近傍系列をcandidate-only/hard-negativeとして閉じた。

v2 artifacts: weighted root `runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-v2-20260814`（manifest `361062be92a6aaabe900c6406d3457f3f482da294c2297f043ce4fd94d558a5e`, summary `4461b753f249cdb39bc538e74538aa5148bb12402b9b75bd3f70eea131456b86`, runtime smoke `3617255092a9e661664924b7a1931bafa576f9882547f4f3d51922a9a2180414`）、common24 root `runs/final-sprint-autonomous/meta-weighted-95cc-neighborhood-v2-common24-20260814`（summary `70729ca6e932f87b78ab595a82fdc8ba4bcb4fef12fc1d4ffa02301232a18da3`）。
