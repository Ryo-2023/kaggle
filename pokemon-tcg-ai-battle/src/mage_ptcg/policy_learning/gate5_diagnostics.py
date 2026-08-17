"""Compact, reproducible Gate 5a timeout and behavior diagnostics.

The commands write complete machine-readable evidence under the requested
output directory.  Standard output deliberately contains one summary line,
so long-running shells retain a readable progress/error history.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import os
from typing import Any, Callable, Iterable

from mage_ptcg.offline_scaleup.progress import ProgressReporter
from mage_ptcg.student.dataset import RuleBCExample


class DiagnosticError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                yield value


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, int(quantile * len(values)) - 1)]


def _counts(values: Iterable[str | None]) -> dict[str, int]:
    return dict(sorted(Counter(value if isinstance(value, str) and value else "UNRECORDED" for value in values).items()))


def fallback_report(*, run_dir: Path, output: Path) -> dict[str, Any]:
    """Aggregate decision telemetry without exposing raw observations."""
    games = list(_rows(run_dir / "game_results.jsonl"))
    schedule = _read_json(run_dir / "schedule.json")
    schedule_games = {str(row["game_id"]): row for row in schedule.get("games", []) if isinstance(row, dict)}
    decisions: list[dict[str, Any]] = []
    for game in games:
        trajectory = game.get("trajectory_path")
        path = Path(trajectory) if isinstance(trajectory, str) else run_dir / "trajectories" / f"{game.get('game_id')}.jsonl"
        if not path.is_file():
            continue
        rows = list(_rows(path))
        for row in rows[1:]:  # first row is the trajectory metadata header
            if row.get("schema_version") == "offline-scaleup-teacher-decision-v1":
                row = dict(row)
                row["opponent"] = game.get("opponent", schedule_games.get(str(game.get("game_id")), {}).get("opponent"))
                row["candidate_side"] = game.get("candidate_side", row.get("candidate_side"))
                decisions.append(row)
    fallback = [row for row in decisions if row.get("fallback_used") is True]
    candidate = [row for row in decisions if row.get("fallback_used") is not True]
    per_episode = Counter(str(row.get("episode_id")) for row in fallback)
    def action_type(row: dict[str, Any]) -> str:
        example = row.get("rule_bc_example") if isinstance(row.get("rule_bc_example"), dict) else {}
        return f"selection_type={example.get('selection_type', 'UNRECORDED')};context={example.get('selection_context', 'UNRECORDED')};min={example.get('min_count', 'UNRECORDED')};max={example.get('max_count', 'UNRECORDED')}"
    latencies = [float(row["decision_latency_us"]) for row in fallback if isinstance(row.get("decision_latency_us"), (int, float))]
    confidences = [float(row["policy_confidence"]) for row in candidate if isinstance(row.get("policy_confidence"), (int, float))]
    known_digests = sum(isinstance(digest, str) and len(digest) == 64 for row in decisions for digest in row.get("selected_action", []))
    selected_digests = sum(len(row.get("selected_action", [])) for row in decisions if isinstance(row.get("selected_action"), list))
    # A legal empty answer to an optional prompt persists no decision row, so
    # the row-derived counts below cannot see a Rule-v0 delegation on that
    # path.  Read the per-game counters for the complete picture and keep the
    # two sources separate rather than merging them into one number.
    counters: Counter[str] = Counter()
    games_with_counters = 0
    for game in games:
        recorded = game.get("decision_counters")
        if isinstance(recorded, dict):
            games_with_counters += 1
            for key, value in recorded.items():
                if isinstance(value, int):
                    counters[key] += value
    uncaptured = int(counters.get("uncaptured_fallback_count", 0))
    summary = {
        "schema_version": "policy-learning-gate5-fallback-diagnostic-v2",
        "run_dir": str(run_dir),
        "episodes": len({str(row.get("episode_id")) for row in decisions}),
        "fallback_episodes": len(per_episode),
        "total_decisions": len(decisions),
        "candidate_decisions": len(candidate),
        "fallback_decisions": len(fallback),
        # ``fallback_decisions`` counts captured rows only.  These three make
        # the uncaptured path explicit and are ``NOT_RECORDED`` for runs
        # collected before the counter contract existed.
        "decision_counters": {key: int(value) for key, value in sorted(counters.items())} if games_with_counters else "NOT_RECORDED",
        "games_with_decision_counters": games_with_counters,
        "uncaptured_fallback_count": uncaptured if games_with_counters else "NOT_RECORDED",
        "optional_declined_count": int(counters.get("optional_declined_count", 0)) if games_with_counters else "NOT_RECORDED",
        "actual_fallback_decisions": (len(fallback) + uncaptured) if games_with_counters else "NOT_RECORDED",
        "fallback_rate": len(fallback) / len(decisions) if decisions else None,
        "fallback_per_affected_episode": {"mean": statistics.mean(per_episode.values()) if per_episode else 0.0,
                                            "max": max(per_episode.values(), default=0)},
        "fallback_reason": _counts(row.get("fallback_reason") for row in fallback),
        "fallback_action_type": _counts(action_type(row) for row in fallback),
        "fallback_turn": _counts(str(row.get("turn")) if row.get("turn") is not None else None for row in fallback),
        "fallback_phase": _counts(row.get("phase") for row in fallback),
        "fallback_opponent": _counts(row.get("opponent") for row in fallback),
        "fallback_candidate_side": _counts(str(row.get("candidate_side")) if row.get("candidate_side") is not None else None for row in fallback),
        "fallback_deck_fingerprint": _counts(row.get("deck_fingerprint") for row in fallback),
        "fallback_latency_us": {"p50": _percentile(latencies, .50), "p95": _percentile(latencies, .95), "max": max(latencies, default=None)},
        "candidate_confidence": {"recorded": len(confidences), "p50": _percentile(confidences, .50), "p95": _percentile(confidences, .95)},
        # Old artifacts did not record confidence/ppo eligibility.  Preserve
        # this as unavailable instead of inferring it from logits after fact.
        "unknown_action_key_digest": {"selected": selected_digests, "well_formed": known_digests,
                                        "malformed": selected_digests - known_digests,
                                        "unseen_vs_training": "NOT_RECORDED"},
        "mapping_failures": sum(game.get("mapping_valid") is False for game in games),
        "unsupported_state": _counts(row.get("actor_action_mode") for row in decisions if row.get("ppo_eligible") is False),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def policy_contract_report(*, run_dir: Path, output: Path) -> dict[str, Any]:
    """Check that a fresh rollout is usable without mixing behavior sources."""
    games = list(_rows(run_dir / "game_results.jsonl"))
    versions: set[str] = set(); vocabularies: set[str] = set(); decks: set[str] = set()
    mode_counts: Counter[str] = Counter(); total = fallback = eligible = ineligible = finite_log_probability = 0
    malformed_log_probability = 0; affected_episodes = 0; usable_episodes = 0; usable_decisions = 0
    for game in games:
        samples = game.get("teacher_samples")
        if not isinstance(samples, list):
            continue
        episode_ineligible = False; episode_eligible = 0
        for sample in samples:
            if not isinstance(sample, dict):
                episode_ineligible = True
                continue
            total += 1
            fallback += int(sample.get("fallback_used") is True)
            is_eligible = sample.get("ppo_eligible") is True
            if is_eligible:
                eligible += 1; episode_eligible += 1
            else:
                ineligible += 1; episode_ineligible = True
                mode_counts[str(sample.get("actor_action_mode") or "UNRECORDED")] += 1
            log_probability = sample.get("behavior_log_probability")
            if isinstance(log_probability, (int, float)) and math.isfinite(log_probability):
                finite_log_probability += 1
            else:
                malformed_log_probability += 1
            for value, destination in ((sample.get("actor_policy_version"), versions), (sample.get("vocabulary_hash"), vocabularies),
                                       (sample.get("deck_fingerprint"), decks)):
                if isinstance(value, str) and value:
                    destination.add(value)
        if episode_ineligible:
            affected_episodes += 1
        elif episode_eligible:
            usable_episodes += 1; usable_decisions += episode_eligible
    gate = (fallback == 0 and malformed_log_probability == 0 and len(versions) == len(vocabularies) == len(decks) == 1
            and usable_episodes > 0)
    report = {"schema_version": "policy-learning-gate5-policy-contract-v1", "run_dir": str(run_dir),
              "terminal_games": sum(game.get("status") == "DONE" for game in games), "total_decisions": total,
              "fallback_decisions": fallback, "ppo_eligible_decisions": eligible, "ppo_ineligible_decisions": ineligible,
              "ppo_ineligible_action_modes": dict(sorted(mode_counts.items())), "episodes_excluded_from_ppo": affected_episodes,
              "ppo_usable_episodes": usable_episodes, "ppo_usable_decisions": usable_decisions,
              "finite_behavior_log_probabilities": finite_log_probability, "malformed_behavior_log_probabilities": malformed_log_probability,
              "actor_policy_versions": sorted(versions), "vocabulary_hashes": sorted(vocabularies), "deck_fingerprints": sorted(decks),
              "gate": "PASS" if gate else "BLOCKED"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def dagger_rule_proposal_export(*, run_dir: Path, output: Path, budget: int) -> dict[str, Any]:
    """Relabel only candidate/Rule-v0 single-action disagreements.

    The selected rows contain no raw observation.  Rule-v0's already-recorded
    legal proposal becomes the target, while multi-select prompts remain
    excluded because this actor has no action-set distribution.
    """
    if budget < 1:
        raise DiagnosticError("DAgger budget must be positive")
    candidates: list[tuple[float, str, int, dict[str, Any]]] = []
    for game in _rows(run_dir / "game_results.jsonl"):
        if game.get("status") != "DONE" or game.get("legal") is not True:
            continue
        side, winner = game.get("candidate_side"), game.get("winner")
        outcome = "WIN" if winner == side else "LOSS" if winner in (0, 1) else "DRAW" if winner == -1 else "UNKNOWN"
        for index, sample in enumerate(game.get("teacher_samples", [])):
            if not isinstance(sample, dict) or sample.get("fallback_used") or sample.get("ppo_eligible") is not True:
                continue
            target, proposal = sample.get("target_action_digests"), sample.get("rule_proposal_digests")
            if not (isinstance(target, list) and len(target) == 1 and isinstance(proposal, list) and len(proposal) == 1
                    and isinstance(target[0], str) and isinstance(proposal[0], str) and target != proposal):
                continue
            confidence = sample.get("policy_confidence")
            score = 1.0 + (1.0 - float(confidence) if isinstance(confidence, (int, float)) and math.isfinite(confidence) else 0.0)
            candidates.append((-score, str(game.get("game_id")), index, {"sample": sample, "outcome": outcome,
                                                                           "game_id": str(game.get("game_id")), "index": index}))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    rows: list[dict[str, Any]] = []
    for _score, game_id, index, item in candidates[:budget]:
        sample, proposal = item["sample"], item["sample"]["rule_proposal_digests"]
        example = RuleBCExample.from_dict(sample)
        relabeled = replace(example, target_action_digests=tuple(proposal), fallback_used=False).to_dict()
        rows.append({"schema_version": "policy-learning-dagger-relabel-v1", "episode_id": f"dagger-{game_id}",
                     "game_id": game_id, "decision_index": index, "split": "train", "candidate_outcome": item["outcome"],
                     "teacher_trust": "TRUSTED", "family_id": "RULE_V0_DECK", "opponent_type": "RULE_V0_DECK",
                     "state_fingerprint": relabeled["example_id"], "dagger_reason": ["TEACHER_DISAGREEMENT"],
                     "rule_proposal_digests": proposal, "rule_bc_example": relabeled})
    if not rows:
        raise DiagnosticError("no single-action candidate/Rule-v0 disagreements available for DAgger")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return {"schema_version": "policy-learning-gate5-dagger-export-v1", "run_dir": str(run_dir), "candidates": len(candidates),
            "selected": len(rows), "budget": budget, "output": str(output)}


def _timeout_job(run_dir: Path, game_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    schedule = _read_json(run_dir / "schedule.json")
    results = {str(row.get("game_id")): row for row in _rows(run_dir / "game_results.jsonl")}
    candidates = [game_id] if game_id else [key for key, row in results.items() if row.get("fault", {}).get("kind") == "HARD_TIMEOUT"]
    if len(candidates) != 1:
        raise DiagnosticError("exactly one HARD_TIMEOUT game must be selected")
    selected = str(candidates[0])
    jobs = {str(row.get("game_id")): dict(row) for row in schedule.get("games", []) if isinstance(row, dict)}
    if selected not in jobs or selected not in results:
        raise DiagnosticError("selected timeout game is absent from schedule or results")
    return jobs[selected], results[selected]


def timeout_replay(*, run_dir: Path, population: Path, repo: Path, output_dir: Path, game_id: str | None,
                   repetitions: int, parallelism: int, timeout_seconds: float, callback_timeout_seconds: float | None = None,
                   progress: bool | None = None, reporter: ProgressReporter | None = None,
                   on_result: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Replay one immutable job, retaining only per-attempt aggregate evidence."""
    if repetitions < 1 or parallelism < 1 or timeout_seconds <= 0:
        raise DiagnosticError("repetitions, parallelism, and timeout must be positive")
    job, original = _timeout_job(run_dir, game_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    def once(index: int) -> dict[str, Any]:
        attempt = output_dir / f"attempt-{index:02d}"; attempt.mkdir(parents=True, exist_ok=True)
        job_path, result_path = attempt / "job.json", attempt / "worker_result.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        command = [sys.executable, "-m", "mage_ptcg.offline_scaleup", "worker", "--job", str(job_path), "--population", str(population),
                   "--repo", str(repo), "--executor", "cabt", "--result-path", str(result_path),
                   "--trajectory-root", str(attempt / "trajectories"), "--diagnostic-root", str(attempt / "diagnostics")]
        started = time.monotonic()
        try:
            env = dict(os.environ)
            if callback_timeout_seconds is not None:
                env["OFFLINE_SCALEUP_CANDIDATE_CALLBACK_TIMEOUT_SECONDS"] = str(callback_timeout_seconds)
            process = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=timeout_seconds, env=env)
            elapsed = time.monotonic() - started
            (attempt / "stdout.log").write_text(process.stdout, encoding="utf-8")
            (attempt / "stderr.log").write_text(process.stderr, encoding="utf-8")
            payload = _read_json(result_path) if result_path.is_file() else {}
            outcome = payload.get("outcome") if isinstance(payload, dict) else {}
            return {"attempt": index, "status": outcome.get("status", "NO_RESULT"), "returncode": process.returncode,
                    "elapsed_seconds": round(elapsed, 6), "result_present": result_path.is_file(),
                    "candidate_callback_timing_us": outcome.get("candidate_callback_timing_us"),
                    "engine_elapsed_seconds": outcome.get("elapsed_seconds"), "engine_steps": outcome.get("steps"),
                    "candidate_error_code": outcome.get("candidate_error_code")}
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started
            (attempt / "stdout.log").write_text((exc.stdout or "") if isinstance(exc.stdout, str) else "", encoding="utf-8")
            (attempt / "stderr.log").write_text((exc.stderr or "") if isinstance(exc.stderr, str) else "", encoding="utf-8")
            return {"attempt": index, "status": "HARD_TIMEOUT", "returncode": None, "elapsed_seconds": round(elapsed, 6), "result_present": False}

    results: list[dict[str, Any]] = []
    own_reporter = reporter is None
    active_reporter = reporter or ProgressReporter(phase=output_dir.name, total=repetitions, workers=parallelism, unit="replay",
                                                    progress=progress, summary_path=output_dir / "progress_summary.json")
    try:
        with ThreadPoolExecutor(max_workers=min(parallelism, repetitions)) as pool:
            futures = [pool.submit(once, index) for index in range(1, repetitions + 1)]
            for future in as_completed(futures):
                result = future.result(); results.append(result)
                if on_result is not None:
                    on_result(result)
                else:
                    active_reporter.update(1, timeouts=sum(row["status"] == "HARD_TIMEOUT" for row in results))
    finally:
        if own_reporter:
            active_reporter.close()
    results.sort(key=lambda row: row["attempt"])
    status_counts = _counts(row.get("status") for row in results)
    classification = ("REPRODUCES_HARD_TIMEOUT" if status_counts.get("HARD_TIMEOUT", 0) == repetitions
                      else "NO_TIMEOUT_REPRODUCTION" if status_counts.get("DONE", 0) == repetitions
                      else "MIXED_REPRODUCTION")
    report = {"schema_version": "policy-learning-gate5-timeout-replay-v1", "source_run": str(run_dir), "population": str(population),
              "game": job, "original_fault": original.get("fault"), "repetitions": repetitions, "parallelism": parallelism,
              "timeout_seconds": timeout_seconds, "candidate_callback_timeout_seconds": callback_timeout_seconds,
              "results": results, "status_counts": status_counts,
              # Per-callback engine/opponent timers are not exposed by CABT.
              # Candidate envelope timing is saved for completed attempts;
              # timeout attempts deliberately report no invented last step.
              "unavailable_fields": ["opponent_callback_timing", "engine_step_timing", "last_callback_before_hard_timeout"],
              "classification": classification}
    (output_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def timeout_suite(*, run_dir: Path, population: Path, repo: Path, output_dir: Path, game_id: str | None,
                  repetitions: int, parallelism: int, timeout_seconds: float, callback_timeout_seconds: float | None,
                  progress: bool | None = None) -> dict[str, Any]:
    """Run serial then concurrent reproduction with exactly one live bar."""
    output_dir.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporter(phase="gate5a-timeout-diagnosis", total=repetitions * 2, workers=parallelism,
                                unit="replay", progress=progress, summary_path=output_dir / "progress_summary.json")
    completed: list[dict[str, Any]] = []
    def update(result: dict[str, Any]) -> None:
        completed.append(result)
        reporter.update(1, timeouts=sum(row["status"] == "HARD_TIMEOUT" for row in completed))
    try:
        serial = timeout_replay(run_dir=run_dir, population=population, repo=repo, output_dir=output_dir / "serial-1actor",
                                game_id=game_id, repetitions=repetitions, parallelism=1, timeout_seconds=timeout_seconds,
                                callback_timeout_seconds=callback_timeout_seconds, progress=False, reporter=reporter, on_result=update)
        parallel = timeout_replay(run_dir=run_dir, population=population, repo=repo, output_dir=output_dir / f"parallel-{parallelism}actors",
                                  game_id=game_id, repetitions=repetitions, parallelism=parallelism, timeout_seconds=timeout_seconds,
                                  callback_timeout_seconds=callback_timeout_seconds, progress=False, reporter=reporter, on_result=update)
    finally:
        reporter.close()
    verdict = ("CONCURRENCY_SENSITIVE" if serial["classification"] == "NO_TIMEOUT_REPRODUCTION" and parallel["classification"] != "NO_TIMEOUT_REPRODUCTION"
               else "REPRODUCES_SERIAL" if serial["classification"] != "NO_TIMEOUT_REPRODUCTION"
               else "NOT_REPRODUCED")
    result = {"schema_version": "policy-learning-gate5-timeout-suite-v1", "serial": serial, "parallel": parallel,
              "classification": verdict}
    (output_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mage-policy-gate5-diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    fallback = commands.add_parser("fallback-report"); fallback.add_argument("--run-dir", type=Path, required=True); fallback.add_argument("--output", type=Path, required=True)
    contract = commands.add_parser("policy-contract-report"); contract.add_argument("--run-dir", type=Path, required=True); contract.add_argument("--output", type=Path, required=True)
    dagger = commands.add_parser("dagger-rule-proposal-export"); dagger.add_argument("--run-dir", type=Path, required=True); dagger.add_argument("--output", type=Path, required=True); dagger.add_argument("--budget", type=int, default=1024)
    replay = commands.add_parser("timeout-replay"); replay.add_argument("--run-dir", type=Path, required=True); replay.add_argument("--population", type=Path, required=True); replay.add_argument("--repo", type=Path, required=True); replay.add_argument("--output-dir", type=Path, required=True); replay.add_argument("--game-id"); replay.add_argument("--repetitions", type=int, default=5); replay.add_argument("--parallelism", type=int, default=1); replay.add_argument("--timeout-seconds", type=float, default=180.0); replay.add_argument("--candidate-callback-timeout-seconds", type=float, default=30.0); replay.add_argument("--progress", action="store_true")
    suite = commands.add_parser("timeout-suite"); suite.add_argument("--run-dir", type=Path, required=True); suite.add_argument("--population", type=Path, required=True); suite.add_argument("--repo", type=Path, required=True); suite.add_argument("--output-dir", type=Path, required=True); suite.add_argument("--game-id"); suite.add_argument("--repetitions", type=int, default=5); suite.add_argument("--parallelism", type=int, default=8); suite.add_argument("--timeout-seconds", type=float, default=180.0); suite.add_argument("--candidate-callback-timeout-seconds", type=float, default=30.0); suite.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "fallback-report":
            result = fallback_report(run_dir=args.run_dir, output=args.output)
            print(f"[Gate 5a] fallback diagnostic: decisions={result['total_decisions']} fallback={result['fallback_decisions']} episodes={result['fallback_episodes']} output={args.output}")
        elif args.command == "policy-contract-report":
            result = policy_contract_report(run_dir=args.run_dir, output=args.output)
            print(f"[Gate 5a] policy contract: gate={result['gate']} decisions={result['total_decisions']} ppo_usable={result['ppo_usable_decisions']} excluded_episodes={result['episodes_excluded_from_ppo']} output={args.output}")
        elif args.command == "dagger-rule-proposal-export":
            result = dagger_rule_proposal_export(run_dir=args.run_dir, output=args.output, budget=args.budget)
            print(f"[Gate 5a] DAgger export: candidates={result['candidates']} selected={result['selected']} output={args.output}")
        elif args.command == "timeout-replay":
            result = timeout_replay(run_dir=args.run_dir, population=args.population, repo=args.repo, output_dir=args.output_dir,
                                    game_id=args.game_id, repetitions=args.repetitions, parallelism=args.parallelism, timeout_seconds=args.timeout_seconds,
                                    callback_timeout_seconds=args.candidate_callback_timeout_seconds,
                                    progress=True if args.progress else None)
            print(f"[Gate 5a] timeout replay: attempts={args.repetitions} parallelism={args.parallelism} statuses={result['status_counts']} output={args.output_dir / 'summary.json'}")
        else:
            result = timeout_suite(run_dir=args.run_dir, population=args.population, repo=args.repo, output_dir=args.output_dir,
                                   game_id=args.game_id, repetitions=args.repetitions, parallelism=args.parallelism,
                                   timeout_seconds=args.timeout_seconds, callback_timeout_seconds=args.candidate_callback_timeout_seconds,
                                   progress=True if args.progress else None)
            print(f"[Gate 5a] timeout suite: serial={result['serial']['status_counts']} parallel={result['parallel']['status_counts']} classification={result['classification']} output={args.output_dir / 'summary.json'}")
        return 0
    except (DiagnosticError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Gate 5a diagnostic failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
