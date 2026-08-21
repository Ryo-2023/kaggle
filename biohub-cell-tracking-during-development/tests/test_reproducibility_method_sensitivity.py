"""Determinism, and proof that the race is actually comparing something.

Two failure modes are covered here.

*Determinism*: the same detector cache and the same method must produce the same
prediction.  If it does not, a score difference between two methods is noise.

*Method sensitivity*: two different methods must produce **different** predictions.  If
they collide, the race is comparing nothing and a reported delta is meaningless.

The algorithmic half runs the real ``associate_from_cache`` over a **synthetic**
detector cache with stub graph/solver callables.  Synthetic arrays are invented numbers
used only to exercise scoring logic; they are never presented as a measurement
(AGENTS.md §8).  No detector inference, no checkpoint, no ``.zarr``.

The recorded half asserts against the real four-method race receipt and, when the
artifact tree is reachable, re-hashes the four prediction directories off disk.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from biohub.reproducibility.digest import directory_digest_report
from biohub.reproducibility.receipts import method_sensitivity_report

pytest.importorskip("torch", reason="association scoring uses torch softmax; no model is loaded")

from biohub.detector_fixed_race.association import (  # noqa: E402
    AssociationSpec,
    associate_from_cache,
)
from biohub.detector_fixed_race.schema import (  # noqa: E402
    CandidateEdgeArrays,
    DetectorCache,
    NodeArrays,
)

FIXTURES = Path(__file__).parent / "fixtures" / "reproducibility" / "real_receipts"

_DEFAULT_RACE_DIR = (
    Path(__file__).resolve().parents[4]
    / "strong-baseline-v1"
    / "biohub-cell-tracking-during-development"
    / "artifacts"
    / "detector_fixed_race"
    / "dev_full_auto_compact_timed"
    / "44b6_0113de3b"
)


def real_race_dir() -> Path | None:
    """Locate the persisted four-method race output, if it is reachable read-only."""

    override = os.environ.get("BIOHUB_RACE_ARTIFACTS")
    candidate = Path(override) if override else _DEFAULT_RACE_DIR
    return candidate if candidate.is_dir() else None


# --------------------------------------------------------------------------------------
# Synthetic detector cache (labelled synthetic; three frames, two nodes each).
# --------------------------------------------------------------------------------------


def synthetic_detector_cache(*, forward: list[float], reverse: list[float]) -> DetectorCache:
    """Build a SYNTHETIC, fully populated detector cache with invented scores."""

    times = [0, 0, 1, 1, 2, 2]
    tzyx = np.array([[t, 10 + index, 20 + index, 30 + index] for index, t in enumerate(times)], dtype=np.int32)
    physical = (tzyx[:, 1:].astype(np.float32)) * np.array([1.625, 0.40625, 0.40625], dtype=np.float32)
    nodes = NodeArrays(
        node_id=np.arange(len(times), dtype=np.int64),
        tzyx=tzyx,
        physical_zyx=physical.astype(np.float32),
        detector_peak_logit=np.full(len(times), 3.0, dtype=np.float32),
        detector_peak_probability=np.full(len(times), 0.99, dtype=np.float32),
        node_features=np.arange(len(times) * 2, dtype=np.float32).reshape(len(times), 2),
    )

    pairs = [(0, 2), (0, 3), (1, 2), (1, 3), (2, 4), (2, 5), (3, 4), (3, 5)]
    source = np.array([pair[0] for pair in pairs], dtype=np.int64)
    target = np.array([pair[1] for pair in pairs], dtype=np.int64)
    voxel_delta = (tzyx[target, 1:] - tzyx[source, 1:]).astype(np.float32)
    physical_delta = (physical[target] - physical[source]).astype(np.float32)
    forward_probability = np.array(forward, dtype=np.float32)
    reverse_probability = np.array(reverse, dtype=np.float32)
    edges = CandidateEdgeArrays(
        source_node_id=source,
        target_node_id=target,
        delta_t=(tzyx[target, 0] - tzyx[source, 0]).astype(np.int16),
        voxel_delta=voxel_delta,
        physical_delta=physical_delta,
        voxel_distance=np.linalg.norm(voxel_delta, axis=1).astype(np.float32),
        physical_distance=np.linalg.norm(physical_delta, axis=1).astype(np.float32),
        forward_logit=np.log(forward_probability).astype(np.float32),
        reverse_logit=np.log(reverse_probability).astype(np.float32),
        forward_probability=forward_probability,
        reverse_probability=reverse_probability,
    )
    manifest = {
        "cache_hash": "5" * 64,
        "ground_truth_included": False,
        "sample_id": "synthetic_sample",
        "synthetic_fixture": True,
    }
    return DetectorCache(root=Path("synthetic"), manifest=manifest, nodes=nodes, edges=edges)


class SelectEverythingSolver:
    """Stub graph builder + solver: keeps every candidate, records the order it saw."""

    def __init__(self) -> None:
        self.seen: list[list[tuple[int, int, float, float]]] = []

    def build(self, coords: Any, candidate_rows: list[tuple[int, int, float, float]]) -> dict[str, Any]:
        self.seen.append(list(candidate_rows))
        return {"selected_edges": [(row[0], row[1]) for row in candidate_rows]}

    @staticmethod
    def solve(graph: dict[str, Any]) -> dict[str, Any]:
        return graph


@pytest.fixture
def cache() -> DetectorCache:
    # Forward is confident on the diagonal; reverse disagrees on two pairs, so
    # mutual_confidence must diverge from official_ilp.
    return synthetic_detector_cache(
        forward=[0.90, 0.10, 0.20, 0.80, 0.85, 0.15, 0.30, 0.70],
        reverse=[0.90, 0.10, 0.20, 0.35, 0.85, 0.15, 0.30, 0.25],
    )


def run(cache: DetectorCache, method_id: str) -> tuple[np.ndarray, dict[str, Any], SelectEverythingSolver]:
    stub = SelectEverythingSolver()
    result = associate_from_cache(cache, AssociationSpec(method_id), graph_builder=stub.build, ilp_solver=stub.solve)
    return result.selected_edges, dict(result.config), stub


# --------------------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------------------


def test_same_cache_and_method_produce_byte_identical_selections(cache: DetectorCache) -> None:
    first_edges, first_config, first_stub = run(cache, "official_ilp")
    second_edges, second_config, second_stub = run(cache, "official_ilp")

    assert first_edges.tobytes() == second_edges.tobytes()
    assert first_config == second_config
    # Candidate order feeds ILP tie-breaking, so it must be reproducible too.
    assert first_stub.seen == second_stub.seen


@pytest.mark.parametrize("method_id", ["official_ilp", "harmonic_v1", "mutual_confidence", "motion_gated"])
def test_every_method_is_deterministic(cache: DetectorCache, method_id: str) -> None:
    runs = [run(cache, method_id)[0].tobytes() for _ in range(3)]

    assert len(set(runs)) == 1, f"{method_id} produced different selections across repeats"


def test_determinism_check_would_fail_on_a_perturbed_cache(cache: DetectorCache) -> None:
    """Test-the-test: a cache that differs by one score must change the selection."""

    perturbed = synthetic_detector_cache(
        forward=[0.90, 0.10, 0.20, 0.80, 0.85, 0.15, 0.30, 0.49],
        reverse=[0.90, 0.10, 0.20, 0.35, 0.85, 0.15, 0.30, 0.25],
    )

    assert run(cache, "official_ilp")[0].tobytes() != run(perturbed, "official_ilp")[0].tobytes()


def test_directory_digest_is_stable_and_byte_sensitive(tmp_path: Path) -> None:
    prediction = tmp_path / "synthetic.geff"
    (prediction / "nodes").mkdir(parents=True)
    (prediction / "nodes" / "c0").write_bytes(b"synthetic-node-chunk")

    first = directory_digest_report(prediction)["directory_sha256"]
    assert directory_digest_report(prediction)["directory_sha256"] == first

    (prediction / "nodes" / "c0").write_bytes(b"synthetic-node-chunl")
    assert directory_digest_report(prediction)["directory_sha256"] != first


# --------------------------------------------------------------------------------------
# Method sensitivity at the algorithm level.
# --------------------------------------------------------------------------------------


def test_changing_the_method_changes_the_selected_edges(cache: DetectorCache) -> None:
    """If methods collide on the same cache, the race measures nothing."""

    selections = {method: run(cache, method)[0].tobytes() for method in ("official_ilp", "mutual_confidence")}

    assert selections["official_ilp"] != selections["mutual_confidence"]


def test_method_id_reaches_the_recorded_config(cache: DetectorCache) -> None:
    for method_id in ("official_ilp", "harmonic_v1", "mutual_confidence", "motion_gated"):
        _, config, _ = run(cache, method_id)
        assert config["method_id"] == method_id


def test_motion_gate_removes_candidates_beyond_the_gate(cache: DetectorCache) -> None:
    """A method whose knob does nothing is indistinguishable from a mislabelled run."""

    stub = SelectEverythingSolver()
    wide = associate_from_cache(
        cache,
        AssociationSpec("motion_gated", motion_gate_um=1e6),
        graph_builder=stub.build,
        ilp_solver=stub.solve,
    )
    narrow_stub = SelectEverythingSolver()
    narrow = associate_from_cache(
        cache,
        AssociationSpec("motion_gated", motion_gate_um=0.5),
        graph_builder=narrow_stub.build,
        ilp_solver=narrow_stub.solve,
    )

    assert narrow.config["candidate_edge_count"] < wide.config["candidate_edge_count"]


def test_association_refuses_a_cache_that_admits_ground_truth(cache: DetectorCache) -> None:
    leaky = DetectorCache(
        root=cache.root,
        manifest={**dict(cache.manifest), "ground_truth_included": True},
        nodes=cache.nodes,
        edges=cache.edges,
    )
    stub = SelectEverythingSolver()

    with pytest.raises(ValueError, match="ground-truth-free"):
        associate_from_cache(leaky, AssociationSpec("official_ilp"), graph_builder=stub.build, ilp_solver=stub.solve)


# --------------------------------------------------------------------------------------
# Method sensitivity of the recorded race, and the bytes behind it.
# --------------------------------------------------------------------------------------


@pytest.fixture
def race_records() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "dev_full_auto_compact_timed_race_receipt.json").read_text())


def test_recorded_race_produced_four_distinct_prediction_digests(race_records: list[dict[str, Any]]) -> None:
    report = method_sensitivity_report(race_records)

    assert report["methods_missing_digest"] == []
    assert report["colliding_method_pairs"] == []
    assert report["invariant_holds"] is True
    assert len(set(report["prediction_digest_by_method"].values())) == 4


def test_distinct_predictions_produced_distinct_scores(race_records: list[dict[str, Any]]) -> None:
    scores = {record["method_id"]: record["metrics"]["final_score"] for record in race_records}

    assert len(set(scores.values())) == len(scores), scores


def test_sensitivity_check_fails_when_two_methods_collide(race_records: list[dict[str, Any]]) -> None:
    """Test-the-test: this is the alarm for a race that is comparing nothing."""

    tampered = json.loads(json.dumps(race_records))
    shared = tampered[0]["metrics"]["prediction_manifest_directory_sha256"]
    tampered[1]["metrics"]["prediction_manifest_directory_sha256"] = shared

    report = method_sensitivity_report(tampered)

    assert report["invariant_holds"] is False
    assert ("harmonic_v1", "official_ilp") in report["colliding_method_pairs"]


def test_sensitivity_check_fails_when_a_digest_is_absent(race_records: list[dict[str, Any]]) -> None:
    tampered = json.loads(json.dumps(race_records))
    tampered[0]["metrics"].pop("prediction_manifest_directory_sha256")

    report = method_sensitivity_report(tampered)

    assert report["invariant_holds"] is False
    assert report["methods_missing_digest"] == ["official_ilp"]


def test_recorded_prediction_digests_still_match_the_bytes_on_disk(race_records: list[dict[str, Any]]) -> None:
    """Re-hash the persisted GEFF directories; ~330 KB each, no tracksdata needed."""

    race_dir = real_race_dir()
    if race_dir is None:
        pytest.skip("persisted race artifacts are not reachable from this checkout")

    for record in race_records:
        prediction = race_dir / f"{record['method_id']}.geff"
        if not prediction.is_dir():
            pytest.skip(f"prediction directory is missing: {prediction}")
        report = directory_digest_report(prediction)
        assert report["directory_sha256"] == record["metrics"]["prediction_manifest_directory_sha256"], (
            f"{record['method_id']} bytes no longer match its receipt"
        )
