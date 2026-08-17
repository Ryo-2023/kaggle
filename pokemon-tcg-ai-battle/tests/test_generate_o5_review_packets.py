"""Focused tests for the O5 Current Meta review packet generator."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.generate_o5_review_packets import ReviewPacketError, build_review_rows, main  # noqa: E402
from mage_ptcg.competition_intelligence.o5_registry import O5_REGISTRY_SCHEMA_VERSION  # noqa: E402


def _registry(**overrides):
    base = {
        "schema_version": O5_REGISTRY_SCHEMA_VERSION,
        "deck_lists": {
            "team-hash-1": {
                "card_pool_version": "unknown",
                "cards": [1] * 30 + [2] * 30,
                "deck_hash": "team-hash-1",
                "provenance": [
                    {"source_kind": "TEAM_SHARED", "branch_ref": "origin/agents/example", "commit_sha": "abc123", "git_blob_sha": "blob1", "path": "deck.csv"},
                ],
            },
            "public-hash-1": {
                "card_pool_version": "unknown",
                "cards": [3] * 60,
                "deck_hash": "public-hash-1",
                "provenance": [
                    {"source_kind": "PUBLIC_OTHER", "episode_id": "ep-1"},
                ],
            },
        },
        "agent_deck_links": [
            {"deck_hash": "team-hash-1", "link_status": "VERIFIED_LINK"},
            {"deck_hash": "team-hash-1", "link_status": "UNRESOLVED_AGENT_DECK_LINK"},
        ],
        "branch_artifacts": [],
    }
    base.update(overrides)
    return base


def test_build_review_rows_classifies_team_shared_and_public_other():
    rows = build_review_rows(_registry())
    by_hash = {row["deck_hash"]: row for row in rows}
    assert by_hash["team-hash-1"]["visibility"] == "TEAM_SHARED"
    assert by_hash["team-hash-1"]["permission_status"] == "TEAM_SHARED_PENDING_PERMISSION"
    assert by_hash["team-hash-1"]["attestation_status"] == "NOT_APPLICABLE_TEAM_SHARED_ONLY"
    assert by_hash["team-hash-1"]["linked_agent_count"] == 2
    assert by_hash["team-hash-1"]["verified_agent_link_count"] == 1

    assert by_hash["public-hash-1"]["visibility"] == "PUBLIC_OTHER_OR_OWN_KAGGLE"
    assert by_hash["public-hash-1"]["attestation_status"] == "UNVERIFIED_RULES_CONSTRAINT"
    assert by_hash["public-hash-1"]["permission_status"] == "NOT_APPLICABLE_PUBLIC_SOURCE"


def test_no_row_ever_reports_an_allowed_use():
    # A review packet must never claim a deck is already permitted -- that
    # decision belongs to a human signer, not to this generator.
    for row in build_review_rows(_registry()):
        assert row["allowed_use"] == []


def test_card_counts_are_exact_and_sum_to_reported_card_count():
    rows = build_review_rows(_registry())
    for row in rows:
        assert sum(row["exact_card_counts"].values()) == row["card_count"]


def test_cli_rejects_a_registry_snapshot_with_the_wrong_schema_version(tmp_path):
    import json

    bad_path = tmp_path / "bad_registry.json"
    bad_path.write_text(json.dumps({"schema_version": "not-the-real-schema"}), encoding="utf-8")
    exit_code = main(["--registry-path", str(bad_path), "--output-dir", str(tmp_path / "out")])
    assert exit_code != 0


def test_cli_end_to_end_writes_summary_and_never_activates_a_deck(tmp_path):
    import json

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")
    output_dir = tmp_path / "out"
    exit_code = main(["--registry-path", str(registry_path), "--output-dir", str(output_dir)])
    assert exit_code == 0
    summary = json.loads((output_dir / "current_meta_review_summary.json").read_text(encoding="utf-8"))
    assert summary["total_exact_decks"] == 2
    assert summary["team_shared_decks"] == 1
    assert summary["public_or_own_kaggle_decks"] == 1
    assert summary["activated_decks"] == 0
    assert (output_dir / "team_permission_deck_review.md").exists()
    assert (output_dir / "rules_attestation_deck_review.md").exists()
    assert (output_dir / "rules_attestation_review_packet.md").exists()  # from the reused baseline generator
