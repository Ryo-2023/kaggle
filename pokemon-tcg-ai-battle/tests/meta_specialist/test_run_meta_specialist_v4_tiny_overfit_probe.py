"""Contracts for the diagnostic-only V4 tiny-overfit probe."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib
import importlib.util
import json
import sys

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.recurrent_bc_v4 import RESEARCH_ONLY_UNIFORM_WEIGHT, ResearchSubsetV4
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4
from mage_ptcg.meta_specialist.representation_v4 import (
    ActionCandidateV4,
    EntityTokenV4,
    PublicEntityClassRefV4,
    RelationalStateV4,
)


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_meta_specialist_v4_tiny_overfit_probe.py"
    spec = importlib.util.spec_from_file_location("run_meta_specialist_v4_tiny_overfit_probe", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sequence(*, partition: str, index: int) -> RecurrentBCSequenceV4:
    ref = PublicEntityClassRefV4.actor_visible(1, "hand", 9 + index)
    entity = EntityTokenV4(index + 1, 6, 1, 9, 9 + index, None, (), (), (), ref)
    candidates = tuple(
        ActionCandidateV4(
            f"semantic-{partition}-{index}-{candidate}", 7 + candidate, ref, None, None,
            (candidate,), (), 1, (), False, 0, ref,
        )
        for candidate in range(2)
    )
    state = RelationalStateV4((float(index),), (entity,), candidates)
    step = RecurrentBCStepV4(
        state=state, target_index=0, episode_group=f"{partition}-episode-{index}",
        quality_weight=1.0, model_input=object(), step_input=SimpleNamespace(stop_available=False),
        target_masses=(0.9, 0.1), reach_mass=1.0, episode_start=True,
        component_id=f"{partition}-component-{index}", partition=partition,
        record_id=f"{index + (0 if partition == 'train' else 16):064x}",
        content_hash=f"{index + 64:064x}", research_only=True,
    )
    stop = RecurrentBCStepV4(
        state=state, target_index=len(candidates), episode_group=f"{partition}-episode-{index}",
        quality_weight=1.0, model_input=object(), step_input=SimpleNamespace(stop_available=True),
        target_masses=(0.1, 0.1, 0.8), reach_mass=1.0, episode_start=False,
        component_id=f"{partition}-component-{index}", partition=partition,
        record_id=step.record_id, content_hash=step.content_hash, research_only=True,
    )
    return RecurrentBCSequenceV4(
        "lane", f"{partition}-episode-{index}", f"{partition}-component-{index}", partition,
        (step, stop), burn_in=0, research_only=True,
    )


def _subset(tmp_path: Path) -> ResearchSubsetV4:
    selection = tmp_path / "selection.json"
    selection.write_text("{}", encoding="utf-8")
    sequences = tuple(_sequence(partition=partition, index=index) for partition in ("train", "validation") for index in range(4))
    return ResearchSubsetV4(
        lane="lane", selection_manifest_path=selection,
        selection_manifest_file_sha256=hashlib.sha256(selection.read_bytes()).hexdigest(),
        sequences=sequences, records_by_partition={"train": 4, "validation": 4},
        target_records_by_partition={"train": 4, "validation": 4},
        card_vocabulary_size=32, card_vocabulary_card_id_count=32,
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT, require_positive_stop=True,
        episodes_per_partition=4, components_per_partition=4,
        train_episodes_per_partition=4, validation_episodes_per_partition=4,
        train_components_per_partition=4, validation_components_per_partition=4,
    )


def test_tiny_probe_persists_per_epoch_forced_excluded_metrics_and_strict_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if the diagnostic silently loses metric detail or unverified weights."""
    runner = _load_runner()
    subset = _subset(tmp_path)
    monkeypatch.setattr(runner, "materialize_fast_research_uniform_subset_v4", lambda *_args, **_kwargs: subset)
    output = tmp_path / "tiny-overfit.json"
    progress = tmp_path / "tiny-overfit.progress.json"
    config = runner.TinyOverfitProbeConfigV4(
        selection_manifest=subset.selection_manifest_path,
        selection_manifest_sha256=subset.selection_manifest_file_sha256,
        output=output, progress_path=progress, seed=3, epochs=1, hidden_dim=16, embedding_dim=12,
        device="cpu", max_records=128, episodes_per_partition=4, components_per_partition=4,
        learning_rate=1e-3, tbptt_steps=1, gradient_clip_norm=1.0, burn_in=0,
    )

    report = runner.run_tiny_overfit_probe_v4(config)

    assert report["diagnostic_only"] is True
    assert report["promotion_authority"] is False
    assert report["selected_sequence_sha256"]
    assert report["checkpoint"]["strict_reload_verified"] is True
    assert len(report["epoch_metrics"]) == 1
    for partition in ("train", "validation"):
        metrics = report["epoch_metrics"][0][partition]
        assert metrics["recurrence"] == "carry"
        assert metrics["complete_action"]["eligible_rows"] == 8
        assert metrics["complete_action"]["forced_domain_size1_rows"] == 0
        assert metrics["complete_action"]["complete_action_nll"] is not None
        assert set(metrics["action_type"]) == {"7", "STOP"}
    disk = json.loads(output.read_text(encoding="utf-8"))
    progress_disk = json.loads(progress.read_text(encoding="utf-8"))
    assert progress_disk["status"] == "done"
    assert progress_disk["completed"] == 1
    assert progress_disk["total"] == 1
    assert disk["checkpoint"]["file_sha256"] == report["checkpoint"]["file_sha256"]
    assessment = disk["overfit_assessment"]
    assert assessment["train_exact_top1_reaches_95_epoch"] in {None, 1}
    assert assessment["verdict"] in {"TINY_TRAIN_FIT_CONFIRMED", "TINY_TRAIN_FIT_NOT_REACHED"}
