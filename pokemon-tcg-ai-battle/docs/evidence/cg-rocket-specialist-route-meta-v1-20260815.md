# Rocket Specialist Route Meta v1 / TRAIN-only CEM — 2026-08-15

## 判定

`_SPECIALIST_THETA` のfamily-to-theta routingだけを変更する新しいsource-generation familyを実装し、12件をhash-boundにsealedできた。TRAIN-only smokeとCEM runtimeはfault-freeだったが、P1 controlを上回る独立lower-tail／seat-safe／opponent×seat-safe candidateは得られなかった。判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL` とする。

P1、root deck、BestKnown、Champion、production、submission、current poolは変更していない。META_DEVとMETA_FINALは実行していない。

## source生成

Rocket Theta v2の数値theta transformが独立再評価で反転したため、同じnumeric proxyを再試行せず、受理済みsource内の既存dispatcher routeだけをboundedに組み替えた。

- base source: `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9`
- source branch: `agents/ozawa-rocket-rule`
- source commit: `de797c3646e935157618be3edea17615430ccfec`
- source policy SHA-256: `8025ae95503ef10cc82a433518e81ba61554ce1547846eecc582610a85ae6c7f`
- staged policy SHA-256: `159a5d61ce7d1d12cf955a5d2bf99845b25d3d32eedc3904ee46e21143be053e`
- canonical deck SHA-256: `d61230a21f488d4e78b28b37187c6a468168c0a2fff7842025e6c0409da3614a`
- implementation: `src/mage_ptcg/opponent_ingest/rocket_specialist_route_meta_v1.py`（SHA `6e30e20daf6de1e5972fc690231a96996def3abffe68a6026f2ab2bffd53eec`）
- CLI: `scripts/generate_rocket_specialist_route_meta_v1.py`（SHA `96141634bde7d93d5112c5afa2554550db75b9b50ad11d20b1d357af07247e0f`）
- config: `configs/meta_specialist/cg_rocket_specialist_route_v1.json`（SHA `c7893a5088d38a92b929572e76d9e479520b96de8cdc798bb9804084f9fdf497`）
- test: `tests/test_rocket_specialist_route_meta_v1.py`（SHA `540ed142fc47014cbba0f781b554a0684ac99c0db6ed7f715f3b23d264455638`）

12 variantはA01/A09/A07/A11の各familyをGENERALへ戻すablation、pair共有、GENERAL_ONLY、specialist交換、全循環交換である。現行routeそのものはcandidateに含めず、値Name tokenだけをAST source spanで置換した。`_TIER_A_TO_GROUP`、commit条件、公開情報抽出、deck、import、環境変数、`_apply_theta`は保持した。

sealed rootは `runs/cg-rocket-specialist-route-meta-20260815-b/` で、TRAIN 8／DEV 2／FINAL 2、全policy SHA distinct、`local_eval_only`、authority全falseである。`runs/`全体は約127GBのため、identity scanは`docs/evidence/`とcurrent pool manifestへ限定した。opaque payloadを無制限に走査していない。

| artifact | SHA-256 |
|---|---|
| pool manifest | `dcab93e7b948a6449a48c5e33b8b9836bf3356bd0869fc828095649fce632289` |
| fresh meta | `db1c41c7a86bb018ef74597e68767622fc648a1f25ef17fcb5ec8528838765dd` |
| `cg_historical_split.json` | `a32662471c51718146ac0eee838a05ecafd8e5cbee72af398df73b7661be19b1` |
| `meta_manifest.json` | `946701cd718f02b252ce5fe5790ba244f7568ac1ff5462ce6f63bce26015a6f1` |
| `intake_report.json` | `2c471b6a7b08c8d006d99a090d533889249ce363068dd26bea47ec2f3999e50b` |

## preflightとTRAIN smoke

以下をPASSした。

- 12 policy compile、pool loader、exact 60-card、split verification
- focused suite: `21 passed`（Rocket route 7、Rocket theta 7、derived internal 2、stratified 5）
- `python scripts/docs/validate_docs.py`: `Validated 13 canonical documents.`
- `git diff --check`

TRAIN 8件だけを`--reference-id`で明示し、P1 packageを両seat各1局、合計16局実行した。

- artifact: `runs/cg-rocket-specialist-route-smoke-20260815-b/`
- requested/completed: `16 / 16`
- status: `DONE=16`
- fault: `0`
- draw: `0`
- score rate: `2 / 16 = 12.5%`
- smoke summary SHA: `6ee797ab75ced0b27f04443b1a570d8aac2b75fc9f0e8b6afa02c3b5c3c8fc86`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

## P1 CEM generation 0

DEV/FINALを自動投入しないため、generation 0だけを実行した。P1をsource/controlの両方に固定し、TRAIN 8件、population 16、elite 2、independent re-evaluation 2回、両seat各2局、positive-delta gate、risk-aware updateを使った。

実行rootは `runs/cg-rocket-specialist-route-cem-20260815-b/`。

```text
TMPDIR=/tmp PYTHONPATH=.:src python scripts/run_cg_p1_cem_v1.py \
  --output runs/cg-rocket-specialist-route-cem-20260815-b \
  --split runs/cg-rocket-specialist-route-meta-20260815-b/cg_historical_split.json \
  --source-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package \
  --control-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package \
  --pool-root runs/cg-rocket-specialist-route-meta-20260815-b \
  --generations 1 --all-train-refs --reeval-for-update \
  --reeval-repeats 2 --reeval-games-per-opponent-seat 2 \
  --positive-delta-gate --risk-aware-update \
  --initial-scale-fraction 0.05 --campaign-seed 20260884 \
  --population-size 16 --elite-count 2 --execute
