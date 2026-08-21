# Validation dataset design and evaluation protocol (Lane B)

Author: Claude Lane B, 2026-08-21. Branch `claude/b-validation`.
Status: **binding proposal** — supersedes the implicit "evaluate on `44b6_0113de3b`" practice.

Every performance claim in this project currently rests on one movie with 50 ground-truth
edges. This document measures how much that panel can actually support, designs a
replacement, and states the reporting rules that go with it.

All numbers below are measured, not estimated. Reproduction commands are in §8.

---

## 1. Executive summary

| Question | Answer |
|---|---|
| Smallest score difference the current dev movie can resolve | **0.1356** |
| Observed spread across all four race methods | 0.1115 — **below the noise floor** |
| Pairs of the four methods separable after multiplicity correction | **0 of 6** |
| P(observed method ranking reproduced on a resampled panel) | **0.5644** |
| P(the baseline `official_ilp` ranks best) | **0.1603** |
| GT edges needed for δ=0.005 | **10,508 – 52,540** (49–241 movies) |
| GT edges needed for δ=0.01 | **2,627 – 13,135** (13–61 movies) |
| GT edges in all 5 local samples | **1,093** |
| Best resolution obtainable from the entire 199-movie train set | **δ ≈ 0.0046 – 0.0081** |

**The practical target is δ = 0.01, not 0.005.** A 0.005 difference sits at the very edge
of what the whole published dataset can establish; treat any claim below 0.01 as
undecided until the panel is at least 13 movies / ~2,600 GT edges.

---

## 2. What exists — competition inventory

Measured by listing (never downloading) the competition file manifest:
`scripts/kaggle_list_competition_files.py`, 125 API pages, 24,886 file entries.

| Split | Files | Bytes | Samples | Has image | Has GT |
|---|---:|---:|---:|---:|---:|
| `train/` | 24,477 | 85,703,559,720 (79.818 GiB) | **199** | 199 | 199 |
| `test/` | 408 | 1,906,332,008 (1.775 GiB) | **4** | 4 | 0 |
| root | 1 | 890 | — | `sample_submission.csv` | — |
| **total** | **24,886** | **87,609,892,618 (81.59 GiB)** | | | |

Per-sample image volume: min 317,108,747 B, median 411,186,738 B, max 607,764,799 B
(mean 410.7 MiB). Every sample is 102 zarr chunk files.

**Ground truth for all 199 train samples totals 2,353,863 bytes (2.24 MiB)**
— min 7,587 B, median 11,713 B, max 20,060 B per sample.

Locally on disk: **5 of 199 samples (2.5%)**, 2.4 GB, at
`<CODEX>/artifacts/detector_fixed_race/panel_data/train/`.

Host disk free is 37 GiB against a 79.8 GiB train set, so the full set does not fit.
At the 410.7 MiB mean, 37 GiB holds ~92 samples before headroom; a realistic ceiling for
a locally-materialised panel is **~60 movies**.

### 2.1 S0 — the test images are duplicates of train images whose GT is published

All four `test/` sample ids also appear under `train/`, with **byte-identical** volumes:

| id | `train/…zarr` bytes | `test/…zarr` bytes | equal | `train/…geff` bytes |
|---|---:|---:|:---:|---:|
| `44b6_0113de3b` | 456,757,564 | 456,757,564 | yes | 7,617 |
| `44b6_0b24845f` | 547,662,847 | 547,662,847 | yes | 7,618 |
| `6bba_05b6850b` | 361,668,669 | 361,668,669 | yes | 12,964 |
| `6bba_05db0fb1` | 540,242,928 | 540,242,928 | yes | 15,343 |

Our local `44b6_0113de3b.geff` is 7,617 bytes — the same file. **The movie this project
develops against, and the ground truth it scores against, is one of the four movies the
leaderboard is computed on.**

Consequences, in order of importance:

1. The local 0.9211 is **not a generalisation estimate**. It is a direct readout of a
   quarter of the leaderboard.
2. Any threshold, weight, or method choice made by looking at that number is **leaderboard
   overfitting**, not validation — even though reading the file is legal, since Kaggle
   publishes it under `train/`.
3. The leaderboard itself is small. Its two `44b6` movies carry 50 and 49 GT edges. If the
   two `6bba` movies are comparable, **the entire leaderboard rests on a few hundred GT
   edges** and has a noise floor of its own. Public/private splitting makes this worse.

