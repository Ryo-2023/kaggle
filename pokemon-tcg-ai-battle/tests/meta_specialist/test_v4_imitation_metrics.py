"""Contracts for the sealed V4 offline imitation diagnostic."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4
from mage_ptcg.meta_specialist.recurrent_bc_v4 import RESEARCH_ONLY_UNIFORM_WEIGHT, ResearchSubsetV4
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PublicEntityClassRefV4,
    RelationalStateV4,
    SemanticPrefixTokenV4,
)
from mage_ptcg.meta_specialist.v4_imitation_metrics import (
    V4_IMITATION_METRICS_SCHEMA_V1,
    evaluate_recurrent_imitation_v4,
)


def _state(action_type: int, *, candidate_count: int = 2, prefix_depth: int = 0) -> RelationalStateV4:
    ref = PublicEntityClassRefV4.actor_visible(1, "hand", 9)
    entity = EntityTokenV4(1, 6, 1, 9, 9, None, (), (), (), ref)
    candidates = tuple(
        ActionCandidateV4(
            f"semantic-{action_type}-{index}", action_type + index, ref, None, None,
            (index + 1,), (), 1, (), False, 0, ref,
        )
        for index in range(candidate_count)
    )
    prefix = tuple(SemanticPrefixTokenV4(1, (), (), ref, None, None, ref) for _ in range(prefix_depth))
    return RelationalStateV4((0.0,), (entity,), candidates, prefix)


def _step(
    *, record_id: str, episode_start: bool, action_type: int, forced: bool = False, prefix_depth: int = 0,
) -> RecurrentBCStepV4:
    state = _state(action_type, candidate_count=1 if forced else 2, prefix_depth=prefix_depth)
    return RecurrentBCStepV4(
        state=state, target_index=0, episode_group="episode", quality_weight=1.0,
        model_input=object(), step_input=SimpleNamespace(stop_available=False),
        target_masses=(1.0,) if forced else (0.75, 0.25), reach_mass=1.0,
        episode_start=episode_start, component_id="component", partition="validation",
        record_id=record_id, content_hash=f"{int(record_id, 16) + 40:064x}", research_only=True,
    )


def _validation_sequence() -> RecurrentBCSequenceV4:
    # First physical record has two teacher-prefix rows; final record is forced
    # and must not inflate imitation scores.
    return RecurrentBCSequenceV4(
        "lane", "episode", "component", "validation", (
            _step(record_id=f"{1:064x}", episode_start=True, action_type=7),
            _step(record_id=f"{1:064x}", episode_start=False, action_type=8, prefix_depth=1),
            _step(record_id=f"{2:064x}", episode_start=False, action_type=9, forced=True),
        ), burn_in=0, research_only=True,
    )


def test_offline_metrics_exclude_forced_rows_and_preserve_prefix_and_action_breakdowns() -> None:
    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=7)

    carry = evaluate_recurrent_imitation_v4(model, (_validation_sequence(),), partition="validation", recurrence="carry")
    reset = evaluate_recurrent_imitation_v4(model, (_validation_sequence(),), partition="validation", recurrence="reset")

    assert carry["schema"] == V4_IMITATION_METRICS_SCHEMA_V1
    assert carry["complete_action"]["eligible_rows"] == 2
    assert carry["complete_action"]["forced_domain_size1_rows"] == 1
    assert carry["complete_action"]["complete_action_nll"] >= 0.0
    assert 0.0 <= carry["complete_action"]["top1"] <= 1.0
    assert set(carry["action_type"]) == {"7", "8"}
    assert [row["prefix_depth"] for row in carry["teacher_prefix_survival"]] == [0, 1]
    assert carry["complete_action"]["eligible_rows"] == reset["complete_action"]["eligible_rows"]
    assert carry["complete_action"]["forced_domain_size1_rows"] == reset["complete_action"]["forced_domain_size1_rows"]


def test_cli_strictly_loads_checkpoint_and_binds_subset_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=5)
    checkpoint = tmp_path / "checkpoint.pt"
    descriptor = save_specialist_checkpoint_v4(checkpoint, model)
    file_sha256 = __import__("hashlib").sha256(checkpoint.read_bytes()).hexdigest()
    sequence = _validation_sequence()
    train_steps = tuple(replace(step, partition="train", component_id="train-component") for step in sequence.steps)
    train = RecurrentBCSequenceV4(
        "lane", "episode", "train-component", "train", train_steps, burn_in=0, research_only=True,
    )
    subset = ResearchSubsetV4(
        lane="lane", selection_manifest_path=tmp_path / "selection.json",
        selection_manifest_file_sha256="a" * 64, sequences=(train, sequence),
        records_by_partition={"train": 1, "validation": 2},
        target_records_by_partition={"train": 1, "validation": 2},
        card_vocabulary_size=32, card_vocabulary_card_id_count=32,
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )
    spec = importlib.util.spec_from_file_location("v4_metrics_cli", Path("scripts/measure_v4_imitation_metrics.py"))
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "materialize_fast_research_uniform_subset_v4", lambda *_args, **_kwargs: subset)
    output = tmp_path / "metrics.json"
    progress = tmp_path / "metrics.progress.json"

    assert runner.main([
        "--selection-manifest", str(tmp_path / "selection.json"), "--selection-manifest-sha256", "a" * 64,
        "--checkpoint", str(checkpoint), "--checkpoint-file-sha256", file_sha256,
        "--checkpoint-tensor-state-sha256", str(descriptor["tensor_state_sha256"]),
        "--card-vocabulary-size", "32", "--hidden-dim", "16", "--embedding-dim", "12",
        "--episodes-per-partition", "4", "--components-per-partition", "4",
        "--progress-path", str(progress), "--output", str(output),
    ]) == 0
    payload = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert payload["checkpoint"]["file_sha256"] == file_sha256
    assert payload["selected_sequence_sha256"]
    assert set(payload["partitions"]) == {"train", "validation"}
    assert set(payload["partitions"]["validation"]["recurrence"]) == {"carry", "reset"}
    progress_payload = __import__("json").loads(progress.read_text(encoding="utf-8"))
    assert progress_payload["status"] == "done"
    assert progress_payload["completed"] == progress_payload["total"] == 5
    assert progress_payload["fields"]["stage"] == "evaluate"


def test_batch_cli_materializes_once_and_rejects_report_subset_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The multi-seed path must reuse one sealed subset, never trust a stale report."""
    import importlib.util

    model = SpecialistModelV4(card_vocabulary_size=32, hidden_dim=16, embedding_dim=12, seed=5)
    checkpoint = tmp_path / "checkpoint.pt"
    descriptor = save_specialist_checkpoint_v4(checkpoint, model)
    file_sha256 = __import__("hashlib").sha256(checkpoint.read_bytes()).hexdigest()
    sequence = _validation_sequence()
    train = RecurrentBCSequenceV4(
        "lane", "episode", "train-component", "train",
        tuple(replace(step, partition="train", component_id="train-component") for step in sequence.steps),
        burn_in=0, research_only=True,
    )
    subset = ResearchSubsetV4(
        lane="lane", selection_manifest_path=tmp_path / "selection.json",
        selection_manifest_file_sha256="a" * 64, sequences=(train, sequence),
        records_by_partition={"train": 1, "validation": 2},
        target_records_by_partition={"train": 1, "validation": 2},
        card_vocabulary_size=32, card_vocabulary_card_id_count=32,
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )
    spec = importlib.util.spec_from_file_location("v4_metrics_batch_cli", Path("scripts/measure_v4_imitation_metrics.py"))
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls = 0

    def materialize(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return subset

    monkeypatch.setattr(runner, "materialize_fast_research_uniform_subset_v4", materialize)
    training = {
        "schema": "meta-specialist-recurrent-bc-v4-research-report", "mode": RESEARCH_ONLY_UNIFORM_WEIGHT,
        "promotion_authority": False, "lane": "lane", "selection_manifest": str(tmp_path / "selection.json"),
        "selection_manifest_file_sha256": "a" * 64,
        "selected_sequence_sha256": runner.selected_objective_sha256_v4(subset.sequences),
        "trainer_implementation_sha256": runner.trainer_implementation_sha256_v4(),
        "external_run_config_sha256": None,
        "coverage_target": {
            "episodes_per_partition": 4, "components_per_partition": 4,
            "train_episodes_per_partition": 4, "validation_episodes_per_partition": 4,
            "train_components_per_partition": 4, "validation_components_per_partition": 4,
            "require_positive_stop": False,
        },
        "training_config": {
            "max_records": 1024, "subset_fraction": 0.05, "burn_in": 0,
            "epochs": 1, "patience": 1, "learning_rate": 1e-3, "tbptt_steps": 8,
            "gradient_clip_norm": 1.0, "hidden_dim": 16, "embedding_dim": 12,
            "card_vocabulary_size": 32, "seeds": [0, 1], "device": "cpu",
        },
        "seed_results": {
            str(seed): {
                "best_checkpoint_path": str(checkpoint), "best_checkpoint_file_sha256": file_sha256,
                "best_checkpoint_tensor_state_sha256": str(descriptor["tensor_state_sha256"]),
            }
            for seed in (0, 1)
        },
    }
    training["training_config_sha256"] = runner._training_report_config_sha256(training)
    report_path = tmp_path / "training.json"
    report_path.write_text(__import__("json").dumps(training), encoding="utf-8")
    output = tmp_path / "batch-metrics.json"

    assert runner.main([
        "--selection-manifest", str(tmp_path / "selection.json"), "--selection-manifest-sha256", "a" * 64,
        "--training-report", str(report_path), "--seeds", "0,1", "--max-records", "1024", "--burn-in", "0",
        "--episodes-per-partition", "4", "--components-per-partition", "4", "--output", str(output),
    ]) == 0
    result = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert calls == 1
    assert set(result["seed_results"]) == {"0", "1"}
    assert result["selected_sequence_sha256"] == training["selected_sequence_sha256"]

    training["selected_sequence_sha256"] = "b" * 64
    training["training_config_sha256"] = runner._training_report_config_sha256(training)
    report_path.write_text(__import__("json").dumps(training), encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.main([
            "--selection-manifest", str(tmp_path / "selection.json"), "--selection-manifest-sha256", "a" * 64,
            "--training-report", str(report_path), "--seeds", "0,1", "--max-records", "1024", "--burn-in", "0",
            "--episodes-per-partition", "4", "--components-per-partition", "4", "--output", str(output),
        ])
