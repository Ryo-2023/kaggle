"""Receipt-derived comparison tables for the benchmark-race lanes.

This module intentionally knows nothing about the official metric
implementation.  It only reads persisted ``run.json`` and ``metrics.json``
receipts and renders a deterministic Japanese report.  Missing receipts are
represented as ``BLOCKED`` instead of being filled with zeroes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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

METHOD_ORDER = (
    "official_ilp",
    "harmonic_ilp",
    "blob_lap",
    "cc_flow",
    "motion_lap",
)

METHOD_LABELS = {
    "official_ilp": "公式ベースライン",
    "harmonic_ilp": "harmonic v1",
    "blob_lap": "blob_lap",
    "cc_flow": "cc_flow",
    "motion_lap": "motion_lap",
}

METRIC_LABELS = {
    "prediction_node_count": "予測node数",
    "prediction_edge_count": "予測edge数",
    "edge_tp": "Edge TP",
    "edge_fp": "Edge FP",
    "edge_fn": "Edge FN",
    "division_tp": "Division TP",
    "division_fp": "Division FP",
    "division_fn": "Division FN",
    "edge_jaccard": "Edge Jaccard",
    "adjusted_edge_jaccard": "Adjusted Edge Jaccard",
    "division_jaccard": "Division Jaccard",
    "final_score": "Final Score",
    "node_recall": "Node recall",
    "total_node_ratio": "Total node ratio",
}

_MISSING = object()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _relative_path(value: object, *, root: Path) -> str | None:
    """Return a stable path suitable for a report, never an absolute path."""

    if isinstance(value, Path):
        path = value
    elif isinstance(value, str) and value:
        path = Path(value)
    else:
        return None
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        # A receipt may point at the shared read-only data directory.  A
        # report still needs a useful, machine-independent identifier, but
        # must not leak the host's absolute prefix.
        return path.name


def _number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _format_value(value: object) -> str:
    if value is _MISSING:
        return "BLOCKED"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"
        return format(value, ".15g")
    return str(value)


def _format_delta(value: object, baseline: object) -> str:
    current = _number(value)
    reference = _number(baseline)
    if current is None or reference is None:
        return "—"
    return format(current - reference, "+.15g")


def _method_root(root: Path, method_id: str) -> tuple[Path, Path, Path | None, Path]:
    """Locate run, metrics, and manifest paths for one method.

    The race evaluator writes metrics under ``evaluation/<method>`` while the
    two persisted strong-baseline receipts keep ``metrics.json`` beside their
    run receipt.  Supporting both layouts keeps summary input receipt-only.
    """

    if method_id in {"official_ilp", "harmonic_ilp"}:
        baseline_root = root / "strong_baseline_v1"
        method_root = baseline_root / method_id
        run_path = method_root / "run.json"
        metrics_path = method_root / "metrics.json"
        manifest_path = method_root / "prediction_manifest.json"
        return run_path, metrics_path, manifest_path, method_root

    race_root = root / "multi_method_race"
    method_root = race_root / "methods" / method_id
    if not method_root.is_dir() and (root / "methods" / method_id).is_dir():
        race_root = root
        method_root = root / "methods" / method_id
    run_path = method_root / "run.json"
    metrics_path = race_root / "evaluation" / method_id / "metrics.json"
    if not metrics_path.is_file():
        metrics_path = method_root / "metrics.json"
    manifest_path = method_root / "prediction_manifest.json"
    return run_path, metrics_path, manifest_path, method_root


def _record_for_method(root: Path, method_id: str) -> dict[str, Any]:
    run_path, metrics_path, manifest_path, _method_root_path = _method_root(root, method_id)
    run = _read_json(run_path) if run_path.is_file() else None
    metrics = _read_json(metrics_path) if metrics_path.is_file() else None
    manifest = _read_json(manifest_path) if manifest_path.is_file() else None

    missing: list[str] = []
    if run is None:
        missing.append("run.json")
    if metrics is None:
        missing.append("metrics.json")
    if missing:
        return {
            "method_id": method_id,
            "label": METHOD_LABELS[method_id],
            "status": "BLOCKED",
            "blocked_reason": "未実行またはreceipt不足: " + ", ".join(missing),
            "metrics": {},
            "runtime_seconds": None,
            "expected_device": "不明",
            "actual_device": "不明",
            "artifact_paths": {
                "run": _relative_path(run_path, root=root),
                "metrics": _relative_path(metrics_path, root=root),
                "manifest": _relative_path(manifest_path, root=root),
            },
        }

    status = str(run.get("status", "success")).casefold()
    if status not in {"success", "ok", "completed"}:
        return {
            "method_id": method_id,
            "label": METHOD_LABELS[method_id],
            "status": "BLOCKED",
            "blocked_reason": f"receipt status={run.get('status')!r}",
            "metrics": {},
            "runtime_seconds": None,
            "expected_device": "不明",
            "actual_device": "不明",
            "artifact_paths": {
                "run": _relative_path(run_path, root=root),
                "metrics": _relative_path(metrics_path, root=root),
                "manifest": _relative_path(manifest_path, root=root),
            },
        }

    runtime = run.get("runtime_seconds", run.get("elapsed_seconds"))
    runtime_number = _number(runtime)
    expected_device = run.get("expected_device", run.get("device", "不明"))
    actual_device = run.get("actual_device", run.get("device", "不明"))
    prediction_path = run.get("prediction_path")
    if prediction_path is None and manifest is not None:
        prediction_path = manifest.get("prediction_path")

    record: dict[str, Any] = {
        "method_id": method_id,
        "label": METHOD_LABELS[method_id],
        "status": "OK",
        "blocked_reason": None,
        # Keep the machine-readable summary JSON serializable even when a
        # malformed/partial receipt omits one metric.  The explicit string is
        # intentionally not a numeric fallback.
        "metrics": {key: metrics.get(key, "BLOCKED") for key in METRIC_KEYS},
        "runtime_seconds": runtime_number,
        "expected_device": str(expected_device),
        "actual_device": str(actual_device),
        "artifact_paths": {
            "run": _relative_path(run_path, root=root),
            "metrics": _relative_path(metrics_path, root=root),
            "manifest": _relative_path(manifest_path, root=root) if manifest_path.is_file() else None,
            "prediction": _relative_path(prediction_path, root=root),
        },
        "provenance": {
            key: run[key]
            for key in (
                "method_family",
                "detector_id",
                "linker_id",
                "version",
                "source_commit",
                "checkpoint_sha256",
            )
            if key in run
        },
        "solver_status": run.get("solver_status", run.get("status", "不明")),
    }
    return record


def _normalise_root(root: Path) -> Path:
    root = Path(root)
    if root.name == "strong_baseline_v1":
        return root.parent
    if root.name == "multi_method_race":
        return root.parent
    if (root / "artifacts").is_dir():
        return root / "artifacts"
    return root


def collect_summary(root: Path) -> dict[str, Any]:
    """Collect five fixed lanes from persisted receipts without opening GT."""

    root = _normalise_root(Path(root))
    records = [_record_for_method(root, method_id) for method_id in METHOD_ORDER]
    baseline_metrics = next(
        (
            record["metrics"]
            for record in records
            if record["method_id"] == "official_ilp" and record["status"] == "OK"
        ),
        {},
    )
    for record in records:
        metrics = record["metrics"]
        record["delta_vs_official"] = {
            key: _format_delta(metrics.get(key), baseline_metrics.get(key))
            for key in METRIC_KEYS
        }
    return {
        "schema_version": "benchmark_race.summary.v1",
        "root": ".",
        "baseline_method": "official_ilp",
        "metric_keys": list(METRIC_KEYS),
        "methods": records,
    }


def render_markdown(summary: Mapping[str, Any]) -> str:
    """Render a deterministic Japanese report from :func:`collect_summary`."""

    records = list(summary.get("methods", []))
    by_id = {record.get("method_id"): record for record in records if isinstance(record, Mapping)}
    lines = [
        "# Biohub Multi-Method Benchmark Race",
        "",
        "この文書は保存済みの `run.json` と `metrics.json` だけから生成した比較記録です。",
        "推論中にGTは使用せず、未実行の手法は数値を補わず `BLOCKED` と記録します。",
        "",
        "## 比較結果",
        "",
        "| 指標 | 公式ベースライン | harmonic v1 | blob_lap | cc_flow | motion_lap |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key in METRIC_KEYS:
        cells = [
            _format_value(by_id.get(method_id, {}).get("metrics", {}).get(key, _MISSING))
            for method_id in METHOD_ORDER
        ]
        lines.append(f"| `{key}` | " + " | ".join(cells) + " |")

    lines.extend(
        [
            "",
            "### Final Score差分 (公式ベースライン比)",
            "",
            "| 手法 | 状態 | Final Score | 公式との差分 | 実行時間[s] | expected / actual device |",
            "|---|---|---:|---:|---:|---|",
        ],
    )
    for method_id in METHOD_ORDER:
        record = by_id.get(method_id, {})
        metrics = record.get("metrics", {})
        status = record.get("status", "BLOCKED")
        score = _format_value(metrics.get("final_score", _MISSING)) if status == "OK" else "BLOCKED"
        delta = record.get("delta_vs_official", {}).get("final_score", "—") if status == "OK" else "—"
        runtime = _format_value(record.get("runtime_seconds")) if status == "OK" else "不明"
        device = f"{record.get('expected_device', '不明')} / {record.get('actual_device', '不明')}"
        lines.append(
            f"| {METHOD_LABELS[method_id]} (`{method_id}`) | {status} | {score} | "
            f"{delta} | {runtime} | {device} |",
        )

    lines.extend(["", "## 手法ごとの状態と成果物", ""])
    for method_id in METHOD_ORDER:
        record = by_id.get(method_id, {})
        lines.append(f"### `{method_id}`")
        if record.get("status") != "OK":
            lines.append("")
            lines.append(f"- 状態: `BLOCKED` ({record.get('blocked_reason', '未実行')})")
            continue
        lines.append("")
        lines.append(f"- 状態: `{record.get('status', 'OK')}`")
        provenance = record.get("provenance", {})
        if provenance:
            for key in ("method_family", "detector_id", "linker_id", "version", "source_commit", "checkpoint_sha256"):
                if key in provenance:
                    lines.append(f"- {key}: `{provenance[key]}`")
        paths = record.get("artifact_paths", {})
        for key in ("prediction", "manifest", "run", "metrics"):
            if paths.get(key):
                lines.append(f"- {key}: `{paths[key]}`")

    lines.extend(
        [
            "",
            "## 既知の制約",
            "",
            "- これは単一のKaggle train sampleに対する同条件比較であり、leaderboard性能を意味しません。",
            "- 疎なGTでは未注釈cellをfalse positiveと解釈しません。",
            "- `division_jaccard=null` は公式summarizerのdivision項が存在しない場合の値です。",
            "- official detectorを共有するmotion laneは今回のraceでは未実施です。",
            "  `motion_lap`はblob候補上の古典motion associationです。",
            "",
        ],
    )
    return "\n".join(lines)


def write_summary(root: Path, output: Path, summary_json: Path | None = None) -> dict[str, Any]:
    """Write deterministic Markdown and optional JSON summary."""

    summary = collect_summary(Path(root))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(summary))
    if summary_json is not None:
        summary_json = Path(summary_json)
        summary_json.parent.mkdir(parents=True, exist_ok=True)
        summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return summary


__all__ = [
    "METHOD_ORDER",
    "METRIC_KEYS",
    "collect_summary",
    "render_markdown",
    "write_summary",
]
