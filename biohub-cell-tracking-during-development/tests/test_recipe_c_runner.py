"""TDD contract tests for the GT-free Recipe C runner."""

from __future__ import annotations

import builtins
import csv
import hashlib
import json
import os
import shutil
import subprocess
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
import tracksdata as td

import biohub.recipe_c.runner as runner_module
from biohub.recipe_c.protocol import PANEL_V1
from biohub.recipe_c.runner import InferenceReceipt, run_recipe_c_inference

_STAGE_DEVICE_PREDICTOR = b"print('predictor')\n"
_D4_RUNTIME_PREDICTOR = b"print('d4 predictor')\n"
_BUILDER_RUNTIME_PREDICTOR = b"print('builder predictor')\n"
_STAGE_DEVICE_PREDICTOR_SHA256 = hashlib.sha256(_STAGE_DEVICE_PREDICTOR).hexdigest()
_D4_RUNTIME_PREDICTOR_SHA256 = hashlib.sha256(_D4_RUNTIME_PREDICTOR).hexdigest()
_BUILDER_RUNTIME_PREDICTOR_SHA256 = hashlib.sha256(_BUILDER_RUNTIME_PREDICTOR).hexdigest()


class _FakePath:
    def __init__(
        self,
        path: Path,
        payload: bytes = b"artifact",
        *,
        fspath: str | None = None,
    ) -> None:
        self.path = path
        self.payload = payload
        self.logical_path = path
        self._fspath = fspath

    def __fspath__(self) -> str:
        return self._fspath or str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def read_bytes(self) -> bytes:
        return self.payload

    def is_file(self) -> bool:
        return True


class _FakeReceipt:
    def __init__(self, payload: bytes) -> None:
        self.sha256 = hashlib.sha256(payload).hexdigest()
        self.size = len(payload)
        self.fsynced = True


class _FakeStage:
    selection_lock_id = "b" * 64
    predictor_sha256_preimage = "c" * 64
    predictor_sha256_postimage = _STAGE_DEVICE_PREDICTOR_SHA256
    device_candidates = ("cuda", "mps", "cpu")

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "repo").mkdir(parents=True)
        self.closed = False
        self._repo_fd = os.open(root / "repo", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        self.repo_dir = _FakePath(root / "repo", fspath=f"/proc/self/fd/{self._repo_fd}")
        self.staged_config = _FakePath(root / "config.yaml", b"config: synthetic\n")
        self.predictor_path = _FakePath(root / "repo" / "scripts" / "predict_unet_transformer.py")
        self.predictor_payload = _STAGE_DEVICE_PREDICTOR
        self.primary_checkpoint_path = _FakePath(root / "repo" / "weights" / "primary.pth", b"primary")
        self.secondary_checkpoint_path = _FakePath(root / "repo" / "weights" / "secondary.pth", b"secondary")
        self.receipt = {
            "status": "READY",
            "selection_lock_id": self.selection_lock_id,
            "roles": {
                "repo": "repo",
                "weights": "repo/weights",
                "source_root": "source_root",
                "config": "configs/experiments/recipe_c_motion_off_edge_0_40_det0_96875.yaml",
                "predictor": "repo/scripts/predict_unet_transformer.py",
                "primary_checkpoint": "weights/unet_transformer/split_0/edge_predictor_best.pth",
                "secondary_checkpoint": "weights/unet_transformer/seed_314159/edge_predictor_best.pth",
            },
            "source_commit": "843a47fdd531bdf7e6377673135519c54b69ae28",
            "config_sha256": "bb994d357f3db3af0541c1e64c8862ec6ade85b85857c8eead1c06c95780fd1e",
            "predictor_sha256_before": "c" * 64,
            "predictor_sha256_after": self.predictor_sha256_postimage,
            "primary_checkpoint_sha256": "986a1b7135f4986150aa5fa0028feeaa66cdaf3ed6a00a355dd86e042f7fb494",
            "secondary_checkpoint_sha256": "c0f69e19ba252767f183158737ab1bc44f42380d2473ece23a4f276ae7c80dff",
            "resolved_device_candidates": ["cuda", "mps", "cpu"],
        }
        self.published: list[tuple[str, bytes]] = []

    def __enter__(self) -> _FakeStage:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._repo_fd >= 0:
            os.close(self._repo_fd)
            self._repo_fd = -1
        self.closed = True

    @property
    def repo_fd(self) -> int:
        return self._repo_fd

    def read_repo_bytes(self, relative: object) -> bytes:
        if str(relative).endswith("scripts/predict_unet_transformer.py"):
            return self.predictor_payload
        return b"scratch predictor\n"

    def publish_repo_bytes(self, relative: object, payload: bytes, *, expected: str) -> _FakeReceipt:
        assert expected == "absent"
        self.published.append((str(relative), payload))
        return _FakeReceipt(payload)


def _lock() -> dict[str, object]:
    return {
        "selection_lock_id": "b" * 64,
        "panel": {"panel_id": "PANEL_V1", "sample_ids": list(PANEL_V1)},
        "source_commit": "843a47fdd531bdf7e6377673135519c54b69ae28",
        "config_sha256": "bb994d357f3db3af0541c1e64c8862ec6ade85b85857c8eead1c06c95780fd1e",
        "predictor_sha256": "c" * 64,
        "primary_checkpoint_sha256": "986a1b7135f4986150aa5fa0028feeaa66cdaf3ed6a00a355dd86e042f7fb494",
        "secondary_checkpoint_sha256": "c0f69e19ba252767f183158737ab1bc44f42380d2473ece23a4f276ae7c80dff",
        "secondary_staging_relative_path": "weights/unet_transformer/seed_314159/edge_predictor_best.pth",
        "requested_device": "auto",
    }


def _minimal_receipt(marker: str) -> InferenceReceipt:
    return InferenceReceipt(
        status="READY",
        selection_lock_id="b" * 64,
        source_commit=marker,
        config_sha256="c" * 64,
        predictor_sha256_before="d" * 64,
        stage_predictor_sha256_after="e" * 64,
        d4_predictor_sha256_after="g" * 64,
        predictor_sha256_after="f" * 64,
        primary_checkpoint_sha256="1" * 64,
        secondary_checkpoint_sha256="2" * 64,
        sample_ids=(PANEL_V1[0],),
        mode="smoke_2frame",
        max_frames=2,
        command=("python", "predict"),
        command_sha256="3" * 64,
        execution_argv_sha256="4" * 64,
        cwd_role="repo",
        pythonpath="src",
        resolved_device="cpu",
        child_device="cpu",
        child_stdout_sha256="5" * 64,
        child_stderr_sha256="6" * 64,
        device_candidates=("cuda", "mps", "cpu"),
        runtime_role="live_stage_repo",
        patch_flags={"spatial_d4": True},
        raw_geffs={},
        postprocessed_csv=None,
        final_geffs={},
        manifests={},
        counts={},
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        failure=None,
    )


def test_runner_requires_exact_panel_and_lock_before_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    called = {"images": 0, "subprocess": 0}
    monkeypatch.setattr("biohub.recipe_c.runner._validate_selection_lock", lambda value: _lock())
    monkeypatch.setattr(
        "biohub.recipe_c.runner._preflight_images",
        lambda *args, **kwargs: called.__setitem__("images", called["images"] + 1),
    )
    monkeypatch.setattr(
        "biohub.recipe_c.runner.subprocess.run",
        lambda *args, **kwargs: called.__setitem__("subprocess", called["subprocess"] + 1),
    )
    with pytest.raises((ValueError, RuntimeError)):
        run_recipe_c_inference(
            tmp_path / "images",
            (PANEL_V1[0], "unknown"),
            stage,
            _lock(),
            tmp_path / "output",
        )
    assert called == {"images": 0, "subprocess": 0}


def test_receipt_shape_is_public_and_does_not_allow_gt_fields() -> None:
    fields = set(InferenceReceipt.__dataclass_fields__)
    assert {"status", "selection_lock_id", "sample_ids", "command", "manifests"} <= fields
    assert not fields.intersection({"gt_path", "ground_truth", "metric", "score", "gt_nodes"})


@pytest.mark.parametrize("samples", [PANEL_V1[:2], PANEL_V1])
def test_smoke_requires_exactly_one_sample(
    samples: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match=r"exactly one|smoke"):
        runner_module._sample_selection(samples, _lock(), 2)


@pytest.mark.parametrize(
    "missing",
    ["config_sha256", "predictor_sha256_before", "primary_checkpoint_sha256", "secondary_checkpoint_sha256"],
)
def test_stage_receipt_identity_fields_are_required(missing: str, tmp_path: Path) -> None:
    stage = _FakeStage(tmp_path / "stage")
    lock = _lock()
    stage.receipt[missing] = "x" * 64
    stage.receipt.pop(missing)
    with pytest.raises(ValueError, match=r"missing|identity"):
        runner_module._assert_stage_lock_identity(stage, lock)


def test_stage_lock_identity_rejects_hash_and_device_candidate_mismatch(tmp_path: Path) -> None:
    stage = _FakeStage(tmp_path / "stage")
    lock = _lock()
    stage.receipt["primary_checkpoint_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="identity"):
        runner_module._assert_stage_lock_identity(stage, lock)
    stage.receipt["primary_checkpoint_sha256"] = lock["primary_checkpoint_sha256"]
    stage.receipt["resolved_device_candidates"] = ["cpu"]
    with pytest.raises(ValueError, match="device candidates"):
        runner_module._assert_stage_lock_identity(stage, lock)
    stage.receipt["resolved_device_candidates"] = ["cuda", "mps", "cpu"]
    lock["source_commit"] = "wrong-commit"
    with pytest.raises(ValueError, match="source commit"):
        runner_module._assert_stage_lock_identity(stage, lock)


@pytest.mark.parametrize("attribute", ["predictor_sha256_preimage", "predictor_sha256_postimage"])
def test_stage_lock_identity_requires_predictor_preimage_and_postimage(
    tmp_path: Path, attribute: str
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    setattr(stage, attribute, None)
    with pytest.raises(ValueError, match="preimage/postimage"):
        runner_module._assert_stage_lock_identity(stage, _lock())


def test_stage_lock_identity_requires_secondary_staging_role(tmp_path: Path) -> None:
    stage = _FakeStage(tmp_path / "stage")
    lock = _lock()
    lock.pop("secondary_staging_relative_path")
    with pytest.raises(ValueError, match="secondary checkpoint role"):
        runner_module._assert_stage_lock_identity(stage, lock)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "FAILED"),
        ("selection_lock_id", "x" * 64),
        ("roles", {}),
        ("predictor_sha256_after", "x" * 64),
    ],
)
def test_stage_receipt_requires_ready_role_and_postimage_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    stage.receipt[field] = value
    with pytest.raises(ValueError, match=r"receipt|identity|role|postimage|READY"):
        runner_module._assert_stage_lock_identity(stage, _lock())


