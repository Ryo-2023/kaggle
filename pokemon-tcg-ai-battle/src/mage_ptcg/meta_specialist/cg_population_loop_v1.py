"""Bounded checkpoint contract for the META_TRAIN cg successive-halving loop.

The module deliberately contains orchestration state only.  It does not grant
training, behavior-collection, promotion, submission, or an unbounded runtime
loop; a caller must invoke the existing population stage runner for each
planned stage and persist the returned summary here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping

from .cg_alternating_runtime_v1 import CG_STAGE_GAMES_V1


SCHEMA_V1 = "meta-specialist-cg-population-loop-checkpoint-v1"
AUTHORITY_FALSE_V1 = {
    "training": False,
    "promotion": False,
    "submission": False,
    "longrun": False,
}
_STAGE_OFFSETS = {96: 0, 384: 100_000, 768: 200_000, 1536: 300_000}


class CgPopulationLoopError(ValueError):
    """Raised when bounded loop state is malformed or grants authority."""


@dataclass(frozen=True, slots=True)
class CgPopulationLoopPlanV1:
    phase: str
    base_seed: int
    stage_games: tuple[int, ...]
    authority: Mapping[str, bool]
    research_only: bool

    def __post_init__(self) -> None:
        if not self.stage_games or any(stage not in CG_STAGE_GAMES_V1 for stage in self.stage_games):
            raise CgPopulationLoopError("loop stages must be a non-empty cg stage subsequence")
        if tuple(sorted(self.stage_games)) != self.stage_games:
            raise CgPopulationLoopError("loop stages must be increasing")
        if dict(self.authority) != AUTHORITY_FALSE_V1 or not self.research_only:
            raise CgPopulationLoopError("population loop grants forbidden authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_V1,
            "phase": self.phase,
            "base_seed": self.base_seed,
            "stage_games": list(self.stage_games),
            "authority": dict(self.authority),
            "research_only": self.research_only,
        }


def stage_seed_v1(*, base_seed: int, stage_games: int) -> int:
    if type(base_seed) is not int or base_seed < 0:
        raise CgPopulationLoopError("base_seed must be a non-negative integer")
    if stage_games not in _STAGE_OFFSETS:
        raise CgPopulationLoopError("stage_games is outside the successive-halving sequence")
    return base_seed + _STAGE_OFFSETS[stage_games]


def build_population_loop_plan_v1(
    *, base_seed: int, start_stage_games: int, max_stage_games: int, phase: str
) -> CgPopulationLoopPlanV1:
    if start_stage_games not in CG_STAGE_GAMES_V1 or max_stage_games not in CG_STAGE_GAMES_V1:
        raise CgPopulationLoopError("start/max stage is outside the successive-halving sequence")
    if CG_STAGE_GAMES_V1.index(start_stage_games) > CG_STAGE_GAMES_V1.index(max_stage_games):
        raise CgPopulationLoopError("max_stage_games must not precede start_stage_games")
    if type(phase) is not str or not phase:
        raise CgPopulationLoopError("phase must be non-empty")
    stages = tuple(
        stage
        for stage in CG_STAGE_GAMES_V1
        if CG_STAGE_GAMES_V1.index(start_stage_games)
        <= CG_STAGE_GAMES_V1.index(stage)
        <= CG_STAGE_GAMES_V1.index(max_stage_games)
    )
    return CgPopulationLoopPlanV1(
        phase=phase,
        base_seed=base_seed,
        stage_games=stages,
        authority=dict(AUTHORITY_FALSE_V1),
        research_only=True,
    )


def _validate_checkpoint(payload: Mapping[str, object]) -> dict[str, object]:
    if payload.get("schema_version") != SCHEMA_V1:
        raise CgPopulationLoopError("checkpoint schema mismatch")
    if payload.get("status") not in {"RUNNING", "STOP", "COMPLETE"}:
        raise CgPopulationLoopError("checkpoint status is invalid")
    if payload.get("authority") != AUTHORITY_FALSE_V1:
        raise CgPopulationLoopError("checkpoint grants forbidden authority")
    if payload.get("research_only") is not True:
        raise CgPopulationLoopError("checkpoint must be research-only")
    return dict(payload)


def save_population_loop_checkpoint_v1(payload: Mapping[str, object], path: Path | str) -> str:
    if not isinstance(payload, Mapping):
        raise CgPopulationLoopError("checkpoint must be a mapping")
    checked = _validate_checkpoint(payload)
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite checkpoint: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def load_population_loop_checkpoint_v1(path: Path | str) -> dict[str, object]:
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CgPopulationLoopError(f"checkpoint is unreadable: {target}") from exc
    if not isinstance(raw, Mapping):
        raise CgPopulationLoopError("checkpoint root must be an object")
    return _validate_checkpoint(raw)


def run_population_loop_v1(
    *,
    plan: CgPopulationLoopPlanV1,
    stage_runner: Callable[..., Mapping[str, object]],
    output_root: Path | str,
    execute: bool,
    workers: int = 12,
) -> dict[str, object]:
    """Run bounded successive-halving stages and persist rollback points.

    ``stage_runner`` is normally a closure around
    ``scripts.run_cg_population_alternating_v1.run_population_stage_v1``.  It
    is injected so this state machine remains independent from package
    loading, while every real stage still uses the existing evaluator.  A
    dry-run materializes only the first stage; an execute run advances only
    after an exact ``POSITIVE_CONTINUE`` decision.
    """

    if not isinstance(plan, CgPopulationLoopPlanV1):
        raise CgPopulationLoopError("plan must be CgPopulationLoopPlanV1")
    if not callable(stage_runner):
        raise CgPopulationLoopError("stage_runner must be callable")
    if type(workers) is not int or workers != 12:
        raise CgPopulationLoopError("population loop is sealed to workers=12")
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"refusing to overwrite population loop root: {root}")
    root.mkdir(parents=True, exist_ok=False)
    checkpoints_root = root / "checkpoints"
    checkpoints_root.mkdir()
    checkpoints: list[str] = []
    previous_checkpoint: str | None = None
    last_decision: str | None = None
    last_result: Mapping[str, object] | None = None
    for stage_games in plan.stage_games:
        recycle = 16 if stage_games == 96 else 64
        stage_output = root / f"stage-{stage_games}"
        result = dict(
            stage_runner(
                stage_games=stage_games,
                base_seed=stage_seed_v1(base_seed=plan.base_seed, stage_games=stage_games),
                output_root=stage_output,
                execute=execute,
                workers=workers,
                worker_recycle_games=recycle,
            )
        )
        summary = result.get("summary")
        decision = summary.get("decision") if isinstance(summary, Mapping) else None
        if not isinstance(decision, str):
            decision = "DRY_RUN" if not execute else "INVALID_RESULT"
        checkpoint: dict[str, object] = {
            "schema_version": SCHEMA_V1,
            "status": "RUNNING" if execute and decision == "POSITIVE_CONTINUE" else "STOP",
            "phase": plan.phase,
            "base_seed": plan.base_seed,
            "stage_games": stage_games,
            "stage_seed": stage_seed_v1(base_seed=plan.base_seed, stage_games=stage_games),
            "decision": decision,
            "stage_result": result,
            "rollback_to": previous_checkpoint,
            "authority": dict(AUTHORITY_FALSE_V1),
            "research_only": True,
        }
        checkpoint_path = checkpoints_root / f"checkpoint-{stage_games}.json"
        save_population_loop_checkpoint_v1(checkpoint, checkpoint_path)
        previous_checkpoint = str(checkpoint_path)
        checkpoints.append(previous_checkpoint)
        last_decision = decision
        last_result = result
        if not execute or decision != "POSITIVE_CONTINUE":
            break
    if last_decision == "POSITIVE_CONTINUE" and len(checkpoints) == len(plan.stage_games):
        status = "COMPLETE"
    else:
        status = "DRY_RUN" if not execute else "STOP"
    return {
        "schema_version": SCHEMA_V1,
        "status": status,
        "phase": plan.phase,
        "stages_planned": list(plan.stage_games),
        "stages_completed": [int(Path(path).stem.removeprefix("checkpoint-")) for path in checkpoints],
        "last_decision": last_decision,
        "last_result": dict(last_result) if last_result is not None else None,
        "checkpoints": checkpoints,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }


__all__ = [
    "AUTHORITY_FALSE_V1",
    "CgPopulationLoopError",
    "CgPopulationLoopPlanV1",
    "build_population_loop_plan_v1",
    "load_population_loop_checkpoint_v1",
    "run_population_loop_v1",
    "save_population_loop_checkpoint_v1",
    "stage_seed_v1",
]
