#!/usr/bin/env python3
"""Seal a fresh robust-source pool and a hash-bound weekend split.

The input packages must already have passed independent source validation. This
step copies them into a new root, runs a bounded P1-vs-source smoke, and emits
the pool/meta/fresh/split artifacts consumed by the existing P1 CEM runner.
It is research-only and never mutates the production opponent pool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import AUTHORITY_FALSE_V1  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import BASE_SOURCE_SHA256  # noqa: E402
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256  # noqa: E402
from scripts import run_robust_adversarial_source_cem_v1 as source_runner  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1  # noqa: E402


SCHEMA_V1 = "meta-specialist-robust-source-weekend-pool-v1"


class SealError(ValueError):
    """Raised when a source pool cannot be sealed safely."""


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise SealError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deck_sha(path: Path) -> str:
    cards = [int(token) for token in path.read_text(encoding="utf-8").split()]
    if len(cards) != 60:
        raise SealError(f"deck must contain 60 cards: {path}")
    return canonical_deck_sha256(cards)


def _write_new(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _parse_candidate(value: str) -> tuple[str, Path, str]:
    if "=" not in value:
        raise SealError("candidate must be ID=PACKAGE_ROOT")
    candidate_id, raw = value.split("=", 1)
    if source_runner._ID.fullmatch(candidate_id) is None:
        raise SealError(f"invalid candidate id: {candidate_id!r}")
    source = Path(raw)
    if not source.is_absolute():
        source = _ROOT / source
    return candidate_id, source.resolve(), ""


def _parse_assignment(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise SealError("assignment must be SPLIT=CANDIDATE_ID")
    split, candidate_id = value.split("=", 1)
    if split not in {"META_TRAIN", "META_DEV", "META_FINAL"}:
        raise SealError(f"invalid split: {split!r}")
    if source_runner._ID.fullmatch(candidate_id) is None:
        raise SealError(f"invalid candidate id: {candidate_id!r}")
    return split, candidate_id


def _copy_package(source: Path, target: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise SealError(f"package is not a regular directory: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise SealError(f"package contains symlink: {path}")
    shutil.copytree(source, target)
    if not (target / "main.py").is_file() or not (target / "deck.csv").is_file():
        raise SealError(f"package missing main.py/deck.csv: {source}")


def seal_pool(
    *,
    output_root: Path,
    p1_package: Path,
    candidate_specs: Sequence[tuple[str, Path, str]],
    assignments: Sequence[tuple[str, str]],
    seed: int = 2026084001,
    workers: int = 4,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(output_root)
    if not candidate_specs:
        raise SealError("at least one candidate is required")
    if _sha256(p1_package / "main.py") != BASE_SOURCE_SHA256:
        raise SealError("P1 package main.py does not match sealed parent")
    assignment_map: dict[str, str] = {}
    for split, candidate_id in assignments:
        if candidate_id in assignment_map:
            raise SealError(f"candidate assigned more than once: {candidate_id}")
        assignment_map[candidate_id] = split
    if set(assignment_map) != {item[0] for item in candidate_specs}:
        raise SealError("every candidate must have exactly one split assignment")
    if not all(any(split == name for split, _ in assignments) for name in ("META_TRAIN", "META_DEV", "META_FINAL")):
        raise SealError("all weekend splits must be non-empty")

    output_root.mkdir(parents=True)
    pool_root = output_root / "pool"
    pool_root.mkdir()
    rows: list[dict[str, object]] = []
    meta_rows: list[dict[str, object]] = []
    package_by_id: dict[str, Path] = {}
    policy_seen: set[str] = set()
    for candidate_id, source, _ in candidate_specs:
        target = pool_root / candidate_id
        _copy_package(source, target)
        package_by_id[candidate_id] = target
        policy_sha = _sha256(target / "main.py")
        if policy_sha in policy_seen:
            raise SealError(f"duplicate policy SHA: {policy_sha}")
        policy_seen.add(policy_sha)
        source_manifest = next(
            (path for path in (target / "adversarial_source_manifest.json", target / "source_manifest.json") if path.is_file()),
            target / "main.py",
        )
        deck_sha = _deck_sha(target / "deck.csv")
        rows.append(
            {
                "id": candidate_id,
                "policy_hash": policy_sha,
                "canonical_deck_hash": deck_sha,
                "source": "self_owned_robust_source_independent_validation_v1",
                "usage_boundary": "local_eval_only",
                "smoke_ok": False,
            }
        )
        meta_rows.append(
            {
                "opponent_id": candidate_id,
                "archetype": "SelfOwnedRobustSourceCEM:" + candidate_id,
                "deck_sha256": deck_sha,
                "policy_sha256": policy_sha,
                "source_sha256": _sha256(source_manifest),
                "source": "self_owned_robust_source_independent_validation_v1",
                "weight": 1.0 / len(candidate_specs),
                "usage_boundary": "local_eval_only",
                "training_exposure": 0,
            }
        )

    pool_manifest = pool_root / "pool_manifest.json"
    _write_new(pool_manifest, rows)
    p1_smoke_games: list[object] = []
    for index, (candidate_id, _, _) in enumerate(candidate_specs):
        p1_smoke_games.extend(
            source_runner._smoke_games(
                p1_package=p1_package,
                source_package=package_by_id[candidate_id],
                pool_root=pool_root,
                source_id=candidate_id,
                base_seed=seed + index * 100,
            )
        )
    smoke = source_runner._evaluate(p1_smoke_games, output_root / "p1_source_smoke", workers=workers)
    smoke_rows = smoke["rows"]
    smoke_ok = len(smoke_rows) == len(p1_smoke_games) and all(str(row.get("outcome")) in {"win", "draw", "loss"} for row in smoke_rows)
    if not smoke_ok:
        raise SealError("P1 source smoke has a fault")
    for row in rows:
        row["smoke_ok"] = True
    pool_manifest.write_bytes(_canonical_json(rows))

    meta_manifest = output_root / "meta_manifest.json"
    _write_new(
        meta_manifest,
        {
            "schema_version": SCHEMA_V1,
            "rows": meta_rows,
            "source_epoch": "robust-source-independent-validation-20260820",
            "fresh": True,
            "unused_before_downstream_policy_run": True,
            "research_only": True,
            "authority": dict(AUTHORITY_FALSE_V1),
        },
    )
    fresh_meta = output_root / "fresh_meta.json"
    _write_new(
        fresh_meta,
        {
            "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
            "batch_id": "robust-source-independent-validation-weekend-20260820",
            "references": [
                {
                    "id": row["opponent_id"],
                    "fresh": True,
                    "unused_before_run": True,
                    "policy_sha256": row["policy_sha256"],
                    "canonical_deck_hash": row["deck_sha256"],
                    "source_sha256": row["source_sha256"],
                    "source": row["source"],
                }
                for row in meta_rows
            ],
            "freshness_basis": "independent-source-validation-before-policy-CEM",
            "authority": dict(AUTHORITY_FALSE_V1),
            "research_only": True,
        },
    )

    split_rows: dict[str, list[dict[str, object]]] = {name: [] for name in ("META_TRAIN", "META_DEV", "META_FINAL")}
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}
    for split, candidate_id in assignments:
        split_rows[split].append(dict(meta_by_id[candidate_id]))
    split = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "candidate_exclusion_ids": [],
        "splits": split_rows,
        "train_blocks": [[str(row["opponent_id"]) for row in split_rows["META_TRAIN"]]],
        "sources": {
            "fresh_meta_path": fresh_meta.name,
            "meta_manifest_path": meta_manifest.name,
            "pool_manifest_path": str(Path("pool") / pool_manifest.name),
        },
        "bindings": {
            "p1_policy_sha256": BASE_SOURCE_SHA256,
            "p1_deck_sha256": _sha256(p1_package / "deck.csv"),
            "meta_manifest_sha256": _sha256(meta_manifest),
            "pool_manifest_sha256": _sha256(pool_manifest),
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
        "evaluation_contract": {
            "both_seats": True,
            "train_games_per_opponent_seat": 2,
            "dev_games_per_opponent_seat": 2,
            "final_games_per_opponent_seat": 2,
            "fault_inclusive": True,
            "final_results_read_during_search": False,
            "teacher_labels_saved": False,
            "training_exposure": 0,
        },
        "notes": [
            "Fresh self-owned robust-source candidates were independently revalidated before this split.",
            "Only META_TRAIN is eligible for the first P1 policy CEM; DEV and FINAL remain untouched.",
            "The source CEM screen and independent source validation are prior evidence, not policy-CEM performance evidence.",
        ],
        "research_only": True,
    }
    split_path = output_root / "cg_weekend_split.json"
    _write_new(split_path, split)
    result = {
        "schema_version": SCHEMA_V1,
        "status": "COMPLETE",
        "pool_manifest": str(pool_manifest.resolve()),
        "meta_manifest": str(meta_manifest.resolve()),
        "fresh_meta": str(fresh_meta.resolve()),
        "split": str(split_path.resolve()),
        "p1_smoke_summary": smoke["summary"],
        "assignments": assignment_map,
        "fresh": True,
        "unused_before_downstream_policy_run": True,
        "research_only": True,
    }
    _write_new(output_root / "seal_result.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--p1-package", type=Path, default=source_runner.DEFAULT_P1_PACKAGE)
    parser.add_argument("--candidate", action="append", required=True, help="ID=PACKAGE_ROOT")
    parser.add_argument("--assign", action="append", required=True, help="META_TRAIN|META_DEV|META_FINAL=CANDIDATE_ID")
    parser.add_argument("--seed", type=int, default=2026084001)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        specs = tuple(_parse_candidate(value) for value in args.candidate)
        assignments = tuple(_parse_assignment(value) for value in args.assign)
        result = seal_pool(
            output_root=args.output,
            p1_package=args.p1_package.resolve(),
            candidate_specs=specs,
            assignments=assignments,
            seed=args.seed,
            workers=args.workers,
        )
    except (SealError, source_runner.RobustSourceCampaignError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
