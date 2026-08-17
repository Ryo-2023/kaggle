"""Human-runnable training entry point: real collected trajectories -> real optimizer steps.

Everything this module needs already exists and is committed: ``collect_trajectories_v1``
writes real ``games/*/record.json`` game records, ``trajectory_target_v1`` recomputes a
stored complete action's masked log-probability under the current model,
``vtrace_bridge_v1`` turns a game record's transitions into an unnormalized V-trace loss,
and ``neural_checkpoint_v1`` publishes/restores a content-addressed checkpoint with strict
identity.  The whole loop was verified by hand on a real 4-game, 96-transition collection
(``runs/meta-specialist-actor-pool/cli-smoke-test-alakazam-4``):
``tests/meta_specialist/test_trajectory_target_v1.py::test_collected_trajectories_drive_a_real_optimizer_step``
takes one real optimizer step this way and confirms every parameter moves.  This module is
that loop turned into a real, resumable, multi-step, progress-reporting entry point --
mirroring ``collect_trajectories_v1.py``'s structure (argument style, progress reporting,
summary-JSON discipline) for the training side.

What one call does
-------------------
1. Read every ``<collection-run-dir>/games/*/record.json``, fully re-validating each through
   ``actor_pool_v1.read_actor_pool_game_record_v1`` (the same read-side authority collection
   itself uses).  A record that fails to read/validate is excluded with a recorded reason --
   never silently skipped, never fabricated as if it were valid.
2. Admit the readable records by pool-epoch age window via ``vtrace_bridge_v1.admit_trajectories_v1``,
   reporting admitted/dropped counts and reasons.
3. Build (or restore) one ``SpecialistPolicyModelV1`` against the production card vocabulary
   (``card_vocabulary_registry_v1.load_production_card_vocabulary_v1`` -- the same one
   ``actor_pool_v1`` uses; never a substitute vocabulary), and one optimizer.
4. Take real optimizer steps up to an explicit ``--max-steps`` budget, one step per minibatch
   of trajectories, honoring "accumulate unnormalized, divide once" so a minibatch that has to
   be processed in smaller microbatches (OOM defense) still yields the exact same step a
   single whole-minibatch evaluation would.  A non-finite loss or gradient skips that one step
   (reported, not hidden); the step counter still advances so an explicit budget always
   terminates.
5. Publish a checkpoint via ``neural_checkpoint_v1.publish_checkpoint_v1`` every
   ``--checkpoint-interval-steps`` steps and once more at the end, and write a small
   ``latest_checkpoint.json`` pointer so the *next* invocation of the same command can find
   and resume from it.
6. Emit one canonical run-summary JSON (steps taken, transitions consumed, loss trajectory,
   gradient norms, checkpoint path + content hash, wall time) and print one human-readable
   summary line to stderr, mirroring ``collect_trajectories_v1``.

Resumability
------------
``<output-root>/latest_checkpoint.json`` is the sole resume marker for one ``--run-name``.  If
it exists, its recorded ``training_identity`` (snapshot/model-topology/recipe/seed) must match
this invocation's *freshly recomputed* identity exactly -- a mismatch fails closed rather than
silently adapting or discarding history (mirrors ``neural_checkpoint_v1``'s own discipline: "a
checkpoint from another identity is refused rather than adapted").  On a match, the checkpoint
is loaded and restored (model, optimizer, CPU RNG, step count, minibatch sampler cursor) and
training continues from the stored step; re-running the identical command with the same
``--max-steps`` takes zero further steps rather than restarting from a fresh model.

The critic exists and is live here; θ0 ships it untrained (the real gap)
--------------------------------------------------------------------------------
Correction history, because two earlier versions of this note were both wrong:

1. It once said "no value head exists anywhere in this codebase".  False:
   ``neural_model_v1.SpecialistPolicyModelV1`` defines ``value_head``.
2. The correction then said the head "is never trained and never read" and that
   "what runs here is not V-trace".  Also false, and the more damaging error:
   this loop *does* pass ``state_value`` into ``evaluate_trajectory_loss_v1``
   (see ``scorer.value`` -> ``trajectory_target_v1.value`` ->
   ``model.state_value_from_state``), so V-trace here uses the **current
   learner's** V(x), the value loss carries real gradient, and the policy
   gradient has a real baseline.  The grep that produced that claim searched for
   the literal ``value_head`` and missed the call going through
   ``state_value_from_state``.

The remaining gap that this uncovered has since been closed: BC used to leave
the value head at its random initialisation, so a θ0 handed this loop a critic
that predicted nothing and V-trace had to learn the baseline from scratch while
also improving the policy (the regime
``docs/evidence/vtrace-degenerate-collapse-20260804.md`` records).
``run_bc_distillation.py`` now fits the head against the snapshots'
``value_target`` by default (``--value-coefficient 0.5``), so θ0 arrives with a
calibrated baseline.

``value_target`` is the *undiscounted* terminal outcome, which matches the
collection default ``--non-terminal-discount 1.0``.  Lowering that discount
without refitting the critic against discounted returns would leave the baseline
systematically wrong; ``test_value_head_gap_v1`` pins the two together.

The entropy term is wired and on by default at 0.01 (an earlier version of this
note wrongly said it "is fixed at 0.0"); pass ``--entropy-coefficient 0`` to
disable it.

The stored per-transition ``value`` field remains the collection-time
placeholder ``0.0``; it is unused whenever ``state_value`` is supplied, which
real training always does.

Device support and why it needs a small wrapper, not a modified module
-------------------------------------------------------------------------
``neural_model_v1.SpecialistPolicyModelV1`` builds its own scratch index tensors
(``torch.tensor(...)``) without an explicit ``device=``, so simply moving the module's
parameters to CUDA breaks its forward pass (a CPU index tensor cannot index a CUDA embedding
table).  ``vtrace_v1`` is, separately and deliberately, CPU-only by its own design (its
docstring: "this module never touches CUDA and never selects a device other than CPU"; its
evaluator explicitly rejects a non-CPU tensor).  Rather than editing either committed module,
``_bind_device_target_log_probability_v1`` below wraps the *unmodified*
``trajectory_target_v1.make_trajectory_target_log_probability_v1`` closure: it runs the
model's forward pass inside a ``torch.device(...)`` context (so the model's own factory-built
tensors land on the requested device, matching its parameters) and then moves only the
resulting scalar back to CPU with a plain, differentiable ``Tensor.to("cpu")`` before handing
it to the (still CPU-only) V-trace machinery.  A device-crossing ``.to()`` is a normal
autograd op, so a gradient computed on the CPU side still flows back into the CUDA-resident
model parameters on ``.backward()``.  This was verified by hand against the real production
model and the real ``cli-smoke-test-alakazam-4`` collection with ``--device cuda`` before being
relied on here.  One separately-documented gap: ``neural_checkpoint_v1.build_checkpoint_payload_v1``
only ever captures the CPU RNG stream (``cuda_rng_state`` is always written as ``None`` and is
never restored) -- an existing limitation of that committed module, not something this module
works around.  It does not affect this model, which has no stochastic forward-pass op.

``--device cuda`` works, and is measurably *slower* than the default
--------------------------------------------------------------------
Measured 2026-08-04 on an NVIDIA RTX PRO 5000 Blackwell, scoring 200 real
transitions from ``runs/meta-specialist-actor-pool/p0-rule-agent-2000`` with
the production card vocabulary: **CPU 3.01 ms/transition, CUDA 8.71
ms/transition -- CUDA is 0.35x, i.e. about 2.9x slower.**  This is not a
configuration problem to be tuned away.  ``SpecialistPolicyModelV1`` has
roughly 390K parameters and every prefix step issues its own small
matmul/embedding ops, so a decision is a long chain of tiny sequential
kernels; at that size, per-launch and host/device transfer overhead dominates
the arithmetic the device would accelerate.  ``--device cpu`` is therefore the
correct default for this loop, and switching it to CUDA to "use the GPU" would
be a regression.  Should a much larger model or genuine cross-transition
batching land later, re-measure before assuming that conclusion still holds.

Why the per-step cost is what it is
-------------------------------------
Scoring a stored transition needs its payload validated and its step inputs
rebuilt into live objects, and none of that depends on the model's parameters.
Doing it inside the step loop re-derived an identical result on every one of
``--max-steps`` steps and measured as several times the cost of the model's own
forward pass.  ``_prepare_admitted_transitions_v1`` hoists it to a one-time
phase before the first step; ``trajectory_target_v1``'s prepared transitions
additionally reuse one ``encode_state`` per decision (instead of one per prefix
step) and encode each distinct candidate once per decision (instead of once per
occurrence, and in one batched call).  Measured on the same 200 real
transitions: **5.50 ms/transition before, 0.84 ms/transition after -- 6.5x** --
which took a 200-step run over the full 46,525-transition collection from about
14.2 h to about 2.2 h.  ``tests/meta_specialist/test_trajectory_target_equivalence_v1.py``
holds all of it to identical log-probabilities *and* identical gradients
against the unbatched path, because a faster step that computes something else
is worthless.
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import torch

from mage_ptcg.meta_specialist.actor_pool_v1 import (
    ActorPoolV1Error,
    current_repo_commit_v1,
    read_actor_pool_game_record_v1,
)
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    CardVocabularyRegistryError,
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (
    NeuralCheckpointV1Error,
    TrainingIdentityV1,
    build_checkpoint_payload_v1,
    build_training_identity_v1,
    load_checkpoint_v1,
    publish_checkpoint_v1,
    load_checkpoint_for_inference_v1,
    restore_checkpoint_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (
    NeuralModelV1Error,
    SpecialistModelConfigV1,
    SpecialistPolicyModelV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.trajectory_target_v1 import (
    TrajectoryScorerV1,
    TrajectoryTargetV1Error,
    prepare_trajectory_target_transition_v1,
)
from mage_ptcg.meta_specialist.vtrace_bridge_v1 import (
    TargetLogProbabilityFn,
    VTraceBridgeV1Error,
    VTraceLossV1,
    accumulate_trajectory_losses_v1,
    admit_trajectories_v1,
    evaluate_trajectory_loss_v1,
)
from mage_ptcg.offline_scaleup.progress import ProgressReporter


TRAIN_FROM_TRAJECTORIES_RUN_SUMMARY_SCHEMA_V1 = "meta-specialist-train-from-trajectories-run-summary-v1"
LATEST_CHECKPOINT_POINTER_SCHEMA_V1 = "meta-specialist-train-from-trajectories-latest-checkpoint-v1"

# Fixed, honest "no critic yet" constants -- see the module docstring.
_BOOTSTRAP_VALUE_V1 = 0.0
# The design puts policy/value/entropy coefficients in the recipe. Entropy is a
# real term now that the policy has a distribution to be uncertain about and a
# value baseline to be scored against; a small positive default keeps the policy
# from collapsing onto one candidate, which is exactly the failure the
# baseline-free configuration produced.
_DEFAULT_ENTROPY_COEFFICIENT_V1 = 0.01
# Weight on the behavior-cloning anchor. Without it, offline training on a fixed
# corpus drives the collected actions' log-probabilities toward negative
# infinity until the importance ratios collapse and learning stops. Measured
# sweep on real collected games: 0.0 drifts to -4.7, 0.1 holds near +1.5, and
# 0.5+ saturates into cloning the rule agent so completely that the policy
# gradient can no longer move it. 0.1 is the measured point that is stable while
# leaving the RL term room to matter.
_DEFAULT_BC_COEFFICIENT_V1 = 0.1

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TRAINING_OUTPUT_BASE_V1 = _REPO_ROOT / "runs" / "meta-specialist-training"

_OPTIMIZER_KINDS_V1 = frozenset({"adamw", "sgd"})
from mage_ptcg.meta_specialist.foundation_init_v1 import (
    FoundationInitProvenanceV1,
    INIT_KIND_WARM_START_V1,
    assert_primary_teacher_is_not_rule_v0_v1,
    parse_foundation_init_provenance_v1,
    random_init_provenance_v1,
)
_UNREADABLE_LISTED_CAP_V1 = 50
_DROP_REASON_LISTED_CAP_V1 = 50
_GAME_PATH_LISTED_CAP_V1 = 200
_STEP_METRIC_LISTED_CAP_V1 = 5000
_NONTTY_SNAPSHOT_INTERVAL_SECONDS_V1 = 10.0
# Ceiling on the transitions one *implicitly* sized optimizer step may cover.
# Chosen from the measured ~0.3 MB of autograd graph per transition, so an
# unchosen default stays around 6 GB rather than consuming the machine.
_MAX_IMPLICIT_STEP_TRANSITIONS_V1 = 20_000
# A starting batch that keeps a step responsive rather than merely survivable,
# and the measured CPU cost of one transition's forward+backward on the real
# rule-agent collection (2026-08-04: ~11.5 ms, of which backward is ~2.9x the
# forward). Used only to make the refusal message concrete.
_RECOMMENDED_STEP_TRANSITIONS_V1 = 1_300
_MEASURED_SECONDS_PER_TRANSITION_V1 = 0.0115
# Intra-op threads for the scoring loop. This model's ops are tiny (a 390K-param
# policy scoring one decision at a time), so torch's default -- one thread per
# core -- spends far more time synchronizing threads than doing arithmetic.
# Measured 2026-08-04 on a real 64-game minibatch (forward+backward): 1 thread
# 2.42s, 2 threads 2.55s, 4 threads 2.74s, 8 threads 5.48s, 14 threads (the
# default here) 9.62s -- single-threaded is 4.0x faster than the default.
# Overridable via --torch-threads because the right value depends on op size,
# and a future batched/larger model would shift it.
_DEFAULT_TORCH_THREADS_V1 = 1
# Steps averaged for the reported loss trend.
_TREND_WINDOW_STEPS_V1 = 20


class TrainFromTrajectoriesV1Error(ValueError):
    """Raised for any refuse-closed condition in trajectory-based training."""


def _validate_run_name_v1(run_name: object) -> str:
    if type(run_name) is not str or not run_name:
        raise TrainFromTrajectoriesV1Error("--run-name must be a nonempty string")
    if run_name in (".", "..") or "/" in run_name or "\\" in run_name or run_name.startswith("."):
        raise TrainFromTrajectoriesV1Error(
            f"--run-name must be a single safe directory-name component, got {run_name!r}"
        )
    return run_name


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int or type(value) is bool or value < 1:
        raise TrainFromTrajectoriesV1Error(f"{name} must be a positive int")
    return value


def _require_nonneg_int(value: object, name: str) -> int:
    if type(value) is not int or type(value) is bool or value < 0:
        raise TrainFromTrajectoriesV1Error(f"{name} must be a nonnegative int")
    return value


def _require_positive_float(value: object, name: str) -> float:
    if type(value) is bool or type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) <= 0.0:
        raise TrainFromTrajectoriesV1Error(f"{name} must be a positive finite number")
    return float(value)


def _json_safe_float(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _atomic_write_json_v1(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------
# Reading the collection directory -- never fabricate a record that fails
# to read or validate; exclude it with a recorded reason instead.
# --------------------------------------------------------------------------


def _load_game_records_v1(
    games_dir: Path,
    summary_path: Path | None = None,
    run_name: str = "",
    *,
    progress: bool | None = None,
) -> tuple[list[tuple[str, dict[str, object]]], list[str]]:
    """Read every ``games/*/record.json``, revalidating each through actor_pool_v1.

    Returns ``(loaded, unreadable_reasons)``: ``loaded`` is a deterministically
    ordered ``(path, record)`` list (sorted by path, matching the ordering the
    existing collected-trajectory tests already use); ``unreadable_reasons`` records
    one message per file that could not be read or failed re-validation.
    """
    loaded: list[tuple[str, dict[str, object]]] = []
    reasons: list[str] = []
    paths = sorted(games_dir.glob("*/record.json"))
    transitions_seen = 0
    # Reading and re-validating a few thousand records takes minutes; without a
    # bar of its own this phase is indistinguishable from a hang.  ``total`` is
    # known only after the glob, so the reporter is built here rather than by
    # the caller.
    reporter = ProgressReporter(
        phase="load-trajectories", total=len(paths), run_id=run_name, unit="game",
        progress=progress, interval_seconds=_NONTTY_SNAPSHOT_INTERVAL_SECONDS_V1,
        summary_path=summary_path,
    )
    try:
        for record_path in paths:
            try:
                record = read_actor_pool_game_record_v1(record_path)
            except ActorPoolV1Error as exc:
                reasons.append(f"{record_path}: {exc}")
            else:
                loaded.append((str(record_path), record))
                transitions_seen += len(record["transitions"])
            reporter.update(
                1, valid=len(loaded), faults=len(reasons), transitions=transitions_seen,
            )
    finally:
        reporter.close()
    return loaded, reasons


