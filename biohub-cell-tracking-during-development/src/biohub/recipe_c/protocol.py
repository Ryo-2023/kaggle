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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path, PureWindowsPath

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


def _require_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _require_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be a relative path")
    return value


def _looks_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _assert_no_forbidden_paths(value: object, location: str = "lock") -> None:
    """Reject absolute, credential, and ground-truth paths recursively."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).lower()
            child = f"{location}.{key}"
            if isinstance(item, str):
                lowered = item.lower()
                if _looks_absolute_path(item):
                    raise ValueError(f"absolute path is forbidden in {child}")
                if ".kaggle" in lowered or "kaggle.json" in lowered or "credential" in lowered:
                    raise ValueError(f"credential path is forbidden in {child}")
                if ("ground_truth" in key_text or key_text in {"gt", "gt_path"}) and key_text not in {
                    "ground_truth_usage_scope",
                }:
                    raise ValueError(f"ground truth path/value is forbidden in {child}")
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
        return path.name
    if not path.parts or ".." in path.parts:
        raise ValueError("config path must not escape its relative root")
    return path.as_posix()


def _load_prior_receipt(receipt: object, index: int) -> Mapping[str, object]:
    if isinstance(receipt, Path):
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"prior receipt {index} could not be read as JSON") from exc
    elif isinstance(receipt, Mapping):
        payload = dict(receipt)
    else:
        raise ValueError(f"prior receipt {index} must be a mapping or JSON path")
    if not isinstance(payload, Mapping):
        raise ValueError(f"prior receipt {index} must contain a JSON object")
    try:
        _canonical_json_mapping(payload)
    except ValueError as exc:
        raise ValueError(f"prior receipt {index} is not canonical JSON-safe") from exc
    return payload


def _validate_strong_prior_receipt(receipt: Mapping[str, object], index: int) -> str:
    """Require enough schema/order evidence before accepting a prior receipt."""

    schema_version = receipt.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise ValueError(f"prior receipt {index} has no strong schema_version")
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

    if value["schema_version"] != LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported selection lock schema_version")
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
    candidates = [lock_path.parent / relative, Path.cwd() / relative]
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
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("selection lock is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("selection lock JSON must be an object")
    validated = validate_selection_lock_payload(payload)
    if raw.decode("utf-8") != canonical_lock_json(validated):
        raise ValueError("selection lock bytes are not canonical JSON")
    config_path = validated["config_relative_path"]
    assert isinstance(config_path, str)
    matching = [candidate for candidate in _config_candidates(path, config_path) if candidate.is_file()]
    if not matching:
        raise FileNotFoundError("selection lock config bytes could not be located for validation")
    actual = _sha256_file(matching[0])
    if actual != validated["config_sha256"]:
        raise ValueError("selection lock config bytes do not match config_sha256")
    return validated


def write_selection_lock(path: Path, payload: Mapping[str, object]) -> Path:
    """Create a selection lock exactly once and verify the written bytes."""

    path = Path(path)
    validated = validate_selection_lock_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"selection lock target already exists: {path.name}")
    encoded = canonical_lock_json(validated).encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
    except FileExistsError:
        raise
    reread = validate_selection_lock(path)
    if canonical_lock_json(reread) != encoded.decode("utf-8"):
        raise ValueError("selection lock post-write canonical bytes changed")
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
