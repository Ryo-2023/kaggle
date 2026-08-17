# Rule v0/root deck Colress confirmation384（2026-08-14）

common24 で +2.6042pt だった `1192 Carmine → 1194 Colress's Tenacity` を、同一 broad pool の384局へ確認した。正典 root は `runs/final-sprint-autonomous/rule-v0-root-deck-weighted-support-item-v1-20260814-colress-confirmation384-v1/`、confirmation manifest SHA は `0d53eba02b048b8f4cd53dd5ec6749d5f03b7aa43051a220a30629bd1ec1c07a` である。

| arm | W-D-L-F / 384 | score | 親との差 |
|---|---:|---:|---:|
| parent | 40-0-344-0 | 10.4167% | — |
| Colress mutation | 41-0-343-0 | 10.6771% | +0.2604pt |

全768局が DONE/fault0。各 arm は24 opponents、seat 192/192、unique game ID 384、unique seed 384。workers 12、recycle 64、base seed `23430000`、evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`、held-out exposure 0 である。

common24 の差は384でほぼ消えた（+1 win、+0.2604pt）。従って候補は candidate-only とし、768、longrun、promotion、submission は起動しない。詳細な一次 artifact SHA と authority は同名 JSON に固定した。
