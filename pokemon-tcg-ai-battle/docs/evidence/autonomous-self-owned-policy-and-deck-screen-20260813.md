---
project: MAGE-PTCG
document_status: evidence
canonical_source: git
language: ja
title: 2026-08-13 self-owned policy/deck screen
---

# 方針補正後のself-owned policy/deck screen

## 結論

権限を拡張せず、self-owned Rule v0の同点tie-break/action overlayとroot deck 1-card mutationを、既存broad common24のlocal evaluationへ投入した。現時点で`LONGRUN_READY_CANDIDATE`は成立しない。policy screenは候補差をseed block間で再現できず、2 armでfaultが出た。deck mutationはroot parent 10/96、mutant 9/96（−1.0417pt）で、384へ進めない。

## Policy screen

bridge `scripts/run_rule_v0_knowledge_pool_screen_v1.py` はproduction `main.py`/`agents/rule_agent.py`を変更せず、root policy closure/deck/pool/broad config/evaluator/seed/authority falseを各armへbindする。KnowledgePackはimmutable canonical bytes、action overlayはpublic option typeだけをbounded deltaとして使い、不正/unsupported時はRule v0へexact fallbackする。focused+related 48 tests、py_compile、diff-check PASS。bridge SHA `77cc7eb0f802a5d1dc3ceeb32ae96ed29dfdc93c68e75fcade1fb1a06e4c9970`、tests SHA `24eb14555b4b5dd577b1258bee1bb9289e1ab5a4f5d99b5bdf37c683bc0d8eda`、evidence SHA `bb3778d5b4c86f762e171c9d23a911f6aa4ab0ac8fd7cb0c2f624646b95a5d19`。

seed `14910000` screen（各arm 96、24×両seat×2）のsummary SHA `61af50a621392abac463a44c61bb07a142a64b1c2689f45df565b0f2f4bc9a45`:

| arm | W-D-L-F | score |
|---|---:|---:|
| baseline-no-pack | 3-0-93-0 | 3.125% |
| play-minus | 11-0-85-0 | 11.458% |
| play-plus | 11-0-85-0 | 11.458% |
| attack-plus-200 | 11-0-85-0 | 11.458% |
| play-minus-200 | 10-0-86-0 | 10.417% |

同じseed block内の方向はcandidate改善に見えるが、baselineが別fresh block `14900000`で11/96だったため、96局のpoint estimateだけで昇格しない。baseline差と候補差を分離するため、matched `14900000` runを再実行したが、baseline 14/96、play-minus 9/96、play-plus 10/95/F1、attack-plus-200 15/95/F1、play-minus-200 10/96となった。faultの具体例は`DeckValidationError: deck must contain exactly 60 cards, got 0`で、候補の2 armに発生したため候補はpromotableでない。matched summary SHA `6bdf31aa746666a92f2187a9c98b6c95b2d4f5dd942f148e9697a3b5af4d4843`。

このfaultは学習性能ではなくcandidate worker/isolated import境界の再現可能な実行不整合として扱う。fault0の候補でもseed間の安定改善がなく、古いscore-bias route（96で+9.375pt、384で−2.995pt）へ戻らない。policy routeは一旦SCREEN_ONLY/NOT_PROMOTABLEで停止し、必要なら次にfault rootを最小修正してから同一seed universeで再測定する。

### Fault matrix follow-up

同じ候補の先頭4セル（`aman_crustleaware_fighting`、両seat、repetition 2、seed `14900000..14900003`）を、単一worker direct callおよびparallel evaluator `workers=1/2`で各5回、合計80ゲーム再実行した。play-plus と attack-plus-200 のcandidate factory/KnowledgePack/action-delta単体では全80/80 DONE、fault0だった。従って、matched runの2件の`got 0`は候補の決定ロジック自体から再現できず、当該run固有のworker/import環境またはartifact状態の一過性異常として分類する。ただし元matched runはfault-inclusive評価のためSCREEN_INVALIDのまま保全し、fault0 matrixだけで性能昇格は行わない。

候補の再実行は同一base seedでfresh rootへ一度だけ行い、fault0とpaired差を確認する。候補が明確にpositiveでなければ384へ延長しない。既存production `main.py`/`agents/rule_agent.py`、native pool、permission境界は変更しない。

