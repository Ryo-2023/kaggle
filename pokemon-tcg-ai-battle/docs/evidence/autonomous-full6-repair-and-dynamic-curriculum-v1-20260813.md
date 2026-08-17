# Full6 repair blocked descriptor / dynamic META_TRAIN curriculum v1 evidence

## 結論

Full6 は **修復完了ではなく、fail-closed の dry-run descriptor まで**である。既存の formal bridge が固定した 36,684 decision のうち unordered set 対応は 36,680、ordered schema `5:34` は 4 件、global non-ubiquitous near-duplicate cross は 1 ID であることを再確認した。ただし、一次 raw record の全再走査は 10 分の time bound で停止したため、ordered 4 件の record ID / teacher-order target sequence と component assignment は materialize していない。これらを `null`、`reproduction_skipped=true`、`silent_drop=false`、`performance_training_ready=false` として明示した。

一方、Tomato 単独 clean lane は Full6 descriptor と分離した。今回の lightweight verify は Tomato bridge の canonical bytes / self hash / catalog binding と `performance_training_ready=true` の宣言を再確認するが、一次 raw record の再走査を再実行したとは主張しない。

Dynamic META_TRAIN curriculum iteration 0 は actual common24 から生成・formal reload 済みである。common24 の split は META_TRAIN 20 / META_DEV 0 / META_FINAL 4、非ゼロ exposure は META_TRAIN 20 / META_DEV 0 / META_FINAL 0 である。META_FINAL 4 件は manifest 内に lineage を残すだけで、weight / quota / training exposure をすべて 0 にした。全 20 件が `local_eval_only` であるため、teacher/behavior eligible は 0 であり、この artifact 自体は学習・昇格・提出・外部実行権限を一切付与しない。

## 一次 artifact

### Full6 blocked repair descriptor

- path: `runs/final-sprint-autonomous/student-v3-full6-repair-v1/manifest.json`
- file SHA-256: `a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7`
- semantic repair SHA-256: `f5c50c93e33e95bb815154ba6c60a4f34271a17f647bfdc9b016cc2509e840f2`
- input blocked Full6 bridge file SHA-256: `0639f01c61cd016a4b8b12cfa5b0f675c07ace4552a19a796048f95e45c85c6f`
- input blocked Full6 bridge semantic SHA-256: `bbd6fc7d7a78fb8dd736908699103551d4cad0a06fc1223c4547db50a05f36dc`
- input Tomato clean bridge file SHA-256: `8c026b2ad5eaf9de67a109aaa5393722d4b3c5c05d2813ec9827b6ba42d0c983`
- input Tomato clean bridge semantic SHA-256: `3e9cdf0605078f48cb7f1b8bb33dae1023e4e0a74f33afb97f483657896d95b0`
- catalog file SHA-256: `8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4`
- catalog semantic SHA-256: `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e`
- decision SHA-256: `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc`
- source decisions: 36,684
- unordered set-compatible decisions: 36,680
- ordered pointer-head gap: 4 (`5:34`)
- global non-ubiquitous cross ID: `5a996ab25264020f3a776c00489771e41b1bfbd2a0cff63eb0c907a8953e80ed`
- published rows: 0
- blocked reasons:
  - `component_split_assignment_unmaterialized`
  - `ordered_pointer_head_quarantine_unmaterialized`
  - `primary_reproduction_incomplete`

### Dynamic curriculum iteration 0

- path: `runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json`
- file SHA-256: `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a`
- semantic curriculum SHA-256: `df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4`
- bound meta-distribution manifest file SHA-256: `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
- bound meta schedule file SHA-256: `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`
- bound common24 config file SHA-256: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- bound opponent pool manifest file SHA-256: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- quota: 96
- training families: 12
- teacher/behavior eligible: 0
- authority: training / promotion / submission / external execution = all false

## 実装

- `src/mage_ptcg/meta_specialist/student_v3_full6_repair_v1.py`
  - episode と non-ubiquitous near-duplicate identity の union component split primitive
  - ubiquitous identity 例外
  - ordered pointer-head gap だけを許す exact quarantine primitive
  - lightweight blocked descriptor builder / strict canonical self/source verifier
  - complete primary reproduction を要求した場合は lightweight descriptor を fail-closed で拒否
- `scripts/build_student_v3_full6_repair_v1.py`
  - 既定は lightweight blocked descriptor
  - `--reproduce-primary` は明示 opt-in（今回未完了）
- `src/mage_ptcg/meta_specialist/dynamic_meta_train_curriculum_v1.py`
  - family/archetype、candidate score、fault、seat、exposure、diversityを使う deterministic weight
  - family quota floor / opponent・family weight cap
  - iteration outcome ledger は META_TRAIN のみ許可
  - META_DEV / META_FINAL exposure 0 の strict gate
  - previous iteration / outcome ledger / source SHA lineage、atomic write、formal reproduction verifier
- `scripts/build_dynamic_meta_train_curriculum_v1.py`
  - iteration 0 および consecutive update 用 CLI

既存 GPU trainer、evaluator、`main.py`、production bridge、opponent asset は編集していない。実学習、CABT、提出も起動していない。

## 再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/build_student_v3_full6_repair_v1.py \
  --repo-root . \
  --blocked-full6-bridge runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-full6/bridge-manifest.json \
  --tomato-clean-bridge runs/final-sprint-autonomous/teacher-student-v3-set-bridge-v2-tomato/bridge-manifest.json \
  --output-manifest runs/final-sprint-autonomous/student-v3-full6-repair-v1/manifest.json \
  --seed full6-component-repair-v1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/build_dynamic_meta_train_curriculum_v1.py \
  --repo-root . \
  --meta-manifest runs/final-sprint-autonomous/meta-distribution-v1/manifest.json \
  --meta-schedule runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json \
  --broad-pool-config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --output-manifest runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json \
  --quota 96 \
  --seed common24-dynamic-curriculum-v1 \
  --iteration 0
```

出力は immutable new-write なので、既存artifactへ再実行すると `FileExistsError` になる。再現時は新しい run path を指定する。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q \
  tests/meta_specialist/test_student_v3_full6_repair_v1.py \
  tests/meta_specialist/test_dynamic_meta_train_curriculum_v1.py
=> 9 passed
```

テストは component-unit split、ubiquitous例外、determinism、ordered-only quarantine、他unsupported fail-closed、actual bridgeのlightweight descriptor、META_DEV/FINAL zero exposure、family quota/cap、hard-negative/fault/seat/exposure update、permission/heldout ledger rejection、actual common24 build/formal reloadを覆う。

## 残る completion condition

Full6 を performance training ready にするには、次をすべて完了する必要がある。

1. time-bounded または高速化した一次 raw-record reproduction を完走する。
2. ordered 4 件の exact record ID と teacher-order target sequence をmaterializeする。
3. global cross identity を含む episode/near-duplicate connected component assignmentをmaterializeする。
4. output non-ubiquitous cross = 0 を一次recordから再検証する。
5. ordered pointer headをdataset/trainer/runtimeで同一意味論として実装するか、4件を正式quarantineした unordered-only datasetについて別の採用判断を行う。
6. downstream bridge/GPU consumerがassignment/quarantine/catalogをformalにcross-bindする。

この完了条件を満たすまでは Full6 artifact を学習入力、性能証拠、提出候補として扱わない。
