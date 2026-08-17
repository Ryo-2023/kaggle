# Rule v0/root deck novel v7（2026-08-14）

既存153 multisetと既評価surfaceを除外し、`674 Hariyama→673 Makuhita` と `1152 Poké Pad→1102 Dusk Ball` をweighted48でscreenした。

| arm | W-D-L-F | weighted delta | 判定 |
|---|---:|---:|---|
| parent | 2-0-46-0 | control | control |
| Hariyama→Makuhita | 10-0-38-0 | +17.4455pt | common24 eligible |
| Poké Pad→Dusk Ball | 0-0-0-48 | 未集計 | `AGENT_INVALID` 全48局、invalid保全 |

Hariyama候補はfault0、両seat24/24、identity/GID/seed gate PASS。Poké Pad候補は全局 `AGENT_INVALID` のため、勝敗・勝率・性能差へ変換せず、全arm summaryの `all_faults_zero=false` を維持した。無効armを含むrootからcommon24へは進めず、Hariyama候補＋parentだけを別fresh common24へ送った。

Hariyama-only common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-novel-v7-common24-96-20260814` は親10/96、候補9/96（−1.0417pt）となった。両arm192局DONE/fault0/draw0、seat48/48、24 opponent、GID/seed gate PASS。weighted48の+17.4455ptは再現せず、candidate-only/STOP、384/longrun未起動である。common24 manifest SHA `44cc62bcf1f4305443c8f0338601ec74f02c7db43243ff9d8e03fe1b49a5ab84`、summary SHA `e02d9f045de6422bb5463e369f4dd051301ed3c35cec7dd335df33525fff2bcd`。

Evidence JSONは同名JSON（manifest SHA `d2908c075d91be4c2d4d9f7bb95d006d96286569cea04832df8f6788ec2a9952`、summary SHA `d9aa2971789637845fb5daddb2877d2bdff9089dd2e260b29189394d635a3637`）に固定する。全authority false、training/promotion/longrun/submissionなし。
