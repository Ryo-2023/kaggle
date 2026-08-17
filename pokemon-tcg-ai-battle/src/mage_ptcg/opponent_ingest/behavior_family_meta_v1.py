"""Seal explicit visible-state behavior-family variants from a staged policy.

This is a deliberately narrow, research-only source-generation lane.  It
does not claim native/public diversity: each variant is a deterministic,
hash-bound transformation of one already sealed policy, keeps the original
deck and observation boundary, and is marked ``local_eval_only``.  The
recipes target visible priority tables in several already sealed policies so
that a future CEM can be tested against more than one correlated behavior
family without importing or rewriting arbitrary code.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from scripts.build_historical_meta_split_v1 import build_historical_meta_split_v1

from .derived_internal_meta_v1 import (
    DerivedInternalMetaError,
    _artifact_hits,
    _canonical_json,
    _read_base_source,
    _sha256_bytes,
    _sha256_file,
    _static_findings,
    _write_json_new,
    _write_new,
    _existing_policy_hashes,
)


BEHAVIOR_FAMILY_META_SCHEMA_V1 = "meta-specialist-cg-behavior-family-meta-v1"
BEHAVIOR_FAMILY_SOURCE_V1 = "internal_agents_behavior_family_derived"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
STARMIE_BEHAVIOR_VARIANTS_V1 = (
    "SUPPORTER_DRAW_FIRST",
    "SUPPORTER_HILDA_FIRST",
    "BASIC_EVOLUTION_FIRST",
    "POFFIN_SNORUNT_FIRST",
)
COMFEY_BEHAVIOR_VARIANTS_V1 = (
    "DECKOUT_AGGRESSIVE",
    "DECKOUT_CONSERVATIVE",
    "COMFEY_SETUP_FIRST",
    "LITWICK_SETUP_FIRST",
)
FESTIVAL_BEHAVIOR_VARIANTS_V1 = (
    "ALAKAZAM_FIRST",
    "DUNSPARCE_FIRST",
    "SHAYMIN_SETUP_FIRST",
    "POFFIN_DUNSPARCE_FIRST",
)
METAL_BEHAVIOR_VARIANTS_V1 = (
    "PIPLUP_FIRST",
    "METAGROSS_FIRST",
    "RECEIVER_FIRST",
    "LUCARIO_PLAN_FIRST",
)
METAL_RUNTIME_SAFE_BEHAVIOR_VARIANTS_V1 = (
    "RULE_ONLY_PIPLUP_FIRST",
    "RULE_ONLY_METAGROSS_FIRST",
    "RULE_ONLY_RECEIVER_FIRST",
    "RULE_ONLY_LUCARIO_PLAN_FIRST",
)
ALAKAZAM_BEHAVIOR_VARIANTS_V1 = (
    "ABRA_FIRST",
    "DUNSPARCE_FIRST",
    "FEZANDIPITI_DRAW_FIRST",
    "POFFIN_FIRST",
)
PSYCHIC_BEHAVIOR_VARIANTS_V1 = (
    "ZACIAN_FIRST",
    "XERNEAS_FIRST",
    "LILLIE_DRAW_FIRST",
    "CHEREN_DRAW_FIRST",
)
_ROOT = Path(__file__).resolve().parents[3]


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise DerivedInternalMetaError(f"{label} expected exactly one match, found {count}")
    transformed = text.replace(old, new, 1)
    if transformed == text:
        raise DerivedInternalMetaError(f"{label} transformation was a no-op")
    return transformed


def _replace_one_of(text: str, replacements: tuple[tuple[str, str], ...], label: str) -> str:
    """Apply exactly one of several known source shapes, failing closed otherwise."""

    matches = [(old, new) for old, new in replacements if text.count(old)]
    if len(matches) != 1 or text.count(matches[0][0]) != 1:
        count = sum(text.count(old) for old, _new in replacements)
        raise DerivedInternalMetaError(f"{label} expected exactly one shape match, found {count}")
    old, new = matches[0]
    return _replace_once(text, old, new, label)


def _replace_starmie_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact visible-state priority-table transformation."""

    text = source.decode("utf-8", errors="strict")
    if variant == "SUPPORTER_DRAW_FIRST":
        old = "SUPPORTER_PRIORITY_ORDER = [BOSSES_ORDERS, CRISPIN, HILDA, JUDGE, LILLIE_DECISION]"
        new = "SUPPORTER_PRIORITY_ORDER = [LILLIE_DECISION, JUDGE, CRISPIN, HILDA, BOSSES_ORDERS]"
    elif variant == "SUPPORTER_HILDA_FIRST":
        old = "SUPPORTER_PRIORITY_ORDER = [BOSSES_ORDERS, CRISPIN, HILDA, JUDGE, LILLIE_DECISION]"
        new = "SUPPORTER_PRIORITY_ORDER = [HILDA, BOSSES_ORDERS, CRISPIN, JUDGE, LILLIE_DECISION]"
    elif variant == "BASIC_EVOLUTION_FIRST":
        old = """BASIC_PLAY_PRIORITY = {
    BUDEW: 500,
    STARYU: 400,
    SNORUNT: 300,
    MUNKIDORI: 200,
    MEOWTH_EX: 100,
}"""
        new = """BASIC_PLAY_PRIORITY = {
    STARYU: 500,
    SNORUNT: 400,
    BUDEW: 300,
    MUNKIDORI: 200,
    MEOWTH_EX: 100,
}"""
    elif variant == "POFFIN_SNORUNT_FIRST":
        old = "POFFIN_IDEAL_COUNT = {STARYU: 2, SNORUNT: 1, BUDEW: 1}"
        new = "POFFIN_IDEAL_COUNT = {STARYU: 1, SNORUNT: 2, BUDEW: 1}"
    else:
        raise DerivedInternalMetaError(f"unsupported Starmie behavior variant: {variant}")
    transformed = _replace_once(text, old, new, f"STARMIE_BEHAVIOR:{variant}")
    return transformed.encode("utf-8"), f"STARMIE_BEHAVIOR_FAMILY_V1:{variant}"


