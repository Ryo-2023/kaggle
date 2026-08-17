"""Real actor worker pool for L5 trajectory collection.

`docs/superpowers/plans/2026-08-02-meta-specialist-learning-orchestration-v1.md`,
"Slice L5", requires: "Actor workers use `spawn`, one game/process by default,
a frozen checkpoint hash per job, one typed trajectory writer, bounded
stdout/stderr, timeout and process-group cleanup. A persistent-worker fast
path is disabled until it passes repeated engine-identity tests. Workers must
not initialize CUDA."

This module drives one *real* CABT game per job through the committed
``runtime.py``/``runtime_actions_v2.py`` transactional runtime and emits
``trajectory_v1.ActorTrajectoryTransitionV1`` records -- never a placeholder.

Reconstructing per-decision trajectories without reaching into runtime
internals
------------------------------------------------------------------------
``MetaSpecialistRuntime`` (committed, not modified here) drives one CABT
decision through ``greedy_decode_runtime_action_v2``, calling
``SpecialistDecisionSessionV2.logits`` once per distinct decode prefix and
finally ``.commit(outcome)`` with the committed
``SemanticRuntimeCompleteActionV2``.  Nothing in that path hands a caller the
per-step choice directly (only the final committed selection), and the
*policy* boundary (``SpecialistStepLogitPolicyV1``) deliberately never sees
local/private aliases.

This module wraps the *policy* the runtime is given with a recording layer
(`_RecordingSessionV1`/`_RecordingPolicyV1`) that captures each real
``(step_input, logits)`` pair the runtime actually queried, in query order.
After the runtime commits an outcome, ``_reconstruct_prefix_steps_v1``
rebuilds the ordered ``TrajectoryPrefixStepV1`` tuple *purely* from that
recorded, verbatim data plus the committed
``semantic_action.semantic_selection`` -- using nothing but multiset
(``collections.Counter``) differences between consecutive *recorded*
``step_input.semantic_prefix`` values (themselves canonical, alias-free, and
already validated by the runtime).  It never re-derives a private local
alias and never calls ``choose_lexicographic_alias_v1``.

A forced-STOP final step is never queried through ``.logits`` at all (see
``evaluate_specialist_step_v1``'s short-circuit), so it is reconstructed
directly via ``SpecialistStepInputV1``'s own public constructor at the exact
final canonical prefix, with the fixed ``behavior_log_probability=0.0`` the
schema requires for a forced STOP.

Each per-step log-probability is a real numerically-stable log-softmax over
the *exact* logits the behavior policy returned for that step (never
invented).  ``model_input`` is captured through a second, independent,
side-channel call to the same public, pure extraction functions
(``build_actor_visible_decision_state_v2`` / ``extract_specialist_model_input_v1``)
the runtime uses internally, wired in by ``_RecordingAgentV1`` immediately
before delegating to the real runtime call -- this is necessary because a
decision with zero legal candidates never invokes the policy at all, so
``model_input`` would otherwise be unavailable for that (still real, still
one-transition) decision.

Bootstrap behavior identity (no checkpoint yet)
------------------------------------------------
L1-L4 are complete, but no trained checkpoint exists yet: this module is
itself the *first* thing that can produce training data.  Its only supported
``behavior_kind`` is ``"rule_agent"``, bound to
``templates/meta_specialist/rule_policy_v1.UniformLegalPolicy`` (the P0
static rule bundle, ``candidate_class="static_rule_bundle"``) -- a real,
already-committed policy that returns genuine (if trivial) logits through
the exact same runtime/policy boundary a trained checkpoint would use.  Its
identity is the SHA-256 of ``rule_policy_v1.py``'s own bytes, mirroring how
the real submission archive derives ``policy_identity``.  Every job and every
written record carries ``behavior_kind="rule_agent"`` explicitly: this data
must never be read downstream as if it were policy-derived.

Known, explicitly documented placeholders (not fabrication)
-------------------------------------------------------------
No value head exists anywhere in this codebase yet (see
``neural_model_v1.SpecialistPolicyModelV1`` and the L5 spec's own
architecture section, which describes one as future work).  Every written
transition therefore carries ``value=0.0`` -- an honest declaration of "no
critic available for this behavior policy", not a fabricated estimate.
Likewise ``pool_epoch=0``/``policy_lag=0`` are fixed bootstrap constants: no
L7 calibration pool or learner-lag tracker exists yet to compute real ones.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
import queue
import random
import resource
import signal
import sys
import tempfile
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    STEP_INPUT_SCHEMA_V1,
    CardVocabularyV1,
    SemanticActionV1,
    SpecialistFeatureError,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    SpecialistStepLogitsV1,
    extract_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    ActorVisibleV2Error,
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.decks import (
    DeckAssetInput,
    DeckLockDecision,
    QualifiedDeckAsset,
    create_deck_lock,
    load_archetype_registry,
    qualify_deck_asset,
)
from mage_ptcg.meta_specialist.fault_diagnostics_v1 import (
    CanonicalGameIdentityV1,
    FaultDiagnosticsV1,
    capture_fault_v1,
    classify_retry_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    canonical_json_bytes_v2,
    parse_canonical_json_bytes_v2,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    opponent_version_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.runtime import (
    CommittedSemanticDecisionV2,
    PolicyTelemetrySnapshot,
    RuntimeConstraintManifest,
    SpecialistDecisionPolicyV2,
    SpecialistDecisionSessionV2,
    StepLogitPolicyFactory,
    make_agent,
)
from mage_ptcg.meta_specialist.trajectory_v1 import (
    ActorTrajectoryTransitionV1,
    TrajectoryPrefixStepV1,
    TrajectoryV1Error,
    build_actor_trajectory_transition_v1,
    validate_actor_trajectory_transition_payload_v1,
)

if TYPE_CHECKING:  # pragma: no cover - import only evaluated by a type checker
    # Never imported at runtime at module scope: neural_policy_v1 (transitively
    # torch) must load only *after* a spawned worker's guard_worker_against_cuda_v1
    # has run -- see _build_neural_agent_policy_factory_v1's lazy import.
    from mage_ptcg.meta_specialist.neural_policy_v1 import SpecialistNeuralPolicyV1


GAME_RECORD_SCHEMA_V1 = "meta-specialist-actor-pool-game-record-v1"
DEFAULT_MAX_STEPS_V1 = 4_000
DEFAULT_TIMEOUT_SECONDS_V1 = 600.0
# A game record bundles dozens of per-decision transitions (each with its own
# model input and prefix steps) into one JSON tree, so it is legitimately one
# to two orders of magnitude larger than local_dataset_v2's per-decision
# untrusted-record bound (MAX_CANONICAL_JSON_NODES_V2 = 100_000). A real
# 2,000-game p0-rule-agent-2000 collection run measured every completed
# record at <= 99,897 nodes / ~1.17 MiB (74 transitions, the
# grimmsnarl_froslass_munkidori lane); the untrusted node/byte bounds sat
# right at that edge and silently faulted every longer game (142/2000, 7.1%),
# biasing the surviving dataset toward short games. These game-record bounds
# give roughly 20x headroom over that measured maximum -- enough for games
# several times longer than anything observed -- while still failing closed
# for a genuinely pathological (e.g. runaway/looping) record.
MAX_GAME_RECORD_JSON_NODES_V1 = 2_000_000
MAX_GAME_RECORD_JSON_BYTES_V1 = 64 * 1024 * 1024
# A behavior policy with no critic (every P0 rule/static policy) has no
# principled value estimate; 0.0 is an explicit "no information" convention,
# never a computed/learned number. See the module docstring.
_NO_CRITIC_VALUE_PLACEHOLDER_V1 = 0.0
# Process-wide RLIMIT_FSIZE backstop for one worker (stdout/stderr capture
# files plus its own game-record write). Generous enough that a real game
# record is never at risk; small enough that a runaway print loop cannot
# fill the disk before the outer wall-clock timeout notices it.
_WORKER_RLIMIT_FSIZE_BYTES_V1 = 64 * 1024 * 1024
# Bounded excerpt length stored inline in a fault/diagnostic record.
_CAPTURE_EXCERPT_BYTES_V1 = 8_000
_HEX64 = frozenset("0123456789abcdef")

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARCHETYPE_REGISTRY_PATH_V1 = (
    _REPO_ROOT / "configs" / "meta_specialist" / "archetypes_v1.json"
)
_RULE_POLICY_TEMPLATE_PATH_V1 = (
    _REPO_ROOT / "templates" / "meta_specialist" / "rule_policy_v1.py"
)


class ActorPoolV1Error(ValueError):
    """Raised when actor worker pool creation, job configuration, or a game record is invalid."""


def _require_nonempty_str(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ActorPoolV1Error(f"{name} must be a nonempty string")
    return value


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _HEX64 for c in value):
        raise ActorPoolV1Error(f"{name} must be a 64-character lowercase hex SHA-256 string")
    return value


def _require_hex40(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 40 or any(c not in _HEX64 for c in value):
        raise ActorPoolV1Error(f"{name} must be a 40-character lowercase hex commit id")
    return value


def _require_nonneg_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ActorPoolV1Error(f"{name} must be a nonnegative int")
    return value


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ActorPoolV1Error(f"{name} must be a positive int")
    return value


def _require_positive_float(value: object, name: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or float(value) <= 0.0:
        raise ActorPoolV1Error(f"{name} must be a positive float")
    return float(value)


_BEHAVIOR_KINDS_V1 = frozenset({"rule_agent", "neural_specialist", "neural_specialist_v4"})
# Opponent identity is *registry-driven*, not a literal enum here.  A hard-coded
# list of names cannot carry 正典 §13's opponent instance (deck hash, policy
# hash, policy type, strength band, sampling weight, usage boundary) and -- as
# this module previously demonstrated -- a name in such a list can silently have
# no effect on which policy actually plays: every entry still ran the engine's
# built-in `rule` agent.  Resolution now happens against
# `opponents/pool_manifest.json` inside `run_one_actor_game_v1`, fail-closed.
#
# `ActorJobConfigV1` only checks that the id is a non-empty string: the config
# is a picklable primitive that must survive a `spawn` boundary, while the
# registry lives on disk where the worker re-reads it.  See `opponent_pool_v1`
# for resolution, identity verification, and the hidden-information rules.
_NEURAL_BEHAVIOR_KIND_V1 = "neural_specialist"
_NEURAL_BEHAVIOR_KIND_V4 = "neural_specialist_v4"
_DECODING_MODES_V1 = frozenset({"greedy", "sample"})


@dataclass(frozen=True, slots=True)
class ActorJobConfigV1:
    """Validated job configuration for a single actor worker run.

    Everything a worker needs is a picklable primitive so a ``spawn`` child
    can reconstruct its full binding (qualified deck, deck lock, vocabulary,
    behavior policy) from scratch: several of those objects use process-local
    weakref issuance registries (``QualifiedDeckAsset``,
    ``CardVocabularyV1`` from the production registry) and are therefore
    *not* validly reusable across a process boundary even if pickled.
    """

    job_id: str
    archetype_id: str
    deck_csv_path: str
    source_commit: str
    env_seed: int
    seat: int
    behavior_kind: str
    behavior_identity: str
    opponent_kind: str
    pool_epoch: int = 0
    policy_lag: int = 0
    non_terminal_discount: float = 1.0
    max_steps: int = DEFAULT_MAX_STEPS_V1
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS_V1
    # Neural-subject-only fields; empty/default for every "rule_agent" job.
    neural_checkpoint_path: str = ""
    # V4 artifacts are closed independently-addressed files: both hashes are
    # required rather than inferring one from the live path at worker start.
    neural_checkpoint_file_sha256: str = ""
    neural_checkpoint_tensor_state_sha256: str = ""
    decoding_mode: str = "greedy"
    sampling_seed: int = 0
    opponent_deck_csv_path: str = ""
    retry_index: int = 0
    canonical_game_identity: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.job_id, "job_id")
        _require_nonempty_str(self.archetype_id, "archetype_id")
        _require_nonempty_str(self.deck_csv_path, "deck_csv_path")
        _require_hex40(self.source_commit, "source_commit")
        _require_nonneg_int(self.env_seed, "env_seed")
        if type(self.seat) is not int or self.seat not in (0, 1):
            raise ActorPoolV1Error("seat must be 0 or 1")
        if self.behavior_kind not in _BEHAVIOR_KINDS_V1:
            raise ActorPoolV1Error(f"behavior_kind must be one of {sorted(_BEHAVIOR_KINDS_V1)}")
        _require_hex64(self.behavior_identity, "behavior_identity")
        # Resolved against the on-disk registry in `run_one_actor_game_v1`, not
        # against a literal enum -- see the note above `_BEHAVIOR_KINDS_V1`.
        _require_nonempty_str(self.opponent_kind, "opponent_kind")
        _require_nonneg_int(self.pool_epoch, "pool_epoch")
        _require_nonneg_int(self.policy_lag, "policy_lag")
        discount = _require_positive_float(self.non_terminal_discount, "non_terminal_discount")
        if discount > 1.0:
            raise ActorPoolV1Error("non_terminal_discount must be in (0, 1]")
        _require_positive_int(self.max_steps, "max_steps")
        _require_positive_float(self.timeout_seconds, "timeout_seconds")
        if type(self.neural_checkpoint_path) is not str:
            raise ActorPoolV1Error("neural_checkpoint_path must be a string")
        is_neural_v1 = self.behavior_kind == _NEURAL_BEHAVIOR_KIND_V1
        is_neural_v4 = self.behavior_kind == _NEURAL_BEHAVIOR_KIND_V4
        is_neural = is_neural_v1 or is_neural_v4
        if is_neural:
            _require_nonempty_str(self.neural_checkpoint_path, "neural_checkpoint_path")
        elif self.neural_checkpoint_path:
            raise ActorPoolV1Error(
                "neural_checkpoint_path must be empty unless behavior_kind is "
                f"{_NEURAL_BEHAVIOR_KIND_V1!r} or {_NEURAL_BEHAVIOR_KIND_V4!r}"
            )
        for field in ("neural_checkpoint_file_sha256", "neural_checkpoint_tensor_state_sha256"):
            value = getattr(self, field)
            if type(value) is not str:
                raise ActorPoolV1Error(f"{field} must be a string")
        if is_neural_v4:
            _require_hex64(self.neural_checkpoint_file_sha256, "neural_checkpoint_file_sha256")
            _require_hex64(self.neural_checkpoint_tensor_state_sha256, "neural_checkpoint_tensor_state_sha256")
        elif self.neural_checkpoint_file_sha256 or self.neural_checkpoint_tensor_state_sha256:
            raise ActorPoolV1Error(
                "V4 checkpoint hashes must be empty unless behavior_kind is "
                f"{_NEURAL_BEHAVIOR_KIND_V4!r}"
            )
        if self.decoding_mode not in _DECODING_MODES_V1:
            raise ActorPoolV1Error(f"decoding_mode must be one of {sorted(_DECODING_MODES_V1)}")
        if not is_neural and self.decoding_mode != "greedy":
            raise ActorPoolV1Error(
                f"decoding_mode must be 'greedy' unless behavior_kind is {_NEURAL_BEHAVIOR_KIND_V1!r}"
            )
        _require_nonneg_int(self.sampling_seed, "sampling_seed")
        _require_nonneg_int(self.retry_index, "retry_index")
        if self.canonical_game_identity is not None:
            CanonicalGameIdentityV1.from_dict(self.canonical_game_identity)

    def to_payload(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "archetype_id": self.archetype_id,
            "deck_csv_path": self.deck_csv_path,
            "source_commit": self.source_commit,
            "env_seed": self.env_seed,
            "seat": self.seat,
            "behavior_kind": self.behavior_kind,
            "behavior_identity": self.behavior_identity,
            "opponent_kind": self.opponent_kind,
            "pool_epoch": self.pool_epoch,
            "policy_lag": self.policy_lag,
            "non_terminal_discount": self.non_terminal_discount,
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "neural_checkpoint_path": self.neural_checkpoint_path,
            "neural_checkpoint_file_sha256": self.neural_checkpoint_file_sha256,
            "neural_checkpoint_tensor_state_sha256": self.neural_checkpoint_tensor_state_sha256,
            "decoding_mode": self.decoding_mode,
            "sampling_seed": self.sampling_seed,
            "opponent_deck_csv_path": self.opponent_deck_csv_path,
            "retry_index": self.retry_index,
            "canonical_game_identity": dict(self.canonical_game_identity) if self.canonical_game_identity else None,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ActorJobConfigV1":
        return cls(**dict(payload))


def derive_actor_job_id_v1(
    *,
    archetype_id: str,
    deck_csv_path: str,
    source_commit: str,
    env_seed: int,
    seat: int,
    behavior_kind: str,
    behavior_identity: str,
    opponent_kind: str,
    attempt: int = 0,
    decoding_mode: str = "greedy",
    sampling_seed: int = 0,
) -> str:
    """Content-address one job's identity from its full reproducible recipe.

    ``decoding_mode``/``sampling_seed`` participate in the hash (defaulted
    for backward compatibility) because they change what a neural subject's
    game actually realizes: two otherwise-identical jobs that differ only in
    "greedy" vs "sample" (or in the sampling seed) are different recipes and
    must never collide on -- and silently resume-skip via -- the same
    ``job_id``.  ``neural_checkpoint_path`` deliberately does not
    participate: like the rule policy template's fixed path, only its
    *content* (``behavior_identity``) is part of the recipe identity.
    """
    return content_id(
        "meta-specialist-actor-pool-job-v1",
        {
            "archetype_id": archetype_id,
            "deck_csv_path": str(deck_csv_path),
            "source_commit": source_commit,
            "env_seed": env_seed,
            "seat": seat,
            "behavior_kind": behavior_kind,
            "behavior_identity": behavior_identity,
            "opponent_kind": opponent_kind,
            "attempt": attempt,
            "decoding_mode": decoding_mode,
            "sampling_seed": sampling_seed,
        },
    )


def derive_game_sampling_seed_v1(
    *, base_seed: int, env_seed: int, archetype_id: str, opponent_kind: str, seat: int,
) -> int:
    """Derive one independent, reproducible RNG seed for a game.

    A single ``random.Random(base_seed)`` recreated for every game makes all
    games share the same exploration sequence.  Hashing the complete game
    identity gives independent streams while keeping collection replayable.
    """
    if type(base_seed) is not int or base_seed < 0:
        raise ActorPoolV1Error("base_seed must be a nonnegative int")
    if type(env_seed) is not int or env_seed < 0:
        raise ActorPoolV1Error("env_seed must be a nonnegative int")
    if type(archetype_id) is not str or not archetype_id:
        raise ActorPoolV1Error("archetype_id must be a nonempty string")
    if type(opponent_kind) is not str or not opponent_kind:
        raise ActorPoolV1Error("opponent_kind must be a nonempty string")
    if type(seat) is not int or seat not in (0, 1):
        raise ActorPoolV1Error("seat must be 0 or 1")
    payload = json.dumps(
        [base_seed, env_seed, archetype_id, opponent_kind, seat],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 63) - 1)


def current_repo_commit_v1(repo_root: str | Path = _REPO_ROOT) -> str:
    """Read the exact checked-out commit of this worktree (real, not fabricated)."""
    import subprocess

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    commit = completed.stdout.strip()
    if completed.returncode != 0 or len(commit) != 40 or any(c not in _HEX64 for c in commit):
        raise ActorPoolV1Error(f"could not resolve a git commit for {repo_root}: {completed.stderr.strip()}")
    return commit


# --------------------------------------------------------------------------
# Bootstrap rule-agent behavior identity (loaded by path: templates/ is not a
# package, it is bundled verbatim into a submission archive).
# --------------------------------------------------------------------------


def _load_rule_policy_template_module_v1() -> Any:
    spec = importlib.util.spec_from_file_location(
        "mage_ptcg_actor_pool_rule_policy_template_v1", _RULE_POLICY_TEMPLATE_PATH_V1
    )
    if spec is None or spec.loader is None:
        raise ActorPoolV1Error(
            f"could not load the rule policy template at {_RULE_POLICY_TEMPLATE_PATH_V1}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rule_agent_behavior_identity_v1() -> str:
    """SHA-256 of ``rule_policy_v1.py``'s own bytes -- the P0 bootstrap behavior identity."""
    if not _RULE_POLICY_TEMPLATE_PATH_V1.is_file():
        raise ActorPoolV1Error(f"rule policy template is missing: {_RULE_POLICY_TEMPLATE_PATH_V1}")
    return hashlib.sha256(_RULE_POLICY_TEMPLATE_PATH_V1.read_bytes()).hexdigest()


