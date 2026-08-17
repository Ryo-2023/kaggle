"""Research-only preflight contracts for frozen Wave6 residual learning.

The module closes provenance and masking before any residual optimizer is
allowed to run.  It intentionally does not import the V4 trainer, actor pool,
CABT evaluator, or checkpoint loader.  A future research runner may consume
the manifest and mask summary, but the manifest itself grants no training,
promotion, or long-run authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType


PREFLIGHT_SCHEMA_V1 = "specialist-frozen-wave6-residual-preflight-v1"
SEED_DOMAIN_SCHEMA_V1 = "specialist-frozen-wave6-residual-seed-domain-v1"
MASK_SCHEMA_V1 = "specialist-frozen-wave6-residual-mask-v1"
PREFLIGHT_STATUS_V1 = "PREFLIGHT_ONLY_NOT_EXECUTED"
_HEX64 = frozenset("0123456789abcdef")


class FrozenResidualPreflightError(ValueError):
    """Raised when a residual provenance or mask contract is not closed."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise FrozenResidualPreflightError(f"{field} must be a lowercase SHA-256")
    return value


def _nonempty_text(value: object, *, field: str) -> str:
    if type(value) is not str or not value:
        raise FrozenResidualPreflightError(f"{field} must be a nonempty string")
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FrozenResidualPreflightError("preflight payload is not canonical JSON") from exc


