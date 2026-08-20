"""Statistical power of the current Biohub validation panel.

How small a ``final_score`` difference can this panel actually resolve? The
answer drives every "method A beats method B" claim in the project.

Inputs are the *real saved metric counts* (``artifacts/strong_baseline_v1/
{official_ilp,harmonic_ilp}/metrics.json``). Nothing here is simulated data:
the only modelling assumption is how the per-edge outcomes are paired between
the two methods, and that assumption is stated explicitly and chosen to be the
most generous one available to the challenger (nested true-positive sets).

What it computes
----------------
1. **Score quantum** — how much ``final_score`` moves when exactly one ground
   truth edge flips. If the claimed difference is smaller than one quantum, the
   panel physically cannot express it.
2. **Non-parametric bootstrap** over ground-truth edges — CI on each score and
   on the paired difference.
3. **Exact McNemar** on the discordant edges — the correct test for two methods
   scored on the same ground-truth edge set.
4. **Required panel size** — how many ground-truth edges are needed before a
   target score difference (default 0.005) clears the noise floor, and what
   difference the currently available panels *can* resolve.

Usage (inside the container)::

    python scripts/validation_power_analysis.py \
        --metrics-root ../../../strong-baseline-v1/biohub-cell-tracking-during-development/artifacts/strong_baseline_v1 \
        --gt-char artifacts/validation_design/gt_characterisation.json \
        --out artifacts/validation_design/power_analysis.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

ADJUSTMENT_ALPHA = 0.1
Z95 = 1.959963984540054


# ---------------------------------------------------------------------------
# metric algebra (mirrors src/biohub/official_metrics/metrics.py)
# ---------------------------------------------------------------------------
def edge_jaccard(tp: int, fp: int, fn: int) -> float:
    denom = tp + fp + fn
    return tp / denom if denom > 0 else float("nan")


def adjusted(j: float, total_node_ratio: float) -> float:
    return max(0.0, j * (1.0 - ADJUSTMENT_ALPHA * total_node_ratio))


def score_quantum(tp: int, fp: int, fn: int, ratio: float) -> dict:
    """Score change when exactly one GT edge flips from TP to FN.

    Two variants, because what happens to the predicted edge matters:
      * ``becomes_fp``  - the prediction survives but now matches nothing
        (a link error): TP-1, FP+1, FN+1.
      * ``disappears``  - the prediction is gone entirely (a miss):
        TP-1, FP+0, FN+1.
    """
    base = adjusted(edge_jaccard(tp, fp, fn), ratio)
    return {
        "base_score": base,
        "one_edge_becomes_fp": base - adjusted(edge_jaccard(tp - 1, fp + 1, fn + 1), ratio),
        "one_edge_disappears": base - adjusted(edge_jaccard(tp - 1, fp, fn + 1), ratio),
    }


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------
def paired_edge_labels(tp_a: int, tp_b: int, n_gt: int) -> np.ndarray:
    """Per-GT-edge (method_a_hit, method_b_hit) labels under nested TP sets.

    We only have aggregate counts, not the matched-edge mask, so the pairing is
    unidentified. We assume the better method's TP set *contains* the worse
    method's -- the arrangement most favourable to the challenger, i.e. it
    maximises the apparent evidence for an improvement. Any other pairing makes
    the challenger's case weaker, so conclusions of "not significant" are
    conservative.
    """
    lo, hi = (tp_a, tp_b) if tp_a <= tp_b else (tp_b, tp_a)
    both = lo
    only_hi = hi - lo
    neither = n_gt - hi
    labels = np.zeros((n_gt, 2), dtype=np.int8)
    labels[:both] = (1, 1)
    labels[both : both + only_hi] = (0, 1)
    labels[both + only_hi : both + only_hi + neither] = (0, 0)
    if tp_a > tp_b:  # swap columns back so column 0 is method a
        labels = labels[:, ::-1]
    return labels


def bootstrap(
    labels: np.ndarray,
    fp_a: int,
    fp_b: int,
    ratio_a: float,
    ratio_b: float,
    n_boot: int,
    seed: int,
) -> dict:
    """Resample GT edges with replacement; recompute both scores each draw.

    False positives are not attached to a GT edge, so they are resampled as a
    per-GT-edge Bernoulli with rate ``fp / n_gt`` -- i.e. FP volume scales with
    the panel, which is the behaviour you get from a larger annotated panel.
    """
    rng = np.random.default_rng(seed)
    n = labels.shape[0]
    idx = rng.integers(0, n, size=(n_boot, n))
    hits = labels[idx]  # (n_boot, n, 2)
    tp_a = hits[:, :, 0].sum(axis=1)
    tp_b = hits[:, :, 1].sum(axis=1)
    fpa = rng.binomial(n, fp_a / n, size=n_boot)
    fpb = rng.binomial(n, fp_b / n, size=n_boot)
    ja = tp_a / (n + fpa)  # TP + FP + FN with FN = n - TP
    jb = tp_b / (n + fpb)
    sa = np.maximum(0.0, ja * (1 - ADJUSTMENT_ALPHA * ratio_a))
    sb = np.maximum(0.0, jb * (1 - ADJUSTMENT_ALPHA * ratio_b))
    diff = sb - sa
    return {
        "n_boot": n_boot,
        "score_a": {
            "mean": float(sa.mean()),
            "sd": float(sa.std(ddof=1)),
            "ci95": [float(np.quantile(sa, 0.025)), float(np.quantile(sa, 0.975))],
        },
        "score_b": {
            "mean": float(sb.mean()),
            "sd": float(sb.std(ddof=1)),
            "ci95": [float(np.quantile(sb, 0.025)), float(np.quantile(sb, 0.975))],
        },
        "difference_b_minus_a": {
            "mean": float(diff.mean()),
            "sd": float(diff.std(ddof=1)),
            "ci95": [float(np.quantile(diff, 0.025)), float(np.quantile(diff, 0.975))],
            "p_two_sided_sign": float(2 * min((diff <= 0).mean(), (diff >= 0).mean())),
            "frac_draws_favouring_a": float((diff < 0).mean()),
            "frac_draws_tied": float((diff == 0).mean()),
        },
    }


# ---------------------------------------------------------------------------
# exact McNemar
# ---------------------------------------------------------------------------
def _binom_cdf(k: int, n: int, p: float = 0.5) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts (b, c)."""
    m = b + c
    if m == 0:
        return 1.0
    return min(1.0, 2.0 * _binom_cdf(min(b, c), m, 0.5))


