# RL 1 ラウンドの費用内訳と、収集で失われる局

RL 反復の 1 ラウンドは約 35 分で、うち収集が約半分を占める。収集の遅さは実装の
無駄ではなく対局そのものの費用が支配的であり、大きな改善余地は無い。一方、収集中に
一定割合の局が `AGENT_ERROR` で失われる問題は実在し、原因は未特定である。

- 確認日: 2026-08-07
- commit: `a4e64752` (branch `feature/meta-specialist-canonical`)
- 環境: 28 コア、torch 2.11.0+cu128、CPU 実行
- 測定は実行中パイプラインと競合した区間を含む。競合の影響を受けた値はその旨を記す

## 1 ラウンドの内訳 (実測)

| 段 | 実測 | 備考 |
|---|---:|---|
| 収集 500 局 x 4 レーン | 16-18 分 | 合計 2.06 局/s |
| 学習 80 step x 4 レーン | 約 15 分 | 0.093 step/s (64 軌跡/step) |
| 評価 24 局 x 4 レーン | 約 2 分 | |
| **1 ラウンド** | **約 35 分** | |

θ0 の基準測定 (96 局 x 4 レーン) は約 5 分だった。

### 当初見積もりが外れた理由

単一レーンでの測定を 4 レーンへ外挿していた。単一レーンでは 28 ワーカーでも 1.0 局/s
で頭打ちになるが、4 レーンを並列にすると合計 2.06 局/s まで伸びる。**並列度はワーカー
数ではなくレーン数で稼ぐ**のが正しい。

## 収集はどこに時間を使っているか

局の記録に含まれる `started_at_utc` / `finished_at_utc` から、worker が実際に対局して
いる時間を集計した。

| 構成 | 対局時間 中央値 | worker 稼働率 |
|---|---:|---:|
| 1 ワーカー | 2.69 s | — (wall の約 50% が対局時間) |
| 7 ワーカー | 3.43 s | 41% |
| 28 ワーカー | 6.16 s | 23% |

残りはプロセス起動と torch import、およびエージェント構築である。局ごとに新しい
プロセスを spawn する既定 (`persistent_worker=False`) の代償で、これは局間の状態を
分離するための設計上の選択である。

`OMP_NUM_THREADS=1` を与えても対局時間は 6.16 s → 6.14 s と変わらず、torch の
スレッド数は収集の律速ではない。

## 永続ワーカーは 1.27 倍だが、既定にはしない

`ActorPoolV1` には `persistent_worker=True` の経路があり実装・テスト済みだが、CLI へ
露出していなかった。露出させて A/B を取った (alakazam、80 局、7 ワーカー、同一 seed)。

| | wall | 完走 | 稼働率 |
|---|---:|---:|---:|
| 既定 (局ごとに spawn) | 84.4 s | 68/80 | 41% |
| 永続ワーカー | 66.3 s | 69/80 | 50% |

**1.27 倍**。対局時間そのものは 3.43 s → 3.39 s と変わらないので、消えたのは
プロセス起動と torch import だけで、局ごとのエージェント構築は残る。

採用しない理由は速度ではなく安全性である。永続ワーカーは**局ごとの OS レベル強制終了を
放棄する**。ハングした局は runtime の協調的デッドライン
(`decision_hard_timeout_ms=1000`, `game_hard_timeout_ms=300000`) でしか止められない。
無人の長時間実行では、1 レーンのハングがラウンド全体を止める。1 ラウンド 35 分に対する
13% の短縮は、この保護を手放す理由にならない。

CLI フラグ (`--persistent-worker`) は branch `perf/persistent-actor-workers` に用意
してある。使う場合は外側の実時間タイムアウトと併用すること。

## 収集で局が失われる (未解決)

収集中、一定割合の局が `agent_fault: status=AGENT_ERROR` で失われる。

| 条件 | fault 率 |
|---|---:|
| archaludon / grimmsnarl / rocket (4 レーン並列, 各 40 局) | **0 / 40** |
| alakazam (同上) | **13 / 40 (32.5%)** |
| alakazam (単独 80 局, 28 ワーカー) | 12 / 80 |
| alakazam (単独 80 局, 7 ワーカー) | 10 / 80 |
| alakazam (単独 30 局, 1 ワーカー) | 3 / 30 |
| alakazam (40 局, greedy) | 6 / 40 |
| alakazam (40 局, sample, seed 971000) | 0 / 40 |

**alakazam に強く偏る**が、同じ設定でも実行ごとに 0% から 37% まで振れる。

### 棄却した仮説

| 仮説 | 棄却の根拠 |
|---|---|
| 特定 seed の局が壊れている | 失敗した seed を単独で再実行すると完走する。同じ seed 範囲でも実行ごとに失敗する局が変わる |
| ワーカー数 (並行性) が原因 | 1 ワーカーでも 10% 発生する |
| torch のスレッド競合 | `OMP_NUM_THREADS=1` で fault 率も対局時間も変わらない |
| decoding_mode=sample が異常手を引く | greedy 6/40 に対し sample 0/40 の回があり、方向が逆 |
| CPU 競合で判断が `decision_hard_timeout_ms=1000` を超える | load average 36 の状態で失敗 seed 5 件を再実行しても、全局 DONE でエージェント例外ゼロ |

