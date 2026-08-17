# Archaludon teacher shard 統合復旧 — 2026-08-09

## 結論

`runs/from-worktree/meta-specialist-canonical/meta-specialist-teacher-records/t1-archaludon` は、統合直後の状態では sealed `snapshot_index.json` と完全には一致していなかった。`dataset-0000.jsonl`〜`dataset-0020.jsonl` の21 shardが古い内容で、54 shard中21件のSHA-256がindexと不一致、物理行数は宣言162,925件に対して162,348件で577件不足していた。

同rootの正典生成元 `records/game-*.jsonl` 3,000ファイルから、既存 `scripts/seal_teacher_dataset.py::_write_chunks` と同じ64 MiB chunk規則で54 shardを一時再構築した。再構築した54 shardは `snapshot_index.json.dataset_chunks[*].dataset_snapshot_sha256` と54/54件で完全一致したため、旧21 shardを退避したうえで再構築版へ置換した。

復旧後は54/54 shardのSHA-256がindexと一致し、物理行数も162,925件で `snapshot_index.json.examples_total` および `teacher_dataset_manifest.json.records_written` と一致する。

## 観測事実

- 対象root: `runs/from-worktree/meta-specialist-canonical/meta-specialist-teacher-records/t1-archaludon`
- index/manifest宣言: 54 dataset chunks、162,925 examples/records
- 復旧前:
  - 物理行数: 162,348
  - chunk hash mismatch: 21/54
  - mismatch範囲: `dataset-0000.jsonl`〜`dataset-0020.jsonl`
  - 欠落: 577 records
- `dataset-0021.jsonl`〜`dataset-0053.jsonl` は復旧前からindex hashと一致していたため変更していない。
- Alakazam rootも同時監査し、143/143 chunk hash一致、249,299 examples一致を確認した。

## 復旧方法

1. 現rootの `records/` に `game-000000.jsonl`〜の3,000 game filesが存在することを確認した。
2. 一時領域で `_write_chunks(records_dir, output_dir, 64 * 1024 * 1024)` を実行した。
3. 再構築54 shardのbytes SHA-256をindexの54 expected hashと比較し、mismatch 0を確認した。
4. 復旧前の21 shardを `/tmp/ptcg-arch-stale-backup-20260809` へ退避した。
5. 再構築版の `dataset-0000.jsonl`〜`dataset-0020.jsonl` だけを対象rootへコピーした。
6. 全54 hashと全行数を再検証した。

既存shardをindexの期待hashだけから推測・生成していない。collectionの元record bytesを既存のcanonical chunkerへ通した結果がindexと完全一致したことを復旧authorityとした。

## 復旧後の検証

```text
chunks 54 mismatch 0 expected_examples 162925
162925 total
```

Alakazam比較監査:

```text
chunks 143 mismatch 0 examples 249299
```

## 残る運用条件

- Gate 1 inputは復旧前のraw-line hashを含むため、そのまま再利用せず再buildする。
- Gate input builderと後続BC loaderは、snapshot index bytesだけでなく、参照するdataset chunkの実bytes hashをindexのexpected hashへ照合してfail closedにする。
- `/tmp/ptcg-arch-stale-backup-20260809` は復旧前21 shardの一時退避先である。復旧後Gateと全corpus検証が完了するまで削除しない。
- commit、push、Kaggle提出は行っていない。
