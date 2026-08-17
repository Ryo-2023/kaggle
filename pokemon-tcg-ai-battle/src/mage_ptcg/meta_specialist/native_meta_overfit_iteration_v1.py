"""Strict, research-only hard-negative iteration adapter.

This module binds the already verified dynamic curriculum, the strict common24
META_TRAIN outcome adapter, a Task 1 public advantage table, and an immutable
native baseline into one replayable iteration manifest.  It computes bounded
opponent weights only; it never launches an evaluator, trainer, subprocess, or
submission flow.  META_DEV and META_FINAL are represented for auditability but
always have zero exposure, weight, and quota in the produced manifest.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .common24_curriculum_outcome_adapter_v1 import (
    AUTHORITY_FALSE_V1 as OUTCOME_AUTHORITY_FALSE_V1,
    Common24CurriculumOutcomeAdapterError,
    verify_common24_curriculum_outcome_adapter_v1,
)
from .dynamic_meta_train_curriculum_v1 import (
    DynamicMetaTrainCurriculumError,
    verify_dynamic_curriculum_manifest_v1,
)
from .meta_distribution_v1 import (
    MetaDistributionError,
    load_meta_distribution_manifest_v1,
)
from .native_public_advantage_v1 import (
    NativePublicAdvantageError,
    PublicAdvantageTableV1,
)


ITERATION_SCHEMA_V1 = "meta-specialist-native-meta-overfit-iteration-v1"
ITERATION_PURPOSE_V1 = "NATIVE_PRESERVING_META_OVERFIT_RESEARCH_ONLY"
AUTHORITY_FALSE_V1 = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}
_SPLITS = ("META_TRAIN", "META_DEV", "META_FINAL")
_SHA_CHARS = frozenset("0123456789abcdef")
_WEIGHT_COMPONENTS = {
    "loss_hard_negative": 0.40,
    "seat_imbalance_correction": 0.20,
    "under_exposure_correction": 0.15,
    "family_diversity_floor": 0.15,
    "reliability": 0.10,
}
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "iteration",
        "seed",
        "derived_iteration_seed_sha256",
        "sources",
        "curriculum_identity",
        "outcome_adapter_identity",
        "public_advantage_identity",
        "native_baseline",
        "candidate_identity",
        "weighting_parameters",
        "opponent_statistics",
        "family_statistics",
        "exposure_by_split",
        "hard_negative_weights_sha256",
        "gate_status",
        "ready_for_evaluation",
        "authority",
        "iteration_sha256",
    }
)
_VERIFIED_CURRICULUM_TOKEN = object()


class _VerifiedCurriculum(dict[str, Any]):
    """Opaque in-memory proof that curriculum permissions were source-checked.

    The permission map is deliberately not a serializable trust signal.  Only
    this object, constructed by ``_verified_curriculum`` after reopening and
    verifying the bound meta-distribution, may reach the weighting function.
    """

    __slots__ = ("_meta_source_sha256", "_permission_digest", "_proof_token")

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        meta_source_sha256: str,
        permission_digest: str,
        _proof_token: object,
    ) -> None:
        if _proof_token is not _VERIFIED_CURRICULUM_TOKEN:
            raise TypeError("verified curriculum proof token is invalid")
        super().__init__(value)
        self._meta_source_sha256 = meta_source_sha256
        self._permission_digest = permission_digest
        self._proof_token = _proof_token


class NativeMetaOverfitIterationError(ValueError):
    """Raised when an iteration crosses a source, split, or authority boundary."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NativeMetaOverfitIterationError("value is not canonical JSON") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NativeMetaOverfitIterationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    raise NativeMetaOverfitIterationError(f"non-finite JSON constant: {token}")


def _strict_json(path: Path, *, canonical: bool = True) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except NativeMetaOverfitIterationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeMetaOverfitIterationError(f"invalid JSON source: {path}") from exc
    if type(value) is not dict:
        raise NativeMetaOverfitIterationError(f"JSON source must be an object: {path}")
    if canonical and raw != _canonical_bytes(value):
        raise NativeMetaOverfitIterationError(f"JSON source is not canonical: {path}")
    return value


