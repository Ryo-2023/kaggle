"""Run the resumable League contract through the official cabt loader only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import read_deck_csv  # noqa: E402
from mage_ptcg.distillation.contracts import atomic_write_json  # noqa: E402
from mage_ptcg.league.actual_runner import ActualLeagueConfig, run_actual_league  # noqa: E402
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256  # noqa: E402
from scripts.cabt_capability import diagnose_cabt_capability  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


AGENTS = ("random", "deterministic", "rule", "rule_v1")


class LeagueCapabilityError(RuntimeError):
    """Raised before any League artifact is created for an unavailable cabt."""


def run_official_league(
    *,
    champion_agent: str,
    challenger_agent: str,
    games: int,
    base_seed: int,
    output_path: str | Path,
    deck_path: str | Path = REPOSITORY_ROOT / "deck.csv",
    max_steps: int = 10_000,
    capability_report: dict[str, object] | None = None,
    match_runner: Any = run_match,
) -> dict[str, object]:
    """Run real cabt games and retain only public match-level outcomes."""
    if champion_agent != "rule":
        raise ValueError("champion agent must remain rule")
    if challenger_agent not in AGENTS or challenger_agent == champion_agent:
        raise ValueError("challenger must be a distinct supported agent")
    report = capability_report if capability_report is not None else diagnose_cabt_capability()
    if report.get("status") != "READY":
        raise LeagueCapabilityError(f"cabt capability unavailable: {report.get('reason_code', 'UNKNOWN')}")

    deck = read_deck_csv(deck_path)
    package_version = report.get("kaggle_environments_version")
    config = ActualLeagueConfig(
        champion="rule-agent-v0",
        challenger=challenger_agent,
        games=games,
        base_seed=base_seed,
        deck_fingerprint=canonical_deck_sha256(deck),
        environment_version=f"cabt/kaggle-environments-{package_version}",
        max_steps=max_steps,
    )
    destination = Path(output_path)

    def play(schedule: dict[str, object]) -> dict[str, object]:
        champion_seat = int(schedule["champion_player_index"])
        first_agent, second_agent = (
            (champion_agent, challenger_agent) if champion_seat == 0 else (challenger_agent, champion_agent)
        )
        raw = match_runner(
            deck_a_path=deck_path,
            deck_b_path=deck_path,
            agent_a_name=first_agent,
            agent_b_name=second_agent,
            seed=int(schedule["seed"]),
            max_steps=max_steps,
            output_dir=destination.parent / ".cabt-league-transient",
            save_html=False,
            save_result=False,
        )
        winner = raw.get("winner")
        if raw.get("status") == "DONE":
            winner_agent = "draw" if winner == 2 else ("champion" if winner == champion_seat else "challenger")
        else:
            winner_agent = None
        return {
            "status": raw.get("status"),
            "winner_agent": winner_agent,
            "elapsed_seconds": raw.get("elapsed_seconds"),
            "fallback_count": 0,
        }

    result = run_actual_league(config, output_path=destination, run_match=play)
    result["actual_provenance"] = {
        "environment_loader": "kaggle_environments.make",
        "environment_name": "cabt",
        "kaggle_environments_version": package_version,
        "source": "official-cabt",
    }
    atomic_write_json(destination, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenger", choices=AGENTS, default="deterministic")
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--base-seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_official_league(
            champion_agent="rule",
            challenger_agent=args.challenger,
            games=args.games,
            base_seed=args.base_seed,
            max_steps=args.max_steps,
            deck_path=args.deck,
            output_path=args.output,
        )
    except (LeagueCapabilityError, ValueError) as exc:
        print(f"actual League failed: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["crashes"] == result["invalid_actions"] == result["timeouts"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
