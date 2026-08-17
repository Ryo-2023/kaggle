"""Contracts for the restart-safe, monitored V4 long-run wrapper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

torch = pytest.importorskip("torch")


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_meta_specialist_v4_longrun.py"
    spec = importlib.util.spec_from_file_location("run_meta_specialist_v4_longrun", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lane(tmp_path: Path) -> dict[str, object]:
    selection = tmp_path / "archaludon-selection.json"
    selection.write_text(json.dumps({"lane": "archaludon"}), encoding="utf-8")
    deck = tmp_path / "archaludon.csv"
    deck.write_text("deck\n", encoding="utf-8")
    return {
        "lane": "archaludon",
        "selection_manifest": selection,
        "selection_manifest_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
        "subject_deck_csv": deck,
        "subject_archetype_id": "archaludon",
    }


def test_longrun_writes_a_sealed_pending_state_before_launch(tmp_path: Path) -> None:
    """Breaks if a long run can start without a reproducible config/state artifact."""
    runner = _load_runner()
    lane = _lane(tmp_path)
    config = runner.LongrunConfigV4(
        lane=lane, output_root=tmp_path / "longrun", python="python", max_records=32768,
        episodes_per_partition=64, components_per_partition=64, epochs=16, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
    )

    runner.initialize_longrun_v4(config)

    manifest = json.loads((config.output_root / "run-manifest.json").read_text(encoding="utf-8"))
    progress = json.loads((config.output_root / "progress_summary.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == runner.LONGRUN_SCHEMA
    assert manifest["status"] == "pending"
    assert manifest["config_sha256"] == runner.config_sha256_v4(config)
    assert progress["status"] == "pending"
    assert progress["stage"] == "training"
    assert progress["restart_contract"] == "epoch_boundary_optimizer_resume_only"


def test_longrun_refuses_to_reuse_a_different_config(tmp_path: Path) -> None:
    """Breaks if --resume can silently mix optimizer budgets or dataset coverage."""
    runner = _load_runner()
    lane = _lane(tmp_path)
    first = runner.LongrunConfigV4(
        lane=lane, output_root=tmp_path / "longrun", python="python", max_records=32768,
        episodes_per_partition=64, components_per_partition=64, epochs=16, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
    )
    runner.initialize_longrun_v4(first)
    incompatible = runner.LongrunConfigV4(
        lane=lane, output_root=first.output_root, python="python", max_records=32768,
        episodes_per_partition=64, components_per_partition=64, epochs=17, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
    )

    with pytest.raises(runner.LongrunError, match="config SHA-256"):
        runner.initialize_longrun_v4(incompatible)


def test_manifest_state_update_never_rebases_changed_subject_deck(tmp_path: Path) -> None:
    runner = _load_runner()
    lane = _lane(tmp_path)
    config = runner.LongrunConfigV4(
        lane=lane, output_root=tmp_path / "longrun", python="python", max_records=32,
        episodes_per_partition=4, components_per_partition=4, epochs=1, patience=0,
        seeds=(0, 1), hidden_dim=16, embedding_dim=12, tbptt_steps=1,
        games_per_seat=1, base_seed=1, max_steps=10,
    )
    runner.initialize_longrun_v4(config)
    before = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    Path(lane["subject_deck_csv"]).write_text("changed\n", encoding="utf-8")
    with pytest.raises(runner.LongrunError, match="initially sealed"):
        runner._update_manifest(config, status="running")
    after = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    assert after == before


def test_longrun_rejects_a_selection_file_whose_bytes_do_not_match_its_identity(tmp_path: Path) -> None:
    """Breaks if a fixed manifest path can drift after the longrun was planned."""
    runner = _load_runner()
    lane = _lane(tmp_path)
    Path(lane["selection_manifest"]).write_text('{"lane":"changed"}', encoding="utf-8")
    config = runner.LongrunConfigV4(
        lane=lane, output_root=tmp_path / "longrun", python="python", max_records=32768,
        episodes_per_partition=64, components_per_partition=64, epochs=16, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
    )

    with pytest.raises(runner.LongrunError, match="selection manifest SHA-256 changed"):
        runner.initialize_longrun_v4(config)


def test_longrun_marks_interrupted_training_as_epoch_boundary_resumable(tmp_path: Path) -> None:
    """Breaks if the wrapper loses the documented Adam/epoch resume contract."""
    runner = _load_runner()
    lane = _lane(tmp_path)
    config = runner.LongrunConfigV4(
        lane=lane, output_root=tmp_path / "longrun", python="python", max_records=32768,
        episodes_per_partition=64, components_per_partition=64, epochs=16, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
    )
    runner.initialize_longrun_v4(config)

    runner.mark_interrupted_v4(config, stage="training", returncode=-15)

    manifest = json.loads((config.output_root / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted_epoch_boundary_resumable"
    assert manifest["restart_contract"] == "epoch_boundary_optimizer_resume_only"
    runner.require_startable_v4(config, restart_interrupted=False)
    runner.require_startable_v4(config, restart_interrupted=True)


def test_longrun_training_command_carries_the_sealed_budget(tmp_path: Path) -> None:
    """Breaks if a long-run manifest and its child command can diverge."""
    runner = _load_runner()
    config = runner.LongrunConfigV4(
        lane=_lane(tmp_path), output_root=tmp_path / "longrun", python="python3", max_records=32768,
        episodes_per_partition=64, components_per_partition=64, epochs=16, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
    )

    command = runner.training_command_v4(config)

    assert command[0] == "python3"
    assert command[command.index("--episodes-per-partition") + 1] == "64"
    assert command[command.index("--components-per-partition") + 1] == "64"
    assert command[command.index("--epochs") + 1] == "16"
    assert command[command.index("--seeds") + 1] == "0,1"
    assert command[command.index("--output") + 1] == str(config.training_output)
    assert "--resume" in command


def test_longrun_accepts_asymmetric_512_train_128_validation_coverage(tmp_path: Path) -> None:
    runner = _load_runner()
    config = runner.LongrunConfigV4(
        lane=_lane(tmp_path), output_root=tmp_path / "longrun", python="python3", max_records=131072,
        episodes_per_partition=512, components_per_partition=512, epochs=3, patience=2,
        seeds=(0, 1), hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=8, base_seed=9_800_000, max_steps=2000,
        validation_episodes_per_partition=128, validation_components_per_partition=128,
    )
    runner.initialize_longrun_v4(config)
    command = runner.training_command_v4(config)
    assert command[command.index("--train-episodes-per-partition") + 1] == "512"
    assert command[command.index("--validation-episodes-per-partition") + 1] == "128"


def test_longrun_recovers_a_stale_running_training_pid_as_epoch_boundary_resumable(tmp_path: Path, monkeypatch) -> None:
    runner = _load_runner()
    config = runner.LongrunConfigV4(
        lane=_lane(tmp_path), output_root=tmp_path / "longrun", python="python", max_records=32,
        episodes_per_partition=4, components_per_partition=4, epochs=1, patience=0, seeds=(0, 1),
        hidden_dim=16, embedding_dim=12, tbptt_steps=1, games_per_seat=1, base_seed=1, max_steps=10,
    )
    runner.initialize_longrun_v4(config)
    runner._update_manifest(config, status="running", stage="training", pid=987654321)
    monkeypatch.setattr(runner, "_pid_alive", lambda _pid: False)
    runner.require_startable_v4(config, restart_interrupted=False)
    manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted_epoch_boundary_resumable"


def test_longrun_rejects_live_running_pid(tmp_path: Path, monkeypatch) -> None:
    runner = _load_runner()
    config = runner.LongrunConfigV4(
        lane=_lane(tmp_path), output_root=tmp_path / "longrun", python="python", max_records=32,
        episodes_per_partition=4, components_per_partition=4, epochs=1, patience=0, seeds=(0, 1),
        hidden_dim=16, embedding_dim=12, tbptt_steps=1, games_per_seat=1, base_seed=1, max_steps=10,
    )
    runner.initialize_longrun_v4(config)
    command = ["python", "trainer.py"]
    runner._update_manifest(
        config, status="running", stage="training", pid=123, command=command,
        command_sha256=runner._command_sha256(command), process_start_identity="a" * 64,
    )
    monkeypatch.setattr(runner, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(runner, "_process_start_identity", lambda _pid: "a" * 64)
    with pytest.raises(runner.LongrunError, match="already running"):
        runner.require_startable_v4(config, restart_interrupted=False)


@pytest.mark.parametrize("field,bad", [
    ("base_seed", 2), ("max_steps", 11), ("subject_archetype_id", "wrong"),
    ("opponent_ids", ["wrong"] * 6), ("fixed_held_out_opponent_ids", ["wrong"] * 6),
    ("evaluation_implementation_sha256", "f" * 64),
])
def test_longrun_rejects_wrong_heldout_protocol_field(tmp_path: Path, monkeypatch, field: str, bad: object) -> None:
    runner = _load_runner()
    config = runner.LongrunConfigV4(
        lane=_lane(tmp_path), output_root=tmp_path / "longrun", python="python", max_records=32,
        episodes_per_partition=4, components_per_partition=4, epochs=1, patience=0, seeds=(0, 1),
        hidden_dim=16, embedding_dim=12, tbptt_steps=1, games_per_seat=1, base_seed=1, max_steps=10,
    )
    class Opponent:
        canonical_deck_hash = "d" * 64
        policy_hash = "p" * 64
        deck_csv_path = str(Path(config.lane["subject_deck_csv"]))
    monkeypatch.setattr(runner, "load_opponent_pool_v1", lambda _root: {})
    monkeypatch.setattr(runner, "resolve_opponent_v1", lambda *_args, **_kwargs: Opponent())
    checkpoint = {"path": str(tmp_path / "model.pt"), "file_sha256": "a" * 64, "tensor_state_sha256": "b" * 64}
    ids = list(runner.EVAL_HELD_OUT_V1)
    deck = Path(config.lane["subject_deck_csv"]).resolve()
    report = {
        "schema_version": runner.EVALUATION_SCHEMA, "checkpoint": checkpoint,
        "subject_archetype_id": "archaludon", "subject_deck_csv": str(deck),
        "subject_deck_file_sha256": hashlib.sha256(deck.read_bytes()).hexdigest(),
        "fixed_held_out_opponent_ids": ids, "opponent_ids": ids,
        "opponent_fingerprints": [{
            "opponent_id": item, "canonical_deck_hash": "d" * 64,
            "deck_file_sha256": hashlib.sha256(deck.read_bytes()).hexdigest(), "policy_hash": "p" * 64,
        } for item in ids],
        "evaluation_implementation_sha256": runner.evaluation_implementation_sha256_v1(),
        "games_per_seat": 1, "base_seed": 1, "max_steps": 10, "requested_games": 12,
        "faults": 0, "comparison_status": "valid",
    }
    report[field] = bad
    output = tmp_path / "evaluation.json"
    output.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(runner.LongrunError, match="held-out evaluation"):
        runner.validate_evaluation_report_v4(config, output, checkpoint)


def test_evaluation_aggregate_validator_rejects_impossible_wdlf() -> None:
    runner = _load_runner()
    ids = list(runner.EVAL_HELD_OUT_V1)
    report = {
        "wins": 0, "draws": 12, "losses": 0, "faults": 0, "games_played": 12,
        "score_denominator_games": 12, "score_rate": 0.5,
        "score_ci95": list(runner._wilson(6.0, 12)),
        "seat": {str(seat): {"w": 0, "d": 6, "l": 0, "f": 0, "requested": 6, "score_rate": 0.5} for seat in (0, 1)},
        "per_opponent": {item: {"w": 0, "d": 2, "l": 0, "f": 0, "requested": 2, "score_rate": 0.5} for item in ids},
    }
    runner._validate_evaluation_aggregates_v4(report, expected_ids=ids, games_per_seat=1)
    report["draws"] = -1
    with pytest.raises(runner.LongrunError, match="WDLF"):
        runner._validate_evaluation_aggregates_v4(report, expected_ids=ids, games_per_seat=1)


def test_completed_updates_require_real_finite_adam_moments() -> None:
    runner = _load_runner()
    model = runner.SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=3)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    with pytest.raises(runner.LongrunError, match="nonempty Adam"):
        runner._validate_adam_state_v4(optimizer.state_dict(), model=model, optimizer_updates=1)
    optimizer.zero_grad()
    sum(parameter.sum() for parameter in model.parameters()).backward()
    optimizer.step()
    runner._validate_adam_state_v4(optimizer.state_dict(), model=model, optimizer_updates=1)


def test_failed_child_closes_progress_as_failed_and_keeps_bounded_stderr(tmp_path: Path, monkeypatch) -> None:
    runner = _load_runner()
    config = runner.LongrunConfigV4(
        lane=_lane(tmp_path), output_root=tmp_path / "longrun", python=sys.executable, max_records=32,
        episodes_per_partition=4, components_per_partition=4, epochs=1, patience=0, seeds=(0, 1),
        hidden_dim=16, embedding_dim=12, tbptt_steps=1, games_per_seat=1, base_seed=1, max_steps=10,
    )
    runner.initialize_longrun_v4(config)
    events: list[tuple[str, object]] = []
    class Reporter:
        def __init__(self, **_kwargs):
            pass
        def update(self, advance=1, **fields):
            events.append(("update", (advance, fields)))
        def close(self, *, status="done"):
            events.append(("close", status))
    monkeypatch.setattr(runner, "ProgressReporterV1", Reporter)
    command = [sys.executable, "-c", "import sys; sys.stderr.write('x' * 70000); raise SystemExit(3)"]
    with pytest.raises(runner.LongrunError, match="return code 3"):
        runner._run_child(config, stage="training", command=command, heartbeat_seconds=0.01)
    assert ("close", "failed") in events
    assert not any(event[0] == "update" and event[1][1].get("status") == "complete" for event in events)
    diagnostic = json.loads((config.output_root / "training-failure.json").read_text(encoding="utf-8"))
    assert diagnostic["stderr_truncated"] is True
    assert len(diagnostic["stderr_excerpt"].encode()) <= 65536
    assert json.loads(config.progress_path.read_text(encoding="utf-8"))["status"] == "failed"
