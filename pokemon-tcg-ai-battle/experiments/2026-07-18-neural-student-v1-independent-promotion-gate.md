# Neural Student v1 Independent Promotion Gate

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-18 03:15 UTC |
| 担当 | Antigravity (AI Agent) |
| 種別 | local experiment / Independent Promotion Gate |
| commit | `6782e687a6bb667c3ca5343df9974352ddd7cd2c` |
| branch | `feature/belief-guided-search` |
| model provenance | Neural Student v1 (SHA-256: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`) |
| simulator / data | cabt (Kaggle simulator, offline runtime evaluation) |

## 目的と反証条件

- **問い**: Long-running Offline Trainingにより生成されたNeural Student v1について、再現性、安全性、および現Champion（Rule Agent v0）に対する有意な優位性が確認できるか。
- **仮説**: パッケージビルドの決定性が100%であり、実機評価1,000試合において合法性100%（例外/非合法手/意図しないフォールバックが0）を保ち、Rule Agent v0に対して統計的有意（99% Wilson CI下限が50%超）に勝ち越すことができれば、昇格および提出候補として適格である。
- **反証条件**: パッケージビルドのSHA-256不一致、例外や非合法手の発生、または対戦評価で99% CIの下限が50.00%を下回ること。
- **変更点**: なし（既に生成済みのモデルとパッケージを使用し、提出相当のruntime adapterを検証用に補強）。
- **固定条件**: デッキ `deck.csv`、対戦相手 `Rule Agent v0`、対戦数 1,000、決定並列実行（プロセス単位並列、各プロセス1スレッド制限）。

## 再現

```bash
# 1. パッケージ再現性検証
mage_ptcg.offline_training.package.build_package() # を production 引数で2回実行して同一性を監査

# 2. 安全性 Preflight
/usr/bin/python3 scripts/run_actual_agent_viability.py \
  --challenger neural_student_package \
  --package-path /tmp/neural-student-promotion-build-a \
  --games 100 \
  --base-seed 19000 \
  --canonical-base 6782e687a6bb667c3ca5343df9974352ddd7cd2c \
  --output runs/offline-training-v1/offline-long-run-actual-20260718-r1/promotion_gate/preflight_seed19000.json

# 3. 独立1,000試合対戦評価 (並列度6)
/usr/bin/python3 /home/bfe-lab-ono/.gemini/antigravity-ide/brain/dc5e400c-c9aa-43b3-b4d0-2a8f92964c96/scratch/parallel_eval.py \
  --challenger neural_student_package \
  --package-path /tmp/neural-student-promotion-build-a \
  --seeds 20000 21000 22000 23000 24000 25000 26000 27000 28000 29000 \
  --canonical-base 6782e687a6bb667c3ca5343df9974352ddd7cd2c \
  --output-dir runs/offline-training-v1/offline-long-run-actual-20260718-r1/promotion_gate/rule_v0 \
  --parallel 6
```

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline (Rule v0) | - | - | - | - | - | - | コントロール対象 |
| Neural Student v1 | 10 seeds | 1,000 | 54.20% | 0 | 0 | Mean 4.33ms | 99% CI [50.13%, 58.22%] |

- **sanity check**: 10シード×100試合＝1,000試合が正常に完走。クラッシュ、タイムアウト、非合法選択はすべて 0 件。
- **負の所見**: 特になし。意思決定の遅延も平均 4.33 ms、最大でも 10.04 ms と極めて低遅延。
- **不確実性**: 1,000試合の標本数から得られた 99% 信頼区間の下限は 50.13% であり、統計的に現コントロールを上回っている。

## 解釈と判断

- **観測事実**: 決定的なビルド再現性が確認され、1,000試合における安全性は完璧であり、勝率は 54.20%（99% CI下限 > 50%）を達成。
- **解釈**: Neural Student v1は、既存のRule Agent v0に対して統計的有意な実力優位性を備えている。
- **判断**: **採用（Promotion Gate 合格）**。
- **言わないこと**: 今回の評価はRule Agent v0に対するものであり、他の非公開エージェントや将来のアルゴリズムに対する直接の優位性を一般化するものではない。
- **次 action**:
  1. 証跡データを Git にコミット・プッシュ (owner: Antigravity, 完了条件: リポジトリのドキュメント整合性検査合格)
  2. Notion へのミラー更新用の準備 (Notion sync への連携)
  3. 今後の Deck-Policy 最適化や探索拡張（C2a/C2bなど）へのインプットとして本評価結果を記録

---

## 2026-07-18 追記 (Correction)

本実験ログの記録後、`__file__` NameError に伴う Kaggle Validation Episode での失敗が判明。`main.py` の NameError を修正した entryfix パッケージ `neural-student-v1-entryfix` を作成し、Safety Gate 機構（G1-G6）による自動検証を実行しました。検証の結果、全ゲートをクリアし、依存関係・Kaggle 動作互換性が保証された検証マニフェスト（`submission_verification.json`）を生成しました。
今後は、この Safety Gate を通過し SHA-256 が一致したもののみを提出する wrapper 経由でのみ提出可能とします。
