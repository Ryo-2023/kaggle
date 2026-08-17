# Water Box runtime-safe meta source v1

## 目的

既存の `opponents/waterbox_search_v3` は、Mega Starmie / Mega Froslass の実績あるデッキと探索付き policy を持つ一方、対戦相手としては探索予算が重く、過去の slow lane ではタイムアウトしていた。ここから提出用 policy を作るのではなく、同じ deck/policy lineage を明示的に固定し、探索の実行頻度・予算だけを fail-closed に変えた研究専用の meta source 群を生成する。

## 受入条件

- source は `opponents/waterbox_search_v3/main.py` と `deck.csv` に限定する。
- 変換は探索定数、または `_search_should_run` の周期ゲートの置換だけとする。観測境界、カード効果、deck、合法手 fallback は変更しない。
- `META_TRAIN` 8、`META_DEV` 2、`META_FINAL` 2 の独立した manifest を新規生成する。
- 生成 policy は AST static safety、compile、pool loader、exact 60-card deck を通過する。
- `local_eval_only`、research-only、promotion/submission/authority false を維持する。
- smoke は fault 0 を必須とし、P1 fixed CEM の independent re-eval を通過しない限り DEV/FINAL を読まない。

## 変換バリエーション

次の 12 variant を固定する。

- `RULE_ONLY_V2`: search proposal を停止し、Water Box の rule dispatch のみを使う。
- `MICRO_005`, `MICRO_015`, `MICRO_030`, `MICRO_070`: search budget を 0.005 / 0.015 / 0.03 / 0.07 秒へ固定する。
- `MICRO_015_EVERY_2`, `MICRO_030_EVERY_2`, `MICRO_070_EVERY_2`: 同じ予算を 2 turn 周期に限定する。
- `MICRO_015_EVERY_3`: 0.015 秒予算を 3 turn 周期に限定する。
- `EVERY_2_TURNS_V2`, `EVERY_3_TURNS_V2`, `EVERY_4_TURNS_V2`: base 予算の周期ゲートを held-out DEV/FINAL で検査する。

すべての variant は、base の探索を安全に縮小する方向だけを許可する。現在 pool の policy hash、artifact ledger、source identity と衝突する場合は seal を拒否する。

## 評価手順

1. seal と static/compile/pool-loader 検証。
2. `META_TRAIN` 8 opponent の 1 game/seat smoke。
3. P1 fixed CEM を 1 generation 実行し、screen elite を independent re-eval する。
4. positive delta、fault 0、両 seat の悪化制限を同時に満たす候補だけを昇格候補とする。満たさなければ P1 を保持し、別 source-generation method へ移る。
5. Gate 通過後だけ fresh seed の DEV、さらに未使用 FINAL を読む。

## 非目標

- `opponents/pool_manifest.json` の変更。
- P1/Champion/package の変更。
- Kaggle への提出、commit、push。
- Water Box の deck を self-owned の提出 deck として扱うこと。