### Serial fault-free re-screen

worker/importの一過性を避けるため、同じbase `14900000`・同じcommon24・同じ96セルをworkers=1のfresh root `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-matched14900000-serial-v1/`で再実行した。全5 armが96/96 DONE、fault0だった。baseline-no-packは12W-1D-83L（13.0208%）、play-minusは18W-0D-78L（18.7500%）、play-plusは13W-0D-83L（13.5417%）、attack-plus-200は8W-0D-88L（8.3333%）、play-minus-200は8W-0D-88L（8.3333%）。serial rootのsummary SHAは `28e31a1cc3f8c8f5b64b86817da0ee031e69db7dcf47611d6e6e17fadf55f096`。

同じ `(opponent, seat, repetition)` のpaired比較では、play-minusがbaselineのloss→win 14、win→loss 8、draw→win 1でnet +6 winsだった。play-plusはnet +1、attack-plus-200はnet −4、play-minus-200はnet −4である。したがって、ここで唯一384へ進める候補はplay-minus（KnowledgePackのPLAY tie score -2.0）とし、他のdeltaは停止する。ただし96局の点推定であり、native 72%やlongrunを意味しない。

### 384局 confirmation

play-minusとbaselineだけを、同じcommon24、base seed `14900000`、両seat、repetition8（各384局）、workers=1でfresh rootへ実行した。root `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-play-minus-384-14900000-serial-v1/`、全768局 DONE、fault0、draw0、summary SHA `d79aba2e9b8237813cdc5a4306da83e519fe42533f077a6ddaa1398e870ea05d`。baselineは43-0-341（11.1979%）、play-minusは41-0-343（10.6771%）で、候補は−2勝/−0.5208pt。seat0はbaseline22/192対candidate21/192、seat1は21/192対20/192。pairedではcandidate loss→win 30、win→loss 32でnet −2勝だった。

96局のnet +6は再現せず、play-minusは`NOT_PROMOTABLE`、`LONGRUN_READY_CANDIDATE`ではない。768/1536やlongrunへ延長せず、他のdelta・score-bias・residual sweepへも戻らない。

## Deck screen

新規root `runs/final-sprint-autonomous/root-deck-mutation-v1/common24-96-20260813-retry-v1/`でroot parent、1-card mutant（1252→6、mutant deck SHA `eb88cc4e…`、multiset SHA `2eb35716…`）、Tomato native controlを同一common24/seed schedule `18000000`、各96局で実行した。288/288 DONE、fault0、game ID unique、seed schedule equal、authority false。

| arm | W-D-L-F | score |
|---|---:|---:|
| root parent | 10-0-86-0 | 10.417% |
| root mutant | 9-0-87-0 | 9.375% |
| Tomato native control | 68-0-28-0 | 70.833% |

mutantはparent比−1勝/−1.0417ptのため384はNO-GO。summary SHA `9a34b6fa80ceabd27b98201c5cb48bab0ae3e67afdd7b3683fb37a5aef605ad3`、manifest SHA `ef765f55ee9153ddedeca0e0ab4f1140a5771410da9adb7bb7a4af1fcf5f530c`、integrity SHA `b6eeebbe…`。Tomato controlはlocal_eval_onlyであり、submission pairやbehavior permissionを意味しない。

## 現在の判断

- native behavior permission: `NATIVE_BEHAVIOR_PERMISSION_BLOCKED`、native行動を学習入力にしない。
- Rule v0 broad baseline: fresh 11/96、fault0だが弱い。長時間化しない。
- policy tie/action: faultまたはseed不安定、SCREEN_ONLY/NOT_PROMOTABLE。
- root deck 1-card mutation: parent未達、384停止。
- Full6/B collector: ready=false、critical pathの前提にしない。
- package: Rule v0 + root deck archiveのみlocal package anchor。native/derived strong asset as-isはNO-GO。
- longrun/CABT training/submission/Champion変更: 未起動・未成立。

次の最大情報量の作業は、policy候補faultの最小原因切り分け（production変更なし）または明示的なself-rollout permission発行の確認である。再測定する場合も、同じ24 IDs、両seat、同じseed schedule、fault-inclusive denominator、native/control identityを固定する。
