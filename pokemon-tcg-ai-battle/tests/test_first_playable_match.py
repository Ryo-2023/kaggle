from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from main import (
    DeckValidationError,
    make_deterministic_agent,
    make_random_agent,
    read_deck_csv,
)
from scripts.test_sim import _cabt_only_listdir, build_parser, load_known_card_ids, run_match, save_result_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _write_deck(path: Path, values: list[object]) -> Path:
    path.write_text("\n".join(map(str, values)) + "\n", encoding="utf-8")
    return path


def _selection(
    option_count: int,
    *,
    min_count: int,
    max_count: int,
    select_type: int = 1,
) -> dict:
    return {
        "select": {
            "type": select_type,
            "option": [{"type": 3} for _ in range(option_count)],
            "minCount": min_count,
            "maxCount": max_count,
        }
    }


def test_deck_loader_accepts_exactly_sixty_integer_cards(tmp_path: Path) -> None:
    deck_path = _write_deck(tmp_path / "deck.csv", list(range(1, 61)))

    assert read_deck_csv(deck_path) == list(range(1, 61))


@pytest.mark.parametrize(
    "values",
    [list(range(59)), [*range(59), "not-an-int"]],
)
def test_deck_loader_rejects_invalid_decks(tmp_path: Path, values: list[object]) -> None:
    deck_path = _write_deck(tmp_path / "deck.csv", values)

    with pytest.raises(DeckValidationError):
        read_deck_csv(deck_path)


def test_deck_loader_rejects_card_ids_absent_from_official_data(tmp_path: Path) -> None:
    deck_path = _write_deck(tmp_path / "deck.csv", [1] * 59 + [999])

    with pytest.raises(DeckValidationError, match="unknown card IDs"):
        read_deck_csv(deck_path, known_card_ids={1, 2, 3})


def test_load_known_card_ids_reads_official_column(tmp_path: Path) -> None:
    card_data = tmp_path / "cards.csv"
    card_data.write_text("Card ID,Card Name\n1,One\n2,Two\n", encoding="utf-8")

    assert load_known_card_ids(card_data) == {1, 2}


def test_random_agent_never_returns_out_of_range_or_duplicate_options() -> None:
    agent = make_random_agent(deck=[1] * 60, seed=7)
    observation = _selection(7, min_count=2, max_count=5)

    action = agent(observation)

    assert len(action) == 5
    assert len(set(action)) == len(action)
    assert all(0 <= index < 7 for index in action)


def test_random_agent_is_reproducible_with_the_same_seed() -> None:
    first = make_random_agent(deck=[1] * 60, seed=42)
    second = make_random_agent(deck=[1] * 60, seed=42)
    observation = _selection(8, min_count=1, max_count=3)

    assert [first(observation) for _ in range(4)] == [second(observation) for _ in range(4)]


def test_agents_return_their_injected_deck_when_selection_is_absent() -> None:
    deck = list(range(1, 61))

    assert make_random_agent(deck=deck, seed=1)({"select": None}) == deck
    assert make_deterministic_agent(deck=deck)({"select": None}) == deck


def test_deterministic_agent_prioritizes_attack_during_main_selection() -> None:
    agent = make_deterministic_agent(deck=[1] * 60)
    observation = {
        "select": {
            "type": 0,
            "option": [
                {"type": 14},  # END
                {"type": 7},  # PLAY
                {"type": 13},  # ATTACK
            ],
            "minCount": 1,
            "maxCount": 1,
        }
    }

    assert agent(observation) == [2]
    assert agent(observation) == [2]


def test_deterministic_agent_uses_stable_legal_order_for_auxiliary_selection() -> None:
    agent = make_deterministic_agent(deck=[1] * 60)
    observation = _selection(5, min_count=1, max_count=3, select_type=9)

    assert agent(observation) == [0, 1, 2]


def test_zero_count_selection_is_safe() -> None:
    observation = _selection(0, min_count=0, max_count=0)

    assert make_random_agent(deck=[1] * 60, seed=1)(observation) == []
    assert make_deterministic_agent(deck=[1] * 60)(observation) == []


