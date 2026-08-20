# Detector-Fixed Association Race + Multi-Sample Validation 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公式TemporalUNet3Dを各sampleで一度だけ実行してGT非依存のdetector cacheを永続化し、同一cacheから公式ILP・harmonic v1・追加2 association以上を生成して、開発sampleと3 sample以上の固定validation panelで公式metricを比較する。

**Architecture:** pinned upstream checkoutは変更せず、project側のinstrumented adapterが`predict_video`のdetector出力とforward edge logitsを一度の画像推論で捕捉する。cache readerだけを受け取るassociation層が公式/harmonic/mutual-confidence/motion-gatedを同じnode・candidate集合から生成し、GEFF writerと公式metric adapterがreceiptをhashで連結する。GTはmetric adapterがprediction manifestを検証した後にだけ開く。

**Tech Stack:** Python 3.11、NumPy、PyTorch、Zarr、tracksdata/ILP、既存`biohub.strong_baseline`およびcompetition公式metric、pytest、ruff、Docker Compose `biohub-dev`。

**Spec:** `docs/superpowers/specs/2026-08-20-detector-fixed-association-race-design.md`

## Global Constraints

- detector実行はsampleごとの`materialize_detector_cache`一回だけとし、associationはcache以外の画像・GT・checkpointを開かない。
- cache manifestの`ground_truth_included`は常に`false`で、GT path、`.geff`、annotation/label/truthキーを拒否する。
- 軸順は画像`(T,Z,Y,X)`、空間physical scale`(Z,Y,X)`、node座標`(t,z,y,x)`を維持する。
- candidate edgeは隣接時刻`target_t=source_t+1`、node IDの向き`source<target`を検証する。divisionはGEFF/ILPの意味を通常edgeへ変換しない。
- controlは公式association+ILPとharmonic v1（reverse weight `0.20`）。追加primaryはmutual-confidenceとmotion-gatedの2本で、各設定をdevスコアを見る前に固定する。
- `cc_flow`と現行`motion_lap`は今回のdetector-fixed primary raceへ再投入しない。blob NMSは独立高速branchとして別記録する。
- 重い実行、pytest、ruff、metricは`biohub-dev` container内で行い、hostへ依存をinstallしない。
- Kaggle外部提出は行わない。大容量cache/GEFF/metricsはGit管理せず、再現可能なmanifestとレポートを管理する。
- すべてのユーザー向け結果レポートは日本語で書く。外部versionは一次配布元と確認日を記録し、`Trackastra`は2026-08-20時点のGitHub/PyPI `0.5.5`を採用する。

---

### Task 1: Detector cache schemaとGT-free契約

**Files:**
- Create: `src/biohub/detector_fixed_race/__init__.py`
- Create: `src/biohub/detector_fixed_race/schema.py`
- Create: `src/biohub/detector_fixed_race/cache.py`
- Test: `tests/test_detector_fixed_cache.py`

**Interfaces:**
- `schema.py`は`NodeArrays`、`CandidateEdgeArrays`、`DetectorCache`、`CacheReceipt`を公開する。
- `NodeArrays`は`node_id:int64`、`tzyx:int16/int32`、`physical_zyx:float32`、`detector_peak_logit:float32`、`detector_peak_probability:float32`、`node_features:float32[C]`を保持する。
- `CandidateEdgeArrays`は`source_node_id:int64`、`target_node_id:int64`、`delta_t:int16`、`voxel_delta:float32[3]`、`physical_delta:float32[3]`、`voxel_distance:float32`、`physical_distance:float32`、`forward_logit:float32`、`reverse_logit:float32`、`forward_probability:float32`、`reverse_probability:float32`を保持する。全source-target組み合わせを保存し、forward threshold後のedgeだけに切り詰めない。
- `build_detector_cache_manifest(sample: SampleSpec, image_sha256: str, detector_config: Mapping[str, Any], provenance: Mapping[str, Any], node_digest: str, edge_digest: str) -> dict[str, Any]`はcanonical JSONから`cache_hash`を作る。
- `write_detector_cache(root: Path, manifest: Mapping[str, Any], nodes: NodeArrays, edges: CandidateEdgeArrays) -> CacheReceipt`は一時directoryへ書き、digest検証後に`READY`を置き、atomic renameする。
- `load_detector_cache(root: Path) -> DetectorCache`は`READY`、manifest、全artifact digest、shape、dtype、座標向きを検証してから読み込む。

