# Rocket Dispatch Confidence Meta v1 実装計画

## 1. 失敗テストを先に追加

- `tests/test_rocket_dispatch_confidence_meta_v1.py` を追加する。
- recipe名の固定、未知recipe fail-closed、source変換が12 variantすべてへ適用されることを検証する。
- helperの条件表、`group_turns`の追加、hidden情報・filesystem write・import差分の拒否を検証する。
- generatorがfresh pool／meta／splitを生成し、authority false・local_eval_only・no-clobberを満たすことを検証する。

## 2. bounded generatorを実装

- `src/mage_ptcg/opponent_ingest/rocket_dispatch_confidence_meta_v1.py` を追加する。
- base sourceの厳密なsection fingerprint、既存13 key、必要なdispatch siteを確認する。
- selector定数、group evidence tracking、commit gateをexact replacementで注入する。
- static findings、import/env equality、deck hash、current pool freshness、artifact identity scanを既存fresh/derived laneと同じfail-closed契約で実装する。

## 3. CLI/configを追加

- `scripts/generate_rocket_dispatch_confidence_meta_v1.py` を追加する。
- `configs/meta_specialist/cg_rocket_dispatch_confidence_v1.json` に12 recipe、TRAIN/DEV/FINAL split、source identity、gateを記録する。

## 4. 実CABT

- まず12件ではなくTRAIN 8件だけで両seat smokeを行う。
- smokeが全局DONE/fault0ならP1 control固定CEMを1世代、population 16、elite 2、独立re-evaluation 2回で実行する。
- positive gateを満たす候補が出た場合だけ未使用DEV、続いてFINALを独立seedで確認する。
- いずれかのruntime fault、seat unsafe、lower-tail negativeで停止する。

## 5. 証拠と引き継ぎ

- `docs/evidence/cg-rocket-dispatch-confidence-meta-v1-20260815.md` にsource、recipe、SHA、CABT結果、promotion判定を固定する。
- `docs/status/current_status.md`、`docs/status/handoff.md`、`docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md` を追記する。
- focused tests、py_compile、docs validator、diff check、heavy process終了を確認する。
