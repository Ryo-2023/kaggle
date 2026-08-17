from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import torch


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write_recurrent_selection(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Write the smallest externally-anchorable selected CUDA Gate fixture."""
    from tests.meta_specialist.test_recurrent_gate_v3 import _matrix
    from mage_ptcg.meta_specialist import recurrent_gate_v3 as recurrent_gate

    result_path = tmp_path / "recurrent-gate-result-v3-cuda-0.json"
    cells = _matrix()
    checkpoints = tmp_path / "checkpoints"; checkpoints.mkdir()
    for cell in cells:
        cell["cuda_peak_memory_bytes"] = 1
        cell["cuda_device_name"] = "fixture-gpu"
        cell["cuda_peak_memory_measured"] = True
        # Gate v2 binds a real trained checkpoint for current-R2 as well as
        # both recurrent candidates.  Keep the fixture structurally valid so
        # this test reaches the intended pending-promotion rejection.
        if cell["candidate"] in {"current-R2", "R3-A", "R3-B"}:
            state = {"weight": torch.tensor([float(cell["seed"])])}
            state_sha = recurrent_gate._state_sha256_v3(state)
            descriptor = cell["checkpoint"]
            path = tmp_path / descriptor["path"]
            torch.save(state, path)
            descriptor["file_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            descriptor["state_sha256"] = state_sha
            cell["checkpoint_sha256"] = state_sha
    selection = recurrent_gate._selection_for_device_v3(cells, device="cuda:0")
    result = {"schema": recurrent_gate._RESULT_SCHEMA, "device": "cuda:0", "seeds": [7, 17, 29], "cells": cells, "selection": selection}
    result["result_sha256"] = _sha(result)
    result_path.write_bytes(_canonical(result))
    selection_path = tmp_path / "recurrent-gate-selection-v3-cuda-0.json"
    payload = {
        "schema": recurrent_gate._SELECTION_SCHEMA, "result_path": result_path.name,
        "result_file_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "result_sha256": result["result_sha256"], "selection": selection,
    }
    payload["selection_sha256"] = _sha(payload)
    selection_path.write_bytes(_canonical(payload))
    return (
        selection_path, hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        hashlib.sha256(result_path.read_bytes()).hexdigest(), result["result_sha256"],
    )


def _write_teacher_quality(tmp_path: Path) -> tuple[Path, str, str]:
    path = tmp_path / "teacher-quality.json"
    payload = {"schema": "meta-specialist-teacher-quality-v1", "status": "READY", "quality_manifest_sha256": ""}
    payload["quality_manifest_sha256"] = _sha({key: value for key, value in payload.items() if key != "quality_manifest_sha256"})
    path.write_bytes(_canonical(payload))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), payload["quality_manifest_sha256"]


def _write_physical_teacher_quality(
    tmp_path: Path,
) -> tuple[Path, str, str, Path, str, dict[str, Path], dict[str, str]]:
    from tests.meta_specialist.test_teacher_quality_v3 import _write_ready_authorities

    return _write_ready_authorities(tmp_path)


def _authority_hashes() -> dict[str, str]:
    return {key: hashlib.sha256(key.encode("ascii")).hexdigest() for key in ("source", "data", "split", "model", "config", "command", "seed", "metric", "result")}


def test_theta0_actual_seal_fails_closed_while_gate_runtime_authority_is_pending(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import seal_theta0_v3

    selection, selection_file_sha, result_file_sha, result_sha = _write_recurrent_selection(tmp_path)
    (
        teacher, teacher_file_sha, teacher_sha, rule_path, rule_sha,
        evidence_paths, evidence_shas,
    ) = _write_physical_teacher_quality(tmp_path)
    sources = tmp_path / "sources"; sources.mkdir()
    source = sources / "training.py"; source.write_text("# fixture\n", encoding="utf-8")
    with pytest.raises(ValueError, match="runtime evidence is pending|promotion"):
        seal_theta0_v3(
            checkpoint_state={"weight": torch.tensor([[1.0, 2.0]], dtype=torch.float32)},
            recurrent_selection_path=selection,
            expected_selection_file_sha256=selection_file_sha,
            expected_result_file_sha256=result_file_sha,
            expected_result_sha256=result_sha,
            teacher_quality_manifest_path=teacher,
            expected_teacher_quality_file_sha256=teacher_file_sha,
            expected_teacher_quality_manifest_sha256=teacher_sha,
            teacher_quality_approved_rule_path=rule_path,
            expected_teacher_quality_approved_rule_file_sha256=rule_sha,
            teacher_quality_primary_evidence_paths=evidence_paths,
            expected_teacher_quality_primary_evidence_file_sha256=evidence_shas,
            authority_hashes=_authority_hashes(), source_files={"training": source},
            allowed_source_root=sources, output_dir=tmp_path / "out", lane="alakazam", seed=7,
        )
    assert not (tmp_path / "out").exists()


def test_theta0_strict_reader_rejects_duplicate_and_noncanonical_json_before_checkpoint_load(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import read_theta0_manifest_v3

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"schema":"meta-specialist-theta0-v3","lane":"archaludon","lane":"alakazam"}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        read_theta0_manifest_v3(duplicate)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps({"schema": "meta-specialist-theta0-v3"}, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical JSON"):
        read_theta0_manifest_v3(noncanonical)


def test_theta0_rejects_the_old_independent_ready_teacher_schema(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import _read_ready_teacher_quality

    teacher, teacher_file_sha, teacher_sha = _write_teacher_quality(tmp_path)
    dummy_rule = tmp_path / "approved-rule.json"
    dummy_rule.write_bytes(_canonical({"not": "an approved rule"}))
    with pytest.raises(ValueError, match="schema|READY|authority|SHA-256"):
        _read_ready_teacher_quality(
            teacher, expected_file_sha256=teacher_file_sha,
            expected_manifest_sha256=teacher_sha,
            approved_rule_path=dummy_rule,
            expected_approved_rule_file_sha256=hashlib.sha256(dummy_rule.read_bytes()).hexdigest(),
            primary_evidence_paths={}, expected_primary_evidence_file_sha256={},
        )


def test_theta0_teacher_reader_requires_physical_rule_and_primary_evidence_anchors(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import _read_ready_teacher_quality

    (
        teacher, teacher_file_sha, teacher_sha, rule_path, rule_sha,
        evidence_paths, evidence_shas,
    ) = _write_physical_teacher_quality(tmp_path)
    ready = _read_ready_teacher_quality(
        teacher, expected_file_sha256=teacher_file_sha,
        expected_manifest_sha256=teacher_sha, approved_rule_path=rule_path,
        expected_approved_rule_file_sha256=rule_sha,
        primary_evidence_paths=evidence_paths,
        expected_primary_evidence_file_sha256=evidence_shas,
    )
    assert ready["status"] == "READY"
    with pytest.raises(ValueError, match="primary|key set|artifact"):
        _read_ready_teacher_quality(
            teacher, expected_file_sha256=teacher_file_sha,
            expected_manifest_sha256=teacher_sha, approved_rule_path=rule_path,
            expected_approved_rule_file_sha256=rule_sha,
            primary_evidence_paths={}, expected_primary_evidence_file_sha256={},
        )


@pytest.mark.parametrize("mutate", ("outside_source", "bad_gate_anchor"))
def test_theta0_seal_fails_closed_before_writing_for_untrusted_authority(tmp_path: Path, mutate: str) -> None:
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import seal_theta0_v3

    selection, selection_file_sha, result_file_sha, result_sha = _write_recurrent_selection(tmp_path)
    (
        teacher, teacher_file_sha, teacher_sha, rule_path, rule_sha,
        evidence_paths, evidence_shas,
    ) = _write_physical_teacher_quality(tmp_path)
    sources = tmp_path / "sources"; sources.mkdir()
    source = sources / "training.py"; source.write_text("# fixture\n", encoding="utf-8")
    state: dict[str, torch.Tensor] = {"weight": torch.tensor([1.0])}
    if mutate == "outside_source":
        source = tmp_path / "outside.py"; source.write_text("# outside\n", encoding="utf-8")
    elif mutate == "bad_gate_anchor":
        result_file_sha = "f" * 64

    with pytest.raises(ValueError, match="SHA|allowlist|source"):
        seal_theta0_v3(
            checkpoint_state=state, recurrent_selection_path=selection,
            expected_selection_file_sha256=selection_file_sha,
            expected_result_file_sha256=result_file_sha, expected_result_sha256=result_sha,
            teacher_quality_manifest_path=teacher,
            expected_teacher_quality_file_sha256=teacher_file_sha,
            expected_teacher_quality_manifest_sha256=teacher_sha,
            teacher_quality_approved_rule_path=rule_path,
            expected_teacher_quality_approved_rule_file_sha256=rule_sha,
            teacher_quality_primary_evidence_paths=evidence_paths,
            expected_teacher_quality_primary_evidence_file_sha256=evidence_shas,
            authority_hashes=_authority_hashes(), source_files={"training": source},
            allowed_source_root=sources, output_dir=tmp_path / "out", lane="alakazam", seed=7,
        )
    assert not (tmp_path / "out").exists()


def test_theta0_rejects_nonfinite_checkpoint_state_before_publication() -> None:
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import _canonical_state

    with pytest.raises(ValueError, match="finite"):
        _canonical_state({"weight": torch.tensor([float("nan")])})


def test_selected_checkpoint_binding_rejects_wrong_seed_identity_or_unrelated_tensors(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist import recurrent_gate_v3 as gate_module
    from mage_ptcg.meta_specialist.theta0_manifest_v3 import _selected_checkpoint_binding

    state = {"weight": torch.tensor([7.0])}
    checkpoint_dir = tmp_path / "checkpoints"; checkpoint_dir.mkdir()
    checkpoint = checkpoint_dir / "recurrent-checkpoint-R3-A-alakazam-7.pt"
    torch.save(state, checkpoint)
    state_sha = gate_module._state_sha256_v3(state)
    descriptor = {
        "basename": checkpoint.name, "path": f"checkpoints/{checkpoint.name}",
        "file_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "state_sha256": state_sha, "candidate": "R3-A", "lane": "alakazam", "seed": 7,
    }
    gate = {
        "selection": {"preferred": "R3-A"},
        "cells": [{"candidate": "R3-A", "lane": "alakazam", "seed": 7,
                   "checkpoint_sha256": state_sha, "checkpoint": descriptor}],
    }
    selection_path = tmp_path / "recurrent-gate-selection-v3-cuda-0.json"

    assert _selected_checkpoint_binding(
        gate, selection_path=selection_path, checkpoint_state=state, lane="alakazam", seed=7,
    )[0] == "R3-A"
    with pytest.raises(ValueError, match="candidate/lane/seed"):
        _selected_checkpoint_binding(
            gate, selection_path=selection_path, checkpoint_state=state, lane="alakazam", seed=999,
        )
    with pytest.raises(ValueError, match="unrelated"):
        _selected_checkpoint_binding(
            gate, selection_path=selection_path,
            checkpoint_state={"unrelated_weight": torch.tensor([123.0])}, lane="alakazam", seed=7,
        )
    wrong_identity = json.loads(json.dumps(gate))
    wrong_identity["cells"][0]["checkpoint"]["lane"] = "archaludon"
    with pytest.raises(ValueError, match="candidate/lane/seed"):
        _selected_checkpoint_binding(
            wrong_identity, selection_path=selection_path, checkpoint_state=state,
            lane="alakazam", seed=7,
        )
    wrong_candidate = json.loads(json.dumps(gate))
    wrong_candidate["cells"][0]["checkpoint"]["candidate"] = "R3-B"
    with pytest.raises(ValueError, match="candidate/lane/seed"):
        _selected_checkpoint_binding(
            wrong_candidate, selection_path=selection_path, checkpoint_state=state,
            lane="alakazam", seed=7,
        )
    checkpoint.write_bytes(b"replaced checkpoint bytes")
    with pytest.raises(ValueError, match="file SHA-256"):
        _selected_checkpoint_binding(
            gate, selection_path=selection_path, checkpoint_state=state,
            lane="alakazam", seed=7,
        )


def _bundle_body(tensors: dict[str, dict[str, object]], tensor_sha: str) -> dict[str, object]:
    return {
        "schema": "meta-specialist-theta0-v3", "lane": "alakazam", "seed": 7,
        "candidate": "R3-A", "tensor_sha256": tensor_sha, "tensors": tensors,
        "selected_best_checkpoint_file_sha256": "1" * 64,
        "selected_best_checkpoint_state_sha256": "2" * 64,
        "recurrent_gate_selection_path": "recurrent-gate-selection-v3-cuda-0.json",
        "recurrent_gate_selection_file_sha256": "3" * 64,
        "recurrent_gate_result_file_sha256": "4" * 64,
        "recurrent_gate_result_sha256": "5" * 64,
        "teacher_quality_manifest_path": "teacher-quality.json",
        "teacher_quality_file_sha256": "6" * 64,
        "teacher_quality_manifest_sha256": "7" * 64,
        "teacher_quality_approved_rule_path": "approved-rule.json",
        "teacher_quality_approved_rule_file_sha256": "9" * 64,
        "teacher_quality_primary_evidence_files": {
            "source.json": {"path": "source.json", "file_sha256": "a" * 64},
        },
        "authority_hashes": _authority_hashes(), "source_files": {"training": "8" * 64},
    }


def test_theta0_internal_bundle_fresh_reloads_and_cleans_checkpoint_if_manifest_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import theta0_manifest_v3 as theta0

    state, tensors, tensor_sha = theta0._canonical_state({"weight": torch.tensor([1.0, 2.0])})
    checkpoint = tmp_path / "theta0-alakazam-7.pt"
    manifest = tmp_path / "theta0-alakazam-7.json"
    sealed = theta0._publish_theta0_bundle_v3(
        checkpoint_path=checkpoint, manifest_path=manifest, state=state,
        tensor_sha=tensor_sha, manifest_body=_bundle_body(tensors, tensor_sha),
    )
    assert theta0.reload_theta0_checkpoint_in_fresh_process_v3(checkpoint) == sealed.tensor_sha256

    failed_checkpoint = tmp_path / "theta0-alakazam-17.pt"
    failed_manifest = tmp_path / "theta0-alakazam-17.json"
    monkeypatch.setattr(theta0, "_atomic_json_write", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fixture publish failure")))
    with pytest.raises(OSError, match="fixture publish failure"):
        theta0._publish_theta0_bundle_v3(
            checkpoint_path=failed_checkpoint, manifest_path=failed_manifest, state=state,
            tensor_sha=tensor_sha, manifest_body={**_bundle_body(tensors, tensor_sha), "seed": 17},
        )
    assert not failed_checkpoint.exists()
    assert not failed_manifest.exists()


def test_theta0_external_anchor_rejects_rehashed_primary_authority_tamper(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist import theta0_manifest_v3 as theta0

    state, tensors, tensor_sha = theta0._canonical_state({"weight": torch.tensor([1.0])})
    checkpoint = tmp_path / "theta0-alakazam-7.pt"
    manifest = tmp_path / "theta0-alakazam-7.json"
    theta0._publish_theta0_bundle_v3(
        checkpoint_path=checkpoint, manifest_path=manifest, state=state,
        tensor_sha=tensor_sha, manifest_body=_bundle_body(tensors, tensor_sha),
    )
    external_anchor = hashlib.sha256(manifest.read_bytes()).hexdigest()
    theta0.verify_theta0_manifest_anchor_v3(
        manifest, expected_manifest_file_sha256=external_anchor,
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["teacher_quality_primary_evidence_files"]["source.json"]["file_sha256"] = "b" * 64
    payload["manifest_sha256"] = _sha({key: value for key, value in payload.items() if key != "manifest_sha256"})
    manifest.write_bytes(_canonical(payload))
    with pytest.raises(ValueError, match="external file SHA-256"):
        theta0.verify_theta0_manifest_anchor_v3(
            manifest, expected_manifest_file_sha256=external_anchor,
        )
