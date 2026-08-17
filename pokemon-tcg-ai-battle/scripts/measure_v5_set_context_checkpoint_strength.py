#!/usr/bin/env python3
"""Measure one research-only V5 SetContext artifact on the fixed six pool.

The evaluator deliberately bypasses the production V4 actor-pool behavior
kind: it binds the public deck through ``_build_actor_pool_deck_binding_v1``,
loads the V5 policy factory, and enters the real ``runtime.make_agent``
boundary.  V4 evaluator and submission paths remain untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    _build_actor_pool_deck_binding_v1,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from mage_ptcg.meta_specialist.neural_model_v5 import (  # noqa: E402
    CHECKPOINT_SCHEMA_V5,
    NEURAL_MODEL_SCHEMA_V5,
    NeuralModelV5Error,
    SpecialistModelV5,
    load_specialist_checkpoint_v5,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1  # noqa: E402
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent  # noqa: E402
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402
from scripts.measure_opponent_strength import _wilson  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


V5_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1 = "meta-specialist-v5-set-context-heldout-checkpoint-strength-v1"


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a 64-character lowercase hex SHA-256 string")
    return value


def _descriptor_sha256_v5(descriptor: dict[str, object]) -> str:
    raw = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"meta-specialist-v5-descriptor\0" + raw).hexdigest()


def evaluation_implementation_sha256_v5() -> str:
    """Hash evaluator, V5 model/policy, runtime, and fixed-pool dependencies."""
    digest = hashlib.sha256(b"meta-specialist-v5-set-context-heldout-evaluator-v1\0")
    paths = [
        Path(__file__).resolve(),
        _ROOT / "src/mage_ptcg/meta_specialist/neural_model_v5.py",
        _ROOT / "src/mage_ptcg/meta_specialist/runtime.py",
        _ROOT / "src/mage_ptcg/meta_specialist/runtime_actions_v2.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actions.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actor_pool_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/opponent_pool_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/decks.py",
        _ROOT / "src/mage_ptcg/meta_specialist/collect_teacher_records_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/heldout_protocol_v1.py",
        _ROOT / "scripts/test_sim.py",
        _ROOT / "scripts/make_medal_opponents.py",
    ]
    policy_path = _ROOT / "src/mage_ptcg/meta_specialist/neural_policy_v5.py"
    if policy_path.is_file():
        paths.append(policy_path)
    for path in paths:
        if not path.is_file():
            raise ValueError(f"evaluation implementation dependency is missing: {path}")
        raw = path.read_bytes()
        digest.update(str(path.relative_to(_ROOT)).encode("utf-8") + b"\0")
        digest.update(len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _checkpoint_provenance_v5(checkpoint: Path) -> dict[str, object]:
    """Validate a V5 artifact and expose its base transfer provenance."""
    if not checkpoint.is_file():
        raise ValueError(f"checkpoint does not exist or is not a regular file: {checkpoint}")
    raw = checkpoint.read_bytes()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        import torch

        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, EOFError) as exc:
        raise ValueError("checkpoint has no readable closed V5 descriptor") from exc
    if type(payload) is not dict or set(payload) != {"descriptor", "state_dict"}:
        raise ValueError("checkpoint is not a closed V5 artifact")
    descriptor = payload.get("descriptor")
    if type(descriptor) is not dict or descriptor.get("checkpoint_schema") != CHECKPOINT_SCHEMA_V5:
        raise ValueError("checkpoint schema is not V5 SetContext")
    tensor_sha256 = _require_hex64(descriptor.get("tensor_state_sha256"), "tensor_state_sha256")
    config = descriptor.get("model_config")
    if type(config) is not dict:
        raise ValueError("V5 descriptor has no model_config")
    try:
        model = SpecialistModelV5(**config, seed=0)
        validated = load_specialist_checkpoint_v5(
            checkpoint,
            model,
            expected_file_sha256=file_sha256,
            expected_tensor_state_sha256=tensor_sha256,
        )
    except (NeuralModelV5Error, TypeError, ValueError) as exc:
        raise ValueError("checkpoint failed strict V5 artifact validation") from exc
    return {
        "path": str(checkpoint.resolve()),
        "file_sha256": file_sha256,
        "descriptor_sha256": _descriptor_sha256_v5(validated),
        "tensor_state_sha256": tensor_sha256,
        "checkpoint_schema": validated["checkpoint_schema"],
        "neural_model_schema": validated["neural_model_schema"],
        "implementation_digest_sha256": validated["implementation_digest_sha256"],
        "model_config": validated["model_config"],
        "head_config": validated["head_config"],
        "base_provenance": validated["base_provenance"],
        "transfer": validated["transfer"],
    }


def _v5_subject_factory(
    *,
    checkpoint_path: Path,
    file_sha256: str,
    tensor_state_sha256: str,
    subject_deck_csv: Path,
    subject_archetype_id: str,
):
    """Bind V5 policy through deck binding and the real runtime.make_agent."""
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=subject_archetype_id,
        deck_csv_path=subject_deck_csv,
        source_commit="0" * 40,
    )
    from mage_ptcg.meta_specialist.neural_policy_v5 import (
        SpecialistNeuralPolicyV5Factory,
        load_specialist_neural_policy_from_checkpoint_v5,
    )

    policy = load_specialist_neural_policy_from_checkpoint_v5(
        checkpoint_path,
        expected_file_sha256=file_sha256,
        expected_tensor_state_sha256=tensor_state_sha256,
        checkpoint_lineage_id=deck_lock.policy_lineage_id,
    )
    policy_factory = SpecialistNeuralPolicyV5Factory(policy)
    constraints = RuntimeConstraintManifest.frozen_v1()

    def factory(_deck: object, _seed: int):
        binding = make_agent(
            deck_asset=qualified,
            deck_lock=deck_lock,
            vocabulary=vocabulary,
            policy_factory=policy_factory,
            expected_policy_identity=file_sha256,
            constraints=constraints,
        )
        return binding.agent

    return factory


def _new_row() -> dict[str, int]:
    return {"w": 0, "d": 0, "l": 0, "f": 0, "requested": 0}


def _score(row: dict[str, int]) -> float | None:
    return (row["w"] + 0.5 * row["d"]) / row["requested"] if row["requested"] else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--games-per-seat", type=int, default=4)
    parser.add_argument("--base-seed", type=int, default=9_100_000)
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
    if not args.subject_deck_csv.is_file():
        raise ValueError(f"--subject-deck-csv does not exist or is not a regular file: {args.subject_deck_csv}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    provenance = _checkpoint_provenance_v5(args.checkpoint)
    subject_factory = _v5_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        tensor_state_sha256=provenance["tensor_state_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
    )
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    opponent_ids = tuple(EVAL_HELD_OUT_V1)
    opponent_fingerprints: list[dict[str, str]] = []
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_fingerprints.append({
            "opponent_id": opponent_id,
            "canonical_deck_hash": opponent.canonical_deck_hash,
            "deck_file_sha256": hashlib.sha256(Path(opponent.deck_csv_path).read_bytes()).hexdigest(),
            "policy_hash": opponent.policy_hash,
        })
    requested_games = len(opponent_ids) * 2 * args.games_per_seat
    reporter = ProgressReporterV1(
        total=requested_games,
        desc=f"v5-heldout {provenance['file_sha256'][:12]}",
        progress_path=args.progress_path,
    )
    reporter.note(
        f"[v5-heldout] checkpoint={provenance['file_sha256'][:12]} "
        f"opponents={len(opponent_ids)} games={requested_games}"
    )
    overall = _new_row()
    per_seat = {seat: _new_row() for seat in (0, 1)}
    per_opponent = {opponent_id: _new_row() for opponent_id in opponent_ids}
    fault_reasons: dict[str, int] = {}
    started = time.time()
    output_root = Path("runs/meta-specialist-strength") / f"v5-heldout-{provenance['file_sha256'][:12]}"

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
                        agent_a_name="a",
                        agent_b_name="b",
                        seed=seed,
                        max_steps=args.max_steps,
                        output_dir=str(output_root / f"{opponent_id}-{seat}-{game_index}"),
                        save_html=False,
                        save_result=False,
                        agent_a_factory=subject_factory if first else opponent_factory,
                        agent_b_factory=opponent_factory if first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    fault_reasons[reason] = fault_reasons.get(reason, 0) + 1
                    for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                        row["f"] += 1
                    reporter.update(1, faults=overall["f"], rate=_score(overall) or 0.0)
                    continue
                winner = result.get("winner")
                if winner == 2:
                    key = "d"
                elif winner == seat:
                    key = "w"
                else:
                    key = "l"
                for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                    row[key] += 1
                reporter.update(
                    1,
                    win=overall["w"],
                    loss=overall["l"],
                    draw=overall["d"],
                    faults=overall["f"],
                    rate=_score(overall) or 0.0,
                )
    reporter.close()

    score = _score(overall)
    payload: dict[str, Any] = {
        "schema_version": V5_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1,
        "checkpoint": provenance,
        "v5_artifact": {
            "checkpoint_schema": provenance["checkpoint_schema"],
            "neural_model_schema": provenance["neural_model_schema"],
            "descriptor_sha256": provenance["descriptor_sha256"],
            "model_config": provenance["model_config"],
            "head_config": provenance["head_config"],
            "transfer": provenance["transfer"],
        },
        "base_provenance": provenance["base_provenance"],
        "subject_archetype_id": args.subject_archetype_id,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_file_sha256": hashlib.sha256(args.subject_deck_csv.read_bytes()).hexdigest(),
        "fixed_held_out_opponent_ids": list(EVAL_HELD_OUT_V1),
        "opponent_ids": list(opponent_ids),
        "opponent_fingerprints": opponent_fingerprints,
        "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "evaluation_implementation_sha256": evaluation_implementation_sha256_v5(),
        "games_per_seat": args.games_per_seat,
        "base_seed": args.base_seed,
        "max_steps": args.max_steps,
        "requested_games": overall["requested"],
        "games_played": overall["w"] + overall["d"] + overall["l"],
        "faults": overall["f"],
        "fault_reasons": dict(sorted(fault_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "wins": overall["w"],
        "draws": overall["d"],
        "losses": overall["l"],
        "score_rate": score,
        "score_denominator_games": overall["requested"],
        "score_ci95": list(_wilson(overall["w"] + 0.5 * overall["d"], overall["requested"])),
        "comparison_status": "invalid_faults" if overall["f"] else "valid",
        "seat": {str(seat): {**per_seat[seat], "score_rate": _score(per_seat[seat])} for seat in (0, 1)},
        "per_opponent": {opponent_id: {**row, "score_rate": _score(row)} for opponent_id, row in per_opponent.items()},
        "elapsed_seconds": round(time.time() - started, 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "per_opponent"}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
