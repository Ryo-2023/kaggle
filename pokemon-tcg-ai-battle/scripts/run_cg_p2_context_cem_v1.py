#!/usr/bin/env python3
"""Run a research-only CEM loop on the bounded P2 context surface.

Each generation delegates one paired CABT screen to the existing P2 screen
runner.  Only fault-free, seat-safe, positive candidate-vs-control deltas can
update the center; otherwise the current center and scales are held.  The
campaign never changes Champion, submission, or training authority and does
not claim a fresh/unused meta when the local pool cannot provide one.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p2_context_cem_v1 import (  # noqa: E402
    CemState,
    rank_robust_results,
    rank_valid_results,
    sample_population,
    save_checkpoint,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    P2ContextConfig,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split  # noqa: E402
from scripts.run_cg_p2_context_screen_v1 import (  # noqa: E402
    DEFAULT_CONTROL,
    DEFAULT_SPLIT,
    run_screen,
)


SCHEMA = "cg-p2-context-cem-campaign-v1"
AUTHORITY_FALSE = {
    "training": False,
    "promotion": False,
    "submission": False,
    "longrun": False,
    "teacher": False,
}
FRESH_META_BLOCKED = "BLOCKED_NO_LOCAL_UNUSED_META"


@dataclass(frozen=True, slots=True)
class ContextCemCampaignConfig:
    population_size: int = 8
    elite_count: int = 2
    generations: int = 2
    repetitions: int = 2
    workers: int = 12
    seed: int = 20260815
    base_seed: int = 48526000
    initial_scale: float = 15_000.0
    independent_blocks: int = 0
    independent_repetitions: int = 2
    independent_candidate_count: int = 0

    def validate(self) -> None:
        for name in ("population_size", "elite_count", "generations", "repetitions", "workers", "seed", "base_seed"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.elite_count > self.population_size:
            raise ValueError("elite_count cannot exceed population_size")
        if self.workers != 12:
            raise ValueError("P2 context CEM requires workers=12")
        if isinstance(self.initial_scale, bool) or type(self.initial_scale) not in (int, float) or self.initial_scale <= 0:
            raise ValueError("initial_scale must be positive")
        max_span = max(upper - lower for lower, upper in PARAMETER_BOUNDS.values())
        if self.initial_scale > max_span:
            raise ValueError("initial_scale exceeds the context surface span")
        if type(self.independent_blocks) is not int or self.independent_blocks < 0:
            raise ValueError("independent_blocks must be a non-negative integer")
        if type(self.independent_repetitions) is not int or self.independent_repetitions <= 0:
            raise ValueError("independent_repetitions must be a positive integer")
        if type(self.independent_candidate_count) is not int or self.independent_candidate_count < 0:
            raise ValueError("independent_candidate_count must be a non-negative integer")
        if self.independent_blocks > 0:
            target_count = self.independent_candidate_count or self.elite_count
            if target_count < self.elite_count:
                raise ValueError("independent_candidate_count cannot be below elite_count")


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _default_scales(initial_scale: float) -> dict[str, float]:
    return {name: float(initial_scale) for name in PARAMETER_BOUNDS}


def _load_center(path: Path | str | None) -> P2ContextConfig:
    if path is None:
        return P2ContextConfig.default()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("initial center payload must be a mapping")
    values = payload.get("config", payload.get("parameters", payload))
    if not isinstance(values, Mapping):
        raise ValueError("initial center must contain a parameter mapping")
    return P2ContextConfig.from_mapping(values)


def _elite_record(elite: Mapping[str, object]) -> dict[str, object]:
    config = elite["config"]
    if not isinstance(config, P2ContextConfig):
        config = P2ContextConfig.from_mapping(config)
    record = {key: value for key, value in elite.items() if key != "config"}
    record["config"] = config.as_dict()
    return record


def _config_sha256_from_result(result: Mapping[str, object]) -> str | None:
    config = result.get("config")
    try:
        normalized = config if isinstance(config, P2ContextConfig) else P2ContextConfig.from_mapping(config)
    except (TypeError, ValueError):
        normalized = None
    return normalized.config_sha256() if normalized is not None else None


def combine_independent_results(
    screen_results: Sequence[Mapping[str, object]],
    independent_summaries: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Attach independent block summaries to their screen candidates.

    Matching is by the immutable context configuration hash, never by the
    block-local candidate id.  This keeps the robust gate stable when each
    block materializes its own candidate package and id.
    """

    by_config: dict[str, list[dict[str, object]]] = {}
    for summary in independent_summaries:
        if not isinstance(summary, Mapping):
            continue
        rows = summary.get("results")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            config_sha = _config_sha256_from_result(row)
            if config_sha is not None:
                by_config.setdefault(config_sha, []).append(dict(row))
    combined: list[dict[str, object]] = []
    for result in screen_results:
        if not isinstance(result, Mapping):
            continue
        config_sha = _config_sha256_from_result(result)
        if config_sha is None:
            continue
        row = dict(result)
        row["independent_blocks"] = by_config.get(config_sha, [])
        row["independent_block_count"] = len(row["independent_blocks"])
        combined.append(row)
    return tuple(combined)


