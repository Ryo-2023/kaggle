from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
import pytest

from tests.meta_specialist.test_dagger_v4 import _TeacherFactory, _two_choice_forced_stop_transition

from scripts import run_meta_specialist_v4_dagger_bc as runner


_STRICT_SCREEN_GAMES = (
    ("target-a-0", "9fe94c9cdcc79ecbce87b9209076ef21b24abf354dd01cb9a36ccd7b794fa78a", "target_a", 1, 101, "train"),
    ("target-b-8", "37eca5e2c0efcc6ebea7a411307e56884e3d5b24d3a8c7c4da9b49f904dee2b4", "target_b", 1, 102, "validation"),
    ("other-0", "ffadd49e3de88f2c4f2479fac01dadf981968088386add92254a4e9ce3f505e3", "other", 1, 103, "train"),
    ("target-a-3", "09722db26cd3e08902c84419001b79ae1355988acdeef04df07ef1635cede734", "target_a", 0, 104, "validation"),
)


def _paired_seed_binding(tmp_path: Path, *, seed: int, suffix: str) -> dict[str, object]:
    """Return an intentionally distinct, closed provenance tuple for one seed."""
    return {
        "seed": seed,
        "screen": {
            "path": str(tmp_path / f"screen-{suffix}.json"),
            "file_sha256": ("a" if seed == 0 else "b") * 64,
        },
        "transitions": {
            "path": str(tmp_path / f"screen-{suffix}.jsonl"),
            "file_sha256": ("c" if seed == 0 else "d") * 64,
        },
        "init_checkpoint": {
            "path": str(tmp_path / f"checkpoint-{suffix}.pt"),
            "file_sha256": ("e" if seed == 0 else "f") * 64,
            "tensor_state_sha256": ("1" if seed == 0 else "2") * 64,
        },
    }


def _write_paired_seed_manifest(tmp_path: Path, bindings: list[dict[str, object]]) -> tuple[Path, str]:
    payload = {
        "schema": "meta-specialist-v4-dagger-paired-seed-manifest-v1",
        "lane": "archaludon",
        "seed_provenance": bindings,
    }
    path = tmp_path / "paired-seed-manifest.json"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _strict_screen_and_rows() -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    transition, _ = _two_choice_forced_stop_transition()
    games = []
    rows = []
    for job_id, game_id, opponent_id, seat, env_seed, partition in _STRICT_SCREEN_GAMES:
        games.append({
            "job_id": job_id,
            "opponent_id": opponent_id,
            "seat": seat,
            "env_seed": env_seed,
            "status": "completed",
            "transitions": 2,
            "fault": None,
        })
        for transition_index in range(2):
            rows.append({
                "schema": runner.TRANSITION_SCHEMA_V4,
                "game_id": game_id,
                "episode_group": game_id,
                "component_id": game_id,
                "partition": partition,
                "opponent_id": opponent_id,
                "seat": seat,
                "env_seed": env_seed,
                "transition_index": transition_index,
                "transition": transition.to_dict(),
            })
    screen = {
        "schema": runner.SCREEN_SCHEMA_V4,
        "status": "VALID",
        "games_requested": len(games),
        "games_completed": len(games),
        "faults": 0,
        "transition_records": len(rows),
        "games": games,
    }
    return screen, tuple(rows)


def test_build_dagger_sequences_relabels_and_merges_game(monkeypatch) -> None:
    transition, _ = _two_choice_forced_stop_transition()
    monkeypatch.setattr(
        runner, "build_rule_agent_policy_factory_v1",
        lambda: (_TeacherFactory(), "a" * 64),
    )
    rows = []
    for game_id, partition, component in (("b" * 64, "train", "c" * 64), ("d" * 64, "validation", "e" * 64)):
        for index in range(2):
            rows.append({
                "schema": runner.TRANSITION_SCHEMA_V4,
                "game_id": game_id,
                "episode_group": game_id,
                "component_id": component,
                "partition": partition,
                "opponent_id": "opponent",
                "seat": 0,
                "env_seed": 100,
                "transition_index": index,
                "transition": transition.to_dict(),
            })
    result = runner.build_dagger_sequences_v4(rows, lane="archaludon")
    assert len(result) == 2
    assert all(len(row.steps) == len(transition.prefix_steps) * 2 for row in result)
    assert all(sum(step.episode_start for step in row.steps) == 1 for row in result)