@pytest.mark.parametrize("artifact", ["config", "primary", "secondary", "predictor"])
def test_scratch_artifact_bytes_are_rehashed_against_stage_receipt(
    tmp_path: Path, artifact: str
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    if artifact == "config":
        stage.staged_config.payload = b"tampered config"
    elif artifact == "primary":
        stage.primary_checkpoint_path.payload = b"tampered checkpoint"
    elif artifact == "secondary":
        stage.secondary_checkpoint_path.payload = b"tampered checkpoint"
    else:
        stage.predictor_payload = b"tampered predictor"
    (tmp_path / "scratch").mkdir()
    with pytest.raises(ValueError, match=r"scratch|checkpoint|identity|hash"):
        runner_module._write_scratch_repo(stage, tmp_path / "scratch")


def test_device_resolver_errors_are_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(requested: str):
        raise RuntimeError("device probe failed")

    monkeypatch.setattr("biohub.device.resolve_torch_device", fail)
    with pytest.raises(RuntimeError, match="device probe failed"):
        runner_module._resolve_device("cuda")


def test_device_resolution_uses_canonical_resolver_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def resolve(requested: str):
        calls.append(requested)
        return type("Device", (), {"type": "cpu"})()

    monkeypatch.setattr("biohub.device.resolve_torch_device", resolve)
    assert runner_module._resolve_device("auto") == "cpu"
    assert calls == ["auto"]


def test_source_import_rejects_ambient_module_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    pinned_source = tmp_path / "pinned-source"
    (pinned_source / "src").mkdir(parents=True)
    stage.source_root = _FakePath(pinned_source)
    ambient = types.SimpleNamespace(
        __file__="/opt/ambient/biohub_pipeline/config.py",
        load_config=lambda path: object(),
        apply_spatial_d4_patch=lambda *args: True,
        build_predict_command=lambda *args: ([], Path("splits.json")),
        write_submission_from_geff=lambda *args: {},
    )
    monkeypatch.setattr(runner_module.importlib, "import_module", lambda name: ambient)
    with pytest.raises(ValueError, match=r"pinned|source|provenance"):
        runner_module._load_source_api(stage)


def test_source_import_uses_pinned_root_and_restores_module_namespace(tmp_path: Path) -> None:
    pinned_source = tmp_path / "pinned-source"
    package = pinned_source / "src" / "biohub_pipeline"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("\n", encoding="utf-8")
    (package / "config.py").write_text(
        "def load_config(path):\n    return 'pinned-config'\n", encoding="utf-8"
    )
    (package / "inference.py").write_text(
        "def apply_spatial_d4_patch(*args):\n    return True\n"
        "def build_predict_command(*args):\n    return ([], None)\n",
        encoding="utf-8",
    )
    (package / "submission.py").write_text(
        "def write_submission_from_geff(*args):\n    return {}\n", encoding="utf-8"
    )
    stage = _FakeStage(tmp_path / "stage")
    stage.source_root = _FakePath(pinned_source)
    ambient = types.ModuleType("biohub_pipeline")
    ambient.__file__ = "/opt/ambient/biohub_pipeline/__init__.py"
    import sys

    old = sys.modules.get("biohub_pipeline")
    sys.modules["biohub_pipeline"] = ambient
    try:
        api = runner_module._load_source_api(stage)
        assert api.load_config(Path("config.yaml")) == "pinned-config"
        assert api.load_config.__module__ == "biohub_pipeline.config"
        api.restore()
        assert sys.modules.get("biohub_pipeline") is ambient
    finally:
        if old is None:
            sys.modules.pop("biohub_pipeline", None)
        else:
            sys.modules["biohub_pipeline"] = old


def _command_for_rewrite(*extra: str) -> list[str]:
    return [
        "/opt/venv/bin/python",
        "scripts/predict_unet_transformer.py",
        "--data-dir",
        "/tmp/input",
        "--splits",
        "clean_v106_test_splits.json",
        "--weights",
        "weights/unet_transformer/split_0/edge_predictor_best.pth",
        "--det-threshold",
        "0.96875",
        "--edge-threshold",
        "0.4",
        "--ensemble-alpha",
        "0.5",
        "--ilp-edge-weight",
        "-1.0",
        "--ilp-appearance-weight",
        "0.0",
        "--ilp-disappearance-weight",
        "1.575",
        "--ilp-division-weight",
        "1.0",
        "--ensemble-weights",
        "weights/unet_transformer/seed_314159/edge_predictor_best.pth",
        "--use-ilp",
        *extra,
    ]


def _configure_failure_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    raw_kind: str,
    failure: str,
) -> tuple[_FakeStage, str, dict[str, int]]:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    forbidden_calls = {"gt": 0, "metric": 0}

    def forbidden_gt(*args: object, **kwargs: object) -> None:
        forbidden_calls["gt"] += 1
        raise AssertionError("GT must remain closed on failure")

    def forbidden_metric(*args: object, **kwargs: object) -> None:
        forbidden_calls["metric"] += 1
        raise AssertionError("metrics must remain closed on failure")

    monkeypatch.setattr("biohub.reproducibility.gt_guard.open_ground_truth", forbidden_gt)
    monkeypatch.setattr("biohub.submission.validator.load_ground_truth_nodes", forbidden_gt)
    monkeypatch.setattr("biohub.official_metrics.metrics.evaluate", forbidden_metric)
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda _value: _lock())
    monkeypatch.setattr(runner_module, "_preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_module, "_prepare_image_data", lambda *args: tmp_path / "input")
    (tmp_path / "input").mkdir()

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    def builder(config: object, data_dir: Path, repo_dir: Path, weights: Path, stems: list[str]):
        splits = repo_dir / "clean_v106_test_splits.json"
        splits.write_text("[]\n", encoding="utf-8")
        (repo_dir / "scripts" / "predict_unet_transformer.py").write_bytes(_BUILDER_RUNTIME_PREDICTOR)
        return _command_for_rewrite(), splits

    def writer(geffs: list[Path], config: object, data_dir: Path, output: Path) -> None:
        if failure == "source":
            raise RuntimeError("synthetic source postprocess failure")
        output.write_text("synthetic", encoding="utf-8")

    monkeypatch.setattr(
        runner_module,
        "_load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: object(),
            apply_spatial_d4_patch=d4,
            build_predict_command=builder,
            write_submission_from_geff=writer,
        ),
    )

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        username = runner_module.os.environ.get("USER", runner_module.os.environ.get("USERNAME", "unknown"))
        source = Path(str(kwargs["cwd"])) / "predictions" / username / "unet_transformer" / "split_0"
        source.mkdir(parents=True)
        geff = source / f"{sample}.geff"
        geff.mkdir()
        if raw_kind == "broken":
            (geff / "broken.bin").write_bytes(b"broken")
        if failure == "subprocess":
            raise subprocess.CalledProcessError(17, argv)
        return subprocess.CompletedProcess(argv, 0, stdout="device=cpu\n", stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "biohub.submission.validator.validate_submission",
        lambda *args, **kwargs: SimpleNamespace(ok=True),
    )
    if raw_kind == "valid":
        monkeypatch.setattr(
            runner_module,
            "validate_prediction_geff",
            lambda *args, **kwargs: {"nodes": 1, "edges": 0, "forks": 0},
        )
        monkeypatch.setattr(
            runner_module,
            "directory_digest_report",
            lambda path: {
                "directory_sha256": "a" * 64,
                "files": [],
                "total_bytes": 0,
                "hash_algorithm": "sha256",
            },
        )
    if failure == "bridge":
        def failing_bridge(
            csv_path: Path,
            output_root: Path,
            *,
            sample_ids: tuple[str, ...],
            provenance: dict[str, object],
            on_published: object = None,
        ) -> dict[str, Path]:
            output_root.mkdir()
            if callable(on_published):
                on_published(output_root, runner_module._entry_identity(output_root))
            raise RuntimeError("synthetic bridge failure")

        monkeypatch.setattr(runner_module, "postprocessed_csv_to_geffs", failing_bridge)
    return stage, sample, forbidden_calls


