"""Full CLI end-to-end test: drives the real ``scripts/run_competition_intelligence.py``
entrypoint via subprocess through every O1-2..O1-4 command, not direct Python calls.

Mirrors the repo's existing clean-subprocess convention
(``PYTHONPATH`` popped, ``cwd=REPOSITORY_ROOT``) used by
``tests/test_public_belief_decision_loop.py`` and
``tests/test_competition_intelligence_runtime_isolation.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DECK_PATH = REPOSITORY_ROOT / "deck.csv"
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "run_competition_intelligence.py"


def _clean_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=REPOSITORY_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def offline_training_run(tmp_path_factory) -> Path:
    from mage_ptcg.offline_training.collection import run_collection

    root = tmp_path_factory.mktemp("cli-e2e-offline-run")
    run_collection(
        source="fixture", run_id="cabt", games=6, base_seed=8000, output_root=root / "collection",
        canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPOSITORY_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=3, fixture_option_count=3,
    )
    return root


@pytest.fixture()
def ci_run_dir(tmp_path: Path) -> Path:
    return tmp_path / "ci-run"


class TestCliEndToEnd:
    def test_doctor_runs_via_subprocess(self, tmp_path: Path) -> None:
        result = _run_cli("doctor", "--run-root", str(tmp_path / "doctor-root"))
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)
        assert summary["ok"] is True

    def test_full_pipeline_via_cli_subprocess(self, offline_training_run: Path, ci_run_dir: Path, tmp_path: Path) -> None:
        from mage_ptcg.competition_intelligence.offline_reader import discover_offline_training_run

        discovered = discover_offline_training_run(offline_training_run)
        assert discovered.is_usable()

        # 1. ingest-local
        ingest = _run_cli(
            "ingest-local", "--run-dir", str(ci_run_dir), "--input", str(discovered.collection_jsonl_path),
            "--source-id", "local:cli-e2e-source", "--allowed-uses", "ARCHIVE,ANALYSIS,TRAINING",
            "--acquired-at", "2026-07-18T00:00:00Z",
        )
        assert ingest.returncode == 0, ingest.stderr
        assert json.loads(ingest.stdout)["status"] == "ARCHIVED"

        # 2. normalize
        normalize = _run_cli(
            "normalize", "--run-dir", str(ci_run_dir), "--offline-training-run", str(offline_training_run),
            "--source-id", "local:cli-e2e-source",
        )
        assert normalize.returncode == 0, normalize.stderr
        normalize_summary = json.loads(normalize.stdout)
        assert normalize_summary["episode_count"] > 0

        # 3. analyze
        analyze = _run_cli("analyze", "--run-dir", str(ci_run_dir))
        assert analyze.returncode == 0, analyze.stderr
        assert json.loads(analyze.stdout)["deck_fingerprint_count"] >= 1

        # 4. archive-note (the raw source a knowledge claim must cite)
        archive_note = _run_cli(
            "archive-note", "--run-dir", str(ci_run_dir), "--text", "Deck A tends to hold back energy early.",
            "--source-id", "note:cli-e2e-1", "--origin-reference", "manual entry",
            "--acquired-at", "2026-07-18T00:00:00Z",
        )
        assert archive_note.returncode == 0, archive_note.stderr

        # 5. import-knowledge
        bundle_path = tmp_path / "claims.json"
        bundle_path.write_text(json.dumps({
            "schema_version": "claim-bundle-v1",
            "claims": [{
                "claim_id": "claim-cli-1", "claim_type": "matchup-tech", "scope": {"phase": "OPENING"},
                "recommendation": "test recommendation", "evidence_grade": "E1_ANECDOTAL",
            }],
        }), encoding="utf-8")
        import_knowledge = _run_cli(
            "import-knowledge", "--run-dir", str(ci_run_dir), "--bundle", str(bundle_path),
            "--raw-source-id", "note:cli-e2e-1", "--created-at", "2026-07-18T00:00:00Z",
        )
        assert import_knowledge.returncode == 0, import_knowledge.stderr
        assert json.loads(import_knowledge.stdout)["imported_claim_count"] == 1

        # 6. build-knowledge-snapshot
        build_knowledge_snapshot = _run_cli(
            "build-knowledge-snapshot", "--run-dir", str(ci_run_dir), "--cutoff", "2026-07-18T00:00:00Z",
            "--created-at", "2026-07-18T00:00:00Z",
        )
        assert build_knowledge_snapshot.returncode == 0, build_knowledge_snapshot.stderr
        knowledge_snapshot_summary = json.loads(build_knowledge_snapshot.stdout)
        assert knowledge_snapshot_summary["included_claim_count"] == 1

        # Diversify normalized opponents so build-snapshot's leakage-safe split
        # is achievable (the fixture collector is pure self-play with one
        # constant opponent identity -- see test_pipeline_end_to_end.py for
        # the full explanation of why this step is necessary for THIS
        # fixture, not a general CLI requirement).
        import dataclasses

        from mage_ptcg.competition_intelligence.pipeline import (
            _episode_to_payload,
            _write_jsonl,
            load_normalized_episodes,
        )
        from mage_ptcg.competition_intelligence.runstate import RunPaths

        episodes = load_normalized_episodes(ci_run_dir)
        diversified = [dataclasses.replace(e, agent_b=f"opponent_{i % 4}") for i, e in enumerate(episodes)]
        _write_jsonl(RunPaths(ci_run_dir).normalized / "episodes.jsonl", (_episode_to_payload(e) for e in diversified))

        # 7. build-snapshot
        build_snapshot = _run_cli(
            "build-snapshot", "--run-dir", str(ci_run_dir), "--cutoff", "2026-07-18T00:00:00Z",
            "--base-commit", "6782e68", "--created-at", "2026-07-18T00:00:00Z", "--seed", "1",
            "--knowledge-snapshot-hash", knowledge_snapshot_summary["snapshot_sha256"],
        )
        assert build_snapshot.returncode == 0, build_snapshot.stderr
        snapshot_summary = json.loads(build_snapshot.stdout)
        assert snapshot_summary["leakage_audit_passed"] is True

        # 8. export-offline-dataset
        export_path = tmp_path / "exported.jsonl"
        export = _run_cli(
            "export-offline-dataset", "--run-dir", str(ci_run_dir), "--snapshot-id", snapshot_summary["snapshot_id"],
            "--offline-training-run", str(offline_training_run), "--output", str(export_path), "--split", "train",
        )
        assert export.returncode == 0, export.stderr
        assert json.loads(export.stdout)["kept_rows"] > 0
        assert export_path.exists()

        # 9. materialize-dataset (deterministic, audited dataset materialization)
        materialize = _run_cli(
            "materialize-dataset", "--run-dir", str(ci_run_dir), "--offline-training-run", str(offline_training_run),
            "--sources", "replay", "--snapshot-id", snapshot_summary["snapshot_id"], "--split", "train",
            "--created-at", "2026-07-18T00:00:00Z",
        )
        assert materialize.returncode == 0, materialize.stderr
        materialize_summary = json.loads(materialize.stdout)
        assert materialize_summary["audit_report"]["determinism_verified"] is True
        assert Path(materialize_summary["output_dir"]).joinpath("replay.jsonl").exists()
        # No --training-policy given -> the CLI must default-deny (strictest
        # policy), never "export everything permitted" unconditionally.
        assert materialize_summary["audit_report"]["decision_selection"]["training_policy"] == "TRAINING_HIGH_INFORMATION_VERIFIED"

        # --training-policy must be reflected in the actual materialized output.
        materialize_analysis_policy = _run_cli(
            "materialize-dataset", "--run-dir", str(ci_run_dir), "--offline-training-run", str(offline_training_run),
            "--sources", "replay", "--snapshot-id", snapshot_summary["snapshot_id"], "--split", "train",
            "--created-at", "2026-07-18T00:00:00Z", "--training-policy", "ANALYSIS_ALL_PERMITTED",
        )
        assert materialize_analysis_policy.returncode == 0, materialize_analysis_policy.stderr
        analysis_summary = json.loads(materialize_analysis_policy.stdout)
        assert analysis_summary["audit_report"]["decision_selection"]["training_policy"] == "ANALYSIS_ALL_PERMITTED"
        assert (
            analysis_summary["audit_report"]["decision_selection"]["training_eligible_decision_count"]
            >= materialize_summary["audit_report"]["decision_selection"]["training_eligible_decision_count"]
        )

        # a second materialize-dataset call with identical inputs must produce
        # a byte-identical dataset_hash (regeneration hash match).
        materialize_again = _run_cli(
            "materialize-dataset", "--run-dir", str(ci_run_dir), "--offline-training-run", str(offline_training_run),
            "--sources", "replay", "--snapshot-id", snapshot_summary["snapshot_id"], "--split", "train",
            "--created-at", "2026-07-18T00:00:00Z",
        )
        assert materialize_again.returncode == 0, materialize_again.stderr
        assert json.loads(materialize_again.stdout)["dataset_hash"] == materialize_summary["dataset_hash"]

        # baseline mode: unfiltered pre-O1 dataset, always available.
        baseline = _run_cli(
            "materialize-dataset", "--run-dir", str(ci_run_dir), "--offline-training-run", str(offline_training_run),
            "--sources", "replay", "--baseline", "--created-at", "2026-07-18T00:00:00Z",
        )
        assert baseline.returncode == 0, baseline.stderr
        baseline_summary = json.loads(baseline.stdout)
        assert baseline_summary["audit_report"]["excluded_row_count"] == 0

        # 10. rebuild-catalog
        rebuild_catalog = _run_cli("rebuild-catalog", "--run-dir", str(ci_run_dir))
        assert rebuild_catalog.returncode == 0, rebuild_catalog.stderr
        assert json.loads(rebuild_catalog.stdout)["source_count"] >= 1

        # 11. report
        report = _run_cli("report", "--run-dir", str(ci_run_dir))
        assert report.returncode == 0, report.stderr
        report_summary = json.loads(report.stdout)
        assert report_summary["episode_count"] == normalize_summary["episode_count"]
        assert report_summary["knowledge_claim_latest_count"] == 1

    def test_cli_errors_are_clean_json_not_a_traceback(self, ci_run_dir: Path) -> None:
        result = _run_cli("analyze", "--run-dir", str(ci_run_dir))
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        error = json.loads(result.stderr)
        assert "error" in error

    def test_cli_help_works_for_every_subcommand(self) -> None:
        for command in (
            "doctor", "ingest-local", "rebuild-catalog", "normalize", "analyze", "archive-note", "import-knowledge",
            "build-knowledge-snapshot", "build-snapshot", "export-offline-dataset", "materialize-dataset", "report",
        ):
            result = _run_cli(command, "--help")
            assert result.returncode == 0, f"{command} --help failed: {result.stderr}"
            assert "usage" in result.stdout.lower()
