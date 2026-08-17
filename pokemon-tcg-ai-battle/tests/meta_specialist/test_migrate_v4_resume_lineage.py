"""Tests for the explicit legacy resume-lineage migration."""

from __future__ import annotations

import json
from pathlib import Path

import torch

from scripts.migrate_v4_resume_lineage import migrate_legacy_resume_lineage_v4


def test_migration_requires_both_seeds_and_rebinds_only_the_known_ordering_sha(tmp_path: Path) -> None:
    selected = "a" * 64
    legacy = "b" * 64
    legacy_trainer = "c" * 64
    report = {
        "schema": "meta-specialist-recurrent-bc-v4-research-report",
        "selected_sequence_sha256": selected,
        "trainer_implementation_sha256": legacy_trainer,
        "training_config": {"epochs": 1},
        "coverage_target": {"episodes_per_partition": 1},
        "external_run_config_sha256": "d" * 64,
        "selection_manifest_file_sha256": "e" * 64,
        "seed_results": {},
    }
    for seed in (0, 1):
        checkpoint = tmp_path / f"last-{seed}.pt"
        torch.save({
            "schema": "meta-specialist-recurrent-bc-v4-epoch-resume-v1",
            "run_config": {
                "selected_objective_sha256": legacy,
                "trainer_implementation_sha256": legacy_trainer,
                "user": {
                    "selected_sequence_sha256": selected,
                    "trainer_implementation_sha256": legacy_trainer,
                },
            },
        }, checkpoint)
        report["seed_results"][str(seed)] = {"last_checkpoint_path": str(checkpoint)}
    report_path = tmp_path / "training.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = migrate_legacy_resume_lineage_v4(tmp_path, report_path=report_path)

    assert result["old_selected_objective_sha256"] == legacy
    assert result["new_selected_objective_sha256"] == selected
    assert result["old_trainer_implementation_sha256"] == legacy_trainer
    for seed in (0, 1):
        payload = torch.load(tmp_path / f"last-{seed}.pt", map_location="cpu", weights_only=False)
        assert payload["run_config"]["selected_objective_sha256"] == selected
        assert payload["run_config"]["trainer_implementation_sha256"] == result["new_trainer_implementation_sha256"]
        assert payload["run_config"]["user"]["trainer_implementation_sha256"] == result["new_trainer_implementation_sha256"]
    assert (tmp_path / "resume-lineage-migration-v1.json").is_file()
