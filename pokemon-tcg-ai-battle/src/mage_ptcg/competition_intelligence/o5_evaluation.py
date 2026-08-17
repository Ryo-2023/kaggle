"""Resumable, multi-opponent O5 Benchmark Evaluation Runner.

This module deliberately does not reimplement scheduling, seat swap, or
resumption: it is a thin orchestrator over the already-tested
``league.actual_runner.run_actual_league`` (one resumable league per
opponent member, one per declared seed), merged into a per-member and
overall report using the existing ``wilson_score_interval`` helper.
Population-blocked sets (``current_meta`` and archetype-``adversarial`` while
0 archetypes are active) are reported as zero games with the manifest's own
blocked status, never fabricated or padded. Sets excluded by the manifest's
``benchmark_kind`` (e.g. ``safety`` inside a ``performance`` manifest) are
reported separately as ``EXCLUDED_BY_BENCHMARK_KIND`` so the two reasons for
"zero games" are never confused with each other.

Per-member/seed artifacts are namespaced by the manifest's own
``manifest_hash`` (not just set/member/seed names), so two different
manifests -- different candidate, different game_count, different seeds --
can never resume from, or silently overwrite, each other's results even if
pointed at the same ``output_dir``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Sequence

from main import make_deterministic_agent, make_random_agent, make_rule_agent, make_rule_agent_v1

from mage_ptcg.league.actual_runner import ActualLeagueConfig, run_actual_league
from mage_ptcg.offline_training_v1_support.statistics import wilson_score_interval

from .o5_adversarial_agents import ADVERSARIAL_AGENT_FACTORIES
from .o5_benchmark import VersionedBenchmarkManifest
from .o5_candidate_registry import CANDIDATE_ARTIFACT_REGISTRY

Agent = Callable[[dict], list[int]]
RunMatch = Callable[[Mapping[str, object]], Mapping[str, object]]

_BENCHMARK_SET_NAMES = ("core_regression", "current_meta", "adversarial", "safety")
_PERFORMANCE_SET_NAMES = ("core_regression", "current_meta")
_SAFETY_SET_NAMES = ("safety", "adversarial")
EXCLUDED_BY_BENCHMARK_KIND = "EXCLUDED_BY_BENCHMARK_KIND"


class O5EvaluationError(RuntimeError):
    """Raised for an unknown agent id or malformed O5 evaluation input."""


def _rule_v0_factory(deck: Sequence[int], seed: int) -> Agent:
    return make_rule_agent(deck=deck, seed=seed)


def _rule_v1_factory(deck: Sequence[int], seed: int) -> Agent:
    return make_rule_agent_v1(deck=deck, seed=seed)


def _random_legal_factory(deck: Sequence[int], seed: int) -> Agent:
    return make_random_agent(deck=deck, seed=seed)


def _deterministic_factory(deck: Sequence[int], seed: int) -> Agent:
    return make_deterministic_agent(deck=deck)


KNOWN_AGENT_FACTORIES: Mapping[str, Callable[[Sequence[int], int], Agent]] = {
    "rule_v0": _rule_v0_factory,
    "rule_v1": _rule_v1_factory,
    "random_legal": _random_legal_factory,
    "deterministic": _deterministic_factory,
    **ADVERSARIAL_AGENT_FACTORIES,
}


def _is_known_agent_id(agent_id: str) -> bool:
    return agent_id in KNOWN_AGENT_FACTORIES or agent_id in CANDIDATE_ARTIFACT_REGISTRY


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _sum_int(summaries: Sequence[Mapping[str, object]], key: str) -> int:
    return sum(int(summary[key]) for summary in summaries)


def _win_stats(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """wins/losses/draws/decided_games/win_rate/wilson_ci_95 for one record slice."""
    wins = sum(record.get("winner_agent") == "champion" for record in records)
    losses = sum(record.get("winner_agent") == "challenger" for record in records)
    draws = sum(record.get("winner_agent") == "draw" for record in records)
    decided = wins + losses + draws
    low, high = wilson_score_interval(wins, losses, draws)
    return {
        "games": len(records),
        "decided_games": decided,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": (wins + 0.5 * draws) / decided if decided else 0.0,
        "wilson_ci_95": [low, high],
    }


def _fallback_breakdown(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Split games by whether the candidate (champion) used a fallback that game.

    A game whose champion_fallback_count was never observed is excluded from
    both partitions rather than guessed into either one -- silently treating
    a fallback-assisted win as a "pure" neural-policy win (or vice versa)
    would misrepresent the candidate's real, unassisted performance.
    """
    no_fallback: list[Mapping[str, object]] = []
    fallback_used: list[Mapping[str, object]] = []
    unknown: list[Mapping[str, object]] = []
    for record in records:
        count = record.get("champion_fallback_count")
        is_int = type(count) is int  # excludes bool, which `isinstance(x, int)` would not
        if is_int and count == 0:
            no_fallback.append(record)
        elif is_int and count > 0:
            fallback_used.append(record)
        else:
            unknown.append(record)
    return {
        "no_fallback": _win_stats(no_fallback),
        "fallback_used": _win_stats(fallback_used),
        "fallback_status_unknown_games": len(unknown),
    }


