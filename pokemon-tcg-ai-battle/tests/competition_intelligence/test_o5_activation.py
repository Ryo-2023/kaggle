"""Focused O5 activation contracts that require no external or team data."""

from __future__ import annotations

import math

import pytest

from mage_ptcg.competition_intelligence.o5_activation import (
    ArchetypePolicyPack, BENCHMARK_BLOCKED, GenericArchetypeAgent, GoalRule,
    OpponentInstanceSpec, PhaseRule, RulesUseGate, TeamPermissionManifest,
    build_benchmark_manifest,
)
from mage_ptcg.competition_intelligence.o5_payload import PayloadExtractionError, archive_raw_response, extract_structured_payload


def _pack() -> ArchetypePolicyPack:
    return ArchetypePolicyPack("o5-activation-opponent-factory-v1", "candidate-v1", (), (), (), (PhaseRule("SETUP", 1.0),), (GoalRule("attack", 1.0),), (), (), (), "rule_v0", 0.2, ("fixture",))


@pytest.mark.parametrize("stdout", [
    b"Leaderboard\n[{\"id\":1}]\n",
    b"\xef\xbb\xbf[{\"id\":1}]",
    b"\x1b[33mwarning\x1b[0m\n{\"id\":1}",
])
def test_payload_scanner_accepts_wrapped_single_json(stdout: bytes) -> None:
    candidate = extract_structured_payload(stdout, b"warning on stderr")
    assert candidate.envelope_kind == "SINGLE_JSON"
    assert candidate.payload in ({"id": 1}, [{"id": 1}])


def test_payload_scanner_separates_multiple_and_rejects_truncation() -> None:
    assert extract_structured_payload(b'{"a":1}\n{"b":2}').envelope_kind == "JSON_LINES"
    with pytest.raises(PayloadExtractionError, match="truncated"):
        extract_structured_payload(b'{"a":')


def test_raw_streams_are_archived_before_parser_failure(tmp_path) -> None:
    stdout, stderr = b'{"cut":', b"CLI warning"
    archived = archive_raw_response(tmp_path, stdout=stdout, stderr=stderr, exit_code=0, cli_version="fixture")
    assert (tmp_path / f"{archived['raw_content_hash']}.stdout.bin").read_bytes() == stdout
    assert (tmp_path / f"{archived['raw_content_hash']}.stderr.bin").read_bytes() == stderr
    with pytest.raises(PayloadExtractionError):
        extract_structured_payload(stdout, stderr)


def test_rules_and_team_permissions_remain_fail_closed() -> None:
    assert not RulesUseGate.unverified().permits("archetype_classification")
    manifest = TeamPermissionManifest.from_mapping({
        "schema_version": "team-artifact-permission-v1", "provider_id_hash": "p", "repository": "r", "commit_or_branch": "c",
        "artifact_selectors": [{"path_glob": "agents/*.py", "artifact_hash": None}],
        "allowed_use": {"archive": True, "deck_classification": True, "agent_execution": False, "local_evaluation": False},
        "reviewed_at": "2026-07-20", "reviewed_by_hash": "reviewer", "evidence": "manifest",
    })
    assert manifest.permits("agents/a.py", "0" * 64, "deck_classification")
    assert not manifest.permits("agents/a.py", "0" * 64, "agent_execution")


def test_generic_agent_falls_back_for_deck_mismatch_and_keeps_legal_rule_selection() -> None:
    fallback_calls: list[object] = []
    def fallback(obs):
        fallback_calls.append(obs); return [0]
    agent = GenericArchetypeAgent(deck=[1] * 60, pack=_pack(), expected_deck_hash="not-a-real-hash", fallback=fallback)
    assert agent({"select": {"type": 0, "minCount": 1, "maxCount": 1, "option": [{"type": 7}]}}) == [0]
    assert agent.last_fallback_reason == "deck_mismatch"
    assert fallback_calls


def test_opponent_identity_and_benchmark_are_path_time_independent_and_blocked() -> None:
    values = dict(agent_family_id="generic", agent_artifact_id="agent", deck_hash="deck", variant_id="v", archetype_version_id="a", policy_pack_hash="p", pilot_profile_hash="q", matchup_plan_hash=None, engine_version="cabt", permission_manifest_hash=None, validation_status="FIXTURE_VALIDATED")
    assert OpponentInstanceSpec.build(**values) == OpponentInstanceSpec.build(**values)
    first = build_benchmark_manifest((), active_exact_decks=0, runnable_families=0, verified_links=0)
    assert first["status"] == BENCHMARK_BLOCKED
    assert first["exact_paired_inference"] is False


def test_experimental_pilots_are_additive_and_do_not_change_default_pilots() -> None:
    from mage_ptcg.competition_intelligence.o5_activation import DEFAULT_PILOTS, EXPERIMENTAL_PILOTS
    assert {p.pilot_id for p in DEFAULT_PILOTS} == {"BALANCED", "AGGRESSIVE", "CONSERVATIVE"}
    assert {p.pilot_id for p in EXPERIMENTAL_PILOTS} == {"SETUP_FIRST", "DISRUPTION_FIRST"}
    assert len(DEFAULT_PILOTS) == 3
