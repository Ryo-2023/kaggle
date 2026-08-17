"""Evaluate two existing cabt agents across multiple independent matches."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from main import DeckValidationError, read_deck_csv  # noqa: E402
from scripts.test_sim import (  # noqa: E402
    MatchDependencyError,
    _load_make,
    load_known_card_ids,
    run_match,
)


AGENT_NAMES = ("random", "deterministic", "rule", "rule_v1")
SAVE_HTML_POLICIES = ("none", "failures", "all")
TERMINAL_STATUSES = (
    "DONE",
    "STEP_LIMIT",
    "AGENT_INVALID",
    "AGENT_ERROR",
    "AGENT_TIMEOUT",
    "INCOMPLETE",
)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _require_positive(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _numeric_summary(values: Iterable[object], *, include_total: bool = False) -> dict[str, float | int | None]:
    numbers = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numbers:
        summary: dict[str, float | int | None] = {
            "count": 0,
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
        }
    else:
        summary = {
            "count": len(numbers),
            "mean": statistics.mean(numbers),
            "median": statistics.median(numbers),
            "minimum": min(numbers),
            "maximum": max(numbers),
        }
    if include_total:
        summary["total"] = sum(numbers) if numbers else 0.0
    return summary


def _prepare_output_dir(output_dir: Path, *, overwrite: bool) -> None:
    targets = (
        output_dir / "matches.jsonl",
        output_dir / "summary.json",
        output_dir / "summary.csv",
        output_dir / "html",
    )
    existing = [path for path in targets if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"batch output already exists; use --overwrite: {names}")
    if overwrite:
        for path in existing:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)


def _preflight(
    *,
    deck_a_path: Path,
    deck_b_path: Path,
    agent_a_name: str,
    agent_b_name: str,
    num_matches: int,
    max_steps: int,
    save_html: str,
) -> Callable[..., Any]:
    _require_positive("num_matches", num_matches)
    _require_positive("max_steps", max_steps)
    if agent_a_name not in AGENT_NAMES or agent_b_name not in AGENT_NAMES:
        raise ValueError(f"agents must be one of: {', '.join(AGENT_NAMES)}")
    if save_html not in SAVE_HTML_POLICIES:
        raise ValueError(f"save_html must be one of: {', '.join(SAVE_HTML_POLICIES)}")

    known_ids_path = REPOSITORY_ROOT / "data" / "raw" / "EN_Card_Data.csv"
    known_ids = load_known_card_ids(known_ids_path) if known_ids_path.is_file() else None
    read_deck_csv(deck_a_path, known_card_ids=known_ids)
    read_deck_csv(deck_b_path, known_card_ids=known_ids)
    return _load_make()


def _batch_error_result(*, seed: int, max_steps: int, exc: Exception) -> dict[str, Any]:
    return {
        "seed": seed,
        "agent_seed": seed,
        "engine_seed_supported": False,
        "max_steps": max_steps,
        "status": "ERROR",
        "winner": None,
        "terminal_reason": f"{type(exc).__name__}: {exc}",
        "steps": None,
        "cabt_turn": None,
        "elapsed_seconds": None,
        "agent_status": None,
        "rewards": None,
        "result_html": None,
        "result_json": None,
    }


def _batch_record(
    raw_result: Mapping[str, Any],
    *,
    match_index: int,
    match_seed: int,
    player_0_agent: str,
    player_1_agent: str,
    agent_a_player_index: int,
) -> dict[str, Any]:
    record = dict(raw_result)
    status = record.get("status")
    raw_winner = record.get("winner") if status == "DONE" else None
    winner_player_index = raw_winner if raw_winner in (0, 1, 2) else None
    if winner_player_index == 2:
        winner_agent = "draw"
    elif winner_player_index is None:
        winner_agent = None
    elif winner_player_index == agent_a_player_index:
        winner_agent = "agent_a"
    else:
        winner_agent = "agent_b"

    record.update(
        {
            "match_index": match_index,
            "match_seed": match_seed,
            "player_0_agent_seed": match_seed,
            "player_1_agent_seed": match_seed + 1,
            "agent_a_seed": match_seed if agent_a_player_index == 0 else match_seed + 1,
            "agent_b_seed": match_seed + 1 if agent_a_player_index == 0 else match_seed,
            "player_0_agent": player_0_agent,
            "player_1_agent": player_1_agent,
            "agent_a_player_index": agent_a_player_index,
            "agent_b_player_index": 1 - agent_a_player_index,
            "winner_player_index": winner_player_index,
            "winner_agent": winner_agent,
        }
    )
    return record


def _seat_summary(records: list[Mapping[str, Any]], *, agent: str) -> dict[str, dict[str, float | int | None]]:
    result: dict[str, dict[str, float | int | None]] = {}
    for player_index in (0, 1):
        selected = [record for record in records if record.get(f"{agent}_player_index") == player_index]
        completed = [record for record in selected if record.get("status") == "DONE"]
        wins = sum(record.get("winner_agent") == agent for record in completed)
        result[f"player_{player_index}"] = {
            "matches": len(selected),
            "completed_matches": len(completed),
            "wins": wins,
            "win_rate_all": _rate(wins, len(completed)),
        }
    return result


def build_summary(records: list[Mapping[str, Any]], *, requested_matches: int) -> dict[str, Any]:
    attempted = len(records)
    completed_records = [record for record in records if record.get("status") == "DONE"]
    agent_a_wins = sum(record.get("winner_agent") == "agent_a" for record in completed_records)
    agent_b_wins = sum(record.get("winner_agent") == "agent_b" for record in completed_records)
    draws = sum(record.get("winner_agent") == "draw" for record in completed_records)
    decisive = agent_a_wins + agent_b_wins

    status_counts = Counter(str(record.get("status", "ERROR")) for record in records)
    status_distribution = {status: status_counts.get(status, 0) for status in TERMINAL_STATUSES}
    status_distribution["OTHER_ERROR"] = sum(
        count for status, count in status_counts.items() if status not in TERMINAL_STATUSES
    )
    reason_distribution = Counter(
        record.get("terminal_reason") if isinstance(record.get("terminal_reason"), str) else "null"
        for record in records
    )

    player_seats: dict[str, dict[str, float | int | None]] = {}
    for player_index in (0, 1):
        selected = list(records)
        completed = [record for record in selected if record.get("status") == "DONE"]
        wins = sum(record.get("winner_player_index") == player_index for record in completed)
        player_seats[f"player_{player_index}"] = {
            "matches": len(selected),
            "completed_matches": len(completed),
            "wins": wins,
            "win_rate_all": _rate(wins, len(completed)),
        }

    return {
        "requested_matches": requested_matches,
        "attempted_matches": attempted,
        "completed_matches": len(completed_records),
        "failed_matches": attempted - len(completed_records),
        "completion_rate": _rate(len(completed_records), attempted),
        "completion_rate_denominator": attempted,
        "agent_a_wins": agent_a_wins,
        "agent_b_wins": agent_b_wins,
        "draws": draws,
        "agent_a_win_rate_all": _rate(agent_a_wins, len(completed_records)),
        "agent_b_win_rate_all": _rate(agent_b_wins, len(completed_records)),
        "draw_rate_all": _rate(draws, len(completed_records)),
        "decisive_matches": decisive,
        "agent_a_decisive_win_rate": _rate(agent_a_wins, decisive),
        "agent_b_decisive_win_rate": _rate(agent_b_wins, decisive),
        "seat_statistics": {
            "agent_a": _seat_summary(records, agent="agent_a"),
            "agent_b": _seat_summary(records, agent="agent_b"),
            "players": player_seats,
        },
        "match_length": {
            "steps": _numeric_summary(record.get("steps") for record in completed_records),
            "cabt_turn": _numeric_summary(record.get("cabt_turn") for record in completed_records),
            "elapsed_seconds": _numeric_summary(
                (record.get("elapsed_seconds") for record in completed_records), include_total=True
            ),
        },
        "status_distribution": status_distribution,
        "terminal_reason_distribution": dict(sorted(reason_distribution.items())),
    }


def _write_summary(output_dir: Path, summary: Mapping[str, Any]) -> None:
    (output_dir / "summary.json").write_text(
        json.dumps(dict(summary), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lengths = summary["match_length"]
    row = {
        "num_matches": summary["requested_matches"],
        "attempted_matches": summary["attempted_matches"],
        "completed_matches": summary["completed_matches"],
        "completion_rate": summary["completion_rate"],
        "agent_a_wins": summary["agent_a_wins"],
        "agent_b_wins": summary["agent_b_wins"],
        "draws": summary["draws"],
        "agent_a_win_rate_all": summary["agent_a_win_rate_all"],
        "agent_b_win_rate_all": summary["agent_b_win_rate_all"],
        "mean_steps": lengths["steps"]["mean"],
        "median_steps": lengths["steps"]["median"],
        "total_elapsed_seconds": lengths["elapsed_seconds"]["total"],
    }
    with (output_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def run_batch_evaluation(
    *,
    deck_a_path: str | Path,
    deck_b_path: str | Path,
    agent_a_name: str,
    agent_b_name: str,
    num_matches: int,
    base_seed: int,
    max_steps: int,
    output_dir: str | Path,
    alternate_seats: bool = True,
    save_html: str = "failures",
    overwrite: bool = False,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run sequential cabt matches and persist JSONL plus aggregate summaries."""
    deck_a_file = Path(deck_a_path)
    deck_b_file = Path(deck_b_path)
    destination = Path(output_dir)
    make_environment = _preflight(
        deck_a_path=deck_a_file,
        deck_b_path=deck_b_file,
        agent_a_name=agent_a_name,
        agent_b_name=agent_b_name,
        num_matches=num_matches,
        max_steps=max_steps,
        save_html=save_html,
    )
    _prepare_output_dir(destination, overwrite=overwrite)
    html_setting: bool | str = {"none": False, "failures": "failures", "all": True}[save_html]
    records: list[dict[str, Any]] = []
    jsonl_path = destination / "matches.jsonl"

    with jsonl_path.open("x", encoding="utf-8") as handle:
        try:
            for match_index in range(num_matches):
                match_seed = base_seed + match_index
                agent_a_player_index = match_index % 2 if alternate_seats else 0
                if agent_a_player_index == 0:
                    player_0_agent, player_1_agent = agent_a_name, agent_b_name
                    player_0_deck, player_1_deck = deck_a_file, deck_b_file
                else:
                    player_0_agent, player_1_agent = agent_b_name, agent_a_name
                    player_0_deck, player_1_deck = deck_b_file, deck_a_file

                try:
                    raw_result = run_match(
                        deck_a_path=player_0_deck,
                        deck_b_path=player_1_deck,
                        agent_a_name=player_0_agent,
                        agent_b_name=player_1_agent,
                        seed=match_seed,
                        max_steps=max_steps,
                        output_dir=destination / "html" / f"match-{match_index:04d}",
                        save_html=html_setting,
                        save_result=False,
                        make_environment=make_environment,
                    )
                except KeyboardInterrupt:
                    handle.flush()
                    raise
                except Exception as exc:
                    raw_result = _batch_error_result(seed=match_seed, max_steps=max_steps, exc=exc)

                record = _batch_record(
                    raw_result,
                    match_index=match_index,
                    match_seed=match_seed,
                    player_0_agent=player_0_agent,
                    player_1_agent=player_1_agent,
                    agent_a_player_index=agent_a_player_index,
                )
                records.append(record)
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                if progress is not None:
                    progress(record)
        except KeyboardInterrupt:
            handle.flush()
            raise

    summary = build_summary(records, requested_matches=num_matches)
    _write_summary(destination, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-a", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--deck-b", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--agent-a", choices=AGENT_NAMES, default="random")
    parser.add_argument("--agent-b", choices=AGENT_NAMES, default="deterministic")
    parser.add_argument("--num-matches", type=_positive_int, required=True)
    parser.add_argument("--base-seed", type=int, default=1000)
    parser.add_argument("--max-steps", type=_positive_int, default=10000)
    parser.add_argument("--output-dir", type=Path, required=True)
    seat_group = parser.add_mutually_exclusive_group()
    seat_group.add_argument("--alternate-seats", dest="alternate_seats", action="store_true", default=True)
    seat_group.add_argument("--no-alternate-seats", dest="alternate_seats", action="store_false")
    parser.add_argument("--save-html", choices=SAVE_HTML_POLICIES, default="failures")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _print_progress(record: Mapping[str, Any], *, total: int) -> None:
    print(
        f"[{record['match_index'] + 1}/{total}] status={record['status']} "
        f"winner={record['winner_agent']} steps={record['steps']} "
        f"elapsed={record['elapsed_seconds']}s"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_batch_evaluation(
            deck_a_path=args.deck_a,
            deck_b_path=args.deck_b,
            agent_a_name=args.agent_a,
            agent_b_name=args.agent_b,
            num_matches=args.num_matches,
            base_seed=args.base_seed,
            max_steps=args.max_steps,
            output_dir=args.output_dir,
            alternate_seats=args.alternate_seats,
            save_html=args.save_html,
            overwrite=args.overwrite,
            progress=lambda record: _print_progress(record, total=args.num_matches),
        )
    except (DeckValidationError, MatchDependencyError, FileExistsError, OSError, ValueError) as exc:
        print(f"Batch evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
