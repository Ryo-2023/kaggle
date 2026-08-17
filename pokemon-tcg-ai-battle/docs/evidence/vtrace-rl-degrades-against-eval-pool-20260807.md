# V-trace RL 14 ラウンドは 4 レーンすべてで θ0 より弱くした

収集を `cabt_rule_agent_v0` とのミラー戦で行い、評価を実デッキ方策 6 体で行っていた。
方策は収集相手に対しては着実に強くなったが、評価プールへ転移せず、archaludon では
有意に悪化した。**現時点の最良方策は 4 レーンとも θ0 (BC) である。**

- 確認日: 2026-08-07
- commit: `a4e64752` (branch `feature/meta-specialist-canonical`)
- 実行: `runs/pipeline-logs/pipeline5.log`、RUN_TAG=t1、14 ラウンド完了 (03:46 - 13:24)
- 1 ラウンド = 収集 500 局 x 4 レーン + V-trace 80 step + 評価 24 局
- 評価は全て固定相手 6 体・座席均等・`--base-seed 9400000`

## 結果

θ0 と 14 ラウンド後の方策を、同じ相手・同じ seed・各 96 局で測った。

| lane | θ0 | RL 14 ラウンド後 | 差 | 両側 p |
|---|---|---|---:|---:|
| **archaludon** | 0.448 [0.35,0.55] | 0.281 [0.20,0.38] | **-0.167** | **0.016** |
| alakazam | 0.398 [0.30,0.50] | 0.295 [0.21,0.39] | -0.103 | 0.137 |
| grimmsnarl | 0.271 [0.19,0.37] | 0.208 [0.14,0.30] | -0.062 | 0.310 |
| rocket | 0.400 [0.31,0.50] | 0.339 [0.25,0.44] | -0.061 | 0.379 |

archaludon 単独で有意 (p=0.016)。加えて **4 レーンすべてが低下**しており、RL が中立なら
符号がこう揃う確率は 1/16 = 6.25% である。個々の p 値と合わせて、方向は偶然ではない。

ラウンドごとの測定 (各 24 局) を全ラウンドでプールしても同じ結論になる。

| lane | θ0 (96 局) | 全 14 ラウンド プール (312 局) |
|---|---|---|
| archaludon | 0.448 | 0.301 [0.25,0.35] |
| grimmsnarl | 0.271 | 0.282 [0.24,0.33] |
| alakazam | 0.398 | 0.425 [0.37,0.48] |
| rocket | 0.400 | 0.401 [0.35,0.46] |

前半 7 ラウンドと後半 7 ラウンドの間にも改善傾向は無い
(archaludon +0.03、grimmsnarl -0.09、alakazam -0.05、rocket +0.02)。

## 原因: 学習相手と評価相手が違う

| | 相手 |
|---|---|
| 収集・学習 | `cabt_rule_agent_v0` — **mirror 相手**。エンジン内蔵の rule agent が被験者と同じデッキを操作する |
| 評価 | `kiyotah_lucario` / `nihei_megalopunny` / `ozawa_crustle_v2` / `skarin_dragapult` / `sue124_alakazam` / `yaroslav_crustleaware_lucario` の 6 体 |

`collect-trajectories` の `--opponent-kind` 既定が `cabt_rule_agent_v0` で、
`run_overnight.sh` はこれを上書きしていなかった。`opponent.is_mirror` が真のとき
`opponent_factory=None` となり、engine 内蔵 rule agent が被験者自身のデッキを操作する
(`actor_pool_v1.py` の opponent 解決部)。

**学習は成功している。転移していないだけである。** 収集時のリターンは単調に上がった。

| round | archaludon `ret` | archaludon `V` |
|---:|---:|---:|
| 1 | -0.062 | -0.358 |
| 4 | -0.094 | 0.029 |
| 7 | 0.219 | 0.512 |
| 10 | 0.250 | 0.599 |
| 13 | **0.438** | 0.718 |

同じ期間に評価プールに対する成績は 0.448 から 0.281 へ下がった。単一相手への過適合の
典型である。

