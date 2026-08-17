#!/usr/bin/env python3
"""Run a research-only deck×policy source-side CEM campaign.

The campaign samples the sealed P1 parameter surface independently for each
official-card deck recipe, evaluates every deck-bound candidate as the subject
against a fixed reference portfolio, and validates objective-ranked,
deck-diverse elites on fresh seeds.  Only terminal WDL, seat, and opponent
identity are consumed by selection.  The command can stage a source pool but
never grants training, promotion, submission, or BestKnown authority.
"""

from __future__ import annotations

import argparse
from collections import Counter
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

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import AUTHORITY_FALSE_V1  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import (  # noqa: E402
    P1ParameterConfig,
    sample_population,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import BASE_SOURCE_SHA256  # noqa: E402
from mage_ptcg.meta_specialist.deck_bound_source_cem_v1 import (  # noqa: E402
    PLAN_SCHEMA_V1,
    DeckBoundSourceCemError,
    candidate_id_for_deck_config_v1,
    load_deck_bound_source_cem_plan_v1,
    select_diverse_source_elites_v1,
    source_rankable_v1,
    source_side_gate_v1,
)
from mage_ptcg.meta_specialist.resource_governor_v1 import (  # noqa: E402
    ResourceBudget,
    ResourceGovernor,
)
from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (  # noqa: E402
    canonical_deck_sha256_v1,
    load_card_catalog_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    verify_self_owned_cg_package_v1,
)
from mage_ptcg.opponent_ingest.robust_adversarial_source_cem_v1 import (  # noqa: E402
    aggregate_portfolio_source_rows_v1,
)
from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (  # noqa: E402
    materialize_self_owned_cg_meta_batch_v1,
)
from scripts import generate_self_owned_cg_deck_conditioned_adversarial_meta_v1 as deck_conditioned  # noqa: E402
from scripts import generate_self_owned_cg_deck_v1 as deck_generator  # noqa: E402
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


CAMPAIGN_SCHEMA_V1 = "self-owned-cg-deck-bound-source-cem-campaign-v1"
SOURCE_KIND_V1 = "self_owned_official_card_data_deck_bound_source_cem"
DEFAULT_BUDGET = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
SOURCE_VALIDATION_SEAT_GAP_LIMIT_V1 = 0.25
MIN_SOURCE_COUNT_V1 = 3
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class DeckBoundSourceCemRunnerError(ValueError):
    """Raised when a source-side campaign cannot be sealed safely."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise DeckBoundSourceCemRunnerError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_new(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _deck_identity(path: Path) -> tuple[str, str]:
    try:
        values = tuple(int(token) for token in path.read_text(encoding="utf-8").split())
    except (OSError, UnicodeError, ValueError) as exc:
        raise DeckBoundSourceCemRunnerError(f"cannot parse deck: {path}") from exc
    if len(values) != 60 or any(value <= 0 for value in values):
        raise DeckBoundSourceCemRunnerError(f"deck must contain 60 positive IDs: {path}")
    return _sha256(path), canonical_deck_sha256_v1(values)


def _copy_reference_pool(plan: Mapping[str, object], output_root: Path) -> tuple[Path, tuple[str, ...], list[dict[str, object]]]:
    pool_root = output_root / "reference_pool"
    pool_root.mkdir(parents=True, exist_ok=False)
    refs = plan["reference_specs"]
    if not isinstance(refs, Sequence) or not refs:
        raise DeckBoundSourceCemRunnerError("reference_specs are missing")
    rows: list[dict[str, object]] = []
    ids: list[str] = []
    for raw in refs:
        if not isinstance(raw, Mapping):
            raise DeckBoundSourceCemRunnerError("reference spec is not an object")
        reference_id = str(raw["id"])
        source = Path(str(raw["package"])).resolve()
        if _ID.fullmatch(reference_id) is None or reference_id in ids:
            raise DeckBoundSourceCemRunnerError(f"invalid or duplicate reference: {reference_id}")
        if not source.is_dir() or source.is_symlink():
            raise DeckBoundSourceCemRunnerError(f"reference package is not a directory: {source}")
        for path in source.rglob("*"):
            if path.is_symlink():
                raise DeckBoundSourceCemRunnerError(f"reference package contains symlink: {path}")
        target = pool_root / reference_id
        shutil.copytree(source, target, symlinks=False)
        policy = target / "main.py"
        deck = target / "deck.csv"
        if not policy.is_file() or not deck.is_file():
            raise DeckBoundSourceCemRunnerError(f"reference package is incomplete: {source}")
        deck_file_sha, canonical_deck_sha = _deck_identity(deck)
        rows.append(
            {
                "id": reference_id,
                "policy_hash": _sha256(policy),
                "canonical_deck_hash": canonical_deck_sha,
                "deck_file_sha256": deck_file_sha,
                "source": "fixed_reference_portfolio",
                "usage_boundary": "local_eval_only",
                "smoke_ok": True,
            }
        )
        ids.append(reference_id)
    rows.sort(key=lambda item: str(item["id"]))
    _write_new(pool_root / "pool_manifest.json", rows)
    return pool_root, tuple(ids), rows


def _generate_decks(plan: Mapping[str, object], output_root: Path) -> dict[str, dict[str, object]]:
    deck_root = output_root / "deck-generation"
    deck_root.mkdir(parents=True, exist_ok=False)
    recipes = plan["deck_recipes"]
    if not isinstance(recipes, Sequence):
        raise DeckBoundSourceCemRunnerError("deck_recipes are missing")
    results: dict[str, dict[str, object]] = {}
    catalog = load_card_catalog_v1(str(plan["card_database"]))
    del catalog
    for raw in recipes:
        if not isinstance(raw, Mapping):
            raise DeckBoundSourceCemRunnerError("deck recipe is not an object")
        recipe_id = str(raw["id"])
        recipe_output = deck_root / recipe_id
        results[recipe_id] = deck_generator.run_generation_v1(
            output=recipe_output,
            card_db=str(plan["card_database"]),
            spec=str(raw["spec"]),
            source_package=str(plan["p1_source_package"]),
            public_scan_roots=tuple(str(root) for root in plan["public_scan_roots"]),
            seed=int(raw["seed"]),
            ordinal=int(raw["ordinal"]),
        )
    return results


def _candidate_games(
    *,
    package: Path,
    candidate_id: str,
    pool_root: Path,
    reference_ids: Sequence[str],
    base_seed: int,
    games_per_reference_seat: int,
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
        refs=tuple(reference_ids),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_reference_seat,
        block_id=block_id,
    )


def _evaluate(games: Sequence[object], output_dir: Path, *, workers: int, stage: str) -> dict[str, object]:
    if not games:
        raise DeckBoundSourceCemRunnerError("evaluation has no games")
    total = len(games)
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}
    bar = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm

            bar = tqdm(total=total, desc=stage, unit="game", dynamic_ncols=True)
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
                    {"stage": stage, "completed": state["completed"], "requested": total, "faults": state["faults"]},
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


def _rankable_aggregate(aggregate: Mapping[str, object], reference_count: int) -> bool:
    return source_rankable_v1(aggregate, expected_reference_count=reference_count)


def _materialize_candidate(
    *,
    source_package: Path,
    deck_package: Path,
    output_package: Path,
    source_epoch: str,
    config: P1ParameterConfig,
    candidate_id: str,
    deck_recipe_id: str,
    generation: int,
    index: int,
) -> Path:
    package_manifest = deck_conditioned._materialize_deck_conditioned_package(
        source_package=source_package,
        deck_package=deck_package,
        output_package=output_package,
        config=config,
        candidate_id=candidate_id,
    )
    body = {
        "schema_version": CAMPAIGN_SCHEMA_V1,
        "status": "SCREEN_CANDIDATE",
        "source_epoch": source_epoch,
        "generation": generation,
        "candidate_index": index,
        "candidate_id": candidate_id,
        "deck_recipe_id": deck_recipe_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": package_manifest["policy_sha256"],
        "deck_file_sha256": package_manifest["deck_file_sha256"],
        "canonical_deck_sha256": package_manifest["canonical_deck_sha256"],
        "public_parent_read": False,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    body["manifest_sha256"] = hashlib.sha256(_canonical_json(body)).hexdigest()
    _write_new(output_package / "deck_bound_source_cem_manifest.json", body)
    verify_self_owned_cg_package_v1(output_package)
    return output_package


def run_campaign(
    *,
    plan: str | Path,
    output: str | Path,
    population_per_deck: int = 2,
    elite_count: int = 4,
    screen_games_per_reference_seat: int = 4,
    validation_games_per_reference_seat: int = 32,
    smoke_games_per_reference_seat: int = 1,
    campaign_seed: int = 2026082301,
    workers: int | None = None,
    budget_path: str | Path = DEFAULT_BUDGET,
    initial_config: P1ParameterConfig | None = None,
) -> dict[str, object]:
    plan_data = load_deck_bound_source_cem_plan_v1(plan)
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root exists: {output_root}")
    if type(population_per_deck) is not int or population_per_deck < 2:
        raise DeckBoundSourceCemRunnerError("population_per_deck must be at least 2")
    if type(elite_count) is not int or elite_count < 1:
        raise DeckBoundSourceCemRunnerError("elite_count must be positive")
    if type(screen_games_per_reference_seat) is not int or screen_games_per_reference_seat < 1:
        raise DeckBoundSourceCemRunnerError("screen_games_per_reference_seat must be positive")
    if type(validation_games_per_reference_seat) is not int or validation_games_per_reference_seat < screen_games_per_reference_seat:
        raise DeckBoundSourceCemRunnerError("validation games must be >= screen games")
    if type(smoke_games_per_reference_seat) is not int or smoke_games_per_reference_seat < 1:
        raise DeckBoundSourceCemRunnerError("smoke_games_per_reference_seat must be positive")
    recipes = tuple(plan_data["deck_recipes"])
    reference_count = len(plan_data["reference_specs"])
    if elite_count > len(recipes) * population_per_deck:
        raise DeckBoundSourceCemRunnerError("elite_count exceeds population")
    if elite_count < min(4, len(recipes)):
        raise DeckBoundSourceCemRunnerError("elite_count must cover the deck portfolio")
    source_package = Path(str(plan_data["p1_source_package"])).resolve()
    if _sha256(source_package / "main.py") != BASE_SOURCE_SHA256:
        raise DeckBoundSourceCemRunnerError("P1 package main.py SHA does not match sealed parent")

    output_root.mkdir(parents=True, exist_ok=False)
    budget = ResourceBudget.from_json(Path(budget_path))
    governor = ResourceGovernor(budget)
    decision = governor.decide(task_cap=workers or budget.max_workers, gpu_required=False)
    if decision.state != "normal" or decision.recommended_workers < 1:
        raise DeckBoundSourceCemRunnerError(f"ResourceGovernor refused campaign: {decision.to_dict()}")
    worker_count = min(decision.recommended_workers, workers or decision.recommended_workers)
    governor.write_telemetry(output_root / "resource_telemetry.json", task_cap=worker_count, gpu_required=False)

    pool_root, reference_ids, reference_rows = _copy_reference_pool(plan_data, output_root)
    deck_results = _generate_decks(plan_data, output_root)
    center = initial_config or P1ParameterConfig.default()
    center.validate()
    generation = 0
    candidate_root = output_root / "generation-0000" / "candidates"
    candidate_root.mkdir(parents=True, exist_ok=False)
    candidate_packages: dict[str, Path] = {}
    candidate_configs: dict[str, P1ParameterConfig] = {}
    candidate_decks: dict[str, str] = {}
    candidate_manifests: dict[str, Path] = {}
    all_games: list[object] = []
    recipe_centers = {str(recipe["id"]): center for recipe in recipes}
    for recipe in recipes:
        recipe_id = str(recipe["id"])
        recipe_center = recipe_centers[recipe_id]
        configs = sample_population(
            recipe_center,
            generation=generation,
            population_size=population_per_deck,
            seed=campaign_seed + int(recipe["ordinal"]) * 1009,
        )
        for index, config in enumerate(configs):
            candidate_id = candidate_id_for_deck_config_v1(
                recipe_id,
                generation=generation,
                index=index,
                config=config,
            )
            package = candidate_root / recipe_id / f"candidate-{index:02d}" / "package"
            _materialize_candidate(
                source_package=source_package,
                deck_package=Path(str(deck_results[recipe_id]["artifact_paths"]["package"]))
                if Path(str(deck_results[recipe_id]["artifact_paths"]["package"])).is_absolute()
                else output_root / "deck-generation" / recipe_id / str(deck_results[recipe_id]["artifact_paths"]["package"]),
                output_package=package,
                source_epoch=str(plan_data["source_epoch"]),
                config=config,
                candidate_id=candidate_id,
                deck_recipe_id=recipe_id,
                generation=generation,
                index=index,
            )
            candidate_packages[candidate_id] = package
            candidate_configs[candidate_id] = config
            candidate_decks[candidate_id] = recipe_id
            candidate_manifests[candidate_id] = package / "deck_bound_source_cem_manifest.json"
            all_games.extend(
                _candidate_games(
                    package=package,
                    candidate_id=candidate_id,
                    pool_root=pool_root,
                    reference_ids=reference_ids,
                    base_seed=campaign_seed,
                    games_per_reference_seat=screen_games_per_reference_seat,
                    block_id=f"deck-bound-source-screen-g{generation:02d}",
                )
            )

    screen_root = output_root / "generation-0000" / "screen"
    screen_eval = _evaluate(all_games, screen_root, workers=worker_count, stage="source-side screen")
    results: list[dict[str, object]] = []
    for candidate_id, config in candidate_configs.items():
        aggregate = aggregate_portfolio_source_rows_v1(
            screen_eval["rows"],
            candidate_policy_id=candidate_id,
            reference_ids=reference_ids,
            seat_gap_limit=SOURCE_VALIDATION_SEAT_GAP_LIMIT_V1,
        )
        rankable = _rankable_aggregate(aggregate, reference_count)
        strict = source_side_gate_v1(
            aggregate,
            seat_gap_limit=SOURCE_VALIDATION_SEAT_GAP_LIMIT_V1,
        )
        results.append(
            {
                "candidate_id": candidate_id,
                "deck_recipe_id": candidate_decks[candidate_id],
                "config": config.as_dict(),
                "config_sha256": config.config_sha256(),
                "policy_sha256": _sha256(candidate_packages[candidate_id] / "main.py"),
                "canonical_deck_sha256": _deck_identity(candidate_packages[candidate_id] / "deck.csv")[1],
                "objective": float(aggregate["robust_objective"]),
                "aggregate": aggregate,
                "valid": rankable,
                "rankable": rankable,
                "strict_gate": strict,
                "faults": int(aggregate["faults"]),
            }
        )
    elites = select_diverse_source_elites_v1(results, elite_count=elite_count)
    new_centers: dict[str, dict[str, object]] = {}
    new_scales: dict[str, dict[str, float]] = {}
    for recipe in recipes:
        recipe_id = str(recipe["id"])
        deck_results_for_update = [item for item in elites if item["deck_recipe_id"] == recipe_id]
        if deck_results_for_update:
            updated, scales = update_distribution(
                center,
                [{"config": item["config"], "objective": item["objective"], "faults": 0, "valid": True} for item in deck_results_for_update],
            )
            new_centers[recipe_id] = updated.as_dict()
            new_scales[recipe_id] = scales
        else:
            new_centers[recipe_id] = center.as_dict()
            new_scales[recipe_id] = {}

    validation_candidates: list[dict[str, object]] = []
    validated_strict: list[str] = []
    for rank, elite in enumerate(elites):
        candidate_id = str(elite["candidate_id"])
        validation_games = _candidate_games(
            package=candidate_packages[candidate_id],
            candidate_id=candidate_id,
            pool_root=pool_root,
            reference_ids=reference_ids,
            base_seed=campaign_seed + 500_000 + rank * 100_000,
            games_per_reference_seat=validation_games_per_reference_seat,
            block_id=f"deck-bound-source-validation-r{rank:02d}",
        )
        validation_eval = _evaluate(
            validation_games,
            output_root / "generation-0000" / f"validation-{rank:02d}",
            workers=worker_count,
            stage=f"source validation {rank + 1}/{len(elites)}",
        )
        aggregate = aggregate_portfolio_source_rows_v1(
            validation_eval["rows"],
            candidate_policy_id=candidate_id,
            reference_ids=reference_ids,
            seat_gap_limit=SOURCE_VALIDATION_SEAT_GAP_LIMIT_V1,
        )
        rankable = _rankable_aggregate(aggregate, reference_count)
        strict = source_side_gate_v1(
            aggregate,
            seat_gap_limit=SOURCE_VALIDATION_SEAT_GAP_LIMIT_V1,
        )
        if rankable and strict:
            validated_strict.append(candidate_id)
        validation_candidates.append(
            {
                "rank": rank,
                "candidate_id": candidate_id,
                "deck_recipe_id": elite["deck_recipe_id"],
                "aggregate": aggregate,
                "rankable": rankable,
                "strict_gate": strict,
            }
        )

    staged_batch = None
    smoke_summary = None
    min_source_count = min(MIN_SOURCE_COUNT_V1, len(recipes))
    if len(validated_strict) >= min_source_count:
        selected_ids = validated_strict[:]
        smoke_games: list[object] = []
        for candidate_id in selected_ids:
            smoke_games.extend(
                _candidate_games(
                    package=candidate_packages[candidate_id],
                    candidate_id=candidate_id,
                    pool_root=pool_root,
                    reference_ids=reference_ids,
                    base_seed=campaign_seed + 900_000,
                    games_per_reference_seat=smoke_games_per_reference_seat,
                    block_id="deck-bound-source-selected-smoke",
                )
            )
        smoke_eval = _evaluate(
            smoke_games,
            output_root / "selected-source-smoke",
            workers=worker_count,
            stage="selected source smoke",
        )
        smoke_faults = sum(1 for row in smoke_eval["rows"] if row.get("outcome") == "fault")
        smoke_summary = {
            "schema_version": f"{CAMPAIGN_SCHEMA_V1}-smoke",
            "status": "COMPLETE" if smoke_faults == 0 else "FAULT",
            "selected_ids": selected_ids,
            "requested_games": len(smoke_games),
            "completed_rows": len(smoke_eval["rows"]),
            "faults": smoke_faults,
            "evaluator_summary": smoke_eval["summary"],
            "rows": smoke_eval["rows"],
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        }
        smoke_path = output_root / "selected-source-smoke-summary.json"
        _write_new(smoke_path, smoke_summary)
        if smoke_faults == 0:
            staged_batch = materialize_self_owned_cg_meta_batch_v1(
                candidate_packages=tuple(candidate_packages[candidate_id] for candidate_id in selected_ids),
                output_root=output_root / "staged_source_pool",
                seed_namespace=str(plan_data["seed_namespace"]),
                generation_manifests=tuple(candidate_manifests[candidate_id] for candidate_id in selected_ids),
                source_epoch=str(plan_data["source_epoch"]),
                source_kind=SOURCE_KIND_V1,
            )

    campaign_result = {
        "schema_version": CAMPAIGN_SCHEMA_V1,
        "status": "SOURCE_POOL_STAGED" if staged_batch is not None else "NO_STRICT_SOURCE_POOL",
        "plan_path": plan_data["path"],
        "plan_sha256": plan_data["plan_sha256"],
        "source_epoch": plan_data["source_epoch"],
        "seed_namespace": plan_data["seed_namespace"],
        "reference_ids": list(reference_ids),
        "reference_rows": reference_rows,
        "deck_recipe_ids": [str(recipe["id"]) for recipe in recipes],
        "population_per_deck": population_per_deck,
        "elite_count": elite_count,
        "screen_games_per_reference_seat": screen_games_per_reference_seat,
        "validation_games_per_reference_seat": validation_games_per_reference_seat,
        "source_validation_seat_gap_limit": SOURCE_VALIDATION_SEAT_GAP_LIMIT_V1,
        "minimum_source_count": min_source_count,
        "campaign_seed": campaign_seed,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "screen_requested_games": len(all_games),
        "screen_summary": screen_eval["summary"],
        "results": results,
        "elites": list(elites),
        "new_centers": new_centers,
        "new_scales": new_scales,
        "validation_candidates": validation_candidates,
        "selected_strict_ids": validated_strict,
        "staged_batch": staged_batch,
        "smoke_summary": smoke_summary,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    campaign_result["manifest_sha256"] = hashlib.sha256(_canonical_json(campaign_result)).hexdigest()
    _write_new(output_root / "campaign_result.json", campaign_result)
    return campaign_result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population-per-deck", type=int, default=2)
    parser.add_argument("--elite-count", type=int, default=4)
    parser.add_argument("--screen-games-per-reference-seat", type=int, default=4)
    parser.add_argument("--validation-games-per-reference-seat", type=int, default=32)
    parser.add_argument("--smoke-games-per-reference-seat", type=int, default=1)
    parser.add_argument("--campaign-seed", type=int, default=2026082301)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_campaign(
            plan=args.plan,
            output=args.output,
            population_per_deck=args.population_per_deck,
            elite_count=args.elite_count,
            screen_games_per_reference_seat=args.screen_games_per_reference_seat,
            validation_games_per_reference_seat=args.validation_games_per_reference_seat,
            smoke_games_per_reference_seat=args.smoke_games_per_reference_seat,
            campaign_seed=args.campaign_seed,
            workers=args.workers,
            budget_path=args.budget,
        )
    except (DeckBoundSourceCemError, DeckBoundSourceCemRunnerError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
