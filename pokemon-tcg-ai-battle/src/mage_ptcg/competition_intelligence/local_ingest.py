"""Ingest a single local file into the Competition Intelligence raw archive.

This is the O1-1-scope "local self-play source" ingestion path: it archives
raw bytes by content hash, secret-scans and quarantines unsafe content
instead of archiving it, and writes a validated ``SourceEnvelope`` manifest.
It does not parse/normalize game content (that is O1-2, not implemented yet)
— the output is provenance + an immutable raw blob, nothing more.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from mage_ptcg.competition.redaction import redact_value

from . import archive
from .canonical import sha256_hex
from .contracts import ContractError, SourceKind
from .permissions import DEFAULT_ALLOWED_USES
from .provenance import SourceTimeError, build_source_envelope, require_declared_time, write_source_manifest
from .runstate import RunPaths, load_or_create, run_lock

REDACTION_VERSION = "competition-intelligence-redaction-v1"
PARSER_VERSION = "competition-intelligence-local-ingest-v1"


class IngestError(RuntimeError):
    """Raised when local ingestion cannot safely complete."""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def current_git_commit() -> str:
    """Best-effort current commit hash; ``"unknown"`` if git is unavailable.

    This is provenance metadata only (which run manifest this ingestion
    belongs to), never used for correctness decisions, so a failure here
    degrades gracefully instead of aborting ingestion.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


def ingest_local_file(
    run_dir: str | Path,
    input_path: str | Path,
    *,
    source_id: str | None = None,
    source_kind: SourceKind | str = SourceKind.LOCAL_SELFPLAY,
    allowed_uses: Iterable[str] | None = None,
    owner_scope: str = "self",
    visibility: str = "private",
    config_hash: str = "unset",
    acquired_at: str | None = None,
    origin_reference: str | None = None,
) -> dict[str, Any]:
    """Archive one local file as a Competition Intelligence raw source.

    Returns a JSON-serializable summary; never raises on a secret-scan hit
    (that is a normal, expected outcome recorded as ``quarantined``) but does
    raise ``IngestError``/``ContractError`` for a missing input file or an
    invalid envelope.
    """
    source = input_path if isinstance(input_path, Path) else Path(input_path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise IngestError(f"cannot read input file {source}: {exc}") from exc

    kind = source_kind if isinstance(source_kind, SourceKind) else SourceKind(source_kind)
    resolved_source_id = source_id or f"local:{sha256_hex(str(source.resolve()).encode('utf-8'))[:16]}:{sha256_hex(data)[:16]}"

    run_root = Path(run_dir)
    paths = RunPaths(run_root)
    is_safe, labels = archive.scan_before_archive(data)
    if not is_safe:
        quarantine_hash = archive.quarantine_bytes(
            run_root, data, reason="secret_scan_hit", detail={"labels": list(labels)}
        )
        return {
            "status": "QUARANTINED",
            "source_id": resolved_source_id,
            "quarantine_sha256": quarantine_hash,
            "labels": list(labels),
        }

    raw_sha256 = archive.store_raw(run_root, data)
    # allowed_uses=None falls back to this source kind's default grant (still
    # default-deny for TEAM_SHARED/PUBLIC_OTHER); an explicit empty iterable
    # is respected as "no permissions", never silently upgraded.
    uses = list(allowed_uses) if allowed_uses is not None else [use.value for use in DEFAULT_ALLOWED_USES.get(kind, frozenset())]
    try:
        declared_acquired_at = require_declared_time(acquired_at, field_name="acquired_at", context="local file ingestion")
        envelope = build_source_envelope(
            source_id=resolved_source_id,
            source_kind=kind,
            acquisition_mode="LOCAL_ONLY",
            acquired_at=declared_acquired_at,
            # Home-directory prefixes are redacted (reusing the same
            # redaction the C2b probe already applies) so a canonical
            # SourceEnvelope never embeds the ingesting machine's username.
            origin_reference=origin_reference if origin_reference is not None else redact_value(str(source)),
            owner_scope=owner_scope,
            visibility=visibility,
            allowed_uses=uses,
            raw_sha256=raw_sha256,
            parser_version=PARSER_VERSION,
            redaction_version=REDACTION_VERSION,
        )
    except SourceTimeError as exc:
        raise IngestError(str(exc)) from exc
    except ContractError as exc:
        raise IngestError(f"invalid source envelope: {exc}") from exc

    manifest_path = write_source_manifest(run_root, envelope)

    run_state = load_or_create(
        run_root,
        run_id=run_root.name,
        git_commit=current_git_commit(),
        config_hash=config_hash,
        resume=paths.manifest.exists(),
    )
    with run_lock(paths, run_state.manifest["run_id"]):
        run_state.record_ingested_source(envelope.source_id)

    return {
        "status": "ARCHIVED",
        "source_id": envelope.source_id,
        "raw_sha256": raw_sha256,
        "content_hash": envelope.content_hash(),
        "manifest_path": str(manifest_path),
        "allowed_uses": sorted(use.value for use in envelope.allowed_uses),
        # Operational metadata only: when this ingestion tool actually ran,
        # never part of the envelope's content-derived identity/hash above
        # (see provenance.require_declared_time's docstring for why).
        "ingested_at": _timestamp(),
    }


__all__ = ["IngestError", "current_git_commit", "ingest_local_file"]