@pytest.mark.parametrize("raw_kind", ["empty", "broken"])
def test_empty_or_broken_raw_prediction_is_failed_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_kind: str
) -> None:
    stage, sample, forbidden_calls = _configure_failure_pipeline(
        tmp_path, monkeypatch, raw_kind=raw_kind, failure="raw"
    )
    output = tmp_path / "output"
    with pytest.raises((ValueError, OSError, RuntimeError, TypeError)):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), output, max_frames=2
        )
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]
    assert forbidden_calls == {"gt": 0, "metric": 0}


def test_source_postprocess_failure_is_failed_only_and_gt_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, sample, forbidden_calls = _configure_failure_pipeline(
        tmp_path, monkeypatch, raw_kind="valid", failure="source"
    )
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="source postprocess"):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), output, max_frames=2
        )
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]
    assert forbidden_calls == {"gt": 0, "metric": 0}


def test_bridge_failure_is_failed_only_and_gt_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, sample, forbidden_calls = _configure_failure_pipeline(
        tmp_path, monkeypatch, raw_kind="valid", failure="bridge"
    )
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="bridge"):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), output, max_frames=2
        )
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]
    assert forbidden_calls == {"gt": 0, "metric": 0}


def test_subprocess_partial_raw_failure_is_failed_only_and_gt_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage, sample, forbidden_calls = _configure_failure_pipeline(
        tmp_path, monkeypatch, raw_kind="valid", failure="subprocess"
    )
    output = tmp_path / "output"
    with pytest.raises(subprocess.CalledProcessError):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), output, max_frames=2
        )
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]
    assert forbidden_calls == {"gt": 0, "metric": 0}


