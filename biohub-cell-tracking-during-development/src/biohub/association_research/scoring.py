"""Calibration-free association scoring rules for the detector-fixed race.

Every rule in this module is a pure function of one frame pair's detector
output.  Nothing here reads images, loads a checkpoint, or touches ground
truth; the only inputs are the arrays already present in the detector cache
contract (``forward_logit``, ``reverse_logit``, ``physical_distance``) plus
node coordinates.  That keeps the rules cheap enough to replay many times
against a materialised cache.

Normalisation convention
------------------------
The pinned upstream predictor turns a raw ``(N_source, N_target)`` logit
matrix into probabilities with ``torch.softmax(raw, dim=0)``, i.e. it
normalises over **sources**.  Column ``t`` of that matrix is therefore a
distribution over the possible *parents* of target ``t``:

    p[s, t] = P(parent = s | child = t)

Every candidate is then admitted by the fixed rule ``p > 0.5``.  A scoring
rule that changes the overall scale of ``p`` silently changes what that fixed
threshold means, so unless a rule is explicitly documented as a gate, each
rule here returns a **column-stochastic** matrix.  ``renormalise_columns`` is
applied for exactly that reason.

Two consequences of the convention are used repeatedly below and are proved
in the unit tests:

* Multiplying a column-normalised score by any factor that depends only on
  the target index is a no-op.  Purely density- or column-indexed reweighting
  therefore cannot change the selected edge set; only a per-column *exponent*
  (a temperature) or a per-column *threshold* can.
* Raising a column to a power ``gamma`` and renormalising is a temperature
  change.  It moves entries across the fixed ``0.5`` threshold, which changes
  the candidate count, which changes the prediction node count.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

EPSILON = 1e-12
"""Floor used wherever a probability enters a division or a logarithm."""

PUBLISHED_REVERSE_WEIGHT = 0.20
"""``w`` from the published harmonic fusion (Togashi v18, vendored by Codex)."""

PUBLISHED_VETO_RATIO = (1.0 - PUBLISHED_REVERSE_WEIGHT) / PUBLISHED_REVERSE_WEIGHT
"""``(1-w)/w = 4``: the reverse/forward ratio at which the harmonic mean flips.

The weighted harmonic mean ``1/((1-w)/p + w/q)`` is within a factor of two of
``min(p/(1-w), q/w)`` everywhere, so the published fusion is a soft version of
"cap the forward probability at ``(1-w)/w`` times the reverse probability".
"""

PUBLISHED_SCALE_CLAMP = (0.5, 2.0)
"""Clamp the published fusion applies to its per-column logit rescaling."""

ILP_BREAK_EVEN_PROBABILITY = 0.20
"""``appearance_weight + disappearance_weight`` in the pinned ILP config.

