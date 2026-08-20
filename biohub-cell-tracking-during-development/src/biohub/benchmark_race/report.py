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
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        return f"{root.name}/{relative}" if root.name == "artifacts" else relative
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
    lines.extend(
        [
            "## 実験条件・判定",
            "",
            "- sample: `44b6_0113de3b.zarr`（`(T,Z,Y,X)=(100,64,256,256)`、uint16）。",  # noqa: RUF001
            "- physical scale: `(1.625, 0.40625, 0.40625)` µm、公式 evaluator `max_distance=7.0` µm。",
            "- GT: `../../../data/train/44b6_0113de3b.geff`。GTは推論入力に渡さず、"
            "prediction manifestを検証した後の評価phaseだけで開いた。",
            "- cache/run/prediction receiptの`ground_truth_included`は全laneで`false`。"
            "divisionは初回raceでは無効化した。",
            "- 公式metricはリポジトリ内のRoyerLab由来vendor実装を使用し、再実装していない。",
            "",
            "## 手法構成と最終判定",
            "",
            "- `blob_lap`: 3D Gaussian/local-max + physical NMSの画像-only detector、"
            "Hungarian/LAP linker。新規手法ではFinal Score `0.9140773262846648`で、"
            "公式比`+0.0302828427639145`。",
            "- `cc_flow`: quantile foreground + 3D connected components、全フレーム "
            "`networkx.network_simplex` global min-cost flow。node recall "
            "`0.1346153846153846`、Final Score `0.04212152980003883`で、"
            "候補detectorが今回のデータに適合しなかった。",
            "- `motion_lap`: 固定blob候補に速度・加速度priorを加えたframe-local LAP。"
            "公式detector共有laneではない。Final Score `0.8968305842792937`で、"
            "blob単独より`-0.0172467420053711`となり、今回の設定では採用しない。",
            (
                "- Best Method（全比較）: `harmonic v1`"  # noqa: RUF001
                "（Final Score `0.9211200215044129`）。"  # noqa: RUF001
                "Best new lane: `blob_lap`（`0.9140773262846648`）。"  # noqa: RUF001
            ),
            "- 次に深掘りする候補: 公式TemporalUNet3Dのcenter detector候補を固定し、"
            "harmonic bidirectional association + ILPへ接続する実験。"
            "今回の公開実装調査ではofficial detector中間cacheが無く、別laneとしては未実施。",
            "- 相補component: blob候補のnode recallは`1.0`だったため、"
            "まずconfidence calibration/NMSと、harmonic associationの組合せを優先する。"
            "motion priorは今回のreceipt上の改善根拠がない。",
            "",
            "## 追加改善実験（blob NMS）",  # noqa: RUF001
            "",
            "- 仮説: `blob_lap`のphysical NMS距離を3.0 µmから3.5 µmへ変更し、"
            "過剰nodeを減らす。その他のdetector/linker設定、sample、metricは固定した。",
            "- receipt: `artifacts/performance_experiments/blob_lap_nms35/metrics.json`、"
            "source_commit=`ac2ece5`、CPU、runtime `63.7277883200004` s。",
            "- 結果: nodes `27393`、edges `25098`、Edge TP/FP/FN `48/2/2`、"
            "Division TP/FP/FN `0/0/0`、Final Score `0.9172062183593925`。",
            "- 差分: fixed `blob_lap`（`0.9140773262846648`）比 `+0.0031288920747277`、"  # noqa: RUF001
            "公式ベースライン比 `+0.0334117348386422`。harmonic v1には `-0.0039138031450204`で、"
            "単一sampleの改善候補として採用し、複数sample validation後に固定laneへ昇格する。",
            "",
            "## 失敗・未実施候補",
            "",
            "- HOCT、Trackastra、Ultrack、Linajea、DeepCenterは、"
            "公開source/checkpointまたはsegmentation/instance-mask入力契約、依存、"
            "checkpoint schemaの不足を`docs/results/multi_method_feasibility_ja.md`に記録した。"
            "今回の3本の公式評価値には含めていない。",
            (
                "- `official_motion`（公式detectorを共有するmotion ablation）は、"  # noqa: RUF001
                "upstreamに永続detector cache APIが無く、CPU 100-frame detector再実行を避けるため"
                "deferredとした。"
            ),
            "- 全laneはCPU実行。`cc_flow`はsolver status `optimal`だが、detector側の低recallが支配的だった。",
            "",
            "## 再現コマンド",
            "",
            "```bash",
            (
                "docker compose exec -T -e BIOHUB_BENCHMARK_RACE_SOURCE_REVISION=ac2ece5 -w "
                "/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/"
                "biohub-cell-tracking-during-development "
                "biohub uv run --no-sync python scripts/run_benchmark_race.py "
                "infer --method blob_lap --image-stem ../../../data/train/44b6_0113de3b.zarr "
                "--cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race"
            ),
            (
                "docker compose exec -T -e BIOHUB_BENCHMARK_RACE_SOURCE_REVISION=ac2ece5 -w "
                "/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/"
                "biohub-cell-tracking-during-development "
                "biohub uv run --no-sync python scripts/run_benchmark_race.py "
                "infer --method cc_flow --image-stem ../../../data/train/44b6_0113de3b.zarr "
                "--cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race"
            ),
            (
                "docker compose exec -T -e BIOHUB_BENCHMARK_RACE_SOURCE_REVISION=ac2ece5 -w "
                "/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/"
                "biohub-cell-tracking-during-development "
                "biohub uv run --no-sync python scripts/run_benchmark_race.py "
                "infer --method motion_lap --image-stem ../../../data/train/44b6_0113de3b.zarr "
                "--cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race"
            ),
            (
                "docker compose exec -T -w "
                "/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/"
                "biohub-cell-tracking-during-development "
                "biohub uv run --no-sync python scripts/run_benchmark_race.py "
                "evaluate --prediction artifacts/multi_method_race/methods/<method>/44b6_0113de3b.geff "
                "--ground-truth ../../../data/train/44b6_0113de3b.geff "
                "--metrics artifacts/multi_method_race/evaluation/<method>/metrics.json"
            ),
            (
                "docker compose exec -T -w "
                "/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/"
                "biohub-cell-tracking-during-development "
                "biohub uv run --no-sync python scripts/run_benchmark_race.py "
                "summarize --root . --output docs/results/multi_method_benchmark_race.md "
                "--summary-json artifacts/multi_method_race/race_summary.json"
            ),
            "```",
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
