"""Fail-closed diagnostics for CABT native root branching.

CABT owns the complete game state in ``libcg`` behind a ctypes pointer.  This
module intentionally exposes diagnostics only: its process-fork probe can
prove copy-on-write isolation of the native heap, but it must not be used for
policy evidence unless it also proves a common RNG continuation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import select
from typing import Any, Callable, Mapping, Sequence


class CabtRootBranchError(RuntimeError):
    """Raised when a native CABT diagnostic cannot be run safely."""


def _canonical_digest(value: object, domain: str) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((domain + "\0" + payload).encode("utf-8")).hexdigest()


def _actor_digest(observation: Mapping[str, Any]) -> str:
    """Digest only the existing privacy-safe actor projection, never raw obs."""
    from mage_ptcg.decision_state import build_decision_state

    return build_decision_state(observation).actor_view.digest


def _legal_digest(observation: Mapping[str, Any]) -> str:
    selection = observation.get("select")
    if not isinstance(selection, Mapping):
        return _canonical_digest({"select": None}, "cabt-legal-actions")
    # The raw option list can include private engine details.  It is hashed
    # transiently for equality only and never returned or persisted.
    return _canonical_digest(selection, "cabt-legal-actions")


def _full_digest(observation: Mapping[str, Any]) -> str:
    """Transient native observation equality probe; the observation is not kept."""
    return _canonical_digest(observation, "cabt-native-full-observation")


def _selection_candidates(observation: Mapping[str, Any]) -> tuple[list[int], list[int] | None]:
    selection = observation.get("select")
    if not isinstance(selection, Mapping):
        raise CabtRootBranchError("root has no active selection")
    options = selection.get("option")
    lower, upper = selection.get("minCount"), selection.get("maxCount")
    if not isinstance(options, list) or type(lower) is not int or type(upper) is not int:
        raise CabtRootBranchError("root selection is malformed")
    if lower < 0 or upper < lower or upper > len(options):
        raise CabtRootBranchError("root selection bounds are invalid")
    primary = list(range(lower))
    if lower == 0 and upper > 0 and options:
        return primary, [0]
    if lower > 0 and len(options) > lower:
        return primary, list(range(lower - 1)) + [lower]
    return primary, None


@dataclass(frozen=True)
class NativeBranchTrial:
    exit_status: int
    root_full_digest: str | None
    root_actor_digest: str | None
    root_legal_digest: str | None
    after_full_digest: str | None
    after_actor_digest: str | None
    after_legal_digest: str | None
    error_type: str | None = None


@dataclass(frozen=True)
class NativeBranchAssessment:
    backend: str
    status: str
    root_trial_count: int
    prebranch_equal: bool
    parent_isolated: bool
    same_action_deterministic: bool
    different_action_diverged: bool | None
    rng_continuation: str
    native_state: str
    safety: str
    reason: str

    def payload(self) -> dict[str, object]:
        return asdict(self)


def assess_trials(*, root_full_digest: str, root_actor_digest: str | None, root_legal_digest: str | None,
                  parent_after_full_digest: str, repeated: Sequence[NativeBranchTrial],
                  alternate: NativeBranchTrial | None, rng_continuation: str) -> NativeBranchAssessment:
    """Classify probes without ever upgrading unproven RNG state to evidence."""
    successful = [item for item in repeated if item.exit_status == 0 and item.error_type is None]
    prebranch_equal = len(successful) == len(repeated) and all(
        item.root_full_digest == root_full_digest
        and item.root_actor_digest == root_actor_digest
        and item.root_legal_digest == root_legal_digest
        for item in successful
    )
    parent_isolated = parent_after_full_digest == root_full_digest
    same_action_deterministic = bool(successful) and len({item.after_full_digest for item in successful}) == 1
    different_action_diverged = None
    if alternate is not None and alternate.exit_status == 0 and alternate.error_type is None and successful:
        different_action_diverged = alternate.after_full_digest != successful[0].after_full_digest
    safety = "PASS" if len(successful) == len(repeated) and parent_isolated else "FAIL"
    if safety != "PASS":
        return NativeBranchAssessment("PROCESS_FORK_NATIVE_HEAP", "UNSAFE", len(repeated), prebranch_equal, parent_isolated,
                                      same_action_deterministic, different_action_diverged, rng_continuation,
                                      "COPY_ON_WRITE_UNVERIFIED", safety, "child failure or parent mutation during native fork probe")
    if rng_continuation != "PROVEN_IDENTICAL" or not same_action_deterministic:
        return NativeBranchAssessment("PROCESS_FORK_NATIVE_HEAP", "DIAGNOSTIC_ONLY", len(repeated), prebranch_equal, parent_isolated,
                                      same_action_deterministic, different_action_diverged, rng_continuation,
                                      "COPY_ON_WRITE_ONLY", safety,
                                      "native root is isolated but post-root RNG continuation is not identical; evidence is invalid for paired CTDE")
    return NativeBranchAssessment("PROCESS_FORK_NATIVE_HEAP", "CTDE_READY_WITH_LIMITATIONS", len(repeated), prebranch_equal,
                                  parent_isolated, same_action_deterministic, different_action_diverged, rng_continuation,
                                  "COPY_ON_WRITE_NATIVE_STATE", safety, "native fork verified for this bounded probe only")


def native_rng_inventory() -> dict[str, object]:
    """Report ABI facts; no claim is inferred from Python's RNG state."""
    from kaggle_environments.envs.cabt.cg import sim

    exported = sorted(name for name in ("BattleStart", "BattleFinish", "GetBattleData", "Select", "VisualizeData", "SetSeed", "SaveState", "LoadState", "CloneBattle") if hasattr(sim.lib, name))
    return {
        "library": os.path.basename(sim.lib_path),
        "state_owner": "native-libcg-ctypes-pointer",
        "public_abi": exported,
        "seed_support": "NOT_EXPORTED",
        "state_export": "NOT_EXPORTED",
        "state_restore": "NOT_EXPORTED",
        "clone": "NOT_EXPORTED",
        "rng": "native-random_device-observed-in-binary",
        "rng_state_extract_restore": "NOT_SUPPORTED",
    }


