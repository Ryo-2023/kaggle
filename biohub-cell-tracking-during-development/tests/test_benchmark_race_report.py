from __future__ import annotations

import json
from pathlib import Path

from biohub.benchmark_race.report import METHOD_ORDER, collect_summary, render_markdown, write_summary


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _metrics(score: float, *, edge_tp: int = 4) -> dict[str, object]:
    return {
        "prediction_node_count": 10,
        "prediction_edge_count": 9,
        "edge_tp": edge_tp,
        "edge_fp": 1,
        "edge_fn": 2,
        "division_tp": 0,
        "division_fp": 0,
        "division_fn": 0,
        "node_recall": 1.0,
        "total_node_ratio": 1.0,
        "edge_jaccard": 0.8,
        "adjusted_edge_jaccard": score,
        "division_jaccard": None,
        "final_score": score,
    }


def _write_lane(root: Path, method_id: str, score: float, *, baseline: bool = False) -> None:
    if baseline:
        method_root = root / "strong_baseline_v1" / method_id
        metrics_path = method_root / "metrics.json"
    else:
        method_root = root / "multi_method_race" / "methods" / method_id
        metrics_path = root / "multi_method_race" / "evaluation" / method_id / "metrics.json"
    _write_json(
        method_root / "run.json",
        {
            "method_id": method_id,
            "status": "success",
            "runtime_seconds": 12.5,
            "expected_device": "cpu",
            "actual_device": "cpu",
            "prediction_path": f"artifacts/{method_id}/prediction.geff",
            "method_family": "synthetic",
            "detector_id": "detector",
            "linker_id": "linker",
            "version": "test",
        },
    )
    _write_json(metrics_path, _metrics(score))


def test_report_binds_metrics_and_deltas_to_receipts(tmp_path: Path) -> None:
    _write_lane(tmp_path, "official_ilp", 0.5, baseline=True)
    _write_lane(tmp_path, "blob_lap", 0.75)

    summary = collect_summary(tmp_path)
    markdown = render_markdown(summary)

    assert "| `final_score` | 0.5 | BLOCKED | 0.75 | BLOCKED | BLOCKED |" in markdown
    assert "| blob_lap (`blob_lap`) | OK | 0.75 | +0.25 | 12.5 | cpu / cpu |" in markdown
    assert "| `edge_tp` | 4 | BLOCKED | 4 | BLOCKED | BLOCKED |" in markdown
    assert "公式ベースライン" in markdown


def test_report_discloses_missing_lanes_without_fabricated_numbers(tmp_path: Path) -> None:
    _write_lane(tmp_path, "official_ilp", 0.5, baseline=True)

    summary = collect_summary(tmp_path)
    markdown = render_markdown(summary)

    assert set(record["method_id"] for record in summary["methods"]) == set(METHOD_ORDER)
    for method_id in ("harmonic_ilp", "blob_lap", "cc_flow", "motion_lap"):
        record = next(item for item in summary["methods"] if item["method_id"] == method_id)
        assert record["status"] == "BLOCKED"
        assert "未実行" in record["blocked_reason"]
    assert "| `final_score` | 0.5 | BLOCKED | BLOCKED | BLOCKED | BLOCKED |" in markdown
    assert "未実行またはreceipt不足" in markdown
    assert "| harmonic v1 (`harmonic_ilp`) | BLOCKED | BLOCKED | — | 不明 |" in markdown


def test_report_is_deterministic_and_json_is_relative(tmp_path: Path) -> None:
    _write_lane(tmp_path, "official_ilp", 0.5, baseline=True)
    _write_lane(tmp_path, "blob_lap", 0.75)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first_json = tmp_path / "first.json"
    second_json = tmp_path / "second.json"

    write_summary(tmp_path, first, first_json)
    write_summary(tmp_path, second, second_json)

    assert first.read_text() == second.read_text()
    assert first_json.read_text() == second_json.read_text()
    assert str(tmp_path) not in first.read_text()
    assert str(tmp_path) not in first_json.read_text()


def test_partial_metrics_receipt_is_serializable_without_zero_fallback(tmp_path: Path) -> None:
    method_root = tmp_path / "multi_method_race" / "methods" / "blob_lap"
    _write_json(
        method_root / "run.json",
        {"method_id": "blob_lap", "status": "success", "device": "cpu"},
    )
    _write_json(
        tmp_path / "multi_method_race" / "evaluation" / "blob_lap" / "metrics.json",
        {"final_score": 0.25},
    )

    summary_json = tmp_path / "summary.json"
    write_summary(tmp_path, tmp_path / "report.md", summary_json)
    payload = json.loads(summary_json.read_text())
    record = next(item for item in payload["methods"] if item["method_id"] == "blob_lap")
    assert record["metrics"]["final_score"] == 0.25
    assert record["metrics"]["edge_tp"] == "BLOCKED"
