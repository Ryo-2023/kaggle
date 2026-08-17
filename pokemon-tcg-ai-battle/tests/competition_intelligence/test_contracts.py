"""Contract tests for Competition Intelligence records: schema, invariants, lifecycle."""

from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.contracts import (
    DECISION_RECORD_SCHEMA_VERSION,
    DECK_OBSERVATION_SCHEMA_VERSION,
    EPISODE_RECORD_SCHEMA_VERSION,
    KNOWLEDGE_CLAIM_SCHEMA_VERSION,
    SOURCE_ENVELOPE_SCHEMA_VERSION,
    AcquisitionMode,
    AllowedUse,
    ClaimStatus,
    ContractError,
    DecisionRecord,
    DeckObservation,
    EpisodeRecord,
    EvidenceBasis,
    EvidenceGrade,
    KnowledgeClaim,
    SourceEnvelope,
    SourceKind,
    build_intelligence_snapshot,
    validate_claim_transition,
)


def _envelope(**overrides: object) -> SourceEnvelope:
    fields = dict(
        schema_version=SOURCE_ENVELOPE_SCHEMA_VERSION,
        source_id="local:abc",
        source_kind=SourceKind.LOCAL_SELFPLAY,
        acquisition_mode=AcquisitionMode.LOCAL_ONLY,
        acquired_at="2026-07-18T00:00:00Z",
        observed_at=None,
        origin_reference="fixture.json",
        owner_scope="self",
        visibility="private",
        allowed_uses=frozenset({AllowedUse.ARCHIVE}),
        terms_snapshot_hash=None,
        raw_sha256="a" * 64,
        parser_version="v1",
        redaction_version="v1",
    )
    fields.update(overrides)
    return SourceEnvelope(**fields)


class TestSourceEnvelope:
    def test_valid_envelope_round_trips_content_hash(self) -> None:
        envelope = _envelope()
        assert envelope.content_hash() == envelope.content_hash()  # deterministic

    def test_rejects_wrong_schema_version(self) -> None:
        with pytest.raises(ContractError):
            _envelope(schema_version="wrong-v0")

    def test_rejects_naive_timestamp(self) -> None:
        with pytest.raises(ContractError):
            _envelope(acquired_at="2026-07-18T00:00:00")  # no tz offset

    def test_rejects_bad_sha256(self) -> None:
        with pytest.raises(ContractError):
            _envelope(raw_sha256="not-a-hash")

    def test_rejects_non_frozenset_allowed_uses(self) -> None:
        with pytest.raises(ContractError):
            _envelope(allowed_uses={AllowedUse.ARCHIVE})  # plain set, not frozenset

    def test_two_envelopes_same_fields_have_same_hash(self) -> None:
        assert _envelope().content_hash() == _envelope().content_hash()

    def test_different_allowed_uses_change_hash(self) -> None:
        a = _envelope(allowed_uses=frozenset({AllowedUse.ARCHIVE}))
        b = _envelope(allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS}))
        assert a.content_hash() != b.content_hash()

    def test_public_other_cannot_grant_training_even_if_explicitly_requested(self) -> None:
        with pytest.raises(ContractError):
            _envelope(
                source_kind=SourceKind.PUBLIC_OTHER,
                allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING}),
            )

    def test_public_other_cannot_grant_redistribution(self) -> None:
        with pytest.raises(ContractError):
            _envelope(
                source_kind=SourceKind.PUBLIC_OTHER,
                allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.REDISTRIBUTION}),
            )

    def test_public_other_can_still_grant_analysis_when_manifest_says_so(self) -> None:
        envelope = _envelope(
            source_kind=SourceKind.PUBLIC_OTHER,
            allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS}),
        )
        assert AllowedUse.ANALYSIS in envelope.allowed_uses

    def test_non_public_other_kinds_may_grant_training(self) -> None:
        envelope = _envelope(
            source_kind=SourceKind.LOCAL_SELFPLAY,
            allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING}),
        )
        assert AllowedUse.TRAINING in envelope.allowed_uses


