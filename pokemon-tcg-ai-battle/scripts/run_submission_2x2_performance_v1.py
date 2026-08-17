#!/usr/bin/env python3
"""Research-only policy/deck 2x2 performance bridge.

The directive's first information-gain experiment is the two missing cells:
V4 seed-1 on the submission/root deck and Rule v0 on the existing strong
Archaludon deck.  This module keeps the two cells in one evaluator block so
the machine's worker budget is shared, while preserving an identical
opponent/seat/repetition/seed schedule for every cell.

Only terminal WDL is persisted.  Native action labels, private observations,
training authority, promotion authority, and submission authority are absent
by construction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
    _game_from_payload,
)
from scripts.test_sim import run_match  # noqa: E402


SUBMISSION_2X2_SCHEMA_V1 = "meta-specialist-submission-2x2-performance-v1"
RUNNER_REF_V1 = "scripts.run_submission_2x2_performance_v1:run_submission_2x2_game_v1"
POLICY_KINDS_V1 = frozenset({"rule_v0", "v4_seed1"})
AUTHORITY_FALSE_V1 = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}


class Submission2x2RuntimeError(ValueError):
    """Raised when a 2x2 cell cannot be bound to closed source identities."""


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Submission2x2RuntimeError(f"cannot hash source: {path}") from exc


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise Submission2x2RuntimeError(f"{name} must be a lowercase SHA-256")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise Submission2x2RuntimeError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class Submission2x2ArmV1:
    """One policy/deck cell of the small research 2x2."""

    arm_id: str
    policy_kind: str
    policy_id: str
    policy_sha256: str
    deck_path: Path
    deck_sha256: str
    policy_path: Path
    checkpoint_path: Path | None = None
    checkpoint_tensor_sha256: str | None = None
    subject_archetype_id: str = "archaludon"

    def __post_init__(self) -> None:
        _text(self.arm_id, "arm_id")
        if self.policy_kind not in POLICY_KINDS_V1:
            raise Submission2x2RuntimeError(f"policy_kind is unsupported: {self.policy_kind!r}")
        _text(self.policy_id, "policy_id")
        _sha(self.policy_sha256, "policy_sha256")
        _sha(self.deck_sha256, "deck_sha256")
        object.__setattr__(self, "deck_path", Path(self.deck_path).resolve())
        object.__setattr__(self, "policy_path", Path(self.policy_path).resolve())
        if type(self.subject_archetype_id) is not str or not self.subject_archetype_id:
            raise Submission2x2RuntimeError("subject_archetype_id must be non-empty")
        if self.policy_kind == "v4_seed1":
            if self.checkpoint_path is None or self.checkpoint_tensor_sha256 is None:
                raise Submission2x2RuntimeError("v4_seed1 requires checkpoint identity")
            object.__setattr__(self, "checkpoint_path", Path(self.checkpoint_path).resolve())
            _sha(self.checkpoint_tensor_sha256, "checkpoint_tensor_sha256")

    def verify_sources(self) -> None:
        if not self.deck_path.is_file() or _sha256(self.deck_path) != self.deck_sha256:
            raise Submission2x2RuntimeError(f"deck source SHA changed: {self.deck_path}")
        if not self.policy_path.is_file() or _sha256(self.policy_path) != self.policy_sha256:
            raise Submission2x2RuntimeError(f"policy source SHA changed: {self.policy_path}")
        if self.policy_kind == "v4_seed1":
            assert self.checkpoint_path is not None
            if not self.checkpoint_path.is_file() or _sha256(self.checkpoint_path) != self.policy_sha256:
                raise Submission2x2RuntimeError("V4 checkpoint file SHA changed")

    def to_dict(self) -> dict[str, object]:
        return {
            "arm_id": self.arm_id,
            "policy_kind": self.policy_kind,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "policy_path": str(self.policy_path),
            "deck_path": str(self.deck_path),
            "deck_sha256": self.deck_sha256,
            "checkpoint_path": str(self.checkpoint_path) if self.checkpoint_path else None,
            "checkpoint_tensor_sha256": self.checkpoint_tensor_sha256,
            "subject_archetype_id": self.subject_archetype_id,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        }


def _require_refs(reference_ids: Sequence[str]) -> tuple[str, ...]:
    refs = tuple(_text(item, "reference_id") for item in reference_ids)
    if not refs or len(set(refs)) != len(refs):
        raise Submission2x2RuntimeError("reference_ids must be unique and non-empty")
    return refs


def build_submission_2x2_games_v1(
    *,
    arm: Submission2x2ArmV1,
    pool_root: Path,
    reference_ids: Sequence[str],
    games_per_opponent_seat: int,
    base_seed: int,
    block_id: str,
    runner_ref: str = RUNNER_REF_V1,
    max_steps: int = 2_000,
    timeout_seconds: float = 600.0,
) -> tuple[EvaluationGameV1, ...]:
    """Build one cell with balanced seats and deterministic pair keys."""

    if not isinstance(arm, Submission2x2ArmV1):
        raise Submission2x2RuntimeError("arm must be Submission2x2ArmV1")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise Submission2x2RuntimeError("games_per_opponent_seat must be positive")
    if type(base_seed) is not int or base_seed < 0:
        raise Submission2x2RuntimeError("base_seed must be nonnegative")
    refs = _require_refs(reference_ids)
    arm.verify_sources()
    pool_root = Path(pool_root).resolve()
    pool = load_opponent_pool_v1(pool_root)
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for opponent_id in refs:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(arm.deck_path))
        opponent_deck_sha = _sha256(Path(opponent.deck_csv_path))
        opponent_policy_sha = _sha256(Path(opponent.policy_path)) if opponent.policy_path else "0" * 64
        for seat in (0, 1):
            for repetition in range(games_per_opponent_seat):
                seed = base_seed + ordinal
                game_id = f"{block_id}-{arm.arm_id}-{opponent_id}-s{seat}-r{repetition:02d}"
                games.append(
                    EvaluationGameV1(
                        game_id=game_id,
                        block_id=block_id,
                        policy_id=arm.policy_id,
                        policy_sha256=arm.policy_sha256,
                        deck_id=f"{arm.arm_id}-deck",
                        deck_sha256=arm.deck_sha256,
                        opponent_id=opponent_id,
                        opponent_identity={
                            "policy_sha256": opponent_policy_sha,
                            "deck_sha256": opponent_deck_sha,
                            "usage_boundary": opponent.usage_boundary,
                            "source": opponent.source,
                        },
                        opponent_deck_sha256=opponent_deck_sha,
                        seat=seat,
                        seed=seed,
                        max_steps=max_steps,
                        timeout_seconds=timeout_seconds,
                        subject_deck_path=str(arm.deck_path),
                        opponent_deck_path=str(opponent.deck_csv_path),
                        policy_agent_name=arm.policy_id,
                        opponent_agent_name=opponent_id,
                        runner_ref=runner_ref,
                        metadata={
                            "schema_version": SUBMISSION_2X2_SCHEMA_V1,
                            "cell": arm.arm_id,
                            "policy_kind": arm.policy_kind,
                            "policy_id": arm.policy_id,
                            "policy_sha256": arm.policy_sha256,
                            "deck_sha256": arm.deck_sha256,
                            "checkpoint_path": str(arm.checkpoint_path) if arm.checkpoint_path else None,
                            "checkpoint_tensor_sha256": arm.checkpoint_tensor_sha256,
                            "subject_archetype_id": arm.subject_archetype_id,
                            "pair_key": f"{opponent_id}|seat{seat}|rep{repetition}",
                            "repetition": repetition,
                            "research_only": True,
                            "authority": dict(AUTHORITY_FALSE_V1),
                        },
                    )
                )
                ordinal += 1
    return tuple(games)


def _rule_factory(deck_path: str, seed: int):
    from main import make_rule_agent

    return make_rule_agent(deck_path=deck_path, seed=seed)


def _v4_factory(metadata: Mapping[str, object], deck_path: Path):
    from scripts.measure_v4_checkpoint_strength import _v4_subject_factory

    checkpoint = Path(str(metadata["checkpoint_path"])).resolve()
    tensor_sha = _sha(str(metadata["checkpoint_tensor_sha256"]), "checkpoint_tensor_sha256")
    return _v4_subject_factory(
        checkpoint_path=checkpoint,
        file_sha256=_sha256(checkpoint),
        tensor_state_sha256=tensor_sha,
        subject_deck_csv=deck_path,
        subject_archetype_id=str(metadata["subject_archetype_id"]),
    )


def run_submission_2x2_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Spawn-safe runner for one Rule v0 or V4 subject game."""

    game = _game_from_payload(payload)
    metadata = game.metadata
    kind = metadata.get("policy_kind")
    if kind not in POLICY_KINDS_V1:
        raise Submission2x2RuntimeError("game policy_kind is invalid")
    if metadata.get("research_only") is not True or metadata.get("authority") != AUTHORITY_FALSE_V1:
        raise Submission2x2RuntimeError("2x2 game is not research-only")
    if _sha256(Path(game.subject_deck_path)) != game.deck_sha256:
        raise Submission2x2RuntimeError("subject deck identity changed")
    if kind == "rule_v0":
        subject_factory = lambda _deck, seed: _rule_factory(game.subject_deck_path, seed)
    else:
        subject_factory = _v4_factory(metadata, Path(game.subject_deck_path))
    pool = load_opponent_pool_v1(Path(__file__).resolve().parents[1] / "opponents")
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if first else game.subject_deck_path,
        agent_a_name=game.policy_id if first else game.opponent_id,
        agent_b_name=game.opponent_id if first else game.policy_id,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "submission-2x2-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if first else opponent_factory,
        agent_b_factory=opponent_factory if first else subject_factory,
    )


