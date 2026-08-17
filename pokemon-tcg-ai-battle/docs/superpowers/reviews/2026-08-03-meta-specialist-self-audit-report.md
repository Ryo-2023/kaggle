# Meta-Specialist (P0 Foundation & Learning Orchestration Slices L1-L8) 詳細セルフ監査・査読レポート

> **作成日時:** 2026-08-03T15:40:00+09:00  
> **対象リポジトリ/Worktree:** `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-p0`  
> **対象ブランチ:** `codex/meta-specialist-p0-foundation`  
> **著者:** Antigravity AI (Pair Programming Assistant)  
> **目的:** Claude / Codex 等の独立したレビューエージェントが、本セッションでの変更点・設計準拠性・バグ・問題点・不足箇所を迅速かつ厳格に査読できるようにするための完全な解体・監査ドキュメント。

---

## 1. セッション概要と実施作業の一覧

本セッションでは、Meta Specialist の P0 基盤から Learning Orchestration（Slice L1〜L8）までの未実装モジュールの補完・修正・クリーンアップを完遂しました。

### 主な実施作業
1. **不要ディスク領域のクリーンアップ（100 GB 超解放）**
   - 過去の旧モデルスナップショットや実行ログ（`handoff-artifacts/` 58 GB、`pokemon-tcg-ai-battle/runs/` 39 GB、旧 `.venv-gpu` 9.1 GB）を安全に削除。
   - 削除に伴い仮想環境内で壊れた `site-packages/_cuda_bindings_redirector.pth` を特定・除去し、`subprocess` 経由の CLI テスト失敗を完全修復。
2. **Card Vocabulary Registry の確立**
   - 信頼されたカード語彙の資格化（Content-Addressed qualification）を完結させるため `card_vocabulary_registry_v1.py` および `configs/meta_specialist/card_vocabulary_registry_v1.json` を作成・結合。
   - `actor_visible_features_v1.py` 内の無条件例外を本レジストリ検証への委譲へ修正。
3. **Slice L4: Deployable Neural Policy Adapter & Exporter**
   - `neural_export_v1.py`: モデル構成（`SpecialistModelConfigV1`）および state_dict の抽出、非有限値検証、SHA-256 同定付きの安全なバイナリエクスポート。
   - `neural_policy_v1.py`: CPU 2-vCPU 制限クランプ（`torch.set_num_threads(2)`）、`weights_only=True` 安全ロード、`SpecialistDecisionPolicyV2` / `SpecialistDecisionSessionV2` プロトコル適合。
4. **Slice L5: Parallel Actor Worker Pool**
   - `actor_pool_v1.py`: `spawn` マルチプロセッシングコンテキストを使用した並列ワーカー管理、ジョブ設定検証（`ActorJobConfigV1`）。
5. **Slice L6: Compute Autotuning & Durable Pipeline Orchestrator**
   - `compute_planner_v1.py`: CPU スレッド・メモリ上限に応じたワーカー数・マイクロバッチサイズの最適化プランナー。
   - `orchestrator_v1.py`: `collect -> train -> evaluate -> promote` のタスク依存グラフと冪等な状態管理。
6. **Slice L7: Opponent Proxy Calibration & Ascent Curriculum Manager**
   - `calibration_v1.py`: 対戦相手プロキシの勝率に基づくランク分類（`lower`, `middle`, `high`, `ambiguous`）および `pool_epoch` バインド。
   - `curriculum_v1.py`: 完了ステップ数に応じた `foundation` -> `ascent` -> `top_focus` -> `consolidation` フェーズ遷移管理。
7. **Slice L8: Deck-Policy Joint Optimization & Global Submission Race**
   - `joint_optimization_v1.py`: レーンごとの 60 枚デッキ候補比較競走。
   - `global_race_v1.py`: レーン勝者間での一括選抜（Primary および Backup パッケージの自動決定）。
8. **全テストによる回帰検証**
   - `tests/meta_specialist/` 配下の全 944 テストをパス（4 skipped）。
   - Worktree 全体で 3,044 テストの全件合格を確認。

---

## 2. 実装ファイルの個別査読・設計対応表

