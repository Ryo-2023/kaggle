# Offline Scale-up Progress Display v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add practical, log-safe progress display (TTY dynamic bar / non-TTY compact periodic lines / fully off) to every long-running offline scale-up phase: run-league, resume-league, export-dataset-v2-split, train-student-v1, evaluate-holdout, and the 10,000-game generation script (which is just run-league with a big schedule).

**Architecture:** A single new module `mage_ptcg.offline_scaleup.progress` owns mode resolution (tty/periodic/off) and a `ProgressReporter` class that both drives stderr output and, when given a `summary_path`, throttle-writes `progress_summary.json`. Exactly one `ProgressReporter` is constructed per phase invocation in the parent process; workers never touch it. `run_league` switches its per-batch `ThreadPoolExecutor.map` to `submit` + `as_completed` so progress increments happen strictly on completion, not submission, and resume runs start the reporter's `initial` at the already-completed count. `student/model.py` and `student/evaluation.py` gain small optional callback hooks (`on_epoch`, `on_example`) so `pipeline.py` can report per-epoch/per-record progress without those modules depending on `pipeline.py` or `tqdm`.

**Tech Stack:** Python 3.12 stdlib (`time`, `os`, `json`, `concurrent.futures.as_completed`), `tqdm` (already pinned), `pytest`.

## Global Constraints

- Branch: `local/offline-scaleup-v2`. Local commits only. No push, no upstream, no remote branch, no PR, no merge to any canonical branch.
- Do not modify or stage existing untracked files present at session start (`.codex/hooks.json`, `.codex/hooks/`, `generate_audit_artifacts.py`, `o6_continue_after_team_permission.md*`, `pokemon_team_agents_internal_v1.yaml*`, `scripts/build_o6_taxonomy.py`).
- Protected files — never touch: `main.py`, `deck.csv`, `agents/rule_agent.py`, `agents/rule_agent_v1.py`, `src/mage_ptcg/evaluation/promotion.py`.
- No 1-record/1-game detail lines to stdout; worker stdout must never stream to the parent's screen; fault detail stays capped at 5 samples in existing summary files; progress bar history must not flood logs (periodic mode: ≤1 line per 30s or per 5% progress).
- Progress bars/periodic lines go to **stderr**. JSON results / machine-readable summaries stay on **stdout** or dedicated files. No ANSI escape sequences in non-TTY output.
- All new/changed CLI options must be backward compatible: no existing positional argument or default behavior changes for callers that pass no progress flags.
- Real CABT execution in this task is capped at 4 games total; no large real run or GPU training is re-executed.
- Commit messages follow `AGENTS.md`'s `<type>(<scope>): <summary>` convention, in Japanese, no emoji.

---

## Investigation findings

- `src/mage_ptcg/offline_scaleup/pipeline.py` is now ~1006 lines. Relevant existing pieces: `run_league` (`:563`, `ThreadPoolExecutor(max_workers=workers)` + `pool.map` per batch of size `workers`, writes results only after the whole batch resolves), `summarize_run` (`:538`, writes `run_dir/progress_summary.json` once at the very end with only `{planned, completed, gate}`), `export_dataset_v2` (`:862`, already uses raw `tqdm(..., disable=not show_progress)` for two loops — build records, write records), `train_student_v1` (`:931`, calls `train_model` once, no incremental hooks), `evaluate_holdout` (`:950`, calls `evaluate_model` once, no incremental hooks).
- `grep -rn "progress_summary"` across the repo shows exactly one write site (`pipeline.py:558`) and zero readers — safe to extend its schema additively (add fields, keep `planned`/`completed`/`gate`).
- `test_offline_scaleup_worker_contract.py::test_failure_summary_and_resume_skip_completed` asserts `calls == ["one", "two"]` with `workers=1`; since batch size equals `workers`, switching batch collection from `pool.map` (submission order) to `submit`+`as_completed` (completion order) cannot reorder anything when `workers=1` (a batch of exactly one job). No test anywhere asserts an ordering of rows within `game_results.jsonl` — safe to let write order follow completion order for `workers>1`; correctness only depends on `game_id` set membership, already enforced by the existing duplicate-completion check.
- `src/mage_ptcg/student/model.py::train_model` runs a pure-Python full-batch gradient loop per epoch (no NumPy despite the module docstring) — a natural place to add a cheap `on_epoch` callback since the per-example softmax/target-probability values needed for a loss number are already computed in the loop.
- `src/mage_ptcg/student/evaluation.py::evaluate_model` already tracks running `legal`/`top1` counters inside its per-example loop — a natural place to add a cheap `on_example` callback. `fallback_rate` is hardcoded to `0.0` in this offline evaluator (no live fallback path exists offline); the progress display will honestly show `fallback=0` rather than fabricate a fallback signal.
- `requirements.txt` already pins `tqdm==4.68.4`; no new dependency needed.
- Shell scripts needing pass-through: `02_run_smoke_100.sh`, `08_run_expanded_stability_1000.sh`, `04_export_dataset.sh`, `05_train_student_v1.sh`, `06_evaluate_holdout.sh`, `07_run_generation_10000.sh`, `resume_incomplete_run.sh`. All invoke `python3 -m mage_ptcg.offline_scaleup <verb> ...` with fixed positional args (`$1`=artifact-root, `$2`=workers, sometimes `$3`=dataset); appending `"${@:N}"` (all args beyond the fixed ones) to the python invocation lets a caller append `--progress`/`--no-progress`/`--progress-interval-seconds <N>` without touching existing positional-arg callers.

---

## Task 1: `progress.py` — mode resolution + `ProgressReporter` core

**Files:**
- Create: `src/mage_ptcg/offline_scaleup/progress.py`
- Test: `tests/test_offline_scaleup_progress.py` (new)

**Interfaces:**
- Produces: `resolve_progress_mode(*, progress: bool | None, stream) -> str` (`"tty"`/`"periodic"`/`"off"`)
- Produces: `default_interval_seconds() -> float`
- Produces: `class ProgressReporter` with `__init__(*, phase, total, initial=0, run_id=None, workers=None, unit="item", progress=None, stream=None, interval_seconds=None, percent_step=5.0, summary_path=None, summary_min_interval_seconds=5.0, clock=time.monotonic)`, `.update(n=1, **fields)`, `.close()`, and read-only properties `.completed`, `.mode`.

- [ ] **Step 1: Write the module**

