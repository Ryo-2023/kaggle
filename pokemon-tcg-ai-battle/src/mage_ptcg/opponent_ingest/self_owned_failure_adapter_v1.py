"""Seal self-owned, public-state failure-conditioned opponent adapters.

The repository has already shown that the current P1 telemetry does not yield
an action-level causal label: the strict public-prefix pairing produced no
useful action differences.  This module therefore does *not* train on those
rows.  It turns the observed failure surface into a small, explicit opponent
source recipe instead.  Each adapter is a new policy implementation which
delegates to the sealed P1 scorer and adds one bounded, public-state-only
challenge preference.

The generated pool is local-evaluation-only.  Static safety and deck gates are
performed while sealing, while the CABT runtime smoke gate is deliberately
separate.  A split can be rebound only after every generated source has
completed fault-free smoke.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import (
    LOCAL_EVAL_ONLY_V1,
    scan_source_text,
)
from mage_ptcg.opponent_ingest.pipeline import normalize_deck_text
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1


FAILURE_ADAPTER_META_SCHEMA_V1 = "meta-specialist-cg-self-owned-failure-adapter-v1"
FAILURE_ADAPTER_SOURCE_V1 = "internal_self_owned_failure_adapter"
FAILURE_ADAPTER_RECIPE_V1 = "FAILURE_CONDITIONED_PUBLIC_COUNTERPRESSURE_V1"
VARIANT_IDS = (
    "public_finish_ko_v1",
    "public_survival_retreat_v1",
    "public_counterpressure_v1",
    "public_damaged_tempo_v1",
)
PUBLIC_FEATURES = (
    "mine.active.hp",
    "mine.active.maxHp",
    "mine.active.energyCards",
    "mine.bench.energyCards",
    "opponent.active.hp",
    "opponent.active.maxHp",
    "option.type",
    "option.attackId",
)
_ROOT = Path(__file__).resolve().parents[3]
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class FailureAdapterMetaError(ValueError):
    """Raised when a failure-conditioned source cannot be sealed safely."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FailureAdapterMetaError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_new(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
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


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT))
    except ValueError:
        return str(path.resolve())


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


def _parse_and_validate_deck(deck_bytes: bytes, *, candidate_id: str, repo_root: Path) -> tuple[list[int], str]:
    try:
        cards = [int(token) for token in deck_bytes.decode("utf-8", errors="strict").replace(",", " ").split()]
    except (UnicodeError, ValueError) as exc:
        raise FailureAdapterMetaError(f"{candidate_id}: deck.csv is not an integer card list") from exc
    if len(cards) != 60:
        raise FailureAdapterMetaError(f"{candidate_id}: deck must contain exactly 60 cards")
    official_ids = _official_ids(repo_root)
    normalized = normalize_deck_text(
        deck_bytes.decode("utf-8"),
        source_id=candidate_id,
        path="deck.csv",
        official_ids=official_ids,
    )
    if normalized.get("eligibility") != "EXACT_60_VALID":
        raise FailureAdapterMetaError(f"{candidate_id}: deck is not locally official and exact-60")
    ace_ids = _official_ace_spec_ids(repo_root)
    ace_count = sum(1 for card in cards if card in ace_ids)
    if ace_ids and ace_count != 1:
        raise FailureAdapterMetaError(f"{candidate_id}: deck has {ace_count} ACE SPEC cards, expected exactly one")
    return cards, canonical_deck_sha256(cards)


def _artifact_contains(roots: Sequence[Path], tokens: Sequence[str]) -> list[str]:
    wanted = tuple(token.encode("ascii") for token in tokens if token)
    hits: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for path in paths:
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {
                ".json", ".jsonl", ".md", ".txt", ".csv", ".py"
            }:
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