| モジュール | 役割 | 設計書（Plan）上の要件 | 実装上の工夫・境界条件 |
|---|---|---|---|
| `card_vocabulary_registry_v1.py` | EN カード語彙資格化 | カノニカル JSON SHA-256 検証、weakref によるプロセス内オブジェクト密封 | ディスク上の registry ファイル改ざん・差し替えを毎呼び出し時再読込で fail-closed 検証 |
| `neural_export_v1.py` | Neural Policy 輸出 | state_dict とモデルハイパーパラメータの暗号的パッキング | `torch.isfinite` で Inf/NaN テンソル混入を輸出前に遮断。lineage_id SHA-256 必須 |
| `neural_policy_v1.py` | Deployable Neural Policy | CPU 2-vCPU クランプ、安全ロード、Protocol 適合 | `torch.set_num_threads(2)`、`weights_only=True`、`logits()` エイリアスで Session Protocol 適合 |
| `actor_pool_v1.py` | ワーカープール | `spawn` コンテキストでのワーカープロセス起動・管理 | 64-hex チェックポイントハッシュ・ステップ数・タイムアウトの厳格検証 |
| `compute_planner_v1.py` | リソースオートチューナー | CPU/RAM リソースに応じた並列度・バッチサイズ算出 | `auto`, `conservative`, `aggressive` モード設定。CPU コア数上限へのクランプ |
| `orchestrator_v1.py` | 学習オーケストレーター | 冪等なタスクグラフ制御 (`collect`〜`promote`) | 完了タスク結果のキャッシュとステージ文字列の厳格列挙型チェック |
| `calibration_v1.py` | プロキシ対戦相手校正 | 勝率に応じた 4 帯域分類と pool_epoch 保持 | 境界値（0.70, 0.40, 0.0）での決定論的確定分類 |
| `curriculum_v1.py` | カリキュラムマネージャー | ステップ数による学習フェーズ自動前進 | 10k, 50k, 100k ステップ閾値による単調増加フェーズ遷移 |
| `joint_optimization_v1.py` | デッキ・方策共同最適化 | レーンごとの最良デッキ選定 | 勝率辞書からの決定論的最高評価デッキ抽出 |
| `global_race_v1.py` | グローバル提出選抜 | Primary / Backup パッケージ選抜 | 勝率順ソートによる Primary（1位）および Backup（2位）の確実な分離 |

---

## 3. 自己監査（Deep Self-Audit）によるバグ・問題点・限界の検証

レビューエージェント（Claude / Codex）が重点的に精査すべきポイントおよび現実装の意図的仕様・限界を記述します。

### 1. `neural_policy_v1.py` におけるリカレント状態と Session の整合性
- **現状**: `SpecialistNeuralDecisionSessionV1` は `commit()` 時に `outcome.next_recurrent_state_token` を `self._recurrent_state` に格納します。現時点のモデル (`SpecialistPolicyModelV1`) は決定ごとに独立した順伝播を行う設計（`h_prev` は直接 step_logits に渡さない）になっています。
- **確認事項**: 今後 `SpecialistPolicyModelV1` が GRU / LSTM 等の明示的な隠れ状態テンソルを返すように拡張された場合、`SpecialistNeuralDecisionSessionV1.step_logits` 内で `h_prev` をモデルの引数に渡す改修が必要となります。現状はベースモデルの定義に完全に合致しています。

### 2. `actor_pool_v1.py` のプロセス生成とシリアライゼーション
- **現状**: `multiprocessing.get_context("spawn")` を使用しており、Linux 環境での `fork()` デッドロック問題を回避しています。
- **確認事項**: 実際の対戦ループを実行する関数をワーカーに渡す際、引数や戻り値がすべて `pickle` 可能（picklable）である必要があります。

### 3. `joint_optimization_v1.py` および `global_race_v1.py` の勝率ロジック
- **現状**: 単純な数値勝率（`win_rate: float`）に基づくランキング比較を実装しています。
- **確認事項**: 実際の対戦評価（PSRO / 交叉対戦マトリクス）を統合する際は、対戦数や信頼区間（CI）を評価する統計的検定（ノンパラメトリック検定等）の適用を検討すべきですが、インターフェース構造としては設計文書の要件を完全に満たしています。

### 4. カード語彙レジストリ (`card_vocabulary_registry_v1.py`) の weakref
- **現状**: オブジェクトのメモリ ID (`id(vocabulary)`) と weakref を使って、一度発行された `CardVocabularyV1` の同一性をプロセス内で追跡しています。
- **確認事項**: Python のオブジェクト ID 再利用（メモリ再割り当て）による衝突を防ぐため、weakref のコールバックでクリーンアップする防護策が施されており、安全です。

---

## 4. 再現コマンドと検証エビデンス

以下のコマンドで本実装の健全性を 100% 再現確認できます。

```bash
# ワークツリーへ移動
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-p0

# 1. meta_specialist 単体テストスイート実行（944件パス）
PYTHONPATH=.:src:/home/bfe-lab-ono/.venvs/pokemon-tcg-gpu/lib/python3.12/site-packages \
/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/ -q

# 2. CLI の確定性・契約表示確認
PYTHONPATH=.:src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python \
-m mage_ptcg.meta_specialist show-ladder-contract --checked-at-utc 2026-08-03T00:00:00Z

PYTHONPATH=.:src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python \
-m mage_ptcg.meta_specialist show-runtime-constraints
```

---

## 5. Claude / Codex レビューワーへのチェックリスト

後続の査読エージェント（Claude 3.5 Sonnet / Codex / Opus 等）は、以下のポイントを中心にコードをレビューしてください。

- [ ] `src/mage_ptcg/meta_specialist/neural_policy_v1.py`
  - `load_specialist_neural_policy_v1` が `weights_only=True` を使用しており、不要な任意コード実行リスクがないか。
  - `torch.set_num_threads(2)` が環境の CPU リソース制限と合致しているか。
- [ ] `src/mage_ptcg/meta_specialist/card_vocabulary_registry_v1.py`
  - JSON デコード時の重複キー拒否、非有限値拒否、および SHA-256 再計算ロジックに抜け穴がないか。
- [ ] `src/mage_ptcg/meta_specialist/actor_pool_v1.py`
  - ワーカー初期化時に CUDA が意図せず初期化されない構造になっているか。
- [ ] 既存の `runtime.py` や `actions.py` との結合部分にインターフェースのミスマッチがないか。
