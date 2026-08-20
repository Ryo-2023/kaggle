"""Detector-fixed cache contracts used by association implementations."""

from .association import (
    ASSOCIATION_METHODS,
    OFFICIAL_EDGE_THRESHOLD,
    OFFICIAL_ILP_CONFIG,
    AssociationResult,
    AssociationSpec,
    associate_from_cache,
)
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
    "ASSOCIATION_METHODS",
    "CACHE_SCHEMA_VERSION",
    "DETECTOR_CACHE_SCHEMA_VERSION",
    "OFFICIAL_EDGE_THRESHOLD",
    "OFFICIAL_ILP_CONFIG",
    "AssociationResult",
    "AssociationSpec",
    "CacheReceipt",
    "CandidateEdgeArrays",
    "CaptureConfig",
    "DetectorCache",
    "NodeArrays",
    "associate_from_cache",
    "build_detector_cache_manifest",
    "load_detector_cache",
    "materialize_detector_cache",
    "write_detector_cache",
]
