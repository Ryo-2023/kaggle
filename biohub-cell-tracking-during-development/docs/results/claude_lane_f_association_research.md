# Lane F — association scoring research

Branch `claude/f-association`. Detector fixed; association only. No detector
inference, no checkpoint load, no zarr read anywhere in this document.

Sections 1–5 are **pre-registered**: they were written and committed before any
graph was built or any ILP was solved, so the adopt/reject criteria cannot be
chosen after seeing a score. Section 7 holds measured results only.

---

## 1. What the pipeline actually computes

Verified by reading the pinned upstream predictor and the tracksdata solver,
not by assumption.

**The softmax is over sources, not targets.** `predict_unet_transformer.py:456`
applies `torch.softmax(raw, dim=0)` to a `(n_src, n_tgt)` matrix, so

```
p[s, t] = P(parent = s | child = t)
```

Each column is a distribution over the possible parents of one t+1 detection.
The upstream docstring at line 58 calls this "row-normalised over t+1 nodes",
which is the opposite of what the code does. The convention matters:

* Because every column sums to 1, **at most one entry per column can exceed
  0.5.** The fixed threshold is therefore not a probability cut at all — it is
  the rule "admit the column's argmax if and only if it holds an outright
  majority of the parent posterior". Measured on the dev cache: 24,183
  admitted candidates across 24,183 distinct targets, i.e. never two parents
  for one child.
* Column normalisation is division-friendly and row normalisation is not. Two
  children of one dividing parent live in two *different* columns, so both can
  independently put full mass on the shared parent. Any symmetric
  row-normalising rule (plain dual softmax, Sinkhorn) would force a dividing
  parent to split its row mass and would suppress exactly the events worth
  0.1 of the final score.

**The reverse pass never leaves the source axis.** `upstream_adapter.py:559`
transposes the reverse model output back into `(source, target)` orientation
and `_build_edge_arrays` softmaxes it over axis 0 again. So `forward_probability`
and `reverse_probability` answer the *same* question with the same
normalisation; the second pass is a re-estimate, not a complementary
constraint. The row direction `P(child | parent)` is computed nowhere in the
pipeline. That unused half is the cheapest untapped signal available.

**The ILP is almost a no-op.** `ILPSolver` minimises cost with
`edge_weight = -1.0 * edge_prob`, `appearance = disappearance = 0.1`,
`division = 1.0`. Its flow constraints are
`appear[v] + Σ_in = node[v]` and `disappear[u] + Σ_out = node[u] + division[u]`.
Consequences, all of which follow arithmetically:

| quantity | value | consequence |
|---|---|---|
| break-even for linking two isolated nodes | `p > 0.2` | every candidate that clears 0.5 is worth taking |
| in-degree bound | `≤ 1`, hard | redundant: the 0.5 cut already guarantees it |
| out-degree bound | `≤ 1`, or `≤ 2` at cost 1.0 | the only real decision |
| break-even for a second child | `p₂ > 0.9` | divisions are priced out of existence |
| isolated node | costs `0.2`, gains nothing | switched off, hence dropped from the GEFF |

So the ILP's entire function here is to break out-degree conflicts, and the
way it breaks them is by refusing divisions. On the dev cache 630 sources are
claimed by two or more targets and 24,183 candidates become 23,536 selected
edges: 647 edges deleted, essentially all of them second children priced at
`division_weight = 1.0` against a posterior that must exceed 0.9.

**The metric barely punishes over-prediction.** `metrics.py:194` sets
`pred_valid = out_valid OR in_valid`, so a predicted edge enters the Jaccard
denominator only if it touches a *matched* GT node of the right degree. With
52 annotated nodes against 26k predictions, edges away from the annotation are
free. The node penalty is `J·(1 − 0.1·total_node_ratio)`; the 307 extra nodes
harmonic emits cost 0.0011 while its 2 extra true edges earn 0.037. Recall is
roughly 34× cheaper than precision on this metric.

## 2. Why the harmonic mean helps, mechanically

