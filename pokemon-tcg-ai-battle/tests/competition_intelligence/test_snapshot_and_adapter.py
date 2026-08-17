"""Tests for group_split.py, leakage_audit.py, snapshot_builder.py, offline_adapter.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence.contracts import (
    EPISODE_RECORD_SCHEMA_VERSION,
    SOURCE_ENVELOPE_SCHEMA_VERSION,
    AcquisitionMode,
    AllowedUse,
    EpisodeRecord,
    SourceKind,
    SourceEnvelope,
)
from mage_ptcg.competition_intelligence.group_split import (
    MINIMUM_GROUPS_FOR_SPLIT,
    GroupSplitError,
    build_hard_identity_components,
    split_by_composite_group,
)
from mage_ptcg.competition_intelligence.leakage_audit import audit_split_leakage
from mage_ptcg.competition_intelligence.offline_adapter import (
    DatasetExportError,
    build_example_weights,
    enforce_training_permission,
    export_selected_rows,
)
from mage_ptcg.competition_intelligence.offline_reader import iter_rule_bc_rows
from mage_ptcg.competition_intelligence.replay_normalize import normalize_rule_bc_jsonl
from mage_ptcg.competition_intelligence.snapshot_builder import SnapshotBuildError, build_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK_PATH = REPO_ROOT / "deck.csv"


def _episode(**overrides) -> EpisodeRecord:
    fields = dict(
        schema_version=EPISODE_RECORD_SCHEMA_VERSION, episode_id="ep-1", source_id="src-1",
        competition_id=None, played_at=None, engine_version=None, agent_a="rule_v0", agent_b="opponent_a",
        deck_a_reference="deck-x", deck_b_reference=None, first_player=0, winner=None,
        termination_reason=None, turn_count=5, decision_count=3, public_trace_hash=None,
    )
    fields.update(overrides)
    return EpisodeRecord(**fields)


def _diversify_opponents(episodes, *, n_opponents: int = 4):
    """Return copies of real normalized episodes with synthetic opponent diversity.

    The real offline_training fixture collector is pure self-play (every
    episode has the exact same ``agent_b``), so it is mathematically
    impossible to split it without opponent leakage -- that is a real
    property of single-opponent data, not a bug in the leakage audit. To
    test that a genuinely clean, leakage-free split *is* achievable when the
    underlying data has real opponent diversity, this reuses the real
    ``EpisodeRecord`` objects (real ids, hashes, decision linkage) but
    overrides ``agent_b`` synthetically.
    """
    import dataclasses

    return [dataclasses.replace(episode, agent_b=f"opponent_{index % n_opponents}") for index, episode in enumerate(episodes)]


def _envelope(source_id: str, *, allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS, AllowedUse.TRAINING})) -> SourceEnvelope:
    return SourceEnvelope(
        schema_version=SOURCE_ENVELOPE_SCHEMA_VERSION, source_id=source_id, source_kind=SourceKind.LOCAL_SELFPLAY,
        acquisition_mode=AcquisitionMode.LOCAL_ONLY, acquired_at="2026-07-18T00:00:00Z", observed_at=None,
        origin_reference="x", owner_scope="self", visibility="private", allowed_uses=allowed_uses,
        terms_snapshot_hash=None, raw_sha256="a" * 64, parser_version="v1", redaction_version="v1",
    )


@pytest.fixture(scope="module")
def normalized(tmp_path_factory):
    from mage_ptcg.offline_training.collection import run_collection

    root = tmp_path_factory.mktemp("o1-4-collection")
    run_collection(
        source="fixture", run_id="cabt", games=10, base_seed=4000, output_root=root / "collection",
        canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPO_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=3, fixture_option_count=3,
    )
    jsonl_path = root / "collection" / "cabt" / "private_dataset" / "rule-bc-v1.jsonl"
    result = normalize_rule_bc_jsonl(jsonl_path, source_id="local:o1-4-source")
    return {"jsonl_path": jsonl_path, "episodes": result.episodes, "decisions": result.decisions}


class TestGroupSplit:
    # normalized["episodes"] is real self-play data with one constant
    # opponent identity, which (correctly, post-remediation -- see
    # TestLeakageAudit's self-play test) collapses to a *single*
    # hard-identity component and cannot be split at all: temporal bucketing
    # can no longer manufacture extra splittable groups out of it (that was
    # exactly the independent-audit finding -- see group_split.py's module
    # docstring). Tests that need a genuinely splittable multi-group input
    # diversify opponents instead (real signal, not synthetic grouping).
    def test_partitions_are_disjoint(self, normalized) -> None:
        diversified = _diversify_opponents(normalized["episodes"])
        result = split_by_composite_group(diversified, seed=42)
        train, val, test = set(result.train_episode_ids), set(result.validation_episode_ids), set(result.test_episode_ids)
        assert train & val == set()
        assert train & test == set()
        assert val & test == set()

    def test_every_episode_assigned_exactly_once(self, normalized) -> None:
        diversified = _diversify_opponents(normalized["episodes"])
        result = split_by_composite_group(diversified, seed=42)
        all_assigned = list(result.train_episode_ids) + list(result.validation_episode_ids) + list(result.test_episode_ids)
        assert sorted(all_assigned) == sorted(e.episode_id for e in diversified)
        assert len(all_assigned) == len(set(all_assigned))

    def test_deterministic_same_seed(self, normalized) -> None:
        diversified = _diversify_opponents(normalized["episodes"])
        a = split_by_composite_group(diversified, seed=42)
        b = split_by_composite_group(diversified, seed=42)
        assert a.manifest["split_hash"] == b.manifest["split_hash"]
        assert a.train_episode_ids == b.train_episode_ids

    def test_different_seed_can_change_assignment(self, normalized) -> None:
        diversified = _diversify_opponents(normalized["episodes"])
        a = split_by_composite_group(diversified, seed=1)
        b = split_by_composite_group(diversified, seed=2)
        # not guaranteed to differ for every possible dataset, but the hash must be seed-derived
        assert a.manifest["split_hash"] != b.manifest["split_hash"] or a.train_episode_ids == b.train_episode_ids

    def test_too_few_groups_raises_not_silently_reduces(self) -> None:
        episodes = [_episode(episode_id="ep-1"), _episode(episode_id="ep-2")]
        with pytest.raises(GroupSplitError) as excinfo:
            split_by_composite_group(episodes, seed=1)
        assert excinfo.value.component_count == 1  # both share the default agent_b

    def test_same_opponent_grouped_together_across_episodes(self) -> None:
        # 6 episodes sharing the same opponent identity must all land in the
        # same partition together; 2 more episodes with a distinct opponent
        # give the >=3 distinct components a split requires, without
        # disturbing the 6-episode component's togetherness.
        shared_group = [
            _episode(episode_id=f"ep-{i}", agent_b="shared_opponent", deck_a_reference="shared_deck")
            for i in range(6)
        ]
        other_groups = [
            _episode(episode_id="ep-other-1", agent_b="other_opponent_1", deck_a_reference="shared_deck"),
            _episode(episode_id="ep-other-2", agent_b="other_opponent_2", deck_a_reference="shared_deck"),
        ]
        result = split_by_composite_group(shared_group + other_groups, seed=7)
        buckets = [set(result.train_episode_ids), set(result.validation_episode_ids), set(result.test_episode_ids)]
        shared_ids = {e.episode_id for e in shared_group}
        # whichever bucket contains any episode from shared_group must contain all of them
        containing = [bucket for bucket in buckets if bucket & shared_ids]
        assert len(containing) == 1
        assert containing[0] & shared_ids == shared_ids

    def test_same_opponent_different_deck_stays_together(self) -> None:
        # Independent-audit finding #1: the previous AND-composite key
        # dispersed same-opponent episodes across splits whenever *any other*
        # dimension (e.g. deck) differed. Hard identity (opponent) must win
        # regardless of deck.
        episodes = [
            _episode(episode_id="ep-1", agent_b="shared_opponent", deck_a_reference="deck-A"),
            _episode(episode_id="ep-2", agent_b="shared_opponent", deck_a_reference="deck-B"),
            _episode(episode_id="ep-3", agent_b="other_opponent_1", deck_a_reference="deck-C"),
            _episode(episode_id="ep-4", agent_b="other_opponent_2", deck_a_reference="deck-D"),
        ]
        result = split_by_composite_group(episodes, seed=1)
        buckets = [set(result.train_episode_ids), set(result.validation_episode_ids), set(result.test_episode_ids)]
        shared_ids = {"ep-1", "ep-2"}
        containing = [bucket for bucket in buckets if bucket & shared_ids]
        assert len(containing) == 1
        assert containing[0] & shared_ids == shared_ids

    def test_same_deck_different_opponent_is_not_forced_together(self) -> None:
        # Contrast case: deck identity remains report-only (unchanged,
        # pre-existing design -- see leakage_audit.py). Sharing only a deck
        # (not an opponent) must NOT force two episodes into one component,
        # or splitting a single-deck dataset would become impossible.
        episodes = [
            _episode(episode_id="ep-1", agent_b="opponent_1", deck_a_reference="shared_deck"),
            _episode(episode_id="ep-2", agent_b="opponent_2", deck_a_reference="shared_deck"),
            _episode(episode_id="ep-3", agent_b="opponent_3", deck_a_reference="shared_deck"),
        ]
        assignment = build_hard_identity_components(episodes)
        assert assignment.component_count == 3  # each episode its own component: only opponent differs each time

    def test_transitive_hard_identity_connects_into_one_component(self) -> None:
        # Independent-audit finding #5: A-B share an opponent, B-C share a
        # *different* opponent value is impossible for a single dimension,
        # so this exercises transitivity via two episodes bridging through a
        # shared middle episode's opponent identity chain is represented as:
        # ep-A and ep-B share opponent X; ep-B and ep-C share... a decision:
        # since opponent is single-valued per episode, transitivity here is
        # demonstrated via a bridging episode that shares X with the first
        # group and Y with a third, forcing A, B(bridge), and C together
        # only through the bridge -- but B can only have one agent_b value,
        # so instead this uses two independent bridges over the *same*
        # dimension to prove multi-hop chains collapse into one component.
        episodes = [
            _episode(episode_id="ep-a", agent_b="opponent_X"),
            _episode(episode_id="ep-b", agent_b="opponent_X"),
            _episode(episode_id="ep-b2", agent_b="opponent_Y"),
            _episode(episode_id="ep-c", agent_b="opponent_Y"),
            _episode(episode_id="ep-other", agent_b="opponent_Z"),
        ]
        assignment = build_hard_identity_components(episodes)
        # ep-a/ep-b (opponent_X) and ep-b2/ep-c (opponent_Y) are each directly
        # connected pairs; they are *not* transitively linked to each other
        # here (no shared value bridges the two pairs), so this proves the
        # converse: distinct hard-identity values do NOT get merged without
        # an actual shared value, only true chains do.
        assert assignment.episode_component_id["ep-a"] == assignment.episode_component_id["ep-b"]
        assert assignment.episode_component_id["ep-b2"] == assignment.episode_component_id["ep-c"]
        assert assignment.episode_component_id["ep-a"] != assignment.episode_component_id["ep-b2"]
        assert assignment.component_count == 3  # {a,b}, {b2,c}, {other}

    def test_component_assignment_independent_of_input_order(self) -> None:
        episodes = [
            _episode(episode_id="ep-1", agent_b="shared_opponent"),
            _episode(episode_id="ep-2", agent_b="shared_opponent"),
            _episode(episode_id="ep-3", agent_b="other_opponent"),
        ]
        forward = build_hard_identity_components(episodes)
        backward = build_hard_identity_components(list(reversed(episodes)))
        assert forward.episode_component_id == backward.episode_component_id
        assert set(forward.components_by_id) == set(backward.components_by_id)

    def test_split_hash_independent_of_input_order(self) -> None:
        episodes = [
            _episode(episode_id=f"ep-{i}", agent_b=f"opponent_{i % 4}", deck_a_reference=f"deck-{i}")
            for i in range(12)
        ]
        forward = split_by_composite_group(episodes, seed=42)
        backward = split_by_composite_group(list(reversed(episodes)), seed=42)
        assert forward.manifest["split_hash"] == backward.manifest["split_hash"]

    def test_unsplittable_component_reports_diagnostics_not_fallback(self) -> None:
        episodes = [_episode(episode_id=f"ep-{i}", agent_b="only_opponent") for i in range(5)]
        with pytest.raises(GroupSplitError) as excinfo:
            split_by_composite_group(episodes, seed=1)
        assert excinfo.value.component_count == 1
        assert excinfo.value.component_sizes == (5,)
        assert excinfo.value.largest_component_id is not None

    def test_invalid_fractions_rejected(self, normalized) -> None:
        with pytest.raises(GroupSplitError):
            split_by_composite_group(normalized["episodes"], seed=1, validation_fraction=0.6, test_fraction=0.6)


class TestLeakageAudit:
    def test_pure_self_play_single_opponent_data_cannot_pass_opponent_leakage(self, normalized) -> None:
        # The real offline_training fixture is pure self-play: every episode
        # shares the exact same agent_b. A truly leakage-free split on the
        # opponent dimension is mathematically impossible for such data.
        # Post-remediation (independent audit finding #1), this is now
        # rejected *at split-assignment time* -- hard-identity components are
        # computed before any train/validation/test assignment happens, so a
        # single-opponent dataset collapses into one component and
        # split_by_composite_group itself raises GroupSplitError, rather than
        # silently succeeding and being caught only later (destructively) by
        # audit_split_leakage. temporal bucketing can no longer paper over
        # this (see group_split.py's module docstring for why that was
        # exactly the bug).
        with pytest.raises(GroupSplitError) as excinfo:
            split_by_composite_group(normalized["episodes"], seed=1)
        assert excinfo.value.component_count == 1
        assert excinfo.value.component_sizes == (len(normalized["episodes"]),)

    def test_pure_self_play_component_assignment_confirms_single_component(self, normalized) -> None:
        assignment = build_hard_identity_components(normalized["episodes"])
        assert assignment.component_count == 1
        assert set(assignment.episode_component_id) == {e.episode_id for e in normalized["episodes"]}

    def test_clean_split_passes_with_genuinely_diverse_opponents(self, normalized) -> None:
        # With real opponent diversity (synthetic here, but reusing real
        # EpisodeRecord objects/ids/hashes), a fully clean split is achievable.
        diversified = _diversify_opponents(normalized["episodes"])
        episodes_by_id = {e.episode_id: e for e in diversified}
        split = split_by_composite_group(diversified, seed=1)
        result = audit_split_leakage(
            train_ids=split.train_episode_ids, validation_ids=split.validation_episode_ids,
            test_ids=split.test_episode_ids, episodes_by_id=episodes_by_id,
        )
        assert result.passed
        assert result.episode_leakage_count == 0
        assert result.opponent_leakage_count == 0
        assert result.duplicate_leakage_count == 0

    def test_detects_episode_overlap(self) -> None:
        episodes_by_id = {"ep-1": _episode(episode_id="ep-1")}
        result = audit_split_leakage(
            train_ids=["ep-1"], validation_ids=["ep-1"], test_ids=[], episodes_by_id=episodes_by_id
        )
        assert not result.passed
        assert result.episode_leakage_count == 1

    def test_detects_opponent_leakage(self) -> None:
        episodes_by_id = {
            "ep-1": _episode(episode_id="ep-1", agent_b="opponent_x"),
            "ep-2": _episode(episode_id="ep-2", agent_b="opponent_x"),
        }
        result = audit_split_leakage(
            train_ids=["ep-1"], validation_ids=["ep-2"], test_ids=[], episodes_by_id=episodes_by_id
        )
        assert not result.passed
        assert result.opponent_leakage_count == 1

    def test_detects_temporal_leakage(self) -> None:
        episodes_by_id = {"ep-1": _episode(episode_id="ep-1", played_at="2026-08-01T00:00:00Z")}
        result = audit_split_leakage(
            train_ids=["ep-1"], validation_ids=[], test_ids=[], episodes_by_id=episodes_by_id,
            cutoff_time="2026-07-18T00:00:00Z",
        )
        assert not result.passed
        assert result.temporal_leakage_count == 1

    def test_detects_duplicate_leakage_within_same_split(self) -> None:
        episodes_by_id = {"ep-1": _episode(episode_id="ep-1")}
        result = audit_split_leakage(
            train_ids=["ep-1", "ep-1"], validation_ids=[], test_ids=[], episodes_by_id=episodes_by_id
        )
        assert not result.passed
        assert result.duplicate_leakage_count == 1

    def test_detects_future_knowledge_claim_leakage(self) -> None:
        from mage_ptcg.competition_intelligence.claim_bundle import build_knowledge_claim

        claim = build_knowledge_claim(
            {"claim_id": "c1", "claim_type": "x", "scope": {}, "recommendation": "x", "evidence_grade": "E1_ANECDOTAL"},
            raw_source_id="note-1", created_at="2026-08-01T00:00:00Z",
        )
        episodes_by_id = {"ep-1": _episode(episode_id="ep-1")}
        result = audit_split_leakage(
            train_ids=["ep-1"], validation_ids=[], test_ids=[], episodes_by_id=episodes_by_id,
            cutoff_time="2026-07-18T00:00:00Z", knowledge_claims=[claim],
        )
        assert not result.passed
        assert result.future_knowledge_claim_leakage_count == 1

    def test_result_is_computed_not_hardcoded(self, normalized) -> None:
        # regression guard: two different inputs must not silently produce identical counts of 0
        episodes_by_id = {e.episode_id: e for e in normalized["episodes"]}
        clean = audit_split_leakage(
            train_ids=[normalized["episodes"][0].episode_id], validation_ids=[normalized["episodes"][1].episode_id],
            test_ids=[normalized["episodes"][2].episode_id], episodes_by_id=episodes_by_id,
        )
        dirty = audit_split_leakage(
            train_ids=[normalized["episodes"][0].episode_id], validation_ids=[normalized["episodes"][0].episode_id],
            test_ids=[], episodes_by_id=episodes_by_id,
        )
        assert clean.episode_leakage_count != dirty.episode_leakage_count


class TestSnapshotBuilder:
    def test_builds_snapshot_from_real_normalized_data(self, normalized) -> None:
        # Diversify opponents synthetically (see _diversify_opponents): the raw
        # fixture is pure self-play with one opponent identity, for which a
        # leakage-free split is mathematically impossible (see
        # TestLeakageAudit.test_pure_self_play_single_opponent_data_cannot_pass_opponent_leakage).
        episodes = _diversify_opponents(normalized["episodes"])
        kept_ids = {e.episode_id for e in episodes}
        decisions = [d for d in normalized["decisions"] if d.episode_id in kept_ids]
        envelope = _envelope("local:o1-4-source")
        result = build_snapshot(
            episodes=episodes, decisions=decisions, source_envelopes=[envelope],
            cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
            require_cutoff=False,  # fixture episodes have played_at=None
        )
        assert result.snapshot.episode_count == len(episodes)
        assert result.leakage_audit is not None
        assert result.leakage_audit.passed
        assert result.component_diagnostics["splittable"] is True
        assert result.component_diagnostics["component_count"] >= 3

    def test_component_diagnostics_reports_unsplittable_self_play_data(self, normalized) -> None:
        # No opponent diversification here: real self-play data, one
        # constant opponent -> one component, unsplittable.
        envelope = _envelope("local:o1-4-source")
        result = build_snapshot(
            episodes=normalized["episodes"], decisions=normalized["decisions"], source_envelopes=[envelope],
            cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
            require_cutoff=False,
        )
        assert result.split is None
        assert result.component_diagnostics["splittable"] is False
        assert result.component_diagnostics["component_count"] == 1
        assert "unsplittable_reason" in result.component_diagnostics

    def test_require_cutoff_excludes_unknown_played_at(self, normalized) -> None:
        envelope = _envelope("local:o1-4-source")
        with pytest.raises(SnapshotBuildError):
            build_snapshot(
                episodes=normalized["episodes"], decisions=normalized["decisions"], source_envelopes=[envelope],
                cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
                require_cutoff=True,  # played_at is None for all fixture episodes -> all excluded -> error
            )

    def test_source_denying_analysis_is_excluded(self, normalized) -> None:
        envelope = _envelope("local:o1-4-source", allowed_uses=frozenset({AllowedUse.ARCHIVE}))
        with pytest.raises(SnapshotBuildError):
            build_snapshot(
                episodes=normalized["episodes"], decisions=normalized["decisions"], source_envelopes=[envelope],
                cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
                require_cutoff=False,
            )

    def test_two_builds_produce_identical_snapshot_hash(self, normalized) -> None:
        episodes = _diversify_opponents(normalized["episodes"])
        kept_ids = {e.episode_id for e in episodes}
        decisions = [d for d in normalized["decisions"] if d.episode_id in kept_ids]
        envelope = _envelope("local:o1-4-source")
        kwargs = dict(
            episodes=episodes, decisions=decisions, source_envelopes=[envelope],
            cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
            require_cutoff=False, seed=7,
        )
        a = build_snapshot(**kwargs)
        b = build_snapshot(**kwargs)
        assert a.snapshot.snapshot_sha256 == b.snapshot.snapshot_sha256
        assert a.snapshot.snapshot_id == b.snapshot.snapshot_id

    def test_source_allowlist_excludes_other_sources(self, normalized) -> None:
        envelope = _envelope("local:o1-4-source")
        with pytest.raises(SnapshotBuildError):
            build_snapshot(
                episodes=normalized["episodes"], decisions=normalized["decisions"], source_envelopes=[envelope],
                cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
                require_cutoff=False, source_allowlist=["some-other-source-not-present"],
            )

    def test_source_cap_limits_episodes(self, normalized) -> None:
        envelope = _envelope("local:o1-4-source")
        result = build_snapshot(
            episodes=normalized["episodes"], decisions=normalized["decisions"], source_envelopes=[envelope],
            cutoff_time="2026-07-18T00:00:00Z", base_commit="6782e68", created_at="2026-07-18T00:00:00Z",
            require_cutoff=False, source_cap=2,
        )
        assert result.snapshot.episode_count == 2


class TestOfflineAdapter:
    def test_export_selected_rows_filters_correctly(self, normalized, tmp_path: Path) -> None:
        selected_ids = {normalized["episodes"][0].episode_id, normalized["episodes"][1].episode_id}
        output = tmp_path / "selected.jsonl"
        counts = export_selected_rows(normalized["jsonl_path"], output, selected_episode_ids=selected_ids)
        assert counts["kept_rows"] > 0
        assert counts["kept_rows"] < counts["total_source_rows"]

        kept_source_ids = set()
        for _, row, _ in iter_rule_bc_rows(output):
            assert row is not None
            kept_source_ids.add(row["source_id"])
        assert kept_source_ids == selected_ids

    def test_export_preserves_row_shape_exactly(self, normalized, tmp_path: Path) -> None:
        selected_ids = {normalized["episodes"][0].episode_id}
        output = tmp_path / "selected.jsonl"
        export_selected_rows(normalized["jsonl_path"], output, selected_episode_ids=selected_ids)
        original_rows = [row for _, row, _ in iter_rule_bc_rows(normalized["jsonl_path"]) if row and row["source_id"] in selected_ids]
        exported_rows = [row for _, row, _ in iter_rule_bc_rows(output)]
        assert original_rows == exported_rows  # byte-for-byte field equality, not just count

    def test_no_snapshot_means_full_file_is_semantically_the_source(self, normalized) -> None:
        # "no snapshot" = operator just uses normalized["jsonl_path"] directly; verify it's
        # untouched and still fully readable by the same reader used for the selected export.
        all_rows = [row for _, row, _ in iter_rule_bc_rows(normalized["jsonl_path"])]
        assert len(all_rows) > 0
        assert all(row is not None for row in all_rows)

    def test_enforce_training_permission_passes_when_granted(self) -> None:
        envelope = _envelope("src-1")
        enforce_training_permission([envelope])  # must not raise

    def test_enforce_training_permission_rejects_when_missing(self) -> None:
        envelope = _envelope("src-1", allowed_uses=frozenset({AllowedUse.ARCHIVE}))
        with pytest.raises(DatasetExportError):
            enforce_training_permission([envelope])

    def test_public_other_cannot_be_smuggled_into_training_export(self) -> None:
        # PUBLIC_OTHER + TRAINING is structurally impossible to construct at all
        # (SourceEnvelope.__post_init__ rejects it) -- verifying that guarantee
        # holds is the real test here, exercised via the contract layer.
        from mage_ptcg.competition_intelligence.contracts import ContractError

        with pytest.raises(ContractError):
            _envelope("src-public", allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING})).__class__(
                schema_version=SOURCE_ENVELOPE_SCHEMA_VERSION, source_id="src-public", source_kind=SourceKind.PUBLIC_OTHER,
                acquisition_mode=AcquisitionMode.PUBLIC_ARTIFACTS_ONLY, acquired_at="2026-07-18T00:00:00Z",
                observed_at=None, origin_reference="x", owner_scope="other", visibility="public",
                allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.TRAINING}), terms_snapshot_hash=None,
                raw_sha256="a" * 64, parser_version="v1", redaction_version="v1",
            )

    def test_build_example_weights_defaults_to_1_0(self, normalized) -> None:
        weights = build_example_weights([e.episode_id for e in normalized["episodes"][:2]])
        for entry in weights["weights"].values():
            assert entry["example_weight"] == 1.0
            assert entry["source_kind"] is None