@pytest.mark.parametrize(
    "extra",
    [
        ("--data-dir", "/tmp/other-input"),
        ("--weights", "../outside.pth"),
        ("--debug-video", "../outside.zarr"),
    ],
)
def test_command_rewrite_rejects_duplicate_or_untrusted_path_roles(extra: tuple[str, str]) -> None:
    with pytest.raises(ValueError, match=r"duplicate|path|role|traversal|unknown"):
        runner_module._rewrite_command(
            _command_for_rewrite(*extra),
            "../temporary-input",
            runner_module._PRIMARY_RELATIVE,
            runner_module._SECONDARY_RELATIVE,
        )


def test_execution_data_role_uses_physical_logical_repo_not_fd_path(tmp_path: Path) -> None:
    logical_repo = tmp_path / "very" / "deep" / "stage" / "repo"
    logical_repo.mkdir(parents=True)
    data_root = tmp_path / "runner-temp" / "input"
    data_root.mkdir(parents=True)
    repo_fd = os.open(logical_repo, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        repo = _FakePath(logical_repo, fspath=f"/proc/self/fd/{repo_fd}")
        stage = SimpleNamespace(repo_dir=repo, repo_fd=repo_fd)
        old_role = os.path.relpath(str(data_root), start=os.fspath(repo))
        assert (logical_repo / old_role).resolve() != data_root.resolve()

        role = runner_module._execution_data_role(stage, data_root)

        assert role == os.path.relpath(data_root, start=logical_repo)
        assert (logical_repo / role).resolve() == data_root.resolve()
    finally:
        os.close(repo_fd)


def test_image_preflight_does_not_fail_open_when_zarr_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_root = tmp_path / "images"
    image_root.mkdir()
    (image_root / f"{PANEL_V1[0]}.zarr").mkdir()
    original_import = builtins.__import__

    def missing_zarr(name: str, *args: object, **kwargs: object):
        if name == "zarr":
            raise ModuleNotFoundError("zarr intentionally unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_zarr)
    with pytest.raises((ImportError, RuntimeError), match=r"zarr|Zarr"):
        runner_module._preflight_images(image_root, (PANEL_V1[0],), None)


def test_smoke_subset_preserves_zarr_metadata_and_array_encoding(tmp_path: Path) -> None:
    zarr = pytest.importorskip("zarr")
    import numpy as np

    sample = PANEL_V1[0]
    image_root = tmp_path / "images"
    image_root.mkdir()
    source_path = image_root / f"{sample}.zarr"
    source = zarr.open_group(str(source_path), mode="w", zarr_format=3)
    source.attrs.update(
        {
            "multiscales": [
                {
                    "version": "0.4",
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": [
                                {"type": "scale", "scale": [1.0, 1.625, 0.40625, 0.40625]}
                            ],
                        }
                    ],
                }
            ],
            "image_statistics": {"quantiles": [0.0, 0.5, 1.0]},
        }
    )
    values = np.arange(4 * 2 * 3 * 4, dtype=np.uint16).reshape(4, 2, 3, 4)
    array = source.create_array(
        "0",
        data=values,
        chunks=(1, 2, 3, 4),
        fill_value=17,
        dimension_names=("t", "z", "y", "x"),
    )
    array.attrs["array_marker"] = "preserve-me"

    (tmp_path / "scratch").mkdir()
    destination_root = runner_module._prepare_image_data(
        image_root, (sample,), 2, tmp_path / "scratch"
    )
    destination = zarr.open_group(str(destination_root / f"{sample}.zarr"), mode="r")
    copied = destination["0"]
    assert dict(destination.attrs) == dict(source.attrs)
    assert dict(copied.attrs) == dict(array.attrs)
    assert tuple(copied.shape) == (2, 2, 3, 4)
    assert tuple(copied.chunks) == tuple(array.chunks)
    assert copied.dtype == array.dtype
    assert copied.fill_value == array.fill_value
    assert tuple(copied.metadata.dimension_names) == tuple(array.metadata.dimension_names)
    assert copied.metadata.codecs == array.metadata.codecs
    np.testing.assert_array_equal(copied[:], values[:2])


def test_smoke_subset_is_reopened_and_verified_after_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zarr = pytest.importorskip("zarr")
    import numpy as np

    sample = PANEL_V1[0]
    image_root = tmp_path / "images"
    image_root.mkdir()
    source_path = image_root / f"{sample}.zarr"
    source = zarr.open_group(str(source_path), mode="w", zarr_format=3)
    source.attrs.update(
        {
            "multiscales": [
                {"datasets": [{"coordinateTransformations": [{"scale": [1.0, 1.625, 0.40625, 0.40625]}]}]}
            ],
            "image_statistics": {"quantiles": [0.0, 1.0]},
        }
    )
    source.create_array("0", data=np.zeros((4, 2, 2, 2), dtype=np.uint8), chunks=(1, 2, 2, 2))
    original_open_group = zarr.open_group
    calls: list[tuple[str, object]] = []

    def tracked_open_group(*args: object, **kwargs: object):
        calls.append((str(args[0]) if args else "", kwargs.get("mode")))
        return original_open_group(*args, **kwargs)

    monkeypatch.setattr(zarr, "open_group", tracked_open_group)
    (tmp_path / "scratch").mkdir()
    runner_module._prepare_image_data(image_root, (sample,), 2, tmp_path / "scratch")
    assert any(mode == "r" and path.endswith(f"{sample}.zarr") for path, mode in calls)


