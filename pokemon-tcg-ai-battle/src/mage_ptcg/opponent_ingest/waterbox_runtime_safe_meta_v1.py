"""Materialize bounded, runtime-safe variants of the Water Box search policy.

The frozen Water Box/Starmie source has a useful deck and a strong search
implementation, but its normal search budget is too expensive for a broad
opponent pool.  This module only narrows that search (or disables it) and
keeps the public observation boundary, deck, rule dispatch, and legal-action
fallback byte-for-byte unchanged.  Every output is research-only and
local-evaluation-only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from scripts.build_historical_meta_split_v1 import build_historical_meta_split_v1

from .derived_internal_meta_v1 import (
    _artifact_hits,
    _canonical_json,
    _existing_policy_hashes,
    _sha256_bytes,
    _sha256_file,
    _static_findings,
    _write_json_new,
    _write_new,
)


WATERBOX_RUNTIME_SAFE_META_SCHEMA_V1 = (
    "meta-specialist-cg-waterbox-runtime-safe-meta-v1"
)
WATERBOX_RUNTIME_SAFE_SOURCE_V1 = (
    "internal_agents_waterbox_runtime_safe_derived_v1"
)
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
SUPPORTED_SPLITS_V1 = ("META_TRAIN", "META_DEV", "META_FINAL")

WATERBOX_RUNTIME_SAFE_VARIANTS_V1 = (
    "RULE_ONLY_V2",
    "MICRO_005",
    "MICRO_015",
    "MICRO_030",
    "MICRO_070",
    "MICRO_015_EVERY_2",
    "MICRO_030_EVERY_2",
    "MICRO_070_EVERY_2",
    "MICRO_015_EVERY_3",
    "EVERY_2_TURNS_V2",
    "EVERY_3_TURNS_V2",
    "EVERY_4_TURNS_V2",
)

_SHA64 = re.compile(r"^[0-9a-f]{64}$")


class WaterboxRuntimeSafeMetaError(ValueError):
    """Raised when a Water Box runtime-safe source cannot be sealed safely."""


@dataclass(frozen=True, slots=True)
class _BaseSource:
    candidate_id: str
    source_branch: str
    source_commit: str
    source_policy_sha256: str
    deck_bytes_sha256: str
    canonical_deck_hash: str


def _parse_deck(data: bytes) -> list[int]:
    try:
        cards = [int(token) for token in data.decode("utf-8").replace(",", " ").split()]
    except (UnicodeDecodeError, ValueError) as exc:
        raise WaterboxRuntimeSafeMetaError("Water Box deck is not integer text") from exc
    if len(cards) != 60:
        raise WaterboxRuntimeSafeMetaError(f"Water Box deck must contain exactly 60 cards: {len(cards)}")
    return cards


def _read_base_source(root: Path) -> _BaseSource:
    main_path = root / "main.py"
    deck_path = root / "deck.csv"
    note_path = root / "SOURCE.md"
    for path in (main_path, deck_path, note_path):
        if path.is_symlink() or not path.is_file():
            raise WaterboxRuntimeSafeMetaError(f"base asset missing or not regular: {path}")
    policy_bytes = main_path.read_bytes()
    deck_bytes = deck_path.read_bytes()
    cards = _parse_deck(deck_bytes)
    note = note_path.read_text(encoding="utf-8", errors="strict")
    branch_match = re.search(r"^- branch: `([^`]+)`", note, flags=re.MULTILINE | re.IGNORECASE)
    commit_match = re.search(r"^- (?:取り込み commit|commit)[^`]*`([^`]+)`", note, flags=re.MULTILINE | re.IGNORECASE)
    if branch_match is None or commit_match is None:
        raise WaterboxRuntimeSafeMetaError("SOURCE.md must declare branch and commit")
    policy_sha = _sha256_bytes(policy_bytes)
    deck_sha = _sha256_bytes(deck_bytes)
    canonical = canonical_deck_sha256(cards)
    return _BaseSource(
        candidate_id=root.name,
        source_branch=branch_match.group(1),
        source_commit=commit_match.group(1),
        source_policy_sha256=policy_sha,
        deck_bytes_sha256=deck_sha,
        canonical_deck_hash=canonical,
    )


def _replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise WaterboxRuntimeSafeMetaError(f"expected exactly one {label}, found {count}")
    return updated


def _inject_search_gate(text: str, *, period: int | None, rule_only: bool) -> str:
    signature = r"^(?P<indent>\s*)def _search_should_run\([^\n]*\)[^\n]*:\n"
    match = re.search(signature, text, flags=re.MULTILINE)
    if match is None:
        raise WaterboxRuntimeSafeMetaError("_search_should_run definition is missing")
    indent = match.group("indent") + "    "
    if rule_only:
        line = f"{indent}return False  # WATERBOX_RUNTIME_SAFE_RULE_ONLY_V2\n"
    elif period is not None:
        line = (
            f"{indent}if (_RAW_STEP or 0) % {period} != 0:\n"
            f"{indent}    return False  # WATERBOX_RUNTIME_SAFE_EVERY_{period}_TURNS_V2\n"
        )
    else:
        raise WaterboxRuntimeSafeMetaError("search gate requires a period or rule_only")
    return text[: match.end()] + line + text[match.end() :]


def _transform_waterbox_runtime_safe(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one allow-listed search reduction to the frozen source."""
    if variant not in WATERBOX_RUNTIME_SAFE_VARIANTS_V1:
        raise WaterboxRuntimeSafeMetaError(f"unsupported Water Box runtime-safe variant: {variant}")
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WaterboxRuntimeSafeMetaError("Water Box policy is not valid UTF-8") from exc

    if variant == "RULE_ONLY_V2":
        transformed = _inject_search_gate(text, period=None, rule_only=True)
    elif re.fullmatch(r"EVERY_[234]_TURNS_V2", variant):
        period = int(variant.split("_", 2)[1])
        transformed = _inject_search_gate(text, period=period, rule_only=False)
    else:
        match = re.fullmatch(r"MICRO_(005|015|030|070)(?:_EVERY_(2|3))?", variant)
        if match is None:
            raise WaterboxRuntimeSafeMetaError(f"unsupported Water Box runtime-safe variant: {variant}")
        budget = {
            "005": (0.005, 0.08, 30.0),
            "015": (0.015, 0.12, 45.0),
            "030": (0.030, 0.20, 60.0),
            "070": (0.070, 0.30, 90.0),
        }[match.group(1)]
        local_budget, max_budget, guard = budget
        transformed = text
        transformed = _replace_once(
            transformed,
            r"^SEARCH_NUM_WORLDS\s*=\s*[^\n]+$",
            "SEARCH_NUM_WORLDS = 1",
            "SEARCH_NUM_WORLDS assignment",
        )
        transformed = _replace_once(
            transformed,
            r"^SEARCH_LOCAL_FIXED_BUDGET\s*=\s*[^\n]+$",
            f"SEARCH_LOCAL_FIXED_BUDGET = {local_budget}",
            "SEARCH_LOCAL_FIXED_BUDGET assignment",
        )
        transformed = _replace_once(
            transformed,
            r"^SEARCH_MAX_DECISION_BUDGET\s*=\s*[^\n]+$",
            f"SEARCH_MAX_DECISION_BUDGET = {max_budget}",
            "SEARCH_MAX_DECISION_BUDGET assignment",
        )
        transformed = _replace_once(
            transformed,
            r"^SEARCH_MIN_DECISION_BUDGET\s*=\s*[^\n]+$",
            f"SEARCH_MIN_DECISION_BUDGET = {local_budget}",
            "SEARCH_MIN_DECISION_BUDGET assignment",
        )
        transformed = _replace_once(
            transformed,
            r"^SEARCH_GLOBAL_GUARD_SECONDS\s*=\s*[^\n]+$",
            f"SEARCH_GLOBAL_GUARD_SECONDS = {guard}",
            "SEARCH_GLOBAL_GUARD_SECONDS assignment",
        )
        if match.group(2) is not None:
            transformed = _inject_search_gate(transformed, period=int(match.group(2)), rule_only=False)

    if transformed == text:
        raise WaterboxRuntimeSafeMetaError("Water Box transform was a no-op")
    transformed_bytes = transformed.encode("utf-8")
    try:
        compile(transformed, "<waterbox-runtime-safe>", "exec")
    except SyntaxError as exc:
        raise WaterboxRuntimeSafeMetaError(f"transformed policy does not compile: {variant}") from exc
    return transformed_bytes, f"WATERBOX_RUNTIME_SAFE_V1:{variant}"