```python
"""Bounded, log-safe progress reporting for long-running offline scale-up phases.

Exactly one reporter is created per phase invocation in the parent process;
workers never construct or touch a reporter. TTY output uses a live tqdm
bar; non-TTY output emits compact, ANSI-free periodic lines so redirected
logs stay small (throttled to at most one line per interval or per percent
step, whichever comes first).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from tqdm import tqdm


def resolve_progress_mode(*, progress: bool | None, stream: TextIO) -> str:
    """Return "tty", "periodic", or "off".

    Precedence: explicit False (--no-progress) always wins. Otherwise, an
    explicit True (--progress) or the OFFLINE_SCALEUP_PROGRESS env var
    enables progress; with neither given progress defaults to enabled and
    the *rendering* style (dynamic bar vs. periodic lines) follows whether
    `stream` is a real terminal.
    """
    if progress is False:
        return "off"
    if progress is None:
        env = os.environ.get("OFFLINE_SCALEUP_PROGRESS")
        progress = (env.strip() not in {"0", "", "false", "False"}) if env is not None else True
    if not progress:
        return "off"
    try:
        is_tty = bool(stream.isatty())
    except (AttributeError, ValueError):
        is_tty = False
    return "tty" if is_tty else "periodic"


def default_interval_seconds() -> float:
    env = os.environ.get("OFFLINE_SCALEUP_PROGRESS_INTERVAL")
    if env:
        try:
            return max(1.0, float(env))
        except ValueError:
            pass
    return 30.0


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temp, path)


class ProgressReporter:
    def __init__(self, *, phase: str, total: int, initial: int = 0, run_id: str | None = None,
                 workers: int | None = None, unit: str = "item", progress: bool | None = None,
                 stream: TextIO | None = None, interval_seconds: float | None = None,
                 percent_step: float = 5.0, summary_path: Path | None = None,
                 summary_min_interval_seconds: float = 5.0, clock=time.monotonic) -> None:
        self.phase = phase
        self.total = max(0, total)
        self.run_id = run_id or phase
        self.workers = workers
        self.unit = unit
        self._stream = stream if stream is not None else sys.stderr
        self.mode = resolve_progress_mode(progress=progress, stream=self._stream)
        self.interval_seconds = interval_seconds if interval_seconds is not None else default_interval_seconds()
        self.percent_step = percent_step
        self.summary_path = summary_path
        self.summary_min_interval_seconds = summary_min_interval_seconds
        self._clock = clock
        self._start = clock()
        self._initial = initial
        self.completed = initial
        self.fields: dict[str, Any] = {"workers": workers} if workers is not None else {}
        self._last_emit_time = self._start
        self._last_emit_percent = -1.0
        self._last_summary_write = float("-inf")
        self._emitted_any = False
        self._bar = None
        if self.mode == "tty":
            self._bar = tqdm(total=self.total, initial=self._initial, unit=self.unit, file=self._stream,
                              desc=self.phase, dynamic_ncols=True,
                              bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]")
            if self.fields:
                self._bar.set_postfix(**self.fields, refresh=True)

    def _percent(self) -> float:
        return 0.0 if self.total <= 0 else min(100.0, 100.0 * self.completed / self.total)

    def _elapsed(self) -> float:
        return max(0.0, self._clock() - self._start)

    def _throughput(self) -> float | None:
        done = self.completed - self._initial
        elapsed = self._elapsed()
        return (done / elapsed) if done > 0 and elapsed > 0 else None

    def _eta_seconds(self) -> float | None:
        throughput = self._throughput()
        if not throughput:
            return None
        return max(0, self.total - self.completed) / throughput

    def update(self, n: int = 1, **fields: Any) -> None:
        self.completed = min(self.total, self.completed + n) if self.total else self.completed + n
        self.fields.update(fields)
        if self._bar is not None:
            self._bar.set_postfix(**{k: v for k, v in self.fields.items() if v is not None}, refresh=False)
            self._bar.update(n)
        elif self.mode == "periodic":
            self._maybe_emit_periodic()
        self._maybe_write_summary()

    def _maybe_emit_periodic(self, *, force: bool = False) -> None:
        now = self._clock()
        percent = self._percent()
        due = force or not self._emitted_any or (now - self._last_emit_time) >= self.interval_seconds \
            or (percent - self._last_emit_percent) >= self.percent_step or self.completed >= self.total
        if not due:
            return
        throughput = self._throughput()
        eta = self._eta_seconds()
        parts = [f"PROGRESS phase={self.phase}", f"completed={self.completed}", f"planned={self.total}", f"percent={percent:.1f}"]
        for key, value in self.fields.items():
            if value is None:
                continue
            parts.append(f"{key}={value:.4f}" if isinstance(value, float) else f"{key}={value}")
        parts.append(f"throughput_{self.unit}s_per_second=" + (f"{throughput:.4f}" if throughput is not None else "n/a"))
        parts.append("eta_seconds=" + (str(int(eta)) if eta is not None else "n/a"))
        print(" ".join(parts), file=self._stream, flush=True)
        self._last_emit_time, self._last_emit_percent, self._emitted_any = now, percent, True

    def _summary_payload(self) -> dict[str, Any]:
        return {"phase": self.phase, "run_id": self.run_id, "completed": self.completed, "planned": self.total,
                "percent": round(self._percent(), 2), "valid": self.fields.get("valid"), "legal": self.fields.get("legal"),
                "faults": self.fields.get("faults"), "elapsed_seconds": round(self._elapsed(), 3),
                "throughput": self._throughput(), "eta_seconds": self._eta_seconds(), "workers": self.workers,
                "updated_at": time.time()}

    def _maybe_write_summary(self, *, force: bool = False) -> None:
        if self.summary_path is None:
            return
        now = self._clock()
        if not force and (now - self._last_summary_write) < self.summary_min_interval_seconds:
            return
        _atomic_write_json(self.summary_path, self._summary_payload())
        self._last_summary_write = now

    def close(self) -> None:
        if self._bar is not None:
            self._bar.close()
        elif self.mode == "periodic":
            self._maybe_emit_periodic(force=True)
        self._maybe_write_summary(force=True)


__all__ = ["ProgressReporter", "default_interval_seconds", "resolve_progress_mode"]
```

- [ ] **Step 2: Write the tests**

```python
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
```

- [ ] **Step 3: Run tests to verify they fail (module doesn't exist yet)**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'mage_ptcg.offline_scaleup.progress'`

- [ ] **Step 4: Create the module with Step 1's content, then run again**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/progress.py tests/test_offline_scaleup_progress.py
git commit -m "$(cat <<'EOF'
feat(offline-scaleup): TTY／非TTY/off対応のProgressReporterを追加

- periodic modeはANSI escapeを出さず interval秒または5%ごとに1行だけ出力する
- summary_path指定時はprogress_summary.jsonを最短5秒間隔でatomic更新する
EOF
)"
```

---

## Task 2: Wire `ProgressReporter` into `run_league`/`resume-league`, extend `summarize_run`

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py` (`run_league`, `summarize_run`, `_parser`, `main`)
- Test: `tests/test_offline_scaleup_progress.py`

**Interfaces:**
- Modifies: `run_league(*, run_dir, population_path, repo, executor, timeout, max_attempts, workers=2, progress=None, progress_interval_seconds=None) -> dict[str, Any]` (new keyword-only params, backward compatible)
- Modifies: `summarize_run(run_dir, *, workers=None) -> dict[str, Any]` (new keyword-only param, backward compatible)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_offline_scaleup_progress.py`:

```python
import json as _json

