"""O3 typed CLI wiring and replay ownership governance contracts."""

from __future__ import annotations

import subprocess

import pytest

from mage_ptcg.competition_intelligence.contracts import SourceKind
from mage_ptcg.competition_intelligence.external_transport import (
    FAILURE_UNAVAILABLE,
    ExternalRequest,
    SubprocessKaggleTransport,
    TransportError,
)
from mage_ptcg.competition_intelligence.participant_resolver import TeamIdentity, classify_replay_participants
from mage_ptcg.competition_intelligence.rules_attestation import RulesAttestation
from mage_ptcg.competition_intelligence.run_live_acquisition import LiveAcquisitionConfig, run_live_acquisition
from mage_ptcg.competition_intelligence.external_transport import ExternalRawResponse, FixtureTransport


def _capture_cli(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    # Isolate executable discovery from the developer machine.  The live
    # transport intentionally prefers a project-local ``.venv/bin/kaggle``;
    # patching only ``shutil.which`` therefore makes this contract test depend
    # on whether that optional file happens to exist in the checkout.
    monkeypatch.setattr(SubprocessKaggleTransport, "_executable", staticmethod(lambda: "/bin/kaggle"))
    monkeypatch.setattr("mage_ptcg.competition_intelligence.external_transport._cli_version", lambda _path: "Kaggle CLI 2.2.3")

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"[]", b"")

    monkeypatch.setattr("mage_ptcg.competition_intelligence.external_transport.subprocess.run", fake_run)
    return calls


@pytest.mark.parametrize(
    ("external_request", "expected"),
    [
        (ExternalRequest("own_submission_listing", competition="ptcg"), ["competitions", "submissions", "ptcg"]),
        (ExternalRequest("leaderboard", competition="ptcg"), ["competitions", "leaderboard", "--show", "ptcg"]),
        (ExternalRequest("public_artifacts", competition="ptcg"), ["competitions", "files", "ptcg"]),
        (ExternalRequest("own_episode_listing", submission_id="sub-1"), ["competitions", "episodes", "sub-1"]),
        (ExternalRequest("team_submission_listing", team_id="team-1"), ["competitions", "team-submissions", "team-1"]),
    ],
)
def test_typed_read_only_cli_actions_are_wired(monkeypatch: pytest.MonkeyPatch, external_request: ExternalRequest, expected: list[str]) -> None:
    calls = _capture_cli(monkeypatch)
    response = SubprocessKaggleTransport(max_retries=0).call_request(external_request, timeout=1)
    assert response.success
    assert calls == [["/bin/kaggle", *expected, "--format", "json", "--quiet"]]
    assert all("view" not in argument for argument in calls[0])


def test_logs_require_two_typed_arguments_and_never_use_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_cli(monkeypatch)
    with pytest.raises(TransportError):
        ExternalRequest("own_logs", episode_id="episode-only")
    request = ExternalRequest("own_logs", episode_id="episode;$(bad)", agent_index=1)
    # The mocked command has no download output, so it safely fails schema
    # validation after preserving each untrusted value as one argv item.
    response = SubprocessKaggleTransport(max_retries=0).call_request(request, timeout=1)
    assert not response.success
    assert calls[0][:6] == ["/bin/kaggle", "competitions", "logs", "episode;$(bad)", "1", "--quiet"]


def test_subprocess_inherits_auth_environment_without_logging_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAGGLE_CONFIG_DIR", "/tmp/o4-config")
    captured: dict[str, object] = {}
    monkeypatch.setattr(SubprocessKaggleTransport, "_executable", staticmethod(lambda: "/bin/kaggle"))
    monkeypatch.setattr("mage_ptcg.competition_intelligence.external_transport._cli_version", lambda _path: "Kaggle CLI 2.2.3")

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b"[]", b"")

    monkeypatch.setattr("mage_ptcg.competition_intelligence.external_transport.subprocess.run", fake_run)
    response = SubprocessKaggleTransport(max_retries=0).call_request(
        ExternalRequest("own_submission_listing", competition="ptcg"), timeout=1
    )
    assert response.success
    assert captured["env"]["KAGGLE_CONFIG_DIR"] == "/tmp/o4-config"


