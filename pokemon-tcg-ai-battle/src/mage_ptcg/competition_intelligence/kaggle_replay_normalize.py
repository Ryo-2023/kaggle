"""Fail-closed normalization of verified OWN_KAGGLE Replay payloads.

The official SDK episode-agent mapping is the only authority for which seat
belongs to this submission.  A Replay never gets to infer that fact itself.
The completed game's recorded action is checked for replay integrity, but is
*not* persisted as a behavior-cloning target.  Instead, a permitted training
example is independently re-labelled by the existing Rule v0 teacher from the
actor-visible observation and legal candidates at that instant.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from mage_ptcg.decision_state import DecisionStateError, build_decision_state

# ``student.dataset`` intentionally imports the repository-root ``agents``
# package shared by the submitted Rule v0 implementation.  Source installs
# expose only ``src/`` by default, so make this offline-only normalizer's
# repository dependency explicit before importing that existing contract.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from mage_ptcg.student.dataset import DatasetValidationError, RuleBCExample, build_rule_bc_example

from .canonical import digest
from .contracts import (
    DECISION_RECORD_SCHEMA_VERSION,
    DECK_OBSERVATION_SCHEMA_VERSION,
    EPISODE_RECORD_SCHEMA_VERSION,
    AllowedUse,
    DecisionRecord,
    DeckObservation,
    EpisodeRecord,
    SourceEnvelope,
    SourceKind,
)
from .phase import classify_phase
from .permissions import has_permission


NORMALIZER_VERSION = "competition-intelligence-kaggle-replay-normalizer-v1"
_SOURCE_QUALITY = "OWN_KAGGLE_REPLAY_RULE_V0_RELABELLED_V1"


@dataclass(frozen=True, slots=True)
class VerifiedEpisodeAgentMapping:
    """Ephemeral official proof for one Replay; raw identities never persist."""

    episode_id: str
    submission_id: str
    own_agent_index: int
    identity_hash: str
    episode_mapping_hash: str
    played_at: str | None
    agent_identity_hashes: tuple[str, str]

    def __post_init__(self) -> None:
        if not self.episode_id or not self.submission_id:
            raise ValueError("episode_id and submission_id are required")
        if self.own_agent_index not in (0, 1):
            raise ValueError("own_agent_index must be 0 or 1")
        if len(self.identity_hash) != 64 or len(self.episode_mapping_hash) != 64:
            raise ValueError("identity_hash and episode_mapping_hash must be sha256 digests")
        if len(self.agent_identity_hashes) != 2 or any(len(item) != 64 for item in self.agent_identity_hashes):
            raise ValueError("agent_identity_hashes must contain two sha256 digests")


@dataclass(frozen=True, slots=True)
class KaggleReplayNormalizationResult:
    episode: EpisodeRecord | None
    decisions: tuple[DecisionRecord, ...]
    deck_observations: tuple[DeckObservation, ...]
    training_examples: tuple[RuleBCExample, ...]
    excluded_decisions: tuple[Mapping[str, object], ...]
    schema_fingerprint: str
    schema_summary: Mapping[str, object]
    replay_audit: Mapping[str, int]
    quarantine_reason: str | None = None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _as_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def _schema_keys(value: object) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in value)) if isinstance(value, Mapping) else ()


def replay_schema_summary(raw_replay: Mapping[str, Any]) -> dict[str, object]:
    """Return a value-free structural fingerprint input for a Replay."""
    steps = raw_replay.get("steps")
    first_record: Mapping[str, Any] | None = None
    first_observation: Mapping[str, Any] | None = None
    first_current: Mapping[str, Any] | None = None
    first_select: Mapping[str, Any] | None = None
    first_players: list[Any] | None = None
    first_visualize: object = None
    if isinstance(steps, list) and steps and isinstance(steps[0], list) and steps[0]:
        candidate = steps[0][0]
        if isinstance(candidate, Mapping):
            first_record = candidate
            first_observation = candidate.get("observation") if isinstance(candidate.get("observation"), Mapping) else None
            if first_observation:
                first_current = first_observation.get("current") if isinstance(first_observation.get("current"), Mapping) else None
                first_select = first_observation.get("select") if isinstance(first_observation.get("select"), Mapping) else None
                first_players = first_current.get("players") if isinstance(first_current, Mapping) and isinstance(first_current.get("players"), list) else None
            first_visualize = candidate.get("visualize")
    player_keys = ()
    if isinstance(first_players, list) and first_players and isinstance(first_players[0], Mapping):
        player_keys = _schema_keys(first_players[0])
    visualize_record_keys = ()
    if isinstance(first_visualize, list) and first_visualize and isinstance(first_visualize[0], Mapping):
        visualize_record_keys = _schema_keys(first_visualize[0])
    return {
        "top_level_keys": _schema_keys(raw_replay),
        "info_keys": _schema_keys(raw_replay.get("info")),
        "steps_type": type(steps).__name__,
        "agent_records_per_step": tuple(sorted({len(step) for step in steps if isinstance(step, list)})) if isinstance(steps, list) else (),
        "agent_record_keys": _schema_keys(first_record),
        "observation_keys": _schema_keys(first_observation),
        "current_keys": _schema_keys(first_current),
        "select_keys": _schema_keys(first_select),
        "player_keys": player_keys,
        "visualize_type": type(first_visualize).__name__,
        "visualize_record_keys": visualize_record_keys,
    }


def replay_schema_fingerprint(raw_replay: Mapping[str, Any]) -> tuple[str, dict[str, object]]:
    summary = replay_schema_summary(raw_replay)
    return digest(summary, domain="kaggle-replay-schema-v1"), summary


def _episode_id(raw_replay: Mapping[str, Any]) -> str | None:
    info = raw_replay.get("info")
    if not isinstance(info, Mapping):
        return None
    value = info.get("EpisodeId", info.get("episodeId", info.get("id")))
    return str(value) if isinstance(value, (str, int)) and str(value) else None


def _selected_indices(record: Mapping[str, Any], *, option_count: int, minimum: int, maximum: int) -> tuple[int, ...] | None:
    action = record.get("action")
    if not isinstance(action, list) or any(type(index) is not int for index in action):
        return None
    if len(action) < minimum or len(action) > maximum or len(set(action)) != len(action):
        return None
    if any(index < 0 or index >= option_count for index in action):
        return None
    return tuple(action)


def _public_cards(public_state: Mapping[str, Any]) -> tuple[int, ...]:
    seen: set[int] = set()
    for side in ("self", "opponent"):
        state = public_state.get(side)
        if not isinstance(state, Mapping):
            continue
        for zone in ("active", "bench", "discard"):
            for card in state.get(zone, ()) if isinstance(state.get(zone), list) else ():
                if isinstance(card, Mapping):
                    fields = card.get("fields")
                    if isinstance(fields, Mapping) and type(fields.get("id")) is int:
                        seen.add(fields["id"])
    return tuple(sorted(seen))


def _board_summary(public_state: Mapping[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {}
    for side in ("self", "opponent"):
        state = public_state.get(side)
        if isinstance(state, Mapping):
            result[side] = {
                "prize_count": state.get("prize_count"),
                "hand_count": state.get("hand_count"),
                "deck_count": state.get("deck_count"),
                "bench_count": len(state.get("bench", ())),
                "discard_count": len(state.get("discard", ())),
                "active_present": bool(state.get("active")),
            }
    result["board"] = dict(public_state.get("board", {})) if isinstance(public_state.get("board"), Mapping) else {}
    return result


def _phase(public_state: Mapping[str, Any], *, is_last: bool) -> str:
    self_state = public_state.get("self") if isinstance(public_state.get("self"), Mapping) else {}
    other_state = public_state.get("opponent") if isinstance(public_state.get("opponent"), Mapping) else {}
    return classify_phase(
        turn=public_state.get("turn") if type(public_state.get("turn")) is int else None,
        self_prize_count=self_state.get("prize_count") if type(self_state.get("prize_count")) is int else None,
        opponent_prize_count=other_state.get("prize_count") if type(other_state.get("prize_count")) is int else None,
        self_active_present=bool(self_state.get("active")),
        opponent_active_present=bool(other_state.get("active")),
        termination_proximate=is_last,
    ).phase


def _exact_decks(raw_replay: Mapping[str, Any]) -> tuple[dict[int, int] | None, dict[int, int] | None]:
    """Extract only unordered 60-card multisets from post-game visualization."""
    steps = raw_replay.get("steps")
    if not isinstance(steps, list) or not steps or not isinstance(steps[0], list):
        return None, None
    candidates = [record.get("visualize") for record in steps[0] if isinstance(record, Mapping)]
    for visualize in candidates:
        if not isinstance(visualize, list) or not visualize or not isinstance(visualize[0], Mapping):
            continue
        decks = visualize[0].get("action")
        if not isinstance(decks, list) or len(decks) != 2:
            continue
        result: list[dict[int, int] | None] = []
        for deck in decks:
            if not isinstance(deck, list) or len(deck) != 60 or any(type(card_id) is not int for card_id in deck):
                result.append(None)
            else:
                result.append(dict(sorted(Counter(deck).items())))
        return result[0], result[1]
    return None, None


def _deck_observation(episode_id: str, seat: int, deck: Mapping[int, int] | None) -> DeckObservation:
    return DeckObservation(
        schema_version=DECK_OBSERVATION_SCHEMA_VERSION,
        episode_id=episode_id,
        seat=seat,
        exact_decklist=dict(deck) if deck is not None else None,
        exact_decklist_source="replay_visualize_post_game_unordered_multiset" if deck is not None else None,
        observed_card_counts={},
        inferred_archetypes={},
        inferred_card_distribution={},
        inference_model_version=None,
        confidence=1.0 if deck is not None else 0.0,
    )


def _excluded(step: int, reason: str) -> dict[str, object]:
    return {"step": step, "reason": reason}


def normalize_kaggle_replay(
    raw_replay: Mapping[str, Any],
    verified_episode_agent_mapping: VerifiedEpisodeAgentMapping,
    source_envelope: SourceEnvelope,
) -> KaggleReplayNormalizationResult:
    """Normalize one raw Replay using its separately verified SDK mapping.

    Invalid individual decisions are excluded with a reason. Structural or
    identity failures quarantine the whole Replay by returning no episode.
    """
    schema_fingerprint, schema_summary = replay_schema_fingerprint(raw_replay)
    empty = lambda reason: KaggleReplayNormalizationResult(
        None, (), (), (), (), schema_fingerprint, schema_summary, {}, reason
    )
    if source_envelope.source_kind is not SourceKind.OWN_KAGGLE:
        return empty("IDENTITY_UNRESOLVED")
    if not has_permission(source_envelope, AllowedUse.TRAINING):
        return empty("SUBMISSION_MAPPING_MISSING")
    if _episode_id(raw_replay) != verified_episode_agent_mapping.episode_id:
        return empty("SUBMISSION_MAPPING_MISSING")
    try:
        steps = _as_list(raw_replay.get("steps"), "steps")
    except ValueError:
        return empty("SCHEMA_DRIFT")
    if not steps or any(not isinstance(step, list) or len(step) != 2 or any(not isinstance(item, Mapping) for item in step) for step in steps):
        return empty("SCHEMA_DRIFT")

    own_seat = verified_episode_agent_mapping.own_agent_index
    exact_decks = _exact_decks(raw_replay)
    decisions: list[DecisionRecord] = []
    examples: list[RuleBCExample] = []
    excluded: list[Mapping[str, object]] = []
    history: list[str] = []
    audit = Counter()
    for step_index, row in enumerate(steps[:-1]):
        own_record = _as_mapping(row[own_seat], "step record")
        if own_record.get("status") != "ACTIVE":
            continue
        observation = own_record.get("observation")
        if not isinstance(observation, Mapping):
            excluded.append(_excluded(step_index, "MISSING_OWN_OBSERVATION")); continue
        current = observation.get("current")
        select = observation.get("select")
        if not isinstance(current, Mapping) or not isinstance(select, Mapping):
            excluded.append(_excluded(step_index, "SCHEMA_DRIFT")); continue
        if current.get("yourIndex") != own_seat:
            excluded.append(_excluded(step_index, "ACTOR_INDEX_MISMATCH")); continue
        players = current.get("players")
        if not isinstance(players, list) or len(players) != 2 or not isinstance(players[1 - own_seat], Mapping):
            excluded.append(_excluded(step_index, "SCHEMA_DRIFT")); continue
        if players[1 - own_seat].get("hand") is not None:
            excluded.append(_excluded(step_index, "OPPONENT_PRIVATE_HAND_EXPOSED")); continue
        if current.get("result") not in (None, -1):
            excluded.append(_excluded(step_index, "FUTURE_INFORMATION_RISK")); continue
        options = select.get("option")
        min_count, max_count = select.get("minCount"), select.get("maxCount")
        if not isinstance(options, list) or type(min_count) is not int or type(max_count) is not int:
            excluded.append(_excluded(step_index, "MISSING_LEGAL_CANDIDATES")); continue
        recorded_indices = _selected_indices(
            _as_mapping(steps[step_index + 1][own_seat], "next step record"),
            option_count=len(options), minimum=min_count, maximum=max_count,
        )
        if recorded_indices is None:
            excluded.append(_excluded(step_index, "CHOSEN_ACTION_NOT_LEGAL")); continue
        try:
            state = build_decision_state(observation, visible_history=tuple(history[-64:]))
        except DecisionStateError:
            excluded.append(_excluded(step_index, "ACTION_NORMALIZATION_FAILED")); continue
        if len(state.legal_actions) != len(options):
            excluded.append(_excluded(step_index, "ACTION_NORMALIZATION_FAILED")); continue
        deck_counts = exact_decks[own_seat]
        if deck_counts is None:
            excluded.append(_excluded(step_index, "DECK_SCHEMA_INVALID")); continue
        try:
            example = build_rule_bc_example(
                observation, deck=[card_id for card_id, count in deck_counts.items() for _ in range(count)],
                source_id=source_envelope.source_id,
                source_revision=f"{NORMALIZER_VERSION}:{source_envelope.raw_sha256}:{verified_episode_agent_mapping.episode_mapping_hash}",
                visible_history=tuple(history[-64:]),
            )
        except DatasetValidationError:
            excluded.append(_excluded(step_index, "ACTION_NORMALIZATION_FAILED")); continue
        # The existing offline dataset builder's duplicate boundary is
        # ``(redacted source_id, decision_index)``.  Preserve the original
        # replay step only as a non-secret string so distinct own decisions
        # cannot collapse into one record during materialization.
        example = replace(example, metadata={**example.metadata, "decision_index": str(step_index)})
        action_by_digest = {action["digest"]: action for action in example.legal_actions}
        target = tuple(example.target_action_digests)
        chosen_action_key = target[0] if len(target) == 1 else None
        actor_view = {
            "actor_visibility_valid": True,
            "privacy_valid": True,
            "public_state": state.actor_view.public_state,
            "own_private_state": state.actor_view.own_private_state,
            "limited_knowledge": {},
            "visible_history": list(state.actor_view.visible_history),
            "teacher_ranking": [list(pair) for pair in example.teacher_ranking],
            "teacher_label_origin": "rule_v0_relabelled_from_actor_visible_replay_state",
            "recorded_action_checked_legal_not_used_as_teacher": True,
            "schema_fingerprint": schema_fingerprint,
            "source_content_hash": source_envelope.raw_sha256,
            "episode_mapping_hash": verified_episode_agent_mapping.episode_mapping_hash,
        }
        public_state = state.actor_view.public_state
        decision = DecisionRecord(
            schema_version=DECISION_RECORD_SCHEMA_VERSION,
            episode_id=verified_episode_agent_mapping.episode_id,
            decision_index=len(decisions),
            actor_seat=own_seat,
            turn_index=public_state.get("turn") if type(public_state.get("turn")) is int else step_index,
            phase=_phase(public_state, is_last=step_index == len(steps) - 2),
            actor_information_view=actor_view,
            legal_action_keys=tuple(sorted(action_by_digest)),
            chosen_action_key=chosen_action_key,
            chosen_action_raw={"teacher_action_keys": sorted(target), "recorded_action_checked_legal": True},
            public_cards_seen=_public_cards(public_state),
            board_summary=_board_summary(public_state),
            latency_us=None,
            fallback_used=False,
            result_to_go=None,
            source_quality=_SOURCE_QUALITY,
        )
        decisions.append(decision)
        examples.append(example)
        history.append(state.actor_view.public_state_digest)
        audit["teacher_relabelled_decisions"] += 1
        audit["recorded_actions_legal"] += 1

    rewards = raw_replay.get("rewards")
    winner = None
    if isinstance(rewards, list) and len(rewards) == 2 and all(type(item) in (int, float) for item in rewards):
        if rewards[0] != rewards[1]:
            winner = 0 if rewards[0] > rewards[1] else 1
    statuses = raw_replay.get("statuses")
    termination_reason = "replay_terminal" if isinstance(statuses, list) and any(item == "DONE" for item in statuses) else None
    trace = [{"decision": decision.content_hash(), "public": decision.actor_information_view.get("public_state") if decision.actor_information_view else None} for decision in decisions]
    episode = EpisodeRecord(
        schema_version=EPISODE_RECORD_SCHEMA_VERSION,
        episode_id=verified_episode_agent_mapping.episode_id,
        source_id=source_envelope.source_id,
        competition_id=str(raw_replay.get("id")) if isinstance(raw_replay.get("id"), str) else None,
        played_at=verified_episode_agent_mapping.played_at,
        engine_version=str(raw_replay.get("version")) if isinstance(raw_replay.get("version"), str) else None,
        agent_a=f"sha256:{verified_episode_agent_mapping.agent_identity_hashes[0]}",
        agent_b=f"sha256:{verified_episode_agent_mapping.agent_identity_hashes[1]}",
        deck_a_reference=_deck_observation(verified_episode_agent_mapping.episode_id, 0, exact_decks[0]).content_hash() if exact_decks[0] else None,
        deck_b_reference=_deck_observation(verified_episode_agent_mapping.episode_id, 1, exact_decks[1]).content_hash() if exact_decks[1] else None,
        first_player=None,
        winner=winner,
        termination_reason=termination_reason,
        turn_count=len(steps),
        decision_count=len(decisions),
        public_trace_hash=digest(trace, domain="kaggle-replay-public-trace-v1"),
        quality_flags=frozenset({"OWN_KAGGLE", "ACTOR_VISIBLE", "RULE_V0_RELABELLED", f"schema:{schema_fingerprint[:12]}"}),
    )
    deck_observations = tuple(_deck_observation(episode.episode_id, seat, deck) for seat, deck in enumerate(exact_decks))
    audit["excluded_decisions"] = len(excluded)
    audit["privacy_violations"] = sum(item["reason"] == "OPPONENT_PRIVATE_HAND_EXPOSED" for item in excluded)
    audit["actor_visibility_violations"] = sum(item["reason"] in {"ACTOR_INDEX_MISMATCH", "MISSING_OWN_OBSERVATION"} for item in excluded)
    return KaggleReplayNormalizationResult(
        episode, tuple(decisions), deck_observations, tuple(examples), tuple(excluded),
        schema_fingerprint, schema_summary, dict(sorted(audit.items())), None,
    )


__all__ = [
    "KaggleReplayNormalizationResult", "NORMALIZER_VERSION", "VerifiedEpisodeAgentMapping",
    "normalize_kaggle_replay", "replay_schema_fingerprint", "replay_schema_summary",
]
