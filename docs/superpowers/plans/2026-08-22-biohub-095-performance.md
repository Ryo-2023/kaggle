# Biohub 0.95 Performance Goal 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公開Recipe C dual-seed pipelineを一次sourceとcheckpoint hashへ固定し、GT-free実画像推論からpostprocessed prediction GEFFを生成して、固定5サンプルのvendored official Final Score macro平均 `>= 0.95` を再現する。

**Architecture:** Apache-2.0の公開repositoryをartifactとしてpinned checkoutし、関数を再実装せずadapterから呼ぶ。run-local support treeだけへD4/dual-seed/edge-threshold/device patchを適用し、画像推論とpostprocessをGT-freeで完了する。selection lockを参照するprediction manifestを永続化した後、既存ground-truth ordering guardを通してofficial metricを実行する。

**Tech Stack:** Python 3.11、PyTorch、NumPy、SciPy、PyYAML、tracksdata/GEFF、pyscipopt/ILP、Zarr、pytest、ruff、Docker Compose `biohub-dev`。外部source commit `843a47fdd531bdf7e6377673135519c54b69ae28`。

**Spec:** `docs/superpowers/specs/2026-08-22-biohub-095-performance-design.md`

## Global Constraints

- 推論、cache、candidate生成、method/config/checkpoint選択へGTを渡さない。GTを開くのはprediction GEFFとmanifestを永続化・hash検証した後のofficial evaluationだけ。
- `PANEL_V1` は `44b6_0113de3b`、`44b6_0b24845f`、`44b6_0c582fdc`、`44b6_0db75fae`、`44b6_12dfb391` の5件で固定し、失敗・低score・divisionを理由に分母から除外しない。
- Recipe Cはsource側の公開configをbyte-for-byteで固定する。本panelのmetricを見てthreshold、weight、postprocess、seedを変更しない。
- source checkout、support repo、primary/secondary checkpointは期待commit/SHA-256が一致しない限り実行しない。opaque/不足assetへfallbackしない。
- 外部sourceのpostprocessingをコピー改変・同名再実装しない。pinned checkoutからimportして使用し、互換adapterだけを本repoへ追加する。
- 元のsupport artifactと既存official upstreamを変更しない。patchはrun-local staged copyへだけ適用する。
- device `auto` はPyTorch inferenceで `CUDA → MPS → CPU`。ILP、GEFF I/O、official metricはCPUのまま。resolved deviceをreceiptへ保存する。
- 大規模runはsample単位に逐次実行し、`0b` と `12df` を並列実行しない。OOM時は一括配列展開をやめ、既存mmap/chunk契約へfallbackする。
- vendored `src/biohub/official_metrics/metrics.py` と `division_metrics.py` は変更しない。
- Python、test、lint、推論、metricはUbuntu `biohub-dev` container内で実行する。hostへ依存をinstallしない。
- Kaggleへの外部submission送信は行わない。大容量data/checkpoint/predictionはGit管理しない。
- ユーザー向けreportはすべて日本語。性能主張は実測receiptに限定する。

---

### Task 1: Recipe C source・config・checkpoint契約を固定する

**Files:**
- Create: `configs/biohub_095_recipe_c.yaml`
- Create: `src/biohub/recipe_c/__init__.py`
- Create: `src/biohub/recipe_c/source.py`
- Create: `tests/test_recipe_c_source.py`

**Interfaces:**
- `RecipeCSourceContract` はsource URL/commit、license/config/notebook hash、primary/secondary checkpoint relative pathとSHA-256を保持する。
- `validate_source_checkout(root: Path, contract: RecipeCSourceContract = RECIPE_C_SOURCE) -> dict[str, object]` はGit HEADと固定file hashを検証する。
- `validate_support_artifact(root: Path, contract: RecipeCSourceContract = RECIPE_C_SOURCE) -> dict[str, object]` は`repo/scripts/predict_unet_transformer.py`と2 checkpointを検証する。
- configはsource `configs/experiments/recipe_c_motion_off_edge_0_40_det0_96875.yaml` と同一内容、期待SHA-256 `0e5758f3ea76ba015fb71c35bc749e136c009237e093d544a89a4b03a8c66ced` とする。
- checkpointはprimary `12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771`、secondary `9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f`。

- [ ] **Step 1: hash不一致とasset不足の失敗テストを書く**

