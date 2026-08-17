"""Render the minimal Student submission runtime_main.py.

This module produces a self-contained runtime that includes ONLY:
- Deck validation (Deck, DeckValidationError, validate_deck, read_deck_csv)
- Selection contract
- make_random_agent, make_deterministic_agent (stdlib-only legal baselines)
- make_rule_agent (v0 only, no knowledge)
- make_student_agent (with Rule v0 fallback)

Excluded from submission runtime:
- make_rule_agent_v1, RuleAgentV1
- make_bounded_search_agent, BoundedSearchConfig, BoundedSearchError,
  EngineAdapter, SearchTelemetry, search_bounded
- KnowledgePack, KnowledgeRuleAdapter, load_pack,
  runtime_compatibility_for_deck, knowledge_pack parameters
"""
from __future__ import annotations


def render_student_runtime() -> str:
    """Return deterministic, minimal Student submission runtime source."""
    return '''\
"""Minimal submission runtime for Student v0 + Rule v0 fallback."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, Collection, Sequence


Deck = list[int]
Agent = Callable[[dict], list[int]]


class DeckValidationError(ValueError):
    """Raised when a deck cannot satisfy the cabt deck contract."""


def _default_deck_path() -> Path:
    candidates = (
        Path("deck.csv"),
        Path(__file__).resolve().with_name("deck.csv"),
        Path("/kaggle_simulations/agent/deck.csv"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise DeckValidationError("deck.csv was not found")


def validate_deck(
    deck: Sequence[int],
    *,
    known_card_ids: Collection[int] | None = None,
) -> Deck:
    """Validate and copy a 60-card cabt deck."""
    if len(deck) != 60:
        raise DeckValidationError(f"deck must contain exactly 60 cards, got {len(deck)}")
    if any(isinstance(card_id, bool) or not isinstance(card_id, int) for card_id in deck):
        raise DeckValidationError("every card ID must be an integer")
    if known_card_ids is not None:
        unknown = sorted(set(deck).difference(known_card_ids))
        if unknown:
            raise DeckValidationError(f"deck contains unknown card IDs: {unknown}")
    return list(deck)


def read_deck_csv(
    path: str | Path | None = None,
    *,
    known_card_ids: Collection[int] | None = None,
) -> Deck:
    """Read one integer card ID per line and enforce the 60-card contract."""
    deck_path = Path(path) if path is not None else _default_deck_path()
    try:
        lines = deck_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DeckValidationError(f"could not read deck {deck_path}: {exc}") from exc

    values: Deck = []
    for line_number, raw in enumerate(lines, start=1):
        value = raw.strip()
        if not value:
            continue
        try:
            values.append(int(value))
        except ValueError as exc:
            raise DeckValidationError(
                f"deck {deck_path} line {line_number} is not an integer: {value!r}"
            ) from exc
    return validate_deck(values, known_card_ids=known_card_ids)


def _selection_contract(obs_dict: dict):
    select = obs_dict.get("select")
    if select is None:
        return None

    options = select.get("option")
    if not isinstance(options, list):
        raise ValueError("select.option must be a list")
    min_count = select.get("minCount")
    max_count = select.get("maxCount")
    if (
        isinstance(min_count, bool)
        or isinstance(max_count, bool)
        or not isinstance(min_count, int)
        or not isinstance(max_count, int)
    ):
        raise ValueError("select minCount/maxCount must be integers")
    if not 0 <= min_count <= max_count <= len(options):
        raise ValueError(
            "invalid selection bounds: "
            f"minCount={min_count}, maxCount={max_count}, options={len(options)}"
        )
    return options, min_count, max_count


def _deck_supplier(
    deck: Sequence[int] | None,
    deck_path: str | Path | None,
) -> Callable[[], Deck]:
    cached = validate_deck(deck) if deck is not None else None

    def supply() -> Deck:
        nonlocal cached
        if cached is None:
            cached = read_deck_csv(deck_path)
        return list(cached)

    return supply


def make_random_agent(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
    seed: int | None = None,
) -> Agent:
    """Create an independently seeded agent that samples legal option indices."""
    rng = random.Random(seed)
    supply_deck = _deck_supplier(deck, deck_path)

    def random_agent(obs_dict: dict) -> list[int]:
        contract = _selection_contract(obs_dict)
        if contract is None:
            return supply_deck()
        options, _min_count, max_count = contract
        return rng.sample(range(len(options)), max_count)

    random_agent.__name__ = "random_legal_agent"
    return random_agent


_MAIN_SELECT_TYPE = 0
_OPTION_TYPE_NAMES = {
    7: "PLAY",
    8: "ATTACH",
    9: "EVOLVE",
    10: "ABILITY",
    13: "ATTACK",
    14: "END",
}
_MAIN_PRIORITY = ("ATTACK", "PLAY", "ATTACH", "EVOLVE", "ABILITY", "END")


def _enum_name(value: object, numeric_names: dict[int, str]) -> str | None:
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    if isinstance(value, str):
        return value.rsplit(".", 1)[-1].upper()
    if isinstance(value, int) and not isinstance(value, bool):
        return numeric_names.get(value)
    return None


def _is_main_selection(select: dict) -> bool:
    value = select.get("type")
    if value == _MAIN_SELECT_TYPE:
        return True
    return _enum_name(value, {_MAIN_SELECT_TYPE: "MAIN"}) == "MAIN"


def _option_type_name(option: object) -> str | None:
    value = option.get("type") if isinstance(option, dict) else getattr(option, "type", None)
    return _enum_name(value, _OPTION_TYPE_NAMES)


def make_deterministic_agent(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
) -> Agent:
    """Create a stable legal baseline with a small MAIN-action priority rule."""
    supply_deck = _deck_supplier(deck, deck_path)

    def deterministic_agent(obs_dict: dict) -> list[int]:
        contract = _selection_contract(obs_dict)
        if contract is None:
            return supply_deck()
        options, _min_count, max_count = contract
        if max_count == 0:
            return []

        ordered = list(range(len(options)))
        select = obs_dict["select"]
        if _is_main_selection(select):
            prioritized: list[int] = []
            for target in _MAIN_PRIORITY:
                prioritized.extend(
                    index
                    for index, option in enumerate(options)
                    if _option_type_name(option) == target and index not in prioritized
                )
            ordered = prioritized + [index for index in ordered if index not in prioritized]
        return ordered[:max_count]

    deterministic_agent.__name__ = "deterministic_legal_agent"
    return deterministic_agent


def make_rule_agent(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
    seed: int | None = None,
) -> Agent:
    """Create the deterministic Rule Agent v0 (no knowledge, no v1)."""
    del seed
    import sys

    src_root = Path(__file__).resolve().parent / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from mage_submission_agents import choose_rule_indices

    supply_deck = _deck_supplier(deck, deck_path)

    def rule_agent(obs_dict: dict) -> list[int]:
        selection = choose_rule_indices(obs_dict)
        if selection is None:
            return supply_deck()
        return selection

    rule_agent.__name__ = "rule_legal_agent"
    return rule_agent


def make_student_agent(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
    model_path: str | Path | None = None,
) -> Agent:
    """Create the Student v0 agent with deterministic Rule v0 fallback."""
    import sys

    src_root = Path(__file__).resolve().parent / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    supply_deck = _deck_supplier(deck, deck_path)
    fallback = make_rule_agent(deck=deck, deck_path=deck_path)
    policy = None
    try:
        from mage_ptcg.student import RuntimeStudentPolicy

        policy = RuntimeStudentPolicy.load(model_path)
    except (ImportError, OSError, TypeError, ValueError):
        policy = None

    def student_agent(obs_dict: dict) -> list[int]:
        if _selection_contract(obs_dict) is None:
            return supply_deck()
        if policy is not None:
            selection = policy.choose(obs_dict)
            if selection is not None:
                return selection
        return fallback(obs_dict)

    student_agent.__name__ = "student_v0_with_rule_v0_fallback"
    student_agent.student_policy = policy
    return student_agent
'''
