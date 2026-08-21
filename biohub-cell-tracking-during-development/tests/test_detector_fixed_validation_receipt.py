from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from biohub.detector_fixed_race import panel as panel_api

METHODS = ("official_ilp", "harmonic_v1")
SAMPLES = ("sample-a", "sample-b")


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _write_panel(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "panel.json",
        {
            "schema_version": "detector_fixed.panel.v1",
            "samples": [{"sample_id": sample_id} for sample_id in SAMPLES],
        },
    )


def _write_evidence(tmp_path: Path) -> tuple[Path, list[Path], dict[tuple[str, str], dict[str, object]]]:
    panel_path = _write_panel(tmp_path)
    records_by_pair: dict[tuple[str, str], dict[str, object]] = {}
    receipt_paths: list[Path] = []
    for sample_index, sample_id in enumerate(SAMPLES):
        cache_hash = f"{sample_index + 1:064x}"
        receipt_records: list[dict[str, object]] = []
        for method_index, method_id in enumerate(METHODS):
            manifest_path = tmp_path / sample_id / method_id / "prediction_manifest.json"
            prediction_path = manifest_path.parent / f"{method_id}.geff"
            _write_json(
                manifest_path,
                {
                    "sample_id": sample_id,
                    "method_id": method_id,
                    "cache_hash": cache_hash,
                    "ground_truth_included": False,
                    "prediction_path": str(prediction_path),
                },
            )
            score = (sample_index + 1) * 0.2 + method_index * 0.1
            record = {
                "sample_id": sample_id,
                "method_id": method_id,
                "cache_hash": cache_hash,
                "prediction_manifest_path": str(manifest_path),
                "prediction_path": str(prediction_path),
                "prediction_node_count": 3 + sample_index,
                "prediction_edge_count": 2 + method_index,
                "metrics": {
                    "final_score": score,
                    "prediction_manifest_path": str(manifest_path),
                    "prediction_manifest_validated_before_gt": True,
                },
            }
            records_by_pair[(sample_id, method_id)] = record
            receipt_records.append(record)
        receipt_paths.append(_write_json(tmp_path / f"{sample_id}-race_receipt.json", receipt_records))
    return panel_path, receipt_paths, records_by_pair


def test_aggregate_validation_receipts_is_deterministic_and_summarizes_methods(tmp_path: Path) -> None:
    panel_path, receipt_paths, _records_by_pair = _write_evidence(tmp_path)

    first = panel_api.aggregate_validation_receipts(
        panel_path=panel_path,
        receipt_paths=list(reversed(receipt_paths)),
        methods=METHODS,
    )
    second = panel_api.aggregate_validation_receipts(
        panel_path=panel_path,
        receipt_paths=receipt_paths,
        methods=METHODS,
    )

    assert first == second
    assert first["schema_version"] == "detector_fixed.validation_receipt.v1"
    assert first["panel_path"] == str(panel_path)
    assert len(first["panel_sha256"]) == 64
    assert first["samples"] == list(SAMPLES)
    assert first["methods"] == list(METHODS)
    assert [
        (record["sample_id"], record["method_id"])
        for record in first["records"]
    ] == [(sample, method) for sample in SAMPLES for method in METHODS]
    assert all("race_receipt_path" in record for record in first["records"])
    assert all(len(record["race_receipt_sha256"]) == 64 for record in first["records"])
    assert first["summary"]["official_ilp"]["n"] == 2
    assert first["summary"]["official_ilp"]["mean_final_score"] == pytest.approx(0.3)
    assert first["summary"]["harmonic_v1"]["mean_final_score"] == pytest.approx(0.4)
    assert first["summary"]["harmonic_v1"]["delta_vs_official"] == pytest.approx(0.1)
    assert first["summary"]["harmonic_v1"]["improve_count"] == 2
    assert first["summary"]["harmonic_v1"]["harm_count"] == 0
    assert first["failed_samples"] == []
    assert first["ground_truth_usage"] == "official metric evaluation only"


@pytest.mark.parametrize("failure", ("missing", "duplicate"))
def test_aggregate_validation_receipts_rejects_missing_or_duplicate_pair(
    tmp_path: Path,
    failure: str,
) -> None:
    panel_path, receipt_paths, records_by_pair = _write_evidence(tmp_path)
    first_receipt = json.loads(receipt_paths[0].read_text())
    if failure == "missing":
        first_receipt.pop()
    else:
        first_receipt.append(records_by_pair[(SAMPLES[0], METHODS[0])])
    receipt_paths[0].write_text(json.dumps(first_receipt))

    with pytest.raises(ValueError, match=failure):
        panel_api.aggregate_validation_receipts(
            panel_path=panel_path,
            receipt_paths=receipt_paths,
            methods=METHODS,
        )


