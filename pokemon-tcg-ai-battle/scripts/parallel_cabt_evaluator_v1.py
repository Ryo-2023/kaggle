"""研究専用の並列 CABT evaluator と game-level ledger。

この module は既存の production evaluator、``main.py``、``actor_pool_v1`` を
変更せず、候補の broad arena を高速に測るための閉じた実験用実装である。

設計上の不変条件:

* CABT は spawn process 内で初期化し、各 worker の BLAS/OpenMP/PyTorch
  thread を 1 に固定する。
* ``max_tasks_per_child`` で worker を定期的に再生成する（DEC-024 の既定は
  32 game/worker）。
* 親 process が game ごとの row を temporary file + fsync + ``os.replace`` で
  公開する。部分 JSON、共有 append race、重複 game id は許可しない。
* DONE 以外、runner exception、worker crash、timeout はすべて ``fault`` row
  として requested-game 分母へ残す。fault を分母から捨てて勝率を膨らませない。
* policy/deck/opponent/seat/block/SHA/steps/runtime を各 row と manifest に保存する。

実 CABT へ接続する既定 runner は ``run_cabt_game_v1`` で、単純な agent name
（``scripts.test_sim.run_match``の契約）を使う。V4/checkpointなどの候補は、
同じ payload 契約を受ける importable ``module:function`` runner を指定する。
これは research-only の接続点であり、V4 production runtime をここへ複製しない。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib
import json
import math
import os
import signal
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PARALLEL_CABT_EVALUATOR_SCHEMA_V1 = "meta-specialist-parallel-cabt-evaluator-v1"
DEFAULT_MAX_WORKERS_V1 = 12
DEFAULT_WORKER_RECYCLE_GAMES_V1 = 16
DEFAULT_TIMEOUT_SECONDS_V1 = 600.0
_SHA256_HEX = frozenset("0123456789abcdef")
_THREAD_ENV_V1 = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
}


class ParallelCabtEvaluatorError(ValueError):
    """Raised when an evaluation spec or ledger violates the closed contract."""


class _GameTimeout(RuntimeError):
    """Raised in a worker when its cooperative SIGALRM deadline fires."""


def _require_text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ParallelCabtEvaluatorError(f"{name} must be a non-empty string")
    return value


def _require_sha256(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in _SHA256_HEX for char in value)
    ):
        raise ParallelCabtEvaluatorError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _require_nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ParallelCabtEvaluatorError(f"{name} must be a nonnegative integer")
    return value


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ParallelCabtEvaluatorError(f"{name} must be a positive integer")
    return value


def _require_positive_float(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ParallelCabtEvaluatorError(f"{name} must be a finite positive number")
    if float(value) <= 0.0:
        raise ParallelCabtEvaluatorError(f"{name} must be a finite positive number")
    return float(value)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class EvaluationGameV1:
    """One immutable game cell in an evaluation block.

    ``policy_sha256`` is the subject policy/checkpoint identity.  The opponent
    identity mapping is retained as a bounded public descriptor and must at
    least contain the identity fields used by the caller; no hidden game state
    is accepted or derived here.
    """

    game_id: str
    block_id: str
    policy_id: str
    policy_sha256: str
    deck_id: str
    deck_sha256: str
    opponent_id: str
    opponent_identity: Mapping[str, object]
    opponent_deck_sha256: str
    seat: int
    seed: int
    max_steps: int
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS_V1
    subject_deck_path: str = ""
    opponent_deck_path: str = ""
    policy_agent_name: str = "deterministic"
    opponent_agent_name: str = "deterministic"
    runner_ref: str = "scripts.parallel_cabt_evaluator_v1:run_cabt_game_v1"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "game_id", "block_id", "policy_id", "deck_id", "opponent_id",
            "runner_ref",
        ):
            _require_text(getattr(self, name), name)
        _require_sha256(self.policy_sha256, "policy_sha256")
        _require_sha256(self.deck_sha256, "deck_sha256")
        _require_sha256(self.opponent_deck_sha256, "opponent_deck_sha256")
        if not isinstance(self.opponent_identity, Mapping):
            raise ParallelCabtEvaluatorError("opponent_identity must be a mapping")
        if type(self.seat) is not int or self.seat not in (0, 1):
            raise ParallelCabtEvaluatorError("seat must be 0 or 1")
        _require_nonnegative_int(self.seed, "seed")
        _require_positive_int(self.max_steps, "max_steps")
        _require_positive_float(self.timeout_seconds, "timeout_seconds")
        if not isinstance(self.metadata, Mapping):
            raise ParallelCabtEvaluatorError("metadata must be a mapping")

    def to_payload(self) -> dict[str, object]:
        """Return only spawn/pickle-safe primitive values."""
        return {
            "game_id": self.game_id,
            "block_id": self.block_id,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "deck_id": self.deck_id,
            "deck_sha256": self.deck_sha256,
            "opponent_id": self.opponent_id,
            "opponent_identity": dict(self.opponent_identity),
            "opponent_deck_sha256": self.opponent_deck_sha256,
            "seat": self.seat,
            "seed": self.seed,
            "max_steps": self.max_steps,
            "timeout_seconds": self.timeout_seconds,
            "subject_deck_path": self.subject_deck_path,
            "opponent_deck_path": self.opponent_deck_path,
            "policy_agent_name": self.policy_agent_name,
            "opponent_agent_name": self.opponent_agent_name,
            "runner_ref": self.runner_ref,
            "metadata": dict(self.metadata),
        }


def _game_from_payload(payload: Mapping[str, object]) -> EvaluationGameV1:
    if not isinstance(payload, Mapping):
        raise ParallelCabtEvaluatorError("game payload must be a mapping")
    return EvaluationGameV1(
        game_id=payload["game_id"],
        block_id=payload["block_id"],
        policy_id=payload["policy_id"],
        policy_sha256=payload["policy_sha256"],
        deck_id=payload["deck_id"],
        deck_sha256=payload["deck_sha256"],
        opponent_id=payload["opponent_id"],
        opponent_identity=payload["opponent_identity"],
        opponent_deck_sha256=payload["opponent_deck_sha256"],
        seat=payload["seat"],
        seed=payload["seed"],
        max_steps=payload["max_steps"],
        timeout_seconds=payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS_V1),
        subject_deck_path=payload.get("subject_deck_path", ""),
        opponent_deck_path=payload.get("opponent_deck_path", ""),
        policy_agent_name=payload.get("policy_agent_name", "deterministic"),
        opponent_agent_name=payload.get("opponent_agent_name", "deterministic"),
        runner_ref=payload.get(
            "runner_ref", "scripts.parallel_cabt_evaluator_v1:run_cabt_game_v1"
        ),
        metadata=payload.get("metadata", {}),
    )


def _resolve_runner_v1(reference: str) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ParallelCabtEvaluatorError(
            "runner_ref must be an importable module:function reference"
        )
    module = importlib.import_module(module_name)
    runner = getattr(module, attribute, None)
    if not callable(runner):
        raise ParallelCabtEvaluatorError(f"runner_ref is not callable: {reference}")
    return runner


def _torch_thread_snapshot_v1() -> dict[str, object]:
    snapshot: dict[str, object] = {"requested": 1}
    try:
        import torch

        snapshot["intra_op"] = int(torch.get_num_threads())
        snapshot["interop"] = int(torch.get_num_interop_threads())
    except Exception as exc:  # pragma: no cover - depends on optional torch runtime
        snapshot["available"] = False
        snapshot["error"] = f"{type(exc).__name__}: {exc}"
    else:
        snapshot["available"] = True
    return snapshot


def _worker_initializer_v1() -> None:
    """Apply DEC-024 thread caps before loading CABT or a neural policy."""
    for key, value in _THREAD_ENV_V1.items():
        os.environ[key] = value
    # Avoid importing torch in the parent.  The environment caps above are
    # applied before this import in a fresh spawn child.
    try:
        import torch

        torch.set_num_threads(1)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            # A policy may have initialized the inter-op pool during import;
            # intra-op remains hard-capped and the observed value is persisted.
            pass
    except Exception:
        # CABT-only runners need not have torch installed.  The requested
        # environment variables still provide the BLAS/OpenMP cap.
        pass


def _alarm_handler_v1(_signum: int, _frame: object) -> None:
    raise _GameTimeout("game exceeded timeout_seconds")


def _invoke_runner_with_timeout_v1(
    runner: Callable[[Mapping[str, object]], Mapping[str, object]],
    payload: Mapping[str, object],
    timeout_seconds: float,
) -> Mapping[str, object]:
    """Run one runner with a worker-local SIGALRM deadline on POSIX."""
    if not hasattr(signal, "SIGALRM"):
        return runner(payload)
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0.0)
    signal.signal(signal.SIGALRM, _alarm_handler_v1)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        result = runner(payload)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0.0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    if not isinstance(result, Mapping):
        raise ParallelCabtEvaluatorError("runner must return a mapping")
    return result


def _worker_run_game_v1(payload: Mapping[str, object]) -> dict[str, object]:
    """spawn child entrypoint; return a bounded raw result, never a shared write."""
    game = _game_from_payload(payload)
    started = time.perf_counter()
    try:
        runner = _resolve_runner_v1(game.runner_ref)
        raw = _invoke_runner_with_timeout_v1(runner, payload, game.timeout_seconds)
    except _GameTimeout as exc:
        return {
            "ok": False,
            "fault_kind": "timeout",
            "fault_detail": str(exc),
            "worker_pid": os.getpid(),
            "worker_runtime_seconds": time.perf_counter() - started,
            "worker_threads": _torch_thread_snapshot_v1(),
        }
    except BaseException as exc:  # noqa: BLE001 - fault is persisted, not hidden
        return {
            "ok": False,
            "fault_kind": "runner_exception",
            "fault_detail": f"{type(exc).__name__}: {exc}",
            "worker_pid": os.getpid(),
            "worker_runtime_seconds": time.perf_counter() - started,
            "worker_threads": _torch_thread_snapshot_v1(),
        }
    return {
        "ok": True,
        "raw": dict(raw),
        "worker_pid": os.getpid(),
        "worker_runtime_seconds": time.perf_counter() - started,
        "worker_threads": _torch_thread_snapshot_v1(),
    }


def run_cabt_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Default runner for simple named agents through the official CABT bridge."""
    from scripts.test_sim import run_match

    game = _game_from_payload(payload)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else game.opponent_deck_path,
        deck_b_path=game.opponent_deck_path if subject_first else game.subject_deck_path,
        agent_a_name=game.policy_agent_name if subject_first else game.opponent_agent_name,
        agent_b_name=game.opponent_agent_name if subject_first else game.policy_agent_name,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "parallel-cabt-worker" / game.game_id),
        save_html=False,
        save_result=False,
    )


