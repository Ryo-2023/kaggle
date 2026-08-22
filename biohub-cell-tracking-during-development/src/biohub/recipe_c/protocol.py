"""Fail-closed experiment registration and selection-lock protocol.

The selection lock is deliberately boring: it is a small, explicit JSON schema
whose identity is the SHA-256 of every field except ``selection_lock_id``.  It
does not copy evaluation receipts, ground-truth records, or filesystem paths.
This keeps a retrospective research decision auditable without allowing the
decision to become an input to the prediction run.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from errno import ELOOP, ENOTDIR
from pathlib import Path, PureWindowsPath

from biohub.reproducibility.gt_guard import (
    GroundTruthOrderingError,
    mint_prediction_token,
    prediction_manifest_path,
)

from .source import RECIPE_C_SOURCE

_PANEL_V1_FIXED: tuple[str, ...] = (
    "44b6_0113de3b",
    "44b6_0b24845f",
    "44b6_0c582fdc",
    "44b6_0db75fae",
    "44b6_12dfb391",
)
# Public name for callers; implementation always uses the private literal so
# reassignment of a module attribute cannot turn the panel into a dynamic input.
PANEL_V1: tuple[str, ...] = _PANEL_V1_FIXED

LOCK_SCHEMA_VERSION = 1
PANEL_STATUS = "retrospective_adaptive_research"
GROUND_TRUTH_POST_ANALYSIS_SCOPE = "post_prediction_analysis_only"
_GROUND_TRUTH_NONE_SCOPE = "none"
_ALLOWED_DEVICES = ("auto", "cpu", "cuda", "mps")
_DEVICE_POLICIES = {
    "auto": "accelerator_first_fallback",
    "cpu": "explicit_cpu",
    "cuda": "explicit_cuda",
    "mps": "explicit_mps",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_HASH_CHUNK_SIZE = 1024 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PRIOR_RECEIPT_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "receipt_type",
        "panel",
        "ground_truth_used_for_prediction",
        "ground_truth_used_for_parameter_fitting",
        "ground_truth_usage_scope",
        "gt_guard",
    },
)
_PRIOR_RECEIPT_OPTIONAL_FIELDS = frozenset({"metrics", "selection_lock_id"})
_GT_GUARD_FIELDS = frozenset(
    {
        "sample_id",
        "prediction_path",
        "prediction_manifest_path",
        "prediction_directory_sha256",
        "prediction_files",
        "prediction_total_bytes",
        "prediction_manifest_created_at",
        "prediction_persisted_at",
        "ground_truth_opened_at",
        "ordering_enforced_by",
    },
)
_GT_GUARD_OPTIONAL_FIELDS = frozenset(
    {"ground_truth_path", "ordering_evidence"},
)
_GT_GUARD_ORDERING_TOKEN = "biohub.reproducibility.gt_guard.open_ground_truth"

_SOURCE_FIELDS: tuple[str, ...] = (
    "source_url",
    "source_commit",
    "license",
    "license_relative_path",
    "license_sha256",
    "config_relative_path",
    "config_sha256",
    "notebook_relative_path",
    "notebook_sha256",
    "predictor_relative_path",
    "predictor_sha256",
    "primary_checkpoint_relative_path",
    "primary_checkpoint_sha256",
    "secondary_checkpoint_relative_path",
    "secondary_checkpoint_sha256",
    "secondary_staging_relative_path",
    "primary_dataset",
    "primary_dataset_version",
    "primary_dataset_license",
    "secondary_dataset",
    "secondary_dataset_version",
    "secondary_dataset_license",
)

_LOCK_FIELDS = frozenset(
    {
        "schema_version",
        "selection_lock_id",
        "panel_status",
        "panel",
        "experiment",
        "experiment_id",
        "source_url",
        "source_commit",
        "license",
        "license_relative_path",
        "license_sha256",
        "source_config_relative_path",
        "source_config_sha256",
        "notebook_relative_path",
        "notebook_sha256",
        "predictor_relative_path",
        "predictor_sha256",
        "primary_checkpoint_relative_path",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_relative_path",
        "secondary_checkpoint_sha256",
        "secondary_staging_relative_path",
        "primary_dataset",
        "primary_dataset_version",
        "primary_dataset_license",
        "secondary_dataset",
        "secondary_dataset_version",
        "secondary_dataset_license",
        "config_relative_path",
        "config_sha256",
        "code_commit",
        "code_commit_clean",
        "requested_device",
        "device_policy",
        "ground_truth_usage",
        "ground_truth_used_for_prediction",
        "ground_truth_used_for_parameter_fitting",
        "ground_truth_used_for_method_family_selection",
        "ground_truth_usage_scope",
        "prior_evidence",
    }
)
_PANEL_FIELDS = frozenset({"panel_id", "sample_ids"})
_GROUND_TRUTH_FIELDS = frozenset(
    {
        "ground_truth_used_for_prediction",
        "ground_truth_used_for_parameter_fitting",
        "ground_truth_used_for_method_family_selection",
        "ground_truth_usage_scope",
    }
)
_PRIOR_EVIDENCE_FIELDS = frozenset({"receipt_sha256"})


def _is_finite(value: object) -> bool:
    return not isinstance(value, float) or math.isfinite(value)


def _validate_json_value(value: object, label: str, *, allow_none: bool = False) -> None:
    """Reject values that cannot be represented safely in canonical JSON."""

    if value is None:
        if allow_none:
            return
        raise ValueError(f"{label} must not be empty")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"{label} must not be empty")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"{label} contains an invalid JSON key")
            _validate_json_value(item, f"{label}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError(f"{label} must not be empty")
        for index, item in enumerate(value):
            _validate_json_value(item, f"{label}[{index}]")
        return
    raise ValueError(f"{label} is not JSON-safe")


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """An immutable, pre-registered experiment hypothesis.

    The qualitative fields intentionally accept either a short string or a
    finite JSON scalar.  Campaigns can therefore record ``cost="one run"`` or
    a numeric estimate without silently dropping the information from the
    canonical identity.
    """

    experiment_id: str
    method_family: str
    hypothesis: str
    expected_gain: object
    cost: object
    risk: object
    novelty: object
    changes: object
    control_id: str
    acceptance_criteria: object
    prior_evidence_receipt_hash: str | None = None

    def __post_init__(self) -> None:
        for name in ("experiment_id", "method_family", "hypothesis", "control_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"experiment.{name} must be a non-empty string")
        if isinstance(self.expected_gain, bool) or not isinstance(self.expected_gain, (int, float)):
            raise ValueError("experiment.expected_gain must be a finite number")
        for field in fields(self):
            value = getattr(self, field.name)
            allow_none = field.name == "prior_evidence_receipt_hash"
            _validate_json_value(value, f"experiment.{field.name}", allow_none=allow_none)
        if self.prior_evidence_receipt_hash is not None and not _SHA256_RE.fullmatch(
            self.prior_evidence_receipt_hash,
        ):
            raise ValueError("experiment.prior_evidence_receipt_hash must be a lowercase SHA-256")

    def to_payload(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_mapping(payload: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"payload is not canonical JSON-safe: {exc}") from exc


def canonical_lock_json(payload: Mapping[str, object], *, without_id: bool = False) -> str:
    """Return the exact canonical bytes used by the selection-lock identity."""

    if not isinstance(payload, Mapping):
        raise ValueError("selection lock payload must be a mapping")
    value = dict(payload)
    if without_id:
        value.pop("selection_lock_id", None)
    return _canonical_json_mapping(value)


def canonical_json(payload: Mapping[str, object], *, without_id: bool = False) -> str:
    """Compatibility spelling for callers that use the source module helper name."""

    return canonical_lock_json(payload, without_id=without_id)


def recompute_selection_lock_id(payload: Mapping[str, object]) -> str:
    """Recompute the lock identity from all fields except its self-reference."""

    return _sha256_bytes(canonical_lock_json(payload, without_id=True).encode("utf-8"))


def _require_exact_keys(payload: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    actual = set(payload)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ValueError(f"{label} missing required field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(sorted(unknown))}")


def _require_required_keys(
    payload: Mapping[str, object],
    required: frozenset[str],
    optional: frozenset[str],
    label: str,
) -> None:
    actual = set(payload)
    missing = required - actual
    unknown = actual - required - optional
    if missing:
        raise ValueError(f"{label} missing required field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(sorted(unknown))}")


def _require_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_schema_version(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != LOCK_SCHEMA_VERSION:
        raise ValueError(f"{label} must be the exact integer schema version {LOCK_SCHEMA_VERSION}")


def _require_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a relative path")
    return value


def _looks_absolute_path(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value.startswith(("~/", "~\\"))
    )


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


_GT_PATH_KEYS = frozenset(
    {
        "groundtruth",
        "groundtruthdir",
        "groundtruthdirectory",
        "groundtruthpath",
        "groundtruthroot",
        "groundtruthfile",
        "groundtruthuri",
        "gt",
        "gtdir",
        "gtdirectory",
        "gtpath",
        "gtroot",
        "gtfile",
        "gturi",
        "truthpath",
        "truthdir",
        "truthdirectory",
        "labelpath",
        "labelspath",
        "annotationpath",
        "annotationfile",
        "annotationdir",
        "annotationdirectory",
        "annotationuri",
    },
)
_CREDENTIAL_KEYS = frozenset(
    {
        "credential",
        "credentials",
        "credentialfile",
        "credentialpath",
        "secret",
        "secretfile",
        "secretpath",
        "token",
        "tokenpath",
        "apikey",
        "apikeypath",
        "accesstoken",
        "accesskey",
        "privatekey",
        "auth",
        "authpath",
        "kaggle",
        "kaggledir",
        "kagglepath",
        "password",
        "passwordpath",
    },
)
_ALLOWED_GT_USAGE_KEYS = frozenset(
    {
        "groundtruthusedforprediction",
        "groundtruthusedforparameterfitting",
        "groundtruthusedformethodfamilyselection",
        "groundtruthusagescope",
    },
)


def _key_forbids_reference(normalized_key: str) -> str | None:
    if normalized_key in _ALLOWED_GT_USAGE_KEYS:
        return None
    if normalized_key in _GT_PATH_KEYS:
        return "ground truth"
    if normalized_key in _CREDENTIAL_KEYS:
        return "credential"
    if (
        "credential" in normalized_key
        or "secret" in normalized_key
        or "token" in normalized_key
        or "apikey" in normalized_key
        or "password" in normalized_key
        or "accesskey" in normalized_key
        or "auth" in normalized_key
        or "kaggle" in normalized_key
        or "privatekey" in normalized_key
    ):
        return "credential"
    return None


def _assert_no_forbidden_paths(value: object, location: str = "lock") -> None:
    """Reject absolute, credential, and ground-truth paths recursively."""

    if isinstance(value, str):
        lowered = value.lower()
        if _looks_absolute_path(value):
            raise ValueError(f"absolute path is forbidden in {location}")
        if ".kaggle" in lowered or "kaggle.json" in lowered or "credential" in lowered:
            raise ValueError(f"credential path is forbidden in {location}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _normalized_key(key)
            child = f"{location}.{key}"
            forbidden_kind = _key_forbids_reference(normalized_key)
            if forbidden_kind is not None:
                raise ValueError(f"{forbidden_kind} path/value is forbidden in {child}")
            if isinstance(item, str):
                lowered = item.lower()
                if _looks_absolute_path(item):
                    raise ValueError(f"absolute path is forbidden in {child}")
                if (
                    ".kaggle" in lowered
                    or "kaggle.json" in lowered
                    or "credential" in lowered
                    or forbidden_kind == "credential"
                ):
                    raise ValueError(f"credential path is forbidden in {child}")
            _assert_no_forbidden_paths(item, child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _assert_no_forbidden_paths(item, f"{location}[{index}]")


def _source_identity(source_receipt: Mapping[str, object]) -> dict[str, object]:
    """Require the direct receipt fields and compare each to ``RECIPE_C_SOURCE``."""

    if not isinstance(source_receipt, Mapping):
        raise ValueError("source receipt must be a mapping with direct contract fields")
    missing = [field for field in _SOURCE_FIELDS if field not in source_receipt]
    if missing:
        raise ValueError(f"source receipt missing direct field(s): {', '.join(missing)}")
    identity: dict[str, object] = {}
    for field in _SOURCE_FIELDS:
        expected = getattr(RECIPE_C_SOURCE, field)
        actual = source_receipt[field]
        if actual != expected:
            raise ValueError(f"source receipt {field} does not match RECIPE_C_SOURCE")
        identity[field] = actual
    return identity


def _safe_config_label(path: Path) -> str:
    """Retain only a repository-relative label; never persist an absolute path."""

    if path.is_absolute():
        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(_PROJECT_ROOT).as_posix()
        except ValueError:
            return path.name
    if not path.parts or ".." in path.parts:
        raise ValueError("config path must not escape its relative root")
    return path.as_posix()


def _reject_symlink_components(path: Path, label: str) -> Path:
    """Reject a path whose existing components include a symlink."""

    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} symlink traversal is forbidden: {current}")
    return absolute


def _load_prior_receipt(receipt: object, index: int) -> Mapping[str, object]:
    if isinstance(receipt, str):
        receipt = Path(receipt)
    if not isinstance(receipt, Path):
        raise ValueError(f"prior receipt {index} must be a persisted JSON path")
    receipt = _reject_symlink_components(receipt, f"prior receipt {index}")
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError(f"prior receipt {index} must be a regular persisted file")
    try:
        raw = receipt.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"prior receipt {index} could not be read as JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"prior receipt {index} must contain a JSON object")
    try:
        canonical = _canonical_json_mapping(payload)
    except ValueError as exc:
        raise ValueError(f"prior receipt {index} is not canonical JSON-safe") from exc
    if raw.decode("utf-8") != canonical:
        raise ValueError(f"prior receipt {index} is not persisted as canonical JSON")
    return payload


def _validate_strong_prior_receipt(receipt: Mapping[str, object], index: int) -> str:
    """Require persisted schema, exact panel order, and GT-guard timestamps."""

    schema_version = receipt.get("schema_version")
    _require_schema_version(schema_version, f"prior receipt {index} schema_version")
    if receipt.get("receipt_type") != "panel_evaluation":
        raise ValueError(f"prior receipt {index} has no strong receipt_type")
    allowed_fields = _PRIOR_RECEIPT_REQUIRED_FIELDS | _PRIOR_RECEIPT_OPTIONAL_FIELDS
    missing = _PRIOR_RECEIPT_REQUIRED_FIELDS - set(receipt)
    unknown = set(receipt) - allowed_fields
    if missing or unknown:
        detail = ", ".join(sorted(missing or unknown))
        raise ValueError(f"prior receipt {index} schema is incomplete or unknown: {detail}")
    panel = receipt.get("panel")
    if not isinstance(panel, Mapping):
        raise ValueError(f"prior receipt {index} has no strong PANEL_V1 ordering evidence")
    if set(panel) != {"panel_id", "sample_ids"}:
        raise ValueError(f"prior receipt {index} panel schema is weak or unknown")
    if panel.get("panel_id") != "PANEL_V1" or panel.get("sample_ids") != list(_PANEL_V1_FIXED):
        raise ValueError(f"prior receipt {index} does not prove exact PANEL_V1 ordering")
    if receipt.get("ground_truth_used_for_prediction") is not False:
        raise ValueError(f"prior receipt {index} ground truth prediction flag is not false")
    if receipt.get("ground_truth_used_for_parameter_fitting") is not False:
        raise ValueError(f"prior receipt {index} ground truth fitting flag is not false")
    scope = receipt.get("ground_truth_usage_scope")
    if scope != GROUND_TRUTH_POST_ANALYSIS_SCOPE:
        raise ValueError(f"prior receipt {index} has no post-prediction ground truth usage scope")
    guard = receipt.get("gt_guard")
    if not isinstance(guard, list) or len(guard) != len(_PANEL_V1_FIXED):
        raise ValueError(f"prior receipt {index} gt_guard must cover all five PANEL_V1 samples")
    for guard_index, item in enumerate(guard):
        if not isinstance(item, Mapping):
            raise ValueError(f"prior receipt {index} gt_guard[{guard_index}] is not an object")
        _require_required_keys(
            item,
            _GT_GUARD_FIELDS,
            _GT_GUARD_OPTIONAL_FIELDS,
            f"prior receipt {index} gt_guard[{guard_index}]",
        )
        if item["sample_id"] != _PANEL_V1_FIXED[guard_index]:
            raise ValueError(f"prior receipt {index} gt_guard sample order is not PANEL_V1")
        for path_field in ("prediction_path", "prediction_manifest_path"):
            if not isinstance(item[path_field], str) or not item[path_field].strip():
                raise ValueError(f"prior receipt {index} {path_field} must be a non-empty path")
        prediction_path = _reject_symlink_components(
            Path(item["prediction_path"]),
            f"prior receipt {index} prediction",
        )
        manifest_path = _reject_symlink_components(
            Path(item["prediction_manifest_path"]),
            f"prior receipt {index} prediction manifest",
        )
        if prediction_path.is_symlink() or not prediction_path.is_dir():
            raise ValueError(f"prior receipt {index} prediction path is not a persisted directory")
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError(f"prior receipt {index} prediction manifest is not persisted")
        expected_manifest = prediction_manifest_path(prediction_path)
        if manifest_path.absolute() != expected_manifest.absolute():
            raise ValueError(
                f"prior receipt {index} prediction manifest must be the per-prediction manifest",
            )
        try:
            token = mint_prediction_token(prediction_path)
        except (GroundTruthOrderingError, OSError, ValueError) as exc:
            raise ValueError(
                f"prior receipt {index} prediction manifest or bytes could not be revalidated",
            ) from exc
        if token.manifest_path.absolute() != manifest_path.absolute():
            raise ValueError(f"prior receipt {index} prediction manifest path does not match gt_guard")
        if item["prediction_directory_sha256"] != token.directory_sha256:
            raise ValueError(f"prior receipt {index} prediction directory digest does not match persisted bytes")
        for field in ("prediction_files", "prediction_total_bytes"):
            number = item[field]
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise ValueError(f"prior receipt {index} gt_guard {field} is invalid")
        if item["prediction_files"] < 1:
            raise ValueError(f"prior receipt {index} gt_guard prediction directory is empty")
        if item["prediction_files"] != token.files or item["prediction_total_bytes"] != token.total_bytes:
            raise ValueError(f"prior receipt {index} prediction size evidence does not match persisted bytes")
        if item["ordering_enforced_by"] != _GT_GUARD_ORDERING_TOKEN:
            raise ValueError(f"prior receipt {index} gt_guard ordering token is invalid")
        timestamps: list[datetime] = []
        for field in (
            "prediction_manifest_created_at",
            "prediction_persisted_at",
            "ground_truth_opened_at",
        ):
            timestamp = item[field]
            if not isinstance(timestamp, str):
                raise ValueError(f"prior receipt {index} gt_guard {field} is not a timestamp")
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise ValueError(f"prior receipt {index} gt_guard {field} is invalid") from exc
            if parsed.tzinfo is None:
                raise ValueError(f"prior receipt {index} gt_guard {field} must include timezone")
            parsed = parsed.astimezone(UTC)
            if parsed > datetime.now(UTC):
                raise ValueError(f"prior receipt {index} gt_guard {field} is in the future")
            timestamps.append(parsed)
        try:
            manifest_created = datetime.fromisoformat(token.manifest_created_at).astimezone(UTC)
            receipt_created = datetime.fromisoformat(item["prediction_manifest_created_at"]).astimezone(UTC)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"prior receipt {index} gt_guard manifest timestamp is invalid") from exc
        if manifest_created != receipt_created:
            raise ValueError(f"prior receipt {index} manifest creation time does not match persisted manifest")
        if not timestamps[0] < timestamps[1] < timestamps[2]:
            raise ValueError(f"prior receipt {index} gt_guard timestamps violate prediction-before-GT order")
    selection_lock_id = receipt.get("selection_lock_id")
    if selection_lock_id is not None:
        _require_hash(selection_lock_id, f"prior receipt {index} selection_lock_id")
    try:
        canonical = _canonical_json_mapping(receipt)
    except ValueError as exc:
        raise ValueError(f"prior receipt {index} is not canonical JSON-safe") from exc
    return _sha256_bytes(canonical.encode("utf-8"))


def _prior_hashes(prior_evaluation_receipts: Sequence[object]) -> list[str]:
    hashes: list[str] = []
    for index, item in enumerate(prior_evaluation_receipts):
        payload = _load_prior_receipt(item, index)
        hashes.append(_validate_strong_prior_receipt(payload, index))
    return hashes


def _build_experiment_payload(experiment: ExperimentSpec, prior_hashes: list[str]) -> dict[str, object]:
    payload = experiment.to_payload()
    prior_hash = payload["prior_evidence_receipt_hash"]
    if prior_hash is not None and prior_hash not in prior_hashes:
        raise ValueError("experiment prior evidence hash does not match supplied prior receipt")
    if prior_hashes and prior_hash is None:
        # The receipt list is the authoritative identity; the optional singular
        # field remains None when callers do not know its hash before building.
        payload["prior_evidence_receipt_hash"] = prior_hashes[0]
    if not prior_hashes and prior_hash is not None:
        raise ValueError("experiment prior evidence hash supplied without prior receipt")
    return payload


def build_selection_lock(
    source_receipt: Mapping[str, object],
    config_path: Path,
    code_commit: str,
    requested_device: str,
    experiment: ExperimentSpec,
    prior_evaluation_receipts: Sequence[object] = (),
) -> dict[str, object]:
    """Build and validate a canonical, immutable selection-lock payload."""

    identity = _source_identity(source_receipt)
    config_path = Path(config_path)
    if not config_path.is_file() or config_path.is_dir():
        raise FileNotFoundError(f"config file is missing: {config_path.name}")
    config_sha256 = _sha256_file(config_path)
    if not _SHA256_RE.fullmatch(config_sha256):  # defensive; hashlib always returns this form
        raise ValueError("config bytes did not produce a lowercase SHA-256")
    if config_sha256 != RECIPE_C_SOURCE.config_sha256:
        raise ValueError("config bytes do not match the pinned RECIPE_C_SOURCE config")
    if not isinstance(code_commit, str) or not _SHA1_RE.fullmatch(code_commit):
        raise ValueError("code_commit must be a 40-character lowercase SHA-1")
    if not isinstance(requested_device, str) or requested_device not in _ALLOWED_DEVICES:
        raise ValueError(f"requested_device must be one of {_ALLOWED_DEVICES}")
    prior_hashes = _prior_hashes(prior_evaluation_receipts)
    experiment_payload = _build_experiment_payload(experiment, prior_hashes)
    selected_source = {
        "source_url": identity["source_url"],
        "source_commit": identity["source_commit"],
        "license": identity["license"],
        "license_relative_path": identity["license_relative_path"],
        "license_sha256": identity["license_sha256"],
        "source_config_relative_path": identity["config_relative_path"],
        "source_config_sha256": identity["config_sha256"],
        "notebook_relative_path": identity["notebook_relative_path"],
        "notebook_sha256": identity["notebook_sha256"],
        "predictor_relative_path": identity["predictor_relative_path"],
        "predictor_sha256": identity["predictor_sha256"],
        "primary_checkpoint_relative_path": identity["primary_checkpoint_relative_path"],
        "primary_checkpoint_sha256": identity["primary_checkpoint_sha256"],
        "secondary_checkpoint_relative_path": identity["secondary_checkpoint_relative_path"],
        "secondary_checkpoint_sha256": identity["secondary_checkpoint_sha256"],
        "secondary_staging_relative_path": identity["secondary_staging_relative_path"],
        "primary_dataset": identity["primary_dataset"],
        "primary_dataset_version": identity["primary_dataset_version"],
        "primary_dataset_license": identity["primary_dataset_license"],
        "secondary_dataset": identity["secondary_dataset"],
        "secondary_dataset_version": identity["secondary_dataset_version"],
        "secondary_dataset_license": identity["secondary_dataset_license"],
    }
    selected_source.update(
        {
            "schema_version": LOCK_SCHEMA_VERSION,
            "selection_lock_id": "",
            "panel_status": PANEL_STATUS,
            "panel": {"panel_id": "PANEL_V1", "sample_ids": list(_PANEL_V1_FIXED)},
            "experiment": experiment_payload,
            "experiment_id": experiment.experiment_id,
            "config_relative_path": _safe_config_label(config_path),
            "config_sha256": config_sha256,
            "code_commit": code_commit,
            "code_commit_clean": True,
            "requested_device": requested_device,
            "device_policy": _DEVICE_POLICIES[requested_device],
            "ground_truth_usage": {
                "ground_truth_used_for_prediction": False,
                "ground_truth_used_for_parameter_fitting": False,
                "ground_truth_used_for_method_family_selection": bool(prior_hashes),
                "ground_truth_usage_scope": (
                    GROUND_TRUTH_POST_ANALYSIS_SCOPE if prior_hashes else _GROUND_TRUTH_NONE_SCOPE
                ),
            },
            "ground_truth_used_for_prediction": False,
            "ground_truth_used_for_parameter_fitting": False,
            "ground_truth_used_for_method_family_selection": bool(prior_hashes),
            "ground_truth_usage_scope": (
                GROUND_TRUTH_POST_ANALYSIS_SCOPE if prior_hashes else _GROUND_TRUTH_NONE_SCOPE
            ),
            "prior_evidence": {"receipt_sha256": prior_hashes},
        },
    )
    selected_source["selection_lock_id"] = recompute_selection_lock_id(selected_source)
    validate_selection_lock_payload(selected_source)
    return selected_source


def validate_selection_lock_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate a lock mapping without reading its referenced config bytes."""

    if not isinstance(payload, Mapping):
        raise ValueError("selection lock payload must be a mapping")
    value = dict(payload)
    _require_exact_keys(value, _LOCK_FIELDS, "selection lock")
    try:
        _canonical_json_mapping(value)
    except ValueError as exc:
        raise ValueError(f"selection lock is not canonical JSON-safe: {exc}") from exc
    _assert_no_forbidden_paths(value)

    _require_schema_version(value["schema_version"], "selection lock schema_version")
    if value["panel_status"] != PANEL_STATUS:
        raise ValueError("panel_status must be retrospective_adaptive_research")
    panel = value["panel"]
    if not isinstance(panel, Mapping):
        raise ValueError("PANEL_V1 panel must be a mapping")
    _require_exact_keys(panel, _PANEL_FIELDS, "PANEL_V1 panel")
    if panel["panel_id"] != "PANEL_V1" or panel["sample_ids"] != list(_PANEL_V1_FIXED):
        raise ValueError("PANEL_V1 sample_ids must have the exact fixed order")

    experiment = value["experiment"]
    if not isinstance(experiment, Mapping):
        raise ValueError("experiment must be a mapping")
    expected_experiment_fields = frozenset(field.name for field in fields(ExperimentSpec))
    _require_exact_keys(experiment, expected_experiment_fields, "experiment")
    for field in fields(ExperimentSpec):
        _validate_json_value(
            experiment[field.name],
            f"experiment.{field.name}",
            allow_none=field.name == "prior_evidence_receipt_hash",
        )
    for name in ("experiment_id", "method_family", "hypothesis", "control_id"):
        if not isinstance(experiment[name], str) or not experiment[name].strip():
            raise ValueError(f"experiment.{name} must be a non-empty string")
    if isinstance(experiment["expected_gain"], bool) or not isinstance(
        experiment["expected_gain"],
        (int, float),
    ):
        raise ValueError("experiment.expected_gain must be a finite number")
    if experiment["prior_evidence_receipt_hash"] is not None:
        _require_hash(experiment["prior_evidence_receipt_hash"], "experiment prior evidence hash")
    if value["experiment_id"] != experiment["experiment_id"]:
        raise ValueError("experiment_id does not match experiment registration")
    if not isinstance(experiment["method_family"], str) or not experiment["method_family"].strip():
        raise ValueError("experiment method_family must not be empty")

    for field in _SOURCE_FIELDS:
        root_field = "source_config_relative_path" if field == "config_relative_path" else field
        if field == "config_sha256":
            root_field = "source_config_sha256"
        expected = getattr(RECIPE_C_SOURCE, field)
        if value[root_field] != expected:
            raise ValueError(f"{root_field} does not match RECIPE_C_SOURCE")
    for field in (
        "license_relative_path",
        "notebook_relative_path",
        "predictor_relative_path",
        "primary_checkpoint_relative_path",
        "secondary_checkpoint_relative_path",
        "secondary_staging_relative_path",
        "source_config_relative_path",
        "config_relative_path",
    ):
        _require_relative_path(value[field], field)
    for field in (
        "license_sha256",
        "source_config_sha256",
        "notebook_sha256",
        "predictor_sha256",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_sha256",
        "config_sha256",
    ):
        _require_hash(value[field], field)
    if value["config_sha256"] != RECIPE_C_SOURCE.config_sha256:
        raise ValueError("config_sha256 does not match the pinned RECIPE_C_SOURCE config")
    if not isinstance(value["code_commit"], str) or not _SHA1_RE.fullmatch(value["code_commit"]):
        raise ValueError("code_commit must be a 40-character lowercase SHA-1")
    if value["code_commit_clean"] is not True:
        raise ValueError("code commit must be a clean HEAD")
    if value["requested_device"] not in _ALLOWED_DEVICES:
        raise ValueError(f"requested_device must be one of {_ALLOWED_DEVICES}")
    if value["device_policy"] != _DEVICE_POLICIES[value["requested_device"]]:
        raise ValueError("requested device policy does not match requested_device")

    usage = value["ground_truth_usage"]
    if not isinstance(usage, Mapping):
        raise ValueError("ground truth usage must be a mapping")
    _require_exact_keys(usage, _GROUND_TRUTH_FIELDS, "ground truth usage")
    for field in (
        "ground_truth_used_for_prediction",
        "ground_truth_used_for_parameter_fitting",
        "ground_truth_used_for_method_family_selection",
    ):
        if not isinstance(value[field], bool) or not isinstance(usage[field], bool):
            raise ValueError(f"{field} must be a boolean")
        if field in {"ground_truth_used_for_prediction", "ground_truth_used_for_parameter_fitting"} and (
            value[field] or usage[field]
        ):
            raise ValueError("ground truth cannot be used for prediction or parameter fitting")
        if value[field] != usage[field]:
            raise ValueError(f"{field} disagrees between direct and nested fields")
    if value["ground_truth_usage_scope"] != usage["ground_truth_usage_scope"]:
        raise ValueError("ground truth usage scope disagrees between direct and nested fields")
    if value["ground_truth_usage_scope"] not in {
        _GROUND_TRUTH_NONE_SCOPE,
        GROUND_TRUTH_POST_ANALYSIS_SCOPE,
    }:
        raise ValueError("ground_truth_usage_scope is invalid")

    prior = value["prior_evidence"]
    if not isinstance(prior, Mapping):
        raise ValueError("prior evidence must be a mapping")
    _require_exact_keys(prior, _PRIOR_EVIDENCE_FIELDS, "prior evidence")
    hashes = prior["receipt_sha256"]
    if not isinstance(hashes, list):
        raise ValueError("prior evidence receipt_sha256 must be a list")
    for index, item in enumerate(hashes):
        _require_hash(item, f"prior evidence receipt_sha256[{index}]")
    has_prior = bool(hashes)
    if value["ground_truth_used_for_method_family_selection"] != has_prior:
        raise ValueError("prior evidence and method-family selection flag disagree")
    expected_scope = GROUND_TRUTH_POST_ANALYSIS_SCOPE if has_prior else _GROUND_TRUTH_NONE_SCOPE
    if value["ground_truth_usage_scope"] != expected_scope:
        raise ValueError("prior evidence and ground truth usage scope disagree")
    experiment_prior = experiment["prior_evidence_receipt_hash"]
    if has_prior and experiment_prior not in hashes:
        raise ValueError("experiment prior evidence hash is not in prior evidence")
    if not has_prior and experiment_prior is not None:
        raise ValueError("experiment prior evidence hash exists without prior evidence")
    if value["selection_lock_id"] != recompute_selection_lock_id(value):
        raise ValueError("selection_lock_id does not match canonical payload")
    return value


