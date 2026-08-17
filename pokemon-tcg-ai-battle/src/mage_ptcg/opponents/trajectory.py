"""Per-game trajectory evidence and uniqueness-aware League statistics.

O6-AUD-002 (HIGH) remediation. Before this module existed, the Team League
recorded only match-level bookkeeping (status/winner/elapsed/fallback) per
game -- there was no way to tell whether ``games_per_pair`` "raw executions"
against one opponent pair were genuinely different games or the same
trajectory replayed, and the ``cabt`` engine seed's effectiveness was never
verified. This module adds:

* deterministic, timestamp/path-independent digests of each game's initial
  state, action trace, and terminal state, computed from
  ``kaggle_environments`` ``env.steps``;
* an explicit, *verified* (not asserted) classification of cabt's seed
  capability: the ``cabt`` environment spec has no ``seed`` configuration
  key at all (confirmed by inspecting a live
  ``kaggle_environments.make('cabt', ...).configuration.keys()``, which is
  exactly ``{decks, episodeSteps, actTimeout, runTimeout}``), so every
  League game is ``ENGINE_SEED_UNSUPPORTED`` -- this module re-checks that
  live instead of hardcoding it, so it self-corrects if a future
  ``kaggle_environments`` version adds seed support;
* uniqueness aggregation (raw executions vs. unique trajectories,
  multiplicity, effective independent sample size) per bucket (typically
  pair x seat-direction);
* Wilson CI and Bradley-Terry helpers computed on both the raw-execution
  basis (explicitly labeled descriptive-only) and the deduplicated
  unique-trajectory basis, with small-sample suppression.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.canonical import digest

from .errors import OpponentError

TRAJECTORY_DIGEST_SCHEMA_VERSION = "o6-trajectory-digest-v1"
UNIQUENESS_SCHEMA_VERSION = "o6-trajectory-uniqueness-v1"
PAIR_STATISTICS_SCHEMA_VERSION = "o6-pair-win-rate-statistics-v1"
BRADLEY_TERRY_SCHEMA_VERSION = "o6-bradley-terry-v1"

ENGINE_SEED_EFFECTIVE = "ENGINE_SEED_EFFECTIVE"
ENGINE_SEED_UNSUPPORTED = "ENGINE_SEED_UNSUPPORTED"
ENGINE_SEED_UNVERIFIED = "ENGINE_SEED_UNVERIFIED"
AGENT_RANDOMNESS_ONLY = "AGENT_RANDOMNESS_ONLY"
DETERMINISTIC_TRAJECTORY = "DETERMINISTIC_TRAJECTORY"
SEED_CAPABILITY_VALUES = frozenset({ENGINE_SEED_EFFECTIVE, ENGINE_SEED_UNSUPPORTED, ENGINE_SEED_UNVERIFIED, AGENT_RANDOMNESS_ONLY, DETERMINISTIC_TRAJECTORY})

DEFAULT_MIN_EFFECTIVE_INDEPENDENT_SAMPLE_SIZE = 5
_WILSON_Z_95 = 1.959963984540054


def determine_engine_seed_capability(configuration_keys: Iterable[str]) -> str:
    """Classify cabt's seed capability from a live ``environment.configuration``.

    Returns :data:`ENGINE_SEED_UNSUPPORTED` when the environment spec has no
    ``seed`` key at all (true for ``cabt`` today), or
    :data:`ENGINE_SEED_UNVERIFIED` when a ``seed`` key exists but this
    function makes no claim about whether it actually controls trajectory
    diversity -- that would require a dedicated replay-equality probe, not
    just key presence.
    """
    return ENGINE_SEED_UNVERIFIED if "seed" in set(configuration_keys) else ENGINE_SEED_UNSUPPORTED


_VOLATILE_OBSERVATION_KEYS = {"remainingOverageTime"}


def strip_volatile_observation(observation: Any) -> Any:
    if not isinstance(observation, Mapping):
        return observation
    return {key: value for key, value in observation.items() if key not in _VOLATILE_OBSERVATION_KEYS}


def canonical_step_seat(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {"observation": strip_volatile_observation(entry.get("observation")), "action": entry.get("action"), "status": entry.get("status")}


def compute_trajectory_digests(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute a digest set from PUBLIC_TRAJECTORY_PROJECTION_V1 events.

    ``events`` is the output of
    :func:`mage_ptcg.opponents.public_trajectory_projection.build_public_trajectory_events`
    -- allow-list-projected public events, never raw ``env.steps``.

    Mirrors (but does not share code with)
    :func:`mage_ptcg.opponents.independent_trajectory_verifier.recompute_digests`
    exactly in *what* participates: every digest input is wrapped with the
    event's own ``schema_version`` so a schema bump changes every digest,
    even if the payload content is byte-identical.
    """
    if not events:
        raise OpponentError("cannot compute trajectory digests from empty events")
    ordered = sorted(events, key=lambda e: e["step_index"])
    schema_version = ordered[0]["schema_version"]
    action_trace = [
        {"step": e["step_index"], "seat_direction": e.get("seat_direction"), "action": e["public_payload"].get("action")}
        for e in ordered if e["public_payload"].get("action") is not None
    ]
    return {
        "schema_version": TRAJECTORY_DIGEST_SCHEMA_VERSION,
        "initial_observation_digest": digest({"schema_version": schema_version, "payload": ordered[0]["public_payload"]}, domain="o6-trajectory-initial"),
        "terminal_observation_digest": digest({"schema_version": schema_version, "payload": ordered[-1]["public_payload"]}, domain="o6-trajectory-terminal"),
        "action_trace_digest": digest({"schema_version": schema_version, "trace": action_trace}, domain="o6-trajectory-actions"),
        "complete_trajectory_digest": digest({"schema_version": schema_version, "events": [
            {"event_type": e["event_type"], "step_index": e["step_index"], "seat_direction": e.get("seat_direction"), "public_payload": e["public_payload"]}
            for e in ordered
        ]}, domain="o6-trajectory-complete"),
        "game_length": len(ordered),
        "raw_action_count": len(action_trace),
    }


