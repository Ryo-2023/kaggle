# cg stratified behavior meta v2b / CEM evidence (2026-08-15)

## 判定

`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`

既存の `stratified_behavior_meta_v2` recipeを、同じartifactやsource commitを再利用しない
別compositionへ接続した。12件のsourceはseal・static safety・runtime smokeを通過したが、
2世代CEMの独立lower-tailでrobust positive candidateが成立しなかった。P1、BestKnown、
Champion、production、submissionは不変である。

## source composition

specは `configs/meta_specialist/cg_stratified_behavior_v2_epoch_b.json`、生成rootは
`runs/cg-stratified-behavior-meta-20260815-v2b/` である。Alakazam、Comfey、Festival、
Psychicの4 familyから12件を選び、source commit／base candidate／derived policy SHAを
pool全体で各12 distinctにした。splitは META_TRAIN 8、META_DEV 2、META_FINAL 2で、各
splitは2 family以上を含む。Metalは既使用のため再利用していない。

| artifact | SHA-256 |
|---|---|
| pool manifest | `e3474b0864b5d55302f7efea7f3b1c09ce7772f966c2d45a14f10ed53a304550` |
| fresh meta | `91f8a6ad8fc7d6bab8ae65e8b970c3f9e06c37d37c9d0000ae7374f336237dd9` |
| `cg_historical_split.json` | `867062ff515f028dd282d266f2d710abc5a9b5fbcab67cdd75b7c5fdf10faede` |
| `meta_manifest.json` | `5a737a55751f1dadb1f9d25d3b3c0e4431310376c45f00245ba7818d4705dc07` |

seal結果は12 accepted、authorityは training／promotion／longrun／submission 全て false、
imports実行なし、network accessなしである。

## smoke

P1 packageを候補に、全12 reference・両seat・各1局の接続smokeを実行した。

- artifact: `runs/cg-stratified-behavior-smoke-20260815-v2b/`
- requested/completed: `24 / 24`
- status: `DONE=24`
- fault: `0`
- smoke summary SHA: `2d759f8d88e2ca4e219cacb1bde00ce8e5265d7870bc0f2aeae6390fa7b7a28a`

このsmokeは実行対象を全poolへ指定したため、META_FINAL 2件もCABTへ投入済みになった。
したがってMETA_FINALは今後のfresh holdoutとしては使用不可と扱う。CEM自体はMETA_TRAIN
だけを検索し、META_FINALのidentity hitは0件で、FINAL confirmationは起動していない。
次回はsmokeでもTRAINだけを指定し、DEV/FINALを完全に分離する。

## CEM

rootは `runs/cg-stratified-behavior-cem-20260815-v2b/`。P1をcandidate/controlのcenterに
固定し、population 8、elite 2、2 generations、campaign seed `20260963`、all TRAIN refs、
独立再評価2回、各re-evaluationは両seat・2局、`positive_delta_gate`、`risk_aware_update`
を使用した。

| block | requested/completed | fault | score rate |
|---|---:|---:|---:|
| generation 0 screen | 288 / 288 | 0 | 42.7083% |
| generation 0 independent | 192 / 192 | 0 | 40.6250% |
| generation 1 screen | 288 / 288 | 0 | 47.2222% |
| generation 1 independent | 192 / 192 | 0 | 41.6667% |

generation 0のscreen上位候補は `−3.125pt` と `−9.375pt`だった。独立再評価はそれぞれ
`−6.25pt / −9.375pt`、`+3.125pt / +12.50pt`で、後者はopponent×seat safe gate外。
generation 1はscreen上位が `+25.00pt`、`+28.125pt`まで見えたが、独立再評価はそれぞれ
`+18.75pt / −18.75pt`、`+12.50pt / −15.625pt`へ反転した。後者のseat gap自体は
全体でsafeでも、opponent別seat gateを満たさない。両世代の elite selection は
`independent_reeval_x2_positive_delta_gate_preserve_center`で、centerはP1のままである。

generation 1のMETA_DEV診断はP1 center同士で `14W-0D-18L` 対 `14W-0D-18L`、差 `0pt`、
fault 0だった。これはpolicy変更ではなく、center固定であることを確認する診断である。

## 研究判断

このepochは、(1)既存transformを別compositionへ再利用するsource generation契約、
(2)source commit／base／policy SHAの重複禁止、(3)family-balanced split、(4)fault0 CEM接続、
を確認した。一方、性能改善は未成立であり、今回のpoolをnative/public evidenceや
BestKnown更新の根拠にはしない。

P1 policy SHAは
`1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは
`2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`のままである。P2/P3、
deck search、`cg_bestknown_loop_v1.py`への昇格、commit、push、Kaggle submissionは行っていない。

次にsourceを増やす場合は同じproxyのblind retryではなく、fresh holdoutをsmokeから分離した
新しいpermission済みsnapshotまたは複数familyの新recipeをsealし、fault0→独立positive→
opponent×seat safe（≤5%）→未使用DEV/FINALの順で判定する。
