# plamen deck-mutation / alternating optimizer / longrun 接続監査

作成日: 2026-08-13 JST

## 結論

plamen06_steel を親に生成した 2-swap deck candidate は、親 native policy を固定した
4 independent block の bounded confirmation では明確な正の結果を示した。しかし、
この結果を `alternating_meta_optimizer_v1` の状態から `LONGRUN_READY` へ接続するのは
現時点で **NO-GO** とする。

理由は性能そのものではなく、評価 split の境界、利用権限、提出 package 閉包、policy
race の情報量、longrun checkpoint の未成立である。candidate は
`bounded_confirmation_positive` として研究上保持するが、BestKnown 昇格、teacher/
behavior source、longrun、submission には進めない。

## 監査対象と一次 artifact

### Deck mutation candidate

| 項目 | 値 |
|---|---|
| candidate ID | `aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9` |
| candidate manifest | `runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json` |
| candidate manifest SHA-256 | `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b` |
| candidate status | `candidate_only`（確認後の解釈: `bounded_confirmation_positive`） |
| candidate deck CSV | `runs/final-sprint-autonomous/deck-mutation-plamen-v1/aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9/deck.csv` |
| candidate deck CSV SHA-256 | `9f413dd4423c2a90f40fa25753f01a610607fa1e0be8c54a9aee50b1285639e7` |
| candidate exact multiset SHA-256 | `a9b45c1d90672bf46ad67bc61e4f8a7382a44e5745d27f1b823495655909f227` |
| parent raw deck SHA-256 | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` |
| parent multiset SHA-256 | `d0b36a40a383c262723a60b14a0785f99074cd7816f187a39214f0ec12cc5ae0` |
| parent/native policy SHA-256 | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` |
| candidate mutation | remove cards `[57, 1185]`, add `[1115, 345]`, 2-swap |
| candidate generator source SHA-256 | `01c0a74f6b122904d417958ef3413c3ab835b840387f7b164fe3364ff70becf6` |
| candidate source manifest SHA-256 | `895d76e3ea52b0ea99485ffe3107e9d336aabc040d4f8f54da3f13934a9aa911` |

The candidate manifest and generator enforce 60 cards, core signature, exact multiset
identity, and `training/promotion/submission/execute=false`. This is structural legality
evidence only; it is not a permission or package-closure grant.

### Deck confirmation

Runner and evaluator identities:

- native-pilot runner: `scripts/run_deck_mutation_native_pilot_v1.py`, SHA
  `7896b58e429e029b43bd60f98b9d1435e4efc008dd84b60e829b437df2306dfc`
- top-confirm helper: `scripts/run_deck_mutation_top_confirm_v1.py`, SHA
  `3d8d122f561dfcee64f3e2716d39e461d457c4cf1f6e19655015c094ff89dae1`
- evaluator implementation SHA-256:
  `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- screen summary SHA-256:
  `8bb22edf47ddffc70f763aa1969124adb0c30204a389958118906dafbc3deb37`

The screen was 8 candidates × 92 games = 736 games, all `DONE`, fault 0. The selected
candidate was 76/92 (82.6087%) in that screen. The parent native arm and the candidate were
then evaluated as 368 games per arm in each of four independent blocks:

| block | candidate | parent native | delta |
|---|---:|---:|---:|
| 1 | 269/368 = 73.0978% | 255/368 = 69.2935% | +3.8043pt |
| 2 | 271/368 = 73.6413% | 270/368 = 73.3696% | +0.2717pt |
| 3 | 278/368 = 75.5435% | 270/368 = 73.3696% | +2.1739pt |
| 4 | 283W/1D/368 = 77.0380% | 277/368 = 75.2717% | +1.7663pt |
| pooled 1472 | 1101W/1D = 74.8302% | 1072W = 72.8261% | **+2.0041pt** |

All four blocks and both arms were fault 0. The block artifact SHA-256 values are:

- block 1: `b347542057453a78c420fba0ed70a2b3c7d6ddbcd215248cc47093959a4ec7d1`
- block 2: `5aeac755dfa9d069dc44f6f0e6cf8dda833022bf35557063593bb9ad96420b43`
- block 3: `7f17835b96625a3d5dad66058aee90e28ed8d655680e4a7543bd76c42db21c1e`
- block 4: `708a8884548eac424fd68617f2d90b12b28f6e20b62f1b1c55a4dbae1cbd0f79`

The four blocks are independent layers because the CABT engine has no seed setter; they are
not game-level paired observations.

## Split audit: positive result is not META_DEV evidence

The confirmation pool contains 23 opponent IDs. Compared with the sealed autonomous meta
manifest (`e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`), the pool has:

- `META_DEV`: **0 IDs**
- `META_FINAL`: `aristophanivan_multiply`, `dashimaki360_crustlecounter`,
  `lucifer19_battlecore` (**3 IDs**)
- `META_TRAIN`: 20 IDs

Therefore the four positive blocks are valid bounded confirmation against the recorded
23-opponent arena, but cannot be passed to the current longrun gate as `META_DEV` blocks.
Three held-out `META_FINAL` opponents were consumed during candidate selection/confirmation;
using the result as a clean `META_DEV` gate would contaminate the final split. A new isolated
META_DEV evaluation would be required before any gate decision. This audit does not rerun it.

The sealed manifest and evaluation schedule remain:

- meta manifest SHA-256: `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
- meta schedule SHA-256: `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`

