"""Executable reproducibility invariants for the detector-fixed association race.

This package exists to make comparison conditions **fail loudly** when they drift,
instead of quietly becoming incomparable.  Nothing here runs a detector, loads a
checkpoint, or reads an image ``.zarr``; every check operates on already-persisted
receipts, manifests, and small prediction ``.geff`` directories.

Three invariants are enforced rather than recorded:

``gt_guard``
    Ground truth cannot be opened without a :class:`~biohub.reproducibility.gt_guard.
    PredictionPersistedToken` that can only be minted from a prediction whose manifest
    is already on disk and whose bytes still hash to the recorded digest.

``digest``
    Independent re-implementations of the prediction-directory digest and the detector
    cache hash, so a receipt can be checked against bytes without trusting the code
    that produced it.

``receipts``
    Field-level completeness auditing: a ``metrics.json`` is only admissible when an
    accompanying receipt names source commit, checkpoint SHA-256, cache digest, device,
    command, and the code identity of the association path.
"""

from __future__ import annotations

from biohub.reproducibility.cache_identity import (
    RUN_ONLY_PROVENANCE_KEYS,
    compare_caches,
    content_input_digest,
    content_output_digest,
)
from biohub.reproducibility.digest import (
    PREDICTION_DIRECTORY_HASH_ALGORITHM,
    directory_digest,
    directory_digest_report,
    file_sha256,
    recompute_cache_hash,
)
from biohub.reproducibility.gt_guard import (
    GroundTruthOrderingError,
    PredictionPersistedToken,
    mint_prediction_token,
    open_ground_truth,
    require_token,
)
from biohub.reproducibility.receipts import (
    REQUIRED_RECEIPT_FIELDS,
    ReceiptAudit,
    audit_receipt,
    detector_invariance_report,
    method_sensitivity_report,
    prediction_manifest_candidates,
)

__all__ = [
    "PREDICTION_DIRECTORY_HASH_ALGORITHM",
    "REQUIRED_RECEIPT_FIELDS",
    "RUN_ONLY_PROVENANCE_KEYS",
    "GroundTruthOrderingError",
    "PredictionPersistedToken",
    "ReceiptAudit",
    "audit_receipt",
    "compare_caches",
    "content_input_digest",
    "content_output_digest",
    "detector_invariance_report",
    "directory_digest",
    "directory_digest_report",
    "file_sha256",
    "method_sensitivity_report",
    "mint_prediction_token",
    "open_ground_truth",
    "prediction_manifest_candidates",
    "recompute_cache_hash",
    "require_token",
]