This does not mean anyone broke a rule. It means the rule in §0.4 of the team brief
("GT only for metric evaluation") is now load-bearing, and §6 below makes it enforceable.

### 2.2 S1 — the panel selection rule structurally excludes 64% of the train distribution

Train sample ids carry two prefixes:

| prefix | train samples | test samples |
|---|---:|---:|
| `6bba_` | **128** (64.3%) | 2 (50%) |
| `44b6_` | 71 (35.7%) | 2 (50%) |

`panel.json` records `"selection_rule": "lexicographic image filename"`. Because `'4' < '6'`
and there are 71 `44b6_` samples, **the first 71 lexicographic samples are all `44b6_`**.
Taking the first 5 therefore cannot ever produce a `6bba_` sample. The project has never
seen a single movie from the domain that makes up 64% of train and 50% of the test set.

This is a defect in the selection rule, not bad luck. The fix is one line: stratify by
prefix, or sample by a hash of the id, instead of taking a lexicographic prefix.

---

## 3. Characterisation of the 5 local samples (GT metadata only)

Produced by `scripts/characterise_gt_panel.py` — reads only the ~8–20 KB GEFF graph
arrays, never the 410 MiB image volumes. Image intensity quantiles are taken from the
existing `panel.json`. The script emits aggregate structure only (counts, spans,
distributions); it deliberately never emits node coordinates, so its output cannot be
fed back into detection or association.

| sample | GT nodes | GT edges | tracks | divisions | frames ann. | span | movie cov. | nodes/frame | est. nodes | annotated % | d50 µm | d95 µm | dmax µm | edges >7 µm | edges >3.5 µm |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `44b6_0113de3b` | 52 | 50 | 2 | 0 | 52 | 76 | 52% | 1.00 | 25,755 | 0.202% | 2.88 | 6.29 | 7.81 | 1 | 32.0% |
| `44b6_0b24845f` | 51 | 49 | 2 | 0 | 40 | 40 | 40% | 1.27 | 32,795 | 0.156% | 2.07 | 4.24 | 5.15 | 0 | 10.2% |
| `44b6_0c582fdc` | 71 | 70 | 1 | 0 | 71 | 71 | 71% | 1.00 | 27,958 | 0.254% | 2.19 | 3.87 | 5.03 | 0 | 5.7% |
| `44b6_0db75fae` | 157 | 151 | 6 | 0 | 89 | 89 | 89% | 1.76 | 15,335 | 1.024% | 2.33 | 4.72 | 7.63 | 1 | 15.2% |
| `44b6_12dfb391` | 788 | 773 | 15 | **1** | 100 | 100 | 100% | 7.88 | 58,672 | 1.343% | 1.28 | 2.37 | 5.87 | 0 | 1.0% |
| **total** | **1,119** | **1,093** | 26 | **1** | | | | | | | | | | | |

Track length (nodes per weakly-connected component): min 1→ none are singletons; ranges
3–49, 11–40, 71–71, 2–78, 4–111 respectively. **No track has an internal frame gap** and
**every GT edge connects consecutive frames** (`dt` histogram is `{1: n}` for all five) —
so the panel exercises no gap-closing behaviour at all.

Annotation sparsity is extreme: 0.156%–1.343% of the estimated node population is
annotated. Unannotated cells are not negatives.

### 3.1 The development sample is the least representative movie in the panel

`44b6_0113de3b` is simultaneously:

- the **smallest** (50 GT edges, tied-smallest with `0b24845f`),
- the **sparsest** (exactly 1.00 annotated cell per frame — the minimum possible),
- the **fastest-moving** (median displacement 2.88 µm, highest of the five),
- the **worst-covered** (52% of the movie; 24 frames inside its annotated span carry no
  annotation at all — its two tracks occupy disjoint frame ranges),
- the **most metric-fragile** (32% of its GT edges displace more than half the 7.0 µm
  matching radius, and one edge displaces **7.81 µm**, i.e. further than the matcher's
  entire tolerance),
- and it has **zero divisions**.

It is also one of the four leaderboard movies (§2.1). Choosing it as the development
sample was the worst available choice on every axis simultaneously.

### 3.2 S2 — the matching radius is the same order as real cell motion

