#!/usr/bin/env python3
"""Screen self-owned public-state packages against the immutable P1 control.

This runner is research-only.  It binds every candidate and the P1 control to
the same fresh ``META_TRAIN`` opponent/seat/seed strata, leaves ``META_DEV``
and ``META_FINAL`` unread, and writes the CABT ledger through the canonical
parallel evaluator.  The explicit file-backed entrypoint is intentional:
``spawn`` workers cannot safely restart a ``<stdin>`` module.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

import scripts.run_cg_p1_cem_v1 as cem  # noqa: E402
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split  # noqa: E402


SCHEMA = "cg-p1-public-state-mix-candidate-screen-v1"
SPLIT_NAME = "META_TRAIN_PUBLIC_CANDIDATE_SCREEN"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "champion_change_allowed": False,
}


class PublicStateMixCandidateScreenError(ValueError):
    """Raised when the candidate screen cannot be hash-bound safely."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise PublicStateMixCandidateScreenError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicStateMixCandidateScreenError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise PublicStateMixCandidateScreenError(f"JSON object required: {path}")
    return value


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _candidate_rows(
    pool_root: Path,
    selected_ids: Sequence[str] | None = None,
) -> tuple[dict[str, object], ...]:
    pool_path = pool_root / "pool_manifest.json"
    raw = json.loads(pool_path.read_text(encoding="utf-8"))
    rows = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list) or not rows:
        raise PublicStateMixCandidateScreenError("pool manifest must contain candidate rows")
    selected = None if selected_ids is None else {str(item) for item in selected_ids}
    if selected is not None and (not selected or any(not item for item in selected)):
        raise PublicStateMixCandidateScreenError("selected candidate IDs must be non-empty")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            raise PublicStateMixCandidateScreenError("pool row must be an object")
        row = dict(raw_row)
        candidate_id = str(row.get("id", ""))
        if not candidate_id or candidate_id in seen:
            raise PublicStateMixCandidateScreenError("candidate IDs must be unique and non-empty")
        if selected is not None and candidate_id not in selected:
            continue
        if row.get("smoke_ok") is not True or row.get("usage_boundary") != "local_eval_only":
            raise PublicStateMixCandidateScreenError(f"candidate is not smoke-qualified: {candidate_id}")
        package = (pool_root / candidate_id).resolve()
        main = package / "main.py"
        manifest = package / "self_owned_cg_package_manifest.json"
        if _sha256(main) != str(row.get("policy_hash")):
            raise PublicStateMixCandidateScreenError(f"candidate policy hash mismatch: {candidate_id}")
        if not manifest.is_file():
            raise PublicStateMixCandidateScreenError(f"candidate manifest missing: {candidate_id}")
        normalized.append(
            {
                "id": candidate_id,
                "package": package,
                "policy_hash": str(row["policy_hash"]),
                "manifest_sha256": _sha256(manifest),
            }
        )
        seen.add(candidate_id)
    if selected is not None and {str(item["id"]) for item in normalized} != selected:
        missing = sorted(selected - {str(item["id"]) for item in normalized})
        raise PublicStateMixCandidateScreenError(f"selected candidate IDs are absent: {missing}")
    return tuple(sorted(normalized, key=lambda item: str(item["id"])))


