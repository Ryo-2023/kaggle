"""Normalize a discovered Offline Training run into canonical Episode/Decision records.

Builds records directly from ``rule-bc-v1.jsonl`` rows (see
``offline_reader.py``). Fields with no corresponding signal in
``offline_training``'s collected data are set to ``None``/flagged rather than
fabricated. Known limitations of this source, as of this slice:

- no ``winner`` / ``termination_reason`` per episode (the collector consumes
  and discards the match result; it is never persisted to any artifact);
- no per-decision ``latency``;
- no per-decision ``result_to_go`` / reward signal;
- ``fallback_used`` is always ``False`` by construction in this source (a
  schema placeholder in ``RuleBCExample``, not real fallback detection) --
  treat it as "not populated", not as ground truth.

Stable ActionKey digests are used verbatim from the source rows (never
recomputed). ``actor_information_view`` holds exactly the acting player's own
view (own hand + public board state) already redacted by the original
collector -- opponent hand contents are never present in this source, so
nothing here can leak them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .canonical import digest
from .contracts import (
    DECISION_RECORD_SCHEMA_VERSION,
    EPISODE_RECORD_SCHEMA_VERSION,
    ContractError,
    DecisionRecord,
    EpisodeRecord,
)
from .offline_reader import iter_rule_bc_rows
from .phase import classify_phase

NORMALIZER_VERSION = "competition-intelligence-offline-training-normalizer-v1"


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    episodes: tuple[EpisodeRecord, ...]
    decisions: tuple[DecisionRecord, ...]
    quarantined_rows: tuple[dict[str, Any], ...]
    source_row_count: int
    valid_row_count: int


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_card_ids(zone: Any) -> set[int]:
    ids: set[int] = set()
    if isinstance(zone, list):
        for item in zone:
            if isinstance(item, Mapping):
                card_id = item.get("id")
                if isinstance(card_id, int) and not isinstance(card_id, bool):
                    ids.add(card_id)
    return ids


def _board_summary(public_state: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for side in ("self", "opponent"):
        side_state = public_state.get(side)
        if not isinstance(side_state, Mapping):
            continue
        summary[side] = {
            "prize_count": side_state.get("prize_count"),
            "hand_count": side_state.get("hand_count"),
            "deck_count": side_state.get("deck_count"),
            "bench_count": len(side_state.get("bench") or []),
            "active_present": bool(side_state.get("active")),
            "discard_count": len(side_state.get("discard") or []),
        }
    board = public_state.get("board")
    if isinstance(board, Mapping):
        summary["board"] = dict(board)
    return summary


def _normalize_decision(
    row: Mapping[str, Any], *, episode_id: str, position: int, is_last: bool
) -> tuple[DecisionRecord | None, dict[str, Any] | None]:
    decision_index = _int_or_none(row["metadata"].get("decision_index"))
    if decision_index is None:
        decision_index = position
    actor_seat = _int_or_none(row["metadata"].get("seat"))
    if actor_seat not in (0, 1):
        return None, {"episode_id": episode_id, "decision_index": decision_index, "reason": f"invalid seat {actor_seat!r}"}

    public_state = row["public_state"]
    turn = _int_or_none(public_state.get("turn")) if isinstance(public_state, Mapping) else None
    self_state = public_state.get("self") if isinstance(public_state, Mapping) else None
    opponent_state = public_state.get("opponent") if isinstance(public_state, Mapping) else None
    self_prizes = _int_or_none(self_state.get("prize_count")) if isinstance(self_state, Mapping) else None
    opponent_prizes = _int_or_none(opponent_state.get("prize_count")) if isinstance(opponent_state, Mapping) else None
    self_active = bool(self_state.get("active")) if isinstance(self_state, Mapping) else None
    opponent_active = bool(opponent_state.get("active")) if isinstance(opponent_state, Mapping) else None

    classification = classify_phase(
        turn=turn,
        self_prize_count=self_prizes,
        opponent_prize_count=opponent_prizes,
        self_active_present=self_active,
        opponent_active_present=opponent_active,
        termination_proximate=is_last,
    )

    legal_action_keys = tuple(action["digest"] for action in row["legal_actions"])
    target_digests = row["target_action_digests"]
    chosen_action_key: str | None = None
    chosen_action_raw: dict[str, Any] | None = None
    if len(target_digests) == 1:
        chosen_action_key = target_digests[0]
        match = next((action for action in row["legal_actions"] if action.get("digest") == chosen_action_key), None)
        if match is not None and isinstance(match.get("payload"), Mapping):
            chosen_action_raw = dict(match["payload"])
    elif len(target_digests) > 1:
        chosen_action_raw = {"multi_select_digests": list(target_digests)}

    public_cards: set[int] = set()
    for side_state in (self_state, opponent_state):
        if isinstance(side_state, Mapping):
            for zone_name in ("active", "bench", "discard"):
                public_cards |= _extract_card_ids(side_state.get(zone_name))

    own_private_state = row.get("own_private_state")
    teacher_ranking = row.get("teacher_ranking")
    actor_view: dict[str, Any] = {
        "public_state": dict(public_state) if isinstance(public_state, Mapping) else {},
        "own_private_state": dict(own_private_state) if isinstance(own_private_state, Mapping) else {},
        "visible_history": list(row.get("visible_history") or []),
        "phase_reason": classification.reason,
        # Rule v0's own per-candidate priority scores, carried through
        # verbatim (never recomputed) so O1-2's high-information selector can
        # ground a "small top-2 margin" signal in real data instead of
        # marking it unavailable; this is a priority score, not a learned
        # position-value estimate, so it is not used for anything claiming
        # to be a value function.
        "teacher_ranking": [list(pair) for pair in teacher_ranking] if isinstance(teacher_ranking, list) else None,
    }

    try:
        decision = DecisionRecord(
            schema_version=DECISION_RECORD_SCHEMA_VERSION,
            episode_id=episode_id,
            decision_index=decision_index,
            actor_seat=actor_seat,
            turn_index=turn if turn is not None else 0,
            phase=classification.phase,
            actor_information_view=actor_view,
            legal_action_keys=legal_action_keys,
            chosen_action_key=chosen_action_key,
            chosen_action_raw=chosen_action_raw,
            public_cards_seen=tuple(sorted(public_cards)),
            board_summary=_board_summary(public_state) if isinstance(public_state, Mapping) else None,
            latency_us=None,
            fallback_used=bool(row.get("fallback_used", False)),
            result_to_go=None,
            source_quality="offline-training-v1-rule-bc-v1",
        )
    except ContractError as exc:
        return None, {"episode_id": episode_id, "decision_index": decision_index, "reason": f"invalid decision record: {exc}"}
    return decision, None


def normalize_rule_bc_jsonl(path: str | Path, *, source_id: str) -> NormalizationResult:
    """Normalize one ``rule-bc-v1.jsonl`` file into Episode/Decision records.

    ``source_id`` (this function's own keyword parameter) is the id of the
    ``SourceEnvelope`` this file was archived under: one ingested file may
    contain many episodes, and every episode produced here shares that same
    envelope ``source_id`` (see ``EpisodeRecord(source_id=source_id, ...)``
    below).

    KNOWN SCHEMA NAMING COLLISION (independent-audit finding #5, minimal-fix
    scope -- a full ``rule-bc-v2`` schema separating ``episode_id``/
    ``source_envelope_id``/``replay_id``/``match_id`` was considered too
    large a change for this remediation pass): each raw ``rule-bc-v1.jsonl``
    *row* also has its own ``"source_id"`` key, inherited unchanged from
    ``mage_ptcg.offline_training``'s producer schema -- but that key actually
    holds the **episode** identity (its grouping key below, ``row_episode_id``,
    becomes ``EpisodeRecord.episode_id``), not a ``SourceEnvelope`` id. The
    two same-named concepts are read from entirely different places (one
    ingested file's raw rows vs. this function's own keyword argument) and
    are never conflated in the code that follows, but a reader must not
    assume ``row["source_id"]`` means the same thing as this function's
    ``source_id`` parameter.
    """
    rows_by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quarantined: list[dict[str, Any]] = []
    source_row_count = 0
    valid_row_count = 0

    for line_number, row, error in iter_rule_bc_rows(path):
        source_row_count += 1
        if row is None:
            quarantined.append({"line_number": line_number, "reason": error})
            continue
        valid_row_count += 1
        row_episode_id = row["source_id"]  # see the KNOWN SCHEMA NAMING COLLISION note above
        rows_by_episode[row_episode_id].append(row)

    episodes: list[EpisodeRecord] = []
    decisions: list[DecisionRecord] = []

    for episode_id, rows in rows_by_episode.items():
        ordered = sorted(rows, key=lambda item: _int_or_none(item["metadata"].get("decision_index")) or 0)
        decision_records: list[DecisionRecord] = []
        max_turn = 0
        for position, row in enumerate(ordered):
            decision, error = _normalize_decision(
                row, episode_id=episode_id, position=position, is_last=(position == len(ordered) - 1)
            )
            if error is not None:
                quarantined.append(error)
                continue
            assert decision is not None
            max_turn = max(max_turn, decision.turn_index)
            decision_records.append(decision)

        if not decision_records:
            continue
        decisions.extend(decision_records)

        first_row = ordered[0]
        first_player = _int_or_none(first_row["public_state"].get("first_player"))
        agent_by_seat: dict[int, str | None] = {}
        for row in ordered:
            seat = _int_or_none(row["metadata"].get("seat"))
            if seat in (0, 1) and seat not in agent_by_seat:
                agent_by_seat[seat] = row["metadata"].get("source_agent")

        ordered_decisions = sorted(decision_records, key=lambda d: d.decision_index)
        trace_payload = [
            [d.decision_index, d.chosen_action_key, list(d.legal_action_keys or ())] for d in ordered_decisions
        ]
        public_trace_hash = digest({"episode_id": episode_id, "decisions": trace_payload}, domain="episode-public-trace")

        try:
            episode = EpisodeRecord(
                schema_version=EPISODE_RECORD_SCHEMA_VERSION,
                episode_id=episode_id,
                source_id=source_id,
                competition_id=None,
                played_at=None,
                engine_version=None,
                agent_a=agent_by_seat.get(0),
                agent_b=agent_by_seat.get(1),
                deck_a_reference=first_row.get("deck_fingerprint"),
                deck_b_reference=None,
                first_player=first_player,
                winner=None,
                termination_reason=None,
                turn_count=max_turn,
                decision_count=len(decision_records),
                public_trace_hash=public_trace_hash,
                quality_flags=frozenset({
                    "winner_unavailable_in_source",
                    "termination_reason_unavailable_in_source",
                    "turn_count_is_max_observed_decision_turn",
                }),
            )
        except ContractError as exc:
            quarantined.append({"episode_id": episode_id, "reason": f"invalid episode record: {exc}"})
            continue
        episodes.append(episode)

    return NormalizationResult(
        episodes=tuple(episodes),
        decisions=tuple(decisions),
        quarantined_rows=tuple(quarantined),
        source_row_count=source_row_count,
        valid_row_count=valid_row_count,
    )


__all__ = ["NORMALIZER_VERSION", "NormalizationResult", "normalize_rule_bc_jsonl"]
