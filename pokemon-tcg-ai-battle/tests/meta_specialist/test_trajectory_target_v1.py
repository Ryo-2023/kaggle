"""Recomputing a stored action's log-probability, and the collect→train loop it closes."""

from __future__ import annotations

import copy
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
    TrajectoryTargetV1Error,
    make_trajectory_target_log_probability_v1,
)
from mage_ptcg.meta_specialist.vtrace_bridge_v1 import (  # noqa: E402
    accumulate_trajectory_losses_v1,
    admit_trajectories_v1,
    evaluate_trajectory_loss_v1,
)


ROOT = Path(__file__).resolve().parents[2]
COLLECTED = ROOT / "runs/meta-specialist-actor-pool/cli-smoke-test-alakazam-4/games"

pytestmark = pytest.mark.skipif(
    not COLLECTED.is_dir(),
    reason="no collected trajectories present; regenerate with collect-trajectories",
)


def _records():
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(COLLECTED.glob("*/record.json"))
    ]
    assert records, "expected at least one collected game"
    return records


def _model():
    vocabulary = load_production_card_vocabulary_v1()
    config = SpecialistModelConfigV1(
        card_vocabulary_size=max(vocabulary.recognized_card_ids)
    )
    return build_specialist_policy_model_v1(config, seed=1)


def test_the_target_is_differentiable_and_finite_on_real_transitions() -> None:
    model = _model()
    target = make_trajectory_target_log_probability_v1(model)
    transitions = [t for record in _records() for t in record["transitions"]]

    assert len(transitions) >= 1
    for transition in transitions[:8]:
        value = target(transition)
        assert value.requires_grad
        assert torch.isfinite(value)
        # A log-probability is never positive.
        assert float(value.detach()) <= 1e-12


def test_the_target_scores_the_stored_action_not_a_rechosen_one() -> None:
    """Changing which action was stored must change the recomputed value.

    The swap is applied to the FINAL scoreable step so the decode chain stays
    self-consistent; the module rejects a payload whose later prefixes no longer
    follow from the earlier chosen tokens, which is a separate guarantee tested
    below.
    """
    model = _model()
    target = make_trajectory_target_log_probability_v1(model)
    transitions = [t for record in _records() for t in record["transitions"]]

    def last_multi_index(transition):
        found = None
        for index, step in enumerate(transition["prefix_steps"]):
            classes = step.get("step_input", {}).get("allowed_semantic_classes", [])
            if len(classes) > 1 and not step.get("forced_stop"):
                found = index
        return found

    candidate = None
    for transition in transitions:
        index = last_multi_index(transition)
        if index is not None and index == len(transition["prefix_steps"]) - 1:
            candidate = (transition, index)
            break
    if candidate is None:
        pytest.skip("no collected decision ends on a step with two legal classes")

    transition, index = candidate
    baseline = float(target(transition).detach())
    swapped = copy.deepcopy(transition)
    step = swapped["prefix_steps"][index]
    classes = step["step_input"]["allowed_semantic_classes"]
    current = step["chosen_token"]["semantic_action"]
    other = next(item["semantic_row"] for item in classes if item["semantic_row"] != current)
    step["chosen_token"] = {"kind": "semantic", "semantic_action": other}

    assert float(target(swapped).detach()) != baseline


def test_a_broken_decode_chain_is_rejected() -> None:
    """A later prefix must follow from the earlier chosen tokens."""
    model = _model()
    target = make_trajectory_target_log_probability_v1(model)
    transitions = [t for record in _records() for t in record["transitions"]]

    chained = next(
        (
            t
            for t in transitions
            if len(t["prefix_steps"]) > 1
            and len(
                t["prefix_steps"][0].get("step_input", {}).get("allowed_semantic_classes", [])
            ) > 1
            and not t["prefix_steps"][0].get("forced_stop")
        ),
        None,
    )
    if chained is None:
        pytest.skip("no multi-step decision with a choosable first step")

    broken = copy.deepcopy(chained)
    step = broken["prefix_steps"][0]
    classes = step["step_input"]["allowed_semantic_classes"]
    current = step["chosen_token"]["semantic_action"]
    other = next(item["semantic_row"] for item in classes if item["semantic_row"] != current)
    step["chosen_token"] = {"kind": "semantic", "semantic_action": other}

    with pytest.raises(TrajectoryTargetV1Error, match="chain|rebuilt"):
        target(broken)


def test_a_corrupted_transition_raises_instead_of_returning_a_default() -> None:
    model = _model()
    target = make_trajectory_target_log_probability_v1(model)
    transition = _records()[0]["transitions"][0]

    with pytest.raises(TrajectoryTargetV1Error):
        target({})
    without_input = copy.deepcopy(transition)
    without_input.pop("model_input")
    with pytest.raises(TrajectoryTargetV1Error):
        target(without_input)

    illegal = copy.deepcopy(transition)
    for step in illegal["prefix_steps"]:
        if not step.get("forced_stop") and step.get("chosen_token", {}).get("kind") == "semantic":
            # An action that was never in this step's own legal set.
            step["chosen_token"]["semantic_action"]["option_type"] = 31
            break
    else:
        pytest.skip("no scoreable semantic step to corrupt")
    with pytest.raises(TrajectoryTargetV1Error):
        target(illegal)


def test_collected_trajectories_drive_a_real_optimizer_step() -> None:
    """The whole loop: collected games -> V-trace loss -> weights actually move.

    Uses the full scorer -- log-probability, model value, and entropy -- because
    that is what a training step runs. Scoring with only the log-probability
    would leave the value head without a gradient, and this test's whole point
    is that *every* parameter the model has is reached.
    """
    model = _model()
    scorer = TrajectoryScorerV1(model)
    target = scorer.log_probability
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

    kept, admission = admit_trajectories_v1(
        _records(), current_pool_epoch=0, recipe_max_age=0
    )
    assert admission.admitted == len(_records()) and admission.dropped == 0

    before = [parameter.detach().clone() for parameter in model.parameters()]
    merged = accumulate_trajectory_losses_v1([
        evaluate_trajectory_loss_v1(
            record["transitions"], target_log_probability=target,
            state_value=scorer.value, entropy=scorer.entropy,
            bootstrap_value=0.0, rho_bar=1.0, c_bar=1.0,
        )
        for record in kept
    ])
    loss = merged.total(value_coefficient=0.5, entropy_coefficient=0.01) / merged.weight_sum
    optimizer.zero_grad()
    loss.backward()
    gradient_norm = torch.sqrt(
        sum((p.grad**2).sum() for p in model.parameters() if p.grad is not None)
    )
    optimizer.step()

    assert merged.steps == sum(len(record["transitions"]) for record in kept)
    assert torch.isfinite(loss) and torch.isfinite(gradient_norm)
    assert float(gradient_norm) > 0.0
    moved = sum(
        1 for a, b in zip(before, model.parameters(), strict=True)
        if not torch.equal(a, b.detach())
    )
    assert moved == len(before), f"only {moved}/{len(before)} parameters moved"
