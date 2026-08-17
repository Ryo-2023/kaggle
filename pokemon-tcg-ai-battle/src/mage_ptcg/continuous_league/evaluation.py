"""再開可能な league evaluator と統計集計。"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .benchmark import BenchmarkManifest, ExposureSnapshot, ScheduledGame, build_schedule
from .catalog import CatalogEntry, CatalogSnapshot
from .contracts import (
    LeagueContractError,
    append_jsonl_once,
    atomic_write_json,
    content_id,
    utc_now,
)


GameRunner = Callable[[ScheduledGame, CatalogEntry], Mapping[str, Any]]


class _EvaluationProgress:
    """TTY は単一バー、非TTY は約10秒ごとの集約状態だけを出す。"""

    def __init__(self, *, total: int, initial: int) -> None:
        self.total = total
        self.completed = initial
        self.initial = initial
        self.started = time.monotonic()
        self.last_snapshot = self.started
        self.bar: Any | None = None
        if sys.stderr.isatty():
            try:
                from tqdm import tqdm

                self.bar = tqdm(
                    total=total,
                    initial=initial,
                    desc="checkpoint benchmark",
                    unit="game",
                    dynamic_ncols=True,
                )
            except ImportError:
                self.bar = None

    def update(self, *, faults: int) -> None:
        self.completed += 1
        if self.bar is not None:
            self.bar.update(1)
            self.bar.set_postfix(faults=faults, refresh=False)
            return
        now = time.monotonic()
        if now - self.last_snapshot < 10.0 and self.completed != self.total:
            return
        elapsed = max(now - self.started, 1e-9)
        rate = (self.completed - self.initial) / elapsed
        remaining = self.total - self.completed
        print(
            "stage=checkpoint-benchmark "
            f"completed={self.completed}/{self.total} rate={rate:.3f}/s "
            f"eta_seconds={remaining / rate:.1f} faults={faults}"
            if rate
            else (
                "stage=checkpoint-benchmark "
                f"completed={self.completed}/{self.total} rate=0.000/s "
                f"eta_seconds=unknown faults={faults}"
            ),
            file=sys.stderr,
            flush=True,
        )
        self.last_snapshot = now

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


@dataclass(frozen=True, slots=True)
class EvaluationJob:
    benchmark_id: str
    runtime_policy_id: str
    exposure_snapshot_id: str
    evaluation_job_id: str

    @classmethod
    def build(
        cls,
        benchmark: BenchmarkManifest,
        runtime_policy_id: str,
        exposure: ExposureSnapshot,
    ) -> "EvaluationJob":
        identity = {
            "benchmark_id": benchmark.benchmark_id,
            "runtime_policy_id": runtime_policy_id,
            "exposure_snapshot_id": exposure.exposure_snapshot_id,
        }
        return cls(**identity, evaluation_job_id=content_id("evaluation-job-v1", identity))


def _wilson_interval(successes: float, total: int, z: float = 1.95996398454) -> list[float]:
    if total <= 0:
        return [0.0, 1.0]
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = proportion + z * z / (2.0 * total)
    radius = z * math.sqrt(
        proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)
    )
    return [
        max(0.0, (centre - radius) / denominator),
        min(1.0, (centre + radius) / denominator),
    ]


def _record_score(record: Mapping[str, Any]) -> float:
    outcome = record["outcome"]
    if outcome == "win":
        return 1.0
    if outcome == "draw":
        return 0.5
    if outcome == "loss":
        return 0.0
    raise LeagueContractError(f"unknown game outcome: {outcome}")


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wins = sum(record["outcome"] == "win" for record in records)
    losses = sum(record["outcome"] == "loss" for record in records)
    draws = sum(record["outcome"] == "draw" for record in records)
    total = wins + losses + draws
    score = wins + 0.5 * draws
    return {
        "games": total,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "score_rate": score / total if total else None,
        "wilson_95": _wilson_interval(score, total),
    }


def aggregate_records(
    records: Sequence[Mapping[str, Any]],
    *,
    catalog: CatalogSnapshot,
    exposure: ExposureSnapshot,
) -> dict[str, Any]:
    faults = [record for record in records if record.get("fault")]
    valid = [record for record in records if not record.get("fault")]
    per_opponent: dict[str, dict[str, Any]] = {}
    for opponent_id in sorted({record["opponent_instance_id"] for record in valid}):
        subset = [
            record for record in valid if record["opponent_instance_id"] == opponent_id
        ]
        entry = catalog.get_instance(opponent_id)
        per_opponent[opponent_id] = {
            **_summary(subset),
            "asset_id": entry.asset_id,
            "deck_id": entry.deck_id,
            "policy_id": entry.policy_id,
            "archetype_id": entry.effective_archetype_id,
            "exposure_cohort": exposure.classify(entry).value,
        }
    per_cohort: dict[str, dict[str, Any]] = {}
    for opponent_id, opponent_summary in per_opponent.items():
        cohort = opponent_summary["exposure_cohort"]
        subset = [
            record for record in valid if record["opponent_instance_id"] == opponent_id
        ]
        per_cohort.setdefault(cohort, {"records": []})["records"].extend(subset)
    per_cohort_output = {
        cohort: _summary(value["records"]) for cohort, value in sorted(per_cohort.items())
    }
    rates = [
        summary["score_rate"]
        for summary in per_opponent.values()
        if summary["score_rate"] is not None
    ]
    return {
        "status": "FAULTED" if faults else "COMPLETE",
        "fault_count": len(faults),
        "game_weighted": _summary(valid),
        "opponent_equal_score_rate": sum(rates) / len(rates) if rates else None,
        "worst_opponent_score_rate": min(rates) if rates else None,
        "per_opponent": per_opponent,
        "per_exposure_cohort": per_cohort_output,
    }


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LeagueContractError(
                    f"corrupt evaluation ledger {path}:{line_number}: {exc}"
                ) from exc
            game_key = record.get("game_key")
            if not game_key or game_key in seen:
                raise LeagueContractError(
                    f"duplicate or missing game_key at {path}:{line_number}"
                )
            seen.add(game_key)
            records.append(record)
    return records


def run_evaluation(
    *,
    job: EvaluationJob,
    benchmark: BenchmarkManifest,
    catalog: CatalogSnapshot,
    exposure: ExposureSnapshot,
    output_dir: Path,
    run_game: GameRunner,
    max_games: int | None = None,
) -> dict[str, Any]:
    if job.benchmark_id != benchmark.benchmark_id:
        raise LeagueContractError("evaluation job benchmark mismatch")
    if job.exposure_snapshot_id != exposure.exposure_snapshot_id:
        raise LeagueContractError("evaluation job exposure mismatch")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "games.jsonl"
    schedule = build_schedule(benchmark, job.runtime_policy_id)
    scheduled_by_key = {game.game_key: game for game in schedule}
    existing = _read_ledger(ledger_path)
    for record in existing:
        game = scheduled_by_key.get(record["game_key"])
        if game is None:
            raise LeagueContractError("evaluation ledger contains an alien game key")
        for field, value in game.to_dict().items():
            if record.get(field) != value:
                raise LeagueContractError(
                    f"evaluation ledger mismatch for {record['game_key']} field {field}"
                )
    completed = {record["game_key"] for record in existing}
    remaining_budget = max_games
    progress = _EvaluationProgress(total=len(schedule), initial=len(existing))
    fault_count = sum(record.get("fault") is not None for record in existing)
    try:
        for game in schedule:
            if game.game_key in completed:
                continue
            if remaining_budget is not None and remaining_budget <= 0:
                break
            entry = catalog.get_instance(game.opponent_instance_id)
            try:
                raw_result = dict(run_game(game, entry))
                outcome = raw_result.get("outcome")
                if outcome not in {"win", "loss", "draw"}:
                    raise LeagueContractError(
                        "game runner must return outcome win/loss/draw"
                    )
                record = {
                    **game.to_dict(),
                    "outcome": outcome,
                    "duration_seconds": raw_result.get("duration_seconds"),
                    "steps": raw_result.get("steps"),
                    "fault": None,
                    "completed_at": utc_now(),
                }
            except Exception as exc:  # evaluation fault must be persisted, never replaced
                record = {
                    **game.to_dict(),
                    "outcome": None,
                    "duration_seconds": None,
                    "steps": None,
                    "fault": {"type": type(exc).__name__, "message": str(exc)},
                    "completed_at": utc_now(),
                }
            append_jsonl_once(ledger_path, record, "game_key")
            existing.append(record)
            completed.add(game.game_key)
            fault_count += int(record["fault"] is not None)
            progress.update(faults=fault_count)
            if remaining_budget is not None:
                remaining_budget -= 1
    finally:
        progress.close()
    aggregate = aggregate_records(existing, catalog=catalog, exposure=exposure)
    result = {
        "schema_version": 1,
        "evaluation_job_id": job.evaluation_job_id,
        "benchmark_id": benchmark.benchmark_id,
        "runtime_policy_id": job.runtime_policy_id,
        "exposure_snapshot_id": exposure.exposure_snapshot_id,
        "scheduled_games": len(schedule),
        "completed_games": len(existing),
        "is_schedule_complete": len(existing) == len(schedule),
        "aggregate": aggregate,
    }
    result["evaluation_result_id"] = content_id("evaluation-result-v1", result)
    atomic_write_json(output_dir / "result.json", result)
    return result


def _bootstrap_block_deltas(
    candidate: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
    *,
    samples: int = 2_000,
    seed: int = 71_000,
) -> list[float]:
    by_block_candidate: dict[tuple[str, str, str, int], list[float]] = {}
    by_block_baseline: dict[tuple[str, str, str, int], list[float]] = {}
    for source, destination in (
        (candidate, by_block_candidate),
        (baseline, by_block_baseline),
    ):
        for record in source:
            if record.get("fault"):
                continue
            key = (
                record["subject_deck_id"],
                record["opponent_instance_id"],
                record["execution_block"],
                int(record["repetition_index"]),
            )
            destination.setdefault(key, []).append(_record_score(record))
    keys = sorted(set(by_block_candidate).intersection(by_block_baseline))
    if not keys:
        return []
    block_deltas = [
        sum(by_block_candidate[key]) / len(by_block_candidate[key])
        - sum(by_block_baseline[key]) / len(by_block_baseline[key])
        for key in keys
    ]
    generator = random.Random(seed)
    return [
        sum(generator.choice(block_deltas) for _ in block_deltas) / len(block_deltas)
        for _ in range(samples)
    ]


def compare_evaluations(
    candidate_records: Sequence[Mapping[str, Any]],
    baseline_records: Sequence[Mapping[str, Any]],
    *,
    bootstrap_samples: int = 2_000,
    seed: int = 71_000,
    include_block_logistic: bool = True,
) -> dict[str, Any]:
    candidate_valid = [record for record in candidate_records if not record.get("fault")]
    baseline_valid = [record for record in baseline_records if not record.get("fault")]
    candidate_summary = _summary(candidate_valid)
    baseline_summary = _summary(baseline_valid)
    if not candidate_valid or not baseline_valid:
        raise LeagueContractError("candidate and baseline require valid evaluation games")
    delta = candidate_summary["score_rate"] - baseline_summary["score_rate"]
    newcombe = [
        candidate_summary["wilson_95"][0] - baseline_summary["wilson_95"][1],
        candidate_summary["wilson_95"][1] - baseline_summary["wilson_95"][0],
    ]
    bootstrap = sorted(
        _bootstrap_block_deltas(
            candidate_valid,
            baseline_valid,
            samples=bootstrap_samples,
            seed=seed,
        )
    )
    if bootstrap:
        lower_index = max(0, int(0.025 * (len(bootstrap) - 1)))
        upper_index = min(len(bootstrap) - 1, int(0.975 * (len(bootstrap) - 1)))
        bootstrap_interval: list[float] | None = [
            bootstrap[lower_index],
            bootstrap[upper_index],
        ]
    else:
        bootstrap_interval = None
    logistic: dict[str, Any] | None = None
    if include_block_logistic:
        try:
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.linear_model import LogisticRegression

            rows = []
            labels = []
            for candidate_indicator, source in (
                (1.0, candidate_valid),
                (0.0, baseline_valid),
            ):
                for record in source:
                    if record["outcome"] == "draw":
                        continue
                    rows.append(
                        {
                            "candidate": candidate_indicator,
                            f"deck={record['subject_deck_id']}": 1.0,
                            f"opponent={record['opponent_instance_id']}": 1.0,
                            f"seat={record['seat']}": 1.0,
                            f"block={record['execution_block']}": 1.0,
                        }
                    )
                    labels.append(int(record["outcome"] == "win"))
            if len(set(labels)) >= 2:
                vectorizer = DictVectorizer(sparse=False)
                features = vectorizer.fit_transform(rows)
                model = LogisticRegression(
                    solver="liblinear",
                    C=1_000.0,
                    random_state=seed,
                    max_iter=2_000,
                ).fit(features, labels)
                names = list(vectorizer.get_feature_names_out())
                coefficient = float(model.coef_[0][names.index("candidate")])
                logistic = {
                    "status": "AVAILABLE",
                    "candidate_log_odds": coefficient,
                    "candidate_odds_ratio": math.exp(coefficient),
                    "decisive_games": len(labels),
                    "covariates": [
                        "subject_deck",
                        "opponent",
                        "seat",
                        "execution_block",
                    ],
                    "interpretation": "descriptive_fixed_effect_adjustment",
                }
            else:
                logistic = {
                    "status": "UNAVAILABLE_SINGLE_OUTCOME_CLASS",
                    "decisive_games": len(labels),
                }
        except ImportError:
            logistic = {"status": "UNAVAILABLE_SKLEARN"}
    return {
        "candidate": candidate_summary,
        "baseline": baseline_summary,
        "delta_score_rate": delta,
        "newcombe_95": newcombe,
        "block_bootstrap_95": bootstrap_interval,
        "bootstrap_samples": bootstrap_samples,
        "block_logistic": logistic,
    }
