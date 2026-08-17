"""Evaluate the self-owned root-cg package against the common native pool.

The package is treated as a submission-compatible *candidate* only.  This
research bridge never uses native agents as teachers and never submits.  It
compares the packaged self-owned policy with the existing Rule-v0 control on
the same root deck, opponent, seat, repetition, and seed strata.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    EvaluationGameV1,
    _game_from_payload,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.test_sim import run_match  # noqa: E402


SCHEMA = "meta-specialist-root-cg-candidate-arena-v1"
RUNNER_REF = "scripts.run_root_cg_candidate_arena_v1:run_root_cg_game_v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}
ROOT_DECK = _ROOT / "deck.csv"
DEFAULT_PACKAGE = _ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package"
DEFAULT_POOL = _ROOT / "opponents"
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"

_CANDIDATE_MODULES: dict[str, object] = {}
_RULE_MODULE: object | None = None


class RootCgArenaError(ValueError):
    """Raised when a candidate arena cannot be bound to its artifacts."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RootCgArenaError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_candidate(package_root: Path):
    package_root = Path(package_root).resolve()
    main_path = package_root / "main.py"
    if not main_path.is_file() or not (package_root / "deck.csv").is_file():
        raise RootCgArenaError(f"candidate package is incomplete: {package_root}")
    key = str(package_root)
    module = _CANDIDATE_MODULES.get(key)
    if module is not None:
        return module
    if key not in sys.path:
        sys.path.insert(0, key)
    module_name = "_root_cg_candidate_" + hashlib.sha256(key.encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    if spec is None or spec.loader is None:
        raise RootCgArenaError(f"cannot import candidate main: {main_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "agent", None)):
        raise RootCgArenaError("candidate main has no callable agent")
    _CANDIDATE_MODULES[key] = module
    return module


def _load_rule_module():
    global _RULE_MODULE
    if _RULE_MODULE is not None:
        return _RULE_MODULE
    path = _ROOT / "main.py"
    spec = importlib.util.spec_from_file_location("_root_rule_v0_control_main", path)
    if spec is None or spec.loader is None:
        raise RootCgArenaError("cannot import Rule-v0 control main")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _RULE_MODULE = module
    return module


def _candidate_policy_factory(package_root: Path):
    module = _load_candidate(package_root)

    def factory(_deck: Sequence[int], _seed: int):
        return module.agent

    return factory


def _rule_policy_factory():
    module = _load_rule_module()

    def factory(deck: Sequence[int], seed: int):
        return module.make_rule_agent(deck=deck, seed=seed)

    return factory


def run_root_cg_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Spawn-safe runner for one packaged candidate/control game."""
    game = _game_from_payload(payload)
    metadata = game.metadata
    if metadata.get("schema_version") != SCHEMA or metadata.get("authority") != AUTHORITY_FALSE:
        raise RootCgArenaError("game is not bound to the research-only arena schema")
    if metadata.get("research_only") is not True:
        raise RootCgArenaError("game is not research-only")
    subject_deck = Path(game.subject_deck_path).resolve()
    opponent_deck = Path(game.opponent_deck_path).resolve()
    subject_sha = _sha256(subject_deck)
    opponent_sha = _sha256(opponent_deck)
    if subject_sha != game.deck_sha256 or opponent_sha != game.opponent_deck_sha256:
        raise RootCgArenaError(
            "deck identity changed: "
            f"subject expected={game.deck_sha256} actual={subject_sha}; "
            f"opponent expected={game.opponent_deck_sha256} actual={opponent_sha}"
        )
    if metadata.get("arm_kind") == "root_cg":
        package_root = Path(str(metadata["candidate_package_root"])).resolve()
        if _sha256(package_root / "main.py") != game.policy_sha256:
            raise RootCgArenaError("candidate policy identity changed")
        subject_factory = _candidate_policy_factory(package_root)
    elif metadata.get("arm_kind") == "rule_v0":
        subject_factory = _rule_policy_factory()
    else:
        raise RootCgArenaError("unknown arm_kind")

    pool_root_value = metadata.get("pool_root", str(_ROOT / "opponents"))
    if not isinstance(pool_root_value, str) or not pool_root_value:
        raise RootCgArenaError("game is missing a valid opponent pool root")
    pool_root = Path(pool_root_value).resolve()
    pool_manifest_path = pool_root / "pool_manifest.json"
    expected_pool_sha = metadata.get("pool_manifest_sha256")
    if expected_pool_sha is not None:
        if not isinstance(expected_pool_sha, str) or _sha256(pool_manifest_path) != expected_pool_sha:
            raise RootCgArenaError("opponent pool manifest identity changed")
    pool = load_opponent_pool_v1(pool_root)
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=str(subject_deck))
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=subject_deck if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else subject_deck,
        agent_a_name=game.policy_id if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else game.policy_id,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=f"/tmp/root-cg-arena-worker/{game.game_id}",
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def _read_refs(config_path: Path) -> tuple[str, ...]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    refs = payload.get("opponent_ids") if isinstance(payload, Mapping) else None
    if not isinstance(refs, list) or len(refs) != 24 or len(set(refs)) != 24:
        raise RootCgArenaError("broad config must contain 24 unique opponent_ids")
    return tuple(str(item) for item in refs)


@dataclass(frozen=True, slots=True)
class ArenaArm:
    arm_id: str
    policy_id: str
    policy_sha256: str
    arm_kind: str
    candidate_package_root: Path | None = None


def _arm_subject_deck(arm: ArenaArm) -> Path:
    if arm.arm_kind == "root_cg":
        if arm.candidate_package_root is None:
            raise RootCgArenaError("root_cg arm has no candidate package")
        deck = Path(arm.candidate_package_root) / "deck.csv"
    elif arm.arm_kind == "rule_v0":
        deck = ROOT_DECK
    else:
        raise RootCgArenaError(f"unknown arm_kind for deck binding: {arm.arm_kind}")
    deck = deck.resolve()
    _sha256(deck)
    return deck


def _build_games(
    *,
    arm: ArenaArm,
    refs: Sequence[str],
    pool_root: Path,
    base_seed: int,
    games_per_opponent_seat: int,
    block_id: str,
    max_steps: int = 2_000,
) -> tuple[EvaluationGameV1, ...]:
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for opponent_id in refs:
        subject_deck = _arm_subject_deck(arm)
        opponent = resolve_opponent_v1(load_opponent_pool_v1(pool_root), opponent_id, subject_deck_csv_path=str(subject_deck))
        opponent_deck = Path(opponent.deck_csv_path).resolve()
        opponent_deck_sha = _sha256(opponent_deck)
        opponent_policy_sha = _sha256(Path(opponent.policy_path)) if opponent.policy_path else "0" * 64
        for seat in (0, 1):
            for repetition in range(games_per_opponent_seat):
                seed = base_seed + ordinal
                games.append(
                    EvaluationGameV1(
                        game_id=f"{block_id}-{arm.arm_id}-{opponent_id}-s{seat}-r{repetition:02d}",
                        block_id=block_id,
                        policy_id=arm.policy_id,
                        policy_sha256=arm.policy_sha256,
                        deck_id=f"{arm.arm_id}-subject-deck",
                        deck_sha256=_sha256(subject_deck),
                        opponent_id=opponent_id,
                        opponent_identity={
                            "policy_sha256": opponent_policy_sha,
                            "deck_sha256": opponent_deck_sha,
                            "usage_boundary": opponent.usage_boundary,
                            "source": opponent.source,
                        },
                        opponent_deck_sha256=opponent_deck_sha,
                        seat=seat,
                        seed=seed,
                        max_steps=max_steps,
                        timeout_seconds=600.0,
                        subject_deck_path=str(subject_deck),
                        opponent_deck_path=str(opponent_deck),
                        policy_agent_name=arm.policy_id,
                        opponent_agent_name=opponent_id,
                        runner_ref=RUNNER_REF,
                        metadata={
                            "schema_version": SCHEMA,
                            "research_only": True,
                            "authority": dict(AUTHORITY_FALSE),
                            "pool_root": str(pool_root.resolve()),
                            "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
                            "arm_id": arm.arm_id,
                            "arm_kind": arm.arm_kind,
                            "candidate_package_root": str(arm.candidate_package_root) if arm.candidate_package_root else None,
                            "policy_sha256": arm.policy_sha256,
                            "pair_key": f"{opponent_id}|seat{seat}|rep{repetition}",
                            "repetition": repetition,
                            "usage_boundary": "submission_compatible_local_eval_candidate_only",
                        },
                    )
                )
                ordinal += 1
    return tuple(games)


def _aggregate(rows: Sequence[Mapping[str, object]], *, include_seat: bool = True) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    result: dict[str, object] = {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": (outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)) / requested if requested else None,
    }
    if include_seat:
        result["seat"] = {
            str(seat): _aggregate([row for row in rows if row.get("seat") == seat], include_seat=False)
            for seat in (0, 1)
        }
    return result


def finalize_arena(output_root: Path) -> dict[str, object]:
    """Seal a completed immutable evaluator ledger without rerunning games."""
    output_root = Path(output_root).resolve()
    manifest_path = output_root / "manifest.json"
    ledger_path = output_root / "evaluation/ledger.jsonl"
    if not manifest_path.is_file() or not ledger_path.is_file():
        raise RootCgArenaError("arena root has no immutable manifest/ledger")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != int(manifest.get("requested_games", -1)):
        raise RootCgArenaError("ledger row count does not match requested games")
    arm_ids = [str(item["arm_id"]) for item in manifest.get("arms", [])]
    by_arm = {arm_id: _aggregate([row for row in rows if row.get("metadata", {}).get("arm_id") == arm_id]) for arm_id in arm_ids}
    candidate = by_arm.get("root_cg_self_owned_v1", {})
    control = by_arm.get("rule_v0_control", {})
    candidate_score = float(candidate.get("score_rate") or 0.0)
    control_score = float(control.get("score_rate") or 0.0)
    evaluator_summary = json.loads((output_root / "evaluation/summary.json").read_text(encoding="utf-8"))
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "arms": by_arm,
        "candidate_delta_points": (candidate_score - control_score) * 100.0,
        "requested_games": len(rows),
        "evaluator_summary": evaluator_summary,
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path), "evaluation_output": "evaluation"})
    complete_path = output_root / "manifest-complete.json"
    complete_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "summary": summary, "output_root": str(output_root)}


def run_arena(
    *,
    candidate_package_root: Path,
    output_root: Path,
    refs: Sequence[str],
    base_seed: int = 40110000,
    games_per_opponent_seat: int = 2,
    workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
) -> dict[str, object]:
    candidate_package_root = Path(candidate_package_root).resolve()
    candidate_main = candidate_package_root / "main.py"
    if _sha256(candidate_main) != _sha256(candidate_main):  # explicit regular-file check above
        raise RootCgArenaError("candidate main hash check failed")
    output_root = Path(output_root).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    candidate = ArenaArm(
        arm_id="root_cg_self_owned_v1",
        policy_id="root-cg-self-owned-v1",
        policy_sha256=_sha256(candidate_main),
        arm_kind="root_cg",
        candidate_package_root=candidate_package_root,
    )
    rule_module = _load_rule_module()
    rule_sha = hashlib.sha256(b"".join(((_ROOT / name).read_bytes()) for name in ("main.py", "agents/__init__.py", "agents/rule_agent.py"))).hexdigest()
    control = ArenaArm(
        arm_id="rule_v0_control",
        policy_id="rule-v0-root-control",
        policy_sha256=rule_sha,
        arm_kind="rule_v0",
    )
    del rule_module
    pool_root = DEFAULT_POOL
    games = _build_games(arm=candidate, refs=refs, pool_root=pool_root, base_seed=base_seed, games_per_opponent_seat=games_per_opponent_seat, block_id=f"root-cg-candidate-{base_seed}")
    games += _build_games(arm=control, refs=refs, pool_root=pool_root, base_seed=base_seed, games_per_opponent_seat=games_per_opponent_seat, block_id=f"root-cg-control-{base_seed}")
    output_root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "arms": [candidate.__dict__ if hasattr(candidate, "__dict__") else {"arm_id": candidate.arm_id, "policy_id": candidate.policy_id, "policy_sha256": candidate.policy_sha256, "arm_kind": candidate.arm_kind, "candidate_package_root": str(candidate.candidate_package_root)}, {"arm_id": control.arm_id, "policy_id": control.policy_id, "policy_sha256": control.policy_sha256, "arm_kind": control.arm_kind}],
        "requested_games": len(games),
        "games_per_opponent_seat": games_per_opponent_seat,
        "base_seed": base_seed,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "reference_ids": list(refs),
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evaluation = run_parallel_cabt_evaluation(games, output_dir=output_root / "evaluation", max_workers=workers, worker_recycle_games=worker_recycle_games, overwrite=False)
    rows = evaluation["rows"]
    by_arm = {
        arm_id: _aggregate([row for row in rows if row.get("metadata", {}).get("arm_id") == arm_id])
        for arm_id in (candidate.arm_id, control.arm_id)
    }
    candidate_score = float(by_arm[candidate.arm_id]["score_rate"] or 0.0)
    control_score = float(by_arm[control.arm_id]["score_rate"] or 0.0)
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "arms": by_arm,
        "candidate_delta_points": (candidate_score - control_score) * 100.0,
        "requested_games": len(games),
        "evaluator_summary": evaluation["summary"],
        "authority": dict(AUTHORITY_FALSE),
        "research_only": True,
    }
    (output_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["status"] = "COMPLETE"
    manifest["summary_sha256"] = _sha256(output_root / "summary.json")
    (output_root / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "summary": summary, "output_root": str(output_root)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--base-seed", type=int, default=40110000)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--finalize", action="store_true", help="seal an existing completed ledger without rerunning games")
    args = parser.parse_args(argv)
    try:
        if args.finalize:
            result = finalize_arena(args.output)
        else:
            result = run_arena(candidate_package_root=args.candidate_package, output_root=args.output, refs=_read_refs(args.config), base_seed=args.base_seed, games_per_opponent_seat=args.games_per_opponent_seat, workers=args.workers, worker_recycle_games=args.worker_recycle_games)
    except (RootCgArenaError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
