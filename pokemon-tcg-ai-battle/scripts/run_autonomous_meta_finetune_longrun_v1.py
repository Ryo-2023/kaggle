#!/usr/bin/env python3
"""Prepare (but do not implicitly launch) the autonomous meta long-run.

The default command is a dry-run and only writes a hash-bound descriptor.  An
``--execute`` request is deliberately fail-closed until a ``gate.json`` has
been recorded and a caller supplies a research runner through the Python API;
this CLI has no training/CABT/submission side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from mage_ptcg.meta_specialist.longrun_autonomous_v1 import (
    LongrunConfigV1,
    LongrunError,
    NativeBaselineV1,
    initialize_longrun_v1,
    launch_longrun_v1,
)
from mage_ptcg.meta_specialist.meta_distribution_v1 import load_meta_distribution_manifest_v1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_config_v1(
    *,
    manifest_path: Path,
    run_dir: Path,
    baseline_id: str,
    baseline_deck_sha256: str,
    baseline_policy_sha256: str,
    evaluator_sha256: str,
    baseline_status: str = "UNPROVEN",
) -> LongrunConfigV1:
    """Build a config from the sealed manifest; split membership is not guessed."""
    manifest = load_meta_distribution_manifest_v1(manifest_path, verify_sources=True)
    return LongrunConfigV1(
        run_dir=run_dir,
        manifest_path=manifest_path,
        manifest_sha256=_sha256(manifest_path),
        native_baseline=NativeBaselineV1(
            pair_id=baseline_id,
            deck_sha256=baseline_deck_sha256,
            policy_sha256=baseline_policy_sha256,
            evaluator_sha256=evaluator_sha256,
            status=baseline_status,
        ),
        meta_train_ids=tuple(manifest.split_ids["META_TRAIN"]),
        meta_dev_ids=tuple(manifest.split_ids["META_DEV"]),
        meta_final_ids=tuple(manifest.split_ids["META_FINAL"]),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--baseline-deck-sha256", required=True)
    parser.add_argument("--baseline-policy-sha256", required=True)
    parser.add_argument("--evaluator-sha256", required=True)
    parser.add_argument("--baseline-status", choices=("PROVEN", "UNPROVEN"), default="UNPROVEN")
    parser.add_argument("--execute", action="store_true", help="request execution; remains fail-closed without LONGRUN_READY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = build_config_v1(
            manifest_path=args.manifest,
            run_dir=args.run_dir,
            baseline_id=args.baseline_id,
            baseline_deck_sha256=args.baseline_deck_sha256,
            baseline_policy_sha256=args.baseline_policy_sha256,
            evaluator_sha256=args.evaluator_sha256,
            baseline_status=args.baseline_status,
        )
        if args.execute:
            # No runner is accepted by this CLI.  This is an intentional safety
            # boundary: a user must opt into a Python adapter after reviewing
            # gate.json and the sealed package closure.
            initialize_longrun_v1(config, execute=False)
            result = launch_longrun_v1(config, execute=True, runner=None)
        else:
            initialize_longrun_v1(config, execute=False)
            result = launch_longrun_v1(config, execute=False)
    except LongrunError as exc:
        print(json.dumps({"status": "HARD_EXTERNAL_BLOCKER", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
