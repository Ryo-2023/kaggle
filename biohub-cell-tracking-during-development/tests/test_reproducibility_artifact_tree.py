"""Sweep the whole persisted artifact tree, so new runs cannot reintroduce old drift.

The fixture-based tests pin what was true when they were written.  This file walks
every ``race_receipt.json`` and every detector cache manifest that exists *now*, so a
race Codex runs tomorrow is audited by the same invariants without anyone updating a
fixture.

It skips cleanly when the read-only Codex worktree is not reachable, and it never
opens a ``.zarr``, a checkpoint, or a ``.npz``: manifests and receipts only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

from _race_tree import find_race_tree, unreachable_message
from biohub.reproducibility.cache_identity import compare_caches, content_input_digest
from biohub.reproducibility.gt_guard import GroundTruthOrderingError, ordering_holds
from biohub.reproducibility.receipts import detector_invariance_report


def race_tree() -> Path:
    tree = find_race_tree()
    if tree is None:
        pytest.skip(unreachable_message())
    return tree


# Explicit globs, not rglob: the tree holds multi-gigabyte cache and GEFF directories,
# and walking them costs a minute per test.  Receipts live at a known depth.
_RECEIPT_GLOBS = ("*/*/race_receipt.json", "*/*/*/race_receipt.json")
_CACHE_GLOBS = ("*/cache/*/manifest.json", "*/*/cache/*/manifest.json")


@lru_cache(maxsize=4)
def _receipts(root: str) -> tuple[tuple[Path, str], ...]:
    base = Path(root)
    paths = sorted({path for pattern in _RECEIPT_GLOBS for path in base.glob(pattern)})
    return tuple((path, path.read_text()) for path in paths)


@lru_cache(maxsize=4)
def _caches(root: str) -> tuple[tuple[str, str], ...]:
    base = Path(root)
    paths = sorted({path for pattern in _CACHE_GLOBS for path in base.glob(pattern)})
    return tuple((str(path.parent.relative_to(base)), path.read_text()) for path in paths)


def race_receipts(root: Path) -> list[tuple[Path, list[dict[str, Any]]]]:
    found = []
    for path, text in _receipts(str(root)):
        payload = json.loads(text)
        if isinstance(payload, list) and payload:
            found.append((path, payload))
    if not found:
        pytest.skip("no race receipts on disk")
    return found


def cache_manifests(root: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for name, text in _caches(str(root)):
        payload = json.loads(text)
        if "cache_hash" in payload and "detector_config" in payload:
            found[name] = payload
    if not found:
        pytest.skip("no detector cache manifests on disk")
    return found


# --------------------------------------------------------------------------------------
# Detector invariance across every run that exists.
# --------------------------------------------------------------------------------------


def test_every_run_of_one_sample_quotes_one_detector_cache() -> None:
    """Changing the association method must never change the detector digest."""

    by_sample: dict[str, dict[str, str]] = defaultdict(dict)
    for path, records in race_receipts(race_tree()):
        for record in records:
            key = f"{path.parent.parent.name}/{record['method_id']}"
            by_sample[record["sample_id"]][key] = record["cache_hash"]

    offenders = {
        sample: runs for sample, runs in by_sample.items() if len({*runs.values()}) != 1
    }
    assert offenders == {}, f"a sample's detector moved between runs: {offenders}"


def test_each_race_receipt_is_internally_detector_fixed() -> None:
    for path, records in race_receipts(race_tree()):
        report = detector_invariance_report(records)
        assert report["invariant_holds"] is True, f"{path}: {report['cache_hash_by_method']}"


def test_every_quoted_cache_digest_belongs_to_a_cache_that_exists() -> None:
    """A receipt quoting a digest with no matching cache cannot be replayed."""

    root = race_tree()
    known = {manifest["cache_hash"] for manifest in cache_manifests(root).values()}
    quoted = {record["cache_hash"] for _, records in race_receipts(root) for record in records}

    assert quoted <= known, f"receipts quote caches that are not on disk: {sorted(quoted - known)}"


def test_caches_with_identical_inputs_have_identical_outputs() -> None:
    """The drift alarm: same detector inputs, different detector bytes is S0."""

    manifests = cache_manifests(race_tree())
    by_inputs: dict[str, list[str]] = defaultdict(list)
    for name, manifest in manifests.items():
        by_inputs[content_input_digest(manifest)].append(name)

    for names in by_inputs.values():
        if len(names) < 2:
            continue
        reference = manifests[names[0]]
        for name in names[1:]:
            comparison = compare_caches(reference, manifests[name])
            assert comparison["detector_content_invariant_holds"] is True, (
                f"{names[0]} and {name} share detector inputs but produced different output"
            )


def test_panel_samples_do_not_all_share_one_capture_implementation() -> None:
    """Documents the live defect: one panel, more than one detector code version.

    Delete this test — and the finding — once every full-length cache in the panel is
    rebuilt with a single ``adapter_source_sha256``.
    """

    manifests = cache_manifests(race_tree())
    full_length = {
        name: manifest
        for name, manifest in manifests.items()
        if manifest.get("detector_config", {}).get("max_frames") is None
    }
    if len(full_length) < 2:
        pytest.skip("fewer than two full-length caches on disk")

    adapters = {
        name: manifest.get("provenance", {}).get("adapter_source_sha256") for name, manifest in full_length.items()
    }
    assert len({*adapters.values()}) > 1, (
        f"panel caches now share one capture implementation; remove this test: {adapters}"
    )


# --------------------------------------------------------------------------------------
# Prediction manifests: one per prediction, and re-openable afterwards.
# --------------------------------------------------------------------------------------


def test_a_directory_with_several_predictions_keeps_only_one_manifest() -> None:
    """The clobber, measured across the whole tree.

    ``write_prediction_manifest`` targets ``<parent>/prediction_manifest.json``, so a
    directory holding N predictions ends up with one manifest describing the last one.
    Directories that happen to hold a single prediction escape by accident, not design.
    """

    damaged: dict[str, dict[str, Any]] = {}
    for path, records in race_receipts(race_tree()):
        directory = path.parent
        predictions = sorted(item.name for item in directory.glob("*.geff"))
        if len(predictions) < 2:
            continue
        shared = directory / "prediction_manifest.json"
        owner = json.loads(shared.read_text()).get("method_id") if shared.is_file() else None
        damaged[str(directory)] = {
            "predictions": len(predictions),
            "surviving_manifests": len(sorted(directory.glob("*prediction_manifest*.json"))),
            "described": owner,
            "orphaned": sorted({record["method_id"] for record in records} - {owner}),
        }

    for directory, detail in damaged.items():
        assert detail["surviving_manifests"] == 1, directory
        assert detail["orphaned"], directory
    assert damaged, (
        "no multi-prediction directory found; if the writer now emits one manifest per "
        "prediction, delete this test and the finding"
    )


def test_the_clobber_was_worked_around_by_layout_not_fixed_in_the_writer() -> None:
    """One method per directory avoids the collision; the writer still causes it.

    ``write_prediction_manifest`` still targets ``<parent>/prediction_manifest.json``.
    Newer runs escape only because each writes a single prediction into its own
    directory — a convention nothing enforces.  The older multi-prediction directories
    were never regenerated and are still missing three manifests apiece.
    """

    single = 0
    still_broken: list[str] = []
    for path, records in race_receipts(race_tree()):
        directory = path.parent
        predictions = sorted(directory.glob("*.geff"))
        if len(predictions) == 1:
            single += 1
            continue
        still_broken.append(str(directory.relative_to(race_tree())))
        assert len(sorted(directory.glob("*prediction_manifest*.json"))) == 1
        assert len(records) > 1

    assert single >= 20, f"expected the per-method layout to dominate, found {single}"
    assert still_broken, (
        "every multi-prediction directory is gone; if the writer now emits one manifest "
        "per prediction, delete this test and the finding"
    )


def test_no_metrics_receipt_can_prove_its_own_ordering() -> None:
    """``prediction_manifest_validated_before_gt`` is unfalsifiable as persisted.

    The manifest's ``manifest_created_at`` never reaches the metrics payload, so the
    ordering cannot be rechecked from a saved receipt even in principle.  Once the
    field is propagated, this test flips and must be replaced by the positive check
    below it.
    """

    unprovable = 0
    total = 0
    for _, records in race_receipts(race_tree()):
        for record in records:
            metrics = record["metrics"]
            total += 1
            assert metrics.get("prediction_manifest_validated_before_gt") is True
            if "prediction_manifest_created_at" not in metrics:
                unprovable += 1

    assert total > 0
    assert unprovable == total, (
        "some receipts now carry manifest_created_at; switch this file to asserting "
        "ordering_holds(created_at, validated_at) for those receipts"
    )


def test_the_ordering_check_is_ready_for_the_field_once_it_is_written() -> None:
    """Prove the replacement check works, so adopting it is a one-line change."""

    _, records = race_receipts(race_tree())[0]
    validated_at = records[0]["metrics"]["prediction_manifest_validated_at"]

    assert ordering_holds("2020-01-01T00:00:00+00:00", validated_at) is True
    assert ordering_holds("2099-01-01T00:00:00+00:00", validated_at) is False
    with pytest.raises(GroundTruthOrderingError):
        ordering_holds(None, validated_at)