def _reject_an_unbounded_implicit_minibatch_v1(
    admitted: Sequence[tuple[str, Mapping[str, object]]],
    *,
    trajectories_per_step: int | None,
    effective_trajectories_per_step: int,
    microbatch_trajectories: int | None,
) -> None:
    """Refuse a full-corpus step that was never chosen, before it exhausts memory.

    Omitting ``--trajectories-per-step`` means "one step over every admitted
    trajectory".  On the four-game smoke collection that is a reasonable
    default; on a real one it is not a batch size anyone picked.  One step holds
    the autograd graph for every transition in the minibatch at once, and the
    real rule-agent collection (4,270 games / 87,258 transitions) was measured
    reaching **27 GB resident and still climbing** before a single step
    completed -- on a 47 GB machine, roughly 0.3 MB of graph per transition.

    So this refuses, with the number to pass, rather than letting a run consume
    the machine 25 minutes after it was started.  It only ever fires on the
    *implicit* default: an operator who names a batch size (or names a
    ``--microbatch-trajectories`` bound, which caps the resident graph
    regardless of the minibatch) has made the choice, and this does not
    second-guess it.
    """
    if trajectories_per_step is not None or microbatch_trajectories is not None:
        return
    transitions = sum(len(record["transitions"]) for _path, record in admitted)
    if transitions <= _MAX_IMPLICIT_STEP_TRANSITIONS_V1:
        return

    per_game = max(1.0, transitions / max(1, len(admitted)))
    ceiling = max(1, int(_MAX_IMPLICIT_STEP_TRANSITIONS_V1 / per_game))
    recommended = max(1, int(_RECOMMENDED_STEP_TRANSITIONS_V1 / per_game))
    seconds = _MEASURED_SECONDS_PER_TRANSITION_V1 * recommended * per_game
    raise TrainFromTrajectoriesV1Error(
        f"--trajectories-per-step was not given, so one optimizer step would cover all "
        f"{effective_trajectories_per_step} admitted trajectories ({transitions} transitions) "
        f"in a single autograd graph. Measured at roughly 0.3 MB of graph per transition, that "
        f"is about {transitions * 0.3 / 1024:.0f} GB here -- it exhausts memory before the "
        f"first step finishes.\n"
        f"Pass an explicit batch size. --trajectories-per-step {recommended} "
        f"(~{int(recommended * per_game)} transitions, measured ~{seconds:.0f}s per step on CPU) "
        f"is a reasonable starting point; {ceiling} is about the largest that still fits in "
        f"memory, and a larger batch buys fewer optimizer steps for the same wall time.\n"
        f"Alternatively bound only the resident graph with --microbatch-trajectories, which "
        f"accumulates unnormalized and divides once, giving the identical step a whole-minibatch "
        f"evaluation would."
    )


