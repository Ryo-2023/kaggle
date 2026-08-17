"""Materialize the research-only Rule v0 ABILITY+120 weighted48 bridge."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.rule_v0_main_ability_weighted48_v1 import (  # noqa: E402
    DEFAULT_SCHEDULE,
    build_rule_v0_main_ability_weighted48_v1,
    verify_rule_v0_main_ability_weighted48_v1,
)


def _write_new(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to replace existing artifact: {path}")
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    if tmp.exists():
        raise FileExistsError(f"temporary artifact already exists: {tmp}")
    try:
        with tmp.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def build_artifact(*, output: Path, schedule: Path = DEFAULT_SCHEDULE, base_seed: int = 14910000) -> dict[str, object]:
    artifact = build_rule_v0_main_ability_weighted48_v1(repo_root=ROOT, schedule_path=schedule, base_seed=base_seed)
    manifest = verify_rule_v0_main_ability_weighted48_v1(artifact["manifest"], repo_root=ROOT)
    sidecar = {
        "schema_version": manifest["schema_version"],
        "screen_sha256": manifest["screen_sha256"],
        "execution_allowed": False,
        "candidate_games": [game.to_payload() for game in artifact["candidate_games"]],
        "control_games": [game.to_payload() for game in artifact["control_games"]],
    }
    _write_new(output / "manifest.json", manifest)
    _write_new(output / "games.json", sidecar)
    return {"output": str(output.resolve()), "screen_sha256": manifest["screen_sha256"], "games": 96, "execution_allowed": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE)
    parser.add_argument("--base-seed", type=int, default=14910000)
    args = parser.parse_args(argv)
    print(json.dumps(build_artifact(output=args.output, schedule=args.schedule, base_seed=args.base_seed), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
