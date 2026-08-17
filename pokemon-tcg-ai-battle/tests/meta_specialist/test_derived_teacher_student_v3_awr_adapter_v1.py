from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def aligned_artifacts(tmp_path_factory: pytest.TempPathFactory):
    helper_path = ROOT / "tests/meta_specialist/test_derived_teacher_catalog_v1.py"
    helper_spec = importlib.util.spec_from_file_location(
        "derived_teacher_catalog_fixture", helper_path
    )
    if helper_spec is None or helper_spec.loader is None:
        raise RuntimeError("could not load the catalog fixture module")
    helper_module = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper_module)
    _ready_v2_fixture = helper_module._ready_v2_fixture
    import mage_ptcg.meta_specialist.derived_teacher_catalog_v1 as catalog_module
    from mage_ptcg.meta_specialist.derived_teacher_awr_artifact_v1 import (
        build_derived_teacher_awr_artifact_v1,
    )
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        build_teacher_snapshot_student_v3_bridge_v1,
    )
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import build_set_dataset

    workspace = tmp_path_factory.mktemp("student-v3-awr-adapter")
    fixture = _ready_v2_fixture(workspace)
    original_teachers = catalog_module._TEACHERS
    catalog_module._TEACHERS = (fixture["spec"],)
    try:
        catalog_path = fixture["root"] / "catalog-v2.json"
        catalog = catalog_module.build_derived_teacher_catalog_v1(
            fixture["root"], output_path=catalog_path
        )
        awr_dir = fixture["root"] / "awr"
        awr_manifest_path = awr_dir / "manifest.json"
        awr_weights_path = awr_dir / "weights.jsonl"
        build_derived_teacher_awr_artifact_v1(
            repo_root=fixture["root"],
            catalog_path=catalog_path,
            output_sidecar_path=awr_weights_path,
            output_manifest_path=awr_manifest_path,
            fold_count=2,
        )
        bridge_dir = fixture["root"] / "bridge"
        source_path = bridge_dir / "student-v3-source.jsonl"
        bridge_manifest_path = bridge_dir / "manifest.json"
        bridge_manifest = build_teacher_snapshot_student_v3_bridge_v1(
            repo_root=fixture["root"],
            catalog_path=catalog_path,
            output_dataset_path=source_path,
            output_manifest_path=bridge_manifest_path,
        )
        assert bridge_manifest["performance_training_ready"] is True
        gpu_dir = fixture["root"] / "gpu"
        synthetic_gpu_dir = fixture["root"] / "gpu-synthetic"
        gpu_manifest = build_set_dataset(
            source=source_path,
            output_dir=synthetic_gpu_dir,
            shard_size=32,
            synthetic_test_only=True,
        )
        # Attach the already-formal bridge receipt without depending on the
        # concurrently evolving bridge→GPU construction helper.
        gpu_manifest["bridge_manifest_path"] = str(bridge_manifest_path.resolve())
        gpu_manifest["bridge_manifest_sha256"] = _file_sha(bridge_manifest_path)
        gpu_manifest["bridge_sha256"] = bridge_manifest["bridge_sha256"]
        gpu_manifest["selected_teacher_ids"] = bridge_manifest[
            "selected_teacher_ids"
        ]
        gpu_manifest["synthetic_test_only"] = False
        gpu_manifest["dataset_sha256"] = hashlib.sha256(
            b"offline-scaleup-gpu-set-dataset-v1\0"
            + _canonical(
                {
                    key: value
                    for key, value in gpu_manifest.items()
                    if key != "dataset_sha256"
                }
            )
        ).hexdigest()
        synthetic_gpu_dir.rename(gpu_dir)
        (gpu_dir / "manifest.json").write_bytes(_canonical(gpu_manifest))
        yield {
            **fixture,
            "catalog_path": catalog_path,
            "catalog": catalog,
            "awr_manifest_path": awr_manifest_path,
            "awr_weights_path": awr_weights_path,
            "source_path": source_path,
            "gpu_dir": gpu_dir,
            "gpu_manifest": gpu_manifest,
        }
    finally:
        catalog_module._TEACHERS = original_teachers


