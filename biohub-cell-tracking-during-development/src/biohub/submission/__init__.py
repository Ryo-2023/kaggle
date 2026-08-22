"""Kaggle submission packaging and validation for the Biohub cell-tracking competition.

Two independent halves, deliberately kept apart:

``validator``
    Pure standard library.  Validates a candidate ``submission.csv`` with no
    competition data and no third-party packages present.  Intended to be the
    final gate of the Kaggle notebook.

``packaging``
    Needs ``polars`` and ``tracksdata``.  Turns per-sample prediction GEFFs into
    the submission artefact, mirroring the organisers' own converter.

Neither half submits anything.  Submitting to Kaggle requires the user's
explicit instruction and is not performed by any code in this package.
"""

from __future__ import annotations

from .schema import (
    COMPETITION_SLUG,
    MAX_MATCH_DISTANCE_UM,
    REFERENCE_SCALE_UM,
    REFERENCE_SHAPE_TZYX,
    SENTINEL,
    SUBMISSION_COLUMNS,
)
from .validator import (
    ERROR,
    INFO,
    WARN,
    DatasetSummary,
    Finding,
    ValidationReport,
    datasets_from_zarr_dir,
    load_ground_truth_nodes,
    validate_submission,
    write_report_json,
)

__all__ = [
    "COMPETITION_SLUG",
    "ERROR",
    "INFO",
    "MAX_MATCH_DISTANCE_UM",
    "REFERENCE_SCALE_UM",
    "REFERENCE_SHAPE_TZYX",
    "SENTINEL",
    "SUBMISSION_COLUMNS",
    "WARN",
    "DatasetSummary",
    "Finding",
    "ValidationReport",
    "datasets_from_zarr_dir",
    "load_ground_truth_nodes",
    "validate_submission",
    "write_report_json",
]