def aggregate_trajectory_uniqueness(records: Iterable[Mapping[str, Any]], *, bucket_key: Callable[[Mapping[str, Any]], Any]) -> dict[Any, dict[str, Any]]:
    """Group per-game trajectory-evidence records and measure real diversity.

    Each record must carry ``initial_observation_digest``,
    ``action_trace_digest``, ``terminal_observation_digest``, and
    ``complete_trajectory_digest``. ``effective_independent_sample_size`` is
    the count of *distinct* ``complete_trajectory_digest`` values in the
    bucket: repeated identical trajectories count once, per the example in
    the remediation spec (``raw_executions=10, unique=1 -> effective N=1``).
    """
    buckets: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        buckets[bucket_key(record)].append(record)
    result: dict[Any, dict[str, Any]] = {}
    for key, items in buckets.items():
        complete = [item["complete_trajectory_digest"] for item in items]
        initial = [item["initial_observation_digest"] for item in items]
        actions = [item["action_trace_digest"] for item in items]
        terminal = [item["terminal_observation_digest"] for item in items]
        multiplicity = Counter(complete)
        result[key] = {
            "schema_version": UNIQUENESS_SCHEMA_VERSION,
            "raw_executions": len(items),
            "unique_initial_states": len(set(initial)),
            "unique_action_traces": len(set(actions)),
            "unique_terminal_states": len(set(terminal)),
            "unique_complete_trajectories": len(set(complete)),
            "duplicate_trajectory_groups": {trajectory_digest: count for trajectory_digest, count in multiplicity.items() if count > 1},
            "max_multiplicity": max(multiplicity.values()) if multiplicity else 0,
            "effective_independent_sample_size": len(set(complete)),
            "independence_limitation": (
                "cabt has no effective/controllable seed for this run (see engine_seed_support_status); "
                "effective_independent_sample_size counts distinct observed complete_trajectory_digest values, "
                "treating repeated identical trajectories as one independent outcome rather than N separate votes"
            ),
        }
    return result


