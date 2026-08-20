from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tracksdata as td
import zarr

from biohub.benchmark_race.cc_flow import (
    CandidateTable,
    CCFlowConfig,
    detect_cc_candidates,
    link_cc_flow,
    run_cc_flow,
)
from biohub.benchmark_race.contracts import RaceRequest, SampleSpec


def _config(**overrides: object) -> CCFlowConfig:
    values: dict[str, object] = {
        "scale": (2.0, 0.5, 0.5),
        "q_low": 0.0,
        "q_high": 1.0,
        "foreground_threshold": 0.2,
        "min_component_voxels": 2,
        "max_component_voxels": 20,
        "max_link_distance_um": 3.0,
        "link_cost_per_um": 1.0,
        "gap_cost_um": 5.0,
    }
    values.update(overrides)
    return CCFlowConfig(**values)


def _two_frame_blobs() -> np.ndarray:
    image = np.zeros((2, 5, 10, 10), dtype=np.float32)
    image[0, 2, 2, 2:4] = (10.0, 8.0)
    image[0, 2, 7, 7] = 9.0
    image[0, 2, 7, 8] = 9.0
    image[0, 2, 7, 9] = 9.0
    image[1, 2, 3, 2:4] = (9.0, 7.0)
    image[1, 2, 7, 8:10] = (8.0, 8.0)
    return image


def test_cc_detector_returns_component_centroid_area_and_intensity_features() -> None:
    candidates = detect_cc_candidates(_two_frame_blobs(), _config())

    assert isinstance(candidates, CandidateTable)
    assert candidates.coordinates.shape == (4, 4)
    assert candidates.coordinates[:, 0].tolist() == [0.0, 0.0, 1.0, 1.0]
    assert candidates.areas.tolist() == [2, 3, 2, 2]
    np.testing.assert_allclose(candidates.coordinates[0], [0.0, 2.0, 2.0, 2.5])
    np.testing.assert_allclose(candidates.physical_coordinates[0], [4.0, 1.0, 1.25])
    assert candidates.mean_intensities[0] == pytest.approx(0.9)
    assert candidates.max_intensities[0] == pytest.approx(1.0)
    assert candidates.scores[0] == pytest.approx(0.9)


def test_cc_detector_filters_components_by_voxel_area() -> None:
    image = np.zeros((1, 4, 6, 6), dtype=np.float32)
    image[0, 1, 2, 2] = 10.0
    image[0, 1, 4, 4:6] = 8.0

    candidates = detect_cc_candidates(
        image,
        _config(min_component_voxels=2, max_component_voxels=2),
    )

    assert len(candidates) == 1
    assert candidates.areas.tolist() == [2]
    np.testing.assert_allclose(candidates.coordinates[0], [0.0, 1.0, 4.0, 4.5])


def test_cc_detector_uses_finite_values_for_quantile_normalization() -> None:
    image = np.zeros((1, 4, 6, 6), dtype=np.float32)
    image[0, 1, 2, 2] = 10.0
    image[0, 1, 4, 4] = np.inf
    image[0, 1, 4, 5] = np.nan

    candidates = detect_cc_candidates(
        image,
        _config(min_component_voxels=1, max_component_voxels=2),
    )

    assert len(candidates) == 1
    assert candidates.areas.tolist() == [1]


def test_cc_flow_links_all_frames_with_global_min_cost_flow_deterministically() -> None:
    candidates = CandidateTable(
        coordinates=np.asarray(
            [[0.0, 1.0, 2.0, 2.0], [1.0, 1.0, 2.0, 3.0], [2.0, 1.0, 2.0, 4.0]],
        ),
        physical_coordinates=np.asarray([[2.0, 1.0, 1.0], [2.0, 1.0, 1.5], [2.0, 1.0, 2.0]]),
        areas=np.asarray([4, 4, 4]),
        mean_intensities=np.asarray([0.8, 0.9, 0.85]),
        max_intensities=np.asarray([1.0, 1.0, 1.0]),
        scores=np.asarray([0.8, 0.9, 0.85]),
    )

    edges = link_cc_flow(candidates, _config())
    repeat = link_cc_flow(candidates, _config())

    assert edges.pairs.tolist() == [[0, 1], [1, 2]]
    assert np.all(edges.distances_um <= 3.0)
    np.testing.assert_array_equal(edges.pairs, repeat.pairs)
    np.testing.assert_allclose(edges.distances_um, repeat.distances_um)


