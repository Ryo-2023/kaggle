# Biohub 0.95 Performance Goal 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公開Recipe C dual-seed pipelineを一次sourceとcheckpoint hashへ固定し、GT-free実画像推論からpostprocessed prediction GEFFを生成して、固定5サンプルのvendored official Final Score macro平均 `>= 0.95` を再現する。

**Architecture:** Apache-2.0の公開repositoryをartifactとしてpinned checkoutし、関数を再実装せずadapterから呼ぶ。primary support packと別配布secondary seedをhash検証し、run-local support treeだけへD4/dual-seed/edge-threshold/device patchを適用する。一次sourceのCUDA-only wrapperは呼ばず、同sourceが生成したargvを互換adapterで実行して画像推論とpostprocessをGT-freeで完了する。selection lockを参照するprediction manifestを永続化した後、既存ground-truth ordering guardを通してofficial metricを実行する。

**Tech Stack:** Python 3.11、PyTorch、NumPy、SciPy、PyYAML、tracksdata/GEFF、pyscipopt/ILP、Zarr、pytest、ruff、Docker Compose `biohub-dev`。外部source commit `843a47fdd531bdf7e6377673135519c54b69ae28`。

**Spec:** `docs/superpowers/specs/2026-08-22-biohub-095-performance-design.md`

## Global Constraints

- 推論、cache、candidate生成、model input、parameter fittingへGTを渡さない。GTを開くのはprediction GEFFとmanifestを永続化・hash検証した後。完了済みofficial evaluation/error analysisは次experimentのmethod/model family選択へ使えるが、GT edge/座標/metricを次runのinput、loss、threshold fittingへ渡さない。
- `PANEL_V1` は `44b6_0113de3b`、`44b6_0b24845f`、`44b6_0c582fdc`、`44b6_0db75fae`、`44b6_12dfb391` の5件で固定し、失敗・低score・divisionを理由に分母から除外しない。
- Recipe Cはsource側の公開configをbyte-for-byteで固定する。本panelのmetricを見てthreshold、weight、postprocess、seedを変更しない。
- source checkout、primary support repo、別配布secondary seed、primary/secondary checkpointは期待commit/SHA-256が一致しない限り実行しない。opaque/不足assetへfallbackしない。
- 外部sourceのpostprocessingをコピー改変・同名再実装しない。pinned checkoutからimportして使用し、互換adapterだけを本repoへ追加する。
- 元のprimary/secondary support artifactと既存official upstreamを変更しない。patchはrun-local staged copyへだけ適用する。
- device `auto` はPyTorch inferenceで `CUDA → MPS → CPU`。ILP、GEFF I/O、official metricはCPUのまま。resolved deviceをreceiptへ保存する。
- 大規模runはsample単位に逐次実行し、`0b` と `12df` を並列実行しない。OOM時は一括配列展開をやめ、既存mmap/chunk契約へfallbackする。
- vendored `src/biohub/official_metrics/metrics.py` と `division_metrics.py` は変更しない。
- Python、test、lint、推論、metricはUbuntu `biohub-dev` container内で実行する。hostへ依存をinstallしない。
- 既存5sample dataは並行one-pass worktreeのread-only artifact `/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/artifacts/detector_fixed_race/panel_data/train` を参照し、コピー・変更しない。
- Kaggleへの外部submission送信は行わない。大容量data/checkpoint/predictionはGit管理しない。
- ユーザー向けreportはすべて日本語。性能主張は実測receiptに限定する。
- 各experimentを実行前lockへ記録し、評価後に5sample macro/median/control勝率/worst-case harm/divisionをappend-only ledgerへ残す。単一sample改善ではBestKnownへ昇格しない。
- 同一method familyで5 experiment連続BestKnown更新がなければ微小tuningを停止し、全family通算10 experiment以上meaningful improvementがなければarchitecture-level reviewと公開手法再調査へ切り替える。

---

### Task 1: Recipe C source・config・checkpoint契約を固定する

**Files:**
- Create: `configs/biohub_095_recipe_c.yaml`
- Create: `src/biohub/recipe_c/__init__.py`
- Create: `src/biohub/recipe_c/source.py`
- Create: `tests/test_recipe_c_source.py`

