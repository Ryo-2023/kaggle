# cg P1 公開テレメトリと P2 bounded screen — 2026-08-14

## 結論

研究親を `cg-lethal-target-v1 + root deck`（P1）に固定し、P1の public decision/action telemetry を新規 read-only wrapper で収集した。96局は全て `DONE`、fault 0 で、4,077件のdecision rowと96件のredacted deck-registration rowを得た。private/opaque key scanは0件、projection faultは0件だった。この観測から最大3件のbounded public hypothesisを作り、P1をcontrolとして96局screenを実施した。

結果は `cg-lethal-retreat-damage-v2` が +0.5208ptの弱い正差に留まりcandidate-only、`cg-lethal-attach-threshold-v2` は−6.25pt、`cg-lethal-overkill-conservation-v2` は−3.125ptで停止した。いずれもcommon24/384/768へ進めない。P1、Rule v0 Champion、root deck、production entrypoint、提出物は変更していない。training、teacher label、promotion、longrun、Kaggle submissionも未実施である。

## 1. 不変 identity と収集契約

- P1 source/package SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- telemetry module SHA: `f00c7e88b33f87fc38739318ccd7affcc1295ca15da4f5291aac35a4f05c6bd6`
- telemetry runner SHA: `16719260aeee164077756bfa02f639ddd3820c81935cb07ee4832bea46b891c2`
- telemetry test SHA: `73aaeab378099d84a940b4ed20e16b213804f71462a4dd5b60d997aa590216c2`
- P2 candidate module SHA: `5841eb1cdb75ac64db652b20183cc7a67b85b706b92ae1c11b358d1127961051`
- P2 screen runner SHA: `4f07ac2e8bc14142b0cb8a43ac4a1755a1d426b50d20234d9ca31dfd2075896e`
- P2 test SHA: `090d077cbf5740a7f7e9cc6ee9b4743a9d48e18fb0d88bca8136cb275e347a1c`

収集rootは `runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260814-v1`。broad24、両seat、各opponent×seat×repetition strata、workers=12、recycle=16、authority全falseで固定した。manifest-complete SHAは `5e389d495d480d5883213a09815ed24e6a92e174d7c4ea0800fca7f15a278e8c`、summary SHAは `fabdd3fcc49432bf058f33bb2673904c7c194aebe480163558900a5171fc2f1f`。これはP1 policyとnative poolの対戦結果を再評価するscreenではなく、候補設計用の観測収集である。

telemetryは `normalize_decision_record` のpublic projectionだけを保存し、deck registrationはカードIDを含めず `deck_size` のみを保存した。`hand`、`deck`、`prize`、`logs`、`search_begin_input`、`raw_observation`、opaque/private fieldは保存しない。外部scanで全4,173行のexact private-key hitは0件だった。

## 2. 観測された状態と候補化

decision rowは4,077件、MAINは2,658件だった。MAIN action type別のselected countは PLAY 1,252、ATTACH 446、ATTACK 403、END 224、EVOLVE 178、ABILITY 136、RETREAT 19。候補に使える公開状態の観測は次の通りである。

| 公開状態 | eligible count | selected count | 仮説 |
|---|---:|---:|---|
| active damage >=100 かつ powered bench | 38 | 1 RETREAT | damaged activeからの交換優先が不足している可能性 |
| Mega LucarioへF Energyを付ける閾値状態 | 69 | 37 ATTACH | 1 energy状態の加速を優先すべき可能性 |
| attack damage−target HP >100 | 73 | 70 ATTACK | 過剰打点を避ける保存的選択が可能な可能性 |
| lethal (`hp <= damage`) | 192 | 163 ATTACK | P1既存のlethal優先が比較対象 |

各候補は `Observed failure / Hypothesis / Exact change / Risk / Kill condition` を固定したbounded overlayであり、private state、teacher label、native behavior、future RNGを使わない。

## 3. P2 screen結果

