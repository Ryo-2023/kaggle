"""A cache-implementation change must not silently change the detector output.

Codex rewrote the detector capture path twice (``19feb13`` "Stream detector pair
captures to disk", ``8b03cd6`` "Use chunked memmap edge cache validation").  Rebuilding
a cache to check is forbidden here — it is detector inference — but the tree happens to
contain a genuine before/after pair for the same sample and the same frame count, so the
question is answerable from the persisted manifests alone.

It also exposes why ``cache_hash`` cannot be used to answer it: the hash covers
``provenance.elapsed_seconds`` and ``provenance.adapter_source_sha256``, so it differs
across the rewrite even though every detector byte is identical.

Fixtures are **verbatim copies of real cache manifests**.  Manifests the tests mutate
are clearly derived copies used to prove a check fires.  No detector inference, no
checkpoint, no ``.zarr``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from biohub.reproducibility.cache_identity import (
    RUN_ONLY_PROVENANCE_KEYS,
    compare_caches,
    content_input_digest,
    content_output_digest,
    run_metadata,
)
from biohub.reproducibility.digest import recompute_cache_hash

FIXTURES = Path(__file__).parent / "fixtures" / "reproducibility" / "real_receipts"

# The before/after pair: same sample, same 4 frames, same image bytes; the capture
# implementation differs (adapter fcec103b… before, bd7bfb7a… = commit 19feb13 after).
BEFORE_REWRITE = "smoke_fix_cache_manifest_44b6_0113de3b.json"
AFTER_REWRITE = "smoke_disk_cache_manifest_44b6_0113de3b.json"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def before() -> dict[str, Any]:
    return load(BEFORE_REWRITE)


@pytest.fixture
def after() -> dict[str, Any]:
    return load(AFTER_REWRITE)


# --------------------------------------------------------------------------------------
# The invariant: equal detector inputs must imply equal detector outputs.
# --------------------------------------------------------------------------------------


def test_the_two_manifests_really_are_a_before_after_pair(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Guard the premise: without a genuine pair the comparison below proves nothing."""

    assert before["sample_id"] == after["sample_id"] == "44b6_0113de3b"
    assert before["image_sha256"] == after["image_sha256"]
    assert before["detector_config"]["max_frames"] == after["detector_config"]["max_frames"] == 4
    assert (
        before["provenance"]["adapter_source_sha256"] != after["provenance"]["adapter_source_sha256"]
    ), "the capture implementation is identical, so this is not a before/after pair"


def test_cache_rewrite_preserved_the_detector_output(before: dict[str, Any], after: dict[str, Any]) -> None:
    """The S0 question: did streaming captures to disk change what the detector produced?"""

    comparison = compare_caches(before, after)

    assert comparison["same_content_inputs"] is True
    assert comparison["same_content_outputs"] is True
    assert comparison["detector_content_invariant_holds"] is True
    assert before["node_digest"] == after["node_digest"]
    assert before["edge_digest"] == after["edge_digest"]
    assert (before["node_count"], before["edge_count"]) == (after["node_count"], after["edge_count"])


def test_cache_hash_cannot_certify_that_sameness(before: dict[str, Any], after: dict[str, Any]) -> None:
    """``cache_hash`` differs for byte-identical detector output; it is a run identity."""

    comparison = compare_caches(before, after)

    assert comparison["same_content_outputs"] is True
    assert comparison["same_cache_hash"] is False
    assert set(comparison["differing_run_metadata"]) >= {"adapter_source_sha256", "elapsed_seconds"}


def test_cache_hash_moves_with_wall_clock_time_alone(before: dict[str, Any]) -> None:
    """Demonstrate the contamination directly: only the stopwatch changes."""

    slower = json.loads(json.dumps(before))
    slower["provenance"]["elapsed_seconds"] = float(before["provenance"]["elapsed_seconds"]) + 1.0

    assert recompute_cache_hash(slower) != recompute_cache_hash(before)
    assert content_output_digest(slower) == content_output_digest(before)
    assert content_input_digest(slower) == content_input_digest(before)


def test_content_digests_ignore_every_run_only_field(before: dict[str, Any]) -> None:
    baseline_inputs = content_input_digest(before)
    baseline_outputs = content_output_digest(before)
    for key in sorted(RUN_ONLY_PROVENANCE_KEYS):
        mutated = json.loads(json.dumps(before))
        mutated["provenance"][key] = "changed-by-a-refactor"
        assert content_input_digest(mutated) == baseline_inputs, key
        assert content_output_digest(mutated) == baseline_outputs, key


def test_run_metadata_view_reports_what_changed(before: dict[str, Any], after: dict[str, Any]) -> None:
    assert run_metadata(before)["elapsed_seconds"] != run_metadata(after)["elapsed_seconds"]
    assert set(run_metadata(before)) <= RUN_ONLY_PROVENANCE_KEYS


