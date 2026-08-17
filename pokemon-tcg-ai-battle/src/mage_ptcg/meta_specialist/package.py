"""Deterministic, structural-only specialist bundle archives.

This module deliberately stops at archive structure.  It does not stage a
repository entrypoint, import a policy, run CABT, or report submission
readiness.  Those concerns belong to Task 5B and consume the verified bytes
produced here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
from types import MappingProxyType
from typing import Callable, Final
import zlib

from mage_ptcg.deck_io import parse_deck_csv_bytes
from mage_ptcg.exact_file import ExactFileSnapshot, ExactFileSnapshotError, read_exact_regular_file
from mage_ptcg.knowledge.model import deck_identity_from_card_ids
from mage_ptcg.meta_specialist.contracts import BUNDLE_SIZE_LIMIT_BYTES, ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import (
    ArchetypeSpec,
    DeckAssetInput,
    DeckLineageError,
    DeckLockDecision,
    DeckQualificationError,
    QualifiedDeckAsset,
    create_deck_lock,
    qualify_deck_asset,
    require_lineage_deck,
    require_qualified_deck_asset,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, RuntimeContractError


_SPEC_SCHEMA: Final = "meta-specialist-bundle-spec-v1"
_MANIFEST_SCHEMA: Final = "meta-specialist-bundle-manifest-v1"
_STRUCTURAL_REPORT_SCHEMA: Final = "meta-specialist-structural-verification-v1"
_QUALIFIED_DECK_SCHEMA: Final = "meta-specialist-qualified-deck-asset-v1"
_DECK_LOCK_SCHEMA: Final = "meta-specialist-deck-lock-decision-v1"
_ENTRYPOINT_SCHEMA: Final = "meta-specialist-entrypoint-contract-v1"
_MANIFEST_NAME: Final = "meta_specialist_bundle.json"
_REQUIRED_TOP_LEVEL_FILES: Final = ("main.py", "deck.csv")
_BUNDLE_SIZE_LIMIT_BYTES = BUNDLE_SIZE_LIMIT_BYTES
_MAX_MEMBER_COUNT = 4_096
_MAX_EXPANDED_BYTES = 12_388_608 * 1024
_MAX_MEMBER_BYTES = _MAX_EXPANDED_BYTES
_SENSITIVE_PATH_ALLOWLIST: Final[frozenset[str]] = frozenset()
_SENSITIVE_BYTES: Final = (
    b"kaggle_key", b"kaggle_username", b"aws_secret", b"private key",
    b"authorization", b"cookie", b"/home/", b"\\users\\", b"file://",
)
_SENSITIVE_PATH_PARTS: Final = (
    "kaggle.json", ".env", ".git", ".ssh", "ssh", "history", "cache",
    "tests", "docs", "data", "report", "reports", "experiments",
)


class BundleContractError(ValueError):
    """Raised when a structural package contract is malformed."""


class BundleSecurityError(BundleContractError):
    """Raised when a path, archive, or byte stream is unsafe to trust."""


@dataclass(frozen=True, slots=True)
class BlockedResource:
    """A sanitized host-capacity result; it is never a readiness report."""

    message: str
    code: str = field(default="host_resource_unavailable", init=False)

    def __post_init__(self) -> None:
        _validate_sanitized_message(self.message)

    def to_payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BundleContractError("value is not canonical JSON") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _content_id(domain: str, payload: object) -> str:
    return _sha256(domain.encode("utf-8") + b"\0" + _canonical_json_bytes(payload))


def _require_digest(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise BundleContractError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _strict_int(value: object, field_name: str, *, positive: bool = False) -> int:
    if type(value) is not int or (positive and value <= 0) or (not positive and value < 0):
        qualifier = "positive" if positive else "nonnegative"
        raise BundleContractError(f"{field_name} must be a {qualifier} built-in integer")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise BundleContractError("JSON object has duplicate keys")
        document[key] = value
    return document


def _reject_json_constant(_: str) -> object:
    raise BundleContractError("JSON contains a nonfinite number")


def _load_canonical_json(payload: bytes, *, trailing_lf: bool) -> dict[str, object]:
    expected = payload[:-1] if trailing_lf and payload.endswith(b"\n") else payload
    if trailing_lf and (not payload.endswith(b"\n") or payload.endswith(b"\n\n")):
        raise BundleContractError("local specification must end with exactly one LF")
    try:
        document = json.loads(
            expected.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleContractError("could not parse strict JSON") from exc
    if type(document) is not dict:
        raise BundleContractError("JSON document must be an object")
    canonical = _canonical_json_bytes(document)
    if expected != canonical:
        raise BundleContractError("JSON document is not canonical")
    return document


def _safe_member_name(value: object, field_name: str = "member") -> str:
    if type(value) is not str or not value:
        raise BundleSecurityError(f"unsafe member {field_name}: name must be a nonempty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleSecurityError(f"unsafe member {field_name}: invalid UTF-8") from exc
    if len(encoded) > 100 or "\\" in value or "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BundleSecurityError(f"unsafe member {field_name}")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/") or value.endswith("/") or any(part in ("", ".", "..") for part in path.parts):
        raise BundleSecurityError(f"unsafe member {field_name}")
    if any(len(part.encode("utf-8")) > 100 for part in path.parts):
        raise BundleSecurityError(f"unsafe member {field_name}")
    lower_parts = tuple(part.lower() for part in path.parts)
    if value not in _SENSITIVE_PATH_ALLOWLIST and (
        any(part in _SENSITIVE_PATH_PARTS for part in lower_parts)
        or any(part.endswith((".pem", ".key", ".p12", ".pfx")) for part in lower_parts)
    ):
        raise BundleSecurityError("sensitive member path is not allowed")
    return value


def _scan_sensitive_bytes(payload: bytes, *, field_name: str) -> None:
    lowered = payload.lower()
    if any(marker in lowered for marker in _SENSITIVE_BYTES):
        raise BundleSecurityError(f"sensitive marker in {field_name}")


def _validate_sanitized_message(value: object) -> str:
    if type(value) is not str:
        raise BundleContractError("blocked-resource message must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleContractError("blocked-resource message must be UTF-8") from exc
    if not encoded or len(encoded) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BundleContractError("blocked-resource message must be a sanitized 1--512 byte string")
    _scan_sensitive_bytes(encoded, field_name="blocked-resource message")
    if "/" in value or "\\" in value:
        raise BundleContractError("blocked-resource message must not contain a path")
    return value


def _validate_freeform(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise BundleContractError(f"{field_name} must be an exact string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BundleContractError(f"{field_name} must be UTF-8") from exc
    if not encoded or len(encoded) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BundleContractError(f"{field_name} must be a sanitized 1--512 byte string")
    _scan_sensitive_bytes(encoded, field_name=field_name)
    lower = value.lower()
    if "\\" in value or value.startswith("/") or value.startswith("~") or lower.startswith("file:") or "/../" in value or value.startswith("../") or value.endswith("/.."):
        raise BundleContractError(f"{field_name} contains an unsafe path")
    if len(value) >= 2 and value[1] == ":":
        raise BundleContractError(f"{field_name} contains an unsafe path")
    if "://" in value:
        authority = value.split("://", 1)[1].split("/", 1)[0]
        if "@" in authority:
            raise BundleContractError(f"{field_name} contains URL user-info")
    return value


def _assert_no_symlink(path: Path, *, directory: bool) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise BundleSecurityError(f"source path is unavailable: {path.name}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise BundleSecurityError("source path contains a symlink")
    if directory and not stat.S_ISDIR(info.st_mode):
        raise BundleSecurityError("source root must be a directory")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise BundleSecurityError("source member must be a regular file")


def _assert_directory_ancestors_are_safe(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    for ancestor in reversed((absolute, *absolute.parents)):
        _assert_no_symlink(ancestor, directory=True)
    return absolute


def _assert_source_path_is_safe(root: Path, member: str | None = None) -> Path:
    absolute_root = Path(os.path.abspath(os.fspath(root)))
    # ``lstat(root)`` alone follows intermediate components.  Every component
    # from the filesystem anchor through the declared root is therefore part
    # of the trust boundary, not just the final directory.
    _assert_directory_ancestors_are_safe(absolute_root)
    if member is None:
        return absolute_root
    current = absolute_root
    for part in PurePosixPath(member).parts:
        current = current / part
        _assert_no_symlink(current, directory=part != PurePosixPath(member).parts[-1])
    try:
        common = os.path.commonpath((os.fspath(absolute_root), os.path.abspath(os.fspath(current))))
    except ValueError as exc:
        raise BundleSecurityError("source member escapes source root") from exc
    if common != os.fspath(absolute_root):
        raise BundleSecurityError("source member escapes source root")
    return current


def _inside_root(candidate: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((os.path.abspath(os.fspath(candidate)), os.path.abspath(os.fspath(root)))) == os.path.abspath(os.fspath(root))
    except ValueError:
        return False


def _record(path: str, payload: bytes) -> dict[str, object]:
    return {"path": path, "sha256": _sha256(payload), "size": len(payload)}


def _literal_cabt_contract_id() -> str:
    schemas = (
        (0, 0), (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7),
        (1, 8), (1, 9), (1, 10), (1, 11), (1, 12), (1, 13), (1, 14),
        (1, 15), (1, 16), (1, 17), (1, 18), (1, 19), (1, 20), (1, 21),
        (1, 22), (1, 23), (1, 24), (1, 25), (2, 26), (2, 27), (2, 28),
        (3, 29), (4, 30), (4, 31), (4, 32), (4, 33), (5, 34), (6, 35),
        (6, 36), (7, 37), (8, 38), (8, 39), (8, 40), (9, 41), (9, 42),
        (9, 43), (9, 44), (9, 45), (9, 46), (10, 47), (10, 48),
    )
    return _content_id(
        "meta-specialist-cabt-agent-json-contract-v1",
        {
            "schema_version": "meta-specialist-cabt-agent-json-contract-v1",
            "selection_schemas": [list(item) for item in schemas],
            "ordered_selection_schemas": [[5, 34]],
        },
    )


@dataclass(frozen=True, slots=True)
class DependencyContractIds:
    cabt_agent_json_contract_id: str
    runtime_constraints_id: str
    ladder_mechanics_id: str
    entrypoint_contract_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "cabt_agent_json_contract_id", "runtime_constraints_id",
            "ladder_mechanics_id", "entrypoint_contract_id",
        ):
            _require_digest(getattr(self, field_name), field_name)
        if self.cabt_agent_json_contract_id != _literal_cabt_contract_id():
            raise BundleContractError("cabt_agent_json_contract_id is not the literal v1 contract")

    def to_payload(self) -> dict[str, str]:
        return {
            "cabt_agent_json_contract_id": self.cabt_agent_json_contract_id,
            "runtime_constraints_id": self.runtime_constraints_id,
            "ladder_mechanics_id": self.ladder_mechanics_id,
            "entrypoint_contract_id": self.entrypoint_contract_id,
        }

    @classmethod
    def from_payload(cls, value: object) -> "DependencyContractIds":
        if type(value) is not dict or set(value) != {
            "cabt_agent_json_contract_id", "runtime_constraints_id",
            "ladder_mechanics_id", "entrypoint_contract_id",
        }:
            raise BundleContractError("dependency_contract_ids has invalid fields")
        return cls(**value)  # type: ignore[arg-type]


def _qualified_payload(asset: QualifiedDeckAsset) -> dict[str, object]:
    require_qualified_deck_asset(asset)
    return {
        "schema_version": _QUALIFIED_DECK_SCHEMA,
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
    }


_QUALIFIED_KEYS: Final = frozenset({
    "schema_version", "asset_id", "archetype_id", "card_ids", "deck_identity",
    "deck_file_sha256", "source_ref", "source_commit", "asset_class",
    "usage_boundary", "policy_compatibility", "card_database_version", "card_count",
    "cabt_legality_status", "cabt_legality_evidence",
})


def _validate_qualified_payload(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != _QUALIFIED_KEYS or value.get("schema_version") != _QUALIFIED_DECK_SCHEMA:
        raise BundleContractError("qualified_deck_asset has invalid fields")
    cards = value["card_ids"]
    if type(cards) is not list or len(cards) != 60 or any(type(card) is not int or card <= 0 for card in cards):
        raise BundleContractError("qualified_deck_asset card_ids must contain exactly 60 positive integers")
    if type(value["card_count"]) is not int or value["card_count"] != 60:
        raise BundleContractError("qualified_deck_asset card_count must equal 60")
    for field_name in (
        "asset_id", "archetype_id", "source_ref", "policy_compatibility",
        "card_database_version", "cabt_legality_evidence",
    ):
        _validate_freeform(value[field_name], f"qualified_deck_asset.{field_name}")
    if type(value["source_commit"]) is not str or len(value["source_commit"]) != 40 or any(char not in "0123456789abcdef" for char in value["source_commit"]):
        raise BundleContractError("qualified_deck_asset source_commit is invalid")
    if value["asset_class"] not in {"deck_only", "runnable_rule", "checkpoint_teacher"} or type(value["asset_class"]) is not str:
        raise BundleContractError("qualified_deck_asset asset_class is invalid")
    if value["usage_boundary"] != "bundle_allowed":
        raise BundleContractError("qualified_deck_asset must be bundle_allowed")
    if value["cabt_legality_status"] != "passed":
        raise BundleContractError("qualified_deck_asset CABT legality must have passed")
    _require_digest(value["deck_file_sha256"], "qualified_deck_asset.deck_file_sha256")
    if type(value["deck_identity"]) is not str or value["deck_identity"] != deck_identity_from_card_ids(cards):
        raise BundleContractError("qualified_deck_asset deck_identity is invalid")
    return dict(value)


def _deck_lock_payload(lock: DeckLockDecision) -> dict[str, object]:
    return {
        "schema_version": _DECK_LOCK_SCHEMA,
        "archetype_id": lock.archetype_id,
        "selected_deck_identity": lock.selected_deck_identity,
        "compared_deck_identities": list(lock.compared_deck_identities),
        "foundation_init_id": lock.foundation_init_id,
        "joint_race_schedule_id": lock.joint_race_schedule_id,
        "equal_transition_budget": lock.equal_transition_budget,
        "deck_lock_id": lock.deck_lock_id,
        "policy_lineage_id": lock.policy_lineage_id,
    }


_LOCK_KEYS: Final = frozenset({
    "schema_version", "archetype_id", "selected_deck_identity", "compared_deck_identities",
    "foundation_init_id", "joint_race_schedule_id", "equal_transition_budget",
    "deck_lock_id", "policy_lineage_id",
})


def _lock_from_payload(value: object) -> DeckLockDecision:
    if type(value) is not dict or set(value) != _LOCK_KEYS or value.get("schema_version") != _DECK_LOCK_SCHEMA:
        raise BundleContractError("deck_lock has invalid fields")
    compared = value["compared_deck_identities"]
    if type(compared) is not list:
        raise BundleContractError("deck_lock compared_deck_identities must be a JSON array")
    try:
        lock = DeckLockDecision(
            archetype_id=value["archetype_id"], selected_deck_identity=value["selected_deck_identity"],
            compared_deck_identities=tuple(compared), foundation_init_id=value["foundation_init_id"],
            joint_race_schedule_id=value["joint_race_schedule_id"],
            equal_transition_budget=value["equal_transition_budget"], deck_lock_id=value["deck_lock_id"],
            policy_lineage_id=value["policy_lineage_id"],
        )
    except (DeckLineageError, TypeError) as exc:
        raise BundleContractError("deck_lock is invalid") from exc
    return lock


def _runtime_from_payload(value: object) -> RuntimeConstraintManifest:
    if type(value) is not dict:
        raise BundleContractError("runtime_constraints must be an object")
    expected_keys = set(RuntimeConstraintManifest.frozen_v1().to_payload())
    if set(value) != expected_keys or type(value.get("host_dependencies")) is not list:
        raise BundleContractError("runtime_constraints has invalid fields")
    candidate = dict(value)
    candidate["host_dependencies"] = tuple(candidate["host_dependencies"])
    try:
        result = RuntimeConstraintManifest(**candidate)
    except (RuntimeContractError, TypeError) as exc:
        raise BundleContractError("runtime_constraints is invalid") from exc
    if result.to_payload() != value:
        raise BundleContractError("runtime_constraints is not the exact v1 payload")
    return result


def _ladder_payload(value: object) -> dict[str, object]:
    if type(value) is not dict or "checked_at_utc" not in value:
        raise BundleContractError("ladder_mechanics must be an exact published payload")
    checked_at = value["checked_at_utc"]
    if type(checked_at) is not str:
        raise BundleContractError("ladder_mechanics checked_at_utc must be a string")
    try:
        expected = ladder_mechanics_payload(checked_at_utc=checked_at)
    except ValueError as exc:
        raise BundleContractError("ladder_mechanics checked_at_utc is invalid") from exc
    expected["ladder_mechanics_id"] = _content_id("meta-specialist-ladder-mechanics-v1", expected)
    if set(value) != set(expected) or any(type(value[key]) is not type(expected[key]) or value[key] != expected[key] for key in expected):
        raise BundleContractError("ladder_mechanics is not the exact published payload")
    return dict(value)


def _validate_member_sequence(value: object, field_name: str) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        raise BundleContractError(f"{field_name} must be a nonempty tuple")
    names = tuple(_safe_member_name(item, field_name) for item in value)
    if names != tuple(sorted(names)) or len(set(names)) != len(names):
        raise BundleContractError(f"{field_name} must be sorted and unique")
    return names


def _snapshots_for_spec(spec: "BundleSpec") -> dict[str, ExactFileSnapshot]:
    root = _assert_source_path_is_safe(spec.source_root)
    snapshots: dict[str, ExactFileSnapshot] = {}
    for member in spec.members:
        path = _assert_source_path_is_safe(root, member)
        try:
            snapshot = read_exact_regular_file(path, max_bytes=_MAX_MEMBER_BYTES)
        except ExactFileSnapshotError as exc:
            raise BundleSecurityError(f"could not snapshot source member {member}") from exc
        _assert_source_path_is_safe(root, member)
        _scan_sensitive_bytes(snapshot.payload, field_name=member)
        snapshots[member] = snapshot
    return snapshots


def _entrypoint_contract_from_payloads(
    payloads: Mapping[str, bytes], *, policy_members: tuple[str, ...] = (),
) -> dict[str, object]:
    names = tuple(sorted(
        name for name in payloads
        if name.endswith(".py") and name not in policy_members
    ))
    if "main.py" not in names or "policy_loader.py" not in names:
        raise BundleContractError("entrypoint contract requires main.py and policy_loader.py")
    records = [_record(name, payloads[name]) for name in names]
    contract_without_id = {"schema_version": _ENTRYPOINT_SCHEMA, "members": records}
    return {
        **contract_without_id,
        "entrypoint_contract_id": _content_id(_ENTRYPOINT_SCHEMA, contract_without_id),
    }


def derive_entrypoint_contract_id(
    source_root: Path,
    members: tuple[str, ...],
    *,
    policy_members: tuple[str, ...] = (),
) -> str:
    """Derive the structural Task 5A entrypoint identity from frozen safe files.

    Task 5B narrows this conservative declared-Python set to the actual recursive
    import closure.  Task 5A deliberately binds every declared non-policy
    Python behavior file, so no helper can silently escape its identity.
    """
    root = _assert_source_path_is_safe(Path(source_root))
    names = _validate_member_sequence(members, "members")
    policy_names = _validate_member_sequence(policy_members, "policy_members") if policy_members else ()
    if any(member not in names for member in policy_names):
        raise BundleContractError("policy_members must be declared members")
    payloads: dict[str, bytes] = {}
    for member in names:
        if not member.endswith(".py") or member in policy_names:
            continue
        path = _assert_source_path_is_safe(root, member)
        try:
            payloads[member] = read_exact_regular_file(path, max_bytes=_MAX_MEMBER_BYTES).payload
        except ExactFileSnapshotError as exc:
            raise BundleSecurityError("could not snapshot structural entrypoint") from exc
    return _entrypoint_contract_from_payloads(
        payloads, policy_members=policy_names,
    )["entrypoint_contract_id"]  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class BundleSpec:
    source_root: Path
    members: tuple[str, ...]
    deck_member: str
    policy_entrypoint_member: str
    qualified_deck_asset: QualifiedDeckAsset
    deck_lock: DeckLockDecision
    runtime_constraints: RuntimeConstraintManifest
    ladder_mechanics: Mapping[str, object]
    dependency_contract_ids: DependencyContractIds
    candidate_class: str
    policy_members: tuple[str, ...]
    model_member: str | None
    policy_identity: str
    checkpoint_lineage_id: str | None
    checkpoint_lineage_reason: str | None
    schema_version: str = field(default=_SPEC_SCHEMA, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_root", Path(self.source_root))
        object.__setattr__(self, "ladder_mechanics", MappingProxyType(dict(self.ladder_mechanics)))
        self._validate_shape()
        self.validate()

    def _validate_shape(self) -> None:
        if self.schema_version != _SPEC_SCHEMA:
            raise BundleContractError("unsupported BundleSpec schema")
        names = _validate_member_sequence(self.members, "members")
        policy_names = _validate_member_sequence(self.policy_members, "policy_members")
        if self.deck_member != "deck.csv" or self.policy_entrypoint_member != "policy_loader.py":
            raise BundleContractError("deck_member and policy_entrypoint_member have fixed names")
        if self.deck_member not in names or self.policy_entrypoint_member not in names or "main.py" not in names:
            raise BundleContractError("members must include main.py, deck.csv, and policy_loader.py")
        if any(member not in names for member in policy_names):
            raise BundleContractError("policy_members must be declared members")
        try:
            require_qualified_deck_asset(self.qualified_deck_asset)
            require_lineage_deck(self.deck_lock, self.qualified_deck_asset.deck_identity)
        except (DeckQualificationError, DeckLineageError) as exc:
            raise BundleContractError("qualified deck asset or deck lock is invalid") from exc
        if self.qualified_deck_asset.usage_boundary != "bundle_allowed":
            raise BundleContractError("qualified deck asset must be bundle_allowed")
        if type(self.runtime_constraints) is not RuntimeConstraintManifest:
            raise BundleContractError("runtime_constraints must be RuntimeConstraintManifest")
        _runtime_from_payload(self.runtime_constraints.to_payload())
        ladder = _ladder_payload(dict(self.ladder_mechanics))
        if type(self.dependency_contract_ids) is not DependencyContractIds:
            raise BundleContractError("dependency_contract_ids must be DependencyContractIds")
        if self.dependency_contract_ids.runtime_constraints_id != self.runtime_constraints.runtime_constraints_id or self.dependency_contract_ids.ladder_mechanics_id != ladder["ladder_mechanics_id"]:
            raise BundleContractError("dependency_contract_ids do not bind runtime and ladder contracts")
        _require_digest(self.policy_identity, "policy_identity")
        if self.candidate_class == "checkpointed_specialist":
            if type(self.model_member) is not str or policy_names != (self.model_member,):
                raise BundleContractError("checkpointed policy must have exactly its model as policy_members")
            _safe_member_name(self.model_member, "model_member")
            if self.checkpoint_lineage_id != self.deck_lock.policy_lineage_id or self.checkpoint_lineage_reason is not None:
                raise BundleContractError("checkpointed policy lineage must bind the deck lock")
        elif self.candidate_class == "static_rule_bundle":
            if self.model_member is not None or self.checkpoint_lineage_id is not None or self.checkpoint_lineage_reason != "not_applicable_static_policy":
                raise BundleContractError("static policy model and lineage fields are invalid")
            if self.deck_member in policy_names or self.policy_entrypoint_member in policy_names or "main.py" in policy_names:
                raise BundleContractError("static policy_members must exclude trusted entrypoint and deck files")
        else:
            raise BundleContractError("candidate_class is invalid")

    def validate(self) -> None:
        """Revalidate all filesystem-bound facts from one source snapshot each."""
        snapshots = _snapshots_for_spec(self)
        _validate_spec_snapshots(self, snapshots)

    def to_local_payload(self, *, spec_path: Path) -> dict[str, object]:
        base = Path(spec_path).parent
        relative_root = os.path.relpath(self.source_root, base).replace(os.sep, "/")
        return {
            "schema_version": _SPEC_SCHEMA,
            "source_root": relative_root,
            "members": list(self.members),
            "deck_member": self.deck_member,
            "policy_entrypoint_member": self.policy_entrypoint_member,
            "qualified_deck_asset": _qualified_payload(self.qualified_deck_asset),
            "deck_lock": _deck_lock_payload(self.deck_lock),
            "runtime_constraints": self.runtime_constraints.to_payload(),
            "ladder_mechanics": dict(self.ladder_mechanics),
            "dependency_contract_ids": self.dependency_contract_ids.to_payload(),
            "candidate_class": self.candidate_class,
            "policy_members": list(self.policy_members),
            "model_member": self.model_member,
            "policy_identity": self.policy_identity,
            "checkpoint_lineage_id": self.checkpoint_lineage_id,
            "checkpoint_lineage_reason": self.checkpoint_lineage_reason,
        }

    @classmethod
    def from_payload(cls, payload: object, *, spec_path: Path) -> "BundleSpec":
        if type(payload) is not dict or set(payload) != _LOCAL_SPEC_KEYS or payload.get("schema_version") != _SPEC_SCHEMA:
            raise BundleContractError("BundleSpec has invalid fields")
        root_value = payload["source_root"]
        if type(root_value) is not str or not root_value or Path(root_value).is_absolute() or "\\" in root_value:
            raise BundleContractError("BundleSpec source_root must be a relative POSIX path")
        root = Path(spec_path).parent / Path(root_value)
        members_value = payload["members"]
        policy_value = payload["policy_members"]
        if type(members_value) is not list or type(policy_value) is not list:
            raise BundleContractError("BundleSpec member fields must be JSON arrays")
        qualified_payload = _validate_qualified_payload(payload["qualified_deck_asset"])
        deck_member = payload["deck_member"]
        if deck_member != "deck.csv":
            raise BundleContractError("BundleSpec deck_member must be deck.csv")
        qualified = _qualify_payload_from_source(qualified_payload, root / deck_member)
        lock = _lock_from_payload(payload["deck_lock"])
        runtime = _runtime_from_payload(payload["runtime_constraints"])
        return cls(
            source_root=root, members=tuple(members_value), deck_member=deck_member,
            policy_entrypoint_member=payload["policy_entrypoint_member"],
            qualified_deck_asset=qualified, deck_lock=lock, runtime_constraints=runtime,
            ladder_mechanics=_ladder_payload(payload["ladder_mechanics"]),
            dependency_contract_ids=DependencyContractIds.from_payload(payload["dependency_contract_ids"]),
            candidate_class=payload["candidate_class"], policy_members=tuple(policy_value),
            model_member=payload["model_member"], policy_identity=payload["policy_identity"],
            checkpoint_lineage_id=payload["checkpoint_lineage_id"],
            checkpoint_lineage_reason=payload["checkpoint_lineage_reason"],
        )


_LOCAL_SPEC_KEYS: Final = frozenset({
    "schema_version", "source_root", "members", "deck_member", "policy_entrypoint_member",
    "qualified_deck_asset", "deck_lock", "runtime_constraints", "ladder_mechanics",
    "dependency_contract_ids", "candidate_class", "policy_members", "model_member",
    "policy_identity", "checkpoint_lineage_id", "checkpoint_lineage_reason",
})


def _qualify_payload_from_source(payload: Mapping[str, object], deck_path: Path) -> QualifiedDeckAsset:
    """Re-establish process-local qualification after byte-for-byte deck validation."""
    try:
        asset = DeckAssetInput.from_path(
            asset_id=payload["asset_id"], archetype_id=payload["archetype_id"], path=deck_path,
            source_ref=payload["source_ref"], source_commit=payload["source_commit"],
            asset_class=payload["asset_class"], usage_boundary=payload["usage_boundary"],
            policy_compatibility=payload["policy_compatibility"], card_database_version=payload["card_database_version"],
        )
        cards = tuple(payload["card_ids"])
        if asset.card_ids != cards or asset.deck_identity != payload["deck_identity"] or asset.deck_file_sha256 != payload["deck_file_sha256"]:
            raise BundleContractError("qualified_deck_asset does not match deck.csv")
        qualified = qualify_deck_asset(
            asset, ArchetypeSpec(asset.archetype_id, (), (cards[0],), "qualified_not_trained"),
            known_card_ids=set(cards), cabt_legality=lambda _: (True, payload["cabt_legality_evidence"]),
        )
    except (DeckQualificationError, IndexError, TypeError) as exc:
        raise BundleContractError("could not revalidate qualified_deck_asset") from exc
    if _qualified_payload(qualified) != dict(payload):
        raise BundleContractError("qualified_deck_asset does not exactly revalidate")
    return qualified


def load_bundle_spec(spec_path: Path) -> BundleSpec:
    """Load one strict, canonical local spec relative to its own location."""
    path = Path(spec_path)
    try:
        payload = read_exact_regular_file(path, max_bytes=4 * 1024 * 1024).payload
    except ExactFileSnapshotError as exc:
        raise BundleSecurityError("could not read local bundle specification") from exc
    return BundleSpec.from_payload(_load_canonical_json(payload, trailing_lf=True), spec_path=path)


def write_bundle_spec(spec: BundleSpec, spec_path: Path) -> None:
    if type(spec) is not BundleSpec:
        raise BundleContractError("spec must be BundleSpec")
    spec.validate()
    payload = _canonical_json_bytes(spec.to_local_payload(spec_path=Path(spec_path))) + b"\n"
    _write_new_or_identical(Path(spec_path), payload)


def _validate_spec_snapshots(spec: BundleSpec, snapshots: Mapping[str, ExactFileSnapshot]) -> None:
    payloads = {name: snapshot.payload for name, snapshot in snapshots.items()}
    if tuple(payloads) != spec.members:
        raise BundleContractError("source snapshots do not match declared members")
    qualified = _validate_qualified_payload(_qualified_payload(spec.qualified_deck_asset))
    deck_bytes = payloads[spec.deck_member]
    try:
        cards = parse_deck_csv_bytes(deck_bytes)
    except ValueError as exc:
        raise BundleContractError("deck.csv is invalid") from exc
    if tuple(cards) != spec.qualified_deck_asset.card_ids or _sha256(deck_bytes) != qualified["deck_file_sha256"] or deck_identity_from_card_ids(cards) != qualified["deck_identity"]:
        raise BundleContractError("deck.csv does not exactly bind qualified_deck_asset")
    lock = _lock_from_payload(_deck_lock_payload(spec.deck_lock))
    try:
        require_lineage_deck(lock, spec.qualified_deck_asset.deck_identity)
    except DeckLineageError as exc:
        raise BundleContractError("deck_lock does not bind deck.csv") from exc
    actual_entrypoint_id = _entrypoint_contract_from_payloads(
        payloads, policy_members=spec.policy_members,
    )["entrypoint_contract_id"]
    if spec.dependency_contract_ids.entrypoint_contract_id != actual_entrypoint_id:
        raise BundleContractError("entrypoint_contract_id does not match archived entrypoint bytes")
    policy_payloads = {name: payloads[name] for name in spec.policy_members}
    if spec.candidate_class == "checkpointed_specialist":
        assert spec.model_member is not None
        expected_policy_identity = _sha256(policy_payloads[spec.model_member])
    else:
        expected_policy_identity = _content_id(
            "meta-specialist-static-policy-v1", [_record(name, policy_payloads[name]) for name in spec.policy_members],
        )
    if spec.policy_identity != expected_policy_identity:
        raise BundleContractError("policy_identity does not match frozen policy bytes")


def _manifest_from_spec(spec: BundleSpec, snapshots: Mapping[str, ExactFileSnapshot]) -> dict[str, object]:
    _validate_spec_snapshots(spec, snapshots)
    payloads = {name: snapshot.payload for name, snapshot in snapshots.items()}
    body: dict[str, object] = {
        "schema_version": _MANIFEST_SCHEMA,
        "members": [_record(name, payloads[name]) for name in spec.members],
        "required_top_level_files": list(_REQUIRED_TOP_LEVEL_FILES),
        "deck_member": spec.deck_member,
        "policy_entrypoint_member": spec.policy_entrypoint_member,
        "qualified_deck_asset": _qualified_payload(spec.qualified_deck_asset),
        "deck_lock": _deck_lock_payload(spec.deck_lock),
        "runtime_constraints": spec.runtime_constraints.to_payload(),
        "ladder_mechanics": dict(spec.ladder_mechanics),
        "dependency_contract_ids": spec.dependency_contract_ids.to_payload(),
        "entrypoint_contract": _entrypoint_contract_from_payloads(
            payloads, policy_members=spec.policy_members,
        ),
        "candidate_class": spec.candidate_class,
        "policy_members": list(spec.policy_members),
        "model_member": spec.model_member,
        "policy_identity": spec.policy_identity,
        "checkpoint_lineage_id": spec.checkpoint_lineage_id,
        "checkpoint_lineage_reason": spec.checkpoint_lineage_reason,
    }
    return {"schema_version": _MANIFEST_SCHEMA, "content_hash": _content_id(_MANIFEST_SCHEMA, body), **{key: value for key, value in body.items() if key != "schema_version"}}


_MANIFEST_KEYS: Final = frozenset({
    "schema_version", "content_hash", "members", "required_top_level_files", "deck_member",
    "policy_entrypoint_member", "qualified_deck_asset", "deck_lock", "runtime_constraints",
    "ladder_mechanics", "dependency_contract_ids", "entrypoint_contract", "candidate_class",
    "policy_members", "model_member", "policy_identity", "checkpoint_lineage_id",
    "checkpoint_lineage_reason",
})


def _validate_member_records(value: object, payloads: Mapping[str, bytes]) -> tuple[str, ...]:
    if type(value) is not list or not value or len(value) > _MAX_MEMBER_COUNT:
        raise BundleSecurityError("archive member records are invalid")
    names: list[str] = []
    for item in value:
        if type(item) is not dict or set(item) != {"path", "sha256", "size"}:
            raise BundleContractError("archive member record has invalid fields")
        name = _safe_member_name(item["path"], "record path")
        size = _strict_int(item["size"], "member size")
        digest = _require_digest(item["sha256"], "member sha256")
        if name not in payloads or len(payloads[name]) != size or _sha256(payloads[name]) != digest:
            raise BundleContractError("archive member record does not match frozen bytes")
        names.append(name)
    if tuple(names) != tuple(sorted(names)) or len(set(names)) != len(names) or tuple(names) != tuple(sorted(payloads)):
        raise BundleContractError("archive member records do not exactly match archive names")
    return tuple(names)


def _validate_entrypoint_contract(
    value: object, payloads: Mapping[str, bytes], *, policy_members: tuple[str, ...],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != {"schema_version", "members", "entrypoint_contract_id"} or value.get("schema_version") != _ENTRYPOINT_SCHEMA:
        raise BundleContractError("entrypoint_contract has invalid fields")
    expected = _entrypoint_contract_from_payloads(payloads, policy_members=policy_members)
    if value != expected:
        raise BundleContractError("entrypoint_contract does not match structural entrypoint bytes")
    return expected


def _validate_manifest(manifest: object, payloads: Mapping[str, bytes]) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS or manifest.get("schema_version") != _MANIFEST_SCHEMA:
        raise BundleContractError("bundle manifest has invalid fields")
    content_hash = _require_digest(manifest["content_hash"], "manifest content_hash")
    without_hash = {key: value for key, value in manifest.items() if key != "content_hash"}
    if content_hash != _content_id(_MANIFEST_SCHEMA, without_hash):
        raise BundleContractError("bundle manifest content_hash is invalid")
    if manifest["required_top_level_files"] != list(_REQUIRED_TOP_LEVEL_FILES):
        raise BundleContractError("bundle manifest required top-level files are invalid")
    names = _validate_member_records(manifest["members"], payloads)
    if manifest["deck_member"] != "deck.csv" or manifest["policy_entrypoint_member"] != "policy_loader.py":
        raise BundleContractError("bundle manifest fixed member names are invalid")
    if "main.py" not in names or "deck.csv" not in names or "policy_loader.py" not in names:
        raise BundleContractError("bundle manifest is missing required members")
    qualified = _validate_qualified_payload(manifest["qualified_deck_asset"])
    try:
        cards = parse_deck_csv_bytes(payloads["deck.csv"])
    except ValueError as exc:
        raise BundleContractError("archived deck.csv is invalid") from exc
    if list(cards) != qualified["card_ids"] or _sha256(payloads["deck.csv"]) != qualified["deck_file_sha256"] or deck_identity_from_card_ids(cards) != qualified["deck_identity"]:
        raise BundleContractError("archived deck.csv does not bind qualified_deck_asset")
    lock = _lock_from_payload(manifest["deck_lock"])
    try:
        require_lineage_deck(lock, qualified["deck_identity"])
    except DeckLineageError as exc:
        raise BundleContractError("archived deck lock does not bind the qualified deck") from exc
    runtime = _runtime_from_payload(manifest["runtime_constraints"])
    ladder = _ladder_payload(manifest["ladder_mechanics"])
    dependencies = DependencyContractIds.from_payload(manifest["dependency_contract_ids"])
    policy_members = manifest["policy_members"]
    if type(policy_members) is not list:
        raise BundleContractError("policy_members must be a JSON array")
    policy_names = tuple(_safe_member_name(name, "policy member") for name in policy_members)
    if not policy_names or policy_names != tuple(sorted(policy_names)) or len(set(policy_names)) != len(policy_names) or any(name not in names for name in policy_names):
        raise BundleContractError("policy_members are invalid")
    entrypoint = _validate_entrypoint_contract(
        manifest["entrypoint_contract"], payloads, policy_members=policy_names,
    )
    if dependencies.runtime_constraints_id != runtime.runtime_constraints_id or dependencies.ladder_mechanics_id != ladder["ladder_mechanics_id"] or dependencies.entrypoint_contract_id != entrypoint["entrypoint_contract_id"]:
        raise BundleContractError("dependency contract IDs do not bind archive content")
    policy_identity = _require_digest(manifest["policy_identity"], "policy_identity")
    candidate = manifest["candidate_class"]
    if candidate == "checkpointed_specialist":
        if type(manifest["model_member"]) is not str or policy_names != (manifest["model_member"],) or manifest["checkpoint_lineage_id"] != lock.policy_lineage_id or manifest["checkpoint_lineage_reason"] is not None:
            raise BundleContractError("checkpointed policy fields are invalid")
        expected_identity = _sha256(payloads[manifest["model_member"]])
    elif candidate == "static_rule_bundle":
        if manifest["model_member"] is not None or manifest["checkpoint_lineage_id"] is not None or manifest["checkpoint_lineage_reason"] != "not_applicable_static_policy":
            raise BundleContractError("static policy fields are invalid")
        if any(name in {"deck.csv", "main.py", "policy_loader.py"} for name in policy_names):
            raise BundleContractError("static policy_members include trusted files")
        expected_identity = _content_id("meta-specialist-static-policy-v1", [_record(name, payloads[name]) for name in policy_names])
    else:
        raise BundleContractError("candidate_class is invalid")
    if policy_identity != expected_identity:
        raise BundleContractError("policy_identity does not match archived policy bytes")
    return dict(manifest)


@dataclass(frozen=True, slots=True)
class StructuralVerificationReport:
    archive_sha256: str
    compressed_size_bytes: int
    manifest_content_hash: str
    deck_identity: str
    deck_file_sha256: str
    policy_identity: str
    candidate_class: str
    checkpoint_lineage_id: str | None
    checkpoint_lineage_reason: str | None
    runtime_constraints_id: str
    ladder_mechanics_id: str
    entrypoint_contract_id: str
    required_top_level_files: tuple[str, ...]
    member_count: int
    schema_version: str = field(default=_STRUCTURAL_REPORT_SCHEMA, init=False)
    status: str = field(default="structurally_verified", init=False)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "status": self.status,
            "archive_sha256": self.archive_sha256, "compressed_size_bytes": self.compressed_size_bytes,
            "manifest_content_hash": self.manifest_content_hash, "deck_identity": self.deck_identity,
            "deck_file_sha256": self.deck_file_sha256, "policy_identity": self.policy_identity,
            "candidate_class": self.candidate_class, "checkpoint_lineage_id": self.checkpoint_lineage_id,
            "checkpoint_lineage_reason": self.checkpoint_lineage_reason,
            "runtime_constraints_id": self.runtime_constraints_id,
            "ladder_mechanics_id": self.ladder_mechanics_id,
            "entrypoint_contract_id": self.entrypoint_contract_id,
            "required_top_level_files": list(self.required_top_level_files), "member_count": self.member_count,
        }


def _decompress_canonical_gzip(payload: bytes) -> bytes:
    if len(payload) < 18 or payload[:10] != b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff":
        raise BundleSecurityError("archive gzip header is not canonical")
    decoder = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        expanded = decoder.decompress(payload, _MAX_EXPANDED_BYTES + 1)
        expanded += decoder.flush()
    except zlib.error as exc:
        raise BundleSecurityError("archive gzip stream is invalid") from exc
    if len(expanded) > _MAX_EXPANDED_BYTES:
        raise BundleSecurityError("archive expanded bytes exceed the permitted limit")
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise BundleSecurityError("archive gzip stream has trailing or concatenated data")
    return expanded


def _tar_members(expanded: bytes) -> tuple[dict[str, bytes], tuple[str, ...]]:
    if len(expanded) < 1024 or not expanded.endswith(b"\0" * 1024):
        raise BundleSecurityError("archive tar terminator is invalid")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(expanded), mode="r:")
    except tarfile.TarError as exc:
        raise BundleSecurityError("archive tar stream is invalid") from exc
    try:
        members = archive.getmembers()
        if not members or len(members) > _MAX_MEMBER_COUNT:
            raise BundleSecurityError("archive member count is invalid")
        payloads: dict[str, bytes] = {}
        names: list[str] = []
        total = 0
        for member in members:
            name = _safe_member_name(member.name, "archive member")
            if member.type != tarfile.REGTYPE or not member.isfile() or member.mode != 0o644 or member.uid != 0 or member.gid != 0 or member.mtime != 0 or member.uname != "" or member.gname != "" or member.linkname != "" or member.pax_headers:
                raise BundleSecurityError("archive member metadata is not canonical")
            raw_header = expanded[member.offset:member.offset + 512]
            if raw_header[257:265] != b"ustar\x0000":
                raise BundleSecurityError("archive member is not USTAR")
            if member.size > _MAX_MEMBER_BYTES or total + member.size > _MAX_EXPANDED_BYTES:
                raise BundleSecurityError("archive member expanded size exceeds limit")
            source = archive.extractfile(member)
            if source is None:
                raise BundleSecurityError("archive member bytes are unavailable")
            contents = source.read(member.size + 1)
            if len(contents) != member.size:
                raise BundleSecurityError("archive member size does not match header")
            _scan_sensitive_bytes(contents, field_name=name)
            if name in payloads:
                raise BundleSecurityError("archive contains duplicate member names")
            payloads[name] = contents
            names.append(name)
            total += member.size
        if tuple(names) != tuple(sorted(names)):
            raise BundleSecurityError("archive member order is not canonical")
        return payloads, tuple(names)
    finally:
        archive.close()


def _verify_archive_payload(payload: bytes) -> tuple[StructuralVerificationReport, dict[str, bytes]]:
    expanded = _decompress_canonical_gzip(payload)
    all_payloads, names = _tar_members(expanded)
    if names.count(_MANIFEST_NAME) != 1:
        raise BundleContractError("archive must contain exactly one bundle manifest")
    try:
        manifest = _load_canonical_json(all_payloads[_MANIFEST_NAME], trailing_lf=False)
    except BundleContractError:
        raise
    payloads = {name: contents for name, contents in all_payloads.items() if name != _MANIFEST_NAME}
    verified = _validate_manifest(manifest, payloads)
    expected_names = tuple(sorted((*payloads, _MANIFEST_NAME)))
    if names != expected_names:
        raise BundleSecurityError("archive name order is not canonical")
    return StructuralVerificationReport(
        archive_sha256=_sha256(payload), compressed_size_bytes=len(payload),
        manifest_content_hash=verified["content_hash"], deck_identity=verified["qualified_deck_asset"]["deck_identity"],
        deck_file_sha256=verified["qualified_deck_asset"]["deck_file_sha256"], policy_identity=verified["policy_identity"],
        candidate_class=verified["candidate_class"], checkpoint_lineage_id=verified["checkpoint_lineage_id"],
        checkpoint_lineage_reason=verified["checkpoint_lineage_reason"],
        runtime_constraints_id=verified["runtime_constraints"]["runtime_constraints_id"],
        ladder_mechanics_id=verified["ladder_mechanics"]["ladder_mechanics_id"],
        entrypoint_contract_id=verified["entrypoint_contract"]["entrypoint_contract_id"],
        required_top_level_files=_REQUIRED_TOP_LEVEL_FILES, member_count=len(payloads),
    ), all_payloads


def _snapshot_archive(path: Path) -> bytes:
    archive_path = Path(path)
    try:
        return read_exact_regular_file(archive_path, max_bytes=_BUNDLE_SIZE_LIMIT_BYTES).payload
    except ExactFileSnapshotError as exc:
        raise BundleSecurityError("archive must be a bounded no-follow regular file") from exc


def verify_specialist_archive(path: Path) -> StructuralVerificationReport:
    """Verify a frozen archive snapshot without importing or executing it."""
    report, _ = _verify_archive_payload(_snapshot_archive(Path(path)))
    return report


def _canonical_archive_bytes(payloads: Mapping[str, bytes], manifest: Mapping[str, object]) -> bytes:
    entries = dict(payloads)
    entries[_MANIFEST_NAME] = _canonical_json_bytes(dict(manifest))
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(entries):
                contents = entries[name]
                info = tarfile.TarInfo(name)
                info.type = tarfile.REGTYPE
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.mtime = 0
                info.uname = ""
                info.gname = ""
                info.pax_headers = {}
                info.size = len(contents)
                archive.addfile(info, io.BytesIO(contents))
    return buffer.getvalue()


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("could not write archive")
        view = view[written:]


def _write_new_or_identical(
    target: Path,
    payload: bytes,
    *,
    verify_temporary: Callable[[bytes], object] | None = None,
) -> None:
    target = Path(os.path.abspath(os.fspath(target)))
    parent = target.parent
    _assert_no_symlink(parent, directory=True)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        for _ in range(32):
            candidate = parent / f".{target.name}.{os.urandom(16).hex()}.tmp"
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise BundleSecurityError("could not allocate exclusive sibling temporary")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if verify_temporary is not None:
            try:
                temporary_bytes = read_exact_regular_file(
                    temporary, max_bytes=max(len(payload), 1),
                ).payload
            except ExactFileSnapshotError as exc:
                raise BundleSecurityError("could not re-snapshot archive temporary") from exc
            if temporary_bytes != payload:
                raise BundleSecurityError("archive temporary bytes changed before publish")
            verify_temporary(temporary_bytes)
        try:
            os.link(temporary, target)
        except FileExistsError:
            try:
                current = read_exact_regular_file(target, max_bytes=max(len(payload), _BUNDLE_SIZE_LIMIT_BYTES)).payload
            except ExactFileSnapshotError as exc:
                raise BundleSecurityError("existing output is not a regular comparable archive") from exc
            if current != payload:
                raise BundleContractError("output collision has different bytes")
        os.unlink(temporary)
        temporary = None
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def build_specialist_archive(spec: BundleSpec, output_path: Path) -> StructuralVerificationReport:
    """Build and structurally verify a deterministic archive from frozen source bytes."""
    if type(spec) is not BundleSpec:
        raise BundleContractError("spec must be BundleSpec")
    output = Path(os.path.abspath(os.fspath(output_path)))
    root = _assert_source_path_is_safe(spec.source_root)
    if _inside_root(output, root):
        raise BundleSecurityError("output archive must not be inside source_root")
    snapshots = _snapshots_for_spec(spec)
    manifest = _manifest_from_spec(spec, snapshots)
    archive_bytes = _canonical_archive_bytes({name: snapshot.payload for name, snapshot in snapshots.items()}, manifest)
    if len(archive_bytes) > _BUNDLE_SIZE_LIMIT_BYTES:
        raise BundleSecurityError("compressed archive exceeds bundle size limit")
    report, _ = _verify_archive_payload(archive_bytes)
    _write_new_or_identical(
        output,
        archive_bytes,
        verify_temporary=_verify_archive_payload,
    )
    return report


def _prepare_destination(destination: Path) -> tuple[Path, bool]:
    destination = Path(os.path.abspath(os.fspath(destination)))
    _assert_directory_ancestors_are_safe(destination.parent)
    try:
        info = os.lstat(destination)
    except FileNotFoundError:
        try:
            os.mkdir(destination, 0o700)
        except FileExistsError:
            return _prepare_destination(destination)
        return destination, True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise BundleSecurityError("extraction destination must be an absent or nonsymlink directory")
    if os.listdir(destination):
        raise BundleSecurityError("extraction destination must be empty")
    return destination, False


def _extract_payloads(destination: Path, payloads: Mapping[str, bytes]) -> None:
    base_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name in sorted(payloads):
            current_fd = os.dup(base_fd)
            try:
                parts = PurePosixPath(name).parts
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o755, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current_fd)
                    os.close(current_fd)
                    current_fd = next_fd
                descriptor = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=current_fd)
                try:
                    _write_all(descriptor, payloads[name])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                os.close(current_fd)
        os.fsync(base_fd)
    finally:
        os.close(base_fd)


def _clean_partial_destination(destination: Path, *, remove_root: bool) -> None:
    """Remove only entries created in the destination that was empty at entry."""
    if remove_root:
        shutil.rmtree(destination)
        return
    for child in destination.iterdir():
        info = os.lstat(child)
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            shutil.rmtree(child)
        else:
            os.unlink(child)


def extract_verified_archive(path: Path, destination: Path) -> Path | BlockedResource:
    """Verify one frozen archive snapshot and fd-safely materialize its files.

    The extracted tree includes the canonical manifest so downstream bootstrap
    code can bind itself to the exact verified package content.
    """
    archive_payload = _snapshot_archive(Path(path))
    _, payloads = _verify_archive_payload(archive_payload)
    target, created = _prepare_destination(Path(destination))
    available = shutil.disk_usage(target).free
    required = sum(len(payload) for payload in payloads.values())
    if available < required:
        if created:
            os.rmdir(target)
        return BlockedResource("host capacity is insufficient for verified archive extraction")
    try:
        _extract_payloads(target, payloads)
    except BaseException:
        try:
            _clean_partial_destination(target, remove_root=created)
        except OSError:
            pass
        raise
    return target


__all__ = [
    "BlockedResource", "BundleContractError", "BundleSecurityError", "BundleSpec",
    "DependencyContractIds", "StructuralVerificationReport", "build_specialist_archive",
    "derive_entrypoint_contract_id", "extract_verified_archive", "load_bundle_spec",
    "verify_specialist_archive", "write_bundle_spec",
]
