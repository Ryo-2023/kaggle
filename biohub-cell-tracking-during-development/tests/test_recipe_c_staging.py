"""TDD contract tests for immutable Recipe C runtime staging."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

import biohub.recipe_c.staging as staging_module
from biohub.recipe_c.device_patch import (
    DEVICE_POSTIMAGE,
    prepare_device_fallback_patch,
    publish_device_fallback_patch_at,
)
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
    assert not stage.primary_checkpoint_path.is_symlink()
    assert not stage.secondary_checkpoint_path.is_symlink()
    assert stage.primary_checkpoint_path.read_bytes() == b"primary-checkpoint"
    assert stage.secondary_checkpoint_path.read_bytes() == b"secondary-checkpoint"
    assert json.dumps(stage.receipt, sort_keys=True).find(str(tmp_path)) == -1
    stage.close()


def test_device_patch_contains_cuda_mps_cpu_order() -> None:
    source = DEVICE_PREIMAGE.encode("utf-8")
    patched, changed = prepare_device_fallback_patch(source)

    assert changed is True
    text = patched.decode("utf-8")
    assert text.index("cuda") < text.index("mps") < text.index("cpu")
    idempotent, changed = prepare_device_fallback_patch(patched)
    assert changed is False
    assert idempotent == patched


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

    def fail_patch(
        _source_root_descriptor: int,
        _source_relative_path: Path,
        _destination_root_descriptor: int,
        _destination_relative_path: Path,
    ) -> bool:
        raise RuntimeError("synthetic patch failure")

    monkeypatch.setattr(staging_module, "publish_device_fallback_patch_at", fail_patch)
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


def test_device_patch_rejects_unknown_or_multiple_preimage_without_write() -> None:
    unknown = b"unknown preimage"
    with pytest.raises(ValueError, match="preimage"):
        prepare_device_fallback_patch(unknown)

    multiple = (DEVICE_PREIMAGE + DEVICE_PREIMAGE).encode("utf-8")
    with pytest.raises(ValueError, match="preimage"):
        prepare_device_fallback_patch(multiple)


def test_device_patch_rejects_symlink_without_write(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    link = source_root / "predict.py"
    source_root.mkdir()
    target = tmp_path / "target.py"
    target.write_text(DEVICE_PREIMAGE, encoding="utf-8")
    link.symlink_to(target)
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    with pytest.raises(ValueError, match="symlink"):
        publish_device_fallback_patch_at(source_fd, Path("predict.py"), destination_fd, Path("predict.py"))
    os.close(source_fd)
    os.close(destination_fd)
    assert target.read_text(encoding="utf-8") == DEVICE_PREIMAGE


def test_device_patch_compile_failure_does_not_write(tmp_path: Path) -> None:
    original = b"if (\n" + DEVICE_PREIMAGE.encode("utf-8") + b"\n"
    with pytest.raises(ValueError, match="compile"):
        prepare_device_fallback_patch(original)


def test_device_postimage_is_exactly_idempotent() -> None:
    before = (DEVICE_POSTIMAGE + "\n").encode("utf-8")
    patched, changed = prepare_device_fallback_patch(before)
    assert changed is False
    assert patched == before


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


def test_claimed_stage_fd_writes_original_directory_after_parent_replacement(tmp_path: Path) -> None:
    destination = tmp_path / "parent" / "stage"
    destination.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    stage_root, parent_fd, stage_fd = staging_module._claim_destination(destination)
    try:
        destination.rename(tmp_path / "moved-stage")
        destination.symlink_to(outside, target_is_directory=True)
        staging_module._write_json_exclusive_at(
            stage_fd,
            "receipt.json",
            {"status": "READY"},
        )
        assert (stage_root / "receipt.json").is_file() is False
        assert (outside / "receipt.json").exists() is False
        assert (tmp_path / "moved-stage" / "receipt.json").read_text(encoding="utf-8").strip()
    finally:
        os.close(stage_fd)
        os.close(parent_fd)


def test_source_snapshot_fd_does_not_follow_replaced_root_symlink(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    root_fd = staging_module._open_directory_path(root, "source")
    try:
        root.rename(tmp_path / "moved-source")
        root.symlink_to(outside, target_is_directory=True)
        snapshot = staging_module._snapshot_tree_fd(root_fd, reject_symlinks=True)
        assert "outside.txt" not in snapshot
        assert "inside.txt" in snapshot
    finally:
        os.close(root_fd)


def test_device_patch_fd_does_not_replace_external_file_after_ancestor_swap(tmp_path: Path) -> None:
    controlled = tmp_path / "controlled" / "nested"
    controlled.mkdir(parents=True)
    predictor = controlled / "predict.py"
    predictor.write_text(DEVICE_PREIMAGE, encoding="utf-8")
    original = predictor.read_bytes()
    outside = tmp_path / "outside" / "nested"
    outside.mkdir(parents=True)
    outside_predictor = outside / "predict.py"
    outside_predictor.hardlink_to(predictor)
    destination = tmp_path / "destination" / "nested"
    destination.mkdir(parents=True)
    root_fd = staging_module._open_directory_path(tmp_path / "controlled", "controlled")
    destination_fd = staging_module._open_directory_path(tmp_path / "destination", "destination")
    try:
        (tmp_path / "controlled").rename(tmp_path / "controlled-moved")
        (tmp_path / "controlled").symlink_to(tmp_path / "outside", target_is_directory=True)
        published = publish_device_fallback_patch_at(
            root_fd,
            Path("nested/predict.py"),
            destination_fd,
            Path("nested/predict.py"),
        )
        assert published.changed is True
        os.close(published.descriptor)
        assert outside_predictor.read_bytes() == original
        assert b"mps" in (destination / "predict.py").read_bytes()
    finally:
        os.close(root_fd)
        os.close(destination_fd)


def test_device_fresh_publish_is_atomic_and_no_clobbering(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_predictor = source_root / "repo/scripts/predict.py"
    source_predictor.parent.mkdir(parents=True)
    source_predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    (destination_root / "repo/scripts").mkdir(parents=True)
    destination_predictor = destination_root / "repo/scripts/predict.py"
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        published = publish_device_fallback_patch_at(
            source_fd,
            Path("repo/scripts/predict.py"),
            destination_fd,
            Path("repo/scripts/predict.py"),
        )
        assert published.changed is True
        os.close(published.descriptor)
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert source_predictor.read_bytes() == DEVICE_PREIMAGE.encode("utf-8")
    assert b"mps" in destination_predictor.read_bytes()
    assert not list(destination_predictor.parent.glob(".predict.py.recipe-c-device-patch.*"))


@pytest.mark.parametrize("kind", ["file", "symlink", "directory"])
def test_device_fresh_publish_preserves_existing_final_entry(
    tmp_path: Path,
    kind: str,
) -> None:
    source_root = tmp_path / "source"
    source_predictor = source_root / "repo/scripts/predict.py"
    source_predictor.parent.mkdir(parents=True)
    source_predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_parent = destination_root / "repo/scripts"
    destination_parent.mkdir(parents=True)
    destination_predictor = destination_parent / "predict.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside")
    if kind == "file":
        destination_predictor.write_bytes(b"attacker")
    elif kind == "symlink":
        destination_predictor.symlink_to(outside)
    else:
        destination_predictor.mkdir()
        (destination_predictor / "sentinel").write_bytes(b"attacker")
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        with pytest.raises(ValueError, match="final entry already exists"):
            publish_device_fallback_patch_at(
                source_fd,
                Path("repo/scripts/predict.py"),
                destination_fd,
                Path("repo/scripts/predict.py"),
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    if kind == "file":
        assert destination_predictor.read_bytes() == b"attacker"
    elif kind == "symlink":
        assert destination_predictor.is_symlink()
        assert destination_predictor.resolve() == outside
    else:
        assert (destination_predictor / "sentinel").read_bytes() == b"attacker"
    assert not list(destination_parent.glob(".predict.py.recipe-c-device-patch.*"))


def test_device_fresh_publish_detects_final_entry_race_without_clobber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import biohub.recipe_c.device_patch as device_patch_module

    source_root = tmp_path / "source"
    source_predictor = source_root / "repo/scripts/predict.py"
    source_predictor.parent.mkdir(parents=True)
    source_predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_parent = destination_root / "repo/scripts"
    destination_parent.mkdir(parents=True)
    destination_predictor = destination_parent / "predict.py"
    original_link = device_patch_module._link_anonymous

    def create_attacker_entry_before_link(
        descriptor: int,
        parent_descriptor: int,
        name: str,
    ) -> None:
        destination_predictor.write_bytes(b"attacker")
        original_link(descriptor, parent_descriptor, name)

    monkeypatch.setattr(device_patch_module, "_link_anonymous", create_attacker_entry_before_link)
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        with pytest.raises(ValueError, match="final entry already exists"):
            publish_device_fallback_patch_at(
                source_fd,
                Path("repo/scripts/predict.py"),
                destination_fd,
                Path("repo/scripts/predict.py"),
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert destination_predictor.read_bytes() == b"attacker"
    assert not list(destination_parent.glob(".predict.py.recipe-c-device-patch.*"))


def test_device_fresh_publish_cleans_owned_temp_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import biohub.recipe_c.device_patch as device_patch_module

    source_root = tmp_path / "source"
    source_predictor = source_root / "repo/scripts/predict.py"
    source_predictor.parent.mkdir(parents=True)
    source_predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_parent = destination_root / "repo/scripts"
    destination_parent.mkdir(parents=True)
    original_write = device_patch_module.os.write

    def fail_temp_write(_descriptor: int, _payload: bytes) -> int:
        raise OSError("synthetic temp write failure")

    monkeypatch.setattr(device_patch_module.os, "write", fail_temp_write)
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        with pytest.raises(OSError, match="temp write"):
            publish_device_fallback_patch_at(
                source_fd,
                Path("repo/scripts/predict.py"),
                destination_fd,
                Path("repo/scripts/predict.py"),
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
        monkeypatch.setattr(device_patch_module.os, "write", original_write)
    assert not list(destination_parent.glob(".predict.py.recipe-c-device-patch.*"))


def test_device_publish_does_not_link_attacker_replaced_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import biohub.recipe_c.device_patch as device_patch_module

    source_root = tmp_path / "source"
    source_predictor = source_root / "predict.py"
    source_root.mkdir()
    source_predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    destination_predictor = destination_root / "predict.py"
    original_link = device_patch_module._link_anonymous
    triggered = False

    def replace_temp_before_link(descriptor: int, parent: int, name: str) -> object:
        nonlocal triggered
        temporary_entries = sorted(destination_root.glob(".recipe-c-anonymous.*"))
        assert len(temporary_entries) == 1
        temporary_entries[0].unlink()
        temporary_entries[0].write_bytes(b"attacker-temp")
        triggered = True
        return original_link(descriptor, parent, name)

    monkeypatch.setattr(device_patch_module, "_O_TMPFILE", 0)
    monkeypatch.setattr(device_patch_module, "_link_anonymous", replace_temp_before_link)
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        with pytest.raises(OSError):
            publish_device_fallback_patch_at(
                source_fd,
                Path("predict.py"),
                destination_fd,
                Path("predict.py"),
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert triggered
    assert not destination_predictor.exists()
    temporary_entries = sorted(destination_root.glob(".recipe-c-anonymous.*"))
    assert len(temporary_entries) == 1
    assert temporary_entries[0].read_bytes() == b"attacker-temp"
    temporary_entries[0].unlink()


def test_device_named_temp_replacement_cannot_publish_attacker_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import biohub.recipe_c.device_patch as device_patch_module

    source_root = tmp_path / "source"
    source_predictor = source_root / "predict.py"
    source_root.mkdir()
    source_predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    destination_predictor = destination_root / "predict.py"
    original_link = device_patch_module._link_anonymous
    triggered = False
    replaced: list[Path] = []

    def replace_named_temp(
        descriptor: int,
        parent_descriptor: int,
        name: str,
    ) -> None:
        nonlocal triggered
        temporary_entries = sorted(destination_root.glob(".recipe-c-anonymous.*"))
        assert len(temporary_entries) == 1
        temporary = temporary_entries[0]
        temporary.unlink()
        temporary.write_bytes(b"attacker-temp")
        replaced.append(temporary)
        triggered = True
        original_link(descriptor, parent_descriptor, name)

    monkeypatch.setattr(device_patch_module, "_O_TMPFILE", 0)
    monkeypatch.setattr(device_patch_module, "_link_anonymous", replace_named_temp)
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        with pytest.raises(OSError):
            publish_device_fallback_patch_at(
                source_fd,
                Path("predict.py"),
                destination_fd,
                Path("predict.py"),
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert triggered
    assert not destination_predictor.exists()
    assert replaced and replaced[0].read_bytes() == b"attacker-temp"
    replaced[0].unlink()


def test_staging_rejects_predictor_replaced_after_publish_return(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs
    original_publish = staging_module.publish_device_fallback_patch_at
    published_returned = False

    def publish_then_replace(
        source_root_descriptor: int,
        source_relative_path: Path,
        destination_root_descriptor: int,
        destination_relative_path: Path,
    ) -> object:
        nonlocal published_returned
        published = original_publish(
            source_root_descriptor,
            source_relative_path,
            destination_root_descriptor,
            destination_relative_path,
        )
        parent, name = staging_module._open_relative_parent(
            destination_root_descriptor,
            destination_relative_path,
            "attacker predictor",
        )
        try:
            os.unlink(name, dir_fd=parent)
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            try:
                os.write(descriptor, b"attacker = 1\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            staging_module._fsync_directory(parent)
        finally:
            os.close(parent)
        published_returned = True
        return published

    monkeypatch.setattr(staging_module, "publish_device_fallback_patch_at", publish_then_replace)
    destination = tmp_path / "stage"
    with pytest.raises(ValueError, match="published predictor"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)
    failed = json.loads((destination / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["reusable"] is False
    assert not (destination / "receipt.json").exists()
    assert published_returned


def test_staging_checkpoint_copy_is_not_escaped_by_support_root_swap(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_checkpoint = outside / "model.pth"
    outside_checkpoint.write_bytes(b"attacker-checkpoint")
    original_copy = staging_module._copy_regular_file_at
    swapped = False

    def copy_then_swap(
        source_parent_descriptor: int,
        source_name: str,
        destination_parent_descriptor: int,
        destination_name: str,
        label: str,
    ) -> None:
        nonlocal swapped
        original_copy(
            source_parent_descriptor,
            source_name,
            destination_parent_descriptor,
            destination_name,
            label,
        )
        if label == "primary checkpoint" and not swapped:
            swapped = True
            moved = primary.with_name("primary-moved")
            primary.rename(moved)
            primary.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(staging_module, "_copy_regular_file_at", copy_then_swap)
    stage = stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)
    try:
        assert stage.primary_checkpoint_path.is_file()
        assert not stage.primary_checkpoint_path.is_symlink()
        assert stage.primary_checkpoint_path.read_bytes() == b"primary-checkpoint"
        assert _sha256(stage.primary_checkpoint_path) == lock["primary_checkpoint_sha256"]
        assert swapped
    finally:
        stage.close()


def test_runtime_stage_consumers_remain_fd_backed_after_final_claim_swap(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs
    destination = tmp_path / "stage"
    outside = tmp_path / "outside"
    (outside / "repo/scripts").mkdir(parents=True)
    (outside / "repo/scripts/predict_unet_transformer.py").write_text(
        "attacker = 1\n",
        encoding="utf-8",
    )
    original_assert = staging_module._assert_claimed_destination
    calls = 0

    def assert_then_swap(parent_descriptor: int, name: str, stage_descriptor: int) -> None:
        nonlocal calls
        original_assert(parent_descriptor, name, stage_descriptor)
        calls += 1
        if calls == 3:
            moved = tmp_path / "moved-stage"
            destination.rename(moved)
            destination.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(staging_module, "_assert_claimed_destination", assert_then_swap)
    stage = stage_recipe_c_runtime(source, primary, secondary, destination, lock)
    try:
        assert calls >= 3
        assert not stage.stage_root.is_symlink()
        assert b"mps" in stage.predictor_path.read_bytes()
        assert b"attacker" not in stage.predictor_path.read_bytes()
    finally:
        stage.close()


def test_directory_fsync_failure_is_not_treated_as_success(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic directory fsync failure")

    monkeypatch.setattr(staging_module, "_fsync_directory", fail_fsync, raising=False)
    with pytest.raises(OSError, match="directory fsync"):
        stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)


def test_cached_fd_backed_path_rejects_fd_reuse_after_stage_close(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    stage = stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)
    cached_payload = stage.stage_root / "payload.txt"
    reused_descriptor = cached_payload.root_descriptor
    stage.close()

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.txt").write_bytes(b"attacker")
    held: list[int] = []
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        while True:
            descriptor = os.open(outside, flags)
            held.append(descriptor)
            if descriptor == reused_descriptor:
                break
        with pytest.raises(ValueError, match="runtime stage is closed"):
            cached_payload.read_bytes()
    finally:
        for descriptor in reversed(held):
            os.close(descriptor)


def test_all_cached_fd_backed_views_share_stage_close_lease(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
) -> None:
    source, primary, secondary, lock = fake_inputs
    stage = stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)
    cached_views = (
        stage.stage_root,
        stage.repo_dir,
        stage.source_root,
        stage.staged_config,
        stage.predictor_path,
        stage.primary_checkpoint_path,
        stage.secondary_checkpoint_path,
        stage.receipt_path,
    )
    stage.close()

    for view in cached_views:
        with pytest.raises(ValueError, match="runtime stage is closed"):
            view.exists()


def test_staging_does_not_leave_ready_receipt_after_final_validation_failure(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs
    original_assert = staging_module._assert_published_predictor_at
    calls = 0

    def fail_after_receipt_would_have_been_written(
        root_descriptor: int,
        relative_path: Path,
        published: object,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("synthetic final validation failure")
        original_assert(root_descriptor, relative_path, published)

    monkeypatch.setattr(
        staging_module,
        "_assert_published_predictor_at",
        fail_after_receipt_would_have_been_written,
    )
    destination = tmp_path / "stage"
    with pytest.raises(RuntimeError, match="final validation"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)

    assert calls == 3
    assert not (destination / "receipt.json").exists()
    failed = json.loads((destination / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["reusable"] is False


def test_device_patch_closes_temp_fd_when_cleanup_raises_without_masking_publish_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import biohub.recipe_c.device_patch as device_patch_module

    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "predict.py").write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    original_open = device_patch_module._open_anonymous_temp
    opened_descriptor: int | None = None

    def capture_open(parent_descriptor: int) -> tuple[int, str | None, tuple[int, int]]:
        nonlocal opened_descriptor
        result = original_open(parent_descriptor)
        opened_descriptor = result[0]
        return result

    def fail_link(_descriptor: int, _parent_descriptor: int, _name: str) -> None:
        raise OSError("synthetic publish failure")

    def fail_cleanup(_parent_descriptor: int, _name: str, _owner: tuple[int, int]) -> bool:
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(device_patch_module, "_O_TMPFILE", 0)
    monkeypatch.setattr(device_patch_module, "_open_anonymous_temp", capture_open)
    monkeypatch.setattr(device_patch_module, "_link_anonymous", fail_link)
    monkeypatch.setattr(device_patch_module, "_cleanup_owned_temp", fail_cleanup)
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    try:
        with pytest.raises(OSError, match="publish failure"):
            publish_device_fallback_patch_at(
                source_fd,
                Path("predict.py"),
                destination_fd,
                Path("predict.py"),
            )
        assert opened_descriptor is not None
        with pytest.raises(OSError):
            os.fstat(opened_descriptor)
        assert list(destination_root.glob(".recipe-c-anonymous.*"))
    finally:
        os.close(source_fd)
        os.close(destination_fd)


def _assert_failed_only(destination: Path) -> None:
    assert not (destination / "receipt.json").exists()
    failed = json.loads((destination / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["reusable"] is False


def test_receipt_partial_write_failure_cleans_owned_entry_and_marks_failed(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, primary, secondary, lock = fake_inputs
    original_open = staging_module.os.open
    original_write = staging_module.os.write
    receipt_descriptor: int | None = None
    failed = False

    def capture_open(path: str | bytes, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal receipt_descriptor
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == "receipt.json":
            receipt_descriptor = descriptor
        return descriptor

    def fail_receipt_write(descriptor: int, payload: bytes) -> int:
        nonlocal failed
        if descriptor == receipt_descriptor and not failed:
            failed = True
            partial = max(1, len(payload) // 2)
            original_write(descriptor, payload[:partial])
            raise OSError("synthetic receipt write failure")
        return original_write(descriptor, payload)

    monkeypatch.setattr(staging_module.os, "open", capture_open)
    monkeypatch.setattr(staging_module.os, "write", fail_receipt_write)
    destination = tmp_path / "stage"
    with pytest.raises(OSError, match="receipt write failure"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)

    assert failed
    _assert_failed_only(destination)


def test_receipt_identity_is_postwrite_fsynced_file_identity(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    stage_descriptor = staging_module._open_directory_path(stage, "stage")
    try:
        identity = staging_module._write_json_exclusive_at(
            stage_descriptor,
            "receipt.json",
            {"status": "READY"},
        )
        metadata = os.stat(stage / "receipt.json", follow_symlinks=False)
        assert identity == (metadata.st_ino, metadata.st_dev, metadata.st_size)
        assert metadata.st_size > 0
    finally:
        os.close(stage_descriptor)


@pytest.mark.parametrize("failure_point", ["stage", "parent", "cleanup"])
def test_receipt_fsync_failures_never_leave_ready_marker(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    source, primary, secondary, lock = fake_inputs
    original_write = staging_module._write_json_exclusive_at
    original_fsync = staging_module._fsync_directory
    receipt_written = False
    fsyncs_after_receipt = 0
    triggered: set[str] = set()

    def write_json(parent_descriptor: int, name: str, payload: dict[str, object]) -> tuple[int, int, int]:
        nonlocal receipt_written
        result = original_write(parent_descriptor, name, payload)
        if name == "receipt.json":
            receipt_written = True
        return result

    def fail_fsync(descriptor: int) -> None:
        nonlocal fsyncs_after_receipt
        if receipt_written:
            fsyncs_after_receipt += 1
            if failure_point == "stage" and fsyncs_after_receipt == 1:
                triggered.add("stage")
                raise OSError("synthetic stage fsync failure")
            if failure_point == "parent" and fsyncs_after_receipt == 2:
                triggered.add("parent")
                raise OSError("synthetic parent fsync failure")
            if failure_point == "cleanup" and fsyncs_after_receipt == 1:
                triggered.add("publish")
                raise OSError("synthetic receipt cleanup fsync failure")
            if failure_point == "cleanup" and fsyncs_after_receipt == 2:
                triggered.add("cleanup")
                raise OSError("synthetic receipt cleanup fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(staging_module, "_write_json_exclusive_at", write_json)
    monkeypatch.setattr(staging_module, "_fsync_directory", fail_fsync)
    destination = tmp_path / "stage"
    with pytest.raises(OSError, match="fsync"):
        stage_recipe_c_runtime(source, primary, secondary, destination, lock)

    _assert_failed_only(destination)
    assert triggered == ({failure_point} if failure_point != "cleanup" else {"publish", "cleanup"})


@pytest.mark.parametrize(
    "field",
    [
        "predictor_sha256",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_sha256",
        "secondary_staging_relative_path",
    ],
)
def test_staging_rejects_each_support_lock_identity_mismatch(
    tmp_path: Path,
    fake_inputs: tuple[Path, Path, Path, dict[str, object]],
    field: str,
) -> None:
    source, primary, secondary, lock = fake_inputs
    lock[field] = "0" * 64 if field.endswith("sha256") else "weights/other-seed/model.pth"

    with pytest.raises(ValueError, match=field):
        stage_recipe_c_runtime(source, primary, secondary, tmp_path / "stage", lock)


def test_copy_tree_requires_destination_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested/file.txt").write_text("payload", encoding="utf-8")
    destination_parent = tmp_path / "destination"
    destination_parent.mkdir()
    source_fd = staging_module._open_directory_path(source, "source")
    destination_parent_fd = staging_module._open_secure_directory(
        destination_parent,
        create_missing=False,
    )

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic copy directory fsync failure")

    monkeypatch.setattr(staging_module, "_fsync_directory", fail_fsync)
    try:
        with pytest.raises(OSError, match="copy directory fsync"):
            staging_module._copy_tree_at(
                source_fd,
                destination_parent_fd,
                "repo",
                reject_symlinks=True,
            )
    finally:
        os.close(source_fd)
        os.close(destination_parent_fd)


def test_checkpoint_copy_requires_destination_directory_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "checkpoint.pth"
    source.write_bytes(b"checkpoint")
    source_parent_fd = staging_module._open_secure_directory(tmp_path, create_missing=False)
    destination = tmp_path / "destination"
    destination.mkdir()
    destination_parent_fd = staging_module._open_secure_directory(destination, create_missing=False)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("synthetic checkpoint directory fsync failure")

    monkeypatch.setattr(staging_module, "_fsync_directory", fail_fsync)
    try:
        with pytest.raises(OSError, match="checkpoint directory fsync"):
            staging_module._copy_regular_file_at(
                source_parent_fd,
                source.name,
                destination_parent_fd,
                "weights.pth",
                "checkpoint",
            )
    finally:
        os.close(source_parent_fd)
        os.close(destination_parent_fd)


def test_device_patch_detects_changed_owned_stage_entry_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import biohub.recipe_c.device_patch as device_patch_module

    source_root = tmp_path / "source"
    predictor = source_root / "predict.py"
    source_root.mkdir()
    predictor.write_bytes(DEVICE_PREIMAGE.encode("utf-8"))
    destination_root = tmp_path / "destination"
    destination_root.mkdir()
    destination_predictor = destination_root / "predict.py"
    decoy = tmp_path / "decoy.py"
    decoy.write_bytes(b"decoy")
    original_require = device_patch_module._require_regular_at
    calls = 0

    def report_changed_entry(parent_descriptor: int, name: str) -> os.stat_result:
        nonlocal calls
        calls += 1
        metadata = original_require(parent_descriptor, name)
        if calls == 4:
            return os.stat(decoy, follow_symlinks=False)
        return metadata

    monkeypatch.setattr(device_patch_module, "_require_regular_at", report_changed_entry)
    source_fd = staging_module._open_directory_path(source_root, "source")
    destination_fd = staging_module._open_directory_path(destination_root, "destination")
    with pytest.raises(ValueError, match="publication identity changed"):
        publish_device_fallback_patch_at(source_fd, Path("predict.py"), destination_fd, Path("predict.py"))
    os.close(source_fd)
    os.close(destination_fd)
    assert calls >= 4
    assert decoy.read_bytes() == b"decoy"
    assert destination_predictor.is_file()
