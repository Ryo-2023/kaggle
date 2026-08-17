from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import torch
from torch import nn

import mage_ptcg.meta_specialist.representation_benchmark_v3 as benchmark
from mage_ptcg.meta_specialist.representation_benchmark_v3 import (
    _production_vocabulary_identity_v3,
    load_teacher_examples_v3,
    read_gate_result_v3,
    read_gate_selection_manifest_v3,
    run_gate1_v3,
    run_representation_benchmark_v3,
    validate_gate_snapshot_v3,
)
from mage_ptcg.meta_specialist.representation_v3 import ActionCandidateV3, RelationalStateV3, SemanticPrefixTokenV3


def _rehash_result(payload: dict[str, object]) -> dict[str, object]:
    core = {key: value for key, value in payload.items() if key != "result_sha256"}
    payload["result_sha256"] = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _metrics(*, record_ids: list[str], parameter_count: int) -> dict[str, object]:
    return {
        "best_validation_token_nll": 0.5,
        "validation_complete_action_nll": 0.5,
        "top1": 0.5,
        "top3": 1.0,
        "topk_soft_target_tie_rule": "lowest-token-index-among-max-mass",
        "rare_action_recall": {
            "rule_version": "train-action-type-frequency-lte-1-v1", "eligible": 1,
            "value": 1.0, "status": "measured",
        },
        "action_type_nll": {
            "by_type": {"2": {"count": 1, "target_mass": 1.0, "nll_contribution": 0.5, "normalized_nll": 0.5}},
            "macro": 0.5, "overall": 0.5,
        },
        "p50_ms": 1.0, "p95_ms": 2.0, "cpu_preprocessing_ms": 3.0,
        "cuda_vram": {"measured": False, "peak_allocated_bytes": None, "peak_reserved_bytes": None, "blocker": "CPU execution requested"},
        "epochs": 3, "updates": 3, "best_epoch": 0, "stale_epochs": 2,
        "stop_reason": "patience", "history": [0.5, 0.5, 0.5],
        "parameter_delta_l1": 1.0, "parameter_count": parameter_count,
        "checkpoint_sha256": "a" * 64, "record_ids": record_ids, "step_count": 1,
    }


def _formal_gate_result() -> dict[str, object]:
    parameter_counts = {"current-R2": 452035, "R3-A": 3776386, "R3-B": 3867138}
    adapters = {"current-R2": "SpecialistPolicyModelV1", "R3-A": "ZoneDeepSetsEncoderV3", "R3-B": "RelationAwareEncoderV3"}
    input_hashes = {"alakazam": "1" * 64, "archaludon": "2" * 64}
    split_hashes = {"alakazam": "3" * 64, "archaludon": "4" * 64}
    record_ids = {"alakazam": ["a" * 64], "archaludon": ["b" * 64]}
    runs: list[dict[str, object]] = []
    for lane in ("alakazam", "archaludon"):
        for candidate in ("current-R2", "R3-A", "R3-B"):
            for seed in (7, 17, 29):
                runs.append({
                    "lane": lane, "candidate": candidate, "adapter": adapters[candidate],
                    "representation_version": 2 if candidate == "current-R2" else 3,
                    "seed": seed, "split_manifest_sha256": split_hashes[lane],
                    "input_manifest_sha256": input_hashes[lane],
                    "budget": {"max_epochs": 4, "patience": 2, "min_delta": 0.0001},
                    "target": "complete-legal-action-autoregressive-semantic-plus-stop-v1",
                    "status": "measured", "coverage": {
                        "learned_stop_domain_count": 1,
                        "positive_stop_target_count": 1, "ordered_nonempty_prefix_count": 1,
                        "validation_positive_stop_target_count": 1,
                        "prefix_conditioned_positive_stop_target_count": 1,
                        "rare_rule_version": "train-action-type-frequency-lte-1-v1",
                        "rare_anchor": {
                            "action_type": 0, "record_id": record_ids[lane][0],
                            "shard": "dataset-0000.jsonl", "line": 0,
                            "partition": "validation",
                        },
                    },
                    "metrics": _metrics(record_ids=record_ids[lane], parameter_count=parameter_counts[candidate]),
                })
    return _rehash_result({
        "schema": "meta-specialist-gate1-v3", "status": "BLOCKED", "execution_device": "cpu",
        "seeds": [7, 17, 29], "runs": runs,
        "selection": {
            "decision_status": "BLOCKED_THRESHOLD_UNSPECIFIED", "preferred": None,
            "blockers": ["v2_major_regression_threshold_unspecified", "cuda_measurement_unavailable"],
            "rule": "test rule",
        },
    })


