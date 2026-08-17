"""Strict allow-list public trajectory projection (O6-AUD-002 final remediation).

Builds ``PUBLIC_TRAJECTORY_PROJECTION_V1`` events by constructing a brand-new
dict from only recognized, individually-vetted-safe keys -- never by copying
the raw observation and deleting/denying keys afterward. Any raw key this
module does not explicitly recognize at any nesting depth raises
:class:`PublicSchemaUnknownFieldError` and aborts the whole game's evidence
generation (fail-closed): a partially-redacted trajectory is worse than none.

The recognized raw key inventory (player/card/option field names) is exactly
the structural findings audited in ``docs/evidence/cabt-observation-schema.md``
(8 episodes, 474 observations, 3986 options), corrected in several places
against *directly observed* real cabt games during this remediation (see
below) where the document's own guesses did not match reality. Fields whose
zone semantics remain genuinely unverified (``option.area``, ``option.index``,
``option.inPlayArea``, ``option.inPlayIndex``, ``option.energyIndex``) are
recognized (so they don't trigger a false "unknown field" failure by mere
presence) but deliberately never forwarded into the public payload.

Corrections made against directly-observed real games (not the document's
guesses):

* ``current.stadium`` is a card-slot zone (list, 0-or-1 elements observed),
  not a nullable single ``{"id": int}`` object -- the document explicitly
  flagged this shape as never observed/unverified; it is now confirmed and
  projected the same way as ``active``/``bench``/``discard``.
* ``current.looking`` (search/reveal results, e.g. "search your deck for a
  card") is common in real games (most games trigger it) and carries real
  card identities from a private zone -- contradicting the document's "never
  observed non-null" note. Its content is always dropped, silently, the same
  as ``logs``/``search_begin_input``; unlike a genuinely unrecognized field,
  its mere presence does not abort the whole game (rejecting the majority of
  real games over an already-understood, already-excluded field would be a
  fail-closed policy applied for no safety benefit).
* ``select``'s array of candidate decisions is keyed ``option`` (singular)
  and already *is* the list -- not ``options`` (plural) needing a
  single-item wrapper, which was an unverified guess corrected once real
  ``select`` payloads were inspected.
* Each option in that list is a flat dict (``{"type": <int>, "area": ...,
  "index": ..., ...}``), not nested under a ``"fields"`` key -- that nesting
  belonged to ``cabt-observation-schema.md``'s own *output* schema design for
  a different, already-existing trace format, not the raw engine shape.

Raw ``environment.steps`` select/action pairing (observed directly, not
documented anywhere prior to this remediation): a raw step's own
``observation.select`` is the decision *prompt* for a seat, but that seat's
*response* is not recorded at the same step index -- it appears one raw
index later, at ``canonical_steps[index + 1][seat]['action']`` (confirmed by
wrapping the real agent callables and comparing call-time observation/action
pairs against ``environment.steps`` for an actual cabt game). Each decision
is attached to the event at the *response* index, not the *prompt* index
(see :func:`_decision_answered_at`), so the very first kept event never
carries an action (nothing precedes it) and the terminal event naturally
carries whichever action ended the game -- this keeps
``initial_observation_digest`` independent of downstream action content,
matching the action-trace digest's own separate role. This also means the
first raw step(s), where both seats are still mid deck-registration
(``observation.current is None`` for both), never have a real board to
project -- they are dropped rather than guessed at (see ``_has_board``).

This module is imported only by the runtime writer
(:mod:`mage_ptcg.opponents.public_trajectory_evidence`). The independent
verifier must not import it; see that module's docstring.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .errors import OpponentError

PUBLIC_TRAJECTORY_SCHEMA_VERSION = "o6-public-trajectory-v1"
EVENT_INITIAL = "INITIAL_PUBLIC_STATE"
EVENT_ACTION = "PUBLIC_ACTION"
EVENT_TERMINAL = "TERMINAL_PUBLIC_STATE"

_OPTION_TYPE_NAMES = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}

_PLAYER_KEYS = {"active", "asleep", "bench", "benchMax", "burned", "confused", "deckCount", "discard", "hand", "handCount", "paralyzed", "poisoned", "prize"}
_CARD_KEYS = {"id", "serial", "playerIndex", "hp", "maxHp", "appearThisTurn", "energies", "energyCards", "tools", "preEvolution"}
_CURRENT_KEYS = {"yourIndex", "turn", "turnActionCount", "firstPlayer", "result", "players", "energyAttached", "retreated", "stadium", "stadiumPlayed", "supporterPlayed", "looking"}
_OBSERVATION_KEYS = {"current", "logs", "search_begin_input", "remainingOverageTime", "select", "step"}
_SELECT_KEYS = {"context", "contextCard", "deck", "effect", "maxCount", "minCount", "option", "remainDamageCounter", "remainEnergyCost", "type"}
_OPTION_FIELD_KEYS = {"index", "area", "inPlayArea", "inPlayIndex", "playerIndex", "energyIndex", "toolIndex", "count", "number", "attackId"}
_OPTION_KEYS = {"type"} | _OPTION_FIELD_KEYS
_OPTION_FIELD_FORWARDED = {"playerIndex": "player_index", "attackId": "attack_id", "count": "count", "number": "number"}


class PublicSchemaUnknownFieldError(OpponentError):
    """A raw field this module does not recognize was present; evidence generation fails closed."""


def _reject(path: str, key: str) -> None:
    raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: unrecognized key {key!r} at {path}")


def _require_dict(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: expected object at {path}, got {type(value).__name__}")
    return value


def _project_card(card: Any, *, path: str) -> dict[str, Any] | None:
    if card is None:
        return None
    card = _require_dict(card, path=path)
    for key in card:
        if key not in _CARD_KEYS:
            _reject(path, key)
    energy_cards = card.get("energyCards")
    energies = card.get("energies")
    attached_energy_count = len(energy_cards) if isinstance(energy_cards, list) else (len(energies) if isinstance(energies, list) else 0)
    tools = card.get("tools")
    pre_evolution = card.get("preEvolution")
    return {
        "card_id": card.get("id"),
        "serial": card.get("serial"),
        "player_index": card.get("playerIndex"),
        "current_hp": card.get("hp"),
        "max_hp": card.get("maxHp"),
        "appear_this_turn": card.get("appearThisTurn"),
        "attached_energy_count": attached_energy_count,
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "evolution_depth": len(pre_evolution) if isinstance(pre_evolution, list) else 0,
    }


def _project_card_list(cards: Any, *, path: str) -> list[dict[str, Any] | None]:
    if not isinstance(cards, list):
        _reject(path, "<non-list card container>")
    return [_project_card(card, path=f"{path}[{index}]") for index, card in enumerate(cards)]


def _project_status(player: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "poisoned": bool(player.get("poisoned")), "burned": bool(player.get("burned")), "asleep": bool(player.get("asleep")),
        "paralyzed": bool(player.get("paralyzed")), "confused": bool(player.get("confused")),
    }


def _project_player(player: Any, *, path: str) -> dict[str, Any]:
    player = _require_dict(player, path=path)
    for key in player:
        if key not in _PLAYER_KEYS:
            _reject(path, key)
    hand = player.get("hand")
    prize = player.get("prize")
    return {
        "hand_count": player.get("handCount", len(hand) if isinstance(hand, list) else 0),
        "deck_count": player.get("deckCount"),
        "prize_count": len(prize) if isinstance(prize, list) else player.get("prizeCount"),
        "bench_max": player.get("benchMax"),
        "active": _project_card_list(player.get("active"), path=f"{path}.active"),
        "bench": _project_card_list(player.get("bench"), path=f"{path}.bench"),
        "discard": _project_card_list(player.get("discard"), path=f"{path}.discard"),
        "status": _project_status(player),
    }


def _project_stadium(stadium: Any, *, path: str) -> list[dict[str, Any] | None]:
    """``current.stadium`` is a card-slot zone (0 or 1 elements observed in practice), not a
    nullable single object -- ``cabt-observation-schema.md``'s ``{"id": int}`` guess was never
    actually observed and is superseded by direct engine observation during this remediation.
    ``None`` is still tolerated (treated as an empty zone) for robustness, but any other
    non-list shape has no verified basis and fails closed.
    """
    if stadium is None:
        return []
    return _project_card_list(stadium, path=path)


def _project_board(current: Mapping[str, Any], *, path: str) -> dict[str, Any]:
    return {
        "stadium": _project_stadium(current.get("stadium"), path=f"{path}.stadium"),
        "stadium_played": bool(current.get("stadiumPlayed")),
        "supporter_played": bool(current.get("supporterPlayed")),
        "energy_attached": bool(current.get("energyAttached")),
        "retreated": bool(current.get("retreated")),
    }


def _project_action(select: Any, action: Any, *, path: str) -> dict[str, Any] | None:
    """Project the selected option from a raw ``select`` object.

    Raw shape (confirmed by direct engine observation, not the same as
    ``cabt-observation-schema.md``'s own *output*-schema "fields" nesting
    example): ``select["option"]`` is already the flat list of candidate
    option dicts (key ``option``, singular), and each option dict is itself
    flat -- ``{"type": <int>, "area": ..., "index": ..., ...}`` -- with no
    nested "fields" wrapper.
    """
    if not action:
        return None
    select = _require_dict(select, path=path)
    for key in select:
        if key not in _SELECT_KEYS:
            _reject(path, key)
    options = select.get("option")
    if not isinstance(options, list) or not options:
        raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: action recorded but no options at {path}")
    index = action[0] if isinstance(action, list) and action else action
    if not isinstance(index, int) or not (0 <= index < len(options)):
        raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: action index out of range at {path}")
    option = _require_dict(options[index], path=f"{path}.option[{index}]")
    for key in option:
        if key not in _OPTION_KEYS:
            _reject(f"{path}.option[{index}]", key)
    projected: dict[str, Any] = {"option_type": option.get("type"), "option_type_name": _OPTION_TYPE_NAMES.get(option.get("type"))}
    for raw_key, out_key in _OPTION_FIELD_FORWARDED.items():
        projected[out_key] = option.get(raw_key)
    return projected


def _has_board(step: Sequence[Mapping[str, Any]]) -> bool:
    return any(isinstance(seat.get("observation"), Mapping) and seat["observation"].get("current") is not None for seat in step)


def _decision_answered_at(canonical_steps: Sequence[Sequence[Mapping[str, Any]]], index: int) -> tuple[int, Any, Any] | None:
    """Find the (seat, select, action) that *produced* the board state at raw ``index``,
    empirically confirmed against the real cabt engine: ``canonical_steps[i][seat]['observation']['select']``
    is a decision prompt, but that seat's *response* is not recorded at the same index -- it
    appears one raw index later, at ``canonical_steps[i + 1][seat]['action']``. Attaching the
    decision to the *response* index (rather than the prompt index) means the very first kept
    event (no raw step precedes it) never carries an action -- ``initial_observation_digest``
    stays independent of whichever action is taken from the initial state, matching the
    action-trace digest's own independent role -- and the terminal event naturally carries
    whichever action ended the game. Deck registration (``select is None``) is excluded by
    construction: it never matches the ``select is not None`` condition below, so its action
    (the submitted 60-card list) is never projected, matching the "no deck contents" privacy
    boundary.
    """
    if index == 0:
        return None
    prev_step, step = canonical_steps[index - 1], canonical_steps[index]
    for seat_index, seat in enumerate(prev_step):
        observation = seat.get("observation")
        select = observation.get("select") if isinstance(observation, Mapping) else None
        if select is None:
            continue
        if seat_index >= len(step):
            continue
        response_action = step[seat_index].get("action")
        if response_action:
            return (seat_index, select, response_action)
    return None


def _project_step(step: Sequence[Mapping[str, Any]], *, decision: tuple[int, Any, Any] | None) -> dict[str, Any]:
    acting_seat = decision[0] if decision is not None else None
    source_seat = acting_seat
    if source_seat is None:
        source_seat = next(
            (i for i, seat in enumerate(step) if isinstance(seat.get("observation"), Mapping) and seat["observation"].get("current")),
            0,
        )
    observation = _require_dict(step[source_seat].get("observation"), path=f"$.step[{source_seat}].observation")
    for key in observation:
        if key not in _OBSERVATION_KEYS:
            _reject(f"$.step[{source_seat}].observation", key)
    current = _require_dict(observation.get("current"), path=f"$.step[{source_seat}].observation.current")
    for key in current:
        if key not in _CURRENT_KEYS:
            _reject(f"$.step[{source_seat}].observation.current", key)
    # current.looking (search/reveal results, e.g. "search your deck for a card") carries real
    # card identities from a private zone -- confirmed by direct observation, contradicting
    # cabt-observation-schema.md's "never observed non-null" note. It is common (most real games
    # trigger it at least once), so rejecting the whole game would make evidence generation
    # fail on the majority of games; instead its content is always dropped, silently, like
    # logs/search_begin_input -- never forwarded, never causes a fail-closed abort on its own.
    players_raw = current.get("players")
    if not isinstance(players_raw, list) or len(players_raw) != 2:
        raise PublicSchemaUnknownFieldError("PUBLIC_SCHEMA_UNKNOWN_FIELD: current.players must have exactly 2 entries")
    players = [_project_player(p, path=f"$.step[{source_seat}].observation.current.players[{i}]") for i, p in enumerate(players_raw)]
    board = _project_board(current, path=f"$.step[{source_seat}].observation.current")
    action = None
    if decision is not None:
        _, select, response_action = decision
        action = _project_action(select, response_action, path=f"$.step[{acting_seat}].observation.select")
    return {"players": players, "board": board, "result": current.get("result"), "action": action}


def build_public_trajectory_events(canonical_steps: Sequence[Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    if not canonical_steps:
        raise OpponentError("cannot build public trajectory events from empty steps")
    # Deck-registration-only raw steps (both seats' observation.current still None, before any
    # board exists) are dropped entirely: there is no board to project, and their action (a
    # 60-card deck list) must never be persisted (see module docstring).
    kept_indices = [i for i, step in enumerate(canonical_steps) if _has_board(step)]
    if not kept_indices:
        raise OpponentError("cannot build public trajectory events: no step has a board (current) observation")
    events: list[dict[str, Any]] = []
    last = len(kept_indices) - 1
    for output_index, raw_index in enumerate(kept_indices):
        event_type = EVENT_INITIAL if output_index == 0 else EVENT_TERMINAL if output_index == last else EVENT_ACTION
        decision = _decision_answered_at(canonical_steps, raw_index)
        public_payload = _project_step(canonical_steps[raw_index], decision=decision)
        acting_seat = decision[0] if decision is not None else None
        events.append({
            "schema_version": PUBLIC_TRAJECTORY_SCHEMA_VERSION,
            "event_type": event_type,
            "step_index": output_index,
            "seat_direction": (f"SEAT_{acting_seat}" if acting_seat is not None else None),
            "public_payload": public_payload,
        })
    return events