def _prepare_admitted_transitions_v1(
    admitted: Sequence[tuple[str, Mapping[str, object]]],
    *,
    summary_path: Path | None,
    run_name: str,
    progress: bool | None,
) -> list[tuple[str, dict[str, object]]]:
    """Validate and rebuild every admitted transition once, for all steps to reuse.

    Scoring a transition needs its payload validated and its step inputs rebuilt
    into live objects.  None of that depends on the model's parameters, so doing
    it per optimizer step re-derives an identical result ``--max-steps`` times;
    measured on the 2000-game rule-agent collection it dominated the step,
    costing several times the model's own forward pass.  Hoisting it here makes
    it a one-time cost paid before the first step, and
    ``trajectory_target_v1.prepare_trajectory_target_transition_v1`` guarantees a
    prepared transition scores to the same value (and gradient) as the raw
    payload it replaces.

    A transition that cannot be prepared is a transition that could not have been
    scored either; it fails here, loudly and before any weight moves, rather than
    surfacing as a mid-training scoring failure.
    """
    total = sum(len(record["transitions"]) for _path, record in admitted)
    prepared_records: list[tuple[str, dict[str, object]]] = []
    reporter = ProgressReporter(
        phase="prepare-transitions", total=total, run_id=run_name, unit="transition",
        progress=progress, interval_seconds=_NONTTY_SNAPSHOT_INTERVAL_SECONDS_V1,
        summary_path=summary_path,
    )
    try:
        for path, record in admitted:
            try:
                prepared = [
                    prepare_trajectory_target_transition_v1(transition)
                    for transition in record["transitions"]
                ]
            except TrajectoryTargetV1Error as exc:
                raise TrainFromTrajectoriesV1Error(
                    f"{path}: an admitted transition cannot be prepared for scoring: {exc}"
                ) from exc
            prepared_records.append((path, {**record, "transitions": prepared}))
            reporter.update(len(prepared), games=len(prepared_records))
    finally:
        reporter.close()
    return prepared_records


# --------------------------------------------------------------------------
# Deterministic, resumable minibatch cycling over admitted trajectories.
# ``cursor`` is exactly the checkpoint's ``sampler_cursor``.
# --------------------------------------------------------------------------


def _next_minibatch_v1(
    admitted: Sequence[tuple[str, Mapping[str, object]]], *, cursor: int, size: int,
) -> tuple[list[tuple[str, Mapping[str, object]]], int]:
    n = len(admitted)
    if n == 0:
        raise TrainFromTrajectoriesV1Error("no admitted trajectory to draw a minibatch from")
    cursor = cursor % n
    end = cursor + size
    if end <= n:
        window = list(admitted[cursor:end])
    else:
        window = list(admitted[cursor:]) + list(admitted[: end - n])
    return window, end % n


# --------------------------------------------------------------------------
# Device support: run the (unmodified) model forward on ``device``, hand the
# (unmodified, CPU-only) V-trace machinery a CPU tensor. See module docstring.
# --------------------------------------------------------------------------


def _bind_device_v1(
    inner: Callable[[Mapping[str, Any]], torch.Tensor], *, device: torch.device,
) -> Callable[[Mapping[str, Any]], torch.Tensor]:
    """Run one per-transition scorer on ``device``, hand the result back on CPU.

    Applies equally to the log-probability, the state value, and the entropy:
    all three are model forwards whose scalar result feeds the CPU-only V-trace
    machinery, and a device-crossing ``.to()`` is a normal autograd op, so the
    gradient still reaches device-resident parameters.
    """
    if device.type == "cpu":
        return inner

    def bound(transition: Mapping[str, Any]) -> torch.Tensor:
        with torch.device(device):
            value = inner(transition)
        return value.to("cpu")

    return bound


def _resolve_device_v1(device: str) -> torch.device:
    try:
        resolved = torch.device(device)
    except (RuntimeError, TypeError) as exc:
        raise TrainFromTrajectoriesV1Error(f"--device {device!r} is not a valid torch device: {exc}") from exc
    if resolved.type == "cuda" and not torch.cuda.is_available():
        raise TrainFromTrajectoriesV1Error(
            f"--device {device!r} requested but CUDA is not available in this process"
        )
    return resolved


# --------------------------------------------------------------------------
# One trajectory's loss, and "accumulate unnormalized, divide once" over a
# minibatch that may be processed in smaller microbatches to defend against OOM.
# Mirrors neural_learner_v1.training_step_v1's discipline over a different
# data shape (trajectories, not ragged snapshot examples).
# --------------------------------------------------------------------------


def _score_trajectory_v1(
    record: Mapping[str, object],
    *,
    target_log_probability: TargetLogProbabilityFn,
    state_value: Callable[[Mapping[str, Any]], torch.Tensor] | None,
    entropy: Callable[[Mapping[str, Any]], torch.Tensor] | None,
    rho_bar: float,
    c_bar: float,
    advantage_shift: float = 0.0,
    advantage_scale: float = 1.0,
) -> VTraceLossV1:
    return evaluate_trajectory_loss_v1(
        record["transitions"], target_log_probability=target_log_probability,
        bootstrap_value=_BOOTSTRAP_VALUE_V1, rho_bar=rho_bar, c_bar=c_bar,
        state_value=state_value, entropy=entropy,
        advantage_shift=advantage_shift, advantage_scale=advantage_scale,
    )


#: How to rescale the V-trace advantage before it weights the policy gradient.
#: ``none`` reproduces the pre-2026-08-07 update exactly.
ADVANTAGE_NORMALIZATION_MODES_V1 = ("none", "center", "standardize")

#: Floor on the advantage standard deviation used as a divisor.  A minibatch
#: whose advantages are nearly identical carries no ranking information, and
#: dividing by that near-zero spread would amplify rounding noise into the whole
#: update rather than sharpen it.
_ADVANTAGE_SCALE_FLOOR_V1 = 1.0e-3