副次的に critic が楽観的である。r13 で予測 `V=0.718` に対し実測 `ret=0.438`。
alakazam も同様 (`V=0.837` / `ret=0.438`)。baseline が系統的に高いと advantage が
偏る。

## 棄却した交絡

ラウンド評価 24 局は θ0 の 96 局と同じ `--base-seed 9400000` の部分集合 (seat あたり
先頭 2 局) なので、その部分集合が難しいだけの可能性があった。θ0 をラウンド評価と
完全に同じ条件 (24 局、seat あたり 2 局、同 seed) で測り直すと **0.417 [0.24,0.61]**
で、96 局の 0.448 とほぼ一致した。部分集合の偏りではない。

## 実行の健全性

14 ラウンドで `WARN` / `FAIL` はゼロ。最終評価の fault は 96 局中 0 / 0 / 1 / 0。
パイプライン自体は設計どおり動いた。問題は方策探索の設定にある。

## critic は全レーンで楽観的

各ラウンド最終 step の報告値を 14 ラウンド平均した。

| lane | V 平均 | ret 平均 | V - ret | clip_hi | dead_rho |
|---|---:|---:|---:|---:|---:|
| archaludon | 0.398 | 0.190 | **+0.209** | 0.244 | 0.0005 |
| grimmsnarl | 0.834 | 0.652 | **+0.182** | 0.252 | 0.0004 |
| alakazam | 0.811 | 0.516 | **+0.296** | 0.332 | 0.0007 |
| rocket | 0.761 | 0.484 | **+0.277** | 0.242 | 0.0006 |

4 レーンとも +0.18〜0.30 の過大評価で、方向が揃っている。`dead_rho` は無視できるが
`clip_hi` は 0.24〜0.33 で、重要度重みの上側クリップが常時 4 分の 1 以上に効いている。

## 実施した修正 (2026-08-07)

### 学習相手をプール全体へ広げた

`collect-trajectories` に `--opponent-kinds` を追加し、計画した局に相手を巡回させる
ようにした。`run_overnight.sh` は `pool_manifest.json` から評価 6 体を除いた **60 体**
を渡す。

座席は `seat_for_game_v1`、すなわち巡回の**周回番号**から決める。`index % 2` のままだと
相手数が偶数のときに座席と巡回がエイリアスし、run 全体の `seat_counts` は均等に見える
まま、各 matchup が片側の座席でしか行われなくなる。先手の価値が大きいこのゲームでは
matchup ごとの成績が座席と交絡する。相手 1 体のときは従来の `index % 2` に一致する。

実地確認 (archaludon、8 体巡回、32 局): 8 体が 4 局ずつ、**8 体すべてが両座席**、相手
`opponent_version` が 8 種類。

### 相手の provenance が虚偽だった

`opponent_instance_id` が `f"cabt-rule-agent-seed-{env_seed+1}"` にハードコードされて
おり、登録済み相手との対局まで rule agent として記録していた。mirror が唯一の相手
だった頃の名残である。学習 (`train_from_trajectories_v1`) はこのフィールドを読まない
ので過去の学習結果には影響しないが、収集データを相手別に読むあらゆる分析が全相手を
1 つに潰してしまう。実際に打った相手を記録するよう修正した。

## 相手プールの是正 (2026-08-07)

### 観測メタ分布

`report/leaderboard-deck-analysis-0804.json` (2026-08-04 取得、6,228 チーム、自順位
2,854) の medal 圏 62 件と中位ライバル層 20 件を数えた。

| 帯 | 順位範囲 | 調査数 | 最多アーキタイプ |
|---|---|---:|---|
| 金 | 1–22 | 22 | 13 種に分散 (最多 Grimmsnarl 3 件) |
| 銀 | 23–312 | 20 | Marnie's Grimmsnarl ex 11 件 (55%) |
| 銅 | 313–623 | 20 | Marnie's Grimmsnarl ex 9 件 (45%) |
| 中位 | 2844–2864 | 20 | Mega Lucario ex 4 件 (20%) |

medal 圏合計では Marnie's Grimmsnarl ex が **37.1%** を占める。

### 3 つの別々の欠陥

