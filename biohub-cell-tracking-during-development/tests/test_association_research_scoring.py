"""Unit tests for the Lane F association scoring rules.

Every array in this file is **synthetic test data**, constructed by hand or
from a seeded RNG, as required by AGENTS.md section 8.  No detector output,
no cache, no image and no ground truth is read here.  The tests exist to pin
down the *mechanics* of the published fusion and of the proposed rules; they
make no claim about tracking accuracy.
"""

from __future__ import annotations

import numpy as np
import pytest

from biohub.association_research.scoring import (
    ILP_BREAK_EVEN_PROBABILITY,
    PUBLISHED_REVERSE_WEIGHT,
    PUBLISHED_VETO_RATIO,
    RESEARCH_RULES,
    PairInputs,
    column_entropy,
    column_softmax,
    column_temperature,
    published_harmonic_probability,
    published_temperature,
    renormalise_columns,
    row_softmax,
    row_top_two_mask,
    row_top_two_share,
)

SEED = 20260821


def _synthetic_pair(sources: int = 6, targets: int = 5, seed: int = SEED) -> PairInputs:
    """Synthetic frame pair: random logits and random positive distances."""

    rng = np.random.default_rng(seed)
    forward = rng.normal(0.0, 2.0, size=(sources, targets))
    reverse = forward + rng.normal(0.0, 1.0, size=(sources, targets))
    distance = rng.uniform(0.5, 20.0, size=(sources, targets))
    return PairInputs(
        forward_logit=forward,
        reverse_logit=reverse,
        physical_distance=distance,
    )


# ---------------------------------------------------------------------------
# normalisation convention
# ---------------------------------------------------------------------------


def test_column_softmax_normalises_over_sources_like_upstream() -> None:
    """Upstream uses torch.softmax(raw, dim=0) on an (N_source, N_target) matrix."""

    logits = np.array([[0.0, 1.0], [np.log(3.0), 1.0]])
    probabilities = column_softmax(logits)
    np.testing.assert_allclose(probabilities.sum(axis=0), np.ones(2))
    # Column 0 splits 1 : 3, so the parent posterior is 0.25 / 0.75.
    np.testing.assert_allclose(probabilities[:, 0], [0.25, 0.75])
    # Column 1 is a tie.
    np.testing.assert_allclose(probabilities[:, 1], [0.5, 0.5])


def test_row_softmax_is_the_direction_the_pipeline_never_uses() -> None:
    logits = np.array([[0.0, np.log(3.0)], [1.0, 1.0]])
    rows = row_softmax(logits)
    np.testing.assert_allclose(rows.sum(axis=1), np.ones(2))
    np.testing.assert_allclose(rows[0], [0.25, 0.75])
    assert not np.allclose(rows, column_softmax(logits))


def test_per_column_multiplier_is_a_no_op_after_renormalisation() -> None:
    """Any purely target-indexed reweighting cannot change the selected edges.

    This is why a local-density term has to act on the *threshold* (or as a
    per-column exponent); multiplying a column-normalised score by a
    density-derived factor is provably inert.
    """

    pair = _synthetic_pair()
    base = column_softmax(pair.forward_logit)
    density = np.array([1.0, 7.0, 0.2, 3.5, 11.0])
    reweighted = renormalise_columns(base * density[None, :])
    np.testing.assert_allclose(reweighted, base, rtol=0, atol=1e-12)


def test_per_column_exponent_moves_entries_across_the_fixed_threshold() -> None:
    """A temperature is not a no-op, and that is how sharpening changes counts."""

    # Synthetic diluted column: an unambiguous winner that still sits below 0.5.
    logits = np.array([[0.9], [0.0], [0.0], [0.0]])
    base = column_softmax(logits)
    assert 0.2 < float(base[0, 0]) < 0.5
    sharpened = column_temperature(base, 2.0)
    assert float(sharpened[0, 0]) > 0.5
    np.testing.assert_allclose(sharpened.sum(axis=0), np.ones(1))


# ---------------------------------------------------------------------------
# the published fusion, mechanically
# ---------------------------------------------------------------------------


