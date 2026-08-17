#!/usr/bin/env python3
"""Generate a portfolio-robust research-only adversarial meta source.

Unlike the original source-side CEM, the generated source is evaluated against
several fixed policy/deck references.  The source objective uses terminal WDL
only and ranks the mean/worst reference score, preventing one P1-specific
matchup from becoming the only meta signal.  This runner never mutates the
production pool, BestKnown, Champion, submission package, or Kaggle state.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import (  # noqa: E402
    AUTHORITY_FALSE_V1,
    build_fresh_meta_batch_v1,
)
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import (  # noqa: E402
    P1ParameterConfig,
    sample_population,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
)
from mage_ptcg.meta_specialist.resource_governor_v1 import (  # noqa: E402
    ResourceBudget,
    ResourceGovernor,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256  # noqa: E402
from mage_ptcg.opponent_ingest.adversarial_source_cem_v1 import (  # noqa: E402
    AdversarialSourceError,
    materialize_adversarial_source_package,
)
from mage_ptcg.opponent_ingest.robust_adversarial_source_cem_v1 import (  # noqa: E402
    SCHEMA_V1 as ROBUST_SOURCE_SCHEMA_V1,
    RobustAdversarialSourceError,
    aggregate_portfolio_source_rows_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


CAMPAIGN_SCHEMA_V1 = "meta-specialist-robust-adversarial-source-cem-campaign-v1"
SOURCE_V1 = "self_owned_robust_adversarial_source_cem"
DEFAULT_P1_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DEFAULT_BUDGET = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
DEFAULT_OUTPUT = _ROOT / "runs/cg-robust-adversarial-source-cem-20260816-a"
DEFAULT_REFERENCE_SPECS = (
    "p1-reference-v1=runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package",
    "rule-v0-reference-v1=runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck",
    "balanced-independent-v1=runs/cg-self-owned-independent-root-policy-family-v1-20260816/p1-controls/balanced-independent-v1-00",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    reference_id: str
    package_root: Path
    source: str


class RobustSourceCampaignError(ValueError):
    """Raised when a portfolio source campaign cannot be sealed safely."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RobustSourceCampaignError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_json_new(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _deck_hash(path: Path) -> str:
    try:
        cards = [int(token) for token in path.read_text(encoding="utf-8").split()]
    except (OSError, UnicodeError, ValueError) as exc:
        raise RobustSourceCampaignError(f"cannot parse deck: {path}") from exc
    if len(cards) != 60:
        raise RobustSourceCampaignError(f"deck must contain exactly 60 cards: {path}")
    return canonical_deck_sha256(cards)


def _parse_reference(value: str) -> ReferenceSpec:
    if "=" not in value:
        raise RobustSourceCampaignError("reference must be ID=PACKAGE_ROOT")
    reference_id, raw_path = value.split("=", 1)
    if _ID.fullmatch(reference_id) is None:
        raise RobustSourceCampaignError(f"invalid reference id: {reference_id!r}")
    package = Path(raw_path)
    if not package.is_absolute():
        package = _ROOT / package
    package = package.resolve()
    return ReferenceSpec(reference_id=reference_id, package_root=package, source=f"fixed_reference:{reference_id}")


def _load_initial_config(path: Path | None) -> P1ParameterConfig:
    """Load a sealed source-CEM center from a raw or wrapped JSON mapping."""

    if path is None:
        return P1ParameterConfig.default()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RobustSourceCampaignError(f"cannot read initial config: {path}") from exc
    if isinstance(raw, Mapping) and isinstance(raw.get("config"), Mapping):
        raw = raw["config"]
    elif isinstance(raw, Mapping) and isinstance(raw.get("initial_config"), Mapping):
        raw = raw["initial_config"]
    if not isinstance(raw, Mapping):
        raise RobustSourceCampaignError("initial config must be a JSON mapping")
    try:
        config = P1ParameterConfig.from_mapping(raw)
        config.validate()
    except (TypeError, ValueError) as exc:
        raise RobustSourceCampaignError("initial config does not match P1 parameter surface") from exc
    return config


