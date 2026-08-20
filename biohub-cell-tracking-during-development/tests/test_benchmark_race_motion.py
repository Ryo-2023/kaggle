from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tracksdata as td

from biohub.benchmark_race.blob_lap import (
    BlobLapConfig,
    CandidateTable,
    link_blob_lap,
)
from biohub.benchmark_race.cache import build_cache_manifest
from biohub.benchmark_race.contracts import RaceRequest, SampleSpec
from biohub.benchmark_race.motion import (
    MotionLapConfig,
    estimate_velocities,
    link_motion,
    motion_cost,
    run_motion_lap,
)


def _config(**overrides: object) -> MotionLapConfig:
    values: dict[str, object] = {
        "scale": (1.0, 1.0, 1.0),
        "max_link_distance_um": 8.0,
        "velocity_weight": 1.0,
        "acceleration_penalty": 0.5,
        "initial_velocity_policy": "zero",
    }
    values.update(overrides)
    return MotionLapConfig(**values)


def _crossing_candidates() -> CandidateTable:
    # At frame 1 the identities move toward one another.  At frame 2 they
    # continue through the crossing, so distance-only LAP picks the opposite
    # identities while a velocity prior preserves them.
    coordinates = np.asarray(
        [
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 10.0],
            [1.0, 0.0, 0.0, 4.0],
            [1.0, 0.0, 0.0, 6.0],
            [2.0, 0.0, 0.0, 8.0],
            [2.0, 0.0, 0.0, 2.0],
        ],
    )
    return CandidateTable(
        coordinates=coordinates,
        physical_coordinates=coordinates[:, 1:].copy(),
        scores=np.ones(len(coordinates), dtype=np.float64),
    )


def test_velocity_estimation_is_deterministic_and_uses_previous_frame() -> None:
    candidates = CandidateTable(
        coordinates=np.asarray(
            [[0.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 2.0], [2.0, 0.0, 0.0, 4.0]],
        ),
        physical_coordinates=np.asarray([[0.0, 0.0, 0.0], [0.0, 0.0, 2.0], [0.0, 0.0, 4.0]]),
        scores=np.ones(3),
    )

    velocities = estimate_velocities(candidates, _config())
    repeat = estimate_velocities(candidates, _config())

    np.testing.assert_allclose(velocities, [[0.0, 0.0, 0.0], [0.0, 0.0, 2.0], [0.0, 0.0, 2.0]])
    np.testing.assert_array_equal(velocities, repeat)


def test_motion_cost_rewards_predicted_position_and_penalizes_acceleration() -> None:
    config = _config(velocity_weight=2.0, acceleration_penalty=0.5)
    source = np.asarray([0.0, 0.0, 0.0])
    target = np.asarray([5.0, 0.0, 0.0])

    predicted = motion_cost(source, target, np.asarray([5.0, 0.0, 0.0]), config)
    stationary = motion_cost(source, target, np.zeros(3), config)

    assert predicted == pytest.approx(0.0)
    assert stationary == pytest.approx(12.5)
    assert stationary > predicted


def test_motion_lap_is_one_to_one_for_explicit_edge_scores() -> None:
    candidates = CandidateTable(
        coordinates=np.asarray(
            [[0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 3.0], [1.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 2.0]],
        ),
        physical_coordinates=np.asarray([[0.0, 0.0, 0.0], [0.0, 0.0, 3.0], [0.0, 0.0, 1.0], [0.0, 0.0, 2.0]]),
        scores=np.ones(4),
    )
    edge_scores = {(0, 2): 0.4, (0, 3): 0.1, (1, 2): 0.2, (1, 3): 0.8}

    edges = link_motion(candidates, edge_scores, _config(max_link_distance_um=4.0))

    assert edges.pairs.tolist() == [[0, 3], [1, 2]]
    assert len(set(edges.source.tolist())) == len(edges)
    assert len(set(edges.target.tolist())) == len(edges)


def test_motion_prior_changes_crossing_choice_relative_to_blob_lap() -> None:
    candidates = _crossing_candidates()
    blob_config = BlobLapConfig(scale=(1.0, 1.0, 1.0), max_link_distance_um=8.0)

    blob_edges = link_blob_lap(candidates, blob_config)
    motion_edges = link_motion(candidates, None, _config())

    assert [pair for pair in blob_edges.pairs.tolist() if pair[0] in (2, 3)] == [[2, 5], [3, 4]]
    assert [pair for pair in motion_edges.pairs.tolist() if pair[0] in (2, 3)] == [[2, 4], [3, 5]]


