"""Tests for dataset_materialization.py (O1 offline dataset + audit deliverable)."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from mage_ptcg.competition_intelligence.claim_bundle import CLAIM_BUNDLE_SCHEMA_VERSION
from mage_ptcg.competition_intelligence.contracts import ClaimStatus
from mage_ptcg.competition_intelligence.dataset_materialization import DatasetMaterializationError
from mage_ptcg.competition_intelligence.knowledge_registry import transition_claim
from mage_ptcg.competition_intelligence.local_ingest import ingest_local_file
from mage_ptcg.competition_intelligence.offline_reader import discover_offline_training_run
from mage_ptcg.competition_intelligence.pipeline import (
    _episode_to_payload,
    _write_jsonl,
    load_normalized_episodes,
    run_analyze,
    run_build_knowledge_snapshot,
    run_build_snapshot,
    run_import_knowledge,
    run_materialize_dataset,
    run_normalize,
)
from mage_ptcg.competition_intelligence.raw_notes import archive_raw_note
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
    episodes = load_normalized_episodes(ci_run_root)
    diversified = [dataclasses.replace(e, agent_b=f"opponent_{i % n_opponents}") for i, e in enumerate(episodes)]
    _write_jsonl(RunPaths(ci_run_root).normalized / "episodes.jsonl", (_episode_to_payload(e) for e in diversified))


def _claim_bundle(tmp_path: Path, *, claim_id: str, evidence_grade: str = "E3_CONTROLLED_LOCAL_EVIDENCE") -> Path:
    path = tmp_path / f"{claim_id}.yaml"
    path.write_text(
        yaml.safe_dump({
            "schema_version": CLAIM_BUNDLE_SCHEMA_VERSION,
            "claims": [{
                "claim_id": claim_id, "claim_type": "matchup-tech", "scope": {"phase": "OPENING"},
                "recommendation": "prioritize board development", "evidence_grade": evidence_grade,
            }],
        }),
        encoding="utf-8",
    )
    return path


def _build_replay_snapshot(tmp_path: Path, *, seed: int) -> tuple[Path, str]:
    offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=seed)
    ci_run_root = tmp_path / "ci-run"
    discovered = discover_offline_training_run(offline_run)
    ingest_local_file(
        ci_run_root, discovered.collection_jsonl_path, source_id="local:dm-source",
        allowed_uses=["ARCHIVE", "ANALYSIS", "TRAINING"], acquired_at="2026-07-18T00:00:00Z",
        origin_reference="local:dm-source-path",
    )
    run_normalize(ci_run_root, offline_training_run=offline_run, source_id="local:dm-source")
    run_analyze(ci_run_root)
    _diversify_normalized_opponents(ci_run_root)
    snapshot_result = run_build_snapshot(
        ci_run_root, cutoff_time="2026-07-18T00:00:00Z", created_at="2026-07-18T00:00:00Z",
        base_commit="6782e68", seed=1, require_cutoff=False,
    )
    assert snapshot_result["leakage_audit_passed"] is True
    return ci_run_root, snapshot_result["snapshot_id"]


class TestBaselineMode:
    def test_baseline_reproduces_every_well_formed_row_unfiltered(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=9000)
        ci_run_root = tmp_path / "ci-run"
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", baseline=True,
        )
        assert result["audit_report"]["adopted_row_count"] > 0
        assert result["audit_report"]["adopted_row_count"] == result["audit_report"]["total_source_row_count"]
        assert result["audit_report"]["excluded_row_count"] == 0

    def test_baseline_rejects_non_replay_sources(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=9001)
        with pytest.raises(DatasetMaterializationError):
            run_materialize_dataset(
                tmp_path / "ci-run", offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
                sources="both", baseline=True,
            )

    def test_baseline_is_deterministic_across_two_materializations(self, tmp_path: Path) -> None:
        offline_run = _collect_fixture_run(tmp_path / "offline-run", seed=9002)
        ci_run_root = tmp_path / "ci-run"
        first = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", baseline=True,
        )
        second = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", baseline=True,
        )
        assert first["dataset_hash"] == second["dataset_hash"]
        assert first["dataset_id"] == second["dataset_id"]
        shard_a = (Path(first["output_dir"]) / "replay.jsonl").read_bytes()
        shard_b = (Path(second["output_dir"]) / "replay.jsonl").read_bytes()
        assert shard_a == shard_b


class TestReplaySnapshotMode:
    def test_materialize_from_snapshot_produces_all_artifacts(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9100)
        offline_run = tmp_path / "offline-run"
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train",
        )
        output_dir = Path(result["output_dir"])
        assert (output_dir / "replay.jsonl").exists()
        assert (output_dir / "manifest.json").exists()
        assert (output_dir / "audit_report.json").exists()
        assert (output_dir / "statistics_report.json").exists()
        assert not (output_dir / "knowledge_claims.jsonl").exists()

        audit = result["audit_report"]
        assert audit["permission_check"]["replay_passed"] is True
        assert audit["leakage_check"] is not None
        assert audit["leakage_check"]["passed"] is True
        assert audit["determinism_verified"] is True

        stats = result["statistics_report"]
        assert stats["retained_decision_count"] > 0
        assert sum(stats["by_seat"].values()) == stats["retained_decision_count"]
        assert sum(stats["by_split"].values()) == stats["retained_decision_count"]
        assert stats["observed_count"] == stats["retained_decision_count"]
        assert stats["inferred_count"] == 0

    def test_missing_snapshot_id_raises(self, tmp_path: Path) -> None:
        ci_run_root, _ = _build_replay_snapshot(tmp_path, seed=9101)
        with pytest.raises(Exception):
            run_materialize_dataset(
                ci_run_root, offline_training_run=tmp_path / "offline-run", created_at="2026-07-18T00:00:00Z",
                sources="replay", snapshot_id=None,
            )

    def test_replay_snapshot_dataset_hash_deterministic(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9102)
        offline_run = tmp_path / "offline-run"
        first = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train",
        )
        second = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-19T00:00:00Z",  # different created_at
            sources="replay", snapshot_id=snapshot_id, split="train",
        )
        # dataset_hash is a hash of the *data*, not the manifest -- must be
        # stable across calls even when an unrelated timestamp differs.
        assert first["dataset_hash"] == second["dataset_hash"]


class TestTrainingPolicySelection:
    def test_default_policy_is_strictest_and_reflected_in_manifest(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9110)
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=tmp_path / "offline-run", created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train",
        )
        manifest = json.loads((Path(result["output_dir"]) / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["training_policy"] == "TRAINING_HIGH_INFORMATION_VERIFIED"

    def test_analysis_all_permitted_yields_at_least_as_many_rows_as_the_strict_default(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9111)
        offline_run = tmp_path / "offline-run"
        strict = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train", training_policy="TRAINING_HIGH_INFORMATION_VERIFIED",
        )
        permissive = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train", training_policy="ANALYSIS_ALL_PERMITTED",
        )
        # ANALYSIS_ALL_PERMITTED can never be stricter than the training
        # policies (it is a superset: every training-eligible decision is
        # also analysis-permitted), though for a small enough fixture the two
        # sets may coincide -- the unit-level distinction is exercised
        # exhaustively in test_decision_eligibility.py.
        assert permissive["audit_report"]["adopted_row_count"] >= strict["audit_report"]["adopted_row_count"]
        assert permissive["audit_report"]["decision_selection"]["training_policy"] == "ANALYSIS_ALL_PERMITTED"
        assert strict["audit_report"]["decision_selection"]["training_policy"] == "TRAINING_HIGH_INFORMATION_VERIFIED"

    def test_eligibility_manifest_persists_reasons_for_every_decision(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9112)
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=tmp_path / "offline-run", created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train", training_policy="TRAINING_VERIFIED",
        )
        eligibility_path = Path(result["output_dir"]) / "eligibility_manifest.json"
        assert eligibility_path.exists()
        eligibility = json.loads(eligibility_path.read_text(encoding="utf-8"))
        assert eligibility["selection_policy"] == "TRAINING_VERIFIED"
        assert len(eligibility["decisions"]) > 0
        assert all(len(d["training_eligibility_reasons"]) > 0 for d in eligibility["decisions"])
        assert all("episode_id" in d and "decision_index" in d for d in eligibility["decisions"])

    def test_policy_counts_in_audit_report_match_eligibility_manifest(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9113)
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=tmp_path / "offline-run", created_at="2026-07-18T00:00:00Z",
            sources="replay", snapshot_id=snapshot_id, split="train", training_policy="TRAINING_HIGH_INFORMATION",
        )
        eligibility = json.loads((Path(result["output_dir"]) / "eligibility_manifest.json").read_text(encoding="utf-8"))
        decision_selection = result["audit_report"]["decision_selection"]
        actual_eligible_count = sum(1 for d in eligibility["decisions"] if d["training_eligible"])
        assert decision_selection["training_eligible_decision_count"] == actual_eligible_count
        assert decision_selection["analysis_decision_count"] == len(eligibility["decisions"])
        assert decision_selection["training_policy"] == "TRAINING_HIGH_INFORMATION"

    def test_invalid_training_policy_rejected(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9114)
        with pytest.raises(Exception):
            run_materialize_dataset(
                ci_run_root, offline_training_run=tmp_path / "offline-run", created_at="2026-07-18T00:00:00Z",
                sources="replay", snapshot_id=snapshot_id, split="train", training_policy="NOT_A_REAL_POLICY",
            )


class TestKnowledgeMode:
    def _setup_claims(self, tmp_path: Path) -> tuple[Path, str]:
        ci_run_root = tmp_path / "ci-run"
        archive_raw_note(
            ci_run_root, "note text", source_id="note:dm-eligible", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE", "ANALYSIS", "TRAINING"),
        )
        archive_raw_note(
            ci_run_root, "note text 2", source_id="note:dm-ineligible", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE", "ANALYSIS"),
        )
        run_import_knowledge(
            ci_run_root, bundle_path=_claim_bundle(tmp_path, claim_id="claim-eligible"),
            raw_source_id="note:dm-eligible", created_at="2026-07-18T00:00:00Z",
        )
        run_import_knowledge(
            ci_run_root, bundle_path=_claim_bundle(tmp_path, claim_id="claim-ineligible-status"),
            raw_source_id="note:dm-eligible", created_at="2026-07-18T00:00:00Z",
        )
        run_import_knowledge(
            ci_run_root, bundle_path=_claim_bundle(tmp_path, claim_id="claim-ineligible-permission"),
            raw_source_id="note:dm-ineligible", created_at="2026-07-18T00:00:00Z",
        )
        # Only claim-eligible is advanced to SUPPORTED with training_eligible=True.
        transition_claim(ci_run_root, "claim-eligible", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        transition_claim(ci_run_root, "claim-eligible", ClaimStatus.HYPOTHESIS, updated_at="2026-07-19T00:00:01Z")
        transition_claim(
            ci_run_root, "claim-eligible", ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:02Z",
            training_eligible=True,
        )
        knowledge_snapshot_result = run_build_knowledge_snapshot(
            ci_run_root, cutoff_time="2026-07-20T00:00:00Z", created_at="2026-07-20T00:00:00Z"
        )
        assert knowledge_snapshot_result["included_claim_count"] == 3
        return ci_run_root, knowledge_snapshot_result["snapshot_id"]

    def test_only_training_eligible_claim_is_materialized(self, tmp_path: Path) -> None:
        ci_run_root, knowledge_snapshot_id = self._setup_claims(tmp_path)
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=tmp_path / "unused", created_at="2026-07-20T00:00:00Z",
            sources="knowledge", knowledge_snapshot_id=knowledge_snapshot_id,
        )
        output_dir = Path(result["output_dir"])
        claim_lines = (output_dir / "knowledge_claims.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(claim_lines) == 1
        assert json.loads(claim_lines[0])["claim_id"] == "claim-eligible"

        audit = result["audit_report"]
        assert audit["excluded_reason_counts"].get("not_training_eligible") == 2
        assert audit["adopted_row_count"] == 1

    def test_missing_knowledge_snapshot_id_raises(self, tmp_path: Path) -> None:
        ci_run_root, _ = self._setup_claims(tmp_path)
        with pytest.raises(Exception):
            run_materialize_dataset(
                ci_run_root, offline_training_run=tmp_path / "unused", created_at="2026-07-20T00:00:00Z",
                sources="knowledge", knowledge_snapshot_id=None,
            )

    def test_knowledge_dataset_hash_deterministic(self, tmp_path: Path) -> None:
        ci_run_root, knowledge_snapshot_id = self._setup_claims(tmp_path)
        first = run_materialize_dataset(
            ci_run_root, offline_training_run=tmp_path / "unused", created_at="2026-07-20T00:00:00Z",
            sources="knowledge", knowledge_snapshot_id=knowledge_snapshot_id,
        )
        second = run_materialize_dataset(
            ci_run_root, offline_training_run=tmp_path / "unused", created_at="2026-07-20T00:00:00Z",
            sources="knowledge", knowledge_snapshot_id=knowledge_snapshot_id,
        )
        assert first["dataset_hash"] == second["dataset_hash"]


class TestBothMode:
    def test_both_mode_writes_both_shards(self, tmp_path: Path) -> None:
        ci_run_root, snapshot_id = _build_replay_snapshot(tmp_path, seed=9200)
        offline_run = tmp_path / "offline-run"
        archive_raw_note(
            ci_run_root, "note text", source_id="note:both-eligible", acquired_at="2026-07-18T00:00:00Z",
            origin_reference="manual entry", allowed_uses=("ARCHIVE", "ANALYSIS", "TRAINING"),
        )
        run_import_knowledge(
            ci_run_root, bundle_path=_claim_bundle(tmp_path, claim_id="claim-both"),
            raw_source_id="note:both-eligible", created_at="2026-07-18T00:00:00Z",
        )
        transition_claim(ci_run_root, "claim-both", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        transition_claim(ci_run_root, "claim-both", ClaimStatus.HYPOTHESIS, updated_at="2026-07-19T00:00:01Z")
        transition_claim(
            ci_run_root, "claim-both", ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:02Z", training_eligible=True,
        )
        knowledge_snapshot_result = run_build_knowledge_snapshot(
            ci_run_root, cutoff_time="2026-07-20T00:00:00Z", created_at="2026-07-20T00:00:00Z"
        )
        result = run_materialize_dataset(
            ci_run_root, offline_training_run=offline_run, created_at="2026-07-20T00:00:00Z",
            sources="both", snapshot_id=snapshot_id, knowledge_snapshot_id=knowledge_snapshot_result["snapshot_id"],
        )
        output_dir = Path(result["output_dir"])
        assert (output_dir / "replay.jsonl").exists()
        assert (output_dir / "knowledge_claims.jsonl").exists()
        # The default training policy (TRAINING_HIGH_INFORMATION_VERIFIED) may
        # legitimately gate out all replay decisions for a small fixture, so
        # this asserts on what "both mode" structurally guarantees: the
        # knowledge claim is adopted, and replay decisions were genuinely
        # considered (not silently skipped) by the eligibility gate.
        assert result["audit_report"]["adopted_row_count"] >= 1  # at least the 1 knowledge claim
        assert result["audit_report"]["decision_selection"]["analysis_decision_count"] > 0
