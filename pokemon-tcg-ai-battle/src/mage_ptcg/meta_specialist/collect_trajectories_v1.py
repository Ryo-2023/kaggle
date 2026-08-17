"""Human-runnable trajectory collection: plans and drives real ``ActorPoolV1`` jobs.

Everything this module needs to actually collect a real CABT trajectory
already exists and is committed (``actor_pool_v1.py``): a validated job
config, a spawn-based worker pool with per-job timeout/resume, and an
atomic, content-addressed game-record writer.  What has been missing is a
human-runnable entry point that (a) resolves an archetype/lane name to the
one qualified deck the seed-qualification report actually attests for it,
(b) plans a seat-balanced set of jobs across one or more lanes, (c) drives
``ActorPoolV1`` to completion while showing live progress on a terminal
without spamming a line per game, and (d) reports an honest run summary --
never counting a faulted game as collected.

This module drives ``actor_pool_v1.py``'s public API only; it does not
reimplement or modify any of that module's scheduling, timeout, or
resume logic.  A background thread simply calls the real, blocking
``ActorPoolV1.run_jobs`` once with the full job plan (which already knows
how to resume-skip a completed game); the calling thread only *polls the
filesystem* for cheap, best-effort progress -- the authoritative outcome
of every job always comes from ``run_jobs``'s own return value.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

from mage_ptcg.meta_specialist.actor_pool_v1 import (
    DEFAULT_MAX_STEPS_V1,
    DEFAULT_TIMEOUT_SECONDS_V1,
    ActorJobConfigV1,
    ActorPoolJobOutcomeV1,
    ActorPoolV1,
    ActorPoolV1Error,
    current_repo_commit_v1,
    derive_actor_job_id_v1,
    derive_game_sampling_seed_v1,
    neural_checkpoint_behavior_identity_v1,
    rule_agent_behavior_identity_v1,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seat_for_game_v1
from mage_ptcg.meta_specialist.seed_qualification_report_v1 import (
    SeedQualificationReportV1Error,
    read_seed_qualification_report_v1,
)
from mage_ptcg.offline_scaleup.progress import ProgressReporter

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_QUALIFICATION_REPORT_PATH_V1 = (
    _REPO_ROOT / "runs" / "meta-specialist-seed-qualification" / "seed_qualification_report_v1.json"
)
DEFAULT_MATERIALIZED_DECK_DIR_V1 = (
    _REPO_ROOT / "runs" / "meta-specialist-seed-qualification" / "materialized"
)
DEFAULT_ACTOR_POOL_OUTPUT_BASE_V1 = _REPO_ROOT / "runs" / "meta-specialist-actor-pool"

RUN_SUMMARY_SCHEMA_V1 = "meta-specialist-collect-trajectories-run-summary-v1"
_FAULTED_JOBS_LISTED_CAP_V1 = 50
_FAULT_REASON_EXCERPT_CHARS_V1 = 300
_POLL_INTERVAL_SECONDS_V1 = 1.0
_NONTTY_SNAPSHOT_INTERVAL_SECONDS_V1 = 10.0


class CollectTrajectoriesError(ValueError):
    """Raised for any refuse-closed condition in trajectory-collection planning."""


# --------------------------------------------------------------------------
# Deck/lane resolution -- reads the archetype->deck mapping only from the
# already-published seed qualification report; never hardcodes a deck path.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QualifiedLaneV1:
    archetype_id: str
    deck_csv_path: Path
    deck_identity: str
    priority: int
    qualified_asset_id: str


def load_qualified_lanes_v1(
    *,
    report_path: Path = DEFAULT_SEED_QUALIFICATION_REPORT_PATH_V1,
    materialized_dir: Path = DEFAULT_MATERIALIZED_DECK_DIR_V1,
) -> dict[str, QualifiedLaneV1]:
    """Read the seed qualification report and return every ``qualified`` lane.

    The materialized deck path is derived from report fields
    (``runtime_id``/``priority``/``deck_identity``) using the same naming
    formula ``scripts/qualify_meta_specialist_seeds.py`` already uses to
    write these files -- never a literal per-archetype path.
    """
    try:
        report = read_seed_qualification_report_v1(report_path)
    except OSError as exc:
        raise CollectTrajectoriesError(
            f"could not read seed qualification report at {report_path}: {exc}"
        ) from exc
    except SeedQualificationReportV1Error as exc:
        raise CollectTrajectoriesError(
            f"seed qualification report at {report_path} failed validation: {exc}"
        ) from exc

    lanes: dict[str, QualifiedLaneV1] = {}
    for candidate in report["candidates"]:
        if candidate["outcome"] != "qualified":
            continue
        runtime_id = candidate["runtime_id"]
        deck_identity = candidate["deck_identity"]
        priority = candidate["priority"]
        deck_csv_path = Path(materialized_dir) / f"{runtime_id}-p{priority}-{deck_identity}.csv"
        if not deck_csv_path.is_file():
            raise CollectTrajectoriesError(
                f"qualified deck for archetype {runtime_id!r} is missing on disk at "
                f"{deck_csv_path} (the seed qualification report and the materialized/ "
                "directory have drifted apart)"
            )
        if runtime_id in lanes:
            raise CollectTrajectoriesError(
                f"seed qualification report names more than one qualified deck for "
                f"archetype {runtime_id!r}"
            )
        lanes[runtime_id] = QualifiedLaneV1(
            archetype_id=runtime_id,
            deck_csv_path=deck_csv_path,
            deck_identity=deck_identity,
            priority=priority,
            qualified_asset_id=candidate["qualified_asset_id"],
        )
    return lanes


def resolve_requested_lanes_v1(
    requested: str, qualified: Mapping[str, QualifiedLaneV1],
) -> tuple[str, ...]:
    """Resolve ``--lanes`` (``"all"`` or a comma-separated archetype list).

    Fails closed with a clear message for any archetype that is not
    ``qualified`` in the seed qualification report -- whether it is simply
    unknown or a real, registered-but-not-yet-qualified archetype.
    """
    if type(requested) is not str or not requested.strip():
        raise CollectTrajectoriesError("--lanes must be 'all' or a comma-separated archetype id list")
    if requested.strip().lower() == "all":
        if not qualified:
            raise CollectTrajectoriesError(
                "no archetype is qualified in the seed qualification report; nothing to collect"
            )
        return tuple(sorted(qualified))
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in requested.split(","):
        archetype_id = raw.strip()
        if not archetype_id or archetype_id in seen:
            continue
        seen.add(archetype_id)
        if archetype_id not in qualified:
            raise CollectTrajectoriesError(
                f"archetype_id {archetype_id!r} is not a qualified deck in the seed "
                f"qualification report; qualified archetypes are: {sorted(qualified)}"
            )
        ordered.append(archetype_id)
    if not ordered:
        raise CollectTrajectoriesError("--lanes named no archetype id")
    return tuple(ordered)


# --------------------------------------------------------------------------
# Seat-balanced, resumable job planning.
# --------------------------------------------------------------------------


def _distribute_games_v1(total: int, lane_count: int) -> list[int]:
    if total <= 0:
        raise CollectTrajectoriesError("--num-games must be a positive int")
    if lane_count <= 0:
        raise CollectTrajectoriesError("at least one lane must be selected")
    base, remainder = divmod(total, lane_count)
    return [base + (1 if index < remainder else 0) for index in range(lane_count)]


def build_collection_plan_v1(
    *,
    lanes: Sequence[QualifiedLaneV1],
    num_games: int,
    base_seed: int,
    source_commit: str,
    behavior_kind: str,
    behavior_identity: str,
    opponent_kind: str,
    decoding_mode: str,
    sampling_seed: int,
    pool_epoch: int,
    policy_lag: int,
    non_terminal_discount: float,
    max_steps: int,
    timeout_seconds: float,
    neural_checkpoint_path: str,
    opponent_kinds: Sequence[str] | None = None,
) -> list[ActorJobConfigV1]:
    """Build one seat-balanced, content-addressed job per planned game.

    ``env_seed`` is a single run-wide counter starting at ``base_seed``, so every
    planned job in the run gets a distinct seed -- across lanes too -- and the
    same command re-run later reproduces an identical plan (hence identical
    ``job_id``s, hence resumable).

    ``opponent_kinds`` cycles the opponent across the planned games; ``None``
    keeps the single ``opponent_kind`` for every game.  Training against one
    opponent while measuring against a different set produced a policy that beat
    its training opponent and lost to the evaluation pool
    (docs/evidence/vtrace-rl-degrades-against-eval-pool-20260807.md).

    Seats come from ``seat_for_game_v1``, i.e. the *cycle* number rather than the
    raw index, so the seat does not alias with the opponent rotation.  With
    ``index % 2`` and an even opponent count, every opponent would always be met
    from the same seat -- ``seat_counts`` would still look balanced run-wide
    while each matchup was played from one side only.  With a single opponent
    this reduces to the previous ``index % 2``.
    """
    if type(base_seed) is not int or base_seed < 0:
        raise CollectTrajectoriesError("--base-seed must be a nonnegative int")
    rotation = tuple(opponent_kinds) if opponent_kinds else (opponent_kind,)
    if not all(type(item) is str and item for item in rotation):
        raise CollectTrajectoriesError("every opponent kind must be a nonempty string")
    per_lane_counts = _distribute_games_v1(num_games, len(lanes))

    jobs: list[ActorJobConfigV1] = []
    seat_cursor: dict[str, int] = {name: 0 for name in rotation}
    env_seed = base_seed
    for lane, count in zip(lanes, per_lane_counts):
        deck_csv_path = str(lane.deck_csv_path)
        for index in range(count):
            opponent_kind = rotation[index % len(rotation)]
            # 座席はこの相手との対戦が何局目かで決める。周回番号で決めると、
            # 巡回長が収集局数を超えたときに一度も座席が入れ替わらない
            # (538 件の巡回に対し 200 局を集めたら全局が seat 0 だった)。
            seat = seat_cursor[opponent_kind] % 2
            seat_cursor[opponent_kind] += 1
            game_sampling_seed = derive_game_sampling_seed_v1(
                base_seed=sampling_seed,
                env_seed=env_seed,
                archetype_id=lane.archetype_id,
                opponent_kind=opponent_kind,
                seat=seat,
            )
            job_id = derive_actor_job_id_v1(
                archetype_id=lane.archetype_id,
                deck_csv_path=deck_csv_path,
                source_commit=source_commit,
                env_seed=env_seed,
                seat=seat,
                behavior_kind=behavior_kind,
                behavior_identity=behavior_identity,
                opponent_kind=opponent_kind,
                decoding_mode=decoding_mode,
                sampling_seed=game_sampling_seed,
            )
            jobs.append(ActorJobConfigV1(
                job_id=job_id,
                archetype_id=lane.archetype_id,
                deck_csv_path=deck_csv_path,
                source_commit=source_commit,
                env_seed=env_seed,
                seat=seat,
                behavior_kind=behavior_kind,
                behavior_identity=behavior_identity,
                opponent_kind=opponent_kind,
                pool_epoch=pool_epoch,
                policy_lag=policy_lag,
                non_terminal_discount=non_terminal_discount,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
                neural_checkpoint_path=neural_checkpoint_path,
                decoding_mode=decoding_mode,
                sampling_seed=game_sampling_seed,
            ))
            env_seed += 1
    return jobs


def load_opponent_schedule_v1(path: str | Path) -> tuple[str, ...]:
    """Expand a ``{opponent_id: weight}`` JSON file into a cycling rotation.

    A uniform rotation is not the same thing as full coverage.  After the pool
    was widened to cover 100% of the observed medal-zone decks, a uniform cycle
    still gave the archetype that is 37.1% of that zone only 4.8% of the games,
    while an archetype that is 1.6% of it kept 11.9%
    (docs/evidence/vtrace-rl-degrades-against-eval-pool-20260807.md).  Coverage
    and distribution are separate problems; this file fixes the second.

    Weights are integer game counts per cycle, so the schedule is exact and
    auditable rather than a float that rounds differently per run length.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(payload) is not dict or not payload:
        raise CollectTrajectoriesError(f"{path}: schedule must be a nonempty JSON object")
    rotation: list[str] = []
    for name, weight in payload.items():
        if type(name) is not str or not name:
            raise CollectTrajectoriesError(f"{path}: every key must be a nonempty opponent id")
        if type(weight) is not int or type(weight) is bool or weight < 0:
            raise CollectTrajectoriesError(
                f"{path}: weight for {name!r} must be a nonnegative int, got {weight!r}"
            )
        rotation.extend([name] * weight)
    if not rotation:
        raise CollectTrajectoriesError(f"{path}: every weight is zero; nothing to collect")
    return smooth_weighted_order_v1(payload)


