# Derived Teacher Snapshot → Student v2 Bridge v1（2026-08-13）

## 結論

現時点で SEALED な `tomatomato_archaludon` と `lucifer19_battlecore` の
10,248 decision を全件再検証した。9,735 decision（95.0%）は、actor-visible
state と Stable ActionKey だけを入力とする generic Student v2 へ正確に接続可能で
ある。一方、現在の Student v2 decoder が表現できない decision が 513 件残るため、
性能学習用 JSONL は **fail-closed で未公開**とした。

- `optional_decline_not_representable`: 141 decision
- `decoder_cardinality_mismatch`: 372 decision
- `supported_single_decisions`: 8,938 decision
- `supported_multi_positive_decisions`: 797 decision
- 全対応時に出力される行数: 10,699 行

固定個数の unordered multi-select は黙って先頭 action だけへ縮退させない。選択された
各 digest が既存 `gpu_student_v2._sample_from_row` の正解となるよう、candidate order の
multi-positive replica を 1 digest につき 1 行生成する契約を実装した。optional decline、
可変個数、ordered Skill、選択対象の ActionKey alias 衝突は、未対応数と理由を manifest
へ残して dataset 全体を止める。

この結果は「teacher data が使えない」という意味ではない。現在の generic Student v2
経路は 95.0% を正確に受け取れるが、残り 5.0% を削除して性能学習を始めると policy を
系統的に歪めるため、decoder / trainer の契約拡張が先である、という NO-GO である。

## 実装

- `src/mage_ptcg/meta_specialist/teacher_snapshot_student_v2_bridge_v1.py`
  - derived teacher catalog と decision SHA を再検証
  - policy / deck / teacher manifest / permission / snapshot index / shard / raw chunk SHA を再検証
  - snapshot shard の self-hash、split count、record ID、raw record content hash を照合
  - production vocabulary と `require_qualified_training_record_v2` で全 raw record を再構築
  - episode 単位・teacher stratified の 5 split を決定
  - unsupported decision が 1 件でもあれば performance dataset を作らない
  - native teacher code / deck は出力へコピーせず、SHA provenance だけを保持
- `scripts/build_teacher_snapshot_student_v2_bridge_v1.py`
  - audit / conditional build 用 CLI
  - 失敗時は machine-readable JSON と exit code 2
- `tests/meta_specialist/test_teacher_snapshot_student_v2_bridge_v1.py`
  - fixed multi-positive、optional decline、可変個数、ordered、forced STOP を TDD で固定
  - 実 SEALED tomato 5,146 record の全件 integration audit
  - catalog 改ざんと CLI fail-closed を検証

## 入力特徴境界

既存 GPU trainer がモデル入力へ読むのは次の 4 項目だけである。

- `rule_bc_example.public_state`
- `rule_bc_example.own_private_state`
- `rule_bc_example.visible_history`
- `rule_bc_example.legal_actions`

`opponent_id`、`candidate_side`、`teacher_identity` は outer metadata にのみ存在し、
`gpu_student_v2._sample_from_row` の state/action tensor へ入らない。bridge の
`feature_boundary` にこの境界を明記した。teacher native code と source deck の bytes は
dataset / manifest のどちらにも複製していない。

## 厳密な source binding

### tomatomato_archaludon

- records: 5,146 / episodes: 96
- policy SHA-256: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- deck SHA-256: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- teacher manifest SHA-256: `b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff`
- snapshot index SHA-256: `b5cc75c82ee321cb7841b99f80d49fd6759e56d060af435200239a45b36bc72f`
- dataset corpus SHA-256: `38a361ec571e2d8ba9546db333fd48f33ffb72d7d8526ba304f4be80235c559a`

### lucifer19_battlecore

- records: 5,102 / episodes: 96
- policy SHA-256: `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c`
- deck SHA-256: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- teacher manifest SHA-256: `d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84`
- snapshot index SHA-256: `ea5275370d17bcc520d31aec3302ea0be054520eb92811cd5af2cdac54005ba4`
- dataset corpus SHA-256: `ed0b61159e1f89f4aab4d04d0964632aa9965972a1ac99dc7afdadcf6ea8d309`

