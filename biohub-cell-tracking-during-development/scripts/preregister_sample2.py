"""PRE-REGISTRATION: what sample 2 must show, written before the numbers exist.

Codex is running the four-method association race on the second panel sample
``44b6_0b24845f`` (51 GT nodes / 49 GT edges) right now. This script fixes, in
advance, what each hypothesis predicts, so the result can be *tested* instead of
narrated. A prediction written after seeing the data is worth nothing.

Hypotheses
----------
``H0`` -- the sample-1 method differences are sampling noise. All four methods
         share one true edge recall, the pooled sample-1 rate.
``H1`` -- the sample-1 differences are real. Each method's true edge recall is
         its observed sample-1 rate.

Both are simulated on ``n2`` GT edges under two correlation regimes, which
bracket reality:

``independent``  -- each method draws its own Binomial(n2, p_m). Methods share a
                    detector cache, so this *understates* their correlation and
                    therefore overstates how often rankings shuffle.
``comonotone``   -- one shared per-edge difficulty ranks all methods identically
                    (the nested model used elsewhere in this lane). This is the
                    maximum-correlation extreme and overstates ranking stability.

The truth lies between. Reporting both is the honest form.

Usage (inside the container)::

    python scripts/preregister_sample2.py \
        --receipt <CODEX>/.../44b6_0113de3b/race_receipt.json \
        --n2 49 --out artifacts/validation_design/prereg_sample2.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from validation_power_analysis import mcnemar_exact  # same scripts/ dir


def simulate(p: np.ndarray, n2: int, mode: str, n_sim: int, rng) -> np.ndarray:
    """Return (n_sim, n_methods) simulated TP counts on ``n2`` GT edges."""
    k = p.size
    if mode == "independent":
        return rng.binomial(n2, p[None, :], size=(n_sim, k))
    if mode == "comonotone":
        # one shared uniform difficulty per edge; method m recovers edge i iff u_i < p_m
        u = rng.random((n_sim, n2, 1))
        return (u < p[None, None, :]).sum(axis=1)
    raise ValueError(mode)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--receipt", type=Path, required=True, help="sample-1 race receipt")
    ap.add_argument("--n2", type=int, default=49, help="GT edges in sample 2")
    ap.add_argument("--n-sim", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    raw = json.loads(args.receipt.read_text())
    entries = [e.get("metrics", e) for e in raw]
    methods = [e.get("method_id") or r.get("method_id") for e, r in zip(entries, raw)]
    tp1 = np.array([int(e["edge_tp"]) for e in entries])
    fn1 = np.array([int(e["edge_fn"]) for e in entries])
    n1 = int(tp1[0] + fn1[0])
    order1 = np.argsort(-tp1)  # sample-1 ranking, best first

    p_h1 = tp1 / n1
    p_h0 = np.full_like(p_h1, tp1.sum() / (n1 * len(methods)))

    rng = np.random.default_rng(args.seed)
    results: dict = {}
    for hyp, p in (("H0_all_equal", p_h0), ("H1_sample1_rates_are_true", p_h1)):
        for mode in ("independent", "comonotone"):
            tp2 = simulate(p, args.n2, mode, args.n_sim, rng)
            ranks = np.argsort(-tp2, axis=1)
            same_rank = np.all(ranks == order1[None, :], axis=1).mean()
            first = ranks[:, 0]
            # sign agreement across the 6 pairs, vs sample 1
            agree = np.zeros(args.n_sim)
            npairs = 0
            for i in range(len(methods)):
                for j in range(i + 1, len(methods)):
                    s1 = np.sign(tp1[i] - tp1[j])
                    s2 = np.sign(tp2[:, i] - tp2[:, j])
                    agree += (s2 == s1) | ((s2 == 0) & (s1 == 0))
                    npairs += 1
            best_i, off_i = int(order1[0]), int(np.where(np.array(methods) == "official_ilp")[0][0])
            d_edges = tp2[:, best_i] - tp2[:, off_i]
            results[f"{hyp}|{mode}"] = {
                "true_recalls": {m: float(v) for m, v in zip(methods, p)},
                "p_same_ranking_as_sample1": float(same_rank),
                "p_ranks_first": {
                    m: float((first == k).mean()) for k, m in enumerate(methods)
                },
                "mean_pairwise_sign_agreements_of_6": float(agree.mean()),
                "p_all_6_signs_agree": float((agree == npairs).mean()),
                "tp_95pct_interval": {
                    m: [int(np.quantile(tp2[:, k], 0.025)), int(np.quantile(tp2[:, k], 0.975))]
                    for k, m in enumerate(methods)
                },
                "best_minus_official_edges": {
                    "mean": float(d_edges.mean()),
                    "p_positive": float((d_edges > 0).mean()),
                    "p_zero_or_negative": float((d_edges <= 0).mean()),
                    "q95": [float(np.quantile(d_edges, 0.025)), float(np.quantile(d_edges, 0.975))],
                },
            }

    # --- what a two-sample agreement would actually license -------------------
    pooled = []
    for per_sample_b in (1, 2, 3):
        for n_samples in (1, 2, 3, 4, 5):
            b = per_sample_b * n_samples
            pooled.append(
                {
                    "edges_gained_per_sample": per_sample_b,
                    "n_samples_agreeing": n_samples,
                    "pooled_b": b,
                    "pooled_c": 0,
                    "exact_mcnemar_p": mcnemar_exact(b, 0),
                    "significant_0.05": mcnemar_exact(b, 0) < 0.05,
                    "significant_bonferroni_6pairs": mcnemar_exact(b, 0) < 0.05 / 6,
                }
            )

    n_pooled = n1 + args.n2
    Z95 = 1.959963984540054
    d_jdr = 1.8491
    pi_d = 0.070
    out = {
        "schema_version": "claude.lane_b.prereg_sample2.v1",
        "written_before_sample2_results_existed": True,
        "sample1": {
            "sample_id": "44b6_0113de3b",
            "n_gt_edges": n1,
            "edge_tp": {m: int(v) for m, v in zip(methods, tp1)},
            "ranking": [methods[k] for k in order1],
        },
        "sample2": {"sample_id": "44b6_0b24845f", "n_gt_edges": args.n2},
        "predictions": results,
        "pooled_two_sample_panel": {
            "n_gt_edges": n_pooled,
            "resolvable_delta": Z95 * math.sqrt(pi_d / n_pooled) * d_jdr,
            "note": "pooling samples 1 and 2 still cannot resolve the 0.0373 claim",
        },
        "what_agreement_licenses": pooled,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    print(f"sample 1: n={n1} TP={dict(zip(methods, tp1.tolist()))}")
    print(f"sample 2: n={args.n2} (44b6_0b24845f) -- predictions below\n")
    for key, r in results.items():
        hyp, mode = key.split("|")
        print(f"[{hyp} / {mode}]")
        print(f"  P(same ranking as sample 1)      = {r['p_same_ranking_as_sample1']:.4f}")
        print(f"  P(all 6 pairwise signs agree)    = {r['p_all_6_signs_agree']:.4f}")
        print(f"  mean sign agreements (of 6)      = {r['mean_pairwise_sign_agreements_of_6']:.2f}")
        print(f"  P(harmonic_v1 ranks first)       = {r['p_ranks_first'].get('harmonic_v1', float('nan')):.4f}")
        d = r["best_minus_official_edges"]
        print(f"  harmonic-official edge gain      = {d['mean']:+.2f}  P(>0)={d['p_positive']:.3f}  P(<=0)={d['p_zero_or_negative']:.3f}")
        print(f"  predicted TP 95% intervals       = " + ", ".join(
            f"{m}:{lo}-{hi}" for m, (lo, hi) in r["tp_95pct_interval"].items()))
        print()
    print(f"pooled panel (samples 1+2) = {n_pooled} GT edges, resolvable delta = "
          f"{out['pooled_two_sample_panel']['resolvable_delta']:.4f}")
    print("\nwhat an agreement would license (harmonic ahead by k edges on each of N samples):")
    print(f"  {'k/sample':>9}{'N':>3}{'pooled b':>10}{'exact p':>10}  sig05  sigBonf")
    for row in pooled:
        if row["edges_gained_per_sample"] == 2 or row["n_samples_agreeing"] <= 3:
            print(f"  {row['edges_gained_per_sample']:>9}{row['n_samples_agreeing']:>3}"
                  f"{row['pooled_b']:>10}{row['exact_mcnemar_p']:>10.4f}"
                  f"  {str(row['significant_0.05']):<6}{row['significant_bonferroni_6pairs']}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
