"""Task 5/6: thin standalone scripts wrapping the meta-specialist build/verify CLI."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, create_deck_lock, qualify_deck_asset
from mage_ptcg.meta_specialist.package import BundleSpec, DependencyContractIds, derive_entrypoint_contract_id, write_bundle_spec
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import CABT_AGENT_JSON_CONTRACT_SHA256_V1

ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "scripts" / "build_meta_specialist_submission.py"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_meta_specialist_submission.py"


def _run_script(script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, timeout=60, check=False,
    )


def _write_static_bundle_spec(source: Path) -> Path:
    source.mkdir(parents=True, exist_ok=True)
    cards = tuple(range(1, 61))
    deck_path = source / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    asset = DeckAssetInput.from_path(
        asset_id="script-fixture", archetype_id="script-fixture", path=deck_path,
        source_ref="https://example.invalid/decks/script-fixture.csv", source_commit="a" * 40,
        asset_class="deck_only", usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2", card_database_version="fixture-v1",
    )
    qualified = qualify_deck_asset(
        asset, ArchetypeSpec("script-fixture", (), (cards[0],), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _: (True, "script-fixture placeholder evidence"),
    )
    (source / "main.py").write_text("agent = lambda observation, configuration: []\n", encoding="utf-8")
    (source / "policy_loader.py").write_text("# structural fixture\n", encoding="utf-8")
    (source / "rule_policy_v1.py").write_text("# static policy fixture\n", encoding="utf-8")

    constraints = RuntimeConstraintManifest.frozen_v1()
    ladder = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
    lock = create_deck_lock(
        archetype_id=qualified.archetype_id, selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,), foundation_init_id="b" * 64,
        joint_race_schedule_id="c" * 64, equal_transition_budget=1,
    )
    members = ("deck.csv", "main.py", "policy_loader.py", "rule_policy_v1.py")
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
    spec = BundleSpec(
        source_root=source, members=members, deck_member="deck.csv",
        policy_entrypoint_member="policy_loader.py", qualified_deck_asset=qualified, deck_lock=lock,
        runtime_constraints=constraints, ladder_mechanics=ladder, dependency_contract_ids=dependency_ids,
        candidate_class="static_rule_bundle", policy_members=("rule_policy_v1.py",), model_member=None,
        policy_identity=policy_identity, checkpoint_lineage_id=None,
        checkpoint_lineage_reason="not_applicable_static_policy",
    )
    spec_path = source.parent / "bundle_spec.json"
    write_bundle_spec(spec, spec_path)
    return spec_path


def test_build_then_verify_scripts_round_trip_on_a_real_temporary_bundle(tmp_path: Path) -> None:
    """Required regression test: build -> verify round trip, exercised as standalone scripts."""
    spec_path = _write_static_bundle_spec(tmp_path / "source")
    archive_path = tmp_path / "submission.tar.gz"

    build_result = _run_script(BUILD_SCRIPT, ["--spec", str(spec_path), "--output", str(archive_path)])

    assert build_result.returncode == 0, build_result.stderr
    assert build_result.stderr == ""
    build_report = json.loads(build_result.stdout)
    assert build_report["status"] == "structurally_verified"
    assert archive_path.is_file()

    verify_result = _run_script(VERIFY_SCRIPT, ["--archive", str(archive_path)])

    assert verify_result.returncode == 0, verify_result.stderr
    assert verify_result.stderr == ""
    verify_report = json.loads(verify_result.stdout)
    assert verify_report == build_report


def test_build_script_missing_spec_raises_rather_than_fabricates(tmp_path: Path) -> None:
    result = _run_script(
        BUILD_SCRIPT,
        ["--spec", str(tmp_path / "missing_spec.json"), "--output", str(tmp_path / "out.tar.gz")],
    )

    assert result.returncode != 0
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["status"] == "ERROR"
    assert not (tmp_path / "out.tar.gz").exists()


def test_verify_script_missing_archive_raises_rather_than_fabricates(tmp_path: Path) -> None:
    result = _run_script(VERIFY_SCRIPT, ["--archive", str(tmp_path / "missing.tar.gz")])

    assert result.returncode != 0
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["status"] == "ERROR"


def test_build_script_deck_csv_removed_from_bundle_source_raises(tmp_path: Path) -> None:
    """Required regression test: a missing deck.csv under the bundle's own source_root fails closed."""
    source = tmp_path / "source"
    spec_path = _write_static_bundle_spec(source)
    (source / "deck.csv").unlink()
    archive_path = tmp_path / "submission.tar.gz"

    result = _run_script(BUILD_SCRIPT, ["--spec", str(spec_path), "--output", str(archive_path)])

    assert result.returncode != 0
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["status"] == "ERROR"
    assert not archive_path.exists()


@pytest.mark.parametrize("script", [BUILD_SCRIPT, VERIFY_SCRIPT])
def test_scripts_have_no_submission_or_network_path(script: Path) -> None:
    """Hard rule under test: these scripts can only build/verify locally, never submit or reach the network."""
    source_text = script.read_text(encoding="utf-8").lower()
    forbidden_substrings = (
        "competitions submit", "kaggle competitions", "subprocess", "socket",
        "urllib", "requests", "http.client", "ftplib", "smtplib",
    )
    for forbidden in forbidden_substrings:
        assert forbidden not in source_text, f"{script.name} must not reference {forbidden!r}"
