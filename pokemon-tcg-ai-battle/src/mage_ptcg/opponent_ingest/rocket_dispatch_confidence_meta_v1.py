"""Materialize bounded Rocket dispatch-confidence variants.

The base Rocket policy commits to a specialist theta table after observing one
exclusive public family.  This lane keeps the source, deck, theta tables, and
observation boundary fixed, but requires a bounded amount of repeated public
evidence before that commit.  It is research-only and local-evaluation-only.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
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
    _existing_policy_hashes,
    _read_base_source,
    _sha256_bytes,
    _sha256_file,
    _static_findings,
    _write_json_new,
    _write_new,
)


ROCKET_DISPATCH_CONFIDENCE_META_SCHEMA_V1 = (
    "meta-specialist-cg-rocket-dispatch-confidence-meta-v1"
)
ROCKET_DISPATCH_CONFIDENCE_SOURCE_V1 = (
    "internal_agents_rocket_dispatch_confidence_derived_v1"
)
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
SUPPORTED_SPLITS_V1 = ("META_TRAIN", "META_DEV", "META_FINAL")

ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1 = (
    "GENERAL_ONLY",
    "TURN1_DELAY",
    "TURN2_DELAY",
    "TWO_TURN_CONFIRM",
    "MULTI_CARD_CONFIRM",
    "TURN1_OR_MULTI_CARD",
    "TWO_TURN_OR_MULTI_CARD",
    "THREE_TURN_CONFIRM",
    "TWO_TURN_AND_MULTI_CARD",
    "TURN3_OR_MULTI_CARD",
    "FOUR_TURN_CONFIRM",
    "TURN1_AND_MULTI_CARD",
)

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
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


class RocketDispatchConfidenceMetaError(DerivedInternalMetaError):
    """Raised when dispatch-confidence materialization is unsafe."""


@dataclass(frozen=True, slots=True)
class _BasePolicy:
    candidate_id: str
    source_branch: str
    source_commit: str
    source_policy_sha256: str
    staged_policy_sha256: str
    deck_bytes_sha256: str
    canonical_deck_hash: str
    localization_patch: str


def _extract_classifier_map(source: bytes) -> dict[int, str]:
    try:
        tree = ast.parse(source.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RocketDispatchConfidenceMetaError("Rocket source is not valid UTF-8 Python") from exc
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_TIER_A_TO_GROUP"
    ]
    if len(matches) != 1:
        raise RocketDispatchConfidenceMetaError(
            f"expected exactly one _TIER_A_TO_GROUP assignment, found {len(matches)}"
        )
    value = matches[0].value
    if not isinstance(value, ast.Dict) or any(key is None for key in value.keys):
        raise RocketDispatchConfidenceMetaError("_TIER_A_TO_GROUP must be a literal dictionary")
    if len(value.keys) != len(_CLASSIFIER_KEYS):
        raise RocketDispatchConfidenceMetaError("_TIER_A_TO_GROUP keys must contain exactly thirteen entries")
    result: dict[int, str] = {}
    for key_node, value_node in zip(value.keys, value.values):
        if not isinstance(key_node, ast.Constant) or type(key_node.value) is not int:
            raise RocketDispatchConfidenceMetaError("_TIER_A_TO_GROUP keys must be integer literals")
        if not isinstance(value_node, ast.Constant) or type(value_node.value) is not str:
            raise RocketDispatchConfidenceMetaError("_TIER_A_TO_GROUP values must be string literals")
        key = int(key_node.value)
        if key in result:
            raise RocketDispatchConfidenceMetaError(f"duplicate _TIER_A_TO_GROUP key: {key}")
        result[key] = str(value_node.value)
    if set(result) != set(_CLASSIFIER_KEYS):
        raise RocketDispatchConfidenceMetaError(
            f"_TIER_A_TO_GROUP keys mismatch; expected={list(_CLASSIFIER_KEYS)} got={sorted(result)}"
        )
    if result != _BASE_CLASSIFIER:
        raise RocketDispatchConfidenceMetaError(
            "confidence source must use the unmodified Rocket classifier family values"
        )
    return result


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RocketDispatchConfidenceMetaError(f"{label} expected exactly one match, found {count}")
    transformed = text.replace(old, new, 1)
    if transformed == text:
        raise RocketDispatchConfidenceMetaError(f"{label} transformation was a no-op")
    return transformed


_COMMIT_HELPER = '''\
def _dispatch_commit_allowed(mode: str, state: dict, turn: int, opponent_card_ids: set[int]) -> bool:
    """Bounded visible-evidence gate for specialist dispatch."""
    if mode == "GENERAL_ONLY":
        return False
    groups = state.get("groups") or set()
    if len(groups) != 1:
        return False
    family = next(iter(groups))
    observed_turns = state.get("group_turns", {}).get(family, set())
    evidence_turns = len(observed_turns)
    recognized_cards = sum(1 for card_id in opponent_card_ids if card_id in _TIER_A_TO_GROUP)
    if mode == "TURN1_DELAY":
        return turn >= 1
    if mode == "TURN2_DELAY":
        return turn >= 2
    if mode == "TWO_TURN_CONFIRM":
        return evidence_turns >= 2
    if mode == "MULTI_CARD_CONFIRM":
        return recognized_cards >= 2
    if mode == "TURN1_OR_MULTI_CARD":
        return turn >= 1 or recognized_cards >= 2
    if mode == "TWO_TURN_OR_MULTI_CARD":
        return evidence_turns >= 2 or recognized_cards >= 2
    if mode == "THREE_TURN_CONFIRM":
        return turn >= 3 and evidence_turns >= 2
    if mode == "TWO_TURN_AND_MULTI_CARD":
        return evidence_turns >= 2 and recognized_cards >= 2
    if mode == "TURN3_OR_MULTI_CARD":
        return turn >= 3 or recognized_cards >= 2
    if mode == "FOUR_TURN_CONFIRM":
        return turn >= 4 and evidence_turns >= 2
    if mode == "TURN1_AND_MULTI_CARD":
        return turn >= 1 and recognized_cards >= 2
    raise RuntimeError(f"unknown Rocket dispatch confidence mode: {mode}")

'''


def _transform_dispatch_confidence(source: bytes, variant: str) -> tuple[bytes, str]:
    """Inject one exact dispatch-confidence gate into the sealed source."""
    variant = str(variant)
    if variant not in ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1:
        raise RocketDispatchConfidenceMetaError(
            f"unsupported Rocket dispatch confidence variant: {variant}"
        )
    _extract_classifier_map(source)
    text = source.decode("utf-8", errors="strict")
    if "_ROCKET_DISPATCH_CONFIDENCE_MODE" in text:
        raise RocketDispatchConfidenceMetaError("source already contains confidence transformation")

    selector = '_DISPATCH_ACTUAL_FAMILY = os.environ.get("ROCKET_DISPATCH_ACTUAL_FAMILY")'
    text = _replace_once(
        text,
        selector,
        selector + f'\n_ROCKET_DISPATCH_CONFIDENCE_MODE = "{variant}"',
        "confidence selector",
    )
    state_line = '            "commit_turn": None, "conflict": False, "miscommit": False,\n'
    text = _replace_once(
        text,
        state_line,
        state_line + '            "group_turns": {},\n',
        "confidence state",
    )
    groups_line = '        state["groups"].update(groups)\n'
    text = _replace_once(
        text,
        groups_line,
        groups_line
        + '        for group in groups:\n'
        + '            state["group_turns"].setdefault(group, set()).add(turn)\n',
        "confidence evidence tracking",
    )
    commit_line = '        elif state["family"] is None and len(state["groups"]) == 1:\n'
    text = _replace_once(
        text,
        commit_line,
        '        elif (state["family"] is None and len(state["groups"]) == 1\n'
        '              and _dispatch_commit_allowed(\n'
        '                  _ROCKET_DISPATCH_CONFIDENCE_MODE, state, turn, opponent_card_ids\n'
        '              )):\n',
        "confidence commit condition",
    )
    helper_matches = list(re.finditer(r"(?m)^def _dispatch_update\([^\n]*\)[^\n]*:\n", text))
    if len(helper_matches) != 1:
        raise RocketDispatchConfidenceMetaError(
            f"confidence helper expected exactly one dispatch function, found {len(helper_matches)}"
        )
    helper_match = helper_matches[0]
    text = text[:helper_match.start()] + _COMMIT_HELPER + text[helper_match.start():]
    transformed = text.encode("utf-8")
    if transformed == source or "group_turns" not in text or "_dispatch_commit_allowed(" not in text:
        raise RocketDispatchConfidenceMetaError("confidence transformation was a no-op")
    compile(transformed.decode("utf-8"), "<rocket-confidence>", "exec")
    return transformed, f"ROCKET_DISPATCH_CONFIDENCE_V1:{variant}"


def _dispatch_commit_allowed(mode: str, state: dict, turn: int, opponent_card_ids: set[int]) -> bool:
    """Reference implementation used by tests and generated sources."""
    if mode == "GENERAL_ONLY":
        return False
    groups = state.get("groups") or set()
    if len(groups) != 1:
        return False
    family = next(iter(groups))
    observed_turns = state.get("group_turns", {}).get(family, set())
    evidence_turns = len(observed_turns)
    recognized_cards = sum(1 for card_id in opponent_card_ids if card_id in _BASE_CLASSIFIER)
    if mode == "TURN1_DELAY":
        return turn >= 1
    if mode == "TURN2_DELAY":
        return turn >= 2
    if mode == "TWO_TURN_CONFIRM":
        return evidence_turns >= 2
    if mode == "MULTI_CARD_CONFIRM":
        return recognized_cards >= 2
    if mode == "TURN1_OR_MULTI_CARD":
        return turn >= 1 or recognized_cards >= 2
    if mode == "TWO_TURN_OR_MULTI_CARD":
        return evidence_turns >= 2 or recognized_cards >= 2
    if mode == "THREE_TURN_CONFIRM":
        return turn >= 3 and evidence_turns >= 2
    if mode == "TWO_TURN_AND_MULTI_CARD":
        return evidence_turns >= 2 and recognized_cards >= 2
    if mode == "TURN3_OR_MULTI_CARD":
        return turn >= 3 or recognized_cards >= 2
    if mode == "FOUR_TURN_CONFIRM":
        return turn >= 4 and evidence_turns >= 2
    if mode == "TURN1_AND_MULTI_CARD":
        return turn >= 1 and recognized_cards >= 2
    raise RocketDispatchConfidenceMetaError(f"unknown Rocket dispatch confidence mode: {mode}")


def _normalize_split(variants: Sequence[str], split_by_variant: Mapping[str, str]) -> dict[str, str]:
    ordered = [str(item) for item in variants]
    if len(ordered) != 12 or len(set(ordered)) != len(ordered):
        raise RocketDispatchConfidenceMetaError("exactly twelve unique confidence variants are required")
    if set(ordered) != set(ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1):
        raise RocketDispatchConfidenceMetaError("variant list does not match confidence recipe set")
    if not isinstance(split_by_variant, Mapping) or set(split_by_variant) != set(ordered):
        raise RocketDispatchConfidenceMetaError("split_by_variant must cover every confidence variant exactly")
    normalized = {variant: str(split_by_variant[variant]).upper() for variant in ordered}
    if any(split not in SUPPORTED_SPLITS_V1 for split in normalized.values()):
        raise RocketDispatchConfidenceMetaError("unknown META split")
    counts = {split: sum(value == split for value in normalized.values()) for split in SUPPORTED_SPLITS_V1}
    if counts != {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}:
        raise RocketDispatchConfidenceMetaError(f"confidence split must be 8/2/2, got {counts}")
    return normalized


def _source_note(*, target: Path, base, policy_sha: str, recipe: str, variant: str, split: str) -> None:
    _write_new(
        target / "SOURCE.md",
        (
            "# Rocket dispatch confidence meta source (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- staged policy SHA-256: `{policy_sha}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- localization patch: `{base.localization_patch}` (preserved)\n"
            "- source family: `rocket_dispatch_confidence_v1`\n"
            f"- variant: `{variant}`\n"
            f"- split: `{split}`\n"
            f"- derivation recipe: `{recipe}`\n"
            "- observation boundary: `visible_state_only`\n"
            "- runtime change scope: dispatch evidence turn tracking and bounded commit gate\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_rocket_dispatch_confidence_meta_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    split_by_variant: Mapping[str, str],
    variants: Sequence[str] = ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal confidence variants with explicit split and freshness evidence."""
    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite Rocket confidence root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise RocketDispatchConfidenceMetaError("source_epoch and seed_namespace must be non-empty")
    ordered_variants = tuple(str(item) for item in variants)
    normalized_split = _normalize_split(ordered_variants, split_by_variant)
    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise RocketDispatchConfidenceMetaError("P1 package must contain main.py and deck.csv")

    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, base_imports, base_environment_keys = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise RocketDispatchConfidenceMetaError(f"base policy is not statically safe: {findings}")
    existing_hashes = (
        _existing_policy_hashes(Path(current_pool_manifest).resolve())
        if current_pool_manifest is not None
        else set()
    )
    roots = tuple(Path(root).resolve() for root in scan_roots)

    prepared: list[dict[str, object]] = []
    prepared_hashes: set[str] = set()
    for variant in ordered_variants:
        policy_bytes, recipe = _transform_dispatch_confidence(source_bytes, variant)
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in existing_hashes or policy_sha in prepared_hashes:
            raise RocketDispatchConfidenceMetaError(f"confidence policy identity is already used: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise RocketDispatchConfidenceMetaError(f"confidence policy identity appears in artifacts: {variant}")
        transformed_findings, imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise RocketDispatchConfidenceMetaError(
                f"derived confidence policy is not statically safe: {variant}: {transformed_findings}"
            )
        if tuple(imports) != tuple(base_imports) or tuple(environment_keys) != tuple(base_environment_keys):
            raise RocketDispatchConfidenceMetaError(
                f"confidence transform changed imports or environment keys: {variant}"
            )
        candidate_id = f"derived_{base.candidate_id}_rocket_dispatch_confidence_{variant.lower()}_{policy_sha[:12]}"
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
                "source": ROCKET_DISPATCH_CONFIDENCE_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "smoke_ok": True,
                "derived": True,
                "source_family": "rocket_dispatch_confidence_v1",
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
                "source": ROCKET_DISPATCH_CONFIDENCE_SOURCE_V1,
                "source_branch": base.source_branch,
                "source_commit": base.source_commit,
                "source_policy_sha256": base.source_policy_sha256,
                "derived_from_policy_sha256": base.staged_policy_sha256,
                "policy_sha256": str(item["policy_sha"]),
                "deck_bytes_sha256": base.deck_bytes_sha256,
                "canonical_deck_hash": base.canonical_deck_hash,
                "source_family": "rocket_dispatch_confidence_v1",
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
        "batch_id": f"rocket-dispatch-confidence-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new policy SHA from bounded visible-evidence dispatch-confidence materialization; current pool and configured artifact identity scan",
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
        "schema_version": ROCKET_DISPATCH_CONFIDENCE_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "source_commit": base.source_commit,
        "source_family": "rocket_dispatch_confidence_v1",
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
    "ROCKET_DISPATCH_CONFIDENCE_META_SCHEMA_V1",
    "ROCKET_DISPATCH_CONFIDENCE_SOURCE_V1",
    "ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1",
    "SUPPORTED_SPLITS_V1",
    "RocketDispatchConfidenceMetaError",
    "_dispatch_commit_allowed",
    "_transform_dispatch_confidence",
    "seal_rocket_dispatch_confidence_meta_v1",
]