`official_metrics.evaluate` matches predicted to GT nodes with
`DistanceMatching(max_distance=7.0)` (µm; confirmed default in
`src/biohub/detector_fixed_race/prediction.py:62` and `strong_baseline/evaluation.py:60`).
GT cells routinely move 2–3 µm per frame and up to **7.81 µm**. A tolerance that admits a
match at 7.0 µm, when a cell's own one-frame motion can exceed that, means node identity
assignment is not comfortably determined by geometry. This is a property of the official
metric, not a bug in our code, but it means the TP/FN labels themselves carry matching
noise on top of the sampling noise quantified in §4.

### 3.3 What the 5 local samples cannot cover

- **Divisions — the panel is effectively blind.** One division source across 1,093 edges.
  Every evaluation to date has `division_tp/fp/fn = 0/0/0` and `division_jaccard = null`,
  so `summarise()` **drops the division term entirely** and `final_score` silently reduces
  to the adjusted edge Jaccard. The real metric is
  `J_adj + 0.1 · division_jaccard`. **10% of the score is untested.** A method that never
  predicts a division and a method with perfect division handling score identically here.
- **The `6bba_` domain — zero coverage** (§2.2), against 50% of the test set.
- **Gap closing — zero coverage.** All 1,093 edges are `dt = 1`.
- **Density and motion are confounded.** Rank by density: `12dfb391` (7.88/frame) is 4.5×
  denser than any other; rank by speed, it is the *slowest* (d50 1.28 µm vs 2.07–2.88).
  The single dense sample is also the single slow sample, so no result on this panel can
  attribute a win to "handles crowding" versus "handles fast motion".
- **Aggregate weighting is dominated by one movie.** `44b6_12dfb391` holds 773 of 1,093
  edges (70.7%). Both the micro-averaged `edge_jaccard` and the size-weighted
  `adj_edge_jaccard` in `summarise()` are, to first order, that one movie's score.

---

## 4. Statistical power — how small a difference means anything

Computed by `scripts/validation_power_analysis.py` (two methods) and
`scripts/validation_power_multi.py` (the four-method race), from the real saved counts in
`race_receipt.json`. 200,000 bootstrap draws, seed 20260821.

### 4.1 The metric's own granularity

`edge_jaccard = TP / (TP + FP + FN)` with `FN = GT_edges − TP` exactly, and
`J_adj = J · (1 − 0.1 · total_node_ratio)`. On 50 GT edges, flipping **one** edge moves
`final_score` by:

- **0.036208** if the prediction survives as a false positive (a link error), or
- **0.019190** if the prediction disappears (a miss).

A 0.005 difference is **below the quantisation step of the metric on this panel**. On a
fixed edge set it cannot be produced by tracking at all — only by the continuous
node-count adjustment term (§4.5).

### 4.2 The four-method race is one measurement of noise

Four methods, one identical detector cache
(`artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json`):

| method | TP/FP/FN | edge Jaccard | node-adj. multiplier | final_score |
|---|---|---:|---:|---:|
| `harmonic_v1` | 48/2/2 | 0.923077 | 0.997880 | 0.9211200215 |
| `official_ilp` | 46/2/4 | 0.884615 | 0.999072 | 0.8837944835 |
| `mutual_confidence` | 43/0/7 | 0.860000 | 0.999802 | 0.8598297030 |
| `motion_gated` | 42/2/8 | 0.807692 | **1.002376** | 0.8096115765 |

Pairwise exact McNemar on the discordant GT edges (the correct test: all four methods are
scored on the *same* 50 edges). Bonferroni over 6 pairs gives α = 0.00833.

| A | B | Δscore | discordant edges | exact p | p<0.05 | survives Bonferroni |
|---|---|---:|---:|---:|:---:|:---:|
| `official_ilp` | `harmonic_v1` | +0.0373 | 2 | **0.5000** | no | no |
| `official_ilp` | `mutual_confidence` | −0.0240 | 3 | 0.2500 | no | no |
| `official_ilp` | `motion_gated` | −0.0742 | 4 | 0.1250 | no | no |
| `harmonic_v1` | `mutual_confidence` | −0.0613 | 5 | 0.0625 | no | no |
| `harmonic_v1` | `motion_gated` | −0.1115 | 6 | **0.0312** | yes | **no** |
| `mutual_confidence` | `motion_gated` | −0.0502 | 1 | 1.0000 | no | no |

**Zero of six pairs survive multiplicity correction.** The only nominally significant
comparison is best-versus-worst, and it fails Bonferroni. You need **≥ 6** one-way
discordant edges for p<0.05 and **≥ 8** after Bonferroni; the largest observed is 6.