- [ ] **Step 1: GT混入・shape・hashの失敗テストを書く**

```python
def test_detector_cache_manifest_rejects_ground_truth_fields(sample_spec):
    with pytest.raises(ValueError, match="ground.?truth|annotation|label"):
        build_detector_cache_manifest(
            sample_spec,
            image_sha256="image",
            detector_config={"annotation_path": "labels.json"},
            provenance={"source_commit": "abc"},
            node_digest="nodes",
            edge_digest="edges",
        )


def test_detector_cache_rejects_wrong_edge_direction(tmp_path, sample_arrays):
    manifest = valid_manifest()
    bad_edges = replace(sample_arrays.edges, source_node_id=np.array([4]), target_node_id=np.array([2]))
    with pytest.raises(ValueError, match="source|target|time"):
        write_detector_cache(tmp_path / "cache", manifest, sample_arrays.nodes, bad_edges)


def test_detector_cache_requires_ready_and_digest(tmp_path, sample_arrays):
    receipt = write_detector_cache(tmp_path / "cache", valid_manifest(), sample_arrays.nodes, sample_arrays.edges)
    assert receipt.cache_hash == load_detector_cache(receipt.root).manifest["cache_hash"]
    (receipt.root / "READY").unlink()
    with pytest.raises(ValueError, match="READY"):
        load_detector_cache(receipt.root)
```

- [ ] **Step 2: テストをredで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_cache.py`

Expected: `FAIL` with missing `biohub.detector_fixed_race` symbols or未実装例外。

- [ ] **Step 3: 最小のschemaとatomic cacheを実装する**

`schema.py`で全arrayの一次元長、node idの連番、`t`の単調性、`source_node_id < target_node_id`、`target_t > source_t`、finite floatを検証する。`cache.py`では`np.savez_compressed`と`json.dumps(sort_keys=True, allow_nan=False)`を用い、`<root>.tmp-<pid>`へ書いて`READY`作成後に`os.replace`する。manifestにはartifactごとのSHA-256、`ground_truth_included: false`、`cache_hash`を保存する。

- [ ] **Step 4: 同じテストをgreenで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_cache.py`

Expected: 全テストPASS。

- [ ] **Step 5: 契約だけをコミットする**

```bash
git add src/biohub/detector_fixed_race tests/test_detector_fixed_cache.py
git commit -m "Add detector-fixed cache contracts"
```

### Task 2: pinned upstreamを一回だけ実行するcapture adapter

**Files:**
- Create: `src/biohub/detector_fixed_race/upstream_adapter.py`
- Test: `tests/test_detector_fixed_upstream_adapter.py`
- Modify: `src/biohub/detector_fixed_race/schema.py`（必要なprovenance型のみ）

**Interfaces:**
- `CaptureConfig`は`det_threshold=0.99`、`pool_kernel_um=3.0`、`edge_activation="softmax"`、`edge_threshold=0.5`、`det_tta=True`、`window_size`、`downsample`、`unet_batch_size`を保持する。
- `materialize_detector_cache(image_path: Path, upstream_root: Path, checkpoint: Path, output_root: Path, sample: SampleSpec, config: CaptureConfig, expected_device: str, max_frames: int | None = None) -> CacheReceipt`が唯一のdetector入口である。
- adapterは既存`_load_upstream_predictor`相当でpinned commit `075fc5f5a52d11077f9dc2b074644618f26939e2`をロードし、`predict_video`のglobal `_detect_cells_pooled`とmodel `predict_edges`を一時wrapperする。pinned checkout自体は編集しない。
- detection wrapperはframe`t`、downsample node座標、peak logit/probabilityを記録する。edge wrapperはforward raw logits、`_index_features`出力、source/target座標、geometryを記録し、upstreamへforward logitsをそのまま返す。
- `predict_video`終了後、保存したfeature/positionを逆順に一度だけ`original_predict_edges`へ渡してreverse raw logitsを作る。画像/UNet encodeは再実行しない。
- receiptの`provenance`にはupstream repo/commit、adapter source SHA、checkpoint SHA、実config、torch/device、runtime、detector call countを含める。

