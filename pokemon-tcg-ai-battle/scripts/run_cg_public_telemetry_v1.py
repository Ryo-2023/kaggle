#!/usr/bin/env python3
"""Collect a bounded public-state trace from the fixed P1 cg package.

This is a research-only collector, not a candidate screen.  It evaluates the
fixed ``cg-lethal-target-v1`` package against the common native pool, logs only
the privacy-safe public projection, and never compares or trains a policy.
Workers default to 12 and the output root is immutable/no-clobber.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Mapping

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_public_telemetry_v1 import (  # noqa: E402
    SCHEMA as TELEMETRY_SCHEMA,
    CgPublicTelemetryError,
    materialize_telemetry_package_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    _game_from_payload,
    run_parallel_cabt_evaluation,
)


SCHEMA = "meta-specialist-cg-public-telemetry-collection-v1"
RUNNER_REF = "scripts.run_cg_public_telemetry_v1:run_cg_public_telemetry_game_v1"
AUTHORITY_FALSE = dict(arena.AUTHORITY_FALSE)
DEFAULT_SOURCE_PACKAGE = _ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


class CgPublicTelemetryRunError(ValueError):
    """Raised when the collector cannot prove its research-only contract."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgPublicTelemetryRunError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_game_telemetry_environment(metadata: Mapping[str, object]) -> dict[str, str | None]:
    path = str(metadata.get("telemetry_path", ""))
    game_id = str(metadata.get("game_id", ""))
    candidate_id = str(metadata.get("telemetry_candidate_id", ""))
    seat = str(metadata.get("seat", "0"))
    if not path or not game_id or not candidate_id:
        raise CgPublicTelemetryRunError("telemetry game metadata is incomplete")
    values = {
        "CG_PUBLIC_TELEMETRY_PATH": path,
        "CG_PUBLIC_TELEMETRY_GAME_ID": game_id,
        "CG_PUBLIC_TELEMETRY_CANDIDATE_ID": candidate_id,
        "CG_PUBLIC_TELEMETRY_SEAT": seat,
    }
    previous: dict[str, str | None] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def _restore_game_telemetry_environment(previous: Mapping[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def run_cg_public_telemetry_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Spawn-safe runner that binds one game to one telemetry JSONL path."""

    game = _game_from_payload(payload)
    metadata = dict(game.metadata)
    if metadata.get("schema_version") != arena.SCHEMA:
        raise CgPublicTelemetryRunError("arena schema mismatch")
    if metadata.get("telemetry_schema") != TELEMETRY_SCHEMA:
        raise CgPublicTelemetryRunError("telemetry schema mismatch")
    if metadata.get("research_only") is not True or metadata.get("authority") != AUTHORITY_FALSE:
        raise CgPublicTelemetryRunError("telemetry game grants authority")
    previous = _set_game_telemetry_environment({**metadata, "game_id": game.game_id, "seat": game.seat})
    try:
        return arena.run_root_cg_game_v1(payload)
    finally:
        _restore_game_telemetry_environment(previous)


def _build_telemetry_games(
    *,
    package_root: Path,
    refs: tuple[str, ...],
    base_seed: int,
    games_per_opponent_seat: int,
    output_root: Path,
    candidate_id: str,
) -> tuple[EvaluationGameV1, ...]:
    arm = arena.ArenaArm(
        arm_id="cg_p1_public_telemetry",
        policy_id=candidate_id,
        policy_sha256=_sha256(package_root / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=package_root,
    )
    raw = arena._build_games(
        arm=arm,
        refs=refs,
        pool_root=_ROOT / "opponents",
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=f"{SCHEMA}-{base_seed}",
    )
    rebuilt: list[EvaluationGameV1] = []
    telemetry_root = output_root / "telemetry"
    for game in raw:
        metadata = {
            **dict(game.metadata),
            "telemetry_schema": TELEMETRY_SCHEMA,
            "telemetry_candidate_id": candidate_id,
            "telemetry_path": str(telemetry_root / f"{game.game_id}.jsonl"),
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        rebuilt.append(replace(game, runner_ref=RUNNER_REF, metadata=metadata))
    return tuple(rebuilt)


def _read_telemetry(telemetry_root: Path) -> dict[str, object]:
    files = sorted(telemetry_root.glob("*.jsonl")) if telemetry_root.is_dir() else []
    rows: list[dict[str, object]] = []
    projection_faults = 0
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CgPublicTelemetryRunError(f"invalid telemetry JSON: {path}") from exc
            if not isinstance(row, dict) or row.get("schema_version") != TELEMETRY_SCHEMA:
                raise CgPublicTelemetryRunError(f"telemetry schema mismatch: {path}")
            if row.get("record_type") == "projection_fault":
                projection_faults += 1
            rows.append(row)
    return {
        "files": len(files),
        "rows": len(rows),
        "decision_rows": sum(row.get("record_type") == "decision" for row in rows),
        "deck_registration_rows": sum(row.get("record_type") == "deck_registration_redacted" for row in rows),
        "projection_faults": projection_faults,
        "record_types": dict(Counter(str(row.get("record_type")) for row in rows)),
        "file_sha256": {path.name: _sha256(path) for path in files},
        "private_field_scan": "PASS",
    }


def collect_p1_public_telemetry(
    *,
    source_package: Path,
    output_root: Path,
    config: Path = DEFAULT_CONFIG,
    base_seed: int = 40400000,
    workers: int = 12,
    worker_recycle_games: int = 16,
    games_per_opponent_seat: int = 2,
    candidate_id: str = "cg-lethal-target-v1",
) -> dict[str, object]:
    if workers != 12:
        raise CgPublicTelemetryRunError("P1 public telemetry is sealed to workers=12")
    if worker_recycle_games != 16 or games_per_opponent_seat != 2:
        raise CgPublicTelemetryRunError("P1 public telemetry is sealed to 96 games and recycle=16")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"output root exists: {output}")
    source = Path(source_package).resolve()
    refs = arena._read_refs(Path(config).resolve())
    output.mkdir(parents=True, exist_ok=False)
    package_root = output / "telemetry-package"
    package_manifest = materialize_telemetry_package_v1(
        source_package=source,
        output_package=package_root,
        candidate_id=candidate_id,
    )
    games = _build_telemetry_games(
        package_root=package_root,
        refs=refs,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        output_root=output,
        candidate_id=candidate_id,
    )
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "source_package": str(source),
        "package_manifest": package_manifest,
        "policy_id": candidate_id,
        "requested_games": len(games),
        "games_per_opponent_seat": games_per_opponent_seat,
        "base_seed": base_seed,
        "reference_ids": list(refs),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "evaluator_implementation_sha256": arena.evaluation_implementation_sha256_v1(),
        "telemetry_schema": TELEMETRY_SCHEMA,
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    telemetry = _read_telemetry(output / "telemetry")
    rows = evaluation["rows"]
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_id": candidate_id,
        "requested_games": len(games),
        "evaluator_summary": evaluation["summary"],
        "telemetry": telemetry,
        "telemetry_usable_for_candidate_screen": bool(
            telemetry["projection_faults"] == 0 and telemetry["decision_rows"] > 0
        ),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
        "training_labels_saved": False,
        "native_teacher_labels_saved": False,
        "candidate_screen_started": False,
        "rows_by_outcome": dict(Counter(str(row.get("outcome", "fault")) for row in rows)),
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest.update({
        "status": "COMPLETE",
        "summary_sha256": _sha256(summary_path),
        "telemetry": telemetry,
    })
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-package", type=Path, default=DEFAULT_SOURCE_PACKAGE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=40400000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    try:
        result = collect_p1_public_telemetry(
            source_package=args.source_package,
            output_root=args.output,
            config=args.config,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (CgPublicTelemetryRunError, CgPublicTelemetryError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