def _formal_cuda_gate_result() -> dict[str, object]:
    """A self-consistent measured artifact used only for integrity tampering."""
    payload = _formal_gate_result()
    payload["execution_device"] = "cuda:0"
    for row in payload["runs"]:
        row["metrics"]["cuda_vram"] = {
            "measured": True,
            "peak_allocated_bytes": 1024,
            "peak_reserved_bytes": 2048,
            "device_name": "fixture-cuda",
            "runtime": "fixture-runtime",
        }
    payload["selection"]["blockers"].remove("cuda_measurement_unavailable")
    return _rehash_result(payload)


def _baseline_retained_gate_result() -> dict[str, object]:
    """The incumbent is usable, but no R3 promotion has been approved."""
    payload = _formal_gate_result()
    payload["status"] = "BASELINE_RETAINED"
    payload["selection"]["decision_status"] = "BASELINE_RETAINED_R3_UNAPPROVED"
    payload["selection"]["preferred"] = "current-R2"
    return _rehash_result(payload)


def _write_minimal_valid_gate_input(tmp_path: Path, lane: str) -> tuple[Path, Path]:
    """Create a sealed dry-run manifest; no teacher corpus is needed in dry mode."""
    root = tmp_path / lane
    root.mkdir()
    split_core: dict[str, object] = {
        "schema": "meta-specialist-bc-split-v3", "source_dataset_sha256": "a" * 64,
        "ubiquitous_keys": [], "ubiquitous_metadata": {"threshold": 2, "rule_version": "fixture"},
        "assignments": [], "counts": {"train": 26, "validation": 6},
        "overlap_counters": {"episode_overlap": 0, "near_duplicate_overlap": 0},
    }
    split = {**split_core, "manifest_sha256": benchmark._hash(split_core)}
    payload: dict[str, object] = {
        "schema": "meta-specialist-gate1-input-v1", "lane": lane, "root": str(root.resolve()),
        "snapshot_index_sha256": "1" * 64, "dataset_snapshot_sha256": "2" * 64,
        "teacher_manifest_sha256": "3" * 64, "trusted_permission_bytes_b64": "e30=",
        "trusted_permission_sha256": "4" * 64, "vocabulary": _production_vocabulary_identity_v3(),
        "coverage": {
            "learned_stop_domain_count": 1, "positive_stop_target_count": 1,
            "ordered_nonempty_prefix_count": 1,
            "validation_positive_stop_target_count": 1,
            "prefix_conditioned_positive_stop_target_count": 1,
            "rare_rule_version": "train-action-type-frequency-lte-1-v1",
            "rare_anchor": {"partition": "validation", "action_type": 0},
        },
        "qualification_time_utc": "2026-08-09T00:00:00Z",
        "selection_rule": "coverage-first-positive-stop-then-rare-validation-then-validation-stop-v3;first-common-r2-r3-eligible-per-shard-line-order",
        "selection": [{"fixture": index} for index in range(32)], "rejections": [], "split": split,
        "target_contract": "complete-legal-action-autoregressive-semantic-plus-stop-v1",
    }
    payload["manifest_sha256"] = benchmark._hash(benchmark._gate_input_core(payload))
    path = tmp_path / f"{lane}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return root, path


def test_gate_uses_a_closed_production_vocabulary_identity() -> None:
    identity = _production_vocabulary_identity_v3()
    assert identity["test_only"] is False
    assert identity["count"] > 0
    assert identity["max_id"] >= identity["count"]
    assert len(identity["source_sha256"]) == 64


