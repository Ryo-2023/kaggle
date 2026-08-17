"""Materialize bounded Rocket family-to-specialist-theta route variants.

This lane changes only the value names in the sealed ``_SPECIALIST_THETA``
dictionary.  It deliberately leaves the public-card classifier, commit state,
theta tables, deck, imports, and runtime behavior outside that dictionary
byte-for-byte unchanged.  Outputs are research-only local meta sources.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from scripts.build_historical_meta_split_v1 import build_historical_meta_split_v1
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1

from .derived_internal_meta_v1 import (
    DerivedInternalMetaError,
    _artifact_hits,
    _canonical_json,
    _existing_policy_hashes,
    _read_base_source,
    _sha256_bytes,
    _sha256_file,
    _static_findings,
    _write_json_new,
    _write_new,
)


ROCKET_SPECIALIST_ROUTE_META_SCHEMA_V1 = "meta-specialist-cg-rocket-specialist-route-meta-v1"
ROCKET_SPECIALIST_ROUTE_SOURCE_V1 = "internal_agents_rocket_specialist_route_derived_v1"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"

_ROUTE_KEYS = ("A01", "A09", "A07", "A11")
_ALLOWED_THETA_NAMES = frozenset(
    {
        "_THETA_GENERAL",
        "_THETA_LUCMIX",
        "_THETA_A09_MERGED",
        "_THETA_A07_MERGED",
        "_THETA_ABOMASNOW_R2",
    }
)
_BASE_ROUTE = {
    "A01": "_THETA_LUCMIX",
    "A09": "_THETA_A09_MERGED",
    "A07": "_THETA_A07_MERGED",
    "A11": "_THETA_ABOMASNOW_R2",
}

_ROUTE_VARIANT_MAPS: dict[str, dict[str, str]] = {
    "A01_GENERAL": {**_BASE_ROUTE, "A01": "_THETA_GENERAL"},
    "A09_GENERAL": {**_BASE_ROUTE, "A09": "_THETA_GENERAL"},
    "A07_GENERAL": {**_BASE_ROUTE, "A07": "_THETA_GENERAL"},
    "A11_GENERAL": {**_BASE_ROUTE, "A11": "_THETA_GENERAL"},
    "A01_A09_GENERAL": {
        **_BASE_ROUTE,
        "A01": "_THETA_GENERAL",
        "A09": "_THETA_GENERAL",
    },
    "A07_A11_GENERAL": {
        **_BASE_ROUTE,
        "A07": "_THETA_GENERAL",
        "A11": "_THETA_GENERAL",
    },
    "GENERAL_ONLY": {key: "_THETA_GENERAL" for key in _ROUTE_KEYS},
    "A01_A07_LUCMIX": {
        **_BASE_ROUTE,
        "A07": "_THETA_LUCMIX",
    },
    "A09_A11_A09MERGED": {
        **_BASE_ROUTE,
        "A11": "_THETA_A09_MERGED",
    },
    "SWAP_A01_A09": {
        **_BASE_ROUTE,
        "A01": "_THETA_A09_MERGED",
        "A09": "_THETA_LUCMIX",
    },
    "SWAP_A07_A11": {
        **_BASE_ROUTE,
        "A07": "_THETA_ABOMASNOW_R2",
        "A11": "_THETA_A07_MERGED",
    },
    "ROTATE_A01_A09_A07_A11": {
        "A01": "_THETA_A09_MERGED",
        "A09": "_THETA_A07_MERGED",
        "A07": "_THETA_ABOMASNOW_R2",
        "A11": "_THETA_LUCMIX",
    },
}
ROCKET_SPECIALIST_ROUTE_VARIANTS_V1 = tuple(_ROUTE_VARIANT_MAPS)
SUPPORTED_SPLITS_V1 = ("META_TRAIN", "META_DEV", "META_FINAL")

_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ROOT = Path(__file__).resolve().parents[3]


class RocketSpecialistRouteMetaError(DerivedInternalMetaError):
    """Raised when Rocket specialist route materialization is unsafe."""


@dataclass(frozen=True, slots=True)
class _RouteLiteral:
    key: str
    name: str
    start: int
    end: int


def _byte_offsets(source: bytes) -> tuple[int, ...]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return tuple(offsets)


def _node_span(node: ast.AST, offsets: tuple[int, ...]) -> tuple[int, int]:
    if (
        node.lineno is None
        or node.end_lineno is None
        or node.col_offset is None
        or node.end_col_offset is None
    ):
        raise RocketSpecialistRouteMetaError("route value has no source span")
    try:
        return (
            offsets[node.lineno - 1] + node.col_offset,
            offsets[node.end_lineno - 1] + node.end_col_offset,
        )
    except IndexError as exc:
        raise RocketSpecialistRouteMetaError("route value source span is invalid") from exc


def _extract_specialist_route(source: bytes) -> tuple[_RouteLiteral, ...]:
    try:
        text = source.decode("utf-8", errors="strict")
        tree = ast.parse(text)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RocketSpecialistRouteMetaError("Rocket source is not valid UTF-8 Python") from exc

    offsets = _byte_offsets(source)
    matches: list[ast.Assign] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_SPECIALIST_THETA"
        ):
            matches.append(node)
    if len(matches) != 1:
        raise RocketSpecialistRouteMetaError(
            f"expected exactly one _SPECIALIST_THETA assignment, found {len(matches)}"
        )

    value = matches[0].value
    if not isinstance(value, ast.Dict) or any(key is None for key in value.keys):
        raise RocketSpecialistRouteMetaError("_SPECIALIST_THETA must be a literal dictionary")
    if len(value.keys) != len(_ROUTE_KEYS):
        raise RocketSpecialistRouteMetaError("_SPECIALIST_THETA keys must contain exactly four entries")

    entries: list[_RouteLiteral] = []
    seen: set[str] = set()
    for key_node, value_node in zip(value.keys, value.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            raise RocketSpecialistRouteMetaError("_SPECIALIST_THETA keys must be string literals")
        key = key_node.value
        if key in seen:
            raise RocketSpecialistRouteMetaError(f"duplicate _SPECIALIST_THETA key: {key}")
        seen.add(key)
        if not isinstance(value_node, ast.Name):
            raise RocketSpecialistRouteMetaError(
                f"_SPECIALIST_THETA value for {key} must be a Name reference"
            )
        if value_node.id not in _ALLOWED_THETA_NAMES:
            raise RocketSpecialistRouteMetaError(
                f"_SPECIALIST_THETA value for {key} is not an allowed theta name: {value_node.id}"
            )
        start, end = _node_span(value_node, offsets)
        if source[start:end] != value_node.id.encode("utf-8"):
            raise RocketSpecialistRouteMetaError("route value span does not match source token")
        entries.append(_RouteLiteral(key=key, name=value_node.id, start=start, end=end))

    if set(seen) != set(_ROUTE_KEYS):
        raise RocketSpecialistRouteMetaError(
            f"_SPECIALIST_THETA keys mismatch; expected={list(_ROUTE_KEYS)} got={sorted(seen)}"
        )
    return tuple(sorted(entries, key=lambda item: _ROUTE_KEYS.index(item.key)))


def _route_map(source: bytes) -> dict[str, str]:
    return {item.key: item.name for item in _extract_specialist_route(source)}


def _transform_specialist_route(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact route map by replacing only theta Name tokens."""

    variant = str(variant)
    if variant not in _ROUTE_VARIANT_MAPS:
        raise RocketSpecialistRouteMetaError(f"unsupported Rocket specialist route variant: {variant}")
    entries = _extract_specialist_route(source)
    route = _ROUTE_VARIANT_MAPS[variant]
    replacements: list[tuple[int, int, bytes]] = []
    for entry in entries:
        replacements.append((entry.start, entry.end, route[entry.key].encode("ascii")))
    transformed = source
    for start, end, replacement in sorted(replacements, reverse=True):
        transformed = transformed[:start] + replacement + transformed[end:]
    if transformed == source:
        raise RocketSpecialistRouteMetaError(f"Rocket specialist route transform was a no-op: {variant}")
    transformed_route = _route_map(transformed)
    if transformed_route != route:
        raise RocketSpecialistRouteMetaError(
            f"transformed route mismatch for {variant}: {transformed_route} != {route}"
        )
    return transformed, f"ROCKET_SPECIALIST_ROUTE_V1:{variant}"