@dataclass(frozen=True, slots=True)
class AdvantageNormalizationV1:
    """The shift/scale one step applies, and how the next step's is derived.

    The moments come from the *previous* optimizer step.  The corpus is fixed
    within a round and the policy moves little in a single step, so the previous
    step's statistics estimate this step's well.  Taking them from the current
    step instead would need either a second forward pass over the minibatch or
    holding every trajectory's advantage until the batch closed -- and the
    microbatch loop exists precisely so that much is never held at once.

    The first step of a run normalizes with the identity, having no previous
    step to estimate from.  Over an 80-step round that is one step.
    """

    mode: str = "none"
    shift: float = 0.0
    scale: float = 1.0

    @classmethod
    def for_mode_v1(cls, mode: str) -> "AdvantageNormalizationV1":
        if mode not in ADVANTAGE_NORMALIZATION_MODES_V1:
            raise TrainFromTrajectoriesV1Error(
                "--advantage-normalization must be one of "
                f"{list(ADVANTAGE_NORMALIZATION_MODES_V1)}"
            )
        return cls(mode=mode)

    def advanced_v1(self, loss: VTraceLossV1) -> "AdvantageNormalizationV1":
        """Return the normalization the *next* step should use."""
        if self.mode == "none":
            return self
        total = loss.advantage_sum
        squares = loss.advantage_square_sum
        weight = float(loss.weight_sum.detach())
        if total is None or squares is None or weight <= 0.0:
            return self
        mean = float(total.detach()) / weight
        variance = max(float(squares.detach()) / weight - mean * mean, 0.0)
        if not math.isfinite(mean) or not math.isfinite(variance):
            return self
        scale = 1.0
        if self.mode == "standardize":
            scale = max(math.sqrt(variance), _ADVANTAGE_SCALE_FLOOR_V1)
        return AdvantageNormalizationV1(mode=self.mode, shift=mean, scale=scale)


@dataclass(frozen=True, slots=True)
class LearningHealthV1:
    """Aggregates that say whether the policy is *learning*, not merely running.

    A falling loss and a nonzero gradient norm are necessary but not sufficient:
    both look healthy while a policy collapses onto a degenerate action, and
    neither says which way the policy moved relative to the behavior that
    generated the data.  These do:

    ``mean_log_probability_shift``
        ``mean(log pi_target(a) - log pi_behavior(a))`` over the minibatch's
        transitions, for the action the actor really took.  Positive means the
        policy is becoming *more* likely to reproduce the collected behavior,
        negative means less.  Near zero across many steps means the update is
        not moving the policy where the data is.
    ``clipped_importance_fraction``
        The share of transitions whose importance ratio ``exp(shift)`` exceeds
        ``rho_bar``.  V-trace clips those, so a fraction near 1.0 means almost
        every step's policy gradient is truncated: the run is off-policy enough
        to be learning from a heavily biased signal, which no loss curve reveals
        on its own.
    ``mean_target_log_probability``
        The absolute level.  Collapsing toward 0 means the policy has become
        near-deterministic on the stored actions -- possibly converged, possibly
        a degenerate mode that an entropy term would have prevented (none is
        wired to this bridge yet; see the module docstring).

    ``mean_importance_ratio`` / ``mean_continuation_c``
        Direct measurements of the two V-trace gates.  The latter is the
        continuation multiplier that compounds through a trajectory, so it
        exposes attenuation that a per-step ``dead_rho`` fraction misses.

    ``opponent_state_value_means``
        Critic predictions split by the recorded opponent instance.  A pooled
        value mean can hide a stable matchup baseline mixed with a hard one;
        the strata make that mismatch auditable.

    Every field is measured on the transitions this step actually scored, and is
    ``None`` when nothing scored rather than a fabricated zero.
    """

    transitions: int
    mean_target_log_probability: float | None
    mean_behavior_log_probability: float | None
    mean_log_probability_shift: float | None
    clipped_importance_fraction: float | None
    vanishing_importance_fraction: float | None
    mean_state_value: float | None
    mean_terminal_return: float | None
    mean_importance_ratio: float | None = None
    mean_continuation_c: float | None = None
    opponent_state_value_means: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "transitions": self.transitions,
            "mean_target_log_probability": self.mean_target_log_probability,
            "mean_behavior_log_probability": self.mean_behavior_log_probability,
            "mean_log_probability_shift": self.mean_log_probability_shift,
            "clipped_importance_fraction": self.clipped_importance_fraction,
            "vanishing_importance_fraction": self.vanishing_importance_fraction,
            "mean_state_value": self.mean_state_value,
            "mean_terminal_return": self.mean_terminal_return,
            "mean_importance_ratio": self.mean_importance_ratio,
            "mean_continuation_c": self.mean_continuation_c,
            "opponent_state_value_means": [
                {"opponent_instance_id": key, "mean_state_value": value}
                for key, value in self.opponent_state_value_means
            ],
        }


def assert_on_policy_health_v1(
    health: LearningHealthV1, *, max_abs_log_shift: float = 1e-5, min_mean_c: float = 0.95,
) -> None:
    """Fail closed when a supposedly on-policy batch is actually off-policy."""
    if type(health) is not LearningHealthV1:
        raise TrainFromTrajectoriesV1Error("health must be a LearningHealthV1")
    if health.transitions <= 0:
        raise TrainFromTrajectoriesV1Error("on-policy health has no scored transitions")
    shift = health.mean_log_probability_shift
    mean_c = health.mean_continuation_c
    if shift is None or mean_c is None:
        raise TrainFromTrajectoriesV1Error("on-policy health is missing ratio diagnostics")
    if abs(shift) > max_abs_log_shift or mean_c < min_mean_c:
        raise TrainFromTrajectoriesV1Error(
            f"on-policy health failed: mean_log_probability_shift={shift:.6g}, "
            f"mean_continuation_c={mean_c:.6g}"
        )


_EMPTY_LEARNING_HEALTH_V1 = LearningHealthV1(0, None, None, None, None, None, None, None)
# An importance ratio at or below this contributes essentially nothing to the
# policy gradient, because V-trace multiplies the advantage by rho.
_VANISHING_IMPORTANCE_RATIO_V1 = 0.01


class _RecordingTargetV1:
    """Wrap a scorer and remember what it produced, without altering it.

    The tensor is passed through untouched -- the same object, still attached to
    the graph -- so this observes the step rather than changing it.  Only
    detached floats are retained.
    """

    __slots__ = (
        "_inner", "_rho_bar", "_c_bar", "targets", "behaviors", "values", "returns",
        "opponent_values",
    )

    def __init__(self, inner: TargetLogProbabilityFn, *, rho_bar: float, c_bar: float = 1.0) -> None:
        self._inner = inner
        self._rho_bar = rho_bar
        self._c_bar = c_bar
        self.targets: list[float] = []
        self.behaviors: list[float] = []
        self.values: list[float] = []
        self.returns: list[float] = []
        self.opponent_values: dict[str, list[float]] = {}

    def observe_value(self, transition: Mapping[str, Any], value: torch.Tensor) -> None:
        """Record the critic's prediction and the terminal reward it must explain."""
        self.values.append(float(value.detach()))
        opponent = transition.get("opponent_instance_id")
        if isinstance(opponent, str) and math.isfinite(float(value.detach())):
            self.opponent_values.setdefault(opponent, []).append(float(value.detach()))
        reward = transition.get("reward")
        if type(reward) in (int, float) and transition.get("terminal") is True:
            self.returns.append(float(reward))

    def __call__(self, transition: Mapping[str, Any]) -> torch.Tensor:
        value = self._inner(transition)
        self.targets.append(float(value.detach()))
        behavior = transition.get("behavior_log_probability")
        usable = type(behavior) in (int, float)
        self.behaviors.append(float(behavior) if usable else math.nan)
        return value

    def health(self) -> LearningHealthV1:
        pairs = [
            (target, behavior)
            for target, behavior in zip(self.targets, self.behaviors)
            if math.isfinite(target) and math.isfinite(behavior)
        ]
        if not pairs:
            return LearningHealthV1(
                len(self.targets), None, None, None, None, None, None, None,
            )
        shifts = [target - behavior for target, behavior in pairs]
        ratios = [math.exp(shift) for shift in shifts]
        continuations = [min(self._c_bar, ratio) for ratio in ratios]
        log_rho_bar = math.log(self._rho_bar) if self._rho_bar > 0.0 else -math.inf
        clipped = sum(1 for shift in shifts if shift > log_rho_bar)
        log_floor = math.log(_VANISHING_IMPORTANCE_RATIO_V1)
        vanished = sum(1 for shift in shifts if shift < log_floor)
        finite_values = [item for item in self.values if math.isfinite(item)]
        return LearningHealthV1(
            transitions=len(pairs),
            mean_target_log_probability=sum(t for t, _ in pairs) / len(pairs),
            mean_behavior_log_probability=sum(b for _, b in pairs) / len(pairs),
            mean_log_probability_shift=sum(shifts) / len(shifts),
            clipped_importance_fraction=clipped / len(shifts),
            vanishing_importance_fraction=vanished / len(shifts),
            mean_state_value=(
                sum(finite_values) / len(finite_values) if finite_values else None
            ),
            mean_terminal_return=(
                sum(self.returns) / len(self.returns) if self.returns else None
            ),
            mean_importance_ratio=sum(ratios) / len(ratios),
            mean_continuation_c=sum(continuations) / len(continuations),
            opponent_state_value_means=tuple(
                sorted(
                    (key, sum(values) / len(values))
                    for key, values in self.opponent_values.items()
                    if values
                )
            ),
        )