```python
def test_source_contract_rejects_wrong_commit(tmp_path, fake_source_tree):
    fake_source_tree.write_git_head("0" * 40)
    with pytest.raises(ValueError, match="source commit"):
        validate_source_checkout(fake_source_tree.root)


def test_support_contract_requires_both_distinct_checkpoints(tmp_path, fake_support):
    fake_support.secondary.unlink()
    with pytest.raises(FileNotFoundError, match="seed_314159"):
        validate_support_artifact(fake_support.root)
```

- [ ] **Step 2: source testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_source.py'`

Expected: missing `biohub.recipe_c.source` でFAIL。

- [ ] **Step 3: canonical SHA-256検証とconfigを最小実装する**

hashは1 MiB chunkで読み、manifestへabsolute credential pathを保存しない。source receiptにはsource commit、file hashes、license、config values、checkpoint hashesをcanonical JSONとして返す。

- [ ] **Step 4: source testをGREENで実行する**

Run: Task 1 Step 2と同じ。

Expected: 全テストPASS。

- [ ] **Step 5: Task 1だけをcommitする**

```bash
git add configs/biohub_095_recipe_c.yaml src/biohub/recipe_c tests/test_recipe_c_source.py
git commit -m "Pin public Biohub Recipe C source and assets"
```

### Task 2: immutable protocolとselection lockを機械化する

**Files:**
- Create: `src/biohub/recipe_c/protocol.py`
- Create: `tests/test_recipe_c_protocol.py`
- Create: `scripts/run_biohub_095.py`（`freeze` subcommandのみ）

**Interfaces:**
- `PANEL_V1: tuple[str, ...]` は固定5件を順序付きで公開する。
- `build_selection_lock(source_receipt, config_path, code_commit, requested_device, result_visible_before_selection=True) -> dict[str, object]` はpanel/config/source/asset identityをcanonical hash化する。
- `write_selection_lock(path: Path, payload: Mapping[str, object]) -> Path` は既存pathを上書きせず、`ground_truth_used_for_selection=false` と `panel_status=retrospective_locked_confirmation` を必須にする。
- `validate_selection_lock(path: Path) -> dict[str, object]` はlock ID、5件の順序、config/source/checkpoint hash、GT境界を再検証する。
- `scripts/run_biohub_095.py freeze` は `artifacts/biohub_095/selection_lock.json` を作る。

- [ ] **Step 1: panel変更・GT選択・lock上書きの失敗テストを書く**

```python
def test_selection_lock_rejects_changed_panel(valid_lock):
    valid_lock["panel"]["sample_ids"] = valid_lock["panel"]["sample_ids"][:-1]
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_is_write_once(tmp_path, valid_lock):
    path = write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    with pytest.raises(FileExistsError):
        write_selection_lock(path, valid_lock)
```

- [ ] **Step 2: protocol testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_protocol.py'`

Expected: missing protocol symbolsでFAIL。

- [ ] **Step 3: canonical lockとfreeze CLIを実装する**

既存Claude receipt auditorのfield aliasは再利用するが、今回のlockは`selection_lock_id`、`source_commit`、`config_sha256`、両checkpoint SHA、`requested_device`、`ground_truth_used_for_selection=false`を直接必須化する。

- [ ] **Step 4: protocol/CLI testをGREENで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_protocol.py tests/test_recipe_c_source.py'`

Expected: 全テストPASS。

- [ ] **Step 5: Task 2だけをcommitする**

```bash
git add src/biohub/recipe_c/protocol.py tests/test_recipe_c_protocol.py scripts/run_biohub_095.py
git commit -m "Add immutable Biohub 0.95 selection lock"
```

### Task 3: run-local source/support stagingとdevice fallbackを実装する

**Files:**
- Create: `src/biohub/recipe_c/staging.py`
- Create: `src/biohub/recipe_c/device_patch.py`
- Create: `tests/test_recipe_c_staging.py`
- Modify: `scripts/run_biohub_095.py`（`dry-run` subcommand）