def test_build_dagger_sequences_with_strict_disagreement_returns_complete_game_report(monkeypatch) -> None:
    transition, _ = _two_choice_forced_stop_transition()
    monkeypatch.setattr(
        runner, "build_rule_agent_policy_factory_v1",
        lambda: (_TeacherFactory(), "a" * 64),
    )
    rows = []
    for index in range(2):
        rows.append({
            "schema": runner.TRANSITION_SCHEMA_V4,
            "game_id": "b" * 64,
            "episode_group": "b" * 64,
            "component_id": "c" * 64,
            "partition": "train",
            "opponent_id": "target",
            "seat": 1,
            "env_seed": 100,
            "transition_index": index,
            "transition": transition.to_dict(),
        })

    result, report = runner.build_dagger_sequences_with_strict_disagreement_v4(
        rows,
        lane="archaludon",
        focus_action_types=(3,),
        max_mean_behavior_log_probability=-0.2,
    )

    assert len(result) == 1
    assert report["selected_components"] == ["c" * 64]
    assert report["selected_episode_count"] == 1
    assert report["effective_loss_mass"] > 0.0


def test_strict_disagreement_marks_only_mismatched_prefixes_as_supervised(monkeypatch) -> None:
    transition, _ = _two_choice_forced_stop_transition()
    monkeypatch.setattr(
        runner, "build_rule_agent_policy_factory_v1",
        lambda: (_TeacherFactory(), "a" * 64),
    )
    rows = [{
        "schema": runner.TRANSITION_SCHEMA_V4,
        "game_id": "b" * 64,
        "episode_group": "b" * 64,
        "component_id": "c" * 64,
        "partition": "train",
        "opponent_id": "target",
        "seat": 1,
        "env_seed": 100,
        "transition_index": 0,
        "transition": transition.to_dict(),
    }]

    result, report = runner.build_dagger_sequences_with_strict_disagreement_v4(
        rows,
        lane="archaludon",
        focus_action_types=(3,),
        max_mean_behavior_log_probability=-0.2,
    )

    assert len(result) == 1
    weights = tuple(step.supervision_weight for step in result[0].steps)
    assert weights == (1.0, 1.0, 0.0)
    assert report["supervised_prefix_count"] == 2


def test_focus_csv_parsers_keep_weak_matchup_defaults_explicit() -> None:
    assert runner._parse_focus_names("ozawa_crustle_v2,sue124_alakazam") == (
        "ozawa_crustle_v2", "sue124_alakazam",
    )
    assert runner._parse_focus_ints("1,0", field="focus_seats", minimum=0, maximum=1) == (1, 0)
    assert runner._parse_focus_ints("9,13,14", field="focus_action_types", minimum=0, maximum=16) == (9, 13, 14)


def test_action_type_weight_parser_exposes_explicit_balanced_arm() -> None:
    assert runner._parse_action_type_weights("none") is None
    balanced = runner._parse_action_type_weights("balanced_v1")
    assert balanced["9"] == 1.5
    assert balanced["13"] == 1.25
    assert balanced["14"] == 1.5
    assert balanced["STOP"] == 0.75
    assert runner._parse_action_type_weights("9=1.5,13=1.25") == {"9": 1.5, "13": 1.25}


def test_action_type_weight_parser_rejects_duplicate_or_invalid_values() -> None:
    for value in ("9=1.5,9=1.25", "9=0", "9=nan", "9", "=1.0"):
        with pytest.raises(argparse.ArgumentTypeError):
            runner._parse_action_type_weights(value)


def test_training_progress_callback_throttles_but_flushes_epoch_end(tmp_path) -> None:
    callback = runner._make_training_progress_callback(
        tmp_path / "progress.json", seed=3, epochs=2, started=time.monotonic() - 5.0,
        write_interval_seconds=999.0,
    )

    callback({"epoch": 0, "sequences_completed": 1, "sequences_total": 3})
    callback({"epoch": 0, "sequences_completed": 2, "sequences_total": 3})
    callback({"epoch": 0, "sequences_completed": 3, "sequences_total": 3})

    payload = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "training"
    assert payload["status"] == "running"
    assert payload["seed"] == 3
    assert payload["epochs_requested"] == 2
    assert payload["sequences_completed"] == 3