def test_importing_test_sim_does_not_change_directory_or_start_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = Path.cwd()
    calls: list[str] = []
    monkeypatch.setitem(
        __import__("sys").modules,
        "kaggle_environments",
        type("ForbiddenKaggleModule", (), {"make": lambda *args, **kwargs: calls.append("make")})(),
    )

    module = importlib.reload(importlib.import_module("scripts.test_sim"))

    assert Path.cwd() == before
    assert calls == []
    assert callable(module.main)


def test_cabt_import_filter_only_changes_kaggle_environment_registry_listing() -> None:
    calls: list[object] = []

    def original(path: object) -> list[str]:
        calls.append(path)
        return ["untouched"]

    filtered = _cabt_only_listdir(original)

    assert filtered("/opt/python/kaggle_environments/envs") == ["cabt"]
    assert filtered(Path("/tmp/another/envs")) == ["untouched"]
    assert calls == [Path("/tmp/another/envs")]


def test_output_json_can_be_saved_and_read_back(tmp_path: Path) -> None:
    payload = {"status": "DONE", "winner": 0, "steps": 12}
    output = tmp_path / "result.json"

    save_result_json(output, payload)

    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_run_match_uses_official_make_shape_and_writes_artifacts(tmp_path: Path) -> None:
    deck_path = _write_deck(tmp_path / "deck.csv", [1] * 60)
    calls: list[tuple[str, dict]] = []

    class FakeEnvironment:
        done = True
        state = [
            {"status": "DONE", "reward": 1},
            {"status": "DONE", "reward": -1},
        ]
        steps = [
            [
                {
                    "visualize": [
                        {
                            "current": {"result": 0, "turn": 4},
                            "logs": [{"type": "Result", "reason": 3}],
                        }
                    ]
                },
                {},
            ]
        ]

        def run(self, agents):
            assert agents[0]({"select": None}) == [1] * 60
            assert agents[1]({"select": None}) == [1] * 60
            return [[{}, {}], [{}, {}]]

        def render(self, *, mode: str) -> str:
            assert mode == "html"
            return "<html>cabt replay</html>"

    def fake_make(name: str, *, configuration: dict):
        calls.append((name, configuration))
        return FakeEnvironment()

    ticks = iter((10.0, 10.25))
    result = run_match(
        deck_a_path=deck_path,
        deck_b_path=deck_path,
        agent_a_name="random",
        agent_b_name="deterministic",
        seed=42,
        max_steps=12,
        output_dir=tmp_path / "artifacts",
        make_environment=fake_make,
        clock=lambda: next(ticks),
    )

    assert calls == [
        ("cabt", {"decks": [[1] * 60, [1] * 60], "episodeSteps": 12})
    ]
    assert result["status"] == "DONE"
    assert result["winner"] == 0
    assert result["rewards"] == [1, -1]
    assert result["max_steps"] == 12
    assert result["agent_seed"] == 42
    assert result["engine_seed_supported"] is False
    assert result["steps"] == 1
    assert result["elapsed_seconds"] == 0.25
    assert (tmp_path / "artifacts" / "result.html").is_file()
    saved = json.loads((tmp_path / "artifacts" / "result.json").read_text(encoding="utf-8"))
    assert saved == result


def _run_terminal_environment(
    tmp_path: Path,
    *,
    statuses: list[str],
    winner: int | None,
    episode_length: int,
    max_steps: int,
) -> dict:
    deck_path = _write_deck(tmp_path / "deck.csv", [1] * 60)

    class FakeEnvironment:
        done = True
        state = [
            {"status": statuses[0], "reward": 1},
            {"status": statuses[1], "reward": -1},
        ]
        steps = [
            [
                {
                    "visualize": [
                        {
                            "current": {"result": winner if winner is not None else -1, "turn": 4},
                            "logs": [],
                        }
                    ]
                },
                {},
            ]
        ]

        def run(self, _agents):
            return [[{}, {}] for _ in range(episode_length)]

        def render(self, *, mode: str) -> str:
            assert mode == "html"
            return "<html>cabt replay</html>"

    return run_match(
        deck_a_path=deck_path,
        deck_b_path=deck_path,
        agent_a_name="random",
        agent_b_name="deterministic",
        seed=42,
        max_steps=max_steps,
        output_dir=tmp_path / "artifacts",
        make_environment=lambda _name, *, configuration: FakeEnvironment(),
    )


