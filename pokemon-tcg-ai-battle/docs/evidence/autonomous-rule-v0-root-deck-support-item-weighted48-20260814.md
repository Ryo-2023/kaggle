# Rule v0/root deck Supporter・Item weighted48（2026-08-14）

P0 の submission-compatible `Rule v0 + root deck` を固定し、既存 multiset と Judge/Explorer surface を除外した新規1-card mutationを2件だけ META_TRAIN weighted48 で screen した。根拠 artifact は `runs/final-sprint-autonomous/rule-v0-root-deck-weighted-support-item-v1-20260814/`、manifest SHA は `6aa07690b4f605309418e1a574bd24050758412b185a3b2285974b77f052b417`、weighted summary SHA は `06dee2fbb89bf2913fff99108f7175e4b397ba7fb7381f1951179f963456e1a7` である。

| arm | mutation | W-D-L-F / 48 | weighted score | 親との差 | 判定 |
|---|---|---:|---:|---:|---|
| parent | root deck | 5-0-43-0 | 0.1038043 | — | control |
| root-carmine-to-colress | 1192 Carmine → 1194 Colress's Tenacity | 7-0-41-0 | 0.1389849 | +3.5181pt | common24 eligible |
| root-switch-to-hammer | 1123 Switch → 1120 Crushing Hammer | 0-0-48-0 | 0.0000000 | −10.3804pt | candidate-only / STOP |

全144局が DONE、fault 0。各 arm は12 opponents、両 seat 24/24、unique game ID 48、unique seed 48。protocol は base seed `23410000`、workers 12、recycle 16、evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`、META_TRAIN subset SHA `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed` で固定した。held-out は weight 更新へ投入していない。

Colress は weighted screen の正値により common24 のみ eligible とする。Crushing Hammer は明確な負値のため再実行しない。common24/384/longrun、training、promotion、submission、production変更はこの screen から自動起動しない。全 authority は false である。機械分類の完全版は同名 JSON に保存した。