def test_validation_imitation_metrics_are_attached_to_each_seed_report(monkeypatch) -> None:
    expected = {
        "schema": "meta-specialist-v4-imitation-metrics-v1",
        "partition": "validation",
        "recurrence": "carry",
        "complete_action": {"top1": 0.72},
    }
    observed = {}

    def fake_evaluate(model, sequences, *, partition, recurrence):
        observed.update({"model": model, "sequences": sequences, "partition": partition, "recurrence": recurrence})
        return expected

    monkeypatch.setattr(runner, "evaluate_recurrent_imitation_v4", fake_evaluate)
    model = object()
    validation = (object(),)
    result = runner._validation_imitation_metrics_v4(model, validation)

    assert result == expected
    assert observed == {"model": model, "sequences": validation, "partition": "validation", "recurrence": "carry"}


def test_dagger_screen_subject_identity_rejects_legacy_schema_and_missing_fields() -> None:
    with pytest.raises(ValueError, match="screen schema"):
        runner._validate_screen_subject_identity(
            {"schema": "meta-specialist-v4-dagger-screen-v1"}, lane="archaludon",
        )
    with pytest.raises(ValueError, match="subject deck"):
        runner._validate_screen_subject_identity(
            {"schema": "meta-specialist-v4-dagger-screen-v2", "subject_archetype_id": "archaludon"},
            lane="archaludon",
        )


def test_dagger_fraction_report_counts_selected_overlay_not_available_overlay() -> None:
    base = tuple(SimpleNamespace(component_id=value) for value in ("a" * 64, "b" * 64))
    dagger = tuple(SimpleNamespace(component_id=value) for value in ("c" * 64, "d" * 64))
    mixed = (base[0], base[1], dagger[0])
    summary = runner._summarize_dagger_mixture(base=base, dagger=dagger, mixed=mixed)
    assert summary == {
        "base_available": 2,
        "dagger_available": 2,
        "base_selected": 2,
        "dagger_selected": 1,
        "dagger_fraction_actual": 1 / 3,
    }


def test_default_dagger_selection_keeps_all_screen_rows_unchanged() -> None:
    screen, rows = _strict_screen_and_rows()

    selected, metadata = runner._select_dagger_transition_rows_v4(
        rows,
        screen=screen,
        strict_focus_targets=False,
        focus_opponents=("target_a",),
        focus_seats=(1,),
    )

    assert selected == rows
    assert all(selected[index] is rows[index] for index in range(len(rows)))
    assert metadata is None


def test_strict_focus_targets_select_complete_target_components_and_report_actual_selection() -> None:
    screen, rows = _strict_screen_and_rows()

    selected, available_metadata = runner._select_dagger_transition_rows_v4(
        rows,
        screen=screen,
        strict_focus_targets=True,
        focus_opponents=("target_a", "target_b"),
        focus_seats=(1,),
    )

    target_a = _STRICT_SCREEN_GAMES[0][1]
    target_b = _STRICT_SCREEN_GAMES[1][1]
    assert tuple(row["component_id"] for row in selected) == (
        target_a, target_a, target_b, target_b,
    )
    assert {(row["opponent_id"], row["seat"]) for row in selected} == {
        ("target_a", 1), ("target_b", 1),
    }
    assert available_metadata == (
        {
            "game_id": target_a,
            "episode_group": target_a,
            "component_id": target_a,
            "partition": "train",
            "opponent_id": "target_a",
            "seat": 1,
            "env_seed": 101,
            "transition_records": 2,
        },
        {
            "game_id": target_b,
            "episode_group": target_b,
            "component_id": target_b,
            "partition": "validation",
            "opponent_id": "target_b",
            "seat": 1,
            "env_seed": 102,
            "transition_records": 2,
        },
    )

    base = (SimpleNamespace(
        component_id="f" * 64, episode_group="f" * 64, partition="train",
    ),)
    dagger = tuple(SimpleNamespace(
        component_id=row["component_id"],
        episode_group=row["episode_group"],
        partition=row["partition"],
    ) for row in available_metadata)
    mixed = (base[0], dagger[1])
    report = runner._strict_target_sequence_report_v4(
        focus_opponents=("target_a", "target_b"),
        focus_seats=(1,),
        available_metadata=available_metadata,
        base=base,
        dagger=dagger,
        mixed=mixed,
    )

    assert report["available_counts"] == {
        "sequences": 2,
        "episodes": 2,
        "components": 2,
        "transitions": 4,
        "by_partition": {"train": 1, "validation": 1},
        "by_opponent": {"target_a": 1, "target_b": 1},
        "by_seat": {"1": 2},
        "by_opponent_seat": {"target_a": {"1": 1}, "target_b": {"1": 1}},
    }
    assert report["selected_counts"] == {
        "sequences": 1,
        "episodes": 1,
        "components": 1,
        "transitions": 2,
        "by_partition": {"train": 0, "validation": 1},
        "by_opponent": {"target_a": 0, "target_b": 1},
        "by_seat": {"1": 1},
        "by_opponent_seat": {"target_a": {"1": 0}, "target_b": {"1": 1}},
    }
    assert report["selected_sequence_metadata"] == [dict(available_metadata[1])]