def _aggregate(rows: Sequence[Mapping[str, object]], *, include_opponent: bool = True) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    result: dict[str, object] = {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
    }
    result["score_rate"] = (result["wins"] + 0.5 * result["draws"]) / requested if requested else None
    result["fault_rate"] = result["faults"] / requested if requested else None
    result["seat_score"] = {
        str(seat): {
            "requested_games": sum(1 for row in rows if row.get("seat") == seat),
            "wins": sum(1 for row in rows if row.get("seat") == seat and row.get("outcome") == "win"),
            "draws": sum(1 for row in rows if row.get("seat") == seat and row.get("outcome") == "draw"),
            "losses": sum(1 for row in rows if row.get("seat") == seat and row.get("outcome") == "loss"),
            "faults": sum(1 for row in rows if row.get("seat") == seat and row.get("outcome") == "fault"),
        }
        for seat in (0, 1)
    }
    for seat, data in result["seat_score"].items():
        n = data["requested_games"]
        data["score_rate"] = (data["wins"] + 0.5 * data["draws"]) / n if n else None
    if include_opponent:
        per_opponent: dict[str, dict[str, object]] = {}
        for opponent in sorted({str(row.get("opponent_id")) for row in rows}):
            per_opponent[opponent] = _aggregate(
                [row for row in rows if row.get("opponent_id") == opponent],
                include_opponent=False,
            )
        result["per_opponent"] = per_opponent
    return result


