from __future__ import annotations


def test_phase_conditioned_screen_is_bounded_and_parallel_by_default() -> None:
    from scripts import run_rule_v0_phase_conditioned_policy_screen_v1 as runner

    assert runner.DEFAULT_WORKERS == 12
    assert runner.DEFAULT_WORKER_RECYCLE_GAMES == 16
    assert runner.POLICY_ID == "rule-v0-phase-conditioned-attack-after-energy-v1"
    assert runner.CANDIDATE_ID != runner.CONTROL_ID


def test_phase_condition_manifest_closes_public_state_and_authority() -> None:
    from scripts import run_rule_v0_phase_conditioned_policy_screen_v1 as runner

    manifest = runner.build_manifest_payload(
        candidate_policy_sha256="a" * 64,
        control_policy_sha256="b" * 64,
        deck_sha256="c" * 64,
        config_sha256="d" * 64,
        selected_ids=("opponent_a",),
    )
    assert manifest["phase_condition"] == {
        "energyAttached": True,
        "turnActionCount_min": 2,
        "action_type": "ATTACK",
        "bonus": 240,
    }
    assert manifest["authority"]["submission_authority"] is False
    assert manifest["public_only"] is True
