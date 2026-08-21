from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from biohub.benchmark_race.contracts import SampleSpec
from biohub.detector_fixed_race.cache import (
    build_detector_cache_manifest,
    build_edge_memory_map,
    load_detector_cache,
    write_detector_cache,
)
from biohub.detector_fixed_race.schema import CandidateEdgeArrays, NodeArrays


@pytest.fixture
def sample_spec() -> SampleSpec:
    return SampleSpec(
        sample_id="sample-01",
        image_stem=Path("images/sample-01.zarr"),
        shape=(2, 4, 8, 8),
        scale=(1.5, 0.5, 0.5),
        quantiles={"0.001": 0.0, "0.999": 1.0},
    )


@pytest.fixture
def sample_arrays() -> dict[str, NodeArrays | CandidateEdgeArrays]:
    nodes = NodeArrays(
        node_id=np.array([0, 1, 2, 3], dtype=np.int64),
        tzyx=np.array(
            [[0, 0, 0, 0], [0, 0, 1, 1], [1, 0, 0, 0], [1, 0, 1, 1]],
            dtype=np.int32,
        ),
        physical_zyx=np.array(
            [[0.0, 0.0, 0.0], [0.0, 0.5, 0.5], [0.0, 0.0, 0.0], [0.0, 0.5, 0.5]],
            dtype=np.float32,
        ),
        detector_peak_logit=np.array([1.0, 1.1, 1.2, 1.3], dtype=np.float32),
        detector_peak_probability=np.array([0.7, 0.8, 0.9, 0.95], dtype=np.float32),
        node_features=np.ones((4, 2), dtype=np.float32),
    )
    edges = CandidateEdgeArrays(
        source_node_id=np.array([0, 1], dtype=np.int64),
        target_node_id=np.array([2, 3], dtype=np.int64),
        delta_t=np.array([1, 1], dtype=np.int16),
        voxel_delta=np.zeros((2, 3), dtype=np.float32),
        physical_delta=np.zeros((2, 3), dtype=np.float32),
        voxel_distance=np.zeros(2, dtype=np.float32),
        physical_distance=np.zeros(2, dtype=np.float32),
        forward_logit=np.array([0.1, 0.2], dtype=np.float32),
        reverse_logit=np.array([0.3, 0.4], dtype=np.float32),
        forward_probability=np.array([0.6, 0.7], dtype=np.float32),
        reverse_probability=np.array([0.65, 0.75], dtype=np.float32),
    )
    return {"nodes": nodes, "edges": edges}


def valid_manifest(sample_spec: SampleSpec) -> dict[str, object]:
    return build_detector_cache_manifest(
        sample_spec,
        image_sha256="image",
        detector_config={"detector_id": "detector-v1"},
        provenance={"source_commit": "abc"},
        node_digest="nodes",
        edge_digest="edges",
    )


def test_detector_cache_manifest_rejects_ground_truth_fields(sample_spec: SampleSpec) -> None:
    with pytest.raises(ValueError, match=r"ground.?truth|annotation|label"):
        build_detector_cache_manifest(
            sample_spec,
            image_sha256="image",
            detector_config={"annotation_path": "labels.json"},
            provenance={"source_commit": "abc"},
            node_digest="nodes",
            edge_digest="edges",
        )


def test_detector_cache_rejects_wrong_edge_direction(
    tmp_path: Path,
    sample_spec: SampleSpec,
    sample_arrays: dict[str, NodeArrays | CandidateEdgeArrays],
) -> None:
    nodes = sample_arrays["nodes"]
    edges = sample_arrays["edges"]
    assert isinstance(nodes, NodeArrays)
    assert isinstance(edges, CandidateEdgeArrays)
    bad_edges = replace(
        edges,
        source_node_id=np.array([3], dtype=np.int64),
        target_node_id=np.array([2], dtype=np.int64),
        delta_t=np.array([0], dtype=np.int16),
        voxel_delta=np.zeros((1, 3), dtype=np.float32),
        physical_delta=np.zeros((1, 3), dtype=np.float32),
        voxel_distance=np.zeros(1, dtype=np.float32),
        physical_distance=np.zeros(1, dtype=np.float32),
        forward_logit=np.array([0.1], dtype=np.float32),
        reverse_logit=np.array([0.3], dtype=np.float32),
        forward_probability=np.array([0.6], dtype=np.float32),
        reverse_probability=np.array([0.65], dtype=np.float32),
    )
    with pytest.raises(ValueError, match=r"source|target|time"):
        write_detector_cache(tmp_path / "cache", valid_manifest(sample_spec), nodes, bad_edges)


def test_detector_cache_requires_ready_and_digest(
    tmp_path: Path,
    sample_spec: SampleSpec,
    sample_arrays: dict[str, NodeArrays | CandidateEdgeArrays],
) -> None:
    nodes = sample_arrays["nodes"]
    edges = sample_arrays["edges"]
    assert isinstance(nodes, NodeArrays)
    assert isinstance(edges, CandidateEdgeArrays)
    receipt = write_detector_cache(tmp_path / "cache", valid_manifest(sample_spec), nodes, edges)
    assert receipt.cache_hash == load_detector_cache(receipt.root).manifest["cache_hash"]
    (receipt.root / "READY").unlink()
    with pytest.raises(ValueError, match="READY"):
        load_detector_cache(receipt.root)


def test_detector_cache_edge_memory_map_is_used_for_replay(
    tmp_path: Path,
    sample_spec: SampleSpec,
    sample_arrays: dict[str, NodeArrays | CandidateEdgeArrays],
) -> None:
    nodes = sample_arrays["nodes"]
    edges = sample_arrays["edges"]
    assert isinstance(nodes, NodeArrays)
    assert isinstance(edges, CandidateEdgeArrays)
    receipt = write_detector_cache(tmp_path / "cache", valid_manifest(sample_spec), nodes, edges)
    sidecar = build_edge_memory_map(receipt.root)
    assert sidecar.is_dir()
    loaded = load_detector_cache(receipt.root)
    assert isinstance(loaded.edges.source_node_id, np.memmap)
    np.testing.assert_array_equal(loaded.edges.source_node_id, edges.source_node_id)
