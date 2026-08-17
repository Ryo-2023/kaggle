"""Contracts for TTY/periodic/off progress display and throttled summary writes."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from mage_ptcg.offline_scaleup.progress import ProgressReporter, resolve_progress_mode


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _TTYStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class _PipeStream(io.StringIO):
    def isatty(self) -> bool:
        return False


def test_tty_stream_resolves_to_tty_mode() -> None:
    assert resolve_progress_mode(progress=None, stream=_TTYStream()) == "tty"


def test_no_progress_disables_regardless_of_tty() -> None:
    assert resolve_progress_mode(progress=False, stream=_TTYStream()) == "off"


def test_non_tty_stream_resolves_to_periodic_mode() -> None:
    assert resolve_progress_mode(progress=None, stream=_PipeStream()) == "periodic"


def test_explicit_progress_forces_bar_through_a_tee_pipe() -> None:
    assert resolve_progress_mode(progress=True, stream=_PipeStream()) == "tty"


def test_off_mode_reporter_never_writes_stream() -> None:
    stream = _PipeStream()
    reporter = ProgressReporter(phase="p", total=10, progress=False, stream=stream)
    for _ in range(10):
        reporter.update(1)
    reporter.close()
    assert stream.getvalue() == ""


def test_periodic_mode_never_contains_ansi_escape() -> None:
    stream = _PipeStream()
    reporter = ProgressReporter(phase="p", total=4, progress=None, stream=stream, interval_seconds=0, percent_step=0)
    for _ in range(4):
        reporter.update(1)
    reporter.close()
    assert "\x1b" not in stream.getvalue()
    assert "PROGRESS phase=p" in stream.getvalue()


def test_periodic_mode_throttles_to_interval_or_percent_step(tmp_path: Path) -> None:
    clock = _FakeClock()
    stream = _PipeStream()
    reporter = ProgressReporter(phase="p", total=100, progress=None, stream=stream, interval_seconds=30, percent_step=5, clock=clock)
    reporter.update(1)  # first update always emits
    lines_after_first = stream.getvalue().count("\n")
    reporter.update(1)  # 2% progress, 0s elapsed: below both thresholds
    assert stream.getvalue().count("\n") == lines_after_first
    clock.advance(31)
    reporter.update(1)
    assert stream.getvalue().count("\n") == lines_after_first + 1


def test_eta_and_throughput_are_computed_from_elapsed(tmp_path: Path) -> None:
    clock = _FakeClock()
    reporter = ProgressReporter(phase="p", total=100, progress=False, clock=clock)
    clock.advance(10)
    reporter.update(10)
    assert reporter._throughput() == 1.0
    assert reporter._eta_seconds() == 90.0


def test_summary_path_is_written_atomically_and_throttled(tmp_path: Path) -> None:
    clock = _FakeClock()
    path = tmp_path / "progress_summary.json"
    reporter = ProgressReporter(phase="league", total=10, run_id="run-x", workers=2, progress=False,
                                 clock=clock, summary_path=path, summary_min_interval_seconds=5)
    reporter.update(1, valid=1, legal=1, faults=0)
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["phase"] == "league" and first["run_id"] == "run-x" and first["completed"] == 1 and first["workers"] == 2
    clock.advance(2)
    reporter.update(1, valid=2, legal=2, faults=0)
    assert json.loads(path.read_text(encoding="utf-8"))["completed"] == 1  # throttled: no write yet
    clock.advance(4)
    reporter.update(1, valid=3, legal=3, faults=0)
    assert json.loads(path.read_text(encoding="utf-8"))["completed"] == 3
    for key in ("phase", "run_id", "completed", "planned", "percent", "valid", "legal", "faults",
                "elapsed_seconds", "throughput", "eta_seconds", "workers", "updated_at"):
        assert key in first


def test_resume_initial_count_is_reflected_immediately() -> None:
    stream = _PipeStream()
    reporter = ProgressReporter(phase="p", total=900, initial=576, progress=None, stream=stream, interval_seconds=0, percent_step=0)
    reporter.update(0)
    assert reporter.completed == 576
    assert re.search(r"completed=576 planned=900", stream.getvalue())


def test_close_forces_final_periodic_line_and_summary_write(tmp_path: Path) -> None:
    stream = _PipeStream()
    path = tmp_path / "progress_summary.json"
    reporter = ProgressReporter(phase="p", total=4, progress=None, stream=stream, interval_seconds=9999, percent_step=9999, summary_path=path)
    reporter.update(2)
    before = stream.getvalue()
    reporter.close()
    assert stream.getvalue() != before
    assert path.exists()


import json as _json

from mage_ptcg.offline_scaleup.pipeline import RESULT_SCHEMA, build_schedule, run_league, summarize_run

ROOT = Path(__file__).resolve().parents[1]


def _tiny_population() -> dict[str, object]:
    entry = {"opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": "x",
             "deck_id": "current-deck", "deck_fingerprint": "a" * 64, "runtime_id": "r", "runtime_fingerprint": "a" * 64,
             "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE",
             "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES",
             "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []}
    return {"schema_version": "offline-scaleup-population-v2", "entries": [entry], "semantic_population_digest": "d" * 64,
            "alias_count": 0, "created_by": "test", "population_id": "population-test"}


def _setup_run(tmp_path: Path, *, games: int) -> tuple[Path, Path]:
    population = _tiny_population()
    population_path = tmp_path / "population.json"
    population_path.write_text(_json.dumps(population), encoding="utf-8")
    schedule = build_schedule(population, candidate="rule-v0-current-deck", opponents=["rule-v0-current-deck"], games=games, base_seed=5)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "schedule.json").write_text(_json.dumps(schedule), encoding="utf-8")
    return run_dir, population_path


def test_run_league_writes_progress_summary_with_required_fields(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=4)
    run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    payload = _json.loads((run_dir / "progress_summary.json").read_text(encoding="utf-8"))
    for key in ("phase", "run_id", "completed", "planned", "percent", "valid", "legal", "faults",
                "elapsed_seconds", "throughput", "eta_seconds", "workers", "updated_at", "gate"):
        assert key in payload, key
    assert payload["completed"] == 4 and payload["planned"] == 4 and payload["gate"] == "PASS"


def test_run_league_resume_initial_progress_reflects_completed_count(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=8)
    schedule = _json.loads((run_dir / "schedule.json").read_text(encoding="utf-8"))
    for job in schedule["games"][:3]:
        from mage_ptcg.offline_scaleup.pipeline import _write_jsonl_once
        _write_jsonl_once(run_dir / "game_results.jsonl", {"schema_version": RESULT_SCHEMA, **job, "status": "DONE", "legal": True,
                           "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True, "teacher_samples": [],
                           "fault": {"kind": "COMPLETED"}, "attempt_history": [], "completed_at_unix": 0.0})
    seen_initial: list[int] = []
    import mage_ptcg.offline_scaleup.pipeline as pipeline_module

    class _SpyReporter(pipeline_module.ProgressReporter):
        def __init__(self, *args, **kwargs):
            seen_initial.append(kwargs.get("initial", 0))
            super().__init__(*args, **kwargs)

    original = pipeline_module.ProgressReporter
    pipeline_module.ProgressReporter = _SpyReporter
    try:
        run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    finally:
        pipeline_module.ProgressReporter = original
    assert seen_initial == [3]


def test_run_league_updates_on_completion_not_submission_count_matches_planned(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=6)
    summary = run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=3, progress=False)
    assert summary["completed"] == 6
    payload = _json.loads((run_dir / "progress_summary.json").read_text(encoding="utf-8"))
    assert payload["completed"] == 6


def test_run_league_records_wall_clock_metrics_separately_from_individual_durations(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=4)
    summary = run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    assert summary["wall_clock_games_per_second"] is not None
    assert summary["wall_clock_seconds_per_game"] is not None
    assert summary["sum_worker_game_seconds"] is not None
    assert summary["effective_parallelism"] is not None
    timing = _json.loads((run_dir / "wall_clock_timing.json").read_text(encoding="utf-8"))
    assert len(timing["segments"]) == 1 and timing["segments"][0]["submitted_games"] == 4


def test_run_league_no_duplicate_completion_after_reporter_wiring(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=4)
    run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    resumed = run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    assert resumed["completed"] == 4
    rows = [_json.loads(line) for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
    game_ids = [row["game_id"] for row in rows]
    assert len(game_ids) == len(set(game_ids)) == 4


def test_summarize_run_alone_still_produces_schema_complete_progress_summary(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=2)
    run_league(run_dir=run_dir, population_path=population_path, repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=1, progress=False)
    summary = summarize_run(run_dir, workers=1)
    payload = _json.loads((run_dir / "progress_summary.json").read_text(encoding="utf-8"))
    assert payload["planned"] == summary["planned"] and payload["completed"] == summary["completed"] and payload["gate"] == summary["gate"]


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _observation() -> dict[str, object]:
    player = lambda card: {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def test_train_student_v1_reports_epoch_progress_without_ansi(tmp_path: Path, capsys) -> None:
    from mage_ptcg.offline_scaleup.pipeline import DATASET_SCHEMA, train_student_v1
    from mage_ptcg.student.dataset import build_rule_bc_example
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    dataset_path = tmp_path / "dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for split in ("train", "train", "validation"):
            handle.write(_json.dumps({"schema_version": DATASET_SCHEMA, "split": split, "rule_bc_example": example.to_dict()}) + "\n")
    train_student_v1(dataset=dataset_path, model_dir=tmp_path / "model", epochs=2, learning_rate=0.1, progress=None, progress_interval_seconds=0)
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "PROGRESS phase=train-student-v1" in captured.err


def test_evaluate_holdout_reports_per_record_progress_without_ansi(tmp_path: Path, capsys) -> None:
    from mage_ptcg.offline_scaleup.pipeline import DATASET_SCHEMA, evaluate_holdout, train_student_v1
    from mage_ptcg.student.dataset import build_rule_bc_example
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    dataset_path = tmp_path / "dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for split in ("train", "validation", "test", "test", "opponent_holdout", "deck_holdout"):
            handle.write(_json.dumps({"schema_version": DATASET_SCHEMA, "split": split, "rule_bc_example": example.to_dict()}) + "\n")
    train_student_v1(dataset=dataset_path, model_dir=tmp_path / "model", epochs=1, learning_rate=0.1, progress=False)
    evaluate_holdout(dataset=dataset_path, model_path=tmp_path / "model" / "student_v1_model.json",
                      output=tmp_path / "holdout.json", progress=None, progress_interval_seconds=0)
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "PROGRESS phase=evaluate-holdout:test" in captured.err
    assert "PROGRESS phase=evaluate-holdout:opponent_holdout" in captured.err
    assert "PROGRESS phase=evaluate-holdout:deck_holdout" in captured.err
    assert "fallback=0" in captured.err
