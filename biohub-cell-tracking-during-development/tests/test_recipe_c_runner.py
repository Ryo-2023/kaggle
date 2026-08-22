"""TDD contract tests for the GT-free Recipe C runner."""

from __future__ import annotations

import csv
import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import tracksdata as td

from biohub.recipe_c.protocol import PANEL_V1
from biohub.recipe_c.runner import InferenceReceipt, run_recipe_c_inference


class _FakePath:
    def __init__(self, path: Path, payload: bytes = b"artifact") -> None:
        self.path = path
        self.payload = payload

    def __fspath__(self) -> str:
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
    predictor_sha256_postimage = "d" * 64
    device_candidates = ("cuda", "mps", "cpu")
    repo_fd = 42

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "repo").mkdir(parents=True)
        self.closed = False
        self.repo_dir = _FakePath(root / "repo")
        self.staged_config = _FakePath(root / "config.yaml", b"config: synthetic\n")
        self.predictor_path = _FakePath(root / "repo" / "scripts" / "predict_unet_transformer.py")
        self.primary_checkpoint_path = _FakePath(root / "repo" / "weights" / "primary.pth", b"primary")
        self.secondary_checkpoint_path = _FakePath(root / "repo" / "weights" / "secondary.pth", b"secondary")
        self.receipt = {
            "source_commit": "843a47fdd531bdf7e6377673135519c54b69ae28",
            "config_sha256": "e" * 64,
        }
        self.published: list[tuple[str, bytes]] = []

    def __enter__(self) -> _FakeStage:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def read_repo_bytes(self, relative: object) -> bytes:
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
        "config_sha256": "e" * 64,
        "predictor_sha256": "c" * 64,
        "primary_checkpoint_sha256": "f" * 64,
        "secondary_checkpoint_sha256": "1" * 64,
        "secondary_staging_relative_path": "weights/unet_transformer/seed_314159/edge_predictor_best.pth",
        "requested_device": "auto",
    }


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


def test_runner_calls_d4_builder_publish_and_direct_subprocess_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample = PANEL_V1[0]
    stage = _FakeStage(tmp_path / "stage")
    lock = _lock()
    calls = {"d4": 0, "builder": 0, "writer": 0, "run": 0}

    def d4(repo_dir: Path, prediction_script: str) -> bool:
        calls["d4"] += 1
        (repo_dir / prediction_script).write_text("print('predictor')\n", encoding="utf-8")
        return True

    def builder(config: object, data_dir: Path, repo_dir: Path, weights: Path, stems: list[str]):
        calls["builder"] += 1
        assert stems == [sample]
        splits = repo_dir / "clean_v106_test_splits.json"
        splits.write_text("[]\n", encoding="utf-8")
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
        write_raw_geff(str(kwargs["cwd"]))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def writer(geffs: list[Path], config: object, test_dir: Path, output: Path) -> dict[str, int]:
        calls["writer"] += 1
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
    assert len(stage.published) == 2
    assert (tmp_path / "output" / "receipt.json").is_file()
    assert not (tmp_path / "output" / "FAILED.json").exists()
