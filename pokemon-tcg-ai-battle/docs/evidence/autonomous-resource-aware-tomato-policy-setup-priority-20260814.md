# Autonomous resource-aware Tomato setup-active priority — 2026-08-14

## 結論

Tomato native parent policyの研究用コピーで、setup時のactive候補優先順位だけを変更した2候補をscreenした。weighted48ではDuraludon-firstがparent比+20.052pt、Relicanth-firstが+4.577ptとpositiveだったため、両候補を全24 common24 evaluation-only guardrailへ拡張した。しかしcommon24ではDuraludon-firstが−4.167pt、Relicanth-firstが−9.375ptへ反転した。全288局はDONE/fault0、draw0、seat48/48、opponent各4局、arm内GID/seed unique、parentとのpaired seed/strata一致だった。局所weighted positiveは再現しなかったため、両候補をcandidate-only/NO-GOとして停止し、384/768/longrun/training/promotion/submissionは起動していない。

## 変更範囲とpermission

- parent policy: `opponents/tomatomato_archaludon/main.py`
- parent policy SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- parent deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- 変更対象は研究用コピー内のsealed `_SETUP_ACTIVE_PRIORITY` blockのみ
- candidate IDs: `setup-duraludon-first-v1`, `setup-relicanth-first-v1`
- Duraludon-first: `CINDERACE=20000, DURALUDON=100000, RELICANTH=5000`
- Relicanth-first: `CINDERACE=20000, DURALUDON=5000, RELICANTH=100000`
- unknown key、非整数、範囲外、sealed blockの複数/未置換はfail-closed
- native action/teacher label/logit/private fieldは使用していない
- authority: `research_only=true`、execution/training/promotion/longrun/submissionは全false

## Weighted48 screen

同一META_TRAIN weighted subsetの12 opponentを、各arm 12×両seat×repetition2=48局で比較した。weighted scoreはopponent別WDL rateを既存META_TRAIN weightで平均した値である。

| arm | W-D-L | raw score | weighted meta score | parent差 |
|---|---:|---:|---:|---:|
| parent | 27-0-21 | 56.25% | 0.557124342 | — |
| Duraludon-first | 36-0-12 | 75.00% | 0.757646623 | +20.0522pt |
| Relicanth-first | 29-0-19 | 60.4167% | 0.602890066 | +4.5766pt |

3 arm計144局は全てDONE/fault0/draw0、seat24/24、opponent各4、paired key/seed一致、arm内GID/seed uniqueだった。ResourceGovernorはnormal、workers12、recycle16、weighted wall約9.35秒、throughput約15.40 games/s、restart0、kill0。weighted positiveだけでは昇格せず、全24 guardrailへ進めた。

## Common24 guardrail

common24はperformance objectiveではなく、全24 opponent・両seat・repetition2の安全確認である。broad configの24 IDsを使い、META_TRAIN重みは使わず、heldout4（`aristophanivan_multiply`, `dashimaki360_crustlecounter`, `lucifer19_battlecore`, `plamen06_steel`）は評価のみとした。`heldout_training_exposure=0`をmanifest/summaryに固定した。

| arm | W-D-L / 96 | score | parent差 |
|---|---:|---:|---:|
| parent | 75-0-21 | 78.1250% | — |
| Duraludon-first | 71-0-25 | 73.9583% | −4.1667pt |
| Relicanth-first | 66-0-30 | 68.7500% | −9.3750pt |

全288局はDONE/fault0/draw0、各arm seat48/48、24 opponent×4、arm内GID/seed unique、parentとのpaired seed/strata一致。ResourceGovernorはnormal、workers12、recycle16、wall約18.29秒、throughput約15.75 games/s、restart0、kill0。heldout4のtraining exposureは0である。

## 初回実行の失敗と採用root

最初のweighted起動はgame materialize時にwrapperが別surface moduleの非公開`replace_game`を誤参照し、`AttributeError`で実評価前に停止した。失敗をfocused regression testで再現し、local metadata rebindingへ最小修正した。初回partial rootは上書きせず保全し、性能結果には採用していない。修正後のfresh retry rootだけを正典とした。

## 正典artifact

### Weighted retry

Root: `runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-weighted-v1-retry-20260814/`

- candidate manifest: `7ea9be120b62698e8000289cc8f3a8f399a4880d781666ca2f46690a5718114b`
- warmup telemetry: `bf8cc614ec3af93cf4b916e344377f6b2f77774aa45a5a13e2a23b137ce5da1b`
- weighted summary JSON: `f13061be0612ede24cab97326bc0a5b645f7edf9d8e10766b2d0d3aac4f3448c`
- weighted summary MD: `c51e6a7ddb807f273846e220f8a38e9d2fc0751caf3f18f9ac5ccce730803a2d`
- final summary: `d5049cdce4a2d4e03865642108e35564e6c7e7a7609dbe514d435cd582c2c325`

### Common24

Root: `runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-common24-v1-20260814/`

- common24 manifest: `ecfb02332052026b9cbfa56914f73da2e372861add2abdac513a21f833d5d88c`
- warmup telemetry: `c13b901c61e3157951d016b0d8480a22cfee3a3b97f7896fca777ab257253ef2`
- common24 summary JSON: `a6d44ae164d31c7d9fad423e750ec9b2c3f3af252300dbd80f7be524d6455e5a`
- common24 summary MD: `382ca6d8738eb51179d459ad06ee8d1224d87589d5a56455e9abeb37fbf76e62`
- final summary: `d4f15b11ae3e21b4c442e7b098f4c6399e3992990dcbbc0d9eddd32834d23f7c`
- evaluation manifest: `4d17f6484d2c8e65f8779e2d2d3d4ffa3e30bc327d8ec8ada208522ac41773f9`
- evaluation ledger: `0d31041b0dac222259388d187569e7a676ae9ccc48ff4742380320d9fdbd8e14`
- evaluation summary: `6c435625ad257de3ed089e9ac47566dc266e6d8618a2a9c877b2419b66936765`

## Implementation and verification

- runner: `scripts/run_resource_aware_tomato_policy_setup_priority_weighted_v1.py` SHA `54cf5399c3fe0bb3a9a5f382b7521e43a617988198d8f4e85fd6583d001fdf51`
- focused tests: `tests/meta_specialist/test_resource_aware_tomato_policy_setup_priority_weighted_v1.py` SHA `d72347451773c311a797028fd258b7f86b361f20a2b90dcb1fd842e557868a93`
- final focused setup tests: `5 passed`; nearby threshold/surface integration: `9 passed`
- `py_compile` PASS
- docs validator: `Validated 13 canonical documents.`
- `git diff --check` PASS
- no production main/evaluator/parent deck modification
- no commit, push, Kaggle submission

The setup-priority surface is closed as a negative guardrail result. It does not imply every Tomato policy or deck-policy alternating surface is negative; it only rules out these two large setup-active priority reorderings under the current protocol.
