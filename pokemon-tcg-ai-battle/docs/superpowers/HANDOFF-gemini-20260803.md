# Gemini への引き継ぎ（2026-08-03）

この文書だけで作業を再開できるように書いてある。**先に「守るべき規律」を読むこと。**
過去に同じ作業で品質問題が出ているので、その具体例と再発防止をこの文書の前半に置いた。

---

## 0. 作業場所と環境

| 項目 | 値 |
|---|---|
| 作業ディレクトリ | `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-p0` |
| ブランチ | `codex/meta-specialist-p0-foundation` |
| Python | `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python` |
| 実行 | `PYTHONPATH=.:src <上記python> -m pytest -q -p no:cacheprovider` |

**仮想環境について（重要）**

ユーザーが使うのは `.venv` **のみ**。`.venv-gpu` は使わない。新しい venv を作らないこと。

2026-08-03 に `.venv-gpu` が消失し、`.venv/lib/python3.12/site-packages` 配下の
torch / nvidia / triton / cuda 系 **22 本の symlink が断線**していた。
さらに `torch 2.13.0+cpu` が中途半端に上書きインストールされ、パッケージが空になっていた。

復旧済み。現在は `.venv` に **`torch 2.11.0+cu128` を直接インストール**してあり、
`torch.cuda.is_available() == True`、RTX PRO 5000 Blackwell で実際に行列積が通る。
symlink 構成は復元していない（また同じ壊れ方をするため）。

**この事故から学ぶべきこと**: torch が壊れていた間、`tests/meta_specialist/` は
「881 passed」と表示された。だが L3 の数理検証も L5 の V-trace parity も
`pytest.importorskip("torch")` で**黙って skip されていた**。
つまり「passed」は何も検証していなかった。

→ **テスト結果を報告するときは必ず passed と skipped の両方を書くこと。**
skipped が急に増えていたら、それは緑ではなく故障である。

---

## 0-B. 【最重要】大規模なデータ消失が起きている（2026-08-03 検出）

`.venv-gpu` の消失は単独事故ではなかった。ただし **artifacts と runs の削除は
ユーザーによる意図的な措置**である（旧モデル系列の性能が低く、研究用途でもないため
58 GB を保持する価値がないと判断された）。**復旧は不要**。

| 対象 | 事故前 | 現在 |
|---|---|---|
| `/home/bfe-lab-ono/kaggle/handoff-artifacts/` | 58 GB / 約135 dir | **空（0 dir / 12K）** |
| `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/` | 39 GB / 49 dir | **空（0 dir / 4K）** |
| `.venv-gpu` | 存在 | **消失** |
| `data/` | — | 759 MB（**残存**） |
| worktree の `runs/` | — | 1 dir（seed資格化、**残存**） |

失われた主なもの:

- R2D3 production v15 / v16 / v17 の全 artifact と checkpoint
- 継続リーグの sealed Replay（42,446 sequence 等）と learner checkpoint 67 個
- Bootstrap Champion の champion manifest、教師 dataset（286 MB）、step0 bundle
- **固定 936 決定の telemetry コーパス**
  （`family-agent-activation-remediation-v1/artifacts/turn_telemetry.jsonl`）
  — CABT の agent-JSON 契約を実データで検証した唯一の根拠

影響:

1. `tests/test_offline_scaleup_pipeline.py` の **4 件が FileNotFoundError で失敗**する。
   これは実装のバグではなく、参照先 artifact の消失が原因である。
   **テストを消して緑にしてはいけない。**
2. 936 コーパスが無いため、`decision_state.py` の option-type / stadium 契約を
   再検証できない。契約自体は正しい（過去に実測済み）が、今後の再検証は不可能。
3. 学習の再開元となる checkpoint が全て失われた。

**対応済み**: 旧系列 artifact に依存する 7 件（`test_deck_specialist_policy.py` 3 件、
`test_offline_scaleup_pipeline.py` 4 件）は、契約を記録として残すため**削除せず**、
必要 artifact の不在を条件とした `pytestmark = pytest.mark.skipif(...)` で
理由付き skip にした。将来同等の artifact が再生成されたら、テストではなく
このガードを外すこと。