def deduplicate_by_trajectory(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return one representative record per distinct ``complete_trajectory_digest``, in first-seen order."""
    seen: set[Any] = set()
    result: list[Mapping[str, Any]] = []
    for record in records:
        key = record["complete_trajectory_digest"]
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def wilson_ci(wins: int, total: int, *, z: float = _WILSON_Z_95) -> tuple[float, float] | None:
    """95%-by-default Wilson score interval for a binomial proportion."""
    if total == 0:
        return None
    p = wins / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (max(0.0, (center - margin) / denom), min(1.0, (center + margin) / denom))


def pair_win_rate_statistics(records: Iterable[Mapping[str, Any]], *, side_a: str, side_b: str,
                              min_effective_n: int = DEFAULT_MIN_EFFECTIVE_INDEPENDENT_SAMPLE_SIZE) -> dict[str, Any]:
    """Win-rate statistics for one pair, both raw-execution and unique-trajectory basis.

    ``records`` must carry ``winner_participant`` (one of ``side_a``,
    ``side_b``, ``"draw"``, or ``None``) and ``complete_trajectory_digest``.
    The raw-execution Wilson CI is always computed but explicitly labeled
    descriptive-only (it may double-count identical trajectories); the
    unique-trajectory CI is suppressed (``None``,
    ``INSUFFICIENT_INDEPENDENT_SAMPLES``) when the deduplicated sample is
    too small to be statistically interpretable.
    """
    records = list(records)
    raw_total = len(records)
    raw_wins_a = sum(1 for r in records if r.get("winner_participant") == side_a)
    raw_wins_b = sum(1 for r in records if r.get("winner_participant") == side_b)
    raw_draws = sum(1 for r in records if r.get("winner_participant") == "draw")
    unique_records = deduplicate_by_trajectory(records)
    unique_total = len(unique_records)
    unique_wins_a = sum(1 for r in unique_records if r.get("winner_participant") == side_a)
    unique_wins_b = sum(1 for r in unique_records if r.get("winner_participant") == side_b)
    unique_draws = sum(1 for r in unique_records if r.get("winner_participant") == "draw")
    sufficient = unique_total >= min_effective_n
    return {
        "schema_version": PAIR_STATISTICS_SCHEMA_VERSION,
        "side_a": side_a, "side_b": side_b,
        "raw_execution_total": raw_total, "raw_execution_wins": {side_a: raw_wins_a, side_b: raw_wins_b}, "raw_execution_draws": raw_draws,
        "raw_execution_win_rate_a": (raw_wins_a / raw_total) if raw_total else None,
        "raw_execution_wilson_ci_a": wilson_ci(raw_wins_a, raw_total - raw_draws) if (raw_total - raw_draws) else None,
        "raw_execution_wilson_ci_is_descriptive_only": True,
        "unique_trajectory_total": unique_total, "unique_trajectory_wins": {side_a: unique_wins_a, side_b: unique_wins_b}, "unique_trajectory_draws": unique_draws,
        "effective_independent_sample_size": unique_total,
        "unique_trajectory_win_rate_a": (unique_wins_a / unique_total) if unique_total else None,
        "unique_trajectory_wilson_ci_a": wilson_ci(unique_wins_a, unique_total - unique_draws) if sufficient and (unique_total - unique_draws) else None,
        "unique_trajectory_wilson_ci_status": "COMPUTED" if sufficient else "INSUFFICIENT_INDEPENDENT_SAMPLES",
        "statistically_interpretable": sufficient,
        "min_effective_independent_sample_size_threshold": min_effective_n,
    }


def _connected_components(participants: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[set[str]]:
    parent = {p: p for p in participants}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a, b in edges:
        union(a, b)
    groups: dict[str, set[str]] = defaultdict(set)
    for p in participants:
        groups[find(p)].add(p)
    return list(groups.values())


def fit_bradley_terry(pairwise_wins: Mapping[tuple[str, str], int], participants: Iterable[str], *, iterations: int = 200, epsilon: float = 1e-9) -> dict[str, Any]:
    """Fit Bradley-Terry log-strengths via the classical multiplicative MM update.

    ``pairwise_wins[(i, j)]`` = number of times ``i`` beat ``j`` (decisive
    outcomes only; draws are not modeled by this simple fit). Always
    returned with ``descriptive_only=True`` and
    ``statistically_supported_ranking=False``: with league-scale sample
    sizes this is a rating, not a statistically supported ranking claim
    (see the module docstring). A disconnected win/loss graph makes
    cross-component strengths non-identifiable, flagged via
    ``graph_connected``/``components``/``identifiability_warning`` rather
    than silently reported as comparable numbers.
    """
    participants = sorted(set(participants))
    strength = {p: 1.0 for p in participants}
    edges = [(i, j) for (i, j) in pairwise_wins if pairwise_wins.get((i, j), 0) > 0 or pairwise_wins.get((j, i), 0) > 0]
    components = _connected_components(participants, edges)
    for _ in range(iterations):
        new_strength = dict(strength)
        for i in participants:
            numerator = sum(pairwise_wins.get((i, j), 0) for j in participants if j != i)
            denominator = 0.0
            for j in participants:
                if j == i:
                    continue
                total_ij = pairwise_wins.get((i, j), 0) + pairwise_wins.get((j, i), 0)
                if total_ij:
                    denominator += total_ij / (strength[i] + strength[j])
            new_strength[i] = (numerator / denominator) if denominator > epsilon else strength[i]
        strength = new_strength
    log_strength = {p: math.log(max(strength[p], epsilon)) for p in participants}
    mean_log = (sum(log_strength.values()) / len(log_strength)) if log_strength else 0.0
    normalized = {p: value - mean_log for p, value in log_strength.items()}
    return {
        "schema_version": BRADLEY_TERRY_SCHEMA_VERSION,
        "log_strength": normalized,
        "normalization": "mean_log_strength_zero",
        "graph_connected": len(components) <= 1,
        "components": [sorted(c) for c in components],
        "descriptive_only": True,
        "statistically_supported_ranking": False,
        "identifiability_warning": None if len(components) <= 1 else "disconnected win/loss graph: log-strength is only comparable within the same component, not across components",
    }


def load_trajectory_evidence(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load a per-pair resumable trajectory-evidence store, keyed by ``str(match_index)``."""
    target = Path(path)
    if not target.exists():
        return {}
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OpponentError(f"trajectory evidence file is corrupt: {target}")
    return value


def record_trajectory_evidence(path: str | Path, match_index: int, record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Append one game's evidence to the resumable store and persist atomically.

    Keyed by ``str(match_index)``: since :func:`mage_ptcg.league.actual_runner.run_actual_league`
    only invokes its ``run_match`` callback for indices that are *not*
    already present in a resumed league artifact, this function is only
    ever called once per match index across a resumed multi-run league --
    a resumed run cannot produce two evidence entries for the same game.
    """
    existing = load_trajectory_evidence(path)
    existing[str(match_index)] = dict(record)
    atomic_write_json(Path(path), existing)
    return existing


def pairwise_wins_from_records(records: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], int]:
    """Build a ``{(winner, loser): count}`` map from trajectory-evidence records (draws excluded)."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for record in records:
        winner = record.get("winner_participant")
        side_a, side_b = record.get("participant_a"), record.get("participant_b")
        if winner in (None, "draw") or winner not in (side_a, side_b):
            continue
        loser = side_b if winner == side_a else side_a
        counts[(winner, loser)] += 1
    return dict(counts)