def smooth_weighted_order_v1(weights: Mapping[str, int]) -> tuple[str, ...]:
    """Order the schedule so **every prefix** approximates the target ratios.

    A plain round-robin over the distinct names puts one of each first, so a run
    shorter than one full cycle collects a nearly uniform sample no matter what
    the weights say.  Measured: a 200-game run against a 538-entry cycle gave the
    28%-of-meta archetype no share at all and left the 6%-of-meta one at 14.5%.

    This is stride scheduling: each opponent's next position is
    ``(taken + 0.5) / weight``, and the smallest one goes next.  A prefix of any
    length then holds roughly ``weight / total`` of each opponent.
    """
    taken = {name: 0 for name in weights}
    order: list[str] = []
    total = sum(weights.values())
    for _ in range(total):
        name = min(
            (n for n in weights if taken[n] < weights[n]),
            key=lambda n: ((taken[n] + 0.5) / weights[n], n),
        )
        order.append(name)
        taken[name] += 1
    return tuple(order)


def _resolve_opponent_rotation_v1(
    opponent_kind: str, opponent_kinds: str | Sequence[str] | None,
) -> tuple[str, ...]:
    """Normalize the opponent argument into the rotation the plan will use.

    Accepts either the single ``--opponent-kind`` or a comma-separated
    ``--opponent-kinds``.  Duplicates are rejected rather than silently deduped:
    a repeated id would double that opponent's share of the training data, which
    is a schedule decision the caller should make explicitly.
    """
    if not opponent_kinds:
        return (opponent_kind,)
    if type(opponent_kinds) is str:
        names = [item.strip() for item in opponent_kinds.split(",") if item.strip()]
    else:
        names = [str(item).strip() for item in opponent_kinds if str(item).strip()]
    if not names:
        raise CollectTrajectoriesError("--opponent-kinds was given but is empty")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise CollectTrajectoriesError(
            f"--opponent-kinds repeats {duplicates}; list each opponent once"
        )
    return tuple(names)


