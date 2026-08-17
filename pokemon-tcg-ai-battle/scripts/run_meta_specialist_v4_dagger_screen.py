#!/usr/bin/env python3
"""Collect actor-visible learner states for a bounded V4 DAgger screen.

This runner only captures transitions returned by the existing actor-pool
boundary. It does not query private engine state and it never trains or
promotes a checkpoint by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorJobConfigV1,
    current_repo_commit_v1,
    run_one_actor_game_v1,
)
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402


SCREEN_SCHEMA_V4 = "meta-specialist-v4-dagger-screen-v2"
_HEX = frozenset("0123456789abcdef")


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _subject_deck_identity(path: Path) -> tuple[Path, str]:
    """Return the immutable leaf path and bytes hash for a subject deck."""
    if not isinstance(path, Path):
        raise ValueError("subject deck path is invalid")
    if path.is_symlink():
        raise ValueError("subject deck must be a regular non-symlink file")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("subject deck is missing") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError("subject deck must be a regular non-symlink file")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ValueError("subject deck cannot be read") from exc
    return resolved, hashlib.sha256(raw).hexdigest()


def _atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"DAgger screen output already exists: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic_bytes(path, raw)


def _atomic_progress_json(path: Path, payload: dict[str, object]) -> None:
    """Atomically replace a progress snapshot; final outputs remain immutable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_progress(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    _atomic_progress_json(path, {
        "schema": "meta-specialist-v4-dagger-screen-progress-v1",
        "updated_unix": time.time(),
        **payload,
    })


def _atomic_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> str:
    raw = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )
    digest = hashlib.sha256(raw).hexdigest()
    _atomic_bytes(path, raw)
    return digest


def build_dagger_jobs_v4(
    *,
    checkpoint: Path,
    checkpoint_file_sha256: str,
    checkpoint_tensor_state_sha256: str,
    subject_deck_csv: Path,
    subject_archetype_id: str,
    source_commit: str,
    opponent_count: int,
    games_per_seat: int,
    base_seed: int,
) -> tuple[ActorJobConfigV1, ...]:
    """Build fixed-pool, opponent-major, seat-balanced V4 actor jobs."""
    _require_sha(checkpoint_file_sha256, field="checkpoint_file_sha256")
    _require_sha(checkpoint_tensor_state_sha256, field="checkpoint_tensor_state_sha256")
    if type(opponent_count) is not int or not 1 <= opponent_count <= len(EVAL_HELD_OUT_V1):
        raise ValueError("opponent_count must be between 1 and six")
    if type(games_per_seat) is not int or not 1 <= games_per_seat <= 32:
        raise ValueError("games_per_seat must be between 1 and 32")
    if type(base_seed) is not int or base_seed < 0:
        raise ValueError("base_seed must be nonnegative")
    jobs: list[ActorJobConfigV1] = []
    index = 0
    for opponent_id in EVAL_HELD_OUT_V1[:opponent_count]:
        for seat in (0, 1):
            for repetition in range(games_per_seat):
                job_id = f"v4-dagger-{opponent_id}-seat{seat}-game{repetition:03d}-seed{base_seed + index}"
                jobs.append(ActorJobConfigV1(
                    job_id=job_id,
                    archetype_id=subject_archetype_id,
                    deck_csv_path=str(subject_deck_csv),
                    source_commit=source_commit,
                    env_seed=base_seed + index,
                    seat=seat,
                    behavior_kind="neural_specialist_v4",
                    behavior_identity=checkpoint_file_sha256,
                    opponent_kind=opponent_id,
                    neural_checkpoint_path=str(checkpoint),
                    neural_checkpoint_file_sha256=checkpoint_file_sha256,
                    neural_checkpoint_tensor_state_sha256=checkpoint_tensor_state_sha256,
                    decoding_mode="greedy",
                    sampling_seed=0,
                    max_steps=2000,
                    timeout_seconds=600.0,
                ))
                index += 1
    return tuple(jobs)


def _game_component(job: ActorJobConfigV1) -> tuple[str, str, str]:
    game_id = hashlib.sha256(f"meta-specialist-v4-dagger-game:{job.job_id}".encode("utf-8")).hexdigest()
    # Partition at the complete-game boundary.  A stable first-byte split is
    # sufficient for a bounded screen and cannot leak a prefix row across sets.
    partition = "validation" if int(game_id[:2], 16) < 64 else "train"
    return game_id, game_id, partition


