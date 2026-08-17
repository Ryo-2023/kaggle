# Offline Training v1 Support Platform - Gemini Inventory

本文書は、Offline Training v1 Support Platform の実装にあたり、調査したリポジトリの規約、API、データ形式を記録するインベントリです。

## 1. 調査項目

### パッケージ構成 (Package Layout)
- 既存の実装コードは `src/mage_ptcg/` 配下に格納されています。
- 名前空間の決定: `src/mage_ptcg/offline_training_v1_support` に配置し、外部からは `mage_ptcg.offline_training_v1_support` として import します。
- テストコードは `tests/offline_training_v1_support/` 配下に配置します。
- CLI エントリポイントは `scripts/run_offline_training_v1_support.py` に配置します。

### テスト規約 (Test Conventions)
- `pytest` を使用します。
- `PYTHONPATH=. uv run pytest` によって実行可能です。
- 新しいテストは `tests/offline_training_v1_support/` に格納します。

### JSON/JSONL ユーティリティ (JSON/JSONL Utilities)
- `src/mage_ptcg/distillation/contracts.py` に `load_records` などの JSONL 読み込みユーティリティが存在します。
- 改行コードや空行を安全に無視しつつ、行ごとの `json.loads` デコードと検証を行う仕組みが確立されています。

### アトミック書き込みユーティリティ (Atomic Write Utilities)
- `src/mage_ptcg/distillation/contracts.py` に `atomic_write_json` および `atomic_write_records` が存在します。
- 実装では `tempfile.NamedTemporaryFile` を対象ディレクトリと同じ親に作成し、書き込み後に `os.replace` を用いてアトミックにリネームしています。

### ロックユーティリティ (Lock Utilities)
- 標準ライブラリ以外の外部ロックマネージャー（Redis等）は使用できないため、ファイルベースのロック（PID、ホスト名、作成タイムスタンプを含むロックファイル）を自作して使用します。

### ハッシュユーティリティ (Hash Utilities)
- `src/mage_ptcg/distillation/contracts.py` に `canonical_json` と `digest` があります。
- `canonical_json`: `json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))`
- `digest`: `hashlib.sha256(f"mage_ptcg:{domain}:v1\0".encode() + canonical_json(value).encode()).hexdigest()`

### 対戦評価出力 (Paired Evaluation Output)
- 評価統計用に入力する per-game JSONL は、`game_id`, `seed`, `candidate_policy_id`, `opponent_policy_id`, `candidate_deck_id`, `opponent_deck_id`, `candidate_seat`, `winner` ("candidate", "opponent", "draw"), `invalid`, `crash`, `timeout`, `candidate_legal_rate`, `candidate_fallback_count`, `metadata` を持つ形式とします。

### C4 データセットレコード形状 (C4 Dataset Record Shape)
- `src/mage_ptcg/student/dataset.py` の `RuleBCExample` クラスで定義されています。
- 主要フィールド: `schema_version`, `example_id`, `source_id`, `public_state`, `own_private_state`, `visible_history`, `selection_type`, `selection_context`, `min_count`, `max_count`, `legal_actions` (各 action は `digest` と `payload` を持つ), `target_action_digests`, `teacher_ranking`, `fallback_used`, `deck_fingerprint`, `source_revision`, `metadata`。

### 安定アクションキー表現 (Stable ActionKey Representation)
- `contracts.py` の `public_action_payload` と `public_action_id` を使います。
- パブリック射影されたペイロードから `digest(payload, domain="public-action")` を用いて一意な ID を計算します。

### プライバシー規約 (Privacy Conventions)
- `contracts.py` に定義される `_PRIVATE_KEYS` (例: `token`, `email`, `cookie`, `opponent_hand`, `raw_observation` など) や `FORBIDDEN_OBSERVATION_KEYS` を含めてはいけません。
- ファイルパス (`/home/` などの文字列) を検知した場合は `_walk_safe` 等で検知・拒否します。
