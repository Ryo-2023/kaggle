"""The detector must be provably identical across association methods.

The detector-fixed race only means anything if changing the association method leaves
the detector byte-identical.  For six weeks that was an assumption backed by a shared
directory path.  These tests turn it into an assertion over the recorded digests, and
each one is paired with a deliberately broken variant proving the check fires.

Fixtures under ``tests/fixtures/reproducibility/real_receipts/`` are **verbatim copies
of real receipts**, not synthetic data.  Objects the tests build themselves are
labelled ``synthetic_``.  Nothing here loads a checkpoint or reads a ``.zarr``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from biohub.reproducibility.digest import recompute_cache_hash
from biohub.reproducibility.receipts import detector_invariance_report

FIXTURES = Path(__file__).parent / "fixtures" / "reproducibility" / "real_receipts"

#: Association-policy knobs.  If any of these ever appear in a detector cache manifest,
#: the cache digest becomes a function of the association method and detector-fixedness
#: is destroyed at the schema level.
ASSOCIATION_ONLY_KEYS = frozenset(
    {
        "method_id",
        "reverse_weight",
        "mutual_threshold",
        "motion_gate_um",
        "motion_alpha",
        "selected_edge_count",
        "candidate_edge_count",
        "ilp",
    }
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def race_records() -> list[dict[str, Any]]:
    """Real four-method race receipt for sample ``44b6_0113de3b``."""

    return load("dev_full_auto_compact_timed_race_receipt.json")


@pytest.fixture
def cache_manifest() -> dict[str, Any]:
    """Real detector cache manifest for the 100-frame ``full_auto`` cache."""

    return load("full_auto_cache_manifest.json")


# --------------------------------------------------------------------------------------
# Invariant: one detector, four methods, one digest.
# --------------------------------------------------------------------------------------


def test_all_methods_quote_one_identical_detector_cache_digest(race_records: list[dict[str, Any]]) -> None:
    report = detector_invariance_report(race_records)

    assert report["methods_missing_cache_hash"] == []
    assert len(report["distinct_cache_hashes"]) == 1, report["cache_hash_by_method"]
    assert report["invariant_holds"] is True
    assert sorted(report["methods"]) == [
        "harmonic_v1",
        "motion_gated",
        "mutual_confidence",
        "official_ilp",
    ]


def test_race_cache_digest_matches_the_cache_manifest_and_ready_marker(
    race_records: list[dict[str, Any]],
    cache_manifest: dict[str, Any],
) -> None:
    """The methods must quote the digest of a cache that actually exists on disk."""

    quoted = detector_invariance_report(race_records)["distinct_cache_hashes"]
    ready_marker = (FIXTURES / "full_auto_cache_READY").read_text().strip()

    assert quoted == [cache_manifest["cache_hash"]]
    assert ready_marker == cache_manifest["cache_hash"]


def test_detector_cache_digest_recomputes_from_its_own_manifest(cache_manifest: dict[str, Any]) -> None:
    """Independently re-derive the cache hash from the manifest's documented contract.

    ``recompute_cache_hash`` does not call the cache module, so this catches a manifest
    edited in place after publication as well as a hashing bug in the writer.
    """

    assert recompute_cache_hash(cache_manifest) == cache_manifest["cache_hash"]


def test_cache_digest_covers_the_detector_configuration(cache_manifest: dict[str, Any]) -> None:
    """A detector change must move the digest, one field at a time."""

    baseline = recompute_cache_hash(cache_manifest)
    moved: dict[str, str] = {}
    for field, replacement in (
        ("det_threshold", 0.95),
        ("pool_kernel_um", 5.0),
        ("edge_threshold", 0.6),
        ("window_size", 3),
        ("det_tta", False),
    ):
        mutated = json.loads(json.dumps(cache_manifest))
        assert mutated["detector_config"][field] != replacement
        mutated["detector_config"][field] = replacement
        moved[field] = recompute_cache_hash(mutated)

    assert all(digest != baseline for digest in moved.values()), moved
    assert len(set(moved.values())) == len(moved), "distinct detector changes collided"


def test_cache_digest_covers_checkpoint_and_source_identity(cache_manifest: dict[str, Any]) -> None:
    baseline = recompute_cache_hash(cache_manifest)
    for pointer in ("checkpoint_sha256", "source_commit", "image_sha256"):
        mutated = json.loads(json.dumps(cache_manifest))
        mutated[pointer] = "0" * 64
        assert recompute_cache_hash(mutated) != baseline, pointer


def test_detector_cache_manifest_contains_no_association_policy(cache_manifest: dict[str, Any]) -> None:
    """Schema-level guarantee that the association method cannot reach the detector."""

    def keys(payload: Any) -> set[str]:
        if isinstance(payload, dict):
            found = set(payload)
            for value in payload.values():
                found |= keys(value)
            return found
        if isinstance(payload, list):
            found: set[str] = set()
            for item in payload:
                found |= keys(item)
            return found
        return set()

    leaked = keys(cache_manifest) & ASSOCIATION_ONLY_KEYS
    assert leaked == set(), f"association policy leaked into the detector cache manifest: {sorted(leaked)}"


def test_detector_cache_manifest_declares_no_ground_truth(cache_manifest: dict[str, Any]) -> None:
    assert cache_manifest["ground_truth_included"] is False
    assert ".geff" not in json.dumps(cache_manifest)


# --------------------------------------------------------------------------------------
# Test-the-test: break the condition on a copy and require the check to fail.
# --------------------------------------------------------------------------------------


def test_invariance_check_fails_when_one_method_moves_the_detector(
    race_records: list[dict[str, Any]],
) -> None:
    """This is the check that would have caught a silently un-fixed detector."""

    tampered = json.loads(json.dumps(race_records))
    tampered[1]["cache_hash"] = "f" * 64

    report = detector_invariance_report(tampered)

    assert report["invariant_holds"] is False
    assert len(report["distinct_cache_hashes"]) == 2
    assert report["cache_hash_by_method"]["harmonic_v1"] == "f" * 64


def test_invariance_check_fails_when_a_method_records_no_detector_digest(
    race_records: list[dict[str, Any]],
) -> None:
    tampered = json.loads(json.dumps(race_records))
    tampered[0].pop("cache_hash")

    report = detector_invariance_report(tampered)

    assert report["invariant_holds"] is False
    assert report["methods_missing_cache_hash"] == ["official_ilp"]


def test_recompute_detects_a_manifest_edited_after_publication(cache_manifest: dict[str, Any]) -> None:
    tampered = json.loads(json.dumps(cache_manifest))
    tampered["node_count"] = int(tampered["node_count"]) + 1

    assert recompute_cache_hash(tampered) != tampered["cache_hash"]


def test_invariance_report_requires_at_least_one_record() -> None:
    with pytest.raises(ValueError):
        detector_invariance_report([])
