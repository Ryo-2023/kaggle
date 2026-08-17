"""V4 checkpoint bindings for the existing actor-pool job contract."""

from __future__ import annotations

import hashlib

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.actor_pool_v1 import ActorJobConfigV1
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4


def _v4_job(path, *, file_sha: str, tensor_sha: str) -> ActorJobConfigV1:
    return ActorJobConfigV1(
        job_id="v4-actor-job", archetype_id="alakazam", deck_csv_path="deck.csv",
        source_commit="a" * 40, env_seed=1, seat=0,
        behavior_kind="neural_specialist_v4", behavior_identity=file_sha,
        neural_checkpoint_path=str(path), neural_checkpoint_file_sha256=file_sha,
        neural_checkpoint_tensor_state_sha256=tensor_sha,
        opponent_kind="cabt_rule_agent_v0",
    )


def test_actor_pool_v4_job_binds_both_checkpoint_digests_and_a_runtime_factory(tmp_path) -> None:
    """Breaks if V4 jobs can omit a digest or bypass the V4 strict runtime loader."""
    from mage_ptcg.meta_specialist.actor_pool_v1 import _build_neural_agent_policy_factory_v4

    path = tmp_path / "v4.pt"
    descriptor = save_specialist_checkpoint_v4(
        path, SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=3).eval(),
    )
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    job = _v4_job(path, file_sha=file_sha, tensor_sha=descriptor["tensor_state_sha256"])

    factory, identity = _build_neural_agent_policy_factory_v4(job, checkpoint_lineage_id="b" * 64)

    assert identity == file_sha
    assert factory.new_policy().policy_telemetry().policy_identity == file_sha
    with pytest.raises(ValueError, match="tensor_state"):
        _v4_job(path, file_sha=file_sha, tensor_sha="").__post_init__()
