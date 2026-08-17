# Derived Teacher Student v3 AWR Adapter v1 Evidence — 2026-08-13

## 結論

fresh v2b の actor-visible AWR 全6 teacher artifact から、formal V3 bridge で選択された
`tomatomato_archaludon` の Student v3 train record だけへ raw `awr_weight` を厳密に
結合した。出力は既存 consumer schema
`offline-scaleup-student-v3-weight-sidecar-v1` であり、3,623 train record の every/only
完全一致を確認した。`effective_weight` と `example_quality_weight` は出力していないため、
trainer が sealed quality を一度だけ乗算する。

この artifact は学習、昇格、提出のいずれにも権限を持たない。生成・検証のみであり、
学習や Champion 変更、commit、push、Kaggle submission は実行していない。

## 一次 artifact

| artifact | path | file / semantic SHA-256 |
|---|---|---|
| exact catalog | `runs/final-sprint-autonomous/derived-teacher-catalog-v2b/catalog.json` | file `8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4`; semantic `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e` |
| all-six AWR manifest | `runs/final-sprint-autonomous/derived-teacher-actor-visible-awr-v2b/manifest.json` | file `388a021a5dfbf4dcd035bb3951c0d164cead71625e0d2af001a437fb63d594ff`; semantic `eea52195b9b3d5d1d68a28527b17a17dbbc9e5357cb35e01692ab25e8c4c641b` |
| all-six AWR rows | `runs/final-sprint-autonomous/derived-teacher-actor-visible-awr-v2b/weights.jsonl` | file `38660b3772badb2791158c3d441d76fc1d3f3008b8468b3af27b39d19317ddd3` |
| selected formal bridge | `runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-tomato/bridge-manifest.json` | file `8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983`; semantic `3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0` |
| formal GPU dataset | `runs/final-sprint-autonomous/student-v3-set-gpu-dataset-v2-tomato/manifest.json` | file `67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518`; semantic `351459083349917faf3b30384506849be0493de9996a4a4afa043c8f646626b5` |
| adapter output | `runs/final-sprint-autonomous/derived-teacher-student-v3-awr-adapter-v1-tomato/weights.json` | file `63e25c029d08c1612b86567bab469a1eba92976884f3f488dbe9e9a19d002229` |

## 入力と選択集合

- AWR 全体: 36,684 rows、train 25,923、heldout 10,761、6 teachers。
- selected teacher: `tomatomato_archaludon` のみ。
- selected teacher 全 split: 5,110 records。
- GPU split: train 3,623、validation 486、test 1,001。
- sidecar output: GPU train と同一の 3,623 record IDs のみ。
- AWR の非selected teacher rows は formal catalog 内にある場合だけ無視する。
- selected teacher については train/validation/test 全 record が AWR と GPU で完全一致しない限り拒否する。

## 出力契約と再読込結果

出力は canonical JSON、末尾 newline なし、閉じた6キー schema である。各 weight row は
`record_id` と `weight` の2キーだけを持つ。`weight` は AWR row の raw normalized
`awr_weight` である。

- `dataset_manifest_sha256`: `67bba0f4abb94ec0092473301b7ce2a4f21087ebfe79693e2f41121e8b53d518`
- `catalog_sha256`: `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e`
- raw AWR weight mass: `3987.33142044282`
- raw AWR ESS: `2804.4098482164172`
- authority: training / promotion / submission すべて `false`

既存の `load_training_weight_sidecar` で独立再読込し、次を確認した。

```json
{"authority":{"promotion_authority":false,"submission_authority":false,"training_authority":false},"canonical_no_newline":true,"every_only_train":true,"external_weight_ess":2804.4098482164172,"external_weight_mass":3987.33142044282,"raw_awr_weight_only":true,"rows":3623,"sidecar_sha256":"63e25c029d08c1612b86567bab469a1eba92976884f3f488dbe9e9a19d002229","status":"PASS"}
```

## Fail-closed oracle

focused test は以下を拒否する。

- exact catalog file SHA mismatch。
- old / cross-catalog AWR binding と old AWR schema。
- old formal-bridge schema、bridge/catalog/source binding drift。
- catalog 外 teacher row。
- AWR または GPU の duplicate record ID。
- old / missing GPU record ID。
- selected teacher の GPU missing / extra record。
- heldout AWR row の GPU train 混入と split mismatch。
- `record_content_hash` / GPU `source_record_sha256` mismatch。
- 既存出力の overwrite。

なお旧 `student-v3-set-gpu-dataset-v1-tomato` は、linked bridge に exact
`catalog_path` がなく、実CLIで
`GPU bridge is old or invalid: exact catalog_path is required` として出力を作らず拒否した。
成功 artifact は formal bridge v2 に結合した fresh GPU dataset v2 だけから生成した。

## 検証コマンド

```bash
PYTHONPATH=.:src pytest -s -q \
  tests/meta_specialist/test_derived_teacher_student_v3_awr_adapter_v1.py
# 15 passed

PYTHONPATH=.:src pytest -s -q \
  tests/test_gpu_student_v3_set_contract.py::test_weight_sidecar_strictly_joins_every_and_only_train_record \
  tests/test_gpu_student_v3_set_contract.py::test_weighted_empirical_batch_loss_uses_global_mean_and_is_partition_invariant
# 2 passed

PYTHONPATH=.:src python scripts/build_derived_teacher_student_v3_awr_adapter_v1.py \
  --repo-root . \
  --awr-manifest runs/final-sprint-autonomous/derived-teacher-actor-visible-awr-v2b/manifest.json \
  --gpu-dataset-dir runs/final-sprint-autonomous/student-v3-set-gpu-dataset-v2-tomato \
  --catalog runs/final-sprint-autonomous/derived-teacher-catalog-v2b/catalog.json \
  --catalog-file-sha256 8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4 \
  --output runs/final-sprint-autonomous/derived-teacher-student-v3-awr-adapter-v1-tomato/weights.json
```

## 実装

- `src/mage_ptcg/meta_specialist/derived_teacher_student_v3_awr_adapter_v1.py`
- `scripts/build_derived_teacher_student_v3_awr_adapter_v1.py`
- `tests/meta_specialist/test_derived_teacher_student_v3_awr_adapter_v1.py`

