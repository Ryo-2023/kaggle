"""Privacy-safe normalization of official cabt agent observations into a JSONL trace.

Only bounded, observed, primitive scalar fields are persisted. Opponent hidden
information (hand contents, prize contents, deck contents/order) is never
retained, and three known-opaque or unbounded observation fields
(``search_begin_input``, ``logs``, ``remainingOverageTime``) are excluded by
construction and re-checked defensively before every write. See
``docs/evidence/cabt-observation-schema.md`` for provenance and rationale.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SOURCE = "official_cabt_agent_observation"

# Fields excluded by explicit design decision: opaque/possibly hidden-state
# (search_begin_input), unboundedly growing (logs), or agent time budget
# metadata unrelated to game state (remainingOverageTime).
FORBIDDEN_OBSERVATION_KEYS: tuple[str, ...] = (
    "search_begin_input",
    "logs",
    "remainingOverageTime",
)

# select.type == 0 is the only enum value with a name established in main.py
# (`_MAIN_SELECT_TYPE`). Other observed select.type values (1, 4, 8, 9) have
# unresolved semantics and are intentionally left unnamed.
SELECT_TYPE_NAMES: dict[int, str] = {0: "MAIN"}

# option.type names established in main.py's `_OPTION_TYPE_NAMES`. Other
# observed option.type values (0, 1, 2, 3, 6, 12) have unresolved semantics
# and are intentionally left unnamed.
OPTION_TYPE_NAMES: dict[int, str] = {
    7: "PLAY",
    8: "ATTACH",
    9: "EVOLVE",
    10: "ABILITY",
    13: "ATTACK",
    14: "END",
}

# Safe scalar allowlist for legal-option fields, per the handoff report.
OPTION_SCALAR_FIELDS: tuple[str, ...] = (
    "index",
    "area",
    "inPlayArea",
    "inPlayIndex",
    "playerIndex",
    "energyIndex",
    "count",
    "number",
    "attackId",
)

# Safe scalar allowlist for a public zone card (active/bench/discard/hand
# element), observed via a single targeted probe of real cabt output.
CARD_SCALAR_FIELDS: tuple[str, ...] = (
    "id",
    "serial",
    "playerIndex",
    "hp",
    "maxHp",
    "appearThisTurn",
)

# Nested list fields on a card that are reduced to a count rather than
# persisted in full (energy/tool/evolution line contents).
CARD_LIST_COUNT_FIELDS: tuple[str, ...] = (
    "energies",
    "energyCards",
    "tools",
    "preEvolution",
)

STATUS_FLAG_FIELDS: tuple[str, ...] = (
    "poisoned",
    "burned",
    "asleep",
    "paralyzed",
    "confused",
)


class MalformedObservationError(ValueError):
    """Raised when an observation does not match the official agent contract."""


class PrivacyInvariantError(RuntimeError):
    """Raised when a normalized record would leak a forbidden observation field.

    This should never trigger in practice; it is a defense-in-depth check
    against a bug in the normalizers themselves, not user input validation.
    """


def _is_primitive_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def find_forbidden_keys(value: Any) -> list[str]:
    """Recursively scan a (already-plain) structure for forbidden key names."""
    found: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, sub_value in node.items():
                if key in FORBIDDEN_OBSERVATION_KEYS:
                    found.add(key)
                _walk(sub_value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return sorted(found)


def canonical_deck_sha256(deck_card_ids: Sequence[int]) -> str:
    """Hash a 60-card deck's composition, independent of submission order."""
    ids = [int(card_id) for card_id in deck_card_ids]
    canonical = json.dumps(sorted(ids), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_option(option: Any, option_index: int) -> dict[str, Any]:
    """Reduce one legal-option object to its bounded scalar allowlist."""
    if not isinstance(option, Mapping):
        raise MalformedObservationError("option must be a mapping")

    type_value = option.get("type")
    fields: dict[str, Any] = {}
    unknown_keys: list[str] = []
    for key in option:
        if key == "type":
            continue
        if key in OPTION_SCALAR_FIELDS:
            value = option.get(key)
            if _is_primitive_scalar(value):
                fields[key] = value
            else:
                unknown_keys.append(key)
        else:
            unknown_keys.append(key)

    return {
        "option_index": option_index,
        "type": type_value,
        "type_name": OPTION_TYPE_NAMES.get(type_value),
        "fields": fields,
        "unknown_keys": sorted(unknown_keys),
    }


def normalize_visible_card(card: Any) -> dict[str, Any] | None:
    """Reduce one public zone-card object (or an empty-slot ``None``) to a
    bounded scalar allowlist. Nested lists (energy/tools/evolution line) are
    reduced to counts. Unknown field values are never persisted.
    """
    if card is None:
        return None
    if not isinstance(card, Mapping):
        raise MalformedObservationError("zone element must be a mapping or null")

    fields: dict[str, Any] = {}
    unknown_keys: list[str] = []
    for key in card:
        if key in CARD_SCALAR_FIELDS:
            value = card.get(key)
            if _is_primitive_scalar(value):
                fields[key] = value
            else:
                unknown_keys.append(key)
        elif key in CARD_LIST_COUNT_FIELDS:
            value = card.get(key)
            if isinstance(value, list):
                fields[f"{key}_count"] = len(value)
            else:
                unknown_keys.append(key)
        else:
            unknown_keys.append(key)

    return {"fields": fields, "unknown_keys": sorted(unknown_keys)}


def _zone_count(zone: Any) -> int | None:
    return len(zone) if isinstance(zone, list) else None


def _normalize_zone(zone: Any) -> list[dict[str, Any] | None]:
    if not isinstance(zone, list):
        return []
    return [normalize_visible_card(card) for card in zone]


def _hand_card_ids(hand: Any) -> list[int]:
    if not isinstance(hand, list):
        return []
    ids: list[int] = []
    for card in hand:
        if isinstance(card, Mapping):
            card_id = card.get("id")
            if isinstance(card_id, int) and not isinstance(card_id, bool):
                ids.append(card_id)
    return ids


def normalize_player_view(player: Any, *, is_self: bool) -> dict[str, Any]:
    """Reduce one ``current.players[i]`` entry to the privacy-safe public view.

    Opponent ``hand`` contents are never read into the result, regardless of
    what the raw observation contains, matching the hidden-information
    boundary: only ``hand_count`` is ever stored for the opponent. Prize
    contents are never stored for either seat; only the count is stored.
    """
    if not isinstance(player, Mapping):
        raise MalformedObservationError("player view must be a mapping")

    view: dict[str, Any] = {
        "hand_count": player.get("handCount"),
        "deck_count": player.get("deckCount"),
        "prize_count": _zone_count(player.get("prize")),
        "active": _normalize_zone(player.get("active")),
        "bench": _normalize_zone(player.get("bench")),
        "discard": _normalize_zone(player.get("discard")),
        "bench_max": player.get("benchMax"),
        "status": {flag: player.get(flag) for flag in STATUS_FLAG_FIELDS},
    }
    return view


def _public_candidate_attestations(
    *,
    observation: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Project legal candidates to public ActionKey identities only.

    The DecisionState boundary verifies official option schemas and resolves
    public ToolCard hosts before any identity is emitted.  Incomplete legacy
    trace observations fail closed to a neutral empty attestation list.
    """
    from mage_ptcg.decision_state import DecisionStateError, build_decision_state

    try:
        state = build_decision_state(observation)
    except DecisionStateError:
        return []
    result: list[dict[str, str]] = []
    for legal_action in state.legal_actions:
        key = legal_action.action_key
        payload = key.to_public_trace_payload()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result.append({
            "candidate_public_id": hashlib.sha256(
                b"mage_ptcg:public-candidate:v1\0" + encoded.encode("utf-8")
            ).hexdigest(),
            "semantic_operation": key.semantic_operation,
        })
    return sorted(result, key=lambda item: item["candidate_public_id"])


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _normalize_stadium(value: Any) -> Any:
    if value is None:
        return None
    if _is_primitive_scalar(value):
        return value
    if isinstance(value, Mapping):
        card_id = value.get("id")
        if isinstance(card_id, int) and not isinstance(card_id, bool):
            return {"id": card_id}
    return None


def normalize_board(current: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce shared-board fields. Non-boolean/non-scalar values are dropped
    defensively (their real shape is unresolved; see the schema doc)."""
    return {
        "stadium": _normalize_stadium(current.get("stadium")),
        "stadium_played": _optional_bool(current.get("stadiumPlayed")),
        "supporter_played": _optional_bool(current.get("supporterPlayed")),
        "energy_attached": _optional_bool(current.get("energyAttached")),
        "retreated": _optional_bool(current.get("retreated")),
    }


def _build_deck_registration_record(
    action_list: list[Any],
    *,
    seat: int,
    episode_index: int,
    decision_index: int,
    seat_decision_index: int,
    engine_seed_supported: bool,
) -> dict[str, Any]:
    if not all(isinstance(card_id, int) and not isinstance(card_id, bool) for card_id in action_list):
        raise MalformedObservationError("deck registration action must be a list of integer card ids")

    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "record_type": "deck_registration",
        "episode_index": episode_index,
        "decision_index": decision_index,
        "seat_decision_index": seat_decision_index,
        "engine_seed_supported": engine_seed_supported,
        "seat": seat,
        "deck_size": len(action_list),
        "deck_card_ids": list(action_list),
        "deck_sha256": canonical_deck_sha256(action_list),
    }


def _build_decision_record(
    obs: Mapping[str, Any],
    select: Any,
    action_list: list[Any],
    *,
    episode_index: int,
    decision_index: int,
    seat_decision_index: int,
    engine_seed_supported: bool,
) -> dict[str, Any]:
    if not isinstance(select, Mapping):
        raise MalformedObservationError("select must be a mapping or null")

    options_raw = select.get("option")
    if not isinstance(options_raw, list):
        raise MalformedObservationError("select.option must be a list")

    min_count = select.get("minCount")
    max_count = select.get("maxCount")
    if (
        isinstance(min_count, bool)
        or isinstance(max_count, bool)
        or not isinstance(min_count, int)
        or not isinstance(max_count, int)
    ):
        raise MalformedObservationError("select minCount/maxCount must be integers")

    current = obs.get("current")
    if not isinstance(current, Mapping):
        raise MalformedObservationError("decision observation missing current")

    acting_seat = current.get("yourIndex")
    if isinstance(acting_seat, bool) or acting_seat not in (0, 1):
        raise MalformedObservationError("current.yourIndex must be 0 or 1")

    players = current.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise MalformedObservationError("current.players must contain exactly two players")

    self_player = players[acting_seat]
    opponent_player = players[1 - acting_seat]

    select_type = select.get("type")

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "record_type": "decision",
        "episode_index": episode_index,
        "decision_index": decision_index,
        "seat_decision_index": seat_decision_index,
        "engine_seed_supported": engine_seed_supported,
        "seat": acting_seat,
        "step": obs.get("step"),
        "turn": current.get("turn"),
        "turn_action_count": current.get("turnActionCount"),
        "first_player": current.get("firstPlayer"),
        "select": {
            "type": select_type,
            "type_name": SELECT_TYPE_NAMES.get(select_type),
            "context": select.get("context"),
            "min_count": min_count,
            "max_count": max_count,
            "option_count": len(options_raw),
        },
        "options": [normalize_option(option, index) for index, option in enumerate(options_raw)],
        "public_candidate_attestations": _public_candidate_attestations(
            observation=obs
        ),
        "action": list(action_list),
        "self": normalize_player_view(self_player, is_self=True),
        "opponent": normalize_player_view(opponent_player, is_self=False),
        "board": normalize_board(current),
    }
    if "result" in current:
        record["observed_result"] = current["result"]
    return record