def update_after_generation(
    center: P2ContextConfig,
    scales: Mapping[str, float],
    results: Sequence[Mapping[str, object]],
    *,
    elite_count: int,
    robust_gate: bool = False,
) -> tuple[P2ContextConfig, dict[str, float], tuple[dict[str, object], ...], str]:
    """Apply the positive, safe elite gate or hold the current distribution."""

    center.validate()
    normalized_scales = {str(name): float(value) for name, value in scales.items()}
    if set(normalized_scales) != set(center.as_dict()):
        raise ValueError("CEM scales do not match parameter surface")
    if any(not math.isfinite(value) or value < 0 for value in normalized_scales.values()):
        raise ValueError("CEM scales must be finite and non-negative")
    if type(robust_gate) is not bool:
        raise ValueError("robust_gate must be boolean")
    try:
        if robust_gate:
            elites = rank_robust_results(
                results,
                elite_count=elite_count,
                min_independent_blocks=2,
            )
        else:
            elites = rank_valid_results(results, elite_count=elite_count, positive_delta_gate=True)
    except ValueError:
        status = (
            "CENTER_HELD_NOT_ENOUGH_ROBUST_POSITIVE_ELITES"
            if robust_gate
            else "CENTER_HELD_NOT_ENOUGH_POSITIVE_ELITES"
        )
        return center, normalized_scales, (), status
    next_center, next_scales = update_distribution(center, elites)
    status = "UPDATED_FROM_ROBUST_POSITIVE_ELITES" if robust_gate else "UPDATED_FROM_POSITIVE_ELITES"
    return next_center, next_scales, tuple(_elite_record(item) for item in elites), status


