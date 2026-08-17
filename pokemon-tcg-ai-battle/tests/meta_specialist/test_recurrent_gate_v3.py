from __future__ import annotations

from types import SimpleNamespace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import torch
from torch import nn

from mage_ptcg.meta_specialist.bc_trainer_v3 import BCExampleV3, RecurrentBCSequenceV3
from mage_ptcg.meta_specialist.neural_model_v3 import PolicyOutputV3
from mage_ptcg.meta_specialist.recurrent_gate_v3 import (
    RecurrentSequenceSourceV3,
    evaluate_carry_vs_reset_v3,
    prepared_recurrent_sequence_source_v3,
    sealed_recurrent_sequence_source_v3,
    train_recurrent_r3_v3,
)
from mage_ptcg.meta_specialist import recurrent_gate_v3 as recurrent_gate
from mage_ptcg.meta_specialist.neural_model_v3 import SpecialistModelV3
from mage_ptcg.meta_specialist.representation_v3 import ActionCandidateV3, EntityTokenV3, RelationalStateV3


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _cell(
    candidate: str, lane: str, seed: int, *, carry_complete: float = 0.40,
    reset_complete: float = 0.40, carry_stop: float = 0.20, reset_stop: float = 0.20,
) -> dict[str, object]:
    result = {
        "candidate": candidate, "lane": lane, "seed": seed,
        "manifest_file_sha256": "a" * 64 if lane == "alakazam" else "b" * 64,
        "manifest_sha256": "c" * 64 if lane == "alakazam" else "d" * 64,
        "budget": {
            "max_epochs": 4, "patience": 2, "min_delta": 0.0001,
            "burn_in": 1, "learning_rate": 0.0001, "gradient_clip_norm": 1.0,
        },
        "checkpoint_sha256": "e" * 64, "optimizer_updates": 1,
        "parameter_delta_l1": 0.1, "carry_complete_nll": carry_complete,
        "reset_complete_nll": reset_complete, "carry_stop_nll": carry_stop,
        "reset_stop_nll": reset_stop, "complete_rows": 2, "stop_target_rows": 1,
        "forced_sole_stop_rows": 0, "non_reset_hidden_steps": 1,
        "validation_complete_nll": carry_complete, "top1": 0.50, "top3": 0.90,
        "rare_action_recall": {"rule_version": "train-action-type-frequency-lte-1-v1", "eligible": 1, "value": 0.5, "status": "measured"},
        "calibration": {"bin_count": 10, "expected_calibration_error": 0.1, "sample_count": 2},
        "training": {"epochs": 2, "best_epoch": 0, "stop_reason": "patience", "update_unit": "physical-sequence", "validation_authority": "independent-sealed-validation"},
        "coverage": {
            "schema": "meta-specialist-recurrent-cell-coverage-v1",
            "train": {"sequence_count": 2, "step_count": 4, "stop_available_count": 2, "positive_stop_target_count": 1, "nonempty_prefix_count": 1, "ordered_nonempty_prefix_count": 1, "burn_in_step_count": 0},
            "validation": {"sequence_count": 1, "step_count": 2, "stop_available_count": 1, "positive_stop_target_count": 1, "nonempty_prefix_count": 1, "ordered_nonempty_prefix_count": 1, "burn_in_step_count": 0},
        },
    }
    if candidate == "current-R2":
        result["reference_kind"] = "CurrentR2GateAdapterV3"
        result["non_reset_hidden_steps"] = 0
    result["checkpoint"] = {
        "basename": f"recurrent-checkpoint-{candidate}-{lane}-{seed}.pt",
        "path": f"checkpoints/recurrent-checkpoint-{candidate}-{lane}-{seed}.pt",
        "file_sha256": "f" * 64, "state_sha256": "e" * 64,
        "candidate": candidate, "lane": lane, "seed": seed,
        "gradient_clip_norm": 1.0,
    }
    return result


