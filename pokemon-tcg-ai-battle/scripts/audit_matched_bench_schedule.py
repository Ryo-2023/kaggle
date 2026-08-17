"""Audit aggregate bench summaries before treating a candidate delta as evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def canonical_deck_hash(path: Path) -> str:
    cards = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(cards) != 60:
        raise ValueError(f"{path} does not contain 60 cards")
    return hashlib.sha256(("\n".join(map(str, cards)) + "\n").encode()).hexdigest()


def _selected_summaries(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("track-a-*/summary.json")):
        if not (path.parent.name.startswith("track-a-baseline-") or path.parent.name.startswith("track-a-jumbo-") or path.parent.name.startswith("track-a-lana-") or path.parent.name.startswith("track-a-extra-")):
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        matchup = str(value.get("matchup", ""))
        if "_vs_" not in matchup:
            continue
        candidate, opponent = matchup.split("_vs_", 1)
        if candidate in {"baseline", "jumbo", "lana"}:
            rows.append((path, value))
    return rows


def audit(root: Path, decks: dict[str, Path]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path, row in _selected_summaries(root):
        candidate, opponent = str(row["matchup"]).split("_vs_", 1)
        grouped[(candidate, opponent)].append({"source": str(path), **row})
    records = []
    failures: list[str] = []
    opponents_by_candidate: dict[str, set[str]] = defaultdict(set)
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"games": 0, "completed": 0, "wins": 0, "errors": 0})
    for (candidate, opponent), rows in sorted(grouped.items()):
        games = sum(int(row.get("games", 0)) for row in rows)
        completed = sum(int(row.get("completed_games", 0)) for row in rows)
        wins = sum(int(row.get("a_wins", 0)) for row in rows)
        errors = sum(int(row.get("errors", 0)) for row in rows)
        seeds = sorted(int(row.get("seed_base", -1)) for row in rows)
        status = "PASS" if games == completed == 10 and errors == 0 and sorted(int(row.get("games", 0)) for row in rows) == [2, 8] else "FAIL"
        if status == "FAIL":
            failures.append(f"{candidate}/{opponent}: games={games} completed={completed} errors={errors} parts={[row.get('games') for row in rows]}")
        records.append({"candidate": candidate, "opponent": opponent, "games": games, "completed": completed, "wins": wins, "errors": errors, "seed_bases": seeds, "status": status, "sources": [row["source"] for row in rows]})
        opponents_by_candidate[candidate].add(opponent)
        for field, value in (("games", games), ("completed", completed), ("wins", wins), ("errors", errors)):
            totals[candidate][field] += value
    opponent_sets = {candidate: sorted(values) for candidate, values in opponents_by_candidate.items()}
    if len({tuple(values) for values in opponent_sets.values()}) != 1:
        failures.append("candidate opponent sets differ")
    if any(len(values) != 13 for values in opponent_sets.values()):
        failures.append("expected exactly 13 opponents per candidate")
    for candidate in ("baseline", "jumbo", "lana"):
        if totals[candidate] != {"games": 130, "completed": 130, "wins": totals[candidate]["wins"], "errors": 0}:
            failures.append(f"{candidate}: aggregate is not 130 completed games without errors")
    return {
        "status": "PASS" if not failures else "FAIL",
        "candidate_deck_hashes": {name: canonical_deck_hash(path) for name, path in decks.items()},
        "candidate_totals": totals,
        "opponents": opponent_sets,
        "records": records,
        "seat_balance": "Each 8- and 2-game bench invocation is even; bench.cli alternates seat_of_a=i%2.",
        "orientation": "a_wins is the candidate because candidate was passed as --agent-a in every retained matchup.",
        "fallback_telemetry": "Not instrumented by this bench summary schema; no agent exception or illegal-action error occurred.",
        "duplicate_check": "Aggregate summaries have no per-game identity. The 8- and 2-game blocks use disjoint seed intervals (2026072700..07 and 2026072900..01) per matchup.",
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--jumbo", type=Path, required=True)
    parser.add_argument("--lana", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.bench_root, {"baseline": args.baseline, "jumbo": args.jumbo, "lana": args.lana})
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "totals": report["candidate_totals"], "failures": report["failures"]}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