**Interfaces:**
- `stage_recipe_c_runtime(source_root, support_root, destination, selection_lock) -> RuntimeStage` はsource/supportを検証してから、support `repo/`だけを新規run directoryへcopyする。checkpointはread-only元pathを参照する。
- `apply_device_fallback_patch(predictor_path: Path) -> bool` はsupport scriptの `cuda if available else cpu` preimageを `cuda → mps → cpu` へ置換し、二回目はno-op、未知preimageは失敗する。
- `RuntimeStage` はstaged repo、weights root、source root、config、patch前後SHA、resolved device候補を保持する。
- 元source/supportのdirectory digestがstaging前後で一致しなければ失敗する。

- [ ] **Step 1: 元artifact不変・patch idempotence・fallback順序の失敗テストを書く**

```python
def test_staging_never_mutates_source_or_support(tmp_path, fake_source, fake_support, valid_lock):
    before = digest_trees(fake_source.root, fake_support.root)
    stage_recipe_c_runtime(fake_source.root, fake_support.root, tmp_path / "stage", valid_lock)
    assert digest_trees(fake_source.root, fake_support.root) == before


def test_device_patch_contains_cuda_mps_cpu_order(tmp_path, predictor_preimage):
    path = tmp_path / "predict.py"
    path.write_text(predictor_preimage)
    assert apply_device_fallback_patch(path) is True
    text = path.read_text()
    assert text.index("cuda") < text.index("mps") < text.index("cpu")
    assert apply_device_fallback_patch(path) is False
```

- [ ] **Step 2: staging testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_staging.py'`

Expected: missing staging/device patchでFAIL。

- [ ] **Step 3: immutable stagingとstrict source patchを実装する**

copy先が存在する場合は削除・上書きせず`FileExistsError`。external sourceのD4、dual-seed、edge-threshold patchはpinned `biohub_pipeline.inference` をimportしてstaged predictorへ適用する。本repoはalgorithm patchを再実装しない。

- [ ] **Step 4: staging/source/protocol testをGREENで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_staging.py tests/test_recipe_c_source.py tests/test_recipe_c_protocol.py'`

Expected: 全テストPASS、元tree digest不変。

- [ ] **Step 5: Task 3だけをcommitする**

```bash
git add src/biohub/recipe_c/staging.py src/biohub/recipe_c/device_patch.py tests/test_recipe_c_staging.py scripts/run_biohub_095.py
git commit -m "Stage Recipe C runtime without mutating upstream"
```

### Task 4: GT-free Recipe C inferenceとpostprocessed GEFF bridgeを実装する

**Files:**
- Create: `src/biohub/recipe_c/runner.py`
- Create: `src/biohub/recipe_c/geff_bridge.py`
- Create: `tests/test_recipe_c_runner.py`
- Create: `tests/test_recipe_c_geff_bridge.py`
- Modify: `scripts/run_biohub_095.py`（`infer` subcommand）

**Interfaces:**
- `run_recipe_c_inference(image_root, sample_ids, runtime_stage, selection_lock, output_root, max_frames=None) -> InferenceReceipt` はimageとlocked configだけを受け取り、GT path/metric/result引数を持たない。
- external `build_predict_command()` を使い、dual-seed、D4、threshold `.96875/.40`、ILP weights、sample splitをcommandへ固定する。
- raw GEFFを外部 `write_submission_from_geff()` でpostprocessし、`postprocessed_csv_to_geffs()` がsampleごとのreload可能GEFFへ変換する。
- GEFF nodeは `(t,z,y,x)`、edgeはdirected source→target、in-degree `<=1`、out-degree `<=2`、隣接frameを検証する。
- 各GEFFにper-prediction manifestを作り、`selection_lock_id`、source/config/checkpoint/patch hash、resolved device、runtime、node/edge/fork count、`ground_truth_included=false`を保存する。

- [ ] **Step 1: APIにGTがないこととexact commandの失敗テストを書く**

```python
def test_inference_signature_has_no_ground_truth_or_metric_parameters():
    names = inspect.signature(run_recipe_c_inference).parameters
    assert not ({"gt_path", "ground_truth", "metric", "score"} & set(names))


def test_recipe_c_command_pins_public_values(fake_stage, valid_lock):
    command = build_recipe_c_command(fake_stage, valid_lock, ["sample"])
    joined = " ".join(command)
    for expected in ("--det-threshold 0.96875", "--edge-threshold 0.4", "--ensemble-alpha 0.5"):
        assert expected in joined
```

- [ ] **Step 2: graph bridge topologyの失敗テストを書く**

