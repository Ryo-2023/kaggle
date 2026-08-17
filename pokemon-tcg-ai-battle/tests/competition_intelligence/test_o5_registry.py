"""O5 environment/team registry safety and deduplication tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mage_ptcg.competition_intelligence.o5_registry import (
    CAPTURE_ONLY,
    DeckArchetypeRegistry,
    EnvironmentTopDeckCollector,
    EnvironmentTopDeckPolicy,
    TeamBranchInventoryImporter,
    coverage_report,
    parse_exact_deck_text,
)
from mage_ptcg.competition_intelligence.rules_attestation import RulesAttestation


def _deck(card: int) -> list[int]:
    return [card] * 60


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, text=True)


def test_environment_capture_only_deduplicates_exact_deck_and_own_encounter(tmp_path: Path) -> None:
    registry = DeckArchetypeRegistry(tmp_path / "registry")
    result = EnvironmentTopDeckCollector().collect(
        [{"submission_id": "s-public", "rating": 1500}, {"submission_id": "s-own", "rating": 1400}],
        {"s-public": {"submission_lineage": "a"}, "s-own": {"submission_lineage": "b"}},
        {
            "s-public": [{"episode_id": "e-public", "content_hash": "a" * 64, "cards": _deck(1)}],
            "s-own": [{"episode_id": "e-own", "content_hash": "b" * 64, "cards": _deck(1), "source_kind": "OWN_KAGGLE", "own_encounter": True}],
        },
        EnvironmentTopDeckPolicy(top_rating_submissions=2, recent_or_rising_submissions=0, diversity_submissions=0),
        RulesAttestation(competition="pokemon-tcg-ai-battle"), registry=registry, now="2026-07-20T00:00:00Z",
    )
    assert result.mode == CAPTURE_ONLY
    assert result.exact_deck_count == 2
    assert len(registry.data["deck_lists"]) == 1
    stats = registry.reconcile()
    assert next(iter(stats.values()))["own_encounter_count"] == 1
    assert next(iter(stats.values()))["environment_observation_count"] == 2
    assert all(row["allowed_use"] == "ARCHIVE" for row in registry.data["deck_observations"])


def test_environment_resume_and_exact_snapshot_cap(tmp_path: Path) -> None:
    registry = DeckArchetypeRegistry(tmp_path / "registry")
    policy = EnvironmentTopDeckPolicy(top_rating_submissions=1, recent_or_rising_submissions=0, diversity_submissions=0, max_total_exact_decks_per_snapshot=1)
    episodes = {"s": [
        {"episode_id": "same", "content_hash": "a" * 64, "cards": _deck(1)},
        {"episode_id": "same", "content_hash": "a" * 64, "cards": _deck(2)},
    ]}
    first = EnvironmentTopDeckCollector().collect([{"submission_id": "s"}], {"s": {}}, episodes, policy, None, registry=registry, now="2026-07-20T00:00:00Z")
    second = EnvironmentTopDeckCollector().collect([{"submission_id": "s"}], {"s": {}}, episodes, policy, None, registry=registry, now="2026-07-20T00:00:01Z")
    assert first.acquired_replay_count == 1
    assert second.acquired_replay_count == 0
    assert len(registry.data["deck_lists"]) == 1
    assert len(registry.data["environment_episode_samples"]) == 1


def test_invalid_card_ids_never_become_exact_decks() -> None:
    assert parse_exact_deck_text("0\n" * 60) is None
    assert parse_exact_deck_text("1\n" * 59) is None


def test_environment_keeps_partial_replay_as_incomplete_observation(tmp_path: Path) -> None:
    registry = DeckArchetypeRegistry(tmp_path / "registry")
    result = EnvironmentTopDeckCollector().collect(
        [{"submission_id": "s"}], {"s": {}},
        {"s": [{"episode_id": "e", "observed_card_counts": {"1": 4}}]},
        EnvironmentTopDeckPolicy(top_rating_submissions=1, recent_or_rising_submissions=0, diversity_submissions=0), None,
        registry=registry, now="2026-07-20T00:00:00Z",
    )
    assert result.incomplete_observation_count == 1
    assert not registry.data["deck_lists"]
    assert registry.data["deck_observations"][0]["exact"] is False
    assert "deck_hash" not in registry.data["deck_observations"][0]


def test_explicit_rules_attestation_is_required_before_archetype_classification(tmp_path: Path) -> None:
    registry = DeckArchetypeRegistry(tmp_path / "registry")
    EnvironmentTopDeckCollector().collect(
        [{"submission_id": "s"}], {"s": {}},
        {"s": [{"episode_id": "e", "cards": _deck(3), "archetype_hint": "fixture-archetype"}]},
        EnvironmentTopDeckPolicy(top_rating_submissions=1, recent_or_rising_submissions=0, diversity_submissions=0),
        RulesAttestation(competition="pokemon-tcg-ai-battle", status="VERIFIED_RULES_CONSTRAINT", verified_at="2026-07-20T00:00:00Z", verified_by="reviewer", reference="rules-url"),
        registry=registry, now="2026-07-20T00:00:00Z",
    )
    report = coverage_report(registry)
    assert report["o5_c_candidate_archetypes"] == ["fixture-archetype"]
    assert report["deck_classifications"][0]["classification_status"] == "ACTIVE"


def test_branch_inventory_reads_git_objects_and_preserves_cross_branch_deck_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "opponents" / "alpha").mkdir(parents=True)
    (repo / "opponents" / "alpha" / "deck.csv").write_text("1\n" * 60, encoding="utf-8")
    (repo / "opponents" / "alpha" / "main.py").write_text("open('deck.csv')\ndef agent(obs):\n    return []\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "fixture_deck.csv").write_text("2\n" * 60, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    _git(repo, "branch", "copied")
    before = _git_head(repo)
    registry = DeckArchetypeRegistry(tmp_path / "registry")
    result = TeamBranchInventoryImporter(repo).inventory(registry, refs=("master", "copied"), observed_at="2026-07-20T00:00:00Z")
    assert result["team_branches_scanned"] == 2
    assert _git_head(repo) == before
    assert len(registry.data["deck_lists"]) == 1
    assert len(registry.data["branch_artifacts"]) == 4  # test fixture is excluded entirely
    assert not any(row["artifact_kind"] == "INVALID_OR_STALE" for row in registry.data["branch_artifacts"])
    assert {link["link_status"] for link in registry.data["agent_deck_links"]} == {"VERIFIED_LINK"}
    report = coverage_report(registry)
    assert report["cross_source_duplicate_decks"] == 0
    assert report["permission_blocked_records"] == 4
    assert report["unique_agent_implementations"] == 1
    assert "NO_ACTIVE_RUNNABLE_AGENT" in report["o5_c_candidate_reasons"]
    assert coverage_report(registry) == report


def test_ambiguous_agent_is_not_promoted_to_verified_link(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    (repo / "opponents" / "beta").mkdir(parents=True)
    (repo / "opponents" / "beta" / "deck.csv").write_text("1\n" * 60, encoding="utf-8")
    (repo / "opponents" / "beta" / "agent.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "ambiguous")
    registry = DeckArchetypeRegistry(tmp_path / "registry")
    TeamBranchInventoryImporter(repo).inventory(registry, refs=("master",))
    assert registry.data["agent_deck_links"][0]["link_status"] == "UNRESOLVED_AGENT_DECK_LINK"


def _git_head(repo: Path) -> str:
    return subprocess.run(("git", "-C", str(repo), "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()


def test_team_permission_template_is_fail_closed() -> None:
    template = Path(__file__).parents[2] / "configs" / "competition" / "team_permission_o5_v1.template.yaml"
    text = template.read_text(encoding="utf-8")
    assert "schema_version: team-permission-v1" in text
    assert "training: false" in text
    assert "evaluation: false" in text
