"""GT-free, failure-atomic Recipe C inference orchestration."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import py_compile
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from biohub.recipe_c.geff_bridge import (
    postprocessed_csv_to_geffs,
    validate_prediction_geff,
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
_PRIMARY_RELATIVE = PurePosixPath(RECIPE_C_SOURCE.primary_checkpoint_relative_path)
_SECONDARY_RELATIVE = PurePosixPath(RECIPE_C_SOURCE.secondary_staging_relative_path)
_VOLUME_FULL = (100, 64, 256, 256)
_VOLUME_SMOKE = (2, 64, 256, 256)


@dataclass(frozen=True, slots=True)
class InferenceReceipt:
    status: Literal["READY", "FAILED"]
    selection_lock_id: str
    sample_ids: tuple[str, ...]
    mode: Literal["full", "smoke_2frame"]
    max_frames: int | None
    command: tuple[str, ...]
    cwd_role: str
    pythonpath: str
    resolved_device: str
    device_candidates: tuple[str, ...]
    patch_flags: Mapping[str, bool]
    raw_geffs: Mapping[str, str]
    postprocessed_csv: str | None
    final_geffs: Mapping[str, str]
    manifests: Mapping[str, str]
    counts: Mapping[str, object]
    started_at: str
    finished_at: str
    failure: Mapping[str, str] | None


@dataclass(frozen=True, slots=True)
class _SourceApi:
    load_config: Any
    apply_spatial_d4_patch: Any
    build_predict_command: Any
    write_submission_from_geff: Any


def _validate_selection_lock(selection_lock: Mapping[str, object] | Path) -> dict[str, object]:
    if isinstance(selection_lock, Path):
        return validate_selection_lock(selection_lock)
    if isinstance(selection_lock, Mapping):
        return validate_selection_lock_payload(selection_lock)
    raise TypeError("selection_lock must be a persisted Path or mapping")


def _sample_selection(sample_ids: Sequence[str], lock: Mapping[str, object], max_frames: int | None) -> tuple[str, ...]:
    panel = lock.get("panel")
    if not isinstance(panel, Mapping) or panel.get("panel_id") != "PANEL_V1":
        raise ValueError("selection lock does not contain PANEL_V1")
    if tuple(panel.get("sample_ids", ())) != PANEL_V1:
        raise ValueError("selection lock PANEL_V1 order is not exact")
    requested = tuple(sample_ids) or PANEL_V1
    if len(set(requested)) != len(requested) or any(sample not in PANEL_V1 for sample in requested):
        raise ValueError("sample_ids contain an unknown or duplicate panel sample")
    panel_positions = {sample: index for index, sample in enumerate(PANEL_V1)}
    if tuple(sorted(requested, key=panel_positions.__getitem__)) != requested:
        raise ValueError("sample_ids are not in PANEL_V1 order")
    if requested != PANEL_V1 and max_frames != 2:
        raise ValueError("a panel subset is only permitted for the two-frame smoke")
    if max_frames is not None and max_frames != 2:
        raise ValueError("max_frames must be exactly 2 when provided")
    return requested


def _assert_stage_lock_identity(stage: Any, lock: Mapping[str, object]) -> None:
    if lock.get("selection_lock_id") != getattr(stage, "selection_lock_id", None):
        raise ValueError("selection lock id does not match runtime stage")
    receipt = getattr(stage, "receipt", {})
    if not isinstance(receipt, Mapping):
        receipt = {}
    pairs = (
        ("source_commit", "source_commit"),
        ("config_sha256", "config_sha256"),
        ("predictor_sha256", "predictor_sha256_before"),
        ("primary_checkpoint_sha256", "primary_checkpoint_sha256"),
        ("secondary_checkpoint_sha256", "secondary_checkpoint_sha256"),
    )
    for lock_key, receipt_key in pairs:
        expected = lock.get(lock_key)
        actual = receipt.get(receipt_key)
        if expected is not None and actual is not None and expected != actual:
            raise ValueError(f"selection lock identity mismatch: {lock_key}")
    preimage = getattr(stage, "predictor_sha256_preimage", None)
    if lock.get("predictor_sha256") is not None and preimage is not None:
        if lock["predictor_sha256"] != preimage:
            raise ValueError("selection lock predictor preimage mismatch")
    if lock.get("secondary_staging_relative_path") not in {
        None,
        RECIPE_C_SOURCE.secondary_staging_relative_path,
    }:
        raise ValueError("selection lock secondary checkpoint role mismatch")


def _preflight_images(image_root: Path, sample_ids: Sequence[str], max_frames: int | None) -> None:
    image_root = Path(image_root)
    if image_root.is_symlink() or not image_root.is_dir():
        raise ValueError("image_root must be a regular directory")
    for sample_id in sample_ids:
        image = image_root / f"{sample_id}.zarr"
        if image.is_symlink() or not image.is_dir():
            raise FileNotFoundError(f"image Zarr is missing: {sample_id}.zarr")
    # Opening only the explicitly selected image stores is allowed.  Adjacent
    # .geff files are intentionally never enumerated or opened.
    try:
        import zarr
    except ModuleNotFoundError:
        return
    for sample_id in sample_ids:
        image = image_root / f"{sample_id}.zarr"
        root = zarr.open(str(image), mode="r")
        array = root if hasattr(root, "shape") else root["0"] if "0" in root else None
        if array is None or tuple(array.shape) != _VOLUME_FULL:
            raise ValueError(f"image shape is not the pinned (100,64,256,256): {sample_id}")
        metadata = dict(getattr(root, "attrs", {}))
        multiscales = metadata.get("multiscales")
        if isinstance(multiscales, list) and multiscales:
            datasets = multiscales[0].get("datasets") if isinstance(multiscales[0], Mapping) else None
            transforms = (
                datasets[0].get("coordinateTransformations")
                if isinstance(datasets, list) and datasets
                else None
            )
            scale = transforms[0].get("scale") if isinstance(transforms, list) and transforms else None
            if scale != [1.0, 1.625, 0.40625, 0.40625]:
                raise ValueError(f"image scale is not the pinned value: {sample_id}")


def _prepare_image_data(
    image_root: Path,
    sample_ids: Sequence[str],
    max_frames: int | None,
    scratch_root: Path,
) -> Path:
    if max_frames is None:
        return Path(image_root)
    import zarr

    data_root = scratch_root / "input"
    data_root.mkdir()
    for sample_id in sample_ids:
        source = zarr.open(str(Path(image_root) / f"{sample_id}.zarr"), mode="r")
        source_array = source if hasattr(source, "shape") else source["0"]
        destination = zarr.open_group(str(data_root / f"{sample_id}.zarr"), mode="w")
        destination.attrs.update(dict(getattr(source, "attrs", {})))
        destination_array = destination.create_dataset(
            "0",
            shape=(max_frames, *tuple(source_array.shape[1:])),
            chunks=source_array.chunks,
            dtype=source_array.dtype,
        )
        destination_array.attrs.update(dict(source_array.attrs))
        destination_array[:] = source_array[:max_frames]
    return data_root


def _load_source_api(stage: Any) -> _SourceApi:
    try:
        config_module = importlib.import_module("biohub_pipeline.config")
        inference_module = importlib.import_module("biohub_pipeline.inference")
        submission_module = importlib.import_module("biohub_pipeline.submission")
    except ModuleNotFoundError:
        source_root = os.fspath(stage.source_root)
        source_src = os.path.join(source_root, "src")
        import sys

        sys.path.insert(0, source_src)
        try:
            config_module = importlib.import_module("biohub_pipeline.config")
            inference_module = importlib.import_module("biohub_pipeline.inference")
            submission_module = importlib.import_module("biohub_pipeline.submission")
        finally:
            sys.path.remove(source_src)
    return _SourceApi(
        config_module.load_config,
        inference_module.apply_spatial_d4_patch,
        inference_module.build_predict_command,
        submission_module.write_submission_from_geff,
    )


def _write_scratch_repo(stage: Any, root: Path) -> tuple[Path, Path, Path, Path]:
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
    primary.write_bytes(stage.primary_checkpoint_path.read_bytes())
    secondary.write_bytes(stage.secondary_checkpoint_path.read_bytes())
    config = root / "recipe_c.yaml"
    config.write_bytes(stage.staged_config.read_bytes())
    return repo, predictor, primary, config


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
    index = 2
    seen: set[str] = set()
    while index < len(values):
        flag = values[index]
        if flag in replacements:
            if index + 1 >= len(values):
                raise ValueError(f"command flag is missing a value: {flag}")
            values[index + 1] = replacements[flag]
            seen.add(flag)
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


def _copy_raw_prediction(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"raw prediction is not a regular GEFF directory: {source.name}")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    temporary = destination.parent / f".{destination.name}.tmp"
    shutil.copytree(source, temporary, symlinks=False)
    try:
        os.rename(temporary, destination)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _role_path(path: Path, output_root: Path) -> str:
    return path.relative_to(output_root).as_posix()


def _safe_failure_message(exc: BaseException) -> str:
    # A type-only message is deliberate: failure receipts must not become a
    # side channel for credentials, host paths, or GT paths.
    return type(exc).__name__


def _write_failed(output_root: Path, lock_id: str, phase: str, exc: BaseException, command: Sequence[str]) -> None:
    if not output_root.is_dir():
        return
    for child in list(output_root.iterdir()):
        if child.name == "FAILED.json":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        elif child.is_symlink() or not child.is_dir():
            child.unlink()
    payload = {
        "status": "FAILED",
        "selection_lock_id": lock_id,
        "phase": phase,
        "error_type": _safe_failure_message(exc),
        "command_sha256": _sha256(json.dumps(list(command), separators=(",", ":")).encode()),
        "reusable": False,
    }
    temporary = output_root / ".FAILED.json.tmp"
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, output_root / "FAILED.json")


def _resolve_device(candidates: Sequence[str]) -> str:
    try:
        import torch

        if "cuda" in candidates and bool(torch.cuda.is_available()):
            return "cuda"
        if "mps" in candidates and bool(getattr(torch.backends, "mps", None)) and torch.backends.mps.is_available():
            return "mps"
    except (ImportError, AttributeError, RuntimeError):
        pass
    return "cpu" if "cpu" in candidates else str(candidates[-1])


def _write_ready_receipt(output_root: Path, receipt: InferenceReceipt) -> None:
    target = output_root / "receipt.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    temporary = output_root / ".receipt.json.tmp"
    temporary.write_text(json.dumps(asdict(receipt), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


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
    lock = _validate_selection_lock(selection_lock)
    chosen_samples = _sample_selection(sample_ids, lock, max_frames)
    _assert_stage_lock_identity(runtime_stage, lock)
    output_root = Path(output_root)
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(f"output root must be fresh: {output_root.name}")
    output_root.mkdir(parents=True, exist_ok=False)
    phase = "preflight"
    command: tuple[str, ...] = ()
    try:
        _preflight_images(Path(image_root), chosen_samples, max_frames)
        with runtime_stage:
            source_api = _load_source_api(runtime_stage)
            phase = "patch"
            with tempfile.TemporaryDirectory(prefix="recipe-c-") as scratch_name:
                scratch = Path(scratch_name)
                scratch_repo, scratch_predictor, scratch_primary, scratch_config = _write_scratch_repo(
                    runtime_stage, scratch
                )
                data_root = _prepare_image_data(Path(image_root), chosen_samples, max_frames, scratch)
                patched = source_api.apply_spatial_d4_patch(scratch_repo, "scripts/predict_unet_transformer.py")
                if patched is not True:
                    raise RuntimeError("spatial D4 patch did not report a fresh postimage")
                py_compile.compile(str(scratch_predictor), doraise=True)
                phase = "builder"
                config = source_api.load_config(scratch_config)
                raw_command, splits_path = source_api.build_predict_command(
                    config,
                    data_root,
                    scratch_repo,
                    scratch_primary,
                    list(chosen_samples),
                )
                if not isinstance(raw_command, (list, tuple)) or not isinstance(splits_path, Path):
                    raise TypeError("source builder returned an invalid command or splits path")
                predictor_payload = scratch_predictor.read_bytes()
                splits_payload = splits_path.read_bytes()
                publish_predictor = _publish_derived(runtime_stage, _PREDICTOR_DERIVED, predictor_payload)
                publish_splits = _publish_derived(runtime_stage, _SPLITS_DERIVED, splits_payload)
                phase = "subprocess"
                environment = {**os.environ, "PYTHONPATH": "src"}
                repo_cwd = os.fspath(runtime_stage.repo_dir)
                execution_data_role = os.path.relpath(str(data_root), start=repo_cwd)
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
                subprocess.run(
                    list(execution_command),
                    cwd=repo_cwd,
                    pass_fds=(runtime_stage.repo_fd,),
                    env=environment,
                    shell=False,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                username = os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
                stage_predictions = Path(repo_cwd) / "predictions" / username / "unet_transformer" / "split_0"
                raw_sources = sorted(stage_predictions.glob("*.geff"))
                if {path.stem for path in raw_sources} != set(chosen_samples) or len(raw_sources) != len(
                    chosen_samples
                ):
                    raise ValueError("raw predictor output does not exactly cover selected samples")
                phase = "raw_persist"
                raw_root = output_root / "raw"
                raw_root.mkdir()
                raw_geffs: dict[str, Path] = {}
                raw_counts: dict[str, object] = {}
                for source in raw_sources:
                    destination = raw_root / source.name
                    _copy_raw_prediction(source, destination)
                    counts = validate_prediction_geff(
                        destination,
                        source.stem,
                        expected_volume_shape_tzyx=_VOLUME_SMOKE if max_frames else _VOLUME_FULL,
                    )
                    raw_geffs[source.stem] = destination
                    raw_counts[source.stem] = {
                        **counts,
                        **directory_digest_report(destination),
                    }
                phase = "postprocess"
                csv_path = output_root / "submission.csv"
                source_api.write_submission_from_geff(list(raw_geffs.values()), config, data_root, csv_path)
                from biohub.submission.validator import validate_submission

                report = validate_submission(
                    csv_path,
                    expected_datasets=chosen_samples,
                    volume_shape_tzyx=_VOLUME_SMOKE if max_frames else _VOLUME_FULL,
                    ground_truth_nodes=None,
                    require_divisions=False,
                )
                if not report.ok:
                    raise ValueError("source submission failed target structural validation")
                unexpected = {
                    child.name
                    for child in output_root.iterdir()
                    if child.name not in {"raw", "submission.csv"}
                }
                if unexpected:
                    raise ValueError("source postprocess produced an unexpected output entry")
                phase = "bridge"
                final_root = output_root / "predictions"
                final_geffs = postprocessed_csv_to_geffs(
                    csv_path,
                    final_root,
                    sample_ids=chosen_samples,
                    provenance={"source_commit": runtime_stage.receipt.get("source_commit", "")},
                )
                final_counts: dict[str, object] = {}
                for sample_id, final in final_geffs.items():
                    counts = validate_prediction_geff(
                        final,
                        sample_id,
                        expected_volume_shape_tzyx=_VOLUME_SMOKE if max_frames else _VOLUME_FULL,
                    )
                    final_counts[sample_id] = {**counts, **directory_digest_report(final)}
                phase = "manifest"
                manifests: dict[str, Path] = {}
                for sample_id, final in final_geffs.items():
                    manifest = write_prediction_manifest(
                        final,
                        selection_lock_id=str(lock["selection_lock_id"]),
                        provenance={
                            "source_commit": runtime_stage.receipt.get("source_commit", ""),
                            "config_sha256": runtime_stage.receipt.get("config_sha256", ""),
                            "predictor_sha256": runtime_stage.predictor_sha256_postimage,
                            "device": _resolve_device(runtime_stage.device_candidates),
                        },
                    )
                    mint_prediction_token(final)
                    manifests[sample_id] = manifest
                finished = datetime.now(UTC).isoformat()
                receipt = InferenceReceipt(
                    status="READY",
                    selection_lock_id=str(lock["selection_lock_id"]),
                    sample_ids=tuple(chosen_samples),
                    mode="smoke_2frame" if max_frames else "full",
                    max_frames=max_frames,
                    command=command,
                    cwd_role="repo",
                    pythonpath="src",
                    resolved_device=_resolve_device(runtime_stage.device_candidates),
                    device_candidates=tuple(runtime_stage.device_candidates),
                    patch_flags={"spatial_d4": True, "builder": True},
                    raw_geffs={sample: _role_path(path, output_root) for sample, path in raw_geffs.items()},
                    postprocessed_csv=_role_path(csv_path, output_root),
                    final_geffs={sample: _role_path(path, output_root) for sample, path in final_geffs.items()},
                    manifests={sample: _role_path(path, output_root) for sample, path in manifests.items()},
                    counts={
                        "raw": raw_counts,
                        "final": final_counts,
                        "publish": {"predictor": publish_predictor, "splits": publish_splits},
                    },
                    started_at=started,
                    finished_at=finished,
                    failure=None,
                )
                _write_ready_receipt(output_root, receipt)
                return receipt
    except BaseException as exc:
        try:
            _write_failed(output_root, str(lock["selection_lock_id"]), phase, exc, command)
        except BaseException as cleanup_error:
            exc.add_note(f"failed receipt cleanup: {type(cleanup_error).__name__}")
        raise


__all__ = ["InferenceReceipt", "run_recipe_c_inference"]
