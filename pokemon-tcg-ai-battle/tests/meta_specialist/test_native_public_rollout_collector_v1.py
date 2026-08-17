from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import build_native_public_rollout_dry_run_v1 as cli
from mage_ptcg.meta_specialist.native_public_rollout_collector_v1 import (
    NativePublicRolloutAuthorizationV1,
    NativePublicRolloutCollectorError,
    NativePublicRolloutIdentityV1,
    PublicRolloutRecordV1,
    build_common24_plan_v1,
    build_native_public_rollout_dry_run_v1,
    materialize_native_public_rollout_dry_run_v1,
    load_native_public_rollout_manifest_v1,
    validate_complete_public_rollout_snapshot_v1,
    validate_public_rollout_records_v1,
)


SHA = "a" * 64


def _identity() -> NativePublicRolloutIdentityV1:
    return NativePublicRolloutIdentityV1(
        candidate_id="tomatomato_archaludon",
        policy_sha256="b" * 64,
        deck_sha256="c" * 64,
        evaluator_sha256="d" * 64,
        engine_sha256="e" * 64,
        runner_sha256="f" * 64,
        pool_manifest_sha256="1" * 64,
        protocol_sha256="2" * 64,
        projection_schema_sha256="3" * 64,
        action_schema_sha256="4" * 64,
        source_commit_sha256="5" * 64,
    )


def _plan():
    opponent_ids = tuple(f"opponent_{index:02d}" for index in range(24))
    families = {opponent_id: f"family_{index % 6}" for index, opponent_id in enumerate(opponent_ids)}
    return build_common24_plan_v1(
        opponent_ids=opponent_ids,
        opponent_families=families,
        base_seed=20260813,
    )


def _unauthorized() -> NativePublicRolloutAuthorizationV1:
    return NativePublicRolloutAuthorizationV1(
        source_kind="pooled_external_submission_agent",
        usage_boundary="local_eval_only",
        owned_policy=False,
        explicit_self_rollout_allowed=False,
        teacher_behavior_allowed=False,
        permission_manifest_id=None,
        permission_content_hash=None,
        decision_ref=None,
        allowed_usages=(),
    )


def _owned() -> NativePublicRolloutAuthorizationV1:
    return NativePublicRolloutAuthorizationV1(
        source_kind="owned_research_policy",
        usage_boundary="training_local",
        owned_policy=True,
        explicit_self_rollout_allowed=False,
        teacher_behavior_allowed=False,
        permission_manifest_id=None,
        permission_content_hash=None,
        decision_ref=None,
        allowed_usages=("audit-local",),
    )