def _matrix(**overrides: tuple[float, float, float, float]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for candidate in ("current-R2", "R3-A", "R3-B"):
        for lane in ("alakazam", "archaludon"):
            carry_complete, reset_complete, carry_stop, reset_stop = overrides.get(
                f"{candidate}:{lane}",
                (0.40, 0.40, 0.20, 0.20) if candidate == "current-R2"
                else (0.38, 0.40, 0.20, 0.20) if candidate == "R3-A" and lane == "alakazam"
                else (0.40, 0.40, 0.20, 0.20),
            )
            rows.extend(
                _cell(candidate, lane, seed, carry_complete=carry_complete,
                      reset_complete=reset_complete, carry_stop=carry_stop, reset_stop=reset_stop)
                for seed in (7, 17, 29)
            )
    return rows


def test_recurrent_selection_blocks_a_lane_when_carry_exceeds_reset_tolerance() -> None:
    """One failed seed/lane cannot be hidden by the other eleven cells."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    decision = select_recurrent_r3_v3(_matrix(**{"R3-A:archaludon": (0.53, 0.50, 0.20, 0.20)}))

    assert decision["lanes"]["archaludon"]["preferred"] == "current-R2"
    assert any("temporal" in item for item in decision["lanes"]["archaludon"]["blockers"])


def test_recurrent_selection_is_lane_local_and_pending_runtime_after_supervised_selection() -> None:
    """One lane's R3 failure falls back only to that lane and never fabricates runtime PASS."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    decision = select_recurrent_r3_v3(_matrix(**{
        "R3-A:archaludon": (0.53, 0.50, 0.20, 0.20),
        "R3-B:archaludon": (0.53, 0.50, 0.20, 0.20),
    }))

    assert decision["lanes"]["alakazam"]["status"] == "MODEL_SELECTED_PENDING_RUNTIME"
    assert decision["lanes"]["alakazam"]["preferred"] == "R3-A"
    assert decision["lanes"]["archaludon"]["status"] == "CURRENT_R2_FALLBACK"
    assert decision["lanes"]["archaludon"]["preferred"] == "current-R2"
    assert decision["status"] == "MODEL_SELECTED_PENDING_RUNTIME"
    assert decision["promotion_authority"] is False


def test_recurrent_selection_requires_r2_reference_cells_and_r3_absolute_top1_floor() -> None:
    """R3 cannot qualify without the same-lane R2 reference or when it regresses top-1 too far."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    cells = _matrix()
    cells = [cell for cell in cells if not (cell["candidate"] == "current-R2" and cell["lane"] == "alakazam")]
    blocked = select_recurrent_r3_v3(cells)
    assert blocked["lanes"]["alakazam"]["status"] == "BLOCKED"
    cells = _matrix()
    for cell in cells:
        if cell["candidate"] == "R3-A" and cell["lane"] == "alakazam":
            cell["top1"] = 0.47
    fallback = select_recurrent_r3_v3(cells)
    assert fallback["lanes"]["alakazam"]["preferred"] == "current-R2"


def test_recurrent_selection_rejects_untrained_r2_self_reported_metrics() -> None:
    """A metric-only current-R2 fixture cannot act as the lane fallback authority."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    cells = _matrix()
    current = next(cell for cell in cells if cell["candidate"] == "current-R2")
    current.pop("checkpoint")
    current.pop("checkpoint_sha256")
    current.pop("optimizer_updates")
    current.pop("parameter_delta_l1")
    assert "checkpoint" not in current

    decision = select_recurrent_r3_v3(cells)

    assert decision["lanes"][current["lane"]]["status"] == "BLOCKED"
    assert any("checkpoint" in item or "parameter_delta" in item for item in decision["blockers"])


def test_recurrent_selection_requires_the_registered_r3b_margin() -> None:
    """R3-B is not selected merely because it wins an unregistered tiny amount."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    without_margin = select_recurrent_r3_v3(_matrix(**{
        "R3-A:alakazam": (0.38, 0.40, 0.20, 0.20),
        "R3-A:archaludon": (0.38, 0.40, 0.20, 0.20),
        "R3-B:alakazam": (0.395, 0.40, 0.20, 0.20),
        "R3-B:archaludon": (0.395, 0.40, 0.20, 0.20),
    }))
    with_stop_margin = select_recurrent_r3_v3(_matrix(**{
        "R3-A:archaludon": (0.38, 0.40, 0.20, 0.20),
        "R3-B:alakazam": (0.40, 0.40, 0.17, 0.20),
        "R3-B:archaludon": (0.40, 0.40, 0.17, 0.20),
    }))

    assert without_margin["status"] == "MODEL_SELECTED_PENDING_RUNTIME"
    assert all(row["preferred"] == "R3-A" for row in without_margin["lanes"].values())
    assert with_stop_margin["status"] == "MODEL_SELECTED_PENDING_RUNTIME"
    assert all(row["preferred"] == "R3-B" for row in with_stop_margin["lanes"].values())


def test_recurrent_selection_blocks_cross_candidate_source_or_budget_drift() -> None:
    """Comparing R3-A to R3-B is meaningless unless each lane reuses its sealed setup."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    cells = _matrix()
    cells[-1]["manifest_file_sha256"] = "f" * 64

    decision = select_recurrent_r3_v3(cells)

    assert decision["lanes"]["archaludon"]["preferred"] == "current-R2"
    assert any("cross-candidate" in item for item in decision["lanes"]["archaludon"]["blockers"])


def test_recurrent_cell_binds_the_fixed_gradient_clip_to_budget_and_checkpoint() -> None:
    """A result cannot claim a different clip than the optimizer/checkpoint identity."""
    valid = _cell("current-R2", "alakazam", 7)

    assert recurrent_gate._selection_cell_v3(valid)[:3] == ("current-R2", "alakazam", 7)

    budget_drift = json.loads(json.dumps(valid))
    budget_drift["budget"]["gradient_clip_norm"] = 2.0
    with pytest.raises(ValueError, match="gradient_clip_norm changed"):
        recurrent_gate._selection_cell_v3(budget_drift)

    checkpoint_drift = json.loads(json.dumps(valid))
    checkpoint_drift["checkpoint"]["gradient_clip_norm"] = 2.0
    with pytest.raises(ValueError, match="checkpoint.*gradient_clip_norm"):
        recurrent_gate._selection_cell_v3(checkpoint_drift)


