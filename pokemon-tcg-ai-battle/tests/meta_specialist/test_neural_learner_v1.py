"""L3 learner: microbatch-invariant steps, OOM shrink safety, and finite guards."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_batch_v1 import (  # noqa: E402
    build_ragged_step_batch_v1,
)
from mage_ptcg.meta_specialist.neural_learner_v1 import (  # noqa: E402
    NeuralLearnerV1Error,
    accumulate_batch_loss_v1,
    training_step_v1,
)

from tests.meta_specialist.test_neural_batch_v1 import _fixture  # noqa: E402


class _LinearScorer(torch.nn.Module):
    """A tiny learnable scorer standing in for the full candidate model."""

    def __init__(self, width: int = 8) -> None:
        super().__init__()
        self.width = width
        self.table = torch.nn.Parameter(torch.zeros(width, dtype=torch.float64))

    def forward(self, examples) -> torch.Tensor:
        batch = build_ragged_step_batch_v1(examples)
        base = torch.arange(batch.max_tokens, dtype=torch.float64).unsqueeze(0)
        return base + self.table[: batch.max_tokens].unsqueeze(0).expand(batch.rows, -1)


def _examples(copies: int = 3):
    base, _targets = _fixture()
    out = []
    for index in range(copies):
        for example in base:
            out.append({
                "loss_rows": example["loss_rows"],
                "example_quality_weight": example["example_quality_weight"] * (1.0 + index * 0.1),
            })
    return out


def _grad(model, examples, *, microbatch):
    optimizer = torch.optim.SGD(model.parameters(), lr=0.0)  # lr 0: inspect gradient only
    result = training_step_v1(
        examples, model=model, optimizer=optimizer, row_logits=model,
        microbatch_examples=microbatch, max_gradient_norm=None,
    )
    return result


def test_every_microbatch_size_yields_the_same_loss_and_gradient() -> None:
    examples = _examples()
    reference_grad = None
    reference_loss = None
    for microbatch in (1, 2, 3, len(examples)):
        model = _LinearScorer()
        # Capture the gradient before the (zero-lr) step clears it.
        optimizer = torch.optim.SGD(model.parameters(), lr=0.0)
        loss_sum, weight_sum, rows, chunks = accumulate_batch_loss_v1(
            examples, row_logits=model, microbatch_examples=microbatch
        )
        (loss_sum / weight_sum).backward()
        gradient = model.table.grad.clone()
        loss = float((loss_sum / weight_sum).detach())
        optimizer.zero_grad(set_to_none=True)

        assert chunks == -(-len(examples) // microbatch)
        if reference_grad is None:
            reference_grad, reference_loss = gradient, loss
        else:
            assert loss == pytest.approx(reference_loss, abs=1e-15)
            assert torch.allclose(gradient, reference_grad, atol=1e-15, rtol=0.0)


def test_an_oom_shrink_retry_takes_the_same_step_as_the_full_batch() -> None:
    examples = _examples()

    full = _LinearScorer()
    baseline = _grad(full, examples, microbatch=len(examples))

    shrinking = _LinearScorer()
    calls = {"n": 0}
    original = _LinearScorer.forward

    def flaky(self, chunk):
        calls["n"] += 1
        # Fail only on the first, widest attempt so the learner must shrink.
        if calls["n"] == 1 and len(chunk) == len(examples):
            raise RuntimeError("CUDA out of memory")
        return original(self, chunk)

    _LinearScorer.forward = flaky
    try:
        retried = _grad(shrinking, examples, microbatch=len(examples))
    finally:
        _LinearScorer.forward = original

    assert retried.microbatches > baseline.microbatches
    assert retried.loss == pytest.approx(baseline.loss, abs=1e-15)
    assert retried.weight_sum == pytest.approx(baseline.weight_sum, abs=1e-15)
    assert retried.gradient_norm == pytest.approx(baseline.gradient_norm, abs=1e-12)
    assert not retried.skipped


def test_a_step_is_actually_taken_and_reported() -> None:
    examples = _examples()
    model = _LinearScorer()
    before = model.table.detach().clone()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.5)

    result = training_step_v1(examples, model=model, optimizer=optimizer, row_logits=model)

    assert not result.skipped
    assert result.examples == len(examples)
    assert result.rows > 0 and result.microbatches == 1
    assert not torch.equal(model.table.detach(), before)
    assert result.to_dict()["schema_version"] == "specialist-neural-learner-v1"
    assert model.table.grad is None  # gradients are cleared after the step


def test_a_nonfinite_gradient_skips_the_step_instead_of_corrupting_weights() -> None:
    examples = _examples()
    model = _LinearScorer()
    before = model.table.detach().clone()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.5)

    original = _LinearScorer.forward

    def poisoned(self, chunk):
        return original(self, chunk) + self.table.sum() * float("inf") * 0.0 + torch.tensor(
            float("nan"), dtype=torch.float64
        ) * self.table.sum()

    _LinearScorer.forward = poisoned
    try:
        result = training_step_v1(
            examples, model=model, optimizer=optimizer, row_logits=model
        )
    except Exception as exc:  # a non-finite logit is rejected even earlier
        assert "finite" in str(exc)
        assert torch.equal(model.table.detach(), before)
        return
    finally:
        _LinearScorer.forward = original

    assert result.skipped
    assert torch.equal(model.table.detach(), before)


def test_zero_total_quality_weight_skips_rather_than_dividing_by_zero() -> None:
    examples = [dict(item, example_quality_weight=0.0) for item in _examples()]
    model = _LinearScorer()
    before = model.table.detach().clone()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.5)

    result = training_step_v1(examples, model=model, optimizer=optimizer, row_logits=model)

    assert result.skipped and result.weight_sum == 0.0 and result.loss == 0.0
    assert torch.equal(model.table.detach(), before)


def test_learner_validates_its_inputs() -> None:
    model = _LinearScorer()
    with pytest.raises(NeuralLearnerV1Error, match="at least one example"):
        accumulate_batch_loss_v1([], row_logits=model, microbatch_examples=1)
    with pytest.raises(NeuralLearnerV1Error, match="positive int"):
        accumulate_batch_loss_v1(_examples(), row_logits=model, microbatch_examples=0)
    with pytest.raises(NeuralLearnerV1Error, match="one padded logit row per batch row"):
        accumulate_batch_loss_v1(
            _examples(), row_logits=lambda chunk: torch.zeros(1, 1, dtype=torch.float64),
            microbatch_examples=2,
        )