The published fusion is `h = 1/((1-w)/p + w/q)` with `w = 0.20`. Two separate
mechanisms are bundled inside it and the project has been treating them as one.

**Mechanism A — an asymmetric agreement veto.** The weighted harmonic mean is
a soft minimum: `0.5 ≤ h / min(p/(1-w), q/w) ≤ 1` everywhere (asserted over
5,000 synthetic pairs in `test_weighted_harmonic_is_a_soft_minimum_with_ratio_four`).
With `w = 0.20` that reads **"cap the forward probability at four times the
reverse probability"**. The asymmetry is total: agreement can lift a score by
at most `1/(1-w) = 1.25×`, while disagreement can crush it without bound,
down to `q/w = 5q`. So "both directions must agree" is right in direction but
the free parameter is not `w`, it is the veto ratio `ρ = (1-w)/w = 4`.

**Mechanism B — an accidental per-column power transform.** `harmonic.py:84-92`
does not stop where the published formula stops. It re-standardises the fused
log-probabilities to the forward per-column mean and standard deviation, and
upstream then applies `softmax(dim=0)` to that return value. A softmax ignores
the additive term, so the whole re-alignment collapses to

```
final ∝ ĥ ^ γ(t),    γ(t) = clamp( std_s(F[:,t]) / std_s(log ĥ[:,t]), 0.5, 2.0 )
```

a per-target, data-dependent **temperature**. Lane A verified the same algebra
independently (residual 2.3e-15). This is not a normalisation detail: a
temperature moves entries across the fixed 0.5 cut, which changes the
candidate count, which changes the edge count and the prediction node count.
Measured on the dev cache, `forward_only` admits 24,183 candidates and
`published_harmonic` admits 25,023 — **840 extra targets whose argmax was
pushed over the majority line**. That is the same direction as the observed
+669 selected edges and +307 prediction nodes.

**Corroboration from Codex's own sweep.** `f405c00`/`a07c3be` swept
`w ∈ {0.10, 0.20, 0.30}`, i.e. veto ratio `ρ ∈ {9, 4, 2.33}` — a fourfold
change in veto strength. Edge TP moved by at most one edge on one of three
samples (48/48/48, 40/40/39, 62/62/61). If mechanism A were doing the work,
a 4× change in the veto ratio should move something. It does not. That is
evidence, not proof, that mechanism B carries the gain.

**Therefore:** plain harmonic is a crude proxy for agreement *and* an
undeclared temperature. An explicit disagreement term should beat it only if
mechanism A is real. The pre-registered experiment below decides which.

## 3. Statistics, decided before running

Primary metric: `final_score` (= adjusted edge Jaccard; all three available
samples have zero divisions, so the division term is dropped by the official
summariser). Secondary: edge TP / FP / FN.

Pooled evidence available today: 3 samples, 169 GT edges
(50 + 49 + 70). Reference totals — official 142 TP / 17 FP / 27 FN,
harmonic 150 TP / 18 FP / 19 FN.

**The single-sample result was never significant.** On `44b6_0113de3b` alone,
harmonic beat official by 46→48 TP with FP unchanged: two discordant GT edges,
both in one direction. McNemar exact, `b = 2, c = 0`, gives **p = 0.5**. The
+0.037 headline is one coin flip.

Three samples change the picture but do not settle it. The TP delta is +8,
which bounds McNemar only if the discordant set is one-sided: `b = 8, c = 0`
gives `p = 0.0078`, but `b = 14, c = 6` gives `p = 0.115`. **The TP delta alone
cannot establish significance**; the per-GT-edge match masks are required and
are not currently emitted by `evaluate_prediction`. Recorded as a gap, not
papered over. Lane B owns the power analysis; this lane does not block on it.

Sample-size target: for a two-sided McNemar at p < 0.05 with a one-sided
discordant set, at least 6 discordant edges are needed (`2·0.5⁶ = 0.031`).
Observed discordance is roughly 4–8% of GT edges, so ~150 GT edges is the
floor and the three-sample pool is at that floor, not above it.

