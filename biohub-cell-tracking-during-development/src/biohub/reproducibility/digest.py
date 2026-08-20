"""Independent digest re-implementations used to audit persisted receipts.

These deliberately do **not** import
:mod:`biohub.strong_baseline.manifest` or :mod:`biohub.detector_fixed_race.cache`.
An audit that calls the same function that produced the number cannot detect a bug in
that function.  The algorithms are re-derived from the documented contract strings
(``HASH_ALGORITHM`` and the canonical-JSON cache hash) so a divergence between the
producing code and its own documented contract shows up as a test failure.

Everything here is pure stdlib: no numpy, no torch, no tracksdata, no zarr.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PREDICTION_DIRECTORY_HASH_ALGORITHM = "sha256(sorted relative file path + NUL + file bytes + NUL)"

_CHUNK = 1024 * 1024


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of *path*, read in bounded chunks."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_digest(path: Path) -> str:
    """Return the prediction-directory digest for *path*.

    Implements ``PREDICTION_DIRECTORY_HASH_ALGORITHM`` from first principles: files
    sorted by path, each contributing ``relative_posix_path || NUL || bytes || NUL``.
    """

    return directory_digest_report(path)["directory_sha256"]


def directory_digest_report(path: Path) -> dict[str, Any]:
    """Return digest, file count and byte count for a prediction directory."""

    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"prediction directory does not exist: {path}")
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        payload = child.read_bytes()
        digest.update(payload)
        digest.update(b"\0")
        files += 1
        total_bytes += len(payload)
    return {
        "directory_sha256": digest.hexdigest(),
        "files": files,
        "total_bytes": total_bytes,
        "hash_algorithm": PREDICTION_DIRECTORY_HASH_ALGORITHM,
    }


def recompute_cache_hash(manifest: Mapping[str, Any]) -> str:
    """Recompute a detector cache manifest's ``cache_hash`` from its own content.

    The declared contract is: canonical JSON of every manifest field except
    ``cache_hash``, sorted keys, compact separators, no NaN, then SHA-256.  If any
    field of the manifest is edited without regenerating the hash, this diverges.
    """

    payload = {key: value for key, value in dict(manifest).items() if key != "cache_hash"}
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "PREDICTION_DIRECTORY_HASH_ALGORITHM",
    "directory_digest",
    "directory_digest_report",
    "file_sha256",
    "recompute_cache_hash",
]