def normalize_decision_record(
    observation: Any,
    action: Sequence[Any],
    *,
    seat: int,
    episode_index: int,
    decision_index: int,
    seat_decision_index: int,
    engine_seed_supported: bool = False,
) -> dict[str, Any]:
    """Build one privacy-safe trace record from a real cabt agent invocation.

    Never mutates ``observation`` or ``action``, and never retains a
    reference to any mutable node from either input.
    """
    if not isinstance(observation, Mapping):
        raise MalformedObservationError("observation must be a mapping")
    obs = observation
    action_list = list(action)
    select = obs.get("select")

    if select is None:
        record = _build_deck_registration_record(
            action_list,
            seat=seat,
            episode_index=episode_index,
            decision_index=decision_index,
            seat_decision_index=seat_decision_index,
            engine_seed_supported=engine_seed_supported,
        )
    else:
        record = _build_decision_record(
            obs,
            select,
            action_list,
            episode_index=episode_index,
            decision_index=decision_index,
            seat_decision_index=seat_decision_index,
            engine_seed_supported=engine_seed_supported,
        )

    forbidden = find_forbidden_keys(record)
    if forbidden:
        raise PrivacyInvariantError(
            f"normalized record unexpectedly contains forbidden keys: {forbidden}"
        )
    return record