def test_public_logs_and_removed_competitions_view_are_explicitly_unavailable() -> None:
    transport = SubprocessKaggleTransport(max_retries=0)
    public = transport.call_request(ExternalRequest("public_logs", episode_id="e"), timeout=1)
    metadata = transport.call_request(ExternalRequest("competition_metadata", competition="ptcg"), timeout=1)
    assert public.error_type == metadata.error_type == FAILURE_UNAVAILABLE
    assert public.error_message == "NOT_SUPPORTED_BY_PUBLIC_API"
    assert "view" not in (metadata.command or ())


def test_sdk_episode_route_is_opt_in_and_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = ExternalRawResponse(action="own_episode_listing", target="sub-1", success=True, body=b'{"episodes":[]}', client_name="kaggle-sdk")
    calls: list[tuple[str, float]] = []

    def fake_sdk(self, request, *, timeout):
        calls.append((request.action, timeout))
        return sentinel

    monkeypatch.setattr(SubprocessKaggleTransport, "_call_sdk_episode_listing", fake_sdk)
    transport = SubprocessKaggleTransport(sdk_episode_agents=True)
    response = transport.call_request(ExternalRequest("own_episode_listing", submission_id="sub-1"), timeout=3)
    assert response is sentinel
    assert calls == [("own_episode_listing", 3)]


def test_participant_resolver_is_exact_and_fail_closed() -> None:
    identity = TeamIdentity(team_id="42", team_name="ours")
    own = classify_replay_participants({"info": {"TeamIds": ["42", "7"]}}, identity)
    other = classify_replay_participants({"info": {"TeamNames": ["them", "else"]}}, identity)
    ambiguous = classify_replay_participants({"info": {"TeamNames": ["ours", "ours"]}}, identity)
    assert own.source_kind is SourceKind.OWN_KAGGLE
    assert other.source_kind is SourceKind.PUBLIC_OTHER
    assert ambiguous.source_kind is None
    assert classify_replay_participants({"info": {}}, identity).source_kind is None


def test_unverified_rules_attestation_never_expands_public_other_access() -> None:
    assert not RulesAttestation(competition="ptcg").permits_public_other_collection()
    assert RulesAttestation(
        competition="ptcg", status="VERIFIED_RULES_CONSTRAINT", verified_at="2026-07-20T00:00:00Z",
        verified_by="operator", reference="official-rules",
    ).permits_public_other_collection()


def test_live_runner_archives_owned_replay_and_keeps_public_other_unscheduled(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("O3_TEAM_ID", "42")
    def response(action: str, body: object) -> ExternalRawResponse:
        return ExternalRawResponse(action=action, target="fixture", success=True, body=__import__("json").dumps(body).encode(), content_type="application/json")
    transport = FixtureTransport({
        "own_submission_listing": response("own_submission_listing", [{"id": "submission-1"}]),
        "leaderboard": response("leaderboard", []),
        "public_artifacts": response("public_artifacts", []),
        "own_episode_listing": response("own_episode_listing", [{"id": "episode-1", "agents": [{"submissionId": "submission-1", "index": 0, "teamId": "42"}, {"submissionId": "submission-2", "index": 1, "teamId": "7"}]}]),
        "replay": response("replay", {"info": {"TeamIds": ["42", "7"]}, "events": []}),
    })
    manifest = run_live_acquisition(
        run_root=tmp_path / "run", config=LiveAcquisitionConfig(competition="ptcg"), transport=transport,
        rules_attestation=RulesAttestation(competition="ptcg"),
    )
    assert manifest["replay_count"] == 1
    assert manifest["public_other_status"] == "RULES_UNVERIFIED_ARCHIVE_ONLY"
    assert any(item["action"] == "replay" and item["status"] == "ARCHIVED" for item in manifest["outcomes"])
