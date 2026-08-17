"""Bounded current-R2 champion--challenger orchestration.

This module deliberately does not invent a second collector, learner, or CABT
evaluator.  A caller binds the existing implementations through
``PerformanceSprintHooksV1``.  The orchestration owns only the safety-critical
research protocol: fresh rollouts are consumed once, critic-only warm-up must
not alter the actor, exactly one actor update is allowed, candidate artifacts
are atomically published and re-read in a fresh process, and an independent
both-seat quick screen chooses candidate or baseline.

The result is research evidence, not a promotion decision.  In particular a
small quick screen must never be labelled readiness evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Literal, Sequence


PERFORMANCE_SPRINT_SCHEMA_V1 = "meta-specialist-performance-sprint-v1"
_OUTCOMES_V1 = frozenset({"win", "draw", "loss", "fault"})
_ARMS_V1 = frozenset({"candidate", "baseline"})


class PerformanceSprintV1Error(ValueError):
    """Raised when a bounded sprint would violate its experimental contract."""


@dataclass(frozen=True, slots=True)
class FreshRolloutV1:
    """One collection result, identified independently of its directory name."""

    rollout_id: str
    collection_dir: Path

    def __post_init__(self) -> None:
        if type(self.rollout_id) is not str or not self.rollout_id:
            raise PerformanceSprintV1Error("fresh rollout_id must be a nonempty string")


@dataclass(frozen=True, slots=True)
class EvaluationSlotV1:
    opponent_instance_id: str
    seat: int

    def __post_init__(self) -> None:
        if type(self.opponent_instance_id) is not str or not self.opponent_instance_id:
            raise PerformanceSprintV1Error("evaluation opponent_instance_id must be a nonempty string")
        if type(self.seat) is not int or type(self.seat) is bool or self.seat not in (0, 1):
            raise PerformanceSprintV1Error("evaluation seat must be 0 or 1")


@dataclass(frozen=True, slots=True)
class EvaluationGameV1:
    """One independently played quick-screen game, not a counterfactual pair."""

    arm: Literal["candidate", "baseline"]
    opponent_instance_id: str
    seat: int
    outcome: Literal["win", "draw", "loss", "fault"]

    def __post_init__(self) -> None:
        if self.arm not in _ARMS_V1:
            raise PerformanceSprintV1Error(f"unknown evaluation arm {self.arm!r}")
        EvaluationSlotV1(self.opponent_instance_id, self.seat)
        if self.outcome not in _OUTCOMES_V1:
            raise PerformanceSprintV1Error(f"unknown evaluation outcome {self.outcome!r}")


@dataclass(frozen=True, slots=True)
class PerformanceSprintConfigV1:
    run_dir: Path
    baseline_checkpoint: Path
    training_opponent_instance_ids: tuple[str, ...]
    evaluation_opponent_instance_ids: tuple[str, ...]
    seed_qualification_report: Path
    baseline_strength_status: Literal["UNPROVEN_SMOKE"] = "UNPROVEN_SMOKE"
    value_warmup_updates: int = 1
    minimum_score_delta: float = 0.05

    def __post_init__(self) -> None:
        if not self.baseline_checkpoint.is_file():
            raise PerformanceSprintV1Error(
                f"baseline checkpoint does not exist: {self.baseline_checkpoint}"
            )
        if not self.seed_qualification_report.is_file():
            raise PerformanceSprintV1Error(
                "seed_qualification_report must be an explicit existing path; "
                "do not infer a collector default"
            )
        if self.baseline_strength_status != "UNPROVEN_SMOKE":
            raise PerformanceSprintV1Error(
                "current-R2 performance sprint accepts only UNPROVEN_SMOKE baselines; "
                "historical strong checkpoints are not runtime-compatible"
            )
        _validate_opponent_ids_v1(self.training_opponent_instance_ids, "training")
        _validate_opponent_ids_v1(self.evaluation_opponent_instance_ids, "evaluation")
        overlap = set(self.training_opponent_instance_ids) & set(self.evaluation_opponent_instance_ids)
        if overlap:
            raise PerformanceSprintV1Error(
                "evaluation opponents must be instances independent from training opponents: "
                + ", ".join(sorted(overlap))
            )
        if type(self.value_warmup_updates) is not int or type(self.value_warmup_updates) is bool or self.value_warmup_updates < 1:
            raise PerformanceSprintV1Error("value_warmup_updates must be a positive int")
        if type(self.minimum_score_delta) not in (int, float) or isinstance(self.minimum_score_delta, bool):
            raise PerformanceSprintV1Error("minimum_score_delta must be a finite number")
        if not float("-inf") < float(self.minimum_score_delta) < float("inf"):
            raise PerformanceSprintV1Error("minimum_score_delta must be a finite number")
        if float(self.minimum_score_delta) <= 0.0:
            raise PerformanceSprintV1Error("minimum_score_delta must require a strict positive improvement")


CollectFreshV1 = Callable[[str, int, Path], FreshRolloutV1]
ValueWarmupV1 = Callable[[Path, FreshRolloutV1], Path]
ActorUpdateV1 = Callable[[Path, FreshRolloutV1], Path]
EvaluateV1 = Callable[[Path, Path, tuple[EvaluationSlotV1, ...]], Sequence[EvaluationGameV1]]
ActorStateShaV1 = Callable[[Path], str]
RuntimePolicyPreflightV1 = Callable[[Path], None]


@dataclass(frozen=True, slots=True)
class PerformanceSprintHooksV1:
    """Thin adapters to the existing collector/train/V-trace/evaluation layers.

    ``warmup_value_head`` should call the existing trajectory trainer with only
    ``value_head`` trainable. ``actor_update`` should call it once with the
    normal V-trace objective. The separate actor-state digest makes an adapter
    prove that the former did not inadvertently update the actor.
    """

    collect_fresh: CollectFreshV1
    warmup_value_head: ValueWarmupV1
    actor_update: ActorUpdateV1
    evaluate: EvaluateV1
    actor_state_sha256: ActorStateShaV1
    runtime_policy_preflight: RuntimePolicyPreflightV1


@dataclass(frozen=True, slots=True)
class PerformanceSprintResultV1:
    run_dir: Path
    baseline_checkpoint: Path
    challenger_checkpoint: Path
    selected_checkpoint: Path
    challenger_sha256: str
    reloaded_sha256: str
    consumed_rollout_ids: tuple[str, ...]
    candidate_score: float
    baseline_score: float
    promoted: bool
    rollback_applied: bool


def run_performance_sprint_v1(
    config: PerformanceSprintConfigV1,
    hooks: PerformanceSprintHooksV1,
) -> PerformanceSprintResultV1:
    """Run the bounded warm-up -> one actor update -> independent screen flow."""
    config.run_dir.mkdir(parents=True, exist_ok=True)
    baseline = config.baseline_checkpoint.resolve()
    _runtime_policy_preflight_v1(hooks, baseline)
    actor_digest = _actor_digest_v1(hooks, baseline)
    active_checkpoint = baseline
    consumed: set[str] = set()
    consumed_order: list[str] = []

    for ordinal in range(config.value_warmup_updates):
        rollout = _collect_once_v1(hooks, consumed, consumed_order, "value-warmup", ordinal, active_checkpoint)
        warmed = _required_checkpoint_v1(hooks.warmup_value_head(active_checkpoint, rollout), "value warm-up")
        warmed_digest = _actor_digest_v1(hooks, warmed)
        if warmed_digest != actor_digest:
            raise PerformanceSprintV1Error(
                "value warm-up changed actor parameters; only value_head may be updated"
            )
        active_checkpoint = warmed

    rollout = _collect_once_v1(hooks, consumed, consumed_order, "actor", 0, active_checkpoint)
    challenger_source = _required_checkpoint_v1(hooks.actor_update(active_checkpoint, rollout), "actor update")
    challenger, challenger_sha256 = _publish_checkpoint_atomically_v1(config.run_dir, challenger_source)
    reloaded_sha256 = _fresh_process_sha256_v1(challenger)
    if reloaded_sha256 != challenger_sha256:
        raise PerformanceSprintV1Error("fresh-process checkpoint reload hash does not match published artifact")
    _runtime_policy_preflight_v1(hooks, challenger)

    slots = tuple(
        EvaluationSlotV1(opponent, seat)
        for opponent in config.evaluation_opponent_instance_ids
        for seat in (0, 1)
    )
    evaluation = _validate_quick_screen_v1(hooks.evaluate(challenger, baseline, slots), slots)
    candidate_score = _score_arm_v1(evaluation, "candidate")
    baseline_score = _score_arm_v1(evaluation, "baseline")
    promoted = candidate_score - baseline_score >= float(config.minimum_score_delta)
    selected = challenger if promoted else baseline
    rollback = not promoted
    _atomic_write_json_v1(config.run_dir / "decision.json", {
        "schema_version": PERFORMANCE_SPRINT_SCHEMA_V1,
        "research_only": True,
        "baseline_checkpoint": str(baseline),
        "baseline_strength_status": config.baseline_strength_status,
        "seed_qualification_report": str(config.seed_qualification_report.resolve()),
        "challenger_checkpoint": str(challenger),
        "challenger_sha256": challenger_sha256,
        "reloaded_sha256": reloaded_sha256,
        "consumed_rollout_ids": consumed_order,
        "evaluation": {
            "candidate_score": candidate_score,
            "baseline_score": baseline_score,
            "slots": [{"opponent_instance_id": slot.opponent_instance_id, "seat": slot.seat} for slot in slots],
            "independent_from_training_opponents": True,
        },
        "promoted": promoted,
        "rollback_applied": rollback,
        "selected_checkpoint": str(selected),
    })
    return PerformanceSprintResultV1(
        run_dir=config.run_dir, baseline_checkpoint=baseline, challenger_checkpoint=challenger,
        selected_checkpoint=selected, challenger_sha256=challenger_sha256,
        reloaded_sha256=reloaded_sha256, consumed_rollout_ids=tuple(consumed_order),
        candidate_score=candidate_score, baseline_score=baseline_score, promoted=promoted,
        rollback_applied=rollback,
    )


def checkpoint_actor_state_sha256_v1(path: Path) -> str:
    """Digest all model tensors except ``value_head`` for current-R2 adapters."""
    import torch
    from mage_ptcg.meta_specialist.neural_checkpoint_v1 import load_checkpoint_for_inference_v1

    source = _required_checkpoint_v1(path, "actor digest")
    content_hash = _sha256_file_v1(source)
    try:
        payload = load_checkpoint_for_inference_v1(source, expected_content_hash=content_hash)
        state = payload["model"]
    except Exception as exc:
        raise PerformanceSprintV1Error(f"could not read current-R2 checkpoint {source}: {exc}") from exc
    if not isinstance(state, dict):
        raise PerformanceSprintV1Error("current-R2 checkpoint has no model state_dict")
    digest = hashlib.sha256(b"mage-ptcg:actor-state:v1\0")
    for name in sorted(state):
        if name.startswith("value_head."):
            continue
        value = state[name]
        if not isinstance(value, torch.Tensor):
            raise PerformanceSprintV1Error(f"model state {name!r} is not a tensor")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(repr(tuple(value.shape)).encode("ascii"))
        digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def make_runtime_policy_preflight_current_r2_v1(
    *, checkpoint_lineage_id: str,
) -> RuntimePolicyPreflightV1:
    """Build the required actual-current-R2 loader preflight callback.

    Drivers should use this helper (or the actor-pool factory which follows the
    same path).  It intentionally does not treat payload parsing as evidence
    that the checkpoint matches the currently installed model topology.
    """
    if type(checkpoint_lineage_id) is not str or not checkpoint_lineage_id:
        raise PerformanceSprintV1Error("checkpoint_lineage_id must be a nonempty string")

    def preflight(checkpoint: Path) -> None:
        from mage_ptcg.meta_specialist.actor_pool_v1 import neural_checkpoint_behavior_identity_v1
        from mage_ptcg.meta_specialist.neural_policy_v1 import load_specialist_neural_policy_from_checkpoint_v1

        identity = neural_checkpoint_behavior_identity_v1(checkpoint)
        load_specialist_neural_policy_from_checkpoint_v1(
            checkpoint, expected_content_hash=identity,
            checkpoint_lineage_id=checkpoint_lineage_id,
        )

    return preflight


def fresh_rollout_from_collection_summary_v1(
    summary_path: Path,
    *,
    expected_behavior_identity: str,
) -> FreshRolloutV1:
    """Validate a completed collector summary before consuming it as one rollout.

    This is intentionally a pure read-side check: it neither resumes nor edits a
    collection.  A prior run may be consumed once by this sprint, but incomplete,
    faulted, timed-out, or resumed/skipped summaries are refused rather than
    silently treated as fresh data.
    """
    if not isinstance(summary_path, Path) or not summary_path.is_file():
        raise PerformanceSprintV1Error("collection summary must be an existing explicit path")
    if type(expected_behavior_identity) is not str or len(expected_behavior_identity) != 64:
        raise PerformanceSprintV1Error("expected_behavior_identity must be a SHA-256 digest")
    try:
        raw = summary_path.read_bytes()
        summary = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise PerformanceSprintV1Error(f"could not read collection summary {summary_path}: {exc}") from exc
    if not isinstance(summary, dict) or summary.get("schema_version") != "meta-specialist-collect-trajectories-run-summary-v1":
        raise PerformanceSprintV1Error("collection summary has an unrecognized schema")
    if summary.get("behavior_kind") != "neural_specialist":
        raise PerformanceSprintV1Error("collection summary is not a neural-specialist rollout")
    if summary.get("behavior_identity") != expected_behavior_identity:
        raise PerformanceSprintV1Error("collection summary behavior identity does not match checkpoint")
    requested = summary.get("num_games_requested")
    completed = summary.get("games_completed")
    if type(requested) is not int or type(requested) is bool or requested < 1 or completed != requested:
        raise PerformanceSprintV1Error("collection summary is not a complete fresh collection")
    if summary.get("games_faulted") != 0 or summary.get("games_timeout") != 0:
        raise PerformanceSprintV1Error("collection summary is not fault-free and timeout-free")
    if summary.get("games_resumed_skipped") != 0:
        raise PerformanceSprintV1Error("collection summary contains resumed/skipped games and is not fresh")
    _require_both_collector_seats_v1(summary.get("per_lane"))
    games_dir = summary.get("games_dir")
    if type(games_dir) is not str or not games_dir:
        raise PerformanceSprintV1Error("collection summary has no games_dir")
    return FreshRolloutV1(
        rollout_id="fresh-collection:" + hashlib.sha256(raw).hexdigest(),
        collection_dir=Path(games_dir),
    )


def _validate_opponent_ids_v1(values: tuple[str, ...], label: str) -> None:
    if not values or any(type(value) is not str or not value for value in values):
        raise PerformanceSprintV1Error(f"{label}_opponent_instance_ids must be nonempty strings")
    if len(set(values)) != len(values):
        raise PerformanceSprintV1Error(f"{label}_opponent_instance_ids must not repeat an instance")


def _require_both_collector_seats_v1(per_lane: object) -> None:
    if not isinstance(per_lane, dict) or not per_lane:
        raise PerformanceSprintV1Error("collection summary has no per-lane seat evidence")
    for lane, payload in per_lane.items():
        if type(lane) is not str or not isinstance(payload, dict):
            raise PerformanceSprintV1Error("collection summary per-lane evidence is malformed")
        seats = payload.get("seats")
        if not isinstance(seats, dict):
            raise PerformanceSprintV1Error("collection summary has no both-seat evidence")
        for seat in ("0", "1"):
            evidence = seats.get(seat)
            if not isinstance(evidence, dict) or evidence.get("collected", 0) < 1:
                raise PerformanceSprintV1Error("collection summary has no both-seat evidence")


def _required_checkpoint_v1(path: Path, stage: str) -> Path:
    if not isinstance(path, Path) or not path.is_file():
        raise PerformanceSprintV1Error(f"{stage} did not return an existing checkpoint path")
    return path.resolve()


def _actor_digest_v1(hooks: PerformanceSprintHooksV1, checkpoint: Path) -> str:
    value = hooks.actor_state_sha256(checkpoint)
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PerformanceSprintV1Error("actor_state_sha256 must return a lowercase SHA-256 digest")
    return value


def _runtime_policy_preflight_v1(hooks: PerformanceSprintHooksV1, checkpoint: Path) -> None:
    """Require the actual inference loader, not only a checkpoint payload read."""
    try:
        hooks.runtime_policy_preflight(checkpoint)
    except Exception as exc:
        raise PerformanceSprintV1Error(
            f"runtime policy preflight failed for {checkpoint}: {exc}"
        ) from exc


def _collect_once_v1(
    hooks: PerformanceSprintHooksV1,
    consumed: set[str],
    consumed_order: list[str],
    phase: str,
    ordinal: int,
    checkpoint: Path,
) -> FreshRolloutV1:
    rollout = hooks.collect_fresh(phase, ordinal, checkpoint)
    if not isinstance(rollout, FreshRolloutV1):
        raise PerformanceSprintV1Error("collect_fresh must return FreshRolloutV1")
    if rollout.rollout_id in consumed:
        raise PerformanceSprintV1Error(f"fresh rollout {rollout.rollout_id!r} was already consumed")
    consumed.add(rollout.rollout_id)
    consumed_order.append(rollout.rollout_id)
    return rollout


def _publish_checkpoint_atomically_v1(run_dir: Path, source: Path) -> tuple[Path, str]:
    content_hash = _sha256_file_v1(source)
    destination = run_dir / "checkpoints" / f"checkpoint-{content_hash}.pt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=".checkpoint.tmp.", dir=destination.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            with Path(temporary).open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    if _sha256_file_v1(destination) != content_hash:
        raise PerformanceSprintV1Error("published checkpoint bytes differ from actor-update artifact")
    return destination, content_hash


def _fresh_process_sha256_v1(path: Path) -> str:
    code = "import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())"
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)], check=False, capture_output=True, text=True,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or len(value) != 64:
        raise PerformanceSprintV1Error("fresh-process checkpoint reload failed")
    return value


def _validate_quick_screen_v1(
    records: Sequence[EvaluationGameV1], slots: tuple[EvaluationSlotV1, ...]
) -> tuple[EvaluationGameV1, ...]:
    expected = {(arm, slot.opponent_instance_id, slot.seat) for arm in _ARMS_V1 for slot in slots}
    observed: set[tuple[str, str, int]] = set()
    normalized: list[EvaluationGameV1] = []
    for record in records:
        if not isinstance(record, EvaluationGameV1):
            raise PerformanceSprintV1Error("evaluate must return EvaluationGameV1 records")
        key = (record.arm, record.opponent_instance_id, record.seat)
        if key in observed:
            raise PerformanceSprintV1Error("quick screen repeats an arm/opponent/seat game")
        observed.add(key)
        normalized.append(record)
    if observed != expected:
        raise PerformanceSprintV1Error("quick screen must cover both arms for every independent opponent and both seats")
    if any(record.outcome == "fault" for record in normalized):
        raise PerformanceSprintV1Error("quick screen must be fault-free before scores are compared")
    return tuple(normalized)


def _score_arm_v1(records: Sequence[EvaluationGameV1], arm: str) -> float:
    scores = {"win": 1.0, "draw": 0.5, "loss": 0.0, "fault": 0.0}
    selected = [scores[record.outcome] for record in records if record.arm == arm]
    return sum(selected) / len(selected)


def _sha256_file_v1(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json_v1(path: Path, payload: dict[str, object]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


__all__ = [
    "EvaluationGameV1", "EvaluationSlotV1", "FreshRolloutV1", "PERFORMANCE_SPRINT_SCHEMA_V1",
    "PerformanceSprintConfigV1", "PerformanceSprintHooksV1", "PerformanceSprintResultV1",
    "PerformanceSprintV1Error", "checkpoint_actor_state_sha256_v1",
    "fresh_rollout_from_collection_summary_v1", "make_runtime_policy_preflight_current_r2_v1",
    "run_performance_sprint_v1",
]
