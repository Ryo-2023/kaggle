"""L3 model adapter: real snapshot examples driven through SpecialistPolicyModelV1.

Every example here comes from a real, published training snapshot (built by
``tests.meta_specialist.test_training_snapshot_v1._build``), not a synthetic
dict, so the tests exercise the actual ``SpecialistModelInputV1`` /
``SpecialistStepInputV1`` reconstruction path end to end.
"""

from __future__ import annotations

import copy
import math

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (  # noqa: E402
    extract_specialist_model_input_v1,
    make_test_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (  # noqa: E402
    build_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import (  # noqa: E402
    canonical_json_bytes_v2,
    semantic_loss_rows_from_record_v2,
)
from mage_ptcg.meta_specialist.neural_adapter_v1 import (  # noqa: E402
    NeuralAdapterV1Error,
    make_specialist_row_logits_v1,
)
from mage_ptcg.meta_specialist.neural_batch_v1 import build_ragged_step_batch_v1  # noqa: E402
from mage_ptcg.meta_specialist.neural_learner_v1 import (  # noqa: E402
    accumulate_batch_loss_v1,
    training_step_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    SpecialistModelConfigV1,
    SpecialistPolicyModelV1,
    build_specialist_policy_model_v1,
)

from tests.meta_specialist.test_training_example_envelope_v2 import (  # noqa: E402
    _observation,
    _teacher_record,
)
from tests.meta_specialist.test_training_snapshot_v1 import _build  # noqa: E402


CARD_VOCABULARY_SIZE = 1_000  # covers _unordered_multirow_example's range(1, 1000) vocabulary


def _config() -> SpecialistModelConfigV1:
    return SpecialistModelConfigV1(card_vocabulary_size=CARD_VOCABULARY_SIZE)


def _examples(tmp_path, **kwargs):
    snapshot, *_rest = _build(tmp_path, **kwargs)
    return list(snapshot["examples"])


def _unordered_multirow_example() -> dict[str, object]:
    """A real, multi-row, non-tampered example with a legitimately STOP-available row.

    Reuses ``test_unordered_alias_min_max_optional_and_forced_stop_match_l2_oracle``'s
    own fixture shape (an unordered min=1/max=2 hand selection with an alias pair),
    pushed through the same production ``semantic_loss_rows_from_record_v2`` the
    real snapshot pipeline uses. Unlike ``_build``'s single YES/NO decision, depth 1
    here legitimately has ``len(prefix) == 1 >= min_count(1)``, so STOP is available
    without any tampering -- this exercises the adapter's real STOP-available path.
    """
    observation = _observation()
    cards = [
        {"id": 101, "serial": 1001, "playerIndex": 0},
        {"id": 101, "serial": 1002, "playerIndex": 0},
        {"id": 102, "serial": 1003, "playerIndex": 0},
    ]
    observation["current"]["players"][0]["hand"] = cards  # type: ignore[index]
    observation["current"]["players"][0]["handCount"] = 3  # type: ignore[index]
    observation["select"] = {
        "context": 1, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 1,
        "option": [
            {"type": 3, "area": 2, "index": index, "playerIndex": 0} for index in range(3)
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
    }
    state = build_actor_visible_decision_state_v2(observation)
    vocabulary = make_test_card_vocabulary_v1(range(1, 1000))
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    by_semantic: dict[bytes, list[str]] = {}
    for local_id, index in extracted.local_action_id_to_candidate_row_index.items():
        key = canonical_json_bytes_v2(extracted.model_input.candidate_rows[index].to_dict())
        by_semantic.setdefault(key, []).append(local_id)
    alias_ids = sorted(next(ids for ids in by_semantic.values() if len(ids) == 2))
    singleton = next(ids[0] for ids in by_semantic.values() if len(ids) == 1)
    physical = (
        ((alias_ids[0],), 0.1), ((alias_ids[1],), 0.2), ((singleton,), 0.1),
        (tuple(sorted(alias_ids)), 0.2),
        (tuple(sorted((alias_ids[0], singleton))), 0.2),
        (tuple(sorted((alias_ids[1], singleton))), 0.2),
    )
    record = _teacher_record(state, vocabulary, physical, quality=0.7)
    rows = semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary)
    return {
        "model_input": extracted.model_input.to_dict(), "loss_rows": rows,
        "example_quality_weight": 0.7,
    }


def test_row_logits_matches_the_batch_padded_shape(tmp_path) -> None:
    examples = _examples(tmp_path)
    batch = build_ragged_step_batch_v1(examples)
    model = build_specialist_policy_model_v1(_config(), seed=1)

    logits = make_specialist_row_logits_v1(model)(examples)

    assert type(logits) is torch.Tensor
    assert logits.shape == batch.token_mask.shape
    assert torch.isfinite(logits).all()
    assert logits.requires_grad


def test_training_step_actually_updates_weights_through_the_adapter(tmp_path) -> None:
    examples = _examples(tmp_path)
    model = build_specialist_policy_model_v1(_config(), seed=2)
    before = {name: value.detach().clone() for name, value in model.named_parameters()}
    optimizer = torch.optim.SGD(model.parameters(), lr=1.0)

    result = training_step_v1(
        examples, model=model, optimizer=optimizer,
        row_logits=make_specialist_row_logits_v1(model),
    )

    assert result.skipped is False
    assert math.isfinite(result.loss)
    assert result.rows == build_ragged_step_batch_v1(examples).rows
    changed = [
        name for name, value in model.named_parameters()
        if not torch.equal(value.detach(), before[name])
    ]
    assert changed, "no parameter moved after an unskipped optimizer step"


def test_microbatch_size_does_not_change_loss_or_gradient(tmp_path) -> None:
    examples = _examples(tmp_path, two=True)
    assert len(examples) == 2
    reference_loss: float | None = None
    reference_grads: dict[str, torch.Tensor] | None = None

    for microbatch in (1, 2):
        model = build_specialist_policy_model_v1(_config(), seed=11)
        loss_sum, weight_sum, rows, chunks = accumulate_batch_loss_v1(
            examples, row_logits=make_specialist_row_logits_v1(model),
            microbatch_examples=microbatch,
        )
        assert chunks == (2 if microbatch == 1 else 1)
        loss = loss_sum / weight_sum
        loss.backward()
        grads = {
            name: value.grad.clone()
            for name, value in model.named_parameters() if value.grad is not None
        }
        assert grads, "no parameter received a gradient"

        if reference_loss is None:
            reference_loss = float(loss.detach())
            reference_grads = grads
        else:
            assert float(loss.detach()) == pytest.approx(reference_loss, abs=1e-9)
            assert set(grads) == set(reference_grads)
            for name in grads:
                assert torch.allclose(
                    grads[name], reference_grads[name], atol=1e-7, rtol=1e-6
                )


def test_row_logits_handles_a_real_multirow_stop_available_decision() -> None:
    example = _unordered_multirow_example()
    examples = [example]
    rows_by_prefix_len = sorted(len(row["semantic_prefix"]) for row in example["loss_rows"])
    assert rows_by_prefix_len == [0, 1]  # depth 0 (STOP illegal) and depth 1 (STOP legal)
    assert any(
        any(token["kind"] == "stop" for token in row["token_masses"])
        for row in example["loss_rows"]
    )
    batch = build_ragged_step_batch_v1(examples)
    model = build_specialist_policy_model_v1(_config(), seed=13)

    logits = make_specialist_row_logits_v1(model)(examples)

    assert logits.shape == batch.token_mask.shape
    assert torch.isfinite(logits).all()

    result = training_step_v1(
        examples, model=model, optimizer=torch.optim.SGD(model.parameters(), lr=1.0),
        row_logits=make_specialist_row_logits_v1(model),
    )
    assert result.skipped is False
    assert result.rows == batch.rows


def test_make_specialist_row_logits_v1_requires_the_policy_model_type() -> None:
    with pytest.raises(NeuralAdapterV1Error, match="SpecialistPolicyModelV1"):
        make_specialist_row_logits_v1(object())  # type: ignore[arg-type]


def test_row_logits_fails_closed_on_a_stop_availability_mismatch(tmp_path) -> None:
    examples = _examples(tmp_path)
    tampered = copy.deepcopy(examples)
    row = tampered[0]["loss_rows"][0]
    # This row's prefix is empty and min_count is 1, so STOP must be unavailable;
    # force a STOP token into the domain to break that invariant.
    row["token_masses"].append({"kind": "stop", "mass": 0.0})
    model = build_specialist_policy_model_v1(_config(), seed=3)

    with pytest.raises(NeuralAdapterV1Error, match="STOP availability"):
        make_specialist_row_logits_v1(model)(tampered)


def test_row_logits_fails_closed_on_a_duplicated_semantic_token(tmp_path) -> None:
    examples = _examples(tmp_path)
    tampered = copy.deepcopy(examples)
    row = tampered[0]["loss_rows"][0]
    semantic_tokens = [token for token in row["token_masses"] if token["kind"] == "semantic"]
    assert len(semantic_tokens) >= 1
    row["token_masses"].insert(1, copy.deepcopy(semantic_tokens[0]))
    model = build_specialist_policy_model_v1(_config(), seed=4)

    with pytest.raises(NeuralAdapterV1Error, match="allowed-class set"):
        make_specialist_row_logits_v1(model)(tampered)


def test_row_logits_fails_closed_on_a_nonfinite_model_logit(tmp_path, monkeypatch) -> None:
    examples = _examples(tmp_path)
    model = build_specialist_policy_model_v1(_config(), seed=5)
    original = SpecialistPolicyModelV1.step_logits

    def poisoned(self, model_input, step_input):
        semantic, stop = original(self, model_input, step_input)
        return semantic + float("nan"), stop

    monkeypatch.setattr(SpecialistPolicyModelV1, "step_logits", poisoned)

    with pytest.raises(NeuralAdapterV1Error, match="non-finite"):
        make_specialist_row_logits_v1(model)(examples)


def test_row_logits_fails_closed_on_an_unsorted_semantic_token_domain(tmp_path) -> None:
    examples = _examples(tmp_path)
    tampered = copy.deepcopy(examples)
    row = tampered[0]["loss_rows"][0]
    semantic_tokens = [token for token in row["token_masses"] if token["kind"] == "semantic"]
    assert len(semantic_tokens) == 2
    # Reverse the two semantic tokens so the row's domain is no longer in the
    # canonical order SpecialistStepInputV1 requires for allowed_semantic_classes.
    row["token_masses"] = list(reversed(semantic_tokens))
    model = build_specialist_policy_model_v1(_config(), seed=7)

    with pytest.raises(NeuralAdapterV1Error, match="allowed-class set"):
        make_specialist_row_logits_v1(model)(tampered)