def test_recurrent_selection_marks_zero_ordered_prefix_coverage_unmeasured() -> None:
    """A lane with no ordered nonempty-prefix evidence cannot emit optimistic R3 pending."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import select_recurrent_r3_v3

    cells = _matrix()
    for cell in cells:
        if cell["lane"] == "alakazam":
            cell["coverage"]["train"]["ordered_nonempty_prefix_count"] = 0
            cell["coverage"]["validation"]["ordered_nonempty_prefix_count"] = 0

    decision = select_recurrent_r3_v3(cells)

    assert decision["lanes"]["alakazam"]["status"] == "UNMEASURED_ORDERED_PREFIX"
    assert decision["lanes"]["alakazam"]["preferred"] == "current-R2"
    assert decision["status"] == "BLOCKED_COVERAGE_UNMEASURED"
    assert decision["promotion_authority"] is False


def test_recurrent_gate_external_anchor_rejects_rehashed_preferred_tamper(tmp_path: Path) -> None:
    """Self-hashing an edited result must not defeat the caller's file anchor."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import (
        read_recurrent_gate_selection_v3,
        verify_recurrent_gate_anchor_v3,
    )

    result_path = tmp_path / "recurrent-gate-result-v3-cpu.json"
    result = {
        "schema": "meta-specialist-recurrent-gate-result-v2", "device": "cpu",
        "seeds": [7, 17, 29], "cells": _matrix(),
        "selection": recurrent_gate._selection_for_device_v3(_matrix(), device="cpu"),
    }
    result["result_sha256"] = _digest(result)
    result_path.write_bytes(_canonical(result))
    selection_path = tmp_path / "recurrent-gate-selection-v3-cpu.json"
    selection = {
        "schema": "meta-specialist-recurrent-gate-selection-v2", "result_path": result_path.name,
        "result_file_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "result_sha256": result["result_sha256"], "selection": result["selection"],
    }
    selection["selection_sha256"] = _digest(selection)
    selection_path.write_bytes(_canonical(selection))
    original_result_file_sha = hashlib.sha256(result_path.read_bytes()).hexdigest()
    original_selection_file_sha = hashlib.sha256(selection_path.read_bytes()).hexdigest()
    original_result_sha = result["result_sha256"]

    assert read_recurrent_gate_selection_v3(selection_path)["selection"]["status"] == "RESEARCH_ONLY"
    result["selection"] = {**result["selection"], "preferred": "R3-B"}
    result["result_sha256"] = _digest({key: value for key, value in result.items() if key != "result_sha256"})
    result_path.write_bytes(_canonical(result))

    with pytest.raises(ValueError, match="file SHA"):
        verify_recurrent_gate_anchor_v3(
            selection_path, expected_selection_file_sha256=original_selection_file_sha,
            expected_result_file_sha256=original_result_file_sha,
            expected_result_sha256=original_result_sha,
        )


