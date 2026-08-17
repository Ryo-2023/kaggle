from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.opponent_pool_v1 import OpponentInstanceV1
from scripts.run_native_policy_candidate_pilot_v1 import (
    NativeCandidatePilotError,
    _config_sha,
    _validate_min_score_gain,
    _validate_biases,
    _validate_env,
    build_native_candidate_games_v1,
)


def _sha(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()


def _instance(tmp_path: Path, name: str) -> OpponentInstanceV1:
    root = tmp_path / name
    root.mkdir()
    deck = root / "deck.csv"
    deck.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    main = root / "main.py"
    main.write_text("def agent(obs):\n    return [0]\n", encoding="utf-8")
    return OpponentInstanceV1(
        opponent_id=name,
        deck_csv_path=str(deck),
        policy_path=str(main),
        canonical_deck_hash=_sha(f"canonical:{name}"),
        policy_hash=_sha(main.read_text()),
        usage_boundary="local_eval_only",
        source="fixture",
        mean_decision_ms=1.0,
    )


def test_candidate_config_is_bounded_and_canonical():
    env = _validate_env({"USE_SEARCH": "1", "SP_BUDGET": 0.25})
    biases = _validate_biases({"ATTACK": 5})
    assert env == {"SP_BUDGET": "0.25", "USE_SEARCH": "1"}
    assert biases == {"ATTACK": 5.0}
    assert len(_config_sha(env, biases)) == 64
    with pytest.raises(NativeCandidatePilotError):
        _validate_env({"PYTHONPATH": "."})
    assert _validate_min_score_gain(1000) == 1000.0
    with pytest.raises(NativeCandidatePilotError):
        _validate_min_score_gain(-1)


def test_game_builder_binds_candidate_config_and_balances_seats(tmp_path):
    candidate = _instance(tmp_path, "candidate")
    opponent = _instance(tmp_path, "opponent")
    candidate_spec = {
        "main_path": candidate.policy_path,
        "deck_path": candidate.deck_csv_path,
        "policy_sha256": candidate.policy_hash,
        "deck_sha256": _sha(Path(candidate.deck_csv_path).read_text()),
        "env": {"USE_SEARCH": "0"},
        "biases": {},
        "pool_root": str(tmp_path),
    }
    games = build_native_candidate_games_v1(
        candidate_id="candidate",
        candidate=candidate_spec,
        pool={"candidate": candidate, "opponent": opponent},
        reference_ids=("opponent",),
        games_per_opponent_seat=2,
    )
    assert len(games) == 4
    assert {game.seat for game in games} == {0, 1}
    assert all(game.metadata["promotion_authority"] is False for game in games)
    assert len({game.metadata["candidate_config_sha256"] for game in games}) == 1


def test_guarded_candidate_binds_min_score_gain_in_identity(tmp_path):
    candidate = _instance(tmp_path, "candidate")
    opponent = _instance(tmp_path, "opponent")
    candidate_spec = {
        "main_path": candidate.policy_path,
        "deck_path": candidate.deck_csv_path,
        "policy_sha256": candidate.policy_hash,
        "deck_sha256": _sha(Path(candidate.deck_csv_path).read_text()),
        "env": {},
        "biases": {"ATTACK": 5},
        "min_score_gain": 1000,
        "config_sha256": _config_sha({}, {"ATTACK": 5.0}, 1000.0),
        "pool_root": str(tmp_path),
    }
    games = build_native_candidate_games_v1(
        candidate_id="candidate",
        candidate=candidate_spec,
        pool={"candidate": candidate, "opponent": opponent},
        reference_ids=("opponent",),
        games_per_opponent_seat=1,
    )
    assert games[0].metadata["candidate_min_score_gain"] == 1000.0


def test_candidate_builder_rejects_empty_reference(tmp_path):
    candidate = _instance(tmp_path, "candidate")
    with pytest.raises(NativeCandidatePilotError):
        build_native_candidate_games_v1(
            candidate_id="candidate",
            candidate={
                "main_path": candidate.policy_path,
                "deck_path": candidate.deck_csv_path,
                "policy_sha256": candidate.policy_hash,
                "deck_sha256": _sha(Path(candidate.deck_csv_path).read_text()),
            },
            pool={"candidate": candidate},
            reference_ids=(),
        )