**Interfaces:**
- `RecipeCSourceContract` はsource URL/commit、license/config/notebook hash、primary v10/secondary v2 dataset identity/CC0、predictor、primary/secondary checkpoint relative pathとSHA-256を保持する。
- `validate_source_checkout(root: Path, contract: RecipeCSourceContract = RECIPE_C_SOURCE) -> dict[str, object]` はGit HEADと固定file hashを検証する。
- `validate_support_artifacts(primary_root: Path, secondary_root: Path, contract: RecipeCSourceContract = RECIPE_C_SOURCE) -> dict[str, object]` はprimaryの`repo/scripts/predict_unet_transformer.py`、primary checkpoint、別artifactのsecondary checkpointを検証する。
- primary relative pathは `weights/unet_transformer/split_0/edge_predictor_best.pth`。secondary配布時relative pathも同じだが、run-local stagingでは `weights/unet_transformer/seed_314159/edge_predictor_best.pth` へ配置する。
- configはsource `configs/experiments/recipe_c_motion_off_edge_0_40_det0_96875.yaml` と同一内容、期待SHA-256 `0e5758f3ea76ba015fb71c35bc749e136c009237e093d544a89a4b03a8c66ced` とする。
- predictorは `c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9`、checkpointはprimary `12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771`、secondary `9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f`。

- [x] **Step 1: hash不一致とasset不足の失敗テストを書く**

```python
def test_source_contract_rejects_wrong_commit(tmp_path, fake_source_tree):
    fake_source_tree.write_git_head("0" * 40)
    with pytest.raises(ValueError, match="source commit"):
        validate_source_checkout(fake_source_tree.root)


def test_support_contract_requires_both_distinct_checkpoints(tmp_path, fake_primary, fake_secondary):
    fake_secondary.checkpoint.unlink()
    with pytest.raises(FileNotFoundError, match="seed_314159"):
        validate_support_artifacts(fake_primary.root, fake_secondary.root)
```

- [x] **Step 2: source testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_source.py'`

Expected: missing `biohub.recipe_c.source` でFAIL。

- [x] **Step 3: canonical SHA-256検証とconfigを最小実装する**

hashは1 MiB chunkで読み、manifestへabsolute credential pathを保存しない。source receiptにはsource commit、file hashes、license、config values、checkpoint hashesをcanonical JSONとして返す。

- [x] **Step 4: source testをGREENで実行する**

Run: Task 1 Step 2と同じ。

Expected: 全テストPASS。

- [x] **Step 5: Task 1だけをcommitする**

```bash
git add configs/biohub_095_recipe_c.yaml src/biohub/recipe_c tests/test_recipe_c_source.py
git commit -m "Pin public Biohub Recipe C source and assets"
```

実測: 初回REDはmodule欠落。review 3 roundのfail-closed hardening後、最終targeted `82 passed`、全repo `416 passed, 9 skipped, 2 warnings`、Ruff pass。実装range `976e87c..17135f0` は独立Luna reviewでAPPROVED（finding 0）。

### Task 2: immutable protocolとselection lockを機械化する

**Files:**
- Create: `src/biohub/recipe_c/protocol.py`
- Create: `tests/test_recipe_c_protocol.py`
- Create: `scripts/run_biohub_095.py`（`freeze` subcommandのみ）

**Interfaces:**
- `PANEL_V1: tuple[str, ...]` は固定5件を順序付きで公開する。
- `ExperimentSpec` はfrozenな事前登録として、`experiment_id`、`method_family`、仮説、expected gain、cost、risk、novelty、変更点、control ID、採否基準、prior evidence receipt hashを保持し、空値・非finite値を拒否する。
- `build_selection_lock(source_receipt, config_path, code_commit, requested_device, experiment: ExperimentSpec, prior_evaluation_receipts=()) -> dict[str, object]` はpanel/config/source/asset/experiment/prior evidence identityをcanonical hash化する。
- `write_selection_lock(path: Path, payload: Mapping[str, object]) -> Path` はexclusive createで既存file/directory/symlinkを上書きせず、`ground_truth_used_for_prediction=false`、`ground_truth_used_for_parameter_fitting=false`、`panel_status=retrospective_adaptive_research` を必須にする。
- `validate_selection_lock(path: Path) -> dict[str, object]` はlock IDをpayloadから再計算し、5件のexact順序、config bytes、source/predictor/checkpoint identity、clean code HEAD、device policy、GT境界を直接キーで再検証する。旧receiptのfield alias探索や動的panel選択は使わない。
- `scripts/run_biohub_095.py freeze` は `artifacts/biohub_095/selection_lock.json` を作る。prior evaluationを使った場合はreceipt hash、`ground_truth_used_for_method_family_selection=true`、`ground_truth_usage_scope=post_prediction_analysis_only`を記録する。

