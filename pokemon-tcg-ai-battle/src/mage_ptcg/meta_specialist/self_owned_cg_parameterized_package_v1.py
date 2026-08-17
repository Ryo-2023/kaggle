"""Bind the sealed P1 policy surface to a research-only scratch deck.

This module is deliberately separate from the existing P1 materializer.  The
existing path is root-deck bound; this path accepts only a verified
``self-owned-cg`` deck package and emits a package whose policy and deck are
both content-addressed.  It never writes the repository root deck or grants
training, promotion, or submission authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping

from .cg_p1_parameterization_v1 import (
    BASE_SOURCE_SHA256,
    P1ParameterConfig,
    render_parameterized_source,
)
from .self_owned_cg_deck_v1 import canonical_deck_sha256_v1
from .self_owned_cg_package_v1 import (
    PACKAGE_SCHEMA_VERSION_V1,
    SelfOwnedCgPackageV1Error,
    _patch_root_deck_constant,
    _runtime_file_hashes,
    _sha256_file,
    _write_exclusive,
    _canonical_json,
    _prepare_empty_root,
    _semantic_sha,
    _parse_deck_bytes,
    _regular_tree,
    verify_self_owned_cg_package_v1,
)


_AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}


def _copy_runtime(source: Path, target: Path) -> None:
    """Copy only regular runtime files, omitting interpreter caches."""
    _regular_tree(source)
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def materialize_self_owned_cg_parameterized_package_v1(
    *,
    source_package: str | Path,
    self_owned_deck_package: str | Path,
    output_package: str | Path,
    config: P1ParameterConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Create one hash-bound self-owned package on the immutable P1 surface.

    ``source_package`` must be the exact sealed P1 package used by the
    parameter renderer.  ``self_owned_deck_package`` must already pass the
    self-owned package verifier; its deck bytes are copied verbatim and the
    generated policy's ``ROOT_DECK`` tuple is rebound to the same multiset.
    """

    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise SelfOwnedCgPackageV1Error("candidate_id must be non-empty")
    if not isinstance(config, P1ParameterConfig):
        raise SelfOwnedCgPackageV1Error("config must be P1ParameterConfig")
    config.validate()

    source = Path(source_package).resolve()
    source_main = source / "main.py"
    source_cg = source / "cg"
    if source_main.is_symlink() or not source_main.is_file():
        raise SelfOwnedCgPackageV1Error("P1 source package main.py is not regular")
    if _sha256_file(source_main) != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("P1 source policy SHA mismatch")
    _regular_tree(source_cg)

    deck_package = Path(self_owned_deck_package).resolve()
    try:
        deck_manifest = verify_self_owned_cg_package_v1(deck_package)
    except SelfOwnedCgPackageV1Error as exc:
        raise SelfOwnedCgPackageV1Error(
            f"self-owned deck package verification failed: {deck_package}"
        ) from exc
    if deck_manifest.get("parent_policy_sha256") != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("self-owned deck package is not P1-bound")
    deck_path = deck_package / "deck.csv"
    deck_bytes = deck_path.read_bytes()
    card_ids = _parse_deck_bytes(deck_bytes)
    if _sha256_file(deck_path) != deck_manifest.get("deck_file_sha256"):
        raise SelfOwnedCgPackageV1Error("self-owned deck manifest does not bind deck bytes")
    canonical_deck_sha = canonical_deck_sha256_v1(card_ids)
    if canonical_deck_sha != deck_manifest.get("canonical_deck_sha256"):
        raise SelfOwnedCgPackageV1Error("self-owned deck manifest does not bind canonical deck")

    rendered = render_parameterized_source(
        config,
        candidate_id=candidate_id,
        source_path=source_main,
    )
    patched = _patch_root_deck_constant(rendered, card_ids)
    if patched == rendered:
        raise SelfOwnedCgPackageV1Error("parameterized policy was not rebound to scratch deck")

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

    # Keep a separate, machine-readable record of the policy point.  The
    # package verifier intentionally ignores this sidecar so its contract
    # remains identical to existing self-owned packages.
    parameter_manifest = {
        "schema_version": "self-owned-cg-parameterized-v1",
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": payload["policy_sha256"],
        "canonical_deck_sha256": canonical_deck_sha,
        "research_only": True,
    }
    _write_exclusive(
        target / "self_owned_cg_parameterization_manifest.json",
        (_canonical_json(parameter_manifest) + b"\n"),
    )
    return verify_self_owned_cg_package_v1(target)


__all__ = ["materialize_self_owned_cg_parameterized_package_v1"]
