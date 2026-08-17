"""Adversarial PyTorch <-> pure-Python export parity and fallback legality.

Randomized parity over >=1,000 cases across multiple seeds and architectures,
boundary inputs, JSON round-trip precision, malformed-export rejection, and the
full runtime failure matrix that must end in Rule v0 fallback (``None``).
"""

from __future__ import annotations

import json
import math
import random

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.offline_training import export as export_mod
from mage_ptcg.offline_training import neural
from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy
from mage_ptcg.student.artifact import feature_schema


def _random_export(seed: int, hidden_dims: tuple[int, ...]):
    torch.manual_seed(seed)
    dim = feature_schema()["feature_dimension"]
    spec = neural.ModelSpec(input_dim=dim, hidden_dims=hidden_dims)
    module = neural.build_module(spec)
    rng = random.Random(seed)
    mean = [rng.uniform(-0.5, 0.5) for _ in range(dim)]
    std = [rng.uniform(0.5, 2.0) for _ in range(dim)]
    document = export_mod.build_export(
        module=module, model_spec_dict=spec.to_dict(),
        normalization={"mean": mean, "std": std},
        feature_schema=feature_schema(), dataset_hash="d" * 64, config_hash="c" * 64,
        teacher_id="rule-agent-v0", model_purpose=neural.MODEL_PURPOSE_SMOKE,
    )
    return module, document, mean, std, dim


def _torch_scores(module, rows, mean, std):
    feats = (torch.tensor(rows, dtype=torch.float32) - torch.tensor(mean, dtype=torch.float32)) / torch.tensor(std, dtype=torch.float32)
    with torch.no_grad():
        return module(feats).squeeze(-1).tolist()


@pytest.mark.parametrize("seed,hidden", [(1, (32,)), (2, (64, 32)), (3, (16, 16, 16)), (4, (128,)), (5, (8,))])
def test_randomized_parity_200_cases_per_seed(seed, hidden):
    """5 seeds x 200 random candidate rows = 1,000 parity cases total."""
    module, document, mean, std, dim = _random_export(seed, hidden)
    rng = random.Random(seed * 1000)
    max_diff = 0.0
    for _case in range(200):
        row = [rng.uniform(-5.0, 5.0) for _ in range(dim)]
        t = _torch_scores(module, [row], mean, std)[0]
        p = export_mod.score_candidate(document, row)
        max_diff = max(max_diff, abs(t - p))
    assert max_diff < 1e-4, f"parity drift {max_diff} (seed={seed}, hidden={hidden})"


def test_ranking_and_top1_agreement_excluding_float_ties():
    module, document, mean, std, dim = _random_export(11, (32, 16))
    rng = random.Random(99)
    disagreements = 0
    for _case in range(200):
        rows = [[rng.uniform(-3, 3) for _ in range(dim)] for _ in range(rng.randint(2, 8))]
        t = _torch_scores(module, rows, mean, std)
        p = export_mod.score_candidates(document, rows)
        t_top = max(range(len(rows)), key=lambda i: t[i])
        p_top = max(range(len(rows)), key=lambda i: p[i])
        if t_top != p_top:
            gap = sorted(t, reverse=True)[0] - sorted(t, reverse=True)[1]
            if gap > 1e-4:  # a real disagreement, not a float32/float64 near-tie
                disagreements += 1
    assert disagreements == 0


def test_parity_on_boundary_inputs():
    module, document, mean, std, dim = _random_export(21, (32,))
    for row in ([0.0] * dim, [1e6] * dim, [-1e6] * dim, [1e-30] * dim, [5.0] + [0.0] * (dim - 1)):
        p = export_mod.score_candidate(document, list(row))
        t = _torch_scores(module, [list(row)], mean, std)[0]
        # float32 saturates earlier than float64; only require agreement when
        # torch itself stays finite.
        if math.isfinite(t):
            assert abs(p - t) <= max(1e-4, abs(t) * 1e-3)


