"""Resumable, side-swapped actual-cabt League runner.

This is deliberately an offline orchestration layer around the existing
official-environment ``run_match`` entry point.  It records only match-level
outcomes and timing; decision traces remain in the separate public trace
artifact and actor-visible bindings remain in their separate private artifact.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from mage_ptcg.distillation.contracts import atomic_write_json, digest


@dataclass(frozen=True, slots=True)
class ActualLeagueConfig:
    champion: str
    challenger: str
    games: int
    base_seed: int
    deck_fingerprint: str
    environment_version: str
    max_steps: int = 10_000

    def __post_init__(self) -> None:
        if not self.champion or not self.challenger or self.champion == self.challenger:
            raise ValueError("league requires distinct champion and challenger")
        if type(self.games) is not int or self.games <= 0 or self.games % 2:
            raise ValueError("games must be a positive even integer for side swap")
        if type(self.base_seed) is not int or type(self.max_steps) is not int or self.max_steps <= 0:
            raise ValueError("base_seed and max_steps are invalid")
        if not self.deck_fingerprint or not self.environment_version:
            raise ValueError("deck and environment provenance are required")


def deterministic_schedule(config: ActualLeagueConfig) -> list[dict[str, object]]:
    return [
        {
            "match_index": index,
            "seed": config.base_seed + index,
            "champion_player_index": index % 2,
            "challenger_player_index": 1 - (index % 2),
        }
        for index in range(config.games)
    ]


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


_SEAT_STATUS_VALUES = {"DONE", "INVALID", "ERROR", "TIMEOUT", "UNKNOWN", "NOT_OBSERVABLE"}
NOT_OBSERVABLE = "NOT_OBSERVABLE"


def _seat_fault_counts(records: list[Mapping[str, object]], *, field: str) -> tuple[int, int, int]:
    """Count INVALID/ERROR/TIMEOUT for one seat's status field, ignoring NOT_OBSERVABLE."""
    values = [str(record.get(field)) for record in records]
    return (
        sum(value == "INVALID" for value in values),
        sum(value == "ERROR" for value in values),
        sum(value == "TIMEOUT" for value in values),
    )


def _seat_fallback_totals(records: list[Mapping[str, object]], *, field: str) -> tuple[int, int]:
    """Sum a per-seat fallback counter, ignoring records where it was never observed."""
    observed = [record.get(field) for record in records if type(record.get(field)) is int]
    return sum(observed), sum(1 for value in observed if value > 0)


def _summary(config: ActualLeagueConfig, schedule: list[dict[str, object]], records: list[Mapping[str, object]]) -> dict[str, object]:
    done = [record for record in records if record.get("status") == "DONE"]
    status_counts = Counter(str(record.get("status", "ERROR")) for record in records)
    wins = Counter(str(record.get("winner_agent")) for record in done)
    latencies = [float(record["elapsed_seconds"]) for record in records if isinstance(record.get("elapsed_seconds"), (int, float))]
    schedule_by_index = {int(item["match_index"]): item for item in schedule}
    seat_wld: dict[str, dict[str, int]] = {}
    for champion_seat in (0, 1):
        selected = [
            record for record in done
            if schedule_by_index[int(record["match_index"])]["champion_player_index"] == champion_seat
        ]
        seat_wld[f"champion_player_{champion_seat}"] = {
            "wins": sum(record.get("winner_agent") == "champion" for record in selected),
            "losses": sum(record.get("winner_agent") == "challenger" for record in selected),
            "draws": sum(record.get("winner_agent") == "draw" for record in selected),
        }
    candidate_invalid, candidate_exception, candidate_timeout = _seat_fault_counts(records, field="champion_status")
    opponent_invalid, opponent_exception, opponent_timeout = _seat_fault_counts(records, field="challenger_status")
    candidate_fallback_total, candidate_fallback_games = _seat_fallback_totals(records, field="champion_fallback_count")
    opponent_fallback_total, opponent_fallback_games = _seat_fallback_totals(records, field="challenger_fallback_count")
    # A record whose seat status is "UNKNOWN" (observed, but not classifiable
    # as DONE/INVALID/ERROR/TIMEOUT) is just as untrustworthy for attribution
    # purposes as one that was never observed at all -- both must keep
    # attribution_available False, not just NOT_OBSERVABLE.
    _unattributed_statuses = {NOT_OBSERVABLE, "UNKNOWN"}
    missing = sum(
        1 for record in records
        if record.get("champion_status") in _unattributed_statuses or record.get("challenger_status") in _unattributed_statuses
    )
    return {
        "schema_version": "c5-actual-league-v1",
        "config": asdict(config),
        "config_hash": digest(asdict(config), domain="c5-actual-league-config"),
        "schedule": schedule,
        "games": len(records),
        "wins": wins["champion"],
        "losses": wins["challenger"],
        "draws": wins["draw"],
        "seat_wld": seat_wld,
        "invalid_actions": status_counts["AGENT_INVALID"],
        "crashes": status_counts["ERROR"] + status_counts["AGENT_ERROR"],
        "timeouts": status_counts["AGENT_TIMEOUT"] + status_counts["STEP_LIMIT"],
        "fallbacks": sum(int(record.get("fallback_count", 0)) for record in records if type(record.get("fallback_count", 0)) is int),
        # Per-seat fault/fallback attribution. Only populated when the
        # injected run_match supplied champion_status/challenger_status/
        # *_fallback_count; a legacy caller that omits them never gets a
        # fabricated 0 -- attribution_available/attribution_missing_games
        # tell the reader exactly how much of the below is trustworthy.
        "attribution_available": missing == 0 and len(records) > 0,
        "attribution_missing_games": missing,
        "candidate_invalid": candidate_invalid,
        "candidate_exception": candidate_exception,
        "candidate_timeout": candidate_timeout,
        "opponent_invalid": opponent_invalid,
        "opponent_exception": opponent_exception,
        "opponent_timeout": opponent_timeout,
        "candidate_fallback_total": candidate_fallback_total,
        "candidate_fallback_games": candidate_fallback_games,
        "opponent_fallback_total": opponent_fallback_total,
        "opponent_fallback_games": opponent_fallback_games,
        "privacy_violations": 0,
        "match_latency_seconds": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95), "max": max(latencies) if latencies else None},
        "completed_match_indices": sorted(int(record["match_index"]) for record in records),
        "reproducible": len(records) == config.games,
    }