各rootは候補96局＋同一P1 control96局、合計192局。全行 `DONE`、fault 0、同一paired strata、両seat support、authority全falseだった。

| candidate | exact change | candidate | P1 control | delta | 判定 |
|---|---|---:|---:|---:|---|
| `cg-lethal-retreat-damage-v2` | damage >=100 かつ powered bench のRETREAT +12000 | 20W-0D-76L (20.8333%) | 19W-1D-76L (20.3125%) | +0.5208pt | 弱い正差、candidate-only/STOP |
| `cg-lethal-attach-threshold-v2` | Mega LucarioへのF attachment、energy=1に +12000 | 12W-0D-84L (12.5000%) | 18W-0D-78L (18.7500%) | −6.2500pt | STOP |
| `cg-lethal-overkill-conservation-v2` | excess >100にbounded penalty、P1 lethal base維持 | 18W-0D-78L (18.7500%) | 21W-0D-75L (21.8750%) | −3.1250pt | STOP |

主要artifactは以下の通り。

- retreat root `runs/final-sprint-autonomous/cg-p1-variant-retreat-damage-96-20260814-v1`、summary `4690e1e7c0980736e6f8e16a78ab52b70eaceef05117ba99383b1b71ae98019a`、manifest-complete `60d59cf7ec42a60f55857fc22661b10567c11a68bee5c16ed54c33643070eeaf`
- attach root `runs/final-sprint-autonomous/cg-p1-variant-attach-threshold-96-20260814-v1`、summary `9aff25218a833b2df9c93556201278f16331a17b6170dbab2dc0b156f5c42f68`、manifest-complete `134b48db036b119988f492d928eb298e8a97fa1cc12527ab46cb2e0df99c3242`
- overkill root `runs/final-sprint-autonomous/cg-p1-variant-overkill-conservation-96-20260814-v1`、summary `59095487f124839c2cfd6e05ea40c90c82b8af4e48e38167545df759254a673c`、manifest-complete `02bab0fe0e7bb3cbe3fee7d246897850a734827c6a1e72de78161fa64486dd5b`

retreatの差は1勝相当未満の弱い信号であり、P1 parent更新や384昇格条件を満たさない。attachとoverkillは明確な負差のため再実行しない。P1既存ledgerにdecision-level causal linkageが無かった問題は、今回のtelemetryで候補生成の観測範囲を補ったが、screen結果の因果学習やpolicy promotionを意味しない。

## 4. 実行上の不具合と再封印

3つのscreenは評価自体を全て完了したが、初回runnerはsummaryのseat集計で再帰引数を誤り、集計段階だけで停止した。これは評価中のgame faultではない。原因を特定して `_aggregate(..., include_seat=False)` を追加し、既存のDONE/fault0 ledgerを再利用してsummary/manifestを再封印した。局のblind retryや性能値の再計算は行っていない。修正後のfocused testsは7 passedで、smokeはcandidate packageごとにDONEだった。

## 5. 判定と再開条件

現在のresearch parentはP1 `cg-lethal-target-v1 + root deck`のまま。P2は全てcandidate-onlyで、Champion、SubmissionEligibleBestKnown、production default、longrunへ昇格しない。Rule v0探索、既評価surfaceのblind retry、Student/AWR/BC、native teacher、V4/R2D3/PSROは再開しない。

再開する場合は、P1をcontrolにした新規 bounded public hypothesisだけを、workers=12/recycle=16でweighted48から開始する。positiveかつfault0・両seat support・同一strata再現の候補のみcommon24、さらに明確な再現差がある場合だけ384/768へ進める。384/768はrecycle=64。training、teacher、promotion、submission、longrun authorityはfalseのままとする。

提出についてはlocal cg verifierのPASSとremote contract確認を分離し、`submission_ready=false`、Kaggle API/UI未実施を維持する。active processはなく、production/Champion/root deck/既存artifactは不変、main worktreeへのcommit/pushも行っていない。