def test_run_match_rejects_non_positive_max_steps(tmp_path: Path) -> None:
    deck_path = _write_deck(tmp_path / "deck.csv", [1] * 60)

    with pytest.raises(ValueError, match="positive integer"):
        run_match(
            deck_a_path=deck_path,
            deck_b_path=deck_path,
            agent_a_name="random",
            agent_b_name="deterministic",
            seed=42,
            max_steps=0,
            output_dir=tmp_path / "artifacts",
        )


def test_cli_rejects_non_positive_max_steps() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--max-steps", "0"])


def test_invalid_agent_status_is_not_classified_as_done(tmp_path: Path) -> None:
    result = _run_terminal_environment(
        tmp_path,
        statuses=["INVALID", "DONE"],
        winner=0,
        episode_length=2,
        max_steps=10,
    )

    assert result["status"] == "AGENT_INVALID"
    assert result["winner"] is None
    assert result["rewards"] is None


def test_error_agent_status_is_not_classified_as_done(tmp_path: Path) -> None:
    result = _run_terminal_environment(
        tmp_path,
        statuses=["ERROR", "DONE"],
        winner=0,
        episode_length=2,
        max_steps=10,
    )

    assert result["status"] == "AGENT_ERROR"
    assert result["winner"] is None
    assert result["rewards"] is None


def test_timeout_agent_status_is_not_classified_as_done(tmp_path: Path) -> None:
    result = _run_terminal_environment(
        tmp_path,
        statuses=["TIMEOUT", "DONE"],
        winner=0,
        episode_length=2,
        max_steps=10,
    )

    assert result["status"] == "AGENT_TIMEOUT"
    assert result["winner"] is None
    assert result["rewards"] is None


def test_missing_winner_at_step_limit_is_saved_as_step_limit(tmp_path: Path) -> None:
    output_dir = tmp_path / "artifacts"
    result = _run_terminal_environment(
        tmp_path,
        statuses=["DONE", "DONE"],
        winner=None,
        episode_length=10,
        max_steps=10,
    )

    assert result["status"] == "STEP_LIMIT"
    assert result["winner"] is None
    saved = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "STEP_LIMIT"


def test_missing_winner_before_step_limit_is_incomplete(tmp_path: Path) -> None:
    result = _run_terminal_environment(
        tmp_path,
        statuses=["DONE", "DONE"],
        winner=None,
        episode_length=2,
        max_steps=10,
    )

    assert result["status"] == "INCOMPLETE"
    assert result["winner"] is None


def test_only_done_statuses_with_a_terminal_winner_are_normal_completion(tmp_path: Path) -> None:
    result = _run_terminal_environment(
        tmp_path,
        statuses=["DONE", "DONE"],
        winner=2,
        episode_length=2,
        max_steps=10,
    )

    assert result["status"] == "DONE"
    assert result["winner"] == 2


@pytest.mark.skipif(
    importlib.util.find_spec("kaggle_environments") is None,
    reason="kaggle-environments with cabt is not installed",
)
def test_real_cabt_completes_one_smoke_match() -> None:
    from kaggle_environments import make

    deck = read_deck_csv(REPOSITORY_ROOT / "deck.csv")
    env = make("cabt", configuration={"decks": [deck, deck]})
    episode = env.run(
        [
            make_random_agent(deck=deck, seed=42),
            make_deterministic_agent(deck=deck),
        ]
    )

    assert len(episode) > 1
    assert env.done
    assert [_state.status for _state in env.state] == ["DONE", "DONE"]
    assert sorted(_state.reward for _state in env.state) == [-1, 1]
