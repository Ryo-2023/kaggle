# Biohub Multi-Method Benchmark Race 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 同一の実データと公式 evaluator で `blob_lap`、`cc_flow`、`motion_lap` を実行し、official/harmonic 基準と比較できる日本語 benchmark report を作る。

**Architecture:** race orchestrator は sample/cache/method/evaluation を分離する。detector candidates は可能な範囲で一度だけ cache し、各 linker は自分の score を再計算する。全 prediction は GEFF reload と manifest の後にだけ evaluation へ渡す。

**Tech Stack:** Python 3.11、既存 `biohub-dev`、Zarr、NumPy/SciPy、tracksdata/GEFF、RoyerLab official metric、既存 Strong Baseline v1 provenance/manifest evaluator。

**Spec:** `docs/superpowers/specs/2026-08-20-multi-method-benchmark-race-design.md`

## Global constraints

- Python、test、smoke、inference、evaluation は既存 `biohub-dev` 内で実行する。
- GT は evaluation phase 以外で開かない。未注釈 prediction を負例にしない。
- `44b6_0113de3b.zarr`、physical scale、max distance、official evaluator revision を固定する。
- 大規模 training と大量 sweep は行わない。methodごとに事前固定 config を一つだけ使う。
- 公式ソース/checkpointの commit、version、SHA、license、failure を保存する。
- artifacts は Git 管理外、race report と compact unit fixtures は日本語で追跡する。

---

### Task 1: race contract と cache manifest

**Files:**
- Create: `src/biohub/benchmark_race/__init__.py`
- Create: `src/biohub/benchmark_race/contracts.py`
- Create: `src/biohub/benchmark_race/cache.py`
- Create: `tests/test_benchmark_race_contracts.py`

**Interfaces:**
- `SampleSpec` は sample_id、image_stem、shape、scale、quantiles を持つ。
- `RaceRequest` は sample、cache_root、output_root、expected_device、config を持ち、GT field を持たない。
- `MethodSpec` は method_id、family、detector_id、linker_id、version、requires を持つ。
- `build_cache_manifest()` は deterministic JSONを返し、`ground_truth_included` を false に固定する。

- [ ] Step 1: GT path、`.geff` image、manifest leakageを拒否する failing testsを書く。
- [ ] Step 2: containerで対象testを実行し、import failureまたは assertion failureを確認する。
- [ ] Step 3: contracts/cacheを実装し、path・axis・scale・GT leakageを fail closed にする。
- [ ] Step 4: targeted test と Ruff を実行する。

### Task 2: `blob_lap` detector/linker

**Files:**
- Create: `src/biohub/benchmark_race/blob_lap.py`
- Create: `tests/test_benchmark_race_blob_lap.py`
- Modify: `scripts/run_benchmark_race.py`

**Interfaces:**
- `detect_blob_candidates(image, config) -> CandidateTable`
- `link_blob_lap(candidates, config) -> EdgeTable`
- `run_blob_lap(request) -> PredictionArtifact`

- [ ] Step 1: synthetic two-frame peak fixtureで candidate shape、physical distance、Hungarian edgeの failing testsを書く。
- [ ] Step 2: failing testをcontainerで確認する。
- [ ] Step 3: fixed Gaussian/local-max/NMS と 1-to-1 LAP linker を実装する。division disabled、config、countsをreceiptに保存する。
- [ ] Step 4: synthetic smokeでGEFF reload、manifest、GT path不在を確認する。
- [ ] Step 5: real sample max_frames=2 smokeを実行する。

### Task 3: `cc_flow` detector/global linker

**Files:**
- Create: `src/biohub/benchmark_race/cc_flow.py`
- Create: `tests/test_benchmark_race_cc_flow.py`
- Modify: `scripts/run_benchmark_race.py`

**Interfaces:**
- `detect_cc_candidates(image, config) -> CandidateTable`
- `link_cc_flow(candidates, config) -> EdgeTable`
- `run_cc_flow(request) -> PredictionArtifact`

