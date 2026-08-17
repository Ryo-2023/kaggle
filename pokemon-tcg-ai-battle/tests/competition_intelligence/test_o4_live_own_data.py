from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence.external_transport import ExternalRawResponse, FixtureTransport
from mage_ptcg.competition_intelligence.live_payloads import LivePayloadError, normalize_live_payload, normalized_episode_ids, normalized_submission_ids
from mage_ptcg.competition_intelligence.participant_resolver import (
    OwnSubmissionBootstrap, TeamIdentity, bootstrap_identity_from_replay, classify_replay_participants,
    resolve_episode_agent_mapping, resolve_own_agent_indices,
)
from mage_ptcg.competition_intelligence.rules_attestation import RulesAttestation
from mage_ptcg.competition_intelligence.run_live_acquisition import LiveAcquisitionConfig, run_live_acquisition


def _body(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def test_cli_payload_parses_lists_extra_fields_and_empty_response() -> None:
    submissions = normalize_live_payload("own_submission_listing", _body([{"ref": "sub-1", "extra": True}])).payload
    episodes = normalize_live_payload("own_episode_listing", _body({"episodes": [{"episodeId": 9, "privateScore": None}]})).payload
    empty = normalize_live_payload("own_submission_listing", b"[]").payload
    assert normalized_submission_ids(submissions) == ("sub-1",)
    assert normalized_episode_ids(episodes) == ("9",)
    assert empty["records"] == []


def test_episode_agent_mapping_preserves_sdk_fields_and_rejects_ambiguous_side() -> None:
    payload = normalize_live_payload("own_episode_listing", _body({"episodes": [{
        "id": 9,
        "agents": [
            {"submissionId": 101, "index": 0, "teamId": 42, "teamName": "ours", "reward": 1},
            {"submissionId": 202, "index": 1, "teamId": 7, "teamName": "other", "reward": 0},
        ],
    }]})).payload
    episode = payload["records"][0]
    result = resolve_episode_agent_mapping(episode, OwnSubmissionBootstrap("101", "a" * 64))
    assert result.reason == "episode_submission_side_verified"
    assert result.identity == TeamIdentity(team_id="42", team_name="ours")
    assert result.agent_indices == (0,)
    ambiguous = dict(episode)
    ambiguous["_normalized_agents"] = [*episode["_normalized_agents"], {"submission_id": "101", "agent_index": 2, "team_id": "42", "team_name": "ours", "state": None}]
    assert resolve_episode_agent_mapping(ambiguous, OwnSubmissionBootstrap("101", "a" * 64)).reason == "episode_agent_mapping_ambiguous"
    snake = normalize_live_payload("own_episode_listing", _body([{
        "id": "snake", "agents": [{"submission_id": "101", "index": "0", "team_id": "42", "team_name": "ours"}],
    }])).payload["records"][0]
    assert resolve_episode_agent_mapping(snake, OwnSubmissionBootstrap("101", "a" * 64)).agent_indices == (0,)


def test_leaderboard_table_and_missing_required_identifier_are_handled() -> None:
    table = "rank  team  privateScore\n1     ours  \n2     other  0.5\n"
    payload = normalize_live_payload("leaderboard", table.encode()).payload
    assert payload["input_format"] == "table"
    assert len(payload["records"]) == 2
    headed = normalize_live_payload("leaderboard", b"Leaderboard = example\n[{\"rank\": 1, \"privateScore\": null}]").payload
    assert headed["input_format"] == "json"
    with pytest.raises(LivePayloadError, match="lacks an identifier"):
        normalize_live_payload("own_submission_listing", _body([{"title": "missing"}]))


def test_replay_parser_requires_progression_and_keeps_only_schema_summary() -> None:
    with pytest.raises(LivePayloadError, match="progression"):
        normalize_live_payload("replay", _body({"info": {"TeamIds": ["42", "7"]}}))
    payload = normalize_live_payload("replay", _body({"info": {"TeamIds": ["42", "7"]}, "events": []})).payload
    assert payload["progression_field"] == "events"


def test_identity_uses_id_before_name_and_resolves_only_own_agent() -> None:
    identity = TeamIdentity(team_id="42", team_name="ours")
    replay = {"info": {"TeamIds": ["42", "7"], "TeamNames": ["ours", "other"], "Agents": [{"TeamId": "42"}, {"TeamId": "7"}]}}
    assert classify_replay_participants(replay, identity).reason == "exact_team_id_match"
    assert resolve_own_agent_indices(replay, identity).agent_indices == (0,)
    assert classify_replay_participants({"info": {"TeamNames": ["ours", "ours"]}}, TeamIdentity(team_name="ours")).source_kind is None
    assert resolve_own_agent_indices({"info": {"TeamIds": ["7", "8"]}}, identity).agent_indices == ()


def test_bootstrap_requires_authenticated_listing_and_explicit_replay_mapping() -> None:
    bootstrap = OwnSubmissionBootstrap("submission-1", "a" * 64)
    replay = {
        "info": {
            "SubmissionIds": ["submission-1", "submission-2"],
            "TeamIds": ["42", "7"], "TeamNames": ["ours", "other"],
        }, "events": [],
    }
    result = bootstrap_identity_from_replay(replay, bootstrap)
    assert result.reason == "replay_submission_side_verified"
    assert result.identity == TeamIdentity(team_id="42", team_name="ours")
    assert result.agent_indices == (0,)
    rejected = bootstrap_identity_from_replay({"info": {"TeamNames": ["ours", "other"]}, "events": []}, bootstrap)
    assert rejected.identity is None and rejected.reason == "replay_submission_mapping_missing"
    with pytest.raises(ValueError):
        OwnSubmissionBootstrap("submission-1", "a" * 64, authenticated_own_listing=False)


def test_live_chain_requests_only_own_episode_replay_and_own_log(tmp_path: Path) -> None:
    def response(action: str, body: object) -> ExternalRawResponse:
        return ExternalRawResponse(action=action, target="fixture", success=True, body=_body(body), content_type="application/json")

    transport = FixtureTransport({
        "own_submission_listing": response("own_submission_listing", [{"id": "submission-1", "teamId": "42"}]),
        "own_episode_listing": response("own_episode_listing", [{"episodeId": "episode-1", "agents": [{"submissionId": "submission-1", "index": 0, "teamId": "42"}, {"submissionId": "submission-2", "index": 1, "teamId": "7"}]}]),
        "replay": response("replay", {"info": {"TeamIds": ["42", "7"], "Agents": [{"TeamId": "42"}, {"TeamId": "7"}]}, "events": []}),
        "own_logs": response("own_logs", "owner-only log"),
    })
    manifest = run_live_acquisition(
        run_root=tmp_path / "run", config=LiveAcquisitionConfig(competition="ptcg"), transport=transport,
        rules_attestation=RulesAttestation(competition="ptcg"), team_id="42",
    )
    assert (manifest["submission_count"], manifest["episode_count"], manifest["replay_count"], manifest["log_count"]) == (1, 1, 1, 1)
    assert all(item.get("action") != "leaderboard" for item in manifest["outcomes"])
    log = next(item for item in manifest["outcomes"] if item.get("action") == "own_logs")
    assert log["status"] == "ARCHIVED"
    assert manifest["public_other_collection_enabled"] is False


def test_live_chain_reuses_submission_probe_response(tmp_path: Path) -> None:
    def response(action: str, body: object) -> ExternalRawResponse:
        return ExternalRawResponse(action=action, target="fixture", success=True, body=_body(body), content_type="application/json")

    base = FixtureTransport({"own_submission_listing": response("own_submission_listing", [])})
    calls: list[str] = []

    class CountingTransport:
        def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse:
            calls.append(action)
            return base.call(action, target=target, timeout=timeout)

    manifest = run_live_acquisition(
        run_root=tmp_path / "run", config=LiveAcquisitionConfig(competition="ptcg"),
        transport=CountingTransport(), team_id="42",
    )
    assert manifest["submission_count"] == 0
    assert calls == ["own_submission_listing"]


def test_live_chain_bootstraps_identity_after_own_episode_listing(tmp_path: Path) -> None:
    def response(action: str, body: object) -> ExternalRawResponse:
        return ExternalRawResponse(action=action, target="fixture", success=True, body=_body(body), content_type="application/json")

    transport = FixtureTransport({
        "own_submission_listing": response("own_submission_listing", [{"id": "submission-1"}]),
        "own_episode_listing": response("own_episode_listing", [{"id": "episode-1", "agents": [{"submissionId": "submission-1", "index": 0, "teamName": "ours"}, {"submissionId": "submission-2", "index": 1, "teamName": "other"}]}]),
        "replay": response("replay", {"info": {"TeamNames": ["ours", "other"]}, "events": []}),
        "own_logs": response("own_logs", "owner-only log"),
    })
    manifest = run_live_acquisition(
        run_root=tmp_path / "run", config=LiveAcquisitionConfig(competition="ptcg"), transport=transport,
    )
    assert manifest["identity_status"] == "RESOLVED"
    assert (manifest["submission_count"], manifest["episode_count"], manifest["replay_count"]) == (1, 1, 1)
    cache = json.loads((tmp_path / "run" / "state" / "o4_identity_cache.json").read_text())
    assert cache["schema_version"] == "o4-identity-cache-v2"
    assert cache["agent_index"] == 0
    assert "team" not in json.dumps(cache).lower()


def test_episode_mapping_missing_stops_before_replay_and_preserves_attempt_budget(tmp_path: Path) -> None:
    def response(action: str, body: object) -> ExternalRawResponse:
        return ExternalRawResponse(action=action, target="fixture", success=True, body=_body(body), content_type="application/json")

    transport = FixtureTransport({
        "own_submission_listing": response("own_submission_listing", [{"id": "submission-1"}]),
        "own_episode_listing": response("own_episode_listing", [{"id": "episode-1"}, {"id": "episode-2"}]),
    })
    manifest = run_live_acquisition(
        run_root=tmp_path / "run", config=LiveAcquisitionConfig(competition="ptcg", maximum_replays=1), transport=transport,
    )
    assert sum(item.get("action") == "replay" for item in manifest["outcomes"]) == 0
    assert manifest["episode_mapping_quarantined_count"] == 2
    assert {item.get("detail") for item in manifest["outcomes"] if item.get("action") == "episode_agent_mapping"} == {"episode_agent_mapping_missing"}


def test_identity_cache_schema_or_competition_mismatch_is_marked_for_reverification(tmp_path: Path) -> None:
    cache_dir = tmp_path / "run" / "state"
    cache_dir.mkdir(parents=True)
    (cache_dir / "o4_identity_cache.json").write_text(json.dumps({"schema_version": "wrong", "competition": "other"}), encoding="utf-8")
    transport = FixtureTransport({"own_submission_listing": ExternalRawResponse(
        action="own_submission_listing", target="fixture", success=True,
        body=_body([]), content_type="application/json",
    )})
    manifest = run_live_acquisition(
        run_root=tmp_path / "run", config=LiveAcquisitionConfig(competition="ptcg"), transport=transport,
    )
    assert manifest["identity_cache_status"] == "INVALID_REVERIFY"