def _replace_comfey_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact Comfey library-out visible-policy transformation."""

    text = source.decode("utf-8", errors="strict")
    if variant == "DECKOUT_AGGRESSIVE":
        old, new = "COMFEY_LO_SELF_DECK_RESERVE = 4", "COMFEY_LO_SELF_DECK_RESERVE = 2"
    elif variant == "DECKOUT_CONSERVATIVE":
        old, new = "COMFEY_LO_SELF_DECK_RESERVE = 4", "COMFEY_LO_SELF_DECK_RESERVE = 8"
    elif variant == "COMFEY_SETUP_FIRST":
        old = """            COMFEY_LO_COMFEY: 1000,
            COMFEY_LO_MAWILE: 900,
            COMFEY_LO_MIMIKYU: 800,"""
        new = """            COMFEY_LO_MAWILE: 1000,
            COMFEY_LO_COMFEY: 900,
            COMFEY_LO_MIMIKYU: 800,"""
    elif variant == "LITWICK_SETUP_FIRST":
        old = """        COMFEY_LO_LITWICK: 1000,
        COMFEY_LO_COMFEY: 950,
        COMFEY_LO_DUNSPARCE: 900,"""
        new = """        COMFEY_LO_COMFEY: 1000,
        COMFEY_LO_LITWICK: 950,
        COMFEY_LO_DUNSPARCE: 900,"""
    else:
        raise DerivedInternalMetaError(f"unsupported Comfey behavior variant: {variant}")
    transformed = _replace_once(text, old, new, f"COMFEY_BEHAVIOR:{variant}")
    return transformed.encode("utf-8"), f"COMFEY_BEHAVIOR_FAMILY_V1:{variant}"


def _replace_festival_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact visible-state priority transformation to Festival."""

    text = source.decode("utf-8", errors="strict")
    if variant == "ALAKAZAM_FIRST":
        old = """POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    LEGACY_DUNSPARCE: 300,
    SHAYMIN: 250,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}"""
        new = """POKEMON_PRIORITY = {
    ALAKAZAM: 300,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 600,
    LEGACY_DUNSPARCE: 600,
    SHAYMIN: 250,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}"""
    elif variant == "DUNSPARCE_FIRST":
        old = """POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    LEGACY_DUNSPARCE: 300,
    SHAYMIN: 250,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}"""
        new = """POKEMON_PRIORITY = {
    ALAKAZAM: 450,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 700,
    LEGACY_DUNSPARCE: 700,
    SHAYMIN: 250,
    DUDUNSPARCE: 350,
    FEZANDIPITI_EX: 100,
}"""
    elif variant == "SHAYMIN_SETUP_FIRST":
        old = """SETUP_ACTIVE_PRIORITY = {
    DUNSPARCE: 300,
    LEGACY_DUNSPARCE: 300,
    ABRA: 200,
    SHAYMIN: 150,
    FEZANDIPITI_EX: 100,
}"""
        new = """SETUP_ACTIVE_PRIORITY = {
    DUNSPARCE: 250,
    LEGACY_DUNSPARCE: 250,
    ABRA: 200,
    SHAYMIN: 500,
    FEZANDIPITI_EX: 100,
}"""
    elif variant == "POFFIN_DUNSPARCE_FIRST":
        old = """        search_priority = {
            ABRA: 1000 if not has_abra else 800,
            DUNSPARCE: 950 if not has_dunsparce else 750,
            LEGACY_DUNSPARCE: 950 if not has_dunsparce else 750,
            SHAYMIN: 300,
        }"""
        new = """        search_priority = {
            ABRA: 850 if not has_abra else 700,
            DUNSPARCE: 1100 if not has_dunsparce else 900,
            LEGACY_DUNSPARCE: 1100 if not has_dunsparce else 900,
            SHAYMIN: 300,
        }"""
    else:
        raise DerivedInternalMetaError(f"unsupported Festival behavior variant: {variant}")
    transformed = _replace_once(text, old, new, f"FESTIVAL_BEHAVIOR:{variant}")
    return transformed.encode("utf-8"), f"FESTIVAL_BEHAVIOR_FAMILY_V1:{variant}"