def _normalize_split(
    variants: Sequence[str], split_by_variant: Mapping[str, str]
) -> dict[str, str]:
    ordered = [str(item) for item in variants]
    if len(ordered) != len(ROCKET_SPECIALIST_ROUTE_VARIANTS_V1) or len(set(ordered)) != len(ordered):
        raise RocketSpecialistRouteMetaError("exactly twelve unique route variants are required")
    if set(ordered) != set(ROCKET_SPECIALIST_ROUTE_VARIANTS_V1):
        raise RocketSpecialistRouteMetaError("variant list does not match the sealed route recipe set")
    if not isinstance(split_by_variant, Mapping) or set(split_by_variant) != set(ordered):
        raise RocketSpecialistRouteMetaError("split_by_variant must cover every route variant exactly")
    normalized = {variant: str(split_by_variant[variant]).upper() for variant in ordered}
    if any(split not in SUPPORTED_SPLITS_V1 for split in normalized.values()):
        raise RocketSpecialistRouteMetaError("unknown META split")
    counts = {split: sum(value == split for value in normalized.values()) for split in SUPPORTED_SPLITS_V1}
    if counts != {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}:
        raise RocketSpecialistRouteMetaError(f"route split must be 8/2/2, got {counts}")
    return normalized


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _source_note(*, target: Path, base, policy_sha: str, recipe: str, variant: str, split: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Rocket specialist route meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- localization patch: `{base.localization_patch}` (preserved)\n"
            f"- source family: `rocket_specialist_route_v1`\n"
            f"- variant: `{variant}`\n"
            f"- split: `{split}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- runtime change scope: `_SPECIALIST_THETA` value names only\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_rocket_specialist_route_meta_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    split_by_variant: Mapping[str, str],
    variants: Sequence[str] = ROCKET_SPECIALIST_ROUTE_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal route variants with no-clobber and explicit split reservations."""

    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Rocket specialist route root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise RocketSpecialistRouteMetaError("source_epoch and seed_namespace must be non-empty")
    ordered_variants = tuple(str(item) for item in variants)
    normalized_split = _normalize_split(ordered_variants, split_by_variant)
    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise RocketSpecialistRouteMetaError("P1 package must contain main.py and deck.csv")

    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, base_imports, base_environment_keys = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise RocketSpecialistRouteMetaError(f"base policy is not statically safe: {findings}")
    if current_pool_manifest is not None:
        existing_hashes = _existing_policy_hashes(Path(current_pool_manifest).resolve())
    else:
        existing_hashes = set()
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    for variant in ordered_variants:
        policy_bytes, recipe = _transform_specialist_route(source_bytes, variant)
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise RocketSpecialistRouteMetaError(f"route policy identity is already used: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise RocketSpecialistRouteMetaError(f"route policy identity appears in artifacts: {variant}")
        transformed_findings, imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise RocketSpecialistRouteMetaError(
                f"derived route policy is not statically safe: {variant}: {transformed_findings}"
            )
        candidate_id = (
            f"derived_{base.candidate_id}_rocket_route_{variant.lower()}_{policy_sha[:12]}"
        )
        prepared.append(
            {
                "variant": variant,
                "split": normalized_split[variant],
                "policy_bytes": policy_bytes,
                "policy_sha": policy_sha,
                "recipe": recipe,
                "candidate_id": candidate_id,
                "imports": tuple(imports or base_imports),
                "environment_keys": tuple(environment_keys or base_environment_keys),
            }
        )
        prepared_hashes.add(policy_sha)

    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    for item in prepared:
        target = output / str(item["candidate_id"])
        target.mkdir(parents=True, exist_ok=False)
        _write_new(target / "main.py", bytes(item["policy_bytes"]))
        _write_new(target / "deck.csv", deck_bytes)
        _source_note(
            target=target,
            base=base,
            policy_sha=str(item["policy_sha"]),
            recipe=str(item["recipe"]),
            variant=str(item["variant"]),
            split=str(item["split"]),
        )
        rows.append(
            {
                "id": str(item["candidate_id"]),
                "policy_hash": str(item["policy_sha"]),
                "source_policy_sha256": base.source_policy_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source": ROCKET_SPECIALIST_ROUTE_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "smoke_ok": True,
                "derived": True,
                "source_family": "rocket_specialist_route_v1",
                "source_label": str(item["variant"]),
                "split": str(item["split"]),
                "observation_boundary": "visible_state_only",
                "derivation_recipe": str(item["recipe"]),
                "asset_preflight": "STATIC_AND_EXACT_60",
            }
        )
        evidence.append(
            {
                "candidate_id": str(item["candidate_id"]),
                "fresh": True,
                "unused_before_run": True,
                "derived": True,
                "source": ROCKET_SPECIALIST_ROUTE_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source_family": "rocket_specialist_route_v1",
                "source_label": str(item["variant"]),
                "split": str(item["split"]),
                "derivation_recipe": str(item["recipe"]),
                "observation_boundary": "visible_state_only",
                "imports": list(item["imports"]),
                "environment_keys": list(item["environment_keys"]),
                "static_findings": [],
            }
        )

    pool_path = output / "pool_manifest.json"
    _write_json_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    evidence_dir = output / "evidence"
    for item in evidence:
        _write_json_new(evidence_dir / f"{item['candidate_id']}.json", item)

    references = []
    for item in evidence:
        evidence_path = evidence_dir / f"{item['candidate_id']}.json"
        references.append(
            {
                "id": item["candidate_id"],
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256_file(evidence_path),
                "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
                "policy_sha256": item["policy_sha256"],
                "canonical_deck_hash": item["canonical_deck_hash"],
                "source": item["source"],
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "derived": True,
                "derivation_recipe": item["derivation_recipe"],
                "source_family": item["source_family"],
                "split": item["split"],
            }
        )
    reference_ids = [str(item["candidate_id"]) for item in prepared]
    seed_plan_sha = _sha256_bytes(
        _canonical_json(
            {
                "source_epoch": source_epoch,
                "seed_namespace": seed_namespace,
                "source_commit": base.source_commit,
                "reference_ids": reference_ids,
                "variants": [item["variant"] for item in prepared],
                "splits": [item["split"] for item in prepared],
            }
        )
    )
    fresh_payload = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"rocket-specialist-route-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new policy SHA from bounded _SPECIALIST_THETA route materialization; current pool and configured artifact identity scan",
        "references": references,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh_payload)
    split_report = build_historical_meta_split_v1(
        pool_root=output,
        fresh_meta_path=fresh_path,
        p1_package=p1,
        train_ids=[str(item["candidate_id"]) for item in prepared if item["split"] == "META_TRAIN"],
        dev_ids=[str(item["candidate_id"]) for item in prepared if item["split"] == "META_DEV"],
        final_ids=[str(item["candidate_id"]) for item in prepared if item["split"] == "META_FINAL"],
    )
    report = {
        "schema_version": ROCKET_SPECIALIST_ROUTE_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "source_commit": base.source_commit,
        "source_family": "rocket_specialist_route_v1",
        "variants": [item["variant"] for item in prepared],
        "accepted_count": len(rows),
        "accepted_ids": reference_ids,
        "pool_manifest_path": str(pool_path),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "split_path": split_report["split_path"],
        "split_sha256": split_report["split_sha256"],
        "meta_manifest_path": split_report["meta_manifest_path"],
        "meta_manifest_sha256": split_report["meta_manifest_sha256"],
        "split_counts": {
            split: sum(item["split"] == split for item in prepared) for split in SUPPORTED_SPLITS_V1
        },
        "imports_executed": False,
        "network_access": False,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "research_only": True,
    }
    _write_json_new(output / "intake_report.json", report)
    load_opponent_pool_v1(output)
    return report


__all__ = [
    "ROCKET_SPECIALIST_ROUTE_META_SCHEMA_V1",
    "ROCKET_SPECIALIST_ROUTE_SOURCE_V1",
    "ROCKET_SPECIALIST_ROUTE_VARIANTS_V1",
    "SUPPORTED_SPLITS_V1",
    "RocketSpecialistRouteMetaError",
    "_transform_specialist_route",
    "seal_rocket_specialist_route_meta_v1",
]
