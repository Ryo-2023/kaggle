# Offline Training v1 — 実データ Short Pilot

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-18 01:22-01:50 JST |
| 担当 | agent (Claude Sonnet 5) |
| 種別 | local experiment（実データ short pilot、long-running training 進行可否の判断） |
| commit | `e69ef63f9024c0c877fd9630f45036ff385867ea`（base）、本pilotの変更は branch `feature/offline-training-short-pilot` 上 |
| branch | `feature/offline-training-short-pilot` |
| model provenance | モデルなし（Claude Code エージェントによる手動実行、外部LLM推論の介在なし） |
| simulator / data | `kaggle_environments==1.32.0` cabt、actual 実行、本 pilot で収集 |

詳細な machine-readable 証跡は [docs/evidence/offline-training-v1-short-pilot-20260718.json](../docs/evidence/offline-training-v1-short-pilot-20260718.json)、要約は [docs/evidence/offline-training-v1-short-pilot-20260718.md](../docs/evidence/offline-training-v1-short-pilot-20260718.md) を参照。本記録はそれらの実験記録としての骨子のみを示す。

## 目的と反証条件

- **問い**: 実環境から収集した実データを Offline Training v1 の既存経路（collection→audit→training→export→package→verify→evaluation）へ通し、long-running offline training へ進める合理的根拠があるか。
- **仮説**: pipeline が実データで安全に完走し、offline fidelity と実対戦成績の両方で学習が機能した兆候が見えれば、long run へ進む価値がある。
- **反証条件**: crash/timeout/illegal action が発生する、または Rule Agent v0 に対して致命的な性能退行（例: ほぼ全敗）が見られれば `BLOCKED_RUNTIME` または `RETRY_TRAINING` とする。
- **変更点**: baseline（Final Acceptance 時点の synthetic/fixture 検証）から、actual cabt 実データでの collection・training・real match evaluation に変更。
- **固定条件**: deck（`deck.csv`）、training config（`configs/offline_training_v1/pilot.json`、games=128, epochs=20, seed=7）、champion=Rule Agent v0、max_steps=10000。

## 再現

```bash
cd ~/kaggle/pokemon-tcg-ai-battle-short-pilot

# smoke（actual, 2 games）
/usr/bin/python3 scripts/run_offline_training_v1.py collect \
  --config configs/offline_training_v1/short_pilot_smoke_actual.json \
  --run-id offline-short-pilot-smoke-actual-20260718-r1

# pilot collection（actual, 128 games）
/usr/bin/python3 scripts/run_offline_training_v1.py collect \
  --config configs/offline_training_v1/pilot.json \
  --run-id offline-short-pilot-actual-20260718-r1

# dataset build
/usr/bin/python3 scripts/run_offline_training_v1.py build-dataset \
  --config configs/offline_training_v1/pilot.json \
  --run-dir runs/offline-training-v1/offline-short-pilot-actual-20260718-r1

# training（interrupt/resume 込み）→ export/evaluate/screen/package/verify は resume が連鎖実行
/usr/bin/python3 scripts/run_offline_training_v1.py train \
  --config configs/offline_training_v1/pilot.json \
  --run-dir runs/offline-training-v1/offline-short-pilot-actual-20260718-r1
# (SIGINT を RUNNING 確認後に送信)
/usr/bin/python3 scripts/run_offline_training_v1.py resume \
  --run-dir runs/offline-training-v1/offline-short-pilot-actual-20260718-r1

# 100 match preflight + paired evaluation（新規 neural_student challenger）
MODEL=runs/offline-training-v1/offline-short-pilot-actual-20260718-r1/package/neural-student-v1/models/neural-student-v1.json
/usr/bin/python3 scripts/run_actual_agent_viability.py --challenger neural_student \
  --neural-model "$MODEL" --games 100 --base-seed 7000 \
  --canonical-base e69ef63f9024c0c877fd9630f45036ff385867ea \
  --output runs/offline-training-v1/offline-short-pilot-actual-20260718-r1/evaluation/preflight_100_seed7000.json
/usr/bin/python3 scripts/run_actual_agent_viability.py --challenger neural_student \
  --neural-model "$MODEL" --games 100 --base-seed 8000 \
  --canonical-base e69ef63f9024c0c877fd9630f45036ff385867ea \
  --output runs/offline-training-v1/offline-short-pilot-actual-20260718-r1/evaluation/paired_eval_seed8000.json
```