def _merge_league_summaries(summaries: Sequence[Mapping[str, object]]) -> dict[str, object]:
    games = _sum_int(summaries, "games")
    wins = _sum_int(summaries, "wins")
    losses = _sum_int(summaries, "losses")
    draws = _sum_int(summaries, "draws")
    invalid_actions = _sum_int(summaries, "invalid_actions")
    crashes = _sum_int(summaries, "crashes")
    timeouts = _sum_int(summaries, "timeouts")
    fallbacks = _sum_int(summaries, "fallbacks")
    seat_wld: dict[str, dict[str, int]] = {}
    for summary in summaries:
        for seat, wld in dict(summary.get("seat_wld", {})).items():
            aggregate = seat_wld.setdefault(seat, {"wins": 0, "losses": 0, "draws": 0})
            for key in ("wins", "losses", "draws"):
                aggregate[key] += int(dict(wld).get(key, 0))
    latencies = [
        float(record["elapsed_seconds"])
        for summary in summaries
        for record in summary.get("records", [])
        if isinstance(record.get("elapsed_seconds"), (int, float))
    ]
    game_lengths = [
        int(record["steps"])
        for summary in summaries
        for record in summary.get("records", [])
        if type(record.get("steps")) is int
    ]
    all_records = [record for summary in summaries for record in summary.get("records", [])]
    seeds = [int(dict(summary["config"])["base_seed"]) for summary in summaries]
    # A crashed/invalid/timed-out opponent produces no winner (see the CLI's
    # `play()` closure: winner_agent stays None off-DONE), so it is neither
    # a win, a loss, nor a draw. win_rate and the Wilson interval must share
    # the same decided_games denominator, or a member whose opponent always
    # crashes (decided_games == 0) would silently read as a 0% loss rate
    # instead of "no outcome was ever decided."
    decided_games = wins + losses + draws
    wilson_low, wilson_high = wilson_score_interval(wins, losses, draws)
    attribution_available = bool(summaries) and all(bool(s.get("attribution_available")) for s in summaries)
    return {
        "games": games,
        "decided_games": decided_games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": (wins + 0.5 * draws) / decided_games if decided_games else 0.0,
        "wilson_ci_95": [wilson_low, wilson_high],
        # A fallback-assisted win must never be presented as identical to a
        # pure neural-policy win: this splits win/loss/draw/win_rate/CI by
        # whether the candidate (champion) used its fallback that specific
        # game, so "no_fallback" alone is the honest pure-policy figure.
        "fallback_breakdown": _fallback_breakdown(all_records),
        "seat_wld": seat_wld,
        "invalid_actions": invalid_actions,
        "crashes": crashes,
        "timeouts": timeouts,
        "fallbacks": fallbacks,
        # Per-agent/seat fault and fallback attribution (see
        # league.actual_runner's extension). attribution_available is False
        # whenever ANY underlying league summary lacked full per-seat data
        # -- the counts below still reflect whatever WAS observed, they are
        # just not guaranteed complete in that case.
        "attribution_available": attribution_available,
        "attribution_missing_games": _sum_int(summaries, "attribution_missing_games") if summaries else 0,
        "candidate_invalid": _sum_int(summaries, "candidate_invalid") if summaries else 0,
        "candidate_exception": _sum_int(summaries, "candidate_exception") if summaries else 0,
        "candidate_timeout": _sum_int(summaries, "candidate_timeout") if summaries else 0,
        "candidate_fallback_total": _sum_int(summaries, "candidate_fallback_total") if summaries else 0,
        "candidate_fallback_games": _sum_int(summaries, "candidate_fallback_games") if summaries else 0,
        "opponent_invalid": _sum_int(summaries, "opponent_invalid") if summaries else 0,
        "opponent_exception": _sum_int(summaries, "opponent_exception") if summaries else 0,
        "opponent_timeout": _sum_int(summaries, "opponent_timeout") if summaries else 0,
        "opponent_fallback_total": _sum_int(summaries, "opponent_fallback_total") if summaries else 0,
        "opponent_fallback_games": _sum_int(summaries, "opponent_fallback_games") if summaries else 0,
        "match_latency_seconds": {
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "game_length_steps": {
            "p50": _percentile([float(v) for v in game_lengths], 0.5),
            "p95": _percentile([float(v) for v in game_lengths], 0.95),
        },
        "seeds": seeds,
        "reproducible": all(bool(s.get("reproducible")) for s in summaries),
    }


def _empty_report(status: str) -> dict[str, object]:
    return {"status": status, "games": 0, "members": {}}


def run_o5_benchmark(
    manifest: VersionedBenchmarkManifest,
    *,
    candidate_agent_id: str,
    deck_fingerprint: str,
    output_dir: str | Path,
    run_match: RunMatch | None = None,
    run_match_factory: Callable[[str, str], RunMatch] | None = None,
) -> dict[str, object]:
    """Run every populated Benchmark member as an opponent of ``candidate_agent_id``.

    Exactly one of ``run_match`` (used unchanged for every member) or
    ``run_match_factory`` (called once per ``(candidate, member)`` pair, for
    callers whose real match runner needs to bind a different opponent agent
    per pairing) must be given. ``candidate_agent_id`` must be a name known
    either to the simple ``(deck, seed) -> Agent`` opponent registry
    (:data:`KNOWN_AGENT_FACTORIES`) or to the hash-pinned candidate artifact
    registry (:data:`~.o5_candidate_registry.CANDIDATE_ARTIFACT_REGISTRY`);
    this function never silently substitutes a different candidate.
    """
    if not _is_known_agent_id(candidate_agent_id):
        raise O5EvaluationError(f"unknown candidate_agent_id: {candidate_agent_id}")
    if run_match is None and run_match_factory is None:
        raise O5EvaluationError("either run_match or run_match_factory must be provided")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_prefix = manifest.manifest_hash[:16]
    in_scope_sets = _PERFORMANCE_SET_NAMES if manifest.benchmark_kind == "performance" else _SAFETY_SET_NAMES

    report: dict[str, object] = {}
    all_raw_summaries: list[dict[str, object]] = []
    for set_name in _BENCHMARK_SET_NAMES:
        if set_name not in in_scope_sets:
            report[set_name] = _empty_report(EXCLUDED_BY_BENCHMARK_KIND)
            continue
        members = manifest.sets.get(set_name, ())
        if not members:
            report[set_name] = _empty_report(manifest.status)
            continue
        member_reports: dict[str, object] = {}
        for member_id in members:
            if member_id == candidate_agent_id:
                continue  # a candidate never plays itself
            if not _is_known_agent_id(member_id):
                raise O5EvaluationError(f"benchmark set {set_name!r} references an unknown member: {member_id!r}")
            resolved_run_match = run_match if run_match is not None else run_match_factory(candidate_agent_id, member_id)
            per_seed_summaries: list[dict[str, object]] = []
            for seed in manifest.seed_set:
                config = ActualLeagueConfig(
                    champion=candidate_agent_id,
                    challenger=member_id,
                    games=manifest.game_count,
                    base_seed=seed,
                    deck_fingerprint=deck_fingerprint,
                    environment_version=manifest.cabt_version,
                )
                artifact_path = output_root / f"{manifest_prefix}__{set_name}__{member_id}__seed{seed}.json"
                per_seed_summaries.append(run_actual_league(config, output_path=artifact_path, run_match=resolved_run_match))
            member_reports[member_id] = _merge_league_summaries(per_seed_summaries)
            all_raw_summaries.extend(per_seed_summaries)
        report[set_name] = (
            {"status": "EXECUTED", "games": sum(int(r["games"]) for r in member_reports.values()), "members": member_reports}
            if member_reports
            else _empty_report(manifest.status)
        )

    report["overall"] = _merge_league_summaries(all_raw_summaries)
    return report


__all__ = ["EXCLUDED_BY_BENCHMARK_KIND", "KNOWN_AGENT_FACTORIES", "O5EvaluationError", "run_o5_benchmark"]