def _existing_pairs(pool_manifest: Path | None) -> set[tuple[str, str]]:
    if pool_manifest is None:
        return set()
    try:
        raw = json.loads(pool_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailureAdapterMetaError(f"cannot read current pool: {pool_manifest}") from exc
    rows = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        raise FailureAdapterMetaError("current pool manifest must contain a list")
    return {
        (str(row.get("policy_hash")), str(row.get("canonical_deck_hash")))
        for row in rows
        if isinstance(row, Mapping) and row.get("policy_hash") and row.get("canonical_deck_hash")
    }


def _copy_base_assets(source: Path, target: Path) -> None:
    for name in ("main.py", "deck.csv"):
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise FailureAdapterMetaError(f"source asset is missing or not regular: {path}")
        _write_new(target / name, path.read_bytes())


def _adapter_patch(candidate_id: str) -> str:
    if candidate_id not in VARIANT_IDS:
        raise FailureAdapterMetaError(f"unknown failure adapter variant: {candidate_id}")
    # The adapter sees only the actor-visible public state.  It delegates all
    # unsupported/malformed shapes to the sealed P1 scorer and never changes
    # deck selection or non-MAIN choices.
    return f'''\n\n# RESEARCH_VARIANT: {candidate_id}\n# Recipe: {FAILURE_ADAPTER_RECIPE_V1}\n# Public-state-only opponent challenge adapter.  This is a source policy, not\n# an expert/action-label dataset; no hidden opponent fields are read.\n_FAILURE_ADAPTER_VARIANT = {candidate_id!r}\n_FAILURE_ADAPTER_BASE_MAIN_SCORE = _main_score\n_FAILURE_ADAPTER_BASE_SCORE = _score\n_FAILURE_ADAPTER_BASE_AGENT = agent\n\ndef _failure_adapter_public_stats(obs):\n    mine = _mine(obs)\n    opponent = _opponent(obs)\n    own_active = mine.active[0] if mine.active else None\n    opp_active = opponent.active[0] if opponent.active else None\n    own_hp = int(getattr(own_active, "hp", 0)) if own_active is not None else 0\n    own_max_hp = int(getattr(own_active, "maxHp", own_hp)) if own_active is not None else 0\n    opp_hp = int(getattr(opp_active, "hp", 0)) if opp_active is not None else 0\n    opp_max_hp = int(getattr(opp_active, "maxHp", opp_hp)) if opp_active is not None else 0\n    own_damage = max(0, own_max_hp - own_hp)\n    opp_damage = max(0, opp_max_hp - opp_hp)\n    bench_energy = max((_energy_count(card) for card in (mine.bench or []) if card is not None), default=0)\n    return own_hp, own_max_hp, own_damage, opp_hp, opp_max_hp, opp_damage, bench_energy\n\ndef _failure_adapter_delta(obs, option):\n    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:\n        return 0\n    try:\n        own_hp, own_max_hp, own_damage, opp_hp, opp_max_hp, opp_damage, bench_energy = _failure_adapter_public_stats(obs)\n        option_type = option.type\n        damage = int(_available_attack_damage(option)) if option_type == OptionType.ATTACK else 0\n        if _FAILURE_ADAPTER_VARIANT == "public_finish_ko_v1":\n            if option_type == OptionType.ATTACK and opp_hp > 0 and damage >= opp_hp and own_max_hp > 0 and own_hp * 4 >= own_max_hp * 3:\n                return 18000\n        elif _FAILURE_ADAPTER_VARIANT == "public_survival_retreat_v1":\n            if option_type == OptionType.RETREAT and own_max_hp > 0 and own_hp * 5 <= own_max_hp * 2 and bench_energy > 0:\n                return 20000\n        elif _FAILURE_ADAPTER_VARIANT == "public_counterpressure_v1":\n            if option_type == OptionType.ATTACK and own_max_hp > 0 and own_damage * 4 >= own_max_hp and damage > 0 and damage < max(1, opp_hp):\n                return 16000\n        elif _FAILURE_ADAPTER_VARIANT == "public_damaged_tempo_v1":\n            if option_type == OptionType.ATTACK and opp_max_hp > 0 and opp_damage * 2 >= opp_max_hp and damage > 0 and damage < max(1, opp_hp):\n                return 15000\n    except Exception:\n        return 0\n    return 0\n\ndef _main_score(obs, option: object) -> int:\n    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:\n        return 0\n    try:\n        return int(_FAILURE_ADAPTER_BASE_MAIN_SCORE(obs, option)) + int(_failure_adapter_delta(obs, option))\n    except Exception:\n        return 0\n\ndef _score(obs, option: object) -> int:\n    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:\n        return 0\n    try:\n        return int(_FAILURE_ADAPTER_BASE_SCORE(obs, option))\n    except Exception:\n        return 0\n\ndef agent(obs_dict: dict) -> list[int]:\n    return _FAILURE_ADAPTER_BASE_AGENT(obs_dict)\n'''


def render_failure_adapter_variant_v1(source_bytes: bytes, candidate_id: str) -> bytes:
    """Return one deterministic public-state adapter source."""

    if not source_bytes:
        raise FailureAdapterMetaError("base source is empty")
    try:
        source_text = source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FailureAdapterMetaError("base source must be UTF-8") from exc
    required = ("def _main_score", "def _score", "def agent", "_opponent", "_available_attack_damage")
    missing = [token for token in required if token not in source_text]
    if missing:
        raise FailureAdapterMetaError(f"base source lacks adapter contract: {missing}")
    transformed = source_text.rstrip() + _adapter_patch(candidate_id)
    if transformed == source_text:
        raise FailureAdapterMetaError("adapter transformation was a no-op")
    try:
        compile(transformed, "failure_adapter_main.py", "exec")
    except SyntaxError as exc:
        raise FailureAdapterMetaError(f"adapter source has syntax error: {exc}") from exc
    findings, _imports = scan_source_text(transformed)
    if findings:
        raise FailureAdapterMetaError(f"adapter source is statically unsafe: {findings}")
    return transformed.encode("utf-8")


def materialize_failure_adapter_variant_v1(
    *,
    source_package: Path | str,
    output_package: Path | str,
    candidate_id: str,
) -> dict[str, object]:
    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if not source.is_dir():
        raise FailureAdapterMetaError(f"source package is not a directory: {source}")
    main_path = source / "main.py"
    deck_path = source / "deck.csv"
    if main_path.is_symlink() or deck_path.is_symlink() or not main_path.is_file() or not deck_path.is_file():
        raise FailureAdapterMetaError(f"source package is incomplete: {source}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"failure adapter output exists: {target}")
    rendered = render_failure_adapter_variant_v1(main_path.read_bytes(), candidate_id)
    target.mkdir(parents=True, exist_ok=False)
    _write_new(target / "main.py", rendered)
    _write_new(target / "deck.csv", deck_path.read_bytes())
    return {
        "schema_version": FAILURE_ADAPTER_META_SCHEMA_V1,
        "candidate_id": candidate_id,
        "source_package": str(source),
        "output_package": str(target),
        "base_policy_sha256": _sha256_file(main_path),
        "policy_sha256": _sha256_bytes(rendered),
        "deck_bytes_sha256": _sha256_file(deck_path),
        "public_features": list(PUBLIC_FEATURES),
        "authority": {
            "training": False,
            "promotion": False,
            "submission": False,
            "longrun": False,
            "teacher": False,
        },
        "research_only": True,
    }


def _meta_row(row: Mapping[str, object], *, source_sha: str, variant: str) -> dict[str, object]:
    return {
        "opponent_id": str(row["id"]),
        "archetype": f"FailureAdapter:{variant}",
        "deck_sha256": str(row["canonical_deck_hash"]),
        "policy_sha256": str(row["policy_hash"]),
        "source_sha256": source_sha,
        "weight": 1.0,
        "usage_boundary": LOCAL_EVAL_ONLY_V1,
        "training_exposure": 0,
        "source": FAILURE_ADAPTER_SOURCE_V1,
        "derivation_recipe": FAILURE_ADAPTER_RECIPE_V1,
    }


def _build_split(
    *,
    output: Path,
    rows: Sequence[Mapping[str, object]],
    meta_rows: Sequence[Mapping[str, object]],
    p1_package: Path,
    meta_name: str = "meta_manifest.json",
    split_name: str = "cg_failure_adapter_intake_split.json",
) -> Path:
    if len(rows) < 4:
        raise FailureAdapterMetaError("at least four adapter references are required for train/dev/final separation")
    p1_main = p1_package / "main.py"
    p1_deck = p1_package / "deck.csv"
    if not p1_main.is_file() or not p1_deck.is_file():
        raise FailureAdapterMetaError("P1 package must contain main.py and deck.csv")
    meta_path = output / meta_name
    pool_path = output / "pool_manifest.json"
    ids = sorted(str(row["id"]) for row in rows)
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
            "meta_manifest_sha256": _sha256_file(meta_path),
            "pool_manifest_sha256": _sha256_file(pool_path),
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
        "sources": {"meta_manifest_path": _relative(meta_path), "pool_manifest_path": _relative(pool_path)},
        "evaluation_contract": {
            "both_seats": True,
            "fault_inclusive": True,
            "training_exposure": 0,
            "teacher_labels_saved": False,
            "final_results_read_during_search": False,
        },
        "train_blocks": [ids[:-2]],
        "splits": {
            "META_TRAIN": [split_row(item) for item in ids[:-2]],
            "META_DEV": [split_row(ids[-2])],
            "META_FINAL": [split_row(ids[-1])],
        },
        "notes": [
            "Self-owned public-state failure adapters are local-eval-only challenge sources.",
            "No action labels, private opponent fields, or final results are used to construct the source.",
            "Runtime smoke must promote every row before this split is bound to CEM.",
        ],
    }
    split_path = output / split_name
    _write_json_new(split_path, split)
    return split_path


def _source_sha(base_policy_sha: str, variant: str, policy_sha: str, deck_sha: str) -> str:
    return _sha256_bytes(
        _canonical_json(
            {
                "recipe": FAILURE_ADAPTER_RECIPE_V1,
                "base_policy_sha256": base_policy_sha,
                "variant": variant,
                "policy_sha256": policy_sha,
                "deck_sha256": deck_sha,
            }
        )
    )


def seal_failure_adapter_meta_v1(
    *,
    source_package: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = VARIANT_IDS,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a new self-owned adapter pool; runtime smoke remains separate."""

    if not source_epoch.strip() or not seed_namespace.strip():
        raise FailureAdapterMetaError("source_epoch and seed_namespace must be non-empty")
    ordered = tuple(str(value) for value in variants)
    if len(ordered) < 4 or len(set(ordered)) != len(ordered) or any(value not in VARIANT_IDS for value in ordered):
        raise FailureAdapterMetaError("variants must contain at least four unique declared adapter ids")
    source = Path(source_package).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite failure-adapter intake root: {output}")
    main_path = source / "main.py"
    deck_path = source / "deck.csv"
    if not source.is_dir() or main_path.is_symlink() or deck_path.is_symlink() or not main_path.is_file() or not deck_path.is_file():
        raise FailureAdapterMetaError(f"source package is incomplete: {source}")
    base_bytes = main_path.read_bytes()
    base_policy_sha = _sha256_bytes(base_bytes)
    findings, base_imports = scan_source_text(base_bytes.decode("utf-8", errors="strict"))
    if findings:
        raise FailureAdapterMetaError(f"base policy is statically unsafe: {findings}")
    _cards, deck_sha = _parse_and_validate_deck(deck_path.read_bytes(), candidate_id=source.name, repo_root=_ROOT)
    existing_pairs = _existing_pairs(Path(current_pool_manifest).resolve() if current_pool_manifest is not None else None)
    roots = tuple(Path(value).resolve() for value in scan_roots)
    output.mkdir(parents=True, exist_ok=False)

    rows: list[dict[str, object]] = []
    meta_rows: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    evidence_dir = output / "evidence"
    for variant in ordered:
        rendered = render_failure_adapter_variant_v1(base_bytes, variant)
        policy_sha = _sha256_bytes(rendered)
        candidate_id = f"failure_adapter_{variant}_{policy_sha[:12]}"
        if not _ID.fullmatch(candidate_id):
            raise FailureAdapterMetaError(f"generated candidate id is invalid: {candidate_id}")
        pair = (policy_sha, deck_sha)
        if pair in existing_pairs or any((str(row["policy_hash"]), str(row["canonical_deck_hash"])) == pair for row in rows):
            raise FailureAdapterMetaError(f"adapter pair identity already exists: {candidate_id}")
        if _artifact_contains(roots, (candidate_id, policy_sha)):
            raise FailureAdapterMetaError(f"adapter identity already appears in configured artifacts: {candidate_id}")
        target = output / candidate_id
        target.mkdir(parents=True, exist_ok=False)
        _write_new(target / "main.py", rendered)
        deck_bytes = deck_path.read_bytes()
        _write_new(target / "deck.csv", deck_bytes)
        source_sha = _source_sha(base_policy_sha, variant, policy_sha, deck_sha)
        _write_new(
            target / "SOURCE.md",
            (
                "# Self-owned failure-conditioned opponent adapter (research-only)\n\n"
                f"- derivation recipe: `{FAILURE_ADAPTER_RECIPE_V1}`\n"
                f"- base policy SHA-256: `{base_policy_sha}`\n"
                f"- generated policy SHA-256: `{policy_sha}`\n"
                f"- canonical deck SHA-256: `{deck_sha}`\n"
                f"- source SHA-256: `{source_sha}`\n"
                f"- adapter variant: `{variant}`\n"
                f"- public features: `{','.join(PUBLIC_FEATURES)}`\n"
                "- parent usage context: `BestKnown base was performance-exposed; generated policy/deck pair is fresh`\n"
                "- usage boundary: `local_eval_only`\n"
                "- runtime smoke: `REQUIRED_BEFORE_CEM`\n"
                "- submission bundle: prohibited\n"
            ).encode("utf-8"),
        )
        evidence = {
            "candidate_id": candidate_id,
            "fresh": True,
            "unused_before_run": True,
            "parent_performance_exposed": True,
            "source": FAILURE_ADAPTER_SOURCE_V1,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "derivation_recipe": FAILURE_ADAPTER_RECIPE_V1,
            "adapter_variant": variant,
            "base_policy_sha256": base_policy_sha,
            "policy_sha256": policy_sha,
            "canonical_deck_hash": deck_sha,
            "source_sha256": source_sha,
            "public_features": list(PUBLIC_FEATURES),
            "base_imports": list(base_imports),
            "static_findings": [],
            "runtime_smoke_required": True,
            "failure_condition_source": "P1 aggregate outcome telemetry; no action labels or private fields retained",
        }
        evidence_path = evidence_dir / f"{candidate_id}.json"
        _write_json_new(evidence_path, evidence)
        row = {
            "id": candidate_id,
            "canonical_deck_hash": deck_sha,
            "mean_decision_ms": None,
            "policy_hash": policy_sha,
            "source_policy_sha256": base_policy_sha,
            "smoke_ok": False,
            "source": FAILURE_ADAPTER_SOURCE_V1,
            "source_branch": f"internal/failure-adapter/{variant}",
            "source_commit": f"self-owned-base-{base_policy_sha[:16]}",
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "asset_preflight": "STATIC_AND_EXACT_60",
            "derivation_recipe": FAILURE_ADAPTER_RECIPE_V1,
            "adapter_variant": variant,
        }
        rows.append(row)
        meta_rows.append(_meta_row(row, source_sha=source_sha, variant=variant))
        references.append(
            {
                "id": candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256_file(evidence_path),
                "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
                "policy_sha256": policy_sha,
                "canonical_deck_hash": deck_sha,
                "source": FAILURE_ADAPTER_SOURCE_V1,
                "source_sha256": source_sha,
                "adapter_variant": variant,
            }
        )

    rows.sort(key=lambda row: str(row["id"]))
    meta_rows.sort(key=lambda row: str(row["opponent_id"]))
    references.sort(key=lambda row: str(row["id"]))
    pool_path = output / "pool_manifest.json"
    _write_json_new(pool_path, rows)
    meta_path = output / "meta_manifest.json"
    _write_json_new(
        meta_path,
        {
            "schema_version": "cg-failure-adapter-meta-distribution-v1",
            "research_only": True,
            "source_kind": FAILURE_ADAPTER_SOURCE_V1,
            "rows": meta_rows,
        },
    )
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": [str(row["id"]) for row in rows]}))
    fresh = {
        "schema_version": FRESH_META_SCHEMA_V1,
        "batch_id": f"failure-adapter-{re.sub(r'[^A-Za-z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "pool_manifest_sha256": _sha256_file(pool_path),
        "reference_ids": [str(row["id"]) for row in rows],
        "references": references,
        "freshness_basis": "new self-owned public-state adapter policy SHA; pair-level artifact identity scan; runtime smoke pending",
        "parent_performance_exposed": True,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    fresh_path = output / "fresh_meta.json"
    _write_json_new(fresh_path, fresh)
    p1_root = Path(p1_package).resolve()
    split_path = _build_split(output=output, rows=rows, meta_rows=meta_rows, p1_package=p1_root)
    report = {
        "schema_version": FAILURE_ADAPTER_META_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "recipe": FAILURE_ADAPTER_RECIPE_V1,
        "base_policy_sha256": base_policy_sha,
        "accepted_count": len(rows),
        "accepted_ids": [str(row["id"]) for row in rows],
        "pool_manifest_path": str(pool_path),
        "pool_manifest_sha256": _sha256_file(pool_path),
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": _sha256_file(meta_path),
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
        "runtime_smoke_required": True,
        "public_features": list(PUBLIC_FEATURES),
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
        "imports_executed": False,
        "network_access": False,
    }
    _write_json_new(output / "intake_report.json", report)
    load_opponent_pool_v1(output)
    return report


def build_failure_adapter_split_v1(*, output_root: Path | str, p1_package: Path | str) -> dict[str, object]:
    """Bind a new split to a fault-free promoted pool without mutating intake files."""

    output = Path(output_root).resolve()
    pool_path = output / "pool_manifest.json"
    fresh_path = output / "fresh_meta.json"
    if not pool_path.is_file() or not fresh_path.is_file():
        raise FailureAdapterMetaError("promoted root must contain pool_manifest.json and fresh_meta.json")
    try:
        raw_pool = json.loads(pool_path.read_text(encoding="utf-8"))
        fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FailureAdapterMetaError("promoted source manifests are unreadable") from exc
    rows = raw_pool if isinstance(raw_pool, list) else raw_pool.get("opponents", raw_pool) if isinstance(raw_pool, Mapping) else None
    if not isinstance(rows, list) or len(rows) < 4 or any(not isinstance(row, Mapping) for row in rows):
        raise FailureAdapterMetaError("promoted pool must contain at least four rows")
    if any(row.get("smoke_ok") is not True for row in rows):
        raise FailureAdapterMetaError("split can be rebound only after smoke promotion")
    refs = fresh.get("references") if isinstance(fresh, Mapping) else None
    if not isinstance(refs, list):
        raise FailureAdapterMetaError("fresh_meta.references must be a list")
    ref_by_id = {str(ref.get("id")): ref for ref in refs if isinstance(ref, Mapping)}
    meta_rows: list[dict[str, object]] = []
    for row in rows:
        candidate_id = str(row.get("id", ""))
        ref = ref_by_id.get(candidate_id)
        if ref is None:
            raise FailureAdapterMetaError(f"fresh_meta is missing {candidate_id}")
        meta_rows.append(
            {
                "opponent_id": candidate_id,
                "archetype": f"FailureAdapter:{row.get('adapter_variant', candidate_id)}",
                "deck_sha256": str(row["canonical_deck_hash"]),
                "policy_sha256": str(row["policy_hash"]),
                "source_sha256": str(ref.get("source_sha256")),
                "weight": 1.0,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "training_exposure": 0,
                "source": FAILURE_ADAPTER_SOURCE_V1,
                "derivation_recipe": FAILURE_ADAPTER_RECIPE_V1,
            }
        )
    meta_path = output / "meta_manifest_rebound.json"
    split_path = output / "cg_failure_adapter_split.json"
    if meta_path.exists() or split_path.exists():
        raise FileExistsError("failure adapter rebound artifacts already exist")
    _write_json_new(
        meta_path,
        {
            "schema_version": "cg-failure-adapter-meta-distribution-v1",
            "research_only": True,
            "source_kind": FAILURE_ADAPTER_SOURCE_V1,
            "rows": sorted(meta_rows, key=lambda row: str(row["opponent_id"])),
        },
    )
    split_path = _build_split(
        output=output,
        rows=rows,
        meta_rows=meta_rows,
        p1_package=Path(p1_package).resolve(),
        meta_name=meta_path.name,
        split_name=split_path.name,
    )
    return {
        "status": "SEALED",
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": _sha256_file(meta_path),
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
    }


__all__ = [
    "FAILURE_ADAPTER_META_SCHEMA_V1",
    "FAILURE_ADAPTER_RECIPE_V1",
    "FAILURE_ADAPTER_SOURCE_V1",
    "FailureAdapterMetaError",
    "PUBLIC_FEATURES",
    "VARIANT_IDS",
    "build_failure_adapter_split_v1",
    "materialize_failure_adapter_variant_v1",
    "render_failure_adapter_variant_v1",
    "seal_failure_adapter_meta_v1",
]
