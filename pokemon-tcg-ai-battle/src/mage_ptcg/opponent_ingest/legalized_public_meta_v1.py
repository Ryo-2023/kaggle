"""Seal public policies with an explicit, fail-closed deck legality repair.

This lane is for public kernels whose policy payload is usable but whose
published deck snapshot cannot enter the local simulator as-is (for example,
an old snapshot with no ACE SPEC).  It never changes the policy code and it
never guesses a replacement: every card replacement is supplied as an
index/old/new recipe and is recorded in evidence.  The resulting pair remains
research-only and must pass a separate runtime smoke before CEM use.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import (
    KAGGLE_PUBLIC_SOURCE_V1,
    LOCAL_EVAL_ONLY_V1,
    scan_source_text,
    write_candidate_wrapper,
)
from mage_ptcg.opponent_ingest.pipeline import normalize_deck_text


LEGALIZED_PUBLIC_META_SCHEMA_V1 = "meta-specialist-cg-legalized-public-meta-v1"
LEGALIZED_PUBLIC_SOURCE_V1 = "internal_legalized_public_kernel"
REPAIR_RECIPE_V1 = "EXPLICIT_POSITION_REPLACEMENT_V1"
ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1 = "DECK_ON_INITIAL_SELECT_NONE_V1"
ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1 = "DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1"
_ROOT = Path(__file__).resolve().parents[3]
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_TEXT_SOURCE_SUFFIXES = {".py", ".json", ".txt", ".csv", ".yaml", ".yml", ".md"}


class LegalizedPublicMetaError(ValueError):
    """Raised when a public policy/deck repair cannot be sealed safely."""


@dataclass(frozen=True, slots=True)
class DeckRepairSpec:
    """One immutable public source plus an explicit card replacement recipe."""

    candidate_id: str
    source_root: Path
    replacements: tuple[Mapping[str, object], ...]
    source_ref: str = "public_kaggle_kernel/unknown"
    source_commit: str = "unknown"
    source_url: str = ""
    entrypoint_adapter: str = ""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise LegalizedPublicMetaError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
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


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LegalizedPublicMetaError(f"{label} is unreadable: {path}") from exc


def _official_ids(repo_root: Path) -> set[int]:
    path = repo_root / "data/raw/EN_Card_Data.csv"
    if not path.is_file():
        return set()
    values: set[int] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"\s*(\d+)\s*,", line)
        if match:
            values.add(int(match.group(1)))
    return values


def _official_ace_spec_ids(repo_root: Path) -> set[int]:
    path = repo_root / "data/raw/EN_Card_Data.csv"
    if not path.is_file():
        return set()
    import csv

    values: set[int] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("Rule", "")).strip().upper() != "ACE SPEC":
                    continue
                try:
                    values.add(int(str(row.get("Card ID", "")).strip()))
                except ValueError:
                    continue
    except (OSError, UnicodeError, csv.Error) as exc:
        raise LegalizedPublicMetaError("official ACE SPEC catalog is unreadable") from exc
    return values


def _has_agent_entrypoint(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "agent":
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "agent" for target in targets):
                return True
    return False


def _source_files(source_root: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in sorted(source_root.rglob("*")):
        relative = path.relative_to(source_root)
        if path.is_symlink():
            raise LegalizedPublicMetaError(f"source symlink is forbidden: {path}")
        if path.is_dir() or "__pycache__" in relative.parts or path.suffix.lower() == ".pyc":
            continue
        # Submission archives and model binaries are provenance inputs, not
        # executable payload members.  They are intentionally excluded rather
        # than decoded as UTF-8 or copied into the generated wrapper.
        if path.suffix.lower() not in _TEXT_SOURCE_SUFFIXES:
            continue
        if relative.as_posix() in {"deck.csv", "SOURCE.md"}:
            continue
        result.append(path)
    return tuple(result)


def _source_policy_path(source_root: Path) -> Path:
    direct = source_root / "main.py"
    if direct.is_file():
        return direct
    nested = source_root / "payload" / "original_main.py"
    if nested.is_file():
        return nested
    raise LegalizedPublicMetaError(f"source main.py is missing: {source_root}")


def _copy_payload(source_root: Path, destination: Path, policy_path: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    retained: list[str] = []
    # A raw public root has main.py at its top level.  A previously sealed
    # source may instead expose payload/original_main.py.  Both are copied
    # into the generated wrapper's isolated payload root.
    for path in _source_files(source_root):
        relative = path.relative_to(source_root)
        if path.resolve() == policy_path.resolve():
            target_relative = Path("original_main.py")
        elif relative.as_posix() == "main.py":
            continue
        elif relative.parts and relative.parts[0] == "payload":
            target_relative = Path(*relative.parts[1:])
            if target_relative.as_posix() == "original_main.py":
                continue
        else:
            target_relative = relative
        if not target_relative.parts:
            continue
        target = destination / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_new(target, path.read_bytes())
        retained.append(target_relative.as_posix())
    if "original_main.py" not in retained:
        raise LegalizedPublicMetaError("source payload did not contain original_main.py")
    return sorted(retained)


def _pool_rows(path: Path) -> list[Mapping[str, object]]:
    raw = _read_json(path, "current pool manifest")
    rows = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise LegalizedPublicMetaError("current pool manifest must contain a list")
    return rows


def _artifact_hits(roots: Sequence[Path], tokens: Sequence[str]) -> list[str]:
    wanted = tuple(token.encode("ascii") for token in tokens if token)
    hits: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for path in paths:
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {".json", ".jsonl", ".md", ".txt", ".csv", ".py"}:
                continue
            try:
                if path.stat().st_size > 16 * 1024 * 1024:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if any(token in data for token in wanted):
                hits.append(str(path))
    return sorted(set(hits))


def _repair_cards(source_root: Path, spec: DeckRepairSpec, *, official_ids: set[int], ace_ids: set[int]) -> tuple[list[int], list[dict[str, object]], bytes]:
    deck_path = source_root / "deck.csv"
    if not deck_path.is_file():
        raise LegalizedPublicMetaError(f"deck.csv is missing: {deck_path}")
    try:
        deck_bytes = deck_path.read_bytes()
        cards = [int(token) for token in deck_bytes.decode("utf-8", errors="strict").replace(",", " ").split()]
    except (OSError, UnicodeError, ValueError) as exc:
        raise LegalizedPublicMetaError(f"deck.csv is not an integer card list: {deck_path}") from exc
    if len(cards) != 60:
        raise LegalizedPublicMetaError(f"deck must contain exactly 60 cards: {spec.candidate_id}")
    normalized = normalize_deck_text("\n".join(str(card) for card in cards) + "\n", source_id=spec.candidate_id, path="deck.csv", official_ids=official_ids)
    if normalized.get("eligibility") != "EXACT_60_VALID":
        raise LegalizedPublicMetaError(f"deck contains unresolved card IDs: {spec.candidate_id}")
    if not spec.replacements:
        raise LegalizedPublicMetaError("at least one explicit deck replacement is required")
    repaired = list(cards)
    seen: set[int] = set()
    recipe: list[dict[str, object]] = []
    for raw in spec.replacements:
        if not isinstance(raw, Mapping):
            raise LegalizedPublicMetaError("each replacement must be an object")
        index, old, new = raw.get("index"), raw.get("old"), raw.get("new")
        if type(index) is not int or type(old) is not int or type(new) is not int:
            raise LegalizedPublicMetaError("replacement index/old/new must be integers")
        if index in seen:
            raise LegalizedPublicMetaError(f"duplicate replacement index: {index}")
        if not 0 <= index < len(repaired):
            raise LegalizedPublicMetaError(f"replacement index out of range: {index}")
        if repaired[index] != old:
            raise LegalizedPublicMetaError(f"old card mismatch at index {index}: {repaired[index]} != {old}")
        if official_ids and new not in official_ids:
            raise LegalizedPublicMetaError(f"replacement card is not in official catalog: {new}")
        repaired[index] = new
        seen.add(index)
        recipe.append({"index": index, "old": old, "new": new})
    ace_count = sum(card in ace_ids for card in repaired)
    if ace_ids and ace_count != 1:
        raise LegalizedPublicMetaError(f"repaired deck must contain exactly one ACE SPEC: {ace_count}")
    deck_text = ("\n".join(str(card) for card in repaired) + "\n").encode("utf-8")
    return repaired, recipe, deck_text


def _source_sha(spec: DeckRepairSpec, source_policy_sha: str, deck_sha: str, recipe: Sequence[Mapping[str, object]]) -> str:
    return _sha256_bytes(_canonical_json({"candidate_id": spec.candidate_id, "recipe": REPAIR_RECIPE_V1, "source_policy_sha256": source_policy_sha, "canonical_deck_hash": deck_sha, "replacements": list(recipe), "entrypoint_adapter": spec.entrypoint_adapter}))


def seal_legalized_public_meta_v1(
    *,
    specs: Sequence[DeckRepairSpec],
    current_pool_manifest: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    if not source_epoch.strip() or not seed_namespace.strip():
        raise LegalizedPublicMetaError("source_epoch and seed_namespace must be non-empty")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    current_path = Path(current_pool_manifest).resolve()
    rows = _pool_rows(current_path)
    existing_ids = {str(row.get("id")) for row in rows if row.get("id")}
    existing_policy = {str(row.get(key)) for row in rows for key in ("policy_hash", "source_policy_sha256", "policy_sha256") if row.get(key)}
    existing_decks = {str(row.get("canonical_deck_hash")) for row in rows if row.get("canonical_deck_hash")}
    roots = tuple(Path(root).resolve() for root in scan_roots)
    official_ids = _official_ids(_ROOT)
    ace_ids = _official_ace_spec_ids(_ROOT)
    output.mkdir(parents=True, exist_ok=False)
    accepted: list[tuple[DeckRepairSpec, Path, str, str, list[int], list[dict[str, object]], bytes, str]] = []
    rejections: dict[str, list[str]] = {}
    for spec in specs:
        reasons: list[str] = []
        if not _ID.fullmatch(spec.candidate_id):
            reasons.append("invalid_candidate_id")
        if spec.candidate_id in existing_ids:
            reasons.append("candidate_id_reused")
        source_root = Path(spec.source_root).resolve()
        if not source_root.is_dir():
            reasons.append("source_root_missing")
            rejections[spec.candidate_id] = reasons
            continue
        try:
            policy_path = _source_policy_path(source_root)
            policy_bytes = policy_path.read_bytes()
            policy_text = policy_bytes.decode("utf-8", errors="strict")
            if not _has_agent_entrypoint(policy_text):
                reasons.append("missing_agent_entrypoint")
            source_findings: set[str] = set()
            source_imports: set[str] = set()
            for path in _source_files(source_root):
                text = path.read_text(encoding="utf-8", errors="strict")
                findings, imports = scan_source_text(text)
                source_findings.update(findings)
                source_imports.update(imports)
            if source_findings:
                reasons.extend(sorted(source_findings))
            source_policy_sha = _sha256_bytes(policy_bytes)
            cards, recipe, deck_bytes = _repair_cards(source_root, spec, official_ids=official_ids, ace_ids=ace_ids)
            deck_sha = canonical_deck_sha256(cards)
            if spec.entrypoint_adapter not in {
                "",
                ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1,
                ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1,
            }:
                reasons.append(f"unsupported_entrypoint_adapter:{spec.entrypoint_adapter}")
            policy_sha = _sha256_bytes((f"legalized:{spec.candidate_id}\n" + policy_text).encode("utf-8"))
            source_sha = _source_sha(spec, source_policy_sha, deck_sha, recipe)
            if source_policy_sha in existing_policy:
                reasons.append("source_identity_reused")
            if deck_sha in existing_decks:
                reasons.append("deck_identity_seen")
            if _artifact_hits(roots, (spec.candidate_id, source_policy_sha, source_sha)):
                reasons.append("artifact_identity_reused")
            if not reasons:
                accepted.append((spec, source_root, source_policy_sha, policy_sha, cards, recipe, deck_bytes, source_sha))
                existing_ids.add(spec.candidate_id)
                existing_policy.add(source_policy_sha)
                existing_decks.add(deck_sha)
        except (LegalizedPublicMetaError, OSError, UnicodeError, ValueError) as exc:
            reasons.append(str(exc))
        if reasons:
            rejections[spec.candidate_id] = sorted(set(reasons))

    pool_rows: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    for spec, source_root, source_policy_sha, policy_sha, cards, recipe, deck_bytes, source_sha in sorted(accepted, key=lambda item: item[0].candidate_id):
        target = output / spec.candidate_id
        target.mkdir(parents=True, exist_ok=False)
        payload = target / "payload"
        retained = _copy_payload(source_root, payload, _source_policy_path(source_root))
        wrapper_text = _wrapper_text(
            spec.candidate_id,
            payload,
            deck_bytes=deck_bytes,
            entrypoint_adapter=spec.entrypoint_adapter,
        )
        _write_new(target / "main.py", wrapper_text.encode("utf-8"))
        _write_new(target / "deck.csv", deck_bytes)
        evidence = {
            "candidate_id": spec.candidate_id,
            "source": LEGALIZED_PUBLIC_SOURCE_V1,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "fresh": True,
            "unused_before_run": True,
            "repair_recipe": REPAIR_RECIPE_V1,
            "replacements": recipe,
            "source_root": str(source_root),
            "source_ref": spec.source_ref,
            "source_url": spec.source_url,
            "source_commit": spec.source_commit,
            "entrypoint_adapter": spec.entrypoint_adapter,
            "source_policy_sha256": source_policy_sha,
            "policy_sha256": _sha256_bytes(wrapper_text.encode("utf-8")),
            "canonical_deck_hash": canonical_deck_sha256(cards),
            "deck_bytes_sha256": _sha256_bytes(deck_bytes),
            "source_sha256": source_sha,
            "retained_members": retained,
            "static_findings": [],
            "runtime_smoke_required": True,
        }
        evidence_path = output / "evidence" / f"{spec.candidate_id}.json"
        _write_json_new(evidence_path, evidence)
        _write_new(target / "SOURCE.md", ("# Legalized public policy source (research-only)\n\n" + f"- recipe: `{REPAIR_RECIPE_V1}`\n- entrypoint adapter: `{spec.entrypoint_adapter or 'none'}`\n- source ref: `{spec.source_ref}`\n- source policy SHA-256: `{source_policy_sha}`\n- generated wrapper SHA-256: `{evidence['policy_sha256']}`\n- canonical deck SHA-256: `{evidence['canonical_deck_hash']}`\n- source SHA-256: `{source_sha}`\n- usage boundary: `local_eval_only`\n- runtime smoke: `REQUIRED_BEFORE_CEM`\n- submission bundle: prohibited\n").encode("utf-8"))
        pool_rows.append({"id": spec.candidate_id, "canonical_deck_hash": evidence["canonical_deck_hash"], "mean_decision_ms": None, "policy_hash": evidence["policy_sha256"], "source_policy_sha256": source_policy_sha, "smoke_ok": False, "source": LEGALIZED_PUBLIC_SOURCE_V1, "source_branch": spec.source_ref, "source_commit": spec.source_commit, "usage_boundary": LOCAL_EVAL_ONLY_V1, "asset_preflight": "STATIC_AND_EXACT_60", "derivation_recipe": REPAIR_RECIPE_V1, "entrypoint_adapter": spec.entrypoint_adapter})
        references.append({"id": spec.candidate_id, "fresh": True, "unused_before_run": True, "freshness_evidence_sha256": _sha256_file(evidence_path), "freshness_evidence_path": str(Path("evidence") / evidence_path.name), "policy_sha256": evidence["policy_sha256"], "canonical_deck_hash": evidence["canonical_deck_hash"], "source": LEGALIZED_PUBLIC_SOURCE_V1, "source_sha256": source_sha})

    pool_path = output / "pool_manifest.json"
    fresh_path = output / "fresh_meta.json"
    if pool_rows:
        _write_json_new(pool_path, pool_rows)
        reference_ids = sorted(row["id"] for row in pool_rows)
        seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": reference_ids}))
        _write_json_new(fresh_path, {"schema_version": FRESH_META_SCHEMA_V1, "batch_id": f"legalized-{re.sub(r'[^A-Za-z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', seed_namespace)}", "source_epoch": source_epoch, "seed_namespace": seed_namespace, "seed_plan_sha256": seed_plan_sha, "reference_ids": reference_ids, "pool_manifest_sha256": _sha256_file(pool_path), "freshness_basis": "explicit deck legality repair over locally acquired public policy; source policy and replacement recipe are SHA-bound", "references": references, "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False}, "research_only": True})
    report = {"schema_version": LEGALIZED_PUBLIC_META_SCHEMA_V1, "status": "SEALED" if pool_rows else "BLOCKED_NO_SAFE_CANDIDATES", "source_epoch": source_epoch, "seed_namespace": seed_namespace, "accepted_count": len(pool_rows), "accepted_ids": [row["id"] for row in pool_rows], "rejected_count": len(rejections), "rejections": rejections, "current_pool_manifest": str(current_path), "current_pool_manifest_sha256": _sha256_file(current_path), "pool_manifest_path": str(pool_path) if pool_rows else None, "pool_manifest_sha256": _sha256_file(pool_path) if pool_rows else None, "fresh_meta_path": str(fresh_path) if pool_rows else None, "fresh_meta_sha256": _sha256_file(fresh_path) if pool_rows else None, "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False}, "research_only": True, "imports_executed": False, "network_access": False, "git_mutation": False}
    _write_json_new(output / "repair_report.json", report)
    return report


def _wrapper_text(
    candidate_id: str,
    payload_root: Path,
    *,
    deck_bytes: bytes,
    entrypoint_adapter: str,
) -> str:
    # Keep wrapper generation in one place and reuse the audited import
    # boundary from the public-kernel intake lane.
    temporary = payload_root.parent / "main.py"
    write_candidate_wrapper(candidate_id, payload_root, temporary)
    text = temporary.read_text(encoding="utf-8")
    temporary.unlink()
    if not entrypoint_adapter:
        return text
    if entrypoint_adapter not in {
        ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1,
        ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1,
    }:
        raise LegalizedPublicMetaError(f"unsupported entrypoint adapter: {entrypoint_adapter}")
    try:
        deck = [int(token) for token in deck_bytes.decode("utf-8", errors="strict").split()]
    except (UnicodeError, ValueError) as exc:
        raise LegalizedPublicMetaError("sealed deck is not an integer card list") from exc
    marker = "def agent(observation, configuration=None):\n"
    if marker not in text:
        raise LegalizedPublicMetaError("generated wrapper entrypoint marker is missing")
    deck_literal = json.dumps(deck, separators=(",", ":"))
    adapter = (
        f"_SEALED_DECK = tuple({deck_literal})\n"
        "_MISSING = object()\n\n"
        f"{marker}"
        "    if isinstance(observation, dict) and observation.get(\"select\", _MISSING) is None:\n"
        "        return list(_SEALED_DECK)\n"
    )
    text = text.replace(marker, adapter, 1)
    if entrypoint_adapter == ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1:
        text = text.replace(
            "        return _PAYLOAD_AGENT(observation, configuration)\n",
            "        return _PAYLOAD_AGENT(observation)\n",
            1,
        )
    return text


__all__ = ["DeckRepairSpec", "LegalizedPublicMetaError", "ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_V1", "ENTRYPOINT_ADAPTER_DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1", "LEGALIZED_PUBLIC_META_SCHEMA_V1", "LEGALIZED_PUBLIC_SOURCE_V1", "REPAIR_RECIPE_V1", "seal_legalized_public_meta_v1"]