```python
def test_postprocessed_csv_round_trips_division_to_geff(tmp_path, division_rows):
    paths = postprocessed_csv_to_geffs(division_rows.csv, tmp_path, provenance=division_rows.provenance)
    graph = load_graph(paths["sample"])
    assert graph.number_of_nodes() == 4
    assert sorted(graph.out_edges(1)) == [(1, 2), (1, 3)]


def test_prediction_manifest_references_selection_lock(tmp_path, synthetic_recipe_run):
    manifest = json.loads(synthetic_recipe_run.manifest.read_text())
    assert manifest["selection_lock_id"] == synthetic_recipe_run.lock_id
    assert manifest["ground_truth_included"] is False
```

- [ ] **Step 3: runner/bridge testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_runner.py tests/test_recipe_c_geff_bridge.py'`

Expected: missing runner/bridgeでFAIL。

- [ ] **Step 4: external source orchestrationとGEFF writerを最小実装する**

subprocessはargument listで実行し、shell interpolationを使わない。途中失敗時はpartial directoryに`FAILED.json`を残し、valid manifestを作らない。GEFF writerはtracksdataの既存attribute contractを使い、postprocess algorithm自体は外部sourceを呼ぶ。

- [ ] **Step 5: runner/bridge関連testをGREENで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_runner.py tests/test_recipe_c_geff_bridge.py tests/test_recipe_c_staging.py tests/test_recipe_c_protocol.py'`

Expected: 全テストPASS、synthetic divisionがGEFF round-trip後もout-degree 2。

- [ ] **Step 6: Task 4だけをcommitする**

```bash
git add src/biohub/recipe_c/runner.py src/biohub/recipe_c/geff_bridge.py tests/test_recipe_c_runner.py tests/test_recipe_c_geff_bridge.py scripts/run_biohub_095.py
git commit -m "Run pinned Recipe C and persist prediction GEFFs"
```

### Task 5: official metric境界とfixed macro gateを追加する

**Files:**
- Create: `src/biohub/recipe_c/evaluation.py`
- Create: `tests/test_recipe_c_evaluation.py`
- Modify: `scripts/run_biohub_095.py`（`evaluate`、`aggregate` subcommand）

**Interfaces:**
- `evaluate_locked_prediction(prediction, gt, selection_lock, output) -> dict[str, object]` は既存 `mint_prediction_token()` / `open_ground_truth()` とvendored official evaluatorを再利用する。
- `aggregate_panel_receipts(receipts, selection_lock) -> dict[str, object]` は5件のexact set/order、共通config/source/checkpoint/lock、成功statusを検証し、5で割るunweighted macroを計算する。
- aggregateはper-sample Edge/Division TP/FP/FN、Edge/Adjusted/Division Jaccard、Final Score、node/edge/fork count、runtime/device/path/hashを保持する。
- 1件でも欠損・失敗・NaN Final Scoreならgate statusは`INCOMPLETE`で、成功sampleだけのmacroを出さない。

- [ ] **Step 1: GT open順序と5件固定の失敗テストを書く**

```python
def test_invalid_manifest_fails_before_ground_truth_open(monkeypatch, invalid_prediction, gt):
    events = []
    monkeypatch.setattr("biohub.recipe_c.evaluation._open_gt", lambda *a: events.append("gt"))
    with pytest.raises(Exception, match="manifest|token"):
        evaluate_locked_prediction(invalid_prediction, gt, invalid_prediction.lock, invalid_prediction.out)
    assert events == []


def test_aggregate_refuses_four_of_five(valid_receipts, valid_lock):
    with pytest.raises(ValueError, match="PANEL_V1"):
        aggregate_panel_receipts(valid_receipts[:-1], valid_lock)
```

- [ ] **Step 2: evaluation testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_evaluation.py'`

Expected: missing evaluation symbolsでFAIL。

- [ ] **Step 3: existing official adapterを組み合わせて実装する**

metric formulaやdivision semanticsを複製しない。aggregate receiptには `target=0.95`、`macro_final_score`、`gap_to_target`、`gate_passed` を保存する。source側`official-spec-lite`値は比較参考でありgate判定へ使わない。

- [ ] **Step 4: evaluation/GT-ordering testをGREENで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_evaluation.py tests/test_detector_fixed_prediction.py tests/test_reproducibility_gt_ordering.py'`

