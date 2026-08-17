"""Minimal legal agents for the Pokémon TCG AI Battle submission surface."""

from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Callable, Collection, Sequence


Deck = list[int]
Agent = Callable[[dict], list[int]]


class DeckValidationError(ValueError):
    """Raised when a deck cannot satisfy the cabt deck contract."""


def _source_root() -> Path:
    """Resolve this source location under imports and Kaggle raw ``exec``."""
    def has_runtime_bundle(candidate: Path) -> bool:
        return any(
            (candidate / package_name / "rule_agent.py").is_file()
            for package_name in ("agents", "mage_submission_agents")
        )

    if "__file__" in globals():
        source_name = __file__
    else:
        source_name = getattr(sys._getframe().f_code, "co_filename", "")
    if source_name and not str(source_name).startswith("<"):
        candidate = Path(source_name).resolve().parent
        if has_runtime_bundle(candidate):
            return candidate
    kaggle_candidate = Path("/kaggle_simulations/agent")
    if has_runtime_bundle(kaggle_candidate):
        return kaggle_candidate
    raise RuntimeError("main.py source root could not be resolved")


def _prepare_source_imports() -> Path:
    root = _source_root()
    for entry in (root, root / "src"):
        value = str(entry)
        if value not in sys.path:
            sys.path.insert(0, value)
    return root


def _default_deck_path() -> Path:
    candidates = (
        Path("deck.csv"),
        _source_root() / "deck.csv",
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


def _selection_contract(obs_dict: dict) -> tuple[list, int, int] | None:
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
    knowledge_pack: object | None = None,
) -> Agent:
    """Create the deterministic Rule Agent v0.

    ``seed`` is accepted for registry parity.  The v0 policy is stateless, so
    identical observations have the same result for every seed.
    """
    del seed
    _prepare_source_imports()
    from agents import choose_rule_indices, rank_rule_indices

    supply_deck = _deck_supplier(deck, deck_path)
    adapter: object | None = None
    adapter_initialized = False

    def knowledge_adapter() -> object | None:
        nonlocal adapter, adapter_initialized
        if adapter_initialized:
            return adapter
        adapter_initialized = True
        if knowledge_pack is None:
            return None
        try:
            from mage_ptcg.knowledge import (
                KnowledgePack,
                KnowledgeRuleAdapter,
                load_pack,
                runtime_compatibility_for_deck,
            )

            pack = (
                knowledge_pack
                if isinstance(knowledge_pack, KnowledgePack)
                else load_pack(knowledge_pack)
            )
            target = runtime_compatibility_for_deck(supply_deck())
            adapter = KnowledgeRuleAdapter.create(pack, target)
        except (OSError, TypeError, ValueError):
            adapter = None
        return adapter

    def rule_agent(obs_dict: dict) -> list[int]:
        selection = choose_rule_indices(obs_dict)
        if selection is None:
            return supply_deck()
        active_adapter = knowledge_adapter()
        if active_adapter is None:
            return selection
        return active_adapter.reorder_ties(obs_dict, selection, rank_rule_indices(obs_dict))

    rule_agent.__name__ = "rule_legal_agent"
    return rule_agent


def make_rule_agent_v1(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
    seed: int | None = None,
    knowledge_pack: object | None = None,
) -> Agent:
    """Create the C1 public-belief Challenger without changing Champion v0."""
    del seed
    _prepare_source_imports()
    from agents.rule_agent_v1 import RuleAgentV1
    from mage_ptcg.knowledge import (
        KnowledgePack,
        KnowledgeRuleAdapter,
        load_pack,
        runtime_compatibility_for_deck,
    )

    supply_deck = _deck_supplier(deck, deck_path)
    try:
        pack = (
            knowledge_pack
            if isinstance(knowledge_pack, KnowledgePack)
            else load_pack(knowledge_pack)
            if knowledge_pack is not None
            else None
        )
        target = (
            None
            if pack is None
            else runtime_compatibility_for_deck(supply_deck())
        )
        adapter = KnowledgeRuleAdapter.create(pack, target)
    except (OSError, TypeError, ValueError):
        adapter = KnowledgeRuleAdapter.create(None, None)
    decision_loop = RuleAgentV1(knowledge_adapter=adapter)

    def rule_agent_v1(obs_dict: dict) -> list[int]:
        selection = decision_loop.choose(obs_dict)
        return supply_deck() if selection is None else selection

    rule_agent_v1.__name__ = "rule_v1_challenger_agent"
    rule_agent_v1.decision_loop = decision_loop  # type: ignore[attr-defined]
    return rule_agent_v1