def build_screen_games(
    *,
    split_path: Path | str,
    pool_root: Path | str,
    control_package: Path | str,
    base_seed: int,
    games_per_opponent_seat: int,
    selected_candidate_ids: Sequence[str] | None = None,
) -> tuple[tuple[cem.EvaluationGameV1, ...], tuple[str, ...], str]:
    """Build paired candidate/control games without starting CABT."""

    if type(base_seed) is not int or base_seed <= 0:
        raise ValueError("base_seed must be a positive integer")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise ValueError("games_per_opponent_seat must be positive")
    pool = Path(pool_root).resolve()
    if not (pool / "pool_manifest.json").is_file():
        raise FileNotFoundError(pool / "pool_manifest.json")
    control = Path(control_package).resolve()
    control_id, _ = cem._control_identity(control)
    split = load_weekend_split(Path(split_path), verify_sources=True)
    refs = split.ids("META_TRAIN")
    rows = _candidate_rows(pool, selected_candidate_ids)
    games: list[cem.EvaluationGameV1] = []
    for index, row in enumerate(rows):
        candidate_id = str(row["id"])
        games.extend(
            cem.build_paired_games(
                candidate_package=Path(row["package"]),
                candidate_id=candidate_id,
                config_sha256=str(row["policy_hash"]),
                split=split,
                train_block_index=0,
                games_per_opponent_seat=games_per_opponent_seat,
                base_seed=base_seed,
                include_control=True,
                refs_override=refs,
                split_name=SPLIT_NAME,
                control_package=control,
                block_id=f"cg-public-state-mix-screen-{index:02d}",
                pool_root=pool,
            )
        )
    expected = len(rows) * len(refs) * 2 * games_per_opponent_seat * 2
    if len(games) != expected:
        raise PublicStateMixCandidateScreenError(
            f"paired game count mismatch: {len(games)} != {expected}"
        )
    if any(game.metadata.get("split") != SPLIT_NAME for game in games):
        raise PublicStateMixCandidateScreenError("screen game split binding mismatch")
    if any(game.metadata.get("training_exposure") != 0 for game in games):
        raise PublicStateMixCandidateScreenError("screen game has nonzero training exposure")
    return tuple(games), tuple(str(row["id"]) for row in rows), control_id


def _score_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    seat_rates: dict[str, float] = {}
    for seat in (0, 1):
        seat_rows = [row for row in rows if row.get("seat") == seat]
        seat_outcomes = Counter(str(row.get("outcome", "fault")) for row in seat_rows)
        seat_rates[str(seat)] = (
            seat_outcomes.get("win", 0) + 0.5 * seat_outcomes.get("draw", 0)
        ) / len(seat_rows) if seat_rows else 0.0
    return {
        "requested_games": requested,
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": (
            outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)
        ) / requested if requested else None,
        "seat_rates": seat_rates,
    }


def _paired_candidate_results(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_ids: Sequence[str],
    control_id: str,
) -> list[dict[str, object]]:
    """Aggregate each candidate with the control from its own paired block."""

    by_block: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        block_id = row.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise PublicStateMixCandidateScreenError("ledger row is missing block_id")
        by_block[block_id].append(row)
    results: list[dict[str, object]] = []
    for candidate_id in candidate_ids:
        matching = [
            block_rows
            for block_rows in by_block.values()
            if any(row.get("policy_id") == candidate_id for row in block_rows)
        ]
        if len(matching) != 1:
            raise PublicStateMixCandidateScreenError(
                f"candidate must have exactly one paired block: {candidate_id}"
            )
        block_rows = matching[0]
        candidate_rows = [row for row in block_rows if row.get("policy_id") == candidate_id]
        control_rows = [row for row in block_rows if row.get("policy_id") == control_id]
        if not candidate_rows or len(candidate_rows) != len(control_rows):
            raise PublicStateMixCandidateScreenError(
                f"candidate/control paired row count mismatch: {candidate_id}"
            )
        candidate = _score_rows(candidate_rows)
        control = _score_rows(control_rows)
        results.append(
            {
                "candidate_id": candidate_id,
                "block_id": str(block_rows[0]["block_id"]),
                "candidate": candidate,
                "control": control,
                "delta_points": round(
                    (float(candidate["score_rate"]) - float(control["score_rate"])) * 100.0,
                    10,
                ),
            }
        )
    return results


def _summary_from_ledger(
    *,
    output: Path,
    candidate_ids: Sequence[str],
    control_id: str,
    suffix: str,
) -> dict[str, object]:
    ledger_path = output / "evaluation" / "ledger.jsonl"
    if not ledger_path.is_file():
        raise PublicStateMixCandidateScreenError(f"ledger missing: {ledger_path}")
    rows = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(not isinstance(row, Mapping) for row in rows):
        raise PublicStateMixCandidateScreenError("ledger rows must be objects")
    evaluator_summary = _read_json(output / "evaluation" / "summary.json")
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_results": _paired_candidate_results(
            rows,
            candidate_ids=candidate_ids,
            control_id=control_id,
        ),
        "control_policy_id": control_id,
        "evaluator_summary": evaluator_summary,
        "research_only": True,
        "dev_final_read_during_search": False,
        "authority": dict(AUTHORITY_FALSE),
        "aggregation": "candidate/control paired within block_id",
    }
    summary_path = output / f"summary{suffix}.json"
    _write_new_json(summary_path, summary)
    manifest = dict(_read_json(output / "manifest.json"))
    manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path)})
    _write_new_json(output / f"manifest-complete{suffix}.json", manifest)
    return summary


