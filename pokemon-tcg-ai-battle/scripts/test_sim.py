"""Run one official cabt match and persist a replay plus machine-readable result."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from main import (  # noqa: E402
    Agent,
    Deck,
    DeckValidationError,
    make_deterministic_agent,
    make_random_agent,
    make_rule_agent,
    make_rule_agent_v1,
    read_deck_csv,
)


class MatchDependencyError(RuntimeError):
    """Raised when the official Kaggle cabt runtime is unavailable."""


class MatchExecutionError(RuntimeError):
    """Raised after a failed match has been recorded to result.json."""


def load_known_card_ids(path: str | Path) -> set[int]:
    """Load card IDs from the official competition card-data CSV."""
    card_data_path = Path(path)
    try:
        with card_data_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if "Card ID" not in (reader.fieldnames or []):
                raise DeckValidationError(
                    f"card data {card_data_path} does not contain a 'Card ID' column"
                )
            return {int(row["Card ID"]) for row in reader if row.get("Card ID")}
    except (OSError, ValueError) as exc:
        raise DeckValidationError(f"could not read card data {card_data_path}: {exc}") from exc


def save_result_json(path: str | Path, result: Mapping[str, Any]) -> None:
    """Persist a result that can be read back with the standard JSON decoder."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(dict(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _save_result_if_requested(
    path: str | Path,
    result: Mapping[str, Any],
    *,
    save_result: bool,
) -> None:
    if save_result:
        save_result_json(path, result)


def _should_save_html(save_html: bool | str, status: str) -> bool:
    if save_html is True:
        return True
    if save_html is False:
        return False
    if save_html == "failures":
        return status != "DONE"
    raise ValueError("save_html must be True, False, or 'failures'")


