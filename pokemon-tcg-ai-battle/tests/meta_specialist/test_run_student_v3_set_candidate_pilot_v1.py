from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest

from scripts.run_student_v3_set_candidate_pilot_v1 import (
    _MODEL_CACHE,
    _load_student_v3_policy_factory_v1,
    build_student_v3_candidate_artifact_v1,
    StudentV3CandidateArtifactV1,
    build_student_v3_candidate_games_v1,
    load_student_v3_candidate_artifact_v1,
    run_student_v3_candidate_game_v1,
    summarize_student_v3_candidate_rows_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1


ROOT = Path(__file__).resolve().parents[2]
REFERENCE_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
POOL_ROOT = ROOT / "opponents"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_candidate(tmp_path: Path) -> tuple[Path, StudentV3CandidateArtifactV1]:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    checkpoint = model_dir / "best.pt"
    checkpoint.write_bytes(b"strict-student-v3-checkpoint")
    bridge = tmp_path / "bridge-manifest.json"
    bridge_payload = {"bridge_sha256": "d" * 64}
    bridge.write_text(json.dumps(bridge_payload, sort_keys=True), encoding="utf-8")
    dataset_manifest = tmp_path / "dataset-manifest.json"
    dataset_manifest_payload = {
        "purpose": "DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY",
        "catalog_sha256": "b" * 64,
        "bridge_manifest_path": str(bridge),
        "bridge_manifest_sha256": _sha(bridge),
        "bridge_sha256": "d" * 64,
        "synthetic_test_only": False,
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }
    dataset_manifest.write_text(
        json.dumps(dataset_manifest_payload, sort_keys=True), encoding="utf-8"
    )
    summary = model_dir / "training_summary.json"
    summary_payload = {
        "schema_version": "offline-scaleup-student-v3-set-v1",
        "purpose": "DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY",
        "objective_kind": "AWR_FINE_TUNE",
        "dataset_manifest_sha256": _sha(dataset_manifest),
        "catalog_sha256": "b" * 64,
        "weight_sidecar_sha256": "c" * 64,
        "best_checkpoint_sha256": _sha(checkpoint),
        "model_config": {"max_count": 2},
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }
    summary.write_text(json.dumps(summary_payload, sort_keys=True), encoding="utf-8")
    deck = ROOT / "deck.csv"
    qualification = (
        ROOT
        / "runs/final-sprint-autonomous/submission-root-deck-qualification-v1/qualification.json"
    )
    qualification_payload = json.loads(qualification.read_text(encoding="utf-8"))
    payload = {
        "schema_version": "meta-specialist-student-v3-candidate-artifact-v2",
        "candidate_id": "student-v3-fixture",
        "model_dir": str(model_dir),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _sha(checkpoint),
        "training_summary_path": str(summary),
        "training_summary_sha256": _sha(summary),
        "deck_path": str(deck),
        "deck_sha256": _sha(deck),
        "dataset_manifest_path": str(dataset_manifest),
        "dataset_manifest_sha256": _sha(dataset_manifest),
        "bridge_manifest_path": str(bridge),
        "bridge_manifest_sha256": _sha(bridge),
        "bridge_sha256": "d" * 64,
        "teacher_catalog_sha256": "b" * 64,
        "target_sidecar_sha256": "c" * 64,
        "objective_kind": "AWR_FINE_TUNE",
        "purpose": "DERIVED_MULTI_TEACHER_THETA0_PRETRAIN_ONLY",
        "submission_deck_qualification_path": str(qualification.relative_to(ROOT)),
        "submission_deck_qualification_file_sha256": _sha(qualification),
        "submission_deck_qualification_sha256": qualification_payload[
            "qualification_sha256"
        ],
        "qualified_deck_identity": qualification_payload["qualified_deck_asset"][
            "deck_identity"
        ],
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, load_student_v3_candidate_artifact_v1(path)


def _decision_observation() -> dict[str, object]:
    card = {
        "id": 1,
        "serial": 0,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }
    player = {
        "active": [],
        "asleep": False,
        "bench": [],
        "benchMax": 5,
        "burned": False,
        "confused": False,
        "deckCount": 53,
        "discard": [],
        "hand": [card],
        "handCount": 1,
        "paralyzed": False,
        "poisoned": False,
        "prize": [object() for _ in range(6)],
    }
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [player, player],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "context": 0,
            "maxCount": 2,
            "minCount": 1,
            "option": [
                {"type": 14},
                {"type": 13, "attackId": 1},
                {"type": 7, "index": 0},
            ],
            "type": 0,
        },
        "step": 7,
    }


