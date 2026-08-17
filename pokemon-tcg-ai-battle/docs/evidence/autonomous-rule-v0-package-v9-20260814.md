# 2026-08-14 — Rule v0 coordinated package v9

既存multisetを除外し、generator seed `23708000`で2-card packageを生成した。runtime smoke後、workers=12/recycle=16のweighted48を実行したが、両候補とも親を大きく下回った。

| candidate | package | weighted差 | 判定 |
|---|---|---:|---|
| `4b049839…` | `[1152,1152]→[3,1121]` | −7.3836pt | STOP |
| `9664c8d2…` | `[1182,1227]→[3,1122]` | −10.6352pt | STOP |

全144局DONE/fault0、両seat24/24、12 META_TRAIN opponent、paired seed/GID/identity gate PASS、authority false。両候補negativeのためcommon24/384/768/longrun/promotion/training/submissionは起動しない。invalid/faultを勝率へ変換していない。

- root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v9-20260814/`
- manifest `80e4d75f64f40789fdbf14160c55208792b2c8485dbbdb22299056a17dce7947`
- weighted summary `670a661cdd6423c31a131ebf693b79398fdb7105856aa59eb06918d470daf10f`
- runtime smoke `388fc4ed9e788e1ceef6c7eba94c20d09726a3522cb4229eb87c4133a456bb82`

production/Champion/既存artifact不変。v9はhard-negativeとして保存し、同候補blind retryはしない。