def _accumulate_minibatch_loss_v1(
    minibatch: Sequence[tuple[str, Mapping[str, object]]],
    *,
    make_scorer: Callable[[], TrajectoryScorerV1],
    entropy_coefficient: float,
    device: torch.device,
    rho_bar: float,
    c_bar: float,
    microbatch_trajectories: int | None,
    oom_shrink: bool = True,
    advantage_normalization: AdvantageNormalizationV1 = AdvantageNormalizationV1(),
) -> tuple[VTraceLossV1 | None, list[str], int, LearningHealthV1]:
    """Score and accumulate every trajectory in ``minibatch``, shrinking on OOM.

    Returns ``(merged_or_none, scoring_failure_reasons, chunks_used)``.  A
    trajectory that cannot be rebuilt/rescored is recorded as a failure reason
    and excluded, never defaulted; ``merged`` is ``None`` only when every
    trajectory in the minibatch failed to score.
    """
    size = len(minibatch) if microbatch_trajectories is None else microbatch_trajectories
    if type(size) is not int or size < 1:
        raise TrainFromTrajectoriesV1Error("microbatch_trajectories must be a positive int")
    attempts: list[int] = []
    probe = size
    while probe >= 1:
        attempts.append(probe)
        probe //= 2
        if not oom_shrink:
            break

    last_oom: RuntimeError | None = None
    chunk_losses: list[VTraceLossV1] = []
    failures: list[str] = []
    for attempt in attempts:
        try:
            chunk_losses = []
            failures = []
            # One target per attempt, so its shared candidate cache spans exactly
            # the graph this attempt will back-propagate and no more. A retry
            # after OOM starts from an empty cache rather than carrying the
            # abandoned attempt's tensors (and their memory) into the next one.
            scorer = make_scorer()
            recorder = _RecordingTargetV1(
                _bind_device_v1(scorer.log_probability, device=device),
                rho_bar=rho_bar, c_bar=c_bar,
            )
            target_log_probability = recorder
            bound_value = _bind_device_v1(scorer.value, device=device)

            def state_value(transition, _bound=bound_value, _recorder=recorder):
                value = _bound(transition)
                _recorder.observe_value(transition, value)
                return value
            policy_entropy = (
                None if entropy_coefficient == 0.0
                else _bind_device_v1(scorer.entropy, device=device)
            )
            for start in range(0, len(minibatch), attempt):
                chunk = minibatch[start : start + attempt]
                scored: list[VTraceLossV1] = []
                for path, record in chunk:
                    try:
                        scored.append(_score_trajectory_v1(
                            record, target_log_probability=target_log_probability,
                            state_value=state_value, entropy=policy_entropy,
                            rho_bar=rho_bar, c_bar=c_bar,
                            advantage_shift=advantage_normalization.shift,
                            advantage_scale=advantage_normalization.scale,
                        ))
                    except (TrajectoryTargetV1Error, VTraceBridgeV1Error) as exc:
                        failures.append(f"{path}: {exc}")
                if scored:
                    chunk_losses.append(accumulate_trajectory_losses_v1(scored))
        except torch.cuda.OutOfMemoryError as exc:  # pragma: no cover - CPU tests
            last_oom = exc
            continue
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower():
                raise
            last_oom = exc
            continue
        break
    else:
        raise TrainFromTrajectoriesV1Error("every microbatch size ran out of memory") from last_oom

    if not chunk_losses:
        return None, failures, 0, recorder.health()
    merged = chunk_losses[0] if len(chunk_losses) == 1 else accumulate_trajectory_losses_v1(chunk_losses)
    return merged, failures, len(chunk_losses), recorder.health()


@dataclass(frozen=True, slots=True)
class TrajectoryTrainingStepResultV1:
    """What one optimizer step over one minibatch of trajectories actually did."""

    loss: float
    weight_sum: float
    trajectories_used: int
    trajectories_failed: int
    failure_reasons: tuple[str, ...]
    transitions: int
    microbatches: int
    gradient_norm: float
    skipped: bool
    skip_reason: str | None
    health: LearningHealthV1 = _EMPTY_LEARNING_HEALTH_V1
    #: What the *next* step should normalize its advantage by.
    advantage_normalization: AdvantageNormalizationV1 = AdvantageNormalizationV1()


def _take_trajectory_training_step_v1(
    minibatch: Sequence[tuple[str, Mapping[str, object]]],
    *,
    model: SpecialistPolicyModelV1,
    optimizer: torch.optim.Optimizer,
    make_scorer: Callable[[], TrajectoryScorerV1],
    entropy_coefficient: float,
    device: torch.device,
    rho_bar: float,
    c_bar: float,
    value_coefficient: float,
    bc_coefficient: float,
    max_gradient_norm: float,
    microbatch_trajectories: int | None,
    advantage_normalization: AdvantageNormalizationV1 = AdvantageNormalizationV1(),
) -> TrajectoryTrainingStepResultV1:
    optimizer.zero_grad(set_to_none=True)
    merged, failures, chunks, health = _accumulate_minibatch_loss_v1(
        minibatch, make_scorer=make_scorer, entropy_coefficient=entropy_coefficient,
        device=device,
        rho_bar=rho_bar, c_bar=c_bar,
        microbatch_trajectories=microbatch_trajectories,
        advantage_normalization=advantage_normalization,
    )
    # Derived even on a skipped step: the moments describe the corpus, not the
    # update, so a step that could not be applied still informs the next one.
    next_normalization = (
        advantage_normalization if merged is None
        else advantage_normalization.advanced_v1(merged)
    )
    trajectories_used = len(minibatch) - len(failures)

    def _skip(*, loss: float, gradient_norm: float, reason: str) -> TrajectoryTrainingStepResultV1:
        optimizer.zero_grad(set_to_none=True)
        return TrajectoryTrainingStepResultV1(
            loss=loss, weight_sum=0.0 if merged is None else float(merged.weight_sum.detach()),
            trajectories_used=trajectories_used, trajectories_failed=len(failures),
            failure_reasons=tuple(failures), transitions=0 if merged is None else merged.steps,
            microbatches=chunks, gradient_norm=gradient_norm, skipped=True, skip_reason=reason,
            health=health, advantage_normalization=next_normalization,
        )

    if merged is None:
        return _skip(loss=0.0, gradient_norm=0.0, reason="no trajectory in this minibatch could be scored")
    if float(merged.weight_sum.detach()) <= 0.0:
        return _skip(loss=0.0, gradient_norm=0.0, reason="minibatch has zero total V-trace weight")

    loss = merged.total(
        value_coefficient=value_coefficient, entropy_coefficient=entropy_coefficient,
        bc_coefficient=bc_coefficient,
    ) / merged.weight_sum
    if not torch.isfinite(loss).all():
        return _skip(loss=float(loss.detach()), gradient_norm=0.0, reason="non-finite loss")
    loss.backward()

    parameters = [item for item in model.parameters() if item.grad is not None]
    if not parameters:
        raise TrainFromTrajectoriesV1Error("no model parameter received a gradient")
    if any(not torch.isfinite(item.grad).all() for item in parameters):
        return _skip(loss=float(loss.detach()), gradient_norm=float("nan"), reason="non-finite gradient")

    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_gradient_norm)
    if not torch.isfinite(norm).all():
        return _skip(loss=float(loss.detach()), gradient_norm=float("nan"), reason="non-finite gradient norm")

    optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    return TrajectoryTrainingStepResultV1(
        loss=float(loss.detach()), weight_sum=float(merged.weight_sum.detach()),
        trajectories_used=trajectories_used, trajectories_failed=len(failures),
        failure_reasons=tuple(failures), transitions=merged.steps, microbatches=chunks,
        gradient_norm=float(norm.detach()), skipped=False, skip_reason=None,
        health=health, advantage_normalization=next_normalization,
    )


# --------------------------------------------------------------------------
# Checkpoint publish + resume-pointer.
# --------------------------------------------------------------------------


