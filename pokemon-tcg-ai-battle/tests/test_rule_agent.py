from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agents.rule_agent import choose_rule_indices
from main import make_deterministic_agent, make_random_agent, make_rule_agent
from scripts.test_sim import _make_agent, run_match


def _selection(
    options: list[object],
    *,
    minimum: int = 1,
    maximum: int = 1,
    select_type: int | str = 1,
    context: int | str = 1,
) -> dict:
    return {
        "select": {
            "type": select_type,
            "context": context,
            "option": options,
            "minCount": minimum,
            "maxCount": maximum,
        }
    }


def test_missing_observation_and_select_register_the_deck() -> None:
    deck = list(range(1, 61))
    agent = make_rule_agent(deck=deck)

    assert choose_rule_indices(None) is None
    assert agent({}) == deck
    assert agent({"select": None}) == deck


def test_main_imports_with_the_rule_v0_runtime_bundle(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    source = repository_root / "main.py"
    isolated_main = tmp_path / "main.py"
    isolated_main.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(repository_root / "agents", tmp_path / "agents")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    script = """
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('isolated_main', Path('main.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
deck = [1] * 60
assert module.make_random_agent(deck=deck, seed=7)({'select': None}) == deck
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_repository_main_raw_exec_without_file_and_separate_cwd(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    agent_root = tmp_path / "kaggle_simulations" / "agent"
    working_root = tmp_path / "kaggle" / "working"
    agent_root.mkdir(parents=True)
    working_root.mkdir(parents=True)
    shutil.copy2(repository_root / "main.py", agent_root / "main.py")
    shutil.copy2(repository_root / "deck.csv", agent_root / "deck.csv")
    shutil.copytree(repository_root / "agents", agent_root / "agents")

    script = """
from pathlib import Path
p = Path(__import__('os').environ['RAW_MAIN_PATH'])
env = {'__name__': '__main__'}
exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'), env)
action = env['agent']({'select': None})
assert isinstance(action, list) and len(action) == 60
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["RAW_MAIN_PATH"] = str(agent_root / "main.py")
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=working_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_submission_default_is_the_rule_v0_champion() -> None:
    import main

    observation = _selection(
        [{"type": 14}, {"type": 13}, {"type": 7}], select_type=0, context=0
    )

    assert main.agent(observation) == make_rule_agent(deck=[1] * 60)(observation) == [2]


@pytest.mark.parametrize(
    "observation",
    [
        {"select": {"option": None}},
        {"select": {"option": {"type": 7}}},
        {"select": {"option": [], "minCount": 1, "maxCount": 1}},
    ],
)
def test_missing_empty_or_malformed_options_are_safe(observation: dict) -> None:
    assert make_rule_agent(deck=[1] * 60)(observation) == []


def test_optional_selection_returns_zero_indices() -> None:
    observation = _selection([{"type": 3}, {"type": 3}], minimum=0, maximum=2)

    assert choose_rule_indices(observation) == []


def test_mandatory_selection_enforces_minimum_and_maximum() -> None:
    observation = _selection([{"type": "UNKNOWN"} for _ in range(5)], minimum=3, maximum=4)

    result = choose_rule_indices(observation)

    assert result == [0, 1, 2]
    assert 3 <= len(result) <= 4


def test_multi_selection_is_unique_and_in_range() -> None:
    observation = _selection(
        [{"type": 14}, {"type": 8}, {"type": 9}, {"type": 7}],
        minimum=2,
        maximum=3,
        select_type=0,
        context=0,
    )

    result = choose_rule_indices(observation)

    assert result == [2, 1]
    assert len(result) == len(set(result)) == 2
    assert all(0 <= index < 4 for index in result)


def test_output_is_deterministic_for_identical_observations_and_seed() -> None:
    observation = _selection(
        [{"type": 14}, {"type": 13}, {"type": 7}], select_type=0, context=0
    )
    first = make_rule_agent(deck=[1] * 60, seed=3)
    second = make_rule_agent(deck=[1] * 60, seed=999)

    assert [first(observation) for _ in range(3)] == [second(observation) for _ in range(3)]


def test_unknown_context_and_option_type_use_stable_fallback() -> None:
    observation = _selection([None, {"unexpected": object()}, {"type": 999}], context="FUTURE")

    assert choose_rule_indices(observation) == [0]


def test_numeric_option_type_twelve_uses_unknown_stable_fallback() -> None:
    observation = _selection(
        [{"type": 999}, {"type": 12}, {"type": 14}], select_type=0, context=0
    )

    assert choose_rule_indices(observation) == [0]


def test_productive_main_action_beats_attack_and_end() -> None:
    observation = _selection(
        [{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}],
        select_type=0,
        context=0,
    )

    assert choose_rule_indices(observation) == [2]


def test_setup_target_is_selected_safely() -> None:
    observation = _selection(
        [
            {"type": 3, "area": 2, "index": 0, "playerIndex": 1},
            {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
        ],
        context=7,
    )
    observation["current"] = {"yourIndex": 0}

    assert choose_rule_indices(observation) == [1]


def test_target_scoring_uses_only_visible_damage_and_hp() -> None:
    observation = _selection(
        [
            {"type": 3, "damage": 90, "hp": 120},
            {"type": 3, "damage": 80, "hp": 80},
        ],
        context=7,
    )

    assert choose_rule_indices(observation) == [1]


def test_hidden_hand_and_opaque_payload_are_not_read() -> None:
    class GuardedObservation(dict):
        def get(self, key, default=None):
            if key in {"opponentHand", "search_begin_input", "opaque"}:
                raise AssertionError(f"forbidden field read: {key}")
            return super().get(key, default)

    opaque = object()
    observation = GuardedObservation(
        _selection([{"type": 3, "opaque": opaque}], context=7)
    )
    observation["opponentHand"] = ["secret"]
    observation["search_begin_input"] = "secret"

    assert choose_rule_indices(observation) == [0]


def test_existing_and_rule_agents_are_registered_and_invalid_name_is_rejected() -> None:
    deck = [1] * 60

    assert make_random_agent(deck=deck, seed=1)({"select": None}) == deck
    assert make_deterministic_agent(deck=deck)({"select": None}) == deck
    assert _make_agent("rule", deck, 1)({"select": None}) == deck
    with pytest.raises(ValueError, match="unknown agent"):
        _make_agent("not-an-agent", deck, 1)


@pytest.mark.skipif(
    importlib.util.find_spec("kaggle_environments") is None,
    reason="kaggle-environments with cabt is not installed",
)
def test_official_cabt_rule_agent_smoke(tmp_path: Path) -> None:
    result = run_match(
        deck_a_path=Path("deck.csv"),
        deck_b_path=Path("deck.csv"),
        agent_a_name="rule",
        agent_b_name="random",
        seed=1200,
        max_steps=10000,
        output_dir=tmp_path / "rule-smoke",
        save_html=False,
    )

    assert result["status"] == "DONE"


@pytest.mark.skipif(
    importlib.util.find_spec("kaggle_environments") is None,
    reason="kaggle-environments with cabt is not installed",
)
def test_official_cabt_rule_vs_rule_smoke(tmp_path: Path) -> None:
    result = run_match(
        deck_a_path=Path("deck.csv"),
        deck_b_path=Path("deck.csv"),
        agent_a_name="rule",
        agent_b_name="rule",
        seed=1201,
        max_steps=10000,
        output_dir=tmp_path / "rule-vs-rule-smoke",
        save_html=False,
    )

    assert result["status"] == "DONE"
