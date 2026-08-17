"""Atomic, externally anchored θ0 checkpoint sealing for the recurrent R3 Gate.

This module creates a *training initialization* artifact only.  It never
selects a recurrent candidate and refuses every Gate artifact that does not
already carry CUDA-backed ``promotion_authority``.  A manifest self-hash is an
integrity check, not a provenance authority: callers must pin the recurrent
selection and teacher-quality files out of band before calling the sealer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import torch

from mage_ptcg.meta_specialist.recurrent_gate_v3 import _state_sha256_v3, verify_recurrent_gate_anchor_v3


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LANES = frozenset({"alakazam", "archaludon"})
_AUTHORITY_KEYS = frozenset({"source", "data", "split", "model", "config", "command", "seed", "metric", "result"})
_SCHEMA = "meta-specialist-theta0-v3"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> object:
    raise ValueError(f"non-finite JSON value {value}")


def _strict_canonical_object(path: Path, *, name: str) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is unreadable strict JSON") from exc
    if type(payload) is not dict:
        raise ValueError(f"{name} must be a JSON object")
    if _canonical(payload) != raw:
        raise ValueError(f"{name} is not canonical JSON")
    return payload


def _object_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError("θ0 authority path must be a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _safe_basename(value: object, *, field: str, suffix: str) -> str:
    if type(value) is not str or Path(value).name != value or not value.endswith(suffix):
        raise ValueError(f"{field} must be a contained {suffix} basename")
    return value


def _contained(path: str | Path, *, root: Path, field: str) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{field} is outside the explicit source allowlist") from exc
    if not resolved.is_file():
        raise ValueError(f"{field} must be a regular file")
    return resolved


def _tensor_bytes(tensor: torch.Tensor) -> bytes:
    cpu = tensor.detach().cpu().contiguous()
    return cpu.view(torch.uint8).numpy().tobytes()


def _canonical_state(state: Mapping[str, torch.Tensor]) -> tuple[dict[str, torch.Tensor], dict[str, dict[str, object]], str]:
    if not isinstance(state, Mapping) or not state:
        raise ValueError("θ0 checkpoint state must be a nonempty tensor mapping")
    normalized: dict[str, torch.Tensor] = {}
    metadata: dict[str, dict[str, object]] = {}
    for key, tensor in state.items():
        if type(key) is not str or not key or key in normalized or not isinstance(tensor, torch.Tensor):
            raise ValueError("θ0 checkpoint state has duplicate/invalid tensor keys")
        if tensor.layout != torch.strided or tensor.device.type == "meta":
            raise ValueError("θ0 checkpoint tensor layout/device is invalid")
        if (tensor.is_floating_point() or tensor.is_complex()) and not bool(torch.isfinite(tensor).all().item()):
            raise ValueError("θ0 checkpoint tensors must be finite")
        copied = tensor.detach().cpu().contiguous().clone()
        raw = _tensor_bytes(copied)
        normalized[key] = copied
        metadata[key] = {
            "dtype": str(copied.dtype), "shape": list(copied.shape),
            "bytes_sha256": hashlib.sha256(raw).hexdigest(), "nbytes": len(raw),
        }
    core = [{"key": key, **metadata[key]} for key in sorted(metadata)]
    return normalized, {key: metadata[key] for key in sorted(metadata)}, _object_sha256(core)


def _read_ready_teacher_quality(
    path: str | Path, *, expected_file_sha256: str, expected_manifest_sha256: str,
    approved_rule_path: str | Path, expected_approved_rule_file_sha256: str,
    primary_evidence_paths: Mapping[str, str | Path],
    expected_primary_evidence_file_sha256: Mapping[str, str],
) -> dict[str, object]:
    """Use the sole public teacher-quality READY authority; never parse a local variant."""
    from mage_ptcg.meta_specialist.teacher_quality_v3 import read_ready_teacher_quality_manifest_v3

    return read_ready_teacher_quality_manifest_v3(
        path, expected_manifest_file_sha256=expected_file_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
        approved_rule_path=approved_rule_path,
        expected_approved_rule_file_sha256=expected_approved_rule_file_sha256,
        primary_evidence_paths=primary_evidence_paths,
        expected_primary_evidence_file_sha256=expected_primary_evidence_file_sha256,
    )


def _selected_checkpoint_binding(
    gate: Mapping[str, object], *, selection_path: Path, checkpoint_state: Mapping[str, torch.Tensor],
    lane: str, seed: int,
) -> tuple[str, Path, str, str]:
    """Bind the requested θ0 exactly to the selected candidate/lane/seed cell.

    The Gate v2 descriptor is a relative, result-root-contained artifact pin.
    It is checked independently of the caller-supplied in-memory state so an
    unrelated model cannot be relabelled as the selected best checkpoint.
    """
    selection = gate.get("selection")
    cells = gate.get("cells")
    if type(selection) is not dict or type(cells) is not list:
        raise ValueError("recurrent Gate selection/cells are invalid")
    candidate = selection.get("preferred")
    if candidate not in {"R3-A", "R3-B"} or lane not in _LANES or seed not in {7, 17, 29}:
        raise ValueError("θ0 candidate/lane/seed does not identify a selected Gate cell")
    matches = [
        cell for cell in cells if type(cell) is dict
        and (cell.get("candidate"), cell.get("lane"), cell.get("seed")) == (candidate, lane, seed)
    ]
    if len(matches) != 1:
        raise ValueError("θ0 candidate/lane/seed does not bind exactly one selected Gate cell")
    cell = matches[0]
    descriptor = cell.get("checkpoint")
    required = {"basename", "path", "file_sha256", "state_sha256", "candidate", "lane", "seed"}
    if type(descriptor) is not dict or set(descriptor) != required:
        raise ValueError("recurrent Gate schema gap: selected cell lacks a closed best checkpoint artifact binding")
    if (descriptor["candidate"], descriptor["lane"], descriptor["seed"]) != (candidate, lane, seed):
        raise ValueError("selected best checkpoint candidate/lane/seed disagrees with its Gate cell")
    checkpoint_name = _safe_basename(descriptor["basename"], field="checkpoint.basename", suffix=".pt")
    relative_path = Path(str(descriptor["path"]))
    if (type(descriptor["path"]) is not str or relative_path.is_absolute() or ".." in relative_path.parts
            or relative_path.name != checkpoint_name):
        raise ValueError("selected best checkpoint path escapes the recurrent Gate result root")
    checkpoint_file_sha = _digest(descriptor["file_sha256"], field="best checkpoint file SHA-256")
    checkpoint_state_sha = _digest(descriptor["state_sha256"], field="best checkpoint state SHA-256")
    if cell.get("checkpoint_sha256") != checkpoint_state_sha:
        raise ValueError("selected Gate cell checkpoint state hashes disagree")
    checkpoint_path = (selection_path.parent / relative_path).resolve()
    try:
        checkpoint_path.relative_to(selection_path.parent.resolve())
    except ValueError as exc:
        raise ValueError("selected best checkpoint path escapes the recurrent Gate result root") from exc
    if _file_sha256(checkpoint_path) != checkpoint_file_sha:
        raise ValueError("selected best checkpoint artifact file SHA-256 does not match Gate cell")
    try:
        payload = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("selected best checkpoint artifact cannot be loaded") from exc
    if type(payload) is not dict or not payload or any(type(key) is not str or not isinstance(value, torch.Tensor) for key, value in payload.items()):
        raise ValueError("selected best checkpoint artifact has an invalid closed payload")
    loaded_state = payload
    if _state_sha256_v3(loaded_state) != checkpoint_state_sha:
        raise ValueError("selected best checkpoint artifact state SHA-256 does not match Gate cell")
    supplied_state_sha = _state_sha256_v3(checkpoint_state)
    if supplied_state_sha != checkpoint_state_sha:
        raise ValueError("supplied θ0 tensor state is unrelated to the selected best checkpoint")
    return str(candidate), checkpoint_path, checkpoint_file_sha, checkpoint_state_sha


def _atomic_torch_save(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            torch.save(dict(payload), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def reload_theta0_checkpoint_in_fresh_process_v3(path: str | Path) -> str:
    """Return a stable tensor-state hash after a separate Python reload."""
    target = Path(path).resolve()
    script = r'''
import hashlib, json, sys, torch
path = sys.argv[1]
payload = torch.load(path, map_location="cpu", weights_only=True)
state = payload["state_dict"]
rows = []
for key in sorted(state):
    value = state[key].detach().cpu().contiguous()
    if (value.is_floating_point() or value.is_complex()) and not bool(torch.isfinite(value).all().item()):
        raise ValueError("nonfinite tensor")
    raw = value.view(torch.uint8).numpy().tobytes()
    rows.append({"key": key, "dtype": str(value.dtype), "shape": list(value.shape), "bytes_sha256": hashlib.sha256(raw).hexdigest(), "nbytes": len(raw)})
print(hashlib.sha256(json.dumps(rows, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest())
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(target)], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ValueError("θ0 checkpoint fresh-process reload failed")
    return _digest(completed.stdout.strip(), field="fresh-process tensor SHA-256")


@dataclass(frozen=True, slots=True)
class Theta0ManifestV3:
    checkpoint_path: Path
    manifest_path: Path
    checkpoint_file_sha256: str
    tensor_sha256: str
    selected_best_checkpoint_file_sha256: str
    selected_best_checkpoint_state_sha256: str
    recurrent_gate_selection_file_sha256: str
    recurrent_gate_result_file_sha256: str
    recurrent_gate_result_sha256: str
    teacher_quality_file_sha256: str
    teacher_quality_manifest_sha256: str
    teacher_quality_approved_rule_file_sha256: str
    teacher_quality_primary_evidence_files: Mapping[str, Mapping[str, str]]
    authority_hashes: Mapping[str, str]
    manifest_sha256: str


def _manifest_to_dataclass(path: Path, payload: Mapping[str, object]) -> Theta0ManifestV3:
    return Theta0ManifestV3(
        checkpoint_path=path.parent / str(payload["checkpoint_path"]), manifest_path=path,
        checkpoint_file_sha256=str(payload["checkpoint_file_sha256"]), tensor_sha256=str(payload["tensor_sha256"]),
        selected_best_checkpoint_file_sha256=str(payload["selected_best_checkpoint_file_sha256"]),
        selected_best_checkpoint_state_sha256=str(payload["selected_best_checkpoint_state_sha256"]),
        recurrent_gate_selection_file_sha256=str(payload["recurrent_gate_selection_file_sha256"]),
        recurrent_gate_result_file_sha256=str(payload["recurrent_gate_result_file_sha256"]),
        recurrent_gate_result_sha256=str(payload["recurrent_gate_result_sha256"]),
        teacher_quality_file_sha256=str(payload["teacher_quality_file_sha256"]),
        teacher_quality_manifest_sha256=str(payload["teacher_quality_manifest_sha256"]),
        teacher_quality_approved_rule_file_sha256=str(payload["teacher_quality_approved_rule_file_sha256"]),
        teacher_quality_primary_evidence_files={
            str(key): dict(value) for key, value in payload["teacher_quality_primary_evidence_files"].items()
        },
        authority_hashes=dict(payload["authority_hashes"]), manifest_sha256=str(payload["manifest_sha256"]),
    )


def read_theta0_manifest_v3(path: str | Path) -> Theta0ManifestV3:
    """Strictly validate the adjacent checkpoint and all sealed metadata."""
    target = Path(path)
    payload = _strict_canonical_object(target, name="θ0 manifest")
    required = {
        "schema", "lane", "seed", "candidate", "checkpoint_path", "checkpoint_file_sha256", "tensor_sha256",
        "selected_best_checkpoint_file_sha256", "selected_best_checkpoint_state_sha256",
        "tensors", "recurrent_gate_selection_path", "recurrent_gate_selection_file_sha256",
        "recurrent_gate_result_file_sha256", "recurrent_gate_result_sha256", "teacher_quality_manifest_path",
        "teacher_quality_file_sha256", "teacher_quality_manifest_sha256",
        "teacher_quality_approved_rule_path", "teacher_quality_approved_rule_file_sha256",
        "teacher_quality_primary_evidence_files", "authority_hashes", "source_files", "manifest_sha256",
    }
    if type(payload) is not dict or set(payload) != required or payload["schema"] != _SCHEMA:
        raise ValueError("θ0 manifest has an invalid closed schema")
    if payload["lane"] not in _LANES or type(payload["seed"]) is not int or payload["seed"] < 0 or payload["candidate"] not in {"R3-A", "R3-B"}:
        raise ValueError("θ0 manifest lane/seed/candidate is invalid")
    manifest_sha = _digest(payload["manifest_sha256"], field="θ0 manifest self SHA-256")
    if _object_sha256({key: value for key, value in payload.items() if key != "manifest_sha256"}) != manifest_sha:
        raise ValueError("θ0 manifest self hash does not verify")
    _safe_basename(payload["checkpoint_path"], field="checkpoint_path", suffix=".pt")
    _safe_basename(payload["recurrent_gate_selection_path"], field="recurrent_gate_selection_path", suffix=".json")
    _safe_basename(payload["teacher_quality_manifest_path"], field="teacher_quality_manifest_path", suffix=".json")
    _safe_basename(payload["teacher_quality_approved_rule_path"], field="teacher_quality_approved_rule_path", suffix=".json")
    for field in (
        "checkpoint_file_sha256", "tensor_sha256", "selected_best_checkpoint_file_sha256",
        "selected_best_checkpoint_state_sha256", "recurrent_gate_selection_file_sha256",
        "recurrent_gate_result_file_sha256", "recurrent_gate_result_sha256", "teacher_quality_file_sha256",
        "teacher_quality_manifest_sha256",
        "teacher_quality_approved_rule_file_sha256",
    ):
        _digest(payload[field], field=field)
    primary_evidence = payload["teacher_quality_primary_evidence_files"]
    if type(primary_evidence) is not dict or not primary_evidence:
        raise ValueError("θ0 manifest teacher primary evidence bindings are invalid")
    for source_name, binding in primary_evidence.items():
        _safe_basename(source_name, field="teacher primary evidence source name", suffix=".json")
        if type(binding) is not dict or set(binding) != {"path", "file_sha256"}:
            raise ValueError("θ0 manifest teacher primary evidence has an invalid closed nested schema")
        _safe_basename(binding["path"], field=f"teacher primary evidence {source_name} path", suffix=".json")
        _digest(binding["file_sha256"], field=f"teacher primary evidence {source_name} file SHA-256")
    authority = payload["authority_hashes"]
    if type(authority) is not dict or set(authority) != _AUTHORITY_KEYS:
        raise ValueError("θ0 manifest authority hash schema is invalid")
    for key, value in authority.items():
        _digest(value, field=f"authority_hashes.{key}")
    source_files = payload["source_files"]
    if type(source_files) is not dict or not source_files:
        raise ValueError("θ0 manifest source file bindings are invalid")
    if any(type(key) is not str or not key or type(value) is not str or _SHA256.fullmatch(value) is None for key, value in source_files.items()):
        raise ValueError("θ0 manifest source file hashes are invalid")
    tensors = payload["tensors"]
    if type(tensors) is not dict or not tensors:
        raise ValueError("θ0 manifest tensor metadata is invalid")
    for key, row in tensors.items():
        if (type(key) is not str or not key or type(row) is not dict
                or set(row) != {"dtype", "shape", "bytes_sha256", "nbytes"}
                or type(row["dtype"]) is not str or not row["dtype"].startswith("torch.")
                or type(row["shape"]) is not list
                or any(type(size) is not int or isinstance(size, bool) or size < 0 for size in row["shape"])
                or type(row["nbytes"]) is not int or isinstance(row["nbytes"], bool) or row["nbytes"] < 0):
            raise ValueError("θ0 manifest tensor metadata has an invalid closed nested schema")
        _digest(row["bytes_sha256"], field=f"tensors.{key}.bytes_sha256")
    checkpoint = target.parent / str(payload["checkpoint_path"])
    if _file_sha256(checkpoint) != payload["checkpoint_file_sha256"]:
        raise ValueError("θ0 checkpoint file SHA-256 does not match manifest")
    loaded = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if type(loaded) is not dict or set(loaded) != {"state_dict"}:
        raise ValueError("θ0 checkpoint has an invalid closed payload")
    _state, tensor_metadata, tensor_sha = _canonical_state(loaded["state_dict"])
    if tensor_metadata != payload["tensors"] or tensor_sha != payload["tensor_sha256"]:
        raise ValueError("θ0 checkpoint tensors do not match manifest")
    if reload_theta0_checkpoint_in_fresh_process_v3(checkpoint) != payload["tensor_sha256"]:
        raise ValueError("θ0 checkpoint fresh-process hash does not match manifest")
    return _manifest_to_dataclass(target, payload)


def verify_theta0_manifest_anchor_v3(
    path: str | Path, *, expected_manifest_file_sha256: str,
) -> Theta0ManifestV3:
    """Read θ0 only after an external manifest-bytes anchor has matched.

    The canonical manifest self hash detects accidental corruption.  It cannot
    detect an actor who rewrites both the manifest body and self hash, so any
    consumer that treats θ0 as lineage authority must use this verifier with a
    caller-owned file digest.
    """
    target = Path(path)
    expected = _digest(expected_manifest_file_sha256, field="expected θ0 manifest file SHA-256")
    if _file_sha256(target) != expected:
        raise ValueError("θ0 manifest external file SHA-256 anchor does not match")
    return read_theta0_manifest_v3(target)


def _publish_theta0_bundle_v3(
    *, checkpoint_path: Path, manifest_path: Path, state: Mapping[str, torch.Tensor],
    tensor_sha: str, manifest_body: Mapping[str, object],
) -> Theta0ManifestV3:
    """Publish checkpoint+manifest as one recoverable bundle after authority checks."""
    if checkpoint_path.exists() or manifest_path.exists():
        raise ValueError("θ0 output target already exists; refusing overwrite")
    try:
        _atomic_torch_save(checkpoint_path, {"state_dict": state})
        if reload_theta0_checkpoint_in_fresh_process_v3(checkpoint_path) != tensor_sha:
            raise ValueError("θ0 checkpoint fresh-process reload does not preserve tensor bytes")
        body = {
            **manifest_body, "checkpoint_path": checkpoint_path.name,
            "checkpoint_file_sha256": _file_sha256(checkpoint_path),
        }
        payload = {**body, "manifest_sha256": _object_sha256(body)}
        _atomic_json_write(manifest_path, payload)
        return read_theta0_manifest_v3(manifest_path)
    except BaseException:
        # Both targets were proven absent above, so cleanup cannot remove
        # pre-existing user data.  An orphan checkpoint is never authoritative.
        manifest_path.unlink(missing_ok=True)
        checkpoint_path.unlink(missing_ok=True)
        _fsync_directory(checkpoint_path.parent)
        raise


def seal_theta0_v3(
    *, checkpoint_state: Mapping[str, torch.Tensor], recurrent_selection_path: str | Path,
    expected_selection_file_sha256: str, expected_result_file_sha256: str, expected_result_sha256: str,
    teacher_quality_manifest_path: str | Path, expected_teacher_quality_file_sha256: str,
    expected_teacher_quality_manifest_sha256: str, teacher_quality_approved_rule_path: str | Path,
    expected_teacher_quality_approved_rule_file_sha256: str,
    teacher_quality_primary_evidence_paths: Mapping[str, str | Path],
    expected_teacher_quality_primary_evidence_file_sha256: Mapping[str, str],
    authority_hashes: Mapping[str, str],
    source_files: Mapping[str, str | Path], allowed_source_root: str | Path, output_dir: str | Path,
    lane: str, seed: int,
) -> Theta0ManifestV3:
    """Atomically seal a selected recurrent R3 state only after all authorities verify."""
    if lane not in _LANES or type(seed) is not int or seed < 0:
        raise ValueError("θ0 lane/seed is invalid")
    if type(authority_hashes) is not dict or set(authority_hashes) != _AUTHORITY_KEYS:
        raise ValueError("θ0 authority hashes must have the closed source/data/split/model/config/command/seed/metric/result keys")
    normalized_authority = {key: _digest(value, field=f"authority_hashes.{key}") for key, value in sorted(authority_hashes.items())}
    if type(source_files) is not dict or not source_files or any(type(key) is not str or not key for key in source_files):
        raise ValueError("θ0 source file bindings are invalid")
    source_root = Path(allowed_source_root).resolve()
    if not source_root.is_dir():
        raise ValueError("θ0 explicit source allowlist root is invalid")
    source_bindings = {key: _file_sha256(_contained(value, root=source_root, field=f"source_files.{key}")) for key, value in sorted(source_files.items())}
    gate = verify_recurrent_gate_anchor_v3(
        recurrent_selection_path, expected_selection_file_sha256=expected_selection_file_sha256,
        expected_result_file_sha256=expected_result_file_sha256, expected_result_sha256=expected_result_sha256,
    )
    selection = gate["selection"]
    if selection.get("status") != "SELECTED" or selection.get("promotion_authority") is not True or selection.get("preferred") not in {"R3-A", "R3-B"}:
        raise ValueError("recurrent Gate selection has no promotion authority for θ0")
    candidate, _best_checkpoint_path, best_checkpoint_file_sha, best_checkpoint_state_sha = _selected_checkpoint_binding(
        gate, selection_path=Path(recurrent_selection_path), checkpoint_state=checkpoint_state,
        lane=lane, seed=seed,
    )
    teacher = _read_ready_teacher_quality(
        teacher_quality_manifest_path, expected_file_sha256=expected_teacher_quality_file_sha256,
        expected_manifest_sha256=expected_teacher_quality_manifest_sha256,
        approved_rule_path=teacher_quality_approved_rule_path,
        expected_approved_rule_file_sha256=expected_teacher_quality_approved_rule_file_sha256,
        primary_evidence_paths=teacher_quality_primary_evidence_paths,
        expected_primary_evidence_file_sha256=expected_teacher_quality_primary_evidence_file_sha256,
    )
    del teacher  # authority was fully checked above; never embed its unpinned body.
    approved_rule_sha = _digest(
        expected_teacher_quality_approved_rule_file_sha256,
        field="expected teacher-quality approved rule file SHA-256",
    )
    primary_evidence_bindings = {
        source_name: {
            "path": Path(teacher_quality_primary_evidence_paths[source_name]).name,
            "file_sha256": _digest(file_sha, field=f"teacher primary evidence {source_name} file SHA-256"),
        }
        for source_name, file_sha in sorted(expected_teacher_quality_primary_evidence_file_sha256.items())
    }
    state, tensors, tensor_sha = _canonical_state(checkpoint_state)
    out = Path(output_dir)
    if out.exists() and not out.is_dir():
        raise ValueError("θ0 output path is not a directory")
    out.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out / f"theta0-{lane}-{seed}.pt"
    manifest_path = out / f"theta0-{lane}-{seed}.json"
    manifest_body: dict[str, Any] = {
            "schema": _SCHEMA, "lane": lane, "seed": seed, "candidate": candidate,
            "tensor_sha256": tensor_sha, "tensors": tensors,
            "selected_best_checkpoint_file_sha256": best_checkpoint_file_sha,
            "selected_best_checkpoint_state_sha256": best_checkpoint_state_sha,
            "recurrent_gate_selection_path": Path(recurrent_selection_path).name,
            "recurrent_gate_selection_file_sha256": expected_selection_file_sha256,
            "recurrent_gate_result_file_sha256": expected_result_file_sha256,
            "recurrent_gate_result_sha256": expected_result_sha256,
            "teacher_quality_manifest_path": Path(teacher_quality_manifest_path).name,
            "teacher_quality_file_sha256": expected_teacher_quality_file_sha256,
            "teacher_quality_manifest_sha256": expected_teacher_quality_manifest_sha256,
            "teacher_quality_approved_rule_path": Path(teacher_quality_approved_rule_path).name,
            "teacher_quality_approved_rule_file_sha256": approved_rule_sha,
            "teacher_quality_primary_evidence_files": primary_evidence_bindings,
            "authority_hashes": normalized_authority, "source_files": source_bindings,
    }
    return _publish_theta0_bundle_v3(
        checkpoint_path=checkpoint_path, manifest_path=manifest_path, state=state,
        tensor_sha=tensor_sha, manifest_body=manifest_body,
    )


__all__ = ["Theta0ManifestV3", "read_theta0_manifest_v3", "reload_theta0_checkpoint_in_fresh_process_v3", "seal_theta0_v3", "verify_theta0_manifest_anchor_v3"]