**Divisions cannot be validated locally at all.** Four of five panel samples
have zero divisions; `44b6_12dfb391` has one division source. `n = 1` gives
`division_jaccard ∈ {0, 1}` and no power whatsoever. Any division-aware claim
in this document is therefore argued from the ILP cost algebra, never from a
measured local gain.

## 4. Adopt / reject criteria — fixed before the first ILP run

* **C1 (harness validity, blocking).** `forward_only` must reproduce
  `official_ilp` and `published_harmonic` must reproduce `harmonic_v1`, on TP,
  FP, FN, candidate count and `final_score`. If either fails, every other
  number in section 7 is void.
* **C2 (screen).** A rule advances to multi-sample evaluation only if, on the
  dev sample, `TP ≥ 46` and `FP ≤ 2` (i.e. no worse than the official control
  on either axis).
* **C3 (candidate adoption).** A rule is a *candidate improvement* only if it
  beats `harmonic_v1` on pooled TP with pooled FP not worse, across all
  samples run.
* **C4 (adoption).** Adoption additionally requires McNemar exact `p < 0.05`
  on pooled per-GT-edge outcomes. **No rule can satisfy C4 today**, because the
  per-edge masks are not emitted. Every section 7 result is therefore reported
  as *screened*, never as *adopted*.
* **C5 (no fitting).** Any rule whose constants were chosen after seeing a
  score is rejected outright regardless of its number. All constants in this
  lane are fixed a priori: `4 = (1-w)/w` from the published weight, `2 =`
  twice the runner-up (textbook ratio test), `0.2 =` the ILP's own
  appearance + disappearance break-even.

## 5. Hypothesis table

`p = softmax_col(F)`, `q = softmax_col(R)`, `F`/`R` the cached forward and
reverse logits in source-by-target orientation, `d` physical distance in µm.
"Control" names what the rule is compared against. Rank is the pre-registered
run order: most informative first.

