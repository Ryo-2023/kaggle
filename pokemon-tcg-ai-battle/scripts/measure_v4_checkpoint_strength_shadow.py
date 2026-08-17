#!/usr/bin/env python3
"""Measure a V4 checkpoint against an explicitly frozen shadow opponent pool.

This runner intentionally does not alter the fixed-six evaluator.  The shadow
manifest is hash-anchored and every opponent's deck/policy/source identity is
rechecked before the first match.  Results are diagnostic only; no promotion or
submission decision is made here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1  # noqa: E402
from scripts.measure_opponent_strength import _wilson  # noqa: E402
from scripts.measure_v4_checkpoint_strength import (  # noqa: E402
    _checkpoint_provenance,
    _new_row,
    _score,
    _v4_subject_factory,
)
from scripts.test_sim import run_match  # noqa: E402


SHADOW_SCHEMA_V1 = "meta-specialist-v4-shadow-checkpoint-strength-v1"
SHADOW_POOL_SCHEMAS = frozenset({
    "meta-specialist-v4-shadow-pool-v1",
    "meta-specialist-v4-shadow-pool-v2",
})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _resolve_manifest_path(value: object, *, manifest_path: Path, field: str) -> Path:
    if type(value) is not str or not value:
        raise ValueError(f"shadow manifest {field} path is invalid")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = _ROOT / candidate
    return candidate.resolve()


def _load_shadow_pool_manifest(
    manifest_path: Path, expected_sha256: str, *, pool: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], ...]:
    """Read, hash-check, and resolve the exact frozen shadow opponent cohort."""
    expected = _require_sha(expected_sha256, field="shadow_manifest_sha256")
    raw = manifest_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError("shadow manifest bytes do not match the supplied SHA-256")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("shadow manifest is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in SHADOW_POOL_SCHEMAS:
        raise ValueError("shadow manifest schema is invalid")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("shadow manifest has no candidates")
    ids: set[str] = set()
    decks: set[str] = set()
    policies: set[str] = set()
    resolved: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("shadow manifest candidate is not an object")
        opponent_id = candidate.get("id")
        if type(opponent_id) is not str or not opponent_id or opponent_id in ids:
            raise ValueError("shadow manifest candidate ID is missing or duplicated")
        ids.add(opponent_id)
        deck_path = _resolve_manifest_path(candidate.get("deck_path"), manifest_path=manifest_path, field="deck")
        policy_path = _resolve_manifest_path(candidate.get("policy_path"), manifest_path=manifest_path, field="policy")
        source_path = _resolve_manifest_path(candidate.get("source_metadata_path"), manifest_path=manifest_path, field="source")
        for path, field in ((deck_path, "deck"), (policy_path, "policy"), (source_path, "source")):
            if not path.is_file():
                raise ValueError(f"shadow manifest {field} asset is missing: {path}")
        deck_sha = _require_sha(candidate.get("deck_file_sha256"), field=f"{opponent_id}.deck_file_sha256")
        policy_sha = _require_sha(candidate.get("policy_sha256"), field=f"{opponent_id}.policy_sha256")
        source_sha = _require_sha(candidate.get("source_metadata_sha256"), field=f"{opponent_id}.source_metadata_sha256")
        canonical_sha = _require_sha(candidate.get("canonical_deck_sha256"), field=f"{opponent_id}.canonical_deck_sha256")
        if _sha256(deck_path) != deck_sha or _sha256(policy_path) != policy_sha or _sha256(source_path) != source_sha:
            raise ValueError(f"shadow manifest asset hash mismatch for {opponent_id}")
        if canonical_sha in decks or policy_sha in policies:
            raise ValueError("shadow manifest contains duplicate deck or policy identity")
        decks.add(canonical_sha)
        policies.add(policy_sha)
        resolved.append({
            "opponent_id": opponent_id,
            "deck_path": deck_path,
            "policy_path": policy_path,
            "source_metadata_path": source_path,
            "deck_file_sha256": deck_sha,
            "policy_sha256": policy_sha,
            "source_metadata_sha256": source_sha,
            "canonical_deck_sha256": canonical_sha,
        })
    if pool is not None:
        for item in resolved:
            opponent = resolve_opponent_v1(pool, str(item["opponent_id"]), subject_deck_csv_path="x")
            if opponent.canonical_deck_hash != item["canonical_deck_sha256"]:
                raise ValueError(f"shadow pool canonical deck identity mismatch for {item['opponent_id']}")
            if opponent.policy_hash != item["policy_sha256"]:
                raise ValueError(f"shadow pool policy identity mismatch for {item['opponent_id']}")
            if Path(opponent.deck_csv_path).resolve() != item["deck_path"]:
                raise ValueError(f"shadow pool deck path mismatch for {item['opponent_id']}")
            if Path(opponent.policy_path).resolve() != item["policy_path"]:
                raise ValueError(f"shadow pool policy path mismatch for {item['opponent_id']}")
    return tuple(resolved)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--shadow-manifest-sha256", required=True)
    parser.add_argument("--games-per-seat", type=int, default=4)
    parser.add_argument("--base-seed", type=int, default=10_100_000)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress-path", type=Path)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.games_per_seat <= 0:
        raise ValueError("--games-per-seat must be positive")
    if args.base_seed < 0:
        raise ValueError("--base-seed must be nonnegative")
    if args.max_steps <= 0:
        raise ValueError("--max-steps must be positive")
    if not args.checkpoint.is_file():
        raise ValueError(f"checkpoint does not exist: {args.checkpoint}")
    if not args.subject_deck_csv.is_file():
        raise ValueError(f"subject deck does not exist: {args.subject_deck_csv}")
    if not args.shadow_manifest.is_file():
        raise ValueError(f"shadow manifest does not exist: {args.shadow_manifest}")


def evaluation_implementation_sha256_v1() -> str:
    digest = hashlib.sha256(b"meta-specialist-v4-shadow-evaluator-v1\0")
    for path in (
        Path(__file__),
        Path(__file__).with_name("measure_v4_checkpoint_strength.py"),
        _ROOT / "src/mage_ptcg/meta_specialist/opponent_pool_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actor_pool_v1.py",
        _ROOT / "scripts/test_sim.py",
    ):
        raw = path.read_bytes()
        digest.update(str(path.relative_to(_ROOT)).encode("utf-8") + b"\0" + len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    provenance = _checkpoint_provenance(args.checkpoint)
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    candidates = _load_shadow_pool_manifest(args.shadow_manifest, args.shadow_manifest_sha256, pool=pool)
    opponent_ids = tuple(str(item["opponent_id"]) for item in candidates)
    subject_factory = _v4_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        tensor_state_sha256=provenance["tensor_state_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
    )
    requested_games = len(candidates) * 2 * args.games_per_seat
    reporter = ProgressReporterV1(
        total=requested_games, desc=f"v4-shadow {provenance['file_sha256'][:12]}",
        progress_path=args.progress_path,
    )
    reporter.note(f"[v4-shadow] checkpoint={provenance['file_sha256'][:12]} opponents={len(candidates)} games={requested_games}")
    overall = _new_row()
    per_seat = {seat: _new_row() for seat in (0, 1)}
    per_opponent = {opponent_id: _new_row() for opponent_id in opponent_ids}
    per_opponent_seat = {opponent_id: {seat: _new_row() for seat in (0, 1)} for opponent_id in opponent_ids}
    fault_reasons: dict[str, int] = {}
    started = time.time()
    match_root = args.output.parent / f"{args.output.stem}-matches"
    for item in candidates:
        opponent_id = str(item["opponent_id"])
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for game_index in range(args.games_per_seat):
                seed = args.base_seed + game_index
                subject_first = seat == 0
                rows = (overall, per_seat[seat], per_opponent[opponent_id], per_opponent_seat[opponent_id][seat])
                for row in rows:
                    row["requested"] += 1
                seed_agent_randomness_v1(seed)
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv) if subject_first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if subject_first else str(args.subject_deck_csv),
                        agent_a_name="a", agent_b_name="b", seed=seed, max_steps=args.max_steps,
                        output_dir=str(match_root / f"{opponent_id}-{seat}-{game_index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if subject_first else opponent_factory,
                        agent_b_factory=opponent_factory if subject_first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    fault_reasons[reason] = fault_reasons.get(reason, 0) + 1
                    for row in rows:
                        row["f"] += 1
                    reporter.update(1, faults=overall["f"], rate=_score(overall) or 0.0)
                    continue
                winner = result.get("winner")
                key = "d" if winner == 2 else ("w" if winner == seat else "l")
                for row in rows:
                    row[key] += 1
                reporter.update(1, win=overall["w"], loss=overall["l"], draw=overall["d"], faults=overall["f"], rate=_score(overall) or 0.0)
    reporter.close()
    fingerprints = [
        {
            "opponent_id": str(item["opponent_id"]),
            "canonical_deck_sha256": str(item["canonical_deck_sha256"]),
            "deck_file_sha256": str(item["deck_file_sha256"]),
            "policy_sha256": str(item["policy_sha256"]),
            "source_metadata_sha256": str(item["source_metadata_sha256"]),
        }
        for item in candidates
    ]
    payload: dict[str, Any] = {
        "schema_version": SHADOW_SCHEMA_V1,
        "comparison_status": "invalid_faults" if overall["f"] else "valid",
        "checkpoint": provenance,
        "subject_archetype_id": args.subject_archetype_id,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_file_sha256": _sha256(args.subject_deck_csv),
        "shadow_manifest": str(args.shadow_manifest.resolve()),
        "shadow_manifest_file_sha256": _sha256(args.shadow_manifest),
        "opponent_ids": list(opponent_ids),
        "opponent_fingerprints": fingerprints,
        "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "evaluation_implementation_sha256": evaluation_implementation_sha256_v1(),
        "games_per_seat": args.games_per_seat,
        "base_seed": args.base_seed,
        "max_steps": args.max_steps,
        "requested_games": overall["requested"],
        "games_played": overall["w"] + overall["d"] + overall["l"],
        "faults": overall["f"],
        "fault_reasons": dict(sorted(fault_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "wins": overall["w"], "draws": overall["d"], "losses": overall["l"],
        "score_rate": _score(overall),
        "score_denominator_games": overall["requested"],
        "score_ci95": list(_wilson(overall["w"] + 0.5 * overall["d"], overall["requested"])),
        "seat": {str(seat): {**per_seat[seat], "score_rate": _score(per_seat[seat])} for seat in (0, 1)},
        "per_opponent": {opponent_id: {**row, "score_rate": _score(row)} for opponent_id, row in per_opponent.items()},
        "per_opponent_seat": {
            opponent_id: {str(seat): {**row, "score_rate": _score(row)} for seat, row in seats.items()}
            for opponent_id, seats in per_opponent_seat.items()
        },
        "elapsed_seconds": round(time.time() - started, 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