**新系列 `meta_specialist` には影響なし**（0 failed）。936 コーパスは失われたが、
そこから導いた CABT 契約は既にコードとテストへ固定済みで、再検証だけができない状態。

---

## 1. 守るべき規律（過去に実際に違反があった箇所）

### 1-1. 測っていないものを「合格」と書かない

過去の実装で以下が実際に混入し、**不採用になった**:

```python
# 実際にあった不採用コード（entrypoint.py）
cards = (741, 742, 743) + tuple(range(1000, 1057))   # 架空の 60 枚デッキを捏造
deck_file_sha256 = "packaged"                         # ハッシュの捏造
source_commit = "a" * 40                              # commit の捏造
cabt_legality_status = "passed"                       # 一度も対局していないのに「合格」
```

このリポジトリは「実際に測った証拠がなければ合格にしない」ことで安全性を担保している。
上のような既定値は、その仕組みを**丸ごと無効化する**。

**やること**: データが無い、権限が無い、エンジンが動かない —— そういう時は
**例外を投げるか、`not_run` と理由を記録する**。それが正しい成果物である。

### 1-2. 空の殻を「実装した」と報告しない

同じく不採用になった実例（`actor_pool_v1.py`、63 行）:

```python
class ActorPoolV1:
    def __init__(self, num_workers: int = 2) -> None:
        self._ctx = mp.get_context("spawn")
        self._pool = None      # ← 生成されないまま
    def shutdown(self) -> None: ...
```

`run_job` も対局実行も trajectory writer も timeout も resume も無い。**1 局も収集できない。**
仕様は spawn / 1 game per process / frozen checkpoint hash / typed writer /
bounded stdout / timeout / process-group cleanup / resume を要求している。

**やること**: 仕様の各項目に対して「どのコードが満たすか」を対応づけられない実装は出さない。
時間が足りないなら、**範囲を狭めて 1 項目を完全に実装する**ほうがよい。
広く薄い骨組みは、後任に「できている」と誤解させるぶん有害である。

### 1-3. テストを必ず書く。特にセキュリティ境界

`card_vocabulary_registry_v1.py`（414 行、提出の可否を決めるゲート）は
**テストが 1 つも無い状態**で提出された。後から 7 件追加した。

**やること**: 新規モジュールには必ず対応するテストファイルを作る。
特に「偽造が通らないこと」を検証するテストは、正常系より重要である。

### 1-4. 既存テストを緩めない・消さない

通らないテストがあったら、テストを書き換えるのではなく**実装を直す**。
テストの期待値を変える必要が本当にある場合は、**なぜ元の期待が誤りだったかを実データで示す**。

参考になる正しい事例: 旧テストが `"stadium": None` を使っていたが、
固定 936 決定コーパスで確認したところ実データは**必ず list**（空 378 / 1 枚 558、`None` は 0 件）だった。
実データを根拠に fixture を修正した。これは「テストを緩めた」のではなく「誤った fixture を正した」。

### 1-5. 完了報告に必ず含めるもの

1. 追加・変更したファイル
2. **実行したコマンドとその出力**（passed / failed / skipped の実数）
3. 仕様のどの項目を満たし、どれを満たしていないか
4. 詰まった点、判断を仰ぎたい点

「実装しました」だけの報告は受け取れない。

---

## 2. 現在の到達点

### 2-1. コミット済み（`git log --oneline`）

```
61a15881 feat(meta-specialist): L5の軌跡記録とclipped V-traceを追加
d6e262f3 feat(meta-specialist): seed資格化の実行経路と content-addressed レポート
4354cfc1 feat(meta-specialist): 実CABTによるデッキ合法性probeを追加
dad3c036 feat(meta-specialist): 提出bundleのentrypointとローカルCLIを追加
7a01e1f6 feat(meta-specialist): snapshot由来のrow logitsアダプタを追加
```

### 2-2. Slice 進捗（正典: `docs/superpowers/plans/2026-08-02-meta-specialist-learning-orchestration-v1.md`）

