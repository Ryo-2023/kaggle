"""Validate one fixed CEM candidate on fresh train/dev/final strata."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_p1_cem_v1 import aggregate_candidate_rows  # noqa: E402
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (  # noqa: E402
    BASE_SOURCE_SHA256,
    P1ParameterConfig,
)
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import WeekendSplit, load_weekend_split  # noqa: E402
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_cg_p1_cem_v1 import (  # noqa: E402
    CONTROL_POLICY_ID,
    P1_PACKAGE,
    _resource_gate,
    _sha256,
    _static_smoke,
    build_paired_games,
)
from scripts.run_cg_p1_cem_v1 import candidate_result_from_rows  # noqa: E402


SCHEMA = "cg-p1-p2-validation-v1"
DEFAULT_SPLIT = _ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json"
DEFAULT_BUDGET = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
DEFAULT_POOL_ROOT = _ROOT / "opponents"


def build_validation_games(
    *,
    candidate_package: Path | str,
    candidate_id: str,
    config_sha256: str,
    split: WeekendSplit,
    stage: str,
    base_seed: int,
    pool_root: Path | str = DEFAULT_POOL_ROOT,
) -> tuple:
    stage_spec = {
        "META_TRAIN_384": ("META_TRAIN", 16),
        "META_DEV_96": ("META_DEV", 8),
        "META_FINAL_96": ("META_FINAL", 8),
    }
    if stage not in stage_spec:
        raise ValueError(f"unknown validation stage: {stage}")
    split_name, repetitions = stage_spec[stage]
    return build_paired_games(
        candidate_package=candidate_package,
        candidate_id=candidate_id,
        config_sha256=config_sha256,
        split=split,
        train_block_index=0,
        games_per_opponent_seat=repetitions,
        base_seed=base_seed,
        include_control=True,
        refs_override=split.ids(split_name),
        split_name=split_name,
        pool_root=pool_root,
    )


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run_validation(
    *,
    output_root: Path | str,
    candidate_package: Path | str,
    candidate_id: str,
    config: P1ParameterConfig,
    split_path: Path | str = DEFAULT_SPLIT,
    base_seed: int = 20260815 + 2_000_000,
    pool_root: Path | str = DEFAULT_POOL_ROOT,
) -> dict[str, object]:
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"validation output is not empty: {root}")
    package = Path(candidate_package).resolve()
    staged_pool = Path(pool_root).resolve()
    if not (staged_pool / "pool_manifest.json").is_file():
        raise FileNotFoundError(f"pool manifest missing: {staged_pool / 'pool_manifest.json'}")
    split = load_weekend_split(split_path, verify_sources=True)
    config.validate()
    if _sha256(package / "main.py") == BASE_SOURCE_SHA256:
        raise ValueError("validation candidate must be a parameterized P2 package")
    if _sha256(package / "deck.csv") != split.metadata["bindings"]["p1_deck_sha256"]:
        raise ValueError("candidate deck does not match immutable root deck")
    _static_smoke(package, P1_PACKAGE)
    governor = ResourceGovernor(ResourceBudget.from_json(DEFAULT_BUDGET))
    resource = _resource_gate(governor)
    root.mkdir(parents=True, exist_ok=True)
    _write_new_json(
        root / "manifest.json",
        {
            "schema_version": SCHEMA,
            "candidate_id": candidate_id,
            "candidate_policy_sha256": _sha256(package / "main.py"),
            "candidate_deck_sha256": _sha256(package / "deck.csv"),
            "config": config.as_dict(),
            "config_sha256": config.config_sha256(),
            "parent_policy_sha256": BASE_SOURCE_SHA256,
            "split_sha256": split.config_sha256,
            "pool_root": str(staged_pool),
            "pool_manifest_sha256": _sha256(staged_pool / "pool_manifest.json"),
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
            "resource_decision": resource,
            "training_exposure": 0,
            "research_only": True,
            "candidate_fixed_before_final": True,
            "submission_sent": False,
        },
    )
    results: dict[str, object] = {}
    for index, stage in enumerate(("META_TRAIN_384", "META_DEV_96", "META_FINAL_96")):
        games = build_validation_games(
            candidate_package=package,
            candidate_id=candidate_id,
            config_sha256=config.config_sha256(),
            split=split,
            stage=stage,
            base_seed=base_seed + index * 100_000,
            pool_root=staged_pool,
        )
        evaluation = run_parallel_cabt_evaluation(
            games,
            output_dir=root / stage / "evaluation",
            max_workers=resource["recommended_workers"],
            worker_recycle_games=16,
            overwrite=False,
        )
        stage_name = {
            "META_TRAIN_384": "META_TRAIN",
            "META_DEV_96": "META_DEV",
            "META_FINAL_96": "META_FINAL",
        }[stage]
        weights = split.weights(stage_name)
        result = candidate_result_from_rows(
            evaluation["rows"],
            candidate_policy_id=candidate_id,
            control_policy_id=CONTROL_POLICY_ID,
            weights=weights,
            config=config,
            candidate_id=candidate_id,
        )
        results[stage] = result
        _write_new_json(
            root / stage / "summary.json",
            {"schema_version": SCHEMA, "stage": stage, **result, "research_only": True},
        )
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest.update({"status": "COMPLETE", "stages": list(results)})
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(root), "results": results}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--config-json", type=Path, required=True)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL_ROOT)
    parser.add_argument("--base-seed", type=int, default=20260815 + 2_000_000)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing heavy validation without --execute")
    payload = json.loads(args.config_json.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and "config" in payload:
        payload = payload["config"]
    config = P1ParameterConfig.from_mapping(payload)
    result = run_validation(
        output_root=args.output,
        candidate_package=args.candidate_package,
        candidate_id=args.candidate_id,
        config=config,
        split_path=args.split,
        base_seed=args.base_seed,
        pool_root=args.pool_root,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "results"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
