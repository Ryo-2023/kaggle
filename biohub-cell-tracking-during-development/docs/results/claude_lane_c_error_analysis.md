# Lane C — edge-level error analysis: official_ilp vs harmonic_v1 vs mutual_confidence vs motion_gated

Sample: `44b6_0113de3b`. Ground truth: `MAIN/data/train/44b6_0113de3b.geff` (52 nodes, 50 edges,
scale `(1.625, 0.40625, 0.40625)` µm/voxel Z/Y/X, image `(100,64,256,256)` uint16).

Predictions used: the **detector-fixed race** GEFFs supplied in the coordinator's 2026-08-21
08:00 JST addendum, all four built off one identical detector cache (hash
`0bc38739fa40d5dc38db99ec52a7ea5891849a6520d95ecbeed9bc126c6a62a8`):

```
MAIN/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/artifacts/
  detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/
    official_ilp.geff        harmonic_v1.geff
    mutual_confidence.geff   motion_gated.geff
```

This supersedes an earlier pass over the older `artifacts/strong_baseline_v1/{official_ilp,
harmonic_ilp}` GEFFs, which are not detector-fixed against each other (different node/edge
compaction) and are no longer used for this diff.

All numbers below come from running the **vendored official metric itself**
(`biohub.official_metrics.metrics.evaluate` / `_evaluate_matched_graph`, unmodified) inside
`biohub-dev`, not a reimplementation. Script:
`scratch/claude/c-viewer/.../artifacts/edge_diff_analysis.py` (+ `edge_diff_detail.py`,
`edge_diff_extra.py`), outputs saved to `artifacts/edge_diff_raw.json` /
`artifacts/edge_diff_detail.json` (gitignored; regenerate with the commands in §7).

## 1. Ground truth structure

The 50 GT edges form exactly **two disjoint lineages**, confirmed via `out_degree`/`in_degree`
on every GT node (max is 1 in both directions everywhere — **no divisions, no merges** in this
sample, consistent with `division_tp/fp/fn = 0/0/0` for all four methods):

| lineage | nodes | edges | t range |
|---|---:|---:|---|
| short track (local id `...075`) | 3 | 2 | t = 0 → 2 |
| long track (local id `...003` → `...004` at t=68→69, single non-dividing hand-off) | 49 | 48 | t = 27 → 75 |

The local-id change from `...003` to `...004` at t=68→69 is a single edge with in/out-degree 1
on both sides — an annotation-tool track-id relabel, not a division.

**This matters more than it looks**: every edge where the four methods disagree lies on the
**long track only** (see §3). With effectively one hard-to-track cell and one trivial one, the
entire method ranking on this sample is decided by how many hops of *one* 48-frame trajectory
each method recovers, not by broad tracking quality. Treat the ranking as anecdotal (n≈1
lineage), not a validated result.

## 2. Per-method summary (recomputed and cross-checked against `race_receipt.json`)

| method | pred nodes/edges | edge TP/FP/FN | node_recall | final_score |
|---|---:|---|---:|---:|
| `official_ilp` | 25,994 / 23,536 | 46/2/4 | 1.0 (52/52) | 0.8837944835 |
| `harmonic_v1` | 26,301 / 24,205 | 48/2/2 | 1.0 (52/52) | 0.9211200215 |
| `mutual_confidence` | 25,806 / 22,727 | 43/0/7 | 1.0 (52/52) | 0.8598297030 |
| `motion_gated` | 25,143 / 21,799 | 42/2/8 | **0.9808 (51/52)** | 0.8096115765 |

TP/FP/FN and `final_score` reproduced exactly from each method's own `metrics.json` via a
fresh `evaluate()` call — confirms my node/edge classification logic (below) is consistent
with the official pipeline before trusting the per-edge breakdown. `node_recall` was not
in the addendum table; **`motion_gated` is the only method that fails to node-match one GT
node** (id `40000000003`, t=29, voxel z/y/x=`(5.0, 80.0, 77.0)`) — a genuine detection-level
miss, not just an association miss (detail in §5).

## 3. Coverage of the 50 GT edges

- **39 edges**: unanimous TP, all four methods recover them.
- **1 edge**: unanimous FN — `(40000000003 → 41000000003)`, t=29→30, on the long track.
  All four methods miss it even though only `motion_gated` fails to node-match the source.
- **10 edges**: disagreement — some methods TP, some FN. **All ten are hops of the long
  track** (none on the short track, which is solved unanimously).

Full 10-edge matrix (`True`=TP, `False`=FN), plus the unanimous edges for reference:

