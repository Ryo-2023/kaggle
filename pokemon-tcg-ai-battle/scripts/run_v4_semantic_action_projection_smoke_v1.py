#!/usr/bin/env python3
"""Capture a public-only semantic-action projection for a closed V4 subject.

This is a research diagnostic.  The V4 checkpoint and Rule/CABT evaluator are
unchanged; a wrapper reconstructs the typed public decision view in memory,
calls the unchanged V4 agent, and persists only semantic public identities and
terminal WDL.  It grants no training, promotion, submission, or long-run
authority.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2  # noqa: E402
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.v4_semantic_action_projection_bridge_v1 import (  # noqa: E402
    aggregate_projection_rows_v1,
    project_v4_decision_v1,
)
from scripts.run_v4_public_trace_meta_train_v1 import (  # noqa: E402
    EXPECTED_POOL_SIZE_V1,
    _load_config,
    _sha256,
    _v4_subject_factory,
    verify_meta_train_pool_entries_v1,
)
from scripts.test_sim import run_match  # noqa: E402


SCHEMA_V1 = "meta-specialist-v4-semantic-action-projection-smoke-v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_bytes(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        Path(temporary).unlink(missing_ok=True)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return digest


def _atomic_json(path: Path, value: Mapping[str, object]) -> str:
    return _atomic_bytes(path, (json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


class _PublicDecisionCapture:
    """In-memory adapter; no raw observation is retained after projection."""

    def __init__(self, inner: Any, decisions: list[dict[str, object]]) -> None:
        self._inner = inner
        self._decisions = decisions

    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        if not isinstance(observation, Mapping) or not isinstance(observation.get("select"), Mapping):
            return self._inner(observation, configuration)
        state = build_actor_visible_decision_state_v2(observation)
        public_trace = state.to_public_trace_payload()
        action = self._inner(observation, configuration)
        if type(action) is not list or any(type(index) is not int for index in action):
            raise ValueError("V4 subject returned a malformed action list")
        self._decisions.append({
            "public_trace": public_trace,
            "chosen_option_indices": tuple(action),
            "selection_type": state.information_view.selection_type,
            "selection_context": state.information_view.selection_context,
            "min_count": state.information_view.min_count,
            "max_count": state.information_view.max_count,
        })
        return action


def _make_capturing_factory(*, base_factory: Any, decisions: list[dict[str, object]]):
    def factory(deck: object, seed: int) -> _PublicDecisionCapture:
        return _PublicDecisionCapture(base_factory(deck, seed), decisions)

    return factory


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=14910000)
    parser.add_argument("--max-steps", type=int, default=2000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.games_per_seat <= 0 or args.max_steps <= 0 or args.base_seed < 0:
        raise SystemExit("invalid games/seed/steps")
    if not args.checkpoint.is_file() or not args.subject_deck_csv.is_file():
        raise SystemExit("checkpoint/deck path is not a regular file")
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"output root must be fresh and empty: {args.output}")
    args.output.mkdir(parents=True, exist_ok=True)
    config, config_sha, opponent_ids = _load_config(args.config)
    if len(opponent_ids) != EXPECTED_POOL_SIZE_V1:
        raise SystemExit("config is not the exact common24 pool")
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    verified = verify_meta_train_pool_entries_v1(opponent_ids, pool)
    subject_deck_sha = _sha256(args.subject_deck_csv)
    provenance = __import__("scripts.measure_v4_checkpoint_strength", fromlist=["_checkpoint_provenance"])._checkpoint_provenance(args.checkpoint)
    base_subject_factory = _v4_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        tensor_state_sha256=provenance["tensor_state_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id="archaludon",
        trace_sinks=None,
    )
    pool_manifest = default_pool_root_v1(_ROOT) / "pool_manifest.json"
    rows: list[dict[str, object]] = []
    projection_rows: list[dict[str, object]] = []
    faults: Counter[str] = Counter()
    projection_faults: Counter[str] = Counter()
    started = time.time()
    requested = len(opponent_ids) * 2 * args.games_per_seat
    completed = 0
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for repetition in range(args.games_per_seat):
                game_id = f"v4-semantic-smoke-{opponent_id}-seat{seat}-g{repetition:04d}"
                seed = args.base_seed + (len(rows) // 2)
                decisions: list[dict[str, object]] = []
                subject_factory = _make_capturing_factory(
                    base_factory=base_subject_factory, decisions=decisions,
                )
                subject_first = seat == 0
                seed_agent_randomness_v1(seed)
                fault: str | None = None
                result: Mapping[str, object] | None = None
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv if subject_first else opponent.deck_csv_path),
                        deck_b_path=str(opponent.deck_csv_path if subject_first else args.subject_deck_csv),
                        agent_a_name="v4_subject" if subject_first else opponent_id,
                        agent_b_name=opponent_id if subject_first else "v4_subject",
                        seed=seed, max_steps=args.max_steps,
                        output_dir=str(args.output / "games" / game_id), save_html=False, save_result=False,
                        agent_a_factory=subject_factory if subject_first else opponent_factory,
                        agent_b_factory=opponent_factory if subject_first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        fault = "non_done"
                except Exception as exc:  # noqa: BLE001 - preserve fault denominator
                    fault = f"{type(exc).__name__}: {exc}"
                if fault is not None:
                    faults[fault] += 1
                    outcome = "fault"
                    winner = None
                else:
                    completed += 1
                    winner = result.get("winner") if result is not None else None
                    outcome = "draw" if winner == 2 else ("win" if winner == seat else "loss")
                    for decision in decisions:
                        try:
                            projection_rows.append(project_v4_decision_v1(
                                public_trace=decision["public_trace"],
                                chosen_option_indices=decision["chosen_option_indices"],
                                game_id=game_id, episode_id=game_id, outcome=outcome,
                                seat=seat, opponent_id=opponent_id, seed=seed,
                                selection_type=decision["selection_type"],
                                selection_context=decision["selection_context"],
                                min_count=decision["min_count"], max_count=decision["max_count"],
                            ))
                        except Exception as exc:  # noqa: BLE001 - preserve sparse public coverage
                            projection_faults[f"{type(exc).__name__}: {exc}"] += 1
                rows.append({
                    "game_id": game_id, "opponent_id": opponent_id, "seat": seat,
                    "repetition": repetition, "seed": seed, "outcome": outcome,
                    "winner": winner, "status": "FAULT" if fault else "DONE",
                    "fault": fault, "policy_sha256": provenance["file_sha256"],
                    "tensor_state_sha256": provenance["tensor_state_sha256"],
                    "deck_sha256": subject_deck_sha,
                })
                if len(rows) % 12 == 0:
                    print(f"[v4-semantic-smoke] games={len(rows)}/{requested} faults={sum(faults.values())}", flush=True)
    ledger_raw = b"".join((_canonical(row) + b"\n") for row in rows)
    projection_raw = b"".join((_canonical(row) + b"\n") for row in projection_rows)
    ledger_sha = _atomic_bytes(args.output / "ledger.jsonl", ledger_raw)
    projection_sha = _atomic_bytes(args.output / "semantic-action-projection.jsonl", projection_raw)
    projection_summary = aggregate_projection_rows_v1(projection_rows) if projection_rows else {
        "schema_version": "meta-specialist-v4-semantic-action-projection-v1",
        "rows": 0, "complete_rows": 0, "distinct_games": 0, "distinct_episodes": 0,
        "distinct_semantic_operations": 0, "chosen_operation_counts": {},
        "reasons": ["no_public_decision_rows"], "usable_signal": False,
        "ready_for_candidate_screen": False, "private_fields_saved": False,
        "native_action_labels_saved": False, "teacher_labels_saved": False,
    }
    if projection_faults:
        # Collision/unrepresentable decisions are intentionally omitted rather
        # than guessed.  Any such omissions make this diagnostic unsuitable
        # for candidate screening, even when the remaining rows are plentiful.
        projection_summary = dict(projection_summary)
        reasons = list(projection_summary.get("reasons", []))
        reasons.append("projection_faults_present")
        projection_summary["reasons"] = sorted(set(reasons))
        projection_summary["usable_signal"] = False
        projection_summary["ready_for_candidate_screen"] = False
        projection_summary.pop("summary_sha256", None)
        projection_summary["summary_sha256"] = hashlib.sha256(
            b"v4-semantic-action-projection-v1\0" + _canonical(projection_summary)
        ).hexdigest()
    summary: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "requested_games": requested,
        "completed_games": completed,
        "faults": requested - completed,
        "fault_reasons": dict(sorted(faults.items())),
        "projection_faults": sum(projection_faults.values()),
        "projection_fault_reasons": dict(sorted(projection_faults.items())),
        "opponent_ids": list(opponent_ids),
        "pool_manifest_sha256": _sha256(pool_manifest),
        "config_sha256": config_sha,
        "checkpoint": provenance,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_sha256": subject_deck_sha,
        "base_seed": args.base_seed,
        "games_per_seat": args.games_per_seat,
        "engine_seed_supported": False,
        "ledger_sha256": ledger_sha,
        "semantic_projection_sha256": projection_sha,
        "semantic_projection_rows": len(projection_rows),
        "projection_summary": projection_summary,
        "training_authority": False, "behavior_authority": False,
        "promotion_authority": False, "submission_authority": False,
        "longrun_allowed": False, "private_fields_saved": False,
        "native_action_labels_saved": False, "teacher_labels_saved": False,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    _atomic_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA_V1", "main"]
