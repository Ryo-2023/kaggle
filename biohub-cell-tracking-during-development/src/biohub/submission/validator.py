"""Standalone validator for a candidate ``submission.csv``.

Design constraints, in priority order:

1. **Zero third-party dependencies.**  Only the Python standard library is
   imported.  The validator is meant to be the last cell of the Kaggle notebook,
   where ``polars``/``tracksdata`` may be unavailable or already unloaded, and it
   must still run when the process is nearly out of memory.
2. **Runs with no competition data present.**  Every check works from the CSV
   alone.  Optional cross-checks (expected dataset list, volume shape, ground
   truth) are opt-in arguments.
3. **Fails loudly.**  ``ERROR`` findings mean "do not submit this".  ``WARN``
   findings mean "this will score worse than you think".  Nothing is silently
   repaired.

The contract being checked is documented and sourced in :mod:`.schema`.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .schema import (
    EDGE_ONLY_FIELDS,
    MAX_SCORED_OUT_DEGREE,
    NODE_ONLY_FIELDS,
    REFERENCE_SCALE_UM,
    REFERENCE_SHAPE_TZYX,
    ROW_TYPE_NODE,
    ROW_TYPES,
    SENTINEL,
    SUBMISSION_COLUMNS,
)

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"

#: Integer-valued columns.  ``id`` is validated separately as the row index.
_INT_FIELDS: tuple[str, ...] = ("node_id", "t", "z", "y", "x", "source_id", "target_id")

#: Above this ratio of (z extent) / (max of y,x extent) the coordinates no longer
#: look like anisotropic voxel indices.  Voxel space gives ~63/252 = 0.25;
#: micrometre space gives ~102/104 = 0.98.
_ANISOTROPY_ALARM_RATIO: float = 0.60

#: A submission whose per-dataset node count is within this factor of the sparse
#: ground-truth node count is not a detector output.
_GT_SIZED_FACTOR: float = 4.0

#: Fraction of ground-truth nodes reproduced at the exact integer voxel above
#: which the submission is treated as ground-truth derived.
_GT_EXACT_MATCH_ALARM: float = 0.50

_MAX_EXAMPLES = 5


@dataclass(frozen=True)
class Finding:
    """One validation result.  ``severity`` is ``ERROR``/``WARN``/``INFO``."""

    severity: str
    code: str
    message: str
    dataset: str | None = None
    examples: tuple[str, ...] = ()

    def __str__(self) -> str:
        where = f" [{self.dataset}]" if self.dataset else ""
        tail = f"  e.g. {', '.join(self.examples)}" if self.examples else ""
        return f"{self.severity:5s} {self.code}{where}: {self.message}{tail}"


@dataclass
class DatasetSummary:
    """Per-dataset structural summary, derived only from the CSV."""

    dataset: str
    n_node_rows: int = 0
    n_edge_rows: int = 0
    t_min: int | None = None
    t_max: int | None = None
    n_timepoints_with_nodes: int = 0
    z_min: int | None = None
    z_max: int | None = None
    y_min: int | None = None
    y_max: int | None = None
    x_min: int | None = None
    x_max: int | None = None
    n_forks: int = 0
    max_out_degree: int = 0
    n_merges: int = 0
    max_in_degree: int = 0
    n_isolated_nodes: int = 0
    n_self_loops: int = 0
    n_duplicate_edges: int = 0
    n_non_adjacent_edges: int = 0
    n_backward_edges: int = 0


@dataclass
class ValidationReport:
    """Result of :func:`validate_submission`."""

    csv_path: str
    n_rows: int = 0
    datasets: dict[str, DatasetSummary] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        dataset: str | None = None,
        examples: Iterable[str] = (),
    ) -> None:
        self.findings.append(
            Finding(severity, code, message, dataset, tuple(examples)[:_MAX_EXAMPLES])
        )

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARN]

    @property
    def ok(self) -> bool:
        """True when nothing blocks submission.  Warnings do not block."""
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "csv_path": self.csv_path,
            "ok": self.ok,
            "n_rows": self.n_rows,
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "datasets": {k: asdict(v) for k, v in sorted(self.datasets.items())},
            "findings": [asdict(f) for f in self.findings],
        }

    def render(self) -> str:
        lines = [f"submission: {self.csv_path}", f"rows: {self.n_rows}"]
        for name, s in sorted(self.datasets.items()):
            lines.append(
                f"  {name}: {s.n_node_rows} nodes, {s.n_edge_rows} edges, "
                f"t=[{s.t_min},{s.t_max}], forks={s.n_forks}, merges={s.n_merges}"
            )
        lines.append("")
        if not self.findings:
            lines.append("no findings")
        for f in self.findings:
            lines.append(str(f))
        lines.append("")
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(
            f"{verdict}: {len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        )
        if self.ok:
            lines.append(
                "NOTE: a PASS means the artefact is well-formed. It is NOT permission "
                "to submit; submitting requires the user's explicit instruction."
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def _parse_int(raw: str) -> int | None:
    """Strict integer parse.  Rejects floats, blanks, NaN/Inf and whitespace."""
    if raw is None:
        return None
    s = raw.strip()
    if not s or s != raw:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _iter_rows(csv_path: Path) -> Iterator[tuple[int, list[str]]]:
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        for lineno, row in enumerate(csv.reader(fh)):
            yield lineno, row


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #


def validate_submission(
    csv_path: str | Path,
    *,
    expected_datasets: Sequence[str] | None = None,
    volume_shape_tzyx: tuple[int, int, int, int] | None = REFERENCE_SHAPE_TZYX,
    ground_truth_nodes: Mapping[str, set[tuple[int, int, int, int]]] | None = None,
    require_divisions: bool = False,
) -> ValidationReport:
    """Validate one candidate submission CSV.

    Parameters
    ----------
    csv_path
        Path to ``submission.csv``.
    expected_datasets
        The dataset names the submission must cover exactly.  When ``None``,
        coverage is reported but not enforced -- so the validator still runs
        with no test data present.
    volume_shape_tzyx
        ``(T, Z, Y, X)`` used for bounds checks.  Pass ``None`` to skip bounds
        checking entirely (hidden test movies may legitimately differ).
    ground_truth_nodes
        Optional ``{dataset: {(t, z, y, x), ...}}`` of ground-truth node voxels,
        used **only** to detect ground-truth leakage into the submission.  It
        never influences any prediction.
    require_divisions
        When True, a submission containing no predicted fork anywhere is an
        ``ERROR`` rather than a ``WARN``.
    """
    csv_path = Path(csv_path)
    report = ValidationReport(csv_path=str(csv_path))

    if not csv_path.exists():
        report.add(ERROR, "FILE_MISSING", f"{csv_path} does not exist")
        return report
    if csv_path.stat().st_size == 0:
        report.add(ERROR, "FILE_EMPTY", f"{csv_path} is zero bytes")
        return report
    if csv_path.name != "submission.csv":
        report.add(
            WARN,
            "FILE_NAME",
            f"file is named {csv_path.name!r}; Kaggle expects 'submission.csv'",
        )

    rows = _iter_rows(csv_path)
    try:
        _, header = next(rows)
    except StopIteration:
        report.add(ERROR, "FILE_EMPTY", "file has no header row")
        return report

    if tuple(header) != SUBMISSION_COLUMNS:
        report.add(
            ERROR,
            "HEADER",
            "header does not match the official schema exactly "
            f"(expected {','.join(SUBMISSION_COLUMNS)}, got {','.join(header)})",
        )
        return report

    ncols = len(SUBMISSION_COLUMNS)
    idx = {name: i for i, name in enumerate(SUBMISSION_COLUMNS)}

    # accumulators
    nodes: dict[str, dict[int, tuple[int, int, int, int]]] = defaultdict(dict)
    edges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    dup_node_ids: dict[str, list[int]] = defaultdict(list)
    bad_ids: list[str] = []
    malformed: list[str] = []
    nonint: list[str] = []
    bad_row_type: list[str] = []
    sentinel_violations_node: list[str] = []
    sentinel_violations_edge: list[str] = []
    negative_values: list[str] = []
    blank_dataset: list[str] = []

    expected_id = 0
    n_rows = 0

    for lineno, raw in rows:
        if not raw:
            continue
        n_rows += 1
        if len(raw) != ncols:
            malformed.append(f"line {lineno + 1}: {len(raw)} fields")
            continue

        row_id = _parse_int(raw[idx["id"]])
        if row_id is None or row_id != expected_id:
            bad_ids.append(f"line {lineno + 1}: id={raw[idx['id']]!r} expected {expected_id}")
        expected_id += 1

        dataset = raw[idx["dataset"]]
        if not dataset or dataset.strip() != dataset:
            blank_dataset.append(f"line {lineno + 1}: dataset={dataset!r}")
            continue

        row_type = raw[idx["row_type"]]
        if row_type not in ROW_TYPES:
            bad_row_type.append(f"line {lineno + 1}: row_type={row_type!r}")
            continue

        vals: dict[str, int] = {}
        ok = True
        for name in _INT_FIELDS:
            v = _parse_int(raw[idx[name]])
            if v is None:
                nonint.append(f"line {lineno + 1}: {name}={raw[idx[name]]!r}")
                ok = False
            else:
                vals[name] = v
        if not ok:
            continue

        summary = report.datasets.setdefault(dataset, DatasetSummary(dataset=dataset))

        if row_type == ROW_TYPE_NODE:
            summary.n_node_rows += 1
            for f in EDGE_ONLY_FIELDS:
                if vals[f] != SENTINEL:
                    sentinel_violations_node.append(f"line {lineno + 1}: {f}={vals[f]}")
            for f in NODE_ONLY_FIELDS:
                if vals[f] < 0:
                    negative_values.append(f"line {lineno + 1}: {f}={vals[f]}")
            nid = vals["node_id"]
            coord = (vals["t"], vals["z"], vals["y"], vals["x"])
            if nid in nodes[dataset]:
                dup_node_ids[dataset].append(nid)
            else:
                nodes[dataset][nid] = coord
        else:
            summary.n_edge_rows += 1
            for f in NODE_ONLY_FIELDS:
                if vals[f] != SENTINEL:
                    sentinel_violations_edge.append(f"line {lineno + 1}: {f}={vals[f]}")
            s, t = vals["source_id"], vals["target_id"]
            if s < 0 or t < 0:
                negative_values.append(f"line {lineno + 1}: source_id={s} target_id={t}")
            edges[dataset].append((s, t))

    report.n_rows = n_rows

    if n_rows == 0:
        report.add(ERROR, "NO_ROWS", "submission has a header but no data rows")
        return report

    for code, msgs, sev, text in (
        ("ROW_WIDTH", malformed, ERROR, "rows do not have exactly 10 fields"),
        ("ID_INDEX", bad_ids, ERROR, "'id' is not a 0-based contiguous row index"),
        ("DATASET_BLANK", blank_dataset, ERROR, "blank or padded dataset name"),
        ("ROW_TYPE", bad_row_type, ERROR, "row_type is not 'node' or 'edge'"),
        ("NON_INTEGER", nonint, ERROR, "non-integer value in an integer column"),
        (
            "NODE_ROW_SENTINEL",
            sentinel_violations_node,
            ERROR,
            "node rows must carry source_id=target_id=-1",
        ),
        (
            "EDGE_ROW_SENTINEL",
            sentinel_violations_edge,
            ERROR,
            "edge rows must carry node_id=t=z=y=x=-1",
        ),
        ("NEGATIVE", negative_values, ERROR, "negative value in a required field"),
    ):
        if msgs:
            report.add(sev, code, f"{len(msgs)} row(s): {text}", examples=msgs)

    for dataset, dups in dup_node_ids.items():
        if dups:
            report.add(
                ERROR,
                "NODE_ID_DUPLICATE",
                f"{len(dups)} duplicate node_id(s) within the dataset",
                dataset=dataset,
                examples=[str(d) for d in dups],
            )

    _check_graphs(report, nodes, edges, require_divisions=require_divisions)
    _check_geometry(report, nodes, volume_shape_tzyx)
    _check_coverage(report, expected_datasets)
    if ground_truth_nodes:
        check_no_ground_truth(report, nodes, ground_truth_nodes)

    return report


# --------------------------------------------------------------------------- #
# structural checks
# --------------------------------------------------------------------------- #


def _check_graphs(
    report: ValidationReport,
    nodes: Mapping[str, dict[int, tuple[int, int, int, int]]],
    edges: Mapping[str, list[tuple[int, int]]],
    *,
    require_divisions: bool,
) -> None:
    total_forks = 0

    for dataset, summary in sorted(report.datasets.items()):
        ds_nodes = nodes.get(dataset, {})
        ds_edges = edges.get(dataset, [])

        if not ds_nodes:
            report.add(ERROR, "DATASET_NO_NODES", "dataset has no node rows", dataset=dataset)
            continue
        if not ds_edges:
            report.add(
                ERROR,
                "DATASET_NO_EDGES",
                "dataset has no edge rows; edge Jaccard for it is 0",
                dataset=dataset,
            )

        ts = [c[0] for c in ds_nodes.values()]
        zs = [c[1] for c in ds_nodes.values()]
        ys = [c[2] for c in ds_nodes.values()]
        xs = [c[3] for c in ds_nodes.values()]
        summary.t_min, summary.t_max = min(ts), max(ts)
        summary.z_min, summary.z_max = min(zs), max(zs)
        summary.y_min, summary.y_max = min(ys), max(ys)
        summary.x_min, summary.x_max = min(xs), max(xs)
        summary.n_timepoints_with_nodes = len(set(ts))

        dangling: list[str] = []
        self_loops: list[str] = []
        backward: list[str] = []
        non_adjacent: list[str] = []
        seen: Counter[tuple[int, int]] = Counter()
        out_deg: Counter[int] = Counter()
        in_deg: Counter[int] = Counter()

        for s, t in ds_edges:
            if s not in ds_nodes or t not in ds_nodes:
                dangling.append(f"{s}->{t}")
                continue
            if s == t:
                self_loops.append(f"{s}->{t}")
                continue
            seen[(s, t)] += 1
            out_deg[s] += 1
            in_deg[t] += 1
            dt = ds_nodes[t][0] - ds_nodes[s][0]
            if dt <= 0:
                backward.append(f"{s}->{t} (dt={dt})")
            elif dt != 1:
                non_adjacent.append(f"{s}->{t} (dt={dt})")

        dupes = [f"{s}->{t} x{n}" for (s, t), n in seen.items() if n > 1]
        summary.n_self_loops = len(self_loops)
        summary.n_duplicate_edges = len(dupes)
        summary.n_backward_edges = len(backward)
        summary.n_non_adjacent_edges = len(non_adjacent)
        summary.max_out_degree = max(out_deg.values(), default=0)
        summary.max_in_degree = max(in_deg.values(), default=0)
        summary.n_forks = sum(1 for v in out_deg.values() if v >= 2)
        summary.n_merges = sum(1 for v in in_deg.values() if v >= 2)
        summary.n_isolated_nodes = sum(
            1 for nid in ds_nodes if out_deg[nid] == 0 and in_deg[nid] == 0
        )
        total_forks += summary.n_forks

        if dangling:
            report.add(
                ERROR,
                "EDGE_DANGLING",
                f"{len(dangling)} edge(s) reference a node_id absent from this dataset",
                dataset=dataset,
                examples=dangling,
            )
        if self_loops:
            report.add(
                ERROR, "EDGE_SELF_LOOP", f"{len(self_loops)} self-loop edge(s)",
                dataset=dataset, examples=self_loops,
            )
        if backward:
            report.add(
                ERROR,
                "EDGE_NOT_FORWARD",
                f"{len(backward)} edge(s) do not advance in time (dt <= 0)",
                dataset=dataset,
                examples=backward,
            )
        if dupes:
            report.add(
                ERROR, "EDGE_DUPLICATE", f"{len(dupes)} duplicated edge(s)",
                dataset=dataset, examples=dupes,
            )
        if non_adjacent:
            report.add(
                WARN,
                "EDGE_GAP",
                f"{len(non_adjacent)} edge(s) skip timepoints (dt != 1); the official "
                "candidate generator only builds t -> t+1 edges",
                dataset=dataset,
                examples=non_adjacent,
            )
        if summary.max_out_degree > MAX_SCORED_OUT_DEGREE:
            wide = sum(1 for v in out_deg.values() if v > MAX_SCORED_OUT_DEGREE)
            report.add(
                WARN,
                "OUT_DEGREE_TRUNCATED",
                f"{wide} node(s) have out-degree > {MAX_SCORED_OUT_DEGREE} "
                f"(max {summary.max_out_degree}); the official metric drops the "
                "surplus out-edges rather than scoring them",
                dataset=dataset,
            )
        if summary.n_merges:
            report.add(
                WARN,
                "MERGE_PRESENT",
                f"{summary.n_merges} node(s) have in-degree >= 2 (max "
                f"{summary.max_in_degree}); the official metric de-duplicates merges",
                dataset=dataset,
            )
        if summary.n_isolated_nodes:
            report.add(
                INFO,
                "ISOLATED_NODES",
                f"{summary.n_isolated_nodes} node(s) take part in no edge; they still "
                "count towards total_node_ratio and therefore lower the adjusted score",
                dataset=dataset,
            )

    if total_forks == 0:
        report.add(
            ERROR if require_divisions else WARN,
            "NO_DIVISIONS",
            "no predicted fork (out-degree >= 2) anywhere in the submission; the "
            "0.1 * division_jaccard term of the official score is forfeited",
        )


def _check_geometry(
    report: ValidationReport,
    nodes: Mapping[str, dict[int, tuple[int, int, int, int]]],
    shape: tuple[int, int, int, int] | None,
) -> None:
    for dataset, summary in sorted(report.datasets.items()):
        if not nodes.get(dataset):
            continue

        z_extent = (summary.z_max or 0) - (summary.z_min or 0)
        xy_extent = max(
            (summary.y_max or 0) - (summary.y_min or 0),
            (summary.x_max or 0) - (summary.x_min or 0),
        )
        if xy_extent > 0:
            ratio = z_extent / xy_extent
            if ratio > _ANISOTROPY_ALARM_RATIO:
                report.add(
                    ERROR,
                    "COORDS_NOT_VOXEL",
                    f"z extent / xy extent = {ratio:.2f}, which is close to isotropic. "
                    f"Voxel indices on a {REFERENCE_SHAPE_TZYX[1]}x"
                    f"{REFERENCE_SHAPE_TZYX[2]}x{REFERENCE_SHAPE_TZYX[3]} volume give "
                    f"~0.25. Coordinates in micrometres (scale {REFERENCE_SCALE_UM}) "
                    "give ~1.0, so this submission may be in the wrong units or have "
                    "permuted z/y/x. geffs_to_csv rounds to int, so micrometres are "
                    "destroyed silently.",
                    dataset=dataset,
                )

        if shape is None:
            continue
        T, Z, Y, X = shape
        oob: list[str] = []
        for nid, (t, z, y, x) in nodes[dataset].items():
            if not (0 <= t < T and 0 <= z < Z and 0 <= y < Y and 0 <= x < X):
                oob.append(f"node {nid} (t={t},z={z},y={y},x={x})")
                if len(oob) > 50:
                    break
        if oob:
            report.add(
                ERROR,
                "COORDS_OUT_OF_BOUNDS",
                f"{len(oob)}+ node(s) fall outside the assumed volume "
                f"(T,Z,Y,X)={shape}. Either the coordinates are wrong or the hidden "
                "movie has a different shape -- re-check before trusting this.",
                dataset=dataset,
                examples=oob,
            )
        if summary.n_timepoints_with_nodes < T:
            report.add(
                WARN,
                "TIMEPOINTS_MISSING",
                f"only {summary.n_timepoints_with_nodes} of {T} timepoints carry nodes",
                dataset=dataset,
            )


def _check_coverage(
    report: ValidationReport, expected_datasets: Sequence[str] | None
) -> None:
    present = set(report.datasets)
    if expected_datasets is None:
        report.add(
            INFO,
            "COVERAGE_UNCHECKED",
            f"{len(present)} dataset(s) present; no expected list supplied so "
            "per-sample completeness was NOT verified",
            examples=sorted(present),
        )
        return

    expected = set(expected_datasets)
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    if missing:
        report.add(
            ERROR,
            "DATASET_MISSING",
            f"{len(missing)} expected dataset(s) have no rows",
            examples=missing,
        )
    if extra:
        report.add(
            ERROR,
            "DATASET_UNEXPECTED",
            f"{len(extra)} dataset(s) present that are not in the expected set "
            "(a train dataset leaking into the submission looks exactly like this)",
            examples=extra,
        )
    if not missing and not extra:
        report.add(
            INFO, "COVERAGE_OK", f"all {len(expected)} expected dataset(s) present"
        )


# --------------------------------------------------------------------------- #
# ground-truth leakage
# --------------------------------------------------------------------------- #


def check_no_ground_truth(
    report: ValidationReport,
    nodes: Mapping[str, dict[int, tuple[int, int, int, int]]],
    ground_truth_nodes: Mapping[str, set[tuple[int, int, int, int]]],
) -> None:
    """Flag a submission that looks derived from ground truth.

    Ground truth is read here **only** as a leak detector.  It never reaches
    detection, association, thresholds or any hyper-parameter.

    Two independent signals, either of which is decisive:

    * *size*: this competition's ground truth is extremely sparse (tens of nodes
      per movie against tens of thousands of detections).  A submission whose
      node count is within :data:`_GT_SIZED_FACTOR` of the ground-truth node
      count is not a detector output.
    * *exact coincidence*: predicted centroids landing on the exact integer
      voxel of a ground-truth node should be rare.  A high hit rate means the
      ground truth was copied.
    """
    for dataset, gt in sorted(ground_truth_nodes.items()):
        if dataset not in report.datasets or not gt:
            continue
        pred = nodes.get(dataset, {})
        n_pred, n_gt = len(pred), len(gt)
        if n_pred == 0:
            continue

        if n_pred <= n_gt * _GT_SIZED_FACTOR:
            report.add(
                ERROR,
                "GT_SIZED_SUBMISSION",
                f"{n_pred} predicted node(s) against {n_gt} sparse ground-truth "
                f"node(s) (factor {n_pred / n_gt:.2f}). A real detector output is "
                "orders of magnitude larger; this looks ground-truth derived.",
                dataset=dataset,
            )

        pred_coords = set(pred.values())
        hits = len(gt & pred_coords)
        rate = hits / n_gt
        if rate >= _GT_EXACT_MATCH_ALARM:
            report.add(
                ERROR,
                "GT_EXACT_COINCIDENCE",
                f"{hits}/{n_gt} ({rate:.0%}) ground-truth nodes are reproduced at the "
                "exact integer voxel; independent detections do not coincide at this "
                "rate.",
                dataset=dataset,
            )
        else:
            report.add(
                INFO,
                "GT_LEAK_CHECK_PASSED",
                f"{hits}/{n_gt} ({rate:.0%}) exact ground-truth coincidences, "
                f"{n_pred} predicted nodes: consistent with an independent detector",
                dataset=dataset,
            )


def load_ground_truth_nodes(geff_dir: str | Path) -> dict[str, set[tuple[int, int, int, int]]]:
    """Read ``{dataset: {(t, z, y, x)}}`` from a directory of ground-truth GEFFs.

    Requires ``tracksdata``; kept out of the pure-stdlib validation path on
    purpose so :func:`validate_submission` still runs where it is unavailable.
    Coordinates are rounded the same way ``geffs_to_csv`` rounds them, so the
    comparison happens in submission space.
    """
    import tracksdata as td  # local import: optional dependency

    out: dict[str, set[tuple[int, int, int, int]]] = {}
    for geff in sorted(Path(geff_dir).glob("*.geff")):
        result = td.graph.IndexedRXGraph.from_geff(geff)
        graph = result[0] if isinstance(result, tuple) else result
        attrs = graph.node_attrs()
        out[geff.stem] = {
            (int(r["t"]), _round_half_even(r["z"]), _round_half_even(r["y"]), _round_half_even(r["x"]))
            for r in attrs.iter_rows(named=True)
        }
    return out


def _round_half_even(v: float) -> int:
    """Match ``polars.round(0)`` semantics closely enough for a coincidence test."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return -1
    return int(round(float(v)))


# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #


def datasets_from_zarr_dir(zarr_dir: str | Path) -> list[str]:
    """Dataset names implied by ``<dir>/*.zarr`` -- how a notebook learns the test set."""
    return sorted(p.name[: -len(".zarr")] for p in Path(zarr_dir).glob("*.zarr"))


def write_report_json(report: ValidationReport, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path
