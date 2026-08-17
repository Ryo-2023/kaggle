"""Pure selection primitives for deck-bound source-side CEM.

The source-side campaign samples a policy configuration separately for each
official-card deck recipe.  This module keeps candidate identity, strict
source-side eligibility, and deck-diverse elite selection deterministic and
independent from CABT execution.
"""

from __future__ import annotations

import math
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
DEFAULT_SEAT_GAP_LIMIT_V1 = 0.05
PLAN_SCHEMA_V1 = "self-owned-cg-deck-bound-source-cem-plan-v1"


class DeckBoundSourceCemError(ValueError):
    """Raised when a source-side CEM identity or selection is malformed."""


def _repo_path(value: object, *, field: str) -> Path:
    if type(value) is not str or not value.strip():
        raise DeckBoundSourceCemError(f"{field} must be a non-empty path")
    path = Path(value).resolve()
    if not path.exists():
        raise DeckBoundSourceCemError(f"{field} does not exist: {path}")
    return path


def load_deck_bound_source_cem_plan_v1(path: str | Path) -> dict[str, object]:
    """Load a strict plan for one deck×policy source-side CEM campaign."""

    plan_path = Path(path).resolve()
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeckBoundSourceCemError(f"cannot read plan: {plan_path}") from exc
    if not isinstance(payload, Mapping):
        raise DeckBoundSourceCemError("plan must be a JSON object")
    expected = {
        "schema_version",
        "source_epoch",
        "seed_namespace",
        "card_database",
        "p1_source_package",
        "public_scan_roots",
        "reference_specs",
        "deck_recipes",
    }
    if set(payload) != expected or payload.get("schema_version") != PLAN_SCHEMA_V1:
        raise DeckBoundSourceCemError("plan schema or fields are invalid")
    for field in ("source_epoch", "seed_namespace"):
        if type(payload[field]) is not str or not str(payload[field]).strip():
            raise DeckBoundSourceCemError(f"{field} must be non-empty")
    card_database = _repo_path(payload["card_database"], field="card_database")
    if not card_database.is_file():
        raise DeckBoundSourceCemError(f"card_database must be a file: {card_database}")
    source_package = _repo_path(payload["p1_source_package"], field="p1_source_package")
    if not source_package.is_dir():
        raise DeckBoundSourceCemError(f"p1_source_package must be a directory: {source_package}")

    roots_raw = payload["public_scan_roots"]
    if not isinstance(roots_raw, list) or not roots_raw:
        raise DeckBoundSourceCemError("public_scan_roots must be a non-empty list")
    roots: list[str] = []
    for index, value in enumerate(roots_raw):
        root = _repo_path(value, field=f"public_scan_roots[{index}]")
        if not root.is_dir():
            raise DeckBoundSourceCemError(f"public scan root must be a directory: {root}")
        roots.append(str(root))

    refs_raw = payload["reference_specs"]
    if not isinstance(refs_raw, list) or len(refs_raw) < 2:
        raise DeckBoundSourceCemError("reference_specs must contain at least two references")
    refs: list[dict[str, str]] = []
    seen_refs: set[str] = set()
    for index, item in enumerate(refs_raw):
        if not isinstance(item, Mapping) or set(item) != {"id", "package"}:
            raise DeckBoundSourceCemError(f"reference_specs[{index}] has invalid fields")
        reference_id = item["id"]
        if type(reference_id) is not str or _ID.fullmatch(reference_id) is None:
            raise DeckBoundSourceCemError(f"reference_specs[{index}].id is invalid")
        if reference_id in seen_refs:
            raise DeckBoundSourceCemError(f"duplicate reference id: {reference_id}")
        package = _repo_path(item["package"], field=f"reference_specs[{index}].package")
        if not package.is_dir():
            raise DeckBoundSourceCemError(f"reference package must be a directory: {package}")
        seen_refs.add(reference_id)
        refs.append({"id": reference_id, "package": str(package)})

    recipes_raw = payload["deck_recipes"]
    if not isinstance(recipes_raw, list) or len(recipes_raw) < 2:
        raise DeckBoundSourceCemError("deck_recipes must contain at least two recipes")
    recipes: list[dict[str, object]] = []
    seen_recipes: set[str] = set()
    for index, item in enumerate(recipes_raw):
        if not isinstance(item, Mapping) or set(item) != {"id", "spec", "seed", "ordinal"}:
            raise DeckBoundSourceCemError(f"deck_recipes[{index}] has invalid fields")
        recipe_id = item["id"]
        if type(recipe_id) is not str or _ID.fullmatch(recipe_id) is None:
            raise DeckBoundSourceCemError(f"deck_recipes[{index}].id is invalid")
        if recipe_id in seen_recipes:
            raise DeckBoundSourceCemError(f"duplicate deck recipe id: {recipe_id}")
        spec = _repo_path(item["spec"], field=f"deck_recipes[{index}].spec")
        if not spec.is_file():
            raise DeckBoundSourceCemError(f"deck recipe spec must be a file: {spec}")
        if type(item["seed"]) is not int or type(item["ordinal"]) is not int or item["ordinal"] < 0:
            raise DeckBoundSourceCemError(f"deck recipe {recipe_id} has invalid seed/ordinal")
        seen_recipes.add(recipe_id)
        recipes.append(
            {
                "id": recipe_id,
                "spec": str(spec),
                "seed": int(item["seed"]),
                "ordinal": int(item["ordinal"]),
            }
        )
    return {
        "path": str(plan_path),
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "schema_version": PLAN_SCHEMA_V1,
        "source_epoch": str(payload["source_epoch"]),
        "seed_namespace": str(payload["seed_namespace"]),
        "card_database": str(card_database),
        "p1_source_package": str(source_package),
        "public_scan_roots": tuple(roots),
        "reference_specs": tuple(refs),
        "deck_recipes": tuple(recipes),
    }