@pytest.mark.parametrize("corruption", ["data", "metadata"])
def test_smoke_subset_reopen_rejects_backing_store_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    zarr = pytest.importorskip("zarr")
    import numpy as np

    sample = PANEL_V1[0]
    image_root = tmp_path / "images"
    image_root.mkdir()
    source_path = image_root / f"{sample}.zarr"
    source = zarr.open_group(str(source_path), mode="w", zarr_format=3)
    source.attrs.update(
        {
            "multiscales": [{"datasets": [{"coordinateTransformations": [{"scale": [1.0, 1.625, 0.40625, 0.40625]}]}]}],
            "image_statistics": {"quantiles": [0.0, 1.0]},
        }
    )
    source.create_array("0", data=np.zeros((4, 2, 2, 2), dtype=np.uint8), chunks=(1, 2, 2, 2))
    original_open_group = zarr.open_group

    def corrupt_then_open(*args: object, **kwargs: object):
        path = str(args[0]) if args else ""
        if kwargs.get("mode") == "r" and path.endswith(f"{sample}.zarr") and "input" in path:
            writable = original_open_group(path, mode="a")
            if corruption == "data":
                writable["0"][0, 0, 0, 0] = 7
            else:
                writable.attrs["image_statistics"] = {"quantiles": [0.0, 0.5]}
        return original_open_group(*args, **kwargs)

    monkeypatch.setattr(zarr, "open_group", corrupt_then_open)
    (tmp_path / "scratch").mkdir()
    with pytest.raises(ValueError, match=r"metadata|bytes|frame"):
        runner_module._prepare_image_data(image_root, (sample,), 2, tmp_path / "scratch")


def test_preflight_failure_persists_only_nonreusable_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    output = tmp_path / "output"
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda value: _lock())
    monkeypatch.setattr(
        runner_module,
        "_preflight_images",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("synthetic preflight failure")),
    )
    with pytest.raises(ValueError, match="preflight"):
        run_recipe_c_inference(tmp_path / "images", (sample,), stage, _lock(), output, max_frames=2)
    failed = json.loads((output / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["status"] == "FAILED"
    assert failed["phase"] == "preflight"
    assert failed["reusable"] is False
    assert not (output / "receipt.json").exists()
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]


def test_invalid_lock_is_claimed_then_records_unvalidated_failed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    output = tmp_path / "output"

    def reject(_value: object) -> dict[str, object]:
        raise ValueError("invalid lock")

    monkeypatch.setattr(runner_module, "_validate_selection_lock", reject)
    with pytest.raises(ValueError, match="invalid lock"):
        run_recipe_c_inference(tmp_path / "images", (PANEL_V1[0],), stage, {}, output, max_frames=2)
    failed = json.loads((output / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["selection_lock_id"] == "unvalidated"
    assert failed["phase"] == "preflight"
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]


def test_only_auto_requested_device_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    lock = _lock()
    lock["requested_device"] = "cpu"
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda _value: lock)
    with pytest.raises(ValueError, match="exactly auto"):
        run_recipe_c_inference(tmp_path / "images", (PANEL_V1[0],), stage, lock, tmp_path / "output", max_frames=2)
    failed = json.loads((tmp_path / "output" / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["phase"] == "preflight"


@pytest.mark.parametrize("stdout", ["", "device=cpu\ndevice=cpu\n", "device=mps\n"])
def test_child_device_output_must_be_exactly_one_resolved_device(stdout: str) -> None:
    with pytest.raises(ValueError, match="device"):
        runner_module._extract_child_device(stdout, "cpu")


def test_raw_prediction_publish_keeps_competitor_on_no_replace_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.geff"
    source.mkdir()
    (source / "source.bin").write_bytes(b"source")
    destination = tmp_path / "destination.geff"

    def race(_temporary: Path, final: Path) -> None:
        final.mkdir()
        (final / "competitor").write_bytes(b"keep")
        raise FileExistsError(final)

    monkeypatch.setattr(runner_module, "publish_directory_noreplace", race)
    with pytest.raises(FileExistsError):
        runner_module._copy_raw_prediction(source, destination)
    assert (destination / "competitor").read_bytes() == b"keep"


def test_raw_prediction_fsync_failure_removes_only_just_published_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.geff"
    source.mkdir()
    (source / "source.bin").write_bytes(b"source")
    destination = tmp_path / "destination.geff"
    published = False
    original_publish = runner_module.publish_directory_noreplace
    original_fsync = runner_module.fsync_directory

    def publish_then_fail(source_path: Path, destination_path: Path) -> None:
        nonlocal published
        original_publish(source_path, destination_path)
        published = True

    def fail_after_publish(path: Path) -> None:
        if published and Path(path) == destination.parent:
            raise OSError("synthetic parent fsync failure")
        original_fsync(path)

    monkeypatch.setattr(runner_module, "publish_directory_noreplace", publish_then_fail)
    monkeypatch.setattr(runner_module, "fsync_directory", fail_after_publish)
    with pytest.raises(OSError, match="fsync"):
        runner_module._copy_raw_prediction(source, destination)
    assert published is True
    assert not destination.exists()
    assert not list(tmp_path.glob(".destination.geff.*"))


@pytest.mark.parametrize("stems", [(), ("extra",), ("unknown",), (PANEL_V1[0], PANEL_V1[0])])
def test_raw_prediction_requires_exact_selected_sample_stems(
    tmp_path: Path, stems: tuple[str, ...]
) -> None:
    sample = PANEL_V1[0]
    raw_sources = [tmp_path / f"{stem}.geff" for stem in stems]
    with pytest.raises(ValueError, match=r"exactly|cover selected"):
        runner_module._assert_exact_raw_sources(raw_sources, (sample,))


def test_raw_publish_callback_uses_supplied_identity_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda _value: _lock())
    monkeypatch.setattr(runner_module, "_preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_module, "_prepare_image_data", lambda *args: tmp_path / "input")
    (tmp_path / "input").mkdir()

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    def builder(config: object, data_dir: Path, repo_dir: Path, weights: Path, stems: list[str]):
        splits = repo_dir / "clean_v106_test_splits.json"
        splits.write_text("[]\n", encoding="utf-8")
        (repo_dir / "scripts" / "predict_unet_transformer.py").write_bytes(_BUILDER_RUNTIME_PREDICTOR)
        return _command_for_rewrite(), splits

    monkeypatch.setattr(
        runner_module,
        "_load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: object(),
            apply_spatial_d4_patch=d4,
            build_predict_command=builder,
            write_submission_from_geff=lambda *args: None,
        ),
    )

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        username = runner_module.os.environ.get("USER", runner_module.os.environ.get("USERNAME", "unknown"))
        source = Path(str(kwargs["cwd"])) / "predictions" / username / "unet_transformer" / "split_0"
        source.mkdir(parents=True)
        (source / f"{sample}.geff").mkdir()
        return subprocess.CompletedProcess(argv, 0, stdout="device=cpu\n", stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)

    def race_copy(
        source: Path,
        destination: Path,
        *,
        on_published: object,
    ) -> tuple[int, int]:
        destination.mkdir()
        original_identity = runner_module._entry_identity(destination)
        competitor = tmp_path / "competitor.geff"
        competitor.mkdir()
        (competitor / "sentinel").write_bytes(b"keep")
        shutil.rmtree(destination)
        competitor.rename(destination)
        assert callable(on_published)
        on_published(destination, original_identity)
        raise OSError("synthetic raw post-publish failure")

    monkeypatch.setattr(runner_module, "_copy_raw_prediction", race_copy)
    with pytest.raises(OSError, match="post-publish"):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), tmp_path / "output", max_frames=2
        )
    assert (tmp_path / "output" / "raw" / f"{sample}.geff" / "sentinel").read_bytes() == b"keep"