def run_campaign(
    *,
    output_root: Path | str,
    config: ContextCemCampaignConfig | None = None,
    initial_center: P2ContextConfig | None = None,
    split_path: Path | str = DEFAULT_SPLIT,
    control_package: Path | str = DEFAULT_CONTROL,
) -> dict[str, object]:
    """Run generations sequentially and publish file-backed checkpoints."""

    campaign_config = config or ContextCemCampaignConfig()
    campaign_config.validate()
    center = initial_center or P2ContextConfig.default()
    center.validate()
    initial_center_values = center.as_dict()
    output = Path(output_root).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"output root is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    split = load_weekend_split(split_path, verify_sources=True)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "research_only": True,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "split_sha256": split.config_sha256,
        "split_name": "META_TRAIN",
        "meta_train_reference_count": len(split.ids("META_TRAIN")),
        "control_package": str(Path(control_package).resolve()),
        "campaign_config": asdict(campaign_config),
        "initial_center": initial_center_values,
        "fresh_unused_meta_confirmation": FRESH_META_BLOCKED,
        "promotion_authority": False,
        "authority": dict(AUTHORITY_FALSE),
    }
    _write_new_json(output / "manifest.json", manifest)

    scales = _default_scales(campaign_config.initial_scale)
    generations: list[dict[str, object]] = []
    for generation in range(campaign_config.generations):
        population = sample_population(
            center,
            generation=generation,
            population_size=campaign_config.population_size,
            seed=campaign_config.seed,
            scales=scales,
        )
        generation_root = output / f"generation-{generation:02d}"
        screen = run_screen(
            output_root=generation_root,
            split_path=split_path,
            control_package=control_package,
            configs=population,
            candidate_generation=generation,
            base_seed=campaign_config.base_seed + generation * 100_003,
            repetitions=campaign_config.repetitions,
            workers=campaign_config.workers,
        )
        summary = screen.get("summary")
        if not isinstance(summary, Mapping):
            raise ValueError("P2 screen returned no summary")
        results = summary.get("results")
        if not isinstance(results, list):
            raise ValueError("P2 screen returned no result rows")
        independent_summaries: list[Mapping[str, object]] = []
        robust_results: Sequence[Mapping[str, object]] = results
        if campaign_config.independent_blocks > 0:
            try:
                initial_targets = rank_valid_results(
                    results,
                    elite_count=(
                        campaign_config.independent_candidate_count
                        or campaign_config.elite_count
                    ),
                    positive_delta_gate=True,
                )
            except ValueError:
                initial_targets = ()
            if initial_targets:
                target_configs = tuple(
                    item["config"]
                    if isinstance(item.get("config"), P2ContextConfig)
                    else P2ContextConfig.from_mapping(item.get("config"))
                    for item in initial_targets
                )
                for block_index in range(campaign_config.independent_blocks):
                    block_root = generation_root / f"independent-{block_index:02d}"
                    block_output = run_screen(
                        output_root=block_root,
                        split_path=split_path,
                        control_package=control_package,
                        configs=target_configs,
                        candidate_generation=generation * 1_000 + block_index + 1,
                        base_seed=(
                            campaign_config.base_seed
                            + generation * 100_003
                            + (block_index + 1) * 1_000_003
                        ),
                        repetitions=campaign_config.independent_repetitions,
                        workers=campaign_config.workers,
                    )
                    block_summary = block_output.get("summary")
                    if not isinstance(block_summary, Mapping):
                        raise ValueError("independent P2 screen returned no summary")
                    independent_summaries.append(block_summary)
                robust_results = combine_independent_results(results, independent_summaries)
        next_center, next_scales, elites, update_status = update_after_generation(
            center,
            scales,
            robust_results,
            elite_count=campaign_config.elite_count,
            robust_gate=campaign_config.independent_blocks > 0,
        )
        generation_record = {
            "generation": generation,
            "population": [item.as_dict() for item in population],
            "results": results,
            "robust_results": list(robust_results),
            "independent_blocks": [dict(item) for item in independent_summaries],
            "elites": list(elites),
            "center_before": center.as_dict(),
            "scales_before": dict(scales),
            "center_after": next_center.as_dict(),
            "scales_after": dict(next_scales),
            "update_status": update_status,
            "screen_root": str(generation_root),
        }
        generations.append(generation_record)
        center, scales = next_center, next_scales
        save_checkpoint(
            output,
            CemState(
                generation=generation,
                center=center,
                scales=dict(scales),
                next_candidate_index=(generation + 1) * campaign_config.population_size,
                evaluated=list(generations),
                campaign_identity={
                    "schema_version": SCHEMA,
                    "parent_policy_sha256": BASE_SOURCE_SHA256,
                    "split_sha256": split.config_sha256,
                    "fresh_unused_meta_confirmation": FRESH_META_BLOCKED,
                },
            ),
        )

    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "initial_center": initial_center_values,
        "final_center": center.as_dict(),
        "final_scales": dict(scales),
        "generations": generations,
        "fresh_unused_meta_confirmation": FRESH_META_BLOCKED,
        "promotion_authority": False,
        "authority": dict(AUTHORITY_FALSE),
    }
    summary_path = output / "summary.json"
    _write_new_json(summary_path, summary)
    complete_manifest = dict(manifest)
    complete_manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path)})
    _write_new_json(output / "manifest-complete.json", complete_manifest)
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--center", type=Path, default=None, help="JSON file containing a P2 context config")
    parser.add_argument("--population-size", type=int, default=8)
    parser.add_argument("--elite-count", type=int, default=2)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--base-seed", type=int, default=48526000)
    parser.add_argument("--initial-scale", type=float, default=15_000.0)
    parser.add_argument(
        "--independent-blocks",
        type=int,
        default=0,
        help="number of independent re-evaluation blocks for robust elite gating",
    )
    parser.add_argument(
        "--independent-repetitions",
        type=int,
        default=2,
        help="repetitions per opponent/seat in each independent block",
    )
    parser.add_argument(
        "--independent-candidate-count",
        type=int,
        default=0,
        help="screen-positive candidates to re-evaluate (0 means elite-count)",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--control-package", type=Path, default=DEFAULT_CONTROL)
    parser.add_argument("--execute", action="store_true", help="allow the heavy CABT campaign to start")
    args = parser.parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing heavy P2 CEM run without --execute")
    config = ContextCemCampaignConfig(
        population_size=args.population_size,
        elite_count=args.elite_count,
        generations=args.generations,
        repetitions=args.repetitions,
        workers=args.workers,
        seed=args.seed,
        base_seed=args.base_seed,
        initial_scale=args.initial_scale,
        independent_blocks=args.independent_blocks,
        independent_repetitions=args.independent_repetitions,
        independent_candidate_count=args.independent_candidate_count,
    )
    result = run_campaign(
        output_root=args.output,
        config=config,
        initial_center=_load_center(args.center),
        split_path=args.split,
        control_package=args.control_package,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
