"""Score the sample-2 pre-registration, then pool all available samples.

Three things, in order:

1. **Score `scripts/preregister_sample2.py` against reality.** For each new sample,
   count how many of the 6 pairwise TP-orderings agree with sample 1, and compare
   that against the pre-registered H0 (2.60 of 6) and H1 (4.85-5.42) predictions.
   The pre-registration is *not* edited; it is graded.

2. **Pooled pairwise exact McNemar** across every sample, with and without a
   Bonferroni correction over the 6 pairs, plus a sensitivity sweep showing how
   many reversed edges it takes to destroy each verdict.

3. **Pooled panel scores and noise floor** -- micro-averaged edge Jaccard and the
   `summarise()`-style size-weighted adjusted Jaccard, a paired bootstrap CI on
   each pairwise score difference, and the score-level resolution of the pooled
   panel.

Counts are read from the on-disk `race_receipt.json` files, never transcribed.

Usage (inside the container)::

    python scripts/score_prereg_and_pool.py \
        --race-root <CODEX>/artifacts/detector_fixed_race \
        --prereg artifacts/validation_design/prereg_sample2.json \
        --out artifacts/validation_design/pooled_three_sample.json
"""

from __future__ import annotations

import argparse
import glob
import itertools
import json
import math
import os
from pathlib import Path

import numpy as np

from validation_power_analysis import ADJUSTMENT_ALPHA, Z95, mcnemar_exact

METHODS = ["official_ilp", "harmonic_v1", "mutual_confidence", "motion_gated"]

# Where each sample's per-method receipts live. Sample 1 has all four methods in one
# receipt; samples 2 and 3 were run per method into separate directories.
SAMPLE_GLOBS = {
    "44b6_0113de3b": ["dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json"],
    "44b6_0b24845f": ["panel_runs_0b_*/44b6_0b24845f/race_receipt.json",
                      "panel_runs/44b6_0b24845f/race_receipt.json"],
    "44b6_0c582fdc": ["panel_runs_0c_*/44b6_0c582fdc/race_receipt.json"],
}


def load_samples(root: Path) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for sample, patterns in SAMPLE_GLOBS.items():
        found: dict[str, dict] = {}
        for pat in patterns:
            for path in sorted(glob.glob(str(root / pat))):
                for entry in json.loads(Path(path).read_text()):
                    mid = entry.get("method_id")
                    if mid in found:
                        continue
                    m = entry.get("metrics", entry)
                    m["_receipt"] = os.path.relpath(path, root)
                    found[mid] = m
        missing = [m for m in METHODS if m not in found]
        if missing:
            print(f"  note: {sample} missing {missing}; skipping sample")
            continue
        out[sample] = found
    return out


