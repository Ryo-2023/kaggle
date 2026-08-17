"""Run a bounded source-side CEM campaign against immutable P1.

This is a research-only source factory.  It searches a sealed P1 score
surface in the opposite direction: candidate policies are evaluated as the
opponent and maximize their terminal WDL score against P1.  A candidate is
usable as a fresh meta source only after independent validation and an
opponent-seat CABT smoke gate.  The script never changes the production pool,
Champion, submission branch, or Kaggle state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
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
    rank_valid_results,
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
    SCHEMA_V1 as SOURCE_SCHEMA_V1,
    SOURCE_V1,
    AdversarialSourceError,
    aggregate_source_rows_v1,
    build_source_pool_row_v1,
    materialize_adversarial_source_package,
    source_candidate_id_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


CAMPAIGN_SCHEMA_V1 = "meta-specialist-adversarial-source-cem-campaign-v1"
DEFAULT_P1_PACKAGE = (
    _ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DEFAULT_BUDGET = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
DEFAULT_OUTPUT = _ROOT / "runs/cg-adversarial-source-cem-20260815-a"
REFERENCE_ID = "p1-reference-v1"
PROMOTED_SOURCE_ID_PREFIX = "adversarial-source"


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise AdversarialSourceError(f"regular file required: {path}")
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
        raise AdversarialSourceError(f"cannot parse deck: {path}") from exc
    if len(cards) != 60:
        raise AdversarialSourceError(f"deck must contain exactly 60 cards: {path}")
    return canonical_deck_sha256(cards)


def _make_reference_pool(root: Path, reference_package: Path) -> tuple[Path, dict[str, object]]:
    """Create an isolated one-reference pool containing the immutable P1."""

    pool_root = root / "reference_pool"
    package_root = pool_root / REFERENCE_ID
    package_root.mkdir(parents=True, exist_ok=False)
    for name in ("main.py", "deck.csv"):
        source = reference_package / name
        if source.is_symlink() or not source.is_file():
            raise AdversarialSourceError(f"reference package is incomplete: {reference_package}")
        shutil.copy2(source, package_root / name)
    policy_sha = _sha256(package_root / "main.py")
    deck_sha = _deck_hash(package_root / "deck.csv")
    row = {
        "id": REFERENCE_ID,
        "policy_hash": policy_sha,
        "canonical_deck_hash": deck_sha,
        "source": "self_owned_p1_reference",
        "usage_boundary": "local_eval_only",
        "smoke_ok": True,
    }
    _write_json_new(pool_root / "pool_manifest.json", [row])
    return pool_root, row


def _evaluate(games: Sequence[object], output_dir: Path, *, workers: int) -> dict[str, object]:
    if not games:
        raise AdversarialSourceError("source evaluation has no games")
    total = len(games)
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}
    bar = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm

            bar = tqdm(total=total, desc="adversarial source CEM", unit="game", dynamic_ncols=True)
        except Exception:  # pragma: no cover - optional display dependency
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
                        "stage": "adversarial_source_cem",
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


def _candidate_games(
    *,
    package: Path,
    candidate_id: str,
    pool_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    block_id: str,
) -> tuple[object, ...]:
    arm = arena.ArenaArm(
        arm_id=candidate_id,
        policy_id=candidate_id,
        policy_sha256=_sha256(package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=package,
    )
    return arena._build_games(
        arm=arm,
        refs=(REFERENCE_ID,),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=block_id,
    )


def _p1_smoke_games(*, p1_package: Path, source_package: Path, pool_root: Path, base_seed: int) -> tuple[object, ...]:
    arm = arena.ArenaArm(
        arm_id="p1-smoke-subject",
        policy_id="p1-smoke-subject",
        policy_sha256=_sha256(p1_package / "main.py"),
        arm_kind="root_cg",
        candidate_package_root=p1_package,
    )
    return arena._build_games(
        arm=arm,
        refs=(source_package.name,),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=1,
        block_id="adversarial-source-smoke",
    )


def _write_fresh_meta_manifest(
    *,
    campaign_root: Path,
    source_id: str,
    source_row: Mapping[str, object],
    pool_manifest: Path,
    smoke_summary: Path,
    seed_namespace: str,
    seed_plan: Mapping[str, object],
) -> Path:
    seed_plan_sha = hashlib.sha256(_canonical_json(seed_plan)).hexdigest()
    evidence = {
        "schema_version": SOURCE_SCHEMA_V1,
        "source_id": source_id,
        "source_row": dict(source_row),
        "smoke_summary_path": str(smoke_summary.resolve()),
        "smoke_summary_sha256": _sha256(smoke_summary),
        "source_policy_sha256": source_row["policy_hash"],
        "canonical_deck_hash": source_row["canonical_deck_hash"],
        "fresh": True,
        "unused_before_run": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    evidence_path = campaign_root / "freshness-evidence.json"
    _write_json_new(evidence_path, evidence)
    manifest = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"adversarial-source-{source_id}",
        "source_epoch": "adversarial_source_cem_v1",
        "seed_namespace": seed_namespace,
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
                "canonical_deck_hash": source_row["canonical_deck_hash"],
                "policy_sha256": source_row["policy_hash"],
                "source": source_row["source"],
            }
        ],
        "freshness_basis": "source-side-CEM-positive-independent-validation-and-opponent-seat-smoke",
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    path = campaign_root / "fresh_meta_manifest.json"
    _write_json_new(path, manifest)
    # Reload the exact verifier before publishing the campaign result.  This
    # fails closed if an identity, path, or freshness field is inconsistent.
    build_fresh_meta_batch_v1(
        manifest_path=path,
        pool_manifest_path=pool_manifest,
        consumed_ids=(),
        consumed_seed_namespaces=(),
    )
    return path


def run_campaign(
    *,
    p1_package: Path,
    output_root: Path,
    population_size: int = 8,
    elite_count: int = 2,
    games_per_opponent_seat: int = 4,
    validation_games_per_opponent_seat: int = 8,
    campaign_seed: int = 20260820,
    workers: int | None = None,
    budget_path: Path = DEFAULT_BUDGET,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    if type(population_size) is not int or population_size < 2:
        raise AdversarialSourceError("population_size must be >= 2")
    if type(elite_count) is not int or elite_count < 1 or elite_count > population_size:
        raise AdversarialSourceError("elite_count must be in [1,population_size]")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat < 1:
        raise AdversarialSourceError("games_per_opponent_seat must be positive")
    if type(validation_games_per_opponent_seat) is not int or validation_games_per_opponent_seat < 1:
        raise AdversarialSourceError("validation_games_per_opponent_seat must be positive")
    if type(campaign_seed) is not int:
        raise AdversarialSourceError("campaign_seed must be an integer")
    p1_package = p1_package.resolve()
    if _sha256(p1_package / "main.py") != BASE_SOURCE_SHA256:
        raise AdversarialSourceError("P1 package main.py SHA does not match sealed parent")
    output_root.mkdir(parents=True, exist_ok=False)
    budget = ResourceBudget.from_json(budget_path)
    governor = ResourceGovernor(budget)
    decision = governor.decide(task_cap=workers or budget.max_workers, gpu_required=False)
    if decision.state != "normal" or decision.recommended_workers < 1:
        raise AdversarialSourceError(f"ResourceGovernor refused source campaign: {decision.to_dict()}")
    worker_count = min(decision.recommended_workers, workers or decision.recommended_workers)
    governor.write_telemetry(output_root / "resource_telemetry.json", task_cap=worker_count, gpu_required=False)

    pool_root, reference_row = _make_reference_pool(output_root, p1_package)
    center = P1ParameterConfig.default()
    configs = sample_population(
        center,
        generation=0,
        population_size=population_size,
        seed=campaign_seed,
    )
    gen_root = output_root / "generation-0000"
    candidate_root = gen_root / "candidates"
    packages: dict[str, Path] = {}
    config_by_id: dict[str, P1ParameterConfig] = {}
    games: list[object] = []
    for index, config in enumerate(configs):
        candidate_id = source_candidate_id_v1(config, generation=0, index=index)
        package = candidate_root / f"candidate-{index:02d}" / "package"
        materialize_adversarial_source_package(
            source_package=p1_package,
            output_package=package,
            config=config,
            candidate_id=candidate_id,
        )
        packages[candidate_id] = package
        config_by_id[candidate_id] = config
        games.extend(
            _candidate_games(
                package=package,
                candidate_id=candidate_id,
                pool_root=pool_root,
                base_seed=campaign_seed,
                games_per_opponent_seat=games_per_opponent_seat,
                block_id=f"adversarial-source-cem-g000-{candidate_id}",
            )
        )
    _write_json_new(
        gen_root / "manifest.json",
        {
            "schema_version": CAMPAIGN_SCHEMA_V1,
            "source_schema_version": SOURCE_SCHEMA_V1,
            "generation": 0,
            "candidate_count": len(configs),
            "population_size": population_size,
            "elite_count": elite_count,
            "requested_games": len(games),
            "games_per_opponent_seat": games_per_opponent_seat,
            "campaign_seed": campaign_seed,
            "reference_id": REFERENCE_ID,
            "reference_policy_sha256": reference_row["policy_hash"],
            "reference_deck_hash": reference_row["canonical_deck_hash"],
            "parent_policy_sha256": BASE_SOURCE_SHA256,
            "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
            "objective": "maximize_source_score_against_p1",
            "action_trace_used": False,
            "private_fields_used": False,
            "teacher_labels_used": False,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        },
    )
    evaluation = _evaluate(games, gen_root / "evaluation", workers=worker_count)
    results: list[dict[str, object]] = []
    for candidate_id, config in config_by_id.items():
        aggregate = aggregate_source_rows_v1(
            evaluation["rows"], candidate_policy_id=candidate_id, opponent_id=REFERENCE_ID
        )
        results.append(
            {
                "candidate_id": candidate_id,
                "config": config.as_dict(),
                "config_sha256": config.config_sha256(),
                "policy_sha256": _sha256(packages[candidate_id] / "main.py"),
                "aggregate": aggregate,
                "objective": aggregate["objective"],
                "valid": aggregate["valid"],
                "faults": aggregate["faults"],
            }
        )
    rank_rows = [
        {
            "candidate_id": item["candidate_id"],
            "config": item["config"],
            "objective": item["objective"],
            "valid": item["valid"],
            "faults": item["faults"],
        }
        for item in results
    ]
    try:
        elites = rank_valid_results(rank_rows, elite_count=elite_count)
    except ValueError:
        elites = ()
    new_center, new_scales = update_distribution(center, elites) if elites else (center, None)
    validation = None
    top = elites[0] if elites else None
    if top is not None:
        top_id = str(top["candidate_id"])
        validation_games = _candidate_games(
            package=packages[top_id],
            candidate_id=top_id,
            pool_root=pool_root,
            base_seed=campaign_seed + 500_000,
            games_per_opponent_seat=validation_games_per_opponent_seat,
            block_id=f"adversarial-source-validation-{top_id}",
        )
        validation_eval = _evaluate(validation_games, gen_root / "validation", workers=worker_count)
        validation = aggregate_source_rows_v1(
            validation_eval["rows"], candidate_policy_id=top_id, opponent_id=REFERENCE_ID
        )

    promoted = None
    if top is not None and validation is not None:
        train_aggregate = next(item["aggregate"] for item in results if item["candidate_id"] == top["candidate_id"])
        positive = (
            bool(train_aggregate["valid"])
            and bool(validation["valid"])
            and float(train_aggregate["source_score"]) > 0.5
            and float(validation["source_score"]) > 0.5
            and bool(validation["seat_safe"])
        )
        if positive:
            top_id = str(top["candidate_id"])
            promoted_root = output_root / "promoted_source_pool"
            promoted_package = promoted_root / top_id
            promoted_package.parent.mkdir(parents=True, exist_ok=False)
            for name in ("main.py", "deck.csv"):
                shutil.copy2(packages[top_id] / name, promoted_package / name)
            row = build_source_pool_row_v1(
                candidate_id=top_id,
                policy_sha256=_sha256(promoted_package / "main.py"),
                canonical_deck_hash=_deck_hash(promoted_package / "deck.csv"),
                smoke_ok=False,
            )
            pool_manifest = promoted_root / "pool_manifest.json"
            _write_json_new(pool_manifest, [row])
            smoke_games = _p1_smoke_games(p1_package=p1_package, source_package=promoted_package, pool_root=promoted_root, base_seed=campaign_seed + 900_000)
            smoke_eval = _evaluate(smoke_games, output_root / "promoted_source_smoke", workers=worker_count)
            smoke_rows = smoke_eval["rows"]
            smoke_ok = len(smoke_rows) == len(smoke_games) and all(
                str(item.get("outcome")) in {"win", "draw", "loss"} for item in smoke_rows
            )
            row["smoke_ok"] = smoke_ok
            pool_manifest.write_bytes(_canonical_json([row]))
            smoke_summary = output_root / "promoted_source_smoke_summary.json"
            _write_json_new(
                smoke_summary,
                {
                    "source_id": top_id,
                    "requested_games": len(smoke_games),
                    "rows": smoke_rows,
                    "faults": sum(1 for item in smoke_rows if item.get("outcome") == "fault"),
                    "smoke_ok": smoke_ok,
                    "research_only": True,
                },
            )
            if smoke_ok:
                seed_plan = {
                    "screen_base_seed": campaign_seed,
                    "validation_base_seed": campaign_seed + 500_000,
                    "smoke_base_seed": campaign_seed + 900_000,
                    "games_per_opponent_seat": games_per_opponent_seat,
                    "validation_games_per_opponent_seat": validation_games_per_opponent_seat,
                    "smoke_games_per_opponent_seat": 1,
                }
                fresh_meta = _write_fresh_meta_manifest(
                    campaign_root=output_root,
                    source_id=top_id,
                    source_row=row,
                    pool_manifest=pool_manifest,
                    smoke_summary=smoke_summary,
                    seed_namespace=f"adversarial-source-{top_id}-{campaign_seed}",
                    seed_plan=seed_plan,
                )
                promoted = {
                    "source_id": top_id,
                    "pool_root": str(promoted_root.resolve()),
                    "pool_manifest": str(pool_manifest.resolve()),
                    "fresh_meta_manifest": str(fresh_meta.resolve()),
                    "smoke_summary": str(smoke_summary.resolve()),
                    "smoke_ok": True,
                }
            else:
                promoted = {
                    "source_id": top_id,
                    "pool_root": str(promoted_root.resolve()),
                    "pool_manifest": str(pool_manifest.resolve()),
                    "smoke_ok": False,
                }

    result = {
        "schema_version": CAMPAIGN_SCHEMA_V1,
        "source_schema_version": SOURCE_SCHEMA_V1,
        "status": "COMPLETE",
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "reference_id": REFERENCE_ID,
        "population_size": population_size,
        "elite_count": elite_count,
        "requested_games": len(games),
        "evaluation_summary": evaluation["summary"],
        "results": results,
        "elites": [
            {
                **{key: value for key, value in item.items() if key != "config"},
                "config": (
                    item["config"].as_dict()
                    if isinstance(item.get("config"), P1ParameterConfig)
                    else dict(item.get("config", {}))
                ),
            }
            for item in elites
        ],
        "new_center": new_center.as_dict(),
        "new_scales": new_scales,
        "validation": validation,
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
    parser.add_argument("--population-size", type=int, default=8)
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--games-per-opponent-seat", type=int, default=4)
    parser.add_argument("--validation-games-per-opponent-seat", type=int, default=8)
    parser.add_argument("--campaign-seed", type=int, default=20260820)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    args = parser.parse_args(argv)
    try:
        result = run_campaign(
            p1_package=args.p1_package,
            output_root=args.output,
            population_size=args.population_size,
            elite_count=args.elite_count,
            games_per_opponent_seat=args.games_per_opponent_seat,
            validation_games_per_opponent_seat=args.validation_games_per_opponent_seat,
            campaign_seed=args.campaign_seed,
            workers=args.workers,
            budget_path=args.budget,
        )
    except (AdversarialSourceError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