def _build_rule_agent_policy_factory_v1() -> tuple[StepLogitPolicyFactory, str]:
    module = _load_rule_policy_template_module_v1()
    identity = rule_agent_behavior_identity_v1()
    return module.UniformLegalPolicyFactory(policy_identity=identity), identity


def build_rule_agent_policy_factory_v1() -> tuple[StepLogitPolicyFactory, str]:
    """Return the sealed public teacher factory used by research relabeling."""
    return _build_rule_agent_policy_factory_v1()


# --------------------------------------------------------------------------
# Neural behavior identity: a job may name a checkpointed subject by content
# hash + path.  Loading (and therefore importing torch) happens only inside
# these functions, called only after a worker's guard_worker_against_cuda_v1
# has already run -- see the module docstring's CUDA discipline and
# `_install_worker_isolation_v1`.  Never imported at module scope.
# --------------------------------------------------------------------------


def neural_checkpoint_behavior_identity_v1(checkpoint_path: str | Path) -> str:
    """SHA-256 of one checkpoint file's exact bytes -- the neural behavior identity.

    Mirrors ``rule_agent_behavior_identity_v1``'s discipline (hash the exact
    on-disk artifact bytes actually read) for a ``neural_specialist`` job's
    checkpoint file.
    """
    path = Path(checkpoint_path)
    if not path.is_file():
        raise ActorPoolV1Error(f"neural checkpoint is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


_GUMBEL_UNIFORM_EPSILON_V1 = 1e-12


def _draw_gumbel_noise_v1(rng: random.Random) -> float:
    """One iid standard-Gumbel(0,1) draw via inverse CDF, ``-log(-log(U))``.

    Adding one independent draw of this to each candidate's real logit, then
    taking the argmax, is the Gumbel-max reparameterization of categorical
    sampling: the runtime's deterministic argmax realizes an exact sample of
    the base categorical distribution.  The decode logits and base logits are
    both retained: only the base logits define the behavior probability,
    while the perturbed logits are returned to the decoder.
    """
    uniform = min(max(rng.random(), _GUMBEL_UNIFORM_EPSILON_V1), 1.0 - _GUMBEL_UNIFORM_EPSILON_V1)
    return -math.log(-math.log(uniform))


class _NeuralSamplingSessionV1:
    """Wraps one real neural decision session; injects seeded Gumbel(0,1) noise.

    Every ``.logits()`` call is answered with the wrapped session's own real
    logits plus one fresh, RNG-drawn Gumbel offset per candidate (and per
    STOP, when legal) -- never a placeholder.  The unperturbed result is
    retained separately because Gumbel noise is a sampling device, not part of
    the categorical distribution whose log probability is recorded.
    """

    def __init__(self, inner: SpecialistDecisionSessionV2, rng: random.Random) -> None:
        self._inner = inner
        self._rng = rng
        self._last_base_logits: SpecialistStepLogitsV1 | None = None

    def logits(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        base = self._inner.logits(model_input, step_input)
        self._last_base_logits = base
        semantic_logits = tuple(
            value + _draw_gumbel_noise_v1(self._rng) for value in base.semantic_logits
        )
        stop_logit = (
            None if base.stop_logit is None
            else base.stop_logit + _draw_gumbel_noise_v1(self._rng)
        )
        return SpecialistStepLogitsV1(semantic_logits=semantic_logits, stop_logit=stop_logit)

    def behavior_logits(self) -> SpecialistStepLogitsV1:
        """Return the unperturbed logits for the most recent query."""
        if self._last_base_logits is None:
            raise ActorPoolV1Error("behavior_logits requested before logits")
        return self._last_base_logits

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        self._inner.commit(outcome)

    def abort(self) -> None:
        self._inner.abort()


class _NeuralAgentPolicyV1:
    """One fresh, weak-referenceable policy object per ``new_policy()`` call.

    Wraps the one shared loaded neural policy for this job (loading a
    checkpoint is the expensive part; this wrapper itself is trivial state,
    matching ``make_agent``'s requirement that every ``new_policy()`` call
    return a distinct object -- see ``runtime._claim_fresh_policy_object``).
    In ``"sample"`` mode every decision session injects seeded Gumbel noise;
    in ``"greedy"`` mode the model's own logits pass through unperturbed,
    identical to ``neural_policy_v1``'s own default session behavior.
    """

    def __init__(
        self,
        inner: SpecialistDecisionPolicyV2,
        *,
        decoding_mode: str,
        rng: random.Random | None,
    ) -> None:
        self._inner = inner
        self._decoding_mode = decoding_mode
        self._rng = rng

    def reset(self) -> None:
        self._inner.reset()

    def begin_decision(self) -> SpecialistDecisionSessionV2:
        session = self._inner.begin_decision()
        if self._decoding_mode == "sample":
            if self._rng is None:  # pragma: no cover - constructed together, defensive only
                raise ActorPoolV1Error("sample decoding mode requires a seeded rng")
            return _NeuralSamplingSessionV1(session, self._rng)
        return session

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return self._inner.policy_telemetry()


class _NeuralAgentPolicyFactoryV1:
    """Produces one fresh ``_NeuralAgentPolicyV1`` per ``new_policy()`` call.

    The checkpoint is loaded exactly once by the caller (expensive: reads
    and content-hash-verifies the file, builds the topology, loads its state
    dict); only the thin per-game wrapper -- and, in ``"sample"`` mode, a
    fresh ``random.Random(sampling_seed)`` -- is constructed per call, so a
    run replayed with the same ``env_seed``/``sampling_seed`` reproduces.
    """

    def __init__(
        self, *, policy: "SpecialistNeuralPolicyV1", decoding_mode: str, sampling_seed: int,
    ) -> None:
        self._policy = policy
        self._decoding_mode = decoding_mode
        self._sampling_seed = sampling_seed

    def new_policy(self) -> SpecialistDecisionPolicyV2:
        rng = random.Random(self._sampling_seed) if self._decoding_mode == "sample" else None
        return _NeuralAgentPolicyV1(self._policy, decoding_mode=self._decoding_mode, rng=rng)


def _build_neural_agent_policy_factory_v1(
    job: "ActorJobConfigV1", *, deck_lock: DeckLockDecision,
) -> tuple[StepLogitPolicyFactory, str]:
    """Load ``job.neural_checkpoint_path`` and bind it into a fresh-per-game factory.

    ``neural_policy_v1`` (and, transitively, ``torch``) is imported here,
    lazily, inside the function body -- never at this module's top level --
    so a worker process never touches torch until after
    ``_install_worker_isolation_v1``/``guard_worker_against_cuda_v1`` has
    already made CUDA unreachable.
    """
    from mage_ptcg.meta_specialist.neural_policy_v1 import (
        load_specialist_neural_policy_from_checkpoint_v1,
    )

    live_identity = neural_checkpoint_behavior_identity_v1(job.neural_checkpoint_path)
    policy = load_specialist_neural_policy_from_checkpoint_v1(
        job.neural_checkpoint_path,
        expected_content_hash=live_identity,
        checkpoint_lineage_id=deck_lock.policy_lineage_id,
    )
    factory = _NeuralAgentPolicyFactoryV1(
        policy=policy, decoding_mode=job.decoding_mode, sampling_seed=job.sampling_seed,
    )
    return factory, live_identity


def _build_neural_agent_policy_factory_v4(
    job: "ActorJobConfigV1", *, checkpoint_lineage_id: str,
) -> tuple[StepLogitPolicyFactory, str]:
    """Load one closed V4 checkpoint into the existing actor runtime boundary.

    Unlike ``neural_specialist``'s legacy v1 checkpoint, V4 requires the
    caller-provided file and tensor-state SHA-256 values.  The live file hash
    remains independently recomputed here so a moved/replaced file cannot be
    silently accepted merely because a job payload names an old digest.
    """
    from mage_ptcg.meta_specialist.neural_policy_v4 import (
        SpecialistNeuralPolicyV4Factory,
        load_specialist_neural_policy_from_checkpoint_v4,
    )

    live_identity = neural_checkpoint_behavior_identity_v1(job.neural_checkpoint_path)
    if live_identity != job.neural_checkpoint_file_sha256:
        raise ActorPoolV1Error("V4 checkpoint file SHA-256 does not match the job binding")
    if live_identity != job.behavior_identity:
        raise ActorPoolV1Error("V4 checkpoint file SHA-256 does not match behavior_identity")
    policy = load_specialist_neural_policy_from_checkpoint_v4(
        job.neural_checkpoint_path,
        expected_file_sha256=job.neural_checkpoint_file_sha256,
        expected_tensor_state_sha256=job.neural_checkpoint_tensor_state_sha256,
        checkpoint_lineage_id=checkpoint_lineage_id,
    )
    # The V4 factory supplies a fresh hidden state per game.  Greedy rollout
    # uses it directly; sampled collection retains the established seeded
    # Gumbel wrapper and never changes the checkpoint's base logits.
    base_factory = SpecialistNeuralPolicyV4Factory(policy)
    if job.decoding_mode == "greedy":
        return base_factory, live_identity
    return _NeuralAgentPolicyFactoryV1(
        policy=policy, decoding_mode=job.decoding_mode, sampling_seed=job.sampling_seed,
    ), live_identity


def engine_identity_v1() -> tuple[Callable[..., dict[str, Any]], str, str]:
    """Import the shipped CABT engine entry point and pin its exact bytes.

    Mirrors ``cabt_legality_v1._load_run_match``'s identity discipline
    without importing that module's private helper: same entry point
    (``scripts.test_sim.run_match``), same "hash the exact source bytes" rule.
    """
    try:
        from scripts.test_sim import run_match
    except Exception as exc:  # pragma: no cover - environment without the engine
        raise ActorPoolV1Error(
            "CABT engine entry point scripts.test_sim.run_match is unavailable"
        ) from exc
    module_path = Path(getattr(run_match, "__globals__", {}).get("__file__", "")).resolve()
    if not module_path.is_file():
        raise ActorPoolV1Error("could not locate the CABT engine source file")
    digest = hashlib.sha256(module_path.read_bytes()).hexdigest()
    return run_match, str(module_path), digest


def _build_actor_pool_deck_binding_v1(
    *,
    archetype_id: str,
    deck_csv_path: Path,
    source_commit: str,
    archetype_registry_path: Path = DEFAULT_ARCHETYPE_REGISTRY_PATH_V1,
) -> tuple[QualifiedDeckAsset, DeckLockDecision, CardVocabularyV1]:
    """Rebuild a fresh, real qualification for this deck -- required per process.

    ``QualifiedDeckAsset``/the production ``CardVocabularyV1`` both seal
    themselves to a process-local weakref issuance registry at construction
    time; neither can be validly reused after being pickled across a
    ``spawn`` boundary. Every worker therefore re-derives both from scratch,
    using a genuine CABT legality probe game -- never a cached/replayed
    verdict.
    """
    registry = load_archetype_registry(archetype_registry_path)
    archetype = registry.archetypes.get(archetype_id)
    if archetype is None:
        raise ActorPoolV1Error(f"unknown archetype_id: {archetype_id}")
    vocabulary = load_production_card_vocabulary_v1()
    asset_input = DeckAssetInput.from_path(
        asset_id=f"actor-pool-seed-{archetype_id}",
        archetype_id=archetype_id,
        path=deck_csv_path,
        source_ref=f"runs/meta-specialist-seed-qualification/materialized/{deck_csv_path.name}",
        source_commit=source_commit,
        asset_class="deck_only",
        usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2",
        card_database_version=vocabulary.environment_version,
    )
    from mage_ptcg.meta_specialist.cabt_legality_v1 import make_cabt_legality_v1

    qualified = qualify_deck_asset(
        asset_input,
        archetype,
        known_card_ids=vocabulary.recognized_card_ids,
        cabt_legality=make_cabt_legality_v1(),
    )
    foundation_init_id = content_id(
        "meta-specialist-actor-pool-v1-bootstrap-foundation-init",
        {"archetype_id": archetype_id, "deck_identity": qualified.deck_identity},
    )
    joint_race_schedule_id = content_id(
        "meta-specialist-actor-pool-v1-bootstrap-joint-race-schedule",
        {"archetype_id": archetype_id, "deck_identity": qualified.deck_identity},
    )
    deck_lock = create_deck_lock(
        archetype_id=archetype_id,
        selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,),
        foundation_init_id=foundation_init_id,
        joint_race_schedule_id=joint_race_schedule_id,
        # Not a completed L8 joint race: DeckLockDecision is a structural
        # requirement of MetaSpecialistRuntime, not a race result, here.
        equal_transition_budget=1,
    )
    return qualified, deck_lock, vocabulary


# --------------------------------------------------------------------------
# Recording layer: captures real per-step (step_input, logits) pairs and the
# real committed semantic selection, then reconstructs TrajectoryPrefixStepV1
# from that recorded data alone.
# --------------------------------------------------------------------------


def _step_log_probabilities_v1(
    semantic_logits: tuple[float, ...], stop_logit: float | None,
) -> tuple[float, ...]:
    """Numerically stable log-softmax over one step's real observed logits."""
    logits = semantic_logits + (() if stop_logit is None else (stop_logit,))
    if not logits:
        raise ActorPoolV1Error("cannot normalize an empty logit domain")
    maximum = max(logits)
    denominator = math.fsum(math.exp(value - maximum) for value in logits)
    log_denominator = maximum + math.log(denominator)
    return tuple(value - log_denominator for value in logits)


def _reconstruct_prefix_steps_v1(
    *,
    recorded: tuple[
        tuple[SpecialistStepInputV1, SpecialistStepLogitsV1]
        | tuple[SpecialistStepInputV1, SpecialistStepLogitsV1, SpecialistStepLogitsV1], ...
    ],
    final_semantic_selection: tuple[SemanticActionV1, ...],
    order_semantics: str,
) -> tuple[TrajectoryPrefixStepV1, ...]:
    """Rebuild one decision's ordered prefix steps from real recorded (step, logits) pairs.

    Uses only: the verbatim recorded ``step_input`` objects (each the exact
    canonical step the runtime itself built and queried), their verbatim
    decode logits, optional unperturbed behavior logits, and the runtime's
    own committed semantic selection. Two-item entries remain valid for
    deterministic policies; sampled policies pass three items so Gumbel noise
    cannot corrupt the behavior probability.
    Every chosen token is recovered as a multiset difference between two
    *recorded* canonical prefixes -- never a re-derived local alias.
    """
    n = len(final_semantic_selection)
    m = len(recorded)
    if m not in (n, n + 1):
        raise ActorPoolV1Error(
            f"recorded step count {m} is inconsistent with a {n}-token committed selection"
        )

    def _one_new_token(before: Counter, after: Counter) -> SemanticActionV1:
        diff = after - before
        if sum(diff.values()) != 1 or before + diff != after:
            raise ActorPoolV1Error("recorded canonical prefixes did not grow by exactly one token")
        return next(iter(diff.elements()))

    chosen_rows: list[SemanticActionV1] = []
    for index in range(m - 1):
        before = Counter(recorded[index][0].semantic_prefix)
        after = Counter(recorded[index + 1][0].semantic_prefix)
        chosen_rows.append(_one_new_token(before, after))

    if m == n and m > 0:
        before = Counter(recorded[m - 1][0].semantic_prefix)
        total = Counter(final_semantic_selection)
        chosen_rows.append(_one_new_token(before, total))

    if len(chosen_rows) != n:
        raise ActorPoolV1Error("reconstructed chosen-row count does not match the committed selection")

    accumulated: tuple[SemanticActionV1, ...] = ()
    for row in chosen_rows:
        accumulated = (*accumulated, row)
        if order_semantics == "unordered_set":
            accumulated = tuple(sorted(accumulated, key=lambda item: item.canonical_bytes))
    expected_final = (
        tuple(sorted(final_semantic_selection, key=lambda item: item.canonical_bytes))
        if order_semantics == "unordered_set"
        else final_semantic_selection
    )
    if accumulated != expected_final:
        raise ActorPoolV1Error(
            "reconstructed chosen rows do not reproduce the runtime's committed selection"
        )

    def _parts(
        entry: tuple[SpecialistStepInputV1, SpecialistStepLogitsV1]
        | tuple[SpecialistStepInputV1, SpecialistStepLogitsV1, SpecialistStepLogitsV1],
    ) -> tuple[SpecialistStepInputV1, SpecialistStepLogitsV1, SpecialistStepLogitsV1]:
        if len(entry) == 2:
            step_input, logits = entry
            return step_input, logits, logits
        if len(entry) == 3:
            step_input, decode_logits, behavior_logits = entry
            return step_input, decode_logits, behavior_logits
        raise ActorPoolV1Error("recorded logit entry must contain two or three items")

    steps: list[TrajectoryPrefixStepV1] = []
    for index in range(n):
        step_input, _decode_logits, behavior_logits = _parts(recorded[index])
        row = chosen_rows[index]
        class_index = next(
            (
                position
                for position, item in enumerate(step_input.allowed_semantic_classes)
                if item.semantic_row == row
            ),
            None,
        )
        if class_index is None:
            raise ActorPoolV1Error("reconstructed chosen row is outside this step's legal domain")
        log_probabilities = _step_log_probabilities_v1(
            behavior_logits.semantic_logits, behavior_logits.stop_logit
        )
        steps.append(TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=False, chosen_is_stop=False,
            chosen_semantic_action=row, behavior_log_probability=log_probabilities[class_index],
        ))

    if m == n + 1:
        step_input, _decode_logits, behavior_logits = _parts(recorded[m - 1])
        log_probabilities = _step_log_probabilities_v1(
            behavior_logits.semantic_logits, behavior_logits.stop_logit
        )
        steps.append(TrajectoryPrefixStepV1(
            step_input=step_input, forced_stop=False, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=log_probabilities[-1],
        ))
    else:
        final_step_input = SpecialistStepInputV1(
            schema_version=STEP_INPUT_SCHEMA_V1, order_semantics=order_semantics,
            semantic_prefix=accumulated, allowed_semantic_classes=(), stop_available=True,
        )
        steps.append(TrajectoryPrefixStepV1(
            step_input=final_step_input, forced_stop=True, chosen_is_stop=True,
            chosen_semantic_action=None, behavior_log_probability=0.0,
        ))
    return tuple(steps)