def make_bounded_search_agent(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
    seed: int | None = None,
    knowledge_pack: object | None = None,
    engine_adapter: object | None = None,
    search_config: object | None = None,
    guided: bool = True,
) -> Agent:
    """Create the optional C3 agent without changing the submission Champion.

    Public ``Environment.clone``/``step`` can advance an environment owned by
    an external evaluator, but the submission ``agent(obs)`` contract has no
    documented way to reconstruct that environment from one decision.  Thus
    ``engine_adapter=None`` is the safe default and deterministically returns
    Rule Agent v0. Knowledge is imported only when a pack is supplied.
    """
    del seed
    _prepare_source_imports()
    from agents import choose_rule_indices
    from mage_ptcg.solver import (
        BoundedSearchConfig,
        BoundedSearchError,
        EngineAdapter,
        SearchTelemetry,
        search_bounded,
    )

    if engine_adapter is not None and not isinstance(engine_adapter, EngineAdapter):
        raise TypeError("engine_adapter must implement EngineAdapter")
    if search_config is not None and not isinstance(search_config, BoundedSearchConfig):
        raise TypeError("search_config must be BoundedSearchConfig")

    supply_deck = _deck_supplier(deck, deck_path)
    telemetry = SearchTelemetry()
    adapter: object | None = None
    adapter_initialized = False

    def knowledge_adapter() -> object | None:
        nonlocal adapter, adapter_initialized
        if adapter_initialized:
            return adapter
        adapter_initialized = True
        if knowledge_pack is None:
            return None
        try:
            from mage_ptcg.knowledge import (
                KnowledgePack,
                KnowledgeRuleAdapter,
                load_pack,
                runtime_compatibility_for_deck,
            )

            pack = (
                knowledge_pack
                if isinstance(knowledge_pack, KnowledgePack)
                else load_pack(knowledge_pack)
            )
            target = runtime_compatibility_for_deck(supply_deck())
            candidate = KnowledgeRuleAdapter.create(pack, target)
            adapter = candidate if candidate.enabled else None
        except (OSError, TypeError, ValueError):
            adapter = None
        return adapter

    # An explicitly requested pack is validated at factory construction so
    # one-time I/O/import cost is not charged to the first search decision.
    # The omitted-pack path still performs no Knowledge import.
    if knowledge_pack is not None:
        knowledge_adapter()

    def bounded_search_agent(obs_dict: dict) -> list[int]:
        fallback = choose_rule_indices(obs_dict)
        if fallback is None:
            return supply_deck()
        active_knowledge = knowledge_adapter()
        prior_score = (
            active_knowledge.prior_score if active_knowledge is not None else None
        )
        try:
            result = search_bounded(
                obs_dict,
                fallback_selection=fallback,
                adapter=engine_adapter,
                config=search_config,
                guided=guided,
                knowledge_prior=prior_score,
            )
        except BoundedSearchError:
            return fallback
        telemetry.record(result)
        bounded_search_agent.last_search_result = result  # type: ignore[attr-defined]
        return list(result.selection)

    bounded_search_agent.__name__ = (
        "bounded_search_guided_agent" if guided else "bounded_search_unguided_agent"
    )
    bounded_search_agent.search_telemetry = telemetry  # type: ignore[attr-defined]
    bounded_search_agent.last_search_result = None  # type: ignore[attr-defined]
    return bounded_search_agent


def make_student_agent(
    *,
    deck: Sequence[int] | None = None,
    deck_path: str | Path | None = None,
    model_path: str | Path | None = None,
) -> Agent:
    """Create the optional C4 Student v0 with deterministic Rule v0 fallback.

    The public submission default remains Rule v0.  A missing, incompatible,
    or malformed model is intentionally indistinguishable from no model: it
    never prevents deck registration and always delegates decisions to Rule v0.
    """
    _prepare_source_imports()
    supply_deck = _deck_supplier(deck, deck_path)
    fallback = make_rule_agent(deck=deck, deck_path=deck_path)
    policy: object | None = None
    try:
        from mage_ptcg.student import RuntimeStudentPolicy

        policy = RuntimeStudentPolicy.load(model_path)
    except (ImportError, OSError, TypeError, ValueError):
        policy = None

    def student_agent(obs_dict: dict) -> list[int]:
        if _selection_contract(obs_dict) is None:
            return supply_deck()
        if policy is not None:
            selection = policy.choose(obs_dict)  # type: ignore[attr-defined]
            if selection is not None:
                return selection
        return fallback(obs_dict)

    student_agent.__name__ = "student_v0_with_rule_v0_fallback"
    student_agent.student_policy = policy  # type: ignore[attr-defined]
    return student_agent


# The public submission surface must remain pinned to the approved Champion.
# Random and deterministic factories are retained only for local evaluation.
_DEFAULT_AGENT = make_rule_agent()


def agent(obs_dict: dict) -> list[int]:
    """Kaggle submission entry point returning cabt option indices."""
    return _DEFAULT_AGENT(obs_dict)


if __name__ == "__main__":
    print("Pokemon TCG AI Battle minimal legal-agent baseline")
