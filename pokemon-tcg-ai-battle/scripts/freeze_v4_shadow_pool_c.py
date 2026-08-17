#!/usr/bin/env python3
"""Freeze an identity-anchored, untouched V4 shadow-C opponent cohort.

shadow-C is intentionally built from the previously unused public medal-zone
deck snapshots.  The generated opponents all use the repository's generic
local evaluator policy, so this cohort is *deck-identity disjoint* from the
fixed-six, shadow-A, and shadow-B cohorts but does not provide six independent
policy families.  That limitation is recorded in the manifest rather than
silently treating the deck snapshots as independent agents.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
POOL_PATH = ROOT / "opponents/pool_manifest.json"
SHADOW_A_MANIFEST = ROOT / "runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json"
SHADOW_B_MANIFEST = ROOT / "runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json"

FIXED_IDS = (
    "kiyotah_lucario",
    "nihei_megalopunny",
    "ozawa_crustle_v2",
    "skarin_dragapult",
    "sue124_alakazam",
    "yaroslav_crustleaware_lucario",
)

# Highest observed public medal-zone deck identities selected for broad
# archetype coverage.  These IDs were absent from all V4 artifacts at freeze
# time; no strength or fault claim is made by this selection.
SHADOW_C_IDS = (
    "medal_0001_77a53ffc",  # Mega Lucario ex / Hariyama
    "medal_0004_01501d64",  # Mega Lopunny ex / Dudunsparce
    "medal_0006_07bedfff",  # Dragapult ex / Fezandipiti ex
    "medal_0010_4bf59ca5",  # Mega Kangaskhan ex / Cornerstone Mask Ogerpon ex
    "medal_0015_5e60b8c7",  # Teal Mask Ogerpon ex
    "medal_0016_706fa912",  # Thwackey / Dipplin
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_deck_sha256(deck: Iterable[int]) -> str:
    payload = ",".join(str(card) for card in sorted(deck)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_deck(path: Path) -> list[int]:
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != 60:
        raise ValueError(f"deck must contain exactly 60 non-empty lines: {path}")
    try:
        deck = [int(value) for value in values]
    except ValueError as exc:
        raise ValueError(f"deck contains a non-integer card id: {path}") from exc
    return deck


def _field(row: Mapping[str, object], *names: str) -> object:
    for name in names:
        if name in row:
            return row[name]
    raise KeyError(f"missing any of {names!r}")


def _load_manifest(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_rows(manifest: object) -> list[Mapping[str, object]]:
    if not isinstance(manifest, Mapping):
        raise ValueError("shadow manifest must be an object")
    rows = manifest.get("candidates")
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("shadow manifest candidates must be a list of objects")
    return list(rows)


def _artifact_contains(root: Path, candidate_id: str, *, exclude: Path | None = None) -> bool:
    runs_root = root / "runs"
    if not runs_root.is_dir():
        return False
    for path in runs_root.glob("meta-specialist-v4*/**/*"):
        if not path.is_file() or path.suffix not in {".json", ".md"}:
            continue
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        if path.stat().st_size > 20_000_000:
            continue
        if candidate_id in path.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


def _build_candidate(root: Path, row: Mapping[str, object], *, fixed_rows: list[Mapping[str, object]],
                     shadow_a_rows: list[Mapping[str, object]], shadow_b_rows: list[Mapping[str, object]],
                     artifact_exclude: Path | None) -> dict[str, object]:
    candidate_id = row.get("id")
    if candidate_id not in SHADOW_C_IDS:
        raise ValueError(f"unexpected shadow-C candidate: {candidate_id}")
    if row.get("source") != "public" or row.get("smoke_ok") is not True or row.get("usage_boundary") != "local_eval_only":
        raise ValueError(f"candidate is not a qualified public local-eval opponent: {candidate_id}")

    directory = root / "opponents" / str(candidate_id)
    required = {name: directory / name for name in ("SOURCE.md", "deck.csv", "main.py")}
    if any(not path.is_file() for path in required.values()):
        raise ValueError(f"candidate assets are incomplete: {candidate_id}")

    deck = read_deck(required["deck.csv"])
    actual_deck_hash = canonical_deck_sha256(deck)
    actual_policy_hash = sha256_file(required["main.py"])
    actual_deck_file_hash = sha256_file(required["deck.csv"])
    if actual_deck_hash != row.get("canonical_deck_hash"):
        raise ValueError(f"candidate canonical deck hash does not match source: {candidate_id}")
    if actual_policy_hash != row.get("policy_hash"):
        raise ValueError(f"candidate policy hash does not match source file: {candidate_id}")

    prior_decks = {
        _field(item, "canonical_deck_hash", "canonical_deck_sha256")
        for item in (*fixed_rows, *shadow_a_rows, *shadow_b_rows)
    }
    prior_policies = {
        _field(item, "policy_hash", "policy_sha256")
        for item in (*fixed_rows, *shadow_a_rows, *shadow_b_rows)
    }
    if actual_deck_hash in prior_decks:
        raise ValueError(f"candidate deck identity overlaps fixed/shadow-A/shadow-B: {candidate_id}")
    if actual_policy_hash in prior_policies:
        raise ValueError(f"candidate policy identity overlaps fixed/shadow-A/shadow-B: {candidate_id}")
    if _artifact_contains(root, str(candidate_id), exclude=artifact_exclude):
        raise ValueError(f"candidate ID already appears in a V4 artifact: {candidate_id}")

    return {
        "id": candidate_id,
        "source": row["source"],
        "usage_boundary": row["usage_boundary"],
        "policy_path": str(required["main.py"].relative_to(root)),
        "policy_sha256": actual_policy_hash,
        "deck_path": str(required["deck.csv"].relative_to(root)),
        "deck_file_sha256": actual_deck_file_hash,
        "canonical_deck_sha256": actual_deck_hash,
        "source_metadata_path": str(required["SOURCE.md"].relative_to(root)),
        "source_metadata_sha256": sha256_file(required["SOURCE.md"]),
    }


def build_payload(*, root: Path = ROOT, output: Path | None = None,
                  frozen_at: str | None = None) -> dict[str, object]:
    pool_payload = _load_manifest(root / "opponents/pool_manifest.json")
    if not isinstance(pool_payload, list) or not all(isinstance(row, Mapping) for row in pool_payload):
        raise ValueError("opponent pool manifest must be a list of objects")
    by_id = {str(row["id"]): row for row in pool_payload}

    missing = sorted(set(SHADOW_C_IDS) - set(by_id))
    if missing:
        raise ValueError(f"shadow-C candidates are absent from pool manifest: {missing}")
    fixed_missing = sorted(set(FIXED_IDS) - set(by_id))
    if fixed_missing:
        raise ValueError(f"fixed-six candidates are absent from pool manifest: {fixed_missing}")

    shadow_a_path = root / "runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json"
    shadow_b_path = root / "runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json"
    shadow_a_rows = _candidate_rows(_load_manifest(shadow_a_path))
    shadow_b_rows = _candidate_rows(_load_manifest(shadow_b_path))
    fixed_rows = [by_id[item] for item in FIXED_IDS]
    artifact_exclude = output.resolve() if output is not None else None
    candidates = [
        _build_candidate(
            root,
            by_id[item],
            fixed_rows=fixed_rows,
            shadow_a_rows=shadow_a_rows,
            shadow_b_rows=shadow_b_rows,
            artifact_exclude=artifact_exclude,
        )
        for item in SHADOW_C_IDS
    ]

    deck_hashes = [str(item["canonical_deck_sha256"]) for item in candidates]
    policy_hashes = [str(item["policy_sha256"]) for item in candidates]
    policy_groups: dict[str, list[str]] = {}
    for item, policy_hash in zip(candidates, policy_hashes):
        policy_groups.setdefault(policy_hash, []).append(str(item["id"]))
    shared_policy_groups = [
        {"policy_sha256": policy_hash, "ids": ids}
        for policy_hash, ids in sorted(policy_groups.items())
        if len(ids) > 1
    ]

    source_manifest = root / "opponents/pool_manifest.json"
    return {
        "schema_version": "meta-specialist-v4-shadow-pool-v3",
        "selection_status": "frozen_untouched_shadow_c_not_yet_evaluated",
        "frozen_at": frozen_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        "purpose": "次候補の選択後にのみ評価する、medal-zone deck identity の untouched shadow-C cohort を固定する。",
        "role": "promotion_untouched_test_candidate",
        "identity_scope": "deck_disjoint_from_fixed_shadow_a_shadow_b; policy_family_shared_within_cohort",
        "selection_criteria": {
            "selected_ids": list(SHADOW_C_IDS),
            "excluded_fixed_six": list(FIXED_IDS),
            "excluded_shadow_a": [str(item["id"]) for item in shadow_a_rows],
            "excluded_shadow_b": [str(item["id"]) for item in shadow_b_rows],
            "required": [
                "opponents/pool_manifest.json で source=public、smoke_ok=true、usage_boundary=local_eval_only",
                "fixed-six、shadow-A、shadow-B と canonical deck SHA-256 および policy SHA-256 が重複しない",
                "candidate 内で canonical deck SHA-256 が一意である",
                "SOURCE.md、deck.csv、main.py が存在し、source file の SHA-256 を freeze する",
                "freeze 時点の runs/meta-specialist-v4* JSON/Markdown に candidate ID が存在しない",
            ],
            "selection_scope": "identity の凍結のみ。強度、fault 0、時間制限、統計的汎化は未検証。",
        },
        "identity_checks": {
            "candidate_count": len(candidates),
            "deck_hash_unique_within_cohort": len(set(deck_hashes)) == len(deck_hashes),
            "policy_hash_unique_within_cohort": len(set(policy_hashes)) == len(policy_hashes),
            "shared_policy_groups": shared_policy_groups,
            "deck_hash_disjoint_from_prior_cohorts": True,
            "policy_hash_disjoint_from_prior_cohorts": True,
            "v4_artifact_reference_absent_at_freeze": True,
        },
        "source_manifest": {
            "path": str(source_manifest.relative_to(root)),
            "sha256": sha256_file(source_manifest),
            "git_state_at_freeze": "modified; content SHA-256 により snapshot を固定",
        },
        "prior_manifests": {
            "shadow_a": {
                "path": str(shadow_a_path.relative_to(root)),
                "sha256": sha256_file(shadow_a_path),
            },
            "shadow_b": {
                "path": str(shadow_b_path.relative_to(root)),
                "sha256": sha256_file(shadow_b_path),
            },
        },
        "candidates": candidates,
        "limitations": [
            "medal_* の main.py は全候補で同じ generic local-eval policy SHA-256 を共有するため、独立 policy family の shadow ではない。",
            "この cohort は deck identity の外部診断用であり、同一 policy の複数 materialization を独立再現として数えない。",
            "source=public は decklist の出所だけを意味し、元 leaderboard team の agent や戦略の再現ではない。",
        ],
        "unverified": [
            "この cohort の CABT 対戦、fault 0、時間制限、両 seat は未実施。",
            "pool_manifest の smoke_ok は既存観測であり、この freeze 時点で再実行していない。",
            "shadow-C の勝率は候補選択・development 評価が完了するまで参照しない。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = build_payload(output=args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