def _publish_checkpoint_and_pointer_v1(
    *,
    output_root: Path,
    model: SpecialistPolicyModelV1,
    optimizer: torch.optim.Optimizer,
    identity: TrainingIdentityV1,
    recipe: Mapping[str, object],
    step: int,
    sampler_cursor: int,
    foundation_init: FoundationInitProvenanceV1,
) -> tuple[Path, str]:
    payload = build_checkpoint_payload_v1(
        model=model, optimizer=optimizer, scheduler=None, identity=identity, recipe=recipe,
        step=step, sampler_cursor=sampler_cursor, foundation_init=foundation_init,
    )
    path = publish_checkpoint_v1(output_root / "checkpoints", payload)
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    pointer = {
        "schema_version": LATEST_CHECKPOINT_POINTER_SCHEMA_V1,
        "checkpoint_path": str(path),
        "checkpoint_sha256": content_hash,
        "step": step,
        "sampler_cursor": sampler_cursor,
        "training_identity": identity.to_dict(),
    }
    _atomic_write_json_v1(output_root / "latest_checkpoint.json", pointer)
    return path, content_hash


def _restore_from_pointer_if_present_v1(
    *,
    output_root: Path,
    model: SpecialistPolicyModelV1,
    optimizer: torch.optim.Optimizer,
    identity: TrainingIdentityV1,
) -> tuple[int, int, bool]:
    pointer_path = output_root / "latest_checkpoint.json"
    if not pointer_path.is_file():
        return 0, 0, False
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainFromTrajectoriesV1Error(f"could not read {pointer_path}: {exc}") from exc
    if type(pointer) is not dict or pointer.get("schema_version") != LATEST_CHECKPOINT_POINTER_SCHEMA_V1:
        raise TrainFromTrajectoriesV1Error(f"{pointer_path} is not a recognized checkpoint pointer")
    if pointer.get("training_identity") != identity.to_dict():
        raise TrainFromTrajectoriesV1Error(
            f"an existing training run at this --run-name used a different training identity "
            f"(model topology / recipe / seed / snapshot); use a different --run-name or "
            f"match the prior hyperparameters exactly (pointer: {pointer_path})"
        )
    checkpoint_path = pointer.get("checkpoint_path")
    if type(checkpoint_path) is not str or not checkpoint_path:
        raise TrainFromTrajectoriesV1Error(f"{pointer_path} has no checkpoint_path")
    try:
        payload = load_checkpoint_v1(checkpoint_path, expected=identity)
    except NeuralCheckpointV1Error as exc:
        raise TrainFromTrajectoriesV1Error(f"could not load checkpoint {checkpoint_path}: {exc}") from exc
    step, sampler_cursor = restore_checkpoint_v1(payload, model=model, optimizer=optimizer, scheduler=None)
    return step, sampler_cursor, True


# --------------------------------------------------------------------------
# Entry point driven by the CLI (and directly by tests).
# --------------------------------------------------------------------------



def _load_bootstrap_weights_v1(
    model: SpecialistPolicyModelV1,
    checkpoint_path: Path,
    *,
    expected_config: SpecialistModelConfigV1,
) -> FoundationInitProvenanceV1:
    """Load θ0's weights into `model` and return the θ0's own provenance.

    Weights only.  正典 §10.3 は phase 境界で「optimizer state を継続するか reset
    するか」を manifest に固定することを求めるが、ここは phase 境界ではなく
    **objective の変更**である: θ0 は教師あり (BC) で、この run は V-trace である。
    supervised な目的で推定した Adam の moment を V-trace へ持ち込む理由が無いため
    optimizer / scheduler / RNG / step / sampler cursor はすべて新規にする。

    Topology mismatch は fail-closed とする。形の合わないところだけ黙って捨てて
    残りを読むと、「θ0 から始めた」と記録しながら実際にはほぼ乱数初期化、という
    最も見つけにくい失敗になる。
    """
    # `publish_checkpoint_v1` embeds the body's SHA-256 in the filename, and the
    # loader verifies the bytes against it.  Deriving the expectation from the
    # name rather than re-hashing whatever is on disk keeps the check meaningful:
    # a renamed or truncated file fails instead of being accepted as itself.
    body_sha256 = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    name = checkpoint_path.name
    if not (name.startswith("checkpoint-") and name.endswith(".pt")):
        raise TrainFromTrajectoriesV1Error(
            f"bootstrap checkpoint must be a published 'checkpoint-<sha256>.pt': {name}"
        )
    named_hash = name[len("checkpoint-"):-len(".pt")]
    if named_hash != body_sha256:
        raise TrainFromTrajectoriesV1Error(
            f"bootstrap checkpoint bytes do not match its content-addressed name: "
            f"{body_sha256} != {named_hash}"
        )
    payload = load_checkpoint_for_inference_v1(
        checkpoint_path, expected_content_hash=body_sha256
    )
    metadata = payload["metadata"]
    stored_config = metadata.get("model_config")
    if stored_config != expected_config.to_dict():
        raise TrainFromTrajectoriesV1Error(
            "bootstrap checkpoint topology does not match this run's model config: "
            f"{stored_config!r} != {expected_config.to_dict()!r}"
        )
    missing, unexpected = model.load_state_dict(payload["model"], strict=True)
    if missing or unexpected:  # pragma: no cover - strict=True already raises
        raise TrainFromTrajectoriesV1Error("bootstrap checkpoint state_dict did not match")
    theta0 = parse_foundation_init_provenance_v1(metadata["foundation_init"])
    assert_primary_teacher_is_not_rule_v0_v1(theta0)
    # This run is a *warm start from θ0*, not θ0 itself.  Recording it as
    # `bc_distilled` would lose the fact that a distinct RL run sits between the
    # teacher and these weights; recording it as `random` would lose the teacher
    # entirely.  Keep the teacher list and name θ0 as the parent, so the lineage
    # teacher -> θ0 -> this run stays readable from the checkpoint alone.
    warm = FoundationInitProvenanceV1(
        init_kind=INIT_KIND_WARM_START_V1,
        teachers=theta0.teachers,
        parent_checkpoint_sha256=body_sha256,
        notes=f"warm start from θ0 ({theta0.init_kind}); {theta0.notes}"[:512],
    )
    assert_primary_teacher_is_not_rule_v0_v1(warm)
    return warm