The tracksdata ILP minimises ``-1.0 * edge_prob`` per selected edge against
``+0.1`` for an appearance and ``+0.1`` for a disappearance, so linking two
otherwise isolated detections pays for itself as soon as ``edge_prob > 0.2``.
Any acceptance floor below that value proposes edges the ILP would rather
take than leave, which is why it is used as the floor of the dominance rule
rather than a number fitted to observed scores.
"""

RUNNER_UP_RATIO = 2.0
"""Scale-free dominance ratio (top-1 at least twice the runner-up)."""


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


def _as_matrix(name: str, value: np.ndarray) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2:
        raise ValueError(f"{name} must be a 2-D (N_source, N_target) matrix, got shape {array.shape}")
    if array.size and not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite values")
    return array


def column_softmax(logits: np.ndarray) -> np.ndarray:
    """Softmax over the **source** axis: ``P(parent = s | child = t)``.

    This reproduces the pinned upstream ``torch.softmax(raw, dim=0)``.
    """

    array = _as_matrix("logits", logits)
    if array.size == 0:
        return array
    shifted = array - array.max(axis=0, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / np.maximum(exponentiated.sum(axis=0, keepdims=True), EPSILON)


def row_softmax(logits: np.ndarray) -> np.ndarray:
    """Softmax over the **target** axis: ``P(child = t | parent = s)``.

    The pinned pipeline never computes this direction.  It is the half of the
    matching problem that the current bidirectional fusion throws away: the
    reverse model pass is transposed back into the source axis and softmaxed
    over sources again, so both fused terms answer the same question.
    """

    array = _as_matrix("logits", logits)
    if array.size == 0:
        return array
    shifted = array - array.max(axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    return exponentiated / np.maximum(exponentiated.sum(axis=1, keepdims=True), EPSILON)


def renormalise_columns(scores: np.ndarray) -> np.ndarray:
    """Rescale each column to sum to one, preserving relative order."""

    array = _as_matrix("scores", scores)
    if array.size == 0:
        return array
    if np.any(array < 0.0):
        raise ValueError("scores must be non-negative before column renormalisation")
    return array / np.maximum(array.sum(axis=0, keepdims=True), EPSILON)


def column_temperature(scores: np.ndarray, gamma: np.ndarray | float) -> np.ndarray:
    """Apply a per-column exponent and renormalise.

    ``gamma > 1`` sharpens a column (its top entry gains mass), ``gamma < 1``
    flattens it.  Unlike a per-column multiplier, this is not a no-op.
    """

    array = _as_matrix("scores", scores)
    if array.size == 0:
        return array
    exponent = np.asarray(gamma, dtype=np.float64)
    if exponent.ndim == 1:
        exponent = exponent[None, :]
    powered = np.exp(exponent * np.log(np.maximum(array, EPSILON)))
    return renormalise_columns(powered)


def column_entropy(probabilities: np.ndarray) -> np.ndarray:
    """Shannon entropy (nats) of each column."""

    array = _as_matrix("probabilities", probabilities)
    if array.size == 0:
        return np.zeros((array.shape[1],), dtype=np.float64)
    safe = np.maximum(array, EPSILON)
    return -(safe * np.log(safe)).sum(axis=0)


def top_two_by_column(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the largest and second-largest value of every column."""

    array = _as_matrix("scores", scores)
    if array.size == 0:
        empty = np.zeros((array.shape[1],), dtype=np.float64)
        return empty, empty
    if array.shape[0] == 1:
        return array[0].copy(), np.zeros((array.shape[1],), dtype=np.float64)
    partitioned = np.partition(array, -2, axis=0)
    return partitioned[-1].copy(), partitioned[-2].copy()


def row_top_two_share(logits: np.ndarray) -> np.ndarray:
    """Division-tolerant row dominance in ``(0, 1]``.

    A plain row softmax asks "is ``t`` the unique child of ``s``".  That is the
    wrong question for this problem: a dividing parent has two children, so
    requiring a peaked row actively penalises exactly the events the score is
    supposed to reward.  This helper compares each entry against the mean of
    its own row's two largest entries and caps the result at one, so:

    * a parent with one clearly best child scores that child ``1.0`` and
      suppresses the rest;
    * a parent whose two best children are comparable scores **both** ``1.0``,
      which is what a division looks like;
    * a target that is only a source's third-best child is discounted, which is
      the contention signal the column direction cannot see.

    It carries no fitted constant and no dependence on how many also-rans the
    row contains.
    """

    array = _as_matrix("logits", logits)
    if array.size == 0:
        return array
    shifted = array - array.max(axis=1, keepdims=True)
    exponentiated = np.exp(shifted)
    if array.shape[1] == 1:
        return np.ones_like(array)
    partitioned = np.partition(exponentiated, -2, axis=1)
    top_two_sum = partitioned[:, -1] + partitioned[:, -2]
    share = 2.0 * exponentiated / np.maximum(top_two_sum[:, None], EPSILON)
    return np.minimum(share, 1.0)


def row_top_two_mask(logits: np.ndarray) -> np.ndarray:
    """Boolean mask of each row's two largest entries.

    "``t`` is one of source ``s``'s two best children" is completely scale
    free and needs no threshold, which is why it is used as the gate rather
    than a cut on :func:`row_top_two_share`.
    """

    array = _as_matrix("logits", logits)
    if array.size == 0:
        return np.zeros(array.shape, dtype=bool)
    if array.shape[1] <= 2:
        return np.ones(array.shape, dtype=bool)
    second_largest = np.partition(array, -2, axis=1)[:, -2]
    return array >= second_largest[:, None]


def nearest_source_scale(distance: np.ndarray) -> float:
    """GT-free length scale for one frame pair, in the distance's own units.

    The median over targets of the distance to that target's nearest candidate
    source.  It is derived from detections only, adapts per frame pair, and
    involves no fitted constant.
    """

    array = _as_matrix("distance", distance)
    if array.size == 0:
        return 1.0
    nearest = array.min(axis=0)
    scale = float(np.median(nearest))
    return scale if np.isfinite(scale) and scale > 0.0 else 1.0