def _child_trial(*, select_action: Callable[[list[int]], Mapping[str, Any]], action: list[int], write_fd: int) -> None:
    try:
        from kaggle_environments.envs.cabt.cg.sim import Battle

        root = Battle.obs
        if not isinstance(root, Mapping):
            raise CabtRootBranchError("native battle observation unavailable")
        next_observation = select_action(action)
        payload = NativeBranchTrial(
            0, _full_digest(root), _actor_digest(root), _legal_digest(root), _full_digest(next_observation),
            _actor_digest(next_observation), _legal_digest(next_observation), None,
        )
    except BaseException as exc:  # Child must serialize only the exception type.
        payload = NativeBranchTrial(3, None, None, None, None, None, None, type(exc).__name__)
    os.write(write_fd, json.dumps(asdict(payload), sort_keys=True).encode("utf-8"))
    os.close(write_fd)
    os._exit(payload.exit_status)


def _fork_trial(*, select_action: Callable[[list[int]], Mapping[str, Any]], action: list[int], timeout_seconds: float) -> NativeBranchTrial:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        _child_trial(select_action=select_action, action=action, write_fd=write_fd)
    os.close(write_fd)
    ready, _, _ = select.select([read_fd], [], [], timeout_seconds)
    payload = os.read(read_fd, 65536).decode("utf-8") if ready else ""
    os.close(read_fd)
    _, wait_status = os.waitpid(pid, 0)
    if not payload:
        return NativeBranchTrial(wait_status or 124, None, None, None, None, None, None, "TIMEOUT_OR_EMPTY_CHILD_RESULT")
    row = json.loads(payload)
    return NativeBranchTrial(exit_status=wait_status, **{key: value for key, value in row.items() if key != "exit_status"})


def run_native_fork_probe(*, deck_a: Sequence[int], deck_b: Sequence[int], warmup_steps: int = 12,
                          repeats: int = 20, timeout_seconds: float = 10.0) -> NativeBranchAssessment:
    """Run a bounded native probe and return digests only.

    This intentionally uses no Python ``random`` or NumPy RNG.  On non-POSIX
    systems, or with an active CABT battle, it fails closed before mutation.
    """
    if os.name != "posix" or not hasattr(os, "fork"):
        raise CabtRootBranchError("process fork is unavailable")
    if repeats < 20:
        raise CabtRootBranchError("at least 20 repetitions are required")
    from kaggle_environments.envs.cabt.cg.game import battle_finish, battle_select, battle_start
    from kaggle_environments.envs.cabt.cg.sim import Battle

    if Battle.battle_ptr not in (None, 0):
        raise CabtRootBranchError("a CABT battle is already active")
    observation = None
    try:
        observation, start = battle_start(list(deck_a), list(deck_b))
        if start.errorPlayer >= 0 or not isinstance(observation, Mapping):
            raise CabtRootBranchError("native battle start failed")
        for _ in range(warmup_steps):
            if observation.get("current", {}).get("result", -1) >= 0:
                break
            primary, _ = _selection_candidates(observation)
            observation = battle_select(primary)
        if not isinstance(observation, Mapping):
            raise CabtRootBranchError("native root observation unavailable")
        root_full = _full_digest(observation)
        root_actor = _actor_digest(observation)
        root_legal = _legal_digest(observation)
        primary, alternate_action = _selection_candidates(observation)
        repeated = [_fork_trial(select_action=battle_select, action=primary, timeout_seconds=timeout_seconds) for _ in range(repeats)]
        alternate = _fork_trial(select_action=battle_select, action=alternate_action, timeout_seconds=timeout_seconds) if alternate_action else None
        parent_after = _full_digest(Battle.obs)
        return assess_trials(root_full_digest=root_full, root_actor_digest=root_actor, root_legal_digest=root_legal,
                             parent_after_full_digest=parent_after, repeated=repeated, alternate=alternate,
                             rng_continuation="UNCONTROLLED_NATIVE_RANDOM_DEVICE")
    finally:
        if Battle.battle_ptr not in (None, 0):
            battle_finish()
            Battle.battle_ptr = None
