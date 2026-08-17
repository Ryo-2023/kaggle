"""Shared fixtures for the independent Offline Training v1 review tests.

These fixtures rebuild the fixture-collection -> dataset -> trained-checkpoint
chain independently from tests/test_offline_training_v1.py so review tests do
not depend on the production test module's internals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK_PATH = REPO_ROOT / "deck.csv"

torch = pytest.importorskip("torch")


@pytest.fixture(scope="session")
def review_collected(tmp_path_factory) -> Path:
    from mage_ptcg.offline_training.collection import collection_dataset_path, run_collection

    root = tmp_path_factory.mktemp("review-collection")
    run_collection(
        source="fixture", run_id="cabt", games=6, base_seed=4100, output_root=root,
        canonical_base_sha="b" * 40, deck_path=DECK_PATH, repository_root=REPO_ROOT,
        validation_percent=20, split_seed=3, fixture_decisions_per_seat=4, fixture_option_count=3,
    )
    return collection_dataset_path(root, "cabt")


@pytest.fixture(scope="session")
def review_dataset_dir(tmp_path_factory, review_collected) -> Path:
    from mage_ptcg.offline_training.dataset import build_dataset

    out = tmp_path_factory.mktemp("review-dataset") / "canonical"
    build_dataset(
        source_jsonl=review_collected, output_dir=out, shard_size=8, split_seed=99,
        train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25,
        teacher_id="rule-agent-v0", trainer_id="offline-training-v1", source_collection_hash="NONE",
    )
    return out


@pytest.fixture(scope="session")
def review_trained(tmp_path_factory, review_dataset_dir):
    from mage_ptcg.offline_training import neural

    ckdir = tmp_path_factory.mktemp("review-checkpoints")
    result = neural.train(
        dataset_dir=review_dataset_dir, checkpoint_dir=ckdir, hidden_dims=[32], epochs=2,
        learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=5, seed=11,
        max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu",
    )
    return ckdir, result


@pytest.fixture(scope="session")
def review_export_document(review_dataset_dir, review_trained):
    from mage_ptcg.offline_training import export as export_mod
    from mage_ptcg.offline_training import neural
    from mage_ptcg.student.artifact import feature_schema

    ckdir, _ = review_trained
    module, meta, spec = neural.load_module_from_checkpoint(ckdir / "best", device="cpu")
    return export_mod.build_export(
        module=module, model_spec_dict=spec.to_dict(), normalization=meta["normalization"],
        feature_schema=feature_schema(), dataset_hash=meta["dataset_hash"], config_hash="review-cfg",
        teacher_id="rule-agent-v0", model_purpose=meta["model_purpose"],
    )
