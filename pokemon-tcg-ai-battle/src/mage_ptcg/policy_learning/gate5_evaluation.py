"""Gate 5a fixed-schedule checkpoint selection and holdout evaluation."""
from __future__ import annotations

from collections import defaultdict
import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


class FinalEvaluationError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]


def _identity(entry: dict[str, Any]) -> str | None:
    for key in ("runtime_fingerprint", "agent_digest"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def wilson_interval(wins: int, games: int) -> list[float]:
    if games <= 0 or wins < 0 or wins > games:
        raise FinalEvaluationError("win count is invalid")
    z = 1.959963984540054
    p = wins / games
    denominator = 1 + z * z / games
    center = (p + z * z / (2 * games)) / denominator
    radius = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denominator
    return [center - radius, center + radius]


def difference_interval(first_wins: int, first_games: int, second_wins: int, second_games: int) -> tuple[float, list[float]]:
    if first_games <= 0 or second_games <= 0:
        raise FinalEvaluationError("difference needs non-empty samples")
    first, second = first_wins / first_games, second_wins / second_games
    delta = first - second
    standard_error = math.sqrt(first * (1 - first) / first_games + second * (1 - second) / second_games)
    radius = 1.959963984540054 * standard_error
    return delta, [delta - radius, delta + radius]


def select_holdout_opponents(*, population_path: Path, source_run: Path, output: Path) -> dict[str, Any]:
    population = _read_json(population_path)
    source = _read_json(source_run / "schedule.json")
    entries = {
        str(entry.get("opponent_id")): entry
        for entry in population.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("availability_status") == "AVAILABLE"
        and entry.get("validation_status") == "VALIDATED"
    }
    known_ids = {str(value) for value in source.get("opponents", [])}
    if not known_ids or not known_ids.issubset(entries):
        raise FinalEvaluationError("training opponent identities are absent from the population")
    known_policy_hashes = {_identity(entries[opponent]) for opponent in known_ids}
    known_policy_hashes.discard(None)
    known_deck_hashes = {
        str(entries[opponent].get("deck_fingerprint"))
        for opponent in known_ids
        if entries[opponent].get("deck_fingerprint")
    }
    unknown_candidates = [
        entry
        for opponent, entry in sorted(entries.items())
        if opponent not in known_ids
        and entry.get("opponent_type") in {"FAMILY_SPECIFIC", "STUDENT_AGENT", "TEAM_NATIVE"}
        and _identity(entry) not in known_policy_hashes
    ]
    approved_loaders = {"family_specific_external_v1", "team_native_subprocess_v1"}
    unknown = [entry for entry in unknown_candidates if entry.get("loader") in approved_loaders]
    excluded = [
        {
            "opponent_id": entry["opponent_id"],
            "opponent_type": entry.get("opponent_type"),
            "opponent_policy_hash": _identity(entry),
            "reason": f"OPPONENT_LOADER_NOT_APPROVED:{entry.get('loader', 'UNRECORDED')}",
        }
        for entry in unknown_candidates
        if entry.get("loader") not in approved_loaders
    ]
    # Fill the remaining strata with unseen Rule decks so the holdout is
    # exactly 1,024 games at 128 per stratum.  Excluded policies remain
    # explicit evidence; they are never silently reclassified as executable.
    deck_candidates = [
        entry
        for opponent, entry in sorted(entries.items())
        if opponent not in known_ids
        and opponent != "rule-v0-current-deck"
        and entry.get("opponent_type") == "RULE_V0_DECK"
    ]
    deck_holdouts = [
        entry
        for entry in deck_candidates
        if entry.get("deck_fingerprint")
        and str(entry["deck_fingerprint"]) not in known_deck_hashes
    ]
    excluded.extend(
        {
            "opponent_id": entry["opponent_id"],
            "opponent_type": entry.get("opponent_type"),
            "opponent_policy_hash": _identity(entry),
            "deck_fingerprint": entry.get("deck_fingerprint"),
            "reason": (
                "DECK_FINGERPRINT_MISSING"
                if not entry.get("deck_fingerprint")
                else "DECK_FINGERPRINT_SEEN_IN_TRAINING"
            ),
        }
        for entry in deck_candidates
        if entry not in deck_holdouts
    )
    deck_count = 8 - len(unknown)
    if len(unknown) < 1 or deck_count < 1 or len(deck_holdouts) < deck_count:
        raise FinalEvaluationError("the population cannot form policy/deck holdouts")
    selected = [*unknown, *deck_holdouts[:deck_count]]
    if len(selected) != 8:
        raise FinalEvaluationError(f"expected exactly 8 holdout strata, found {len(selected)}")
    rows = []
    for entry in selected:
        identity = _identity(entry)
        rows.append(
            {
                "opponent_id": entry["opponent_id"],
                "opponent_type": entry["opponent_type"],
                "deck_fingerprint": entry.get("deck_fingerprint"),
                "opponent_policy_hash": identity,
                "unknown_policy_hash": identity not in known_policy_hashes,
                "unknown_deck_hash": bool(
                    entry.get("deck_fingerprint")
                    and str(entry["deck_fingerprint"]) not in known_deck_hashes
                ),
            }
        )
    result = {
        "schema": "policy-learning-gate5a-holdout-population-v1",
        "source_population_digest": population.get("semantic_population_digest"),
        "training_opponents": sorted(known_ids),
        "training_policy_hashes": sorted(known_policy_hashes),
        "training_deck_hashes": sorted(known_deck_hashes),
        "opponents": rows,
        "excluded_opponents": excluded,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _pairing_digest(games: Iterable[dict[str, Any]]) -> str:
    keys = sorted(
        (
            str(game.get("opponent")),
            int(game.get("candidate_side", -1)),
            int(game.get("repetition", -1)),
            int(game.get("seed", -1)),
        )
        for game in games
    )
    return hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()


def _summarize_run(run: Path, *, expected_games: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary = _read_json(run / "run_summary.json")
    games = _rows(run / "game_results.jsonl")
    bad_faults = {key: value for key, value in summary.get("fault_counts", {}).items() if key != "COMPLETED" and value}
    clean = (
        summary.get("gate") == "PASS"
        and summary.get("completed") == expected_games
        and summary.get("legal_games") == expected_games
        and summary.get("candidate_faults") == 0
        and summary.get("mapping_failures") == 0
        and summary.get("score_identity_failures") == 0
        and not bad_faults
        and len(games) == expected_games
        and all(
            game.get("status") == "DONE"
            and game.get("legal") is True
            and game.get("candidate_fault") is False
            for game in games
        )
    )
    if not clean:
        raise FinalEvaluationError(f"CABT run is not clean: {run}")
    wins = sum(game.get("winner") == game.get("candidate_side") for game in games)
    return (
        {
            "games": len(games),
            "wins": wins,
            "win_rate": wins / len(games),
            "wilson_95": wilson_interval(wins, len(games)),
            "pairing_digest": _pairing_digest(games),
            "wall_clock_games_per_second": summary.get("wall_clock_games_per_second"),
            "safety_gate": "PASS",
        },
        games,
    )


def _group_result(games: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(games)
    wins = sum(game.get("winner") == game.get("candidate_side") for game in values)
    return {"games": len(values), "wins": wins, "win_rate": wins / len(values) if values else None}


def summarize_primary(
    *, output_root: Path, candidate_manifest: Path, expected_games: int, base_seed: int, workers: int, output: Path
) -> dict[str, Any]:
    manifest = _read_json(candidate_manifest)
    results = []
    pairing: str | None = None
    for candidate in manifest["candidates"]:
        aggregate, games = _summarize_run(output_root / "evaluations" / candidate["label"], expected_games=expected_games)
        if pairing is None:
            pairing = aggregate["pairing_digest"]
        elif pairing != aggregate["pairing_digest"]:
            raise FinalEvaluationError("primary candidates do not use the same schedule pairing")
        by_side = {str(side): _group_result(game for game in games if game["candidate_side"] == side) for side in (0, 1)}
        results.append({**candidate, **aggregate, "by_side": by_side})
    bc = next((row for row in results if row["role"] == "bc"), None)
    eligible = [row for row in results if row["role"] != "bc"]
    if bc is None or not eligible:
        raise FinalEvaluationError("primary evaluation needs BC and at least one PPO checkpoint")
    best = max(eligible, key=lambda row: (row["win_rate"], -int(row.get("decisions", 0)), row["label"]))
    delta, interval = difference_interval(best["wins"], best["games"], bc["wins"], bc["games"])
    for result in results:
        result["minus_bc"], result["minus_bc_95"] = difference_interval(
            result["wins"], result["games"], bc["wins"], bc["games"]
        )
    primary_side_both = all(best["by_side"][str(side)]["win_rate"] > bc["by_side"][str(side)]["win_rate"] for side in (0, 1))
    payload = {
        "schema": "policy-learning-gate5a-primary-checkpoint-selection-v1",
        "pairing": {
            "opponent": "rule-v0-current-deck",
            "games_per_candidate": expected_games,
            "balanced_sides": True,
            "base_seed": base_seed,
            "workers": workers,
            "engine_seed_supported": False,
            "pairing_digest": pairing,
            "comparison_kind": "matched-schedule-unpaired-inference",
        },
        "results": results,
        "selected": best["label"],
        "selected_model": best["model_dir"],
        "selected_minus_bc": delta,
        "selected_minus_bc_95": interval,
        "bc_improvement": "CONFIRMED" if interval[0] > 0 else "NOT_CONFIRMED",
        "rule_v0_point_target": "MET" if best["win_rate"] >= 0.5 else "NOT_MET",
        "rule_v0_confirmed_target": "CONFIRMED" if best["wilson_95"][0] > 0.5 else "NOT_CONFIRMED",
        "primary_side_both_improved": primary_side_both,
        "promotion": "NO_DECISION",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def summarize_holdout(
    *,
    output_root: Path,
    primary_summary_path: Path,
    holdout_manifest_path: Path,
    expected_games: int,
    output: Path,
) -> dict[str, Any]:
    primary, holdout = _read_json(primary_summary_path), _read_json(holdout_manifest_path)
    bc_aggregate, bc_games = _summarize_run(output_root / "evaluations" / "holdout-bc", expected_games=expected_games)
    selected_aggregate, selected_games = _summarize_run(
        output_root / "evaluations" / "holdout-selected", expected_games=expected_games
    )
    if bc_aggregate["pairing_digest"] != selected_aggregate["pairing_digest"]:
        raise FinalEvaluationError("holdout candidates do not use the same schedule pairing")
    opponent_meta = {row["opponent_id"]: row for row in holdout["opponents"]}

    def groups(games: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for game in games:
            grouped[str(game[key])].append(game)
        return {name: _group_result(values) for name, values in sorted(grouped.items())}

    bc_by_side, selected_by_side = groups(bc_games, "candidate_side"), groups(selected_games, "candidate_side")
    bc_by_opponent, selected_by_opponent = groups(bc_games, "opponent"), groups(selected_games, "opponent")
    opponent_results = []
    for opponent in sorted(bc_by_opponent):
        bc_value, selected_value = bc_by_opponent[opponent], selected_by_opponent[opponent]
        opponent_results.append(
            {
                **opponent_meta[opponent],
                "bc": bc_value,
                "selected": selected_value,
                "delta": selected_value["win_rate"] - bc_value["win_rate"],
            }
        )
    quartile_size = max(1, math.ceil(len(opponent_results) * 0.25))
    hardest = sorted(opponent_results, key=lambda row: (row["bc"]["win_rate"], row["opponent_id"]))[:quartile_size]
    hardest_ids = {row["opponent_id"] for row in hardest}
    bc_worst = _group_result(game for game in bc_games if game["opponent"] in hardest_ids)
    selected_worst = _group_result(game for game in selected_games if game["opponent"] in hardest_ids)
    unknown_ids = {row["opponent_id"] for row in holdout["opponents"] if row["unknown_policy_hash"]}
    bc_unknown = _group_result(game for game in bc_games if game["opponent"] in unknown_ids)
    selected_unknown = _group_result(game for game in selected_games if game["opponent"] in unknown_ids)
    unknown_delta, unknown_delta_95 = difference_interval(
        selected_unknown["wins"], selected_unknown["games"], bc_unknown["wins"], bc_unknown["games"]
    )
    policy_hash_by_opponent = {
        row["opponent_id"]: row["opponent_policy_hash"]
        for row in holdout["opponents"]
        if row["unknown_policy_hash"]
    }
    policy_hash_results = []
    for policy_hash in sorted(set(policy_hash_by_opponent.values())):
        opponents = {key for key, value in policy_hash_by_opponent.items() if value == policy_hash}
        bc_hash = _group_result(game for game in bc_games if game["opponent"] in opponents)
        selected_hash = _group_result(game for game in selected_games if game["opponent"] in opponents)
        policy_hash_results.append(
            {
                "opponent_policy_hash": policy_hash,
                "opponents": sorted(opponents),
                "bc": bc_hash,
                "selected": selected_hash,
                "delta": selected_hash["win_rate"] - bc_hash["win_rate"],
            }
        )
    policy_hash_macro_delta = sum(row["delta"] for row in policy_hash_results) / len(policy_hash_results)
    side_deltas = {
        side: selected_by_side[side]["win_rate"] - bc_by_side[side]["win_rate"]
        for side in sorted(bc_by_side)
    }
    final_result = next(row for row in primary["results"] if row["role"] == "final")
    frozen = next(row for row in primary["results"] if row["role"] == "frozen-round-3")
    bc_primary = next(row for row in primary["results"] if row["role"] == "bc")
    final_minus_frozen, final_minus_frozen_95 = difference_interval(
        final_result["wins"], final_result["games"], frozen["wins"], frozen["games"]
    )
    if final_result["win_rate"] > frozen["win_rate"]:
        learning_curve = "ROUND15_ABOVE_FROZEN_ROUND3"
    elif final_result["win_rate"] > bc_primary["win_rate"]:
        learning_curve = "EARLY_PEAK_ROUND15_ABOVE_BC_BELOW_OR_EQUAL_ROUND3"
    else:
        learning_curve = "ROUND15_NOT_ABOVE_BC"
    if final_minus_frozen_95[0] > 0 and final_result["minus_bc_95"][0] > 0:
        continuation_gate = "GATE5A_PPO_IMPROVEMENT_CONFIRMED"
    elif final_result["win_rate"] > bc_primary["win_rate"]:
        continuation_gate = "GATE5A_EARLY_PEAK_OR_UNCERTAIN_IMPROVEMENT"
    else:
        continuation_gate = "GATE5A_PPO_NO_PERFORMANCE_IMPROVEMENT"
    conditions = {
        "bc_improvement_confirmed": primary["bc_improvement"] == "CONFIRMED",
        "rule_v0_confirmed_target": primary["rule_v0_confirmed_target"] == "CONFIRMED",
        "primary_side_both_improved": primary["primary_side_both_improved"],
        "holdout_side_both_improved": all(delta > 0 for delta in side_deltas.values()),
        "worst_quartile_not_worse": selected_worst["win_rate"] >= bc_worst["win_rate"],
        "unknown_policy_hash_improved": unknown_delta_95[0] > 0 and policy_hash_macro_delta > 0,
        "fault_illegal_timeout_zero": True,
        "unknown_holdout_pass": bool(unknown_ids) and unknown_delta_95[0] > 0 and policy_hash_macro_delta > 0,
    }
    payload = {
        "schema": "policy-learning-gate5a-final-evaluation-v1",
        "selected": primary["selected"],
        "primary": primary,
        "holdout": {
            "pairing_digest": bc_aggregate["pairing_digest"],
            "bc": bc_aggregate,
            "selected_result": selected_aggregate,
            "by_side": {"bc": bc_by_side, "selected": selected_by_side, "delta": side_deltas},
            "by_opponent": opponent_results,
            "worst_quartile": {
                "definition": "bottom 25% of opponent strata ranked by BC win rate",
                "opponents": sorted(hardest_ids),
                "bc": bc_worst,
                "selected": selected_worst,
                "delta": selected_worst["win_rate"] - bc_worst["win_rate"],
            },
            "unknown_policy_hash": {
                "opponents": sorted(unknown_ids),
                "bc": bc_unknown,
                "selected": selected_unknown,
                "delta": unknown_delta,
                "delta_95": unknown_delta_95,
                "by_policy_hash": policy_hash_results,
                "macro_delta": policy_hash_macro_delta,
            },
        },
        "learning_curve_verdict": learning_curve,
        "round15_minus_frozen_round3": final_minus_frozen,
        "round15_minus_frozen_round3_95": final_minus_frozen_95,
        "ppo_continuation_gate": continuation_gate,
        "conditions": conditions,
        "gate": "GATE5A_FINAL_CANDIDATE_VALIDATED" if all(conditions.values()) else "GATE5A_EVALUATION_COMPLETE_NO_PROMOTION",
        "promotion": "NO_DECISION",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gate5-final-evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select-holdout")
    select.add_argument("--population", type=Path, required=True)
    select.add_argument("--source-run", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    primary = sub.add_parser("summarize-primary")
    primary.add_argument("--output-root", type=Path, required=True)
    primary.add_argument("--candidate-manifest", type=Path, required=True)
    primary.add_argument("--expected-games", type=int, required=True)
    primary.add_argument("--base-seed", type=int, required=True)
    primary.add_argument("--workers", type=int, required=True)
    primary.add_argument("--output", type=Path, required=True)
    final = sub.add_parser("summarize-holdout")
    final.add_argument("--output-root", type=Path, required=True)
    final.add_argument("--primary-summary", type=Path, required=True)
    final.add_argument("--holdout-manifest", type=Path, required=True)
    final.add_argument("--expected-games", type=int, required=True)
    final.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "select-holdout":
            result = select_holdout_opponents(population_path=args.population, source_run=args.source_run, output=args.output)
        elif args.command == "summarize-primary":
            result = summarize_primary(
                output_root=args.output_root,
                candidate_manifest=args.candidate_manifest,
                expected_games=args.expected_games,
                base_seed=args.base_seed,
                workers=args.workers,
                output=args.output,
            )
        else:
            result = summarize_holdout(
                output_root=args.output_root,
                primary_summary_path=args.primary_summary,
                holdout_manifest_path=args.holdout_manifest,
                expected_games=args.expected_games,
                output=args.output,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (FinalEvaluationError, KeyError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