These p-values assume **nested true-positive sets** — that the better method recovered a
superset of the worse method's edges. That is the arrangement most favourable to the
challenger. `race_receipt.json` stores counts, not the matched-edge mask, so the true
pairing is unidentified; any other pairing makes the evidence weaker. **All "not
separable" conclusions here are therefore conservative.**

### 4.3 Ranking stability

Resampling the 50 GT edges with replacement, 200,000 times:

- P(the full observed ranking `harmonic_v1 > official_ilp > mutual_confidence > motion_gated`
  is reproduced) = **0.5644**
- P(`harmonic_v1` ranks best) = **0.7879**
- P(`official_ilp` ranks best) = **0.1603**
- P(`mutual_confidence` ranks best) = 0.0486
- P(`motion_gated` ranks best) = 0.0032

A panel that crowns the plain baseline as champion one run in six is not selecting methods;
it is sampling them.

For the headline `official_ilp → harmonic_v1` claim specifically, the bootstrap 95% CI on
the difference is **[−0.0458, +0.1251]** (sd 0.0428) — it contains zero, and **18.0% of
draws favour the baseline**.

### 4.4 Noise floor by panel, and the size required

Using the paired normal-approximation McNemar relation
`n ≥ 1.96² · π_d / Δr²` with `Δr = δ / (dJ/dr)` and `dJ/dr = (1+k)/(1+k−r)² = 1.8491` at
`r = 0.96`, `k = VP/GT = 1.0`; `π_d` is the discordance rate (observed mean across the six
pairs: 0.070).

Smallest score difference resolvable at p<0.05:

| panel | GT edges | δ resolvable |
|---|---:|---:|
| dev movie `44b6_0113de3b` (current practice) | 50 | **0.1356** |
| proposed locked tier (2 movies) | 99 | 0.0964 |
| proposed dev tier (3 movies) | 994 | **0.0304** |
| all 5 local samples | 1,093 | 0.0290 |
| all 199 train movies, at local mean 218.6 edges/movie | ~43,501 | **0.0046** |
| all 199 train movies, at local median 70 edges/movie | ~13,930 | **0.0081** |

GT edges (and movies) required for a target difference to be meaningful:

| target δ | discordance 0.02 | discordance 0.070 (observed) | discordance 0.10 |
|---|---:|---:|---:|
| **0.005** | 10,508 edges (49 / 151 movies) | 36,778 edges (169 / 526 movies) | 52,540 edges (241 / 751 movies) |
| **0.010** | 2,627 edges (13 / 38 movies) | 9,195 edges (43 / 132 movies) | 13,135 edges (61 / 188 movies) |

Movie counts are given as *mean-based / median-based*, extrapolating from the 5 local
samples (mean 218.6, median 70.0 edges per movie). That extrapolation carries the §2.2
selection bias — all five are `44b6_`, and four of five are sparse — so treat the bracket,
not either endpoint, as the answer. Resolving it costs 2.24 MiB (§7, item 1).

**The decisive number: δ = 0.005 requires 49–241 movies, and the entire published train
set of 199 movies resolves only δ ≈ 0.0046–0.0081.** A 0.005 improvement is at the edge of
what this competition's data can ever demonstrate. δ = 0.01 is reachable with 13–61 movies
and is the correct engineering target.

### 4.5 S1 — the node-count term can move the score without changing any tracking

`total_node_ratio = (N_pred − N_total)/N_total` and `J_adj = J · (1 − 0.1 · ratio)`. Across
the four methods the multiplier spans **0.997880 to 1.002376**, worth **0.00391** of
`final_score` at the observed Jaccards — with identical tracking decisions. Note
`motion_gated` has a *negative* ratio and therefore receives a **bonus**: predicting fewer
nodes than the estimate is rewarded.

That 0.00391 is 78% of the 0.005 "meaningful difference" target. Any comparison at the
0.005 scale is measuring the GEFF writer's node-emission policy, not tracking quality.
This interacts directly with the `_compact_prediction_inputs()` change flagged for Lane A:
dropping isolated nodes lowers `N_pred`, which raises the score. **Report
`total_node_ratio` and raw `edge_jaccard` alongside `final_score`, always**, so this term
is visible rather than absorbed.

---

## 4.6 PRE-REGISTRATION — what sample 2 must show

