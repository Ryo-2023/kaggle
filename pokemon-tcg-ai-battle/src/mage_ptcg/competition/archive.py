"""Atomic, path-safe raw/derived archive writer for competition probes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .fingerprint import fingerprint_document
from .redaction import REDACTION_VERSION, redact_value, secret_scan


class ArchiveSafetyError(RuntimeError):
    """Raised when an archive could escape its root or retain unsafe output."""


class DuplicateProbeError(FileExistsError):
    """Raised instead of overwriting an existing probe archive."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _safe_probe_id(probe_id: str) -> str:
    if not probe_id or probe_id in {".", ".."} or "/" in probe_id or "\\" in probe_id:
        raise ArchiveSafetyError("probe id must be a single safe path component")
    if not all(char.isalnum() or char in "._-" for char in probe_id):
        raise ArchiveSafetyError("probe id contains unsupported characters")
    return probe_id


def _prepare_root(output_dir: str | Path) -> Path:
    root = Path(output_dir)
    if root.exists() and root.is_symlink():
        raise ArchiveSafetyError("output directory must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _write_file(root: Path, relative: str, content: bytes) -> None:
    target = root / relative
    if target.parent.is_symlink() or root not in target.resolve().parents:
        raise ArchiveSafetyError("archive path escaped temporary root")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def archive_probe(
    *,
    output_dir: str | Path,
    probe_id: str,
    manifest: dict[str, Any],
    summary: dict[str, Any],
    response: bytes | None,
    response_json: Any | None,
    error: dict[str, Any] | None,
    force: bool = False,
) -> Path:
    """Publish one complete probe directory with an atomic final rename.

    A response with possible secrets remains in the ignored quarantine subtree;
    only its redacted derivative and safe metadata are written elsewhere.
    """
    root = _prepare_root(output_dir)
    probe_id = _safe_probe_id(probe_id)
    destination = root / probe_id
    if destination.exists() and not force:
        raise DuplicateProbeError(f"probe archive already exists: {probe_id}")
    if destination.is_symlink():
        raise ArchiveSafetyError("probe destination must not be a symlink")

    temporary = Path(tempfile.mkdtemp(prefix=f".{probe_id}.tmp-", dir=root))
    try:
        response_hash = hashlib.sha256(response or b"").hexdigest() if response is not None else None
        raw_findings = secret_scan(response_json if response_json is not None else (response or b""))
        safe_manifest = dict(manifest)
        safe_manifest.update(
            {
                "redaction_version": REDACTION_VERSION,
                "response_sha256": response_hash,
                "raw_secret_findings": raw_findings,
                "raw_storage": "quarantine" if raw_findings else "raw",
            }
        )
        _write_file(temporary, "manifest.json", _json_bytes(safe_manifest))
        _write_file(temporary, "summary.json", _json_bytes(summary))
        fingerprint_input = response_json if response_json is not None else {
            "content_type": summary.get("response_content_type"),
            "byte_size": len(response or b""),
            "parse_failure": response is not None and response_json is None,
        }
        _write_file(temporary, "schema-fingerprint.json", _json_bytes(fingerprint_document(fingerprint_input)))
        _write_file(
            temporary,
            "raw/metadata.json",
            _json_bytes({"sha256": response_hash, "byte_size": len(response or b""), "secret_findings": raw_findings}),
        )
        if response is not None:
            relative = "quarantine/response.bin" if raw_findings else "raw/response.bin"
            _write_file(temporary, relative, response)
        if response_json is not None:
            redacted = redact_value(response_json)
            remaining = secret_scan(redacted)
            if remaining:
                raise ArchiveSafetyError("redacted response did not pass the secret scan")
            _write_file(temporary, "redacted/response.json", _json_bytes(redacted))
        if error is not None:
            redacted_error = redact_value(error)
            if secret_scan(redacted_error):
                raise ArchiveSafetyError("redacted error did not pass the secret scan")
            _write_file(temporary, "errors/error.json", _json_bytes(redacted_error))
        if force and destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
        directory_fd = os.open(str(root), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