def test_adapter_emits_only_raw_awr_weight_for_every_v3_train_record(
    aligned_artifacts: dict[str, object],
) -> None:
    try:
        from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
            build_derived_teacher_student_v3_awr_sidecar_v1,
        )
    except ModuleNotFoundError:
        pytest.fail("derived teacher Student v3 AWR adapter is not implemented")
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        _examples,
        load_training_weight_sidecar,
    )

    output = aligned_artifacts["root"] / "adapter-output.json"
    receipt = build_derived_teacher_student_v3_awr_sidecar_v1(
        repo_root=aligned_artifacts["root"],
        awr_manifest_path=aligned_artifacts["awr_manifest_path"],
        gpu_dataset_dir=aligned_artifacts["gpu_dir"],
        catalog_path=aligned_artifacts["catalog_path"],
        expected_catalog_file_sha256=_file_sha(aligned_artifacts["catalog_path"]),
        output_path=output,
    )

    raw = output.read_bytes()
    payload = json.loads(raw)
    _rows, gpu_metadata_by_split, _selected = _join_fixture_inputs(
        aligned_artifacts
    )
    train_metadata = gpu_metadata_by_split["train"]
    train_ids = [row["record_id"] for row in train_metadata]
    assert raw == _canonical(payload)
    assert not raw.endswith(b"\n")
    assert set(payload) == {
        "schema_version",
        "objective_kind",
        "dataset_manifest_sha256",
        "catalog_sha256",
        "weights",
        "authority",
    }
    assert [row["record_id"] for row in payload["weights"]] == sorted(train_ids)
    assert all(set(row) == {"record_id", "weight"} for row in payload["weights"])
    assert b"effective_weight" not in raw
    assert b"example_quality_weight" not in raw
    assert payload["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    manifest_sha = _file_sha(aligned_artifacts["gpu_dir"] / "manifest.json")
    assert payload["dataset_manifest_sha256"] == manifest_sha
    assert payload["catalog_sha256"] == aligned_artifacts["catalog"][
        "catalog_sha256"
    ]
    joined, stats = load_training_weight_sidecar(
        output,
        dataset_manifest_sha256=manifest_sha,
        catalog_sha256=payload["catalog_sha256"],
        train_record_ids=train_ids,
    )
    assert len(joined) == aligned_artifacts["gpu_manifest"]["records"]["train"]
    assert receipt["output_sha256"] == _file_sha(output)
    assert receipt["rows"] == len(joined) == stats["joined_train_records"]
    assert receipt["selected_teacher_ids"] == aligned_artifacts["gpu_manifest"][
        "selected_teacher_ids"
    ]
    assert receipt["selected_records"] == sum(
        aligned_artifacts["gpu_manifest"]["records"].values()
    )
    assert receipt["gpu_records"] == aligned_artifacts["gpu_manifest"]["records"]


def _join_fixture_inputs(aligned_artifacts: dict[str, object]):
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        read_actor_visible_awr_sidecar_v1,
    )
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import SPLITS, _examples
    import torch

    manifest = json.loads(
        aligned_artifacts["awr_manifest_path"].read_text(encoding="utf-8")
    )
    rows = read_actor_visible_awr_sidecar_v1(
        aligned_artifacts["awr_weights_path"],
        expected_sha256=manifest["sidecar"]["sha256"],
    )
    metadata = {
        split: [
            example[6]
            for shard in aligned_artifacts["gpu_manifest"]["shards"]
            if shard["split"] == split
            for example in _examples(
                (
                    torch.load(
                        aligned_artifacts["gpu_dir"] / shard["path"],
                        map_location="cpu",
                        weights_only=True,
                    ),
                )
            )
        ]
        for split in SPLITS
    }
    selected = aligned_artifacts["gpu_manifest"]["selected_teacher_ids"]
    return rows, metadata, selected


