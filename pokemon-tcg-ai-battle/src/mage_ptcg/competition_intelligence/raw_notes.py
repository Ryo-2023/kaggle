"""Raw human-text note archiving (O1-3 §1): the first, immutable layer.

Archives a raw text note by content hash (reusing ``archive.py``) and
records a ``SourceEnvelope`` with ``source_kind=HUMAN_TEXT``, kept strictly
separate from the structured ``KnowledgeClaim``(s) later parsed from it (see
``claim_bundle.py``) -- the raw text, its hash, provenance, permission, and
parser_version are never overwritten by the structured claim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from . import archive
from .contracts import SourceEnvelope
from .provenance import build_source_envelope, write_source_manifest

RAW_NOTE_PARSER_VERSION = "competition-intelligence-raw-note-v1"
RAW_NOTE_REDACTION_VERSION = "competition-intelligence-redaction-v1"


def archive_raw_note(
    run_root: str | Path,
    text: str,
    *,
    source_id: str,
    acquired_at: str,
    origin_reference: str,
    owner_scope: str = "self",
    visibility: str = "private",
    allowed_uses: Iterable[str] = ("ARCHIVE", "ANALYSIS", "REPORTING"),
) -> SourceEnvelope:
    """Archive raw note ``text`` and return its validated ``SourceEnvelope``.

    Raises ``archive.ArchiveError`` (never silently drops the note) if the
    secret scanner flags the content; the note is quarantined instead of
    archived in that case.
    """
    root = Path(run_root)
    data = text.encode("utf-8")
    is_safe, labels = archive.scan_before_archive(data)
    if not is_safe:
        archive.quarantine_bytes(root, data, reason="secret_scan_hit", detail={"labels": list(labels)})
        raise archive.ArchiveError(f"raw note failed secret scan and was quarantined: {list(labels)}")
    raw_sha256 = archive.store_raw(root, data)
    envelope = build_source_envelope(
        source_id=source_id,
        source_kind="HUMAN_TEXT",
        acquisition_mode="LOCAL_ONLY",
        acquired_at=acquired_at,
        origin_reference=origin_reference,
        owner_scope=owner_scope,
        visibility=visibility,
        allowed_uses=allowed_uses,
        raw_sha256=raw_sha256,
        parser_version=RAW_NOTE_PARSER_VERSION,
        redaction_version=RAW_NOTE_REDACTION_VERSION,
    )
    write_source_manifest(root, envelope)
    return envelope


__all__ = ["RAW_NOTE_PARSER_VERSION", "RAW_NOTE_REDACTION_VERSION", "archive_raw_note"]
