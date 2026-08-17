"""Policy-only CABT holdout runner for a frozen self-owned deck."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

from .cg_alternating_runtime_v1 import (
    AUTHORITY_FALSE_V1,
    CG_DECK_FIXED_LONG_V1,
    CgAlternatingPairV1,
    CgAlternatingRuntimeError,
    CgPackageSpecV1,
    run_cg_alternating_stage_v1,
    validate_cg_pair_v1,
)


SCHEMA = "meta-specialist-cg-policy-holdout-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_sha(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_policy_holdout_refs(config_path: Path | str) -> tuple[str, ...]:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        refs = payload["opponent_ids"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CgAlternatingRuntimeError(f"invalid opponent config: {config_path}") from exc
    if not isinstance(refs, list) or len(refs) != 24 or len(set(refs)) != 24:
        raise CgAlternatingRuntimeError("config must contain exactly 24 unique opponent_ids")
    return tuple(str(item) for item in refs)


def validate_policy_holdout_pair_v1(
    *,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    stage_games: int,
) -> CgAlternatingPairV1:
    """Require equal deck identity and a changed policy identity."""

    if candidate.deck_sha256 != control.deck_sha256:
        raise CgAlternatingRuntimeError("policy holdout requires the same deck")
    return validate_cg_pair_v1(
        phase=CG_DECK_FIXED_LONG_V1,
        candidate=candidate,
        control=control,
        stage_games=stage_games,
    )


def run_policy_holdout_v1(
    *,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    reference_ids: Sequence[str],
    pool_root: Path | str,
    stage_games: int,
    base_seed: int,
    output_root: Path | str,
    execute: bool = False,
    workers: int = 12,
    worker_recycle_games: int | None = None,
) -> dict[str, object]:
    """Execute one bounded policy-only stage without deck or promotion side effects."""

    validate_policy_holdout_pair_v1(candidate=candidate, control=control, stage_games=stage_games)
    refs = tuple(reference_ids)
    if len(refs) != 24 or len(set(refs)) != 24:
        raise CgAlternatingRuntimeError("policy holdout requires exactly 24 unique reference IDs")
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"policy holdout output root exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    stage = run_cg_alternating_stage_v1(
        candidate=candidate,
        control=control,
        phase=CG_DECK_FIXED_LONG_V1,
        reference_ids=refs,
        pool_root=pool_root,
        stage_games=stage_games,
        base_seed=base_seed,
        block_id="cg-policy-holdout",
        output_root=root / "policy-holdout",
        execute=execute,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": stage["status"],
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
        "phase": CG_DECK_FIXED_LONG_V1,
        "stage_games": stage_games,
        "base_seed": base_seed,
        "reference_ids": list(refs),
        "pool_root": str(Path(pool_root).resolve()),
        "candidate": candidate.to_dict(),
        "control": control.to_dict(),
        "stage": stage,
    }
    payload["run_sha256"] = _semantic_sha(payload)
    (root / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


__all__ = ["SCHEMA", "load_policy_holdout_refs", "run_policy_holdout_v1", "validate_policy_holdout_pair_v1"]