def _copy_package(source: Path, target: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise RobustSourceCampaignError(f"reference package is not a regular directory: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RobustSourceCampaignError(f"reference package contains symlink: {path}")
    if target.exists():
        raise RobustSourceCampaignError(f"reference target already exists: {target}")
    shutil.copytree(source, target)
    for name in ("main.py", "deck.csv"):
        if not (target / name).is_file():
            raise RobustSourceCampaignError(f"reference package is incomplete: {source}")


def _make_reference_pool(root: Path, specs: Sequence[ReferenceSpec]) -> tuple[Path, tuple[str, ...], list[dict[str, object]]]:
    if not specs or len({spec.reference_id for spec in specs}) != len(specs):
        raise RobustSourceCampaignError("reference ids must be non-empty and unique")
    pool_root = root / "reference_pool"
    pool_root.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    for spec in sorted(specs, key=lambda item: item.reference_id):
        target = pool_root / spec.reference_id
        _copy_package(spec.package_root, target)
        rows.append(
            {
                "id": spec.reference_id,
                "policy_hash": _sha256(target / "main.py"),
                "canonical_deck_hash": _deck_hash(target / "deck.csv"),
                "source": spec.source,
                "usage_boundary": "local_eval_only",
                "smoke_ok": True,
            }
        )
    _write_json_new(pool_root / "pool_manifest.json", rows)
    return pool_root, tuple(row["id"] for row in rows), rows


def _evaluate(games: Sequence[object], output_dir: Path, *, workers: int) -> dict[str, object]:
    if not games:
        raise RobustSourceCampaignError("source evaluation has no games")
    total = len(games)
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}
    bar = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm

            bar = tqdm(total=total, desc="robust source CEM", unit="game", dynamic_ncols=True)
        except Exception:  # pragma: no cover
            bar = None

    def progress(row: Mapping[str, object]) -> None:
        state["completed"] += 1
        if str(row.get("outcome", "fault")) == "fault":
            state["faults"] += 1
        if bar is not None:
            bar.update(1)
            bar.set_postfix(faults=state["faults"])
            return
        now = time.monotonic()
        if now - state["last_emit"] >= 10.0 or state["completed"] == total:
            print(
                json.dumps(
                    {
                        "stage": "robust_adversarial_source_cem",
                        "completed": state["completed"],
                        "requested": total,
                        "faults": state["faults"],
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            state["last_emit"] = now

    try:
        return run_parallel_cabt_evaluation(
            tuple(games),
            output_dir=output_dir,
            max_workers=workers,
            worker_recycle_games=16,
            overwrite=False,
            progress=progress,
        )
    finally:
        if bar is not None:
            bar.close()


def _candidate_games(*, package: Path, candidate_id: str, pool_root: Path, refs: Sequence[str], base_seed: int, games_per_reference_seat: int, block_id: str) -> tuple[object, ...]:
    arm = arena.ArenaArm(
        arm_id=candidate_id,
        policy_id=candidate_id,
        policy_sha256=_sha256(package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=package,
    )
    return arena._build_games(
        arm=arm,
        refs=refs,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_reference_seat,
        block_id=block_id,
    )


def _smoke_games(*, p1_package: Path, source_package: Path, pool_root: Path, source_id: str, base_seed: int) -> tuple[object, ...]:
    arm = arena.ArenaArm(
        arm_id="p1-smoke-subject",
        policy_id="p1-smoke-subject",
        policy_sha256=_sha256(p1_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=p1_package,
    )
    return arena._build_games(
        arm=arm,
        refs=(source_id,),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=1,
        block_id="robust-source-smoke",
    )


def _source_pool_row(source_id: str, package: Path, *, smoke_ok: bool) -> dict[str, object]:
    return {
        "id": source_id,
        "policy_hash": _sha256(package / "main.py"),
        "canonical_deck_hash": _deck_hash(package / "deck.csv"),
        "source": SOURCE_V1,
        "usage_boundary": "local_eval_only",
        "smoke_ok": smoke_ok,
    }


def _passes_promotion_gate_v1(
    train_aggregate: Mapping[str, object],
    validation_aggregate: Mapping[str, object],
) -> bool:
    """Require both screen and independent validation to be robustly usable."""

    return (
        bool(train_aggregate.get("valid"))
        and bool(validation_aggregate.get("valid"))
        and float(train_aggregate.get("mean_source_score", 0.0)) > 0.5
        and float(validation_aggregate.get("mean_source_score", 0.0)) > 0.5
        and float(validation_aggregate.get("min_reference_score", 0.0)) >= 0.25
        and bool(validation_aggregate.get("seat_safe"))
    )


def _write_fresh_meta(*, root: Path, source_id: str, row: Mapping[str, object], pool_manifest: Path, smoke_summary: Path, seed_plan: Mapping[str, object]) -> Path:
    evidence = {
        "schema_version": ROBUST_SOURCE_SCHEMA_V1,
        "source_id": source_id,
        "source_policy_sha256": row["policy_hash"],
        "canonical_deck_hash": row["canonical_deck_hash"],
        "smoke_summary_path": str(smoke_summary.resolve()),
        "smoke_summary_sha256": _sha256(smoke_summary),
        "fresh": True,
        "unused_before_run": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    evidence_path = root / "freshness-evidence.json"
    _write_json_new(evidence_path, evidence)
    seed_plan_sha = hashlib.sha256(_canonical_json(seed_plan)).hexdigest()
    manifest = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"robust-adversarial-source-{source_id}",
        "source_epoch": "robust_adversarial_source_cem_v1",
        "seed_namespace": f"robust-adversarial-source-{source_id}",
        "seed_plan_sha256": seed_plan_sha,
        "pool_manifest_sha256": _sha256(pool_manifest),
        "reference_ids": [source_id],
        "references": [
            {
                "id": source_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256(evidence_path),
                "freshness_evidence_path": str(evidence_path.resolve()),
                "policy_sha256": row["policy_hash"],
                "canonical_deck_hash": row["canonical_deck_hash"],
                "source": SOURCE_V1,
            }
        ],
        "freshness_basis": "portfolio-robust-source-CEM-positive-independent-validation-and-seat-smoke",
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    path = root / "fresh_meta_manifest.json"
    _write_json_new(path, manifest)
    build_fresh_meta_batch_v1(manifest_path=path, pool_manifest_path=pool_manifest, consumed_ids=(), consumed_seed_namespaces=())
    return path


def run_campaign(
    *,
    p1_package: Path,
    output_root: Path,
    reference_specs: Sequence[ReferenceSpec],
    population_size: int = 8,
    elite_count: int = 2,
    games_per_reference_seat: int = 2,
    validation_games_per_reference_seat: int = 6,
    campaign_seed: int = 2026081691,
    workers: int | None = None,
    budget_path: Path = DEFAULT_BUDGET,
    initial_config: P1ParameterConfig | None = None,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    if population_size < 2 or elite_count < 1 or elite_count > population_size:
        raise RobustSourceCampaignError("invalid population/elite count")
    if games_per_reference_seat < 1 or validation_games_per_reference_seat < 1:
        raise RobustSourceCampaignError("games per reference seat must be positive")
    p1_package = p1_package.resolve()
    if _sha256(p1_package / "main.py") != BASE_SOURCE_SHA256:
        raise RobustSourceCampaignError("P1 package main.py SHA does not match sealed parent")
    output_root.mkdir(parents=True, exist_ok=False)
    budget = ResourceBudget.from_json(budget_path)
    governor = ResourceGovernor(budget)
    decision = governor.decide(task_cap=workers or budget.max_workers, gpu_required=False)
    if decision.state != "normal" or decision.recommended_workers < 1:
        raise RobustSourceCampaignError(f"ResourceGovernor refused source campaign: {decision.to_dict()}")
    worker_count = min(decision.recommended_workers, workers or decision.recommended_workers)
    governor.write_telemetry(output_root / "resource_telemetry.json", task_cap=worker_count, gpu_required=False)

    pool_root, reference_ids, reference_rows = _make_reference_pool(output_root, reference_specs)
    center = initial_config or P1ParameterConfig.default()
    center.validate()
    configs = sample_population(center, generation=0, population_size=population_size, seed=campaign_seed)
    generation_root = output_root / "generation-0000"
    candidate_root = generation_root / "candidates"
    packages: dict[str, Path] = {}
    config_by_id: dict[str, P1ParameterConfig] = {}
    games: list[object] = []
    for index, config in enumerate(configs):
        candidate_id = f"robust-source-g00-c{index:02d}-{config.config_sha256()[:12]}"
        package = candidate_root / f"candidate-{index:02d}" / "package"
        materialize_adversarial_source_package(source_package=p1_package, output_package=package, config=config, candidate_id=candidate_id)
        packages[candidate_id] = package
        config_by_id[candidate_id] = config
        games.extend(
            _candidate_games(
                package=package,
                candidate_id=candidate_id,
                pool_root=pool_root,
                refs=reference_ids,
                base_seed=campaign_seed,
                games_per_reference_seat=games_per_reference_seat,
                block_id=f"robust-source-cem-g000-{candidate_id}",
            )
        )

    _write_json_new(
        generation_root / "manifest.json",
        {
            "schema_version": CAMPAIGN_SCHEMA_V1,
            "source_schema_version": ROBUST_SOURCE_SCHEMA_V1,
            "generation": 0,
            "population_size": population_size,
            "elite_count": elite_count,
            "requested_games": len(games),
            "games_per_reference_seat": games_per_reference_seat,
            "campaign_seed": campaign_seed,
            "reference_ids": list(reference_ids),
            "reference_rows": reference_rows,
            "parent_policy_sha256": BASE_SOURCE_SHA256,
            "initial_config_sha256": center.config_sha256(),
            "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
            "objective": "maximize_mean_and_worst_source_score_over_fixed_portfolio",
            "action_trace_used": False,
            "private_fields_used": False,
            "teacher_labels_used": False,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        },
    )
    evaluation = _evaluate(games, generation_root / "evaluation", workers=worker_count)
    results: list[dict[str, object]] = []
    for candidate_id, config in config_by_id.items():
        aggregate = aggregate_portfolio_source_rows_v1(
            evaluation["rows"],
            candidate_policy_id=candidate_id,
            reference_ids=reference_ids,
            seat_gap_limit=0.25,
        )
        results.append(
            {
                "candidate_id": candidate_id,
                "config": config.as_dict(),
                "config_sha256": config.config_sha256(),
                "policy_sha256": _sha256(packages[candidate_id] / "main.py"),
                "aggregate": aggregate,
                "objective": aggregate["robust_objective"],
                "valid": aggregate["valid"],
                "faults": aggregate["faults"],
            }
        )

    valid_results = [item for item in results if item["valid"] and item["faults"] == 0]
    valid_results.sort(key=lambda item: (-float(item["objective"]), str(item["config_sha256"])))
    elites = valid_results[:elite_count]
    if len(elites) == elite_count:
        new_center, new_scales = update_distribution(
            center,
            [{"config": item["config"], "objective": item["objective"], "faults": item["faults"], "valid": True} for item in elites],
        )
    else:
        new_center, new_scales = center, None

    # Validate every screen elite instead of trusting a single noisy winner.
    # The first elite that passes the independent robust gate is selected for
    # promotion; all validation aggregates remain sealed for auditability.
    validation_candidates: list[dict[str, object]] = []
    selected_elite: dict[str, object] | None = None
    selected_validation: dict[str, object] | None = None
    for rank, elite in enumerate(elites):
        elite_id = str(elite["candidate_id"])
        validation_games = _candidate_games(
            package=packages[elite_id],
            candidate_id=elite_id,
            pool_root=pool_root,
            refs=reference_ids,
            base_seed=campaign_seed + 500_000,
            games_per_reference_seat=validation_games_per_reference_seat,
            block_id=f"robust-source-validation-r{rank:02d}-{elite_id}",
        )
        validation_eval = _evaluate(
            validation_games,
            generation_root / f"validation-{rank:02d}",
            workers=worker_count,
        )
        aggregate = aggregate_portfolio_source_rows_v1(
            validation_eval["rows"],
            candidate_policy_id=elite_id,
            reference_ids=reference_ids,
            seat_gap_limit=0.25,
        )
        promotion_gate = _passes_promotion_gate_v1(elite["aggregate"], aggregate)
        validation_candidates.append(
            {
                "rank": rank,
                "candidate_id": elite_id,
                "aggregate": aggregate,
                "promotion_gate": promotion_gate,
            }
        )
        if selected_elite is None and promotion_gate:
            selected_elite = elite
            selected_validation = aggregate

    # Preserve the historical field for consumers while adding the complete
    # per-elite record below.
    validation = validation_candidates[0]["aggregate"] if validation_candidates else None
    top = selected_elite

    promoted = None
    if top is not None and selected_validation is not None:
        if _passes_promotion_gate_v1(top["aggregate"], selected_validation):
            promoted_root = output_root / "promoted_source_pool"
            promoted_package = promoted_root / top["candidate_id"]
            promoted_root.mkdir(parents=True, exist_ok=False)
            shutil.copytree(packages[str(top["candidate_id"])], promoted_package)
            row = _source_pool_row(str(top["candidate_id"]), promoted_package, smoke_ok=False)
            pool_manifest = promoted_root / "pool_manifest.json"
            _write_json_new(pool_manifest, [row])
            smoke_games = _smoke_games(
                p1_package=p1_package,
                source_package=promoted_package,
                pool_root=promoted_root,
                source_id=str(top["candidate_id"]),
                base_seed=campaign_seed + 900_000,
            )
            smoke_eval = _evaluate(smoke_games, output_root / "promoted_source_smoke", workers=worker_count)
            smoke_rows = smoke_eval["rows"]
            smoke_ok = len(smoke_rows) == len(smoke_games) and all(str(item.get("outcome")) in {"win", "draw", "loss"} for item in smoke_rows)
            row["smoke_ok"] = smoke_ok
            pool_manifest.write_bytes(_canonical_json([row]))
            smoke_summary = output_root / "promoted_source_smoke_summary.json"
            _write_json_new(
                smoke_summary,
                {
                    "source_id": top["candidate_id"],
                    "requested_games": len(smoke_games),
                    "rows": smoke_rows,
                    "faults": sum(1 for item in smoke_rows if item.get("outcome") == "fault"),
                    "smoke_ok": smoke_ok,
                    "research_only": True,
                },
            )
            if smoke_ok:
                fresh_meta = _write_fresh_meta(
                    root=output_root,
                    source_id=str(top["candidate_id"]),
                    row=row,
                    pool_manifest=pool_manifest,
                    smoke_summary=smoke_summary,
                    seed_plan={
                        "screen_base_seed": campaign_seed,
                        "validation_base_seed": campaign_seed + 500_000,
                        "smoke_base_seed": campaign_seed + 900_000,
                        "games_per_reference_seat": games_per_reference_seat,
                        "validation_games_per_reference_seat": validation_games_per_reference_seat,
                        "smoke_games_per_reference_seat": 1,
                    },
                )
                promoted = {
                    "source_id": top["candidate_id"],
                    "pool_root": str(promoted_root.resolve()),
                    "pool_manifest": str(pool_manifest.resolve()),
                    "fresh_meta_manifest": str(fresh_meta.resolve()),
                    "smoke_summary": str(smoke_summary.resolve()),
                    "smoke_ok": True,
                }

    result = {
        "schema_version": CAMPAIGN_SCHEMA_V1,
        "source_schema_version": ROBUST_SOURCE_SCHEMA_V1,
        "status": "COMPLETE",
        "reference_ids": list(reference_ids),
        "reference_rows": reference_rows,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "initial_config_sha256": center.config_sha256(),
        "population_size": population_size,
        "elite_count": elite_count,
        "requested_games": len(games),
        "evaluation_summary": evaluation["summary"],
        "results": results,
        "elites": elites,
        "new_center": new_center.as_dict(),
        "new_scales": new_scales,
        "validation": validation,
        "validation_candidates": validation_candidates,
        "selected_validation_candidate_id": str(top["candidate_id"]) if top is not None else None,
        "promoted": promoted,
        "resource_workers": worker_count,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    _write_json_new(output_root / "campaign_result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p1-package", type=Path, default=DEFAULT_P1_PACKAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reference", action="append", default=None, help="fixed reference as ID=PACKAGE_ROOT; repeatable")
    parser.add_argument("--population-size", type=int, default=8)
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--games-per-reference-seat", type=int, default=2)
    parser.add_argument("--validation-games-per-reference-seat", type=int, default=6)
    parser.add_argument("--campaign-seed", type=int, default=2026081691)
    parser.add_argument("--initial-config-json", type=Path, default=None, help="raw or wrapped P1 parameter mapping used as the source-CEM center")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    args = parser.parse_args(argv)
    try:
        raw_refs = args.reference if args.reference is not None else list(DEFAULT_REFERENCE_SPECS)
        specs = tuple(_parse_reference(value) for value in raw_refs)
        result = run_campaign(
            p1_package=args.p1_package,
            output_root=args.output,
            reference_specs=specs,
            population_size=args.population_size,
            elite_count=args.elite_count,
            games_per_reference_seat=args.games_per_reference_seat,
            validation_games_per_reference_seat=args.validation_games_per_reference_seat,
            campaign_seed=args.campaign_seed,
            workers=args.workers,
            budget_path=args.budget,
            initial_config=_load_initial_config(args.initial_config_json),
        )
    except (RobustSourceCampaignError, RobustAdversarialSourceError, AdversarialSourceError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