def test_numpy_reimplementation_matches_codex_torch_fusion() -> None:
    """published_harmonic_probability must equal Codex's torch path."""

    torch = pytest.importorskip("torch")
    from biohub.strong_baseline.harmonic import fuse_harmonic_logits

    pair = _synthetic_pair(sources=7, targets=6, seed=SEED + 1)
    forward = np.asarray(pair.forward_logit, dtype=np.float32)
    reverse = np.asarray(pair.reverse_logit, dtype=np.float32)

    # Codex's helper takes the model's native (N_target, N_source) reverse output.
    fused = fuse_harmonic_logits(
        torch.from_numpy(forward).unsqueeze(0),
        torch.from_numpy(reverse.T).unsqueeze(0),
        reverse_weight=PUBLISHED_REVERSE_WEIGHT,
    )[0]
    expected = torch.softmax(fused.float(), dim=0).numpy()
    actual = published_harmonic_probability(forward, reverse)
    np.testing.assert_allclose(actual, expected, rtol=1e-4, atol=1e-5)


def test_identical_reverse_pass_leaves_the_forward_score_unchanged() -> None:
    """If the two passes agree exactly, the fusion is the identity."""

    pair = _synthetic_pair(seed=SEED + 2)
    forward = np.asarray(pair.forward_logit)
    fused = published_harmonic_probability(forward, forward)
    np.testing.assert_allclose(fused, column_softmax(forward), rtol=1e-6, atol=1e-9)
    np.testing.assert_allclose(published_temperature(forward, forward), 1.0, rtol=1e-5, atol=1e-6)


def test_weighted_harmonic_is_a_soft_minimum_with_ratio_four() -> None:
    """1/((1-w)/p + w/q) sits between 0.5x and 1x of min(p/(1-w), q/w).

    That is the mechanical content of "harmonic means both directions must
    agree": with w = 0.20 the rule is "cap the forward probability at four
    times the reverse probability".
    """

    weight = PUBLISHED_REVERSE_WEIGHT
    rng = np.random.default_rng(SEED + 3)
    forward = rng.uniform(1e-6, 1.0, size=5000)
    reverse = rng.uniform(1e-6, 1.0, size=5000)
    harmonic = 1.0 / ((1.0 - weight) / forward + weight / reverse)
    soft_minimum = np.minimum(forward / (1.0 - weight), reverse / weight)
    ratio = harmonic / soft_minimum
    assert ratio.min() >= 0.5 - 1e-12
    assert ratio.max() <= 1.0 + 1e-12
    assert float(PUBLISHED_VETO_RATIO) == pytest.approx(4.0)


def test_harmonic_boost_is_bounded_but_the_penalty_is_not() -> None:
    """Agreement can only lift a score by 1/(1-w); disagreement can crush it."""

    weight = PUBLISHED_REVERSE_WEIGHT
    forward = 0.4

    generous_reverse = 1.0
    boosted = 1.0 / ((1.0 - weight) / forward + weight / generous_reverse)
    assert boosted / forward <= 1.0 / (1.0 - weight) + 1e-12

    for tiny_reverse in (1e-3, 1e-4, 1e-5):
        crushed = 1.0 / ((1.0 - weight) / forward + weight / tiny_reverse)
        assert crushed < forward
        # The floor is the reverse probability scaled by 1/w.
        assert crushed <= tiny_reverse / weight + 1e-12


def test_published_temperature_stays_inside_the_published_clamp() -> None:
    pair = _synthetic_pair(sources=9, targets=8, seed=SEED + 4)
    gamma = published_temperature(pair.forward_logit, pair.reverse_logit)
    assert gamma.shape == (8,)
    assert float(gamma.min()) >= 0.5 - 1e-12
    assert float(gamma.max()) <= 2.0 + 1e-12


def test_temperature_ablation_actually_changes_the_score() -> None:
    """Dropping the re-standardisation must not be a silent no-op."""

    pair = _synthetic_pair(sources=8, targets=7, seed=SEED + 5)
    with_temperature = published_harmonic_probability(pair.forward_logit, pair.reverse_logit)
    without = published_harmonic_probability(
        pair.forward_logit,
        pair.reverse_logit,
        apply_temperature=False,
    )
    assert not np.allclose(with_temperature, without)
    np.testing.assert_allclose(with_temperature.sum(axis=0), np.ones(7), rtol=1e-6)
    np.testing.assert_allclose(without.sum(axis=0), np.ones(7), rtol=1e-6)