def _public_seat_status(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    return value if isinstance(value, str) and value in _SEAT_STATUS_VALUES else NOT_OBSERVABLE


def _public_fallback_count(raw: Mapping[str, object], field: str) -> int | str:
    value = raw.get(field)
    if type(value) is int and value >= 0:
        return value
    return NOT_OBSERVABLE


def _public_step_count(raw: Mapping[str, object]) -> int | None:
    value = raw.get("steps")
    return value if type(value) is int and value >= 0 else None


def _public_match_record(raw: Mapping[str, object]) -> dict[str, object]:
    """Drop callback-only fields before a League artifact is persisted."""
    status = raw.get("status")
    winner = raw.get("winner_agent")
    elapsed = raw.get("elapsed_seconds")
    fallback = raw.get("fallback_count", 0)
    return {
        "status": status if isinstance(status, str) else "ERROR",
        "winner_agent": winner if winner in {"champion", "challenger", "draw", None} else None,
        "elapsed_seconds": elapsed if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) else None,
        "fallback_count": fallback if type(fallback) is int and fallback >= 0 else 0,
        "champion_status": _public_seat_status(raw, "champion_status"),
        "challenger_status": _public_seat_status(raw, "challenger_status"),
        "champion_fallback_count": _public_fallback_count(raw, "champion_fallback_count"),
        "challenger_fallback_count": _public_fallback_count(raw, "challenger_fallback_count"),
        "steps": _public_step_count(raw),
    }


def run_actual_league(
    config: ActualLeagueConfig,
    *,
    output_path: str | Path,
    run_match: Callable[[Mapping[str, object]], Mapping[str, object]],
) -> dict[str, object]:
    """Resume an exact schedule without recording decision-private material.

    ``run_match`` is injected so the official cabt adapter owns environment
    construction.  It receives public schedule metadata only and must return
    a match-level result (status, winner_agent, elapsed_seconds).
    """
    destination = Path(output_path)
    schedule = deterministic_schedule(config)
    previous: dict[int, dict[str, object]] = {}
    if destination.exists():
        loaded = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("config_hash") != digest(asdict(config), domain="c5-actual-league-config"):
            raise ValueError("existing league artifact has a different config")
        for item in loaded.get("records", []):
            if not isinstance(item, dict) or type(item.get("match_index")) is not int:
                raise ValueError("existing league artifact is malformed")
            # A pre-attribution artifact (written before champion_status/
            # challenger_status/*_fallback_count existed) is backfilled with
            # explicit NOT_OBSERVABLE rather than silently treated as 0.
            previous[int(item["match_index"])] = {
                **item,
                "champion_status": _public_seat_status(item, "champion_status"),
                "challenger_status": _public_seat_status(item, "challenger_status"),
                "champion_fallback_count": _public_fallback_count(item, "champion_fallback_count"),
                "challenger_fallback_count": _public_fallback_count(item, "challenger_fallback_count"),
            }
    records: list[dict[str, object]] = []
    for item in schedule:
        index = int(item["match_index"])
        if index in previous:
            records.append(previous[index])
            continue
        try:
            raw = _public_match_record(dict(run_match(item)))
        except Exception:
            raw = {"status": "ERROR", "winner_agent": None, "elapsed_seconds": None, "fallback_count": 0}
        raw["match_index"] = index
        records.append(raw)
        summary = _summary(config, schedule, records)
        atomic_write_json(destination, {**summary, "records": records})
    summary = _summary(config, schedule, records)
    result = {**summary, "records": records}
    atomic_write_json(destination, result)
    return result


__all__ = ["ActualLeagueConfig", "deterministic_schedule", "run_actual_league"]