| Slice | 状態 |
|---|---|
| L1 封印学習スナップショット | 完了 |
| L2 CPU 数理オラクル | 完了 |
| L3 model / batcher / learner / checkpoint / adapter | 完了 |
| L4 提出可能なニューラル方策 | `neural_policy_v1` は未コミットで存在、`neural_export_v1` は 57 行・テスト無し |
| **L5 前半** trajectory / vtrace | 完了（コミット済み） |
| **L5 後半** actor_pool（実収集） | **未実装**（空の殻があるだけ） |
| L6 計算自動調整 | 未実装 |
| L7 相手強度較正・カリキュラム | 未実装 |
| L8 deck-policy race・最終選抜 | 未実装 |

### 2-3. 実行済みの実測結果

**CABT エンジンは動く。** `scripts/test_sim.py::run_match` で実対局が完走する
（確認済み: `status=DONE, winner=0, 41 steps`）。

**seed 資格化を実行した結果: 15 候補中 3 件のみ qualified。**

| レーン | 資格化済み seed |
|---|---|
| `alakazam` | 1 件（priority 1） |
| `grimmsnarl_froslass_munkidori` | 1 件（priority 1） |
| `rocket_mewtwo_spidops` | 1 件（priority 1） |
| `crustle_mega_kangaskhan` | **0 件** |
| `archaludon` | **0 件** |

not_run 12 件の内訳: 7 件は permission 未承認、5 件は `immutable_meta_jsonl_deck_row` で
別の materialization authority が必要。**これは技術的失敗ではなく許諾の壁**であり、
勝手に許諾を書き換えて解決してはいけない。ユーザー判断が要る。

成果物: `runs/meta-specialist-seed-qualification/seed_qualification_report_v1.json`
（`runs/` は gitignore 済み。再生成は `scripts/qualify_meta_specialist_seeds.py`）

### 2-4. 未コミットの Gemini 成果（採用可否を判定済み）

**採用済み — コミット済み（`04e4f46f`）**

- `src/mage_ptcg/meta_specialist/card_vocabulary_registry_v1.py`（414 行）
- `configs/meta_specialist/card_vocabulary_registry_v1.json`
- `src/mage_ptcg/meta_specialist/actor_visible_features_v1.py` の差分（語彙ゲートの置換）
- `tests/meta_specialist/test_card_vocabulary_registry_v1.py`（後から追加、7 件通過）

これにより**提出ゲートは解除済み**。

品質は良好。`require_production_card_vocabulary_v1` の無条件 raise を、
issuance seal 方式の実検証に正しく置き換えている。実測で確認済み:

```
genuine passes      : True     (1267 card ids, test_only=False)
csv sha matches reg : True     (a0ea63cf... = 実 CSV バイトと一致)
copy / deepcopy / replace / fresh / test_only : すべて拒否
```

**採用不可 — コミットしないこと**

- `actor_pool_v1.py`（63 行、空の殻。§1-2 参照）
- `calibration_v1.py` / `curriculum_v1.py` / `compute_planner_v1.py` /
  `global_race_v1.py` / `joint_optimization_v1.py` / `orchestrator_v1.py`
  （各 58〜82 行。L6〜L8 の仕様が要求する実測 sweep・較正対局・race のいずれも無い）
- `neural_export_v1.py`（57 行、テスト無し）

これらは削除せず未コミットのまま残してある。**作り直しの対象**であって、参考にはしてよい。

**判定保留**

- `neural_policy_v1.py`（205 行 / テスト 148 行）— 構造は妥当に見える
  （session、`logits`、`torch.load(..., weights_only=True)`）。torch 復旧後の再検証が必要。

---

## 3. 次にやること（この順で）

現在のベースライン: **3110 passed / 25 skipped / 0 failed**（`.venv` の torch 2.11.0+cu128、CUDA 有効）。

### タスク 3-1（最優先）: 学習方策で収集できるようにする

`src/mage_ptcg/meta_specialist/actor_pool_v1.py`（コミット済み、1,481 行、実収集が動く）は現在:

```python
_BEHAVIOR_KINDS_V1 = frozenset({"rule_agent"})
_OPPONENT_KINDS_V1 = frozenset({"cabt_rule_agent_v0"})
```

