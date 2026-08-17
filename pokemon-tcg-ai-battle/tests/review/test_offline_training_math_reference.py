"""Independent reference-implementation checks for the neural training math.

Pure-Python references (no torch in the reference path) verify masked softmax,
cross entropy, top-k / MRR / NLL metrics, mask semantics, and tie-breaking, and
document the microbatch loss-accumulation weighting of the implementation.
"""

from __future__ import annotations

import math
import random

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.offline_training import neural
from mage_ptcg.offline_training.dataset import Decision


# --------------------------------------------------------------------------- #
# Pure-Python references
# --------------------------------------------------------------------------- #


def ref_masked_log_softmax(scores: list[float], mask: list[bool]) -> list[float]:
    legal = [s for s, m in zip(scores, mask) if m]
    assert legal, "reference requires at least one legal candidate"
    peak = max(legal)
    denominator = sum(math.exp(s - peak) for s, m in zip(scores, mask) if m)
    out = []
    for s, m in zip(scores, mask):
        out.append((s - peak) - math.log(denominator) if m else -math.inf)
    return out


def ref_cross_entropy(scores: list[float], mask: list[bool], targets: list[int]) -> float:
    log_probs = ref_masked_log_softmax(scores, mask)
    share = 1.0 / len(targets)
    return -sum(share * log_probs[t] for t in targets)


def _decision(features, targets, digests=None, selection_type="0"):
    n = len(features)
    return Decision(
        example_id=f"ex-{random.getrandbits(32):08x}",
        source_id="src",
        split="train",
        selection_type=selection_type,
        candidate_digests=tuple(digests or [f"d{i:02d}" for i in range(n)]),
        candidate_features=tuple(tuple(row) for row in features),
        target_indices=tuple(targets),
        min_count=1,
    )


def _identity_module(dim: int):
    """A 1-layer linear module with fixed weights so scores are analytic."""
    import torch.nn as nn

    module = nn.Sequential(nn.Linear(dim, 1))
    with torch.no_grad():
        module[0].weight.copy_(torch.arange(1.0, dim + 1.0).unsqueeze(0))
        module[0].bias.zero_()
    return module


# --------------------------------------------------------------------------- #
# Masked softmax semantics
# --------------------------------------------------------------------------- #


def test_masked_log_softmax_matches_reference_and_blocks_illegal():
    rng = random.Random(7)
    for _case in range(200):
        n = rng.randint(1, 9)
        legal_n = rng.randint(1, n)
        scores = [rng.uniform(-50, 50) for _ in range(n)]
        mask = [i < legal_n for i in range(n)]
        t_scores = torch.tensor([scores], dtype=torch.float32)
        t_mask = torch.tensor([mask], dtype=torch.bool)
        got = neural._masked_log_softmax(t_scores, t_mask, torch)[0].tolist()
        want = ref_masked_log_softmax(scores, mask)
        for g, w, m in zip(got, want, mask):
            if m:
                assert abs(g - w) < 1e-4, (g, w)
            else:
                # illegal candidates must carry zero probability mass
                assert math.exp(g) < 1e-30
        legal_mass = sum(math.exp(g) for g, m in zip(got, mask) if m)
        assert abs(legal_mass - 1.0) < 1e-5


def test_masked_log_softmax_survives_extreme_scores():
    scores = torch.tensor([[3.0e38, -3.0e38, 0.0]], dtype=torch.float32)
    mask = torch.tensor([[True, True, False]], dtype=torch.bool)
    got = neural._masked_log_softmax(scores, mask, torch)[0]
    assert torch.isfinite(got[0]), "max-score candidate must have finite log-prob"
    # the -inf tail for the dominated legal candidate is mathematically correct
    assert math.exp(float(got[2])) < 1e-30


def test_forward_loss_matches_reference_cross_entropy():
    dim = 4
    module = _identity_module(dim)
    mean = [0.0] * dim
    std = [1.0] * dim
    rng = random.Random(3)
    for _case in range(50):
        rows = [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(rng.randint(1, 5))]
        targets = [rng.randrange(len(rows))]
        decision = _decision(rows, targets)
        loss = neural._forward_loss(module, [decision], mean, std, torch, torch.device("cpu"), False)
        weights = list(range(1, dim + 1))
        scores = [sum(w * x for w, x in zip(weights, row)) for row in rows]
        want = ref_cross_entropy(scores, [True] * len(rows), targets)
        assert abs(float(loss.detach()) - want) < 1e-4


def test_forward_loss_none_when_no_supervised_decision():
    dim = 3
    module = _identity_module(dim)
    decision = _decision([[0.1] * dim, [0.2] * dim], targets=[])
    loss = neural._forward_loss(module, [decision], [0.0] * dim, [1.0] * dim, torch, torch.device("cpu"), False)
    assert loss is None


# --------------------------------------------------------------------------- #
# Metrics reference (top-1/top-3/MRR/NLL, tie-break determinism)
# --------------------------------------------------------------------------- #


def test_evaluate_module_metrics_match_reference():
    dim = 4
    module = _identity_module(dim)
    mean = [0.0] * dim
    std = [1.0] * dim
    rng = random.Random(13)
    decisions = []
    for _ in range(40):
        rows = [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(rng.randint(1, 6))]
        targets = sorted(rng.sample(range(len(rows)), rng.randint(1, min(2, len(rows)))))
        decisions.append(_decision(rows, targets))
    got = neural.evaluate_module(module, decisions, mean, std, torch=torch, device=torch.device("cpu"))

    weights = list(range(1, dim + 1))
    total = top1 = top3 = 0
    mrr = nll = 0.0
    for decision in decisions:
        scores = [sum(w * x for w, x in zip(weights, row)) for row in decision.candidate_features]
        log_probs = ref_masked_log_softmax(scores, [True] * len(scores))
        order = sorted(range(len(scores)), key=lambda i: (-log_probs[i], decision.candidate_digests[i], i))
        target_set = set(decision.target_indices)
        total += 1
        top1 += order[0] in target_set
        top3 += any(i in target_set for i in order[:3])
        rank = next(pos for pos, i in enumerate(order, start=1) if i in target_set)
        mrr += 1.0 / rank
        nll += -math.log(max(sum(math.exp(log_probs[i]) for i in target_set), 1e-12))
    assert got["decisions"] == total
    assert abs(got["top1"] - top1 / total) < 1e-9
    assert abs(got["top3"] - top3 / total) < 1e-9
    assert abs(got["mrr"] - mrr / total) < 1e-6
    assert abs(got["nll"] - nll / total) < 1e-4