def min_discordant_for_significance(alpha: float = 0.05) -> int:
    """Smallest all-one-way discordant count reaching two-sided p < alpha."""
    m = 1
    while mcnemar_exact(m, 0) >= alpha:
        m += 1
        if m > 200:
            raise RuntimeError("no solution")
    return m


# ---------------------------------------------------------------------------
# required panel size
# ---------------------------------------------------------------------------
def d_score_d_recall(recall: float, vp_over_gt: float = 1.0) -> float:
    """d(edge_jaccard)/d(recall), holding valid-prediction volume proportional.

    With ``VP = k*n`` predicted valid edges and ``TP = r*n`` over ``n`` GT edges,
    ``J = r / (1 + k - r)``, so ``dJ/dr = (1 + k) / (1 + k - r)**2``.
    """
    k = vp_over_gt
    return (1.0 + k) / (1.0 + k - recall) ** 2


def required_gt_edges(
    target_delta: float, recall: float, discordance: float, vp_over_gt: float = 1.0
) -> float:
    """GT edges needed for a paired score difference ``target_delta`` to reach p<0.05.

    Normal-approximation McNemar: ``z = (b - c)/sqrt(b + c)``. With
    ``b - c = dr * n`` and ``b + c = pi_d * n``:
        ``z = dr*sqrt(n)/sqrt(pi_d) >= 1.96``  =>  ``n >= 1.96^2 * pi_d / dr^2``.
    """
    dr = target_delta / d_score_d_recall(recall, vp_over_gt)
    return Z95**2 * discordance / dr**2