def _fault_payload(result: object) -> dict[str, object] | None:
    fault = getattr(result, "fault", None)
    if fault is None:
        return None
    return {
        "kind": str(getattr(fault, "kind", "unknown")),
        "detail": str(getattr(fault, "detail", "unknown")),
    }


def collect_dagger_screen_v4(
    *,
    jobs: tuple[ActorJobConfigV1, ...],
    checkpoint: Path,
    output: Path,
    transitions_output: Path,
    progress_path: Path | None = None,
    lane: str | None = None,
) -> dict[str, object]:
    """Run jobs and publish a non-overwriting screen plus transition sidecar."""
    if output.exists() or transitions_output.exists():
        raise ValueError("DAgger screen output already exists")
    if not jobs:
        raise ValueError("DAgger screen requires at least one job")
    checkpoint_file_sha256 = jobs[0].neural_checkpoint_file_sha256
    checkpoint_tensor_state_sha256 = jobs[0].neural_checkpoint_tensor_state_sha256
    if any(
        job.neural_checkpoint_file_sha256 != checkpoint_file_sha256
        or job.neural_checkpoint_tensor_state_sha256 != checkpoint_tensor_state_sha256
        for job in jobs
    ):
        raise ValueError("DAgger screen jobs do not share one checkpoint identity")
    subject_archetype_id = jobs[0].archetype_id
    if not isinstance(subject_archetype_id, str) or not subject_archetype_id:
        raise ValueError("subject archetype is invalid")
    subject_deck_path, subject_deck_sha = _subject_deck_identity(Path(jobs[0].deck_csv_path))
    for job in jobs:
        if job.archetype_id != subject_archetype_id:
            raise ValueError("DAgger screen jobs must share the same subject archetype")
        job_deck_path, job_deck_sha = _subject_deck_identity(Path(job.deck_csv_path))
        if job_deck_path != subject_deck_path or job_deck_sha != subject_deck_sha:
            raise ValueError("DAgger screen jobs must share the same subject deck")
    transition_rows: list[dict[str, object]] = []
    game_rows: list[dict[str, object]] = []
    faults = 0
    try:
        from tqdm import tqdm
        iterator = tqdm(
            jobs, total=len(jobs), desc="v4-dagger-screen", unit="game",
            dynamic_ncols=True, file=sys.stdout, disable=not sys.stdout.isatty(),
        )
    except ImportError:  # pragma: no cover - tqdm is part of the project venv
        iterator = jobs
    _write_progress(progress_path, {
        "status": "running", "stage": "games", "games_requested": len(jobs),
        "games_completed": 0, "faults": 0, "transition_records": 0,
    })
    completed_now = 0
    for job_index, job in enumerate(iterator, start=1):
        game_id, component_id, partition = _game_component(job)
        game_output = output.parent / f".{output.stem}.games" / job.job_id
        _write_progress(progress_path, {
            "status": "running", "stage": "game", "games_requested": len(jobs),
            "games_finished": job_index - 1, "games_completed": completed_now,
            "faults": faults, "transition_records": len(transition_rows),
            "current_job_index": job_index, "current_job_id": job.job_id,
            "current_job_started_unix": time.time(),
        })
        try:
            result = run_one_actor_game_v1(job=job, output_dir=game_output)
        except BaseException as exc:
            _write_progress(progress_path, {
                "status": "failed", "stage": "game", "games_requested": len(jobs),
                "games_finished": job_index - 1, "games_completed": sum(
                    row["status"] == "completed" and row["fault"] is None for row in game_rows
                ), "faults": faults, "transition_records": len(transition_rows),
                "failed_job_index": job_index, "failed_job_id": job.job_id,
                "error_type": type(exc).__name__, "error": str(exc)[:2000],
            })
            raise
        fault = _fault_payload(result)
        status = str(getattr(result, "status", "faulted"))
        if status != "completed" or fault is not None:
            faults += 1
        transitions = tuple(getattr(result, "transitions", ())) if status == "completed" else ()
        for transition_index, transition in enumerate(transitions):
            if not hasattr(transition, "to_dict"):
                raise ValueError("actor-pool returned an invalid trajectory transition")
            transition_rows.append({
                "schema": "meta-specialist-v4-dagger-transition-v1",
                "game_id": game_id,
                "episode_group": game_id,
                "component_id": component_id,
                "partition": partition,
                "opponent_id": job.opponent_kind,
                "seat": job.seat,
                "env_seed": job.env_seed,
                "transition_index": transition_index,
                "transition": transition.to_dict(),
            })
        game_rows.append({
            "job_id": job.job_id,
            "opponent_id": job.opponent_kind,
            "seat": job.seat,
            "env_seed": job.env_seed,
            "status": status,
            "outcome": getattr(result, "outcome", None),
            "winner": getattr(result, "winner", None),
            "steps": getattr(result, "steps", None),
            "transitions": len(transitions),
            "fault": fault,
        })
        completed_now = sum(row["status"] == "completed" and row["fault"] is None for row in game_rows)
        if hasattr(iterator, "set_postfix"):
            iterator.set_postfix(done=completed_now, faults=faults, transitions=len(transition_rows), refresh=False)
        _write_progress(progress_path, {
            "status": "running", "stage": "games", "games_requested": len(jobs),
            "games_completed": completed_now, "games_finished": job_index,
            "faults": faults, "transition_records": len(transition_rows),
            "last_job_id": job.job_id,
        })

    transition_sha = _atomic_jsonl(transitions_output, transition_rows)
    completed = sum(row["status"] == "completed" and row["fault"] is None for row in game_rows)
    payload: dict[str, object] = {
        "schema": SCREEN_SCHEMA_V4,
        "status": "VALID" if faults == 0 else "INVALID_FAULTS",
        "promotion_authority": False,
        "subject_archetype_id": subject_archetype_id,
        "subject_deck_csv_path": str(subject_deck_path),
        "subject_deck_file_sha256": subject_deck_sha,
        "checkpoint": {
            "path": str(checkpoint.resolve()),
            "file_sha256": checkpoint_file_sha256,
            "tensor_state_sha256": checkpoint_tensor_state_sha256,
        },
        "games_requested": len(jobs),
        "games_completed": completed,
        "faults": faults,
        "transition_records": len(transition_rows),
        "transitions_path": str(transitions_output.resolve()),
        "transitions_file_sha256": transition_sha,
        "games": game_rows,
    }
    if lane is not None:
        if not isinstance(lane, str) or not lane:
            raise ValueError("DAgger screen lane is invalid")
        payload["lane"] = lane
    _atomic_json(output, payload)
    _write_progress(progress_path, {
        "status": payload["status"], "stage": "complete", "games_requested": len(jobs),
        "games_completed": completed, "faults": faults,
        "transition_records": len(transition_rows), "output": str(output),
    })
    return payload