def test_representation_benchmark_is_small_deterministic_and_reports_all_candidates() -> None:
    report = run_representation_benchmark_v3(seed=5, samples=24, epochs=2)
    assert set(report["candidates"]) == {"R2-negative-control", "R3-A", "R3-B"}
    for metrics in report["metrics"].values():
        assert 0 <= metrics["top1"] <= 1
        assert metrics["nll"] >= 0
        assert metrics["p50_ms"] >= 0
        assert metrics["p95_ms"] >= metrics["p50_ms"]


def test_gate1_rejects_a_self_reported_split_hash_without_a_closed_input_manifest(tmp_path) -> None:
    manifest = tmp_path / "split.json"
    manifest.write_text('{"manifest_sha256":"' + "a" * 64 + '"}', encoding="utf-8")
    with pytest.raises(ValueError, match="input manifest"):
        run_gate1_v3(
            lane_roots={"alakazam": tmp_path, "archaludon": tmp_path},
            split_manifest_paths={"alakazam": manifest, "archaludon": manifest},
            output_dir=tmp_path / "out", dry_run=True,
        )


def test_legacy_first_action_teacher_loader_is_not_a_formal_gate_input(tmp_path) -> None:
    (tmp_path / "dataset-0000.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="retired"):
        load_teacher_examples_v3(tmp_path)


def test_gate_rejects_requested_cuda_when_cuda_is_unavailable(tmp_path) -> None:
    if __import__("torch").cuda.is_available():
        pytest.skip("CPU-only contract")
    with pytest.raises(RuntimeError, match="requested CUDA"):
        run_gate1_v3(
            lane_roots={"alakazam": tmp_path, "archaludon": tmp_path},
            split_manifest_paths={"alakazam": tmp_path / "missing", "archaludon": tmp_path / "missing"},
            output_dir=tmp_path / "out", device="cuda:0",
        )


def test_gate_snapshot_binds_each_closed_chunk_to_its_declared_bytes_and_rejects_escape(tmp_path: Path) -> None:
    shard = tmp_path / "dataset-0000.jsonl"
    shard.write_text('{"payload":"one"}\n', encoding="utf-8")
    expected = hashlib.sha256(shard.read_bytes()).hexdigest()
    snapshot = {
        "examples_total": 1,
        "dataset_chunks": [{
            "path": shard.name, "dataset_snapshot_sha256": expected,
            "manifest_content_hash": "m" * 64, "manifest_id": "fixture",
        }],
    }
    assert validate_gate_snapshot_v3(tmp_path, snapshot) == {shard.name: expected}

    # Same row count is not evidence that the sealed teacher bytes remained
    # unchanged.  A rehashed input manifest must still be rejected here.
    shard.write_text('{"payload":"two"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="chunk|hash|snapshot"):
        validate_gate_snapshot_v3(tmp_path, snapshot)

    shard.write_text('{"payload":"one"}\n', encoding="utf-8")
    escaped = copy.deepcopy(snapshot)
    escaped["dataset_chunks"][0]["path"] = "../dataset-0000.jsonl"
    with pytest.raises(ValueError, match="strict|escape|path"):
        validate_gate_snapshot_v3(tmp_path, escaped)

    unknown_field = copy.deepcopy(snapshot)
    unknown_field["dataset_chunks"][0]["unattested"] = True
    with pytest.raises(ValueError, match="malformed|closed|chunk"):
        validate_gate_snapshot_v3(tmp_path, unknown_field)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["runs"].pop(),
        lambda payload: payload["runs"].append(copy.deepcopy(payload["runs"][0])),
        lambda payload: payload["runs"][0]["metrics"].__setitem__("top1", 1.5),
        lambda payload: payload["runs"][0].__setitem__("status", "planned"),
        lambda payload: payload["runs"][0].__setitem__("input_manifest_sha256", "f" * 64),
    ],
    ids=["deleted-run", "duplicate-run", "out-of-domain-metric", "planned-status", "lane-input-tamper"],
)
def test_gate_result_rejects_rehashed_structural_or_metric_tampering(tmp_path: Path, mutate) -> None:
    payload = _formal_gate_result()
    mutate(payload)
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(_rehash_result(payload)), encoding="utf-8")
    with pytest.raises(ValueError):
        benchmark._read_gate_result_v3(path)


