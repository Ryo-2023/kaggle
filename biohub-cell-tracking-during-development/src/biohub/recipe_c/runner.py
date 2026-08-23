"""GT-free, failure-atomic Recipe C inference orchestration."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import biohub.device as device_module
from biohub.recipe_c.diagnostics import (
    DiagnosticRecorder,
    _capture_source_provenance,
    _json_safe,
    _sha256_file,
    _verify_source_provenance,
)
from biohub.recipe_c.geff_bridge import (
    fsync_directory,
    postprocessed_csv_to_geffs,
    publish_directory_noreplace,
    publish_file_noreplace,
    validate_prediction_geff,
    write_json_exclusive,
    write_prediction_manifest,
)
from biohub.recipe_c.protocol import (
    PANEL_V1,
    validate_selection_lock,
    validate_selection_lock_payload,
)
from biohub.recipe_c.source import RECIPE_C_SOURCE
from biohub.reproducibility.digest import directory_digest_report
from biohub.reproducibility.gt_guard import mint_prediction_token

_REPO_FILES = (
    "scripts/augmentations.py",
    "scripts/dataspec.py",
    "scripts/evaluate.py",
    "scripts/predict_unet_transformer.py",
    "scripts/train_unet_transformer.py",
    "src/biohub_tracking/__init__.py",
    "src/biohub_tracking/division_metrics.py",
    "src/biohub_tracking/img_proc.py",
    "src/biohub_tracking/io.py",
    "src/biohub_tracking/metrics.py",
    "src/biohub_tracking/models/__init__.py",
    "src/biohub_tracking/models/simple_node_transformer.py",
    "src/biohub_tracking/models/temporal_unet.py",
)
_PREDICTOR_DERIVED = PurePosixPath("scripts/predict_unet_transformer_recipe_c_runtime.py")
_SPLITS_DERIVED = PurePosixPath("clean_v106_test_splits_recipe_c_runtime.json")
_TRACE_DERIVED = PurePosixPath("src/biohub_pipeline/postprocess_stage_trace_recipe_c_runtime.py")
_PRIMARY_RELATIVE = PurePosixPath(RECIPE_C_SOURCE.primary_checkpoint_relative_path)
_SECONDARY_RELATIVE = PurePosixPath(RECIPE_C_SOURCE.secondary_staging_relative_path)
RECIPE_C_SMOKE_FRAMES = 6
_VOLUME_FULL = (100, 64, 256, 256)
_VOLUME_SMOKE = (RECIPE_C_SMOKE_FRAMES, 64, 256, 256)


@dataclass(frozen=True, slots=True)
class InferenceReceipt:
    status: Literal["READY", "FAILED"]
    selection_lock_id: str
    source_commit: str
    config_sha256: str
    predictor_sha256_before: str
    stage_predictor_sha256_after: str
    d4_predictor_sha256_after: str
    predictor_sha256_after: str
    primary_checkpoint_sha256: str
    secondary_checkpoint_sha256: str
    sample_ids: tuple[str, ...]
    mode: Literal["full", "smoke_6frame"]
    max_frames: int | None
    command: tuple[str, ...]
    command_sha256: str
    execution_argv_sha256: str
    cwd_role: str
    pythonpath: str
    resolved_device: str
    child_device: str
    child_stdout_sha256: str
    child_stderr_sha256: str
    device_candidates: tuple[str, ...]
    runtime_role: str
    patch_flags: Mapping[str, bool]
    raw_geffs: Mapping[str, str]
    postprocessed_csv: str | None
    final_geffs: Mapping[str, str]
    manifests: Mapping[str, str]
    counts: Mapping[str, object]
    started_at: str
    finished_at: str
    failure: Mapping[str, str] | None
    diagnostics: str | None = None
    diagnostics_sha256: str | None = None
    cuda_equivalence_validated: bool = False
    ground_truth_open_count: int = 0
    ground_truth_opened: bool = False
    metric_call_count: int = 0
    metric_status: str = "not_run_gt_guard"


@dataclass(frozen=True, slots=True)
class _SourceApi:
    load_config: Any
    apply_spatial_d4_patch: Any
    build_predict_command: Any
    write_submission_from_geff: Any
    _restore_modules: Any = None
    trace_filter_output_graph: Any = None
    trace_module_path: Path | None = None
    postprocessing_module_sha256: str | None = None
    configure_postprocessing: Any = None
    submission_graph_rows: Any = None
    source_root: Path | None = None
    source_module_paths: Mapping[str, Path] = field(default_factory=dict)
    source_module_provenance_before: Mapping[str, object] = field(default_factory=dict)

    def restore(self) -> None:
        if self._restore_modules is not None:
            self._restore_modules()


def _validate_selection_lock(selection_lock: Mapping[str, object] | Path) -> dict[str, object]:
    if isinstance(selection_lock, Path):
        return validate_selection_lock(selection_lock)
    if isinstance(selection_lock, Mapping):
        return validate_selection_lock_payload(selection_lock)
    raise TypeError("selection_lock must be a persisted Path or mapping")


def _validate_max_frames(max_frames: int | None) -> None:
    if max_frames is not None and (
        type(max_frames) is not int or max_frames != RECIPE_C_SMOKE_FRAMES
    ):
        raise ValueError(
            "max_frames must be omitted for full inference or exactly "
            f"{RECIPE_C_SMOKE_FRAMES} for the fixed smoke"
        )


def _sample_selection(sample_ids: Sequence[str], lock: Mapping[str, object], max_frames: int | None) -> tuple[str, ...]:
    _validate_max_frames(max_frames)
    panel = lock.get("panel")
    if not isinstance(panel, Mapping) or panel.get("panel_id") != "PANEL_V1":
        raise ValueError("selection lock does not contain PANEL_V1")
    if tuple(panel.get("sample_ids", ())) != PANEL_V1:
        raise ValueError("selection lock PANEL_V1 order is not exact")
    requested = tuple(sample_ids) or PANEL_V1
    if len(set(requested)) != len(requested) or any(sample not in PANEL_V1 for sample in requested):
        raise ValueError("sample_ids contain an unknown or duplicate panel sample")
    for sample in requested:
        if not sample or sample != sample.strip() or any(character in sample for character in ("/", "\\", ".")):
            raise ValueError("sample_ids must be clean sample stems")
    panel_positions = {sample: index for index, sample in enumerate(PANEL_V1)}
    if tuple(sorted(requested, key=panel_positions.__getitem__)) != requested:
        raise ValueError("sample_ids are not in PANEL_V1 order")
    if requested != PANEL_V1 and max_frames != RECIPE_C_SMOKE_FRAMES:
        raise ValueError("a panel subset is only permitted for the fixed six-frame smoke")
    if max_frames == RECIPE_C_SMOKE_FRAMES and len(requested) != 1:
        raise ValueError("smoke_6frame requires exactly one sample")
    return requested


def _assert_stage_lock_identity(stage: Any, lock: Mapping[str, object]) -> None:
    if lock.get("selection_lock_id") != getattr(stage, "selection_lock_id", None):
        raise ValueError("selection lock id does not match runtime stage")
    if lock.get("source_commit") != RECIPE_C_SOURCE.source_commit:
        raise ValueError("selection lock source commit does not match RECIPE_C_SOURCE")
    receipt = getattr(stage, "receipt", {})
    if not isinstance(receipt, Mapping):
        raise ValueError("runtime stage receipt is missing")
    if receipt.get("status") != "READY":
        raise ValueError("runtime stage receipt is not READY")
    if receipt.get("selection_lock_id") != lock.get("selection_lock_id"):
        raise ValueError("runtime stage receipt selection lock identity mismatch")
    expected_roles = {
        "repo": "repo",
        "weights": "repo/weights",
        "source_root": "source_root",
        "config": RECIPE_C_SOURCE.config_relative_path,
        "predictor": RECIPE_C_SOURCE.predictor_relative_path,
        "primary_checkpoint": RECIPE_C_SOURCE.primary_checkpoint_relative_path,
        "secondary_checkpoint": RECIPE_C_SOURCE.secondary_staging_relative_path,
    }
    if receipt.get("roles") != expected_roles:
        raise ValueError("runtime stage receipt role identity mismatch")
    required_receipt_fields = (
        "config_sha256",
        "predictor_sha256_before",
        "predictor_sha256_after",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_sha256",
        "resolved_device_candidates",
    )
    missing = [field for field in required_receipt_fields if field not in receipt]
    if missing:
        raise ValueError(f"runtime stage receipt identity is missing: {', '.join(missing)}")
    pairs = (
        ("config_sha256", "config_sha256"),
        ("predictor_sha256", "predictor_sha256_before"),
        ("primary_checkpoint_sha256", "primary_checkpoint_sha256"),
        ("secondary_checkpoint_sha256", "secondary_checkpoint_sha256"),
    )
    for lock_key, receipt_key in pairs:
        expected = lock.get(lock_key)
        actual = receipt.get(receipt_key)
        if expected is None or actual != expected:
            raise ValueError(f"selection lock identity mismatch: {lock_key}")
    candidates = tuple(receipt["resolved_device_candidates"])
    stage_candidates = tuple(getattr(stage, "device_candidates", ()))
    if candidates != device_module.DEVICE_SELECTION_ORDER or stage_candidates != candidates:
        raise ValueError("runtime stage device candidates do not match the canonical order")
    preimage = getattr(stage, "predictor_sha256_preimage", None)
    postimage = getattr(stage, "predictor_sha256_postimage", None)
    if (
        not isinstance(preimage, str)
        or len(preimage) != 64
        or not isinstance(postimage, str)
        or len(postimage) != 64
    ):
        raise ValueError("runtime stage predictor preimage/postimage identity is missing")
    if lock["predictor_sha256"] != preimage:
        raise ValueError("selection lock predictor preimage mismatch")
    if receipt["predictor_sha256_after"] != postimage:
        raise ValueError("runtime stage receipt predictor postimage mismatch")
    if lock.get("secondary_staging_relative_path") != RECIPE_C_SOURCE.secondary_staging_relative_path:
        raise ValueError("selection lock secondary checkpoint role mismatch")


def _preflight_images(image_root: Path, sample_ids: Sequence[str], max_frames: int | None) -> None:
    _validate_max_frames(max_frames)
    image_root = Path(image_root)
    if image_root.is_symlink() or not image_root.is_dir():
        raise ValueError("image_root must be a regular directory")
    for sample_id in sample_ids:
        image = image_root / f"{sample_id}.zarr"
        if image.is_symlink() or not image.is_dir():
            raise FileNotFoundError(f"image Zarr is missing: {sample_id}.zarr")
    try:
        import zarr
    except ModuleNotFoundError as exc:
        raise RuntimeError("zarr is required for pinned image preflight") from exc
    # Opening only the explicitly selected image stores is allowed.  Adjacent
    # .geff files are intentionally never enumerated or opened.
    for sample_id in sample_ids:
        image = image_root / f"{sample_id}.zarr"
        root = zarr.open(str(image), mode="r")
        array = root if hasattr(root, "shape") else root["0"] if "0" in root else None
        if array is None or tuple(array.shape) != _VOLUME_FULL:
            raise ValueError(f"image shape is not the pinned (100,64,256,256): {sample_id}")
        metadata = dict(getattr(root, "attrs", {}))
        multiscales = metadata.get("multiscales")
        if not isinstance(multiscales, list) or not multiscales or not isinstance(multiscales[0], Mapping):
            raise ValueError(f"image multiscales metadata is missing: {sample_id}")
        datasets = multiscales[0].get("datasets")
        transforms = (
            datasets[0].get("coordinateTransformations")
            if isinstance(datasets, list) and datasets and isinstance(datasets[0], Mapping)
            else None
        )
        scale = transforms[0].get("scale") if isinstance(transforms, list) and transforms else None
        if scale != [1.0, 1.625, 0.40625, 0.40625]:
            raise ValueError(f"image scale is not the pinned value: {sample_id}")
        statistics = metadata.get("image_statistics")
        if not isinstance(statistics, Mapping) or "quantiles" not in statistics:
            raise ValueError(f"image quantile metadata is missing: {sample_id}")


def _prepare_image_data(
    image_root: Path,
    sample_ids: Sequence[str],
    max_frames: int | None,
    scratch_root: Path,
) -> Path:
    _validate_max_frames(max_frames)
    if max_frames is None:
        return Path(image_root)
    import zarr

    data_root = scratch_root / "input"
    data_root.mkdir()
    for sample_id in sample_ids:
        source = zarr.open(str(Path(image_root) / f"{sample_id}.zarr"), mode="r")
        source_array = source if hasattr(source, "shape") else source["0"]
        source_metadata = getattr(source, "metadata", None)
        source_format = getattr(source_metadata, "zarr_format", None)
        destination = zarr.open_group(
            str(data_root / f"{sample_id}.zarr"),
            mode="w",
            **({"zarr_format": source_format} if source_format in {2, 3} else {}),
        )
        destination.attrs.update(dict(getattr(source, "attrs", {})))
        array_metadata = getattr(source_array, "metadata", None)
        dimension_names = getattr(array_metadata, "dimension_names", None)
        array_options: dict[str, object] = {
            "shape": (max_frames, *tuple(source_array.shape[1:])),
            "chunks": source_array.chunks,
            "dtype": source_array.dtype,
            "fill_value": getattr(source_array, "fill_value", None),
        }
        if dimension_names is not None:
            array_options["dimension_names"] = dimension_names
        if getattr(array_metadata, "zarr_format", None) == 3:
            codecs = tuple(getattr(array_metadata, "codecs", ()))
            # Zarr v3 supplies the bytes codec automatically; compressors are
            # the remaining codec chain and are safe to pass through intact.
            array_options["compressors"] = codecs[1:]
        else:
            for key in ("compressor", "filters"):
                value = getattr(source_array, key, None)
                if value is not None:
                    array_options[key] = value
        destination_array = destination.create_array("0", **array_options)
        destination_array.attrs.update(dict(source_array.attrs))
        destination_array[:] = source_array[:max_frames]
        _verify_image_subset(source, source_array, data_root / f"{sample_id}.zarr", max_frames)
    return data_root


def _verify_image_subset(
    source: Any, source_array: Any, destination_path: Path, max_frames: int
) -> None:
    """Reopen the written subset and verify bytes plus the pinned Zarr contract."""

    import numpy as np
    import zarr

    reopened = zarr.open_group(str(destination_path), mode="r")
    if "0" not in reopened:
        raise ValueError("reopened image subset is missing array 0")
    copied = reopened["0"]
    if tuple(copied.shape) != (max_frames, *tuple(source_array.shape[1:])):
        raise ValueError("reopened image subset shape mismatch")
    if copied.dtype != source_array.dtype or tuple(copied.chunks) != tuple(source_array.chunks):
        raise ValueError("reopened image subset dtype/chunks mismatch")
    if copied.fill_value != getattr(source_array, "fill_value", None):
        raise ValueError("reopened image subset fill value mismatch")
    if dict(reopened.attrs) != dict(getattr(source, "attrs", {})):
        raise ValueError("reopened image subset root metadata mismatch")
    if dict(copied.attrs) != dict(getattr(source_array, "attrs", {})):
        raise ValueError("reopened image subset array metadata mismatch")
    source_metadata = getattr(source_array, "metadata", None)
    copied_metadata = getattr(copied, "metadata", None)
    if source_metadata is not None and copied_metadata is not None:
        if getattr(copied_metadata, "dimension_names", None) != getattr(
            source_metadata, "dimension_names", None
        ):
            raise ValueError("reopened image subset dimension names mismatch")
        if getattr(copied_metadata, "codecs", None) != getattr(source_metadata, "codecs", None):
            raise ValueError("reopened image subset codecs mismatch")
    if getattr(source_metadata, "zarr_format", None) == 2:
        for key in ("compressor", "filters"):
            if getattr(copied, key, None) != getattr(source_array, key, None):
                raise ValueError(f"reopened image subset {key} mismatch")
    if not np.array_equal(copied[:], source_array[:max_frames]):
        raise ValueError("reopened image subset frame bytes mismatch")


def _load_source_api(stage: Any) -> _SourceApi:
    source_root = Path(os.fspath(stage.source_root)).resolve()
    source_src = source_root / "src"
    if not source_root.is_dir() or not source_src.is_dir():
        raise ValueError("pinned source root is not a regular source tree")
    saved_modules = {
        name: sys.modules[name]
        for name in tuple(sys.modules)
        if name == "biohub_pipeline" or name.startswith("biohub_pipeline.")
    }
    for name in tuple(saved_modules):
        sys.modules.pop(name, None)
    source_src_text = os.fspath(source_src)
    sys.path.insert(0, source_src_text)
    try:
        try:
            config_module = importlib.import_module("biohub_pipeline.config")
            inference_module = importlib.import_module("biohub_pipeline.inference")
            submission_module = importlib.import_module("biohub_pipeline.submission")
            trace_module = importlib.import_module("biohub_pipeline.postprocess_stage_trace")
            postprocessing_module = importlib.import_module("biohub_pipeline.postprocessing")
        except ModuleNotFoundError as exc:
            raise ValueError("pinned source required diagnostics module is missing") from exc
        modules = (config_module, inference_module, submission_module, trace_module, postprocessing_module)
        for module in modules:
            module_file = getattr(module, "__file__", None)
            if not isinstance(module_file, str):
                raise ValueError("pinned source module has no provenance file")
            try:
                Path(module_file).resolve(strict=True).relative_to(source_root)
            except (OSError, ValueError) as exc:
                raise ValueError("source module provenance is outside the pinned source root") from exc
        trace_path = (
            Path(trace_module.__file__).resolve(strict=True)
            if trace_module is not None
            else None
        )
        postprocessing_path = (
            Path(postprocessing_module.__file__).resolve(strict=True)
            if postprocessing_module is not None
            else None
        )
        trace_function = (
            getattr(trace_module, "filter_output_graph_traced", None)
        )
        configure_function = getattr(postprocessing_module, "configure", None)
        submission_graph_rows = getattr(submission_module, "graph_rows", None)
        if not callable(trace_function):
            raise ValueError("pinned source trace function is missing")
        if not callable(configure_function):
            raise ValueError("pinned source postprocessing configure function is missing")
        if not callable(submission_graph_rows):
            raise ValueError("pinned source submission canonicalizer is missing")
        module_paths = {
            "config": Path(config_module.__file__).resolve(strict=True),
            "inference": Path(inference_module.__file__).resolve(strict=True),
            "submission": Path(submission_module.__file__).resolve(strict=True),
            "postprocess_stage_trace": Path(trace_module.__file__).resolve(strict=True),
            "postprocessing": Path(postprocessing_module.__file__).resolve(strict=True),
        }
        provenance_before = _capture_source_provenance(module_paths, source_root)
    except BaseException:
        sys.path.remove(source_src_text)
        for name in tuple(sys.modules):
            if name == "biohub_pipeline" or name.startswith("biohub_pipeline."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        raise

    restored = False

    def restore_modules() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        if source_src_text in sys.path:
            sys.path.remove(source_src_text)
        for name in tuple(sys.modules):
            if name == "biohub_pipeline" or name.startswith("biohub_pipeline."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)

    return _SourceApi(
        load_config=config_module.load_config,
        apply_spatial_d4_patch=inference_module.apply_spatial_d4_patch,
        build_predict_command=inference_module.build_predict_command,
        write_submission_from_geff=submission_module.write_submission_from_geff,
        _restore_modules=restore_modules,
        trace_filter_output_graph=trace_function,
        trace_module_path=trace_path,
        postprocessing_module_sha256=(
            _sha256_file(postprocessing_path) if postprocessing_path is not None else None
        ),
        configure_postprocessing=lambda config, test_dir: configure_function(
            config.postprocessing, test_dir
        ),
        submission_graph_rows=submission_graph_rows,
        source_root=source_root,
        source_module_paths=module_paths,
        source_module_provenance_before=provenance_before,
    )


def _write_scratch_repo(stage: Any, root: Path) -> tuple[Path, Path, Path, Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    for relative in _REPO_FILES:
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(stage.read_repo_bytes(PurePosixPath(relative)))
    predictor = repo / "scripts/predict_unet_transformer.py"
    primary = repo / _PRIMARY_RELATIVE
    secondary = repo / _SECONDARY_RELATIVE
    primary.parent.mkdir(parents=True, exist_ok=True)
    secondary.parent.mkdir(parents=True, exist_ok=True)
    primary_payload = stage.primary_checkpoint_path.read_bytes()
    secondary_payload = stage.secondary_checkpoint_path.read_bytes()
    primary.write_bytes(primary_payload)
    secondary.write_bytes(secondary_payload)
    config = root / "recipe_c.yaml"
    config_payload = stage.staged_config.read_bytes()
    config.write_bytes(config_payload)

    receipt = getattr(stage, "receipt", None)
    if not isinstance(receipt, Mapping):
        raise ValueError("runtime stage receipt is missing for scratch identity")
    expected = {
        "predictor_sha256_after": getattr(stage, "predictor_sha256_postimage", None),
        "config_sha256": receipt.get("config_sha256"),
        "primary_checkpoint_sha256": receipt.get("primary_checkpoint_sha256"),
        "secondary_checkpoint_sha256": receipt.get("secondary_checkpoint_sha256"),
    }
    actual = {
        "predictor_sha256_after": _sha256(predictor.read_bytes()),
        "config_sha256": _sha256(config_payload),
        "primary_checkpoint_sha256": _sha256(primary_payload),
        "secondary_checkpoint_sha256": _sha256(secondary_payload),
    }
    for key, value in actual.items():
        expected_hash = expected[key]
        if not isinstance(expected_hash, str) or value != expected_hash:
            raise ValueError(f"scratch artifact identity mismatch: {key}")
    return repo, predictor, primary, secondary, config


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _assert_scratch_lock_identity(
    stage: Any,
    lock: Mapping[str, object],
    predictor: Path,
    primary: Path,
    secondary: Path,
    config: Path,
) -> None:
    values = {
        "config_sha256": _sha256(config.read_bytes()),
        "primary_checkpoint_sha256": _sha256(primary.read_bytes()),
        "secondary_checkpoint_sha256": _sha256(secondary.read_bytes()),
    }
    for key, actual in values.items():
        if lock.get(key) != actual:
            raise ValueError(f"scratch artifact does not match selection lock: {key}")
    stage_postimage = getattr(stage, "predictor_sha256_postimage", None)
    actual_predictor = _sha256(predictor.read_bytes())
    if not isinstance(stage_postimage, str) or actual_predictor != stage_postimage:
        raise ValueError("scratch predictor does not match stage postimage")


def _argv_sha256(argv: Sequence[str]) -> str:
    return _sha256(json.dumps(list(argv), separators=(",", ":")).encode("utf-8"))


def _publish_derived(stage: Any, relative: PurePosixPath, payload: bytes) -> dict[str, object]:
    receipt = stage.publish_repo_bytes(relative, payload, expected="absent")
    expected_hash = _sha256(payload)
    if (
        getattr(receipt, "sha256", None) != expected_hash
        or getattr(receipt, "size", None) != len(payload)
        or getattr(receipt, "fsynced", None) is not True
    ):
        raise ValueError(f"published repository artifact identity is invalid: {relative}")
    return {
        "relative_path": relative.as_posix(),
        "sha256": expected_hash,
        "size": len(payload),
    }


def _rewrite_command(
    command: Sequence[object],
    data_role: str,
    primary_relative: PurePosixPath,
    secondary_relative: PurePosixPath,
) -> tuple[str, ...]:
    values = [str(value) for value in command]
    if len(values) < 2:
        raise ValueError("source command is incomplete")
    values[0] = "python"
    values[1] = _PREDICTOR_DERIVED.as_posix()
    replacements = {
        "--data-dir": data_role,
        "--splits": _SPLITS_DERIVED.as_posix(),
        "--weights": primary_relative.as_posix(),
        "--ensemble-weights": secondary_relative.as_posix(),
    }
    flag_arity = {
        "--data-dir": 1,
        "--splits": 1,
        "--weights": 1,
        "--ensemble-weights": 1,
        "--split": 1,
        "--method": 1,
        "--det-threshold": 1,
        "--edge-threshold": 1,
        "--ensemble-alpha": 1,
        "--ilp-edge-weight": 1,
        "--ilp-appearance-weight": 1,
        "--ilp-disappearance-weight": 1,
        "--ilp-division-weight": 1,
        "--use-ilp": 0,
        "--unet-batch-size": 1,
        "--margin-gated-dist-lambda": 1,
        "--margin-gated-dist-delta": 1,
        "--margin-gated-dist-dens-min": 1,
        "--margin-gated-dist-radius-um": 1,
        "--pairwise-hardneg-weights": 1,
        "--pairwise-hardneg-dens-min": 1,
        "--pairwise-hardneg-gap-max": 1,
        "--pairwise-hardneg-radius-um": 1,
    }
    index = 2
    seen: set[str] = set()
    while index < len(values):
        flag = values[index]
        if not flag.startswith("-"):
            raise ValueError("command contains an unexpected positional role")
        arity = flag_arity.get(flag)
        if arity is None:
            raise ValueError(f"command contains an unknown or untrusted flag: {flag}")
        if flag in seen:
            raise ValueError(f"command contains a duplicate role: {flag}")
        seen.add(flag)
        if arity:
            if index + arity >= len(values) or values[index + 1].startswith("--"):
                raise ValueError(f"command flag is missing a value: {flag}")
            if flag in replacements:
                values[index + 1] = replacements[flag]
            index += 2
            continue
        index += 1
    required = {"--data-dir", "--splits", "--weights", "--ensemble-weights"}
    if not required <= seen:
        raise ValueError("source command is missing a required role mapping")
    forbidden = ("/proc/self/fd", "ground_truth", "gt_path", "train/*.geff", "--evaluate")
    for value in values:
        if value.startswith("/") or any(token in value.lower() for token in forbidden):
            raise ValueError("command contains an absolute, GT, or forbidden role")
    for flag, expected in replacements.items():
        index = values.index(flag)
        actual = values[index + 1]
        if actual != expected:
            raise ValueError(f"command role mapping mismatch for {flag}")
    expected_flags = {
        "--det-threshold": "0.96875",
        "--edge-threshold": "0.4",
        "--ensemble-alpha": "0.5",
        "--ilp-edge-weight": "-1.0",
        "--ilp-appearance-weight": "0.0",
        "--ilp-disappearance-weight": "1.575",
        "--ilp-division-weight": "1.0",
    }
    for flag, expected in expected_flags.items():
        try:
            actual = values[values.index(flag) + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"command is missing required value: {flag}") from exc
        if actual != expected:
            raise ValueError(f"command value mismatch for {flag}: {actual!r}")
    if "--use-ilp" not in values:
        raise ValueError("command must enable ILP")
    return tuple(values)


def _assert_exact_raw_sources(raw_sources: Sequence[Path], sample_ids: Sequence[str]) -> None:
    stems = [path.stem for path in raw_sources]
    expected = tuple(sample_ids)
    if len(stems) != len(expected) or len(set(stems)) != len(stems) or set(stems) != set(expected):
        raise ValueError("raw predictor output does not exactly cover selected samples")


def _copy_raw_prediction(
    source: Path,
    destination: Path,
    *,
    on_published: Callable[[Path, tuple[int, int]], None] | None = None,
) -> tuple[int, int]:
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"raw prediction is not a regular GEFF directory: {source.name}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    temporary_stat = temporary.lstat()
    temporary_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
    destination_identity: tuple[int, int] | None = None
    try:
        shutil.copytree(source, temporary, symlinks=False, dirs_exist_ok=True)
        _fsync_tree(temporary)
        publish_directory_noreplace(temporary, destination)
        destination_identity = _entry_identity(destination)
        if on_published is not None:
            on_published(destination, destination_identity)
        fsync_directory(destination.parent)
        return destination_identity
    except BaseException:
        if destination_identity is not None:
            try:
                current = destination.lstat()
                if (current.st_dev, current.st_ino) == destination_identity:
                    shutil.rmtree(destination)
                    try:
                        fsync_directory(destination.parent)
                    except BaseException:
                        pass
            except FileNotFoundError:
                pass
        try:
            current = temporary.lstat()
            if (current.st_dev, current.st_ino) == temporary_identity:
                shutil.rmtree(temporary)
        except FileNotFoundError:
            pass
        raise


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("prediction tree contains a symlink")
        if path.is_file():
            _fsync_file(path)
    for path in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        fsync_directory(path)
    fsync_directory(root)


def _role_path(path: Path, output_root: Path) -> str:
    return path.relative_to(output_root).as_posix()


def _execution_data_role(runtime_stage: Any, data_root: Path) -> str:
    """Return a relative data role that is valid from the physical fd cwd."""

    repo_view = getattr(runtime_stage, "repo_dir", None)
    logical_repo = getattr(repo_view, "logical_path", None)
    if not isinstance(logical_repo, Path) or not logical_repo.is_absolute():
        raise ValueError("runtime stage logical repository path must be absolute")
    if logical_repo.is_symlink() or not logical_repo.is_dir():
        raise ValueError("runtime stage logical repository path must be a regular directory")

    repo_fd = getattr(runtime_stage, "repo_fd", None)
    if not isinstance(repo_fd, int) or repo_fd < 0:
        raise ValueError("runtime stage repository descriptor is invalid")
    logical_stat = logical_repo.stat()
    fd_stat = os.fstat(repo_fd)
    if not stat.S_ISDIR(fd_stat.st_mode) or (logical_stat.st_dev, logical_stat.st_ino) != (
        fd_stat.st_dev,
        fd_stat.st_ino,
    ):
        raise ValueError("runtime stage logical repository does not match repository descriptor")

    data_root = Path(data_root)
    if not data_root.is_absolute() or data_root.is_symlink() or not data_root.is_dir():
        raise ValueError("execution data root must be an absolute regular directory")
    resolved_data_root = data_root.resolve(strict=True)
    role = os.path.relpath(str(resolved_data_root), start=str(logical_repo))
    if Path(role).is_absolute():
        raise ValueError("execution data role must be relative")
    try:
        resolved_role = (logical_repo / role).resolve(strict=True)
    except OSError as exc:
        raise ValueError("execution data role does not resolve to the runner-owned input") from exc
    if resolved_role != resolved_data_root:
        raise ValueError("execution data role does not resolve to the runner-owned input")
    return role


def _safe_failure_message(exc: BaseException) -> str:
    # A type-only message is deliberate: failure receipts must not become a
    # side channel for credentials, host paths, or GT paths.
    return type(exc).__name__


def _entry_identity(path: Path) -> tuple[int, int]:
    stat_result = path.lstat()
    return stat_result.st_dev, stat_result.st_ino


def _cleanup_owned_entries(
    owned_entries: Mapping[Path, tuple[int, int]], recursive_entries: set[Path] | None = None
) -> None:
    recursive_entries = recursive_entries or set()
    for path, identity in sorted(owned_entries.items(), key=lambda item: len(item[0].parts), reverse=True):
        try:
            stat_result = path.lstat()
        except FileNotFoundError:
            continue
        if (stat_result.st_dev, stat_result.st_ino) != identity:
            continue
        if path in recursive_entries and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError:
                pass
        else:
            path.unlink()


def _write_failed(
    output_root: Path,
    lock_id: str,
    phase: str,
    exc: BaseException,
    command: Sequence[str],
    *,
    owned_entries: Mapping[Path, tuple[int, int]] | None = None,
    recursive_entries: set[Path] | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> None:
    if not output_root.is_dir():
        return
    if owned_entries:
        _cleanup_owned_entries(owned_entries, recursive_entries or set())
    payload = {
        "status": "FAILED",
        "selection_lock_id": lock_id,
        "phase": phase,
        "error_type": _safe_failure_message(exc),
        "command_sha256": _sha256(json.dumps(list(command), separators=(",", ":")).encode()),
        "reusable": False,
        "diagnostics": _json_safe(diagnostics or {}, label="failure.diagnostics"),
    }
    write_json_exclusive(output_root / "FAILED.json", payload, mode=0o644)


def _resolve_device(requested_device: str) -> str:
    resolved = device_module.resolve_torch_device(requested_device)
    device_name = getattr(resolved, "type", None)
    if not isinstance(device_name, str) or not device_name:
        raise ValueError("canonical device resolver returned an invalid device")
    return device_name


def _extract_child_device(stdout: str, expected: str) -> str:
    matches = re.findall(r"(?<![A-Za-z0-9_])device=(cuda|mps|cpu)(?![A-Za-z0-9_])", stdout)
    if len(matches) != 1 or matches[0] != expected:
        raise ValueError("child predictor device output is missing, duplicated, or mismatched")
    return matches[0]


def _write_ready_receipt(output_root: Path, receipt: InferenceReceipt) -> None:
    write_json_exclusive(output_root / "receipt.json", asdict(receipt), mode=0o644)


def run_recipe_c_inference(
    image_root: Path,
    sample_ids: Sequence[str],
    runtime_stage: Any,
    selection_lock: Mapping[str, object] | Path,
    output_root: Path,
    max_frames: int | None = None,
) -> InferenceReceipt:
    """Run the pinned source recipe with no GT/evaluation boundary crossing."""

    started = datetime.now(UTC).isoformat()
    output_root = Path(output_root)
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"output root must be fresh: {output_root.name}")
    output_root.mkdir(parents=True, exist_ok=False)
    lock: dict[str, object] | None = None
    lock_id = "unvalidated"
    phase = "preflight"
    command: tuple[str, ...] = ()
    execution_argv_sha256 = ""
    resolved_device = ""
    child_device = ""
    child_stdout_sha256 = ""
    child_stderr_sha256 = ""
    chosen_samples: tuple[str, ...] = ()
    diagnostics_path: Path | None = None
    diagnostics_sha256: str | None = None
    diagnostic_run: DiagnosticRecorder | None = None
    source_api: Any = None
    owned_entries: dict[Path, tuple[int, int]] = {}
    recursive_entries: set[Path] = set()

    def remember(
        path: Path,
        *,
        identity: tuple[int, int] | None = None,
        recursive: bool = False,
    ) -> None:
        owned_entries[path] = _entry_identity(path) if identity is None else identity
        if recursive:
            recursive_entries.add(path)

    try:
        lock = _validate_selection_lock(selection_lock)
        candidate_lock_id = lock.get("selection_lock_id")
        if isinstance(candidate_lock_id, str) and candidate_lock_id:
            lock_id = candidate_lock_id
        chosen_samples = _sample_selection(sample_ids, lock, max_frames)
        _assert_stage_lock_identity(runtime_stage, lock)
        requested_device = lock.get("requested_device")
        if requested_device != "auto":
            raise ValueError("selection lock requested_device must be exactly auto")
        resolved_device = _resolve_device(requested_device)
        diagnostic_run = DiagnosticRecorder.create(chosen_samples, resolved_device)
        if resolved_device not in tuple(runtime_stage.device_candidates):
            raise ValueError("resolved device is not present in the runtime stage candidates")
        _preflight_images(Path(image_root), chosen_samples, max_frames)
        with runtime_stage:
            source_api = _load_source_api(runtime_stage)
            phase = "patch"
            with tempfile.TemporaryDirectory(prefix="recipe-c-") as scratch_name:
                scratch = Path(scratch_name)
                (
                    scratch_repo,
                    scratch_predictor,
                    scratch_primary,
                    scratch_secondary,
                    scratch_config,
                ) = _write_scratch_repo(runtime_stage, scratch)
                _assert_scratch_lock_identity(
                    runtime_stage,
                    lock,
                    scratch_predictor,
                    scratch_primary,
                    scratch_secondary,
                    scratch_config,
                )
                data_root = _prepare_image_data(Path(image_root), chosen_samples, max_frames, scratch)
                diagnostic_run.record_image_input(
                    _VOLUME_SMOKE if max_frames is not None else _VOLUME_FULL
                )
                config = source_api.load_config(scratch_config)
                source_api.configure_postprocessing(config, data_root)
                trace_function = diagnostic_run.prepare_trace(
                    source_api,
                    scratch / "diagnostics" / _TRACE_DERIVED.name,
                    _TRACE_DERIVED,
                    lambda relative, payload: _publish_derived(runtime_stage, relative, payload),
                )
                patched = source_api.apply_spatial_d4_patch(scratch_repo, "scripts/predict_unet_transformer.py")
                if patched is not True:
                    raise RuntimeError("spatial D4 patch did not report a fresh postimage")
                py_compile.compile(str(scratch_predictor), doraise=True)
                d4_predictor_payload = scratch_predictor.read_bytes()
                stage_predictor_postimage = getattr(runtime_stage, "predictor_sha256_postimage", None)
                if not isinstance(stage_predictor_postimage, str):
                    raise ValueError("runtime stage predictor postimage is missing")
                d4_predictor_sha256_after = _sha256(d4_predictor_payload)
                if d4_predictor_sha256_after == stage_predictor_postimage:
                    raise ValueError("spatial D4 did not produce a distinct runtime predictor postimage")
                raw_command, splits_path = source_api.build_predict_command(
                    config,
                    data_root,
                    scratch_repo,
                    scratch_primary,
                    list(chosen_samples),
                )
                if not isinstance(raw_command, (list, tuple)) or not isinstance(splits_path, Path):
                    raise TypeError("source builder returned an invalid command or splits path")
                try:
                    splits_path.resolve(strict=True).relative_to(scratch_repo.resolve())
                except (OSError, ValueError) as exc:
                    raise ValueError("source builder returned a splits path outside isolated scratch") from exc
                if splits_path.is_symlink() or not splits_path.is_file():
                    raise ValueError("source builder returned a non-regular splits artifact")
                py_compile.compile(str(scratch_predictor), doraise=True)
                predictor_payload = diagnostic_run.instrument_predictor(
                    scratch_predictor,
                    lambda path: py_compile.compile(str(path), doraise=True),
                )
                if (
                    trace_function is not None
                    and getattr(source_api, "trace_module_path", None) is not None
                    and not diagnostic_run.predictor_diagnostic_instrumented
                ):
                    raise ValueError("child predictor diagnostic instrumentation was not applied")
                predictor_sha256_after = _sha256(predictor_payload)
                if predictor_sha256_after == d4_predictor_sha256_after:
                    raise ValueError("source builder did not produce a distinct final predictor postimage")
                if predictor_sha256_after == stage_predictor_postimage:
                    raise ValueError("final predictor postimage must differ from the stage device postimage")
                splits_payload = splits_path.read_bytes()
                publish_predictor = _publish_derived(runtime_stage, _PREDICTOR_DERIVED, predictor_payload)
                publish_splits = _publish_derived(runtime_stage, _SPLITS_DERIVED, splits_payload)
                phase = "subprocess"
                environment = {
                    **os.environ,
                    "PYTHONPATH": "src",
                    "BIOHUB_TORCH_DEVICE": resolved_device,
                }
                repo_cwd = os.fspath(runtime_stage.repo_dir)
                execution_data_role = _execution_data_role(runtime_stage, data_root)
                execution_command = _rewrite_command(
                    raw_command,
                    execution_data_role,
                    _PRIMARY_RELATIVE,
                    _SECONDARY_RELATIVE,
                )
                # Persist only a stable role label; the execution argv may
                # carry a relative path to the run-local temporary subset.
                command = _rewrite_command(
                    raw_command,
                    "input",
                    _PRIMARY_RELATIVE,
                    _SECONDARY_RELATIVE,
                )
                execution_argv_sha256 = _argv_sha256(execution_command)
                completed = subprocess.run(
                    list(execution_command),
                    cwd=repo_cwd,
                    pass_fds=(runtime_stage.repo_fd,),
                    env=environment,
                    shell=False,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"child predictor returned non-zero status: {completed.returncode}")
                child_stdout = completed.stdout or ""
                child_stderr = completed.stderr or ""
                child_device = _extract_child_device(child_stdout, resolved_device)
                diagnostic_run.set_child_device(child_device)
                diagnostic_run.parse_child_stdout(child_stdout)
                child_stdout_sha256 = _sha256(child_stdout.encode("utf-8"))
                child_stderr_sha256 = _sha256(child_stderr.encode("utf-8"))
                username = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
                stage_predictions = Path(repo_cwd) / "predictions" / username / "unet_transformer" / "split_0"
                raw_sources = sorted(stage_predictions.glob("*.geff"))
                _assert_exact_raw_sources(raw_sources, chosen_samples)
                phase = "raw_persist"
                raw_root = output_root / "raw"
                raw_root.mkdir()
                remember(raw_root)
                raw_geffs: dict[str, Path] = {}
                raw_counts: dict[str, object] = {}
                raw_graphs: dict[str, tuple[dict[int, dict[str, object]], list[dict[str, object]]]] = {}
                strict_diagnostics = trace_function is not None and getattr(
                    source_api, "trace_module_path", None
                ) is not None
                for source in raw_sources:
                    destination = raw_root / source.name
                    _copy_raw_prediction(
                        source,
                        destination,
                        on_published=lambda path, identity: remember(
                            path, identity=identity, recursive=True
                        ),
                    )
                    counts = validate_prediction_geff(
                        destination,
                        source.stem,
                        expected_volume_shape_tzyx=(
                            _VOLUME_SMOKE if max_frames is not None else _VOLUME_FULL
                        ),
                    )
                    raw_geffs[source.stem] = destination
                    raw_counts[source.stem], raw_graphs[source.stem] = diagnostic_run.record_raw_sample(
                        source.stem,
                        destination,
                        counts,
                        directory_digest_report(destination),
                        strict=strict_diagnostics,
                    )
                diagnostic_run.record_shadow_trace(
                    raw_graphs,
                    trace_function,
                    strict=True,
                )
                phase = "postprocess"
                csv_path = output_root / "submission.csv"
                csv_temporary = output_root / ".submission.csv.tmp"
                csv_temporary.touch(exist_ok=False)
                remember(csv_temporary)
                source_api.write_submission_from_geff(
                    list(raw_geffs.values()), config, data_root, csv_temporary
                )
                publish_file_noreplace(csv_temporary, csv_path)
                owned_entries.pop(csv_temporary, None)
                remember(csv_path)
                from biohub.submission.validator import validate_submission

                report = validate_submission(
                    csv_path,
                    expected_datasets=chosen_samples,
                    volume_shape_tzyx=(
                        _VOLUME_SMOKE if max_frames is not None else _VOLUME_FULL
                    ),
                    ground_truth_nodes=None,
                    require_divisions=False,
                )
                if not report.ok:
                    raise ValueError("source submission failed target structural validation")
                csv_graphs = diagnostic_run.record_csv_and_trace(
                    csv_path,
                    raw_graphs,
                    None,
                    strict=strict_diagnostics,
                    trace_module_sha256=diagnostic_run.trace_module_sha256,
                )
                unexpected = {
                    child.name
                    for child in output_root.iterdir()
                    if child.name not in {"raw", "submission.csv"}
                }
                if unexpected:
                    raise ValueError("source postprocess produced an unexpected output entry")
                phase = "bridge"
                final_root = output_root / "predictions"

                def remember_bridge(path: Path, identity: tuple[int, int]) -> None:
                    remember(path, identity=identity, recursive=path != final_root)

                final_geffs = postprocessed_csv_to_geffs(
                    csv_path,
                    final_root,
                    sample_ids=chosen_samples,
                    provenance={"source_commit": runtime_stage.receipt.get("source_commit", "")},
                    on_published=remember_bridge,
                )
                final_counts: dict[str, object] = {}
                for sample_id, final in final_geffs.items():
                    counts = validate_prediction_geff(
                        final,
                        sample_id,
                        expected_volume_shape_tzyx=(
                            _VOLUME_SMOKE if max_frames is not None else _VOLUME_FULL
                        ),
                    )
                    final_counts[sample_id] = diagnostic_run.record_final_sample(
                        sample_id,
                        final,
                        counts,
                        directory_digest_report(final),
                        csv_graphs[sample_id],
                        strict=strict_diagnostics,
                        digest_function=directory_digest_report,
                    )
                if max_frames == RECIPE_C_SMOKE_FRAMES:
                    diagnostic_run.assert_six_frame_contract()
                diagnostics_path = output_root / "diagnostics.json"
                source_module_provenance_after = _verify_source_provenance(
                    getattr(source_api, "source_module_paths", {}),
                    getattr(source_api, "source_root", Path.cwd()),
                    getattr(source_api, "source_module_provenance_before", {}),
                )
                diagnostic_provenance = diagnostic_run.provenance(
                    source_commit=str(lock["source_commit"]),
                    predictor_sha256_before=str(lock["predictor_sha256"]),
                    predictor_sha256_after=predictor_sha256_after,
                    postprocessing_module_sha256=getattr(
                        source_api, "postprocessing_module_sha256", None
                    ),
                    child_stdout_sha256=child_stdout_sha256,
                    source_module_provenance_before=getattr(
                        source_api, "source_module_provenance_before", {}
                    ),
                    source_module_provenance_after=source_module_provenance_after,
                )
                diagnostics_sha256 = diagnostic_run.finalize(
                    diagnostics_path,
                    diagnostic_provenance,
                    write_json_exclusive,
                )
                remember(diagnostics_path)
                phase = "manifest"
                manifests: dict[str, Path] = {}
                for sample_id, final in final_geffs.items():
                    manifest = write_prediction_manifest(
                        final,
                        selection_lock_id=str(lock["selection_lock_id"]),
                        provenance={
                            "source_commit": str(lock["source_commit"]),
                            "config_sha256": runtime_stage.receipt.get("config_sha256", ""),
                            "predictor_sha256_before": runtime_stage.receipt.get(
                                "predictor_sha256_before", ""
                            ),
                            "predictor_sha256": predictor_sha256_after,
                            "predictor_sha256_after": predictor_sha256_after,
                            "d4_predictor_sha256_after": d4_predictor_sha256_after,
                            "stage_predictor_sha256_after": stage_predictor_postimage,
                            "primary_checkpoint_sha256": runtime_stage.receipt.get(
                                "primary_checkpoint_sha256", ""
                            ),
                            "secondary_checkpoint_sha256": runtime_stage.receipt.get(
                                "secondary_checkpoint_sha256", ""
                            ),
                            "resolved_device": resolved_device,
                            "device_candidates": ",".join(runtime_stage.device_candidates),
                            "patch_spatial_d4": True,
                            "patch_builder": True,
                            "runtime_role": "live_stage_repo",
                            "command_sha256": _argv_sha256(command),
                            "execution_argv_sha256": execution_argv_sha256,
                            "child_device": child_device,
                            "child_stdout_sha256": child_stdout_sha256,
                            "child_stderr_sha256": child_stderr_sha256,
                            **diagnostic_provenance,
                        },
                    )
                    remember(manifest)
                    mint_prediction_token(final)
                    manifests[sample_id] = manifest
                if _verify_source_provenance(
                    getattr(source_api, "source_module_paths", {}),
                    getattr(source_api, "source_root", Path.cwd()),
                    getattr(source_api, "source_module_provenance_before", {}),
                ) != source_module_provenance_after:
                    raise ValueError("pinned source module provenance changed before READY")
                finished = datetime.now(UTC).isoformat()
                receipt = InferenceReceipt(
                    status="READY",
                    selection_lock_id=str(lock["selection_lock_id"]),
                    source_commit=str(lock["source_commit"]),
                    config_sha256=str(lock["config_sha256"]),
                    predictor_sha256_before=str(lock["predictor_sha256"]),
                    stage_predictor_sha256_after=stage_predictor_postimage,
                    d4_predictor_sha256_after=d4_predictor_sha256_after,
                    predictor_sha256_after=predictor_sha256_after,
                    primary_checkpoint_sha256=str(lock["primary_checkpoint_sha256"]),
                    secondary_checkpoint_sha256=str(lock["secondary_checkpoint_sha256"]),
                    sample_ids=tuple(chosen_samples),
                    mode="smoke_6frame" if max_frames is not None else "full",
                    max_frames=max_frames,
                    command=command,
                    command_sha256=_argv_sha256(command),
                    execution_argv_sha256=execution_argv_sha256,
                    cwd_role="repo",
                    pythonpath="src",
                    resolved_device=resolved_device,
                    child_device=child_device,
                    child_stdout_sha256=child_stdout_sha256,
                    child_stderr_sha256=child_stderr_sha256,
                    device_candidates=tuple(runtime_stage.device_candidates),
                    runtime_role="live_stage_repo",
                    patch_flags={
                        "spatial_d4": True,
                        "builder": True,
                        "stage_device_postimage_verified": True,
                        "runtime_d4_postimage_verified": True,
                        "runtime_builder_postimage_verified": True,
                        "predictor_diagnostic_instrumented": diagnostic_run.predictor_diagnostic_instrumented,
                        "source_stage_trace_loaded": trace_function is not None,
                    },
                    raw_geffs={sample: _role_path(path, output_root) for sample, path in raw_geffs.items()},
                    postprocessed_csv=_role_path(csv_path, output_root),
                    final_geffs={sample: _role_path(path, output_root) for sample, path in final_geffs.items()},
                    manifests={sample: _role_path(path, output_root) for sample, path in manifests.items()},
                    counts={
                        "raw": raw_counts,
                        "final": final_counts,
                        "publish": {
                            "predictor": publish_predictor,
                            "splits": publish_splits,
                            **(
                                {"trace": diagnostic_run.trace_publish}
                                if diagnostic_run.trace_publish is not None
                                else {}
                            ),
                        },
                        "diagnostic": diagnostic_run.receipt_counts(
                            getattr(source_api, "postprocessing_module_sha256", None)
                        ),
                    },
                    started_at=started,
                    finished_at=finished,
                    failure=None,
                    diagnostics=_role_path(diagnostics_path, output_root) if diagnostics_path is not None else None,
                    diagnostics_sha256=diagnostics_sha256,
                    cuda_equivalence_validated=False,
                    ground_truth_open_count=0,
                    ground_truth_opened=False,
                    metric_call_count=0,
                    metric_status="not_run_gt_guard",
                )
                _write_ready_receipt(output_root, receipt)
                return receipt
    except BaseException as exc:
        if diagnostic_run is not None:
            diagnostic_run.mark_failed()
        try:
            _write_failed(
                output_root,
                lock_id,
                phase,
                exc,
                command,
                owned_entries=owned_entries,
                recursive_entries=recursive_entries,
                diagnostics=diagnostic_run.state if diagnostic_run is not None else {},
            )
        except BaseException as cleanup_error:
            exc.add_note(f"failed receipt cleanup: {type(cleanup_error).__name__}")
        raise
    finally:
        if source_api is not None:
            restore = getattr(source_api, "restore", None)
            if callable(restore):
                restore()


__all__ = ["RECIPE_C_SMOKE_FRAMES", "InferenceReceipt", "run_recipe_c_inference"]
