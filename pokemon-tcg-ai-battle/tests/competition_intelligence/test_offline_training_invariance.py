"""Snapshot-less Offline Training must be unaffected by O1 (non-negotiable, see AGENTS.md).

Proves two things the O1-4 design promises structurally
(``dataset_materialization.py``'s and ``offline_adapter.py``'s module
docstrings) but which were previously untested directly:

1. Importing and *using* the Competition Intelligence sidecar in a process
   does not change ``mage_ptcg.offline_training.dataset.build_dataset``'s
   output on the exact same input, called directly, with no snapshot
   involved -- byte-identical manifest and shard files before/after.
2. Importing ``dataset_materialization``/``offline_adapter`` does not pull in
   ``mage_ptcg.offline_training.dataset`` or ``mage_ptcg.student`` as an
   import side effect (the adapter only ever receives a *path* to a
   collection file; it never imports the trainer/model code itself).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK_PATH = REPO_ROOT / "deck.csv"


def _collect_fixture_run(root: Path, *, seed: int) -> Path:
    from mage_ptcg.offline_training.collection import run_collection

    run_collection(
        source="fixture", run_id="cabt", games=6, base_seed=seed, output_root=root / "collection",
        canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPO_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=3, fixture_option_count=3,
    )
    return root / "collection" / "cabt" / "private_dataset" / "rule-bc-v1.jsonl"


def _read_dataset_dir(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


class TestBuildDatasetUnaffectedByO1Usage:
    def test_build_dataset_output_identical_before_and_after_using_o1(self, tmp_path: Path) -> None:
        from mage_ptcg.offline_training.dataset import build_dataset

        source_jsonl = _collect_fixture_run(tmp_path / "offline-run", seed=9500)

        before_dir = tmp_path / "dataset-before"
        result_before = build_dataset(
            source_jsonl=source_jsonl, output_dir=before_dir, shard_size=100, split_seed=0,
            train_fraction=0.7, validation_fraction=0.15, test_fraction=0.15,
            teacher_id="rule_v0", trainer_id="test-trainer", source_collection_hash="a" * 64,
        )

        # Import and actually exercise the O1 sidecar (baseline dataset
        # materialization) in this same process -- if it monkeypatched
        # anything, mutated shared module state, or otherwise reached into
        # offline_training, the second build_dataset call below would differ.
        from mage_ptcg.competition_intelligence.pipeline import run_materialize_dataset

        run_materialize_dataset(
            tmp_path / "ci-run", offline_training_run=tmp_path / "offline-run",
            created_at="2026-07-18T00:00:00Z", sources="replay", baseline=True,
        )

        after_dir = tmp_path / "dataset-after"
        result_after = build_dataset(
            source_jsonl=source_jsonl, output_dir=after_dir, shard_size=100, split_seed=0,
            train_fraction=0.7, validation_fraction=0.15, test_fraction=0.15,
            teacher_id="rule_v0", trainer_id="test-trainer", source_collection_hash="a" * 64,
        )

        assert result_before == result_after
        assert _read_dataset_dir(before_dir) == _read_dataset_dir(after_dir)


class TestAdapterDoesNotImportOfflineTrainingInternals:
    def _clean_env(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPO_ROOT / "src")
        return environment

    def _sys_modules_after(self, script: str) -> list[str]:
        wrapped = script + "\nimport json as _json, sys as _sys\nprint(_json.dumps(sorted(_sys.modules.keys())))\n"
        result = subprocess.run(
            [sys.executable, "-c", wrapped], cwd=REPO_ROOT, env=self._clean_env(),
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_importing_dataset_materialization_does_not_pull_in_offline_training_dataset_or_student(self) -> None:
        modules = self._sys_modules_after("import mage_ptcg.competition_intelligence.dataset_materialization\n")
        assert "mage_ptcg.offline_training.dataset" not in modules
        assert "mage_ptcg.student" not in modules

    def test_importing_offline_adapter_does_not_pull_in_offline_training_dataset_or_student(self) -> None:
        modules = self._sys_modules_after("import mage_ptcg.competition_intelligence.offline_adapter\n")
        assert "mage_ptcg.offline_training.dataset" not in modules
        assert "mage_ptcg.student" not in modules
