"""Rebuildable SQLite catalog: a non-canonical index over canonical artifacts.

Deleting ``state/catalog.sqlite3`` and calling :func:`rebuild_catalog` again
must always reproduce an equivalent catalog, because the catalog stores
nothing that isn't already derivable from ``source_manifests/*.json`` (and,
once O1-2 normalization exists, ``normalized/*.jsonl``). This module only
implements the sources index today; an episodes/decisions index is expected
to be added once ``normalize`` is implemented, without changing this
function's contract of "safe to delete, always rebuildable".
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from .contracts import ContractError
from .provenance import envelope_from_manifest_payload
from .runstate import RunPaths

CATALOG_SCHEMA_VERSION = "competition-intelligence-catalog-v1"

_SCHEMA_STATEMENTS = (
    "CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE sources ("
    "source_id TEXT PRIMARY KEY, "
    "source_kind TEXT NOT NULL, "
    "acquisition_mode TEXT NOT NULL, "
    "acquired_at TEXT NOT NULL, "
    "allowed_uses TEXT NOT NULL, "
    "raw_sha256 TEXT NOT NULL, "
    "content_hash TEXT NOT NULL, "
    "manifest_file TEXT NOT NULL"
    ")",
)


class CatalogError(RuntimeError):
    """Raised when the catalog cannot be rebuilt from canonical artifacts."""


def rebuild_catalog(run_root: str | Path) -> dict[str, int]:
    """Rebuild ``state/catalog.sqlite3`` from ``source_manifests/*.json``.

    Builds into a temporary file and atomically replaces the previous
    catalog, so a crash mid-rebuild never leaves a half-written database in
    the canonical catalog path.
    """
    paths = RunPaths(Path(run_root))
    paths.state.mkdir(parents=True, exist_ok=True)
    tmp_path = paths.state / f".catalog.{os.getpid()}.tmp.sqlite3"
    if tmp_path.exists():
        tmp_path.unlink()
    quarantined = 0
    connection = sqlite3.connect(tmp_path)
    try:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        source_count = 0
        if paths.source_manifests.exists():
            for manifest_path in sorted(paths.source_manifests.glob("*.json")):
                try:
                    payload = _load_json(manifest_path)
                    envelope = envelope_from_manifest_payload(payload)
                except (ContractError, ValueError) as exc:
                    quarantined += 1
                    connection.execute(
                        "INSERT OR IGNORE INTO catalog_meta VALUES (?, ?)",
                        (f"skipped:{manifest_path.name}", str(exc)),
                    )
                    continue
                connection.execute(
                    "INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        envelope.source_id,
                        envelope.source_kind.value,
                        envelope.acquisition_mode.value,
                        envelope.acquired_at,
                        ",".join(sorted(use.value for use in envelope.allowed_uses)),
                        envelope.raw_sha256,
                        envelope.content_hash(),
                        manifest_path.name,
                    ),
                )
                source_count += 1
        connection.execute(
            "INSERT INTO catalog_meta VALUES (?, ?)", ("schema_version", CATALOG_SCHEMA_VERSION)
        )
        connection.execute(
            "INSERT INTO catalog_meta VALUES (?, ?)",
            ("rebuilt_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        connection.execute("INSERT INTO catalog_meta VALUES (?, ?)", ("source_count", str(source_count)))
        connection.commit()
    finally:
        connection.close()
    os.replace(tmp_path, paths.catalog_db)
    return {"source_count": source_count, "quarantined_manifest_count": quarantined}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_summary(run_root: str | Path) -> dict[str, str]:
    """Read back ``catalog_meta`` for a doctor/report check, without rebuilding."""
    paths = RunPaths(Path(run_root))
    if not paths.catalog_db.exists():
        raise CatalogError(f"no catalog at {paths.catalog_db}; run rebuild-catalog first")
    connection = sqlite3.connect(paths.catalog_db)
    try:
        rows = connection.execute("SELECT key, value FROM catalog_meta").fetchall()
    finally:
        connection.close()
    return dict(rows)


__all__ = ["CATALOG_SCHEMA_VERSION", "CatalogError", "catalog_summary", "rebuild_catalog"]
