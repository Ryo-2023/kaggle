"""Contract tests for the resumable joint Deck × Policy screen."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts import run_alakazam_joint_optimization as runner


def _opponents() -> list[runner.Opponent]:
    return [
        runner.Opponent(f"op-{policy}-{family}", (1,) * 60, f"kind-{policy}", f"policy-{policy}", f"family-{family}")
        for policy in range(5)
        for family in range(4)
    ]


def _candidate(candidate_id: str = "alakazam_baseline_v1--rule_v0") -> dict[str, object]:
    deck = runner.DeckAsset("alakazam_baseline_v1", (1,) * 60, "test", True)
    policy = runner.PolicyAsset("rule_v0", "rule_v0", "policy-hash", None, "test", "local-cpu")
    record = runner._joint_candidate_record(deck, policy)
    record["candidate_id"] = candidate_id
    record["candidate_identity_hash"] = runner._digest({key: value for key, value in record.items() if key != "candidate_identity_hash"})
    return record


def test_joint_schedule_balances_policy_hashes_and_sides() -> None:
    schedule = runner._joint_schedule(_opponents(), 64)
    policies = Counter(str(row["opponent_policy_hash"]) for row in schedule)
    sides = Counter(int(row["side"]) for row in schedule)

    assert len(schedule) == 64
    assert max(policies.values()) == 13
    assert min(policies.values()) == 12
    assert sides == {0: 32, 1: 32}


def test_atomic_shard_read_ignores_temporary_and_rejects_identity_mismatch(tmp_path: Path) -> None:
    candidate = _candidate()
    shard_dir = tmp_path / "shards" / str(candidate["candidate_id"])
    shard_dir.mkdir(parents=True)
    runner._write_json(shard_dir / "game-000001.json", {"game_id": "game-000001", "schedule_hash": "schedule", "candidate_identity_hash": candidate["candidate_identity_hash"], "status": "DONE"})
    (shard_dir / "game-000002.json.tmp").write_text("{partial", encoding="utf-8")
    assert list(runner._read_complete_shards(shard_dir, candidate, "schedule")) == ["game-000001"]

    runner._write_json(shard_dir / "game-000003.json", {"game_id": "game-000003", "schedule_hash": "other", "candidate_identity_hash": candidate["candidate_identity_hash"], "status": "DONE"})
    with pytest.raises(ValueError, match="identity mismatch"):
        runner._read_complete_shards(shard_dir, candidate, "schedule")


def test_aggregate_selects_top_quartile_and_writes_scorecards(tmp_path: Path) -> None:
    phase_root = tmp_path / "joint_screen"
    for name in ("shards", "aggregate"):
        (phase_root / name).mkdir(parents=True)
    candidates = [_candidate(f"candidate-{index}") for index in range(4)]
    schedule_hash = "schedule"
    schedule = [
        {"game_index": game + 1, "game_id": f"game-{game + 1:06d}", "opponent_id": f"op-{game}", "opponent_policy_hash": f"policy-{game % 2}", "opponent_deck_hash": f"deck-{game}", "opponent_family": f"family-{game}", "side": game % 2}
        for game in range(4)
    ]
    for index, candidate in enumerate(candidates):
        shard_dir = phase_root / "shards" / str(candidate["candidate_id"])
        shard_dir.mkdir()
        for game, slot in enumerate(schedule):
            runner._write_json(shard_dir / f"game-{game + 1:06d}.json", {
                **candidate, **slot, "schedule_hash": schedule_hash,
                "status": "DONE", "won": game < index, "opponent_id": f"op-{game}",
                "opponent_policy_hash": f"policy-{game % 2}", "side": game % 2,
                "elapsed_seconds": 0.1, "illegal": False, "crash": False, "timeout": False,
            })
    summary = runner._aggregate_joint_screen(phase_root, candidates, schedule_hash, schedule)
    selected = json.loads((phase_root / "aggregate" / "next_stage_candidates.json").read_text(encoding="utf-8"))
    assert len(summary) == 4
    assert len(selected["selected"]) == 1
    assert (phase_root / "aggregate" / "scorecard_uniform_policy.csv").is_file()
    assert (phase_root / "aggregate" / "scorecard_observed_meta.csv").is_file()
    assert (phase_root / "aggregate" / "scorecard_worst_quartile.csv").is_file()


def test_joint_screen_phase_dispatches_real_runner_not_ready_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_run(output: Path, checkpoint: Path, replay_registry: Path, *, resume: bool, games_per_pair: int = 64, smoke: bool = False, workers: int = 8) -> int:
        called.update({"output": output, "resume": resume, "games_per_pair": games_per_pair, "smoke": smoke, "workers": workers})
        return 0

    monkeypatch.setattr(runner, "_run_joint_screen", fake_run)
    assert runner.main(["joint-screen", "--output", str(tmp_path), "--resume"]) == 0
    assert called == {"output": tmp_path, "resume": True, "games_per_pair": 64, "smoke": False, "workers": 8}
