#!/usr/bin/env python3
"""Write a native public-only self-rollout contract in dry-run mode.

The command never imports a native agent, starts an evaluator, launches a
subprocess, or collects a game.  It only binds caller-supplied hashes,
permission metadata, and a deterministic common24 (96-game) schedule into a
research-only manifest.  ``--execute`` is rejected fail-closed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from mage_ptcg.meta_specialist.native_public_rollout_collector_v1 import (
    NativePublicRolloutAuthorizationV1,
    NativePublicRolloutCollectorError,
    NativePublicRolloutIdentityV1,
    build_common24_plan_v1,
    materialize_native_public_rollout_dry_run_v1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-manifest", required=True, type=Path)
    parser.add_argument(
        "--repo-root",
        required=False,
        type=Path,
        help="Repository root that must contain the new dry-run output.",
    )
    parser.add_argument("--identity-json", required=True, type=Path)
    parser.add_argument("--authorization-json", required=True, type=Path)
    parser.add_argument("--opponents-json", required=True, type=Path)
    parser.add_argument("--base-seed", required=True, type=int)
    parser.add_argument("--execute", action="store_true", help="Always rejected; dry-run only.")
    return parser


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativePublicRolloutCollectorError(f"cannot read JSON input: {path}") from exc


def _identity(path: Path) -> NativePublicRolloutIdentityV1:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise NativePublicRolloutCollectorError("identity JSON must be an object")
    return NativePublicRolloutIdentityV1(**payload)


def _authorization(path: Path) -> NativePublicRolloutAuthorizationV1:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise NativePublicRolloutCollectorError("authorization JSON must be an object")
    payload = dict(payload)
    payload["allowed_usages"] = tuple(payload.get("allowed_usages", ()))
    return NativePublicRolloutAuthorizationV1(**payload)


def _plan(path: Path, base_seed: int):
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise NativePublicRolloutCollectorError("opponents JSON must be an object")
    opponents = payload.get("opponent_ids")
    families = payload.get("opponent_families")
    if not isinstance(opponents, list) or not isinstance(families, dict):
        raise NativePublicRolloutCollectorError(
            "opponents JSON requires opponent_ids list and opponent_families object"
        )
    return build_common24_plan_v1(
        opponent_ids=opponents,
        opponent_families=families,
        base_seed=base_seed,
        pool_manifest_path=payload.get("pool_manifest_path"),
        pool_manifest_sha256=payload.get("pool_manifest_sha256"),
        pool_manifest_semantic_sha256=payload.get("pool_manifest_semantic_sha256"),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.execute:
        print("ERROR: --execute is disabled; this command is dry-run only", file=sys.stderr)
        return 2
    if args.repo_root is None:
        print("ERROR: --repo-root is required for a contained dry-run output", file=sys.stderr)
        return 2
    try:
        result = materialize_native_public_rollout_dry_run_v1(
            output_manifest=args.output_manifest,
            identity=_identity(args.identity_json),
            authorization=_authorization(args.authorization_json),
            plan=_plan(args.opponents_json, args.base_seed),
            repo_root=args.repo_root,
        )
    except (NativePublicRolloutCollectorError, FileExistsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
