"""Materialize bounded Rocket public-card classifier variants.

Only values in the sealed ``_TIER_A_TO_GROUP`` dictionary are changed.  The
dispatcher, observation boundary, theta tables, deck, imports, environment
reads, and fallback behavior remain byte-for-byte unchanged outside those
literal value tokens.  Every output is research-only and local-evaluation
only.
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


ROCKET_DISPATCH_CLASSIFIER_META_SCHEMA_V1 = (
    "meta-specialist-cg-rocket-dispatch-classifier-meta-v1"
)
ROCKET_DISPATCH_CLASSIFIER_SOURCE_V1 = (
    "internal_agents_rocket_dispatch_classifier_derived_v1"
)
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
SUPPORTED_SPLITS_V1 = ("META_TRAIN", "META_DEV", "META_FINAL")

_CLASSIFIER_KEYS = (
    675,
    676,
    677,
    678,
    646,
    647,
    648,
    741,
    742,
    743,
    721,
    722,
    723,
)
_ALLOWED_GROUPS = frozenset({"A01", "A09", "A07", "A11"})
_BASE_CLASSIFIER = {
    675: "A01",
    676: "A01",
    677: "A01",
    678: "A01",
    646: "A09",
    647: "A09",
    648: "A09",
    741: "A07",
    742: "A07",
    743: "A07",
    721: "A11",
    722: "A11",
    723: "A11",
}

# Each recipe moves only a visible classifier key or a small, semantically
# coherent subset.  The base group is explicit in _BASE_CLASSIFIER, so a
# repeated transform or a source drift is rejected rather than silently
# producing a different lineage.
_CLASSIFIER_RECIPE_CHANGES: dict[str, dict[int, str]] = {
    "A01_ENGINE_TO_A09": {675: "A09", 676: "A09"},
    "A01_LUCARIO_TO_A09": {677: "A09", 678: "A09"},
    "A01_ENGINE_TO_A11": {675: "A11", 676: "A11"},
    "A01_LUCARIO_TO_A11": {677: "A11", 678: "A11"},
    "A09_LINE_TO_A01": {646: "A01", 647: "A01", 648: "A01"},
    "A09_LINE_TO_A07": {646: "A07", 647: "A07", 648: "A07"},
    "A07_LINE_TO_A09": {741: "A09", 742: "A09", 743: "A09"},
    "A07_LINE_TO_A11": {741: "A11", 742: "A11", 743: "A11"},
    "A11_LINE_TO_A07": {721: "A07", 722: "A07", 723: "A07"},
    "A11_LINE_TO_A01": {721: "A01", 722: "A01", 723: "A01"},
    "A01_MIX_ENGINE_LUCARIO": {675: "A09", 677: "A09"},
    "A09_SPLIT_TO_A07": {646: "A07", 648: "A07"},
}
ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1 = tuple(_CLASSIFIER_RECIPE_CHANGES)
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ROOT = Path(__file__).resolve().parents[3]


class RocketDispatchClassifierMetaError(DerivedInternalMetaError):
    """Raised when classifier materialization is unsafe."""


@dataclass(frozen=True, slots=True)
class _ClassifierLiteral:
    key: int
    group: str
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
        raise RocketDispatchClassifierMetaError("classifier value has no source span")
    try:
        return (
            offsets[node.lineno - 1] + node.col_offset,
            offsets[node.end_lineno - 1] + node.end_col_offset,
        )
    except IndexError as exc:
        raise RocketDispatchClassifierMetaError("classifier value source span is invalid") from exc


def _extract_classifier(source: bytes) -> tuple[_ClassifierLiteral, ...]:
    try:
        text = source.decode("utf-8", errors="strict")
        tree = ast.parse(text)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RocketDispatchClassifierMetaError("Rocket source is not valid UTF-8 Python") from exc

    matches: list[ast.Assign] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_TIER_A_TO_GROUP"
        ):
            matches.append(node)
    if len(matches) != 1:
        raise RocketDispatchClassifierMetaError(
            f"expected exactly one _TIER_A_TO_GROUP assignment, found {len(matches)}"
        )

    value = matches[0].value
    if not isinstance(value, ast.Dict) or any(key is None for key in value.keys):
        raise RocketDispatchClassifierMetaError("_TIER_A_TO_GROUP must be a literal dictionary")
    if len(value.keys) != len(_CLASSIFIER_KEYS):
        raise RocketDispatchClassifierMetaError(
            "_TIER_A_TO_GROUP keys must contain exactly thirteen entries"
        )

    entries: list[_ClassifierLiteral] = []
    seen: set[int] = set()
    offsets = _byte_offsets(source)
    for key_node, value_node in zip(value.keys, value.values):
        if not isinstance(key_node, ast.Constant) or type(key_node.value) is not int:
            raise RocketDispatchClassifierMetaError(
                "_TIER_A_TO_GROUP keys must be integer literals"
            )
        key = int(key_node.value)
        if key in seen:
            raise RocketDispatchClassifierMetaError(f"duplicate _TIER_A_TO_GROUP key: {key}")
        seen.add(key)
        if not isinstance(value_node, ast.Constant) or type(value_node.value) is not str:
            raise RocketDispatchClassifierMetaError(
                f"_TIER_A_TO_GROUP value for {key} must be a string literal"
            )
        group = str(value_node.value)
        if group not in _ALLOWED_GROUPS:
            raise RocketDispatchClassifierMetaError(
                f"_TIER_A_TO_GROUP value for {key} is not an allowed family: {group}"
            )
        start, end = _node_span(value_node, offsets)
        if source[start:end] != repr(group).encode("utf-8") and source[start:end] != (
            '"' + group + '"'
        ).encode("utf-8"):
            raise RocketDispatchClassifierMetaError("classifier value span does not match source token")
        entries.append(_ClassifierLiteral(key=key, group=group, start=start, end=end))

    if set(seen) != set(_CLASSIFIER_KEYS):
        raise RocketDispatchClassifierMetaError(
            f"_TIER_A_TO_GROUP keys mismatch; expected={list(_CLASSIFIER_KEYS)} got={sorted(seen)}"
        )
    return tuple(sorted(entries, key=lambda item: _CLASSIFIER_KEYS.index(item.key)))


def _classifier_map(source: bytes) -> dict[int, str]:
    return {entry.key: entry.group for entry in _extract_classifier(source)}


def _transform_dispatch_classifier(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact classifier recipe by replacing only string tokens."""

    variant = str(variant)
    if variant not in _CLASSIFIER_RECIPE_CHANGES:
        raise RocketDispatchClassifierMetaError(
            f"unsupported Rocket dispatch classifier variant: {variant}"
        )
    entries = _extract_classifier(source)
    by_key = {entry.key: entry for entry in entries}
    changes = _CLASSIFIER_RECIPE_CHANGES[variant]
    replacements: list[tuple[int, int, bytes]] = []
    for key, new_group in changes.items():
        entry = by_key[key]
        expected_old = _BASE_CLASSIFIER[key]
        if entry.group != expected_old:
            raise RocketDispatchClassifierMetaError(
                f"classifier key {key} has old family {entry.group}, expected {expected_old}"
            )
        if new_group not in _ALLOWED_GROUPS:
            raise RocketDispatchClassifierMetaError(f"replacement family is not allowed: {new_group}")
        if new_group == entry.group:
            raise RocketDispatchClassifierMetaError(
                f"Rocket dispatch classifier transform was a no-op: {variant}"
            )
        replacements.append((entry.start, entry.end, json.dumps(new_group).encode("ascii")))

    transformed = source
    for start, end, replacement in sorted(replacements, reverse=True):
        transformed = transformed[:start] + replacement + transformed[end:]
    if transformed == source:
        raise RocketDispatchClassifierMetaError(
            f"Rocket dispatch classifier transform was a no-op: {variant}"
        )
    expected = dict(_BASE_CLASSIFIER)
    expected.update(changes)
    actual = _classifier_map(transformed)
    if actual != expected:
        raise RocketDispatchClassifierMetaError(
            f"transformed classifier mismatch for {variant}: {actual} != {expected}"
        )
    return transformed, f"ROCKET_DISPATCH_CLASSIFIER_V1:{variant}"