生成物（`runs/`, `dist/` 配下）は Git 管理外。主な artifact SHA-256 は下表と [docs/evidence/offline-training-v1-short-pilot-20260718.json](../docs/evidence/offline-training-v1-short-pilot-20260718.json) を参照。

| artifact | sha256 |
|---|---|
| pilot collection jsonl | `d6be2973d4021a7df40be3523b6976e80ee2c628e48cf89fa12eaf1821cfb06e` |
| dataset manifest_hash | `261c0f58e9df8b00183ee3ba06d26868e203aa822f302551c5a2f1d5dac7659e` |
| model_hash（export/package 共通） | `9ed2268e09a9bf52d84b508ddc552d88514c50fae0f1894caa3337d2615d4a70` |
| package archive_sha256 | `0aab9654dd55b4b3fc412d3a82f55df40fd0f0575625e95388e9ed13d4528997` |

## 結果

| condition | seeds | games | challenger win rate | timeout | illegal/invalid action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| preflight | 7000 | 100 | 0.500 | 0 | 0 | 33.0s | `CLEAN_PASS`、legal_action_rate=1.0 |
| paired eval | 8000 | 100 | 0.310 | 0 | 0 | 31.2s | `CLEAN_PASS` |
| 合算 | 7000, 8000 | 200 | 0.405（95%CI [0.339, 0.474]） | 0 | 0 | — | fallback 0/200 |

offline evaluate（test split）: neural top1=0.9394、linear baseline top1=0.9113（+0.0281）。training は 20 epoch 完走、train_loss 1.502→0.507、val_nll 1.422→0.454、val_top1 0.649→0.938、early stop 未発動。

- **sanity check**: episode/decision 件数、split 件数、hash はすべて `docs/evidence/offline-training-v1-short-pilot-20260718.json` に記録した値と一致。`build-dataset --force` の再実行で `manifest_hash` が完全一致し split 再現性を確認。
- **負の所見**: 合算 200 game で pilot Student は Rule Agent v0 に対して 40.5%（下回る）。seed 間で 50/100 と 31/100 とばらつきが大きい。offline fidelity（93.9%）と live 勝率の間に明確なギャップがある。
- **不確実性**: seed 2本・各100 gameのみであり、statistically definitiveな推定ではない（95%CIの幅は約14pt）。座席バイアスは合算後ほぼ解消（60.0%/59.0%）したが、単一 seed では座席起因かエージェント起因かを分離できなかった。

## 解釈と判断

- **観測事実**: 収集・監査・学習・resume・export・package・clean-room・100 match preflight は全て安全条件を満たした。実対戦での pilot Student 勝率は Rule Agent v0 に対し 40.5%。fallback は 0 回（常に実推論）。
- **解釈**: offline top-1 fidelity が高い一方で live 勝率が伸びないのは、128 games・compact model・20 epoch という pilot 規模の behavior cloning に典型的な compounding error（教師の決定的方策からの逸脱が対局を通じて蓄積する現象）として説明できる。pipeline やモデルロード自体の欠陥ではない（crash/timeout/invalid が 200 game 中 0 件、fallback も 0 件）。代替説明として、この特定 deck/対戦条件における座席バイアスの影響も検討したが、合算後の座席勝率がほぼ同一だったため主要因ではないと判断した。
- **判断**: **`GO_LONG_RUN`**。ただし Champion 昇格判断ではない。long run は本 pilot の勝率ギャップを埋めることを明示的な成功基準に含めるべきである。
- **言わないこと**: 本 pilot の 40.5% という数値を、大規模学習後の性能の予測値として一般化しない。2 seed・各100 gameという規模は、Promotion 判断や統計的に確定的な性能比較の根拠にはならない。
- **次 action**:
  1. long-running training（`production.json` 相当、より多くの collection games・epoch）の実行 — owner: ユーザー承認後に実行、停止条件: 計算リソース超過または crash/timeout の再発。
  2. paired evaluation の seed 数・game 数を増やし confidence interval を狭める — owner: 次セッション、停止条件: CI 幅が意思決定に十分な精度に達するまで。
  3. `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup` の pre-existing failure（0.5 秒 timeout の環境依存 flakiness）の別途調査 — owner: 未定、停止条件: 原因特定または timeout 値の妥当性検証。

## Kaggle 提出（該当時）

該当なし（本 pilot では Kaggle 提出を行っていない）。