def local_source_density(distance: np.ndarray, radius: float) -> np.ndarray:
    """Number of candidate sources within ``radius`` of each target."""

    array = _as_matrix("distance", distance)
    if array.size == 0:
        return np.zeros((array.shape[1],), dtype=np.float64)
    return (array <= float(radius)).sum(axis=0).astype(np.float64)


# ---------------------------------------------------------------------------
# published fusion, reimplemented in numpy
# ---------------------------------------------------------------------------


def published_harmonic_probability(
    forward_logits: np.ndarray,
    reverse_logits: np.ndarray,
    *,
    reverse_weight: float = PUBLISHED_REVERSE_WEIGHT,
    apply_temperature: bool = True,
) -> np.ndarray:
    """Reimplement ``fuse_harmonic_logits`` + final softmax without torch.

    ``reverse_logits`` is expected in the cache's source-by-target
    orientation, i.e. already transposed out of the model's native
    target-by-source output.

    Setting ``apply_temperature=False`` removes the final per-column rescaling
    that the published code applies to the fused logits.  That switch is the
    ablation that separates the two mechanisms bundled inside the published
    method: agreement (the harmonic mean itself) and sharpening (the
    rescaling).
    """

    forward = _as_matrix("forward_logits", forward_logits)
    reverse = _as_matrix("reverse_logits", reverse_logits)
    if forward.shape != reverse.shape:
        raise ValueError("forward and reverse logits must have the same shape")
    weight = float(reverse_weight)
    if not 0.0 < weight <= 0.35:
        raise ValueError("reverse_weight must be in (0, 0.35] to match the published method")
    if forward.size == 0:
        return forward

    forward_centre = forward.mean(axis=0, keepdims=True)
    forward_scale = np.maximum(forward.std(axis=0, keepdims=True), 1e-4)
    reverse_centre = reverse.mean(axis=0, keepdims=True)
    reverse_scale = np.maximum(reverse.std(axis=0, keepdims=True), 1e-4)
    ratio = np.clip(forward_scale / reverse_scale, *PUBLISHED_SCALE_CLAMP)
    reverse_aligned = (reverse - reverse_centre) * ratio + forward_centre

    forward_probability = np.maximum(column_softmax(forward), 1e-8)
    reverse_probability = np.maximum(column_softmax(reverse_aligned), 1e-8)
    harmonic = 1.0 / ((1.0 - weight) / forward_probability + weight / reverse_probability)
    harmonic = harmonic / np.maximum(harmonic.sum(axis=0, keepdims=True), 1e-8)
    if not apply_temperature:
        return harmonic

    harmonic_logits = np.log(np.maximum(harmonic, 1e-8))
    harmonic_centre = harmonic_logits.mean(axis=0, keepdims=True)
    harmonic_scale = np.maximum(harmonic_logits.std(axis=0, keepdims=True), 1e-4)
    harmonic_ratio = np.clip(forward_scale / harmonic_scale, *PUBLISHED_SCALE_CLAMP)
    fused = (harmonic_logits - harmonic_centre) * harmonic_ratio + forward_centre
    return column_softmax(fused)


def published_temperature(
    forward_logits: np.ndarray,
    reverse_logits: np.ndarray,
    *,
    reverse_weight: float = PUBLISHED_REVERSE_WEIGHT,
) -> np.ndarray:
    """Return the per-column exponent the published fusion ends up applying.

    The published code re-standardises the fused log-probabilities to the
    forward per-column mean and standard deviation.  A softmax ignores the
    additive term, so the whole re-standardisation reduces to raising the
    fused column to the power ``std(forward) / std(log harmonic)``, clamped to
    ``[0.5, 2.0]``.  Recovering that exponent explicitly is what makes the
    sharpening mechanism measurable.
    """

    forward = _as_matrix("forward_logits", forward_logits)
    if forward.size == 0:
        return np.zeros((forward.shape[1],), dtype=np.float64)
    harmonic = published_harmonic_probability(
        forward_logits,
        reverse_logits,
        reverse_weight=reverse_weight,
        apply_temperature=False,
    )
    forward_scale = np.maximum(forward.std(axis=0, keepdims=True), 1e-4)
    harmonic_logits = np.log(np.maximum(harmonic, 1e-8))
    harmonic_scale = np.maximum(harmonic_logits.std(axis=0, keepdims=True), 1e-4)
    return np.clip(forward_scale / harmonic_scale, *PUBLISHED_SCALE_CLAMP)[0]