| GT edge (source→target) | t | official_ilp | harmonic_v1 | mutual_confidence | motion_gated |
|---|---|:-:|:-:|:-:|:-:|
| 38000000003→39000000003 | 27→28 | TP | TP | FN | TP |
| 39000000003→40000000003 | 28→29 | TP | TP | TP | FN |
| 44000000003→45000000003 | 33→34 | TP | TP | TP | FN |
| 45000000003→46000000003 | 34→35 | TP | TP | TP | FN |
| **50000000003→51000000003** | **39→40** | **FN** | **TP** | FN | FN |
| **56000000003→57000000003** | **45→46** | **FN** | **TP** | FN | FN |
| 57000000003→58000000003 | 46→47 | TP | TP | TP | FN |
| 58000000003→59000000003 | 47→48 | FN | FN | FN | TP |
| 62000000003→63000000003 | 51→52 | TP | TP | FN | FN |
| 68000000003→69000000003 | 57→58 | TP | TP | FN | TP |

Row counts reconcile exactly: `official_ilp` 7/10 + 39 unanimous = 46; `harmonic_v1` 9/10 + 39
= 48; `mutual_confidence` 4/10 + 39 = 43; `motion_gated` 3/10 + 39 = 42.

## 4. THE answer: the two edges behind official_ilp → harmonic_v1 (46 → 48 TP)

Bold rows in §3 — exactly these two GT edges flip from FN to TP, nothing else changes between
`official_ilp` and `harmonic_v1` (their FN sets differ by precisely these two; their FP sets
are positionally identical, see §6).

### Edge A — GT `50000000003 → 51000000003`, t = 39 → 40

| | t | voxel (z, y, x) | matched pred node id | dist to GT (µm) |
|---|---:|---|---:|---:|
| source | 39 | (18.0, 114.0, 89.0) | official: 9216 · harmonic: 9278 | 0.9084 (**identical** both methods) |
| target | 40 | (19.0, 116.0, 92.0) | official: 9481 · harmonic: 9547 | 1.6250 (**identical** both methods) |

GT source→target physical displacement: dz=1.625, dy=0.8125, dx=1.21875 µm → **2.19 µm**
(an easy, short hop).

- `official_ilp`: no edge exists between pred nodes 9216→9481 in its selected graph → FN.
- `harmonic_v1`: edge 9278→9547 **is** selected → TP, with `edge_dist=3.9804` (raw feature,
  not physical µm), `edge_prob=0.6186`.

### Edge B — GT `56000000003 → 57000000003`, t = 45 → 46

| | t | voxel (z, y, x) | matched pred node id | dist to GT (µm) |
|---|---:|---|---:|---:|
| source | 45 | (26.0, 125.0, 98.0) | official: 10801 · harmonic: 10887 | 1.8617 (identical) |
| target | 46 | (29.0, 118.0, 107.0) | official: 11042 · harmonic: 11147 | 0.9084 (identical) |

GT source→target physical displacement: dz=4.875, dy=2.8438, dx=3.65625 µm → **6.72 µm** —
close to the `max_distance=7.0` µm node-matching radius, i.e. a genuinely fast hop for this
cell.

- `official_ilp`: no edge between pred nodes 10801→11042 → FN.
- `harmonic_v1`: edge 10887→11147 selected → TP, `edge_dist=5.6292`, `edge_prob=0.6534`.

**Reading**: in both edges, the two methods node-match the *identical physical detections*
(distance-to-GT for the matched node is exactly the same number under both methods — this is
the detector-fixed guarantee holding at the node level; only the *export id* differs, per
Addendum A2's compaction explanation). The only thing that changes is whether the ILP selects
the connecting edge. Both `harmonic_v1` edge probabilities are moderate (0.62, 0.65) — clearly
past whatever bar mattered, not overwhelming confidence. This is a real, if narrow,
association-scoring effect isolated from detection, exactly as the "detector-fixed" framing
intends — but it is **two edges**, both on one cell, at one confidence level away from going
the other way.

I do not have `official_ilp`'s score for this same candidate edge (it is absent — not merely
low-scored — from `official_ilp`'s compacted output, so its edge_prob is not retrievable from
the final GEFF; would need the retained candidate pool, see §7 QUEUED-HEAVY / for Codex).

## 5. `motion_gated`'s unique detection-level miss

