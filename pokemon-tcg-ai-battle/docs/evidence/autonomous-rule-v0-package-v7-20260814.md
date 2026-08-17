# 2026-08-14 — Rule v0 coordinated package v7

既評価multisetを自動除外する2-card package generatorを新seed `23703000`で実行し、P0 Rule v0＋root deckの新規候補2件をruntime smoke後にweighted48へ投入した。両候補とも全48局DONE/fault0だったが、親を下回ったためcommon24へ進めず停止した。

| arm | package | 結果 | weighted差 |
|---|---|---:|---:|
| parent | root Rule v0＋root deck | 5W-0D-43L / 48 | — |
| candidate `b8dbe1c5…` | `[1102,1142]→[3,5]` | 4W-0D-44L / 48 | −2.6789pt |
| candidate `e02fe41e…` | `[1123,1227]→[3,3]` | 4W-0D-44L / 48 | −2.3188pt |

両seat24/24、META_TRAIN 12 opponent×両seat×2 repetition、GID/seed/identity gate、ResourceGovernor normal、workers=12/recycle=16、authority全falseを確認した。`AGENT_INVALID`やfaultは勝率へ変換していない。両候補negativeのためcommon24/384/768/longrun/promotion/training/submissionは起動しない。同候補のblind retryも行わない。

- root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v7-20260814/`
- candidate `b8dbe1c5a0b29bf7ea0b340df79bad57a4d1e2d7db02e44febca9c42304d04d7`、deck SHA `41ce4d67e8714f8f9d23b1144fd386691a623669700b6bd32c3733934ffa6506`、multiset SHA `0a4bd31e38ccba43afed8e2c5d9737d5fef39fdc7304e832a024eb463ec18ae7`
- candidate `e02fe41e52511e608857156e2e105640d656038ab20cfce641f8ed988ed3fbb8`、deck SHA `fd190a794402a74269d8fde0e56c4cb20473129b9fa17fb58857745451524839`、multiset SHA `90aef2a9a24bb4e3a8b5c91876aef7f56a7c1d885d6d13b367fe35b0cf121d41`
- manifest SHA `16d92ecb9637a38802ed812065b59364eef3f1dbbb98728e019a57f278ea0267`
- runtime smoke SHA `5116531ee619a0d894c2dde64aa3ff151c2156ec757dc3cc4bb34f2997b846ec`
- weighted summary SHA `c96901229953aa107635e255b190a1fa8b1812586335bd82959f14e5c40b6275`
- weighted MD SHA `ff2dd09f778fad3e0461c9e7bdaba99d9d0921549bdd759d84eab4065287b405`

production `main.py`/`agents`、Champion、submission package、既存artifactは不変。focused tests、py_compile、docs validator、git diff-checkは別途実行する。