- [ ] **Step 1: fake predictorでdetector一回契約の失敗テストを書く**

```python
def test_materializer_calls_predict_video_once_and_writes_forward_reverse_logits(tmp_path, fake_upstream):
    receipt = materialize_detector_cache(
        image_path=Path("sample.zarr"),
        upstream_root=fake_upstream.root,
        checkpoint=Path("checkpoint.pth"),
        output_root=tmp_path,
        sample=sample_spec,
        config=CaptureConfig(),
        expected_device="cpu",
    )
    cache = load_detector_cache(receipt.root)
    assert fake_upstream.predict_video_calls == 1
    assert fake_upstream.encode_calls == 1
    assert cache.edges.forward_logit.shape == cache.edges.reverse_logit.shape
    assert cache.manifest["ground_truth_included"] is False
```

- [ ] **Step 2: fake adapter testをredで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_upstream_adapter.py::test_materializer_calls_predict_video_once_and_writes_forward_reverse_logits`

Expected: `FAIL` because the materializer and capture hooks are not defined。

- [ ] **Step 3: upstream capture wrapperを実装する**

検出結果をframeごとに保持し、pair wrapperの呼び出し順から未処理のnon-empty隣接pairを割り当てる。source/target座標をframe別node registryへ照合してglobal node IDを決め、同一nodeのfeatureが複数pairで現れた場合は`np.testing.assert_allclose`で一致を検証する。raw logitsは`softmax(dim=0)`のforward/reverse probabilityと共にfloat32で保存する。例外時はpartial directoryを削除し、`READY`を作らない。

- [ ] **Step 4: fake testとcache契約をgreenで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_upstream_adapter.py tests/test_detector_fixed_cache.py`

Expected: 全テストPASS、detector/encode call countが各1、reverse callはedge pair数だけ。

- [ ] **Step 5: adapterをコミットする**

```bash
git add src/biohub/detector_fixed_race/upstream_adapter.py src/biohub/detector_fixed_race/schema.py tests/test_detector_fixed_upstream_adapter.py
git commit -m "Capture pinned detector outputs into persistent cache"
```

### Task 3: cache-only association engine（公式・harmonic・追加2方式）

**Files:**
- Create: `src/biohub/detector_fixed_race/association.py`
- Test: `tests/test_detector_fixed_association.py`

**Interfaces:**
- `AssociationSpec(method_id: str, reverse_weight: float = 0.20, mutual_threshold: float = 0.50, motion_gate_um: float = 12.0, motion_alpha: float = 0.05)`を凍結する。dev metricを見た後に値を変更しない。
- `AssociationResult(method_id: str, cache_hash: str, selected_edges: np.ndarray, graph: Any, config: Mapping[str, Any])`を返す。
- `associate_from_cache(cache: DetectorCache, spec: AssociationSpec, *, graph_builder: Callable[..., Any], ilp_solver: Callable[[Any], Any]) -> AssociationResult`は画像path、GT path、checkpoint pathを引数に持たない。
- officialは`forward_probability`、threshold `0.5`、既存ILP cost `edge=-1, appearance=.1, disappearance=.1, division=1`を使用する。
- harmonicは既存`biohub.strong_baseline.harmonic.fuse_harmonic_logits`にforward/reverse raw logitsを渡し、reverse weight `.20`で得たprobabilityを同じILPへ渡す。
- mutualは`geometric_mean(sqrt(forward_probability * reverse_probability))`をscoreとし、score `.50`未満を除外して同じILPへ渡す。harmonicとは異なるscoreである。
- motion-gatedは`physical_distance > 12.0µm`を除外し、残りのforward probabilityへ`exp(-0.05 * physical_distance)`を乗じたscoreを同じILPへ渡す。閾値と係数は事前固定し、sweepしない。

- [ ] **Step 1: cache-only・score式・controlの失敗テストを書く**

```python
def test_association_has_no_image_or_ground_truth_parameters():
    parameters = inspect.signature(associate_from_cache).parameters
    assert "image_path" not in parameters
    assert "ground_truth_path" not in parameters
    assert "checkpoint" not in parameters


@pytest.mark.parametrize("method_id", ["official_ilp", "harmonic_v1", "mutual_confidence", "motion_gated"])
def test_all_methods_keep_same_cache_hash_and_finite_edges(fake_cache, fake_graph_builder, fake_solver, method_id):
    result = associate_from_cache(fake_cache, AssociationSpec(method_id), graph_builder=fake_graph_builder, ilp_solver=fake_solver)
    assert result.cache_hash == fake_cache.manifest["cache_hash"]
    assert np.isfinite(result.selected_edges).all()
```