def sign_agreement(tp_ref: dict[str, int], tp_new: dict[str, int]) -> tuple[int, list]:
    """How many of the 6 pairwise TP orderings match the reference sample."""
    agree, detail = 0, []
    for a, b in itertools.combinations(METHODS, 2):
        s_ref = int(np.sign(tp_ref[a] - tp_ref[b]))
        s_new = int(np.sign(tp_new[a] - tp_new[b]))
        ok = s_ref == s_new
        agree += ok
        detail.append({"pair": f"{a} vs {b}", "sign_ref": s_ref, "sign_new": s_new, "agrees": ok})
    return agree, detail


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--race-root", type=Path, required=True)
    ap.add_argument("--prereg", type=Path, default=None)
    ap.add_argument("--n-boot", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    samples = load_samples(args.race_root)
    sample_ids = list(samples)
    ref = sample_ids[0]
    tp = {s: {m: int(samples[s][m]["edge_tp"]) for m in METHODS} for s in sample_ids}
    fp = {s: {m: int(samples[s][m]["edge_fp"]) for m in METHODS} for s in sample_ids}
    fn = {s: {m: int(samples[s][m]["edge_fn"]) for m in METHODS} for s in sample_ids}
    score = {s: {m: float(samples[s][m]["final_score"]) for m in METHODS} for s in sample_ids}
    ratio = {s: {m: float(samples[s][m]["total_node_ratio"]) for m in METHODS} for s in sample_ids}
    n_gt = {s: tp[s][METHODS[0]] + fn[s][METHODS[0]] for s in sample_ids}

    # ---- 1. score the pre-registration ------------------------------------------
    prereg = json.loads(args.prereg.read_text()) if args.prereg and args.prereg.exists() else {}
    pred = prereg.get("predictions", {})
    scoring = []
    for s in sample_ids[1:]:
        agree, detail = sign_agreement(tp[ref], tp[s])
        ranking = sorted(METHODS, key=lambda m: -tp[s][m])
        ranking_ref = sorted(METHODS, key=lambda m: -tp[ref][m])
        scoring.append(
            {
                "sample_id": s,
                "n_gt_edges": n_gt[s],
                "sign_agreements_of_6": agree,
                "ranking": ranking,
                "ranking_matches_sample1": ranking == ranking_ref,
                "harmonic_minus_official_edges": tp[s]["harmonic_v1"] - tp[s]["official_ilp"],
                "detail": detail,
            }
        )
    p_all6_h0 = pred.get("H0_all_equal|independent", {}).get("p_all_6_signs_agree")
    p_all6_h1i = pred.get("H1_sample1_rates_are_true|independent", {}).get("p_all_6_signs_agree")
    p_all6_h1c = pred.get("H1_sample1_rates_are_true|comonotone", {}).get("p_all_6_signs_agree")
    n_perfect = sum(1 for r in scoring if r["sign_agreements_of_6"] == 6)
    verdict = {
        "n_new_samples": len(scoring),
        "n_with_all_6_signs_agreeing": n_perfect,
        "predicted_p_all_6_agree": {
            "H0": p_all6_h0, "H1_independent": p_all6_h1i, "H1_comonotone": p_all6_h1c,
        },
    }
    if p_all6_h0 and n_perfect == len(scoring) and len(scoring) > 0:
        verdict["joint_probability_under_H0"] = p_all6_h0 ** len(scoring)
        verdict["joint_probability_under_H1_independent"] = (p_all6_h1i or 0) ** len(scoring)
        verdict["joint_probability_under_H1_comonotone"] = (p_all6_h1c or 0) ** len(scoring)
        if verdict["joint_probability_under_H0"] > 0:
            verdict["likelihood_ratio_H1comono_over_H0"] = (
                verdict["joint_probability_under_H1_comonotone"]
                / verdict["joint_probability_under_H0"]
            )
        verdict["conclusion"] = (
            "H0 (method differences are pure noise) is rejected on the ordering statistic"
        )

    # ---- 2. pooled pairwise exact McNemar ---------------------------------------
    alpha_b = 0.05 / 6
    pairwise = []
    for a, b in itertools.combinations(METHODS, 2):
        per = []
        pooled_b = pooled_c = 0
        for s in sample_ids:
            d = tp[s][b] - tp[s][a]  # >0 means b recovered more
            per.append({"sample_id": s, "edge_gain_b_over_a": d})
            pooled_b += max(d, 0)
            pooled_c += max(-d, 0)
        pv = mcnemar_exact(pooled_b, pooled_c)
        # sensitivity: nested-TP assumes c=0 within each sample. How many reversals kill it?
        sens = []
        for extra_c in (0, 1, 2, 3):
            p_alt = mcnemar_exact(pooled_b + extra_c, pooled_c + extra_c)
            sens.append(
                {
                    "reversed_edges_added": extra_c,
                    "b": pooled_b + extra_c,
                    "c": pooled_c + extra_c,
                    "p": p_alt,
                    "sig_0.05": p_alt < 0.05,
                    "sig_bonferroni": p_alt < alpha_b,
                }
            )
        pairwise.append(
            {
                "a": a, "b": b,
                "per_sample": per,
                "pooled_b": pooled_b, "pooled_c": pooled_c,
                "p_two_sided_exact": pv,
                "sig_0.05": pv < 0.05,
                "sig_bonferroni": pv < alpha_b,
                "sensitivity_to_reversed_edges": sens,
            }
        )

    # ---- 3. pooled scores and noise floor ---------------------------------------
    tot_gt = sum(n_gt.values())
    micro = {}
    weighted = {}
    for m in METHODS:
        T = sum(tp[s][m] for s in sample_ids)
        F = sum(fp[s][m] for s in sample_ids)
        N = sum(fn[s][m] for s in sample_ids)
        micro[m] = {
            "edge_tp": T, "edge_fp": F, "edge_fn": N,
            "micro_edge_jaccard": T / (T + F + N),
        }
        w = [tp[s][m] + fp[s][m] + fn[s][m] for s in sample_ids]
        weighted[m] = {
            "weights": dict(zip(sample_ids, w)),
            "weighted_adj_edge_jaccard": sum(
                wi * score[s][m] for wi, s in zip(w, sample_ids)
            ) / sum(w),
        }

    # paired bootstrap over pooled GT edges, stratified by sample, nested-TP labels
    rng = np.random.default_rng(args.seed)
    labels = []  # (edge, method) hit matrix over the pooled panel
    for s in sample_ids:
        order = sorted(METHODS, key=lambda m: -tp[s][m])
        block = np.zeros((n_gt[s], len(METHODS)), dtype=np.int8)
        for m in METHODS:
            block[: tp[s][m], METHODS.index(m)] = 1
        # nested within sample: rank edges by how many methods find them
        labels.append(block)
        _ = order
    L = np.concatenate(labels, axis=0)
    sample_of = np.concatenate(
        [np.full(n_gt[s], i, dtype=np.int32) for i, s in enumerate(sample_ids)]
    )
    fp_rate = np.array([[fp[s][m] / n_gt[s] for m in METHODS] for s in sample_ids])
    ratio_arr = np.array([[ratio[s][m] for m in METHODS] for s in sample_ids])

    idx = rng.integers(0, L.shape[0], size=(args.n_boot, L.shape[0]))
    hits = L[idx]
    src = sample_of[idx]
    boot_scores = np.zeros((args.n_boot, len(METHODS)))
    for i, s in enumerate(sample_ids):
        mask = (src == i)
        n_i = mask.sum(axis=1)
        tp_i = (hits * mask[:, :, None]).sum(axis=1)
        fp_i = rng.binomial(np.maximum(n_i, 0)[:, None], fp_rate[i][None, :])
        denom = n_i[:, None] + fp_i
        j = np.divide(tp_i, denom, out=np.zeros_like(tp_i, dtype=float), where=denom > 0)
        adj = np.maximum(0.0, j * (1 - ADJUSTMENT_ALPHA * ratio_arr[i][None, :]))
        boot_scores += adj * n_i[:, None]  # weight by sample size, as summarise() does
    boot_scores /= L.shape[0]

    # Stratified variant: resample WITHIN each sample, holding n_i fixed. This removes
    # the movie-mixture variance and isolates edge-level noise, answering "is b better
    # than a on THESE movies" rather than "on a fresh draw of movies like these".
    # FP is a property of the prediction, not of which GT edges you happened to sample,
    # so the correct PAIRED analysis holds it fixed. Resampling it independently per
    # method (as the pooled variant above does) injects ~5.5 edges of purely artificial
    # sd into the difference -- the two methods share one detector cache, so their false
    # positives are strongly correlated, not independent. Both variants are reported.
    fp_fixed = np.array([[fp[s][m] for m in METHODS] for s in sample_ids])
    strat = np.zeros((args.n_boot, len(METHODS)))
    for i, s_id in enumerate(sample_ids):
        n_i = n_gt[s_id]
        block = L[sample_of == i]
        bidx = rng.integers(0, n_i, size=(args.n_boot, n_i))
        bh = block[bidx]
        tp_i = bh.sum(axis=1)
        fp_i = fp_fixed[i][None, :]
        j = tp_i / (n_i + fp_i)
        adj = np.maximum(0.0, j * (1 - ADJUSTMENT_ALPHA * ratio_arr[i][None, :]))
        strat += adj * n_i
    strat /= L.shape[0]

    boot_pairs = []
    for a, b in itertools.combinations(METHODS, 2):
        ds = strat[:, METHODS.index(b)] - strat[:, METHODS.index(a)]
        d = boot_scores[:, METHODS.index(b)] - boot_scores[:, METHODS.index(a)]
        boot_pairs.append(
            {
                "a": a, "b": b,
                "observed_diff": weighted[b]["weighted_adj_edge_jaccard"]
                - weighted[a]["weighted_adj_edge_jaccard"],
                "boot_mean": float(d.mean()),
                "ci95": [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))],
                "p_sign": float(2 * min((d <= 0).mean(), (d >= 0).mean())),
                "ci_excludes_zero": bool(np.quantile(d, 0.025) > 0 or np.quantile(d, 0.975) < 0),
                "paired_fp_fixed_ci95": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))],
                "paired_fp_fixed_p_sign": float(2 * min((ds <= 0).mean(), (ds >= 0).mean())),
                "paired_fp_fixed_ci_excludes_zero": bool(
                    np.quantile(ds, 0.025) > 0 or np.quantile(ds, 0.975) < 0
                ),
            }
        )

    # score-level resolution of the pooled panel
    best = max(METHODS, key=lambda m: weighted[m]["weighted_adj_edge_jaccard"])
    r = micro[best]["edge_tp"] / tot_gt
    k = (micro[best]["edge_tp"] + micro[best]["edge_fp"]) / tot_gt
    d_jdr = (1 + k) / (1 + k - r) ** 2
    disc = float(np.mean([pr["pooled_b"] + pr["pooled_c"] for pr in pairwise])) / tot_gt
    noise = {
        "pooled_gt_edges": tot_gt,
        "reference_method": best,
        "pooled_recall": r,
        "valid_pred_over_gt": k,
        "d_score_d_recall": d_jdr,
        "mean_pairwise_discordance": disc,
        "resolvable_delta": Z95 * math.sqrt(disc / tot_gt) * d_jdr,
    }

    # What the node-count adjustment costs, per sample and pooled.
    node_penalty = {"per_sample": {}, "pooled": {}}
    for s_id in sample_ids:
        node_penalty["per_sample"][s_id] = {
            m: {
                "total_node_ratio": ratio[s_id][m],
                "multiplier": 1 - ADJUSTMENT_ALPHA * ratio[s_id][m],
                "score_lost": (tp[s_id][m] / (tp[s_id][m] + fp[s_id][m] + fn[s_id][m]))
                * ADJUSTMENT_ALPHA * ratio[s_id][m],
                "prediction_node_count": samples[s_id][m]["prediction_node_count"],
            }
            for m in METHODS
        }
    for m in METHODS:
        w = [tp[s][m] + fp[s][m] + fn[s][m] for s in sample_ids]
        raw = sum(
            wi * (tp[s][m] / wi) for wi, s in zip(w, sample_ids)
        ) / sum(w)
        node_penalty["pooled"][m] = {
            "weighted_raw_edge_jaccard": raw,
            "weighted_adj_edge_jaccard": weighted[m]["weighted_adj_edge_jaccard"],
            "score_lost_to_node_penalty": raw - weighted[m]["weighted_adj_edge_jaccard"],
        }

    out = {
        "schema_version": "claude.lane_b.pooled_three_sample.v1",
        "node_penalty": node_penalty,
        "samples": {
            s: {
                "n_gt_edges": n_gt[s],
                "per_method": {
                    m: {"edge_tp": tp[s][m], "edge_fp": fp[s][m], "edge_fn": fn[s][m],
                        "final_score": score[s][m], "total_node_ratio": ratio[s][m],
                        "node_recall": samples[s][m]["node_recall"],
                        "prediction_node_count": samples[s][m]["prediction_node_count"],
                        "receipt": samples[s][m]["_receipt"]}
                    for m in METHODS
                },
            }
            for s in sample_ids
        },
        "prereg_scoring": {"per_sample": scoring, "verdict": verdict},
        "pooled_pairwise_mcnemar": pairwise,
        "alpha_bonferroni_6_pairs": alpha_b,
        "pooled_micro": micro,
        "pooled_weighted": weighted,
        "bootstrap_pairwise": boot_pairs,
        "noise_floor": noise,
        "pairing_assumption": "nested TP sets within each sample (maximises apparent separation)",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True, default=str) + "\n")

    # ---- report -----------------------------------------------------------------
    print("=" * 78)
    print("1. SCORING THE PRE-REGISTRATION (§4.6 not edited)")
    print("=" * 78)
    print(f"  pre-registered H0 mean sign agreements of 6 : "
          f"{pred.get('H0_all_equal|independent', {}).get('mean_pairwise_sign_agreements_of_6', float('nan')):.2f}")
    print(f"  pre-registered H1 mean (indep / comonotone) : "
          f"{pred.get('H1_sample1_rates_are_true|independent', {}).get('mean_pairwise_sign_agreements_of_6', float('nan')):.2f}"
          f" / {pred.get('H1_sample1_rates_are_true|comonotone', {}).get('mean_pairwise_sign_agreements_of_6', float('nan')):.2f}")
    for r_ in scoring:
        print(f"  {r_['sample_id']} (n={r_['n_gt_edges']}): "
              f"OBSERVED {r_['sign_agreements_of_6']}/6 signs agree; "
              f"ranking matches sample 1 = {r_['ranking_matches_sample1']}; "
              f"harmonic-official = {r_['harmonic_minus_official_edges']:+d} edges")
    if "joint_probability_under_H0" in verdict:
        print(f"  joint P(this outcome | H0)            = {verdict['joint_probability_under_H0']:.6f}")
        print(f"  joint P(this outcome | H1 comonotone) = {verdict['joint_probability_under_H1_comonotone']:.6f}")
        print(f"  likelihood ratio H1/H0                = {verdict['likelihood_ratio_H1comono_over_H0']:.0f}x")
        print(f"  => {verdict['conclusion']}")

    print()
    print("=" * 78)
    print(f"2. POOLED PAIRWISE EXACT McNEMAR  (n={tot_gt} GT edges, Bonferroni alpha={alpha_b:.5f})")
    print("=" * 78)
    print(f"  {'A':<18}{'B':<18}{'gains':>14}{'b':>4}{'c':>3}{'p':>11}  sig05 sigBonf")
    for pr in pairwise:
        gains = ",".join(f"{d['edge_gain_b_over_a']:+d}" for d in pr["per_sample"])
        print(f"  {pr['a']:<18}{pr['b']:<18}{gains:>14}{pr['pooled_b']:>4}{pr['pooled_c']:>3}"
              f"{pr['p_two_sided_exact']:>11.6f}  {str(pr['sig_0.05']):<6}{pr['sig_bonferroni']}")
    print("\n  sensitivity - how many reversed edges destroy each verdict:")
    for pr in pairwise:
        row = "  ".join(
            f"+{s['reversed_edges_added']}:{s['p']:.4f}{'*' if s['sig_bonferroni'] else ''}"
            for s in pr["sensitivity_to_reversed_edges"]
        )
        print(f"    {pr['a'][:9]:<10}vs {pr['b'][:9]:<10} {row}   (* = survives Bonferroni)")

    print()
    print("=" * 78)
    print(f"3. POOLED PANEL ({tot_gt} GT edges)")
    print("=" * 78)
    print(f"  {'method':<20}{'TP':>5}{'FP':>5}{'FN':>5}{'micro J':>10}{'weighted adj J':>16}")
    for m in METHODS:
        print(f"  {m:<20}{micro[m]['edge_tp']:>5}{micro[m]['edge_fp']:>5}{micro[m]['edge_fn']:>5}"
              f"{micro[m]['micro_edge_jaccard']:>10.6f}{weighted[m]['weighted_adj_edge_jaccard']:>16.6f}")
    print(f"\n  d(score)/d(recall) = {d_jdr:.4f}, mean discordance = {disc:.4f}")
    print(f"  score-level resolvable delta for this panel = {noise['resolvable_delta']:.4f}")
    print("\n  paired bootstrap on the weighted score difference:")
    print(f"    {'pair':<24}{'obs':>8}{'FP-resampled CI95':>26}{'paired, FP fixed CI95':>28}")
    for bp in boot_pairs:
        print(f"    {bp['a'][:10]+'->'+bp['b'][:10]:<24}{bp['observed_diff']:>+8.4f}"
              f"  [{bp['ci95'][0]:+.4f},{bp['ci95'][1]:+.4f}] {str(bp['ci_excludes_zero']):<6}"
              f"  [{bp['paired_fp_fixed_ci95'][0]:+.4f},{bp['paired_fp_fixed_ci95'][1]:+.4f}] "
              f"{bp['paired_fp_fixed_ci_excludes_zero']}")
    print("\n  node-count penalty (pooled, weighted):")
    print(f"    {'method':<20}{'raw J':>10}{'adj J':>10}{'lost':>9}")
    for m in METHODS:
        np_ = node_penalty["pooled"][m]
        print(f"    {m:<20}{np_['weighted_raw_edge_jaccard']:>10.6f}"
              f"{np_['weighted_adj_edge_jaccard']:>10.6f}{np_['score_lost_to_node_penalty']:>9.4f}")
    print("\n  per-sample node ratio / predicted nodes:")
    for s_id in sample_ids:
        row = "  ".join(
            f"{m[:8]}:{node_penalty['per_sample'][s_id][m]['total_node_ratio']:+.3f}"
            f"({node_penalty['per_sample'][s_id][m]['prediction_node_count']})"
            for m in METHODS
        )
        print(f"    {s_id}: {row}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