def test_tie_break_is_deterministic_by_digest_then_index():
    dim = 2
    module = _identity_module(dim)
    rows = [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]  # identical scores force a tie
    digests = ("zz", "aa", "mm")
    decision = _decision(rows, targets=[1], digests=digests)
    got = neural.evaluate_module(module, [decision], [0.0] * dim, [1.0] * dim, torch=torch, device=torch.device("cpu"))
    # digest "aa" (index 1) wins the tie and is the target -> top1 == 1
    assert got["top1"] == 1.0


def test_evaluate_module_raises_on_unsupervised_only():
    dim = 2
    module = _identity_module(dim)
    decision = _decision([[0.1, 0.2]], targets=[])
    with pytest.raises(neural.NeuralError):
        neural.evaluate_module(module, [decision], [0.0] * dim, [1.0] * dim, torch=torch, device=torch.device("cpu"))


# --------------------------------------------------------------------------- #
# Candidate shuffle invariance of per-candidate scoring
# --------------------------------------------------------------------------- #


def test_candidate_shuffle_only_permutes_log_probs():
    dim = 3
    module = _identity_module(dim)
    rng = random.Random(5)
    rows = [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(5)]
    base = _decision(rows, targets=[0])
    perm = [3, 0, 4, 1, 2]
    shuffled = _decision([rows[p] for p in perm], targets=[perm.index(0)])
    mean, std = [0.0] * dim, [1.0] * dim
    f1, m1, _ = neural._pad_batch([base], mean, std, torch, torch.device("cpu"))
    f2, m2, _ = neural._pad_batch([shuffled], mean, std, torch, torch.device("cpu"))
    with torch.no_grad():
        s1 = neural._masked_log_softmax(module(f1).squeeze(-1), m1, torch)[0].tolist()
        s2 = neural._masked_log_softmax(module(f2).squeeze(-1), m2, torch)[0].tolist()
    for new_index, p in enumerate(perm):
        assert abs(s2[new_index] - s1[p]) < 1e-6


# --------------------------------------------------------------------------- #
# REV-E1 fix: microbatch accumulation must equal the full-batch gradient
# --------------------------------------------------------------------------- #


def _batch_for_split_test(dim: int):
    """5 decisions (one unsupervised in the tail micro) so a microbatch of 2
    produces the required unequal 2+2+1 split."""
    rng = random.Random(23)
    rows = lambda n: [[rng.uniform(-1, 1) for _ in range(dim)] for _ in range(n)]
    batch = [_decision(rows(2), targets=[0]) for _ in range(4)]
    batch.append(_decision(rows(3), targets=[]))
    return batch


def _run_production_step(batch, dim: int, micro_size: int):
    """Drive the production accumulation (`neural._train_batch_once`) once."""
    torch.manual_seed(0)
    module = _identity_module(dim)
    optimizer = torch.optim.AdamW(module.parameters(), lr=1e-2, weight_decay=1e-4)
    loss = neural._train_batch_once(
        module, optimizer, batch, microbatch=micro_size, mean=[0.0] * dim, std=[1.0] * dim,
        torch=torch, device=torch.device("cpu"), use_bf16=False, grad_clip=1e9,
    )
    grad = module[0].weight.grad.clone()  # grads survive optimizer.step()
    steps = int(optimizer.state[module[0].weight]["step"])
    return loss, grad, module[0].weight.detach().clone(), steps


def test_unequal_microbatch_gradient_matches_full_batch():
    """REV-E1 fix: the production accumulation over a 5-decision batch split
    2+2+1 must equal the single full-batch step -- same reported loss, same
    gradient, same updated parameters, and exactly one optimizer step."""
    dim = 3
    batch = _batch_for_split_test(dim)
    full_loss, full_grad, full_weight, full_steps = _run_production_step(batch, dim, 5)
    split_loss, split_grad, split_weight, split_steps = _run_production_step(batch, dim, 2)
    assert abs(full_loss - split_loss) < 1e-6
    assert torch.allclose(full_grad, split_grad, atol=1e-7), "gradient differs across micro splits"
    assert torch.allclose(full_weight, split_weight, atol=1e-7), "updated parameters differ"
    assert full_steps == split_steps == 1


def test_microbatch_gradient_matches_reference_mean():
    """The production accumulated gradient equals an independent reference:
    the gradient of the mean cross entropy over the supervised decisions."""
    dim = 3
    batch = _batch_for_split_test(dim)
    _loss, split_grad, _weight, _steps = _run_production_step(batch, dim, 2)

    torch.manual_seed(0)
    module = _identity_module(dim)
    supervised = [d for d in batch if d.target_indices and d.candidate_features]
    reference = neural._forward_loss(module, supervised, [0.0] * dim, [1.0] * dim, torch, torch.device("cpu"), False)
    module.zero_grad()
    reference.backward()
    assert torch.allclose(module[0].weight.grad, split_grad, atol=1e-7), (
        "accumulated gradient is not the mean over supervised decisions"
    )
