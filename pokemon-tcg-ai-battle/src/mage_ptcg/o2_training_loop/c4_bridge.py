"""Adapter: O2's match plan drives C4's actual collector, unmodified on both sides.

This module builds no new persisted schema.  It maps an already-built
``MatchSpec`` list onto ``dataops.collector.ActualEpisodeLineageInput`` and
calls the existing collector; O2's own ``build_match_matrix`` and cabt
resolvers are reused verbatim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.competition_intelligence.canonical import digest
from mage_ptcg.dataops.collector import ActualEpisodeLineageInput, collect_actual_dataset

from .cabt import resolve_real_agent, resolve_real_deck
from .core import DeckEntry, MatchSpec, O2ContractError, OpponentEntry


def _spec_hash(spec: MatchSpec) -> str:
    return digest(spec.to_dict(), domain="o2-match-spec-c4-lineage-v0")


def build_episode_lineage_inputs(
    specs: Sequence[MatchSpec],
    *,
    challenger_id: str,
    opponents: Mapping[str, OpponentEntry],
    decks: Mapping[str, DeckEntry],
    repository_root: str | Path,
) -> list[ActualEpisodeLineageInput]:
    """Map each O2 ``MatchSpec`` onto an ``ActualEpisodeLineageInput``.

    ``spec.first_player`` is the seat O2 placed the challenger in (see
    ``core.build_match_matrix``), not a claim about turn order.  Every entry
    must place the challenger in its own declared seat and this bridge's MVP
    supports exactly one own agent and one opponent per collection run.
    """
    entries: list[ActualEpisodeLineageInput] = []
    for spec in specs:
        own_seat = spec.first_player
        own_agent_id, opponent_agent_id = (
            (spec.player_a_agent, spec.player_b_agent) if own_seat == 0 else (spec.player_b_agent, spec.player_a_agent)
        )
        if own_agent_id != challenger_id:
            raise O2ContractError(f"spec {spec.match_id!r} does not place the challenger in its own declared seat")
        own_deck_id, opponent_deck_id = (
            (spec.player_a_deck, spec.player_b_deck) if own_seat == 0 else (spec.player_b_deck, spec.player_a_deck)
        )
        _, own_deck_hash = resolve_real_deck(decks[own_deck_id], repository_root=repository_root)
        _, opponent_deck_hash = resolve_real_deck(decks[opponent_deck_id], repository_root=repository_root)
        entries.append(
            ActualEpisodeLineageInput(
                match_id=spec.match_id,
                plan_hash=spec.plan_hash,
                match_spec_hash=_spec_hash(spec),
                backend_kind="cabt",
                requested_seed=spec.seed,
                engine_seed_supported=False,
                seat_index=own_seat,
                player_side="A" if own_seat == 0 else "B",
                own_agent_id=own_agent_id,
                opponent_agent_id=opponent_agent_id,
                own_implementation_hash=opponents[own_agent_id].implementation_hash,
                opponent_implementation_hash=opponents[opponent_agent_id].implementation_hash,
                own_deck_hash=own_deck_hash,
                opponent_deck_hash=opponent_deck_hash,
                pair_id=spec.pair_id,
            )
        )
    own_ids = {entry.own_agent_id for entry in entries}
    opponent_ids = {entry.opponent_agent_id for entry in entries}
    if len(own_ids) > 1 or len(opponent_ids) > 1:
        raise O2ContractError("bridge MVP supports exactly one own agent and one opponent per collection run")
    return entries


def run_o2_actual_collection(
    *,
    specs: Sequence[MatchSpec],
    challenger_id: str,
    opponents: Mapping[str, OpponentEntry],
    decks: Mapping[str, DeckEntry],
    repository_root: str | Path,
    output_root: str | Path,
    run_id: str,
    base_seed: int,
    canonical_base_sha: str,
    max_steps: int = 10_000,
    validation_percent: int = 20,
    split_seed: int = 0,
) -> dict[str, object]:
    """Drive O2's real Rule-vs-opponent match plan through C4's O2 lineage mode.

    ``specs`` is O2's own already-built match plan (``build_match_matrix``
    output); this function never regenerates or reorders it.  The own/
    opponent deck and agent are resolved exactly once via O2's existing
    ``resolve_real_deck``/``resolve_real_agent`` and reused for every match,
    matching this bridge's single-challenger/single-opponent MVP scope.
    """
    if not specs:
        raise O2ContractError("specs must not be empty")
    entries = build_episode_lineage_inputs(
        specs, challenger_id=challenger_id, opponents=opponents, decks=decks, repository_root=repository_root,
    )
    first_spec = specs[0]
    own_deck_id = first_spec.player_a_deck if first_spec.first_player == 0 else first_spec.player_b_deck
    opponent_deck_id = first_spec.player_b_deck if first_spec.first_player == 0 else first_spec.player_a_deck
    own_deck_path, _ = resolve_real_deck(decks[own_deck_id], repository_root=repository_root)
    opponent_deck_path, _ = resolve_real_deck(decks[opponent_deck_id], repository_root=repository_root)
    opponent_agent_factory = resolve_real_agent(opponents[entries[0].opponent_agent_id], repository_root=repository_root)
    return collect_actual_dataset(
        run_id=run_id,
        games=len(specs),
        base_seed=base_seed,
        output_root=output_root,
        canonical_base_sha=canonical_base_sha,
        deck_path=own_deck_path,
        repository_root=repository_root,
        max_steps=max_steps,
        validation_percent=validation_percent,
        split_seed=split_seed,
        episode_lineage_inputs=entries,
        opponent_deck_path=opponent_deck_path,
        opponent_agent_factory=opponent_agent_factory,
    )


__all__ = ["build_episode_lineage_inputs", "run_o2_actual_collection"]
