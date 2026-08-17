#!/usr/bin/env python3
"""Render one V4 progress JSON as a single bar with aggregate diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _read_progress(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"progress file is not readable JSON: {path}") from exc
    if type(value) is not dict:
        raise ValueError("progress root must be an object")
    return value


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    if value is None:
        return "-"
    return str(value)


def _format_snapshot(payload: dict[str, Any]) -> str:
    fields = payload.get("fields")
    if type(fields) is not dict:
        fields = {}
    completed = payload.get("completed", 0)
    total = payload.get("total", 0)
    rate = payload.get("rate_per_second", 0.0)
    eta = payload.get("eta_seconds")
    elapsed = payload.get("elapsed_seconds", 0.0)
    parts = [
        f"{completed}/{total}",
        f"rate={_fmt(rate)}/s",
        f"elapsed={_fmt(elapsed)}s",
        f"eta={_fmt(eta)}s",
    ]
    for key in (
        "stage", "seed", "partition", "recurrence", "phase", "phase_elapsed_seconds",
        "sequence_count", "selected_train_sequences", "selected_validation_sequences",
        "train_records", "validation_records", "complete_action_nll", "complete_action_top1",
    ):
        if key in fields:
            label = {
                "complete_action_nll": "nll",
                "complete_action_top1": "top1",
                "phase_elapsed_seconds": "phase_s",
                "phase": "phase",
            }.get(key, key)
            parts.append(f"{label}={_fmt(fields[key])}")
    return "v4-imitation | " + " | ".join(parts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("progress_path", type=Path)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.interval <= 0:
        raise ValueError("--interval must be positive")
    bar = None
    if not args.once and sys.stderr.isatty():
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        if tqdm is not None:
            bar = tqdm(total=0, desc="v4-imitation", unit="it", file=sys.stderr, dynamic_ncols=True)
    last_status = "running"
    try:
        while True:
            try:
                payload = _read_progress(args.progress_path)
            except ValueError as exc:
                if args.once:
                    raise
                if bar is not None:
                    bar.set_postfix(status="waiting", error=str(exc)[:64])
                    bar.refresh()
                else:
                    print(f"v4-imitation | waiting | {exc}", flush=True)
                time.sleep(args.interval)
                continue
            completed = int(payload.get("completed", 0))
            total = int(payload.get("total", 0))
            if bar is not None:
                if bar.total != total:
                    bar.total = total
                bar.n = completed
                fields = payload.get("fields") if type(payload.get("fields")) is dict else {}
                postfix = {"status": payload.get("status", "?"), **{
                    key: fields[key] for key in ("stage", "seed", "partition", "recurrence", "complete_action_nll", "complete_action_top1")
                    if key in fields
                }}
                bar.set_postfix(**{key: _fmt(value) for key, value in postfix.items()})
                bar.refresh()
            else:
                print(_format_snapshot(payload), flush=True)
            last_status = str(payload.get("status", "running"))
            if args.once or last_status not in {"running", "pending"} or completed >= total > 0:
                break
            time.sleep(args.interval)
    finally:
        if bar is not None:
            bar.close()
    return 0 if last_status in {"done", "complete", "running", "pending"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
