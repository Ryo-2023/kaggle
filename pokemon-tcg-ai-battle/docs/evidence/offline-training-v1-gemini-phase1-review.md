# Offline Training v1 Support Platform Phase 1 Adversarial Review

本稿は、Phase 1 で実装されたモジュールを対象とした敵対的レビュー（Adversarial Review）の結果です。

---

## 1. 指摘事項一覧

### 指摘 01: レジストリ登録時の入力オブジェクトのミューテーション (High)
- **ファイル**: `src/mage_ptcg/offline_training_v1_support/registries.py`
- **シンボル**: `BaseRegistry.register`, `BaseRegistry.archive`
- **問題点**:
  `register` メソッドおよび `archive` メソッドにおいて、引数で渡された `record` ディクトオブジェクトに対して直接 `record["content_hash"] = ...` や `record["created_at"] = ...` といった変更を行っています。呼び出し元がこのディクトオブジェクトを再利用していた場合、予期しないミューテーション（副作用）が発生します。
- **再現方法**:
  ```python
  record = {"schema_version": "...", "deck_id": "deck_1", ...}
  registry.register_deck(record)
  # record 内に "content_hash" や "created_at" が勝手に追加されている
  ```
- **影響**:
  呼び出し元で同じディクトを別用途（他のレジストリや比較処理など）で再利用した際に、不要なハッシュが含まれたり、意図しない値の書き換えが発生しバグの原因になります。
- **修正方針 (Minimal Fix)**:
  `record` を受け取った直後に `record = dict(record)` または `record = record.copy()` を呼び出し、内部の変更用コピーを作成します。
- **解決状況**: Milestone 0 にて修正予定。

### 指摘 02: サンプリング時のレコード参照のミューテーション可能性 (Medium)
- **ファイル**: `src/mage_ptcg/offline_training_v1_support/sampling.py`
- **シンボル**: `priority_sample`
- **問題点**:
  サンプリング結果として元の `records` に含まれるディクトオブジェクトの参照をそのままリスト `sampled_records` に格納して返却しています。呼び出し元が返却されたレコードを変更した場合、元のプールにあるオブジェクトも変更されます。
- **再現方法**:
  ```python
  sampled, manifest = priority_sample(records, config, count=1)
  sampled[0]["some_key"] = "mutated"  # records 内のオブジェクトも変わる
  ```
- **影響**:
  パイプラインの下流でレコードに一時的なプロパティなどを付与した際、元のデータプールが破壊され、再現性に悪影響を及ぼします。
- **修正方針 (Minimal Fix)**:
  サンプリングされた各レコードをシャローコピー (`item.copy()`) してリストを構築します。
- **解決状況**: Milestone 0 にて修正予定。

### 指摘 03: 巨大ファイル読み込み時のメモリ使用量の増大 (Low / Observation)
- **ファイル**: `src/mage_ptcg/offline_training_v1_support/contracts.py`
- **シンボル**: `load_records`
- **問題点**:
  `load_records` はファイルを 1 行ずつ読み込んでいますが、最終的にすべてのレコードを `list[dict]` としてメモリ上に展開して一括返却しています。ファイルサイズが数百メガバイトにおよぶ場合、メモリを圧迫します。
- **影響**:
  データセットの行数が多い場合、実行環境のメモリ制限に達してプロセスが強制終了する可能性があります。
- **修正方針**:
  今回は要件上リストで返却することが求められていますが、必要に応じてジェネレータ形式 (`yield`) で 1 件ずつストリーミング処理する API も検討可能。Phase 2 で新規実装する `dataset_ops.py` では、全データを一括で RAM に乗せず、ジェネレータを用いたストリーミング処理を原則とします。
- **解決状況**: 監視事項とし、`dataset_ops.py` 等で徹底します。
