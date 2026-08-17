#!/usr/bin/env python3
"""Watch a V4 DAgger screen/BC progress file with one aggregate bar."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
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


def _counts(payload: dict[str, Any]) -> tuple[int, int]:
    stage = payload.get("stage")
    if stage == "games":
        return int(payload.get("games_finished", 0)), int(payload.get("games_requested", 0))
    if stage == "training":
        return int(payload.get("seed_index", 0)), int(payload.get("seeds_total", 0))
    if stage == "complete":
        return int(payload.get("seeds_completed", payload.get("games_completed", 0))), int(payload.get("seeds_total", payload.get("games_requested", 0)))
    return 0, 0


def _line(payload: dict[str, Any]) -> str:
    completed, total = _counts(payload)
    fields = [f"{completed}/{total}", f"stage={payload.get('stage', '?')}", f"status={payload.get('status', '?')}"]
    for key in ("seed", "epoch", "epochs_completed", "epochs_requested", "games_completed", "games_finished", "faults", "transition_records", "last_job_id"):
        if key in payload:
            fields.append(f"{key}={_fmt(payload[key])}")
    if "optimizer_updates_completed" in payload:
        fields.append(f"updates={_fmt(payload['optimizer_updates_completed'])}")
    if "sequences_completed" in payload and "sequences_total" in payload:
        fields.append(
            f"sequences={_fmt(payload['sequences_completed'])}/{_fmt(payload['sequences_total'])}"
        )
    if "partial_train_complete_action_nll" in payload:
        fields.append(f"partial_train_nll={_fmt(payload['partial_train_complete_action_nll'])}")
    if "elapsed_seconds" in payload:
        fields.append(f"elapsed={float(payload['elapsed_seconds']):.0f}s")
    if "heartbeat_age_seconds" in payload:
        fields.append(f"heartbeat_age={float(payload['heartbeat_age_seconds']):.0f}s")
    for key in ("history_row",):
        row = payload.get(key)
        if isinstance(row, dict):
            if "train_complete_action_nll" in row:
                fields.append(f"train_nll={_fmt(row['train_complete_action_nll'])}")
            if "validation_complete_action_nll" in row:
                fields.append(f"val_nll={_fmt(row['validation_complete_action_nll'])}")
    return "v4-dagger | " + " | ".join(fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("progress_path", type=Path)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.interval <= 0.0:
        parser.error("--interval must be positive")
    bar = None
    if not args.once and sys.stderr.isatty():
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        if tqdm is not None:
            bar = tqdm(total=0, desc="v4-dagger", unit="unit", dynamic_ncols=True, file=sys.stderr)
    last_status = "waiting"
    watch_started = time.monotonic()
    try:
        while True:
            try:
                payload = _read(args.progress_path)
            except ValueError as exc:
                if args.once:
                    raise
                if bar is not None:
                    bar.set_postfix(status="waiting", error=str(exc)[:48])
                    bar.refresh()
                else:
                    print(f"v4-dagger | waiting | {exc}", flush=True)
                time.sleep(args.interval)
                continue
            completed, total = _counts(payload)
            display_payload = dict(payload)
            display_payload["elapsed_seconds"] = time.monotonic() - watch_started
            updated_unix = display_payload.get("updated_unix")
            if isinstance(updated_unix, (int, float)):
                display_payload["heartbeat_age_seconds"] = max(0.0, time.time() - float(updated_unix))
            if bar is not None:
                if bar.total != total:
                    bar.total = total
                bar.n = completed
                bar.set_postfix(**{
                    "stage": display_payload.get("stage", "?"),
                    "status": display_payload.get("status", "?"),
                    "seed": display_payload.get("seed", "-"),
                    "epoch": display_payload.get("epochs_completed", "-"),
                    "updates": display_payload.get("optimizer_updates_completed", "-"),
                    "sequences": (
                        f"{display_payload.get('sequences_completed', '-')}/"
                        f"{display_payload.get('sequences_total', '-')}"
                    ),
                    "elapsed": f"{float(display_payload['elapsed_seconds']):.0f}s",
                    "age": f"{float(display_payload.get('heartbeat_age_seconds', 0.0)):.0f}s",
                    "faults": display_payload.get("faults", "-"),
                    "nll": (display_payload.get("history_row") or {}).get("validation_complete_action_nll", "-"),
                    "partial_nll": display_payload.get("partial_train_complete_action_nll", "-"),
                })
                bar.refresh()
            else:
                print(_line(display_payload), flush=True)
            last_status = str(display_payload.get("status", "running"))
            if args.once or last_status in {"complete", "VALID", "INVALID_FAULTS", "failed"}:
                break
            time.sleep(args.interval)
    finally:
        if bar is not None:
            bar.close()
    return 0 if last_status in {"running", "complete", "VALID"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