| rank | rule | hypothesis | formula | control | metric | adopt criterion |
|---:|---|---|---|---|---|---|
| — | `forward_only` | harness reproduces the baseline | `s = p` | `official_ilp` | TP/FP/FN, score | C1 exact match |
| — | `published_harmonic` | harness reproduces the best | published fusion + power transform | `harmonic_v1` | TP/FP/FN, score | C1 exact match |
| 1 | `published_harmonic_no_temperature` | **ablation A.** If the published formula *as published* keeps the gain, the mechanism is agreement | fusion with `harmonic.py:84-92` dropped | `published_harmonic` | ΔTP | C2, C3 |
| 2 | `forward_published_temperature` | **ablation B.** If sharpening alone keeps the gain, the reverse pass is decoration | `s = norm(p^γ)`, γ from the published clamp, no reverse info | `published_harmonic` | ΔTP | C2, C3 |
| 3 | `entropy_temperature` | sharpening with no reverse pass and no published constant | `s = norm(p^clamp(log N / H(p), 0.5, 2))` | `forward_only` | ΔTP | C2, C3 |
| 4 | `column_dominance` | the losses are diluted columns with an unambiguous winner under 0.5 | accept `p > 0.5` **or** (argmax ∧ top1 ≥ 2·top2 ∧ top1 ≥ 0.2) | `forward_only` | ΔTP, ΔFP | C2, C3 |
| 5 | `veto_ratio` | explicit agreement veto, the clean limit of harmonic | `s = norm(min(p, 4q))` | `published_harmonic` | ΔTP | C2, C3 |
| 6 | `dual_softmax_top2` | the unused row direction, made division-tolerant | `s = norm(p·√row_top2_share(F))` | `forward_only` | ΔTP, ΔFP | C2, C3 |
| 7 | `lane_f_v1` | veto × division-tolerant row consistency | `s = norm(min(p,4q)·√row_top2_share(F))` | `published_harmonic` | ΔTP | C3 |
| 8 | `lane_f_v1_dominance` | agreement and sharpening addressed separately, not bundled | rank 7 + dominance admission | `published_harmonic` | ΔTP | C3 |
| 9 | `dual_softmax` | row consistency from the forward matrix alone | `s = norm(√(softmax_col F · softmax_row F))` | `forward_only` | ΔTP | C2 |
| 10 | `dual_softmax_bidirectional` | use the reverse pass in its native direction | `s = norm(√(softmax_col F · softmax_row R))` | `published_harmonic` | ΔTP | C2 |
| 11 | `disagreement_symmetric` | penalise disagreement in both directions | `s = norm(p·e^{-|log p − log q|})` | `published_harmonic` | ΔTP | C3 |
| 12 | `disagreement_one_sided` | penalise only a less-confident reverse | `s = norm(p·min(1, q/p))` | `published_harmonic` | ΔTP | C3 |
| 13 | `min_rule` / `max_rule` | hard AND / hard OR fusion | `s = norm(min/max(p,q))` | `published_harmonic` | ΔTP | C2 |
| 14 | `geometric_mean` | ≡ `softmax_col((F+R)/2)`; product of experts at temperature 2 | `s = norm(√(pq))` | `mutual_confidence` | ΔTP | C2 |
| 15 | `logit_sum` | product of experts at temperature 1 | `s = softmax_col(F+R)` | `geometric_mean` | ΔTP | C2 |
| 16 | `arithmetic_mean` | variance reduction without veto | `s = (p+q)/2` | `published_harmonic` | ΔTP | C2 |
| 17 | `harmonic_mean` | symmetric veto, ratio 1 | `s = norm(1/(0.5/p + 0.5/q))` | `published_harmonic` | ΔTP | C2 |
| 18 | `reverse_only` | is the reverse pass alone competitive? | `s = q` | `forward_only` | ΔTP | diagnostic |
| 19 | `mutual_confidence_unnormalised` | Codex's rule; documents a scale defect | `s = √(pq)`, no renormalisation | `geometric_mean` | candidate count | diagnostic |
| 20 | `distance_prior_adaptive` | soft displacement prior, scale from the data | `s = norm(p·e^{−(d/σ)²/2})`, `σ = median_t min_s d` | `motion_gated` | ΔTP, ΔFP | C2 |
| 21 | `mutual_top2_gate` | pure precision filter | `s = p` where `t` is in `s`'s row top-2 | `forward_only` | ΔFP | C2 |
| 22 | `motion_gated` | Codex's control; the hard 12 µm cut | `s = p·e^{−0.05d}`, 0 beyond 12 µm | `forward_only` | ΔTP | control |

### Rejected before running, on analysis rather than measurement

* **Explicit local-density reweighting.** Any factor that depends only on the
  target index cancels under column renormalisation, so
  `s = norm(p · f(density(t)))` is *provably* the identity
  (`test_per_column_multiplier_is_a_no_op_after_renormalisation`). Density can
  only act through a per-column *exponent* or a per-column *threshold*.
  `column_dominance` is the scale-free version: comparing top-1 against top-2
  adapts to local density automatically, because a dense neighbourhood
  produces a competitive runner-up.
* **Gap-conditioned scoring (Δt ≥ 2).** Impossible from this cache: every
  candidate has `delta_t = 1`, because the upstream window only ever pairs
  `(t, t+1)`. Consequence worth stating on its own: a missed detection can
  never be bridged, so one missed node destroys two GT edges. Gap closing
  requires a change to candidate *generation*, not to scoring.
* **Symmetric dual softmax / Sinkhorn as the primary score.** Row
  normalisation forces a dividing parent to split its mass across two
  children, pushing both under the 0.5 majority line. It optimises linking by
  destroying divisions, which are worth 0.1 of the final score.
  `row_top_two_share` is the division-tolerant replacement: two comparable
  children both score 1.0, a third-best child is suppressed, and the value is
  independent of how many also-rans the row contains.
* **Track-history / velocity rescoring, second pass.** Designed but not
  implemented in this lane: it needs the solved graph from pass one to define
  a per-track velocity, so it is not a pure function of one frame pair and
  does not fit the interface. Queued, not claimed.

