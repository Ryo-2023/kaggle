from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_benchmark_race.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("benchmark_race_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metrics() -> dict[str, object]:
    return {
        "prediction_node_count": 2,
        "prediction_edge_count": 1,
        "edge_tp": 1,
        "edge_fp": 0,
        "edge_fn": 0,
        "division_tp": 0,
        "division_fp": 0,
        "division_fn": 0,
        "node_recall": 1.0,
        "total_node_ratio": 1.0,
        "edge_jaccard": 1.0,
        "adjusted_edge_jaccard": 1.0,
        "division_jaccard": None,
        "final_score": 1.0,
    }


def test_inference_parsers_have_no_ground_truth_option() -> None:
    cli = _load_cli()

    for command in ("smoke", "infer"):
        with pytest.raises(SystemExit):
            cli._build_parser().parse_args(
                [command, "--image-stem", "sample.zarr", "--ground-truth", "gt.geff"],
            )


def test_evaluate_validates_manifest_before_any_gt_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    events: list[object] = []

    def reject_manifest(path: Path) -> dict[str, object]:
        events.append(("manifest", path))
        raise ValueError("prediction manifest is missing")

    def should_not_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("GT evaluation must not start after invalid manifest")

    monkeypatch.setattr(cli, "validate_prediction_manifest", reject_manifest)
    monkeypatch.setattr(cli, "evaluate_prediction", should_not_evaluate)

    with pytest.raises(ValueError, match="manifest"):
        cli.evaluate_prediction_after_manifest(
            tmp_path / "prediction.geff",
            tmp_path / "sentinel-ground-truth.geff",
            scale=(1.625, 0.40625, 0.40625),
        )

    assert events == [("manifest", tmp_path / "prediction.geff")]


def test_evaluate_writes_official_metrics_and_manifest_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_cli()
    prediction = tmp_path / "prediction.geff"
    ground_truth = tmp_path / "ground_truth.geff"
    metrics_path = tmp_path / "evaluation" / "metrics.json"
    manifest_receipt = {
        "manifest_path": str(tmp_path / "prediction_manifest.json"),
        "directory_sha256": "abc",
        "validated_at": "fixed",
        "validation_action": "validated persisted prediction manifest before opening ground truth",
    }

    monkeypatch.setattr(cli, "validate_prediction_manifest", lambda path: manifest_receipt)
    monkeypatch.setattr(
        cli,
        "evaluate_prediction",
        lambda prediction_path, gt_path, scale, max_distance: _metrics(),
    )

    result = cli.run_evaluate(
        prediction=prediction,
        ground_truth=ground_truth,
        metrics_path=metrics_path,
        scale=(1.625, 0.40625, 0.40625),
        max_distance=7.0,
    )

    assert result["final_score"] == 1.0
    assert result["prediction_manifest_validation_receipt"] == manifest_receipt
    persisted = json.loads(metrics_path.read_text())
    assert persisted["edge_tp"] == 1
    assert persisted["adjusted_edge_jaccard"] == 1.0
    assert persisted["prediction_manifest_validation_receipt"] == manifest_receipt


def test_summarize_command_accepts_root_and_outputs_markdown_and_json(tmp_path: Path) -> None:
    cli = _load_cli()
    output = tmp_path / "report.md"
    summary_json = tmp_path / "summary.json"

    cli.main(
        [
            "summarize",
            "--root",
            str(tmp_path),
            "--output",
            str(output),
            "--summary-json",
            str(summary_json),
        ],
    )

    assert output.is_file()
    assert summary_json.is_file()
    assert "BLOCKED" in output.read_text()
    assert json.loads(summary_json.read_text())["schema_version"] == "benchmark_race.summary.v1"
