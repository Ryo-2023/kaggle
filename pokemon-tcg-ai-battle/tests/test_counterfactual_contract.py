from mage_ptcg.optimization.counterfactual import EvidenceTier, PublicBeliefParticleSampler, RootBranchBackend, allowed_uses, evidence_record


def test_branch_probe_detects_independent_python_mutation() -> None:
    probe = RootBranchBackend().probe({"cards": [1]}, lambda state: state["cards"].append(2), repr)
    assert probe.independent_mutation
    assert probe.production_viability == "NO"


def test_hidden_feature_is_excluded_from_all_evidence() -> None:
    assert allowed_uses(EvidenceTier.PUBLIC_BELIEF_AGGREGATED, runtime_feature_keys=["opponent_hand"]) == ()
    row = evidence_record(tier=EvidenceTier.PUBLIC_BELIEF_AGGREGATED, runtime_feature_keys=["opponent_hand"], root_id="r")
    assert not row["eligible_training"] and not row["promotion_eligible"]


def test_sampler_rejects_round_trip_mismatch_deterministically() -> None:
    sampler = PublicBeliefParticleSampler()
    rows = sampler.sample(deck_ids=["B", "A"], seed=7, reconstruct=lambda deck, seed: {"deck": deck, "seed": seed}, actor_view_digest=lambda value: value["deck"], target_view_digest="X", posterior={"UNKNOWN": 1.0})
    assert [row.deck_id for row in rows] == ["A", "B"]
    assert all(not row.actor_view_match and row.rejection_reason == "ACTOR_VIEW_ROUND_TRIP_MISMATCH" for row in rows)


def test_ctde_without_proven_common_rng_is_invalid() -> None:
    row = evidence_record(tier=EvidenceTier.CTDE_PAIRED_TRUE_STATE, runtime_feature_keys=[], root_id="r",
                          branch_status="DIAGNOSTIC_ONLY")
    assert row["evidence_tier"] == EvidenceTier.INVALID_BRANCH.value
    assert not row["eligible_training"] and not row["promotion_eligible"]
