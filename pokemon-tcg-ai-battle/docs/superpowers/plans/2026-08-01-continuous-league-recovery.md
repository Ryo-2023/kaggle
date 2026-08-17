# Continuous League: 実戦再現性と短周期運用への移行計画

## 目的

現在の単一 Replay を無制限に反復学習する運用を止め、実際に実行可能なリモート
エージェントとの対戦経験を根拠に、短い学習窓・固定ベンチマーク・次周期への
再収集を繰り返せる状態にする。

## 受入条件

| 項目 | 完了条件 |
| --- | --- |
| Replay 根拠 | Exposure は Replay 内で観測された `policy_hash + deck_hash` の組で判定される |
| 収集 | 指定した相手ごとの局数と先後を満たす決定的な収集計画を実行できる |
| 学習窓 | `learn` で Replay 周回数から上限 update 数を指定でき、実績も保存される |
| 監視 | 進捗ファイルが死んだプロセスを永続的に `RUNNING` と誤認しない |
| 評価 | ベンチマーク構築時に実行可能な非 Rule ポリシー数を検査できる |
| 次周期 | カタログと Replay から不足相手を特定し、次の収集・学習・評価の実行計画を保存できる |

## 実装順

### 1. Replay からの対戦相手被覆率と Exposure

**対象:** `continuous_league/benchmark.py`、新規 `coverage.py`、CLI、契約テスト。

1. 封印済み Replay を走査し、相手の policy/deck hash 組、系列、window 数、遷移数、
   episode 数を集計する `ReplayCoverage` を追加する。
2. `ExposureSnapshot` に hash 組を保持させ、`EXACT_KNOWN` を catalog の存在ではなく
   観測済みの hash 組で判定する `from_replay` を追加する。
3. CLI の Exposure 構築に Replay manifest を受け取る経路を追加し、coverage report を
   artifact として出力する。

### 2. 相手ごとの均等な経験収集

**対象:** `collector.py`、CLI、収集テスト。

1. 相手ごとの偶数局 quota を検証し、各相手を先手・後手で同数にする決定的 schedule
   を作る。
2. 既存の mixture sampling は後方互換で残し、quota 指定時だけ schedule を使う。
3. manifest へ相手別・seat 別の実績を記録し、要求との差を fail-closed で検出する。

### 3. 有限の学習窓と liveness

**対象:** `learner_service.py`、CLI、設定、テスト。

1. Replay sequence 数、batch size、指定周回数から上限 update 数を算出する。
2. 学習進捗に pid/host/heartbeat、nominal replay pass、parameter norm を記録し、
   `learn-status` が stale 判定を表示する。
3. warm-up と cosine decay を設定可能にし、新しい短周期設定を用意する。既存設定の
   意味は変更しない。

### 4. 実行可能ポリシーを含むベンチマークの検査

**対象:** `benchmark.py`、CLI、テスト、runbook。

1. benchmark pool を検査し、異なる policy hash 数と submitted policy 数の下限を
   指定できるようにする。
2. 要件を満たさない benchmark は ID を発行せず、どの entry が不足しているか示す。

### 5. 次の学習周期を再現可能に組み立てる

**対象:** 新規 `cycle.py`、CLI、runbook、テスト。

1. catalog と Replay coverage を照合して、未経験の実行可能相手を quota 付きで
   列挙する cycle plan を保存する。
2. 計画には collection、seal、bounded learn、benchmark の入力 artifact と実行順を
   記録する。外部ブランチの取得や提出は実行しない。
3. runbook を短い表とコピー可能なコマンドで更新する。

## 検証

各段階で最も近い unit/CLI test を追加して実行し、最後に continuous league の
関連テスト、`git diff --check`、小さな synthetic Replay による end-to-end smoke test を
実行する。実対戦の長時間収集・学習はこの変更のテストには含めない。