def run_screen(
    *,
    output_root: Path | str,
    split_path: Path | str,
    pool_root: Path | str,
    control_package: Path | str,
    base_seed: int,
    games_per_opponent_seat: int = 1,
    workers: int = 12,
    selected_candidate_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """Run and seal one TRAIN-only public-state candidate screen."""

    if type(workers) is not int or workers <= 0:
        raise ValueError("workers must be positive")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    pool = Path(pool_root).resolve()
    control = Path(control_package).resolve()
    split = load_weekend_split(Path(split_path), verify_sources=True)
    games, candidate_ids, control_id = build_screen_games(
        split_path=split.path,
        pool_root=pool,
        control_package=control,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        selected_candidate_ids=selected_candidate_ids,
    )
    output.mkdir(parents=True, exist_ok=False)
    _write_new_json(
        output / "manifest.json",
        {
            "schema_version": SCHEMA,
            "status": "EXECUTING",
            "split_path": str(split.path),
            "split_sha256": split.config_sha256,
            "pool_root": str(pool),
            "pool_manifest_sha256": _sha256(pool / "pool_manifest.json"),
            "control_package": str(control),
            "control_policy_id": control_id,
            "control_policy_sha256": _sha256(control / "main.py"),
            "candidate_ids": list(candidate_ids),
            "reference_ids": list(split.ids("META_TRAIN")),
            "games_per_opponent_seat": games_per_opponent_seat,
            "base_seed": base_seed,
            "requested_games": len(games),
            "workers": workers,
            "research_only": True,
            "dev_final_read_during_search": False,
            "authority": dict(AUTHORITY_FALSE),
        },
    )
    evaluation = cem._evaluate_games(games, output / "evaluation", workers)
    _write_new_json(output / "evaluation_result.json", {"summary": evaluation["summary"]})
    candidate_summary = _paired_candidate_results(
        evaluation["rows"],
        candidate_ids=candidate_ids,
        control_id=control_id,
    )
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "candidate_results": candidate_summary,
        "control_policy_id": control_id,
        "evaluator_summary": evaluation["summary"],
        "research_only": True,
        "dev_final_read_during_search": False,
        "authority": dict(AUTHORITY_FALSE),
        "aggregation": "candidate/control paired within block_id",
    }
    summary_path = output / "summary.json"
    _write_new_json(summary_path, summary)
    manifest = dict(_read_json(output / "manifest.json"))
    manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path)})
    (output / "manifest-complete.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for CABT")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--games-per-opponent-seat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument(
        "--candidate-id",
        action="append",
        dest="candidate_ids",
        help="screen only the selected pool candidate (repeat for multiple IDs)",
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="reconcile an existing completed ledger without rerunning CABT",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        if args.finalize:
            output = args.output.resolve()
            manifest = _read_json(output / "manifest.json")
            candidate_ids = manifest.get("candidate_ids")
            control_id = manifest.get("control_policy_id")
            if not isinstance(candidate_ids, list) or not all(isinstance(item, str) for item in candidate_ids):
                raise PublicStateMixCandidateScreenError("manifest candidate_ids are invalid")
            if not isinstance(control_id, str) or not control_id:
                raise PublicStateMixCandidateScreenError("manifest control_policy_id is invalid")
            summary = _summary_from_ledger(
                output=output,
                candidate_ids=tuple(candidate_ids),
                control_id=control_id,
                suffix="-reconciled",
            )
            print(json.dumps({"status": "COMPLETE", "output_root": str(output), "summary": summary}, ensure_ascii=False, indent=2))
            return 0
        result = run_screen(
            output_root=args.output,
            split_path=args.split,
            pool_root=args.pool_root,
            control_package=args.control_package,
            base_seed=args.base_seed,
            games_per_opponent_seat=args.games_per_opponent_seat,
            workers=args.workers,
            selected_candidate_ids=args.candidate_ids,
        )
    except (PublicStateMixCandidateScreenError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output_root": result["output_root"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
