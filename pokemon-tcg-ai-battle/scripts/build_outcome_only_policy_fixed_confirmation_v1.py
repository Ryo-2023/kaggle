#!/usr/bin/env python3
"""Materialize a seed-disjoint four-block policy-fixed confirmation plan."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_policy_fixed_confirmation_v1 import (  # noqa: E402
    build_policy_fixed_confirmation_v1,
    verify_policy_fixed_confirmation_v1,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_new(path: Path, value: object) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    raw = _canonical(value) + b"\n"
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to replace existing artifact: {path}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_ROOT)
    parser.add_argument("--parent-bridge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    artifact = build_policy_fixed_confirmation_v1(
        repo_root=args.repo_root,
        parent_bridge_path=args.parent_bridge,
        block_count=4,
    )
    verify_policy_fixed_confirmation_v1(artifact["manifest"], repo_root=args.repo_root)
    output = args.output.resolve()
    _write_new(output, artifact["manifest"])
    sidecar = {
        "schema_version": artifact["manifest"]["schema_version"],
        "confirmation_sha256": artifact["manifest"]["confirmation_sha256"],
        "execution_allowed": False,
        "control_games": [game.to_payload() for game in artifact["control_games"]],
        "candidate_games": [game.to_payload() for game in artifact["candidate_games"]],
    }
    sidecar_path = output.with_name(f"{output.stem}.games.json")
    _write_new(sidecar_path, sidecar)
    print(json.dumps({
        "manifest": str(output),
        "games": str(sidecar_path),
        "confirmation_sha256": artifact["manifest"]["confirmation_sha256"],
        "block_count": artifact["manifest"]["block_count"],
        "slot_count": len(artifact["manifest"]["slots"]),
        "execution_allowed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
