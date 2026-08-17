#!/usr/bin/env python3
"""Generate a fresh official-data-only self-owned deck x policy source epoch.

The plan defines independent official-card-data deck recipes and a bounded
P1 parameter factorial.  Each pair becomes a separate research-only package;
the command only generates and stages artifacts.  Runtime smoke, promotion,
split construction, and CABT performance remain explicit later gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    P1ParameterConfig,
)
from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (  # noqa: E402
    load_card_catalog_v1,
    load_self_owned_deck_spec_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_parameterized_package_v1 import (  # noqa: E402
    materialize_self_owned_cg_parameterized_package_v1,
)
from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (  # noqa: E402
    materialize_self_owned_cg_meta_batch_v1,
)
from scripts.generate_self_owned_cg_deck_v1 import (  # noqa: E402
    run_generation_v1 as generate_deck_v1,
    scan_public_canonical_hashes_v1,
)


SCHEMA = "self-owned-cg-policy-factorial-plan-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}


class SelfOwnedCgPolicyMetaV1Error(ValueError):
    """Raised when a factorial plan is malformed or unsafe."""


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
        raise SelfOwnedCgPolicyMetaV1Error("value is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SelfOwnedCgPolicyMetaV1Error(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedCgPolicyMetaV1Error(f"cannot read plan: {path}") from exc
    if not isinstance(value, Mapping):
        raise SelfOwnedCgPolicyMetaV1Error("plan root must be an object")
    return value


def _resolve_repo_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SelfOwnedCgPolicyMetaV1Error(f"{field} must be a non-empty path")
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (_ROOT / path).resolve()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        for char in value
    ):
        raise SelfOwnedCgPolicyMetaV1Error(f"{field} must be an ASCII identifier")
    return value


def load_factorial_plan_v1(path: str | Path) -> dict[str, object]:
    """Load and validate a content-addressable factorial plan."""

    plan_path = Path(path).resolve()
    raw = dict(_read_json(plan_path))
    expected = {
        "schema_version",
        "source_epoch",
        "seed_namespace",
        "card_database",
        "public_scan_roots",
        "deck_recipes",
        "policy_variants",
    }
    if set(raw) != expected or raw["schema_version"] != SCHEMA:
        raise SelfOwnedCgPolicyMetaV1Error("plan schema or fields are invalid")
    for field in ("source_epoch", "seed_namespace"):
        if not isinstance(raw[field], str) or not raw[field].strip():
            raise SelfOwnedCgPolicyMetaV1Error(f"{field} must be non-empty")
    _resolve_repo_path(raw["card_database"], field="card_database")
    scan_roots = raw["public_scan_roots"]
    if not isinstance(scan_roots, list) or not scan_roots:
        raise SelfOwnedCgPolicyMetaV1Error("public_scan_roots must be non-empty")
    for index, value in enumerate(scan_roots):
        _resolve_repo_path(value, field=f"public_scan_roots[{index}]")

    recipes = raw["deck_recipes"]
    if not isinstance(recipes, list) or not recipes:
        raise SelfOwnedCgPolicyMetaV1Error("deck_recipes must be non-empty")
    recipes_by_id: dict[str, dict[str, object]] = {}
    for index, item in enumerate(recipes):
        if not isinstance(item, Mapping) or set(item) != {"id", "spec", "seed", "ordinal"}:
            raise SelfOwnedCgPolicyMetaV1Error(f"deck_recipes[{index}] has invalid fields")
        recipe_id = _identifier(item["id"], f"deck_recipes[{index}].id")
        if recipe_id in recipes_by_id:
            raise SelfOwnedCgPolicyMetaV1Error(f"duplicate deck recipe: {recipe_id}")
        if type(item["seed"]) is not int or type(item["ordinal"]) is not int or item["ordinal"] < 0:
            raise SelfOwnedCgPolicyMetaV1Error(f"deck recipe {recipe_id} has invalid seed/ordinal")
        spec_path = _resolve_repo_path(item["spec"], field=f"deck_recipes[{index}].spec")
        if not spec_path.is_file():
            raise SelfOwnedCgPolicyMetaV1Error(f"deck recipe spec is missing: {spec_path}")
        recipes_by_id[recipe_id] = {
            "id": recipe_id,
            "spec": str(spec_path),
            "seed": int(item["seed"]),
            "ordinal": int(item["ordinal"]),
        }

    variants = raw["policy_variants"]
    if not isinstance(variants, list) or not variants:
        raise SelfOwnedCgPolicyMetaV1Error("policy_variants must be non-empty")
    variants_by_id: dict[str, dict[str, object]] = {}
    for index, item in enumerate(variants):
        if not isinstance(item, Mapping) or set(item) != {"id", "deck_recipe_id", "overrides"}:
            raise SelfOwnedCgPolicyMetaV1Error(f"policy_variants[{index}] has invalid fields")
        variant_id = _identifier(item["id"], f"policy_variants[{index}].id")
        if variant_id in variants_by_id:
            raise SelfOwnedCgPolicyMetaV1Error(f"duplicate policy variant: {variant_id}")
        recipe_id = _identifier(item["deck_recipe_id"], f"policy_variants[{index}].deck_recipe_id")
        if recipe_id not in recipes_by_id:
            raise SelfOwnedCgPolicyMetaV1Error(f"variant references unknown deck recipe: {recipe_id}")
        overrides = item["overrides"]
        if not isinstance(overrides, Mapping):
            raise SelfOwnedCgPolicyMetaV1Error(f"variant {variant_id} overrides must be an object")
        try:
            config = P1ParameterConfig.from_mapping(dict(overrides))
        except (TypeError, ValueError) as exc:
            raise SelfOwnedCgPolicyMetaV1Error(f"variant {variant_id} has invalid overrides") from exc
        variants_by_id[variant_id] = {
            "id": variant_id,
            "deck_recipe_id": recipe_id,
            "overrides": dict(config.as_dict()),
            "config_sha256": config.config_sha256(),
        }
    return {
        "path": str(plan_path),
        "plan_sha256": _sha256_file(plan_path),
        "schema_version": SCHEMA,
        "source_epoch": str(raw["source_epoch"]),
        "seed_namespace": str(raw["seed_namespace"]),
        "card_database": str(_resolve_repo_path(raw["card_database"], field="card_database")),
        "public_scan_roots": [
            str(_resolve_repo_path(value, field="public_scan_root")) for value in scan_roots
        ],
        "deck_recipes": tuple(recipes_by_id.values()),
        "policy_variants": tuple(variants_by_id.values()),
    }


def _write_canonical(path: Path, payload: Mapping[str, object]) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(payload) + b"\n"
    path.write_bytes(raw)
    return _sha256_bytes(raw)


def run_generation_v1(
    *,
    plan: str | Path,
    output: str | Path,
    p1_source_package: str | Path,
) -> dict[str, object]:
    """Generate all factorial packages and a staged source batch."""

    plan_data = load_factorial_plan_v1(plan)
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    source_package = Path(p1_source_package).resolve()
    source_main = source_package / "main.py"
    source_cg = source_package / "cg"
    if _sha256_file(source_main) != BASE_SOURCE_SHA256 or not source_cg.is_dir():
        raise SelfOwnedCgPolicyMetaV1Error("p1 source package is not the immutable P1 runtime")

    catalog = load_card_catalog_v1(plan_data["card_database"])
    forbidden = scan_public_canonical_hashes_v1(plan_data["public_scan_roots"])
    recipe_by_id = {str(row["id"]): row for row in plan_data["deck_recipes"]}
    output_root.mkdir(parents=True, exist_ok=False)
    deck_roots = output_root / "deck-generation"
    package_roots = output_root / "packages"
    manifest_roots = output_root / "generation-manifests"
    deck_roots.mkdir()
    package_roots.mkdir()
    manifest_roots.mkdir()

    packages: list[Path] = []
    generation_manifests: list[Path] = []
    identities: list[dict[str, object]] = []
    seen_decks: set[str] = set()
    seen_policies: set[str] = set()
    for variant in plan_data["policy_variants"]:
        variant_id = str(variant["id"])
        recipe = recipe_by_id[str(variant["deck_recipe_id"])]
        deck_root = deck_roots / str(recipe["id"])
        deck_result = generate_deck_v1(
            output=deck_root,
            card_db=plan_data["card_database"],
            spec=str(recipe["spec"]),
            source_package=source_package,
            public_scan_roots=plan_data["public_scan_roots"],
            seed=int(recipe["seed"]),
            ordinal=int(recipe["ordinal"]),
        )
        deck_canonical = str(deck_result["canonical_deck_sha256"])
        if deck_canonical in seen_decks:
            raise SelfOwnedCgPolicyMetaV1Error(f"factorial deck collision: {variant_id}")
        seen_decks.add(deck_canonical)
        config = P1ParameterConfig.from_mapping(dict(variant["overrides"]))
        candidate_id = f"self-owned-cg-factorial-{variant_id}-{config.config_sha256()[:12]}"
        package_root = package_roots / variant_id
        package_manifest = materialize_self_owned_cg_parameterized_package_v1(
            source_package=source_package,
            self_owned_deck_package=deck_root / "package",
            output_package=package_root,
            config=config,
            candidate_id=candidate_id,
        )
        policy_sha = str(package_manifest["policy_sha256"])
        if policy_sha in seen_policies:
            raise SelfOwnedCgPolicyMetaV1Error(f"factorial policy collision: {variant_id}")
        seen_policies.add(policy_sha)
        generation_body: dict[str, object] = {
            "schema_version": "self-owned-cg-policy-factorial-source-v1",
            "status": "COMPLETE",
            "source_epoch": plan_data["source_epoch"],
            "seed_namespace": plan_data["seed_namespace"],
            "variant_id": variant_id,
            "candidate_id": candidate_id,
            "deck_recipe_id": recipe["id"],
            "deck_generation_manifest_path": str(deck_root / "generation_manifest.json"),
            "deck_generation_manifest_sha256": _sha256_file(deck_root / "generation_manifest.json"),
            "card_database_path": plan_data["card_database"],
            "card_database_sha256": catalog.source_sha256,
            "role_spec_path": recipe["spec"],
            "role_spec_sha256": load_self_owned_deck_spec_v1(recipe["spec"]).source_sha256,
            "p1_parent_policy_sha256": BASE_SOURCE_SHA256,
            "config": config.as_dict(),
            "config_sha256": config.config_sha256(),
            "policy_sha256": policy_sha,
            "deck_file_sha256": package_manifest["deck_file_sha256"],
            "canonical_deck_sha256": package_manifest["canonical_deck_sha256"],
            "parent_deck": None,
            "public_parent_read": False,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        generation_body["manifest_sha256"] = _sha256_bytes(_canonical_json(generation_body))
        generation_path = manifest_roots / f"{variant_id}.json"
        _write_canonical(generation_path, generation_body)
        packages.append(package_root)
        generation_manifests.append(generation_path)
        identities.append(
            {
                "variant_id": variant_id,
                "candidate_id": candidate_id,
                "policy_sha256": policy_sha,
                "canonical_deck_sha256": deck_canonical,
                "config_sha256": config.config_sha256(),
                "generation_manifest_sha256": _sha256_file(generation_path),
            }
        )

    staged_root = output_root / "staged"
    batch = materialize_self_owned_cg_meta_batch_v1(
        candidate_packages=tuple(packages),
        output_root=staged_root,
        seed_namespace=str(plan_data["seed_namespace"]),
        generation_manifests=tuple(generation_manifests),
        source_epoch=str(plan_data["source_epoch"]),
    )
    manifest = {
        "schema_version": SCHEMA,
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
        "identities": sorted(identities, key=lambda row: str(row["variant_id"])),
        "batch": batch,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
    }
    manifest["manifest_sha256"] = _sha256_bytes(_canonical_json(manifest))
    manifest_path = output_root / "factorial_manifest.json"
    _write_canonical(manifest_path, manifest)
    return {
        "status": "STAGED",
        "output_root": str(output_root),
        "staged_root": str(staged_root),
        "factorial_manifest": str(manifest_path),
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
    except (SelfOwnedCgPolicyMetaV1Error, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