Written **before** the second sample's numbers existed. Codex is running the four-method
race on `44b6_0b24845f` (51 GT nodes / **49 GT edges**) now. A prediction recorded after
seeing the result is worth nothing, so this is fixed here and must not be edited once the
numbers land — only compared against.

Produced by `scripts/preregister_sample2.py`, 200,000 simulations, seed 20260821.

**Hypotheses.** `H0`: the sample-1 differences are noise; all four methods share the pooled
sample-1 recall (179/200 = 0.895). `H1`: the sample-1 differences are real; each method's
true recall is its sample-1 rate (harmonic 0.96, official 0.92, mutual 0.86, motion 0.84).

Simulated under two correlation regimes that bracket reality. `independent` — each method
draws its own `Binomial(49, p_m)`; the methods share a detector cache so this understates
their coupling and *overstates* ranking churn. `comonotone` — one shared per-edge
difficulty ranks all methods identically; the maximum-coupling extreme, which *overstates*
ranking stability. Note `H0/comonotone` is degenerate: identical recalls plus perfect
coupling predicts all four methods return **exactly equal** TP counts. That is itself a
falsifiable prediction, and its near-certain falsification is why the meaningful H0 is the
independent column.

| statistic on sample 2 | H0 (noise) | H1 (real), independent | H1 (real), comonotone |
|---|---:|---:|---:|
| P(sample-1 ranking reproduced exactly) | **0.053** | 0.371 | 0.865 |
| P(`harmonic_v1` ranks first) | **0.266** | 0.724 | 0.865 |
| P(all 6 pairwise signs agree with sample 1) | **0.017** | 0.243 | 0.514 |
| mean pairwise sign agreements, of 6 | **2.60** | 4.85 | 5.42 |
| `harmonic_v1 − official_ilp` edge gain, mean | **0.00** | +1.97 | +1.96 |
| P(that gain > 0) | **0.434** | 0.735 | 0.865 |

Predicted TP counts on 49 GT edges (95% intervals):

| method | H0 | H1 |
|---|---|---|
| `official_ilp` | 39–48 | 41–48 |
| `harmonic_v1` | 39–48 | **44–49** |
| `mutual_confidence` | 39–48 | 37–47 |
| `motion_gated` | 39–48 | 36–46 |

**The discriminating statistic is the count of pairwise sign agreements (of 6): H0 predicts
≈2.6, H1 predicts ≈4.9–5.4.** Report that number. The H0 and H1 TP intervals overlap almost
completely, so no individual method's TP count on sample 2 can settle anything by itself.

### What a two-sample result does and does not license

Pooling samples 1 and 2 gives **99 GT edges**, resolvable δ = **0.0964** — still 2.6× larger
than the 0.0373 claim. Pooled exact McNemar, assuming harmonic's TP set nests official's on
each sample (again the assumption most favourable to harmonic):

| harmonic's edge gain per sample | samples agreeing | pooled b (c=0) | exact p | p<0.05 | survives Bonferroni |
|---:|---:|---:|---:|:---:|:---:|
| +1 | 2 | 2 | 0.5000 | no | no |
| +2 | 1 | 2 | 0.5000 | no | no |
| **+2** | **2** | **4** | **0.1250** | **no** | **no** |
| +2 | 3 | 6 | 0.0312 | yes | no |
| +2 | 4 | 8 | 0.0078 | yes | **yes** |
| +3 | 2 | 6 | 0.0312 | yes | no |
| +3 | 3 | 9 | 0.0039 | yes | **yes** |

**Licensed conclusions, fixed in advance:**

- **Agreement (harmonic first again, +2 edges) does NOT license "confirmed".** Pooled
  p = 0.125. The correct wording is *"consistent across two samples, still not separated
  (pooled McNemar p = 0.125; two samples resolve δ ≥ 0.0964)."* Under H1 this outcome was
  expected 72–87% of the time, but under H0 it still happens 43% of the time — agreement on
  a second sample is weak evidence, not confirmation.
- **Disagreement (official ties or beats harmonic) is strong evidence for H0.** H1 gives
  this only 13.5–26.5% of the time. Two disagreeing samples should stop any move to adopt
  harmonic as default.
- **A sign-agreement count near 2–3 of 6 is an H0 signature**; near 5–6 favours H1. This is
  the number to look at first.