def _ordered_decision_observation() -> dict[str, object]:
    observation = _decision_observation()
    observation["select"] = {
        **observation["select"],
        "type": 5,
        "context": 34,
    }
    return observation


class _FixedSetModel:
    def eval(self):
        return self

    def __call__(self, state, actions, legal_mask):
        import torch

        del state, legal_mask
        return (
            torch.tensor([[0.0, 4.0, 2.0]], device=actions.device),
            torch.tensor([[0.0, 10.0, 0.0]], device=actions.device),
        )


def _one_game(artifact: StudentV3CandidateArtifactV1):
    pool = load_opponent_pool_v1(POOL_ROOT)
    reference_id = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))["opponent_ids"][0]
    games = build_student_v3_candidate_games_v1(
        candidate=artifact,
        pool=pool,
        reference_ids=(reference_id,),
        games_per_opponent_seat=1,
    )
    return games[0]


def _one_game_metadata(artifact: StudentV3CandidateArtifactV1) -> Mapping[str, object]:
    return _one_game(artifact).metadata


def test_candidate_artifact_is_closed_and_hash_bound(tmp_path: Path) -> None:
    path, artifact = _write_candidate(tmp_path)
    assert artifact.candidate_id == "student-v3-fixture"
    assert artifact.policy_identity_sha256 != artifact.checkpoint_sha256
    assert len(artifact.policy_identity_sha256) == 64
    assert artifact.runtime_closure["closure_sha256"]
    assert artifact.objective_kind == "AWR_FINE_TUNE"
    assert artifact.research_only is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["promotion_authority"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="promotion_authority"):
        load_student_v3_candidate_artifact_v1(path)

    payload["promotion_authority"] = False
    payload["checkpoint_sha256"] = "d" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint SHA"):
        load_student_v3_candidate_artifact_v1(path)


def test_policy_identity_binds_runtime_closure_and_changes_games_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, artifact = _write_candidate(tmp_path)
    original_identity = artifact.policy_identity_sha256
    original_game = _one_game(artifact)
    from mage_ptcg.offline_scaleup import student_v3_set_runtime

    closure = student_v3_set_runtime.student_v3_set_runtime_closure_v1()
    changed = {
        **closure,
        "closure_sha256": "f" * 64,
        "source_sha256s": {
            **closure["source_sha256s"],
            "student_v3_set_runtime": "e" * 64,
        },
    }
    monkeypatch.setattr(
        student_v3_set_runtime,
        "student_v3_set_runtime_closure_v1",
        lambda: changed,
    )
    changed_artifact = load_student_v3_candidate_artifact_v1(artifact.artifact_path)
    changed_game = _one_game(changed_artifact)
    changed_metadata = changed_game.metadata

    assert changed_artifact.policy_identity_sha256 != original_identity
    assert changed_metadata["policy_identity_sha256"] != original_game.metadata[
        "policy_identity_sha256"
    ]
    pool = load_opponent_pool_v1(POOL_ROOT)
    reference_id = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))[
        "opponent_ids"
    ][0]
    before = build_student_v3_candidate_games_v1(
        candidate=artifact,
        pool=pool,
        reference_ids=(reference_id,),
        games_per_opponent_seat=1,
    )[0]
    after = build_student_v3_candidate_games_v1(
        candidate=changed_artifact,
        pool=pool,
        reference_ids=(reference_id,),
        games_per_opponent_seat=1,
    )[0]
    assert before.game_id != after.game_id
    assert before.policy_sha256 != after.policy_sha256
    assert changed_game.game_id != original_game.game_id

    summary = json.loads(artifact.training_summary_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        student_v3_set_runtime,
        "load_set_candidate_ranker",
        lambda _model_dir, _device: (_FixedSetModel(), summary),
    )
    _MODEL_CACHE.clear()
    _load_student_v3_policy_factory_v1(
        changed_artifact,
        schedule_metadata=changed_metadata,
    )
    assert set(_MODEL_CACHE) == {changed_artifact.policy_identity_sha256}


def test_policy_identity_binds_rule_v0_fallback_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, artifact = _write_candidate(tmp_path)
    original_identity = artifact.policy_identity_sha256
    from mage_ptcg.offline_scaleup import student_v3_set_runtime

    closure = student_v3_set_runtime.student_v3_set_runtime_closure_v1()
    changed = {
        **closure,
        "closure_sha256": "f" * 64,
        "source_sha256s": {
            **closure["source_sha256s"],
            "rule_v0": "e" * 64,
        },
    }
    monkeypatch.setattr(
        student_v3_set_runtime,
        "student_v3_set_runtime_closure_v1",
        lambda: changed,
    )

    changed_artifact = load_student_v3_candidate_artifact_v1(
        artifact.artifact_path
    )

    assert changed_artifact.policy_identity_sha256 != original_identity


