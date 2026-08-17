# 2026-08-14 — Rule v0 coordinated package v8

v7までのmultisetを除外し、新seed `23705000`で2-card packageを生成した。runtime smoke後、workers=12/recycle=16でweighted48とcommon24を実行した。

| candidate | package | weighted差 | common24 |
|---|---|---:|---:|
| `a4b9cd38…` | `[1102,1123]→[3,1121]` | +0.7375pt | 11/96、親11/96（0.0pt） |
| `e46e61cd…` | `[1141,1142]→[1122,3]` | +1.2051pt | 6/96、親11/96（−5.2083pt） |

weighted48は親5W-0D-43L、候補は各4W-0D-44L相当の局結果で、全144局DONE/fault0。common24も親・候補各96局、全288局DONE/fault0、両seat/24 opponent/paired seed/GID gate PASS。候補Aは差0、候補Bは負差のため、両候補とも384/768/longrun/promotion/training/submissionへ進めない。invalidやfaultをscoreへ変換していない。

- root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v8-20260814/`
- weighted manifest `1d664051cce9d9c0fdcd013c835c0528f8a91801a45f34f42f084652e0b1e15e`
- weighted summary `4a4acc1e977dfd1452dd32b15bb7de96750ecd3881f4f24dd7012fc63bbe62a7`
- runtime smoke `544c3f0b0b40efdbd7a98bd9d389ecc0964e6d0c1be6442941d48d2d4e6b9f71`
- common24 manifest `e17b04a6fc92eabca5b26ba524eee771b31c07e8f44bc6fcac5d2c12af98776f`
- common24 summary `96feebd492b0ca823a6ad6923fccf0c1bbfe3f5b6d0091d087ad9b3b8041b6f9`

production/Champion/既存artifact/authorityは不変。次は同候補の再実行ではなく、新しいdeckまたはpublic-state policy仮説を選ぶ。