- [x] **Step 1: panel変更・GT選択・lock上書きの失敗テストを書く**

```python
def test_selection_lock_rejects_changed_panel(valid_lock):
    valid_lock["panel"]["sample_ids"] = valid_lock["panel"]["sample_ids"][:-1]
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_is_write_once(tmp_path, valid_lock):
    path = write_selection_lock(tmp_path / "selection_lock.json", valid_lock)
    with pytest.raises(FileExistsError):
        write_selection_lock(path, valid_lock)


@pytest.mark.parametrize(
    "field", ("ground_truth_used_for_prediction", "ground_truth_used_for_parameter_fitting")
)
def test_selection_lock_rejects_forbidden_gt_usage(valid_lock, field):
    valid_lock["ground_truth_usage"][field] = True
    with pytest.raises(ValueError, match="ground truth|GT"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_rejects_reordered_panel(valid_lock):
    valid_lock["panel"]["sample_ids"] = list(reversed(PANEL_V1))
    with pytest.raises(ValueError, match="PANEL_V1"):
        validate_selection_lock_payload(valid_lock)


def test_selection_lock_recomputes_id(valid_lock):
    valid_lock["experiment"]["hypothesis"] = "post-hoc mutation"
    with pytest.raises(ValueError, match="selection_lock_id"):
        validate_selection_lock_payload(valid_lock)
```

- [x] **Step 2: protocol testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_protocol.py'`

Expected: missing protocol symbolsでFAIL。

- [x] **Step 3: canonical lockとfreeze CLIを実装する**

`validate_source_checkout()`と`validate_support_artifacts()`の認証済みreceiptをfreeze前に作り、今回のlockは`selection_lock_id`、`experiment_id`、`source_commit`、license/notebook/config SHA、predictor/両checkpoint SHA、dataset version/license、`requested_device`、`ground_truth_used_for_prediction=false`、`ground_truth_used_for_parameter_fitting=false`を直接必須化する。configはparse/re-dumpせず実bytesをhash化し、source identityは`RECIPE_C_SOURCE`と照合する。lock IDは `selection_lock_id` を除くpayloadのcanonical JSON SHA-256として再計算する。JSON NaN/Inf、未知field、absolute/credential/GT path、40桁lowercase SHA-1でないcode commit、dirty checkoutを拒否する。

prior evidenceが空ならmethod-family selection flagはfalse、非空ならtrueかつscopeは`post_prediction_analysis_only`でなければならない。prior receipt本体やGT edge/座標/metricをlockへコピーせず、強いGT ordering evidenceを持つreceiptのcanonical file hashと用途だけを保存する。writeはvalidation後に`open(..., "x")`相当で排他的に作成し、書込み後の再読・canonical bytes・lock ID検証まで行う。

- [x] **Step 4: protocol/CLI testをGREENで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_protocol.py tests/test_recipe_c_source.py'`

Expected: 全テストPASS。

- [x] **Step 5: Task 2だけをcommitする**

```bash
git add src/biohub/recipe_c/protocol.py tests/test_recipe_c_protocol.py scripts/run_biohub_095.py
git commit -m "Add immutable Biohub 0.95 selection lock"
```

