"""SYNTHETIC submission fixtures.

**Everything this module produces is synthetic.**  Per ``AGENTS.md`` section 8,
synthetic data is used for unit and smoke tests only and must be labelled as
such.  Nothing here is derived from competition data, from any model, or from
ground truth, and no number produced from these fixtures may be reported as an
experimental result.  Every generated CSV carries the dataset-name prefix
``SYNTHETIC_`` so that a fixture can never be mistaken for a real submission --
and so that a fixture accidentally fed to the real coverage check fails loudly.

The fixtures exist so the validator can be exercised with no competition data
present, which is the situation this repository is actually in.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from .schema import (
    REFERENCE_SHAPE_TZYX,
    ROW_TYPE_EDGE,
    ROW_TYPE_NODE,
    SENTINEL,
    SUBMISSION_COLUMNS,
)

#: Prefix stamped onto every synthetic dataset name.
SYNTHETIC_PREFIX = "SYNTHETIC_"

SYNTHETIC_DATASETS: tuple[str, ...] = (
    f"{SYNTHETIC_PREFIX}aaaa_00000001",
    f"{SYNTHETIC_PREFIX}bbbb_00000002",
)


class _Builder:
    """Accumulates node and edge rows for one synthetic dataset."""

    def __init__(self, dataset: str) -> None:
        self.dataset = dataset
        self.nodes: list[tuple[int, int, int, int, int]] = []  # id,t,z,y,x
        self.edges: list[tuple[int, int]] = []
        self._next = 0

    def add_node(self, t: int, z: int, y: int, x: int) -> int:
        nid = self._next
        self._next += 1
        self.nodes.append((nid, t, z, y, x))
        return nid

    def add_edge(self, source: int, target: int) -> None:
        self.edges.append((source, target))

    def rows(self) -> Iterable[list]:
        for nid, t, z, y, x in self.nodes:
            yield [self.dataset, ROW_TYPE_NODE, nid, t, z, y, x, SENTINEL, SENTINEL]
        for s, t in self.edges:
            yield [
                self.dataset,
                ROW_TYPE_EDGE,
                SENTINEL,
                SENTINEL,
                SENTINEL,
                SENTINEL,
                SENTINEL,
                s,
                t,
            ]


def _track(
    builder: _Builder,
    *,
    t0: int,
    n_frames: int,
    z: int,
    y: int,
    x: int,
    step: int = 3,
) -> list[int]:
    """One straight synthetic track advancing by ``step`` voxels in x per frame."""
    ids: list[int] = []
    prev: int | None = None
    for k in range(n_frames):
        nid = builder.add_node(t0 + k, z, y, x + k * step)
        if prev is not None:
            builder.add_edge(prev, nid)
        prev = nid
        ids.append(nid)
    return ids


def build_valid_dataset(
    dataset: str,
    *,
    n_timepoints: int = 6,
    with_division: bool = True,
    shape_tzyx: tuple[int, int, int, int] = REFERENCE_SHAPE_TZYX,
) -> _Builder:
    """A structurally valid synthetic dataset: three tracks and one division."""
    _, Z, Y, X = shape_tzyx
    b = _Builder(dataset)

    _track(b, t0=0, n_frames=n_timepoints, z=Z // 4, y=Y // 4, x=8)
    _track(b, t0=0, n_frames=n_timepoints, z=Z // 2, y=Y // 2, x=20)

    if with_division:
        # Parent runs for half the movie, then forks into two daughters.
        split = max(2, n_timepoints // 2)
        parent = _track(b, t0=0, n_frames=split, z=(3 * Z) // 4, y=(3 * Y) // 4, x=40)
        last = parent[-1]
        for dy in (-6, +6):
            prev = last
            for k in range(n_timepoints - split):
                nid = b.add_node(
                    split + k, (3 * Z) // 4, (3 * Y) // 4 + dy, 40 + (split + k) * 3
                )
                b.add_edge(prev, nid)
                prev = nid
    else:
        _track(b, t0=0, n_frames=n_timepoints, z=(3 * Z) // 4, y=(3 * Y) // 4, x=40)

    return b


def write_csv(builders: Sequence[_Builder], csv_path: str | Path) -> Path:
    """Write builders to a submission CSV with the official header and ``id`` index."""
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SUBMISSION_COLUMNS)
        i = 0
        for b in builders:
            for row in b.rows():
                w.writerow([i, *row])
                i += 1
    return csv_path


def write_valid_submission(
    csv_path: str | Path,
    datasets: Sequence[str] = SYNTHETIC_DATASETS,
    *,
    with_division: bool = True,
) -> Path:
    """SYNTHETIC: a submission that should pass every validator check."""
    return write_csv(
        [build_valid_dataset(d, with_division=with_division) for d in datasets], csv_path
    )


# --------------------------------------------------------------------------- #
# deliberately broken variants, for testing the validator itself
# --------------------------------------------------------------------------- #

BREAKAGES: tuple[str, ...] = (
    "bad_header",
    "non_contiguous_id",
    "node_row_sentinel",
    "edge_row_sentinel",
    "dangling_edge",
    "self_loop",
    "duplicate_edge",
    "backward_edge",
    "duplicate_node_id",
    "micrometre_coords",
    "out_of_bounds",
    "empty_dataset_edges",
    "float_coords",
    "wide_fork",
)


def write_broken_submission(csv_path: str | Path, breakage: str) -> Path:
    """SYNTHETIC: a submission with exactly one defect of the named kind."""
    if breakage not in BREAKAGES:
        raise ValueError(f"unknown breakage {breakage!r}; expected one of {BREAKAGES}")

    csv_path = Path(csv_path)
    builders = [build_valid_dataset(d) for d in SYNTHETIC_DATASETS]
    b = builders[0]

    if breakage == "dangling_edge":
        b.add_edge(b.nodes[0][0], 999_999)
    elif breakage == "self_loop":
        b.add_edge(b.nodes[0][0], b.nodes[0][0])
    elif breakage == "duplicate_edge":
        b.add_edge(*b.edges[0])
    elif breakage == "backward_edge":
        # target sits one frame earlier than source
        src = next(n for n in b.nodes if n[1] == 3)
        tgt = next(n for n in b.nodes if n[1] == 2)
        b.add_edge(src[0], tgt[0])
    elif breakage == "duplicate_node_id":
        nid, t, z, y, x = b.nodes[0]
        b.nodes.append((nid, t + 1, z, y, x + 1))
    elif breakage == "micrometre_coords":
        # 1.625 / 0.40625 µm per voxel makes the volume look isotropic
        b.nodes = [
            (nid, t, round(z * 1.625), round(y * 0.40625), round(x * 0.40625))
            for nid, t, z, y, x in b.nodes
        ]
    elif breakage == "out_of_bounds":
        nid, t, z, y, x = b.nodes[0]
        b.nodes[0] = (nid, t, z, y, REFERENCE_SHAPE_TZYX[3] + 10)
    elif breakage == "empty_dataset_edges":
        b.edges = []
    elif breakage == "wide_fork":
        root = b.nodes[0][0]
        for dy in (10, 20, 30):
            nid = b.add_node(1, 8, 60 + dy, 30)
            b.add_edge(root, nid)

    path = write_csv(builders, csv_path)

    if breakage in ("bad_header", "non_contiguous_id", "node_row_sentinel",
                    "edge_row_sentinel", "float_coords"):
        path = _corrupt_text(path, breakage)
    return path


def _corrupt_text(path: Path, breakage: str) -> Path:
    lines = path.read_text(encoding="utf-8").splitlines()
    header, body = lines[0], lines[1:]

    if breakage == "bad_header":
        header = header.replace("source_id,target_id", "target_id,source_id")
    elif breakage == "non_contiguous_id":
        parts = body[3].split(",")
        parts[0] = str(int(parts[0]) + 100)
        body[3] = ",".join(parts)
    elif breakage == "node_row_sentinel":
        parts = body[0].split(",")
        parts[SUBMISSION_COLUMNS.index("source_id")] = "7"
        body[0] = ",".join(parts)
    elif breakage == "edge_row_sentinel":
        i = next(i for i, ln in enumerate(body) if ",edge," in ln)
        parts = body[i].split(",")
        parts[SUBMISSION_COLUMNS.index("t")] = "4"
        body[i] = ",".join(parts)
    elif breakage == "float_coords":
        parts = body[0].split(",")
        parts[SUBMISSION_COLUMNS.index("z")] = "12.5"
        body[0] = ",".join(parts)

    path.write_text("\n".join([header, *body]) + "\n", encoding="utf-8")
    return path