class TestEpisodeRecord:
    def _episode(self, **overrides: object) -> EpisodeRecord:
        fields = dict(
            schema_version=EPISODE_RECORD_SCHEMA_VERSION,
            episode_id="ep-1",
            source_id="local:abc",
            competition_id=None,
            played_at="2026-07-18T00:00:00Z",
            engine_version="cabt-1.0",
            agent_a="rule_v0",
            agent_b="rule_v0",
            deck_a_reference=None,
            deck_b_reference=None,
            first_player=0,
            winner=1,
            termination_reason="normal",
            turn_count=10,
            decision_count=42,
            public_trace_hash=None,
        )
        fields.update(overrides)
        return EpisodeRecord(**fields)

    def test_valid(self) -> None:
        self._episode()

    def test_rejects_bad_first_player(self) -> None:
        with pytest.raises(ContractError):
            self._episode(first_player=2)

    def test_rejects_negative_turn_count(self) -> None:
        with pytest.raises(ContractError):
            self._episode(turn_count=-1)


class TestDecisionRecord:
    def _decision(self, **overrides: object) -> DecisionRecord:
        fields = dict(
            schema_version=DECISION_RECORD_SCHEMA_VERSION,
            episode_id="ep-1",
            decision_index=0,
            actor_seat=0,
            turn_index=1,
            phase="OPENING",
            actor_information_view=None,
            legal_action_keys=None,
            chosen_action_key=None,
            chosen_action_raw=None,
            public_cards_seen=(),
            board_summary=None,
            latency_us=None,
            fallback_used=False,
            result_to_go=None,
            source_quality="normalized",
        )
        fields.update(overrides)
        return DecisionRecord(**fields)

    def test_null_legal_action_keys_distinct_from_empty_tuple(self) -> None:
        with_null = self._decision(legal_action_keys=None)
        with_empty = self._decision(legal_action_keys=())
        assert with_null.content_payload()["legal_action_keys"] is None
        assert with_empty.content_payload()["legal_action_keys"] == []
        assert with_null.content_hash() != with_empty.content_hash()

    def test_rejects_list_for_legal_action_keys(self) -> None:
        with pytest.raises(ContractError):
            self._decision(legal_action_keys=["a", "b"])  # must be a tuple

    def test_rejects_bad_actor_seat(self) -> None:
        with pytest.raises(ContractError):
            self._decision(actor_seat=5)

    def test_rejects_non_finite_result_to_go(self) -> None:
        with pytest.raises(ContractError):
            self._decision(result_to_go=float("nan"))


class TestDeckObservation:
    def _observation(self, **overrides: object) -> DeckObservation:
        fields = dict(
            schema_version=DECK_OBSERVATION_SCHEMA_VERSION,
            episode_id="ep-1",
            seat=0,
            exact_decklist=None,
            exact_decklist_source=None,
            observed_card_counts={1: 2, 2: 1},
            inferred_archetypes={"aggro": 0.6, "control": 0.3},
            inferred_card_distribution={1: 0.9},
            inference_model_version="v0",
            confidence=0.5,
        )
        fields.update(overrides)
        return DeckObservation(**fields)

    def test_valid(self) -> None:
        self._observation()

    def test_rejects_observed_total_over_deck_size(self) -> None:
        with pytest.raises(ContractError):
            self._observation(observed_card_counts={1: 61})

    def test_rejects_negative_card_count(self) -> None:
        with pytest.raises(ContractError):
            self._observation(observed_card_counts={1: -1})

    def test_rejects_exact_decklist_not_totaling_60(self) -> None:
        with pytest.raises(ContractError):
            self._observation(exact_decklist={1: 4, 2: 4})

    def test_rejects_observed_exceeding_exact_decklist(self) -> None:
        exact = {1: 2}
        exact[3] = 58
        with pytest.raises(ContractError):
            self._observation(exact_decklist=exact, observed_card_counts={1: 3})

    def test_accepts_consistent_exact_and_observed(self) -> None:
        exact = {1: 4, 2: 56}
        self._observation(exact_decklist=exact, observed_card_counts={1: 2})

    def test_rejects_posterior_sum_over_one(self) -> None:
        with pytest.raises(ContractError):
            self._observation(inferred_archetypes={"a": 0.7, "b": 0.7})

    def test_allows_partial_posterior_mass_as_unknown(self) -> None:
        obs = self._observation(inferred_archetypes={"aggro": 0.4})
        assert obs.inferred_archetypes == {"aggro": 0.4}

    def test_rejects_out_of_range_confidence(self) -> None:
        with pytest.raises(ContractError):
            self._observation(confidence=1.5)


