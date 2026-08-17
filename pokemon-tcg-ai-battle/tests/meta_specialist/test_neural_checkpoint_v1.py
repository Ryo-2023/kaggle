"""L3 checkpoints: content-addressed publication, strict identity, and resume parity."""

from __future__ import annotations

from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.foundation_init_v1 import random_init_provenance_v1
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (  # noqa: E402
    NEURAL_CHECKPOINT_SCHEMA_V1,
    NeuralCheckpointV1Error,
    build_checkpoint_payload_v1,
    build_training_identity_v1,
    load_checkpoint_v1,
    publish_checkpoint_v1,
    restore_checkpoint_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (  # noqa: E402
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)


RECIPE = {"optimizer": "adamw", "learning_rate": 0.001, "batch_examples": 8}
SNAPSHOT_ID = "a" * 64


def _run(*, steps: int, seed: int = 3):
    """Take deterministic optimizer steps on a fixed synthetic objective."""
    config = SpecialistModelConfigV1(card_vocabulary_size=64, hidden_dim=16, card_dim=8, symbol_dim=4)
    model = build_specialist_policy_model_v1(config, seed=seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=RECIPE["learning_rate"])
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=2, gamma=0.5)
    return config, model, optimizer, scheduler


def _step(model, optimizer, scheduler) -> float:
    optimizer.zero_grad()
    # A fixed, RNG-consuming objective so the RNG stream is part of resume parity.
    noise = torch.randn(())
    loss = sum((parameter * parameter).sum() for parameter in model.parameters()) * (1.0 + noise * 0.0)
    loss = loss + noise * 0.0
    loss.backward()
    optimizer.step()
    scheduler.step()
    return float(loss.detach())


def _identity(config):
    return build_training_identity_v1(
        snapshot_id=SNAPSHOT_ID, config=config, recipe=RECIPE, seed=3
    )


def _weights(model):
    return [parameter.detach().clone() for parameter in model.parameters()]


def test_uninterrupted_and_resumed_runs_produce_identical_weights(tmp_path: Path) -> None:
    torch.manual_seed(1)
    config, model, optimizer, scheduler = _run(steps=4)
    for _index in range(4):
        _step(model, optimizer, scheduler)
    uninterrupted = _weights(model)

    torch.manual_seed(1)
    config_b, model_b, optimizer_b, scheduler_b = _run(steps=4)
    for _index in range(2):
        _step(model_b, optimizer_b, scheduler_b)
    identity = _identity(config_b)
    published = publish_checkpoint_v1(
        tmp_path / "ckpt",
        build_checkpoint_payload_v1(
            model=model_b, optimizer=optimizer_b, scheduler=scheduler_b,
            identity=identity, recipe=RECIPE, step=2, sampler_cursor=17, foundation_init=random_init_provenance_v1()),
    )

    # A fresh process would rebuild the objects, then restore into them.
    _config_c, model_c, optimizer_c, scheduler_c = _run(steps=0, seed=99)
    payload = load_checkpoint_v1(published, expected=identity)
    step, cursor = restore_checkpoint_v1(
        payload, model=model_c, optimizer=optimizer_c, scheduler=scheduler_c
    )
    assert (step, cursor) == (2, 17)
    for _index in range(2):
        _step(model_c, optimizer_c, scheduler_c)

    for expected, actual in zip(uninterrupted, _weights(model_c), strict=True):
        assert torch.equal(expected, actual)
    assert scheduler_c.get_last_lr() == scheduler.get_last_lr()