def test_strict_focus_targets_fail_closed_when_requested_pair_is_unavailable() -> None:
    screen, rows = _strict_screen_and_rows()

    with pytest.raises(ValueError, match="target availability"):
        runner._select_dagger_transition_rows_v4(
            rows,
            screen=screen,
            strict_focus_targets=True,
            focus_opponents=("target_a", "target_b"),
            focus_seats=(0, 1),
        )


@pytest.mark.parametrize("corruption", ("component_overlap", "transition_gap"))
def test_strict_focus_targets_fail_closed_on_component_or_episode_corruption(corruption: str) -> None:
    screen, original_rows = _strict_screen_and_rows()
    rows = [deepcopy(row) for row in original_rows]
    if corruption == "component_overlap":
        rows[2]["component_id"] = rows[0]["component_id"]
        expected = "component overlap"
    else:
        rows[1]["transition_index"] = 2
        expected = "transition indices"

    with pytest.raises(ValueError, match=expected):
        runner._select_dagger_transition_rows_v4(
            tuple(rows),
            screen=screen,
            strict_focus_targets=True,
            focus_opponents=("target_a", "target_b"),
            focus_seats=(1,),
        )


def test_strict_target_report_rejects_non_target_overlay_and_zero_selected_targets() -> None:
    target_a = _STRICT_SCREEN_GAMES[0][1]
    metadata = ({
        "game_id": target_a,
        "episode_group": target_a,
        "component_id": target_a,
        "partition": "train",
        "opponent_id": "target_a",
        "seat": 1,
        "env_seed": 101,
        "transition_records": 2,
    },)
    target = SimpleNamespace(component_id=target_a, episode_group=target_a, partition="train")
    other = SimpleNamespace(component_id="e" * 64, episode_group="e" * 64, partition="train")
    base = (SimpleNamespace(component_id="f" * 64, episode_group="f" * 64, partition="train"),)

    with pytest.raises(ValueError, match="non-target"):
        runner._strict_target_sequence_report_v4(
            focus_opponents=("target_a",), focus_seats=(1,),
            available_metadata=metadata, base=base, dagger=(target, other), mixed=(target,),
        )
    with pytest.raises(ValueError, match="selected no target"):
        runner._strict_target_sequence_report_v4(
            focus_opponents=("target_a",), focus_seats=(1,),
            available_metadata=metadata, base=base, dagger=(target,), mixed=base,
        )


def test_paired_seed_manifest_binds_each_requested_seed_to_closed_provenance(tmp_path: Path) -> None:
    seed_zero = _paired_seed_binding(tmp_path, seed=0, suffix="zero")
    seed_one = _paired_seed_binding(tmp_path, seed=1, suffix="one")
    manifest, manifest_sha = _write_paired_seed_manifest(tmp_path, [seed_one, seed_zero])

    bindings, manifest_identity = runner._resolve_paired_seed_provenance_v4(
        seeds=(0, 1), lane="archaludon", manifest_path=manifest,
        manifest_file_sha256=manifest_sha,
    )

    assert tuple(binding["seed"] for binding in bindings) == (0, 1)
    assert bindings[0]["screen_file_sha256"] == seed_zero["screen"]["file_sha256"]
    assert bindings[1]["init_checkpoint_tensor_state_sha256"] == seed_one["init_checkpoint"]["tensor_state_sha256"]
    assert manifest_identity == {
        "mode": "paired_seed_manifest",
        "path": str(manifest.resolve()),
        "file_sha256": manifest_sha,
    }


