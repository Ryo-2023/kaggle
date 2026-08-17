"""Wave2 DAgger short-gate summary contracts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


OPPONENTS = [
    "kiyotah_lucario",
    "sue124_alakazam",
    "skarin_dragapult",
    "ozawa_crustle_v2",
    "nihei_megalopunny",
    "yaroslav_crustleaware_lucario",
]


def _load_script():
    script = Path(__file__).resolve().parents[2] / "scripts" / "summarize_v4_dagger_wave2.py"
    spec = importlib.util.spec_from_file_location("summarize_v4_dagger_wave2", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _row(wins: int, requested: int, *, faults: int = 0) -> dict[str, object]:
    losses = requested - wins - faults
    return {
        "w": wins,
        "d": 0,
        "l": losses,
        "f": faults,
        "requested": requested,
        "score_rate": wins / requested,
    }


def _evaluation(
    *, checkpoint_sha: str, tensor_sha: str, wins: int,
    seat_wins: tuple[int, int], opponent_wins: list[int], faults: int = 0,
    protocol_sha: str = "a" * 64,
) -> dict[str, object]:
    assert sum(seat_wins) == wins
    assert sum(opponent_wins) == wins
    seat_faults = (faults, 0)
    opponent_faults = [faults, 0, 0, 0, 0, 0]
    games_played = 192 - faults
    return {
        "schema_version": "meta-specialist-v4-heldout-checkpoint-strength-v1",
        "checkpoint": {
            "path": f"/fixture/{checkpoint_sha}.pt",
            "file_sha256": checkpoint_sha,
            "tensor_state_sha256": tensor_sha,
        },
        "subject_archetype_id": "archaludon",
        "subject_deck_csv": "/fixture/deck.csv",
        "subject_deck_file_sha256": "b" * 64,
        "fixed_held_out_opponent_ids": OPPONENTS,
        "opponent_ids": OPPONENTS,
        "opponent_fingerprints": [
            {
                "opponent_id": opponent,
                "canonical_deck_hash": hashlib.sha256(f"deck:{opponent}".encode()).hexdigest(),
                "deck_file_sha256": hashlib.sha256(f"file:{opponent}".encode()).hexdigest(),
                "policy_hash": hashlib.sha256(f"policy:{opponent}".encode()).hexdigest(),
            }
            for opponent in OPPONENTS
        ],
        "evaluation_protocol_sha256": protocol_sha,
        "evaluation_implementation_sha256": "c" * 64,
        "games_per_seat": 16,
        "base_seed": 12_500_000,
        "max_steps": 2_000,
        "requested_games": 192,
        "games_played": games_played,
        "faults": faults,
        "fault_reasons": {"RuntimeError: fixture": faults} if faults else {},
        "wins": wins,
        "draws": 0,
        "losses": games_played - wins,
        "score_rate": wins / 192,
        "score_denominator_games": 192,
        "score_ci95": [0.0, 1.0],
        "comparison_status": "invalid_faults" if faults else "valid",
        "seat": {
            str(seat): _row(seat_wins[seat], 96, faults=seat_faults[seat])
            for seat in (0, 1)
        },
        "per_opponent": {
            opponent: _row(opponent_wins[index], 32, faults=opponent_faults[index])
            for index, opponent in enumerate(OPPONENTS)
        },
        "elapsed_seconds": 1.0,
    }


def _validation_imitation_metrics() -> dict[str, object]:
    action_type = {
        str(action_type): {"top1": 0.75, "eligible_rows": 100}
        for action_type in (3, 7, 8, 9, 12, 13, 14)
    }
    action_type["STOP"] = {"top1": 0.86, "eligible_rows": 100}
    return {
        "schema": "meta-specialist-v4-imitation-metrics-v1",
        "partition": "validation",
        "recurrence": "carry",
        "complete_action": {
            "top1": 0.74, "eligible_rows": 1000, "forced_domain_size1_rows": 100,
        },
        "root": {"top1": 0.74, "eligible_rows": 800},
        "action_type": action_type,
    }


def _fixture_inputs(tmp_path: Path) -> dict[str, object]:
    checkpoints: dict[int, tuple[str, str]] = {}
    for seed in (0, 1):
        checkpoint = tmp_path / f"candidate-seed{seed}.pt"
        checkpoint.write_bytes(f"candidate-{seed}".encode())
        checkpoints[seed] = (hashlib.sha256(checkpoint.read_bytes()).hexdigest(), f"{seed + 1}" * 64)
    report = {
        "schema": "meta-specialist-v4-dagger-bc-report-v1",
        "mode": "RESEARCH_ONLY_UNIFORM_WEIGHT",
        "promotion_authority": False,
        "status": "RESEARCH_ONLY_COMPLETE",
        "lane": "archaludon",
        "training_config": {"seeds": [0, 1]},
        "seed_results": {
            str(seed): {
                "best_checkpoint_path": str(tmp_path / f"candidate-seed{seed}.pt"),
                "best_checkpoint_file_sha256": checkpoints[seed][0],
                "best_checkpoint_tensor_state_sha256": checkpoints[seed][1],
                "improved": True,
                "validation_imitation_metrics": _validation_imitation_metrics(),
            }
            for seed in (0, 1)
        },
    }
    report_path = tmp_path / "bc.json"
    report_sha = _write_json(report_path, report)

    baseline_specs = {
        0: (84, (37, 47), [18, 13, 11, 13, 14, 15]),
        1: (108, (51, 57), [21, 19, 11, 19, 21, 17]),
    }
    candidate_specs = {
        0: (102, (53, 49), [18, 16, 16, 15, 18, 19]),
        1: (114, (53, 61), [21, 21, 19, 20, 18, 15]),
    }
    baseline: dict[int, tuple[Path, str]] = {}
    candidate: dict[int, tuple[Path, str]] = {}
    for seed in (0, 1):
        baseline_path = tmp_path / f"wave6-seed{seed}.json"
        baseline_sha = _write_json(
            baseline_path,
            _evaluation(
                checkpoint_sha=f"{seed + 3}" * 64,
                tensor_sha=f"{seed + 5}" * 64,
                wins=baseline_specs[seed][0],
                seat_wins=baseline_specs[seed][1],
                opponent_wins=baseline_specs[seed][2],
            ),
        )
        baseline[seed] = (baseline_path, baseline_sha)
        candidate_path = tmp_path / f"candidate-seed{seed}.json"
        candidate_sha = _write_json(
            candidate_path,
            _evaluation(
                checkpoint_sha=checkpoints[seed][0],
                tensor_sha=checkpoints[seed][1],
                wins=candidate_specs[seed][0],
                seat_wins=candidate_specs[seed][1],
                opponent_wins=candidate_specs[seed][2],
            ),
        )
        candidate[seed] = (candidate_path, candidate_sha)
    return {
        "report_path": report_path,
        "report_sha": report_sha,
        "baseline": baseline,
        "candidate": candidate,
    }


def _args(inputs: dict[str, object], tmp_path: Path, *, candidates: bool = True) -> list[str]:
    baseline = inputs["baseline"]
    candidate = inputs["candidate"]
    assert isinstance(baseline, dict) and isinstance(candidate, dict)
    args = [
        "--bc-report", str(inputs["report_path"]),
        "--bc-report-sha256", str(inputs["report_sha"]),
        "--wave6-evaluation", f"0={baseline[0][0]}",
        "--wave6-evaluation-sha256", f"0={baseline[0][1]}",
        "--wave6-evaluation", f"1={baseline[1][0]}",
        "--wave6-evaluation-sha256", f"1={baseline[1][1]}",
        "--json-output", str(tmp_path / "summary.json"),
        "--markdown-output", str(tmp_path / "summary.md"),
    ]
    if candidates:
        args += [
            "--candidate-evaluation", f"0={candidate[0][0]}",
            "--candidate-evaluation-sha256", f"0={candidate[0][1]}",
            "--candidate-evaluation", f"1={candidate[1][0]}",
            "--candidate-evaluation-sha256", f"1={candidate[1][1]}",
        ]
    return args


def test_missing_candidate_evaluations_is_unmeasured_and_has_no_training_authority(tmp_path: Path) -> None:
    """A completed BC report alone must never become a passing short gate."""
    script = _load_script()
    inputs = _fixture_inputs(tmp_path)
    assert script.main(_args(inputs, tmp_path, candidates=False)) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["short_gate"]["status"] == "UNMEASURED"
    assert summary["long_training_authority"] is False
    assert summary["candidate_evaluations"] == {}
    assert summary["short_gate"]["checks"]["candidate_evaluations_complete"]["status"] == "UNMEASURED"


def test_complete_fair_two_seed_comparison_emits_pass_with_seed_pooled_seat_and_opponent_breakdown(
    tmp_path: Path,
) -> None:
    """Wrong pooling, omitted seed checks, or omitted subgroup regressions must fail this contract."""
    script = _load_script()
    inputs = _fixture_inputs(tmp_path)
    assert script.main(_args(inputs, tmp_path)) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["short_gate"]["status"] == "PASS"
    assert summary["long_training_authority"] is False
    assert summary["comparison"]["pooled"]["candidate"]["wins"] == 216
    assert summary["comparison"]["pooled"]["baseline"]["wins"] == 192
    assert summary["comparison"]["pooled"]["delta_score_rate"] == pytest.approx(0.0625)
    assert summary["comparison"]["by_seed"]["0"]["delta_score_rate"] == pytest.approx(18 / 192)
    assert summary["comparison"]["by_seed"]["1"]["delta_score_rate"] == pytest.approx(6 / 192)
    assert summary["comparison"]["by_seat"]["0"]["delta_score_rate"] == pytest.approx(18 / 192)
    assert summary["comparison"]["by_seat"]["1"]["delta_score_rate"] == pytest.approx(6 / 192)
    assert summary["short_gate"]["checks"]["opponent_non_regression"]["non_regressive_count"] == 6
    markdown = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "| 0 |" in markdown
    assert "kiyotah_lucario" in markdown
    assert "研究費用ゲート" in markdown


def test_candidate_without_inline_action_metrics_cannot_pass_short_gate(tmp_path: Path) -> None:
    """Outcome-only evidence must not authorize the next training arm."""
    script = _load_script()
    inputs = _fixture_inputs(tmp_path)
    report = json.loads(inputs["report_path"].read_text(encoding="utf-8"))
    for row in report["seed_results"].values():
        row.pop("validation_imitation_metrics")
    inputs["report_sha"] = _write_json(inputs["report_path"], report)
    assert script.main(_args(inputs, tmp_path)) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["short_gate"]["status"] != "PASS"
    assert summary["short_gate"]["checks"]["action_metrics_complete"]["status"] == "UNMEASURED"


def test_faulted_candidate_fails_gate_even_when_its_score_is_higher(tmp_path: Path) -> None:
    """A faulted requested game must not disappear behind a higher nominal win rate."""
    script = _load_script()
    inputs = _fixture_inputs(tmp_path)
    candidate = inputs["candidate"]
    assert isinstance(candidate, dict)
    path, _old_sha = candidate[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["faults"] = 1
    payload["games_played"] = 191
    payload["losses"] -= 1
    payload["comparison_status"] = "invalid_faults"
    payload["fault_reasons"] = {"RuntimeError: fixture": 1}
    payload["seat"]["0"]["f"] = 1
    payload["seat"]["0"]["l"] -= 1
    payload["per_opponent"][OPPONENTS[0]]["f"] = 1
    payload["per_opponent"][OPPONENTS[0]]["l"] -= 1
    candidate[0] = (path, _write_json(path, payload))
    assert script.main(_args(inputs, tmp_path)) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["short_gate"]["status"] == "FAIL"
    assert summary["short_gate"]["checks"]["zero_faults"]["status"] == "FAIL"


def test_fault_aggregate_must_match_seat_and_opponent_rows(tmp_path: Path) -> None:
    """A top-level fault cannot be hidden by clean subgroup rows."""
    script = _load_script()
    inputs = _fixture_inputs(tmp_path)
    candidate = inputs["candidate"]
    assert isinstance(candidate, dict)
    path, _old_sha = candidate[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["faults"] = 1
    payload["games_played"] = 191
    payload["losses"] -= 1
    candidate[0] = (path, _write_json(path, payload))
    with pytest.raises(SystemExit) as exc_info:
        script.main(_args(inputs, tmp_path))
    assert exc_info.value.code == 2


@pytest.mark.parametrize("mutation", ["raw_sha", "checkpoint", "protocol"])
def test_tampered_or_incompatible_evidence_is_rejected(tmp_path: Path, mutation: str) -> None:
    """Raw-byte tampering, checkpoint substitution, and unfair protocols must fail closed."""
    script = _load_script()
    inputs = _fixture_inputs(tmp_path)
    candidate = inputs["candidate"]
    assert isinstance(candidate, dict)
    if mutation == "raw_sha":
        path, _sha = candidate[0]
        candidate[0] = (path, "f" * 64)
    else:
        path, _sha = candidate[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if mutation == "checkpoint":
            payload["checkpoint"]["file_sha256"] = "f" * 64
        else:
            payload["evaluation_protocol_sha256"] = "f" * 64
        candidate[0] = (path, _write_json(path, payload))
    with pytest.raises(SystemExit) as exc_info:
        script.main(_args(inputs, tmp_path))
    assert exc_info.value.code == 2
