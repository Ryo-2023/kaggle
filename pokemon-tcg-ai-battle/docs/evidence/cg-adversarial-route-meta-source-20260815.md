# 新規 meta source 生成：self-owned adversarial CEM と同一deck routed parent（2026-08-15）

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。新しい source 生成経路として、P1 の parameter surface を相手側へ反転して探索する self-owned source CEM と、同一 canonical deck を共有する未使用 parent pair の actor-visible routed ensemble を実行した。後者は runtime-safe で P1 に対して強い challenge signal を再現したが、P1 policy CEM の小規模候補は全て seat-collapse gate 外となった。P1、root deck、BestKnown、Champion、production、submission は不変である。

## 1. self-owned adversarial source CEM

実装は `src/mage_ptcg/opponent_ingest/adversarial_source_cem_v1.py`、runner は `scripts/run_adversarial_source_cem_v1.py`、tests は `tests/test_adversarial_source_cem_v1.py` である。terminal WDL だけを使い、action trace、private field、expert/action label は参照しない。生成 candidate は P1 parameter overlay として sealed し、authority は全て false のままとした。

修正版 campaign `runs/cg-adversarial-source-cem-20260815-b/` は、screen 64局、独立 validation 16局、全て `DONE`・fault 0 で完了した。

- screen: `31W-0D-33L`、score `48.4375%`
- screen elite: center `7W-1L`（score `87.5%`）、candidate `6W-2L`（score `75%`）
- validation: `10W-0D-6L`、score `62.5%`
- validation seat rate: seat0 `75%`、seat1 `50%`、seat gap `25%`
- `seat_safe=false` のため promoted source は `null`
- campaign result SHA: `034370f17ee369b0f25da06863845f8a9328274b23cbb6be399729c7dbded78c`

同じ P1 surface を相手化するだけでは、独立 seat-safe source を安定生成できない。今後この経路を blind retry せず、別 parent lineage／deck-conditioned source の組合せを優先する。

## 2. 異種deck routed source の runtime 契約失敗

最初に Faheem Dragapult と Prvsiyan Alakazam v10 を混ぜた `runs/cg-adversarial-route-meta-20260815-a/` を生成した。8局 smoke は全8局 fault で、wrapper の parent import 中に `StopIteration` が発生した。直接 traceback で、Faheem payload が自分の `deck.csv` を探す `next(path for path in DECK_PATHS ...)` を実行していたことを確認した。routed wrapper は候補全体で一つの deck を共有するため、異種 canonical deck の parent payload を同居させる設計が不正である。この artifact は削除・改変せず、性能結果へ昇格しない。

## 3. 同一 canonical deck の未使用 parent pair

契約を同一 canonical deck に限定し、Prvsiyan Alakazam v10 と Prvsiyan control v11（いずれも `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb`）を組み合わせた。public state（turn、yourIndex、visible active／bench、stadium、selection context）だけで親を切り替え、次の4 recipeを生成した。

| candidate | routing | policy SHA |
|---|---|---|
| `routed_alakazam_control_board_8c7510dc9a62` | `OPPONENT_BOARD_HASH_V1` | `ea00264c4eca4001f3bf86008832ecff3ec9be5676b88811a9f3d485e4e9e0aa` |
| `routed_alakazam_control_context_f9272dcf745d` | `CONTEXT_TURN_HASH_V1` | `496d4c283ecc9e57f9ec71d8d9d8f62f3bb3607b6a865aa6ba6f7ee828f5afa5` |
| `routed_control_alakazam_public_00cd3c231ee5` | `PUBLIC_HASH_V1` | `1fca88e8da8e0719d303b10478cf1b77d4a70d157bd0507eb1373854ad7eae9f` |
| `routed_control_alakazam_turn_1be882379cc7` | `TURN_PARITY_V1` | `29ac980e338bbab3a64df08c72ba7a8ed647723d62b200e5111f1840743281b0` |

