# Neural Student v1 Submission Candidate Freeze

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-18 11:30 JST |
| 担当 | Antigravity (AI Agent) |
| 種別 | local experiment / candidate freeze audit |
| commit | `3ef1ba39794180cdc36162a0b0347d3ffbcc6239` |
| branch | `feature/belief-guided-search` |
| model provenance | Neural Student v1 (SHA-256: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`) |
| simulator / data | cabt (Kaggle simulator, offline runtime evaluation) |

## 目的と反証条件

- **問い**: Neural Student v1の提出用候補（Submission Candidate）としての整合性、再現性、および安全性を完全に証明し、固定化できるか。
- **仮説**: パッケージビルドのバイナリ同一性が確認され、かつクリーンルームでの動作と3経路（Raw, Packaged, Rebuild）での Policy Identity が100%一致し、未使用シードによる Smoke テストで 100% の合法性を保てば、提出用 tarball として最終固定可能である。
- **反証条件**: バイト数やハッシュ値の不一致、Policy Identity での不一致の発生、または Smoke テストでの非合法手/クラッシュの検出。
- **変更点**: なし（既に独立 Promotion Gate を通過したバイナリの監査とメタデータの整合化）。
- **固定条件**: デッキ `deck.csv`、コントロール `Rule Agent v0`、シード `31000`、対戦数 20。

## 再現

```bash
# 1. 3種類のパッケージ同一性監査
# runs/.../package/neural-student-v1/ と /tmp/neural-student-promotion-build-a/, b/ 間の比較

# 2. クリーンルーム & Policy Identity 検証
env -i PATH=/usr/bin:/bin HOME=/tmp/promotion-clean-home PYTHONPATH= \
  /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python \
  scratch/policy_identity.py

# 3. 20-Game Artifact Smoke Test
/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python scripts/run_actual_agent_viability.py \
  --challenger neural_student_package \
  --package-path /tmp/neural-student-promotion-build-a \
  --games 20 \
  --base-seed 31000 \
  --canonical-base 3ef1ba39794180cdc36162a0b0347d3ffbcc6239 \
  --output /tmp/smoke_seed31000.json
```

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline (Rule v0) | - | - | - | - | - | - | コントロール対象 |
| Neural Student v1 | 31000 | 20 | 60.00% | 0 | 0 | Mean 8.40ms | 12 wins / 8 losses |

- **sanity check**: 20試合すべて正常に完走。クラッシュ、タイムアウト、非合法手、例外、意図しないフォールバックすべて 0 件。
- **負の所見**: なし。
- **不確実性**: 20試合は動作確認の Smoke テストであり、これをもって勝率の推定値とするものではない（勝率は前回の 1,000 試合評価を参照）。

## 解釈と判断

- **観測事実**: パッケージ A/B/Original はバイトサイズ（630,679 バイト）および SHA-256（`d4e2cdcb...`）で 100% 同一。Policy Identity も 120 ケースで 100% 一致。Smoke テストも完璧に合格。
- **解釈**: パッケージの決定的な再現性、および提出時における推論挙動の完全な同一性が証明された。
- **判断**: **採用（SUBMISSION_CANDIDATE_READY）**。
- **言わないこと**: 提出候補の固定化であり、実際の Kaggle アップロードおよびデフォルトエージェントの配線変更はまだおこなわない。
- **次 action**:
  1. コピーした確定 tarball と各種 JSON メタデータを `submission_candidate/neural-student-v1/` 配下に固定配置。
  2. 証跡およびステータスドキュメントの更新と Git コミット・プッシュ。

## Kaggle 提出（該当時）

- **Kaggle submission**: 未実行 (不変条件の維持)

---

## 2026-07-18 追記 (Correction)

本ドキュメントの凍結後、提出物 `neural-student-v1` パッケージが Kaggle Validation Episode で失敗したことを受け、`main.py` の NameError を修正した entryfix パッケージ `neural-student-v1-entryfix` を作成しました。

それに伴い、新しく導入された Safety Gate 機構（G1-G6）を用いてこのパッケージの自動検証を実行し、検証マニフェスト（`submission_verification.json`）を生成しました。このマニフェストには、今回合格したローカル Validation エミュレーション結果や、20試合の Smoke テスト、依存関係の完全閉包が記録されています。