def test_gate_result_rejects_rehashed_cross_candidate_budget_or_record_id_drift(tmp_path: Path) -> None:
    for mutate in (
        lambda payload: payload["runs"][1]["budget"].__setitem__("max_epochs", 5),
        lambda payload: payload["runs"][1]["metrics"].__setitem__("record_ids", ["c" * 64]),
        lambda payload: payload["selection"].__setitem__("preferred", "R3-B"),
    ):
        payload = _formal_gate_result()
        mutate(payload)
        path = tmp_path / "gate.json"
        path.write_text(json.dumps(_rehash_result(payload)), encoding="utf-8")
        with pytest.raises(ValueError):
            benchmark._read_gate_result_v3(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("status", "PASS"),
        lambda payload: payload["selection"].__setitem__("blockers", []),
        lambda payload: payload["runs"][0].__setitem__("candidate", "forged-R4"),
        lambda payload: payload["runs"][0].__setitem__("seed", 999),
        lambda payload: payload["runs"][0]["metrics"].__setitem__("parameter_count", 1),
    ],
    ids=["top-level-pass", "empty-blockers", "forged-candidate", "forged-seed", "forged-parameter-count"],
)
def test_public_gate_result_reader_rejects_rehashed_forged_gate_claims(tmp_path: Path, mutate) -> None:
    payload = _formal_gate_result()
    mutate(payload)
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(_rehash_result(payload)), encoding="utf-8")
    with pytest.raises(ValueError):
        read_gate_result_v3(path)


@pytest.mark.parametrize(
    ("mutate", "label"),
    [
        (lambda payload: payload["runs"][0]["metrics"].__setitem__("p50_ms", 1.5), "cuda-p50"),
        (lambda payload: payload["runs"][0]["metrics"].__setitem__("p95_ms", 2.5), "cuda-p95"),
        (lambda payload: payload["runs"][0]["metrics"]["cuda_vram"].__setitem__("device_name", "forged-cuda"), "cuda-name"),
    ],
)
def test_external_gate_result_anchor_rejects_rehashed_cuda_measurement_tampering(
    tmp_path: Path, mutate, label: str,
) -> None:
    """A result self-hash is not an external integrity anchor.

    Each mutation remains a closed-schema, internally rehashed CUDA result.
    The independent expected on-disk SHA and expected signed-payload SHA must
    still prevent treating it as the previously reviewed measurement.
    """
    payload = _formal_cuda_gate_result()
    path = tmp_path / f"{label}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    expected_file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    expected_result_sha256 = payload["result_sha256"]

    verified = benchmark.verify_gate_result_anchor_v3(
        path,
        expected_file_sha256=expected_file_sha256,
        expected_result_sha256=expected_result_sha256,
    )
    assert verified["result_sha256"] == expected_result_sha256

    mutate(payload)
    path.write_text(json.dumps(_rehash_result(payload)), encoding="utf-8")
    # The self-contained reader must accept this internally consistent forged
    # file; the expected artifact bytes are the independent trust boundary.
    assert benchmark._read_gate_result_v3(path)["execution_device"] == "cuda:0"
    with pytest.raises(ValueError, match="file.*SHA|SHA.*file|anchor"):
        benchmark.verify_gate_result_anchor_v3(
            path,
            expected_file_sha256=expected_file_sha256,
            expected_result_sha256=expected_result_sha256,
        )

    # Even if a caller intentionally supplies the tampered file's own digest,
    # the reviewed payload's expected self SHA remains a separate check.
    tampered_file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="result.*SHA|SHA.*result|anchor"):
        benchmark.verify_gate_result_anchor_v3(
            path,
            expected_file_sha256=tampered_file_sha256,
            expected_result_sha256=expected_result_sha256,
        )