def fixture_runner_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Small deterministic research fixture used by focused evaluator tests."""
    game = _game_from_payload(payload)
    status = str(game.metadata.get("fixture_status", "DONE"))
    if status == "RAISE":
        raise RuntimeError("fixture runner failure")
    if status == "SLEEP":
        time.sleep(0.25)
    if status == "BLOCK":
        signal.signal(signal.SIGALRM, signal.SIG_IGN)
        time.sleep(6.0)
    winner = 0 if game.seed % 2 == 0 else 1
    if status == "DRAW":
        winner = 2
    return {
        "status": status,
        "winner": winner if status == "DONE" else None,
        "steps": 10 + game.seat,
        "elapsed_seconds": 0.01,
        "cabt_turn": 5,
        "terminal_reason": "fixture",
        "engine_seed_supported": False,
    }


def evaluation_implementation_sha256_v1() -> str:
    """Hash the evaluator module and CABT bridge bytes used by the runner."""
    digest = hashlib.sha256(b"parallel-cabt-evaluator-v1\0")
    for path in (Path(__file__).resolve(), _ROOT / "scripts" / "test_sim.py"):
        raw = path.read_bytes()
        digest.update(str(path.relative_to(_ROOT)).encode("utf-8") + b"\0")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def evaluator_implementation_sha256_v1() -> str:
    """Compatibility spelling used by ledger consumers and evidence packs."""
    return evaluation_implementation_sha256_v1()


def _finite_float(value: object) -> float | None:
    if type(value) not in (int, float) or isinstance(value, bool):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _outcome_from_raw_v1(raw: Mapping[str, object], seat: int) -> tuple[str, str, int | None]:
    status = raw.get("status")
    winner = raw.get("winner")
    if status != "DONE":
        return "fault", "non_done", winner if type(winner) is int else None
    if winner == 2:
        return "draw", "done", 2
    if winner == seat:
        return "win", "done", seat
    if winner in (0, 1):
        return "loss", "done", winner
    return "fault", "missing_winner", None


def _build_row_v1(
    game: EvaluationGameV1,
    worker_payload: Mapping[str, object],
    *,
    evaluator_sha256: str,
) -> dict[str, object]:
    started = time.perf_counter()
    common: dict[str, object] = {
        "schema_version": PARALLEL_CABT_EVALUATOR_SCHEMA_V1,
        "game_id": game.game_id,
        "block_id": game.block_id,
        "policy_id": game.policy_id,
        "policy_sha256": game.policy_sha256,
        "deck_id": game.deck_id,
        "deck_sha256": game.deck_sha256,
        "opponent_id": game.opponent_id,
        "opponent_identity": dict(game.opponent_identity),
        "opponent_deck_sha256": game.opponent_deck_sha256,
        "seat": game.seat,
        "seed": game.seed,
        "max_steps": game.max_steps,
        "requested": 1,
        "evaluator_implementation_sha256": evaluator_sha256,
        "engine_seed_supported": False,
        "worker_pid": worker_payload.get("worker_pid"),
        "worker_threads": worker_payload.get("worker_threads", {}),
        "worker_runtime_seconds": _finite_float(worker_payload.get("worker_runtime_seconds")),
        "metadata": dict(game.metadata),
    }
    if worker_payload.get("ok") is not True:
        fault_kind = str(worker_payload.get("fault_kind", "worker_failure"))
        fault_detail = str(worker_payload.get("fault_detail", "worker failed without detail"))
        common.update({
            "status": "FAULT",
            "raw_status": None,
            "outcome": "fault",
            "winner": None,
            "steps": None,
            "cabt_turn": None,
            "runtime_seconds": _finite_float(worker_payload.get("worker_runtime_seconds")),
            "terminal_reason": fault_detail,
            "fault_kind": fault_kind,
            "fault_detail": fault_detail,
        })
        return common

    raw = worker_payload.get("raw")
    if not isinstance(raw, Mapping):
        common.update({
            "status": "FAULT",
            "raw_status": None,
            "outcome": "fault",
            "winner": None,
            "steps": None,
            "cabt_turn": None,
            "runtime_seconds": _finite_float(worker_payload.get("worker_runtime_seconds")),
            "terminal_reason": "worker returned no result mapping",
            "fault_kind": "malformed_worker_result",
            "fault_detail": "worker returned no result mapping",
        })
        return common

    outcome, fault_kind, winner = _outcome_from_raw_v1(raw, game.seat)
    status = "DONE" if outcome in {"win", "draw", "loss"} else "FAULT"
    runtime = _finite_float(raw.get("elapsed_seconds"))
    if runtime is None:
        runtime = _finite_float(worker_payload.get("worker_runtime_seconds"))
    common.update({
        "status": status,
        "raw_status": raw.get("status"),
        "outcome": outcome,
        "winner": winner,
        "steps": raw.get("steps") if type(raw.get("steps")) is int else None,
        "cabt_turn": raw.get("cabt_turn") if type(raw.get("cabt_turn")) is int else None,
        "runtime_seconds": runtime if runtime is not None else time.perf_counter() - started,
        "terminal_reason": raw.get("terminal_reason"),
        "fault_kind": None if status == "DONE" else fault_kind,
        "fault_detail": None if status == "DONE" else str(raw.get("terminal_reason", "non-DONE CABT result")),
    })
    for key in ("remaining_prizes", "prizes_remaining", "reward", "rewards"):
        if key in raw:
            common[key] = raw[key]
    return common


def _atomic_write_json_v1(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _atomic_write_bytes_v1(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def aggregate_ledger_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate game rows without removing faults from the requested denominator."""
    requested = len(rows)
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    statuses = Counter(str(row.get("raw_status") or row.get("status") or "UNKNOWN") for row in rows)
    wins = outcomes.get("win", 0)
    draws = outcomes.get("draw", 0)
    losses = outcomes.get("loss", 0)
    faults = outcomes.get("fault", 0)
    return {
        "requested_games": requested,
        "completed_games": wins + draws + losses,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "faults": faults,
        "fault_rate": faults / requested if requested else None,
        "score_rate": (wins + 0.5 * draws) / requested if requested else None,
        "score_denominator_games": requested,
        "status_distribution": dict(sorted(statuses.items())),
        "outcome_distribution": dict(sorted(outcomes.items())),
    }


