# Rule v0＋root deck META_TRAIN重み付き自動探索（2026-08-14）

## 結論

提出互換の Rule v0＋`deck.csv` に、META_TRAIN 12件の重み付きカード頻度から生成した新規1枚交換候補を接続した。weighted48では4候補すべてがpositiveだったが、common24でpositiveを維持したのは2候補だけで、384確認では両候補とも親を下回った。したがって現時点のChampion、提出package、production `main.py`／`agents/`／`deck.csv` は変更せず、候補はすべてcandidate-onlyとする。768、longrun、Kaggle提出は起動していない。

## 固定した契約

- subject policy: Rule v0 package closure SHA `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- META_TRAIN subset SHA: `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- all runs: authority flags false、heldoutは評価のみ、production bytes不変
- weighted/common24はworkers=12、recycle=16。384はworkers=12、recycle=64。各段階でcandidate/controlのopponent×seat×repetition strataとseed scheduleを一致させた。

## 実測

### weighted48（parent＋4候補、240局）

root `runs/final-sprint-autonomous/rule-v0-meta-weighted-auto-search-v1-20260814/`、全240局DONE/fault0。

| candidate | mutation | weighted delta |
|---|---|---:|
| `3b6da2dc79af…` | 1102→3 | +8.364pt |
| `bf0dd7e715e7…` | 1102→1086 | +8.291pt |
| `5b33eaaf32d5…` | 1102→5 | +6.267pt |
| `07c69dc8fb93…` | 1102→1097 | +4.520pt |

manifest SHA `b492103aa5defdf1b5459da0aa895c1fde5be5492ebd29f0c4b4f6080533e3e7`、summary SHA `148c632aedba3779ab0aa174d80151805575012fadbec8647097d294d8fc4c6f`、summary MD SHA `6adf3f0c32b6a5b62e2bcfa4c7e8a60dc62f26162d2cd38529650217f317707f`。

### common24 retry-v2（parent＋4候補、480局）

初回rootはweighted48と同じgame block identityを再利用し、parentの1局が `DeckValidationError: deck must contain exactly 60 cards, got 0` になったためinvalid扱いとした。block prefixをcommon24専用へ分離し、seedをdisjointにした retry-v2 を正典とする。

retry root `runs/final-sprint-autonomous/rule-v0-meta-weighted-auto-common24-v1-20260814-retry-v2/` は全480局DONE/fault0。

| arm | W-D-L | score | delta vs parent |
|---|---:|---:|---:|
| parent | 9-0-87 | 9.375% | — |
| `5b33eaaf32d5…` (1102→5) | 11-0-85 | 11.458% | +2.083pt |
| `bf0dd7e715e7…` (1102→1086) | 13-0-83 | 13.542% | +4.167pt |
| `3b6da2dc79af…` (1102→3) | 9-0-87 | 9.375% | 0.000pt |
| `07c69dc8fb93…` (1102→1097) | 9-0-87 | 9.375% | 0.000pt |

manifest SHA `d090178d63d86166f9a24e434b29d9419626665710415cf86772256f5886e373`、summary SHA `adc8fba21888f55947af9776b9ea87aa19abee8d86b3b4e9fab3453d8efc08e0`、summary MD SHA `b36db261c35f420339d49c1deaae95fda832909270a28693aeddae3d75b93e89`。

### confirmation384（parent＋2候補、1152局）

common24 positiveの2候補だけを新規384確認へ投入した。全1152局DONE/fault0、global game ID、paired strata、seed、seatのgateはPASS。

| arm | W-D-L | score | delta vs parent |
|---|---:|---:|---:|
| parent | 49-0-335 | 12.760% | — |
| `5b33eaaf32d5…` (1102→5) | 47-1-336 | 12.370% | −0.391pt |
| `bf0dd7e715e7…` (1102→1086) | 46-0-338 | 11.979% | −0.781pt |

confirmation root `runs/final-sprint-autonomous/rule-v0-meta-weighted-auto-confirmation384-v1-20260814/`、manifest SHA `dd94d9a1510405b65489d412d17686784c2d4744c5045cf96d7b6546c8a768cb`、summary SHA `3e88c08686c882132560ed04a11368a30df3243c66504f57989b8ff0b948653c`、summary MD SHA `852b6dea0069afe6df2e61c776681c784387d560d6891948d51949f182f4928b`。

## 実装と検証

- runner: `scripts/run_rule_v0_meta_weighted_auto_search_v1.py` SHA `786e60767d785aa83c5ec19011cc2dc420c437fda1befb933e0a414a316ca45e`
- common24 runner: `scripts/run_rule_v0_meta_weighted_auto_common24_v1.py` SHA `8f184f6d572b38d9ce43f5dbe30231eecaa299de37045123b698a9a5ace44ba1`
- confirmation runner: `scripts/run_rule_v0_meta_weighted_auto_confirmation384_v1.py` SHA `bd286945c666e11bdea7bed09d0b7129e0df6ed3d4e3e644e74c51305a876d43`
- focused tests: `8 passed`（3 runner contracts＋common24 block identity＋confirmation selector等）
- `py_compile`: PASS
- `git diff --check`: PASS
- docs validator: `Validated 13 canonical documents.`

初回common24のfaultは候補性能の根拠に算入せず、retry-v2のみを採用した。再実行は同じblockを上書きせず、すべてfresh rootへ分離している。

## 次の状態

この自動deck探索は、提出互換root laneへ候補生成→weighted→common24→384の性能ゲートを接続できたが、今回の候補は384で親を超えなかった。次は同じ候補・同じsurfaceを再実行せず、(a)新規deck surface、または(b)root Rule v0の未評価policy bridgeを1件だけ選び、同じworkers12・96→384ゲートへ接続する。promotion、Champion更新、学習、提出は別の明示承認と追加evidenceが必要である。