- [ ] Step 1: synthetic separated blobsで component centroidとglobal edgeの failing testsを書く。
- [ ] Step 2: containerでREDを確認する。
- [ ] Step 3: quantile foreground、3D connected components、physical centroid、global flow/ILPを実装する。GTを閾値選択に使わない。
- [ ] Step 4: GEFF round-tripとmanifest boundaryのGREENを確認する。
- [ ] Step 5: real sample max_frames=2 smokeを実行する。

### Task 4: velocity-aware `motion_lap` association

**Files:**
- Create: `src/biohub/benchmark_race/motion.py`
- Create: `tests/test_benchmark_race_motion.py`
- Modify: `scripts/run_benchmark_race.py`

**Interfaces:**
- `motion_cost(source, target, velocity, config) -> float`
- `link_motion(candidates, edge_scores, config) -> EdgeTable`
- `run_motion_lap(request, blob_cache) -> PredictionArtifact`

- [ ] Step 1: deterministic velocity/acceleration cost fixtureのfailing testsを書く。
- [ ] Step 2: REDをcontainerで確認する。
- [ ] Step 3: fixed `blob_lap` candidate cacheを読み、GTを参照しない velocity/acceleration prior linkerを実装する。`classical_motion_association` と記録し、official detector shared lane は deferred と明記する。
- [ ] Step 4: smokeでprediction GEFFとmanifestを確認する。
- [ ] Step 5: full run前に runtime/candidate count/solver timeout gateを確認する。

### Task 5: race CLI、evaluation、比較表

**Files:**
- Create: `scripts/run_benchmark_race.py`
- Create: `tests/test_benchmark_race_cli.py`
- Create: `tests/test_benchmark_race_report.py`
- Create: `docs/results/multi_method_benchmark_race.md`

**Interfaces:**
- CLI `smoke`, `infer`, `evaluate`, `summarize`。inference subcommandにGT optionを持たせない。
- `evaluate` はmanifestを検証してからGTをopenする。
- `summarize` はofficial/harmonic/blob_lap/cc_flow/motion_lapの全metrics/delta/failureを日本語Markdownに書く。

- [ ] Step 1: report table binding、GT boundary、blocked method disclosureのfailing testsを書く。
- [ ] Step 2: REDをcontainerで確認する。
- [ ] Step 3: CLIとsummary schemaを実装する。
- [ ] Step 4: reportをreceiptから生成し、全metricsと日本語のknown limitationsを保存する。
- [ ] Step 5: report test、full pytest、targeted Ruffを実行する。

### Task 6: candidate feasibility lanes

**Files:**
- Create: `docs/results/multi_method_feasibility_ja.md`
- Generate ignored only: `artifacts/multi_method_race/feasibility/*`

- [ ] Step 1: HOCT、Trackastra、Ultrack、Linajea、DeepCenterの公式source/checkpoint/input compatibilityを記録する。
- [ ] Step 2: dependenciesが既存環境を壊さない候補だけ2–5 frame smokeを行う。
- [ ] Step 3: blocked理由を推測で埋めず、source URL、version、license、missing dependency/adapterを日本語で保存する。

### Task 7: full race and final decision

**Files:**
- Generate ignored only: `artifacts/multi_method_race/<run_id>/*`
- Modify: `docs/results/multi_method_benchmark_race.md`

- [ ] Step 1: smoke GREEN の全 laneだけ固定 configで100 frame real inferenceを実行する。
- [ ] Step 2: 各 GEFFをreload/hashしてから、別phaseで公式 evaluatorを実行する。
- [ ] Step 3: Final Score、Adjusted/Edge Jaccard、edge/division counts、node/edge、runtime/device、failure、baseline差を表にする。
- [ ] Step 4: Best Method、次に深掘りする候補、相補 componentを性能・安定性・時間・複雑度・将来性で決める。
- [ ] Step 5: full pytest、Ruff、artifact inventory、GT分離、Git statusを再確認し、Done条件を監査する。
