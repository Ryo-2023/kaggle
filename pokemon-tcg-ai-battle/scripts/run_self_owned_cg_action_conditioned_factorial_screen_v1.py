#!/usr/bin/env python3
"""Run a paired TRAIN-only screen for action-conditioned self-owned packages.

Each candidate is compared with a P1 control bound to the same self-owned
deck.  The command is research-only and requires ``--execute`` before any
CABT worker is started.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
import tempfile
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split  # noqa: E402
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    materialize_self_owned_cg_package_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)


SCHEMA = "cg-action-conditioned-factorial-screen-v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}


def _config_from_main(path: Path) -> Mapping[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_CG_ACTION_CONDITIONED_PARAMETERS"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict) and all(type(key) is str and type(item) is int for key, item in value.items()):
                return value
    raise ValueError(f"action-conditioned config missing: {path}")


def _load_self_owned_decks(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for package in (root / "deck-generation").glob("*/package"):
        manifest_path = package / "self_owned_cg_package_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result[str(manifest["canonical_deck_sha256"])] = package.resolve()
    if not result:
        raise ValueError("no self-owned deck-generation packages found")
    return result


def run_screen(
    *,
    source_root: str | Path,
    candidate_root: str | Path | None = None,
    split_path: str | Path,
    split_name: str = "META_TRAIN",
    p1_package: str | Path,
    output_root: str | Path,
    base_seed: int = 2026081625,
    games_per_opponent_seat: int = 2,
    workers: int = 12,
    execute: bool = False,
) -> dict[str, object]:
    """Evaluate all staged action-conditioned candidates against same-deck P1."""

    if not execute:
        raise PermissionError("--execute is required before CABT")
    if type(base_seed) is not int or base_seed < 0:
        raise ValueError("base_seed must be a non-negative integer")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    if split_name not in {"META_TRAIN", "META_DEV", "META_FINAL"}:
        raise ValueError("split_name must be META_TRAIN, META_DEV, or META_FINAL")
    source = Path(source_root).resolve()
    candidate_source = Path(candidate_root or source_root).resolve()
    split = load_weekend_split(Path(split_path).resolve())
    refs = tuple(split.ids(split_name))
    candidates = sorted(candidate_source.glob("*/"))
    if not candidates:
        raise ValueError("candidate_root has no candidate packages")
    for candidate in candidates:
        if not (candidate / "main.py").is_file() or not (candidate / "deck.csv").is_file():
            raise ValueError(f"candidate package is incomplete: {candidate}")
        # The promoted/staged opponent pool intentionally omits native ``cg``
        # runtime files.  A candidate-seat evaluation must use the full package
        # emitted by the generator; otherwise the native library aborts with a
        # misleading ``buffer full`` error before the first scored game.
        if not (candidate / "cg").is_dir():
            raise ValueError(
                f"candidate package lacks cg runtime; use generator packages/ root, not source pool: {candidate}"
            )
    p1 = Path(p1_package).resolve()
    if not (p1 / "main.py").is_file() or not (p1 / "deck.csv").is_file():
        raise ValueError("P1 package is incomplete")
    deck_packages = _load_self_owned_decks(source.parent)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)

    games = []
    records: list[dict[str, object]] = []
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="cg-action-conditioned-factorial-controls-") as temporary:
        temporary_root = Path(temporary)
        for index, candidate in enumerate(candidates):
            manifest = json.loads((candidate / "self_owned_cg_package_manifest.json").read_text(encoding="utf-8"))
            deck_sha = str(manifest["canonical_deck_sha256"])
            deck_package = deck_packages.get(deck_sha)
            if deck_package is None:
                raise ValueError(f"candidate deck has no generation package: {candidate}")
            control = temporary_root / f"control-{index:02d}"
            materialize_self_owned_cg_package_v1(
                source_package=p1,
                candidate_deck=deck_package / "deck.csv",
                output_package=control,
                candidate_id=f"action-conditioned-p1-control-{index:02d}",
            )
            candidate_arm = arena.ArenaArm(
                arm_id=f"ac-candidate-{index:02d}",
                policy_id=candidate.name,
                policy_sha256=arena._sha256(candidate / "main.py"),
                arm_kind="root_cg",
                candidate_package_root=candidate.resolve(),
            )
            control_arm = arena.ArenaArm(
                arm_id=f"ac-control-{index:02d}",
                policy_id=f"p1-control-{index:02d}",
                policy_sha256=arena._sha256(control / "main.py"),
                arm_kind="root_cg",
                candidate_package_root=control.resolve(),
            )
            block = f"cg-action-conditioned-factorial-screen-{index:02d}"
            seed = base_seed + index * 1000
            candidate_games = arena._build_games(
                arm=candidate_arm,
                refs=refs,
                pool_root=source.parent / "promoted",
                base_seed=seed,
                games_per_opponent_seat=games_per_opponent_seat,
                block_id=block,
            )
            control_games = arena._build_games(
                arm=control_arm,
                refs=refs,
                pool_root=source.parent / "promoted",
                base_seed=seed,
                games_per_opponent_seat=games_per_opponent_seat,
                block_id=block,
            )
            if {game.metadata["pair_key"] for game in candidate_games} != {game.metadata["pair_key"] for game in control_games}:
                raise ValueError(f"candidate/control pair strata differ: {candidate.name}")
            games.extend(candidate_games)
            games.extend(control_games)
            records.append({
                "candidate_id": candidate.name,
                "candidate_arm_id": candidate_arm.arm_id,
                "control_arm_id": control_arm.arm_id,
                "candidate_policy_sha256": candidate_arm.policy_sha256,
                "control_policy_sha256": control_arm.policy_sha256,
                "deck_sha256": deck_sha,
                "config": dict(_config_from_main(candidate / "main.py")),
            })

        # Keep the temporary P1 controls alive until all worker processes have
        # finished.  The evaluator only receives package paths, so destroying
        # this directory before the call causes opaque worker failures.
        evaluation = run_parallel_cabt_evaluation(
            tuple(games),
            output_dir=output / "evaluation",
            max_workers=workers,
            worker_recycle_games=16,
            overwrite=False,
        )
        rows = evaluation["rows"]
        for record in records:
            candidate_rows = [row for row in rows if row.get("metadata", {}).get("arm_id") == record["candidate_arm_id"]]
            control_rows = [row for row in rows if row.get("metadata", {}).get("arm_id") == record["control_arm_id"]]
            candidate = arena._aggregate(candidate_rows)
            control = arena._aggregate(control_rows)
            record.update({
                "candidate": candidate,
                "control": control,
                "delta_points": round((float(candidate["score_rate"] or 0.0) - float(control["score_rate"] or 0.0)) * 100.0, 6),
            })
    # ``evaluation`` is assigned inside the temporary-directory scope above;
    # only its serializable result is used below.
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "split": split_name,
        "split_sha256": split.config_sha256,
        "reference_ids": list(refs),
        "games_per_opponent_seat": games_per_opponent_seat,
        "base_seed": base_seed,
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
        "evaluator_summary": evaluation["summary"],
        "candidates": records,
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (output / "screen_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--candidate-root",
        type=Path,
        help="full generator package root containing cg/ runtime; defaults to --source-root",
    )
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--split-name", choices=("META_TRAIN", "META_DEV", "META_FINAL"), default="META_TRAIN")
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, default=2026081625)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        result = run_screen(
            source_root=args.source_root,
            candidate_root=args.candidate_root,
            split_path=args.split,
            split_name=args.split_name,
            p1_package=args.p1_package,
            output_root=args.output,
            base_seed=args.base_seed,
            games_per_opponent_seat=args.games_per_opponent_seat,
            workers=args.workers,
            execute=True,
        )
    except (PermissionError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output), "evaluator_summary": result["evaluator_summary"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