generated root `runs/cg-adversarial-route-meta-20260815-b/` の pool／fresh／meta／pre-smoke split SHA は、順に `f26f14832b34303d5e483862c137be0228f9838676c457b6795b8a817f1ee5f2`／`74e880542eb8e5daffdaa39b93fc1728a8ea15a40c1fe03ca3c6d2e0f006704a`／`2246c29be97948970ea1df842ad8da3bf801f33c1c06c1e2defcc2fece6bc073`／`bc3ab8a9d85a8068fea329913622dc2c8a32e29caba45a509e0d797cd4dcc1b3`。生成時は全行 `smoke_ok=false` である。

promoted root `runs/cg-adversarial-route-promoted-20260815-b/` は、pool／fresh／rebound split SHA が `3768e6faea58c81b39ec9ffe9e9c393162ec7c4d1d01f1ee8c003abd04cf9b`／`33abeafad806da65b4155b1ccf7f4eeae0390089f1ce861fd98d8ef7afd3294f`／`575cf0fbe6c70cdfd508141caa52aea5c1fbbb7a859ccbb49600eef62f8b6d2f`。8局の両seat smoke は `DONE=8/8`、fault 0、draw 0、P1 `0W-0D-8L` だった。

## 4. 新metaの独立確認と P1 policy CEM

promoted poolを P1 subject の独立確認へ接続した `runs/cg-adversarial-route-confirm-20260815-b/` は、4候補×両seat×48局の384局を `DONE=384/384`、fault 0 で完了した。

- P1: `21W-0D-363L`、score `5.46875%`
- source側: `363W-0D-21L`
- P1の候補別／seat別 score range: `2.0833%`〜`10.4167%`
- summary SHA: `eb9b31223d48ecf6d6403b874b9f99c6e234613085073d6284177fb51e3080d7`
- evaluator summary SHA: `11a778f23048aaeb06dcf7a152b4172bdf257dcf72fbadea3f017a5f4bf05a9f`

この結果は新metaが P1 に対して強いことを示すが、4候補全てを確認に使ったため、同じ pool 内の DEV／FINAL は未使用 holdout ではない。

`runs/cg-adversarial-route-cem-20260815-b/` では P1 source/control、rebound split、`META_TRAIN_ALL`、population 4／elite 1、1 generation、positive-delta／risk-aware gate を接続した。screen は `40/40` DONE・fault 0。P1 control は新metaに `0W-0D-8L`、候補は最大 `1W-0D-7L`だったが、4件全て `seat_collapse=true`／`valid=false`。`screen_valid_candidates_below_elite_count_preserve_center` で independent re-evaluation／DEV／FINALを起動せず、P1 centerを保持した。

- campaign manifest SHA: `3abf41c5a2c111a38feec84dccfdd5e273cf65de2dcaeedaaf5ce56a5f973511`
- generation results SHA: `372e3c17492b7ea3a7af4907b2822c8b49d03d5c1c1f649b89b6c16aa7492a52`
- evaluation summary SHA: `be1634ff51fdc66a5c66583f1437df0c9e39f18b81d49e42cd836f9320d96e08`

## 5. 実装・検証・次のゲート

- `tests/test_adversarial_source_cem_v1.py`: 4 passed
- routed／cross-lineage／historical smoke／P1 CEM focused suite: 47 passed
- new module／runner／routed generator／rebind `py_compile`: PASS
- docs validator: `Validated 13 canonical documents.`
- active heavy process: なし（実験完了後確認）
- commit／push／Champion変更／Kaggle提出: 未実施

したがって、今回得られた「次に使える source」は同一deck routed poolの runtime-safe・strong-challenge artifactまでであり、P2やBestKnown candidateではない。次はこの pool をそのまま holdout として再利用せず、同一deck parentを含む別の未使用 policy lineage、または新しい deck-conditioned／behavior-family sourceを追加して、`fault0 → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を再実行する。全ゲートを満たすまで `cg_bestknown_loop_v1.py` の heavy loop は起動しない。
