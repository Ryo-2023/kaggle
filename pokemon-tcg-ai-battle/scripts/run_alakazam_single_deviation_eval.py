"""Run a balanced actual-CABT control/treatment experiment for Alakazam.

The treatment is candidate-only and exact-bound to ``Alakazam Baseline Deck
v1``.  Every treatment game has at most one legal, public-option-type-only
deviation; control games are Rule v0.  Results are descriptive because CABT
does not expose a controllable engine seed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from main import Agent, make_deterministic_agent, make_random_agent, make_rule_agent, make_rule_agent_v1, read_deck_csv
from mage_ptcg.optimization.alakazam_single_deviation import make_alakazam_single_deviation_agent
from mage_ptcg.opponents.synthetic_stress_v1 import make_synthetic_stress_agent
from scripts.test_sim import run_match


OPPONENTS = ("random", "deterministic", "rule_v1", "setup-heavy")
ARMS = ("rule_v0_control", "single_deviation_treatment")


def _opponent_factory(name: str) -> Callable[[list[int], int], Agent]:
    if name == "random":
        return lambda deck, seed: make_random_agent(deck=deck, seed=seed)
    if name == "deterministic":
        return lambda deck, seed: make_deterministic_agent(deck=deck)
    if name == "rule_v1":
        return lambda deck, seed: make_rule_agent_v1(deck=deck, seed=seed)
    if name == "setup-heavy":
        return lambda deck, seed: make_synthetic_stress_agent(kind="setup-heavy", deck=deck, seed=seed).as_agent()
    raise ValueError(f"unknown opponent {name}")


def _own_factory(arm: str) -> Callable[[list[int], int], Agent]:
    if arm == "rule_v0_control":
        return lambda deck, seed: make_rule_agent(deck=deck, seed=seed)
    if arm == "single_deviation_treatment":
        return lambda deck, seed: make_alakazam_single_deviation_agent(deck=deck).as_agent()
    raise ValueError(f"unknown arm {arm}")


def schedule(games: int) -> list[tuple[str, str, int]]:
    """Return arm, opponent, own-seat; all dimensions are exactly balanced."""
    if games <= 0 or games % (len(ARMS) * len(OPPONENTS) * 2):
        raise ValueError("games must be a positive multiple of 16")
    rows: list[tuple[str, str, int]] = []
    for replicate in range(games // 16):
        for arm in ARMS:
            for opponent in OPPONENTS:
                for own_seat in (0, 1):
                    rows.append((arm, opponent, own_seat))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        done = [row for row in selected if row["status"] == "DONE"]
        groups[arm] = {
            "games": len(selected),
            "completed": len(done),
            "wins": sum(row["own_won"] for row in done),
            "win_rate": (sum(row["own_won"] for row in done) / len(done)) if done else None,
            "faults": len(selected) - len(done),
            "mean_steps": (sum(row["steps"] for row in done) / len(done)) if done else None,
            "mean_elapsed_seconds": (sum(row["elapsed_seconds"] for row in done) / len(done)) if done else None,
        }
    by_opponent: dict[str, dict[str, Any]] = {}
    for opponent in OPPONENTS:
        by_opponent[opponent] = {}
        for arm in ARMS:
            selected = [row for row in rows if row["opponent"] == opponent and row["arm"] == arm and row["status"] == "DONE"]
            by_opponent[opponent][arm] = {
                "games": len(selected),
                "wins": sum(row["own_won"] for row in selected),
                "win_rate": (sum(row["own_won"] for row in selected) / len(selected)) if selected else None,
            }
    return {
        "schema": "alakazam-single-deviation-eval-v1",
        "games": len(rows),
        "arms": groups,
        "by_opponent": by_opponent,
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "limitations": [
            "CABT engine seed is not controllable; control and treatment are balanced but unpaired.",
            "Side/prize-zone contents and private observations are not collected by this runner.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--games", type=int, default=192)
    parser.add_argument("--seed", type=int, default=2026072601)
    args = parser.parse_args(argv)
    deck = read_deck_csv(args.deck)
    rows: list[dict[str, Any]] = []
    args.output.mkdir(parents=True, exist_ok=True)
    for index, (arm, opponent, own_seat) in enumerate(schedule(args.games)):
        own_factory, opponent_factory = _own_factory(arm), _opponent_factory(opponent)
        if own_seat == 0:
            result = run_match(deck_a_path=args.deck, deck_b_path=args.deck, agent_a_name=arm, agent_b_name=opponent, agent_a_factory=own_factory, agent_b_factory=opponent_factory, seed=args.seed + index, output_dir=args.output / "transient", save_html=False, save_result=False)
        else:
            result = run_match(deck_a_path=args.deck, deck_b_path=args.deck, agent_a_name=opponent, agent_b_name=arm, agent_a_factory=opponent_factory, agent_b_factory=own_factory, seed=args.seed + index, output_dir=args.output / "transient", save_html=False, save_result=False)
        own_won = result.get("winner") == own_seat
        rows.append({"game": index, "arm": arm, "opponent": opponent, "own_seat": own_seat, "own_won": own_won, "status": result.get("status"), "steps": result.get("steps"), "elapsed_seconds": result.get("elapsed_seconds"), "winner": result.get("winner")})
    payload = summarize(rows)
    (args.output / "matches.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status_counts"].get("DONE") == args.games else 2


if __name__ == "__main__":
    raise SystemExit(main())
