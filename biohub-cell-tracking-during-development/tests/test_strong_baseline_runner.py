from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import zarr

from biohub.strong_baseline import runner
from biohub.strong_baseline.runner import InferenceRequest, build_official_command


@pytest.fixture
def inference_request(tmp_path: Path) -> InferenceRequest:
    return InferenceRequest(
        upstream_root=tmp_path / "upstream",
        image_stem=tmp_path / "44b6_0113de3b",
        checkpoint=tmp_path / "model.pth",
        output_dir=tmp_path / "output.geff",
        expected_device="cpu",
    )


def _prepared_request(tmp_path: Path, output_dir: Path | None = None) -> InferenceRequest:
    upstream_root = tmp_path / "upstream"
    (upstream_root / "scripts").mkdir(parents=True)
    (upstream_root / "src").mkdir()
    (upstream_root / "scripts" / "predict_unet_transformer.py").write_text("# fake upstream\n")
    image_stem = tmp_path / "data" / "44b6_0113de3b"
    image_stem.parent.mkdir()
    image_path = image_stem.with_suffix(".zarr")
    zarr_root = zarr.open_group(image_path, mode="w")
    zarr_root.create_array("0", shape=(2, 2, 2, 2), dtype="f4")
    zarr_root.attrs["image_statistics"] = {"quantiles": {"0.001": 0.0, "0.999": 1.0}}
    checkpoint = tmp_path / "model.pth"
    checkpoint.write_bytes(b"checkpoint")
    return InferenceRequest(
        upstream_root=upstream_root,
        image_stem=image_stem,
        checkpoint=checkpoint,
        output_dir=output_dir or tmp_path / "results" / "prediction.geff",
        expected_device="cpu",
    )


def test_inference_request_rejects_geff_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="image stem"):
        InferenceRequest(
            upstream_root=tmp_path / "upstream",
            image_stem=tmp_path / "sample.geff",
            checkpoint=tmp_path / "model.pth",
            output_dir=tmp_path / "output",
            expected_device="cpu",
        )


def test_inference_command_never_contains_gt(inference_request: InferenceRequest) -> None:
    command = build_official_command(inference_request)

    assert "--evaluate" not in command
    assert not any(str(part).endswith(".geff") for part in command)
    assert command[command.index("--debug-video") + 1].endswith("44b6_0113de3b")


def test_official_command_resolves_predictor_for_upstream_cwd(tmp_path: Path) -> None:
    request = InferenceRequest(
        upstream_root=Path("artifacts") / "strong_baseline_v1" / "upstream",
        image_stem=tmp_path / "44b6_0113de3b",
        checkpoint=tmp_path / "model.pth",
        output_dir=tmp_path / "output",
        expected_device="cpu",
    )

    command = build_official_command(request)

    assert Path(command[1]) == (
        Path.cwd() / "artifacts" / "strong_baseline_v1" / "upstream" / "scripts" / "predict_unet_transformer.py"
    ).resolve()


def test_official_command_resolves_all_paths_for_upstream_cwd(tmp_path: Path) -> None:
    request = InferenceRequest(
        upstream_root=Path("artifacts") / "strong_baseline_v1" / "upstream",
        image_stem=Path("data") / "44b6_0113de3b",
        checkpoint=Path("artifacts") / "inputs" / "model.pth",
        output_dir=tmp_path / "output",
        expected_device="cpu",
    )

    command = build_official_command(request)

    assert Path(command[command.index("--data-dir") + 1]).is_absolute()
    assert Path(command[command.index("--debug-video") + 1]).is_absolute()
    assert Path(command[command.index("--weights") + 1]).is_absolute()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "not_official"),
        ("split", 1),
        ("threshold", 0.5),
        ("unet_batch_size", 4),
        ("ilp_edge_weight", -2.0),
        ("ilp_appearance_weight", 0.2),
        ("ilp_disappearance_weight", 0.2),
        ("ilp_division_weight", 2.0),
    ],
)
def test_inference_request_rejects_non_official_configuration(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="fixed official"):
        InferenceRequest(
            upstream_root=tmp_path / "upstream",
            image_stem=tmp_path / "44b6_0113de3b",
            checkpoint=tmp_path / "model.pth",
            output_dir=tmp_path / "output.geff",
            expected_device="cpu",
            **{field: value},
        )


