"""Materialize bounded Rocket theta behavior variants for local CABT research.

The source is intentionally narrow: only numeric literals inside the five
sealed Rocket theta dictionaries may change.  The deck, dispatcher, visible
observation boundary, and runtime code remain byte-for-byte identical.  The
resulting policies are local-evaluation-only meta sources and never become
public opponents or submission assets.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
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


ROCKET_THETA_BEHAVIOR_META_SCHEMA_V2 = "meta-specialist-cg-rocket-theta-behavior-meta-v2"
ROCKET_THETA_BEHAVIOR_SOURCE_V2 = "internal_agents_rocket_theta_behavior_derived_v2"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"

THETA_TABLE_NAMES_V2 = (
    "_THETA_GENERAL",
    "_THETA_LUCMIX",
    "_THETA_A09_MERGED",
    "_THETA_A07_MERGED",
    "_THETA_ABOMASNOW_R2",
)
THETA_KEYS_V2 = frozenset(
    {
        "a_place_yami",
        "a_place_tama",
        "a_place_freezer",
        "a_place_mewtwo",
        "a_ideal_wanaida",
        "a_ideal_freezer",
        "b_sup_sakaki",
        "b_sup_lance",
        "b_sup_apollo",
        "b_sup_athena",
        "b_sup_lillie",
        "c_tr_mewtwo",
        "c_grass_core",
        "c_mewtwo_notready_div",
        "c_grass_wana2",
        "c_tr_yami",
        "c_t1_yami_supremacy",
        "d_deckout_guard",
        "d_hand_thin",
        "d_mewtwo_tr_reserve",
        "d_power_saver",
        "d_safe_prize",
        "e_go_first",
        "e_race_tie_attack",
        "e_unobserved_attack",
        "e_rush_mewtwo",
        "e_torment_gate",
    }
)

_SETUP_KEYS = frozenset(
    {"a_place_yami", "a_place_tama", "a_place_freezer", "a_place_mewtwo"}
)
_BOARD_KEYS = frozenset({"a_ideal_wanaida", "a_ideal_freezer"})
_SUPPORTER_KEYS = frozenset(
    {"b_sup_sakaki", "b_sup_lance", "b_sup_apollo", "b_sup_athena", "b_sup_lillie"}
)
_ATTACK_EXPONENT_KEYS = frozenset(
    {"c_tr_mewtwo", "c_grass_core", "c_grass_wana2", "c_tr_yami"}
)
_ATTACK_DIVISOR_KEY = "c_mewtwo_notready_div"
_GUARD_KEYS = frozenset(
    {"d_deckout_guard", "d_hand_thin", "d_mewtwo_tr_reserve", "d_safe_prize"}
)

_BASE_RECIPES = (
    "SETUP_SHRINK",
    "SETUP_EXPAND",
    "BOARD_WIDE",
    "BOARD_LEAN",
    "SUPPORTER_FLATTEN",
    "SUPPORTER_CONCENTRATE",
    "ATTACK_SHRINK",
    "ATTACK_EXPAND",
    "GUARD_CONSERVATIVE",
    "GUARD_AGGRESSIVE",
)
_COMPOSED_RECIPES = {
    "SETUP_SHRINK+SUPPORTER_FLATTEN": ("SETUP_SHRINK", "SUPPORTER_FLATTEN"),
    "SETUP_EXPAND+ATTACK_EXPAND": ("SETUP_EXPAND", "ATTACK_EXPAND"),
}
ROCKET_THETA_VARIANTS_V2 = _BASE_RECIPES + tuple(_COMPOSED_RECIPES)
SUPPORTED_SPLITS_V2 = ("META_TRAIN", "META_DEV", "META_FINAL")

_ROOT = Path(__file__).resolve().parents[3]


class RocketThetaBehaviorMetaError(DerivedInternalMetaError):
    """Raised when Rocket theta materialization cannot be sealed safely."""


@dataclass(frozen=True, slots=True)
class _ThetaLiteral:
    value: int | float | bool
    start: int
    end: int


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _literal_value(node: ast.AST) -> int | float | bool:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise RocketThetaBehaviorMetaError("theta value must be a literal") from exc
    if isinstance(value, bool):
        return value
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise RocketThetaBehaviorMetaError("theta numeric value must be finite")
    return value


def _byte_offsets(source: bytes) -> tuple[int, ...]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return tuple(offsets)


def _node_span(node: ast.AST, offsets: tuple[int, ...]) -> tuple[int, int]:
    if node.lineno is None or node.end_lineno is None or node.col_offset is None or node.end_col_offset is None:
        raise RocketThetaBehaviorMetaError("theta literal has no source span")
    try:
        return (
            offsets[node.lineno - 1] + node.col_offset,
            offsets[node.end_lineno - 1] + node.end_col_offset,
        )
    except IndexError as exc:
        raise RocketThetaBehaviorMetaError("theta literal source span is invalid") from exc


def _extract_theta_tables(source: bytes) -> dict[str, dict[str, _ThetaLiteral]]:
    try:
        text = source.decode("utf-8", errors="strict")
        tree = ast.parse(text)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RocketThetaBehaviorMetaError("Rocket source is not valid UTF-8 Python") from exc

    offsets = _byte_offsets(source)
    tables: dict[str, dict[str, _ThetaLiteral]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in THETA_TABLE_NAMES_V2:
            continue
        name = target.id
        if name in tables:
            raise RocketThetaBehaviorMetaError(f"duplicate theta table: {name}")
        if not isinstance(node.value, ast.Dict) or any(key is None for key in node.value.keys):
            raise RocketThetaBehaviorMetaError(f"theta table is not a literal dict: {name}")

        table: dict[str, _ThetaLiteral] = {}
        for key_node, value_node in zip(node.value.keys, node.value.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                raise RocketThetaBehaviorMetaError(f"theta table key is not a string: {name}")
            key = key_node.value
            if key in table:
                raise RocketThetaBehaviorMetaError(f"duplicate theta key {key}: {name}")
            value = _literal_value(value_node)
            start, end = _node_span(value_node, offsets)
            table[key] = _ThetaLiteral(value=value, start=start, end=end)
        if frozenset(table) != THETA_KEYS_V2:
            missing = sorted(THETA_KEYS_V2 - frozenset(table))
            extra = sorted(frozenset(table) - THETA_KEYS_V2)
            raise RocketThetaBehaviorMetaError(
                f"theta table keys mismatch for {name}; missing={missing}, extra={extra}"
            )
        tables[name] = table

    missing_tables = [name for name in THETA_TABLE_NAMES_V2 if name not in tables]
    if missing_tables:
        raise RocketThetaBehaviorMetaError(f"theta table(s) missing: {missing_tables}")
    return tables


def _validate_theta_tables(source_text: str) -> tuple[str, ...]:
    """Validate the five literal theta tables and return their stable names."""

    tables = _extract_theta_tables(source_text.encode("utf-8"))
    return tuple(name for name in THETA_TABLE_NAMES_V2 if name in tables)


def _recipe_steps(variant: str) -> tuple[str, ...]:
    if variant in _BASE_RECIPES:
        return (variant,)
    if variant in _COMPOSED_RECIPES:
        return _COMPOSED_RECIPES[variant]
    raise RocketThetaBehaviorMetaError(f"unsupported Rocket theta behavior variant: {variant}")


def _transform_number(key: str, value: int | float, step: str) -> int | float:
    result = float(value)
    if step == "SETUP_SHRINK" and key in _SETUP_KEYS:
        result = _clip(result * 0.85, -1.2, 1.2)
    elif step == "SETUP_EXPAND" and key in _SETUP_KEYS:
        result = _clip(result * 1.15, -1.2, 1.2)
    elif step == "BOARD_WIDE" and key in _BOARD_KEYS:
        result += 1.0
        result = _clip(result, 1.0, 4.0 if key == "a_ideal_wanaida" else 2.0)
    elif step == "BOARD_LEAN" and key in _BOARD_KEYS:
        result -= 1.0
        result = _clip(result, 1.0, 4.0 if key == "a_ideal_wanaida" else 2.0)
    elif step == "SUPPORTER_FLATTEN" and key in _SUPPORTER_KEYS:
        result = _clip(result * 0.9 + 50.0, 0.0, 1000.0)
    elif step == "SUPPORTER_CONCENTRATE" and key in _SUPPORTER_KEYS:
        result = _clip(500.0 + (result - 500.0) * 1.1, 0.0, 1000.0)
    elif step == "ATTACK_SHRINK":
        if key in _ATTACK_EXPONENT_KEYS:
            result = _clip(result * 0.9, -1.25, 1.4)
        elif key == _ATTACK_DIVISOR_KEY:
            result = _clip(result * 0.9, 1.0, 5.0)
    elif step == "ATTACK_EXPAND":
        if key in _ATTACK_EXPONENT_KEYS:
            result = _clip(result * 1.1, -1.4, 1.4)
        elif key == _ATTACK_DIVISOR_KEY:
            result = _clip(result * 1.1, 1.0, 5.0)
    elif step == "GUARD_CONSERVATIVE":
        delta = {
            "d_deckout_guard": 2.0,
            "d_hand_thin": 1.0,
            "d_mewtwo_tr_reserve": 1.0,
            "d_safe_prize": 1.0,
        }.get(key)
        if delta is not None:
            result += delta
            lower, upper = {
                "d_deckout_guard": (1.0, 24.0),
                "d_hand_thin": (0.0, 6.0),
                "d_mewtwo_tr_reserve": (0.0, 3.0),
                "d_safe_prize": (0.0, 5.0),
            }[key]
            result = _clip(result, lower, upper)
    elif step == "GUARD_AGGRESSIVE":
        delta = {
            "d_deckout_guard": -2.0,
            "d_hand_thin": -1.0,
            "d_mewtwo_tr_reserve": -1.0,
            "d_safe_prize": -1.0,
        }.get(key)
        if delta is not None:
            result += delta
            lower, upper = {
                "d_deckout_guard": (1.0, 24.0),
                "d_hand_thin": (0.0, 6.0),
                "d_mewtwo_tr_reserve": (0.0, 3.0),
                "d_safe_prize": (0.0, 5.0),
            }[key]
            result = _clip(result, lower, upper)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(round(result))
    return float(result)


def _format_literal(value: int | float) -> bytes:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).encode("ascii")
    rendered = repr(float(value))
    if rendered in {"nan", "inf", "-inf"}:
        raise RocketThetaBehaviorMetaError("transformed theta value is not finite")
    return rendered.encode("ascii")


def _transform_rocket_theta(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one strict bounded recipe to all five Rocket theta tables."""

    steps = _recipe_steps(str(variant))
    tables = _extract_theta_tables(source)
    replacements: list[tuple[int, int, bytes]] = []
    for table in tables.values():
        for key, literal in table.items():
            if isinstance(literal.value, bool):
                continue
            value: int | float = literal.value
            for step in steps:
                value = _transform_number(key, value, step)
            replacements.append((literal.start, literal.end, _format_literal(value)))

    transformed = source
    for start, end, replacement in sorted(replacements, reverse=True):
        transformed = transformed[:start] + replacement + transformed[end:]
    if transformed == source:
        raise RocketThetaBehaviorMetaError(f"Rocket theta transform was a no-op: {variant}")
    _extract_theta_tables(transformed)
    return transformed, f"ROCKET_THETA_BEHAVIOR_V2:{variant}"


