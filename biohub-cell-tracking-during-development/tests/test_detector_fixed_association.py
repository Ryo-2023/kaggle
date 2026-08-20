import inspect
from pathlib import Path

import numpy as np
import pytest

from biohub.detector_fixed_race.association import (
    AssociationSpec,
    associate_from_cache,
)
from biohub.detector_fixed_race.schema import CandidateEdgeArrays, DetectorCache, NodeArrays


class FakeGraph:
    def __init__(self, coords: np.ndarray, edges: list[tuple[int, int, float, float]]) -> None:
        self.coords = coords
        self.edge_rows = list(edges)

    def edge_list(self) -> list[list[int]]:
        return [[int(source), int(target)] for source, target, _score, _distance in self.edge_rows]


@pytest.fixture
def fake_cache() -> DetectorCache:
    tzyx = np.array(
        [
            [0, 0, 0, 0],
            [0, 0, 0, 10],
            [1, 0, 0, 0],
            [1, 0, 0, 10],
            [1, 0, 0, 30],
        ],
        dtype=np.int32,
    )
    nodes = NodeArrays(
        node_id=np.arange(5, dtype=np.int64),
        tzyx=tzyx,
        physical_zyx=tzyx[:, 1:].astype(np.float32),
        detector_peak_logit=np.ones(5, dtype=np.float32),
        detector_peak_probability=np.full(5, 0.75, dtype=np.float32),
        node_features=np.ones((5, 2), dtype=np.float32),
    )
    source = np.repeat(np.array([0, 1], dtype=np.int64), 3)
    target = np.tile(np.array([2, 3, 4], dtype=np.int64), 2)
    forward_probability = np.array([0.9, 0.6, 0.2, 0.7, 0.8, 0.4], dtype=np.float32)
    reverse_probability = np.array([0.8, 0.5, 0.3, 0.6, 0.9, 0.2], dtype=np.float32)
    # Logits are deliberately finite and independent from probability columns;
    # harmonic_v1 is expected to use these raw values rather than mutual scores.
    forward_logit = np.log(forward_probability / (1.0 - forward_probability)).astype(np.float32)
    reverse_logit = np.log(reverse_probability / (1.0 - reverse_probability)).astype(np.float32)
    voxel_delta = tzyx[target, 1:].astype(np.float32) - tzyx[source, 1:].astype(np.float32)
    edges = CandidateEdgeArrays(
        source_node_id=source,
        target_node_id=target,
        delta_t=np.ones(6, dtype=np.int16),
        voxel_delta=voxel_delta,
        physical_delta=voxel_delta.copy(),
        voxel_distance=np.linalg.norm(voxel_delta, axis=1).astype(np.float32),
        physical_distance=np.linalg.norm(voxel_delta, axis=1).astype(np.float32),
        forward_logit=forward_logit,
        reverse_logit=reverse_logit,
        forward_probability=forward_probability,
        reverse_probability=reverse_probability,
    )
    edges.validate(nodes)
    return DetectorCache(
        root=Path("cache/fake"),
        manifest={"cache_hash": "cache-hash-fake", "ground_truth_included": False},
        nodes=nodes,
        edges=edges,
    )


@pytest.fixture
def graph_builder_and_solver() -> tuple[object, object, list[FakeGraph]]:
    graphs: list[FakeGraph] = []

    def graph_builder(coords: np.ndarray, edges: list[tuple[int, int, float, float]]) -> FakeGraph:
        graph = FakeGraph(coords, edges)
        graphs.append(graph)
        return graph

    def solver(graph: FakeGraph) -> FakeGraph:
        return graph

    return graph_builder, solver, graphs


def test_association_has_no_image_or_ground_truth_parameters() -> None:
    parameters = inspect.signature(associate_from_cache).parameters
    assert "image_path" not in parameters
    assert "ground_truth_path" not in parameters
    assert "checkpoint" not in parameters


@pytest.mark.parametrize("method_id", ["official_ilp", "harmonic_v1", "mutual_confidence", "motion_gated"])
def test_all_methods_keep_same_cache_hash_and_finite_edges(
    fake_cache: DetectorCache,
    graph_builder_and_solver: tuple[object, object, list[FakeGraph]],
    method_id: str,
) -> None:
    graph_builder, solver, _graphs = graph_builder_and_solver
    result = associate_from_cache(
        fake_cache,
        AssociationSpec(method_id),
        graph_builder=graph_builder,
        ilp_solver=solver,
    )
    assert result.cache_hash == fake_cache.manifest["cache_hash"]
    assert np.isfinite(result.selected_edges).all()


def test_mutual_confidence_uses_geometric_mean(
    fake_cache: DetectorCache,
    graph_builder_and_solver: tuple[object, object, list[FakeGraph]],
) -> None:
    graph_builder, solver, graphs = graph_builder_and_solver
    associate_from_cache(
        fake_cache,
        AssociationSpec("mutual_confidence"),
        graph_builder=graph_builder,
        ilp_solver=solver,
    )
    rows = {(int(row[0]), int(row[1])): float(row[2]) for row in graphs[-1].edge_rows}
    assert rows[(0, 2)] == pytest.approx(np.sqrt(0.9 * 0.8))
    assert rows[(1, 3)] == pytest.approx(np.sqrt(0.8 * 0.9))
    assert (1, 4) not in rows  # sqrt(0.4 * 0.2) is below the strict .50 cutoff


def test_motion_gated_excludes_long_edges_and_applies_decay(
    fake_cache: DetectorCache,
    graph_builder_and_solver: tuple[object, object, list[FakeGraph]],
) -> None:
    graph_builder, solver, graphs = graph_builder_and_solver
    associate_from_cache(
        fake_cache,
        AssociationSpec("motion_gated"),
        graph_builder=graph_builder,
        ilp_solver=solver,
    )
    rows = {(int(row[0]), int(row[1])): float(row[2]) for row in graphs[-1].edge_rows}
    assert (0, 4) not in rows
    assert (1, 4) not in rows
    assert rows[(0, 2)] == pytest.approx(0.9)
    assert rows[(1, 3)] == pytest.approx(0.8)