def test_inference_cli_has_no_configuration_overrides() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_strong_baseline_v1.py"
    for command in ("infer-official", "smoke-official"):
        result = subprocess.run(
            [sys.executable, str(script), command, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--method" not in result.stdout
        assert "--split" not in result.stdout
        assert "--threshold" not in result.stdout
        assert "--unet-batch-size" not in result.stdout
        assert "--ilp-edge-weight" not in result.stdout
        assert "--ilp-appearance-weight" not in result.stdout
        assert "--ilp-disappearance-weight" not in result.stdout
        assert "--ilp-division-weight" not in result.stdout


def test_smoke_uses_separate_official_ilp_smoke_path(inference_request: InferenceRequest) -> None:
    smoke_target = runner._smoke_prediction_target(inference_request)
    full_target = runner._prediction_target(inference_request)

    assert smoke_target != full_target
    assert "official_ilp_smoke" in smoke_target.parts


def test_verify_is_read_only_when_prediction_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir = tmp_path / "results" / "prediction.geff"
    request = _prepared_request(tmp_path, output_dir=output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "existing").write_text("already produced")
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    digest = hashlib.sha256(request.checkpoint.read_bytes()).hexdigest()
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: digest)

    result = runner.verify_inference_inputs(
        request,
        expected_commit="expected",
        expected_checkpoint_sha256="expected",
    )

    assert result["image_shape"] == (2, 2, 2, 2)
    assert output_dir.is_dir()
    assert (output_dir / "existing").read_text() == "already produced"


def test_inference_preflight_failure_persists_failure_receipt(tmp_path: Path) -> None:
    request = InferenceRequest(
        upstream_root=tmp_path / "missing-upstream",
        image_stem=tmp_path / "44b6_0113de3b",
        checkpoint=tmp_path / "missing.pth",
        output_dir=tmp_path / "results" / "prediction.geff",
        expected_device="cpu",
    )

    with pytest.raises(ValueError, match="upstream commit"):
        runner.run_official_inference(request)

    run_dir = request.output_dir.parent
    payload = json.loads((run_dir / "run.json").read_text())
    assert payload["status"] == "failed"
    assert payload["return_code"] == -1
    assert (run_dir / "inference.log").is_file()


def test_inference_process_start_failure_persists_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _prepared_request(tmp_path)

    def fail_start(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("process could not start")

    monkeypatch.setattr(runner.subprocess, "run", fail_start)
    with pytest.raises(OSError, match="process could not start"):
        runner.run_official_inference(request)

    run_dir = request.output_dir.parent
    payload = json.loads((run_dir / "run.json").read_text())
    assert payload["status"] == "failed"
    assert payload["return_code"] == -1
    assert "process could not start" in payload["error"]
    assert (run_dir / "inference.log").is_file()


@pytest.mark.parametrize("entrypoint", ["official", "harmonic"])
def test_inference_provenance_failure_aborts_before_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    request = _prepared_request(tmp_path)
    calls: list[str] = []

    def reject_source(root: Path, expected: str) -> None:
        calls.append("source")
        raise ValueError("tracked source/index modifications")

    monkeypatch.setattr(runner, "verify_source", reject_source)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: calls.append("checkpoint"))
    monkeypatch.setattr(runner, "_validate_request_files", lambda *args, **kwargs: calls.append("image"))
    monkeypatch.setattr(runner, "_load_upstream_predictor", lambda root: calls.append("model"))
    monkeypatch.setattr(runner, "_safe_git_commit", lambda root: None)

    function = runner.run_official_inference if entrypoint == "official" else runner.run_harmonic_inference
    with pytest.raises(ValueError, match="tracked source/index modifications"):
        function(request)

    assert calls == ["source"]


@pytest.mark.parametrize("entrypoint", ["official", "harmonic"])
def test_inference_checkpoint_hash_mismatch_aborts_before_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    request = _prepared_request(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(runner, "verify_source", lambda root, expected: calls.append("source"))

    def reject_checkpoint(path: Path, expected: str) -> str:
        calls.append("checkpoint")
        raise ValueError("SHA-256 mismatch")

    monkeypatch.setattr(runner, "verify_sha256", reject_checkpoint)
    monkeypatch.setattr(runner, "_validate_request_files", lambda *args, **kwargs: calls.append("image"))
    monkeypatch.setattr(runner, "_load_upstream_predictor", lambda root: calls.append("model"))
    monkeypatch.setattr(runner, "_safe_git_commit", lambda root: None)

    function = runner.run_official_inference if entrypoint == "official" else runner.run_harmonic_inference
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        function(request)

    assert calls == ["source", "checkpoint"]


def test_official_cpu_run_hides_cuda_and_records_actual_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _prepared_request(tmp_path)
    predictor = request.upstream_root / "scripts" / "predict_unet_transformer.py"
    predictor.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path('cuda-visible.txt').write_text(os.environ.get('CUDA_VISIBLE_DEVICES', '<missing>'))\n"
        "target = Path('predictions/strong_baseline_v1/strong_baseline_v1_official_ilp/split_0/44b6_0113de3b.geff')\n"
        "target.mkdir(parents=True)\n"
        "(target / 'marker').write_text('graph')\n"
        "print('Fold 0: device=cpu')\n",
    )
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)
    monkeypatch.setattr(runner, "_validate_prediction_graph", lambda path: 1)
    monkeypatch.setattr(runner, "_prediction_directory_manifest", lambda path: {
        "prediction_path": str(path),
        "directory_sha256": "fake",
        "hash_algorithm": "fake",
        "files": 1,
        "total_bytes": 5,
        "nodes": 1,
        "edges": 0,
        "structural_reload": "fake",
    })

    receipt = runner.run_official_inference(request)

    assert (request.upstream_root / "cuda-visible.txt").read_text() == ""
    payload = json.loads(receipt.run_json_path.read_text())
    assert payload["expected_device"] == "cpu"
    assert payload["actual_device"] == "cpu"
    manifest = json.loads((request.output_dir.parent / "prediction_manifest.json").read_text())
    assert manifest["manifest_action"] == "created automatically after prediction GEFF structural reload"
    assert manifest["nodes"] == 1


