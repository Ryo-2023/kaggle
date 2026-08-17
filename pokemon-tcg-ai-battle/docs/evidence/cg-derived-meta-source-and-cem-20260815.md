# cg derived meta source / CEM / fresh holdout — 2026-08-15

## 結論

許可済みinternal sourceから、source内に明示されていたRocket theta tableだけを切り替えるderived meta source生成経路を追加した。base 1件＋derived variant 4件の5件poolを封印し、P1をcontrolにした2世代CEM、独立2ブロック再評価、未使用`META_FINAL` holdoutまで実行した。

全CABT blockはfault 0だったが、独立blockのworst-caseが正になるcandidateは0件だった。fresh holdoutのcenterはP1 control比`+6.25pt`だったものの、seat gap `12.50%`でgate外となった。したがってP1＋root deck、BestKnown、Champion、production、submissionは不変であり、deck phaseへ進めていない。

## derived source契約

- base: `internal_ozawa-rocket-rule_de797c3646e9`
- derived recipe: `ROCKET_THETA_SELECTION_V1:{LUCMIX,A09_MERGED,A07_MERGED,ABOMASNOW_R2}`
- 変更範囲: `_THETA_GENERAL.items()` の初期化参照1箇所だけ。deck、観測境界、runtimeの他部分は変更しない。
- source boundary: `local_eval_only`、authorityはtraining/promotion/submission/longrun全てfalse。
- current pool、configured artifact roots、generated policy SHAを照合し、既使用policy identityを拒否する。
- derived sourceは相関したlocal proxyであり、public/native opponentや独立external sourceとは扱わない。

artifact rootは `runs/cg-derived-internal-meta-20260815-a/` である。

| artifact | SHA-256 |
|---|---|
| pool manifest | `3206f892e587e6983d10cdedacbcdcefad4e12c775ca00e93641ae78db9e0d0c` |
| meta manifest | `deed1833cc00be0e7075d8ba882cd3b9b9b12ed9430720c631b7f6ef9df1037b` |
| fresh meta | `f400208d235f9b1261a56d5a3a54f6b7631755682be8610f6cd7d31a41a8fc35` |
| custom split | `39c139dcefa1d45555dbda0560626f146cc12274d16510f5a28ded4db1ba2a3f` |

`load_opponent_pool_v1`、`build_fresh_meta_batch_v1`、`load_weekend_split`、全5 policyのcompile/static preflightはPASSした。

## CEM

artifact root: `runs/cg-derived-cem-20260815-d/`

- split: `META_TRAIN` 2 refs、`META_DEV` 1 ref、`META_FINAL` 2 refs
- population 24、elite 2、initial scale fraction 0.05
- generation 0/1、各screen 200局、各独立再評価96局
- independent repeats: 2、`positive_delta_gate=true`、`risk_aware_update=true`
- 合計 CEM 624局: 全てDONE、fault 0
- generation 0: worst independent delta `−18.75pt`、center保持
- generation 1: worst independent delta `−12.50pt`、center保持
- generation 1のDEV（base Rocket ref、32局）はcenter `0.125` 対 control `0.0625`、差`+6.25pt`だが小標本であり、CEM promotion根拠にはしない

CEM manifest SHA: `311dc339746677167a3f24939657f14253f4ced1393150755eeada82bc84e26d`。

なお、最初の試行ではelite_count=6に対しvalid candidateが4件しかなくfail-closed停止した。runnerへpopulation/elite overrideと「valid候補不足時はcenter保持」の契約を追加し、risk-aware設定で再実行した。これはCABT faultではなく研究runnerのbudget契約修正である。

## fresh META_FINAL holdout

artifact root: `runs/cg-derived-holdout-final-20260815-d/`

- candidate: gen1 center `cg-p1-cem-incumbent-g01-39c7de5282bc`
- candidate policy SHA: `a417da9ca0177d2f86e4120ec0359713d1d49a9256e7a55f2fb8e1de10edb0d9`
- control policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- refs: A07_MERGED＋ABOMASNOW_R2、両seat、各8局、合計64局
- candidate: `4W-0D-28L`、score `0.125`
- control: `2W-0D-30L`、score `0.0625`
- delta: `+6.25pt`
- candidate seat rates: `0.1875 / 0.0625`、gap `12.50%`
- fault: 0
- decision: `NOT_PROMOTABLE`

holdout summary SHA: `7b6512ccf21191f3d054f127832bb7a9c058b87472f967b2661e0bf7ff34d103`。

## 再開条件

1. P1をBestKnownとして保持する。
2. derived poolのholdoutを再利用せず、新しいsource epoch／unused refsを追加する。
3. external/internal source diversityを増やせない場合、derived proxyの結果をnative性能根拠に昇格しない。
4. 次の候補は`risk_aware_update`で全independent block positive、fault 0、seat gap≤5%を満たしてからfresh DEV/FINALへ送る。
5. positive transferが成立した場合だけ`cg_bestknown_loop_v1`へpolicy phaseを渡し、deck phase→policy phaseへ進む。

commit、push、Champion変更、Kaggle提出は行っていない。
