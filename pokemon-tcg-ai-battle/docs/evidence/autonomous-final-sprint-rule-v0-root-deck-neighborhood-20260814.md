# FINAL-SPRINT Rule v0 root-deck neighborhood（2026-08-14）

Rule v0 と bundle-compatible root deckを固定し、候補生成を2件に限定した48局 discoveryを workers=12 で実行した。親は4/48（8.3333%）、`1182 Boss's Orders→1213 Judge` は3/48（6.25%、−2.083pt）、`1152 Poké Pad→1185 Explorer` は1/48（2.0833%、−6.25pt）だった。全144局は DONE/fault0/draw0、両seat、12 opponents、repetition2、同一seed schedule、evaluator SHA固定である。

| arm | mutation | W-D-L-F / 48 | score | 判定 |
|---|---|---:|---:|---|
| parent | root deck | 4-0-44-0 | 8.3333% | control |
| boss-to-judge | 1182→1213 | 3-0-45-0 | 6.25% | negative / stop |
| pad-to-explorer | 1152→1185 | 1-0-47-0 | 2.0833% | negative / stop |

正典 root は `runs/final-sprint-autonomous/final-sprint-rule-v0-root-deck-neighborhood-20260814/`。parent summary `bdef62332649e02d40e6c624fc15087c8c78ba083b7144d16ae14e30be4c974d`、boss summary `fcaf4735a0ea0bf8f59c11ddc533b0ba334a5de34f6eecdbb5afb2c22e5c4486`、pad summary `426ed27e7c7f3b4dd74bc22aeac6379e2dd9544fa811d93a30cf11ddccbde29b`。candidate deck SHAは順に `f5d8424a…` / `68e405b1…`。

2候補とも親を下回ったため、common24/384/longrunへ進めずcandidate-onlyで停止する。同surfaceはhard-negativeへ追加し、Rule v0×root deckの12-opponent局所改善としては不採用。native action/teacher/private情報、training/promotion/submission authorityは不使用・全false。既存production、2×2 artifact、Champion、permission、commit/push/submissionは不変。

この画面の機械artifactは同名JSONに保存した。次の実験は既存hard-negativeとdeck multisetが重複しない別surfaceを、ResourceGovernor正常時のworkers=12で48→96に限定して選ぶ。
