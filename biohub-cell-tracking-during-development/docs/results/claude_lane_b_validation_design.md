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
| Sample 2 (`44b6_0b24845f`, 49 edges): agreement licenses | **"consistent, still not separated"** — pooled McNemar p = 0.125 (§4.6) |
| **UPDATE §4.7** — pre-registered H0 (2.60 of 6 sign agreements) vs observed | **6/6 twice; H0 rejected, LR 916×** |
| Pooled 3-sample panel (169 edges): harmonic vs official | **p = 0.0078, CI [+0.011,+0.069] — passes, knife-edge** |
| Reversed edges needed to overturn that verdict | **1** at 3 samples; **≥15** at 5 samples |
| **FIVE-SAMPLE FINAL (§4.8)** — harmonic vs official | **b=29, c=0, p=3.7e-09 — ESTABLISHED** |
| Effect size as evidence grew | +0.0373 (1 movie) → +0.0360 (3) → **+0.0178 (5)** |
| `mutual_confidence` vs `motion_gated` | **not separated** — fails leave-one-out, gap 0.0059 < floor 0.0195 |
| Share of the official score carried by `44b6_12dfb391` | **71.2%** |
| Division term, now live | every method **0/1**, `division_jaccard` = 0.0 |
| Value of detecting the one division | **+0.100**, vs a four-method spread of 0.0486 |
| Panel noise floor, one movie → five | 0.1356 → 0.0672 → **0.0195** |
| Pooled `harmonic_v1` score (not 0.9211) | **0.7802** |
| Pooled score discarded to node over-prediction | **0.0220** (61% of the harmonic gain) |

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

### 4.6 PRE-REGISTRATION — what sample 2 must show

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

#### What a two-sample result does and does not license

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

### 4.7 SCORED — the pre-registration graded against three samples

Added 2026-08-21 after Codex completed the four-method race on samples 2 and 3.
**§4.6 above is unedited.** This section grades it.

Counts read from the on-disk receipts (`panel_runs_0b_*`, `panel_runs_0c_*`,
`panel_runs/`, `dev_full_auto_compact_timed/`), never transcribed:

| sample | GT edges | official | harmonic | mutual | motion |
|---|---:|---|---|---|---|
| `44b6_0113de3b` | 50 | 46/2/4 → 0.88379 | 48/2/2 → 0.92112 | 43/0/7 → 0.85983 | 42/2/8 → 0.80961 |
| `44b6_0b24845f` | 49 | 39/9/10 → 0.62622 | 40/10/9 → 0.62747 | 37/8/12 → 0.60938 | 35/8/14 → 0.58141 |
| `44b6_0c582fdc` | 70 | 57/6/13 → 0.73850 | 62/6/8 → 0.80224 | 55/5/15 → 0.72368 | 50/6/20 → 0.65057 |

#### The pre-registration's H0 is rejected

| | predicted | observed |
|---|---|---|
| mean pairwise sign agreements, of 6 — **H0** | **2.60** | — |
| mean pairwise sign agreements, of 6 — **H1** | 4.85 – 5.42 | — |
| `44b6_0b24845f` | — | **6 / 6**, ranking matches sample 1, harmonic −official = **+1** |
| `44b6_0c582fdc` | — | **6 / 6**, ranking matches sample 1, harmonic −official = **+5** |

Joint probability of this outcome: **0.000288 under H0**, 0.264 under H1/comonotone —
a likelihood ratio of **916×** in favour of H1. Both new samples reproduced the sample-1
ranking `harmonic_v1 > official_ilp > mutual_confidence > motion_gated` exactly.

**I was wrong to treat the ordering as noise.** The pre-registered H0 predicted ~2.6 of 6
sign agreements and the data delivered 6 of 6, twice. The method ordering is systematic.
What remains contested is not *whether* the ordering is real but *how large* the score
consequence is and whether it generalises — see below.

#### Pooled pairwise exact McNemar (169 GT edges, Bonferroni α = 0.00833)

| A | B | per-sample edge gains | pooled b | c | exact p | p<0.05 | Bonferroni |
|---|---|---|---:|---:|---:|:---:|:---:|
| official_ilp | harmonic_v1 | +2, +1, +5 | 8 | 0 | **0.007812** | yes | **yes (barely)** |
| official_ilp | mutual_confidence | −3, −2, −2 | 0 | 7 | 0.015625 | yes | **no** |
| official_ilp | motion_gated | −4, −4, −7 | 0 | 15 | 0.000061 | yes | yes |
| harmonic_v1 | mutual_confidence | −5, −3, −7 | 0 | 15 | 0.000061 | yes | yes |
| harmonic_v1 | motion_gated | −6, −5, −12 | 0 | 23 | <0.000001 | yes | yes |
| mutual_confidence | motion_gated | −1, −2, −5 | 0 | 8 | **0.007812** | yes | **yes (barely)** |