def test_failed_receipt_never_replaces_competitor(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    target = output / "FAILED.json"
    target.write_text('{"status":"competitor"}\n', encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner_module._write_failed(output, "unvalidated", "preflight", ValueError("x"), ())
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "competitor"


def test_failed_cleanup_does_not_rmtree_unowned_competitor_child(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    raw = output / "raw"
    raw.mkdir()
    competitor = raw / "competitor.geff"
    competitor.mkdir()
    (competitor / "sentinel").write_bytes(b"keep")
    identity = runner_module._entry_identity(raw)
    runner_module._write_failed(
        output,
        "unvalidated",
        "raw_persist",
        RuntimeError("synthetic"),
        (),
        owned_entries={raw: identity},
    )
    assert (competitor / "sentinel").read_bytes() == b"keep"
    assert (output / "FAILED.json").is_file()


def test_ready_receipt_exclusive_publish_has_one_winner(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    receipts = (_minimal_receipt("winner-a"), _minimal_receipt("winner-b"))

    def publish(receipt: InferenceReceipt) -> str:
        try:
            runner_module._write_ready_receipt(output, receipt)
        except FileExistsError:
            return "lost"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, receipts))
    assert sorted(outcomes) == ["lost", "won"]
    payload = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert payload["source_commit"] in {"winner-a", "winner-b"}
    assert not list(output.glob(".receipt.json.*.tmp"))


def test_existing_output_root_is_rejected_without_failed_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda value: _lock())
    with pytest.raises(FileExistsError, match="fresh"):
        run_recipe_c_inference(tmp_path / "images", (sample,), stage, _lock(), output, max_frames=2)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (output / "FAILED.json").exists()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling", "failed"])
def test_all_existing_output_root_kinds_are_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    stage = _FakeStage(tmp_path / "stage")
    output = tmp_path / "output"
    if kind == "file":
        output.write_bytes(b"keep")
    elif kind == "directory":
        output.mkdir()
        (output / "sentinel").write_bytes(b"keep")
    elif kind == "failed":
        output.mkdir()
        (output / "FAILED.json").write_text('{"status":"old"}\n', encoding="utf-8")
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.mkdir()
        output.symlink_to(target, target_is_directory=kind == "symlink")
    monkeypatch.setattr(
        runner_module,
        "_validate_selection_lock",
        lambda _value: (_ for _ in ()).throw(AssertionError("existing output must reject first")),
    )
    with pytest.raises(FileExistsError, match="fresh"):
        run_recipe_c_inference(tmp_path / "images", (PANEL_V1[0],), stage, {}, output, max_frames=2)
    if kind == "file":
        assert output.read_bytes() == b"keep"
    elif kind == "directory":
        assert (output / "sentinel").read_bytes() == b"keep"
    elif kind == "failed":
        assert json.loads((output / "FAILED.json").read_text(encoding="utf-8"))["status"] == "old"
    else:
        assert output.is_symlink()


def test_builder_preflight_failure_uses_allowed_patch_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda value: _lock())
    monkeypatch.setattr(runner_module, "_preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner_module,
        "_prepare_image_data",
        lambda image_root, sample_ids, max_frames, scratch: tmp_path / "input",
    )
    (tmp_path / "input").mkdir()

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    monkeypatch.setattr(
        runner_module,
        "_load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: (_ for _ in ()).throw(RuntimeError("builder preflight failure")),
            apply_spatial_d4_patch=d4,
            build_predict_command=lambda *args: ([], Path("splits.json")),
            write_submission_from_geff=lambda *args: {},
        ),
    )
    with pytest.raises(RuntimeError, match="builder preflight"):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), tmp_path / "output", max_frames=2
        )
    failed = json.loads((tmp_path / "output" / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["phase"] == "patch"


def test_d4_postimage_hash_mismatch_fails_before_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    stage.predictor_sha256_postimage = "d" * 64
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda value: _lock())
    monkeypatch.setattr(runner_module, "_preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner_module,
        "_prepare_image_data",
        lambda image_root, sample_ids, max_frames, scratch: tmp_path / "input",
    )
    (tmp_path / "input").mkdir()

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    calls = {"builder": 0}

    def builder(*args: object):
        calls["builder"] += 1
        return _command_for_rewrite(), Path("splits.json")

    monkeypatch.setattr(
        runner_module,
        "_load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: object(),
            apply_spatial_d4_patch=d4,
            build_predict_command=builder,
            write_submission_from_geff=lambda *args: {},
        ),
    )
    with pytest.raises(ValueError, match="postimage"):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), tmp_path / "output", max_frames=2
        )
    assert calls["builder"] == 0
    failed = json.loads((tmp_path / "output" / "FAILED.json").read_text(encoding="utf-8"))
    assert failed["phase"] == "preflight"


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(17, ["python", "predict"]),
        subprocess.TimeoutExpired(["python", "predict"], 1.0),
        MemoryError("synthetic OOM"),
    ],
)
def test_subprocess_failures_are_nonreusable_and_phase_labeled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda value: _lock())
    monkeypatch.setattr(runner_module, "_preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner_module,
        "_prepare_image_data",
        lambda image_root, sample_ids, max_frames, scratch: tmp_path / "input",
    )
    (tmp_path / "input").mkdir()

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    def builder(config: object, data_dir: Path, repo_dir: Path, weights: Path, stems: list[str]):
        splits = repo_dir / "clean_v106_test_splits.json"
        splits.write_text("[]\n", encoding="utf-8")
        (repo_dir / "scripts" / "predict_unet_transformer.py").write_bytes(_BUILDER_RUNTIME_PREDICTOR)
        return _command_for_rewrite(), splits

    monkeypatch.setattr(
        runner_module,
        "_load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: object(),
            apply_spatial_d4_patch=d4,
            build_predict_command=builder,
            write_submission_from_geff=lambda *args: {},
        ),
    )
    monkeypatch.setattr(runner_module.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(type(failure)):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), tmp_path / "output", max_frames=2
        )
    failed_payload = json.loads((tmp_path / "output" / "FAILED.json").read_text(encoding="utf-8"))
    assert failed_payload["phase"] == "subprocess"
    assert failed_payload["reusable"] is False