def test_official_cpu_run_rejects_reported_cuda_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _prepared_request(tmp_path)
    predictor = request.upstream_root / "scripts" / "predict_unet_transformer.py"
    predictor.write_text("print('Fold 0: device=cuda')\n")
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)

    with pytest.raises(ValueError, match="actual device"):
        runner.run_official_inference(request)


def test_harmonic_cpu_model_path_hides_cuda_and_records_actual_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _prepared_request(tmp_path, output_dir=tmp_path / "harmonic-output")
    calls: dict[str, object] = {}

    class FakeModel:
        def predict_edges(self, *args: object) -> object:
            return args

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeGraph:
        def num_edges(self) -> int:
            return 0

    model = FakeModel()
    upstream = SimpleNamespace(
        PredictConfig=FakeConfig,
        load_model=lambda checkpoint, device: calls.setdefault(
            "load_env", __import__("os").environ.get("CUDA_VISIBLE_DEVICES")
        ) or (model, 2, (1, 4, 4)),
        predict_video=lambda *args, **kwargs: ([], []),
        build_graph=lambda coords, edges: FakeGraph(),
        save_graph=lambda graph, path: (path.mkdir(parents=True), (path / "marker").write_text("graph")),
    )
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)
    monkeypatch.setattr(runner, "_validate_request_files", lambda request, **kwargs: kwargs.get("target"))
    monkeypatch.setattr(runner, "_load_upstream_predictor", lambda root: upstream)
    monkeypatch.setattr(runner, "_prediction_graph_counts", lambda path: (1, 0))
    monkeypatch.setattr(runner, "_prediction_directory_manifest", lambda path: {
        "prediction_path": str(path),
        "directory_sha256": "fake",
        "hash_algorithm": "fake",
        "files": 1,
        "total_bytes": 5,
        "nodes": 1,
        "edges": 0,
        "structural_reload": "fake",
    })

    receipt = runner.run_harmonic_inference(request)

    assert calls["load_env"] == ""
    payload = json.loads(receipt.run_json_path.read_text())
    assert payload["expected_device"] == "cpu"
    assert payload["actual_device"] == "cpu"


