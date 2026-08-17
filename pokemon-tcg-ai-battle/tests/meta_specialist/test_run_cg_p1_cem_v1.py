from __future__ import annotations

from pathlib import Path
import hashlib
import json
import inspect
import sys
import types

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import update_distribution
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.run_cg_p1_cem_v1 import (
    _search_plan,
    _select_initial_elites,
    _select_update_elites,
    _control_identity,
    _elite_update_rows,
    _risk_aware_reevaluation,
    build_paired_games,
    candidate_result_from_rows,
    _load_initial_config,
    _bind_repeat_control,
    _scales_from_fraction,
    _static_smoke,
    _materialize_cem_candidate,
    run_campaign,
)


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
P1_SOURCE_CORE = ROOT / "runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-source-core"
CONTROL_SELF_OWNED = ROOT / "runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-core-control"
G01_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"
SPLIT = load_weekend_split(ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json")


def test_cem_campaign_exposes_an_explicit_independent_campaign_seed() -> None:
    assert "campaign_seed" in inspect.signature(run_campaign).parameters


def test_cem_campaign_exposes_a_reevaluation_games_budget() -> None:
    assert "reeval_games_per_opponent_seat" in inspect.signature(run_campaign).parameters


def test_cem_campaign_exposes_a_positive_delta_update_gate() -> None:
    assert "positive_delta_gate" in inspect.signature(run_campaign).parameters


def test_cem_campaign_exposes_a_staged_pool_root_override() -> None:
    assert "pool_root" in inspect.signature(run_campaign).parameters
    assert "pool_root" in inspect.signature(build_paired_games).parameters


def test_cem_campaign_exposes_population_and_elite_budget_overrides() -> None:
    assert "population_size" in inspect.signature(run_campaign).parameters
    assert "elite_count" in inspect.signature(run_campaign).parameters


def test_static_smoke_runs_outside_the_cem_parent_process(monkeypatch, tmp_path: Path) -> None:
    """Static candidate import must not initialize native ``cg`` in the CEM parent."""
    import scripts.run_cg_p1_cem_v1 as cem

    calls: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls["command"] = command
        calls["kwargs"] = kwargs
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "static_smoke_report.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "candidate_main_sha256": hashlib.sha256((P1_PACKAGE / "main.py").read_bytes()).hexdigest(),
                    "control_main_sha256": hashlib.sha256((P1_PACKAGE / "main.py").read_bytes()).hexdigest(),
                    "candidate_agent_contract": "PASS",
                }
            ),
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def fail_parent_import(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("native candidate import happened in the CEM parent")

    monkeypatch.setattr(cem, "subprocess", types.SimpleNamespace(run=fake_run), raising=False)
    monkeypatch.setattr(cem.arena, "_load_candidate", fail_parent_import)
    before = {name for name in sys.modules if name == "cg" or name.startswith("cg.")}

    _static_smoke(P1_PACKAGE, P1_PACKAGE)

    after = {name for name in sys.modules if name == "cg" or name.startswith("cg.")}
    assert after == before
    assert isinstance(calls.get("command"), list)
    command = calls["command"]
    assert "--candidate-package" in command
    assert "--control-package" in command
    assert "--output" in command
    kwargs = calls["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["check"] is False
    assert kwargs["start_new_session"] is True


def test_deck_bound_materializer_rebinds_source_fallback_to_control_deck(tmp_path: Path) -> None:
    """A mismatched P1 source must be rebound before CEM static smoke."""
    target = tmp_path / "candidate"
    manifest = _materialize_cem_candidate(
        source_package=P1_SOURCE_CORE,
        output_package=target,
        config=P1ParameterConfig.default(),
        candidate_id="cg-p1-cem-deck-bound-test",
        deck_binding_package=CONTROL_SELF_OWNED,
    )

    assert manifest["root_deck_bound"] is True
    assert manifest["deck_binding_mode"] == "SELF_OWNED_CONTROL_PACKAGE"
    assert manifest["deck_sha256"] == hashlib.sha256((CONTROL_SELF_OWNED / "deck.csv").read_bytes()).hexdigest()
    _static_smoke(target, CONTROL_SELF_OWNED)


def test_initial_scale_fraction_is_bounded_and_tracks_parameter_span() -> None:
    scales = _scales_from_fraction(0.10)
    assert scales["lethal_bonus"] == 3000.0
    assert scales["attack_damage_weight_milli"] == 300.0
    assert all(value > 0.0 for value in scales.values())


def test_paired_games_use_same_strata_and_preserve_hash_metadata() -> None:
    games = build_paired_games(
        candidate_package=P1_PACKAGE,
        candidate_id="cg-p1-cem-g00-c00-test",
        config_sha256=P1ParameterConfig.default().config_sha256(),
        split=SPLIT,
        train_block_index=0,
        games_per_opponent_seat=1,
        base_seed=123,
    )
    assert len(games) == 16  # four train refs × two seats × one repetition × two arms
    candidate = [game for game in games if game.metadata["arm_role"] == "candidate"]
    control = [game for game in games if game.metadata["arm_role"] == "p1_control"]
    assert len(candidate) == len(control) == 8
    assert {game.metadata["pair_key"] for game in candidate} == {game.metadata["pair_key"] for game in control}
    assert all(game.metadata["split"] == "META_TRAIN" for game in games)
    assert all(game.metadata["config_sha256"] for game in candidate)
    assert all(game.metadata["schema_version"] == "meta-specialist-root-cg-candidate-arena-v1" for game in games)
    assert all(game.metadata["cem_schema"] == "cg-p1-cem-game-v1" for game in games)
    assert all(game.metadata["pool_root"].endswith("/opponents") for game in games)
    assert all(len(game.metadata["pool_manifest_sha256"]) == 64 for game in games)


def test_candidate_result_keeps_control_and_fault_breakdown() -> None:
    rows = [
        {"policy_id": "candidate", "opponent_id": "a", "outcome": "win", "seat": 0},
        {"policy_id": "candidate", "opponent_id": "a", "outcome": "loss", "seat": 1},
        {"policy_id": "p1-control", "opponent_id": "a", "outcome": "draw", "seat": 0},
        {"policy_id": "p1-control", "opponent_id": "a", "outcome": "loss", "seat": 1},
    ]
    result = candidate_result_from_rows(
        rows,
        candidate_policy_id="candidate",
        control_policy_id="p1-control",
        weights={"a": 1.0},
        config=P1ParameterConfig.default(),
        candidate_id="candidate",
    )
    assert result["candidate"]["requested_games"] == 2
    assert result["control"]["requested_games"] == 2
    assert result["candidate_id"] == "candidate"


def test_repeat_result_binds_shared_control_before_delta_calculation() -> None:
    repeat = {
        "candidate": {"objective": 0.30},
        "control": {"objective": -1.0},
        "delta_objective": 1.30,
    }

    bound = _bind_repeat_control(
        repeat,
        {"objective": 0.20, "requested_games": 96},
        control_block_id="control-reeval-r00",
    )

    assert bound["control"]["objective"] == 0.20
    assert bound["delta_objective"] == 0.10
    assert bound["control_block_id"] == "control-reeval-r00"


def test_robust_search_plan_uses_all_train_refs_at_two_repetitions() -> None:
    refs, repetitions, mode = _search_plan(SPLIT, generation=2, all_train_refs=True)
    assert refs == SPLIT.ids("META_TRAIN")
    assert len(refs) == 12
    assert repetitions == 2
    assert mode == "META_TRAIN_ALL"


def test_default_search_plan_keeps_rotating_four_ref_blocks() -> None:
    refs, repetitions, mode = _search_plan(SPLIT, generation=2, all_train_refs=False)
    assert refs == SPLIT.train_blocks[2]
    assert len(refs) == 4
    assert repetitions == 6
    assert mode == "META_TRAIN_BLOCK_2"


def test_expanded_search_plan_can_include_dev_refs_without_touching_final() -> None:
    refs, repetitions, mode = _search_plan(
        SPLIT,
        generation=0,
        all_train_refs=True,
        include_dev_refs=True,
    )
    assert refs == SPLIT.ids("META_TRAIN") + SPLIT.ids("META_DEV")
    assert len(refs) == 18
    assert not set(refs) & set(SPLIT.ids("META_FINAL"))
    assert repetitions == 2
    assert mode == "META_TRAIN_PLUS_DEV"


def test_initial_config_loader_reads_parameterized_candidate_manifest(tmp_path: Path) -> None:
    config = P1ParameterConfig.default()
    path = tmp_path / "candidate-manifest.json"
    path.write_text(json.dumps({"config": config.as_dict()}), encoding="utf-8")
    loaded = _load_initial_config(path)
    assert loaded == config


def test_custom_control_package_gets_distinct_policy_identity() -> None:
    policy_id, policy_sha = _control_identity(G01_PACKAGE)
    assert policy_id.startswith("cg-control-")
    assert len(policy_sha) == 64
    games = build_paired_games(
        candidate_package=P1_PACKAGE,
        candidate_id="candidate",
        config_sha256=P1ParameterConfig.default().config_sha256(),
        split=SPLIT,
        train_block_index=0,
        games_per_opponent_seat=1,
        base_seed=123,
        control_package=G01_PACKAGE,
    )
    assert {game.policy_id for game in games} == {"candidate", policy_id}


def test_repeated_reevaluation_can_override_block_identity() -> None:
    games = build_paired_games(
        candidate_package=P1_PACKAGE,
        candidate_id="candidate",
        config_sha256=P1ParameterConfig.default().config_sha256(),
        split=SPLIT,
        train_block_index=0,
        games_per_opponent_seat=1,
        base_seed=123,
        block_id="cg-p1-cem-candidate-reeval-r1",
    )
    assert {game.block_id for game in games} == {"cg-p1-cem-candidate-reeval-r1"}


def test_reevaluated_elite_rows_drive_distribution_update() -> None:
    result = {
        "candidate_id": "candidate",
        "config": P1ParameterConfig.default().as_dict(),
        "candidate": {"objective": 0.01, "valid": True, "faults": 0},
        "independent_train96": {
            "candidate": {"objective": 0.25, "valid": True, "faults": 0},
        },
    }
    rows = _elite_update_rows([result], use_reeval=True)
    assert rows[0]["objective"] == 0.25
    assert rows[0]["candidate_id"] == "candidate"


def test_positive_delta_gate_filters_non_improving_reevaluated_candidates() -> None:
    result = {
        "candidate_id": "candidate",
        "config": P1ParameterConfig.default().as_dict(),
        "candidate": {"objective": 0.30, "valid": True, "faults": 0},
        "independent_train96": {
            "candidate": {"objective": 0.12, "valid": True, "faults": 0},
            "delta_objective": -0.01,
        },
    }

    rows = _elite_update_rows([result], use_reeval=True, positive_delta_gate=True)

    assert rows == ()


def test_screen_with_only_invalid_candidates_preserves_center() -> None:
    center = P1ParameterConfig.default()
    rows = [
        {
            "candidate_id": "candidate-00",
            "config": center.as_dict(),
            "objective": 0.25,
            "valid": False,
            "faults": 0,
        }
    ]

    elites, selection, scales, valid_count = _select_initial_elites(
        rows,
        elite_count=1,
        center=center,
        scales=None,
    )

    assert elites == ()
    assert selection == "screen_valid_candidates_below_elite_count_preserve_center"
    assert valid_count == 0
    assert scales == update_distribution(center, ({"config": center.as_dict()},))[1]


def test_update_with_too_few_valid_candidates_preserves_center() -> None:
    center = P1ParameterConfig.default()
    rows = [
        {
            "candidate_id": "candidate-positive-valid",
            "config": center.as_dict(),
            "objective": 0.25,
            "valid": True,
            "faults": 0,
        },
        {
            "candidate_id": "candidate-positive-seat-collapse",
            "config": center.as_dict(),
            "objective": 0.50,
            "valid": False,
            "faults": 0,
        },
    ]

    elites, selection = _select_update_elites(
        rows,
        elite_count=2,
        center=center,
    )

    assert selection == "independent_train96_valid_candidates_below_elite_count_preserve_center"
    assert len(elites) == 2
    assert all(item["candidate_id"] == "incumbent-center" for item in elites)
    assert all(item["config"] == center.as_dict() for item in elites)


def test_risk_aware_reevaluation_uses_worst_repeat_and_seat_safety() -> None:
    repeats = (
        {
            "candidate": {
                "objective": 0.30,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.10,
        },
        {
            "candidate": {
                "objective": 0.08,
                "valid": False,
                "faults": 0,
                "seat_collapse": True,
            },
            "control": {"objective": 0.20},
            "delta_objective": -0.12,
        },
    )

    robust = _risk_aware_reevaluation(repeats)

    assert robust["candidate"]["objective"] == 0.08
    assert robust["candidate"]["valid"] is False
    assert robust["candidate"]["seat_collapse"] is True
    assert robust["repeat_objectives"] == [0.30, 0.08]
    assert robust["min_delta_objective"] == -0.12


def test_risk_aware_reevaluation_rejects_repeat_seat_gap_above_gate() -> None:
    repeats = (
        {
            "candidate": {
                "objective": 0.20,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
                "seat_rates": {"0": 0.20, "1": 0.26},
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.0,
        },
        {
            "candidate": {
                "objective": 0.22,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
                "seat_rates": {"0": 0.25, "1": 0.25},
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.02,
        },
    )

    robust = _risk_aware_reevaluation(repeats)

    assert robust["candidate"]["valid"] is True
    assert robust["candidate"]["objective"] == 0.19
    assert robust["seat_safe"] is False
    assert robust["repeat_seat_gaps"] == [0.06, 0.0]


def test_risk_aware_reevaluation_never_rewards_safe_seat_gap() -> None:
    repeats = (
        {
            "candidate": {
                "objective": 0.20,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
                "seat_rates": {"0": 0.20, "1": 0.24},
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.0,
        },
        {
            "candidate": {
                "objective": 0.22,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
                "seat_rates": {"0": 0.25, "1": 0.22},
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.02,
        },
    )

    robust = _risk_aware_reevaluation(repeats)

    assert robust["seat_safe"] is True
    assert robust["seat_gap_penalty"] == 0.0
    assert robust["candidate"]["objective"] == 0.20


def test_risk_aware_reevaluation_penalizes_opponent_seat_gap() -> None:
    repeats = (
        {
            "candidate": {
                "objective": 0.20,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
                "seat_rates": {"0": 0.20, "1": 0.24},
                "opponent_seat_rates": {
                    "hard": {"0": 0.00, "1": 0.20},
                    "easy": {"0": 0.25, "1": 0.25},
                },
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.0,
        },
        {
            "candidate": {
                "objective": 0.22,
                "valid": True,
                "faults": 0,
                "seat_collapse": False,
                "seat_rates": {"0": 0.25, "1": 0.25},
                "opponent_seat_rates": {
                    "hard": {"0": 0.20, "1": 0.20},
                    "easy": {"0": 0.25, "1": 0.25},
                },
            },
            "control": {"objective": 0.20},
            "delta_objective": 0.02,
        },
    )

    robust = _risk_aware_reevaluation(repeats)

    assert robust["opponent_seat_safe"] is False
    assert robust["opponent_seat_gap_penalty"] == 0.15
    assert robust["candidate"]["objective"] == 0.05


def test_risk_aware_elite_rows_use_conservative_candidate_objective() -> None:
    result = {
        "candidate_id": "candidate",
        "config": P1ParameterConfig.default().as_dict(),
        "candidate": {"objective": 0.01, "valid": True, "faults": 0},
        "independent_train96": {
            "candidate": {"objective": 0.25, "valid": True, "faults": 0},
            "risk_aware": {
                "candidate": {"objective": 0.11, "valid": True, "faults": 0},
            },
        },
    }

    rows = _elite_update_rows([result], use_reeval=True, risk_aware=True)

    assert rows[0]["objective"] == 0.11
