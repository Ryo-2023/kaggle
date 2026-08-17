"""Building, (de)serializing, and archiving ``SourceEnvelope`` provenance.

Source manifest filenames are derived from a hash of ``source_id`` (not the
raw string) so an attacker- or bug-supplied ``source_id`` containing path
separators or ``..`` can never escape ``source_manifests/`` — the same
defensive pattern the content-addressed raw archive already uses for blob
paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .atomic_io import atomic_write_json
from .canonical import sha256_hex
from .contracts import (
    SOURCE_ENVELOPE_SCHEMA_VERSION,
    AcquisitionMode,
    ContractError,
    SourceEnvelope,
    SourceKind,
)
from .permissions import validate_allowed_uses_subset
from .runstate import RunPaths


class SourceTimeError(ValueError):
    """Raised when a source's declared acquisition/observation time is missing.

    Independent-audit remediation: ``SourceEnvelope.acquired_at`` is part of
    the envelope's content-derived identity (``content_hash()`` includes it,
    see ``contracts.SourceEnvelope.content_payload``). Silently substituting
    the ingestion tool's current wall-clock time whenever a caller omits it
    would make re-ingesting byte-identical content produce a different,
    non-deterministic ``SourceEnvelope`` identity depending on *when* the
    ingestion happened to run -- never on what was ingested. Callers must
    supply the source's own declared time explicitly; this is never
    auto-filled with ``datetime.now()``-style values.
    """


def require_declared_time(value: str | None, *, field_name: str, context: str) -> str:
    """Return ``value`` if provided; otherwise raise ``SourceTimeError``.

    The operational "when did the ingestion tool actually run this" moment
    is a *separate* concept (``ingested_at``, tracked by each ingestion
    function's own caller-facing summary, never part of any content hash)
    from this source-declared time -- see this module's and
    ``local_ingest.py``/``team_bundle.py``/``external_acquisition.py``'s
    docstrings for the full separation.
    """
    if not value:
        raise SourceTimeError(
            f"{field_name} is required for {context} and must be the source's own declared time; "
            "it is never auto-filled with the current time because it is part of the SourceEnvelope's "
            "content-derived identity (see contracts.SourceEnvelope.content_hash)"
        )
    return value


def build_source_envelope(
    *,
    source_id: str,
    source_kind: SourceKind | str,
    acquisition_mode: AcquisitionMode | str,
    acquired_at: str,
    origin_reference: str,
    owner_scope: str,
    visibility: str,
    allowed_uses: Iterable[str],
    raw_sha256: str,
    parser_version: str,
    redaction_version: str,
    observed_at: str | None = None,
    terms_snapshot_hash: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> SourceEnvelope:
    """Construct a validated ``SourceEnvelope``.

    ``allowed_uses`` must be the *actual* permission grant for this source
    (e.g. from a team bundle's ``permission_statement``, or the fixed
    defaults for one's own local/Kaggle data) — never a convenience default
    applied because the caller didn't check.
    """
    kind = source_kind if isinstance(source_kind, SourceKind) else SourceKind(source_kind)
    mode = acquisition_mode if isinstance(acquisition_mode, AcquisitionMode) else AcquisitionMode(acquisition_mode)
    if isinstance(allowed_uses, str):
        raise ContractError("allowed_uses must be an iterable of use names, not a single string")
    uses = validate_allowed_uses_subset(allowed_uses)
    return SourceEnvelope(
        schema_version=SOURCE_ENVELOPE_SCHEMA_VERSION,
        source_id=source_id,
        source_kind=kind,
        acquisition_mode=mode,
        acquired_at=acquired_at,
        observed_at=observed_at,
        origin_reference=origin_reference,
        owner_scope=owner_scope,
        visibility=visibility,
        allowed_uses=uses,
        terms_snapshot_hash=terms_snapshot_hash,
        raw_sha256=raw_sha256,
        parser_version=parser_version,
        redaction_version=redaction_version,
        metadata=dict(metadata) if metadata else {},
    )


def envelope_to_manifest_payload(envelope: SourceEnvelope) -> dict[str, Any]:
    payload = envelope.content_payload()
    payload["content_hash"] = envelope.content_hash()
    return payload


def envelope_from_manifest_payload(payload: Mapping[str, Any]) -> SourceEnvelope:
    if not isinstance(payload, Mapping):
        raise ContractError("source manifest payload must be a mapping")
    fields = {key: value for key, value in payload.items() if key != "content_hash"}
    try:
        envelope = SourceEnvelope(
            schema_version=fields["schema_version"],
            source_id=fields["source_id"],
            source_kind=SourceKind(fields["source_kind"]),
            acquisition_mode=AcquisitionMode(fields["acquisition_mode"]),
            acquired_at=fields["acquired_at"],
            observed_at=fields.get("observed_at"),
            origin_reference=fields["origin_reference"],
            owner_scope=fields["owner_scope"],
            visibility=fields["visibility"],
            allowed_uses=validate_allowed_uses_subset(fields["allowed_uses"]),
            terms_snapshot_hash=fields.get("terms_snapshot_hash"),
            raw_sha256=fields["raw_sha256"],
            parser_version=fields["parser_version"],
            redaction_version=fields["redaction_version"],
            metadata=fields.get("metadata") or {},
        )
    except KeyError as exc:
        raise ContractError(f"source manifest payload is missing required field {exc}") from exc
    expected_hash = payload.get("content_hash")
    if expected_hash is not None and expected_hash != envelope.content_hash():
        raise ContractError(
            f"source manifest content_hash mismatch: stored {expected_hash} but recomputed {envelope.content_hash()}"
        )
    return envelope


def _manifest_filename(source_id: str) -> str:
    return sha256_hex(source_id.encode("utf-8")) + ".json"


def source_manifest_path(run_root: Path, source_id: str) -> Path:
    return RunPaths(run_root).source_manifests / _manifest_filename(source_id)


def write_source_manifest(run_root: Path, envelope: SourceEnvelope) -> Path:
    destination = source_manifest_path(run_root, envelope.source_id)
    atomic_write_json(destination, envelope_to_manifest_payload(envelope))
    return destination


def read_source_manifest(run_root: Path, source_id: str) -> SourceEnvelope:
    destination = source_manifest_path(run_root, source_id)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    return envelope_from_manifest_payload(payload)


__all__ = [
    "SourceTimeError",
    "build_source_envelope",
    "envelope_from_manifest_payload",
    "envelope_to_manifest_payload",
    "read_source_manifest",
    "require_declared_time",
    "source_manifest_path",
    "write_source_manifest",
]