# ---------------------------------------------------------------------------
# rule container
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PairInputs:
    """One frame pair's detector output in source-by-target orientation."""

    forward_logit: np.ndarray
    reverse_logit: np.ndarray
    physical_distance: np.ndarray
    forward_probability: np.ndarray | None = None

    def __post_init__(self) -> None:
        forward = _as_matrix("forward_logit", self.forward_logit)
        reverse = _as_matrix("reverse_logit", self.reverse_logit)
        distance = _as_matrix("physical_distance", self.physical_distance)
        if forward.shape != reverse.shape or forward.shape != distance.shape:
            raise ValueError("forward_logit, reverse_logit and physical_distance must share a shape")
        if self.forward_probability is not None:
            probability = _as_matrix("forward_probability", self.forward_probability)
            if probability.shape != forward.shape:
                raise ValueError("forward_probability must match the logit shape")

    @property
    def cached_or_recomputed_forward_probability(self) -> np.ndarray:
        """Prefer the cache's own float32 softmax so controls stay bit-exact."""

        if self.forward_probability is not None:
            return _as_matrix("forward_probability", self.forward_probability)
        return column_softmax(self.forward_logit)


ScoreFunction = Callable[[PairInputs], np.ndarray]
GateFunction = Callable[[PairInputs, np.ndarray], np.ndarray]


