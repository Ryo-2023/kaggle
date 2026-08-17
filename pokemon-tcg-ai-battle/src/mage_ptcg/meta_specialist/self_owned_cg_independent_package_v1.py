"""Materialize and verify self-owned packages on the independent root lineage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping

from .cg_independent_policy_renderer_v1 import (
    BASE_SOURCE_SHA256,
    IndependentCgParameterConfig,
    render_independent_source,
)
from .self_owned_cg_deck_v1 import canonical_deck_sha256_v1
from .self_owned_cg_package_v1 import (
    PACKAGE_SCHEMA_VERSION_V1,
    SelfOwnedCgPackageV1Error,
    _canonical_json,
    _parse_deck_bytes,
    _patch_root_deck_constant,
    _prepare_empty_root,
    _runtime_file_hashes,
    _semantic_sha,
    _sha256_file,
    _write_exclusive,
    _regular_tree,
    verify_self_owned_cg_package_v1,
)


SCHEMA = "self-owned-cg-independent-parameterization-v1"
_AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}
_SIDECAR_KEYS = {
    "schema_version",
    "candidate_id",
    "config",
    "config_sha256",
    "parent_policy_sha256",
    "policy_sha256",
    "canonical_deck_sha256",
    "lineage",
    "research_only",
    "manifest_sha256",
}


def _copy_runtime(source: Path, target: Path) -> None:
    _regular_tree(source)
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _load_sidecar(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedCgPackageV1Error(f"independent sidecar is unreadable: {path}") from exc
    if not isinstance(value, dict) or set(value) != _SIDECAR_KEYS:
        raise SelfOwnedCgPackageV1Error("independent sidecar has unexpected fields")
    if raw != _canonical_json(value) + b"\n":
        raise SelfOwnedCgPackageV1Error("independent sidecar is not canonical JSON")
    supplied = value.get("manifest_sha256")
    body = {key: item for key, item in value.items() if key != "manifest_sha256"}
    if supplied != _semantic_sha(body):
        raise SelfOwnedCgPackageV1Error("independent sidecar semantic SHA mismatch")
    return value


def materialize_self_owned_cg_independent_package_v1(
    *,
    source_package: str | Path,
    self_owned_deck_package: str | Path,
    output_package: str | Path,
    config: IndependentCgParameterConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Create one hash-bound package without importing P1 policy code."""

    if not isinstance(config, IndependentCgParameterConfig):
        raise SelfOwnedCgPackageV1Error("config must be IndependentCgParameterConfig")
    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise SelfOwnedCgPackageV1Error("candidate_id must be non-empty")

    source = Path(source_package).resolve()
    source_main = source / "main.py"
    source_cg = source / "cg"
    if source_main.is_symlink() or not source_main.is_file():
        raise SelfOwnedCgPackageV1Error("root source package main.py is not regular")
    if _sha256_file(source_main) != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("root source policy SHA mismatch")
    _regular_tree(source_cg)

    deck_package = Path(self_owned_deck_package).resolve()
    try:
        deck_manifest = verify_self_owned_cg_package_v1(deck_package)
    except SelfOwnedCgPackageV1Error as exc:
        raise SelfOwnedCgPackageV1Error(
            f"self-owned deck package verification failed: {deck_package}"
        ) from exc
    deck_path = deck_package / "deck.csv"
    deck_bytes = deck_path.read_bytes()
    card_ids = _parse_deck_bytes(deck_bytes)
    canonical_deck_sha = canonical_deck_sha256_v1(card_ids)
    if canonical_deck_sha != deck_manifest.get("canonical_deck_sha256"):
        raise SelfOwnedCgPackageV1Error("deck package manifest does not bind canonical deck")

    rendered = render_independent_source(
        config,
        candidate_id=candidate_id,
        source_path=source_main,
    )
    patched = _patch_root_deck_constant(rendered, card_ids)
    if patched == rendered:
        raise SelfOwnedCgPackageV1Error("independent policy was not rebound to scratch deck")

    target = Path(output_package).resolve()
    _prepare_empty_root(target)
    _copy_runtime(source_cg, target / "cg")
    _write_exclusive(target / "main.py", patched.encode("utf-8"))
    _write_exclusive(target / "deck.csv", deck_bytes)

    runtime_files = {
        f"cg/{relative_path}": digest
        for relative_path, digest in _runtime_file_hashes(target / "cg").items()
    }
    payload: dict[str, object] = {
        "schema_version": PACKAGE_SCHEMA_VERSION_V1,
        "candidate_id": candidate_id,
        "archetype_id": deck_manifest.get("archetype_id", "self-owned-cg"),
        "parent_deck": None,
        "public_parent_read": False,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_file(target / "main.py"),
        "deck_file_sha256": _sha256_file(target / "deck.csv"),
        "canonical_deck_sha256": canonical_deck_sha,
        "root_deck_replaced": True,
        "runtime_files": runtime_files,
        "research_only": True,
        "authority": dict(_AUTHORITY_FALSE),
    }
    payload["manifest_sha256"] = _semantic_sha(payload)
    _write_exclusive(target / "self_owned_cg_package_manifest.json", _canonical_json(payload))

    sidecar: dict[str, object] = {
        "schema_version": SCHEMA,
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": payload["policy_sha256"],
        "canonical_deck_sha256": canonical_deck_sha,
        "lineage": "independent-root-public-state-v1",
        "research_only": True,
    }
    sidecar["manifest_sha256"] = _semantic_sha(sidecar)
    _write_exclusive(
        target / "self_owned_cg_independent_parameterization_manifest.json",
        _canonical_json(sidecar) + b"\n",
    )
    return verify_self_owned_cg_independent_package_v1(target)


def verify_self_owned_cg_independent_package_v1(package_root: str | Path) -> dict[str, object]:
    """Verify both the standard package identity and independent lineage sidecar."""

    root = Path(package_root).resolve()
    manifest = verify_self_owned_cg_package_v1(root)
    if manifest.get("parent_policy_sha256") != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("package is not bound to the independent root source")
    source = root / "main.py"
    text = source.read_text(encoding="utf-8")
    if "RESEARCH_INDEPENDENT_LINEAGE: root-cg-public-state-v1" not in text:
        raise SelfOwnedCgPackageV1Error("independent lineage marker is missing")
    sidecar = _load_sidecar(root / "self_owned_cg_independent_parameterization_manifest.json")
    if (
        sidecar.get("parent_policy_sha256") != BASE_SOURCE_SHA256
        or sidecar.get("policy_sha256") != manifest.get("policy_sha256")
        or sidecar.get("canonical_deck_sha256") != manifest.get("canonical_deck_sha256")
        or sidecar.get("research_only") is not True
        or sidecar.get("lineage") != "independent-root-public-state-v1"
    ):
        raise SelfOwnedCgPackageV1Error("independent lineage sidecar is not bound")
    return manifest


__all__ = [
    "SCHEMA",
    "materialize_self_owned_cg_independent_package_v1",
    "verify_self_owned_cg_independent_package_v1",
]
