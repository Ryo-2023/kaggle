from __future__ import annotations

from pathlib import Path

REPORT = Path(__file__).parents[1] / "docs" / "results" / "detector_fixed_association_race.md"


def test_detector_fixed_report_contains_contract_and_independent_blob_nms_comparison() -> None:
    report = REPORT.read_text()

    for phrase in (
        "結論",
        "validation",
        "公式metric",
        "既知の問題",
        "再現",
        "ground_truth_included=false",
        "race_receipt.json",
        "prediction_manifest.json",
        "artifacts/detector_fixed_race/panel.json",
        "独立 blob NMS 比較",
        "detector-fixed lane ではない",
        "NMS 3.0 Final Score: 0.9140773262846648",
        "NMS 3.5 Final Score: 0.9172062183593925",
        "Delta: +0.0031288920747277",
        "artifacts/performance_experiments/blob_lap_nms35/metrics.json",
    ):
        assert phrase in report
