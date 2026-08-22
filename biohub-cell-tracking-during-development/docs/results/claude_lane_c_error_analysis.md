# Lane C — edge-level error analysis: official_ilp vs harmonic_v1 vs mutual_confidence vs motion_gated

Primary sample (§1–§9): `44b6_0113de3b`. A second sample, `44b6_0c582fdc`, is checked in §10
once its four-method race finished — same method, same conclusion recurs. Ground truth for the
primary sample: `MAIN/data/train/44b6_0113de3b.geff` (52 nodes, 50 edges, scale
`(1.625, 0.40625, 0.40625)` µm/voxel Z/Y/X, image `(100,64,256,256)` uint16).

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

Rendered as single-`(t,z)`-plane crops (96×96 px windows, not a full-volume read — the same
lazy `arr[t, z, :, :]` access pattern the visualizer's own `frame_png` uses) around the GT
positions in §4 and §5, using the visualizer's own `normalize_to_uint8` /
`encode_grayscale_png` with the volume's global 0.1%/99.9% intensity quantiles
(`low=26.22, high=2145.0`, from the zarr's `image_statistics` attrs) so every crop uses
identical, comparable contrast. A crosshair marks the exact annotated `(y, x)` voxel.
Saved under `artifacts/*_marked.png` (gitignored; unmarked originals alongside as
`artifacts/*.png` without the `_marked` suffix):

| file | GT node | t | z | what's visible |
|---|---|---:|---:|---|
| `edge1_src_t39_z18_marked.png` | 50000000003 | 39 | 18 | crosshair sits on a **dim, low-contrast** blob at the edge of a faint patch; 2-3 visibly **brighter** nuclei sit within ~10 px (~4 µm) |
| `edge1_tgt_t40_z19_marked.png` | 51000000003 | 40 | 19 | same pattern one frame later: dim target blob, brighter neighbors nearby |
| `edge2_src_t45_z26_marked.png` | 56000000003 | 45 | 26 | crosshair on a moderate-brightness blob with a similarly-bright neighbor immediately to its right — a close, ambiguous distractor |
| `edge2_tgt_t46_z29_marked.png` | 57000000003 | 46 | 29 | crosshair in a comparatively dim area between two brighter blobs |
| `unanimousFN_src_t29_z5_marked.png` | 40000000003 | 29 | 5 | crosshair on a dim region; a distinctly brighter blob sits just up/right — this is also the node `motion_gated` fails to node-match at all (§5) |
| `unanimousFN_tgt_t30_z7_marked.png` | 41000000003 | 30 | 7 | same pattern: dim target, brighter neighbor offset nearby |

**Reading**: across all six crops (three consecutive hops of the one hard lineage), the
annotated cell is consistently the **dimmer** of several visually similar, closely-packed
nuclei, never the single obviously-brightest blob in its neighborhood. This is a plausible,
visually-grounded explanation for why this specific lineage — and not the trivial 2-edge
track — is the one every method struggles with: a low-SNR nucleus in a crowded field is
exactly where small differences in edge-scoring (forward-only vs. harmonic's forward+reverse
fusion) can plausibly tip the ILP's choice, and where a purely motion-gated heuristic can lose
the node entirely (§5). This reading is descriptive, not quantitative — I did not measure
neighbor brightness numerically, only inspected the rendered crops.

## 9. Caveats

- **Statistical power (restates BRIEF red flag #1, sharper now)**: this sample has exactly two
  annotated lineages, one trivial (2 edges, unanimous) and one hard (48 edges). The entire
  42→48 four-way TP spread, and the specific 46→48 official→harmonic delta, lives entirely on
  the hard lineage. This is an n≈1-cell comparison, not a general tracking-quality comparison.
  Do not generalize "harmonic beats official" beyond this one trajectory without the 5-sample
  panel. **§10 checks this on a second sample and the same structural problem recurs.**
- Division handling is untested by this sample (zero divisions in GT) — unrelated to this
  diff, already flagged in BRIEF §3.3.
- `official_ilp`'s and `harmonic_v1`'s candidate-level score for Edge A/B is not visible from
  the final compacted GEFFs (only the *selected* edges are retained); a precise "how close was
  official to picking it too" needs the candidate pool, which lives in the detector cache —
  out of scope for Lane C's resource allowance (cache-only work is Lane F's per Addendum A4).

## 10. Second sample: `44b6_0c582fdc` — does the "one hard lineage" pattern generalize?

Per the coordinator's follow-up (2026-08-21 13:20 JST): Codex finished the same four-method
race on two more panel samples. `44b6_0c582fdc` is the most interesting — harmonic gets a
5-edge TP gain (57→62) versus the dev sample's 2-edge gain (46→48), so if the effect is
general it should be much more visible here.

Sources (all read-only, Codex's `strong-baseline-v1` worktree):
```
GT      artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff  (71 nodes, 70 edges)
image   artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.zarr  (100,64,256,256) uint16, same scale
preds   artifacts/detector_fixed_race/panel_runs_0c_{official,harmonic,mutual,motion}/44b6_0c582fdc/{method}.geff
```
Same reproduction method as §1–§7 (vendored `evaluate`/`_evaluate_matched_graph`, no
reimplementation); every method's recomputed TP/FP/node_recall matches its own
`race_receipt.json` exactly:

| method | edge TP/FP/FN | node_recall | final_score |
|---|---|---:|---:|
| `official_ilp` | 57/6/13 | 0.9718 (69/71) | 0.7385 |
| `harmonic_v1` | 62/6/8 | **1.0 (71/71)** | 0.8022 |
| `mutual_confidence` | 55/5/15 | 0.9577 | 0.7237 |
| `motion_gated` | 50/6/20 | 0.9437 | 0.6506 |

**Structural finding, more extreme than the dev sample**: a union-find over all 70 GT edges
shows the entire 71-node GT graph is **one single connected component**, spanning t=20→90
continuously, with in/out-degree ≤ 1 everywhere (no divisions/merges, verified the same way as
§1). This sample's annotation is **one single tracked cell for its whole 71-frame span** — not
two lineages like the dev sample, just one, slightly longer. The many different node-id local
suffixes (`...072` through `...086`) are the same kind of in-tool track relabeling seen in §1,
not separate cells.

**The 5 edges harmonic recovers over official** (official FN → harmonic TP; zero edges go the
other way — no regressions):

| GT edge | t | source (z,y,x) | target (z,y,x) | official | harmonic |
|---|---|---|---|---|---|
| 129000000085→130000000084 | 41→42 | (36,153,147) | (35,149,141) | both node-match identically (1.72, 0.57 µm); no edge selected | edge selected, `edge_prob=0.547` |
| 133000000083→134000000083 | 45→46 | (34,138,135) | (34,139,134) | **source node not even kept** in official's compacted graph (isolated node dropped, Addendum A2) | both endpoints matched (1.86, 1.86 µm), edge selected, `edge_prob=0.572` |
| 146000000079→147000000079 | 58→59 | (29,87,108) | (30,83,106) | both node-match identically (0.41, 1.46 µm); no edge selected (also recovered by `motion_gated`) | edge selected, `edge_prob=0.647` |
| 155000000077→156000000077 | 67→68 | (29,55,86) | (29,53,85) | **source node not even kept** in official's compacted graph | source matched at 4.24 µm (loose - dimmer/less certain detection), target at 1.72 µm, edge selected, `edge_prob=0.522` |
| 167000000076→168000000076 | 79→80 | (26,27,77) | (26,24,72) | both node-matched, but target match is 6.7 µm — right at the 7.0 µm matching radius, a borderline call | target match is 0.0 µm (essentially exact), edge selected, `edge_prob=0.527` |

**Reading**: 3 of the 5 (edges 1, 3, 5) repeat the dev-sample pattern exactly — identical
node-level detections, pure edge-selection difference, moderate `edge_prob` (0.52–0.65, all
comfortably past whatever bar mattered but nowhere near overwhelming confidence). The other 2
(edges 2, 4) are a new wrinkle: `official_ilp`'s ILP solution never keeps that GT node's
matching prediction at all (it has no edge worth keeping in official's scoring, so it is
compacted away as an isolated node — see Addendum A2), while harmonic's ILP does connect it.
This is a genuine association-driven node-recall difference (0.9718 vs 1.0), not a detector
difference — the same underlying detection is present in both methods' shared cache, only
`official_ilp`'s solved graph discards it.

Two representative crops (96×96, `low=80.0, high=2702.9` from this zarr's own quantiles,
crosshair on the exact GT voxel), saved under `artifacts/` (gitignored):
`0c_edge1_src_t41_z36_marked.png` / `0c_edge1_tgt_t42_z35_marked.png` (edge 1, the "clean"
edge-selection-only case) and `0c_edge5_src_t79_z26_marked.png` /
`0c_edge5_tgt_t80_z26_marked.png` (edge 5, the borderline-vs-exact match case). Both show the
same qualitative pattern as §8: the annotated cell sits in a visually crowded neighborhood of
several similar-brightness nuclei, not standing out as the obviously brightest blob.

**Conclusion for this sample**: the pattern from §9 is not a one-off. Two independent samples
now both reduce to a single continuously-tracked lineage, and in both, harmonic's entire
advantage over official is concentrated on that one lineage's hardest hops. A 5-edge gain on a
70-edge single-cell trajectory is not stronger evidence of general superiority than a 2-edge
gain on a 48-edge one — it is the same phenomenon at a different length. **Both data points
available so far are n=1-cell comparisons.** Whether this is how every sample in this dataset
is annotated (one cell densely tracked per movie, everything else sparse/unannotated) is worth
Codex or another lane confirming directly against the panel's `panel.json` / annotation
provenance; if so, the project's "5-sample panel" is closer to a 5-cell panel for edge-Jaccard
purposes, and per-lineage variance (not just per-sample variance) should be reported.

Raw outputs: `artifacts/edge_diff_raw_44b6_0c582fdc.json`,
`artifacts/edge_diff_detail_44b6_0c582fdc.json`; scripts:
`artifacts/edge_diff_generalized.py`, `artifacts/edge_diff_detail_0c.py`,
`artifacts/render_crops_0c.py` (all gitignored, kept for reproducibility).

Sample `44b6_0b24845f` (the other newly-finished panel sample) is not yet analyzed — queued,
see the Lane C report §5.

## 11. The division picture: the one GT division every method misses (`44b6_12dfb391`)

Per the coordinator's 2026-08-21 follow-up: `44b6_12dfb391` holds the dataset's only GT
division source, and **all four methods score `division_tp=0, division_fn=1`** there;
`harmonic_v1` additionally emits **3** division false positives on this sample and **1** on
`44b6_0db75fae` (which has zero GT divisions at all). This is the highest-value remaining
question because the official score's `0.1 · division_jaccard` term is completely unclaimed
project-wide, and Lane A separately found the ILP imposes a hard `p>0.9` fork-acceptance
threshold.

Reproduced with the vendored `biohub.official_metrics.division_metrics.score_divisions` /
`evaluate_divisions` directly (no reimplementation) against:
```
GT    artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff
pred  artifacts/detector_fixed_race/panel_runs_12df_{official,harmonic,mutual,motion}/
      44b6_12dfb391/{method}.geff
```
Recomputed `edge_tp/fp/fn` and `division_tp/fp/fn` for all four methods match
`race_receipt.json` exactly (official 668/81/105, harmonic 688/89/85, mutual 648/84/125,
motion 644/78/129; division_fp = 0/3/0/0 respectively) before trusting the per-node detail.

### 11.1 The missed division, structurally

```
parent (171000000049, t=65, z44.0 y64.0 x161.0)
  -> divider (172000000050, t=66, z46.0 y63.0 x160.0)
       -> child A (173000000051, t=67, z46.0 y74.0 x164.0) -> grandchild (174000000052, t=68)
       -> child B (173000000050, t=67, z46.0 y51.0 x160.0) -> grandchild (174000000051, t=68)
```
The two daughters separate by dy=+11 / dy=-12 voxels (≈4.5-4.9 µm each from the parent,
individually well inside the 7 µm matching radius) but end up ≈23 voxels / 9.3 µm apart from
each other - clearly two distinct cells one frame after the divider.

**What every method actually predicts there**: the node matched to the GT divider
(`172000000050`) is at the *exact right position* in all four methods (official 37074,
harmonic 37280, mutual 36819, motion 36514 - all four report position `(66, 46.0, 64.0,
160.0)`, i.e. correct detection, correct node-matching) - but **its out-degree is 0 in every
single method**. Not "one child accepted, one rejected" and not "wrong child accepted" - the
node is a dead end in all four predicted graphs. That is a stronger and more specific failure
mode than "the fork threshold rejects the second branch": it looks like **neither candidate
successor edge clears whatever bar the ILP requires**, so the track simply stops at the
divider instead of continuing at all (this is also counted as a portion of each method's
regular edge_fn, on top of the fixed 1 division_fn). This is consistent with, and sharpens,
Lane A's `p>0.9` hard-threshold finding: it is not merely making the *second* branch hard to
accept, it can apparently reject the correct continuation entirely at a genuine division site.

I cannot see the two real candidate edges' `edge_prob` from any method's final GEFF - only
*selected* edges are retained (§7's caveat, repeated here), and no method selects either real
daughter edge. Getting those requires the detector cache's retained candidate pool - `BLOCKED`,
Lane F's territory per Addendum A4.

### 11.2 Image evidence: the division site is visually dim and blurry

Crops (112×112 px, this zarr's own `low=70.0, high=3065.0` quantiles, crosshair on the exact
GT voxel; saved under `artifacts/`, gitignored):

| file | node | t | what's visible |
|---|---|---:|---|
| `div_wide_t66_z46_nomark.png` | (no crosshair - context view) | 66 | a soft, blurry field of several dim, low-contrast blobs; nothing stands out as a clearly-in-focus nucleus |
| `div_parent_t65_z44_marked.png` | parent 171000000049 | 65 | crosshair on a moderately dim blob, unremarkable versus neighbors |
| `div_divider_t66_z46_marked.png` | divider 172000000050 | 66 | crosshair sits in a soft, low-contrast area - no sharp nucleus at the exact division moment |
| `div_child1_t67_z46_marked.png` | child A 173000000051 | 67 | crosshair near a faint, small blob, not clearly separated from noise |
| `div_child2_t67_z46_marked.png` | child B 173000000050 | 67 | similar - dim, blurry, distinctly lower contrast than a typical single-track detection |
| `div_grandchild1_t68_z46_marked.png` / `div_grandchild2_t68_z45_marked.png` | grandchildren | 68 | same qualitatively dim/blurry pattern continues |

Every frame of this division event is visibly dimmer and blurrier than the crisp, high-contrast
single-cell-track crops in §8 and §10. This is a plausible, visually-grounded second cause
compounding the `p>0.9` threshold problem: **the dividing cell itself may be intrinsically
harder to detect/associate confidently** (lower signal, less sharply resolved - plausibly
because chromatin condensation or the mitotic shape briefly changes the fluorescence signature)
- so even if the threshold were relaxed, the raw edge evidence at this specific site may
legitimately be weaker than at a typical non-dividing hop. This is a qualitative, visual
observation only, not a measured signal-to-noise comparison.

### 11.3 The 3 wrong forks: confidently wrong, not merely below-threshold-but-forced

`harmonic_v1`'s 3 division false positives on this sample, plus its 1 on `44b6_0db75fae`
(which has zero GT divisions - this FP has no possible TP anywhere), all show the **same
signature**: both branch edges out of the fork node score **very high** `edge_prob`
(0.95-0.9995), clearly past the presumed 0.9 threshold on both sides - this directly answers
the coordinator's question of whether the fork threshold is "merely too strict" or the
underlying probabilities are "simply wrong": here, **the probabilities are high and the fork
still fires wrong**, so the threshold is not the limiting factor for these particular forks.

| fork (t, z,y,x) | matched GT | branch 1: pos, matched GT, `edge_dist`/`edge_prob` | branch 2: pos, matched GT, `edge_dist`/`edge_prob` |
|---|---|---|---|
| (55, 33.0,140.0,136.0) | 161000000072 | (56, 33.0,136.0,136.0), matched `162000000072`, 1.625 / **0.9976** | (56, 32.0,144.0,128.0), unmatched, 3.980 / 0.9811 |
| (14, 46.0,104.0,144.0) | 120000000056 | (15, 47.0,104.0,144.0), unmatched, 1.625 / 0.9994 | (15, 43.0,104.0,144.0), unmatched, 4.875 / 0.9908 |
| (46, 48.0,76.0,164.0) | 152000000054 | (47, 51.0,76.0,168.0), unmatched, 5.139 / 0.9662 | (47, 48.0,76.0,164.0), matched `153000000054`, 0.0 / **0.9995** |
| `44b6_0db75fae` fork (72, 48.0,132.0,188.0) | 113000000030 | (73, 51.0,132.0,192.0), matched `114000000030`, 5.139 / 0.9529 | (73, 48.0,132.0,196.0), unmatched, 3.25 / 0.9502 |

In every one of these 4 forks, **one branch matches a real GT node that has a legitimate
single-parent GT edge** (i.e. the fork's parent correctly continues the track through one
branch), while the *other* branch is the spurious one - so each of these is really "one correct
continuation edge, plus one extra high-confidence edge to a nearby cell that should not be
attached here," rather than two wrong branches. Image evidence for the third fork (chosen as
the highest-confidence example, both branches 0.966/0.9995 - crops
`fp_fork3_t46_z48_marked.png`, `fp_fork3_child_t47_z51_marked.png`,
`fp_fork3_child_exact_t47_z48_marked.png`): unlike the true division site, this neighborhood is
**visibly brighter and sharper**, with multiple distinct, well-resolved nuclei close together.
This is a genuine crowding/proximity case - two real, clearly-imaged cells sitting close enough
at t+1 that the model confidently (and wrongly) links both to the same t-frame parent, rather
than a probability-calibration failure in a vacuum.

**Answer to the coordinator's two questions**:
1. *Is the fork threshold merely too strict, or are probabilities simply wrong?* Both, in
   different places. At the one true division site, no candidate reaches whatever bar is
   needed (out-degree 0 everywhere) - consistent with a too-strict threshold on real,
   possibly-genuinely-weaker evidence (§11.2). At harmonic's false forks, the opposite problem:
   probabilities are confidently high (>0.95) on both branches, so the threshold is not the
   limiting factor there - it is a real local crowding ambiguity between correct track
   continuation and a nearby, brightly-imaged distractor cell.
2. *What does the missed division look like?* A visually dim, blurry, low-contrast few frames
   around an otherwise-correctly-detected/matched node that simply stops (zero successors) in
   every method's solved graph - not a case of picking the wrong daughter.

Scripts + raw JSON: `artifacts/division_analysis.py` (+ output
`artifacts/division_analysis_44b6_12dfb391.json`), `artifacts/division_0db.py`,
`artifacts/render_division_crops.py` (all gitignored, kept for reproducibility).
