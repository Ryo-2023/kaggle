from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_alternating_runtime_v1 import (
    OutcomeOnlyAlternatingRuntimeError,
    OutcomeOnlyCandidateSpecV1,
    build_candidate_control_games_v1,
    load_alternating_stage_v1,
    next_stage_games_v1,
    run_alternating_stage_v1,
    summarize_candidate_control_rows_v1,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config_sha(env: dict[str, str], biases: dict[str, float], min_score_gain: float) -> str:
    payload = {
        "env": dict(sorted(env.items())),
        "biases": dict(sorted(biases.items())),
        "min_score_gain": float(min_score_gain),
    }
    return hashlib.sha256(
        b"mage-ptcg:outcome-only-alternating-candidate-config:v1\0"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _spec(tmp_path: Path, candidate_id: str, *, config_delta: float = 0.0) -> OutcomeOnlyCandidateSpecV1:
    main_path = tmp_path / f"{candidate_id}.py"
    deck_path = tmp_path / f"{candidate_id}.csv"
    main_path.write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    deck_path.write_text("1\n" * 60, encoding="utf-8")
    env = {"USE_SEARCH": "0"} if config_delta else {}
    biases = {"PLAY": config_delta} if config_delta else {}
    min_score_gain = 0.0
    return OutcomeOnlyCandidateSpecV1(
        candidate_id=candidate_id,
        main_path=main_path,
        deck_path=deck_path,
        policy_sha256=_sha(main_path),
        deck_sha256=_sha(deck_path),
        config_sha256=_config_sha(env, biases, min_score_gain),
        env=env,
        biases=biases,
        min_score_gain=min_score_gain,
    )


_STAGE_REFERENCE_IDS = (
    "aman_crustleaware_fighting",
    "aristophanivan_multiply",
    "aristophanivan_probabilistic",
    "biohack44_crustlecounter2",
    "dashimaki360_crustlecounter",
    "ferozahmedds_solution",
    "harukiharada_crustle",
    "itsuki9180_lucario_jp",
    "kiyotah_abomasnow",
    "kiyotah_dragapult",
    "kiyotah_iono",
    "kojimar_lucario",
    "kokinnwakashuu_lucario_search",
    "lucifer19_battlecore",
    "masamikobayashi_garchomp",
    "medal_0001_77a53ffc",
    "naoto714_kangaskhan",
    "naoto714_slowking",
    "naoto714_ursaluna",
    "official_random",
    "pilkwang_lucario_alakazam",
    "plamen06_steel",
    "prvsiyan_grimmsnarl",
    "rauffauzanrambe_advanced",
)


def test_candidate_spec_rejects_changed_source_bytes(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "candidate")
    spec.main_path.write_text("def agent(obs):\n    return [0]\n", encoding="utf-8")
    with pytest.raises(OutcomeOnlyAlternatingRuntimeError, match="policy SHA"):
        spec.verify_sources()


def test_game_builder_preserves_candidate_control_pair_strata(tmp_path: Path) -> None:
    candidate = _spec(tmp_path, "candidate", config_delta=1.0)
    native = _spec(tmp_path, "native")
    games = build_candidate_control_games_v1(
        candidate=candidate,
        native_control=native,
        pool_root=Path("opponents"),
        reference_ids=("official_random", "plamen06_steel"),
        stage_games=8,
        base_seed=1234,
        block_id="test-block",
        runner_ref="scripts.parallel_cabt_evaluator_v1:fixture_runner_v1",
    )
    assert len(games) == 16
    arms = {str(game.metadata["alternating_arm"]) for game in games}
    assert arms == {"candidate", "native_control"}
    by_arm = {
        arm: {
            (str(game.metadata["pair_key"]), game.seed)
            for game in games
            if game.metadata["alternating_arm"] == arm
        }
        for arm in arms
    }
    assert by_arm["candidate"] == by_arm["native_control"]


def test_summary_keeps_faults_in_denominator_and_rejects_missing_arm() -> None:
    def rows(arm: str, outcome: str) -> list[dict[str, object]]:
        return [
            {
                "outcome": outcome,
                "status": "DONE" if outcome != "fault" else "FAULT",
                "fault_kind": None if outcome != "fault" else "runner_exception",
                "game_id": f"raw-{arm}-{index}",
                "seed": 100 + index,
                "opponent_id": "official_random",
                "seat": index % 2,
                "metadata": {
                    "alternating_arm": arm,
                    "pair_key": f"official_random|seat{index % 2}|rep{index // 2}",
                    "repetition": index // 2,
                },
            }
            for index, outcome in enumerate((outcome, "loss"))
        ]

    candidate = _spec(Path("/tmp"), "candidate-summary", config_delta=1.0)
    native = _spec(Path("/tmp"), "native-summary")
    summary = summarize_candidate_control_rows_v1(
        rows("candidate", "win") + rows("native_control", "loss"),
        candidate=candidate,
        native_control=native,
        stage_games=2,
        protocol_sha256="a" * 64,
    )
    assert summary["candidate"]["score_rate"] == 0.5
    assert summary["native_control"]["score_rate"] == 0.0
    assert summary["candidate"]["faults"] == 0
    with pytest.raises(OutcomeOnlyAlternatingRuntimeError, match="both arms"):
        summarize_candidate_control_rows_v1(
            rows("candidate", "win"),
            candidate=candidate,
            native_control=native,
            stage_games=2,
            protocol_sha256="a" * 64,
        )


def test_stage_dry_run_is_sealed_without_evaluation(tmp_path: Path) -> None:
    candidate = _spec(tmp_path, "candidate", config_delta=1.0)
    native = _spec(tmp_path, "native")
    result = run_alternating_stage_v1(
        candidate=candidate,
        native_control=native,
        pool_root=Path("opponents"),
        reference_ids=_STAGE_REFERENCE_IDS,
        stage_games=96,
        base_seed=1234,
        block_id="dry-run",
        output_root=tmp_path / "dry-run",
        execute=False,
        runner_ref="scripts.parallel_cabt_evaluator_v1:fixture_runner_v1",
    )
    assert result["status"] == "DRY_RUN"
    assert result["execution_started"] is False
    assert not (tmp_path / "dry-run" / "evaluation").exists()
    loaded = load_alternating_stage_v1(tmp_path / "dry-run")
    assert loaded["status"] == "DRY_RUN"
    assert loaded["authority"] == {
        "execute_allowed": False,
        "training_allowed": False,
        "promotion_allowed": False,
        "submission_allowed": False,
        "longrun_allowed": False,
    }


def test_stage_execute_persists_parallel_fixture_summary(tmp_path: Path) -> None:
    candidate = _spec(tmp_path, "candidate", config_delta=1.0)
    native = _spec(tmp_path, "native")
    result = run_alternating_stage_v1(
        candidate=candidate,
        native_control=native,
        pool_root=Path("opponents"),
        reference_ids=_STAGE_REFERENCE_IDS,
        stage_games=96,
        base_seed=1234,
        block_id="execute",
        output_root=tmp_path / "execute",
        execute=True,
        runner_ref="scripts.parallel_cabt_evaluator_v1:fixture_runner_v1",
    )
    assert result["status"] == "COMPLETE"
    assert result["execution_started"] is True
    manifest = json.loads((tmp_path / "execute" / "manifest.json").read_text())
    assert manifest["workers"] == 12
    assert manifest["worker_recycle_games"] == 16
    assert manifest["summary_sha256"] == result["summary_sha256"]
    assert result["summary"]["candidate"]["requested_games"] == 96
    assert result["summary"]["native_control"]["requested_games"] == 96
    assert result["decision"] == "NOT_PROMOTABLE"


def test_next_stage_requires_positive_result() -> None:
    assert next_stage_games_v1(96, positive=True) == 384
    assert next_stage_games_v1(96, positive=False) is None
    assert next_stage_games_v1(1536, positive=True) is None
    with pytest.raises(OutcomeOnlyAlternatingRuntimeError, match="successive-halving"):
        next_stage_games_v1(8, positive=True)