def _replace_metal_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact visible-state priority transformation to Metal/Psychic."""

    text = source.decode("utf-8", errors="strict")
    if variant == "PIPLUP_FIRST":
        old = """POKEMON_PRIORITY = {
    PIPLUP: 800,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    PRINPLUP: 630,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}"""
        new = """POKEMON_PRIORITY = {
    PIPLUP: 1000,
    BELDUM: 550,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    PRINPLUP: 760,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}"""
    elif variant == "METAGROSS_FIRST":
        old = """POKEMON_PRIORITY = {
    PIPLUP: 800,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    PRINPLUP: 630,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}"""
        new = """POKEMON_PRIORITY = {
    PIPLUP: 650,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 850,
    PRINPLUP: 780,
    METAGROSS_EX: 1000,
    EMPOLEON_EX: 900,
}"""
    elif variant == "RECEIVER_FIRST":
        old = """ITEM_PRIORITY = {
    RECEIVER: 800,
    PRECIOUS_CARRIER: 700,
    POFFIN: 600,
    RARE_CANDY: 500,
    HYPER_BALL: 400,
    JUMBO_ICE: 300,
    NIGHT_STRETCHER: 200,
    ENERGY_RECYCLE: 100,
}"""
        new = """ITEM_PRIORITY = {
    RECEIVER: 1100,
    PRECIOUS_CARRIER: 650,
    POFFIN: 750,
    RARE_CANDY: 500,
    HYPER_BALL: 400,
    JUMBO_ICE: 300,
    NIGHT_STRETCHER: 200,
    ENERGY_RECYCLE: 100,
}"""
    elif variant == "LUCARIO_PLAN_FIRST":
        old = "_PLAN_CONFLICT_PRIORITY = [PLAN_ALAKAZAM, PLAN_EX_BLOCKER, PLAN_DRAGAPULT, PLAN_LUCARIO, PLAN_DARK]"
        new = "_PLAN_CONFLICT_PRIORITY = [PLAN_LUCARIO, PLAN_EX_BLOCKER, PLAN_ALAKAZAM, PLAN_DRAGAPULT, PLAN_DARK]"
    else:
        raise DerivedInternalMetaError(f"unsupported Metal behavior variant: {variant}")
    replacements = ((old, new),)
    if variant in {"PIPLUP_FIRST", "METAGROSS_FIRST"}:
        replacements = (
            (old, new),
            (old.replace("    PRINPLUP: 630,\n", ""), new.replace("    PRINPLUP: 760,\n", "").replace("    PRINPLUP: 780,\n", "")),
        )
    transformed = _replace_one_of(text, replacements, f"METAL_BEHAVIOR:{variant}")
    return transformed.encode("utf-8"), f"METAL_BEHAVIOR_FAMILY_V1:{variant}"


def _replace_metal_runtime_safe_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply a Metal behavior transform and structurally disable expensive search."""

    prefix = "RULE_ONLY_"
    if not variant.startswith(prefix):
        raise DerivedInternalMetaError(f"unsupported Metal runtime-safe behavior variant: {variant}")
    base_variant = variant[len(prefix):]
    transformed, _ = _replace_metal_behavior(source, base_variant)
    text = transformed.decode("utf-8", errors="strict")
    text = _replace_once(text, "SEARCH_NUM_WORLDS = 3", "SEARCH_NUM_WORLDS = 0", f"METAL_RUNTIME_SAFE:{variant}")
    text = _replace_once(
        text,
        'SEARCH_LOCAL_FIXED_BUDGET = float(os.environ.get("SEARCH_LOCAL_FIXED_BUDGET", "1.0"))',
        'SEARCH_LOCAL_FIXED_BUDGET = float(os.environ.get("SEARCH_LOCAL_FIXED_BUDGET", "0.0"))',
        f"METAL_RUNTIME_SAFE:{variant}",
    )
    return text.encode("utf-8"), f"METAL_RUNTIME_SAFE_BEHAVIOR_FAMILY_V1:{variant}"


