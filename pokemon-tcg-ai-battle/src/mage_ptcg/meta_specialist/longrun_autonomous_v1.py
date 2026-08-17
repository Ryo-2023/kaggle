"""Fail-closed, research-only contract for autonomous meta fine-tuning.

This module owns the *lineage and gates* around a possible long run.  It does
not collect trajectories, train a model, invoke CABT, mutate a native agent,
or submit a package.  A caller may use :func:`launch_longrun_v1` with an
explicit runner only after a hash-bound ``LONGRUN_READY`` gate has been
recorded; ``execute=False`` always produces a dry-run descriptor and never
calls the runner.

The contract intentionally binds three things that are easy to accidentally
mix during an overnight run:

* the immutable meta manifest and its exact META_TRAIN/DEV/FINAL membership;
* the native BestKnown deck/policy/evaluator identities; and
* the checkpoint journal, including the active checkpoint used for rollback.

All JSON writes are atomic.  Source files are re-hashed when a run is loaded,
so editing a manifest or baseline while a process is asleep turns into a
closed error instead of silently changing the experiment.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionError,
    load_meta_distribution_manifest_v1,
)


LONGRUN_SCHEMA_V1 = "meta-specialist-autonomous-longrun-v1"
GATE_SCHEMA_V1 = "meta-specialist-autonomous-longrun-gate-v1"
CHECKPOINT_SCHEMA_V1 = "meta-specialist-autonomous-longrun-checkpoint-v1"
_STATUSES_V1 = frozenset(
    {"DRY_RUN", "PENDING", "BLOCKED", "LONGRUN_READY", "RUNNING", "CHECKPOINTED", "STOPPED", "ROLLED_BACK", "FAILED"}
)
_BASELINE_STATUSES_V1 = frozenset({"PROVEN", "UNPROVEN"})
_SHA_CHARS = frozenset("0123456789abcdef")


class LongrunError(ValueError):
    """Raised when a long-run precondition, identity, or state is invalid."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise LongrunError(f"{name} must be a non-empty string")
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise LongrunError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _finite_unit(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise LongrunError(f"{name} must be a numeric value")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise LongrunError(f"{name} must be finite in [0,1]")
    return result


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise LongrunError(f"{name} must be a positive integer")
    return value


def _sha256_file(path: Path | str) -> str:
    source = Path(path)
    try:
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LongrunError(f"cannot hash file: {source}") from exc
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LongrunError(f"value is not canonically serializable: {exc}") from exc


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LongrunError(f"cannot read JSON state: {path}") from exc
    if not isinstance(value, dict):
        raise LongrunError(f"JSON state must be an object: {path}")
    return value


def _append_event(path: Path, event: Mapping[str, Any]) -> None:
    """Append one fsynced event without routing it through a line logger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


@dataclass(frozen=True, slots=True)
class NativeBaselineV1:
    """Immutable identity of the native BestKnown comparison arm."""

    pair_id: str
    deck_sha256: str
    policy_sha256: str
    evaluator_sha256: str
    status: str = "UNPROVEN"

    def __post_init__(self) -> None:
        _text(self.pair_id, "native_baseline.pair_id")
        _sha(self.deck_sha256, "native_baseline.deck_sha256")
        _sha(self.policy_sha256, "native_baseline.policy_sha256")
        _sha(self.evaluator_sha256, "native_baseline.evaluator_sha256")
        if self.status not in _BASELINE_STATUSES_V1:
            raise LongrunError(f"native_baseline.status must be one of {sorted(_BASELINE_STATUSES_V1)}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LongrunConfigV1:
    """Hash-bound fixed-split configuration for one research lineage."""

    run_dir: Path
    manifest_path: Path
    manifest_sha256: str
    native_baseline: NativeBaselineV1
    meta_train_ids: tuple[str, ...]
    meta_dev_ids: tuple[str, ...]
    meta_final_ids: tuple[str, ...]
    checkpoint_interval: int = 1
    stop_after_regressions: int = 2
    min_dev_delta: float = 0.01
    max_seat_gap: float = 0.05
    research_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_dir", Path(self.run_dir).resolve())
        object.__setattr__(self, "manifest_path", Path(self.manifest_path).resolve())
        _sha(self.manifest_sha256, "manifest_sha256")
        if type(self.native_baseline) is not NativeBaselineV1:
            raise LongrunError("native_baseline must be a NativeBaselineV1")
        for name in ("meta_train_ids", "meta_dev_ids", "meta_final_ids"):
            values = getattr(self, name)
            if type(values) is not tuple or not values or any(type(item) is not str or not item for item in values):
                raise LongrunError(f"{name} must be a non-empty tuple of IDs")
            if len(set(values)) != len(values):
                raise LongrunError(f"{name} must not contain duplicate IDs")
        split_sets = (set(self.meta_train_ids), set(self.meta_dev_ids), set(self.meta_final_ids))
        if split_sets[0] & split_sets[1] or split_sets[0] & split_sets[2] or split_sets[1] & split_sets[2]:
            raise LongrunError("META_TRAIN, META_DEV, and META_FINAL must be disjoint")
        _positive_int(self.checkpoint_interval, "checkpoint_interval")
        _positive_int(self.stop_after_regressions, "stop_after_regressions")
        if type(self.min_dev_delta) not in (int, float) or isinstance(self.min_dev_delta, bool) or not math.isfinite(float(self.min_dev_delta)) or float(self.min_dev_delta) <= 0.0:
            raise LongrunError("min_dev_delta must be a finite positive number")
        if type(self.max_seat_gap) not in (int, float) or isinstance(self.max_seat_gap, bool) or not math.isfinite(float(self.max_seat_gap)) or not 0.0 <= float(self.max_seat_gap) <= 1.0:
            raise LongrunError("max_seat_gap must be finite in [0,1]")
        if self.research_only is not True:
            raise LongrunError("autonomous meta longrun must remain research_only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": str(self.run_dir),
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "native_baseline": self.native_baseline.to_dict(),
            "meta_train_ids": list(self.meta_train_ids),
            "meta_dev_ids": list(self.meta_dev_ids),
            "meta_final_ids": list(self.meta_final_ids),
            "checkpoint_interval": self.checkpoint_interval,
            "stop_after_regressions": self.stop_after_regressions,
            "min_dev_delta": float(self.min_dev_delta),
            "max_seat_gap": float(self.max_seat_gap),
            "research_only": self.research_only,
        }


@dataclass(frozen=True, slots=True)
class BlockEvidenceV1:
    """One independent META_DEV block used by the start gate."""

    block_id: str
    split: str
    seed_id: str
    games: int
    native_score: float
    candidate_score: float
    fault_count: int
    seat0_games: int
    seat1_games: int
    seat0_candidate_score: float
    seat1_candidate_score: float

    def __post_init__(self) -> None:
        _text(self.block_id, "block_id")
        if self.split not in {"META_DEV"}:
            raise LongrunError("longrun gate blocks must be META_DEV only; META_FINAL is held out")
        _text(self.seed_id, "seed_id")
        _positive_int(self.games, "games")
        _finite_unit(self.native_score, "native_score")
        _finite_unit(self.candidate_score, "candidate_score")
        if type(self.fault_count) is not int or self.fault_count < 0 or self.fault_count > self.games:
            raise LongrunError("fault_count must be in [0,games]")
        for name in ("seat0_games", "seat1_games"):
            _positive_int(getattr(self, name), name)
        if self.seat0_games + self.seat1_games != self.games:
            raise LongrunError("seat game counts must sum to games")
        _finite_unit(self.seat0_candidate_score, "seat0_candidate_score")
        _finite_unit(self.seat1_candidate_score, "seat1_candidate_score")

    @property
    def candidate_delta(self) -> float:
        return float(self.candidate_score) - float(self.native_score)

    @property
    def seat_gap(self) -> float:
        return abs(float(self.seat0_candidate_score) - float(self.seat1_candidate_score))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"candidate_delta": self.candidate_delta, "seat_gap": self.seat_gap}


@dataclass(frozen=True, slots=True)
class GateEvidenceV1:
    """Machine-checkable evidence for the ``LONGRUN_READY`` transition."""

    schema_version: str
    baseline_pair_id: str
    manifest_sha256: str | None
    meta_train_ids: tuple[str, ...]
    meta_dev_ids: tuple[str, ...]
    meta_final_ids: tuple[str, ...]
    blocks: tuple[BlockEvidenceV1, ...]
    native_baseline_ok: bool
    meta_split_ok: bool
    meta_final_isolated: bool
    fault_free: bool
    seat_balance_ok: bool
    seed_stability_ok: bool
    dev_improvement_ok: bool
    package_closure: bool
    rollback_ready: bool
    min_dev_delta: float
    max_seat_gap: float
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != GATE_SCHEMA_V1:
            raise LongrunError("wrong gate schema")
        _text(self.baseline_pair_id, "gate.baseline_pair_id")
        if self.manifest_sha256 is not None:
            _sha(self.manifest_sha256, "gate.manifest_sha256")
        for name in ("meta_train_ids", "meta_dev_ids", "meta_final_ids"):
            values = getattr(self, name)
            if type(values) is not tuple or any(type(item) is not str or not item for item in values):
                raise LongrunError(f"gate.{name} must be a tuple of strings")
        if type(self.blocks) is not tuple or any(type(item) is not BlockEvidenceV1 for item in self.blocks):
            raise LongrunError("gate.blocks must be a tuple of BlockEvidenceV1")
        for name in (
            "native_baseline_ok", "meta_split_ok", "meta_final_isolated", "fault_free",
            "seat_balance_ok", "seed_stability_ok", "dev_improvement_ok",
            "package_closure", "rollback_ready",
        ):
            if type(getattr(self, name)) is not bool:
                raise LongrunError(f"gate.{name} must be bool")
        if type(self.min_dev_delta) not in (int, float) or not math.isfinite(float(self.min_dev_delta)) or float(self.min_dev_delta) <= 0:
            raise LongrunError("gate.min_dev_delta must be finite and positive")
        if type(self.max_seat_gap) not in (int, float) or not math.isfinite(float(self.max_seat_gap)) or not 0.0 <= float(self.max_seat_gap) <= 1.0:
            raise LongrunError("gate.max_seat_gap must be finite in [0,1]")

    @property
    def ready(self) -> bool:
        return all(
            (
                self.native_baseline_ok,
                self.meta_split_ok,
                self.meta_final_isolated,
                self.fault_free,
                self.seat_balance_ok,
                self.seed_stability_ok,
                self.dev_improvement_ok,
                self.package_closure,
                self.rollback_ready,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "baseline_pair_id": self.baseline_pair_id,
            "manifest_sha256": self.manifest_sha256,
            "meta_train_ids": list(self.meta_train_ids),
            "meta_dev_ids": list(self.meta_dev_ids),
            "meta_final_ids": list(self.meta_final_ids),
            "blocks": [block.to_dict() for block in self.blocks],
            "native_baseline_ok": self.native_baseline_ok,
            "meta_split_ok": self.meta_split_ok,
            "meta_final_isolated": self.meta_final_isolated,
            "fault_free": self.fault_free,
            "seat_balance_ok": self.seat_balance_ok,
            "seed_stability_ok": self.seed_stability_ok,
            "dev_improvement_ok": self.dev_improvement_ok,
            "package_closure": self.package_closure,
            "rollback_ready": self.rollback_ready,
            "min_dev_delta": float(self.min_dev_delta),
            "max_seat_gap": float(self.max_seat_gap),
            "ready": self.ready,
            "reasons": list(self.reasons),
        }


def config_sha256_v1(config: LongrunConfigV1) -> str:
    """Return the immutable configuration identity used by every state file."""
    if type(config) is not LongrunConfigV1:
        raise LongrunError("config must be a LongrunConfigV1")
    return hashlib.sha256(b"mage-ptcg:autonomous-longrun-config:v1\0" + _canonical_bytes(config.to_dict())).hexdigest()


def _verify_manifest_v1(config: LongrunConfigV1) -> None:
    if not config.manifest_path.is_file():
        raise LongrunError(f"meta manifest does not exist: {config.manifest_path}")
    actual = _sha256_file(config.manifest_path)
    if actual != config.manifest_sha256:
        raise LongrunError(
            f"manifest SHA-256 changed: expected {config.manifest_sha256}, observed {actual}"
        )
    try:
        manifest = load_meta_distribution_manifest_v1(config.manifest_path, verify_sources=True)
    except MetaDistributionError as exc:
        raise LongrunError(f"meta manifest is not a valid closed manifest: {exc}") from exc
    if not manifest.research_only or manifest.training_authority or manifest.promotion_authority or manifest.submission_authority:
        raise LongrunError("meta manifest must remain research_only with all authority flags false")
    expected = {
        "META_TRAIN": tuple(sorted(config.meta_train_ids)),
        "META_DEV": tuple(sorted(config.meta_dev_ids)),
        "META_FINAL": tuple(sorted(config.meta_final_ids)),
    }
    for split, expected_ids in expected.items():
        actual_ids = tuple(sorted(manifest.split_ids.get(split, ())))
        if actual_ids != expected_ids:
            raise LongrunError(
                f"{split} membership differs from sealed config: expected {list(expected_ids)}, observed {list(actual_ids)}"
            )


def _manifest_state_v1(config: LongrunConfigV1) -> Path:
    return config.run_dir / "run-manifest.json"


def _progress_state_v1(config: LongrunConfigV1) -> Path:
    return config.run_dir / "progress_summary.json"


def _events_path_v1(config: LongrunConfigV1) -> Path:
    return config.run_dir / "events.jsonl"


def _verify_state_v1(config: LongrunConfigV1) -> dict[str, Any]:
    _verify_manifest_v1(config)
    path = _manifest_state_v1(config)
    if not path.is_file():
        raise LongrunError(f"longrun state does not exist: {path}")
    state = _load_json(path)
    if state.get("schema_version") != LONGRUN_SCHEMA_V1:
        raise LongrunError("wrong autonomous longrun state schema")
    expected_config_sha = config_sha256_v1(config)
    if state.get("config_sha256") != expected_config_sha:
        raise LongrunError("config SHA-256 does not match initially sealed longrun state")
    status = state.get("status")
    if status not in _STATUSES_V1:
        raise LongrunError(f"unknown longrun status: {status!r}")
    if state.get("manifest_sha256") != config.manifest_sha256:
        raise LongrunError("sealed manifest SHA-256 does not match config")
    return state


def _write_progress_v1(config: LongrunConfigV1, state: Mapping[str, Any]) -> None:
    progress = {
        "schema_version": LONGRUN_SCHEMA_V1,
        "status": state.get("status"),
        "stage": state.get("stage", "preflight"),
        "revision": state.get("revision", 0),
        "config_sha256": state.get("config_sha256"),
        "latest_checkpoint_sha256": state.get("latest_checkpoint_sha256"),
        "active_checkpoint_sha256": state.get("active_checkpoint_sha256"),
        "gate_ready": bool((state.get("gate") or {}).get("ready", False)),
        "restart_contract": "atomic_checkpoint_resume_with_explicit_rollback_v1",
        "research_only": True,
    }
    _atomic_write_json(_progress_state_v1(config), progress)


def _update_state_v1(config: LongrunConfigV1, *, status: str, stage: str | None = None, **updates: Any) -> dict[str, Any]:
    if status not in _STATUSES_V1:
        raise LongrunError(f"unknown longrun status: {status!r}")
    state = _verify_state_v1(config)
    revision = int(state.get("revision", 0)) + 1
    state["status"] = status
    state["revision"] = revision
    if stage is not None:
        state["stage"] = stage
    state.update(updates)
    _atomic_write_json(_manifest_state_v1(config), state)
    _write_progress_v1(config, state)
    _append_event(
        _events_path_v1(config),
        {"schema_version": LONGRUN_SCHEMA_V1, "revision": revision, "status": status, "stage": state.get("stage"), "updates": updates},
    )
    return state


def initialize_longrun_v1(config: LongrunConfigV1, *, execute: bool = False) -> dict[str, Any]:
    """Seal a dry-run descriptor, or refuse an unproven execution request."""
    if type(config) is not LongrunConfigV1:
        raise LongrunError("config must be a LongrunConfigV1")
    _verify_manifest_v1(config)
    config_sha = config_sha256_v1(config)
    state_path = _manifest_state_v1(config)
    if state_path.exists():
        state = _verify_state_v1(config)
        if execute:
            _require_ready_v1(state)
        return state
    config.run_dir.mkdir(parents=True, exist_ok=True)
    status = "PENDING" if execute else "DRY_RUN"
    state: dict[str, Any] = {
        "schema_version": LONGRUN_SCHEMA_V1,
        "status": status,
        "stage": "preflight",
        "revision": 0,
        "config_sha256": config_sha,
        "config": config.to_dict(),
        "manifest_sha256": config.manifest_sha256,
        "native_baseline": config.native_baseline.to_dict(),
        "meta_splits": {
            "META_TRAIN": list(config.meta_train_ids),
            "META_DEV": list(config.meta_dev_ids),
            "META_FINAL": list(config.meta_final_ids),
        },
        "gate": None,
        "latest_checkpoint_path": None,
        "latest_checkpoint_sha256": None,
        "active_checkpoint_path": None,
        "active_checkpoint_sha256": None,
        "checkpoint_count": 0,
        "regression_count": 0,
        "research_only": True,
        "execute_requested": bool(execute),
        "launch_allowed": False,
    }
    _atomic_write_json(state_path, state)
    _write_progress_v1(config, state)
    _append_event(
        _events_path_v1(config),
        {"schema_version": LONGRUN_SCHEMA_V1, "revision": 0, "status": status, "event": "initialize", "execute_requested": bool(execute)},
    )
    if execute:
        raise LongrunError("LONGRUN_READY gate is not satisfied; execution remains fail-closed")
    return state


def load_longrun_state_v1(config: LongrunConfigV1) -> dict[str, Any]:
    """Reload and re-hash a sealed state before any resume or mutation."""
    return _verify_state_v1(config)


def _require_ready_v1(state: Mapping[str, Any]) -> None:
    gate = state.get("gate")
    if not isinstance(gate, Mapping) or gate.get("ready") is not True:
        raise LongrunError("LONGRUN_READY gate is not satisfied; execution remains fail-closed")


def evaluate_longrun_gate_v1(
    *,
    baseline: NativeBaselineV1,
    meta_train_ids: Sequence[str],
    meta_dev_ids: Sequence[str],
    meta_final_ids: Sequence[str],
    blocks: Sequence[BlockEvidenceV1],
    package_closure: bool,
    manifest_sha256: str | None = None,
    min_dev_delta: float = 0.01,
    max_seat_gap: float = 0.05,
) -> GateEvidenceV1:
    """Build a gate report without changing state or launching any process."""
    if type(baseline) is not NativeBaselineV1:
        raise LongrunError("baseline must be a NativeBaselineV1")
    train = tuple(meta_train_ids)
    dev = tuple(meta_dev_ids)
    final = tuple(meta_final_ids)
    all_ids = train + dev + final
    disjoint = len(set(all_ids)) == len(all_ids) and bool(train) and bool(dev) and bool(final)
    block_tuple = tuple(blocks)
    if any(type(block) is not BlockEvidenceV1 for block in block_tuple):
        raise LongrunError("blocks must contain only BlockEvidenceV1 values")
    block_ids = [block.block_id for block in block_tuple]
    seed_ids = [block.seed_id for block in block_tuple]
    fault_free = bool(block_tuple) and all(block.fault_count == 0 for block in block_tuple)
    seat_balance_ok = bool(block_tuple) and all(block.seat_gap <= float(max_seat_gap) for block in block_tuple)
    seed_stability_ok = len(set(block_ids)) >= 2 and len(set(seed_ids)) >= 2
    meta_final_isolated = bool(final) and all(block.split != "META_FINAL" for block in block_tuple)
    dev_improvement_ok = (
        bool(block_tuple)
        and all(block.split == "META_DEV" and block.candidate_delta >= float(min_dev_delta) for block in block_tuple)
    )
    reasons: list[str] = []
    checks = {
        "native_baseline_ok": baseline.status == "PROVEN",
        "meta_split_ok": disjoint,
        "meta_final_isolated": meta_final_isolated,
        "fault_free": fault_free,
        "seat_balance_ok": seat_balance_ok,
        "seed_stability_ok": seed_stability_ok,
        "dev_improvement_ok": dev_improvement_ok,
        "package_closure": bool(package_closure),
        "rollback_ready": bool(package_closure),
    }
    for name, passed in checks.items():
        if not passed:
            reasons.append(name)
    return GateEvidenceV1(
        schema_version=GATE_SCHEMA_V1,
        baseline_pair_id=baseline.pair_id,
        manifest_sha256=manifest_sha256,
        meta_train_ids=tuple(sorted(train)),
        meta_dev_ids=tuple(sorted(dev)),
        meta_final_ids=tuple(sorted(final)),
        blocks=block_tuple,
        native_baseline_ok=checks["native_baseline_ok"],
        meta_split_ok=checks["meta_split_ok"],
        meta_final_isolated=checks["meta_final_isolated"],
        fault_free=checks["fault_free"],
        seat_balance_ok=checks["seat_balance_ok"],
        seed_stability_ok=checks["seed_stability_ok"],
        dev_improvement_ok=checks["dev_improvement_ok"],
        package_closure=checks["package_closure"],
        rollback_ready=checks["rollback_ready"],
        min_dev_delta=float(min_dev_delta),
        max_seat_gap=float(max_seat_gap),
        reasons=tuple(reasons),
    )


def record_gate_v1(config: LongrunConfigV1, gate: GateEvidenceV1) -> dict[str, Any]:
    """Bind a gate report to the sealed config; no final split is consumed."""
    if type(gate) is not GateEvidenceV1:
        raise LongrunError("gate must be a GateEvidenceV1")
    _verify_state_v1(config)
    if gate.baseline_pair_id != config.native_baseline.pair_id:
        raise LongrunError("gate baseline pair does not match sealed native baseline")
    if gate.manifest_sha256 is not None and gate.manifest_sha256 != config.manifest_sha256:
        raise LongrunError("gate manifest SHA-256 does not match sealed manifest")
    expected = {
        "META_TRAIN": tuple(sorted(config.meta_train_ids)),
        "META_DEV": tuple(sorted(config.meta_dev_ids)),
        "META_FINAL": tuple(sorted(config.meta_final_ids)),
    }
    observed = {
        "META_TRAIN": gate.meta_train_ids,
        "META_DEV": gate.meta_dev_ids,
        "META_FINAL": gate.meta_final_ids,
    }
    for split in expected:
        if observed[split] != expected[split]:
            raise LongrunError(f"{split} gate membership does not match sealed config")
    _atomic_write_json(config.run_dir / "gate.json", gate.to_dict())
    return _update_state_v1(
        config,
        status="LONGRUN_READY" if gate.ready else "BLOCKED",
        stage="gate",
        gate=gate.to_dict(),
    )


def checkpoint_longrun_v1(
    config: LongrunConfigV1,
    *,
    checkpoint_path: Path | str,
    stage: str,
    checkpoint_sha256: str | None = None,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish one content-addressed checkpoint and make it resumable."""
    state = _verify_state_v1(config)
    if type(stage) is not str or not stage.strip():
        raise LongrunError("checkpoint stage must be a non-empty string")
    source = Path(checkpoint_path).resolve()
    if not source.is_file():
        raise LongrunError(f"checkpoint does not exist: {source}")
    actual = _sha256_file(source)
    if checkpoint_sha256 is not None and _sha(checkpoint_sha256, "checkpoint_sha256") != actual:
        raise LongrunError("checkpoint SHA-256 does not match checkpoint bytes")
    count = int(state.get("checkpoint_count", 0)) + 1
    descriptor = {
        "schema_version": CHECKPOINT_SCHEMA_V1,
        "ordinal": count,
        "stage": stage,
        "path": str(source),
        "checkpoint_sha256": actual,
        "config_sha256": config_sha256_v1(config),
        "manifest_sha256": config.manifest_sha256,
        "native_baseline": config.native_baseline.to_dict(),
        "metrics": dict(metrics or {}),
        "research_only": True,
    }
    descriptor_path = config.run_dir / "checkpoints" / f"{count:04d}-{actual[:16]}.json"
    _atomic_write_json(descriptor_path, descriptor)
    return _update_state_v1(
        config,
        status="CHECKPOINTED",
        stage=stage,
        latest_checkpoint_path=str(source),
        latest_checkpoint_sha256=actual,
        checkpoint_sha256=actual,
        active_checkpoint_path=str(source),
        active_checkpoint_sha256=actual,
        checkpoint_count=count,
        last_checkpoint_descriptor=str(descriptor_path),
    )


def stop_longrun_v1(config: LongrunConfigV1, *, reason: str) -> dict[str, Any]:
    """Stop a run without deleting checkpoints; it can only be resumed explicitly."""
    _text(reason, "stop reason")
    return _update_state_v1(config, status="STOPPED", stage="stopped", stop_reason=reason)


def record_native_regression_v1(
    config: LongrunConfigV1,
    *,
    block_id: str,
    candidate_score: float,
    native_score: float,
    reason: str = "candidate regressed against native baseline",
) -> dict[str, Any]:
    """Count a native regression and safety-stop after the sealed threshold.

    A regression is recorded even when the caller is only doing a screen.  The
    second consecutive/accumulated regression does not get papered over by
    continuing the overnight process: the state becomes ``STOPPED`` and the
    latest checkpoint remains available for explicit rollback.
    """
    _text(block_id, "regression block_id")
    candidate = _finite_unit(candidate_score, "candidate_score")
    native = _finite_unit(native_score, "native_score")
    _text(reason, "regression reason")
    if candidate >= native:
        raise LongrunError("record_native_regression_v1 requires candidate_score < native_score")
    state = _verify_state_v1(config)
    count = int(state.get("regression_count", 0)) + 1
    if count >= config.stop_after_regressions:
        return _update_state_v1(
            config,
            status="STOPPED",
            stage="safety_stop",
            regression_count=count,
            stop_reason=f"{reason}: {count} native regressions (last block {block_id})",
            last_regression={
                "block_id": block_id,
                "candidate_score": candidate,
                "native_score": native,
            },
        )
    return _update_state_v1(
        config,
        status=state["status"],
        stage="regression_watch",
        regression_count=count,
        last_regression={
            "block_id": block_id,
            "candidate_score": candidate,
            "native_score": native,
        },
    )


def rollback_longrun_v1(config: LongrunConfigV1, *, checkpoint_path: Path | str | None = None) -> dict[str, Any]:
    """Select a previously published checkpoint as the active rollback target."""
    state = _verify_state_v1(config)
    target = Path(checkpoint_path).resolve() if checkpoint_path is not None else Path(state.get("latest_checkpoint_path", "")).resolve()
    if not target.is_file():
        raise LongrunError(f"rollback checkpoint does not exist: {target}")
    actual = _sha256_file(target)
    known = {
        str(state.get("latest_checkpoint_path")),
        str(state.get("active_checkpoint_path")),
    }
    if str(target) not in known and actual not in {
        str(state.get("latest_checkpoint_sha256")),
        str(state.get("active_checkpoint_sha256")),
    }:
        raise LongrunError("rollback target was not published by this longrun")
    return _update_state_v1(
        config,
        status="ROLLED_BACK",
        stage="rollback",
        active_checkpoint_path=str(target),
        active_checkpoint_sha256=actual,
        rollback_count=int(state.get("rollback_count", 0)) + 1,
    )


def launch_longrun_v1(
    config: LongrunConfigV1,
    *,
    execute: bool = False,
    runner: Callable[[LongrunConfigV1], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Return a dry-run descriptor or invoke an explicitly supplied runner.

    The repository's CABT/training runners are deliberately not imported here.
    A real caller must provide an adapter and a persisted, ready gate.  This
    keeps accidental ``--execute`` calls fail-closed while still making the
    contract testable with a tiny in-process callback.
    """
    state = _verify_state_v1(config)
    if not execute:
        return {
            "schema_version": LONGRUN_SCHEMA_V1,
            "status": state["status"],
            "launch_allowed": False,
            "execute": False,
            "config_sha256": state["config_sha256"],
            "reason": "dry-run does not start training, CABT, or submission",
        }
    _require_ready_v1(state)
    if runner is None:
        raise LongrunError("LONGRUN_READY is satisfied but no explicit research runner was supplied")
    _update_state_v1(config, status="RUNNING", stage="running", execute=True)
    try:
        result = runner(config)
    except BaseException as exc:
        _update_state_v1(config, status="FAILED", stage="failed", failure=str(exc))
        raise
    updated = _verify_state_v1(config)
    return {
        "schema_version": LONGRUN_SCHEMA_V1,
        "status": updated["status"],
        "launch_allowed": True,
        "execute": True,
        "config_sha256": updated["config_sha256"],
        "runner_result": dict(result or {}),
    }


def resume_longrun_v1(
    config: LongrunConfigV1,
    *,
    execute: bool = False,
    runner: Callable[[LongrunConfigV1], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Reload a sealed lineage and optionally request a gated continuation.

    This is a named resume entry point rather than a synonym for blindly
    re-initializing a run: it always re-hashes the manifest and config first.
    ``execute=False`` is still a no-op descriptor.
    """
    _verify_state_v1(config)
    result = launch_longrun_v1(config, execute=execute, runner=runner)
    result["resumed"] = True
    return result


__all__ = [
    "BlockEvidenceV1",
    "GateEvidenceV1",
    "GATE_SCHEMA_V1",
    "CHECKPOINT_SCHEMA_V1",
    "LONGRUN_SCHEMA_V1",
    "LongrunConfigV1",
    "LongrunError",
    "NativeBaselineV1",
    "checkpoint_longrun_v1",
    "config_sha256_v1",
    "evaluate_longrun_gate_v1",
    "initialize_longrun_v1",
    "launch_longrun_v1",
    "load_longrun_state_v1",
    "record_gate_v1",
    "record_native_regression_v1",
    "resume_longrun_v1",
    "rollback_longrun_v1",
    "stop_longrun_v1",
]
