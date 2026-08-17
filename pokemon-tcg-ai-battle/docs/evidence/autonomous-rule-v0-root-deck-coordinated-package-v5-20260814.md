# Rule v0/root deck 2-card package v5（2026-08-14）

既存v1〜v4とmultisetを分離した2件をgenerator seed `23695000`で生成した。runtime smokeは親＋候補2件の6/6 `DONE`/fault0、weighted48は144/144 `DONE`/fault0、workers=12/recycle=16だった。

| candidate | 変更 | weighted48 | common24 | 判定 |
|---|---|---:|---:|---|
| `0b49700b…` | `[1152,1182]→[3,3]` | 親2/48→5/48（+6.2545pt） | 親13/96、候補13/96（0.0pt） | STOP |
| `fc0bfd8d…` | `[1141,1227]→[5,3]` | 親2/48→6/48（+8.9595pt） | 親13/96、候補11/96（−2.0833pt） | STOP |

weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v5-20260814/`、manifest SHA `ded751844988cb7500f4c4e13122994cf0158537783a375dc3bc542d1f744879`、summary SHA `7cd537df3bd822b5544ef2d5c78d2fe5a142c4f14eb7a2e65104c39a4e4b48f2`、runtime smoke SHA `bdf814e1eba25924a38fe7b04df2f3320d887515dfebfc2bca0175985a6e91fa`。common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v5-common24-20260814/`、manifest SHA `d7da287c87e8ab5b1e660898be3328d8d1811dc27d01cf4a9b6bb6e6458c5569`、summary SHA `6674c5df7f62c3a0d52a9d4b0e7970e886afc2ad1c46104e7e8b68e6758d3243`。全authority false、heldout exposure 0、paired/seat/GID/seed gate PASS。384/768/longrun/promotion/submissionは起動しない。