def test_cc_flow_global_birth_death_can_reject_edge_that_frame_local_lap_would_take() -> None:
    candidates = CandidateTable(
        coordinates=np.asarray([[0.0, 1.0, 1.0, 1.0], [1.0, 1.0, 1.0, 3.0]]),
        physical_coordinates=np.asarray([[1.0, 1.0, 1.0], [1.0, 1.0, 3.0]]),
        areas=np.asarray([4, 4]),
        mean_intensities=np.asarray([0.8, 0.8]),
        max_intensities=np.asarray([1.0, 1.0]),
        scores=np.asarray([0.8, 0.8]),
    )
    config = _config(scale=(1.0, 1.0, 1.0), link_cost_per_um=3.0, gap_cost_um=4.0)

    edges = link_cc_flow(candidates, config)

    # A frame-local distance-gated LAP would accept this pair (distance 2 <=
    # max_link_distance_um 3).  The global flow chooses two birth/death paths
    # instead because 2 * 3.0 > the combined gap cost of 4.0.
    local_lap_pairs = [
        [source_id, target_id]
        for source_id in range(1)
        for target_id in range(1, 2)
        if np.linalg.norm(candidates.physical_coordinates[target_id] - candidates.physical_coordinates[source_id])
        <= config.max_link_distance_um
    ]
    assert local_lap_pairs == [[0, 1]]
    assert edges.pairs.tolist() == []


def test_cc_flow_writes_and_reloads_geff_with_features_and_no_gt_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    image_path = tmp_path / "synthetic.zarr"
    root = zarr.open_group(image_path, mode="w")
    root.create_array("0", data=_two_frame_blobs(), chunks=(1, 5, 10, 10))

    request = RaceRequest(
        sample=SampleSpec(
            sample_id="synthetic",
            image_stem="synthetic.zarr",
            shape=(2, 5, 10, 10),
            scale=(2.0, 0.5, 0.5),
            quantiles={"0.001": 0.0, "0.999": 10.0},
        ),
        cache_root=Path("artifacts/cache"),
        output_root=Path("artifacts/cc_flow"),
        expected_device="cpu",
        config={
            "q_low": 0.0,
            "q_high": 1.0,
            "foreground_threshold": 0.2,
            "min_component_voxels": 2,
            "max_component_voxels": 20,
            "max_link_distance_um": 3.0,
            "link_cost_per_um": 2.5,
            "gap_cost_um": 5.0,
        },
    )

    artifact = run_cc_flow(request)

    assert artifact.prediction_path.is_dir()
    loaded = td.graph.IndexedRXGraph.from_geff(artifact.prediction_path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    assert graph.num_nodes() == 4
    assert graph.num_edges() == 2
    edge_rows = graph.edge_attrs(attr_keys=["distance_um", "link_cost"]).to_dicts()
    assert edge_rows
    assert edge_rows[0]["link_cost"] == pytest.approx(edge_rows[0]["distance_um"] * 2.5)

    manifest = json.loads(artifact.prediction_manifest_path.read_text())
    receipt = json.loads(artifact.run_json_path.read_text())
    assert manifest["nodes"] == 4
    assert manifest["edges"] == 2
    assert manifest["structural_reload"].startswith("tracksdata")
    assert receipt["ground_truth_included"] is False
    assert "ground_truth_path" not in receipt
    assert receipt["method_id"] == "cc_flow"
    assert receipt["linker_id"] == "global_min_cost_flow"
    assert receipt["solver"] == "networkx.network_simplex"
    assert receipt["expected_device"] == "cpu"
    assert receipt["actual_device"] == "cpu"
    assert receipt["candidate_count"] == 4
    assert receipt["edge_count"] == 2
    assert receipt["config"]["link_cost_per_um"] == pytest.approx(2.5)
    assert receipt["source_revision"] == "unavailable-in-container"
    assert receipt["source_commit"] == "unavailable-in-container"
    cache_manifest = json.loads(artifact.cache_manifest_path.read_text())
    assert cache_manifest["source_commit"] == "unavailable-in-container"
    assert len(receipt["source_file_sha256"]) == 64


def test_cc_flow_rejects_request_config_scale_that_differs_from_sample_scale() -> None:
    sample = SampleSpec(
        sample_id="synthetic",
        image_stem="synthetic.zarr",
        shape=(2, 5, 10, 10),
        scale=(1.625, 0.40625, 0.40625),
        quantiles={"0.001": 0.0, "0.999": 10.0},
    )
    request = RaceRequest(
        sample=sample,
        cache_root=Path("artifacts/cache"),
        output_root=Path("artifacts/cc_flow"),
        expected_device="cpu",
        config={"scale": [9.0, 9.0, 9.0]},
    )

    with pytest.raises(ValueError, match="scale"):
        run_cc_flow(request)


def test_cc_flow_request_rejects_gt_option_before_inference() -> None:
    sample = SampleSpec(
        sample_id="synthetic",
        image_stem="synthetic.zarr",
        shape=(2, 5, 10, 10),
        scale=(2.0, 0.5, 0.5),
        quantiles={"0.001": 0.0, "0.999": 10.0},
    )

    with pytest.raises(ValueError, match=r"ground.?truth"):
        RaceRequest(
            sample=sample,
            cache_root=Path("artifacts/cache"),
            output_root=Path("artifacts/cc_flow"),
            expected_device="cpu",
            config={"ground_truth_path": "labels.geff"},
        )


def test_cc_flow_cli_exposes_smoke_and_infer_without_gt_option() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_benchmark_race.py"

    for command in ("smoke", "infer"):
        result = subprocess.run(
            [sys.executable, str(script), command, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "cc_flow" in result.stdout
        assert "ground-truth" not in result.stdout
        assert "ground_truth" not in result.stdout