def test_paired_seed_manifest_fails_closed_on_incomplete_or_extra_seed_coverage(tmp_path: Path) -> None:
    seed_zero = _paired_seed_binding(tmp_path, seed=0, suffix="zero")
    manifest, manifest_sha = _write_paired_seed_manifest(tmp_path, [seed_zero])

    with pytest.raises(ValueError, match="seed coverage"):
        runner._resolve_paired_seed_provenance_v4(
            seeds=(0, 1), lane="archaludon", manifest_path=manifest,
            manifest_file_sha256=manifest_sha,
        )


def test_paired_seed_checkpoint_binding_rejects_cross_seed_screen_and_init_mix(tmp_path: Path) -> None:
    manifest, manifest_sha = _write_paired_seed_manifest(
        tmp_path, [_paired_seed_binding(tmp_path, seed=0, suffix="zero")],
    )
    binding = runner._resolve_paired_seed_provenance_v4(
        seeds=(0,), lane="archaludon",
        manifest_path=manifest, manifest_file_sha256=manifest_sha,
    )[0][0]
    mixed_screen = {
        "checkpoint": {
            "file_sha256": "f" * 64,
            "tensor_state_sha256": "2" * 64,
        },
    }

    with pytest.raises(ValueError, match="checkpoint identity"):
        runner._validate_dagger_seed_checkpoint_binding_v4(mixed_screen, binding=binding)


def test_paired_selected_sequence_identity_seals_every_seed_provenance(tmp_path: Path) -> None:
    seed_zero = _paired_seed_binding(tmp_path, seed=0, suffix="zero")
    seed_one = _paired_seed_binding(tmp_path, seed=1, suffix="one")
    manifest, manifest_sha = _write_paired_seed_manifest(tmp_path, [seed_zero, seed_one])
    bindings, manifest_identity = runner._resolve_paired_seed_provenance_v4(
        seeds=(0, 1), lane="archaludon", manifest_path=manifest,
        manifest_file_sha256=manifest_sha,
    )
    records = [
        {
            "seed": binding["seed"],
            "selected_sequence_sha256": str(binding["screen_file_sha256"]),
            "dagger_sequence_sha256": str(binding["transitions_file_sha256"]),
            **binding,
        }
        for binding in bindings
    ]

    identity = runner._paired_selected_sequence_identity_v4(
        records, paired_manifest_identity=manifest_identity,
    )
    changed = deepcopy(records)
    changed[1]["init_checkpoint_file_sha256"] = "0" * 64

    assert identity == runner._paired_selected_sequence_identity_v4(
        list(reversed(records)), paired_manifest_identity=manifest_identity,
    )
    assert identity != runner._paired_selected_sequence_identity_v4(
        changed, paired_manifest_identity=manifest_identity,
    )


def test_training_material_resolves_seed_specific_checkpoint_and_sequences() -> None:
    material = {
        "binding": {
            "init_checkpoint_path": "/tmp/seed-1.pt",
            "init_checkpoint_file_sha256": "a" * 64,
            "init_checkpoint_tensor_state_sha256": "b" * 64,
            "screen_path": "/tmp/seed-1-screen.json",
            "screen_file_sha256": "c" * 64,
            "transitions_path": "/tmp/seed-1-screen.jsonl",
            "transitions_file_sha256": "d" * 64,
        },
        "dagger": ("dagger-seed-1",),
        "mixed": ("mixed-seed-1",),
        "train": ("train-seed-1",),
        "validation": ("validation-seed-1",),
        "mixture_summary": {"dagger_selected": 1},
        "selected_sha": "e" * 64,
        "dagger_sha": "7" * 64,
        "focus_component_ids": ("f" * 64,),
        "strict_target_report": {"enabled": True},
    }

    resolved = runner._training_material_v4(material)

    assert resolved["init_checkpoint_path"] == Path("/tmp/seed-1.pt")
    assert resolved["init_checkpoint_file_sha256"] == "a" * 64
    assert resolved["init_checkpoint_tensor_state_sha256"] == "b" * 64
    assert resolved["screen_file_sha256"] == "c" * 64
    assert resolved["transitions_file_sha256"] == "d" * 64
    assert resolved["dagger"] == ("dagger-seed-1",)
    assert resolved["mixed"] == ("mixed-seed-1",)
    assert resolved["train"] == ("train-seed-1",)
    assert resolved["validation"] == ("validation-seed-1",)
    assert resolved["selected_sha"] == "e" * 64
    assert resolved["dagger_sha"] == "7" * 64
    assert resolved["focus_component_ids"] == ("f" * 64,)
