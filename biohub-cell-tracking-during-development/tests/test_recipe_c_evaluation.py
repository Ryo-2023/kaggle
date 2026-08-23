"""Synthetic tests for the Recipe C official-metric boundary.

These tests deliberately avoid real GEFF/OME-Zarr/ground-truth content.  They
exercise the persistence, hash, ordering, and fixed-panel contracts around the
vendored metric with small fake graphs and monkeypatched metric functions.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import biohub.recipe_c.evaluation as evaluation
from biohub.official_metrics.metrics import EvaluationResult
from biohub.recipe_c.protocol import PANEL_V1, ExperimentSpec, build_selection_lock
from biohub.recipe_c.source import RECIPE_C_SOURCE
from biohub.reproducibility.digest import directory_digest_report
from biohub.reproducibility.gt_guard import prediction_manifest_path


def _prediction(root: Path, sample_id: str, *, payload: bytes = b"prediction") -> Path:
    path = root / f"{sample_id}.geff"
    path.mkdir(parents=True)
    (path / "zarr.json").write_bytes(b'{"synthetic":true}')
    (path / "payload").write_bytes(payload)
    return path


def _manifest(prediction: Path, **overrides: Any) -> Path:
    digest = directory_digest_report(prediction)
    lock = _lock()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "prediction_role": "recipe_c_prediction",
        "prediction_path": prediction.name,
        "prediction_name": prediction.name,
        "selection_lock_id": lock["selection_lock_id"],
        "ground_truth_included": False,
        "ground_truth_inputs": [],
        "manifest_created_at": datetime.now(UTC).isoformat(),
        "directory_sha256": digest["directory_sha256"],
        "files": digest["files"],
        "total_bytes": digest["total_bytes"],
        "hash_algorithm": digest["hash_algorithm"],
        "nodes": 2,
        "edges": 1,
        "forks": 0,
        "source_commit": RECIPE_C_SOURCE.source_commit,
        "config_sha256": RECIPE_C_SOURCE.config_sha256,
        "predictor_sha256_before": lock["predictor_sha256"],
        "predictor_sha256": "e" * 64,
        "predictor_sha256_after": "e" * 64,
        "d4_predictor_sha256_after": "8" * 64,
        "stage_predictor_sha256_after": "7" * 64,
        "primary_checkpoint_sha256": lock["primary_checkpoint_sha256"],
        "secondary_checkpoint_sha256": lock["secondary_checkpoint_sha256"],
        "resolved_device": "cpu",
        "device_candidates": "cpu",
        "patch_spatial_d4": True,
        "patch_builder": True,
        "runtime_role": "live_stage_repo",
        "command_sha256": "3" * 64,
        "execution_argv_sha256": "4" * 64,
        "child_device": "cpu",
        "child_stdout_sha256": "5" * 64,
        "child_stderr_sha256": "6" * 64,
    }
    payload.update(overrides)
    path = prediction_manifest_path(prediction)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _lock() -> dict[str, Any]:
    source = {field.name: getattr(RECIPE_C_SOURCE, field.name) for field in fields(RECIPE_C_SOURCE)}
    return build_selection_lock(
        source,
        Path(__file__).parents[1] / "configs" / "biohub_095_recipe_c.yaml",
        "a" * 40,
        "cpu",
        ExperimentSpec(
            experiment_id="task5_test",
            method_family="recipe_c",
            hypothesis="synthetic boundary",
            expected_gain=0.0,
            cost="test",
            risk="test",
            novelty="test",
            changes="test",
            control_id="control",
            acceptance_criteria="boundary",
        ),
    )


def _task4_receipt(root: Path, lock: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    final_geffs = {sample: f"predictions/{sample}.geff" for sample in PANEL_V1}
    manifests = {sample: f"predictions/{sample}.geff.manifest.json" for sample in PANEL_V1}
    final_counts: dict[str, Any] = {}
    raw_counts: dict[str, Any] = {}
    first_manifest: dict[str, Any] | None = None
    for sample in PANEL_V1:
        prediction = root / final_geffs[sample]
        digest = directory_digest_report(prediction)
        if first_manifest is None:
            first_manifest = json.loads(
                prediction_manifest_path(prediction).read_text(encoding="utf-8")
            )
        counts = {"nodes": 2, "edges": 1, "forks": 0}
        final_counts[sample] = {**counts, **digest}
        raw_counts[sample] = dict(counts)
    payload: dict[str, Any] = {
        "status": "READY",
        "selection_lock_id": lock["selection_lock_id"],
        "source_commit": lock["source_commit"],
        "config_sha256": lock["config_sha256"],
        "predictor_sha256_before": lock["predictor_sha256"],
        "stage_predictor_sha256_after": first_manifest.get("stage_predictor_sha256_after", "a" * 64),
        "d4_predictor_sha256_after": first_manifest.get("d4_predictor_sha256_after", "b" * 64),
        "predictor_sha256_after": first_manifest.get("predictor_sha256_after", "c" * 64),
        "primary_checkpoint_sha256": lock["primary_checkpoint_sha256"],
        "secondary_checkpoint_sha256": lock["secondary_checkpoint_sha256"],
        "sample_ids": list(PANEL_V1),
        "mode": "full",
        "max_frames": None,
        "command": ["python", "predict.py"],
        "command_sha256": first_manifest.get("command_sha256", "d" * 64),
        "execution_argv_sha256": first_manifest.get("execution_argv_sha256", "e" * 64),
        "cwd_role": "repo",
        "pythonpath": "src",
        "resolved_device": "cpu",
        "child_device": "cpu",
        "child_stdout_sha256": first_manifest.get("child_stdout_sha256", "f" * 64),
        "child_stderr_sha256": first_manifest.get("child_stderr_sha256", "1" * 64),
        "device_candidates": [str(first_manifest.get("device_candidates", "cpu"))],
        "runtime_role": "live_stage_repo",
        "patch_flags": {
            "spatial_d4": first_manifest.get("patch_spatial_d4", True),
            "builder": first_manifest.get("patch_builder", True),
            "stage_device_postimage_verified": True,
            "runtime_d4_postimage_verified": True,
            "runtime_builder_postimage_verified": True,
            "predictor_diagnostic_instrumented": False,
            "source_stage_trace_loaded": False,
        },
        "raw_geffs": {sample: f"raw/{sample}.geff" for sample in PANEL_V1},
        "postprocessed_csv": "submission.csv",
        "final_geffs": final_geffs,
        "manifests": manifests,
        "counts": {
            "raw": raw_counts,
            "final": final_counts,
            "publish": {},
            "diagnostic": {},
        },
        "started_at": "2026-08-22T00:00:00+00:00",
        "finished_at": "2026-08-22T00:00:01+00:00",
        "failure": None,
        "diagnostics": "diagnostics.json",
        "diagnostics_sha256": "2" * 64,
        "cuda_equivalence_validated": False,
    }
    diagnostics_path = root / "diagnostics.json"
    if not diagnostics_path.exists():
        diagnostics_path.write_text("{}", encoding="utf-8")
    payload.update(overrides)
    payload["diagnostics_sha256"] = hashlib.sha256(diagnostics_path.read_bytes()).hexdigest()
    return payload


def _panel_inputs(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = tmp_path / "run"
    root.mkdir()
    (root / "predictions").mkdir()
    lock = _lock()
    artifacts: dict[str, Any] = {}
    for sample_id in PANEL_V1:
        prediction = _prediction(root / "predictions", sample_id)
        manifest = _manifest(prediction)
        artifacts[sample_id] = {
            "prediction_path": prediction,
            "manifest_path": manifest,
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "directory_sha256": directory_digest_report(prediction)["directory_sha256"],
            "files": directory_digest_report(prediction)["files"],
            "total_bytes": directory_digest_report(prediction)["total_bytes"],
            "counts": {"nodes": 2, "edges": 1, "forks": 0},
        }
    return root, _task4_receipt(root, lock), artifacts


def _patch_fake_metric(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    fake_prediction = SimpleNamespace(num_nodes=lambda: 2, num_edges=lambda: 1)
    fake_gt = SimpleNamespace(num_nodes=lambda: 2, num_edges=lambda: 1)
    monkeypatch.setattr(evaluation, "_load_graph", lambda path: fake_prediction)

    def fake_open(path: Path, token: Any) -> tuple[Any, dict[str, Any]]:
        events.append("gt")
        opened = datetime.now(UTC)
        return (
            evaluation.GroundTruthOpened(fake_gt, 2.0),
            {
                "prediction_path": str(token.prediction_path),
                "prediction_manifest_path": str(token.manifest_path),
                "prediction_directory_sha256": token.directory_sha256,
                "prediction_files": token.files,
                "prediction_total_bytes": token.total_bytes,
                "prediction_manifest_created_at": token.manifest_created_at,
                "prediction_persisted_at": token.minted_at,
                "ground_truth_path": str(path),
                "ground_truth_opened_at": opened.isoformat(),
                "ordering_enforced_by": "biohub.reproducibility.gt_guard.open_ground_truth",
                "ordering_evidence": (
                    "prediction bytes re-hashed to prediction_directory_sha256 immediately before "
                    "this ground-truth open"
                ),
            },
        )

    monkeypatch.setattr(
        evaluation,
        "_open_gt",
        fake_open,
    )
    monkeypatch.setattr(
        evaluation,
        "validate_prediction_geff",
        lambda path, sample_id, expected_volume_shape_tzyx=None: (
            events.append("validate")
            or {"nodes": 2, "edges": 1, "forks": 0}
        ),
    )

    def fake_evaluate(
        prediction: object,
        gt: object,
        *,
        scale: tuple[float, ...],
        max_distance: float,
    ) -> EvaluationResult:
        events.append("evaluate")
        assert scale == (1.625, 0.40625, 0.40625)
        assert max_distance == 7.0
        return EvaluationResult(1, 0, 0, 0, 0, 0, 2)

    monkeypatch.setattr(evaluation, "evaluate", fake_evaluate)
    monkeypatch.setattr(evaluation, "node_recall", lambda prediction, gt: events.append("recall") or 1.0)
    monkeypatch.setattr(
        evaluation,
        "per_sample_metrics",
        lambda er, n_total, node_recall: (
            events.append("row")
            or {
                "edge_tp": 1,
                "edge_fp": 0,
                "edge_fn": 0,
                "division_tp": er.division_tp,
                "division_fp": er.division_fp,
                "division_fn": er.division_fn,
                "num_pred_nodes": er.num_pred_nodes,
                "node_recall": node_recall,
                "total_node_ratio": 0.0,
                "edge_jaccard": 1.0,
                "adj_edge_jaccard": 1.0,
            }
        ),
    )
    monkeypatch.setattr(
        evaluation,
        "summarise",
        lambda rows: events.append("summary")
        or {
            "edge_jaccard": 1.0,
            "adj_edge_jaccard": 1.0,
            "division_jaccard": float("nan"),
            "score": 1.0,
        },
    )


def test_prediction_manifest_and_gt_open_order_is_structural(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)

    receipt = evaluation.evaluate_locked_prediction(
        prediction,
        tmp_path / "ground-truth.geff",
        _lock(),
    )

    assert events == ["validate", "gt", "evaluate", "recall", "row", "summary"]
    assert receipt["status"] == "READY"
    assert receipt["gt_open_receipt"]["ordering_enforced_by"] == "biohub.reproducibility.gt_guard.open_ground_truth"
    assert receipt["division_jaccard"] is None
    assert receipt["division_term_live"] is False


def test_full_volume_bounds_are_passed_to_prediction_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    observed_shapes: list[tuple[int, int, int, int] | None] = []
    original_validate = evaluation.validate_prediction_geff

    def capture_shape(path: Path, sample_id: str, *, expected_volume_shape_tzyx: Any = None) -> dict[str, int]:
        observed_shapes.append(expected_volume_shape_tzyx)
        return original_validate(path, sample_id, expected_volume_shape_tzyx=expected_volume_shape_tzyx)

    monkeypatch.setattr(evaluation, "validate_prediction_geff", capture_shape)
    evaluation.evaluate_locked_prediction(prediction, tmp_path / "ground-truth.geff", _lock())
    assert observed_shapes == [(100, 64, 256, 256)]


def test_prediction_snapshot_precedes_gt_and_token_catches_snapshot_toctou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    ground_truth = tmp_path / "ground-truth.geff"
    ground_truth.mkdir()
    events: list[str] = []
    real_open = evaluation._open_gt
    _patch_fake_metric(monkeypatch, events)
    fake_prediction = SimpleNamespace(num_nodes=lambda: 2, num_edges=lambda: 1)
    snapshot_calls = 0

    def snapshot_loader(path: Path) -> Any:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 1:
            (prediction / "payload").write_bytes(b"mutated-after-snapshot")
        return fake_prediction

    monkeypatch.setattr(evaluation, "_load_graph", snapshot_loader)
    monkeypatch.setattr(
        evaluation,
        "_load_ground_truth",
        lambda path: evaluation.GroundTruthOpened(fake_prediction, 2.0),
    )
    monkeypatch.setattr(evaluation, "_open_gt", real_open)

    with pytest.raises(ValueError, match=r"changed|digest|prediction"):
        evaluation.evaluate_locked_prediction(prediction, ground_truth, _lock())
    assert snapshot_calls == 1
    assert "evaluate" not in events


def test_gt_guard_receipt_requires_exact_identity_and_requested_gt_path(tmp_path: Path) -> None:
    prediction = tmp_path / "44b6_0113de3b.geff"
    manifest = tmp_path / "44b6_0113de3b.geff.manifest.json"
    created = "2026-08-22T00:00:00+00:00"
    artifact = {
        "prediction_path": prediction,
        "manifest_path": manifest,
        "directory_sha256": "a" * 64,
        "files": 2,
        "total_bytes": 10,
        "manifest_created_at": created,
    }
    requested_gt = tmp_path / "requested.gt.geff"
    guard = {
        "prediction_path": str(prediction),
        "prediction_manifest_path": str(manifest),
        "prediction_directory_sha256": "a" * 64,
        "prediction_files": 2,
        "prediction_total_bytes": 10,
        "prediction_manifest_created_at": created,
        "prediction_persisted_at": "2026-08-22T00:00:01+00:00",
        "ground_truth_path": str(tmp_path / "other.gt.geff"),
        "ground_truth_opened_at": "2026-08-22T00:00:02+00:00",
        "ordering_enforced_by": "biohub.reproducibility.gt_guard.open_ground_truth",
        "ordering_evidence": (
            "prediction bytes re-hashed to prediction_directory_sha256 immediately "
            "before this ground-truth open"
        ),
        "score": 0.99,
    }
    with pytest.raises(ValueError, match=r"ground.?truth|unknown|schema|identity"):
        evaluation._validate_guard_receipt(guard, artifact, PANEL_V1[0], requested_gt)


def test_task4_handoff_requires_full_panel_mode_and_public_receipt_schema() -> None:
    payload = {
        "status": "READY",
        "selection_lock_id": _lock()["selection_lock_id"],
        "sample_ids": list(PANEL_V1),
        "final_geffs": {sample: f"{sample}.geff" for sample in PANEL_V1},
        "manifests": {sample: f"{sample}.geff.manifest.json" for sample in PANEL_V1},
        "mode": "smoke_6frame",
        "max_frames": 6,
    }
    with pytest.raises(ValueError, match=r"full|smoke|schema|receipt"):
        evaluation._validate_inference_receipt(payload, selection_lock=_lock())


def test_aggregate_requires_artifact_root_and_canonical_task4_receipt() -> None:
    rows = [_sample_receipt(sample, 0.9) for sample in PANEL_V1]
    with pytest.raises(TypeError, match=r"prediction_root|inference_receipt"):
        evaluation.aggregate_panel_receipts(rows, _lock())


def test_panel_preflight_failure_writes_failure_receipt_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, inference, _ = _panel_inputs(tmp_path)
    lock = _lock()
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    output = tmp_path / "panel.json"
    invalid_map = {"not-panel": tmp_path / "gt.geff"}
    with pytest.raises(ValueError, match=r"PANEL_V1|gt_map"):
        evaluation.evaluate_panel(
            lock,
            root,
            output=output,
            inference_receipt=inference,
            ground_truth_map=invalid_map,
            reproduction_command="test-command",
        )
    failure = json.loads(output.read_text(encoding="utf-8"))
    assert failure["status"] == "FAILED"
    assert failure["panel_status"] == "INCOMPLETE"
    assert failure["failure"]["sample_id"] is None
    assert failure["failure"]["phase"] == "gt_map"
    assert failure["failure"]["reproduction_command"] == "test-command"
    assert "gt" not in events


def test_nonfinite_official_row_never_becomes_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    monkeypatch.setattr(
        evaluation,
        "per_sample_metrics",
        lambda er, n_total, node_recall: {
            "edge_tp": 1,
            "edge_fp": 0,
            "edge_fn": 0,
            "division_tp": 0,
            "division_fp": 0,
            "division_fn": 0,
            "num_pred_nodes": 2,
            "node_recall": float("nan"),
            "total_node_ratio": 0.0,
            "edge_jaccard": 1.0,
            "adj_edge_jaccard": 1.0,
        },
    )
    with pytest.raises(ValueError, match=r"finite|node_recall|metric"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "ground-truth.geff", _lock())


def test_manifest_boolean_schema_version_is_rejected_before_gt_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction, schema_version=True)
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    with pytest.raises(ValueError, match=r"schema_version|manifest"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "ground-truth.geff", _lock())
    assert "gt" not in events


@pytest.mark.parametrize("invalid", ["", "gt/*.geff", r"gt\\sample.geff"])
def test_ground_truth_map_rejects_empty_glob_and_backslash_before_gt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    root, inference, _ = _panel_inputs(tmp_path)
    lock = _lock()
    ground_truth_map = {sample: tmp_path / f"{sample}.gt.geff" for sample in PANEL_V1}
    ground_truth_map[PANEL_V1[0]] = invalid
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    with pytest.raises(ValueError, match=r"ground.?truth|map|path"):
        evaluation.evaluate_panel(
            lock,
            root,
            inference_receipt=inference,
            ground_truth_map=ground_truth_map,
        )
    assert "gt" not in events


def test_real_gt_guard_opener_orders_persistence_before_gt_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    ground_truth = tmp_path / "ground-truth.geff"
    ground_truth.mkdir()
    events: list[str] = []
    real_open = evaluation._open_gt
    _patch_fake_metric(monkeypatch, events)
    fake_gt = SimpleNamespace(num_nodes=lambda: 2, num_edges=lambda: 1)
    monkeypatch.setattr(
        evaluation,
        "_load_ground_truth",
        lambda path: events.append("gt_callback") or evaluation.GroundTruthOpened(fake_gt, 2.0),
    )
    monkeypatch.setattr(evaluation, "_open_gt", real_open)

    receipt = evaluation.evaluate_locked_prediction(prediction, ground_truth, _lock())

    assert events[:3] == ["validate", "gt_callback", "evaluate"]
    assert receipt["gt_open_receipt"]["ordering_enforced_by"] == (
        "biohub.reproducibility.gt_guard.open_ground_truth"
    )


def test_invalid_manifest_fails_before_gt_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction, ground_truth_included=True)
    opened: list[str] = []
    monkeypatch.setattr(evaluation, "_open_gt", lambda *args: opened.append("gt"))

    with pytest.raises(Exception, match=r"ground_truth_included|manifest"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "gt.geff", _lock())

    assert opened == []


def test_boolean_only_legacy_receipt_and_shared_manifest_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    (tmp_path / "prediction_manifest.json").write_text(
        json.dumps({"prediction_manifest_validated_before_gt": True}), encoding="utf-8"
    )
    opened: list[str] = []
    monkeypatch.setattr(evaluation, "_open_gt", lambda *args: opened.append("gt"))

    with pytest.raises(Exception, match="manifest"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "gt.geff", _lock())

    assert opened == []


def test_selection_lock_mapping_is_always_canonical_and_weak_mapping_is_rejected() -> None:
    weak = {
        "selection_lock_id": _lock()["selection_lock_id"],
        "panel": {"panel_id": "PANEL_V1", "sample_ids": list(PANEL_V1)},
    }
    with pytest.raises(ValueError, match=r"selection lock|schema|experiment"):
        evaluation._validate_panel_lock(weak)


def test_official_pin_is_verified_before_gt_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    original_pin = evaluation._assert_official_metric_pin
    monkeypatch.setattr(
        evaluation,
        "_assert_official_metric_pin",
        lambda: (events.append("pin"), original_pin())[1],
    )

    evaluation.evaluate_locked_prediction(prediction, tmp_path / "ground-truth.geff", _lock())

    assert events.index("pin") < events.index("gt")


def test_gt_graph_and_estimated_nodes_are_returned_by_single_opener() -> None:
    assert hasattr(evaluation, "GroundTruthOpened")
    assert not hasattr(evaluation, "_estimated_node_count")


def test_manifest_unknown_feedback_field_is_rejected_before_gt_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction, score=0.99)
    opened: list[str] = []
    monkeypatch.setattr(evaluation, "_open_gt", lambda *args: opened.append("gt"))

    with pytest.raises(ValueError, match=r"unknown|manifest|score"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "gt.geff", _lock())
    assert opened == []


@pytest.mark.parametrize("mutation", ["missing", "future"])
def test_manifest_missing_or_future_timestamp_is_rejected_before_gt_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    manifest = _manifest(prediction)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload.pop("files")
    else:
        payload["manifest_created_at"] = "2999-01-01T00:00:00+00:00"
    manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(evaluation, "_open_gt", lambda *args: opened.append("gt"))

    with pytest.raises(ValueError, match=r"manifest|files|future|timestamp"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "ground-truth.geff", _lock())
    assert opened == []


def test_prediction_symlink_is_rejected_before_gt_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prediction = _prediction(tmp_path, PANEL_V1[0])
    _manifest(prediction)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (prediction / "linked").symlink_to(outside)
    opened: list[str] = []
    monkeypatch.setattr(evaluation, "_open_gt", lambda *args: opened.append("gt"))

    with pytest.raises(ValueError, match=r"symlink|prediction"):
        evaluation.evaluate_locked_prediction(prediction, tmp_path / "ground-truth.geff", _lock())
    assert opened == []


def test_actual_task4_manifest_schema_is_accepted_without_prediction_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from biohub.recipe_c import geff_bridge

    prediction = _prediction(tmp_path, PANEL_V1[0])
    counts = {"nodes": 2, "edges": 1, "forks": 0}
    monkeypatch.setattr(geff_bridge, "validate_prediction_geff", lambda *args, **kwargs: counts)
    monkeypatch.setattr(evaluation, "validate_prediction_geff", lambda *args, **kwargs: counts)
    manifest = geff_bridge.write_prediction_manifest(
        prediction,
        selection_lock_id=str(_lock()["selection_lock_id"]),
        provenance={
            "source_commit": "a" * 40,
            "config_sha256": "b" * 64,
            "predictor_sha256_before": "c" * 64,
            "predictor_sha256": "d" * 64,
            "predictor_sha256_after": "d" * 64,
            "d4_predictor_sha256_after": "e" * 64,
            "stage_predictor_sha256_after": "f" * 64,
            "primary_checkpoint_sha256": "1" * 64,
            "secondary_checkpoint_sha256": "2" * 64,
            "resolved_device": "cpu",
            "device_candidates": "cpu",
            "patch_spatial_d4": True,
            "patch_builder": True,
            "runtime_role": "live_stage_repo",
            "command_sha256": "3" * 64,
            "execution_argv_sha256": "4" * 64,
            "child_device": "cpu",
            "child_stdout_sha256": "5" * 64,
            "child_stderr_sha256": "6" * 64,
            "predictor_diagnostic_sha256": "7" * 64,
            "trace_module_sha256": "8" * 64,
            "trace_module_derived_sha256": "9" * 64,
            "cuda_equivalence_validated": False,
        },
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert "prediction_sha256" not in payload
    artifact = evaluation._validate_prediction_artifact(
        prediction,
        PANEL_V1[0],
        selection_lock_id=str(_lock()["selection_lock_id"]),
    )
    assert artifact["manifest_path"] == manifest


def test_receipt_write_is_atomic_write_once_and_does_not_clobber(
    tmp_path: Path,
) -> None:
    target = tmp_path / "receipt.json"
    evaluation._write_json(target, {"status": "READY"})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        evaluation._write_json(target, {"status": "FAILED"})
    assert target.read_bytes() == before


def test_role_paths_reject_parent_escape_absolute_and_symlink_parent(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    with pytest.raises(ValueError, match=r"relative|contain|role"):
        evaluation._resolve_role_path(Path("../outside.geff"), root=root)
    with pytest.raises(ValueError, match=r"relative|contain|role"):
        evaluation._resolve_role_path(Path("/tmp/outside.geff"), root=root)
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        evaluation._resolve_role_path(Path("predictions/sample.geff"), root=linked)


def test_evaluate_panel_requires_explicit_ordered_ground_truth_map() -> None:
    parameters = inspect.signature(evaluation.evaluate_panel).parameters
    assert "ground_truth_root" not in parameters
    assert "ground_truth_map" in parameters


def test_panel_stays_incomplete_until_all_five_samples_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, inference, _ = _panel_inputs(tmp_path)
    lock = _lock()
    ground_truth_map = {sample: tmp_path / f"{sample}.gt.geff" for sample in PANEL_V1}
    events: list[str] = []
    _patch_fake_metric(monkeypatch, events)
    successful_open = evaluation._open_gt
    opens = 0

    def fail_on_third(path: Path, token: Any) -> Any:
        nonlocal opens
        opens += 1
        if opens == 3:
            raise evaluation.MetricBoundaryError("synthetic panel failure", phase="gt_open", sample_id=PANEL_V1[2])
        return successful_open(path, token)

    monkeypatch.setattr(evaluation, "_open_gt", fail_on_third)
    result = evaluation.evaluate_panel(
        lock,
        root,
        inference_receipt=inference,
        ground_truth_map=ground_truth_map,
    )

    assert result["status"] == "FAILED"
    assert result["panel_status"] == "INCOMPLETE"
    assert "macro_final_score" not in result
    assert opens == 3
    assert events[:5] == ["validate"] * 5


def test_aggregate_rejects_tampered_final_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, inference, artifacts = _panel_inputs(tmp_path)
    monkeypatch.setattr(
        evaluation,
        "validate_prediction_geff",
        lambda path, sample_id, expected_volume_shape_tzyx=None: {"nodes": 2, "edges": 1, "forks": 0},
    )
    rows = [_sample_receipt(sample, 0.9, artifact=artifacts[sample]) for sample in PANEL_V1]
    rows[2]["final_score"] = 0.1
    with pytest.raises(ValueError, match=r"score|summar|metric"):
        evaluation.aggregate_panel_receipts(
            rows,
            _lock(),
            prediction_root=root,
            inference_receipt=inference,
        )


def test_aggregate_revalidates_artifact_drift_without_opening_ground_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, inference, artifacts = _panel_inputs(tmp_path)
    monkeypatch.setattr(
        evaluation,
        "validate_prediction_geff",
        lambda path, sample_id, expected_volume_shape_tzyx=None: {"nodes": 2, "edges": 1, "forks": 0},
    )
    rows = [_sample_receipt(sample, 0.9, artifact=artifacts[sample]) for sample in PANEL_V1]
    opened: list[str] = []
    monkeypatch.setattr(evaluation, "_open_gt", lambda *args: opened.append("gt"))
    (root / "predictions" / f"{PANEL_V1[0]}.geff" / "payload").write_bytes(b"drift")

    with pytest.raises(ValueError, match=r"digest|directory|manifest|changed"):
        evaluation.aggregate_panel_receipts(
            rows,
            _lock(),
            prediction_root=root,
            inference_receipt=inference,
        )
    assert opened == []


def _sample_receipt(
    sample_id: str,
    score: float,
    *,
    division: tuple[int, int, int] = (0, 0, 0),
    edge_weight: int = 1,
    artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    division_tp, division_fp, division_fn = division
    division_total = division_tp + division_fp + division_fn
    edge_tp = round(score * 10) * edge_weight
    edge_fp = (10 - round(score * 10)) * edge_weight
    if artifact is None:
        prediction_path = f"predictions/{sample_id}.geff"
        manifest_path = f"predictions/{sample_id}.geff.manifest.json"
        manifest_sha256 = "a" * 64
        directory_sha256 = "b" * 64
        prediction_files = 2
        prediction_total_bytes = 10
        prediction_counts = {"nodes": 2, "edges": edge_tp + edge_fp, "forks": 0}
    else:
        prediction_path = str(artifact["prediction_path"])
        manifest_path = str(artifact["manifest_path"])
        manifest_sha256 = str(artifact["manifest_sha256"])
        directory_sha256 = str(artifact["directory_sha256"])
        prediction_files = int(artifact["files"])
        prediction_total_bytes = int(artifact["total_bytes"])
        prediction_counts = dict(artifact["counts"])
    return {
        "schema_version": "biohub_095.metric_boundary.v1",
        "status": "READY",
        "panel_id": "PANEL_V1",
        "selection_lock_id": _lock()["selection_lock_id"],
        "sample_id": sample_id,
        "sample_index": list(PANEL_V1).index(sample_id),
        "prediction_path": prediction_path,
        "prediction_manifest_path": manifest_path,
        "prediction_manifest_sha256": manifest_sha256,
        "prediction_directory_sha256": directory_sha256,
        "prediction_files": prediction_files,
        "prediction_total_bytes": prediction_total_bytes,
        "prediction_node_count": prediction_counts["nodes"],
        "prediction_edge_count": prediction_counts["edges"],
        "prediction_fork_count": prediction_counts["forks"],
        "metric_config": {
            "scale": [1.625, 0.40625, 0.40625],
            "max_distance": 7.0,
            "alpha": 0.1,
            "division_weight": 0.1,
        },
        "official_metric_provenance": evaluation.OFFICIAL_METRIC_PROVENANCE,
        "edge_tp": edge_tp,
        "edge_fp": edge_fp,
        "edge_fn": 0,
        "division_tp": division_tp,
        "division_fp": division_fp,
        "division_fn": division_fn,
        "node_recall": 1.0,
        "total_node_ratio": 0.0,
        "edge_jaccard": score,
        "adjusted_edge_jaccard": score,
        "division_jaccard": (division_tp / division_total if division_total else None),
        "final_score": score,
        "division_term_live": bool(division_total),
        "metric_started_at": "2026-08-23T00:00:00+00:00",
        "metric_finished_at": "2026-08-23T00:00:01+00:00",
        "reproduction_command": "synthetic",
        "gt_open_receipt": {
            "prediction_path": prediction_path,
            "prediction_manifest_path": manifest_path,
            "prediction_directory_sha256": directory_sha256,
            "prediction_files": prediction_files,
            "prediction_total_bytes": prediction_total_bytes,
            "prediction_manifest_created_at": "2026-08-22T00:00:00+00:00",
            "prediction_persisted_at": "2026-08-22T00:00:01+00:00",
            "ground_truth_opened_at": "2026-08-22T00:00:02+00:00",
            "ordering_enforced_by": "biohub.reproducibility.gt_guard.open_ground_truth",
            "ground_truth_path": f"/tmp/{sample_id}.gt.geff",
            "ordering_evidence": (
                "prediction bytes re-hashed to prediction_directory_sha256 immediately before "
                "this ground-truth open"
            ),
        },
        "official_metric_row": {
            "edge_tp": edge_tp,
            "edge_fp": edge_fp,
            "edge_fn": 0,
            "division_tp": division_tp,
            "division_fp": division_fp,
            "division_fn": division_fn,
            "num_pred_nodes": 2,
            "node_recall": 1.0,
            "total_node_ratio": 0.0,
            "edge_jaccard": score,
            "adj_edge_jaccard": score,
        },
    }


def test_aggregate_requires_exact_five_and_uses_unweighted_macro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, inference, artifacts = _panel_inputs(tmp_path)
    monkeypatch.setattr(
        evaluation,
        "validate_prediction_geff",
        lambda path, sample_id, expected_volume_shape_tzyx=None: {"nodes": 2, "edges": 1, "forks": 0},
    )
    rows = [
        _sample_receipt(sample, 0.5 + index * 0.1, edge_weight=index + 1, artifact=artifacts[sample])
        for index, sample in enumerate(PANEL_V1)
    ]

    result = evaluation.aggregate_panel_receipts(
        rows,
        _lock(),
        prediction_root=root,
        inference_receipt=inference,
    )

    assert result["status"] == "READY"
    assert result["sample_order"] == list(PANEL_V1)
    assert result["macro_final_score"] == pytest.approx(0.7)
    assert result["official_size_weighted_score"] == pytest.approx(11.5 / 15.0)
    assert result["macro_final_score"] != result["official_size_weighted_score"]
    assert len(result["inference_receipt_sha256"]) == 64
    assert result["inference_receipt_identity"]["source_commit"] == _lock()["source_commit"]

    with pytest.raises(ValueError, match=r"PANEL_V1|five"):
        evaluation.aggregate_panel_receipts(
            rows[:-1],
            _lock(),
            prediction_root=root,
            inference_receipt=inference,
        )


def test_aggregate_preserves_division_null_and_live_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, inference, artifacts = _panel_inputs(tmp_path)
    monkeypatch.setattr(
        evaluation,
        "validate_prediction_geff",
        lambda path, sample_id, expected_volume_shape_tzyx=None: {"nodes": 2, "edges": 1, "forks": 0},
    )
    rows = [_sample_receipt(sample, 0.9, artifact=artifacts[sample]) for sample in PANEL_V1]
    no_division = evaluation.aggregate_panel_receipts(
        rows,
        _lock(),
        prediction_root=root,
        inference_receipt=inference,
    )
    assert no_division["panel_division_jaccard"] is None
    assert no_division["division_term_live"] is False

    rows[-1] = _sample_receipt(
        PANEL_V1[-1],
        0.9,
        division=(0, 0, 1),
        artifact=artifacts[PANEL_V1[-1]],
    )
    with_division = evaluation.aggregate_panel_receipts(
        rows,
        _lock(),
        prediction_root=root,
        inference_receipt=inference,
    )
    assert with_division["panel_division_jaccard"] == 0.0
    assert with_division["division_term_live"] is True