def _checkpoint_provenance(path: Path, expected_file_sha256: str, expected_tensor_sha256: str) -> None:
    raw = path.read_bytes()
    actual_file_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_file_sha256 != expected_file_sha256:
        raise ValueError("checkpoint file SHA does not match the requested identity")
    try:
        import torch

        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
        descriptor = payload["descriptor"]
        actual_tensor_sha256 = descriptor["tensor_state_sha256"]
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, EOFError) as exc:
        raise ValueError("checkpoint is not a closed V4 artifact") from exc
    if actual_tensor_sha256 != expected_tensor_sha256:
        raise ValueError("checkpoint tensor-state SHA does not match the requested identity")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-file-sha256", required=True)
    parser.add_argument("--checkpoint-tensor-state-sha256", required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--opponent-count", type=int, default=6)
    parser.add_argument("--games-per-seat", type=int, default=1)
    parser.add_argument("--base-seed", type=int, default=11000000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transitions-output", type=Path)
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument("--lane", default=None)
    args = parser.parse_args()
    file_sha = _require_sha(args.checkpoint_file_sha256, field="checkpoint_file_sha256")
    tensor_sha = _require_sha(args.checkpoint_tensor_state_sha256, field="checkpoint_tensor_state_sha256")
    _checkpoint_provenance(args.checkpoint, file_sha, tensor_sha)
    transitions_output = args.transitions_output or args.output.with_suffix(".transitions.jsonl")
    jobs = build_dagger_jobs_v4(
        checkpoint=args.checkpoint,
        checkpoint_file_sha256=file_sha,
        checkpoint_tensor_state_sha256=tensor_sha,
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
        source_commit=current_repo_commit_v1(),
        opponent_count=args.opponent_count,
        games_per_seat=args.games_per_seat,
        base_seed=args.base_seed,
    )
    collect_dagger_screen_v4(
        jobs=jobs, checkpoint=args.checkpoint, output=args.output,
        transitions_output=transitions_output,
        progress_path=args.progress_path,
        lane=args.lane,
    )
    print(json.dumps(json.loads(args.output.read_text(encoding="utf-8")), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