### 評価経路では起きない

同じ checkpoint・同じデッキで、`measure_opponent_strength.py` による θ0 基準測定
(各 96 局) の fault は次のとおりだった。

| lane | fault |
|---|---:|
| archaludon | 0 / 96 |
| grimmsnarl | 0 / 96 |
| alakazam | **3 / 93** |
| rocket | 1 / 95 |

alakazam ですら評価では 3% であり、収集の 32% とは桁が違う。**これは収集経路固有の
問題であって、エージェントが一般に落ちるわけではない。** 提出物の安全性に対する
懸念としては、この事実で優先度を下げてよい。

### 解決: 判断が hard timeout を超えていた (2026-08-07 追記)

再現できなかったのは、再現プローブが opponent pool の `kiyotah_lucario` を相手に
していたためだった。収集は `cabt_rule_agent_v0` すなわち **mirror** 相手で、engine
内蔵 rule agent が被験者と同じデッキを操作する。`agent_a_name="runtime_policy"` /
`agent_b_name="rule"` まで揃えて再現したところ、例外が捕まった。

```
RuntimeDecisionTimeoutError: decision exceeded the cooperative hard timeout
```

負荷依存であることも確認した (alakazam、同一 40 seed)。

| 条件 | AGENT_ERROR |
|---|---:|
| 静音 | 1 / 40 (2.5%) |
| 人工負荷 24 プロセス | 6 / 40 (15%) |
| 収集 (4 レーン x 7 ワーカー) | 約 32% |

`decision_hard_timeout_ms = 1000` は monotonic clock による**実時間**なので、並行実行が
そのまま判断の実時間を押し上げる。

### 原因は一般的な遅さではなく alakazam の裾

静音時の判断時間分布 (各 12 局)。

| lane | 中央値 | p95 | p99 | 最大 | >1000ms |
|---|---:|---:|---:|---:|---:|
| alakazam | 11.2 ms | 73.7 ms | 110.3 ms | **1585.4 ms** | 1 |
| archaludon | 9.4 ms | 19.2 ms | 25.5 ms | 70.2 ms | 0 |
| grimmsnarl | 8.8 ms | 21.4 ms | 30.1 ms | 57.0 ms | 0 |

制約の目標は p95=100 ms / p99=250 ms であり、3 レーンとも中央値・p95・p99 は満たす。
問題は alakazam の裾で、p99 の 14 倍に当たる 1.5 秒超の判断が稀に発生する。

**archaludon は最大 70 ms で hard timeout に 14 倍の余裕がある。提出時のタイムアウト
リスクは無い。** alakazam の裾の原因 (どの局面で何が重いのか) は (要検証)。

### 分かっていること

エンジン (cabt) がエージェントの例外を飲み込み、状態を `AGENT_ERROR` にするだけなので、
例外そのものが残らない。`make_agent` の返すエージェントを包んで traceback を出す
プローブを書いたが、隔離環境では再現しなかった。

再現環境と収集経路の残る差分は次の 2 つで、ここが次の調査対象である。

- 収集は記録用のラッパー (`recording_factory`) を通してエージェントを呼ぶ
- 収集の相手は `cabt_rule_agent_v0`。再現プローブは opponent pool の
  `kiyotah_lucario` を使った

### 診断可能性の修正

原因追跡が難しかった直接の理由は、必要な情報が捨てられていたことである。
`league_runtime` は捕捉した例外を `error` キーへ入れるが、`actor_pool_v1` の fault
detail は `terminal_reason` しか読んでいなかった。結果、すべての `AGENT_ERROR` が
`status=AGENT_ERROR terminal_reason=...` としか記録されなかった。

`error` を detail に含めるよう修正した (branch `perf/persistent-actor-workers`)。
回帰テストは `test_a_faulted_game_reports_the_engine_error_not_only_the_status`。
修正前のコードでは失敗する。

なお今回の `AGENT_ERROR` では `error=None` だった。エンジンが例外経由ではなく状態と
して報告しているためで、この修正だけでは原因は出ない。それでも、例外経由の fault は
今後そのまま読めるようになる。

## その他

`train-from-trajectories --value-coefficient` のヘルプが
「currently gradient-inert ... no value head exists yet」と書かれていたが誤りである。
`SpecialistPolicyModelV1` は value head を持ち、この loop は `state_value` を
`evaluate_trajectory_loss_v1` へ渡し、V-trace は現在の learner の V(x) を baseline に
使う。0 にすると policy gradient が baseline を失う。`train_from_trajectories_v1` の
module docstring が同じ誤りを既に訂正しており、ヘルプ文だけが古いまま残っていた。
branch `perf/persistent-actor-workers` で修正した。

## 未確認事項

- 収集の `AGENT_ERROR` の原因 (上記)
- 永続ワーカーで残る局ごとのエージェント構築費用を、worker 内で方策を再利用して
  削れるか。方策 identity の束縛が局ごとに要るため、単純な使い回しはできない (要検証)
