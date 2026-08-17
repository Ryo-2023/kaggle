"""Exported policy bytes must deploy, or the export must fail.

``neural_export_v1`` is the write half of a format whose read half
(``neural_policy_v1.load_specialist_neural_policy_v1``) runs on a submission
machine, where a bad artifact cannot be fixed.  These tests pin the properties
that make that safe: the bytes round-trip under ``weights_only=True``, they
carry exactly the live model's weights, the identity is the hash of the exact
bytes, and every rejection path refuses rather than exporting something
degraded.
"""

from __future__ import annotations

import hashlib
import io

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_export_v1 import (  # noqa: E402
    EXPORTED_POLICY_SCHEMA_V1,
    NeuralExportV1Error,
    export_specialist_neural_policy_v1,
    exported_policy_identity_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)


LINEAGE = "a1" * 32  # 64 lowercase hex characters


def _model(seed: int = 3):
    # A small topology keeps these tests fast; the format does not depend on size.
    config = SpecialistModelConfigV1(
        card_vocabulary_size=64, hidden_dim=16, card_dim=8, symbol_dim=4
    )
    return build_specialist_policy_model_v1(config, seed=seed)


def test_exported_bytes_reload_to_the_exact_trained_weights() -> None:
    model = _model()
    body = export_specialist_neural_policy_v1(model, LINEAGE)

    payload = torch.load(io.BytesIO(body), weights_only=True)
    assert payload["schema_version"] == EXPORTED_POLICY_SCHEMA_V1
    assert payload["lineage_id"] == LINEAGE
    assert payload["topology_config"] == model.config.to_dict()

    live = model.state_dict()
    assert set(payload["state_dict"]) == set(live)
    for name, tensor in live.items():
        assert torch.equal(payload["state_dict"][name], tensor), name


def test_export_is_deterministic_for_the_same_model_and_lineage() -> None:
    model = _model()
    assert export_specialist_neural_policy_v1(model, LINEAGE) == (
        export_specialist_neural_policy_v1(model, LINEAGE)
    )


def test_a_weight_change_changes_the_exported_bytes() -> None:
    """Otherwise two different policies could share one identity."""
    model = _model()
    before = export_specialist_neural_policy_v1(model, LINEAGE)
    with torch.no_grad():
        next(iter(model.parameters())).add_(1.0)
    after = export_specialist_neural_policy_v1(model, LINEAGE)
    assert before != after
    assert exported_policy_identity_v1(before) != exported_policy_identity_v1(after)


def test_identity_is_the_hash_of_the_exact_bytes() -> None:
    body = export_specialist_neural_policy_v1(_model(), LINEAGE)
    assert exported_policy_identity_v1(body) == hashlib.sha256(body).hexdigest()


def test_the_exported_policy_loads_through_the_real_runtime_loader() -> None:
    """The read half is the actual consumer; exercise it, not a stand-in."""
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
        load_production_card_vocabulary_v1,
    )
    from mage_ptcg.meta_specialist.neural_policy_v1 import (
        load_specialist_neural_policy_v1,
    )

    vocabulary = load_production_card_vocabulary_v1()
    config = SpecialistModelConfigV1(card_vocabulary_size=max(vocabulary.recognized_card_ids))
    model = build_specialist_policy_model_v1(config, seed=5)
    body = export_specialist_neural_policy_v1(model, LINEAGE)

    policy = load_specialist_neural_policy_v1(body, LINEAGE, vocabulary)

    # The public contract the runtime binds against.
    telemetry = policy.policy_telemetry()
    assert telemetry.policy_identity == exported_policy_identity_v1(body)
    assert telemetry.checkpoint_lineage_id == LINEAGE
    assert telemetry.candidate_class == "checkpointed_specialist"
    assert telemetry.model_loaded is True


def test_a_lineage_mismatch_is_refused_by_the_loader() -> None:
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
        load_production_card_vocabulary_v1,
    )
    from mage_ptcg.meta_specialist.neural_policy_v1 import (
        NeuralPolicyV1Error,
        load_specialist_neural_policy_v1,
    )

    vocabulary = load_production_card_vocabulary_v1()
    config = SpecialistModelConfigV1(card_vocabulary_size=max(vocabulary.recognized_card_ids))
    body = export_specialist_neural_policy_v1(build_specialist_policy_model_v1(config, seed=5), LINEAGE)

    with pytest.raises(NeuralPolicyV1Error, match="lineage"):
        load_specialist_neural_policy_v1(body, "b2" * 32, vocabulary)


@pytest.mark.parametrize(
    "bad_lineage",
    [
        "A1" * 32,        # uppercase hex is not the canonical form
        "a1" * 31,        # too short
        "a1" * 33,        # too long
        "g" + "a" * 63,   # right length, non-hex character
        "",
    ],
)
def test_a_malformed_lineage_id_is_refused(bad_lineage: str) -> None:
    with pytest.raises(NeuralExportV1Error, match="lineage_id"):
        export_specialist_neural_policy_v1(_model(), bad_lineage)


def test_a_non_string_lineage_id_is_refused() -> None:
    with pytest.raises(NeuralExportV1Error, match="lineage_id"):
        export_specialist_neural_policy_v1(_model(), None)  # type: ignore[arg-type]


def test_a_non_finite_weight_is_refused_rather_than_exported() -> None:
    """A NaN weight makes every logit NaN, so the policy has no legal argmax."""
    model = _model()
    with torch.no_grad():
        next(iter(model.parameters()))[0] = float("nan")
    with pytest.raises(NeuralExportV1Error, match="non-finite"):
        export_specialist_neural_policy_v1(model, LINEAGE)


def test_an_infinite_weight_is_refused_rather_than_exported() -> None:
    model = _model()
    with torch.no_grad():
        next(iter(model.parameters()))[0] = float("inf")
    with pytest.raises(NeuralExportV1Error, match="non-finite"):
        export_specialist_neural_policy_v1(model, LINEAGE)


def test_a_foreign_object_is_not_exportable_as_a_policy() -> None:
    with pytest.raises(NeuralExportV1Error, match="SpecialistPolicyModelV1"):
        export_specialist_neural_policy_v1(torch.nn.Linear(2, 2), LINEAGE)  # type: ignore[arg-type]


def test_identity_refuses_empty_or_non_bytes_input() -> None:
    for bad in (b"", "not bytes", None, bytearray(b"abc")):
        with pytest.raises(NeuralExportV1Error, match="exported_bytes"):
            exported_policy_identity_v1(bad)  # type: ignore[arg-type]