def _fault_row_for_unresolved_v1(
    game: EvaluationGameV1,
    *,
    evaluator_sha256: str,
    fault_kind: str,
    detail: str,
) -> dict[str, object]:
    return _build_row_v1(
        game,
        {
            "ok": False,
            "fault_kind": fault_kind,
            "fault_detail": detail,
            "worker_pid": None,
            "worker_runtime_seconds": None,
            "worker_threads": {},
        },
        evaluator_sha256=evaluator_sha256,
    )


def run_parallel_cabt_evaluation(
    games: Sequence[EvaluationGameV1],
    *,
    output_dir: str | Path,
    max_workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
    overwrite: bool = False,
    progress: Callable[[Mapping[str, object]], None] | None = None,
) -> dict[str, object]:
    """Evaluate independent game cells and persist an atomic ledger.

    The runner is intentionally injected through ``EvaluationGameV1.runner_ref``
    rather than a closure: a spawn child must be able to resolve it from a
    stable module path, and the payload itself becomes part of the provenance.
    ``max_workers=1`` is the serial smoke mode; it uses the same worker contract
    and therefore compares aggregate schemas rather than promising CABT game
    pairing (the engine has no common-RNG setter).
    """
    if type(max_workers) is not int or max_workers <= 0:
        raise ParallelCabtEvaluatorError("max_workers must be a positive integer")
    if type(worker_recycle_games) is not int or worker_recycle_games <= 0:
        raise ParallelCabtEvaluatorError("worker_recycle_games must be a positive integer")
    if not isinstance(games, Sequence) or not games:
        raise ParallelCabtEvaluatorError("games must be a non-empty sequence")
    normalized_games = tuple(
        game if isinstance(game, EvaluationGameV1) else _game_from_payload(game)
        for game in games
    )
    ids = [game.game_id for game in normalized_games]
    if len(set(ids)) != len(ids):
        raise ParallelCabtEvaluatorError("duplicate game_id in evaluation block")
    destination = Path(output_dir)
    games_dir = destination / "games"
    if destination.exists() and not overwrite and any(destination.iterdir()):
        raise FileExistsError(f"evaluation output is not empty: {destination}")
    if overwrite and destination.exists():
        for child in destination.iterdir():
            if child.is_dir():
                import shutil

                shutil.rmtree(child)
            else:
                child.unlink()
    destination.mkdir(parents=True, exist_ok=True)
    evaluator_sha256 = evaluation_implementation_sha256_v1()
    manifest: dict[str, object] = {
        "schema_version": PARALLEL_CABT_EVALUATOR_SCHEMA_V1,
        "evaluator_implementation_sha256": evaluator_sha256,
        "max_workers": max_workers,
        "worker_recycle_games": worker_recycle_games,
        "max_in_flight_games": min(max_workers, len(normalized_games)),
        "start_method": "spawn",
        "thread_environment": dict(_THREAD_ENV_V1),
        "torch_threads_requested": {"intra_op": 1, "interop": 1},
        "engine_seed_supported": False,
        "pairing": "independent_stratified_not_game_paired",
        "requested_games": len(normalized_games),
        "game_ids": ids,
        "block_ids": sorted({game.block_id for game in normalized_games}),
    }
    _atomic_write_json_v1(destination / "manifest.json", manifest)

    rows_by_id: dict[str, dict[str, object]] = {}
    pending: dict[concurrent.futures.Future[dict[str, object]], EvaluationGameV1] = {}
    timed_out_or_unresolved: set[str] = set()
    executor: concurrent.futures.ProcessPoolExecutor | None = None
    had_pool_failure = False
    last_progress_write = 0.0

    def write_progress_v1() -> None:
        """Persist a bounded non-terminal snapshot for long arenas.

        The final ledger is intentionally written only after every requested
        game has a row.  A separate atomic snapshot makes a broad native-asset
        race observable without making a partial ledger look complete.
        """
        nonlocal last_progress_write
        now = time.monotonic()
        completed = len(rows_by_id)
        if completed and completed != len(normalized_games) and now - last_progress_write < 1.0:
            return
        outcomes = Counter(str(row.get("outcome", "fault")) for row in rows_by_id.values())
        payload = {
            "schema_version": f"{PARALLEL_CABT_EVALUATOR_SCHEMA_V1}-progress",
            "requested_games": len(normalized_games),
            "completed_rows": completed,
            "remaining_games": len(normalized_games) - completed,
            "wins": outcomes.get("win", 0),
            "draws": outcomes.get("draw", 0),
            "losses": outcomes.get("loss", 0),
            "faults": outcomes.get("fault", 0),
            "last_game_id": next(reversed(rows_by_id), None),
            "updated_monotonic_seconds": now,
        }
        _atomic_write_json_v1(destination / "progress_summary.json", payload)
        last_progress_write = now
    try:
        context = __import__("multiprocessing").get_context("spawn")
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=context,
            initializer=_worker_initializer_v1,
            max_tasks_per_child=worker_recycle_games,
        )
        submitted_at: dict[concurrent.futures.Future[dict[str, object]], float] = {}
        next_game_index = 0

        def submit_next_game_v1() -> None:
            """Keep the parent queue bounded to the number of workers.

            A previous implementation submitted the complete block at once.
            The parent watchdog then measured queued futures from submit time,
            so a long block could mark games that had not started as
            ``parent_timeout``.  Bounded submission makes submit time a close
            upper bound for worker start time while retaining spawn/recycle.
            """
            nonlocal next_game_index
            if next_game_index >= len(normalized_games):
                return
            game = normalized_games[next_game_index]
            next_game_index += 1
            future = executor.submit(_worker_run_game_v1, game.to_payload())
            pending[future] = game
            submitted_at[future] = time.monotonic()

        for _ in range(min(max_workers, len(normalized_games))):
            submit_next_game_v1()
        while pending:
            done, _ = concurrent.futures.wait(
                tuple(pending), timeout=0.25, return_when=concurrent.futures.FIRST_COMPLETED
            )
            if not done:
                # Worker-local SIGALRM is the normal timeout path.  This outer
                # guard prevents a broken native engine from blocking forever;
                # rows are marked fault and the pool is shut down fail-closed.
                for future, game in list(pending.items()):
                    if time.monotonic() - submitted_at[future] > game.timeout_seconds + 5.0:
                        if game.game_id in rows_by_id:
                            continue
                        timed_out_or_unresolved.add(game.game_id)
                        rows_by_id[game.game_id] = _fault_row_for_unresolved_v1(
                            game,
                            evaluator_sha256=evaluator_sha256,
                            fault_kind="parent_timeout",
                            detail="parent watchdog exceeded game timeout grace",
                        )
                        # The timed-out future may still be running in a native
                        # child, but its requested game has a terminal fault
                        # row now.  Keep the bounded queue moving so one hung
                        # game cannot silently drop every game behind it.
                        future.cancel()
                        pending.pop(future, None)
                        submitted_at.pop(future, None)
                        write_progress_v1()
                        if progress is not None:
                            progress(rows_by_id[game.game_id])
                        submit_next_game_v1()
                        break
                continue
            for future in done:
                game = pending.pop(future)
                submitted_at.pop(future, None)
                if game.game_id in rows_by_id:
                    continue
                try:
                    worker_payload = future.result()
                except BaseException as exc:  # noqa: BLE001 - preserve crash row
                    had_pool_failure = True
                    worker_payload = {
                        "ok": False,
                        "fault_kind": "worker_crash",
                        "fault_detail": f"{type(exc).__name__}: {exc}",
                        "worker_pid": None,
                        "worker_runtime_seconds": None,
                        "worker_threads": {},
                    }
                row = _build_row_v1(
                    game, worker_payload, evaluator_sha256=evaluator_sha256
                )
                rows_by_id[game.game_id] = row
                write_progress_v1()
                if progress is not None:
                    progress(row)
                submit_next_game_v1()
        if pending:
            for future, game in list(pending.items()):
                if game.game_id not in rows_by_id:
                    rows_by_id[game.game_id] = _fault_row_for_unresolved_v1(
                        game,
                        evaluator_sha256=evaluator_sha256,
                        fault_kind="pool_unresolved",
                        detail="worker pool ended without a result",
                    )
                future.cancel()
            pending.clear()
    except BaseException:
        had_pool_failure = True
        for future, game in list(pending.items()):
            if game.game_id not in rows_by_id:
                rows_by_id[game.game_id] = _fault_row_for_unresolved_v1(
                    game,
                    evaluator_sha256=evaluator_sha256,
                    fault_kind="pool_exception",
                    detail="executor failed before game result was available",
                )
            future.cancel()
        pending.clear()
        raise
    finally:
        if executor is not None:
            executor.shutdown(wait=not (had_pool_failure or timed_out_or_unresolved), cancel_futures=True)

    ordered_rows = [rows_by_id[game.game_id] for game in normalized_games]
    for row in ordered_rows:
        _atomic_write_json_v1(games_dir / f"{row['game_id']}.json", row)
    ledger_raw = b"".join(
        (_canonical_json(row) + "\n").encode("utf-8") for row in ordered_rows
    )
    _atomic_write_bytes_v1(destination / "ledger.jsonl", ledger_raw)
    summary = aggregate_ledger_v1(ordered_rows)
    summary["evaluator_implementation_sha256"] = evaluator_sha256
    summary["runtime_seconds_total"] = sum(
        float(row["runtime_seconds"])
        for row in ordered_rows
        if type(row.get("runtime_seconds")) in (int, float)
    )
    _atomic_write_json_v1(destination / "summary.json", summary)
    manifest["completed_games"] = summary["completed_games"]
    manifest["faults"] = summary["faults"]
    manifest["status_distribution"] = summary["status_distribution"]
    manifest["pool_failure_observed"] = had_pool_failure
    _atomic_write_json_v1(destination / "manifest.json", manifest)
    return {
        "manifest": manifest,
        "summary": summary,
        "rows": ordered_rows,
        "output_dir": str(destination.resolve()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = json.loads(args.games_json.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("--games-json must contain a list of game objects")
    result = run_parallel_cabt_evaluation(
        tuple(_game_from_payload(item) for item in payload),
        output_dir=args.output,
        max_workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
        overwrite=args.overwrite,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MAX_WORKERS_V1",
    "DEFAULT_TIMEOUT_SECONDS_V1",
    "DEFAULT_WORKER_RECYCLE_GAMES_V1",
    "EvaluationGameV1",
    "PARALLEL_CABT_EVALUATOR_SCHEMA_V1",
    "ParallelCabtEvaluatorError",
    "aggregate_ledger_v1",
    "evaluator_implementation_sha256_v1",
    "evaluation_implementation_sha256_v1",
    "fixture_runner_v1",
    "run_cabt_game_v1",
    "run_parallel_cabt_evaluation",
]