- [ ] **Step 2: association testsをredで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_association.py`

Expected: `FAIL` with missing association method or cache-only contract。

- [ ] **Step 3: probability、mutual、motionの最小実装を追加する**

matrix row/columnの向きを固定し、upstreamと同じ`softmax(axis=0)`を使う。各score matrixを`(source_node_id,target_node_id,score,physical_distance)`へ変換し、`graph_builder`へ渡す。ILP出力から選択edgeだけをreceipt用のsorted arrayへ戻す。empty pairは静かに補間せず、該当pairのedge count 0として保存する。

- [ ] **Step 4: association testsをgreenで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_association.py tests/test_detector_fixed_cache.py`

Expected: 全テストPASS。4方式の`cache_hash`が一致し、motion gateが12µm超のedgeを除外する。

- [ ] **Step 5: association engineをコミットする**

```bash
git add src/biohub/detector_fixed_race/association.py tests/test_detector_fixed_association.py
git commit -m "Add cache-only association race methods"
```

### Task 4: GEFF writer、prediction receipt、公式metric境界

**Files:**
- Create: `src/biohub/detector_fixed_race/prediction.py`
- Test: `tests/test_detector_fixed_prediction.py`
- Modify: `src/biohub/strong_baseline/manifest.py`（既存manifest validatorを再利用するための最小公開wrapperのみ）

**Interfaces:**
- `write_prediction(cache: DetectorCache, result: AssociationResult, predictor_module: ModuleType, output_path: Path) -> Path`はcache node coordinatesとselected edgesからupstream `build_graph`/`save_graph`でGEFFを作り、prediction manifestを隣接JSONへ書く。
- prediction manifestは`method_id`、`cache_hash`、`prediction_sha256`、node/edge count、config、`ground_truth_included=false`を持つ。
- `evaluate_prediction(prediction_path: Path, ground_truth_path: Path, metric_config: Mapping[str, Any]) -> dict[str, Any]`はprediction manifest検証後にGTを開き、既存`biohub.strong_baseline.evaluation`/公式metricを呼ぶ。manifest検証が失敗した場合はGT open count 0で例外にする。

- [ ] **Step 1: prediction manifestのGT境界テストを書く**

```python
def test_metric_does_not_open_ground_truth_before_prediction_manifest(monkeypatch, valid_prediction, gt_path):
    events = []
    monkeypatch.setattr("biohub.detector_fixed_race.prediction._open_ground_truth", lambda p: events.append("gt"))
    write_prediction_manifest(valid_prediction)
    evaluate_prediction(valid_prediction.path, gt_path, {})
    assert events == ["gt"]


def test_invalid_prediction_manifest_rejects_metric_before_gt(monkeypatch, invalid_prediction, gt_path):
    events = []
    monkeypatch.setattr("biohub.detector_fixed_race.prediction._open_ground_truth", lambda p: events.append("gt"))
    with pytest.raises(ValueError, match="prediction manifest"):
        evaluate_prediction(invalid_prediction, gt_path, {})
    assert events == []
```

- [ ] **Step 2: prediction testsをredで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_prediction.py`

Expected: `FAIL` because GEFF/manifest/metric boundary is未実装。

- [ ] **Step 3: GEFF writerとmetric adapterを実装する**

既存official runnerのmanifest validationとcompetition evaluatorを再利用し、GT pathは`evaluate_prediction`の引数に限定する。prediction manifestを`tracksdata.graph.IndexedRXGraph.from_geff`でreloadしてnode/edge countを確認し、prediction digestを固定する。

- [ ] **Step 4: prediction testsをgreenで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_prediction.py tests/test_strong_baseline_evaluation.py`

Expected: 全テストPASS、invalid manifest時にGT openイベントが無い。

- [ ] **Step 5: writerとmetric境界をコミットする**