## Policy-fixed / deck-fixed race audit

The fixed candidate deck was compared under native/default and `USE_SEARCH=0` policy
configuration:

- race runner: `scripts/run_deck_mutation_policy_race_v1.py`, SHA
  `71bd0c06608c756e2c911a7e6b3ff1e2388acd89c3339abbe9262ed447101926`
- race summary: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/policy_race_summary.json`
- race summary SHA-256: `e941429da0252c9dd79f95ba294c7ba68d3eb3e8e9acbe12c71a3a1426a93f65`
- race output manifest SHA-256: `f7fe3bb46d310f9eaa2763553168a8481e5986c154b5a1be7208ce330dd331b9`
- policy race ledger SHA-256: `d5a0d035d7ac4ce3e70d63e05011195f75a2e5bab2d8fcfe4df564aa2164e5ae`

Both arms were 368 games, 271W/97L, fault 0 (73.6413%). This is
`policy_no_difference_observed`, not a policy improvement and not proof that both policies
are globally identical. No policy candidate is promoted from this race.

## Permission and package blockers

The census row for the parent `plamen06_steel` has:

- `usage_boundary=local_eval_only`
- `training_usable=no_not_authorized_or_not_evidenced`
- `smoke_ok=true`
- parent source SHA-256 `167c155b93f9290be6d5b38a9b434b49a38850faeddbde1b92b9abc797d76f5f`

The mutation candidate inherits this boundary. Its manifest explicitly keeps
`training_allowed=false`, `promotion_allowed=false`, and `submission_allowed=false`. The
candidate directory contains a research deck CSV and evaluation records, but no sealed
submission package closure, entrypoint/license audit, or permission artifact authorizing
this external native policy. `submissions/` therefore cannot be populated from this pair.

The positive CABT result proves only that this deck/policy pair ran legally in the research
arena. It does not prove that the pair is packageable, submission-eligible, or permissible as
a behavior policy for on-policy/AWR collection.

## Alternating optimizer / longrun gate connection

The optimizer contract is research-only and independently hash-binds candidate deck SHA,
policy config SHA, manifest SHA, schedule SHA, and native baseline:

- `alternating_meta_optimizer_v1.py` SHA-256:
  `15687de5f271e3323297464b33add70a7c250308812eaccff1050b70384b1d47`
- optimizer dry-run used the real manifest/schedule with `POLICY_FIXED_SHORT`, stage 96,
  `launch_allowed=false`, all authorities false.
- longrun contract module SHA-256:
  `cfb4a798b602546697c2bb7f846ee43c094e96a80c1ee853882e1b1680302c0e`

The positive deck result may be stored as a candidate state for a later isolated race, but it
does not satisfy the start gate:

1. current confirmation opponents are not `META_DEV` and include `META_FINAL`;
2. parent/candidate permission is `local_eval_only`, with no training or submission grant;
3. policy race has no observed improvement;
4. package closure is absent;
5. rollback checkpoint and resume lineage for this candidate are absent;
6. manifest/schedule-bound longrun gate evidence has not been produced for this candidate.

## Final decision

**Decision: NO-GO for `LONGRUN_READY` and `LONGRUN_STARTED`.**

Allowed next step is a new research-only candidate record and, if permission/package scope is
resolved, an isolated evaluation using only `META_DEV` opponents with the candidate deck and
native baseline in every block. Until that evidence and package closure exist, do not treat
the positive 1472-game result as a submission candidate, teacher source, behavior policy, or
longrun authorization. No CABT, training, longrun, package submission, Champion change, or
Kaggle submission was started for this audit.