def test_recurrent_gate_runs_the_exact_18_cell_matrix_and_seals_atomic_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production orchestrator must not silently skip a lane, candidate, or seed."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import (
        RecurrentGateLaneInputV3,
        run_recurrent_gate_v3,
        verify_recurrent_gate_anchor_v3,
    )

    inputs = {
        "alakazam": RecurrentGateLaneInputV3(tmp_path / "alakazam.json", "a" * 64),
        "archaludon": RecurrentGateLaneInputV3(tmp_path / "archaludon.json", "b" * 64),
    }
    monkeypatch.setattr(
        recurrent_gate, "_load_lane_input_v3",
        lambda lane, _input: {"lane": lane, "manifest_sha256": "c" * 64 if lane == "alakazam" else "d" * 64},
    )
    command_identities: list[str] = []

    def fake_prepare(_path, **kwargs):
        command_identities.append(kwargs["command_identity"])
        return recurrent_gate.PreparedRecurrentLaneV3(
            tmp_path / "prepared" / "receipt.json", "e" * 64, kwargs["command_identity"],
        )

    monkeypatch.setattr(recurrent_gate, "prepare_sealed_recurrent_lane_v3", fake_prepare)
    calls: list[tuple[str, str, int]] = []

    def fake_cell(*, candidate, lane, seed, lane_input, manifest, prepared_lane, max_epochs, patience, min_delta, burn_in, device, checkpoint_dir):
        calls.append((candidate, lane, seed))
        cell = _cell(
            candidate, lane, seed,
            carry_complete=0.38 if candidate == "R3-A" and lane == "alakazam" else 0.40,
            reset_complete=0.40,
        )
        checkpoint = checkpoint_dir / cell["checkpoint"]["basename"]
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        state = {"weight": torch.tensor([float(seed)])}
        torch.save(state, checkpoint)
        cell["checkpoint"]["file_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        cell["checkpoint"]["state_sha256"] = recurrent_gate._state_sha256_v3(state)
        cell["checkpoint_sha256"] = cell["checkpoint"]["state_sha256"]
        return cell

    monkeypatch.setattr(recurrent_gate, "_run_recurrent_cell_v3", fake_cell)
    def fake_current(*, lane, seed, lane_input, manifest, prepared_lane, max_epochs, patience, min_delta, burn_in, device, checkpoint_dir):
        cell = _cell("current-R2", lane, seed)
        checkpoint = checkpoint_dir / cell["checkpoint"]["basename"]
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        state = {"weight": torch.tensor([float(seed)])}
        torch.save(state, checkpoint)
        cell["checkpoint"]["file_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        cell["checkpoint"]["state_sha256"] = recurrent_gate._state_sha256_v3(state)
        cell["checkpoint_sha256"] = cell["checkpoint"]["state_sha256"]
        return cell
    monkeypatch.setattr(
        recurrent_gate, "_run_current_r2_reference_cell_v3",
        fake_current,
    )
    result = run_recurrent_gate_v3(
        lane_inputs=inputs, max_epochs=4, patience=2, min_delta=0.0001,
        burn_in=1, device=torch.device("cpu"), output_dir=tmp_path,
    )

    assert len(calls) == 12
    assert command_identities == [
        _digest({
            "runner": "meta-specialist-recurrent-gate-v3", "device": "cpu",
            "max_epochs": 4, "patience": 2, "min_delta": 0.0001,
            "burn_in": 1, "learning_rate": 0.0001, "gradient_clip_norm": 1.0,
            "seeds": [7, 17, 29],
            "lane_manifest_file_sha256": {"alakazam": "a" * 64, "archaludon": "b" * 64},
        }),
    ] * 2
    assert set(calls) == {(candidate, lane, seed) for candidate in ("R3-A", "R3-B") for lane in ("alakazam", "archaludon") for seed in (7, 17, 29)}
    with pytest.raises(ValueError, match="research-only"):
        verify_recurrent_gate_anchor_v3(
            result.decision_path,
            expected_selection_file_sha256=hashlib.sha256(result.decision_path.read_bytes()).hexdigest(),
            expected_result_file_sha256=hashlib.sha256(result.output_path.read_bytes()).hexdigest(),
            expected_result_sha256=result.result_sha256,
        )
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert len(payload["cells"]) == 18
    assert payload["selection"]["status"] == "RESEARCH_ONLY"
    assert payload["selection"]["promotion_authority"] is False
    checkpoint = tmp_path / next(cell["checkpoint"]["path"] for cell in payload["cells"] if cell["candidate"] == "R3-A")
    torch.save({"weight": torch.tensor([999.0])}, checkpoint)
    with pytest.raises(ValueError, match="checkpoint external file SHA"):
        recurrent_gate._read_recurrent_gate_result_v3(result.output_path)


def test_cuda_anchor_rejects_a_self_rehashed_result_missing_peak_evidence(tmp_path: Path) -> None:
    """CUDA-labelled evidence without all twelve measured peaks is never selectable."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import verify_recurrent_gate_anchor_v3

    cells = _matrix()
    for cell in cells:
        cell["cuda_device_name"] = "Fixture CUDA"
        cell["cuda_peak_memory_bytes"] = 123
        cell["cuda_peak_memory_measured"] = True
        checkpoint = tmp_path / cell["checkpoint"]["path"]
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        state = {"weight": torch.tensor([float(cell["seed"])])}
        torch.save(state, checkpoint)
        cell["checkpoint"]["file_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        cell["checkpoint"]["state_sha256"] = recurrent_gate._state_sha256_v3(state)
        cell["checkpoint_sha256"] = cell["checkpoint"]["state_sha256"]
    result_path = tmp_path / "recurrent-gate-result-v3-cuda-0.json"
    result = {
        "schema": "meta-specialist-recurrent-gate-result-v2", "device": "cuda:0",
        "seeds": [7, 17, 29], "cells": cells,
        "selection": recurrent_gate._selection_for_device_v3(cells, device="cuda:0"),
    }
    result["result_sha256"] = _digest(result)
    result_path.write_bytes(_canonical(result))
    selection_path = tmp_path / "recurrent-gate-selection-v3-cuda-0.json"
    selection = {
        "schema": "meta-specialist-recurrent-gate-selection-v2", "result_path": result_path.name,
        "result_file_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "result_sha256": result["result_sha256"], "selection": result["selection"],
    }
    selection["selection_sha256"] = _digest(selection)
    selection_path.write_bytes(_canonical(selection))
    with pytest.raises(ValueError, match="runtime evidence"):
        verify_recurrent_gate_anchor_v3(
            selection_path,
            expected_selection_file_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest(),
            expected_result_file_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
            expected_result_sha256=result["result_sha256"],
        )

    del cells[-1]["cuda_peak_memory_bytes"]
    result["result_sha256"] = _digest({key: value for key, value in result.items() if key != "result_sha256"})
    result_path.write_bytes(_canonical(result))
    selection["result_file_sha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()
    selection["result_sha256"] = result["result_sha256"]
    selection["selection_sha256"] = _digest({key: value for key, value in selection.items() if key != "selection_sha256"})
    selection_path.write_bytes(_canonical(selection))

    with pytest.raises(ValueError, match="CUDA"):
        verify_recurrent_gate_anchor_v3(
            selection_path,
            expected_selection_file_sha256=hashlib.sha256(selection_path.read_bytes()).hexdigest(),
            expected_result_file_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
            expected_result_sha256=result["result_sha256"],
        )


def test_cuda_runner_failure_writes_blocked_cuda_artifact_without_cpu_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CUDA error remains CUDA evidence failure; it must not become a CPU success."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import RecurrentGateLaneInputV3, run_recurrent_gate_v3

    inputs = {
        "alakazam": RecurrentGateLaneInputV3(tmp_path / "alakazam.json", "a" * 64),
        "archaludon": RecurrentGateLaneInputV3(tmp_path / "archaludon.json", "b" * 64),
    }
    monkeypatch.setattr(recurrent_gate, "_load_lane_input_v3", lambda lane, _input: {"lane": lane, "manifest_sha256": "c" * 64})
    monkeypatch.setattr(
        recurrent_gate, "prepare_sealed_recurrent_lane_v3",
        lambda _path, **_kwargs: recurrent_gate.PreparedRecurrentLaneV3(
            tmp_path / "prepared" / "receipt.json", "e" * 64, _kwargs["command_identity"],
        ),
    )
    monkeypatch.setattr(recurrent_gate, "_run_recurrent_cell_v3", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("CUDA unavailable")))

    result = run_recurrent_gate_v3(
        lane_inputs=inputs, max_epochs=4, patience=2, min_delta=0.0001,
        burn_in=1, device=torch.device("cuda:0"), output_dir=tmp_path,
    )

    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert result.status == "BLOCKED"
    assert payload["device"] == "cuda:0"
    assert payload["selection"]["status"] == "BLOCKED"


def test_recurrent_gate_cli_requires_two_explicit_anchored_lanes() -> None:
    """A single unanchored recurrent corpus cannot be mistaken for the two-lane Gate."""
    completed = subprocess.run(
        [sys.executable, "scripts/run_meta_specialist_v3_recurrent_gate.py", "--help"],
        cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, check=False,
    )

    assert completed.returncode == 0
    assert "--lane-input" in completed.stdout


def _state(index: int, *, candidates: int) -> RelationalStateV3:
    return RelationalStateV3(
        (0.0,) * 41,
        (EntityTokenV3(1, 1, 1, 1, 10 + index, None, (0.0,), (1,), ()),),
        tuple(
            ActionCandidateV3(f"a-{index}-{item}", item, 1, None, (), (), 0)
            for item in range(candidates)
        ),
    )


def _step(index: int, *, stop_mass: float = 0.0, forced_stop: bool = False) -> BCExampleV3:
    candidate_count = 0 if forced_stop else 1
    masses = (1.0,) if forced_stop else (1.0 - stop_mass, stop_mass)
    return BCExampleV3(
        state=_state(index, candidates=candidate_count),
        target_index=0,
        episode_group="episode-a",
        model_input=object(),
        step_input=SimpleNamespace(stop_available=True),
        target_masses=masses,
        episode_start=index == 0,
        component_id="component-a",
        partition="validation",
    )


def _sequence(
    *steps: BCExampleV3, partition: str = "validation", episode_id: str = "episode-a",
    component_id: str = "component-a",
) -> RecurrentBCSequenceV3:
    adjusted = tuple(
        BCExampleV3(
            state=step.state, target_index=step.target_index, episode_group=episode_id,
            quality_weight=step.quality_weight, model_input=step.model_input, step_input=step.step_input,
            target_masses=step.target_masses, episode_start=index == 0,
            component_id=component_id, partition=partition,
        )
        for index, step in enumerate(steps)
    )
    return RecurrentBCSequenceV3("alakazam", episode_id, component_id, partition, adjusted, 0)


class _TinyCurrentR2(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.semantic_logit = nn.Parameter(torch.tensor(0.0))
        self.stop_logit = nn.Parameter(torch.tensor(0.0))

    def step_logits(self, _model_input: object, _step_input: object) -> tuple[torch.Tensor, torch.Tensor]:
        return self.semantic_logit.reshape(1), self.stop_logit


def test_current_r2_uses_real_updates_and_independent_best_validation_checkpoint() -> None:
    """The baseline arm must be trained under the same sealed epoch/seed budget as R3."""
    from mage_ptcg.meta_specialist.recurrent_gate_v3 import _train_current_r2_v3

    train = _sequence(
        _step(0, stop_mass=0.2), partition="train", episode_id="train-episode", component_id="train-component",
    )
    validation = _sequence(
        _step(0, stop_mass=0.2), partition="validation", episode_id="valid-episode", component_id="valid-component",
    )
    model = _TinyCurrentR2()

    result = _train_current_r2_v3(
        model, (train,), (validation,), device=torch.device("cpu"), max_epochs=4,
        patience=2, min_delta=10.0, learning_rate=0.1,
    )

    assert result.candidate == "current-R2"
    assert result.optimizer_updates >= 1
    assert result.parameter_delta_l1 > 0.0
    assert result.best_epoch == 0
    assert result.checkpoint_state


class _LargeGradientCurrentR2(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.logit = nn.Parameter(torch.tensor(0.0))

    def step_logits(self, _model_input: object, _step_input: object) -> tuple[torch.Tensor, torch.Tensor]:
        return (-1_000_000.0 * self.logit).reshape(1), (1_000_000.0 * self.logit).reshape(1)


def test_current_r2_clips_a_large_gradient_before_every_optimizer_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The static baseline must use the same 1.0 gradient norm as recurrent candidates."""
    train = _sequence(
        _step(0, stop_mass=0.2), partition="train",
        episode_id="train-episode", component_id="train-component",
    )
    validation = _sequence(
        _step(0, stop_mass=0.2), partition="validation",
        episode_id="valid-episode", component_id="valid-component",
    )
    observed_step_norms: list[float] = []
    original_step = torch.optim.Adam.step

    def step_after_recording_norm(optimizer, *args, **kwargs):
        squared = torch.tensor(0.0)
        for group in optimizer.param_groups:
            for parameter in group["params"]:
                if parameter.grad is not None:
                    squared += parameter.grad.detach().float().pow(2).sum().cpu()
        observed_step_norms.append(float(squared.sqrt().item()))
        return original_step(optimizer, *args, **kwargs)

    monkeypatch.setattr(torch.optim.Adam, "step", step_after_recording_norm)

    recurrent_gate._train_current_r2_v3(
        _LargeGradientCurrentR2(), (train,), (validation,), device=torch.device("cpu"),
        max_epochs=1, patience=1, min_delta=0.0, learning_rate=0.1,
    )

    assert observed_step_norms
    assert max(observed_step_norms) <= 1.000001


class _SpyRecurrentR3(nn.Module):
    """Minimal real differentiable forward contract that records hidden use."""

    def __init__(self) -> None:
        super().__init__()
        self.logit = nn.Parameter(torch.tensor(0.0))
        self.stop_vector = nn.Parameter(torch.tensor([0.0]))
        self.stop_bias = nn.Parameter(torch.tensor(0.0))
        self.calls: list[tuple[bool, bool]] = []

    def forward_v3(
        self, state: RelationalStateV3, *, hidden_state: torch.Tensor | None = None,
        episode_start: bool = False,
    ) -> PolicyOutputV3:
        self.calls.append((hidden_state is not None, episode_start))
        hidden = self.logit.reshape(1, 1, 1) if episode_start or hidden_state is None else hidden_state + 1.0
        logits = self.logit.reshape(1).expand(len(state.candidates))
        return PolicyOutputV3(logits=logits, global_token=self.logit.reshape(1), hidden_state=hidden)


def test_carry_evaluation_preserves_hidden_only_within_episode() -> None:
    """Resetting every step would make the carry branch's second input None."""
    model = _SpyRecurrentR3()
    sequence = _sequence(_step(0, stop_mass=0.25), _step(1, stop_mass=0.25))

    metrics = evaluate_carry_vs_reset_v3(model, (sequence,), device=torch.device("cpu"))

    assert model.calls[:2] == [(False, True), (True, False)]
    assert model.calls[2:] == [(False, True), (False, True)]
    assert metrics.non_reset_hidden_steps == 1


def test_complete_soft_target_counts_stop_mass_but_excludes_forced_sole_stop() -> None:
    """Including forced STOP would create a zero-information loss row."""
    model = _SpyRecurrentR3()
    sequence = _sequence(_step(0, stop_mass=0.25), _step(1, forced_stop=True))

    metrics = evaluate_carry_vs_reset_v3(model, (sequence,), device=torch.device("cpu"))

    assert metrics.carry_stop_nll == pytest.approx(-0.25 * torch.log(torch.tensor(0.5)).item())
    assert metrics.carry_complete_nll == pytest.approx(torch.log(torch.tensor(2.0)).item())
    assert metrics.forced_sole_stop_rows == 1


def test_training_records_best_epoch_history_and_real_parameter_delta() -> None:
    """A no-op optimizer or missing early-stop bookkeeping would fail this."""
    train = _sequence(
        _step(0, stop_mass=0.25), _step(1, stop_mass=0.25), partition="train",
        episode_id="train-episode", component_id="train-component",
    )
    validation = _sequence(
        _step(2, stop_mass=0.25), partition="validation",
        episode_id="validation-episode", component_id="validation-component",
    )
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=4)

    result = train_recurrent_r3_v3(
        model, (train,), (validation,), candidate="R3-A", device=torch.device("cpu"),
        max_epochs=4, patience=2, min_delta=10.0, learning_rate=0.1,
    )

    assert result.parameter_delta_l1 > 0
    assert result.stop_reason == "patience"
    assert len(result.history) == result.epochs
    assert result.checkpoint_state


def test_training_rejects_a_non_r3_model_even_when_it_implements_forward_contract() -> None:
    """Letting a static lookalike through would make the recurrent θ0 claim false."""
    train = _sequence(
        _step(0, stop_mass=0.25), partition="train", episode_id="train-episode", component_id="train-component",
    )
    validation = _sequence(
        _step(1, stop_mass=0.25), partition="validation",
        episode_id="validation-episode", component_id="validation-component",
    )

    with pytest.raises(ValueError, match="SpecialistModelV3"):
        train_recurrent_r3_v3(
            _SpyRecurrentR3(), (train,), (validation,), candidate="R3-A",
            device=torch.device("cpu"), max_epochs=1,
        )


def test_reopenable_stream_is_revalidated_for_each_carry_and_reset_pass() -> None:
    """A single consumed iterator could silently skip the reset ablation."""
    sequence = _sequence(_step(0, stop_mass=0.25), _step(1, stop_mass=0.25))
    opens = 0

    def stream_factory():
        nonlocal opens
        opens += 1
        yield sequence

    source = RecurrentSequenceSourceV3(stream_factory, "sealed-test-source", True)
    evaluate_carry_vs_reset_v3(_SpyRecurrentR3(), source, device=torch.device("cpu"))

    assert opens == 2


def test_training_rejects_a_validation_sequence_in_the_training_stream() -> None:
    """Crossing the sealed component split would turn validation into training data."""
    validation = _sequence(_step(0, stop_mass=0.25), partition="validation")
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=5)

    with pytest.raises(ValueError, match="partition"):
        train_recurrent_r3_v3(
            model, (validation,), (validation,), candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1,
        )