```bash
git add src/biohub/detector_fixed_race/prediction.py src/biohub/strong_baseline/manifest.py tests/test_detector_fixed_prediction.py
git commit -m "Write detector-fixed predictions with metric receipts"
```

### Task 5: panel固定、CLI、実験receipt

**Files:**
- Create: `src/biohub/detector_fixed_race/panel.py`
- Create: `src/biohub/detector_fixed_race/cli.py`
- Create: `scripts/run_detector_fixed_race.py`
- Test: `tests/test_detector_fixed_panel.py`
- Test: `tests/test_detector_fixed_cli.py`

**Interfaces:**
- `freeze_validation_panel(train_root: Path, gt_root: Path, development_sample: str, minimum: int = 3, maximum: int = 5, require_division_if_available: bool = True) -> dict[str, Any]`はzarr shape、GT存在、division metadataだけで決定し、metric scoreを読み込まない。
- `run_dev_race(*, sample_id: str, cache_root: Path, output_root: Path, methods: Sequence[str], gt_path: Path, predictor_module: ModuleType) -> list[dict[str, Any]]`はcacheから4 association → GEFF → official metricをsampleごとに行い、全method receiptへ同じ`cache_hash`を記録する。
- `run_panel(*, panel_path: Path, methods: Sequence[str], output_root: Path, train_root: Path, gt_root: Path) -> dict[str, Any]`はfrozen panel JSONを読み、winner選択を行わず全primary methodを同条件で実行し、mean/median score、delta、improve/harm countを集計する。
- CLI subcommandsは`freeze-panel`、`materialize`、`associate`、`evaluate`、`dev-race`、`panel`とし、GT pathを受け取るのは`evaluate`/`panel`のevaluation phaseだけにする。

- [ ] **Step 1: score-free panel選択とCLI禁止引数の失敗テストを書く**

```python
def test_panel_selection_is_deterministic_without_metric(tmp_path):
    first = freeze_validation_panel(tmp_path / "train", tmp_path / "gt", "44b6_0113de3b")
    second = freeze_validation_panel(tmp_path / "train", tmp_path / "gt", "44b6_0113de3b")
    assert first == second
    assert len(first["samples"]) >= 3
    assert "score" not in json.dumps(first)


def test_associate_cli_rejects_ground_truth_argument():
    result = runner.invoke(app, ["associate", "--ground-truth", "truth.geff"])
    assert result.exit_code != 0
    assert "ground" in result.stdout.lower()
```

- [ ] **Step 2: panel/CLI testsをredで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_panel.py tests/test_detector_fixed_cli.py`

Expected: `FAIL` with未定義のpanel/CLI。

- [ ] **Step 3: inventoryとCLIを実装する**

train zarrをlexicographic/Kaggle API順に検査し、development sampleを必ず含め、GT metadataが存在する候補を選ぶ。事前棚卸しで固定した候補は`44b6_0113de3b`、`44b6_0b24845f`、`44b6_0c582fdc`、`44b6_0db75fae`、`44b6_12dfb391`で、最後のsampleはdivision annotationが1件ある。ローカルに画像chunkが無いsampleはKaggle公開ファイルから必要なZarr/GEFFを取得してから推論し、取得元・version・digestをreceiptへ記録する。division annotationはGT metadataをpanel選択時に読むため「推論入力」には渡さず、判定根拠とpathはpanel receiptへ記録する。GPU/CPU timeoutは候補を黙って落とさず`failed_samples`へ記録する。CLIは`PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src python scripts/run_detector_fixed_race.py materialize --sample 44b6_0113de3b --device cpu --output artifacts/detector_fixed_race`で再現できる形にする。

- [ ] **Step 4: CLI/panel testsをgreenで実行する**

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_panel.py tests/test_detector_fixed_cli.py`

Expected: 全テストPASS、panel JSONにmetric scoreが含まれず、association subcommandがGTを拒否する。

- [ ] **Step 5: orchestrationをコミットする**

```bash
git add src/biohub/detector_fixed_race/panel.py src/biohub/detector_fixed_race/cli.py scripts/run_detector_fixed_race.py tests/test_detector_fixed_panel.py tests/test_detector_fixed_cli.py
git commit -m "Add detector-fixed race and validation panel CLI"
```

### Task 6: dev sampleでmaterializeとcontrol parityを検証

