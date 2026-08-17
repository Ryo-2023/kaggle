"""Read-only reader for an existing Offline Training v1 run directory.

Never writes to, renames, appends to, or deletes anything under the source
run; every function here only opens files for reading.

This module deliberately does **not** import ``mage_ptcg.student`` or
``mage_ptcg.offline_training.dataset`` — both transitively import the Student
runtime module (``mage_ptcg.student.runtime.RuntimeStudentPolicy``) as a side
effect of ``mage_ptcg/student/__init__.py``, which would put a
"competition_intelligence -> Student runtime" edge in the import graph even
though nothing here calls any inference code. Instead this module re-parses
the ``rule-bc-v1.jsonl`` row shape directly: that row shape is a stable,
versioned, on-disk JSON contract (``schema_version: "rule-bc-v1"``), so
reading it here is a data-format read, not a runtime dependency. The **Stable
ActionKey** digests already present in every row are used verbatim (never
recomputed), which is what "reuse Stable ActionKey exactly" means at the
level that matters: the identity, not the Python class used to build it.

Only ``mage_ptcg.offline_training.runstate`` (paths/schema constants only, no
Student coupling — its package ``__init__`` is empty) is reused for run
discovery conventions.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

RULE_BC_SCHEMA_VERSION = "rule-bc-v1"
DATASET_MANIFEST_SCHEMA_VERSION = "offline-training-v1-dataset-v1"

# KNOWN SCHEMA NAMING COLLISION (independent-audit finding #5, minimal-fix
# scope): a raw row's own "source_id" key (below, inherited unchanged from
# mage_ptcg.offline_training's producer schema) actually holds the row's
# *episode* identity, not a contracts.SourceEnvelope id -- see
# replay_normalize.normalize_rule_bc_jsonl's docstring for the full
# explanation and why a full rule-bc-v2 schema split was out of scope here.
_REQUIRED_ROW_KEYS = (
    "schema_version",
    "example_id",
    "source_id",
    "public_state",
    "own_private_state",
    "selection_type",
    "selection_context",
    "min_count",
    "max_count",
    "legal_actions",
    "target_action_digests",
    "fallback_used",
    "deck_fingerprint",
    "metadata",
)
_REQUIRED_METADATA_KEYS = ("decision_index", "episode_group_id", "seat")


class RuleBCRowError(ValueError):
    """Raised for a structurally invalid ``rule-bc-v1`` row (caller should quarantine it)."""


@dataclass(frozen=True, slots=True)
class DiscoveredRun:
    """Paths found (or not found) under a candidate Offline Training run root."""

    run_root: Path
    run_manifest_path: Path | None
    run_manifest_schema_version: str | None
    collection_jsonl_path: Path | None
    collection_bindings_path: Path | None
    dataset_manifest_path: Path | None

    def is_usable(self) -> bool:
        return self.collection_jsonl_path is not None


def discover_offline_training_run(run_root: str | Path) -> DiscoveredRun:
    """Locate the known artifacts under ``run_root`` without reading their content.

    Tolerant by design: a run missing some pieces (e.g. no built dataset yet,
    only raw collection) is not an error here — callers decide what they can
    do with what's found. Only ``run_manifest.json`` is opened (to read its
    ``schema_version``); everything else is a pure path/glob discovery.
    """
    root = Path(run_root)
    manifest_path = root / "run_manifest.json"
    manifest_schema: str | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, Mapping):
                schema = manifest.get("schema_version")
                manifest_schema = schema if isinstance(schema, str) else None
        except (OSError, json.JSONDecodeError):
            manifest_schema = None

    collection_jsonl: Path | None = None
    bindings: Path | None = None
    # Two layouts are both real: the full Offline Training v1 CLI pipeline
    # (Pipeline.phase_collect) writes under "<run>/collection/<id>/...", but
    # mage_ptcg.offline_training.collection.run_collection() itself has no
    # opinion on that extra directory and is also called directly (with
    # output_root pointing straight at the target dir) by tests and ad-hoc
    # collection scripts, producing "<run>/<id>/..." with no "collection/"
    # layer. Try the pipeline layout first (it is what production runs use).
    for candidates in (
        sorted((root / "collection").glob("*/private_dataset/rule-bc-v1.jsonl")) if (root / "collection").is_dir() else [],
        sorted(root.glob("*/private_dataset/rule-bc-v1.jsonl")) if root.is_dir() else [],
    ):
        if candidates:
            collection_jsonl = candidates[0]
            sibling_bindings = collection_jsonl.parent / "private_bindings.jsonl"
            if sibling_bindings.is_file():
                bindings = sibling_bindings
            break

    dataset_manifest: Path | None = None
    candidate_dataset_manifest = root / "dataset" / "canonical" / "dataset_manifest.json"
    if candidate_dataset_manifest.is_file():
        dataset_manifest = candidate_dataset_manifest

    return DiscoveredRun(
        run_root=root,
        run_manifest_path=manifest_path if manifest_path.is_file() else None,
        run_manifest_schema_version=manifest_schema,
        collection_jsonl_path=collection_jsonl,
        collection_bindings_path=bindings,
        dataset_manifest_path=dataset_manifest,
    )


def parse_rule_bc_row(raw: object) -> dict[str, Any]:
    """Validate and return ``raw`` as a plain dict, or raise ``RuleBCRowError``.

    Deliberately more tolerant than the producer's own ``RuleBCExample.from_dict``
    (which raises on many finer-grained issues meant for a trusted internal
    pipeline): this only checks the structural minimum needed to safely
    extract the fields O1-2 normalizes, since malformed rows here are meant
    to be quarantined and skipped, not treated as a fatal ingestion error.
    """
    if not isinstance(raw, Mapping):
        raise RuleBCRowError("row is not a JSON object")
    missing = [key for key in _REQUIRED_ROW_KEYS if key not in raw]
    if missing:
        raise RuleBCRowError(f"row missing required keys: {missing}")
    if raw["schema_version"] != RULE_BC_SCHEMA_VERSION:
        raise RuleBCRowError(f"unsupported schema_version {raw['schema_version']!r}")
    metadata = raw["metadata"]
    if not isinstance(metadata, Mapping):
        raise RuleBCRowError("metadata is not a JSON object")
    missing_meta = [key for key in _REQUIRED_METADATA_KEYS if key not in metadata]
    if missing_meta:
        raise RuleBCRowError(f"metadata missing required keys: {missing_meta}")
    legal_actions = raw["legal_actions"]
    if not isinstance(legal_actions, list) or not all(
        isinstance(action, Mapping) and "digest" in action for action in legal_actions
    ):
        raise RuleBCRowError("legal_actions is malformed (expected a list of {digest, payload} objects)")
    target_digests = raw["target_action_digests"]
    if not isinstance(target_digests, list) or not all(isinstance(digest, str) for digest in target_digests):
        raise RuleBCRowError("target_action_digests is malformed (expected a list of digest strings)")
    public_state = raw["public_state"]
    if not isinstance(public_state, Mapping):
        raise RuleBCRowError("public_state is not a JSON object")
    return dict(raw)


def iter_rule_bc_rows(path: str | Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    """Yield ``(line_number, parsed_row_or_None, error_or_None)`` for every line.

    Never raises on a single malformed line — the caller (normalization)
    decides whether to quarantine it. ``line_number`` is 0-indexed.
    Transparently handles both plain ``.jsonl`` and gzip-compressed
    ``.jsonl.gz`` (used by built dataset shards).
    """
    source = Path(path)
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line_number, line in enumerate(handle):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
                row = parse_rule_bc_row(raw)
            except (json.JSONDecodeError, RuleBCRowError) as exc:
                yield line_number, None, str(exc)
                continue
            yield line_number, row, None


def read_dataset_manifest(path: str | Path) -> dict[str, Any]:
    """Read a built dataset's ``dataset_manifest.json`` (read-only, no writes)."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise RuleBCRowError("dataset_manifest.json is not a JSON object")
    if manifest.get("schema_version") != DATASET_MANIFEST_SCHEMA_VERSION:
        raise RuleBCRowError(f"unsupported dataset manifest schema_version {manifest.get('schema_version')!r}")
    return dict(manifest)


__all__ = [
    "DATASET_MANIFEST_SCHEMA_VERSION",
    "RULE_BC_SCHEMA_VERSION",
    "DiscoveredRun",
    "RuleBCRowError",
    "discover_offline_training_run",
    "iter_rule_bc_rows",
    "parse_rule_bc_row",
    "read_dataset_manifest",
]