Expected: 全テストPASS、invalid manifest時のGT open count 0。

- [ ] **Step 5: Task 5だけをcommitする**

```bash
git add src/biohub/recipe_c/evaluation.py tests/test_recipe_c_evaluation.py scripts/run_biohub_095.py
git commit -m "Evaluate locked Recipe C with the official metric"
```

### Task 6: 一次source/support artifactを取得し、real-data smokeを完走する

**Files:**
- Create: `docs/results/biohub_095_performance.md`
- Artifacts only: `artifacts/biohub_095/source/`, `artifacts/biohub_095/support/`, `artifacts/biohub_095/smoke/`

- [ ] **Step 1: sourceを固定commitへ取得して検証する**

```bash
git clone https://github.com/asapacsin/biohub-cell-tracking.git artifacts/biohub_095/source/clean_v106
git -C artifacts/biohub_095/source/clean_v106 checkout 843a47fdd531bdf7e6377673135519c54b69ae28
```

Expected: `validate_source_checkout` がcommit/license/config/notebook SHAをPASS。

- [ ] **Step 2: Kaggle support artifactを必要範囲だけ取得する**

```bash
kaggle datasets download -d pilkwang/biohub-tracking-support-pack-50ep-v1 -p artifacts/biohub_095/support --unzip
```

Expected: primary/secondary checkpointが両方存在し、期待SHA-256と一致。credential/tokenをlog/reportへ保存しない。

- [ ] **Step 3: selection lockとdry-runを作る**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py freeze --source artifacts/biohub_095/source/clean_v106 --support artifacts/biohub_095/support --config configs/biohub_095_recipe_c.yaml --output artifacts/biohub_095/selection_lock.json --device auto'`

Expected: write-once lock、両checkpoint hash一致、resolved backend情報あり。

- [ ] **Step 4: train実画像の2-frame smokeをGT-freeで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py infer --selection-lock artifacts/biohub_095/selection_lock.json --source artifacts/biohub_095/source/clean_v106 --support artifacts/biohub_095/support --image-root artifacts/detector_fixed_race/panel_data/train --sample 44b6_0113de3b --max-frames 2 --output artifacts/biohub_095/smoke'`

Expected: raw/postprocessed GEFFとmanifestを生成、reload成功、GT access 0、device fallback receiptあり。

- [ ] **Step 5: smoke predictionだけをofficial metricへ通す**

2-frame smokeとfull GTは時刻範囲が異なるため、metric値は性能比較に採用しない。目的はmanifest検証→GT open→official evaluatorの境界確認だけ。`smoke_only=true`を記録する。

- [ ] **Step 6: source/asset/smoke事実を日本語レポートへ記録する**

未実測値を埋めず、source側参考値と本repo実測を別表にする。

- [ ] **Step 7: Task 6の管理対象だけをcommitする**

```bash
git add docs/results/biohub_095_performance.md
git commit -m "Record Recipe C source and real-data smoke"
```

### Task 7: locked Recipe Cを固定5サンプルで完走する

**Files:**
- Modify: `docs/results/biohub_095_performance.md`
- Artifacts only: `artifacts/biohub_095/panel_runs/`, `artifacts/biohub_095/panel_receipt.json`

- [ ] **Step 1: 5 sampleの入力とhashをlockに照合する**

Expected: 5/5 imageが存在。GTは存在だけを確認し、推論processへpathを渡さない。

- [ ] **Step 2: sample単位にGT-free full inferenceを逐次実行する**

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py infer-panel --selection-lock artifacts/biohub_095/selection_lock.json --source artifacts/biohub_095/source/clean_v106 --support artifacts/biohub_095/support --image-root artifacts/detector_fixed_race/panel_data/train --output artifacts/biohub_095/panel_runs --device auto'
```

Expected: 5/5 prediction GEFF/manifest。CUDAならCUDA、Mac native実行でMPS、現Linux DockerではCPU。resolved deviceをsampleごとに記録。

- [ ] **Step 3: 各predictionを個別にofficial evaluationする**

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py evaluate-panel --selection-lock artifacts/biohub_095/selection_lock.json --prediction-root artifacts/biohub_095/panel_runs --gt-root artifacts/detector_fixed_race/panel_data/train --output artifacts/biohub_095/panel_receipt.json'
```

