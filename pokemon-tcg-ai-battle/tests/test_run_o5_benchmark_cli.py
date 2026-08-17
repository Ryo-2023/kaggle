"""Argument parsing, manifest generation, and seat/attribution wiring for
scripts/run_o5_benchmark.py.

Real cabt execution is exercised manually (see the O5 versioned benchmark
evidence docs): unit tests here cover wiring, not the live engine, matching
the existing convention for ``scripts/run_actual_league.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.run_o5_benchmark import _make_run_match_factory, _normalize_seat_status, main  # noqa: E402


def _find_manifest(tmp_path, *, prefix: str) -> Path:
    matches = sorted(tmp_path.glob(f"{prefix}*.json"))
    assert len(matches) == 1, f"expected exactly one manifest matching {prefix}*, found {matches}"
    return matches[0]


def _base_args(tmp_path, **overrides):
    args = [
        "--benchmark-id", "o5-benchmark-core-v1",
        "--benchmark-version", "1.0.0",
        "--benchmark-set", "performance",
        "--candidate-agent-id", "rule_v0",
        "--deck", str(REPOSITORY_ROOT / "deck.csv"),
        "--seeds", "90000",
        "--games-per-member", "2",
        "--output-dir", str(tmp_path),
        "--dry-run",
    ]
    for key, value in overrides.items():
        args += [key, value] if not isinstance(value, list) else [key, *value]
    return args


def test_dry_run_writes_manifest_without_running_any_match(tmp_path, monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_match must not be called in --dry-run mode")

    monkeypatch.setattr("scripts.run_o5_benchmark.run_match", _fail_if_called)
    exit_code = main(_base_args(tmp_path))
    assert exit_code == 0
    manifest_path = _find_manifest(tmp_path, prefix="versioned_benchmark_manifest__performance__rule_v0__")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["benchmark_id"] == "o5-benchmark-core-v1"
    assert manifest["benchmark_kind"] == "performance"
    assert manifest["status"] == "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"
    assert manifest["sets"]["current_meta"] == []
    assert manifest["sets"]["safety"] == []
    assert manifest["candidate_artifact_hash"] == "NOT_APPLICABLE"
    # cabt_version must be the real probed version, not a dry-run placeholder,
    # so the manifest a dry-run previews matches what a real run would build.
    assert manifest["cabt_version"] not in ("", "unknown")


def test_safety_benchmark_set_excludes_performance_sets(tmp_path):
    exit_code = main(_base_args(tmp_path, **{"--benchmark-set": "safety"}))
    assert exit_code == 0
    manifest_path = _find_manifest(tmp_path, prefix="versioned_benchmark_manifest__safety__rule_v0__")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["benchmark_kind"] == "safety"
    assert manifest["sets"]["core_regression"] == []
    assert manifest["sets"]["safety"] != []


def test_rejects_unknown_candidate_agent_id(tmp_path):
    exit_code = main(_base_args(tmp_path, **{"--candidate-agent-id": "not_a_real_agent"}))
    assert exit_code == 3


def test_hash_pinned_candidate_requires_model_path(tmp_path):
    exit_code = main(_base_args(tmp_path, **{"--candidate-agent-id": "neural_actual_trained"}))
    assert exit_code == 3


def test_hash_pinned_candidate_with_model_path_builds_a_manifest(tmp_path):
    exit_code = main(_base_args(
        tmp_path,
        **{"--candidate-agent-id": "neural_actual_trained", "--candidate-model-path": str(tmp_path / "unused.json")},
    ))
    assert exit_code == 0
    manifest_path = _find_manifest(tmp_path, prefix="versioned_benchmark_manifest__performance__neural_actual_trained__")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["candidate_artifact_id"] == "neural_actual_trained"
    assert len(manifest["candidate_artifact_hash"]) == 64


def test_two_different_manifests_never_collide_in_the_same_output_dir(tmp_path, monkeypatch):
    # Independent-audit regression: a previous revision named the manifest
    # file only by benchmark_kind + candidate_agent_id, so a second run with
    # a different --benchmark-id/--benchmark-version/--games-per-member
    # pointed at the same --output-dir silently overwrote the first run's
    # manifest file even though nothing else about the run was shared.
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("run_match must not be called in --dry-run mode")

    monkeypatch.setattr("scripts.run_o5_benchmark.run_match", _fail_if_called)
    first_exit = main(_base_args(tmp_path, **{"--benchmark-id": "o5-audit-check-A", "--benchmark-version": "1.0.0"}))
    second_exit = main(_base_args(tmp_path, **{"--benchmark-id": "o5-audit-check-B", "--benchmark-version": "9.9.9", "--games-per-member": "6"}))
    assert first_exit == 0
    assert second_exit == 0
    manifests = sorted(tmp_path.glob("versioned_benchmark_manifest__performance__rule_v0__*.json"))
    assert len(manifests) == 2, f"expected both manifests to coexist, found {manifests}"
    ids = {json.loads(path.read_text(encoding="utf-8"))["benchmark_id"] for path in manifests}
    assert ids == {"o5-audit-check-A", "o5-audit-check-B"}


def test_candidate_agent_id_has_no_default_and_is_always_required(tmp_path):
    import pytest

    import scripts.run_o5_benchmark as module

    with pytest.raises(SystemExit) as excinfo:
        module.main([
            "--benchmark-id", "x", "--benchmark-version", "1.0.0", "--benchmark-set", "performance",
            "--seeds", "1", "--output-dir", str(tmp_path), "--dry-run",
        ])
    assert excinfo.value.code != 0


def test_normalize_seat_status_maps_known_and_unknown_values():
    assert _normalize_seat_status("DONE") == "DONE"
    assert _normalize_seat_status("invalid") == "INVALID"
    assert _normalize_seat_status(None) == "NOT_OBSERVABLE"
    assert _normalize_seat_status("SOMETHING_ELSE") == "UNKNOWN"


def test_play_closure_attributes_seat_and_winner_correctly_when_champion_is_seat_1(monkeypatch, tmp_path):
    calls: list[dict] = []

    def fake_run_match(**kwargs):
        calls.append(kwargs)
        # agent_a is always seat 0, agent_b is seat 1 (cabt's own convention).
        # champion is seat 1 here, so agent_b_name must be the champion.
        assert kwargs["agent_b_name"] == "rule_v0"
        assert kwargs["agent_a_name"] == "random_legal"
        return {"status": "DONE", "winner": 1, "elapsed_seconds": 0.05, "steps": 12, "agent_status": ["DONE", "DONE"]}

    monkeypatch.setattr("scripts.run_o5_benchmark.run_match", fake_run_match)
    factory = _make_run_match_factory(
        deck_path=REPOSITORY_ROOT / "deck.csv", max_steps=10_000, transient_root=tmp_path, candidate_model_path=None,
    )
    play = factory("rule_v0", "random_legal")
    result = play({"match_index": 0, "seed": 1, "champion_player_index": 1, "challenger_player_index": 0})
    assert calls, "run_match was not invoked"
    assert result["winner_agent"] == "champion"
    assert result["champion_status"] == "DONE"
    assert result["challenger_status"] == "DONE"
    assert result["steps"] == 12


def test_play_closure_reports_a_challenger_win_and_opponent_invalid_status(monkeypatch, tmp_path):
    def fake_run_match(**kwargs):
        return {"status": "AGENT_INVALID", "winner": None, "elapsed_seconds": 0.02, "steps": 1, "agent_status": ["DONE", "INVALID"]}

    monkeypatch.setattr("scripts.run_o5_benchmark.run_match", fake_run_match)
    factory = _make_run_match_factory(
        deck_path=REPOSITORY_ROOT / "deck.csv", max_steps=10_000, transient_root=tmp_path, candidate_model_path=None,
    )
    play = factory("rule_v0", "invalid_artifact")
    # champion is seat 0 this time: agent_status[0]="DONE" (champion),
    # agent_status[1]="INVALID" (challenger) must map correctly.
    result = play({"match_index": 0, "seed": 1, "champion_player_index": 0, "challenger_player_index": 1})
    assert result["winner_agent"] is None
    assert result["champion_status"] == "DONE"
    assert result["challenger_status"] == "INVALID"


def test_play_closure_reports_not_observable_when_agent_status_is_absent(monkeypatch, tmp_path):
    def fake_run_match(**kwargs):
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.02, "steps": 5}

    monkeypatch.setattr("scripts.run_o5_benchmark.run_match", fake_run_match)
    factory = _make_run_match_factory(
        deck_path=REPOSITORY_ROOT / "deck.csv", max_steps=10_000, transient_root=tmp_path, candidate_model_path=None,
    )
    play = factory("rule_v0", "random_legal")
    result = play({"match_index": 0, "seed": 1, "champion_player_index": 0, "challenger_player_index": 1})
    assert result["champion_status"] == "NOT_OBSERVABLE"
    assert result["challenger_status"] == "NOT_OBSERVABLE"
