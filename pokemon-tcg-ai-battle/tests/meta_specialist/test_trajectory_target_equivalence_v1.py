"""The prepared/batched scoring path must produce the *same* numbers as the plain one.

``trajectory_target_v1`` scores each stored transition by hoisting two
deterministic computations out of the per-prefix-step loop -- ``encode_state``
(once per decision instead of once per step) and candidate encoding (once per
distinct candidate instead of once per occurrence, in one batched call) -- and
by validating each transition once instead of once per optimizer step.  Every
one of those is a reuse of a value that does not depend on where it is
computed, so none of them may change what the training step sees.

These tests hold that claim to the only standard that matters for a learner:
identical log-probabilities *and* identical gradients, measured on really
collected games, against a reference that walks the unbatched, uncached,
encode-state-per-step path through the model's public ``step_logits``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (  # noqa: E402
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.trajectory_target_v1 import (  # noqa: E402
    _prepare_step,
    make_trajectory_target_log_probability_v1,
    prepare_trajectory_target_transition_v1,
)


ROOT = Path(__file__).resolve().parents[2]
COLLECTED = ROOT / "runs/meta-specialist-actor-pool/cli-smoke-test-alakazam-4/games"

pytestmark = pytest.mark.skipif(
    not COLLECTED.is_dir(),
    reason="no collected trajectories present; regenerate with collect-trajectories",
)


def _transitions(limit: int = 40) -> list[dict]:
    collected: list[dict] = []
    for path in sorted(COLLECTED.glob("*/record.json")):
        collected.extend(json.loads(path.read_text(encoding="utf-8"))["transitions"])
        if len(collected) >= limit:
            break
    assert collected, "expected at least one collected transition"
    return collected[:limit]


def _model(seed: int = 1):
    vocabulary = load_production_card_vocabulary_v1()
    config = SpecialistModelConfigV1(card_vocabulary_size=max(vocabulary.recognized_card_ids))
    return build_specialist_policy_model_v1(config, seed=seed)


def _assert_gradients_match(reference_model, other_model, *, ulp_budget: int = 32) -> None:
    """Compare gradients against the scale of the gradients themselves.

    A bare absolute tolerance is the wrong test here: these parameters carry
    gradients spanning several orders of magnitude (measured up to ~33 on
    ``candidate_bias.weight``), so one fixed epsilon is simultaneously far too
    loose for the small ones and too tight for the large ones.  Reordering a sum
    in float32 costs a few ULP *of that sum*, so that is what is asserted.
    Measured across the reordering these tests cover: worst case ~1.1e-06
    relative, i.e. under 10 ULP.
    """
    epsilon = torch.finfo(torch.float32).eps
    compared = 0
    for (name, a), (_, b) in zip(
        reference_model.named_parameters(), other_model.named_parameters(), strict=True
    ):
        if a.grad is None and b.grad is None:
            continue
        assert a.grad is not None and b.grad is not None, f"{name}: only one path has a gradient"
        with torch.no_grad():
            deviation = float((a.grad - b.grad).abs().max())
            scale = float(a.grad.abs().max())
        allowed = ulp_budget * epsilon * max(scale, 1.0)
        assert deviation <= allowed, (
            f"{name}: gradient deviates by {deviation:.3e}, more than {ulp_budget} ULP "
            f"({allowed:.3e}) of its own scale {scale:.4f} -- that is a different "
            "computation, not float32 reordering"
        )
        compared += 1
    assert compared > 0, "no parameter received a gradient in either path"
    total = torch.sqrt(
        sum((p.grad**2).sum() for p in reference_model.parameters() if p.grad is not None)
    )
    assert float(total) > 0.0, "an all-zero gradient would make this comparison vacuous"


def _reference_log_probability(model, transition) -> torch.Tensor:
    """Score one transition the plain way: no prepared plan, no state hoist, no cache.

    This deliberately re-derives the value through ``model.step_logits``, which
    encodes the decision state itself on every call and encodes every candidate
    with a separate ``encode_candidate`` call -- the path the module used before
    it learned to reuse either.
    """
    prepared = prepare_trajectory_target_transition_v1(transition)
    contributions = []
    for index, step_payload in enumerate(transition["prefix_steps"]):
        step = _prepare_step(step_payload, field=f"prefix_steps[{index}]")
        if step.forced_stop:
            contributions.append(torch.zeros((), dtype=torch.float32))
            continue
        semantic, stop = model.step_logits(prepared.model_input, step.step_input)
        scores = semantic if stop is None else torch.cat([semantic, stop.reshape(1)])
        contributions.append((scores - torch.logsumexp(scores, dim=0))[step.target_index])
    return torch.stack(contributions).sum()


def test_encode_candidates_batch_matches_the_sequential_path() -> None:
    """One batched encode of N candidates equals N separate encodes, stacked."""
    model = _model()
    candidates = []
    for transition in _transitions():
        for step in transition["prefix_steps"]:
            classes = step.get("step_input", {}).get("allowed_semantic_classes", [])
            if classes:
                prepared = _prepare_step(step, field="probe") if not step["forced_stop"] else None
                if prepared is not None and prepared.step_input is not None:
                    candidates.extend(
                        item.semantic_row for item in prepared.step_input.allowed_semantic_classes
                    )
        if len(candidates) >= 64:
            break
    assert len(candidates) >= 8, "expected real candidates to encode"
    candidates = candidates[:64]

    sequential = torch.stack([model.encode_candidate(item) for item in candidates])
    batched = model.encode_candidates_batch(candidates)

    assert batched.shape == sequential.shape
    # Batched GEMM reassociates the sums inside each linear/LayerNorm, so the
    # two paths agree only to float32 rounding -- but they must agree to
    # *rounding*, not merely to something small.  Measure the deviation against
    # the scale of the values themselves: anything beyond a few ULP of the
    # largest activation would mean the batched path computes something else.
    # (Measured on this corpus: max |diff| = 1.4e-06 at max |value| = 3.13,
    # i.e. about 4 ULP of float32.)
    with torch.no_grad():
        deviation = (batched - sequential).abs().max()
        scale = sequential.abs().max()
    ulp = torch.finfo(torch.float32).eps * scale
    assert deviation <= 16 * ulp, (
        f"batched encode deviates by {float(deviation):.3e}, more than 16 ULP "
        f"({float(16 * ulp):.3e}) of the largest activation {float(scale):.3f} -- "
        "that is a different computation, not rounding"
    )


def test_prepared_scoring_matches_the_unbatched_reference_log_probability() -> None:
    model = _model()
    target = make_trajectory_target_log_probability_v1(model)

    checked = 0
    for transition in _transitions():
        reference = float(_reference_log_probability(model, transition).detach())
        optimized = float(target(transition).detach())
        assert optimized == pytest.approx(reference, rel=0.0, abs=1e-5), (
            f"prepared scoring drifted from the plain path: {optimized} vs {reference}"
        )
        checked += 1
    assert checked >= 8, f"only {checked} transitions compared"


def test_prepared_scoring_matches_the_reference_gradient() -> None:
    """Same value is not enough: the gradient the optimizer consumes must match too."""
    transitions = _transitions(limit=16)

    reference_model = _model(seed=7)
    optimized_model = _model(seed=7)
    # Same seed => same initial weights; verify rather than assume.
    for a, b in zip(reference_model.parameters(), optimized_model.parameters(), strict=True):
        assert torch.equal(a, b)

    reference_loss = torch.stack(
        [_reference_log_probability(reference_model, t) for t in transitions]
    ).sum()
    reference_loss.backward()

    target = make_trajectory_target_log_probability_v1(optimized_model)
    optimized_loss = torch.stack([target(t) for t in transitions]).sum()
    optimized_loss.backward()

    assert float(optimized_loss.detach()) == pytest.approx(float(reference_loss.detach()), rel=0.0, abs=1e-4)

    _assert_gradients_match(reference_model, optimized_model)


def test_preparing_a_transition_does_not_change_what_scoring_it_yields() -> None:
    """A prepared transition and its raw payload score identically."""
    model = _model()
    target = make_trajectory_target_log_probability_v1(model)
    for transition in _transitions(limit=12):
        raw = float(target(transition).detach())
        prepared = float(target(prepare_trajectory_target_transition_v1(transition)).detach())
        assert prepared == raw


def test_a_prepared_transition_is_still_readable_as_its_own_payload() -> None:
    """Preparation must not hide fields the V-trace bridge reads off a transition."""
    for transition in _transitions(limit=4):
        prepared = prepare_trajectory_target_transition_v1(transition)
        assert isinstance(prepared, dict)
        assert set(prepared) == set(transition)
        for key, value in transition.items():
            assert prepared[key] == value
            assert prepared.get(key) == value


# --------------------------------------------------------------------------
# A cache shared across many decisions makes several losses depend on one
# encoded-candidate graph node. Autograd sums those contributions, which must
# equal what re-encoding the candidate per decision produces -- otherwise the
# 18.6x candidate reuse this buys would be bought with a wrong gradient.
# --------------------------------------------------------------------------


def test_a_shared_candidate_cache_gives_the_same_loss_and_gradient() -> None:
    transitions = _transitions(limit=32)

    per_decision_model = _model(seed=13)
    shared_model = _model(seed=13)
    for a, b in zip(per_decision_model.parameters(), shared_model.parameters(), strict=True):
        assert torch.equal(a, b)

    per_decision = make_trajectory_target_log_probability_v1(
        per_decision_model, shared_candidate_cache=False
    )
    shared = make_trajectory_target_log_probability_v1(
        shared_model, shared_candidate_cache=True
    )

    per_decision_loss = torch.stack([per_decision(t) for t in transitions]).sum()
    shared_loss = torch.stack([shared(t) for t in transitions]).sum()
    per_decision_loss.backward()
    shared_loss.backward()

    assert float(shared_loss.detach()) == pytest.approx(
        float(per_decision_loss.detach()), rel=0.0, abs=1e-4
    )

    _assert_gradients_match(per_decision_model, shared_model)


def test_a_shared_cache_actually_reuses_rather_than_re_encoding() -> None:
    """Guards the optimization itself: if reuse stopped happening, this fails."""
    model = _model(seed=13)
    calls = {"n": 0}
    original = model.encode_candidates_batch

    def counting(actions):
        calls["n"] += len(actions)
        return original(actions)

    model.encode_candidates_batch = counting  # type: ignore[method-assign]
    target = make_trajectory_target_log_probability_v1(model, shared_candidate_cache=True)
    transitions = _transitions(limit=32)
    for transition in transitions:
        target(transition)
    encoded = calls["n"]

    occurrences = 0
    for transition in transitions:
        for index, step in enumerate(transition["prefix_steps"]):
            if step["forced_stop"]:
                continue
            prepared = _prepare_step(step, field=f"prefix_steps[{index}]")
            assert prepared.step_input is not None
            occurrences += len(prepared.step_input.allowed_semantic_classes)
            occurrences += len(prepared.step_input.semantic_prefix)

    assert encoded < occurrences, (
        f"shared cache encoded {encoded} candidates for {occurrences} occurrences -- no reuse"
    )