def test_harmonic_cpu_run_rejects_model_on_cuda(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _prepared_request(tmp_path, output_dir=tmp_path / "harmonic-output")

    class FakeParameter:
        device = "cuda"

    class FakeModel:
        def parameters(self):
            return iter([FakeParameter()])

        def predict_edges(self, *args: object) -> object:
            return args

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    upstream = SimpleNamespace(
        PredictConfig=FakeConfig,
        load_model=lambda checkpoint, device: (FakeModel(), 2, (1, 4, 4)),
    )
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)
    monkeypatch.setattr(runner, "_validate_request_files", lambda request, **kwargs: kwargs.get("target"))
    monkeypatch.setattr(runner, "_load_upstream_predictor", lambda root: upstream)

    with pytest.raises(ValueError, match="actual device"):
        runner.run_harmonic_inference(request)


def test_inference_sets_user_and_upstream_pythonpath(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _prepared_request(tmp_path)
    env_receipt = request.upstream_root / "env.txt"
    predictor = request.upstream_root / "scripts" / "predict_unet_transformer.py"
    predictor.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path('env.txt').write_text(os.environ['USER'] + '\\n' + os.environ['PYTHONPATH'])\n"
        "target = Path('predictions/strong_baseline_v1/strong_baseline_v1_official_ilp/split_0/44b6_0113de3b.geff')\n"
        "target.mkdir(parents=True)\n"
        "(target / 'marker').write_text('graph')\n"
        "print('upstream stdout device=cpu')\n",
    )
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)
    monkeypatch.setattr(runner, "_validate_prediction_graph", lambda path: 1)
    monkeypatch.setattr(runner, "_prediction_directory_manifest", lambda path: {
        "prediction_path": str(path),
        "directory_sha256": "fake",
        "hash_algorithm": "fake",
        "files": 1,
        "total_bytes": 5,
        "nodes": 1,
        "edges": 0,
        "structural_reload": "fake",
    })

    receipt = runner.run_official_inference(request)

    env_lines = env_receipt.read_text().splitlines()
    assert env_lines[0] == "strong_baseline_v1"
    assert env_lines[1].split(":")[0] == str(request.upstream_root / "src")
    assert "--evaluate" not in receipt.command
    assert receipt.prediction_path.is_dir()


def test_empty_and_malformed_prediction_graphs_are_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.geff"
    empty.mkdir()
    with pytest.raises(ValueError, match="missing or empty"):
        runner._validate_prediction_graph(empty)

    malformed = tmp_path / "malformed.geff"
    malformed.mkdir()
    (malformed / "marker").write_text("not a GEFF")
    with pytest.raises(ValueError, match="unable to load prediction graph"):
        runner._validate_prediction_graph(malformed)


def test_smoke_calls_upstream_helpers_and_ilp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    request = _prepared_request(tmp_path)
    calls: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeGraph:
        def num_edges(self) -> int:
            return 1

    def predict_video(*args: object, **kwargs: object) -> tuple[list[tuple[int, int, int, int]], list[object]]:
        calls["predict"] = (args, kwargs)
        return [], []

    def build_graph(coords: object, edges: object) -> FakeGraph:
        calls["build"] = (coords, edges)
        return FakeGraph()

    def save_graph(graph: FakeGraph, path: Path) -> None:
        calls["save"] = path
        path.mkdir(parents=True)
        (path / "marker").write_text("graph")

    upstream = SimpleNamespace(
        PredictConfig=FakeConfig,
        load_model=lambda checkpoint, device: ("model", 2, (1, 4, 4)),
        predict_video=predict_video,
        build_graph=build_graph,
        save_graph=save_graph,
    )
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)
    monkeypatch.setattr(runner, "_load_upstream_predictor", lambda root: upstream)
    monkeypatch.setattr(runner, "_solve_ilp", lambda graph, request: calls.setdefault("ilp", (graph, request)) or graph)
    monkeypatch.setattr(runner, "_validate_prediction_graph", lambda path: 1)
    monkeypatch.setattr(runner, "_prediction_directory_manifest", lambda path: {
        "prediction_path": str(path),
        "directory_sha256": "fake",
        "hash_algorithm": "fake",
        "files": 1,
        "total_bytes": 5,
        "nodes": 1,
        "edges": 0,
        "structural_reload": "fake",
    })

    receipt = runner.run_official_smoke(request)

    predict_args, predict_kwargs = calls["predict"]
    assert predict_args[0] == "model"
    assert predict_args[1] == request.image_stem
    assert predict_kwargs == {
        "window_size": 2,
        "max_frames": 2,
        "unet_batch_size": 1,
        "downsample": (1, 4, 4),
    }
    assert "ilp" in calls
    assert "official_ilp_smoke" in receipt.prediction_path.parts