@dataclass(frozen=True, slots=True)
class RecordedActorDecisionV1:
    """One committed decision, ready to become one ``ActorTrajectoryTransitionV1``."""

    model_input: SpecialistModelInputV1
    order_semantics: str
    prefix_steps: tuple[TrajectoryPrefixStepV1, ...]


class _RecordingSessionV1:
    """Wraps one real decision session; records every real (step_input, logits) query."""

    def __init__(self, inner: SpecialistDecisionSessionV2, owner: "_RecordingPolicyV1") -> None:
        self._inner = inner
        self._owner = owner
        self._recorded: list[
            tuple[SpecialistStepInputV1, SpecialistStepLogitsV1, SpecialistStepLogitsV1]
        ] = []

    def logits(
        self, model_input: SpecialistModelInputV1, step_input: SpecialistStepInputV1,
    ) -> SpecialistStepLogitsV1:
        result = self._inner.logits(model_input, step_input)
        behavior_logits_fn = getattr(self._inner, "behavior_logits", None)
        behavior_logits = result if behavior_logits_fn is None else behavior_logits_fn()
        if not isinstance(behavior_logits, SpecialistStepLogitsV1):
            raise ActorPoolV1Error("sampling session returned invalid behavior logits")
        self._recorded.append((step_input, result, behavior_logits))
        return result

    def commit(self, outcome: CommittedSemanticDecisionV2) -> None:
        self._inner.commit(outcome)
        model_input = self._owner.take_pending_model_input()
        prefix_steps = _reconstruct_prefix_steps_v1(
            recorded=tuple(self._recorded),
            final_semantic_selection=outcome.semantic_action.semantic_selection,
            order_semantics=outcome.semantic_action.order_semantics,
        )
        self._owner.decisions.append(RecordedActorDecisionV1(
            model_input=model_input,
            order_semantics=outcome.semantic_action.order_semantics,
            prefix_steps=prefix_steps,
        ))

    def abort(self) -> None:
        self._inner.abort()


class _RecordingPolicyV1:
    """Wraps one game's real behavior policy; accumulates committed decisions in order."""

    def __init__(self, inner: SpecialistDecisionPolicyV2) -> None:
        self._inner = inner
        self._pending_model_input: SpecialistModelInputV1 | None = None
        self.decisions: list[RecordedActorDecisionV1] = []

    def set_pending_model_input(self, model_input: SpecialistModelInputV1 | None) -> None:
        self._pending_model_input = model_input

    def take_pending_model_input(self) -> SpecialistModelInputV1:
        model_input = self._pending_model_input
        self._pending_model_input = None
        if model_input is None:
            raise ActorPoolV1Error(
                "committed decision has no independently captured model_input "
                "(the side-channel extraction in _RecordingAgentV1 did not run or failed)"
            )
        return model_input

    def reset(self) -> None:
        self._inner.reset()

    def begin_decision(self) -> SpecialistDecisionSessionV2:
        return _RecordingSessionV1(self._inner.begin_decision(), self)

    def policy_telemetry(self) -> PolicyTelemetrySnapshot:
        return self._inner.policy_telemetry()


