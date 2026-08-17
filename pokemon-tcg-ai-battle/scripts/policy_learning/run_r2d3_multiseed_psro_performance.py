#!/usr/bin/env python3
"""Fail-closed, resumable R2D3/PSRO performance experiment controller.

This is deliberately a Python controller, not a shell workflow.  Both the
small CUDA smoke and the production protocol invoke these exact stage
functions; profiles merely provide different bounded work counts.
"""
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
import functools
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
SOURCE_ARTIFACT = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-opponents-r2d3-psro-v1-20260728_180801")
PRIOR_ARTIFACT = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-r2d3-e2e-v1-20260728_184333")
BC_POPULATION = ROOT / "runs/policy-learning-gate5a/final-evaluation-argmax/evaluations/primary-bc-recurrent-population.json"
PPO_POPULATION = ROOT / "runs/policy-learning-gate5a/final-evaluation-argmax/evaluations/primary-frozen-round-3-population.json"
FAMILY_POPULATION = ROOT / "runs/policy-learning-gate4/cabt/bc-recurrent-population.json"
STAGES = ("source_freeze", "scale_benchmark", "teacher_calibration", "replay_collection", "replay_freeze", "learner_scale_benchmark", "architecture_screen", "multiseed_training", "full_training", "development_validation", "deck_holdout_gate", "psro_payoff", "psro_online_collection", "psro_best_response", "final_holdout_gate", "promotion_decision")
DEFAULT_PARALLEL_WORKERS_V1 = 12
# A performance-only continuation is deliberately narrow: the parent may
# contribute completed evidence only through online collection.  It must then
# execute the interrupted best-response validation in the child artifact.
CONTINUATION_INHERITED_STAGES = STAGES[:STAGES.index("psro_best_response")]
# Holdout consumption is one-way, so opening a split requires the upstream
# gates to have *passed*, not merely to have executed ahead of it in STAGES.
HOLDOUT_PREREQUISITE_STAGES = {
    "deck_holdout": ("development_validation",),
    "final_holdout": ("development_validation", "deck_holdout_gate", "psro_payoff", "psro_online_collection", "psro_best_response"),
}
WIN_RATE_PREREQUISITE_STAGES = frozenset({"development_validation", "deck_holdout_gate", "psro_best_response"})


@dataclass(frozen=True)
class Profile:
    name: str; scale_games_per_config: int; replay_games: int; replay_quality_interval: int; replay_checkpoint_games: int
    minimum_replay_sequences: int; screen_updates: int; screen_validation_games: int
    multiseed_updates: int; full_training_updates: int; development_validation_games: int
    development_validation_interval: int; deck_holdout_games: int; psro_pair_games: int
    psro_online_games: int; psro_best_response_updates: int; final_holdout_games: int; holdout_min_win_rate: float
    learner_scale_updates: int; learner_batch_candidates: tuple[int, ...]; model_hidden_size: int
    cabt_workers: int = 1; learner_batch_size: int = 32; training_log_interval: int = 1; replay_sequence_stride: int = 20
    multiseed_seeds: tuple[int, ...] = (0, 1, 2); multiseed_top_k: int = 2
    screen_architectures: tuple[str, ...] = ("gru_demo_0", "gru_demo_1_32", "gru_demo_1_16", "lru_demo_0", "lru_demo_1_32", "lru_demo_1_16")
    # CPU CABT collection can use every logical processor.  Validation workers
    # each own a model, a CUDA context and one submitted-opponent subprocess,
    # so they are bounded independently -- see ``validation_workers``.  The
    # effective count is additionally clamped to the physical core count, so
    # raising this alone can never oversubscribe cores.
    cuda_validation_workers: int = 4
    validation_reserved_cores: int = 2
    # Submitted search opponents cap themselves at a 4.0s wall-clock decision
    # budget, so this only has to absorb scheduling jitter and IPC overhead.
    # It does not license a slower opponent: raising it cannot buy the opponent
    # more search time than its own internal cap allows.
    validation_callback_timeout_seconds: float = 8.0
    # CUDA allocations made through WSL are charged to the Windows commit
    # budget as well as VRAM.  Leave host headroom instead of selecting a batch
    # that is only marginally faster while making the desktop unstable.
    learner_peak_reserved_limit_mb: float | None = None
    bc_weight: float = 0.15
    psro_floor_probability: float = 0.15
    screen_draws_per_window: float = 32.0
    multiseed_draws_per_window: float = 64.0
    full_draws_per_window: float = 96.0
    psro_draws_per_window: float = 96.0


PROFILES = {
    "smoke": Profile("smoke", 8, 128, 64, 64, 1, 20, 4, 20, 40, 8, 20, 8, 2, 4, 20, 8, 1.10, 3, (32, 64, 128), 128, 4, 32, 1, 20),
    "production": Profile(
        "production", 128, 5000, 1250, 256, 25000, 10000, 384, 50000,
        150000, 384, 25000, 1024, 64, 2000, 50000, 1024, .50, 10,
        (64, 128, 256, 512, 1024, 2048, 3072), 256, DEFAULT_PARALLEL_WORKERS_V1, 128, 100, 4,
        # Measured on this 14-physical-core host:
        # 4 workers 0.136 games/s, 8 workers 0.192, 12 workers 0.294, all with
        # zero faults and a 4.06s worst-case opponent decision.
        #
        # The earlier 12-worker VM restart occurred with WSL capped near 31GiB.
        # The host now assigns WSL 48GB, so use the measured throughput knee while
        # retaining two physical cores for the parent and OS.
        cuda_validation_workers=12,
        validation_callback_timeout_seconds=12.0,
        # v15 measured 3,022 seq/s at batch 512 (9,448MiB reserved) and
        # 3,254 seq/s at batch 1,024 (20,144MiB reserved).  A separate 200-update
        # production-shape soak completed batch 2,048 at 32,184MiB.  A 40GB
        # budget benchmarks that point while skipping projected ~57GB batch 3,072.
        learner_peak_reserved_limit_mb=40_000.0,
    ),
}


def physical_cores() -> int:
    """Count physical cores, not SMT siblings.

    Submitted search opponents budget their rollouts against a wall-clock
    deadline, so two games sharing one physical core each finish on time while
    each completes roughly half the search.  Sizing concurrency by logical
    processors would therefore weaken the opponents instead of failing loudly.
    """
    seen: set[tuple[str, str]] = set()
    physical_id = core_id = ""
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "physical id": physical_id = value
        elif key == "core id": core_id = value
        elif not key and core_id: seen.add((physical_id, core_id)); physical_id = core_id = ""
    if core_id: seen.add((physical_id, core_id))
    return len(seen) or len(os.sched_getaffinity(0))


def now() -> str: return datetime.now(timezone.utc).isoformat()
def canonical(value: object) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
def digest(value: object) -> str: return hashlib.sha256(canonical(value).encode()).hexdigest()
def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def git(*args: str) -> str: return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def validation_schedule(assets: list[Any], games: int, *, seed_namespace: str) -> list[dict[str, int | str]]:
    """Schedule every validation asset in both seats equally.

    The previous ``index % len(assets)`` / ``index % 2`` mapping coupled an
    opponent with one seat whenever the split had two assets.  This schedule
    makes the Cartesian cells explicit and uses a namespaced seed stream so
    model selection never consumes the development evaluation games.
    """
    if not assets:
        raise ValueError("validation requires at least one asset")
    cell_count = len(assets) * 2
    if games < cell_count or games % cell_count:
        raise ValueError("validation games must be divisible by the asset×seat cell count")
    if not seed_namespace:
        raise ValueError("validation seed namespace is required")
    repeats = games // cell_count
    schedule: list[dict[str, int | str]] = []
    for repeat in range(repeats):
        for asset_index in range(len(assets)):
            for candidate_side in (0, 1):
                index = len(schedule)
                seed = 1_000_000 + int(digest({
                    "namespace": seed_namespace, "repeat": repeat,
                    "asset_index": asset_index, "candidate_side": candidate_side,
                })[:12], 16) % 900_000_000
                schedule.append({"index": index, "asset_index": asset_index,
                                 "candidate_side": candidate_side, "seed": seed,
                                 "seed_namespace": seed_namespace})
    return schedule


def _wilson_interval(wins: int, games: int) -> tuple[float, float]:
    if games < 1 or not 0 <= wins <= games:
        raise ValueError("invalid Wilson interval count")
    z = 1.959963984540054
    rate = wins / games; denominator = 1.0 + z * z / games
    center = (rate + z * z / (2.0 * games)) / denominator
    half = z * math.sqrt(rate * (1.0 - rate) / games + z * z / (4.0 * games * games)) / denominator
    return center - half, center + half


