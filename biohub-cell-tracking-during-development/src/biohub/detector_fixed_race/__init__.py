"""Detector-fixed cache contracts used by association implementations."""

from .cache import (
    CACHE_SCHEMA_VERSION,
    DETECTOR_CACHE_SCHEMA_VERSION,
    build_detector_cache_manifest,
    load_detector_cache,
    write_detector_cache,
)
from .schema import CacheReceipt, CandidateEdgeArrays, DetectorCache, NodeArrays
from .upstream_adapter import CaptureConfig, materialize_detector_cache

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "DETECTOR_CACHE_SCHEMA_VERSION",
    "CacheReceipt",
    "CandidateEdgeArrays",
    "CaptureConfig",
    "DetectorCache",
    "NodeArrays",
    "build_detector_cache_manifest",
    "load_detector_cache",
    "materialize_detector_cache",
    "write_detector_cache",
]
