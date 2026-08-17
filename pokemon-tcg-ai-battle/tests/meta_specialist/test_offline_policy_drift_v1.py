"""Training on a frozen corpus must not drive the policy away from it without bound.

``-(advantage * log_pi)`` is unbounded below for a negative advantage, so nothing
in the V-trace surrogate alone stops an offline learner from driving the
log-probability of every collected action toward negative infinity.  Once it
does, the importance ratios collapse to zero, V-trace scales the gradient to
nothing, and the run stops learning having learned only "do not do what the data
did" (measured: ``docs/evidence/vtrace-degenerate-collapse-20260804.md``).

The design's recipe is "Policy/value/entropy/BC losses", and names
``rule_bc_vtrace`` for exactly this situation.  The BC term is the anchor: it is
what makes V-trace's "importance ratios correct subject behavior lag only"
assumption survivable when the pool is a fixed corpus rather than a
continuously refreshed one.

This test is the regression guard for that anchor.
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
    TrajectoryScorerV1,
    prepare_trajectory_target_transition_v1,
)
from mage_ptcg.meta_specialist.vtrace_bridge_v1 import (  # noqa: E402
    accumulate_trajectory_losses_v1,
    evaluate_trajectory_loss_v1,
)


ROOT = Path(__file__).resolve().parents[2]
COLLECTED = ROOT / "runs/meta-specialist-actor-pool/cli-smoke-test-alakazam-4/games"

pytestmark = pytest.mark.skipif(
    not COLLECTED.is_dir(),
    reason="no collected trajectories present; regenerate with collect-trajectories",
)


def _records():
    records = []
    for path in sorted(COLLECTED.glob("*/record.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        records.append({
            **record,
            "transitions": [
                prepare_trajectory_target_transition_v1(item)
                for item in record["transitions"]
            ],
        })
    assert records
    return records


def _train_and_measure_drift(*, bc_coefficient: float, steps: int = 30) -> list[float]:
    """Return mean(log pi_target - log pi_behavior) after each step."""
    torch.set_num_threads(1)
    vocabulary = load_production_card_vocabulary_v1()
    model = build_specialist_policy_model_v1(
        SpecialistModelConfigV1(card_vocabulary_size=max(vocabulary.recognized_card_ids)),
        seed=3,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=1.0e-3)
    records = _records()

    drift: list[float] = []
    for _ in range(steps):
        scorer = TrajectoryScorerV1(model, shared_candidate_cache=True)
        losses, shifts = [], []
        for record in records:
            transitions = record["transitions"]
            losses.append(evaluate_trajectory_loss_v1(
                transitions, target_log_probability=scorer.log_probability,
                state_value=scorer.value, entropy=scorer.entropy,
                bootstrap_value=0.0, rho_bar=1.0, c_bar=1.0,
            ))
            shifts.extend(
                float(scorer.log_probability(item).detach())
                - float(item["behavior_log_probability"])
                for item in transitions
            )
        merged = accumulate_trajectory_losses_v1(losses)
        loss = merged.total(
            value_coefficient=0.5, entropy_coefficient=0.01,
            bc_coefficient=bc_coefficient,
        ) / merged.weight_sum
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        drift.append(sum(shifts) / len(shifts))
    return drift


def test_without_the_bc_anchor_the_policy_runs_away_from_the_data() -> None:
    """Characterizes the failure the anchor exists to prevent.

    If this ever stops diverging, the surrogate changed and the guard below is
    no longer testing what it claims to test.
    """
    drift = _train_and_measure_drift(bc_coefficient=0.0)
    assert min(drift) < -2.0, (
        "expected the unanchored surrogate to drive log-probabilities down; "
        f"worst drift was only {min(drift):.3f}"
    )


def test_the_bc_anchor_keeps_the_policy_near_the_collected_behavior() -> None:
    drift = _train_and_measure_drift(bc_coefficient=0.1)

    assert min(drift) > -1.0, (
        f"policy drifted to {min(drift):.3f} despite the BC anchor; "
        "an offline run that leaves the data's support stops learning"
    )
    # And it must be going the right way by the end, not merely bounded.
    assert drift[-1] > drift[0], (
        f"drift did not improve: {drift[0]:.3f} -> {drift[-1]:.3f}"
    )


def test_a_stronger_anchor_holds_the_policy_closer_still() -> None:
    """Monotone in the coefficient, so the knob means what it says."""
    weak = _train_and_measure_drift(bc_coefficient=0.1)[-1]
    strong = _train_and_measure_drift(bc_coefficient=1.0)[-1]
    assert strong >= weak - 1e-6, f"stronger anchor drifted further: {strong} < {weak}"
