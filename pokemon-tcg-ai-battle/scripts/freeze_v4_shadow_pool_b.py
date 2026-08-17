#!/usr/bin/env python3
"""Freeze a hash-anchored, identity-disjoint V4 shadow-B opponent cohort."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
FIXED_IDS = {
    "kiyotah_lucario", "nihei_megalopunny", "ozawa_crustle_v2",
    "skarin_dragapult", "sue124_alakazam", "yaroslav_crustleaware_lucario",
}
SHADOW_A_MANIFEST = ROOT / "runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json"
SHADOW_B_IDS = (
    "biohack44_crustlecounter2",
    "harukiharada_crustle",
    "kiyotah_iono",
    "naoto714_ursaluna",
    "pilkwang_lucario_alakazam",
    "prvsiyan_grimmsnarl",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_pool() -> tuple[Path, list[dict[str, object]]]:
    path = ROOT / "opponents/pool_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("opponent pool manifest must be a list")
    return path, payload


def _artifact_contains(candidate_id: str) -> bool:
    for path in (*ROOT.glob("runs/meta-specialist-v4*/**/*.json"), *ROOT.glob("runs/meta-specialist-v4*/**/*.md")):
        if path.stat().st_size > 20_000_000:
            continue
        if candidate_id in path.read_text(encoding="utf-8", errors="ignore"):
            return True
    return False


def _candidate(row: dict[str, object], *, fixed_rows: list[dict[str, object]], shadow_a_rows: list[dict[str, object]]) -> dict[str, object]:
    candidate_id = row.get("id")
    if candidate_id not in SHADOW_B_IDS:
        raise ValueError(f"unexpected shadow-B candidate: {candidate_id}")
    if row.get("source") != "public" or row.get("smoke_ok") is not True or row.get("usage_boundary") != "local_eval_only":
        raise ValueError(f"candidate is not a qualified public local-eval opponent: {candidate_id}")
    fixed_decks = {item.get("canonical_deck_hash") for item in fixed_rows}
    fixed_policies = {item.get("policy_hash") for item in fixed_rows}
    shadow_a_decks = {item.get("canonical_deck_sha256") for item in shadow_a_rows}
    shadow_a_policies = {item.get("policy_sha256") for item in shadow_a_rows}
    if row.get("canonical_deck_hash") in fixed_decks | shadow_a_decks:
        raise ValueError(f"candidate deck identity overlaps a frozen cohort: {candidate_id}")
    if row.get("policy_hash") in fixed_policies | shadow_a_policies:
        raise ValueError(f"candidate policy identity overlaps a frozen cohort: {candidate_id}")
    directory = ROOT / "opponents" / str(candidate_id)
    required = {name: directory / name for name in ("SOURCE.md", "deck.csv", "main.py")}
    if any(not path.is_file() for path in required.values()):
        raise ValueError(f"candidate assets are incomplete: {candidate_id}")
    if _sha(required["main.py"]) != row.get("policy_hash"):
        raise ValueError(f"candidate policy hash does not match source file: {candidate_id}")
    if _artifact_contains(str(candidate_id)):
        raise ValueError(f"candidate ID already appears in a V4 artifact: {candidate_id}")
    return {
        "id": candidate_id,
        "source": row["source"],
        "usage_boundary": row["usage_boundary"],
        "policy_path": str((directory / "main.py").relative_to(ROOT)),
        "policy_sha256": row["policy_hash"],
        "deck_path": str((directory / "deck.csv").relative_to(ROOT)),
        "deck_file_sha256": _sha(directory / "deck.csv"),
        "canonical_deck_sha256": row["canonical_deck_hash"],
        "source_metadata_path": str((directory / "SOURCE.md").relative_to(ROOT)),
        "source_metadata_sha256": _sha(directory / "SOURCE.md"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    source_path, pool = _read_pool()
    by_id = {str(row["id"]): row for row in pool}
    if set(SHADOW_B_IDS) != set(by_id).intersection(SHADOW_B_IDS):
        missing = sorted(set(SHADOW_B_IDS) - set(by_id))
        raise ValueError(f"shadow-B candidates are absent from pool manifest: {missing}")
    shadow_a = json.loads(SHADOW_A_MANIFEST.read_text(encoding="utf-8"))
    shadow_a_rows = list(shadow_a["candidates"])
    fixed_rows = [by_id[item] for item in sorted(FIXED_IDS)]
    candidates = [_candidate(by_id[item], fixed_rows=fixed_rows, shadow_a_rows=shadow_a_rows) for item in SHADOW_B_IDS]
    if len({item["canonical_deck_sha256"] for item in candidates}) != len(candidates):
        raise ValueError("shadow-B canonical deck hashes are not unique")
    if len({item["policy_sha256"] for item in candidates}) != len(candidates):
        raise ValueError("shadow-B policy hashes are not unique")
    payload = {
        "schema_version": "meta-specialist-v4-shadow-pool-v2",
        "selection_status": "frozen_untouched_shadow_b_not_yet_evaluated",
        "frozen_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        "purpose": "新規 strict-disagreement candidate の promotion-untouched shadow-B 評価用 opponent cohort を固定する。",
        "role": "promotion_untouched_test_candidate",
        "selection_criteria": {
            "excluded_fixed_six": sorted(FIXED_IDS),
            "excluded_shadow_a": [item["id"] for item in shadow_a_rows],
            "required": [
                "opponents/pool_manifest.json で source=public、smoke_ok=true、usage_boundary=local_eval_only",
                "fixed-six と shadow-A の canonical deck/policy SHA-256、および shadow-B cohort 内の各 SHA-256 と重複しない",
                "SOURCE.md、deck.csv、main.py が存在し、policy/deck/source の SHA-256 を freeze する",
                "freeze 時点の runs/meta-specialist-v4* JSON/Markdown に candidate ID が存在しない",
            ],
            "selection_scope": "identity の凍結のみ。強度、fault 0、速度、generalization は未検証。",
        },
        "source_manifest": {
            "path": str(source_path.relative_to(ROOT)),
            "sha256": _sha(source_path),
            "git_state_at_freeze": "modified; content SHA-256 により snapshot を固定",
        },
        "shadow_a_manifest": {
            "path": str(SHADOW_A_MANIFEST.relative_to(ROOT)),
            "sha256": _sha(SHADOW_A_MANIFEST),
        },
        "candidates": candidates,
        "unverified": [
            "この cohort の CABT 対戦、fault 0、時間制限、両 seat は未実施。",
            "pool_manifest の smoke_ok は既存観測であり、この freeze 時点で再実行していない。",
            "shadow-B は新規 strict candidate の学習・arm選択に使わず、学習後の外部診断へ温存する。",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
