# Rule v0/root deck Colress common24（2026-08-14）

weighted48 で唯一正値だった `1192 Carmine → 1194 Colress's Tenacity` を、P0 `Rule v0 + root deck` と同一 seed schedule の broad common24 guardrail へ拡張した。正典 root は `runs/final-sprint-autonomous/rule-v0-root-deck-weighted-support-item-v1-20260814-common24-retry-v3/`、common24 manifest SHA は `98d484667946fc24e9cf94b335c75aa702c792d24e61c1a71a283f77931be56b` である。

| arm | W-D-L-F / 96 | score | 親との差 |
|---|---:|---:|---:|
| parent | 8-1-87-0 | 8.8542% | — |
| Colress mutation | 11-0-85-0 | 11.4583% | +2.6042pt |

全192局が DONE/fault0。各 arm は24 opponents、seat 48/48、unique game ID 96、unique seed 96。workers 12、recycle 16、base seed `23420000`、evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`、held-out exposure 0 である。

差は正だが小さく、これは common24-positive の candidate-only 証拠であり、384/768/longrun・promotion・submission を自動起動する根拠ではない。先行 root は相対 deck path の一時 fault、retry-v2 は stdin spawn failure であり、いずれも得点へ算入していない。詳細 SHA と authority は同名 JSON に固定した。
