import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionError,
    build_meta_distribution_manifest_v1,
    build_meta_schedule_v1,
    load_meta_distribution_manifest_v1,
    save_meta_distribution_manifest_v1,
)


def _sha(char: str) -> str:
    return char * 64


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    ids = ["top", "hard", "diverse", "dev", "final", "teacher"]
    assets = []
    for index, asset_id in enumerate(ids):
        assets.append(
            {
                "pair_id": f"{asset_id}::pair",
                "agent_id": asset_id,
                "archetype": "Archaludon" if asset_id in {"top", "hard", "teacher"} else "Crustle",
                "deck_sha256_raw_file": _sha(str(index + 1)),
                "policy_sha256_raw_main_py": _sha(str(index + 2)),
                "source": "public",
                "source_sha256": _sha(str(index + 3)),
                "usage_boundary": "local_eval_only",
                "smoke_ok": True,
                "runtime_status": "smoke_pass_fast",
                "training_usable": "yes_bounded_local_teacher_collection"
                if asset_id == "teacher"
                else "no_not_authorized_or_not_evidenced",
                "mean_decision_ms": 0.1 + index,
            }
        )
    census = tmp_path / "census.json"
    census.write_text(json.dumps({"schema_version": "census-fixture-v1", "assets": assets}), encoding="utf-8")
    ranking = tmp_path / "ranking.json"
    ranking.write_text(
        json.dumps(
            {
                "schema_version": "ranking-fixture-v1",
                "ranking": [
                    {
                        "asset_id": "top",
                        "score_rate": 0.80,
                        "completed_games": 96,
                        "fault_rate": 0.0,
                        "opponents": {
                            "hard": {"score_rate": 0.20, "completed_games": 16, "fault_rate": 0.0},
                            "diverse": {"score_rate": 0.70, "completed_games": 16, "fault_rate": 0.0},
                        },
                    },
                    {
                        "asset_id": "hard",
                        "score_rate": 0.60,
                        "completed_games": 96,
                        "fault_rate": 0.0,
                        "opponents": {},
                    },
                    {
                        "asset_id": "teacher",
                        "score_rate": 0.55,
                        "completed_games": 96,
                        "fault_rate": 0.0,
                        "opponents": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return census, ranking


def test_manifest_assigns_disjoint_splits_and_normalized_components(tmp_path: Path):
    census, ranking = _write_inputs(tmp_path)
    manifest = build_meta_distribution_manifest_v1(
        census,
        (ranking,),
        candidate_id="top",
        dev_ids=("dev",),
        final_ids=("final",),
    )

    assert manifest.schema_version == "meta-specialist-meta-distribution-v1"
    assert len(manifest.rows) == 6
    assert {row.split for row in manifest.rows} == {"META_TRAIN", "META_DEV", "META_FINAL"}
    assert sum(row.weight for row in manifest.rows) == pytest.approx(1.0)
    assert sum(row.top_meta_component for row in manifest.rows) == pytest.approx(1.0)
    assert sum(row.hard_negative_component for row in manifest.rows) == pytest.approx(1.0)
    assert sum(row.diversity_component for row in manifest.rows) == pytest.approx(1.0)
    hard = next(row for row in manifest.rows if row.opponent_id == "hard")
    diverse = next(row for row in manifest.rows if row.opponent_id == "diverse")
    assert hard.hard_negative_score > diverse.hard_negative_score
    assert next(row for row in manifest.rows if row.opponent_id == "dev").split == "META_DEV"
    assert next(row for row in manifest.rows if row.opponent_id == "final").split == "META_FINAL"


def test_training_schedule_is_permission_filtered_and_deterministic(tmp_path: Path):
    census, ranking = _write_inputs(tmp_path)
    manifest = build_meta_distribution_manifest_v1(
        census,
        (ranking,),
        candidate_id="top",
        dev_ids=("dev",),
        final_ids=("final",),
    )
    schedule = build_meta_schedule_v1(
        manifest, split="META_TRAIN", quota=32, require_training_permission=True
    )
    assert sum(row.count for row in schedule) == 32
    assert {row.opponent_id for row in schedule} == {"teacher"}
    with pytest.raises(MetaDistributionError, match="training permission"):
        build_meta_schedule_v1(manifest, split="META_FINAL", quota=1, require_training_permission=True)
    assert schedule == build_meta_schedule_v1(
        manifest, split="META_TRAIN", quota=32, require_training_permission=True
    )


def test_save_load_hash_binds_sources_and_rejects_tampering(tmp_path: Path):
    census, ranking = _write_inputs(tmp_path)
    manifest = build_meta_distribution_manifest_v1(
        census,
        (ranking,),
        candidate_id="top",
        dev_ids=("dev",),
        final_ids=("final",),
    )
    output = tmp_path / "manifest.json"
    save_meta_distribution_manifest_v1(manifest, output)
    loaded = load_meta_distribution_manifest_v1(output, verify_sources=True)
    assert loaded.rows == manifest.rows
    ranking.write_text(ranking.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(MetaDistributionError, match="source SHA"):
        load_meta_distribution_manifest_v1(output, verify_sources=True)


def test_manifest_rejects_unknown_split_and_bad_scope(tmp_path: Path):
    census, ranking = _write_inputs(tmp_path)
    raw = json.loads(census.read_text(encoding="utf-8"))
    raw["assets"][0]["usage_boundary"] = "training_everything"
    census.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MetaDistributionError, match="usage_boundary"):
        build_meta_distribution_manifest_v1(
            census,
            (ranking,),
            candidate_id="top",
            dev_ids=("dev",),
            final_ids=("final",),
        )


def test_cli_builder_writes_manifest_and_schedule(tmp_path: Path):
    from scripts.build_meta_distribution_manifest_v1 import build_and_write_meta_manifest_v1

    census, ranking = _write_inputs(tmp_path)
    output = tmp_path / "out" / "manifest.json"
    result = build_and_write_meta_manifest_v1(
        census_path=census,
        ranking_paths=(ranking,),
        output_path=output,
        candidate_id="top",
        dev_ids=("dev",),
        final_ids=("final",),
        eval_quota=12,
        train_quota=4,
    )
    assert result["manifest_path"] == str(output)
    assert Path(result["schedule_path"]).is_file()
