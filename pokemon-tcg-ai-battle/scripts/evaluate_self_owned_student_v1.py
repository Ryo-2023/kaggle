"""Evaluate a self-owned Student v0 model on the same 24×2×2 pool schedule."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from main import make_rule_agent, make_rule_agent_v1, make_student_agent, read_deck_csv  # noqa: E402
from mage_ptcg.student.hybrid import HybridStudentPolicy  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.collect_self_owned_rule_bc_v1 import build_game_specs_v1, load_opponent_ids  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


SCHEMA_V1 = "student-self-owned-local-evaluation-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_policy_factory(
    policy_kind: str,
    model_path: Path | None,
    *,
    margin_threshold: float = 0.05,
):
    if policy_kind == "rule_v0":
        return lambda deck, seed: make_rule_agent(deck=deck, seed=seed)
    if policy_kind == "rule_v1":
        return lambda deck, seed: make_rule_agent_v1(deck=deck, seed=seed)
    if policy_kind == "student" and model_path is not None:
        return lambda deck, _seed: make_student_agent(deck=deck, model_path=model_path)
    if policy_kind == "hybrid" and model_path is not None:
        def factory(deck, _seed):
            student_agent = make_student_agent(deck=deck, model_path=model_path)
            student_policy = getattr(student_agent, "student_policy", None)
            baseline = make_rule_agent(deck=deck)
            if student_policy is None:
                return baseline
            hybrid = HybridStudentPolicy(
                student=student_policy,
                baseline=baseline,
                margin_threshold=margin_threshold,
            )
            # The CABT adapter expects a plain function object.  Passing a
            # bound method directly is treated as an invalid agent by some
            # kaggle-environments versions even though it is callable.
            def hybrid_agent(observation: dict) -> list[int]:
                return hybrid.choose(observation)

            hybrid_agent.__name__ = "student_v0_rule_v0_hybrid"
            hybrid_agent.hybrid_policy = hybrid  # type: ignore[attr-defined]
            return hybrid_agent
        return factory
    raise ValueError("student policy requires a model path")


def run_one_student_game_v1(spec: Mapping[str, object]) -> dict[str, object]:
    game_id = str(spec["game_id"])
    opponent_id = str(spec["opponent_id"])
    subject_seat = int(spec["subject_seat"])
    seed = int(spec["seed"])
    policy_kind = str(spec["policy_kind"])
    margin_threshold = float(spec.get("margin_threshold", 0.05))
    model_value = spec.get("model_path")
    model_path = Path(str(model_value)).resolve() if model_value else None
    subject_deck_path = str(spec["subject_deck_path"])
    output_root = Path(str(spec["output_root"]))
    max_steps = int(spec["max_steps"])
    episode_dir = output_root / "episodes" / game_id
    episode_dir.mkdir(parents=True, exist_ok=False)
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=subject_deck_path)
    subject_deck = read_deck_csv(subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)

    policy_factory = _build_policy_factory(
        policy_kind, model_path, margin_threshold=margin_threshold
    )

    subject_first = subject_seat == 0
    result = run_match(
        deck_a_path=subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else subject_deck_path,
        agent_a_name=policy_kind if subject_first else opponent_id,
        agent_b_name=opponent_id if subject_first else policy_kind,
        seed=seed,
        max_steps=max_steps,
        output_dir=str(episode_dir / "match"),
        save_html=False,
        save_result=False,
        agent_a_factory=policy_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else policy_factory,
    )
    winner_value = result.get("winner")
    winner = winner_value if type(winner_value) is int and winner_value in (0, 1, 2) else None
    row = {
        "game_id": game_id,
        "opponent_id": opponent_id,
        "subject_seat": subject_seat,
        "seed": seed,
        "status": result.get("status"),
        "winner": winner,
        "elapsed_seconds": result.get("elapsed_seconds"),
    }
    (episode_dir / "result.json").write_text(json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return row


def _summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    done = [row for row in rows if row.get("status") == "DONE"]
    wins = sum(1 for row in done if row.get("winner") == row.get("subject_seat"))
    draws = sum(1 for row in done if row.get("winner") == 2)
    losses = len(done) - wins - draws
    by_seat = {
        str(seat): sum(1 for row in done if row.get("subject_seat") == seat and row.get("winner") == seat)
        for seat in (0, 1)
    }
    return {
        "games": len(rows),
        "done": len(done),
        "faults": len(rows) - len(done),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "win_rate": wins / len(done) if done else 0.0,
        "wins_by_seat": by_seat,
    }


def evaluate_student_v1(
    *,
    model_path: Path | None,
    policy_kind: str = "student",
    output_root: Path,
    subject_deck: Path,
    config_path: Path,
    games_per_seat: int = 2,
    base_seed: int = 20260814,
    workers: int = 12,
    worker_recycle_games: int = 16,
    max_steps: int = 2000,
    margin_threshold: float = 0.05,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    if policy_kind not in {"student", "hybrid", "rule_v0", "rule_v1"}:
        raise ValueError("unsupported policy kind")
    if policy_kind == "student" and model_path is None:
        raise ValueError("student policy requires model_path")
    if type(worker_recycle_games) is not int or worker_recycle_games <= 0:
        raise ValueError("worker_recycle_games must be positive")
    output_root.mkdir(parents=True)
    (output_root / "episodes").mkdir()
    opponent_ids = load_opponent_ids(config_path)
    source_revision = _sha256(Path(__file__).resolve())
    specs = build_game_specs_v1(
        opponent_ids=opponent_ids,
        games_per_seat=games_per_seat,
        base_seed=base_seed,
        subject_deck_path=subject_deck,
        output_root=output_root,
        source_revision=source_revision,
        max_steps=max_steps,
    )
    payloads = [
        {
            "game_id": spec.game_id.replace("self-owned-rule-bc-", f"self-owned-{policy_kind}-"),
            "opponent_id": spec.opponent_id,
            "subject_seat": spec.subject_seat,
            "seed": spec.seed,
            "subject_deck_path": spec.subject_deck_path,
            "output_root": str(output_root.resolve()),
            "model_path": str(model_path.resolve()) if model_path is not None else None,
            "policy_kind": policy_kind,
            "margin_threshold": margin_threshold,
            "max_steps": spec.max_steps,
        }
        for spec in specs
    ]
    rows: list[dict[str, object]] = []
    context = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=context,
        max_tasks_per_child=worker_recycle_games,
    ) as executor:
        futures = [executor.submit(run_one_student_game_v1, payload) for payload in payloads]
        for future in as_completed(futures):
            try:
                rows.append(dict(future.result()))
            except Exception as exc:
                rows.append({"status": "WORKER_ERROR", "error": f"{type(exc).__name__}: {exc}"})
    rows.sort(key=lambda row: str(row.get("game_id", "")))
    summary = _summarize(rows)
    (output_root / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_V1,
        "research_only": True,
        "authority": {"training": False, "behavior": False, "submission": False, "promotion": False},
        "policy_kind": policy_kind,
        "margin_threshold": margin_threshold,
        "model_path": str(model_path.resolve()) if model_path is not None else None,
        "model_sha256": _sha256(model_path) if model_path is not None else None,
        "subject_deck_path": str(subject_deck.resolve()),
        "subject_deck_sha256": _sha256(subject_deck),
        "config_path": str(config_path.resolve()),
        "config_sha256": _sha256(config_path),
        "pool_manifest_sha256": _sha256(ROOT / "opponents" / "pool_manifest.json"),
        "source_revision": source_revision,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "games_requested": len(payloads),
        "summary": summary,
        "status": "DONE" if summary["faults"] == 0 else "FAULTED",
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"manifest": manifest, "summary": summary, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--policy", choices=("student", "hybrid", "rule_v0", "rule_v1"), default="student")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject-deck", type=Path, default=ROOT / "deck.csv")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json")
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=20260814)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--margin-threshold", type=float, default=0.05)
    args = parser.parse_args(argv)
    try:
        result = evaluate_student_v1(
            model_path=args.model,
            policy_kind=args.policy,
            output_root=args.output,
            subject_deck=args.subject_deck,
            config_path=args.config,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            max_steps=args.max_steps,
            margin_threshold=args.margin_threshold,
        )
    except (OSError, ValueError) as exc:
        print(f"Student evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["evaluate_student_v1", "run_one_student_game_v1"]