実測: initial REDはmodule欠落、review fix roundは`11 failed`、final integrity roundは`25 failed`。最終targeted `143 passed`、全repo `536 passed, 9 skipped, 2 warnings`、Task 2対象Ruffと`git diff --check`はpass。実装commit `0449c7e`、`3b46eb1`、`e1416e4` はfresh Luna最終reviewで`APPROVED`（blocking finding 0）。dirfd/O_NOFOLLOW、atomic no-clobber、directory fsync、実prediction/manifest再hash、hidden Git flag拒否まで確認した。

### Task 3: run-local source/dual-support stagingとdevice fallbackを実装する

**Files:**
- Create: `src/biohub/recipe_c/staging.py`
- Create: `src/biohub/recipe_c/device_patch.py`
- Create: `tests/test_recipe_c_staging.py`
- Modify: `scripts/run_biohub_095.py`（`dry-run` subcommand）

**Interfaces:**
- `stage_recipe_c_runtime(source_root, primary_support_root, secondary_support_root, destination, selection_lock) -> RuntimeStage` はsourceと両supportを検証してから、期待predictor hashを持つprimaryのpristine `repo/`だけを新規run directoryへcopyする。primary/secondary checkpointはread-only元pathを指すsymlinkとして、staged support treeのsource期待pathへ配置する。
- `apply_device_fallback_patch(predictor_path: Path) -> bool` はregular-file support scriptのexact `cuda if available else cpu` preimageが一箇所だけある場合に `cuda → mps → cpu` へ置換する。exact postimageはbytes不変で`False`、未知・複数preimage・symlinkは書込み前に失敗し、patch後compileを必須にする。
- `RuntimeStage` はstaged `repo_dir`、`weights_root`、orchestration `source_root`、staged config、selection lock ID、predictor patch前後SHA、resolved device候補、role-relative receipt identityを保持する。credential/absolute source pathはreceiptへ保存しない。
- destinationのfile/directory/dangling symlinkと親symlinkを拒否し、dirfd/O_NOFOLLOWでatomicに所有権を確保する。失敗した自分のpartial stageは`FAILED.json`で再利用不能にし、既存pathを削除・上書きしない。
- 元source/primary support/secondary supportはsymlink-aware snapshotをstaging前後で比較する。source/support内の外部・dangling symlink、copy中のinode/size変化、primary/secondary target同一性、staged predictor/checkpoint hash不一致を拒否する。

- [ ] **Step 1: 元artifact不変・patch idempotence・fallback順序の失敗テストを書く**

```python
def test_staging_never_mutates_source_or_support(tmp_path, fake_source, fake_primary, fake_secondary, valid_lock):
    before = digest_trees(fake_source.root, fake_primary.root, fake_secondary.root)
    stage_recipe_c_runtime(
        fake_source.root, fake_primary.root, fake_secondary.root, tmp_path / "stage", valid_lock
    )
    assert digest_trees(fake_source.root, fake_primary.root, fake_secondary.root) == before


def test_device_patch_contains_cuda_mps_cpu_order(tmp_path, predictor_preimage):
    path = tmp_path / "predict.py"
    path.write_text(predictor_preimage)
    assert apply_device_fallback_patch(path) is True
    text = path.read_text()
    assert text.index("cuda") < text.index("mps") < text.index("cpu")
    assert apply_device_fallback_patch(path) is False


def test_staging_rejects_existing_or_symlink_destination(tmp_path, valid_inputs, valid_lock):
    destination = tmp_path / "stage"
    destination.symlink_to(tmp_path / "missing")
    with pytest.raises((FileExistsError, ValueError), match="symlink|exists"):
        stage_recipe_c_runtime(*valid_inputs, destination, valid_lock)


def test_device_patch_rejects_unknown_or_multiple_preimage_without_write(tmp_path):
    path = tmp_path / "predict.py"
    path.write_text("unknown preimage")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="preimage"):
        apply_device_fallback_patch(path)
    assert path.read_bytes() == before
```

- [ ] **Step 2: staging testをREDで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run pytest -q tests/test_recipe_c_staging.py'`

Expected: missing staging/device patchでFAIL。

- [ ] **Step 3: immutable stagingとstrict source patchを実装する**