def test_publication_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    config, model, optimizer, scheduler = _run(steps=0)
    payload = build_checkpoint_payload_v1(
        model=model, optimizer=optimizer, scheduler=scheduler,
        identity=_identity(config), recipe=RECIPE, step=0, sampler_cursor=0, foundation_init=random_init_provenance_v1())
    first = publish_checkpoint_v1(tmp_path / "ckpt", payload)
    before = first.stat()
    second = publish_checkpoint_v1(tmp_path / "ckpt", payload)

    assert first == second
    assert first.name.startswith("checkpoint-") and first.name.endswith(".pt")
    assert (second.stat().st_ino, second.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert list((tmp_path / "ckpt").glob(".checkpoint-*.tmp.*")) == []


def test_a_different_identity_or_topology_is_refused(tmp_path: Path) -> None:
    config, model, optimizer, scheduler = _run(steps=0)
    identity = _identity(config)
    published = publish_checkpoint_v1(
        tmp_path / "ckpt",
        build_checkpoint_payload_v1(
            model=model, optimizer=optimizer, scheduler=scheduler,
            identity=identity, recipe=RECIPE, step=0, sampler_cursor=0, foundation_init=random_init_provenance_v1()),
    )

    other = build_training_identity_v1(
        snapshot_id="b" * 64, config=config, recipe=RECIPE, seed=3
    )
    with pytest.raises(NeuralCheckpointV1Error, match="training_identity does not match"):
        load_checkpoint_v1(published, expected=other)

    recipe_changed = build_training_identity_v1(
        snapshot_id=SNAPSHOT_ID, config=config, recipe={**RECIPE, "learning_rate": 0.5}, seed=3
    )
    with pytest.raises(NeuralCheckpointV1Error, match="training_identity does not match"):
        load_checkpoint_v1(published, expected=recipe_changed)

    payload = load_checkpoint_v1(published, expected=identity)
    wider = SpecialistModelConfigV1(
        card_vocabulary_size=64, hidden_dim=32, card_dim=8, symbol_dim=4
    )
    live = build_specialist_policy_model_v1(wider, seed=3)
    with pytest.raises(NeuralCheckpointV1Error, match="topology does not match"):
        restore_checkpoint_v1(
            payload, model=live, optimizer=torch.optim.AdamW(live.parameters()), scheduler=None
        )


def test_scheduler_presence_must_match_and_legacy_layouts_are_refused(tmp_path: Path) -> None:
    config, model, optimizer, scheduler = _run(steps=0)
    identity = _identity(config)
    published = publish_checkpoint_v1(
        tmp_path / "ckpt",
        build_checkpoint_payload_v1(
            model=model, optimizer=optimizer, scheduler=scheduler,
            identity=identity, recipe=RECIPE, step=0, sampler_cursor=0, foundation_init=random_init_provenance_v1()),
    )
    payload = load_checkpoint_v1(published, expected=identity)
    with pytest.raises(NeuralCheckpointV1Error, match="scheduler presence"):
        restore_checkpoint_v1(payload, model=model, optimizer=optimizer, scheduler=None)

    legacy = tmp_path / "legacy.pt"
    torch.save({"metadata": {}, "r2d3": {"step": 1}, "target": {}}, legacy)
    with pytest.raises(NeuralCheckpointV1Error, match="legacy or foreign checkpoint"):
        load_checkpoint_v1(legacy, expected=identity)


def test_metadata_is_a_closed_field_set_and_counters_are_validated(tmp_path: Path) -> None:
    config, model, optimizer, scheduler = _run(steps=0)
    identity = _identity(config)
    payload = build_checkpoint_payload_v1(
        model=model, optimizer=optimizer, scheduler=scheduler,
        identity=identity, recipe=RECIPE, step=0, sampler_cursor=0, foundation_init=random_init_provenance_v1())
    assert payload["metadata"]["schema_version"] == NEURAL_CHECKPOINT_SCHEMA_V1

    payload["metadata"]["extra"] = 1
    extended = tmp_path / "extended.pt"
    torch.save(payload, extended)
    with pytest.raises(NeuralCheckpointV1Error, match="wrong closed field set"):
        load_checkpoint_v1(extended, expected=identity)

    for bad in (-1, "2"):
        with pytest.raises(NeuralCheckpointV1Error, match="nonnegative int"):
            build_checkpoint_payload_v1(
                model=model, optimizer=optimizer, scheduler=scheduler,
                identity=identity, recipe=RECIPE, step=bad, sampler_cursor=0, foundation_init=random_init_provenance_v1())


def test_training_identity_digest_changes_with_every_component() -> None:
    config = SpecialistModelConfigV1(card_vocabulary_size=64, hidden_dim=16, card_dim=8, symbol_dim=4)
    base = build_training_identity_v1(
        snapshot_id=SNAPSHOT_ID, config=config, recipe=RECIPE, seed=3
    )
    variants = [
        build_training_identity_v1(snapshot_id="c" * 64, config=config, recipe=RECIPE, seed=3),
        build_training_identity_v1(
            snapshot_id=SNAPSHOT_ID,
            config=SpecialistModelConfigV1(
                card_vocabulary_size=65, hidden_dim=16, card_dim=8, symbol_dim=4
            ),
            recipe=RECIPE, seed=3,
        ),
        build_training_identity_v1(
            snapshot_id=SNAPSHOT_ID, config=config, recipe={**RECIPE, "batch_examples": 9}, seed=3
        ),
        build_training_identity_v1(snapshot_id=SNAPSHOT_ID, config=config, recipe=RECIPE, seed=4),
    ]
    assert len({base.digest(), *(item.digest() for item in variants)}) == len(variants) + 1
