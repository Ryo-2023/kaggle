#!/usr/bin/env python3
"""Command-line interface for the MAGE-PTCG Bootstrap Kernel."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

try:  # Support both ``python scripts/orchestrate.py`` and package imports in tests.
    from .orchestration.kernel import Kernel, KernelError
    from .orchestration.progress import (
        Progress,
        ProgressDataError,
        is_terminal,
        is_terminal_state,
        new_records,
    )
    from .orchestration.state import RunState
    from .orchestration.overnight import OvernightRunner, OvernightError
    from .orchestration.overnight_state import (
        TERMINAL_SESSION_STATES,
        OvernightStateError,
    )
    from .orchestration.report import write_report
    from .orchestration.authorization import load_authorized_provider_capabilities
except ImportError:  # pragma: no cover - exercised by the script entry point.
    from orchestration.kernel import Kernel, KernelError
    from orchestration.progress import (
        Progress,
        ProgressDataError,
        is_terminal,
        is_terminal_state,
        new_records,
    )
    from orchestration.state import RunState
    from orchestration.overnight import OvernightRunner, OvernightError
    from orchestration.overnight_state import (
        TERMINAL_SESSION_STATES,
        OvernightStateError,
    )
    from orchestration.report import write_report
    from orchestration.authorization import load_authorized_provider_capabilities


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _state_json(value: RunState | list[RunState]) -> Any:
    if isinstance(value, list):
        return [state.to_dict() for state in value]
    return value.to_dict()


def build_parser() -> argparse.ArgumentParser:
    """Build the public Bootstrap Kernel argument parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--contract", required=True, type=Path)
    status = subparsers.add_parser("status")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--follow", action="store_true")
    status.add_argument("--interval", type=float, default=1.0)
    resume = subparsers.add_parser("resume")
    resume.add_argument("run_id")
    approve = subparsers.add_parser("approve")
    approve.add_argument("run_id")
    approve.add_argument("gate", choices=["integration"])
    reject = subparsers.add_parser("reject")
    reject.add_argument("run_id")
    reject.add_argument("gate", choices=["integration"])
    reject.add_argument("--reason", required=True)
    abort = subparsers.add_parser("abort")
    abort.add_argument("run_id")
    subparsers.add_parser("doctor")
    overnight = subparsers.add_parser("overnight")
    overnight_group = overnight.add_mutually_exclusive_group(required=True)
    overnight_group.add_argument("--plan", type=Path)
    overnight_group.add_argument("--resume", metavar="SESSION_ID")
    overnight_status = subparsers.add_parser("overnight-status")
    overnight_status.add_argument("session_id")
    overnight_status.add_argument("--follow", action="store_true")
    overnight_status.add_argument("--interval", type=float, default=1.0)
    overnight_report = subparsers.add_parser("overnight-report")
    overnight_report.add_argument("session_id")
    return parser


def _print_progress(record: dict[str, object]) -> bool:
    """Render one already-sanitized progress record."""

    try:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    except Exception:
        try:
            sys.stderr.write("warning: progress observer disabled\n")
            sys.stderr.flush()
        except Exception:
            pass
        return False
    return True


def _print_live_progress(record: dict[str, object]) -> None:
    """Write one already-sanitized start record to stderr and flush it."""

    sys.stderr.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stderr.flush()


def _follow_status(kernel: Kernel, run_id: str, interval: float) -> int:
    """Print one current sanitized record and then only appended records."""

    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("follow interval must be a positive finite number")
    state = kernel.status(run_id)
    run_dir = kernel.runs_root / run_id
    records = Progress.read(run_dir)
    if not _print_progress(records[-1]):
        return 0
    baseline = len(records)
    if is_terminal(records[-1]) or is_terminal_state(state.state):
        return 0
    while True:
        time.sleep(interval)
        records = Progress.read(run_dir)
        if len(records) < baseline:
            raise ProgressDataError("progress stream is not append-only")
        for record in new_records(records, baseline):
            if not _print_progress(record):
                return 0
            if is_terminal(record):
                return 0
        baseline = len(records)
        state = kernel.status(run_id)
        if is_terminal_state(state.state):
            return 0


def _follow_overnight(runner: OvernightRunner, session_id: str, interval: float) -> int:
    """Print current state once, then only newly appended sanitized events."""

    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("follow interval must be a positive finite number")
    current = runner.progress_record(session_id)
    if not _print_progress(current):
        return 0
    records = runner.events(session_id)
    baseline = len(records)
    if current["status"] in TERMINAL_SESSION_STATES:
        return 0
    while True:
        time.sleep(interval)
        records = runner.events(session_id)
        if len(records) < baseline:
            raise OvernightStateError("overnight event stream is not append-only")
        for record in records[baseline:]:
            if not _print_progress(record):
                return 0
        baseline = len(records)
        if runner.status(session_id)["status"] in TERMINAL_SESSION_STATES:
            return 0


def main(argv: list[str] | None = None) -> int:
    """Execute a CLI command and emit one JSON document."""

    try:
        arguments = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    if arguments.command == "status" and arguments.follow and not arguments.run_id:
        print(json.dumps({"error": "status --follow requires a run identifier"}), file=sys.stderr)
        return 2
    progress = Progress(sink=_print_live_progress) if arguments.command == "start" else None
    kernel = Kernel(_repository_root(), progress=progress)
    try:
        capabilities = (
            load_authorized_provider_capabilities(_repository_root())
            if arguments.command == "overnight"
            else ()
        )
        overnight = OvernightRunner(
            _repository_root(), provider_capabilities=capabilities
        )
        if arguments.command == "start":
            output = _state_json(kernel.start(arguments.contract.resolve()))
        elif arguments.command == "status":
            if arguments.follow:
                return _follow_status(kernel, arguments.run_id, arguments.interval)
            output = _state_json(kernel.status(arguments.run_id))
        elif arguments.command == "resume":
            output = _state_json(kernel.resume(arguments.run_id))
        elif arguments.command == "approve":
            output = _state_json(kernel.approve(arguments.run_id, arguments.gate))
        elif arguments.command == "reject":
            output = _state_json(
                kernel.reject(arguments.run_id, arguments.gate, arguments.reason)
            )
        elif arguments.command == "abort":
            output = _state_json(kernel.abort(arguments.run_id))
        elif arguments.command == "overnight":
            output = overnight.start(arguments.plan.resolve()) if arguments.plan else overnight.resume(arguments.resume)
        elif arguments.command == "overnight-status":
            if arguments.follow:
                return _follow_overnight(overnight, arguments.session_id, arguments.interval)
            output = overnight.status(arguments.session_id)
        elif arguments.command == "overnight-report":
            session = overnight._session(arguments.session_id)
            output = write_report(session, overnight.status(arguments.session_id))
        else:
            output = kernel.doctor()
    except KeyboardInterrupt:
        return 130
    except (
        KernelError,
        OvernightError,
        OvernightStateError,
        ProgressDataError,
        RuntimeError,
        ValueError,
        OSError,
    ) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