def test_runner_calls_d4_builder_publish_and_direct_subprocess_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    lock = _lock()
    calls = {"d4": 0, "builder": 0, "writer": 0, "run": 0}
    forbidden_calls = {"gt": 0, "metric": 0}

    def forbidden_gt(*args: object, **kwargs: object) -> None:
        forbidden_calls["gt"] += 1
        raise AssertionError("ground truth must not be opened by Recipe C")

    def forbidden_metric(*args: object, **kwargs: object) -> None:
        forbidden_calls["metric"] += 1
        raise AssertionError("official metrics must not be called by Recipe C")

    monkeypatch.setattr("biohub.reproducibility.gt_guard.open_ground_truth", forbidden_gt)
    monkeypatch.setattr("biohub.submission.validator.load_ground_truth_nodes", forbidden_gt)
    monkeypatch.setattr("biohub.official_metrics.metrics.evaluate", forbidden_metric)

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        calls["d4"] += 1
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    def builder(config: object, data_dir: Path, repo_dir: Path, weights: Path, stems: list[str]):
        calls["builder"] += 1
        assert stems == [sample]
        splits = repo_dir / "clean_v106_test_splits.json"
        splits.write_text("[]\n", encoding="utf-8")
        (repo_dir / "scripts" / "predict_unet_transformer.py").write_bytes(_BUILDER_RUNTIME_PREDICTOR)
        return (
            [
                "/opt/venv/bin/python",
                "scripts/predict_unet_transformer.py",
                "--data-dir",
                str(data_dir),
                "--splits",
                splits.name,
                "--weights",
                "weights/unet_transformer/split_0/edge_predictor_best.pth",
                "--unet-batch-size",
                "4",
                "--det-threshold",
                "0.96875",
                "--ilp-edge-weight",
                "-1.0",
                "--ilp-appearance-weight",
                "0.0",
                "--ilp-disappearance-weight",
                "1.575",
                "--ilp-division-weight",
                "1.0",
                "--edge-threshold",
                "0.4",
                "--ensemble-weights",
                "weights/unet_transformer/seed_314159/edge_predictor_best.pth",
                "--ensemble-alpha",
                "0.5",
                "--use-ilp",
            ],
            splits,
        )

    def write_raw_geff(cwd: str) -> None:
        import polars as pl

        destination = Path(cwd) / "predictions" / "unknown" / "unet_transformer" / "split_0"
        destination.mkdir(parents=True)
        graph = td.graph.IndexedRXGraph()
        for key in ("z", "y", "x"):
            graph.add_node_attr_key(key, dtype=pl.Int64, default_value=0)
        graph.add_node({"t": 0, "z": 1, "y": 2, "x": 3}, index=0)
        graph.add_node({"t": 1, "z": 1, "y": 2, "x": 3}, index=1)
        graph.add_edge(0, 1, {})
        graph.to_geff(destination / f"{sample}.geff")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls["run"] += 1
        assert kwargs["shell"] is False
        assert kwargs["check"] is True
        assert kwargs["pass_fds"] == (stage.repo_fd,)
        assert kwargs["env"]["PYTHONPATH"] == "src"  # type: ignore[index]
        assert all(not value.startswith("/") for value in argv)
        data_argument = argv[argv.index("--data-dir") + 1]
        physical_cwd = stage.repo_dir.logical_path
        assert (physical_cwd / data_argument).resolve() == (tmp_path / "input").resolve()
        write_raw_geff(str(kwargs["cwd"]))
        return subprocess.CompletedProcess(argv, 0, stdout="Fold 0: synthetic | device=cpu | done\n", stderr="")

    def writer(geffs: list[Path], config: object, test_dir: Path, output: Path) -> dict[str, int]:
        calls["writer"] += 1
        assert test_dir == tmp_path / "input"
        assert test_dir != tmp_path / "images"
        with output.open("w", newline="", encoding="utf-8") as handle:
            out = csv.DictWriter(handle, fieldnames=(
                "id", "dataset", "row_type", "node_id", "t", "z", "y", "x", "source_id", "target_id"
            ))
            out.writeheader()
            out.writerow(
                {"id": 0, "dataset": sample, "row_type": "node", "node_id": 0, "t": 0,
                 "z": 1, "y": 2, "x": 3, "source_id": -1, "target_id": -1}
            )
            out.writerow(
                {"id": 1, "dataset": sample, "row_type": "node", "node_id": 1, "t": 1,
                 "z": 1, "y": 2, "x": 3, "source_id": -1, "target_id": -1}
            )
            out.writerow(
                {"id": 2, "dataset": sample, "row_type": "edge", "node_id": -1, "t": -1,
                 "z": -1, "y": -1, "x": -1, "source_id": 0, "target_id": 1}
            )
        return {"rows": 3}

    monkeypatch.setattr("biohub.recipe_c.runner._validate_selection_lock", lambda value: lock)
    monkeypatch.setattr("biohub.recipe_c.runner._preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr("biohub.recipe_c.runner._prepare_image_data", lambda *args: tmp_path / "input")
    (tmp_path / "input").mkdir()
    monkeypatch.setattr(
        "biohub.recipe_c.runner._load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: object(),
            apply_spatial_d4_patch=d4,
            build_predict_command=builder,
            write_submission_from_geff=writer,
        ),
    )
    monkeypatch.setattr("biohub.recipe_c.runner.subprocess.run", fake_run)

    receipt = run_recipe_c_inference(
        tmp_path / "images",
        (sample,),
        stage,
        lock,
        tmp_path / "output",
        max_frames=2,
    )

    assert isinstance(receipt, InferenceReceipt)
    assert receipt.status == "READY"
    assert receipt.mode == "smoke_2frame"
    assert calls == {"d4": 1, "builder": 1, "writer": 1, "run": 1}
    assert forbidden_calls == {"gt": 0, "metric": 0}
    assert len(stage.published) == 2
    assert (tmp_path / "output" / "receipt.json").is_file()
    assert not (tmp_path / "output" / "FAILED.json").exists()
    assert len(receipt.command_sha256) == 64
    assert len(receipt.execution_argv_sha256) == 64
    assert receipt.stage_predictor_sha256_after == _STAGE_DEVICE_PREDICTOR_SHA256
    assert receipt.predictor_sha256_after == _BUILDER_RUNTIME_PREDICTOR_SHA256
    assert receipt.d4_predictor_sha256_after == _D4_RUNTIME_PREDICTOR_SHA256
    assert receipt.stage_predictor_sha256_after != receipt.d4_predictor_sha256_after
    assert receipt.d4_predictor_sha256_after != receipt.predictor_sha256_after
    assert stage.predictor_sha256_postimage == _STAGE_DEVICE_PREDICTOR_SHA256
    assert stage.receipt["predictor_sha256_after"] == _STAGE_DEVICE_PREDICTOR_SHA256
    assert receipt.child_device == "cpu"
    assert len(receipt.child_stdout_sha256) == 64
    assert len(receipt.child_stderr_sha256) == 64
    assert receipt.counts["publish"]["predictor"]["sha256"] == _BUILDER_RUNTIME_PREDICTOR_SHA256  # type: ignore[index]
    manifest_path = tmp_path / "output" / "predictions" / f"{sample}.geff.manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in (
        "source_commit",
        "config_sha256",
        "predictor_sha256_before",
        "predictor_sha256",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_sha256",
        "resolved_device",
        "command_sha256",
        "execution_argv_sha256",
        "stage_predictor_sha256_after",
        "child_device",
        "child_stdout_sha256",
        "child_stderr_sha256",
    ):
        assert key in manifest_payload
    assert manifest_payload["predictor_sha256"] == _BUILDER_RUNTIME_PREDICTOR_SHA256
    assert manifest_payload["predictor_sha256_after"] == _BUILDER_RUNTIME_PREDICTOR_SHA256
    assert manifest_payload["d4_predictor_sha256_after"] == _D4_RUNTIME_PREDICTOR_SHA256
    assert manifest_payload["stage_predictor_sha256_after"] == _STAGE_DEVICE_PREDICTOR_SHA256