```

| block | result |
|---|---:|
| screen | `544 / 544` DONE、fault 0、`60 / 544 = 11.0294%` |
| independent re-evaluation | `192 / 192` DONE、fault 0、`19 / 192 = 9.8958%` |

主要artifact SHAは次のとおり。

- campaign manifest: `e4e5f4f6a44b4a3a5d894045eb476bdc4071e82ee402b2f778ab3d13290d2ce`
- generation manifest: `6386a6fea7091d0b24d46d0daaae2ad2448e14b243283a8b383103b4d99c8640`
- screen summary: `faa51c846882633e66c08121b4cf1619a3ed790a1a4307ac613ead4c0adbd539`
- independent summary: `6a3785522cc9899e9d8c9174e8da4cf7f18f2ab735aab0b2a336c8acd6b1a79b`
- generation results: `451ef79ccf0bb1a9742cba10cfbcf53e1dc4fc31e824a47b4d419b3c0cdb1355`

screen上位候補は独立評価で安全な改善にならなかった。

| candidate | screen delta | independent repeat delta | independent mean / min delta | seat-safe | opponent×seat-safe |
|---|---:|---:|---:|---|---|
| `cg-p1-cem-g00-c02-eda8ff2f64c8` | `+12.50pt` | `+3.125pt / 0pt` | `+1.5625pt / 0pt` | false | false |
| `cg-p1-cem-g00-c11-11a9b242e687` | `+9.375pt` | `+9.375pt / 0pt` | `+4.6875pt / 0pt` | false | false |

両candidateとも独立lower-tail positive、seat-safe、opponent×seat-safeを同時に満たさず、`independent_reeval_x2_positive_delta_gate_preserve_center`でP1 centerを保持した。generation 1、DEV、FINAL、policy promotion、deck phase、`cg_bestknown_loop_v1.py`接続は起動していない。

## 研究判断

このfamilyは、Rocketの既存specialist routingを新規policy SHAとして安全に生成・split・CEMへ接続できることを示した。しかし今回のTRAIN arenaでは、CEM policy候補のscreen正差は独立lower-tailで0まで縮み、route compositionの再現性付き改善は確認できなかった。同一Rocket source commit由来のlocal proxyであり、native/public metaの代替とは扱わない。

次はこのroute familyやnumeric theta familyのblind retryをしない。次の再開条件は、許可済みの別source snapshot／新しいfamily composition、または別の独立meta acquisitionである。source generationが独立positiveを得た場合のみ、未使用DEV→FINALを経てpolicy→deck→policy loopへ接続する。

