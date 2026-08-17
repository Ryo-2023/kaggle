#!/usr/bin/env python3
"""Create an offline, hash-bound strict-disagreement report from a V4 screen."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts import run_meta_specialist_v4_dagger_bc as runner  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--screen-sha256", required=True)
    parser.add_argument("--transitions", type=Path, required=True)
    parser.add_argument("--transitions-sha256", required=True)
    parser.add_argument("--lane", choices=("alakazam", "archaludon"), required=True)
    parser.add_argument("--focus-opponents", type=runner._parse_focus_names, default=())
    parser.add_argument(
        "--focus-seats",
        type=lambda value: runner._parse_focus_ints(value, field="focus_seats", minimum=0, maximum=1),
        default=(),
    )
    parser.add_argument(
        "--focus-action-types",
        type=lambda value: runner._parse_focus_ints(value, field="focus_action_types", minimum=0, maximum=16),
        default=(),
    )
    parser.add_argument("--max-mean-behavior-log-probability", type=float, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    screen = runner._read_hashed_json(args.screen, args.screen_sha256, field="screen")
    if screen.get("status") != "VALID" or screen.get("faults") != 0:
        raise ValueError("screen must be a fault-free VALID artifact")
    rows = runner._read_transition_rows(
        args.transitions,
        expected_sha=args.transitions_sha256,
        expected_screen=screen,
    )
    sequences, selection = runner.build_dagger_sequences_with_strict_disagreement_v4(
        rows,
        lane=args.lane,
        focus_opponents=args.focus_opponents,
        focus_seats=args.focus_seats,
        focus_action_types=args.focus_action_types,
        max_mean_behavior_log_probability=args.max_mean_behavior_log_probability,
    )
    report = {
        "schema": "meta-specialist-v4-strict-disagreement-offline-report-v1",
        "screen_path": str(args.screen.resolve()),
        "screen_file_sha256": _sha(args.screen),
        "transitions_path": str(args.transitions.resolve()),
        "transitions_file_sha256": _sha(args.transitions),
        "lane": args.lane,
        "screen_games_completed": screen.get("games_completed"),
        "screen_transition_records": screen.get("transition_records"),
        "sequence_count_returned": len(sequences),
        "selection": selection,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
