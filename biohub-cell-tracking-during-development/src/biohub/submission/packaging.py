"""Turn per-sample prediction GEFFs into a Kaggle submission artefact.

This mirrors the organisers' ``scripts/geffs_to_csv.py`` (pinned commit
``075fc5f5a52d11077f9dc2b074644618f26939e2``) rather than inventing a format.
The one deliberate divergence is the entry point:

    upstream:  ``geffs_to_csv(in_dir, csv)``  -> dataset name = ``geff.stem``
    here:      ``write_submission_csv({dataset: geff_path}, csv)``

The upstream form is a live footgun for this repository, because the prediction
artefacts are laid out as ``<run>/<sample>/<method>.geff``.  Pointing the
upstream converter at a sample directory silently produces ``dataset`` values of
``harmonic_v1``, ``motion_gated`` and so on -- a syntactically valid submission
that scores zero.  Requiring an explicit ``{dataset: path}`` mapping makes that
mistake impossible to make by accident.

Ground truth is never read here.  The module has no ``gt``/``train`` code path
at all, and :func:`write_provenance` records that fact next to the artefact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .schema import (
    GRAPH_ROW_COLUMNS,
    ROW_TYPE_EDGE,
    ROW_TYPE_NODE,
    SENTINEL,
    SUBMISSION_COLUMNS,
    UPSTREAM_COMMIT,
    UPSTREAM_CONVERTER,
    UPSTREAM_REPOSITORY,
)


@dataclass(frozen=True)
class PackagedDataset:
    dataset: str
    geff_path: str
    n_nodes: int
    n_edges: int


def load_graph(geff_path: str | Path):
    """Load one GEFF exactly the way the upstream converter loads it."""
    import tracksdata as td

    result = td.graph.IndexedRXGraph.from_geff(Path(geff_path))
    return result[0] if isinstance(result, tuple) else result


def graph_to_rows(graph, dataset: str):
    """Flatten one graph into node rows then edge rows, in submission schema.

    Byte-compatible with the upstream ``graph_to_rows``: ``z``/``y``/``x`` are
    cast to ``Float64``, rounded to 0 decimals and cast to ``Int64``, and every
    field that does not apply to the row type is ``-1``.
    """
    import polars as pl

    nodes = graph.node_attrs().select(
        pl.lit(dataset).alias("dataset"),
        pl.lit(ROW_TYPE_NODE).alias("row_type"),
        pl.col("node_id").cast(pl.Int64),
        pl.col("t").cast(pl.Int64),
        pl.col("z").cast(pl.Float64).round(0).cast(pl.Int64),
        pl.col("y").cast(pl.Float64).round(0).cast(pl.Int64),
        pl.col("x").cast(pl.Float64).round(0).cast(pl.Int64),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("source_id"),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("target_id"),
    )
    edges = graph.edge_attrs().select(
        pl.lit(dataset).alias("dataset"),
        pl.lit(ROW_TYPE_EDGE).alias("row_type"),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("node_id"),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("t"),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("z"),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("y"),
        pl.lit(SENTINEL, dtype=pl.Int64).alias("x"),
        pl.col("source_id").cast(pl.Int64),
        pl.col("target_id").cast(pl.Int64),
    )
    return pl.concat([nodes, edges])


def write_submission_csv(
    geffs_by_dataset: Mapping[str, str | Path],
    csv_path: str | Path,
) -> tuple[Path, list[PackagedDataset]]:
    """Write ``submission.csv`` covering exactly ``geffs_by_dataset``.

    Datasets are emitted in sorted name order so the artefact is deterministic.
    Returns the CSV path and a per-dataset summary.
    """
    import polars as pl

    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    if not geffs_by_dataset:
        raise ValueError(
            "refusing to write an empty submission: no datasets were supplied. "
            "A silent empty submission is worse than a loud failure."
        )

    frames = []
    packaged: list[PackagedDataset] = []
    for dataset in sorted(geffs_by_dataset):
        geff_path = Path(geffs_by_dataset[dataset])
        if not geff_path.exists():
            raise FileNotFoundError(f"{dataset}: missing prediction GEFF {geff_path}")
        graph = load_graph(geff_path)
        n_nodes, n_edges = graph.num_nodes(), graph.num_edges()
        if n_nodes == 0:
            raise ValueError(f"{dataset}: prediction GEFF has zero nodes ({geff_path})")
        frames.append(graph_to_rows(graph, dataset))
        packaged.append(
            PackagedDataset(dataset, str(geff_path), int(n_nodes), int(n_edges))
        )

    table = pl.concat(frames).select(list(GRAPH_ROW_COLUMNS)).with_row_index("id")
    if tuple(table.columns) != SUBMISSION_COLUMNS:
        raise AssertionError(
            f"internal error: built columns {table.columns} != {SUBMISSION_COLUMNS}"
        )
    table.write_csv(csv_path)
    return csv_path, packaged


def write_provenance(
    csv_path: str | Path,
    packaged: list[PackagedDataset],
    *,
    method_id: str,
    extra: dict | None = None,
) -> Path:
    """Write ``<csv>.provenance.json`` next to the artefact.

    Records what went in, asserts no ground truth was read, and states plainly
    that the artefact has not been submitted.  Mirrors the ``ground_truth_included``
    convention already used by ``detector_fixed_race``'s ``prediction_manifest.json``.
    """
    csv_path = Path(csv_path)
    payload = {
        "artifact": csv_path.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "method_id": method_id,
        "format_source": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": UPSTREAM_COMMIT,
            "converter": UPSTREAM_CONVERTER,
        },
        "ground_truth_included": False,
        "ground_truth_inputs": [],
        "submitted_to_kaggle": False,
        "datasets": [
            {
                "dataset": p.dataset,
                "geff_path": p.geff_path,
                "nodes": p.n_nodes,
                "edges": p.n_edges,
            }
            for p in packaged
        ],
        "total_nodes": sum(p.n_nodes for p in packaged),
        "total_edges": sum(p.n_edges for p in packaged),
    }
    if extra:
        payload.update(extra)
    out = csv_path.with_suffix(csv_path.suffix + ".provenance.json")
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out


def collect_method_geffs(run_dir: str | Path, method_id: str) -> dict[str, Path]:
    """Map ``{sample: <run_dir>/<sample>/<method_id>.geff}`` for every sample present.

    This is the layout written by ``detector_fixed_race``.  Samples missing the
    requested method raise rather than being skipped, so a partial submission
    cannot be produced by accident.
    """
    run_dir = Path(run_dir)
    out: dict[str, Path] = {}
    for sample_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if sample_dir.name.endswith(".geff"):
            continue
        geff = sample_dir / f"{method_id}.geff"
        if not geff.exists():
            raise FileNotFoundError(
                f"{sample_dir.name}: no {method_id}.geff under {sample_dir}"
            )
        out[sample_dir.name] = geff
    if not out:
        raise FileNotFoundError(f"no per-sample directories under {run_dir}")
    return out


# --------------------------------------------------------------------------- #
# round-trip
# --------------------------------------------------------------------------- #


@dataclass
class RoundTripResult:
    datasets: dict[str, tuple[int, int]]
    matches_source: bool
    mismatches: list[str]


def roundtrip_from_csv(csv_path: str | Path) -> dict[str, tuple[int, int]]:
    """Reconstruct ``{dataset: (n_nodes, n_edges)}`` from the CSV alone.

    Deliberately stdlib-only, mirroring ``csv_to_geffs``'s grouping without
    depending on ``tracking_cellmot.io.save_graph`` (which is not importable
    from this repository).  The purpose is to confirm the CSV can be read back
    into per-dataset graphs with intact id references -- the property the
    scorer actually depends on.
    """
    import csv as _csv

    counts: dict[str, list[int]] = {}
    node_ids: dict[str, set[int]] = {}
    edge_refs: dict[str, list[tuple[int, int]]] = {}

    with Path(csv_path).open("r", newline="", encoding="utf-8") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            ds = row["dataset"]
            counts.setdefault(ds, [0, 0])
            node_ids.setdefault(ds, set())
            edge_refs.setdefault(ds, [])
            if row["row_type"] == ROW_TYPE_NODE:
                counts[ds][0] += 1
                node_ids[ds].add(int(row["node_id"]))
            else:
                counts[ds][1] += 1
                edge_refs[ds].append((int(row["source_id"]), int(row["target_id"])))

    for ds, refs in edge_refs.items():
        missing = [f"{s}->{t}" for s, t in refs if s not in node_ids[ds] or t not in node_ids[ds]]
        if missing:
            raise ValueError(
                f"{ds}: {len(missing)} edge(s) reference unknown node ids after "
                f"round-trip, e.g. {missing[:5]}"
            )
    return {ds: (n, e) for ds, (n, e) in counts.items()}


def check_roundtrip(csv_path: str | Path, packaged: list[PackagedDataset]) -> RoundTripResult:
    """Compare a CSV round-trip against the source graph counts."""
    got = roundtrip_from_csv(csv_path)
    mismatches: list[str] = []
    expected = {p.dataset: (p.n_nodes, p.n_edges) for p in packaged}
    for ds in sorted(set(expected) | set(got)):
        if ds not in got:
            mismatches.append(f"{ds}: absent from CSV")
        elif ds not in expected:
            mismatches.append(f"{ds}: unexpected dataset in CSV")
        elif got[ds] != expected[ds]:
            mismatches.append(f"{ds}: source {expected[ds]} != roundtrip {got[ds]}")
    return RoundTripResult(got, not mismatches, mismatches)