def _run_manifest_mint_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    preserve_competitor: bool,
) -> tuple[Path, dict[str, int]]:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    forbidden_calls = {"gt": 0, "metric": 0}

    def forbidden(*args: object, **kwargs: object) -> None:
        forbidden_calls["gt"] += 1
        raise AssertionError("GT must remain closed on manifest failure")

    def forbidden_metric(*args: object, **kwargs: object) -> None:
        forbidden_calls["metric"] += 1
        raise AssertionError("metrics must remain closed on manifest failure")

    monkeypatch.setattr("biohub.reproducibility.gt_guard.open_ground_truth", forbidden)
    monkeypatch.setattr("biohub.submission.validator.load_ground_truth_nodes", forbidden)
    monkeypatch.setattr("biohub.official_metrics.metrics.evaluate", forbidden_metric)
    monkeypatch.setattr(runner_module, "_validate_selection_lock", lambda _value: _lock())
    monkeypatch.setattr(runner_module, "_preflight_images", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_module, "_prepare_image_data", lambda *args: tmp_path / "input")
    (tmp_path / "input").mkdir()

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        (repo_dir / prediction_script).write_bytes(_D4_RUNTIME_PREDICTOR)
        return True

    def builder(config: object, data_dir: Path, repo_dir: Path, weights: Path, stems: list[str]):
        splits = repo_dir / "clean_v106_test_splits.json"
        splits.write_text("[]\n", encoding="utf-8")
        (repo_dir / "scripts" / "predict_unet_transformer.py").write_bytes(_BUILDER_RUNTIME_PREDICTOR)
        return _command_for_rewrite(), splits

    monkeypatch.setattr(
        runner_module,
        "_load_source_api",
        lambda stage_value: SimpleNamespace(
            load_config=lambda path: object(),
            apply_spatial_d4_patch=d4,
            build_predict_command=builder,
            write_submission_from_geff=lambda geffs, config, data_dir, output: output.write_text(
                "synthetic", encoding="utf-8"
            ),
        ),
    )

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        destination = Path(str(kwargs["cwd"])) / "predictions" / "unknown" / "unet_transformer" / "split_0"
        destination.mkdir(parents=True)
        (destination / f"{sample}.geff").mkdir()
        return subprocess.CompletedProcess(argv, 0, stdout="device=cpu\n", stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runner_module,
        "validate_prediction_geff",
        lambda *args, **kwargs: {"nodes": 1, "edges": 0, "forks": 0},
    )
    monkeypatch.setattr(
        runner_module,
        "directory_digest_report",
        lambda path: {"directory_sha256": "a" * 64, "files": [], "total_bytes": 0, "hash_algorithm": "sha256"},
    )
    monkeypatch.setattr(
        "biohub.submission.validator.validate_submission",
        lambda *args, **kwargs: SimpleNamespace(ok=True),
    )

    def fake_bridge(
        csv_path: Path,
        output_root: Path,
        *,
        sample_ids: tuple[str, ...],
        provenance: dict[str, object],
        on_published: object = None,
    ) -> dict[str, Path]:
        output_root.mkdir()
        original_root_identity = runner_module._entry_identity(output_root)
        if callable(on_published):
            on_published(output_root, original_root_identity)
        final = output_root / f"{sample}.geff"
        final.mkdir()
        (final / "graph.bin").write_bytes(b"synthetic")
        original_final_identity = runner_module._entry_identity(final)
        if preserve_competitor:
            competitor_final = tmp_path / "bridge-competitor.geff"
            competitor_final.mkdir()
            (competitor_final / "sentinel").write_bytes(b"keep")
            shutil.rmtree(final)
            competitor_final.rename(final)
        if callable(on_published):
            on_published(final, original_final_identity)
        return {sample: final}

    monkeypatch.setattr(runner_module, "postprocessed_csv_to_geffs", fake_bridge)

    def fake_manifest(prediction_path: Path, *, selection_lock_id: str, provenance: dict[str, object]) -> Path:
        manifest = prediction_path.with_name(f"{prediction_path.name}.manifest.json")
        manifest.write_text("{}\n", encoding="utf-8")
        return manifest

    monkeypatch.setattr(runner_module, "write_prediction_manifest", fake_manifest)
    monkeypatch.setattr(
        runner_module,
        "mint_prediction_token",
        lambda prediction: (_ for _ in ()).throw(RuntimeError("synthetic mint failure")),
    )

    with pytest.raises(RuntimeError, match="mint"):
        run_recipe_c_inference(
            tmp_path / "images", (sample,), stage, _lock(), tmp_path / "output", max_frames=2
        )
    return tmp_path / "output", forbidden_calls


def test_manifest_mint_failure_preserves_same_root_competitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, forbidden_calls = _run_manifest_mint_failure(
        tmp_path, monkeypatch, preserve_competitor=True
    )
    assert (output / "FAILED.json").is_file()
    assert (output / "predictions" / f"{PANEL_V1[0]}.geff" / "sentinel").read_bytes() == b"keep"
    assert forbidden_calls == {"gt": 0, "metric": 0}


def test_manifest_mint_failure_leaves_failed_only_and_never_opens_gt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, forbidden_calls = _run_manifest_mint_failure(
        tmp_path, monkeypatch, preserve_competitor=False
    )
    assert sorted(path.name for path in output.iterdir()) == ["FAILED.json"]
    assert forbidden_calls == {"gt": 0, "metric": 0}
