"""Run privacy-safe, evaluation-only actual-cabt viability gates.

The command never changes the submission agent.  It accepts only registered
evaluation candidates and writes aggregate public metrics; raw observations,
selections, exception messages, and paths remain transient.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import read_deck_csv  # noqa: E402
from mage_ptcg.distillation.contracts import atomic_write_json, digest  # noqa: E402
from mage_ptcg.evaluation.actual_agents import agent_inventory, make_instrumented_agent  # noqa: E402
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256, find_forbidden_keys  # noqa: E402
from mage_ptcg.competition.redaction import secret_scan  # noqa: E402
from scripts.cabt_capability import diagnose_cabt_capability  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


PUBLIC_STATUSES = frozenset({"DONE", "AGENT_INVALID", "AGENT_ERROR", "AGENT_TIMEOUT", "STEP_LIMIT", "INCOMPLETE", "ERROR"})
STOPPED_CLASSIFICATIONS = frozenset({"NOT_A_RUNTIME_AGENT", "BLOCKED_BY_MISSING_CAPABILITY", "BLOCKED_BY_MISSING_ARTIFACT", "BLOCKED_BY_INVALID_ARTIFACT", "UNSAFE"})
_ABSOLUTE_PRIVATE_PATH = re.compile(r"(?:^|[\s\"'])/(?:home|tmp|Users)/")
_KEY_CATEGORIES = {
    "id": "raw_card_identity",
    "card_id": "raw_card_identity",
    "cardid": "raw_card_identity",
    "own_hand": "own_hand_identity",
    "hand": "own_hand_identity",
    "opponent_hand": "opponent_hidden_information",
    "opponent_deck": "opponent_hidden_information",
    "prize": "opponent_hidden_information",
    "candidate_identity": "candidate_identity",
    "identity_hash": "identity_hash",
    "raw_observation": "raw_observation",
    "observation": "raw_observation",
    "terminal_reason": "raw_exception_message",
    "exception_message": "raw_exception_message",
    "exception": "raw_exception_message",
    "repr": "object_repr",
    "path": "absolute_private_path",
    "actor_a_view": "actor_cross_contamination",
    "actor_b_view": "actor_cross_contamination",
    "actor_views": "actor_cross_contamination",
}


class ViabilityError(RuntimeError):
    """Raised before an unsafe or unavailable viability run starts."""


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, check=True, text=True, capture_output=True
    )
    return completed.stdout.strip()


def _schedule(games: int, base_seed: int) -> list[dict[str, int]]:
    if type(games) is not int or games <= 0:
        raise ValueError("games must be positive")
    if games > 1 and games % 2:
        raise ValueError("multi-game viability runs require an even side-swap count")
    return [
        {
            "match_index": index,
            "seed": base_seed + index,
            "champion_player_index": index % 2,
            "challenger_player_index": 1 - (index % 2),
        }
        for index in range(games)
    ]


def _public_environment(report: Mapping[str, object]) -> dict[str, object]:
    return {
        "environment_loader": "kaggle_environments.make",
        "environment_name": "cabt",
        "kaggle_environments_version": report.get("kaggle_environments_version"),
        "requested_environment": report.get("requested_environment"),
        "actual_execution_allowed": report.get("actual_execution_allowed") is True,
        "engine_seed_supported": report.get("engine_seed_supported"),
    }


def _safe_status(value: object) -> str:
    return value if isinstance(value, str) and value in PUBLIC_STATUSES else "ERROR"


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _privacy_scan(value: object) -> dict[str, object]:
    """Return controlled categories only; never retain a detected raw value."""
    categories: Counter[str] = Counter()

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                normalized = str(key).lower().replace("-", "_")
                category = _KEY_CATEGORIES.get(normalized)
                if category is not None:
                    categories[category] += 1
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            if _ABSOLUTE_PRIVATE_PATH.search(node):
                categories["absolute_private_path"] += 1

    walk(value)
    for key in find_forbidden_keys(value):
        categories["opaque_observation_field"] += 1
    for finding in secret_scan(value):
        if finding.startswith("home_path:"):
            categories["absolute_private_path"] += 1
        elif finding.startswith(("sensitive_key:", "signed_url:", "secret_like_value:", "email:")):
            categories["secret_or_private_value"] += 1
    return {
        "privacy_scan_executed": True,
        "privacy_violations": sum(categories.values()),
        "privacy_violation_categories": dict(sorted(categories.items())),
    }


def _apply_privacy_scan(summary: dict[str, object], records: list[dict[str, object]]) -> dict[str, object]:
    """Scan all persisted public components before each atomic publication."""
    components = {
        "decision_aggregates": [summary.get("champion_metrics"), summary.get("challenger_metrics")],
        "match_records": records,
        "league_summary": {key: value for key, value in summary.items() if key not in {"config", "records"}},
        "provenance_manifest": summary.get("config"),
    }
    total: Counter[str] = Counter()
    for component in components.values():
        result = _privacy_scan(component)
        total.update(result["privacy_violation_categories"])
    return {
        **summary,
        "privacy_scan_executed": True,
        "privacy_scan_scope": sorted(components),
        "privacy_violations": sum(total.values()),
        "privacy_violation_categories": dict(sorted(total.items())),
    }


def _aggregate_metrics(records: list[Mapping[str, object]], field: str) -> dict[str, object]:
    totals = Counter()
    reasons: Counter[str] = Counter()
    policies: set[str] = set()
    policy_counts: Counter[str] = Counter()
    latency_values: list[float] = []
    features: dict[str, object] = {}
    for record in records:
        metrics = record.get(field)
        if not isinstance(metrics, Mapping):
            continue
        for name in ("agent_calls", "decisions", "legal_decisions", "invalid_selections", "exception_count", "fallback_count", "decision_latency_samples"):
            value = metrics.get(name)
            if type(value) is int:
                totals[name] += value
        for key, value in (metrics.get("fallback_reason_counts") or {}).items() if isinstance(metrics.get("fallback_reason_counts"), Mapping) else ():
            if isinstance(key, str) and type(value) is int:
                reasons[key] += value
        policy = metrics.get("effective_policy")
        if isinstance(policy, str):
            policies.add(policy)
        raw_policy_counts = metrics.get("effective_policy_counts")
        if isinstance(raw_policy_counts, Mapping):
            for policy_name, count in raw_policy_counts.items():
                if isinstance(policy_name, str) and type(count) is int:
                    policy_counts[policy_name] += count
        elif isinstance(policy, str):
            policy_counts[policy] += 1
        for name in ("model_hash", "model_artifact_purpose"):
            value = metrics.get(name)
            if value is not None:
                if name not in features:
                    features[name] = value
                elif features[name] != value:
                    features[name] = "MIXED"
        latency = metrics.get("latency_ms")
        if isinstance(latency, Mapping):
            for name in ("p50", "p95", "max"):
                value = latency.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    latency_values.append(float(value))
        runtime = metrics.get("runtime_features")
        if isinstance(runtime, Mapping):
            for name, value in runtime.items():
                if type(value) is int:
                    features[name] = int(features.get(name, 0)) + value
                elif name not in features:
                    features[name] = value
                elif features[name] != value:
                    features[name] = "MIXED"
    return {
        **{name: totals[name] for name in ("agent_calls", "decisions", "legal_decisions", "invalid_selections", "exception_count", "fallback_count", "decision_latency_samples")},
        "legal_action_rate": totals["legal_decisions"] / totals["decisions"] if totals["decisions"] else "UNKNOWN",
        "timeout_count": "UNKNOWN",
        "fallback_reason_counts": dict(sorted(reasons.items())),
        "latency_ms": {"p50": _percentile(latency_values, 0.50), "p95": _percentile(latency_values, 0.95), "max": max(latency_values) if latency_values else None},
        "effective_policy": sorted(policies) if policies else ["UNKNOWN"],
        "effective_policy_counts": dict(sorted(policy_counts.items())),
        "model_hash": features.get("model_hash"),
        "model_artifact_purpose": features.get("model_artifact_purpose", "NOT_APPLICABLE"),
        "runtime_features": features,
    }


def _summary(*, config: Mapping[str, object], schedule: list[dict[str, int]], records: list[dict[str, object]]) -> dict[str, object]:
    completed = [record for record in records if record.get("status") == "DONE"]
    wins = Counter(str(record.get("winner_agent")) for record in completed)
    by_index = {entry["match_index"]: entry for entry in schedule}
    seat_wld: dict[str, dict[str, int]] = {}
    for seat in (0, 1):
        selected = [record for record in completed if by_index[int(record["match_index"])]["champion_player_index"] == seat]
        seat_wld[f"champion_player_{seat}"] = {
            "wins": sum(record.get("winner_agent") == "champion" for record in selected),
            "losses": sum(record.get("winner_agent") == "challenger" for record in selected),
            "draws": sum(record.get("winner_agent") == "draw" for record in selected),
        }
    elapsed = [float(record["elapsed_seconds"]) for record in records if isinstance(record.get("elapsed_seconds"), (int, float)) and not isinstance(record.get("elapsed_seconds"), bool)]
    status_counts = Counter(str(record.get("status")) for record in records)
    return {
        "schema_version": "actual-agent-viability-v0",
        "config": dict(config),
        "config_hash": digest(dict(config), domain="actual-agent-viability-config-v0"),
        "seed_schedule": schedule,
        "seat_schedule": [{"match_index": item["match_index"], "champion_player_index": item["champion_player_index"], "challenger_player_index": item["challenger_player_index"]} for item in schedule],
        "attempted_games": len(schedule),
        "completed_games": len(records),
        "wins": wins["champion"],
        "losses": wins["challenger"],
        "draws": wins["draw"],
        "seat_wld": seat_wld,
        "invalid_actions": status_counts["AGENT_INVALID"],
        "crashes": status_counts["ERROR"] + status_counts["AGENT_ERROR"],
        "timeouts": status_counts["AGENT_TIMEOUT"] + status_counts["STEP_LIMIT"],
        "privacy_scan_executed": False,
        "privacy_violations": "UNKNOWN",
        "privacy_violation_categories": {},
        "match_latency_seconds": {"p50": _percentile(elapsed, 0.50), "p95": _percentile(elapsed, 0.95), "max": max(elapsed) if elapsed else None},
        "champion_metrics": _aggregate_metrics(records, "champion_metrics"),
        "challenger_metrics": _aggregate_metrics(records, "challenger_metrics"),
        "completed_match_indices": sorted(int(record["match_index"]) for record in records),
        "resume_duplicate_execution_detected": False,
        "resume_duplicate_measurement": "STRUCTURAL_GUARANTEE",
        "resume_idempotent": True,
        "schedule_complete": len(records) == len(schedule),
    }


def _gate_status(summary: Mapping[str, object], challenger_id: str) -> tuple[str, str | None]:
    if summary.get("privacy_scan_executed") is not True:
        return "STOPPED", "privacy_scan_not_executed"
    privacy_violations = summary.get("privacy_violations")
    if type(privacy_violations) is not int:
        return "STOPPED", "privacy_scan_not_executed"
    if privacy_violations > 0:
        return "STOPPED", "privacy_violation_detected"
    if any(type(summary.get(name)) is int and int(summary[name]) for name in ("invalid_actions", "crashes", "timeouts")):
        return "STOPPED", "safety_or_execution_failure"
    metrics = summary.get("challenger_metrics")
    effective = metrics.get("effective_policy") if isinstance(metrics, Mapping) else []
    if challenger_id == "bounded_search" and effective == ["Rule Agent v0 fallback only"]:
        return "STOPPED", "effective_policy_rule_v0_fallback_only"
    if challenger_id in ("student", "neural_student", "neural_student_package"):
        runtime = metrics.get("runtime_features") if isinstance(metrics, Mapping) else None
        if not isinstance(runtime, Mapping):
            return "STOPPED", f"{challenger_id}_runtime_metrics_unavailable"
        if runtime.get("model_loaded") is not True or not isinstance(metrics.get("model_hash"), str):
            return "STOPPED", f"{challenger_id}_model_not_loaded"
        if any(type(runtime.get(name)) is not int or int(runtime[name]) <= 0 for name in ("inference_requested", "inference_completed", "student_selection_count")):
            return "STOPPED", f"{challenger_id}_inference_not_effective"
        if not any("Student" in item for item in effective if isinstance(item, str)):
            return "STOPPED", "effective_policy_rule_v0_fallback_only"
    if not summary.get("schedule_complete"):
        return "STOPPED", "incomplete_schedule"
    if challenger_id in ("student", "neural_student", "neural_student_package"):
        return ("CLEAN_PASS", None) if metrics.get("fallback_count") == 0 else ("PASS_WITH_RUNTIME_FALLBACKS", None)
    return "PASS", None


def run_actual_agent_viability(
    *,
    challenger_id: str,
    games: int,
    base_seed: int,
    output_path: str | Path,
    canonical_base_sha: str,
    student_model_path: str | Path | None = None,
    student_manifest_path: str | Path | None = None,
    neural_model_path: str | Path | None = None,
    package_path: str | Path | None = None,
    deck_path: str | Path = REPOSITORY_ROOT / "deck.csv",
    max_steps: int = 10_000,
    capability_report: Mapping[str, object] | None = None,
    match_runner: Callable[..., Mapping[str, object]] = run_match,
) -> dict[str, object]:
    """Execute one Gate 1 probe or a Gate 2 viability smoke for one challenger."""
    report = dict(capability_report) if capability_report is not None else diagnose_cabt_capability()
    if report.get("status") != "READY":
        raise ViabilityError("cabt_capability_unavailable")
    inventory = agent_inventory(
        student_model_path=student_model_path,
        student_manifest_path=student_manifest_path,
        neural_model_path=neural_model_path,
        package_path=package_path,
    )
    if challenger_id not in inventory:
        raise ViabilityError("unknown_evaluation_agent")
    challenger = inventory[challenger_id]
    if challenger.classification in STOPPED_CLASSIFICATIONS:
        raise ViabilityError(f"agent_not_runnable:{challenger.classification}")
    if challenger_id == "rule":
        raise ViabilityError("rule_is_champion_control_not_a_distinct_challenger")
    if games > 1 and challenger_id == "bounded_search":
        raise ViabilityError("bounded_search_requires_passing_independent_probe")
    if games > 1 and challenger_id == "student" and challenger.artifact_purpose != "ACTUAL_TRAINED":
        raise ViabilityError("student_gate_b_requires_actual_trained")
    if games > 1 and challenger_id in ("neural_student", "neural_student_package") and challenger.artifact_purpose != "NEURAL_ACTUAL_TRAINED":
        raise ViabilityError("neural_student_gate_b_requires_actual_trained")
    deck = read_deck_csv(deck_path)
    schedule = _schedule(games, base_seed)
    work_commit = _git_head()
    public_environment = _public_environment(report)
    engine_seed_supported = report.get("engine_seed_supported")
    if type(engine_seed_supported) is not bool:
        raise ViabilityError("engine_seed_capability_unknown")
    config = {
        "canonical_base_sha": canonical_base_sha,
        "work_commit_sha": work_commit,
        "cabt_environment": public_environment,
        "engine_seed_supported": engine_seed_supported,
        "agent_seed_schedule_deterministic": True,
        "seat_schedule_deterministic": True,
        "resume_idempotent": True,
        "engine_outcomes_deterministic": False,
        "environment_fingerprint": digest(public_environment, domain="actual-agent-environment-v0"),
        "deck_fingerprint": canonical_deck_sha256(deck),
        "champion": inventory["rule"].to_public_dict(),
        "challenger": challenger.to_public_dict(),
        "max_steps": max_steps,
    }
    destination = Path(output_path)
    config_hash = digest(config, domain="actual-agent-viability-config-v0")
    previous: dict[int, dict[str, object]] = {}
    if destination.exists():
        loaded = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping) or loaded.get("config_hash") != config_hash:
            raise ViabilityError("existing_artifact_has_different_config")
        for item in loaded.get("records", []):
            if not isinstance(item, dict) or type(item.get("match_index")) is not int:
                raise ViabilityError("existing_artifact_malformed")
            previous[int(item["match_index"])] = item
    records: list[dict[str, object]] = []
    for item in schedule:
        index = item["match_index"]
        if index in previous:
            records.append(previous[index])
            continue
        champion_seat = item["champion_player_index"]
        first_id, second_id = ("rule", challenger_id) if champion_seat == 0 else (challenger_id, "rule")
        created: dict[str, object] = {}

        def factory(agent_id: str) -> Callable[[list[int], int], object]:
            def build(active_deck: list[int], agent_seed: int) -> object:
                runtime = make_instrumented_agent(
                    agent_id, deck=active_deck, seed=agent_seed,
                    student_model_path=student_model_path, student_manifest_path=student_manifest_path,
                    neural_model_path=neural_model_path, package_path=package_path,
                )
                created[agent_id] = runtime
                return runtime.as_runtime_function()
            return build

        try:
            raw = match_runner(
                deck_a_path=deck_path,
                deck_b_path=deck_path,
                agent_a_name=first_id,
                agent_b_name=second_id,
                seed=item["seed"],
                max_steps=max_steps,
                output_dir=destination.parent / ".actual-agent-viability-transient",
                save_html=False,
                save_result=False,
                agent_a_factory=factory(first_id),
                agent_b_factory=factory(second_id),
            )
            status = _safe_status(raw.get("status"))
            winner = raw.get("winner")
            winner_agent = "draw" if status == "DONE" and winner == 2 else ("champion" if status == "DONE" and winner == champion_seat else "challenger" if status == "DONE" and winner in (0, 1) else None)
            elapsed = raw.get("elapsed_seconds")
            elapsed_seconds = float(elapsed) if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) else None
        except Exception:
            status, winner_agent, elapsed_seconds = "ERROR", None, None
        first_metrics = created.get(first_id)
        second_metrics = created.get(second_id)
        first_public = first_metrics.public_metrics() if hasattr(first_metrics, "public_metrics") else None
        second_public = second_metrics.public_metrics() if hasattr(second_metrics, "public_metrics") else None
        record = {
            "match_index": index,
            "status": status,
            "winner_agent": winner_agent,
            "elapsed_seconds": elapsed_seconds,
            "champion_metrics": first_public if champion_seat == 0 else second_public,
            "challenger_metrics": second_public if champion_seat == 0 else first_public,
        }
        records.append(record)
        partial = _apply_privacy_scan(_summary(config=config, schedule=schedule, records=records), records)
        atomic_write_json(destination, {**partial, "records": records, "gate_status": "IN_PROGRESS", "gate_reason": None})
    summary = _apply_privacy_scan(_summary(config=config, schedule=schedule, records=records), records)
    gate_status, gate_reason = _gate_status(summary, challenger_id)
    result = {**summary, "records": records, "gate_status": gate_status, "gate_reason": gate_reason}
    result["artifact_hash"] = digest(result, domain="actual-agent-viability-artifact-v0")
    atomic_write_json(destination, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenger", choices=("deterministic", "bounded_search", "student", "neural_student", "neural_student_package", "c5"), required=True)
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--base-seed", type=int, default=0)
    parser.add_argument("--canonical-base", required=True)
    parser.add_argument("--student-model", type=Path)
    parser.add_argument("--student-manifest", type=Path)
    parser.add_argument("--neural-model", type=Path)
    parser.add_argument("--package-path", type=Path)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_actual_agent_viability(
            challenger_id=args.challenger,
            games=args.games,
            base_seed=args.base_seed,
            output_path=args.output,
            canonical_base_sha=args.canonical_base,
            student_model_path=args.student_model,
            student_manifest_path=args.student_manifest,
            neural_model_path=args.neural_model,
            package_path=args.package_path,
            deck_path=args.deck,
            max_steps=args.max_steps,
        )
    except (ViabilityError, ValueError) as exc:
        print(f"actual agent viability failed: {type(exc).__name__}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["gate_status"] in {"PASS", "CLEAN_PASS", "PASS_WITH_RUNTIME_FALLBACKS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
