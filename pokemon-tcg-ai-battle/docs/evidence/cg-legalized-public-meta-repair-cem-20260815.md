# cg legalized public meta repair / CEM v1（2026-08-15）

## 結論

公開 Kaggle kernel の合法 deck 化は、元 policy を改変せず、明示した deck 位置置換と repository-owned wrapper を SHA 固定する方式で実装できた。Dragapult v3／Gardevoir／Hydreigon は bounded CABT smoke を fault なしで通過し、未使用 `META_TRAIN / META_DEV / META_FINAL` split を作成できた。一方、Gouging Fire は entrypoint adapter を追加しても公開 policy が `None` の active を処理できず、4/4 smoke fault のため quarantine した。

P1 固定 CEM は 2 世代・population 8・独立再評価 2 回を fault なしで完走した。screen 上では候補が最大 +25.0pt、世代1の候補は独立再評価でも平均 +12.5pt だったが、seat-safe / opponent×seat-safe gateを満たさず center は更新しなかった。世代1最良候補を未使用 `META_FINAL`（Hydreigon）で再確認すると P1 に −18.75pt で、BestKnown 更新候補ではない。

判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。P1 policy、root deck、BestKnown、Champion、production、submission は変更していない。

## 1. 生成方式と合法化

実装は `src/mage_ptcg/opponent_ingest/legalized_public_meta_v1.py`、CLI は `scripts/generate_legalized_public_meta_v1.py`。recipe は `EXPLICIT_POSITION_REPLACEMENT_V1` で、元 source policy の byte / SHA を保持したまま deck だけを明示置換する。公式 catalog による exact 60、ACE SPEC exactly one、static source scan を生成前に実施した。

置換は次の4件である。

- Gardevoir: index 9 の旧 ID 5 → 1158
- Hydreigon: index 19 の旧 ID 7 → 1088
- Dragapult v3: index 11–13 の旧 ID 13 → 1184
- Gouging Fire: index 25 の旧 ID 1088 → 1227

初回 root `runs/cg-legalized-public-meta-v1-20260815/` は binary tar を text source として decode しようとしたため失敗し、性能 artifact へ昇格させず quarantine した。suffix skip を修正した v2 は4件を受理し、pool SHA `e00e5f83d0960822be9a8be5ecda907ab591501752270cc6222918fc3df2955b`、fresh SHA `1d8f196cab2ca260f6768bcba56890ffec94903b38d6d42baeb18d568e9396ca` を seal した。

## 2. bounded smoke と昇格 subset

初回4件の smoke は 8 requested 中 6 DONE / 2 AGENT_ERROR（fault 2）だった。Gouging Fireだけが fault で、Dragapult／Gardevoir／Hydreigon は両 seat DONE・fault 0。安全 subset を別 rootへ partial promotionし、pool／fresh／split SHA は次の通りである。

- root: `runs/cg-legalized-public-meta-promoted-v2-20260815/`
- pool: `93ebd7a6090afcbf7361576821281aeb665da3ee9e4ef91eb7df2e110d2b2479`
- fresh: `d1d5c07db9dc3ce1f59272c79142d5d17eaeeaff5f666d9d33e12dcce5d2fb9b`
- split: `74e4c2c8bd29c201b79b3cc1cef08191bee282720873a21deadec00d843b12cd`
- `META_TRAIN`: Dragapult v3、`META_DEV`: Gardevoir、`META_FINAL`: Hydreigon

Gouging Fire向けに `DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1` adapterも試した。wrapper契約と single-argument 呼び出しは修復できたが、公開 policy 自身が `None` active に対して `prize_count(None)` を通るため v2 smoke は 4/4 AGENT_ERROR。根拠のない broad fallback は加えず、poolへ昇格していない。

## 3. P1固定 CEM

実験 root は `runs/cg-legalized-public-meta-cem-v1-20260815/`。設定は campaign seed `20260895`、2 generations、population 8、elite 2、`META_TRAIN + META_DEV` search、`META_FINAL` validation、独立 re-evaluation 2 repeats、positive-delta gate、risk-aware update、research-only authority全false。全 evaluation / re-evaluation row は fault 0 である。

世代の要点は以下である。

| 世代 | screen上位 | 独立再評価 | gate結果 |
|---|---:|---:|---|
| g00 c03 `a1e8b4dbd2a2` | +25.0pt | repeat −25.0pt / +25.0pt、mean 0、min −25.0pt | positive gate不通過 |
| g01 c05 `35c1f8f63faf` | +12.5pt | repeat +12.5pt / +12.5pt、mean/min +12.5pt、seat gap 0 / 25% | seat-safe不通過 |

両世代とも `elites = [incumbent-center, incumbent-center]` で、P1 center・policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` を保持した。g01 c05 の candidate policy SHA は `b7616fe97f8d151b37af2bee94f0a7b858017912e397095b5b6a9e6bc9798cc9`。

g01 c05を未使用 `META_FINAL` の fresh confirmationへ回した。

- root: `runs/cg-legalized-public-meta-cem-fresh-confirmation-v1-20260815/`
- seed `20260917`、Hydreigon両 seat各16局、合計32局/arm
- candidate: 21W-0D-11L、65.625%、seat gap 6.25%
- P1 control: 27W-0D-5L、84.375%、seat gap 6.25%
- candidate delta: −18.75pt、fault 0、decision `NOT_PROMOTABLE`
- fresh SHAは `d1d5c07db9dc3ce1f59272c79142d5d17eaeeaff5f666d9d33e12dcce5d2fb9b`

この FINAL は今回の候補確認に使用済みである。次 campaignでは新しい blind holdout を作り、Hydreigonを再び未使用 FINAL として扱わない。

## 4. provenance と権限境界

合法化対象は Sushanth 公開 Kaggle kernel の local-eval-only snapshotであり、native性能証拠・提出候補ではない。各 wrapper の source policy / generated policy / deck / source SHA は各 `SOURCE.md` と pool manifest に記録した。P1＋root deckの基準 packageは self-owned cg lineage（internal branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`）であり、今回の公開 source repairで変更していない。

全 artifact は `training_allowed=false`、`promotion_allowed=false`、`submission_allowed=false`、`longrun_allowed=false`。commit、push、Champion変更、Kaggle提出は行っていない。

## 5. 次の再開条件

同じ Sushanth source、同じ deck replacement、同じ CEM campaign の blind retry はしない。次は未性能使用 policy lineageまたは新規 permission済み sourceを含む相関の低い poolを生成し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を通過した候補だけを `cg_bestknown_loop_v1.py` へ接続する。