- **The earliest point the harmonic claim can survive multiplicity correction is 4 samples
  at +2 edges each** (pooled b=8, p=0.0078), or 3 samples at +3. Nothing before that
  licenses "harmonic is better"; sample 2 cannot get there arithmetically.
- **`motion_gated` can be rejected sooner.** Its gap to harmonic is 6 edges on sample 1; a
  repeat on sample 2 gives pooled b=12, p=0.0005, which survives Bonferroni. Expect the
  first defensible conclusion of this whole programme to be a *rejection*, not a promotion.

---

## 5. The split

Two tiers. Assignment is driven by §2.1 first (leaderboard contamination), then by the
metadata axes in §3.

### Dev tier — iterate freely

| sample | GT edges | why it is here |
|---|---:|---|
| `44b6_12dfb391` | 773 | Only division in the panel; densest (7.88/frame); full 100-frame coverage; slowest motion. Not a test movie. |
| `44b6_0db75fae` | 151 | Mid density (1.76/frame); most tracks after `12dfb391` (6); shortest tracks (median 5 nodes) so it exercises track starts/ends; 89% coverage. Not a test movie. |
| `44b6_0c582fdc` | 70 | The clean single-track control: 1 track, 71 contiguous frames, no edge above 5.03 µm. Isolates association from detection. Not a test movie. |
| **total** | **994** | resolvable δ = **0.0304** |

### Locked tier — final confirmation only, every touch logged

| sample | GT edges | why it is locked |
|---|---:|---|
| `44b6_0113de3b` | 50 | **Is a leaderboard test movie** (§2.1). Also the sparsest and most metric-fragile sample. |
| `44b6_0b24845f` | 49 | **Is a leaderboard test movie** (§2.1). |
| **total** | **99** | resolvable δ = **0.0964** |

This reverses current practice: `44b6_0113de3b`, today's development sample, moves to the
locked tier. The justification is not statistical fastidiousness — it is that iterating on
it is iterating on the leaderboard.

**Honest limits of this split.** The dev tier is 3 movies, one domain, one division. It
resolves δ ≥ 0.030 — enough to reject `motion_gated`, not enough to rank `harmonic_v1`
against `official_ilp`. It gives **no** signal on the 10% division term and **no** signal
on `6bba_`. It is a floor to build on, not a validation set. §7 says how to grow it.

---

## 6. Reporting protocol

**P1 — never report a single number.** Every result is a per-sample table
(`sample_id`, `edge_tp`, `edge_fp`, `edge_fn`, `edge_jaccard`, `division_tp/fp/fn`,
`total_node_ratio`, `adj_edge_jaccard`) **plus** the aggregate from
`official_metrics.summarise()`. A bare `final_score` is not a result.

**P2 — always report `total_node_ratio` and raw `edge_jaccard`** next to `final_score`,
so the §4.5 node-count term is visible and cannot silently carry a comparison.

**P3 — state the noise floor with every comparison.** Quote the δ resolvable for the
panel used (§4.4). A difference smaller than that floor is reported as
**"not separated"** — never as "improved", "better", or "wins".

**P4 — a difference needs a test, not an ordering.** Two methods are "separated" only when
the exact McNemar p over their discordant edges clears α, Bonferroni-corrected by the
number of methods compared in that report. Report `b`, `c`, and `p`.

**P5 — no adoption on the strength of one sample.** *A result on any single sample, however
large the margin, may never be the reason a method, threshold, or hyperparameter is
adopted.* Adoption requires the full dev tier, per-sample, with P3 and P4 satisfied. "This
sample looked good so we adopt it" is prohibited — that is the failure mode that produced
the current 50-edge anecdote.

**P6 — the locked tier is measured, never optimised against.** Touching it requires:
(a) the change is already frozen on the dev tier; (b) a line appended to
`docs/results/locked_tier_log.md` with UTC timestamp, git SHA, method id, and the reason;
(c) the result is reported whatever it says. Locked-tier numbers may not motivate a
subsequent change. If they do, the sample is burned and must be treated as dev from then on.

**P7 — GT is for measurement only.** Ground truth may enter metric evaluation and
metadata-only panel design. It may not enter detection, candidate generation, association
scoring, threshold choice, or hyperparameter tuning. This is doubly binding for
`44b6_0113de3b`, `44b6_0b24845f`, `6bba_05b6850b`, `6bba_05db0fb1`, whose GT is the
leaderboard's own answer key (§2.1).