class TestKnowledgeClaimLifecycle:
    def _claim(self, **overrides: object) -> KnowledgeClaim:
        fields = dict(
            schema_version=KNOWLEDGE_CLAIM_SCHEMA_VERSION,
            claim_id="claim-1",
            raw_source_id="note-1",
            claim_type="matchup-tech",
            scope={"deck": "A"},
            preconditions=("faces_deck_a",),
            recommendation="hold card X",
            expected_effect=None,
            evidence_grade=EvidenceGrade.E0_UNVALIDATED,
            status=ClaimStatus.RAW,
            validity=0.5,
            support=0.0,
            freshness=1.0,
            supporting_artifacts=(),
            contradicting_claims=(),
            created_at="2026-07-18T00:00:00Z",
            updated_at="2026-07-18T00:00:00Z",
        )
        fields.update(overrides)
        return KnowledgeClaim(**fields)

    def test_valid_raw_claim(self) -> None:
        self._claim()

    def test_rejects_e0_evidence_with_supported_status(self) -> None:
        with pytest.raises(ContractError):
            self._claim(status=ClaimStatus.SUPPORTED, evidence_grade=EvidenceGrade.E0_UNVALIDATED)

    def test_allows_e3_evidence_with_supported_status(self) -> None:
        self._claim(status=ClaimStatus.SUPPORTED, evidence_grade=EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE)

    @pytest.mark.parametrize(
        "old,new",
        [
            (ClaimStatus.RAW, ClaimStatus.PARSED),
            (ClaimStatus.PARSED, ClaimStatus.HYPOTHESIS),
            (ClaimStatus.HYPOTHESIS, ClaimStatus.SUPPORTED),
            (ClaimStatus.HYPOTHESIS, ClaimStatus.INCONCLUSIVE),
            (ClaimStatus.SUPPORTED, ClaimStatus.DEPRECATED),
        ],
    )
    def test_legal_transitions(self, old: ClaimStatus, new: ClaimStatus) -> None:
        validate_claim_transition(old, new, evidence_grade=EvidenceGrade.E4_STRONG_EMPIRICAL_EVIDENCE)

    @pytest.mark.parametrize(
        "old,new",
        [
            (ClaimStatus.RAW, ClaimStatus.SUPPORTED),  # cannot skip straight to SUPPORTED
            (ClaimStatus.REJECTED, ClaimStatus.HYPOTHESIS),  # terminal state reopened
            (ClaimStatus.DEPRECATED, ClaimStatus.SUPPORTED),  # terminal state reopened
            (ClaimStatus.RAW, ClaimStatus.HYPOTHESIS),  # skips PARSED
        ],
    )
    def test_illegal_transitions(self, old: ClaimStatus, new: ClaimStatus) -> None:
        with pytest.raises(ContractError):
            validate_claim_transition(old, new, evidence_grade=EvidenceGrade.E4_STRONG_EMPIRICAL_EVIDENCE)

    def test_transition_to_supported_requires_minimum_evidence(self) -> None:
        with pytest.raises(ContractError):
            validate_claim_transition(
                ClaimStatus.HYPOTHESIS, ClaimStatus.SUPPORTED, evidence_grade=EvidenceGrade.E1_ANECDOTAL
            )

    def test_with_transition_produces_new_object_and_preserves_lineage_fields(self) -> None:
        claim = self._claim(status=ClaimStatus.HYPOTHESIS, evidence_grade=EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE)
        moved = claim.with_transition(ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:00Z")
        assert moved.status == ClaimStatus.SUPPORTED
        assert moved.claim_id == claim.claim_id
        assert claim.status == ClaimStatus.HYPOTHESIS  # original is untouched (immutable)

    def test_contradicting_claims_are_preserved_not_deduplicated_away(self) -> None:
        claim = self._claim(contradicting_claims=("claim-2", "claim-3"))
        assert claim.contradicting_claims == ("claim-2", "claim-3")

    def test_new_claim_defaults_to_inferred_and_ineligible(self) -> None:
        claim = self._claim()
        assert claim.evidence_basis == EvidenceBasis.INFERRED
        assert claim.training_eligible is False
        assert claim.runtime_eligible is False
        assert claim.supersedes == ()

    def test_training_eligible_requires_supported_status(self) -> None:
        with pytest.raises(ContractError):
            self._claim(status=ClaimStatus.HYPOTHESIS, training_eligible=True)

    def test_runtime_eligible_requires_supported_status(self) -> None:
        with pytest.raises(ContractError):
            self._claim(status=ClaimStatus.HYPOTHESIS, runtime_eligible=True)

    def test_supported_claim_can_be_training_and_runtime_eligible(self) -> None:
        claim = self._claim(
            status=ClaimStatus.SUPPORTED, evidence_grade=EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE,
            evidence_basis=EvidenceBasis.OBSERVED, training_eligible=True, runtime_eligible=True,
        )
        assert claim.training_eligible is True
        assert claim.runtime_eligible is True

    def test_claim_cannot_supersede_itself(self) -> None:
        with pytest.raises(ContractError):
            self._claim(supersedes=("claim-1",))

    def test_with_transition_to_supported_can_grant_training_eligible(self) -> None:
        claim = self._claim(status=ClaimStatus.HYPOTHESIS, evidence_grade=EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE)
        moved = claim.with_transition(ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:00Z", training_eligible=True)
        assert moved.training_eligible is True
        assert moved.runtime_eligible is False
        assert claim.training_eligible is False  # original untouched

    def test_with_transition_away_from_supported_resets_eligibility(self) -> None:
        claim = self._claim(status=ClaimStatus.HYPOTHESIS, evidence_grade=EvidenceGrade.E3_CONTROLLED_LOCAL_EVIDENCE)
        supported = claim.with_transition(ClaimStatus.SUPPORTED, updated_at="2026-07-19T00:00:00Z", training_eligible=True)
        deprecated = supported.with_transition(ClaimStatus.DEPRECATED, updated_at="2026-07-20T00:00:00Z")
        assert deprecated.training_eligible is False
        assert deprecated.runtime_eligible is False

    def test_with_transition_cannot_grant_eligibility_off_supported_target(self) -> None:
        claim = self._claim(status=ClaimStatus.RAW)
        with pytest.raises(ContractError):
            claim.with_transition(ClaimStatus.PARSED, updated_at="2026-07-19T00:00:00Z", training_eligible=True)


class TestIntelligenceSnapshot:
    def _base_fields(self) -> dict[str, object]:
        return dict(
            created_at="2026-07-18T00:00:00Z",
            cutoff_time="2026-07-17T00:00:00Z",
            base_commit="6782e68",
            input_source_ids=("s1", "s2"),
            input_hashes=("h1", "h2"),
            normalizer_versions={"replay": "v1"},
            analysis_versions={"deck_fingerprint": "v1"},
            permission_summary={"ANALYSIS": 2, "TRAINING": 1},
            knowledge_snapshot_hash=None,
            meta_snapshot_hash=None,
            selection_policy="all",
            source_weights={"s1": 1.0, "s2": 0.5},
            split_policy="episode_group",
            excluded_records=(),
            episode_count=2,
            decision_count=10,
        )

    def test_build_is_self_verifying_and_deterministic(self) -> None:
        snapshot_a = build_intelligence_snapshot(**self._base_fields())
        snapshot_b = build_intelligence_snapshot(**self._base_fields())
        assert snapshot_a.snapshot_sha256 == snapshot_b.snapshot_sha256
        assert snapshot_a.snapshot_id == snapshot_b.snapshot_id
        assert snapshot_a.snapshot_id.startswith("intelligence-snapshot-")

    def test_changed_input_changes_hash(self) -> None:
        fields = self._base_fields()
        base = build_intelligence_snapshot(**fields)
        fields["episode_count"] = 3
        changed = build_intelligence_snapshot(**fields)
        assert base.snapshot_sha256 != changed.snapshot_sha256

    def test_changed_permission_summary_changes_hash(self) -> None:
        fields = self._base_fields()
        base = build_intelligence_snapshot(**fields)
        fields["permission_summary"] = {"ANALYSIS": 2}
        changed = build_intelligence_snapshot(**fields)
        assert base.snapshot_sha256 != changed.snapshot_sha256

    def test_rejects_negative_permission_summary_count(self) -> None:
        fields = self._base_fields()
        fields["permission_summary"] = {"ANALYSIS": -1}
        with pytest.raises(ContractError):
            build_intelligence_snapshot(**fields)

    def test_tampered_hash_is_rejected_on_reconstruction(self) -> None:
        snapshot = build_intelligence_snapshot(**self._base_fields())
        payload = snapshot.content_payload()
        with pytest.raises(ContractError):
            from mage_ptcg.competition_intelligence.contracts import (
                INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION,
                IntelligenceSnapshot,
            )

            IntelligenceSnapshot(
                schema_version=INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION,
                snapshot_id=snapshot.snapshot_id,
                snapshot_sha256="0" * 64,
                **{k: v for k, v in payload.items() if k != "schema_version"},
            )

    def test_rejects_explicit_snapshot_id_or_hash(self) -> None:
        fields = self._base_fields()
        fields["snapshot_id"] = "hand-picked"
        with pytest.raises(ContractError):
            build_intelligence_snapshot(**fields)
