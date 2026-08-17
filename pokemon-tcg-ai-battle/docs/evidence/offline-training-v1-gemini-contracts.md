# Offline Training v1 Support Platform - Gemini Contracts

本文書は、各データ型およびメタデータの構造を定義するスキーマ契約書です。

## 1. 共通ポリシー

- **`schema_version`の必須化**: すべてのレコードは有効な `schema_version` フィールドを保持しなければならず、未定義または非互換の場合は拒否されます。
- **Required Field検証**: 必須項目が欠落しているレコードは即座に拒否 (Reject) されます。
- **未知のフィールド (Unknown Field) の扱い**: 原則として保持するか無視し、エラーにはしません。
- **プライバシー安全**: 非公開のゲーム情報やパスなどの個人特定情報、認証トークンなどはログ出力およびパブリックレポートに含まないよう除外または難読化します。
- **非有限数値の拒否**: `NaN` や `Infinity` のような非有限値が含まれるレコードは拒否します。
- **ハッシュ算出**: リポジトリ規定の `canonical_json` および SHA-256 に従う `stable canonical JSON hash` を一貫して使用します。
- **確定的順序 (Deterministic Ordering)**: キーやレコードの出力順はソートされ、決定論的になります。

---

## 2. スキーマ定義

### game result record
対戦ごとの結果を記録するスキーマ。
```json
{
  "schema_version": "support-game-result-v1",
  "game_id": "str (UUID or hash)",
  "seed": "int",
  "candidate_policy_id": "str",
  "opponent_policy_id": "str",
  "candidate_deck_id": "str",
  "opponent_deck_id": "str",
  "candidate_seat": "int (0 or 1)",
  "winner": "str ('candidate' | 'opponent' | 'draw')",
  "invalid": "bool",
  "crash": "bool",
  "timeout": "bool",
  "candidate_legal_rate": "float",
  "candidate_fallback_count": "int",
  "metadata": "dict"
}
```

### decision diagnostic record
エージェントの行動決定診断ログのスキーマ。
```json
{
  "schema_version": "support-decision-diagnostic-v1",
  "episode_id": "str",
  "decision_id": "str",
  "seat": "int",
  "selection_type": "str or null",
  "context_type": "str or null",
  "state_digest": "str",
  "candidate_digest": "str",
  "teacher_action_key": "str",
  "student_action_key": "str",
  "teacher_scores": "dict or null",
  "student_scores": "dict or null",
  "student_margin": "float or null",
  "student_entropy": "float or null",
  "is_error": "bool",
  "fallback_used": "bool",
  "metadata": "dict"
}
```

### dataset registry record
データセット登録用スキーマ。
```json
{
  "schema_version": "support-dataset-registry-v1",
  "dataset_id": "str",
  "parent_dataset_ids": "list of str",
  "dataset_hash": "str",
  "feature_schema_hash": "str",
  "episode_count": "int",
  "decision_count": "int",
  "candidate_count": "int",
  "split_hashes": "dict",
  "shard_hashes": "list of str",
  "privacy_status": "str",
  "validation_status": "str",
  "source_collection_hash": "str",
  "created_at": "float",
  "updated_at": "float"
}
```

### model registry record
モデル登録用スキーマ。
```json
{
  "schema_version": "support-model-registry-v1",
  "model_id": "str",
  "model_hash": "str",
  "parent_model_id": "str or null",
  "dataset_hash": "str",
  "feature_schema_hash": "str",
  "architecture": "str",
  "training_config_hash": "str",
  "metrics": "dict",
  "runtime_benchmark": "dict",
  "package_hash": "str or null",
  "stage": "str ('TRAINING' | 'EVALUATED' | 'SCREENED' | 'PACKAGE_READY' | 'REJECTED' | 'ARCHIVED')",
  "created_at": "float",
  "updated_at": "float"
}
```

### experiment registry record
実験登録用スキーマ。
```json
{
  "schema_version": "support-experiment-registry-v1",
  "run_id": "str",
  "git_commit": "str",
  "config_hash": "str",
  "dataset_hash": "str",
  "model_hash": "str",
  "environment_hash": "str",
  "offline_metrics": "dict",
  "screening_metrics": "dict",
  "latency": "float",
  "status": "str",
  "started_at": "float",
  "completed_at": "float"
}
```

### deck registry record
デッキ登録用スキーマ。
```json
{
  "schema_version": "support-deck-registry-v1",
  "deck_id": "str",
  "deck_hash": "str",
  "version": "str",
  "availability": "str",
  "validation_status": "str",
  "provenance": "dict",
  "created_at": "float",
  "updated_at": "float"
}
```

### opponent registry record
対戦相手ポリシー登録用スキーマ。
```json
{
  "schema_version": "support-opponent-registry-v1",
  "opponent_id": "str",
  "config_hash": "str",
  "version": "str",
  "availability": "str",
  "validation_status": "str",
  "provenance": "dict",
  "created_at": "float",
  "updated_at": "float"
}
```

### hard-state record
困難なゲーム状態（Hard-State）を記録するスキーマ。
```json
{
  "schema_version": "support-hard-state-v1",
  "hard_state_id": "str",
  "source_record_reference": {
    "episode_id": "str",
    "decision_id": "str"
  },
  "reason_codes": "list of str",
  "priority_score": "float",
  "priority_contributions": "dict",
  "dedup_key": "str",
  "conflict_status": "str",
  "safe_summary": "dict"
}
```

### quarantine record
隔離された不良データの記録用スキーマ。
```json
{
  "schema_version": "support-quarantine-v1",
  "quarantine_id": "str",
  "reason": "str",
  "source_file": "str",
  "source_line": "int",
  "record_hash": "str",
  "conflicting_record_hashes": "list of str",
  "safe_summary": "dict",
  "timestamp": "float"
}
```

### sampling manifest
優先サンプリングの結果マニフェスト。
```json
{
  "schema_version": "support-sampling-manifest-v1",
  "input_count": "int",
  "eligible_count": "int",
  "sampled_count": "int",
  "reason_distribution": "dict",
  "selection_type_distribution": "dict",
  "context_type_distribution": "dict",
  "source_hash": "str",
  "sample_hash": "str",
  "seed": "int",
  "weights": "dict"
}
```

### cross-play report
クロス対戦（ポリシー対抗戦）レポートのスキーマ。
```json
{
  "schema_version": "support-cross-play-v1",
  "matrices": {
    "game_count": "dict",
    "wins": "dict",
    "draws": "dict",
    "invalid": "dict",
    "crash": "dict",
    "timeout": "dict",
    "fallback": "dict",
    "legal_rate": "dict",
    "win_rate": "dict",
    "wilson_lower": "dict",
    "wilson_upper": "dict"
  },
  "created_at": "float"
}
```

### rating report
レーティング算出結果のレポートスキーマ。
```json
{
  "schema_version": "support-rating-report-v1",
  "ratings": {
    "policy_id": {
      "rating": "float",
      "games": "int",
      "wins": "int",
      "losses": "int",
      "draws": "int",
      "uncertainty_indicator": "float",
      "data_sufficiency_status": "str"
    }
  },
  "created_at": "float"
}
```