**harmonic-vs-official pools to b=8, c=0, p = 0.0078125, which does cross α = 0.008333.
Stated plainly: on this evidence harmonic_v1 beats official_ilp.** Two caveats that must
travel with that sentence:

1. **This is an optimistic bound, not a measurement.** Both b and c come from the
   nested-TP assumption — that within each sample the better method recovered a strict
   superset of the worse one's edges, so c = 0 by construction. Nested-TP *maximises*
   apparent separation. The receipts store counts, not the matched-edge mask, so the true
   pairing is unobserved.
2. **It is knife-edge.** Sensitivity to reversals (edges where official succeeded and
   harmonic failed):

| reversed edges added | b | c | p | survives Bonferroni |
|---:|---:|---:|---:|:---:|
| 0 (nested assumption) | 8 | 0 | 0.0078 | **yes** |
| 1 | 9 | 1 | 0.0215 | no |
| 2 | 10 | 2 | 0.0386 | no |
| 3 | 11 | 3 | 0.0574 | no (fails even α=0.05) |

**A single reversed edge out of 169 moves harmonic-vs-official from "significant after
correction" to "not significant".** The same applies to mutual-vs-motion (also b=8). The
other four pairs survive ≥3 reversals and are robust.

**What the matched-edge mask is needed for:** it is the only thing that turns b=8, c=0 from
an assumption into an observation. Dumping
`td.DEFAULT_ATTR_KEYS.MATCHED_EDGE_MASK` per predicted edge alongside each GEFF would give
the real per-edge outcome for every method, hence the true (b, c) per pair. Until then the
harmonic verdict rests on an unverified assumption that a single counter-example destroys.
This is now the highest-value cheap fix in the project.

#### Pooled panel scores (169 GT edges)

| method | TP | FP | FN | micro edge Jaccard | size-weighted adj Jaccard (`summarise()`) |
|---|---:|---:|---:|---:|---:|
| `harmonic_v1` | 150 | 18 | 19 | **0.802139** | **0.780156** |
| `official_ilp` | 142 | 17 | 27 | 0.763441 | 0.744108 |
| `mutual_confidence` | 135 | 13 | 34 | 0.741758 | 0.725286 |
| `motion_gated` | 127 | 16 | 42 | 0.686486 | 0.673964 |

harmonic − official = **+0.036048** on the pooled panel, near-identical to the +0.037326 seen
on the dev movie alone. Paired bootstrap on the weighted score difference, 100,000 draws:

| pair | observed | paired CI95 (FP fixed) | excludes 0 |
|---|---:|---|:---:|
| official → harmonic | +0.0360 | **[+0.0111, +0.0693]** | **yes** |
| official → mutual | −0.0188 | [−0.0478, +0.0080] | no |
| official → motion | −0.0701 | [−0.1119, −0.0345] | yes |
| harmonic → mutual | −0.0549 | [−0.0973, −0.0183] | yes |
| harmonic → motion | −0.1062 | [−0.1568, −0.0651] | yes |
| mutual → motion | −0.0513 | [−0.0846, −0.0268] | yes |

False positives are held fixed here, which is the correct paired analysis: FP is a property
of the prediction, not of which GT edges you happened to resample. A variant that resamples
FP independently per method widens official→harmonic to [−0.0117, +0.0886] and loses
significance, but that variant is wrong for this question — the four methods share one
detector cache, so their false positives are strongly correlated, and drawing them
independently injects roughly 5.5 edges of purely artificial standard deviation into the
difference. Both variants are recorded in `pooled_three_sample.json`.

The two tests now agree: McNemar p = 0.0078 and a paired CI excluding zero, for the same
pair, under the same nested-TP assumption.

#### Score-level resolution is still coarse

