"""Privacy-gated, canonical public trajectory persistence (O6-AUD-002 final remediation).

This is the *runtime write* side: raw observations are never written to
disk. Every event a game produces is a strict allow-list projection (see
:mod:`mage_ptcg.opponents.public_trajectory_projection`), independently
privacy-scanned again here as defense-in-depth (see
:mod:`mage_ptcg.opponents.privacy_gate`) before a single byte is written.
The independent verifier (:mod:`mage_ptcg.opponents.independent_trajectory_verifier`)
must not share code with this module; see its docstring.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.competition_intelligence.canonical import canonical_json_bytes, sha256_hex

from .errors import OpponentError
from .privacy_gate import assert_public_only
from .public_trajectory_projection import build_public_trajectory_events

TRAJECTORY_MANIFEST_SCHEMA_VERSION = "o6-public-trajectory-manifest-v1"
CANONICALIZATION_VERSION = "o6-canonical-json-v1"
EVIDENCE_FORMAT_VERSION = "o6-evidence-format-v1"


class ImmutableEvidenceConflict(OpponentError):
    """Attempted to overwrite already-persisted evidence with different content."""


def write_immutable_json(path: Path, value: Any) -> None:
    """Write ``value`` as JSON unless different content already exists at ``path``.

    Same content already on disk is a no-op (idempotent republish, safe for
    a resumed League run). Different content already on disk is a tamper/
    reuse conflict and raises rather than silently overwriting evidence.
    """
    path = Path(path)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if canonical_json_bytes(existing) != canonical_json_bytes(value):
            raise ImmutableEvidenceConflict(f"refusing to overwrite immutable evidence with different content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_gzip_jsonl(path: Path, events: list[dict[str, Any]]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for event in events) + "\n"
    raw_bytes = lines.encode("utf-8")
    if path.exists():
        with gzip.open(path, "rb") as handle:
            existing = handle.read()
        if existing != raw_bytes:
            raise ImmutableEvidenceConflict(f"refusing to overwrite immutable public trajectory with different content: {path}")
        return path.read_bytes()
    with open(path, "wb") as fileobj:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fileobj, mtime=0) as handle:
            handle.write(raw_bytes)
    return path.read_bytes()


def persist_game_evidence(evidence_root: Path, game_dir_id: str, *, canonical_steps: Sequence[Sequence[Mapping[str, Any]]],
                           runtime_digests: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed persist of one game's public trajectory projection evidence.

    Projection (allow-list, may raise ``PublicSchemaUnknownFieldError``) runs
    before privacy scanning, which runs before anything is written: a single
    rejected event blocks the whole game's evidence, not just that event.
    """
    events = build_public_trajectory_events(canonical_steps)
    for event in events:
        assert_public_only(event)
    game_dir = Path(evidence_root) / "games" / game_dir_id
    jsonl_gz_path = game_dir / "public_projection_trajectory.jsonl.gz"
    compressed = _write_gzip_jsonl(jsonl_gz_path, events)
    manifest = {
        "schema_version": TRAJECTORY_MANIFEST_SCHEMA_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evidence_format_version": EVIDENCE_FORMAT_VERSION,
        "game_dir_id": game_dir_id,
        "event_count": len(events),
        "runtime_digests": dict(runtime_digests),
        "privacy_validation": {"status": "PASS", "events_checked": len(events)},
    }
    write_immutable_json(game_dir / "trajectory_manifest.json", manifest)
    write_immutable_json(game_dir / "game_metadata.json", dict(metadata))
    digest_text_path = game_dir / "runtime_digest.txt"
    digest_text = json.dumps(dict(runtime_digests), sort_keys=True) + "\n"
    if digest_text_path.exists() and digest_text_path.read_text(encoding="utf-8") != digest_text:
        raise ImmutableEvidenceConflict(f"refusing to overwrite immutable evidence with different content: {digest_text_path}")
    digest_text_path.write_text(digest_text, encoding="utf-8")
    hashes = {
        "schema_version": "o6-public-evidence-hashes-v1",
        "files": {
            "public_projection_trajectory.jsonl.gz": sha256_hex(compressed),
            "trajectory_manifest.json": sha256_hex((game_dir / "trajectory_manifest.json").read_bytes()),
            "game_metadata.json": sha256_hex((game_dir / "game_metadata.json").read_bytes()),
            "runtime_digest.txt": sha256_hex((game_dir / "runtime_digest.txt").read_bytes()),
        },
    }
    write_immutable_json(game_dir / "hashes.json", hashes)
    return manifest


def compute_checksums_file(root: Path, checksums_path: Path) -> None:
    """Write a ``sha256sum -c``-compatible checksums file covering every file under ``root``.

    Excludes ``checksums_path`` itself (a checksums file cannot reference
    its own bytes) and is source-order independent (paths are sorted before
    writing).
    """
    root = Path(root)
    checksums_path = Path(checksums_path)
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.resolve() != checksums_path.resolve()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}" for path in files]
    checksums_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