# --------------------------------------------------------------------------------------
# Test-the-test: break the condition and require the invariant to fail.
# --------------------------------------------------------------------------------------


def test_invariant_fails_when_the_rewrite_would_have_changed_one_node(
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    """This is the S0 alarm: identical inputs, different detector output."""

    drifted = json.loads(json.dumps(after))
    drifted["node_count"] = int(drifted["node_count"]) + 1

    comparison = compare_caches(before, drifted)

    assert comparison["same_content_inputs"] is True
    assert comparison["same_content_outputs"] is False
    assert comparison["detector_content_invariant_holds"] is False


def test_invariant_fails_when_an_artifact_digest_moves(before: dict[str, Any], after: dict[str, Any]) -> None:
    drifted = json.loads(json.dumps(after))
    drifted["edge_digest"] = "f" * 64
    drifted["artifact_digests"]["candidate_edges.npz"] = "f" * 64

    comparison = compare_caches(before, drifted)

    assert comparison["same_content_inputs"] is True
    assert comparison["detector_content_invariant_holds"] is False


def test_invariant_tolerates_different_outputs_when_the_inputs_genuinely_differ(
    before: dict[str, Any],
) -> None:
    """A different detector threshold is allowed to move the output; that is not drift."""

    reconfigured = json.loads(json.dumps(before))
    reconfigured["detector_config"]["det_threshold"] = 0.95
    reconfigured["node_digest"] = "a" * 64
    reconfigured["edge_digest"] = "b" * 64

    comparison = compare_caches(before, reconfigured)

    assert comparison["same_content_inputs"] is False
    assert comparison["same_content_outputs"] is False
    assert comparison["detector_content_invariant_holds"] is True


def test_content_input_digest_moves_for_every_detector_setting(before: dict[str, Any]) -> None:
    baseline = content_input_digest(before)
    for field, replacement in (
        ("det_threshold", 0.95),
        ("pool_kernel_um", 5.0),
        ("edge_threshold", 0.6),
        ("window_size", 3),
        ("det_tta", False),
        ("max_frames", 8),
    ):
        mutated = json.loads(json.dumps(before))
        mutated["detector_config"][field] = replacement
        assert content_input_digest(mutated) != baseline, field


def test_content_input_digest_moves_for_image_and_checkpoint(before: dict[str, Any]) -> None:
    baseline = content_input_digest(before)
    for pointer in ("image_sha256", "checkpoint_sha256", "source_commit"):
        mutated = json.loads(json.dumps(before))
        mutated[pointer] = "0" * 64
        assert content_input_digest(mutated) != baseline, pointer


def test_content_input_digest_moves_when_the_device_changes(before: dict[str, Any]) -> None:
    """CPU and CUDA are not the same experiment; float results may differ."""

    mutated = json.loads(json.dumps(before))
    assert mutated["provenance"]["device"] == "cpu"
    mutated["provenance"]["device"] = "cuda"

    assert content_input_digest(mutated) != content_input_digest(before)


# --------------------------------------------------------------------------------------
# Cross-sample drift: the frozen validation panel is not detector-fixed across samples.
# --------------------------------------------------------------------------------------


def test_panel_samples_were_detected_by_different_capture_implementations() -> None:
    """Records the live defect: one panel, two detector code versions.

    ``44b6_0113de3b`` was captured with adapter ``24ac2cb6…`` (commit ``b31dd76``) and
    ``44b6_0b24845f`` with adapter ``e914af35…`` (commit ``8b03cd6``).  Panel-level
    aggregation treats them as one fixed detector.  Any per-sample content-input digest
    is identical in structure, so nothing in the current machinery objects.
    """

    development = load("full_auto_cache_manifest.json")
    second = load("panel_auto_cache_manifest_44b6_0b24845f.json")

    assert development["sample_id"] != second["sample_id"]
    assert (
        development["provenance"]["adapter_source_sha256"] != second["provenance"]["adapter_source_sha256"]
    ), "panel samples now share a capture implementation; delete this test and the finding"
    # The detector settings are the same, which is what makes the code difference invisible.
    assert development["detector_config"] == second["detector_config"]
    assert development["checkpoint_sha256"] == second["checkpoint_sha256"]


def test_capture_implementations_used_by_persisted_caches_are_enumerable() -> None:
    """Every persisted cache must name the capture implementation that built it."""

    manifests = sorted(FIXTURES.glob("*cache_manifest*.json"))
    assert len(manifests) >= 5
    adapters = {}
    for path in manifests:
        payload = json.loads(path.read_text())
        adapter = payload.get("provenance", {}).get("adapter_source_sha256")
        assert isinstance(adapter, str) and len(adapter) == 64, path.name
        adapters[path.name] = adapter

    # Five persisted caches, five distinct capture implementations. Recorded, but never
    # constrained: nothing requires two caches in one comparison to share one.
    assert len(set(adapters.values())) == len(adapters), adapters