def _write_bound_json(path: Path, payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _bound_local_eval_inputs(tmp_path: Path):
    identity = _identity()
    plan = _plan()
    pool_rows = [
        {
            "id": opponent_id,
            "policy_sha256": identity.policy_sha256,
            "canonical_deck_hash": identity.deck_sha256,
            "usage_boundary": "local_eval_only",
            "family": dict(plan.opponent_families)[opponent_id],
        }
        for opponent_id in plan.opponent_ids
    ]
    pool_path = tmp_path / "pool-manifest.json"
    pool_sha = _write_bound_json(pool_path, pool_rows)
    pool_semantic_sha = hashlib.sha256(
        b"mage_ptcg:native-public-rollout-pool-manifest:v1\0"
        + json.dumps(pool_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    plan = build_common24_plan_v1(
        opponent_ids=plan.opponent_ids,
        opponent_families=dict(plan.opponent_families),
        base_seed=plan.base_seed,
        pool_manifest_path=str(pool_path),
        pool_manifest_sha256=pool_sha,
        pool_manifest_semantic_sha256=pool_semantic_sha,
    )
    identity = replace(identity, pool_manifest_sha256=pool_sha)
    source_path = tmp_path / "source-manifest.json"
    source_sha = _write_bound_json(
        source_path,
        {
            "schema_version": "meta-specialist-meta-distribution-v1",
            "candidate_id": identity.candidate_id,
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "external_execution_authority": False,
            "rows": [
                {
                    "opponent_id": identity.candidate_id,
                    "policy_sha256": identity.policy_sha256,
                    "deck_sha256": identity.deck_sha256,
                    "usage_boundary": "local_eval_only",
                    "behavior_allowed": False,
                    "submission_allowed": False,
                }
            ],
        },
    )
    permission_path = tmp_path / "permission-manifest.json"
    permission_id = "6" * 64
    decision_ref = "decision://native-self-rollout/v1"
    permission_sha = _write_bound_json(
        permission_path,
        {
            "schema_version": "meta-specialist-native-self-rollout-permission-v1",
            "permission_manifest_id": permission_id,
            "candidate_id": identity.candidate_id,
            "policy_sha256": identity.policy_sha256,
            "deck_sha256": identity.deck_sha256,
            "explicit_self_rollout_allowed": True,
            "teacher_behavior_allowed": False,
            "decision_ref": decision_ref,
            "authority": {
                "training_authority": False,
                "promotion_authority": False,
                "submission_authority": False,
                "external_execution_authority": False,
            },
        },
    )
    projection_path = tmp_path / "projection-audit.json"
    projection_sha = _write_bound_json(
        projection_path,
        {
            "schema_version": "meta-specialist-native-public-projection-audit-v1",
            "candidate_id": identity.candidate_id,
            "public_only": True,
            "private_field_scan": {"forbidden_fields": [], "count": 0},
            "source_to_derived_ledger_sha256": "7" * 64,
            "projection_schema_sha256": identity.projection_schema_sha256,
            "action_schema_sha256": identity.action_schema_sha256,
            "authority": {
                "training_authority": False,
                "promotion_authority": False,
                "submission_authority": False,
                "external_execution_authority": False,
            },
        },
    )
    authorization = NativePublicRolloutAuthorizationV1(
        source_kind="pooled_external_submission_agent",
        usage_boundary="local_eval_only",
        owned_policy=False,
        explicit_self_rollout_allowed=True,
        teacher_behavior_allowed=False,
        permission_manifest_id=permission_id,
        permission_content_hash=permission_sha,
        decision_ref=decision_ref,
        allowed_usages=("native-self-rollout-local",),
        source_manifest_path=str(source_path),
        source_manifest_sha256=source_sha,
        permission_manifest_path=str(permission_path),
        permission_manifest_sha256=permission_sha,
        projection_audit_path=str(projection_path),
        projection_audit_sha256=projection_sha,
    )
    return identity, authorization, plan


def test_common24_plan_is_exact_96_and_seed_schedule_is_deterministic():
    first = _plan()
    second = _plan()
    assert len(first.games) == 96
    assert first.games == second.games
    assert {game.seat for game in first.games} == {0, 1}
    assert {game.repetition for game in first.games} == {0, 1}
    assert len({game.game_id for game in first.games}) == 96
    assert all(game.opponent_family.startswith("family_") for game in first.games)


@pytest.mark.parametrize("forbidden", ["private_state", "hidden_cards", "teacher_label", "teacher_action"])
def test_public_rollout_record_rejects_private_and_teacher_fields(forbidden):
    payload = {
        "game_id": "game-0",
        "step_index": 0,
        "seed": 1,
        "seat": 0,
        "opponent_id": "opponent_00",
        "opponent_family": "family_0",
        "state_digest": SHA,
        "action_key": SHA,
        "terminal_outcome": "win",
        forbidden: "must-reject",
    }
    with pytest.raises(NativePublicRolloutCollectorError, match="forbidden|unsupported"):
        PublicRolloutRecordV1.from_mapping(payload)


def test_current_native_local_eval_only_is_not_ready_for_collection_or_evaluation():
    manifest = build_native_public_rollout_dry_run_v1(
        identity=_identity(), authorization=_unauthorized(), plan=_plan()
    )
    assert manifest["status"] == "BLOCKED"
    assert manifest["ready_for_collection"] is False
    assert manifest["ready_for_evaluation"] is False
    assert manifest["teacher_labels_allowed"] is False
    assert manifest["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
    }


def test_unbound_owned_policy_is_blocked_for_collection_and_evaluation():
    manifest = build_native_public_rollout_dry_run_v1(
        identity=_identity(), authorization=_owned(), plan=_plan()
    )
    assert manifest["status"] == "BLOCKED"
    assert manifest["ready_for_collection"] is False
    assert manifest["ready_for_evaluation"] is False
    assert manifest["collection_started"] is False
    assert manifest["records_present"] is False


def test_explicit_permission_requires_research_usage_and_binding():
    with pytest.raises(NativePublicRolloutCollectorError, match="permission"):
        NativePublicRolloutAuthorizationV1(
            source_kind="pooled_external_submission_agent",
            usage_boundary="local_eval_only",
            owned_policy=False,
            explicit_self_rollout_allowed=True,
            teacher_behavior_allowed=False,
            permission_manifest_id="6" * 64,
            permission_content_hash="7" * 64,
            decision_ref="decision.md",
            allowed_usages=("training-local",),
        )


def test_records_must_match_plan_and_exclude_unknown_game():
    game = _plan().games[0]
    record = PublicRolloutRecordV1(
        game_id=game.game_id,
        step_index=0,
        seed=game.seed,
        seat=game.seat,
        opponent_id=game.opponent_id,
        opponent_family=game.opponent_family,
        state_digest=SHA,
        action_key=SHA,
        terminal_outcome="win",
    )
    validate_public_rollout_records_v1(records=(record,), plan=_plan())
    forged = PublicRolloutRecordV1(
        game_id="unknown-game",
        step_index=0,
        seed=game.seed,
        seat=game.seat,
        opponent_id=game.opponent_id,
        opponent_family=game.opponent_family,
        state_digest=SHA,
        action_key=SHA,
        terminal_outcome="win",
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="plan|unknown"):
        validate_public_rollout_records_v1(records=(forged,), plan=_plan())


def test_dry_run_materializer_is_new_root_only_and_does_not_collect(tmp_path: Path):
    output = tmp_path / "run" / "manifest.json"
    result = materialize_native_public_rollout_dry_run_v1(
        output_manifest=output,
        repo_root=tmp_path,
        identity=_identity(),
        authorization=_unauthorized(),
        plan=_plan(),
    )
    assert result["status"] == "BLOCKED"
    assert result["ready_for_evaluation"] is False
    assert result["processes_launched"] is False
    assert json.loads(output.read_text(encoding="utf-8"))["records_present"] is False
    with pytest.raises(FileExistsError):
        materialize_native_public_rollout_dry_run_v1(
            output_manifest=output,
            repo_root=tmp_path,
            identity=_identity(),
            authorization=_unauthorized(),
            plan=_plan(),
        )


def test_cli_execute_is_rejected_without_writing_any_manifest(tmp_path: Path, capsys):
    output = tmp_path / "run" / "manifest.json"
    args = [
        "--output-manifest", str(output),
        "--identity-json", str(tmp_path / "identity.json"),
        "--authorization-json", str(tmp_path / "authorization.json"),
        "--opponents-json", str(tmp_path / "opponents.json"),
        "--base-seed", "1",
        "--execute",
    ]
    assert cli.main(args) == 2
    assert "dry-run only" in capsys.readouterr().err
    assert not output.exists()


def test_dry_run_manifest_roundtrip_and_forged_authority_fail_closed(tmp_path: Path):
    output = tmp_path / "run" / "manifest.json"
    materialize_native_public_rollout_dry_run_v1(
        output_manifest=output,
        repo_root=tmp_path,
        identity=_identity(),
        authorization=_unauthorized(),
        plan=_plan(),
    )
    loaded = load_native_public_rollout_manifest_v1(output)
    assert loaded["status"] == "BLOCKED"
    assert loaded["plan"]["protocol"] == "common24"

    forged = json.loads(output.read_text(encoding="utf-8"))
    forged["authority"]["submission_authority"] = True
    output.write_text(json.dumps(forged, sort_keys=True), encoding="utf-8")
    with pytest.raises(NativePublicRolloutCollectorError, match="hash|authority"):
        load_native_public_rollout_manifest_v1(output)


def test_local_eval_owned_flag_cannot_authorize_without_verified_source_binding():
    authorization = NativePublicRolloutAuthorizationV1(
        source_kind="owned_research_policy",
        usage_boundary="local_eval_only",
        owned_policy=True,
        explicit_self_rollout_allowed=False,
        teacher_behavior_allowed=False,
        permission_manifest_id=None,
        permission_content_hash=None,
        decision_ref=None,
        allowed_usages=("audit-local",),
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="source|permission|binding"):
        build_native_public_rollout_dry_run_v1(
            identity=_identity(), authorization=authorization, plan=_plan()
        )


def test_local_eval_explicit_permission_cannot_use_self_declared_hashes():
    authorization = NativePublicRolloutAuthorizationV1(
        source_kind="pooled_external_submission_agent",
        usage_boundary="local_eval_only",
        owned_policy=False,
        explicit_self_rollout_allowed=True,
        teacher_behavior_allowed=False,
        permission_manifest_id="6" * 64,
        permission_content_hash="7" * 64,
        decision_ref="self-authored",
        allowed_usages=("native-self-rollout-local",),
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="source|permission|binding"):
        build_native_public_rollout_dry_run_v1(
            identity=_identity(), authorization=authorization, plan=_plan()
        )


def test_verified_local_eval_authorization_binds_source_pool_permission_and_projection(tmp_path: Path):
    identity, authorization, plan = _bound_local_eval_inputs(tmp_path)
    manifest = build_native_public_rollout_dry_run_v1(
        identity=identity, authorization=authorization, plan=plan
    )
    assert manifest["status"] == "DRY_RUN"
    assert manifest["ready_for_collection"] is True
    assert manifest["ready_for_evaluation"] is False


def test_verified_manifest_reload_rechecks_root_artifacts(tmp_path: Path):
    identity, authorization, plan = _bound_local_eval_inputs(tmp_path)
    output = tmp_path / "run" / "manifest.json"
    materialize_native_public_rollout_dry_run_v1(
        output_manifest=output,
        repo_root=tmp_path,
        identity=identity,
        authorization=authorization,
        plan=plan,
    )
    source_path = Path(authorization.source_manifest_path)
    source_path.write_bytes(source_path.read_bytes() + b"\n")
    with pytest.raises(NativePublicRolloutCollectorError, match="source|hash"):
        load_native_public_rollout_manifest_v1(output)


def test_local_eval_plan_requires_pool_manifest_binding(tmp_path: Path):
    identity, authorization, plan = _bound_local_eval_inputs(tmp_path)
    unbound = build_common24_plan_v1(
        opponent_ids=plan.opponent_ids,
        opponent_families=dict(plan.opponent_families),
        base_seed=plan.base_seed,
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="pool"):
        build_native_public_rollout_dry_run_v1(
            identity=identity, authorization=authorization, plan=unbound
        )


def test_pool_manifest_rejects_selected_id_not_in_bound_rows(tmp_path: Path):
    identity, authorization, plan = _bound_local_eval_inputs(tmp_path)
    bad_pool = tmp_path / "bad-pool.json"
    rows = [
        {
            "id": opponent_id,
            "policy_sha256": identity.policy_sha256,
            "canonical_deck_hash": identity.deck_sha256,
            "usage_boundary": "local_eval_only",
            "family": dict(plan.opponent_families)[opponent_id],
        }
        for opponent_id in plan.opponent_ids[:-1]
    ] + [
        {
            "id": "not-selected",
            "policy_sha256": identity.policy_sha256,
            "canonical_deck_hash": identity.deck_sha256,
            "usage_boundary": "local_eval_only",
            "family": "family_x",
        }
    ]
    bad_sha = _write_bound_json(bad_pool, rows)
    bad_plan = build_common24_plan_v1(
        opponent_ids=plan.opponent_ids,
        opponent_families=dict(plan.opponent_families),
        base_seed=plan.base_seed,
        pool_manifest_path=str(bad_pool),
        pool_manifest_sha256=bad_sha,
    )
    bad_identity = replace(identity, pool_manifest_sha256=bad_sha)
    with pytest.raises(NativePublicRolloutCollectorError, match="pool|opponent"):
        build_native_public_rollout_dry_run_v1(
            identity=bad_identity, authorization=authorization, plan=bad_plan
        )


def test_complete_snapshot_requires_all_96_games_and_contiguous_steps():
    plan = _plan()
    records = tuple(
        PublicRolloutRecordV1(
            game_id=game.game_id,
            step_index=0,
            seed=game.seed,
            seat=game.seat,
            opponent_id=game.opponent_id,
            opponent_family=game.opponent_family,
            state_digest=SHA,
            action_key=SHA,
            terminal_outcome="win",
            terminal=True,
        )
        for game in plan.games
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="96|complete"):
        validate_complete_public_rollout_snapshot_v1(records=records[:95], plan=plan)
    summary = validate_complete_public_rollout_snapshot_v1(records=records, plan=plan)
    assert summary["games"] == 96
    assert summary["completed_games"] == 96
    assert summary["fault_games"] == 0
    assert summary["fault_denominator"] == 96
    noncontiguous = records + (
        PublicRolloutRecordV1(
            game_id=plan.games[0].game_id,
            step_index=2,
            seed=plan.games[0].seed,
            seat=plan.games[0].seat,
            opponent_id=plan.games[0].opponent_id,
            opponent_family=plan.games[0].opponent_family,
            state_digest=SHA,
            action_key=SHA,
            terminal_outcome="win",
            terminal=True,
        ),
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="contiguous|step"):
        validate_complete_public_rollout_snapshot_v1(records=noncontiguous, plan=plan)


def test_dry_run_rejects_records_until_a_real_complete_snapshot_gate_exists():
    game = _plan().games[0]
    record = PublicRolloutRecordV1(
        game_id=game.game_id,
        step_index=0,
        seed=game.seed,
        seat=game.seat,
        opponent_id=game.opponent_id,
        opponent_family=game.opponent_family,
        state_digest=SHA,
        action_key=SHA,
        terminal_outcome="win",
        terminal=True,
    )
    with pytest.raises(NativePublicRolloutCollectorError, match="dry-run|records"):
        build_native_public_rollout_dry_run_v1(
            identity=_identity(), authorization=_unauthorized(), plan=_plan(), records=(record,)
        )


def test_public_record_fault_status_is_explicit_and_private_fields_remain_forbidden():
    payload = {
        "game_id": "game-0",
        "step_index": 0,
        "seed": 1,
        "seat": 0,
        "opponent_id": "opponent_00",
        "opponent_family": "family_0",
        "state_digest": SHA,
        "action_key": SHA,
        "terminal_outcome": "fault",
        "fault_status": "step_limit",
        "terminal": True,
    }
    record = PublicRolloutRecordV1.from_mapping(payload)
    assert record.fault_status == "step_limit"


def test_materializer_rejects_output_outside_explicit_repo_root(tmp_path: Path):
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside" / "manifest.json"
    with pytest.raises(NativePublicRolloutCollectorError, match="repo|contained"):
        materialize_native_public_rollout_dry_run_v1(
            output_manifest=outside,
            repo_root=repo_root,
            identity=_identity(),
            authorization=_unauthorized(),
            plan=_plan(),
        )
    inside = repo_root / "runs" / "b-route" / "manifest.json"
    result = materialize_native_public_rollout_dry_run_v1(
        output_manifest=inside,
        repo_root=repo_root,
        identity=_identity(),
        authorization=_unauthorized(),
        plan=_plan(),
    )
    assert result["processes_launched"] is False


def test_materializer_requires_repo_root_even_for_blocked_dry_run(tmp_path: Path):
    with pytest.raises(NativePublicRolloutCollectorError, match="repo_root"):
        materialize_native_public_rollout_dry_run_v1(
            output_manifest=tmp_path / "run" / "manifest.json",
            identity=_identity(),
            authorization=_unauthorized(),
            plan=_plan(),
        )