@pytest.mark.parametrize(
    ("artifact_field", "replacement", "message"),
    [
        ("dataset_manifest_sha256", "d" * 64, "dataset manifest SHA"),
        ("teacher_catalog_sha256", "d" * 64, "teacher_catalog_sha256"),
        ("target_sidecar_sha256", "d" * 64, "target_sidecar_sha256"),
        ("objective_kind", "THETA0_PRETRAIN", "objective_kind"),
        ("purpose", "wrong-purpose", "purpose"),
    ],
)
def test_candidate_artifact_cross_binds_training_summary(
    tmp_path: Path,
    artifact_field: str,
    replacement: object,
    message: str,
) -> None:
    path, _artifact = _write_candidate(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[artifact_field] = replacement
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_student_v3_candidate_artifact_v1(path)


def test_candidate_artifact_requires_exact_summary_selected_best_checkpoint(
    tmp_path: Path,
) -> None:
    path, _artifact = _write_candidate(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    alternate = Path(payload["model_dir"]) / "alternate.pt"
    alternate.write_bytes(Path(payload["checkpoint_path"]).read_bytes())
    payload["checkpoint_path"] = str(alternate)
    payload["checkpoint_sha256"] = _sha(alternate)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="model_dir/best.pt"):
        load_student_v3_candidate_artifact_v1(path)

    payload["checkpoint_path"] = str(Path(payload["model_dir"]) / "best.pt")
    summary_path = Path(payload["training_summary_path"])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["best_checkpoint_sha256"] = "d" * 64
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    payload["training_summary_sha256"] = _sha(summary_path)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="best_checkpoint_sha256"):
        load_student_v3_candidate_artifact_v1(path)


def test_candidate_artifact_requires_formal_qualified_deck_exact_binding(
    tmp_path: Path,
) -> None:
    path, artifact = _write_candidate(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["submission_deck_qualification_sha256"] = "e" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="qualified deck"):
        load_student_v3_candidate_artifact_v1(path)

    payload["submission_deck_qualification_sha256"] = (
        artifact.submission_deck_qualification_sha256
    )
    payload["submission_deck_qualification_path"] = str(
        artifact.submission_deck_qualification_path
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="repository-relative"):
        load_student_v3_candidate_artifact_v1(path)


def test_candidate_builder_derives_every_primary_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_path, fixture = _write_candidate(tmp_path)
    fixture_path.unlink()
    from mage_ptcg.offline_scaleup import gpu_student_v3_set

    calls: list[tuple[Path, Mapping[str, object]]] = []
    monkeypatch.setattr(
        gpu_student_v3_set,
        "_verify_dataset_manifest",
        lambda directory, manifest: calls.append((directory, manifest)) or {},
    )
    output = tmp_path / "built-candidate.json"

    built = build_student_v3_candidate_artifact_v1(
        candidate_id="student-v3-built",
        model_dir=fixture.model_dir,
        dataset_manifest_path=fixture.dataset_manifest_path,
        submission_deck_qualification_path=(
            fixture.submission_deck_qualification_path
        ),
        output_path=output,
    )

    assert calls and calls[0][0] == fixture.dataset_manifest_path.parent
    assert built.candidate_id == "student-v3-built"
    assert built.checkpoint_sha256 == fixture.checkpoint_sha256
    assert built.policy_identity_sha256 == load_student_v3_candidate_artifact_v1(
        output
    ).policy_identity_sha256
    with pytest.raises(FileExistsError):
        build_student_v3_candidate_artifact_v1(
            candidate_id="student-v3-built",
            model_dir=fixture.model_dir,
            dataset_manifest_path=fixture.dataset_manifest_path,
            submission_deck_qualification_path=(
                fixture.submission_deck_qualification_path
            ),
            output_path=output,
        )


