from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.bc_trainer_v3 import (
    BCExampleV3,
    build_split_manifest_v3,
    load_bc_examples_from_teacher_records_v3,
    make_recurrent_batch_v3,
    materialize_recurrent_gate_sequences_v3,
    split_episode_groups_v3,
    train_bc_v3,
)
from mage_ptcg.meta_specialist import representation_benchmark_v3 as benchmark
from mage_ptcg.meta_specialist.neural_model_v3 import SpecialistModelV3
from mage_ptcg.meta_specialist.representation_v3 import ActionCandidateV3, EntityTokenV3, RelationalStateV3


def _example(index: int, group: str) -> BCExampleV3:
    state = RelationalStateV3(
        (0.0,) * 41,
        (EntityTokenV3(1, 1, 1, 1, 10 + index, None, (0.0,), (1,), ()),),
        (ActionCandidateV3(f"a-{index}", 0, 1, None, (), (), 0), ActionCandidateV3(f"b-{index}", 1, 1, None, (), (), 0)),
    )
    return BCExampleV3(state=state, target_index=index % 2, episode_group=group, quality_weight=1.0)


def test_split_never_crosses_episode_groups() -> None:
    train, valid = split_episode_groups_v3(tuple(_example(i, f"g{i // 2}") for i in range(8)), validation_fraction=0.25)
    assert {item.episode_group for item in train}.isdisjoint({item.episode_group for item in valid})
    assert train and valid


def test_bc_training_reports_best_validation_checkpoint() -> None:
    examples = tuple(_example(i, f"g{i}") for i in range(10))
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=2)
    result = train_bc_v3(model, examples[:8], examples[8:], epochs=2, learning_rate=1e-3)
    assert result.best_epoch in (0, 1)
    assert result.best_validation_nll >= 0
    assert result.checkpoint_state


def test_split_manifest_groups_transitive_near_duplicates_and_excludes_ubiquitous_keys() -> None:
    # Without transitive union, A would be separated from B despite A--X--Y and B--Y.
    records = (
        {"record_id": "a", "episode_id_hash": "episode-a", "near_duplicate_id": "x"},
        {"record_id": "a2", "episode_id_hash": "episode-a", "near_duplicate_id": "y"},
        {"record_id": "b", "episode_id_hash": "episode-b", "near_duplicate_id": "y"},
        {"record_id": "c", "episode_id_hash": "episode-c", "near_duplicate_id": "constant"},
        {"record_id": "d", "episode_id_hash": "episode-d", "near_duplicate_id": "constant"},
    )
    manifest = build_split_manifest_v3(records, validation_fraction=0.4, ubiquitous_threshold=2, ubiquitous_keys=("constant",))
    assignment = {row["record_id"]: row for row in manifest.assignments}
    assert assignment["a"]["component_id"] == assignment["a2"]["component_id"] == assignment["b"]["component_id"]
    assert assignment["c"]["component_id"] != assignment["d"]["component_id"]
    assert manifest.ubiquitous_keys == ("constant",)
    assert manifest.overlap_counters == {"episode_overlap": 0, "near_duplicate_overlap": 0}
    assert len(manifest.manifest_sha256) == 64


