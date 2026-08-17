"""Research-only BestKnown coordinator for self-owned cg candidates.

The existing CEM and alternating runtimes each own one bounded experiment.
This module supplies the missing boundary between them: a caller presents an
explicitly fresh meta batch and an injected candidate/evaluation runner, and
the coordinator advances only a positive, fault-free, seat-safe result.  It
never starts CABT itself, changes Champion, trains, packages, submits, or
reuses a batch after the caller has marked its references consumed.

The default phase is ``DECK_FIXED_LONG`` (policy improvement on a frozen
deck), followed by ``POLICY_FIXED_SHORT`` (deck improvement on the frozen
policy), then back to policy.  The phase names are inherited from the
hash-bound alternating runtime and therefore describe the dimension held
fixed, not the dimension being optimized.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from .cg_alternating_runtime_v1 import (
    AUTHORITY_FALSE_V1,
    CG_DECK_FIXED_LONG_V1,
    CG_POLICY_FIXED_SHORT_V1,
    CgPackageSpecV1,
)
from .opponent_pool_v1 import load_opponent_pool_v1
from ..observability.cabt_trace import canonical_deck_sha256


FRESH_META_SCHEMA_V1 = "meta-specialist-cg-fresh-meta-batch-v1"
BESTKNOWN_LOOP_SCHEMA_V1 = "meta-specialist-cg-bestknown-loop-v1"
MAX_CYCLES_V1 = 8
SEAT_GAP_LIMIT_V1 = 0.05
_SHA_CHARS = frozenset("0123456789abcdef")


class CgBestKnownLoopError(ValueError):
    """Raised when freshness, identity, or promotion-safe loop state fails."""


def _sha256(path: Path | str) -> str:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise CgBestKnownLoopError(f"regular file required: {source}")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _sha_value(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise CgBestKnownLoopError(f"{name} must be a lowercase SHA-256")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise CgBestKnownLoopError(f"{name} must be a non-empty string")
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CgBestKnownLoopError("loop payload is not canonical JSON") from exc


def _write_json_no_clobber(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(payload) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CgBestKnownLoopError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise CgBestKnownLoopError(f"JSON root must be an object: {path}")
    return value


def _pool_rows(pool_manifest_path: Path) -> dict[str, Mapping[str, Any]]:
    # The pool loader accepts either a bare list or an ``opponents`` wrapper;
    # read the raw file separately because smoke/source/canonical fields are
    # provenance gates, not runtime instance fields.
    try:
        decoded = json.loads(pool_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CgBestKnownLoopError(f"cannot read pool manifest: {pool_manifest_path}") from exc
    rows: object = decoded
    if isinstance(decoded, Mapping):
        rows = decoded.get("opponents", decoded)
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows:
        raise CgBestKnownLoopError("pool manifest must contain a non-empty list")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or type(row.get("id")) is not str:
            raise CgBestKnownLoopError("pool rows must contain string id")
        opponent_id = str(row["id"])
        if opponent_id in result:
            raise CgBestKnownLoopError(f"duplicate pool id: {opponent_id}")
        result[opponent_id] = row
    return result


@dataclass(frozen=True, slots=True)
class FreshMetaBatchV1:
    """A hash-bound, explicitly unused evaluation batch."""

    schema_version: str
    batch_id: str
    source_epoch: str
    seed_namespace: str
    seed_plan_sha256: str
    manifest_path: str
    manifest_sha256: str
    pool_manifest_path: str
    pool_manifest_sha256: str
    reference_ids: tuple[str, ...]
    reference_identity: Mapping[str, Mapping[str, str]]
    freshness_basis: str
    authority: Mapping[str, bool]
    research_only: bool

    def __post_init__(self) -> None:
        if self.schema_version != FRESH_META_SCHEMA_V1:
            raise CgBestKnownLoopError("fresh-meta schema mismatch")
        _text(self.batch_id, "batch_id")
        _text(self.source_epoch, "source_epoch")
        _text(self.seed_namespace, "seed_namespace")
        _sha_value(self.seed_plan_sha256, "seed_plan_sha256")
        _sha_value(self.manifest_sha256, "manifest_sha256")
        _sha_value(self.pool_manifest_sha256, "pool_manifest_sha256")
        _text(self.freshness_basis, "freshness_basis")
        if not self.reference_ids or len(self.reference_ids) != len(set(self.reference_ids)):
            raise CgBestKnownLoopError("fresh-meta references must be non-empty and unique")
        if tuple(sorted(self.reference_ids)) != self.reference_ids:
            raise CgBestKnownLoopError("fresh-meta references must be canonical sorted order")
        if set(self.reference_identity) != set(self.reference_ids):
            raise CgBestKnownLoopError("fresh-meta identity keys do not match references")
        if dict(self.authority) != AUTHORITY_FALSE_V1 or self.research_only is not True:
            raise CgBestKnownLoopError("fresh-meta batch grants forbidden authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "source_epoch": self.source_epoch,
            "seed_namespace": self.seed_namespace,
            "seed_plan_sha256": self.seed_plan_sha256,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "pool_manifest_path": self.pool_manifest_path,
            "pool_manifest_sha256": self.pool_manifest_sha256,
            "reference_ids": list(self.reference_ids),
            "references": {
                key: dict(self.reference_identity[key]) for key in sorted(self.reference_identity)
            },
            "freshness_basis": self.freshness_basis,
            "authority": dict(self.authority),
            "research_only": True,
        }


def build_fresh_meta_batch_v1(
    *,
    manifest_path: Path | str,
    pool_manifest_path: Path | str,
    consumed_ids: Sequence[str] = (),
    consumed_seed_namespaces: Sequence[str] = (),
) -> FreshMetaBatchV1:
    """Load and verify one fresh batch without changing any source manifest.

    The caller must provide an evidence-backed ``references`` row for every
    id.  ``fresh`` and ``unused_before_run`` are deliberately separate flags:
    both must be true, and a 64-character evidence digest is required.  This
    prevents a copied pool row or a raw-byte deck hash from masquerading as a
    new independent meta source.
    """

    manifest_file = Path(manifest_path).resolve()
    pool_file = Path(pool_manifest_path).resolve()
    payload = _read_json(manifest_file)
    if payload.get("schema_version") != FRESH_META_SCHEMA_V1:
        raise CgBestKnownLoopError("fresh-meta schema mismatch")
    if payload.get("authority") != AUTHORITY_FALSE_V1 or payload.get("research_only") is not True:
        raise CgBestKnownLoopError("fresh-meta manifest grants forbidden authority")
    batch_id = _text(payload.get("batch_id"), "batch_id")
    source_epoch = _text(payload.get("source_epoch"), "source_epoch")
    seed_namespace = _text(payload.get("seed_namespace"), "seed_namespace")
    seed_plan_sha256 = _sha_value(payload.get("seed_plan_sha256"), "seed_plan_sha256")
    freshness_basis = _text(payload.get("freshness_basis"), "freshness_basis")
    if payload.get("pool_manifest_sha256") != _sha256(pool_file):
        raise CgBestKnownLoopError("pool manifest SHA mismatch")

    raw_ids = payload.get("reference_ids")
    if not isinstance(raw_ids, list) or any(type(value) is not str for value in raw_ids):
        raise CgBestKnownLoopError("reference_ids must be a string list")
    reference_ids = tuple(str(value) for value in raw_ids)
    if not reference_ids or len(reference_ids) != len(set(reference_ids)):
        raise CgBestKnownLoopError("fresh-meta references must be non-empty and unique")
    if tuple(sorted(reference_ids)) != reference_ids:
        raise CgBestKnownLoopError("fresh-meta references must be canonical sorted order")
    consumed = tuple(str(value) for value in consumed_ids)
    if len(consumed) != len(set(consumed)):
        raise CgBestKnownLoopError("consumed_ids must be unique")
    overlap = sorted(set(reference_ids).intersection(consumed))
    if overlap:
        raise CgBestKnownLoopError(f"fresh-meta references already consumed: {overlap}")
    consumed_seeds = tuple(str(value) for value in consumed_seed_namespaces)
    if len(consumed_seeds) != len(set(consumed_seeds)):
        raise CgBestKnownLoopError("consumed_seed_namespaces must be unique")
    if seed_namespace in consumed_seeds:
        raise CgBestKnownLoopError(f"fresh-meta seed namespace already consumed: {seed_namespace}")

    references = payload.get("references")
    if not isinstance(references, list):
        raise CgBestKnownLoopError("references must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in references:
        if not isinstance(row, Mapping) or type(row.get("id")) is not str:
            raise CgBestKnownLoopError("fresh-meta reference rows must contain string id")
        opponent_id = str(row["id"])
        if opponent_id in by_id:
            raise CgBestKnownLoopError(f"duplicate fresh-meta reference: {opponent_id}")
        by_id[opponent_id] = row
    if set(by_id) != set(reference_ids):
        raise CgBestKnownLoopError("reference rows do not match reference_ids")

    if pool_file.name != "pool_manifest.json":
        raise CgBestKnownLoopError("pool manifest must be named pool_manifest.json")
    pool = load_opponent_pool_v1(pool_file.parent)
    raw_pool = _pool_rows(pool_file)
    identities: dict[str, Mapping[str, str]] = {}
    for opponent_id in reference_ids:
        row = by_id[opponent_id]
        if row.get("fresh") is not True or row.get("unused_before_run") is not True:
            raise CgBestKnownLoopError(f"{opponent_id} is not proven fresh and unused")
        evidence_sha = _sha_value(row.get("freshness_evidence_sha256"), f"{opponent_id}.freshness_evidence_sha256")
        evidence_path_value = _text(row.get("freshness_evidence_path"), f"{opponent_id}.freshness_evidence_path")
        evidence_path = Path(evidence_path_value)
        if not evidence_path.is_absolute():
            evidence_path = manifest_file.parent / evidence_path
        evidence_path = evidence_path.resolve()
        if _sha256(evidence_path) != evidence_sha:
            raise CgBestKnownLoopError(f"freshness evidence SHA mismatch: {opponent_id}")
        pool_row = raw_pool.get(opponent_id)
        instance = pool.get(opponent_id)
        if pool_row is None or instance is None:
            raise CgBestKnownLoopError(f"fresh-meta reference is absent from pool: {opponent_id}")
        if pool_row.get("smoke_ok") is not True:
            raise CgBestKnownLoopError(f"{opponent_id} is not smoke-qualified")
        if pool_row.get("usage_boundary") != "local_eval_only":
            raise CgBestKnownLoopError(f"{opponent_id} crosses the evaluation-only boundary")
        source = _text(pool_row.get("source"), f"{opponent_id}.source")
        deck_path = Path(instance.deck_csv_path)
        try:
            card_ids = [int(token) for token in deck_path.read_text(encoding="utf-8").split()]
        except (OSError, ValueError) as exc:
            raise CgBestKnownLoopError(f"cannot parse deck for {opponent_id}") from exc
        canonical = canonical_deck_sha256(card_ids)
        if pool_row.get("canonical_deck_hash") != canonical:
            raise CgBestKnownLoopError(f"pool canonical deck hash is inconsistent: {opponent_id}")
        if row.get("canonical_deck_hash") != canonical:
            raise CgBestKnownLoopError(f"canonical deck hash mismatch: {opponent_id}")
        if row.get("policy_sha256") != instance.policy_hash:
            raise CgBestKnownLoopError(f"policy identity mismatch: {opponent_id}")
        if row.get("source") != source:
            raise CgBestKnownLoopError(f"source identity mismatch: {opponent_id}")
        identities[opponent_id] = {
            "source": source,
            "policy_sha256": instance.policy_hash,
            "canonical_deck_hash": canonical,
            "freshness_evidence_sha256": evidence_sha,
            "freshness_evidence_path": str(evidence_path),
        }

    return FreshMetaBatchV1(
        schema_version=FRESH_META_SCHEMA_V1,
        batch_id=batch_id,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        seed_plan_sha256=seed_plan_sha256,
        manifest_path=str(manifest_file),
        manifest_sha256=_sha256(manifest_file),
        pool_manifest_path=str(pool_file),
        pool_manifest_sha256=_sha256(pool_file),
        reference_ids=reference_ids,
        reference_identity=identities,
        freshness_basis=freshness_basis,
        authority=dict(AUTHORITY_FALSE_V1),
        research_only=True,
    )


def _validate_candidate_dimension(
    *, phase: str, incumbent: CgPackageSpecV1, candidate: CgPackageSpecV1
) -> None:
    candidate.verify_sources()
    incumbent.verify_sources()
    if candidate.candidate_id == incumbent.candidate_id:
        raise CgBestKnownLoopError("candidate and incumbent IDs must differ")
    if phase == CG_DECK_FIXED_LONG_V1:
        if candidate.deck_sha256 != incumbent.deck_sha256:
            raise CgBestKnownLoopError("deck-fixed policy phase changed the deck")
        if candidate.policy_sha256 == incumbent.policy_sha256:
            raise CgBestKnownLoopError("deck-fixed policy phase did not change policy")
    elif phase == CG_POLICY_FIXED_SHORT_V1:
        if candidate.policy_sha256 != incumbent.policy_sha256:
            raise CgBestKnownLoopError("policy-fixed deck phase changed the policy")
        if candidate.deck_sha256 == incumbent.deck_sha256:
            raise CgBestKnownLoopError("policy-fixed deck phase did not change deck")
    else:
        raise CgBestKnownLoopError(f"unsupported BestKnown phase: {phase}")


def _metric(summary: Mapping[str, object], name: str, *, default: float | None = None) -> float | None:
    value = summary.get(name, default)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if not math.isfinite(value):
        return default
    return value


def _next_phase(phase: str) -> str:
    if phase == CG_DECK_FIXED_LONG_V1:
        return CG_POLICY_FIXED_SHORT_V1
    if phase == CG_POLICY_FIXED_SHORT_V1:
        return CG_DECK_FIXED_LONG_V1
    raise CgBestKnownLoopError(f"unsupported BestKnown phase: {phase}")


def run_bestknown_loop_v1(
    *,
    incumbent: CgPackageSpecV1,
    fresh_meta: FreshMetaBatchV1,
    candidate_runner: Callable[..., Mapping[str, object]],
    output_root: Path | str,
    max_cycles: int,
    execute: bool = True,
    start_phase: str = CG_DECK_FIXED_LONG_V1,
) -> dict[str, object]:
    """Advance a bounded policy→deck→policy BestKnown research loop.

    ``candidate_runner`` owns candidate generation and CABT invocation.  It
    receives ``phase``, ``incumbent``, ``reference_ids``, ``fresh_meta``,
    ``cycle_index``, ``output_root`` and ``execute`` and must return a mapping
    with a ``CgPackageSpecV1`` under ``candidate`` and an evaluation ``summary``.
    A candidate becomes the new research parent only when the summary reports
    ``POSITIVE_CONTINUE`` with zero faults, positive delta, and a seat gap no
    larger than ``SEAT_GAP_LIMIT_V1``.
    """

    if type(incumbent) is not CgPackageSpecV1:
        raise CgBestKnownLoopError("incumbent must be CgPackageSpecV1")
    if type(fresh_meta) is not FreshMetaBatchV1:
        raise CgBestKnownLoopError("fresh_meta must be FreshMetaBatchV1")
    if not callable(candidate_runner):
        raise CgBestKnownLoopError("candidate_runner must be callable")
    if type(max_cycles) is not int or not 0 < max_cycles <= MAX_CYCLES_V1:
        raise CgBestKnownLoopError(f"max_cycles must be in [1,{MAX_CYCLES_V1}]")
    if type(execute) is not bool:
        raise CgBestKnownLoopError("execute must be bool")
    if start_phase not in {CG_DECK_FIXED_LONG_V1, CG_POLICY_FIXED_SHORT_V1}:
        raise CgBestKnownLoopError("unsupported BestKnown start phase")
    incumbent.verify_sources()
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"refusing to overwrite BestKnown loop root: {root}")
    root.mkdir(parents=True, exist_ok=False)
    checkpoint_root = root / "checkpoints"
    checkpoint_root.mkdir()

    current = incumbent
    phase = start_phase
    checkpoints: list[str] = []
    last_decision: str | None = None
    terminal_status = "DRY_RUN" if not execute else "STOP"

    for cycle_index in range(max_cycles):
        stage_root = root / f"cycle-{cycle_index:04d}"
        result = candidate_runner(
            phase=phase,
            incumbent=current,
            reference_ids=fresh_meta.reference_ids,
            fresh_meta=fresh_meta,
            cycle_index=cycle_index,
            output_root=stage_root,
            execute=execute,
        )
        if not isinstance(result, Mapping):
            raise CgBestKnownLoopError("candidate_runner must return a mapping")
        decision_result = result.get("summary")
        candidate_value = result.get("candidate")
        if not execute and result.get("status") == "DRY_RUN" and candidate_value is None:
            checkpoint = {
                "schema_version": BESTKNOWN_LOOP_SCHEMA_V1,
                "status": "DRY_RUN",
                "cycle_index": cycle_index,
                "phase": phase,
                "decision": "DRY_RUN",
                "incumbent_identity": current.to_dict(),
                "candidate_identity": None,
                "summary": None,
            "fresh_meta_batch_id": fresh_meta.batch_id,
            "seed_namespace": fresh_meta.seed_namespace,
            "seed_plan_sha256": fresh_meta.seed_plan_sha256,
            "reference_ids": list(fresh_meta.reference_ids),
                "authority": dict(AUTHORITY_FALSE_V1),
                "research_only": True,
            }
            path = checkpoint_root / f"checkpoint-{cycle_index:04d}.json"
            _write_json_no_clobber(path, checkpoint)
            checkpoints.append(str(path))
            last_decision = "DRY_RUN"
            terminal_status = "DRY_RUN"
            break
        if type(candidate_value) is not CgPackageSpecV1:
            raise CgBestKnownLoopError("candidate_runner result must contain CgPackageSpecV1")
        if not isinstance(decision_result, Mapping):
            raise CgBestKnownLoopError("candidate_runner result must contain summary mapping")
        summary = dict(decision_result)
        decision = summary.get("decision")
        if type(decision) is not str:
            raise CgBestKnownLoopError("summary decision must be a string")
        if decision not in {"POSITIVE_CONTINUE", "NOT_PROMOTABLE", "INVALID_FAULT", "DRY_RUN"}:
            raise CgBestKnownLoopError(f"unsupported summary decision: {decision}")
        _validate_candidate_dimension(phase=phase, incumbent=current, candidate=candidate_value)
        _canonical_json(summary)

        faults_value = summary.get("faults", 0)
        if isinstance(faults_value, bool) or not isinstance(faults_value, int) or faults_value < 0:
            raise CgBestKnownLoopError("summary faults must be a non-negative integer")
        delta = _metric(summary, "candidate_delta")
        if delta is None:
            points = _metric(summary, "candidate_delta_points")
            delta = points / 100.0 if points is not None else None
        seat_gap = _metric(summary, "candidate_seat_gap")
        positive = (
            decision == "POSITIVE_CONTINUE"
            and faults_value == 0
            and delta is not None
            and delta > 0.0
            and seat_gap is not None
            and 0.0 <= seat_gap <= SEAT_GAP_LIMIT_V1
        )
        if decision == "DRY_RUN":
            status = "DRY_RUN"
        elif faults_value:
            status = "STOP_FAULT"
        elif decision == "POSITIVE_CONTINUE" and not positive:
            status = "STOP_INVALID"
        elif positive:
            status = "RUNNING" if cycle_index + 1 < max_cycles else "BOUNDARY"
        else:
            status = "STOP_NOT_PROMOTABLE"

        promoted = candidate_value if positive else current
        checkpoint = {
            "schema_version": BESTKNOWN_LOOP_SCHEMA_V1,
            "status": status,
            "cycle_index": cycle_index,
            "phase": phase,
            "decision": decision,
            "incumbent_identity": current.to_dict(),
            "candidate_identity": candidate_value.to_dict(),
            "promoted_identity": promoted.to_dict(),
            "summary": summary,
            "fresh_meta_batch_id": fresh_meta.batch_id,
            "source_epoch": fresh_meta.source_epoch,
            "seed_namespace": fresh_meta.seed_namespace,
            "seed_plan_sha256": fresh_meta.seed_plan_sha256,
            "reference_ids": list(fresh_meta.reference_ids),
            "next_phase": _next_phase(phase) if positive else None,
            "authority": dict(AUTHORITY_FALSE_V1),
            "research_only": True,
        }
        path = checkpoint_root / f"checkpoint-{cycle_index:04d}.json"
        _write_json_no_clobber(path, checkpoint)
        checkpoints.append(str(path))
        last_decision = decision
        terminal_status = status
        if not positive:
            break
        current = candidate_value
        phase = _next_phase(phase)

    return {
        "schema_version": BESTKNOWN_LOOP_SCHEMA_V1,
        "status": terminal_status,
        "bestknown_candidate_id": current.candidate_id,
        "bestknown_identity": current.to_dict(),
        "last_decision": last_decision,
        "cycles_completed": len(checkpoints),
        "checkpoints": checkpoints,
        "fresh_meta_batch_id": fresh_meta.batch_id,
        "source_epoch": fresh_meta.source_epoch,
        "seed_namespace": fresh_meta.seed_namespace,
        "seed_plan_sha256": fresh_meta.seed_plan_sha256,
        "consumed_reference_ids": list(fresh_meta.reference_ids) if execute else [],
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }


__all__ = [
    "BESTKNOWN_LOOP_SCHEMA_V1",
    "CgBestKnownLoopError",
    "FreshMetaBatchV1",
    "FRESH_META_SCHEMA_V1",
    "MAX_CYCLES_V1",
    "SEAT_GAP_LIMIT_V1",
    "build_fresh_meta_batch_v1",
    "run_bestknown_loop_v1",
]