def test_closed_baseline_retained_decision_is_safe_and_cannot_be_rehashed_into_r3_promotion(tmp_path: Path) -> None:
    payload = _baseline_retained_gate_result()
    path = tmp_path / "baseline-retained.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    artifact = read_gate_result_v3(path)
    assert artifact["status"] == "BASELINE_RETAINED"
    assert artifact["selection"] == {
        "decision_status": "BASELINE_RETAINED_R3_UNAPPROVED",
        "preferred": "current-R2",
        "blockers": ["v2_major_regression_threshold_unspecified", "cuda_measurement_unavailable"],
        "rule": "test rule",
    }

    payload["selection"]["preferred"] = "R3-A"
    path.write_text(json.dumps(_rehash_result(payload)), encoding="utf-8")
    with pytest.raises(ValueError, match="baseline|selection|preferred|R3"):
        read_gate_result_v3(path)


def test_gate_result_fixture_covers_every_required_coverage_field() -> None:
    required = {
        "learned_stop_domain_count", "positive_stop_target_count", "ordered_nonempty_prefix_count",
        "validation_positive_stop_target_count", "prefix_conditioned_positive_stop_target_count",
        "rare_rule_version", "rare_anchor",
    }
    assert all(set(row["coverage"]) == required for row in _formal_gate_result()["runs"])


@pytest.mark.parametrize("field", [
    "learned_stop_domain_count", "positive_stop_target_count", "ordered_nonempty_prefix_count",
    "validation_positive_stop_target_count", "prefix_conditioned_positive_stop_target_count",
    "rare_rule_version", "rare_anchor",
])
def test_gate_result_rejects_rehashed_missing_required_coverage_field(tmp_path: Path, field: str) -> None:
    payload = _formal_gate_result()
    for row in payload["runs"]:
        del row["coverage"][field]
    path = tmp_path / "coverage-missing.json"
    path.write_text(json.dumps(_rehash_result(payload)), encoding="utf-8")
    with pytest.raises(ValueError, match="coverage"):
        read_gate_result_v3(path)


def test_dry_run_with_sealed_input_manifests_strictly_reloads_a_blocked_planned_matrix(tmp_path: Path) -> None:
    alakazam_root, alakazam_manifest = _write_minimal_valid_gate_input(tmp_path, "alakazam")
    archaludon_root, archaludon_manifest = _write_minimal_valid_gate_input(tmp_path, "archaludon")
    result = run_gate1_v3(
        lane_roots={"alakazam": alakazam_root, "archaludon": archaludon_root},
        split_manifest_paths={"alakazam": alakazam_manifest, "archaludon": archaludon_manifest},
        output_dir=tmp_path / "out", dry_run=True,
    )
    assert result.status == "BASELINE_RETAINED"
    artifact = read_gate_result_v3(result.output_path)
    assert artifact["status"] == "BASELINE_RETAINED"
    assert artifact["selection"]["decision_status"] == "BASELINE_RETAINED_R3_UNAPPROVED"
    assert artifact["selection"]["preferred"] == "current-R2"
    assert artifact["selection"]["blockers"] == [
        "v2_major_regression_threshold_unspecified", "cuda_measurement_unavailable",
    ]
    decision = read_gate_selection_manifest_v3(result.decision_path)
    assert decision["active_representation"] == "current-R2"
    assert decision["r3_promotion_status"] == "UNAPPROVED"
    assert len(artifact["runs"]) == 18
    assert {row["status"] for row in artifact["runs"]} == {"planned"}


@pytest.mark.parametrize("field", ["validation_positive_stop_target_count", "prefix_conditioned_positive_stop_target_count"])
def test_gate_input_coverage_seals_validation_and_prefix_conditioned_stop_counts(tmp_path: Path, field: str) -> None:
    _root, path = _write_minimal_valid_gate_input(tmp_path, "alakazam")
    assert benchmark._read_gate_input_v3(path)["coverage"][field] == 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["coverage"][field]
    payload["manifest_sha256"] = benchmark._hash(benchmark._gate_input_core(payload))
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="coverage"):
        benchmark._read_gate_input_v3(path)


