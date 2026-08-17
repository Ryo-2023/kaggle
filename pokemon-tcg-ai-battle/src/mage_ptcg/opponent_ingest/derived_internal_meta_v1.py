"""Generate explicit, research-only variants from a sealed internal policy.

This module is a narrow source-generation lane, not a way to bless arbitrary
rewrites.  The first recipe is the already materialized Rocket specialist
table: it changes exactly one initialization line from ``_THETA_GENERAL`` to
one named, source-contained theta table.  The deck, observation boundary, and
runtime are otherwise byte-for-byte preserved.  Every generated policy gets a
new hash, provenance record, freshness evidence, and a custom cg split.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1

from .fresh_internal_meta_v1 import _artifact_hits, _canonical_json, _static_findings


DERIVED_META_SCHEMA_V1 = "meta-specialist-cg-derived-internal-meta-v1"
DERIVED_SOURCE_V1 = "internal_agents_derived"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
ROCKET_THETA_VARIANTS_V1 = (
    "LUCMIX",
    "A09_MERGED",
    "A07_MERGED",
    "ABOMASNOW_R2",
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ROOT = Path(__file__).resolve().parents[3]


class DerivedInternalMetaError(ValueError):
    """Raised when a derived source cannot be sealed fail-closed."""


@dataclass(frozen=True, slots=True)
class _BaseSource:
    candidate_id: str
    source_branch: str
    source_commit: str
    source_policy_sha256: str
    staged_policy_sha256: str
    deck_bytes_sha256: str
    canonical_deck_hash: str
    localization_patch: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise DerivedInternalMetaError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_json_new(path: Path, value: object) -> None:
    _write_new(path, _canonical_json(value))


def _text_field(text: str, label: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise DerivedInternalMetaError(f"SOURCE.md is missing {label}")
    return match.group(1)


def _read_base_source(root: Path) -> _BaseSource:
    main_path = root / "main.py"
    deck_path = root / "deck.csv"
    note_path = root / "SOURCE.md"
    for path in (main_path, deck_path, note_path):
        if path.is_symlink() or not path.is_file():
            raise DerivedInternalMetaError(f"sealed base asset missing or not regular: {path}")
    note = note_path.read_text(encoding="utf-8")
    candidate_id = root.name
    source_branch = _text_field(note, "source branch", r"^- branch: `([^`]+)`$")
    source_commit = _text_field(note, "source commit", r"^- commit: `([^`]+)`$")
    source_policy_sha = _text_field(note, "source policy SHA", r"^- source policy SHA-256: `([^`]+)`$")
    staged_policy_sha = _text_field(note, "staged policy SHA", r"^- staged policy SHA-256: `([^`]+)`$")
    deck_bytes_sha = _text_field(note, "deck bytes SHA", r"^- deck bytes SHA-256: `([^`]+)`$")
    canonical_deck = _text_field(note, "canonical deck SHA", r"^- canonical deck SHA-256: `([^`]+)`$")
    localization_patch = _text_field(note, "localization patch", r"^- localization patch: `([^`]+)` \(")
    for value, label, pattern in (
        (source_commit, "source commit", _SHA40),
        (source_policy_sha, "source policy SHA", _SHA64),
        (staged_policy_sha, "staged policy SHA", _SHA64),
        (deck_bytes_sha, "deck bytes SHA", _SHA64),
        (canonical_deck, "canonical deck SHA", _SHA64),
    ):
        if not pattern.fullmatch(value):
            raise DerivedInternalMetaError(f"invalid {label}: {value}")
    if _sha256_file(main_path) != staged_policy_sha:
        raise DerivedInternalMetaError("base SOURCE.md staged policy SHA mismatch")
    if _sha256_file(deck_path) != deck_bytes_sha:
        raise DerivedInternalMetaError("base SOURCE.md deck bytes SHA mismatch")
    cards = _parse_deck(deck_path)
    if canonical_deck_sha256(cards) != canonical_deck:
        raise DerivedInternalMetaError("base SOURCE.md canonical deck SHA mismatch")
    return _BaseSource(
        candidate_id=candidate_id,
        source_branch=source_branch,
        source_commit=source_commit,
        source_policy_sha256=source_policy_sha,
        staged_policy_sha256=staged_policy_sha,
        deck_bytes_sha256=deck_bytes_sha,
        canonical_deck_hash=canonical_deck,
        localization_patch=localization_patch,
    )


def _parse_deck(path: Path) -> list[int]:
    try:
        values = [int(token) for token in path.read_text(encoding="utf-8").replace(",", " ").split()]
    except (OSError, ValueError) as exc:
        raise DerivedInternalMetaError(f"cannot parse deck: {path}") from exc
    if len(values) != 60:
        raise DerivedInternalMetaError(f"deck must contain exactly 60 cards: {path}")
    return values


def _replace_rocket_theta(source: bytes, variant: str) -> tuple[bytes, str]:
    if variant not in ROCKET_THETA_VARIANTS_V1:
        raise DerivedInternalMetaError(f"unsupported Rocket theta variant: {variant}")
    text = source.decode("utf-8", errors="strict")
    dictionary = re.search(rf"(?m)^_THETA_{re.escape(variant)}\s*=\s*\{{", text)
    if dictionary is None:
        raise DerivedInternalMetaError(f"source does not contain _THETA_{variant}")
    pattern = re.compile(
        r"(?m)^(?P<indent>\s*)for _param_name, _param_value in _THETA_GENERAL\.items\(\):\s*$"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise DerivedInternalMetaError(
            f"expected exactly one Rocket theta initialization site, found {len(matches)}"
        )
    match = matches[0]
    replacement = f"{match.group('indent')}for _param_name, _param_value in _THETA_{variant}.items():"
    transformed = text[: match.start()] + replacement + text[match.end() :]
    if transformed == text:
        raise DerivedInternalMetaError("Rocket theta transformation was a no-op")
    return transformed.encode("utf-8"), f"ROCKET_THETA_SELECTION_V1:{variant}"


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path.resolve())


def _existing_policy_hashes(pool_manifest: Path) -> set[str]:
    try:
        payload = json.loads(pool_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DerivedInternalMetaError(f"cannot read current pool: {pool_manifest}") from exc
    rows: object = payload.get("opponents", payload) if isinstance(payload, Mapping) else payload
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise DerivedInternalMetaError("current pool must contain a list")
    return {str(row.get("policy_hash")) for row in rows if isinstance(row, Mapping) and isinstance(row.get("policy_hash"), str)}


def _source_sha(base: _BaseSource, recipe: str) -> str:
    return _sha256_bytes(f"{base.source_commit}:{base.staged_policy_sha256}:{recipe}".encode("utf-8"))


def _row(
    *,
    candidate_id: str,
    policy_sha: str,
    source_policy_sha: str,
    deck_sha: str,
    base: _BaseSource,
    source: str,
    recipe: str,
    derived: bool,
) -> dict[str, object]:
    return {
        "canonical_deck_hash": deck_sha,
        "id": candidate_id,
        "mean_decision_ms": None,
        "policy_hash": policy_sha,
        "source_policy_sha256": source_policy_sha,
        "smoke_ok": True,
        "source": source,
        "source_branch": base.source_branch,
        "source_commit": base.source_commit,
        "usage_boundary": LOCAL_EVAL_ONLY_V1,
        "localization_patch": base.localization_patch,
        "derivation_recipe": recipe,
        "derived": derived,
        "asset_preflight": "STATIC_AND_EXACT_60",
    }


def _meta_row(row: Mapping[str, object], *, base: _BaseSource, recipe: str, weight: float) -> dict[str, object]:
    return {
        "opponent_id": str(row["id"]),
        "archetype": f"RocketTheta:{recipe.split(':', 1)[-1] if ':' in recipe else 'GENERAL'}",
        "deck_sha256": str(row["canonical_deck_hash"]),
        "policy_sha256": str(row["policy_hash"]),
        "source_sha256": _source_sha(base, recipe),
        "weight": weight,
        "usage_boundary": LOCAL_EVAL_ONLY_V1,
        "training_exposure": 0,
        "source": str(row["source"]),
        "derived": bool(row.get("derived", False)),
        "derivation_recipe": recipe,
    }


def _copy_base(base_root: Path, output: Path, base: _BaseSource) -> None:
    target = output / base.candidate_id
    target.mkdir(parents=True, exist_ok=False)
    for name in ("main.py", "deck.csv"):
        _write_new(target / name, (base_root / name).read_bytes())
    _write_new(
        target / "SOURCE.md",
        (
            "# Derived meta source base snapshot (research-only)\n\n"
            f"- branch: `{base.source_branch}`\n"
            f"- commit: `{base.source_commit}`\n"
            f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
            f"- staged policy SHA-256: `{base.staged_policy_sha256}`\n"
            f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
            f"- localization patch: `{base.localization_patch}` (copied)\n"
            "- derivation recipe: `BASE_SNAPSHOT_V1`\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
        ).encode("utf-8"),
    )


def seal_derived_internal_meta_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    variants: Sequence[str] = ROCKET_THETA_VARIANTS_V1,
    include_base: bool = True,
    current_pool_manifest: Path | str | None = None,
    p1_package: Path | str,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a deterministic derived pool and a cg-weekend-compatible split."""

    base_path = Path(base_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite derived intake root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise DerivedInternalMetaError("source_epoch and seed_namespace must be non-empty")
    ordered_variants = tuple(str(value) for value in variants)
    if not ordered_variants or len(set(ordered_variants)) != len(ordered_variants):
        raise DerivedInternalMetaError("variants must be non-empty and unique")
    base = _read_base_source(base_path)
    source_bytes = (base_path / "main.py").read_bytes()
    deck_bytes = (base_path / "deck.csv").read_bytes()
    findings, imports, _environment_keys = _static_findings(source_bytes.decode("utf-8"))
    if findings:
        raise DerivedInternalMetaError(f"base policy is not statically safe: {findings}")
    if current_pool_manifest is not None:
        existing_hashes = _existing_policy_hashes(Path(current_pool_manifest).resolve())
        if base.staged_policy_sha256 in existing_hashes:
            raise DerivedInternalMetaError("base policy is already present in current pool")
    roots = tuple(Path(root).resolve() for root in scan_roots)
    output.mkdir(parents=True, exist_ok=False)

    rows: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    if include_base:
        _copy_base(base_path, output, base)
        base_row = _row(
            candidate_id=base.candidate_id,
            policy_sha=base.staged_policy_sha256,
            source_policy_sha=base.source_policy_sha256,
            deck_sha=base.canonical_deck_hash,
            base=base,
            source="internal_agents",
            recipe="BASE_SNAPSHOT_V1",
            derived=False,
        )
        rows.append(base_row)
        evidence.append({
            "candidate_id": base.candidate_id,
            "fresh": True,
            "unused_before_run": True,
            "derived": False,
            "source": "internal_agents",
            "source_branch": base.source_branch,
            "source_commit": base.source_commit,
            "source_policy_sha256": base.source_policy_sha256,
            "policy_sha256": base.staged_policy_sha256,
            "deck_bytes_sha256": base.deck_bytes_sha256,
            "canonical_deck_hash": base.canonical_deck_hash,
            "derivation_recipe": "BASE_SNAPSHOT_V1",
            "imports": list(imports),
            "static_findings": [],
        })

    for variant in ordered_variants:
        policy_bytes, recipe = _replace_rocket_theta(source_bytes, variant)
        policy_sha = _sha256_bytes(policy_bytes)
        if policy_sha in {str(row["policy_hash"]) for row in rows}:
            raise DerivedInternalMetaError(f"derived policy hash duplicated: {variant}")
        if current_pool_manifest is not None and policy_sha in existing_hashes:
            raise DerivedInternalMetaError(f"derived policy is already present in current pool: {variant}")
        hits = _artifact_hits(roots, (policy_sha,))
        if hits:
            raise DerivedInternalMetaError(f"derived policy identity already appears in artifacts: {variant}")
        transformed_findings, transformed_imports, environment_keys = _static_findings(policy_bytes.decode("utf-8"))
        if transformed_findings:
            raise DerivedInternalMetaError(f"derived policy is not statically safe: {variant}: {transformed_findings}")
        candidate_id = f"derived_{base.candidate_id}_{variant.lower()}_{policy_sha[:12]}"
        target = output / candidate_id
        target.mkdir(parents=True, exist_ok=False)
        _write_new(target / "main.py", policy_bytes)
        _write_new(target / "deck.csv", deck_bytes)
        _write_new(
            target / "SOURCE.md",
            (
                "# Derived internal meta source (research-only)\n\n"
                f"- branch: `{base.source_branch}`\n"
                f"- commit: `{base.source_commit}`\n"
                f"- source policy SHA-256: `{base.source_policy_sha256}`\n"
                f"- derived-from staged policy SHA-256: `{base.staged_policy_sha256}`\n"
                f"- staged policy SHA-256: `{policy_sha}`\n"
                f"- deck bytes SHA-256: `{base.deck_bytes_sha256}`\n"
                f"- canonical deck SHA-256: `{base.canonical_deck_hash}`\n"
                f"- derivation recipe: `{recipe}`\n"
                "- usage boundary: `local_eval_only`\n"
                "- submission bundle: prohibited\n"
            ).encode("utf-8"),
        )
        row = _row(
            candidate_id=candidate_id,
            policy_sha=policy_sha,
            source_policy_sha=base.source_policy_sha256,
            deck_sha=base.canonical_deck_hash,
            base=base,
            source=DERIVED_SOURCE_V1,
            recipe=recipe,
            derived=True,
        )
        rows.append(row)
        evidence.append({
            "candidate_id": candidate_id,
            "fresh": True,
            "unused_before_run": True,
            "derived": True,
            "source": DERIVED_SOURCE_V1,
            "source_branch": base.source_branch,
            "source_commit": base.source_commit,
            "source_policy_sha256": base.source_policy_sha256,
            "derived_from_policy_sha256": base.staged_policy_sha256,
            "policy_sha256": policy_sha,
            "deck_bytes_sha256": base.deck_bytes_sha256,
            "canonical_deck_hash": base.canonical_deck_hash,
            "derivation_recipe": recipe,
            "imports": list(transformed_imports),
            "environment_keys": list(environment_keys),
            "static_findings": list(transformed_findings),
        })

    if include_base and len(ordered_variants) < 3:
        raise DerivedInternalMetaError("include_base requires at least three derived variants for train/dev/final separation")
    if not include_base and len(ordered_variants) < 4:
        raise DerivedInternalMetaError("at least four derived variants are required for train/dev/final separation")

    base_id = base.candidate_id
    # Resolve generated IDs by their recipe rather than relying on hash text.
    derived_by_recipe = {
        str(row["derivation_recipe"]): str(row["id"])
        for row in rows
        if bool(row.get("derived"))
    }
    train_ids = [derived_by_recipe[f"ROCKET_THETA_SELECTION_V1:{variant}"] for variant in ordered_variants[:2]]
    dev_ids = [base_id] if include_base else [derived_by_recipe[f"ROCKET_THETA_SELECTION_V1:{ordered_variants[2]}"]]
    final_start = 2 if include_base else 3
    final_ids = [derived_by_recipe[f"ROCKET_THETA_SELECTION_V1:{variant}"] for variant in ordered_variants[final_start:]]
    if not final_ids:
        raise DerivedInternalMetaError("META_FINAL cannot be empty")

    p1_root = Path(p1_package).resolve()
    p1_policy_path = p1_root / "main.py"
    p1_deck_path = p1_root / "deck.csv"
    if not p1_policy_path.is_file() or not p1_deck_path.is_file():
        raise DerivedInternalMetaError("P1 package must contain main.py and deck.csv")
    p1_policy_sha = _sha256_file(p1_policy_path)
    p1_deck_sha = _sha256_file(p1_deck_path)
    meta_rows = []
    for row in rows:
        recipe = str(row["derivation_recipe"])
        meta_rows.append(_meta_row(row, base=base, recipe=recipe, weight=1.0))
    meta_payload = {
        "schema_version": "cg-derived-meta-distribution-v1",
        "research_only": True,
        "source_kind": DERIVED_SOURCE_V1,
        "rows": meta_rows,
    }
    meta_path = output / "meta_manifest.json"
    _write_json_new(meta_path, meta_payload)
    pool_path = output / "pool_manifest.json"
    _write_json_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    meta_sha = _sha256_file(meta_path)

    def split_row(candidate_id: str) -> dict[str, object]:
        meta = next(item for item in meta_rows if item["opponent_id"] == candidate_id)
        return {
            "opponent_id": candidate_id,
            "archetype": meta["archetype"],
            "deck_sha256": meta["deck_sha256"],
            "policy_sha256": meta["policy_sha256"],
            "source_sha256": meta["source_sha256"],
            "weight": 1.0,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "training_exposure": 0,
        }

    split_payload = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {
            "p1_policy_sha256": p1_policy_sha,
            "p1_deck_sha256": p1_deck_sha,
            "meta_manifest_sha256": meta_sha,
            "pool_manifest_sha256": pool_sha,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
        "sources": {
            "meta_manifest_path": _relative_or_absolute(meta_path),
            "pool_manifest_path": _relative_or_absolute(pool_path),
        },
        "evaluation_contract": {
            "both_seats": True,
            "fault_inclusive": True,
            "training_exposure": 0,
            "teacher_labels_saved": False,
            "final_results_read_during_search": False,
        },
        "train_blocks": [train_ids],
        "splits": {
            "META_TRAIN": [split_row(item) for item in train_ids],
            "META_DEV": [split_row(item) for item in dev_ids],
            "META_FINAL": [split_row(item) for item in final_ids],
        },
        "notes": [
            "Derived Rocket theta variants are correlated local-eval proxies, not public or native opponents.",
            "META_TRAIN is reserved for screen; META_DEV and META_FINAL are held out until the corresponding gate.",
        ],
    }
    split_path = output / "cg_derived_split.json"
    _write_json_new(split_path, split_payload)

    evidence_dir = output / "evidence"
    for item in evidence:
        _write_json_new(evidence_dir / f"{item['candidate_id']}.json", item)
    reference_ids = sorted(str(item["candidate_id"]) for item in evidence)
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": reference_ids}))
    references = []
    for item in evidence:
        evidence_path = evidence_dir / f"{item['candidate_id']}.json"
        references.append({
            "id": item["candidate_id"],
            "fresh": True,
            "unused_before_run": True,
            "freshness_evidence_sha256": _sha256_file(evidence_path),
            "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
            "policy_sha256": item["policy_sha256"],
            "canonical_deck_hash": item["canonical_deck_hash"],
            "source": item["source"],
            "derived": item["derived"],
            "derivation_recipe": item["derivation_recipe"],
        })
    fresh_payload = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"derived-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": "new derived policy SHA; source transform fixed; current pool and configured artifact identity scan",
        "references": sorted(references, key=lambda item: str(item["id"])),
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh_payload)
    report = {
        "schema_version": DERIVED_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "base_candidate_id": base.candidate_id,
        "derived_count": len(ordered_variants),
        "accepted_count": len(rows),
        "accepted_ids": [str(row["id"]) for row in rows],
        "derived_variants": list(ordered_variants),
        "pool_manifest_path": str(pool_path),
        "pool_manifest_sha256": pool_sha,
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": meta_sha,
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
        "imports_executed": False,
        "network_access": False,
    }
    _write_json_new(output / "intake_report.json", report)
    # Ensure the emitted pool is actually loadable before returning.
    load_opponent_pool_v1(output)
    return report


__all__ = [
    "DERIVED_META_SCHEMA_V1",
    "DERIVED_SOURCE_V1",
    "DerivedInternalMetaError",
    "ROCKET_THETA_VARIANTS_V1",
    "seal_derived_internal_meta_v1",
]