def _sha_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise NativeMetaOverfitIterationError(f"cannot hash source: {path}") from exc


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise NativeMetaOverfitIterationError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _finite(value: object, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise NativeMetaOverfitIterationError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise NativeMetaOverfitIterationError(f"{name} must be finite")
    if minimum is not None and result < minimum - 1e-12:
        raise NativeMetaOverfitIterationError(f"{name} is below its lower bound")
    if maximum is not None and result > maximum + 1e-12:
        raise NativeMetaOverfitIterationError(f"{name} is above its upper bound")
    return result


def _semantic_sha(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _permission_digest(permission_by_id: Mapping[str, Mapping[str, object]]) -> str:
    canonical = {
        str(opponent_id): {
            "usage_boundary": permissions.get("usage_boundary"),
            "training_allowed": permissions.get("training_allowed"),
            "behavior_allowed": permissions.get("behavior_allowed"),
            "submission_allowed": permissions.get("submission_allowed"),
        }
        for opponent_id, permissions in sorted(permission_by_id.items())
    }
    return _semantic_sha("mage-ptcg:native-meta-overfit-permission-proof:v1", canonical)


def _inside(root: Path, value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise NativeMetaOverfitIterationError(f"{label} path is invalid")
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise NativeMetaOverfitIterationError(f"{label} escapes repo_root") from exc
    if not path.is_file():
        raise NativeMetaOverfitIterationError(f"{label} is not a file: {path}")
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError as exc:
        raise NativeMetaOverfitIterationError("source path escapes repo_root") from exc


def _source_binding(root: Path, path: Path, role: str) -> dict[str, str]:
    return {"path": _relative(root, path), "file_sha256": _sha_file(path), "role": role}


def _atomic_write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # ``os.replace`` is deliberately not used: a second writer could win
        # the check/replace race and clobber an already published artifact.
        # A hard-link create is an atomic, no-clobber destination claim when
        # the temporary and destination live in the same directory.
        os.link(temporary, path)
        temporary.unlink(missing_ok=True)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_published_if_owned(path: Path, raw: bytes) -> None:
    """Remove only an artifact whose bytes are exactly ours.

    A concurrent writer may publish the destination after our preflight
    check.  Never unlink such a winner merely because our verification failed.
    """
    try:
        if path.is_file() and path.read_bytes() == raw:
            path.unlink()
    except OSError:
        # Cleanup is best effort; preserving an unknown artifact is safer than
        # turning a verification error into a destructive race.
        return


def _authority_false(value: object, label: str) -> None:
    if type(value) is not dict:
        raise NativeMetaOverfitIterationError(f"{label} authority must be an object")
    if value != AUTHORITY_FALSE_V1:
        raise NativeMetaOverfitIterationError(f"{label} grants authority")


def _verified_curriculum(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = verify_dynamic_curriculum_manifest_v1(path, root)
    except (DynamicMetaTrainCurriculumError, OSError, ValueError) as exc:
        raise NativeMetaOverfitIterationError(f"dynamic curriculum verification failed: {path}") from exc
    if type(value) is not dict or value.get("schema_version") != "meta-specialist-dynamic-meta-train-curriculum-v1":
        raise NativeMetaOverfitIterationError("dynamic curriculum schema is invalid")
    _authority_false(value.get("authority"), "dynamic curriculum")
    if type(value.get("iteration")) is not int or value["iteration"] < 0:
        raise NativeMetaOverfitIterationError("dynamic curriculum iteration is invalid")
    if type(value.get("seed")) is not str or not value["seed"]:
        raise NativeMetaOverfitIterationError("dynamic curriculum seed is invalid")
    if type(value.get("quota")) is not int or value["quota"] <= 0:
        raise NativeMetaOverfitIterationError("dynamic curriculum quota is invalid")
    parameters = value.get("parameters")
    if type(parameters) is not dict:
        raise NativeMetaOverfitIterationError("dynamic curriculum parameters are missing")
    for name in ("max_opponent_weight", "max_family_weight"):
        _finite(parameters.get(name), f"curriculum.{name}", minimum=0.0, maximum=1.0)
    if type(parameters.get("min_family_quota")) is not int or parameters["min_family_quota"] <= 0:
        raise NativeMetaOverfitIterationError("curriculum.min_family_quota is invalid")
    entries = value.get("entries")
    if type(entries) is not list or not entries:
        raise NativeMetaOverfitIterationError("dynamic curriculum entries are missing")
    # Re-open the bound meta distribution rather than trusting permission
    # booleans copied into a curriculum entry.  A curriculum can only feed
    # this adapter when its source itself is research-only and its META_TRAIN
    # rows explicitly permit training-local behavior.
    sources = value.get("sources")
    if type(sources) is not list:
        raise NativeMetaOverfitIterationError("dynamic curriculum sources are missing")
    meta_sources = [
        source for source in sources
        if type(source) is dict and source.get("role") == "meta_distribution_manifest"
    ]
    if len(meta_sources) != 1:
        raise NativeMetaOverfitIterationError("dynamic curriculum meta distribution source is ambiguous")
    meta_source = meta_sources[0]
    meta_path = _inside(root, meta_source.get("path", ""), "curriculum meta distribution source")
    if _sha_file(meta_path) != meta_source.get("file_sha256"):
        raise NativeMetaOverfitIterationError("dynamic curriculum meta distribution source SHA mismatch")
    try:
        meta = load_meta_distribution_manifest_v1(meta_path, verify_sources=True)
    except (MetaDistributionError, OSError, ValueError) as exc:
        raise NativeMetaOverfitIterationError("dynamic curriculum meta distribution verification failed") from exc
    if (
        meta.research_only is not True
        or meta.training_authority is not False
        or meta.promotion_authority is not False
        or meta.submission_authority is not False
    ):
        raise NativeMetaOverfitIterationError("dynamic curriculum meta distribution grants authority")
    meta_rows = {row.opponent_id: row for row in meta.rows}
    seen: set[str] = set()
    train_family_counts: dict[str, int] = {}
    train_quota = 0
    for entry in entries:
        if type(entry) is not dict:
            raise NativeMetaOverfitIterationError("curriculum entry is invalid")
        opponent_id = entry.get("opponent_id")
        if type(opponent_id) is not str or not opponent_id or opponent_id in seen:
            raise NativeMetaOverfitIterationError("curriculum opponent ids are invalid or duplicated")
        seen.add(opponent_id)
        split = entry.get("split")
        if split not in _SPLITS:
            raise NativeMetaOverfitIterationError("curriculum entry split is invalid")
        weight = _finite(entry.get("weight"), f"curriculum[{opponent_id}].weight", minimum=0.0, maximum=1.0)
        quota = entry.get("quota")
        if type(quota) is not int or quota < 0:
            raise NativeMetaOverfitIterationError("curriculum entry quota is invalid")
        exposure = entry.get("training_exposure_allowed")
        behavior = entry.get("teacher_behavior_allowed")
        if type(exposure) is not bool or type(behavior) is not bool:
            raise NativeMetaOverfitIterationError("curriculum entry permission flags are invalid")
        source_row = meta_rows.get(opponent_id)
        if source_row is None:
            raise NativeMetaOverfitIterationError("curriculum entry is absent from meta distribution")
        if split != "META_TRAIN":
            if weight != 0.0 or quota != 0 or exposure or behavior:
                raise NativeMetaOverfitIterationError("held-out curriculum entry has nonzero exposure")
        else:
            if exposure is not True or behavior is not True:
                raise NativeMetaOverfitIterationError("META_TRAIN curriculum entry lacks exposure/behavior permission")
            if (
                source_row.usage_boundary not in {"training_local", "training_local_and_eval"}
                or source_row.training_allowed is not True
                or source_row.behavior_allowed is not True
                or source_row.submission_allowed is not False
            ):
                raise NativeMetaOverfitIterationError(
                    "META_TRAIN source row is not training-local and behavior-authorized"
                )
            train_family_counts[str(entry.get("family"))] = train_family_counts.get(str(entry.get("family")), 0) + 1
            train_quota += quota
    if train_quota != value["quota"]:
        raise NativeMetaOverfitIterationError("curriculum train quota does not close")
    if not train_family_counts:
        raise NativeMetaOverfitIterationError("curriculum has no META_TRAIN family")
    # Keep the verified source permission map in memory for the weighting
    # stage.  It is deliberately not serialized into the source manifest.
    value = dict(value)
    permission_by_id = {
        opponent_id: {
            "usage_boundary": row.usage_boundary,
            "training_allowed": row.training_allowed,
            "behavior_allowed": row.behavior_allowed,
            "submission_allowed": row.submission_allowed,
        }
        for opponent_id, row in meta_rows.items()
    }
    value["_verified_permission_by_id"] = permission_by_id
    return _VerifiedCurriculum(
        value,
        meta_source_sha256=_sha_file(meta_path),
        permission_digest=_permission_digest(permission_by_id),
        _proof_token=_VERIFIED_CURRICULUM_TOKEN,
    )


def _verified_adapter(path: Path, root: Path) -> dict[str, Any]:
    try:
        value = verify_common24_curriculum_outcome_adapter_v1(path, root)
    except (Common24CurriculumOutcomeAdapterError, OSError, ValueError) as exc:
        raise NativeMetaOverfitIterationError(f"common24 outcome adapter verification failed: {path}") from exc
    if type(value) is not dict or value.get("schema_version") != "meta-specialist-common24-curriculum-outcome-adapter-v1":
        raise NativeMetaOverfitIterationError("outcome adapter schema is invalid")
    _authority_false(value.get("authority"), "outcome adapter")
    closure = value.get("execution_closure")
    if type(closure) is not dict:
        raise NativeMetaOverfitIterationError("outcome adapter execution closure is missing")
    _sha(closure.get("protocol_sha256"), "outcome adapter protocol_sha256")
    _sha(closure.get("execution_closure_sha256"), "outcome adapter execution_closure_sha256")
    records = value.get("records")
    if type(records) is not list or not records:
        raise NativeMetaOverfitIterationError("outcome adapter records are missing")
    game_ids: set[str] = set()
    for record in records:
        if type(record) is not dict:
            raise NativeMetaOverfitIterationError("outcome adapter record is invalid")
        if record.get("split") != "META_TRAIN":
            raise NativeMetaOverfitIterationError("held-out outcome record appeared in iteration ledger")
        game_id = record.get("game_id")
        if type(game_id) is not str or not game_id or game_id in game_ids:
            raise NativeMetaOverfitIterationError("outcome adapter game ids are invalid or duplicated")
        game_ids.add(game_id)
        if type(record.get("opponent_id")) is not str or not record["opponent_id"]:
            raise NativeMetaOverfitIterationError("outcome adapter opponent id is invalid")
        _finite(record.get("candidate_score"), "outcome candidate_score", minimum=0.0, maximum=1.0)
        if type(record.get("fault")) is not bool or record.get("seat") not in (0, 1):
            raise NativeMetaOverfitIterationError("outcome adapter fault/seat is invalid")
    return value


def _load_public_table(path: Path) -> PublicAdvantageTableV1:
    raw = _strict_json(path)
    try:
        table = PublicAdvantageTableV1.from_dict(raw)
    except (NativePublicAdvantageError, TypeError, ValueError) as exc:
        raise NativeMetaOverfitIterationError(f"public advantage table verification failed: {path}") from exc
    if not table.authority_false:
        raise NativeMetaOverfitIterationError("public advantage table grants authority")
    return table


def _load_identity(value: Mapping[str, object] | str | Path, root: Path, label: str) -> dict[str, object]:
    if isinstance(value, (str, Path)):
        path = _inside(root, value, f"{label} identity")
        payload = _strict_json(path)
        identity_path = _relative(root, path)
        identity_file_sha = _sha_file(path)
    elif isinstance(value, Mapping):
        payload = dict(value)
        identity_path = None
        identity_file_sha = None
        if payload.get("identity_path") is not None:
            identity_file = _inside(root, str(payload["identity_path"]), f"{label} identity")
            identity_path = _relative(root, identity_file)
            identity_file_sha = _sha_file(identity_file)
            if payload.get("identity_file_sha256") != identity_file_sha:
                raise NativeMetaOverfitIterationError(f"{label} identity file SHA mismatch")
    else:
        raise NativeMetaOverfitIterationError(f"{label} identity must be a path or object")
    candidate_id = payload.get("candidate_id") or payload.get("asset_id")
    if type(candidate_id) is not str or not candidate_id:
        raise NativeMetaOverfitIterationError(f"{label} candidate_id is invalid")
    policy_sha = _sha(payload.get("policy_sha256"), f"{label}.policy_sha256")
    deck_sha = _sha(payload.get("deck_sha256"), f"{label}.deck_sha256")
    result: dict[str, object] = {
        "candidate_id": candidate_id,
        "policy_sha256": policy_sha,
        "deck_sha256": deck_sha,
    }
    for field, hash_field in (("policy_path", "policy_sha256"), ("deck_path", "deck_sha256")):
        raw_path = payload.get(field)
        if raw_path is None:
            continue
        bound = _inside(root, str(raw_path), f"{label}.{field}")
        actual = _sha_file(bound)
        if actual != result[hash_field]:
            raise NativeMetaOverfitIterationError(f"{label}.{field} SHA mismatch")
        result[field] = _relative(root, bound)
    if payload.get("evaluator_sha256") is not None:
        result["evaluator_sha256"] = _sha(payload["evaluator_sha256"], f"{label}.evaluator_sha256")
    authority = payload.get("authority", AUTHORITY_FALSE_V1)
    _authority_false(authority, label)
    if payload.get("research_only") is not True:
        raise NativeMetaOverfitIterationError(f"{label} must be research_only")
    result["authority"] = dict(AUTHORITY_FALSE_V1)
    result["research_only"] = True
    if identity_path is not None:
        result["identity_path"] = identity_path
        result["identity_file_sha256"] = identity_file_sha
    return result


def _load_candidate_deck(path: Path | str, root: Path) -> dict[str, object]:
    manifest_path = _inside(root, path, "candidate deck manifest")
    payload = _strict_json(manifest_path)
    if payload.get("legal") is not True:
        raise NativeMetaOverfitIterationError("candidate deck is not marked legal")
    if payload.get("research_only") is not True:
        raise NativeMetaOverfitIterationError("candidate deck must be research_only")
    _authority_false(payload.get("authority"), "candidate deck")
    deck_path_raw = payload.get("deck_path")
    deck_sha = _sha(payload.get("deck_sha256"), "candidate deck SHA")
    if type(deck_path_raw) is not str:
        raise NativeMetaOverfitIterationError("candidate deck path is missing")
    deck_path = _inside(root, deck_path_raw, "candidate deck")
    if _sha_file(deck_path) != deck_sha:
        raise NativeMetaOverfitIterationError("candidate deck SHA mismatch")
    candidate_id = payload.get("candidate_id")
    if type(candidate_id) is not str or not candidate_id:
        raise NativeMetaOverfitIterationError("candidate deck candidate_id is invalid")
    return {
        "candidate_id": candidate_id,
        "deck_path": _relative(root, deck_path),
        "deck_sha256": deck_sha,
        "manifest_path": _relative(root, manifest_path),
        "manifest_file_sha256": _sha_file(manifest_path),
        "legal": True,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _project_distribution(
    raw: Mapping[str, float], *, total: float, lower: Mapping[str, float], upper: Mapping[str, float], label: str
) -> dict[str, float]:
    keys = tuple(sorted(raw))
    if not keys or set(keys) != set(lower) or set(keys) != set(upper):
        raise NativeMetaOverfitIterationError(f"{label} distribution keys are invalid")
    if any(lower[key] < -1e-12 or upper[key] < lower[key] - 1e-12 for key in keys):
        raise NativeMetaOverfitIterationError(f"{label} bounds are invalid")
    if sum(lower.values()) > total + 1e-10 or sum(upper.values()) < total - 1e-10:
        raise NativeMetaOverfitIterationError(f"{label} floor/cap is infeasible")
    remaining = set(keys)
    result: dict[str, float] = {}
    mass = float(total)
    while remaining:
        denominator = sum(max(0.0, float(raw[key])) for key in remaining)
        provisional = {
            key: (mass / len(remaining) if denominator <= 0.0 else mass * max(0.0, float(raw[key])) / denominator)
            for key in remaining
        }
        below = sorted(key for key in remaining if provisional[key] < lower[key] - 1e-12)
        above = sorted(key for key in remaining if provisional[key] > upper[key] + 1e-12)
        if below:
            key = below[0]
            result[key] = lower[key]
            remaining.remove(key)
            mass -= lower[key]
            continue
        if above:
            key = above[0]
            result[key] = upper[key]
            remaining.remove(key)
            mass -= upper[key]
            continue
        result.update(provisional)
        remaining.clear()
    correction = total - sum(result.values())
    if abs(correction) > 1e-10:
        for key in keys:
            candidate = result[key] + correction
            if lower[key] - 1e-10 <= candidate <= upper[key] + 1e-10:
                result[key] = candidate
                correction = 0.0
                break
    if abs(correction) > 1e-8 or abs(sum(result.values()) - total) > 1e-8:
        raise NativeMetaOverfitIterationError(f"{label} distribution does not close")
    return {key: float(result[key]) for key in keys}


def _derive_weighting(
    curriculum: _VerifiedCurriculum, adapter: Mapping[str, object]
) -> tuple[dict[str, object], dict[str, object], dict[str, object], str]:
    if (
        type(curriculum) is not _VerifiedCurriculum
        or curriculum._proof_token is not _VERIFIED_CURRICULUM_TOKEN
    ):
        raise NativeMetaOverfitIterationError(
            "curriculum permission source is not an opaque verified curriculum"
        )
    entries = curriculum["entries"]
    train_entries = [entry for entry in entries if entry["split"] == "META_TRAIN"]
    heldout_entries = [entry for entry in entries if entry["split"] != "META_TRAIN"]
    by_id = {entry["opponent_id"]: entry for entry in train_entries}
    if len(by_id) != len(train_entries):
        raise NativeMetaOverfitIterationError("curriculum META_TRAIN opponent ids are duplicated")
    permission_by_id = curriculum.get("_verified_permission_by_id")
    if type(permission_by_id) is not dict:
        raise NativeMetaOverfitIterationError("curriculum permission source is not verified")
    if _permission_digest(permission_by_id) != curriculum._permission_digest:
        raise NativeMetaOverfitIterationError("curriculum permission proof was mutated")
    for entry in train_entries:
        opponent_id = entry["opponent_id"]
        if entry.get("training_exposure_allowed") is not True or entry.get("teacher_behavior_allowed") is not True:
            raise NativeMetaOverfitIterationError(
                f"META_TRAIN entry lacks exposure/behavior permission: {opponent_id}"
            )
        permissions = permission_by_id.get(opponent_id)
        if type(permissions) is not dict:
            raise NativeMetaOverfitIterationError(f"META_TRAIN permission source is missing: {opponent_id}")
        if (
            permissions.get("usage_boundary") not in {"training_local", "training_local_and_eval"}
            or permissions.get("training_allowed") is not True
            or permissions.get("behavior_allowed") is not True
            or permissions.get("submission_allowed") is not False
        ):
            raise NativeMetaOverfitIterationError(
                f"META_TRAIN permission source is not eligible for weighting: {opponent_id}"
            )
    records = adapter["records"]
    observations: dict[str, list[Mapping[str, object]]] = {key: [] for key in by_id}
    for record in records:
        opponent_id = record["opponent_id"]
        if opponent_id not in by_id:
            if opponent_id in {entry["opponent_id"] for entry in heldout_entries}:
                raise NativeMetaOverfitIterationError("held-out opponent appeared in outcome adapter")
            raise NativeMetaOverfitIterationError("outcome opponent is absent from curriculum")
        observations[opponent_id].append(record)
    max_exposure = max((len(value) for value in observations.values()), default=0)
    family_members: dict[str, list[Mapping[str, object]]] = {}
    for entry in train_entries:
        family_members.setdefault(str(entry["family"]), []).append(entry)
    parameters = curriculum["parameters"]
    quota = int(curriculum["quota"])
    min_family_quota = int(parameters["min_family_quota"])
    max_opponent_weight = float(parameters["max_opponent_weight"])
    max_family_weight = float(parameters["max_family_weight"])
    family_count = len(family_members)
    family_floor = min_family_quota / quota
    family_caps = {
        family: min(max_family_weight, max_opponent_weight * len(members))
        for family, members in family_members.items()
    }
    if family_floor * family_count > 1.0 + 1e-9:
        raise NativeMetaOverfitIterationError("family floor is infeasible")
    raw_scores: dict[str, float] = {}
    stats: dict[str, dict[str, object]] = {}
    for entry in sorted(train_entries, key=lambda row: row["opponent_id"]):
        opponent_id = str(entry["opponent_id"])
        games = observations[opponent_id]
        exposure = len(games)
        seat_exposure = {
            "0": sum(1 for game in games if game["seat"] == 0),
            "1": sum(1 for game in games if game["seat"] == 1),
        }
        fault_count = sum(1 for game in games if game["fault"])
        fault_rate = fault_count / exposure if exposure else _clamp(
            (entry.get("statistics") or {}).get("fault_rate", 0.0)
        )
        candidate_score = (
            sum(float(game["candidate_score"]) for game in games) / exposure if exposure else None
        )
        loss_score = 1.0 - candidate_score if candidate_score is not None else _clamp(
            (entry.get("statistics") or {}).get("hard_negative", entry.get("weight", 0.0))
        )
        seat_imbalance = abs(seat_exposure["0"] - seat_exposure["1"]) / max(1, exposure)
        under_exposure = (
            (max_exposure - exposure) / max(1, max_exposure) if max_exposure else 1.0
        )
        family_size = len(family_members[str(entry["family"])])
        diversity = 1.0 / family_size
        reliability = _clamp(1.0 - fault_rate)
        raw = (
            _WEIGHT_COMPONENTS["loss_hard_negative"] * _clamp(loss_score)
            + _WEIGHT_COMPONENTS["seat_imbalance_correction"] * _clamp(seat_imbalance)
            + _WEIGHT_COMPONENTS["under_exposure_correction"] * _clamp(under_exposure)
            + _WEIGHT_COMPONENTS["family_diversity_floor"] * _clamp(diversity)
            + _WEIGHT_COMPONENTS["reliability"] * reliability
        )
        raw_scores[opponent_id] = max(1e-12, raw)
        stats[opponent_id] = {
            "split": "META_TRAIN",
            "family": str(entry["family"]),
            "exposure": exposure,
            "candidate_score": candidate_score,
            "loss_score": loss_score,
            "fault_count": fault_count,
            "fault_rate": fault_rate,
            "seat_exposure": seat_exposure,
            "seat_imbalance": seat_imbalance,
            "under_exposure": under_exposure,
            "diversity": diversity,
            "reliability": reliability,
            "raw_score": raw,
            "quota": int(entry["quota"]),
            "weight": 0.0,
        }
    family_raw = {
        family: sum(raw_scores[str(entry["opponent_id"])] for entry in members)
        for family, members in family_members.items()
    }
    family_weights = _project_distribution(
        family_raw,
        total=1.0,
        lower={family: family_floor for family in family_members},
        upper=family_caps,
        label="family",
    )
    weights: dict[str, float] = {}
    for family, members in sorted(family_members.items()):
        member_raw = {str(entry["opponent_id"]): raw_scores[str(entry["opponent_id"])] for entry in members}
        member_weights = _project_distribution(
            member_raw,
            total=family_weights[family],
            lower={key: 0.0 for key in member_raw},
            upper={key: max_opponent_weight for key in member_raw},
            label=f"opponent:{family}",
        )
        weights.update(member_weights)
    for opponent_id, weight in weights.items():
        stats[opponent_id]["weight"] = weight
    for entry in sorted(heldout_entries, key=lambda row: row["opponent_id"]):
        opponent_id = str(entry["opponent_id"])
        stats[opponent_id] = {
            "split": str(entry["split"]),
            "family": str(entry["family"]),
            "exposure": 0,
            "candidate_score": None,
            "loss_score": None,
            "fault_count": 0,
            "fault_rate": None,
            "seat_exposure": {"0": 0, "1": 0},
            "seat_imbalance": 0.0,
            "under_exposure": 0.0,
            "diversity": 0.0,
            "reliability": 0.0,
            "raw_score": 0.0,
            "quota": 0,
            "weight": 0.0,
            "reason": "held_out_split_zero_exposure",
        }
    family_stats: dict[str, dict[str, object]] = {}
    for family, members in sorted(family_members.items()):
        family_stats[family] = {
            "weight": family_weights[family],
            "floor": family_floor,
            "cap": family_caps[family],
            "quota": sum(int(entry["quota"]) for entry in members),
            "members": sorted(str(entry["opponent_id"]) for entry in members),
        }
    exposure = {
        "META_TRAIN": len(records),
        "META_DEV": 0,
        "META_FINAL": 0,
    }
    weight_payload = {
        key: stats[key]["weight"] for key in sorted(stats) if stats[key]["split"] == "META_TRAIN"
    }
    weights_sha = _semantic_sha("mage-ptcg:native-meta-overfit-hard-negative-weights:v1", weight_payload)
    return stats, family_stats, exposure, weights_sha


def _derive_payload(
    *,
    root: Path,
    curriculum_path: Path,
    curriculum: Mapping[str, object],
    adapter_path: Path,
    adapter: Mapping[str, object],
    table_path: Path,
    table: PublicAdvantageTableV1,
    native_baseline: Mapping[str, object],
    candidate_deck: Mapping[str, object] | None,
) -> dict[str, object]:
    stats, family_stats, exposure, weights_sha = _derive_weighting(curriculum, adapter)
    iteration = int(curriculum["iteration"])
    seed = str(curriculum["seed"])
    derived_seed = _semantic_sha(
        "mage-ptcg:native-meta-overfit-iteration-seed:v1",
        {"seed": seed, "iteration": iteration},
    )
    sources = [
        _source_binding(root, curriculum_path, "dynamic_curriculum_manifest"),
        _source_binding(root, adapter_path, "common24_outcome_adapter_manifest"),
        _source_binding(root, table_path, "public_advantage_table"),
    ]
    closure = adapter["execution_closure"]
    adapter_arms = adapter.get("arms") if isinstance(adapter.get("arms"), Mapping) else {}
    native_arm = adapter_arms.get("native") if isinstance(adapter_arms.get("native"), Mapping) else {}
    candidate_arm = adapter_arms.get("candidate") if isinstance(adapter_arms.get("candidate"), Mapping) else {}
    for field, role in (("policy_path", "native_policy"), ("deck_path", "native_deck")):
        if field in native_baseline:
            sources.append(
                {
                    "path": native_baseline[field],
                    "file_sha256": native_baseline["policy_sha256" if field == "policy_path" else "deck_sha256"],
                    "role": role,
                }
            )
    if native_baseline.get("identity_path"):
        sources.append(
            {
                "path": native_baseline["identity_path"],
                "file_sha256": native_baseline["identity_file_sha256"],
                "role": "native_baseline_identity",
            }
        )
    if candidate_deck is not None:
        sources.extend(
            [
                {
                    "path": candidate_deck["manifest_path"],
                    "file_sha256": candidate_deck["manifest_file_sha256"],
                    "role": "candidate_deck_manifest",
                },
                {
                    "path": candidate_deck["deck_path"],
                    "file_sha256": candidate_deck["deck_sha256"],
                    "role": "candidate_deck",
                },
            ]
        )
    gate_status = {
        "curriculum_verified": True,
        "outcome_adapter_verified": True,
        "public_advantage_table_verified": True,
        "native_control_bound": True,
        "candidate_identity_bound": candidate_deck is not None,
        "meta_train_only": exposure["META_DEV"] == 0 and exposure["META_FINAL"] == 0,
        "heldout_zero_exposure": all(
            stats[key]["exposure"] == 0 and stats[key]["weight"] == 0.0
            for key in stats
            if stats[key]["split"] != "META_TRAIN"
        ),
        "authority_false": True,
        "package_closure": False,
        "evaluator_closure": False,
        "performance_gate": False,
    }
    # Task 2 is an artifact/materialization stage.  Package, evaluator, and
    # performance gates are intentionally absent, therefore this remains false.
    ready = all(bool(value) for value in gate_status.values())
    body: dict[str, object] = {
        "schema_version": ITERATION_SCHEMA_V1,
        "purpose": ITERATION_PURPOSE_V1,
        "iteration": iteration,
        "seed": seed,
        "derived_iteration_seed_sha256": derived_seed,
        "sources": sources,
        "curriculum_identity": {
            "path": _relative(root, curriculum_path),
            "file_sha256": _sha_file(curriculum_path),
            "curriculum_sha256": curriculum.get("curriculum_sha256"),
            "iteration": iteration,
        },
        "outcome_adapter_identity": {
            "path": _relative(root, adapter_path),
            "file_sha256": _sha_file(adapter_path),
            "adapter_sha256": adapter.get("adapter_sha256"),
            "records": len(adapter["records"]),
            "protocol_sha256": closure["protocol_sha256"],
            "execution_closure_sha256": closure["execution_closure_sha256"],
            "native_arm_policy_sha256": native_arm.get("policy_sha256"),
            "native_arm_deck_sha256": native_arm.get("deck_sha256"),
            "candidate_arm_policy_sha256": candidate_arm.get("policy_sha256"),
            "candidate_arm_deck_sha256": candidate_arm.get("deck_sha256"),
        },
        "public_advantage_identity": {
            "path": _relative(root, table_path),
            "file_sha256": _sha_file(table_path),
            "table_sha256": table.table_sha256,
            "meta_manifest_sha256": table.meta_manifest_sha256,
        },
        "native_baseline": dict(native_baseline),
        "candidate_identity": dict(candidate_deck) if candidate_deck is not None else None,
        "weighting_parameters": {
            "components": dict(_WEIGHT_COMPONENTS),
            "max_opponent_weight": float(curriculum["parameters"]["max_opponent_weight"]),
            "max_family_weight": float(curriculum["parameters"]["max_family_weight"]),
            "min_family_quota": int(curriculum["parameters"]["min_family_quota"]),
        },
        "opponent_statistics": {key: stats[key] for key in sorted(stats)},
        "family_statistics": {key: family_stats[key] for key in sorted(family_stats)},
        "exposure_by_split": exposure,
        "hard_negative_weights_sha256": weights_sha,
        "gate_status": gate_status,
        "ready_for_evaluation": ready,
        "authority": dict(AUTHORITY_FALSE_V1),
        "iteration_sha256": None,
    }
    body["iteration_sha256"] = _semantic_sha(
        ITERATION_SCHEMA_V1,
        {key: value for key, value in body.items() if key != "iteration_sha256"},
    )
    return body


def _load_all_sources(
    *,
    repo_root: Path,
    curriculum_manifest_path: str | Path,
    outcome_adapter_manifest_path: str | Path,
    public_advantage_table_path: str | Path,
    native_baseline_identity: Mapping[str, object] | str | Path,
    candidate_deck_manifest_path: str | Path | None,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any], Path, PublicAdvantageTableV1, dict[str, object], dict[str, object] | None]:
    curriculum_path = _inside(repo_root, curriculum_manifest_path, "dynamic curriculum manifest")
    adapter_path = _inside(repo_root, outcome_adapter_manifest_path, "outcome adapter manifest")
    table_path = _inside(repo_root, public_advantage_table_path, "public advantage table")
    curriculum = _verified_curriculum(curriculum_path, repo_root)
    adapter = _verified_adapter(adapter_path, repo_root)
    table = _load_public_table(table_path)
    baseline = _load_identity(native_baseline_identity, repo_root, "native baseline")
    arms = adapter.get("arms") if isinstance(adapter.get("arms"), Mapping) else {}
    native_arm = arms.get("native") if isinstance(arms.get("native"), Mapping) else None
    if native_arm is not None:
        for field in ("policy_sha256", "deck_sha256"):
            arm_value = native_arm.get(field)
            if arm_value is not None and arm_value != baseline[field]:
                raise NativeMetaOverfitIterationError(
                    f"native baseline {field} differs from outcome adapter native arm"
                )
    candidate = _load_candidate_deck(candidate_deck_manifest_path, repo_root) if candidate_deck_manifest_path else None
    return curriculum_path, curriculum, adapter_path, adapter, table_path, table, baseline, candidate


def build_native_meta_overfit_iteration_v1(
    *,
    repo_root: str | Path,
    curriculum_manifest_path: str | Path | None = None,
    outcome_adapter_manifest_path: str | Path | None = None,
    public_advantage_table_path: str | Path | None = None,
    native_baseline_identity: Mapping[str, object] | str | Path | None = None,
    output_manifest_path: str | Path,
    candidate_deck_manifest_path: str | Path | None = None,
    dynamic_curriculum_manifest_path: str | Path | None = None,
    common24_outcome_adapter_manifest_path: str | Path | None = None,
    native_baseline_identity_path: str | Path | None = None,
) -> dict[str, object]:
    """Build and atomically publish one strict hard-negative iteration manifest."""

    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise NativeMetaOverfitIterationError("repo_root must be a directory")
    curriculum_manifest_path = curriculum_manifest_path or dynamic_curriculum_manifest_path
    outcome_adapter_manifest_path = outcome_adapter_manifest_path or common24_outcome_adapter_manifest_path
    native_baseline_identity = native_baseline_identity or native_baseline_identity_path
    if curriculum_manifest_path is None or outcome_adapter_manifest_path is None or public_advantage_table_path is None:
        raise NativeMetaOverfitIterationError("curriculum, outcome adapter, and public table are required")
    if native_baseline_identity is None:
        raise NativeMetaOverfitIterationError("native baseline identity is required")
    output = Path(output_manifest_path).resolve()
    if output.exists():
        raise FileExistsError(output)
    loaded = _load_all_sources(
        repo_root=root,
        curriculum_manifest_path=curriculum_manifest_path,
        outcome_adapter_manifest_path=outcome_adapter_manifest_path,
        public_advantage_table_path=public_advantage_table_path,
        native_baseline_identity=native_baseline_identity,
        candidate_deck_manifest_path=candidate_deck_manifest_path,
    )
    curriculum_path, curriculum, adapter_path, adapter, table_path, table, baseline, candidate = loaded
    manifest = _derive_payload(
        root=root,
        curriculum_path=curriculum_path,
        curriculum=curriculum,
        adapter_path=adapter_path,
        adapter=adapter,
        table_path=table_path,
        table=table,
        native_baseline=baseline,
        candidate_deck=candidate,
    )
    raw = _canonical_bytes(manifest)
    try:
        _atomic_write_new(output, raw)
    except FileExistsError:
        # Exclusive destination claim lost to another writer.  That artifact
        # is not ours and must remain intact.
        raise
    except BaseException:
        _remove_published_if_owned(output, raw)
        raise
    try:
        verified = verify_native_meta_overfit_iteration_v1(output, root)
    except BaseException:
        _remove_published_if_owned(output, raw)
        raise
    if verified != manifest:
        _remove_published_if_owned(output, raw)
        raise NativeMetaOverfitIterationError("iteration manifest does not reproduce after write")
    return manifest


def verify_native_meta_overfit_iteration_v1(path: str | Path, repo_root: str | Path) -> dict[str, object]:
    """Reload every source, recompute weights, and verify one iteration exactly."""

    root = Path(repo_root).resolve()
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file():
        raise NativeMetaOverfitIterationError(f"iteration manifest is missing: {manifest_path}")
    manifest = _strict_json(manifest_path)
    if set(manifest) != _MANIFEST_KEYS or manifest.get("schema_version") != ITERATION_SCHEMA_V1:
        raise NativeMetaOverfitIterationError("iteration manifest schema is invalid")
    if manifest.get("purpose") != ITERATION_PURPOSE_V1:
        raise NativeMetaOverfitIterationError("iteration purpose is invalid")
    _authority_false(manifest.get("authority"), "iteration manifest")
    supplied = manifest.get("iteration_sha256")
    expected = _semantic_sha(
        ITERATION_SCHEMA_V1,
        {key: value for key, value in manifest.items() if key != "iteration_sha256"},
    )
    if supplied != expected:
        raise NativeMetaOverfitIterationError("iteration semantic SHA mismatch")
    sources = manifest.get("sources")
    if type(sources) is not list or not sources:
        raise NativeMetaOverfitIterationError("iteration source bindings are missing")
    for source in sources:
        if type(source) is not dict or set(source) != {"path", "file_sha256", "role"}:
            raise NativeMetaOverfitIterationError("iteration source binding is invalid")
        source_path = _inside(root, str(source["path"]), "iteration source")
        if _sha_file(source_path) != source["file_sha256"]:
            raise NativeMetaOverfitIterationError("iteration source SHA mismatch")
    curriculum_binding = manifest["curriculum_identity"]
    adapter_binding = manifest["outcome_adapter_identity"]
    table_binding = manifest["public_advantage_identity"]
    for binding, name in ((curriculum_binding, "curriculum"), (adapter_binding, "outcome adapter"), (table_binding, "public table")):
        if type(binding) is not dict or type(binding.get("path")) is not str or type(binding.get("file_sha256")) is not str:
            raise NativeMetaOverfitIterationError(f"{name} identity binding is invalid")
    curriculum_path = _inside(root, curriculum_binding["path"], "dynamic curriculum manifest")
    adapter_path = _inside(root, adapter_binding["path"], "outcome adapter manifest")
    table_path = _inside(root, table_binding["path"], "public advantage table")
    curriculum, adapter = _verified_curriculum(curriculum_path, root), _verified_adapter(adapter_path, root)
    table = _load_public_table(table_path)
    if _sha_file(curriculum_path) != curriculum_binding["file_sha256"] or _sha_file(adapter_path) != adapter_binding["file_sha256"] or _sha_file(table_path) != table_binding["file_sha256"]:
        raise NativeMetaOverfitIterationError("bound source SHA differs from iteration identity")
    baseline = _load_identity(manifest["native_baseline"], root, "native baseline")
    arms = adapter.get("arms") if isinstance(adapter.get("arms"), Mapping) else {}
    native_arm = arms.get("native") if isinstance(arms.get("native"), Mapping) else None
    if native_arm is not None:
        for field in ("policy_sha256", "deck_sha256"):
            arm_value = native_arm.get(field)
            if arm_value is not None and arm_value != baseline[field]:
                raise NativeMetaOverfitIterationError(
                    f"native baseline {field} differs from outcome adapter native arm"
                )
    candidate_payload = manifest.get("candidate_identity")
    candidate = None
    if candidate_payload is not None:
        if type(candidate_payload) is not dict or type(candidate_payload.get("manifest_path")) is not str:
            raise NativeMetaOverfitIterationError("candidate identity binding is invalid")
        candidate = _load_candidate_deck(candidate_payload["manifest_path"], root)
    expected_manifest = _derive_payload(
        root=root,
        curriculum_path=curriculum_path,
        curriculum=curriculum,
        adapter_path=adapter_path,
        adapter=adapter,
        table_path=table_path,
        table=table,
        native_baseline=baseline,
        candidate_deck=candidate,
    )
    if expected_manifest != manifest:
        raise NativeMetaOverfitIterationError("iteration manifest does not reproduce from bound sources")
    return manifest


__all__ = [
    "AUTHORITY_FALSE_V1",
    "ITERATION_PURPOSE_V1",
    "ITERATION_SCHEMA_V1",
    "NativeMetaOverfitIterationError",
    "build_native_meta_overfit_iteration_v1",
    "verify_native_meta_overfit_iteration_v1",
]