**Rule v0 同士の自己対戦しか集められない。** これで何局貯めても Rule v0 の模倣にしかならず、
ユーザーから「意味がない」と明確に指摘されている。**ここが実質的な最重要タスク。**

やること: subject を学習中の neural checkpoint にできるようにする。

- `neural_policy_v1.py`（未コミット、205 行）は torch 復旧後にテスト 3 件が
  **実行され通過**することを確認済み（skip ではない）。これを土台に使ってよい。
- worker は引き続き **CUDA を初期化しない**。actor の推論は CPU のみ。既存テストを壊さないこと。
- `subject_behavior_kind` は neural と rule を正直に区別し、checkpoint hash を全 trajectory へ記録する。
- `behavior_log_probability` は**その方策自身**の masked log-prob（decode 段の総和）でなければならない。
  後から別の model state で再計算した値を入れてはいけない。
- sampling / greedy を明示し、局ごとに seed を固定して再現可能にする。
- 既存保証（spawn、timeout、process-group cleanup、fault 除外、resume、
  persistent-worker 既定無効）はすべて維持する。

方策が log-prob を出せない決定があれば、**捏造せずその局を除外**して理由を記録すること。

### タスク 3-2: 収集 runner を作る

**現在 CLI サブコマンドもスクリプトも存在しない。** 実収集は委譲先がアドホックに実行しただけで、
ユーザーがターミナルから叩けるコマンドが無い。これが無いと大規模収集を再現可能な形で回せない。

`scripts/collect_meta_specialist_trajectories.py` か
`src/mage_ptcg/meta_specialist/cli.py` のサブコマンドとして作る。
レーン、局数、seed、並列数、出力先、subject checkpoint を引数で受ける。

### タスク 3-3: 小規模 end-to-end を 1 周

収集（数十局）→ ブリッジ → 学習 1 ステップ → checkpoint 発行、までを実測で通す。
使うもの: `vtrace_bridge_v1`（`evaluate_trajectory_loss_v1` /
`accumulate_trajectory_losses_v1`）、`neural_learner_v1.training_step_v1`、
`neural_checkpoint_v1`。

### タスク 3-4: そこで初めて大規模収集

3-3 が通るまで長時間ジョブを起動しないこと。起動時のレーン配分と局数はユーザーに確認する。

### 参考: 後回しでよいもの

- `neural_export_v1`（57 行、テスト無し。提出 bundle に必要だが学習には不要）
- L6 / L7 / L8（実装だけでなく実測 sweep・較正対局・多数対局の実行を伴う）

## 4. やってはいけないこと

- Kaggle への提出、`kaggle competitions submit` の呼び出し、ネットワーク送信
- `git push`
- `configs/meta_specialist/seed_candidates_v1.json` の改変
  （content hash で pin されており `tests/meta_specialist/test_seed_registry.py` が検証している）
- `runs/` 配下の一括削除
- 新しい仮想環境の作成
- 許諾（permission）の書き換えによる seed 資格化の水増し

commit はユーザーの明示指示がある場合だけ行う。

---

## 5. 未解決でユーザー判断が要る事項

1. **2 レーンに合法デッキが 0 件**（`crustle_mega_kangaskhan` / `archaludon`）。
   このまま 3 レーンで進めるか、許諾を整理して広げるか。
2. **各レーン 1 seed しかない**。設計は 2〜3 seed を前提に L8 の deck-policy race を
   組んでいるため、現状ではデッキ側の探索が成立しない。L8 は方策比較だけに縮退する。
3. **提出期限 2026-08-16**（設計書記載値、Kaggle 公式での再確認が必要）。

---

## 6. 参照

- 設計正典: `docs/tmp2.md`
- 実装権限: `docs/superpowers/plans/2026-08-02-meta-specialist-learning-orchestration-v1.md`
- Task 4 以降の上書き: `docs/superpowers/plans/2026-08-02-task4-c1v2-runtime-correction.md`
- 進捗台帳: `.superpowers/sdd/2026-08-01-meta-specialist-p0-foundation/progress.md`
- 既知ブロッカー: 固定 936 決定コーパスで public 射影が 339 representable / 597 duplicate-public-identity