def test_runtime_rejects_rehashed_coverage_claim_that_disagrees_with_reconstructed_stop_and_prefix_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root_a, manifest_a = _write_minimal_valid_gate_input(tmp_path, "alakazam")
    root_b, manifest_b = _write_minimal_valid_gate_input(tmp_path, "archaludon")
    # Rehash the inputs after a forged zero-coverage declaration.  Runtime must
    # compare the declaration with its actual semantic loss rows, not just
    # accept the manifest hash.
    for path in (manifest_a, manifest_b):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["coverage"]["positive_stop_target_count"] = 0
        payload["coverage"]["ordered_nonempty_prefix_count"] = 0
        payload["coverage"]["validation_positive_stop_target_count"] = 0
        payload["coverage"]["prefix_conditioned_positive_stop_target_count"] = 0
        payload["manifest_sha256"] = benchmark._hash(benchmark._gate_input_core(payload))
        path.write_text(json.dumps(payload), encoding="utf-8")
    state = RelationalStateV3(
        (0.0,) * 41, (), (ActionCandidateV3("semantic", 2, None, None, (), (), 0),),
        semantic_prefix=(SemanticPrefixTokenV3(2, (), ()),), prefix_order_sensitive=True,
    )
    stop_step = type("StepInput", (), {"stop_available": True})()
    actual_steps = (
        benchmark._GateStepV3("lane", "train", "component", "train", object(), stop_step, state, 0, (0.5, 0.5), 2),
        benchmark._GateStepV3("lane", "valid", "component", "validation", object(), stop_step, state, 0, (0.5, 0.5), 2),
    )
    monkeypatch.setattr(benchmark, "_gate_steps_from_input_v3", lambda _payload: actual_steps)
    monkeypatch.setattr(
        benchmark, "_run_candidate_v3",
        lambda candidate, _steps, **_kwargs: _metrics(
            record_ids=["a" * 64],
            parameter_count={"current-R2": 452035, "R3-A": 3776386, "R3-B": 3867138}[candidate],
        ),
    )
    with pytest.raises(ValueError, match="coverage|stop|prefix"):
        run_gate1_v3(
            lane_roots={"alakazam": root_a, "archaludon": root_b},
            split_manifest_paths={"alakazam": manifest_a, "archaludon": manifest_b},
            output_dir=tmp_path / "out", dry_run=False, patience=2,
        )


def test_gate_blocks_each_lane_whose_measured_rare_eligible_count_is_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root_a, manifest_a = _write_minimal_valid_gate_input(tmp_path, "alakazam")
    root_b, manifest_b = _write_minimal_valid_gate_input(tmp_path, "archaludon")
    monkeypatch.setattr(benchmark, "_gate_steps_from_input_v3", lambda _payload: (object(),))

    def fake_candidate(candidate: str, steps: tuple[object, ...], *, seed: int, **_kwargs: object) -> dict[str, object]:
        # The first lane is identified by its loaded sentinel only after the
        # wrapper below sets it; all three candidates/seeds receive the same
        # lane-level evidence.
        eligible = 0 if steps[0] == "alakazam" else 1
        return {
            **_metrics(
                record_ids=["a" * 64],
                parameter_count={"current-R2": 452035, "R3-A": 3776386, "R3-B": 3867138}[candidate],
            ),
            "rare_action_recall": {"rule_version": "train-action-type-frequency-lte-1-v1", "eligible": eligible, "value": None if not eligible else 1.0, "status": "no_eligible_targets" if not eligible else "measured"},
        }

    monkeypatch.setattr(benchmark, "_run_candidate_v3", fake_candidate)
    # Bind the reconstructed evidence to each lane without permitting source
    # files or corpus bytes to influence this focused orchestration test.
    monkeypatch.setattr(benchmark, "_gate_steps_from_input_v3", lambda payload: (payload["lane"],))
    result = run_gate1_v3(
        lane_roots={"alakazam": root_a, "archaludon": root_b},
        split_manifest_paths={"alakazam": manifest_a, "archaludon": manifest_b},
        output_dir=tmp_path / "out", dry_run=False, patience=2,
    )
    blockers = read_gate_result_v3(result.output_path)["selection"]["blockers"]
    assert "alakazam_rare_action_coverage_unavailable" in blockers
    assert "archaludon_rare_action_coverage_unavailable" not in blockers