def _get(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _cabt_only_listdir(original: Callable[[str | bytes | Path], list[str]]) -> Callable[[str | bytes | Path], list[str]]:
    """Limit Kaggle's eager environment discovery to the one engine we use.

    ``kaggle_environments`` imports and registers every bundled environment at
    package import time.  Several unrelated environments import LiteLLM, which
    performs a remote price-map lookup and loads a large dependency tree.  A
    fresh isolated CABT process paid that cost once per game.

    The official CABT module and the public ``make`` API are unchanged.  Only
    the temporary directory listing used during package initialization is
    narrowed; all other filesystem listings delegate to the real function.
    """
    def listdir(path: str | bytes | Path) -> list[str]:
        value = os.fsdecode(path).replace("\\", "/").rstrip("/")
        if value.endswith("/kaggle_environments/envs"):
            return ["cabt"]
        return original(path)
    return listdir


def _load_make() -> Callable[..., Any]:
    original_listdir = os.listdir
    os.listdir = _cabt_only_listdir(original_listdir)
    try:
        from kaggle_environments import make
    except ModuleNotFoundError as exc:
        raise MatchDependencyError(
            "kaggle-environments with the cabt plugin is required; "
            "install requirements.txt (kaggle-environments==1.32.0)"
        ) from exc
    finally:
        os.listdir = original_listdir
    return make


def _make_agent(name: str, deck: Deck, seed: int) -> Agent:
    if name == "random":
        return make_random_agent(deck=deck, seed=seed)
    if name == "deterministic":
        return make_deterministic_agent(deck=deck)
    if name == "rule":
        return make_rule_agent(deck=deck, seed=seed)
    if name == "rule_v1":
        return make_rule_agent_v1(deck=deck, seed=seed)
    raise ValueError(f"unknown agent: {name}")


def _terminal_details(env: Any) -> tuple[int | None, int | None, int | None]:
    """Read exact cabt terminal values from the replay payload when available."""
    try:
        visualization = _get(env.steps[0][0], "visualize")
        if not visualization:
            return None, None, None
        terminal = visualization[-1]
        current = _get(terminal, "current", {})
        result = _get(current, "result")
        turn = _get(current, "turn")
        reason = None
        for log in reversed(_get(terminal, "logs", []) or []):
            log_type = _get(log, "type")
            if log_type in (23, "Result", "RESULT"):
                reason = _get(log, "reason")
                break
        return result if result in (0, 1, 2) else None, reason, turn
    except (AttributeError, IndexError, KeyError, TypeError):
        return None, None, None


def _initial_result(
    *,
    seed: int,
    max_steps: int,
    agent_a: str,
    agent_b: str,
    deck_a: Path,
    deck_b: Path,
) -> dict[str, Any]:
    return {
        "seed": seed,
        "agent_seed": seed,
        "engine_seed_supported": False,
        "max_steps": max_steps,
        "agent_a": agent_a,
        "agent_b": agent_b,
        "deck_a": str(deck_a.resolve()),
        "deck_b": str(deck_b.resolve()),
        "status": "STARTING",
        "winner": None,
        "terminal_reason": None,
        "steps": 0,
        "elapsed_seconds": 0.0,
        "agent_status": None,
        "rewards": None,
    }


def _classify_terminal_state(
    *,
    statuses: list[object],
    winner: int | None,
    steps: int,
    max_steps: int,
) -> str:
    """Classify a cabt episode without treating agent failures as wins."""
    if "INVALID" in statuses:
        return "AGENT_INVALID"
    if "ERROR" in statuses:
        return "AGENT_ERROR"
    if "TIMEOUT" in statuses:
        return "AGENT_TIMEOUT"
    if statuses == ["DONE", "DONE"] and winner in (0, 1, 2):
        return "DONE"
    if winner is None and steps >= max_steps - 1:
        return "STEP_LIMIT"
    return "INCOMPLETE"


def run_match(
    *,
    deck_a_path: str | Path,
    deck_b_path: str | Path,
    agent_a_name: str,
    agent_b_name: str,
    seed: int,
    output_dir: str | Path,
    max_steps: int = 10000,
    save_html: bool | str = True,
    save_result: bool = True,
    make_environment: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    agent_a_factory: Callable[[Deck, int], Agent] | None = None,
    agent_b_factory: Callable[[Deck, int], Agent] | None = None,
) -> dict[str, Any]:
    """Run one match through ``kaggle_environments.make('cabt')``."""
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")
    if not isinstance(save_result, bool):
        raise ValueError("save_result must be a boolean")
    _should_save_html(save_html, "DONE")
    deck_a_file = Path(deck_a_path)
    deck_b_file = Path(deck_b_path)
    destination = Path(output_dir)
    result_path = destination / "result.json"
    html_path = destination / "result.html"
    result = _initial_result(
        seed=seed,
        max_steps=max_steps,
        agent_a=agent_a_name,
        agent_b=agent_b_name,
        deck_a=deck_a_file,
        deck_b=deck_b_file,
    )
    result["result_html"] = None
    result["result_json"] = str(result_path.resolve()) if save_result else None
    started = clock()

    try:
        known_ids_path = REPOSITORY_ROOT / "data" / "raw" / "EN_Card_Data.csv"
        known_ids = load_known_card_ids(known_ids_path) if known_ids_path.is_file() else None
        deck_a = read_deck_csv(deck_a_file, known_card_ids=known_ids)
        deck_b = read_deck_csv(deck_b_file, known_card_ids=known_ids)
        agent_a = (
            agent_a_factory(deck_a, seed)
            if agent_a_factory is not None
            else _make_agent(agent_a_name, deck_a, seed)
        )
        agent_b = (
            agent_b_factory(deck_b, seed + 1)
            if agent_b_factory is not None
            else _make_agent(agent_b_name, deck_b, seed + 1)
        )

        make = make_environment or _load_make()
        env = make(
            "cabt",
            configuration={"decks": [deck_a, deck_b], "episodeSteps": max_steps},
        )
        episode = env.run([agent_a, agent_b])

        states = list(env.state)
        statuses = [_get(state, "status") for state in states]
        rewards = [_get(state, "reward") for state in states]
        winner, reason, turn = _terminal_details(env)
        steps = max(0, len(episode) - 1)
        status = _classify_terminal_state(
            statuses=statuses,
            winner=winner,
            steps=steps,
            max_steps=max_steps,
        )
        terminal_reason = (
            f"cabt_result={winner}; reason={reason}"
            if winner is not None
            else "cabt terminal result unavailable"
        )
        if status != "DONE":
            terminal_reason = f"{status}; {terminal_reason}"
        result.update(
            {
                "status": status,
                "winner": winner if status == "DONE" else None,
                "terminal_reason": terminal_reason,
                "steps": steps,
                "cabt_turn": turn,
                "agent_status": statuses,
                "rewards": rewards if status == "DONE" else None,
            }
        )

        if _should_save_html(save_html, status):
            try:
                rendered = env.render(mode="html")
                if not isinstance(rendered, str) or not rendered:
                    raise MatchExecutionError("cabt did not produce an HTML replay")
                destination.mkdir(parents=True, exist_ok=True)
                html_path.write_text(rendered, encoding="utf-8")
                result["result_html"] = str(html_path.resolve())
            except Exception:
                if status == "DONE":
                    raise
    except Exception as exc:
        result["status"] = "ERROR"
        result["terminal_reason"] = f"{type(exc).__name__}: {exc}"
        result["elapsed_seconds"] = round(clock() - started, 6)
        _save_result_if_requested(result_path, result, save_result=save_result)
        if isinstance(exc, (DeckValidationError, MatchDependencyError, MatchExecutionError)):
            raise
        raise MatchExecutionError(str(exc)) from exc

    result["elapsed_seconds"] = round(clock() - started, 6)
    _save_result_if_requested(result_path, result, save_result=save_result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-a", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--deck-b", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument(
        "--agent-a", choices=("random", "deterministic", "rule", "rule_v1"), default="random"
    )
    parser.add_argument(
        "--agent-b",
        choices=("random", "deterministic", "rule", "rule_v1"),
        default="deterministic",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-steps", type=_positive_int, default=10000)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "matches" / "first-playable",
    )
    return parser


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_match(
            deck_a_path=args.deck_a,
            deck_b_path=args.deck_b,
            agent_a_name=args.agent_a,
            agent_b_name=args.agent_b,
            seed=args.seed,
            max_steps=args.max_steps,
            output_dir=args.output_dir,
        )
    except (DeckValidationError, MatchDependencyError, MatchExecutionError) as exc:
        print(f"Match failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "DONE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
