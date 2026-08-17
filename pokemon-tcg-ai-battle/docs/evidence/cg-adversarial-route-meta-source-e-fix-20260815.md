# actor-visible routed meta source e-fix / P1 CEM (2026-08-15)

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。別deck family（Skarin／Zoli Dragapult）の同一 canonical deck parent pairから、actor-visible routed ensembleを新しいsource epochとして生成した。初回epochは親payloadのdeck asset不足で全faultとなったが、生成器を修正して親deckを隔離wrapperへ同梱し、異なるcanonical deckを組み合わせるspecificationを生成前に拒否した。修正版は8/8 smoke・fault0でpromoteできたものの、P1固定CEMの独立positiveはDEVへ転移せず、P1 centerを保持した。P1、root deck、BestKnown、Champion、production、submissionは不変である。

## 固定状態

- P1 policy SHA-256: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck canonical SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- parent deck canonical SHA-256（Skarin／Zoli）: `17cd742b94386fa5e003e8e43a7ea5b4bbb2f39443f75b0fe7ba81d15f3a1f35`
- source／policyは`local_eval_only`、authorityは全てfalse
- heavy run完了後のactive processなし

## 1. 初回route eの失敗と再発防止

`runs/cg-adversarial-route-meta-20260815-e/` はSkarin／Zoliの4 routed candidateをsealしたが、P1 smokeが8/8 faultとなった。直接原因はSkarin payloadがimport時に相対`deck.csv`を読み、存在しない場合に`/kaggle_simulations/agent/deck.csv`へfallbackする契約だった一方、生成wrapperの`parent_a/`／`parent_b/`へ親deckをコピーしていなかったことである。これは親policyの性能結果ではなく、生成器のasset contract defectとしてquarantineした。

TDDで次を固定した。

- 親payload隔離rootへ親自身の`deck.csv`をコピーする。
- `policy_a`、`policy_b`、`deck_parent`のcanonical deck hashが一致しないspecificationを生成前にfail-closedで拒否する。
- 相対deck importとKaggle絶対path fallbackをloader testで検証する。

実装は`src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`、回帰は`tests/test_routed_ensemble_meta_v1.py`である。旧fault artifactは削除・改変していない。

## 2. 修正版sourceのseal・smoke・split

新規root `runs/cg-adversarial-route-meta-20260815-e-fix2/` は、同じ親pairからroute recipeを変えた4候補を生成した。

| artifact | SHA-256 / 結果 |
|---|---|
| generated pool | `888f2325a80b91a1dde54cf83ca613007bea032f74d75f5f5040e544aafc8291` |
| generated fresh meta | `b0b0562aca82d35de57dda3d6154121585c8a84b0cbdef91f0a99e530b9abafb` |
| generated split | `747f20f4174b60509a25f88d4cb6fe84eec153db0b95a1b92d605017a97d1647` |
| smoke summary | `823733b079ce8bc2bbfb3172336360861951ff2fad4efecd42209ee0a9216772` |
| promoted pool | `ea71e13bc89dcc9cc634c8bc520f4e05c8895aebeef0c1614eae1554761e2e0d` |
| promoted fresh meta | `4e0cdee6bf9dd24607c8d336f59cd2c657c2eb497cc215428e18f7197d56db90` |
| rebound split | `d3cd0221f4c3f7dc34811cb5fea495fce8495ba002fd4b507e6b6fa0dbade761` |

全4候補について親deck同梱・loader importを確認し、P1両seat smokeは`DONE=8/8`、fault `0`、draw `0`、P1 `2W-0D-6L`（score `25.0%`）だった。smokeは候補のruntime安全性を示すもので、BestKnown性能の根拠にはしない。

splitは`META_TRAIN=2`、`META_DEV=1`、`META_FINAL=1`である。CEMはTRAINのみを探索し、DEVはgeneration 1の診断、FINALは未使用のまま保持した。

## 3. P1固定CEMの結果

`runs/cg-adversarial-route-cem-20260815-e-fix2/` を、P1をcontrolに固定して実行した。

- campaign seed: `20260882`
- search mode: `META_TRAIN_ALL`
- population／elite: `4／1`
- generation: `2`
- independent re-evaluation: 1 block、候補ごと4 games/opponent/seat
- positive-delta gate: 有効
- 全CABT row: fault `0`
- manifest SHA: `07bdf5b6104cdcc2fb78de51ce6ef6e94bf041c1fea16ac0d7dee6e0db895c74`
- generation 0 results SHA: `aff3bb1500535a7028c4fccf51b7f62f6bf36dad182a3cc3f92ab5d13303d7a5`
- generation 1 results SHA: `34239b80f1afa2b4e46c75e7affaaecceef8e7f863092c4700d80399b4e95c69`

generation 0では候補 `cg-p1-cem-g00-c02-d892b7a55419` が独立再評価で `6W-0D-10L`、objective `0.375`、control `4W-0D-12L`、objective `0.25`（差 `+12.5pt`、seat rates `37.5%/37.5%`）となり、centerとしてgeneration 1へ進んだ。しかしgeneration 1のscreen独立候補はpositive gateを満たさず、centerを保持した。

generation 1のDEV診断では、centerが `3W-0D-13L`、objective `0.1875`、controlが `5W-0D-11L`、objective `0.3125`で、差は `-12.5pt`（両方fault0、seat ratesはcandidate `25.0%/12.5%`、control `37.5%/25.0%`）だった。従ってcandidateはfresh validationで再現せず、P1 centerを研究parentとして維持した。META_FINALは読んでいない。

## 4. 直近routeの解釈

- route c（Koushik v4＋v10/v11）は8/8 fault0のsmokeまで通過した。
- route dのcandidate c01は独立route d確認で `19W-0D-365L`、score `4.9479%`。P1は同一seed strataで `32W-0D-352L`、score `8.3333%`となり、candidateはP1より13勝少なく、`-3.3854pt`。transferを明示的に棄却した。
- route e初回はdeck asset defectで全fault、e-fix2はその契約を修正してfault0へ回復した。

異なるparentを混ぜるだけでは十分なpolicy improvementを得られず、source強度・seat symmetry・independent transferを同時に満たすには、runtime-safeな別lineageと複数behavior familyの相関管理が必要である。

## 5. 検証と次のゲート

- routed generator focused tests: `5 passed`
- adversarial source／bestknown／weekend split focused tests: `10 passed`
- e-fix2 smoke: `8/8 DONE`, fault `0`
- CEM: 全row `DONE`, fault `0`
- commit／push／Champion変更／Kaggle submission: 未実施

次は同じSkarin／Zoli pair、同じroute recipe、同じCEM seedをblind retryしない。新しいmeta sourceは、未性能使用policy lineageまたは相関の低い複数runtime-safe familyを含め、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を順に通す。全ゲートを通過したcandidateだけを`cg_bestknown_loop_v1.py`へ接続し、P1→policy CEM→fresh validation→deck→policyを再開する。