def test_candidate_builder_cli_does_not_start_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fixture_path, fixture = _write_candidate(tmp_path)
    from mage_ptcg.offline_scaleup import gpu_student_v3_set
    import scripts.run_student_v3_set_candidate_pilot_v1 as pilot

    monkeypatch.setattr(
        gpu_student_v3_set,
        "_verify_dataset_manifest",
        lambda _directory, _manifest: {},
    )
    monkeypatch.setattr(
        pilot,
        "run_parallel_cabt_evaluation",
        lambda *_args, **_kwargs: pytest.fail("candidate build started CABT"),
    )
    output = tmp_path / "cli-candidate.json"

    assert pilot.main(
        [
            "--build-candidate-artifact",
            "--candidate-id",
            "student-v3-cli",
            "--model-dir",
            str(fixture.model_dir),
            "--dataset-manifest",
            str(fixture.dataset_manifest_path),
            "--deck-qualification",
            str(fixture.submission_deck_qualification_path),
            "--candidate-output",
            str(output),
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidate_artifact_sha256"] == _sha(output)
    assert report["performance_evidence"] is False
    assert output.is_file()


def test_factory_uses_summary_max_count_and_runs_one_real_runtime_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, artifact = _write_candidate(tmp_path)
    summary = json.loads(artifact.training_summary_path.read_text(encoding="utf-8"))
    from mage_ptcg.offline_scaleup import student_v3_set_runtime

    monkeypatch.setattr(
        student_v3_set_runtime,
        "load_set_candidate_ranker",
        lambda _model_dir, _device: (_FixedSetModel(), summary),
    )
    _MODEL_CACHE.clear()
    factory = _load_student_v3_policy_factory_v1(
        artifact,
        schedule_metadata=_one_game_metadata(artifact),
    )
    agent = factory(None, 7)

    assert agent(_decision_observation()) == [1]


def test_game_runner_writes_ordered_fallback_telemetry_and_summary_strictly_aggregates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, artifact = _write_candidate(tmp_path)
    training_summary = json.loads(
        artifact.training_summary_path.read_text(encoding="utf-8")
    )
    from mage_ptcg.offline_scaleup import student_v3_set_runtime
    import scripts.run_student_v3_set_candidate_pilot_v1 as pilot

    monkeypatch.setattr(
        student_v3_set_runtime,
        "load_set_candidate_ranker",
        lambda _model_dir, _device: (_FixedSetModel(), training_summary),
    )
    _MODEL_CACHE.clear()
    reference_id = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))[
        "opponent_ids"
    ][0]
    game = build_student_v3_candidate_games_v1(
        candidate=artifact,
        pool=load_opponent_pool_v1(POOL_ROOT),
        reference_ids=(reference_id,),
        games_per_opponent_seat=1,
        telemetry_root=tmp_path / "telemetry",
    )[0]

    def fake_run_match(**kwargs):
        subject = kwargs["agent_a_factory"]([1] * 60, 7)
        assert subject(_ordered_decision_observation()) == [0]
        return {
            "status": "DONE",
            "winner": 0,
            "steps": 1,
            "elapsed_seconds": 0.01,
            "cabt_turn": 1,
            "terminal_reason": "fixture",
        }

    monkeypatch.setattr(pilot, "run_match", fake_run_match)
    result = run_student_v3_candidate_game_v1(game.to_payload())
    telemetry_path = Path(game.metadata["student_v3_runtime_telemetry_path"])

    assert result["student_v3_runtime_telemetry"]["fallback_count"] == 1
    assert telemetry_path.is_file()
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
    assert telemetry["game_id"] == game.game_id
    assert telemetry["policy_identity_sha256"] == artifact.policy_identity_sha256
    assert telemetry["fallback_reason_counts"] == {
        "ordered_selection_requires_pointer_head": 1,
    }
    row = {
        "status": "DONE",
        "raw_status": "DONE",
        "outcome": "win",
        "seat": 0,
        "game_id": game.game_id,
        "policy_sha256": game.policy_sha256,
        "metadata": game.metadata,
    }
    evaluation = summarize_student_v3_candidate_rows_v1((row,))
    assert evaluation["student_v3_runtime_telemetry"] == {
        "status": "COMPLETE",
        "observed_games": 1,
        "selection_decision_count": 1,
        "model_decision_count": 0,
        "fallback_count": 1,
        "fallback_reason_counts": {
            "ordered_selection_requires_pointer_head": 1,
        },
        "artifact_file_sha256s": {
            game.game_id: _sha(telemetry_path),
        },
    }

    telemetry["fallback_count"] = 0
    telemetry_path.write_text(json.dumps(telemetry), encoding="utf-8")
    with pytest.raises(ValueError, match="telemetry SHA"):
        summarize_student_v3_candidate_rows_v1((row,))


