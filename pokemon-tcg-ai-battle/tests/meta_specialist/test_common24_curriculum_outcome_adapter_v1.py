from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mage_ptcg.meta_specialist.common24_curriculum_outcome_adapter_v1 import (
    Common24CurriculumOutcomeAdapterError,
    build_common24_curriculum_outcome_adapter_v1,
    verify_common24_curriculum_outcome_adapter_v1,
)
from mage_ptcg.meta_specialist.dynamic_meta_train_curriculum_v1 import _read_outcomes
from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionManifestV1,
    MetaDistributionRowV1,
    MetaSourceArtifactV1,
    SCHEMA_V1,
    save_meta_distribution_manifest_v1,
)
from mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 import (
    write_student_v3_native_common24_reconciliation_v1,
)
from tests.meta_specialist.test_student_v3_native_common24_reconcile_v1 import (
    CANDIDATE_DECK_SHA,
    CANDIDATE_POLICY_SHA,
    _make_request,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _meta_manifest(root: Path, opponent_ids: list[str]) -> Path:
    source = root / "meta-source.json"
    source.write_text("{}", encoding="utf-8")
    rows = []
    for index, opponent_id in enumerate(opponent_ids):
        split = "META_FINAL" if index == 0 else "META_DEV" if index == 1 else "META_TRAIN"
        rows.append(
            MetaDistributionRowV1(
                opponent_id=opponent_id,
                pair_id=f"pair::{opponent_id}",
                deck_sha256=hashlib.sha256(f"d:{opponent_id}".encode()).hexdigest(),
                policy_sha256=hashlib.sha256(f"p:{opponent_id}".encode()).hexdigest(),
                archetype=f"family-{index % 4}",
                runtime_class="native_fast",
                source="fixture",
                source_sha256=_sha(source),
                usage_boundary="local_eval_only",
                evaluation_allowed=True,
                training_allowed=False,
                behavior_allowed=False,
                submission_allowed=False,
                observed_strength=0.5,
                observed_games=96,
                observed_fault_rate=0.0,
                frequency_proxy=0.5,
                hard_negative_score=0.5,
                diversity_contribution=0.5,
                top_meta_component=0.025,
                hard_negative_component=0.01,
                diversity_component=0.005,
                weight=1.0 / len(opponent_ids),
                split=split,
                runtime_status="smoke_pass_fast",
                evidence_status="fixture",
            )
        )
    manifest = MetaDistributionManifestV1(
        schema_version=SCHEMA_V1,
        candidate_id="fixture-meta",
        sources=(MetaSourceArtifactV1(str(source), _sha(source), "fixture"),),
        rows=tuple(rows),
        component_targets={"top_meta": 0.60, "hard_negative": 0.25, "diversity": 0.15},
        split_ids={
            split: tuple(row.opponent_id for row in rows if row.split == split)
            for split in ("META_TRAIN", "META_DEV", "META_FINAL")
        },
        training_authority=False,
        promotion_authority=False,
        submission_authority=False,
        research_only=True,
        notes=("synthetic fixture",),
    )
    path = root / "meta-manifest.json"
    save_meta_distribution_manifest_v1(manifest, path)
    return path


@pytest.fixture
def synthetic_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    import mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 as reconcile

    monkeypatch.setattr(
        reconcile,
        "_formal_candidate_identity_v1",
        lambda _path: SimpleNamespace(
            candidate_id="student-v3-tomato",
            policy_identity_sha256=CANDIDATE_POLICY_SHA,
            deck_sha256=CANDIDATE_DECK_SHA,
        ),
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_student_v3_set_candidate_pilot_v1.py").write_text(
        "def run_student_v3_candidate_game_v1(): pass\n", encoding="utf-8"
    )
    (scripts / "run_native_policy_candidate_pilot_v1.py").write_text(
        "def run_native_candidate_game_v1(): pass\n", encoding="utf-8"
    )
    request_path, request = _make_request(tmp_path)
    reconciliation_path = tmp_path / "reconciliation.json"
    write_student_v3_native_common24_reconciliation_v1(request_path, reconciliation_path)
    opponents = list(request["protocol"]["opponent_ids"])
    return reconciliation_path, _meta_manifest(tmp_path, opponents)


def test_synthetic_adapter_is_train_only_and_strictly_bound(
    tmp_path: Path, synthetic_sources: tuple[Path, Path]
) -> None:
    reconciliation, meta = synthetic_sources
    output = tmp_path / "adapter"

    built = build_common24_curriculum_outcome_adapter_v1(
        repo_root=tmp_path,
        reconciliation_path=reconciliation,
        meta_manifest_path=meta,
        output_dir=output,
    )
    verified = verify_common24_curriculum_outcome_adapter_v1(
        output / "adapter-manifest.json", tmp_path
    )

    assert verified == built
    assert built["summary"] == {
        "candidate_source_rows": 96,
        "emitted_meta_train_rows": 88,
        "excluded_meta_dev_rows": 4,
        "excluded_meta_final_rows": 4,
        "fault_rows": 0,
        "unique_emitted_game_ids": 88,
    }
    assert {row["split"] for row in built["records"]} == {"META_TRAIN"}
    assert len({row["game_id"] for row in built["records"]}) == 88
    assert all(row["seed"] == row["base_seed"] + row["ordinal"] for row in built["records"])
    assert built["execution_closure"]["candidate_runner"]["source_sha256"] == _sha(
        tmp_path / "scripts/run_student_v3_set_candidate_pilot_v1.py"
    )
    assert built["execution_closure"]["native_runner"]["source_sha256"] == _sha(
        tmp_path / "scripts/run_native_policy_candidate_pilot_v1.py"
    )
    outcomes = _read_outcomes(output / "outcome-ledger.jsonl")
    assert len(outcomes) == 88
    assert {row["opponent_id"] for row in outcomes}.isdisjoint(
        {"opponent-00", "opponent-01"}
    )
    assert built["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
    }


def test_verifier_rejects_heldout_injection_and_source_tamper(
    tmp_path: Path, synthetic_sources: tuple[Path, Path]
) -> None:
    reconciliation, meta = synthetic_sources
    output = tmp_path / "adapter"
    build_common24_curriculum_outcome_adapter_v1(
        repo_root=tmp_path,
        reconciliation_path=reconciliation,
        meta_manifest_path=meta,
        output_dir=output,
    )
    ledger = output / "outcome-ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[0]["opponent_id"] = "opponent-00"
    ledger.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    with pytest.raises(Common24CurriculumOutcomeAdapterError, match="outcome ledger SHA"):
        verify_common24_curriculum_outcome_adapter_v1(
            output / "adapter-manifest.json", tmp_path
        )


def test_cli_builds_and_verifies_synthetic_artifact(
    tmp_path: Path,
    synthetic_sources: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.build_common24_curriculum_outcome_adapter_v1 import main

    reconciliation, meta = synthetic_sources
    output = tmp_path / "cli-adapter"
    assert main(
        [
            "--repo-root", str(tmp_path),
            "--reconciliation", str(reconciliation),
            "--meta-manifest", str(meta),
            "--output-dir", str(output),
        ]
    ) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["emitted_meta_train_rows"] == 88
    assert printed["adapter_manifest_file_sha256"] == _sha(
        output / "adapter-manifest.json"
    )


def test_actual_common24_sources_rebuild_a_train_only_adapter() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = root / (
        "runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/"
        "theta0-common24-96-v2/adapter-manifest.json"
    )
    verified = verify_common24_curriculum_outcome_adapter_v1(manifest, root)
    assert verified["summary"]["candidate_source_rows"] == 96
    assert verified["summary"]["emitted_meta_train_rows"] == 80
    assert verified["summary"]["excluded_meta_dev_rows"] == 0
    assert verified["summary"]["excluded_meta_final_rows"] == 16
    assert {row["split"] for row in verified["records"]} == {"META_TRAIN"}