class _RecordingPolicyFactoryV1:
    """Produces one fresh ``_RecordingPolicyV1`` per game and remembers it for the agent wrapper."""

    def __init__(self, inner_factory: StepLogitPolicyFactory) -> None:
        self._inner_factory = inner_factory
        self.policy: _RecordingPolicyV1 | None = None
        self.last_valid_observation: Mapping[str, object] | None = None
        self.last_valid_action: tuple[int, ...] | None = None
        self.state_hash_sequence: list[str] = []
        self.action_sequence: list[tuple[int, ...]] = []
        self.current_decision_index: int | None = None
        self.current_state_hash: str | None = None
        self._decision_calls = 0
        self.runtime_fault: FaultDiagnosticsV1 | None = None

    def new_policy(self) -> _RecordingPolicyV1:
        policy = _RecordingPolicyV1(self._inner_factory.new_policy())
        self.policy = policy
        return policy

    def record_runtime_observation_v1(self, observation: Mapping[str, object]) -> None:
        """Snapshot the real input before the runtime can throw or CABT can flatten it."""
        observed = dict(observation)
        self.last_valid_observation = observed
        try:
            state_hash = hashlib.sha256(canonical_json_bytes_v2(observed)).hexdigest()
        except (TypeError, ValueError):
            state_hash = None
        self.current_state_hash = state_hash
        if state_hash is not None:
            self.state_hash_sequence.append(state_hash)
        if observed.get("select") is not None:
            self.current_decision_index = self._decision_calls
        else:
            self.current_decision_index = None

    def record_runtime_action_v1(self, action: Sequence[object]) -> None:
        recorded_action = tuple(int(value) for value in action)
        self.last_valid_action = recorded_action
        self.action_sequence.append(recorded_action)
        if self.current_decision_index is not None:
            self._decision_calls += 1

    def capture_runtime_fault_v1(
        self,
        exception: Exception,
        *,
        identity: CanonicalGameIdentityV1,
        elapsed_seconds: float,
    ) -> None:
        """Keep the first source exception; later engine text is only a lossy wrapper."""
        if self.runtime_fault is None:
            self.runtime_fault = capture_fault_v1(
                exception, game_identity=identity, elapsed_seconds=elapsed_seconds,
                decision_index=self.current_decision_index, state_hash=self.current_state_hash,
                last_valid_observation=self.last_valid_observation,
                last_valid_action=self.last_valid_action,
                state_hash_sequence=self.state_hash_sequence,
                action_sequence=self.action_sequence,
            )


class _RecordingAgentV1:
    """Wraps the real runtime agent to independently derive ``model_input`` per decision.

    This performs the exact same public, pure extraction the runtime performs
    internally (`build_actor_visible_decision_state_v2` +
    `extract_specialist_model_input_v1`) a second time, purely as a read-only
    side channel for training-data capture. It never influences CABT index
    production, legality, or timing -- the real runtime call, delegated to
    unconditionally below, still owns all of that.
    """

    def __init__(
        self,
        runtime_agent: Callable[[object, object], list[int]],
        vocabulary: CardVocabularyV1,
        recording_factory: _RecordingPolicyFactoryV1,
        identity: CanonicalGameIdentityV1,
        started_monotonic: float,
    ) -> None:
        self._runtime_agent = runtime_agent
        self._vocabulary = vocabulary
        self._recording_factory = recording_factory
        self._identity = identity
        self._started_monotonic = started_monotonic

    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        try:
            if isinstance(observation, Mapping):
                self._recording_factory.record_runtime_observation_v1(observation)
            if isinstance(observation, Mapping) and observation.get("select") is not None:
                model_input: SpecialistModelInputV1 | None
                try:
                    state = build_actor_visible_decision_state_v2(observation)
                    model_input = extract_specialist_model_input_v1(state, self._vocabulary).model_input
                except (ActorVisibleV2Error, SpecialistFeatureError):
                    model_input = None
                policy = self._recording_factory.policy
                if policy is not None:
                    policy.set_pending_model_input(model_input)
            action = self._runtime_agent(observation, configuration)
        except Exception as exc:
            self._recording_factory.capture_runtime_fault_v1(
                exc, identity=self._identity,
                elapsed_seconds=time.monotonic() - self._started_monotonic,
            )
            raise
        if isinstance(action, Sequence) and not isinstance(action, (str, bytes)):
            try:
                self._recording_factory.record_runtime_action_v1(action)
            except Exception as exc:
                self._recording_factory.capture_runtime_fault_v1(
                    exc, identity=self._identity,
                    elapsed_seconds=time.monotonic() - self._started_monotonic,
                )
                raise
        return action


def _capture_recording_fault_v1(
    exception: BaseException,
    *,
    identity: CanonicalGameIdentityV1,
    elapsed_seconds: float,
    recording_factory: _RecordingPolicyFactoryV1,
) -> FaultDiagnosticsV1:
    """Capture source-time worker evidence without inventing unavailable trace data."""
    if recording_factory.runtime_fault is not None:
        diagnostic = recording_factory.runtime_fault
        return (
            diagnostic if diagnostic.elapsed_seconds is not None
            else replace(diagnostic, elapsed_seconds=elapsed_seconds)
        )
    return capture_fault_v1(
        exception, game_identity=identity, elapsed_seconds=elapsed_seconds,
        decision_index=recording_factory.current_decision_index,
        state_hash=recording_factory.current_state_hash,
        last_valid_observation=recording_factory.last_valid_observation,
        last_valid_action=recording_factory.last_valid_action,
        state_hash_sequence=recording_factory.state_hash_sequence,
        action_sequence=recording_factory.action_sequence,
    )


# --------------------------------------------------------------------------
# One real game.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActorGameFaultV1:
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class ActorGameCollectionResultV1:
    status: str  # "completed" | "faulted"
    job_id: str
    transitions: tuple[ActorTrajectoryTransitionV1, ...]
    fault: ActorGameFaultV1 | None
    winner: int | None
    outcome: str | None
    steps: int | None
    elapsed_seconds: float
    engine_entry_point: str
    engine_source_sha256: str
    opponent_version: str
    deck_identity: str | None
    # どの相手インスタンスが実際に打ったか。record の provenance に使う。
    # 早期 return する faulted 経路では相手が解決済みとは限らないので既定は空。
    opponent_instance_id: str = ""
    game_identity: CanonicalGameIdentityV1 | None = None
    diagnostic: FaultDiagnosticsV1 | None = None


def worker_cuda_diagnostics_v1() -> dict[str, object]:
    """Report -- never mutate -- this process's CUDA exposure for the record."""
    diagnostics: dict[str, object] = {
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "torch_imported_by_diagnostics": False,
        "torch_cuda_available": None,
    }
    if "torch" in sys.modules:
        torch = sys.modules["torch"]
        diagnostics["torch_cuda_available"] = bool(torch.cuda.is_available())
    return diagnostics


def guard_worker_against_cuda_v1() -> None:
    """Make CUDA unreachable from this process before any heavier import runs.

    Setting ``CUDA_VISIBLE_DEVICES=""`` is read by the CUDA driver itself at
    context-init time, so it holds even if ``torch`` (or anything else) is
    imported later in this same process -- unlike a one-shot check, it is not
    bypassed by import order.
    """
    os.environ["CUDA_VISIBLE_DEVICES"] = ""


def seed_worker_rngs_v1(identity: CanonicalGameIdentityV1) -> None:
    """Seed all game-local RNG sources inside a fresh worker process only."""
    # Retry is diagnostic metadata, never a source of stochastic variation.
    # A retry must replay precisely the same controlled RNG stream.
    seed = int(identity.game_key[:16], 16) & ((1 << 63) - 1)
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed % (2 ** 32))
    except ImportError:  # pragma: no cover - numpy is optional for minimal runtime installs
        pass
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:  # pragma: no cover - torch-free collector installation
        pass


def _opponent_instance_id_v1(opponent: "OpponentInstanceV1", env_seed: int) -> str:
    """Name the opponent instance that actually played this game.

    This used to be hardcoded to ``f"cabt-rule-agent-seed-{env_seed + 1}"``
    everywhere, which was correct only while the self-mirror was the sole
    opponent.  Once a rotation names registered pool members, that label records
    a game against e.g. ``kiyotah_dragapult`` as one against the rule agent --
    false provenance that silently collapses every opponent into one bucket for
    anything reading the trajectories per opponent.

    The mirror keeps a per-seed id because it genuinely is a distinct instance
    per seed: the built-in rule agent is re-seeded for each game.
    """
    if opponent.is_mirror:
        return f"cabt-rule-agent-seed-{env_seed + 1}"
    return opponent.opponent_id


def _resolve_canonical_game_identity_v1(job: ActorJobConfigV1) -> CanonicalGameIdentityV1:
    """Resolve the complete immutable game recipe before a worker is spawned."""
    pool = load_opponent_pool_v1(default_pool_root_v1(_REPO_ROOT))
    opponent = resolve_opponent_v1(pool, job.opponent_kind, subject_deck_csv_path=job.deck_csv_path)
    opponent_version = opponent_version_v1(opponent, mirror_version=_rule_agent_opponent_version_v1())
    deck_path = job.opponent_deck_csv_path or opponent.deck_csv_path
    return CanonicalGameIdentityV1(
        opponent_id=opponent.opponent_id, opponent_policy_version=opponent_version,
        opponent_deck_fingerprint=hashlib.sha256(Path(deck_path).read_bytes()).hexdigest(),
        seat=job.seat, environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
        retry_index=job.retry_index,
    )


def _job_with_resolved_identity_v1(job: ActorJobConfigV1) -> ActorJobConfigV1:
    identity = _resolve_canonical_game_identity_v1(job)
    return replace(job, canonical_game_identity=identity.to_dict())


def _rule_agent_opponent_version_v1() -> str:
    """Pin the CABT heuristic "rule" agent's entry-point identity.

    Mirrors ``cabt_legality_v1``'s discipline of hashing one exact entry-point
    file rather than a whole transitive closure: ``main.py`` at the repo root
    is where ``run_match``/``scripts.test_sim._make_agent`` resolve the
    ``"rule"`` agent name (``make_rule_agent``) from.
    """
    main_path = _REPO_ROOT / "main.py"
    if not main_path.is_file():
        raise ActorPoolV1Error(f"could not locate main.py for opponent identity at {main_path}")
    return hashlib.sha256(main_path.read_bytes()).hexdigest()


