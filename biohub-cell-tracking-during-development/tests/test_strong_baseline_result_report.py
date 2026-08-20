from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
ARTIFACTS = Path(
    os.environ.get(
        "BIOHUB_STRONG_BASELINE_ARTIFACTS",
        str(ROOT / "artifacts" / "strong_baseline_v1"),
    ),
)
FIXTURES = ROOT / "tests" / "fixtures" / "strong_baseline_v1"
REPORT = ROOT / "docs" / "results" / "strong_baseline_v1.md"

METRIC_KEYS = (
    "prediction_node_count",
    "prediction_edge_count",
    "edge_tp",
    "edge_fp",
    "edge_fn",
    "division_tp",
    "division_fp",
    "division_fn",
    "edge_jaccard",
    "adjusted_edge_jaccard",
    "division_jaccard",
    "final_score",
    "node_recall",
    "total_node_ratio",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _value_token(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _delta_token(baseline: Any, harmonic: Any) -> str:
    if baseline is None or harmonic is None:
        return "N/A"
    delta = harmonic - baseline
    if isinstance(delta, int):
        return f"{delta:+d}"
    return f"{delta:+}"


def test_result_report_matches_persisted_receipts_and_metrics() -> None:
    """The permanent report must remain a faithful receipt-derived record."""

    assert REPORT.is_file(), f"result report is missing: {REPORT}"
    report = REPORT.read_text()
    report_lower = " ".join(report.lower().split())

    source = _json(FIXTURES / "official" / "source_receipt.json")
    official_run = _json(FIXTURES / "official" / "run.json")
    official_manifest = _json(FIXTURES / "official" / "prediction_manifest.json")
    official_metrics = _json(FIXTURES / "official" / "metrics.json")
    harmonic_source = _json(FIXTURES / "harmonic" / "source_receipt.json")
    harmonic_run = _json(FIXTURES / "harmonic" / "run.json")
    harmonic_manifest = _json(FIXTURES / "harmonic" / "prediction_manifest.json")
    harmonic_metrics = _json(FIXTURES / "harmonic" / "metrics.json")

    assert Path(official_run["image_stem"]).name in report
    assert source["official_source"]["commit"] in report
    assert source["kaggle_artifact"]["checkpoint"]["sha256"] in report
    assert str(source["kaggle_artifact"]["version"]) in report
    assert str(source["kaggle_artifact"]["organizer_notebook_version"]) in report
    assert harmonic_source["source"]["version_number"] == 18
    assert str(harmonic_source["source"]["version_number"]) in report
    assert str(harmonic_source["source"]["script_version_id"]) in report

    for manifest in (official_manifest, harmonic_manifest):
        assert manifest["directory_sha256"] in report
        assert str(manifest["nodes"]) in report
        assert str(manifest["edges"]) in report

    for run in (official_run, harmonic_run):
        assert str(run["elapsed_seconds"]) in report
        assert run["expected_device"] in report
        assert run["actual_device"] in report
        assert str(run["torch_cuda_available"]).lower() in report_lower

    for key in METRIC_KEYS:
        assert f"`{key}`" in report
        assert _value_token(official_metrics[key]) in report
        assert _value_token(harmonic_metrics[key]) in report
        assert _delta_token(official_metrics[key], harmonic_metrics[key]) in report

    for path in (
        "artifacts/strong_baseline_v1/inputs/source_receipt.json",
        "artifacts/strong_baseline_v1/official_ilp/run.json",
        "artifacts/strong_baseline_v1/official_ilp/prediction_manifest.json",
        "artifacts/strong_baseline_v1/official_ilp/metrics.json",
        "artifacts/strong_baseline_v1/official_ilp/inference.log",
        "artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff",
        "artifacts/strong_baseline_v1/harmonic_ilp/source_receipt.json",
        "artifacts/strong_baseline_v1/harmonic_ilp/run.json",
        "artifacts/strong_baseline_v1/harmonic_ilp/prediction_manifest.json",
        "artifacts/strong_baseline_v1/harmonic_ilp/metrics.json",
        "artifacts/strong_baseline_v1/harmonic_ilp/inference.log",
        "artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff",
    ):
        assert path in report

    for heading in (
        "## 結論",
        "## 手法",
        "## ソース、バージョン、チェックポイントの来歴",
        "## 入力サンプルと疎な正解",
        "## 固定した推論・評価設定",
        "## 実行コマンド",
        "## 実行時間とリソース",
        "## 予測成果物と指標",
        "## 可視化の健全性確認",
        "## 既知の失敗と制約",
        "## 次の実験",
        "## 再現性と成果物一覧",
    ):
        assert heading in report

    for phrase in (
        "BSD-3-Clause",
        "License: Unknown",
        "Harmonic v1 の測定値は w=0.20",
        "ILP後のnode数はassociationの影響で異なり得るため",
        "official receiptにはraw candidateのdigestがない",
        "division Jaccardは、official summarizerが存在しないdivision項を落とすためnull",
        "疎なGTに対する未マッチ検出をfalse positiveとして扱ってはならない",
        "推論中にGTは使用していない",
        "GTを開く前に永続化manifestを検証",
        "harmonic v1 のソースセル独立監査は BLOCKED",
        "receiptに基づく補足結果",
        "CPUのみの実行",
        "SCIPフォールバック",
        "viewerのmatched_node_id/match_node_id属性不一致",
        "commit、push、Kaggle submissionは実施していない",
    ):
        assert phrase in report

    for path in (
        "scripts/check_strong_baseline_visual.py",
        "artifacts/strong_baseline_v1/visual_sanity/visual_sanity.json",
        "artifacts/strong_baseline_v1/visual_sanity/visual_sanity.txt",
    ):
        assert path in report


def test_metric_table_binds_every_markdown_cell_to_receipts() -> None:
    report = REPORT.read_text()
    rows = _parse_metric_rows(report)
    official_metrics = _json(FIXTURES / "official" / "metrics.json")
    harmonic_metrics = _json(FIXTURES / "harmonic" / "metrics.json")

    assert set(rows) == set(METRIC_KEYS)
    for key in METRIC_KEYS:
        assert rows[key] == (
            _value_token(official_metrics[key]),
            _value_token(harmonic_metrics[key]),
            _delta_token(official_metrics[key], harmonic_metrics[key]),
        )


def test_metric_table_rejects_swapped_or_malformed_cells() -> None:
    report = REPORT.read_text()
    swapped = report.replace(
        "| `edge_tp` | 46 | 48 | +2 |",
        "| `edge_tp` | 48 | 46 | -2 |",
    )
    malformed = report.replace(
        "| `edge_fp` | 2 | 2 | +0 |",
        "| `edge_fp` | 2 | 2 |",
    )
    bad_boundary = report.replace("|---|---:|---:|---:|", "|---|---:|---:|")
    official_metrics = _json(FIXTURES / "official" / "metrics.json")
    harmonic_metrics = _json(FIXTURES / "harmonic" / "metrics.json")

    with pytest.raises(AssertionError):
        _assert_metric_rows(
            _parse_metric_rows(swapped), official_metrics, harmonic_metrics
        )
    with pytest.raises(ValueError, match="four cells"):
        _parse_metric_rows(malformed)
    with pytest.raises(ValueError, match="delimiter"):
        _parse_metric_rows(bad_boundary)


def test_inference_receipts_are_image_only() -> None:
    for path in (
        FIXTURES / "official" / "run.json",
        FIXTURES / "harmonic" / "run.json",
    ):
        command = _json(path)["command"]
        assert "--evaluate" not in command
        assert "--ground-truth" not in command
        assert not any(str(part).endswith(".geff") for part in command)
        assert not any(
            "/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff"
            in str(part)
            for part in command
        )


def _parse_metric_rows(report: str) -> dict[str, tuple[str, str, str]]:
    """Parse exactly the four-column official-vs-harmonic metric table."""

    marker = (
        "| 指標 | 公式ベースライン | harmonic v1 | 正確な差分"
        "\N{FULLWIDTH LEFT PARENTHESIS}harmonic \N{MINUS SIGN} official"
        "\N{FULLWIDTH RIGHT PARENTHESIS} |"
    )
    lines = report.splitlines()
    try:
        start = lines.index(marker) + 2
    except ValueError as exc:
        raise ValueError("metric table header is missing") from exc
    if start > len(lines) or lines[start - 1] != "|---|---:|---:|---:|":
        raise ValueError("metric table delimiter is missing")

    rows: dict[str, tuple[str, str, str]] = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise ValueError(f"metric table row must contain four cells: {line!r}")
        key = cells[0].strip("`")
        if not key or key in rows:
            raise ValueError(f"invalid or duplicate metric table key: {key!r}")
        rows[key] = (cells[1], cells[2], cells[3])
    if not rows:
        raise ValueError("metric table has no data rows")
    return rows


def _assert_metric_rows(
    rows: dict[str, tuple[str, str, str]],
    official_metrics: dict[str, Any],
    harmonic_metrics: dict[str, Any],
) -> None:
    assert set(rows) == set(METRIC_KEYS)
    for key in METRIC_KEYS:
        assert rows[key] == (
            _value_token(official_metrics[key]),
            _value_token(harmonic_metrics[key]),
            _delta_token(official_metrics[key], harmonic_metrics[key]),
        )


def test_tracked_fixtures_match_real_receipts_when_artifacts_are_present() -> None:
    """Use real ignored artifacts as an optional derivation/integrity check."""

    real_pairs = (
        (ARTIFACTS / "inputs" / "source_receipt.json", FIXTURES / "official" / "source_receipt.json"),
        (ARTIFACTS / "official_ilp" / "run.json", FIXTURES / "official" / "run.json"),
        (ARTIFACTS / "official_ilp" / "prediction_manifest.json", FIXTURES / "official" / "prediction_manifest.json"),
        (ARTIFACTS / "official_ilp" / "metrics.json", FIXTURES / "official" / "metrics.json"),
        (ARTIFACTS / "harmonic_ilp" / "source_receipt.json", FIXTURES / "harmonic" / "source_receipt.json"),
        (ARTIFACTS / "harmonic_ilp" / "run.json", FIXTURES / "harmonic" / "run.json"),
        (ARTIFACTS / "harmonic_ilp" / "prediction_manifest.json", FIXTURES / "harmonic" / "prediction_manifest.json"),
        (ARTIFACTS / "harmonic_ilp" / "metrics.json", FIXTURES / "harmonic" / "metrics.json"),
    )
    if not all(path.is_file() for path, _ in real_pairs):
        pytest.skip("ignored strong-baseline artifacts are unavailable")

    real_official_source = _json(real_pairs[0][0])
    fixture_official_source = _json(real_pairs[0][1])
    assert fixture_official_source["official_source"]["commit"] == real_official_source["official_source"]["commit"]
    assert fixture_official_source["kaggle_artifact"]["version"] == real_official_source["kaggle_artifact"]["version"]
    assert (
        fixture_official_source["kaggle_artifact"]["checkpoint"]["sha256"]
        == real_official_source["kaggle_artifact"]["checkpoint"]["sha256"]
    )

    for real_path, fixture_path in real_pairs[1:]:
        real = _json(real_path)
        fixture = _json(fixture_path)
        for key in (
            "directory_sha256",
            "files",
            "total_bytes",
            "nodes",
            "edges",
            "prediction_node_count",
            "prediction_edge_count",
            "final_score",
            "source_sha256",
            "version_number",
            "script_version_id",
        ):
            real_values = _find_values(real, key)
            fixture_values = _find_values(fixture, key)
            if real_values and fixture_values:
                assert real_values == fixture_values, f"fixture drift for {real_path}: {key}"


def _find_values(mapping: dict[str, Any], key: str) -> list[Any]:
    values: list[Any] = []
    for name, value in mapping.items():
        if name == key:
            values.append(value)
        elif isinstance(value, dict):
            values.extend(_find_values(value, key))
    return values
