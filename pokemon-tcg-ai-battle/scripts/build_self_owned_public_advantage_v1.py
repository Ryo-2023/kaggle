#!/usr/bin/env python3
"""Materialize a real common24 public state/action diagnostic.

The command reads the previously collected public-only common24 source, binds
all source identity and permission hashes, and writes a small immutable table
plus manifest.  It never starts an evaluator, runs a game, or writes raw
trajectory data.  A quality gate with sparse/mixed-sign evidence is required
before any future candidate screen; this command does not screen candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from mage_ptcg.meta_specialist.self_owned_public_advantage_v1 import (  # noqa: E402
    SCHEMA_V1,
    SelfOwnedPublicAdvantageError,
    build_state_action_advantage_table_v1,
    load_real_common24_state_action_source_v1,
)


_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}


class AdvantageBundleError(ValueError):
    """Raised when a diagnostic bundle cannot be atomically materialized."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdvantageBundleError("bundle value is not canonical JSON") from exc


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        raise AdvantageBundleError(f"cannot hash bundle file: {path}") from exc


def _atomic_claim_bytes(path: Path, raw: bytes) -> str:
    """Publish bytes with exclusive claim; never clobber a competing winner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise AdvantageBundleError(f"cannot inspect existing bundle file: {path}") from exc
        if existing != raw:
            raise AdvantageBundleError(f"refusing to overwrite existing bundle file: {path}")
        return _sha_bytes(existing)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # os.link is an exclusive destination claim on the same filesystem;
            # unlike os.replace it cannot clobber another writer's winner.
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise AdvantageBundleError(f"bundle destination won a conflicting race: {path}")
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AdvantageBundleError(f"atomic bundle publish failed: {path}") from exc
    return _sha_bytes(raw)


def _verify_table(table: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(table, Mapping) or table.get("schema_version") != SCHEMA_V1:
        raise AdvantageBundleError("diagnostic table schema is unsupported")
    if table.get("authority") != _AUTHORITY_FALSE or table.get("research_only") is not True:
        raise AdvantageBundleError("diagnostic table authority gate failed")
    if table.get("private_state_used") is not False or table.get("teacher_labels_used") is not False:
        raise AdvantageBundleError("diagnostic table privacy/teacher gate failed")
    provenance = table.get("source_provenance")
    if not isinstance(provenance, Mapping):
        raise AdvantageBundleError("diagnostic table source provenance is missing")
    if provenance.get("record_count") != 96 or provenance.get("engine_seed_support") != "ENGINE_SEED_UNSUPPORTED":
        raise AdvantageBundleError("diagnostic table source is not complete common24")
    expected = _sha_bytes(b"self-owned-public-state-action-table-v1\0" + _canonical({key: value for key, value in table.items() if key != "table_sha256"}))
    if table.get("table_sha256") != expected:
        raise AdvantageBundleError("diagnostic table semantic SHA does not verify")
    return dict(table)


def materialize_state_action_bundle_v1(
    *, output_dir: Path | str, table: Mapping[str, object], source_provenance: Mapping[str, object],
) -> dict[str, object]:
    """Atomically publish a table and its bound, research-only manifest."""
    verified = _verify_table(table)
    if dict(verified["source_provenance"]) != dict(source_provenance):
        raise AdvantageBundleError("table/source provenance mismatch")
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    table_raw = _canonical(verified) + b"\n"
    table_path = destination / "state-action-advantage-table.json"
    table_file_sha = _atomic_claim_bytes(table_path, table_raw)
    quality_gate = verified.get("quality_gate")
    if not isinstance(quality_gate, Mapping):
        raise AdvantageBundleError("diagnostic table quality gate is missing")
    manifest = {
        "schema_version": "self-owned-public-state-action-bundle-v1",
        "table_schema_version": SCHEMA_V1,
        "table_path": str(table_path),
        "table_file_sha256": table_file_sha,
        "table_sha256": verified["table_sha256"],
        "source_provenance": dict(source_provenance),
        "quality_gate": dict(quality_gate),
        "ready_for_candidate_screen": bool(quality_gate.get("ready_for_candidate_screen") is True),
        "ready_for_longrun": False,
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "private_state_used": False,
        "teacher_labels_used": False,
        "candidate_screen_started": False,
        "performance_run_started": False,
        "notes": [
            "Real self-owned Rule-v0 public common24 rows only.",
            "This is a sparse state/action outcome diagnostic, not a trained value function.",
            "No candidate screen is admissible until the quality gate is true.",
        ],
    }
    manifest_raw = _canonical(manifest) + b"\n"
    manifest_path = destination / "bundle-manifest.json"
    manifest_sha = _atomic_claim_bytes(manifest_path, manifest_raw)
    result = dict(manifest)
    result["manifest_file_sha256"] = manifest_sha
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        examples, provenance = load_real_common24_state_action_source_v1(
            records_path=args.records,
            evidence_root=args.evidence_root,
            source_manifest_path=args.source_manifest,
        )
        table = build_state_action_advantage_table_v1(
            examples,
            source_provenance=provenance,
            min_support=3,
        )
        result = materialize_state_action_bundle_v1(
            output_dir=args.output_dir,
            table=table,
            source_provenance=provenance,
        )
    except (SelfOwnedPublicAdvantageError, AdvantageBundleError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["AdvantageBundleError", "main", "materialize_state_action_bundle_v1"]
