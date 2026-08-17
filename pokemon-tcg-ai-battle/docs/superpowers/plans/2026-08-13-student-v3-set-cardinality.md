# Generic Student v3 Set+Cardinality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each task in order.

**Goal:** unordered selectionのzero/variable/fixed multiをlosslessに学習・推論するgeneric Student v3とstrict teacher bridgeを追加する。

**Architecture:** V2を変更せず、V3 source bridge、GPU shard、set+count model、strict runtimeを新規ファイルで分離する。全authorityはfalse、purposeは`DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY`に固定する。

**Tech Stack:** Python 3.12、PyTorch、既存ActorInformationView/Stable ActionKey/derived teacher catalog。

## Global Constraints

- commit、push、remote branch、Champion変更、Kaggle submissionを行わない。
- native teacher code/deckをbundleしない。
- ordered selectionはpointer headなしでfail-closed。
- 実performance training/CABTを起動しない。
- collector v2 fresh reseal前の旧6-teacher catalogから実datasetを生成しない。
- TDDのRED→GREENを各taskで記録する。

---

### Task 1: GPU set dataset contract

**Files:**
- Create: `src/mage_ptcg/offline_scaleup/gpu_student_v3_set.py`
- Create: `tests/test_gpu_student_v3_set_contract.py`

**Interfaces:**
- `build_set_dataset(source: Path, output_dir: Path, shard_size: int) -> dict`
- `_sample_from_source_row(row: dict) -> tuple[state, actions, target_set, count bounds, metadata]`
- `_collate_set(batch: list) -> dict[str, Tensor]`
- `load_training_weight_sidecar(...) -> (record_id_to_weight, stats)`

- [ ] RED: zero、single、fixed multi、variable multiを含む5 split source fixtureを作り、target-set/count/boundsとepisode leakageをassertする。
- [ ] RED実行: module未存在でfailを確認する。
- [ ] GREEN: RuleBCExampleと既存feature extractorだけを使うstrict converter/shard writerを実装する。
- [ ] GREEN実行: focused testをpassさせる。

### Task 2: Set+cardinality model/loss

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/gpu_student_v3_set.py`
- Modify: `tests/test_gpu_student_v3_set_contract.py`

**Interfaces:**
- `make_set_cardinality_model(hidden, blocks, dropout, max_count) -> nn.Module`
- `set_cardinality_loss(action_logits, count_logits, batch, count_loss_weight=1.0) -> dict`
- `decode_set_predictions(...) -> list[list[int]]`

- [ ] RED: permutation invariance、illegal count mask、zero count、multi-positive BCEをassertする。
- [ ] RED実行: missing functionsでfailを確認する。
- [ ] GREEN: masked BCE + masked CE、mean/max pooling、stable tensor decodeを実装する。
- [ ] GREEN実行: loss testsをpassさせる。
- [ ] RED/GREEN: canonical `record_id`のtrain完全一致sidecar joinとmissing/extra/duplicate/
  nonfinite/nonpositive拒否、mass/ESSを検証する。

### Task 3: Strict train/checkpoint/resume and tiny overfit

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/gpu_student_v3_set.py`
- Modify: `tests/test_gpu_student_v3_set_contract.py`

**Interfaces:**
- `train_set_student(...) -> dict`
- `load_set_checkpoint(model_dir: Path, device) -> tuple[model, summary]`
- CLI commands: `build-dataset`, `train`, `evaluate`

- [ ] RED: synthetic CPU runでloss低下、1 epoch後のresume、dataset/config SHA改ざん拒否をassertする。
- [ ] RED: CUDA利用可能時のtiny-overfitとCPU/GPU decode parityをassertする。
- [ ] GREEN: strict checkpoint schema、summary SHA、resume validation、metricsを実装する。
- [ ] GREEN実行: CPU/GPU focused testsをpassさせる。

### Task 4: Fail-closed runtime

**Files:**
- Create: `src/mage_ptcg/offline_scaleup/student_v3_set_runtime.py`
- Create: `tests/test_student_v3_set_runtime.py`

**Interfaces:**
- `StudentV3SetCandidatePolicy(model, device, deck, max_count) -> policy`
- `load_set_candidate_ranker(model_dir, device) -> tuple[model, summary]`
- `choose(observation) -> list[int]`

- [ ] RED: k=0、variable/fixed multi、digest+option tie、ordered rejection、non-finite rejectionをassertする。
- [ ] RED実行: runtime未存在でfailを確認する。
- [ ] GREEN: exact runtime feature/decode contractを実装する。
- [ ] GREEN実行: runtime testsをpassさせる。

### Task 5: Six-teacher V3 bridge and CLI

**Files:**
- Create: `src/mage_ptcg/meta_specialist/teacher_snapshot_student_v3_bridge_v1.py`
- Create: `scripts/build_teacher_snapshot_student_v3_bridge_v1.py`
- Create: `tests/meta_specialist/test_teacher_snapshot_student_v3_bridge_v1.py`

**Interfaces:**
- `build_teacher_snapshot_student_v3_bridge_v1(...) -> dict`
- source schema: `offline-scaleup-student-v3-set-source-v1`

- [ ] RED: optional declineとvariable multiがsupported、orderedがschema別集計され全datasetを止めることをassertする。
- [ ] RED実行: bridge未存在でfailを確認する。
- [ ] GREEN: V2 bridgeのstrict read-only helpersを再利用し、1 decision 1 rowのV3 sourceを実装する。
- [ ] GREEN実行: current SEALED source integrationをpassさせる。
- [ ] collector v2で全6をfresh再収集・resealしformal loaderがREADYにした新catalogだけを
  全件audit/buildする。旧catalogはintegrity NO-GOとして使用しない。

### Task 6: Evidence and final verification

**Files:**
- Create: `docs/evidence/autonomous-student-v3-set-cardinality-20260813.md`
- Create under ignored run dir: V3 bridge/dataset/tiny-overfit manifests

- [ ] focused pytest、syntax compile、JSON/SHA再計算、whitespace、`git diff --check`を実行する。
- [ ] artifact SHA、ordered count、unsupported count、GPU loss、resume、CPU parityをevidenceへ記録する。
- [ ] hard BC性能主張なし、AWR/value/package blocker、Git状態を明記する。
