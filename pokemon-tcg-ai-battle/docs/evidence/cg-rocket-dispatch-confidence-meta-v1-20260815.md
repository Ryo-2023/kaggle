# Rocket dispatch-confidence meta v1 — 2026-08-15

## 判定

`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。公開 card evidence の蓄積条件だけを変えた 12 variant は static/compile/pool loader を通過し、TRAIN smoke は 16/16 DONE・fault 0 だった。しかし P1 fixed CEM の独立再評価で正差を再現できず、P1 center を保持した。DEV/FINAL は読んでいない。

## 生成物

- source: accepted `internal_ozawa-rocket-rule_de797c3646e9`、commit `de797c3646e935157618be3edea17615430ccfec`
- root: `runs/cg-rocket-dispatch-confidence-meta-20260815-d/`
- 12 policy（TRAIN 8 / DEV 2 / FINAL 2）、全件 `local_eval_only`、authority 全 false
- pool SHA: `78b2118fbc2d537f4cc3c7e7f65a3657878dc6495491f23056ea0394c9cefdd0`
- fresh meta SHA: `0aace92bd1be10270e5fb59355a936069de1d06c5ae1d74e9e4837960a1d4850`
- split SHA: `01099af99abcb77e6b7922eea382410763b9bb605201613b9afb64ccc90fe09f`

変換は `_dispatch_commit_allowed` に turn history / multi-card の bounded gate を追加しただけで、deck、theta table、visible-state boundary、legal fallback は変更していない。

## 評価

P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` に対し、seed `20260888` の TRAIN smoke は `2W-0D-14L`、runtime 18.02 秒、fault 0。

P1 fixed CEM（seed `20260889`、generation 0、population 16、elite 2、TRAIN 全8 refs、screen 544局、independent re-eval 2 block × 2局/相手/seat）は全て DONE・fault 0。screen elite は c01/c09 とも delta `+3.125pt`だったが、独立結果は c01 `−1.5625pt`、c09 `−4.6875pt`。robust positive / seat-safe 候補は 0 件で、selection は `independent_reeval_x2_positive_delta_gate_preserve_center`。

P1、root deck、BestKnown、Champion、production、submission は不変。Rocket の同一source familyに対する blind retryは行わず、次は相関を下げた別sourceまたは別recipeへ移る。
