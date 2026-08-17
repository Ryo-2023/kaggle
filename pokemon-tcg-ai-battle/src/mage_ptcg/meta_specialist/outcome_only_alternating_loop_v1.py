"""One bounded, outcome-only deck/policy alternating iteration.

The stage runner already owns paired candidate/control evaluation and its
successive-halving gate.  This module adds the missing orchestration boundary:
one short deck phase followed, only after a positive result, by one policy
phase on the frozen deck.  It is intentionally research-only; it does not
train, promote, submit, or start an unbounded loop.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

from .outcome_only_alternating_runtime_v1 import (
    ALTERNATING_STAGE_GAMES_V1,
    AUTHORITY_FALSE_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1 as _RUNTIME_DEFAULT_WORKER_RECYCLE_GAMES_V1,
    DEFAULT_MAX_WORKERS_V1 as _RUNTIME_DEFAULT_WORKERS_V1,
    DECK_FIXED_LONG_V1,
    OutcomeOnlyCandidateSpecV1,
    POLICY_FIXED_SHORT_V1,
    run_alternating_stage_v1,
)


OUTCOME_ONLY_ALTERNATING_LOOP_SCHEMA_V1 = "meta-specialist-outcome-only-alternating-loop-v1"
DEFAULT_WORKERS_V1 = _RUNTIME_DEFAULT_WORKERS_V1
DEFAULT_WORKER_RECYCLE_GAMES_V1 = _RUNTIME_DEFAULT_WORKER_RECYCLE_GAMES_V1


class AlternatingLoopError(ValueError):
    """Raised when an iteration cannot prove its fixed-dimension contract."""


@dataclass(frozen=True, slots=True)
class AlternatingLoopPairV1:
    """One paired stage and the identity it is allowed to change."""

    phase: str
    candidate: OutcomeOnlyCandidateSpecV1
    control: OutcomeOnlyCandidateSpecV1
    stage_games: int

    @property
    def authority_false(self) -> bool:
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "candidate_id": self.candidate.candidate_id,
            "control_id": self.control.candidate_id,
            "candidate_policy_sha256": self.candidate.policy_sha256,
            "candidate_config_sha256": self.candidate.config_sha256,
            "candidate_deck_sha256": self.candidate.deck_sha256,
            "control_policy_sha256": self.control.policy_sha256,
            "control_config_sha256": self.control.config_sha256,
            "control_deck_sha256": self.control.deck_sha256,
            "stage_games": self.stage_games,
            "authority": dict(AUTHORITY_FALSE_V1),
            "research_only": True,
        }


def _identity(spec: OutcomeOnlyCandidateSpecV1) -> tuple[str, str]:
    return spec.policy_sha256, spec.config_sha256


def validate_alternating_pair_v1(
    *,
    phase: str,
    candidate: OutcomeOnlyCandidateSpecV1,
    control: OutcomeOnlyCandidateSpecV1,
    stage_games: int,
) -> AlternatingLoopPairV1:
    """Validate which side of an alternating phase is allowed to change."""

    if type(candidate) is not OutcomeOnlyCandidateSpecV1 or type(control) is not OutcomeOnlyCandidateSpecV1:
        raise AlternatingLoopError("candidate and control must be OutcomeOnlyCandidateSpecV1")
    if candidate.candidate_id == control.candidate_id:
        raise AlternatingLoopError("candidate and control IDs must differ")
    if phase not in {POLICY_FIXED_SHORT_V1, DECK_FIXED_LONG_V1}:
        raise AlternatingLoopError(f"unsupported alternating phase: {phase}")
    if stage_games not in ALTERNATING_STAGE_GAMES_V1:
        raise AlternatingLoopError("stage_games is outside the successive-halving sequence")
    if phase == POLICY_FIXED_SHORT_V1:
        if _identity(candidate) != _identity(control):
            raise AlternatingLoopError("policy-fixed phase cannot change policy identity")
        if candidate.deck_sha256 == control.deck_sha256:
            raise AlternatingLoopError("policy-fixed phase requires a deck change")
    else:
        if candidate.deck_sha256 != control.deck_sha256:
            raise AlternatingLoopError("deck-fixed phase requires the frozen deck")
        if _identity(candidate) == _identity(control):
            raise AlternatingLoopError("deck-fixed phase requires a policy identity change")
    return AlternatingLoopPairV1(
        phase=phase,
        candidate=candidate,
        control=control,
        stage_games=stage_games,
    )


def build_alternating_iteration_plan_v1(
    *,
    deck_candidate: OutcomeOnlyCandidateSpecV1,
    native_control: OutcomeOnlyCandidateSpecV1,
    policy_candidate: OutcomeOnlyCandidateSpecV1,
    policy_control: OutcomeOnlyCandidateSpecV1,
    stage_games: int = 96,
) -> tuple[AlternatingLoopPairV1, AlternatingLoopPairV1]:
    """Build the two phases without starting an evaluator.

    The deck phase compares the child deck to the current control while the
    policy is fixed.  The policy phase compares two policies on that exact
    child deck.  A caller may execute the second phase only after the first
    stage reports ``POSITIVE_CONTINUE``.
    """

    deck_pair = validate_alternating_pair_v1(
        phase=POLICY_FIXED_SHORT_V1,
        candidate=deck_candidate,
        control=native_control,
        stage_games=stage_games,
    )
    if policy_candidate.deck_sha256 != deck_candidate.deck_sha256:
        raise AlternatingLoopError("policy phase must reuse the frozen deck candidate")
    if policy_control.deck_sha256 != deck_candidate.deck_sha256:
        raise AlternatingLoopError("policy control must reuse the frozen deck candidate")
    policy_pair = validate_alternating_pair_v1(
        phase=DECK_FIXED_LONG_V1,
        candidate=policy_candidate,
        control=policy_control,
        stage_games=stage_games,
    )
    return deck_pair, policy_pair


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write_json_no_clobber(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(payload) + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_name, path)
        os.unlink(temp_name)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def run_alternating_iteration_v1(
    *,
    deck_candidate: OutcomeOnlyCandidateSpecV1,
    native_control: OutcomeOnlyCandidateSpecV1,
    policy_candidate: OutcomeOnlyCandidateSpecV1,
    policy_control: OutcomeOnlyCandidateSpecV1,
    pool_root: Path | str,
    reference_ids: Sequence[str],
    output_root: Path | str,
    base_seed: int,
    stage_games: int = 96,
    block_id: str = "outcome-only-alternating-loop-v1",
    execute: bool = False,
    workers: int = DEFAULT_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
) -> dict[str, object]:
    """Execute at most one bounded deck→policy iteration.

    The policy phase is not started for a dry run or a non-positive deck
    phase.  This makes a loop restartable and prevents a weak first phase from
    silently consuming the larger policy budget.
    """

    plan = build_alternating_iteration_plan_v1(
        deck_candidate=deck_candidate,
        native_control=native_control,
        policy_candidate=policy_candidate,
        policy_control=policy_control,
        stage_games=stage_games,
    )
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise AlternatingLoopError(f"iteration output root must be fresh: {root}")
    root.mkdir(parents=True, exist_ok=False)
    deck_result = run_alternating_stage_v1(
        candidate=plan[0].candidate,
        native_control=plan[0].control,
        pool_root=Path(pool_root),
        reference_ids=tuple(reference_ids),
        stage_games=plan[0].stage_games,
        base_seed=base_seed,
        block_id=f"{block_id}:deck",
        output_root=root / "policy-fixed-short",
        execute=execute,
        phase=POLICY_FIXED_SHORT_V1,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    policy_result: dict[str, object] | None = None
    if execute and deck_result.get("decision") == "POSITIVE_CONTINUE":
        policy_result = run_alternating_stage_v1(
            candidate=plan[1].candidate,
            native_control=plan[1].control,
            pool_root=Path(pool_root),
            reference_ids=tuple(reference_ids),
            stage_games=plan[1].stage_games,
            base_seed=base_seed + stage_games * 2,
            block_id=f"{block_id}:policy",
            output_root=root / "deck-fixed-long",
            execute=True,
            phase=DECK_FIXED_LONG_V1,
            workers=workers,
            worker_recycle_games=worker_recycle_games,
        )
    payload: dict[str, object] = {
        "schema_version": OUTCOME_ONLY_ALTERNATING_LOOP_SCHEMA_V1,
        "status": "COMPLETE" if execute else "DRY_RUN",
        "execute": bool(execute),
        "stage_games": stage_games,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "plan": [item.to_dict() for item in plan],
        "deck_phase": deck_result,
        "policy_phase": policy_result,
        "policy_phase_started": policy_result is not None,
        "next_action": (
            "manual_successive_halving_required"
            if policy_result is not None and policy_result.get("decision") == "POSITIVE_CONTINUE"
            else "stop_or_review"
        ),
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    payload["iteration_sha256"] = hashlib.sha256(
        b"mage-ptcg:outcome-only-alternating-loop:v1\0" + _canonical(payload)
    ).hexdigest()
    payload["iteration_file_sha256"] = _write_json_no_clobber(root / "iteration.json", payload)
    return payload


__all__ = [
    "AlternatingLoopError",
    "AlternatingLoopPairV1",
    "DEFAULT_WORKERS_V1",
    "DEFAULT_WORKER_RECYCLE_GAMES_V1",
    "DECK_FIXED_LONG_V1",
    "OUTCOME_ONLY_ALTERNATING_LOOP_SCHEMA_V1",
    "POLICY_FIXED_SHORT_V1",
    "build_alternating_iteration_plan_v1",
    "run_alternating_iteration_v1",
    "validate_alternating_pair_v1",
]