def test_split_manifest_reader_rejects_a_rehashed_content_tamper(tmp_path) -> None:
    records = (
        {"record_id": "a", "episode_id_hash": "episode-a", "near_duplicate_id": "near-a", "target": {"mass": 1}},
        {"record_id": "b", "episode_id_hash": "episode-b", "near_duplicate_id": "near-b", "target": {"mass": 1}},
    )
    manifest = build_split_manifest_v3(records, validation_fraction=0.5, ubiquitous_threshold=2, ubiquitous_keys=())
    path = tmp_path / "split.json"
    manifest.write_json(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_dataset_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    from mage_ptcg.meta_specialist.bc_trainer_v3 import SplitManifestV3
    with pytest.raises(ValueError, match="self hash"):
        SplitManifestV3.read_json(path)


def test_legacy_root_global_bc_loader_rejects_unsealed_records(tmp_path) -> None:
    """A root scan must not silently accept a teacher record without Gate authority."""
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
    from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
    from mage_ptcg.meta_specialist.local_dataset_v2 import build_local_record_v2, derive_complete_action_id_v1
    from tests.meta_specialist.test_local_dataset_v2 import _audit_source, _observation

    observation = _observation()
    observation["select"]["minCount"] = 2  # type: ignore[index]
    observation["select"]["maxCount"] = 2  # type: ignore[index]
    state = build_actor_visible_decision_state_v2(observation)
    vocabulary = make_test_card_vocabulary_v1(range(1, 4096))
    selected = tuple(action.local_action_id for action in state.legal_actions)
    bootstrap = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "bootstrap"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    record = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={
            "status": "available", "teacher_id": "search", "teacher_revision": "r1",
            "input_id": bootstrap["model_input_id"], "target_kind": "hard_selection", "quality_weight": 1.0,
            "value_target": None, "mass_rows": [{
                "complete_action_id": derive_complete_action_id_v1(
                    decision_id=bootstrap["decision_id"], selection_type=state.information_view.selection_type,
                    selection_context=state.information_view.selection_context, selection=selected,
                ), "selection": list(selected), "weight": 1,
            }],
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=_audit_source(), provenance={"source_record_ordinal": 0},
    )
    (tmp_path / "dataset-fixture.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="sealed Gate-1 input manifest"):
        load_bc_examples_from_teacher_records_v3(tmp_path)


def _sealed_gate_input(lane: str = "archaludon") -> Path:
    path = Path(__file__).resolve().parents[2] / "runs" / "meta-specialist-two-lane-readiness" / "gate1" / f"gate1-input-{lane}.json"
    if not path.is_file():
        pytest.skip("sealed Gate 1 input is unavailable in this checkout")
    return path


def _input_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_materialize_recurrent_sequences_carries_only_inside_episode() -> None:
    """A materializer that crosses a record/episode boundary must fail this."""
    sealed_input = _sealed_gate_input()
    sequences = materialize_recurrent_gate_sequences_v3(
        sealed_input, burn_in=1, expected_input_file_sha256=_input_file_sha256(sealed_input),
    )

    sequence = next(item for item in sequences if len(item.steps) >= 2)
    assert sequence.steps[0].episode_start is True
    assert all(not step.episode_start for step in sequence.steps[1:])
    assert {step.component_id for step in sequence.steps} == {sequence.component_id}
    assert all(step.partition == sequence.partition for step in sequence.steps)
    assert all(sum(step.target_masses) == pytest.approx(1.0) for step in sequence.steps)


def test_materializer_rejects_rehashed_selected_line_or_partition_tamper(tmp_path) -> None:
    """A self-hashed manifest cannot authorize a changed split assignment."""
    tampered = tmp_path / "rehashed-input.json"
    sealed_input = _sealed_gate_input()
    payload = json.loads(sealed_input.read_text(encoding="utf-8"))
    split = payload["split"]
    assert isinstance(split, dict)
    assignments = split["assignments"]
    assert isinstance(assignments, list) and assignments
    assignments[0]["partition"] = "validation" if assignments[0]["partition"] == "train" else "train"
    split_core = {key: value for key, value in split.items() if key != "manifest_sha256"}
    split["manifest_sha256"] = benchmark._hash(split_core)
    payload["manifest_sha256"] = benchmark._hash(benchmark._gate_input_core(payload))
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="input file SHA-256|split|raw line|coverage"):
        materialize_recurrent_gate_sequences_v3(
            tampered, burn_in=0, expected_input_file_sha256=_input_file_sha256(sealed_input),
        )


def test_materializer_rejects_rehashed_selected_line_permutation_without_external_anchor_match(tmp_path) -> None:
    """A self-rehashed selection order cannot replace the independently pinned bytes."""
    sealed_input = _sealed_gate_input()
    tampered = tmp_path / "reordered-input.json"
    payload = json.loads(sealed_input.read_text(encoding="utf-8"))
    selection = payload["selection"]
    assert isinstance(selection, list) and len(selection) >= 2
    selection[0], selection[1] = selection[1], selection[0]
    payload["manifest_sha256"] = benchmark._hash(benchmark._gate_input_core(payload))
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="input file SHA-256"):
        materialize_recurrent_gate_sequences_v3(
            tampered, burn_in=0, expected_input_file_sha256=_input_file_sha256(sealed_input),
        )


def test_padding_and_burn_in_neither_add_loss_nor_mark_padding_valid() -> None:
    """A padded row or burn-in row must never enter the recurrent loss mask."""
    sealed_input = _sealed_gate_input()
    sequences = materialize_recurrent_gate_sequences_v3(
        sealed_input, burn_in=1, expected_input_file_sha256=_input_file_sha256(sealed_input),
    )
    sequence_a = next(item for item in sequences if len(item.steps) >= 2)
    sequence_b = next(item for item in sequences if len(item.steps) == 1)

    batch = make_recurrent_batch_v3((sequence_a, sequence_b), burn_in=1)
    assert batch.loss_mask[:, 0].sum().item() == 0
    assert batch.padding_mask[1, -1].item() is False
