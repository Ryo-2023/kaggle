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
    explicit True (--progress) forces a dynamic bar even when a shell wrapper
    mirrors stderr to a log through ``tee``.  With neither explicit flag nor
    environment setting, the rendering style follows whether `stream` is a
    real terminal; redirected logs remain ANSI-free periodic lines.
    """
    if progress is False:
        return "off"
    if progress is True:
        return "tty"
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
        self._bar_stream: TextIO | None = None
        self._owns_bar_stream = False
        if self.mode == "tty":
            # Wrappers mirror stderr with ``tee`` for durable logs.  Writing a
            # carriage-return bar into that pipe produces one pasted line per
            # refresh instead of one live terminal bar.  In an interactive
            # invocation, address the controlling terminal directly while
            # retaining normal stdout/stderr for structured logs.
            self._bar_stream = self._stream
            try:
                is_tty = bool(self._stream.isatty())
            except (AttributeError, ValueError):
                is_tty = False
            if not is_tty:
                try:
                    self._bar_stream = open("/dev/tty", "w", encoding="utf-8", buffering=1)
                    self._owns_bar_stream = True
                except OSError:
                    # CI and redirected non-interactive callers have no
                    # controlling terminal; tqdm still renders deterministically
                    # to the explicitly requested stream.
                    self._bar_stream = self._stream
            self._bar = tqdm(total=self.total, initial=self._initial, unit=self.unit, file=self._bar_stream,
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
        if self._owns_bar_stream and self._bar_stream is not None:
            self._bar_stream.close()
        elif self.mode == "periodic":
            self._maybe_emit_periodic(force=True)
        self._maybe_write_summary(force=True)


__all__ = ["ProgressReporter", "default_interval_seconds", "resolve_progress_mode"]
