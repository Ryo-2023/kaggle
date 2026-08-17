"""Identity sealing and package binding for self-owned CG deck candidates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Mapping

from .self_owned_cg_deck_v1 import (
    SelfOwnedDeckCandidateV1,
    canonical_deck_sha256_v1,
    deck_file_bytes_v1,
)


PACKAGE_SCHEMA_VERSION_V1 = "self-owned-cg-package-v1"
_AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}
_ARTIFACT_KEYS = {
    "schema_version",
    "candidate_id",
    "archetype_id",
    "card_ids",
    "deck_file_sha256",
    "canonical_deck_sha256",
    "parent_deck",
    "public_parent_read",
    "seed",
    "candidate_ordinal",
    "generator_source_sha256",
    "role_spec_sha256",
    "card_database_sha256",
    "research_only",
    "authority",
    "deck_file",
    "manifest_sha256",
}
_PACKAGE_KEYS = {
    "schema_version",
    "candidate_id",
    "archetype_id",
    "parent_deck",
    "public_parent_read",
    "parent_policy_sha256",
    "policy_sha256",
    "deck_file_sha256",
    "canonical_deck_sha256",
    "root_deck_replaced",
    "runtime_files",
    "research_only",
    "authority",
    "manifest_sha256",
}


class SelfOwnedCgPackageV1Error(ValueError):
    """Raised when a self-owned artifact or package fails closed."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SelfOwnedCgPackageV1Error(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


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
        raise SelfOwnedCgPackageV1Error("manifest is not canonical JSON") from exc


def _semantic_sha(payload: Mapping[str, object]) -> str:
    return _sha256_bytes(_canonical_json(payload))


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise
    except OSError as exc:
        raise SelfOwnedCgPackageV1Error(f"cannot write artifact: {path}") from exc


