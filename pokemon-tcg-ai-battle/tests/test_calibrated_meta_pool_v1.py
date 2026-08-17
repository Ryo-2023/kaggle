from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from mage_ptcg.opponent_ingest.calibrated_meta_pool_v1 import (
    CalibratedMetaPoolError,
    build_calibrated_meta_split_v1,
    build_calibrated_meta_pool_v1,
)


def _write_candidate(root: Path, candidate_id: str, family: str, policy_body: str | None = None) -> dict[str, object]:
    candidate = root / candidate_id
    candidate.mkdir(parents=True, exist_ok=False)
    (candidate / "main.py").write_text(
        policy_body or "def agent(observation, configuration=None):\n    return []\n",
        encoding="utf-8",
    )
    (candidate / "deck.csv").write_text(" ".join(str(value) for value in range(1, 61)) + "\n", encoding="utf-8")
    policy_sha = hashlib.sha256((candidate / "main.py").read_bytes()).hexdigest()
    return {
        "id": candidate_id,
        "canonical_deck_hash": canonical_deck_sha256(range(1, 61)),
        "policy_hash": policy_sha,
        "source_policy_sha256": policy_sha,
        "source": "internal_test_source",
        "source_family": family,
        "smoke_ok": True,
        "usage_boundary": "local_eval_only",
        "derivation_recipe": f"{family}_RECIPE_V1:TEST",
    }


def _write_source_roots(tmp_path: Path, families: tuple[str, ...]) -> tuple[Path, ...]:
    candidates = {
        "A": ("a1", "a2"),
        "B": ("b1", "b2"),
        "C": ("c1",),
    }
    roots: list[Path] = []
    for family in families:
        root = tmp_path / f"source-{family.lower()}"
        root.mkdir()
        rows = [
            _write_candidate(
                root,
                candidate_id,
                family,
                policy_body=f"# {candidate_id}\ndef agent(observation, configuration=None):\n    return []\n",
            )
            for candidate_id in candidates[family]
        ]
        (root / "pool_manifest.json").write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
        roots.append(root)
    return tuple(roots)


def _write_p1(tmp_path: Path) -> Path:
    package = tmp_path / "p1"
    package.mkdir()
    (package / "main.py").write_text("def agent(observation, configuration=None):\n    return []\n", encoding="utf-8")
    (package / "deck.csv").write_text(" ".join(str(value) for value in range(1, 61)) + "\n", encoding="utf-8")
    return package


def _write_ledger(tmp_path: Path, rows: dict[str, tuple[tuple[int, str], ...]]) -> Path:
    path = tmp_path / "calibration-train.ledger.jsonl"
    lines: list[str] = []
    counter = 0
    for opponent_id, outcomes in rows.items():
        for seat, outcome in outcomes:
            lines.append(
                json.dumps(
                    {
                        "game_id": f"g-{counter}",
                        "opponent_id": opponent_id,
                        "seat": seat,
                        "outcome": outcome,
                        "status": "DONE",
                        "raw_status": "DONE",
                        "fault_detail": None,
                        "metadata": {"calibration_scope": "META_TRAIN"},
                    },
                    sort_keys=True,
                )
            )
            counter += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_holdout_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "calibration-holdout.ledger.jsonl"
    rows = [
        {
            "game_id": "holdout-0",
            "opponent_id": "a1",
            "seat": 0,
            "outcome": "win",
            "status": "DONE",
            "raw_status": "DONE",
            "fault_detail": None,
            "split": "META_DEV",
        },
        {
            "game_id": "fault-0",
            "opponent_id": "a1",
            "seat": 1,
            "outcome": "fault",
            "status": "FAULT",
            "raw_status": "FAULT",
            "fault_detail": "timeout",
            "metadata": {"calibration_scope": "META_TRAIN"},
        },
    ]
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return path


def test_selects_train_only_moderate_candidates_with_family_floor_and_seat_support(tmp_path: Path) -> None:
    roots = _write_source_roots(tmp_path, families=("A", "B", "C"))
    p1 = _write_p1(tmp_path)
    ledger = _write_ledger(
        tmp_path,
        rows={
            "a1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
            "a2": ((0, "loss"), (1, "loss")),
            "b1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
            "b2": ((0, "loss"),),
            "c1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
        },
    )
    report = build_calibrated_meta_pool_v1(
        source_roots=roots,
        calibration_ledger_paths=(ledger,),
        output_root=tmp_path / "out",
        p1_package=p1,
        source_epoch="test-epoch",
        seed_namespace="test-seed",
        requested_count=3,
        min_families=3,
    )
    assert report["selected_count"] == 3
    assert set(report["selected_families"]) == {"A", "B", "C"}
    assert "a2" not in report["selected_ids"]
    assert "b2" not in report["selected_ids"]
    fresh = json.loads((tmp_path / "out" / "fresh_meta.json").read_text(encoding="utf-8"))
    assert fresh["authority"]["promotion_allowed"] is False