Expected: manifest/token検証後にのみGTを開く。5件のTP/FP/FN/Jaccard/Final Scoreを保存。

- [ ] **Step 4: 0.95 gateを確認する**

Expected: `macro_final_score >= 0.95` かつ `gate_passed=true`。未達なら値を隠さず、Task 8の診断planへ進む。

- [ ] **Step 5: 実測結果と再現commandを日本語レポートへ追記する**

Prediction/GT GEFF、node/edge/fork count、source/checkpoint/config、runtime/device、official metric全項目、baseline差を記録する。

- [ ] **Step 6: Task 7のreportだけをcommitする**

```bash
git add docs/results/biohub_095_performance.md
git commit -m "Record locked five-sample Recipe C evaluation"
```

### Task 8: 未達時だけRAM-safe診断を実装する

**Condition:** Task 7でofficial macro `< 0.95`、またはsource側参考値とofficial値にmaterialな不一致がある場合だけ実行する。達成時は理由付きでskipする。

**Files:**
- Create: `src/biohub/detector_fixed_race/diagnostics.py`
- Create: `tests/test_detector_fixed_diagnostics.py`
- Modify: `src/biohub/detector_fixed_race/cli.py`（`diagnose` subcommand）

**Interfaces:**
- prediction manifest/cache hashをGT open前に検証する。
- node coverage、candidate coverage、true-edge score/rank、detector-fixed edge-perfect oracle、postfilter renormalizationを分離する。
- 1,000,000行chunkと既存`candidate_edges.mmap`を使い、E長array/Python listを作らない。

- [ ] **Step 1: orientation、one-to-one matching、coverage欠損理由、oracle、chunk境界の失敗テストを書く**
- [ ] **Step 2: `tests/test_detector_fixed_diagnostics.py` をREDで確認する**
- [ ] **Step 3: GTを推論へ返さない診断を最小実装する**
- [ ] **Step 4: 診断testをGREENで確認する**
- [ ] **Step 5: 失敗sampleだけを診断し、config変更には使わず日本語で原因を記録する**
- [ ] **Step 6: Task 8だけをcommitする**

### Task 9: 日本語ChatGPT統合レポート、全検証、push

**Files:**
- Modify: `docs/results/chatgpt_submission_report_ja.md`
- Modify: `docs/results/strong_baseline_v1.md`
- Modify: `docs/results/biohub_095_performance.md`

- [ ] **Step 1: 現行レポートの既知の誤記を修正する**

`0.794414...`をmacroと明記し、weighted/microを併記する。harmonicの勝因をreverse agreementと断定せずtemperature/sharpening効果とする。Claude lane統合commit、canonical validation receipt、GT ordering guardの実証範囲、GPU未実測、Kaggle notebook-only制約を更新する。

- [ ] **Step 2: Recipe Cの一次sourceと本repo実測を一つの日本語reportへ統合する**

source側参考macro `0.9560058787896148` とvendored official実測を混同しない。5sample全行、baseline差、artifact link、再現command、known issuesを掲載する。

- [ ] **Step 3: 関連testをcontainerで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_source.py tests/test_recipe_c_protocol.py tests/test_recipe_c_staging.py tests/test_recipe_c_runner.py tests/test_recipe_c_geff_bridge.py tests/test_recipe_c_evaluation.py tests/test_detector_fixed_prediction.py tests/test_reproducibility_gt_ordering.py'`

Expected: 全テストPASS。

- [ ] **Step 4: full pytestとruffを実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q && uv run ruff check .'`

Expected: 全テストPASS、ruff error 0。失敗時は件数を作らず修正後に再実行する。

- [ ] **Step 5: artifact/receiptを再検証する**

5/5、共通lock/config/checkpoints、GT ordering token、prediction reload、official metric、macro算術、report値を独立再計算する。

- [ ] **Step 6: report updateをcommitしてbranchをpushする**

```bash
git add docs/results/chatgpt_submission_report_ja.md docs/results/strong_baseline_v1.md docs/results/biohub_095_performance.md
git commit -m "Finalize Japanese Biohub 0.95 performance report"
git push -u origin codex/biohub-095-performance
```

- [ ] **Step 7: userへ日本語成果物リンクと未解決事項を報告する**

Done claimはTask 7 gate実測とTask 9 verificationが両方揃った場合だけ行う。