def _object_sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise FrozenResidualPreflightError(f"source is not a regular non-symlink file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise FrozenResidualPreflightError(f"source cannot be read: {path}") from exc
    return digest.hexdigest()


def _closed_mapping(value: object, expected: set[str], *, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise FrozenResidualPreflightError(f"{field} must be a JSON object")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        raise FrozenResidualPreflightError(
            f"{field} has an open schema (missing={missing}, unknown={unknown})"
        )
    return value


@dataclass(frozen=True, slots=True)
class Wave6ProvenanceV1:
    """Hash-bound provenance for one corresponding Wave6 training seed."""

    seed: int
    checkpoint_path: str
    checkpoint_file_sha256: str
    checkpoint_tensor_state_sha256: str
    screen_path: str
    screen_file_sha256: str
    transitions_path: str
    transitions_file_sha256: str
    subject_deck_sha256: str
    partition: str = "train"

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed not in {0, 1}:
            raise FrozenResidualPreflightError("Wave6 seed must be exactly 0 or 1")
        for field in (
            "checkpoint_path", "screen_path", "transitions_path",
        ):
            _nonempty_text(getattr(self, field), field=field)
        for field in (
            "checkpoint_file_sha256", "checkpoint_tensor_state_sha256",
            "screen_file_sha256", "transitions_file_sha256", "subject_deck_sha256",
        ):
            _sha(getattr(self, field), field=field)
        if self.partition != "train":
            raise FrozenResidualPreflightError("residual known-domain source must be train partition")

    def to_dict(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_file_sha256": self.checkpoint_file_sha256,
            "checkpoint_tensor_state_sha256": self.checkpoint_tensor_state_sha256,
            "screen_path": self.screen_path,
            "screen_file_sha256": self.screen_file_sha256,
            "transitions_path": self.transitions_path,
            "transitions_file_sha256": self.transitions_file_sha256,
            "subject_deck_sha256": self.subject_deck_sha256,
            "partition": self.partition,
        }


def verify_wave6_provenance_files_v1(provenance: Wave6ProvenanceV1) -> None:
    """Verify all local source paths against a typed provenance record."""
    if type(provenance) is not Wave6ProvenanceV1:
        raise FrozenResidualPreflightError("provenance must be exact Wave6ProvenanceV1")
    checks = (
        (provenance.checkpoint_path, provenance.checkpoint_file_sha256, "checkpoint"),
        (provenance.screen_path, provenance.screen_file_sha256, "screen"),
        (provenance.transitions_path, provenance.transitions_file_sha256, "transitions"),
    )
    for raw_path, expected, label in checks:
        actual = _file_sha(Path(raw_path))
        if actual != expected:
            raise FrozenResidualPreflightError(f"Wave6 {label} SHA-256 does not match provenance")


@dataclass(frozen=True, slots=True)
class SeedKnownDomainManifestV1:
    """Known public context/action keys for one seed and one train partition."""

    schema_version: str
    provenance: Wave6ProvenanceV1
    transition_count: int
    prefix_count: int
    context_ids: tuple[str, ...]
    action_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SEED_DOMAIN_SCHEMA_V1:
            raise FrozenResidualPreflightError("seed domain schema is invalid")
        if type(self.provenance) is not Wave6ProvenanceV1:
            raise FrozenResidualPreflightError("seed domain provenance is invalid")
        for field in ("transition_count", "prefix_count"):
            value = getattr(self, field)
            if type(value) is not int or value < 1:
                raise FrozenResidualPreflightError(f"{field} must be a positive int")
        if self.prefix_count < self.transition_count:
            raise FrozenResidualPreflightError("prefix_count cannot be below transition_count")
        for field in ("context_ids", "action_keys"):
            values = getattr(self, field)
            if type(values) is not tuple or not values:
                raise FrozenResidualPreflightError(f"{field} must be a nonempty tuple")
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise FrozenResidualPreflightError(f"{field} must be sorted and duplicate-free")
            for value in values:
                _sha(value, field=f"{field}[]")

    @property
    def context_count(self) -> int:
        return len(self.context_ids)

    @property
    def action_count(self) -> int:
        return len(self.action_keys)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "provenance": self.provenance.to_dict(),
            "transition_count": self.transition_count,
            "prefix_count": self.prefix_count,
            "context_ids": list(self.context_ids),
            "action_keys": list(self.action_keys),
        }


def build_seed_known_manifest_v1(
    provenance: Wave6ProvenanceV1,
    *,
    context_ids: Iterable[str],
    action_keys: Iterable[str],
    transition_count: int,
    prefix_count: int,
) -> SeedKnownDomainManifestV1:
    if type(provenance) is not Wave6ProvenanceV1:
        raise FrozenResidualPreflightError("provenance must be exact Wave6ProvenanceV1")
    try:
        contexts = tuple(sorted(set(context_ids)))
        actions = tuple(sorted(set(action_keys)))
    except TypeError as exc:
        raise FrozenResidualPreflightError("context/action keys must be iterable") from exc
    return SeedKnownDomainManifestV1(
        schema_version=SEED_DOMAIN_SCHEMA_V1,
        provenance=provenance,
        transition_count=transition_count,
        prefix_count=prefix_count,
        context_ids=contexts,
        action_keys=actions,
    )


@dataclass(frozen=True, slots=True)
class FrozenResidualPreflightManifestV1:
    """Two-seed, diagnostic-only manifest consumed before a residual pilot."""

    schema_version: str
    status: str
    subject_deck_sha256: str
    seeds: tuple[SeedKnownDomainManifestV1, ...]
    promotion_authority: bool = False
    longrun_allowed: bool = False
    training_permitted: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != PREFLIGHT_SCHEMA_V1 or self.status != PREFLIGHT_STATUS_V1:
            raise FrozenResidualPreflightError("preflight schema/status is not diagnostic-only")
        _sha(self.subject_deck_sha256, field="subject_deck_sha256")
        if type(self.seeds) is not tuple or tuple(item.provenance.seed for item in self.seeds) != (0, 1):
            raise FrozenResidualPreflightError("preflight must contain seed0 then seed1 exactly")
        if any(type(item) is not SeedKnownDomainManifestV1 for item in self.seeds):
            raise FrozenResidualPreflightError("preflight seed domains are invalid")
        if any(item.provenance.subject_deck_sha256 != self.subject_deck_sha256 for item in self.seeds):
            raise FrozenResidualPreflightError("seed provenance subject deck differs from manifest")
        transition_sources = [item.provenance.transitions_file_sha256 for item in self.seeds]
        if len(set(transition_sources)) != len(transition_sources):
            raise FrozenResidualPreflightError("seed transition source hashes must be distinct")
        for field in ("promotion_authority", "longrun_allowed", "training_permitted"):
            if getattr(self, field) is not False:
                raise FrozenResidualPreflightError(f"preflight cannot grant {field}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "subject_deck_sha256": self.subject_deck_sha256,
            "promotion_authority": self.promotion_authority,
            "longrun_allowed": self.longrun_allowed,
            "training_permitted": self.training_permitted,
            "seeds": [item.to_dict() for item in self.seeds],
        }


def build_frozen_residual_preflight_manifest_v1(
    seeds: Sequence[SeedKnownDomainManifestV1],
    *,
    subject_deck_sha256: str,
) -> FrozenResidualPreflightManifestV1:
    if type(seeds) not in (tuple, list) or any(type(item) is not SeedKnownDomainManifestV1 for item in seeds):
        raise FrozenResidualPreflightError("seeds must contain typed seed domain manifests")
    return FrozenResidualPreflightManifestV1(
        schema_version=PREFLIGHT_SCHEMA_V1,
        status=PREFLIGHT_STATUS_V1,
        subject_deck_sha256=_sha(subject_deck_sha256, field="subject_deck_sha256"),
        seeds=tuple(seeds),
    )


def _provenance_from_mapping(value: object) -> Wave6ProvenanceV1:
    fields = {
        "seed", "checkpoint_path", "checkpoint_file_sha256", "checkpoint_tensor_state_sha256",
        "screen_path", "screen_file_sha256", "transitions_path", "transitions_file_sha256",
        "subject_deck_sha256", "partition",
    }
    payload = _closed_mapping(value, fields, field="seed provenance")
    return Wave6ProvenanceV1(**payload)  # type: ignore[arg-type]


def _seed_domain_from_mapping(value: object) -> SeedKnownDomainManifestV1:
    payload = _closed_mapping(
        value,
        {"schema_version", "provenance", "transition_count", "prefix_count", "context_ids", "action_keys"},
        field="seed domain",
    )
    contexts = payload["context_ids"]
    actions = payload["action_keys"]
    if type(contexts) is not list or type(actions) is not list:
        raise FrozenResidualPreflightError("seed domain key sets must be JSON lists")
    return SeedKnownDomainManifestV1(
        schema_version=payload["schema_version"],  # type: ignore[arg-type]
        provenance=_provenance_from_mapping(payload["provenance"]),
        transition_count=payload["transition_count"],  # type: ignore[arg-type]
        prefix_count=payload["prefix_count"],  # type: ignore[arg-type]
        context_ids=tuple(contexts),
        action_keys=tuple(actions),
    )


def load_frozen_residual_preflight_manifest_v1(
    value: Mapping[str, object] | Path | str,
    *,
    expected_sha256: str | None = None,
    verify_files: bool = False,
) -> FrozenResidualPreflightManifestV1:
    """Load and validate the closed two-seed manifest.

    ``verify_files`` is opt-in so unit tests can use sealed paths; production
    research runners should set it when source files are available locally.
    """
    actual_sha: str | None = None
    if isinstance(value, Mapping):
        parsed = dict(value)
    else:
        path = Path(value)
        try:
            raw = path.read_bytes()
            actual_sha = hashlib.sha256(raw).hexdigest()
            parsed = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FrozenResidualPreflightError("preflight manifest cannot be read as JSON") from exc
    if type(parsed) is not dict:
        raise FrozenResidualPreflightError("preflight manifest must be a JSON object")
    if expected_sha256 is not None:
        expected = _sha(expected_sha256, field="expected preflight SHA-256")
        if actual_sha is None or actual_sha != expected:
            raise FrozenResidualPreflightError("preflight manifest SHA-256 mismatch")
    payload = _closed_mapping(
        parsed,
        {"schema_version", "status", "subject_deck_sha256", "promotion_authority", "longrun_allowed", "training_permitted", "seeds"},
        field="preflight manifest",
    )
    seeds = payload["seeds"]
    if type(seeds) is not list:
        raise FrozenResidualPreflightError("preflight seeds must be a JSON list")
    manifest = FrozenResidualPreflightManifestV1(
        schema_version=payload["schema_version"],  # type: ignore[arg-type]
        status=payload["status"],  # type: ignore[arg-type]
        subject_deck_sha256=payload["subject_deck_sha256"],  # type: ignore[arg-type]
        seeds=tuple(_seed_domain_from_mapping(item) for item in seeds),
        promotion_authority=payload["promotion_authority"],  # type: ignore[arg-type]
        longrun_allowed=payload["longrun_allowed"],  # type: ignore[arg-type]
        training_permitted=payload["training_permitted"],  # type: ignore[arg-type]
    )
    if verify_files:
        for seed in manifest.seeds:
            verify_wave6_provenance_files_v1(seed.provenance)
    return manifest


@dataclass(frozen=True, slots=True)
class ResidualMaskRowV1:
    """One recurrent prefix mask row; context always advances the hidden state."""

    context_id: str
    eligible: bool
    supervision_weight: float
    recurrent_context: bool = True

    def __post_init__(self) -> None:
        _sha(self.context_id, field="mask context_id")
        if type(self.eligible) is not bool or type(self.recurrent_context) is not bool:
            raise FrozenResidualPreflightError("mask booleans are invalid")
        if not self.recurrent_context:
            raise FrozenResidualPreflightError("every mask row must advance recurrent context")
        if type(self.supervision_weight) not in (int, float) or type(self.supervision_weight) is bool or not math.isfinite(float(self.supervision_weight)):
            raise FrozenResidualPreflightError("supervision_weight must be finite")
        weight = float(self.supervision_weight)
        if weight < 0.0 or weight > 1.0:
            raise FrozenResidualPreflightError("supervision_weight must be in [0, 1]")
        if not self.eligible and weight != 0.0:
            raise FrozenResidualPreflightError("context-only row must have zero supervision weight")
        if self.eligible and weight <= 0.0:
            raise FrozenResidualPreflightError("eligible row must have positive supervision weight")


@dataclass(frozen=True, slots=True)
class ResidualMaskAggregateV1:
    schema_version: str
    total_rows: int
    context_only_rows: int
    loss_bearing_rows: int
    denominator_rows: int
    effective_loss_mass: float
    weighted_loss_sum: float

    def __post_init__(self) -> None:
        if self.schema_version != MASK_SCHEMA_V1:
            raise FrozenResidualPreflightError("mask aggregate schema is invalid")
        if any(type(getattr(self, field)) is not int or getattr(self, field) < 0 for field in ("total_rows", "context_only_rows", "loss_bearing_rows", "denominator_rows")):
            raise FrozenResidualPreflightError("mask aggregate counts are invalid")
        if self.context_only_rows + self.loss_bearing_rows != self.total_rows or self.denominator_rows != self.loss_bearing_rows:
            raise FrozenResidualPreflightError("mask aggregate counts do not close")
        for field in ("effective_loss_mass", "weighted_loss_sum"):
            value = getattr(self, field)
            if type(value) is not float or not math.isfinite(value) or value < 0.0:
                raise FrozenResidualPreflightError(f"mask aggregate {field} is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "total_rows": self.total_rows,
            "context_only_rows": self.context_only_rows,
            "loss_bearing_rows": self.loss_bearing_rows,
            "denominator_rows": self.denominator_rows,
            "effective_loss_mass": self.effective_loss_mass,
            "weighted_loss_sum": self.weighted_loss_sum,
        }


def aggregate_residual_mask_v1(
    rows: Sequence[ResidualMaskRowV1],
    *,
    loss_terms: Sequence[float] | None = None,
) -> ResidualMaskAggregateV1:
    if type(rows) not in (tuple, list) or not rows or any(type(row) is not ResidualMaskRowV1 for row in rows):
        raise FrozenResidualPreflightError("mask rows must be a nonempty tuple/list of typed rows")
    if loss_terms is not None:
        if type(loss_terms) not in (tuple, list) or len(loss_terms) != len(rows):
            raise FrozenResidualPreflightError("loss_terms must align one-to-one with mask rows")
        if any(type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)) or float(value) < 0.0 for value in loss_terms):
            raise FrozenResidualPreflightError("loss_terms must be finite nonnegative values")
    context_only = sum(not row.eligible for row in rows)
    loss_bearing = len(rows) - context_only
    mass = float(sum(float(row.supervision_weight) for row in rows))
    weighted = 0.0
    if loss_terms is not None:
        weighted = float(sum(float(term) * float(row.supervision_weight) for term, row in zip(loss_terms, rows, strict=True)))
    return ResidualMaskAggregateV1(
        schema_version=MASK_SCHEMA_V1,
        total_rows=len(rows),
        context_only_rows=context_only,
        loss_bearing_rows=loss_bearing,
        denominator_rows=loss_bearing,
        effective_loss_mass=mass,
        weighted_loss_sum=weighted,
    )


__all__ = [
    "PREFLIGHT_SCHEMA_V1",
    "SEED_DOMAIN_SCHEMA_V1",
    "MASK_SCHEMA_V1",
    "PREFLIGHT_STATUS_V1",
    "FrozenResidualPreflightError",
    "Wave6ProvenanceV1",
    "SeedKnownDomainManifestV1",
    "FrozenResidualPreflightManifestV1",
    "ResidualMaskRowV1",
    "ResidualMaskAggregateV1",
    "verify_wave6_provenance_files_v1",
    "build_seed_known_manifest_v1",
    "build_frozen_residual_preflight_manifest_v1",
    "load_frozen_residual_preflight_manifest_v1",
    "aggregate_residual_mask_v1",
]