def run_one_actor_game_v1(
    *,
    job: ActorJobConfigV1,
    output_dir: str | Path,
    run_match: Callable[..., dict[str, Any]] | None = None,
    engine_identity: tuple[str, str] | None = None,
    deck_binding: tuple[QualifiedDeckAsset, DeckLockDecision, CardVocabularyV1] | None = None,
) -> ActorGameCollectionResultV1:
    """Run exactly one real CABT game and return a typed collection result.

    Setup failures (unknown archetype, missing deck file, CABT legality
    probe failure) raise ``ActorPoolV1Error``/``DeckQualificationError``
    directly -- they are configuration bugs, not per-game randomness.  Game
    *execution* faults (engine error, non-DONE terminal status, agent fault,
    an internal reconstruction inconsistency) are caught and returned as a
    ``status="faulted"`` result with ``transitions=()`` -- never converted
    into a usable trajectory.

    ``deck_binding`` lets a test inject an already-qualified deck (built with
    a fixture ``cabt_legality`` callback, exactly like
    ``tests/meta_specialist/test_runtime.py``'s own helpers) instead of
    paying a real CABT legality probe on every call.  Worker processes never
    pass it: see the module docstring on why a qualified asset cannot be
    reused across a ``spawn`` boundary.
    """
    if type(job) is not ActorJobConfigV1:
        raise ActorPoolV1Error("job must be an ActorJobConfigV1")
    deck_csv_path = Path(job.deck_csv_path)
    if deck_binding is None:
        qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
            archetype_id=job.archetype_id, deck_csv_path=deck_csv_path, source_commit=job.source_commit,
        )
    else:
        qualified, deck_lock, vocabulary = deck_binding
    if run_match is None:
        engine_run_match, engine_entry_point, engine_source_sha256 = engine_identity_v1()
    else:
        if engine_identity is None:
            raise ActorPoolV1Error("an injected run_match must be accompanied by an explicit engine_identity")
        engine_run_match = run_match
        engine_entry_point, engine_source_sha256 = engine_identity

    if job.behavior_kind == "rule_agent":
        inner_factory, live_identity = _build_rule_agent_policy_factory_v1()
        if live_identity != job.behavior_identity:
            raise ActorPoolV1Error(
                "job.behavior_identity does not match the live rule policy template's "
                f"content hash ({live_identity})"
            )
    elif job.behavior_kind == _NEURAL_BEHAVIOR_KIND_V1:
        inner_factory, live_identity = _build_neural_agent_policy_factory_v1(job, deck_lock=deck_lock)
        if live_identity != job.behavior_identity:
            raise ActorPoolV1Error(
                "job.behavior_identity does not match the live neural checkpoint's "
                f"content hash ({live_identity})"
            )
    elif job.behavior_kind == _NEURAL_BEHAVIOR_KIND_V4:
        inner_factory, live_identity = _build_neural_agent_policy_factory_v4(
            job, checkpoint_lineage_id=deck_lock.policy_lineage_id,
        )
        if live_identity != job.behavior_identity:
            raise ActorPoolV1Error(
                "job.behavior_identity does not match the live V4 neural checkpoint's "
                f"content hash ({live_identity})"
            )
    else:  # pragma: no cover - guarded by ActorJobConfigV1.__post_init__'s closed enum
        raise ActorPoolV1Error(f"unsupported behavior_kind: {job.behavior_kind}")

    # Resolve the opponent fail-closed.  An unregistered id, a missing deck, a
    # missing policy, or a policy whose bytes disagree with the manifest all
    # raise here.  The previous implementation fell back to the subject's own
    # deck whenever it could not find the opponent's, so a job naming an
    # opponent that did not exist on disk silently ran a self-mirror while
    # still reporting that opponent's name.
    deck_path_str = str(deck_csv_path)
    pool = load_opponent_pool_v1(default_pool_root_v1(_REPO_ROOT))
    opponent = resolve_opponent_v1(
        pool, job.opponent_kind, subject_deck_csv_path=deck_path_str
    )
    opponent_version = opponent_version_v1(
        opponent, mirror_version=_rule_agent_opponent_version_v1()
    )
    # An explicit per-job override still has to agree with the registry: it may
    # only re-point the deck within the resolved instance, never introduce an
    # unregistered opponent.
    opp_deck_str = job.opponent_deck_csv_path or opponent.deck_csv_path
    resolved_identity = CanonicalGameIdentityV1(
        opponent_id=opponent.opponent_id, opponent_policy_version=opponent_version,
        opponent_deck_fingerprint=hashlib.sha256(Path(opp_deck_str).read_bytes()).hexdigest(),
        seat=job.seat, environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
        retry_index=job.retry_index,
    )
    identity = (
        CanonicalGameIdentityV1.from_dict(job.canonical_game_identity)
        if job.canonical_game_identity is not None else resolved_identity
    )
    if identity != resolved_identity:
        raise ActorPoolV1Error("job canonical_game_identity does not match resolved opponent recipe")
    # This is the lowest shared construction boundary: every policy factory,
    # deck shuffle and engine call below sees only this attempt's RNG state.
    seed_worker_rngs_v1(identity)
    opponent_factory = (
        None if opponent.is_mirror else build_opponent_agent_factory_v1(opponent)
    )

    recording_factory = _RecordingPolicyFactoryV1(inner_factory)
    constraints = RuntimeConstraintManifest.frozen_v1()
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    started_monotonic = time.monotonic()

    def candidate_factory(_deck: list[int], _seed: int) -> Callable[[object, object], list[int]]:
        binding = make_agent(
            deck_asset=qualified, deck_lock=deck_lock, vocabulary=vocabulary,
            policy_factory=recording_factory, expected_policy_identity=job.behavior_identity,
            constraints=constraints,
        )
        return _RecordingAgentV1(
            binding.agent, vocabulary, recording_factory, identity, started_monotonic,
        )

    subject_first = job.seat == 0
    deck_a_path = deck_path_str if subject_first else opp_deck_str
    deck_b_path = opp_deck_str if subject_first else deck_path_str
    # The engine builds its built-in "rule" agent only for the mirror instance.
    # Every registered external opponent supplies its own factory, so naming an
    # opponent now actually changes which policy plays -- not merely which deck
    # the built-in rule agent is handed.
    opponent_name = "rule" if opponent.is_mirror else opponent.opponent_id
    try:
        try:
            from scripts.test_sim import DeckValidationError, MatchDependencyError, MatchExecutionError
            err_types: tuple[type[BaseException], ...] = (DeckValidationError, MatchDependencyError, MatchExecutionError)
        except ImportError:
            # An unavailable optional runner module does not make every
            # arbitrary exception a known engine failure.  The broad handler
            # below still captures it at this source boundary.
            err_types = ()
        result = engine_run_match(
            deck_a_path=deck_a_path,
            deck_b_path=deck_b_path,
            agent_a_name="runtime_policy" if subject_first else opponent_name,
            agent_b_name=opponent_name if subject_first else "runtime_policy",
            seed=job.env_seed,
            max_steps=job.max_steps,
            output_dir=str(output_dir),
            save_html=False,
            save_result=False,
            agent_a_factory=candidate_factory if subject_first else opponent_factory,
            agent_b_factory=opponent_factory if subject_first else candidate_factory,
        )
    except err_types as exc:
        elapsed = time.monotonic() - started_monotonic
        diagnostic = _capture_recording_fault_v1(
            exc, identity=identity, elapsed_seconds=elapsed, recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(kind="engine_error", detail=f"{type(exc).__name__}: {exc}"),
            winner=None, outcome=None, steps=None, elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )
    except Exception as exc:
        elapsed = time.monotonic() - started_monotonic
        diagnostic = _capture_recording_fault_v1(
            exc, identity=identity, elapsed_seconds=elapsed, recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(kind="unexpected_engine_error", detail=f"{type(exc).__name__}: {exc}"),
            winner=None, outcome=None, steps=None, elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )
    elapsed = time.monotonic() - started_monotonic
    if not isinstance(result, Mapping):
        exception = ActorPoolV1Error("engine returned a non-mapping result")
        diagnostic = _capture_recording_fault_v1(
            exception, identity=identity, elapsed_seconds=elapsed, recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(kind="unexpected_engine_result", detail=diagnostic.message),
            winner=None, outcome=None, steps=None, elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )

    status = result.get("status")
    if status != "DONE":
        detail = (
            f"status={status} terminal_reason={result.get('terminal_reason')}"
            f" error={result.get('error')}"
        )
        diagnostic = _capture_recording_fault_v1(
            ActorPoolV1Error(detail), identity=identity, elapsed_seconds=elapsed,
            recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(
                kind="non_terminal" if status in {"STEP_LIMIT", "INCOMPLETE"} else "agent_fault",
                # `error` carries the actual exception the runtime caught (see
                # `league_runtime._run_once`, which stores `str(exc)[:200]` there).
                # This only reported `terminal_reason`, a key that path never sets,
                # so every AGENT_ERROR read as
                # "status=AGENT_ERROR terminal_reason=None" and the one piece of
                # information needed to diagnose it was dropped on the floor.
                detail=detail,
            ),
            winner=None, outcome=None, steps=result.get("steps"), elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )

    policy = recording_factory.policy
    if policy is None or not policy.decisions:
        detail = "runtime committed zero decisions"
        diagnostic = _capture_recording_fault_v1(
            ActorPoolV1Error(detail), identity=identity, elapsed_seconds=elapsed,
            recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(kind="agent_fault", detail=detail),
            winner=None, outcome=None, steps=result.get("steps"), elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )

    candidate_side = 0 if subject_first else 1
    winner = result.get("winner")
    if winner == candidate_side:
        outcome, reward = "win", 1.0
    elif winner == 2:
        outcome, reward = "draw", 0.0
    elif winner in (0, 1):
        outcome, reward = "loss", -1.0
    else:
        detail = f"invalid winner: {winner!r}"
        diagnostic = _capture_recording_fault_v1(
            ActorPoolV1Error(detail), identity=identity, elapsed_seconds=elapsed,
            recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(kind="engine_error", detail=detail),
            winner=None, outcome=None, steps=result.get("steps"), elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )

    subject_behavior_version = job.behavior_identity
    opponent_instance_id = _opponent_instance_id_v1(opponent, job.env_seed)
    try:
        transitions: list[ActorTrajectoryTransitionV1] = []
        last_index = len(policy.decisions) - 1
        for index, decision in enumerate(policy.decisions):
            terminal = index == last_index
            transitions.append(build_actor_trajectory_transition_v1(
                model_input=decision.model_input,
                order_semantics=decision.order_semantics,
                prefix_steps=decision.prefix_steps,
                value=_NO_CRITIC_VALUE_PLACEHOLDER_V1,
                reward=reward if terminal else 0.0,
                discount=0.0 if terminal else job.non_terminal_discount,
                terminal=terminal,
                subject_behavior_version=subject_behavior_version,
                opponent_instance_id=opponent_instance_id,
                opponent_version=opponent_version,
                pool_epoch=job.pool_epoch,
                policy_lag=job.policy_lag,
            ))
    except Exception as exc:
        diagnostic = _capture_recording_fault_v1(
            exc, identity=identity, elapsed_seconds=elapsed, recording_factory=recording_factory,
        )
        return ActorGameCollectionResultV1(
            status="faulted", job_id=job.job_id, transitions=(),
            fault=ActorGameFaultV1(kind="reconstruction_error", detail=str(exc)),
            winner=None, outcome=None, steps=result.get("steps"), elapsed_seconds=elapsed,
            engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
            opponent_version=opponent_version, deck_identity=qualified.deck_identity, game_identity=identity,
            diagnostic=diagnostic,
        )

    return ActorGameCollectionResultV1(
        status="completed", job_id=job.job_id, transitions=tuple(transitions), fault=None,
        winner=winner, outcome=outcome, steps=result.get("steps"), elapsed_seconds=elapsed,
        engine_entry_point=engine_entry_point, engine_source_sha256=engine_source_sha256,
        opponent_version=opponent_version, deck_identity=qualified.deck_identity,
        opponent_instance_id=opponent_instance_id, game_identity=identity,
    )


# --------------------------------------------------------------------------
# Content-addressed, atomic, resumable per-game writer.
# --------------------------------------------------------------------------

_GAME_RECORD_LEGACY_KEYS_V1 = frozenset({
    "schema_version", "content_hash", "job_id", "status", "archetype_id", "deck_identity",
    "seed", "seat", "winner", "outcome", "steps", "elapsed_seconds",
    "engine_entry_point", "engine_source_sha256",
    "subject_behavior_kind", "subject_behavior_version", "decoding_mode", "sampling_seed",
    "opponent_kind", "opponent_version", "opponent_instance_id",
    "pool_epoch", "policy_lag", "non_terminal_discount",
    "transitions_count", "transitions", "worker_diagnostics", "persistent_worker",
    "started_at_utc", "finished_at_utc",
})

# Replay/identity fields were added after records using the same public v1
# schema identifier had already been published.  Keep that identifier and
# accept exactly the former closed field set on read: any other partial or
# hybrid shape remains invalid rather than being silently "upgraded".
_GAME_RECORD_KEYS_V1 = frozenset({
    "schema_version", "content_hash", "replay_hash", "job_id", "status", "archetype_id", "deck_identity",
    "seed", "seat", "winner", "outcome", "steps", "elapsed_seconds",
    "engine_entry_point", "engine_source_sha256",
    "subject_behavior_kind", "subject_behavior_version", "decoding_mode", "sampling_seed",
    "opponent_kind", "opponent_version", "opponent_instance_id",
    "game_identity",
    "state_hash_sequence", "action_sequence",
    "pool_epoch", "policy_lag", "non_terminal_discount",
    "transitions_count", "transitions", "worker_diagnostics", "persistent_worker",
    "started_at_utc", "finished_at_utc",
})