def test_json_round_trip_preserves_scores_exactly(tmp_path):
    _module, document, _mean, _std, dim = _random_export(31, (16,))
    path = tmp_path / "export.json"
    export_mod.write_export(document, path)
    reloaded = export_mod.load_export(path)
    rng = random.Random(0)
    for _case in range(50):
        row = [rng.uniform(-2, 2) for _ in range(dim)]
        assert export_mod.score_candidate(document, row) == export_mod.score_candidate(reloaded, row)


def test_shuffle_invariance_of_pure_python_scorer():
    _module, document, _mean, _std, dim = _random_export(41, (16,))
    rng = random.Random(7)
    rows = [[rng.uniform(-2, 2) for _ in range(dim)] for _ in range(6)]
    base = export_mod.score_candidates(document, rows)
    perm = [5, 2, 0, 4, 1, 3]
    shuffled = export_mod.score_candidates(document, [rows[p] for p in perm])
    for new_index, p in enumerate(perm):
        assert shuffled[new_index] == base[p]


def test_empty_candidates_and_dimension_mismatch():
    _module, document, _mean, _std, dim = _random_export(51, (16,))
    assert export_mod.score_candidates(document, []) == []
    with pytest.raises(export_mod.ExportError):
        export_mod.score_candidate(document, [0.0] * (dim - 1))
    with pytest.raises(export_mod.ExportError):
        export_mod.score_candidate(document, [0.0] * (dim + 1))


def test_malformed_exports_rejected():
    _module, document, _mean, _std, dim = _random_export(61, (16,))
    # Non-finite weight: raises during canonical-JSON hashing (allow_nan=False)
    # as a plain ValueError before the explicit finiteness check runs (REV-G1).
    # ExportError subclasses ValueError, so every catch site still handles it,
    # but the error type is inconsistent with the module's own contract.
    bad = json.loads(json.dumps(document))
    bad["layers"][0]["weight"][0][0] = 1e400  # float('inf')
    with pytest.raises(ValueError):
        export_mod.validate_export(bad)
    # ragged weight row
    bad2 = json.loads(json.dumps(document))
    bad2["layers"][0]["weight"][0] = bad2["layers"][0]["weight"][0][:-1]
    with pytest.raises(export_mod.ExportError):
        export_mod.validate_export(bad2)
    # hash tamper
    bad3 = json.loads(json.dumps(document))
    bad3["teacher_id"] = "someone-else"
    with pytest.raises(export_mod.ExportError):
        export_mod.validate_export(bad3)
    # wrong schema version
    bad4 = json.loads(json.dumps(document))
    bad4["schema_version"] = "unknown-v9"
    with pytest.raises(export_mod.ExportError):
        export_mod.validate_export(bad4)


def test_multi_output_head_rejected_at_scoring():
    """A layer stack whose head emits >1 value must be refused, not truncated."""
    _module, document, _mean, _std, dim = _random_export(71, (16,))
    doc = json.loads(json.dumps(document))
    head = doc["layers"][-1]
    head["weight"] = [head["weight"][0], head["weight"][0]]
    head["bias"] = [head["bias"][0], head["bias"][0]]
    doc["model_hash"] = export_mod._digest({k: v for k, v in doc.items() if k != "model_hash"})
    export_mod.validate_export(doc)  # structurally valid...
    with pytest.raises(export_mod.ExportError):
        export_mod.score_candidate(doc, [0.0] * dim)  # ...but not a scorer


# --------------------------------------------------------------------------- #
# Runtime failure matrix -> must return None (Rule v0 fallback), never raise
# --------------------------------------------------------------------------- #


def _observation(options, *, min_count=1, max_count=1, select_type=0, context=0):
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
              "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": 1}], "handCount": 1,
              "paralyzed": False, "poisoned": False, "prize": [None] * 6}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, dict(player)],
                        "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
                        "supporterPlayed": False, "turn": 1, "turnActionCount": 0, "yourIndex": 0},
            "select": {"type": select_type, "context": context, "option": options,
                       "minCount": min_count, "maxCount": max_count},
            "step": 1}