`motion_gated` fails to node-match GT node `40000000003` (t=29, voxel z/y/x=`(5.0, 80.0,
77.0)`) — the other three methods match it fine. This node sits right next to the unanimous-FN
hop above (`40000000003 → 41000000003`, t=29→30) and just after a disagreement hop
(`39000000003 → 40000000003`, t=28→29, where `motion_gated` is also the only FN). So around
t=28–30 `motion_gated` has a compounding failure: it loses the node entirely, not just the
association. z=5 is near the shallow edge of the 64-plane Z stack, worth checking visually
(§8, queued).

## 6. FP edges (official=2, harmonic=2, mutual_confidence=0, motion_gated=2)

`official_ilp` and `harmonic_v1` make **the same two FP mistakes at the same physical
location**, t=47→48, right where GT edge `58000000003→59000000003` is FN for both of them:

| method | pred edge | source pos (t,z,y,x) | source→GT | target pos (t,z,y,x) | target→GT |
|---|---|---|---|---|---|
| official_ilp | 11263→11475 | (47, 31.0, 108.0, 120.0) | `58000000003` | (48, 28.0, 108.0, 116.0) | *unmatched* |
| official_ilp | 11269→11490 | (47, 33.0, 124.0, 124.0) | *unmatched* | (48, 31.0, 124.0, 116.0) | `59000000003` |
| harmonic_v1 | 11399→11626 | (47, 31.0, 108.0, 120.0) | `58000000003` | (48, 28.0, 108.0, 116.0) | *unmatched* |
| harmonic_v1 | 11406→11641 | (47, 33.0, 124.0, 124.0) | *unmatched* | (48, 31.0, 124.0, 116.0) | `59000000003` |

Positions are pixel-identical between the two methods — same underlying wrong candidates,
renumbered. True GT positions for reference: `58000000003`=(t47, 31.0,110.0,118.0),
`59000000003`=(t48, 31.0,125.0,116.0). Both methods link the right cell (58 or 59) to a
*plausible but wrong* neighboring detection instead of to each other — a local
one-cell-vs-its-neighbor confusion at exactly the hop both methods fail to solve correctly.

`mutual_confidence` has **zero** FP edges anywhere (it is precision-heavy: it never proposes a
wrong hop touching an annotated node, at the cost of recall — 43 TP vs 46/48).

`motion_gated`'s two FPs are at different positions (t=45→46 and t=34→35), consistent with its
errors being more spread out rather than concentrated at one hop.

## 7. Reproduction

```bash
# inside biohub-dev, from the biohub-cell-tracking-during-development root
uv run python - <<'PY'
import tracksdata as td
from biohub.official_metrics.metrics import evaluate, _evaluate_matched_graph
# GT:  data/train/44b6_0113de3b.geff
# Pred: scratch/strong-baseline-v1/.../artifacts/detector_fixed_race/
#        dev_full_auto_compact_timed/44b6_0113de3b/{method}.geff
# scale=(1.625, 0.40625, 0.40625), max_distance=7.0
PY
```

Full scripts used (kept for reference, not committed — regenerate as needed):
`artifacts/edge_diff_analysis.py`, `artifacts/edge_diff_detail.py`, `artifacts/edge_diff_extra.py`
in this worktree. Raw outputs: `artifacts/edge_diff_raw.json`, `artifacts/edge_diff_detail.json`.

## 8. Image evidence

QUEUED / in progress — single-`(t,z)`-plane crops (not a full-volume read) around the four
node positions in §4 plus the unanimous-FN pair in §5, using the visualizer's own
`normalize_to_uint8` / `encode_grayscale_png`. Will be added under `artifacts/*.png` with a
short visual description here once rendered and reviewed.

## 9. Caveats

- **Statistical power (restates BRIEF red flag #1, sharper now)**: this sample has exactly two
  annotated lineages, one trivial (2 edges, unanimous) and one hard (48 edges). The entire
  42→48 four-way TP spread, and the specific 46→48 official→harmonic delta, lives entirely on
  the hard lineage. This is an n≈1-cell comparison, not a general tracking-quality comparison.
  Do not generalize "harmonic beats official" beyond this one trajectory without the 5-sample
  panel.
- Division handling is untested by this sample (zero divisions in GT) — unrelated to this
  diff, already flagged in BRIEF §3.3.
- `official_ilp`'s and `harmonic_v1`'s candidate-level score for Edge A/B is not visible from
  the final compacted GEFFs (only the *selected* edges are retained); a precise "how close was
  official to picking it too" needs the candidate pool, which lives in the detector cache —
  out of scope for Lane C's resource allowance (cache-only work is Lane F's per Addendum A4).