def _write_blob_cache(tmp_path: Path, candidates: CandidateTable, sample: SampleSpec) -> Path:
    detector_config = BlobLapConfig(scale=sample.scale).as_dict()
    manifest = build_cache_manifest(
        sample=sample,
        image_digest="synthetic-image-digest",
        detector_config=detector_config,
        source_commit="fixture-source",
    )
    cache_dir = tmp_path / "cache" / str(manifest["cache_key"])
    cache_dir.mkdir(parents=True)
    np.savez(
        cache_dir / "detections.npz",
        coordinates=candidates.coordinates,
        physical_coordinates=candidates.physical_coordinates,
        scores=candidates.scores,
    )
    (cache_dir / "cache_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return cache_dir


def test_motion_run_reloads_geff_and_writes_method_specific_gt_free_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    candidates = CandidateTable(
        coordinates=np.asarray([[0.0, 1.0, 1.0, 1.0], [1.0, 1.0, 1.0, 2.0]]),
        physical_coordinates=np.asarray([[1.0, 1.0, 1.0], [1.0, 1.0, 2.0]]),
        scores=np.asarray([1.0, 0.9]),
    )
    sample = SampleSpec(
        sample_id="synthetic",
        image_stem="synthetic.zarr",
        shape=(2, 4, 8, 8),
        scale=(1.0, 1.0, 1.0),
        quantiles={"0.001": 0.0, "0.999": 1.0},
    )
    cache_dir = _write_blob_cache(tmp_path, candidates, sample)
    request = RaceRequest(
        sample=sample,
        cache_root=Path("artifacts/cache"),
        output_root=Path("artifacts/motion_lap"),
        expected_device="cpu",
        config={
            "max_link_distance_um": 3.0,
            "velocity_weight": 1.0,
            "acceleration_penalty": 0.5,
            "initial_velocity_policy": "zero",
        },
    )

    artifact = run_motion_lap(request, cache_dir)

    assert artifact.prediction_path.is_dir()
    loaded = td.graph.IndexedRXGraph.from_geff(artifact.prediction_path)
    graph = loaded[0] if isinstance(loaded, tuple) else loaded
    assert graph.num_nodes() == 2
    assert graph.num_edges() == 1
    edge_rows = graph.edge_attrs(attr_keys=["distance_um", "motion_cost"]).to_dicts()
    assert edge_rows[0]["distance_um"] == pytest.approx(1.0)
    assert edge_rows[0]["motion_cost"] == pytest.approx(1.5)

    manifest = json.loads(artifact.prediction_manifest_path.read_text())
    receipt = json.loads(artifact.run_json_path.read_text())
    assert manifest["method_id"] == "motion_lap"
    assert manifest["ground_truth_included"] is False
    assert manifest["method_family"] == "classical_motion_association"
    assert receipt["method_id"] == "motion_lap"
    assert receipt["method_family"] == "classical_motion_association"
    assert receipt["detector_id"] == "blob_lap_fixed_image_only_candidates"
    assert receipt["linker_id"] == "velocity_acceleration_hungarian_lap"
    assert receipt["division_enabled"] is False
    assert receipt["ground_truth_included"] is False
    assert "ground_truth_path" not in receipt
    assert receipt["candidate_cache_method"] == "blob_lap"
    assert receipt["expected_device"] == "cpu"
    assert receipt["actual_device"] == "cpu"


def test_motion_cache_rejects_ground_truth_manifest(tmp_path: Path) -> None:
    candidates = _crossing_candidates()
    sample = SampleSpec(
        sample_id="synthetic",
        image_stem="synthetic.zarr",
        shape=(3, 1, 1, 11),
        scale=(1.0, 1.0, 1.0),
        quantiles={"0.001": 0.0, "0.999": 1.0},
    )
    cache_dir = _write_blob_cache(tmp_path, candidates, sample)
    manifest_path = cache_dir / "cache_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["ground_truth_included"] = True
    manifest_path.write_text(json.dumps(manifest))
    request = RaceRequest(
        sample=sample,
        cache_root=Path("cache"),
        output_root=Path("out"),
        expected_device="cpu",
    )

    with pytest.raises(ValueError, match=r"ground.?truth"):
        run_motion_lap(request, cache_dir)


def test_motion_cli_lists_method_without_ground_truth_boundary() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_benchmark_race.py"

    for command in ("smoke", "infer"):
        result = subprocess.run(
            [sys.executable, str(script), command, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "motion_lap" in result.stdout
        assert "ground-truth" not in result.stdout
        assert "ground_truth" not in result.stdout
