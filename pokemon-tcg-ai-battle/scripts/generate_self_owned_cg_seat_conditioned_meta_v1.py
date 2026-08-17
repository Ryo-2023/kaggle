#!/usr/bin/env python3
"""Generate an official-card-only public-seat-conditioned CG source epoch.

Only staged research artifacts are produced.  Runtime smoke, promotion, split
sealing, and CABT evaluation remain explicit separate gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_seat_conditioned_renderer_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    SeatConditionedConfig,
    materialize_seat_conditioned_package,
)
from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (  # noqa: E402
    load_card_catalog_v1,
    load_self_owned_deck_spec_v1,
)
from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (  # noqa: E402
    SOURCE_KIND_V1,
    materialize_self_owned_cg_meta_batch_v1,
)
from scripts.generate_self_owned_cg_deck_v1 import (  # noqa: E402
    run_generation_v1 as generate_deck_v1,
    scan_public_canonical_hashes_v1,
)


PLAN_SCHEMA = "self-owned-cg-seat-conditioned-factorial-plan-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}


class SeatConditionedPlanError(ValueError):
    """Raised when a source plan is malformed or unsafe."""


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SeatConditionedPlanError("value is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SeatConditionedPlanError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeatConditionedPlanError(f"cannot read plan: {path}") from exc
    if not isinstance(value, Mapping):
        raise SeatConditionedPlanError("plan root must be an object")
    return value


def _resolve_repo_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SeatConditionedPlanError(f"{field} must be a non-empty path")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in value
    ):
        raise SeatConditionedPlanError(f"{field} must be an ASCII identifier")
    return value


def load_seat_conditioned_plan_v1(path: str | Path) -> dict[str, object]:
    """Load and normalize one content-addressed seat-conditioned plan."""

    plan_path = Path(path).resolve()
    raw = dict(_read_json(plan_path))
    expected = {
        "schema_version", "source_epoch", "seed_namespace", "card_database",
        "public_scan_roots", "deck_recipes", "policy_variants",
    }
    if set(raw) != expected or raw.get("schema_version") != PLAN_SCHEMA:
        raise SeatConditionedPlanError("plan schema or fields are invalid")
    for field in ("source_epoch", "seed_namespace"):
        if not isinstance(raw[field], str) or not raw[field].strip():
            raise SeatConditionedPlanError(f"{field} must be non-empty")
    card_database = _resolve_repo_path(raw["card_database"], field="card_database")
    if not card_database.is_file():
        raise SeatConditionedPlanError(f"card database is missing: {card_database}")

    roots_raw = raw["public_scan_roots"]
    if not isinstance(roots_raw, list) or not roots_raw:
        raise SeatConditionedPlanError("public_scan_roots must be non-empty")
    scan_roots: list[str] = []
    for index, value in enumerate(roots_raw):
        root = _resolve_repo_path(value, field=f"public_scan_roots[{index}]")
        if not root.is_dir():
            raise SeatConditionedPlanError(f"public scan root is missing: {root}")
        scan_roots.append(str(root))

    recipes_raw = raw["deck_recipes"]
    if not isinstance(recipes_raw, list) or not recipes_raw:
        raise SeatConditionedPlanError("deck_recipes must be non-empty")
    recipes: dict[str, dict[str, object]] = {}
    for index, item in enumerate(recipes_raw):
        if not isinstance(item, Mapping) or set(item) != {"id", "spec", "seed", "ordinal"}:
            raise SeatConditionedPlanError(f"deck_recipes[{index}] has invalid fields")
        recipe_id = _identifier(item["id"], f"deck_recipes[{index}].id")
        if recipe_id in recipes:
            raise SeatConditionedPlanError(f"duplicate deck recipe: {recipe_id}")
        if type(item["seed"]) is not int or type(item["ordinal"]) is not int or item["ordinal"] < 0:
            raise SeatConditionedPlanError(f"deck recipe {recipe_id} has invalid seed/ordinal")
        spec = _resolve_repo_path(item["spec"], field=f"deck_recipes[{index}].spec")
        if not spec.is_file():
            raise SeatConditionedPlanError(f"deck recipe spec is missing: {spec}")
        recipes[recipe_id] = {
            "id": recipe_id,
            "spec": str(spec),
            "seed": int(item["seed"]),
            "ordinal": int(item["ordinal"]),
        }

    variants_raw = raw["policy_variants"]
    if not isinstance(variants_raw, list) or not variants_raw:
        raise SeatConditionedPlanError("policy_variants must be non-empty")
    variants: dict[str, dict[str, object]] = {}
    for index, item in enumerate(variants_raw):
        if not isinstance(item, Mapping) or set(item) != {"id", "deck_recipe_id", "overrides"}:
            raise SeatConditionedPlanError(f"policy_variants[{index}] has invalid fields")
        variant_id = _identifier(item["id"], f"policy_variants[{index}].id")
        if variant_id in variants:
            raise SeatConditionedPlanError(f"duplicate policy variant: {variant_id}")
        recipe_id = _identifier(item["deck_recipe_id"], f"policy_variants[{index}].deck_recipe_id")
        if recipe_id not in recipes:
            raise SeatConditionedPlanError(f"variant references unknown deck recipe: {recipe_id}")
        overrides = item["overrides"]
        if not isinstance(overrides, Mapping):
            raise SeatConditionedPlanError(f"variant {variant_id} overrides must be an object")
        try:
            config = SeatConditionedConfig.from_mapping(dict(overrides))
        except (TypeError, ValueError) as exc:
            raise SeatConditionedPlanError(f"variant {variant_id} has invalid overrides") from exc
        variants[variant_id] = {
            "id": variant_id,
            "deck_recipe_id": recipe_id,
            "config": config.as_dict(),
            "config_sha256": config.config_sha256(),
        }
    return {
        "path": str(plan_path),
        "plan_sha256": _sha256_file(plan_path),
        "schema_version": PLAN_SCHEMA,
        "source_epoch": str(raw["source_epoch"]),
        "seed_namespace": str(raw["seed_namespace"]),
        "card_database": str(card_database),
        "public_scan_roots": tuple(scan_roots),
        "deck_recipes": tuple(recipes.values()),
        "policy_variants": tuple(variants.values()),
    }


def _write_canonical(path: Path, payload: Mapping[str, object]) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(payload) + b"\n"
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def run_generation_v1(
    *, plan: str | Path, output: str | Path, p1_source_package: str | Path
) -> dict[str, object]:
    plan_data = load_seat_conditioned_plan_v1(plan)
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    source_package = Path(p1_source_package).resolve()
    if _sha256_file(source_package / "main.py") != BASE_SOURCE_SHA256 or not (source_package / "cg").is_dir():
        raise SeatConditionedPlanError("P1 source package is not the immutable runtime")
    catalog = load_card_catalog_v1(plan_data["card_database"])
    forbidden = scan_public_canonical_hashes_v1(plan_data["public_scan_roots"])
    output_root.mkdir(parents=True, exist_ok=False)
    deck_roots = output_root / "deck-generation"
    package_roots = output_root / "packages"
    manifest_roots = output_root / "generation-manifests"
    deck_roots.mkdir()
    package_roots.mkdir()
    manifest_roots.mkdir()

    recipes = {str(item["id"]): item for item in plan_data["deck_recipes"]}
    deck_results: dict[str, dict[str, object]] = {}
    for recipe_id, recipe in recipes.items():
        deck_results[recipe_id] = generate_deck_v1(
            output=deck_roots / recipe_id,
            card_db=plan_data["card_database"],
            spec=recipe["spec"],
            source_package=source_package,
            public_scan_roots=plan_data["public_scan_roots"],
            seed=recipe["seed"],
            ordinal=recipe["ordinal"],
        )

    packages: list[Path] = []
    manifests: list[Path] = []
    identities: list[dict[str, object]] = []
    seen_decks: set[str] = set()
    seen_policies: set[str] = set()
    for variant in plan_data["policy_variants"]:
        variant_id = str(variant["id"])
        recipe = recipes[str(variant["deck_recipe_id"])]
        deck_result = deck_results[str(recipe["id"])]
        deck_hash = str(deck_result["canonical_deck_sha256"])
        if deck_hash in seen_decks:
            raise SeatConditionedPlanError(f"factorial deck collision: {variant_id}")
        seen_decks.add(deck_hash)
        config = SeatConditionedConfig.from_mapping(dict(variant["config"]))
        candidate_id = f"self-owned-cg-seat-conditioned-{variant_id}-{config.config_sha256()[:12]}"
        package = package_roots / variant_id
        package_manifest = materialize_seat_conditioned_package(
            source_package=source_package,
            self_owned_deck_package=deck_roots / str(recipe["id"]) / "package",
            output_package=package,
            config=config,
            candidate_id=candidate_id,
        )
        policy_hash = str(package_manifest["policy_sha256"])
        if policy_hash in seen_policies:
            raise SeatConditionedPlanError(f"factorial policy collision: {variant_id}")
        seen_policies.add(policy_hash)
        body: dict[str, object] = {
            "schema_version": "self-owned-cg-seat-conditioned-source-v1",
            "status": "COMPLETE",
            "source_epoch": plan_data["source_epoch"],
            "seed_namespace": plan_data["seed_namespace"],
            "variant_id": variant_id,
            "candidate_id": candidate_id,
            "deck_recipe_id": recipe["id"],
            "deck_generation_manifest_path": str(deck_roots / str(recipe["id"]) / "generation_manifest.json"),
            "deck_generation_manifest_sha256": _sha256_file(deck_roots / str(recipe["id"]) / "generation_manifest.json"),
            "card_database_path": plan_data["card_database"],
            "card_database_sha256": catalog.source_sha256,
            "role_spec_path": recipe["spec"],
            "role_spec_sha256": load_self_owned_deck_spec_v1(recipe["spec"]).source_sha256,
            "p1_parent_policy_sha256": BASE_SOURCE_SHA256,
            "config": config.as_dict(),
            "config_sha256": config.config_sha256(),
            "policy_sha256": policy_hash,
            "deck_file_sha256": package_manifest["deck_file_sha256"],
            "canonical_deck_sha256": deck_hash,
            "parent_deck": None,
            "public_parent_read": False,
            "public_scan_hashes": list(forbidden),
            "actor_visible_only": True,
            "hidden_opponent_zones_used": False,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        body["manifest_sha256"] = _sha256_bytes(_canonical_json(body))
        manifest_path = manifest_roots / f"{variant_id}.json"
        _write_canonical(manifest_path, body)
        packages.append(package)
        manifests.append(manifest_path)
        identities.append({
            "variant_id": variant_id,
            "candidate_id": candidate_id,
            "policy_sha256": policy_hash,
            "canonical_deck_sha256": deck_hash,
            "config_sha256": config.config_sha256(),
            "generation_manifest_sha256": _sha256_file(manifest_path),
        })

    batch = materialize_self_owned_cg_meta_batch_v1(
        candidate_packages=tuple(packages),
        output_root=output_root / "staged",
        seed_namespace=str(plan_data["seed_namespace"]),
        generation_manifests=tuple(manifests),
        source_epoch=str(plan_data["source_epoch"]),
        source_kind=SOURCE_KIND_V1,
    )
    epoch: dict[str, object] = {
        "schema_version": PLAN_SCHEMA,
        "status": "STAGED",
        "plan_path": plan_data["path"],
        "plan_sha256": plan_data["plan_sha256"],
        "source_epoch": plan_data["source_epoch"],
        "seed_namespace": plan_data["seed_namespace"],
        "p1_parent_policy_sha256": BASE_SOURCE_SHA256,
        "card_database_sha256": catalog.source_sha256,
        "source_count": len(identities),
        "deck_count": len(seen_decks),
        "policy_count": len(seen_policies),
        "identities": sorted(identities, key=lambda item: str(item["variant_id"])),
        "batch": batch,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
    }
    epoch["manifest_sha256"] = _sha256_bytes(_canonical_json(epoch))
    manifest_path = output_root / "seat_conditioned_manifest.json"
    _write_canonical(manifest_path, epoch)
    return {
        "status": "STAGED",
        "output_root": str(output_root),
        "staged_root": str(output_root / "staged"),
        "seat_conditioned_manifest": str(manifest_path),
        "source_count": len(identities),
        "deck_count": len(seen_decks),
        "policy_count": len(seen_policies),
        "batch_manifest_sha256": batch["batch_manifest_sha256"],
        "research_only": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="enable artifact generation")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--p1-source-package", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_generation_v1(plan=args.plan, output=args.output, p1_source_package=args.p1_source_package)
    except (SeatConditionedPlanError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
