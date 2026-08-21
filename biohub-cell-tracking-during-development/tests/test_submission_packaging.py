"""Tests for submission packaging.

Inputs are SYNTHETIC (``biohub.submission.fixture``) or tiny graphs built in the
test itself. No competition data, model output or ground truth is used. See
``AGENTS.md`` section 8.
"""

from __future__ import annotations

import json

import pytest

from biohub.submission.fixture import SYNTHETIC_DATASETS, write_valid_submission
from biohub.submission.packaging import (
    PackagedDataset,
    check_roundtrip,
    collect_method_geffs,
    roundtrip_from_csv,
    write_provenance,
)
from biohub.submission.schema import SUBMISSION_COLUMNS
from biohub.submission.validator import validate_submission


# --------------------------------------------------------------------------- #
# round-trip (stdlib only)
# --------------------------------------------------------------------------- #


def test_roundtrip_recovers_counts(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    got = roundtrip_from_csv(csv)
    report = validate_submission(csv)
    assert set(got) == set(SYNTHETIC_DATASETS)
    for ds, (n_nodes, n_edges) in got.items():
        assert n_nodes == report.datasets[ds].n_node_rows
        assert n_edges == report.datasets[ds].n_edge_rows


def test_roundtrip_rejects_dangling_reference(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    with csv.open("a", encoding="utf-8") as fh:
        fh.write(f"999999,{SYNTHETIC_DATASETS[0]},edge,-1,-1,-1,-1,-1,0,424242\n")
    with pytest.raises(ValueError, match="unknown node ids"):
        roundtrip_from_csv(csv)


def test_check_roundtrip_detects_count_mismatch(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    packaged = [PackagedDataset(SYNTHETIC_DATASETS[0], "x.geff", 1, 1)]
    result = check_roundtrip(csv, packaged)
    assert not result.matches_source
    assert any("!=" in m for m in result.mismatches)
    assert any("unexpected dataset" in m for m in result.mismatches)


def test_check_roundtrip_passes_when_consistent(tmp_path):
    csv = write_valid_submission(tmp_path / "submission.csv")
    got = roundtrip_from_csv(csv)
    packaged = [PackagedDataset(ds, f"{ds}.geff", n, e) for ds, (n, e) in got.items()]
    assert check_roundtrip(csv, packaged).matches_source


# --------------------------------------------------------------------------- #
# per-sample collection: the <run>/<sample>/<method>.geff footgun
# --------------------------------------------------------------------------- #


def test_collect_method_geffs_maps_sample_names_not_method_names(tmp_path):
    """Dataset keys must be the SAMPLE names, never the method names.

    Pointing the upstream converter at a sample directory would emit
    ``dataset=harmonic_v1``, producing a valid CSV that scores zero.
    """
    run = tmp_path / "run"
    for sample in ("6bba_05db0fb1", "44b6_0113de3b"):
        for method in ("harmonic_v1", "official_ilp"):
            (run / sample / f"{method}.geff").mkdir(parents=True)

    got = collect_method_geffs(run, "harmonic_v1")
    assert set(got) == {"44b6_0113de3b", "6bba_05db0fb1"}
    for sample, path in got.items():
        assert path.name == "harmonic_v1.geff"
        assert path.parent.name == sample


def test_collect_method_geffs_refuses_partial_coverage(tmp_path):
    """A sample missing the method must raise, not be silently skipped."""
    run = tmp_path / "run"
    (run / "sample_a" / "harmonic_v1.geff").mkdir(parents=True)
    (run / "sample_b" / "official_ilp.geff").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="sample_b"):
        collect_method_geffs(run, "harmonic_v1")


def test_collect_method_geffs_empty_run_dir(tmp_path):
    (tmp_path / "run").mkdir()
    with pytest.raises(FileNotFoundError, match="no per-sample directories"):
        collect_method_geffs(tmp_path / "run", "harmonic_v1")


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #


def test_provenance_records_no_ground_truth_and_no_submission(tmp_path):
    csv = tmp_path / "submission.csv"
    csv.write_text("placeholder", encoding="utf-8")
    packaged = [
        PackagedDataset("44b6_0113de3b", "a.geff", 26301, 24205),
        PackagedDataset("6bba_05db0fb1", "b.geff", 100, 90),
    ]
    out = write_provenance(csv, packaged, method_id="harmonic_v1")
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["ground_truth_included"] is False
    assert payload["ground_truth_inputs"] == []
    assert payload["submitted_to_kaggle"] is False
    assert payload["method_id"] == "harmonic_v1"
    assert payload["total_nodes"] == 26401
    assert payload["total_edges"] == 24295
    assert payload["format_source"]["commit"].startswith("075fc5f5")


# --------------------------------------------------------------------------- #
# polars / tracksdata paths
# --------------------------------------------------------------------------- #


def test_write_submission_csv_refuses_empty_input(tmp_path):
    from biohub.submission.packaging import write_submission_csv

    with pytest.raises(ValueError, match="refusing to write an empty submission"):
        write_submission_csv({}, tmp_path / "submission.csv")


def test_write_submission_csv_reports_missing_geff(tmp_path):
    from biohub.submission.packaging import write_submission_csv

    with pytest.raises(FileNotFoundError, match="missing prediction GEFF"):
        write_submission_csv({"ds": tmp_path / "absent.geff"}, tmp_path / "submission.csv")


def test_graph_to_rows_matches_upstream_contract():
    """z/y/x must be rounded to int and unused fields set to -1."""
    pl = pytest.importorskip("polars")
    from biohub.submission.packaging import graph_to_rows

    class _FakeGraph:
        def node_attrs(self):
            return pl.DataFrame(
                {
                    "node_id": [0, 1],
                    "t": [0, 1],
                    "z": [1.4, 2.6],
                    "y": [8.5, 9.2],
                    "x": [52.0, 55.7],
                }
            )

        def edge_attrs(self):
            return pl.DataFrame({"source_id": [0], "target_id": [1]})

    rows = graph_to_rows(_FakeGraph(), "SYNTHETIC_ds")
    assert tuple(rows.columns) == SUBMISSION_COLUMNS[1:]

    nodes = rows.filter(pl.col("row_type") == "node")
    assert nodes["z"].to_list() == [1, 3]
    assert nodes["y"].to_list() == [8, 9]
    assert nodes["x"].to_list() == [52, 56]
    assert nodes["source_id"].to_list() == [-1, -1]
    assert nodes["target_id"].to_list() == [-1, -1]

    edges = rows.filter(pl.col("row_type") == "edge")
    for col in ("node_id", "t", "z", "y", "x"):
        assert edges[col].to_list() == [-1]
    assert edges["source_id"].to_list() == [0]
    assert edges["target_id"].to_list() == [1]
