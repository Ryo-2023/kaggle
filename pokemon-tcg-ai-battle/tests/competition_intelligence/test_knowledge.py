"""Tests for claim_bundle.py, contradiction.py, knowledge_registry.py, knowledge_snapshot.py, raw_notes.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from mage_ptcg.competition_intelligence import archive
from mage_ptcg.competition_intelligence.claim_bundle import (
    CLAIM_BUNDLE_SCHEMA_VERSION,
    ClaimBundleError,
    build_knowledge_claim,
    import_claim_bundle,
    parse_claim_bundle,
)
from mage_ptcg.competition_intelligence.contracts import (
    ClaimStatus,
    ContractError,
    EvidenceBasis,
    EvidenceGrade,
)
from mage_ptcg.competition_intelligence.contradiction import detect_contradictions
from mage_ptcg.competition_intelligence.knowledge_registry import (
    KnowledgeRegistryError,
    claims_log_path,
    import_claims,
    iter_claim_versions,
    latest_claims,
    transition_claim,
)
from mage_ptcg.competition_intelligence.knowledge_snapshot import build_knowledge_snapshot
from mage_ptcg.competition_intelligence.raw_notes import archive_raw_note


def _raw_claim(**overrides) -> dict:
    base = {
        "claim_id": "claim-1",
        "claim_type": "matchup-tech",
        "scope": {"phase": "OPENING"},
        "preconditions": ["faces_deck_a"],
        "recommendation": "hold card X until turn 3",
        "evidence_grade": "E1_ANECDOTAL",
        "validity": 0.5,
        "support": 0.1,
        "freshness": 1.0,
    }
    base.update(overrides)
    return base


class TestClaimBundleParsing:
    def test_parses_json_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps({"schema_version": CLAIM_BUNDLE_SCHEMA_VERSION, "claims": [_raw_claim()]}), encoding="utf-8")
        claims = parse_claim_bundle(path)
        assert len(claims) == 1
        assert claims[0]["claim_id"] == "claim-1"

    def test_parses_yaml_bundle(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.yaml"
        path.write_text(yaml.safe_dump({"schema_version": CLAIM_BUNDLE_SCHEMA_VERSION, "claims": [_raw_claim()]}), encoding="utf-8")
        claims = parse_claim_bundle(path)
        assert len(claims) == 1

    def test_rejects_wrong_schema_version(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps({"schema_version": "wrong", "claims": []}), encoding="utf-8")
        with pytest.raises(ClaimBundleError):
            parse_claim_bundle(path)

    def test_rejects_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ClaimBundleError):
            parse_claim_bundle(path)

    def test_rejects_malformed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.yaml"
        path.write_text("claims: [unterminated", encoding="utf-8")
        with pytest.raises(ClaimBundleError):
            parse_claim_bundle(path)

    def test_rejects_non_mapping_root(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ClaimBundleError):
            parse_claim_bundle(path)

    def test_rejects_missing_claims_list(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps({"schema_version": CLAIM_BUNDLE_SCHEMA_VERSION}), encoding="utf-8")
        with pytest.raises(ClaimBundleError):
            parse_claim_bundle(path)


class TestBuildKnowledgeClaim:
    def test_builds_valid_claim(self) -> None:
        claim = build_knowledge_claim(_raw_claim(), raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")
        assert claim.status == ClaimStatus.RAW
        assert claim.evidence_grade == EvidenceGrade.E1_ANECDOTAL

    def test_status_field_in_raw_bundle_is_ignored_always_starts_raw(self) -> None:
        claim = build_knowledge_claim(
            _raw_claim(status="SUPPORTED"), raw_source_id="note-1", created_at="2026-07-18T00:00:00Z"
        )
        assert claim.status == ClaimStatus.RAW  # cannot be imported pre-SUPPORTED

    def test_rejects_invalid_evidence_grade(self) -> None:
        with pytest.raises(ClaimBundleError):
            build_knowledge_claim(_raw_claim(evidence_grade="NOT_A_GRADE"), raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")

    def test_rejects_missing_evidence_grade(self) -> None:
        raw = _raw_claim()
        del raw["evidence_grade"]
        with pytest.raises(ClaimBundleError):
            build_knowledge_claim(raw, raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")

    def test_rejects_missing_claim_id(self) -> None:
        raw = _raw_claim()
        del raw["claim_id"]
        with pytest.raises(ClaimBundleError):
            build_knowledge_claim(raw, raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")

    def test_evidence_basis_defaults_to_inferred(self) -> None:
        claim = build_knowledge_claim(_raw_claim(), raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")
        assert claim.evidence_basis == EvidenceBasis.INFERRED

    def test_evidence_basis_can_be_declared_observed(self) -> None:
        claim = build_knowledge_claim(
            _raw_claim(evidence_basis="OBSERVED"), raw_source_id="note-1", created_at="2026-07-18T00:00:00Z"
        )
        assert claim.evidence_basis == EvidenceBasis.OBSERVED

    def test_rejects_invalid_evidence_basis(self) -> None:
        with pytest.raises(ClaimBundleError):
            build_knowledge_claim(
                _raw_claim(evidence_basis="MAYBE"), raw_source_id="note-1", created_at="2026-07-18T00:00:00Z"
            )

    def test_training_and_runtime_eligible_are_ignored_on_import(self) -> None:
        # Cannot be imported pre-SUPPORTED (mirrors the `status` field ignore
        # above) -- eligibility can only be granted later via with_transition.
        claim = build_knowledge_claim(
            _raw_claim(training_eligible=True, runtime_eligible=True),
            raw_source_id="note-1", created_at="2026-07-18T00:00:00Z",
        )
        assert claim.training_eligible is False
        assert claim.runtime_eligible is False

    def test_import_claim_bundle_end_to_end(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.yaml"
        path.write_text(
            yaml.safe_dump({"schema_version": CLAIM_BUNDLE_SCHEMA_VERSION, "claims": [_raw_claim(), _raw_claim(claim_id="claim-2")]}),
            encoding="utf-8",
        )
        claims = import_claim_bundle(path, raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")
        assert len(claims) == 2
        assert {c.claim_id for c in claims} == {"claim-1", "claim-2"}


class TestContradictionDetection:
    def _claim(self, **overrides):
        fields = dict(
            claim_id="claim-a", raw_source_id="note-1", claim_type="matchup-tech", scope={"phase": "OPENING", "deck": "A"},
            preconditions=(), recommendation="x", evidence_grade=EvidenceGrade.E1_ANECDOTAL, status=ClaimStatus.RAW,
        )
        fields.update(overrides)
        return build_knowledge_claim(
            {
                "claim_id": fields["claim_id"], "claim_type": fields["claim_type"], "scope": fields["scope"],
                "recommendation": fields["recommendation"], "evidence_grade": fields["evidence_grade"].value,
            },
            raw_source_id=fields["raw_source_id"], created_at="2026-07-18T00:00:00Z",
        )

    def test_detects_overlapping_same_type_claims(self) -> None:
        claim_a = self._claim(claim_id="claim-a", scope={"phase": "OPENING", "deck": "A"})
        claim_b = self._claim(claim_id="claim-b", scope={"phase": "OPENING", "deck": "B"})
        contradictions = detect_contradictions([claim_a, claim_b])
        assert len(contradictions) == 1
        assert contradictions[0].scope_overlap == {"phase": "OPENING"}

    def test_no_contradiction_for_different_claim_type(self) -> None:
        claim_a = self._claim(claim_id="claim-a", claim_type="matchup-tech", scope={"phase": "OPENING"})
        claim_b = self._claim(claim_id="claim-b", claim_type="deck-build", scope={"phase": "OPENING"})
        assert detect_contradictions([claim_a, claim_b]) == ()

    def test_no_contradiction_when_no_scope_overlap(self) -> None:
        claim_a = self._claim(claim_id="claim-a", scope={"phase": "OPENING"})
        claim_b = self._claim(claim_id="claim-b", scope={"phase": "ENDGAME"})
        assert detect_contradictions([claim_a, claim_b]) == ()

    def test_deterministic_regardless_of_input_order(self) -> None:
        claim_a = self._claim(claim_id="claim-a", scope={"phase": "OPENING"})
        claim_b = self._claim(claim_id="claim-b", scope={"phase": "OPENING"})
        forward = detect_contradictions([claim_a, claim_b])
        backward = detect_contradictions([claim_b, claim_a])
        assert forward[0].contradiction_id == backward[0].contradiction_id

    def test_both_claims_preserved_neither_deleted(self) -> None:
        claim_a = self._claim(claim_id="claim-a", scope={"phase": "OPENING"})
        claim_b = self._claim(claim_id="claim-b", scope={"phase": "OPENING"})
        contradictions = detect_contradictions([claim_a, claim_b])
        assert {contradictions[0].claim_id_a, contradictions[0].claim_id_b} == {"claim-a", "claim-b"}

    def test_a_claim_cannot_contradict_itself(self) -> None:
        from mage_ptcg.competition_intelligence.contradiction import CONTRADICTION_SCHEMA_VERSION, Contradiction

        with pytest.raises(ContractError):
            Contradiction(
                schema_version=CONTRADICTION_SCHEMA_VERSION, contradiction_id="x", claim_id_a="a", claim_id_b="a",
                overlap_reason="x", scope_overlap={}, evidence_grade_a="E0_UNVALIDATED", evidence_grade_b="E0_UNVALIDATED",
                confidence=0.5, resolved=False,
            )


class TestKnowledgeRegistry:
    def _claim(self, claim_id: str, **overrides):
        raw = _raw_claim(claim_id=claim_id, **overrides)
        return build_knowledge_claim(raw, raw_source_id="note-1", created_at="2026-07-18T00:00:00Z")

    def test_import_and_read_back(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1")
        import_claims(tmp_path, [claim])
        loaded = latest_claims(tmp_path)
        assert loaded["claim-1"].content_hash() == claim.content_hash()

    def test_reimporting_identical_existing_claim_is_idempotent_no_op(self, tmp_path: Path) -> None:
        # Independent-audit remediation: re-importing exactly what is already
        # present (same claim_id, byte-identical content) is unambiguous and
        # succeeds as a no-op, rather than being treated the same as a
        # genuinely conflicting re-import (see the next test).
        claim = self._claim("claim-1")
        import_claims(tmp_path, [claim])
        log_bytes_before = claims_log_path(tmp_path).read_bytes()
        import_claims(tmp_path, [claim])  # must not raise
        assert claims_log_path(tmp_path).read_bytes() == log_bytes_before  # nothing new appended
        assert len(list(iter_claim_versions(tmp_path))) == 1

    def test_import_rejects_duplicate_claim_id_with_conflicting_content(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1")
        import_claims(tmp_path, [claim])
        conflicting = self._claim("claim-1", recommendation="a completely different recommendation")
        with pytest.raises(KnowledgeRegistryError):
            import_claims(tmp_path, [conflicting])
        assert len(list(iter_claim_versions(tmp_path))) == 1  # nothing appended on failure

    def test_import_rejects_batch_internal_duplicate_with_conflicting_content(self, tmp_path: Path) -> None:
        claim_a = self._claim("claim-1", recommendation="recommendation A")
        claim_b = self._claim("claim-1", recommendation="recommendation B")
        with pytest.raises(KnowledgeRegistryError):
            import_claims(tmp_path, [claim_a, claim_b])
        assert not claims_log_path(tmp_path).exists()  # zero writes, not a partial append

    def test_import_dedupes_batch_internal_duplicate_with_identical_content(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1")
        identical_copy = self._claim("claim-1")  # same fields -> same content_hash
        assert claim.content_hash() == identical_copy.content_hash()
        import_claims(tmp_path, [claim, identical_copy])
        assert len(list(iter_claim_versions(tmp_path))) == 1

    def test_import_claims_reports_duplicate_skipped_ids_distinct_from_appended(self, tmp_path: Path) -> None:
        # independent-audit finding #2 visibility: a caller must be able to
        # tell "N appended" apart from "M silently-idempotent duplicates".
        existing = self._claim("claim-existing")
        import_claims(tmp_path, [existing])

        new_claim = self._claim("claim-new")
        reimport_of_existing = self._claim("claim-existing")  # identical content -> idempotent
        result = import_claims(tmp_path, [new_claim, reimport_of_existing])
        assert result.appended_claim_ids == ("claim-new",)
        assert result.duplicate_skipped_claim_ids == ("claim-existing",)
        assert set(result.claim_ids) == {"claim-new", "claim-existing"}

    def test_import_batch_is_independent_of_input_order(self, tmp_path: Path) -> None:
        claim_a = self._claim("claim-a")
        claim_b = self._claim("claim-b")
        claim_c = self._claim("claim-c")
        import_claims(tmp_path, [claim_a, claim_b, claim_c])
        forward_bytes = claims_log_path(tmp_path).read_bytes()

        other_root = tmp_path / "reordered"
        import_claims(other_root, [claim_c, claim_a, claim_b])
        backward_bytes = claims_log_path(other_root).read_bytes()
        assert forward_bytes == backward_bytes

    def test_partial_batch_failure_leaves_registry_bytes_and_hash_unchanged(self, tmp_path: Path) -> None:
        claim_a = self._claim("claim-a")
        import_claims(tmp_path, [claim_a])
        log_path = claims_log_path(tmp_path)
        bytes_before = log_path.read_bytes()

        claim_b = self._claim("claim-b")  # new, would succeed on its own
        conflicting_a = self._claim("claim-a", recommendation="conflicting content for an existing claim")
        with pytest.raises(KnowledgeRegistryError):
            import_claims(tmp_path, [claim_b, conflicting_a])
        # claim_b must NOT have been appended even though only claim_a conflicted.
        assert log_path.read_bytes() == bytes_before
        assert set(latest_claims(tmp_path)) == {"claim-a"}

    def test_transition_appends_new_version_preserves_old(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1", evidence_grade="E3_CONTROLLED_LOCAL_EVIDENCE")
        import_claims(tmp_path, [claim])
        transition_claim(tmp_path, "claim-1", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        versions = list(iter_claim_versions(tmp_path))
        assert len(versions) == 2
        assert versions[0].status == ClaimStatus.RAW
        assert versions[1].status == ClaimStatus.PARSED

    def test_illegal_transition_rejected_and_not_appended(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1")
        import_claims(tmp_path, [claim])
        with pytest.raises(KnowledgeRegistryError):
            transition_claim(tmp_path, "claim-1", ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:00Z")
        assert len(list(iter_claim_versions(tmp_path))) == 1  # illegal transition never appended

    def test_transition_of_unknown_claim_id_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(KnowledgeRegistryError):
            transition_claim(tmp_path, "does-not-exist", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")

    def test_latest_claims_reduces_to_most_recent_version(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1")
        import_claims(tmp_path, [claim])
        transition_claim(tmp_path, "claim-1", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        transition_claim(tmp_path, "claim-1", ClaimStatus.HYPOTHESIS, updated_at="2026-07-20T00:00:00Z")
        assert latest_claims(tmp_path)["claim-1"].status == ClaimStatus.HYPOTHESIS

    def test_deprecated_claim_still_readable_not_deleted(self, tmp_path: Path) -> None:
        claim = self._claim("claim-1", evidence_grade="E4_STRONG_EMPIRICAL_EVIDENCE")
        import_claims(tmp_path, [claim])
        transition_claim(tmp_path, "claim-1", ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z")
        transition_claim(tmp_path, "claim-1", ClaimStatus.HYPOTHESIS, updated_at="2026-07-19T00:00:01Z")
        transition_claim(tmp_path, "claim-1", ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:02Z")
        transition_claim(tmp_path, "claim-1", ClaimStatus.DEPRECATED, updated_at="2026-07-20T00:00:00Z")
        assert latest_claims(tmp_path)["claim-1"].status == ClaimStatus.DEPRECATED
        assert len(list(iter_claim_versions(tmp_path))) == 5


class TestKnowledgeSnapshotDeterminism:
    def _fields(self) -> dict:
        return dict(
            created_at="2026-07-18T00:00:00Z", cutoff_time="2026-07-17T00:00:00Z",
            included_claim_ids=("claim-1", "claim-2"), excluded_claims={"claim-3": "below_evidence_threshold"},
            source_hashes={"note-1": "a" * 64}, permissions_summary={"ARCHIVE": 3, "ANALYSIS": 2},
            lifecycle_summary={"RAW": 1, "HYPOTHESIS": 1}, evidence_grade_summary={"E1_ANECDOTAL": 2},
            evidence_basis_summary={"INFERRED": 2},
            contradiction_count=1, normalizer_versions={"claim_bundle": "v1"},
        )

    def test_two_builds_produce_identical_hash(self) -> None:
        a = build_knowledge_snapshot(**self._fields())
        b = build_knowledge_snapshot(**self._fields())
        assert a.snapshot_sha256 == b.snapshot_sha256
        assert a.snapshot_id == b.snapshot_id

    def test_changed_input_changes_hash(self) -> None:
        base = build_knowledge_snapshot(**self._fields())
        fields = self._fields()
        fields["contradiction_count"] = 2
        changed = build_knowledge_snapshot(**fields)
        assert base.snapshot_sha256 != changed.snapshot_sha256

    def test_rejects_claim_included_and_excluded(self) -> None:
        fields = self._fields()
        fields["excluded_claims"] = {"claim-1": "duplicate"}  # claim-1 is also in included_claim_ids
        with pytest.raises(ContractError):
            build_knowledge_snapshot(**fields)

    def test_rejects_explicit_snapshot_id(self) -> None:
        fields = self._fields()
        fields["snapshot_id"] = "hand-picked"
        with pytest.raises(ContractError):
            build_knowledge_snapshot(**fields)

    def test_changed_evidence_basis_summary_changes_hash(self) -> None:
        base = build_knowledge_snapshot(**self._fields())
        fields = self._fields()
        fields["evidence_basis_summary"] = {"OBSERVED": 1, "INFERRED": 1}
        changed = build_knowledge_snapshot(**fields)
        assert base.snapshot_sha256 != changed.snapshot_sha256


class TestRawNoteArchiving:
    def test_archives_clean_note(self, tmp_path: Path) -> None:
        envelope = archive_raw_note(
            tmp_path, "Deck A tends to hold back energy early.", source_id="note:test-1",
            acquired_at="2026-07-18T00:00:00Z", origin_reference="manual entry",
        )
        assert envelope.source_kind.value == "HUMAN_TEXT"
        assert archive.read_raw(tmp_path, envelope.raw_sha256) == "Deck A tends to hold back energy early.".encode("utf-8")

    def test_secret_bearing_note_is_quarantined_not_archived(self, tmp_path: Path) -> None:
        with pytest.raises(archive.ArchiveError):
            archive_raw_note(
                tmp_path, '{"api_key": "sk-abcdefghijklmnop1234"}', source_id="note:test-2",
                acquired_at="2026-07-18T00:00:00Z", origin_reference="manual entry",
            )
