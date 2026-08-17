#!/usr/bin/env python3
"""Measure a frozen V4 logit ensemble through the research-only runtime boundary.

This runner is intentionally separate from the production V4 evaluator.  It
loads two or more closed V4 checkpoints, averages semantic logits/STOP before
the shared decoder, and records independent stratified CABT results.  It does
not claim game pairing: the CABT engine has no controllable engine seed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorJobConfigV1,
    _build_actor_pool_deck_binding_v1,
    _build_neural_agent_policy_factory_v4,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.research_logit_ensemble_v1 import (  # noqa: E402
    ResearchLogitEnsemblePolicyFactoryV1,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent  # noqa: E402
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402
from scripts.measure_v4_checkpoint_strength import _checkpoint_provenance  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


SCHEMA = "meta-specialist-v4-research-logit-ensemble-strength-v1"


def _sha256_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _new_row() -> dict[str, int]:
    return {"w": 0, "d": 0, "l": 0, "f": 0, "requested": 0}


def _score(row: dict[str, int]) -> float:
    return (row["w"] + 0.5 * row["d"]) / row["requested"] if row["requested"] else 0.0


def _ensemble_subject_factory(
    *,
    checkpoints: list[Path],
    provenance: list[dict[str, str]],
    subject_deck_csv: Path,
    subject_archetype_id: str,
    reset_mode: str,
):
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=subject_archetype_id,
        deck_csv_path=subject_deck_csv,
        source_commit="0" * 40,
    )
    member_factories = []
    for index, (checkpoint, item) in enumerate(zip(checkpoints, provenance)):
        job = ActorJobConfigV1(
            job_id=f"v4-ensemble-member-{index}-{item['file_sha256'][:12]}",
            archetype_id=subject_archetype_id,
            deck_csv_path=str(subject_deck_csv),
            source_commit="0" * 40,
            env_seed=0,
            seat=0,
            behavior_kind="neural_specialist_v4",
            behavior_identity=item["file_sha256"],
            neural_checkpoint_path=str(checkpoint),
            neural_checkpoint_file_sha256=item["file_sha256"],
            neural_checkpoint_tensor_state_sha256=item["tensor_state_sha256"],
            opponent_kind="held_out_evaluation",
        )
        factory, identity = _build_neural_agent_policy_factory_v4(
            job, checkpoint_lineage_id=deck_lock.policy_lineage_id,
        )
        if identity != item["file_sha256"]:
            raise ValueError("member factory identity mismatch")
        member_factories.append(factory)
    ensemble_identity = _sha256_json({
        "schema": SCHEMA,
        "reset_mode": reset_mode,
        "members": provenance,
    })
    # Runtime.make_agent binds policy telemetry to the DeckLock lineage.  The
    # ensemble member identities remain in ``ensemble_identity``; the wrapper
    # must expose the exact DeckLock lineage or the runtime must fail closed.
    lineage = deck_lock.policy_lineage_id
    policy_factory = ResearchLogitEnsemblePolicyFactoryV1(
        member_factories,
        reset_mode=reset_mode,
        policy_identity=ensemble_identity,
        checkpoint_lineage_id=lineage,
    )
    constraints = RuntimeConstraintManifest.frozen_v1()

    def factory(_deck: object, _seed: int):
        binding = make_agent(
            deck_asset=qualified,
            deck_lock=deck_lock,
            vocabulary=vocabulary,
            policy_factory=policy_factory,
            expected_policy_identity=ensemble_identity,
            constraints=constraints,
        )
        return binding.agent

    return factory, ensemble_identity, lineage, deck_lock.policy_lineage_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", action="append", type=Path, required=True, help="repeat for each frozen V4 member")
    parser.add_argument(
        "--allow-duplicate-members", action="store_true",
        help="research-only recurrence ablation: duplicate one checkpoint into independent hidden members",
    )
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--reset-mode", choices=("normal", "action", "turn"), default="normal")
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--opponent-count", type=int, default=len(EVAL_HELD_OUT_V1))
    parser.add_argument("--base-seed", type=int, default=10100000)
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.checkpoint) < 2:
        raise ValueError("ensemble requires at least two checkpoints")
    if args.games_per_seat <= 0 or not 1 <= args.opponent_count <= len(EVAL_HELD_OUT_V1):
        raise ValueError("invalid game/opponent count")
    if not args.subject_deck_csv.is_file():
        raise ValueError("subject deck must be a regular file")
    provenance = [_checkpoint_provenance(path) for path in args.checkpoint]
    if not args.allow_duplicate_members and len({item["file_sha256"] for item in provenance}) != len(provenance):
        raise ValueError("ensemble members must have distinct checkpoint file identities")
    subject_factory, identity, lineage, deck_lineage = _ensemble_subject_factory(
        checkpoints=args.checkpoint,
        provenance=provenance,
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
        reset_mode=args.reset_mode,
    )
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    opponent_ids = EVAL_HELD_OUT_V1[:args.opponent_count]
    overall = _new_row()
    per_seat = {seat: _new_row() for seat in (0, 1)}
    per_opponent = {opponent_id: _new_row() for opponent_id in opponent_ids}
    faults: dict[str, int] = {}
    started = time.time()
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for game_index in range(args.games_per_seat):
                seed = args.base_seed + game_index
                first = seat == 0
                for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                    row["requested"] += 1
                seed_agent_randomness_v1(seed)
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv) if first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if first else str(args.subject_deck_csv),
                        agent_a_name="a", agent_b_name="b", seed=seed,
                        max_steps=args.max_steps,
                        output_dir=str(args.output.parent / f"game-{opponent_id}-{seat}-{game_index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if first else opponent_factory,
                        agent_b_factory=opponent_factory if first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                    winner = result.get("winner")
                    key = "d" if winner == 2 else ("w" if winner == seat else "l")
                    for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                        row[key] += 1
                except Exception as exc:  # fault remains in requested denominator
                    reason = f"{type(exc).__name__}: {exc}"
                    faults[reason] = faults.get(reason, 0) + 1
                    for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                        row["f"] += 1
    payload = {
        "schema_version": SCHEMA,
        "status": "invalid_fault" if overall["f"] else "complete_diagnostic",
        "research_only": True,
        "promotion_authority": False,
        "longrun_allowed": False,
        "engine_seed_supported": False,
        "pairing": "independent_stratified_not_game_paired",
        "reset_mode": args.reset_mode,
        "ensemble_identity": identity,
        "ensemble_lineage": lineage,
        "deck_lineage": deck_lineage,
        "members": provenance,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_archetype_id": args.subject_archetype_id,
        "opponents": list(opponent_ids),
        "protocol_sha256": heldout_protocol_sha256_v1(),
        "base_seed": args.base_seed,
        "games_per_seat": args.games_per_seat,
        "overall": {**overall, "score": _score(overall)},
        "per_seat": {str(key): {**value, "score": _score(value)} for key, value in per_seat.items()},
        "per_opponent": {key: {**value, "score": _score(value)} for key, value in per_opponent.items()},
        "fault_reasons": faults,
        "elapsed_seconds": time.time() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if not overall["f"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
