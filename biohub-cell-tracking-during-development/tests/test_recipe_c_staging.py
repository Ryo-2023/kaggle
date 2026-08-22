"""TDD contract tests for immutable Recipe C runtime staging."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

import biohub.recipe_c.staging as staging_module
from biohub.recipe_c.device_patch import DEVICE_POSTIMAGE, apply_device_fallback_patch
from biohub.recipe_c.source import RECIPE_C_SOURCE
from biohub.recipe_c.staging import RuntimeStage, stage_recipe_c_runtime

DEVICE_PREIMAGE = 'device = torch.device("cuda" if torch.cuda.is_available() else "cpu")\n'


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", path.readlink().as_posix()))
        elif path.is_dir():
            entries.append((relative, "directory", ""))
        else:
            entries.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(entries)


@pytest.fixture
def fake_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, dict[str, object]]:
    source = tmp_path / "source"
    config = source / RECIPE_C_SOURCE.config_relative_path
    config.parent.mkdir(parents=True)
    config.write_bytes(b"inference:\n  detection_threshold: 0.96875\n")

    primary = tmp_path / "primary"
    predictor = primary / RECIPE_C_SOURCE.predictor_relative_path
    predictor.parent.mkdir(parents=True)
    predictor.write_text("#!/usr/bin/env python3\n" + DEVICE_PREIMAGE, encoding="utf-8")
    primary_checkpoint = primary / RECIPE_C_SOURCE.primary_checkpoint_relative_path
    primary_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    primary_checkpoint.write_bytes(b"primary-checkpoint")
    (primary / "repo" / "README.txt").write_text("pristine\n", encoding="utf-8")

    secondary = tmp_path / "secondary"
    secondary_checkpoint = secondary / RECIPE_C_SOURCE.secondary_checkpoint_relative_path
    secondary_checkpoint.parent.mkdir(parents=True)
    secondary_checkpoint.write_bytes(b"secondary-checkpoint")

    source_receipt = {
        "source_commit": "source-commit",
        "config_relative_path": RECIPE_C_SOURCE.config_relative_path,
        "config_sha256": _sha256(config),
    }
    support_receipt = {
        "predictor_relative_path": RECIPE_C_SOURCE.predictor_relative_path,
        "predictor_sha256": _sha256(predictor),
        "primary_checkpoint_relative_path": RECIPE_C_SOURCE.primary_checkpoint_relative_path,
        "primary_checkpoint_sha256": _sha256(primary_checkpoint),
        "secondary_checkpoint_relative_path": RECIPE_C_SOURCE.secondary_checkpoint_relative_path,
        "secondary_checkpoint_sha256": _sha256(secondary_checkpoint),
        "secondary_staging_relative_path": RECIPE_C_SOURCE.secondary_staging_relative_path,
    }
    monkeypatch.setattr(staging_module, "validate_source_checkout", lambda _root: source_receipt)
    monkeypatch.setattr(staging_module, "validate_support_artifacts", lambda *_roots: support_receipt)
    monkeypatch.setattr(staging_module, "validate_selection_lock_payload", lambda payload: dict(payload))
    lock = {
        "selection_lock_id": "lock-123",
        "source_commit": "source-commit",
        "source_config_sha256": _sha256(config),
        "config_sha256": _sha256(config),
        "predictor_sha256": _sha256(predictor),
        "primary_checkpoint_sha256": _sha256(primary_checkpoint),
        "secondary_checkpoint_sha256": _sha256(secondary_checkpoint),
        "secondary_staging_relative_path": RECIPE_C_SOURCE.secondary_staging_relative_path,
    }
    return source, primary, secondary, lock


def test_staging_never_mutates_source_or_support(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    before = (_tree_digest(source), _tree_digest(primary), _tree_digest(secondary))

    stage = stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)

    assert isinstance(stage, RuntimeStage)
    assert (_tree_digest(source), _tree_digest(primary), _tree_digest(secondary)) == before
    assert stage.selection_lock_id == "lock-123"
    assert stage.predictor_sha256_before != stage.predictor_sha256_after
    assert stage.repo_dir.is_dir()
    assert stage.predictor_path == stage.repo_dir / "scripts/predict_unet_transformer.py"
    assert stage.predictor_path.is_file()
    assert _sha256(stage.predictor_path) == stage.predictor_sha256_after
    assert stage.staged_config.is_file()
    assert stage.primary_checkpoint_path.is_symlink()
    assert stage.secondary_checkpoint_path.is_symlink()
    assert stage.primary_checkpoint_path.resolve() == (
        primary / RECIPE_C_SOURCE.primary_checkpoint_relative_path
    ).resolve()
    assert stage.secondary_checkpoint_path.resolve() == (
        secondary / RECIPE_C_SOURCE.secondary_checkpoint_relative_path
    ).resolve()
    assert json.dumps(stage.receipt, sort_keys=True).find(str(tmp_path)) == -1


def test_device_patch_contains_cuda_mps_cpu_order(tmp_path: Path) -> None:
    path = tmp_path / "predict.py"
    path.write_text(DEVICE_PREIMAGE, encoding="utf-8")

    assert apply_device_fallback_patch(path) is True
    text = path.read_text(encoding="utf-8")
    assert text.index("cuda") < text.index("mps") < text.index("cpu")
    patched = path.read_bytes()
    assert apply_device_fallback_patch(path) is False
    assert path.read_bytes() == patched


def test_staging_rejects_existing_or_symlink_destination(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    destination = tmp_path / "stage"
    destination.symlink_to(tmp_path / "missing")

    with pytest.raises((FileExistsError, ValueError), match=r"symlink|exists"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_staging_rejects_existing_destination(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    kind: str,
) -> None:
    source, primary, secondary, lock = fake_inputs
    destination = tmp_path / "stage"
    if kind == "file":
        destination.write_text("sentinel", encoding="utf-8")
    else:
        destination.mkdir()
    with pytest.raises(FileExistsError, match="exists"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)


def test_staging_rejects_symlinked_destination_parent(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    parent = tmp_path / "parent"
    parent_target = tmp_path / "parent-target"
    parent_target.mkdir()
    parent.symlink_to(parent_target, target_is_directory=True)
    with pytest.raises(ValueError, match=r"symlink|parent"):
        stage_recipe_c_runtime(source, primary, secondary, parent / "stage", lock)


def test_staging_rejects_destination_inside_immutable_artifact(
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    with pytest.raises(ValueError, match=r"immutable|artifact"):
        stage_recipe_c_runtime(source, primary, secondary, source / "stage", lock)


@pytest.mark.parametrize("target_kind", ["external", "dangling"])
def test_staging_rejects_external_or_dangling_artifact_symlink(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    target_kind: str,
) -> None:
    source, primary, secondary, lock = fake_inputs
    link = source / "unsafe-link"
    if target_kind == "external":
        outside = tmp_path / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link.symlink_to(outside)
    else:
        link.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)


def test_staging_marks_partial_failure_nonreusable(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs

    def fail_patch(_path: Path) -> bool:
        raise RuntimeError("synthetic patch failure")

    monkeypatch.setattr(staging_module, "apply_device_fallback_patch", fail_patch)
    destination = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="synthetic"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)
    failed = json.loads((destination / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["reusable"] is False
    assert str(tmp_path) not in json.dumps(failed)
    with pytest.raises(FileExistsError, match="exists"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)


def test_staging_rejects_primary_secondary_same_inode(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    primary_checkpoint = primary / RECIPE_C_SOURCE.primary_checkpoint_relative_path
    secondary_checkpoint = secondary / RECIPE_C_SOURCE.secondary_checkpoint_relative_path
    secondary_checkpoint.unlink()
    secondary_checkpoint.hardlink_to(primary_checkpoint)
    with pytest.raises(ValueError, match="distinct"):
        stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)


def test_staging_rejects_selection_lock_identity_mismatch(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    lock["predictor_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="selection lock"):
        stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)


def test_device_patch_rejects_unknown_or_multiple_preimage_without_write(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.py"
    unknown.write_text("unknown preimage", encoding="utf-8")
    before = unknown.read_bytes()
    with pytest.raises(ValueError, match="preimage"):
        apply_device_fallback_patch(unknown)
    assert unknown.read_bytes() == before

    multiple = tmp_path / "multiple.py"
    multiple.write_text(DEVICE_PREIMAGE + DEVICE_PREIMAGE, encoding="utf-8")
    before = multiple.read_bytes()
    with pytest.raises(ValueError, match="preimage"):
        apply_device_fallback_patch(multiple)
    assert multiple.read_bytes() == before


def test_device_patch_rejects_symlink_without_write(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text(DEVICE_PREIMAGE, encoding="utf-8")
    link = tmp_path / "predict.py"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        apply_device_fallback_patch(link)
    assert target.read_text(encoding="utf-8") == DEVICE_PREIMAGE


def test_device_patch_compile_failure_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "invalid.py"
    original = b"if (\n" + DEVICE_PREIMAGE.encode("utf-8") + b"\n"
    path.write_bytes(original)
    with pytest.raises(ValueError, match="compile"):
        apply_device_fallback_patch(path)
    assert path.read_bytes() == original


def test_device_postimage_is_exactly_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "already.py"
    path.write_text(DEVICE_POSTIMAGE + "\n", encoding="utf-8")
    before = path.read_bytes()
    assert apply_device_fallback_patch(path) is False
    assert path.read_bytes() == before


def test_dry_run_cli_is_staging_only() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_biohub_095.py"
    spec = importlib.util.spec_from_file_location("run_biohub_095_task3_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module._build_parser().parse_args(
        [
            "dry-run",
            "--source", "source",
            "--primary-support", "primary",
            "--secondary-support", "secondary",
            "--selection-lock", "lock.json",
            "--destination", "stage",
        ],
    )
    assert args.command == "dry-run"
    assert args.handler is module._dry_run
