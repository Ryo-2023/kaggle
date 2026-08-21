from __future__ import annotations

from pathlib import Path

REPORT = Path(__file__).parents[1] / "docs" / "results" / "detector_fixed_association_race.md"
_NMS_HEADING = "## 12. 独立 blob NMS 比較" + chr(0xFF08) + "detector-fixed lane ではない" + chr(0xFF09)


def _section(report: str, heading: str) -> str:
    assert report.count(heading) == 1, f"expected one section headed {heading!r}"
    start = report.index(heading) + len(heading)
    end = report.find("\n## ", start)
    return report[start:] if end == -1 else report[start:end]


def _nms_receipt_rows(section: str) -> dict[str, tuple[str, str]]:
    lines = [line.strip() for line in section.splitlines()]
    header = "| variant | official metric receipt | field/value |"
    try:
        start = lines.index(header)
    except ValueError as exc:
        raise AssertionError("blob NMS receipt table is missing") from exc
    assert lines[start + 1] == "|---|---|---|"

    rows: dict[str, tuple[str, str]] = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == 3, f"expected three NMS receipt cells: {line!r}"
        rows[cells[0]] = (cells[1], cells[2])
    return rows


def test_detector_fixed_report_has_required_japanese_sections() -> None:
    report = REPORT.read_text(encoding="utf-8")

    for heading in (
        "## 0. 結論",
        "## 3. validation panel",
        "## 5. 公式metric結果",
        "## 7. 再現コマンド",
        "## 8. 既知の問題",
    ):
        assert _section(report, heading)


def test_detector_fixed_report_binds_gt_free_conclusion_contract() -> None:
    report = REPORT.read_text(encoding="utf-8")
    conclusion = _section(report, "## 0. 結論")

    assert "ground_truth_included=false" in conclusion
    assert "公式metric評価時だけ" in conclusion
    for reference in (
        "race_receipt.json",
        "prediction_manifest.json",
        "artifacts/detector_fixed_race/panel.json",
    ):
        assert reference in conclusion


def test_detector_fixed_report_binds_independent_blob_nms_receipts() -> None:
    report = REPORT.read_text(encoding="utf-8")
    nms = _section(report, _NMS_HEADING)

    assert "detector-fixed lane ではない" in nms
    assert "metrics.jsonを公式metric receiptとして扱う" in nms
    assert _nms_receipt_rows(nms) == {
        "NMS 3.0": (
            "`artifacts/multi_method_race/evaluation/blob_lap/metrics.json`",
            "`final_score=0.9140773262846648`",
        ),
        "NMS 3.5": (
            "`artifacts/performance_experiments/blob_lap_nms35/metrics.json`",
            "`final_score=0.9172062183593925`",
        ),
        "Delta (NMS 3.5 - NMS 3.0)": (
            "NMS 3.5 - NMS 3.0",
            "`+0.0031288920747277`",
        ),
    }


def test_detector_fixed_report_binds_five_sample_validation_receipt() -> None:
    report = REPORT.read_text(encoding="utf-8")

    for evidence in (
        "artifacts/detector_fixed_race/validation_receipt.json",
        "failed_samples=[]",
        "0.7688958987642377",
        "0.7944143977140719",
        "0.025518498949834156",
        "5/5",
        "0/3/1",
    ):
        assert evidence in report