def _replace_alakazam_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact visible-state priority transform to Alakazam-family policy."""

    text = source.decode("utf-8", errors="strict")
    pokemon_old = """POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}"""
    if variant == "ABRA_FIRST":
        new = """POKEMON_PRIORITY = {
    ALAKAZAM: 400,
    KADABRA: 500,
    ABRA: 700,
    DUNSPARCE: 300,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}"""
        transformed = _replace_once(text, pokemon_old, new, f"ALAKAZAM_BEHAVIOR:{variant}")
    elif variant == "DUNSPARCE_FIRST":
        new = """POKEMON_PRIORITY = {
    ALAKAZAM: 500,
    KADABRA: 450,
    ABRA: 350,
    DUNSPARCE: 700,
    DUDUNSPARCE: 350,
    FEZANDIPITI_EX: 100,
}"""
        transformed = _replace_once(text, pokemon_old, new, f"ALAKAZAM_BEHAVIOR:{variant}")
    elif variant == "FEZANDIPITI_DRAW_FIRST":
        old = """SETUP_ACTIVE_PRIORITY = {
    DUNSPARCE: 300,
    ABRA: 200,
    FEZANDIPITI_EX: 100,
}"""
        new = """SETUP_ACTIVE_PRIORITY = {
    DUNSPARCE: 200,
    ABRA: 150,
    FEZANDIPITI_EX: 500,
}"""
        transformed = _replace_once(text, old, new, f"ALAKAZAM_BEHAVIOR:{variant}")
    elif variant == "POFFIN_FIRST":
        old = """ITEM_PRIORITY = {
    BUDDY_BUDDY_POFFIN: 300,
    POKE_PAD: 200,
    RARE_CANDY: 100,
}"""
        new = """ITEM_PRIORITY = {
    BUDDY_BUDDY_POFFIN: 600,
    POKE_PAD: 250,
    RARE_CANDY: 50,
}"""
        transformed = _replace_once(text, old, new, f"ALAKAZAM_BEHAVIOR:{variant}")
    else:
        raise DerivedInternalMetaError(f"unsupported Alakazam behavior variant: {variant}")
    return transformed.encode("utf-8"), f"ALAKAZAM_BEHAVIOR_FAMILY_V1:{variant}"


def _replace_psychic_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Apply one exact visible-state priority transform to Psychic aggro policy."""

    text = source.decode("utf-8", errors="strict")
    pokemon_old = """POKEMON_PRIORITY = {
    MELOETTA_EX: 400,
    ZACIAN: 300,
    XERNEAS_EX: 200,
    ENAMORUS: 100,
}"""
    supporter_old = """SUPPORTER_PRIORITY = {
    LILLIES_DETERMINATION: 300,
    ZEYU: 200,
    CHEREN: 100,
}"""
    if variant == "ZACIAN_FIRST":
        new = """POKEMON_PRIORITY = {
    MELOETTA_EX: 250,
    ZACIAN: 500,
    XERNEAS_EX: 200,
    ENAMORUS: 100,
}"""
        transformed = _replace_once(text, pokemon_old, new, f"PSYCHIC_BEHAVIOR:{variant}")
    elif variant == "XERNEAS_FIRST":
        new = """POKEMON_PRIORITY = {
    MELOETTA_EX: 300,
    ZACIAN: 250,
    XERNEAS_EX: 550,
    ENAMORUS: 150,
}"""
        transformed = _replace_once(text, pokemon_old, new, f"PSYCHIC_BEHAVIOR:{variant}")
    elif variant == "LILLIE_DRAW_FIRST":
        new = """SUPPORTER_PRIORITY = {
    LILLIES_DETERMINATION: 500,
    ZEYU: 150,
    CHEREN: 50,
}"""
        transformed = _replace_once(text, supporter_old, new, f"PSYCHIC_BEHAVIOR:{variant}")
    elif variant == "CHEREN_DRAW_FIRST":
        new = """SUPPORTER_PRIORITY = {
    LILLIES_DETERMINATION: 200,
    ZEYU: 150,
    CHEREN: 500,
}"""
        transformed = _replace_once(text, supporter_old, new, f"PSYCHIC_BEHAVIOR:{variant}")
    else:
        raise DerivedInternalMetaError(f"unsupported Psychic behavior variant: {variant}")
    return transformed.encode("utf-8"), f"PSYCHIC_BEHAVIOR_FAMILY_V1:{variant}"


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _write_source_note(*, target: Path, base, policy_sha: str, recipe: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Behavior-family derived meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def _seal_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str],
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
    transformer,
) -> dict[str, object]:
    """Seal four or more deterministic behavior-family variants and a split."""

    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite behavior-family root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise DerivedInternalMetaError("source_epoch and seed_namespace must be non-empty")
    ordered_variants = tuple(str(item) for item in variants)
    if len(ordered_variants) < 4 or len(set(ordered_variants)) != len(ordered_variants):
        raise DerivedInternalMetaError("at least four unique behavior variants are required")
    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, base_imports, _base_environment = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise DerivedInternalMetaError(f"base policy is not statically safe: {findings}")
    existing_hashes: set[str] = set()
    if current_pool_manifest is not None:
        existing_hashes = _existing_policy_hashes(Path(current_pool_manifest).resolve())
    roots = tuple(Path(root).resolve() for root in scan_roots)
    output.mkdir(parents=True, exist_ok=False)

    rows: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    for variant in ordered_variants:
        policy_bytes, recipe = transformer(source_bytes, variant)
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in {str(row["policy_hash"]) for row in rows}:
            raise DerivedInternalMetaError(f"behavior policy identity is already used: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise DerivedInternalMetaError(f"behavior policy identity appears in artifacts: {variant}")
        transformed_findings, imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise DerivedInternalMetaError(f"derived behavior policy is not statically safe: {variant}: {transformed_findings}")
        candidate_id = f"derived_{base.candidate_id}_behavior_{variant.lower()}_{policy_sha[:12]}"
        target = output / candidate_id
        target.mkdir(parents=True, exist_ok=False)
        _write_new(target / "main.py", policy_bytes)
        _write_new(target / "deck.csv", deck_bytes)
        _write_source_note(target=target, base=base, policy_sha=policy_sha, recipe=recipe)
        row = {
            "id": candidate_id,
            "policy_hash": policy_sha,
            "source_policy_sha256": base.source_policy_sha256,
            "canonical_deck_hash": base.canonical_deck_hash,
            "source": BEHAVIOR_FAMILY_SOURCE_V1,
            "source_branch": base.source_branch,
            "source_commit": base.source_commit,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "smoke_ok": True,
            "derived": True,
            "derivation_recipe": recipe,
            "observation_boundary": "visible_state_only",
            "asset_preflight": "STATIC_AND_EXACT_60",
        }
        rows.append(row)
        evidence.append(
            {
                "candidate_id": candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "derived": True,
                "source": BEHAVIOR_FAMILY_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": policy_sha,
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "derivation_recipe": recipe,
                "observation_boundary": "visible_state_only",
                "imports": list(imports),
                "environment_keys": list(environment_keys),
                "static_findings": list(transformed_findings),
            }
        )

    pool_path = output / "pool_manifest.json"
    _write_json_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    evidence_dir = output / "evidence"
    for item in evidence:
        _write_json_new(evidence_dir / f"{item['candidate_id']}.json", item)
    ordered_ids = [str(row["id"]) for row in rows]
    reference_ids = sorted(ordered_ids)
    seed_plan_sha = _sha256_bytes(
        _canonical_json(
            {
                "source_epoch": source_epoch,
                "seed_namespace": seed_namespace,
                "reference_ids": reference_ids,
            }
        )
    )
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
            }
        )
    fresh_payload = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"behavior-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new policy SHA from a fixed visible-state behavior-family transform; current pool and configured artifact identity scan",
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

    ids = ordered_ids
    split_report = build_historical_meta_split_v1(
        pool_root=output,
        fresh_meta_path=fresh_path,
        p1_package=p1_package,
        train_ids=ids[:2],
        dev_ids=[ids[2]],
        final_ids=ids[3:],
    )
    report = {
        "schema_version": BEHAVIOR_FAMILY_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "variants": list(ordered_variants),
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
        "imports_executed": False,
        "network_access": False,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "research_only": True,
        "base_imports": list(base_imports),
    }
    _write_json_new(output / "intake_report.json", report)
    load_opponent_pool_v1(output)
    return report