def _normalize_split(
    variants: Sequence[str], split_by_variant: Mapping[str, str]
) -> dict[str, str]:
    ordered = [str(item) for item in variants]
    if len(ordered) != 12 or len(set(ordered)) != len(ordered):
        raise RocketDispatchClassifierMetaError("exactly twelve unique classifier variants are required")
    if set(ordered) != set(ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1):
        raise RocketDispatchClassifierMetaError("variant list does not match classifier recipe set")
    if not isinstance(split_by_variant, Mapping) or set(split_by_variant) != set(ordered):
        raise RocketDispatchClassifierMetaError("split_by_variant must cover every classifier variant exactly")
    normalized = {variant: str(split_by_variant[variant]).upper() for variant in ordered}
    if any(split not in SUPPORTED_SPLITS_V1 for split in normalized.values()):
        raise RocketDispatchClassifierMetaError("unknown META split")
    counts = {split: sum(value == split for value in normalized.values()) for split in SUPPORTED_SPLITS_V1}
    if counts != {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}:
        raise RocketDispatchClassifierMetaError(f"classifier split must be 8/2/2, got {counts}")
    return normalized


def _source_note(*, target: Path, base, policy_sha: str, recipe: str, variant: str, split: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Rocket dispatch classifier meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- localization patch: `{base.localization_patch}` (preserved)\n"
            "- source family: `rocket_dispatch_classifier_v1`\n"
            f"- variant: `{variant}`\n"
            f"- split: `{split}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- runtime change scope: `_TIER_A_TO_GROUP` value tokens only\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_rocket_dispatch_classifier_meta_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    split_by_variant: Mapping[str, str],
    variants: Sequence[str] = ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal classifier variants with no-clobber and explicit split reservations."""

    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Rocket classifier root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise RocketDispatchClassifierMetaError("source_epoch and seed_namespace must be non-empty")
    ordered_variants = tuple(str(item) for item in variants)
    normalized_split = _normalize_split(ordered_variants, split_by_variant)
    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise RocketDispatchClassifierMetaError("P1 package must contain main.py and deck.csv")

    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, base_imports, base_environment_keys = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise RocketDispatchClassifierMetaError(f"base policy is not statically safe: {findings}")
    existing_hashes = (
        _existing_policy_hashes(Path(current_pool_manifest).resolve())
        if current_pool_manifest is not None
        else set()
    )
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    for variant in ordered_variants:
        policy_bytes, recipe = _transform_dispatch_classifier(source_bytes, variant)
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise RocketDispatchClassifierMetaError(f"classifier policy identity is already used: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise RocketDispatchClassifierMetaError(f"classifier policy identity appears in artifacts: {variant}")
        transformed_findings, imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise RocketDispatchClassifierMetaError(
                f"derived classifier policy is not statically safe: {variant}: {transformed_findings}"
            )
        if tuple(imports) != tuple(base_imports) or tuple(environment_keys) != tuple(base_environment_keys):
            raise RocketDispatchClassifierMetaError(
                f"classifier transform changed imports or environment keys: {variant}"
            )
        candidate_id = (
            f"derived_{base.candidate_id}_rocket_dispatch_{variant.lower()}_{policy_sha[:12]}"
        )
        prepared.append(
            {
                "variant": variant,
                "split": normalized_split[variant],
                "policy_bytes": policy_bytes,
                "policy_sha": policy_sha,
                "recipe": recipe,
                "candidate_id": candidate_id,
                "imports": tuple(imports),
                "environment_keys": tuple(environment_keys),
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
                "source": ROCKET_DISPATCH_CLASSIFIER_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "smoke_ok": True,
                "derived": True,
                "source_family": "rocket_dispatch_classifier_v1",
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
                "source": ROCKET_DISPATCH_CLASSIFIER_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source_family": "rocket_dispatch_classifier_v1",
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
        "batch_id": f"rocket-dispatch-classifier-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new policy SHA from bounded _TIER_A_TO_GROUP classifier materialization; current pool and configured artifact identity scan",
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
        "schema_version": ROCKET_DISPATCH_CLASSIFIER_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "source_commit": base.source_commit,
        "source_family": "rocket_dispatch_classifier_v1",
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
    "ROCKET_DISPATCH_CLASSIFIER_META_SCHEMA_V1",
    "ROCKET_DISPATCH_CLASSIFIER_SOURCE_V1",
    "ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1",
    "SUPPORTED_SPLITS_V1",
    "RocketDispatchClassifierMetaError",
    "_transform_dispatch_classifier",
    "seal_rocket_dispatch_classifier_meta_v1",
]