def test_production_run_rejects_an_eager_fixture_tuple() -> None:
    """A full lane run must require a revalidating stream, never a corpus tuple."""
    train = _sequence(_step(0, stop_mass=0.25), partition="train")
    validation = _sequence(_step(1, stop_mass=0.25), partition="validation")
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=6)

    with pytest.raises(ValueError, match="production"):
        train_recurrent_r3_v3(
            model, (train,), (validation,), candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1, production_run=True,
        )


def test_production_run_rejects_a_caller_marked_generic_stream_until_task4_adapter_exists() -> None:
    """A caller-controlled boolean cannot prove manifest/index revalidation."""
    train = _sequence(_step(0, stop_mass=0.25), partition="train")
    validation = _sequence(_step(1, stop_mass=0.25), partition="validation")
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=7)
    train_source = RecurrentSequenceSourceV3(lambda: iter((train,)), "caller-marked", True)
    validation_source = RecurrentSequenceSourceV3(lambda: iter((validation,)), "caller-marked", True)

    with pytest.raises(ValueError, match="Task 4|production"):
        train_recurrent_r3_v3(
            model, train_source, validation_source, candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1, production_run=True,
        )


def test_production_run_rejects_task35_manifest_source_without_external_receipt(tmp_path) -> None:
    """A manifest anchor alone no longer authorizes the scalable production path."""
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=70)
    train_source = sealed_recurrent_sequence_source_v3(
        tmp_path / "selection.json", expected_manifest_file_sha256="d" * 64, burn_in=0, partition="train",
    )
    validation_source = sealed_recurrent_sequence_source_v3(
        tmp_path / "selection.json", expected_manifest_file_sha256="d" * 64, burn_in=0, partition="validation",
    )

    with pytest.raises(ValueError, match="preflight receipt"):
        train_recurrent_r3_v3(
            model, train_source, validation_source, candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1, production_run=True,
        )


