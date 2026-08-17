"""θ0 を RL の初期値として読む経路の契約 (正典 §1, §9.3)。

以前の実装は学習の初期値が乱数 + rule agent コーパスの模倣であり、実測でも方策が
「rule agent の鋭くした複製」へ収束していた。この経路はそこを断つためにある。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.foundation_init_v1 import (
    DERIVATION_QUALIFIED_V1,
    FoundationInitProvenanceV1,
    INIT_KIND_BC_DISTILLED_V1,
    RULE_AGENT_V0_TEACHER_ID_V1,
    TeacherRefV1,
)
from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (
    build_checkpoint_payload_v1,
    build_training_identity_v1,
    publish_checkpoint_v1,
)
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)
from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
    TrainFromTrajectoriesV1Error,
    _load_bootstrap_weights_v1,
)


_CONFIG = SpecialistModelConfigV1(
    card_vocabulary_size=32, hidden_dim=8, card_dim=4, symbol_dim=2
)


def _teacher(teacher_id: str = "a_strong_pooled_agent") -> TeacherRefV1:
    return TeacherRefV1(
        teacher_id=teacher_id, teacher_kind="external_submission_agent",
        policy_hash="a" * 64, usage_boundary="local_eval_only",
        derivation_boundary=DERIVATION_QUALIFIED_V1, decision_ref="docs/decisions/x.md",
    )


def _publish_theta0(tmp_path: Path, *, teacher_id: str = "a_strong_pooled_agent", seed: int = 7) -> Path:
    model = build_specialist_policy_model_v1(_CONFIG, seed=seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    recipe = {"objective": "behavior_cloning"}
    payload = build_checkpoint_payload_v1(
        model=model, optimizer=optimizer, scheduler=None,
        identity=build_training_identity_v1(
            snapshot_id="s" * 64, config=_CONFIG, recipe=recipe, seed=seed
        ),
        recipe=recipe, step=10, sampler_cursor=10,
        foundation_init=FoundationInitProvenanceV1(
            init_kind=INIT_KIND_BC_DISTILLED_V1, teachers=(_teacher(teacher_id),),
            parent_checkpoint_sha256="", notes="test θ0",
        ),
    )
    return publish_checkpoint_v1(tmp_path / "checkpoints", payload)


def test_bootstrap_loads_every_theta0_weight(tmp_path: Path) -> None:
    """θ0 の全 tensor が実際に載ること.

    部分的にしか載らないまま「θ0 から始めた」と記録されるのが最も見つけにくい
    失敗なので、tensor 単位で一致を確かめる。
    """
    path = _publish_theta0(tmp_path, seed=7)
    theta0 = build_specialist_policy_model_v1(_CONFIG, seed=7).state_dict()

    fresh = build_specialist_policy_model_v1(_CONFIG, seed=999)
    before = {k: v.clone() for k, v in fresh.state_dict().items()}
    _load_bootstrap_weights_v1(fresh, path, expected_config=_CONFIG)
    after = fresh.state_dict()

    assert all(torch.equal(after[k], theta0[k]) for k in theta0), "θ0 weights were not fully loaded"
    assert any(not torch.equal(before[k], after[k]) for k in before), "bootstrap changed nothing"


def test_bootstrap_records_a_warm_start_naming_theta0_as_parent(tmp_path: Path) -> None:
    """系譜 teacher -> θ0 -> この run が checkpoint だけから読めること."""
    path = _publish_theta0(tmp_path)
    model = build_specialist_policy_model_v1(_CONFIG, seed=0)
    provenance = _load_bootstrap_weights_v1(model, path, expected_config=_CONFIG)

    assert provenance.init_kind == "warm_start"
    assert [t.teacher_id for t in provenance.teachers] == ["a_strong_pooled_agent"]
    assert provenance.parent_checkpoint_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_topology_mismatch_fails_rather_than_partially_loading(tmp_path: Path) -> None:
    """形が違う θ0 を黙って一部だけ読まないこと.

    合う層だけ読んで残りを乱数のまま残すと、「θ0 から始めた」と記録しながら実質
    ほぼ乱数初期化、という状態になる。
    """
    path = _publish_theta0(tmp_path)
    wider = SpecialistModelConfigV1(
        card_vocabulary_size=_CONFIG.card_vocabulary_size, hidden_dim=_CONFIG.hidden_dim + 8,
        card_dim=_CONFIG.card_dim, symbol_dim=_CONFIG.symbol_dim,
    )
    with pytest.raises(TrainFromTrajectoriesV1Error):
        _load_bootstrap_weights_v1(
            build_specialist_policy_model_v1(wider, seed=0), path, expected_config=wider
        )


def test_a_renamed_or_mutated_checkpoint_is_refused(tmp_path: Path) -> None:
    """content-addressed な名前と中身の不一致を拒否すること."""
    path = _publish_theta0(tmp_path)
    renamed = path.parent / ("checkpoint-" + "0" * 64 + ".pt")
    renamed.write_bytes(path.read_bytes())
    with pytest.raises(TrainFromTrajectoriesV1Error):
        _load_bootstrap_weights_v1(
            build_specialist_policy_model_v1(_CONFIG, seed=0), renamed, expected_config=_CONFIG
        )


def test_a_rule_v0_derived_theta0_is_refused(tmp_path: Path) -> None:
    """Rule Agent v0 由来の θ0 から RL を始められないこと.

    AGENTS.md は Rule v0 を比較対象・fallback に限定する。そこから始めた重みは
    測定済みの「rule agent の鋭くした複製」へ収束する失敗を引き継ぐ。
    """
    path = _publish_theta0(tmp_path, teacher_id=RULE_AGENT_V0_TEACHER_ID_V1)
    with pytest.raises(Exception):
        _load_bootstrap_weights_v1(
            build_specialist_policy_model_v1(_CONFIG, seed=0), path, expected_config=_CONFIG
        )