def resolvable_delta(
    n_gt: int, recall: float, discordance: float, vp_over_gt: float = 1.0
) -> float:
    """Smallest paired score difference resolvable at p<0.05 with ``n_gt`` edges."""
    dr = Z95 * math.sqrt(discordance / n_gt)
    return dr * d_score_d_recall(recall, vp_over_gt)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--metrics-root", type=Path, required=True)
    p.add_argument("--method-a", default="official_ilp")
    p.add_argument("--method-b", default="harmonic_ilp")
    p.add_argument("--gt-char", type=Path, default=None)
    p.add_argument("--target-delta", type=float, default=0.005)
    p.add_argument("--n-boot", type=int, default=200_000)
    p.add_argument("--seed", type=int, default=20260821)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    ma = json.loads((args.metrics_root / args.method_a / "metrics.json").read_text())
    mb = json.loads((args.metrics_root / args.method_b / "metrics.json").read_text())

    n_gt = ma["edge_tp"] + ma["edge_fn"]
    assert n_gt == mb["edge_tp"] + mb["edge_fn"], "methods disagree on GT edge count"

    quantum = {
        args.method_a: score_quantum(
            ma["edge_tp"], ma["edge_fp"], ma["edge_fn"], ma["total_node_ratio"]
        ),
        args.method_b: score_quantum(
            mb["edge_tp"], mb["edge_fp"], mb["edge_fn"], mb["total_node_ratio"]
        ),
    }

    labels = paired_edge_labels(ma["edge_tp"], mb["edge_tp"], n_gt)
    boot = bootstrap(
        labels,
        ma["edge_fp"],
        mb["edge_fp"],
        ma["total_node_ratio"],
        mb["total_node_ratio"],
        args.n_boot,
        args.seed,
    )

    b = int(((labels[:, 0] == 0) & (labels[:, 1] == 1)).sum())  # b favours method B
    c = int(((labels[:, 0] == 1) & (labels[:, 1] == 0)).sum())
    mcnemar = {
        "discordant_b_favours_" + args.method_b: b,
        "discordant_c_favours_" + args.method_a: c,
        "p_two_sided_exact": mcnemar_exact(b, c),
        "min_all_one_way_discordant_for_p_lt_0.05": min_discordant_for_significance(),
        "pairing_assumption": "nested TP sets (most favourable to the challenger)",
    }

    recall_b = mb["edge_tp"] / n_gt
    vp_over_gt = (mb["edge_tp"] + mb["edge_fp"]) / n_gt
    discordance = (b + c) / n_gt

    panels: dict[str, int] = {"current_dev_movie_44b6_0113de3b": n_gt}
    if args.gt_char and args.gt_char.exists():
        gt = json.loads(args.gt_char.read_text())
        panels["all_5_local_samples"] = gt["totals"]["gt_edges"]
        for row in gt["samples"]:
            panels[f"sample_{row['sample_id']}"] = row["gt_edges"]

    sizing = {
        "recall_used": recall_b,
        "valid_pred_over_gt": vp_over_gt,
        "d_score_d_recall": d_score_d_recall(recall_b, vp_over_gt),
        "observed_discordance_rate": discordance,
        "target_delta": args.target_delta,
        "required_gt_edges_for_target_delta": {
            f"discordance={d}": math.ceil(
                required_gt_edges(args.target_delta, recall_b, d, vp_over_gt)
            )
            for d in (0.01, discordance if discordance > 0 else 0.04, 0.10)
        },
        "resolvable_delta_by_panel": {
            name: resolvable_delta(n, recall_b, discordance or 0.04, vp_over_gt)
            for name, n in panels.items()
        },
        "panels_gt_edges": panels,
    }

    out = {
        "schema_version": "claude.lane_b.power_analysis.v1",
        "metrics_root": str(args.metrics_root),
        "n_gt_edges": n_gt,
        "counts": {
            args.method_a: {k: ma[k] for k in ("edge_tp", "edge_fp", "edge_fn", "total_node_ratio", "final_score")},
            args.method_b: {k: mb[k] for k in ("edge_tp", "edge_fp", "edge_fn", "total_node_ratio", "final_score")},
        },
        "observed_score_difference": mb["final_score"] - ma["final_score"],
        "score_quantum": quantum,
        "bootstrap": boot,
        "mcnemar": mcnemar,
        "panel_sizing": sizing,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    d = out["observed_score_difference"]
    print(f"GT edges                        : {n_gt}")
    print(f"observed score difference (B-A) : {d:+.6f}")
    print(
        f"one-edge quantum ({args.method_b}) : "
        f"{quantum[args.method_b]['one_edge_becomes_fp']:.6f} (link error) / "
        f"{quantum[args.method_b]['one_edge_disappears']:.6f} (miss)"
    )
    print(
        f"bootstrap 95% CI on difference  : "
        f"[{boot['difference_b_minus_a']['ci95'][0]:+.4f}, "
        f"{boot['difference_b_minus_a']['ci95'][1]:+.4f}]  "
        f"sd={boot['difference_b_minus_a']['sd']:.4f}"
    )
    print(f"  draws favouring {args.method_a:<14s}: {boot['difference_b_minus_a']['frac_draws_favouring_a']:.3f}")
    print(f"exact McNemar b={b} c={c}         : p = {mcnemar['p_two_sided_exact']:.4f}")
    print(f"  need >= {mcnemar['min_all_one_way_discordant_for_p_lt_0.05']} one-way discordant edges for p<0.05")
    print(f"dScore/dRecall                  : {sizing['d_score_d_recall']:.4f}")
    print(f"\nGT edges required for delta={args.target_delta} at p<0.05:")
    for k, v in sizing["required_gt_edges_for_target_delta"].items():
        print(f"  {k:<24s} n >= {v:,}")
    print("\nSmallest resolvable score delta per panel:")
    for name, val in sizing["resolvable_delta_by_panel"].items():
        print(f"  {name:<34s} n={panels[name]:>5d}  delta >= {val:.4f}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
