"""Selection-only Offline Training adapter + dataset export (O1-4 §4-5).

Integrates with ``mage_ptcg.offline_training`` in the least invasive way
possible: this module filters the *original* ``rule-bc-v1.jsonl`` rows down
to only the rows belonging to episodes an ``IntelligenceSnapshot``'s split
selected, and writes a new file in the exact same on-disk row shape. That
filtered file is then handed to the existing, **unmodified**
``mage_ptcg.offline_training.dataset.build_dataset(source_jsonl=..., ...)``
by the caller (the CLI) -- this module never imports, calls, or modifies any
``offline_training`` or ``mage_ptcg.student`` code itself, so:

- when no snapshot is used, an operator runs ``build_dataset`` on the
  original file exactly as before -- byte-for-byte unaffected by this
  module's existence;
- trainer loss, model architecture, optimizer semantics, export format,
  runtime, and package are untouched, because this module never reaches any
  of that code -- it only ever produces a *different input file* for code
  that already exists and is already tested.

Row *values* are never modified, only which rows are kept and their
relative order (preserved from the source) -- this keeps the exported file
schema-identical to ``rule-bc-v1.jsonl``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from .atomic_io import atomic_write_bytes
from .canonical import canonical_json_bytes
from .contracts import AllowedUse, SourceEnvelope
from .offline_reader import iter_rule_bc_rows
from .permissions import has_permission

EXAMPLE_WEIGHT_DEFAULT = 1.0
EXAMPLE_WEIGHTS_SCHEMA_VERSION = "competition-intelligence-example-weights-v1"


class DatasetExportError(ValueError):
    """Raised when export cannot proceed (e.g. a TRAINING permission re-check fails)."""


def enforce_training_permission(envelopes: Iterable[SourceEnvelope]) -> None:
    """Re-check ``TRAINING`` permission at export time, not only at ingestion.

    ``PUBLIC_OTHER`` sources are structurally incapable of ever holding
    ``TRAINING`` (``SourceEnvelope.__post_init__`` hard-rejects that
    combination unconditionally, from O1-1), so this is defense-in-depth
    re-verification against a caller that bypassed the earlier check or
    supplied malformed/tampered envelopes -- not the only enforcement point.
    """
    denied = sorted(envelope.source_id for envelope in envelopes if not has_permission(envelope, AllowedUse.TRAINING))
    if denied:
        raise DatasetExportError(f"source(s) do not grant TRAINING at export time: {denied}")


def export_selected_rows(
    source_jsonl: str | Path, output_jsonl: str | Path, *, selected_episode_ids: Iterable[str]
) -> dict[str, int]:
    """Write only the rows whose redacted ``source_id`` is a selected episode, preserving order.

    A pure row filter over the existing ``rule-bc-v1.jsonl`` schema -- field
    values are never modified, only row membership. Malformed source rows
    are silently skipped here (already reported by ``normalize``'s own
    quarantine step earlier in the pipeline); this function's job is
    selection, not re-validation.
    """
    selected = frozenset(selected_episode_ids)
    total_source_rows = 0
    kept_rows = 0
    lines: list[bytes] = []
    for _, row, error in iter_rule_bc_rows(source_jsonl):
        total_source_rows += 1
        if row is None:
            continue
        # NOTE (independent-audit finding): the raw rule-bc-v1 row's
        # "source_id" field is actually the *episode* identity here (one
        # ingested rule-bc-v1.jsonl file, i.e. one SourceEnvelope, contains
        # many episodes' decisions) -- see replay_normalize.py's module
        # docstring for the full explanation of this pre-existing schema
        # naming collision with contracts.SourceEnvelope.source_id.
        row_episode_id = row["source_id"]
        if row_episode_id in selected:
            lines.append(canonical_json_bytes(row) + b"\n")
            kept_rows += 1
    atomic_write_bytes(output_jsonl, b"".join(lines))
    return {"total_source_rows": total_source_rows, "kept_rows": kept_rows}


def export_selected_decision_rows(
    source_jsonl: str | Path, output_jsonl: str | Path, *, selected_decision_keys: Iterable[tuple[str, int]]
) -> dict[str, int]:
    """Write only the rows matching a selected ``(episode_id, decision_index)`` pair.

    Unlike ``export_selected_rows`` (whole-episode selection), this filters
    at **decision** granularity -- the unit ``decision_eligibility.py``'s
    training-eligibility gate operates on, per the independent-audit finding
    that a whole permitted, selected episode's decisions were exported
    unconditionally regardless of per-decision quality (fallback, verification
    basis, etc.). Row values are never modified, only row membership.
    """
    selected = frozenset(selected_decision_keys)
    total_source_rows = 0
    kept_rows = 0
    lines: list[bytes] = []
    for _, row, error in iter_rule_bc_rows(source_jsonl):
        total_source_rows += 1
        if row is None:
            continue
        row_episode_id = row["source_id"]  # see the naming note in export_selected_rows above
        metadata = row.get("metadata") or {}
        decision_index = metadata.get("decision_index")
        if decision_index is not None and (row_episode_id, decision_index) in selected:
            lines.append(canonical_json_bytes(row) + b"\n")
            kept_rows += 1
    atomic_write_bytes(output_jsonl, b"".join(lines))
    return {"total_source_rows": total_source_rows, "kept_rows": kept_rows}


def build_example_weights(
    episode_ids: Iterable[str], *, weights_by_source_kind: Mapping[str, float] | None = None
) -> dict[str, object]:
    """Build the O1-4 §4 weight *extension point* -- not activated this slice.

    Returns a JSON-serializable payload with ``example_weight`` (always
    ``1.0`` here: "missing weight means semantic weight 1.0", per the O1-4
    mandate) plus ``source_kind``/``evidence_grade``/``meta_bucket`` fields
    left ``null``. This is intentionally never read by any trainer in this
    slice -- it exists so a future slice can wire weighted loss without a
    schema change, per the explicit instruction not to activate weighted
    loss this session.
    """
    return {
        "schema_version": EXAMPLE_WEIGHTS_SCHEMA_VERSION,
        "weights": {
            episode_id: {
                "example_weight": EXAMPLE_WEIGHT_DEFAULT,
                "source_kind": None,
                "evidence_grade": None,
                "meta_bucket": None,
            }
            for episode_id in episode_ids
        },
    }


__all__ = [
    "EXAMPLE_WEIGHTS_SCHEMA_VERSION",
    "EXAMPLE_WEIGHT_DEFAULT",
    "DatasetExportError",
    "build_example_weights",
    "enforce_training_permission",
    "export_selected_decision_rows",
    "export_selected_rows",
]
