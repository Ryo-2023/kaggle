"""TDD contracts for the performance-first submission bundle audit."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def test_current_submission_audit_binds_root_rule_route_and_deck() -> None:
    from scripts.build_performance_submission_bundle_v1 import audit_current_submission

    report = audit_current_submission()

    assert report.candidate_id == "rule-v0-root-deck"
    assert report.policy_route == "main._DEFAULT_AGENT -> make_rule_agent -> agents.choose_rule_indices"
    assert report.deck_card_count == 60
    assert report.deck_sha256 == hashlib.sha256(Path("deck.csv").read_bytes()).hexdigest()
    assert report.submission_ready is True
    assert report.blockers == ()


def test_rule_bundle_builds_custom_deck_and_clean_room_verifies_archive(tmp_path: Path) -> None:
    from scripts.build_performance_submission_bundle_v1 import build_rule_v0_bundle
    from scripts.build_submission import validate_submission_archive

    output = tmp_path / "rule-bundle"
    result = build_rule_v0_bundle(output, deck_path=Path("deck.csv"))

    assert result["candidate_id"] == "rule-v0-root-deck"
    assert result["archive_only_structural"] is True
    assert result["clean_room"]["deck_size"] == 60
    assert result["deck_qualification_sha256"]
    assert result["deck_qualification_file_sha256"]
    archive = output / "submission.tar.gz"
    assert archive.is_file()
    assert validate_submission_archive(archive) == result["archive_members"]


def test_wave6_v4_audit_is_coherent_but_not_submission_ready() -> None:
    from scripts.build_performance_submission_bundle_v1 import audit_wave6_v4

    report = audit_wave6_v4(
        checkpoint=Path(
            "runs/meta-specialist-v4-archaludon-longrun-wave6-current/"
            "archaludon-training-checkpoints/seed-0/best-recurrent-bc-v4.pt"
        ),
        deck_path=Path("opponents/public_archaludon_cinderace_r7/deck.csv"),
    )

    assert report.candidate_id == "wave6-v4-seed0-archaludon"
    assert report.coherent_pair is True
    assert report.submission_ready is False
    assert "production_entrypoint_not_connected" in report.blockers
    assert "production_card_vocabulary_gate" in report.blockers


def test_bundle_rejects_symlinked_deck(tmp_path: Path) -> None:
    from scripts.build_performance_submission_bundle_v1 import BundleBuildError, build_rule_v0_bundle

    link = tmp_path / "deck.csv"
    link.symlink_to(Path("deck.csv").resolve())

    with pytest.raises(BundleBuildError, match="regular"):
        build_rule_v0_bundle(tmp_path / "out", deck_path=link)


def test_bundle_rejects_an_unverified_deck_qualification(tmp_path: Path) -> None:
    from scripts.build_performance_submission_bundle_v1 import BundleBuildError, build_rule_v0_bundle

    forged = tmp_path / "qualification.json"
    forged.write_text("{}", encoding="utf-8")
    with pytest.raises(BundleBuildError, match="qualification"):
        build_rule_v0_bundle(
            tmp_path / "out",
            deck_path=Path("deck.csv"),
            deck_qualification=forged,
        )
