"""Failure evidence and child-result contracts for the scale-up runner."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from mage_ptcg.offline_scaleup import pipeline


@pytest.mark.parametrize(("stdout", "fault"), [
    ("", "NO_JSON_OUTPUT"),
    ("warning only\n", "NO_JSON_OUTPUT"),
    ('warning\nOFFLINE_SCALEUP_RESULT:{"ok":true}\n', None),
    ("OFFLINE_SCALEUP_RESULT:{broken}\n", "MALFORMED_JSON_OUTPUT"),
    ('OFFLINE_SCALEUP_RESULT:{"ok":true}\nOFFLINE_SCALEUP_RESULT:{"ok":true}\n', "AMBIGUOUS_JSON_OUTPUT"),
])
def test_stdout_contract_is_strict_and_warning_tolerant(stdout: str, fault: str | None) -> None:
    result, actual = pipeline._stdout_result_contract(stdout)
    assert actual == fault
    assert (result is not None) is (fault is None)


def test_empty_malformed_and_jsonl_schedule_are_rejected(tmp_path: Path) -> None:
    for name, content in (("empty.json", ""), ("malformed.json", "{"), ("wrong.json", "{}\n{}\n")):
        path = tmp_path / name; path.write_text(content, encoding="utf-8")
        with pytest.raises(pipeline.ContractError): pipeline._read_json(path)


def test_engine_failure_scope_is_seat_aware_without_candidate_fault_inference() -> None:
    candidate = pipeline._engine_failure_scope({"status": "AGENT_ERROR", "agent_status": ["ERROR", "DONE"]}, 0)
    opponent = pipeline._engine_failure_scope({"status": "AGENT_ERROR", "agent_status": ["DONE", "ERROR"]}, 0)
    both = pipeline._engine_failure_scope({"status": "AGENT_ERROR", "agent_status": ["ERROR", "ERROR"]}, 1)
    unavailable = pipeline._engine_failure_scope({"status": "AGENT_ERROR", "agent_status": None}, 0)
    assert candidate["engine_failure_scope"] == "CANDIDATE_SEAT"
    assert opponent["engine_failure_scope"] == "OPPONENT_SEAT"
    assert both["engine_failure_scope"] == "BOTH_SEATS"
    assert unavailable["engine_failure_scope"] == "UNAVAILABLE"


def test_failure_summary_and_resume_skip_completed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    population = {"schema_version": pipeline.POPULATION_SCHEMA, "semantic_population_digest": "a" * 64, "entries": []}
    population_path = tmp_path / "population.json"; population_path.write_text(json.dumps(population), encoding="utf-8")
    run = tmp_path / "run"; run.mkdir()
    schedule = {"schema_version": pipeline.SCHEDULE_SCHEMA, "schedule_digest": "b" * 64, "population_digest": "a" * 64, "planned_games": 2, "games": [{"game_id": "one"}, {"game_id": "two"}]}
    (run / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    calls: list[str] = []
    def broken(job, **_kwargs):
        calls.append(job["game_id"])
        return ({"status": "ERROR", "legal": False, "candidate_fault": False, "mapping_valid": False, "score_identity_valid": False, "teacher_samples": []}, {"kind": "WORKER_ERROR", "returncode": 2, "stdout_bytes": 0, "stderr_bytes": 0})
    monkeypatch.setattr(pipeline, "_run_worker", broken)
    summary = pipeline.run_league(run_dir=run, population_path=population_path, repo=tmp_path, executor="fixture", timeout=1, max_attempts=1, workers=1)
    assert summary["gate"] == "BLOCKED"
    assert (run / "attempts.jsonl").exists() and (run / "run_failure.json").exists()
    pipeline.run_league(run_dir=run, population_path=population_path, repo=tmp_path, executor="fixture", timeout=1, max_attempts=1, workers=1)
    assert calls == ["one", "two"]


def test_intentional_stop_is_resumable_without_failure_record(tmp_path: Path) -> None:
    population = {"schema_version": pipeline.POPULATION_SCHEMA, "semantic_population_digest": "a" * 64, "entries": []}
    population_path = tmp_path / "population.json"; population_path.write_text(json.dumps(population), encoding="utf-8")
    run = tmp_path / "run"; run.mkdir()
    schedule = {"schema_version": pipeline.SCHEDULE_SCHEMA, "schedule_digest": "b" * 64, "population_digest": "a" * 64,
                "planned_games": 2, "games": [{"game_id": "one", "candidate_side": 0}, {"game_id": "two", "candidate_side": 1}]}
    (run / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    partial = pipeline.run_league(run_dir=run, population_path=population_path, repo=Path.cwd(), executor="fixture",
                                  timeout=1, max_attempts=1, workers=1, stop_after=1)
    assert partial["completed"] == 1 and partial["gate"] == "BLOCKED"
    assert (run / "intentional_pause.json").exists() and not (run / "run_failure.json").exists()
    final = pipeline.run_league(run_dir=run, population_path=population_path, repo=Path.cwd(), executor="fixture",
                                timeout=1, max_attempts=1, workers=1)
    assert final["gate"] == "PASS" and final["completed"] == 2


def test_next_command_is_not_shell_split_in_script() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts/offline_scaleup/02_run_smoke_100.sh").read_text(encoding="utf-8")
    assert '"$ROOT/scripts/offline_scaleup/resume_incomplete_run.sh $ARTIFACT_ROOT $WORKERS smoke-100"' in script


def test_persistent_worker_reuse_matches_isolated_results_and_recycles(tmp_path: Path) -> None:
    """Reusing a child across games must not change the per-game contract.

    The fixture executor is deterministic, so any difference here would be the
    reuse mechanism rather than engine variance.  The child must also be
    discarded after its game budget and immediately after an unclean game.
    """
    population = {"schema_version": pipeline.POPULATION_SCHEMA, "semantic_population_digest": "a" * 64, "entries": []}
    population_path = tmp_path / "population.json"; population_path.write_text(json.dumps(population), encoding="utf-8")
    games = [{"game_id": f"game-{index}", "candidate_side": index % 2} for index in range(6)]
    schedule = {"schema_version": pipeline.SCHEDULE_SCHEMA, "schedule_digest": "b" * 64, "population_digest": "a" * 64,
                "planned_games": len(games), "games": games}
    outcomes = {}
    for label, reuse in (("isolated", 1), ("reused", 4)):
        run = tmp_path / label; run.mkdir()
        (run / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
        summary = pipeline.run_league(run_dir=run, population_path=population_path, repo=Path.cwd(), executor="fixture",
                                      timeout=60, max_attempts=1, workers=2, worker_reuse_games=reuse)
        assert summary["gate"] == "PASS" and summary["completed"] == len(games)
        outcomes[label] = {json.loads(line)["game_id"]: {key: json.loads(line).get(key) for key in ("status", "winner", "legal", "candidate_fault")}
                           for line in (run / "game_results.jsonl").read_text(encoding="utf-8").splitlines()}
        assert json.loads((run / "checkpoint.json").read_text())["worker_reuse_games"] == reuse
    assert outcomes["isolated"] == outcomes["reused"]

    worker = pipeline._PersistentWorker(population_path=population_path, repo=Path.cwd(), executor="fixture", reuse_games=2)
    assert worker.process is None
    worker.games = 2
    worker.close()
    assert worker.process is None
    with pytest.raises(pipeline.ContractError):
        pipeline.run_league(run_dir=tmp_path / "isolated", population_path=population_path, repo=Path.cwd(),
                            executor="fixture", timeout=1, max_attempts=1, workers=1, worker_reuse_games=0)
