"""Research-only direct CG policy versus P1 paired diagnostic.

This helper reuses the hash-bound CEM arena without changing the CEM parent or
any submission artifact.  It is intentionally a diagnostic lane: callers must
provide an already sealed split/pool and the result is never a promotion
decision by itself.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import run_cg_p1_cem_v1 as cem
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split


def run_diagnostic(
    *,
    candidate_package: Path,
    control_package: Path,
    split_path: Path,
    pool_root: Path,
    output_root: Path,
    split_name: str = "META_TRAIN",
    games_per_opponent_seat: int = 2,
    base_seed: int = 202608180,
    workers: int = 12,
) -> dict[str, object]:
    if split_name not in {"META_TRAIN", "META_DEV", "META_FINAL"}:
        raise ValueError("split_name must be a valid META split")
    if games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    if base_seed <= 0:
        raise ValueError("base_seed must be positive")
    if workers <= 0:
        raise ValueError("workers must be positive")
    for path in (candidate_package / "main.py", candidate_package / "deck.csv", control_package / "main.py", control_package / "deck.csv"):
        if not path.is_file():
            raise FileNotFoundError(path)
    split = load_weekend_split(split_path, verify_sources=True)
    refs = split.ids(split_name)
    games = cem.build_paired_games(
        candidate_package=candidate_package,
        candidate_id="direct-policy-diagnostic",
        config_sha256="0" * 64,
        split=split,
        train_block_index=0,
        games_per_opponent_seat=games_per_opponent_seat,
        base_seed=base_seed,
        include_control=True,
        refs_override=refs,
        split_name=split_name,
        control_package=control_package,
        block_id=f"direct-policy-v1-{split_name.lower()}-r0",
        pool_root=pool_root,
    )
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    evaluation = cem._evaluate_games(games, output_root / "evaluation", workers=workers)
    rows = evaluation["rows"]
    control_id = cem._control_identity(control_package)[0]
    result = {
        "schema_version": "direct-cg-policy-pair-diagnostic-v1",
        "candidate": cem.candidate_result_from_rows(
            rows,
            candidate_policy_id="direct-policy-diagnostic",
            control_policy_id=control_id,
            weights=split.weights(split_name),
            config=cem.P1ParameterConfig.default(),
            candidate_id="direct-policy-diagnostic",
        ),
        "candidate_package": str(candidate_package.resolve()),
        "candidate_policy_sha256": cem._sha256(candidate_package / "main.py"),
        "control_package": str(control_package.resolve()),
        "control_policy_id": control_id,
        "control_policy_sha256": cem._sha256(control_package / "main.py"),
        "candidate_deck_sha256": cem._sha256(candidate_package / "deck.csv"),
        "control_deck_sha256": cem._sha256(control_package / "deck.csv"),
        "split": split_name,
        "split_sha256": split.config_sha256,
        "pool_root": str(pool_root.resolve()),
        "pool_manifest_sha256": cem._sha256(pool_root / "pool_manifest.json"),
        "base_seed": base_seed,
        "games_per_opponent_seat": games_per_opponent_seat,
        "requested_rows": len(rows),
        "faults": sum(1 for row in rows if row.get("outcome") == "fault"),
        "research_only": True,
        "promotion_authority": False,
    }
    (output_root / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, default=cem.P1_PACKAGE)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split-name", default="META_TRAIN")
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=202608180)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    result = run_diagnostic(
        candidate_package=args.candidate_package.resolve(),
        control_package=args.control_package.resolve(),
        split_path=args.split.resolve(),
        pool_root=args.pool_root.resolve(),
        output_root=args.output,
        split_name=args.split_name,
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
