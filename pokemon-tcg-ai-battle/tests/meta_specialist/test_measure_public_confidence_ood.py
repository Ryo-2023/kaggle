"""Contracts for the public confidence/OOD replay runner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4
from tests.meta_specialist.test_trajectory_v1 import _two_choice_forced_stop_transition


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_public_confidence_ood.py"
    spec = importlib.util.spec_from_file_location("measure_public_confidence_ood", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_source(path: Path) -> None:
    transition, _ = _two_choice_forced_stop_transition()
    row = {
        "component_id": "a" * 64,
        "game_id": "b" * 64,
        "opponent_id": "must-not-escape",
        "seat": 1,
        "partition": "train",
        "schema": "meta-specialist-v4-dagger-transition-v1",
        "transition_index": 0,
        "transition": transition.to_dict(),
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_runner_replays_public_rows_and_emits_no_opponent_metadata(tmp_path: Path) -> None:
    runner = _load_runner()
    source = tmp_path / "transitions.jsonl"
    _write_source(source)
    reference_path = tmp_path / "reference.json"
    from scripts.build_public_confidence_reference import build_public_bucket_reference

    reference_path.write_text(
        json.dumps(build_public_bucket_reference(source), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reference_source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    checkpoint = tmp_path / "model.pt"
    descriptor = save_specialist_checkpoint_v4(
        checkpoint,
        SpecialistModelV4(card_vocabulary_size=512, hidden_dim=16, embedding_dim=12, seed=17).eval(),
    )
    output = tmp_path / "report.json"
    assert runner.main([
        "--transitions", str(source),
        "--partition", "train",
        "--checkpoint", str(checkpoint),
        "--checkpoint-file-sha256", hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "--checkpoint-tensor-state-sha256", descriptor["tensor_state_sha256"],
        "--reference", str(reference_path),
        "--reference-source-sha256", reference_source_sha256,
        "--card-vocabulary-size", "512",
        "--hidden-dim", "16",
        "--embedding-dim", "12",
        "--device", "cpu",
        "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "meta-specialist-public-confidence-ood-replay-v1"
    assert report["transition_count"] == 1
    assert report["prefix_count"] == 3
    assert report["eligible_transition_count"] == 1
    assert report["target_missing_count"] == 0
    assert report["checkpoint"]["tensor_state_sha256"] == descriptor["tensor_state_sha256"]
    serialized = json.dumps(report, sort_keys=True)
    assert "must-not-escape" not in serialized
    assert "opponent_id" not in report["privacy"]


def test_runner_requires_reference_and_output(tmp_path: Path) -> None:
    runner = _load_runner()
    with pytest.raises(SystemExit):
        runner.main(["--transitions", str(tmp_path / "missing.jsonl")])
