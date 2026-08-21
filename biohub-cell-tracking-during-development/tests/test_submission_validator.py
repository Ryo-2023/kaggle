"""Tests for the standalone submission validator.

Every input here is SYNTHETIC (``biohub.submission.fixture``); no competition
data, model output or ground truth is involved.  See ``AGENTS.md`` section 8.
"""

from __future__ import annotations

import pytest

from biohub.submission.fixture import (
    BREAKAGES,
    SYNTHETIC_DATASETS,
    write_broken_submission,
    write_valid_submission,
)
from biohub.submission.schema import SUBMISSION_COLUMNS
from biohub.submission.validator import (
    ERROR,
    WARN,
    validate_submission,
)


def _codes(report, severity=None):
    return {f.code for f in report.findings if severity is None or f.severity == severity}


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


def test_valid_synthetic_submission_passes(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    report = validate_submission(csv, expected_datasets=SYNTHETIC_DATASETS)
    assert report.ok, report.render()
    assert report.n_rows > 0
    assert set(report.datasets) == set(SYNTHETIC_DATASETS)


def test_valid_submission_reports_division(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv", with_division=True)
    report = validate_submission(csv, expected_datasets=SYNTHETIC_DATASETS)
    assert all(s.n_forks >= 1 for s in report.datasets.values())
    assert "NO_DIVISIONS" not in _codes(report)


def test_missing_divisions_warns_and_can_be_fatal(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv", with_division=False)
    report = validate_submission(csv, expected_datasets=SYNTHETIC_DATASETS)
    assert "NO_DIVISIONS" in _codes(report, WARN)
    assert report.ok  # a warning must not block

    strict = validate_submission(
        csv, expected_datasets=SYNTHETIC_DATASETS, require_divisions=True
    )
    assert "NO_DIVISIONS" in _codes(strict, ERROR)
    assert not strict.ok


def test_runs_with_no_expected_dataset_list(tmp_path):
    """The validator must work with no test data present."""
    csv = write_valid_submission(tmp_path / "submission.csv")
    report = validate_submission(csv)
    assert report.ok
    assert "COVERAGE_UNCHECKED" in _codes(report)


def test_validator_imports_no_third_party(tmp_path):
    """The validation path must stay standard-library only."""
    import ast
    from pathlib import Path

    import biohub.submission.validator as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    stdlib = {
        "csv", "json", "math", "collections", "dataclasses", "pathlib",
        "typing", "__future__",
    }
    top_level_imports: set[str] = set()
    for node in tree.body:  # module level only; optional deps are function-local
        if isinstance(node, ast.Import):
            top_level_imports |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level_imports.add(node.module.split(".")[0])
    assert top_level_imports <= stdlib, f"non-stdlib top-level imports: {top_level_imports - stdlib}"


# --------------------------------------------------------------------------- #
# file / schema level
# --------------------------------------------------------------------------- #


def test_missing_file(tmp_path):
    report = validate_submission(tmp_path / "nope.csv")
    assert "FILE_MISSING" in _codes(report, ERROR)


def test_empty_file(tmp_path):
    p = tmp_path / "submission.csv"
    p.write_bytes(b"")
    assert "FILE_EMPTY" in _codes(validate_submission(p), ERROR)


def test_header_only(tmp_path):
    p = tmp_path / "submission.csv"
    p.write_text(",".join(SUBMISSION_COLUMNS) + "\n", encoding="utf-8")
    assert "NO_ROWS" in _codes(validate_submission(p), ERROR)


def test_wrong_filename_warns(tmp_path):
    csv = write_valid_submission(tmp_path / "candidate.csv")
    assert "FILE_NAME" in _codes(validate_submission(csv), WARN)


# --------------------------------------------------------------------------- #
# one test per deliberate breakage
# --------------------------------------------------------------------------- #

_EXPECTED_CODE = {
    "bad_header": "HEADER",
    "non_contiguous_id": "ID_INDEX",
    "node_row_sentinel": "NODE_ROW_SENTINEL",
    "edge_row_sentinel": "EDGE_ROW_SENTINEL",
    "dangling_edge": "EDGE_DANGLING",
    "self_loop": "EDGE_SELF_LOOP",
    "duplicate_edge": "EDGE_DUPLICATE",
    "backward_edge": "EDGE_NOT_FORWARD",
    "duplicate_node_id": "NODE_ID_DUPLICATE",
    "micrometre_coords": "COORDS_NOT_VOXEL",
    "out_of_bounds": "COORDS_OUT_OF_BOUNDS",
    "empty_dataset_edges": "DATASET_NO_EDGES",
    "float_coords": "NON_INTEGER",
    "wide_fork": "OUT_DEGREE_TRUNCATED",
}


def test_every_breakage_is_covered():
    assert set(_EXPECTED_CODE) == set(BREAKAGES)


@pytest.mark.parametrize("breakage", sorted(BREAKAGES))
def test_breakage_is_detected(tmp_path, breakage):
    csv = write_broken_submission(tmp_path / "submission.csv", breakage)
    report = validate_submission(csv, expected_datasets=SYNTHETIC_DATASETS)
    expected = _EXPECTED_CODE[breakage]
    assert expected in _codes(report), (
        f"{breakage}: expected {expected}, got {sorted(_codes(report))}\n{report.render()}"
    )
    if expected == "OUT_DEGREE_TRUNCATED":
        assert expected in _codes(report, WARN)
    else:
        assert not report.ok, f"{breakage} should block submission\n{report.render()}"


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #


def test_missing_dataset_is_fatal(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    report = validate_submission(
        csv, expected_datasets=[*SYNTHETIC_DATASETS, "SYNTHETIC_cccc_00000003"]
    )
    assert "DATASET_MISSING" in _codes(report, ERROR)


def test_unexpected_dataset_is_fatal(tmp_path):
    """A train dataset leaking into the submission looks exactly like this."""
    csv = write_valid_submission(tmp_path / "submission.csv")
    report = validate_submission(csv, expected_datasets=[SYNTHETIC_DATASETS[0]])
    assert "DATASET_UNEXPECTED" in _codes(report, ERROR)


def test_datasets_from_zarr_dir(tmp_path):
    from biohub.submission.validator import datasets_from_zarr_dir

    for name in ("b_movie", "a_movie"):
        (tmp_path / f"{name}.zarr").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert datasets_from_zarr_dir(tmp_path) == ["a_movie", "b_movie"]


# --------------------------------------------------------------------------- #
# ground-truth leakage
# --------------------------------------------------------------------------- #


def test_gt_sized_submission_is_fatal(tmp_path):
    """A submission the size of sparse GT is not a detector output."""
    csv = write_valid_submission(tmp_path / "submission.csv")
    report = validate_submission(csv)
    ds = SYNTHETIC_DATASETS[0]
    n = report.datasets[ds].n_node_rows
    gt = {ds: {(0, 1, 2, i) for i in range(n)}}  # same order of magnitude
    report = validate_submission(csv, ground_truth_nodes=gt)
    assert "GT_SIZED_SUBMISSION" in _codes(report, ERROR)


def test_gt_exact_coincidence_is_fatal(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    base = validate_submission(csv)
    ds = SYNTHETIC_DATASETS[0]

    import csv as _csv

    coords = set()
    with open(csv, newline="", encoding="utf-8") as fh:
        for row in _csv.DictReader(fh):
            if row["dataset"] == ds and row["row_type"] == "node":
                coords.add((int(row["t"]), int(row["z"]), int(row["y"]), int(row["x"])))

    # A handful of GT nodes, all of which the submission reproduces exactly, but
    # far fewer than the prediction count so the size check does not fire.
    gt = {ds: set(sorted(coords)[:4])}
    n_pred = base.datasets[ds].n_node_rows
    assert n_pred > 4 * 4, "fixture too small for this test"
    report = validate_submission(csv, ground_truth_nodes=gt)
    assert "GT_EXACT_COINCIDENCE" in _codes(report, ERROR)


def test_independent_prediction_passes_gt_check(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    ds = SYNTHETIC_DATASETS[0]
    # GT nowhere near the synthetic tracks, and far sparser than the prediction.
    gt = {ds: {(0, 60, 200, 200 + i) for i in range(4)}}
    report = validate_submission(csv, ground_truth_nodes=gt)
    assert "GT_LEAK_CHECK_PASSED" in _codes(report)
    assert report.ok, report.render()


# --------------------------------------------------------------------------- #
# report plumbing
# --------------------------------------------------------------------------- #


def test_report_json_roundtrip(tmp_path):
    import json

    from biohub.submission.validator import write_report_json

    csv = write_valid_submission(tmp_path / "submission.csv")
    report = validate_submission(csv, expected_datasets=SYNTHETIC_DATASETS)
    out = write_report_json(report, tmp_path / "report.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["n_rows"] == report.n_rows
    assert set(payload["datasets"]) == set(SYNTHETIC_DATASETS)


def test_pass_does_not_imply_permission_to_submit(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    rendered = validate_submission(csv, expected_datasets=SYNTHETIC_DATASETS).render()
    assert "NOT permission" in rendered