@dataclass(frozen=True, slots=True)
class ScoringRule:
    """A named, frozen, calibration-free edge-scoring rule.

    ``score`` returns a source-by-target matrix on the same column-stochastic
    scale as the pinned baseline unless ``changes_scale`` says otherwise.
    ``admit`` optionally widens or narrows the fixed ``threshold`` test; it
    receives the score matrix and returns a boolean matrix of extra
    admissions, which is OR-ed with ``score > threshold``.
    """

    rule_id: str
    hypothesis: str
    formula: str
    score: ScoreFunction
    threshold: float = 0.50
    admit: GateFunction | None = None
    changes_scale: bool = False
    uses_reverse: bool = True
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def evaluate(self, pair: PairInputs) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(scores, accepted_mask)`` for one frame pair."""

        scores = np.asarray(self.score(pair), dtype=np.float64)
        if scores.shape != np.asarray(pair.forward_logit).shape:
            raise ValueError(f"rule {self.rule_id} returned a mis-shaped score matrix")
        accepted = scores > float(self.threshold)
        if self.admit is not None:
            accepted = accepted | np.asarray(self.admit(pair, scores), dtype=bool)
        return scores, accepted

    def describe(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "hypothesis": self.hypothesis,
            "formula": self.formula,
            "threshold": float(self.threshold),
            "changes_scale": bool(self.changes_scale),
            "uses_reverse": bool(self.uses_reverse),
            "has_extra_admission_gate": self.admit is not None,
            "parameters": dict(self.parameters),
        }


# ---------------------------------------------------------------------------
# the rule family
# ---------------------------------------------------------------------------


def _p(pair: PairInputs) -> np.ndarray:
    return pair.cached_or_recomputed_forward_probability


def _q(pair: PairInputs) -> np.ndarray:
    return column_softmax(pair.reverse_logit)


def _forward_only(pair: PairInputs) -> np.ndarray:
    return _p(pair)


def _reverse_only(pair: PairInputs) -> np.ndarray:
    return _q(pair)


def _arithmetic_mean(pair: PairInputs) -> np.ndarray:
    return 0.5 * (_p(pair) + _q(pair))


def _geometric_mean(pair: PairInputs) -> np.ndarray:
    return renormalise_columns(np.sqrt(np.maximum(_p(pair) * _q(pair), 0.0)))


def _logit_sum(pair: PairInputs) -> np.ndarray:
    return column_softmax(np.asarray(pair.forward_logit, dtype=np.float64) + np.asarray(pair.reverse_logit, dtype=np.float64))


def _harmonic_mean(pair: PairInputs) -> np.ndarray:
    forward = np.maximum(_p(pair), EPSILON)
    reverse = np.maximum(_q(pair), EPSILON)
    return renormalise_columns(1.0 / (0.5 / forward + 0.5 / reverse))


def _weighted_harmonic(pair: PairInputs) -> np.ndarray:
    forward = np.maximum(_p(pair), EPSILON)
    reverse = np.maximum(_q(pair), EPSILON)
    weight = PUBLISHED_REVERSE_WEIGHT
    return renormalise_columns(1.0 / ((1.0 - weight) / forward + weight / reverse))


def _minimum(pair: PairInputs) -> np.ndarray:
    return renormalise_columns(np.minimum(_p(pair), _q(pair)))


def _maximum(pair: PairInputs) -> np.ndarray:
    return renormalise_columns(np.maximum(_p(pair), _q(pair)))


def _mutual_confidence_unnormalised(pair: PairInputs) -> np.ndarray:
    return np.sqrt(np.maximum(_p(pair) * _q(pair), 0.0))


def _veto_ratio(pair: PairInputs) -> np.ndarray:
    return renormalise_columns(np.minimum(_p(pair), PUBLISHED_VETO_RATIO * _q(pair)))


def _disagreement_symmetric(pair: PairInputs) -> np.ndarray:
    forward = np.maximum(_p(pair), EPSILON)
    reverse = np.maximum(_q(pair), EPSILON)
    disagreement = np.abs(np.log(forward) - np.log(reverse))
    return renormalise_columns(forward * np.exp(-disagreement))


def _disagreement_one_sided(pair: PairInputs) -> np.ndarray:
    forward = np.maximum(_p(pair), EPSILON)
    reverse = np.maximum(_q(pair), EPSILON)
    return renormalise_columns(forward * np.minimum(1.0, reverse / forward))


def _published_harmonic(pair: PairInputs) -> np.ndarray:
    return published_harmonic_probability(pair.forward_logit, pair.reverse_logit)


def _published_harmonic_no_temperature(pair: PairInputs) -> np.ndarray:
    return published_harmonic_probability(
        pair.forward_logit,
        pair.reverse_logit,
        apply_temperature=False,
    )


def _forward_published_temperature(pair: PairInputs) -> np.ndarray:
    """Sharpen the forward column by the published exponent, discard the fusion."""

    gamma = published_temperature(pair.forward_logit, pair.reverse_logit)
    return column_temperature(_p(pair), gamma)


def _entropy_temperature(pair: PairInputs) -> np.ndarray:
    """Sharpen each column by an exponent set by its own entropy.

    ``gamma(t) = clamp(log(N_source) / H(column t), 0.5, 2.0)``.  Because
    ``H <= log N`` always, the raw ratio is never below one, so this rule only
    ever sharpens.  The exponent is *largest* for the most confident (lowest
    entropy) columns, where the clamp at 2.0 binds; a near-uniform column gets
    ``gamma`` close to 1 and is left almost alone.

    The absolute mass gained is nevertheless largest for mid-range columns,
    because an already-peaked column has almost no headroom left.  That is the
    behaviour asserted in the unit test, and it is why this rule moves entries
    across the fixed 0.5 cut.  No reverse pass and no fitted constant: the
    clamp window is inherited from the published method.
    """

    forward = _p(pair)
    sources = forward.shape[0]
    if sources <= 1:
        return forward
    entropy = np.maximum(column_entropy(forward), EPSILON)
    gamma = np.clip(np.log(sources) / entropy, *PUBLISHED_SCALE_CLAMP)
    return column_temperature(forward, gamma)


def _fixed_temperature(pair: PairInputs) -> np.ndarray:
    """The crudest possible sharpening: square the forward column.

    No reverse pass, no entropy, no per-column adaptation.  The exponent 2.0
    is not fitted; it is the upper end of the clamp the published method
    already applies.  If this alone reproduces the published gain, then the
    bidirectional machinery is not what the gain is made of.
    """

    return column_temperature(_p(pair), 2.0)


def _dual_softmax(pair: PairInputs) -> np.ndarray:
    forward = np.asarray(pair.forward_logit, dtype=np.float64)
    return renormalise_columns(np.sqrt(column_softmax(forward) * row_softmax(forward)))


def _dual_softmax_bidirectional(pair: PairInputs) -> np.ndarray:
    return renormalise_columns(
        np.sqrt(column_softmax(pair.forward_logit) * row_softmax(pair.reverse_logit))
    )


def _dual_softmax_top2(pair: PairInputs) -> np.ndarray:
    return renormalise_columns(_p(pair) * np.sqrt(row_top_two_share(pair.forward_logit)))


def _motion_gated(pair: PairInputs) -> np.ndarray:
    """Codex's control, expressed here as a scale-changing rule."""

    forward = _p(pair)
    distance = np.asarray(pair.physical_distance, dtype=np.float64)
    scores = forward * np.exp(-0.05 * distance)
    return np.where(distance > 12.0, 0.0, scores)


