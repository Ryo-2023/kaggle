"""Matchup statistics aggregation (O1-2 §7).

Reuses the repository's existing Wilson interval implementation
(``mage_ptcg.offline_training_v1_support.statistics.wilson_score_interval``)
rather than a fourth reimplementation (three already exist: the canonical
one plus two script-local copies noted in
``docs/plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md``).

Offline Training's collected data (``rule-bc-v1.jsonl``) does not carry a
per-episode winner (see ``replay_normalize.py`` module docstring for why), so
win/loss/draw aggregation here operates on whatever ``EpisodeRecord.winner``
values a caller actually supplies (which may all be ``None`` for this
source) -- ``games`` still counts, but ``wins``/``losses``/``draws`` and the
Wilson interval are computed only from episodes with a known winner, and the
gap is reported via ``unknown_result_count`` rather than silently treating a
missing winner as a draw or a loss.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from mage_ptcg.offline_training_v1_support.statistics import wilson_score_interval

from .canonical import digest
from .contracts import ContractError, EpisodeRecord

MATCHUP_STATISTICS_SCHEMA_VERSION = "matchup-statistics-v1"


@dataclass(frozen=True, slots=True)
class MatchupStatistics:
    schema_version: str
    group_key: Mapping[str, str]
    games: int
    wins: int
    losses: int
    draws: int
    unknown_result_count: int
    win_rate: float | None
    wilson_interval: tuple[float, float] | None
    effective_sample_size: int
    source_composition: Mapping[str, int]
    confidence: float

    def __post_init__(self) -> None:
        if self.schema_version != MATCHUP_STATISTICS_SCHEMA_VERSION:
            raise ContractError(f"unsupported MatchupStatistics schema_version {self.schema_version!r}")
        if self.games < 0 or self.wins < 0 or self.losses < 0 or self.draws < 0 or self.unknown_result_count < 0:
            raise ContractError("counts must be non-negative")
        if self.wins + self.losses + self.draws + self.unknown_result_count != self.games:
            raise ContractError("wins + losses + draws + unknown_result_count must equal games")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractError("confidence must be within [0, 1]")

    def content_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "group_key": dict(sorted(self.group_key.items())),
            "games": self.games,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "unknown_result_count": self.unknown_result_count,
            "win_rate": self.win_rate,
            "wilson_interval": list(self.wilson_interval) if self.wilson_interval else None,
            "effective_sample_size": self.effective_sample_size,
            "source_composition": dict(sorted(self.source_composition.items())),
            "confidence": self.confidence,
        }
        return digest(payload, domain="matchup-statistics")


def _group_key_for(episode: EpisodeRecord, *, own_seat: int) -> tuple[str, str]:
    """Own agent/model version + opponent identity as a stable group key."""
    own_agent = episode.agent_a if own_seat == 0 else episode.agent_b
    opponent_agent = episode.agent_b if own_seat == 0 else episode.agent_a
    return (own_agent or "unknown_agent", opponent_agent or "unknown_opponent")


def aggregate_matchup_statistics(
    episodes: Sequence[EpisodeRecord], *, own_seat: int = 0, minimum_confident_games: int = 20
) -> dict[tuple[str, str], MatchupStatistics]:
    """Aggregate episodes into per-(own_agent, opponent) ``MatchupStatistics``.

    ``own_seat`` selects which seat is treated as "own" for grouping
    purposes (0 or 1); this only affects grouping, not any actual gameplay
    interpretation, since ``EpisodeRecord`` does not encode seat-relative
    win/loss on its own (winner is a seat index 0/1, or ``None``).
    """
    if own_seat not in (0, 1):
        raise ContractError("own_seat must be 0 or 1")
    buckets: dict[tuple[str, str], list[EpisodeRecord]] = defaultdict(list)
    for episode in episodes:
        buckets[_group_key_for(episode, own_seat=own_seat)].append(episode)

    results: dict[tuple[str, str], MatchupStatistics] = {}
    for key, group in buckets.items():
        wins = losses = draws = unknown = 0
        source_counts: dict[str, int] = defaultdict(int)
        for episode in group:
            source_counts[episode.source_id] += 1
            if episode.winner is None:
                unknown += 1
            elif episode.winner == own_seat:
                wins += 1
            else:
                losses += 1
        games = len(group)
        decided = wins + losses + draws
        win_rate = (wins / decided) if decided else None
        interval = wilson_score_interval(wins, losses, draws) if decided else None
        confidence = min(1.0, decided / minimum_confident_games) if minimum_confident_games > 0 else 0.0
        results[key] = MatchupStatistics(
            schema_version=MATCHUP_STATISTICS_SCHEMA_VERSION,
            group_key={"own_agent": key[0], "opponent_agent": key[1]},
            games=games,
            wins=wins,
            losses=losses,
            draws=draws,
            unknown_result_count=unknown,
            win_rate=win_rate,
            wilson_interval=interval,
            effective_sample_size=decided,
            source_composition=dict(source_counts),
            confidence=confidence,
        )
    return results


__all__ = ["MATCHUP_STATISTICS_SCHEMA_VERSION", "MatchupStatistics", "aggregate_matchup_statistics"]