The mean-discordance noise floor for a 169-edge panel is **δ ≥ 0.0672** (π_d = 0.0750,
dJ/dr = 1.6287). The observed harmonic advantage (0.0360) sits *below* that floor while
still being detectable by the paired test. That is not a contradiction: McNemar conditions
on the 8 discordant edges and discards the ~142 concordant ones, which carry no information
about which method is better. The floor is the right number for an *unpaired* claim ("this
run scored X, that run scored Y"); the paired CI is the right number for a *head-to-head*
comparison on a fixed panel. **Report the paired CI for A/B comparisons and the floor for
absolute claims.**

#### What is licensed now, and what still is not

**Licensed** (169 GT edges, three samples, nested-TP assumption stated):

- `motion_gated` is worse than all three others. Robust to ≥3 reversals; the firmest
  conclusion the project has. **Drop it.**
- `harmonic_v1` beats `mutual_confidence` and `motion_gated`; `official_ilp` beats
  `motion_gated`. Robust.
- `harmonic_v1` beats `official_ilp` — **but say it with the knife-edge caveat**: p = 0.0078
  against α = 0.0083, one reversed edge from failing, and resting on an unverified pairing.
  Wording: *"harmonic_v1 beats official_ilp on the three-sample panel (pooled McNemar
  p = 0.0078, paired CI [+0.011, +0.069]), under a nested-TP assumption that a single
  counter-example would overturn; confirm with the matched-edge mask before relying on it."*

**Not licensed:**

- `official_ilp` vs `mutual_confidence` — p = 0.0156 fails Bonferroni, CI contains zero.
  **Not separated.**
- Any difference at the 0.005–0.01 scale. The best-established pair has a paired CI
  half-width of ±0.029.
- Any claim about divisions. All three samples still have zero; `division_jaccard` is null
  throughout; 10% of the official score remains completely untested.
- Any claim about the `6bba_` domain — still zero coverage, still 64% of train and 50% of test.
- **Any absolute score level.** See below.

#### Absolute scores collapse off the dev movie

`official_ilp`: **0.8838 → 0.6262 → 0.7385**. The dev movie is not merely unrepresentative
(§3.1) — it is anomalously *easy*, and every headline number in the project inherits that:

| | `44b6_0113de3b` | `44b6_0b24845f` | `44b6_0c582fdc` |
|---|---:|---:|---:|
| official FP | 2 | **9** | 6 |
| official node recall | 1.000 | 0.980 | 0.972 |
| official `total_node_ratio` | 0.0093 | **0.6870** | 0.1533 |

The pooled `harmonic_v1` estimate is **0.7802**, not 0.9211. Anyone carrying 0.92 forward as
a leaderboard expectation is out by ~0.14. And because `44b6_0113de3b` and `44b6_0b24845f`
*are* two of the four leaderboard movies (§2.1), the best available leaderboard estimate for
harmonic comes from those two — 0.9211 and 0.6275 — with the two `6bba_` movies unknown.

#### The node-count penalty is now a first-order term

`total_node_ratio` per sample, and the predicted node counts behind it:

| sample | estimated nodes | official pred / ratio | harmonic pred / ratio | motion pred / ratio |
|---|---:|---|---|---|
| `44b6_0113de3b` | 25,755 | 25,994 / +0.009 | 26,301 / +0.021 | 25,143 / **−0.024** |
| `44b6_0b24845f` | 32,795 | 55,324 / **+0.687** | **57,221 / +0.745** | 50,219 / +0.531 |
| `44b6_0c582fdc` | 27,958 | 32,245 / +0.153 | 32,602 / +0.166 | 31,072 / +0.111 |

On sample 2 the detector emits **74% more nodes than the movie is estimated to contain**,
costing harmonic 7.4% of its score on that sample. On the dev movie the same term was worth
0.2%. **§4.5 understated this by a factor of ~35 because the dev movie hid it.**

Pooled, the penalty costs:

| method | weighted raw J | weighted adj J | lost to node penalty |
|---|---:|---:|---:|
| `harmonic_v1` | 0.802139 | 0.780156 | **0.0220** |
| `official_ilp` | 0.763441 | 0.744108 | 0.0193 |
| `mutual_confidence` | 0.741758 | 0.725286 | 0.0165 |
| `motion_gated` | 0.686486 | 0.673964 | 0.0125 |

**0.022 of pooled score is being discarded to node over-prediction — 61% of the entire
harmonic-over-official gain (0.036) that four association methods of work produced.**
Reducing the predicted node count is a larger and cheaper lever than association method
choice, and nobody is pulling it.

Worse, the penalty is *anti-correlated with quality*: the better a method is at association,
the more nodes it keeps, and the harder it is penalised (harmonic 57,221 vs motion 50,219 on
sample 2). The metric partially punishes the better method. Any node-count work must be
reported as a separate line item, never folded into an association comparison — protocol
P2 exists precisely for this.

