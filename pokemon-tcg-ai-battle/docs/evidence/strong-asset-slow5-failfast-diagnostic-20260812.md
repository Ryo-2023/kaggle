---
title: Strong Asset slow-five fail-fast runtime diagnostic (2026-08-12)
status: research-only
---

# 結論

通常のcommon-arenaへ戻すと長時間policyが全ランキングを停止させる5 assetについて、各24 reference opponent・両seat・各1局（48局/asset、計240局）、1局15秒の親/worker timeout、max_steps=2000でfail-fast診断を実行した。全240局がfault（completed=0、fault=240）となり、勝率比較に使えるDONE局は0だった。従って5 assetは現行CABT budgetでは性能ランキングへ統合せず、runtime-incompatible/quarantine cohortとして扱う。

## 条件

- command: `scripts/run_asset_pair_ranking_v1.py`
- asset IDs: `kinoshita_pimc_search`, `ozawa_metal_psychic_search`, `water_box_search`, `waterbox_search_v3`, `tientrum_alakazam_search`
- 参照: 24 opponent IDs、seat 0/1、各1局
- requested: 240 games（48 games/asset）
- base seed: `9600000`
- block id: `asset-ranking-slow5-failfast1`
- workers: 8、worker recycle: 16
- timeout: 15 seconds/game、max_steps: 2000
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- evaluator implementation SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- engine seed setter: false。結果は独立層化であり、paired評価ではない。

## 結果

| asset | requested | DONE | fault | fault rate | score | 判定 |
|---|---:|---:|---:|---:|---:|---|
| `kinoshita_pimc_search` | 48 | 0 | 48 | 100% | — | runtime quarantine |
| `ozawa_metal_psychic_search` | 48 | 0 | 48 | 100% | — | runtime quarantine |
| `water_box_search` | 48 | 0 | 48 | 100% | — | runtime quarantine |
| `waterbox_search_v3` | 48 | 0 | 48 | 100% | — | runtime quarantine |
| `tientrum_alakazam_search` | 48 | 0 | 48 | 100% | — | runtime quarantine |
| **合計** | **240** | **0** | **240** | **100%** | **0.0（fault denominator）** | **ランキング統合不可** |

fault rowはrequested denominatorから除外していない。DONEが0なので、0.0は「弱い」という勝率ではなく「このbudgetで比較可能な対戦を完了できなかった」ことを表す。

## 評価器の不具合と修正

初回fail-fast実行では、親watchdogが`parent_timeout` rowを作ったあと次のfutureを投入しなかったため、要求gameが未処理のまま最終整列で`KeyError`になった。`tests/test_parallel_cabt_evaluator_v1.py::test_timeout_refills_bounded_queue_and_persists_all_rows`をREDで再現し、timeout rowの保存後にfuture cancel、progress callback、次game投入を行う修正を追加した。修正後は関連suite 18 passedで、240件すべてのfault row・ledger・summary・asset rankingを生成できた。

一方、native policyがSIGALRMを無視するケースでは、親がrowを確定しても実行中のchildを即時killできない。今回もartifact生成後に親/child workerが終了待ちになったため、今回の明示PIDだけをTERMで終了した。これはhard native killが未実装であることを示す。今後通常rankingへ混ぜず、worker process isolationまたはper-game subprocess hard-killを別設計する。

## 一次artifact

root: `runs/meta-specialist-asset-ranking-slow5-failfast1-20260812`

- asset ranking: `eb14411fbc0ee71776498ec9a26341ac5692a16bf9732ac490e76cfd6864c201`
- ledger: `25c9e00cb78ae9a26c15535ee59c6492cc75ad6a70eafc525c6d26fcd45e3913`
- summary: `acf56e6466215bd3f3384c4a61c0bcc88ddeeb04f5579c82ea7aced3e28c83c1`
- manifest: `ac784e94d630ced44c2adf39214f4723c6eaa3df36cb264a8ea7b739cca83669`
- progress snapshot: `4341203327895ccfb31d79217d9e2cef3267ce795d605d49f83481a443ef060f`

## BestKnownへの反映

この診断でslow5の「性能順位」は得られていない。したがって、現時点の`EvaluationBestKnown`候補は引き続きnative `tomatomato_archaludon`（top3 pooled1536、72.0703%）であり、`lucifer19_battlecore`（71.8099%）と`plamen06_steel`（71.7448%）をnear-tie controlとして保持する。slow5はGlobalBestKnownへ0勝として入れるのではなく、`comparison_infeasible_under_current_runtime_budget`として別区分に置く。R7は別96局診断済みだが、smoke=false/local_eval_onlyかつ局数非整合のためtraining/submission/Global統合対象外である。

## 制約

- このartifactは性能改善、teacher quality、training eligibility、submission eligibilityを証明しない。
- `local_eval_only`をtraining labelやsubmissionへ転用していない。
- slow5をmax_steps/timeoutを緩めて再度通常queueへ戻すことは禁止する。必要ならhard-kill対応後、同じprotocolで個別診断する。
- Champion変更、longrun、Kaggle submissionは行っていない。
