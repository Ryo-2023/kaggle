"""Multi-level evidence integrity chain and external trusted-root anchor (O6-AUD-002 INTEGRITY-001).

Runtime/orchestrator-only: builds ``run_manifest.json`` and computes
``run_root.sha256`` so tampering with any manifest/hashes/summary file --
not just the raw trajectory bytes -- is detectable. The independent verifier
re-derives its own expectation of these values from disk at verification
time (see ``independent_trajectory_verifier.py``); it does not import this
module, matching the same independence discipline as the rest of O6-AUD-002.

External anchoring: ``run_root.sha256`` living inside the run directory
cannot detect an attacker who rewrites the whole run directory (including
that file) consistently. ``docs/evidence/o6-trusted-league-roots.json`` is
committed to git *outside* the run directory; its own git history is the
actual external trust anchor -- a run's ``run_root_sha256`` cannot be
silently changed there without a new, reviewable git commit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

RUN_MANIFEST_SCHEMA_VERSION = "o6-league-run-manifest-v2"
RUN_ROOT_CANONICALIZATION_VERSION = "o6-run-root-canonical-v1"
TRUSTED_ROOT_REGISTRY_SCHEMA_VERSION = "o6-trusted-league-roots-v1"


def compute_run_root_sha256(run_dir: Path, *, exclude: set[str] = frozenset()) -> str:
    """SHA-256 over a canonical, sorted ``{relative_path: file_sha256}`` mapping of every file under ``run_dir``.

    Any file content change, insertion, deletion, or rename under ``run_dir``
    changes this hash (each is a change to the mapping's keys and/or values).
    """
    run_dir = Path(run_dir)
    entries: dict[str, str] = {}
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        relpath = path.relative_to(run_dir).as_posix()
        if relpath in exclude:
            continue
        entries[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_run_manifest(*, run_id: str, sorted_game_ids: list[str], game_manifest_hashes: Mapping[str, str],
                        summary_hash: str, participant_ids: list[str], population_id: str,
                        team_bundle_hashes: Mapping[str, str], ruleset_version: str, cabt_version: str,
                        evidence_format_version: str) -> dict[str, Any]:
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "canonicalization_version": RUN_ROOT_CANONICALIZATION_VERSION,
        "run_id": run_id,
        "sorted_game_ids": sorted(sorted_game_ids),
        "game_manifest_hashes": dict(game_manifest_hashes),
        "summary_hash": summary_hash,
        "participant_ids": sorted(participant_ids),
        "population_id": population_id,
        "team_bundle_hashes": dict(team_bundle_hashes),
        "ruleset_version": ruleset_version,
        "cabt_version": cabt_version,
        "evidence_format_version": evidence_format_version,
    }


def write_trusted_root_entry(registry_path: Path, *, run_id: str, run_root_sha256: str, source_commit: str,
                              population_id: str, evidence_schema: str, status: str = "TRUSTED") -> None:
    registry_path = Path(registry_path)
    registry: dict[str, Any] = {"schema_version": TRUSTED_ROOT_REGISTRY_SCHEMA_VERSION, "trusted_roots": []}
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = [e for e in registry.get("trusted_roots", []) if e.get("run_id") != run_id]
    entries.append({
        "run_id": run_id, "run_root_sha256": run_root_sha256, "source_commit": source_commit,
        "population_id": population_id, "evidence_schema": evidence_schema, "status": status,
    })
    registry["trusted_roots"] = sorted(entries, key=lambda e: e["run_id"])
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def load_trusted_root_entry(registry_path: Path, run_id: str) -> dict[str, Any] | None:
    registry_path = Path(registry_path)
    if not registry_path.exists():
        return None
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for entry in registry.get("trusted_roots", []):
        if entry.get("run_id") == run_id:
            return entry
    return None