def _normalize_split(variants: Sequence[str], split_by_variant: Mapping[str, str]) -> dict[str, str]:
    if not variants or len(set(variants)) != len(variants):
        raise WaterboxRuntimeSafeMetaError("variants must be non-empty and unique")
    if set(split_by_variant) != set(variants):
        raise WaterboxRuntimeSafeMetaError("split_by_variant must cover variants exactly")
    normalized = {str(k): str(v) for k, v in split_by_variant.items()}
    if any(value not in SUPPORTED_SPLITS_V1 for value in normalized.values()):
        raise WaterboxRuntimeSafeMetaError("split contains an unsupported split name")
    counts = {name: sum(value == name for value in normalized.values()) for name in SUPPORTED_SPLITS_V1}
    if counts != {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}:
        raise WaterboxRuntimeSafeMetaError(f"split must be 8/2/2, got {counts}")
    return normalized


def _source_note(target: Path, base: _BaseSource, policy_sha: str, recipe: str, variant: str, split: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Derived Water Box runtime-safe meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- derivation recipe: `{recipe}`\n"
            f"- source label: `{variant}`\n"
            f"- split: `{split}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- runtime change scope: search budget and/or turn-frequency gate only\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_waterbox_runtime_safe_meta_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    split_by_variant: Mapping[str, str],
    variants: Sequence[str] = WATERBOX_RUNTIME_SAFE_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a deterministic Water Box variant pool and historical split."""
    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Water Box root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise WaterboxRuntimeSafeMetaError("source_epoch and seed_namespace must be non-empty")
    ordered_variants = tuple(str(item) for item in variants)
    normalized_split = _normalize_split(ordered_variants, split_by_variant)
    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise WaterboxRuntimeSafeMetaError("P1 package must contain main.py and deck.csv")

    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, base_imports, base_environment_keys = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise WaterboxRuntimeSafeMetaError(f"base policy is not statically safe: {findings}")
    existing_hashes = (
        _existing_policy_hashes(Path(current_pool_manifest).resolve())
        if current_pool_manifest is not None
        else set()
    )
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    for variant in ordered_variants:
        policy_bytes, recipe = _transform_waterbox_runtime_safe(source_bytes, variant)
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise WaterboxRuntimeSafeMetaError(f"Water Box policy identity is already used: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise WaterboxRuntimeSafeMetaError(f"Water Box policy identity appears in artifacts: {variant}")
        transformed_findings, imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise WaterboxRuntimeSafeMetaError(
                f"derived Water Box policy is not statically safe: {variant}: {transformed_findings}"
            )
        if tuple(imports) != tuple(base_imports) or tuple(environment_keys) != tuple(base_environment_keys):
            raise WaterboxRuntimeSafeMetaError(f"transform changed imports or environment keys: {variant}")
        prepared.append(
            {
                "variant": variant,
                "split": normalized_split[variant],
                "policy_bytes": policy_bytes,
                "policy_sha": policy_sha,
                "recipe": recipe,
                "candidate_id": f"derived_{base.candidate_id}_waterbox_runtime_safe_{variant.lower()}_{policy_sha[:12]}",
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
        _source_note(target, base, str(item["policy_sha"]), str(item["recipe"]), str(item["variant"]), str(item["split"]))
        rows.append(
            {
                "id": str(item["candidate_id"]),
                "policy_hash": str(item["policy_sha"]),
                "source_policy_sha256": base.source_policy_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source": WATERBOX_RUNTIME_SAFE_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "smoke_ok": True,
                "derived": True,
                "source_family": "waterbox_runtime_safe_v1",
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
                "source": WATERBOX_RUNTIME_SAFE_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.source_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source_family": "waterbox_runtime_safe_v1",
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
        "batch_id": f"waterbox-runtime-safe-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new policy SHA from bounded Water Box search materialization; current pool and configured artifact identity scan",
        "references": references,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
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
        "schema_version": WATERBOX_RUNTIME_SAFE_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "source_commit": base.source_commit,
        "source_family": "waterbox_runtime_safe_v1",
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
        "split_counts": {split: sum(item["split"] == split for item in prepared) for split in SUPPORTED_SPLITS_V1},
        "imports_executed": False,
        "network_access": False,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_json_new(output / "intake_report.json", report)
    load_opponent_pool_v1(output)
    return report


__all__ = [
    "WATERBOX_RUNTIME_SAFE_META_SCHEMA_V1",
    "WATERBOX_RUNTIME_SAFE_SOURCE_V1",
    "WATERBOX_RUNTIME_SAFE_VARIANTS_V1",
    "SUPPORTED_SPLITS_V1",
    "WaterboxRuntimeSafeMetaError",
    "_transform_waterbox_runtime_safe",
    "seal_waterbox_runtime_safe_meta_v1",
]
