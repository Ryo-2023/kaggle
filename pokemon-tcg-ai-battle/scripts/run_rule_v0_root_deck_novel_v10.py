#!/usr/bin/env python3
"""Smoke-gated research-only Dusk Ball surface for the P0 Rule v0/root deck.

Only the two newly selected mutations are allowed here.  A real-engine smoke
must pass before the parent and candidates are sent to the sealed weighted48
runner.  This wrapper does not grant training, promotion, submission, or
long-run authority.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
import shutil
import tempfile
from pathlib import Path

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from scripts import run_rule_v0_root_deck_weighted_v1 as base
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, run_parallel_cabt_evaluation
from scripts.run_performance_first_arena_v1 import build_root_arena_games


base.SCHEMA = "meta-specialist-rule-v0-root-deck-dusk-v10"
base.OUTPUT_DEFAULT = (
    base.ROOT
    / "runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-weighted48-20260814"
)
WEIGHTED_BASE_SEED = 23_610_000
base.WEIGHTED_BASE_SEED = WEIGHTED_BASE_SEED
SURFACES = (
    ("root-dusk-to-bloodmoon-ursaluna", 1102, 135),
    ("root-dusk-to-hilda", 1102, 1225),
)
base.SURFACES = SURFACES
SMOKE_BASE_SEED = 23_600_000
SMOKE_OPPONENT = "tomatomato_archaludon"
EXPECTED_CANDIDATE_IDS = frozenset(candidate_id for candidate_id, _old, _new in SURFACES)


def smoke_passes(summary: Mapping[str, object]) -> bool:
    return int(summary.get("completed_games", -1)) == 2 and int(summary.get("faults", -1)) == 0


def validate_retry_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    if manifest.get("schema_version") != base.SCHEMA:
        raise ValueError("retry manifest schema mismatch")
    if manifest.get("authority") != base.AUTHORITY_FALSE:
        raise ValueError("retry manifest authority mismatch")
    rows = manifest.get("candidates")
    if not isinstance(rows, list) or {row.get("candidate_id") for row in rows if isinstance(row, dict)} != EXPECTED_CANDIDATE_IDS:
        raise ValueError("retry manifest candidate pair mismatch")
    expected_mutations = {candidate_id: f"{old}->{new}" for candidate_id, old, new in SURFACES}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("retry manifest candidate row malformed")
        candidate_id = row.get("candidate_id")
        if row.get("mutation") != expected_mutations.get(candidate_id):
            raise ValueError(f"retry manifest mutation mismatch: {candidate_id}")
        deck_path = Path(str(row.get("deck_path", ""))).resolve()
        if not deck_path.is_file() or base._file_sha(deck_path) != row.get("deck_file_sha256"):
            raise ValueError(f"retry manifest deck bytes mismatch: {candidate_id}")
        cards = tuple(parse_deck_csv_bytes(deck_path.read_bytes()))
        if base.deck_multiset_identity_v1(cards) != row.get("deck_multiset_sha256"):
            raise ValueError(f"retry manifest deck identity mismatch: {candidate_id}")
    if manifest.get("performance_run_started") is not False:
        raise ValueError("retry manifest is not a pre-run seal")
    return dict(manifest)


def run_smoke() -> tuple[dict[str, object], ...]:
    """Run two real engine games per candidate in a disposable temp root."""
    temp_root = Path(tempfile.mkdtemp(prefix="rule-v0-root-deck-dusk-v10-smoke-"))
    rows: list[dict[str, object]] = []
    try:
        vocabulary = base.load_production_card_vocabulary_v1()
        parent = list(parse_deck_csv_bytes(base.ROOT_DECK.read_bytes()))
        for ordinal, (candidate_id, old_card, new_card) in enumerate(SURFACES):
            cards = list(parent)
            cards.remove(old_card)
            cards.append(new_card)
            cards = tuple(sorted(cards))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
            deck_path = temp_root / "decks" / candidate_id / "deck.csv"
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
                output_dir=temp_root / "eval" / candidate_id,
                max_workers=1,
                worker_recycle_games=16,
                overwrite=False,
            )
            aggregate = aggregate_ledger_v1(result["rows"])
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "old_card": old_card,
                    "new_card": new_card,
                    "opponent_id": SMOKE_OPPONENT,
                    "requested_games": len(games),
                    "completed_games": int(aggregate["completed_games"]),
                    "faults": int(aggregate["faults"]),
                    "wins": int(aggregate["wins"]),
                    "draws": int(aggregate["draws"]),
                    "losses": int(aggregate["losses"]),
                    "deck_sha256": base._file_sha(deck_path),
                    "deck_multiset_sha256": base.deck_multiset_identity_v1(cards),
                    "smoke_pass": smoke_passes(aggregate),
                }
            )
        return tuple(rows)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=base.OUTPUT_DEFAULT)
    parser.add_argument("--run-sealed-manifest", type=Path, default=None)
    args = parser.parse_args()
    smoke_rows = run_smoke()
    if len(smoke_rows) != len(SURFACES) or not all(row["smoke_pass"] for row in smoke_rows):
        print(json.dumps({"smoke_gate": "FAIL", "smoke": smoke_rows}, ensure_ascii=False, sort_keys=True, indent=2))
        raise SystemExit("runtime smoke gate failed; weighted48 not started")
    output = args.output.resolve()
    print(json.dumps({"smoke_gate": "PASS", "smoke": smoke_rows}, ensure_ascii=False, sort_keys=True, indent=2))
    if args.run_sealed_manifest is None:
        result = base.execute(output)
    else:
        sealed = validate_retry_manifest(json.loads(args.run_sealed_manifest.read_text(encoding="utf-8")))
        base._fresh_root(output)
        base._write_no_clobber(output / "candidate_manifest.json", sealed)
        result = base._execute_prepared(output, sealed)
    smoke_payload = {
        "schema_version": f"{base.SCHEMA}-runtime-smoke",
        "purpose": "candidate_runtime_smoke_precedes_weighted48",
        "smoke": list(smoke_rows),
        "performance_score_allowed": False,
        "authority": dict(base.AUTHORITY_FALSE),
        "weighted_output_root": str(output),
    }
    smoke_sha = base._write_no_clobber(output / "runtime_smoke.json", smoke_payload)
    print(json.dumps({"weighted48": result, "runtime_smoke_sha256": smoke_sha}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
