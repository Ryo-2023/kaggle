"""teacher dataset の占有 cap と、表現できなかった決定の扱い (正典 §9.3)。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import (
    DEFAULT_MATCHUP_CAP_FRACTION_V1,
    CollectTeacherRecordsV1Error,
    TeacherCollectionGameResultV1,
    _collection_manifest_stats_v1,
    _finalize_collection_corpus_v1,
    _initialize_or_validate_collection_contract_v1,
    _initialize_or_validate_collector_source_snapshot_v1,
    _merge_worker_matchup_counts_v1,
    _play_one_game_v1,
    _restore_omissions_v1,
    _restore_game_sidecars_v1,
    _scan_completed_games_v1,
    _write_game_result_sidecar_v1,
    build_teacher_permission_manifest_v1,
    quality_weight_for_v1,
    teacher_source_kind_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import _record_content_hash


def test_a_matchup_under_the_cap_is_not_penalised() -> None:
    assert quality_weight_for_v1(
        opponent_id="a", matchup_counts={"a": 10}, total_records=100
    ) == 1.0


def test_a_matchup_over_the_cap_is_down_weighted_monotonically() -> None:
    """正典 §9.3:「同一 matchup が dataset を占有しないよう cap を設ける」."""
    weights = [
        quality_weight_for_v1(
            opponent_id="a", matchup_counts={"a": share}, total_records=100
        )
        for share in (30, 50, 70, 90)
    ]
    assert weights == sorted(weights, reverse=True), f"not monotonic: {weights}"
    assert weights[0] < 1.0, "a share above the cap must be penalised"


def test_a_dominating_matchup_is_never_zero_weighted() -> None:
    """占有していても 0 にしないこと.

    正典 §9.3 は「leak、fault、illegal、schema 不明がない全ての有効 teacher decision
    を policy target 候補とする」と定める。cap は占有を抑えるためのものであり、
    決定を捨てるためのものではない。``local_dataset_v2`` も quality_weight を
    ``(0, 1]`` に制約する。
    """
    weight = quality_weight_for_v1(
        opponent_id="a", matchup_counts={"a": 100}, total_records=100
    )
    assert 0.0 < weight <= 1.0


def test_the_cap_fraction_is_declared_not_hidden() -> None:
    assert 0.0 < DEFAULT_MATCHUP_CAP_FRACTION_V1 < 1.0


def test_permission_source_kind_matches_public_or_internal_registry_class() -> None:
    from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1

    root = Path(__file__).resolve().parents[2]
    pool = load_opponent_pool_v1(root / "opponents")
    public = pool["tomatomato_archaludon"]
    internal = pool["nihei_alakazam"]
    assert teacher_source_kind_v1(public) == "pooled_external_submission_agent"
    assert teacher_source_kind_v1(internal) == "team_internal_agent"
    assert build_teacher_permission_manifest_v1(
        public, allowed_usages=("training-local",), decision_ref="decision"
    )["source_kind"] == "pooled_external_submission_agent"
    assert build_teacher_permission_manifest_v1(
        internal, allowed_usages=("training-local",), decision_ref="decision"
    )["source_kind"] == "team_internal_agent"


def test_an_empty_dataset_does_not_divide_by_zero() -> None:
    assert quality_weight_for_v1(
        opponent_id="a", matchup_counts={}, total_records=0
    ) == 1.0


def test_worker_qualification_failure_becomes_one_fault_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """一時的な実CABT probe失敗で、他gameの収集結果まで失わない。"""
    from mage_ptcg.meta_specialist import actor_pool_v1
    from mage_ptcg.meta_specialist.decks import DeckQualificationError

    def fail_qualification(**_kwargs: object) -> None:
        raise DeckQualificationError("CABT legality must return (True, nonempty evidence)")

    monkeypatch.setattr(
        actor_pool_v1, "_build_actor_pool_deck_binding_v1", fail_qualification
    )
    root = Path(__file__).resolve().parents[2]
    row = _play_one_game_v1({
        "game_index": 0,
        "deck_path": str(root / "opponents" / "nihei_alakazam" / "deck.csv"),
        "pool_root": str(root / "opponents"),
        "teacher_id": "nihei_alakazam",
        "archetype_id": "alakazam",
        "source_commit": "0" * 40,
        "base_seed": 1,
        "opponent_ids": ["kiyotah_lucario"],
        "run_name": "qualification-fault-fixture",
        "permission_manifest_id": "fixture-permission",
        "max_steps": 10,
        "output_root": str(root / ".tmp-test" / "qualification-fault-fixture"),
        "records_dir": str(root / ".tmp-test" / "qualification-fault-fixture" / "records"),
    })

    assert row["status"] == "faulted"
    assert row["game_index"] == 0
    assert row["n_records"] == 0
    assert row["unlabelled"] == 0
    assert row["omissions"] == []
    assert "DeckQualificationError" in row["detail"]


def test_worker_structural_qualification_failure_is_not_downgraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import actor_pool_v1
    from mage_ptcg.meta_specialist.decks import DeckQualificationError

    def fail_qualification(**_kwargs: object) -> None:
        raise DeckQualificationError("deck is missing core card IDs: [999]")

    monkeypatch.setattr(
        actor_pool_v1, "_build_actor_pool_deck_binding_v1", fail_qualification
    )
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(DeckQualificationError, match="missing core"):
        _play_one_game_v1({
            "game_index": 0,
            "deck_path": str(root / "opponents" / "nihei_alakazam" / "deck.csv"),
            "expected_deck_sha256": __import__("hashlib").sha256(
                (root / "opponents" / "nihei_alakazam" / "deck.csv").read_bytes()
            ).hexdigest(),
            "pool_root": str(root / "opponents"),
            "teacher_id": "nihei_alakazam",
            "archetype_id": "alakazam",
            "source_commit": "0" * 40,
            "base_seed": 1,
            "opponent_ids": ["kiyotah_lucario"],
            "run_name": "qualification-structural-fixture",
            "permission_manifest_id": "fixture-permission",
            "teacher_source_kind": "team_internal_agent",
            "max_steps": 10,
            "output_root": str(root / ".tmp-test" / "qualification-structural-fixture"),
            "records_dir": str(root / ".tmp-test" / "qualification-structural-fixture" / "records"),
        })


def _record(value_target: float | None, *, quality_weight: float = 1.0) -> dict:
    record = {"teacher": {"value_target": value_target, "quality_weight": quality_weight}}
    record["content_hash"] = _record_content_hash(record)
    return record


def test_resume_scan_requires_a_done_value_target_and_valid_self_hash(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    (records / "game-000000.jsonl").write_text(
        json.dumps(_record(1.0)) + "\n", encoding="utf-8"
    )
    (records / "game-000001.jsonl").write_text(
        json.dumps(_record(None)) + "\n", encoding="utf-8"
    )
    assert _scan_completed_games_v1(records) == {0}

    corrupt = _record(-1.0)
    corrupt["teacher"]["quality_weight"] = 0.5
    (records / "game-000002.jsonl").write_text(
        json.dumps(corrupt) + "\n", encoding="utf-8"
    )
    with pytest.raises(CollectTeacherRecordsV1Error, match="content_hash"):
        _scan_completed_games_v1(records)


def test_v2_resume_requires_hash_bound_done_sidecar_and_restores_omissions(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    (tmp_path / "collection_contract.json").write_text(
        '{"schema_version":"specialist-teacher-collection-contract-v2"}\n',
        encoding="utf-8",
    )
    record_path = records / "game-000000.jsonl"
    record_path.write_text(json.dumps(_record(1.0)) + "\n", encoding="utf-8")
    with pytest.raises(CollectTeacherRecordsV1Error, match="without a DONE v2 sidecar"):
        _scan_completed_games_v1(records)

    omission = {
        "episode_id_hash": "e" * 64,
        "decision_index": 4,
        "teacher": {"status": "unavailable"},
    }
    _write_game_result_sidecar_v1(
        records_dir=records,
        game_index=0,
        seed=10,
        seat=0,
        opponent_id="opponent",
        episode_id_hash="e" * 64,
        status="DONE",
        outcome="win",
        record_path=record_path,
        record_count=1,
        unlabelled=1,
        omissions=(omission,),
        detail="",
        subject_deck_sha256="a" * 64,
        teacher_policy_sha256="b" * 64,
        permission_manifest_id="c" * 64,
    )
    assert _scan_completed_games_v1(records) == {0}
    restored = _restore_game_sidecars_v1(records)
    assert restored["unlabelled"] == 1
    assert restored["omissions"] == [omission]

    record_path.write_text(json.dumps(_record(-1.0)) + "\n", encoding="utf-8")
    with pytest.raises(CollectTeacherRecordsV1Error, match="record SHA"):
        _scan_completed_games_v1(records)


def test_v2_global_weight_finalizer_refreshes_the_current_sidecar_hash(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    (tmp_path / "collection_contract.json").write_text(
        '{"schema_version":"specialist-teacher-collection-contract-v2"}\n',
        encoding="utf-8",
    )
    record_path = records / "game-000000.jsonl"
    record_path.write_text(
        json.dumps(_record(1.0, quality_weight=1.0)) + "\n", encoding="utf-8"
    )
    original_sha = hashlib.sha256(record_path.read_bytes()).hexdigest()
    sidecar_path = _write_game_result_sidecar_v1(
        records_dir=records,
        game_index=0,
        seed=10,
        seat=0,
        opponent_id="only-opponent",
        episode_id_hash="e" * 64,
        status="DONE",
        outcome="win",
        record_path=record_path,
        record_count=1,
        unlabelled=0,
        omissions=(),
        detail="",
        subject_deck_sha256="a" * 64,
        teacher_policy_sha256="b" * 64,
        permission_manifest_id="c" * 64,
    )

    result = _finalize_collection_corpus_v1(
        records, opponent_ids=("only-opponent",)
    )

    assert result["rewritten_games"] == 1
    current_sha = hashlib.sha256(record_path.read_bytes()).hexdigest()
    assert current_sha != original_sha
    current_sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert current_sidecar["record_sha256"] == current_sha
    attempts = sorted((tmp_path / "game-attempts").glob("game-000000-attempt-*.json"))
    assert len(attempts) == 2
    latest_attempt = json.loads(attempts[-1].read_text(encoding="utf-8"))
    assert latest_attempt["record_sha256"] == current_sha
    assert latest_attempt["detail"] == "corpus-global matchup weight finalization"
    assert _scan_completed_games_v1(records) == {0}


def test_collection_contract_rejects_run_name_reuse_with_changed_inputs(tmp_path: Path) -> None:
    path = tmp_path / "collection_contract.json"
    contract = {"schema_version": "specialist-teacher-collection-contract-v2", "base_seed": 7}
    first = _initialize_or_validate_collection_contract_v1(path, contract)
    assert first == contract
    assert json.loads(path.read_text(encoding="utf-8")) == contract

    with pytest.raises(CollectTeacherRecordsV1Error, match="contract mismatch"):
        _initialize_or_validate_collection_contract_v1(
            path, {**contract, "base_seed": 8}
        )


def test_collector_source_snapshot_is_preserved_and_tamper_checked(tmp_path: Path) -> None:
    import mage_ptcg.meta_specialist.collect_teacher_records_v1 as module

    expected = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    path = tmp_path / "collector_source_snapshot.py"
    _initialize_or_validate_collector_source_snapshot_v1(
        path, expected_sha256=expected
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected

    path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(CollectTeacherRecordsV1Error, match="source snapshot"):
        _initialize_or_validate_collector_source_snapshot_v1(
            path, expected_sha256=expected
        )


def test_resume_restores_existing_omissions_instead_of_overwriting_them(tmp_path: Path) -> None:
    path = tmp_path / "omissions.jsonl"
    rows = [
        {"episode_id_hash": "a" * 64, "decision_index": 1, "teacher": {"status": "unavailable"}},
        {"episode_id_hash": "b" * 64, "decision_index": 2, "teacher": {"status": "unavailable"}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert _restore_omissions_v1(path) == rows


def test_final_corpus_pass_applies_cap_for_serial_and_parallel_callers(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    path = records / "game-000000.jsonl"
    path.write_text(json.dumps(_record(1.0)) + "\n", encoding="utf-8")

    stats = _finalize_collection_corpus_v1(records, opponent_ids=("only",))
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten["teacher"]["quality_weight"] == pytest.approx(0.1)
    assert rewritten["content_hash"] == _record_content_hash(rewritten)
    assert stats["matchup_counts"] == {"only": 1}
    assert stats["outcome_counts"] == {"win": 1}
    assert stats["records"] == 1


def test_resume_manifest_stats_include_completed_records_and_new_rows() -> None:
    """resume前のrecordをgames_completed/seat_countsから落とさない。"""
    rows = (
        TeacherCollectionGameResultV1(
            game_index=95, seat=1, opponent_id="opponent", status="DONE",
            outcome="win", records=(), unlabelled=0,
        ),
    )

    stats = _collection_manifest_stats_v1(
        already_done=set(range(95)), results=rows, opponent_count=16,
    )

    assert stats == {
        "games_completed": 96,
        "games_faulted": 0,
        "games_other_status": [],
        "seat_counts": {"subject_first": 48, "subject_second": 48},
    }


def test_parallel_worker_rows_are_included_in_manifest_matchup_counts() -> None:
    assert _merge_worker_matchup_counts_v1(
        {"opponent-a": 5},
        (
            {"opponent_id": "opponent-a", "n_records": 3},
            {"opponent_id": "opponent-b", "n_records": 7},
        ),
    ) == {"opponent-a": 8, "opponent-b": 7}


_SMOKE_RUN = (
    Path(__file__).resolve().parents[2]
    / "runs" / "meta-specialist-teacher-records" / "smoke-cap"
)


@pytest.mark.skipif(
    not (_SMOKE_RUN / "teacher_dataset_manifest.json").is_file(),
    reason="no collected teacher run in this checkout",
)
def test_a_real_run_records_its_matchup_distribution_and_omissions() -> None:
    """収集結果が占有分布と omission の所在を残すこと.

    件数だけでは「どの相手が dataset を占めたか」も「どの決定がなぜ落ちたか」も
    後から追えない。
    """
    manifest = json.loads((_SMOKE_RUN / "teacher_dataset_manifest.json").read_text())
    assert manifest["matchup_record_counts"], "per-opponent record counts are missing"
    assert manifest["matchup_cap_fraction"] == DEFAULT_MATCHUP_CAP_FRACTION_V1
    assert Path(manifest["omissions_path"]).is_file(), (
        "the omissions file must exist even when empty, so 'no omissions' is "
        "distinguishable from 'omissions were never recorded'"
    )