## Split 設計

対応可能 decision を持つ 192 episode を、teacher ごとに安定 hash 順へ並べ、episode を
跨がず次の 5 split へ割り当てた。teacher / opponent / seat は split metadata であり、
モデル入力ではない。

| split | episodes |
|---|---:|
| train | 130 |
| validation | 20 |
| test | 20 |
| opponent_holdout | 12 |
| deck_holdout | 10 |

episode leakage は 0。seed は
`derived-teacher-student-v2-split-v1-20260813` である。

## 一次 artifact

- audit manifest:
  `runs/final-sprint-autonomous/teacher-student-v2-bridge-v1/bridge-manifest.json`
- audit manifest file SHA-256:
  `5b36ebe86d244c33f05f9b5c891a4ef36a7c259377b8d10743dd17319f2286a4`
- bridge semantic SHA-256:
  `61e1a82d2356e1b199b598c4389f792076b864ab3cc46be50d509206dd04e546`
- derived teacher catalog file SHA-256:
  `863058863440f9a51a8e6d2817a2aa146e3dfe0814d729074fd087a6e9ed3639`
- derived teacher catalog semantic SHA-256:
  `c6892943aefa4b02b8dbfcecdf5eb44efe17e57db751d3d5599f6c4520ddd3cf`
- derivation decision SHA-256:
  `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc`

`performance_training_ready=false`、`partial_dataset_published=false`、
`output_dataset=null` である。training / promotion / submission authority も全て false。

## 再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_teacher_snapshot_student_v2_bridge_v1.py \
  --repo-root . \
  --catalog runs/final-sprint-autonomous/derived-teacher-catalog-v1/catalog.json \
  --output-dataset runs/final-sprint-autonomous/teacher-student-v2-bridge-v1/student-v2.jsonl \
  --output-manifest runs/final-sprint-autonomous/teacher-student-v2-bridge-v1/bridge-manifest.json \
  --teacher-id tomatomato_archaludon \
  --teacher-id lucifer19_battlecore
```

出力 manifest が既に存在する場合は上書きしない。再実行時は新しい run directory を
指定する。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q -s \
  tests/meta_specialist/test_teacher_snapshot_student_v2_bridge_v1.py \
  tests/test_gpu_student_v2_contract.py
```

結果: `10 passed in 46.53s`。

実 integration test は tomato の sealed 5,146 record を全件読み、catalog / permission /
snapshot / raw record / production vocabulary の同じ検証経路を通している。CLI 改ざんテスト
では output dataset / manifest がどちらも作られないことを確認した。

## 残 blocker と次の実装境界

1. Student v2 runtime に optional decline を明示的に選べる STOP head / STOP candidate がない。
2. Student v2 runtime は `min_count if min_count else 1` の固定個数 decoder であり、
   teacher が合法に選んだ `min_count < selected_count <= max_count` を再現できない。
3. 現 Student v2 は hard target の candidate ranker であり、public-state value + AWR / filtered
   BC そのものではない。この bridge は outcome/value_target と品質重みを保存するが、
   同型 hard BC の再開権限を与えない。
4. Student v2 runtime は PyTorch checkpoint を読むため、pure-Python submission package closure
   は別途必要である。
5. `plamen06_steel` と internal 3 teacher は、この artifact 作成時点の catalog ではまだ
   `MISSING_NOT_STARTED` であり、SEALED 後に同じ bridge audit を新 run へ再実行する必要がある。

したがって次の最小実装は、既存 Student v2 の fixed decoder をそのまま使って 95% のみを
学習することではない。STOP / cardinality をlossとruntimeの両方へ追加し、同じ10,248件が
`unsupported=0` になることを新しい bridge manifestで確認してから、value/AWR系の学習へ
接続する。
