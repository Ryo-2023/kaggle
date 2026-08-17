"""Evaluate C3 search contracts with a deterministic fake transition adapter.

This script does not run matches and does not estimate cabt playing strength.
It exists because the submission ``agent(obs)`` contract in
kaggle-environments 1.32.0 exposes no documented public API for reconstructing
an arbitrary cabt decision state and applying one candidate action.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mage_ptcg.decision_state import DecisionState, build_decision_state  # noqa: E402
from mage_ptcg.solver import BoundedSearchConfig, EngineTransition  # noqa: E402
from main import (  # noqa: E402
    make_bounded_search_agent,
    make_rule_agent,
    read_deck_csv,
)


DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "artifacts" / "evaluations" / "c3-bounded-search-v0"
DEFAULT_KNOWLEDGE_PACK = REPOSITORY_ROOT / "artifacts" / "knowledge" / "team_deck_v0.json"


@dataclass(frozen=True, slots=True)
class FixtureCase:
    case_id: str
    observation: dict[str, Any]
    values: dict[tuple[int, ...], float]
    expected_selection: tuple[int, ...]


def _card(card_id: int) -> dict[str, Any]:
    return {
        "id": card_id,
        "serial": card_id,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }


def _player(card_id: int) -> dict[str, Any]:
    return {
        "active": [],
        "asleep": False,
        "bench": [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": [_card(card_id)],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [None] * 6,
    }


def _observation(
    *,
    turn: int,
    select_type: int,
    selection_context: int = 0,
    options: list[dict[str, object]],
    minimum: int = 1,
    maximum: int = 1,
) -> dict[str, Any]:
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [_player(100), _player(700)],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": turn,
            "turnActionCount": 1,
            "yourIndex": 0,
        },
        "select": {
            "context": selection_context,
            "maxCount": maximum,
            "minCount": minimum,
            "option": options,
            "type": select_type,
        },
        "step": turn,
    }


def fixture_cases() -> tuple[FixtureCase, ...]:
    """Return deterministic contract cases; none is a simulated match."""
    return (
        FixtureCase(
            "prior_tie_break",
            _observation(
                turn=10,
                select_type=0,
                options=[{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}],
            ),
            {(0,): 0.0, (1,): 0.0, (2,): 0.0},
            (2,),
        ),
        FixtureCase(
            "forward_value_overrides_prior",
            _observation(
                turn=11,
                select_type=0,
                options=[{"type": 7, "index": 0}, {"type": 7, "index": 1}],
            ),
            {(0,): 0.0, (1,): 5.0},
            (1,),
        ),
        FixtureCase(
            "target_selection",
            _observation(
                turn=12,
                select_type=1,
                selection_context=1,
                options=[
                    {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                    {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
                ],
            ),
            {(0,): 3.0, (1,): -2.0},
            (0,),
        ),
        FixtureCase(
            "optional_selection",
            _observation(
                turn=13,
                select_type=4,
                selection_context=30,
                options=[
                    {"type": 6, "area": 2, "index": 0, "playerIndex": 0, "energyIndex": 0, "count": 1},
                    {"type": 6, "area": 2, "index": 1, "playerIndex": 0, "energyIndex": 1, "count": 1},
                ],
                minimum=0,
                maximum=1,
            ),
            {(): 0.0, (0,): 1.0, (1,): 4.0},
            (1,),
        ),
        FixtureCase(
            "primitive_complete_multi_selection",
            _observation(
                turn=14,
                select_type=8,
                selection_context=38,
                options=[
                    {"type": 0, "number": 0},
                    {"type": 0, "number": 1},
                    {"type": 0, "number": 2},
                ],
                minimum=2,
                maximum=2,
            ),
            {(0, 1): 4.0, (1, 0): 2.0, (2, 0): 1.0, (2, 1): 0.0},
            (0, 1),
        ),
    )


class DeterministicFakeAdapter:
    """In-memory forward model for testing the public EngineAdapter protocol."""

    def __init__(self, cases: tuple[FixtureCase, ...]) -> None:
        transitions: dict[tuple[str, tuple[int, ...]], float] = {}
        for case in cases:
            digest = build_decision_state(case.observation).digest
            for selection, value in case.values.items():
                transitions[(digest, selection)] = value
        self._transitions = transitions
        self.calls = 0

    def step(
        self,
        state: DecisionState,
        selection: tuple[int, ...],
        *,
        deadline_ns: int,
    ) -> EngineTransition:
        del deadline_ns
        self.calls += 1
        try:
            value = self._transitions[(state.digest, selection)]
        except KeyError as exc:
            raise LookupError("fixture transition is not defined") from exc
        return EngineTransition(value=value, terminal=True)


def _is_legal(selection: list[int], observation: dict[str, Any]) -> bool:
    select = observation["select"]
    minimum = select["minCount"]
    maximum = select["maxCount"]
    option_count = len(select["option"])
    return (
        minimum <= len(selection) <= maximum
        and len(selection) == len(set(selection))
        and all(type(index) is int and 0 <= index < option_count for index in selection)
    )


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.999999)))
    return ordered[index]


def _summarize_records(records: list[dict[str, object]]) -> dict[str, object]:
    latencies = [float(record["elapsed_ms"]) for record in records]
    coverage_values = [
        float(record["primitive_coverage"])
        for record in records
        if isinstance(record["primitive_coverage"], (int, float))
        and not isinstance(record["primitive_coverage"], bool)
    ]
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[str(record["selection_type"])].append(record)
    return {
        "decisions": len(records),
        "legal_action_rate": sum(bool(record["legal"]) for record in records) / len(records),
        "primitive_coverage_rate": (
            sum(value == 1.0 for value in coverage_values) / len(coverage_values)
            if coverage_values
            else None
        ),
        "fixture_oracle_agreement_rate": sum(
            bool(record["fixture_oracle_agreement"]) for record in records
        )
        / len(records),
        "engine_calls_per_decision": statistics.mean(
            int(record["engine_calls"]) for record in records
        ),
        "fallback_rate": sum(record["fallback_reason"] is not None for record in records)
        / len(records),
        "timeout_rate": sum(bool(record["timed_out"]) for record in records) / len(records),
        "latency_ms": {
            "scope": "local_contract_fixture_agent_call_only",
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "maximum": max(latencies),
        },
        "by_selection_type": {
            select_type: {
                "decisions": len(items),
                "legal_action_rate": sum(bool(item["legal"]) for item in items) / len(items),
                "fixture_oracle_agreement_rate": sum(
                    bool(item["fixture_oracle_agreement"]) for item in items
                )
                / len(items),
            }
            for select_type, items in sorted(grouped.items())
        },
    }


def _run_once(
    *,
    cases: tuple[FixtureCase, ...],
    deck: list[int],
    knowledge_pack: Path,
) -> dict[str, list[dict[str, object]]]:
    rule = make_rule_agent(deck=deck)
    guided_adapter = DeterministicFakeAdapter(cases)
    unguided_adapter = DeterministicFakeAdapter(cases)
    config = BoundedSearchConfig(max_depth=1)
    guided = make_bounded_search_agent(
        deck=deck,
        knowledge_pack=knowledge_pack,
        engine_adapter=guided_adapter,
        search_config=config,
        guided=True,
    )
    unguided = make_bounded_search_agent(
        deck=deck,
        engine_adapter=unguided_adapter,
        search_config=config,
        guided=False,
    )
    agents = {"rule_v0": rule, "guided": guided, "unguided": unguided}
    results: dict[str, list[dict[str, object]]] = {name: [] for name in agents}

    for case in cases:
        for name, agent in agents.items():
            started_ns = time.perf_counter_ns()
            selection = agent(case.observation)
            elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            search_result = getattr(agent, "last_search_result", None)
            if search_result is None:
                engine_calls = 0
                primitive_coverage = None
                fallback_reason = None
                timed_out = False
                knowledge_enabled = False
                signature: object = selection
            else:
                engine_calls = search_result.engine_calls
                primitive_coverage = search_result.primitive_coverage
                fallback_reason = search_result.fallback_reason
                timed_out = search_result.timed_out
                knowledge_enabled = search_result.knowledge_enabled
                signature = search_result.deterministic_signature()
            results[name].append(
                {
                    "case_id": case.case_id,
                    "selection_type": case.observation["select"]["type"],
                    "selection": selection,
                    "rule_v0_selection": rule(case.observation),
                    "expected_selection": list(case.expected_selection),
                    "legal": _is_legal(selection, case.observation),
                    "fixture_oracle_agreement": tuple(selection) == case.expected_selection,
                    "engine_calls": engine_calls,
                    "primitive_coverage": primitive_coverage,
                    "fallback_reason": fallback_reason,
                    "timed_out": timed_out,
                    "knowledge_enabled": knowledge_enabled,
                    "elapsed_ms": elapsed_ms,
                    "deterministic_signature": signature,
                }
            )
    return results


def _without_measurements(results: dict[str, list[dict[str, object]]]) -> object:
    return {
        condition: [
            {key: value for key, value in record.items() if key != "elapsed_ms"}
            for record in records
        ]
        for condition, records in results.items()
    }


def run_fixture_evaluation(
    *,
    output_dir: str | Path,
    deck_path: str | Path = REPOSITORY_ROOT / "deck.csv",
    knowledge_pack: str | Path = DEFAULT_KNOWLEDGE_PACK,
) -> dict[str, object]:
    """Run paired decision fixtures and write auditable non-match artifacts."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    cases = fixture_cases()
    deck = read_deck_csv(deck_path)
    pack_path = Path(knowledge_pack)
    try:
        pack_label = str(pack_path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        pack_label = str(pack_path)
    first = _run_once(cases=cases, deck=deck, knowledge_pack=pack_path)
    second = _run_once(cases=cases, deck=deck, knowledge_pack=pack_path)
    reproducible = _without_measurements(first) == _without_measurements(second)

    records = [
        {"condition": condition, **record}
        for condition, condition_records in first.items()
        for record in condition_records
    ]
    by_case = {
        case.case_id: {
            condition: next(
                record for record in first[condition] if record["case_id"] == case.case_id
            )
            for condition in first
        }
        for case in cases
    }
    counterexamples = [
        {
            "case_id": case_id,
            "selection_type": values["guided"]["selection_type"],
            "rule_v0_selection": values["rule_v0"]["selection"],
            "guided_selection": values["guided"]["selection"],
            "unguided_selection": values["unguided"]["selection"],
            "fixture_expected_selection": values["guided"]["expected_selection"],
            "classification": "contract_fixture_disagreement_not_cabt_match_result",
        }
        for case_id, values in by_case.items()
        if values["guided"]["selection"] != values["rule_v0"]["selection"]
        or values["guided"]["selection"] != values["unguided"]["selection"]
    ]
    guided_records = first["guided"]
    unguided_records = first["unguided"]
    disagreement_count = sum(
        guided["selection"] != unguided["selection"]
        for guided, unguided in zip(guided_records, unguided_records, strict=True)
    )
    summary: dict[str, object] = {
        "schema_version": "bounded-search-evaluation-v0",
        "evaluation_scope": {
            "kind": "deterministic_fake_adapter_contract_fixture",
            "synthetic_match": False,
            "actual_cabt_paired_evaluation": "NOT_RUN",
            "actual_cabt_performance_improvement_confirmed": False,
            "reason": (
                "Environment.clone/step requires an evaluator-owned Environment; "
                "agent(obs) has no documented arbitrary-state reconstruction API"
            ),
        },
        "budget": {
            "max_depth": 1,
            "max_expansions": 64,
            "max_engine_calls": 64,
            "wall_clock_budget_ms": 20.0,
            "hard_deadline_margin_ms": 1.0,
            "primitive_exploration_fraction": 1.0,
        },
        "case_count": len(cases),
        "conditions": {
            condition: _summarize_records(condition_records)
            for condition, condition_records in first.items()
        },
        "guided_vs_unguided": {
            "paired_fixture_decisions": len(cases),
            "selection_disagreement_count": disagreement_count,
            "selection_disagreement_rate": disagreement_count / len(cases),
            "interpretation_scope": "fixture_oracle_only_not_playing_strength",
        },
        "reproducibility": {
            "repeat_count": 2,
            "decision_signatures_identical": reproducible,
        },
        "knowledge_pack": {
            "path": pack_label,
            "guided_enabled_decisions": sum(
                bool(record["knowledge_enabled"]) for record in guided_records
            ),
            "unguided_import_requested": False,
        },
        "counterexample_count": len(counterexamples),
    }

    (target / "decisions.jsonl").write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    (target / "counterexamples.json").write_text(
        json.dumps(
            {
                "schema_version": "bounded-search-counterexamples-v0",
                "scope": "deterministic_fake_adapter_contract_fixture",
                "actual_cabt_match_evidence": False,
                "items": counterexamples,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (target / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--knowledge-pack", type=Path, default=DEFAULT_KNOWLEDGE_PACK)
    return parser


def main() -> int:
    args = _parser().parse_args()
    summary = run_fixture_evaluation(
        output_dir=args.output_dir,
        deck_path=args.deck,
        knowledge_pack=args.knowledge_pack,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