def candidate_id_for_deck_config_v1(
    deck_recipe_id: str,
    *,
    generation: int,
    index: int,
    config: P1ParameterConfig,
) -> str:
    """Return a deterministic identity that cannot erase the deck lineage."""

    if type(deck_recipe_id) is not str or _ID.fullmatch(deck_recipe_id) is None:
        raise DeckBoundSourceCemError("deck_recipe_id must be a bounded ASCII identifier")
    if type(generation) is not int or generation < 0:
        raise DeckBoundSourceCemError("generation must be a non-negative integer")
    if type(index) is not int or index < 0:
        raise DeckBoundSourceCemError("index must be a non-negative integer")
    if not isinstance(config, P1ParameterConfig):
        raise DeckBoundSourceCemError("config must be P1ParameterConfig")
    config.validate()
    return (
        f"deck-bound-source-g{generation:02d}-{deck_recipe_id}-c{index:02d}-"
        f"{config.config_sha256()[:12]}"
    )


def source_side_gate_v1(
    aggregate: Mapping[str, object],
    *,
    seat_gap_limit: float = DEFAULT_SEAT_GAP_LIMIT_V1,
) -> bool:
    """Return whether one source aggregate is strict-gate eligible.

    The aggregate is expected to have already been computed from terminal WDL
    rows for every fixed reference.  Missing or malformed evidence fails
    closed; no score is inferred from a partial block.
    """

    if not isinstance(aggregate, Mapping):
        return False
    if type(seat_gap_limit) not in (int, float) or isinstance(seat_gap_limit, bool):
        return False
    if not math.isfinite(float(seat_gap_limit)) or not 0.0 <= float(seat_gap_limit) <= 1.0:
        return False
    faults = aggregate.get("faults")
    gap = aggregate.get("max_seat_gap")
    return (
        aggregate.get("valid") is True
        and type(faults) is int
        and faults == 0
        and type(gap) in (int, float)
        and not isinstance(gap, bool)
        and math.isfinite(float(gap))
        and 0.0 <= float(gap) <= float(seat_gap_limit)
    )


def source_rankable_v1(
    aggregate: Mapping[str, object],
    *,
    expected_reference_count: int,
) -> bool:
    """Return whether a screen aggregate is complete enough to rank.

    Screen blocks may have seat-gap noise, so rankability deliberately does not
    apply the strict five-percent gate.  It still rejects missing references,
    faults, and complete seat collapse; only fresh validation may make a
    candidate eligible for the source pool.
    """

    if not isinstance(aggregate, Mapping) or type(expected_reference_count) is not int or expected_reference_count <= 0:
        return False
    if aggregate.get("reference_count") != expected_reference_count or aggregate.get("faults") != 0:
        return False
    reference_results = aggregate.get("reference_results")
    if not isinstance(reference_results, Mapping) or len(reference_results) != expected_reference_count:
        return False
    for result in reference_results.values():
        if not isinstance(result, Mapping):
            return False
        if type(result.get("requested_games")) is not int or int(result["requested_games"]) <= 0:
            return False
        if result.get("faults") != 0:
            return False
    return True


def _normalized_eligible_results(
    results: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    eligible: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for raw in results:
        if not isinstance(raw, Mapping):
            continue
        candidate_id = raw.get("candidate_id")
        deck_recipe_id = raw.get("deck_recipe_id")
        objective = raw.get("objective")
        faults = raw.get("faults")
        if (
            type(candidate_id) is not str
            or _ID.fullmatch(candidate_id) is None
            or candidate_id in seen_ids
            or type(deck_recipe_id) is not str
            or _ID.fullmatch(deck_recipe_id) is None
            or type(objective) not in (int, float)
            or isinstance(objective, bool)
            or not math.isfinite(float(objective))
            or type(faults) is not int
            or faults != 0
            or raw.get("valid") is not True
        ):
            continue
        seen_ids.add(candidate_id)
        normalized = dict(raw)
        normalized["objective"] = float(objective)
        eligible.append(normalized)
    return eligible


def select_diverse_source_elites_v1(
    results: Sequence[Mapping[str, object]],
    *,
    elite_count: int,
) -> tuple[dict[str, object], ...]:
    """Select objective-ranked elites while covering deck recipes first."""

    if type(elite_count) is not int or elite_count <= 0:
        raise DeckBoundSourceCemError("elite_count must be a positive integer")
    eligible = _normalized_eligible_results(results)
    eligible.sort(key=lambda item: (-float(item["objective"]), str(item["candidate_id"])))
    if len(eligible) < elite_count:
        raise DeckBoundSourceCemError(
            f"not enough eligible candidates: {len(eligible)} < {elite_count}"
        )

    selected: list[dict[str, object]] = []
    seen_decks: set[str] = set()
    for item in eligible:
        deck_id = str(item["deck_recipe_id"])
        if deck_id in seen_decks:
            continue
        selected.append(item)
        seen_decks.add(deck_id)
        if len(selected) == elite_count:
            return tuple(selected)
    for item in eligible:
        candidate_id = str(item["candidate_id"])
        if any(str(selected_item["candidate_id"]) == candidate_id for selected_item in selected):
            continue
        selected.append(item)
        if len(selected) == elite_count:
            break
    return tuple(selected)


__all__ = [
    "DEFAULT_SEAT_GAP_LIMIT_V1",
    "DeckBoundSourceCemError",
    "PLAN_SCHEMA_V1",
    "candidate_id_for_deck_config_v1",
    "load_deck_bound_source_cem_plan_v1",
    "select_diverse_source_elites_v1",
    "source_rankable_v1",
    "source_side_gate_v1",
]
