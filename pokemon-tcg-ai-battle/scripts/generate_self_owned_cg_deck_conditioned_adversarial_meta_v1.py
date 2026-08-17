#!/usr/bin/env python3
"""Stage official-card-only, deck-conditioned adversarial cg meta sources.

Each source is a fresh self-owned deck generated from the official card CSV and
an immutable role specification.  A bounded P1 parameterized policy is then
re-rendered and rebound to that deck.  The source policy is never copied from
an external kernel and the command stops at a research-only staged batch;
runtime smoke, promotion, split sealing, and performance CEM remain separate
gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    P1ParameterConfig,
    render_parameterized_source,
)
from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (  # noqa: E402
    canonical_deck_sha256_v1,
    load_card_catalog_v1,
    load_self_owned_deck_spec_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    PACKAGE_SCHEMA_VERSION_V1,
    SelfOwnedCgPackageV1Error,
    _canonical_json,
    _patch_root_deck_constant,
    _prepare_empty_root,
    _runtime_file_hashes,
    _semantic_sha,
    _sha256_file,
    _write_exclusive,
    _regular_tree,
    verify_self_owned_cg_package_v1,
)
from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (  # noqa: E402
    SOURCE_KIND_V1,
    materialize_self_owned_cg_meta_batch_v1,
)
from scripts.generate_self_owned_cg_deck_v1 import (  # noqa: E402
    run_generation_v1 as generate_deck_v1,
    scan_public_canonical_hashes_v1,
)


PLAN_SCHEMA = "self-owned-cg-deck-conditioned-adversarial-factorial-plan-v1"
GENERATION_SCHEMA = "self-owned-cg-deck-conditioned-adversarial-source-v1"
SOURCE_KIND = "self_owned_official_card_data_deck_with_p1_adversarial_policy"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}


class DeckConditionedAdversarialPlanError(ValueError):
    """Raised when a source plan cannot be represented safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DeckConditionedAdversarialPlanError("value is not canonical JSON") from exc


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise DeckConditionedAdversarialPlanError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, payload: Mapping[str, object]) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(payload) + b"\n"
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeckConditionedAdversarialPlanError(f"cannot read plan: {path}") from exc
    if not isinstance(value, Mapping):
        raise DeckConditionedAdversarialPlanError("plan root must be an object")
    return value


def _repo_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DeckConditionedAdversarialPlanError(f"{field} must be a non-empty path")
    path = Path(value)
    return (path if path.is_absolute() else _ROOT / path).resolve()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        for char in value
    ):
        raise DeckConditionedAdversarialPlanError(f"{field} must be an ASCII identifier")
    return value


def load_deck_conditioned_adversarial_plan_v1(path: str | Path) -> dict[str, object]:
    """Load and normalize a content-addressed factorial plan."""

    plan_path = Path(path).resolve()
    raw = dict(_read_json(plan_path))
    expected = {
        "schema_version",
        "source_epoch",
        "seed_namespace",
        "card_database",
        "p1_source_package",
        "public_scan_roots",
        "deck_recipes",
        "policy_variants",
    }
    if set(raw) != expected or raw.get("schema_version") != PLAN_SCHEMA:
        raise DeckConditionedAdversarialPlanError("plan schema or fields are invalid")
    for field in ("source_epoch", "seed_namespace"):
        if not isinstance(raw[field], str) or not raw[field].strip():
            raise DeckConditionedAdversarialPlanError(f"{field} must be non-empty")
    card_database = _repo_path(raw["card_database"], field="card_database")
    source_package = _repo_path(raw["p1_source_package"], field="p1_source_package")
    if not card_database.is_file():
        raise DeckConditionedAdversarialPlanError(f"card database is missing: {card_database}")
    if not source_package.is_dir():
        raise DeckConditionedAdversarialPlanError(f"P1 source package is missing: {source_package}")

    roots = raw["public_scan_roots"]
    if not isinstance(roots, list) or not roots:
        raise DeckConditionedAdversarialPlanError("public_scan_roots must be non-empty")
    scan_roots: list[str] = []
    for index, value in enumerate(roots):
        root = _repo_path(value, field=f"public_scan_roots[{index}]")
        if not root.is_dir():
            raise DeckConditionedAdversarialPlanError(f"public scan root is missing: {root}")
        scan_roots.append(str(root))

    recipes_raw = raw["deck_recipes"]
    if not isinstance(recipes_raw, list) or not recipes_raw:
        raise DeckConditionedAdversarialPlanError("deck_recipes must be non-empty")
    recipes: dict[str, dict[str, object]] = {}
    for index, item in enumerate(recipes_raw):
        if not isinstance(item, Mapping) or set(item) != {"id", "spec", "seed", "ordinal"}:
            raise DeckConditionedAdversarialPlanError(f"deck_recipes[{index}] has invalid fields")
        recipe_id = _identifier(item["id"], f"deck_recipes[{index}].id")
        if recipe_id in recipes:
            raise DeckConditionedAdversarialPlanError(f"duplicate deck recipe: {recipe_id}")
        if type(item["seed"]) is not int or type(item["ordinal"]) is not int or item["ordinal"] < 0:
            raise DeckConditionedAdversarialPlanError(f"recipe {recipe_id} has invalid seed/ordinal")
        spec = _repo_path(item["spec"], field=f"deck_recipes[{index}].spec")
        if not spec.is_file():
            raise DeckConditionedAdversarialPlanError(f"deck spec is missing: {spec}")
        recipes[recipe_id] = {
            "id": recipe_id,
            "spec": str(spec),
            "seed": int(item["seed"]),
            "ordinal": int(item["ordinal"]),
        }

    variants_raw = raw["policy_variants"]
    if not isinstance(variants_raw, list) or not variants_raw:
        raise DeckConditionedAdversarialPlanError("policy_variants must be non-empty")
    variants: dict[str, dict[str, object]] = {}
    for index, item in enumerate(variants_raw):
        if not isinstance(item, Mapping) or set(item) != {"id", "deck_recipe_id", "overrides"}:
            raise DeckConditionedAdversarialPlanError(f"policy_variants[{index}] has invalid fields")
        variant_id = _identifier(item["id"], f"policy_variants[{index}].id")
        recipe_id = _identifier(item["deck_recipe_id"], f"policy_variants[{index}].deck_recipe_id")
        if variant_id in variants:
            raise DeckConditionedAdversarialPlanError(f"duplicate policy variant: {variant_id}")
        if recipe_id not in recipes:
            raise DeckConditionedAdversarialPlanError(f"unknown deck recipe: {recipe_id}")
        overrides = item["overrides"]
        if not isinstance(overrides, Mapping):
            raise DeckConditionedAdversarialPlanError(f"variant {variant_id} overrides must be an object")
        try:
            config = P1ParameterConfig.from_mapping(dict(overrides))
        except (TypeError, ValueError) as exc:
            raise DeckConditionedAdversarialPlanError(f"variant {variant_id} config is invalid") from exc
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
        "p1_source_package": str(source_package),
        "public_scan_roots": tuple(scan_roots),
        "deck_recipes": tuple(recipes.values()),
        "policy_variants": tuple(variants.values()),
    }