---

### 4.8 FIVE-SAMPLE PANEL — final grading (1,093 GT edges)

Added 2026-08-21 once Codex completed all five samples. §4.6 remains unedited; §4.7 was the
three-sample interim. Counts read from the receipts (`panel_runs_0db_*`, `panel_runs_12df_*`
and the earlier directories), never transcribed.

| sample | GT edges | official | harmonic | mutual | motion |
|---|---:|---|---|---|---|
| `44b6_0113de3b` | 50 | 46/2/4 → 0.88379 | 48/2/2 → 0.92112 | 43/0/7 → 0.85983 | 42/2/8 → 0.80961 |
| `44b6_0b24845f` | 49 | 39/9/10 → 0.62622 | 40/10/9 → 0.62747 | 37/8/12 → 0.60938 | 35/8/14 → 0.58141 |
| `44b6_0c582fdc` | 70 | 57/6/13 → 0.73850 | 62/6/8 → 0.80224 | 55/5/15 → 0.72368 | 50/6/20 → 0.65057 |
| `44b6_0db75fae` | 151 | 133/9/18 → 0.81504 | 134/8/17 → 0.82496 | 124/4/27 → 0.78545 | 125/4/26 → 0.79509 |
| `44b6_12dfb391` | 773 | 668/81/105 → 0.78092 | 688/89/85 → 0.79629 | 648/84/125 → 0.75553 | 644/78/129 → 0.75683 |

#### Pre-registration: final grade

| sample | sign agreements of 6 | ranking matches sample 1 | harmonic − official |
|---|---:|:---:|---:|
| `44b6_0b24845f` | 6/6 | yes | +1 |
| `44b6_0c582fdc` | 6/6 | yes | +5 |
| `44b6_0db75fae` | **5/6** | **no** | +1 |
| `44b6_12dfb391` | 6/6 | yes | +20 |

Mean **5.75 of 6**, against a pre-registered H0 of **2.60** and H1 of 4.85–5.42. The observed
value exceeds even the H1/comonotone prediction. **H0 is decisively rejected**; the single
break is `44b6_0db75fae`, and it is exactly the mutual-vs-motion pair discussed below.

#### 1. harmonic_v1 over official_ilp is ESTABLISHED

Pooled edge gains **+2, +1, +5, +1, +20** → **b = 29, c = 0**, exact McNemar
**p = 3.725 × 10⁻⁹**.

Unlike the three-sample interim, this verdict is now robust:

- **Reversal sensitivity:** survives **at least 15** reversed edges out of 1,093
  (b=44, c=15 still clears Bonferroni). At three samples a *single* reversal broke it.
- **Leave-one-sample-out:** worst case is dropping the dominant `44b6_12dfb391`, leaving
  b = 9, c = 0, **p = 0.0039** — still clears α = 0.00833. The result does not depend on the
  one big movie.
- **Paired bootstrap on score:** CI95 **[+0.0099, +0.0271]**, excludes zero.

**Confidence: high, with one residual caveat.** The nested-TP assumption is still optimistic
in principle, but it no longer carries the verdict: b would have to be wrong by 15+ edges,
and the leave-one-out check removes the single-movie dependence. Report it as established.
The matched-edge mask is still worth having, but it is no longer load-bearing for this pair.

**Note the effect size shrank with evidence:** harmonic − official was +0.0373 on the dev
movie, +0.0360 pooled over three samples, and **+0.0178 pooled over five**. The early
single-movie estimate was inflated by ~2×. This is the ordinary behaviour of an effect
estimated on a tiny panel, and it is why §6 P3 exists.

#### 2. mutual_confidence vs motion_gated — NOT separable in any way worth acting on

This is the pair that flips, and it flips differently depending on what you measure.

| sample | edge gain (motion − mutual) | TP winner | score gain (motion − mutual) | score winner |
|---|---:|---|---:|---|
| `44b6_0113de3b` | −1 | mutual | −0.0502 | mutual |
| `44b6_0b24845f` | −2 | mutual | −0.0280 | mutual |
| `44b6_0c582fdc` | −5 | mutual | −0.0731 | mutual |
| `44b6_0db75fae` | **+1** | **motion** | **+0.0096** | **motion** |
| `44b6_12dfb391` | −4 | mutual | **+0.0013** | **motion** |