def _write_new_json(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temp.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, path)
        temp.unlink(missing_ok=True)
    finally:
        temp.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def run_submission_2x2_v1(
    *,
    arms: Sequence[Submission2x2ArmV1],
    pool_root: Path,
    reference_ids: Sequence[str],
    games_per_opponent_seat: int = 2,
    base_seed: int = 14900000,
    output_root: Path,
    execute: bool = False,
    workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
    max_steps: int = 2_000,
    timeout_seconds: float = 600.0,
) -> dict[str, object]:
    """Materialize or execute one or more 2x2 cells in a shared worker pool."""

    if not arms:
        raise Submission2x2RuntimeError("at least one 2x2 arm is required")
    if len({arm.arm_id for arm in arms}) != len(arms):
        raise Submission2x2RuntimeError("2x2 arm IDs must be unique")
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"output root already exists: {root}")
    games: list[EvaluationGameV1] = []
    for arm in arms:
        games.extend(
            build_submission_2x2_games_v1(
                arm=arm,
                pool_root=pool_root,
                reference_ids=reference_ids,
                games_per_opponent_seat=games_per_opponent_seat,
                base_seed=base_seed,
                block_id=f"{arm.arm_id}-{base_seed}",
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
            )
        )
    pool_manifest = Path(pool_root) / "pool_manifest.json"
    manifest: dict[str, object] = {
        "schema_version": SUBMISSION_2X2_SCHEMA_V1,
        "status": "DRY_RUN" if not execute else "EXECUTING",
        "execution_started": bool(execute),
        "arms": [arm.to_dict() for arm in arms],
        "reference_ids": list(reference_ids),
        "games_per_opponent_seat": games_per_opponent_seat,
        "requested_games": len(games),
        "base_seed": base_seed,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "pool_manifest_sha256": _sha256(pool_manifest),
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    root.mkdir(parents=True, exist_ok=False)
    manifest_sha = _write_new_json(root / "manifest.json", manifest)
    if not execute:
        return {"status": "DRY_RUN", "manifest_sha256": manifest_sha, "output_root": str(root)}
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=root / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    by_arm = {
        arm.arm_id: _aggregate([row for row in evaluation["rows"] if row.get("metadata", {}).get("cell") == arm.arm_id])
        for arm in arms
    }
    summary = {
        "schema_version": SUBMISSION_2X2_SCHEMA_V1,
        "status": "COMPLETE",
        "arms": by_arm,
        "requested_games": len(games),
        "evaluator_summary": evaluation["summary"],
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    summary_sha = _write_new_json(root / "summary.json", summary)
    manifest.update({"status": "COMPLETE", "summary_sha256": summary_sha, "evaluation_output": "evaluation"})
    manifest_sha = _write_new_json(root / "manifest-complete.json", manifest)
    return {
        "status": "COMPLETE",
        "summary": summary,
        "summary_sha256": summary_sha,
        "manifest_sha256": manifest_sha,
        "output_root": str(root),
    }


def finalize_submission_2x2_v1(run_root: Path | str) -> dict[str, object]:
    """Finalize a completed evaluator directory after a summarizer retry.

    The evaluator ledger is immutable evidence.  This helper only derives the
    small 2x2 summary and publishes a separate completion manifest, so a
    summarizer bug cannot trigger a second expensive game run.
    """

    root = Path(run_root).resolve()
    manifest_path = root / "manifest.json"
    evaluation_dir = root / "evaluation"
    if not manifest_path.is_file() or not evaluation_dir.is_dir():
        raise Submission2x2RuntimeError("2x2 evaluator root is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger_path = evaluation_dir / "ledger.jsonl"
    rows: list[dict[str, object]] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise Submission2x2RuntimeError("2x2 ledger row is not an object")
            rows.append(item)
    arm_ids = [str(item["arm_id"]) for item in manifest.get("arms", [])]
    if not arm_ids:
        arm_ids = sorted({str(row.get("metadata", {}).get("cell")) for row in rows})
    by_arm = {
        arm_id: _aggregate(
            [row for row in rows if row.get("metadata", {}).get("cell") == arm_id]
        )
        for arm_id in arm_ids
    }
    summary = {
        "schema_version": SUBMISSION_2X2_SCHEMA_V1,
        "status": "COMPLETE",
        "arms": by_arm,
        "requested_games": len(rows),
        "evaluator_summary": json.loads((evaluation_dir / "summary.json").read_text(encoding="utf-8")),
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    summary_sha = _write_new_json(root / "summary.json", summary)
    manifest.update({"status": "COMPLETE", "summary_sha256": summary_sha, "evaluation_output": "evaluation"})
    manifest_sha = _write_new_json(root / "manifest-complete.json", manifest)
    return {
        "status": "COMPLETE",
        "summary": summary,
        "summary_sha256": summary_sha,
        "manifest_sha256": manifest_sha,
        "output_root": str(root),
    }


__all__ = [
    "AUTHORITY_FALSE_V1",
    "DEFAULT_MAX_WORKERS_V1",
    "DEFAULT_WORKER_RECYCLE_GAMES_V1",
    "RUNNER_REF_V1",
    "Submission2x2ArmV1",
    "Submission2x2RuntimeError",
    "SUBMISSION_2X2_SCHEMA_V1",
    "build_submission_2x2_games_v1",
    "run_submission_2x2_game_v1",
    "run_submission_2x2_v1",
    "finalize_submission_2x2_v1",
]


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v4-checkpoint", type=Path, required=True)
    parser.add_argument("--v4-tensor-sha256", required=True)
    parser.add_argument("--v4-deck", type=Path, required=True)
    parser.add_argument("--rule-main", type=Path, required=True)
    parser.add_argument("--rule-deck", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, default=_ROOT / "opponents")
    parser.add_argument("--reference-config", type=Path, default=_ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=14900000)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    refs_payload = json.loads(args.reference_config.read_text(encoding="utf-8"))
    refs = refs_payload.get("opponent_ids") if isinstance(refs_payload, Mapping) else None
    if not isinstance(refs, list) or len(refs) != 24:
        raise SystemExit("reference config must contain 24 opponent_ids")
    v4_sha = _sha256(args.v4_checkpoint)
    arms = (
        Submission2x2ArmV1(
            arm_id="v4_seed1_x_root_deck",
            policy_kind="v4_seed1",
            policy_id="v4-seed1-wave4",
            policy_sha256=v4_sha,
            deck_path=args.v4_deck,
            deck_sha256=_sha256(args.v4_deck),
            policy_path=args.v4_checkpoint,
            checkpoint_path=args.v4_checkpoint,
            checkpoint_tensor_sha256=args.v4_tensor_sha256,
            subject_archetype_id="archaludon",
        ),
        Submission2x2ArmV1(
            arm_id="rule_v0_x_archaludon_deck",
            policy_kind="rule_v0",
            policy_id="rule-agent-v0",
            policy_sha256=_sha256(args.rule_main),
            deck_path=args.rule_deck,
            deck_sha256=_sha256(args.rule_deck),
            policy_path=args.rule_main,
            subject_archetype_id="archaludon",
        ),
    )
    result = run_submission_2x2_v1(
        arms=arms,
        pool_root=args.pool_root,
        reference_ids=tuple(str(item) for item in refs),
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        output_root=args.output_root,
        execute=args.execute,
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
