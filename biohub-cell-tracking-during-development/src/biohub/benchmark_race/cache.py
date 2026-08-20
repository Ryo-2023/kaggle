"""Deterministic, ground-truth-free cache manifests for benchmark-race runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from biohub.benchmark_race.contracts import (
    RaceRequest,
    SampleSpec,
    _contains_ground_truth,
    _normalise_json_value,
)

CACHE_SCHEMA_VERSION = "benchmark_race.cache.v1"


def _require_digest(name: str, value: str | None, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    lowered = value.lower()
    if lowered.endswith(".geff") or "ground_truth" in lowered or "groundtruth" in lowered:
        raise ValueError(f"{name} must not reference a ground-truth graph")
    return value.strip()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def build_cache_manifest(
    sample: SampleSpec | None = None,
    image_digest: str | None = None,
    detector_config: Mapping[str, Any] | None = None,
    source_commit: str | None = None,
    checkpoint_sha256: str | None = None,
    schema_version: str = CACHE_SCHEMA_VERSION,
    *,
    request: RaceRequest | None = None,
    image_sha256: str | None = None,
    checkpoint_digest: str | None = None,
) -> dict[str, Any]:
    """Build a stable manifest and cache key without opening or naming GT.

    ``image_sha256`` and ``checkpoint_digest`` are accepted as descriptive
    aliases for callers that use those names.  The returned object is a plain
    JSON-compatible mapping; it contains no timestamps or host-specific paths.
    """

    if request is not None:
        if sample is not None and sample != request.sample:
            raise ValueError("sample and request.sample disagree")
        sample = request.sample
        if detector_config is None:
            detector_config = request.config
    if sample is None:
        raise TypeError("sample is required")
    if not isinstance(sample, SampleSpec):
        raise TypeError("sample must be a SampleSpec")
    if image_digest is not None and image_sha256 is not None and image_digest != image_sha256:
        raise ValueError("image_digest and image_sha256 disagree")
    image_digest = image_digest if image_digest is not None else image_sha256
    if checkpoint_sha256 is not None and checkpoint_digest is not None and checkpoint_sha256 != checkpoint_digest:
        raise ValueError("checkpoint_sha256 and checkpoint_digest disagree")
    checkpoint_sha256 = checkpoint_sha256 if checkpoint_sha256 is not None else checkpoint_digest

    image_digest = _require_digest("image_digest", image_digest)
    source_commit = _require_digest("source_commit", source_commit)
    checkpoint_sha256 = _require_digest("checkpoint_sha256", checkpoint_sha256, allow_none=True)
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ValueError("schema_version must be a non-empty string")
    if detector_config is None:
        detector_config = {}
    if not isinstance(detector_config, Mapping):
        raise TypeError("detector_config must be a mapping")
    if _contains_ground_truth(detector_config):
        raise ValueError("detector_config must not contain a ground-truth reference")
    normalised_config = _normalise_json_value(detector_config)

    manifest: dict[str, Any] = {
        "schema_version": schema_version.strip(),
        "sample_id": sample.sample_id,
        "image_stem": sample.image_stem.as_posix(),
        "shape": list(sample.shape),
        "scale": list(sample.scale),
        "quantiles": _normalise_json_value(sample.quantiles),
        "image_digest": image_digest,
        "detector_config": normalised_config,
        "source_commit": source_commit,
        "checkpoint_sha256": checkpoint_sha256,
        "ground_truth_included": False,
    }
    manifest["cache_key"] = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    return manifest


__all__ = ["CACHE_SCHEMA_VERSION", "build_cache_manifest"]