## 6. On the ILP, concretely

Three changes follow from the cost algebra in section 1, ranked by expected
value. None has been measured; all are stated as predictions.

1. **`division_weight = 1.0` makes divisions arithmetically impossible.** A
   second child must clear `p₂ > 0.9` on a column-normalised posterior. Every
   local run reports `division_tp/fp/fn = 0/0/0`, which is consistent with
   "the solver never proposes one". On the real leaderboard, where divisions
   exist, `division_jaccard = 0` costs the full `0.1 · SCORE_DIVISION_WEIGHT`
   term — **2.7× the entire measured harmonic gain**. This is the largest
   untested lever in the project. The break-even that would admit a second
   child at the same confidence the pipeline already accepts for a first child
   (0.5) is `division_weight = 0.6`, since `division_weight − 0.1 < p₂`.
2. **Score calibration and the ILP interact only through the threshold.**
   Because `p > 0.5 ≫ 0.2` for every admitted candidate, the ILP never rejects
   an edge on price; the 0.5 cut alone decides the edge set. A rule that
   sharpens (raising `p₂` above 0.9) is therefore also the only kind of rule
   that can turn divisions on. `entropy_temperature` and the published power
   transform both do this as a side effect, unmeasured and undeclared.
3. **Appearance and disappearance at 0.1 are far too cheap to shape anything.**
   They matter only in that an isolated node costs 0.2 and is therefore
   switched off and dropped from the GEFF — which is what makes the prediction
   node count, and hence `total_node_ratio`, a function of the association
   rule. Raising them would suppress short spurious tracklets, at a node-count
   penalty of 0.1 per unit ratio, i.e. almost nothing.

## 7. Measured results

Real runs only, all from Codex's own READY caches, same ILP config, same
official metric, same `max_distance = 7.0` and per-sample scale.

### 7.1 Harness validity (criterion C1) — PASSED

`forward_only` reproduces `official_ilp` to the last digit on four different
caches, and `published_harmonic` reproduces `harmonic_v1` on the dev cache
including node and edge counts.

| cache | rule | measured | published reference |
|---|---|---|---|
| `44b6_0113de3b` | `forward_only` | 46/2/4  0.8837944835207503 | 46/2/4  0.8837944835 |
| `44b6_0113de3b` | `published_harmonic` | 48/2/2  0.9211200215044129 | 48/2/2  0.9211200215 |
| `44b6_0b24845f` | `forward_only` | 39/9/10  0.6262213541803576 | 39/9/10  0.6262213542 |
| `44b6_0c582fdc` | `forward_only` | 57/6/13  0.738499713856499 | 57/6/13  0.7384997139 |
| `44b6_12dfb391` | `forward_only` | 668/81/105  0.7809215555664836 | 668/81/105  0.7809215556 |

### 7.2 The mechanism ablation, dev sample

| rule | reverse pass used | TP/FP/FN | final score | candidates |
|---|---|---|---:|---:|
| `forward_only` | no | 46/2/4 | 0.8837944835207503 | 24,183 |
| `published_harmonic` | yes | 48/2/2 | 0.9211200215044129 | 25,023 |
| `published_harmonic_no_temperature` | yes | 48/2/2 | 0.9213458178397025 | 24,791 |
| `forward_published_temperature` | only to set γ | 48/2/2 | 0.9216253752072040 | 24,662 |
| `entropy_temperature` | **no** | 48/2/2 | 0.9205429864253395 | 26,198 |
| `column_dominance` | no | 46/2/4 | 0.8831178411958842 | 24,824 |

Every rule that applies a per-column sharpening reaches 48 TP. The rule that
only *readmits* sub-threshold argmaxes (`column_dominance`, +641 candidates)
reaches 46. So the mechanism is not simply "let more candidates through"; the
temperature also re-ranks contested columns. The four 48-TP rules differ in
score only through the `total_node_ratio` penalty, not through edge accuracy.