def test_rejects_holdout_rows_before_consumed_identity_check(tmp_path: Path) -> None:
    roots = _write_source_roots(tmp_path, families=("A",))
    ledger = _write_holdout_ledger(tmp_path)
    with pytest.raises(CalibratedMetaPoolError, match="TRAIN-only"):
        build_calibrated_meta_pool_v1(
            source_roots=roots,
            calibration_ledger_paths=(ledger,),
            output_root=tmp_path / "out",
            p1_package=_write_p1(tmp_path),
            source_epoch="test-epoch",
            seed_namespace="test-seed",
            consumed_policy_sha256=("a" * 64,),
        )


def test_materializes_pool_fresh_meta_split_and_rebinds_hashes(tmp_path: Path) -> None:
    roots = _write_source_roots(tmp_path, families=("A", "B", "C"))
    ledger = _write_ledger(
        tmp_path,
        rows={
            "a1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
            "b1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
            "c1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
        },
    )
    report = build_calibrated_meta_pool_v1(
        source_roots=roots,
        calibration_ledger_paths=(ledger,),
        output_root=tmp_path / "out",
        p1_package=_write_p1(tmp_path),
        source_epoch="test-epoch",
        seed_namespace="test-seed",
        requested_count=3,
        min_families=3,
    )
    pool = json.loads((tmp_path / "out" / "pool_manifest.json").read_text(encoding="utf-8"))
    fresh = json.loads((tmp_path / "out" / "fresh_meta.json").read_text(encoding="utf-8"))
    split = json.loads((tmp_path / "out" / "cg_historical_split.json").read_text(encoding="utf-8"))
    assert report["selected_ids"] == fresh["reference_ids"]
    assert {row["id"] for row in pool} == set(fresh["reference_ids"])
    assert split["evaluation_contract"]["final_results_read_during_search"] is False
    for row in pool:
        policy = tmp_path / "out" / row["id"] / "main.py"
        assert hashlib.sha256(policy.read_bytes()).hexdigest() == row["policy_hash"]


def test_rebinds_promoted_pool_with_calibrated_source_identity(tmp_path: Path) -> None:
    roots = _write_source_roots(tmp_path, families=("A", "B", "C"))
    p1 = _write_p1(tmp_path)
    ledger = _write_ledger(
        tmp_path,
        rows={
            "a1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
            "b1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
            "c1": ((0, "win"), (0, "loss"), (1, "loss"), (1, "loss")),
        },
    )
    built = tmp_path / "out"
    build_calibrated_meta_pool_v1(
        source_roots=roots,
        calibration_ledger_paths=(ledger,),
        output_root=built,
        p1_package=p1,
        source_epoch="test-epoch",
        seed_namespace="test-seed",
        requested_count=3,
        min_families=3,
    )
    promoted = tmp_path / "promoted"
    shutil.copytree(built, promoted)
    (promoted / "meta_manifest.json").unlink()
    (promoted / "cg_historical_split.json").unlink()
    pool_path = promoted / "pool_manifest.json"
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    for row in pool:
        row["smoke_ok"] = True
    pool_path.write_text(json.dumps(pool, sort_keys=True) + "\n", encoding="utf-8")
    fresh_path = promoted / "fresh_meta.json"
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    fresh["pool_manifest_sha256"] = hashlib.sha256(pool_path.read_bytes()).hexdigest()
    fresh_path.write_text(json.dumps(fresh, sort_keys=True) + "\n", encoding="utf-8")
    report = build_calibrated_meta_split_v1(output_root=promoted, p1_package=p1)
    split = json.loads((promoted / "cg_historical_split.json").read_text(encoding="utf-8"))
    meta = json.loads((promoted / "meta_manifest.json").read_text(encoding="utf-8"))
    assert report["status"] == "SEALED"
    assert split["bindings"]["pool_manifest_sha256"] == hashlib.sha256(pool_path.read_bytes()).hexdigest()
    assert meta["source_kind"] == "internal_calibrated_heterogeneous_panel"
    assert load_weekend_split(promoted / "cg_historical_split.json", verify_sources=True).ids("META_TRAIN")
