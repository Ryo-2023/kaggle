"""Characterise ground-truth GEFF tracks for validation-split design.

GROUND-TRUTH POLICY
-------------------
This script reads ``*.geff`` ground truth. That is allowed for exactly two
purposes (see ``AGENTS.md`` / the team brief): metric evaluation, and
*metadata-only split design*. This script is the second one. It emits
**aggregate structural statistics about the annotation** (counts, track
lengths, frame coverage, displacement quantiles) and deliberately does **not**
emit per-node coordinates, so its output can never be fed back into detection,
candidate generation, association scoring, or threshold selection.

It is intentionally cheap: it opens only the tiny GEFF graph arrays
(~84 KB per sample), never the ``.zarr`` image volumes. Image-intensity
statistics are taken from an existing ``panel.json`` (quantiles already
computed by the detector-fixed panel builder) rather than re-read.

Usage (inside the container)::

    python scripts/characterise_gt_panel.py \
        --geff-dir artifacts/detector_fixed_race/panel_data/train \
        --panel-json artifacts/detector_fixed_race/panel.json \
        --out artifacts/validation_design/gt_characterisation.json
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import zarr

SPATIAL_AXES = ("z", "y", "x")


def _open_values(root: Path, rel: str) -> np.ndarray:
    return np.asarray(zarr.open_array(store=str(root / rel), mode="r")[:])


def _geff_attrs(geff_path: Path) -> dict:
    meta = json.loads((geff_path / "zarr.json").read_text())
    return meta["attributes"]["geff"]


def _quantiles(values: np.ndarray, qs=(0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0)) -> dict:
    if values.size == 0:
        return {str(q): None for q in qs}
    return {str(q): float(np.quantile(values, q)) for q in qs}


def _summary(values: np.ndarray) -> dict:
    if values.size == 0:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None, "quantiles": _quantiles(values)}
    return {
        "n": int(values.size),
        "mean": float(np.mean(values)),
        "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "quantiles": _quantiles(values),
    }


def characterise(geff_path: Path, n_frames_total: int | None = None) -> dict:
    """Return metadata-only statistics for one ground-truth GEFF graph."""
    attrs = _geff_attrs(geff_path)
    axes = {a["name"]: a for a in attrs["axes"]}
    scale = tuple(float(axes[a]["scale"]) for a in SPATIAL_AXES)

    node_ids = _open_values(geff_path, "nodes/ids")
    t = _open_values(geff_path, "nodes/props/t/values").astype(np.int64)
    coords_vox = np.stack(
        [_open_values(geff_path, f"nodes/props/{a}/values").astype(np.float64) for a in SPATIAL_AXES],
        axis=1,
    )
    coords_um = coords_vox * np.asarray(scale, dtype=np.float64)[None, :]

    edges = _open_values(geff_path, "edges/ids")
    if edges.ndim == 1:  # degenerate / empty
        edges = edges.reshape(0, 2)
    n_nodes = int(node_ids.size)
    n_edges = int(edges.shape[0])

    index_of = {int(nid): i for i, nid in enumerate(node_ids)}

    # ---- degrees, divisions, gaps -------------------------------------------------
    out_deg: Counter[int] = Counter()
    in_deg: Counter[int] = Counter()
    dt_counts: Counter[int] = Counter()
    for s, d in edges:
        out_deg[int(s)] += 1
        in_deg[int(d)] += 1
        dt_counts[int(t[index_of[int(d)]] - t[index_of[int(s)]])] += 1

    division_sources = [n for n, k in out_deg.items() if k >= 2]
    merge_targets = [n for n, k in in_deg.items() if k >= 2]

    # ---- weakly connected components = tracklets ----------------------------------
    parent = list(range(n_nodes))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for s, d in edges:
        ra, rb = find(index_of[int(s)]), find(index_of[int(d)])
        if ra != rb:
            parent[ra] = rb

    comps: dict[int, list[int]] = defaultdict(list)
    for i in range(n_nodes):
        comps[find(i)].append(i)
    tracks = list(comps.values())
    track_node_counts = np.asarray([len(c) for c in tracks], dtype=np.int64)
    track_frame_spans = np.asarray(
        [int(t[c].max() - t[c].min() + 1) for c in tracks], dtype=np.int64
    )
    # A "gap" = a track whose frame span exceeds its node count (missing frames inside).
    track_internal_gaps = np.asarray(
        [int(span - len(c)) for span, c in zip(track_frame_spans, tracks)], dtype=np.int64
    )
    singletons = int((track_node_counts == 1).sum())

    # ---- per-frame density --------------------------------------------------------
    frames_present = np.unique(t)
    per_frame_counts = np.asarray(
        [int((t == f).sum()) for f in frames_present], dtype=np.int64
    )
    t_min, t_max = int(t.min()), int(t.max())
    annotated_span = t_max - t_min + 1
    frames_missing_in_span = int(annotated_span - frames_present.size)

    # ---- inter-frame displacement (µm), consecutive-frame edges only ---------------
    disp_um: list[float] = []
    disp_um_per_axis: dict[str, list[float]] = {a: [] for a in SPATIAL_AXES}
    for s, d in edges:
        i, j = index_of[int(s)], index_of[int(d)]
        if int(t[j] - t[i]) != 1:
            continue
        delta = coords_um[j] - coords_um[i]
        disp_um.append(float(np.linalg.norm(delta)))
        for k, a in enumerate(SPATIAL_AXES):
            disp_um_per_axis[a].append(float(abs(delta[k])))
    disp = np.asarray(disp_um, dtype=np.float64)

    # ---- annotated volume extent (µm) ---------------------------------------------
    extent_um = {
        a: {
            "min": float(coords_um[:, k].min()),
            "max": float(coords_um[:, k].max()),
            "range": float(coords_um[:, k].max() - coords_um[:, k].min()),
        }
        for k, a in enumerate(SPATIAL_AXES)
    }

    n_total = attrs.get("extra", {}).get("estimated_number_of_nodes")

    return {
        "sample_id": geff_path.stem,
        "geff_version": attrs.get("geff_version"),
        "directed": attrs.get("directed"),
        "scale_um_zyx": list(scale),
        "estimated_number_of_nodes": n_total,
        "gt_nodes": n_nodes,
        "gt_edges": n_edges,
        "annotation_density_vs_estimated_nodes": (
            n_nodes / float(n_total) if n_total else None
        ),
        "divisions": {
            "n_division_sources": len(division_sources),
            "n_merge_targets": len(merge_targets),
            "max_out_degree": int(max(out_deg.values())) if out_deg else 0,
            "max_in_degree": int(max(in_deg.values())) if in_deg else 0,
        },
        "edge_dt_histogram": {str(k): int(v) for k, v in sorted(dt_counts.items())},
        "tracks": {
            "n_tracks": len(tracks),
            "n_singletons": singletons,
            "node_count": _summary(track_node_counts.astype(np.float64)),
            "frame_span": _summary(track_frame_spans.astype(np.float64)),
            "internal_gap_frames_total": int(track_internal_gaps.sum()),
            "n_tracks_with_internal_gaps": int((track_internal_gaps > 0).sum()),
        },
        "frames": {
            "n_frames_total": n_frames_total,
            "t_min": t_min,
            "t_max": t_max,
            "annotated_span": annotated_span,
            "n_frames_annotated": int(frames_present.size),
            "n_frames_missing_inside_span": frames_missing_in_span,
            "frame_coverage_of_movie": (
                frames_present.size / float(n_frames_total) if n_frames_total else None
            ),
            "nodes_per_annotated_frame": _summary(per_frame_counts.astype(np.float64)),
        },
        "displacement_um": {
            "consecutive_frame_edges": int(disp.size),
            "norm": _summary(disp),
            "per_axis_abs": {a: _summary(np.asarray(v)) for a, v in disp_um_per_axis.items()},
        },
        "extent_um": extent_um,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--geff-dir", type=Path, required=True)
    p.add_argument("--panel-json", type=Path, default=None)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    panel_by_id: dict[str, dict] = {}
    if args.panel_json and args.panel_json.exists():
        panel = json.loads(args.panel_json.read_text())
        panel_by_id = {s["sample_id"]: s for s in panel.get("samples", [])}

    rows: list[dict] = []
    for geff in sorted(args.geff_dir.glob("*.geff")):
        meta = panel_by_id.get(geff.stem, {})
        shape = meta.get("shape")
        row = characterise(geff, n_frames_total=shape[0] if shape else None)
        # Image-side facts come from panel.json (already computed); never re-read zarr.
        row["image_shape_tzyx"] = shape
        row["image_intensity_quantiles"] = meta.get("quantiles")
        row["panel_division_source_count"] = meta.get("division_source_count")
        rows.append(row)

    out: dict[str, Any] = {
        "schema_version": "claude.lane_b.gt_characterisation.v1",
        "ground_truth_use": "metadata-only split design (no coordinates emitted)",
        "geff_dir": str(args.geff_dir),
        "panel_json": str(args.panel_json) if args.panel_json else None,
        "n_samples": len(rows),
        "totals": {
            "gt_nodes": sum(r["gt_nodes"] for r in rows),
            "gt_edges": sum(r["gt_edges"] for r in rows),
            "division_sources": sum(r["divisions"]["n_division_sources"] for r in rows),
        },
        "samples": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    hdr = (
        f"{'sample':<16}{'nodes':>7}{'edges':>7}{'trk':>5}{'div':>5}"
        f"{'frm':>5}{'span':>6}{'gapF':>6}{'n/frm':>7}{'d50µm':>8}{'d95µm':>8}{'dmaxµm':>9}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        d = r["displacement_um"]["norm"]
        print(
            f"{r['sample_id']:<16}{r['gt_nodes']:>7}{r['gt_edges']:>7}"
            f"{r['tracks']['n_tracks']:>5}{r['divisions']['n_division_sources']:>5}"
            f"{r['frames']['n_frames_annotated']:>5}{r['frames']['annotated_span']:>6}"
            f"{r['frames']['n_frames_missing_inside_span']:>6}"
            f"{r['frames']['nodes_per_annotated_frame']['mean']:>7.2f}"
            f"{d['quantiles']['0.5']:>8.3f}{d['quantiles']['0.95']:>8.3f}{d['max']:>9.3f}"
        )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