def _game_record_content_hash_v1(payload: Mapping[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(
        b"mage_ptcg:actor-pool-game-record:v1\0"
        + canonical_json_bytes_v2(
            body, max_nodes=MAX_GAME_RECORD_JSON_NODES_V1, max_bytes=MAX_GAME_RECORD_JSON_BYTES_V1,
        )
    ).hexdigest()


def _game_record_replay_hash_v1(payload: Mapping[str, object]) -> str:
    """Hash replay-relevant game evidence, excluding wall-clock/process metadata."""
    excluded = frozenset({
        "content_hash", "replay_hash", "elapsed_seconds", "worker_diagnostics",
        "persistent_worker", "started_at_utc", "finished_at_utc",
    })
    body = {key: value for key, value in payload.items() if key not in excluded}
    return hashlib.sha256(
        b"mage_ptcg:actor-pool-replay-record:v1\0"
        + canonical_json_bytes_v2(
            body, max_nodes=MAX_GAME_RECORD_JSON_NODES_V1, max_bytes=MAX_GAME_RECORD_JSON_BYTES_V1,
        )
    ).hexdigest()


def _transition_replay_evidence_v1(transitions: Sequence[Mapping[str, object]]) -> tuple[list[str], list[object]]:
    """Expose the replay trace needed to locate the first engine divergence."""
    state_hashes = [
        hashlib.sha256(canonical_json_bytes_v2(transition["model_input"])).hexdigest()
        for transition in transitions
    ]
    actions = [transition["chosen_semantic_complete_action"] for transition in transitions]
    return state_hashes, actions


def build_actor_pool_game_record_v1(
    *,
    job: ActorJobConfigV1,
    result: ActorGameCollectionResultV1,
    worker_diagnostics: Mapping[str, object],
    persistent_worker: bool,
    started_at_utc: str,
    finished_at_utc: str,
) -> dict[str, object]:
    if result.status != "completed":
        raise ActorPoolV1Error("only a completed result may become a written game record")
    # Test-only worker seams construct synthetic completed results without
    # running the engine resolver. Preserve their record compatibility while
    # production results always carry the resolved identity from above.
    game_identity = result.game_identity or CanonicalGameIdentityV1(
        opponent_id=job.opponent_kind, opponent_policy_version=result.opponent_version,
        opponent_deck_fingerprint=result.deck_identity or "unknown-fixture-deck",
        seat=job.seat, environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
        retry_index=job.retry_index,
    )
    transition_payloads = [transition.to_dict() for transition in result.transitions]
    state_hashes, actions = _transition_replay_evidence_v1(transition_payloads)
    payload: dict[str, object] = {
        "schema_version": GAME_RECORD_SCHEMA_V1,
        "content_hash": "",
        "replay_hash": "",
        "job_id": job.job_id,
        "status": "completed",
        "archetype_id": job.archetype_id,
        "deck_identity": result.deck_identity,
        "seed": job.env_seed,
        "seat": job.seat,
        "winner": result.winner,
        "outcome": result.outcome,
        "steps": result.steps,
        "elapsed_seconds": round(result.elapsed_seconds, 6),
        "engine_entry_point": result.engine_entry_point,
        "engine_source_sha256": result.engine_source_sha256,
        "subject_behavior_kind": job.behavior_kind,
        "subject_behavior_version": job.behavior_identity,
        "decoding_mode": job.decoding_mode,
        "sampling_seed": job.sampling_seed,
        "opponent_kind": job.opponent_kind,
        "opponent_version": result.opponent_version,
        # 実際に打った相手。ハードコードしていた頃は、登録済み相手との対局まで
        # rule agent として記録されていた (_opponent_instance_id_v1 参照)。
        "opponent_instance_id": (
            result.opponent_instance_id
            or f"cabt-rule-agent-seed-{job.env_seed + 1}"
        ),
        "game_identity": game_identity.to_dict(),
        "pool_epoch": job.pool_epoch,
        "policy_lag": job.policy_lag,
        "non_terminal_discount": job.non_terminal_discount,
        "transitions_count": len(result.transitions),
        "transitions": transition_payloads,
        "state_hash_sequence": state_hashes,
        "action_sequence": actions,
        "worker_diagnostics": dict(worker_diagnostics),
        "persistent_worker": bool(persistent_worker),
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
    }
    payload["replay_hash"] = _game_record_replay_hash_v1(payload)
    payload["content_hash"] = _game_record_content_hash_v1(payload)
    return payload


def _atomic_write_bytes_v1(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def write_actor_pool_game_record_v1(games_dir: str | Path, payload: Mapping[str, object]) -> Path:
    """Publish one completed game's record atomically and content-addressed.

    ``games_dir/record.json`` existing (with ``status == "completed"``) is
    the sole resume marker: the atomic tempfile+``os.replace`` pattern below
    means a crash mid-write can never leave a partially written
    ``record.json`` behind for resume to misread as complete.
    """
    # Historical v1 records are accepted exclusively by the reader so an
    # existing completion marker can resume.  New output must always carry the
    # complete replay/identity evidence in the current closed schema.
    payload = _validate_game_record_shape_v1(dict(payload), allow_legacy=False)
    body = canonical_json_bytes_v2(
        payload, max_nodes=MAX_GAME_RECORD_JSON_NODES_V1, max_bytes=MAX_GAME_RECORD_JSON_BYTES_V1,
    )
    destination = Path(games_dir) / "record.json"
    _atomic_write_bytes_v1(destination, body)
    return destination


def _validate_game_record_shape_v1(
    payload: dict[str, object], *, allow_legacy: bool,
) -> dict[str, object]:
    is_legacy_v1 = set(payload) == _GAME_RECORD_LEGACY_KEYS_V1
    if is_legacy_v1 and not allow_legacy:
        raise ActorPoolV1Error("new game-record writes require the current strict schema")
    if not is_legacy_v1 and set(payload) != _GAME_RECORD_KEYS_V1:
        raise ActorPoolV1Error("game record has the wrong closed field set")
    if payload["schema_version"] != GAME_RECORD_SCHEMA_V1:
        raise ActorPoolV1Error("game record schema_version is invalid")
    if payload["status"] != "completed":
        raise ActorPoolV1Error("only a completed game record may be published")
    transitions = payload["transitions"]
    if type(transitions) is not list or len(transitions) != payload["transitions_count"]:
        raise ActorPoolV1Error("game record transitions_count does not match transitions")
    if not transitions:
        raise ActorPoolV1Error("a completed game record must contain at least one transition")
    if is_legacy_v1:
        expected_hash = _game_record_content_hash_v1(payload)
        if payload["content_hash"] and payload["content_hash"] != expected_hash:
            raise ActorPoolV1Error("legacy game record content_hash does not verify")
        payload["content_hash"] = expected_hash
        return payload
    expected_replay_hash = _game_record_replay_hash_v1(payload)
    # A caller that deliberately clears content_hash asks the writer to rebuild
    # the content-addressed envelope after editing a fixture/payload; in that
    # case its derived replay hash is stale by definition and is rebuilt too.
    if payload["content_hash"] and payload["replay_hash"] and payload["replay_hash"] != expected_replay_hash:
        raise ActorPoolV1Error("game record replay_hash does not verify")
    payload["replay_hash"] = expected_replay_hash
    expected_hash = _game_record_content_hash_v1(payload)
    if payload["content_hash"] and payload["content_hash"] != expected_hash:
        raise ActorPoolV1Error("game record content_hash does not verify")
    payload["content_hash"] = expected_hash
    return payload


def read_actor_pool_game_record_v1(path: str | Path) -> dict[str, object]:
    """Read one published record and fully re-validate every transition.

    Every stored transition is re-checked through
    ``trajectory_v1.validate_actor_trajectory_transition_payload_v1`` -- the
    module's own read-side authority -- not just this module's shape check.
    """
    body = Path(path).read_bytes()
    try:
        payload = parse_canonical_json_bytes_v2(
            body, max_nodes=MAX_GAME_RECORD_JSON_NODES_V1, max_bytes=MAX_GAME_RECORD_JSON_BYTES_V1,
        )
    except LocalDatasetV2Error as exc:
        raise ActorPoolV1Error(f"game record bytes are malformed: {exc}") from exc
    if type(payload) is not dict:
        raise ActorPoolV1Error("game record root must be a JSON object")
    legacy_v1 = set(payload) == _GAME_RECORD_LEGACY_KEYS_V1
    payload = _validate_game_record_shape_v1(payload, allow_legacy=True)
    if canonical_json_bytes_v2(
        payload, max_nodes=MAX_GAME_RECORD_JSON_NODES_V1, max_bytes=MAX_GAME_RECORD_JSON_BYTES_V1,
    ) != body:
        raise ActorPoolV1Error("game record bytes are not canonical")
    for index, transition in enumerate(payload["transitions"]):
        try:
            validate_actor_trajectory_transition_payload_v1(transition)
        except TrajectoryV1Error as exc:
            raise ActorPoolV1Error(
                f"game record transitions[{index}] failed trajectory_v1 validation: {exc}"
            ) from exc
    if legacy_v1:
        # This is read-side metadata, intentionally not persisted back into
        # the signed/canonical legacy bytes.  It makes absence explicit to
        # callers while allowing the historical completion marker to resume.
        return {**payload, "replay_evidence_status": "unavailable_legacy_v1"}
    return payload


def is_actor_pool_job_complete_v1(games_dir: str | Path) -> bool:
    """The sole resume predicate: a completed game is never recollected."""
    record_path = Path(games_dir) / "record.json"
    if not record_path.is_file():
        return False
    try:
        read_actor_pool_game_record_v1(record_path)
    except ActorPoolV1Error:
        return False
    return True


# --------------------------------------------------------------------------
# Worker process entry points (spawn-compatible: module-level, args are
# picklable primitives only).
# --------------------------------------------------------------------------


def _bounded_excerpt_v1(path: Path, *, limit: int = _CAPTURE_EXCERPT_BYTES_V1) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace")


def _bounded_excerpt_since_v1(path: Path, offset: int, *, limit: int = _CAPTURE_EXCERPT_BYTES_V1) -> str:
    """Return this attempt's appended log bytes, not another job's worker log."""
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, offset))
            data = handle.read()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace")


def _log_offset_v1(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _install_worker_isolation_v1(stdout_path: Path, stderr_path: Path) -> None:
    """Make this process a fresh session/process-group leader with bounded, captured output.

    Runs first, before any other worker code: ``os.setsid()`` makes this
    process's PID also its own new process group ID, so the parent can kill
    the whole group (this process plus anything it spawns) with a single
    ``os.killpg`` on timeout instead of leaking a hung descendant.

    Idempotent: ``os.setsid()`` raises ``PermissionError`` (EPERM) if this
    process is already its own process group leader -- a fine state to
    already be in (``os.killpg`` still works), not a fault.
    """
    try:
        os.setsid()
    except PermissionError:
        pass
    try:
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (_WORKER_RLIMIT_FSIZE_BYTES_V1, _WORKER_RLIMIT_FSIZE_BYTES_V1)
        )
    except (ValueError, OSError):  # pragma: no cover - platform without RLIMIT_FSIZE support
        pass
    guard_worker_against_cuda_v1()
    out_fd = os.open(str(stdout_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    err_fd = os.open(str(stderr_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(out_fd, 1)
    os.dup2(err_fd, 2)
    os.close(out_fd)
    os.close(err_fd)
    sys.stdout = os.fdopen(1, "w", closefd=False)
    sys.stderr = os.fdopen(2, "w", closefd=False)


def _run_and_write_job_v1(job: ActorJobConfigV1, *, output_root: Path, persistent_worker: bool) -> None:
    """Run one job and, on success only, publish its record.json. Never raises for expected faults."""
    from datetime import UTC, datetime

    games_dir = output_root / "games" / job.job_id
    started_at = datetime.now(UTC).isoformat()
    scratch_dir = games_dir / "scratch"
    started_monotonic = time.monotonic()
    try:
        result = run_one_actor_game_v1(job=job, output_dir=scratch_dir)
    except Exception as exc:
        # This is still inside the child, before the pool boundary can reduce
        # an arbitrary exception to a process exit code.  Persist its class,
        # message, traceback and the resolved game identity for the retry.
        identity = (
            CanonicalGameIdentityV1.from_dict(job.canonical_game_identity)
            if job.canonical_game_identity is not None else CanonicalGameIdentityV1(
                opponent_id=job.opponent_kind, opponent_policy_version="unresolved",
                opponent_deck_fingerprint="unresolved", seat=job.seat,
                environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
                retry_index=job.retry_index,
            )
        )
        finished_at = datetime.now(UTC).isoformat()
        diagnostic = capture_fault_v1(
            exc, game_identity=identity, elapsed_seconds=time.monotonic() - started_monotonic,
        )
        fault_payload = {
            "job_id": job.job_id,
            "status": "faulted",
            "fault_kind": "unexpected_worker_exception",
            "fault_detail": f"{type(exc).__name__}: {exc}",
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "diagnostic": diagnostic.to_dict(),
        }
        games_dir.mkdir(parents=True, exist_ok=True)
        (games_dir / f"fault-{job.retry_index}-{time.time_ns()}.json").write_text(
            canonical_json_bytes_v2(fault_payload).decode("utf-8"), encoding="utf-8",
        )
        raise ActorPoolV1Error(
            f"actor game faulted: unexpected_worker_exception: {type(exc).__name__}: {exc}"
        ) from exc
    finished_at = datetime.now(UTC).isoformat()
    if result.status != "completed":
        identity = result.game_identity or CanonicalGameIdentityV1(
            opponent_id=job.opponent_kind, opponent_policy_version=result.opponent_version or "unknown",
            opponent_deck_fingerprint=result.deck_identity or "unknown", seat=job.seat,
            environment_seed=job.env_seed, agent_sampling_seed=job.sampling_seed,
            retry_index=job.retry_index,
        )
        diagnostic = result.diagnostic or capture_fault_v1(
            ActorPoolV1Error(result.fault.detail if result.fault else "unknown actor game fault"),
            game_identity=identity, elapsed_seconds=result.elapsed_seconds,
        )
        fault_payload = {
            "job_id": job.job_id,
            "status": "faulted",
            "fault_kind": result.fault.kind if result.fault else "unknown",
            "fault_detail": result.fault.detail if result.fault else "",
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "diagnostic": diagnostic.to_dict(),
        }
        fault_path = games_dir / f"fault-{job.retry_index}-{time.time_ns()}.json"
        games_dir.mkdir(parents=True, exist_ok=True)
        fault_path.write_text(
            canonical_json_bytes_v2(fault_payload).decode("utf-8"), encoding="utf-8",
        )
        raise ActorPoolV1Error(f"actor game faulted: {fault_payload['fault_kind']}: {fault_payload['fault_detail']}")
    payload = build_actor_pool_game_record_v1(
        job=job, result=result, worker_diagnostics=worker_cuda_diagnostics_v1(),
        persistent_worker=persistent_worker, started_at_utc=started_at, finished_at_utc=finished_at,
    )
    write_actor_pool_game_record_v1(games_dir, payload)


def _actor_pool_worker_main_v1(
    job_payload: dict[str, object], stdout_path: str, stderr_path: str, output_root: str,
) -> None:
    """``spawn`` target: one process, one game, then exit."""
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    job = ActorJobConfigV1.from_payload(job_payload)
    _run_and_write_job_v1(job, output_root=Path(output_root), persistent_worker=False)


def _persistent_actor_pool_worker_main_v1(
    job_queue: "mp.Queue[dict[str, object] | None]",
    result_queue: "mp.Queue[dict[str, object]]",
    stdout_path: str,
    stderr_path: str,
    output_root: str,
) -> None:
    """``spawn`` target for the (default-disabled) persistent-worker fast path.

    Reuses one process across multiple jobs read from ``job_queue`` until the
    ``None`` sentinel. This trades away the per-job process-group hard-kill
    guarantee the default path gives -- a hung job here can only be bounded
    by the runtime's own internal cooperative deadline
    (``RuntimeConstraintManifest.frozen_v1().decision_hard_timeout_ms`` /
    ``game_hard_timeout_ms``), not an external OS-level kill. That is exactly
    why this path is never the default.
    """
    _install_worker_isolation_v1(Path(stdout_path), Path(stderr_path))
    output_root_path = Path(output_root)
    stdout_file = Path(stdout_path)
    stderr_file = Path(stderr_path)
    while True:
        payload = job_queue.get()
        if payload is None:
            return
        job = ActorJobConfigV1.from_payload(payload)
        identity = CanonicalGameIdentityV1.from_dict(job.canonical_game_identity or {})
        games_dir = output_root_path / "games" / job.job_id
        started = time.monotonic()
        stdout_offset = _log_offset_v1(stdout_file)
        stderr_offset = _log_offset_v1(stderr_file)

        def _message(
            *, status: str, detail: str, diagnostic: FaultDiagnosticsV1 | None,
        ) -> dict[str, object]:
            # The OS exit code is unknowable until the orderly sentinel join.
            # Send ``None`` rather than inventing a per-job code; the parent
            # binds this attempt to the actual persistent child exit code.
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except OSError:
                pass
            return {
                "job_id": job.job_id,
                "status": status,
                "detail": detail,
                "game_identity": identity.to_dict(),
                "retry_index": job.retry_index,
                "diagnostic": diagnostic.to_dict() if diagnostic is not None else None,
                "stdout_excerpt": _bounded_excerpt_since_v1(stdout_file, stdout_offset),
                "stderr_excerpt": _bounded_excerpt_since_v1(stderr_file, stderr_offset),
                "worker_exit_code": None,
                "process_id": os.getpid(),
                "attempt_metadata": {
                    "retry_index": job.retry_index,
                    "persistent_worker": True,
                },
                "wall_time_seconds": time.monotonic() - started,
            }
        try:
            _run_and_write_job_v1(job, output_root=output_root_path, persistent_worker=True)
        except ActorPoolV1Error as exc:
            child_diagnostics = ActorPoolV1._read_child_diagnostics_v1(games_dir)
            diagnostic = child_diagnostics[-1] if child_diagnostics else None
            result_queue.put(_message(status="faulted", detail=str(exc), diagnostic=diagnostic))
        except Exception as exc:  # noqa: BLE001 - keep one bad job from killing the persistent process
            diagnostic = capture_fault_v1(
                exc, game_identity=identity, elapsed_seconds=time.monotonic() - started,
            )
            fault_payload = {
                "job_id": job.job_id, "status": "faulted",
                "fault_kind": "unexpected_persistent_worker_exception",
                "fault_detail": f"{type(exc).__name__}: {exc}",
                "diagnostic": diagnostic.to_dict(),
            }
            games_dir.mkdir(parents=True, exist_ok=True)
            (games_dir / f"fault-{job.retry_index}-{time.time_ns()}.json").write_text(
                canonical_json_bytes_v2(fault_payload).decode("utf-8"), encoding="utf-8",
            )
            result_queue.put(_message(
                status="faulted", detail=f"{type(exc).__name__}: {exc}", diagnostic=diagnostic,
            ))
        else:
            result_queue.put(_message(status="completed", detail="", diagnostic=None))


# --------------------------------------------------------------------------
# Pool orchestration.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActorPoolJobOutcomeV1:
    job_id: str
    status: str  # "completed" | "resumed" | "faulted" | "timeout"
    record_path: str | None
    transitions_count: int
    fault_reason: str | None
    wall_time_seconds: float
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    worker_exit_code: int | None = None
    diagnostic: FaultDiagnosticsV1 | None = None
    game_identity: Mapping[str, object] | None = None
    retry_index: int = 0
    process_id: int | None = None
    attempt_metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _require_nonneg_int(self.retry_index, "retry_index")
        if self.process_id is not None:
            _require_positive_int(self.process_id, "process_id")
        if self.game_identity is not None:
            identity = CanonicalGameIdentityV1.from_dict(self.game_identity)
            if identity.retry_index != self.retry_index:
                raise ActorPoolV1Error("outcome retry_index does not match game_identity")


@dataclass
class _ActiveJobV1:
    job: ActorJobConfigV1
    process: "mp.process.BaseProcess"
    deadline: float
    started: float
    games_dir: Path
    stdout_path: Path
    stderr_path: Path


class ActorPoolV1:
    """Manages actor worker processes using the ``spawn`` context.

    Default: one freshly spawned process per game (``persistent_worker=False``).
    ``num_workers`` bounds how many of those processes run concurrently.
    """

    def __init__(
        self,
        num_workers: int = 2,
        *,
        persistent_worker: bool = False,
        _worker_target: Callable[..., None] = _actor_pool_worker_main_v1,
    ) -> None:
        if type(num_workers) is not int or num_workers <= 0:
            raise ActorPoolV1Error("num_workers must be a positive int")
        if type(persistent_worker) is not bool:
            raise ActorPoolV1Error("persistent_worker must be a bool")
        self._num_workers = num_workers
        self._persistent_worker = persistent_worker
        self._ctx = mp.get_context("spawn")
        # Internal-only seam: production code always uses the default target.
        # Tests substitute a trivial module-level function to exercise the
        # real spawn/timeout/process-group-kill machinery without running a
        # full CABT game.
        self._worker_target = _worker_target

    @property
    def num_workers(self) -> int:
        return self._num_workers

    @property
    def persistent_worker(self) -> bool:
        return self._persistent_worker

    @property
    def start_method(self) -> str:
        return self._ctx.get_start_method()

    def shutdown(self) -> None:
        """No persistent state to release by default; kept for API/lifecycle symmetry."""
        return None

    def run_jobs(
        self, jobs: Sequence[ActorJobConfigV1], *, output_root: str | Path,
    ) -> tuple[ActorPoolJobOutcomeV1, ...]:
        if not isinstance(jobs, Sequence) or any(type(job) is not ActorJobConfigV1 for job in jobs):
            raise ActorPoolV1Error("jobs must be a sequence of ActorJobConfigV1")
        output_root = Path(output_root)
        jobs = tuple(jobs)
        pending, outcomes = [], {}
        for job in jobs:
            games_dir = output_root / "games" / job.job_id
            if is_actor_pool_job_complete_v1(games_dir):
                record = read_actor_pool_game_record_v1(games_dir / "record.json")
                record_identity = record.get("game_identity")
                identity_payload = dict(record_identity) if isinstance(record_identity, Mapping) else None
                outcomes[job.job_id] = ActorPoolJobOutcomeV1(
                    job_id=job.job_id, status="resumed",
                    record_path=str(games_dir / "record.json"),
                    transitions_count=record["transitions_count"], fault_reason=None,
                    wall_time_seconds=0.0,
                    game_identity=identity_payload,
                    retry_index=(
                        identity_payload["retry_index"] if identity_payload is not None else job.retry_index
                    ),
                    attempt_metadata={
                        "retry_index": (
                            identity_payload["retry_index"] if identity_payload is not None else job.retry_index
                        ),
                        "persistent_worker": bool(record.get("persistent_worker", False)),
                        "resumed": True,
                    },
                )
            else:
                # Identity resolution belongs in the parent, before `spawn`,
                # so retry artifacts never invent policy/deck placeholders.
                # A published record is intentionally checked first: old valid
                # v1 records must resume even if today's live registry can no
                # longer resolve their historical opponent recipe.
                pending.append(_job_with_resolved_identity_v1(job))
        if pending:
            if self._persistent_worker:
                outcomes.update(self._run_persistent(pending, output_root=output_root))
            else:
                outcomes.update(self._run_spawn_per_job(pending, output_root=output_root))
        return tuple(outcomes[job.job_id] for job in jobs)

    def run_job(self, job: ActorJobConfigV1, *, output_root: str | Path) -> ActorPoolJobOutcomeV1:
        return self.run_jobs((job,), output_root=output_root)[0]

    # -- default path: one spawned process per game -----------------------

    def _launch(self, job: ActorJobConfigV1, *, output_root: Path) -> _ActiveJobV1:
        games_dir = output_root / "games" / job.job_id
        scratch_dir = games_dir / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = scratch_dir / "worker.stdout.log"
        stderr_path = scratch_dir / "worker.stderr.log"
        process = self._ctx.Process(
            target=self._worker_target,
            args=(job.to_payload(), str(stdout_path), str(stderr_path), str(output_root)),
            daemon=False,
        )
        process.start()
        now = time.monotonic()
        return _ActiveJobV1(
            job=job, process=process, deadline=now + job.timeout_seconds, started=now,
            games_dir=games_dir, stdout_path=stdout_path, stderr_path=stderr_path,
        )

    @staticmethod
    def _kill_process_group(process: "mp.process.BaseProcess") -> None:
        pid = process.pid
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.join(timeout=5)
        if process.is_alive():  # pragma: no cover - defensive fallback
            process.terminate()
            process.join(timeout=5)

    def _finish_entry(self, entry: _ActiveJobV1, *, timed_out: bool) -> ActorPoolJobOutcomeV1:
        wall_time = time.monotonic() - entry.started
        record_path = entry.games_dir / "record.json"
        identity_payload = (
            dict(entry.job.canonical_game_identity)
            if entry.job.canonical_game_identity is not None else None
        )
        process_id = entry.process.pid
        attempt_metadata = {
            "retry_index": entry.job.retry_index,
            "persistent_worker": False,
        }
        if not timed_out and entry.process.exitcode == 0 and is_actor_pool_job_complete_v1(entry.games_dir):
            record = read_actor_pool_game_record_v1(record_path)
            record_identity = record.get("game_identity")
            if isinstance(record_identity, Mapping):
                identity_payload = dict(record_identity)
            return ActorPoolJobOutcomeV1(
                job_id=entry.job.job_id, status="completed", record_path=str(record_path),
                transitions_count=record["transitions_count"], fault_reason=None,
                wall_time_seconds=wall_time, worker_exit_code=entry.process.exitcode,
                game_identity=identity_payload, retry_index=entry.job.retry_index,
                process_id=process_id, attempt_metadata=attempt_metadata,
            )
        stdout_excerpt = _bounded_excerpt_v1(entry.stdout_path)
        stderr_excerpt = _bounded_excerpt_v1(entry.stderr_path)
        child_diagnostics = self._read_child_diagnostics_v1(entry.games_dir)
        if timed_out:
            fault_reason = f"worker exceeded timeout_seconds={entry.job.timeout_seconds}"
            status = "timeout"
        else:
            fault_reason = f"worker exited with code {entry.process.exitcode}"
            status = "faulted"
        diagnostic = child_diagnostics[-1] if child_diagnostics else None
        if diagnostic is not None:
            diagnostic = replace(
                diagnostic,
                worker_exit_code=entry.process.exitcode,
                # The pool's process handle is the authoritative child PID.
                # Binding it here prevents a malformed child payload from
                # claiming provenance for another process.
                process_id=process_id if process_id is not None else diagnostic.process_id,
            )
        return ActorPoolJobOutcomeV1(
            job_id=entry.job.job_id, status=status, record_path=None, transitions_count=0,
            fault_reason=fault_reason, wall_time_seconds=wall_time,
            stdout_excerpt=stdout_excerpt, stderr_excerpt=stderr_excerpt,
            worker_exit_code=entry.process.exitcode,
            diagnostic=diagnostic, game_identity=identity_payload,
            retry_index=entry.job.retry_index, process_id=process_id,
            attempt_metadata=attempt_metadata,
        )

    @staticmethod
    def _read_child_diagnostics_v1(games_dir: Path) -> list[FaultDiagnosticsV1]:
        diagnostics: list[FaultDiagnosticsV1] = []
        for path in sorted(games_dir.glob("fault-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                diagnostic = payload.get("diagnostic")
                if isinstance(diagnostic, Mapping):
                    diagnostics.append(FaultDiagnosticsV1.from_dict(diagnostic))
            except (OSError, TypeError, ValueError):
                continue
        return diagnostics

    @staticmethod
    def _write_retry_attempt_v1(
        job: ActorJobConfigV1, outcome: ActorPoolJobOutcomeV1, games_dir: Path,
        first_diagnostic: FaultDiagnosticsV1 | None = None,
    ) -> FaultDiagnosticsV1 | None:
        identity = CanonicalGameIdentityV1.from_dict(job.canonical_game_identity or {})
        diagnostic = outcome.diagnostic
        if diagnostic is None and outcome.status not in {"completed", "resumed"}:
            # A hard kill or process crash can leave no structured child
            # artifact.  Only in that genuinely unavailable-evidence case do
            # we synthesize a parent-side diagnostic.
            diagnostic = capture_fault_v1(
                ActorPoolV1Error(outcome.fault_reason or "worker completed"), game_identity=identity,
                worker_exit_code=outcome.worker_exit_code, process_id=(
                    outcome.process_id if outcome.process_id is not None else os.getpid()
                ),
            )
        elif diagnostic is not None and (
            diagnostic.worker_exit_code != outcome.worker_exit_code
            or (outcome.process_id is not None and diagnostic.process_id != outcome.process_id)
        ):
            diagnostic = replace(
                diagnostic, worker_exit_code=outcome.worker_exit_code,
                process_id=(outcome.process_id if outcome.process_id is not None else diagnostic.process_id),
            )
        child_diagnostics = [item.to_dict() for item in ActorPoolV1._read_child_diagnostics_v1(games_dir)]
        attempt_metadata = dict(outcome.attempt_metadata or {
            "retry_index": job.retry_index,
            "persistent_worker": False,
        })
        payload: dict[str, object] = {
            "schema_version": "meta-specialist-actor-retry-attempt-v1",
            "attempt": job.retry_index, "status": outcome.status,
            "job_id": job.job_id, "game_identity": identity.to_dict(),
            "diagnostic": diagnostic.to_dict() if diagnostic is not None else None,
            "worker_exit_code": outcome.worker_exit_code,
            "process_id": outcome.process_id,
            "stdout_excerpt": outcome.stdout_excerpt,
            "stderr_excerpt": outcome.stderr_excerpt,
            "child_diagnostics": child_diagnostics,
            "attempt_metadata": attempt_metadata,
        }
        if first_diagnostic is not None:
            payload["retry_classification"] = (
                "transient" if outcome.status in {"completed", "resumed"}
                else classify_retry_v1(first_diagnostic, diagnostic)
            )
        games_dir.mkdir(parents=True, exist_ok=True)
        (games_dir / f"retry-attempt-{job.retry_index}.json").write_text(
            canonical_json_bytes_v2(payload).decode("utf-8"), encoding="utf-8",
        )
        return diagnostic

    def _run_spawn_per_job(
        self, jobs: list[ActorJobConfigV1], *, output_root: Path,
    ) -> dict[str, ActorPoolJobOutcomeV1]:
        pending = list(jobs)
        active: list[_ActiveJobV1] = []
        outcomes: dict[str, ActorPoolJobOutcomeV1] = {}
        first_attempt_diagnostics: dict[str, object] = {}
        while pending or active:
            while pending and len(active) < self._num_workers:
                active.append(self._launch(pending.pop(0), output_root=output_root))
            still_active: list[_ActiveJobV1] = []
            for entry in active:
                if not entry.process.is_alive():
                    entry.process.join()
                    outcome = self._finish_entry(entry, timed_out=False)
                    if outcome.status in {"faulted", "timeout"} and entry.job.retry_index == 0:
                        first_attempt_diagnostics[entry.job.job_id] = self._write_retry_attempt_v1(
                            entry.job, outcome, entry.games_dir,
                        )
                        retry_identity = CanonicalGameIdentityV1.from_dict(entry.job.canonical_game_identity or {}).with_retry_index(1)
                        pending.append(replace(entry.job, retry_index=1, canonical_game_identity=retry_identity.to_dict()))
                    else:
                        if entry.job.retry_index == 1:
                            self._write_retry_attempt_v1(
                                entry.job, outcome, entry.games_dir,
                                first_attempt_diagnostics.get(entry.job.job_id),
                            )
                        outcomes[entry.job.job_id] = outcome
                elif time.monotonic() > entry.deadline:
                    self._kill_process_group(entry.process)
                    outcome = self._finish_entry(entry, timed_out=True)
                    if entry.job.retry_index == 0:
                        first_attempt_diagnostics[entry.job.job_id] = self._write_retry_attempt_v1(
                            entry.job, outcome, entry.games_dir,
                        )
                        retry_identity = CanonicalGameIdentityV1.from_dict(entry.job.canonical_game_identity or {}).with_retry_index(1)
                        pending.append(replace(entry.job, retry_index=1, canonical_game_identity=retry_identity.to_dict()))
                    else:
                        self._write_retry_attempt_v1(
                            entry.job, outcome, entry.games_dir,
                            first_attempt_diagnostics.get(entry.job.job_id),
                        )
                        outcomes[entry.job.job_id] = outcome
                else:
                    still_active.append(entry)
            active = still_active
            if active:
                time.sleep(0.05)
        return outcomes

    # -- opt-in fast path: persistent workers reading a shared job queue ---

    def _run_persistent(
        self, jobs: list[ActorJobConfigV1], *, output_root: Path,
    ) -> dict[str, ActorPoolJobOutcomeV1]:
        for job in jobs:
            (output_root / "games" / job.job_id / "scratch").mkdir(parents=True, exist_ok=True)
        job_queue: "mp.Queue[dict[str, object] | None]" = self._ctx.Queue()
        result_queue: "mp.Queue[dict[str, object]]" = self._ctx.Queue()
        workers = []
        for index in range(self._num_workers):
            scratch_dir = output_root / "games" / "_persistent_worker" / str(index)
            scratch_dir.mkdir(parents=True, exist_ok=True)
            process = self._ctx.Process(
                target=_persistent_actor_pool_worker_main_v1,
                args=(
                    job_queue, result_queue,
                    str(scratch_dir / "worker.stdout.log"), str(scratch_dir / "worker.stderr.log"),
                    str(output_root),
                ),
                daemon=False,
            )
            process.start()
            workers.append(process)

        # Do not enqueue sentinels until the parent has seen every final
        # attempt.  Enqueuing them with attempt 0 races a fault's retry behind
        # a worker shutdown and was the reason the old fast path never retried.
        active_attempts: dict[str, ActorJobConfigV1] = {job.job_id: job for job in jobs}
        for job in jobs:
            job_queue.put(job.to_payload())

        started = {job.job_id: time.monotonic() for job in jobs}
        overall_deadline = time.monotonic() + sum(job.timeout_seconds for job in jobs) * 2 + 30.0
        first_attempts: dict[str, tuple[ActorJobConfigV1, ActorPoolJobOutcomeV1]] = {}
        final_attempts: dict[str, tuple[ActorJobConfigV1, ActorPoolJobOutcomeV1]] = {}

        def _identity_for(job: ActorJobConfigV1) -> dict[str, object]:
            return CanonicalGameIdentityV1.from_dict(job.canonical_game_identity or {}).to_dict()

        def _outcome_from_message(job: ActorJobConfigV1, payload: Mapping[str, object]) -> ActorPoolJobOutcomeV1:
            expected_identity = _identity_for(job)
            raw_identity = payload.get("game_identity")
            message_has_identity = isinstance(raw_identity, Mapping)
            if raw_identity is None:
                # Compatibility for the pre-provenance test seam only.  The
                # production persistent target always sends the full identity.
                identity = expected_identity
            elif message_has_identity:
                identity = CanonicalGameIdentityV1.from_dict(raw_identity).to_dict()
                if identity != expected_identity:
                    raise ActorPoolV1Error("persistent worker returned a mismatched canonical game identity")
            else:
                raise ActorPoolV1Error("persistent worker returned an invalid canonical game identity")
            retry_index = payload.get("retry_index", job.retry_index)
            if type(retry_index) is not int or retry_index != job.retry_index:
                raise ActorPoolV1Error("persistent worker returned a mismatched retry_index")
            raw_diagnostic = payload.get("diagnostic")
            diagnostic = (
                FaultDiagnosticsV1.from_dict(raw_diagnostic)
                if isinstance(raw_diagnostic, Mapping) else None
            )
            process_id = payload.get("process_id")
            if type(process_id) is not int or process_id <= 0:
                process_id = None
            if diagnostic is not None and process_id is not None and diagnostic.process_id != process_id:
                # The parent binds the process identity only when the worker
                # supplied a real PID.  This catches malformed worker payloads
                # without turning a source diagnostic into a generic message.
                diagnostic = replace(diagnostic, process_id=process_id)
            stdout_excerpt = payload.get("stdout_excerpt")
            stderr_excerpt = payload.get("stderr_excerpt")
            attempt_metadata = payload.get("attempt_metadata")
            metadata = (
                dict(attempt_metadata) if isinstance(attempt_metadata, Mapping)
                else {"retry_index": retry_index, "persistent_worker": True}
            )
            status = payload.get("status")
            wall_time = payload.get("wall_time_seconds")
            if type(wall_time) not in (int, float) or type(wall_time) is bool:
                wall_time = time.monotonic() - started[job.job_id]
            games_dir = output_root / "games" / job.job_id
            if status == "completed" and is_actor_pool_job_complete_v1(games_dir):
                record = read_actor_pool_game_record_v1(games_dir / "record.json")
                record_identity = record.get("game_identity")
                if message_has_identity and isinstance(record_identity, Mapping):
                    record_identity = CanonicalGameIdentityV1.from_dict(record_identity).to_dict()
                    if record_identity != identity:
                        raise ActorPoolV1Error("persistent completed record identity disagrees with worker message")
                return ActorPoolJobOutcomeV1(
                    job_id=job.job_id, status="completed", record_path=str(games_dir / "record.json"),
                    transitions_count=record["transitions_count"], fault_reason=None,
                    wall_time_seconds=float(wall_time),
                    stdout_excerpt=stdout_excerpt if type(stdout_excerpt) is str else "",
                    stderr_excerpt=stderr_excerpt if type(stderr_excerpt) is str else "",
                    diagnostic=diagnostic, game_identity=identity, retry_index=retry_index,
                    process_id=process_id, attempt_metadata=metadata,
                )
            return ActorPoolJobOutcomeV1(
                job_id=job.job_id, status="faulted", record_path=None, transitions_count=0,
                fault_reason=str(payload.get("detail", "unknown persistent-worker fault")),
                wall_time_seconds=float(wall_time),
                stdout_excerpt=stdout_excerpt if type(stdout_excerpt) is str else "",
                stderr_excerpt=stderr_excerpt if type(stderr_excerpt) is str else "",
                diagnostic=diagnostic, game_identity=identity, retry_index=retry_index,
                process_id=process_id, attempt_metadata=metadata,
            )

        while active_attempts and time.monotonic() < overall_deadline:
            try:
                payload = result_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if not isinstance(payload, Mapping) or type(payload.get("job_id")) is not str:
                continue  # malformed stale queue traffic cannot satisfy an active job
            job_id = payload["job_id"]
            job = active_attempts.pop(job_id, None)
            if job is None:
                continue  # duplicate/stale message
            try:
                outcome = _outcome_from_message(job, payload)
            except Exception as exc:
                identity = _identity_for(job)
                diagnostic = capture_fault_v1(
                    exc, game_identity=CanonicalGameIdentityV1.from_dict(identity),
                    elapsed_seconds=time.monotonic() - started[job_id],
                )
                outcome = ActorPoolJobOutcomeV1(
                    job_id=job_id, status="faulted", record_path=None, transitions_count=0,
                    fault_reason=f"invalid persistent-worker message: {type(exc).__name__}: {exc}",
                    wall_time_seconds=time.monotonic() - started[job_id], diagnostic=diagnostic,
                    game_identity=identity, retry_index=job.retry_index,
                    attempt_metadata={"retry_index": job.retry_index, "persistent_worker": True},
                )
            if outcome.status in {"faulted", "timeout"} and job.retry_index == 0:
                first_attempts[job_id] = (job, outcome)
                retry_identity = CanonicalGameIdentityV1.from_dict(_identity_for(job)).with_retry_index(1)
                retry_job = replace(
                    job, retry_index=1, canonical_game_identity=retry_identity.to_dict(),
                )
                active_attempts[job_id] = retry_job
                job_queue.put(retry_job.to_payload())
            else:
                final_attempts[job_id] = (job, outcome)

        # A persistent hang cannot be safely isolated per job.  Kill the
        # shared workers and record the active attempts explicitly; timeout
        # recovery remains fail-closed rather than pretending a retry ran.
        timed_out_jobs = tuple(active_attempts.values())
        if timed_out_jobs:
            for process in workers:
                if process.is_alive():
                    self._kill_process_group(process)
            timed_out_worker_pids = tuple(
                process.pid for process in workers if process.pid is not None
            )
            for job in timed_out_jobs:
                identity = _identity_for(job)
                # A shared persistent queue cannot identify which worker had
                # dequeued a job when the pool is killed.  With one worker the
                # attribution is exact; with several, retain every observed
                # process exit in attempt metadata rather than inventing one.
                timed_out_process_id = (
                    timed_out_worker_pids[0] if len(timed_out_worker_pids) == 1 else None
                )
                timeout_outcome = ActorPoolJobOutcomeV1(
                    job_id=job.job_id, status="timeout", record_path=None, transitions_count=0,
                    fault_reason="persistent worker pool overall deadline exceeded",
                    wall_time_seconds=time.monotonic() - started[job.job_id],
                    game_identity=identity, retry_index=job.retry_index,
                    process_id=timed_out_process_id,
                    attempt_metadata={
                        "retry_index": job.retry_index,
                        "persistent_worker": True,
                        "persistent_worker_process_ids": list(timed_out_worker_pids),
                    },
                )
                if job.retry_index == 0:
                    first_attempts[job.job_id] = (job, timeout_outcome)
                    retry_identity = CanonicalGameIdentityV1.from_dict(identity).with_retry_index(1)
                    retry_job = replace(job, retry_index=1, canonical_game_identity=retry_identity.to_dict())
                    # The persistent process is gone; retain the one-retry
                    # contract through the normal fresh-process implementation.
                    retry_outcome = self._run_spawn_per_job([retry_job], output_root=output_root)[job.job_id]
                    final_attempts[job.job_id] = (retry_job, retry_outcome)
                else:
                    final_attempts[job.job_id] = (job, timeout_outcome)
        else:
            for _ in workers:
                job_queue.put(None)

        for process in workers:
            process.join(timeout=30)
            if process.is_alive():  # pragma: no cover - defensive fallback
                self._kill_process_group(process)
        exit_codes = {process.pid: process.exitcode for process in workers if process.pid is not None}

        def _bind_actual_process_exit(outcome: ActorPoolJobOutcomeV1) -> ActorPoolJobOutcomeV1:
            metadata = dict(outcome.attempt_metadata or {})
            # Spawn fallback children are not members of ``workers``.  Looking
            # their PID up in this map was the lossy overwrite that converted a
            # real exit code 0 into None after a persistent timeout.
            if not metadata.get("persistent_worker", False):
                return outcome
            observed_pids = metadata.get("persistent_worker_process_ids")
            if isinstance(observed_pids, list):
                observed_exit_codes = {
                    str(pid): exit_codes[pid]
                    for pid in observed_pids
                    if type(pid) is int and pid in exit_codes
                }
                if observed_exit_codes:
                    metadata["persistent_worker_exit_codes"] = observed_exit_codes
            exit_code = exit_codes.get(outcome.process_id) if outcome.process_id is not None else None
            diagnostic = outcome.diagnostic
            if diagnostic is not None:
                diagnostic = replace(
                    diagnostic,
                    process_id=(outcome.process_id if outcome.process_id is not None else diagnostic.process_id),
                    worker_exit_code=exit_code,
                )
            return replace(
                outcome, worker_exit_code=exit_code, diagnostic=diagnostic,
                attempt_metadata=metadata,
            )

        outcomes: dict[str, ActorPoolJobOutcomeV1] = {}
        for job_id, (final_job, outcome) in final_attempts.items():
            outcome = _bind_actual_process_exit(outcome)
            if job_id in first_attempts:
                first_job, first_outcome = first_attempts[job_id]
                first_outcome = _bind_actual_process_exit(first_outcome)
                first_diagnostic = self._write_retry_attempt_v1(
                    first_job, first_outcome, output_root / "games" / job_id,
                )
                self._write_retry_attempt_v1(
                    final_job, outcome, output_root / "games" / job_id, first_diagnostic,
                )
            outcomes[job_id] = outcome
        return outcomes


__all__ = [
    "GAME_RECORD_SCHEMA_V1",
    "MAX_GAME_RECORD_JSON_BYTES_V1",
    "MAX_GAME_RECORD_JSON_NODES_V1",
    "ActorGameCollectionResultV1",
    "ActorGameFaultV1",
    "ActorJobConfigV1",
    "ActorPoolJobOutcomeV1",
    "ActorPoolV1",
    "ActorPoolV1Error",
    "RecordedActorDecisionV1",
    "build_actor_pool_game_record_v1",
    "build_rule_agent_policy_factory_v1",
    "current_repo_commit_v1",
    "derive_actor_job_id_v1",
    "derive_game_sampling_seed_v1",
    "engine_identity_v1",
    "guard_worker_against_cuda_v1",
    "is_actor_pool_job_complete_v1",
    "neural_checkpoint_behavior_identity_v1",
    "read_actor_pool_game_record_v1",
    "rule_agent_behavior_identity_v1",
    "run_one_actor_game_v1",
    "worker_cuda_diagnostics_v1",
    "write_actor_pool_game_record_v1",
]