### 7.3 `entropy_temperature` on all five samples — no reverse pass at all

| sample | GT edges | official | harmonic | `entropy_temperature` |
|---|---:|---|---|---|
| `44b6_0113de3b` | 50 | 46/2/4 | 48/2/2 | 48/2/2 |
| `44b6_0b24845f` | 49 | 39/9/10 | 40/10/9 | **42**/11/7 |
| `44b6_0c582fdc` | 70 | 57/6/13 | 62/6/8 | 62/7/8 |
| `44b6_0db75fae` | 151 | 133/9/18 | 134/8/17 | **139**/12/12 |
| `44b6_12dfb391` | 773 | 668/81/105 | 688/89/85 | **694**/106/79 |

Pooled over all five samples (1,093 GT edges):

| method | TP | FP | FN | pooled edge Jaccard |
|---|---:|---:|---:|---:|
| official | 943 | 107 | 150 | 0.785833 |
| harmonic | 972 | 115 | 121 | **0.804636** |
| `entropy_temperature` | **985** | 138 | 108 | 0.800162 |

Per-sample TP delta of `entropy_temperature` over harmonic: `+0, +2, +0, +5, +6`.
Over official: `+2, +3, +5, +6, +26`. It is never below harmonic on any sample.

### 7.4 Verdict: the agreement explanation is dead; the rule is not adopted

**Killed.** "Harmonic wins because the two directions must agree" is false. A
rule that never reads the reverse logits recovers at least as many true edges
as harmonic on every one of five samples, and more on three of them. The
reverse detector pass — which doubles edge-model inference — is not what
produces the recall gain.

**Not adopted, and not better.** The advantage splits cleanly:

* the **recall** half of harmonic's gain is a temperature effect and is fully
  reproducible from the forward logits alone;
* the **precision** half is not. `entropy_temperature` buys its extra 13
  pooled TP with 23 extra pooled FP, and loses on the metric that counts:
  pooled edge Jaccard 0.8002 against harmonic's 0.8046.

Pre-registered criterion C3 required beating harmonic on pooled TP *with
pooled FP not worse*. FP rises 115 → 138, so **C3 fails as written** and the
rule is screened, not adopted. C4 (McNemar) remains unevaluable.

Worth stating against my own interest: had I pre-registered "pooled edge
Jaccard" instead of "TP with FP not worse", the verdict would still be a
rejection, but had I pre-registered "pooled TP" alone it would have been an
acceptance. Three plausible criteria, three different answers on the same
numbers. That is precisely why the criterion has to be fixed before the run,
and it is why the +0.037 headline deserved this much scrutiny.

### 7.5 Structural confirmation, ground-truth-free

Candidate diagnostics on the dev cache, 26,663 target slots across 99 frame
pairs:

| rule | candidates | targets with a parent | contested sources | recoverable targets |
|---|---:|---:|---:|---:|
| `forward_only` | 24,183 | 24,183 | 630 | 2,342 |
| `published_harmonic` | 25,023 | 25,023 | 824 | 1,541 |
| `entropy_temperature` | 26,198 | 26,198 | 1,334 | 441 |

`candidates == targets with a parent` exactly, for every rule: the column
softmax makes two parents for one child arithmetically impossible, confirming
that the ILP's in-degree constraint is redundant and that the 0.5 cut is a
majority test, not a probability test.

Sharpening consumes the recoverable pool (2,342 → 441) and roughly doubles
the number of contested sources (630 → 1,334). More contested sources means
more out-degree decisions for the ILP, which is exactly where the extra false
positives and the spurious divisions come from.

### 7.6 Divisions

Sharpening does switch divisions on, as section 6 predicted, but it switches
on the wrong ones. On `44b6_12dfb391` (the only sample with a GT division),
`forward_only` gives `division 0/0/1` and `entropy_temperature` gives
`division 0/16/1`: sixteen false divisions, still not the true one.
`division_jaccard` is `0.0` in both cases, so the 0.1 division term of the
official score contributes exactly nothing, for every method measured.

