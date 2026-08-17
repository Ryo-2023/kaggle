# Meta-specialist teacher corpus 封印の実測（2026-08-06）

3,000 局規模の teacher corpus を封印できるようにした変更の実測記録。commit `2135174b`、
worktree `pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`、branch
`feature/meta-specialist-canonical`。すべてローカル実行であり、Kaggle への送信は行っていない。

## 対象データ

`scripts/run_parallel_lanes.py --stage collect --run-prefix t1` で収集した 4 レーン、
各 3,000 局。

| レーン | 局数 | records ディレクトリ | 推定 record 数 |
|---|---:|---:|---:|
| t1-alakazam | 3,000 | 8.7 GB | 271,100 |
| t1-archaludon | 3,000 | 3.4 GB | 未計測 |
| t1-grimmsnarl | 3,000 | 5.0 GB | 未計測 |
| t1-rocket | 3,000 | 5.2 GB | 未計測 |

record 数は t1-alakazam の 30 局標本（2,711 件）からの外挿である。

## 封印が失敗していた原因

2 箇所で、いずれも実測により特定した。

1. `scripts/seal_teacher_dataset.py` が全 record を Python dict として保持していた。
   t1-alakazam の 30 局標本で raw JSONL 96.0 MB に対し RSS 増分 354.5 MB（**3.69 倍**）。
   corpus 全体では **35.4 GB** となり OOM した（実機 47 GB、空き 40 GB）。
2. `read_exact_regular_file` は読んだ file 全体を bytes で保持し、上限は
   `MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2 = 4 GiB`。corpus は 8.6 GiB でこれを超える。

## 性能（400 局 / 32,447 record、alakazam の部分集合）

`--chunk-max-bytes 67108864 --workers 7 --shard-max-examples 20000`。

| 項目 | 対策前 | 対策後 |
|---|---:|---:|
| 封印 所要時間 | 505 s | 275 s |
| うち単一プロセス区間 | 284 s | 約 50 s |
| プロセスツリー peak RSS | 1.31 GB | 1.12 GB |
| スループット | 64 record/s | 118 record/s |

20 局標本での段階別内訳（profiler を通さない実時間計測）。

| 段階 | 時間 |
|---|---:|
| manifest 生成（streaming） | 1.7 s |
| envelope 導出（record → envelope） | 50.8 s |
| envelope 再 issue（spool から、完全検証つき） | 9.4 s |

cProfile は呼び出し回数の多い関数を過大に計上した（同一処理が 140 s に対し profiler 下で
393 s）。`typing.Mapping` に対する `isinstance` を `collections.abc.Mapping` へ変更した効果は
実時間で 140 s → 125 s であり、profiler が示した支配率とは一致しない。

## shard 読み出し（22,449 例）

| worker 数 | 時間 |
|---|---:|
| 1 | 151.7 s |
| 7 | 33.2 s |

例の内容と順序は完全一致（`a == b`）。

## worker 数の不変性

同一 `qualification_time_utc` で workers=2 と workers=8 を比較した（2 レーン、
alakazam 120 局 / archaludon 30 局）。

| レーン | corpus identity | shard identity 列 | split_counts | duplicate_cap |
|---|---|---|---|---|
| alakazam | 一致 | 一致 | 一致 | 一致 |
| archaludon | 一致 | 一致 | 一致 | 一致 |

## レーン共有キュー

レーンごとにプールを分けると、小さいレーンのコアが遊ぶ。alakazam 120 局 /
archaludon 30 局を `scripts/seal_lanes.py --workers 8` で封印し、CPU 使用率 508〜536%、
親プロセスの peak RSS 119〜125 MB を観測した。

## 実データでの不変条件

400 局の封印結果（chunk 18 / shard 18 / 32,447 例）に対して確認した。

- `record_id` の重複: 0
- 同一 episode が複数 split に跨る件数: 0
- 全 shard の `duplicate_cap` 一致: 真
- corpus digest が index の chunk 一覧から再計算一致: 真
- `duplicate_cap`: `groups_capped=1, records_capped=200, ubiquity_min_episodes=20`
  （cap が実際に発動している corpus である）

## 見つかった不具合

shard 単体の検証が corpus 全体の重複 cap を自分の断片から再導出しており、cap が発動する
corpus を複数 shard へ分けると
`training snapshot example_quality_weight does not match the duplicate cap` で封印が失敗した。
cap が発動しない小さな corpus では再現しない。

shard へ専用 `schema_version`（`specialist-training-snapshot-shard-v1`）を与え、corpus 全体
でしか検証できない 2 つの性質（cap 準拠、grouped split の非跨り）を
`read_sharded_split_examples_v1` が全 shard に対して検証する形へ移した。検証は削っていない。

## 未確認・制約

- 3,000 局 4 レーンの実封印は未実行である。上表はいずれも部分集合の実測であり、全体の所要
  時間は外挿値である。
- 収集の再現性は依然として無い（`scripts/test_sim.py` の `engine_seed_supported: False`）。
  正典 §9.4 / §14.3 の paired block は設計どおりには実現できない。
- 現行 `t1-*` は座席修正前に収集されたため、相手ごとに座席が固定されている。全体では両座席
  が揃っており、相手 identity は特徴量に入らないため BC の学習信号は歪まないが、この corpus
  から matchup 別の成績を読むことはできない。