def test_prepared_source_reopens_the_pinned_stream_for_each_production_pass(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the manifest-bound adapter may enable production execution."""
    train = _sequence(_step(0, stop_mass=0.25), partition="train", episode_id="train", component_id="train")
    validation = _sequence(_step(1, stop_mass=0.25), partition="validation", episode_id="validation", component_id="validation")
    calls: list[tuple[object, str, int, str]] = []

    def fake_stream(path, *, expected_receipt_file_sha256, burn_in, partition):
        calls.append((path, expected_receipt_file_sha256, burn_in, partition))
        yield train if partition == "train" else validation

    monkeypatch.setattr(recurrent_gate, "stream_prepared_recurrent_selection_v3", fake_stream)
    monkeypatch.setattr(recurrent_gate, "validate_prepared_recurrent_pair_v3", lambda *_args, **_kwargs: None)
    expected = "a" * 64
    train_source = prepared_recurrent_sequence_source_v3(
        tmp_path / "receipt.json", expected_receipt_file_sha256=expected, burn_in=0, partition="train",
    )
    validation_source = prepared_recurrent_sequence_source_v3(
        tmp_path / "receipt.json", expected_receipt_file_sha256=expected, burn_in=0, partition="validation",
    )
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=10)

    result = train_recurrent_r3_v3(
        model, train_source, validation_source, candidate="R3-A", device=torch.device("cpu"),
        max_epochs=1, production_run=True,
    )

    assert result.optimizer_updates == 1
    # Receipt validation replaces the old train+validation full-corpus audit;
    # only the epoch's train and validation passes reopen the raw stream.
    assert [call[-1] for call in calls] == ["train", "validation"]


def test_production_sealed_adapter_rejects_split_overlap_before_optimizer_update(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production adapter must preserve the pre-update split-leak gate."""
    train = _sequence(_step(0, stop_mass=0.25), partition="train", episode_id="train", component_id="shared")
    validation = _sequence(_step(1, stop_mass=0.25), partition="validation", episode_id="validation", component_id="shared")

    def fake_stream(_path, *, expected_receipt_file_sha256, burn_in, partition):
        assert expected_receipt_file_sha256 == "b" * 64
        assert burn_in == 0
        yield train if partition == "train" else validation

    monkeypatch.setattr(recurrent_gate, "stream_prepared_recurrent_selection_v3", fake_stream)
    monkeypatch.setattr(
        recurrent_gate, "validate_prepared_recurrent_pair_v3",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("prepared split overlap")),
    )
    train_source = prepared_recurrent_sequence_source_v3(
        tmp_path / "receipt.json", expected_receipt_file_sha256="b" * 64, burn_in=0, partition="train",
    )
    validation_source = prepared_recurrent_sequence_source_v3(
        tmp_path / "receipt.json", expected_receipt_file_sha256="b" * 64, burn_in=0, partition="validation",
    )
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=11)
    before = {name: value.detach().clone() for name, value in model.state_dict().items()}

    with pytest.raises(ValueError, match="overlap"):
        train_recurrent_r3_v3(
            model, train_source, validation_source, candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1, production_run=True,
        )

    assert all(torch.equal(before[name], value) for name, value in model.state_dict().items())