# ---------------------------------------------------------------------------
# division-tolerant row consistency
# ---------------------------------------------------------------------------


def test_row_top_two_share_does_not_penalise_a_dividing_parent() -> None:
    """Two equally good children both score 1.0; plain row softmax gives 0.5."""

    logits = np.array([[5.0, 5.0, -50.0, -50.0]])
    share = row_top_two_share(logits)
    np.testing.assert_allclose(share[0, :2], [1.0, 1.0])
    assert share[0, 2] < 1e-3
    np.testing.assert_allclose(row_softmax(logits)[0, :2], [0.5, 0.5], rtol=1e-9)


def test_row_top_two_share_suppresses_a_third_best_child() -> None:
    logits = np.array([[5.0, 5.0, 3.0]])
    share = row_top_two_share(logits)
    assert share[0, 2] < 0.2
    np.testing.assert_allclose(share[0, :2], [1.0, 1.0])


def test_row_top_two_share_ignores_the_number_of_also_rans() -> None:
    """Unlike a row softmax, the score of the winner does not decay with N."""

    few = np.array([[4.0, -4.0]])
    many = np.array([[4.0] + [-4.0] * 40])
    assert row_top_two_share(few)[0, 0] == pytest.approx(row_top_two_share(many)[0, 0], rel=1e-9)
    assert row_softmax(many)[0, 0] < row_softmax(few)[0, 0]


def test_row_top_two_mask_selects_exactly_two_children() -> None:
    logits = np.array([[1.0, 5.0, 3.0, 2.0]])
    mask = row_top_two_mask(logits)
    assert mask.sum() == 2
    assert bool(mask[0, 1]) and bool(mask[0, 2])


# ---------------------------------------------------------------------------
# rule registry contracts
# ---------------------------------------------------------------------------


def test_every_rule_returns_a_finite_matrix_of_the_right_shape() -> None:
    pair = _synthetic_pair(sources=10, targets=9, seed=SEED + 6)
    for rule_id, rule in RESEARCH_RULES.items():
        scores, accepted = rule.evaluate(pair)
        assert scores.shape == (10, 9), rule_id
        assert accepted.shape == (10, 9), rule_id
        assert np.isfinite(scores).all(), rule_id
        assert (scores >= 0.0).all(), rule_id


def test_scale_preserving_rules_are_column_stochastic() -> None:
    """A rule that silently shrinks the scale reinterprets the fixed 0.5 cut."""

    pair = _synthetic_pair(sources=10, targets=9, seed=SEED + 7)
    for rule_id, rule in RESEARCH_RULES.items():
        if rule.changes_scale:
            continue
        scores, _ = rule.evaluate(pair)
        np.testing.assert_allclose(
            scores.sum(axis=0),
            np.ones(9),
            rtol=1e-6,
            atol=1e-8,
            err_msg=f"{rule_id} is not column-stochastic",
        )


def test_mutual_confidence_shrinks_the_scale_relative_to_the_geometric_mean() -> None:
    """Codex's mutual_confidence omits renormalisation, so 0.5 acts stricter."""

    pair = _synthetic_pair(sources=12, targets=9, seed=SEED + 8)
    unnormalised, _ = RESEARCH_RULES["mutual_confidence_unnormalised"].evaluate(pair)
    normalised, _ = RESEARCH_RULES["geometric_mean"].evaluate(pair)
    column_mass = unnormalised.sum(axis=0)
    assert float(column_mass.max()) < 1.0
    assert int((unnormalised > 0.5).sum()) <= int((normalised > 0.5).sum())


def test_forward_only_rule_uses_the_cached_probability_verbatim() -> None:
    """The control must be bit-identical to the cache's own float32 softmax."""

    rng = np.random.default_rng(SEED + 9)
    forward = rng.normal(size=(5, 4))
    cached = column_softmax(forward).astype(np.float32)
    pair = PairInputs(
        forward_logit=forward,
        reverse_logit=forward,
        physical_distance=np.ones((5, 4)),
        forward_probability=cached,
    )
    scores, _ = RESEARCH_RULES["forward_only"].evaluate(pair)
    np.testing.assert_array_equal(scores, cached.astype(np.float64))


