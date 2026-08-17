"""Offline unit tests for C2b classification and structured probe reports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition.probe import (
    CompetitionMode,
    OrderedOfficialTransport,
    ProbeRunner,
    RawResponse,
    classify_mode,
)


class FakeTransport:
    def __init__(self, responses: dict[str, RawResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def probe(self, action: str, competition: str, timeout: float) -> RawResponse:
        self.calls.append(action)
        return self.responses[action]


def response(action: str, *, body: object | None = None, success: bool = True, **kwargs: object) -> RawResponse:
    raw = b"" if body is None else json.dumps(body).encode()
    return RawResponse(
        action=action,
        requested_capability=action,
        official_action=f"fake.{action}",
        success=success,
        body=raw,
        content_type="application/json" if raw else None,
        client_name="fake",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            dict(replay_retrieved=True, replay_has_progression=True, legal_option_fields=["$.steps[].legalOptions"], schema_fingerprint="a" * 64, public_artifacts_retrieved=True),
            CompetitionMode.FULL_REPLAY,
        ),
        (
            dict(replay_retrieved=True, replay_has_progression=True, legal_option_fields=[], schema_fingerprint="a" * 64, public_artifacts_retrieved=True),
            CompetitionMode.REPLAY_WITHOUT_LEGAL_OPTIONS,
        ),
        (
            dict(replay_retrieved=False, replay_has_progression=False, legal_option_fields=[], schema_fingerprint=None, public_artifacts_retrieved=True),
            CompetitionMode.PUBLIC_ARTIFACTS_ONLY,
        ),
        (
            dict(replay_retrieved=False, replay_has_progression=False, legal_option_fields=[], schema_fingerprint=None, public_artifacts_retrieved=False),
            CompetitionMode.LOCAL_ONLY,
        ),
        # Bytes alone must not overclaim replay capability.
        (
            dict(replay_retrieved=True, replay_has_progression=False, legal_option_fields=["$.options"], schema_fingerprint="a" * 64, public_artifacts_retrieved=True),
            CompetitionMode.PUBLIC_ARTIFACTS_ONLY,
        ),
    ],
)
def test_classification_is_fail_closed(kwargs: dict[str, object], expected: CompetitionMode) -> None:
    assert classify_mode(**kwargs)[0] == expected


def test_full_replay_probe_is_structured_and_archived(tmp_path: Path) -> None:
    payload = {"steps": [{"state": {"turn": 1}, "select": {"options": [{"id": 1}]}}]}
    transport = FakeTransport(
        {
            "metadata": response("metadata", body={"title": "competition"}),
            "public_files": response("public_files", body=[]),
            "leaderboard": response("leaderboard", body=[]),
            "submissions": response("submissions", body=[]),
            "replay": response("replay", body=payload),
        }
    )
    report = ProbeRunner(transport).run(
        competition="pokemon-tcg-ai-battle", output_dir=tmp_path, probe_id_prefix="full"
    )
    assert report["classified_mode"] == "FULL_REPLAY"
    assert transport.calls == ["metadata", "public_files", "leaderboard", "submissions", "replay"]
    replay = report["actions"][-1]
    assert replay["detected_legal_option_fields"] == ["$.steps[].select.options"]
    assert replay["schema_fingerprint"]
    assert (tmp_path / "full-04-replay" / "manifest.json").is_file()
    report_summary = json.loads((tmp_path / "full-report" / "summary.json").read_text())
    assert report_summary["classified_mode"] == "FULL_REPLAY"


@pytest.mark.parametrize(
    ("body", "content_type", "error"),
    [
        (b"{not json", "application/json", "malformed_json"),
        (b"<html>denied</html>", "text/html", "non_json_response"),
        (b"", "application/json", "zero_byte_response"),
    ],
)
def test_non_replay_response_failures_are_preserved(tmp_path: Path, body: bytes, content_type: str, error: str) -> None:
    failing = RawResponse(
        action="metadata",
        requested_capability="competition_metadata",
        official_action="fake.metadata",
        success=False,
        body=body,
        content_type=content_type,
        error_type="permission_denied",
        error_message="denied",
        client_name="fake",
    )
    transport = FakeTransport({"metadata": failing})
    report = ProbeRunner(transport).run(
        competition="pokemon-tcg-ai-battle", output_dir=tmp_path, metadata_only=True, probe_id_prefix="failure"
    )
    assert report["classified_mode"] == "LOCAL_ONLY"
    assert report["actions"][0]["parse_failure"] == error
    assert (tmp_path / "failure-00-metadata" / "errors" / "error.json").is_file()


def test_offline_probe_never_calls_transport(tmp_path: Path) -> None:
    transport = FakeTransport({})
    report = ProbeRunner(transport).run(
        competition="pokemon-tcg-ai-battle", output_dir=tmp_path, offline=True, probe_id_prefix="offline"
    )
    assert report["classified_mode"] == "LOCAL_ONLY"
    assert transport.calls == []
    assert all(item["error_type"] == "offline" for item in report["actions"])


@pytest.mark.parametrize("error_type", ["authentication_failure", "permission_denied", "rate_limited", "timeout"])
def test_structured_remote_errors_do_not_crash_or_overclaim_mode(tmp_path: Path, error_type: str) -> None:
    transport = FakeTransport(
        {
            "metadata": RawResponse(
                action="metadata",
                requested_capability="competition_metadata",
                official_action="fake.metadata",
                success=False,
                error_type=error_type,
                error_message="Bearer leaked-value@example.com",
                retryable=error_type in {"rate_limited", "timeout"},
                client_name="fake",
            )
        }
    )
    report = ProbeRunner(transport).run(
        competition="pokemon-tcg-ai-battle", output_dir=tmp_path, metadata_only=True, probe_id_prefix=error_type
    )
    entry = report["actions"][0]
    assert report["classified_mode"] == "LOCAL_ONLY"
    assert entry["error_type"] == error_type
    assert "leaked-value" not in entry["sanitized_error_message"]
    assert (tmp_path / f"{error_type}-00-metadata" / "errors" / "error.json").is_file()


def test_mode_and_fingerprint_are_deterministic_for_identical_fixture(tmp_path: Path) -> None:
    payload = {"steps": [{"select": {"options": [{"kind": "attack"}]}, "state": {"turn": 2}}]}
    responses = {action: response(action, body=payload if action == "replay" else {}) for action, _ in [
        ("metadata", ""), ("public_files", ""), ("leaderboard", ""), ("submissions", ""), ("replay", "")
    ]}
    first = ProbeRunner(FakeTransport(responses)).run(
        competition="pokemon-tcg-ai-battle", output_dir=tmp_path / "one", probe_id_prefix="same"
    )
    second = ProbeRunner(FakeTransport(responses)).run(
        competition="pokemon-tcg-ai-battle", output_dir=tmp_path / "two", probe_id_prefix="same"
    )
    assert first["classified_mode"] == second["classified_mode"]
    assert first["actions"][-1]["schema_fingerprint"] == second["actions"][-1]["schema_fingerprint"]


def test_removed_cli_metadata_action_falls_back_to_official_python_client() -> None:
    transport = OrderedOfficialTransport()

    class RemovedCli:
        def probe(self, action: str, competition: str, timeout: float) -> RawResponse:
            return RawResponse(
                action=action,
                requested_capability="competition_metadata",
                official_action="kaggle_cli",
                success=False,
                error_type="official_action_unavailable",
            )

    class PythonClient:
        def probe(self, action: str, competition: str, timeout: float) -> RawResponse:
            return response(action, body={"ref": competition})

    transport.cli = RemovedCli()
    transport.python = PythonClient()

    result = transport.probe("metadata", "pokemon-tcg-ai-battle", 1)

    assert result.success
    assert json.loads(result.body)["ref"] == "pokemon-tcg-ai-battle"