**Files:**
- Create (ignored runtime artifacts): `artifacts/detector_fixed_race/cache/44b6_0113de3b/`
- Create (ignored runtime artifacts): `artifacts/detector_fixed_race/dev/`
- Create: `artifacts/detector_fixed_race/dev_receipt.json`
- Modify: `docs/results/detector_fixed_association_race.md`（実測値を後Taskでまとめる）

- [ ] **Step 1: containerと入力を確認する**

Run: `docker compose ps` and `docker compose exec -T biohub python -c 'import torch; print(torch.__version__, torch.cuda.is_available())'`

Expected:既存`biohub-dev`がhealthy、CPU-onlyをreceiptへ記録。開発sampleの画像/GT、pinned checkpoint、upstream commitが存在する。

- [ ] **Step 2: detector cacheを一度だけmaterializeする**

Run: `docker compose exec -T biohub env PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0113de3b --device cpu --output artifacts/detector_fixed_race`

Expected: `READY`付きcache、manifestの`ground_truth_included=false`、node/candidate edge digest、detector call count 1。途中停止時はREADY無しで再開可能。

- [ ] **Step 3: cacheから公式/harmonicを再構成する**

Run: `docker compose exec -T biohub env PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0113de3b --methods official_ilp,harmonic_v1,mutual_confidence,motion_gated --cache artifacts/detector_fixed_race/cache/44b6_0113de3b`

Expected: 4 prediction GEFFと各metric receiptが生成され、全ての`cache_hash`が一致。official/harmonicのnode/edge/metricが既存canonical artifactsとの差を許容可能な決定論差として記録される。

- [ ] **Step 4: parity差を原因切り分けする**

差が出た場合は、まず`det_threshold`、TTA、pool kernel、softmax axis、node ID order、ILP weightsをreceipt同士で比較する。許容できない差が残る場合は追加association評価へ進まず、cache replayとupstreamの対応箇所を修正する。GT由来の補正や後付け選択は行わない。

- [ ] **Step 5: dev結果をmachine-readableに固定する**

`dev_receipt.json`へmethod、cache hash、prediction path/digest、node/edge数、Edge/Division TP/FP/FN、Jaccard、Final Score、runtime、device、command、source/checkpoint provenanceを保存する。

### Task 7: 3–5 sample validationと日本語最終レポート

**Files:**
- Create: `artifacts/detector_fixed_race/validation_panel.json`
- Create: `artifacts/detector_fixed_race/validation_receipt.json`
- Create: `docs/results/detector_fixed_association_race.md`
- Test: `tests/test_detector_fixed_report.py`

- [ ] **Step 1: 実metricを読む前にpanelをfreezeする**

Run: `docker compose exec -T biohub env PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py freeze-panel --minimum 3 --maximum 5 --development-sample 44b6_0113de3b --output artifacts/detector_fixed_race/validation_panel.json`

Expected: `44b6_0113de3b`、`44b6_0b24845f`、`44b6_0c582fdc`、`44b6_0db75fae`、`44b6_12dfb391`のうち、画像取得に成功した最低3件を含む固定panel。`44b6_12dfb391`はdivision候補として優先し、sample ID、shape、GT存在、division有無の根拠だけを含める。score、method、winnerは含まれない。

- [ ] **Step 2: panelの全primaryを同一条件で走らせる**

Run: `docker compose exec -T biohub env PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py panel --panel artifacts/detector_fixed_race/validation_panel.json --methods official_ilp,harmonic_v1,mutual_confidence,motion_gated --output artifacts/detector_fixed_race/validation`

Expected: 最低3 sample、各sample同一4 method、cache hash receipt、公式metricを取得。Kaggleから取得した画像/GTのfile digestとcompetition file versionをreceiptへ残す。失敗sampleは理由と実行時間を`failed_samples`へ残す。

- [ ] **Step 3: validation集計を作る**

methodごとにFinal Score、Adjusted Edge Jaccard、Edge/Division TP/FP/FN、node/edge count、runtime、mean/median score、mean/median delta、improve/harm countを計算する。winnerはdevで事前に決めたmethodをprimaryとして記録し、validation scoreを見た後のmethod再選択はしない。

- [ ] **Step 4: 日本語レポートを書く**

