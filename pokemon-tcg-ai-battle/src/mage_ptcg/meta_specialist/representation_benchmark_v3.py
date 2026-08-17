"""Formal sealed Gate 1 runner plus an explicitly synthetic smoke benchmark.

``run_gate1_v3`` consumes only closed real-record input manifests and writes a
strictly self-validated device-specific artifact.  The older synthetic
``run_representation_benchmark_v3`` remains a smoke helper and is never a Gate
performance result.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from collections.abc import Mapping
import base64
import hashlib
import math
import re
import os

import torch
from torch import nn
from torch.nn import functional as F

from mage_ptcg.meta_specialist.neural_model_v3 import RelationAwareEncoderV3, SpecialistModelV3, ZoneDeepSetsEncoderV3
from mage_ptcg.meta_specialist.neural_model_v1 import SpecialistModelConfigV1, SpecialistPolicyModelV1
from mage_ptcg.meta_specialist.representation_v3 import (
    ActionCandidateV3, EntityTokenV3, RelationalStateV3,
    representation_v3_from_model_input_v1,
    representation_v3_from_step_input_v1,
    stable_action_id_v3,
)


@dataclass(frozen=True, slots=True)
class _Example:
    state: RelationalStateV3
    label: int


@dataclass(frozen=True, slots=True)
class Gate1ResultV3:
    """Pinned execution ledger for the representation-selection gate."""

    status: str
    seeds: tuple[int, ...]
    runs: tuple[Mapping[str, object], ...]
    output_path: Path
    decision_path: Path


@dataclass(frozen=True, slots=True)
class _GateStepV3:
    lane: str
    record_id: str
    component_id: str
    partition: str
    model_input: object
    step_input: object
    state: RelationalStateV3
    target_index: int
    target_masses: tuple[float, ...]
    target_action_type: int | None
    episode_id: str = ""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_external_sha256_v3(value: object, *, name: str) -> str:
    """Validate an out-of-band SHA-256 value before using it as an anchor."""
    if type(value) is not str or _LOWER_SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


_GATE_SHARD_RE = re.compile(r"dataset-[0-9]{4}\.jsonl\Z")
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GATE_LANES_V3 = ("alakazam", "archaludon")
_GATE_SEEDS_V3 = (7, 17, 29)
_GATE_CANDIDATES_V3: dict[str, tuple[str, int, int]] = {
    "current-R2": ("SpecialistPolicyModelV1", 2, 452_035),
    "R3-A": ("ZoneDeepSetsEncoderV3", 3, 3_776_386),
    "R3-B": ("RelationAwareEncoderV3", 3, 3_867_138),
}
_GATE_SELECTION_RULE_V3 = "coverage-first-positive-stop-then-rare-validation-then-validation-stop-v3;first-common-r2-r3-eligible-per-shard-line-order"
_GATE_COVERAGE_KEYS_V3 = {
    "learned_stop_domain_count", "positive_stop_target_count", "ordered_nonempty_prefix_count",
    "validation_positive_stop_target_count", "prefix_conditioned_positive_stop_target_count",
    "rare_rule_version", "rare_anchor",
}


def _gate_shard_path_v3(root: Path, shard: object) -> Path:
    if type(shard) is not str or _GATE_SHARD_RE.fullmatch(shard) is None:
        raise ValueError("Gate shard must be a strict dataset-NNNN.jsonl basename")
    path = (root / shard).resolve()
    if path.parent != root.resolve():
        raise ValueError("Gate shard escapes its pinned root")
    return path


def validate_gate_snapshot_v3(root: str | Path, snapshot: Mapping[str, object]) -> dict[str, str]:
    """Fail closed on a snapshot's closed chunk set and total record count."""
    root_path = Path(root).resolve()
    chunks = snapshot.get("dataset_chunks")
    if type(chunks) is not list or type(snapshot.get("examples_total")) is not int:
        raise ValueError("snapshot index lacks closed dataset chunk/count metadata")
    expected: dict[str, str] = {}
    for row in chunks:
        required = {"dataset_snapshot_sha256", "manifest_content_hash", "manifest_id", "path"}
        if type(row) is not dict or set(row) != required or type(row.get("path")) is not str:
            raise ValueError("snapshot dataset chunk is malformed")
        declared = Path(row["path"])
        if declared.is_absolute() or ".." in declared.parts:
            raise ValueError("snapshot dataset chunk path escapes its declared root")
        name = declared.name
        _gate_shard_path_v3(root_path, name)
        expected_hash = row["dataset_snapshot_sha256"]
        if type(expected_hash) is not str or len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
            raise ValueError("snapshot dataset chunk hash is malformed")
        if name in expected:
            raise ValueError("snapshot repeats a dataset chunk")
        expected[name] = expected_hash
    actual = {path.name for path in root_path.glob("dataset-*.jsonl") if _GATE_SHARD_RE.fullmatch(path.name)}
    if actual != set(expected):
        raise ValueError("snapshot dataset chunks do not match physical chunk set")
    hashes = {name: _file_hash(_gate_shard_path_v3(root_path, name)) for name in sorted(expected)}
    if any(hashes[name] != expected[name] for name in hashes):
        raise ValueError("snapshot dataset chunk SHA-256 does not match physical bytes")
    line_count = sum(sum(1 for _ in _gate_shard_path_v3(root_path, name).open(encoding="utf-8")) for name in expected)
    if line_count != snapshot["examples_total"]:
        raise ValueError("snapshot examples_total does not match physical dataset rows")
    return hashes


def _gate_input_core(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "manifest_sha256"}


def _finite_number_v3(value: object, *, minimum: float | None = None, maximum: float | None = None) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and (minimum is None or float(value) >= minimum)
        and (maximum is None or float(value) <= maximum)
    )


