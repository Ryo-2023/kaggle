# Policy Learning Gate 4 Offline比較とCABT評価

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-27 JST |
| 担当 | human 実行、Codex 集計 |
| 種別 | local experiment / ablation |
| commit | `254a9f5becd9b431b3e47807634caf123024e6ee` を基点とする未コミット policy-learning worktree |
| branch | `local/offline-scaleup-v2` |
| model provenance | Codex / OpenAI / Terra High。実験実行はローカル環境 |
| simulator / data | `runs/policy-learning-gate4/`、Rule v0 fixed population、exact current deck |

## 目的と反証条件

- **問い**: 同一Gate 3由来の実CABT dataで、BC、AWR recurrent、AWR feedforward、AWR＋Rule proposalを比較できるか。
- **仮説**: AWRがBCを実CABTで明確に上回るなら、PPO前のoffline初期化候補となる。
- **反証条件**: dataset integrity又はcandidate safetyが不通過、またはAWRが256局CABTでBCに対し明確な優位を示せない。
- **変更点**: objective（BC/AWR）、recurrent有無、Rule proposal入力だけを変更した。
- **固定条件**: 同一dataset split、exact deck、Rule v0 opponent、各候補256局、CPU CABT、candidate-only。

## 再現

```bash
bash scripts/policy_learning/run_gate4_collection.sh \
  runs/policy-learning-gate4 \
  runs/policy-learning-gate2/population-rule-diverse.json \
  runs/policy-learning-gate3c-historyfix/gate3c-clean-2000 24

bash scripts/policy_learning/run_gate4_experiments.sh \
  runs/policy-learning-gate4 \
  runs/policy-learning-gate4/primary-with-rule-proposal \
  runs/policy-learning-gate4/rule-v0-teacher-holdout \
  runs/policy-learning-gate2/population-rule-diverse.json cpu

bash scripts/policy_learning/run_gate4_cabt.sh \
  runs/policy-learning-gate4 \
  runs/policy-learning-gate2/population-rule-diverse.json 24
```

Git管理外の主要生成物は`runs/policy-learning-gate4/`である。datasetは1.1 GiBでありGitへ追加しない。

## 結果

datasetは80,133 records、78,689 trainable single-action records、episode split leakage 0、trainable Rule proposal coverage 1.0で`PASS`だった。

| condition | games | win rate | illegal action | candidate fault | p95 latency | 備考 |
|---|---:|---:|---:|---:|---:|---|
| BC recurrent | 256 | 39.06% (100勝) | 0 | 0 | 95.05 s | offline test top-1 81.80% |
| AWR recurrent | 256 | 34.38% (88勝) | 0 | 0 | 57.76 s | Value Brier 0.2674 |
| AWR feedforward | 256 | 40.23% (103勝) | 0 | 0 | 36.54 s | BCとの差は+3勝、+1.172pt |
| AWR + Rule proposal | 256 | 39.84% (102勝) | 0 | 0 | 64.68 s | 実戦改善なし |

- **sanity check**: 各CABT runはcompleted/legal=256/256、candidate fault、mapping failure、score identity failure、duplicate completionはすべて0だった。
- **負の所見**: 4候補すべてRule v0に対し50%未満で、AWR recurrentはBCより悪かった。feedforwardの点推定差は256局一回のunpaired比較であり、優位性ではない。
- **不確実性**: 単一fixed scheduleであり、paired seedはCABTが保証しない。teacher-policy holdoutはcross-policy action agreementであって実戦一般化ではない。

## 解釈と判断

- **観測事実**: Gate 4のdata経路とcandidate安全性は通過したが、現行AWRのCABT優位性は確認できなかった。
- **解釈**: 単一Rule系behavior dataのaction support不足と終端勝敗の粗いcredit assignmentが、offline AWRの改善を制限した可能性がある。
- **判断**: AWR promotionは`NO-GO`。BC recurrentを初期値とするPPO Gate 5a safety pilotは条件付きで開始可能。
- **言わないこと**: AWRが永続的に無効、PPOがRule v0を上回る、又はGate 5aがChampion変更を正当化するとは言わない。
- **次 action**: human ownerがGate 5aを実行し、fault/NaN/entropy/KL/Rule v0 regression guardのいずれかで停止したらGate 5bへ進まない。strong fixed opponentの根拠が無い間は性能主張をしない。