def _distance_prior_adaptive(pair: PairInputs) -> np.ndarray:
    distance = np.asarray(pair.physical_distance, dtype=np.float64)
    sigma = nearest_source_scale(distance)
    prior = np.exp(-0.5 * np.square(distance / sigma))
    return renormalise_columns(_p(pair) * prior)


def _lane_f_v1(pair: PairInputs) -> np.ndarray:
    """Explicit veto x division-tolerant row consistency.

    ``min(p, 4 q)`` is the clean form of what the published harmonic mean
    approximates; ``sqrt(row_top_two_share)`` adds the consistency direction
    the pinned pipeline never looks at, without penalising a parent that backs
    two children.
    """

    vetoed = np.minimum(_p(pair), PUBLISHED_VETO_RATIO * _q(pair))
    return renormalise_columns(vetoed * np.sqrt(row_top_two_share(pair.forward_logit)))


def _dominance_admission(pair: PairInputs, scores: np.ndarray) -> np.ndarray:
    """Admit a column's argmax when it dominates the runner-up.

    A column whose mass is split across several geometrically plausible
    parents can have an unambiguous winner that still sits below the fixed
    ``0.5`` threshold; the pinned rule discards it and the track breaks.  This
    admission is scale-free (it compares top-1 against top-2, so it adapts to
    local density automatically) and its floor is the ILP's own break-even
    probability rather than a fitted number.
    """

    array = np.asarray(scores, dtype=np.float64)
    if array.size == 0:
        return np.zeros(array.shape, dtype=bool)
    top, runner_up = top_two_by_column(array)
    dominant = (top >= RUNNER_UP_RATIO * runner_up) & (top >= ILP_BREAK_EVEN_PROBABILITY)
    is_argmax = array >= top[None, :]
    return is_argmax & dominant[None, :]


def _mutual_top2_admission(pair: PairInputs, scores: np.ndarray) -> np.ndarray:
    """Never widen; used with ``threshold`` to express a pure gate."""

    return np.zeros(np.asarray(scores).shape, dtype=bool)


def _mutual_top2_gate(pair: PairInputs) -> np.ndarray:
    """Forward score, zeroed unless the target is in the source's row top-2."""

    forward = _p(pair)
    share = row_top_two_share(pair.forward_logit)
    return np.where(share >= 0.5, forward, 0.0)


RESEARCH_RULES: dict[str, ScoringRule] = {}


def _register(rule: ScoringRule) -> ScoringRule:
    if rule.rule_id in RESEARCH_RULES:
        raise ValueError(f"duplicate rule_id: {rule.rule_id}")
    RESEARCH_RULES[rule.rule_id] = rule
    return rule


