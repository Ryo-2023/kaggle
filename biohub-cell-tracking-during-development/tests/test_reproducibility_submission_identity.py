"""A persisted prediction must be identifiable as belonging to its sample.

The packaging step derives the submission's ``dataset`` column from the prediction
file's stem.  Under the current ``<run>/<sample>/<method>.geff`` layout that stem is
the association method, so packaging emits ``dataset=harmonic_v1`` — a structurally
valid CSV that scores zero.  The sample id exists only in the parent directory name,
which packaging never reads.

This is a submission-shaped failure, so it cannot be caught by any metric: the local
score is computed from the GEFF directly and is unaffected.  Only an identity invariant
catches it, and only before a submission is spent.

No detector inference, no checkpoint, no ``.zarr``.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import pytest

from biohub.reproducibility.receipts import prediction_identity_report

_DEFAULT_TREE = (
    Path(__file__).resolve().parents[4]
    / "strong-baseline-v1"
    / "biohub-cell-tracking-during-development"
    / "artifacts"
    / "detector_fixed_race"
)

SAMPLE_IDS = (
    "44b6_0113de3b",
    "44b6_0b24845f",
    "44b6_0c582fdc",
    "44b6_0db75fae",
    "44b6_12dfb391",
)


def race_tree() -> Path:
    override = os.environ.get("BIOHUB_DETECTOR_FIXED_RACE_ROOT")
    candidate = Path(override) if override else _DEFAULT_TREE
    if not candidate.is_dir():
        pytest.skip(f"detector-fixed race artifacts are not reachable: {candidate}")
    return candidate


@lru_cache(maxsize=4)
def _predictions(root: str) -> tuple[tuple[str, str], ...]:
    """Return ``(prediction path, sample id)`` for every persisted prediction."""

    base = Path(root)
    found: list[tuple[str, str]] = []
    for pattern in ("*/*/race_receipt.json", "*/*/*/race_receipt.json"):
        for receipt in sorted(base.glob(pattern)):
            records = json.loads(receipt.read_text())
            if not isinstance(records, list):
                continue
            for record in records:
                found.append((record["prediction_path"], record["sample_id"]))
    return tuple(sorted(set(found)))


# --------------------------------------------------------------------------------------
# The check itself, on synthetic paths.
# --------------------------------------------------------------------------------------


def test_identity_check_accepts_a_sample_named_prediction() -> None:
    report = prediction_identity_report("runs/44b6_0113de3b.geff", "44b6_0113de3b")

    assert report["identity_holds"] is True
    assert report["submission_dataset_column"] == "44b6_0113de3b"


def test_identity_check_accepts_a_sample_prefixed_variant() -> None:
    """A method suffix is fine as long as the sample id leads."""

    report = prediction_identity_report("runs/44b6_0113de3b__harmonic_v1.geff", "44b6_0113de3b")

    assert report["identity_holds"] is True


def test_identity_check_rejects_a_method_named_prediction() -> None:
    """Test-the-test: this is the exact shape that scores zero."""

    report = prediction_identity_report("runs/44b6_0113de3b/harmonic_v1.geff", "44b6_0113de3b")

    assert report["identity_holds"] is False
    assert report["submission_dataset_column"] == "harmonic_v1"
    assert report["sample_id"] == "44b6_0113de3b"


@pytest.mark.parametrize(
    "stem",
    ["official_ilp", "harmonic_v1", "mutual_confidence", "motion_gated", "harmonic_v1_rw_0p10"],
)
def test_every_method_stem_in_use_today_fails_the_identity_check(stem: str) -> None:
    report = prediction_identity_report(f"runs/44b6_0113de3b/{stem}.geff", "44b6_0113de3b")

    assert report["identity_holds"] is False
    assert report["submission_dataset_column"] == stem


def test_parent_directory_is_not_consulted() -> None:
    """Naming the parent after the sample does not rescue a stem-derived column.

    Packaging reads ``geff.stem``; the directory is invisible to it.  This test exists
    so nobody 'fixes' the layout by renaming directories and believes it is solved.
    """

    report = prediction_identity_report("runs/44b6_0113de3b/official_ilp.geff", "44b6_0113de3b")

    assert Path(report["prediction_path"]).parent.name == "44b6_0113de3b"
    assert report["identity_holds"] is False


# --------------------------------------------------------------------------------------
# The predictions actually on disk.
# --------------------------------------------------------------------------------------


def test_no_persisted_prediction_would_package_to_its_sample_id() -> None:
    """Documents the live defect across every prediction in the tree.

    When the writer starts naming predictions after the sample, this test flips to
    green-by-inversion and must be replaced with the positive assertion below it.
    """

    predictions = _predictions(str(race_tree()))
    if not predictions:
        pytest.skip("no persisted predictions on disk")

    reports = [prediction_identity_report(path, sample_id) for path, sample_id in predictions]
    passing = [report for report in reports if report["identity_holds"]]

    assert passing == [], (
        f"{len(passing)} predictions now carry their sample id; switch this file to "
        "asserting identity_holds for every prediction and delete the finding"
    )
    assert len(reports) >= 20, f"expected the full panel sweep, found {len(reports)}"


def test_the_submission_column_that_would_be_emitted_is_a_method_name() -> None:
    predictions = _predictions(str(race_tree()))
    if not predictions:
        pytest.skip("no persisted predictions on disk")

    columns = {prediction_identity_report(path, sample)["submission_dataset_column"] for path, sample in predictions}

    assert columns, "no predictions found"
    assert not (columns & set(SAMPLE_IDS)), f"some columns are sample ids after all: {sorted(columns)}"
    assert "harmonic_v1" in columns


def test_distinct_samples_would_collapse_onto_one_submission_column() -> None:
    """The worst consequence: five samples, one ``dataset`` value.

    Because the stem is the method, every sample's ``harmonic_v1`` prediction packages
    to the same ``dataset``, so rows for different movies become indistinguishable.
    """

    predictions = _predictions(str(race_tree()))
    if not predictions:
        pytest.skip("no persisted predictions on disk")

    by_column: dict[str, set[str]] = {}
    for path, sample in predictions:
        column = prediction_identity_report(path, sample)["submission_dataset_column"]
        by_column.setdefault(column, set()).add(sample)

    collisions = {column: sorted(samples) for column, samples in by_column.items() if len(samples) > 1}
    assert collisions, "no column collapses across samples; the layout may have been fixed"
    assert len(collisions["harmonic_v1"]) >= 4, collisions
