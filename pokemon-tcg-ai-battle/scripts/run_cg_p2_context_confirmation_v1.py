#!/usr/bin/env python3
"""High-precision confirmation for one P2 contextual candidate.

This runner is deliberately fail-closed: a positive result on a reused
META_TRAIN pool is recorded as a diagnostic only.  Promotion requires a
separately bound ``fresh_unused`` meta provenance.
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

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    P2ContextConfig,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import WeekendSplit, load_weekend_split  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_cem_v1 import aggregate_candidate_rows  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1  # noqa: E402
from scripts.run_cg_p2_context_screen_v1 import (  # noqa: E402
    AUTHORITY_FALSE,
    CONTROL_ID,
    DEFAULT_CONTROL,
    _evaluate_games,
    _sha256,
    build_context_paired_games,
)


SCHEMA = "cg-p2-context-confirmation-v1"
DEFAULT_SPLIT = _ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json"
DEFAULT_BASE_SEED = 48386000


def build_confirmation_games(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    config: P2ContextConfig,
    split: WeekendSplit,
    control_package: Path | str = DEFAULT_CONTROL,
    reference_ids: Sequence[str] | None = None,
    base_seed: int = DEFAULT_BASE_SEED,
    repetitions: int = 16,
) -> tuple[object, ...]:
    refs = tuple(reference_ids) if reference_ids is not None else tuple(split.ids("META_TRAIN"))
    games = build_context_paired_games(
        candidate_package=candidate_package,
        candidate_id=candidate_id,
        config_sha256=config.config_sha256(),
        split=split,
        control_package=control_package,
        reference_ids=refs,
        base_seed=base_seed,
        repetitions=repetitions,
    )
    candidate = [game for game in games if game.metadata.get("arm_role") == "candidate"]
    control = [game for game in games if game.metadata.get("arm_role") == "p2_control"]
    if not candidate or len(candidate) != len(control):
        raise ValueError("candidate/control confirmation strata differ")
    if {(game.metadata["pair_key"], game.seed) for game in candidate} != {
        (game.metadata["pair_key"], game.seed) for game in control
    }:
        raise ValueError("candidate/control confirmation seeds differ")
    return games


def summarize_confirmation_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_id: str,
    control_id: str,
    weights: Mapping[str, float],
    config: P2ContextConfig,
    meta_provenance: str,
) -> dict[str, object]:
    if meta_provenance not in {"fresh_unused", "reused_meta_train"}:
        raise ValueError("meta_provenance must be fresh_unused or reused_meta_train")
    candidate_rows = [row for row in rows if row.get("policy_id") == candidate_id]
    control_rows = [row for row in rows if row.get("policy_id") == control_id]
    if not candidate_rows or len(candidate_rows) != len(control_rows):
        raise ValueError("candidate/control confirmation rows are missing or unbalanced")
    candidate = aggregate_candidate_rows(candidate_rows, weights=weights)
    control = aggregate_candidate_rows(control_rows, weights=weights)
    seat_rates = candidate.get("seat_rates", {})
    seat_gap = None
    if isinstance(seat_rates, Mapping) and all(
        isinstance(seat_rates.get(seat), (int, float)) for seat in ("0", "1")
    ):
        seat_gap = abs(float(seat_rates["0"]) - float(seat_rates["1"]))
    faults = int(candidate.get("faults", 0)) + int(control.get("faults", 0))
    delta = float(candidate["objective"]) - float(control["objective"])
    seat_safe = seat_gap is not None and seat_gap <= 0.05
    raw_positive = faults == 0 and seat_safe and delta > 0.0
    if raw_positive and meta_provenance == "fresh_unused":
        decision = "PROMISING_CONFIRMATION"
    elif raw_positive:
        decision = "NOT_PROMOTABLE_REUSED_META"
    else:
        decision = "NOT_PROMOTABLE"
    return {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "candidate_id": candidate_id,
        "control_id": control_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "meta_provenance": meta_provenance,
        "candidate": candidate,
        "control": control,
        "delta_objective": delta,
        "delta_points": delta * 100.0,
        "candidate_seat_gap": seat_gap,
        "candidate_seat_safe": seat_safe,
        "faults": faults,
        "decision": decision,
        "promotion_authority": False,
        "authority": dict(AUTHORITY_FALSE),
    }


def run_confirmation(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    config: P2ContextConfig,
    output_root: Path | str,
    split_path: Path | str = DEFAULT_SPLIT,
    control_package: Path | str = DEFAULT_CONTROL,
    reference_ids: Sequence[str] | None = None,
    base_seed: int = DEFAULT_BASE_SEED,
    repetitions: int = 16,
    workers: int = 12,
    meta_provenance: str = "reused_meta_train",
) -> dict[str, object]:
    if workers != 12:
        raise ValueError("P2 context confirmation is sealed to workers=12")
    output = Path(output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output root is not empty: {output}")
    split = load_weekend_split(split_path, verify_sources=True)
    control = Path(control_package).resolve()
    if _sha256(control / "main.py") != BASE_SOURCE_SHA256:
        raise ValueError("control package is not the immutable P2 parent")
    config.validate()
    games = build_confirmation_games(
        candidate_package=candidate_package,
        candidate_id=candidate_id,
        config=config,
        split=split,
        control_package=control,
        reference_ids=reference_ids,
        base_seed=base_seed,
        repetitions=repetitions,
    )
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "candidate_id": candidate_id,
        "candidate_package": str(Path(candidate_package).resolve()),
        "candidate_policy_sha256": _sha256(Path(candidate_package).resolve() / "main.py"),
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "control_package": str(control),
        "control_policy_sha256": _sha256(control / "main.py"),
        "split_name": "META_TRAIN",
        "split_sha256": split.config_sha256,
        "reference_ids": list(reference_ids) if reference_ids is not None else list(split.ids("META_TRAIN")),
        "base_seed": base_seed,
        "repetitions": repetitions,
        "requested_games": len(games),
        "workers": workers,
        "worker_recycle_games": 16,
        "meta_provenance": meta_provenance,
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    evaluation = _evaluate_games(games, output / "evaluation", workers)
    summary = summarize_confirmation_rows(
        evaluation["rows"],
        candidate_id=candidate_id,
        control_id=CONTROL_ID,
        weights=split.weights("META_TRAIN"),
        config=config,
        meta_provenance=meta_provenance,
    )
    summary["requested_games"] = len(evaluation["rows"])
    summary["evaluator_summary"] = evaluation["summary"]
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "decision": summary["decision"], "summary_sha256": _sha256(summary_path)})
    (output / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary}


def _load_config(path: Path) -> P2ContextConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("config JSON must be an object")
    values = payload.get("config", payload.get("parameters", payload))
    if not isinstance(values, Mapping):
        raise ValueError("config mapping is missing")
    return P2ContextConfig.from_mapping(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--config-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--repetitions", type=int, default=16)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--meta-provenance", choices=("fresh_unused", "reused_meta_train"), default="reused_meta_train")
    args = parser.parse_args(argv)
    result = run_confirmation(
        candidate_package=args.candidate_package,
        candidate_id=args.candidate_id,
        config=_load_config(args.config_json),
        output_root=args.output,
        control_package=args.control_package,
        base_seed=args.base_seed,
        repetitions=args.repetitions,
        workers=args.workers,
        meta_provenance=args.meta_provenance,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