def seal_starmie_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = STARMIE_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_starmie_behavior,
    )


def seal_comfey_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = COMFEY_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_comfey_behavior,
    )


def seal_festival_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = FESTIVAL_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_festival_behavior,
    )


def seal_metal_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = METAL_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_metal_behavior,
    )


def seal_metal_runtime_safe_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = METAL_RUNTIME_SAFE_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_metal_runtime_safe_behavior,
    )


def seal_alakazam_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = ALAKAZAM_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_alakazam_behavior,
    )


def seal_psychic_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = PSYCHIC_BEHAVIOR_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_psychic_behavior,
    )


__all__ = [
    "BEHAVIOR_FAMILY_META_SCHEMA_V1",
    "BEHAVIOR_FAMILY_SOURCE_V1",
    "ALAKAZAM_BEHAVIOR_VARIANTS_V1",
    "PSYCHIC_BEHAVIOR_VARIANTS_V1",
    "DerivedInternalMetaError",
    "COMFEY_BEHAVIOR_VARIANTS_V1",
    "FESTIVAL_BEHAVIOR_VARIANTS_V1",
    "METAL_BEHAVIOR_VARIANTS_V1",
    "METAL_RUNTIME_SAFE_BEHAVIOR_VARIANTS_V1",
    "STARMIE_BEHAVIOR_VARIANTS_V1",
    "_replace_comfey_behavior",
    "_replace_alakazam_behavior",
    "_replace_psychic_behavior",
    "_replace_festival_behavior",
    "_replace_metal_behavior",
    "_replace_metal_runtime_safe_behavior",
    "_replace_starmie_behavior",
    "seal_comfey_behavior_family_v1",
    "seal_alakazam_behavior_family_v1",
    "seal_psychic_behavior_family_v1",
    "seal_festival_behavior_family_v1",
    "seal_metal_behavior_family_v1",
    "seal_metal_runtime_safe_behavior_family_v1",
    "seal_starmie_behavior_family_v1",
]
