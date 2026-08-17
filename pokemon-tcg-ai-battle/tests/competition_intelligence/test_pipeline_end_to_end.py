"""End-to-end pipeline test: ingest -> normalize -> analyze -> import-knowledge ->
build-knowledge-snapshot -> build-snapshot -> export-offline-dataset -> report.

Runs the full pipeline twice from clean fixture roots and proves the
canonical artifact hashes match across runs (determinism).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mage_ptcg.competition_intelligence.claim_bundle import CLAIM_BUNDLE_SCHEMA_VERSION
from mage_ptcg.competition_intelligence.contracts import ClaimStatus
from mage_ptcg.competition_intelligence.local_ingest import ingest_local_file
from mage_ptcg.competition_intelligence.offline_reader import discover_offline_training_run
from mage_ptcg.competition_intelligence.raw_notes import archive_raw_note
from mage_ptcg.competition_intelligence.pipeline import (
    PipelineError,
    load_normalized_episodes,
    run_analyze,
    run_build_knowledge_snapshot,
    run_build_snapshot,
    run_export_offline_dataset,
    run_import_knowledge,
    run_normalize,
    run_report,
)
from mage_ptcg.competition_intelligence.pipeline import _episode_to_payload, _write_jsonl
from mage_ptcg.competition_intelligence.runstate import RunPaths

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK_PATH = REPO_ROOT / "deck.csv"


def _collect_fixture_run(root: Path, *, seed: int) -> Path:
    from mage_ptcg.offline_training.collection import run_collection

    run_collection(
        source="fixture", run_id="cabt", games=8, base_seed=seed, output_root=root / "collection",
        canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPO_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=4, fixture_option_count=3,
    )
    return root


def _diversify_normalized_opponents(ci_run_root: Path, *, n_opponents: int = 4) -> None:
    """Rewrite normalized/episodes.jsonl with synthetic opponent diversity.

    See the call site in ``_run_full_pipeline`` for why: the real fixture
    collector is pure self-play, so a genuinely leakage-free split needs
    real opponent diversity, which this simulates while preserving every
    other real field (episode_id, decision linkage, hashes recomputed
    correctly for the new agent_b).
    """
    import dataclasses

    episodes = load_normalized_episodes(ci_run_root)
    diversified = [dataclasses.replace(episode, agent_b=f"opponent_{index % n_opponents}") for index, episode in enumerate(episodes)]
    paths = RunPaths(ci_run_root)
    _write_jsonl(paths.normalized / "episodes.jsonl", (_episode_to_payload(e) for e in diversified))


def _claim_bundle_path(tmp_path: Path, *, evidence_grade: str = "E1_ANECDOTAL") -> Path:
    path = tmp_path / "claims.yaml"
    path.write_text(
        yaml.safe_dump({
            "schema_version": CLAIM_BUNDLE_SCHEMA_VERSION,
            "claims": [
                {
                    "claim_id": "claim-opening-tempo",
                    "claim_type": "matchup-tech",
                    "scope": {"phase": "OPENING"},
                    "preconditions": ["faces_unknown_opponent"],
                    "recommendation": "prioritize board development in the opening",
                    "evidence_grade": evidence_grade,
                    "validity": 0.5,
                    "support": 0.1,
                    "freshness": 1.0,
                },
            ],
        }),
        encoding="utf-8",
    )
    return path


def _run_full_pipeline(work_root: Path, *, offline_run: Path, seed: int) -> dict[str, object]:
    ci_run_root = work_root / "ci-run"
    discovered = discover_offline_training_run(offline_run)
    assert discovered.is_usable()

    ingest_result = ingest_local_file(
        ci_run_root, discovered.collection_jsonl_path, source_id="local:e2e-source",
        allowed_uses=["ARCHIVE", "ANALYSIS", "TRAINING"],
        acquired_at="2026-07-18T00:00:00Z",
        origin_reference="local:e2e-source-path",
    )
    assert ingest_result["status"] == "ARCHIVED"

    normalize_result = run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:e2e-source")
    assert normalize_result["episode_count"] > 0

    analyze_result = run_analyze(ci_run_root)
    assert analyze_result["deck_fingerprint_count"] >= 1

    archive_raw_note(
        ci_run_root, "Deck A tends to hold back energy early.", source_id="note:e2e-1",
        acquired_at="2026-07-18T00:00:00Z", origin_reference="manual entry",
    )
    bundle_path = _claim_bundle_path(work_root)
    knowledge_result = run_import_knowledge(
        ci_run_root, bundle_path=bundle_path, raw_source_id="note:e2e-1", created_at="2026-07-18T00:00:00Z"
    )
    assert knowledge_result["imported_claim_count"] == 1

    knowledge_snapshot_result = run_build_knowledge_snapshot(
        ci_run_root, cutoff_time="2026-07-18T00:00:00Z", created_at="2026-07-18T00:00:00Z"
    )
    assert knowledge_snapshot_result["included_claim_count"] == 1

    # The real offline_training fixture collector is pure self-play (one
    # constant opponent identity across every episode), for which a
    # leakage-free split is mathematically impossible on opponent identity
    # alone (see test_snapshot_and_adapter.py's dedicated proof of this).
    # To exercise a genuinely clean, leakage-free build_snapshot/export in
    # this end-to-end proof, normalized/episodes.jsonl is rewritten with
    # synthetic opponent diversity (same real EpisodeRecord ids/hashes/
    # decision-linkage, only agent_b changed) -- production data with real
    # distinct opponents would not need this step.
    _diversify_normalized_opponents(ci_run_root)

    intelligence_snapshot_result = run_build_snapshot(
        ci_run_root, cutoff_time="2026-07-18T00:00:00Z", created_at="2026-07-18T00:00:00Z",
        base_commit="6782e68", seed=seed, require_cutoff=False,
        knowledge_snapshot_hash=knowledge_snapshot_result["snapshot_sha256"],
    )

    export_path = work_root / "exported.jsonl"
    export_result = run_export_offline_dataset(
        ci_run_root, snapshot_id=intelligence_snapshot_result["snapshot_id"], offline_training_run=offline_run,
        output_path=export_path, split="train",
    )

    report = run_report(ci_run_root)

    return {
        "normalize": normalize_result, "analyze": analyze_result, "knowledge": knowledge_result,
        "knowledge_snapshot": knowledge_snapshot_result, "intelligence_snapshot": intelligence_snapshot_result,
        "export": export_result, "report": report,
    }


class TestEndToEndPipeline:
    def test_full_pipeline_runs_without_error(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=5000)
        result = _run_full_pipeline(tmp_path / "work-1", offline_run=offline_run, seed=99)
        assert result["report"]["episode_count"] == result["normalize"]["episode_count"]
        assert result["export"]["kept_rows"] > 0

    def test_two_runs_from_clean_fixture_roots_produce_identical_canonical_hashes(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=5000)

        result_a = _run_full_pipeline(tmp_path / "work-a", offline_run=offline_run, seed=99)
        result_b = _run_full_pipeline(tmp_path / "work-b", offline_run=offline_run, seed=99)

        assert result_a["knowledge_snapshot"]["snapshot_sha256"] == result_b["knowledge_snapshot"]["snapshot_sha256"]
        assert result_a["intelligence_snapshot"]["snapshot_sha256"] == result_b["intelligence_snapshot"]["snapshot_sha256"]
        assert result_a["intelligence_snapshot"]["snapshot_id"] == result_b["intelligence_snapshot"]["snapshot_id"]

        # normalized episode/decision files must be byte-identical across runs
        episodes_a = (tmp_path / "work-a" / "ci-run" / "normalized" / "episodes.jsonl").read_bytes()
        episodes_b = (tmp_path / "work-b" / "ci-run" / "normalized" / "episodes.jsonl").read_bytes()
        assert episodes_a == episodes_b

        decisions_a = (tmp_path / "work-a" / "ci-run" / "normalized" / "decisions.jsonl").read_bytes()
        decisions_b = (tmp_path / "work-b" / "ci-run" / "normalized" / "decisions.jsonl").read_bytes()
        assert decisions_a == decisions_b

        # exported offline-training-compatible dataset files must also match
        export_a = (tmp_path / "work-a" / "exported.jsonl").read_bytes()
        export_b = (tmp_path / "work-b" / "exported.jsonl").read_bytes()
        assert export_a == export_b

    def test_normalize_without_ingest_still_works_standalone(self, tmp_path: Path) -> None:
        # normalize() itself does not require a prior ingest -- it only needs a
        # discoverable offline_training run; the SourceEnvelope requirement is
        # enforced later, at build_snapshot() time.
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=6000)
        ci_run_root = tmp_path / "ci-run"
        result = run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:no-ingest")
        assert result["episode_count"] > 0

    def test_build_snapshot_fails_clearly_without_matching_source_envelope(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=6000)
        ci_run_root = tmp_path / "ci-run"
        run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:missing-envelope")
        with pytest.raises(PipelineError):
            run_build_snapshot(
                ci_run_root, cutoff_time="2026-07-18T00:00:00Z", created_at="2026-07-18T00:00:00Z",
                base_commit="6782e68", require_cutoff=False,
            )

    def test_analyze_without_normalize_fails_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(PipelineError):
            run_analyze(tmp_path / "empty-run")

    def test_normalize_is_idempotent(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=8000)
        ci_run_root = tmp_path / "ci-run"
        first = run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:idempotent")
        second = run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:idempotent")
        assert first == second
        episodes_path = ci_run_root / "normalized" / "episodes.jsonl"
        content_after_first = episodes_path.read_bytes()
        run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:idempotent")
        assert episodes_path.read_bytes() == content_after_first  # re-run overwrites cleanly, no duplication

    def test_analyze_is_idempotent(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=8100)
        ci_run_root = tmp_path / "ci-run"
        run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:idempotent")
        first = run_analyze(ci_run_root)
        second = run_analyze(ci_run_root)
        assert first == second

    def test_build_snapshot_same_params_is_idempotent(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=8200)
        ci_run_root = tmp_path / "ci-run"
        discovered = discover_offline_training_run(offline_run)
        ingest_local_file(
            ci_run_root, discovered.collection_jsonl_path, source_id="local:idempotent",
            acquired_at="2026-07-18T00:00:00Z",
        )
        run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:idempotent")
        _diversify_normalized_opponents(ci_run_root)
        kwargs = dict(
            cutoff_time="2026-07-18T00:00:00Z", created_at="2026-07-18T00:00:00Z",
            base_commit="6782e68", seed=5, require_cutoff=False,
        )
        first = run_build_snapshot(ci_run_root, **kwargs)
        second = run_build_snapshot(ci_run_root, **kwargs)
        assert first == second

    def test_import_knowledge_rejects_duplicate_claim_id_rather_than_silently_reimporting(self, tmp_path: Path) -> None:
        # A deliberate fail-closed choice, not an idempotence gap: re-importing
        # the same claim_id is ambiguous (same claim reaffirmed, or a *new*
        # claim that coincidentally reuses an id?) so it is rejected loudly
        # rather than silently accepted a second time.
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=8300)
        ci_run_root = tmp_path / "ci-run"
        run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:idempotent")
        archive_raw_note(
            ci_run_root, "note text", source_id="note:1", acquired_at="2026-07-18T00:00:00Z", origin_reference="manual entry",
        )
        bundle_path = _claim_bundle_path(tmp_path)
        run_import_knowledge(ci_run_root, bundle_path=bundle_path, raw_source_id="note:1", created_at="2026-07-18T00:00:00Z")
        with pytest.raises(Exception):
            run_import_knowledge(ci_run_root, bundle_path=bundle_path, raw_source_id="note:1", created_at="2026-07-18T00:00:01Z")

    def test_import_knowledge_rejects_unarchived_raw_source_id(self, tmp_path: Path) -> None:
        bundle_path = _claim_bundle_path(tmp_path)
        with pytest.raises(PipelineError):
            run_import_knowledge(
                tmp_path / "ci-run", bundle_path=bundle_path, raw_source_id="note:never-archived",
                created_at="2026-07-18T00:00:00Z",
            )

    def test_import_knowledge_rejects_source_without_analysis_permission(self, tmp_path: Path) -> None:
        ci_run_root = tmp_path / "ci-run"
        archive_raw_note(
            ci_run_root, "note text", source_id="note:no-analysis", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE",),
        )
        bundle_path = _claim_bundle_path(tmp_path)
        with pytest.raises(PipelineError):
            run_import_knowledge(
                ci_run_root, bundle_path=bundle_path, raw_source_id="note:no-analysis", created_at="2026-07-18T00:00:00Z"
            )

    def test_build_knowledge_snapshot_permission_summary_reflects_real_source_permissions(self, tmp_path: Path) -> None:
        ci_run_root = tmp_path / "ci-run"
        archive_raw_note(
            ci_run_root, "note text", source_id="note:perm-1", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE", "ANALYSIS"),
        )
        bundle_path = _claim_bundle_path(tmp_path)
        run_import_knowledge(ci_run_root, bundle_path=bundle_path, raw_source_id="note:perm-1", created_at="2026-07-18T00:00:00Z")
        result = run_build_knowledge_snapshot(ci_run_root, cutoff_time="2026-07-18T00:00:00Z", created_at="2026-07-18T00:00:00Z")
        manifest = json.loads((RunPaths(ci_run_root).snapshots / result["snapshot_id"] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["permissions_summary"] == {"ARCHIVE": 1, "ANALYSIS": 1}

    def test_transition_claim_to_training_eligible_requires_training_permission(self, tmp_path: Path) -> None:
        from mage_ptcg.competition_intelligence.knowledge_registry import KnowledgeRegistryError, transition_claim

        ci_run_root = tmp_path / "ci-run"
        archive_raw_note(
            ci_run_root, "note text", source_id="note:no-training", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE", "ANALYSIS"),
        )
        bundle_path = _claim_bundle_path(tmp_path, evidence_grade="E3_CONTROLLED_LOCAL_EVIDENCE")
        run_import_knowledge(ci_run_root, bundle_path=bundle_path, raw_source_id="note:no-training", created_at="2026-07-18T00:00:00Z")
        transition_claim(ci_run_root, "claim-opening-tempo", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        transition_claim(ci_run_root, "claim-opening-tempo", ClaimStatus.HYPOTHESIS, updated_at="2026-07-19T00:00:01Z")
        with pytest.raises(KnowledgeRegistryError):
            transition_claim(
                ci_run_root, "claim-opening-tempo", ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:02Z",
                training_eligible=True,
            )

    def test_transition_claim_to_training_eligible_succeeds_when_source_grants_training(self, tmp_path: Path) -> None:
        from mage_ptcg.competition_intelligence.knowledge_registry import transition_claim

        ci_run_root = tmp_path / "ci-run"
        archive_raw_note(
            ci_run_root, "note text", source_id="note:with-training", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE", "ANALYSIS", "TRAINING"),
        )
        bundle_path = _claim_bundle_path(tmp_path, evidence_grade="E3_CONTROLLED_LOCAL_EVIDENCE")
        run_import_knowledge(ci_run_root, bundle_path=bundle_path, raw_source_id="note:with-training", created_at="2026-07-18T00:00:00Z")
        transition_claim(ci_run_root, "claim-opening-tempo", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        transition_claim(ci_run_root, "claim-opening-tempo", ClaimStatus.HYPOTHESIS, updated_at="2026-07-19T00:00:01Z")
        moved = transition_claim(
            ci_run_root, "claim-opening-tempo", ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:02Z",
            training_eligible=True,
        )
        assert moved.training_eligible is True

    def test_report_reflects_actual_state_not_hardcoded(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=7000)
        result = _run_full_pipeline(tmp_path / "work-1", offline_run=offline_run, seed=1)
        report = result["report"]
        assert report["episode_count"] == result["normalize"]["episode_count"]
        assert report["knowledge_claim_latest_count"] == 1
        assert "RAW" in report["knowledge_claim_status_summary"]
