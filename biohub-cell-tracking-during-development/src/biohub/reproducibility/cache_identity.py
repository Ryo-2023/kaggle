"""Separate *what the detector computed* from *how and when the run happened*.

``cache_hash`` is a SHA-256 over the whole detector cache manifest, and that manifest
embeds run metadata: ``provenance.elapsed_seconds`` (a wall clock),
``provenance.adapter_source_sha256`` (the hash of the capture module's source), and the
per-call counters.  So ``cache_hash`` changes whenever the machine is slower or the
capture code is refactored, even when every byte the detector produced is identical.

That makes ``cache_hash`` a **run identity**, not a content digest.  It cannot answer
the only question that matters for a detector-fixed race — *did the detector output
change?* — because it is guaranteed to differ across any refactor and across any rerun.

This module splits the manifest into three disjoint views:

``content_inputs``
    Everything that ought to determine the detector output: image bytes, checkpoint,
    detector configuration, resolved device, pinned upstream commit, array schema
    version.  Two caches with equal content inputs must produce equal content outputs.

``content_outputs``
    Digests and counts of the serialized arrays.  These are the values a
    detector-invariance claim must be made on.

``run_metadata``
    Timings, source hashes and call counters.  Useful provenance, but never part of an
    equality claim about detector output.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

#: Manifest fields that ought to fully determine the detector output.
CONTENT_INPUT_KEYS: tuple[str, ...] = (
    "schema_version",
    "sample_id",
    "image_sha256",
    "shape",
    "scale",
    "checkpoint_sha256",
    "source_repo",
    "source_commit",
)

#: ``provenance`` sub-keys that belong to the inputs rather than to run metadata.
CONTENT_INPUT_PROVENANCE_KEYS: tuple[str, ...] = (
    "detector_id",
    "device",
    "node_feature_policy",
)

#: Digests and counts of what the detector actually produced.
CONTENT_OUTPUT_KEYS: tuple[str, ...] = (
    "node_digest",
    "edge_digest",
    "node_count",
    "edge_count",
)

CONTENT_OUTPUT_PROVENANCE_KEYS: tuple[str, ...] = ("node_feature_conflict_observation_count",)

#: Fields that vary between two runs of the *same* computation.  Their presence inside
#: the hashed manifest is precisely why ``cache_hash`` cannot certify sameness.
RUN_ONLY_PROVENANCE_KEYS: frozenset[str] = frozenset(
    {
        "elapsed_seconds",
        "adapter_source_sha256",
        "detector_call_count",
        "forward_edge_call_count",
        "reverse_edge_call_count",
        "requested_device",
    }
)


def _provenance(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    provenance = manifest.get("provenance")
    return provenance if isinstance(provenance, Mapping) else {}


def content_inputs(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields that should fully determine the detector output."""

    provenance = _provenance(manifest)
    payload: dict[str, Any] = {key: manifest.get(key) for key in CONTENT_INPUT_KEYS}
    payload["detector_config"] = manifest.get("detector_config")
    for key in CONTENT_INPUT_PROVENANCE_KEYS:
        payload[f"provenance.{key}"] = provenance.get(key)
    return payload


def content_outputs(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the digests and counts of what the detector produced."""

    provenance = _provenance(manifest)
    payload: dict[str, Any] = {key: manifest.get(key) for key in CONTENT_OUTPUT_KEYS}
    artifacts = manifest.get("artifact_digests")
    payload["artifact_digests"] = dict(sorted(artifacts.items())) if isinstance(artifacts, Mapping) else None
    for key in CONTENT_OUTPUT_PROVENANCE_KEYS:
        payload[f"provenance.{key}"] = provenance.get(key)
    return payload


def run_metadata(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the run-only fields that must never enter an equality claim."""

    provenance = _provenance(manifest)
    return {key: provenance.get(key) for key in sorted(RUN_ONLY_PROVENANCE_KEYS) if key in provenance}


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def content_input_digest(manifest: Mapping[str, Any]) -> str:
    """Content-addressable identity of the detector *inputs*."""

    return _digest(content_inputs(manifest))


def content_output_digest(manifest: Mapping[str, Any]) -> str:
    """Content-addressable identity of the detector *outputs*.

    This is the value a detector-fixed claim must be made on.  It is stable across
    reruns and across output-preserving refactors of the capture code, and it moves the
    moment a single detected node or candidate edge changes.
    """

    return _digest(content_outputs(manifest))


def compare_caches(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two detector caches on inputs, outputs and run metadata separately."""

    same_inputs = content_input_digest(left) == content_input_digest(right)
    same_outputs = content_output_digest(left) == content_output_digest(right)
    return {
        "same_content_inputs": same_inputs,
        "same_content_outputs": same_outputs,
        "same_cache_hash": left.get("cache_hash") == right.get("cache_hash"),
        # The invariant: equal inputs must imply equal outputs.  A False here means the
        # detector moved under identical inputs, which invalidates any comparison that
        # spans the two caches.
        "detector_content_invariant_holds": (not same_inputs) or same_outputs,
        "differing_run_metadata": sorted(
            key
            for key in RUN_ONLY_PROVENANCE_KEYS
            if run_metadata(left).get(key) != run_metadata(right).get(key)
        ),
    }


__all__ = [
    "CONTENT_INPUT_KEYS",
    "CONTENT_INPUT_PROVENANCE_KEYS",
    "CONTENT_OUTPUT_KEYS",
    "CONTENT_OUTPUT_PROVENANCE_KEYS",
    "RUN_ONLY_PROVENANCE_KEYS",
    "compare_caches",
    "content_input_digest",
    "content_inputs",
    "content_output_digest",
    "content_outputs",
    "run_metadata",
]
