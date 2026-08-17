from __future__ import annotations

from mage_ptcg.competition_intelligence.contracts import AcquisitionMode, AllowedUse, SourceEnvelope, SourceKind
from mage_ptcg.competition_intelligence.kaggle_replay_normalize import (
    VerifiedEpisodeAgentMapping,
    normalize_kaggle_replay,
    replay_schema_fingerprint,
)


def _card(card_id: int) -> dict[str, int]:
    return {"id": card_id}


def _player(card_id: int, *, hand: object) -> dict[str, object]:
    return {
        "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": hand,
        "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6,
    }


def _observation(*, opponent_hand: object = None, result: object = -1) -> dict[str, object]:
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0,
            "players": [_player(100, hand=[_card(100)]), _player(700, hand=opponent_hand)],
            "result": result, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 0, "yourIndex": 0,
        },
        "logs": ["never persisted"], "search_begin_input": "opaque", "step": 1,
        "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0},
    }


def _source() -> SourceEnvelope:
    return SourceEnvelope(
        schema_version="source-envelope-v1", source_id="own-replay", source_kind=SourceKind.OWN_KAGGLE,
        acquisition_mode=AcquisitionMode.FULL_REPLAY, acquired_at="2026-07-20T00:00:00Z", observed_at=None,
        origin_reference="kaggle:replay:redacted", owner_scope="self", visibility="private",
        allowed_uses=frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS, AllowedUse.TRAINING}),
        terms_snapshot_hash=None, raw_sha256="a" * 64, parser_version="test", redaction_version="test", metadata={"action": "replay"},
    )


def _mapping() -> VerifiedEpisodeAgentMapping:
    return VerifiedEpisodeAgentMapping(
        episode_id="episode-1", submission_id="submission-private", own_agent_index=0,
        identity_hash="b" * 64, episode_mapping_hash="c" * 64,
        played_at="2026-07-19T00:00:00Z", agent_identity_hashes=("d" * 64, "e" * 64),
    )


def _replay(*, opponent_hand: object = None, result: object = -1) -> dict[str, object]:
    deck_a = [1] * 60
    deck_b = [2] * 60
    return {
        "id": "pokemon-tcg-ai-battle", "version": "test", "info": {"EpisodeId": "episode-1"},
        "rewards": [1, 0], "statuses": ["DONE", "DONE"],
        "steps": [
            [
                {"status": "ACTIVE", "reward": 0, "action": [], "observation": _observation(opponent_hand=opponent_hand, result=result),
                 "visualize": [{"action": [deck_a, deck_b]}]},
                {"status": "ACTIVE", "reward": 0, "action": [], "observation": _observation()},
            ],
            [
                {"status": "DONE", "reward": 1, "action": [0], "observation": {}},
                {"status": "DONE", "reward": 0, "action": [0], "observation": {}},
            ],
        ],
    }


def test_normalizer_keeps_only_verified_actor_visible_rule_relabels() -> None:
    result = normalize_kaggle_replay(_replay(), _mapping(), _source())
    assert result.quarantine_reason is None
    assert result.episode is not None and result.episode.winner == 0
    assert len(result.decisions) == len(result.training_examples) == 1
    decision = result.decisions[0]
    encoded = str(decision.content_payload())
    assert decision.actor_seat == 0
    assert decision.actor_information_view["actor_visibility_valid"] is True
    assert decision.actor_information_view["privacy_valid"] is True
    assert "logs" not in encoded and "search_begin_input" not in encoded and "submission-private" not in encoded
    assert decision.chosen_action_raw["recorded_action_checked_legal"] is True
    assert decision.result_to_go is None
    assert len(result.deck_observations) == 2
    assert result.deck_observations[0].exact_decklist == {1: 60}
    fingerprint, summary = replay_schema_fingerprint(_replay())
    assert fingerprint == result.schema_fingerprint and "top_level_keys" in summary


def test_normalizer_excludes_opponent_private_hand_and_terminal_result() -> None:
    private = normalize_kaggle_replay(_replay(opponent_hand=[_card(700)]), _mapping(), _source())
    terminal = normalize_kaggle_replay(_replay(result=1), _mapping(), _source())
    assert not private.decisions and private.excluded_decisions[0]["reason"] == "OPPONENT_PRIVATE_HAND_EXPOSED"
    assert not terminal.decisions and terminal.excluded_decisions[0]["reason"] == "FUTURE_INFORMATION_RISK"


def test_normalizer_rejects_replay_episode_not_matching_official_mapping() -> None:
    replay = _replay()
    replay["info"] = {"EpisodeId": "other"}
    result = normalize_kaggle_replay(replay, _mapping(), _source())
    assert result.episode is None and result.quarantine_reason == "SUBMISSION_MAPPING_MISSING"
