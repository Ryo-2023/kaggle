"""Image-only orchestration for the pinned upstream strong baseline.

The upstream repository remains the source of truth for model construction,
prediction, graph construction, and ILP solving.  This module only validates
the phase boundary, invokes those helpers, and records a reproducible receipt.
Ground-truth paths deliberately do not appear in :class:`InferenceRequest` or
in either inference command.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from biohub.strong_baseline.manifest import (
    prediction_directory_manifest as _shared_prediction_directory_manifest,
)
from biohub.strong_baseline.manifest import write_prediction_manifest as _shared_write_prediction_manifest
from biohub.strong_baseline.provenance import (
    LOCAL_CHECKPOINT_SHA256,
    OFFICIAL_COMMIT,
    verify_sha256,
    verify_source,
)

OFFICIAL_METHOD = "strong_baseline_v1_official_ilp"
DEFAULT_SPLIT = 0
DEFAULT_THRESHOLD = 0.99
DEFAULT_UNET_BATCH_SIZE = 1
DEFAULT_ILP_EDGE_WEIGHT = -1.0
DEFAULT_ILP_APPEARANCE_WEIGHT = 0.1
DEFAULT_ILP_DISAPPEARANCE_WEIGHT = 0.1
DEFAULT_ILP_DIVISION_WEIGHT = 1.0
DEFAULT_MAX_FRAMES = 2
DEFAULT_HARMONIC_REVERSE_WEIGHT = 0.20
MAX_PUBLISHED_HARMONIC_REVERSE_WEIGHT = 0.35
KAGGLE_ARTIFACT_VERSION = 1
ORGANIZER_NOTEBOOK_VERSION = 331429261
_DEVICE_RE = re.compile(r"\bdevice\s*=\s*([A-Za-z0-9_.:-]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """Inputs that are allowed during inference.

    ``image_stem`` is the upstream dataset stem, without ``.zarr``.  A caller
    may provide a ``.zarr`` path for convenience; it is canonicalized to the
    stem before any command is constructed.  A GEFF path is always rejected.
    """

    upstream_root: Path
    image_stem: Path
    checkpoint: Path
    output_dir: Path
    threshold: float = DEFAULT_THRESHOLD
    expected_device: str = "cpu"
    ilp_edge_weight: float = DEFAULT_ILP_EDGE_WEIGHT
    ilp_appearance_weight: float = DEFAULT_ILP_APPEARANCE_WEIGHT
    ilp_disappearance_weight: float = DEFAULT_ILP_DISAPPEARANCE_WEIGHT
    ilp_division_weight: float = DEFAULT_ILP_DIVISION_WEIGHT
    method: str = OFFICIAL_METHOD
    split: int = DEFAULT_SPLIT
    unet_batch_size: int = DEFAULT_UNET_BATCH_SIZE

    def __post_init__(self) -> None:
        for name in ("upstream_root", "image_stem", "checkpoint", "output_dir"):
            object.__setattr__(self, name, Path(getattr(self, name)))

        image_stem = self.image_stem
        if image_stem.suffix.lower() == ".geff":
            raise ValueError("image stem must identify an image, not a .geff ground-truth graph")
        if image_stem.suffix.lower() == ".zarr":
            image_stem = image_stem.with_suffix("")
        if not image_stem.name:
            raise ValueError("image stem must not be empty")
        object.__setattr__(self, "image_stem", image_stem)

        if self.checkpoint.suffix.lower() == ".geff":
            raise ValueError("checkpoint must not be a .geff ground-truth graph")
        fixed_values = {
            "method": OFFICIAL_METHOD,
            "split": DEFAULT_SPLIT,
            "threshold": DEFAULT_THRESHOLD,
            "unet_batch_size": DEFAULT_UNET_BATCH_SIZE,
            "ilp_edge_weight": DEFAULT_ILP_EDGE_WEIGHT,
            "ilp_appearance_weight": DEFAULT_ILP_APPEARANCE_WEIGHT,
            "ilp_disappearance_weight": DEFAULT_ILP_DISAPPEARANCE_WEIGHT,
            "ilp_division_weight": DEFAULT_ILP_DIVISION_WEIGHT,
        }
        for name, expected in fixed_values.items():
            if getattr(self, name) != expected:
                raise ValueError(f"{name} is fixed official configuration value {expected!r}")
        if not math.isfinite(self.threshold) or not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be a finite probability in [0, 1]")


@dataclass(frozen=True, slots=True)
class RunReceipt:
    """Persisted execution details returned by an inference run."""

    prediction_path: Path
    run_json_path: Path
    command: tuple[str, ...]
    return_code: int
    started_at: str
    finished_at: str
    elapsed_seconds: float
    device: str
    actual_device: str | None = None
    expected_device: str | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def success(self) -> bool:
        return self.return_code == 0

    @property
    def returncode(self) -> int:
        """Compatibility alias matching :class:`subprocess.CompletedProcess`."""

        return self.return_code

    @property
    def prediction(self) -> Path:
        """Compatibility alias for the generated GEFF directory."""

        return self.prediction_path


def build_official_command(request: InferenceRequest) -> list[str]:
    """Build the fixed official CLI invocation.

    This function is intentionally pure and does not check the filesystem.  A
    command snapshot can therefore be tested without opening an image or GT.
    """

    return [
        sys.executable,
        str((request.upstream_root / "scripts" / "predict_unet_transformer.py").resolve()),
        "--data-dir",
        str(request.image_stem.parent.resolve()),
        "--debug-video",
        str(request.image_stem.resolve()),
        "--weights",
        str(request.checkpoint.resolve()),
        "--method",
        OFFICIAL_METHOD,
        "--split",
        str(DEFAULT_SPLIT),
        "--det-threshold",
        str(DEFAULT_THRESHOLD),
        "--unet-batch-size",
        str(DEFAULT_UNET_BATCH_SIZE),
        "--use-ilp",
        "--ilp-edge-weight",
        str(DEFAULT_ILP_EDGE_WEIGHT),
        "--ilp-appearance-weight",
        str(DEFAULT_ILP_APPEARANCE_WEIGHT),
        "--ilp-disappearance-weight",
        str(DEFAULT_ILP_DISAPPEARANCE_WEIGHT),
        "--ilp-division-weight",
        str(DEFAULT_ILP_DIVISION_WEIGHT),
    ]


def build_harmonic_command(
    request: InferenceRequest,
    *,
    reverse_weight: float = DEFAULT_HARMONIC_REVERSE_WEIGHT,
    command_name: str = "infer-harmonic",
    max_frames: int | None = None,
) -> list[str]:
    """Build the image-only CLI receipt for the harmonic association path."""

    weight = float(reverse_weight)
    if not math.isfinite(weight) or not 0.0 < weight <= MAX_PUBLISHED_HARMONIC_REVERSE_WEIGHT:
        raise ValueError("reverse_weight must be finite and in (0, 0.35]")
    project_root = Path(__file__).resolve().parents[3]
    command = [
        sys.executable,
        str(project_root / "scripts" / "run_strong_baseline_v1.py"),
        command_name,
        "--upstream-root",
        str(request.upstream_root.resolve()),
        "--image-stem",
        str(request.image_stem.resolve()),
        "--checkpoint",
        str(request.checkpoint.resolve()),
        "--output-dir",
        str(request.output_dir.resolve()),
        "--expected-device",
        request.expected_device,
    ]
    if max_frames is not None:
        command.extend(["--max-frames", str(max_frames)])
    return command


def _prediction_target(request: InferenceRequest) -> Path:
    """Resolve a GEFF destination from an output root or explicit GEFF path."""

    if request.output_dir.name.lower().endswith(".geff"):
        return request.output_dir
    return request.output_dir / f"{request.image_stem.name}.geff"


def _run_directory(request: InferenceRequest) -> Path:
    return _prediction_target(request).parent


def _smoke_prediction_target(request: InferenceRequest) -> Path:
    """Place smoke output below a distinct ``official_ilp_smoke`` directory."""

    full_target = _prediction_target(request)
    return full_target.parent / "official_ilp_smoke" / full_target.name


def _source_prediction_path(request: InferenceRequest) -> Path:
    return (
        request.upstream_root
        / "predictions"
        / "strong_baseline_v1"
        / OFFICIAL_METHOD
        / f"split_{DEFAULT_SPLIT}"
        / f"{request.image_stem.name}.geff"
    )


def _config(request: InferenceRequest) -> dict[str, Any]:
    return {
        "method": OFFICIAL_METHOD,
        "split": DEFAULT_SPLIT,
        "det_threshold": DEFAULT_THRESHOLD,
        "unet_batch_size": DEFAULT_UNET_BATCH_SIZE,
        "use_ilp": True,
        "ilp_edge_weight": DEFAULT_ILP_EDGE_WEIGHT,
        "ilp_appearance_weight": DEFAULT_ILP_APPEARANCE_WEIGHT,
        "ilp_disappearance_weight": DEFAULT_ILP_DISAPPEARANCE_WEIGHT,
        "ilp_division_weight": DEFAULT_ILP_DIVISION_WEIGHT,
    }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _verify_fixed_provenance(request: InferenceRequest) -> None:
    """Verify source and checkpoint before any inference input is touched."""

    verify_source(request.upstream_root, OFFICIAL_COMMIT)
    verify_sha256(request.checkpoint, LOCAL_CHECKPOINT_SHA256)


@contextlib.contextmanager
def _cpu_visibility_guard(expected_device: str):
    """Hide CUDA from in-process inference when CPU is the requested device."""

    if expected_device.lower() != "cpu":
        yield
        return
    sentinel = object()
    previous = os.environ.get("CUDA_VISIBLE_DEVICES", sentinel)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        yield
    finally:
        if previous is sentinel:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        else:
            os.environ["CUDA_VISIBLE_DEVICES"] = str(previous)


def _devices_match(expected: str, actual: str) -> bool:
    expected = expected.strip().lower()
    actual = actual.strip().lower()
    if expected == "cpu":
        return actual == "cpu"
    if expected == "cuda":
        return actual.startswith("cuda")
    return actual == expected


def _require_actual_device(expected: str, actual: str) -> str:
    actual = str(actual).strip()
    if not actual or not _devices_match(expected, actual):
        raise ValueError(f"actual device {actual!r} differs from expected device {expected!r}")
    return actual


def _reported_subprocess_device(stdout: str, expected: str) -> str:
    matches = _DEVICE_RE.findall(stdout)
    if matches:
        return _require_actual_device(expected, matches[-1])
    # The fixed upstream CLI prints this value.  A small test double may not;
    # for CPU the visibility guard itself is an enforceable observation.
    if expected.lower() == "cpu":
        return _require_actual_device(expected, "cpu")
    raise ValueError("actual device was not reported by the inference subprocess")


def _model_actual_device(model: Any, requested: Any) -> str:
    actual = str(requested)
    parameters = getattr(model, "parameters", None)
    if callable(parameters):
        try:
            parameter = next(parameters())
        except (StopIteration, TypeError, AttributeError):
            pass
        else:
            actual = str(getattr(parameter, "device", requested))
    return actual


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_sha256_file(path: Path) -> str | None:
    try:
        return _sha256_file(path)
    except (OSError, ValueError):
        return None


def _prediction_graph_counts(path: Path) -> tuple[int, int]:
    tracksdata = importlib.import_module("tracksdata")
    loaded = tracksdata.graph.IndexedRXGraph.from_geff(path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    return int(graph.num_nodes()), int(graph.num_edges())


def _prediction_directory_manifest(path: Path) -> dict[str, Any]:
    return _shared_prediction_directory_manifest(path)


def _write_prediction_manifest(path: Path, payload: dict[str, Any]) -> Path:
    return _shared_write_prediction_manifest(path, payload)


def _git_commit(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _safe_git_commit(root: Path) -> str | None:
    try:
        return _git_commit(root)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _torch_cuda_available() -> bool | None:
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return None
    return bool(torch.cuda.is_available())


def _torch_cuda_available_for_request(request: InferenceRequest) -> bool | None:
    """Report availability under the same visibility policy as inference."""

    if request.expected_device.lower() == "cpu":
        return False
    return _torch_cuda_available()


def _validate_request_files(
    request: InferenceRequest,
    *,
    target: Path | None = None,
    check_destination: bool = True,
) -> Path:
    if not request.upstream_root.is_dir():
        raise FileNotFoundError(f"upstream checkout not found: {request.upstream_root}")
    predictor = request.upstream_root / "scripts" / "predict_unet_transformer.py"
    if not predictor.is_file():
        raise FileNotFoundError(f"upstream predictor not found: {predictor}")
    image_path = request.image_stem.with_suffix(".zarr")
    if not image_path.exists():
        raise FileNotFoundError(f"image Zarr not found: {image_path}")
    if not request.checkpoint.is_file():
        raise FileNotFoundError(f"checkpoint not found: {request.checkpoint}")
    target = target or _prediction_target(request)
    if check_destination and target.exists():
        raise FileExistsError(f"prediction destination already exists: {target}")
    return target


def _prediction_is_nonempty(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_dir():
        return any(path.iterdir())
    return path.stat().st_size > 0


def _validate_prediction_graph(path: Path) -> int:
    if not _prediction_is_nonempty(path):
        raise ValueError(f"prediction graph is missing or empty: {path}")
    try:
        tracksdata = importlib.import_module("tracksdata")
        loaded = tracksdata.graph.IndexedRXGraph.from_geff(path)
    except (ImportError, OSError, TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"unable to load prediction graph: {path}") from exc
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    node_count = int(graph.num_nodes())
    if node_count <= 0:
        raise ValueError(f"prediction graph is empty: {path}")
    return node_count


def _write_run_files(run_dir: Path, payload: dict[str, Any], stdout: str, stderr: str) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    run_json_path = run_dir / "run.json"
    run_json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (run_dir / "inference.log").write_text(stdout + stderr)
    return run_json_path


def _receipt(
    *,
    request: InferenceRequest,
    target: Path,
    run_json_path: Path,
    command: Sequence[str],
    return_code: int,
    started_at: str,
    finished_at: str,
    elapsed_seconds: float,
    actual_device: str | None,
    stdout: str,
    stderr: str,
) -> RunReceipt:
    return RunReceipt(
        prediction_path=target,
        run_json_path=run_json_path,
        command=tuple(command),
        return_code=return_code,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=elapsed_seconds,
        device=actual_device or request.expected_device,
        actual_device=actual_device,
        expected_device=request.expected_device,
        stdout=stdout,
        stderr=stderr,
    )


def run_official_inference(request: InferenceRequest) -> RunReceipt:
    """Invoke the fixed upstream CLI using image/checkpoint inputs only."""

    target = _prediction_target(request)
    run_dir = _run_directory(request)
    command: list[str] = []
    source_prediction = _source_prediction_path(request)
    started_at = _timestamp()
    started_mono = time.monotonic()
    stdout = ""
    stderr = ""
    return_code = -1
    status = "failed"
    error_text = ""
    prediction_node_count: int | None = None
    actual_device: str | None = None
    manifest_path: Path | None = None
    copied = False
    try:
        _verify_fixed_provenance(request)
        command = build_official_command(request)
        _validate_request_files(request, target=target)
        if source_prediction.exists():
            if source_prediction.is_dir():
                shutil.rmtree(source_prediction)
            else:
                source_prediction.unlink()

        env = os.environ.copy()
        env["USER"] = "strong_baseline_v1"
        if request.expected_device.lower() == "cpu":
            env["CUDA_VISIBLE_DEVICES"] = ""
        src_path = str((request.upstream_root / "src").resolve())
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = src_path + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
        result = subprocess.run(
            command,
            cwd=request.upstream_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        return_code = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if return_code != 0:
            raise RuntimeError(f"official inference failed with return code {return_code}")
        actual_device = _reported_subprocess_device(stdout, request.expected_device)
        if not _prediction_is_nonempty(source_prediction):
            raise ValueError(f"upstream predictor did not create a non-empty graph: {source_prediction}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_prediction, target)
        copied = True
        prediction_node_count = _validate_prediction_graph(target)
        manifest = _prediction_directory_manifest(target)
        manifest["method"] = OFFICIAL_METHOD
        manifest_path = _write_prediction_manifest(target, manifest)
        status = "success"
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        stderr = f"{stderr}{error_text}\n"
        if copied and target.is_dir():
            shutil.rmtree(target)
        raise

    finally:
        finished_at = _timestamp()
        elapsed_seconds = time.monotonic() - started_mono
        payload: dict[str, Any] = {
            "status": status,
            "command": command,
            "source_commit": _safe_git_commit(request.upstream_root),
            "checkpoint_sha256": _safe_sha256_file(request.checkpoint),
            "image_stem": str(request.image_stem),
            "prediction_path": str(target),
            "config": _config(request),
            "expected_device": request.expected_device,
            "actual_device": actual_device,
            "device": actual_device or request.expected_device,
            "torch_cuda_available": _torch_cuda_available_for_request(request),
            "kaggle_artifact_version": KAGGLE_ARTIFACT_VERSION,
            "organizer_notebook_version": ORGANIZER_NOTEBOOK_VERSION,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
        }
        if error_text:
            payload["error"] = error_text
        if prediction_node_count is not None:
            payload["prediction_node_count"] = prediction_node_count
        if manifest_path is not None:
            payload["prediction_manifest"] = str(manifest_path)
        run_json_path = _write_run_files(run_dir, payload, stdout, stderr)

    return _receipt(
        request=request,
        target=target,
        run_json_path=run_json_path,
        command=command,
        return_code=return_code,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=elapsed_seconds,
        actual_device=actual_device,
        stdout=stdout,
        stderr=stderr,
    )


def _harmonic_prediction_target(request: InferenceRequest, *, smoke: bool) -> Path:
    target = _prediction_target(request)
    if not smoke:
        return target
    return target.parent / "harmonic_ilp_smoke" / target.name


def run_harmonic_inference(
    request: InferenceRequest,
    *,
    reverse_weight: float = DEFAULT_HARMONIC_REVERSE_WEIGHT,
    max_frames: int | None = None,
) -> RunReceipt:
    """Run fixed upstream inference with only the published harmonic wrapper."""

    weight = float(reverse_weight)
    if not math.isfinite(weight) or not 0.0 < weight <= MAX_PUBLISHED_HARMONIC_REVERSE_WEIGHT:
        raise ValueError("reverse_weight must be finite and in (0, 0.35]")
    smoke = max_frames is not None
    target = _harmonic_prediction_target(request, smoke=smoke)
    run_dir = target.parent
    command = build_harmonic_command(
        request,
        reverse_weight=weight,
        command_name="smoke-harmonic" if smoke else "infer-harmonic",
        max_frames=max_frames,
    )
    started_at = _timestamp()
    started_mono = time.monotonic()
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    status = "failed"
    error_text = ""
    raw_detection_node_count: int | None = None
    prediction_node_count: int | None = None
    prediction_edge_count: int | None = None
    actual_device: str | None = None
    manifest_path: Path | None = None
    original_user = os.environ.get("USER")
    target_created = False

    try:
        _verify_fixed_provenance(request)
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be positive")
        _validate_request_files(request, target=target)
        os.environ["USER"] = "strong_baseline_v1"
        with _cpu_visibility_guard(request.expected_device):
            torch = importlib.import_module("torch")
            device = torch.device(request.expected_device)
            if device.type == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
            from biohub.strong_baseline.harmonic import harmonic_predict_edges

            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                upstream = _load_upstream_predictor(request.upstream_root)
                model, window_size, downsample = upstream.load_model(request.checkpoint, device)
                actual_device = _require_actual_device(
                    request.expected_device,
                    _model_actual_device(model, device),
                )
                config = upstream.PredictConfig(
                    det_threshold=DEFAULT_THRESHOLD,
                    use_ilp=True,
                    ilp_edge_weight=DEFAULT_ILP_EDGE_WEIGHT,
                    ilp_appearance_weight=DEFAULT_ILP_APPEARANCE_WEIGHT,
                    ilp_disappearance_weight=DEFAULT_ILP_DISAPPEARANCE_WEIGHT,
                    ilp_division_weight=DEFAULT_ILP_DIVISION_WEIGHT,
                )
                original_predict_edges = model.predict_edges
                model.predict_edges = harmonic_predict_edges(  # type: ignore[method-assign]
                    original_predict_edges,
                    reverse_weight=weight,
                )
                try:
                    coords, edges = upstream.predict_video(
                        model,
                        request.image_stem,
                        device,
                        config,
                        window_size=window_size,
                        max_frames=max_frames,
                        unet_batch_size=DEFAULT_UNET_BATCH_SIZE,
                        downsample=downsample,
                    )
                finally:
                    model.predict_edges = original_predict_edges  # type: ignore[method-assign]

                raw_detection_node_count = len(coords)
                graph = upstream.build_graph(coords, edges)
                graph = _solve_ilp(graph, request)
                target.parent.mkdir(parents=True, exist_ok=True)
                upstream.save_graph(graph, target)
                target_created = True
                prediction_node_count, prediction_edge_count = _prediction_graph_counts(target)
                manifest = _prediction_directory_manifest(target)
                manifest["method"] = "strong_baseline_v1_harmonic_ilp"
                manifest["harmonic_reverse_weight"] = weight
                manifest["raw_detection_node_count"] = raw_detection_node_count
                manifest_path = _write_prediction_manifest(target, manifest)
        status = "success"
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        stderr_capture.write(error_text + "\n")
        if target_created and target.is_dir():
            shutil.rmtree(target)
        raise
    finally:
        if original_user is None:
            os.environ.pop("USER", None)
        else:
            os.environ["USER"] = original_user
        finished_at = _timestamp()
        elapsed_seconds = time.monotonic() - started_mono
        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()
        payload: dict[str, Any] = {
            "status": status,
            "mode": "harmonic-ilp-smoke" if smoke else "harmonic-ilp",
            "command": command,
            "source_commit": _safe_git_commit(request.upstream_root),
            "checkpoint_sha256": _safe_sha256_file(request.checkpoint),
            "image_stem": str(request.image_stem),
            "prediction_path": str(target),
            "config": {
                **_config(request),
                "harmonic_reverse_weight": weight,
                **({"max_frames": max_frames} if max_frames is not None else {}),
            },
            "expected_device": request.expected_device,
            "actual_device": actual_device,
            "device": actual_device or request.expected_device,
            "torch_cuda_available": _torch_cuda_available_for_request(request),
            "kaggle_artifact_version": KAGGLE_ARTIFACT_VERSION,
            "organizer_notebook_version": ORGANIZER_NOTEBOOK_VERSION,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "return_code": 0 if status == "success" else -1,
            "stdout": stdout,
            "stderr": stderr,
        }
        if error_text:
            payload["error"] = error_text
        if raw_detection_node_count is not None:
            payload["raw_detection_node_count"] = raw_detection_node_count
        if prediction_node_count is not None:
            payload["prediction_node_count"] = prediction_node_count
        if prediction_edge_count is not None:
            payload["prediction_edge_count"] = prediction_edge_count
        if manifest_path is not None:
            payload["prediction_manifest"] = str(manifest_path)
        run_json_path = _write_run_files(run_dir, payload, stdout, stderr)

    return _receipt(
        request=request,
        target=target,
        run_json_path=run_json_path,
        command=command,
        return_code=0,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=float(payload["elapsed_seconds"]),
        actual_device=actual_device,
        stdout=stdout,
        stderr=stderr,
    )


def run_harmonic_smoke(
    request: InferenceRequest,
    *,
    reverse_weight: float = DEFAULT_HARMONIC_REVERSE_WEIGHT,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> RunReceipt:
    """Run a bounded harmonic image-only smoke through the same helper path."""

    return run_harmonic_inference(
        request,
        reverse_weight=reverse_weight,
        max_frames=max_frames,
    )


def _load_upstream_predictor(upstream_root: Path) -> ModuleType:
    predictor_path = upstream_root / "scripts" / "predict_unet_transformer.py"
    scripts_path = str(predictor_path.parent)
    src_path = str(upstream_root / "src")
    old_path = list(sys.path)
    sys.path[:0] = [scripts_path, src_path]
    module_name = f"_biohub_upstream_predictor_{time.monotonic_ns()}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, predictor_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"unable to import upstream predictor: {predictor_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old_path


def _solve_ilp(graph: Any, request: InferenceRequest) -> Any:
    if graph.num_edges() <= 0:
        return graph
    tracksdata = importlib.import_module("tracksdata")
    solver = tracksdata.solvers.ILPSolver(
        edge_weight=DEFAULT_ILP_EDGE_WEIGHT * tracksdata.EdgeAttr("edge_prob"),
        appearance_weight=DEFAULT_ILP_APPEARANCE_WEIGHT,
        disappearance_weight=DEFAULT_ILP_DISAPPEARANCE_WEIGHT,
        division_weight=DEFAULT_ILP_DIVISION_WEIGHT,
    )
    return solver.solve(graph)


def run_official_smoke(request: InferenceRequest, max_frames: int = DEFAULT_MAX_FRAMES) -> RunReceipt:
    """Run a bounded image-only prediction through upstream Python helpers."""

    target = _smoke_prediction_target(request)
    run_dir = target.parent
    command: list[str] = []
    started_at = _timestamp()
    started_mono = time.monotonic()
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    status = "failed"
    error_text = ""
    prediction_node_count: int | None = None
    actual_device: str | None = None
    manifest_path: Path | None = None
    original_user = os.environ.get("USER")

    try:
        _verify_fixed_provenance(request)
        if max_frames < 1:
            raise ValueError("max_frames must be positive")
        command = build_official_command(request)
        _validate_request_files(request, target=target)
        os.environ["USER"] = "strong_baseline_v1"
        with _cpu_visibility_guard(request.expected_device):
            torch = importlib.import_module("torch")
            device = torch.device(request.expected_device)
            if device.type == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                upstream = _load_upstream_predictor(request.upstream_root)
                model, window_size, downsample = upstream.load_model(request.checkpoint, device)
                actual_device = _require_actual_device(
                    request.expected_device,
                    _model_actual_device(model, device),
                )
                config = upstream.PredictConfig(
                    det_threshold=DEFAULT_THRESHOLD,
                    use_ilp=True,
                    ilp_edge_weight=DEFAULT_ILP_EDGE_WEIGHT,
                    ilp_appearance_weight=DEFAULT_ILP_APPEARANCE_WEIGHT,
                    ilp_disappearance_weight=DEFAULT_ILP_DISAPPEARANCE_WEIGHT,
                    ilp_division_weight=DEFAULT_ILP_DIVISION_WEIGHT,
                )
                coords, edges = upstream.predict_video(
                    model,
                    request.image_stem,
                    device,
                    config,
                    window_size=window_size,
                    max_frames=max_frames,
                    unet_batch_size=DEFAULT_UNET_BATCH_SIZE,
                    downsample=downsample,
                )
                graph = upstream.build_graph(coords, edges)
                graph = _solve_ilp(graph, request)
                upstream.save_graph(graph, target)
                prediction_node_count = _validate_prediction_graph(target)
                manifest_path = _write_prediction_manifest(
                    target,
                    {**_prediction_directory_manifest(target), "method": OFFICIAL_METHOD},
                )
        status = "success"
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        stderr_capture.write(error_text + "\n")
        raise
    finally:
        if original_user is None:
            os.environ.pop("USER", None)
        else:
            os.environ["USER"] = original_user
        finished_at = _timestamp()
        elapsed_seconds = time.monotonic() - started_mono
        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()
        payload = {
            "status": status,
            "command": command,
            "mode": "smoke-official",
            "source_commit": _safe_git_commit(request.upstream_root),
            "checkpoint_sha256": _safe_sha256_file(request.checkpoint),
            "image_stem": str(request.image_stem),
            "prediction_path": str(target),
            "config": {**_config(request), "max_frames": max_frames},
            "expected_device": request.expected_device,
            "actual_device": actual_device,
            "device": actual_device or request.expected_device,
            "torch_cuda_available": _torch_cuda_available_for_request(request),
            "kaggle_artifact_version": KAGGLE_ARTIFACT_VERSION,
            "organizer_notebook_version": ORGANIZER_NOTEBOOK_VERSION,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "return_code": 0 if status == "success" else -1,
            "stdout": stdout,
            "stderr": stderr,
        }
        if error_text:
            payload["error"] = error_text
        if prediction_node_count is not None:
            payload["prediction_node_count"] = prediction_node_count
        if manifest_path is not None:
            payload["prediction_manifest"] = str(manifest_path)
        run_json_path = _write_run_files(run_dir, payload, stdout, stderr)

    return _receipt(
        request=request,
        target=target,
        run_json_path=run_json_path,
        command=command,
        return_code=0,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_seconds=float(payload["elapsed_seconds"]),
        actual_device=actual_device,
        stdout=stdout,
        stderr=stderr,
    )


def verify_inference_inputs(
    request: InferenceRequest,
    *,
    expected_commit: str = OFFICIAL_COMMIT,
    expected_checkpoint_sha256: str = LOCAL_CHECKPOINT_SHA256,
) -> dict[str, Any]:
    """Verify source, checkpoint, image metadata, and ILP dependencies."""

    _validate_request_files(request, check_destination=False)
    verify_source(request.upstream_root, expected_commit)
    checkpoint_sha256 = verify_sha256(request.checkpoint, expected_checkpoint_sha256)
    image_path = request.image_stem.with_suffix(".zarr")
    zarr = importlib.import_module("zarr")
    root = zarr.open_group(image_path, mode="r")
    if "0" not in root:
        raise ValueError(f"image Zarr is missing array '0': {image_path}")
    image_shape = tuple(int(value) for value in root["0"].shape)
    if len(image_shape) != 4:
        raise ValueError(f"image must have shape (T, Z, Y, X), got {image_shape}")
    attrs = dict(root.attrs)
    quantiles = attrs.get("image_statistics", {}).get("quantiles", {})
    if not {"0.001", "0.999"}.issubset(quantiles):
        raise ValueError(f"image metadata is missing 0.001/0.999 quantiles: {image_path}")
    imported = {}
    for module_name in ("tracksdata", "ilpy", "pyscipopt"):
        imported[module_name] = importlib.import_module(module_name).__name__
    return {
        "source_commit": expected_commit,
        "checkpoint_sha256": checkpoint_sha256,
        "kaggle_artifact_version": KAGGLE_ARTIFACT_VERSION,
        "organizer_notebook_version": ORGANIZER_NOTEBOOK_VERSION,
        "image_stem": str(request.image_stem),
        "image_shape": image_shape,
        "quantiles": {key: quantiles[key] for key in ("0.001", "0.999")},
        "ilp_dependencies": imported,
        "torch_cuda_available": _torch_cuda_available_for_request(request),
    }


__all__ = [
    "DEFAULT_HARMONIC_REVERSE_WEIGHT",
    "DEFAULT_MAX_FRAMES",
    "KAGGLE_ARTIFACT_VERSION",
    "ORGANIZER_NOTEBOOK_VERSION",
    "InferenceRequest",
    "RunReceipt",
    "build_harmonic_command",
    "build_official_command",
    "run_harmonic_inference",
    "run_harmonic_smoke",
    "run_official_inference",
    "run_official_smoke",
    "verify_inference_inputs",
]