def test_join_allows_formal_awr_rows_only_for_nonselected_teachers(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    nonselected = replace(
        rows[0],
        record_id=hashlib.sha256(b"nonselected-record").hexdigest(),
        episode_id=hashlib.sha256(b"nonselected-episode").hexdigest(),
        teacher_id="nonselected_teacher",
    )

    weights = _join_selected_awr_rows_v1(
        awr_rows=(*rows, nonselected),
        gpu_metadata_by_split=metadata,
        selected_teacher_ids=selected,
        catalog_teacher_ids=[*selected, "nonselected_teacher"],
    )

    assert set(weights) == {
        row["record_id"] for row in metadata["train"]
    }
    assert nonselected.record_id not in weights


def test_join_rejects_awr_row_from_teacher_outside_formal_catalog(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    outside = replace(
        rows[0],
        record_id=hashlib.sha256(b"outside-record").hexdigest(),
        episode_id=hashlib.sha256(b"outside-episode").hexdigest(),
        teacher_id="outside_catalog",
    )

    with pytest.raises(DerivedTeacherStudentV3AwrAdapterError, match="formal catalog"):
        _join_selected_awr_rows_v1(
            awr_rows=(*rows, outside),
            gpu_metadata_by_split=metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_join_rejects_duplicate_awr_record_id(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    with pytest.raises(DerivedTeacherStudentV3AwrAdapterError, match="duplicated"):
        _join_selected_awr_rows_v1(
            awr_rows=(*rows, rows[0]),
            gpu_metadata_by_split=metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_join_rejects_duplicate_gpu_record_id(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    duplicate_metadata = deepcopy(metadata)
    duplicate_metadata["train"].append(deepcopy(duplicate_metadata["train"][0]))
    with pytest.raises(DerivedTeacherStudentV3AwrAdapterError, match="duplicated"):
        _join_selected_awr_rows_v1(
            awr_rows=rows,
            gpu_metadata_by_split=duplicate_metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_join_rejects_old_gpu_record_id(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    old_metadata = deepcopy(metadata)
    old_metadata["train"][0]["record_id"] = hashlib.sha256(
        b"old-record-id"
    ).hexdigest()
    with pytest.raises(DerivedTeacherStudentV3AwrAdapterError, match="old AWR"):
        _join_selected_awr_rows_v1(
            awr_rows=rows,
            gpu_metadata_by_split=old_metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_join_rejects_selected_awr_record_missing_from_gpu(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    incomplete_metadata = deepcopy(metadata)
    incomplete_metadata["train"].pop()
    with pytest.raises(
        DerivedTeacherStudentV3AwrAdapterError, match="missing or extra GPU"
    ):
        _join_selected_awr_rows_v1(
            awr_rows=rows,
            gpu_metadata_by_split=incomplete_metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_join_rejects_heldout_awr_record_in_gpu_train(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    train_id = metadata["train"][0]["record_id"]
    heldout_rows = tuple(
        replace(
            row,
            split="development",
            fit_membership=False,
            fold_index=None,
            value_estimation="full_train_heldout",
        )
        if row.record_id == train_id
        else row
        for row in rows
    )
    with pytest.raises(DerivedTeacherStudentV3AwrAdapterError, match="heldout"):
        _join_selected_awr_rows_v1(
            awr_rows=heldout_rows,
            gpu_metadata_by_split=metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_join_rejects_record_content_hash_mismatch(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        _join_selected_awr_rows_v1,
    )

    rows, metadata, selected = _join_fixture_inputs(aligned_artifacts)
    mismatched_metadata = deepcopy(metadata)
    mismatched_metadata["train"][0]["source_record_sha256"] = hashlib.sha256(
        b"different-record-content"
    ).hexdigest()
    with pytest.raises(DerivedTeacherStudentV3AwrAdapterError, match="content_hash"):
        _join_selected_awr_rows_v1(
            awr_rows=rows,
            gpu_metadata_by_split=mismatched_metadata,
            selected_teacher_ids=selected,
            catalog_teacher_ids=selected,
        )


def test_adapter_rejects_wrong_exact_catalog_file_sha_without_output(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        build_derived_teacher_student_v3_awr_sidecar_v1,
    )

    output = aligned_artifacts["root"] / "wrong-catalog-output.json"
    with pytest.raises(
        DerivedTeacherStudentV3AwrAdapterError, match="exact catalog file"
    ):
        build_derived_teacher_student_v3_awr_sidecar_v1(
            repo_root=aligned_artifacts["root"],
            awr_manifest_path=aligned_artifacts["awr_manifest_path"],
            gpu_dataset_dir=aligned_artifacts["gpu_dir"],
            catalog_path=aligned_artifacts["catalog_path"],
            expected_catalog_file_sha256="0" * 64,
            output_path=output,
        )
    assert not output.exists()


def test_cli_fails_closed_for_wrong_catalog_file_sha(
    aligned_artifacts: dict[str, object],
) -> None:
    output = aligned_artifacts["root"] / "cli-wrong-catalog-output.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_derived_teacher_student_v3_awr_adapter_v1.py"),
            "--repo-root",
            str(aligned_artifacts["root"]),
            "--awr-manifest",
            str(aligned_artifacts["awr_manifest_path"]),
            "--gpu-dataset-dir",
            str(aligned_artifacts["gpu_dir"]),
            "--catalog",
            str(aligned_artifacts["catalog_path"]),
            "--catalog-file-sha256",
            "0" * 64,
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}"},
        check=False,
        capture_output=True,
        text=True,
    )
    error = json.loads(result.stdout)
    assert result.returncode == 2
    assert error["error"] == "DerivedTeacherStudentV3AwrAdapterError"
    assert "exact catalog file" in error["message"]
    assert not output.exists()


def test_adapter_rejects_old_bridge_manifest_without_output(
    aligned_artifacts: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        build_derived_teacher_student_v3_awr_sidecar_v1,
    )
    from mage_ptcg.meta_specialist.teacher_snapshot_student_v3_bridge_v1 import (
        TeacherSnapshotStudentV3BridgeError,
    )

    output = aligned_artifacts["root"] / "old-bridge-output.json"

    def reject_old_bridge(_path: Path, _root: Path):
        raise TeacherSnapshotStudentV3BridgeError(
            "bridge manifest has an invalid closed schema"
        )

    monkeypatch.setattr(
        "mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1."
        "verify_teacher_snapshot_student_v3_bridge_manifest_v1",
        reject_old_bridge,
    )
    with pytest.raises(
        DerivedTeacherStudentV3AwrAdapterError, match="does not verify"
    ):
        build_derived_teacher_student_v3_awr_sidecar_v1(
            repo_root=aligned_artifacts["root"],
            awr_manifest_path=aligned_artifacts["awr_manifest_path"],
            gpu_dataset_dir=aligned_artifacts["gpu_dir"],
            catalog_path=aligned_artifacts["catalog_path"],
            expected_catalog_file_sha256=_file_sha(aligned_artifacts["catalog_path"]),
            output_path=output,
        )
    assert not output.exists()


def test_adapter_rejects_cross_catalog_awr_manifest_without_output(
    aligned_artifacts: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        build_derived_teacher_student_v3_awr_sidecar_v1,
    )
    import mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 as adapter

    cross_catalog_manifest = json.loads(
        aligned_artifacts["awr_manifest_path"].read_text(encoding="utf-8")
    )
    cross_catalog_manifest["catalog"] = {
        **cross_catalog_manifest["catalog"],
        "catalog_sha256": hashlib.sha256(b"old-catalog").hexdigest(),
    }
    monkeypatch.setattr(
        adapter,
        "read_derived_teacher_awr_manifest_v1",
        lambda *_args, **_kwargs: cross_catalog_manifest,
    )
    output = aligned_artifacts["root"] / "cross-catalog-output.json"
    with pytest.raises(
        DerivedTeacherStudentV3AwrAdapterError, match="old or cross-catalog"
    ):
        build_derived_teacher_student_v3_awr_sidecar_v1(
            repo_root=aligned_artifacts["root"],
            awr_manifest_path=aligned_artifacts["awr_manifest_path"],
            gpu_dataset_dir=aligned_artifacts["gpu_dir"],
            catalog_path=aligned_artifacts["catalog_path"],
            expected_catalog_file_sha256=_file_sha(aligned_artifacts["catalog_path"]),
            output_path=output,
        )
    assert not output.exists()


def test_adapter_rejects_old_awr_schema_without_output(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        DerivedTeacherStudentV3AwrAdapterError,
        build_derived_teacher_student_v3_awr_sidecar_v1,
    )

    old_manifest = json.loads(
        aligned_artifacts["awr_manifest_path"].read_text(encoding="utf-8")
    )
    old_manifest["schema_version"] = "legacy-awr-v0"
    old_manifest_path = aligned_artifacts["root"] / "old-awr-manifest.json"
    old_manifest_path.write_bytes(_canonical(old_manifest))
    output = aligned_artifacts["root"] / "old-awr-output.json"
    with pytest.raises(
        DerivedTeacherStudentV3AwrAdapterError, match="does not formally verify"
    ):
        build_derived_teacher_student_v3_awr_sidecar_v1(
            repo_root=aligned_artifacts["root"],
            awr_manifest_path=old_manifest_path,
            gpu_dataset_dir=aligned_artifacts["gpu_dir"],
            catalog_path=aligned_artifacts["catalog_path"],
            expected_catalog_file_sha256=_file_sha(aligned_artifacts["catalog_path"]),
            output_path=output,
        )
    assert not output.exists()


def test_adapter_refuses_to_overwrite_existing_sidecar(
    aligned_artifacts: dict[str, object],
) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_student_v3_awr_adapter_v1 import (
        build_derived_teacher_student_v3_awr_sidecar_v1,
    )

    output = aligned_artifacts["root"] / "already-exists.json"
    output.write_bytes(b"do-not-overwrite")
    with pytest.raises(FileExistsError, match="overwrite"):
        build_derived_teacher_student_v3_awr_sidecar_v1(
            repo_root=aligned_artifacts["root"],
            awr_manifest_path=aligned_artifacts["awr_manifest_path"],
            gpu_dataset_dir=aligned_artifacts["gpu_dir"],
            catalog_path=aligned_artifacts["catalog_path"],
            expected_catalog_file_sha256=_file_sha(aligned_artifacts["catalog_path"]),
            output_path=output,
        )
    assert output.read_bytes() == b"do-not-overwrite"