class TraceWriter:
    """Append-only JSONL writer that flushes after every record and refuses
    to write a record containing a forbidden key (defense in depth)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle = self._path.open("a", encoding="utf-8")

    def write(self, record: Mapping[str, Any]) -> None:
        forbidden = find_forbidden_keys(record)
        if forbidden:
            raise PrivacyInvariantError(
                f"refusing to write a record containing forbidden keys: {forbidden}"
            )
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self._handle.write(line + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class ActorVisibleAttestationWriter:
    """Separate writer for redacted offline teacher-binding outcomes.

    It accepts only the bounded redacted payload defined by the binder.  It is
    deliberately not interchangeable with :class:`TraceWriter`.
    """

    _REQUIRED = frozenset({
        "teacher_id", "canonical_rule_id", "candidate_public_id",
        "condition_evaluated", "condition_result", "binding_status",
        "binding_reason", "binder_version", "provenance_category",
    })

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._handle = self._path.open("a", encoding="utf-8")

    def write(self, record: Mapping[str, Any]) -> None:
        if set(record) != self._REQUIRED:
            raise PrivacyInvariantError("invalid actor-visible redacted attestation payload")
        if find_forbidden_keys(record):
            raise PrivacyInvariantError("attestation contains forbidden observation field")
        self._handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "ActorVisibleAttestationWriter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def make_traced_agent(
    agent: Callable[[Any], Sequence[int]],
    *,
    seat: int,
    episode_index: int,
    writer: TraceWriter,
    decision_counter: Iterator[int] | None = None,
    engine_seed_supported: bool = False,
    actor_visible_attestation_writer: ActorVisibleAttestationWriter | None = None,
    actor_visible_card_classifier: Callable[[int], str | None] | None = None,
) -> Callable[[Any], Sequence[int]]:
    """Wrap an official cabt agent to append one privacy-safe JSONL record per
    call, without altering the agent's observed behavior.

    ``decision_counter`` may be shared across both seats of one episode by
    the caller so that ``decision_index`` reflects the episode-wide
    interleaved invocation order; each wrapper keeps its own
    ``seat_decision_index`` regardless.
    """
    shared_counter = decision_counter if decision_counter is not None else itertools.count()
    seat_counter = itertools.count()

    def traced_agent(observation: Any) -> Sequence[int]:
        action = agent(observation)
        decision_index = next(shared_counter)
        seat_decision_index = next(seat_counter)
        if (
            actor_visible_attestation_writer is not None
            and actor_visible_card_classifier is not None
            and isinstance(observation, Mapping)
        ):
            from mage_ptcg.distillation.actor_visible_attestation import bind_tr000010

            for attestation in bind_tr000010(
                observation, card_classifier=actor_visible_card_classifier
            ):
                actor_visible_attestation_writer.write(attestation.to_private_artifact())
        record = normalize_decision_record(
            observation,
            action,
            seat=seat,
            episode_index=episode_index,
            decision_index=decision_index,
            seat_decision_index=seat_decision_index,
            engine_seed_supported=engine_seed_supported,
        )
        writer.write(record)
        return action

    return traced_agent
