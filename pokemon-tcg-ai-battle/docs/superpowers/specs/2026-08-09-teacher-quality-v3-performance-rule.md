# Teacher quality v3 baseline-comparative rule

## 結論

teacher-quality v2のactual trust setを解放しない。v2はresult ledgerのwins/draws/lossesを再導出するが、weight決定はlogical game数、fault率、teacher identityだけであり、0勝teacherにも正weightを与え得る。

v3は、同じcurrent-pool panelでexternal teacherとRule v0 baselineを独立armとして測り、performanceをweight導出へ必須化する。CABTのdeterministic replayを仮定せず、opponent×seat固定strataの独立評価を使う。

## Pre-registered ledger

各laneで次を実行する。

- arms: `teacher`, `rule-v0-baseline`
- opponents: current schedule/poolから、結果を見る前にhash順で固定したeligible 6体
- seats: 0, 1
- repetitions: 0..7
- logical games: 2 arms × 6 opponents × 2 seats × 8 repetitions = 192 / lane
- 2 lanes合計: 384 logical games
- retry: logical game identityを維持し最大1回。attempt 0のfaultを消さず、attempt 1と別rowで保存する
- 最大attempt数: 384 logical games × 2 attempts = 768
- fault: non-winとしてperformance denominatorへ残し、source exception、exit code、retry indexを保存する

最初に各lane 3 opponents × 2 seats × 1 repetition × 2 arms、計24 logical gamesをserial calibrationとして実行し、p50/p95 attempt time、fault/retry率、最大384 attemptsの見積りを固定する。calibration結果でquality thresholdを変更しない。

## Metrics

- armごとに12 opponent×seat strataのwin rateを計算する。draw、fault、未完了はprimary metricではnon-winとし、勝ちへ変換しない。
- `macro_delta = mean_strata(teacher_win_rate - baseline_win_rate)`。
- 各opponent×seat stratum内のlogical gameをarmごとに独立復元抽出し、12 strataをmacro平均する。固定seed `20260809`、20,000 replicatesでmacro deltaとteacher absolute macro win rateのone-sided 95% lower boundを計算する。attemptをbootstrap unitにしない。
- aggregateだけでなく、arm/stratum別games、wins、draws、losses、faultsをsealed resultへ保存する。
- missing/duplicate stratum、arm間panel差、policy/deck/engine/source identity差、外部SHA欠落はweight 0ではなくauthority build自体を拒否する。

## Proposed conservative weights

本表は実run前の設計値である。actual trusted digestへ追加する前に独立レビューする。

| 条件 | weight |
|---|---:|
| provenance不完全、ledger不完全、fault率がapproved上限超過、teacher absolute macro win lower `< 0.10`、`macro_delta < 0`、またはdelta lower `< -0.10` | 0.0 |
| teacher absolute macro win lower `>= 0.10`、`macro_delta >= 0`、delta lower `>= -0.10`。confidence/agreement/search strengthはunavailable | 0.4 |
| `macro_delta >= 0.05`、one-sided lower `>= -0.05`、かつactor-visible fixed-budget search agreement `>= 0.70` | 0.7 |
| one-sided lower `> 0`、search agreement `>= 0.85`、confidence/agreement/search strengthが全て実測 | 1.0 |

external teacherがhard selectionしか返さずconfidenceを公開しない現状では、performanceが良くても上限は0.4とする。confidenceをhard labelから擬似確率化しない。search evidenceを収集しない初回campaignでは0.7/1.0を出さない。

teacherとbaselineは同じsubject deck bytes、同じ6 opponent ID/policy/deck、同じseat/repetition cardinality、同じengine/source closureを要求する。policy identityと独立seed namespaceだけがarm間で異なる。engine replay capabilityを証明できない限りpaired sampleとは呼ばない。

## Overlay derivation

- teacher/lane単位のevidence classから、そのteacherの全eligible recordへ同じbase weightを割り当てる。
- record_id/content_hashはfull-corpus selectionと完全joinし、missing/extra/mismatchを拒否する。
- weight 0 recordをsilent skipしない。selectionを作り直してcomponent split authorityを更新するまではtheta0を許可しない。
- existing record内の`teacher.quality_weight`は参照しない。

## Approval boundary

- この文書だけではruleをAPPROVEDにしない。
- implementation、tests、independent review、24-game calibration wiringが通った後、canonical rule JSONのraw SHA-256を明示してtrust setへ追加する。
- 24-game calibrationはruntime/fault wiringの確認専用で、performance thresholdを満たす証拠にもtrust解放条件にもならない。full 384 logical-game ledgerを必要とする。
- run結果を見てthreshold、panel、repetitions、bootstrap seedを変更したruleは別versionとし、同じevidenceでのpost-hoc再選択をpromotion根拠にしない。