**TP and score disagree on `44b6_12dfb391`**: mutual recovers 4 more GT edges, yet motion
scores higher — because motion carries 6 fewer false positives (78 vs 84) and a better node
ratio (−0.001 vs +0.008). The metric reverses the edge-level ordering.

Verdicts:

- **Edge level:** b = 1, c = 12, p = 0.003418 — nominally clears Bonferroni. But it
  **fails leave-one-out**: drop `44b6_0c582fdc` and it becomes b = 1, c = 7, p = 0.0703,
  failing even α = 0.05. It also fails at just **2 reversed edges** (p = 0.0127).
- **Score level:** pooled size-weighted gap is **0.0059**, well under the panel's 0.0195
  noise floor. The paired CI is [−0.0120, −0.0006] — technically excluding zero, but by
  0.0006, which is a third of the metric's own one-edge quantum.

**Conclusion: not separated.** These two methods should be reported as tied. This is the one
pair where the five-sample panel still cannot decide, and it is precisely the pair whose
ordering depends on which averaging you pick.

##### Does the flip track a characterisation axis?

The two samples where motion wins by score are `44b6_0db75fae` and `44b6_12dfb391` — the two
densest (1.76 and 7.88 annotated nodes per frame, against 1.00–1.27 for the other three),
with the most tracks (6 and 15, against 1–2) and the most GT edges (151 and 773, against
49–70). Median displacement does *not* split them (2.33 µm for `0db75fae` sits inside the
mutual-winning range 2.07–2.88).

**But this is a hypothesis, not a finding, and it must not be reported as one.** Density,
track count and edge count rise together across this panel (§3.3) — they are one confounded
axis, not three independent confirmations. With five samples and a 3/2 split, the chance that
any given axis separates them cleanly is 1/C(5,2) = **0.10** one-sided. A 10% coincidence is
not evidence. Testing it needs samples that break the density/size confound — which no
locally available movie does.

#### 3. Micro vs macro vs size-weighted, and what the official summariser uses

| method | micro edge Jaccard | size-weighted adj (**official**) | macro (unweighted mean) |
|---|---:|---:|---:|
| `harmonic_v1` | 0.804636 | **0.797563** | 0.794414 |
| `official_ilp` | 0.785833 | **0.779765** | 0.768896 |
| `mutual_confidence` | 0.759631 | **0.754804** | 0.746774 |
| `motion_gated` | 0.752309 | **0.748935** | 0.718701 |

`official_metrics.summarise()` computes `edge_jaccard` **micro** (TP/FP/FN summed, then
Jaccard) but builds the reported `score` from `adj_edge_jaccard`, which is a **size-weighted
mean with weights wᵢ = TPᵢ + FPᵢ + FNᵢ**. Macro is not used anywhere.

Weight share under that size weighting:

| sample | share of the official score |
|---|---:|
| `44b6_0113de3b` | 4.3% |
| `44b6_0b24845f` | 4.8% |
| `44b6_0c582fdc` | 6.3% |
| `44b6_0db75fae` | 13.3% |
| `44b6_12dfb391` | **71.2%** |

**The official score for this panel is 71% one movie.** The consequence is visible in the
mutual−motion gap, which is +0.0073 micro, +0.0059 size-weighted, and **+0.0281 macro** —
4.8× larger unweighted, because macro gives the three small samples where mutual wins
decisively the same voice as the one big sample where it does not.

**What the team should do:** report **per-sample, then micro, then macro, then the official
size-weighted score** — four numbers, always. Micro and the official score answer "how will
this do on a leaderboard weighted like this panel"; macro answers "does this help on a
typical movie". A change that moves macro but not the weighted score is helping small movies
only, and vice versa. Reporting one number hides which.

#### 4. Divisions — measurable at last, and the largest untapped lever in the project

`44b6_12dfb391` holds exactly one GT division, so the term is finally live: `summarise()` no
longer drops it.

| method | div TP | div FP | div FN | division_jaccard | contribution (0.1·J) |
|---|---:|---:|---:|---:|---:|
| `official_ilp` | 0 | 0 | 1 | 0.0 | 0.0000 |
| `harmonic_v1` | 0 | **4** | 1 | 0.0 | 0.0000 |
| `mutual_confidence` | 0 | 0 | 1 | 0.0 | 0.0000 |
| `motion_gated` | 0 | 0 | 1 | 0.0 | 0.0000 |

harmonic's 4 false divisions are 1 on `44b6_0db75fae` (which has no GT division at all) and
3 on `44b6_12dfb391`.