def test_game_runner_propagates_general_model_error_and_records_no_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, artifact = _write_candidate(tmp_path)
    training_summary = json.loads(
        artifact.training_summary_path.read_text(encoding="utf-8")
    )
    from mage_ptcg.offline_scaleup import student_v3_set_runtime
    import scripts.run_student_v3_set_candidate_pilot_v1 as pilot

    class _ExplodingModel:
        def eval(self):
            return self

        def __call__(self, *_args, **_kwargs):
            raise LookupError("model failure sentinel")

    monkeypatch.setattr(
        student_v3_set_runtime,
        "load_set_candidate_ranker",
        lambda _model_dir, _device: (_ExplodingModel(), training_summary),
    )
    _MODEL_CACHE.clear()
    reference_id = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))[
        "opponent_ids"
    ][0]
    game = build_student_v3_candidate_games_v1(
        candidate=artifact,
        pool=load_opponent_pool_v1(POOL_ROOT),
        reference_ids=(reference_id,),
        games_per_opponent_seat=1,
        telemetry_root=tmp_path / "telemetry",
    )[0]

    def fake_run_match(**kwargs):
        subject = kwargs["agent_a_factory"]([1] * 60, 7)
        subject(_decision_observation())
        raise AssertionError("unreachable")

    monkeypatch.setattr(pilot, "run_match", fake_run_match)

    with pytest.raises(LookupError, match="model failure sentinel"):
        run_student_v3_candidate_game_v1(game.to_payload())

    telemetry_path = Path(game.metadata["student_v3_runtime_telemetry_path"])
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
    assert telemetry["match_status"] == "RUNNER_EXCEPTION"
    assert telemetry["model_decision_count"] == 1
    assert telemetry["fallback_count"] == 0
    assert telemetry["fallback_reason_counts"] == {}


def test_factory_rechecks_runtime_summary_and_schedule_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _path, artifact = _write_candidate(tmp_path)
    summary = json.loads(artifact.training_summary_path.read_text(encoding="utf-8"))
    metadata = dict(_one_game_metadata(artifact))
    from mage_ptcg.offline_scaleup import student_v3_set_runtime

    tampered_schedule = dict(metadata)
    tampered_schedule["dataset_manifest_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="scheduled dataset_manifest_sha256"):
        _load_student_v3_policy_factory_v1(
            artifact,
            schedule_metadata=tampered_schedule,
        )

    runtime_summary = dict(summary)
    runtime_summary["catalog_sha256"] = "d" * 64
    monkeypatch.setattr(
        student_v3_set_runtime,
        "load_set_candidate_ranker",
        lambda _model_dir, _device: (_FixedSetModel(), runtime_summary),
    )
    _MODEL_CACHE.clear()
    with pytest.raises(ValueError, match="runtime summary catalog_sha256"):
        _load_student_v3_policy_factory_v1(
            artifact,
            schedule_metadata=metadata,
        )


def test_common24_game_builder_preserves_both_seats_and_all_hashes(tmp_path: Path) -> None:
    _path, artifact = _write_candidate(tmp_path)
    pool = load_opponent_pool_v1(POOL_ROOT)
    reference_ids = tuple(json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))["opponent_ids"])
    games = build_student_v3_candidate_games_v1(
        candidate=artifact,
        pool=pool,
        reference_ids=reference_ids,
        games_per_opponent_seat=1,
        base_seed=13_000_000,
    )
    assert len(games) == 48
    assert {game.opponent_id for game in games} == set(reference_ids)
    assert {game.seat for game in games} == {0, 1}
    assert len({game.game_id for game in games}) == 48
    assert all(game.policy_sha256 == artifact.policy_identity_sha256 for game in games)
    assert all(game.deck_sha256 == artifact.deck_sha256 for game in games)
    assert all(game.runner_ref.endswith(":run_student_v3_candidate_game_v1") for game in games)
    assert all(game.metadata["teacher_catalog_sha256"] == "b" * 64 for game in games)
    assert all(game.metadata["engine_seed_supported"] is False for game in games)
    assert all(game.metadata["promotion_authority"] is False for game in games)


def test_summary_keeps_faults_in_requested_denominator() -> None:
    rows = (
        {
            "status": "DONE", "outcome": "win", "seat": 0,
            "metadata": {"candidate_id": "c", "purpose": "p"},
        },
        {
            "status": "FAULT", "outcome": "fault", "seat": 1,
            "metadata": {"candidate_id": "c", "purpose": "p"},
        },
    )
    summary = summarize_student_v3_candidate_rows_v1(rows)
    assert summary["requested_games"] == 2
    assert summary["faults"] == 1
    assert summary["score_denominator_games"] == 2
    assert summary["score_rate"] == pytest.approx(0.5)
    assert summary["performance_evidence"] is False
    assert summary["submission_authority"] is False
