from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def _deck(path: Path) -> Path:
    path.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    return path


def test_candidate_pack_is_immutable_and_hash_bound(tmp_path: Path) -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
        build_candidate_pack,
        pack_bytes_sha256,
    )
    from mage_ptcg.knowledge import KnowledgePack, serialize_pack

    pack = build_candidate_pack(_deck(tmp_path / "deck.csv"), candidate_id="play-plus", score=2.0)
    assert isinstance(pack, KnowledgePack)
    assert pack.manifest.pack_id.startswith("knowledge-pack-v0-")
    assert pack_bytes_sha256(pack) == hashlib.sha256(serialize_pack(pack)).hexdigest()
    with pytest.raises(AttributeError):
        pack.manifest.source = "tampered"  # type: ignore[misc]
    assert pack.action_priors[0].score == 2.0


def test_candidate_manifest_binds_root_pool_evaluator_and_authority(tmp_path: Path) -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
        build_candidate_manifest,
        build_candidate_pack,
    )

    pack = build_candidate_pack(_deck(tmp_path / "deck.csv"), candidate_id="play-minus", score=-2.0)
    manifest = build_candidate_manifest(
        candidate_id="play-minus",
        pack=pack,
        root_policy_sha256="a" * 64,
        deck_sha256="b" * 64,
        pool_manifest_sha256="c" * 64,
        broad_config_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        common24_ids=("official_random",),
    )
    assert manifest["candidate_id"] == "play-minus"
    assert manifest["research_only"] is True
    assert manifest["promotion_authority"] is False
    assert manifest["training_authority"] is False
    assert manifest["submission_authority"] is False
    assert manifest["usage_boundary"] == "local_eval_only"
    assert manifest["pack_sha256"] == manifest["pack_sha256"]
    assert manifest["root_policy_sha256"] == "a" * 64
    assert manifest["common24_ids"] == ["official_random"]


def test_build_screen_games_are_balanced_and_pack_bound(tmp_path: Path) -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
        build_candidate_pack,
        build_screen_games,
    )
    from mage_ptcg.knowledge import write_pack

    deck = _deck(tmp_path / "deck.csv")
    pack = build_candidate_pack(deck, candidate_id="play-neutral", score=0.0)
    write_pack(pack, tmp_path / "play-neutral.json")
    games = build_screen_games(
        candidate_id="play-neutral",
        pack_path=tmp_path / "play-neutral.json",
        pack=pack,
        opponent_ids=("official_random",),
        games_per_seat=2,
        base_seed=14900000,
        subject_deck=deck,
        pool_manifest_sha256="c" * 64,
        broad_config_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        root_policy_sha256="a" * 64,
    )
    assert len(games) == 4
    assert {game.seat for game in games} == {0, 1}
    assert {game.metadata["candidate_id"] for game in games} == {"play-neutral"}
    assert all(game.metadata["pack_sha256"] for game in games)
    assert all(game.metadata["research_only"] is True for game in games)
    assert all(game.runner_ref.endswith(":run_rule_v0_knowledge_pool_game_v1") for game in games)


def test_baseline_screen_has_no_pack_but_same_identity_contract(tmp_path: Path) -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import build_screen_games

    deck = _deck(tmp_path / "deck.csv")
    games = build_screen_games(
        candidate_id="baseline-no-pack",
        pack_path=None,
        pack=None,
        opponent_ids=("official_random",),
        games_per_seat=1,
        base_seed=14900000,
        subject_deck=deck,
        pool_manifest_sha256="c" * 64,
        broad_config_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        root_policy_sha256="a" * 64,
    )
    assert len(games) == 2
    assert all(game.metadata["pack_sha256"] is None for game in games)
    assert all(game.metadata["candidate_policy_sha256"] == "a" * 64 for game in games)