@pytest.mark.parametrize(
    ("lane", "line", "split_prefix", "split_suffix", "expected_prefix_conditioned_stop_count"),
    [
        # The bounded Alakazam validation STOP anchor is at prefix length zero.
        # Do not fabricate prefix-conditioned coverage merely to pass Gate 1.
        ("alakazam", 979, "0897a0", "52d", 0),
        ("archaludon", 1392, "3989e2", "ddc7", 1),
    ],
)
def test_final_gate_inputs_seal_validation_stop_anchors_from_the_bounded_builder(
    lane: str, line: int, split_prefix: str, split_suffix: str, expected_prefix_conditioned_stop_count: int,
) -> None:
    path = Path("runs/meta-specialist-two-lane-readiness/gate1") / f"gate1-input-{lane}.json"
    payload = benchmark._read_gate_input_v3(path)
    coverage = payload["coverage"]
    assert coverage["validation_positive_stop_target_count"] >= 1
    assert coverage["prefix_conditioned_positive_stop_target_count"] == expected_prefix_conditioned_stop_count
    assert any(entry["shard"] == "dataset-0001.jsonl" and entry["line"] == line for entry in payload["selection"])
    split_hash = payload["split"]["manifest_sha256"]
    assert split_hash.startswith(split_prefix) and split_hash.endswith(split_suffix)


def test_gate_writer_strictly_reloads_the_atomic_result_before_returning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    root_a.mkdir(); root_b.mkdir()
    coverage = {
        "learned_stop_domain_count": 1, "positive_stop_target_count": 1,
        "ordered_nonempty_prefix_count": 1, "validation_positive_stop_target_count": 1,
        "prefix_conditioned_positive_stop_target_count": 1,
        "rare_rule_version": "train-action-type-frequency-lte-1-v1",
        "rare_anchor": {"partition": "validation", "action_type": 0},
    }
    manifests = {
        "alakazam": {"lane": "alakazam", "root": str(root_a.resolve()), "split": {"manifest_sha256": "a" * 64}, "coverage": coverage, "target_contract": "complete-legal-action-autoregressive-semantic-plus-stop-v1", "manifest_sha256": "1" * 64},
        "archaludon": {"lane": "archaludon", "root": str(root_b.resolve()), "split": {"manifest_sha256": "b" * 64}, "coverage": coverage, "target_contract": "complete-legal-action-autoregressive-semantic-plus-stop-v1", "manifest_sha256": "2" * 64},
    }
    monkeypatch.setattr(benchmark, "_read_gate_input_v3", lambda path: manifests[Path(path).stem])
    observed: list[Path] = []

    def reject_partial(path: str | Path) -> dict[str, object]:
        observed.append(Path(path))
        raise ValueError("strict reload sentinel")

    monkeypatch.setattr(benchmark, "_read_gate_result_v3", reject_partial)
    with pytest.raises(ValueError, match="strict reload sentinel"):
        run_gate1_v3(
            lane_roots={"alakazam": root_a, "archaludon": root_b},
            split_manifest_paths={"alakazam": tmp_path / "alakazam.json", "archaludon": tmp_path / "archaludon.json"},
            output_dir=tmp_path / "out", dry_run=True,
        )
    assert observed and observed[0].name.startswith("gate1-result-v3-")


class _TinyGateModel(nn.Module):
    """A deterministic differentiable Gate boundary; it never reads real data."""

    def __init__(self, **_kwargs: object) -> None:
        super().__init__()
        self.logits = nn.Parameter(torch.tensor([0.0, -0.5, -1.0]))

    def step_logits_v3(self, _state: object, *, stop_available: bool):
        assert not stop_available
        return self.logits[:2], None