def test_aggregate_validation_receipts_rejects_per_sample_cache_hash_mismatch(tmp_path: Path) -> None:
    panel_path, receipt_paths, records_by_pair = _write_evidence(tmp_path)
    mismatched_hash = "f" * 64
    record = records_by_pair[(SAMPLES[0], METHODS[1])]
    manifest_path = Path(record["prediction_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest["cache_hash"] = mismatched_hash
    manifest_path.write_text(json.dumps(manifest))
    record["cache_hash"] = mismatched_hash
    record["metrics"]["prediction_manifest_path"] = str(manifest_path)
    receipt_records = json.loads(receipt_paths[0].read_text())
    receipt_records[1] = record
    receipt_paths[0].write_text(json.dumps(receipt_records))

    with pytest.raises(ValueError, match="cache_hash"):
        panel_api.aggregate_validation_receipts(
            panel_path=panel_path,
            receipt_paths=receipt_paths,
            methods=METHODS,
        )


@pytest.mark.parametrize(
    ("manifest_ground_truth", "validated_before_gt"),
    ((True, True), (False, False)),
)
def test_aggregate_validation_receipts_rejects_gt_contaminated_evidence(
    tmp_path: Path,
    manifest_ground_truth: bool,
    validated_before_gt: bool,
) -> None:
    panel_path, receipt_paths, records_by_pair = _write_evidence(tmp_path)
    record = records_by_pair[(SAMPLES[0], METHODS[0])]
    manifest_path = Path(record["prediction_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest["ground_truth_included"] = manifest_ground_truth
    manifest_path.write_text(json.dumps(manifest))
    record["metrics"]["prediction_manifest_validated_before_gt"] = validated_before_gt
    receipt_records = json.loads(receipt_paths[0].read_text())
    receipt_records[0] = record
    receipt_paths[0].write_text(json.dumps(receipt_records))

    with pytest.raises(ValueError, match=r"ground_truth|validated_before_gt"):
        panel_api.aggregate_validation_receipts(
            panel_path=panel_path,
            receipt_paths=receipt_paths,
            methods=METHODS,
        )


def test_aggregate_accepts_actual_shape_legacy_manifest_without_sample_id(tmp_path: Path) -> None:
    panel_path, receipt_paths, records_by_pair = _write_evidence(tmp_path)
    record = records_by_pair[(SAMPLES[0], METHODS[0])]
    manifest_path = Path(record["prediction_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("sample_id")
    manifest_path.write_text(json.dumps(manifest))
    receipt_records = json.loads(receipt_paths[0].read_text())
    receipt_records[0] = record
    receipt_paths[0].write_text(json.dumps(receipt_records))

    result = panel_api.aggregate_validation_receipts(
        panel_path=panel_path,
        receipt_paths=receipt_paths,
        methods=METHODS,
    )

    assert result["records"][0]["sample_id"] == SAMPLES[0]


@pytest.mark.parametrize("path_case", ("manifest_missing", "record_missing", "mismatch"))
def test_aggregate_validation_receipts_requires_matching_prediction_path(
    tmp_path: Path,
    path_case: str,
) -> None:
    panel_path, receipt_paths, records_by_pair = _write_evidence(tmp_path)
    record = records_by_pair[(SAMPLES[0], METHODS[0])]
    manifest_path = Path(record["prediction_manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    if path_case == "manifest_missing":
        manifest.pop("prediction_path")
    elif path_case == "mismatch":
        manifest["prediction_path"] = str(tmp_path / "wrong" / "official_ilp.geff")
    manifest_path.write_text(json.dumps(manifest))
    if path_case == "record_missing":
        record.pop("prediction_path")
    elif path_case == "mismatch":
        record["prediction_path"] = str(tmp_path / "another" / "official_ilp.geff")
    receipt_records = json.loads(receipt_paths[0].read_text())
    receipt_records[0] = record
    receipt_paths[0].write_text(json.dumps(receipt_records))

    with pytest.raises(ValueError, match="prediction_path"):
        panel_api.aggregate_validation_receipts(
            panel_path=panel_path,
            receipt_paths=receipt_paths,
            methods=METHODS,
        )


def test_aggregate_validation_receipts_requires_official_control(tmp_path: Path) -> None:
    panel_path, receipt_paths, _records_by_pair = _write_evidence(tmp_path)
    for receipt_path in receipt_paths:
        records = json.loads(receipt_path.read_text())
        receipt_path.write_text(
            json.dumps([record for record in records if record["method_id"] == "harmonic_v1"])
        )

    with pytest.raises(ValueError, match="official_ilp"):
        panel_api.aggregate_validation_receipts(
            panel_path=panel_path,
            receipt_paths=receipt_paths,
            methods=("harmonic_v1",),
        )


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_detector_fixed_race.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("detector_fixed_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_aggregate_panel_receipts_parser_accepts_repeated_receipts() -> None:
    cli = _load_cli()
    args = cli._build_parser().parse_args(
        [
            "aggregate-panel-receipts",
            "--panel",
            "panel.json",
            "--receipt",
            "sample-a.json",
            "--receipt",
            "sample-b.json",
            "--methods",
            "official_ilp,harmonic_v1",
            "--output",
            "aggregate.json",
        ]
    )
    assert args.receipt == [Path("sample-a.json"), Path("sample-b.json")]
    assert args.methods == METHODS


@pytest.mark.parametrize("missing", ("--panel", "--receipt", "--methods", "--output"))
def test_aggregate_panel_receipts_parser_requires_fields(missing: str) -> None:
    cli = _load_cli()
    values = {
        "--panel": "panel.json",
        "--receipt": "receipt.json",
        "--methods": "official_ilp",
        "--output": "aggregate.json",
    }
    argv = ["aggregate-panel-receipts"]
    for option, value in values.items():
        if option != missing:
            argv.extend((option, value))

    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(argv)