def test_harmonic_allows_post_ilp_count_change_from_raw_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = InferenceRequest(
        upstream_root=tmp_path / "upstream",
        image_stem=tmp_path / "44b6_0113de3b",
        checkpoint=tmp_path / "model.pth",
        output_dir=tmp_path / "harmonic_ilp",
        expected_device="cpu",
    )
    calls: dict[str, object] = {}

    class FakeModel:
        def predict_edges(self, *args: object) -> object:
            return args

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeGraph:
        def __init__(self, node_count: int) -> None:
            self.node_count = node_count

    model = FakeModel()

    def predict_video(*args: object, **kwargs: object) -> tuple[list[tuple[int, int, int, int]], list[object]]:
        calls["predict"] = (args, kwargs)
        return [(0, 0, 0, 0), (1, 0, 0, 0), (2, 0, 0, 0)], []

    def build_graph(coords: list[tuple[int, int, int, int]], edges: list[object]) -> FakeGraph:
        assert len(coords) == 3
        calls["raw_graph"] = (coords, edges)
        return FakeGraph(node_count=3)

    def save_graph(graph: FakeGraph, path: Path) -> None:
        calls["saved_graph"] = graph.node_count
        path.mkdir(parents=True)
        (path / "marker").write_text("persisted")

    upstream = SimpleNamespace(
        PredictConfig=FakeConfig,
        load_model=lambda checkpoint, device: (model, 2, (1, 4, 4)),
        predict_video=predict_video,
        build_graph=build_graph,
        save_graph=save_graph,
    )
    monkeypatch.setattr(runner, "verify_source", lambda root, expected: None)
    monkeypatch.setattr(runner, "verify_sha256", lambda path, expected: expected)
    monkeypatch.setattr(runner, "_validate_request_files", lambda request, **kwargs: request.output_dir)
    monkeypatch.setattr(runner, "_load_upstream_predictor", lambda root: upstream)
    monkeypatch.setattr(runner, "_solve_ilp", lambda graph, request: FakeGraph(node_count=2))
    monkeypatch.setattr(runner, "_prediction_graph_counts", lambda path: (2, 1))
    monkeypatch.setattr(
        runner,
        "_prediction_directory_manifest",
        lambda path: {
            "prediction_path": str(path),
            "directory_sha256": "fake",
            "hash_algorithm": "fake",
            "files": 1,
            "total_bytes": 9,
            "nodes": 2,
            "edges": 1,
            "structural_reload": "fake reload succeeded",
        },
    )

    receipt = runner.run_harmonic_inference(request)

    payload = json.loads(receipt.run_json_path.read_text())
    assert receipt.success
    assert calls["raw_graph"]
    assert calls["saved_graph"] == 2
    assert payload["raw_detection_node_count"] == 3
    assert payload["prediction_node_count"] == 2
    assert payload["prediction_edge_count"] == 1
    assert receipt.prediction_path.is_dir()
    assert (request.output_dir / "prediction_manifest.json").is_file()


def test_evaluate_cli_requires_ground_truth() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_strong_baseline_v1.py"
    result = subprocess.run(
        [sys.executable, str(script), "evaluate", "--prediction", "prediction.geff"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--ground-truth" in result.stderr
