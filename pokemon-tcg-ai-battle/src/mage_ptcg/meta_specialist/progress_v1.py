"""長時間 runner の端末表示。

AGENTS.md「長時間実験の端末表示」に対応する。

- TTY では **単一の更新式 progress bar** を出す。現在値、速度、ETA、fault など
  判断に必要な集計値は postfix に載せ、局や step ごとの行ログは出さない。
- 非 TTY では 10 秒ごとの集約スナップショットだけを出す。詳細は artifact 内の
  ``progress_summary.json`` に置き、端末へ複製しない。
- progress stream を ``tee``、pipe、行単位 logger へ通すと carriage return が
  解釈されず bar の断片が大量に出る。**この module の出力を pipe しないこと。**
  pipe されている (非 TTY) と検出したら自動で集約モードへ落ちるので、事故には
  ならないが、bar は見えなくなる。

bar とは別に出してよいのは stage の開始・完了・fail-closed の原因だけである。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping


class ProgressReporterV1:
    """One updating bar on a TTY; sparse aggregate snapshots otherwise."""

    def __init__(
        self,
        *,
        total: int,
        desc: str,
        stream: Any = None,
        snapshot_interval_seconds: float = 10.0,
        progress_path: str | Path | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._total = int(total)
        self._desc = desc
        self._completed = 0
        self._started = time.monotonic()
        self._first_completed_at: float | None = None
        self._last_snapshot = 0.0
        self._snapshot_interval = float(snapshot_interval_seconds)
        # A supervisor rendering several lanes at once needs the numbers without
        # any lane writing to the terminal.  Written atomically so a reader never
        # sees a half-written record.
        self._progress_path = Path(progress_path) if progress_path else None
        self._last_fields: dict[str, Any] = {}
        self._status = "running"
        self._bar = None
        self._is_tty = bool(getattr(self._stream, "isatty", lambda: False)())
        if self._is_tty:
            try:
                from tqdm import tqdm
            except ImportError:  # pragma: no cover - tqdm is a declared dependency
                self._is_tty = False
            else:
                self._bar = tqdm(
                    total=self._total, desc=desc, unit="it",
                    file=self._stream, dynamic_ncols=True, leave=True,
                )

    def _write_progress_file(self) -> None:
        if self._progress_path is None:
            return
        elapsed, rate = self._progress_stats()
        remaining = max(0, self._total - self._completed)
        payload = {
            "desc": self._desc,
            "status": self._status,
            "completed": self._completed,
            "total": self._total,
            "rate_per_second": rate,
            "elapsed_seconds": elapsed,
            "eta_seconds": (remaining / rate) if rate > 0 else None,
            "fields": {key: _jsonable(value) for key, value in self._last_fields.items()},
            "updated_unix": time.time(),
        }
        self._progress_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._progress_path.name}.tmp.", dir=self._progress_path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._progress_path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def update(self, advance: int = 1, **fields: Any) -> None:
        first_completion = self._completed == 0 and int(advance) > 0
        self._completed += int(advance)
        if first_completion:
            self._first_completed_at = time.monotonic()
        if fields:
            self._last_fields = dict(fields)
        now = time.monotonic()
        if self._progress_path is not None and (
            now - self._last_snapshot >= 1.0 or self._completed >= self._total
        ):
            self._write_progress_file()
        if self._bar is not None:
            if fields:
                self._bar.set_postfix(**{k: _format(v) for k, v in fields.items()})
            self._bar.update(int(advance))
            if first_completion:
                # tqdm otherwise includes a long materialization/setup phase in
                # its ETA for the remaining, homogeneous units.
                self._bar.start_t = time.time()
                self._bar.last_print_t = self._bar.start_t
            return
        now = time.monotonic()
        if now - self._last_snapshot < self._snapshot_interval and self._completed < self._total:
            return
        self._last_snapshot = now
        self._snapshot(fields)

    def _snapshot(self, fields: Mapping[str, Any]) -> None:
        elapsed, rate = self._progress_stats()
        remaining = max(0, self._total - self._completed)
        eta = remaining / rate if rate > 0 else float("inf")
        parts = [
            f"{self._desc}: {self._completed}/{self._total}",
            f"{rate:.2f} it/s",
            f"elapsed {elapsed:.0f}s",
            f"eta {eta:.0f}s" if eta != float("inf") else "eta -",
        ]
        parts.extend(f"{key}={_format(value)}" for key, value in fields.items())
        print(" | ".join(parts), file=self._stream, flush=True)

    def _progress_stats(self) -> tuple[float, float]:
        """Return wall elapsed and a unit rate excluding pre-first-unit setup."""
        now = time.monotonic()
        elapsed = max(1e-9, now - self._started)
        if self._first_completed_at is not None:
            # One completed unit is not enough to estimate a stable rate.  In
            # particular, the first unit may follow a long materialization
            # phase; reporting ``1 / total_elapsed`` makes ETA grow while the
            # second unit is still running.  Keep elapsed visible but leave
            # rate/ETA unknown until two homogeneous units are complete.
            if self._completed <= 1:
                return elapsed, 0.0
            active_elapsed = max(1e-9, now - self._first_completed_at)
            return elapsed, self._completed / active_elapsed
        return elapsed, self._completed / elapsed

    def note(self, message: str) -> None:
        """A state transition -- stage start/finish or a fail-closed reason.

        Written through the bar so it cannot corrupt the bar's line.
        """
        if self._bar is not None:
            self._bar.write(message, file=self._stream)
        else:
            print(message, file=self._stream, flush=True)

    def close(self, *, status: str = "done") -> None:
        self._status = status
        self._write_progress_file()
        if self._bar is not None:
            self._bar.close()
            self._bar = None
        else:
            self._snapshot({})


def _format(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = ["ProgressReporterV1"]
