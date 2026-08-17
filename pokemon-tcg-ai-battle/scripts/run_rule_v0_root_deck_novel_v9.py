#!/usr/bin/env python3
"""Smoke-gated research-only search/recursion surface for P0 Rule v0/root deck."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
from collections import Counter

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from scripts import run_rule_v0_root_deck_weighted_v1 as base
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, run_parallel_cabt_evaluation
from scripts.run_performance_first_arena_v1 import build_root_arena_games


base.SCHEMA = "meta-specialist-rule-v0-root-deck-novel-v9"
base.OUTPUT_DEFAULT = (
    base.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-novel-v9-weighted48-20260814"
)
base.WEIGHTED_BASE_SEED = 23_590_000
base.ROOT_CORE_COUNTS = {k: v for k, v in base.ROOT_CORE_COUNTS.items() if k not in {1152}}
base.SURFACES = (
    ("root-poke-pad-to-poffin", 1152, 1086),
    ("root-poke-pad-to-night-stretcher", 1152, 1097),
)

SMOKE_BASE_SEED = 23_580_000
SMOKE_OPPONENT = "tomatomato_archaludon"


def _candidate_deck(old_card: int, new_card: int) -> tuple[int, ...]:
    cards = list(parse_deck_csv_bytes(base.ROOT_DECK.read_bytes()))
    cards.remove(old_card)
    cards.append(new_card)
    return tuple(sorted(cards))


def run_smoke() -> dict[str, object]:
    """Run two real engine games per candidate outside the persistent run tree."""
    tmp = Path(tempfile.mkdtemp(prefix="rule-v0-root-deck-novel-v9-smoke-"))
    results: dict[str, object] = {}
    try:
        vocabulary = base.load_production_card_vocabulary_v1()
        for ordinal, (candidate_id, old_card, new_card) in enumerate(base.SURFACES):
            cards = _candidate_deck(old_card, new_card)
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
            deck_path = tmp / "decks" / candidate_id / "deck.csv"
            deck_path.parent.mkdir(parents=True, exist_ok=True)
            deck_path.write_text("\n".join(str(card) for card in cards) + "\n", encoding="utf-8")
            games = build_root_arena_games(
                opponent_ids=(SMOKE_OPPONENT,),
                games_per_seat=1,
                base_seed=SMOKE_BASE_SEED + ordinal * 10,
                subject_deck=deck_path,
                block_id=f"{base.SCHEMA}-smoke-{candidate_id}",
            )
            result = run_parallel_cabt_evaluation(
                games,
                output_dir=tmp / "eval" / candidate_id,
                max_workers=1,
                worker_recycle_games=16,
                overwrite=False,
            )
            rows = list(result["rows"])
            aggregate = aggregate_ledger_v1(rows)
            results[candidate_id] = {
                "old_card": old_card,
                "new_card": new_card,
                "deck_sha256": base._file_sha(deck_path),
                "deck_multiset_sha256": base.deck_multiset_identity_v1(cards),
                "requested_games": len(rows),
                "completed_games": int(aggregate["completed_games"]),
                "faults": int(aggregate["faults"]),
                "wins": int(aggregate["wins"]),
                "draws": int(aggregate["draws"]),
                "losses": int(aggregate["losses"]),
                "smoke_pass": int(aggregate["faults"]) == 0 and int(aggregate["completed_games"]) == 2,
                "evaluator_sha256": base.evaluator_implementation_sha256_v1(),
                "opponent_id": SMOKE_OPPONENT,
            }
        return {"tmp_root": str(tmp), "candidates": results, "all_smoke_pass": all(bool(row["smoke_pass"]) for row in results.values())}
    finally:
        # The persistent weighted run records the smoke result; no candidate
        # deck is left under runs/, so novelty scanning remains sound.
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_DEFAULT)
    args = parser.parse_args()
    smoke = run_smoke()
    print(json.dumps({"smoke": smoke}, ensure_ascii=False, sort_keys=True, indent=2))
    if not smoke["all_smoke_pass"]:
        raise SystemExit("smoke gate failed; weighted48 not started")
    result = base.execute(args.output.resolve())
    print(json.dumps({"weighted48": result}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