def test_veto_ratio_and_weighted_harmonic_pick_the_same_winner() -> None:
    """min(p, 4q) and the published mean agree on the winner but not the tail.

    The soft minimum is only within a factor of two of the true minimum, so
    the two rules are *not* order-identical: the published harmonic mean is a
    genuine mean in the low-score tail, not a hard veto.  Both facts matter,
    so both are asserted.
    """

    pair = _synthetic_pair(sources=9, targets=7, seed=SEED + 10)
    veto, _ = RESEARCH_RULES["veto_ratio"].evaluate(pair)
    harmonic, _ = RESEARCH_RULES["weighted_harmonic"].evaluate(pair)
    np.testing.assert_array_equal(veto.argmax(axis=0), harmonic.argmax(axis=0))
    identical_order = all(
        np.array_equal(np.argsort(veto[:, column]), np.argsort(harmonic[:, column]))
        for column in range(veto.shape[1])
    )
    assert not identical_order


def test_column_dominance_admits_a_sub_threshold_winner_but_not_a_tie() -> None:
    """The failure mode being targeted: a clear winner diluted below 0.5."""

    dominant = np.array([[0.9], [0.0], [0.0], [0.0]])
    pair = PairInputs(
        forward_logit=dominant,
        reverse_logit=dominant,
        physical_distance=np.ones((4, 1)),
    )
    scores, accepted = RESEARCH_RULES["column_dominance"].evaluate(pair)
    assert float(scores[0, 0]) < 0.5
    assert float(scores[0, 0]) >= ILP_BREAK_EVEN_PROBABILITY
    assert bool(accepted[0, 0])
    assert not accepted[1:, 0].any()

    ambiguous = np.array([[0.30], [0.0], [0.0], [0.0]])
    tie_pair = PairInputs(
        forward_logit=ambiguous,
        reverse_logit=ambiguous,
        physical_distance=np.ones((4, 1)),
    )
    _, tie_accepted = RESEARCH_RULES["column_dominance"].evaluate(tie_pair)
    assert not tie_accepted.any()


def test_column_dominance_never_removes_a_threshold_admission() -> None:
    pair = _synthetic_pair(sources=8, targets=6, seed=SEED + 11)
    baseline, baseline_accepted = RESEARCH_RULES["forward_only"].evaluate(pair)
    _, widened = RESEARCH_RULES["column_dominance"].evaluate(pair)
    assert np.array_equal(baseline_accepted & widened, baseline_accepted)


def test_dominance_floor_is_the_ilp_break_even_probability() -> None:
    """0.2 = appearance_weight + disappearance_weight, not a fitted constant."""

    assert ILP_BREAK_EVEN_PROBABILITY == pytest.approx(0.1 + 0.1)


def test_entropy_temperature_sharpens_diluted_columns_most() -> None:
    confident = np.array([[6.0], [0.0], [0.0], [0.0]])
    diluted = np.array([[0.8], [0.0], [0.0], [0.0]])
    logits = np.concatenate([confident, diluted], axis=1)
    pair = PairInputs(
        forward_logit=logits,
        reverse_logit=logits,
        physical_distance=np.ones((4, 2)),
    )
    base = column_softmax(logits)
    scores, _ = RESEARCH_RULES["entropy_temperature"].evaluate(pair)
    confident_gain = float(scores[0, 0] - base[0, 0])
    diluted_gain = float(scores[0, 1] - base[0, 1])
    assert diluted_gain > confident_gain
    assert column_entropy(base)[1] > column_entropy(base)[0]


def test_pair_inputs_rejects_mismatched_shapes() -> None:
    with pytest.raises(ValueError):
        PairInputs(
            forward_logit=np.zeros((3, 2)),
            reverse_logit=np.zeros((2, 3)),
            physical_distance=np.zeros((3, 2)),
        )


def test_rule_descriptions_are_complete() -> None:
    for rule_id, rule in RESEARCH_RULES.items():
        described = rule.describe()
        assert described["rule_id"] == rule_id
        assert described["hypothesis"].strip()
        assert described["formula"].strip()