def _config_candidates(lock_path: Path, config_relative_path: str) -> list[Path]:
    relative = Path(config_relative_path)
    candidates = [lock_path.parent / relative, _PROJECT_ROOT / relative]
    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        try:
            normalized = candidate.resolve(strict=False)
        except RuntimeError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            result.append(candidate)
    return result


def validate_selection_lock(path: Path) -> dict[str, object]:
    """Read, canonicalize, and validate a persisted selection lock."""

    path = Path(path)
    if path.is_symlink():
        raise ValueError("selection lock symlink is forbidden")
    if not path.is_file():
        raise FileNotFoundError(f"selection lock file is missing: {path.name}")
    raw = path.read_bytes()
    validated = _validate_lock_bytes(raw)
    _validate_config_bytes(path, validated)
    return validated


def _validate_lock_bytes(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("selection lock is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("selection lock JSON must be an object")
    validated = validate_selection_lock_payload(payload)
    if raw.decode("utf-8") != canonical_lock_json(validated):
        raise ValueError("selection lock bytes are not canonical JSON")
    return validated


def _validate_config_bytes(path: Path, validated: Mapping[str, object]) -> None:
    config_path = validated["config_relative_path"]
    assert isinstance(config_path, str)
    matching = [candidate for candidate in _config_candidates(path, config_path) if candidate.is_file()]
    if not matching:
        raise FileNotFoundError("selection lock config bytes could not be located for validation")
    actual = _sha256_file(matching[0])
    if actual != validated["config_sha256"]:
        raise ValueError("selection lock config bytes do not match config_sha256")


def _open_safe_parent(path: Path) -> tuple[int, str]:
    """Open every parent component with no-follow dirfd-relative operations."""

    path = Path(path)
    target_name = path.name
    if not target_name or target_name in {".", ".."}:
        raise ValueError("selection lock target must have a filename")
    absolute_parent = path.parent if path.is_absolute() else Path.cwd() / path.parent
    root = Path(absolute_parent.anchor)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(str(root), directory_flags)
    try:
        relative_parent = absolute_parent.relative_to(root)
        for component in relative_parent.parts:
            try:
                child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o755, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                try:
                    child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
                except OSError as exc:
                    if exc.errno in {ELOOP, ENOTDIR}:
                        raise ValueError(
                            f"selection lock parent symlink or non-directory: {component}",
                        ) from exc
                    raise
            except OSError as exc:
                if exc.errno in {ELOOP, ENOTDIR}:
                    raise ValueError(f"selection lock parent symlink or non-directory: {component}") from exc
                raise
            os.close(parent_fd)
            parent_fd = child_fd
    except BaseException:
        os.close(parent_fd)
        raise
    return parent_fd, target_name


def _open_temp_at(parent_fd: int, target_name: str) -> tuple[int, str]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(100):
        temporary_name = f".{target_name}.{secrets.token_hex(12)}"
        try:
            temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return temporary_fd, temporary_name
    raise FileExistsError("could not create a private selection-lock temporary file")


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("selection lock temporary write made no progress")
        offset += written


def _read_fd_bytes(fd: int) -> bytes:
    size = os.fstat(fd).st_size
    if size < 0:
        raise ValueError("selection lock file size is invalid")
    raw = os.pread(fd, size + 1, 0)
    if len(raw) != size:
        raise ValueError("selection lock file changed while being read")
    return raw


def write_selection_lock(path: Path, payload: Mapping[str, object]) -> Path:
    """Create a selection lock exactly once using a failure-atomic publication."""

    path = Path(path)
    validated = validate_selection_lock_payload(payload)
    encoded = canonical_lock_json(validated).encode("utf-8")
    parent_fd, target_name = _open_safe_parent(path)
    temporary_fd = -1
    temporary_name: str | None = None
    try:
        try:
            os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"selection lock target already exists: {target_name}")
        temporary_fd, temporary_name = _open_temp_at(parent_fd, target_name)
        _write_all(temporary_fd, encoded)
        os.fsync(temporary_fd)
        prepublish = _validate_lock_bytes(_read_fd_bytes(temporary_fd))
        if canonical_lock_json(prepublish) != encoded.decode("utf-8"):
            raise ValueError("selection lock prepublish canonical bytes changed")
        # Hard-link publication is atomic and refuses to replace an existing
        # final file, unlike os.replace().
        os.link(
            temporary_name,
            target_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        os.fsync(parent_fd)
        os.unlink(temporary_name, dir_fd=parent_fd)
        temporary_name = None
        os.fsync(parent_fd)
        final_fd = os.open(
            target_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            reread = _validate_lock_bytes(_read_fd_bytes(final_fd))
        finally:
            os.close(final_fd)
        _validate_config_bytes(path, reread)
        if canonical_lock_json(reread) != encoded.decode("utf-8"):
            raise ValueError("selection lock post-write canonical bytes changed")
    except BaseException:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        # A valid final artifact is never removed after publication; this
        # preserves write-once semantics when post-publication verification
        # fails for an external reason.
        raise
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        os.close(parent_fd)
    return path


__all__ = [
    "GROUND_TRUTH_POST_ANALYSIS_SCOPE",
    "LOCK_SCHEMA_VERSION",
    "PANEL_STATUS",
    "PANEL_V1",
    "ExperimentSpec",
    "build_selection_lock",
    "canonical_json",
    "canonical_lock_json",
    "recompute_selection_lock_id",
    "validate_selection_lock",
    "validate_selection_lock_payload",
    "write_selection_lock",
]
