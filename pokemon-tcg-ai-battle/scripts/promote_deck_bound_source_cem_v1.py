#!/usr/bin/env python3
"""Promote one completed deck-bound source-CEM batch to fresh local meta evidence.

This helper consumes only an existing ``SOURCE_POOL_STAGED`` campaign and its
fault-free selected-source smoke summary.  It does not run CABT, alter
BestKnown/Champion/production, or submit anything to Kaggle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import (  # noqa: E402
    AUTHORITY_FALSE_V1,
    build_fresh_meta_batch_v1,
)
from mage_ptcg.opponent_ingest.self_owned_cg_meta_source_v1 import (  # noqa: E402
    promote_self_owned_cg_meta_batch_v1,
)


PROMOTION_SCHEMA_V1 = "self-owned-cg-deck-bound-source-cem-promotion-v1"


class DeckBoundSourcePromotionError(ValueError):
    """Raised when a staged source-CEM batch cannot be promoted safely."""


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeckBoundSourcePromotionError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise DeckBoundSourcePromotionError(f"JSON object required: {path}")
    return value


def _write_json_new(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise DeckBoundSourcePromotionError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_json(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def build_promotion_smoke_summary_v1(
    campaign_result: Mapping[str, object],
    evaluator_summary: Mapping[str, object],
) -> dict[str, object]:
    """Wrap an evaluator summary in the promotion helper's smoke contract."""

    if not isinstance(campaign_result, Mapping) or campaign_result.get("status") != "SOURCE_POOL_STAGED":
        raise DeckBoundSourcePromotionError("campaign is not staged")
    selected = campaign_result.get("selected_strict_ids")
    batch = campaign_result.get("staged_batch")
    if (
        not isinstance(selected, list)
        or not selected
        or any(type(value) is not str or not value for value in selected)
        or len(selected) != len(set(selected))
        or not isinstance(batch, Mapping)
        or batch.get("status") != "STAGED"
    ):
        raise DeckBoundSourcePromotionError("staged campaign has no distinct selected sources")
    batch_ids = batch.get("source_ids")
    if not isinstance(batch_ids, list) or any(type(value) is not str for value in batch_ids):
        raise DeckBoundSourcePromotionError("staged batch source ids are malformed")
    if set(batch_ids) == set(selected):
        source_ids = list(selected)
    elif set(batch_ids) == {f"self-owned-cg-{value}" for value in selected}:
        source_ids = list(batch_ids)
    else:
        raise DeckBoundSourcePromotionError("selected sources do not match staged batch")
    if not isinstance(evaluator_summary, Mapping):
        raise DeckBoundSourcePromotionError("evaluator summary must be an object")
    requested = evaluator_summary.get("requested_games")
    completed = evaluator_summary.get("completed_games")
    faults = evaluator_summary.get("faults")
    status_distribution = evaluator_summary.get("status_distribution")
    if type(requested) is not int or requested <= 0 or completed != requested:
        raise DeckBoundSourcePromotionError("smoke summary incomplete")
    if type(faults) is not int or faults != 0:
        raise DeckBoundSourcePromotionError("smoke summary has faults")
    if not isinstance(status_distribution, Mapping) or status_distribution.get("DONE") != requested:
        raise DeckBoundSourcePromotionError("smoke summary is not all DONE")
    body: dict[str, object] = {
        "schema_version": f"{PROMOTION_SCHEMA_V1}-smoke",
        "status": "COMPLETE",
        "selected_ids": source_ids,
        "requested_games": requested,
        "completed_rows": completed,
        "faults": 0,
        "evaluator_summary": dict(evaluator_summary),
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    body["manifest_sha256"] = hashlib.sha256(_canonical_json(body)).hexdigest()
    return body


def promote_campaign(
    *,
    campaign_root: str | Path,
    output_root: str | Path,
) -> dict[str, object]:
    """Create a promoted local source pool and verify its fresh-meta manifest."""

    campaign = Path(campaign_root).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise DeckBoundSourcePromotionError(f"output root already exists: {output}")
    campaign_result = _read_json(campaign / "campaign_result.json")
    staged_root = campaign / "staged_source_pool"
    if not staged_root.is_dir():
        raise DeckBoundSourcePromotionError(f"staged source pool is missing: {staged_root}")
    evaluator_summary = _read_json(campaign / "selected-source-smoke" / "summary.json")
    smoke = build_promotion_smoke_summary_v1(campaign_result, evaluator_summary)
    smoke_input = output.parent / f"{output.name}.promotion-smoke-input.json"
    _write_json_new(smoke_input, smoke)
    promoted = promote_self_owned_cg_meta_batch_v1(
        staged_root=staged_root,
        output_root=output,
        smoke_summary=smoke_input,
    )
    fresh_meta = build_fresh_meta_batch_v1(
        manifest_path=output / "fresh_meta.json",
        pool_manifest_path=output / "pool_manifest.json",
    )
    result: dict[str, object] = {
        "schema_version": PROMOTION_SCHEMA_V1,
        "status": "PROMOTED_FRESH_META",
        "campaign_root": str(campaign),
        "staged_root": str(staged_root),
        "output_root": str(output),
        "smoke_input": str(smoke_input),
        "promoted": promoted,
        "fresh_meta": fresh_meta.to_dict(),
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    _write_json_new(output / "promotion_result.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = promote_campaign(campaign_root=args.campaign_root, output_root=args.output)
    except (DeckBoundSourcePromotionError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