`docs/results/detector_fixed_association_race.md`に、結論、panel固定規則、detector/checkpoint/source、cache schema/hash、4 association方式、GEFF/receipt paths、全metric表、control差、runtime/device、Trackastra/HOCT等の外部候補の扱い、既知の問題、再現command、未検証項目を記録する。数値はreceiptからのみ転記し、推測値を埋めない。

- [ ] **Step 5: レポート契約テストを追加してgreenにする**

```python
def test_detector_fixed_report_is_japanese_and_cites_machine_receipts():
    text = Path("docs/results/detector_fixed_association_race.md").read_text()
    for heading in ("結論", "cache", "公式metric", "validation", "既知の問題", "再現"):
        assert heading in text
    assert "0.9211200215044129" in text or "validation_receipt.json" in text
    assert "ground_truth_included=false" in text
```

Run: `PYTHONPATH="$PWD/src" uv run pytest -q tests/test_detector_fixed_report.py`

Expected: PASS。

- [ ] **Step 6: レポートとvalidation receiptをコミットする**

```bash
git add docs/results/detector_fixed_association_race.md tests/test_detector_fixed_report.py
git commit -m "Record detector-fixed association race validation"
```

### Task 8: 全検証、差分確認、commit/push

**Files:**
- Modify only files already listed above if verification reveals a scoped defect.

- [ ] **Step 1: changed-scope testsを実行する**

Run: `docker compose exec -T biohub env PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src uv run pytest -q tests/test_detector_fixed_cache.py tests/test_detector_fixed_upstream_adapter.py tests/test_detector_fixed_association.py tests/test_detector_fixed_prediction.py tests/test_detector_fixed_panel.py tests/test_detector_fixed_cli.py tests/test_detector_fixed_report.py`

Expected: 全changed-scope tests PASS。未実行なら成功と報告しない。

- [ ] **Step 2: Ruffとdiffを確認する**

Run: `docker compose exec -T biohub env PYTHONPATH=/workspace/biohub-cell-tracking-during-development/src uv run ruff check src/biohub/detector_fixed_race tests/test_detector_fixed_*.py scripts/run_detector_fixed_race.py` and `git diff --check`。

Expected: changed scopeにRuff/whitespace error無し。既存unrelated errorは修正しない。

- [ ] **Step 3: provenanceとartifact hygieneを確認する**

Run: `git status --short --ignored | sed -n '1,160p'` and `git diff --stat HEAD~8..HEAD`。

Expected: checkpoint、Zarr、GEFF、cache、秘密情報がcommit対象外。日本語仕様/計画/レポートと小さなコード・テストだけがcommit対象。

- [ ] **Step 4: 最終commitを作成する**

```bash
git add src tests scripts docs/results/detector_fixed_association_race.md
git commit -m "Complete detector-fixed association race validation"
```

- [ ] **Step 5: branchをpushし、remoteと一致することを確認する**

Run: `git push origin codex/biohub-multi-method-race` and `git status --short --branch`。

Expected: push成功、branchが`origin/codex/biohub-multi-method-race`と一致、未追跡大容量artifact以外の変更無し。

## Self-review checklist

- Spec section 2のGT-free、座標、candidate向きはTask 1/2/4で機械検証する。
- Spec section 3のadapter境界はTask 2–4で分離し、association関数はcache-only signatureにする。
- Spec section 4のartifact digest、READY、cache hashはTask 1で保存・検証する。
- Spec section 5の公式/harmonic/mutual/motionはTask 3で同一ILP制約に乗せる。gap-awareはcandidateが隣接時刻のみであるため無理に追加しない。
- Spec section 6のscore-free panelはTask 5/7でfreezeしてからmetricを読む。
- Spec section 7のTDDは各Taskでred→実装→greenを行う。
- Spec section 8のfallbackはTask 2でupstream checkoutを編集しないinstrumentationと、失敗時のpartial cache拒否で担保する。
- Spec section 9の日本語receipt/report、再現command、既知問題はTask 7で固定する。

## Execution handoff

計画は `docs/superpowers/plans/2026-08-20-detector-fixed-association-race.md` に保存する。実装時はTask 1から順にTDDで進め、各Taskの小さなcommit後に差分とテストを主担当が確認する。並列化する場合は、cache契約、upstream adapter、panel inventoryのファイル責務を分離し、同じファイルを複数agentが編集しない。