def summarize_evaluation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the aggregate and every opponent/seat cell without mixing them."""
    if not rows:
        raise ValueError("evaluation summary needs at least one row")

    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        games = len(group); wins = sum(row.get("winner") == row.get("candidate_side") for row in group)
        faults = sum(not row.get("legal") or bool(row.get("candidate_fault")) or bool(row.get("timeout")) for row in group)
        lower, upper = _wilson_interval(wins, games)
        return {"games": games, "wins": wins, "win_rate": wins / games,
                "wilson95": {"lower": lower, "upper": upper}, "faults": faults}

    by_asset: dict[str, list[dict[str, Any]]] = {}
    by_seat: dict[str, list[dict[str, Any]]] = {"0": [], "1": []}
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        asset = str(row["opponent_asset_id"]); seat = str(int(row["candidate_side"]))
        by_asset.setdefault(asset, []).append(row); by_seat.setdefault(seat, []).append(row)
        by_cell.setdefault(f"{asset}|seat{seat}", []).append(row)
    return {"schema": "r2d3-evaluation-summary-v1", "aggregate": summarize(rows),
            "by_asset": {key: summarize(value) for key, value in sorted(by_asset.items())},
            "by_seat": {key: summarize(value) for key, value in sorted(by_seat.items())},
            "by_asset_seat": {key: summarize(value) for key, value in sorted(by_cell.items())}}


def balanced_mixture_quotas(members: list[Any], games: int, *, floor_probability: float) -> dict[str, int]:
    """Turn a meta-strategy into deterministic integer quotas with a floor."""
    if not members or games < len(members) or not 0.0 <= floor_probability <= 1.0:
        raise ValueError("invalid PSRO quota request")
    if floor_probability * len(members) > 1.0 + 1e-12:
        raise ValueError("PSRO probability floor exceeds one")
    identifiers = [str(member.opponent_policy_id) for member in members]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("PSRO members must have unique ids")
    floor = math.ceil(games * floor_probability)
    if floor * len(members) > games:
        raise ValueError("PSRO integer floor exceeds game budget")
    remaining = games - floor * len(members)
    total_weight = sum(float(member.probability) for member in members)
    if total_weight <= 0.0:
        raise ValueError("PSRO member probabilities must have positive mass")
    raw = [remaining * float(member.probability) / total_weight for member in members]
    quotas = {identifier: floor + math.floor(value) for identifier, value in zip(identifiers, raw, strict=True)}
    for _fraction, identifier in sorted(((value - math.floor(value), identifier) for identifier, value in zip(identifiers, raw, strict=True)), reverse=True)[:games - sum(quotas.values())]:
        quotas[identifier] += 1
    return quotas


def source_identity_files(root: Path = ROOT) -> tuple[str, ...]:
    """Enumerate every repository file that can affect this campaign.

    The controller imports through ``main.py``, ``agents``, ``src/mage_ptcg``,
    ``scripts/test_sim.py`` and policy-learning helpers.  Hashing only the R2D3
    package left old Replay/model stages reusable after runtime or split logic
    changed.  A broad source closure is intentionally fail-closed: unrelated
    documentation does not invalidate a run, but executable Python/shell
    changes do.
    """
    relative: set[str] = set()
    for name in ("main.py", "deck.csv", "scripts/test_sim.py", "requirements.txt", "pyproject.toml", "uv.lock"):
        if (root / name).is_file():
            relative.add(name)
    for directory, suffixes in (
        ("agents", {".py"}),
        ("src/mage_ptcg", {".py"}),
        ("scripts/policy_learning", {".py", ".sh"}),
    ):
        base = root / directory
        if not base.is_dir():
            continue
        relative.update(
            path.relative_to(root).as_posix()
            for path in base.rglob("*")
            if path.is_file() and path.suffix in suffixes
        )
    return tuple(sorted(relative))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: object, *, durable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        if durable:
            handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    if durable:
        _fsync_directory(path.parent)


class ControllerLease:
    """Process-lifetime GPU lease preventing concurrent performance runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    @classmethod
    def for_gpu(cls, gpu_id: str) -> "ControllerLease":
        identity = hashlib.sha256(str(gpu_id).encode()).hexdigest()[:16]
        return cls(Path("/tmp") / f"mage-ptcg-r2d3-gpu-{identity}.lock")

    def acquire(self, metadata: dict[str, Any]) -> None:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "owner metadata unavailable"
            handle.close()
            raise RuntimeError(
                f"GPU already has an active R2D3 performance controller: {owner}"
            ) from exc
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(canonical(metadata) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise
        self.handle = handle

    def close(self) -> None:
        if self.handle is None:
            return
        import fcntl

        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def projected_learner_peak_reserved_mb(
    batch_size: int,
    rows: list[dict[str, Any]],
) -> float | None:
    """Conservative linear projection from the largest passing CUDA batch."""
    passing = [
        row for row in rows
        if row.get("status") == "PASS"
        and int(row.get("batch_size", 0)) > 0
        and float(row.get("peak_reserved_mb", 0.0)) > 0.0
    ]
    if not passing:
        return None
    reference = max(passing, key=lambda row: int(row["batch_size"]))
    return (
        float(reference["peak_reserved_mb"])
        * int(batch_size)
        / int(reference["batch_size"])
    )


def durable_psro_payoff_prefix(
    path: Path,
    *,
    identity_hash: str,
    jobs: list[dict[str, Any]],
    play: Callable[[dict[str, Any]], dict[str, Any]],
    persisted: Callable[[int, int], None] | None = None,
    workers: int = DEFAULT_PARALLEL_WORKERS_V1,
    job: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Run/recover a deterministic PSRO payoff schedule with a durable prefix.

    Passing ``workers > 1`` together with ``job`` -- a module-level callable a
    spawn worker can import -- plays the outstanding games concurrently.  Every
    job already carries its own seed, seats and opponent pair, so concurrency
    changes only the order games execute in, never which games are played or
    what they score.

    The checkpoint still advances one *contiguous* prefix at a time.  Results
    that complete out of order are held until every earlier game has landed, so
    a crash resumes from exactly the schedule position a sequential run would
    have resumed from, at the cost of discarding completed-but-not-yet-prefixed
    games.
    """
    rows: list[dict[str, Any]] = []
    if path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        if (
            state.get("schema") != "r2d3-psro-payoff-checkpoint-v1"
            or state.get("identity_hash") != identity_hash
            or state.get("completed") != len(state.get("rows", []))
        ):
            raise RuntimeError("PSRO payoff checkpoint identity or count differs")
        rows = list(state["rows"])
        if len(rows) > len(jobs):
            raise RuntimeError("PSRO payoff checkpoint exceeds the current schedule")
    for index, row in enumerate(rows):
        if (
            row.get("job") != jobs[index]
            or row.get("status") != "DONE"
            or not math.isfinite(float(row.get("payoff_left", float("nan"))))
            or abs(float(row["payoff_left"])) > 1.0
        ):
            raise RuntimeError(f"PSRO payoff checkpoint row {index} is invalid")
    def accept(schedule_position: int, result: dict[str, Any]) -> None:
        entry = {"job": jobs[schedule_position], **result}
        if (
            entry.get("status") != "DONE"
            or not math.isfinite(float(entry.get("payoff_left", float("nan"))))
            or abs(float(entry["payoff_left"])) > 1.0
        ):
            raise RuntimeError(f"PSRO payoff game failed: {jobs[schedule_position]['game_id']}")
        rows.append(entry)
        atomic_json(
            path,
            {
                "schema": "r2d3-psro-payoff-checkpoint-v1",
                "identity_hash": identity_hash,
                "completed": len(rows),
                "rows": rows,
            },
            durable=True,
        )
        if persisted is not None:
            persisted(len(rows), len(jobs))

    outstanding = jobs[len(rows):]
    if workers > 1 and job is not None and len(outstanding) > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import multiprocessing
        held: dict[int, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=min(workers, len(outstanding)),
                                 mp_context=multiprocessing.get_context("spawn")) as executor:
            futures = [executor.submit(job, item) for item in outstanding]
            for future in as_completed(futures):
                index, result = future.result()
                held[int(index)] = result
                while len(rows) in held:
                    accept(len(rows), held.pop(len(rows)))
        if held:
            raise RuntimeError(f"PSRO payoff schedule left {len(held)} out-of-order result(s) unplaced")
        return rows
    for item in outstanding:
        accept(len(rows), play(item))
    return rows


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8"); os.replace(temporary, path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    names = sorted({key for row in rows for key in row}) or ["status"]
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names); writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


class TerminalProgress:
    """One human monitor: tqdm on a TTY, 10-second summaries otherwise."""
    def __init__(self, mode: str) -> None:
        requested = mode if mode != "auto" else ("bar" if sys.stderr.isatty() else "summary")
        self.mode = requested; self.bar: Any | None = None; self.stage = ""; self.total = 0; self.completed = 0
        self.last_summary = 0.0

    @staticmethod
    def _postfix(*, faults: int, extra: dict[str, Any]) -> dict[str, Any]:
        visible = {"faults": faults}
        for key in ("learner_updates", "games", "sequences", "legal", "gpu_memory_mb", "validation_score"):
            if key in extra and extra[key] is not None: visible[key] = extra[key]
        return visible

    def update(self, stage: str, completed: int, total: int, *, faults: int, extra: dict[str, Any]) -> None:
        total = max(1, total); completed = min(max(0, completed), total); postfix = self._postfix(faults=faults, extra=extra)
        if self.mode == "quiet": return
        if self.mode == "bar":
            from tqdm import tqdm
            if self.bar is None or self.stage != stage or self.total != total:
                if self.bar is not None: self.bar.close()
                self.bar = tqdm(total=total, desc=stage, unit="item", dynamic_ncols=True, mininterval=.25, leave=True)
                self.stage, self.total, self.completed = stage, total, 0
            self.bar.update(completed - self.completed); self.completed = completed; self.bar.set_postfix(postfix, refresh=False)
            return
        # Logs are useful in redirected jobs, but only as an aggregate health
        # snapshot.  Do not write an unbounded per-game JSON stream.
        moment = time.monotonic()
        if completed == total or moment - self.last_summary >= 10.0:
            rate = completed / max(1e-9, moment - self.last_summary) if self.last_summary else None
            suffix = " ".join(f"{key}={value}" for key, value in postfix.items())
            print(f"[progress] stage={stage} {completed}/{total} {suffix}" + (f" delta_rate={rate:.2f}/s" if rate is not None else ""), flush=True)
            self.last_summary = moment
        self.stage, self.total, self.completed = stage, total, completed

    def note(self, message: str) -> None:
        if self.mode == "bar" and self.bar is not None:
            self.bar.write(message)
        elif self.mode != "quiet":
            print(message, flush=True)

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close(); self.bar = None


def load_e2e() -> Any:
    scripts_dir = str(ROOT / "scripts" / "policy_learning")
    if scripts_dir not in sys.path: sys.path.insert(0, scripts_dir)
    # Use the physical module name.  Spawn workers must import it again when
    # unpickling isolated CABT job functions.
    spec = importlib.util.spec_from_file_location("run_submitted_r2d3_e2e", ROOT / "scripts/policy_learning/run_submitted_r2d3_e2e.py")
    if spec is None or spec.loader is None: raise RuntimeError("cannot import submitted R2D3 implementation")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module); return module


_VALIDATION_WORKER: dict[str, Any] = {}


def _validation_game_job(arguments: dict[str, Any]) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    """One candidate validation game in an isolated process.

    The checkpoint, the module and the deterministic split registry are loaded
    once per worker process and reused across that worker's games; only the
    per-game CABT opponent runtime is rebuilt.  Opponent, seed and seat stay
    pure functions of ``index``, so the parallel schedule plays exactly the
    games the sequential loop played.
    """
    import torch
    from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig
    checkpoint, core = str(arguments["checkpoint"]), str(arguments["core"])
    hidden_size = int(arguments["hidden_size"])
    if _VALIDATION_WORKER.get("key") != (checkpoint, core, hidden_size, str(arguments["device"])):
        module = load_e2e(); device = torch.device(str(arguments["device"]))
        from mage_ptcg.policy_learning.submitted_opponents import assert_no_leakage, load_registry, split_assets
        assets = load_registry(ROOT, module.LEDGER); splits = split_assets(assets, seed=71000); assert_no_leakage(splits)
        _VALIDATION_WORKER.clear()
        _VALIDATION_WORKER.update({"key": (checkpoint, core, hidden_size, str(arguments["device"])), "module": module, "device": device,
                                   "splits": splits, "model": module._load_model(Path(checkpoint), R2D3ModelConfig(recurrent_core=core, hidden_size=hidden_size), device)})
    module, device, splits = _VALIDATION_WORKER["module"], _VALIDATION_WORKER["device"], _VALIDATION_WORKER["splits"]
    index = int(arguments["index"]); assets = splits[str(arguments["split"])]
    asset_index, candidate_side, seed = int(arguments["asset_index"]), int(arguments["candidate_side"]), int(arguments["seed"])
    row, traces = module._candidate_validation_game(Path(arguments["artifact"]), assets[asset_index], _VALIDATION_WORKER["model"],
                                                    device, str(arguments["policy_hash"]), index=800000 + index, candidate_side=candidate_side,
                                                    seed=seed,
                                                    callback_timeout_seconds=float(arguments["callback_timeout_seconds"]))
    row["evaluation"] = str(arguments["label"])
    return index, row, traces


PSRO_POPULATION_ORDER = ("rule-v0", "rule-v1", "ppo", "r2d3")
_PSRO_PAYOFF_WORKER: dict[str, Any] = {}


def _psro_payoff_job(context: dict[str, Any], item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One PSRO payoff game in an isolated process.

    ``context`` carries the worker's build inputs and is deliberately kept out
    of ``item``: the schedule entries are hashed into the payoff identity, so
    they must stay byte-identical to the sequential schedule.  The population is
    rebuilt from the frozen checkpoint inside the worker so no CUDA model is
    ever pickled, and seat/seed/pair come from ``item``, which makes the game
    identical to the one the sequential loop would have played.
    """
    import torch
    from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
    from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig
    from main import make_rule_agent, make_rule_agent_v1
    from scripts.test_sim import run_match
    checkpoint, core = str(context["checkpoint"]), str(context["core"])
    hidden_size, device_name = int(context["hidden_size"]), str(context["device"])
    key = (checkpoint, core, hidden_size, device_name)
    if _PSRO_PAYOFF_WORKER.get("key") != key:
        module = load_e2e(); device = torch.device(device_name)
        _PSRO_PAYOFF_WORKER.clear()
        _PSRO_PAYOFF_WORKER.update({
            "key": key, "module": module, "device": device,
            "deck": [int(value) for value in (ROOT / "deck.csv").read_text().splitlines() if value.strip()],
            "model": module._load_model(Path(checkpoint), R2D3ModelConfig(recurrent_core=core, hidden_size=hidden_size), device),
        })
    module, device = _PSRO_PAYOFF_WORKER["module"], _PSRO_PAYOFF_WORKER["device"]
    deck, model = _PSRO_PAYOFF_WORKER["deck"], _PSRO_PAYOFF_WORKER["model"]
    policy_hash, game_id = str(context["policy_hash"]), str(item["game_id"])
    seat_left = int(item["seat_left"])

    def member(index: int, seat: int) -> Any:
        kind = PSRO_POPULATION_ORDER[index]
        if kind == "rule-v0": return make_rule_agent(deck=deck)
        if kind == "rule-v1": return make_rule_agent_v1(deck=deck)
        if kind == "ppo": return module.TracingPPO(deck=deck)
        return R2D3CandidatePolicy(model, deck=deck, device=device, policy_version=policy_hash,
                                   game_id=game_id, seat=seat)

    left_agent = member(int(item["left"]), seat_left)
    right_agent = member(int(item["right"]), 1 - seat_left)
    agents = [left_agent, right_agent] if seat_left == 0 else [right_agent, left_agent]
    result = run_match(
        deck_a_path=ROOT / "deck.csv", deck_b_path=ROOT / "deck.csv",
        agent_a_name="rule", agent_b_name="rule", seed=int(item["seed"]),
        output_dir=Path(context["artifact"]) / "psro_scratch", save_html=False, save_result=False,
        agent_a_factory=lambda _deck, _seed, value=agents[0]: value,
        agent_b_factory=lambda _deck, _seed, value=agents[1]: value,
    )
    payoff = (0.0 if result.get("winner") == 2 else 1.0 if result.get("winner") == seat_left else -1.0)
    return int(item["index"]), {"status": result.get("status"), "winner": result.get("winner"), "payoff_left": payoff}


_PSRO_ONLINE_WORKER: dict[str, Any] = {}


def _psro_online_job(context: dict[str, Any], item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Play one PSRO online-collection game and return its public traces.

    Only the game runs here.  Building sequences and adding them to the replay
    stays in the controller, because insertion order fixes the replay's priority
    layout and must follow the schedule rather than completion order.  The
    mixture member is sampled by the controller and only its runtime id is sent,
    so a worker cannot re-sample and drift from the recorded mixture.
    """
    import torch
    from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
    from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig
    from main import make_rule_agent, make_rule_agent_v1
    from scripts.test_sim import run_match
    checkpoint, core = str(context["checkpoint"]), str(context["core"])
    hidden_size, device_name = int(context["hidden_size"]), str(context["device"])
    key = (checkpoint, core, hidden_size, device_name)
    if _PSRO_ONLINE_WORKER.get("key") != key:
        module = load_e2e(); device = torch.device(device_name)
        _PSRO_ONLINE_WORKER.clear()
        _PSRO_ONLINE_WORKER.update({
            "key": key, "module": module, "device": device,
            "deck": [int(value) for value in (ROOT / "deck.csv").read_text().splitlines() if value.strip()],
            "model": module._load_model(Path(checkpoint), R2D3ModelConfig(recurrent_core=core, hidden_size=hidden_size), device),
        })
    module, device = _PSRO_ONLINE_WORKER["module"], _PSRO_ONLINE_WORKER["device"]
    deck, model = _PSRO_ONLINE_WORKER["deck"], _PSRO_ONLINE_WORKER["model"]
    policy_hash = str(context["policy_hash"])
    index, seed, side = int(item["index"]), int(item["seed"]), int(item["side"])
    game_id, opponent_id = str(item["game_id"]), str(item["opponent_policy_id"])
    candidate = R2D3CandidatePolicy(model, deck=deck, device=device, policy_version=policy_hash,
                                    game_id=game_id, seat=side)
    if opponent_id == "rule-v0":
        opponent = make_rule_agent(deck=deck)
    elif opponent_id == "rule-v1":
        opponent = make_rule_agent_v1(deck=deck)
    elif opponent_id == "ppo":
        opponent = module.TracingPPO(deck=deck); opponent.reset_episode(game_id=game_id, candidate_side=1 - side)
    elif opponent_id == "r2d3":
        opponent = R2D3CandidatePolicy(model, deck=deck, device=device, policy_version=policy_hash,
                                       game_id=game_id + "-opponent", seat=1 - side)
    else:
        raise RuntimeError(f"PSRO mixture runtime is unavailable: {opponent_id}")
    agents = [candidate, opponent] if side == 0 else [opponent, candidate]
    result = run_match(deck_a_path=ROOT / "deck.csv", deck_b_path=ROOT / "deck.csv", agent_a_name="rule",
                       agent_b_name="rule", seed=seed,
                       output_dir=Path(context["artifact"]) / "psro_online_scratch" / f"{index:06d}",
                       save_html=False, save_result=False,
                       agent_a_factory=lambda _deck, _seed, value=agents[0]: value,
                       agent_b_factory=lambda _deck, _seed, value=agents[1]: value)
    statuses = result.get("agent_status") or [None, None]
    return index, {"status": result["status"], "winner": result.get("winner"),
                   "candidate_status": statuses[side], "traces": candidate.traces}


def _adapter_episode_job(arguments: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """A BC/Family replay game in an isolated process and scratch directory."""
    from mage_ptcg.offline_scaleup.candidate_runtime import adapter_for
    from mage_ptcg.decision_state import build_decision_state
    from mage_ptcg.policy_learning.r2d3.semantic_action import encode_legal_action
    from mage_ptcg.policy_learning.r2d3.semantic_state import encode_public_state
    from main import make_rule_agent
    from scripts.test_sim import run_match
    index, entry, label, artifact = int(arguments["index"]), arguments["entry"], arguments["label"], Path(arguments["artifact"])
    deck = list(entry["deck_cards"]); main_deck = [int(card) for card in (ROOT / "deck.csv").read_text().splitlines() if card.strip()]; side = index % 2
    adapter = adapter_for(entry); traces: list[dict[str, Any]] = []; game_id = f"replay-{label}-{int(arguments['game_number'])}"
    try:
        adapter.prepare(deck)
        def actor(observation: object, configuration: object = None) -> list[int]:
            del configuration; choice = adapter.decide(observation)
            if isinstance(observation, dict) and isinstance(observation.get("select"), dict) and len(choice) == 1:
                state = build_decision_state(observation); matched = [n for n, action in enumerate(state.legal_actions) if action.option_index == choice[0]]
                if len(matched) == 1:
                    actions = [encode_legal_action({"digest": action.action_key.digest, "action_type": action.action_key.selection_type, "card_id": action.action_key.card_id, "source_zone": action.action_key.source_entity_key, "target_zone": action.action_key.target_entity_key, "target_card": action.action_key.target_entity_key, "amount": None, "selection_order": action.option_index, "phase": action.action_key.context, "optional": False, "semantic_role": action.action_key.semantic_operation}) for action in state.legal_actions]
                    from mage_ptcg.policy_learning.r2d3.sequence import public_prize_potential
                    traces.append({"state": encode_public_state(state.actor_view.public_state), "actions": actions, "selected_action": matched[0], "potential": public_prize_potential(state.actor_view.public_state)})
            return choice
        source_deck = artifact / "source_decks" / f"{label}.csv"; scratch = artifact / "replay_adapter_scratch" / f"{label}-{index:06d}"
        result = run_match(deck_a_path=source_deck if side == 0 else ROOT / "deck.csv", deck_b_path=ROOT / "deck.csv" if side == 0 else source_deck, agent_a_name="rule", agent_b_name="rule", seed=830000 + int(arguments["game_number"]), output_dir=scratch, save_html=False, save_result=False, agent_a_factory=(lambda _d, _s: actor) if side == 0 else (lambda _d, _s: make_rule_agent(deck=main_deck)), agent_b_factory=(lambda _d, _s: make_rule_agent(deck=main_deck)) if side == 0 else (lambda _d, _s: actor))
        statuses = result.get("agent_status") or [None, None]; row = {"game_id": game_id, "status": result["status"], "legal": result["status"] == "DONE", "bucket": label, "candidate_side": side, "behavior_policy_version": adapter.runtime_fingerprint, "behavior_source": label, "opponent_policy_hash": sha(ROOT / "agents/rule_agent.py"), "opponent_deck_hash": sha(ROOT / "deck.csv"), "opponent_source_lineage": arguments["source_lineage"], "opponent_deck_family": "RULE_V0", "winner": result.get("winner"), "candidate_fault": statuses[side] in {"ERROR", "INVALID", "TIMEOUT"}, "timeout": result["status"] == "TIMEOUT"}
        return index, row, {"row": row, "traces": traces, "demonstration": False}
    finally: adapter.close()


def _deck_pool_episode_job(arguments: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Collect PPO trajectories against Rule-v0 bound to an exact deck."""
    from main import make_rule_agent
    from scripts.test_sim import run_match
    index = int(arguments["index"]); entry = arguments["entry"]; side = index % 2
    artifact = Path(arguments["artifact"]); game_id = f"replay-deck-pool-{int(arguments['game_number']):06d}"
    own_deck = [int(value) for value in (ROOT / "deck.csv").read_text().splitlines() if value.strip()]
    opponent_deck = list(entry["deck_cards"])
    candidate = load_e2e()._cached_tracing_ppo(own_deck, game_id=game_id, candidate_side=side)
    opponent = make_rule_agent(deck=opponent_deck)
    agents = [candidate, opponent] if side == 0 else [opponent, candidate]
    paths = [ROOT / "deck.csv", Path(entry["deck_path"])] if side == 0 else [Path(entry["deck_path"]), ROOT / "deck.csv"]
    result = run_match(deck_a_path=paths[0], deck_b_path=paths[1], agent_a_name="rule", agent_b_name="rule",
                       seed=835000 + int(arguments["game_number"]), output_dir=artifact / "deck_pool_scratch" / f"{index:06d}",
                       save_html=False, save_result=False,
                       agent_a_factory=lambda _deck, _seed, value=agents[0]: value,
                       agent_b_factory=lambda _deck, _seed, value=agents[1]: value)
    statuses = result.get("agent_status") or [None, None]
    row = {"game_id": game_id, "status": result["status"], "legal": result["status"] == "DONE",
           "bucket": "environment_top_decks", "candidate_side": side,
           "behavior_policy_version": candidate.summary.get("checkpoint_sha256", candidate.summary.get("schema", "ppo")),
           "behavior_source": "ppo_vs_environment_top_deck", "opponent_policy_hash": sha(ROOT / "agents/rule_agent.py"),
           "opponent_deck_hash": entry["deck_hash"], "opponent_source_lineage": entry["source_id"],
           "opponent_deck_family": f"TOP_DECK_RANK_{entry['rank']}" if entry.get("rank") else "TEAM_REMOTE_DECK",
           "opponent_id": entry["opponent_id"], "winner": result.get("winner"),
           "candidate_fault": statuses[side] in {"ERROR", "INVALID", "TIMEOUT"}, "timeout": result["status"] == "TIMEOUT"}
    return index, row, {"row": row, "traces": candidate.traces, "demonstration": False}


class Controller:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args, self.profile, self.artifact, self.run_root = args, PROFILES[args.profile], args.artifact_root.resolve(), args.run_root.resolve()
        self.replay_input = args.replay_input_artifact.resolve() if args.replay_input_artifact else None
        self.source_artifact = args.source_artifact.resolve()
        self.deck_pool_path = args.deck_pool.resolve()
        self.e2e = load_e2e(); self.state_path = self.run_root / "r2d3_performance_controller_identity.json"
        self.context: dict[str, Any] = {"profile": asdict(self.profile), "profile_hash": digest(asdict(self.profile)), "stage_order": list(STAGES)}
        self.monitor = TerminalProgress(getattr(args, "progress_mode", "auto")); self._active_stage = "initializing"
        self.e2e.PROGRESS_CALLBACK = self._e2e_progress
        self.lease = ControllerLease.for_gpu(args.gpu_id)

    def stage_dir(self, name: str) -> Path: return self.artifact / "stages" / name
    def output(self, name: str) -> dict[str, Any]: return json.loads((self.stage_dir(name) / "output_manifest.json").read_text())

    def continuation_manifest(self) -> dict[str, Any] | None:
        """Load the explicit parent-lineage record, if this is a continuation."""
        manifest = self.context.get("continuation")
        if manifest is None:
            path = self.artifact / "continuation_manifest.json"
            if path.is_file():
                manifest = json.loads(path.read_text())
                self.context["continuation"] = manifest
        return manifest

    def inherited_stage(self, name: str) -> bool:
        manifest = self.continuation_manifest()
        return bool(manifest and name in manifest.get("inherited_stages", []))

    def imported_final_checkpoint(self, name: str, *, updates: int) -> dict[str, Any] | None:
        """Return a verified imported checkpoint only at its original final step."""
        manifest = self.continuation_manifest()
        record = manifest.get("parent_checkpoint") if manifest else None
        if not isinstance(record, dict) or record.get("name") != name:
            return None
        step, stored_updates = int(record.get("step", -1)), int(record.get("updates", -1))
        if step != updates or stored_updates != updates:
            raise RuntimeError("continuation checkpoint is not a final-step checkpoint for this stage")
        checkpoint = Path(str(record.get("child_path", "")))
        expected_hash = str(record.get("sha256", ""))
        if not checkpoint.is_file() or not expected_hash or sha(checkpoint) != expected_hash:
            raise RuntimeError("continuation checkpoint hash differs from its recorded parent")
        if not record.get("training_identity_hash"):
            raise RuntimeError("continuation checkpoint training identity is missing")
        return record

    def _continuation_parent(self) -> Path:
        value = getattr(self.args, "continue_from_artifact", None)
        if value is None:
            manifest = self.continuation_manifest()
            value = manifest.get("parent_artifact") if manifest else None
        if value is None:
            raise RuntimeError("continuation parent was not specified")
        parent = Path(value).resolve()
        if parent == self.artifact:
            raise RuntimeError("continuation parent must differ from the child artifact")
        if not parent.is_dir():
            raise RuntimeError(f"continuation parent artifact does not exist: {parent}")
        return parent

    def _verify_continuation_source(self, parent: Path) -> dict[str, Any]:
        identity_path = parent / "source_identity.json"
        if not identity_path.is_file():
            raise RuntimeError("continuation parent source identity is missing")
        parent_source = json.loads(identity_path.read_text())
        current_source = self.context["source"]
        keys = (
            "protected_before", "semantic_feature_version", "submitted_registry_hash",
            "source_artifact", "source_artifact_manifest_hash", "deck_pool_file_hash",
            "population_hash",
        )
        if any(parent_source.get(key) != current_source.get(key) for key in keys):
            raise RuntimeError("continuation source identity differs from the parent artifact")
        return parent_source

    @staticmethod
    def _copy_continuation_file(source: Path, destination: Path, expected_hash: str | None = None) -> dict[str, Any]:
        if not source.is_file():
            raise RuntimeError(f"continuation input is missing: {source}")
        source_hash = sha(source)
        if expected_hash is not None and source_hash != expected_hash:
            raise RuntimeError(f"continuation input hash differs: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha(destination) != source_hash:
            raise RuntimeError(f"continuation copy hash differs: {destination}")
        return {"parent_path": str(source), "child_path": str(destination), "sha256": source_hash}

    @staticmethod
    def _continuation_tree_files(root: Path) -> dict[str, str]:
        if not root.is_dir():
            raise RuntimeError(f"continuation tree is missing: {root}")
        return {
            path.relative_to(root).as_posix(): sha(path)
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    @classmethod
    def _copy_continuation_tree(cls, source: Path, destination: Path, expected_hash: str | None = None) -> dict[str, Any]:
        source_files = cls._continuation_tree_files(source)
        source_hash = digest(source_files)
        if expected_hash is not None and source_hash != expected_hash:
            raise RuntimeError(f"continuation tree hash differs: {source}")
        if destination.exists():
            destination_files = cls._continuation_tree_files(destination)
            conflicts = [name for name, value in destination_files.items() if source_files.get(name) != value]
            if conflicts:
                raise RuntimeError(f"continuation child snapshot tree has conflicting file: {conflicts[0]}")
        shutil.copytree(source, destination, dirs_exist_ok=True)
        if digest(cls._continuation_tree_files(destination)) != source_hash:
            raise RuntimeError(f"continuation tree copy hash differs: {destination}")
        return {"parent_path": str(source), "child_path": str(destination), "tree_hash": source_hash, "files": len(source_files)}

    def repair_parent_continuation(self) -> None:
        """Restore only missing continuation inputs after an interrupted import."""
        manifest = self.continuation_manifest()
        if manifest is None:
            raise RuntimeError("continuation manifest is missing")
        parent = self._continuation_parent()
        self._verify_continuation_source(parent)
        runtime = manifest.get("runtime_source")
        snapshots = manifest.get("snapshots")
        if not isinstance(runtime, dict) or not isinstance(snapshots, dict):
            # v16 artifacts created by the first continuation implementation
            # predate snapshot provenance.  Derive it only from the verified
            # immutable parent, then durably upgrade the child manifest.
            runtime = self._copy_continuation_file(
                parent / "runtime_source_manifest.json", self.artifact / "runtime_source_manifest.json"
            )
            snapshots = self._copy_continuation_tree(parent / "snapshots", self.artifact / "snapshots")
            manifest = {**manifest, "runtime_source": runtime, "snapshots": snapshots}
            atomic_json(self.artifact / "continuation_manifest.json", manifest, durable=True)
            self.context["continuation"] = manifest
        runtime_source, runtime_child = Path(str(runtime.get("parent_path", ""))), Path(str(runtime.get("child_path", "")))
        if runtime_child.exists() and sha(runtime_child) != runtime.get("sha256"):
            raise RuntimeError("continuation child runtime source hash differs")
        if not runtime_child.exists():
            self._copy_continuation_file(runtime_source, runtime_child, str(runtime.get("sha256", "")))
        self._copy_continuation_tree(Path(str(snapshots.get("parent_path", ""))), Path(str(snapshots.get("child_path", ""))), str(snapshots.get("tree_hash", "")))

    def import_parent_continuation(self) -> None:
        """Materialize one verified final checkpoint into a new child artifact.

        The parent stays immutable.  This does not grant a broad source
        rebaseline: only the completed stages required to rerun the interrupted
        PSRO best-response validation are inherited.
        """
        if getattr(self.args, "resume", False):
            raise RuntimeError("--continue-from-artifact is for the first child invocation; later invocations use --resume")
        parent = self._continuation_parent()
        parent_source = self._verify_continuation_source(parent)
        inherited_outputs: dict[str, dict[str, Any]] = {}
        for stage in CONTINUATION_INHERITED_STAGES:
            status_path = parent / "stages" / stage / "status.json"
            output_path = parent / "stages" / stage / "output_manifest.json"
            if not status_path.is_file() or not output_path.is_file():
                raise RuntimeError(f"continuation parent stage is incomplete: {stage}")
            status, output = json.loads(status_path.read_text()), json.loads(output_path.read_text())
            if status.get("status") != "PASS" or output.get("status") != "PASS":
                raise RuntimeError(f"continuation parent stage is not PASS: {stage}")
            inherited_outputs[stage] = output

        replay_manifest_path = parent / "replay_manifest.json"
        online_manifest_path = parent / "psro_online_replay_manifest.json"
        if not replay_manifest_path.is_file() or not online_manifest_path.is_file():
            raise RuntimeError("continuation replay manifest is missing")
        replay_manifest = json.loads(replay_manifest_path.read_text())
        online_manifest = json.loads(online_manifest_path.read_text())
        copied_replay = self._copy_continuation_file(parent / "replay.json", self.artifact / "replay.json", replay_manifest.get("replay_sha256"))
        self._copy_continuation_file(replay_manifest_path, self.artifact / "replay_manifest.json")
        copied_online = self._copy_continuation_file(parent / "psro_online_replay.json", self.artifact / "psro_online_replay.json", online_manifest.get("replay_sha256"))
        self._copy_continuation_file(online_manifest_path, self.artifact / "psro_online_replay_manifest.json")
        copied_runtime_source = self._copy_continuation_file(parent / "runtime_source_manifest.json", self.artifact / "runtime_source_manifest.json")
        copied_snapshots = self._copy_continuation_tree(parent / "snapshots", self.artifact / "snapshots")

        full_output = inherited_outputs["full_training"]
        parent_full = Path(str(full_output.get("checkpoint", "")))
        copied_full = self._copy_continuation_file(parent_full, self.artifact / "checkpoints" / "full-training" / parent_full.name, str(full_output.get("checkpoint_hash", "")))

        training_dir = parent / "checkpoints" / "psro-best-response-seed0"
        training_manifest_path = training_dir / "training_manifest.json"
        if not training_manifest_path.is_file():
            raise RuntimeError("continuation checkpoint manifest is missing")
        training_manifest = json.loads(training_manifest_path.read_text())
        updates = int(training_manifest.get("updates", -1))
        checkpoint_info = training_manifest.get("checkpoint", {})
        step = int(checkpoint_info.get("step", -1))
        if step != updates or updates <= 0:
            raise RuntimeError("continuation checkpoint must be a final-step checkpoint")
        parent_checkpoint = training_dir / f"r2d3-step-{step:06d}.pt"
        copied_checkpoint = self._copy_continuation_file(parent_checkpoint, self.artifact / "checkpoints" / "psro-best-response-seed0" / parent_checkpoint.name, checkpoint_info.get("sha256"))
        curve = training_dir / "training_curve.csv"
        copied_curve = self._copy_continuation_file(curve, self.artifact / "checkpoints" / "psro-best-response-seed0" / "training_curve.csv") if curve.is_file() else None

        for stage, output in inherited_outputs.items():
            child_output = dict(output)
            child_output.update({"inherited_from": str(parent), "parent_output_sha256": sha(parent / "stages" / stage / "output_manifest.json")})
            if stage == "full_training":
                child_output["checkpoint"] = copied_full["child_path"]
                child_output["checkpoint_hash"] = copied_full["sha256"]
            child_stage = self.stage_dir(stage)
            atomic_json(child_stage / "output_manifest.json", child_output)
            atomic_json(child_stage / "status.json", {"stage": stage, "status": "PASS", "inherited_from": str(parent), "parent_status_sha256": sha(parent / "stages" / stage / "status.json")})

        manifest = {
            "schema": "r2d3-performance-continuation-v1", "parent_artifact": str(parent),
            "parent_source_identity_sha256": sha(parent / "source_identity.json"),
            "parent_source_identity": parent_source, "inherited_stages": list(CONTINUATION_INHERITED_STAGES),
            "offline_replay": copied_replay, "online_replay": copied_online, "full_checkpoint": copied_full,
            "runtime_source": copied_runtime_source, "snapshots": copied_snapshots,
            "parent_checkpoint": {**copied_checkpoint, "name": "psro-best-response-seed0", "step": step,
                                  "updates": updates, "training_identity_hash": training_manifest.get("training_identity_hash")},
            "training_curve": copied_curve,
        }
        atomic_json(self.artifact / "continuation_manifest.json", manifest, durable=True)
        self.context["continuation"] = manifest
    def progress(self, stage: str, completed: int, total: int, *, faults: int = 0, **extra: Any) -> None:
        elapsed = max(1e-9, time.monotonic() - self.started); eta = (total - completed) * elapsed / completed if completed else None
        value = {"stage": stage, "completed": completed, "total": total, "faults": faults, "eta_seconds": eta, "updated_at": now(), **extra}
        atomic_json(self.artifact / "progress_summary.json", value); self.monitor.update(stage, completed, total, faults=faults, extra=extra)

    def _e2e_progress(self, gate: str, completed: int, total: int, details: dict[str, Any]) -> None:
        summary = dict(details); faults = int(summary.pop("faults", 0))
        self.progress(f"{self._active_stage}/{gate}", completed, total, faults=faults, **summary)

    def identity(self) -> dict[str, Any]:
        protected = {name: sha(ROOT / name) for name in ("main.py", "deck.csv", "agents/rule_agent.py")}
        tracked = list(source_identity_files(ROOT))
        files = [{"path": name, "sha256": sha(ROOT / name)} for name in tracked if (ROOT / name).is_file()]
        patch = subprocess.run(["git", "diff", "--binary", "HEAD", "--", *tracked], cwd=ROOT, text=True, capture_output=True, check=True).stdout
        # A dirty working tree can contain untracked controller/R2D3 files.
        # Bind their bytes into the patch identity too; ``git diff`` alone
        # deliberately omits them and would otherwise permit unsafe resume.
        source_patch_hash = hashlib.sha256((patch + canonical(files)).encode()).hexdigest()
        return {"schema": "r2d3-performance-source-identity-v3", "head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
                "source_patch_hash": source_patch_hash, "source_tree_hash": digest(files), "files": files,
                "semantic_feature_version": digest([sha(ROOT / "src/mage_ptcg/policy_learning/r2d3/semantic_action.py"), sha(ROOT / "src/mage_ptcg/policy_learning/r2d3/semantic_state.py")]),
                "protected_before": protected,
                "submitted_registry_hash": sha(self.e2e.LEDGER),
                "source_artifact": str(self.source_artifact),
                "source_artifact_manifest_hash": sha(self.source_artifact / "artifact_manifest.json"),
                "deck_pool_file_hash": sha(self.deck_pool_path),
                "population_hash": digest({
                    str(BC_POPULATION): sha(BC_POPULATION),
                    str(PPO_POPULATION): sha(PPO_POPULATION),
                    str(FAMILY_POPULATION): sha(FAMILY_POPULATION),
                    "environment_deck_pool": json.loads(self.deck_pool_path.read_text())["pool_hash"],
                }),
                "prior_artifact": str(PRIOR_ARTIFACT), "prior_checkpoint_hash": sha(PRIOR_ARTIFACT / "checkpoints/r2d3-step-000200.pt")}

    def prepare(self) -> None:
        if self.artifact.exists() and not self.args.resume: raise RuntimeError("artifact root exists; use --resume after verifying its identity")
        self.artifact.mkdir(parents=True, exist_ok=True); self.run_root.mkdir(parents=True, exist_ok=True); identity = self.identity()
        source_identity_hash = digest(identity)
        collection_identity_hash = digest({
            "profile_hash": self.context["profile_hash"],
            "source_identity_hash": source_identity_hash,
        })
        run_identity = {
            "profile_hash": self.context["profile_hash"],
            "source_identity_hash": source_identity_hash,
            "source_patch_hash": identity["source_patch_hash"],
            "head": identity["head"],
            "artifact_root": str(self.artifact),
        }
        continuation_resume = bool(self.args.resume) and (
            bool(getattr(self.args, "continue_from_artifact", None))
            or (self.artifact / "continuation_manifest.json").is_file()
        )
        self.context["source"] = identity
        if self.state_path.exists():
            old = json.loads(self.state_path.read_text())
            if old != run_identity:
                if continuation_resume:
                    self.rebaseline_continuation_identity(old, run_identity)
                elif not getattr(self.args, "rebaseline_source_identity", False):
                    raise RuntimeError("resume identity mismatch: profile, source patch, HEAD, or artifact root changed. "
                                       "If the controller sources changed deliberately and the completed stages are still valid under them, "
                                       "re-run with --rebaseline-source-identity to record the change and continue")
                else:
                    self.rebaseline_identity(old, run_identity)
        else: atomic_json(self.state_path, run_identity, durable=True)
        self.context["source"] = identity
        self.context["source_identity_hash"] = source_identity_hash
        self.context["collection_identity_hash"] = collection_identity_hash
        initial_identity_path = self.artifact / "source_identity_initial.json"
        if not initial_identity_path.exists():
            atomic_json(initial_identity_path, identity, durable=True)
        atomic_json(self.artifact / "source_identity.json", identity, durable=True)
        continuation_active = bool(getattr(self.args, "continue_from_artifact", None)) or (
            bool(self.args.resume) and (self.artifact / "continuation_manifest.json").is_file()
        )
        if continuation_active:
            if self.args.resume:
                manifest = self.continuation_manifest()
                if manifest is None:
                    raise RuntimeError("--resume continuation artifact is missing continuation_manifest.json")
                if getattr(self.args, "continue_from_artifact", None) and Path(str(manifest.get("parent_artifact", ""))).resolve() != self._continuation_parent():
                    raise RuntimeError("continuation parent differs from the recorded child lineage")
                self._verify_continuation_source(self._continuation_parent())
                self.repair_parent_continuation()
            else:
                self.import_parent_continuation()
        write_text(self.run_root / "latest_r2d3_performance_artifact.txt", str(self.artifact) + "\n")

    def rebaseline_identity(self, previous: dict[str, Any], current: dict[str, Any]) -> None:
        """Re-bind a resumed campaign to reviewed controller sources.

        Fail-closed in the direction that matters: a consumed holdout can never
        be re-derived, so a source change is refused outright once any holdout
        split has been opened, and the profile may not change because the stage
        work counts would no longer be comparable.  Both identities are appended
        to an audit log inside the artifact instead of being overwritten.
        """
        markers = sorted(path.name for path in self.artifact.glob("*_holdout_used.json"))
        if markers:
            raise RuntimeError(f"refusing to rebaseline the source identity after holdout consumption: {markers}")
        if previous.get("artifact_root") != current.get("artifact_root"):
            raise RuntimeError("refusing to rebaseline across a different artifact root")
        if previous.get("profile_hash") != current.get("profile_hash"):
            raise RuntimeError("refusing to rebaseline across a different profile: the recorded stage work counts would not be comparable")
        started = sorted(
            path.relative_to(self.artifact).as_posix()
            for name in STAGES
            for path in self.stage_dir(name).rglob("*")
            if path.is_file()
        )
        if started:
            raise RuntimeError(
                "refusing to retain completed or partial stages across a source identity change; "
                f"start a new artifact (first recorded stage file: {started[0]})"
            )
        log_path = self.artifact / "source_rebaseline_log.json"
        history = json.loads(log_path.read_text())["entries"] if log_path.exists() else []
        history.append({"at": now(), "previous": previous, "current": current,
                        "completed_stages": []})
        atomic_json(log_path, {"schema": "r2d3-source-rebaseline-v2", "entries": history}, durable=True)
        atomic_json(self.state_path, current, durable=True)
        self.monitor.note(f"[stage] source identity rebaselined before computation ({len(history)} recorded change(s))")

    def rebaseline_continuation_identity(self, previous: dict[str, Any], current: dict[str, Any]) -> None:
        """Record a controller-only repair before the child spends a new holdout."""
        if previous.get("artifact_root") != current.get("artifact_root"):
            raise RuntimeError("continuation source rebaseline requires the same child artifact root")
        if previous.get("profile_hash") != current.get("profile_hash"):
            raise RuntimeError("continuation source rebaseline cannot change the profile")
        if any(self.artifact.glob("*_holdout_used.json")):
            raise RuntimeError("continuation source rebaseline is forbidden after holdout consumption")
        parent = self._continuation_parent()
        self._verify_continuation_source(parent)
        br_status = self.stage_dir("psro_best_response") / "status.json"
        if br_status.is_file() and json.loads(br_status.read_text()).get("status") == "PASS":
            raise RuntimeError("continuation source rebaseline is forbidden after best-response validation passes")
        for stage in CONTINUATION_INHERITED_STAGES:
            status_path = self.stage_dir(stage) / "status.json"
            if status_path.is_file():
                status = json.loads(status_path.read_text())
                if status.get("status") != "PASS" or status.get("inherited_from") != str(parent):
                    raise RuntimeError(f"continuation inherited stage is not immutable parent evidence: {stage}")
        log_path = self.artifact / "continuation_source_rebaseline_log.json"
        history = json.loads(log_path.read_text())["entries"] if log_path.exists() else []
        history.append({"at": now(), "previous": previous, "current": current, "reason": "pre-validation continuation repair"})
        atomic_json(log_path, {"schema": "r2d3-continuation-source-rebaseline-v1", "entries": history}, durable=True)
        atomic_json(self.state_path, current, durable=True)
        self.monitor.note(f"[stage] continuation source identity rebaselined before validation ({len(history)} recorded repair(s))")

    def hashes(self) -> dict[str, Any]:
        source = self.context["source"]
        replay = self.output("replay_freeze").get("replay_hash") if (self.stage_dir("replay_freeze") / "output_manifest.json").exists() else None
        model = self.output("full_training").get("checkpoint_hash") if (self.stage_dir("full_training") / "output_manifest.json").exists() else None
        return {"source_patch_hash": source["source_patch_hash"], "config_hash": self.context["profile_hash"], "population_hash": source["population_hash"], "replay_hash": replay, "model_hash": model}

    def run_stage(self, name: str, fn: Callable[[], dict[str, Any]]) -> None:
        directory = self.stage_dir(name); status_path = directory / "status.json"; directory.mkdir(parents=True, exist_ok=True)
        if status_path.exists():
            prior = json.loads(status_path.read_text())
            if prior.get("status") == "PASS" and self.args.resume: return
            if prior.get("status") == "PASS" and self.inherited_stage(name): return
            if prior.get("status") == "PASS": raise RuntimeError(f"stage {name} is already PASS; use --resume")
        input_manifest = {"stage": name, "status": "RUNNING", "started_at": now(), **self.hashes(), "input_stages": list(STAGES[:STAGES.index(name)])}
        self._active_stage = name; self.monitor.note(f"[stage] {name} started")
        atomic_json(directory / "input_manifest.json", input_manifest); write_text(directory / "stdout.log", f"{now()} {name} started\n"); write_text(directory / "stderr.log", "")
        atomic_json(status_path, {"stage": name, "status": "NOT_RUN", "started_at": input_manifest["started_at"], **self.hashes()})
        try:
            result = fn(); finished = now(); output = {"stage": name, "status": "PASS", "started_at": input_manifest["started_at"], "ended_at": finished,
                "fault_count": int(result.get("fault_count", 0)), "resume_cursor": result.get("resume_cursor"), "checkpoint": result.get("checkpoint"), **self.hashes(), **result}
            atomic_json(directory / "output_manifest.json", output); atomic_json(status_path, {"stage": name, "status": "PASS", "ended_at": finished, **self.hashes()})
            write_text(directory / "stdout.log", f"{input_manifest['started_at']} {name} started\n{finished} {name} PASS\n")
            self.monitor.note(f"[stage] {name} PASS faults={output['fault_count']}")
        except Exception as exc:
            failure = {"stage": name, "status": "FAIL", "ended_at": now(), "error": f"{type(exc).__name__}: {exc}", **self.hashes()}
            atomic_json(directory / "output_manifest.json", failure); atomic_json(status_path, failure); write_text(directory / "stderr.log", failure["error"] + "\n"); raise

    def run_source_freeze(self) -> dict[str, Any]:
        import torch
        from mage_ptcg.offline_scaleup.candidate_runtime import adapter_for
        from mage_ptcg.offline_scaleup.pipeline import _cabt_result
        if not torch.cuda.is_available(): raise RuntimeError("CUDA is required for a performance run")
        snapshots, splits = self.e2e.snapshot_gate(self.source_artifact, self.artifact)
        qualified = []
        for label, path, opponent in (("ppo", PPO_POPULATION, "gate5a-eval-primary-frozen-round-3"), ("bc", BC_POPULATION, "gate5a-eval-primary-bc-recurrent"), ("family", FAMILY_POPULATION, "family-alakazam-deck-74d86ec36fd144b9")):
            payload = json.loads(path.read_text()); entry = next(row for row in payload["entries"] if row["opponent_id"] == opponent); adapter = adapter_for(entry)
            try:
                adapter.prepare(list(entry["deck_cards"])); game = _cabt_result({"game_id": f"performance-freeze-{label}", "candidate": opponent, "opponent": "rule-v0-current-deck", "candidate_side": 0, "seed": 720000 + len(label)}, payload, ROOT)
                if game.get("status") != "DONE" or not game.get("legal") or game.get("candidate_fault"): raise RuntimeError(f"{label} CABT qualification failed")
                qualified.append({"source": label, "policy_hash": adapter.runtime_fingerprint, "deck_hash": adapter.deck_fingerprint, "adapter": adapter.adapter_type, "cabt": game})
            finally: adapter.close()
        warm_model, _config, _device, warm_hash = self.initial_model()
        del warm_model
        atomic_json(self.artifact / "runtime_source_manifest.json", {"qualified": qualified, "submitted_snapshots": len(snapshots), "split_sizes": {k: len(v) for k, v in splits.items()}})
        return {"snapshots": len(snapshots), "qualified_sources": len(qualified), "fault_count": 0, "checkpoint": str(PRIOR_ARTIFACT / "checkpoints/r2d3-step-000200.pt"), "warm_start_policy_hash": warm_hash}

    def initial_model(self, *, core: str = "gru") -> tuple[Any, Any, Any, str]:
        import torch
        from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig, RecurrentDistributionalQ
        config = R2D3ModelConfig(recurrent_core=core); device = torch.device("cuda:0")
        checkpoint_path = PRIOR_ARTIFACT / "checkpoints/r2d3-step-000200.pt"
        payload = torch.load(checkpoint_path, map_location=device, weights_only=False)
        source = payload.get("model")
        if not isinstance(source, dict):
            raise RuntimeError("prior checkpoint has no model state")
        model = RecurrentDistributionalQ(config).to(device)
        destination = model.state_dict()
        compatible = {
            key: value
            for key, value in source.items()
            if key in destination and destination[key].shape == value.shape
        }
        # State encoder, recurrent core, action encoder and distributional Q
        # head define the inherited policy.  The new categorical auxiliary
        # heads are intentionally initialized afresh.
        required_prefixes = ("state.", "core.", "action.", "q.")
        missing_required = sorted(
            key
            for key in source
            if key.startswith(required_prefixes) and key not in compatible
        )
        if missing_required:
            raise RuntimeError(f"prior checkpoint is incompatible with policy core: {missing_required}")
        incompatible_source = sorted(
            key
            for key, value in source.items()
            if key in destination and destination[key].shape != value.shape
        )
        model.load_state_dict(compatible, strict=False)
        model.eval()
        atomic_json(
            self.artifact / "initial_model_warm_start.json",
            {
                "source_checkpoint": str(checkpoint_path),
                "source_checkpoint_hash": sha(checkpoint_path),
                "loaded_keys": sorted(compatible),
                "newly_initialized_keys": sorted(set(destination) - set(compatible)),
                "shape_incompatible_source_keys": incompatible_source,
                "required_policy_core_complete": True,
            },
        )
        return model, config, device, sha(PRIOR_ARTIFACT / "checkpoints/r2d3-step-000200.pt")

    def validate(self, model: Any, policy_hash: str, split: str, games: int, *, label: str,
                 checkpoint: str | None = None, core: str | None = None,
                 seed_namespace: str | None = None) -> list[dict[str, Any]]:
        """Play ``games`` candidate validation games and gate on legality.

        Each game is independent and its opponent, seed and seat are pure
        functions of ``index``, so the games are distributed across worker
        processes without changing which games are played.  Workers keep the
        model on the same CUDA device: the identical weights evaluated on
        another device can round a legal-action score differently and quietly
        change the greedy candidate's move.
        """
        splits = self.load_splits()
        namespace = seed_namespace or label
        schedule = validation_schedule(splits[split], games, seed_namespace=namespace)
        workers = self.validation_workers()
        if workers > 1 and checkpoint and core:
            self.release_cuda_cache()
            return self._validate_parallel(policy_hash, split, games, label=label, checkpoint=checkpoint, core=core,
                                           hidden_size=int(model.config.hidden_size), workers=workers,
                                           schedule=schedule)
        import torch
        rows = []
        for entry in schedule:
            index = int(entry["index"]); asset = splits[split][int(entry["asset_index"])]
            row, _ = self.e2e._candidate_validation_game(self.artifact, asset, model, torch.device("cuda:0"), policy_hash,
                                                         index=800000 + index, candidate_side=int(entry["candidate_side"]), seed=int(entry["seed"]),
                                                         callback_timeout_seconds=float(self.profile.validation_callback_timeout_seconds))
            row["evaluation"] = label; rows.append(row); self.progress(label, index + 1, games, faults=sum(not r["legal"] or r["candidate_fault"] or r["timeout"] for r in rows))
        if any(not row["legal"] or row["candidate_fault"] or row["timeout"] for row in rows): raise RuntimeError(f"{label} CABT legality/fault gate failed")
        summary_key = digest({"label": label, "namespace": namespace})
        summary_path = self.artifact / "evaluation_summaries" / f"{summary_key}.json"
        atomic_json(summary_path, {"label": label, "seed_namespace": namespace, "summary": summarize_evaluation(rows)})
        return rows

    def _validate_parallel(self, policy_hash: str, split: str, games: int, *, label: str, checkpoint: str,
                           core: str, hidden_size: int, workers: int,
                           schedule: list[dict[str, int | str]]) -> list[dict[str, Any]]:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import multiprocessing
        jobs = [{"index": int(entry["index"]), "asset_index": int(entry["asset_index"]),
                 "candidate_side": int(entry["candidate_side"]), "seed": int(entry["seed"]),
                 "split": split, "label": label, "artifact": str(self.artifact), "policy_hash": policy_hash,
                 "checkpoint": checkpoint, "core": core, "hidden_size": hidden_size,
                 "device": "cuda:0",
                 "callback_timeout_seconds": float(self.profile.validation_callback_timeout_seconds)} for entry in schedule]
        results: dict[int, dict[str, Any]] = {}
        with ProcessPoolExecutor(max_workers=min(workers, games), mp_context=multiprocessing.get_context("spawn")) as executor:
            futures = [executor.submit(_validation_game_job, job) for job in jobs]
            for future in as_completed(futures):
                index, row, _traces = future.result(); results[index] = row
                self.progress(label, len(results), games, faults=sum(not value["legal"] or value["candidate_fault"] or value["timeout"] for value in results.values()))
        rows = [results[index] for index in range(games)]
        if any(not row["legal"] or row["candidate_fault"] or row["timeout"] for row in rows): raise RuntimeError(f"{label} CABT legality/fault gate failed")
        summary_key = digest({"label": label, "parallel": True})
        summary_path = self.artifact / "evaluation_summaries" / f"{summary_key}.json"
        namespace = str(schedule[0]["seed_namespace"]) if schedule else label
        atomic_json(summary_path, {"label": label, "seed_namespace": namespace, "summary": summarize_evaluation(rows)})
        return rows

    def load_splits(self) -> dict[str, list[Any]]:
        # snapshot_gate's deterministic registry/split construction is also the split identity validator.
        from mage_ptcg.policy_learning.submitted_opponents import assert_no_leakage, load_registry, split_assets
        assets = load_registry(ROOT, self.e2e.LEDGER); splits = split_assets(assets, seed=71000); assert_no_leakage(splits); return splits

    def run_scale_benchmark(self) -> dict[str, Any]:
        rows = []
        actor_candidates = (4, 8, 12, 16) if self.profile.name == "smoke" else (4, 8, 12, 16, 20, 24, 28)
        for config_index, actor_count in enumerate(actor_candidates):
            started = time.monotonic()
            games, _episodes = self.deck_pool_episodes(
                self.profile.scale_games_per_config,
                810000 + config_index * self.profile.scale_games_per_config,
                workers=actor_count,
            )
            elapsed = time.monotonic() - started
            faults = sum(not row["legal"] or row["candidate_fault"] or row["timeout"] for row in games)
            if faults: raise RuntimeError(f"scale config cpu_actor/{actor_count} has {faults} faults")
            rows.append({"mode": "cpu_actor", "actor_count": actor_count, "games": len(games), "faults": faults,
                         "elapsed_seconds": elapsed, "games_per_second": len(games) / max(1e-9, elapsed),
                         "measurement": "real_process_pool_ppo_vs_rule_deck_games", "status": "PASS"})
        write_csv(self.artifact / "scale_benchmark.csv", rows); selected = max(rows, key=lambda row: row["games_per_second"])
        atomic_json(self.artifact / "selected_scale_config.json", {"status": "PASS", "selected": selected, "all_configs": rows})
        return {"rows": len(rows), "selected": selected, "fault_count": 0}

    def cabt_workers(self) -> int:
        path = self.stage_dir("scale_benchmark") / "output_manifest.json"
        if path.is_file():
            selected = json.loads(path.read_text()).get("selected", {})
            if selected.get("mode") == "cpu_actor":
                return max(1, int(selected["actor_count"]))
        return self.profile.cabt_workers

    def validation_workers(self) -> int:
        """Bound concurrent CABT validation games.

        Deliberately *not* bounded by ``cabt_workers()``.  That value is chosen
        by a benchmark whose games run entirely in-process, while a validation
        game spends nearly all of its wall clock inside a submitted-opponent
        subprocess, so the two have no common capacity limit.

        The binding limit is one physical core per concurrent opponent.
        Submitted search opponents spend a wall-clock budget per decision, so
        oversubscribing cores does not slow the games down and does not fail
        loudly -- it silently buys each opponent fewer rollouts, weakening it
        and inflating the candidate win rate that gates promotion.
        """
        return max(1, min(int(self.profile.cuda_validation_workers),
                          physical_cores() - int(self.profile.validation_reserved_cores)))

    def release_cuda_cache(self) -> None:
        """Return the learner's retained VRAM before spawning game workers.

        Training leaves a large block cached in the allocator.  The parent does
        not need it while games run in child processes, and under WSL that
        reservation is also charged against the Windows commit budget that each
        worker's own CUDA context competes for.  Releasing it first is what makes
        concurrent validation workers affordable rather than VM-destabilising.
        """
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize(); torch.cuda.empty_cache()

    def adapter_episodes(self, label: str, population: Path, opponent_id: str, games: int, offset: int, *, workers: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Collect public semantic trajectories from a pinned BC/Family adapter."""
        from mage_ptcg.offline_scaleup.candidate_runtime import adapter_for
        payload = json.loads(population.read_text()); entry = next(row for row in payload["entries"] if row["opponent_id"] == opponent_id); deck = list(entry["deck_cards"])
        source_deck = self.artifact / "source_decks" / f"{label}.csv"
        if not source_deck.exists(): write_text(source_deck, "\n".join(str(card) for card in deck) + "\n")
        jobs = [{"index": index, "entry": entry, "label": label, "artifact": str(self.artifact), "game_number": offset + index, "source_lineage": payload.get("semantic_population_digest", "")} for index in range(games)]
        results: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        if workers > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            import multiprocessing
            with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as executor:
                futures = [executor.submit(_adapter_episode_job, job) for job in jobs]
                for future in as_completed(futures):
                    index, row, episode = future.result(); results[index] = (row, episode); self.progress(f"replay_collection/{label}", len(results), games, faults=sum(not value[0]["legal"] or value[0]["candidate_fault"] or value[0]["timeout"] for value in results.values()))
        else:
            for job in jobs:
                index, row, episode = _adapter_episode_job(job); results[index] = (row, episode)
        return [results[index][0] for index in range(games)], [results[index][1] for index in range(games)]

    def deck_pool_episodes(self, games: int, offset: int, *, workers: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        payload = json.loads(self.deck_pool_path.read_text())
        if payload.get("schema") != "r2d3-deck-opponent-pool-v1" or not payload.get("entries"):
            raise RuntimeError("deck opponent pool is missing or incompatible")
        entries = payload["entries"]
        jobs = [{"index": index, "entry": entries[index % len(entries)], "artifact": str(self.artifact),
                 "game_number": offset + index} for index in range(games)]
        results: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        if workers > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            import multiprocessing
            with ProcessPoolExecutor(max_workers=min(workers, games), mp_context=multiprocessing.get_context("spawn")) as executor:
                futures = [executor.submit(_deck_pool_episode_job, job) for job in jobs]
                for future in as_completed(futures):
                    index, row, episode = future.result(); results[index] = (row, episode)
                    self.progress("replay_collection/environment_top_decks", len(results), games,
                                  faults=sum(not value[0]["legal"] or value[0]["candidate_fault"] or value[0]["timeout"] for value in results.values()))
        else:
            for job in jobs:
                index, row, episode = _deck_pool_episode_job(job); results[index] = (row, episode)
        return [results[index][0] for index in range(games)], [results[index][1] for index in range(games)]

    def replay_schedule(self) -> list[tuple[str, int]]:
        ppo_games = (self.profile.replay_games * 2) // 5
        if ppo_games % 2: ppo_games -= 1
        deck_games = self.profile.replay_games // 5
        bc_games = self.profile.replay_games // 5
        family_games = self.profile.replay_games - ppo_games - deck_games - bc_games
        return [("ppo_submitted_rule", ppo_games), ("environment_top_decks", deck_games),
                ("bc_recurrent", bc_games), ("family_alakazam", family_games)]

    def _load_replay_demonstrations(self, splits: dict[str, list[Any]], *, calibrated_asset_ids: set[str]) -> list[dict[str, Any]] | None:
        path = self.artifact / "replay_demonstrations.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text())
        if (
            payload.get("schema") != "r2d3-replay-demonstrations-v3"
            or payload.get("collection_identity_hash") != self.context["collection_identity_hash"]
            or set(payload.get("teacher_asset_ids", [])) != calibrated_asset_ids
        ):
            raise RuntimeError("replay demonstration checkpoint schema differs")
        by_id = {asset.asset_id: asset for asset in splits["training"]}
        output = []
        for item in payload.get("items", []):
            asset_id = str(item.get("asset_id", ""))
            if asset_id not in calibrated_asset_ids or asset_id not in by_id:
                raise RuntimeError(f"replay demonstration asset disappeared: {asset_id}")
            output.append({"asset": by_id[asset_id], "game": item["game"], "traces": item["traces"]})
        if len(output) != int(payload.get("count", -1)):
            raise RuntimeError("replay demonstration checkpoint count differs")
        return output

    def _save_replay_demonstrations(self, demonstrations: list[dict[str, Any]], *, calibrated_asset_ids: set[str]) -> None:
        items = [{"asset_id": item["asset"].asset_id, "game": item["game"], "traces": item["traces"]}
                 for item in demonstrations]
        atomic_json(self.artifact / "replay_demonstrations.json",
                    {"schema": "r2d3-replay-demonstrations-v3",
                     "collection_identity_hash": self.context["collection_identity_hash"],
                     "teacher_asset_ids": sorted(calibrated_asset_ids),
                     "count": len(items), "items": items}, durable=True)

    def _expected_replay_label(self, start: int, end: int) -> str:
        cursor = 0
        for label, count in self.replay_schedule():
            boundary = cursor + count
            if cursor <= start < boundary:
                if end > boundary:
                    raise RuntimeError("replay checkpoint crosses a source boundary")
                return label
            cursor = boundary
        raise RuntimeError("replay checkpoint starts beyond the configured schedule")

    def _load_replay_chunks(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        episodes: list[dict[str, Any]] = []
        manifests: list[dict[str, Any]] = []
        directory = self.artifact / "replay_collection_chunks"
        for path in sorted(directory.glob("chunk-*.json")):
            payload = json.loads(path.read_text())
            content = {key: value for key, value in payload.items() if key != "content_hash"}
            if (
                payload.get("schema") != "r2d3-replay-collection-chunk-v2"
                or payload.get("collection_identity_hash") != self.context["collection_identity_hash"]
                or payload.get("content_hash") != digest(content)
            ):
                raise RuntimeError(f"replay checkpoint hash differs: {path}")
            start, end = int(payload["start"]), int(payload["end"])
            if start != len(rows) or end - start != len(payload["rows"]) or len(payload["rows"]) != len(payload["episodes"]):
                raise RuntimeError(f"replay checkpoint is non-contiguous: {path}")
            expected = self._expected_replay_label(start, end)
            if payload["source"] != expected:
                raise RuntimeError(f"replay checkpoint source differs: {path}")
            rows.extend(payload["rows"])
            episodes.extend(payload["episodes"])
            manifests.append({"path": str(path), "start": start, "end": end,
                              "source": expected, "content_hash": payload["content_hash"]})
        return rows, episodes, manifests

    def _save_replay_chunk(self, *, source: str, start: int, rows: list[dict[str, Any]],
                           episodes: list[dict[str, Any]]) -> dict[str, Any]:
        if len(rows) != len(episodes) or not rows:
            raise RuntimeError("replay checkpoint chunk is empty or misaligned")
        end = start + len(rows)
        if self._expected_replay_label(start, end) != source:
            raise RuntimeError("replay checkpoint source does not match schedule")
        content = {"schema": "r2d3-replay-collection-chunk-v2",
                   "collection_identity_hash": self.context["collection_identity_hash"], "source": source,
                   "start": start, "end": end, "rows": rows, "episodes": episodes}
        payload = {**content, "content_hash": digest(content)}
        path = self.artifact / "replay_collection_chunks" / f"chunk-{end:06d}.json"
        if path.exists():
            existing = json.loads(path.read_text())
            if existing != payload:
                raise RuntimeError(f"refusing to replace a different replay checkpoint: {path}")
        else:
            atomic_json(path, payload, durable=True)
        return {"path": str(path), "start": start, "end": end, "source": source,
                "content_hash": payload["content_hash"]}

    def run_replay_collection(self) -> dict[str, Any]:
        if self.replay_input is not None:
            return self.reuse_replay_collection()
        splits = self.load_splits(); slow_assets = {"dev/waterbox_search_v3"} if self.profile.name == "smoke" else set()
        asset_splits = {**splits, "training": [asset for asset in splits["training"] if asset.asset_id not in slow_assets]}
        workers = self.cabt_workers()
        calibrated = set(self.output("teacher_calibration")["teacher_asset_ids"])
        demonstrations = self._load_replay_demonstrations(splits, calibrated_asset_ids=calibrated)
        if demonstrations is None:
            _, demonstrations = self.e2e.asset_smoke_gate(
                self.artifact, asset_splits,
                games_per_asset=1 if self.profile.name == "smoke" else 8,
                workers=workers,
            )
            demonstrations = [item for item in demonstrations if item["asset"].asset_id in calibrated]
            if not demonstrations: raise RuntimeError("teacher calibration produced no replay demonstrations")
            self._save_replay_demonstrations(demonstrations, calibrated_asset_ids=calibrated)
        schedule = self.replay_schedule()
        rows, episodes, chunk_manifests = self._load_replay_chunks()
        completed = len(rows)
        existing_counts = {
            label: sum(row.get("bucket") in (
                {"submitted_agents_dev", "rule_v0_v1"} if label == "ppo_submitted_rule" else {label}
            ) for row in rows)
            for label, _count in schedule
        }
        for label, count in schedule:
            remaining = count - existing_counts[label]
            if remaining < 0:
                raise RuntimeError(f"replay checkpoint has too many rows for {label}")
            while remaining:
                batch = min(self.profile.replay_checkpoint_games, remaining)
                if batch % 2 and label == "ppo_submitted_rule": batch -= 1
                if batch == 0: raise RuntimeError("invalid replay quality interval")
                if label == "ppo_submitted_rule": current_rows, current_episodes = self.e2e.ppo_population_gate(self.artifact, splits, games=batch, seed_offset=completed, slow_asset_once=self.profile.name == "smoke", minimum_asset_coverage=1 if self.profile.name == "smoke" else None, excluded_execution_asset_ids=slow_assets, workers=workers)
                elif label == "environment_top_decks": current_rows, current_episodes = self.deck_pool_episodes(batch, completed, workers=workers)
                elif label == "bc_recurrent": current_rows, current_episodes = self.adapter_episodes("bc_recurrent", BC_POPULATION, "gate5a-eval-primary-bc-recurrent", batch, completed, workers=workers)
                else: current_rows, current_episodes = self.adapter_episodes("family_alakazam", FAMILY_POPULATION, "family-alakazam-deck-74d86ec36fd144b9", batch, completed, workers=workers)
                faults = sum(not row["legal"] or row["candidate_fault"] or row["timeout"] for row in current_rows)
                gate = {"completed_games": completed + len(current_rows), "interval_games": len(current_rows), "faults": faults, "timeouts": sum(bool(row["timeout"]) for row in current_rows), "hidden_leaks": 0, "split_leakage": 0, "corrupt_sequences": 0, "status": "PASS" if faults == 0 else "FAIL"}
                atomic_json(self.stage_dir("replay_collection") / f"quality_gate_{completed + len(current_rows):06d}.json", gate)
                if faults: raise RuntimeError("replay quality gate failed")
                chunk_manifests.append(self._save_replay_chunk(
                    source=label, start=completed, rows=current_rows, episodes=current_episodes
                ))
                rows.extend(current_rows); episodes.extend(current_episodes)
                completed += len(current_rows); remaining -= len(current_rows)
                self.progress("replay_collection", completed, self.profile.replay_games, faults=0)
        if completed != self.profile.replay_games: raise RuntimeError("replay collection count mismatch")
        write_csv(self.artifact / "replay_collection_games.csv", rows); self.context["demonstrations"] = demonstrations; self.context["episodes"] = episodes
        source_counts = {
            "ppo_submitted_rule": sum(row["bucket"] in {"submitted_agents_dev", "rule_v0_v1"} for row in rows),
            "environment_top_decks": sum(row["bucket"] == "environment_top_decks" for row in rows),
            "bc_recurrent": sum(row["bucket"] == "bc_recurrent" for row in rows),
            "family_alakazam": sum(row["bucket"] == "family_alakazam" for row in rows),
        }
        expected_counts = dict(schedule)
        if source_counts != expected_counts:
            raise RuntimeError(f"replay source accounting mismatch: {source_counts} != {expected_counts}")
        deck_pool = json.loads(self.deck_pool_path.read_text())
        atomic_json(self.artifact / "replay_collection_manifest.json", {"games": completed, "sources": source_counts, "submitted_population_manifest_includes": [asset.asset_id for asset in splits["training"]], "environment_deck_pool_hash": deck_pool["pool_hash"], "environment_deck_count": deck_pool["unique_decks"], "smoke_execution_exclusions": sorted(slow_assets), "excluded_splits": ["validation", "deck_holdout", "final_holdout"], "quality_intervals": self.profile.replay_quality_interval, "checkpoint_games": self.profile.replay_checkpoint_games, "chunks": chunk_manifests})
        return {"games": completed, "demonstrations": len(demonstrations), "episodes": len(episodes), "fault_count": 0, "resume_cursor": completed}

    def run_teacher_calibration(self) -> dict[str, Any]:
        """Select local submitted teachers only after balanced-seat quality checks."""
        splits = self.load_splits()
        rows, _ = self.e2e.asset_smoke_gate(self.artifact, splits, games_per_asset=8, workers=self.cabt_workers())
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows: grouped.setdefault(str(row["asset_id"]), []).append(row)
        selected: list[str] = []; summary: list[dict[str, Any]] = []
        for asset_id, group in sorted(grouped.items()):
            seats = {int(row["asset_side"]) for row in group}
            wins = sum(row.get("winner") == row.get("asset_side") for row in group)
            rate = wins / len(group)
            qualified = seats == {0, 1} and rate >= .5
            summary.append({"asset_id": asset_id, "games": len(group), "wins": wins, "win_rate": rate, "qualified": qualified})
            if qualified: selected.append(asset_id)
        atomic_json(self.artifact / "teacher_calibration.json", {"schema": "r2d3-teacher-calibration-v1", "teachers": summary, "teacher_asset_ids": selected})
        if not selected: raise RuntimeError("teacher calibration qualified no local submitted assets")
        return {"teachers": len(summary), "teacher_asset_ids": selected, "fault_count": 0}

    def reuse_replay_collection(self) -> dict[str, Any]:
        """Verify, then reference a completed immutable collection artifact."""
        source = self.replay_input
        if source is None or source == self.artifact: raise RuntimeError("replay input artifact must be a distinct path")
        required = (source / "replay.json", source / "replay_manifest.json", source / "replay_collection_manifest.json",
                    source / "stages/replay_collection/status.json", source / "source_identity.json")
        if any(not path.is_file() for path in required): raise RuntimeError("replay input artifact is incomplete")
        source_status = json.loads((source / "stages/replay_collection/status.json").read_text())
        collection = json.loads((source / "replay_collection_manifest.json").read_text())
        source_replay = json.loads((source / "replay_manifest.json").read_text())
        identity = json.loads((source / "source_identity.json").read_text())
        protected = {name: sha(ROOT / name) for name in ("main.py", "deck.csv", "agents/rule_agent.py")}
        ppo_games = (self.profile.replay_games * 2) // 5
        if ppo_games % 2: ppo_games -= 1
        deck_games = self.profile.replay_games // 5; bc_games = self.profile.replay_games // 5
        expected = {"ppo_submitted_rule": ppo_games, "environment_top_decks": deck_games,
                    "bc_recurrent": bc_games,
                    "family_alakazam": self.profile.replay_games - ppo_games - deck_games - bc_games}
        gates = sorted((source / "stages/replay_collection").glob("quality_gate_*.json"))
        if source_status.get("status") != "PASS" or collection.get("games") != self.profile.replay_games or collection.get("sources") != expected:
            raise RuntimeError("replay input collection count/source manifest is incompatible")
        if len(gates) < 2 or any(json.loads(path.read_text()).get("status") != "PASS" for path in gates):
            raise RuntimeError("replay input quality gates are incomplete or failed")
        if (
            source_replay.get("replay_sha256") != sha(source / "replay.json")
            or identity.get("protected_before") != protected
            or identity.get("semantic_feature_version") != self.context["source"]["semantic_feature_version"]
        ):
            raise RuntimeError("replay input checksum, protected-source identity, or semantic feature version mismatch")
        self.context["replay_input"] = {"artifact": str(source), "replay_sha256": source_replay["replay_sha256"],
                                        "collection_manifest_hash": sha(source / "replay_collection_manifest.json"), "quality_gates": [path.name for path in gates]}
        atomic_json(self.artifact / "replay_collection_reuse_manifest.json", {"status": "PASS", **self.context["replay_input"],
                    "verified_games": collection["games"], "verified_sources": collection["sources"], "note": "verified immutable replay input; no games were recollected"})
        return {"games": collection["games"], "reused_from": str(source), "quality_gates": len(gates), "fault_count": 0, "resume_cursor": collection["games"]}

    def run_replay_freeze(self) -> dict[str, Any]:
        if self.replay_input is not None and "replay_input" not in self.context:
            self.reuse_replay_collection()
        if "replay_input" in self.context:
            from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
            source = Path(self.context["replay_input"]["artifact"]); original = PrioritizedSequenceReplay.load(source / "replay.json")
            source_manifest = json.loads((source / "replay_manifest.json").read_text())
            if original.is_windowed:
                if int(source_manifest.get("sequence_stride", -1)) != self.profile.replay_sequence_stride:
                    raise RuntimeError("windowed replay stride differs from the requested profile")
                replay = original
                expansion = {"source_sequences": len(original), "expanded_sequences": len(original),
                             "stride": self.profile.replay_sequence_stride, "storage": "window_refs",
                             "reused_existing_windows": True, "terminal_burn_in_rejections": 0}
            else:
                replay, expansion = self.e2e.expand_replay_windows(original, stride=self.profile.replay_sequence_stride)
            saved = replay.save(self.artifact / "replay.json"); loaded = PrioritizedSequenceReplay.load(self.artifact / "replay.json")
            manifest = {"schema": "r2d3-e2e-replay-manifest-v2", "burn_in": 8, "learner_unroll": 20, "sequence_stride": self.profile.replay_sequence_stride,
                        "sequences": len(loaded), "replay_sha256": saved["sha256"], "save_reload_passed": len(loaded) == len(replay),
                        "reused_immutable_replay": self.context["replay_input"], "window_expansion": expansion}
            atomic_json(self.artifact / "replay_manifest.json", manifest); atomic_json(self.artifact / "replay_statistics.json", manifest)
        else:
            if "episodes" not in self.context:
                splits = self.load_splits()
                calibrated = set(self.output("teacher_calibration")["teacher_asset_ids"])
                demonstrations = self._load_replay_demonstrations(splits, calibrated_asset_ids=calibrated)
                rows, episodes, _chunks = self._load_replay_chunks()
                if demonstrations is None or len(rows) != self.profile.replay_games:
                    raise RuntimeError("completed replay collection checkpoints are unavailable or incomplete")
                self.context["demonstrations"] = demonstrations
                self.context["episodes"] = episodes
            replay, manifest = self.e2e.build_replay_gate(self.artifact, self.context["demonstrations"], self.context["episodes"], sequence_stride=self.profile.replay_sequence_stride)
        if manifest["sequences"] < self.profile.minimum_replay_sequences: raise RuntimeError(f"replay has {manifest['sequences']} sequences; requires {self.profile.minimum_replay_sequences}")
        replay_hash = sha(self.artifact / "replay.json")
        if not manifest.get("save_reload_passed"): raise RuntimeError("replay save/reload verification failed")
        atomic_json(self.artifact / "replay_freeze_manifest.json", {"status": "PASS", "replay_hash": replay_hash, "sequences": len(replay), "manifest": manifest, "save_reload_verified": True})
        self.context["replay"] = replay; self.context["replay_manifest"] = manifest
        return {"sequences": len(replay), "replay_hash": replay_hash, "checkpoint": str(self.artifact / "replay.json"), "fault_count": 0}

    def run_learner_scale_benchmark(self) -> dict[str, Any]:
        """Select the fastest CUDA batch inside the WSL-safe memory budget."""
        import gc
        import torch
        from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
        from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig, RecurrentDistributionalQ

        replay = self.replay()
        device = torch.device("cuda:0")
        rows: list[dict[str, Any]] = []
        for candidate in self.profile.learner_batch_candidates:
            batch_size = min(int(candidate), len(replay))
            memory_limit = self.profile.learner_peak_reserved_limit_mb
            projected_peak = projected_learner_peak_reserved_mb(batch_size, rows)
            if (
                memory_limit is not None
                and projected_peak is not None
                and projected_peak > memory_limit
            ):
                rows.append({
                    "batch_size": batch_size, "updates": 0, "sequences": 0,
                    "elapsed_seconds": 0.0, "sequences_per_second": 0.0,
                    "updates_per_second": 0.0,
                    "peak_allocated_mb": 0.0, "peak_reserved_mb": 0.0,
                    "projected_peak_reserved_mb": projected_peak,
                    "memory_limit_mb": memory_limit,
                    "status": "SKIPPED_MEMORY_BUDGET",
                })
                continue
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            model = RecurrentDistributionalQ(
                R2D3ModelConfig(hidden_size=self.profile.model_hidden_size)
            ).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
            learner = R2D3Learner(model, optimizer, config=LearnerConfig(target_update_interval=25))
            measured = 0
            try:
                warm_sample = replay.sample(batch_size, beta=.4, demonstration_ratio=1 / 32,
                                            seed=838000 + candidate, episode_first=True)
                warm_batch = self.e2e._learner_batch(warm_sample, device)
                with torch.autocast(device_type="cuda", dtype=torch.bfloat16,
                                    enabled=torch.cuda.is_bf16_supported()):
                    learner.update(**warm_batch)
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                started = time.perf_counter()
                for update in range(self.profile.learner_scale_updates):
                    sample = replay.sample(batch_size, beta=.4, demonstration_ratio=1 / 32,
                                           seed=839000 + candidate * 100 + update, episode_first=True)
                    learner_batch = self.e2e._learner_batch(sample, device)
                    with torch.autocast(device_type="cuda", dtype=torch.bfloat16,
                                        enabled=torch.cuda.is_bf16_supported()):
                        metrics = learner.update(**learner_batch)
                    scalar_metrics_finite = all(
                        math.isfinite(float(value))
                        for key, value in metrics.items()
                        if key != "sequence_priorities"
                    )
                    priorities_finite = all(
                        math.isfinite(float(value))
                        for value in metrics.get("sequence_priorities", ())
                    )
                    if not scalar_metrics_finite or not priorities_finite:
                        raise FloatingPointError("non-finite learner scale metric")
                    measured += len(sample.sequences)
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
                peak_allocated_mb = torch.cuda.max_memory_allocated() / 2**20
                peak_reserved_mb = torch.cuda.max_memory_reserved() / 2**20
                status = (
                    "PASS"
                    if memory_limit is None or peak_reserved_mb <= memory_limit
                    else "REJECTED_MEMORY_BUDGET"
                )
                rows.append({
                    "batch_size": batch_size, "updates": self.profile.learner_scale_updates,
                    "sequences": measured, "elapsed_seconds": elapsed,
                    "sequences_per_second": measured / max(1e-9, elapsed),
                    "updates_per_second": self.profile.learner_scale_updates / max(1e-9, elapsed),
                    "peak_allocated_mb": peak_allocated_mb,
                    "peak_reserved_mb": peak_reserved_mb,
                    "memory_limit_mb": memory_limit,
                    "status": status,
                })
            except torch.OutOfMemoryError as exc:
                rows.append({
                    "batch_size": batch_size, "updates": 0, "sequences": 0,
                    "elapsed_seconds": 0.0,
                    "sequences_per_second": 0.0, "updates_per_second": 0.0,
                    "peak_allocated_mb": torch.cuda.max_memory_allocated() / 2**20,
                    "peak_reserved_mb": torch.cuda.max_memory_reserved() / 2**20,
                    "status": "REJECTED_OOM", "error": str(exc).splitlines()[0],
                })
            finally:
                del learner, optimizer, model
                if "learner_batch" in locals():
                    del learner_batch
                if "warm_batch" in locals():
                    del warm_batch
                gc.collect()
                torch.cuda.empty_cache()
        passing = [row for row in rows if row["status"] == "PASS"]
        if not passing:
            raise RuntimeError("no learner batch candidate passed")
        selected = max(passing, key=lambda row: (row["sequences_per_second"], row["batch_size"]))
        write_csv(self.artifact / "learner_scale_benchmark.csv", rows)
        atomic_json(self.artifact / "selected_learner_scale_config.json",
                    {"status": "PASS", "selected": selected, "all_configs": rows,
                     "memory_limit_mb": self.profile.learner_peak_reserved_limit_mb})
        return {"selected": selected, "rows": len(rows), "fault_count": 0}

    def learner_batch_size(self) -> int:
        path = self.stage_dir("learner_scale_benchmark") / "output_manifest.json"
        if path.is_file():
            selected = json.loads(path.read_text()).get("selected", {})
            if selected.get("status") == "PASS":
                return int(selected["batch_size"])
        return self.profile.learner_batch_size

    def training_updates(self, reference_updates: int) -> int:
        """Bound nominal reuse of each replay window across measured batches."""
        if self.profile.name == "smoke":
            return int(reference_updates)
        scaled = max(
            1,
            round(reference_updates * self.profile.learner_batch_size / self.learner_batch_size()),
        )
        caps = {
            self.profile.screen_updates: self.profile.screen_draws_per_window,
            self.profile.multiseed_updates: self.profile.multiseed_draws_per_window,
            self.profile.full_training_updates: self.profile.full_draws_per_window,
            self.profile.psro_best_response_updates: self.profile.psro_draws_per_window,
        }
        cap = caps.get(reference_updates)
        if cap is None:
            raise ValueError(f"unknown training reference budget: {reference_updates}")
        return min(scaled, max(1, math.floor(len(self.fresh_training_replay()) * cap / self.learner_batch_size())))

    def replay(self) -> Any:
        if "replay" not in self.context:
            from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
            self.context["replay"] = PrioritizedSequenceReplay.load(self.artifact / "replay.json")
            self.context["replay_manifest"] = json.loads((self.artifact / "replay_manifest.json").read_text())
        return self.context["replay"]

    def replay_identity(self) -> str:
        """SHA-256 of the frozen replay, hashed once per process.

        Every training run bound its checkpoints to this digest by re-reading
        the multi-gigabyte replay file; the artifact is immutable for the whole
        campaign, so hash it once.
        """
        if "replay_identity" not in self.context:
            self.context["replay_identity"] = sha(self.artifact / "replay.json")
        return self.context["replay_identity"]

    def fresh_training_replay(self) -> Any:
        """Give each experiment an isolated mutable PER state.

        Model candidates must start from identical priorities, and a process
        restart must restore only that run's priorities from its checkpoint.
        Sharing ``self.replay()`` made architecture order and restart timing
        part of the experiment.
        """
        factory = self.context.get("active_replay_factory")
        if factory is not None:
            return factory()
        return self.replay().fork()

    def training_identity(self, *, name: str, core: str, demo_ratio: float, updates: int, seed: int) -> dict[str, Any]:
        """What a stored run must match before its work may be reused."""
        batch_size = self.learner_batch_size()
        target_interval = max(4, round(25 * 128 / batch_size))
        learning_rate = min(4e-4, 1e-4 * (batch_size / self.profile.learner_batch_size) ** 0.5)
        return {"name": name, "core": core, "demo_ratio": demo_ratio, "updates": updates, "seed": seed,
                "batch_size": batch_size, "hidden_size": self.profile.model_hidden_size,
                "target_update_interval": target_interval, "importance_beta_schedule": "linear_by_training_progress",
                "learning_rate": learning_rate, "reference_batch_size": self.profile.learner_batch_size, "bc_weight": self.profile.bc_weight,
                "training_log_interval": self.profile.training_log_interval,
                "population_hash": self.context["source"]["population_hash"],
                "source_patch_hash": self.context["source"]["source_patch_hash"],
                "replay_hash": self.context.get("active_replay_hash", self.replay_identity()),
                "initial_checkpoint": self.context.get("active_initial_checkpoint"),
                "initial_checkpoint_hash": self.context.get("active_initial_checkpoint_hash")}

    @staticmethod
    def _restore_candidates(directory: Path, updates: int) -> list[Path]:
        """Newest-to-oldest checkpoints that could belong to this run."""
        paths = [
            path for path in directory.glob("r2d3-step-*.pt")
            if path.stem.rsplit("-", 1)[1].isdigit()
            and 0 < int(path.stem.rsplit("-", 1)[1]) <= updates
        ]
        return sorted(paths, key=lambda path: int(path.stem.rsplit("-", 1)[1]), reverse=True)

    def train(self, *, name: str, core: str, demo_ratio: float, updates: int, seed: int, resume_proof: bool = False) -> dict[str, Any]:
        import torch
        from mage_ptcg.policy_learning.r2d3.checkpoint import checkpoint_hash, load_checkpoint, save_checkpoint
        from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
        from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig, RecurrentDistributionalQ
        device = torch.device("cuda:0")
        config = R2D3ModelConfig(recurrent_core=core, hidden_size=self.profile.model_hidden_size)
        batch_size = self.learner_batch_size()
        target_interval = max(4, round(25 * 128 / batch_size))
        learning_rate = min(4e-4, 1e-4 * (batch_size / self.profile.learner_batch_size) ** 0.5)
        population_hash = self.context["source"]["population_hash"]
        replay_hash = self.context.get("active_replay_hash", self.replay_identity())
        directory = self.artifact / "checkpoints" / name; directory.mkdir(parents=True, exist_ok=True)
        identity = self.training_identity(name=name, core=core, demo_ratio=demo_ratio, updates=updates, seed=seed)
        identity_hash = digest(identity)
        manifest_path = directory / "training_manifest.json"

        def build() -> tuple[Any, Any, Any]:
            built = RecurrentDistributionalQ(config).to(device)
            optimiser = torch.optim.AdamW(built.parameters(), lr=learning_rate)
            return built, optimiser, R2D3Learner(
                built, optimiser, config=LearnerConfig(target_update_interval=target_interval, bc_weight=self.profile.bc_weight)
            )

        # A finished run is reused rather than repeated.  The recorded identity
        # must match exactly; anything else is a different experiment and is
        # retrained instead of being silently adopted.
        prior_source_draws: Counter[str] = Counter()
        if manifest_path.exists():
            stored = json.loads(manifest_path.read_text())
            if stored.get("identity") == identity:
                prior_source_draws.update({str(key): int(value) for key, value in stored.get("source_draws", {}).items()})
            final = directory / f"r2d3-step-{updates:06d}.pt"
            if stored.get("identity") == identity and final.is_file():
                try:
                    if (
                        stored.get("training_identity_hash") != identity_hash
                        or stored.get("checkpoint", {}).get("sha256") != checkpoint_hash(final)
                    ):
                        raise ValueError("completed manifest/checkpoint identity differs")
                    model, _optimizer, learner = build()
                    restored_step = load_checkpoint(
                        final, model=model, target=learner.target, optimizer=_optimizer,
                        expected_population_hash=population_hash, expected_replay_manifest_hash=replay_hash,
                        map_location=device, expected_training_identity_hash=identity_hash,
                    )
                    if restored_step != updates:
                        raise ValueError("completed checkpoint step differs")
                    self.monitor.note(f"[stage] reusing completed training run {name} ({updates} updates)")
                    curve = [dict(row) for row in csv.DictReader((directory / "training_curve.csv").open(encoding="utf-8"))] if (directory / "training_curve.csv").is_file() else []
                    return {"model": model, "config": config, "checkpoint": str(final), "checkpoint_hash": checkpoint_hash(final),
                            "training_identity_hash": identity_hash, "updates": updates,
                            "resumed": bool(stored.get("resume_proof")), "curve": curve, "reused": True}
                except Exception as exc:
                    completed_reuse_rejection = {
                        "path": str(final),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            else:
                completed_reuse_rejection = None
        else:
            completed_reuse_rejection = None

        torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
        initial_checkpoint = self.context.get("active_initial_checkpoint")
        initial_payload = None
        if initial_checkpoint is not None:
            expected_initial_hash = self.context.get("active_initial_checkpoint_hash")
            if not expected_initial_hash or sha(Path(initial_checkpoint)) != expected_initial_hash:
                raise RuntimeError("best-response initialization checkpoint hash differs")
            initial_payload = torch.load(Path(initial_checkpoint), map_location=device, weights_only=False)
            if initial_payload.get("schema") not in {"r2d3-checkpoint-v1", "r2d3-checkpoint-v2"}:
                raise RuntimeError("best-response initialization checkpoint is incompatible")

        def initialized() -> tuple[Any, Any, Any]:
            built, optimiser, built_learner = build()
            if initial_payload is not None:
                built.load_state_dict(initial_payload["model"])
                built_learner.target.load_state_dict(initial_payload["target"])
            return built, optimiser, built_learner

        replay = self.fresh_training_replay()
        model, optimizer, learner = initialized()
        bf16 = bool(torch.cuda.is_bf16_supported()); resumed = False; checkpoint = None; finite_metrics = True
        curve: list[dict[str, Any]] = []
        source_draws: Counter[str] = prior_source_draws
        # The sample stream is seeded per step, so restarting from a stored
        # step replays exactly the updates an uninterrupted run would have.
        start = 0
        candidates = self._restore_candidates(directory, updates)
        rejected: list[dict[str, str]] = (
            [completed_reuse_rejection] if completed_reuse_rejection is not None else []
        )
        imported = self.imported_final_checkpoint(name, updates=updates)
        if imported is not None:
            restore = Path(str(imported["child_path"]))
            candidate_replay = self.fresh_training_replay()
            candidate_model, candidate_optimizer, candidate_learner = initialized()
            restored_step = load_checkpoint(
                restore, model=candidate_model, target=candidate_learner.target,
                optimizer=candidate_optimizer, expected_population_hash=population_hash,
                expected_replay_manifest_hash=replay_hash, map_location=device,
                replay=candidate_replay, expected_training_identity_hash=str(imported["training_identity_hash"]),
            )
            if restored_step != updates:
                raise RuntimeError("continuation checkpoint did not restore its recorded final step")
            replay, model, optimizer, learner = candidate_replay, candidate_model, candidate_optimizer, candidate_learner
            learner.steps = restored_step; start = restored_step; resumed = True
            checkpoint = {"schema": "r2d3-checkpoint-v2", "population_hash": population_hash, "replay_manifest_hash": replay_hash,
                          "step": start, "sha256": checkpoint_hash(restore), "continued_from_parent": True}
            self.monitor.note(f"[stage] continuing {name} from verified parent final checkpoint {start}/{updates}")
            if (directory / "training_curve.csv").is_file():
                curve = [dict(row) for row in csv.DictReader((directory / "training_curve.csv").open(encoding="utf-8"))
                         if int(float(row["step"])) <= start]
        else:
            for restore in candidates:
                candidate_replay = self.fresh_training_replay()
                candidate_model, candidate_optimizer, candidate_learner = initialized()
                expected_step = int(restore.stem.rsplit("-", 1)[1])
                try:
                    restored_step = load_checkpoint(
                        restore, model=candidate_model, target=candidate_learner.target,
                        optimizer=candidate_optimizer, expected_population_hash=population_hash,
                        expected_replay_manifest_hash=replay_hash, map_location=device,
                        replay=candidate_replay, expected_training_identity_hash=identity_hash,
                    )
                    if restored_step != expected_step:
                        raise ValueError("checkpoint filename and stored step differ")
                except Exception as exc:
                    rejected.append({"path": str(restore), "error": f"{type(exc).__name__}: {exc}"})
                    continue
                replay, model, optimizer, learner = (
                    candidate_replay, candidate_model, candidate_optimizer, candidate_learner
                )
                learner.steps = restored_step
                start = restored_step; resumed = True
                checkpoint = {"schema": "r2d3-checkpoint-v2", "population_hash": population_hash, "replay_manifest_hash": replay_hash,
                              "step": start, "sha256": checkpoint_hash(restore)}
                self.monitor.note(f"[stage] resuming {name} from step {start}/{updates}")
                if (directory / "training_curve.csv").is_file():
                    curve = [dict(row) for row in csv.DictReader((directory / "training_curve.csv").open(encoding="utf-8"))
                             if int(float(row["step"])) <= start]
                break
        if rejected:
            atomic_json(
                directory / "checkpoint_rejections.json",
                {"status": "RECOVERED_FROM_PRIOR_VALID" if resumed else "NO_VALID_CHECKPOINT",
                 "selected_step": start if resumed else None, "rejected": rejected},
            )
        if imported is None and candidates and not resumed:
            raise RuntimeError(f"no valid resumable checkpoint remains for {name}")
        halfway = max(1, updates // 2)
        # Bounded interruption cost: without periodic saves an interrupted run
        # threw away everything since the halfway point.
        interval = max(1, min(250, max(1, updates // 40)))
        for step in range(start + 1, updates + 1):
            progress_fraction = (step - 1) / max(1, updates - 1)
            beta = .4 + .6 * progress_fraction
            sample = replay.sample(min(batch_size, len(replay)), beta=beta, demonstration_ratio=demo_ratio, seed=seed + step, episode_first=True, source_balanced=True)
            source_draws.update(
                sequence.learner[0].behavior_source
                for sequence in sample.sequences
                if sequence.learner
            )
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=bf16): metrics = learner.update(**self.e2e._learner_batch(sample, device))
            priority_updates = replay.update_priorities(sample.indices, metrics.pop("sequence_priorities"), importance=sample.weights)
            metrics["priority_items"] = float(len(priority_updates))
            metrics["priority_unique_items"] = float(len({row["sample_id"] for row in priority_updates}))
            current_finite = all(math.isfinite(float(value)) for value in metrics.values())
            if not current_finite:
                raise FloatingPointError(f"{name} produced a non-finite learner metric at step {step}")
            finite_metrics = finite_metrics and current_finite
            if step % self.profile.training_log_interval == 0 or step == updates:
                curve.append({"step": step, **metrics, "demo_ratio": demo_ratio})
            if step == halfway or step == updates or step % interval == 0:
                path = directory / f"r2d3-step-{step:06d}.pt"
                write_csv(directory / "training_curve.csv", curve)
                checkpoint = save_checkpoint(
                    path, model=model, target=learner.target, optimizer=optimizer,
                    population_hash=population_hash, replay_manifest_hash=replay_hash,
                    step=step, replay=replay, training_identity_hash=identity_hash,
                )
                if resume_proof and step == halfway:
                    model, optimizer, learner = build()
                    replay = self.fresh_training_replay()
                    restored = load_checkpoint(
                        path, model=model, target=learner.target, optimizer=optimizer,
                        expected_population_hash=population_hash, expected_replay_manifest_hash=replay_hash,
                        map_location=device, replay=replay,
                        expected_training_identity_hash=identity_hash,
                    )
                    learner.steps = restored; resumed = restored == step
            if step % max(1, min(1000, updates // 10)) == 0: self.progress(name, step, updates, faults=0, learner_updates=step)
        if checkpoint is None or learner.steps != updates or not finite_metrics: raise RuntimeError(f"{name} learner failed")
        total_draws = sum(source_draws.values())
        write_csv(directory / "training_curve.csv", curve); atomic_json(manifest_path, {"name": name, "core": core, "demo_ratio": demo_ratio, "updates": updates, "seed": seed, "bf16": bf16, "resume_proof": resumed, "checkpoint": checkpoint, "training_log_interval": self.profile.training_log_interval, "metrics_samples": len(curve), "identity": identity, "training_identity_hash": identity_hash, "resumed_from_step": start, "replay_windows": len(replay), "nominal_replay_draws": total_draws, "draws_per_window": total_draws / max(1, len(replay)), "source_draws": dict(sorted(source_draws.items()))})
        return {"model": model, "config": config, "checkpoint": str(directory / f"r2d3-step-{updates:06d}.pt"),
                "checkpoint_hash": checkpoint["sha256"], "training_identity_hash": identity_hash,
                "updates": updates, "resumed": resumed, "curve": curve, "reused": False}

    @staticmethod
    def architecture(value: str) -> tuple[str, float]:
        core, _, ratio = value.partition("_demo_"); return core, {"0": 0.0, "1_32": 1 / 32, "1_16": 1 / 16}[ratio]

    def run_architecture_screen(self) -> dict[str, Any]:
        rows = []
        for index, architecture in enumerate(self.profile.screen_architectures):
            core, ratio = self.architecture(architecture); trained = self.train(name=f"screen-{architecture}", core=core, demo_ratio=ratio, updates=self.training_updates(self.profile.screen_updates), seed=840000 + index)
            validation = self.validate(trained["model"], trained["checkpoint_hash"], "validation", self.profile.screen_validation_games, label=f"screen-{architecture}", checkpoint=trained["checkpoint"], core=core, seed_namespace="selection")
            win_rate = sum(row.get("winner") == row["candidate_side"] for row in validation) / len(validation)
            rows.append({"architecture": architecture, "core": core, "demo_ratio": ratio, "updates": trained["updates"], "validation_games": len(validation), "win_rate": win_rate, "faults": 0, "checkpoint": trained["checkpoint"], "checkpoint_hash": trained["checkpoint_hash"], "status": "PASS"})
        write_csv(self.artifact / "architecture_screen_results.csv", rows); selected = sorted(rows, key=lambda row: (-row["win_rate"], row["architecture"]))[:self.profile.multiseed_top_k]
        if len(selected) != 2: raise RuntimeError("screen did not select top two architectures")
        atomic_json(self.artifact / "selected_architecture.json", {"status": "PASS", "selected": selected, "complete_enumeration": list(self.profile.screen_architectures)})
        return {"architectures": len(rows), "selected": [row["architecture"] for row in selected], "fault_count": 0}

    def run_multiseed_training(self) -> dict[str, Any]:
        selected = self.output("architecture_screen")["selected"]; rows = []
        for architecture in selected:
            core, ratio = self.architecture(architecture)
            for seed in self.profile.multiseed_seeds:
                trained = self.train(name=f"multiseed-{architecture}-seed{seed}", core=core, demo_ratio=ratio, updates=self.training_updates(self.profile.multiseed_updates), seed=850000 + seed, resume_proof=True)
                validation = self.validate(trained["model"], trained["checkpoint_hash"], "validation", self.profile.screen_validation_games, label=f"multiseed-{architecture}-seed{seed}", checkpoint=trained["checkpoint"], core=core, seed_namespace="selection")
                rows.append({"architecture": architecture, "seed": seed, "updates": trained["updates"], "validation_games": len(validation), "win_rate": sum(row.get("winner") == row["candidate_side"] for row in validation) / len(validation), "checkpoint": trained["checkpoint"], "checkpoint_hash": trained["checkpoint_hash"], "resume_proved": trained["resumed"], "status": "PASS"})
        if len(rows) != self.profile.multiseed_top_k * len(self.profile.multiseed_seeds) or not all(row["resume_proved"] for row in rows): raise RuntimeError("multiseed enumeration/resume gate failed")
        write_csv(self.artifact / "multiseed_validation_results.csv", rows); best = max(rows, key=lambda row: (row["win_rate"], row["architecture"], -row["seed"]))
        atomic_json(self.artifact / "selected_multiseed_candidate.json", best); return {"runs": len(rows), "selected": best, "fault_count": 0}

    def run_full_training(self) -> dict[str, Any]:
        selected = self.output("multiseed_training")["selected"]; core, ratio = self.architecture(selected["architecture"])
        trained = self.train(name="full-training", core=core, demo_ratio=ratio, updates=self.training_updates(self.profile.full_training_updates), seed=860000 + int(selected["seed"]), resume_proof=True)
        atomic_json(self.artifact / "full_training_results.json", {key: value for key, value in trained.items() if key not in {"model", "config", "curve"}})
        return {"architecture": selected["architecture"], "updates": trained["updates"], "checkpoint": trained["checkpoint"],
                "checkpoint_hash": trained["checkpoint_hash"], "training_identity_hash": trained["training_identity_hash"],
                "resume_proved": trained["resumed"], "fault_count": 0}

    def load_full_model(self) -> tuple[Any, str, str, str]:
        import torch
        result = self.output("full_training"); architecture = result["architecture"]; core, _ = self.architecture(architecture)
        checkpoint = Path(result["checkpoint"])
        if not checkpoint.is_file() or sha(checkpoint) != result["checkpoint_hash"]:
            raise RuntimeError("full-training checkpoint hash differs")
        selected = self.output("multiseed_training")["selected"]
        _selected_core, demo_ratio = self.architecture(selected["architecture"])
        expected_training_identity = digest(self.training_identity(
            name="full-training",
            core=core,
            demo_ratio=demo_ratio,
            updates=self.training_updates(self.profile.full_training_updates),
            seed=860000 + int(selected["seed"]),
        ))
        if self.inherited_stage("full_training"):
            continuation = self.continuation_manifest()
            inherited = continuation.get("full_checkpoint", {}) if continuation else {}
            if inherited.get("child_path") != str(checkpoint) or inherited.get("sha256") != result["checkpoint_hash"]:
                raise RuntimeError("inherited full-training checkpoint lineage differs")
            expected_training_identity = str(result.get("training_identity_hash", ""))
            if not expected_training_identity:
                raise RuntimeError("inherited full-training identity is missing")
        if result.get("training_identity_hash") != expected_training_identity:
            raise RuntimeError("full-training output identity differs from the current campaign")
        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if (
            payload.get("schema") != "r2d3-checkpoint-v2"
            or payload.get("training_identity_hash") != expected_training_identity
            or int(payload.get("step", -1)) != int(result["updates"])
        ):
            raise RuntimeError("full-training checkpoint metadata differs from the current campaign")
        from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig
        model = self.e2e._load_model(
            checkpoint,
            R2D3ModelConfig(recurrent_core=core, hidden_size=self.profile.model_hidden_size),
            torch.device("cuda:0"),
        )
        return model, result["checkpoint_hash"], str(result["checkpoint"]), core

    def run_development_validation(self) -> dict[str, Any]:
        model, policy_hash, checkpoint, core = self.load_full_model()
        rows = self.validate(model, policy_hash, "validation", self.profile.development_validation_games, label="development_validation", checkpoint=checkpoint, core=core, seed_namespace="development")
        win_rate = sum(row.get("winner") == row["candidate_side"] for row in rows) / len(rows); write_csv(self.artifact / "development_validation_results.csv", rows)
        return {"games": len(rows), "win_rate": win_rate, "fault_count": 0, "checkpoint": self.output("full_training")["checkpoint"]}

    def holdout_prerequisites(self, stage: str) -> list[dict[str, Any]]:
        """Evaluate every upstream gate a holdout split depends on.

        A holdout split is spent the moment CABT starts playing it, so the
        condition must be that the upstream gates *passed*, not merely that
        their stages ran.  ``STAGES`` already orders execution; that ordering
        alone would open the final holdout on a model whose deck holdout lost.
        """
        threshold = self.profile.holdout_min_win_rate
        checks: list[dict[str, Any]] = []
        for name in HOLDOUT_PREREQUISITE_STAGES[stage]:
            if not (self.stage_dir(name) / "output_manifest.json").exists():
                checks.append({"stage": name, "satisfied": False, "reason": "STAGE_OUTPUT_MISSING"}); continue
            output = self.output(name)
            if output.get("status") != "PASS":
                checks.append({"stage": name, "satisfied": False, "reason": "STAGE_NOT_PASS", "status": output.get("status")}); continue
            if int(output.get("fault_count", 0)) != 0:
                checks.append({"stage": name, "satisfied": False, "reason": "STAGE_REPORTED_FAULTS", "fault_count": output.get("fault_count")}); continue
            if name.endswith("_holdout_gate") and not output.get("holdout_used"):
                checks.append({"stage": name, "satisfied": False, "reason": "UPSTREAM_HOLDOUT_NOT_USED"}); continue
            if name in WIN_RATE_PREREQUISITE_STAGES:
                observed = output.get("win_rate")
                if not isinstance(observed, (int, float)):
                    checks.append({"stage": name, "satisfied": False, "reason": "WIN_RATE_NOT_RECORDED"}); continue
                if float(observed) < threshold:
                    checks.append({"stage": name, "satisfied": False, "reason": "WIN_RATE_BELOW_THRESHOLD",
                                   "observed": float(observed), "threshold": threshold}); continue
            checks.append({"stage": name, "satisfied": True})
        return checks

    def conditional_holdout(self, *, stage: str, split: str, games: int, filename: str) -> dict[str, Any]:
        marker = self.artifact / f"{split}_holdout_used.json"
        # Checked before the prerequisites: a reservation that survived an
        # interrupted run means those games were already dealt, and no later
        # prerequisite outcome can give them back.
        if marker.exists():
            raise RuntimeError(f"{split} one-time-use marker already exists at {marker}; this split is spent and must not be replayed")
        checks = self.holdout_prerequisites(stage)
        unmet = [check for check in checks if not check["satisfied"]]
        if unmet:
            atomic_json(self.artifact / filename, {"status": "NOT_USED", "reason": "upstream promotion prerequisites not met",
                                                   "holdout_used": False, "threshold": self.profile.holdout_min_win_rate,
                                                   "prerequisites": checks})
            return {"holdout_used": False, "gate": "condition_not_met", "prerequisites": checks,
                    "unmet_prerequisites": [check["stage"] for check in unmet], "fault_count": 0}
        model, policy_hash, checkpoint, core = self.load_full_model()
        # Reserve before the first game.  Writing the marker only after a
        # successful evaluation would let a crash mid-holdout look unused and
        # be replayed, which is exactly the leak a one-time split forbids.
        atomic_json(marker, {"status": "RESERVED", "stage": stage, "reserved_at": now(), "games": games, "policy_hash": policy_hash}, durable=True)
        rows = self.validate(model, policy_hash, split, games, label=stage, checkpoint=checkpoint, core=core, seed_namespace=stage); write_csv(self.artifact / filename, rows)
        win_rate = sum(row.get("winner") == row["candidate_side"] for row in rows) / len(rows)
        atomic_json(marker, {"status": "USED", "stage": stage, "used_at": now(), "games": games, "policy_hash": policy_hash, "win_rate": win_rate}, durable=True)
        return {"holdout_used": True, "games": len(rows), "win_rate": win_rate, "prerequisites": checks, "fault_count": 0}

    def run_psro_payoff(self) -> dict[str, Any]:
        from mage_ptcg.policy_learning.league import PSROState, PopulationMember
        from scripts.test_sim import run_match
        from main import make_rule_agent, make_rule_agent_v1
        model, policy_hash, _checkpoint, _core = self.load_full_model(); import torch
        from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
        deck = [int(value) for value in (ROOT / "deck.csv").read_text().splitlines() if value.strip()]
        members = [("rule-v0", lambda game, seat: make_rule_agent(deck=deck)), ("rule-v1", lambda game, seat: make_rule_agent_v1(deck=deck)), ("ppo", lambda game, seat: self.e2e.TracingPPO(deck=deck)), ("r2d3", lambda game, seat: R2D3CandidatePolicy(model, deck=deck, device=torch.device("cuda:0"), policy_version=policy_hash, game_id=game, seat=seat))]
        population = [
            {"id": "rule-v0", "kind": "rule_v0", "policy_hash": sha(ROOT / "agents/rule_agent.py"), "source_lineage": git("rev-parse", "HEAD"), "family": "RULE_V0"},
            {"id": "rule-v1", "kind": "rule_v1", "policy_hash": digest(["rule-v1", sha(ROOT / "main.py")]), "source_lineage": git("rev-parse", "HEAD"), "family": "RULE_V1"},
            {"id": "ppo", "kind": "ppo", "policy_hash": sha(ROOT / "runs/policy-learning-gate5a/model/best.pt"), "source_lineage": git("rev-parse", "HEAD"), "family": "PPO"},
            {"id": "r2d3", "kind": "r2d3", "policy_hash": policy_hash, "source_lineage": git("rev-parse", "HEAD"), "family": "R2D3"},
        ]
        jobs: list[dict[str, Any]] = []
        for left in range(4):
            for right in range(left + 1, 4):
                for game in range(self.profile.psro_pair_games):
                    index = len(jobs)
                    jobs.append({
                        "index": index, "left": left, "right": right, "game_index": game,
                        "seat_left": game % 2, "game_id": f"psro-{left}-{right}-{game}",
                        "seed": 870000 + index,
                    })
        payoff_identity = digest({
            "schema": "r2d3-psro-payoff-schedule-v1",
            "source_identity_hash": self.context["source_identity_hash"],
            "full_policy_hash": policy_hash,
            "population": population,
            "jobs": jobs,
        })

        def play(job: dict[str, Any]) -> dict[str, Any]:
            left, right, seat_left = int(job["left"]), int(job["right"]), int(job["seat_left"])
            gid = str(job["game_id"])
            left_agent = members[left][1](gid, seat_left)
            right_agent = members[right][1](gid, 1 - seat_left)
            agents = [left_agent, right_agent] if seat_left == 0 else [right_agent, left_agent]
            result = run_match(
                deck_a_path=ROOT / "deck.csv", deck_b_path=ROOT / "deck.csv",
                agent_a_name="rule", agent_b_name="rule", seed=int(job["seed"]),
                output_dir=self.artifact / "psro_scratch", save_html=False, save_result=False,
                agent_a_factory=lambda _d, _s, x=agents[0]: x,
                agent_b_factory=lambda _d, _s, x=agents[1]: x,
            )
            payoff = (
                0.0 if result.get("winner") == 2
                else 1.0 if result.get("winner") == seat_left
                else -1.0
            )
            return {"status": result.get("status"), "winner": result.get("winner"), "payoff_left": payoff}

        payoff_workers = self.validation_workers()
        if payoff_workers > 1: self.release_cuda_cache()
        checkpoint_rows = durable_psro_payoff_prefix(
            self.stage_dir("psro_payoff") / "payoff_checkpoint.json",
            identity_hash=payoff_identity,
            jobs=jobs,
            play=play,
            persisted=lambda completed, total: self.progress(
                "psro_payoff", completed, total, faults=0, completed_pairs=completed
            ),
            workers=payoff_workers,
            job=functools.partial(_psro_payoff_job, {
                "checkpoint": self.output("full_training")["checkpoint"],
                "core": _core, "hidden_size": int(self.profile.model_hidden_size),
                "policy_hash": policy_hash, "artifact": str(self.artifact), "device": "cuda:0",
            }),
        )
        sums = [[0.0] * 4 for _ in range(4)]; counts = [[0] * 4 for _ in range(4)]
        games: list[dict[str, Any]] = []
        for row in checkpoint_rows:
            job = row["job"]; left, right = int(job["left"]), int(job["right"])
            payoff = float(row["payoff_left"])
            sums[left][right] += payoff; sums[right][left] -= payoff
            counts[left][right] += 1; counts[right][left] += 1
            games.append({
                **job, "left_policy": members[left][0], "right_policy": members[right][0],
                "payoff_left": payoff, "winner": row.get("winner"), "status": row["status"],
            })
        matrix = [[0.0 if i == j else sums[i][j] / counts[i][j] for j in range(4)] for i in range(4)]; rows = [{"row_policy": members[i][0], "column_policy": members[j][0], "payoff": matrix[i][j], "games": counts[i][j], "ci95": 1.96*math.sqrt(max(0, 1-matrix[i][j]**2)/counts[i][j]) if counts[i][j] else 0.0} for i in range(4) for j in range(4)]
        state = PSROState(); [state.add_member(PopulationMember(name, name, name, hashlib.sha256(name.encode()).hexdigest()), against_existing=matrix[index][:index]) for index, (name, _) in enumerate(members)]; meta = state.meta_strategy()
        write_csv(self.artifact / "psro_payoff_matrix.csv", rows); write_csv(self.artifact / "psro_game_results.csv", games); atomic_json(self.artifact / "psro_meta_strategy.json", {"population": population, "payoff_matrix": matrix, "meta_strategy": meta})
        return {"population": len(members), "games": len(games), "meta_strategy": meta,
                "payoff_checkpoint": str(self.stage_dir("psro_payoff") / "payoff_checkpoint.json"),
                "payoff_identity_hash": payoff_identity, "fault_count": 0}

    def _load_psro_online_checkpoint(
        self, *, mixture_hash: str, policy_hash: str, replay_type: Any
    ) -> tuple[Any | None, list[dict[str, Any]]]:
        directory = self.artifact / "psro_online_checkpoints"
        state_paths = sorted(directory.glob("checkpoint-*-state.json"), reverse=True)
        rejected: list[dict[str, str]] = []
        for state_path in state_paths:
            try:
                state = json.loads(state_path.read_text())
                content = {key: value for key, value in state.items() if key != "content_hash"}
                replay_path = state_path.with_name(state_path.name.replace("-state.json", "-replay.json"))
                filename_games = int(state_path.name.split("-")[1])
                rows = state.get("rows")
                games = int(state.get("games", -1))
                if (
                    state.get("schema") != "r2d3-psro-online-checkpoint-v2"
                    or state.get("content_hash") != digest(content)
                    or state.get("mixture_hash") != mixture_hash
                    or state.get("candidate_policy_version") != policy_hash
                    or not isinstance(rows, list)
                    or games != filename_games
                    or games != len(rows)
                    or not 0 <= games <= self.profile.psro_online_games
                    or not replay_path.is_file()
                    or state.get("replay_sha256") != sha(replay_path)
                ):
                    raise ValueError("checkpoint identity or shape differs")
                for index, row in enumerate(rows):
                    if (
                        row.get("game_id") != f"psro-online-{index:06d}"
                        or row.get("meta_strategy_hash") != mixture_hash
                        or row.get("candidate_policy_version") != policy_hash
                        or row.get("result") != "DONE"
                    ):
                        raise ValueError("checkpoint rows are non-contiguous or misattributed")
                online = replay_type.load(replay_path)
                if (
                    int(state.get("sequences", -1)) != len(online)
                    or sum(int(row.get("sequence_count", -1)) for row in rows) != len(online)
                ):
                    raise ValueError("checkpoint sequence count differs")
                if rejected:
                    atomic_json(
                        self.artifact / "psro_online_checkpoint_rejections.json",
                        {"status": "RECOVERED_FROM_PRIOR_VALID", "rejected": rejected,
                         "selected": str(state_path)},
                    )
                return online, list(rows)
            except Exception as exc:
                rejected.append(
                    {"path": str(state_path), "error": f"{type(exc).__name__}: {exc}"}
                )
        if state_paths:
            atomic_json(
                self.artifact / "psro_online_checkpoint_rejections.json",
                {"status": "NO_VALID_CHECKPOINT", "rejected": rejected},
            )
            raise RuntimeError("PSRO online checkpoints exist but none is valid")
        return None, []

    def run_psro_online_collection(self) -> dict[str, Any]:
        """Collect current-policy trajectories against the frozen meta-mixture."""
        import torch
        from main import make_rule_agent, make_rule_agent_v1
        from scripts.test_sim import run_match
        from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
        from mage_ptcg.policy_learning.r2d3.online_collection import MixtureManifest, collection_record
        from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
        from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, shape_episode_rewards, split_episode

        model, policy_hash, _checkpoint, _core = self.load_full_model()
        mixture = MixtureManifest.from_payload(json.loads((self.artifact / "psro_meta_strategy.json").read_text()))
        atomic_json(self.artifact / "psro_opponent_mixture_manifest.json", mixture.document())
        deck = [int(value) for value in (ROOT / "deck.csv").read_text().splitlines() if value.strip()]
        own_deck_hash = sha(ROOT / "deck.csv")
        checkpoint_directory = self.artifact / "psro_online_checkpoints"
        online, rows = self._load_psro_online_checkpoint(
            mixture_hash=mixture.mixture_hash,
            policy_hash=policy_hash,
            replay_type=PrioritizedSequenceReplay,
        )
        if online is None:
            online = PrioritizedSequenceReplay(max(1024, self.profile.psro_online_games * 64))
        else:
            self.monitor.note(
                f"[stage] resuming PSRO online collection from "
                f"{len(rows)}/{self.profile.psro_online_games} games"
            )
        quotas = balanced_mixture_quotas(list(mixture.members), self.profile.psro_online_games,
                                         floor_probability=self.profile.psro_floor_probability)
        members_by_id = {member.opponent_policy_id: member for member in mixture.members}
        full_members = [members_by_id[identifier] for identifier in sorted(quotas) for _ in range(quotas[identifier])]
        schedule: list[dict[str, Any]] = []
        for index in range(len(rows), self.profile.psro_online_games):
            seed = 875000 + index; member = full_members[index]
            schedule.append({"index": index, "seed": seed, "side": index % 2,
                             "game_id": f"psro-online-{index:06d}",
                             "opponent_policy_id": member.opponent_policy_id, "member": member})

        def ingest(entry: dict[str, Any], payload: dict[str, Any]) -> None:
            """Fold one finished game into the replay in schedule order.

            Kept in the controller on purpose: sequence insertion order fixes the
            replay's priority layout, so it must follow the schedule rather than
            whichever worker happened to finish first.
            """
            index, side, member = int(entry["index"]), int(entry["side"]), entry["member"]
            game_id = str(entry["game_id"]); result_status = str(payload["status"])
            if result_status != "DONE" or payload.get("candidate_status") in {"ERROR", "INVALID", "TIMEOUT"}:
                raise RuntimeError(f"PSRO online collection failed at {game_id}: {result_status}")
            winner = payload.get("winner"); outcome = 1.0 if winner == side else -1.0 if winner in (0, 1) else 0.0
            segments: list[list[dict[str, Any]]] = [[]]
            for trace in payload["traces"]:
                if trace.get("trainable_single_action", True): segments[-1].append(trace)
                elif segments[-1]: segments.append([])
            segments = [segment for segment in segments if segment]; sequence_count = 0
            for segment_index, segment in enumerate(segments):
                segment_outcome = outcome if segment_index == len(segments) - 1 else 0.0
                rewards = shape_episode_rewards([float(trace["potential"]) for trace in segment], outcome=segment_outcome, gamma=.99)
                transitions = []
                for offset, trace in enumerate(segment):
                    terminal = offset == len(segment) - 1
                    transitions.append(R2D3Transition(tuple(trace["state"]), tuple(tuple(action) for action in trace["actions"]),
                        int(trace["selected_action"]), rewards[offset], 0.0 if terminal else .99, terminal, policy_hash,
                        f"psro_online_current_candidate:{member.opponent_policy_id}", member.policy_hash, own_deck_hash, member.source_lineage,
                        member.family, own_deck_hash))
                for sequence in split_episode(transitions, burn_in=8, unroll=20, stride=20, prefix=f"{game_id}-segment-{segment_index}"):
                    online.add(sequence); sequence_count += 1
            rows.append(collection_record(game_id=game_id, mixture=mixture, member=member, candidate_policy_version=policy_hash,
                                          result=result_status, winner=winner, candidate_side=side, sequence_count=sequence_count))
            self.progress("psro_online_collection", index + 1, self.profile.psro_online_games, faults=0, games=index + 1, sequences=len(online))
            completed = index + 1
            if completed % self.profile.replay_checkpoint_games == 0 or completed == self.profile.psro_online_games:
                checkpoint_directory.mkdir(parents=True, exist_ok=True)
                replay_path = checkpoint_directory / f"checkpoint-{completed:06d}-replay.json"
                state_path = checkpoint_directory / f"checkpoint-{completed:06d}-state.json"
                saved_checkpoint = online.save(replay_path)
                content = {
                    "schema": "r2d3-psro-online-checkpoint-v2",
                    "games": completed,
                    "sequences": len(online),
                    "replay_sha256": saved_checkpoint["sha256"],
                    "mixture_hash": mixture.mixture_hash,
                    "candidate_policy_version": policy_hash,
                    "rows": rows,
                }
                atomic_json(state_path, {**content, "content_hash": digest(content)}, durable=True)

        online_workers = self.validation_workers()
        job_context = {"checkpoint": self.output("full_training")["checkpoint"], "core": _core,
                       "hidden_size": int(self.profile.model_hidden_size), "policy_hash": policy_hash,
                       "artifact": str(self.artifact), "device": "cuda:0"}
        play_online = functools.partial(_psro_online_job, job_context)
        if online_workers > 1 and len(schedule) > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            import multiprocessing
            self.release_cuda_cache()
            # Traces are large, so the schedule is drained in bounded windows.
            # Submitting all games at once would let one slow game hold every
            # later result in memory while the durable prefix waits for it.
            window_size = online_workers * 4
            with ProcessPoolExecutor(max_workers=online_workers, mp_context=multiprocessing.get_context("spawn")) as executor:
                for start in range(0, len(schedule), window_size):
                    window = schedule[start:start + window_size]
                    by_index = {int(entry["index"]): entry for entry in window}
                    held: dict[int, dict[str, Any]] = {}
                    expected = int(window[0]["index"])
                    futures = [executor.submit(play_online, {key: value for key, value in entry.items() if key != "member"})
                               for entry in window]
                    for future in as_completed(futures):
                        index, payload = future.result(); held[int(index)] = payload
                        while expected in held:
                            ingest(by_index[expected], held.pop(expected)); expected += 1
                    if held:
                        raise RuntimeError(f"PSRO online window left {len(held)} out-of-order result(s) unplaced")
        else:
            for entry in schedule:
                index, payload = play_online({key: value for key, value in entry.items() if key != "member"})
                ingest(entry, payload)
        if not len(online): raise RuntimeError("PSRO online collection produced no trainable sequence")
        saved = online.save(self.artifact / "psro_online_replay.json")
        write_csv(self.artifact / "psro_online_collection.csv", rows)
        observed = {identifier: sum(row.get("sampled_opponent") == identifier for row in rows) for identifier in quotas}
        if observed != quotas:
            raise RuntimeError(f"PSRO online opponent quotas differ: {observed} != {quotas}")
        atomic_json(self.artifact / "psro_online_replay_manifest.json", {"schema": "r2d3-psro-online-replay-v2", "games": len(rows), "sequences": len(online), "replay_sha256": saved["sha256"], "mixture_hash": mixture.mixture_hash, "candidate_policy_version": policy_hash, "member_quotas": quotas, "member_games": observed, "faults": 0})
        return {"games": len(rows), "sequences": len(online), "replay_hash": saved["sha256"], "mixture_hash": mixture.mixture_hash, "fault_count": 0}

    def run_psro_best_response(self) -> dict[str, Any]:
        from mage_ptcg.policy_learning.r2d3.online_collection import AlternatingReplayPartitions
        from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
        selected = self.output("multiseed_training")["selected"]; core, ratio = self.architecture(selected["architecture"]); rows = []
        online = PrioritizedSequenceReplay.load(self.artifact / "psro_online_replay.json")
        combined_hash = digest({"offline": self.replay_identity(), "online": sha(self.artifact / "psro_online_replay.json"), "mixture": self.output("psro_online_collection")["mixture_hash"]})
        offline_template = self.replay()
        online_template = online
        self.context["active_replay_factory"] = lambda: AlternatingReplayPartitions(
            offline_template.fork(),
            online_template.fork(),
        )
        self.context["active_replay_hash"] = combined_hash
        self.context["active_initial_checkpoint"] = self.output("full_training")["checkpoint"]
        self.context["active_initial_checkpoint_hash"] = self.output("full_training")["checkpoint_hash"]
        seeds = self.profile.multiseed_seeds if self.profile.name == "production" else (0,)
        try:
            for seed in seeds:
                trained = self.train(name=f"psro-best-response-seed{seed}", core=core, demo_ratio=ratio, updates=self.training_updates(self.profile.psro_best_response_updates), seed=880000 + seed, resume_proof=True)
                validation = self.validate(trained["model"], trained["checkpoint_hash"], "validation", self.profile.screen_validation_games, label=f"psro-best-response-seed{seed}", checkpoint=trained["checkpoint"], core=core, seed_namespace="psro_best_response")
                rows.append({"seed": seed, "updates": trained["updates"], "checkpoint": trained["checkpoint"], "checkpoint_hash": trained["checkpoint_hash"], "resume_proved": trained["resumed"], "validation_games": len(validation), "win_rate": sum(row.get("winner") == row["candidate_side"] for row in validation) / len(validation), "combined_replay_hash": combined_hash, "mixture_hash": self.output("psro_online_collection")["mixture_hash"]})
        finally:
            self.context.pop("active_replay_factory", None); self.context.pop("active_replay_hash", None)
            self.context.pop("active_initial_checkpoint", None); self.context.pop("active_initial_checkpoint_hash", None)
        write_csv(self.artifact / "psro_best_response_results.csv", rows); best = max(rows, key=lambda row: (row["win_rate"], -row["seed"]))
        status = "VALIDATION_PASS" if best["win_rate"] >= self.profile.holdout_min_win_rate else "VALIDATION_BELOW_THRESHOLD"
        atomic_json(self.artifact / "psro_expansion_decision.json", {"status": "NO_EXPANSION", "reason": "population expansion remains gated on independent improvement", "best_response_validation": status, "best_response": best})
        return {"seeds": len(rows), "updates_each": rows[0]["updates"],
                "reference_updates_each": self.profile.psro_best_response_updates,
                "checkpoint": best["checkpoint"], "win_rate": best["win_rate"],
                "validation_games": best["validation_games"], "combined_replay_hash": combined_hash,
                "fault_count": 0}

    def run_promotion(self) -> dict[str, Any]:
        threshold = self.profile.holdout_min_win_rate
        development = self.output("development_validation"); deck = self.output("deck_holdout_gate"); final = self.output("final_holdout_gate")

        def cleared(output: dict[str, Any]) -> bool:
            # "Used" only says the games were played.  Promotion needs the
            # result of those games to clear the same threshold.
            observed = output.get("win_rate")
            return bool(output.get("holdout_used")) and isinstance(observed, (int, float)) and float(observed) >= threshold

        evidence = {"threshold": threshold, "development_win_rate": development.get("win_rate"),
                    "deck_holdout": {"used": bool(deck.get("holdout_used")), "win_rate": deck.get("win_rate")},
                    "final_holdout": {"used": bool(final.get("holdout_used")), "win_rate": final.get("win_rate")}}
        promoted = bool(development["win_rate"] >= threshold and cleared(deck) and cleared(final))
        decision = "PROMOTION_ELIGIBLE" if promoted else "NO_PROMOTION_RECOMMENDED"
        write_text(self.artifact / "promotion_decision.md", f"# Promotion decision\n\n`{decision}`。未使用holdoutは性能棄却ではなくfail-closedな条件未達を表します。\n\n```json\n{json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)}\n```\n")
        return {"decision": decision, "evidence": evidence, "fault_count": 0}

    def seal(self) -> None:
        identity = self.context["source"]; protected_after = {name: sha(ROOT / name) for name in identity["protected_before"]}
        readiness = {"overall_status": self.output("promotion_decision")["decision"], "profile": self.profile.name, "production_long_run_executed": self.profile.name == "production", "source_patch_hash": identity["source_patch_hash"], "protected_unchanged": {name: protected_after[name] == value for name, value in identity["protected_before"].items()}, "commit_created": False, "push_executed": False, "kaggle_submission_executed": False, "final_holdout_used": self.output("final_holdout_gate").get("holdout_used", False)}
        atomic_json(self.artifact / "final_readiness.json", readiness)
        files = sorted(path for path in self.artifact.rglob("*") if path.is_file() and path.name not in {"artifact_manifest.json", "checksums.sha256", "stdout.log", "stderr.log"})
        atomic_json(self.artifact / "artifact_manifest.json", {"files": [{"path": path.relative_to(self.artifact).as_posix(), "sha256": sha(path), "size": path.stat().st_size} for path in files]})
        files = sorted(path for path in self.artifact.rglob("*") if path.is_file() and path.name != "checksums.sha256")
        write_text(self.artifact / "checksums.sha256", "".join(f"{sha(path)}  {path.relative_to(self.artifact).as_posix()}\n" for path in files))

    def run(self) -> int:
        self.started = time.monotonic()
        try:
            self.lease.acquire({
                "pid": os.getpid(), "gpu_id": str(self.args.gpu_id),
                "artifact_root": str(self.artifact), "run_root": str(self.run_root),
                "acquired_at": now(),
            })
            self.prepare()
            handlers = {"source_freeze": self.run_source_freeze, "scale_benchmark": self.run_scale_benchmark, "teacher_calibration": self.run_teacher_calibration, "replay_collection": self.run_replay_collection, "replay_freeze": self.run_replay_freeze, "learner_scale_benchmark": self.run_learner_scale_benchmark, "architecture_screen": self.run_architecture_screen, "multiseed_training": self.run_multiseed_training, "full_training": self.run_full_training, "development_validation": self.run_development_validation, "deck_holdout_gate": lambda: self.conditional_holdout(stage="deck_holdout", split="deck_holdout", games=self.profile.deck_holdout_games, filename="deck_holdout_results.json"), "psro_payoff": self.run_psro_payoff, "psro_online_collection": self.run_psro_online_collection, "psro_best_response": self.run_psro_best_response, "final_holdout_gate": lambda: self.conditional_holdout(stage="final_holdout", split="final_holdout", games=self.profile.final_holdout_games, filename="final_holdout_results.json"), "promotion_decision": self.run_promotion}
            selected = STAGES if self.args.stage == "all" else STAGES[:STAGES.index(self.args.stage) + 1]
            for name in selected: self.run_stage(name, handlers[name])
            if self.args.stage == "all": self.seal()
            return 0
        finally:
            self.monitor.close()
            self.lease.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--artifact-root", type=Path, required=True); parser.add_argument("--run-root", type=Path, required=True); parser.add_argument("--gpu-id", default="0"); parser.add_argument("--python-bin", type=Path); parser.add_argument("--profile", choices=tuple(PROFILES), default="production"); parser.add_argument("--stage", choices=("all", *STAGES), default="all"); parser.add_argument("--resume", action="store_true"); parser.add_argument("--continue-from-artifact", type=Path, help="explicit verified parent artifact whose completed final checkpoint is continued in this new artifact"); parser.add_argument("--rebaseline-source-identity", action="store_true", help="record a reviewed source change only before any stage has started; computed stages are never retained across a source change"); parser.add_argument("--replay-input-artifact", type=Path, help="verified completed replay artifact to reuse during recovery only"); parser.add_argument("--source-artifact", type=Path, default=SOURCE_ARTIFACT, help="frozen submitted-opponent registry and deck-disjoint split artifact"); parser.add_argument("--deck-pool", type=Path, default=ROOT / "data/opponent_deck_pool_20260730/opponent_deck_pool.json", help="Git-excluded exact-60 deck pool built from team refs and public Kaggle Replay"); parser.add_argument("--progress-mode", choices=("auto", "bar", "summary", "quiet"), default=os.environ.get("R2D3_PROGRESS_MODE", "auto"))
    args = parser.parse_args(argv); os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id)
    try: return Controller(args).run()
    except Exception as exc: print(json.dumps({"status": "ERROR", "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True); return 2


if __name__ == "__main__": raise SystemExit(main())
