"""Self-owned cg.api policy for the current Rule-v0 root deck.

This module is intentionally independent of the local_eval_only native
opponents.  It uses only the public ``cg.api`` observation and a small,
deck-bound strategy for the Fighting/Mega Lucario list.  The module is copied
as ``main.py`` into an isolated candidate package by the research builder;
production ``main.py`` is not modified.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from cg.api import (
    AreaType,
    OptionType,
    SelectContext,
    all_attack,
    all_card_data,
    to_observation_class,
)


ROOT_DECK = (673, 673, 674, 674, 675, 675, 676, 676, 676, 677, 677, 677,
             678, 678, 678, 678, 1102, 1102, 1102, 1102, 1123, 1123,
             1141, 1141, 1141, 1141, 1142, 1142, 1142, 1142, 1152, 1152,
             6, 1159, 1182, 1182, 1192, 1192, 1192, 1192, 1227, 1227,
             1227, 1227, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 1182,
             677, 1252)

FIGHTING = 6
MAKUHITA, HARIYAMA = 673, 674
LUNATONE, SOLROCK = 675, 676
RIOLU, MEGA_LUCARIO = 677, 678
DUSK_BALL, SWITCH = 1102, 1123
PREMIUM_POWER, FIGHTING_GONG = 1141, 1142
POKE_PAD, HERO_CAPE = 1152, 1159
BOSS, CARMINE = 1182, 1192
LILLIE, GRAVITY = 1227, 1252

_CARD_DB = {card.cardId: card for card in all_card_data()}
_ATTACK_DB = {attack.attackId: attack for attack in all_attack()}
_POKEMON_IDS = {MAKUHITA, HARIYAMA, LUNATONE, SOLROCK, RIOLU, MEGA_LUCARIO}
_SUPPORTERS = {BOSS, CARMINE, LILLIE}
_SEARCH_CARDS = {DUSK_BALL, PREMIUM_POWER, FIGHTING_GONG, POKE_PAD}


def _deck_path() -> Path:
    try:
        local = Path(__file__).resolve().with_name("deck.csv")
    except NameError:
        local = Path("deck.csv")
    for candidate in (local, Path("deck.csv"), Path("/kaggle_simulations/agent/deck.csv")):
        if candidate.is_file():
            return candidate
    raise RuntimeError("deck.csv not found")


def _read_deck() -> list[int]:
    values = [int(line.strip()) for line in _deck_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != 60:
        raise ValueError("root deck must contain exactly 60 cards")
    return values


def _mine(obs):
    return obs.current.players[obs.current.yourIndex]


def _opponent(obs):
    return obs.current.players[1 - obs.current.yourIndex]


def _pokemon(obs) -> list[object]:
    state = _mine(obs)
    return [card for card in ([*state.active, *state.bench]) if card is not None]


def _hand_ids(obs) -> list[int]:
    return [card.id for card in (_mine(obs).hand or []) if card is not None]


def _discard_ids(obs) -> list[int]:
    return [card.id for card in (_mine(obs).discard or []) if card is not None]


def _energy_count(card: object | None) -> int:
    if card is None:
        return 0
    attached = getattr(card, "energyCards", None)
    return len(attached) if attached is not None else len(getattr(card, "energies", []) or [])


def _damage(card: object | None) -> int:
    if card is None:
        return 0
    return max(0, int(getattr(card, "maxHp", getattr(card, "hp", 0))) - int(getattr(card, "hp", 0)))


def _tool_count(card: object | None) -> int:
    return len(getattr(card, "tools", []) or []) if card is not None else 0


def _count(obs, card_id: int) -> int:
    return sum(1 for card in _pokemon(obs) if getattr(card, "id", None) == card_id)


def _has(obs, card_id: int) -> bool:
    return _count(obs, card_id) > 0


def _card_for_option(obs, option: object) -> object | None:
    index = getattr(option, "index", None)
    if index is None:
        return None
    player_index = getattr(option, "playerIndex", None)
    if player_index is None:
        player_index = obs.current.yourIndex
    area = AreaType.HAND if option.type == OptionType.PLAY else getattr(option, "area", None)
    if area is None:
        return None
    state = obs.current.players[player_index]
    if area == AreaType.HAND:
        cards = state.hand or []
    elif area == AreaType.DISCARD:
        cards = state.discard or []
    elif area == AreaType.ACTIVE:
        cards = state.active or []
    elif area == AreaType.BENCH:
        cards = state.bench or []
    elif area == AreaType.PRIZE:
        cards = state.prize or []
    elif area == AreaType.LOOKING:
        cards = obs.current.looking or []
    elif area == AreaType.STADIUM:
        cards = obs.current.stadium or []
    elif area == AreaType.DECK and getattr(obs.select, "deck", None) is not None:
        cards = obs.select.deck or []
    else:
        return None
    return cards[index] if 0 <= index < len(cards) else None


def _target_for_option(obs, option: object) -> object | None:
    area = getattr(option, "inPlayArea", None)
    index = getattr(option, "inPlayIndex", None)
    if area is None or index is None:
        return None
    state = _mine(obs)
    cards = state.active if area == AreaType.ACTIVE else state.bench if area == AreaType.BENCH else []
    return cards[index] if 0 <= index < len(cards) else None


def _option_card_id(obs, option: object) -> int | None:
    card = _card_for_option(obs, option)
    value = getattr(card, "id", None)
    if isinstance(value, int):
        return value
    value = getattr(option, "cardId", None)
    return value if isinstance(value, int) else None


def _available_attack_damage(option: object) -> int:
    attack = _ATTACK_DB.get(getattr(option, "attackId", None))
    return int(getattr(attack, "damage", 0)) if attack is not None else 0


def _setup_score(obs, option: object) -> int:
    context = obs.select.context
    option_type = option.type
    if option_type == OptionType.NO:
        return 20000 if context in {SelectContext.MULLIGAN, SelectContext.IS_FIRST} else 1
    if option_type == OptionType.YES:
        return 10000 if context == SelectContext.ACTIVATE else 0
    card_id = _option_card_id(obs, option)
    if context == SelectContext.SETUP_ACTIVE_POKEMON:
        return {RIOLU: 40000, SOLROCK: 33000, MAKUHITA: 28000, LUNATONE: 24000}.get(card_id, 1000)
    if context == SelectContext.SETUP_BENCH_POKEMON:
        return {RIOLU: 36000, MAKUHITA: 33000, SOLROCK: 30000, LUNATONE: 28000}.get(card_id, 1000)
    return 1000


def _play_score(obs, option: object) -> int:
    card_id = _option_card_id(obs, option)
    if card_id is None:
        return 1000
    hand = _hand_ids(obs)
    if card_id in _POKEMON_IDS:
        if len(_mine(obs).bench) >= _mine(obs).benchMax:
            return -1000
        priority = {RIOLU: 25000, MAKUHITA: 22000, SOLROCK: 20000, LUNATONE: 19000,
                    MEGA_LUCARIO: -5000, HARIYAMA: -5000}
        return priority.get(card_id, 15000)
    if card_id in _SUPPORTERS:
        if obs.current.supporterPlayed:
            return -10000
        return {CARMINE: 22000, LILLIE: 20000, BOSS: 15000}.get(card_id, 12000)
    if card_id == GRAVITY:
        return -10000 if obs.current.stadiumPlayed else 13000
    if card_id == DUSK_BALL:
        return 23000 if len(_pokemon(obs)) < 3 or not _has(obs, MEGA_LUCARIO) else 14000
    if card_id == PREMIUM_POWER:
        return 21000 if not _has(obs, MEGA_LUCARIO) else 13000
    if card_id == FIGHTING_GONG:
        return 19000 if any(getattr(card, "id", None) in {RIOLU, MEGA_LUCARIO, MAKUHITA, HARIYAMA} for card in _pokemon(obs)) else 9000
    if card_id == POKE_PAD:
        return 16000
    if card_id == SWITCH:
        return 11000 if _mine(obs).active and _damage(_mine(obs).active[0]) > 0 else 5000
    if card_id == HERO_CAPE:
        return 9000 if any(_tool_count(card) == 0 for card in _pokemon(obs)) else -1000
    return 5000 if card_id in hand else 1000


def _evolve_score(obs, option: object) -> int:
    card_id = _option_card_id(obs, option)
    target = _target_for_option(obs, option)
    target_id = getattr(target, "id", None)
    if card_id == MEGA_LUCARIO and target_id == RIOLU:
        return -1000 if getattr(target, "appearThisTurn", False) else 50000
    if card_id == HARIYAMA and target_id == MAKUHITA:
        return -500 if getattr(target, "appearThisTurn", False) else 30000
    return 12000


def _attach_score(obs, option: object) -> int:
    card_id = _option_card_id(obs, option)
    target = _target_for_option(obs, option)
    target_id = getattr(target, "id", None)
    energy = _energy_count(target)
    if card_id == FIGHTING:
        if obs.current.energyAttached:
            return -10000
        base = {MEGA_LUCARIO: 45000, RIOLU: 33000, HARIYAMA: 27000,
                MAKUHITA: 24000, SOLROCK: 22000, LUNATONE: 18000}.get(target_id, 5000)
        if energy >= 2 and target_id == MEGA_LUCARIO:
            base += 10000
        if energy >= 3:
            base -= 30000
        return base
    if card_id == HERO_CAPE:
        return 14000 if target_id == MEGA_LUCARIO and _tool_count(target) == 0 else 4000
    return 1000


def _main_score(obs, option: object) -> int:
    option_type = option.type
    if option_type == OptionType.EVOLVE:
        return _evolve_score(obs, option)
    if option_type == OptionType.PLAY:
        return _play_score(obs, option)
    if option_type == OptionType.ATTACH:
        return _attach_score(obs, option)
    if option_type == OptionType.ATTACK:
        damage = _available_attack_damage(option)
        if getattr(option, "attackId", None) == 983:
            return 50000 + damage
        if getattr(option, "attackId", None) == 982:
            return 36000 + damage
        return 10000 + damage
    if option_type == OptionType.ABILITY:
        return 25000
    if option_type == OptionType.RETREAT:
        return 12000 if any(_energy_count(card) >= 2 for card in _mine(obs).bench) else -500
    if option_type == OptionType.END:
        return -100000
    return 1000


def _search_priority(obs, card_id: int | None, effect_id: int | None) -> int:
    in_play = {getattr(card, "id", None) for card in _pokemon(obs)}
    hand = _hand_ids(obs)
    if card_id == MEGA_LUCARIO and MEGA_LUCARIO not in in_play:
        return 50000
    if card_id == RIOLU and RIOLU not in in_play and RIOLU not in hand:
        return 45000
    if card_id == MAKUHITA and MAKUHITA not in in_play and MAKUHITA not in hand:
        return 38000
    if card_id == SOLROCK and SOLROCK not in in_play:
        return 30000
    if card_id == LUNATONE and LUNATONE not in in_play:
        return 29000
    if card_id == FIGHTING and not obs.current.energyAttached:
        return 25000
    if card_id in {DUSK_BALL, PREMIUM_POWER, POKE_PAD}:
        return 14000
    if card_id in _SUPPORTERS and not obs.current.supporterPlayed:
        return 11000
    return 1000


def _non_main_score(obs, option: object) -> int:
    context = obs.select.context
    card_id = _option_card_id(obs, option)
    effect = getattr(obs.select, "effect", None)
    effect_id = getattr(effect, "id", None)
    if context in {SelectContext.TO_HAND, SelectContext.TO_FIELD, SelectContext.TO_BENCH,
                   SelectContext.CARD, SelectContext.LOOK}:
        return _search_priority(obs, card_id, effect_id)
    if context in {SelectContext.DISCARD, SelectContext.DISCARD_CARD_OR_ATTACHED_CARD}:
        if card_id == FIGHTING and _discard_ids(obs).count(FIGHTING) < 2:
            return 30000
        if card_id in {CARMINE, LILLIE} and _hand_ids(obs).count(card_id) > 1:
            return 15000
        if card_id in {MEGA_LUCARIO, RIOLU, HARIYAMA, MAKUHITA}:
            return -30000
        return 1000
    if context in {SelectContext.ATTACH_TO, SelectContext.ATTACH_FROM}:
        return _attach_score(obs, option)
    if context in {SelectContext.SWITCH, SelectContext.TO_ACTIVE, SelectContext.TO_FIELD}:
        target = _card_for_option(obs, option)
        target_id = getattr(target, "id", None)
        return {MEGA_LUCARIO: 40000, HARIYAMA: 25000, RIOLU: 12000, SOLROCK: 10000}.get(target_id, 1000) + _energy_count(target) * 200
    if context in {SelectContext.HEAL, SelectContext.REMOVE_DAMAGE_COUNTER}:
        return _damage(_card_for_option(obs, option))
    if context in {SelectContext.DAMAGE, SelectContext.DAMAGE_COUNTER, SelectContext.DAMAGE_COUNTER_ANY}:
        target = _card_for_option(obs, option)
        return 20000 - int(getattr(target, "hp", 999)) if target is not None else 1000
    if context == SelectContext.ATTACK:
        return _available_attack_damage(option)
    if context in {SelectContext.NUMBER, SelectContext.DRAW_COUNT}:
        return int(getattr(option, "number", 0) or 0)
    return 1000


def _score(obs, option: object) -> int:
    context = obs.select.context
    if context in {SelectContext.IS_FIRST, SelectContext.MULLIGAN,
                   SelectContext.SETUP_ACTIVE_POKEMON, SelectContext.SETUP_BENCH_POKEMON,
                   SelectContext.ACTIVATE}:
        return _setup_score(obs, option)
    if option.type in {OptionType.YES, OptionType.NO}:
        return _setup_score(obs, option)
    if context == SelectContext.MAIN:
        return _main_score(obs, option)
    return _non_main_score(obs, option)


def _fallback_raw(obs_dict: dict) -> list[int]:
    select = obs_dict.get("select") if isinstance(obs_dict, dict) else None
    if not isinstance(select, dict):
        return list(ROOT_DECK)
    options = select.get("option")
    minimum = select.get("minCount")
    maximum = select.get("maxCount")
    if not isinstance(options, list) or not isinstance(minimum, int) or not isinstance(maximum, int):
        return []
    count = max(0, min(minimum, maximum, len(options)))
    return list(range(count))


def agent(obs_dict: dict) -> list[int]:
    try:
        obs = to_observation_class(obs_dict)
        if obs.select is None:
            return _read_deck()
        options = list(obs.select.option or [])
        if not options:
            return []
        minimum = max(0, min(int(obs.select.minCount), len(options)))
        maximum = max(minimum, min(int(obs.select.maxCount), len(options)))
        scored = [(int(_score(obs, option)), index) for index, option in enumerate(options)]
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        selected: list[int] = []
        for score, index in scored:
            if len(selected) >= maximum:
                break
            if score < 0 and len(selected) >= minimum:
                continue
            selected.append(index)
        if len(selected) < minimum:
            selected = [index for _score_value, index in scored[:minimum]]
        return selected[:maximum]
    except Exception:
        return _fallback_raw(obs_dict)


# RESEARCH_VARIANT: cg-lethal-target-v1
# Public-state only: add a bounded bonus when the selected attack can KO the
# opponent's visible active Pokemon.  Non-attacks and malformed state use the
# immutable cg P0 score exactly.
_CG_POLICY_BASE_MAIN_SCORE = _main_score
_CG_POLICY_BASE_MAIN_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"

def _main_score(obs, option: object) -> int:
    # The engine may briefly expose a Struct without ``type`` while building
    # a selection.  Treat that shape as unsupported instead of allowing the
    # research overlay to turn a legal fallback into an agent fault.
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = _CG_POLICY_BASE_MAIN_SCORE(obs, option)
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACK:
        return score
    try:
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = _available_attack_damage(option)
        if hp > 0 and damage >= hp:
            return score + 12000
    except Exception:
        return score
    return score

_CG_POLICY_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    # Keep the original scorer for well-formed options.  The explicit guard
    # covers the engine's transient untyped Struct before the immutable scorer
    # can dereference ``option.type``.
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_POLICY_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_POLICY_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    # Kaggle's source loader selects the last callable in the file.  Keep the
    # public entrypoint last while delegating to the immutable agent body.
    return _CG_POLICY_BASE_AGENT(obs_dict)