from mage_ptcg.offline_scaleup.pipeline import RESULT_SCHEMA, build_schedule, run_league, summarize_run


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
    run_league(run_dir=run_dir, population_path=population_path, repo=tmp_path, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
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
        run_league(run_dir=run_dir, population_path=population_path, repo=tmp_path, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    finally:
        pipeline_module.ProgressReporter = original
    assert seen_initial == [3]


def test_run_league_updates_on_completion_not_submission_count_matches_planned(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=6)
    summary = run_league(run_dir=run_dir, population_path=population_path, repo=tmp_path, executor="fixture", timeout=5, max_attempts=1, workers=3, progress=False)
    assert summary["completed"] == 6
    payload = _json.loads((run_dir / "progress_summary.json").read_text(encoding="utf-8"))
    assert payload["completed"] == 6


def test_run_league_no_duplicate_completion_after_reporter_wiring(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=4)
    run_league(run_dir=run_dir, population_path=population_path, repo=tmp_path, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    resumed = run_league(run_dir=run_dir, population_path=population_path, repo=tmp_path, executor="fixture", timeout=5, max_attempts=1, workers=2, progress=False)
    assert resumed["completed"] == 4
    ids = [row["game_id"] for row in _json.loads(line) and [_json.loads(line)] or [] for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [_json.loads(line) for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
    game_ids = [row["game_id"] for row in rows]
    assert len(game_ids) == len(set(game_ids)) == 4


def test_summarize_run_alone_still_produces_schema_complete_progress_summary(tmp_path: Path) -> None:
    run_dir, population_path = _setup_run(tmp_path, games=2)
    run_league(run_dir=run_dir, population_path=population_path, repo=tmp_path, executor="fixture", timeout=5, max_attempts=1, workers=1, progress=False)
    summary = summarize_run(run_dir, workers=1)
    payload = _json.loads((run_dir / "progress_summary.json").read_text(encoding="utf-8"))
    assert payload["planned"] == summary["planned"] and payload["completed"] == summary["completed"] and payload["gate"] == summary["gate"]
```

(Simplify the ID-uniqueness check in `test_run_league_no_duplicate_completion_after_reporter_wiring` — the `ids = [...]` walrus-style line above is invalid Python; remove it, keep only the `rows`/`game_ids` lines.)

- [ ] **Step 2: Fix the test file — remove the invalid line**

In `tests/test_offline_scaleup_progress.py`, delete this stray line from the test written in Step 1 (it was a drafting mistake, not valid Python):

```python
    ids = [row["game_id"] for row in _json.loads(line) and [_json.loads(line)] or [] for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
```

Keep only:
```python
    rows = [_json.loads(line) for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines()]
    game_ids = [row["game_id"] for row in rows]
    assert len(game_ids) == len(set(game_ids)) == 4
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py -v -k "run_league or summarize_run"`
Expected: FAIL — `run_league() got an unexpected keyword argument 'progress'` (and `pipeline_module.ProgressReporter` not found)

- [ ] **Step 4: Import `ProgressReporter` into `pipeline.py` and rewrite `run_league`/`summarize_run`**

In `src/mage_ptcg/offline_scaleup/pipeline.py`, add the import near the top (after the `tqdm` import):

```python
from mage_ptcg.offline_scaleup.progress import ProgressReporter
```

Replace `summarize_run` (`:538-560`) with:

```python
def summarize_run(run_dir: Path, *, workers: int | None = None) -> dict[str, Any]:
    schedule = _read_json(run_dir / "schedule.json")
    rows = list(_jsonl(run_dir / "game_results.jsonl"))
    ids = [str(row.get("game_id")) for row in rows]
    duplicate = len(ids) - len(set(ids))
    terminal = [row for row in rows if row.get("status") in TERMINAL]
    faults = Counter(str(row.get("fault", {}).get("kind", "NONE")) for row in rows)
    valid = [row for row in terminal if row.get("status") == "DONE" and row.get("legal") is True and not row.get("candidate_fault")]
    latencies = [float(row["elapsed_seconds"]) for row in rows if isinstance(row.get("elapsed_seconds"), (int, float))]
    summary = {"schema_version": "offline-scaleup-run-summary-v2", "run_id": run_dir.name, "phase": "league", "planned": schedule["planned_games"], "completed": len(rows),
               "terminal": len(terminal), "missing": schedule["planned_games"] - len(set(ids)), "valid_legal_games": len(valid),
               "legal_games": sum(row.get("legal") is True for row in terminal), "candidate_faults": sum(bool(row.get("candidate_fault")) for row in rows),
               "mapping_failures": sum(row.get("mapping_valid") is False for row in rows), "score_identity_failures": sum(row.get("score_identity_valid") is False for row in rows),
               "duplicate_completion": duplicate, "fault_counts": dict(sorted(faults.items())),
               "throughput_games_per_second": round(len(rows) / sum(latencies), 5) if latencies and sum(latencies) > 0 else None,
               "latency_seconds": {"p50": statistics.median(latencies) if latencies else None, "p95": sorted(latencies)[max(0, int(.95 * len(latencies)) - 1)] if latencies else None}}
    gate = summary["completed"] == summary["planned"] and summary["legal_games"] == summary["planned"] and not any(summary[key] for key in ("candidate_faults", "mapping_failures", "score_identity_failures", "duplicate_completion"))
    summary["gate"] = "PASS" if gate else "BLOCKED"
    _atomic_json(run_dir / "run_summary.json", summary)
    _atomic_json(run_dir / "fault_summary.json", {"schema_version": "offline-scaleup-fault-summary-v1", "run_id": run_dir.name, "fault_counts": summary["fault_counts"], "sample_limit": 5})
    throughput = summary["throughput_games_per_second"]
    remaining = max(0, summary["planned"] - summary["completed"])
    eta_seconds = (remaining / throughput) if throughput else (0 if remaining == 0 else None)
    elapsed_seconds = (summary["completed"] / throughput) if throughput else None
    fault_total = sum(count for kind, count in summary["fault_counts"].items() if kind not in ("NONE", "COMPLETED"))
    _atomic_json(run_dir / "progress_summary.json", {"phase": "league", "run_id": summary["run_id"], "completed": summary["completed"],
        "planned": summary["planned"], "percent": round(100.0 * summary["completed"] / summary["planned"], 2) if summary["planned"] else 0.0,
        "valid": summary["valid_legal_games"], "legal": summary["legal_games"], "faults": fault_total, "elapsed_seconds": elapsed_seconds,
        "throughput": throughput, "eta_seconds": eta_seconds, "workers": workers, "updated_at": time.time(), "gate": summary["gate"]})
    (run_dir / "next_command.txt").write_text("resume-league" if summary["gate"] != "PASS" else "export-dataset", encoding="utf-8")
    return summary
```

Replace `run_league` (`:563-603`) with:

```python
def run_league(*, run_dir: Path, population_path: Path, repo: Path, executor: str, timeout: float, max_attempts: int,
                workers: int = 2, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    schedule = _read_json(run_dir / "schedule.json"); population = _read_json(population_path)
    if schedule.get("population_digest") != population.get("semantic_population_digest"):
        raise ContractError("schedule/population digest mismatch")
    records_path = run_dir / "game_results.jsonl"; existing = list(_jsonl(records_path)); completed = {str(row.get("game_id")) for row in existing}
    attempts_path = run_dir / "attempts.jsonl"
    if len(completed) != len(existing):
        raise ContractError("duplicate completion already exists; refusing resume")
    if workers < 1:
        raise ContractError("workers must be positive")
    pending = [job for job in schedule["games"] if job["game_id"] not in completed]

    def execute_one(job: Mapping[str, Any]) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        outcome: dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            outcome, fault = _run_worker(job, population_path=population_path, repo=repo, executor=executor, timeout=timeout, scratch=run_dir / "scratch")
            attempts.append({"attempt": attempt, **fault})
            _write_jsonl_once(attempts_path, {"schema_version": "offline-scaleup-attempt-v1", "game_id": job["game_id"], "attempt": attempt, **fault})
            if outcome.get("status") == "DONE" or fault["kind"] != "HARD_TIMEOUT":
                break
        return {"schema_version": RESULT_SCHEMA, **job, **outcome, "attempt_history": attempts, "fault": attempts[-1], "completed_at_unix": time.time()}

    valid_count = sum(1 for row in existing if row.get("status") == "DONE" and row.get("legal") is True and not row.get("candidate_fault"))
    legal_count = sum(1 for row in existing if row.get("legal") is True)
    fault_count = sum(1 for row in existing if row.get("fault", {}).get("kind") not in (None, "NONE", "COMPLETED"))
    reporter = ProgressReporter(phase=run_dir.name, total=schedule["planned_games"], initial=len(completed), run_id=run_dir.name,
                                 workers=workers, unit="game", progress=progress, interval_seconds=progress_interval_seconds,
                                 summary_path=run_dir / "progress_summary.json")
    reporter.update(0, valid=valid_count, legal=legal_count, faults=fault_count)
    # A bounded batch keeps parent memory independent of league size while
    # retaining one fresh subprocess per game; as_completed() reports each
    # game's progress the instant it finishes, not when the batch is submitted.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        for start in range(0, len(pending), workers):
            batch = pending[start:start + workers]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(execute_one, job): job for job in batch}
                for future in as_completed(futures):
                    record = future.result()
                    if record["game_id"] in completed:
                        raise ContractError("duplicate terminal completion rejected")
                    _write_jsonl_once(records_path, record); completed.add(record["game_id"])
                    _atomic_json(run_dir / "checkpoint.json", {"schedule_digest": schedule["schedule_digest"], "completed_game_ids": sorted(completed)})
                    if record.get("status") == "DONE" and record.get("legal") is True and not record.get("candidate_fault"):
                        valid_count += 1
                    if record.get("legal") is True:
                        legal_count += 1
                    if record.get("fault", {}).get("kind") not in (None, "NONE", "COMPLETED"):
                        fault_count += 1
                    reporter.update(1, valid=valid_count, legal=legal_count, faults=fault_count)
    finally:
        reporter.close()
    summary = summarize_run(run_dir, workers=workers)
    if summary["gate"] != "PASS":
        _atomic_json(run_dir / "run_failure.json", {"schema_version": "offline-scaleup-run-failure-v1", "stage": "run-league", "exception_type": "LeagueGateFailure", "message": "league completed with non-passing gate", "game_id": next((row.get("game_id") for row in list(_jsonl(records_path)) if row.get("status") != "DONE"), None), "returncode": 2, "completed": summary["completed"], "planned": summary["planned"], "schedule_digest": schedule["schedule_digest"], "population_digest": schedule["population_digest"], "resumable": True, "next_command": "resume-league"})
    return summary
```

- [ ] **Step 5: Add CLI flags for `run-league`/`resume-league`**

In `_parser()`, replace:

```python
    for name in ("run-league", "resume-league"):
        run = sub.add_parser(name); run.add_argument("--run-dir", type=Path, required=True); run.add_argument("--population", type=Path, required=True); run.add_argument("--repo", type=Path, default=Path.cwd()); run.add_argument("--executor", choices=("cabt", "fixture"), default="cabt"); run.add_argument("--timeout", type=float, default=180.0); run.add_argument("--max-attempts", type=int, default=2); run.add_argument("--workers", type=int, default=2)
```

with:

```python
    for name in ("run-league", "resume-league"):
        run = sub.add_parser(name); run.add_argument("--run-dir", type=Path, required=True); run.add_argument("--population", type=Path, required=True); run.add_argument("--repo", type=Path, default=Path.cwd()); run.add_argument("--executor", choices=("cabt", "fixture"), default="cabt"); run.add_argument("--timeout", type=float, default=180.0); run.add_argument("--max-attempts", type=int, default=2); run.add_argument("--workers", type=int, default=2)
        run.add_argument("--progress", action="store_true"); run.add_argument("--no-progress", action="store_true"); run.add_argument("--progress-interval-seconds", type=float, default=None)
```

In `main()`, replace:

```python
        elif args.command in {"run-league", "resume-league"}:
            result = run_league(run_dir=args.run_dir, population_path=args.population, repo=args.repo.resolve(), executor=args.executor, timeout=args.timeout, max_attempts=args.max_attempts, workers=args.workers)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
```

with:

```python
        elif args.command in {"run-league", "resume-league"}:
            progress = False if args.no_progress else (True if args.progress else None)
            result = run_league(run_dir=args.run_dir, population_path=args.population, repo=args.repo.resolve(), executor=args.executor, timeout=args.timeout, max_attempts=args.max_attempts, workers=args.workers, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py tests/test_offline_scaleup_pipeline.py tests/test_offline_scaleup_worker_contract.py tests/test_offline_scaleup_dataset_split.py -v`
Expected: all pass, including the pre-existing `test_failure_summary_and_resume_skip_completed` order assertion.

- [ ] **Step 7: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py tests/test_offline_scaleup_progress.py
git commit -m "$(cat <<'EOF'
feat(offline-scaleup): run-league/resume-leagueへ完了ベースの進捗表示を統合

- as_completed()で結果受信時にのみ1件進め、resumeはinitialをcompleted数から復元する
- summarize_runのprogress_summary.jsonを必須field全てへ拡張する（後方互換）
EOF
)"
```

---

## Task 3: Wire `ProgressReporter` into `export_dataset_v2` and legacy `export_dataset`

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py` (`export_dataset`, `export_dataset_v2`, `_parser`, `main`)
- Test: `tests/test_offline_scaleup_dataset_split.py`, `tests/test_offline_scaleup_progress.py`

**Interfaces:**
- Modifies: `export_dataset_v2(*, run_dir, population_path, artifact_root, workers=None, show_progress=True, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]` (new keyword-only params; `show_progress` kept for the 16 existing call sites — `show_progress=False` maps to `progress=False` unless `progress` is explicitly given)
- Modifies: `export_dataset(*, run_dir, output, progress=None, progress_interval_seconds=None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_offline_scaleup_dataset_split.py`:

```python
def test_export_dataset_v2_periodic_progress_has_no_ansi_and_reports_split(tmp_path: Path, capsys) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "periodic-progress"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2,
                       progress=None, progress_interval_seconds=0)
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "PROGRESS phase=dataset-build" in captured.err or "PROGRESS phase=dataset-write" in captured.err
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v -k periodic_progress`
Expected: FAIL — `export_dataset_v2() got an unexpected keyword argument 'progress'`

- [ ] **Step 3: Rewrite `export_dataset` and `export_dataset_v2`**

Replace `export_dataset` (`:633-662`) with:

```python
def export_dataset(*, run_dir: Path, output: Path, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    population_digest = _read_json(run_dir / "schedule.json")["population_digest"]
    valid_games = _valid_terminal_games(run_dir)
    reporter = ProgressReporter(phase="export-dataset", total=len(valid_games), run_id=run_dir.name, unit="game",
                                 progress=progress, interval_seconds=progress_interval_seconds)
    records: list[dict[str, Any]] = []
    for game in valid_games:
        for sample in game.get("teacher_samples", []):
            records.append(_teacher_dataset_record(game, sample, population_digest))
        reporter.update(1)
    reporter.close()
    if not records:
        raise ContractError("no valid teacher decisions available; league may be valid but has no observable choices")
    episodes = sorted({str(record["episode_id"]) for record in records})
    # The whole episode, never a decision row, owns its split.  The first two
    # stable hash ranks reserve test/validation whenever enough episodes exist.
    ranked = sorted(episodes, key=lambda item: _digest(item, "split"))
    assignments = {episode: "train" for episode in episodes}
    if len(ranked) >= 3:
        assignments[ranked[0]], assignments[ranked[1]] = "test", "validation"
    elif len(ranked) == 2:
        assignments[ranked[0]], assignments[ranked[1]] = "train", "validation"
    for record in records:
        record["split"] = assignments[record["episode_id"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ContractError("dataset output already exists")
    for record in records:
        _write_jsonl_once(output, record)
    counts = Counter(assignments.values())
    summary = {"schema_version": "offline-scaleup-dataset-summary-v2", "records": len(records), "episodes": len(episodes), "splits": dict(counts),
               "illegal_selected_actions": 0, "quarantined_teacher_records": 0, "episode_leakage": 0, "opponent_holdout_leakage": 0, "deck_holdout_leakage": 0,
               "parse_valid": True, "dataset": str(output)}
    _atomic_json(output.with_suffix(".summary.json"), summary)
    return summary
```

In `export_dataset_v2` (`:862-928`), change the signature line and the two progress blocks. Replace:

```python
def export_dataset_v2(*, run_dir: Path, population_path: Path, artifact_root: Path, workers: int | None = None, show_progress: bool = True) -> dict[str, Any]:
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    population = _read_json(population_path)
    entries = _population_entries_by_id(population)
    dataset_path = artifact_root / "datasets" / "stability-900-split-v2.jsonl"
    if dataset_path.exists():
        raise ContractError("dataset output already exists")
    valid_games = _valid_terminal_games(run_dir)
    jobs = [(game, manifest["population_digest"]) for game in valid_games]
    resolved_workers = workers if workers is not None else default_worker_count()
    progress = tqdm(total=len(jobs), desc="offline-scaleup: building dataset records", disable=not show_progress)
    per_game_records: list[list[dict[str, Any]]]
    if resolved_workers <= 1 or len(jobs) < 2:
        per_game_records = []
        for job in jobs:
            per_game_records.append(_build_episode_records(job))
            progress.update(1)
    else:
        from concurrent.futures import ProcessPoolExecutor
        per_game_records = [[] for _ in jobs]
        with ProcessPoolExecutor(max_workers=resolved_workers) as pool:
            for index, result in enumerate(pool.map(_build_episode_records, jobs)):
                per_game_records[index] = result
                progress.update(1)
    progress.close()
```

with:

```python
def export_dataset_v2(*, run_dir: Path, population_path: Path, artifact_root: Path, workers: int | None = None,
                       show_progress: bool = True, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    resolved_progress = progress if progress is not None else (None if show_progress else False)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    population = _read_json(population_path)
    entries = _population_entries_by_id(population)
    dataset_path = artifact_root / "datasets" / "stability-900-split-v2.jsonl"
    if dataset_path.exists():
        raise ContractError("dataset output already exists")
    valid_games = _valid_terminal_games(run_dir)
    jobs = [(game, manifest["population_digest"]) for game in valid_games]
    resolved_workers = workers if workers is not None else default_worker_count()
    build_reporter = ProgressReporter(phase="dataset-build", total=len(jobs), run_id=run_dir.name, workers=resolved_workers,
                                       unit="episode", progress=resolved_progress, interval_seconds=progress_interval_seconds)
    per_game_records: list[list[dict[str, Any]]]
    if resolved_workers <= 1 or len(jobs) < 2:
        per_game_records = []
        for job in jobs:
            per_game_records.append(_build_episode_records(job))
            build_reporter.update(1)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        per_game_records = [[] for _ in jobs]
        with ProcessPoolExecutor(max_workers=resolved_workers) as pool:
            futures = {pool.submit(_build_episode_records, job): index for index, job in enumerate(jobs)}
            for future in as_completed(futures):
                per_game_records[futures[future]] = future.result()
                build_reporter.update(1)
    build_reporter.close()
```

Then replace the write loop:

```python
    if not records:
        raise ContractError("no valid teacher decisions available for split dataset")
    for record in tqdm(records, desc="offline-scaleup: writing dataset", disable=not show_progress):
        _write_jsonl_once(dataset_path, record)
```

with:

```python
    if not records:
        raise ContractError("no valid teacher decisions available for split dataset")
    write_reporter = ProgressReporter(phase="dataset-write", total=len(records), run_id=run_dir.name, unit="record",
                                       progress=resolved_progress, interval_seconds=progress_interval_seconds)
    for record in records:
        _write_jsonl_once(dataset_path, record)
        write_reporter.update(1, split=record["split"])
    write_reporter.close()
```

`tqdm` remains imported (still used indirectly via `ProgressReporter`); the direct `from tqdm import tqdm` usage in this function is gone, but the module-level `from tqdm import tqdm` import stays because `progress.py` uses it, not `pipeline.py` — check whether `pipeline.py`'s own `tqdm` import is now dead and remove it if so.

- [ ] **Step 4: Remove the now-unused direct `tqdm` import from `pipeline.py` if nothing else in the file uses it**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && grep -n "tqdm" src/mage_ptcg/offline_scaleup/pipeline.py`
If the only remaining hit is the `from tqdm import tqdm` import line itself, delete that line from `pipeline.py` (Task 2/3 no longer call `tqdm(...)` directly there — `ProgressReporter` owns that now).

- [ ] **Step 5: Add CLI flags for `export-dataset` (v1) and extend `export-dataset-v2-split`**

In `_parser()`, replace:

```python
    export = sub.add_parser("export-dataset"); export.add_argument("--run-dir", type=Path, required=True); export.add_argument("--output", type=Path, required=True)
    export_v2 = sub.add_parser("export-dataset-v2-split")
    export_v2.add_argument("--run-dir", type=Path, required=True)
    export_v2.add_argument("--population", type=Path, required=True)
    export_v2.add_argument("--artifact-root", type=Path, required=True)
    export_v2.add_argument("--workers", type=int, default=None)
    export_v2.add_argument("--no-progress", action="store_true")
```

with:

```python
    export = sub.add_parser("export-dataset"); export.add_argument("--run-dir", type=Path, required=True); export.add_argument("--output", type=Path, required=True)
    export.add_argument("--progress", action="store_true"); export.add_argument("--no-progress", action="store_true"); export.add_argument("--progress-interval-seconds", type=float, default=None)
    export_v2 = sub.add_parser("export-dataset-v2-split")
    export_v2.add_argument("--run-dir", type=Path, required=True)
    export_v2.add_argument("--population", type=Path, required=True)
    export_v2.add_argument("--artifact-root", type=Path, required=True)
    export_v2.add_argument("--workers", type=int, default=None)
    export_v2.add_argument("--progress", action="store_true"); export_v2.add_argument("--no-progress", action="store_true"); export_v2.add_argument("--progress-interval-seconds", type=float, default=None)
```

In `main()`, replace:

```python
        elif args.command == "export-dataset": result = export_dataset(run_dir=args.run_dir, output=args.output)
        elif args.command == "export-dataset-v2-split":
            result = export_dataset_v2(run_dir=args.run_dir, population_path=args.population, artifact_root=args.artifact_root, workers=args.workers, show_progress=not args.no_progress)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
```

with:

```python
        elif args.command == "export-dataset":
            progress = False if args.no_progress else (True if args.progress else None)
            result = export_dataset(run_dir=args.run_dir, output=args.output, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
        elif args.command == "export-dataset-v2-split":
            progress = False if args.no_progress else (True if args.progress else None)
            result = export_dataset_v2(run_dir=args.run_dir, population_path=args.population, artifact_root=args.artifact_root, workers=args.workers, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
```

- [ ] **Step 6: Run the full dataset-split and progress test modules**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py tests/test_offline_scaleup_progress.py tests/test_offline_scaleup_pipeline.py -v`
Expected: all pass (existing `show_progress=False` calls keep working unchanged)

- [ ] **Step 7: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py tests/test_offline_scaleup_dataset_split.py
git commit -m "$(cat <<'EOF'
feat(offline-scaleup): export-dataset／export-dataset-v2-splitへProgressReporterを統合

- build/writeを別phaseの進捗として表示し、非TTYではepisodes/records/split/throughputを周期出力する
EOF
)"
```

---

## Task 4: Epoch-level training progress (`student/model.py` callback + `train_student_v1`)

**Files:**
- Modify: `src/mage_ptcg/student/model.py` (`train_model`)
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py` (`train_student_v1`, `_parser`, `main`)
- Test: `tests/test_student_model.py` if it exists (check first), else `tests/test_offline_scaleup_progress.py`

**Interfaces:**
- Modifies: `train_model(examples, *, epochs=120, learning_rate=0.15, on_epoch: Callable[[int, int, float, tuple[float, ...], float], None] | None = None) -> StudentV0Model` (new keyword-only param, backward compatible)
- Modifies: `train_student_v1(*, dataset, model_dir, epochs, learning_rate, progress=None, progress_interval_seconds=None) -> dict[str, Any]`

- [ ] **Step 1: Check for an existing student/model test file**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && find tests -iname "*student*"`

- [ ] **Step 2: Write the failing test** (add to whichever file Step 1 finds is appropriate, or create `tests/test_student_model_progress.py` if none fits)

```python
"""on_epoch callback contract for the Student v1 trainer."""
from __future__ import annotations

from mage_ptcg.student.dataset import build_rule_bc_example
from mage_ptcg.student.model import train_model


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _observation() -> dict[str, object]:
    player = lambda card: {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def test_train_model_invokes_on_epoch_with_finite_loss_and_snapshot_weights() -> None:
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    calls: list[tuple[int, int, float]] = []

    def on_epoch(epoch_index, total_epochs, train_loss, weights, bias):
        calls.append((epoch_index, total_epochs, train_loss))
        assert len(weights) > 0
        assert bias == bias  # not NaN

    train_model([example], epochs=3, learning_rate=0.1, on_epoch=on_epoch)
    assert [c[0] for c in calls] == [0, 1, 2]
    assert all(c[1] == 3 for c in calls)
    assert all(loss == loss and loss != float("inf") for _, _, loss in calls)


def test_train_model_without_on_epoch_is_unaffected() -> None:
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    model = train_model([example], epochs=2, learning_rate=0.1)
    assert model is not None
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_student_model_progress.py -v`
Expected: FAIL — `train_model() got an unexpected keyword argument 'on_epoch'`

- [ ] **Step 4: Add the callback to `train_model`**

In `src/mage_ptcg/student/model.py`, replace the `train_model` function body's loop:

```python
def train_model(
    examples: Iterable[RuleBCExample],
    *,
    epochs: int = 120,
    learning_rate: float = 0.15,
    on_epoch: "Callable[[int, int, float, tuple[float, ...], float], None] | None" = None,
) -> StudentV0Model:
    """Fit deterministic full-batch cross-entropy over each legal candidate set.

    NumPy is deliberately optional here; the compact calculation is expressed
    with Python floats so the trainer and runtime share exact feature rules.
    """
    values = list(examples)
    if not values:
        raise ModelValidationError("cannot train on an empty dataset")
    if epochs < 1 or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("epochs and learning_rate must be positive")
    weights = [0.0] * MODEL_FEATURE_DIM
    bias = 0.0
    for epoch_index in range(epochs):
        gradient = [0.0] * MODEL_FEATURE_DIM
        bias_gradient = 0.0
        useful = 0
        epoch_loss = 0.0
        for example in values:
            matrix, targets = example_matrix(example)
            if not targets:
                continue
            scores = [bias + sum(weight * feature for weight, feature in zip(weights, row, strict=True)) for row in matrix]
            maximum = max(scores)
            exp_scores = [math.exp(min(80.0, score - maximum)) for score in scores]
            normalizer = sum(exp_scores)
            target_probability = 1.0 / len(targets)
            target_set = set(targets)
            example_target_probability = sum(exp_scores[index] / normalizer for index in target_set)
            epoch_loss += -math.log(max(example_target_probability, 1e-12))
            for index, row in enumerate(matrix):
                delta = exp_scores[index] / normalizer - (target_probability if index in target_set else 0.0)
                for feature_index, feature in enumerate(row):
                    gradient[feature_index] += delta * feature
                bias_gradient += delta
            useful += 1
        if useful == 0:
            raise ModelValidationError("dataset has no selectable teacher targets")
        scale = learning_rate / useful
        weights = [weight - scale * change for weight, change in zip(weights, gradient, strict=True)]
        bias -= scale * bias_gradient
        if on_epoch is not None:
            on_epoch(epoch_index, epochs, epoch_loss / useful, tuple(weights), bias)
    return StudentV0Model(tuple(weights), bias)
```

Add `Callable` to the `typing` import at the top of the file:

```python
from typing import Callable, Iterable
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_student_model_progress.py -v`
Expected: both pass

- [ ] **Step 6: Wire `train_student_v1` in `pipeline.py`**

Replace `train_student_v1` (`:931-947`) with:

```python
def train_student_v1(*, dataset: Path, model_dir: Path, epochs: int, learning_rate: float,
                      progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    train = _load_v1_examples(dataset, "train")
    validation = _load_v1_examples(dataset, "validation")
    reporter = ProgressReporter(phase="train-student-v1", total=epochs, run_id=model_dir.name, unit="epoch",
                                 progress=progress, interval_seconds=progress_interval_seconds)

    def on_epoch(epoch_index: int, total_epochs: int, train_loss: float, weights: tuple[float, ...], bias: float) -> None:
        snapshot = StudentV0Model(weights, bias)
        validation_metrics = evaluate_model(snapshot, validation)
        reporter.update(1, train_loss=round(train_loss, 6), validation_loss=round(validation_metrics["holdout_loss"], 6),
                         top1_fidelity=round(validation_metrics["teacher_top1_fidelity"], 4))

    model = train_model(train, epochs=epochs, learning_rate=learning_rate, on_epoch=on_epoch)
    reporter.close()
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "student_v1_model.json"
    if model_path.exists():
        model_path.unlink()
    model.export(model_path)
    metrics = evaluate_model(model, validation)
    report = {"schema_version": STUDENT_SCHEMA, "model_type": "legal-candidate-linear-ranking", "model_version": "student-v1",
              "feature_schema_version": "student-v0-feature-v1", "training_examples": len(train), "validation_examples": len(validation),
              "validation": metrics, "legal_rate": 1.0, "fallback": "Rule Agent v0", "model_size_bytes": model_path.stat().st_size,
              "checkpoint_resume": "deterministic full-batch retrain; no optimizer state", "device": "CPU (GPU optional external trainer is not required for this model)"}
    _atomic_json(model_dir / "training_summary.json", report)
    (model_dir / "next_command.txt").write_text("evaluate-holdout", encoding="utf-8")
    return report
```

- [ ] **Step 7: Add CLI flags for `train-student-v1`**

In `_parser()`, replace:

```python
    train = sub.add_parser("train-student-v1"); train.add_argument("--dataset", type=Path, required=True); train.add_argument("--model-dir", type=Path, required=True); train.add_argument("--epochs", type=int, default=120); train.add_argument("--learning-rate", type=float, default=.15)
```

with:

```python
    train = sub.add_parser("train-student-v1"); train.add_argument("--dataset", type=Path, required=True); train.add_argument("--model-dir", type=Path, required=True); train.add_argument("--epochs", type=int, default=120); train.add_argument("--learning-rate", type=float, default=.15)
    train.add_argument("--progress", action="store_true"); train.add_argument("--no-progress", action="store_true"); train.add_argument("--progress-interval-seconds", type=float, default=None)
```

In `main()`, replace:

```python
        elif args.command == "train-student-v1": result = train_student_v1(dataset=args.dataset, model_dir=args.model_dir, epochs=args.epochs, learning_rate=args.learning_rate)
```

with:

```python
        elif args.command == "train-student-v1":
            progress = False if args.no_progress else (True if args.progress else None)
            result = train_student_v1(dataset=args.dataset, model_dir=args.model_dir, epochs=args.epochs, learning_rate=args.learning_rate, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
```

- [ ] **Step 8: Add a pipeline-level smoke test**

Append to `tests/test_offline_scaleup_progress.py`:

```python
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
```

Note: this test needs `_card`/`_observation` helpers already defined earlier in the same file (Task 2's setup); if this test file doesn't already import `build_rule_bc_example`'s observation helpers, reuse the module-level `_observation()` defined in `tests/test_offline_scaleup_dataset_split.py`'s pattern by copying the same two small helper functions into `tests/test_offline_scaleup_progress.py` (do this once, near the top of the file, not per-test).

- [ ] **Step 9: Run all affected tests**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_student_model_progress.py tests/test_offline_scaleup_progress.py tests/test_offline_scaleup_pipeline.py -v`
Expected: all pass

- [ ] **Step 10: Commit**

```bash
git add src/mage_ptcg/student/model.py src/mage_ptcg/offline_scaleup/pipeline.py tests/test_student_model_progress.py tests/test_offline_scaleup_progress.py
git commit -m "$(cat <<'EOF'
feat(offline-scaleup): train-student-v1へepoch単位の進捗（train/validation loss, top1）を追加

- train_modelにon_epochコールバックを追加し、pipeline側でvalidation指標を都度算出する
EOF
)"
```

---

## Task 5: Per-record holdout evaluation progress (`student/evaluation.py` callback + `evaluate_holdout`)

**Files:**
- Modify: `src/mage_ptcg/student/evaluation.py` (`evaluate_model`)
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py` (`evaluate_holdout`, `_parser`, `main`)
- Test: `tests/test_offline_scaleup_progress.py`

**Interfaces:**
- Modifies: `evaluate_model(model, examples, *, repeats=1, on_example: Callable[[int, int, dict[str, int]], None] | None = None) -> dict[str, object]` (new keyword-only param, backward compatible)
- Modifies: `evaluate_holdout(*, dataset, model_path, output, progress=None, progress_interval_seconds=None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_offline_scaleup_progress.py`:

```python
def test_evaluate_holdout_reports_per_record_progress_without_ansi(tmp_path: Path, capsys) -> None:
    from mage_ptcg.offline_scaleup.pipeline import DATASET_SCHEMA, evaluate_holdout, train_student_v1
    from mage_ptcg.student.dataset import build_rule_bc_example
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    dataset_path = tmp_path / "dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for split in ("train", "validation", "test", "test"):
            handle.write(_json.dumps({"schema_version": DATASET_SCHEMA, "split": split, "rule_bc_example": example.to_dict()}) + "\n")
    train_student_v1(dataset=dataset_path, model_dir=tmp_path / "model", epochs=1, learning_rate=0.1, progress=False)
    evaluate_holdout(dataset=dataset_path, model_path=tmp_path / "model" / "student_v1_model.json",
                      output=tmp_path / "holdout.json", progress=None, progress_interval_seconds=0)
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "PROGRESS phase=evaluate-holdout" in captured.err
    assert "fallback=0" in captured.err
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py -v -k evaluate_holdout`
Expected: FAIL — `evaluate_holdout() got an unexpected keyword argument 'progress'`

- [ ] **Step 3: Add the callback to `evaluate_model`**

In `src/mage_ptcg/student/evaluation.py`, replace the function signature and loop body:

```python
def evaluate_model(model: StudentV0Model, examples: Iterable[RuleBCExample], *, repeats: int = 1,
                    on_example: "Callable[[int, int, dict[str, int]], None] | None" = None) -> dict[str, object]:
    values = list(examples)
    if not values:
        raise ValueError("evaluation dataset is empty")
    if repeats < 1:
        raise ValueError("repeats must be positive")
    top1 = 0
    top3 = 0
    legal = 0
    fallback = 0
    losses: list[float] = []
    timings_us: list[float] = []
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for index, example in enumerate(values):
        start = time.perf_counter_ns()
        ordered, scores = _ordered_indices(model, example)
        for _ in range(repeats - 1):
            _ordered_indices(model, example)
        timings_us.append((time.perf_counter_ns() - start) / repeats / 1_000)
        target_set = set(example.target_action_digests)
        predicted = [example.legal_actions[index]["digest"] for index in ordered]
        if predicted and predicted[0] in target_set:
            top1 += 1
        if any(digest in target_set for digest in predicted[:3]):
            top3 += 1
        selection_count = example.min_count if example.min_count else 1
        if example.min_count == 0 and example.selection_type != 0:
            selection_count = 0
        chosen = ordered[:selection_count]
        if len(chosen) == len(set(chosen)) and all(0 <= item < len(example.legal_actions) for item in chosen):
            legal += 1
        maximum = max(scores)
        probabilities = [math.exp(min(80.0, score - maximum)) for score in scores]
        normalizer = sum(probabilities)
        target_probability = sum(probabilities[position] / normalizer for position, action in enumerate(example.legal_actions) if action["digest"] in target_set)
        losses.append(-math.log(max(target_probability, 1e-12)))
        key = str(example.selection_type)
        by_type[key][0] += int(bool(predicted and predicted[0] in target_set))
        by_type[key][1] += 1
        if on_example is not None:
            on_example(index, len(values), {"legal": legal, "top1": top1, "fallback": fallback})
    ordered_timings = sorted(timings_us)
    p95_index = min(len(ordered_timings) - 1, math.ceil(len(ordered_timings) * 0.95) - 1)
    return {
        "examples": len(values),
        "fallback_rate": 0.0,
        "holdout_loss": sum(losses) / len(losses),
        "legal_action_rate": legal / len(values),
        "latency_us_p50": statistics.median(ordered_timings),
        "latency_us_p95": ordered_timings[p95_index],
        "teacher_top1_fidelity": top1 / len(values),
        "teacher_top3_fidelity": top3 / len(values),
        "selection_type_top1": {key: correct / total for key, (correct, total) in sorted(by_type.items())},
    }
```

Add `Callable` to the `typing` import at the top of the file:

```python
from typing import Callable, Iterable
```

- [ ] **Step 4: Wire `evaluate_holdout` in `pipeline.py`**

Replace `evaluate_holdout` (`:950-956`) with:

```python
def evaluate_holdout(*, dataset: Path, model_path: Path, output: Path, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    model = StudentV0Model.load(model_path); test = _load_v1_examples(dataset, "test")
    reporter = ProgressReporter(phase="evaluate-holdout", total=len(test), run_id=Path(output).stem, unit="record",
                                 progress=progress, interval_seconds=progress_interval_seconds)

    def on_example(index: int, total: int, stats: dict[str, int]) -> None:
        reporter.update(1, split="test", legal_rate=round(stats["legal"] / (index + 1), 4),
                         top1_fidelity=round(stats["top1"] / (index + 1), 4), fallback=stats["fallback"])

    started = time.perf_counter(); metrics = evaluate_model(model, test, on_example=on_example); elapsed = time.perf_counter() - started
    reporter.close()
    report = {"schema_version": "offline-scaleup-holdout-v1", "test_examples": len(test), "metrics": metrics, "legal_rate": 1.0,
              "latency_seconds": {"p50": elapsed / len(test), "p95": elapsed / len(test), "p99": elapsed / len(test)}, "model_size_bytes": model_path.stat().st_size,
              "opponent_holdout": "episode-hash split; group integrity validated", "deck_holdout": "reported only when multiple deck groups are present"}
    _atomic_json(output, report); return report
```

- [ ] **Step 5: Add CLI flags for `evaluate-holdout`**

In `_parser()`, replace:

```python
    evaluate = sub.add_parser("evaluate-holdout"); evaluate.add_argument("--dataset", type=Path, required=True); evaluate.add_argument("--model", type=Path, required=True); evaluate.add_argument("--output", type=Path, required=True)
```

with:

```python
    evaluate = sub.add_parser("evaluate-holdout"); evaluate.add_argument("--dataset", type=Path, required=True); evaluate.add_argument("--model", type=Path, required=True); evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--progress", action="store_true"); evaluate.add_argument("--no-progress", action="store_true"); evaluate.add_argument("--progress-interval-seconds", type=float, default=None)
```

In `main()`, replace:

```python
        elif args.command == "evaluate-holdout": result = evaluate_holdout(dataset=args.dataset, model_path=args.model, output=args.output)
```

with:

```python
        elif args.command == "evaluate-holdout":
            progress = False if args.no_progress else (True if args.progress else None)
            result = evaluate_holdout(dataset=args.dataset, model_path=args.model, output=args.output, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
```

- [ ] **Step 6: Run tests**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py tests/test_offline_scaleup_pipeline.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/mage_ptcg/student/evaluation.py src/mage_ptcg/offline_scaleup/pipeline.py tests/test_offline_scaleup_progress.py
git commit -m "$(cat <<'EOF'
feat(offline-scaleup): evaluate-holdoutへrecord単位の進捗（legal rate, top1, fallback）を追加

- evaluate_modelにon_exampleコールバックを追加し、fallback=0を実態どおり表示する
EOF
)"
```

---

## Task 6: CLI help/end-to-end sanity, then shell script pass-through

**Files:**
- Modify: `scripts/offline_scaleup/02_run_smoke_100.sh`, `04_export_dataset.sh`, `05_train_student_v1.sh`, `06_evaluate_holdout.sh`, `07_run_generation_10000.sh`, `08_run_expanded_stability_1000.sh`, `resume_incomplete_run.sh`

- [ ] **Step 1: CLI sanity check before touching shell scripts**

Run each and confirm exit 0 with the new flags listed:
```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
for cmd in run-league resume-league export-dataset export-dataset-v2-split train-student-v1 evaluate-holdout; do
  echo "=== $cmd ==="
  PYTHONPATH=.:src python3 -m mage_ptcg.offline_scaleup "$cmd" --help | grep -E "progress|no-progress"
done
```
Expected: each command lists `--progress`, `--no-progress`, `--progress-interval-seconds`.

- [ ] **Step 2: Edit each shell script to forward extra CLI args**

For each script below, the only change is appending `"$@"`-style forwarding of arguments beyond the fixed positional ones to the underlying `python3 -m mage_ptcg.offline_scaleup ...` invocation(s) that support progress flags. Positional argument numbers and existing defaults are unchanged.

`scripts/offline_scaleup/02_run_smoke_100.sh` — change the `run-league` line from:
```bash
if python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt >>"$LOG" 2>&1; then
```
to:
```bash
if python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:3}" >>"$LOG" 2>&1; then
```
Note: progress output goes to stderr and this line redirects `2>&1` into `$LOG`; that is intentional per-script logging behavior already in place — a caller who wants to *see* the bar live should not redirect, i.e. should invoke `python3 -m mage_ptcg.offline_scaleup run-league ...` directly, or this script should stop swallowing stderr when `--progress`/`--no-progress` is explicitly forwarded. Since changing the existing `>>"$LOG" 2>&1` redirection would alter established logging behavior for every other caller, leave the redirection as-is and document this precisely in the final report: **these wrapper scripts always redirect stderr to `$LOG`, so their progress bars land in the log file, not the terminal, by design** (matches "詳細は既存のJSONL／logへ保存" from the spec). Users who want a live terminal bar should invoke `python3 -m mage_ptcg.offline_scaleup run-league ...` directly without the wrapper's redirection.

`scripts/offline_scaleup/08_run_expanded_stability_1000.sh` — change:
```bash
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$POPULATION" --repo "$ROOT" --workers "$WORKERS" --executor cabt >>"$LOG" 2>&1
```
to:
```bash
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$POPULATION" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:3}" >>"$LOG" 2>&1
```

`scripts/offline_scaleup/07_run_generation_10000.sh` — change:
```bash
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt >>"$LOG" 2>&1
```
to:
```bash
python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:3}" >>"$LOG" 2>&1
```

`scripts/offline_scaleup/resume_incomplete_run.sh` — change:
```bash
python3 -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt >"$ARTIFACT_ROOT/logs/resume_${RUN_NAME}.log" 2>&1
```
to:
```bash
python3 -m mage_ptcg.offline_scaleup resume-league --run-dir "$RUN" --population "$ARTIFACT_ROOT/artifacts/opponent_registry.json" --repo "$ROOT" --workers "$WORKERS" --executor cabt "${@:4}" >"$ARTIFACT_ROOT/logs/resume_${RUN_NAME}.log" 2>&1
```
(this script's 3rd positional argument is `RUN_NAME`, so pass-through starts at position 4)

`scripts/offline_scaleup/04_export_dataset.sh` — change:
```bash
python3 -m mage_ptcg.offline_scaleup export-dataset --run-dir "$ARTIFACT_ROOT/runs/stability-1000" --output "$ARTIFACT_ROOT/datasets/stability-1000.jsonl" >"$LOG" 2>&1
```
to:
```bash
python3 -m mage_ptcg.offline_scaleup export-dataset --run-dir "$ARTIFACT_ROOT/runs/stability-1000" --output "$ARTIFACT_ROOT/datasets/stability-1000.jsonl" "${@:3}" >"$LOG" 2>&1
```

`scripts/offline_scaleup/05_train_student_v1.sh` — this script already takes a 3rd positional `DATASET` argument (from the prior dataset-split-remediation task); pass-through starts at position 4. Change:
```bash
python3 -m mage_ptcg.offline_scaleup train-student-v1 --dataset "$DATASET" --model-dir "$ARTIFACT_ROOT/models/student-v1" >"$LOG" 2>&1
```
to:
```bash
python3 -m mage_ptcg.offline_scaleup train-student-v1 --dataset "$DATASET" --model-dir "$ARTIFACT_ROOT/models/student-v1" "${@:4}" >"$LOG" 2>&1
```

`scripts/offline_scaleup/06_evaluate_holdout.sh` — also has a 3rd positional `DATASET`; pass-through starts at position 4. Change:
```bash
python3 -m mage_ptcg.offline_scaleup evaluate-holdout --dataset "$DATASET" --model "$ARTIFACT_ROOT/models/student-v1/student_v1_model.json" --output "$ARTIFACT_ROOT/summaries/holdout_evaluation.json" >"$LOG" 2>&1
```
to:
```bash
python3 -m mage_ptcg.offline_scaleup evaluate-holdout --dataset "$DATASET" --model "$ARTIFACT_ROOT/models/student-v1/student_v1_model.json" --output "$ARTIFACT_ROOT/summaries/holdout_evaluation.json" "${@:4}" >"$LOG" 2>&1
```

For `05_train_student_v1.sh`, the example in the spec (`bash .../06_evaluate_holdout.sh <artifact-root> 8 <dataset-path> --progress`) implies progress flags are meant to be *visible*, not silently redirected into a log. Since the existing convention for every script in this family always redirects the python invocation's stderr into `$LOG`, keep that convention (do not special-case these two scripts) and note this clearly in the final report rather than silently deviating from the established pattern for only two of seven scripts.

- [ ] **Step 3: Verify each edited script is still valid bash and unchanged for existing callers**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && for f in scripts/offline_scaleup/02_run_smoke_100.sh scripts/offline_scaleup/04_export_dataset.sh scripts/offline_scaleup/05_train_student_v1.sh scripts/offline_scaleup/06_evaluate_holdout.sh scripts/offline_scaleup/07_run_generation_10000.sh scripts/offline_scaleup/08_run_expanded_stability_1000.sh scripts/offline_scaleup/resume_incomplete_run.sh; do bash -n "$f" && echo "$f OK"; done`
Expected: all 7 print `OK`.

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_worker_contract.py -v -k next_command`
Expected: PASS (confirms `02_run_smoke_100.sh`'s unrelated `next_command` string contract still holds — that assertion targets a different line than the one edited here).

- [ ] **Step 4: Commit**

```bash
git add scripts/offline_scaleup/02_run_smoke_100.sh scripts/offline_scaleup/04_export_dataset.sh scripts/offline_scaleup/05_train_student_v1.sh scripts/offline_scaleup/06_evaluate_holdout.sh scripts/offline_scaleup/07_run_generation_10000.sh scripts/offline_scaleup/08_run_expanded_stability_1000.sh scripts/offline_scaleup/resume_incomplete_run.sh
git commit -m "$(cat <<'EOF'
feat(offline-scaleup): 各shell scriptがprogress CLI引数を透過的に受け渡せるようにする

- 既存の位置引数・デフォルト値は不変。--progress等は各scriptの追加引数として渡す
EOF
)"
```

---

## Task 7: Real CABT smoke (≤4 games), interrupt/resume behavior, and full regression

**Files:** none (verification only)

- [ ] **Step 1: Real CABT run of exactly 4 games with periodic (non-TTY) progress, capturing stderr to a file to confirm no ANSI and a bounded line count**

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
SCRATCH=/tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/progress-cabt-smoke
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
PYTHONPATH=.:src python3 -m mage_ptcg.offline_scaleup build-population --repo . --output "$SCRATCH/population.json" --recovery-root /home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-target-availability-remediation-v1-timeout-recovery
PYTHONPATH=.:src python3 -m mage_ptcg.offline_scaleup build-schedule --population "$SCRATCH/population.json" --output "$SCRATCH/run/schedule.json" --candidate rule-v0-current-deck --opponent rule-v0-current-deck --games 4 --base-seed 555
mkdir -p "$SCRATCH/run"
PYTHONPATH=.:src python3 -m mage_ptcg.offline_scaleup run-league --run-dir "$SCRATCH/run" --population "$SCRATCH/population.json" --repo . --executor cabt --workers 2 --progress --progress-interval-seconds 0 2> "$SCRATCH/progress.stderr" 1> "$SCRATCH/result.stdout"
echo "exit=$?"
grep -c $'\x1b' "$SCRATCH/progress.stderr" || echo "0 ANSI bytes"
cat "$SCRATCH/run/run_summary.json"
```
Expected: exit 0, 0 ANSI bytes (`grep -c` finding nothing prints `0` or the fallback echo fires), `run_summary.json` shows `"completed":4,"gate":"PASS"`.

- [ ] **Step 2: Interrupt-then-resume test — simulate a partial run and confirm resume starts progress from the right `initial` and produces no duplicates**

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
SCRATCH=/tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/progress-resume-smoke
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH/run"
PYTHONPATH=.:src python3 -c "
from pathlib import Path
import json
from mage_ptcg.offline_scaleup.pipeline import build_population, build_schedule, run_league, _write_jsonl_once, RESULT_SCHEMA
population = build_population(repo=Path('.'), output=Path('$SCRATCH/population.json'), recovery_root=Path('/home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-target-availability-remediation-v1-timeout-recovery'))
schedule = build_schedule(population, candidate='rule-v0-current-deck', opponents=['rule-v0-current-deck'], games=4, base_seed=777)
Path('$SCRATCH/run/schedule.json').write_text(json.dumps(schedule), encoding='utf-8')
# Simulate a Ctrl+C after 2/4 games by writing 2 fixture completions directly, bypassing run_league.
for job in schedule['games'][:2]:
    _write_jsonl_once(Path('$SCRATCH/run/game_results.jsonl'), {'schema_version': RESULT_SCHEMA, **job, 'status': 'DONE', 'legal': True, 'candidate_fault': False, 'mapping_valid': True, 'score_identity_valid': True, 'teacher_samples': [], 'fault': {'kind': 'COMPLETED'}, 'attempt_history': [], 'completed_at_unix': 0.0})
summary = run_league(run_dir=Path('$SCRATCH/run'), population_path=Path('$SCRATCH/population.json'), repo=Path('.'), executor='cabt', timeout=180, max_attempts=2, workers=2, progress=False)
print('resumed completed=', summary['completed'], 'gate=', summary['gate'])
"
python3 -c "
import json
rows = [json.loads(line) for line in open('$SCRATCH/run/game_results.jsonl', encoding='utf-8')]
ids = [row['game_id'] for row in rows]
print('rows=', len(rows), 'unique=', len(set(ids)))
assert len(rows) == len(set(ids)) == 4
print('OK no duplicate completion')
"
```
Expected: `resumed completed= 4 gate= PASS`, `rows= 4 unique= 4`, `OK no duplicate completion`.

- [ ] **Step 3: Full regression across every affected test module**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_progress.py tests/test_offline_scaleup_pipeline.py tests/test_offline_scaleup_worker_contract.py tests/test_offline_scaleup_dataset_split.py tests/test_student_model_progress.py -v`
Expected: all pass, 0 failures.

- [ ] **Step 4: Confirm protected files and untracked files are unaffected**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && git diff --stat HEAD~7..HEAD -- main.py deck.csv agents/rule_agent.py agents/rule_agent_v1.py src/mage_ptcg/evaluation/promotion.py; git status --short`
Expected: empty diff for protected files; `git status --short` shows only the untracked files present at session start (unmodified) plus any new plan/status files this task creates.

---

## Task 8: Status docs and final report

**Files:**
- Modify: `docs/status/current_status.md`, `docs/status/handoff.md` (append a dated entry to the same "Offline Scale-up" sections used by the prior dataset-split-remediation task; do not restructure surrounding content)

- [ ] **Step 1: Append a dated sub-section to both files** summarizing: which 6 phases gained progress display, the TTY/periodic/off mode resolution rule, the `as_completed()`-based run-league change, `progress_summary.json`'s extended schema, and that CABT was exercised for at most 4 games.

- [ ] **Step 2: Validate docs structure**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && python3 scripts/docs/validate_docs.py`
Expected: `Validated N canonical documents.` with no errors.

- [ ] **Step 3: Commit**

```bash
git add docs/status/current_status.md docs/status/handoff.md
git commit -m "docs(offline-scaleup): progress display v1の状態を記録"
```

- [ ] **Step 4: Compose the final report** covering exactly the items the spec requires (processes covered, display examples, TTY/non-TTY behavior, multiprocessing implementation, resume behavior, progress_summary update frequency, test results, changed files, commit hashes, no upstream, 0 pushes, protected files unchanged, next holdout-evaluation command) and end with `OFFLINE_SCALEUP_PROGRESS_DISPLAY_READY` only if every gate in the spec's READY condition list is actually met; otherwise state the correct verdict (`READY_AFTER_LIMITED_PROGRESS_FIX` or `PROGRESS_IMPLEMENTATION_REWORK_REQUIRED`) and omit the marker.

---

## Self-review notes

- Spec coverage: all 6 target processes (Task 2–5 cover run-league/resume-league/export-dataset-v2-split/train-student-v1/evaluate-holdout; 10,000-game generation is the same `run_league` code path via `07_run_generation_10000.sh`, covered by Task 2+6). Parallel semantics (single parent-process bar, completion-based increments, `as_completed()`) — Task 2 and Task 3's `ProcessPoolExecutor` branch. Resume support — Task 2's `initial=len(completed)`. TTY/log separation, ANSI-free periodic mode, `--progress`/`--no-progress`/`--progress-interval-seconds` CLI, env vars — Task 1 (core) + Tasks 2–5 (per-command wiring). Token/log savings (no per-record lines, capped fault samples, throttled summary writes) — Task 1's throttling + existing `sample_limit: 5` in `fault_summary.json` (unchanged). `progress_summary.json` required fields and ≥5s throttle — Task 1 + Task 2. Shell script pass-through — Task 6. 14-item test list — Task 1 (1,2,3,9,11), Task 2 (4,6,7,8,12,13), Task 3 (5 partially, periodic-mode integration), Task 7 (5,6,8,14 end-to-end with real CABT and simulated interrupt/resume).
- No placeholders: every step shows the literal diff or full function body.
- Type consistency checked: `ProgressReporter.update(n=1, **fields)` signature matches every call site added in Tasks 2–5; `on_epoch(epoch_index, total_epochs, train_loss, weights, bias)` matches both `train_model`'s call and `train_student_v1`'s callback; `on_example(index, total, stats)` matches both `evaluate_model`'s call and `evaluate_holdout`'s callback; `summarize_run(run_dir, *, workers=None)` matches its two call sites (`run_league` passes `workers=workers`; CLI `summarize-league`/`verify-run` pass none, using the default).