def _validate_run_name_v1(run_name: object) -> str:
    if type(run_name) is not str or not run_name:
        raise CollectTrajectoriesError("--run-name must be a nonempty string")
    if run_name in (".", "..") or "/" in run_name or "\\" in run_name or run_name.startswith("."):
        raise CollectTrajectoriesError(
            f"--run-name must be a single safe directory-name component, got {run_name!r}"
        )
    return run_name


# --------------------------------------------------------------------------
# Best-effort filesystem progress polling (never authoritative -- see
# module docstring). The final summary is always built from run_jobs's own
# returned ActorPoolJobOutcomeV1 tuple.
# --------------------------------------------------------------------------


def _poll_progress_v1(
    *, pending: dict[str, Path], completed: set[str], faulted: set[str],
) -> None:
    for job_id in list(pending):
        games_dir = pending[job_id]
        if (games_dir / "record.json").is_file():
            completed.add(job_id)
            del pending[job_id]
        elif any(games_dir.glob("fault-*.json")):
            faulted.add(job_id)
            del pending[job_id]


def _atomic_write_json_v1(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _run_jobs_with_progress_v1(
    pool: ActorPoolV1, jobs: Sequence[ActorJobConfigV1], *, output_root: Path, run_name: str,
) -> tuple[ActorPoolJobOutcomeV1, ...]:
    pending = {job.job_id: output_root / "games" / job.job_id for job in jobs}
    completed: set[str] = set()
    faulted: set[str] = set()
    reporter = ProgressReporter(
        phase="collect-trajectories", total=len(jobs), run_id=run_name,
        workers=pool.num_workers, unit="game", interval_seconds=_NONTTY_SNAPSHOT_INTERVAL_SECONDS_V1,
        summary_path=output_root / "progress_summary.json",
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(pool.run_jobs, jobs, output_root=output_root)
        try:
            while True:
                try:
                    outcomes = future.result(timeout=_POLL_INTERVAL_SECONDS_V1)
                    break
                except concurrent.futures.TimeoutError:
                    _poll_progress_v1(pending=pending, completed=completed, faulted=faulted)
                    finished = len(completed) + len(faulted)
                    delta = finished - reporter.completed
                    if delta > 0:
                        reporter.update(delta, faulted=len(faulted))
                    else:
                        reporter.update(0, faulted=len(faulted))
        finally:
            reporter.close()
    return outcomes


# --------------------------------------------------------------------------
# Summary construction -- built only from the real, returned outcomes.
# --------------------------------------------------------------------------


def _empty_lane_summary_v1() -> dict[str, object]:
    return {
        "attempted": 0, "completed": 0, "resumed_skipped": 0, "faulted": 0, "timeout": 0,
        "transitions": 0,
        "seats": {"0": {"attempted": 0, "collected": 0}, "1": {"attempted": 0, "collected": 0}},
    }


def _survey_unplanned_existing_games_v1(
    games_root: Path, *, planned_job_ids: frozenset[str],
) -> dict[str, object]:
    """Count game records already in the output that this run's plan does not cover.

    ``derive_actor_job_id_v1`` binds a job's identity to ``source_commit``, so a
    single commit to this repository changes every job id and the previous run's
    games stop matching -- correctly, because trajectories collected under
    different code should not be silently treated as interchangeable.  What was
    missing was *saying so*: the summary reported ``resumed_skipped=0`` and
    nothing else, which reads as broken resume rather than as a deliberate
    refusal to reuse.  This survey reports the count so the operator can see that
    the earlier games are still on disk, still readable, and simply not claimed
    by this plan.

    It is descriptive only.  It never changes what gets collected or reused, and
    a record it cannot read is counted as unreadable rather than assumed absent.
    """
    if not games_root.is_dir():
        return {"count": 0, "transitions": 0, "unreadable": 0, "behavior_versions": []}
    count = 0
    transitions = 0
    unreadable = 0
    behavior_versions: set[str] = set()
    for record_path in games_root.glob("*/record.json"):
        if record_path.parent.name in planned_job_ids:
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            transitions += len(record["transitions"])
            behavior_versions.add(str(record["subject_behavior_version"]))
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
            continue
        count += 1
    return {
        "count": count,
        "transitions": transitions,
        "unreadable": unreadable,
        "behavior_versions": sorted(behavior_versions),
    }


def _summarize_v1(
    *, jobs: Sequence[ActorJobConfigV1], outcomes: Sequence[ActorPoolJobOutcomeV1],
) -> dict[str, object]:
    per_lane: dict[str, dict[str, object]] = {}
    faulted_jobs: list[dict[str, object]] = []
    final_attempts: list[dict[str, object]] = []
    totals = {"completed": 0, "resumed": 0, "faulted": 0, "timeout": 0, "transitions": 0}

    for job, outcome in zip(jobs, outcomes):
        identity = dict(outcome.game_identity) if outcome.game_identity is not None else None
        final_retry_index = outcome.retry_index
        final_attempts.append({
            "job_id": outcome.job_id,
            "status": outcome.status,
            "retry_index": final_retry_index,
            "game_identity": identity,
        })
        lane = per_lane.setdefault(job.archetype_id, _empty_lane_summary_v1())
        lane["attempted"] += 1
        seat_stats = lane["seats"][str(job.seat)]
        seat_stats["attempted"] += 1

        if outcome.status in ("completed", "resumed"):
            lane["completed" if outcome.status == "completed" else "resumed_skipped"] += 1
            lane["transitions"] += outcome.transitions_count
            seat_stats["collected"] += 1
            totals["transitions"] += outcome.transitions_count
            totals["completed" if outcome.status == "completed" else "resumed"] += 1
        else:
            key = "timeout" if outcome.status == "timeout" else "faulted"
            lane[key] += 1
            totals[key] += 1
            if len(faulted_jobs) < _FAULTED_JOBS_LISTED_CAP_V1:
                reason = outcome.fault_reason or ""
                if len(reason) > _FAULT_REASON_EXCERPT_CHARS_V1:
                    reason = reason[: _FAULT_REASON_EXCERPT_CHARS_V1 - 3] + "..."
                faulted_jobs.append({
                    "job_id": outcome.job_id, "archetype_id": job.archetype_id,
                    "seat": identity["seat"] if identity is not None else job.seat,
                    "env_seed": identity["environment_seed"] if identity is not None else job.env_seed,
                    "retry_index": final_retry_index, "game_identity": identity,
                    "status": outcome.status, "fault_reason": reason,
                })

    return {
        "per_lane": per_lane,
        "faulted_jobs": faulted_jobs,
        "final_attempts": final_attempts,
        "faulted_jobs_truncated": (totals["faulted"] + totals["timeout"]) > len(faulted_jobs),
        "games_completed": totals["completed"],
        "games_resumed_skipped": totals["resumed"],
        "games_faulted": totals["faulted"],
        "games_timeout": totals["timeout"],
        "transitions_collected": totals["transitions"],
    }


# --------------------------------------------------------------------------
# Entry point driven by the CLI (and directly by tests).
# --------------------------------------------------------------------------


def run_collect_trajectories_v1(
    *,
    lanes_arg: str,
    num_games: int,
    base_seed: int,
    workers: int,
    run_name: str,
    persistent_worker: bool = False,
    opponent_schedule: str = "",
    behavior_kind: str = "rule_agent",
    neural_checkpoint_path: str = "",
    decoding_mode: str = "greedy",
    sampling_seed: int = 0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS_V1,
    max_steps: int = DEFAULT_MAX_STEPS_V1,
    opponent_kind: str = "cabt_rule_agent_v0",
    opponent_kinds: str | Sequence[str] | None = None,
    pool_epoch: int = 0,
    policy_lag: int = 0,
    non_terminal_discount: float = 1.0,
    source_commit: str | None = None,
    seed_qualification_report_path: Path = DEFAULT_SEED_QUALIFICATION_REPORT_PATH_V1,
    materialized_deck_dir: Path = DEFAULT_MATERIALIZED_DECK_DIR_V1,
    # Test-only seams: never exposed as a CLI flag. Production always uses
    # the real fixed artifact base and the real spawn-based ActorPoolV1.
    output_base_dir: Path = DEFAULT_ACTOR_POOL_OUTPUT_BASE_V1,
    pool_factory: Callable[[], ActorPoolV1] | None = None,
) -> dict[str, object]:
    run_name = _validate_run_name_v1(run_name)
    output_root = Path(output_base_dir) / run_name

    qualified = load_qualified_lanes_v1(
        report_path=Path(seed_qualification_report_path), materialized_dir=Path(materialized_deck_dir),
    )
    lane_ids = resolve_requested_lanes_v1(lanes_arg, qualified)
    lanes = [qualified[archetype_id] for archetype_id in lane_ids]

    if behavior_kind == "rule_agent":
        behavior_identity = rule_agent_behavior_identity_v1()
    elif behavior_kind == "neural_specialist":
        behavior_identity = neural_checkpoint_behavior_identity_v1(neural_checkpoint_path)
    else:
        raise CollectTrajectoriesError(
            f"--behavior-kind must be 'rule_agent' or 'neural_specialist', got {behavior_kind!r}"
        )
    resolved_source_commit = source_commit or current_repo_commit_v1()

    rotation = (
        load_opponent_schedule_v1(opponent_schedule) if opponent_schedule
        else _resolve_opponent_rotation_v1(opponent_kind, opponent_kinds)
    )

    jobs = build_collection_plan_v1(
        lanes=lanes, num_games=num_games, base_seed=base_seed, source_commit=resolved_source_commit,
        behavior_kind=behavior_kind, behavior_identity=behavior_identity, opponent_kind=opponent_kind,
        opponent_kinds=rotation,
        decoding_mode=decoding_mode, sampling_seed=sampling_seed, pool_epoch=pool_epoch,
        policy_lag=policy_lag, non_terminal_discount=non_terminal_discount, max_steps=max_steps,
        timeout_seconds=timeout_seconds, neural_checkpoint_path=neural_checkpoint_path,
    )

    sys.stderr.write(f"[collect-trajectories] Initializing collection run '{run_name}' ({len(jobs)} jobs across lanes)...\n")
    sys.stderr.flush()

    pool = (
        pool_factory() if pool_factory is not None
        else ActorPoolV1(num_workers=workers, persistent_worker=persistent_worker)
    )
    started_wall = time.monotonic()
    started_at_utc = datetime.now(UTC).isoformat()
    try:
        outcomes = _run_jobs_with_progress_v1(pool, jobs, output_root=output_root, run_name=run_name)
    finally:
        pool.shutdown()
    finished_at_utc = datetime.now(UTC).isoformat()
    wall_time_seconds = time.monotonic() - started_wall

    summary_body = _summarize_v1(jobs=jobs, outcomes=outcomes)
    unplanned_existing = _survey_unplanned_existing_games_v1(
        output_root / "games", planned_job_ids=frozenset(job.job_id for job in jobs),
    )
    payload: dict[str, object] = {
        "schema_version": RUN_SUMMARY_SCHEMA_V1,
        "run_name": run_name,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "wall_time_seconds": round(wall_time_seconds, 3),
        "behavior_kind": behavior_kind,
        "behavior_identity": behavior_identity,
        "opponent_kind": opponent_kind,
        "decoding_mode": decoding_mode,
        "sampling_seed": sampling_seed,
        "source_commit": resolved_source_commit,
        "lanes": list(lane_ids),
        "num_games_requested": num_games,
        "games_attempted": len(jobs),
        "output_root": str(output_root),
        "games_dir": str(output_root / "games"),
        "run_summary_path": str(output_root / "run_summary.json"),
        "progress_summary_path": str(output_root / "progress_summary.json"),
        "existing_games_outside_this_plan": unplanned_existing,
        **summary_body,
    }
    _atomic_write_json_v1(output_root / "run_summary.json", payload)

    print(
        f"[collect-trajectories] run={run_name} lanes={','.join(lane_ids)} "
        f"attempted={payload['games_attempted']} completed={payload['games_completed']} "
        f"resumed_skipped={payload['games_resumed_skipped']} faulted={payload['games_faulted']} "
        f"timeout={payload['games_timeout']} transitions={payload['transitions_collected']} "
        f"wall_time={wall_time_seconds:.1f}s -> {output_root}",
        file=sys.stderr,
    )
    return payload


__all__ = [
    "DEFAULT_ACTOR_POOL_OUTPUT_BASE_V1",
    "DEFAULT_MATERIALIZED_DECK_DIR_V1",
    "DEFAULT_SEED_QUALIFICATION_REPORT_PATH_V1",
    "RUN_SUMMARY_SCHEMA_V1",
    "CollectTrajectoriesError",
    "QualifiedLaneV1",
    "build_collection_plan_v1",
    "_resolve_opponent_rotation_v1",
    "load_opponent_schedule_v1",
    "smooth_weighted_order_v1",
    "load_qualified_lanes_v1",
    "resolve_requested_lanes_v1",
    "run_collect_trajectories_v1",
]
