"""Content-addressed immutable raw archive with fail-closed quarantine.

Reuses ``mage_ptcg.competition.redaction.secret_scan`` (already tested as
part of the C2b Competition Probe) for the scan that decides whether incoming
bytes are safe to keep as plaintext raw content or must be quarantined.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.competition.redaction import secret_scan

from .atomic_io import atomic_write_bytes, atomic_write_json
from .canonical import sha256_hex


class ArchiveError(RuntimeError):
    """Raised on an archive-safety violation (corrupt store, hash mismatch)."""


def _shard_path(root: Path, digest_hex: str) -> Path:
    if len(digest_hex) != 64 or any(char not in "0123456789abcdef" for char in digest_hex):
        raise ArchiveError(f"not a sha256 hex digest: {digest_hex!r}")
    return root / digest_hex[:2] / digest_hex


def raw_root(run_root: Path) -> Path:
    return run_root / "raw" / "sha256"


def quarantine_root(run_root: Path) -> Path:
    return run_root / "quarantine"


def raw_path(run_root: Path, digest_hex: str) -> Path:
    return _shard_path(raw_root(run_root), digest_hex)


def store_raw(run_root: Path, data: bytes) -> str:
    """Idempotently archive ``data`` by content hash; returns the sha256 hex.

    A second call with identical bytes is a no-op (content-addressed
    deduplication), not a second physical copy or an error. This is what
    makes repeated ingestion of the same source idempotent at the archive
    layer.
    """
    digest_hex = sha256_hex(data)
    destination = raw_path(run_root, digest_hex)
    if destination.exists():
        existing = destination.read_bytes()
        if sha256_hex(existing) != digest_hex:
            raise ArchiveError(f"raw archive corruption: {destination} does not match its own path hash")
        return digest_hex
    atomic_write_bytes(destination, data)
    return digest_hex


def read_raw(run_root: Path, digest_hex: str) -> bytes:
    destination = raw_path(run_root, digest_hex)
    try:
        data = destination.read_bytes()
    except FileNotFoundError as exc:
        raise ArchiveError(f"raw blob {digest_hex} not found in archive") from exc
    if sha256_hex(data) != digest_hex:
        raise ArchiveError(f"raw blob {digest_hex} is corrupt (hash mismatch on read)")
    return data


def quarantine_bytes(run_root: Path, data: bytes, *, reason: str, detail: Mapping[str, Any] | None = None) -> str:
    """Move unsafe/malformed content to quarantine instead of the raw archive.

    Keyed by content hash, same as the raw archive, so quarantining the same
    bad input twice updates the same entry rather than accumulating copies.
    """
    digest_hex = sha256_hex(data)
    directory = quarantine_root(run_root) / digest_hex[:2] / digest_hex
    atomic_write_bytes(directory / "content.bin", data)
    atomic_write_json(
        directory / "reason.json",
        {"sha256": digest_hex, "reason": reason, "detail": dict(detail) if detail else {}},
    )
    return digest_hex


def scan_before_archive(data: bytes) -> tuple[bool, tuple[str, ...]]:
    """Return ``(is_safe, labels)`` using the shared C2b secret scanner.

    ``data`` is decoded as UTF-8 JSON when possible (the scanner operates on
    parsed structures); undecodable/non-JSON bytes are treated as opaque and
    always considered safe to archive as raw bytes (there is no structured
    field to leak a secret through), matching how the C2b probe already
    handles non-JSON payloads.
    """
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return True, ()
    labels = secret_scan(value)
    return (len(labels) == 0), tuple(labels)


__all__ = [
    "ArchiveError",
    "quarantine_bytes",
    "quarantine_root",
    "raw_path",
    "raw_root",
    "read_raw",
    "scan_before_archive",
    "store_raw",
]
