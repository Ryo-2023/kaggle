#!/usr/bin/env python3
"""Run a bounded research-only CABT smoke for a coarse public residual gate.

This evaluator is deliberately separate from the production V4 evaluator.  It
uses a hash-bound Wave6 base checkpoint, a train-only public reference bundle,
and a zero/nonzero coarse residual table.  CABT has no engine seed setter, so
results are independent stratified diagnostics, never game-level paired
evidence.  The first permitted run is exactly two games per opponent/seat
cell and always records coarse coverage before any score interpretation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorJobConfigV1,
    _build_actor_pool_deck_binding_v1,
    _build_neural_agent_policy_factory_v4,
)
from mage_ptcg.meta_specialist.coarse_public_residual_factory_v1 import (  # noqa: E402
    CoarsePublicResidualFactoryError,
    CoarsePublicResidualPolicyFactoryV1,
)
from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import (  # noqa: E402
    CoarsePublicResidualGateError,
    load_coarse_public_reference_bundle_v1,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (  # noqa: E402
    FrozenResidualPreflightManifestV1,
    load_frozen_residual_preflight_manifest_v1,
)
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent  # noqa: E402
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


SCHEMA = "meta-specialist-coarse-public-residual-cabt-strength-v1"
TABLE_SCHEMA = "specialist-coarse-public-residual-table-v1"
_HEX64 = frozenset("0123456789abcdef")


def _sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"artifact must be a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _row() -> dict[str, int]:
    return {"requested": 0, "w": 0, "d": 0, "l": 0, "f": 0}


def _rate(row: dict[str, int]) -> float | None:
    return ((row["w"] + 0.5 * row["d"]) / row["requested"]) if row["requested"] else None


def _domain(manifest: FrozenResidualPreflightManifestV1, seed: int):
    matches = [item for item in manifest.seeds if item.provenance.seed == seed]
    if len(matches) != 1:
        raise ValueError("preflight must contain exactly one requested seed")
    return matches[0]


def _load_table(path: Path, *, expected_sha: str, bundle_sha: str, source_list_sha: str) -> dict[str, Any]:
    actual = _sha(path)
    if actual != _require_sha(expected_sha, "table SHA"):
        raise ValueError("coarse residual table SHA mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema_version", "reference_bundle_file_sha256", "reference_source_list_sha256",
        "max_abs_residual", "residual_by_bucket_action", "stop_residual_by_bucket",
        "prefix_count", "training_permitted", "promotion_authority", "longrun_allowed",
        "performance_evidence",
    }
    if type(payload) is not dict or set(payload) != expected_keys:
        raise ValueError("coarse residual table has an open schema")
    if payload["schema_version"] != TABLE_SCHEMA:
        raise ValueError("coarse residual table schema is invalid")
    if payload["reference_bundle_file_sha256"] != bundle_sha or payload["reference_source_list_sha256"] != source_list_sha:
        raise ValueError("coarse residual table reference binding differs")
    if any(payload[field] is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed", "performance_evidence")):
        raise ValueError("coarse residual table grants forbidden authority")
    if type(payload["residual_by_bucket_action"]) is not dict or type(payload["stop_residual_by_bucket"]) is not dict:
        raise ValueError("coarse residual table mappings are invalid")
    return payload


def _build_policy_factory(*, checkpoint: Path, base_sha: str, tensor_sha: str, deck_csv: Path, archetype_id: str,
                          reference: object, table: dict[str, Any], lineage_hint: str):
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=archetype_id, deck_csv_path=deck_csv, source_commit="0" * 40,
    )
    job = ActorJobConfigV1(
        job_id=f"coarse-public-residual-{base_sha[:12]}", archetype_id=archetype_id,
        deck_csv_path=str(deck_csv), source_commit="0" * 40, env_seed=0, seat=0,
        behavior_kind="neural_specialist_v4", behavior_identity=base_sha,
        neural_checkpoint_path=str(checkpoint), neural_checkpoint_file_sha256=base_sha,
        neural_checkpoint_tensor_state_sha256=tensor_sha, opponent_kind="held_out_evaluation",
    )
    base_factory, identity = _build_neural_agent_policy_factory_v4(job, checkpoint_lineage_id=deck_lock.policy_lineage_id)
    if identity != base_sha:
        raise ValueError("V4 base factory identity mismatch")
    factory = CoarsePublicResidualPolicyFactoryV1(
        base_factory,
        reference_bundle=reference,
        residual_by_bucket_action=table["residual_by_bucket_action"],
        stop_residual_by_bucket=table["stop_residual_by_bucket"],
        max_abs_residual=table["max_abs_residual"],
    )
    constraints = RuntimeConstraintManifest.frozen_v1()

    def subject_factory(_deck: object, _seed: int):
        return make_agent(
            deck_asset=qualified, deck_lock=deck_lock, vocabulary=vocabulary,
            policy_factory=factory, expected_policy_identity=base_sha,
            constraints=constraints,
        ).agent

    return subject_factory, factory, deck_lock.policy_lineage_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--table-sha256", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--source-list-sha256", required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--seed", type=int, choices=(0, 1), required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--games-per-cell", type=int, choices=(2, 8), required=True)
    parser.add_argument("--base-seed", type=int, default=10100000)
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise ValueError("coarse CABT execution requires --execute explicitly")
    bundle_sha = _require_sha(args.bundle_sha256, "bundle SHA")
    source_list_sha = _require_sha(args.source_list_sha256, "source-list SHA")
    preflight_sha = _require_sha(args.preflight_sha256, "preflight SHA")
    table = _load_table(args.table, expected_sha=args.table_sha256, bundle_sha=bundle_sha, source_list_sha=source_list_sha)
    deck_sha = _sha(args.subject_deck_csv)
    if _sha(args.bundle) != bundle_sha:
        raise ValueError("bundle file SHA mismatch")
    reference = load_coarse_public_reference_bundle_v1(args.bundle, expected_file_sha256=bundle_sha)
    if reference.source_list_sha256 != source_list_sha:
        raise ValueError("bundle source-list SHA mismatch")
    preflight = load_frozen_residual_preflight_manifest_v1(args.preflight, expected_sha256=preflight_sha, verify_files=True)
    if preflight.subject_deck_sha256 != deck_sha:
        raise ValueError("subject deck SHA differs from frozen preflight")
    domain = _domain(preflight, args.seed)
    checkpoint = Path(domain.provenance.checkpoint_path)
    if _sha(checkpoint) != domain.provenance.checkpoint_file_sha256:
        raise ValueError("base checkpoint SHA differs from preflight")
    subject_factory, factory, lineage = _build_policy_factory(
        checkpoint=checkpoint, base_sha=domain.provenance.checkpoint_file_sha256,
        tensor_sha=domain.provenance.checkpoint_tensor_state_sha256,
        deck_csv=args.subject_deck_csv, archetype_id=args.subject_archetype_id,
        reference=reference, table=table, lineage_hint=domain.provenance.checkpoint_file_sha256,
    )
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    overall, per_seat = _row(), {0: _row(), 1: _row()}
    per_opponent = {oid: _row() for oid in EVAL_HELD_OUT_V1}
    faults: dict[str, int] = {}
    factory.reset_coverage()
    coverage_by_cell: dict[str, object] = {}
    output_root = args.output.parent / f"games-seed-{args.seed}"
    started = time.time()
    for opponent_id in EVAL_HELD_OUT_V1:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(args.subject_deck_csv))
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for game_index in range(args.games_per_cell):
                rows = (overall, per_seat[seat], per_opponent[opponent_id])
                for row in rows:
                    row["requested"] += 1
                seed = args.base_seed + game_index
                seed_agent_randomness_v1(seed)
                first = seat == 0
                cell = f"{opponent_id}:seat-{seat}:game-{game_index}"
                before = factory.coverage_snapshot()
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv) if first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if first else str(args.subject_deck_csv),
                        agent_a_name="coarse" if first else "opponent", agent_b_name="opponent" if first else "coarse",
                        seed=seed, max_steps=args.max_steps, output_dir=str(output_root / f"{opponent_id}-{seat}-{game_index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if first else opponent_factory,
                        agent_b_factory=opponent_factory if first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                    winner = result.get("winner")
                    key = "d" if winner == 2 else ("w" if winner == seat else "l")
                    for row in rows:
                        row[key] += 1
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    faults[reason] = faults.get(reason, 0) + 1
                    for row in rows:
                        row["f"] += 1
                finally:
                    after = factory.coverage_snapshot()
                    coverage_by_cell[cell] = after.delta(before).to_dict()
    coverage = factory.coverage_snapshot().to_dict()
    output = {
        "schema_version": SCHEMA, "execution": "EXECUTED_RESEARCH_CABT",
        "research_only": True, "performance_evidence": False,
        "performance_evidence_reason": "coarse_coverage_smoke_not_promotion_evidence",
        "coverage_evidence": True, "training_permitted": False, "promotion_authority": False,
        "longrun_allowed": False, "engine_seed_supported": False,
        "pairing": "independent_stratified_not_game_paired", "seed": args.seed,
        "base_seed": args.base_seed, "games_per_cell": args.games_per_cell,
        "requested_games": overall["requested"], "games_played": overall["w"] + overall["d"] + overall["l"],
        "faults": overall["f"], "fault_reasons": faults,
        "wins": overall["w"], "draws": overall["d"], "losses": overall["l"], "score_rate": _rate(overall),
        "seat": {str(k): {**v, "score_rate": _rate(v)} for k, v in per_seat.items()},
        "per_opponent": {k: {**v, "score_rate": _rate(v)} for k, v in per_opponent.items()},
        "subject_deck_sha256": deck_sha, "bundle_sha256": bundle_sha,
        "source_list_sha256": source_list_sha, "table_sha256": _sha(args.table),
        "preflight_sha256": preflight_sha, "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "checkpoint_lineage_id": lineage, "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "coverage": coverage, "coverage_by_opponent_seat_game": dict(sorted(coverage_by_cell.items())),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate(args)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
