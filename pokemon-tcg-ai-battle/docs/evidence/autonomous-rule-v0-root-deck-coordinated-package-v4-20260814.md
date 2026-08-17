# Rule v0/root deck 2-card package v4（2026-08-14）

## 結論

提出互換の Rule v0 + root `deck.csv` を親に固定し、既存v1〜v3とmultisetが重複しない2-card packageを2件生成した。runtime smokeは親・候補とも各2局、6/6 `DONE`/fault0。weighted48はworkers=12/recycle=16で144局すべて`DONE`/fault0だったが、common24へ進めた候補は親と同率になり、384へは進めない。

## 候補とweighted48

| candidate | 変更 | deck SHA | multiset SHA | weighted結果 | 判定 |
|---|---|---|---|---:|---|
| `06c7d58d…` | `[1142,1182]→[3,3]` | `9da3ec8b…` | `8be047af…` | 親4/48に対し4/48（−0.0444pt） | STOP |
| `651da340…` | `[1182,1192]→[3,5]` | `98341c91…` | `93890ae1…` | 親4/48に対し6/48（weighted +4.6449pt） | common24へ |

weighted root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v4-20260814/`。manifest SHA `fed3db9cba03fe15ae7ebcc8f0d4c722ad758ee13062d3d63262917cd62acec6`、summary SHA `18693dd5ee2f7d0d5c5ab378ffb5054052b224dfb9c1f1c70d3953e3106c5292`、summary MD SHA `ab1b121f5bef667b54bf9207b1891bdbc5228908c7e30ceaa5ae27553f38875f`、runtime smoke SHA `c3ae87b78a24df92749401b9ba1e1a3d20ff3cc3ff0d6329c250c0e810ea15a4`。ResourceGovernorはnormal、12 workers admitted、throughput 18.535 games/s、restart/killなし。

## common24 guardrail

`651da340…`だけを新規seedでcommon24へ送り、親・候補各96局、計192局をworkers=12/recycle=16で実施した。親10/96、候補10/96（差0.0pt）、全192局`DONE`/fault0、両seat48/48、24 opponent、heldout training exposure=0、paired seed/GID gate PASS。common24 manifest SHA `e3667ae9598bc8bb9ccc9cd14ca9ef1b3dea35fdb1ad10ba67709af4b5d2f1b3`、summary SHA `18701430945549fbfaeafa7728727d11075fa7323b6961a901faacf85b306283`、summary MD SHA `10d85a6905f2cff7500b117eb9744897b940ee2878b2ed5bf321ca93d59cf126`。

weightedの短期positiveはcommon24で再現しなかったため、候補は`candidate-only / STOP`。384/768/1536、longrun、promotion、training、native teacher、submissionは起動しない。同じcandidate・seedのblind retryもしない。

## 境界と検証

policy closure SHA `750a8dac…`、root deck SHA `2a541d7b…`、META_TRAIN subset SHA `09176f16…`を固定。全authority flagはfalse、research-onlyであり、production/Champion/submission packageは不変。`AGENT_INVALID`/faultは勝率へ変換していない。package focused tests 10 passed、py_compile、docs validator 13 canonical、`git diff --check` PASS。現在heavy processはなし。
