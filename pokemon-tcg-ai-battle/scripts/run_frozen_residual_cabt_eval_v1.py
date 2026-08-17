#!/usr/bin/env python3
"""Research-only CABT evaluation for a hash-bound frozen residual sidecar.

This runner is intentionally separate from the production V4 evaluator.  It
uses the existing DeckLock/V4 policy factory and wraps each fresh game policy
with a frozen residual sidecar.  CABT has no controllable engine seed, so the
result is independent stratified evaluation, never game-level pairing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorJobConfigV1,
    _build_actor_pool_deck_binding_v1,
    _build_neural_agent_policy_factory_v4,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.frozen_residual_factory_v1 import FrozenResidualPolicyFactoryV1  # noqa: E402
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (  # noqa: E402
    FrozenResidualPreflightManifestV1,
    load_frozen_residual_preflight_manifest_v1,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent  # noqa: E402
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


SCHEMA = "meta-specialist-frozen-residual-cabt-strength-v1"


def _research_evidence_flags() -> dict[str, object]:
    """Declare the bounded evaluator's evidence class explicitly.

    This runner is a two-games-per-cell research smoke with an engine that
    cannot provide common-random-number pairing.  Fault-free completion proves
    runtime health only; it must never be promoted to performance evidence.
    """

    return {
        "performance_evidence": False,
        "coverage_evidence": True,
        "performance_evidence_reason": "coverage_diagnostic_only_not_promotion_evidence",
    }
_HEX = frozenset("0123456789abcdef")


def _sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _HEX for c in value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _row() -> dict[str, int]:
    return {"requested": 0, "w": 0, "d": 0, "l": 0, "f": 0}


def _rate(row: dict[str, int]) -> float | None:
    return ((row["w"] + 0.5 * row["d"]) / row["requested"]) if row["requested"] else None


def _domain(manifest: FrozenResidualPreflightManifestV1, seed: int):
    match = [item for item in manifest.seeds if item.provenance.seed == seed]
    if len(match) != 1:
        raise ValueError("preflight must contain exactly one requested seed")
    return match[0]


def _coverage_result(snapshot: object, *, by_cell: dict[str, object]) -> dict[str, object]:
    """Serialize measured sidecar counters without inventing coarse coverage."""
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("residual coverage snapshot must expose to_dict()")
    payload = dict(to_dict())
    observed = int(payload.get("total_decisions", 0)) > 0
    return {
        "observed": observed,
        "reason": "measured_sidecar_runtime_counters" if observed else "no_residual_decisions_observed",
        "coarse_public_bucket_observed": False,
        "coarse_public_bucket_reason": "v1_runtime_gate_is_exact_context_sha_and_action_sha",
        "known_public_bucket": None,
        "known_public_bucket_rate": None,
        **payload,
        "by_opponent_seat_game": dict(sorted(by_cell.items())),
    }


def _coverage_summary(snapshot: object) -> dict[str, object]:
    """Return the compact nested summary used by unit/audit callers."""
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("residual coverage snapshot must expose to_dict()")
    payload = dict(to_dict())
    return {
        "observed": int(payload.get("total_decisions", 0)) > 0,
        "snapshot": payload,
    }


def _build_policy_factory(*, checkpoint: Path, base_sha: str, tensor_sha: str,
                          deck_csv: Path, archetype_id: str, sidecar: Path,
                          sidecar_sha: str, preflight: FrozenResidualPreflightManifestV1,
                          seed: int):
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=archetype_id, deck_csv_path=deck_csv, source_commit="0" * 40,
    )
    job = ActorJobConfigV1(
        job_id=f"frozen-residual-{seed}-{base_sha[:12]}",
        archetype_id=archetype_id,
        deck_csv_path=str(deck_csv), source_commit="0" * 40,
        env_seed=0, seat=0, behavior_kind="neural_specialist_v4",
        behavior_identity=base_sha, neural_checkpoint_path=str(checkpoint),
        neural_checkpoint_file_sha256=base_sha,
        neural_checkpoint_tensor_state_sha256=tensor_sha,
        opponent_kind="held_out_evaluation",
    )
    base_factory, identity = _build_neural_agent_policy_factory_v4(
        job, checkpoint_lineage_id=deck_lock.policy_lineage_id,
    )
    if identity != base_sha:
        raise ValueError("V4 base factory identity mismatch")
    residual_factory = FrozenResidualPolicyFactoryV1(
        base_factory, sidecar_path=sidecar, expected_sidecar_sha256=sidecar_sha,
        preflight_manifest=preflight, seed=seed,
    )
    constraints = RuntimeConstraintManifest.frozen_v1()

    def subject_factory(_deck: object, _seed: int):
        # Telemetry identity remains the immutable base checkpoint identity;
        # sidecar identity is recorded separately in the result descriptor.
        return make_agent(
            deck_asset=qualified, deck_lock=deck_lock, vocabulary=vocabulary,
            policy_factory=residual_factory, expected_policy_identity=base_sha,
            constraints=constraints,
        ).agent

    return subject_factory, residual_factory, deck_lock.policy_lineage_id


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sidecar", type=Path, required=True)
    p.add_argument("--sidecar-sha256", required=True)
    p.add_argument("--preflight", type=Path, required=True)
    p.add_argument("--preflight-sha256", required=True)
    p.add_argument("--seed", type=int, choices=(0, 1), required=True)
    p.add_argument("--subject-deck-csv", type=Path, required=True)
    p.add_argument("--subject-archetype-id", required=True)
    p.add_argument("--games-per-cell", type=int, required=True)
    p.add_argument("--base-seed", type=int, default=10100000)
    p.add_argument("--max-steps", type=int, default=2000)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    return p


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        raise ValueError("CABT execution requires --execute explicitly")
    if args.games_per_cell != 2:
        raise ValueError("first bounded residual evaluation requires --games-per-cell 2")
    if args.base_seed < 0 or args.max_steps <= 0:
        raise ValueError("base seed and max steps must be positive")
    sidecar_sha = _require_sha(args.sidecar_sha256, "sidecar SHA")
    preflight_sha = _require_sha(args.preflight_sha256, "preflight SHA")
    deck_sha = _sha(args.subject_deck_csv)
    preflight = load_frozen_residual_preflight_manifest_v1(
        args.preflight, expected_sha256=preflight_sha, verify_files=True,
    )
    if preflight.subject_deck_sha256 != deck_sha:
        raise ValueError("subject deck SHA differs from frozen preflight")
    domain = _domain(preflight, args.seed)
    checkpoint = Path(domain.provenance.checkpoint_path)
    if _sha(checkpoint) != domain.provenance.checkpoint_file_sha256:
        raise ValueError("base checkpoint file SHA differs from preflight")
    subject_factory, residual_factory, lineage = _build_policy_factory(
        checkpoint=checkpoint, base_sha=domain.provenance.checkpoint_file_sha256,
        tensor_sha=domain.provenance.checkpoint_tensor_state_sha256,
        deck_csv=args.subject_deck_csv, archetype_id=args.subject_archetype_id,
        sidecar=args.sidecar, sidecar_sha=sidecar_sha, preflight=preflight, seed=args.seed,
    )
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    overall, per_seat = _row(), {0: _row(), 1: _row()}
    per_opponent: dict[str, dict[str, int]] = {oid: _row() for oid in EVAL_HELD_OUT_V1}
    faults: dict[str, int] = {}
    residual_factory.reset_coverage()
    coverage_by_cell: dict[str, object] = {}
    output_root = args.output.parent / f"games-seed-{args.seed}"
    started = time.time()
    for opponent_id in EVAL_HELD_OUT_V1:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(args.subject_deck_csv))
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for game_index in range(args.games_per_cell):
                rowset = (overall, per_seat[seat], per_opponent[opponent_id])
                for row in rowset:
                    row["requested"] += 1
                seed = args.base_seed + game_index
                seed_agent_randomness_v1(seed)
                first = seat == 0
                cell_key = f"{opponent_id}:seat-{seat}:game-{game_index}"
                coverage_before = residual_factory.coverage_snapshot()
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv) if first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if first else str(args.subject_deck_csv),
                        agent_a_name="residual" if first else "opponent",
                        agent_b_name="opponent" if first else "residual",
                        seed=seed, max_steps=args.max_steps,
                        output_dir=str(output_root / f"{opponent_id}-{seat}-{game_index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if first else opponent_factory,
                        agent_b_factory=opponent_factory if first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                    winner = result.get("winner")
                    key = "d" if winner == 2 else ("w" if winner == seat else "l")
                    for row in rowset:
                        row[key] += 1
                except Exception as exc:  # invalid game is counted, never hidden
                    reason = f"{type(exc).__name__}: {exc}"
                    faults[reason] = faults.get(reason, 0) + 1
                    for row in rowset:
                        row["f"] += 1
                finally:
                    coverage_after = residual_factory.coverage_snapshot()
                    try:
                        coverage_by_cell[cell_key] = coverage_after.delta(coverage_before).to_dict()
                    except Exception as exc:
                        reason = f"coverage:{type(exc).__name__}: {exc}"
                        faults[reason] = faults.get(reason, 0) + 1
                        coverage_by_cell[cell_key] = {
                            "schema_version": "specialist-frozen-wave6-residual-coverage-v1",
                            "error": reason,
                        }
    total_coverage = _coverage_result(
        residual_factory.coverage_snapshot(), by_cell=coverage_by_cell,
    )
    result = {
        "schema_version": SCHEMA,
        "execution": "EXECUTED_RESEARCH_CABT",
        "research_only": True,
        **_research_evidence_flags(),
        "promotion_authority": False, "training_permitted": False, "longrun_allowed": False,
        "engine_seed_supported": False,
        "pairing": "independent_stratified_not_game_paired",
        "seed": args.seed, "base_seed": args.base_seed,
        "games_per_cell": args.games_per_cell,
        "requested_games": overall["requested"], "games_played": overall["w"] + overall["d"] + overall["l"],
        "faults": overall["f"], "fault_reasons": faults,
        "wins": overall["w"], "draws": overall["d"], "losses": overall["l"],
        "score_rate": _rate(overall),
        "seat": {str(k): {**v, "score_rate": _rate(v)} for k, v in per_seat.items()},
        "per_opponent": {k: {**v, "score_rate": _rate(v)} for k, v in per_opponent.items()},
        "subject_deck_sha256": deck_sha,
        "preflight_sha256": preflight_sha, "sidecar_sha256": sidecar_sha,
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "checkpoint_lineage_id": lineage,
        "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "coverage": total_coverage,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