**P8 — division coverage is declared, not assumed.** Any report whose panel yields
`division_tp+fp+fn = 0` must state: *"division term (10% of the official score) untested on
this panel."* `summarise()` drops the term silently; the report must not.

**P9 — panel changes are versioned.** Changing tier membership means a new panel version
id, and results across panel versions are never compared numerically.

---

## 7. Recommended next actions, ranked by value per byte

1. **Download the 199 train GEFF graphs — 2,353,863 bytes (2.24 MiB) total.** This is the
   highest-value action available to the project by a wide margin. It yields the true
   per-movie distribution of GT edges, divisions, density and motion across both domains,
   which (a) replaces the §4.4 mean/median bracket with an exact number, (b) allows the
   panel to be chosen for **division content** instead of alphabetical order, and (c) sizes
   the panel exactly. It downloads no image data and needs no container compute.
   *Requires explicit approval — see §5 of the lane report. Files for the four test movies
   are the leaderboard answer key; if downloaded they are locked-tier on arrival under P6/P7.*
2. **Replace the lexicographic selection rule with prefix-stratified sampling** in
   `src/biohub/detector_fixed_race/panel.py`. One-line class of change; without it no
   future panel can ever contain a `6bba_` movie (§2.2).
3. **Grow the dev tier to ~43 movies / ~9,200 GT edges**, stratified `6bba_`/`44b6_` 64/36
   and required to contain **≥ 20 division events**, to reach δ = 0.01 at the observed
   discordance. At 410.7 MiB per movie that is **17.2 GiB** — it fits in the 37 GiB free.
   13 movies (5.2 GiB, 2,842 edges) is the optimistic-discordance floor and resolves
   δ ≈ 0.018 at the observed rate, so treat it as a staging point, not the target.
   The disk-bound ceiling of ~60 movies (24.1 GiB, ~13,100 edges at the local mean)
   resolves **δ ≈ 0.0084–0.0148**.
   **δ = 0.005 is out of reach locally**: at the observed discordance it needs 169 movies
   ≈ 67.7 GiB, against 37 GiB free. Reaching it would require streaming rather than
   materialising volumes, and even the whole 199-movie train set only gets to ≈0.0046.
4. **Dump the matched-edge mask** in the evaluation path so pairwise comparisons use the
   real per-edge pairing instead of the nested-set assumption in §4.2.
5. **Re-run the four-method race on the dev tier** once its cache exists, and re-apply §6.

---

## 8. Reproduction

```bash
# 1. competition inventory (listing only, no download; resumable, rate-limited)
python scripts/kaggle_list_competition_files.py \
  --cache artifacts/validation_design/kaggle_files.jsonl \
  --out   artifacts/validation_design/kaggle_manifest_summary.json

# 2. GT metadata characterisation (reads only ~8-20 KB GEFF graphs per sample)
python scripts/characterise_gt_panel.py \
  --geff-dir  <CODEX>/artifacts/detector_fixed_race/panel_data/train \
  --panel-json <CODEX>/artifacts/detector_fixed_race/panel.json \
  --out artifacts/validation_design/gt_characterisation.json

# 3. two-method power analysis
PYTHONPATH=scripts python scripts/validation_power_analysis.py \
  --metrics-root <CODEX>/artifacts/strong_baseline_v1 \
  --gt-char artifacts/validation_design/gt_characterisation.json \
  --out artifacts/validation_design/power_analysis.json

# 4. four-method power analysis, tier sizing, train-set ceiling
PYTHONPATH=scripts python scripts/validation_power_multi.py \
  --receipt <CODEX>/artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json \
  --gt-char artifacts/validation_design/gt_characterisation.json \
  --tier dev=44b6_0c582fdc,44b6_0db75fae,44b6_12dfb391 \
  --tier locked=44b6_0113de3b,44b6_0b24845f \
  --n-train-movies 199 \
  --out artifacts/validation_design/power_analysis_multi.json
```

`<CODEX>` = `scratch/strong-baseline-v1/biohub-cell-tracking-during-development`
(read-only). `artifacts/` is gitignored, hence the tables above are inlined here.
Total container cost of steps 2–4: a few seconds and well under 200 MB RSS.

```bash
# 5. pre-registration for sample 2 (run BEFORE its results exist)
PYTHONPATH=scripts python scripts/preregister_sample2.py \
  --receipt <CODEX>/artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json \
  --n2 49 --out artifacts/validation_design/prereg_sample2.json
```