_register(
    ScoringRule(
        rule_id="forward_only",
        hypothesis="Control. Reproduces official_ilp bit-for-bit from the cache.",
        formula="s = softmax_col(F)",
        score=_forward_only,
        uses_reverse=False,
    )
)
_register(
    ScoringRule(
        rule_id="reverse_only",
        hypothesis="If the reverse pass alone is competitive, the two passes carry similar information.",
        formula="s = softmax_col(R)",
        score=_reverse_only,
    )
)
_register(
    ScoringRule(
        rule_id="arithmetic_mean",
        hypothesis="Averaging two estimates reduces variance but cannot veto; expected to sit between the two.",
        formula="s = (p + q) / 2",
        score=_arithmetic_mean,
    )
)
_register(
    ScoringRule(
        rule_id="geometric_mean",
        hypothesis="Renormalised geometric mean is exactly softmax_col((F+R)/2); a product of experts at temperature 2.",
        formula="s = norm_col(sqrt(p q))",
        score=_geometric_mean,
    )
)
_register(
    ScoringRule(
        rule_id="logit_sum",
        hypothesis="Product of experts at temperature 1; sharper than the geometric mean by construction.",
        formula="s = softmax_col(F + R)",
        score=_logit_sum,
    )
)
_register(
    ScoringRule(
        rule_id="harmonic_mean",
        hypothesis="Unweighted harmonic mean is the symmetric 'both must agree' rule (veto ratio 1).",
        formula="s = norm_col(1 / (0.5/p + 0.5/q))",
        score=_harmonic_mean,
    )
)
_register(
    ScoringRule(
        rule_id="weighted_harmonic",
        hypothesis="Published fusion with the final temperature rescaling removed: isolates the agreement term.",
        formula="s = norm_col(1 / (0.8/p + 0.2/q))",
        score=_weighted_harmonic,
        parameters={"reverse_weight": PUBLISHED_REVERSE_WEIGHT},
    )
)
_register(
    ScoringRule(
        rule_id="min_rule",
        hypothesis="Hard 'both directions must agree'; strictly more conservative than any mean.",
        formula="s = norm_col(min(p, q))",
        score=_minimum,
    )
)
_register(
    ScoringRule(
        rule_id="max_rule",
        hypothesis="Hard 'either direction suffices'; should trade precision for recall.",
        formula="s = norm_col(max(p, q))",
        score=_maximum,
    )
)
_register(
    ScoringRule(
        rule_id="mutual_confidence_unnormalised",
        hypothesis="Codex's mutual_confidence. Not renormalised, so sqrt shrinks the scale and the fixed 0.5 acts stricter.",
        formula="s = sqrt(p q)   (no column renormalisation)",
        score=_mutual_confidence_unnormalised,
        changes_scale=True,
    )
)
_register(
    ScoringRule(
        rule_id="disagreement_symmetric",
        hypothesis="Penalise disagreement in both directions, including a reverse pass that is more confident than forward.",
        formula="s = norm_col(p * exp(-|log p - log q|))",
        score=_disagreement_symmetric,
        parameters={"lambda": 1.0},
    )
)
_register(
    ScoringRule(
        rule_id="disagreement_one_sided",
        hypothesis="Penalise only a reverse pass that is less confident; the explicit form of the harmonic asymmetry.",
        formula="s = norm_col(p * min(1, q/p))",
        score=_disagreement_one_sided,
        parameters={"lambda": 1.0},
    )
)
_register(
    ScoringRule(
        rule_id="veto_ratio",
        hypothesis="min(p, 4q) is the clean limit of the published w=0.20 harmonic mean; if harmonic works because of agreement this should match it.",
        formula="s = norm_col(min(p, ((1-w)/w) q)),  w = 0.20",
        score=_veto_ratio,
        parameters={"veto_ratio": PUBLISHED_VETO_RATIO},
    )
)
_register(
    ScoringRule(
        rule_id="published_harmonic",
        hypothesis="Control. Numpy reimplementation of harmonic_v1; must match Codex's torch path.",
        formula="published weighted-harmonic fusion with per-column logit rescaling",
        score=_published_harmonic,
        parameters={"reverse_weight": PUBLISHED_REVERSE_WEIGHT},
    )
)
_register(
    ScoringRule(
        rule_id="published_harmonic_no_temperature",
        hypothesis="ABLATION A. Drop the final per-column rescaling. If the gain survives, the mechanism is agreement.",
        formula="published fusion, final logit re-standardisation removed",
        score=_published_harmonic_no_temperature,
        parameters={"reverse_weight": PUBLISHED_REVERSE_WEIGHT},
    )
)
_register(
    ScoringRule(
        rule_id="forward_published_temperature",
        hypothesis="ABLATION B. Apply the published per-column exponent to the forward score and use no reverse information. If the gain survives, the mechanism is sharpening.",
        formula="s = norm_col(p ** gamma),  gamma = clamp(std_col(F)/std_col(log h), 0.5, 2)",
        score=_forward_published_temperature,
    )
)
_register(
    ScoringRule(
        rule_id="entropy_temperature",
        hypothesis="Sharpening without any reverse pass: raise each column to log(N)/H(column), clamped to [0.5, 2].",
        formula="s = norm_col(p ** clamp(log(N_source)/H(p_col), 0.5, 2))",
        score=_entropy_temperature,
        uses_reverse=False,
    )
)
_register(
    ScoringRule(
        rule_id="fixed_temperature",
        hypothesis="If squaring the forward column reproduces the published gain, the reverse pass is decoration.",
        formula="s = norm_col(p ** 2)",
        score=_fixed_temperature,
        uses_reverse=False,
        parameters={"gamma": 2.0},
    )
)
_register(
    ScoringRule(
        rule_id="dual_softmax",
        hypothesis="The row (child-given-parent) direction is never used. Dual softmax on the forward matrix alone may recover most of the reverse pass for free.",
        formula="s = norm_col(sqrt(softmax_col(F) * softmax_row(F)))",
        score=_dual_softmax,
        uses_reverse=False,
    )
)
_register(
    ScoringRule(
        rule_id="dual_softmax_bidirectional",
        hypothesis="Use the reverse pass in its native direction instead of transposing it back into the source axis.",
        formula="s = norm_col(sqrt(softmax_col(F) * softmax_row(R)))",
        score=_dual_softmax_bidirectional,
    )
)
_register(
    ScoringRule(
        rule_id="dual_softmax_top2",
        hypothesis="Row consistency that tolerates division: renormalise each row over its own top two entries.",
        formula="s = norm_col(p * sqrt(row_top2_share(F)))",
        score=_dual_softmax_top2,
        uses_reverse=False,
    )
)
_register(
    ScoringRule(
        rule_id="mutual_top2_gate",
        hypothesis="Pure precision filter: keep the forward score only where the target is one of the source's two best children.",
        formula="s = p if row_top2_share(F) >= 0.5 else 0",
        score=_mutual_top2_gate,
        admit=_mutual_top2_admission,
        uses_reverse=False,
        changes_scale=True,
    )
)
_register(
    ScoringRule(
        rule_id="motion_gated",
        hypothesis="Control. Codex's motion gate; the hard 12 um cut removes true long-displacement links.",
        formula="s = p * exp(-0.05 d), s = 0 when d > 12 um",
        score=_motion_gated,
        uses_reverse=False,
        changes_scale=True,
        parameters={"motion_gate_um": 12.0, "motion_alpha": 0.05},
    )
)
_register(
    ScoringRule(
        rule_id="distance_prior_adaptive",
        hypothesis="A soft Gaussian displacement prior whose scale is the frame pair's own median nearest-source distance, so nothing is fitted.",
        formula="s = norm_col(p * exp(-(d/sigma)^2/2)),  sigma = median_t min_s d[s,t]",
        score=_distance_prior_adaptive,
        uses_reverse=False,
    )
)
_register(
    ScoringRule(
        rule_id="column_dominance",
        hypothesis="Most losses are diluted columns whose unambiguous winner sits just under 0.5. Admit the argmax when it dominates the runner-up.",
        formula="accept if p > 0.5 OR (p is column argmax AND top1 >= 2*top2 AND top1 >= 0.2)",
        score=_forward_only,
        admit=_dominance_admission,
        uses_reverse=False,
        parameters={"runner_up_ratio": RUNNER_UP_RATIO, "floor": ILP_BREAK_EVEN_PROBABILITY},
    )
)
_register(
    ScoringRule(
        rule_id="lane_f_v1",
        hypothesis="Explicit disagreement veto combined with division-tolerant row consistency; no fitted constant.",
        formula="s = norm_col(min(p, 4q) * sqrt(row_top2_share(F)))",
        score=_lane_f_v1,
        parameters={"veto_ratio": PUBLISHED_VETO_RATIO},
    )
)
_register(
    ScoringRule(
        rule_id="lane_f_v1_dominance",
        hypothesis="lane_f_v1 plus the dominance admission, i.e. agreement and sharpening addressed separately rather than bundled.",
        formula="lane_f_v1 score with the column-dominance admission",
        score=_lane_f_v1,
        admit=_dominance_admission,
        parameters={
            "veto_ratio": PUBLISHED_VETO_RATIO,
            "runner_up_ratio": RUNNER_UP_RATIO,
            "floor": ILP_BREAK_EVEN_PROBABILITY,
        },
    )
)


__all__ = [
    "EPSILON",
    "ILP_BREAK_EVEN_PROBABILITY",
    "PUBLISHED_REVERSE_WEIGHT",
    "PUBLISHED_SCALE_CLAMP",
    "PUBLISHED_VETO_RATIO",
    "RESEARCH_RULES",
    "RUNNER_UP_RATIO",
    "PairInputs",
    "ScoringRule",
    "column_entropy",
    "column_softmax",
    "column_temperature",
    "local_source_density",
    "nearest_source_scale",
    "published_harmonic_probability",
    "published_temperature",
    "renormalise_columns",
    "row_softmax",
    "row_top_two_share",
    "top_two_by_column",
]
