"""Submission-format contract for the Biohub cell-tracking competition.

Everything in this module is transcribed from primary sources, not from memory:

* Column set, column order, sentinel value and the ``id`` index column come from
  the organisers' own converter
  ``scripts/geffs_to_csv.py`` in ``royerlab/kaggle-cell-tracking-competition``
  at pinned commit ``075fc5f5a52d11077f9dc2b074644618f26939e2`` (vendored
  read-only under ``artifacts/strong_baseline_v1/upstream/`` in the Codex
  worktree).
* ``max_distance = 7.0`` and the physical-scale handling come from
  ``src/biohub/official_metrics/metrics.py`` (byte-for-byte copy of the same
  upstream commit) and from ``scripts/evaluate.py``.

The single most load-bearing fact encoded here:

    **Submission coordinates are VOXEL INDICES, not micrometres.**

``geffs_to_csv.graph_to_rows`` casts ``z``/``y``/``x`` to ``Float64``, rounds to
zero decimals and casts to ``Int64``.  The evaluator (``scripts/evaluate.py``
``_read_scale``) separately reads the anisotropic voxel scale out of the
dataset's ``.zarr`` metadata and applies it when matching at 7 µm.  Writing
micrometres into the GEFF would therefore be silently destroyed by the integer
rounding -- ``x = 0.40625 µm`` becomes ``0``.
"""

from __future__ import annotations

from typing import Final

UPSTREAM_REPOSITORY: Final[str] = "royerlab/kaggle-cell-tracking-competition"
UPSTREAM_COMMIT: Final[str] = "075fc5f5a52d11077f9dc2b074644618f26939e2"
UPSTREAM_CONVERTER: Final[str] = "scripts/geffs_to_csv.py"

COMPETITION_SLUG: Final[str] = "biohub-cell-tracking-during-development"

#: Exact header of ``submission.csv``, in order.  ``id`` is prepended by
#: ``polars.DataFrame.with_row_index("id")`` in the upstream converter, so it is
#: a 0-based, contiguous, globally unique row index.
SUBMISSION_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "dataset",
    "row_type",
    "node_id",
    "t",
    "z",
    "y",
    "x",
    "source_id",
    "target_id",
)

#: Columns written by ``graph_to_rows`` before the ``id`` index is prepended.
GRAPH_ROW_COLUMNS: Final[tuple[str, ...]] = SUBMISSION_COLUMNS[1:]

#: Value used for every field that does not apply to a given ``row_type``.
SENTINEL: Final[int] = -1

ROW_TYPE_NODE: Final[str] = "node"
ROW_TYPE_EDGE: Final[str] = "edge"
ROW_TYPES: Final[frozenset[str]] = frozenset({ROW_TYPE_NODE, ROW_TYPE_EDGE})

#: Fields that must carry real values on a ``node`` row and ``SENTINEL`` on an
#: ``edge`` row.
NODE_ONLY_FIELDS: Final[tuple[str, ...]] = ("node_id", "t", "z", "y", "x")

#: Fields that must carry real values on an ``edge`` row and ``SENTINEL`` on a
#: ``node`` row.
EDGE_ONLY_FIELDS: Final[tuple[str, ...]] = ("source_id", "target_id")

#: Spatial axis order, outermost first.  The image axis order is ``(T, Z, Y, X)``
#: and the submission keeps ``z``, ``y``, ``x`` in that order.
SPATIAL_AXES: Final[tuple[str, ...]] = ("z", "y", "x")
IMAGE_AXES: Final[tuple[str, ...]] = ("t", *SPATIAL_AXES)

#: Node-matching radius used by the official metric, in micrometres.
MAX_MATCH_DISTANCE_UM: Final[float] = 7.0

#: Voxel scale (µm per voxel) along ``(z, y, x)``.  Verified identical for all
#: five panel samples; the evaluator re-reads it per dataset from the ``.zarr``
#: so this constant is only a sanity reference, never a substitute.
REFERENCE_SCALE_UM: Final[tuple[float, float, float]] = (1.625, 0.40625, 0.40625)

#: Volume shape of every movie seen so far, ``(T, Z, Y, X)``.  Verified for the
#: five-sample validation panel and consistent with the Kaggle file listing
#: (each ``.zarr`` holds exactly 100 chunk files ``0/c/<t>/0/0/0``).
#: Treated as a *default expectation*, never as a guarantee about hidden data.
REFERENCE_SHAPE_TZYX: Final[tuple[int, int, int, int]] = (100, 64, 256, 256)

#: Metric constants, from ``official_metrics/metrics.py``.
ADJUSTMENT_ALPHA: Final[float] = 0.1
SCORE_DIVISION_WEIGHT: Final[float] = 0.1

#: The official evaluator drops predicted out-edges ranked beyond the second
#: (``metrics.py`` ``_out_rank > 2``) and de-duplicates merges
#: (``_is_merge_dup``).  A predicted fork is therefore at most binary; anything
#: wider is silently truncated rather than scored.
MAX_SCORED_OUT_DEGREE: Final[int] = 2
