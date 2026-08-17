#!/usr/bin/env python3
"""Research-only V4-checkpoint policy × deck common24 comparison.

The V4 checkpoint is loaded through the existing strict actor-pool factory;
this wrapper only supplies fresh, candidate-only deck manifests and a paired
common24 WDL evaluation.  It does not train, promote, package, or submit.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Mapping

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import deck_multiset_identity_v1
from scripts.measure_v4_checkpoint_strength import _checkpoint_provenance, _v4_subject_factory
from scripts.test_sim import run_match
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-v4-checkpoint-deck-mutation-common24-v1"
HEX = frozenset("0123456789abcdef")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> str:
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def _write_deck(path: Path, cards: list[int]) -> str:
    if len(cards) != 60:
        raise ValueError("candidate deck must have exactly 60 cards")
    raw = ("\n".join(str(card) for card in cards) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def _fresh_root(path: Path) -> None:
    if path.exists():
        if any(path.iterdir()):
            raise FileExistsError(f"refusing existing non-empty output root: {path}")
    else:
        path.mkdir(parents=True)


def _candidate_decks(parent: Path, output: Path) -> tuple[dict[str, object], ...]:
    parent_raw = parent.read_bytes()
    parent_cards = list(parse_deck_csv_bytes(parent_raw))
    vocabulary = load_production_card_vocabulary_v1()
    validate_deck(tuple(parent_cards), known_card_ids=vocabulary.recognized_card_ids)
    parent_multiset = deck_multiset_identity_v1(tuple(parent_cards))
    specs = (
        ("v4-tomato-mutant-1244-to-1246", 1244, 1246, "status-stadium"),
        ("v4-tomato-mutant-1097-to-1213", 1097, 1213, "supporter-draw"),
    )
    candidates: list[dict[str, object]] = []
    for candidate_id, removed, added, role in specs:
        cards = list(parent_cards)
        try:
            index = cards.index(removed)
        except ValueError as exc:
            raise ValueError(f"parent deck does not contain mutation card {removed}") from exc
        cards[index] = added
        validate_deck(tuple(cards), known_card_ids=vocabulary.recognized_card_ids)
        multiset = deck_multiset_identity_v1(tuple(cards))
        deck_dir = output / "candidates" / candidate_id
        deck_dir.mkdir(parents=True, exist_ok=False)
        deck_path = deck_dir / "deck.csv"
        deck_sha = _write_deck(deck_path, cards)
        candidates.append({
            "candidate_id": candidate_id,
            "role": role,
            "removed_card_id": removed,
            "added_card_id": added,
            "swap_count": 1,
            "deck_path": str(deck_path.resolve()),
            "deck_file_sha256": deck_sha,
            "deck_multiset_sha256": multiset,
            "parent_deck_file_sha256": _sha(parent),
            "parent_deck_multiset_sha256": parent_multiset,
            "research_only": True,
            "authority": {"training": False, "promotion": False, "submission": False},
        })
    return tuple(candidates)


def _run_arm(
    *, arm_id: str, deck_path: Path, checkpoint: Path, provenance: Mapping[str, str],
    opponent_ids: tuple[str, ...], pool: Mapping[str, object], output: Path,
    base_seed: int, games_per_cell: int, max_steps: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    subject_factory = _v4_subject_factory(
        checkpoint_path=checkpoint,
        file_sha256=str(provenance["file_sha256"]),
        tensor_state_sha256=str(provenance["tensor_state_sha256"]),
        subject_deck_csv=deck_path,
        subject_archetype_id="archaludon",
        trace_sinks=None,
    )
    rows: list[dict[str, object]] = []
    fault_reasons: Counter[str] = Counter()
    ordinal = 0
    arm_started = time.time()
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(deck_path))
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for repetition in range(games_per_cell):
                game_id = f"{SCHEMA}-{arm_id}-{opponent_id}-seat{seat}-g{repetition:04d}"
                seed = base_seed + ordinal
                ordinal += 1
                seed_agent_randomness_v1(seed)
                subject_first = seat == 0
                out_dir = output / "games" / arm_id / game_id
                fault_kind: str | None = None
                result: Mapping[str, object] = {}
                try:
                    result = run_match(
                        deck_a_path=str(deck_path) if subject_first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if subject_first else str(deck_path),
                        agent_a_name=f"v4-{arm_id}" if subject_first else opponent_id,
                        agent_b_name=opponent_id if subject_first else f"v4-{arm_id}",
                        seed=seed, max_steps=max_steps, output_dir=str(out_dir),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if subject_first else opponent_factory,
                        agent_b_factory=opponent_factory if subject_first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        fault_kind = "non_done"
                except Exception as exc:  # noqa: BLE001 - preserve fault denominator
                    fault_kind = type(exc).__name__
                if fault_kind is not None:
                    fault_reasons[fault_kind] += 1
                    outcome, winner, status = "fault", None, "FAULT"
                else:
                    winner = result.get("winner")
                    outcome = "draw" if winner == 2 else ("win" if winner == seat else "loss")
                    status = "DONE"
                rows.append({
                    "schema_version": SCHEMA, "game_id": game_id, "arm_id": arm_id,
                    "opponent_id": opponent_id, "seat": seat, "repetition": repetition,
                    "seed": seed, "outcome": outcome, "winner": winner, "status": status,
                    "fault_kind": fault_kind, "steps": result.get("steps") if status == "DONE" else None,
                    "terminal_reason": result.get("terminal_reason") if status == "DONE" else None,
                    "policy_checkpoint_file_sha256": provenance["file_sha256"],
                    "policy_checkpoint_tensor_sha256": provenance["tensor_state_sha256"],
                    "subject_deck_file_sha256": _sha(deck_path),
                    "authority": {"training": False, "promotion": False, "submission": False},
                    "research_only": True,
                })
                if ordinal % 8 == 0:
                    print(f"[v4-deck] arm={arm_id} completed={ordinal}/96 faults={sum(fault_reasons.values())}", flush=True)
    outcomes = Counter(row["outcome"] for row in rows)
    summary = {
        "arm_id": arm_id, "requested_games": len(rows),
        "completed_games": sum(outcomes.get(k, 0) for k in ("win", "draw", "loss")),
        "wins": outcomes.get("win", 0), "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0), "faults": outcomes.get("fault", 0),
        "score_rate": (outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)) / len(rows),
        "fault_reasons": dict(sorted(fault_reasons.items())),
        "elapsed_seconds": round(time.time() - arm_started, 3),
        "seat": {
            str(seat): {
                "wins": sum(r["outcome"] == "win" for r in rows if r["seat"] == seat),
                "draws": sum(r["outcome"] == "draw" for r in rows if r["seat"] == seat),
                "losses": sum(r["outcome"] == "loss" for r in rows if r["seat"] == seat),
                "faults": sum(r["outcome"] == "fault" for r in rows if r["seat"] == seat),
            } for seat in (0, 1)
        },
    }
    return rows, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-deck", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=18100000)
    parser.add_argument("--games-per-cell", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=2000)
    args = parser.parse_args(argv)
    if args.games_per_cell != 2:
        raise ValueError("common24 protocol requires exactly 2 repetitions per seat")
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"output must be a fresh root: {args.output}")
    for path in (args.parent_deck, args.checkpoint, args.config):
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output.mkdir(parents=True, exist_ok=True)
    provenance = _checkpoint_provenance(args.checkpoint)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    opponent_ids = tuple(config["opponent_ids"])
    if len(opponent_ids) != 24 or len(set(opponent_ids)) != 24:
        raise ValueError("config must contain exactly 24 unique common24 opponent IDs")
    if config.get("local_eval_only") is not True or config.get("promotion_authority") is not False:
        raise ValueError("common24 config permission boundary is invalid")
    pool_path = ROOT / str(config["pool_manifest_path"])
    if _sha(pool_path) != config["pool_manifest_sha256"]:
        raise ValueError("pool manifest SHA mismatch")
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    candidates = _candidate_decks(args.parent_deck, args.output)
    parent_multiset = deck_multiset_identity_v1(tuple(parse_deck_csv_bytes(args.parent_deck.read_bytes())))
    manifest = {
        "schema_version": SCHEMA, "purpose": "V4 seed1 fixed policy deck mutation screen",
        "parent_deck_path": str(args.parent_deck.resolve()), "parent_deck_file_sha256": _sha(args.parent_deck),
        "parent_deck_multiset_sha256": parent_multiset, "checkpoint": dict(provenance),
        "config_path": str(args.config.resolve()), "config_sha256": _sha(args.config),
        "pool_manifest_path": str(pool_path.resolve()), "pool_manifest_sha256": _sha(pool_path),
        "common24_ids": list(opponent_ids), "games_per_cell": args.games_per_cell,
        "base_seed": args.base_seed, "seed_schedule": "arm-local ordinal, exact same 96 slots",
        "candidates": list(candidates), "research_only": True,
        "authority": {"training": False, "promotion": False, "submission": False},
        "existing_artifacts_modified": False, "production_modified": False,
    }
    manifest_sha = _atomic_json(args.output / "candidate_manifest.json", manifest)
    print(f"[v4-deck] manifest={manifest_sha}", flush=True)
    arms = [{"arm_id": "parent", "deck_path": args.parent_deck}] + [
        {"arm_id": str(c["candidate_id"]), "deck_path": Path(str(c["deck_path"]))} for c in candidates
    ]
    all_rows: list[dict[str, object]] = []
    arm_summaries: list[dict[str, object]] = []
    for arm in arms:
        rows, summary = _run_arm(
            arm_id=str(arm["arm_id"]), deck_path=Path(arm["deck_path"]), checkpoint=args.checkpoint,
            provenance=provenance, opponent_ids=opponent_ids, pool=pool, output=args.output,
            base_seed=args.base_seed, games_per_cell=args.games_per_cell, max_steps=args.max_steps,
        )
        arm_summaries.append(summary)
        all_rows.extend(rows)
    rows_raw = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in all_rows)
    ledger_path = args.output / "ledger.jsonl"
    fd, tmp = tempfile.mkstemp(prefix=f".{ledger_path.name}.tmp-", dir=ledger_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(rows_raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, ledger_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True); raise
    summary = {
        "schema_version": SCHEMA, "manifest_sha256": manifest_sha, "ledger_sha256": _sha(ledger_path),
        "checkpoint": dict(provenance), "base_seed": args.base_seed, "games_per_cell": args.games_per_cell,
        "arm_summaries": arm_summaries, "research_only": True,
        "authority": {"training": False, "promotion": False, "submission": False},
        "longrun_allowed": False, "existing_artifacts_modified": False, "production_modified": False,
    }
    summary_sha = _atomic_json(args.output / "summary.json", summary)
    print(json.dumps({**summary, "summary_sha256": summary_sha}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