copy先が存在する場合は削除・上書きしない。各runはprimary v10 `repo/` 13/13 filesとpristine predictorから一度だけstageする。Task 3ではdevice互換patchだけを適用し、external D4/dual-seed/edge-threshold/margin/pairwise patchは適用しない。これらはTask 4でpinned `biohub_pipeline.inference`から一度だけ実行し、非idempotent edge-threshold patchの二重適用を防ぐ。本repoはalgorithm patchを再実装しない。secondaryは配布元の`split_0`からsource期待の`seed_314159` pathへstageし、元artifactは変更しない。

実assetは`artifacts/biohub_095/`へ取得済み。source commit `843a47f...`はclean、primary v10 `repo/`はKaggle file list 13/13、controlled import/compile pass、predictor `c44e771b...`、primary `12f6881...`、secondary v2 `9bac2fa...` はTask 1 validatorでも一致した。

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
- external `run_prediction()` はCUDA未検出時の強制停止を含むため呼ばない。`build_predict_command()` が返すargvをadapterが `subprocess.run(argv, cwd=stage.repo_dir, env={**os.environ, "PYTHONPATH": "src"}, shell=False, check=True)` で実行し、staged predictorのdevice patchにより `CUDA → MPS → CPU` を選ぶ。
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

subprocessはargument listで実行し、shell interpolationを使わない。一次sourceのCUDA-only wrapper bypassはalgorithm変更ではなくdevice/orchestration互換adaptationとして、wrapper source hash、生成argv、split path、patch flags、device patch前後hashをreceiptへ記録する。predict完了後は期待GEFF数を検証し、外部 `postprocessing.configure()` → `write_submission_from_geff()` → CSV integrity/out-degree診断を同じ順で実行する。pandasを要求する外部`fixed8_cv`/`evaluation`はimportしない。途中失敗時はpartial directoryに`FAILED.json`を残し、valid manifestを作らない。GEFF writerはtracksdataの既存attribute contractを使い、postprocess algorithm自体は外部sourceを呼ぶ。

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
- `aggregate_panel_receipts(receipts, selection_lock, control_receipt=None) -> dict[str, object]` は5件のexact set/order、共通config/source/checkpoint/lock、成功statusを検証し、5で割るunweighted macroを計算する。
- aggregateはper-sample Edge/Division TP/FP/FN、Edge/Adjusted/Division Jaccard、Final Score、node/edge/fork count、runtime/device/path/hashに加え、macro、median、control勝率、worst-case harm、division performanceを保持する。
- 1件でも欠損・失敗・NaN Final Scoreならgate statusは`INCOMPLETE`で、成功sampleだけのmacroを出さない。
- `compare_with_best_known(candidate, best_known) -> dict[str, object]` はcandidate/best双方のexact panel順序、公式metric identity/version/hash、5件の成功prediction/evaluation receipt、selection lock/source/config/checkpoint整合、finite score、unweighted macro算術を再検証する。candidate macroが厳密に高く、事前登録したcontrol勝率・worst-case harm・division guardrailも満たす場合だけ`promoted=true`とし、同点・欠損・単一sample win・未定義guardrailは昇格させない。

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
- Artifacts only: `artifacts/biohub_095/source/`, `artifacts/biohub_095/support/primary/`, `artifacts/biohub_095/support/secondary/`, `artifacts/biohub_095/smoke/`

- [ ] **Step 1: sourceを固定commitへ取得して検証する**

```bash
git clone https://github.com/asapacsin/biohub-cell-tracking.git artifacts/biohub_095/source/clean_v106
git -C artifacts/biohub_095/source/clean_v106 checkout 843a47fdd531bdf7e6377673135519c54b69ae28
```

Expected: `validate_source_checkout` がcommit/license/config/notebook SHAをPASS。

- [ ] **Step 2: Kaggle support artifactを必要範囲だけ取得する**

```bash
kaggle datasets download pilkwang/biohub-tracking-support-pack-50ep-v1/10 -p artifacts/biohub_095/support/primary --unzip
kaggle datasets download pilkwang/biohub-temporal-unet3d-seed314159-v1/2 -p artifacts/biohub_095/support/secondary --unzip
```