def _materialize_deck_conditioned_package(
    *,
    source_package: Path,
    deck_package: Path,
    output_package: Path,
    config: P1ParameterConfig,
    candidate_id: str,
) -> dict[str, object]:
    if output_package.exists() or output_package.is_symlink():
        raise SelfOwnedCgPackageV1Error(f"output package exists: {output_package}")
    source_main = source_package / "main.py"
    source_cg = source_package / "cg"
    if _sha256_file(source_main) != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("source policy SHA does not match sealed P1")
    _regular_tree(source_cg)
    deck_manifest = verify_self_owned_cg_package_v1(deck_package)
    deck_path = deck_package / "deck.csv"
    deck_bytes = deck_path.read_bytes()
    try:
        card_ids = tuple(int(token) for token in deck_bytes.decode("utf-8").split())
    except (UnicodeDecodeError, ValueError) as exc:
        raise SelfOwnedCgPackageV1Error("deck package is not parseable") from exc
    canonical = canonical_deck_sha256_v1(card_ids)
    if canonical != deck_manifest.get("canonical_deck_sha256"):
        raise SelfOwnedCgPackageV1Error("deck package canonical hash is inconsistent")
    config.validate()
    rendered = render_parameterized_source(config, candidate_id=candidate_id, source_path=source_main)
    patched = _patch_root_deck_constant(rendered, card_ids)
    if patched == rendered:
        raise SelfOwnedCgPackageV1Error("deck binding did not change ROOT_DECK")
    _prepare_empty_root(output_package)
    _copy_target = output_package / "cg"
    shutil.copytree(source_cg, _copy_target, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _write_exclusive(output_package / "main.py", patched.encode("utf-8"))
    _write_exclusive(output_package / "deck.csv", deck_bytes)
    runtime_files = {f"cg/{name}": digest for name, digest in _runtime_file_hashes(_copy_target).items()}
    payload: dict[str, object] = {
        "schema_version": PACKAGE_SCHEMA_VERSION_V1,
        "candidate_id": candidate_id,
        "archetype_id": deck_manifest.get("archetype_id", "self-owned-cg"),
        "parent_deck": None,
        "public_parent_read": False,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_file(output_package / "main.py"),
        "deck_file_sha256": _sha256_file(output_package / "deck.csv"),
        "canonical_deck_sha256": canonical,
        "root_deck_replaced": True,
        "runtime_files": runtime_files,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
    }
    payload["manifest_sha256"] = _semantic_sha(payload)
    _write_exclusive(output_package / "self_owned_cg_package_manifest.json", _canonical_json(payload))
    return verify_self_owned_cg_package_v1(output_package)


def run_generation_v1(*, plan: str | Path, output: str | Path) -> dict[str, object]:
    plan_data = load_deck_conditioned_adversarial_plan_v1(plan)
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root exists: {output_root}")
    source_package = Path(str(plan_data["p1_source_package"])).resolve()
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
            spec=str(recipe["spec"]),
            source_package=source_package,
            public_scan_roots=plan_data["public_scan_roots"],
            seed=int(recipe["seed"]),
            ordinal=int(recipe["ordinal"]),
        )
    seen_decks: set[str] = set()
    seen_policies: set[str] = set()
    packages: list[Path] = []
    manifests: list[Path] = []
    identities: list[dict[str, object]] = []
    for variant in plan_data["policy_variants"]:
        variant_id = str(variant["id"])
        recipe_id = str(variant["deck_recipe_id"])
        deck_result = deck_results[recipe_id]
        deck_hash = str(deck_result["canonical_deck_sha256"])
        if deck_hash in seen_decks:
            raise DeckConditionedAdversarialPlanError(f"deck collision in variant: {variant_id}")
        seen_decks.add(deck_hash)
        config = P1ParameterConfig.from_mapping(dict(variant["config"]))
        candidate_id = f"self-owned-cg-deck-conditioned-adversarial-{variant_id}-{config.config_sha256()[:12]}"
        package = package_roots / variant_id
        package_manifest = _materialize_deck_conditioned_package(
            source_package=source_package,
            deck_package=deck_roots / recipe_id / "package",
            output_package=package,
            config=config,
            candidate_id=candidate_id,
        )
        policy_sha = str(package_manifest["policy_sha256"])
        if policy_sha in seen_policies:
            raise DeckConditionedAdversarialPlanError(f"policy collision in variant: {variant_id}")
        seen_policies.add(policy_sha)
        body: dict[str, object] = {
            "schema_version": GENERATION_SCHEMA,
            "status": "COMPLETE",
            "source_epoch": plan_data["source_epoch"],
            "seed_namespace": plan_data["seed_namespace"],
            "variant_id": variant_id,
            "candidate_id": candidate_id,
            "deck_recipe_id": recipe_id,
            "deck_generation_manifest_path": str(deck_roots / recipe_id / "generation_manifest.json"),
            "deck_generation_manifest_sha256": _sha256_file(deck_roots / recipe_id / "generation_manifest.json"),
            "card_database_path": plan_data["card_database"],
            "card_database_sha256": catalog.source_sha256,
            "role_spec_path": recipes[recipe_id]["spec"],
            "role_spec_sha256": load_self_owned_deck_spec_v1(recipes[recipe_id]["spec"]).source_sha256,
            "p1_parent_policy_sha256": BASE_SOURCE_SHA256,
            "config": config.as_dict(),
            "config_sha256": config.config_sha256(),
            "policy_sha256": policy_sha,
            "deck_file_sha256": package_manifest["deck_file_sha256"],
            "canonical_deck_sha256": deck_hash,
            "parent_deck": None,
            "public_parent_read": False,
            "public_scan_hashes": list(forbidden),
            "actor_visible_only": True,
            "hidden_opponent_zones_used": False,
            "source_kind": SOURCE_KIND,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        body["manifest_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
        manifest_path = manifest_roots / f"{variant_id}.json"
        _write_new(manifest_path, body)
        packages.append(package)
        manifests.append(manifest_path)
        identities.append(
            {
                "variant_id": variant_id,
                "candidate_id": candidate_id,
                "policy_sha256": policy_sha,
                "canonical_deck_sha256": deck_hash,
                "config_sha256": config.config_sha256(),
                "generation_manifest_sha256": _sha256_file(manifest_path),
            }
        )
    batch = materialize_self_owned_cg_meta_batch_v1(
        candidate_packages=tuple(packages),
        output_root=output_root / "staged",
        seed_namespace=str(plan_data["seed_namespace"]),
        generation_manifests=tuple(manifests),
        source_epoch=str(plan_data["source_epoch"]),
        source_kind=SOURCE_KIND,
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
        "source_kind": SOURCE_KIND,
        "source_count": len(identities),
        "deck_count": len(seen_decks),
        "policy_count": len(seen_policies),
        "identities": sorted(identities, key=lambda item: str(item["variant_id"])),
        "batch": batch,
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
    }
    epoch["manifest_sha256"] = hashlib.sha256(_canonical(epoch)).hexdigest()
    manifest_path = output_root / "deck_conditioned_adversarial_manifest.json"
    _write_new(manifest_path, epoch)
    return {
        "status": "STAGED",
        "output_root": str(output_root),
        "staged_root": str(output_root / "staged"),
        "manifest": str(manifest_path),
        "source_count": len(identities),
        "deck_count": len(seen_decks),
        "policy_count": len(seen_policies),
        "pool_manifest_sha256": batch["pool_manifest_sha256"],
        "batch_manifest_sha256": batch["batch_manifest_sha256"],
        "research_only": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_generation_v1(plan=args.plan, output=args.output)
    except (DeckConditionedAdversarialPlanError, SelfOwnedCgPackageV1Error, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