def test_run_candidate_records_real_update_early_stop_and_mass_weighted_type_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "SpecialistModelV3", _TinyGateModel)
    monkeypatch.setattr(benchmark, "_load_production_vocabulary_v3", lambda: type("Vocabulary", (), {"recognized_card_ids": (1,)})())
    state = RelationalStateV3(
        (0.0,) * 41, (),
        (
            ActionCandidateV3("a", 2, None, None, (), (), 0),
            ActionCandidateV3("b", 3, None, None, (), (), 0),
        ),
    )
    # The second target has zero mass.  It is legal, but it must not inflate a
    # type denominator or contribute a fabricated zero-loss observation.
    step_input = type("StepInput", (), {"stop_available": False})()
    train = benchmark._GateStepV3("lane", "train-record", "c", "train", object(), step_input, state, 0, (1.0, 0.0), 2)
    valid = benchmark._GateStepV3("lane", "valid-record", "c", "validation", object(), step_input, state, 0, (1.0, 0.0), 2)
    metrics = benchmark._run_candidate_v3("R3-A", (train, valid), seed=7, max_epochs=4, patience=2, min_delta=10.0, device=torch.device("cpu"))

    assert metrics["parameter_delta_l1"] > 0
    assert metrics["best_epoch"] == 0
    assert metrics["stale_epochs"] == 2
    assert metrics["stop_reason"] == "patience"
    assert len(metrics["history"]) == metrics["epochs"] == metrics["updates"] == 3
    by_type = metrics["action_type_nll"]["by_type"]
    assert set(by_type) == {"2"}
    assert by_type["2"]["count"] == 1
    assert by_type["2"]["target_mass"] == pytest.approx(1.0)
    # Hand oracle for a one-hot target: its full soft NLL is assigned to type 2.
    assert by_type["2"]["nll_contribution"] == pytest.approx(metrics["validation_complete_action_nll"])


def test_rare_recall_treats_validation_action_type_absent_from_train_as_frequency_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark, "SpecialistModelV3", _TinyGateModel)
    monkeypatch.setattr(benchmark, "_load_production_vocabulary_v3", lambda: type("Vocabulary", (), {"recognized_card_ids": (1,)})())
    step_input = type("StepInput", (), {"stop_available": False})()

    def state_for(action_type: int) -> RelationalStateV3:
        return RelationalStateV3(
            (0.0,) * 41, (),
            (
                ActionCandidateV3("target", action_type, None, None, (), (), 0),
                ActionCandidateV3("other", 3, None, None, (), (), 0),
            ),
        )

    # Type 13 appears only in validation.  Its train frequency is therefore
    # exactly zero, which satisfies the declared <=1 rare-action rule.
    train = benchmark._GateStepV3("lane", "train-record", "c", "train", object(), step_input, state_for(2), 0, (1.0, 0.0), 2)
    valid = benchmark._GateStepV3("lane", "valid-record", "c", "validation", object(), step_input, state_for(13), 0, (1.0, 0.0), 13)
    metrics = benchmark._run_candidate_v3("R3-A", (train, valid), seed=7, max_epochs=1, patience=1, min_delta=0.0, device=torch.device("cpu"))

    rare = metrics["rare_action_recall"]
    assert rare["rule_version"] == "train-action-type-frequency-lte-1-v1"
    assert rare["eligible"] == 1
    assert rare["status"] == "measured"
    assert rare["value"] == pytest.approx(1.0)


def test_gate_production_candidate_parameter_counts_are_pinned() -> None:
    vocabulary = benchmark._load_production_vocabulary_v3()
    card_count = max(vocabulary.recognized_card_ids)
    models = {
        "current-R2": benchmark.SpecialistPolicyModelV1(benchmark.SpecialistModelConfigV1(card_vocabulary_size=card_count, representation_version=2)),
        "R3-A": benchmark.SpecialistModelV3(card_vocabulary_size=card_count, seed=7, encoder_kind="zone-deepsets"),
        "R3-B": benchmark.SpecialistModelV3(card_vocabulary_size=card_count, seed=7, encoder_kind="relation-attention"),
    }
    assert {name: sum(parameter.numel() for parameter in model.parameters()) for name, model in models.items()} == {
        "current-R2": 452035, "R3-A": 3776386, "R3-B": 3867138,
    }