def run_train_from_trajectories_v1(
    *,
    collection_run_dir: str | Path,
    run_name: str,
    max_steps: int,
    current_pool_epoch: int = 0,
    recipe_max_age: int = 0,
    trajectories_per_step: int | None = None,
    microbatch_trajectories: int | None = None,
    optimizer_kind: str = "adamw",
    learning_rate: float = 1.0e-3,
    rho_bar: float = 1.0,
    c_bar: float = 1.0,
    value_coefficient: float = 0.5,
    advantage_normalization: str = "none",
    max_gradient_norm: float = 1.0,
    hidden_dim: int = 128,
    card_dim: int = 64,
    symbol_dim: int = 16,
    seed: int = 0,
    device: str = "cpu",
    checkpoint_interval_steps: int = 10,
    source_commit: str | None = None,
    progress: bool | None = None,
    torch_threads: int | None = None,
    entropy_coefficient: float = _DEFAULT_ENTROPY_COEFFICIENT_V1,
    bc_coefficient: float = _DEFAULT_BC_COEFFICIENT_V1,
    # Test-only seam: never exposed as a CLI flag. Production always uses the
    # real fixed artifact base under runs/meta-specialist-training/.
    output_base_dir: Path = DEFAULT_TRAINING_OUTPUT_BASE_V1,
    # Where θ0's weights came from (正典 §1 / §9.3).  ``None`` means "this run
    # starts from independent random weights", which is recorded explicitly
    # rather than left blank -- a checkpoint that cannot say where it came from
    # is one whose "sharpened copy of the behaviour policy" failure mode cannot
    # be diagnosed later.  A BC-distilled θ0 passes the teacher it came from,
    # whose derivation boundary must already be qualified.
    foundation_init: FoundationInitProvenanceV1 | None = None,
    # Path to a θ0 checkpoint produced by BC distillation.  Weights only; every
    # other piece of training state starts fresh (see the load site).  When set,
    # the θ0's own FoundationInit provenance is carried forward so the lineage
    # from teacher -> θ0 -> this RL run stays inspectable.
    bootstrap_checkpoint_path: str | Path = "",
) -> dict[str, object]:
    foundation_init = (
        random_init_provenance_v1(notes="no teacher supplied to run_train_from_trajectories_v1")
        if foundation_init is None
        else foundation_init
    )
    run_name = _validate_run_name_v1(run_name)
    max_steps = _require_nonneg_int(max_steps, "--max-steps")
    current_pool_epoch = _require_nonneg_int(current_pool_epoch, "--current-pool-epoch")
    recipe_max_age = _require_nonneg_int(recipe_max_age, "--recipe-max-age")
    if trajectories_per_step is not None:
        trajectories_per_step = _require_positive_int(trajectories_per_step, "--trajectories-per-step")
    if microbatch_trajectories is not None:
        microbatch_trajectories = _require_positive_int(microbatch_trajectories, "--microbatch-trajectories")
    if optimizer_kind not in _OPTIMIZER_KINDS_V1:
        raise TrainFromTrajectoriesV1Error(f"--optimizer must be one of {sorted(_OPTIMIZER_KINDS_V1)}")
    learning_rate = _require_positive_float(learning_rate, "--learning-rate")
    rho_bar = _require_positive_float(rho_bar, "--rho-bar")
    c_bar = _require_positive_float(c_bar, "--c-bar")
    if type(value_coefficient) is bool or type(value_coefficient) not in (int, float) or not math.isfinite(float(value_coefficient)) or float(value_coefficient) < 0.0:
        raise TrainFromTrajectoriesV1Error("--value-coefficient must be a nonnegative finite number")
    value_coefficient = float(value_coefficient)
    max_gradient_norm = _require_positive_float(max_gradient_norm, "--max-gradient-norm")
    for name, value in (("--hidden-dim", hidden_dim), ("--card-dim", card_dim), ("--symbol-dim", symbol_dim)):
        _require_positive_int(value, name)
    if type(seed) is not int or type(seed) is bool:
        raise TrainFromTrajectoriesV1Error("--seed must be an int")
    checkpoint_interval_steps = _require_positive_int(checkpoint_interval_steps, "--checkpoint-interval-steps")

    collection_run_dir = Path(collection_run_dir)
    games_dir = collection_run_dir / "games"
    if not games_dir.is_dir():
        raise TrainFromTrajectoriesV1Error(
            f"--collection-run-dir {collection_run_dir} has no games/ directory "
            "(expected output of collect-trajectories)"
        )
    resolved_source_commit = source_commit or current_repo_commit_v1()
    resolved_device = _resolve_device_v1(device)
    entropy_coefficient = float(entropy_coefficient)
    if not math.isfinite(entropy_coefficient) or entropy_coefficient < 0.0:
        raise TrainFromTrajectoriesV1Error("--entropy-coefficient must be a nonnegative finite float")
    bc_coefficient = float(bc_coefficient)
    if not math.isfinite(bc_coefficient) or bc_coefficient < 0.0:
        raise TrainFromTrajectoriesV1Error("--bc-coefficient must be a nonnegative finite float")
    advantage_normalizer = AdvantageNormalizationV1.for_mode_v1(str(advantage_normalization))
    resolved_torch_threads = (
        _DEFAULT_TORCH_THREADS_V1 if torch_threads is None
        else _require_positive_int(torch_threads, "--torch-threads")
    )
    # A process-wide setting, set once here rather than per step: this entry
    # point owns the process it runs in.
    torch.set_num_threads(resolved_torch_threads)

    started_wall = time.monotonic()
    started_at_utc = datetime.now(UTC).isoformat()

    sys.stderr.write(f"[train-from-trajectories] Loading game records from {games_dir}...\n")
    sys.stderr.flush()

    output_root = Path(output_base_dir) / run_name
    _atomic_write_json_v1(
        output_root / "progress_summary.json",
        {
            "phase": "train-from-trajectories",
            "run_id": run_name,
            "completed": 0,
            "planned": max_steps,
            "percent": 0.0,
            "valid": None,
            "legal": None,
            "faults": None,
            "elapsed_seconds": 0.0,
            "throughput": None,
            "eta_seconds": None,
            "workers": None,
            "updated_at": time.time(),
        },
    )

    loaded, unreadable_reasons = _load_game_records_v1(
        games_dir, summary_path=output_root / "progress_summary.json", run_name=run_name,
        progress=progress,
    )
    if not loaded:
        raise TrainFromTrajectoriesV1Error(
            f"no readable game record found under {games_dir} "
            f"({len(unreadable_reasons)} unreadable)"
        )
    path_by_id = {id(record): path for path, record in loaded}
    kept, admission = admit_trajectories_v1(
        [record for _path, record in loaded],
        current_pool_epoch=current_pool_epoch, recipe_max_age=recipe_max_age,
    )
    admitted = [(path_by_id[id(record)], record) for record in kept]
    if not admitted:
        raise TrainFromTrajectoriesV1Error(
            f"no trajectory was admitted from {games_dir} within the pool-epoch age window "
            f"(current_pool_epoch={current_pool_epoch}, recipe_max_age={recipe_max_age}); "
            f"{len(loaded)} record(s) were read but none fell inside the window"
        )
    effective_trajectories_per_step = len(admitted) if trajectories_per_step is None else trajectories_per_step
    if effective_trajectories_per_step > len(admitted):
        raise TrainFromTrajectoriesV1Error(
            f"--trajectories-per-step ({trajectories_per_step}) exceeds the number of "
            f"admitted trajectories ({len(admitted)})"
        )
    _reject_an_unbounded_implicit_minibatch_v1(
        admitted,
        trajectories_per_step=trajectories_per_step,
        effective_trajectories_per_step=effective_trajectories_per_step,
        microbatch_trajectories=microbatch_trajectories,
    )
    games_found = len(loaded)
    admitted = _prepare_admitted_transitions_v1(
        admitted, summary_path=output_root / "progress_summary.json",
        run_name=run_name, progress=progress,
    )
    # Preparation roughly doubles the resident cost of the corpus (measured:
    # ~52 KB per prepared transition, so ~4.3 GB for the 87,258-transition
    # rule-agent collection).  Nothing after this point reads the raw lists --
    # only ``games_found``, captured above -- and every record that survived
    # admission is reachable through ``admitted``, so dropping these here keeps
    # the un-admitted remainder from staying resident for the whole run.
    del loaded, kept, path_by_id
    gc.collect()

    try:
        vocabulary = load_production_card_vocabulary_v1()
    except CardVocabularyRegistryError as exc:
        raise TrainFromTrajectoriesV1Error(f"could not load the production card vocabulary: {exc}") from exc
    model_config = SpecialistModelConfigV1(
        card_vocabulary_size=max(vocabulary.recognized_card_ids),
        hidden_dim=hidden_dim, card_dim=card_dim, symbol_dim=symbol_dim,
    )
    try:
        model = build_specialist_policy_model_v1(model_config, seed=seed).to(resolved_device)
    except NeuralModelV1Error as exc:
        raise TrainFromTrajectoriesV1Error(f"could not build the specialist policy model: {exc}") from exc

    # θ0 bootstrap: load *weights only* from a distilled Foundation checkpoint.
    # Optimizer, scheduler, RNG, step, and sampler cursor all start fresh -- this
    # is a new run that begins from θ0, not a resume of the distillation run.
    # Carrying the distillation's optimizer state would apply Adam moments
    # estimated under a supervised objective to a V-trace objective.
    if bootstrap_checkpoint_path:
        foundation_init = _load_bootstrap_weights_v1(
            model, Path(bootstrap_checkpoint_path), expected_config=model_config
        )

    if optimizer_kind == "adamw":
        optimizer: torch.optim.Optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    else:
        optimizer = torch.optim.SGD(model.parameters(), lr=learning_rate)

    recipe = {
        "collection_run_dir": str(collection_run_dir.resolve()),
        "current_pool_epoch": current_pool_epoch,
        "recipe_max_age": recipe_max_age,
        "trajectories_per_step": trajectories_per_step,
        "optimizer": optimizer_kind,
        "learning_rate": learning_rate,
        "rho_bar": rho_bar,
        "c_bar": c_bar,
        "value_coefficient": value_coefficient,
        "entropy_coefficient": entropy_coefficient,
        "bc_coefficient": bc_coefficient,
        "advantage_normalization": advantage_normalizer.mode,
        "max_gradient_norm": max_gradient_norm,
        "bootstrap_value": _BOOTSTRAP_VALUE_V1,
    }
    identity = build_training_identity_v1(
        snapshot_id=resolved_source_commit, config=model_config, recipe=recipe, seed=seed,
    )

    output_root = Path(output_base_dir) / run_name
    step, sampler_cursor, resumed = _restore_from_pointer_if_present_v1(
        output_root=output_root, model=model, optimizer=optimizer, identity=identity,
    )
    step_before = step

    def make_scorer() -> TrajectoryScorerV1:
        """One scorer per backward pass, so its caches are scoped to that pass.

        The candidate and state caches hold graph nodes; a scorer reused across
        two backward passes would reference a freed graph. Building it here,
        once per attempt, makes the correct lifetime the only reachable one.
        """
        return TrajectoryScorerV1(model, shared_candidate_cache=True)

    steps_to_take = max(0, max_steps - step)
    loss_trajectory: list[float | None] = []
    gradient_norms: list[float | None] = []
    health_trajectory: list[dict[str, object]] = []
    # A single step's loss is noisy; a trend over a window is what tells an
    # operator whether a multi-hour run is still improving.
    recent_losses: deque[float] = deque(maxlen=_TREND_WINDOW_STEPS_V1)
    earlier_loss_mean: float | None = None
    steps_skipped = 0
    transitions_consumed_this_run = 0
    scoring_failures: list[str] = []
    last_checkpoint_path: Path | None = None
    last_checkpoint_hash: str | None = None

    reporter = None
    if steps_to_take > 0:
        reporter = ProgressReporter(
            phase="train-from-trajectories", total=steps_to_take, run_id=run_name, unit="step",
            progress=progress, interval_seconds=_NONTTY_SNAPSHOT_INTERVAL_SECONDS_V1,
            summary_path=output_root / "progress_summary.json",
        )
    try:
        for _ in range(steps_to_take):
            minibatch, sampler_cursor = _next_minibatch_v1(
                admitted, cursor=sampler_cursor, size=effective_trajectories_per_step,
            )
            result = _take_trajectory_training_step_v1(
                minibatch, model=model, optimizer=optimizer,
                make_scorer=make_scorer, entropy_coefficient=entropy_coefficient,
                device=resolved_device,
                rho_bar=rho_bar, c_bar=c_bar,
                value_coefficient=value_coefficient, bc_coefficient=bc_coefficient,
                max_gradient_norm=max_gradient_norm,
                microbatch_trajectories=microbatch_trajectories,
                advantage_normalization=advantage_normalizer,
            )
            advantage_normalizer = result.advantage_normalization
            step += 1
            loss_trajectory.append(_json_safe_float(result.loss))
            gradient_norms.append(_json_safe_float(result.gradient_norm))
            transitions_consumed_this_run += result.transitions
            scoring_failures.extend(result.failure_reasons)
            if result.skipped:
                steps_skipped += 1
            health_trajectory.append(result.health.to_dict())
            if not result.skipped and math.isfinite(result.loss):
                if len(recent_losses) == recent_losses.maxlen:
                    earlier_loss_mean = sum(recent_losses) / len(recent_losses)
                recent_losses.append(result.loss)
            if reporter is not None:
                fields: dict[str, object] = {
                    "loss": _json_safe_float(result.loss),
                    "grad": _json_safe_float(result.gradient_norm),
                    "skipped": steps_skipped,
                }
                # dlogp: is the policy moving toward the behavior that produced
                # the data? clip: how much of the gradient V-trace is truncating?
                shift = result.health.mean_log_probability_shift
                if shift is not None:
                    fields["dlogp"] = round(shift, 4)
                clipped = result.health.clipped_importance_fraction
                if clipped is not None:
                    fields["clip_hi"] = round(clipped, 3)
                # The failure mode that matters here is the *low* side: a policy
                # that has run away from the data has rho ~ 0, so V-trace scales
                # its gradient to nothing and learning silently stops.
                vanished = result.health.vanishing_importance_fraction
                if vanished is not None:
                    fields["dead_rho"] = round(vanished, 3)
                # A baseline only removes the sign bias if it is near the return
                # it is meant to predict; V ~ 0 against a mean return of -0.75
                # means the critic has not learned and the advantage is still
                # negative almost everywhere.
                predicted = result.health.mean_state_value
                if predicted is not None:
                    fields["V"] = round(predicted, 3)
                observed = result.health.mean_terminal_return
                if observed is not None:
                    fields["ret"] = round(observed, 3)
                if recent_losses:
                    mean_recent = sum(recent_losses) / len(recent_losses)
                    fields["loss_avg"] = round(mean_recent, 5)
                    if earlier_loss_mean is not None:
                        fields["trend"] = (
                            "down" if mean_recent < earlier_loss_mean
                            else "up" if mean_recent > earlier_loss_mean else "flat"
                        )
                reporter.update(1, **fields)
            if step % checkpoint_interval_steps == 0:
                last_checkpoint_path, last_checkpoint_hash = _publish_checkpoint_and_pointer_v1(
                    output_root=output_root, model=model, optimizer=optimizer, identity=identity,
                    recipe=recipe, step=step, sampler_cursor=sampler_cursor,
                    foundation_init=foundation_init,
                )
    finally:
        if reporter is not None:
            reporter.close()

    last_checkpoint_path, last_checkpoint_hash = _publish_checkpoint_and_pointer_v1(
        output_root=output_root, model=model, optimizer=optimizer, identity=identity,
        recipe=recipe, step=step, sampler_cursor=sampler_cursor,
        foundation_init=foundation_init,
    )

    finished_at_utc = datetime.now(UTC).isoformat()
    wall_time_seconds = time.monotonic() - started_wall

    payload: dict[str, object] = {
        "schema_version": TRAIN_FROM_TRAJECTORIES_RUN_SUMMARY_SCHEMA_V1,
        "run_name": run_name,
        "collection_run_dir": str(collection_run_dir),
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "wall_time_seconds": round(wall_time_seconds, 3),
        "device": str(resolved_device),
        "source_commit": resolved_source_commit,
        "training_identity": identity.to_dict(),
        "recipe": recipe,
        "model_config": model_config.to_dict(),
        "output_root": str(output_root),
        "run_summary_path": str(output_root / "run_summary.json"),
        "progress_summary_path": str(output_root / "progress_summary.json"),
        "torch_threads": resolved_torch_threads,
        "learning_health_per_step": health_trajectory,
        "learning_health_last": health_trajectory[-1] if health_trajectory else None,
        "games_found": games_found,
        "games_unreadable": len(unreadable_reasons),
        "unreadable_game_records": unreadable_reasons[:_UNREADABLE_LISTED_CAP_V1],
        "unreadable_game_records_truncated": len(unreadable_reasons) > _UNREADABLE_LISTED_CAP_V1,
        "games_admitted": admission.admitted,
        "games_dropped_stale": admission.dropped,
        "drop_reasons": list(admission.drop_reasons[:_DROP_REASON_LISTED_CAP_V1]),
        "drop_reasons_truncated": len(admission.drop_reasons) > _DROP_REASON_LISTED_CAP_V1,
        "admitted_game_record_paths": [path for path, _record in admitted[:_GAME_PATH_LISTED_CAP_V1]],
        "admitted_game_record_paths_truncated": len(admitted) > _GAME_PATH_LISTED_CAP_V1,
        "transitions_admitted_total": sum(len(record["transitions"]) for _path, record in admitted),
        "trajectories_per_step": effective_trajectories_per_step,
        "resumed": resumed,
        "step_before": step_before,
        "step_after": step,
        "max_steps": max_steps,
        "steps_taken_this_run": step - step_before,
        "steps_skipped_this_run": steps_skipped,
        "sampler_cursor": sampler_cursor,
        "transitions_consumed_this_run": transitions_consumed_this_run,
        "scoring_failures_this_run": scoring_failures[:_UNREADABLE_LISTED_CAP_V1],
        "scoring_failures_this_run_truncated": len(scoring_failures) > _UNREADABLE_LISTED_CAP_V1,
        "loss_trajectory": loss_trajectory[:_STEP_METRIC_LISTED_CAP_V1],
        "gradient_norms": gradient_norms[:_STEP_METRIC_LISTED_CAP_V1],
        "step_metrics_truncated": len(loss_trajectory) > _STEP_METRIC_LISTED_CAP_V1,
        "checkpoint_path": str(last_checkpoint_path),
        "checkpoint_sha256": last_checkpoint_hash,
    }
    _atomic_write_json_v1(output_root / "run_summary.json", payload)

    last_loss = loss_trajectory[-1] if loss_trajectory else None
    last_grad_norm = gradient_norms[-1] if gradient_norms else None
    print(
        f"[train-from-trajectories] run={run_name} games_admitted={admission.admitted}/{games_found} "
        f"step={step_before}->{step} (budget={max_steps}) skipped={steps_skipped} "
        f"loss={last_loss} grad_norm={last_grad_norm} "
        f"checkpoint={last_checkpoint_hash[:12] if last_checkpoint_hash else None} "
        f"wall_time={wall_time_seconds:.1f}s -> {output_root}",
        file=sys.stderr,
    )
    return payload


__all__ = [
    "DEFAULT_TRAINING_OUTPUT_BASE_V1",
    "LATEST_CHECKPOINT_POINTER_SCHEMA_V1",
    "TRAIN_FROM_TRAJECTORIES_RUN_SUMMARY_SCHEMA_V1",
    "LearningHealthV1",
    "TrainFromTrajectoriesV1Error",
    "TrajectoryTrainingStepResultV1",
    "assert_on_policy_health_v1",
    "run_train_from_trajectories_v1",
]