def _prepare_empty_root(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise FileExistsError(f"output root is not empty: {path}")
    else:
        path.mkdir(parents=True, exist_ok=False)


def _parse_deck_bytes(payload: bytes) -> tuple[int, ...]:
    try:
        values = tuple(int(token) for token in payload.decode("utf-8").split())
    except (UnicodeDecodeError, ValueError) as exc:
        raise SelfOwnedCgPackageV1Error("deck bytes are not integer UTF-8 tokens") from exc
    if len(values) != 60 or any(value <= 0 for value in values):
        raise SelfOwnedCgPackageV1Error("candidate deck must contain 60 positive IDs")
    return values


def write_self_owned_deck_artifact_v1(
    candidate: SelfOwnedDeckCandidateV1,
    output_root: str | Path,
    *,
    card_database_sha256: str,
    role_spec_sha256: str,
    generator_source_sha256: str,
) -> dict[str, object]:
    """Write a no-clobber deck and its hash-bound provenance manifest."""
    if type(candidate) is not SelfOwnedDeckCandidateV1:
        raise SelfOwnedCgPackageV1Error("candidate has the wrong type")
    if candidate.parent_deck is not None or candidate.research_only is not True:
        raise SelfOwnedCgPackageV1Error("candidate is not a research-only scratch deck")
    if dict(candidate.authority) != _AUTHORITY_FALSE:
        raise SelfOwnedCgPackageV1Error("candidate grants forbidden authority")
    root = Path(output_root).resolve()
    _prepare_empty_root(root)
    deck_bytes = deck_file_bytes_v1(candidate.card_ids)
    if _sha256_bytes(deck_bytes) != candidate.deck_file_sha256:
        raise SelfOwnedCgPackageV1Error("candidate deck file SHA mismatch")
    if canonical_deck_sha256_v1(candidate.card_ids) != candidate.canonical_deck_sha256:
        raise SelfOwnedCgPackageV1Error("candidate canonical deck SHA mismatch")
    _write_exclusive(root / "deck.csv", deck_bytes)
    payload: dict[str, object] = {
        "schema_version": candidate.schema_version,
        "candidate_id": candidate.candidate_id,
        "archetype_id": candidate.archetype_id,
        "card_ids": list(candidate.card_ids),
        "deck_file_sha256": candidate.deck_file_sha256,
        "canonical_deck_sha256": candidate.canonical_deck_sha256,
        "parent_deck": None,
        "public_parent_read": False,
        "seed": candidate.seed,
        "candidate_ordinal": candidate.candidate_ordinal,
        "generator_source_sha256": generator_source_sha256,
        "role_spec_sha256": role_spec_sha256,
        "card_database_sha256": card_database_sha256,
        "research_only": True,
        "authority": dict(_AUTHORITY_FALSE),
        "deck_file": "deck.csv",
    }
    payload["manifest_sha256"] = _semantic_sha(payload)
    _write_exclusive(root / "manifest.json", _canonical_json(payload))
    return payload


def _patch_root_deck_constant(source_text: str, card_ids: tuple[int, ...]) -> str:
    marker = "ROOT_DECK = ("
    start = source_text.find(marker)
    if start < 0:
        raise SelfOwnedCgPackageV1Error("P1 source has no ROOT_DECK assignment")
    end = source_text.find("\n\nFIGHTING =", start)
    if end < 0:
        raise SelfOwnedCgPackageV1Error("P1 ROOT_DECK assignment boundary is not recognized")
    if source_text.find(marker, start + len(marker)) >= 0:
        raise SelfOwnedCgPackageV1Error("P1 source has multiple ROOT_DECK assignments")
    replacement = "ROOT_DECK = " + repr(tuple(card_ids))
    return source_text[:start] + replacement + source_text[end:]


def _regular_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise SelfOwnedCgPackageV1Error(f"regular directory required: {path}")
    for child in path.rglob("*"):
        if child.is_symlink():
            raise SelfOwnedCgPackageV1Error(f"package tree contains non-regular entry: {child}")
        if not child.is_file() and not child.is_dir():
            raise SelfOwnedCgPackageV1Error(f"package tree contains non-regular entry: {child}")


def _runtime_file_hashes(root: Path) -> dict[str, str]:
    """Hash shipped runtime files while excluding interpreter caches."""
    return {
        str(path.relative_to(root)): _sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
def materialize_self_owned_cg_package_v1(
    *,
    source_package: str | Path,
    candidate_deck: str | Path,
    output_package: str | Path,
    candidate_id: str,
) -> dict[str, object]:
    """Copy P1 policy/runtime code and bind a generated deck without copying its parent deck."""
    source = Path(source_package).resolve()
    deck_path = Path(candidate_deck).resolve()
    target = Path(output_package).resolve()
    source_main = source / "main.py"
    source_cg = source / "cg"
    if not isinstance(candidate_id, str) or not candidate_id:
        raise SelfOwnedCgPackageV1Error("candidate_id must be non-empty")
    if source_main.is_symlink() or not source_main.is_file():
        raise SelfOwnedCgPackageV1Error("source package main.py is not a regular file")
    _regular_tree(source_cg)
    if deck_path.is_symlink() or not deck_path.is_file():
        raise SelfOwnedCgPackageV1Error("candidate deck is not a regular file")
    deck_bytes = deck_path.read_bytes()
    card_ids = _parse_deck_bytes(deck_bytes)
    source_text = source_main.read_text(encoding="utf-8")
    patched_text = _patch_root_deck_constant(source_text, card_ids)
    if patched_text == source_text:
        raise SelfOwnedCgPackageV1Error("P1 source was not changed by deck binding")
    _prepare_empty_root(target)
    (target / "cg").mkdir()
    shutil.copytree(
        source_cg,
        target / "cg",
        dirs_exist_ok=True,
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _write_exclusive(target / "main.py", patched_text.encode("utf-8"))
    _write_exclusive(target / "deck.csv", deck_bytes)
    runtime_files = {
        f"cg/{relative_path}": digest
        for relative_path, digest in _runtime_file_hashes(target / "cg").items()
    }
    payload: dict[str, object] = {
        "schema_version": PACKAGE_SCHEMA_VERSION_V1,
        "candidate_id": candidate_id,
        "archetype_id": "self-owned-cg",
        "parent_deck": None,
        "public_parent_read": False,
        "parent_policy_sha256": _sha256_file(source_main),
        "policy_sha256": _sha256_file(target / "main.py"),
        "deck_file_sha256": _sha256_file(target / "deck.csv"),
        "canonical_deck_sha256": canonical_deck_sha256_v1(card_ids),
        "root_deck_replaced": True,
        "runtime_files": runtime_files,
        "research_only": True,
        "authority": dict(_AUTHORITY_FALSE),
    }
    payload["manifest_sha256"] = _semantic_sha(payload)
    _write_exclusive(target / "self_owned_cg_package_manifest.json", _canonical_json(payload))
    return payload


def _load_manifest(path: Path, keys: set[str]) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedCgPackageV1Error(f"manifest is unreadable: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != keys:
        raise SelfOwnedCgPackageV1Error(f"manifest has unexpected fields: {path}")
    if raw != _canonical_json(payload):
        raise SelfOwnedCgPackageV1Error(f"manifest is not canonical JSON: {path}")
    supplied = payload.get("manifest_sha256")
    body = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    if supplied != _semantic_sha(body):
        raise SelfOwnedCgPackageV1Error(f"manifest semantic SHA mismatch: {path}")
    return payload


def verify_self_owned_cg_package_v1(package_root: str | Path) -> dict[str, object]:
    """Verify package shape and all hash bindings without importing policy code."""
    root = Path(package_root).resolve()
    manifest = _load_manifest(root / "self_owned_cg_package_manifest.json", _PACKAGE_KEYS)
    if (
        manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION_V1
        or manifest.get("parent_deck") is not None
        or manifest.get("public_parent_read") is not False
        or manifest.get("root_deck_replaced") is not True
        or manifest.get("research_only") is not True
        or manifest.get("authority") != _AUTHORITY_FALSE
    ):
        raise SelfOwnedCgPackageV1Error("package provenance or authority is invalid")
    main = root / "main.py"
    deck = root / "deck.csv"
    cg = root / "cg"
    _regular_tree(cg)
    if main.is_symlink() or not main.is_file() or deck.is_symlink() or not deck.is_file():
        raise SelfOwnedCgPackageV1Error("package must contain regular main.py and deck.csv")
    card_ids = _parse_deck_bytes(deck.read_bytes())
    if _sha256_file(deck) != manifest.get("deck_file_sha256"):
        raise SelfOwnedCgPackageV1Error("package deck SHA mismatch")
    if canonical_deck_sha256_v1(card_ids) != manifest.get("canonical_deck_sha256"):
        raise SelfOwnedCgPackageV1Error("package canonical deck SHA mismatch")
    if _sha256_file(main) != manifest.get("policy_sha256"):
        raise SelfOwnedCgPackageV1Error("package policy SHA mismatch")
    if manifest.get("policy_sha256") == manifest.get("parent_policy_sha256"):
        raise SelfOwnedCgPackageV1Error("package policy was not deck-bound")
    text = main.read_text(encoding="utf-8")
    if "ROOT_DECK = (" not in text:
        raise SelfOwnedCgPackageV1Error("deck-bound policy has no ROOT_DECK tuple")
    runtime_files = manifest.get("runtime_files")
    if not isinstance(runtime_files, Mapping):
        raise SelfOwnedCgPackageV1Error("runtime_files must be a mapping")
    actual_runtime = {
        f"cg/{relative_path}": digest
        for relative_path, digest in _runtime_file_hashes(cg).items()
    }
    if dict(runtime_files) != actual_runtime:
        raise SelfOwnedCgPackageV1Error("runtime file identity mismatch")
    if (root / "submission.tar.gz").exists():
        raise SelfOwnedCgPackageV1Error("submission archive must not be copied into research package")
    return manifest


__all__ = [
    "PACKAGE_SCHEMA_VERSION_V1",
    "SelfOwnedCgPackageV1Error",
    "materialize_self_owned_cg_package_v1",
    "verify_self_owned_cg_package_v1",
    "write_self_owned_deck_artifact_v1",
]
