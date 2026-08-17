"""Seal self-owned opponent sources by recombining independent lineages.

This lane does not invent hidden labels or copy an already evaluated pair.  It
takes the policy payload from one sealed, fault-free source and the legal deck
from a different sealed source, then emits a new wrapper/deck identity.  The
combination is intentionally treated as an unqualified research candidate:
static checks happen here, while CABT runtime smoke is a separate promotion
gate.  A generated pool therefore starts with ``smoke_ok=false``.

The recipe is useful when public-source acquisition has become the bottleneck:
it expands the meta surface without training on expert labels and without
making an old policy/deck pair look fresh by merely renaming it.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import (
    LOCAL_EVAL_ONLY_V1,
    scan_source_text,
    write_candidate_wrapper,
)
from mage_ptcg.opponent_ingest.pipeline import normalize_deck_text
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1


CROSS_LINEAGE_META_SCHEMA_V1 = "meta-specialist-cg-cross-lineage-v1"
CROSS_LINEAGE_SOURCE_V1 = "internal_cross_lineage_recombined"
RECIPE_V1 = "CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1"
_ROOT = Path(__file__).resolve().parents[3]
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class CrossLineageMetaError(ValueError):
    """Raised when a cross-lineage source cannot be sealed safely."""


@dataclass(frozen=True, slots=True)
class _LineageAsset:
    root: Path
    candidate_id: str
    source_policy_sha256: str
    staged_policy_sha256: str
    canonical_deck_hash: str
    deck_bytes_sha256: str
    source_branch: str
    source_commit: str
    source: str
    main_bytes: bytes
    deck_bytes: bytes
    pool_row: Mapping[str, object]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CrossLineageMetaError(f"regular file required: {path}")
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
        raise CrossLineageMetaError(f"{label} is unreadable: {path}") from exc


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
    except (OSError, UnicodeError, csv.Error):
        return set()
    return values


def _source_note_field(note: str, label: str, pattern: str) -> str:
    match = re.search(pattern, note, flags=re.MULTILINE)
    if match is None:
        raise CrossLineageMetaError(f"SOURCE.md is missing {label}")
    return match.group(1)


def _python_payload_files(root: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or "__pycache__" in path.parts:
            continue
        result.append(path)
    return tuple(result)


def _validate_static_payload(root: Path) -> tuple[list[str], list[str]]:
    findings: set[str] = set()
    imports: set[str] = set()
    for path in _python_payload_files(root):
        # The generated wrapper intentionally uses importlib to isolate a
        # public payload.  The untrusted payload itself must be clean; the
        # wrapper is generated from the audited repository template.
        if path.name == "main.py" and path.parent == root:
            continue
        source_findings, source_imports = scan_source_text(path.read_text(encoding="utf-8", errors="strict"))
        findings.update(source_findings)
        imports.update(source_imports)
    return sorted(findings), sorted(imports)


def _copy_payload_tree(source: Path, destination: Path) -> None:
    """Copy only regular source assets, excluding caches and source notes."""

    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative.parts and relative.parts[0] == "__pycache__":
            continue
        if "__pycache__" in relative.parts or path.suffix.lower() == ".pyc":
            continue
        if relative.as_posix() in {"main.py", "deck.csv", "SOURCE.md"}:
            continue
        target = destination / relative
        if path.is_symlink():
            raise CrossLineageMetaError(f"source symlink is forbidden: {path}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        else:
            raise CrossLineageMetaError(f"unsupported source asset: {path}")


def _read_lineage_asset(root_value: Path | str, *, repo_root: Path) -> _LineageAsset:
    supplied_root = Path(root_value).resolve()
    if not supplied_root.is_dir():
        raise CrossLineageMetaError(f"lineage root is not a directory: {supplied_root}")
    # Accept either a one-row sealed pool root or one candidate directory
    # inside a multi-row sealed pool.  The latter is useful when a smoke batch
    # contains several public sources but only one is selected as a parent.
    if (supplied_root / "pool_manifest.json").is_file():
        root = supplied_root
        manifest_path = supplied_root / "pool_manifest.json"
        selected_id: str | None = None
    else:
        root = supplied_root
        manifest_path = supplied_root.parent / "pool_manifest.json"
        selected_id = supplied_root.name
    if not manifest_path.is_file():
        raise CrossLineageMetaError(f"lineage pool manifest is missing: {manifest_path}")
    main_path = root / "main.py"
    deck_path = root / "deck.csv"
    note_path = root / "SOURCE.md"
    for path in (manifest_path, main_path, deck_path, note_path):
        if path.is_symlink() or not path.is_file():
            raise CrossLineageMetaError(f"lineage asset is missing or not regular: {path}")
    raw_pool = _read_json(manifest_path, "pool_manifest.json")
    rows = raw_pool.get("opponents", raw_pool) if isinstance(raw_pool, Mapping) else raw_pool
    if not isinstance(rows, list) or not rows or any(not isinstance(item, Mapping) for item in rows):
        raise CrossLineageMetaError(f"lineage pool manifest must contain rows: {manifest_path}")
    if selected_id is None:
        if len(rows) != 1:
            raise CrossLineageMetaError(f"a pool-root lineage input must contain exactly one row: {root}")
        row = dict(rows[0])
    else:
        matches = [dict(item) for item in rows if str(item.get("id", "")) == selected_id]
        if len(matches) != 1:
            raise CrossLineageMetaError(f"candidate directory is absent or duplicated in pool manifest: {selected_id}")
        row = matches[0]
    candidate_id = str(row.get("id", ""))
    if not _ID.fullmatch(candidate_id):
        raise CrossLineageMetaError(f"invalid lineage candidate id: {candidate_id!r}")
    main_bytes = main_path.read_bytes()
    deck_bytes = deck_path.read_bytes()
    staged_policy_sha = _sha256_bytes(main_bytes)
    if str(row.get("policy_hash", "")) != staged_policy_sha:
        raise CrossLineageMetaError(f"{candidate_id}: policy hash does not match main.py")
    try:
        cards = [int(token) for token in deck_bytes.decode("utf-8", errors="strict").replace(",", " ").split()]
    except (UnicodeError, ValueError) as exc:
        raise CrossLineageMetaError(f"{candidate_id}: deck.csv is not an integer card list") from exc
    if len(cards) != 60:
        raise CrossLineageMetaError(f"{candidate_id}: deck must contain exactly 60 cards")
    official_ids = _official_ids(repo_root)
    normalized = normalize_deck_text(deck_bytes.decode("utf-8"), source_id=candidate_id, path="deck.csv", official_ids=official_ids)
    if normalized.get("eligibility") != "EXACT_60_VALID":
        raise CrossLineageMetaError(f"{candidate_id}: deck is not locally official and exact-60")
    ace_ids = _official_ace_spec_ids(repo_root)
    ace_count = sum(1 for card in cards if card in ace_ids)
    if ace_ids and ace_count != 1:
        raise CrossLineageMetaError(f"{candidate_id}: deck has {ace_count} ACE SPEC cards, expected exactly one")
    canonical = canonical_deck_sha256(cards)
    if str(row.get("canonical_deck_hash", "")) != canonical:
        raise CrossLineageMetaError(f"{candidate_id}: canonical deck hash does not match deck.csv")
    note = note_path.read_text(encoding="utf-8", errors="strict")
    source_policy = str(row.get("source_policy_sha256", ""))
    if not _SHA64.fullmatch(source_policy):
        source_policy = _source_note_field(note, "source policy SHA", r"^- source policy SHA-256: `([^`]+)`$")
    if not _SHA64.fullmatch(source_policy):
        raise CrossLineageMetaError(f"{candidate_id}: invalid source policy SHA")
    source_branch = str(row.get("source_branch", "cross-lineage-parent"))
    source_commit = str(row.get("source_commit", "unknown"))
    source = str(row.get("source", "sealed_source"))
    if row.get("smoke_ok") is not True:
        raise CrossLineageMetaError(f"{candidate_id}: lineage parent must be smoke_ok")
    findings, _imports = _validate_static_payload(root)
    if findings:
        raise CrossLineageMetaError(f"{candidate_id}: parent payload is statically unsafe: {findings}")
    return _LineageAsset(
        root=root,
        candidate_id=candidate_id,
        source_policy_sha256=source_policy,
        staged_policy_sha256=staged_policy_sha,
        canonical_deck_hash=canonical,
        deck_bytes_sha256=_sha256_bytes(deck_bytes),
        source_branch=source_branch,
        source_commit=source_commit,
        source=source,
        main_bytes=main_bytes,
        deck_bytes=deck_bytes,
        pool_row=row,
    )


def _existing_pairs(pool_manifest: Path | None) -> set[tuple[str, str]]:
    if pool_manifest is None:
        return set()
    raw = _read_json(pool_manifest.resolve(), "current pool manifest")
    rows = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        raise CrossLineageMetaError("current pool manifest must contain a list")
    return {
        (str(row.get("policy_hash")), str(row.get("canonical_deck_hash")))
        for row in rows
        if isinstance(row, Mapping) and row.get("policy_hash") and row.get("canonical_deck_hash")
    }


def _artifact_contains(roots: Sequence[Path], tokens: Sequence[str]) -> list[str]:
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


def _candidate_id(policy: _LineageAsset, deck: _LineageAsset) -> str:
    recipe_digest = _sha256_bytes(f"{policy.candidate_id}:{policy.staged_policy_sha256}:{deck.candidate_id}:{deck.canonical_deck_hash}:{RECIPE_V1}".encode())
    value = f"cross_{policy.candidate_id[:24]}_p{policy.staged_policy_sha256[:8]}_{deck.candidate_id[:24]}_d{deck.canonical_deck_hash[:8]}_{recipe_digest[:10]}"
    if not _ID.fullmatch(value):
        raise CrossLineageMetaError(f"generated candidate id is invalid: {value}")
    return value[:96]


def _source_sha(policy: _LineageAsset, deck: _LineageAsset, candidate_id: str) -> str:
    payload = {
        "candidate_id": candidate_id,
        "recipe": RECIPE_V1,
        "policy_parent": policy.staged_policy_sha256,
        "deck_parent": deck.canonical_deck_hash,
    }
    return _sha256_bytes(_canonical_json(payload))


def _meta_row(row: Mapping[str, object], *, policy: _LineageAsset, deck: _LineageAsset, source_sha: str) -> dict[str, object]:
    return {
        "opponent_id": str(row["id"]),
        "archetype": f"CrossLineage:{policy.candidate_id}x{deck.candidate_id}",
        "deck_sha256": str(row["canonical_deck_hash"]),
        "policy_sha256": str(row["policy_hash"]),
        "source_sha256": source_sha,
        "weight": 1.0,
        "usage_boundary": LOCAL_EVAL_ONLY_V1,
        "training_exposure": 0,
        "source": CROSS_LINEAGE_SOURCE_V1,
        "derivation_recipe": RECIPE_V1,
        "policy_parent_id": policy.candidate_id,
        "deck_parent_id": deck.candidate_id,
    }


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path.resolve())


def _build_split(
    *,
    output: Path,
    rows: Sequence[Mapping[str, object]],
    meta_rows: Sequence[Mapping[str, object]],
    p1_package: Path,
) -> Path:
    if len(rows) < 3:
        raise CrossLineageMetaError("at least three generated references are required for train/dev/final separation")
    pool_path = output / "pool_manifest.json"
    meta_path = output / "meta_manifest.json"
    _write_json_new(meta_path, {"schema_version": "cg-cross-lineage-meta-distribution-v1", "research_only": True, "source_kind": CROSS_LINEAGE_SOURCE_V1, "rows": list(meta_rows)})
    pool_sha = _sha256_file(pool_path)
    meta_sha = _sha256_file(meta_path)
    p1_root = Path(p1_package).resolve()
    p1_main = p1_root / "main.py"
    p1_deck = p1_root / "deck.csv"
    if not p1_main.is_file() or not p1_deck.is_file():
        raise CrossLineageMetaError("P1 package must contain main.py and deck.csv")
    ids = sorted(str(row["id"]) for row in rows)
    split_parts = {"META_TRAIN": ids[:-2], "META_DEV": [ids[-2]], "META_FINAL": [ids[-1]]}
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(candidate_id: str) -> dict[str, object]:
        item = meta_by_id[candidate_id]
        return {key: item[key] for key in ("opponent_id", "archetype", "deck_sha256", "policy_sha256", "source_sha256", "weight", "usage_boundary", "training_exposure")}

    split = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {
            "p1_policy_sha256": _sha256_file(p1_main),
            "p1_deck_sha256": _sha256_file(p1_deck),
            "meta_manifest_sha256": meta_sha,
            "pool_manifest_sha256": pool_sha,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
        "sources": {"meta_manifest_path": _relative(meta_path), "pool_manifest_path": _relative(pool_path)},
        "evaluation_contract": {"both_seats": True, "fault_inclusive": True, "training_exposure": 0, "teacher_labels_saved": False, "final_results_read_during_search": False},
        "train_blocks": [split_parts["META_TRAIN"]],
        "splits": {name: [split_row(candidate_id) for candidate_id in split_parts[name]] for name in ("META_TRAIN", "META_DEV", "META_FINAL")},
        "notes": ["Cross-lineage recombination is a local-eval-only source recipe.", "Runtime smoke must seal pool rows before this split is used by CEM."],
    }
    split_path = output / "cg_historical_split.json"
    _write_json_new(split_path, split)
    return split_path


def build_cross_lineage_split_v1(*, output_root: Path | str, p1_package: Path | str) -> dict[str, object]:
    """Rebind the split after smoke promotion has replaced the pool SHA."""

    output = Path(output_root).resolve()
    pool_path = output / "pool_manifest.json"
    fresh_path = output / "fresh_meta.json"
    if not pool_path.is_file() or not fresh_path.is_file():
        raise CrossLineageMetaError("promoted root must contain pool_manifest.json and fresh_meta.json")
    raw_pool = _read_json(pool_path, "pool_manifest.json")
    fresh = _read_json(fresh_path, "fresh_meta.json")
    rows = raw_pool if isinstance(raw_pool, list) else raw_pool.get("opponents", raw_pool) if isinstance(raw_pool, Mapping) else None
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise CrossLineageMetaError("pool manifest must contain rows")
    if any(row.get("smoke_ok") is not True for row in rows):
        raise CrossLineageMetaError("split can be rebound only after smoke promotion")
    if not isinstance(fresh, Mapping):
        raise CrossLineageMetaError("fresh_meta.json must be an object")
    refs = fresh.get("references")
    if not isinstance(refs, list):
        raise CrossLineageMetaError("fresh_meta.references must be a list")
    meta_rows = []
    for row in rows:
        ref = next((item for item in refs if isinstance(item, Mapping) and str(item.get("id")) == str(row["id"])), None)
        if ref is None:
            raise CrossLineageMetaError(f"fresh_meta is missing {row['id']}")
        meta_rows.append({
            "opponent_id": str(row["id"]),
            "archetype": f"CrossLineage:{row['id']}",
            "deck_sha256": str(row["canonical_deck_hash"]),
            "policy_sha256": str(row["policy_hash"]),
            "source_sha256": str(ref.get("source_sha256")),
            "weight": 1.0,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "training_exposure": 0,
            "source": CROSS_LINEAGE_SOURCE_V1,
            "derivation_recipe": RECIPE_V1,
        })
    # Build a temporary source-independent split directly; this avoids copying
    # stale pre-smoke bindings from the intake root.
    meta_path = output / "meta_manifest.json"
    if meta_path.exists():
        raise FileExistsError(meta_path)
    _write_json_new(meta_path, {"schema_version": "cg-cross-lineage-meta-distribution-v1", "research_only": True, "source_kind": CROSS_LINEAGE_SOURCE_V1, "rows": meta_rows})
    ids = sorted(str(row["id"]) for row in rows)
    if len(ids) < 3:
        raise CrossLineageMetaError("at least three smoke-promoted references are required for a train/dev/final split")
    p1_root = Path(p1_package).resolve()
    p1_main = p1_root / "main.py"
    p1_deck = p1_root / "deck.csv"
    if not p1_main.is_file() or not p1_deck.is_file():
        raise CrossLineageMetaError("P1 package must contain main.py and deck.csv")
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(candidate_id: str) -> dict[str, object]:
        item = meta_by_id[candidate_id]
        return {key: item[key] for key in ("opponent_id", "archetype", "deck_sha256", "policy_sha256", "source_sha256", "weight", "usage_boundary", "training_exposure")}

    split = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {"p1_policy_sha256": _sha256_file(p1_main), "p1_deck_sha256": _sha256_file(p1_deck), "meta_manifest_sha256": _sha256_file(meta_path), "pool_manifest_sha256": _sha256_file(pool_path), "evaluator_sha256": evaluation_implementation_sha256_v1()},
        "sources": {"meta_manifest_path": _relative(meta_path), "pool_manifest_path": _relative(pool_path)},
        "evaluation_contract": {"both_seats": True, "fault_inclusive": True, "training_exposure": 0, "teacher_labels_saved": False, "final_results_read_during_search": False},
        "train_blocks": [ids[:-2]],
        "splits": {"META_TRAIN": [split_row(item) for item in ids[:-2]], "META_DEV": [split_row(ids[-2])], "META_FINAL": [split_row(ids[-1])]},
        "notes": ["Rebound after fault-free runtime smoke promotion.", "Cross-lineage recombination remains local-eval-only."],
    }
    split_path = output / "cg_historical_split.json"
    _write_json_new(split_path, split)
    return {"status": "SEALED", "meta_manifest_path": str(meta_path), "meta_manifest_sha256": _sha256_file(meta_path), "split_path": str(split_path), "split_sha256": _sha256_file(split_path)}


def seal_cross_lineage_meta_v1(
    *,
    lineage_roots: Sequence[Path | str] = (),
    policy_roots: Sequence[Path | str] | None = None,
    deck_roots: Sequence[Path | str] | None = None,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Generate all ordered policy/deck cross-lineage pairs and seal assets."""

    if not source_epoch.strip() or not seed_namespace.strip():
        raise CrossLineageMetaError("source_epoch and seed_namespace must be non-empty")
    if policy_roots is None and deck_roots is None:
        policy_values = tuple(lineage_roots)
        deck_values = tuple(lineage_roots)
    elif policy_roots is not None and deck_roots is not None:
        policy_values = tuple(policy_roots)
        deck_values = tuple(deck_roots)
    else:
        raise CrossLineageMetaError("policy_roots and deck_roots must be supplied together")
    policy_paths = tuple(Path(value).resolve() for value in policy_values)
    deck_paths = tuple(Path(value).resolve() for value in deck_values)
    if not policy_paths or not deck_paths or len(set(policy_paths)) != len(policy_paths) or len(set(deck_paths)) != len(deck_paths):
        raise CrossLineageMetaError("policy_roots and deck_roots must be non-empty and duplicate-free")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    policy_assets = tuple(_read_lineage_asset(root, repo_root=_ROOT) for root in policy_paths)
    deck_assets = tuple(_read_lineage_asset(root, repo_root=_ROOT) for root in deck_paths)
    assets_by_root = {asset.root: asset for asset in (*policy_assets, *deck_assets)}
    assets = tuple(assets_by_root.values())
    existing_pairs = _existing_pairs(Path(current_pool_manifest).resolve() if current_pool_manifest is not None else None)
    artifact_roots = tuple(Path(value).resolve() for value in scan_roots)
    output.mkdir(parents=True, exist_ok=False)

    rows: list[dict[str, object]] = []
    meta_rows: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    evidence_dir = output / "evidence"
    accepted_pairs: set[tuple[str, str]] = set()
    rejected: dict[str, list[str]] = {}
    for policy in policy_assets:
        for deck in deck_assets:
            if policy.candidate_id == deck.candidate_id:
                continue
            candidate_id = _candidate_id(policy, deck)
            reasons: list[str] = []
            target = output / candidate_id
            policy_payload = target / "payload"
            source_findings, source_imports = _validate_static_payload(policy.root)
            if source_findings:
                reasons.append("unsafe_parent_payload")
            if target.exists():
                reasons.append("candidate_id_reused")
            if _artifact_contains(artifact_roots, (candidate_id,)):
                reasons.append("artifact_identity_reused")
            # The wrapper is regenerated for this candidate, so the policy
            # bytes and exact pair identity are checked after materialization.
            target.mkdir(parents=True, exist_ok=False)
            _copy_payload_tree(policy.root, target)
            policy_payload.mkdir(parents=True, exist_ok=True)
            # _copy_payload_tree copied payload/* into target/payload because
            # the parent source has that directory.  The explicit mkdir above
            # also supports a minimal internal source with no payload folder.
            if not policy_payload.is_dir():
                reasons.append("missing_payload")
            try:
                write_candidate_wrapper(candidate_id, policy_payload, target / "main.py")
                _write_new(target / "deck.csv", deck.deck_bytes)
            except Exception as exc:
                reasons.append(f"materialization_failed:{type(exc).__name__}")
            if reasons:
                rejected[candidate_id] = sorted(set(reasons))
                shutil.rmtree(target)
                continue
            generated_policy_sha = _sha256_file(target / "main.py")
            generated_pair = (generated_policy_sha, deck.canonical_deck_hash)
            if generated_pair in existing_pairs or generated_pair in accepted_pairs:
                rejected[candidate_id] = ["pair_identity_reused"]
                shutil.rmtree(target)
                continue
            generated_findings, generated_imports = _validate_static_payload(target)
            # Only payload findings are actionable; the generated wrapper is a
            # repository-owned import boundary and is intentionally allowlisted.
            if generated_findings:
                rejected[candidate_id] = ["generated_payload_unsafe"]
                shutil.rmtree(target)
                continue
            source_sha = _source_sha(policy, deck, candidate_id)
            evidence = {
                "candidate_id": candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "source": CROSS_LINEAGE_SOURCE_V1,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "derivation_recipe": RECIPE_V1,
                "policy_parent_id": policy.candidate_id,
                "policy_parent_source_policy_sha256": policy.source_policy_sha256,
                "policy_parent_staged_policy_sha256": policy.staged_policy_sha256,
                "deck_parent_id": deck.candidate_id,
                "deck_parent_canonical_deck_hash": deck.canonical_deck_hash,
                "policy_sha256": generated_policy_sha,
                "canonical_deck_hash": deck.canonical_deck_hash,
                "deck_bytes_sha256": _sha256_bytes(deck.deck_bytes),
                "source_sha256": source_sha,
                "policy_imports": source_imports,
                "static_findings": [],
                "runtime_smoke_required": True,
                "parent_usage_context": "parent assets are smoke-sealed inputs; generated policy×deck pair is fresh, but parent performance exposure is not inferred",
            }
            evidence_path = evidence_dir / f"{candidate_id}.json"
            _write_json_new(evidence_path, evidence)
            _write_new(
                target / "SOURCE.md",
                (
                    "# Cross-lineage recombined meta source (research-only)\n\n"
                    f"- derivation recipe: `{RECIPE_V1}`\n"
                    f"- policy parent: `{policy.candidate_id}`\n"
                    f"- policy parent source policy SHA-256: `{policy.source_policy_sha256}`\n"
                    f"- policy parent staged wrapper SHA-256: `{policy.staged_policy_sha256}`\n"
                    f"- deck parent: `{deck.candidate_id}`\n"
                    f"- deck parent canonical SHA-256: `{deck.canonical_deck_hash}`\n"
                    f"- generated wrapper SHA-256: `{generated_policy_sha}`\n"
                    f"- generated deck bytes SHA-256: `{_sha256_bytes(deck.deck_bytes)}`\n"
                    f"- source SHA-256: `{source_sha}`\n"
                    "- usage boundary: `local_eval_only`\n"
                    "- parent usage context: `smoke-sealed inputs; parent performance exposure not inferred`\n"
                    "- runtime smoke: `REQUIRED_BEFORE_CEM`\n"
                    "- submission bundle: prohibited\n"
                ).encode("utf-8"),
            )
            row = {
                "id": candidate_id,
                "canonical_deck_hash": deck.canonical_deck_hash,
                "mean_decision_ms": None,
                "policy_hash": generated_policy_sha,
                "source_policy_sha256": policy.source_policy_sha256,
                "smoke_ok": False,
                "source": CROSS_LINEAGE_SOURCE_V1,
                "source_branch": f"cross_lineage/{policy.source_branch}+{deck.source_branch}",
                "source_commit": f"{policy.source_commit}+{deck.source_commit}",
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "asset_preflight": "STATIC_AND_EXACT_60",
                "derivation_recipe": RECIPE_V1,
                "policy_parent_id": policy.candidate_id,
                "deck_parent_id": deck.candidate_id,
            }
            rows.append(row)
            meta = _meta_row(row, policy=policy, deck=deck, source_sha=source_sha)
            meta_rows.append(meta)
            references.append({
                "id": candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256_file(evidence_path),
                "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
                "policy_sha256": generated_policy_sha,
                "canonical_deck_hash": deck.canonical_deck_hash,
                "source": CROSS_LINEAGE_SOURCE_V1,
                "source_sha256": source_sha,
            })
            accepted_pairs.add(generated_pair)

    if len(rows) < 3:
        # Two source roots produce two ordered pairs, which is useful for a
        # smoke check but cannot provide train/dev/final independence.  Keep
        # the artifact fail-closed rather than emitting a misleading split.
        raise CrossLineageMetaError(f"cross-lineage recipe produced {len(rows)} candidates; at least 3 are required")
    rows.sort(key=lambda row: str(row["id"]))
    meta_rows.sort(key=lambda row: str(row["opponent_id"]))
    references.sort(key=lambda row: str(row["id"]))
    pool_path = output / "pool_manifest.json"
    _write_json_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": [str(row["id"]) for row in rows]}))
    fresh = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"cross-lineage-{re.sub(r'[^A-Za-z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "pool_manifest_sha256": pool_sha,
        "reference_ids": [str(row["id"]) for row in rows],
        "references": references,
        "freshness_basis": "new wrapper identity from cross-lineage policy/deck recombination; static gates passed; runtime smoke pending",
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh)
    split_path = _build_split(output=output, rows=rows, meta_rows=meta_rows, p1_package=Path(p1_package).resolve())
    meta_path = output / "meta_manifest.json"
    report = {
        "schema_version": CROSS_LINEAGE_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "recipe": RECIPE_V1,
        "policy_parent_ids": sorted({str(asset.candidate_id) for asset in assets}),
        "deck_parent_ids": sorted({str(asset.candidate_id) for asset in assets}),
        "accepted_count": len(rows),
        "accepted_ids": [str(row["id"]) for row in rows],
        "rejected": rejected,
        "pool_manifest_path": str(pool_path),
        "pool_manifest_sha256": pool_sha,
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": _sha256_file(meta_path),
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
        "runtime_smoke_required": True,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
        "imports_executed": False,
        "network_access": False,
    }
    _write_json_new(output / "intake_report.json", report)
    return report


__all__ = [
    "CROSS_LINEAGE_META_SCHEMA_V1",
    "CROSS_LINEAGE_SOURCE_V1",
    "CrossLineageMetaError",
    "RECIPE_V1",
    "build_cross_lineage_split_v1",
    "seal_cross_lineage_meta_v1",
]
