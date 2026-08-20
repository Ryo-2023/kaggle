from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tracksdata as td
import zarr

from biohub.benchmark_race.blob_lap import (
    BlobLapConfig,
    CandidateTable,
    EdgeTable,
    detect_blob_candidates,
    link_blob_lap,
    run_blob_lap,
)
from biohub.benchmark_race.contracts import RaceRequest, SampleSpec


def _config(**overrides: object) -> BlobLapConfig:
    values: dict[str, object] = {
        "scale": (2.0, 0.5, 0.5),
        "gaussian_sigma": (0.0, 0.0, 0.0),
        "local_max_size": (3, 3, 3),
        "peak_threshold": 0.2,
        "nms_distance_um": 0.75,
        "max_link_distance_um": 2.5,
    }
    values.update(overrides)
    return BlobLapConfig(**values)


def _two_frame_peaks() -> np.ndarray:
    image = np.zeros((2, 5, 8, 8), dtype=np.float32)
    image[0, 2, 2, 2] = 10.0
    image[0, 2, 5, 5] = 8.0
    image[1, 2, 3, 2] = 9.0
    image[1, 2, 5, 6] = 7.0
    return image


def test_blob_detector_returns_tzyx_candidates_and_physical_coordinates() -> None:
    candidates = detect_blob_candidates(_two_frame_peaks(), _config())

    assert isinstance(candidates, CandidateTable)
    assert candidates.coordinates.shape == (4, 4)
    assert candidates.coordinates.dtype.kind == "f"
    assert candidates.coordinates[:, 0].tolist() == [0.0, 0.0, 1.0, 1.0]
    np.testing.assert_allclose(
        candidates.physical_coordinates[0],
        np.array([4.0, 1.0, 1.0]),
    )
    assert candidates.scores[0] == pytest.approx(1.0)
    assert candidates.scores[0] > candidates.scores[2] > candidates.scores[1] > candidates.scores[3]


def test_blob_detector_uses_physical_distance_for_nms() -> None:
    image = np.zeros((1, 4, 6, 6), dtype=np.float32)
    image[0, 1, 2, 2] = 10.0
    image[0, 1, 2, 3] = 9.0

    candidates = detect_blob_candidates(
        image,
        _config(nms_distance_um=0.6),
    )

    assert len(candidates) == 1
    np.testing.assert_allclose(candidates.physical_coordinates[0], [2.0, 1.0, 1.0])


def test_blob_lap_is_deterministic_one_to_one_and_distance_gated() -> None:
    candidates = detect_blob_candidates(_two_frame_peaks(), _config())

    edges = link_blob_lap(candidates, _config())
    repeat = link_blob_lap(candidates, _config())

    assert isinstance(edges, EdgeTable)
    assert edges.pairs.tolist() == [[0, 2], [1, 3]]
    assert edges.pairs.shape == (2, 2)
    assert np.all(edges.distances_um <= 2.5)
    np.testing.assert_array_equal(edges.pairs, repeat.pairs)
    np.testing.assert_allclose(edges.distances_um, repeat.distances_um)
    assert all(
        candidates.coordinates[target, 0] == candidates.coordinates[source, 0] + 1
        for source, target in edges.pairs
    )


def test_blob_lap_writes_and_reloads_geff_with_no_gt_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    image_path = tmp_path / "synthetic.zarr"
    root = zarr.open_group(image_path, mode="w")
    root.create_array("0", data=_two_frame_peaks(), chunks=(1, 5, 8, 8))

    request = RaceRequest(
        sample=SampleSpec(
            sample_id="synthetic",
            image_stem="synthetic.zarr",
            shape=(2, 5, 8, 8),
            scale=(2.0, 0.5, 0.5),
            quantiles={"0.001": 0.0, "0.999": 10.0},
        ),
        cache_root=Path("artifacts/cache"),
        output_root=Path("artifacts/blob_lap"),
        expected_device="cpu",
        config={
            "gaussian_sigma": [0.0, 0.0, 0.0],
            "local_max_size": [3, 3, 3],
            "peak_threshold": 0.2,
            "nms_distance_um": 0.75,
            "max_link_distance_um": 2.5,
        },
    )

    artifact = run_blob_lap(request)

    assert artifact.prediction_path.is_dir()
    loaded = td.graph.IndexedRXGraph.from_geff(artifact.prediction_path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    assert graph.num_nodes() == 4
    assert graph.num_edges() == 2

    manifest = json.loads(artifact.prediction_manifest_path.read_text())
    receipt = json.loads(artifact.run_json_path.read_text())
    assert manifest["nodes"] == 4
    assert manifest["edges"] == 2
    assert manifest["structural_reload"].startswith("tracksdata")
    assert receipt["ground_truth_included"] is False
    assert "ground_truth_path" not in receipt
    assert receipt["method_id"] == "blob_lap"
    assert receipt["expected_device"] == "cpu"
    assert receipt["actual_device"] == "cpu"
    assert receipt["candidate_count"] == 4
    assert receipt["edge_count"] == 2


def test_blob_lap_request_rejects_gt_option_before_inference() -> None:
    sample = SampleSpec(
        sample_id="synthetic",
        image_stem="synthetic.zarr",
        shape=(2, 5, 8, 8),
        scale=(2.0, 0.5, 0.5),
        quantiles={"0.001": 0.0, "0.999": 10.0},
    )

    with pytest.raises(ValueError, match=r"ground.?truth"):
        RaceRequest(
            sample=sample,
            cache_root=Path("artifacts/cache"),
            output_root=Path("artifacts/blob_lap"),
            expected_device="cpu",
            config={"ground_truth_path": "labels.geff"},
        )


def test_blob_lap_cli_does_not_expose_ground_truth_option() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_benchmark_race.py"

    help_result = subprocess.run(
        [sys.executable, str(script), "infer", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0
    assert "ground-truth" not in help_result.stdout
    assert "ground_truth" not in help_result.stdout
