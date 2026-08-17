"""Strict provenance registry for fixed-lane seed deck candidates.

The registry records candidates only.  It does not grant training permission,
run CABT legality, or qualify a deck for a policy race.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from threading import Lock
from types import MappingProxyType
from typing import Callable, Collection, Mapping
from weakref import ReferenceType, ref

from mage_ptcg.continuous_league.contracts import content_id, require_sha256
from mage_ptcg.deck_io import MAX_DECK_FILE_BYTES
from mage_ptcg.exact_file import (
    ExactFileSnapshotError,
    read_exact_regular_file,
    require_snapshot_path_unchanged,
)
from mage_ptcg.knowledge.model import (
    content_hash as knowledge_content_hash,
    deck_identity_from_card_ids,
)
from mage_ptcg.meta_specialist.decks import (
    ArchetypeRegistry,
    DeckAssetInput,
    DeckQualificationError,
)


SCHEMA_VERSION = "meta-specialist-seed-candidates-v1"
CARD_VOCABULARY_DOMAIN = "meta-specialist-en-card-vocabulary-v1"
_CONTENT_DOMAIN = SCHEMA_VERSION
_TOP_LEVEL_KEYS = {
    "schema_version",
    "content_sha256",
    "card_database_sha256",
    "card_vocabulary_sha256",
    "candidates",
}
_CANDIDATE_KEYS = {
    "runtime_id",
    "priority",
    "candidate_status",
    "deck_identity",
    "canonical_multiset_sha256",
    "card_ids",
    "raw_deck_sha256",
    "source_ref",
    "source_commit",
    "source_path",
    "meta_jsonl_locator",
    "asset_class",
    "usage_boundary",
    "permission_status",
    "materialization_status",
    "cabt_status",
    "runtime_compatibility",
    "blocker_codes",
}
_LOCATOR_KEYS = {"path", "line_number", "record_sha256"}
_SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_DECK_IDENTITY_RE = re.compile(r"deck-[0-9a-f]{20}")
_FORBIDDEN_CLAIM_RE = re.compile(r"strong|champion", re.IGNORECASE)
_CARD_ID_TOKEN_RE = re.compile(r"[1-9][0-9]*")

_INTERNAL_PERMISSION = (
    "TEAM_INTERNAL_POLICY_MATCH_CONDITIONAL_TECHNICAL_VALIDATION_REQUIRED"
)
_PUBLIC_PERMISSION = "DECK_ONLY_OR_LOCAL_EVAL_SOURCE_TRAINING_PERMISSION_UNKNOWN"
_META_PERMISSION = "META_DERIVED_DECK_ONLY_TRAINING_PERMISSION_UNKNOWN"
# A public-source deck the repository owner has explicitly approved for training.
# Deliberately distinct from _INTERNAL_PERMISSION: the source really is public, and
# recording it as a team-internal policy match would make the registry lie about
# where the asset came from.  The approval is an owner decision, recorded with its
# date and rationale in docs/decisions/, not an inference this module may make.
_OWNER_APPROVED_PUBLIC_PERMISSION = "PUBLIC_SOURCE_TRAINING_APPROVED_BY_REPOSITORY_OWNER"
_PERMISSION_STATUSES = {
    _INTERNAL_PERMISSION, _PUBLIC_PERMISSION, _META_PERMISSION,
    _OWNER_APPROVED_PUBLIC_PERMISSION,
}
_MATERIALIZED_USAGE = {
    _INTERNAL_PERMISSION: "team_internal_candidate_validation_only",
    _PUBLIC_PERMISSION: "local_offline_evaluation_only_training_not_granted",
    _OWNER_APPROVED_PUBLIC_PERMISSION: "public_source_training_approved_local_only",
}
_META_USAGE = "inventory_only_authorized_materialization_required"
_ASSET_CLASSES = {
    "materialized_deck_csv_deduplicated_by_canonical_multiset",
    "immutable_meta_jsonl_deck_row",
}
_MATERIALIZATION_STATUSES = {"materialized_git_blob", "unmaterialized_meta_row"}
_RUNTIME_COMPATIBILITY = {
    "UNQUALIFIED_STATIC_DECK_MATCH_ONLY",
    "NO_MATCHING_QUALIFIED_RUNTIME",
}
_CABT_STATUS = "NOT_RUN_REGISTERED_UNQUALIFIED"
_GENERAL_BLOCKERS = {
    "cabt_legality_not_run",
    "competition_legality_not_confirmed",
    "current_meta_not_confirmed",
    "runtime_not_qualified",
}
_BLOCKER_CODES = _GENERAL_BLOCKERS.union(
    {
        "materialization_authority_unknown",
        "raw_deck_bytes_unavailable",
        "technical_validation_required",
        "training_permission_unknown",
    }
)
GitBlobByteProvider = Callable[[str, str, str], bytes]
_CANDIDATE_ISSUANCE_DOMAIN = "meta-specialist-issued-seed-candidate-v1"
_VOCABULARY_ISSUANCE_DOMAIN = "meta-specialist-issued-en-card-vocabulary-v1"
_ACQUIRED_BLOB_ISSUANCE_DOMAIN = "meta-specialist-acquired-seed-blob-v1"
_MAX_TEMP_ALLOCATION_ATTEMPTS = 32
_MAX_EN_CARD_DATABASE_BYTES = 16 * 1024 * 1024


class SeedRegistryError(ValueError):
    """Raised when seed provenance or materialization fails closed."""


@dataclass(frozen=True, slots=True)
class MetaJsonlLocator:
    """Immutable locator for a selected JSONL record, not materialized bytes."""

    path: str
    line_number: int
    record_sha256: str


class _VocabularyIssuanceSeal:
    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class EnCardVocabulary:
    """Typed binding issued only after parsing exact EN CSV bytes."""

    card_ids: frozenset[int]
    source_sha256: str
    vocabulary_sha256: str
    _issuance_seal: _VocabularyIssuanceSeal | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


_ISSUED_VOCABULARIES: dict[
    int,
    tuple[
        ReferenceType[EnCardVocabulary],
        _VocabularyIssuanceSeal,
        str,
        str,
        int,
    ],
] = {}
_ISSUED_VOCABULARIES_LOCK = Lock()


class _CandidateIssuanceSeal:
    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SeedCandidate:
    """One registered but unqualified fixed-lane deck candidate."""

    runtime_id: str
    priority: int
    candidate_status: str
    deck_identity: str
    canonical_multiset_sha256: str
    card_ids: tuple[int, ...]
    raw_deck_sha256: str | None
    source_ref: str
    source_commit: str
    source_path: str | None
    meta_jsonl_locator: MetaJsonlLocator | None
    asset_class: str
    usage_boundary: str
    permission_status: str
    materialization_status: str
    cabt_status: str
    runtime_compatibility: str
    blocker_codes: tuple[str, ...]
    _issuance_seal: _CandidateIssuanceSeal | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


_ISSUED_CANDIDATES: dict[
    int,
    tuple[
        ReferenceType[SeedCandidate],
        _CandidateIssuanceSeal,
        str,
        str,
    ],
] = {}
_ISSUED_CANDIDATES_LOCK = Lock()


class _AcquiredSeedBlobIssuanceSeal:
    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class AcquiredSeedBlobV1:
    """Exact provider bytes sealed to one issued seed candidate."""

    deck_identity: str
    source_ref: str
    source_commit: str
    source_path: str
    raw_deck_sha256: str
    card_ids: tuple[int, ...]
    payload: bytes
    _issuance_seal: _AcquiredSeedBlobIssuanceSeal | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )


_ISSUED_ACQUIRED_BLOBS: dict[
    int,
    tuple[
        ReferenceType[AcquiredSeedBlobV1],
        _AcquiredSeedBlobIssuanceSeal,
        ReferenceType[SeedCandidate],
        _CandidateIssuanceSeal,
        str,
        str,
        bytes,
    ],
] = {}
_ISSUED_ACQUIRED_BLOBS_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class SeedCandidateRegistry:
    """The content-addressed 5-lane seed intake registry."""

    schema_version: str
    content_sha256: str
    card_database_sha256: str
    card_vocabulary_sha256: str
    candidates: tuple[SeedCandidate, ...]
    candidates_by_runtime: Mapping[str, tuple[SeedCandidate, ...]]


def canonical_multiset_sha256(card_ids: object) -> str:
    """Return the full canonical Team Deck multiset digest."""
    if not isinstance(card_ids, (list, tuple)) or len(card_ids) != 60:
        raise SeedRegistryError("card_ids must contain exactly 60 cards")
    counts: dict[int, int] = {}
    for index, card_id in enumerate(card_ids):
        if type(card_id) is not int or card_id <= 0:
            raise SeedRegistryError(
                f"card_ids[{index}] must contain positive ints and not bool"
            )
        counts[card_id] = counts.get(card_id, 0) + 1
    return knowledge_content_hash([[card_id, counts[card_id]] for card_id in sorted(counts)])


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise SeedRegistryError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def _reject_nonfinite_json(value: str) -> object:
    raise SeedRegistryError(f"non-finite JSON constant is not allowed: {value}")


def _require_exact_keys(value: object, keys: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SeedRegistryError(f"{field} must be an object")
    unknown = set(value).difference(keys)
    missing = keys.difference(value)
    if unknown or missing:
        raise SeedRegistryError(
            f"{field} has invalid keys: unknown={sorted(unknown)}, missing={sorted(missing)}"
        )
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SeedRegistryError(f"{field} must be a non-empty string")
    return value


def _require_sha256(value: object, field: str) -> str:
    string = _require_string(value, field)
    try:
        return require_sha256(string, field)
    except ValueError as exc:
        raise SeedRegistryError(str(exc)) from exc


def bind_en_card_vocabulary(
    card_ids: Collection[int],
    *,
    source_sha256: str,
) -> EnCardVocabulary:
    """Bind already-read EN IDs to both their set digest and explicit source digest."""
    source_digest = _require_sha256(source_sha256, "source_sha256")
    if card_ids is None:
        raise SeedRegistryError("card_ids is required for an EN card vocabulary")
    try:
        values = tuple(card_ids)
    except TypeError as exc:
        raise SeedRegistryError("card_ids must be a collection") from exc
    if not values:
        raise SeedRegistryError("card_ids must not be empty")
    if any(type(card_id) is not int or card_id <= 0 for card_id in values):
        raise SeedRegistryError("EN card vocabulary IDs must be positive ints and not bool")
    unique_ids = frozenset(values)
    vocabulary_sha256 = content_id(CARD_VOCABULARY_DOMAIN, sorted(unique_ids))
    return EnCardVocabulary(
        card_ids=unique_ids,
        source_sha256=source_digest,
        vocabulary_sha256=vocabulary_sha256,
    )


def _issue_en_card_vocabulary(
    vocabulary: EnCardVocabulary,
    *,
    exact_source_bytes: bytes,
) -> EnCardVocabulary:
    if hashlib.sha256(exact_source_bytes).hexdigest() != vocabulary.source_sha256:
        raise SeedRegistryError("EN card vocabulary source bytes do not match source_sha256")
    seal = _VocabularyIssuanceSeal()
    object.__setattr__(vocabulary, "_issuance_seal", seal)
    object_id = id(vocabulary)

    def discard(dead_ref: ReferenceType[EnCardVocabulary]) -> None:
        with _ISSUED_VOCABULARIES_LOCK:
            registered = _ISSUED_VOCABULARIES.get(object_id)
            if registered is not None and registered[0] is dead_ref:
                del _ISSUED_VOCABULARIES[object_id]

    vocabulary_ref = ref(vocabulary, discard)
    with _ISSUED_VOCABULARIES_LOCK:
        _ISSUED_VOCABULARIES[object_id] = (
            vocabulary_ref,
            seal,
            vocabulary.source_sha256,
            vocabulary.vocabulary_sha256,
            len(exact_source_bytes),
        )
    return vocabulary


def read_en_card_vocabulary(path: str | Path) -> EnCardVocabulary:
    """Read an explicit EN CSV and bind its parsed IDs to the exact source bytes."""
    if not isinstance(path, (str, Path)) or (isinstance(path, str) and not path):
        raise SeedRegistryError("EN card database path must be explicit")
    source_path = Path(path)
    try:
        snapshot = read_exact_regular_file(
            source_path,
            max_bytes=_MAX_EN_CARD_DATABASE_BYTES,
        )
    except ExactFileSnapshotError as exc:
        raise SeedRegistryError(
            f"could not snapshot EN card database {source_path}: {exc}"
        ) from exc
    payload = snapshot.payload
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SeedRegistryError("EN card database must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or reader.fieldnames.count("Card ID") != 1:
        raise SeedRegistryError("EN card database must contain exactly one Card ID column")
    card_ids: list[int] = []
    for row_number, row in enumerate(reader, start=2):
        token = row.get("Card ID")
        if token is None or not token:
            raise SeedRegistryError(f"EN card database row {row_number} has no Card ID")
        if _CARD_ID_TOKEN_RE.fullmatch(token) is None:
            raise SeedRegistryError(
                f"EN card database row {row_number} Card ID must be canonical positive decimal"
            )
        try:
            card_id = int(token)
        except ValueError as exc:
            raise SeedRegistryError(
                f"EN card database row {row_number} Card ID is too large"
            ) from exc
        card_ids.append(card_id)
    return _issue_en_card_vocabulary(
        bind_en_card_vocabulary(
            card_ids,
            source_sha256=snapshot.sha256,
        ),
        exact_source_bytes=payload,
    )


def _validated_en_card_vocabulary(value: object) -> EnCardVocabulary:
    if type(value) is not EnCardVocabulary:
        raise SeedRegistryError("card_vocabulary must be an EnCardVocabulary")
    seal = getattr(value, "_issuance_seal", None)
    with _ISSUED_VOCABULARIES_LOCK:
        issuance = _ISSUED_VOCABULARIES.get(id(value))
    if (
        seal is None
        or issuance is None
        or issuance[0]() is not value
        or issuance[1] is not seal
    ):
        raise SeedRegistryError(
            "card vocabulary must be issued from exact EN CSV bytes"
        )
    expected_source_sha256, expected_vocabulary_sha256, _source_size = issuance[2:]
    rebound = bind_en_card_vocabulary(
        value.card_ids,
        source_sha256=value.source_sha256,
    )
    if (
        rebound.vocabulary_sha256 != value.vocabulary_sha256
        or value.source_sha256 != expected_source_sha256
        or value.vocabulary_sha256 != expected_vocabulary_sha256
    ):
        raise SeedRegistryError("card vocabulary attestation does not match its card IDs")
    return rebound


def _require_source_commit(value: object, field: str) -> str:
    commit = _require_string(value, field)
    if _SOURCE_COMMIT_RE.fullmatch(commit) is None:
        raise SeedRegistryError(f"{field} must be an exact 40-character lowercase hex commit")
    return commit


def _require_relative_git_path(value: object, field: str) -> str:
    path = _require_string(value, field)
    raw_parts = path.split("/")
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or "\\" in path
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise SeedRegistryError(f"{field} must be a normalized relative Git path")
    return path


def _require_string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SeedRegistryError(f"{field} must be a non-empty list")
    result = tuple(
        _require_string(item, f"{field}[{index}]") for index, item in enumerate(value)
    )
    if result != tuple(sorted(result)) or len(result) != len(set(result)):
        raise SeedRegistryError(f"{field} must be strictly sorted and unique")
    return result


def _reject_forbidden_claim_wording(value: object, field: str = "registry") -> None:
    if isinstance(value, str):
        if _FORBIDDEN_CLAIM_RE.search(value):
            raise SeedRegistryError(f"{field} contains forbidden performance claim wording")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_claim_wording(item, f"{field}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_forbidden_claim_wording(key, f"{field}.key")
            _reject_forbidden_claim_wording(item, f"{field}.{key}")


def _parse_locator(value: object, field: str) -> MetaJsonlLocator:
    record = _require_exact_keys(value, _LOCATOR_KEYS, field)
    path = _require_relative_git_path(record["path"], f"{field}.path")
    line_number = record["line_number"]
    if type(line_number) is not int or line_number <= 0:
        raise SeedRegistryError(f"{field}.line_number must be a positive int and not bool")
    record_sha256 = _require_sha256(record["record_sha256"], f"{field}.record_sha256")
    return MetaJsonlLocator(path=path, line_number=line_number, record_sha256=record_sha256)


def _parse_card_ids(
    value: object,
    field: str,
    *,
    known_card_ids: Collection[int],
) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != 60:
        actual = len(value) if isinstance(value, list) else "non-list"
        raise SeedRegistryError(f"{field} must contain exactly 60 cards, got {actual}")
    cards = tuple(value)
    if any(type(card_id) is not int or card_id <= 0 for card_id in cards):
        raise SeedRegistryError(f"{field} must contain positive ints and not bool")
    unknown = sorted(set(cards).difference(known_card_ids))
    if unknown:
        raise SeedRegistryError(f"{field} contains unknown card IDs: {unknown}")
    return cards


def _validate_materialization_contract(
    *,
    index: int,
    raw_deck_sha256: object,
    source_path: object,
    locator_value: object,
    asset_class: str,
    usage_boundary: str,
    permission_status: str,
    materialization_status: str,
    runtime_compatibility: str,
    blocker_codes: tuple[str, ...],
) -> tuple[str | None, str | None, MetaJsonlLocator | None]:
    field = f"candidates[{index}]"
    if (source_path is None) == (locator_value is None):
        raise SeedRegistryError(
            f"{field} must set exactly one of source_path or meta_jsonl_locator"
        )

    if materialization_status == "materialized_git_blob":
        if source_path is None:
            raise SeedRegistryError(f"{field}.source_path is required for a materialized Git blob")
        path = _require_relative_git_path(source_path, f"{field}.source_path")
        raw_sha256 = _require_sha256(raw_deck_sha256, f"{field}.raw_deck_sha256")
        if locator_value is not None:
            raise SeedRegistryError(f"{field}.meta_jsonl_locator must be null")
        if asset_class != "materialized_deck_csv_deduplicated_by_canonical_multiset":
            raise SeedRegistryError(f"{field}.asset_class does not match materialization_status")
        expected_usage = _MATERIALIZED_USAGE.get(permission_status)
        if expected_usage is None or usage_boundary != expected_usage:
            raise SeedRegistryError(f"{field}.usage_boundary does not match permission_status")
        if runtime_compatibility != "UNQUALIFIED_STATIC_DECK_MATCH_ONLY":
            raise SeedRegistryError(
                f"{field}.runtime_compatibility does not match a materialized static deck"
            )
        if permission_status == _INTERNAL_PERMISSION:
            required = {"technical_validation_required"}
        elif permission_status == _OWNER_APPROVED_PUBLIC_PERMISSION:
            # The approval clears the permission blocker; every other blocker
            # (cabt legality, runtime qualification) still has to be cleared on
            # its own evidence.
            required = set()
        else:
            required = {"training_permission_unknown"}
        missing = required.difference(blocker_codes)
        if missing:
            raise SeedRegistryError(f"{field}.blocker_codes missing {sorted(missing)}")
        return raw_sha256, path, None

    if materialization_status != "unmaterialized_meta_row":
        raise SeedRegistryError(f"unsupported materialization_status: {materialization_status}")
    if raw_deck_sha256 is not None:
        raise SeedRegistryError(f"{field}.raw_deck_sha256 must be null for an unmaterialized row")
    if source_path is not None:
        raise SeedRegistryError(f"{field}.source_path must be null for an unmaterialized row")
    locator = _parse_locator(locator_value, f"{field}.meta_jsonl_locator")
    if asset_class != "immutable_meta_jsonl_deck_row":
        raise SeedRegistryError(f"{field}.asset_class does not match materialization_status")
    if permission_status != _META_PERMISSION or usage_boundary != _META_USAGE:
        raise SeedRegistryError(
            f"{field} unmaterialized permission_status/usage_boundary is not fail-closed"
        )
    if runtime_compatibility != "NO_MATCHING_QUALIFIED_RUNTIME":
        raise SeedRegistryError(
            f"{field}.runtime_compatibility must remain unqualified for a meta row"
        )
    required = {
        "materialization_authority_unknown",
        "raw_deck_bytes_unavailable",
        "training_permission_unknown",
    }
    missing = required.difference(blocker_codes)
    if missing:
        raise SeedRegistryError(f"{field}.blocker_codes missing {sorted(missing)}")
    return None, None, locator


def _parse_candidate(
    value: object,
    index: int,
    *,
    archetypes: ArchetypeRegistry,
    known_card_ids: Collection[int],
) -> SeedCandidate:
    field = f"candidates[{index}]"
    record = _require_exact_keys(value, _CANDIDATE_KEYS, field)
    runtime_id = _require_string(record["runtime_id"], f"{field}.runtime_id")
    archetype = archetypes.archetypes.get(runtime_id)
    if archetype is None:
        raise SeedRegistryError(f"{field}.runtime_id is not registered: {runtime_id}")
    priority = record["priority"]
    if type(priority) is not int or priority not in {1, 2, 3}:
        raise SeedRegistryError(f"{field}.priority must be one of 1, 2, 3 and not bool")
    candidate_status = _require_string(record["candidate_status"], f"{field}.candidate_status")
    if candidate_status != "registered_unqualified":
        raise SeedRegistryError(f"{field}.candidate_status must be registered_unqualified")

    card_ids = _parse_card_ids(
        record["card_ids"], f"{field}.card_ids", known_card_ids=known_card_ids
    )
    canonical = _require_sha256(
        record["canonical_multiset_sha256"], f"{field}.canonical_multiset_sha256"
    )
    recomputed_canonical = canonical_multiset_sha256(card_ids)
    if canonical != recomputed_canonical:
        raise SeedRegistryError(f"{field}.canonical_multiset_sha256 does not match card_ids")
    deck_identity = _require_string(record["deck_identity"], f"{field}.deck_identity")
    if _DECK_IDENTITY_RE.fullmatch(deck_identity) is None:
        raise SeedRegistryError(f"{field}.deck_identity must be canonical deck-<20hex>")
    recomputed_identity = deck_identity_from_card_ids(card_ids)
    if deck_identity != recomputed_identity or deck_identity != "deck-" + canonical[:20]:
        raise SeedRegistryError(f"{field}.deck_identity does not match card_ids")
    missing_core = sorted(set(archetype.core_card_ids).difference(card_ids))
    if missing_core:
        raise SeedRegistryError(f"{field} is missing registered core card IDs: {missing_core}")

    source_ref = _require_string(record["source_ref"], f"{field}.source_ref")
    source_commit = _require_source_commit(record["source_commit"], f"{field}.source_commit")
    asset_class = _require_string(record["asset_class"], f"{field}.asset_class")
    if asset_class not in _ASSET_CLASSES:
        raise SeedRegistryError(f"unsupported asset_class: {asset_class}")
    usage_boundary = _require_string(record["usage_boundary"], f"{field}.usage_boundary")
    permission_status = _require_string(
        record["permission_status"], f"{field}.permission_status"
    )
    if permission_status not in _PERMISSION_STATUSES:
        raise SeedRegistryError(f"unsupported permission_status: {permission_status}")
    materialization_status = _require_string(
        record["materialization_status"], f"{field}.materialization_status"
    )
    if materialization_status not in _MATERIALIZATION_STATUSES:
        raise SeedRegistryError(f"unsupported materialization_status: {materialization_status}")
    cabt_status = _require_string(record["cabt_status"], f"{field}.cabt_status")
    if cabt_status != _CABT_STATUS:
        raise SeedRegistryError(f"{field}.cabt_status must remain {_CABT_STATUS}")
    runtime_compatibility = _require_string(
        record["runtime_compatibility"], f"{field}.runtime_compatibility"
    )
    if runtime_compatibility not in _RUNTIME_COMPATIBILITY:
        raise SeedRegistryError(f"unsupported runtime_compatibility: {runtime_compatibility}")
    blocker_codes = _require_string_list(record["blocker_codes"], f"{field}.blocker_codes")
    unknown_blockers = set(blocker_codes).difference(_BLOCKER_CODES)
    if unknown_blockers:
        raise SeedRegistryError(f"{field}.blocker_codes contains unsupported codes")
    missing_general = _GENERAL_BLOCKERS.difference(blocker_codes)
    if missing_general:
        raise SeedRegistryError(f"{field}.blocker_codes missing {sorted(missing_general)}")

    raw_sha256, source_path, locator = _validate_materialization_contract(
        index=index,
        raw_deck_sha256=record["raw_deck_sha256"],
        source_path=record["source_path"],
        locator_value=record["meta_jsonl_locator"],
        asset_class=asset_class,
        usage_boundary=usage_boundary,
        permission_status=permission_status,
        materialization_status=materialization_status,
        runtime_compatibility=runtime_compatibility,
        blocker_codes=blocker_codes,
    )
    return SeedCandidate(
        runtime_id=runtime_id,
        priority=priority,
        candidate_status=candidate_status,
        deck_identity=deck_identity,
        canonical_multiset_sha256=canonical,
        card_ids=card_ids,
        raw_deck_sha256=raw_sha256,
        source_ref=source_ref,
        source_commit=source_commit,
        source_path=source_path,
        meta_jsonl_locator=locator,
        asset_class=asset_class,
        usage_boundary=usage_boundary,
        permission_status=permission_status,
        materialization_status=materialization_status,
        cabt_status=cabt_status,
        runtime_compatibility=runtime_compatibility,
        blocker_codes=blocker_codes,
    )


def _candidate_record(candidate: SeedCandidate) -> dict[str, object]:
    locator = candidate.meta_jsonl_locator
    locator_payload: dict[str, object] | None = None
    if locator is not None:
        locator_payload = {
            "path": locator.path,
            "line_number": locator.line_number,
            "record_sha256": locator.record_sha256,
        }
    return {
        "runtime_id": candidate.runtime_id,
        "priority": candidate.priority,
        "candidate_status": candidate.candidate_status,
        "deck_identity": candidate.deck_identity,
        "canonical_multiset_sha256": candidate.canonical_multiset_sha256,
        "card_ids": list(candidate.card_ids),
        "raw_deck_sha256": candidate.raw_deck_sha256,
        "source_ref": candidate.source_ref,
        "source_commit": candidate.source_commit,
        "source_path": candidate.source_path,
        "meta_jsonl_locator": locator_payload,
        "asset_class": candidate.asset_class,
        "usage_boundary": candidate.usage_boundary,
        "permission_status": candidate.permission_status,
        "materialization_status": candidate.materialization_status,
        "cabt_status": candidate.cabt_status,
        "runtime_compatibility": candidate.runtime_compatibility,
        "blocker_codes": list(candidate.blocker_codes),
    }


def _candidate_issuance_sha256(
    candidate: SeedCandidate,
    *,
    registry_content_sha256: str,
) -> str:
    return content_id(
        _CANDIDATE_ISSUANCE_DOMAIN,
        {
            "registry_content_sha256": registry_content_sha256,
            "candidate": _candidate_record(candidate),
        },
    )


def _issue_candidate(
    candidate: SeedCandidate,
    *,
    registry_content_sha256: str,
) -> SeedCandidate:
    seal = _CandidateIssuanceSeal()
    attestation = _candidate_issuance_sha256(
        candidate,
        registry_content_sha256=registry_content_sha256,
    )
    object.__setattr__(candidate, "_issuance_seal", seal)
    object_id = id(candidate)

    def discard(dead_ref: ReferenceType[SeedCandidate]) -> None:
        with _ISSUED_CANDIDATES_LOCK:
            registered = _ISSUED_CANDIDATES.get(object_id)
            if registered is not None and registered[0] is dead_ref:
                del _ISSUED_CANDIDATES[object_id]

    candidate_ref = ref(candidate, discard)
    with _ISSUED_CANDIDATES_LOCK:
        _ISSUED_CANDIDATES[object_id] = (
            candidate_ref,
            seal,
            registry_content_sha256,
            attestation,
        )
    return candidate


def _require_issued_candidate(candidate: object) -> SeedCandidate:
    if type(candidate) is not SeedCandidate:
        raise SeedRegistryError("candidate must be a SeedCandidate")
    seal = getattr(candidate, "_issuance_seal", None)
    with _ISSUED_CANDIDATES_LOCK:
        issuance = _ISSUED_CANDIDATES.get(id(candidate))
    if (
        seal is None
        or issuance is None
        or issuance[0]() is not candidate
        or issuance[1] is not seal
    ):
        raise SeedRegistryError(
            "candidate must be issued by a content-hash-verified registry"
        )
    registry_content_sha256, expected_attestation = issuance[2:]
    try:
        actual_attestation = _candidate_issuance_sha256(
            candidate,
            registry_content_sha256=registry_content_sha256,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SeedRegistryError("issued candidate fields are malformed") from exc
    if actual_attestation != expected_attestation:
        raise SeedRegistryError("issued candidate content no longer matches its registry")
    return candidate


def load_seed_candidate_registry(
    path: str | Path,
    *,
    archetypes: ArchetypeRegistry,
    card_vocabulary: EnCardVocabulary,
) -> SeedCandidateRegistry:
    """Load the standalone v1 registry and recompute every identity boundary."""
    if not isinstance(archetypes, ArchetypeRegistry):
        raise SeedRegistryError("archetypes must be an explicit ArchetypeRegistry")
    if len(archetypes.archetypes) != 5:
        raise SeedRegistryError("seed registry requires exactly five registered lanes")
    registry_path = Path(path)
    try:
        with registry_path.open(encoding="utf-8") as handle:
            document = json.load(
                handle,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json,
            )
    except (OSError, json.JSONDecodeError) as exc:
        raise SeedRegistryError(f"could not read seed registry {registry_path}: {exc}") from exc

    top = _require_exact_keys(document, _TOP_LEVEL_KEYS, "seed registry")
    if top["schema_version"] != SCHEMA_VERSION:
        raise SeedRegistryError("unsupported seed registry schema_version")
    stored_content_sha256 = _require_sha256(top["content_sha256"], "content_sha256")
    payload = {key: value for key, value in top.items() if key != "content_sha256"}
    try:
        recomputed_content_sha256 = content_id(_CONTENT_DOMAIN, payload)
    except (TypeError, ValueError) as exc:
        raise SeedRegistryError(f"seed registry is not canonical JSON: {exc}") from exc
    if stored_content_sha256 != recomputed_content_sha256:
        raise SeedRegistryError("content_sha256 does not match canonical registry content")
    _reject_forbidden_claim_wording(top)
    card_database_sha256 = _require_sha256(
        top["card_database_sha256"], "card_database_sha256"
    )
    card_vocabulary_sha256 = _require_sha256(
        top["card_vocabulary_sha256"], "card_vocabulary_sha256"
    )
    validated_vocabulary = _validated_en_card_vocabulary(card_vocabulary)
    if validated_vocabulary.vocabulary_sha256 != card_vocabulary_sha256:
        raise SeedRegistryError(
            "card vocabulary does not match the pinned registry vocabulary"
        )
    if validated_vocabulary.source_sha256 != card_database_sha256:
        raise SeedRegistryError(
            "EN card vocabulary source SHA-256 does not match card_database_sha256"
        )
    records = top["candidates"]
    if not isinstance(records, list):
        raise SeedRegistryError("candidates must be a list")
    candidates = tuple(
        _parse_candidate(
            record,
            index,
            archetypes=archetypes,
            known_card_ids=validated_vocabulary.card_ids,
        )
        for index, record in enumerate(records)
    )
    if len(candidates) != 15:
        raise SeedRegistryError("seed registry requires exactly 15 candidates")

    lane_priorities = tuple((candidate.runtime_id, candidate.priority) for candidate in candidates)
    if len(lane_priorities) != len(set(lane_priorities)):
        raise SeedRegistryError("duplicate lane priority in seed registry")
    identities = tuple(candidate.deck_identity for candidate in candidates)
    if len(identities) != len(set(identities)):
        raise SeedRegistryError("duplicate canonical deck in seed registry")
    expected_order = tuple(
        (runtime_id, priority)
        for runtime_id in archetypes.archetypes
        for priority in (1, 2, 3)
    )
    if lane_priorities != expected_order:
        raise SeedRegistryError(
            "candidates must follow archetype registry order with priorities 1, 2, 3"
        )
    candidates = tuple(
        _issue_candidate(
            candidate,
            registry_content_sha256=stored_content_sha256,
        )
        for candidate in candidates
    )
    grouped = MappingProxyType(
        {
            runtime_id: tuple(
                candidate for candidate in candidates if candidate.runtime_id == runtime_id
            )
            for runtime_id in archetypes.archetypes
        }
    )
    return SeedCandidateRegistry(
        schema_version=SCHEMA_VERSION,
        content_sha256=stored_content_sha256,
        card_database_sha256=card_database_sha256,
        card_vocabulary_sha256=card_vocabulary_sha256,
        candidates=candidates,
        candidates_by_runtime=grouped,
    )


def _cards_from_exact_bytes(payload: bytes) -> tuple[int, ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SeedRegistryError("materialized deck bytes must be UTF-8") from exc
    values: list[int] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        value = raw.strip()
        if not value:
            continue
        try:
            card_id = int(value)
        except ValueError as exc:
            raise SeedRegistryError(
                f"materialized deck line {line_number} is not an integer"
            ) from exc
        if card_id <= 0:
            raise SeedRegistryError("materialized deck card IDs must be positive")
        values.append(card_id)
    if len(values) != 60:
        raise SeedRegistryError("materialized deck must contain exactly 60 cards")
    return tuple(values)


@dataclass(frozen=True, slots=True)
class _OutputDirectoryBinding:
    path: Path
    descriptor: int
    device: int
    inode: int


def _required_output_open_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value <= 0:
        raise SeedRegistryError(f"required secure output open flag {name} is unavailable")
    return value


def _open_output_directory(path: Path) -> _OutputDirectoryBinding:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SeedRegistryError(f"could not prepare output parent directory {path}: {exc}") from exc
    flags = os.O_RDONLY
    flags |= _required_output_open_flag("O_CLOEXEC")
    flags |= _required_output_open_flag("O_NOFOLLOW")
    flags |= _required_output_open_flag("O_DIRECTORY")
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SeedRegistryError(f"output parent must be a no-follow directory: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        linked = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(linked.st_mode)
            or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise SeedRegistryError("output parent directory changed while it was bound")
    except Exception:
        os.close(descriptor)
        raise
    return _OutputDirectoryBinding(
        path=path,
        descriptor=descriptor,
        device=opened.st_dev,
        inode=opened.st_ino,
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write while publishing materialized deck")
        remaining = remaining[written:]


def _existing_leaf_state(
    binding: _OutputDirectoryBinding,
    filename: str,
    payload: bytes,
) -> str:
    """Classify the destination leaf without following symlinks or replacing anything."""
    try:
        linked = os.stat(filename, dir_fd=binding.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        raise SeedRegistryError(
            "existing output_path could not be inspected and was preserved"
        ) from exc
    if not stat.S_ISREG(linked.st_mode):
        raise SeedRegistryError(
            "existing output_path is not a regular file and was preserved"
        )
    flags = os.O_RDONLY
    flags |= _required_output_open_flag("O_CLOEXEC")
    flags |= _required_output_open_flag("O_NOFOLLOW")
    try:
        descriptor = os.open(filename, flags, dir_fd=binding.descriptor)
    except OSError as exc:
        raise SeedRegistryError(
            "existing output_path could not be read and was preserved"
        ) from exc
    chunks: list[bytes] = []
    try:
        total = 0
        limit = len(payload) + 1
        while total <= limit:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise SeedRegistryError(
            "existing output_path could not be read and was preserved"
        ) from exc
    finally:
        os.close(descriptor)
    if b"".join(chunks) != payload:
        raise SeedRegistryError(
            "existing output_path is not byte-identical and was preserved"
        )
    return "identical"


def _discard_temporary(binding: _OutputDirectoryBinding, temporary_name: str) -> None:
    """Best-effort removal of our own temporary leaf; never masks the primary failure."""
    try:
        os.unlink(temporary_name, dir_fd=binding.descriptor)
    except OSError:
        pass


def _publish_bound_output(
    binding: _OutputDirectoryBinding,
    filename: str,
    payload: bytes,
) -> None:
    """Publish exact bytes by linking a private temporary leaf onto an absent destination."""
    create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    create_flags |= _required_output_open_flag("O_CLOEXEC")
    create_flags |= _required_output_open_flag("O_NOFOLLOW")
    temporary_name: str | None = None
    descriptor: int | None = None
    for _attempt in range(_MAX_TEMP_ALLOCATION_ATTEMPTS):
        candidate_name = f".{filename}.tmp.{os.urandom(12).hex()}"
        try:
            descriptor = os.open(
                candidate_name,
                create_flags,
                0o600,
                dir_fd=binding.descriptor,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise SeedRegistryError(
                "could not prepare materialization temporary file"
            ) from exc
        temporary_name = candidate_name
        break
    if descriptor is None or temporary_name is None:
        raise SeedRegistryError("could not prepare materialization temporary file")
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except OSError as exc:
        os.close(descriptor)
        _discard_temporary(binding, temporary_name)
        raise SeedRegistryError(
            "could not prepare materialization temporary file"
        ) from exc
    os.close(descriptor)
    try:
        os.link(
            temporary_name,
            filename,
            src_dir_fd=binding.descriptor,
            dst_dir_fd=binding.descriptor,
        )
    except FileExistsError as exc:
        _discard_temporary(binding, temporary_name)
        raise SeedRegistryError(
            "existing output_path was published concurrently and was preserved"
        ) from exc
    except OSError as exc:
        _discard_temporary(binding, temporary_name)
        raise SeedRegistryError("could not publish materialized deck") from exc
    try:
        os.fsync(binding.descriptor)
    except OSError as exc:
        _discard_temporary(binding, temporary_name)
        raise SeedRegistryError("could not durably publish materialized deck") from exc
    try:
        os.unlink(temporary_name, dir_fd=binding.descriptor)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SeedRegistryError("materialization temporary cleanup failed") from exc


def _acquired_blob_issuance_sha256(
    blob: AcquiredSeedBlobV1,
    *,
    candidate_attestation: str,
) -> str:
    return content_id(
        _ACQUIRED_BLOB_ISSUANCE_DOMAIN,
        {
            "candidate_attestation": candidate_attestation,
            "deck_identity": blob.deck_identity,
            "source_ref": blob.source_ref,
            "source_commit": blob.source_commit,
            "source_path": blob.source_path,
            "raw_deck_sha256": blob.raw_deck_sha256,
            "card_ids": list(blob.card_ids),
            "payload_sha256": hashlib.sha256(blob.payload).hexdigest(),
        },
    )


def _issue_acquired_seed_blob(
    blob: AcquiredSeedBlobV1,
    *,
    candidate: SeedCandidate,
) -> AcquiredSeedBlobV1:
    with _ISSUED_CANDIDATES_LOCK:
        candidate_issuance = _ISSUED_CANDIDATES.get(id(candidate))
    if candidate_issuance is None or candidate_issuance[0]() is not candidate:
        raise SeedRegistryError(
            "candidate must be issued by a content-hash-verified registry"
        )
    candidate_seal = candidate_issuance[1]
    candidate_attestation = candidate_issuance[3]
    seal = _AcquiredSeedBlobIssuanceSeal()
    attestation = _acquired_blob_issuance_sha256(
        blob,
        candidate_attestation=candidate_attestation,
    )
    object.__setattr__(blob, "_issuance_seal", seal)
    object_id = id(blob)

    def discard(dead_ref: ReferenceType[AcquiredSeedBlobV1]) -> None:
        with _ISSUED_ACQUIRED_BLOBS_LOCK:
            registered = _ISSUED_ACQUIRED_BLOBS.get(object_id)
            if registered is not None and registered[0] is dead_ref:
                del _ISSUED_ACQUIRED_BLOBS[object_id]

    blob_ref = ref(blob, discard)
    with _ISSUED_ACQUIRED_BLOBS_LOCK:
        _ISSUED_ACQUIRED_BLOBS[object_id] = (
            blob_ref,
            seal,
            candidate_issuance[0],
            candidate_seal,
            candidate_attestation,
            attestation,
            blob.payload,
        )
    return blob


def _require_issued_acquired_blob(
    acquired_blob: object,
    *,
    candidate: SeedCandidate,
) -> AcquiredSeedBlobV1:
    if type(acquired_blob) is not AcquiredSeedBlobV1:
        raise SeedRegistryError("acquired_blob must be an AcquiredSeedBlobV1")
    seal = getattr(acquired_blob, "_issuance_seal", None)
    with _ISSUED_ACQUIRED_BLOBS_LOCK:
        issuance = _ISSUED_ACQUIRED_BLOBS.get(id(acquired_blob))
    if (
        seal is None
        or issuance is None
        or issuance[0]() is not acquired_blob
        or issuance[1] is not seal
    ):
        raise SeedRegistryError(
            "acquired_blob must be an issued acquired seed blob from this process"
        )
    candidate_ref, candidate_seal = issuance[2], issuance[3]
    if (
        candidate_ref() is not candidate
        or getattr(candidate, "_issuance_seal", None) is not candidate_seal
    ):
        raise SeedRegistryError("acquired seed blob does not match candidate")
    candidate_attestation, expected_attestation, expected_payload = issuance[4:]
    if acquired_blob.payload is not expected_payload:
        raise SeedRegistryError("acquired seed blob attestation no longer matches")
    try:
        actual_attestation = _acquired_blob_issuance_sha256(
            acquired_blob,
            candidate_attestation=candidate_attestation,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SeedRegistryError("issued acquired seed blob fields are malformed") from exc
    if actual_attestation != expected_attestation:
        raise SeedRegistryError("acquired seed blob attestation no longer matches")
    return acquired_blob


def acquire_seed_candidate_blob(
    candidate: SeedCandidate,
    *,
    byte_provider: GitBlobByteProvider,
) -> AcquiredSeedBlobV1:
    """Acquire and seal exact provider bytes before any output destination is chosen."""
    candidate = _require_issued_candidate(candidate)
    if candidate.materialization_status == "unmaterialized_meta_row":
        raise SeedRegistryError(
            "meta JSONL rows require separate materialization authority and a new raw hash"
        )
    if candidate.materialization_status != "materialized_git_blob":
        raise SeedRegistryError("candidate is not a materialized_git_blob")
    if candidate.permission_status not in (
        _INTERNAL_PERMISSION, _OWNER_APPROVED_PUBLIC_PERMISSION
    ):
        raise SeedRegistryError(
            "candidate is not permission-approved for Git blob materialization"
        )
    if candidate.source_path is None or candidate.raw_deck_sha256 is None:
        raise SeedRegistryError("materialized candidate lacks exact source path or raw hash")
    if not callable(byte_provider):
        raise SeedRegistryError("byte_provider must be callable")
    source_ref = candidate.source_ref
    source_commit = candidate.source_commit
    source_path = candidate.source_path
    raw_deck_sha256 = candidate.raw_deck_sha256
    card_ids = candidate.card_ids
    try:
        payload = byte_provider(source_ref, source_commit, source_path)
    except Exception as exc:
        raise SeedRegistryError(f"Git blob byte provider failed: {exc}") from exc
    try:
        _require_issued_candidate(candidate)
    except SeedRegistryError as exc:
        raise SeedRegistryError("candidate changed during byte provider callback") from exc
    if type(payload) is not bytes:
        raise SeedRegistryError("Git blob byte provider must return bytes")
    if hashlib.sha256(payload).hexdigest() != raw_deck_sha256:
        raise SeedRegistryError("provided Git blob raw byte SHA-256 does not match registry")
    if _cards_from_exact_bytes(payload) != card_ids:
        raise SeedRegistryError("provided Git blob card order does not match registry")
    blob = AcquiredSeedBlobV1(
        deck_identity=candidate.deck_identity,
        source_ref=source_ref,
        source_commit=source_commit,
        source_path=source_path,
        raw_deck_sha256=raw_deck_sha256,
        card_ids=card_ids,
        payload=payload,
    )
    return _issue_acquired_seed_blob(blob, candidate=candidate)


def materialize_seed_candidate(
    candidate: SeedCandidate,
    output_path: str | Path,
    *,
    acquired_blob: AcquiredSeedBlobV1,
) -> Path:
    """Publish already-sealed seed bytes to an explicit destination, never replacing a leaf."""
    candidate = _require_issued_candidate(candidate)
    blob = _require_issued_acquired_blob(acquired_blob, candidate=candidate)
    if not isinstance(output_path, (str, Path)) or (
        isinstance(output_path, str) and not output_path
    ):
        raise SeedRegistryError("output_path must be explicit")
    destination = Path(os.path.abspath(os.fspath(output_path)))
    if not destination.name:
        raise SeedRegistryError("output_path must name a file")
    payload = blob.payload
    binding = _open_output_directory(destination.parent)
    operation_failed = False
    try:
        if _existing_leaf_state(binding, destination.name, payload) == "identical":
            return destination
        _publish_bound_output(binding, destination.name, payload)
    except Exception:
        operation_failed = True
        raise
    finally:
        try:
            os.close(binding.descriptor)
        except OSError as close_error:
            if not operation_failed:
                raise SeedRegistryError(
                    f"could not close bound output parent directory: {close_error}"
                ) from close_error
    return destination


def build_deck_asset_input(
    candidate: SeedCandidate,
    *,
    materialized_path: str | Path,
    card_database_version: str,
) -> DeckAssetInput:
    """Bridge an explicitly materialized candidate into the existing qualification intake."""
    candidate = _require_issued_candidate(candidate)
    if candidate.materialization_status != "materialized_git_blob":
        raise SeedRegistryError("candidate must be a materialized_git_blob")
    if not isinstance(materialized_path, (str, Path)) or (
        isinstance(materialized_path, str) and not materialized_path
    ):
        raise SeedRegistryError("builder requires an explicit materialized_path")
    if not isinstance(card_database_version, str) or not card_database_version:
        raise SeedRegistryError("card_database_version must be a non-empty string")
    if candidate.source_path is None or candidate.raw_deck_sha256 is None:
        raise SeedRegistryError("materialized candidate lacks exact source path or raw hash")
    path = Path(materialized_path)
    try:
        snapshot = read_exact_regular_file(path, max_bytes=MAX_DECK_FILE_BYTES)
    except ExactFileSnapshotError as exc:
        raise SeedRegistryError(
            f"could not snapshot explicit materialized_path {path}: {exc}"
        ) from exc
    if snapshot.sha256 != candidate.raw_deck_sha256:
        raise SeedRegistryError("materialized_path raw byte SHA-256 does not match candidate")
    try:
        asset = DeckAssetInput.from_snapshot(
            asset_id=(
                f"seed-{candidate.runtime_id}-p{candidate.priority}-{candidate.deck_identity}"
            ),
            archetype_id=candidate.runtime_id,
            snapshot=snapshot,
            source_ref=f"{candidate.source_ref}:{candidate.source_path}",
            source_commit=candidate.source_commit,
            asset_class="deck_only",
            usage_boundary="local_eval_only",
            policy_compatibility=candidate.runtime_compatibility,
            card_database_version=card_database_version,
        )
    except DeckQualificationError as exc:
        raise SeedRegistryError(f"explicit materialized deck is invalid: {exc}") from exc
    try:
        _require_issued_candidate(candidate)
    except SeedRegistryError as exc:
        raise SeedRegistryError("candidate changed while deck asset input was built") from exc
    try:
        require_snapshot_path_unchanged(snapshot)
    except ExactFileSnapshotError as exc:
        raise SeedRegistryError(f"materialized snapshot path changed: {exc}") from exc
    if type(asset) is not DeckAssetInput or asset.path != snapshot.path:
        raise SeedRegistryError("materialized asset path does not bind its exact snapshot")
    if asset.card_ids != candidate.card_ids:
        raise SeedRegistryError("materialized_path ordered card IDs do not match candidate")
    if asset.deck_identity != candidate.deck_identity:
        raise SeedRegistryError(
            "materialized_path canonical deck identity does not match candidate"
        )
    if asset.deck_file_sha256 != candidate.raw_deck_sha256:
        raise SeedRegistryError("materialized_path exact hash does not match candidate")
    if asset.deck_file_bytes != snapshot.payload:
        raise SeedRegistryError("materialized asset bytes do not match its exact snapshot")
    return asset
