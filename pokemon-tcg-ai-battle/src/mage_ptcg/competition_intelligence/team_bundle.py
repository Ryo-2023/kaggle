"""Secure Team Bundle import (O1-5 SS5).

A Team Bundle is a directory containing a ``manifest.yaml``/``manifest.json``
(the format documented in the O1-5 design) plus the files it references, all
relative to the bundle root. Import is fail-closed end to end: default deny
on a missing permission statement, mandatory per-file hash verification,
hard rejection of absolute paths / ``..`` traversal / symlink escape / device
files, and bundle size/file-count limits -- any violation quarantines the
manifest rather than partially importing it. A bundle without an explicit,
non-empty ``permission_statement`` is still archived (so its existence and
provenance are recorded) but is forced down to ``{ARCHIVE}`` regardless of
what its own ``allowed_uses`` list claims, matching the O1-5 requirement that
such a bundle "may be archived but must not be analyzed, reported, or used
for training".
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from . import archive
from .canonical import sha256_hex
from .contracts import AllowedUse, ContractError, SourceKind
from .permissions import validate_allowed_uses_subset
from .provenance import SourceTimeError, build_source_envelope, require_declared_time, write_source_manifest
from .runstate import RunPaths, load_or_create, run_lock

TEAM_BUNDLE_SCHEMA_VERSION = "team-bundle-v1"
PARSER_VERSION = "competition-intelligence-team-bundle-v1"
REDACTION_VERSION = "competition-intelligence-redaction-v1"

MAX_BUNDLE_FILES = 200
MAX_BUNDLE_TOTAL_BYTES = 50 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 20 * 1024 * 1024

STATUS_ARCHIVED = "ARCHIVED"
STATUS_ALREADY_IMPORTED = "ALREADY_IMPORTED"
STATUS_QUARANTINED = "QUARANTINED"


class TeamBundleError(ValueError):
    """Raised for a caller-facing rejection: escalation attempt, missing bundle root, etc."""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bundle_reference(source: Path) -> str:
    """Portable identifier for diagnostics; never serialize the local path."""
    return "bundle-" + sha256_hex(str(source.resolve()).encode("utf-8"))[:16]


def _safe_detail(value: str) -> str:
    from mage_ptcg.competition.redaction import redact_value

    return str(redact_value(value))[:500]


def _load_manifest_text(bundle_root: Path) -> tuple[str, Path]:
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        candidate = bundle_root / name
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.read_text(encoding="utf-8"), candidate
        raise TeamBundleError("no manifest.yaml/.yml/.json found at bundle root")


def _parse_manifest(text: str, manifest_path: Path) -> Any:
    if manifest_path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise TeamBundleError("PyYAML is required to parse a .yaml/.yml team bundle manifest") from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise TeamBundleError(f"invalid YAML: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TeamBundleError(f"invalid JSON: {exc}") from exc


@dataclass(frozen=True, slots=True)
class TeamBundleOutcome:
    status: str
    source_id: str | None
    manifest_path: str | None
    file_count: int
    total_bytes: int
    allowed_uses: tuple[str, ...]
    permission_statement_present: bool
    quarantine_sha256: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_id": self.source_id,
            "manifest_path": self.manifest_path,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "allowed_uses": list(self.allowed_uses),
            "permission_statement_present": self.permission_statement_present,
            "quarantine_sha256": self.quarantine_sha256,
            "detail": self.detail,
        }


def _reject_path_escape(bundle_root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute():
        raise TeamBundleError(f"file path must be relative, got absolute path: {relative!r}")
    if any(part == ".." for part in pure.parts):
        raise TeamBundleError(f"file path must not contain '..': {relative!r}")
    if not pure.parts:
        raise TeamBundleError("file path must not be empty")
    # Walk every path component from the root down, rejecting a symlink at
    # any level (not just the leaf) so a symlinked intermediate directory
    # cannot be used to escape the bundle root either.
    current = bundle_root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise TeamBundleError(f"file path contains a symlink component, rejected: {relative!r}")
    resolved = current.resolve()
    root_resolved = bundle_root.resolve()
    if root_resolved != resolved and root_resolved not in resolved.parents:
        raise TeamBundleError(f"file path escapes bundle root: {relative!r}")
    return current


def _validate_file_entry(bundle_root: Path, entry: Mapping[str, Any]) -> tuple[str, Path, str, int]:
    path = entry.get("path")
    if not isinstance(path, str) or not path:
        raise TeamBundleError("every file entry must have a non-empty string 'path'")
    declared_sha256 = entry.get("sha256")
    if not isinstance(declared_sha256, str) or len(declared_sha256) != 64:
        raise TeamBundleError(f"file {path!r} must declare a 64-hex-char sha256")
    full_path = _reject_path_escape(bundle_root, path)
    if not full_path.exists():
        raise TeamBundleError(f"file {path!r} referenced by manifest does not exist")
    file_stat = full_path.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise TeamBundleError(f"file {path!r} is not a regular file (device/fifo/socket files are rejected)")
    size = file_stat.st_size
    if size > MAX_SINGLE_FILE_BYTES:
        raise TeamBundleError(f"file {path!r} exceeds the single-file size limit ({size} > {MAX_SINGLE_FILE_BYTES} bytes)")
    data = full_path.read_bytes()
    actual_sha256 = sha256_hex(data)
    if actual_sha256 != declared_sha256.lower():
        raise TeamBundleError(f"file {path!r} sha256 mismatch: manifest declares {declared_sha256}, actual is {actual_sha256}")
    return path, full_path, actual_sha256, size


def import_team_bundle(
    bundle_root: str | Path,
    run_root: str | Path,
    *,
    cli_requested_uses: Iterable[str] | None = None,
    config_hash: str = "unset",
    created_at: str | None = None,
) -> TeamBundleOutcome:
    """Validate and archive a Team Bundle. Fail-closed: any violation quarantines
    the manifest and returns a ``QUARANTINED`` outcome rather than raising, so a
    batch importer can process many bundles without one bad bundle aborting the
    run; a caller-error (escalation attempt, missing bundle root) still raises
    ``TeamBundleError`` since that is a programming/usage mistake, not a normal
    "this bundle is bad" outcome.
    """
    root = Path(run_root)
    source = Path(bundle_root)
    if not source.is_dir():
        raise TeamBundleError("bundle_root must be an existing directory")

    try:
        manifest_text, manifest_path_on_disk = _load_manifest_text(source)
        manifest_bytes = manifest_text.encode("utf-8")
        manifest = _parse_manifest(manifest_text, manifest_path_on_disk)
        if not isinstance(manifest, Mapping):
            raise TeamBundleError("team bundle manifest root must be a mapping")
        if manifest.get("bundle_version") != TEAM_BUNDLE_SCHEMA_VERSION:
            raise TeamBundleError(f"unsupported bundle_version {manifest.get('bundle_version')!r}")
        if manifest.get("source_kind") != SourceKind.TEAM_SHARED.value:
            raise TeamBundleError(f"team bundle source_kind must be {SourceKind.TEAM_SHARED.value!r}")
        owner = manifest.get("owner")
        if not isinstance(owner, str) or not owner:
            raise TeamBundleError("team bundle manifest must declare a non-empty 'owner'")
        files = manifest.get("files")
        if not isinstance(files, list):
            raise TeamBundleError("team bundle manifest must have a 'files' list")
        if len(files) > MAX_BUNDLE_FILES:
            raise TeamBundleError(f"bundle has {len(files)} files, exceeds limit of {MAX_BUNDLE_FILES}")
        if not all(isinstance(entry, Mapping) for entry in files):
            raise TeamBundleError("every entry in 'files' must be a mapping")

        seen_paths: set[str] = set()
        validated: list[tuple[str, Path, str, int]] = []
        total_bytes = 0
        for entry in files:
            path, full_path, file_sha256, size = _validate_file_entry(source, entry)
            if path in seen_paths:
                raise TeamBundleError(f"duplicate file path in manifest: {path!r}")
            seen_paths.add(path)
            validated.append((path, full_path, file_sha256, size))
            total_bytes += size
        if total_bytes > MAX_BUNDLE_TOTAL_BYTES:
            raise TeamBundleError(f"bundle total size {total_bytes} exceeds limit of {MAX_BUNDLE_TOTAL_BYTES} bytes")

        permission_statement = manifest.get("permission_statement")
        permission_present = isinstance(permission_statement, str) and bool(permission_statement.strip())
        declared_uses_raw = manifest.get("allowed_uses") or []
        if not isinstance(declared_uses_raw, list):
            raise TeamBundleError("'allowed_uses' must be a list")
        declared_uses = validate_allowed_uses_subset(declared_uses_raw)
        if permission_present:
            effective_uses = declared_uses
        else:
            # Default deny: no permission statement means archive-only,
            # regardless of what the manifest's own allowed_uses list claims.
            effective_uses = frozenset({AllowedUse.ARCHIVE})

        if cli_requested_uses is not None:
            requested = validate_allowed_uses_subset(cli_requested_uses)
            escalation = requested - effective_uses
            if escalation:
                raise TeamBundleError(
                    f"permission escalation rejected: caller requested {sorted(u.value for u in escalation)} "
                    f"beyond what the bundle grants ({sorted(u.value for u in effective_uses)})"
                )
            effective_uses = requested

    except (TeamBundleError, ContractError) as exc:
        try:
            raw = manifest_bytes  # type: ignore[possibly-undefined]
        except NameError:
            raw = b"<unreadable bundle manifest>"
        quarantine_hash = archive.quarantine_bytes(
            root, raw, reason="malformed_team_bundle",
            detail={"error": _safe_detail(str(exc)), "bundle_reference": _bundle_reference(source)},
        )
        return TeamBundleOutcome(
            status=STATUS_QUARANTINED,
            source_id=None,
            manifest_path=None,
            file_count=0,
            total_bytes=0,
            allowed_uses=(),
            permission_statement_present=False,
            quarantine_sha256=quarantine_hash,
            detail=_safe_detail(str(exc)),
        )

    manifest_content_hash = sha256_hex(manifest_bytes)
    resolved_source_id = f"team-bundle:{manifest_content_hash[:24]}"

    paths = RunPaths(root)
    root.mkdir(parents=True, exist_ok=True)
    with run_lock(paths, root.name):
        state = load_or_create(
            root, run_id=root.name, git_commit="unknown", config_hash=config_hash, resume=paths.manifest.exists()
        )
        from .provenance import source_manifest_path
        existing_path = source_manifest_path(root, resolved_source_id)
        if archive.raw_path(root, manifest_content_hash).exists() and existing_path.is_file():
            return TeamBundleOutcome(
                status=STATUS_ALREADY_IMPORTED, source_id=resolved_source_id,
                manifest_path=existing_path.relative_to(root).as_posix(), file_count=len(validated), total_bytes=total_bytes,
                allowed_uses=tuple(sorted(use.value for use in effective_uses)),
                permission_statement_present=permission_present, quarantine_sha256=None,
                detail="idempotent re-import: an identical bundle is already archived",
            )

        archive.store_raw(root, manifest_bytes)
        for _path, full_path, _sha256, _size in validated:
            archive.store_raw(root, full_path.read_bytes())

        try:
            declared_created_at = require_declared_time(created_at, field_name="created_at", context="team bundle import")
        except SourceTimeError as exc:
            raise TeamBundleError(str(exc)) from exc
        envelope = build_source_envelope(
            source_id=resolved_source_id,
            source_kind=SourceKind.TEAM_SHARED,
            acquisition_mode="LOCAL_ONLY",
            acquired_at=declared_created_at,
            origin_reference=f"team-bundle:{manifest_content_hash[:16]}",
            owner_scope=owner,
            visibility="team_private",
            allowed_uses=[use.value for use in effective_uses],
            raw_sha256=manifest_content_hash,
            parser_version=PARSER_VERSION,
            redaction_version=REDACTION_VERSION,
            metadata={
                "file_count": len(validated),
                "total_bytes": total_bytes,
                "permission_statement_present": permission_present,
                "file_paths": sorted(path for path, *_ in validated),
            },
        )
        manifest_write_path = write_source_manifest(root, envelope)
        state.record_ingested_source(envelope.source_id)

    return TeamBundleOutcome(
        status=STATUS_ARCHIVED,
        source_id=resolved_source_id,
        manifest_path=manifest_write_path.relative_to(root).as_posix(),
        file_count=len(validated),
        total_bytes=total_bytes,
        allowed_uses=tuple(sorted(use.value for use in effective_uses)),
        permission_statement_present=permission_present,
        quarantine_sha256=None,
        detail="ok",
    )


__all__ = [
    "MAX_BUNDLE_FILES",
    "MAX_BUNDLE_TOTAL_BYTES",
    "MAX_SINGLE_FILE_BYTES",
    "STATUS_ALREADY_IMPORTED",
    "STATUS_ARCHIVED",
    "STATUS_QUARANTINED",
    "TEAM_BUNDLE_SCHEMA_VERSION",
    "TeamBundleError",
    "TeamBundleOutcome",
    "import_team_bundle",
]
