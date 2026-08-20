"""Can the panel separate the four detector-fixed association methods at all?

Companion to ``validation_power_analysis.py`` (two-method case). This one takes
the completed four-method detector-fixed race -- all four run off one identical
detector cache, so detection really is held constant -- and asks:

  1. Is *any* pair of the four separable on the current panel?
  2. How often does a resampled panel reproduce the observed ranking?
  3. How many ground-truth edges (and therefore movies) are needed before a
     0.005 / 0.01 score difference means anything?
  4. How much of the score spread comes from the node-count adjustment term
     rather than from tracking decisions?

Inputs are the real saved counts in ``race_receipt.json``. Nothing is invented.

Pairing assumption
------------------
``race_receipt.json`` stores counts, not the per-edge matched mask, so the joint
distribution of "which method got which GT edge" is unidentified. We assume
**nested true-positive sets** (the method with more TPs recovered a superset).
That is the arrangement that *maximises* apparent separation, so every
"not separable" conclusion below is conservative. Recovering the true pairing
needs a re-run that dumps the matched-edge mask -- see QUEUED-HEAVY in the report.

Usage (inside the container)::

    python scripts/validation_power_multi.py \
        --receipt <CODEX>/artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json \
        --gt-char artifacts/validation_design/gt_characterisation.json \
        --out artifacts/validation_design/power_analysis_multi.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

import numpy as np

from validation_power_analysis import (  # noqa: F401  (same scripts/ dir)
    ADJUSTMENT_ALPHA,
    Z95,
    adjusted,
    d_score_d_recall,
    edge_jaccard,
    mcnemar_exact,
    required_gt_edges,
    resolvable_delta,
)


def min_discordant_for_alpha(alpha: float) -> int:
    """Smallest all-one-way discordant count reaching two-sided p < alpha."""
    m = 1
    while mcnemar_exact(m, 0) >= alpha:
        m += 1
        if m > 400:
            raise RuntimeError("no solution")
    return m


def nested_labels(tps: list[int], n_gt: int) -> np.ndarray:
    """(n_gt, n_methods) hit matrix with nested TP sets, ranked by TP count.

    Edge i is recovered by method m iff ``i < tp[m]`` once edges are ordered by
    "how many methods find them" -- exactly the nested arrangement.
    """
    return np.stack([(np.arange(n_gt) < tp).astype(np.int8) for tp in tps], axis=1)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--gt-char", type=Path, default=None)
    p.add_argument("--n-boot", type=int, default=200_000)
    p.add_argument("--seed", type=int, default=20260821)
    p.add_argument("--deltas", type=float, nargs="+", default=[0.005, 0.01])
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    raw_entries = json.loads(args.receipt.read_text())
    # race_receipt.json nests the counts under "metrics"; older two-method
    # metrics.json files are flat. Accept both.
    entries = [e.get("metrics", e) for e in raw_entries]
    methods = [
        e.get("method_id") or r.get("method_id") for e, r in zip(entries, raw_entries)
    ]
    tp = [int(e["edge_tp"]) for e in entries]
    fp = [int(e["edge_fp"]) for e in entries]
    fn = [int(e["edge_fn"]) for e in entries]
    ratio = [float(e["total_node_ratio"]) for e in entries]
    score = [float(e["final_score"]) for e in entries]
    n_gt = tp[0] + fn[0]
    assert all(a + b == n_gt for a, b in zip(tp, fn)), "methods disagree on GT edge count"

    # ---- how much of the spread is the node-count adjustment, not tracking? ----
    mult = [1.0 - ADJUSTMENT_ALPHA * r for r in ratio]
    raw_j = [edge_jaccard(a, b, c) for a, b, c in zip(tp, fp, fn)]
    adjustment_swing = (max(mult) - min(mult)) * float(np.mean(raw_j))

    # ---- pairwise separability -------------------------------------------------
    labels = nested_labels(tp, n_gt)
    n_pairs = len(methods) * (len(methods) - 1) // 2
    alpha_bonf = 0.05 / n_pairs
    pairs = []
    for i, j in itertools.combinations(range(len(methods)), 2):
        b = int(((labels[:, i] == 0) & (labels[:, j] == 1)).sum())
        c = int(((labels[:, i] == 1) & (labels[:, j] == 0)).sum())
        pv = mcnemar_exact(b, c)
        pairs.append(
            {
                "a": methods[i],
                "b": methods[j],
                "score_a": score[i],
                "score_b": score[j],
                "observed_score_diff": score[j] - score[i],
                "discordant_edges": b + c,
                "b_favours_b": b,
                "c_favours_a": c,
                "p_two_sided_exact": pv,
                "separable_at_0.05": pv < 0.05,
                "separable_bonferroni": pv < alpha_bonf,
            }
        )

    # ---- bootstrap the whole ranking ------------------------------------------
    rng = np.random.default_rng(args.seed)
    idx = rng.integers(0, n_gt, size=(args.n_boot, n_gt))
    hits = labels[idx]                       # (n_boot, n_gt, n_methods)
    tp_b = hits.sum(axis=1)                  # (n_boot, n_methods)
    fp_b = np.stack(
        [rng.binomial(n_gt, f / n_gt, size=args.n_boot) for f in fp], axis=1
    )
    j_b = tp_b / (n_gt + fp_b)                # denom = TP+FP+FN, FN = n_gt-TP
    s_b = np.maximum(0.0, j_b * np.asarray(mult)[None, :])

    observed_order = tuple(int(k) for k in np.argsort(-np.asarray(score)))
    boot_order = np.argsort(-s_b, axis=1)
    same_order = np.all(boot_order == np.asarray(observed_order)[None, :], axis=1)
    best = np.argmax(s_b, axis=1)
    win_prob = {methods[k]: float((best == k).mean()) for k in range(len(methods))}

    ranking = {
        "observed_ranking": [methods[k] for k in observed_order],
        "p_full_ranking_reproduced": float(same_order.mean()),
        "p_method_ranks_best": win_prob,
        "note": (
            "probability that a panel of the same size, resampled from the same "
            "50 GT edges, reproduces the ranking / picks each method as best"
        ),
    }

    # ---- panel sizing ----------------------------------------------------------
    best_i = int(np.argmax(score))
    recall = tp[best_i] / n_gt
    vp_over_gt = (tp[best_i] + fp[best_i]) / n_gt
    disc_rates = sorted({round((pr["discordant_edges"]) / n_gt, 4) for pr in pairs})
    observed_disc = float(np.mean([pr["discordant_edges"] for pr in pairs]) / n_gt)

    panels: dict[str, int] = {"dev_movie_44b6_0113de3b": n_gt}
    edges_per_movie = {}
    if args.gt_char and args.gt_char.exists():
        gt = json.loads(args.gt_char.read_text())
        panels["all_5_local_samples"] = gt["totals"]["gt_edges"]
        per = [r["gt_edges"] for r in gt["samples"]]
        edges_per_movie = {
            "samples": {r["sample_id"]: r["gt_edges"] for r in gt["samples"]},
            "mean": float(np.mean(per)),
            "median": float(np.median(per)),
            "min": int(min(per)),
            "max": int(max(per)),
        }

    sizing = {
        "reference_method": methods[best_i],
        "recall_used": recall,
        "valid_pred_over_gt": vp_over_gt,
        "d_score_d_recall": d_score_d_recall(recall, vp_over_gt),
        "pairwise_discordance_rates": disc_rates,
        "mean_pairwise_discordance": observed_disc,
        "alpha_bonferroni": alpha_bonf,
        "min_one_way_discordant_edges": {
            "p<0.05": min_discordant_for_alpha(0.05),
            f"p<{alpha_bonf:.5f} (Bonferroni over {n_pairs} pairs)": min_discordant_for_alpha(
                alpha_bonf
            ),
        },
        "requirements": {},
        "resolvable_delta_by_panel": {
            name: resolvable_delta(n, recall, observed_disc, vp_over_gt)
            for name, n in panels.items()
        },
        "panels_gt_edges": panels,
        "edges_per_movie": edges_per_movie,
    }

    for delta in args.deltas:
        req = {}
        for label, d in (
            ("optimistic_discordance_0.02", 0.02),
            (f"observed_discordance_{observed_disc:.3f}", observed_disc),
            ("pessimistic_discordance_0.10", 0.10),
        ):
            n_needed = math.ceil(required_gt_edges(delta, recall, d, vp_over_gt))
            row: dict = {"gt_edges": n_needed}
            if edges_per_movie:
                row["movies_at_local_mean_%.1f" % edges_per_movie["mean"]] = math.ceil(
                    n_needed / edges_per_movie["mean"]
                )
                row["movies_at_local_median_%.0f" % edges_per_movie["median"]] = math.ceil(
                    n_needed / edges_per_movie["median"]
                )
            req[label] = row
        sizing["requirements"][f"delta={delta}"] = req

    out = {
        "schema_version": "claude.lane_b.power_analysis_multi.v1",
        "receipt": str(args.receipt),
        "n_gt_edges": n_gt,
        "methods": [
            {
                "method_id": methods[k],
                "edge_tp": tp[k], "edge_fp": fp[k], "edge_fn": fn[k],
                "edge_jaccard": raw_j[k],
                "total_node_ratio": ratio[k],
                "node_adjustment_multiplier": mult[k],
                "final_score": score[k],
            }
            for k in range(len(methods))
        ],
        "score_spread": max(score) - min(score),
        "node_adjustment": {
            "multiplier_min": min(mult),
            "multiplier_max": max(mult),
            "score_swing_from_node_count_alone": adjustment_swing,
            "note": (
                "score movement available purely by changing how many nodes the GEFF "
                "writer emits, with identical tracking decisions"
            ),
        },
        "pairwise": pairs,
        "ranking_stability": ranking,
        "panel_sizing": sizing,
        "pairing_assumption": "nested TP sets (maximises apparent separation)",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    print(f"GT edges: {n_gt}   score spread: {out['score_spread']:.4f}")
    print(
        f"node-count adjustment alone can move score by "
        f"{adjustment_swing:.5f} (mult {min(mult):.6f}..{max(mult):.6f})"
    )
    print(f"\npairwise separability (exact McNemar, Bonferroni alpha={alpha_bonf:.5f}):")
    print(f"  {'A':<18}{'B':<18}{'dScore':>9}{'disc':>6}{'p':>9}  sep05 sepBonf")
    for pr in pairs:
        print(
            f"  {pr['a']:<18}{pr['b']:<18}{pr['observed_score_diff']:>+9.4f}"
            f"{pr['discordant_edges']:>6}{pr['p_two_sided_exact']:>9.4f}"
            f"  {str(pr['separable_at_0.05']):<6}{pr['separable_bonferroni']}"
        )
    print(
        f"\nneed >= {sizing['min_one_way_discordant_edges']['p<0.05']} one-way discordant edges "
        f"for p<0.05; >= "
        f"{list(sizing['min_one_way_discordant_edges'].values())[1]} after Bonferroni"
    )
    print(f"\nranking {ranking['observed_ranking']}")
    print(f"  P(full ranking reproduced on a resampled panel) = {ranking['p_full_ranking_reproduced']:.4f}")
    for m, v in ranking["p_method_ranks_best"].items():
        print(f"  P({m} ranks best) = {v:.4f}")
    print("\nsmallest resolvable score delta:")
    for name, val in sizing["resolvable_delta_by_panel"].items():
        print(f"  {name:<28s} n={panels[name]:>5d}  delta >= {val:.4f}")
    print("\nrequired panel size:")
    for dk, req in sizing["requirements"].items():
        print(f"  {dk}")
        for label, row in req.items():
            extra = "  ".join(f"{k}={v}" for k, v in row.items() if k != "gt_edges")
            print(f"    {label:<34s} GT edges >= {row['gt_edges']:>8,}   {extra}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