def test_production_sealed_adapter_rejects_partition_mismatch_before_optimizer_update(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream returning the wrong sealed partition cannot reach the optimizer."""
    wrong = _sequence(_step(0, stop_mass=0.25), partition="validation", component_id="wrong")

    def fake_stream(_path, *, expected_receipt_file_sha256, burn_in, partition):
        yield wrong

    monkeypatch.setattr(recurrent_gate, "stream_prepared_recurrent_selection_v3", fake_stream)
    monkeypatch.setattr(recurrent_gate, "validate_prepared_recurrent_pair_v3", lambda *_args, **_kwargs: None)
    train_source = prepared_recurrent_sequence_source_v3(
        tmp_path / "receipt.json", expected_receipt_file_sha256="c" * 64, burn_in=0, partition="train",
    )
    validation_source = prepared_recurrent_sequence_source_v3(
        tmp_path / "receipt.json", expected_receipt_file_sha256="c" * 64, burn_in=0, partition="validation",
    )
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=12)
    before = {name: value.detach().clone() for name, value in model.state_dict().items()}

    with pytest.raises(ValueError, match="pinned partition"):
        train_recurrent_r3_v3(
            model, train_source, validation_source, candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1, production_run=True,
        )

    assert all(torch.equal(before[name], value) for name, value in model.state_dict().items())


def test_training_rejects_component_overlap_before_any_optimizer_update() -> None:
    """A shared component must fail before its training loss can update θ."""
    train = _sequence(_step(0, stop_mass=0.25), partition="train", component_id="shared")
    validation = _sequence(
        _step(1, stop_mass=0.25), partition="validation", episode_id="other-episode", component_id="shared",
    )
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=8)
    before = {name: value.detach().clone() for name, value in model.state_dict().items()}

    with pytest.raises(ValueError, match="overlap"):
        train_recurrent_r3_v3(
            model, (train,), (validation,), candidate="R3-A", device=torch.device("cpu"), max_epochs=1,
        )

    assert all(torch.equal(before[name], value) for name, value in model.state_dict().items())


def test_training_rejects_a_best_checkpoint_without_any_parameter_delta() -> None:
    """An underflowed optimizer step cannot be evidence of a learned recurrent θ0."""
    train = _sequence(
        _step(0, stop_mass=0.25), partition="train", episode_id="train-episode", component_id="train-component",
    )
    validation = _sequence(
        _step(1, stop_mass=0.25), partition="validation",
        episode_id="validation-episode", component_id="validation-component",
    )
    model = SpecialistModelV3(card_vocabulary_size=64, hidden_dim=16, embedding_dim=16, seed=9)

    with pytest.raises(RuntimeError, match="parameter delta"):
        train_recurrent_r3_v3(
            model, (train,), (validation,), candidate="R3-A", device=torch.device("cpu"),
            max_epochs=1, learning_rate=1e-46,
        )
