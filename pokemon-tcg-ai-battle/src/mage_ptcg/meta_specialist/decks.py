"""Fixed-archetype deck registration and qualification contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from types import MappingProxyType
from typing import Callable, Collection, Mapping
from weakref import ReferenceType, ref

from mage_ptcg.deck_io import MAX_DECK_FILE_BYTES, parse_deck_csv_bytes, validate_deck
from mage_ptcg.exact_file import (
    ExactFileSnapshot,
    ExactFileSnapshotError,
    read_exact_regular_file,
    require_snapshot_path_unchanged,
)
from mage_ptcg.knowledge.model import deck_identity_from_card_ids


def content_id(domain: str, payload: object) -> str:
    """Repository-local copy of the continuous-league canonical content ID.

    Deck parsing is a lightweight import boundary and must not initialize the
    continuous-league package (which intentionally exposes heavyweight APIs).
    Keep the byte contract identical to ``continuous_league.contracts``.
    """
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical)
    return digest.hexdigest()


def require_sha256(value: str, field: str) -> str:
    """Validate the same lowercase SHA-256 representation used by deck locks."""
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


class ArchetypeRegistryError(ValueError):
    """Raised when the fixed archetype registry is malformed."""


class DeckQualificationError(ValueError):
    """Raised when a seed deck cannot be safely admitted to a lane."""


class DeckLineageError(ValueError):
    """Raised when a deck lock or its continuation lineage is invalid."""


@dataclass(frozen=True, slots=True)
class ArchetypeSpec:
    """One fixed local optimization lane."""

    runtime_id: str
    aliases: tuple[str, ...]
    core_card_ids: tuple[int, ...]
    candidate_status: str


@dataclass(frozen=True, slots=True)
class ArchetypeRegistry:
    """The ordered lane registry and its deterministic resource priorities."""

    archetypes: Mapping[str, ArchetypeSpec]
    primary_order: tuple[str, ...]
    replacement_order: tuple[str, ...]


_TOP_LEVEL_KEYS = {
    "schema_version",
    "primary_order",
    "replacement_order",
    "archetypes",
}
_RECORD_KEYS = {"runtime_id", "aliases", "core_card_ids", "candidate_status"}
_CANDIDATE_STATUSES = {
    "registered_unqualified",
    "qualified_not_trained",
    "trained_champion",
    "withdrawn",
}


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArchetypeRegistryError(f"{field} must be a non-empty string")
    return value


def _require_string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ArchetypeRegistryError(f"{field} must be a list")
    values = tuple(_require_string(item, f"{field}[{index}]") for index, item in enumerate(value))
    if len(values) != len(set(values)):
        raise ArchetypeRegistryError(f"{field} must not contain duplicates")
    return values


def _parse_archetype(record: object, index: int) -> ArchetypeSpec:
    if not isinstance(record, dict):
        raise ArchetypeRegistryError(f"archetypes[{index}] must be an object")
    unknown_keys = set(record).difference(_RECORD_KEYS)
    missing_keys = _RECORD_KEYS.difference(record)
    if unknown_keys or missing_keys:
        raise ArchetypeRegistryError(
            f"archetypes[{index}] has invalid keys: unknown={sorted(unknown_keys)}, "
            f"missing={sorted(missing_keys)}"
        )
    runtime_id = _require_string(record["runtime_id"], f"archetypes[{index}].runtime_id")
    aliases = _require_string_list(record["aliases"], f"archetypes[{index}].aliases")
    core_values = record["core_card_ids"]
    if not isinstance(core_values, list) or not core_values:
        raise ArchetypeRegistryError(f"archetypes[{index}].core_card_ids must be a non-empty list")
    core_card_ids = tuple(core_values)
    if any(type(card_id) is not int or card_id <= 0 for card_id in core_card_ids):
        raise ArchetypeRegistryError(
            f"archetypes[{index}].core_card_ids must contain positive ints and not bool"
        )
    if tuple(sorted(core_card_ids)) != core_card_ids or len(core_card_ids) != len(set(core_card_ids)):
        raise ArchetypeRegistryError(
            f"archetypes[{index}].core_card_ids must be strictly sorted and unique"
        )
    candidate_status = _require_string(
        record["candidate_status"], f"archetypes[{index}].candidate_status"
    )
    if candidate_status not in _CANDIDATE_STATUSES:
        raise ArchetypeRegistryError(f"unsupported candidate_status: {candidate_status}")
    return ArchetypeSpec(runtime_id, aliases, core_card_ids, candidate_status)


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while rejecting decoder-dependent duplicate keys."""
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ArchetypeRegistryError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def load_archetype_registry(path: str | Path) -> ArchetypeRegistry:
    """Load the explicit, strict v1 registry from ``path``."""
    registry_path = Path(path)
    try:
        with registry_path.open(encoding="utf-8") as handle:
            document = json.load(handle, object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchetypeRegistryError(f"could not read archetype registry {registry_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ArchetypeRegistryError("archetype registry must be an object")
    unknown_keys = set(document).difference(_TOP_LEVEL_KEYS)
    missing_keys = _TOP_LEVEL_KEYS.difference(document)
    if unknown_keys or missing_keys:
        raise ArchetypeRegistryError(
            f"archetype registry has invalid keys: unknown={sorted(unknown_keys)}, "
            f"missing={sorted(missing_keys)}"
        )
    if document["schema_version"] != "meta-specialist-archetypes-v1":
        raise ArchetypeRegistryError("unsupported archetype registry schema_version")
    records = document["archetypes"]
    if not isinstance(records, list) or not records:
        raise ArchetypeRegistryError("archetypes must be a non-empty list")
    specs = tuple(_parse_archetype(record, index) for index, record in enumerate(records))
    runtime_ids = tuple(spec.runtime_id for spec in specs)
    if len(runtime_ids) != len(set(runtime_ids)):
        raise ArchetypeRegistryError("archetypes must not contain duplicate runtime IDs")
    aliases = tuple(alias for spec in specs for alias in spec.aliases)
    if len(aliases) != len(set(aliases)) or set(aliases).intersection(runtime_ids):
        raise ArchetypeRegistryError("archetypes must not contain duplicate aliases")
    primary_order = _require_string_list(document["primary_order"], "primary_order")
    replacement_order = _require_string_list(document["replacement_order"], "replacement_order")
    if set(primary_order).intersection(replacement_order):
        raise ArchetypeRegistryError("primary_order and replacement_order must not overlap")
    missing_priority_ids = set(primary_order).union(replacement_order).difference(runtime_ids)
    if missing_priority_ids:
        raise ArchetypeRegistryError(
            f"priority order references unregistered archetypes: {sorted(missing_priority_ids)}"
        )
    return ArchetypeRegistry(
        archetypes=MappingProxyType({spec.runtime_id: spec for spec in specs}),
        primary_order=primary_order,
        replacement_order=replacement_order,
    )


_ASSET_CLASSES = {"deck_only", "runnable_rule", "checkpoint_teacher"}
_USAGE_BOUNDARIES = {"local_eval_only", "teacher_only", "bundle_allowed"}
_SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
CabtLegality = Callable[[tuple[int, ...]], tuple[bool, str]]


@dataclass(frozen=True, slots=True)
class DeckAssetInput:
    """Immutable deck bytes plus provenance for one explicit source path."""

    asset_id: str
    archetype_id: str
    path: Path
    source_ref: str
    source_commit: str
    asset_class: str
    usage_boundary: str
    policy_compatibility: str
    card_database_version: str
    card_ids: tuple[int, ...]
    deck_identity: str
    deck_file_sha256: str
    deck_file_bytes: bytes = field(repr=False)

    @classmethod
    def from_snapshot(
        cls,
        *,
        asset_id: str,
        archetype_id: str,
        snapshot: ExactFileSnapshot,
        source_ref: str,
        source_commit: str,
        asset_class: str,
        usage_boundary: str,
        policy_compatibility: str,
        card_database_version: str,
    ) -> DeckAssetInput:
        """Build from the exact immutable bytes of one verified file snapshot."""
        if type(snapshot) is not ExactFileSnapshot:
            raise DeckQualificationError("deck snapshot must be an ExactFileSnapshot")
        if snapshot.size != len(snapshot.payload) or snapshot.size > MAX_DECK_FILE_BYTES:
            raise DeckQualificationError("deck snapshot has an invalid bounded byte size")
        exact_sha256 = hashlib.sha256(snapshot.payload).hexdigest()
        if snapshot.sha256 != exact_sha256:
            raise DeckQualificationError("deck snapshot SHA-256 does not bind its exact bytes")
        try:
            require_snapshot_path_unchanged(snapshot)
            card_ids = tuple(parse_deck_csv_bytes(snapshot.payload))
            deck_identity = deck_identity_from_card_ids(card_ids)
        except (ExactFileSnapshotError, ValueError) as exc:
            raise DeckQualificationError(
                f"invalid deck snapshot at {snapshot.path}: {exc}"
            ) from exc
        return cls(
            asset_id=asset_id,
            archetype_id=archetype_id,
            path=snapshot.path,
            source_ref=source_ref,
            source_commit=source_commit,
            asset_class=asset_class,
            usage_boundary=usage_boundary,
            policy_compatibility=policy_compatibility,
            card_database_version=card_database_version,
            card_ids=card_ids,
            deck_identity=deck_identity,
            deck_file_sha256=exact_sha256,
            deck_file_bytes=snapshot.payload,
        )

    @classmethod
    def from_path(
        cls,
        *,
        asset_id: str,
        archetype_id: str,
        path: str | Path,
        source_ref: str,
        source_commit: str,
        asset_class: str,
        usage_boundary: str,
        policy_compatibility: str,
        card_database_version: str,
    ) -> DeckAssetInput:
        """Read one explicit snapshot and preserve its bytes and multiset identity."""
        if path is None or not isinstance(path, (str, Path)) or (isinstance(path, str) and not path):
            raise DeckQualificationError("deck path must be explicit")
        deck_path = Path(path)
        try:
            snapshot = read_exact_regular_file(deck_path, max_bytes=MAX_DECK_FILE_BYTES)
        except ExactFileSnapshotError as exc:
            raise DeckQualificationError(f"could not snapshot deck at {deck_path}: {exc}") from exc
        asset = cls.from_snapshot(
            asset_id=asset_id,
            archetype_id=archetype_id,
            snapshot=snapshot,
            source_ref=source_ref,
            source_commit=source_commit,
            asset_class=asset_class,
            usage_boundary=usage_boundary,
            policy_compatibility=policy_compatibility,
            card_database_version=card_database_version,
        )
        try:
            require_snapshot_path_unchanged(snapshot)
        except ExactFileSnapshotError as exc:
            raise DeckQualificationError(f"deck snapshot path changed: {exc}") from exc
        return asset


@dataclass(frozen=True, slots=True, weakref_slot=True)
class QualifiedDeckAsset:
    """A fully validated 60-card seed eligible for a deck-policy race."""

    asset_id: str
    archetype_id: str
    card_ids: tuple[int, ...]
    deck_identity: str
    deck_file_sha256: str
    source_ref: str
    source_commit: str
    asset_class: str
    usage_boundary: str
    policy_compatibility: str
    card_database_version: str
    card_count: int
    cabt_legality_status: str
    cabt_legality_evidence: str
    _qualification_attestation: object = field(
        default=None, init=False, repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class _QualificationSnapshot:
    """All validated input state retained across the untrusted CABT callback."""

    asset_id: str
    archetype_id: str
    path: Path
    source_ref: str
    source_commit: str
    asset_class: str
    usage_boundary: str
    policy_compatibility: str
    card_database_version: str
    card_ids: tuple[int, ...]
    deck_identity: str
    deck_file_sha256: str
    deck_file_bytes: bytes
    file_snapshot: ExactFileSnapshot


_QUALIFICATION_ATTESTATION = object()
_QUALIFIED_ASSET_REGISTRY: dict[
    int, tuple[ReferenceType[QualifiedDeckAsset], str]
] = {}
_QUALIFIED_ASSET_REGISTRY_LOCK = Lock()


def _qualified_asset_fingerprint(asset: QualifiedDeckAsset) -> str:
    """Bind every public qualification field to one factory-issued capability."""
    return content_id(
        "meta-specialist-qualified-deck-attestation-v1",
        {
            "asset_id": asset.asset_id,
            "archetype_id": asset.archetype_id,
            "card_ids": list(asset.card_ids),
            "deck_identity": asset.deck_identity,
            "deck_file_sha256": asset.deck_file_sha256,
            "source_ref": asset.source_ref,
            "source_commit": asset.source_commit,
            "asset_class": asset.asset_class,
            "usage_boundary": asset.usage_boundary,
            "policy_compatibility": asset.policy_compatibility,
            "card_database_version": asset.card_database_version,
            "card_count": asset.card_count,
            "cabt_legality_status": asset.cabt_legality_status,
            "cabt_legality_evidence": asset.cabt_legality_evidence,
        },
    )


def _attest_qualified_deck_asset(asset: QualifiedDeckAsset) -> QualifiedDeckAsset:
    _require_qualified_asset_structure(asset)
    fingerprint = _qualified_asset_fingerprint(asset)
    object.__setattr__(asset, "_qualification_attestation", _QUALIFICATION_ATTESTATION)
    asset_id = id(asset)

    def release(
        finished_ref: ReferenceType[QualifiedDeckAsset], *, claimed_id: int = asset_id,
    ) -> None:
        with _QUALIFIED_ASSET_REGISTRY_LOCK:
            registered = _QUALIFIED_ASSET_REGISTRY.get(claimed_id)
            # Do not let a delayed weakref callback remove a newer asset after
            # numeric CPython object-ID reuse.
            if registered is not None and registered[0] is finished_ref:
                _QUALIFIED_ASSET_REGISTRY.pop(claimed_id, None)

    asset_ref = ref(asset, release)
    with _QUALIFIED_ASSET_REGISTRY_LOCK:
        previous = _QUALIFIED_ASSET_REGISTRY.get(asset_id)
        if previous is not None and previous[0]() is not None:
            raise DeckQualificationError("qualified deck identity registry collision")
        _QUALIFIED_ASSET_REGISTRY[asset_id] = (asset_ref, fingerprint)
    return asset


def require_qualified_deck_asset(asset: object) -> QualifiedDeckAsset:
    """Require the exact, unmodified object issued by :func:`qualify_deck_asset`.

    A dataclass constructor, ``replace``, copied private field, or post-factory
    ``object.__setattr__`` mutation cannot reproduce this process-local,
    object-bound qualification capability.
    """
    if type(asset) is not QualifiedDeckAsset:
        raise DeckQualificationError("qualified deck attestation requires exact QualifiedDeckAsset")
    _require_qualified_asset_structure(asset)
    with _QUALIFIED_ASSET_REGISTRY_LOCK:
        registered = _QUALIFIED_ASSET_REGISTRY.get(id(asset))
    if (
        asset._qualification_attestation is not _QUALIFICATION_ATTESTATION
        or registered is None
        or registered[0]() is not asset
    ):
        raise DeckQualificationError("qualified deck attestation was not factory-issued")
    try:
        fingerprint = _qualified_asset_fingerprint(asset)
    except (TypeError, ValueError) as exc:
        raise DeckQualificationError("qualified deck attestation fields are malformed") from exc
    if fingerprint != registered[1]:
        raise DeckQualificationError("qualified deck attestation no longer binds its fields")
    return asset


def _require_asset_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeckQualificationError(f"{field} must be a non-empty string")
    return value


def _validated_asset_cards(asset: DeckAssetInput, known_card_ids: Collection[int]) -> tuple[int, ...]:
    if known_card_ids is None:
        raise DeckQualificationError("known_card_ids is required")
    if not isinstance(asset.card_ids, tuple) or len(asset.card_ids) != 60:
        raise DeckQualificationError("deck must contain exactly 60 cards")
    if any(type(card_id) is not int or card_id <= 0 for card_id in asset.card_ids):
        raise DeckQualificationError("deck card IDs must be positive ints and not bool")
    if not isinstance(asset.deck_file_bytes, bytes):
        raise DeckQualificationError("deck asset must retain immutable exact file bytes")
    if len(asset.deck_file_bytes) > MAX_DECK_FILE_BYTES:
        raise DeckQualificationError("deck asset exact bytes exceed the bounded size limit")
    try:
        validate_deck(asset.card_ids, known_card_ids=known_card_ids)
        immutable_cards = tuple(
            parse_deck_csv_bytes(asset.deck_file_bytes, known_card_ids=known_card_ids)
        )
        immutable_identity = deck_identity_from_card_ids(immutable_cards)
        immutable_sha256 = hashlib.sha256(asset.deck_file_bytes).hexdigest()
        current = read_exact_regular_file(asset.path, max_bytes=MAX_DECK_FILE_BYTES)
    except (ExactFileSnapshotError, OSError, ValueError) as exc:
        raise DeckQualificationError(f"deck validation failed: {exc}") from exc
    if asset.deck_file_bytes != current.payload:
        raise DeckQualificationError(
            "deck asset exact bytes or exact file SHA-256 do not match its current path snapshot"
        )
    if asset.card_ids != immutable_cards:
        raise DeckQualificationError("deck asset card IDs do not match its immutable exact bytes")
    if asset.deck_identity != immutable_identity:
        raise DeckQualificationError("deck asset multiset identity does not match its exact bytes")
    if asset.deck_file_sha256 != immutable_sha256 or asset.deck_file_sha256 != current.sha256:
        raise DeckQualificationError("deck asset exact file SHA-256 does not match its exact bytes")
    return asset.card_ids


def _capture_qualification_snapshot(
    asset: DeckAssetInput,
    known_card_ids: Collection[int],
) -> _QualificationSnapshot:
    """Validate and freeze every value that may cross CABT callback authority."""
    _validate_provenance(asset)
    if not isinstance(asset.path, Path):
        raise DeckQualificationError("deck asset path must be an exact Path")
    card_ids = _validated_asset_cards(asset, known_card_ids)
    try:
        file_snapshot = read_exact_regular_file(
            asset.path,
            max_bytes=MAX_DECK_FILE_BYTES,
        )
    except ExactFileSnapshotError as exc:
        raise DeckQualificationError(f"deck validation failed: {exc}") from exc
    if file_snapshot.payload != asset.deck_file_bytes:
        raise DeckQualificationError("deck asset exact bytes changed during validation")
    return _QualificationSnapshot(
        asset_id=asset.asset_id,
        archetype_id=asset.archetype_id,
        path=asset.path,
        source_ref=asset.source_ref,
        source_commit=asset.source_commit,
        asset_class=asset.asset_class,
        usage_boundary=asset.usage_boundary,
        policy_compatibility=asset.policy_compatibility,
        card_database_version=asset.card_database_version,
        card_ids=card_ids,
        deck_identity=asset.deck_identity,
        deck_file_sha256=asset.deck_file_sha256,
        deck_file_bytes=asset.deck_file_bytes,
        file_snapshot=file_snapshot,
    )


def _require_asset_unchanged_after_cabt(
    asset: DeckAssetInput,
    snapshot: _QualificationSnapshot,
) -> None:
    """Reject callback-visible input or source-path changes before issuance."""
    if (
        type(asset) is not DeckAssetInput
        or asset.asset_id != snapshot.asset_id
        or asset.archetype_id != snapshot.archetype_id
        or asset.path != snapshot.path
        or asset.source_ref != snapshot.source_ref
        or asset.source_commit != snapshot.source_commit
        or asset.asset_class != snapshot.asset_class
        or asset.usage_boundary != snapshot.usage_boundary
        or asset.policy_compatibility != snapshot.policy_compatibility
        or asset.card_database_version != snapshot.card_database_version
        or asset.card_ids != snapshot.card_ids
        or asset.deck_identity != snapshot.deck_identity
        or asset.deck_file_sha256 != snapshot.deck_file_sha256
        or asset.deck_file_bytes != snapshot.deck_file_bytes
    ):
        raise DeckQualificationError("deck asset changed during CABT legality callback")
    try:
        require_snapshot_path_unchanged(snapshot.file_snapshot)
    except ExactFileSnapshotError as exc:
        raise DeckQualificationError(
            "deck asset path changed during CABT legality callback"
        ) from exc


def _require_qualified_asset_structure(asset: QualifiedDeckAsset) -> None:
    """Replay non-secret deck invariants before a factory seal is trusted."""
    try:
        if type(asset.card_ids) is not tuple or len(asset.card_ids) != 60:
            raise DeckQualificationError("card_ids must be exactly 60 cards")
        if any(type(card_id) is not int or card_id <= 0 for card_id in asset.card_ids):
            raise DeckQualificationError("card_ids must be positive exact ints")
        if type(asset.card_count) is not int or asset.card_count != len(asset.card_ids):
            raise DeckQualificationError("card_count does not match card_ids")
        if asset.deck_identity != deck_identity_from_card_ids(asset.card_ids):
            raise DeckQualificationError("deck_identity does not match card_ids")
        require_sha256(asset.deck_file_sha256, "deck_file_sha256")
        _validate_provenance(asset)  # type: ignore[arg-type]
        if asset.cabt_legality_status != "passed":
            raise DeckQualificationError("CABT legality status is not passed")
        if not isinstance(asset.cabt_legality_evidence, str) or not asset.cabt_legality_evidence.strip():
            raise DeckQualificationError("CABT legality evidence must be nonempty")
    except (DeckQualificationError, TypeError, ValueError) as exc:
        raise DeckQualificationError(
            f"qualified deck structural invariants failed: {exc}"
        ) from exc


def _validate_provenance(asset: DeckAssetInput) -> None:
    for field in (
        "asset_id",
        "archetype_id",
        "source_ref",
        "source_commit",
        "asset_class",
        "usage_boundary",
        "policy_compatibility",
        "card_database_version",
    ):
        _require_asset_string(getattr(asset, field), field)
    if _SOURCE_COMMIT_RE.fullmatch(asset.source_commit) is None:
        raise DeckQualificationError("source_commit must be a 40-character lowercase hex commit")
    if asset.asset_class not in _ASSET_CLASSES:
        raise DeckQualificationError("asset_class is not permitted")
    if asset.usage_boundary not in _USAGE_BOUNDARIES:
        raise DeckQualificationError("usage_boundary is not permitted")


def qualify_deck_asset(
    asset: DeckAssetInput,
    archetype: ArchetypeSpec,
    *,
    known_card_ids: Collection[int],
    cabt_legality: CabtLegality | None,
) -> QualifiedDeckAsset:
    """Fail closed unless provenance, card membership, core, and CABT all pass."""
    if not isinstance(asset, DeckAssetInput):
        raise DeckQualificationError("asset must be a DeckAssetInput")
    if not isinstance(archetype, ArchetypeSpec):
        raise DeckQualificationError("archetype must be an ArchetypeSpec")
    snapshot = _capture_qualification_snapshot(asset, known_card_ids)
    if snapshot.archetype_id != archetype.runtime_id:
        raise DeckQualificationError("asset archetype_id does not match archetype")
    missing_core = set(archetype.core_card_ids).difference(snapshot.card_ids)
    if missing_core:
        raise DeckQualificationError(f"deck is missing core card IDs: {sorted(missing_core)}")
    if cabt_legality is None or not callable(cabt_legality):
        raise DeckQualificationError("CABT legality callback is required")
    try:
        cabt_result = cabt_legality(snapshot.card_ids)
    except Exception as exc:
        raise DeckQualificationError(f"CABT legality callback failed: {exc}") from exc
    _require_asset_unchanged_after_cabt(asset, snapshot)
    if (
        not isinstance(cabt_result, tuple)
        or len(cabt_result) != 2
        or cabt_result[0] is not True
        or not isinstance(cabt_result[1], str)
        or not cabt_result[1].strip()
    ):
        raise DeckQualificationError("CABT legality must return (True, nonempty evidence)")
    return _attest_qualified_deck_asset(QualifiedDeckAsset(
        asset_id=snapshot.asset_id,
        archetype_id=snapshot.archetype_id,
        card_ids=snapshot.card_ids,
        deck_identity=snapshot.deck_identity,
        deck_file_sha256=snapshot.deck_file_sha256,
        source_ref=snapshot.source_ref,
        source_commit=snapshot.source_commit,
        asset_class=snapshot.asset_class,
        usage_boundary=snapshot.usage_boundary,
        policy_compatibility=snapshot.policy_compatibility,
        card_database_version=snapshot.card_database_version,
        card_count=len(snapshot.card_ids),
        cabt_legality_status="passed",
        cabt_legality_evidence=cabt_result[1],
    ))


def reject_duplicate_seed_decks(assets: object) -> None:
    """Reject provenance-distinct entries which represent the same deck multiset."""
    try:
        values = tuple(assets)
    except TypeError as exc:
        raise DeckQualificationError("qualified assets must be iterable") from exc
    seen: set[str] = set()
    for asset in values:
        if not isinstance(asset, QualifiedDeckAsset):
            raise DeckQualificationError("qualified assets must contain QualifiedDeckAsset values")
        if asset.deck_identity in seen:
            raise DeckQualificationError(f"duplicate canonical deck: {asset.deck_identity}")
        seen.add(asset.deck_identity)


_DECK_IDENTITY_RE = re.compile(r"deck-[0-9a-f]{20}")


@dataclass(frozen=True, slots=True)
class DeckLockDecision:
    """The deterministic result of a fixed-budget pre-curriculum deck race."""

    archetype_id: str
    selected_deck_identity: str
    compared_deck_identities: tuple[str, ...]
    foundation_init_id: str
    joint_race_schedule_id: str
    equal_transition_budget: int
    deck_lock_id: str
    policy_lineage_id: str

    def __post_init__(self) -> None:
        _validate_deck_lock_integrity(self)


def _require_deck_identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _DECK_IDENTITY_RE.fullmatch(value) is None:
        raise DeckLineageError(f"{field} must be a canonical deck identity")
    return value


def _require_lock_sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise DeckLineageError(f"{field} must be a lowercase SHA-256 hex digest")
    try:
        return require_sha256(value, field)
    except ValueError as exc:
        raise DeckLineageError(str(exc)) from exc


def _canonical_deck_lock_payload(
    *,
    archetype_id: object,
    selected_deck_identity: object,
    compared_deck_identities: object,
    foundation_init_id: object,
    joint_race_schedule_id: object,
    equal_transition_budget: object,
) -> dict[str, object]:
    """Validate the complete canonical lock payload without its derived IDs."""
    if not isinstance(archetype_id, str) or not archetype_id:
        raise DeckLineageError("archetype_id must be a non-empty string")
    selected = _require_deck_identity(selected_deck_identity, "selected_deck_identity")
    if not isinstance(compared_deck_identities, tuple):
        raise DeckLineageError("compared_deck_identities must be a tuple")
    compared = tuple(
        _require_deck_identity(value, "compared_deck_identities")
        for value in compared_deck_identities
    )
    normalized_compared = tuple(sorted(set(compared)))
    if not normalized_compared:
        raise DeckLineageError("compared_deck_identities must contain at least one deck")
    if compared != normalized_compared:
        raise DeckLineageError("compared_deck_identities must be sorted and deduplicated")
    if selected not in normalized_compared:
        raise DeckLineageError("selected_deck_identity must be among compared_deck_identities")
    if type(equal_transition_budget) is not int or equal_transition_budget <= 0:
        raise DeckLineageError("equal_transition_budget must be a positive int and not bool")
    foundation = _require_lock_sha256(foundation_init_id, "foundation_init_id")
    schedule = _require_lock_sha256(joint_race_schedule_id, "joint_race_schedule_id")
    return {
        "archetype_id": archetype_id,
        "selected_deck_identity": selected,
        "compared_deck_identities": list(normalized_compared),
        "foundation_init_id": foundation,
        "joint_race_schedule_id": schedule,
        "equal_transition_budget": equal_transition_budget,
    }


def _validate_deck_lock_integrity(lock: object) -> None:
    """Reject malformed or content-ID-forged decisions at every trust boundary."""
    if not isinstance(lock, DeckLockDecision):
        raise DeckLineageError("lock must be a DeckLockDecision")
    try:
        identity_payload = _canonical_deck_lock_payload(
            archetype_id=lock.archetype_id,
            selected_deck_identity=lock.selected_deck_identity,
            compared_deck_identities=lock.compared_deck_identities,
            foundation_init_id=lock.foundation_init_id,
            joint_race_schedule_id=lock.joint_race_schedule_id,
            equal_transition_budget=lock.equal_transition_budget,
        )
        expected_deck_lock_id = content_id(
            "meta-specialist-deck-lock-v1", identity_payload
        )
        expected_policy_lineage_id = content_id(
            "meta-specialist-policy-lineage-v1",
            {"deck_lock_id": expected_deck_lock_id},
        )
        for field in ("deck_lock_id", "policy_lineage_id"):
            value = getattr(lock, field)
            if type(value) is not str:
                raise DeckLineageError(
                    f"{field} must be an exact lowercase SHA-256 string"
                )
            try:
                require_sha256(value, field)
            except ValueError as exc:
                raise DeckLineageError(str(exc)) from exc
        if (
            lock.deck_lock_id != expected_deck_lock_id
            or lock.policy_lineage_id != expected_policy_lineage_id
        ):
            raise DeckLineageError("derived IDs do not match the canonical payload")
    except DeckLineageError as exc:
        raise DeckLineageError(f"DeckLockDecision integrity violation: {exc}") from exc


def create_deck_lock(
    *,
    archetype_id: str,
    selected_deck_identity: str,
    compared_deck_identities: object,
    foundation_init_id: str,
    joint_race_schedule_id: str,
    equal_transition_budget: int,
) -> DeckLockDecision:
    """Create a content-addressed lock from only fair-race identity inputs."""
    selected = _require_deck_identity(selected_deck_identity, "selected_deck_identity")
    if isinstance(compared_deck_identities, str):
        raise DeckLineageError("compared_deck_identities must be an iterable of deck identities")
    try:
        compared = tuple(
            _require_deck_identity(value, "compared_deck_identities")
            for value in compared_deck_identities
        )
    except TypeError as exc:
        raise DeckLineageError("compared_deck_identities must be an iterable of deck identities") from exc
    normalized_compared = tuple(sorted(set(compared)))
    if not normalized_compared:
        raise DeckLineageError("compared_deck_identities must contain at least one deck")
    identity_payload = _canonical_deck_lock_payload(
        archetype_id=archetype_id,
        selected_deck_identity=selected,
        compared_deck_identities=normalized_compared,
        foundation_init_id=foundation_init_id,
        joint_race_schedule_id=joint_race_schedule_id,
        equal_transition_budget=equal_transition_budget,
    )
    deck_lock_id = content_id("meta-specialist-deck-lock-v1", identity_payload)
    policy_lineage_id = content_id(
        "meta-specialist-policy-lineage-v1", {"deck_lock_id": deck_lock_id}
    )
    return DeckLockDecision(
        archetype_id=identity_payload["archetype_id"],
        selected_deck_identity=identity_payload["selected_deck_identity"],
        compared_deck_identities=normalized_compared,
        foundation_init_id=identity_payload["foundation_init_id"],
        joint_race_schedule_id=identity_payload["joint_race_schedule_id"],
        equal_transition_budget=identity_payload["equal_transition_budget"],
        deck_lock_id=deck_lock_id,
        policy_lineage_id=policy_lineage_id,
    )


def require_lineage_deck(lock: DeckLockDecision, deck_identity: str) -> None:
    """Require a curriculum continuation to use the deck selected by its lock."""
    _validate_deck_lock_integrity(lock)
    candidate = _require_deck_identity(deck_identity, "deck_identity")
    if candidate != lock.selected_deck_identity:
        raise DeckLineageError("deck change requires a new branch")
