#!/usr/bin/env python3
"""Summarize strict-disagreement selection without changing any experiment artifact.

The broad report carries every visited prefix from games that contain at least
one disagreement.  The report's available-total fields include the complete
screen, including games with no disagreement, so the effective-mass ratios do
not silently use a selected-game denominator.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


def _csv_ints(value: str) -> tuple[int, ...]:
    result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if len(set(result)) != len(result) or any(item < 0 or item > 16 for item in result):
        raise argparse.ArgumentTypeError("action types must be unique integers in [0, 16]")
    return result


def _csv_floats(value: str) -> tuple[float, ...]:
    try:
        result = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("thresholds must be comma-separated numbers") from exc
    if not result or len(set(result)) != len(result) or any(not math.isfinite(item) for item in result):
        raise argparse.ArgumentTypeError("thresholds must be finite and unique")
    return result


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("selection"), dict):
        raise ValueError(f"invalid strict report: {path}")
    return payload


def _prefix_rows(report: dict[str, Any]) -> Iterable[dict[str, Any]]:
    selection = report["selection"]
    for transition in selection.get("selected_transition_metadata", []):
        if not isinstance(transition, dict):
            raise ValueError("strict report metadata row is not an object")
        disagreement = transition.get("disagreement_prefix_indices", [])
        masses = transition.get("disagreement_effective_loss_masses", [])
        student = transition.get("student_action_types", [])
        teacher = transition.get("teacher_action_types", [])
        domains = transition.get("domain_sizes", [])
        margins = transition.get("teacher_top1_margins", [])
        entropies = transition.get("teacher_entropies", [])
        target_probabilities = transition.get("teacher_target_probabilities", [])
        behavior = transition.get("prefix_behavior_log_probabilities", [])
        if not all(isinstance(value, list) for value in (disagreement, masses, student, teacher, domains, margins, entropies, target_probabilities, behavior)):
            raise ValueError("strict report metadata arrays are malformed")
        mass_by_index = {int(index): float(mass) for index, mass in zip(disagreement, masses, strict=True)}
        for index in disagreement:
            index = int(index)
            yield {
                "game_id": transition.get("game_id"),
                "component_id": transition.get("component_id"),
                "opponent_id": transition.get("opponent_id"),
                "seat": transition.get("seat"),
                "transition_index": transition.get("transition_index"),
                "prefix_index": index,
                "student_type": student[index],
                "teacher_type": teacher[index],
                "domain_size": int(domains[index]),
                "prefix_count": int(transition.get("prefix_count", len(domains))),
                "mean_behavior_log_probability": float(transition.get("mean_behavior_log_probability", 0.0)),
                "prefix_behavior_log_probability": float(behavior[index]),
                "teacher_top1_margin": float(margins[index]),
                "teacher_entropy": float(entropies[index]),
                "teacher_target_probability": float(target_probabilities[index]),
                "effective_loss_mass": mass_by_index[index],
            }


def _kind(row: dict[str, Any], targets: set[int]) -> str:
    student, teacher = row["student_type"], row["teacher_type"]
    if teacher in targets and student not in targets:
        return "false_negative"
    if student in targets and teacher not in targets:
        return "false_positive"
    if student in targets and teacher in targets:
        return "within_type_error"
    return "unrelated_disagreement"


def _selection_at_threshold(
    rows: list[dict[str, Any]], *, targets: set[int], threshold: float, symmetric: bool,
) -> dict[str, Any]:
    qualifying = [
        row for row in rows
        if row["mean_behavior_log_probability"] <= threshold
        and (row["teacher_type"] in targets or (symmetric and row["student_type"] in targets))
    ]
    games = {str(row["game_id"]) for row in qualifying}
    components = {str(row["component_id"]) for row in qualifying}
    return {
        "prefix_count": len(qualifying),
        "transition_count": len({(row["game_id"], row["transition_index"]) for row in qualifying}),
        "game_count": len(games),
        "component_count": len(components),
        "effective_loss_mass": math.fsum(row["effective_loss_mass"] for row in qualifying),
        "non_forced_effective_loss_mass": math.fsum(
            row["effective_loss_mass"] for row in qualifying if row["domain_size"] > 1
        ),
        "mean_teacher_margin": (
            math.fsum(row["teacher_top1_margin"] for row in qualifying) / len(qualifying)
            if qualifying else None
        ),
        "mean_teacher_entropy": (
            math.fsum(row["teacher_entropy"] for row in qualifying) / len(qualifying)
            if qualifying else None
        ),
        "mean_teacher_target_probability": (
            math.fsum(row["teacher_target_probability"] for row in qualifying) / len(qualifying)
            if qualifying else None
        ),
    }


def _bucket_rates(
    rows: list[dict[str, Any]], *, targets: set[int], threshold: float, field: str,
) -> dict[str, dict[str, int]]:
    buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        key = str(row[field])
        buckets[key][0] += 1
        if row["mean_behavior_log_probability"] <= threshold and row["teacher_type"] in targets:
            buckets[key][1] += 1
    return {
        key: {"disagreement_prefixes": values[0], "teacher_target_eligible": values[1]}
        for key, values in sorted(buckets.items(), key=lambda item: int(item[0]))
    }


def _summarize(report: dict[str, Any], *, targets: set[int], thresholds: tuple[float, ...]) -> dict[str, Any]:
    selection = report["selection"]
    rows = list(_prefix_rows(report))
    confusion_count = Counter(_kind(row, targets) for row in rows)
    confusion_mass: Counter[str] = Counter()
    for row in rows:
        confusion_mass[_kind(row, targets)] += row["effective_loss_mass"]
    available_total = selection.get("available_total_non_forced_effective_loss_mass")
    if available_total is None:
        available_total = math.fsum(
            float(row.get("total_non_forced_effective_loss_mass", 0.0))
            for row in selection.get("selected_transition_metadata", [])
        )
    return {
        "report_path": report.get("screen_path"),
        "screen_file_sha256": report.get("screen_file_sha256"),
        "transitions_file_sha256": report.get("transitions_file_sha256"),
        "available_transition_count": selection.get("available_transition_count"),
        "available_episode_count": selection.get("available_episode_count"),
        "available_total_non_forced_effective_loss_mass": float(available_total),
        "broad_disagreement_prefix_count": len(rows),
        "broad_disagreement_game_count": selection.get("selected_episode_count"),
        "confusion": {
            key: {"prefix_count": confusion_count[key], "effective_loss_mass": confusion_mass[key]}
            for key in ("false_negative", "false_positive", "within_type_error", "unrelated_disagreement")
        },
        "thresholds": {
            str(threshold): {
                "teacher_target_only": _selection_at_threshold(
                    rows, targets=targets, threshold=threshold, symmetric=False,
                ),
                "symmetric_student_or_teacher_target": _selection_at_threshold(
                    rows, targets=targets, threshold=threshold, symmetric=True,
                ),
            }
            for threshold in thresholds
        },
        "bucket_rates_at_threshold": {
            str(threshold): {
                "prefix_count": _bucket_rates(rows, targets=targets, threshold=threshold, field="prefix_count"),
                "domain_size": _bucket_rates(rows, targets=targets, threshold=threshold, field="domain_size"),
            }
            for threshold in thresholds
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broad-report", type=Path, action="append", required=True)
    parser.add_argument("--strict-report", type=Path, action="append", default=[])
    parser.add_argument("--targets", type=_csv_ints, default=(9, 13, 14))
    parser.add_argument("--thresholds", type=_csv_floats, default=(-0.2, -0.5, -1.0))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    broad_reports = [_read(path) for path in args.broad_report]
    strict_reports = [_read(path) for path in args.strict_report]
    payload = {
        "schema": "meta-specialist-v4-strict-disagreement-preflight-v1",
        "target_action_types": list(args.targets),
        "thresholds": list(args.thresholds),
        "broad": [_summarize(report, targets=set(args.targets), thresholds=args.thresholds) for report in broad_reports],
        "strict_reference": [
            {
                "report_path": report.get("screen_path"),
                "selection": {
                    key: report["selection"].get(key)
                    for key in (
                        "effective_loss_mass", "non_forced_effective_loss_mass", "eligible_transition_count",
                        "selected_episode_count", "supervised_prefix_count",
                    )
                },
            }
            for report in strict_reports
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["artifact_sha256"] = hashlib.sha256(encoded).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
