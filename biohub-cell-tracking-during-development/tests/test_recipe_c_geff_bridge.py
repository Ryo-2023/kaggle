"""TDD contract tests for the GT-free Recipe C CSV/GEFF bridge."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from biohub.recipe_c.geff_bridge import (
    CSV_HEADER,
    postprocessed_csv_to_geffs,
    validate_prediction_geff,
    write_prediction_manifest,
)
from biohub.reproducibility.gt_guard import mint_prediction_token, prediction_manifest_path

SAMPLE = "44b6_12dfb391"


def _write_csv(path: Path, rows: list[dict[str, object]], *, header: list[str] | None = None) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header or CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _node(row_id: int, node_id: int, t: int, *, z: int = 1, y: int = 2, x: int = 3) -> dict[str, object]:
    return {
        "id": row_id,
        "dataset": SAMPLE,
        "row_type": "node",
        "node_id": node_id,
        "t": t,
        "z": z,
        "y": y,
        "x": x,
        "source_id": -1,
        "target_id": -1,
    }


def _edge(row_id: int, source_id: int, target_id: int) -> dict[str, object]:
    return {
        "id": row_id,
        "dataset": SAMPLE,
        "row_type": "edge",
        "node_id": -1,
        "t": -1,
        "z": -1,
        "y": -1,
        "x": -1,
        "source_id": source_id,
        "target_id": target_id,
    }


def test_bridge_roundtrips_fork_and_uses_exact_sample_name(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(
        csv_path,
        [_node(0, 0, 0), _node(1, 1, 1), _node(2, 2, 1), _edge(3, 0, 1), _edge(4, 0, 2)],
    )

    written = postprocessed_csv_to_geffs(
        csv_path,
        tmp_path / "predictions",
        sample_ids=(SAMPLE,),
        provenance={"recipe": "C", "ground_truth_included": False},
    )

    prediction = written[SAMPLE]
    assert prediction == tmp_path / "predictions" / f"{SAMPLE}.geff"
    assert prediction.is_dir()
    assert validate_prediction_geff(prediction, SAMPLE, expected_volume_shape_tzyx=(2, 16, 16, 16)) == {
        "nodes": 3,
        "edges": 2,
        "forks": 1,
    }


def test_bridge_rejects_invalid_sentinels_and_noncontiguous_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    bad_node = _node(0, 0, 0)
    bad_node["source_id"] = 7
    _write_csv(csv_path, [bad_node])
    with pytest.raises(ValueError, match="sentinel"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={})

    bad_ids = [_node(0, 1, 0)]
    _write_csv(csv_path, bad_ids)
    with pytest.raises(ValueError, match="contiguous"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions-2", sample_ids=(SAMPLE,), provenance={})


def test_bridge_rejects_non_adjacent_edge_and_degree_violation(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    rows = [_node(0, 0, 0), _node(1, 1, 2), _edge(2, 0, 1)]
    _write_csv(csv_path, rows)
    with pytest.raises(ValueError, match="adjacent"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={})

    rows = [_node(0, 0, 0), _node(1, 1, 1), _node(2, 2, 1), _node(3, 3, 1)]
    rows.extend([_edge(4, 0, 1), _edge(5, 0, 2), _edge(6, 0, 3)])
    _write_csv(csv_path, rows)
    with pytest.raises(ValueError, match="outdegree"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions-2", sample_ids=(SAMPLE,), provenance={})


def test_manifest_is_geff_sibling_and_contains_no_absolute_or_gt_path(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    prediction = postprocessed_csv_to_geffs(
        csv_path,
        tmp_path / "predictions",
        sample_ids=(SAMPLE,),
        provenance={"selection_lock_id": "a" * 64, "ground_truth_inputs": []},
    )[SAMPLE]

    manifest = write_prediction_manifest(
        prediction,
        selection_lock_id="a" * 64,
        provenance={"role": "prediction", "ground_truth_included": False},
    )
    assert manifest == prediction_manifest_path(prediction)
    assert manifest.name == f"{prediction.name}.manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["ground_truth_included"] is False
    assert payload["ground_truth_inputs"] == []
    assert payload["ground_truth_inputs"] == []
    assert "gt/" not in json.dumps(payload).lower()
    created = datetime.fromisoformat(payload["manifest_created_at"])
    assert created.tzinfo == UTC
    assert created <= datetime.now(UTC)
    assert str(tmp_path) not in json.dumps(payload)
    assert mint_prediction_token(prediction).directory_sha256 == payload["directory_sha256"]