def _fresh_policy():
    _module, document, _mean, _std, _dim = _random_export(81, (16,))
    return NeuralRuntimePolicy(document), document


def test_runtime_happy_path_is_legal_and_deterministic():
    policy, _document = _fresh_policy()
    obs = _observation([{"type": 14}, {"type": 7, "index": 0}, {"type": 13, "attackId": 1}])
    first = policy.choose(obs)
    assert first is not None and len(first) == 1 and 0 <= first[0] < 3
    for _repeat in range(5):
        assert policy.choose(obs) == first


def test_runtime_failure_matrix_always_falls_back():
    policy, document = _fresh_policy()
    good = _observation([{"type": 14}, {"type": 7, "index": 0}])

    cases: list[tuple[str, object, NeuralRuntimePolicy]] = []
    # observation anomalies
    cases.append(("non-mapping observation", [1, 2, 3], policy))
    cases.append(("missing select", {"current": {}}, policy))
    cases.append(("empty candidates", _observation([]), policy))
    cases.append(("non-integer bounds", _observation([{"type": 14}], min_count="1", max_count="1"), policy))
    cases.append(("min>options", _observation([{"type": 14}], min_count=5, max_count=5), policy))
    # model anomalies
    wrong_dim = {**document, "normalization": {"mean": [0.0], "std": [1.0]}}
    cases.append(("feature dim mismatch", good, NeuralRuntimePolicy(wrong_dim)))
    inf_doc = json.loads(json.dumps(document))
    inf_doc["layers"][-1]["weight"] = [[1e308] * len(inf_doc["layers"][-1]["weight"][0])]
    cases.append(("overflowing weights", good, NeuralRuntimePolicy(inf_doc)))
    broken_layers = {**document, "layers": []}
    cases.append(("no layers", good, NeuralRuntimePolicy(broken_layers)))

    for name, obs, active_policy in cases:
        result = active_policy.choose(obs)
        assert result is None, f"case {name!r} must fall back to Rule v0, got {result!r}"
        trace = active_policy.last_decision_trace
        assert trace is not None and trace.get("status") == "fallback", name
        # the trace must never carry observation contents, only a reason label
        assert set(trace) <= {"status", "reason"}, f"trace leaks fields: {trace}"


def test_runtime_load_rejects_hash_and_schema_mismatch(tmp_path):
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimeError

    _module, document, _mean, _std, _dim = _random_export(91, (16,))
    path = tmp_path / "m.json"
    export_mod.write_export(document, path)
    with pytest.raises(NeuralRuntimeError):
        NeuralRuntimePolicy.load(path, expected_model_hash="0" * 64)
    with pytest.raises(NeuralRuntimeError):
        NeuralRuntimePolicy.load(path, expected_feature_hash="0" * 64)
    with pytest.raises(NeuralRuntimeError):
        NeuralRuntimePolicy.load(None)
    corrupt = tmp_path / "c.json"
    corrupt.write_text("{not json")
    with pytest.raises(NeuralRuntimeError):
        NeuralRuntimePolicy.load(corrupt)


def test_runtime_optional_auxiliary_and_zero_max():
    policy, _document = _fresh_policy()
    aux = _observation([{"type": 3, "area": 2, "index": 0, "playerIndex": 0}],
                       min_count=0, max_count=1, select_type=1, context=7)
    assert policy.choose(aux) == []  # optional auxiliary prompt: decline
    zero = _observation([{"type": 14}], min_count=0, max_count=0)
    assert policy.choose(zero) == []


def test_runtime_multi_select_returns_min_count_distinct_legal():
    policy, _document = _fresh_policy()
    obs = _observation([{"type": 3, "area": 2, "index": index, "playerIndex": 0}
                        for index in range(4)],
                       min_count=2, max_count=3, select_type=1, context=8)
    choice = policy.choose(obs)
    assert choice is not None and len(choice) == 2
    assert len(set(choice)) == 2 and all(0 <= i < 4 for i in choice)
