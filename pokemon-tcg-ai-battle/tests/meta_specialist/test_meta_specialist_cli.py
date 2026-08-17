"""Task 6: the local, network-free JSON CLI for the meta-specialist pipeline."""

from __future__ import annotations

import csv
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.knowledge.model import deck_identity_from_card_ids
from mage_ptcg.meta_specialist import cli
from mage_ptcg.meta_specialist.contracts import BUNDLE_SIZE_LIMIT_BYTES, ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, create_deck_lock, qualify_deck_asset
from mage_ptcg.meta_specialist.package import BundleSpec, DependencyContractIds, derive_entrypoint_contract_id, write_bundle_spec
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import CABT_AGENT_JSON_CONTRACT_SHA256_V1


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_show_runtime_constraints_matches_frozen_v1(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, err = _run(["show-runtime-constraints"], capsys)

    assert exit_code == 0
    assert err == ""
    assert json.loads(out) == RuntimeConstraintManifest.frozen_v1().to_payload()


def test_show_ladder_contract_success(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, err = _run(["show-ladder-contract", "--checked-at-utc", "2026-08-01T00:00:00Z"], capsys)

    assert exit_code == 0
    assert err == ""
    payload = json.loads(out)
    expected = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    expected["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", expected)
    assert payload == expected


def test_show_ladder_contract_rejects_non_utc_timestamp(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, err = _run(["show-ladder-contract", "--checked-at-utc", "2026-08-01T00:00:00"], capsys)

    assert exit_code == 2
    assert out == ""
    error = json.loads(err)
    assert error["status"] == "ERROR"
    assert error["error_type"] == "ARGUMENT_ERROR"


def test_missing_subcommand_is_an_argument_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code, out, err = _run([], capsys)

    assert exit_code == 2
    assert out == ""
    assert json.loads(err)["error_type"] == "ARGUMENT_ERROR"


def _write_known_card_ids_csv(path: Path, card_ids: range) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Card ID", "Name"])
        for card_id in card_ids:
            writer.writerow([card_id, f"fixture-card-{card_id}"])


def _write_qualify_deck_fixtures(tmp_path: Path, *, cards: tuple[int, ...]) -> dict[str, Path]:
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({
            "schema_version": "meta-specialist-archetypes-v1",
            "primary_order": ["cli-fixture-archetype"],
            "replacement_order": [],
            "archetypes": [{
                "runtime_id": "cli-fixture-archetype",
                "aliases": ["cli-fixture-alias"],
                "core_card_ids": [cards[0]],
                "candidate_status": "registered_unqualified",
            }],
        }),
        encoding="utf-8",
    )

    known_ids_path = tmp_path / "known_card_ids.csv"
    _write_known_card_ids_csv(known_ids_path, range(1, 2000))

    asset_json_path = tmp_path / "asset.json"
    asset_json_path.write_text(
        json.dumps({
            "asset_id": "cli-fixture-asset",
            "archetype_id": "cli-fixture-archetype",
            "deck_path": str(deck_path),
            "source_ref": "https://example.invalid/decks/cli-fixture.csv",
            "source_commit": "a" * 40,
            "asset_class": "deck_only",
            "usage_boundary": "bundle_allowed",
            "policy_compatibility": "specialist-v2",
            "card_database_version": "cli-fixture-v1",
        }),
        encoding="utf-8",
    )

    deck_identity = deck_identity_from_card_ids(list(cards))
    deck_file_sha256 = hashlib.sha256(deck_path.read_bytes()).hexdigest()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps({
            "schema_version": "meta-specialist-cabt-deck-evidence-v1",
            "passed": True,
            "deck_identity": deck_identity,
            "deck_file_sha256": deck_file_sha256,
            "card_database_version": "cli-fixture-v1",
            "cabt_runtime_version": RuntimeConstraintManifest.frozen_v1().verifier_dependency,
            "evidence": "cli-fixture placeholder evidence: not a real CABT measurement",
        }),
        encoding="utf-8",
    )

    return {
        "deck": deck_path, "registry": registry_path, "known_ids": known_ids_path,
        "asset_json": asset_json_path, "evidence": evidence_path,
    }


def test_qualify_deck_then_lock_deck_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cards = tuple(range(1, 61))
    fixtures = _write_qualify_deck_fixtures(tmp_path, cards=cards)

    exit_code, out, err = _run([
        "qualify-deck",
        "--asset-json", str(fixtures["asset_json"]),
        "--registry", str(fixtures["registry"]),
        "--known-card-ids", str(fixtures["known_ids"]),
        "--cabt-evidence-json", str(fixtures["evidence"]),
    ], capsys)

    assert exit_code == 0
    assert err == ""
    qualified = json.loads(out)
    assert qualified["status"] == "QUALIFIED"
    assert qualified["cabt_legality_status"] == "passed"
    assert qualified["card_ids"] == list(cards)
    assert qualified["archetype_id"] == "cli-fixture-archetype"
    deck_identity = qualified["deck_identity"]

    exit_code, out, err = _run([
        "lock-deck",
        "--archetype-id", "cli-fixture-archetype",
        "--selected-deck-identity", deck_identity,
        "--compared-deck-identities", deck_identity,
        "--foundation-init-id", "b" * 64,
        "--joint-race-schedule-id", "c" * 64,
        "--equal-transition-budget", "1",
    ], capsys)

    assert exit_code == 0
    assert err == ""
    lock = json.loads(out)
    assert lock["status"] == "LOCKED"
    assert lock["selected_deck_identity"] == deck_identity
    assert len(lock["deck_lock_id"]) == 64


def test_qualify_deck_resolves_archetype_alias(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cards = tuple(range(1, 61))
    fixtures = _write_qualify_deck_fixtures(tmp_path, cards=cards)
    asset_document = json.loads(fixtures["asset_json"].read_text(encoding="utf-8"))
    asset_document["archetype_id"] = "cli-fixture-alias"
    fixtures["asset_json"].write_text(json.dumps(asset_document), encoding="utf-8")

    exit_code, out, _err = _run([
        "qualify-deck",
        "--asset-json", str(fixtures["asset_json"]),
        "--registry", str(fixtures["registry"]),
        "--known-card-ids", str(fixtures["known_ids"]),
        "--cabt-evidence-json", str(fixtures["evidence"]),
    ], capsys)

    assert exit_code == 0
    assert json.loads(out)["archetype_id"] == "cli-fixture-archetype"


def test_qualify_deck_rejects_evidence_bound_to_a_different_deck(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """The hard rule under test: CABT evidence must be deck-bound, or qualification fails closed."""
    cards = tuple(range(1, 61))
    fixtures = _write_qualify_deck_fixtures(tmp_path, cards=cards)
    evidence_document = json.loads(fixtures["evidence"].read_text(encoding="utf-8"))
    evidence_document["deck_identity"] = "deck-0000000000000000000"
    fixtures["evidence"].write_text(json.dumps(evidence_document), encoding="utf-8")

    exit_code, out, err = _run([
        "qualify-deck",
        "--asset-json", str(fixtures["asset_json"]),
        "--registry", str(fixtures["registry"]),
        "--known-card-ids", str(fixtures["known_ids"]),
        "--cabt-evidence-json", str(fixtures["evidence"]),
    ], capsys)

    assert exit_code == 2
    assert out == ""
    error = json.loads(err)
    assert error["error_type"] == "CONTRACT_ERROR"
    assert "deck_identity" in error["message"]


def test_qualify_deck_missing_deck_csv_raises_rather_than_fabricates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Required regression test: a missing deck.csv must fail closed, never substitute a default deck."""
    cards = tuple(range(1, 61))
    fixtures = _write_qualify_deck_fixtures(tmp_path, cards=cards)
    fixtures["deck"].unlink()

    exit_code, out, err = _run([
        "qualify-deck",
        "--asset-json", str(fixtures["asset_json"]),
        "--registry", str(fixtures["registry"]),
        "--known-card-ids", str(fixtures["known_ids"]),
        "--cabt-evidence-json", str(fixtures["evidence"]),
    ], capsys)

    assert exit_code == 2
    assert out == ""
    error = json.loads(err)
    assert error["status"] == "ERROR"
    assert error["error_type"] == "CONTRACT_ERROR"


def test_qualify_deck_short_deck_csv_raises_rather_than_fabricates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """A truncated (short, not exactly 60 cards) deck.csv must also fail closed."""
    cards = tuple(range(1, 61))
    fixtures = _write_qualify_deck_fixtures(tmp_path, cards=cards)
    fixtures["deck"].write_text("1\n2\n3\n", encoding="utf-8")

    exit_code, out, err = _run([
        "qualify-deck",
        "--asset-json", str(fixtures["asset_json"]),
        "--registry", str(fixtures["registry"]),
        "--known-card-ids", str(fixtures["known_ids"]),
        "--cabt-evidence-json", str(fixtures["evidence"]),
    ], capsys)

    assert exit_code == 2
    assert out == ""
    error = json.loads(err)
    assert error["error_type"] == "CONTRACT_ERROR"


def test_lock_deck_rejects_selected_identity_outside_compared_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, out, err = _run([
        "lock-deck",
        "--archetype-id", "cli-fixture-archetype",
        "--selected-deck-identity", "deck-1111111111111111111",
        "--compared-deck-identities", "deck-2222222222222222222",
        "--foundation-init-id", "b" * 64,
        "--joint-race-schedule-id", "c" * 64,
        "--equal-transition-budget", "1",
    ], capsys)

    assert exit_code == 2
    assert out == ""
    assert json.loads(err)["error_type"] == "CONTRACT_ERROR"


def _build_static_bundle_spec(source: Path, *, extra_members: dict[str, bytes] | None = None) -> BundleSpec:
    cards = tuple(range(1, 61))
    deck_path = source / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    asset = DeckAssetInput.from_path(
        asset_id="cli-build-fixture", archetype_id="cli-build-fixture", path=deck_path,
        source_ref="https://example.invalid/decks/cli-build-fixture.csv", source_commit="a" * 40,
        asset_class="deck_only", usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2", card_database_version="fixture-v1",
    )
    qualified = qualify_deck_asset(
        asset, ArchetypeSpec("cli-build-fixture", (), (cards[0],), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _: (True, "cli-build-fixture placeholder evidence"),
    )
    (source / "main.py").write_text("agent = lambda observation, configuration: []\n", encoding="utf-8")
    (source / "policy_loader.py").write_text("# structural fixture\n", encoding="utf-8")
    (source / "rule_policy_v1.py").write_text("# static policy fixture\n", encoding="utf-8")
    for name, payload in (extra_members or {}).items():
        (source / name).write_bytes(payload)

    constraints = RuntimeConstraintManifest.frozen_v1()
    ladder = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
    lock = create_deck_lock(
        archetype_id=qualified.archetype_id, selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,), foundation_init_id="b" * 64,
        joint_race_schedule_id="c" * 64, equal_transition_budget=1,
    )
    members = tuple(sorted(("deck.csv", "main.py", "policy_loader.py", "rule_policy_v1.py", *(extra_members or {}))))
    dependency_ids = DependencyContractIds(
        cabt_agent_json_contract_id=CABT_AGENT_JSON_CONTRACT_SHA256_V1,
        runtime_constraints_id=constraints.runtime_constraints_id,
        ladder_mechanics_id=ladder["ladder_mechanics_id"],
        entrypoint_contract_id=derive_entrypoint_contract_id(source, members, policy_members=("rule_policy_v1.py",)),
    )
    policy_bytes = (source / "rule_policy_v1.py").read_bytes()
    policy_identity = content_id("meta-specialist-static-policy-v1", [
        {"path": "rule_policy_v1.py", "sha256": hashlib.sha256(policy_bytes).hexdigest(), "size": len(policy_bytes)},
    ])
    return BundleSpec(
        source_root=source, members=members, deck_member="deck.csv",
        policy_entrypoint_member="policy_loader.py", qualified_deck_asset=qualified, deck_lock=lock,
        runtime_constraints=constraints, ladder_mechanics=ladder, dependency_contract_ids=dependency_ids,
        candidate_class="static_rule_bundle", policy_members=("rule_policy_v1.py",), model_member=None,
        policy_identity=policy_identity, checkpoint_lineage_id=None,
        checkpoint_lineage_reason="not_applicable_static_policy",
    )


def test_build_submission_then_verify_submission_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Required regression test: build -> verify round trip on a real temporary bundle via the CLI."""
    source = tmp_path / "source"
    source.mkdir()
    spec = _build_static_bundle_spec(source)
    spec_path = tmp_path / "bundle_spec.json"
    write_bundle_spec(spec, spec_path)
    archive_path = tmp_path / "submission.tar.gz"

    exit_code, out, err = _run(["build-submission", "--spec", str(spec_path), "--output", str(archive_path)], capsys)

    assert exit_code == 0
    assert err == ""
    build_report = json.loads(out)
    assert build_report["status"] == "structurally_verified"
    assert archive_path.is_file()

    exit_code, out, err = _run(["verify-submission", "--archive", str(archive_path)], capsys)

    assert exit_code == 0
    assert err == ""
    verify_report = json.loads(out)
    assert verify_report == build_report


def test_build_submission_enforces_the_official_archive_size_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Required regression test: the archive size limit is enforced, using the real imported constant."""
    source = tmp_path / "source"
    source.mkdir()
    # Comfortably exceed BUNDLE_SIZE_LIMIT_BYTES after gzip compression: random
    # bytes are close to incompressible, so a payload a few MiB above the
    # limit still yields a compressed archive over the limit.
    oversized_payload = os.urandom(BUNDLE_SIZE_LIMIT_BYTES + 8 * 1024 * 1024)
    spec = _build_static_bundle_spec(source, extra_members={"filler.bin": oversized_payload})
    spec_path = tmp_path / "bundle_spec.json"
    write_bundle_spec(spec, spec_path)
    archive_path = tmp_path / "oversized.tar.gz"

    exit_code, out, err = _run(["build-submission", "--spec", str(spec_path), "--output", str(archive_path)], capsys)

    assert exit_code == 2
    assert out == ""
    error = json.loads(err)
    assert error["error_type"] == "SECURITY_ERROR"
    assert "size" in error["message"].lower()
    assert not archive_path.exists()


def test_verify_submission_missing_archive_is_input_or_security_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, out, err = _run(["verify-submission", "--archive", str(tmp_path / "missing.tar.gz")], capsys)

    assert exit_code == 2
    assert out == ""
    assert json.loads(err)["error_type"] in {"INPUT_ERROR", "SECURITY_ERROR"}


# ---------------------------------------------------------------------------
# train-from-trajectories: default aggregated stdout summary vs. --json (same
# raw-JSON-to-terminal shape as collect-trajectories -- see
# test_collect_trajectories_cli.py's aggregated-summary tests).
# ---------------------------------------------------------------------------


def _fixture_full_train_payload_v1() -> dict[str, object]:
    return {
        "schema_version": "meta-specialist-train-from-trajectories-run-summary-v1",
        "run_name": "cli-train-summary-check",
        "collection_run_dir": "/fixture/collection_run_dir",
        "started_at_utc": "2026-08-03T00:00:00+00:00",
        "finished_at_utc": "2026-08-03T00:10:00+00:00",
        "wall_time_seconds": 600.0,
        "device": "cpu",
        "source_commit": "c" * 40,
        "training_identity": {"fixture": True},
        "recipe": {"fixture": True},
        "model_config": {"fixture": True},
        "output_root": "/fixture/training_output_root",
        "run_summary_path": "/fixture/training_output_root/run_summary.json",
        "progress_summary_path": "/fixture/training_output_root/progress_summary.json",
        "games_found": 1858,
        "games_unreadable": 0,
        "unreadable_game_records": [],
        "unreadable_game_records_truncated": False,
        "games_admitted": 1858,
        "games_dropped_stale": 0,
        "drop_reasons": [],
        "drop_reasons_truncated": False,
        "admitted_game_record_paths": [f"/fixture/games/{index}/record.json" for index in range(50)],
        "admitted_game_record_paths_truncated": True,
        "transitions_admitted_total": 33848,
        "trajectories_per_step": 1858,
        "resumed": False,
        "step_before": 0,
        "step_after": 100,
        "max_steps": 100,
        "steps_taken_this_run": 100,
        "steps_skipped_this_run": 0,
        "sampler_cursor": 0,
        "transitions_consumed_this_run": 185800,
        "scoring_failures_this_run": [],
        "scoring_failures_this_run_truncated": False,
        "loss_trajectory": [0.9 - 0.001 * index for index in range(100)],
        "gradient_norms": [0.5 for _ in range(100)],
        "step_metrics_truncated": False,
        "checkpoint_path": "/fixture/training_output_root/checkpoint-step-100.pt",
        "checkpoint_sha256": "d" * 64,
    }


def test_cli_train_from_trajectories_default_stdout_is_aggregated_not_raw_step_arrays(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Fails if the CLI ever dumps the full run-summary JSON -- including one
    entry per optimizer step in loss_trajectory/gradient_norms -- to stdout
    by default.
    """
    payload = _fixture_full_train_payload_v1()
    monkeypatch.setattr(cli, "run_train_from_trajectories_v1", lambda **_kwargs: payload)

    exit_code, out, err = _run(
        [
            "train-from-trajectories", "--collection-run-dir", "/fixture/collection_run_dir",
            "--run-name", "x", "--max-steps", "100",
        ],
        capsys,
    )

    assert exit_code == 0
    assert err == ""
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)  # default stdout is a human-readable summary, not JSON
    assert "loss_trajectory" not in out
    assert "gradient_norms" not in out
    assert "admitted_game_record_paths" not in out
    assert "steps: 0->100" in out
    assert "/fixture/training_output_root/run_summary.json" in out
    assert out.count("\n") < 15  # short: aggregated, not one line per step


def test_cli_train_from_trajectories_json_flag_still_emits_the_full_payload(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _fixture_full_train_payload_v1()
    monkeypatch.setattr(cli, "run_train_from_trajectories_v1", lambda **_kwargs: payload)

    exit_code, out, err = _run(
        [
            "train-from-trajectories", "--collection-run-dir", "/fixture/collection_run_dir",
            "--run-name", "x", "--max-steps", "100", "--json",
        ],
        capsys,
    )

    assert exit_code == 0
    assert err == ""
    assert json.loads(out) == payload
    assert "loss_trajectory" in out


def test_cli_source_has_no_submission_or_network_path() -> None:
    """Hard rule under test: the CLI can only build and verify locally, never submit or reach the network."""
    source_text = inspect.getsource(cli)
    forbidden_substrings = (
        "competitions submit", "kaggle competitions", "subprocess", "socket",
        "urllib", "requests", "http.client", "ftplib", "smtplib",
    )
    lowered = source_text.lower()
    for forbidden in forbidden_substrings:
        assert forbidden not in lowered, f"cli.py must not reference {forbidden!r}"


def test_cli_main_module_has_no_submission_or_network_path() -> None:
    from mage_ptcg.meta_specialist import __main__ as cli_main_module

    source_text = inspect.getsource(cli_main_module).lower()
    assert "subprocess" not in source_text
    assert "competitions submit" not in source_text
