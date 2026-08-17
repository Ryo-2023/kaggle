"""Run pre-registered Alakazam Deck-only CABT candidate screens."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import make_deterministic_agent, make_random_agent, make_rule_agent, make_rule_agent_v1, read_deck_csv
from mage_ptcg.optimization.alakazam_deck_search import deck_hash, load_mutations, load_slot_catalog, mutate_deck, validate_catalog_counts
from mage_ptcg.opponents.synthetic_stress_v1 import make_synthetic_stress_agent
from scripts.test_sim import run_match

OPPONENTS = ("random", "deterministic", "rule_v1", "setup-heavy")


def _opponent(name: str, deck: list[int], seed: int):
    if name == "random": return make_random_agent(deck=deck, seed=seed)
    if name == "deterministic": return make_deterministic_agent(deck=deck)
    if name == "rule_v1": return make_rule_agent_v1(deck=deck, seed=seed)
    if name == "setup-heavy": return make_synthetic_stress_agent(kind=name, deck=deck, seed=seed).as_agent()
    raise ValueError(name)


def _schedule(games: int) -> list[tuple[str, int]]:
    if games <= 0 or games % 8:
        raise ValueError("games must be a positive multiple of 8")
    return [(opponent, seat) for _ in range(games // 8) for opponent in OPPONENTS for seat in (0, 1)]


def _summary(rows: list[dict[str, Any]], gate: dict[str, object], stage: str = "stage1") -> dict[str, Any]:
    done = [row for row in rows if row["status"] == "DONE"]
    by_opponent = {}
    for opponent in OPPONENTS:
        group = [row for row in done if row["opponent"] == opponent]
        by_opponent[opponent] = {"games": len(group), "wins": sum(row["won"] for row in group), "win_rate": (sum(row["won"] for row in group) / len(group)) if group else None}
    by_seat = {}
    for seat in (0, 1):
        group = [row for row in done if row["seat"] == seat]
        by_seat[str(seat)] = {"games": len(group), "wins": sum(row["won"] for row in group), "win_rate": (sum(row["won"] for row in group) / len(group)) if group else None}
    rate = (sum(row["won"] for row in done) / len(done)) if done else None
    worst = min((value["win_rate"] for value in by_opponent.values() if value["win_rate"] is not None), default=None)
    passed = len(done) >= int(gate["minimum_completed"]) and len(rows) == len(done) and rate is not None and rate >= float(gate["minimum_overall_win_rate"]) and worst is not None and worst >= float(gate["minimum_worst_opponent_win_rate"])
    return {"games": len(rows), "completed": len(done), "faults": len(rows) - len(done), "wins": sum(row["won"] for row in done), "win_rate": rate, "worst_opponent_win_rate": worst, "by_opponent": by_opponent, "by_seat": by_seat, "mean_steps": (sum(row["steps"] for row in done) / len(done)) if done else None, "mean_elapsed_seconds": (sum(row["elapsed_seconds"] for row in done) / len(done)) if done else None, "status_counts": dict(Counter(row["status"] for row in rows)), f"{stage}_gate": gate, f"{stage}_status": "PASS" if passed else "FAIL", "unavailable_metrics": ["first_attack_turn", "first_evolution_turn", "hand_stall", "energy_shortage", "successor_ready: runner does not archive private or unverified fields"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "configs/alakazam/slot_catalog_v2.json")
    parser.add_argument("--registry", type=Path, default=ROOT / "configs/alakazam/flex_candidates_v2.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--games", type=int, default=32)
    parser.add_argument("--seed", type=int, default=2026072602)
    parser.add_argument("--stage", choices=("stage1", "stage2", "final"), default="stage1")
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    baseline = read_deck_csv(args.baseline); catalog = load_slot_catalog(args.catalog); catalog_counts = validate_catalog_counts(baseline, catalog); registry, mutations = load_mutations(args.registry); gate = dict(registry[f"{args.stage}_gate"])
    if args.games != int(gate["games_per_candidate"]): raise ValueError("games must match the pre-registered stage1 gate")
    selected = set(args.candidate_id)
    known = {row.candidate_id for row in mutations}
    if selected.difference(known): raise ValueError("unknown candidate ID")
    if selected: mutations = [row for row in mutations if row.candidate_id in selected]
    args.output.mkdir(parents=True, exist_ok=True)
    aggregate = []
    for number, mutation in enumerate(mutations):
        deck = mutate_deck(baseline, mutation, catalog); candidate_dir = args.output / mutation.candidate_id; candidate_dir.mkdir(parents=True, exist_ok=True)
        existing_summary = candidate_dir / "summary.json"
        if args.resume and existing_summary.exists():
            existing = json.loads(existing_summary.read_text(encoding="utf-8"))
            aggregate.append({"candidate_id": mutation.candidate_id, "deck_hash": existing["deck_hash"], "status": existing[f"{args.stage}_status"], "win_rate": existing["win_rate"], "worst_opponent_win_rate": existing["worst_opponent_win_rate"], "faults": existing["faults"]})
            continue
        matches_path = candidate_dir / "matches.json"
        if matches_path.exists() and not args.resume:
            raise FileExistsError(f"incomplete candidate output exists: {candidate_dir}")
        deck_path = candidate_dir / "deck.csv"; deck_path.write_text("\n".join(map(str, deck)) + "\n", encoding="utf-8")
        rows = json.loads(matches_path.read_text(encoding="utf-8")) if args.resume and matches_path.exists() else []
        if not isinstance(rows, list) or len(rows) > args.games:
            raise ValueError("malformed or oversized resume checkpoint")
        schedule = _schedule(args.games)
        for index, (opponent, seat) in enumerate(schedule[len(rows):], start=len(rows)):
            own_factory = lambda runtime_deck, runtime_seed: make_rule_agent(deck=runtime_deck, seed=runtime_seed)
            opponent_factory = lambda runtime_deck, runtime_seed, name=opponent: _opponent(name, runtime_deck, runtime_seed)
            if seat == 0:
                result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name="rule", agent_b_name=opponent, agent_a_factory=own_factory, agent_b_factory=opponent_factory, seed=args.seed + number * args.games + index, output_dir=candidate_dir / "transient", save_html=False, save_result=False)
            else:
                result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=opponent, agent_b_name="rule", agent_a_factory=opponent_factory, agent_b_factory=own_factory, seed=args.seed + number * args.games + index, output_dir=candidate_dir / "transient", save_html=False, save_result=False)
            rows.append({"game": index, "opponent": opponent, "seat": seat, "won": result.get("winner") == seat, "status": result.get("status"), "winner": result.get("winner"), "steps": result.get("steps"), "elapsed_seconds": result.get("elapsed_seconds")})
            matches_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = _summary(rows, gate, args.stage) | {"candidate": mutation.__dict__, "deck_hash": deck_hash(deck), "catalog_counts": catalog_counts}
        matches_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (candidate_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        aggregate.append({"candidate_id": mutation.candidate_id, "deck_hash": summary["deck_hash"], "status": summary[f"{args.stage}_status"], "win_rate": summary["win_rate"], "worst_opponent_win_rate": summary["worst_opponent_win_rate"], "faults": summary["faults"]})
    aggregate.sort(key=lambda row: (row["status"] == "PASS", row["win_rate"] if row["win_rate"] is not None else -1.0, row["worst_opponent_win_rate"] if row["worst_opponent_win_rate"] is not None else -1.0), reverse=True)
    payload = {"schema": f"alakazam-deck-search-{args.stage}-v1", "candidate_count": len(aggregate), "gate": gate, "advance": [row["candidate_id"] for row in aggregate if row["status"] == "PASS"][:int(gate["advance_limit"])], "results": aggregate}
    (args.output / f"{args.stage}_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