def _validate_gate_metrics_v3(metrics: object, *, candidate: str, budget: Mapping[str, object], device: str) -> None:
    required = {
        "best_validation_token_nll", "validation_complete_action_nll", "top1", "top3",
        "topk_soft_target_tie_rule", "rare_action_recall", "action_type_nll", "p50_ms",
        "p95_ms", "cpu_preprocessing_ms", "cuda_vram", "epochs", "updates", "best_epoch",
        "stale_epochs", "stop_reason", "history", "parameter_delta_l1", "parameter_count",
        "checkpoint_sha256", "record_ids", "step_count",
    }
    if type(metrics) is not dict or set(metrics) != required:
        raise ValueError("Gate 1 measured metrics have an invalid closed schema")
    if any(not _finite_number_v3(metrics[key], minimum=0.0) for key in (
        "best_validation_token_nll", "validation_complete_action_nll", "p50_ms", "p95_ms",
        "cpu_preprocessing_ms", "parameter_delta_l1",
    )):
        raise ValueError("Gate 1 measured metrics contain a non-finite or negative value")
    if not _finite_number_v3(metrics["top1"], minimum=0.0, maximum=1.0) or not _finite_number_v3(metrics["top3"], minimum=0.0, maximum=1.0) or metrics["top3"] < metrics["top1"]:
        raise ValueError("Gate 1 top-k metrics are outside their probability domain")
    if metrics["topk_soft_target_tie_rule"] != "lowest-token-index-among-max-mass":
        raise ValueError("Gate 1 top-k soft-target tie rule changed")
    expected_parameter_count = _GATE_CANDIDATES_V3[candidate][2]
    if metrics["parameter_count"] != expected_parameter_count or not _finite_number_v3(metrics["parameter_delta_l1"], minimum=0.0) or metrics["parameter_delta_l1"] <= 0:
        raise ValueError("Gate 1 candidate parameter identity/update evidence is invalid")
    if type(metrics["checkpoint_sha256"]) is not str or _LOWER_SHA256_RE.fullmatch(metrics["checkpoint_sha256"]) is None:
        raise ValueError("Gate 1 checkpoint hash is invalid")
    record_ids = metrics["record_ids"]
    if (type(record_ids) is not list or not record_ids or record_ids != sorted(record_ids)
            or len(record_ids) != len(set(record_ids))
            or any(type(value) is not str or _LOWER_SHA256_RE.fullmatch(value) is None for value in record_ids)):
        raise ValueError("Gate 1 validation record IDs are invalid")
    if type(metrics["step_count"]) is not int or metrics["step_count"] < len(record_ids):
        raise ValueError("Gate 1 validation step count is invalid")
    for key in ("epochs", "updates", "best_epoch", "stale_epochs"):
        if type(metrics[key]) is not int:
            raise ValueError("Gate 1 early-stop counters must be integers")
    if not 1 <= metrics["epochs"] <= budget["max_epochs"] or metrics["updates"] != metrics["epochs"]:
        raise ValueError("Gate 1 update/epoch budget is invalid")
    if not 0 <= metrics["best_epoch"] < metrics["epochs"] or not 0 <= metrics["stale_epochs"] <= metrics["epochs"]:
        raise ValueError("Gate 1 early-stop evidence is invalid")
    history = metrics["history"]
    if type(history) is not list or len(history) != metrics["epochs"] or any(not _finite_number_v3(value, minimum=0.0) for value in history):
        raise ValueError("Gate 1 validation history is invalid")
    if not math.isclose(float(history[metrics["best_epoch"]]), float(metrics["best_validation_token_nll"]), rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError("Gate 1 best epoch does not bind its validation NLL")
    if metrics["stop_reason"] == "patience":
        if metrics["stale_epochs"] < budget["patience"]:
            raise ValueError("Gate 1 patience stop lacks stale-epoch evidence")
    elif metrics["stop_reason"] == "max_epochs":
        if metrics["epochs"] != budget["max_epochs"] or metrics["stale_epochs"] >= budget["patience"]:
            raise ValueError("Gate 1 max-epoch stop evidence is inconsistent")
    else:
        raise ValueError("Gate 1 stop reason is invalid")

    rare = metrics["rare_action_recall"]
    if type(rare) is not dict or set(rare) != {"rule_version", "eligible", "value", "status"} or rare["rule_version"] != "train-action-type-frequency-lte-1-v1" or type(rare["eligible"]) is not int or rare["eligible"] < 0:
        raise ValueError("Gate 1 rare-action metric is malformed")
    if rare["eligible"] == 0:
        if rare["value"] is not None or rare["status"] != "no_eligible_targets":
            raise ValueError("Gate 1 empty rare-action metric is dishonest")
    elif not _finite_number_v3(rare["value"], minimum=0.0, maximum=1.0) or rare["status"] != "measured":
        raise ValueError("Gate 1 measured rare-action metric is invalid")

    action = metrics["action_type_nll"]
    if type(action) is not dict or set(action) != {"by_type", "macro", "overall"} or type(action["by_type"]) is not dict or not action["by_type"]:
        raise ValueError("Gate 1 action-type NLL is malformed")
    normalized_values: list[float] = []
    contribution = 0.0
    for kind, row in action["by_type"].items():
        if type(kind) is not str or (kind != "STOP" and not kind.isdigit()) or type(row) is not dict or set(row) != {"count", "target_mass", "nll_contribution", "normalized_nll"}:
            raise ValueError("Gate 1 action-type NLL row is malformed")
        if type(row["count"]) is not int or row["count"] < 1 or not _finite_number_v3(row["target_mass"], minimum=0.0) or row["target_mass"] <= 0 or not _finite_number_v3(row["nll_contribution"], minimum=0.0) or not _finite_number_v3(row["normalized_nll"], minimum=0.0):
            raise ValueError("Gate 1 action-type NLL row is invalid")
        if not math.isclose(float(row["normalized_nll"]), float(row["nll_contribution"]) / float(row["target_mass"]), rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("Gate 1 action-type normalized NLL is inconsistent")
        normalized_values.append(float(row["normalized_nll"]))
        contribution += float(row["nll_contribution"])
    expected_macro = math.fsum(normalized_values) / len(normalized_values)
    expected_overall = contribution / metrics["step_count"]
    if not _finite_number_v3(action["macro"], minimum=0.0) or not _finite_number_v3(action["overall"], minimum=0.0) or not math.isclose(float(action["macro"]), expected_macro, rel_tol=1e-6, abs_tol=1e-6) or not math.isclose(float(action["overall"]), expected_overall, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError("Gate 1 action-type aggregate NLL is inconsistent")
    if not math.isclose(float(action["overall"]), float(metrics["best_validation_token_nll"]), rel_tol=1e-5, abs_tol=1e-5):
        raise ValueError("Gate 1 action-type contributions do not sum to token NLL")
    if metrics["p95_ms"] < metrics["p50_ms"]:
        raise ValueError("Gate 1 inference latency quantiles are invalid")
    cuda_vram = metrics["cuda_vram"]
    if device == "cpu":
        expected_cuda_keys = {"measured", "peak_allocated_bytes", "peak_reserved_bytes", "blocker"}
        if type(cuda_vram) is not dict or set(cuda_vram) != expected_cuda_keys or cuda_vram != {"measured": False, "peak_allocated_bytes": None, "peak_reserved_bytes": None, "blocker": "CPU execution requested"}:
            raise ValueError("Gate 1 CPU result fabricates CUDA memory evidence")
    else:
        expected_cuda_keys = {"measured", "peak_allocated_bytes", "peak_reserved_bytes", "device_name", "runtime"}
        if (type(cuda_vram) is not dict or set(cuda_vram) != expected_cuda_keys or cuda_vram["measured"] is not True
                or type(cuda_vram["peak_allocated_bytes"]) is not int or cuda_vram["peak_allocated_bytes"] <= 0
                or type(cuda_vram["peak_reserved_bytes"]) is not int or cuda_vram["peak_reserved_bytes"] <= 0
                or not isinstance(cuda_vram["device_name"], str) or not cuda_vram["device_name"]
                or not isinstance(cuda_vram["runtime"], str) or not cuda_vram["runtime"]):
            raise ValueError("Gate 1 CUDA result lacks measured device evidence")


def _read_gate_result_v3(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"schema", "status", "execution_device", "seeds", "runs", "selection", "result_sha256"}
    if type(payload) is not dict or set(payload) != required or payload["schema"] != "meta-specialist-gate1-v3":
        raise ValueError("Gate 1 result has an invalid closed schema")
    core = {key: value for key, value in payload.items() if key != "result_sha256"}
    if _hash(core) != payload["result_sha256"]:
        raise ValueError("Gate 1 result self hash is invalid")
    device = payload["execution_device"]
    if payload["status"] not in {"BLOCKED", "BASELINE_RETAINED"} or type(device) is not str or (device != "cpu" and re.fullmatch(r"cuda:[0-9]+", device) is None):
        raise ValueError("Gate 1 result status/device is invalid")
    selection = payload["selection"]
    selection_keys = {"decision_status", "preferred", "blockers", "rule"}
    if (type(selection) is not dict or set(selection) != selection_keys
            or type(selection["blockers"]) is not list
            or not selection["blockers"] or len(selection["blockers"]) != len(set(selection["blockers"]))
            or any(type(value) is not str or not value for value in selection["blockers"])
            or type(selection["rule"]) is not str or not selection["rule"]):
        raise ValueError("Gate 1 blocked selection contract is invalid")
    blockers = set(selection["blockers"])
    if "v2_major_regression_threshold_unspecified" not in blockers or ((device == "cpu") != ("cuda_measurement_unavailable" in blockers)):
        raise ValueError("Gate 1 selection blockers contradict status/device")
    if payload["status"] == "BLOCKED":
        if selection["decision_status"] != "BLOCKED_THRESHOLD_UNSPECIFIED" or selection["preferred"] is not None:
            raise ValueError("Gate 1 legacy blocked selection contract is invalid")
    else:
        if (selection["decision_status"] != "BASELINE_RETAINED_R3_UNAPPROVED"
                or selection["preferred"] != "current-R2"):
            raise ValueError("Gate 1 baseline-retained selection contract is invalid")
    if payload["seeds"] != list(_GATE_SEEDS_V3):
        raise ValueError("Gate 1 result must contain the three formal seeds")
    if type(payload["runs"]) is not list or len(payload["runs"]) != 18:
        raise ValueError("Gate 1 result must contain the complete 18-cell matrix")
    row_statuses = {row.get("status") for row in payload["runs"] if type(row) is dict}
    if row_statuses not in ({"measured"}, {"planned"}):
        raise ValueError("Gate 1 result may contain either a measured or planned matrix")
    planned = row_statuses == {"planned"}
    required_row = {"lane", "candidate", "adapter", "representation_version", "seed", "split_manifest_sha256", "input_manifest_sha256", "budget", "target", "status", "coverage"} | (set() if planned else {"metrics"})
    seen: set[tuple[object, object, object]] = set()
    for row in payload["runs"]:
        if type(row) is not dict or set(row) != required_row or row["status"] != ("planned" if planned else "measured"):
            raise ValueError("Gate 1 result run row is malformed")
        cell = (row["lane"], row["candidate"], row["seed"])
        if cell in seen:
            raise ValueError("Gate 1 result repeats a matrix cell")
        seen.add(cell)
        expected_candidate = _GATE_CANDIDATES_V3.get(row["candidate"])
        if row["lane"] not in _GATE_LANES_V3 or expected_candidate is None or (row["adapter"], row["representation_version"]) != expected_candidate[:2] or row["seed"] not in _GATE_SEEDS_V3:
            raise ValueError("Gate 1 result contains an unknown formal matrix cell")
        if type(row["split_manifest_sha256"]) is not str or _LOWER_SHA256_RE.fullmatch(row["split_manifest_sha256"]) is None or type(row["input_manifest_sha256"]) is not str or _LOWER_SHA256_RE.fullmatch(row["input_manifest_sha256"]) is None:
            raise ValueError("Gate 1 row input/split hash is invalid")
        budget = row["budget"]
        if (type(budget) is not dict or set(budget) != {"max_epochs", "patience", "min_delta"}
                or type(budget["max_epochs"]) is not int or budget["max_epochs"] < 1
                or type(budget["patience"]) is not int or not 1 <= budget["patience"] <= budget["max_epochs"]
                or not _finite_number_v3(budget["min_delta"], minimum=0.0)):
            raise ValueError("Gate 1 row budget is invalid")
        if row["target"] != "complete-legal-action-autoregressive-semantic-plus-stop-v1" or type(row["coverage"]) is not dict or set(row["coverage"]) != _GATE_COVERAGE_KEYS_V3:
            raise ValueError("Gate 1 row target/coverage is invalid")
        for key in (
            "learned_stop_domain_count", "positive_stop_target_count", "ordered_nonempty_prefix_count",
            "validation_positive_stop_target_count", "prefix_conditioned_positive_stop_target_count",
        ):
            if type(row["coverage"].get(key)) is not int or row["coverage"][key] < 0:
                raise ValueError("Gate 1 row coverage counter is invalid")
        if not planned:
            _validate_gate_metrics_v3(row["metrics"], candidate=row["candidate"], budget=budget, device=device)
    expected_cells = {(lane, candidate, seed) for lane in _GATE_LANES_V3 for candidate in _GATE_CANDIDATES_V3 for seed in _GATE_SEEDS_V3}
    if seen != expected_cells:
        raise ValueError("Gate 1 result does not contain the exact formal matrix")
    for lane in _GATE_LANES_V3:
        lane_rows = [row for row in payload["runs"] if row["lane"] == lane]
        if len({row["input_manifest_sha256"] for row in lane_rows}) != 1 or len({row["split_manifest_sha256"] for row in lane_rows}) != 1:
            raise ValueError("Gate 1 result lane input/split hashes drift")
        if len({_canonical(row["budget"]) for row in lane_rows}) != 1 or len({_canonical(row["coverage"]) for row in lane_rows}) != 1:
            raise ValueError("Gate 1 result lane budget/coverage drifts")
        if not planned and len({tuple(row["metrics"]["record_ids"]) for row in lane_rows}) != 1:
            raise ValueError("Gate 1 result budget or record IDs drift")
        positive_stop = lane_rows[0]["coverage"]["positive_stop_target_count"]
        validation_positive_stop = lane_rows[0]["coverage"]["validation_positive_stop_target_count"]
        ordered_prefix = lane_rows[0]["coverage"]["ordered_nonempty_prefix_count"]
        if ((positive_stop == 0) != (f"{lane}_positive_learned_stop_coverage_unavailable" in blockers)
                or (validation_positive_stop == 0) != (f"{lane}_validation_positive_learned_stop_coverage_unavailable" in blockers)
                or (ordered_prefix == 0) != (f"{lane}_ordered_target_coverage_unavailable" in blockers)):
            raise ValueError("Gate 1 coverage blockers contradict the pinned lane coverage")
        rare_blocker = f"{lane}_rare_action_coverage_unavailable"
        if planned:
            if rare_blocker in blockers:
                raise ValueError("planned Gate matrix cannot claim measured rare coverage")
        else:
            rare_unavailable = all(row["metrics"]["rare_action_recall"]["eligible"] == 0 for row in lane_rows)
            if rare_unavailable != (rare_blocker in blockers):
                raise ValueError("Gate 1 rare-action blocker contradicts lane measurements")
    return payload


def read_gate_result_v3(path: str | Path) -> dict[str, object]:
    """Read a self-consistent diagnostic Gate artifact.

    This verifies only the artifact's internal self hash.  It is useful for
    diagnostics, but it does *not* establish provenance against a writer that
    can rewrite both the payload and its self hash.  Selection consumers must
    use :func:`verify_gate_result_anchor_v3` with out-of-band anchors.
    """
    return _read_gate_result_v3(path)


def verify_gate_result_anchor_v3(
    path: str | Path, *, expected_file_sha256: str, expected_result_sha256: str,
) -> dict[str, object]:
    """Read a Gate result only when independently pinned file/result hashes match.

    Both digests are mandatory external trust anchors.  The file digest binds
    the exact on-disk bytes and therefore rejects an attacker who recomputes a
    valid ``result_sha256`` after changing a diagnostic artifact.  The caller
    is responsible for obtaining these values from a trusted manifest, review
    record, or other out-of-band authority.
    """
    expected_file = _require_external_sha256_v3(
        expected_file_sha256, name="expected_file_sha256",
    )
    expected_result = _require_external_sha256_v3(
        expected_result_sha256, name="expected_result_sha256",
    )
    target = Path(path)
    if _file_hash(target) != expected_file:
        raise ValueError("Gate 1 result file SHA-256 does not match its external anchor")
    payload = _read_gate_result_v3(target)
    if payload["result_sha256"] != expected_result:
        raise ValueError("Gate 1 result self SHA-256 does not match its external anchor")
    return payload


def _atomic_write_gate_result_v3(path: str | Path, payload: Mapping[str, object]) -> dict[str, object]:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    data = _canonical(payload)
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    reloaded = _read_gate_result_v3(target)
    if reloaded != payload:
        raise RuntimeError("Gate result atomic reload differs from written payload")
    return reloaded


def _read_gate_selection_manifest_v3(path: str | Path) -> dict[str, object]:
    """Validate the closed baseline-retention decision and its result binding."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "schema", "decision_status", "active_representation", "r3_promotion_status",
        "blockers", "rule", "gate_result_path", "gate_result_file_sha256",
        "gate_result_sha256", "decision_sha256",
    }
    if type(payload) is not dict or set(payload) != required or payload["schema"] != "meta-specialist-gate1-selection-v1":
        raise ValueError("Gate 1 selection manifest has an invalid closed schema")
    core = {key: value for key, value in payload.items() if key != "decision_sha256"}
    if _hash(core) != payload["decision_sha256"]:
        raise ValueError("Gate 1 selection manifest self hash is invalid")
    if (payload["decision_status"] != "BASELINE_RETAINED_R3_UNAPPROVED"
            or payload["active_representation"] != "current-R2"
            or payload["r3_promotion_status"] != "UNAPPROVED"
            or type(payload["blockers"]) is not list or not payload["blockers"]
            or len(payload["blockers"]) != len(set(payload["blockers"]))
            or any(type(blocker) is not str or not blocker for blocker in payload["blockers"])
            or type(payload["rule"]) is not str or not payload["rule"]):
        raise ValueError("Gate 1 selection manifest does not fail closed to current-R2")
    result_name = payload["gate_result_path"]
    if type(result_name) is not str or Path(result_name).name != result_name or not result_name.startswith("gate1-result-v3-"):
        raise ValueError("Gate 1 selection manifest result path is unsafe")
    _require_external_sha256_v3(payload["gate_result_file_sha256"], name="gate_result_file_sha256")
    _require_external_sha256_v3(payload["gate_result_sha256"], name="gate_result_sha256")
    return payload


def read_gate_selection_manifest_v3(path: str | Path) -> dict[str, object]:
    """Read a selection manifest and bind it to its adjacent Gate result bytes.

    This establishes a local artifact chain.  A caller that needs a trust
    boundary beyond the writable artifact directory must externally pin the
    selection manifest file too, then call ``verify_gate_result_anchor_v3``
    using the hashes stored here.
    """
    target = Path(path)
    payload = _read_gate_selection_manifest_v3(target)
    result = verify_gate_result_anchor_v3(
        target.parent / payload["gate_result_path"],
        expected_file_sha256=payload["gate_result_file_sha256"],
        expected_result_sha256=payload["gate_result_sha256"],
    )
    if (result["status"] != "BASELINE_RETAINED"
            or result["selection"]["decision_status"] != payload["decision_status"]
            or result["selection"]["preferred"] != payload["active_representation"]
            or result["selection"]["blockers"] != payload["blockers"]
            or result["selection"]["rule"] != payload["rule"]):
        raise ValueError("Gate 1 selection manifest disagrees with its result artifact")
    return payload


def _atomic_write_gate_selection_manifest_v3(path: str | Path, payload: Mapping[str, object]) -> dict[str, object]:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    data = _canonical(payload)
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    reloaded = read_gate_selection_manifest_v3(target)
    if reloaded != payload:
        raise RuntimeError("Gate selection atomic reload differs from written payload")
    return reloaded


def _production_vocabulary_identity_v3() -> dict[str, object]:
    """Load the sole production vocabulary authority and expose its closed pin."""
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1
    vocabulary = load_production_card_vocabulary_v1()
    return {
        "schema": vocabulary.schema_version,
        "source_sha256": vocabulary.source_sha256,
        "environment_version": vocabulary.environment_version,
        "count": len(vocabulary.recognized_card_ids),
        "max_id": max(vocabulary.recognized_card_ids),
        "test_only": vocabulary.test_only,
        "usage_decision": vocabulary.usage_decision,
        "permission_decision": vocabulary.permission_decision,
    }


def _load_production_vocabulary_v3():
    from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1
    return load_production_card_vocabulary_v1()


def _r3_step_projection_error_v3(record: Mapping[str, object], model_payload: object, vocabulary: object) -> str | None:
    """Return a deterministic common-eligibility rejection before pinning."""
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import ExtractedSpecialistModelInputV1, build_specialist_step_input_v1
    from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import specialist_model_input_from_training_payload_v2
    from mage_ptcg.meta_specialist.representation_v3 import RepresentationV3Error
    try:
        model_input = specialist_model_input_from_training_payload_v2(model_payload)
        groups: dict[bytes, list[int]] = {}
        for index, semantic in enumerate(model_input.candidate_rows): groups.setdefault(_canonical(semantic.to_dict()), []).append(index)
        offsets: dict[bytes, int] = {}; local_to_index: dict[str, int] = {}
        for action in sorted(record["legal_actions"], key=lambda row: row["local_action_id"]):
            key = _canonical(action["semantic_action"]); offset = offsets.get(key, 0)
            local_to_index[action["local_action_id"]] = groups[key][offset]; offsets[key] = offset + 1
        extracted = ExtractedSpecialistModelInputV1(model_input, record["model_input_id"], local_to_index)
        aliases = {key: sorted(local for local, index in local_to_index.items() if _canonical(model_input.candidate_rows[index].to_dict()) == key) for key in groups}
        for row in semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary):
            counts: dict[bytes, int] = {}; prefix: list[str] = []
            for semantic in row["semantic_prefix"]:
                key = _canonical(semantic); offset = counts.get(key, 0); prefix.append(aliases[key][offset]); counts[key] = offset + 1
            step = build_specialist_step_input_v1(extracted, tuple(prefix))
            representation_v3_from_step_input_v1(model_input, step, allow_unbound_selected=True)
    except RepresentationV3Error as exc:
        if str(exc) in {"ambiguous_public_locator", "selectable endpoint is not uniquely public"}:
            return str(exc)
        raise
    return None


def _record_coverage_v3(
    records: list[dict[str, object]], *, assignments: Mapping[str, str], vocabulary: object,
) -> dict[str, int]:
    """Count canonical learned-STOP/prefix coverage after the final split."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2

    coverage = {
        "learned_stop_domain_count": 0,
        "positive_stop_target_count": 0,
        "ordered_nonempty_prefix_count": 0,
        "validation_positive_stop_target_count": 0,
        "prefix_conditioned_positive_stop_target_count": 0,
    }
    for record in records:
        partition = assignments.get(record["record_id"])
        if partition not in {"train", "validation"}:
            raise ValueError("Gate coverage record lacks a final split assignment")
        for row in semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary):
            tokens = row["token_masses"]
            has_stop = any(token["kind"] == "stop" for token in tokens)
            stop_mass = math.fsum(float(token["mass"]) for token in tokens if token["kind"] == "stop")
            positive_stop = has_stop and stop_mass > 0.0
            nonempty_prefix = bool(row["semantic_prefix"])
            coverage["learned_stop_domain_count"] += int(has_stop)
            coverage["positive_stop_target_count"] += int(positive_stop)
            coverage["ordered_nonempty_prefix_count"] += int(
                nonempty_prefix and bool(record.get("selection_order_sensitive", False))
            )
            coverage["validation_positive_stop_target_count"] += int(
                positive_stop and partition == "validation"
            )
            coverage["prefix_conditioned_positive_stop_target_count"] += int(
                positive_stop and nonempty_prefix
            )
    return coverage


def build_gate1_input_manifest_v3(
    *, lane: str, root: str | Path, output_path: str | Path,
    validation_fraction: float = 0.2, ubiquitous_threshold: int = 2,
    qualification_time_utc: str = "2026-08-09T00:00:00Z",
) -> Path:
    """Pin exactly 32 common-eligible records with deterministic coverage anchors."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_trusted_permission_set_v1, canonical_json_bytes_v2,
        require_qualified_training_record_v2,
    )
    from mage_ptcg.meta_specialist.bc_trainer_v3 import build_split_manifest_v3
    root_path = Path(root)
    snapshot_path, teacher_path = root_path / "snapshot_index.json", root_path / "teacher_dataset_manifest.json"
    if not lane or not snapshot_path.is_file() or not teacher_path.is_file():
        raise FileNotFoundError("Gate 1 root lacks sealed snapshot_index.json or teacher_dataset_manifest.json")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    teacher = json.loads(teacher_path.read_text(encoding="utf-8"))
    if (type(snapshot) is not dict or snapshot.get("schema_version") != "specialist-training-snapshot-index-v1"
            or type(snapshot.get("dataset_snapshot_sha256")) is not str):
        raise ValueError("snapshot index has an invalid schema or dataset identity")
    validate_gate_snapshot_v3(root_path, snapshot)
    duplicate_cap = snapshot.get("duplicate_cap")
    if (type(duplicate_cap) is not dict or type(duplicate_cap.get("ubiquitous_near_duplicate_ids")) is not list
            or type(duplicate_cap.get("ubiquity_min_episodes")) is not int):
        raise ValueError("snapshot index lacks authoritative duplicate-cap metadata")
    permission = teacher.get("permission_manifest") if isinstance(teacher, dict) else None
    if not isinstance(permission, dict):
        raise ValueError("teacher manifest lacks a permission manifest")
    permission_bytes = canonical_json_bytes_v2(permission)
    trusted = build_trusted_permission_set_v1((permission_bytes,))
    vocabulary = _load_production_vocabulary_v3()
    vocabulary_identity = _production_vocabulary_identity_v3()
    selected: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    selected_shards: set[str] = set()
    selected_episodes: set[str] = set()
    coverage = {"learned_stop_domain_count": 0, "positive_stop_target_count": 0, "ordered_nonempty_prefix_count": 0}
    shards = sorted(path for path in root_path.glob("dataset-*.jsonl") if _GATE_SHARD_RE.fullmatch(path.name))[:128]
    if len(shards) < 32:
        raise ValueError("Gate root has fewer than 32 sealed dataset shards")
    for line_no in range(1, 11):
      for path in shards:
        shard = path.name
        if shard in selected_shards:
            continue
        with path.open(encoding="utf-8") as handle:
            raw = ""
            for _ in range(line_no): raw = handle.readline()
        if not raw.endswith("\n"):
            continue
        record = json.loads(raw)
        model_payload, _ = require_qualified_training_record_v2(record, vocabulary=vocabulary, trusted_permissions=trusted, qualification_time_utc=qualification_time_utc)
        source = record["source"]
        if source["artifact_sha256"] != permission["artifact_sha256"] or source["permission_manifest_id"] != permission["permission_manifest_id"]:
            raise ValueError(f"{path}: record source does not match trusted permission")
        # Reject only before pinning and record the reproducible reason.  The
        # exact canonical soft-target step domain is tested, not whole-input
        # dead candidates nor candidate-specific model behavior.
        reason = _r3_step_projection_error_v3(record, model_payload, vocabulary)
        if reason is not None:
            rejected.append({"shard": shard, "line": line_no, "record_id": record["record_id"], "reason": reason})
            continue
        episode_id = record["episode_id_hash"]
        if episode_id in selected_episodes:
            # This is not a representational rejection: it is an explicit
            # leakage guard while seeking one pinned record per shard.
            continue
        selected.append({"shard": shard, "line": line_no, "record_id": record["record_id"], "content_hash": record["content_hash"], "raw_line_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()})
        records.append(record)
        from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
        for loss_row in semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary):
            tokens = loss_row["token_masses"]
            stop_mass = sum(float(token["mass"]) for token in tokens if token["kind"] == "stop")
            coverage["learned_stop_domain_count"] += int(stop_mass >= 0 and any(token["kind"] == "stop" for token in tokens))
            coverage["positive_stop_target_count"] += int(stop_mass > 0)
            coverage["ordered_nonempty_prefix_count"] += int(bool(loss_row["semantic_prefix"]) and bool(record.get("selection_order_sensitive", False)))
        selected_shards.add(shard)
        selected_episodes.add(episode_id)
        if len(records) == 32:
            break
      if len(records) == 32:
          break
    if len(records) != 32 or len(selected_shards) != 32 or len(selected_episodes) != 32:
        raise ValueError(f"{lane}: could not find 32 common R2/R3-eligible bounded records")
    # Coverage-first replacement is deterministic but never relaxes the closed
    # shard/episode/qualification/R3 constraints: search the already selected
    # shard's bounded alternatives only when the baseline contains no positive
    # learned STOP target.
    if coverage["positive_stop_target_count"] == 0:
        from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
        for selected_index, entry in enumerate(tuple(selected)):
            path = _gate_shard_path_v3(root_path, entry["shard"])
            for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(keepends=True), start=1):
                if line_no == entry["line"]:
                    continue
                candidate = json.loads(raw)
                # A positive learned STOP can only arise from an empty complete
                # action in the teacher mass rows.  This cheap raw prefilter
                # avoids reconstructing thousands of ordinary non-STOP rows.
                mass_rows = candidate.get("teacher", {}).get("mass_rows", ())
                if not any(type(row) is dict and row.get("selection") == [] and float(row.get("weight", 0)) > 0 for row in mass_rows):
                    continue
                if candidate["episode_id_hash"] in selected_episodes - {records[selected_index]["episode_id_hash"]}:
                    continue
                model_payload, _ = require_qualified_training_record_v2(candidate, vocabulary=vocabulary, trusted_permissions=trusted, qualification_time_utc=qualification_time_utc)
                if _r3_step_projection_error_v3(candidate, model_payload, vocabulary) is not None:
                    continue
                candidate_rows = semantic_loss_rows_from_record_v2(candidate, vocabulary=vocabulary)
                positive = sum(float(token["mass"]) for row in candidate_rows for token in row["token_masses"] if token["kind"] == "stop")
                if positive <= 0:
                    continue
                old = records[selected_index]
                records[selected_index] = candidate
                selected[selected_index] = {"shard": path.name, "line": line_no, "record_id": candidate["record_id"], "content_hash": candidate["content_hash"], "raw_line_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
                selected_episodes.remove(old["episode_id_hash"]); selected_episodes.add(candidate["episode_id_hash"])
                coverage = {"learned_stop_domain_count": 0, "positive_stop_target_count": 0, "ordered_nonempty_prefix_count": 0}
                for item in records:
                    for row in semantic_loss_rows_from_record_v2(item, vocabulary=vocabulary):
                        stop_mass = sum(float(token["mass"]) for token in row["token_masses"] if token["kind"] == "stop")
                        coverage["learned_stop_domain_count"] += int(any(token["kind"] == "stop" for token in row["token_masses"]))
                        coverage["positive_stop_target_count"] += int(stop_mass > 0)
                        coverage["ordered_nonempty_prefix_count"] += int(bool(row["semantic_prefix"]) and bool(item.get("selection_order_sensitive", False)))
                break
            if coverage["positive_stop_target_count"]:
                break
    # Next require one validation anchor for action type 0, which is absent
    # from the baseline train partition.  Candidates are streamed only from
    # already selected shards and trial splits preserve the closed 26/6 gate.
    rare_anchor: dict[str, object] | None = None
    for selected_index, entry in enumerate(tuple(selected)):
        path = _gate_shard_path_v3(root_path, entry["shard"])
        for line_no, raw in enumerate(path.open(encoding="utf-8"), start=1):
            if line_no == entry["line"] or '"option_type":0' not in raw:
                continue
            candidate = json.loads(raw)
            if candidate["episode_id_hash"] in selected_episodes - {records[selected_index]["episode_id_hash"]}:
                continue
            model_payload, _ = require_qualified_training_record_v2(candidate, vocabulary=vocabulary, trusted_permissions=trusted, qualification_time_utc=qualification_time_utc)
            if _r3_step_projection_error_v3(candidate, model_payload, vocabulary) is not None:
                continue
            candidate_loss_rows = semantic_loss_rows_from_record_v2(candidate, vocabulary=vocabulary)
            if not any(
                token["kind"] == "semantic" and float(token["mass"]) > 0
                and token["semantic_action"]["option_type"] == 0
                for row in candidate_loss_rows for token in row["token_masses"]
            ):
                continue
            trial = list(records); trial[selected_index] = candidate
            keys = tuple(sorted(key for key in duplicate_cap["ubiquitous_near_duplicate_ids"] if any(record["near_duplicate_id"] == key for record in trial)))
            trial_split = build_split_manifest_v3(trial, validation_fraction=validation_fraction, ubiquitous_threshold=int(duplicate_cap["ubiquity_min_episodes"]), ubiquitous_keys=keys, ubiquitous_rule_version="snapshot-index-duplicate-cap-v1")
            assignment = {row["record_id"]: row["partition"] for row in trial_split.assignments}
            if trial_split.counts != {"train": 26, "validation": 6} or assignment[candidate["record_id"]] != "validation":
                continue
            old_episode = records[selected_index]["episode_id_hash"]
            records = trial
            selected[selected_index] = {"shard": path.name, "line": line_no, "record_id": candidate["record_id"], "content_hash": candidate["content_hash"], "raw_line_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
            selected_episodes.remove(old_episode); selected_episodes.add(candidate["episode_id_hash"])
            rare_anchor = {"action_type": 0, "record_id": candidate["record_id"], "shard": path.name, "line": line_no, "partition": "validation"}
            break
        if rare_anchor is not None:
            break
    def split_for(candidate_records: list[dict[str, object]]):
        keys = tuple(sorted(
            key for key in duplicate_cap["ubiquitous_near_duplicate_ids"]
            if any(record["near_duplicate_id"] == key for record in candidate_records)
        ))
        return build_split_manifest_v3(
            candidate_records, validation_fraction=validation_fraction,
            ubiquitous_threshold=int(duplicate_cap["ubiquity_min_episodes"]),
            ubiquitous_keys=keys, ubiquitous_rule_version="snapshot-index-duplicate-cap-v1",
        )

    # The frequency rule is declared and materialized before the final
    # validation-STOP replacement.  It is never inferred from incidental row
    # density at read time.
    split = split_for(records)
    if split.counts != {"train": 26, "validation": 6} or split.overlap_counters != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
        raise ValueError(f"{lane}: bounded selection does not satisfy required 26/6 leakage-free split")
    assignments = {row["record_id"]: row["partition"] for row in split.assignments}
    coverage = _record_coverage_v3(records, assignments=assignments, vocabulary=vocabulary)

    # A total positive STOP is insufficient Gate evidence when it lands only in
    # train.  Stream each already-selected shard once, in shard/line order, and
    # take the first same-shard replacement that keeps the rare anchor in
    # validation while placing a real learned STOP target in validation.
    if coverage["validation_positive_stop_target_count"] == 0:
        selected_order = sorted(range(len(selected)), key=lambda index: selected[index]["shard"])
        for selected_index in selected_order:
            entry = selected[selected_index]
            if rare_anchor is not None and entry["record_id"] == rare_anchor["record_id"]:
                continue
            path = _gate_shard_path_v3(root_path, entry["shard"])
            for line_no, raw in enumerate(path.open(encoding="utf-8"), start=1):
                if line_no == entry["line"]:
                    continue
                candidate = json.loads(raw)
                mass_rows = candidate.get("teacher", {}).get("mass_rows", ())
                maximum = candidate.get("information_state", {}).get("max_count")
                # A completed selection shorter than max_count reaches a
                # learned STOP while competing semantic continuations remain.
                # Empty-only prefiltering would silently exclude valid
                # prefix-conditioned STOP supervision.
                if type(maximum) is not int or not any(
                    type(row) is dict and type(row.get("selection")) is list
                    and len(row["selection"]) < maximum and float(row.get("weight", 0)) > 0
                    for row in mass_rows
                ):
                    continue
                if candidate["episode_id_hash"] in selected_episodes - {records[selected_index]["episode_id_hash"]}:
                    continue
                model_payload, _ = require_qualified_training_record_v2(
                    candidate, vocabulary=vocabulary, trusted_permissions=trusted,
                    qualification_time_utc=qualification_time_utc,
                )
                if _r3_step_projection_error_v3(candidate, model_payload, vocabulary) is not None:
                    continue
                candidate_rows = semantic_loss_rows_from_record_v2(candidate, vocabulary=vocabulary)
                if math.fsum(
                    float(token["mass"])
                    for row in candidate_rows for token in row["token_masses"]
                    if token["kind"] == "stop"
                ) <= 0.0:
                    continue
                trial = list(records)
                trial[selected_index] = candidate
                trial_split = split_for(trial)
                if trial_split.counts != {"train": 26, "validation": 6} or trial_split.overlap_counters != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
                    continue
                trial_assignments = {row["record_id"]: row["partition"] for row in trial_split.assignments}
                if trial_assignments.get(candidate["record_id"]) != "validation":
                    continue
                if rare_anchor is not None and trial_assignments.get(rare_anchor["record_id"]) != "validation":
                    continue
                trial_coverage = _record_coverage_v3(trial, assignments=trial_assignments, vocabulary=vocabulary)
                if trial_coverage["validation_positive_stop_target_count"] == 0:
                    continue
                old_episode = records[selected_index]["episode_id_hash"]
                records = trial
                split = trial_split
                assignments = trial_assignments
                coverage = trial_coverage
                selected[selected_index] = {
                    "shard": path.name, "line": line_no, "record_id": candidate["record_id"],
                    "content_hash": candidate["content_hash"],
                    "raw_line_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                }
                selected_episodes.remove(old_episode)
                selected_episodes.add(candidate["episode_id_hash"])
                break
            if coverage["validation_positive_stop_target_count"]:
                break
    payload: dict[str, object] = {
        "schema": "meta-specialist-gate1-input-v1", "lane": lane, "root": str(root_path.resolve()),
        "snapshot_index_sha256": _file_hash(snapshot_path), "dataset_snapshot_sha256": snapshot.get("dataset_snapshot_sha256"),
        "teacher_manifest_sha256": _file_hash(teacher_path),
        "trusted_permission_bytes_b64": base64.b64encode(permission_bytes).decode("ascii"),
        "trusted_permission_sha256": hashlib.sha256(permission_bytes).hexdigest(),
        "vocabulary": vocabulary_identity,
        "coverage": {**coverage, "rare_rule_version": "train-action-type-frequency-lte-1-v1", "rare_anchor": rare_anchor},
        "qualification_time_utc": qualification_time_utc, "selection_rule": _GATE_SELECTION_RULE_V3,
        "selection": selected, "rejections": rejected, "split": split.to_dict(),
        "target_contract": "complete-legal-action-autoregressive-semantic-plus-stop-v1",
    }
    payload["manifest_sha256"] = _hash(_gate_input_core(payload))
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_canonical(payload))
    return destination


def _read_gate_input_v3(path: str | Path) -> dict[str, object]:
    from mage_ptcg.meta_specialist.bc_trainer_v3 import SplitManifestV3
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"schema", "lane", "root", "snapshot_index_sha256", "dataset_snapshot_sha256", "teacher_manifest_sha256", "trusted_permission_bytes_b64", "trusted_permission_sha256", "vocabulary", "coverage", "qualification_time_utc", "selection_rule", "selection", "rejections", "split", "target_contract", "manifest_sha256"}
    if type(payload) is not dict or set(payload) != required or payload["schema"] != "meta-specialist-gate1-input-v1" or _hash(_gate_input_core(payload)) != payload["manifest_sha256"]:
        raise ValueError("Gate 1 input manifest has invalid schema or self hash")
    if payload["selection_rule"] != _GATE_SELECTION_RULE_V3 or type(payload["selection"]) is not list or len(payload["selection"]) != 32:
        raise ValueError("Gate 1 input must pin exactly 32 selected shard lines")
    if payload["target_contract"] != "complete-legal-action-autoregressive-semantic-plus-stop-v1":
        raise ValueError("Gate 1 input has an incompatible target contract")
    if payload["vocabulary"] != _production_vocabulary_identity_v3():
        raise ValueError("Gate 1 input production vocabulary identity changed")
    if type(payload["coverage"]) is not dict or set(payload["coverage"]) != _GATE_COVERAGE_KEYS_V3:
        raise ValueError("Gate 1 input coverage is invalid")
    for key in _GATE_COVERAGE_KEYS_V3 - {"rare_rule_version", "rare_anchor"}:
        if type(payload["coverage"][key]) is not int or payload["coverage"][key] < 0:
            raise ValueError("Gate 1 input coverage counter is invalid")
    SplitManifestV3.read_json  # retain public reader as the one schema authority below
    split = payload["split"]
    if type(split) is not dict:
        raise ValueError("Gate 1 input split is invalid")
    # Reuse the strict reader without trusting a caller file path.
    required_split = {"schema", "source_dataset_sha256", "ubiquitous_keys", "ubiquitous_metadata", "assignments", "counts", "overlap_counters", "manifest_sha256"}
    if set(split) != required_split or _hash({key: split[key] for key in required_split - {"manifest_sha256"}}) != split["manifest_sha256"]:
        raise ValueError("Gate 1 embedded split self hash is invalid")
    return payload


class CurrentR2GateAdapterV3:
    """The Gate 1 current arm: the shipped R2 policy over its real step API."""

    def __init__(self, *, card_vocabulary_size: int, seed: int) -> None:
        rng_state = torch.random.get_rng_state()
        try:
            torch.manual_seed(seed)
            self.model = SpecialistPolicyModelV1(SpecialistModelConfigV1(
                card_vocabulary_size=card_vocabulary_size, hidden_dim=64, card_dim=32,
                symbol_dim=16, representation_version=2,
            ))
        finally:
            torch.random.set_rng_state(rng_state)

    def logits(self, model_input: object, step_input: object) -> tuple[float, ...]:
        # The v1 model validates the exact SpecialistModelInputV1 / StepInputV1
        # contract itself.  Keeping this adapter thin prevents Gate 1 from
        # accidentally evaluating a mean-pool surrogate under the current label.
        semantic, _stop = self.model.step_logits(model_input, step_input)
        return tuple(float(value) for value in semantic.detach().cpu())


class _R2NegativeControl(nn.Module):
    """Mean-pool control: no zone/host/owner relation survives pooling."""

    def __init__(self, *, card_vocabulary_size: int, hidden_dim: int, seed: int) -> None:
        super().__init__()
        state = torch.random.get_rng_state()
        try:
            torch.manual_seed(seed)
            self.card = nn.Embedding(card_vocabulary_size + 1, hidden_dim, padding_idx=0)
            self.projection = nn.Sequential(nn.Linear(hidden_dim, hidden_dim), nn.LayerNorm(hidden_dim), nn.GELU())
        finally:
            torch.random.set_rng_state(state)

    def encode_state_v3(self, state: RelationalStateV3):
        tokens = [self.card(torch.tensor(entity.card_id, dtype=torch.long)) for entity in state.entities]
        pooled = torch.stack(tokens).mean(0) if tokens else torch.zeros(self.card.embedding_dim)
        return type("Encoding", (), {"global_token": self.projection(pooled)})()


def _examples(seed: int, samples: int) -> list[_Example]:
    generator = torch.Generator().manual_seed(seed)
    result = []
    for index in range(samples):
        card = 10 + int(torch.randint(0, 5, (), generator=generator).item())
        label = card - 10
        entities = (
            EntityTokenV3(1, 1, 1, 1, card, None, (float(label), 0.0), (label,), (0,)),
            EntityTokenV3(2, 1, 2, 1, 30 + (index % 3), None, (0.0, 1.0), (2,), (0,)),
            EntityTokenV3(3, 1, 1, 2, 40 + (index % 4), None, (0.0, 0.0), (3,), (0,)),
        )
        candidates = tuple(
            ActionCandidateV3(f"candidate-{candidate}", candidate, 1, None, (candidate,), (0.0,), 0)
            for candidate in range(5)
        )
        result.append(_Example(RelationalStateV3((float(index % 3),) + (0.0,) * 40, entities, candidates), label))
    return result


def _encode(encoder: nn.Module, examples: list[_Example]) -> tuple[torch.Tensor, torch.Tensor, list[float]]:
    features, labels, timings = [], [], []
    encoder.eval()
    with torch.no_grad():
        for example in examples:
            start = time.perf_counter()
            features.append(encoder.encode_state_v3(example.state).global_token)
            timings.append((time.perf_counter() - start) * 1000.0)
            labels.append(example.label)
    return torch.stack(features), torch.tensor(labels), timings


def _train_encoder(encoder: nn.Module, train: list[_Example], valid: list[_Example], *, seed: int, epochs: int) -> tuple[dict[str, float], list[float]]:
    state = torch.random.get_rng_state()
    try:
        torch.manual_seed(seed)
        first = encoder.encode_state_v3(train[0].state).global_token
        class_count = max(example.label for example in (*train, *valid)) + 1
        head = nn.Linear(first.shape[-1], class_count)
        optimizer = torch.optim.Adam((*encoder.parameters(), *head.parameters()), lr=0.01)
        for _ in range(epochs):
            optimizer.zero_grad()
            train_x = torch.stack([encoder.encode_state_v3(example.state).global_token for example in train])
            train_y = torch.tensor([example.label for example in train])
            loss = F.cross_entropy(head(train_x), train_y)
            loss.backward()
            optimizer.step()
        with torch.no_grad():
            valid_x, valid_y, timings = _encode(encoder, valid)
            logits = head(valid_x)
            probabilities = logits.softmax(-1)
            top1 = (logits.argmax(-1) == valid_y).float().mean().item()
            top3 = (logits.topk(min(3, logits.shape[-1]), -1).indices == valid_y[:, None]).any(-1).float().mean().item()
        return {"nll": float(F.cross_entropy(logits, valid_y).item()), "top1": float(top1), "top3": float(top3)}, timings
    finally:
        torch.random.set_rng_state(state)


def run_representation_benchmark_v3(*, seed: int = 0, samples: int = 128, epochs: int = 5) -> dict[str, object]:
    if type(samples) is not int or samples < 10 or type(epochs) is not int or epochs < 1:
        raise ValueError("samples must be >=10 and epochs must be positive ints")
    examples = _examples(seed, samples)
    split = max(2, int(len(examples) * 0.8))
    train, valid = examples[:split], examples[split:]
    encoders = {
        "R2-negative-control": _R2NegativeControl(card_vocabulary_size=64, hidden_dim=32, seed=seed),
        "R3-A": ZoneDeepSetsEncoderV3(card_vocabulary_size=64, hidden_dim=32, embedding_dim=16, seed=seed),
        "R3-B": RelationAwareEncoderV3(card_vocabulary_size=64, hidden_dim=32, embedding_dim=16, seed=seed),
    }
    report: dict[str, object] = {"schema": "representation-benchmark-v3", "seed": seed, "samples": samples, "candidates": list(encoders), "metrics": {}}
    metrics = report["metrics"]
    assert isinstance(metrics, dict)
    for name, encoder in encoders.items():
        values, timings = _train_encoder(encoder, train, valid, seed=seed + 1, epochs=epochs)
        timings.sort()
        p50 = timings[len(timings) // 2]
        p95 = timings[min(len(timings) - 1, int(len(timings) * 0.95))]
        metrics[name] = {**values, "p50_ms": float(p50), "p95_ms": float(p95), "rare_action_recall": float(values["top1"])}
    return report


def load_teacher_examples_v3(root: str | Path, *, limit: int = 512) -> list[_Example]:
    """Load a bounded actor-visible teacher slice without leaking private IDs.

    The loader revalidates each local record through the existing v2 parser,
    then projects the canonical model input through the v3 adapter.  Labels are
    action-type targets from the committed semantic selection; source/episode
    identities remain split metadata and are not model features.
    """
    raise RuntimeError("legacy first-action teacher loader is retired; use the sealed Gate-1 input manifest")
    if type(limit) is not int or limit < 10:
        raise ValueError("limit must be at least 10")
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
    from mage_ptcg.meta_specialist.local_dataset_v2 import validate_local_record_v2
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import specialist_model_input_from_training_payload_v2
    root_path = Path(root)
    files = sorted(root_path.glob("dataset-*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no dataset-*.jsonl files under {root_path}")
    vocabulary = make_test_card_vocabulary_v1(range(1, 4096))
    examples: list[_Example] = []
    for path in files:
        for line in path.open(encoding="utf-8"):
            if len(examples) >= limit:
                return examples
            record = json.loads(line)
            try:
                model_payload, _meta = validate_local_record_v2(record, vocabulary=vocabulary)
                model_input = specialist_model_input_from_training_payload_v2(model_payload)
                selected = tuple(record.get("selection", ()))
                actions = {row["local_action_id"]: row for row in record["legal_actions"]}
                if not selected or selected[0] not in actions:
                    continue
                label = int(actions[selected[0]]["semantic_action"]["option_type"])
                examples.append(_Example(representation_v3_from_model_input_v1(model_input), label))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    return examples


def run_teacher_representation_benchmark_v3(root: str | Path, *, seed: int = 0, limit: int = 512, epochs: int = 3) -> dict[str, object]:
    """Run the Gate 1 metric format on a real teacher-record slice."""
    raise RuntimeError("legacy first-action teacher benchmark is retired; use run_gate1_v3")
    examples = load_teacher_examples_v3(root, limit=limit)
    if len(examples) < 10:
        raise ValueError(f"teacher slice contains only {len(examples)} usable examples")
    split = max(2, int(len(examples) * 0.8))
    train, valid = examples[:split], examples[split:]
    max_card = max((entity.card_id for item in examples for entity in item.state.entities), default=1)
    encoders = {
        "R2-negative-control": _R2NegativeControl(card_vocabulary_size=max_card + 1, hidden_dim=64, seed=seed),
        "R3-A": ZoneDeepSetsEncoderV3(card_vocabulary_size=max_card + 1, hidden_dim=64, embedding_dim=32, seed=seed),
        "R3-B": RelationAwareEncoderV3(card_vocabulary_size=max_card + 1, hidden_dim=64, embedding_dim=32, seed=seed),
    }
    metrics: dict[str, dict[str, float]] = {}
    for name, encoder in encoders.items():
        values, timings = _train_encoder(encoder, train, valid, seed=seed + 1, epochs=epochs)
        timings.sort()
        values.update({"p50_ms": float(timings[len(timings) // 2]), "p95_ms": float(timings[min(len(timings) - 1, int(len(timings) * 0.95))])})
        metrics[name] = values
    return {"schema": "representation-benchmark-v3", "source": str(root), "seed": seed, "samples": len(examples), "candidates": list(encoders), "metrics": metrics}


def _read_split_manifest_sha256_v3(path: str | Path) -> str:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    value = payload.get("manifest_sha256") if isinstance(payload, dict) else None
    if type(value) is not str or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{path}: split manifest needs a lowercase 64-hex manifest_sha256")
    return value


def _gate_steps_from_input_v3(payload: Mapping[str, object]) -> tuple[_GateStepV3, ...]:
    """Re-read every pinned byte and materialize every semantic and STOP step."""
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        ExtractedSpecialistModelInputV1, build_specialist_step_input_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_trusted_permission_set_v1, require_qualified_training_record_v2, semantic_loss_rows_from_record_v2,
    )
    root = Path(payload["root"])
    if _file_hash(root / "snapshot_index.json") != payload["snapshot_index_sha256"] or _file_hash(root / "teacher_dataset_manifest.json") != payload["teacher_manifest_sha256"]:
        raise ValueError("pinned Gate input source bytes changed")
    snapshot = json.loads((root / "snapshot_index.json").read_text(encoding="utf-8"))
    if type(snapshot) is not dict or snapshot.get("schema_version") != "specialist-training-snapshot-index-v1" or snapshot.get("dataset_snapshot_sha256") != payload["dataset_snapshot_sha256"] or type(snapshot.get("duplicate_cap")) is not dict:
        raise ValueError("pinned snapshot index schema/dataset identity changed")
    validate_gate_snapshot_v3(root, snapshot)
    permission_bytes = base64.b64decode(payload["trusted_permission_bytes_b64"], validate=True)
    if hashlib.sha256(permission_bytes).hexdigest() != payload["trusted_permission_sha256"]:
        raise ValueError("pinned permission bytes hash changed")
    from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
    permission = json.loads(permission_bytes)
    trusted = build_trusted_permission_set_v1((permission_bytes,))
    vocabulary = _load_production_vocabulary_v3()
    if payload["vocabulary"] != _production_vocabulary_identity_v3():
        raise ValueError("pinned Gate production vocabulary identity changed")
    assignments = {row["record_id"]: row for row in payload["split"]["assignments"]}
    selected_records: list[dict[str, object]] = []
    steps: list[_GateStepV3] = []
    seen_shards: set[str] = set()
    seen_episodes: set[str] = set()
    for entry in payload["selection"]:
        if type(entry) is not dict or set(entry) != {"shard", "line", "record_id", "content_hash", "raw_line_sha256"} or type(entry["line"]) is not int or entry["line"] < 1:
            raise ValueError("Gate selection entry is malformed")
        path = _gate_shard_path_v3(root, entry["shard"])
        if entry["shard"] in seen_shards:
            raise ValueError("Gate selection pins more than one record from a shard")
        with path.open(encoding="utf-8") as handle:
            raw = ""
            for _ in range(entry["line"]): raw = handle.readline()
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != entry["raw_line_sha256"]:
            raise ValueError("pinned Gate raw line bytes changed")
        record = json.loads(raw)
        if record.get("record_id") != entry["record_id"] or record.get("content_hash") != entry["content_hash"]:
            raise ValueError("pinned Gate record identity/content hash mismatched")
        if record.get("episode_id_hash") in seen_episodes:
            raise ValueError("Gate selection reuses an episode")
        model_payload, _labels = require_qualified_training_record_v2(record, vocabulary=vocabulary, trusted_permissions=trusted, qualification_time_utc=payload["qualification_time_utc"])
        if record["source"]["artifact_sha256"] != permission["artifact_sha256"] or record["source"]["permission_manifest_id"] != permission["permission_manifest_id"]:
            raise ValueError("pinned Gate record authority mismatched")
        assignment = assignments.get(record["record_id"])
        if assignment is None:
            raise ValueError("pinned Gate record has no split assignment")
        selected_records.append(record)
        seen_shards.add(entry["shard"])
        seen_episodes.add(record["episode_id_hash"])
        from mage_ptcg.meta_specialist.training_example_envelope_v2 import specialist_model_input_from_training_payload_v2
        model_input = specialist_model_input_from_training_payload_v2(model_payload)
        by_semantic: dict[bytes, list[int]] = {}
        for index, semantic in enumerate(model_input.candidate_rows):
            by_semantic.setdefault(_canonical(semantic.to_dict()), []).append(index)
        offsets: dict[bytes, int] = {}
        local_to_index: dict[str, int] = {}
        for action in sorted(record["legal_actions"], key=lambda row: row["local_action_id"]):
            key = _canonical(action["semantic_action"]); offset = offsets.get(key, 0)
            if key not in by_semantic or offset >= len(by_semantic[key]):
                raise ValueError("pinned Gate legal action/model input mismatch")
            local_to_index[action["local_action_id"]] = by_semantic[key][offset]; offsets[key] = offset + 1
        extracted = ExtractedSpecialistModelInputV1(model_input, record["model_input_id"], local_to_index)
        locals_by_semantic = {key: sorted(local for local, index in local_to_index.items() if _canonical(model_input.candidate_rows[index].to_dict()) == key) for key in by_semantic}
        # Canonical loss rows aggregate physical aliases and already exclude a
        # forced sole STOP.  They are the one complete-action supervision source
        # used by both R2 and R3.
        for loss_row in semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary):
            prefix_counts: dict[bytes, int] = {}
            prefix_ids_list: list[str] = []
            for item in loss_row["semantic_prefix"]:
                key = _canonical(item); offset = prefix_counts.get(key, 0)
                aliases = locals_by_semantic.get(key, [])
                if offset >= len(aliases):
                    raise ValueError("canonical semantic prefix has insufficient distinct local aliases")
                prefix_ids_list.append(aliases[offset]); prefix_counts[key] = offset + 1
            prefix_ids = tuple(prefix_ids_list)
            step_input = build_specialist_step_input_v1(extracted, prefix_ids)
            expected_semantic = [_canonical(item.semantic_row.to_dict()) for item in step_input.allowed_semantic_classes]
            token_map: dict[tuple[str, bytes], float] = {}
            for token in loss_row["token_masses"]:
                key = ("stop", b"") if token["kind"] == "stop" else ("semantic", _canonical(token["semantic_action"]))
                token_map[key] = float(token["mass"])
            expected_keys = [("semantic", key) for key in expected_semantic] + ([("stop", b"")] if step_input.stop_available else [])
            if set(token_map) != set(expected_keys):
                raise ValueError("canonical teacher mass domain disagrees with rebuilt step legality")
            masses = tuple(token_map[key] for key in expected_keys)
            if not math.isclose(math.fsum(masses), 1.0, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("canonical teacher mass does not normalize")
            state = representation_v3_from_step_input_v1(model_input, step_input, allow_unbound_selected=True)
            target_index = max(range(len(masses)), key=lambda index: (masses[index], -index))
            target_type = state.candidates[target_index].action_type if target_index < len(state.candidates) else None
            steps.append(_GateStepV3(
                payload["lane"], record["record_id"], assignment["component_id"],
                assignment["partition"], model_input, step_input, state, target_index,
                masses, target_type, record["episode_id_hash"],
            ))
    from mage_ptcg.meta_specialist.bc_trainer_v3 import build_split_manifest_v3
    authoritative = payload["split"]["ubiquitous_keys"]
    reconstructed = build_split_manifest_v3(selected_records, validation_fraction=0.2, ubiquitous_threshold=payload["split"]["ubiquitous_metadata"]["threshold"], ubiquitous_keys=authoritative, ubiquitous_rule_version=payload["split"]["ubiquitous_metadata"]["rule_version"])
    if reconstructed.manifest_sha256 != payload["split"]["manifest_sha256"] or reconstructed.overlap_counters != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
        raise ValueError("pinned Gate split cannot be reproduced")
    return tuple(steps)


def _distribution(semantic: torch.Tensor, stop: torch.Tensor | None, target: int, masses: tuple[float, ...]) -> tuple[torch.Tensor, torch.Tensor]:
    logits = torch.cat((semantic, stop.reshape(1))) if stop is not None else semantic
    if not 0 <= target < logits.numel():
        raise ValueError("Gate target is outside its exact legal token domain")
    if len(masses) != logits.numel():
        raise ValueError("Gate soft target arity differs from legal token domain")
    return logits, torch.tensor(masses, dtype=logits.dtype, device=logits.device)


def _soft_nll(logits: torch.Tensor, masses: torch.Tensor) -> torch.Tensor:
    return -(masses * F.log_softmax(logits, dim=-1)).sum()


def _coverage_from_steps_v3(steps: tuple[object, ...]) -> dict[str, int] | None:
    """Reconstruct the three original coverage counters from runtime steps.

    ``None`` is reserved for focused orchestration tests that replace the
    private loader with a non-step sentinel.  The production loader returns
    only ``_GateStepV3`` and therefore always takes the fail-closed path.
    """
    if any(not isinstance(step, _GateStepV3) for step in steps):
        return None
    learned_stop = positive_stop = ordered_prefix = validation_positive_stop = prefix_positive_stop = 0
    for step in steps:
        assert isinstance(step, _GateStepV3)
        stop_available = step.step_input.stop_available
        learned_stop += int(stop_available)
        positive_stop += int(stop_available and step.target_masses[-1] > 0.0)
        ordered_prefix += int(bool(step.state.semantic_prefix) and step.state.prefix_order_sensitive)
        validation_positive_stop += int(
            stop_available and step.target_masses[-1] > 0.0 and step.partition == "validation"
        )
        prefix_positive_stop += int(
            stop_available and step.target_masses[-1] > 0.0 and bool(step.state.semantic_prefix)
        )
    return {
        "learned_stop_domain_count": learned_stop,
        "positive_stop_target_count": positive_stop,
        "ordered_nonempty_prefix_count": ordered_prefix,
        "validation_positive_stop_target_count": validation_positive_stop,
        "prefix_conditioned_positive_stop_target_count": prefix_positive_stop,
    }


def _run_candidate_v3(candidate: str, steps: tuple[_GateStepV3, ...], *, seed: int, max_epochs: int, patience: int, min_delta: float, device: torch.device) -> dict[str, object]:
    """Equal-budget full-batch token trainer for one candidate/seed/lane."""
    if not steps or max_epochs < 1:
        raise ValueError("Gate candidate needs nonempty steps and a positive epoch budget")
    torch.manual_seed(seed)
    vocabulary = _load_production_vocabulary_v3()
    card_vocabulary_size = max(vocabulary.recognized_card_ids)
    if device.type == "cuda":
        with torch.cuda.device(device):
            torch.cuda.synchronize(device)
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(device)
    with torch.device(device):
        if candidate == "current-R2":
            model: nn.Module = SpecialistPolicyModelV1(SpecialistModelConfigV1(card_vocabulary_size=card_vocabulary_size, representation_version=2))
        else:
            model = SpecialistModelV3(card_vocabulary_size=card_vocabulary_size, seed=seed, encoder_kind="zone-deepsets" if candidate == "R3-A" else "relation-attention")
    model.to(device)
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    initial = {name: value.detach().clone() for name, value in model.state_dict().items()}
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    train = tuple(step for step in steps if step.partition == "train")
    valid = tuple(step for step in steps if step.partition == "validation")
    if not train or not valid:
        raise ValueError("Gate split produced an empty token partition")
    train_type_frequency: dict[int, int] = {}
    for step in train:
        if step.target_action_type is not None:
            train_type_frequency[step.target_action_type] = train_type_frequency.get(step.target_action_type, 0) + 1
    # A validation-only semantic type has train frequency zero and is exactly
    # the rare-coverage anchor the Gate must expose; STOP is excluded because
    # this metric is action-type recall, not termination recall.
    validation_semantic_types = {step.target_action_type for step in valid if step.target_action_type is not None}
    rare_types = {kind for kind in validation_semantic_types if train_type_frequency.get(kind, 0) <= 1}
    best, best_state, stale, updates, best_epoch, history = float("inf"), None, 0, 0, -1, []
    for epoch in range(max_epochs):
        model.train(); optimizer.zero_grad(); losses = []
        for step in train:
            with torch.device(device):
                semantic, stop = model.step_logits(step.model_input, step.step_input) if candidate == "current-R2" else model.step_logits_v3(step.state, stop_available=step.step_input.stop_available)  # type: ignore[union-attr]
            logits, masses = _distribution(semantic, stop, step.target_index, step.target_masses); losses.append(_soft_nll(logits, masses))
        torch.stack(losses).mean().backward(); optimizer.step(); updates += 1
        model.eval()
        with torch.no_grad():
            values = []
            for step in valid:
                with torch.device(device):
                    semantic, stop = model.step_logits(step.model_input, step.step_input) if candidate == "current-R2" else model.step_logits_v3(step.state, stop_available=step.step_input.stop_available)  # type: ignore[union-attr]
                logits, masses = _distribution(semantic, stop, step.target_index, step.target_masses); values.append(_soft_nll(logits, masses))
            nll = float(torch.stack(values).mean())
        history.append(nll)
        if nll < best - min_delta:
            best, best_state, stale, best_epoch = nll, {name: value.detach().cpu().clone() for name, value in model.state_dict().items()}, 0, epoch
        else:
            stale += 1
            if stale >= patience:
                break
    assert best_state is not None
    model.load_state_dict(best_state); model.eval()
    stop_reason = "patience" if stale >= patience else "max_epochs"
    top1 = top3 = rare_hits = rare_total = 0; timings = []; record_losses: dict[str, list[float]] = {}; action_rows: dict[int | str, list[tuple[float, float]]] = {}
    with torch.no_grad():
        for step in valid:
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            start = time.perf_counter()
            with torch.device(device):
                semantic, stop = model.step_logits(step.model_input, step.step_input) if candidate == "current-R2" else model.step_logits_v3(step.state, stop_available=step.step_input.stop_available)  # type: ignore[union-attr]
            if device.type == "cuda":
                torch.cuda.synchronize(device)
            timings.append((time.perf_counter() - start) * 1000)
            logits, masses = _distribution(semantic, stop, step.target_index, step.target_masses); loss = float(_soft_nll(logits, masses))
            record_losses.setdefault(step.record_id, []).append(loss)
            top1 += int(int(logits.argmax()) == step.target_index); top3 += int(step.target_index in logits.topk(min(3, logits.numel())).indices.tolist())
            token_nll = -masses * F.log_softmax(logits, dim=-1)
            for index, contribution in enumerate(token_nll.tolist()):
                action_key: int | str = step.state.candidates[index].action_type if index < len(step.state.candidates) else "STOP"
                if float(masses[index]) > 0:
                    action_rows.setdefault(action_key, []).append((float(masses[index]), float(contribution)))
            if step.target_action_type is not None:
                if step.target_action_type in rare_types:
                    rare_total += 1
                    rare_hits += int(step.target_index in logits.topk(min(3, logits.numel())).indices.tolist())
    timings.sort(); delta = sum(float((value - initial[name].cpu()).abs().sum()) for name, value in best_state.items())
    if device.type == "cuda":
        torch.cuda.synchronize(device)
        cuda_vram = {"measured": True, "peak_allocated_bytes": torch.cuda.max_memory_allocated(device), "peak_reserved_bytes": torch.cuda.max_memory_reserved(device), "device_name": torch.cuda.get_device_name(device), "runtime": torch.version.cuda}
    else:
        cuda_vram = {"measured": False, "peak_allocated_bytes": None, "peak_reserved_bytes": None, "blocker": "CPU execution requested"}
    breakdown = {str(kind): {"count": len(values), "target_mass": sum(mass for mass, _ in values), "nll_contribution": sum(loss for _, loss in values), "normalized_nll": sum(loss for _, loss in values) / sum(mass for mass, _ in values)} for kind, values in sorted(action_rows.items(), key=lambda row: str(row[0]))}
    overall = sum(row["nll_contribution"] for row in breakdown.values()) / len(valid) if breakdown else None
    macro = sum(row["normalized_nll"] for row in breakdown.values()) / len(breakdown) if breakdown else None
    rare = {"rule_version": "train-action-type-frequency-lte-1-v1", "eligible": rare_total, "value": rare_hits / rare_total if rare_total else None, "status": "measured" if rare_total else "no_eligible_targets"}
    return {"best_validation_token_nll": best, "validation_complete_action_nll": sum(sum(values) for values in record_losses.values()) / len(record_losses), "top1": top1 / len(valid), "top3": top3 / len(valid), "topk_soft_target_tie_rule": "lowest-token-index-among-max-mass", "rare_action_recall": rare, "action_type_nll": {"by_type": breakdown, "macro": macro, "overall": overall}, "p50_ms": timings[len(timings)//2], "p95_ms": timings[min(len(timings)-1, int(len(timings)*.95))], "cpu_preprocessing_ms": None, "cuda_vram": cuda_vram, "epochs": epoch + 1, "updates": updates, "best_epoch": best_epoch, "stale_epochs": stale, "stop_reason": stop_reason, "history": history, "parameter_delta_l1": delta, "parameter_count": sum(value.numel() for value in model.parameters()), "checkpoint_sha256": hashlib.sha256(b"".join(value.numpy().tobytes() for _, value in sorted(best_state.items()))).hexdigest(), "record_ids": sorted(record_losses), "step_count": len(valid)}


def run_gate1_v3(
    *, lane_roots: Mapping[str, str | Path], split_manifest_paths: Mapping[str, str | Path],
    seeds: tuple[int, ...] = (7, 17, 29), patience: int = 3, min_delta: float = 1e-4,
    output_dir: str | Path, dry_run: bool = False, max_epochs: int = 8, device: str = "cpu",
) -> Gate1ResultV3:
    """Validate sealed real inputs and run the equal-budget two-lane Gate matrix."""
    if not lane_roots or set(lane_roots) != set(split_manifest_paths):
        raise ValueError("lane roots and split manifests must have identical nonempty lane keys")
    if type(seeds) is not tuple or len(seeds) != 3 or any(type(seed) is not int for seed in seeds) or len(set(seeds)) != 3:
        raise ValueError("Gate 1 requires exactly three distinct integer seeds")
    if type(patience) is not int or patience < 1 or type(min_delta) not in {int, float} or min_delta < 0:
        raise ValueError("patience/min_delta are invalid")
    try:
        requested_device = torch.device(device)
    except (RuntimeError, TypeError) as exc:
        raise ValueError("Gate device must be cpu or a CUDA device") from exc
    if requested_device.type not in {"cpu", "cuda"}:
        raise ValueError("Gate device must be cpu or a CUDA device")
    if requested_device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("requested CUDA Gate device is unavailable")
    inputs = {lane: _read_gate_input_v3(path) for lane, path in sorted(split_manifest_paths.items())}
    if any(inputs[lane]["lane"] != lane or Path(inputs[lane]["root"]) != Path(lane_roots[lane]).resolve() for lane in inputs):
        raise ValueError("Gate lane roots do not match their content-addressed input manifests")
    candidates = (
        ("current-R2", "SpecialistPolicyModelV1", 2),
        ("R3-A", "ZoneDeepSetsEncoderV3", 3),
        ("R3-B", "RelationAwareEncoderV3", 3),
    )
    runs: list[Mapping[str, object]] = []
    for lane, root in sorted(lane_roots.items()):
        input_manifest = inputs[lane]
        split_hash = input_manifest["split"]["manifest_sha256"]
        load_start = time.perf_counter()
        loaded_steps = () if dry_run else _gate_steps_from_input_v3(input_manifest)
        preprocessing_ms = (time.perf_counter() - load_start) * 1000.0
        reconstructed_coverage = None if dry_run else _coverage_from_steps_v3(loaded_steps)
        if reconstructed_coverage is not None:
            declared_coverage = input_manifest["coverage"]
            if any(declared_coverage.get(key) != value for key, value in reconstructed_coverage.items()):
                raise ValueError(f"{lane}: pinned coverage disagrees with reconstructed STOP/prefix steps")
        for candidate, adapter, representation_version in candidates:
            for seed in seeds:
                row: dict[str, object] = {
                    "lane": lane, "candidate": candidate, "adapter": adapter,
                    "representation_version": representation_version, "seed": seed,
                    "split_manifest_sha256": split_hash, "input_manifest_sha256": input_manifest["manifest_sha256"],
                    "budget": {"max_epochs": max_epochs, "patience": patience, "min_delta": float(min_delta)},
                    "target": input_manifest["target_contract"], "status": "planned" if dry_run else "measured",
                    "coverage": input_manifest["coverage"],
                }
                if not dry_run:
                    row["metrics"] = _run_candidate_v3(candidate, loaded_steps, seed=seed, max_epochs=max_epochs, patience=patience, min_delta=min_delta, device=requested_device)
                    row["metrics"]["cpu_preprocessing_ms"] = preprocessing_ms
                runs.append(row)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Current-R2 is the already-approved baseline.  Until every R3 promotion
    # condition is pinned and measured, retain it explicitly rather than
    # emitting a diagnostic ``preferred`` value that a downstream consumer can
    # accidentally promote.
    status = "BASELINE_RETAINED"
    blockers = ["v2_major_regression_threshold_unspecified"]
    if requested_device.type == "cpu": blockers.append("cuda_measurement_unavailable")
    for lane, payload in inputs.items():
        coverage = payload["coverage"]
        assert isinstance(coverage, dict)
        if coverage["positive_stop_target_count"] == 0: blockers.append(f"{lane}_positive_learned_stop_coverage_unavailable")
        if coverage["validation_positive_stop_target_count"] == 0: blockers.append(f"{lane}_validation_positive_learned_stop_coverage_unavailable")
        if coverage["ordered_nonempty_prefix_count"] == 0: blockers.append(f"{lane}_ordered_target_coverage_unavailable")
    if not dry_run:
        for lane in _GATE_LANES_V3:
            lane_rows = [row for row in runs if row["lane"] == lane]
            if lane_rows and all(row["metrics"]["rare_action_recall"]["eligible"] == 0 for row in lane_rows):
                blockers.append(f"{lane}_rare_action_coverage_unavailable")
    selection = {
        "decision_status": "BASELINE_RETAINED_R3_UNAPPROVED",
        "preferred": "current-R2",
        "blockers": blockers,
        "rule": "retain current-R2; R3 is unapproved until the v2 major-regression threshold, required coverage, and device measurements are all satisfied",
    }
    result_payload = {"schema": "meta-specialist-gate1-v3", "status": status, "execution_device": str(requested_device), "seeds": list(seeds), "runs": runs, "selection": selection}
    result_payload["result_sha256"] = _hash(result_payload)
    output_path = output / f"gate1-result-v3-{str(requested_device).replace(':', '-')}.json"
    _atomic_write_gate_result_v3(output_path, result_payload)
    decision_path = output / f"gate1-selection-v3-{str(requested_device).replace(':', '-')}.json"
    decision_payload = {
        "schema": "meta-specialist-gate1-selection-v1",
        "decision_status": selection["decision_status"],
        "active_representation": "current-R2",
        "r3_promotion_status": "UNAPPROVED",
        "blockers": blockers,
        "rule": selection["rule"],
        "gate_result_path": output_path.name,
        "gate_result_file_sha256": _file_hash(output_path),
        "gate_result_sha256": result_payload["result_sha256"],
    }
    decision_payload["decision_sha256"] = _hash(decision_payload)
    _atomic_write_gate_selection_manifest_v3(decision_path, decision_payload)
    return Gate1ResultV3(
        status=status, seeds=seeds, runs=tuple(runs), output_path=output_path,
        decision_path=decision_path,
    )


__all__ = ["CurrentR2GateAdapterV3", "Gate1ResultV3", "build_gate1_input_manifest_v3", "read_gate_result_v3", "read_gate_selection_manifest_v3", "run_gate1_v3", "run_representation_benchmark_v3", "validate_gate_snapshot_v3", "verify_gate_result_anchor_v3"]
