"""Build the autonomous meta distribution from existing immutable artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.meta_distribution_v1 import (  # noqa: E402
    MetaDistributionManifestV1,
    build_meta_distribution_manifest_v1,
    build_meta_schedule_v1,
    save_meta_distribution_manifest_v1,
)


DEFAULT_DEV_IDS = (
    "kiyotah_lucario",
    "sue124_alakazam",
    "skarin_dragapult",
    "ozawa_crustle_v2",
    "nihei_megalopunny",
    "yaroslav_crustleaware_lucario",
)
DEFAULT_FINAL_IDS = (
    "plamen06_steel",
    "lucifer19_battlecore",
    "aristophanivan_multiply",
    "nihei_alakazam",
    "dashimaki360_crustlecounter",
    "ozawa_starmie",
)
DEFAULT_CANDIDATE_ID = "tomatomato_archaludon"
DEFAULT_CENSUS = _ROOT / "docs/evidence/strong-asset-census-20260812.json"
DEFAULT_RANKINGS = (
    _ROOT / "runs/meta-specialist-asset-ranking-primary-fast96-20260812/asset_ranking.json",
    _ROOT / "runs/meta-specialist-asset-ranking-top3-confirm384-20260812/asset_ranking.json",
    _ROOT / "runs/meta-specialist-asset-ranking-top3-confirm384-block2-20260812/asset_ranking.json",
    _ROOT / "runs/meta-specialist-asset-ranking-top3-confirm384-block3-20260812/asset_ranking.json",
    _ROOT / "runs/meta-specialist-asset-ranking-top3-confirm384-block4-20260812/asset_ranking.json",
    _ROOT / "runs/meta-specialist-asset-ranking-r7-diagnostic-20260812/asset_ranking.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_schedule(path: Path, manifest: MetaDistributionManifestV1, *, eval_quota: int, train_quota: int) -> str:
    eval_schedule = build_meta_schedule_v1(
        manifest, split="META_TRAIN", quota=eval_quota, require_training_permission=False
    )
    train_schedule = build_meta_schedule_v1(
        manifest, split="META_TRAIN", quota=train_quota, require_training_permission=True
    )
    payload = {
        "schema_version": "meta-specialist-meta-schedule-v1",
        "manifest_candidate_id": manifest.candidate_id,
        "manifest_rows": len(manifest.rows),
        "training_authority": False,
        "promotion_authority": False,
        "research_only": True,
        "schedules": {
            "META_TRAIN_EVALUATION": [row.__dict__ if hasattr(row, "__dict__") else {
                "opponent_id": row.opponent_id,
                "split": row.split,
                "count": row.count,
                "normalized_weight": row.normalized_weight,
                "training_allowed": row.training_allowed,
            } for row in eval_schedule],
            "META_TRAIN_PERMISSION_FILTERED": [row.__dict__ if hasattr(row, "__dict__") else {
                "opponent_id": row.opponent_id,
                "split": row.split,
                "count": row.count,
                "normalized_weight": row.normalized_weight,
                "training_allowed": row.training_allowed,
            } for row in train_schedule],
        },
        "notes": [
            "META_TRAIN_EVALUATION is a local evaluation curriculum and does not grant teacher permission.",
            "META_TRAIN_PERMISSION_FILTERED is the only schedule allowed for teacher collection.",
            "META_DEV and META_FINAL are not emitted as training schedules.",
        ],
    }
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(payload_text, encoding="utf-8")
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()


def build_and_write_meta_manifest_v1(
    *,
    census_path: Path | str,
    ranking_paths: Sequence[Path | str],
    output_path: Path | str,
    candidate_id: str = DEFAULT_CANDIDATE_ID,
    dev_ids: Sequence[str] = DEFAULT_DEV_IDS,
    final_ids: Sequence[str] = DEFAULT_FINAL_IDS,
    eval_quota: int = 512,
    train_quota: int = 256,
) -> dict[str, str | int]:
    output = Path(output_path)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing manifest: {output}")
    manifest = build_meta_distribution_manifest_v1(
        census_path,
        tuple(ranking_paths),
        candidate_id=candidate_id,
        dev_ids=tuple(dev_ids),
        final_ids=tuple(final_ids),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_sha = save_meta_distribution_manifest_v1(manifest, output)
    schedule_path = output.with_name("meta_schedule.json")
    schedule_sha = _write_schedule(schedule_path, manifest, eval_quota=eval_quota, train_quota=train_quota)
    return {
        "manifest_path": str(output),
        "manifest_sha256": manifest_sha,
        "schedule_path": str(schedule_path),
        "schedule_sha256": schedule_sha,
        "row_count": len(manifest.rows),
        "meta_train_count": len(manifest.split_ids["META_TRAIN"]),
        "meta_dev_count": len(manifest.split_ids["META_DEV"]),
        "meta_final_count": len(manifest.split_ids["META_FINAL"]),
    }


def _parse_ids(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--ranking", type=Path, action="append", dest="rankings")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-id", default=DEFAULT_CANDIDATE_ID)
    parser.add_argument("--dev-ids", default=",".join(DEFAULT_DEV_IDS))
    parser.add_argument("--final-ids", default=",".join(DEFAULT_FINAL_IDS))
    parser.add_argument("--eval-quota", type=int, default=512)
    parser.add_argument("--train-quota", type=int, default=256)
    args = parser.parse_args(argv)
    rankings = tuple(args.rankings) if args.rankings else DEFAULT_RANKINGS
    result = build_and_write_meta_manifest_v1(
        census_path=args.census,
        ranking_paths=rankings,
        output_path=args.output,
        candidate_id=args.candidate_id,
        dev_ids=_parse_ids(args.dev_ids),
        final_ids=_parse_ids(args.final_ids),
        eval_quota=args.eval_quota,
        train_quota=args.train_quota,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