Three consequences, in order of importance:

1. **Every method scores zero on 10% of the official metric.** Detecting the single division
   would be worth **+0.100** of score. The entire four-method association spread is
   **0.0486**. Division detection is worth **2.05× more than every association improvement
   in this project combined**, and nobody is working on it.
2. **harmonic carries a latent liability worth more than its entire edge advantage.** Its 4
   division FPs are free today only because TP = 0 makes the Jaccard 0 either way. The moment
   a method detects that division, harmonic's FPs cost real score: TP=1, FP=4 gives
   J = 0.200 → +0.020, against TP=1, FP=0 giving J = 1.000 → **+0.100**. That 0.080 gap
   dwarfs harmonic's established +0.0178 edge advantage. **Whoever works on divisions must
   fix harmonic's false-division rate first, or the association win will be spent paying for it.**
3. **One division is not a measurement.** `division_jaccard` on n=1 has no resolution
   whatsoever — it can only be 0 or 1. Everything above is arithmetic about what the term
   *would* pay, not an estimate of any method's division ability. P8 still applies: this
   panel cannot test division handling, it can only price it.

#### 5. Updated noise floor

1,093 GT edges, mean pairwise discordance 0.0406, dJ/dr = 1.6328 →
**δ resolvable ≥ 0.0195**. Compare the panel's history: 0.1356 (one movie) → 0.0672 (three
samples) → **0.0195** (five samples). The panel improved 7× and is now within reach of the
δ = 0.01 target set in §4.4 — that would need roughly 4× more GT edges again, i.e. the
~43-movie panel of §7 item 3.

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

**Updated 2026-08-21 with all five samples measured.** The tier assignment is unchanged —
the locked pair are leaderboard test movies (§2.1) and that reason does not expire. What the
measured scores add:

| tier | samples | GT edges | share of official (size-weighted) score |
|---|---|---:|---|
| dev | `44b6_12dfb391`, `44b6_0db75fae`, `44b6_0c582fdc` | 994 | 12dfb391 alone = **77.8%** of the tier |
| locked | `44b6_0113de3b`, `44b6_0b24845f` | 99 | — |

The dev tier resolves **δ ≥ 0.021** (recomputed on the measured discordance), holds the
panel's only division, and spans density 1.00 → 7.88 nodes/frame. It correctly ranks
`harmonic_v1` over `official_ilp` on its own (b = 26, c = 0 across its three samples).

**The new problem this exposes: concentration.** `44b6_12dfb391` is 71.2% of the full
panel's official score and 77.8% of the dev tier's. Any conclusion drawn from a single
aggregate number is, to first order, a statement about one movie. This is not fixed by tier
membership; it is fixed by the reporting rule P10 below, which is now mandatory.

**Honest limits that remain.** One domain (`44b6_` only), one division, zero gap-closing
coverage, and density confounded with size. The dev tier is a working panel now — it settles
harmonic-vs-official — but it still cannot test division handling, cannot say anything about
`6bba_`, and cannot separate `mutual_confidence` from `motion_gated`. §7 says how to grow it.

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

**P10 — no aggregate claim without a leave-one-sample-out check.** `44b6_12dfb391` carries
71.2% of the official score's weight, so an aggregate can be moved by one movie alone. Every
comparison that is reported as a conclusion must also report the worst-case result with each
sample dropped in turn. If the verdict flips when any single sample is removed, it is
reported as **fragile** and must not drive a decision. This rule is what separated the
established `harmonic_v1` > `official_ilp` result (survives every drop) from the fragile
`mutual_confidence` > `motion_gated` one (dies when `44b6_0c582fdc` is dropped) in §4.8.
`scripts/score_prereg_and_pool.py` computes this automatically.

**P11 — report four numbers, not one.** Per-sample table, then micro edge Jaccard, then
macro (unweighted mean), then the official size-weighted score. The mutual−motion gap is
+0.0073 micro, +0.0059 size-weighted and +0.0281 macro (§4.8); reporting any single one of
those hides which movies the change actually helped.

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

```bash
# 6. grade the pre-registration and pool all completed samples
PYTHONPATH=scripts python scripts/score_prereg_and_pool.py \
  --race-root <CODEX>/artifacts/detector_fixed_race \
  --prereg artifacts/validation_design/prereg_sample2.json \
  --n-boot 100000 \
  --out artifacts/validation_design/pooled_three_sample.json
```