def _normalize_split(
    variants: Sequence[str], split_by_variant: Mapping[str, str]
) -> dict[str, str]:
    if isinstance(variants, (str, bytes)) or not isinstance(variants, Sequence):
        raise RocketThetaBehaviorMetaError("variants must be a sequence")
    ordered = [str(item) for item in variants]
    if len(ordered) != len(ROCKET_THETA_VARIANTS_V2) or len(set(ordered)) != len(ordered):
        raise RocketThetaBehaviorMetaError("exactly twelve unique Rocket theta variants are required")
    if set(ordered) != set(ROCKET_THETA_VARIANTS_V2):
        raise RocketThetaBehaviorMetaError("variant list does not match the sealed Rocket recipe set")
    if not isinstance(split_by_variant, Mapping):
        raise RocketThetaBehaviorMetaError("split_by_variant must be a mapping")
    if set(split_by_variant) != set(ordered):
        raise RocketThetaBehaviorMetaError("split_by_variant must cover every variant exactly")
    normalized = {variant: str(split_by_variant[variant]).upper() for variant in ordered}
    if any(split not in SUPPORTED_SPLITS_V2 for split in normalized.values()):
        raise RocketThetaBehaviorMetaError("unknown META split")
    counts = {split: sum(value == split for value in normalized.values()) for split in SUPPORTED_SPLITS_V2}
    if counts != {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}:
        raise RocketThetaBehaviorMetaError(f"Rocket theta split must be 8/2/2, got {counts}")
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
            "# Rocket theta behavior meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- localization patch: `{base.localization_patch}` (preserved)\n"
            f"- source family: `rocket_theta_behavior_v2`\n"
            f"- variant: `{variant}`\n"
            f"- split: `{split}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_rocket_theta_behavior_meta_v2(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    split_by_variant: Mapping[str, str],
    variants: Sequence[str] = ROCKET_THETA_VARIANTS_V2,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a split-reserved, no-clobber Rocket theta behavior pool."""

    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Rocket theta root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise RocketThetaBehaviorMetaError("source_epoch and seed_namespace must be non-empty")
    normalized_split = _normalize_split(variants, split_by_variant)
    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise RocketThetaBehaviorMetaError("P1 package must contain main.py and deck.csv")

    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, base_imports, base_environment_keys = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise RocketThetaBehaviorMetaError(f"base policy is not statically safe: {findings}")
    existing_hashes = (
        _existing_policy_hashes(Path(current_pool_manifest).resolve())
        if current_pool_manifest is not None
        else set()
    )
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    for variant in variants:
        policy_bytes, recipe = _transform_rocket_theta(source_bytes, str(variant))
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise RocketThetaBehaviorMetaError(f"Rocket theta policy identity is already used: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise RocketThetaBehaviorMetaError(f"Rocket theta policy identity appears in artifacts: {variant}")
        transformed_findings, imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise RocketThetaBehaviorMetaError(
                f"derived Rocket theta policy is not statically safe: {variant}: {transformed_findings}"
            )
        candidate_id = (
            f"derived_{base.candidate_id}_rocket_theta_{str(variant).lower().replace('+', '_')}_{policy_sha[:12]}"
        )
        prepared.append(
            {
                "variant": str(variant),
                "split": normalized_split[str(variant)],
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
        row = {
            "id": str(item["candidate_id"]),
            "policy_hash": str(item["policy_sha"]),
            "source_policy_sha256": base.source_policy_sha256,
            "canonical_deck_hash": base.canonical_deck_hash,
            "source": ROCKET_THETA_BEHAVIOR_SOURCE_V2,
            "source_branch": base.source_branch,
            "source_commit": base.source_commit,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "smoke_ok": True,
            "derived": True,
            "source_family": "rocket_theta_behavior_v2",
            "source_label": str(item["variant"]),
            "split": str(item["split"]),
            "observation_boundary": "visible_state_only",
            "derivation_recipe": str(item["recipe"]),
            "asset_preflight": "STATIC_AND_EXACT_60",
        }
        rows.append(row)
        evidence.append(
            {
                "candidate_id": str(item["candidate_id"]),
                "fresh": True,
                "unused_before_run": True,
                "derived": True,
                "source": ROCKET_THETA_BEHAVIOR_SOURCE_V2,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source_family": "rocket_theta_behavior_v2",
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
        "batch_id": f"rocket-theta-behavior-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new policy SHA from bounded five-table Rocket theta materialization; current pool and configured artifact identity scan",
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
        "schema_version": ROCKET_THETA_BEHAVIOR_META_SCHEMA_V2,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "source_commit": base.source_commit,
        "source_family": "rocket_theta_behavior_v2",
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
            split: sum(item["split"] == split for item in prepared) for split in SUPPORTED_SPLITS_V2
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
    "ROCKET_THETA_BEHAVIOR_META_SCHEMA_V2",
    "ROCKET_THETA_BEHAVIOR_SOURCE_V2",
    "ROCKET_THETA_VARIANTS_V2",
    "SUPPORTED_SPLITS_V2",
    "RocketThetaBehaviorMetaError",
    "_transform_rocket_theta",
    "_validate_theta_tables",
    "seal_rocket_theta_behavior_meta_v2",
]