def test_action_delta_policy_is_public_bounded_and_legal() -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import build_action_delta_agent

    agent = build_action_delta_agent(
        candidate_id="delta-attack-plus",
        deltas={"ATTACK": 200.0},
    )
    observation = {
        "current": {"yourIndex": 0},
        "select": {
            "type": 0,
            "minCount": 1,
            "maxCount": 1,
            "option": [{"type": 13}, {"type": 7}],
        },
    }
    assert agent(observation) == [0]
    assert agent({"select": {"type": 1, "minCount": 1, "maxCount": 1, "option": [{"type": 13}]}}) == [0]


def test_action_delta_manifest_is_hash_bound_and_rejects_unbounded_values() -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import build_candidate_manifest

    manifest = build_candidate_manifest(
        candidate_id="delta-attack-plus",
        pack=None,
        action_deltas={"ATTACK": 100.0},
        root_policy_sha256="a" * 64,
        deck_sha256="b" * 64,
        pool_manifest_sha256="c" * 64,
        broad_config_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        common24_ids=("official_random",),
    )
    assert manifest["action_deltas"] == {"ATTACK": 100.0}
    assert manifest["candidate_policy_sha256"] != "a" * 64
    import pytest

    with pytest.raises(ValueError, match="bounded"):
        build_candidate_manifest(
            candidate_id="bad",
            pack=None,
            action_deltas={"ATTACK": 201.0},
            root_policy_sha256="a" * 64,
            deck_sha256="b" * 64,
            pool_manifest_sha256="c" * 64,
            broad_config_sha256="d" * 64,
            evaluator_sha256="e" * 64,
            common24_ids=("official_random",),
        )


def test_action_delta_screen_metadata_and_runner_identity(tmp_path: Path) -> None:
    from scripts.run_rule_v0_knowledge_pool_screen_v1 import build_screen_games

    deck = _deck(tmp_path / "deck.csv")
    games = build_screen_games(
        candidate_id="attack-plus-200",
        pack_path=None,
        pack=None,
        action_deltas={"ATTACK": 200.0},
        opponent_ids=("official_random",),
        games_per_seat=1,
        base_seed=14900000,
        subject_deck=deck,
        pool_manifest_sha256="c" * 64,
        broad_config_sha256="d" * 64,
        evaluator_sha256="e" * 64,
        root_policy_sha256="a" * 64,
    )
    assert len(games) == 2
    assert all(game.metadata["action_deltas"] == {"ATTACK": 200.0} for game in games)
    assert all(game.metadata["candidate_policy_sha256"] != "a" * 64 for game in games)
    assert all(game.runner_ref == "scripts.run_rule_v0_knowledge_pool_screen_v1:run_rule_v0_knowledge_pool_game_v1" for game in games)


def test_run_screen_creates_each_arm_root_before_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.run_rule_v0_knowledge_pool_screen_v1 as screen

    monkeypatch.setattr(
        screen,
        "run_parallel_cabt_evaluation",
        lambda games, **_kwargs: {"summary": {"requested_games": len(games), "faults": 0}},
    )
    result = screen.run_screen(
        output_dir=tmp_path / "screen",
        opponent_ids=("official_random",),
        games_per_seat=1,
    )
    assert set(result["arms"]) == {
        "baseline-no-pack",
        "play-minus",
        "play-plus",
        "attack-plus-200",
        "play-minus-200",
    }
    for candidate_id in result["arms"]:
        assert (tmp_path / "screen" / candidate_id / "manifest.json").is_file()


def test_run_screen_forwards_explicit_subject_deck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.run_rule_v0_knowledge_pool_screen_v1 as screen

    subject_deck = _deck(tmp_path / "subject.csv")
    observed: list[Path] = []

    def fake_build_screen_games(**kwargs):
        observed.append(Path(kwargs["subject_deck"]).resolve())
        return ()

    monkeypatch.setattr(screen, "build_screen_games", fake_build_screen_games)
    monkeypatch.setattr(
        screen,
        "run_parallel_cabt_evaluation",
        lambda games, **_kwargs: {"summary": {"requested_games": len(games), "faults": 0}},
    )
    screen.run_screen(
        output_dir=tmp_path / "screen",
        opponent_ids=("official_random",),
        games_per_seat=1,
        subject_deck=subject_deck,
    )
    assert observed and all(path == subject_deck.resolve() for path in observed)