**1. 相手プールが壊れていた。** `opponents/meta_*` の 7 体は 1 局も完走できず、60 体
巡回の実効は 53 体だった。原因は `build_opponent_agent_factory_v1` が読み込んだ
callable をそのまま返していたことである。`kaggle_environments` は

```python
if hasattr(agent, "__code__") and hasattr(agent.__code__, "co_argcount"):
    args = args[: agent.__code__.co_argcount]
```

で引数を切り詰めるため、`__code__` を持たない **callable object** は
(observation, configuration, ...) 全部を渡されて `TypeError` になる。`meta_*` は
`agent = make_agent(deck)` で `_GenericAgentState` インスタンスを束縛していた。
素の関数へ包む修正で 0/28 → **28/28 完走**。自分の提出 `main.py` の `agent` は
素の関数 (`co_argcount=1`) なので提出側に影響は無い。

**2. カバー率が 53% だった。** medal 圏 62 件のうち 29 件はプールに同一デッキが
無かった。金 3 件を占める Mega Lopunny ex は 1 体も持っていなかった。
`scripts/make_medal_opponents.py` で 36 デッキを追加し、**100%** へ。プールは
66 → 102 体 (学習 96 / 評価 6)。実測で 144/144 完走・fault 0。

**3. カバー率と分布は別問題だった。** 96 体を一様に回すと、medal 圏 37.1% の
Grimmsnarl は 4.8% にしかならず、1.6% の Mega Lucario が 11.9% を取る。
`scripts/make_opponent_schedule.py` が `0.7 x メタ比率 + 0.3 x 一様` の重みを出し、
`--opponent-schedule` で巡回させる。

### 加重の実装で見つけた 2 つの罠

いずれも実収集で検証して初めて出た。

- **巡回長が実行長を超えると座席が固定される。** 座席を周回番号
  `(index // len(rotation)) % 2` で決めていたため、538 件の巡回に対し 200 局を
  集めると **全 200 局が seat 0** になった。run 全体の `seat_counts` は均等に見える。
  相手ごとの対戦回数で決めるよう変更した。
- **素朴な round-robin では短い run に重みが出ない。** 相異なる相手を 1 周ずつ
  並べると、1 周に満たない run はほぼ一様になる。実測で Grimmsnarl の取り分が
  **0%** だった。stride scheduling (`(taken + 0.5) / weight` 最小を選ぶ) へ変更し、
  任意の接頭辞で比率が保たれるようにした。

### 是正の結果 (300 局の実収集で検証)

| | 一様 | 加重 |
|---|---:|---:|
| Marnie's Grimmsnarl ex (観測 28.0%) | 4.8% | **21.0%** |
| Mega Lucario ex / Hariyama (観測 6.1%) | 11.9% | **9.0%** |
| 最大乖離 | 23.3 pt | **7.0 pt** |
| 両座席で当たった相手 | — | 57 / 96 (2 局以上当たった全相手) |

残る 7 pt は `mix=0.7` が意図的に残す一様下限による。

### 権利上の扱い

追加した 36 体は既存 `opponents/meta_*` と同じ方式である。**decklist だけ**が公開
リプレイ由来で、操縦は `agents.generic_agent` の deck 非依存 rule policy であり、
元チームの agent や戦略とは無関係である。したがって「leaderboard team の再現」では
ない。全件 `usage_boundary: local_eval_only` で、提出 bundle へは入れない。

評価用 6 体は held-out のまま変更していない。過去の測定 (θ0 = archaludon 0.448 等)
がそのまま比較対象として使える。

## 次にやること

- 収集相手を評価プールと同じ分布にする。`--opponent-kind` は登録済み相手 ID を
  受け取れるので、レーンごと・ラウンドごとに 6 体を巡回させれば揃う。
- これは設計正典 L7 (opponent calibration と ascent curriculum、較正済み参照パネル、
  過去 band の rehearsal floor) が定める内容そのものであり、今回のループは
  単一 mirror 相手で代用していた。正典側の実装へ寄せる。
- critic の較正 (V > ret) を確認する。
- **θ0 を各レーンの現行最良として扱う。** 14 ラウンド分の checkpoint (64 個) は
  すべて保存済みだが、いずれも θ0 を上回っていない。