Expected: primary/support repoとprimary checkpoint、別配布secondary checkpointが存在し、predictor `c44e771b...`、primary `12f688...`、secondary `9bac2f...` と一致。両asset合計の展開量は約711 MB、version/licenseはv10/v2・CC0。credential/tokenをlog/reportへ保存しない。

- [ ] **Step 3: selection lockとdry-runを作る**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py freeze --source artifacts/biohub_095/source/clean_v106 --primary-support artifacts/biohub_095/support/primary --secondary-support artifacts/biohub_095/support/secondary --config configs/biohub_095_recipe_c.yaml --output artifacts/biohub_095/selection_lock.json --device auto'`

Expected: write-once lock、両checkpoint hash一致、resolved backend情報あり。

- [ ] **Step 4: train実画像の2-frame smokeをGT-freeで実行する**

Run: `docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py infer --selection-lock artifacts/biohub_095/selection_lock.json --source artifacts/biohub_095/source/clean_v106 --primary-support artifacts/biohub_095/support/primary --secondary-support artifacts/biohub_095/support/secondary --image-root /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/artifacts/detector_fixed_race/panel_data/train --sample 44b6_0113de3b --max-frames 2 --output artifacts/biohub_095/smoke'`

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
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py infer-panel --selection-lock artifacts/biohub_095/selection_lock.json --source artifacts/biohub_095/source/clean_v106 --primary-support artifacts/biohub_095/support/primary --secondary-support artifacts/biohub_095/support/secondary --image-root /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/artifacts/detector_fixed_race/panel_data/train --output artifacts/biohub_095/panel_runs --device auto'
```

Expected: 5/5 prediction GEFF/manifest。CUDAならCUDA、Mac native実行でMPS、現Linux DockerではCPU。resolved deviceをsampleごとに記録。

- [ ] **Step 3: 各predictionを個別にofficial evaluationする**

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/biohub-095-performance/biohub-cell-tracking-during-development && PYTHONPATH="$PWD/src" uv run python scripts/run_biohub_095.py evaluate-panel --selection-lock artifacts/biohub_095/selection_lock.json --prediction-root artifacts/biohub_095/panel_runs --gt-root /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/artifacts/detector_fixed_race/panel_data/train --output artifacts/biohub_095/panel_receipt.json'
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

### Task 8: 未達時にRAM-safe診断と次experiment選択を実装する

**Condition:** Task 7でofficial macro `< 0.95`、またはsource側参考値とofficial値にmaterialな不一致がある場合だけ実行する。達成時は理由付きでskipする。

**Files:**
- Create: `src/biohub/detector_fixed_race/diagnostics.py`
- Create: `tests/test_detector_fixed_diagnostics.py`
- Modify: `src/biohub/detector_fixed_race/cli.py`（`diagnose` subcommand）

**Interfaces:**
- prediction manifest/cache hashをGT open前に検証する。
- node coverage、candidate coverage、true-edge score/rank、detector-fixed edge-perfect oracle、postfilter renormalizationを分離する。
- 1,000,000行chunkと既存`candidate_edges.mmap`を使い、E長array/Python listを作らない。
- 診断後はexpected gain/cost/risk/noveltyで複数仮説を順位付けし、次experimentのmethod/model familyをlockする。GTをmodel input/parameter fittingへ渡さない。

- [ ] **Step 1: orientation、one-to-one matching、coverage欠損理由、oracle、chunk境界の失敗テストを書く**
- [ ] **Step 2: `tests/test_detector_fixed_diagnostics.py` をREDで確認する**
- [ ] **Step 3: GTを推論へ返さない診断を最小実装する**
- [ ] **Step 4: 診断testをGREENで確認する**
- [ ] **Step 5: 全5 sampleを診断し、次method/model familyの仮説選択へ使うが、GT parameter fittingには使わず日本語で原因を記録する**
- [ ] **Step 6: Task 8だけをcommitする**

Task 7以降も未達なら、各experimentを事前lock→GT-free prediction persist→公式評価→BestKnown判定の順で反復する。同一family 5回連続未更新でfailure analysisへ戻り、全family通算10回meaningful improvementなしでarchitecture-level reviewと外部published method再調査へ切り替える。

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
