"""O1-5 external capability, schema, and secure Team Bundle regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from mage_ptcg.competition_intelligence.contracts import AllowedUse, SourceKind
from mage_ptcg.competition_intelligence.external_acquisition import acquire_external_artifact
from mage_ptcg.competition_intelligence.external_capability import probe_capability
from mage_ptcg.competition.probe import detect_authentication
from mage_ptcg.competition_intelligence.external_schema import (
    COMPATIBLE,
    COMPATIBLE_WITH_ADDITIONS,
    INCOMPATIBLE,
    UNKNOWN_SCHEMA,
    build_schema_drift_report,
)
from mage_ptcg.competition_intelligence.external_transport import (
    FAILURE_AUTHENTICATION,
    FAILURE_TIMEOUT,
    ExternalRawResponse,
    FixtureTransport,
    UnavailableTransport,
    _classify_cli_failure,
)
from mage_ptcg.competition_intelligence.pipeline import run_ingest_public, run_probe_external, run_schema_report
from mage_ptcg.competition_intelligence.team_bundle import (
    STATUS_ALREADY_IMPORTED,
    STATUS_ARCHIVED,
    STATUS_QUARANTINED,
    TEAM_BUNDLE_SCHEMA_VERSION,
    import_team_bundle,
)


def _response(action: str = "public_artifacts", body: object | None = None, *, client_name: str = "fixture") -> ExternalRawResponse:
    return ExternalRawResponse(
        action=action, target="fixture", success=True,
        body=json.dumps({"items": []} if body is None else body, sort_keys=True).encode(),
        content_type="application/json", client_name=client_name,
    )


def _report(transport: FixtureTransport, *, target: str = "competition"):
    return probe_capability(transport, target=target, tested_at="2026-07-18T00:00:00Z")


class TestExternalCapability:
    def test_unavailable_is_not_a_success(self, tmp_path: Path) -> None:
        report = probe_capability(UnavailableTransport(), target="competition", tested_at="2026-07-18T00:00:00Z")
        assert report.capability_mode.value == "UNAVAILABLE"
        outcome = acquire_external_artifact(
            tmp_path / "run", UnavailableTransport(), action="public_artifacts", target="competition",
            capability_report=report, source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=["ARCHIVE"],
        )
        assert outcome.status == "UNAVAILABLE"

    def test_partial_capability_never_becomes_full_replay(self) -> None:
        transport = FixtureTransport({"replay": _response("replay", {"events": [{"turn": 1}]})})
        report = _report(transport)
        assert report.capability_mode.value == "REPLAY_WITHOUT_LEGAL_OPTIONS"

    def test_cli_error_taxonomy_and_redaction(self) -> None:
        assert _classify_cli_failure(1, "401 bearer secret") == FAILURE_AUTHENTICATION
        assert _classify_cli_failure(1, "403 forbidden") == "permission_denied"
        assert _classify_cli_failure(1, "Name or service not known") == "network_error"
        assert _classify_cli_failure(1, "Too many requests") == "rate_limited"
        assert FAILURE_TIMEOUT == "timeout"

    def test_oauth_and_legacy_credential_shapes_are_source_hints_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
        monkeypatch.delenv("KAGGLE_KEY", raising=False)
        monkeypatch.setenv("KAGGLE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "credentials.json").write_text('{"access_token":"redacted"}', encoding="utf-8")
        assert detect_authentication() == (True, "oauth_credentials_file")
        (tmp_path / "credentials.json").unlink()
        (tmp_path / "kaggle.json").write_text('{"username":"u","key":"k"}', encoding="utf-8")
        assert detect_authentication() == (True, "legacy_config_file")

    def test_capability_probe_success_is_live_auth_proof(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
        monkeypatch.delenv("KAGGLE_KEY", raising=False)
        monkeypatch.setenv("KAGGLE_CONFIG_DIR", "/nonexistent/o4-auth-test")
        response = _response("own_submission_listing", [{"ref": "s"}], client_name="kaggle-cli")
        report = probe_capability(FixtureTransport({"own_submission_listing": response}), target="competition")
        # Fixture transport remains deterministic, while live transports use
        # the successful own-listing response rather than static file presence.
        assert report.own_submission_listing_available is True
        assert report.authentication_available is True
        assert report.authentication_source_type == "capability_probe"

    def test_fixture_probe_and_cli_output_are_deterministic_and_safe(self, tmp_path: Path) -> None:
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps({"public_artifacts": {"success": True, "body": {"items": []}}}), encoding="utf-8")
        first = run_probe_external(tmp_path / "a", target="competition", mode="fixture", fixture_path=fixture,
                                   tested_at="2026-07-18T00:00:00Z")
        second = run_probe_external(tmp_path / "b", target="competition", mode="fixture", fixture_path=fixture,
                                    tested_at="2026-07-18T00:00:00Z")
        assert first["capability_report_id"] == second["capability_report_id"]
        assert "secret" not in json.dumps(first).lower()
        assert first["capability_detail"]["structured_request_requirement"].startswith("replay requires episode_id")


class TestExternalSchemaGate:
    def test_schema_drift_classification(self) -> None:
        baseline = {"items": [{"id": "x", "score": 1}]}
        assert build_schema_drift_report(source_kind="OWN_KAGGLE", baseline_value=baseline, candidate_value=baseline).compatibility == COMPATIBLE
        assert build_schema_drift_report(source_kind="OWN_KAGGLE", baseline_value=baseline, candidate_value={"items": [{"id": "x", "score": 1, "extra": True}]}).compatibility == COMPATIBLE_WITH_ADDITIONS
        assert build_schema_drift_report(source_kind="OWN_KAGGLE", baseline_value=baseline, candidate_value={"items": [{"id": 1, "score": 1}]}).compatibility == INCOMPATIBLE
        assert build_schema_drift_report(source_kind="OWN_KAGGLE", baseline_value=None, candidate_value=baseline).compatibility == UNKNOWN_SCHEMA

    def test_untrusted_first_response_is_quarantined(self, tmp_path: Path) -> None:
        response = _response()
        # This small transport deliberately does not mark its response as a
        # fixture baseline; live-like TOFU must remain denied.
        class UntrustedTransport:
            def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse:
                return response
        report = _report(FixtureTransport({"public_artifacts": response}))
        outcome = acquire_external_artifact(
            tmp_path / "run", UntrustedTransport(), action="public_artifacts", target="competition",
            capability_report=report, source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=["ARCHIVE"],
        )
        assert outcome.status == "QUARANTINED"
        assert outcome.schema_compatibility == UNKNOWN_SCHEMA

    def test_fixture_baseline_is_value_free_and_reused(self, tmp_path: Path) -> None:
        transport = FixtureTransport({"public_artifacts": _response(body={"items": [{"id": "x"}]})})
        report = _report(transport)
        first = acquire_external_artifact(
            tmp_path / "run", transport, action="public_artifacts", target="competition", capability_report=report,
            source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=["ARCHIVE"],
        )
        second = acquire_external_artifact(
            tmp_path / "run", transport, action="public_artifacts", target="competition", capability_report=report,
            source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=["ARCHIVE"],
        )
        assert first.status == second.status == "ARCHIVED"
        assert first.manifest_path == second.manifest_path
        schema = run_schema_report(tmp_path / "run", source_kind="PUBLIC_OTHER", action="public_artifacts")
        assert schema["baseline_trust"] == "test_fixture"
        baseline_text = next((tmp_path / "run" / "state" / "external_schema_baselines").glob("*.json")).read_text()
        assert '"x"' not in baseline_text

    def test_public_other_training_and_redistribution_are_hard_denied(self, tmp_path: Path) -> None:
        fixture = tmp_path / "fixture.json"
        fixture.write_text(json.dumps({"public_artifacts": {"success": True, "body": {"items": []}}}), encoding="utf-8")
        with pytest.raises(Exception):
            run_ingest_public(tmp_path / "run", action="public_artifacts", target="competition",
                              allowed_uses=[AllowedUse.TRAINING.value], mode="fixture", fixture_path=fixture)
        with pytest.raises(Exception):
            run_ingest_public(tmp_path / "run-2", action="public_artifacts", target="competition",
                              allowed_uses=[AllowedUse.REDISTRIBUTION.value], mode="fixture", fixture_path=fixture)


def _bundle(root: Path, *, permission: bool = True, files: list[dict[str, object]] | None = None) -> Path:
    root.mkdir()
    data = root / "artifact.json"
    data.write_text('{"ok":true}', encoding="utf-8")
    digest = __import__("hashlib").sha256(data.read_bytes()).hexdigest()
    manifest: dict[str, object] = {
        "bundle_version": TEAM_BUNDLE_SCHEMA_VERSION, "source_kind": "TEAM_SHARED", "owner": "team-a",
        "allowed_uses": ["ARCHIVE", "ANALYSIS"], "files": files or [{"path": "artifact.json", "sha256": digest}],
    }
    if permission:
        manifest["permission_statement"] = "analysis permitted"
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


class TestTeamBundle:
    def test_valid_idempotent_and_default_deny(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path / "bundle")
        first = import_team_bundle(bundle, tmp_path / "run", created_at="2026-07-18T00:00:00Z")
        second = import_team_bundle(bundle, tmp_path / "run", created_at="2026-07-18T00:00:00Z")
        assert first.status == STATUS_ARCHIVED
        assert second.status == STATUS_ALREADY_IMPORTED
        assert not Path(first.manifest_path or "").is_absolute()
        no_permission = import_team_bundle(
            _bundle(tmp_path / "no-permission", permission=False), tmp_path / "run-2", created_at="2026-07-18T00:00:00Z",
        )
        assert no_permission.allowed_uses == ("ARCHIVE",)

    @pytest.mark.parametrize("mutator", ["traversal", "hash", "duplicate", "symlink", "bad-owner", "bad-use"])
    def test_malicious_manifests_are_quarantined_without_absolute_path(self, tmp_path: Path, mutator: str) -> None:
        bundle = _bundle(tmp_path / f"bundle-{mutator}")
        manifest_path = bundle / "manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text())
        if mutator == "traversal":
            manifest["files"][0]["path"] = "../escape"
        elif mutator == "hash":
            manifest["files"][0]["sha256"] = "0" * 64
        elif mutator == "duplicate":
            manifest["files"].append(dict(manifest["files"][0]))
        elif mutator == "symlink":
            outside = tmp_path / "outside"
            outside.write_text("x")
            (bundle / "linked").symlink_to(outside)
            manifest["files"][0]["path"] = "linked"
        elif mutator == "bad-owner":
            manifest["owner"] = ""
        else:
            manifest["allowed_uses"] = ["NOT_A_USE"]
        manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
        outcome = import_team_bundle(bundle, tmp_path / "run")
        assert outcome.status == STATUS_QUARANTINED
        assert str(bundle) not in outcome.detail
        reason = next((tmp_path / "run" / "quarantine").glob("*/*/reason.json")).read_text()
        assert str(bundle) not in reason

    def test_permission_escalation_is_quarantined(self, tmp_path: Path) -> None:
        outcome = import_team_bundle(_bundle(tmp_path / "bundle"), tmp_path / "run", cli_requested_uses=["TRAINING"])
        assert outcome.status == STATUS_QUARANTINED
