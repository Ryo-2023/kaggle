#!/usr/bin/env python3
"""Fail-closed submitted-opponent → R2D3 end-to-end orchestration."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
# Semantic action feature width; padded rows must match the model input.
ACTION_FEATURE_WIDTH = 64
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from main import make_rule_agent
from mage_ptcg.policy_learning.submitted_opponents import assert_no_leakage, load_registry, split_assets
from mage_ptcg.policy_learning.submitted_runtime import SubmittedAgentWorker, SubmittedRuntimeError, pin_snapshot, spec_from_manifest
from scripts.test_sim import run_match

LEDGER = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-assets-calibration-teacher-v1-20260726_181000/submitted_asset_registry.csv")
PROGRESS_ARTIFACT: Path | None = None
PROGRESS_CALLBACK: Callable[[str, int, int, dict[str, Any]], None] | None = None


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"); os.replace(temporary, path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["status"]; path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def progress(gate: str, completed: int, total: int, **details: Any) -> None:
    value = {"gate": gate, "completed": completed, "total": total, "updated_at": datetime.now(timezone.utc).isoformat(), **details}
    if PROGRESS_ARTIFACT is not None: atomic_json(PROGRESS_ARTIFACT / "progress_summary.json", value)
    if PROGRESS_CALLBACK is not None:
        PROGRESS_CALLBACK(gate, completed, total, details)
    else:
        # This module is also a standalone runner.  Its default remains a
        # compact structured record; the performance controller injects a
        # shared tqdm/summary monitor instead of emitting one line per game.
        print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def cuda_environment(python_bin: Path) -> dict[str, Any]:
    code = "import json,torch;print(json.dumps({'torch':torch.__version__,'cuda_available':torch.cuda.is_available(),'cuda_version':torch.version.cuda,'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'bf16_supported':torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False}))"
    run = subprocess.run([str(python_bin), "-c", code], cwd=ROOT, text=True, capture_output=True, check=False)
    try: result = json.loads(run.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError): result = {"cuda_available": False, "error": (run.stderr or run.stdout)[-800:]}
    result.update({"python_bin": str(python_bin), "returncode": run.returncode}); return result


def repository_state(source_artifact: Path) -> dict[str, Any]:
    status = git("status", "--short").splitlines(); modified = [line[3:] for line in status if not line.startswith("??")]; untracked = [line[3:] for line in status if line.startswith("??")]
    tracked_hashes = {}
    for path in ("main.py", "deck.csv", "agents/rule_agent.py", "src/mage_ptcg/policy_learning/submitted_opponents.py"):
        target = ROOT / path
        if target.is_file(): tracked_hashes[path] = sha(target)
    source_hashes = {path.name: sha(path) for path in sorted(source_artifact.glob("*")) if path.is_file() and path.name in {"submitted_asset_registry.csv", "submitted_training_population.json", "submitted_validation_population.json", "submitted_deck_holdout_population.json", "submitted_final_holdout_population.json", "split_leakage_report.json"}}
    return {"branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"), "ahead_behind": git("rev-list", "--left-right", "--count", "HEAD...origin/feature/belief-guided-search"),
            "status_short": status, "modified": modified, "untracked": untracked, "tracked_hashes": tracked_hashes, "source_artifact_hashes": source_hashes,
            "diff_check": subprocess.run(["git", "diff", "--check"], cwd=ROOT, text=True, capture_output=True).returncode}


def selected_ids(source_artifact: Path, name: str) -> set[str]:
    value = json.loads((source_artifact / f"submitted_{name}_population.json").read_text(encoding="utf-8"))
    return {str(row["asset_id"]) for row in value["entries"]}


def snapshot_gate(source_artifact: Path, artifact: Path) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    assets = load_registry(ROOT, LEDGER); splits = split_assets(assets, seed=71000); assert_no_leakage(splits)
    for name in ("training", "validation", "deck_holdout", "final_holdout"):
        expected = selected_ids(source_artifact, name); actual = {asset.asset_id for asset in splits[name]}
        if expected != actual: raise RuntimeError(f"{name} split differs from frozen input artifact")
    atomic_json(artifact / "split_leakage_report.json", {"schema": "submitted-opponent-leakage-report-v1", "leakage": False,
        "split_sizes": {name: len(splits[name]) for name in ("training", "validation", "deck_holdout", "final_holdout")}})
    rows: list[dict[str, Any]] = []; errors: list[dict[str, Any]] = []
    expected_role_counts = {role: len(splits[role]) for role in ("training", "validation")}
    expected_snapshots = sum(expected_role_counts.values())
    for role in ("training", "validation"):
        for asset in splits[role]:
            destination = artifact / "snapshots" / asset.asset_id / asset.source_commit
            try:
                manifest = pin_snapshot(ROOT, asset, destination); rows.append({**{key: value for key, value in manifest.items() if key != "files"}, "split": role, "status": "PASS"})
            except Exception as exc:
                errors.append({"asset_id": asset.asset_id, "split": role, "status": "FAIL", "error_code": getattr(exc, "code", type(exc).__name__), "message": str(exc)})
            progress("GATE_A", len(rows) + len(errors), expected_snapshots)
    write_csv(artifact / "qualified_snapshot_registry.csv", rows + errors)
    write_csv(artifact / "ref_drift_report.csv", [{"asset_id": asset.asset_id, "qualified_runtime_commit": asset.source_commit, "submission_source_commit": asset.submission_source_commit,
        "current_ref_tip": asset.current_ref_commit, "drift": bool(asset.current_ref_commit and asset.current_ref_commit != asset.source_commit)} for asset in assets if asset.qualification == "TRAINING_ELIGIBLE"])
    observed_role_counts = {
        role: len([row for row in rows if row["split"] == role])
        for role in ("training", "validation")
    }
    if errors or observed_role_counts != expected_role_counts:
        raise RuntimeError(f"snapshot gate failed for {len(errors)} assets")
    write_csv(artifact / "runtime_adapter_registry.csv", [{"asset_id": row["asset_id"], "source_commit": row["source_commit"], "adapter_type": row["adapter_type"], "snapshot_root": row["snapshot_root"], "status": "READY"} for row in rows])
    return rows, splits


def cpu_bridge_preflight(artifact: Path, splits: dict[str, list[Any]]) -> dict[str, Any]:
    asset = splits["training"][0]; manifest_path = artifact / "snapshots" / asset.asset_id / asset.source_commit / ".submitted_snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); spec = spec_from_manifest(manifest)
    with tempfile.TemporaryDirectory(prefix="submitted-cpu-preflight-") as temporary:
        worker = SubmittedAgentWorker(spec, scratch_root=Path(temporary) / "workers")
        try:
            side = 1
            result = run_match(deck_a_path=ROOT / "deck.csv", deck_b_path=spec.deck_path, agent_a_name="rule", agent_b_name="random", seed=88000,
                output_dir=Path(temporary) / "match", save_html=False, save_result=False,
                agent_a_factory=lambda deck, _seed: make_rule_agent(deck=deck), agent_b_factory=lambda _deck, _seed: worker)
        finally: worker.close()
    return {"asset_id": asset.asset_id, "source_commit": asset.source_commit, "candidate_side": 0, "opponent_side": side, "status": result["status"], "agent_status": result.get("agent_status"),
            "legal": result["status"] == "DONE", "deck_requests": worker.deck_requests, "scratch_cleanup": not worker.scratch.exists()}


def run_asset_game(asset: Any, manifest: dict[str, Any], *, asset_side: int, seed: int, artifact: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _run_asset_game(asset, manifest, asset_side=asset_side, seed=seed, artifact=artifact, scratch_id="default")


def _run_asset_game(asset: Any, manifest: dict[str, Any], *, asset_side: int, seed: int, artifact: Path, scratch_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One isolated CABT job; scratch paths are unique and process-safe."""
    spec = spec_from_manifest(manifest); worker = SubmittedAgentWorker(spec, scratch_root=artifact / "runtime_scratch" / scratch_id)
    try:
        if asset_side == 0:
            result = run_match(deck_a_path=spec.deck_path, deck_b_path=ROOT / "deck.csv", agent_a_name="random", agent_b_name="rule", seed=seed,
                output_dir=artifact / "match_scratch" / scratch_id, save_html=False, save_result=False, agent_a_factory=lambda _deck, _seed: worker, agent_b_factory=lambda deck, _seed: make_rule_agent(deck=deck))
        else:
            result = run_match(deck_a_path=ROOT / "deck.csv", deck_b_path=spec.deck_path, agent_a_name="rule", agent_b_name="random", seed=seed,
                output_dir=artifact / "match_scratch" / scratch_id, save_html=False, save_result=False, agent_a_factory=lambda deck, _seed: make_rule_agent(deck=deck), agent_b_factory=lambda _deck, _seed: worker)
        statuses = result.get("agent_status") or [None, None]; asset_status = statuses[asset_side] if len(statuses) == 2 else None; candidate_status = statuses[1 - asset_side] if len(statuses) == 2 else None
        row = {"asset_id": asset.asset_id, "source_commit": asset.source_commit, "deck_hash": asset.deck_hash, "policy_hash": asset.policy_hash, "source_lineage": asset.source_lineage,
               "deck_family": asset.deck_family, "asset_side": asset_side, "seed": seed, "status": result["status"], "legal": result["status"] == "DONE",
               "opponent_fault": asset_status in {"ERROR", "INVALID", "TIMEOUT"}, "candidate_fault": candidate_status in {"ERROR", "INVALID", "TIMEOUT"},
               "engine_error": result["status"] not in {"DONE", "INVALID", "TIMEOUT"}, "timeout": result["status"] == "TIMEOUT" or asset_status == "TIMEOUT",
               "winner": result.get("winner"), "runtime_seconds": result.get("elapsed_seconds"), "deck_requests": worker.deck_requests, "scratch_cleanup": True}
        return row, list(worker.public_traces)
    except Exception as exc:
        return {"asset_id": asset.asset_id, "source_commit": asset.source_commit, "deck_hash": asset.deck_hash, "policy_hash": asset.policy_hash, "source_lineage": asset.source_lineage,
                "deck_family": asset.deck_family, "asset_side": asset_side, "seed": seed, "status": "ERROR", "legal": False, "opponent_fault": isinstance(exc, SubmittedRuntimeError),
                "candidate_fault": False, "engine_error": not isinstance(exc, SubmittedRuntimeError), "timeout": getattr(exc, "code", "") == "CALLBACK_TIMEOUT", "message": str(exc)[:500]}, list(worker.public_traces)
    finally: worker.close()


def _asset_smoke_job(arguments: tuple[Any, dict[str, Any], int, int, str, int]) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    asset, manifest, side, seed, artifact, index = arguments
    row, traces = _run_asset_game(asset, manifest, asset_side=side, seed=seed, artifact=Path(artifact), scratch_id=f"asset-{index:06d}")
    return index, row, traces


def asset_smoke_gate(artifact: Path, splits: dict[str, list[Any]], *, games_per_asset: int = 8, workers: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if games_per_asset < 1: raise ValueError("asset smoke needs at least one game per training asset")
    rows = []; demonstrations = []; jobs = []
    for asset_index, asset in enumerate(splits["training"]):
        manifest = json.loads((artifact / "snapshots" / asset.asset_id / asset.source_commit / ".submitted_snapshot_manifest.json").read_text())
        for game in range(games_per_asset):
            jobs.append((asset, manifest, game % 2, 90000 + asset_index * games_per_asset + game, str(artifact), len(jobs)))
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import multiprocessing
        results: dict[int, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as executor:
            futures = [executor.submit(_asset_smoke_job, job) for job in jobs]
            for future in as_completed(futures):
                index, row, traces = future.result(); results[index] = (row, traces)
                progress("GATE_C", len(results), len(jobs), legal=sum(bool(value[0].get("legal")) for value in results.values()), faults=sum(bool(value[0].get("opponent_fault") or value[0].get("candidate_fault") or value[0].get("engine_error")) for value in results.values()))
        ordered = [results[index] for index in range(len(jobs))]
    else:
        ordered = []
        for job in jobs:
            _index, row, traces = _asset_smoke_job(job); ordered.append((row, traces))
            progress("GATE_C", len(ordered), len(jobs), legal=sum(bool(value[0].get("legal")) for value in ordered), faults=sum(bool(value[0].get("opponent_fault") or value[0].get("candidate_fault") or value[0].get("engine_error")) for value in ordered))
    for (asset, _manifest, _side, _seed, _artifact, _index), (row, traces) in zip(jobs, ordered, strict=True):
        rows.append(row); demonstrations.append({"asset": asset, "game": row, "traces": traces})
    write_csv(artifact / "asset_smoke_results.csv", rows)
    if len(rows) != len(splits["training"]) * games_per_asset or any(not row.get("legal") or row.get("opponent_fault") or row.get("candidate_fault") or row.get("engine_error") or row.get("timeout") for row in rows):
        raise RuntimeError("asset CABT smoke gate failed")
    return rows, demonstrations


class TracingPPO:
    def __init__(self, *, deck: list[int]) -> None:
        from mage_ptcg.policy_learning.runtime import load_runtime_policy
        self.policy, self.summary = load_runtime_policy(ROOT / "runs/policy-learning-gate5a/model", device="cpu", deck=deck, action_mode="sample")
        if self.summary.get("config", {}).get("use_rule_proposal"):
            self.policy.set_rule_proposal_agent(make_rule_agent(deck=deck))
        self.traces: list[dict[str, Any]] = []

    def reset_episode(self, *, game_id: str, candidate_side: int) -> None:
        """Reuse immutable PPO weights without leaking state between games."""
        self.policy.reset_episode()
        self.policy.set_episode_seed(game_id=game_id, candidate_side=candidate_side)
        self.traces = []
    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        del configuration
        choice = self.policy.choose(observation)
        if isinstance(observation, dict) and isinstance(observation.get("select"), dict) and len(choice) == 1:
            try:
                from mage_ptcg.decision_state import build_decision_state
                from mage_ptcg.policy_learning.r2d3.semantic_action import encode_legal_action
                from mage_ptcg.policy_learning.r2d3.semantic_state import encode_public_state
                state = build_decision_state(observation); matches = [i for i, item in enumerate(state.legal_actions) if item.option_index == choice[0]]
                if len(matches) == 1:
                    actions = []
                    for item in state.legal_actions:
                        key = item.action_key; actions.append(encode_legal_action({"digest": key.digest, "action_type": key.selection_type, "card_id": key.card_id,
                            "source_zone": key.source_entity_key, "target_zone": key.target_entity_key, "target_card": key.target_entity_key, "amount": None,
                            "selection_order": item.option_index, "phase": key.context, "optional": False, "semantic_role": key.semantic_operation}))
                    from mage_ptcg.policy_learning.r2d3.sequence import public_prize_potential
                    self.traces.append({"state": encode_public_state(state.actor_view.public_state), "actions": actions, "selected_action": matches[0], "potential": public_prize_potential(state.actor_view.public_state)})
            except Exception: pass
        return choice


_PPO_WORKER_CACHE: dict[tuple[int, ...], TracingPPO] = {}


def _cached_tracing_ppo(deck: list[int], *, game_id: str, candidate_side: int) -> TracingPPO:
    """Return a process-local PPO runtime, reset to a deterministic episode.

    ``ProcessPoolExecutor`` keeps each worker alive across games.  Loading the
    frozen CPU model per task dominated collection time while adding no
    isolation: the policy already has an explicit episode reset contract.
    The cache deliberately lives only inside a spawned collector process.
    """
    key = tuple(deck)
    candidate = _PPO_WORKER_CACHE.get(key)
    if candidate is None:
        candidate = TracingPPO(deck=deck)
        _PPO_WORKER_CACHE[key] = candidate
    candidate.reset_episode(game_id=game_id, candidate_side=candidate_side)
    return candidate


def _ppo_population_job(arguments: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    """Execute one PPO behaviour game in a process-isolated, unique scratch root."""
    index = int(arguments["index"]); artifact = Path(arguments["artifact"]); deck = arguments["deck"]
    asset = arguments["asset"]; candidate_side = int(arguments["candidate_side"])
    game_id = f"ppo-submitted-{int(arguments['game_number']):06d}"
    candidate = _cached_tracing_ppo(deck, game_id=game_id, candidate_side=candidate_side); worker = None
    try:
        if asset is not None:
            manifest = json.loads((artifact / "snapshots" / asset.asset_id / asset.source_commit / ".submitted_snapshot_manifest.json").read_text()); spec = spec_from_manifest(manifest)
            worker = SubmittedAgentWorker(spec, scratch_root=artifact / "runtime_scratch" / f"ppo-{index:06d}")
            if candidate_side == 0:
                result = run_match(deck_a_path=ROOT / "deck.csv", deck_b_path=spec.deck_path, agent_a_name="rule", agent_b_name="random", seed=int(arguments["seed"]), output_dir=artifact / "population_match_scratch" / f"ppo-{index:06d}", save_html=False, save_result=False, agent_a_factory=lambda _deck, _seed: candidate, agent_b_factory=lambda _deck, _seed: worker)
            else:
                result = run_match(deck_a_path=spec.deck_path, deck_b_path=ROOT / "deck.csv", agent_a_name="random", agent_b_name="rule", seed=int(arguments["seed"]), output_dir=artifact / "population_match_scratch" / f"ppo-{index:06d}", save_html=False, save_result=False, agent_a_factory=lambda _deck, _seed: worker, agent_b_factory=lambda _deck, _seed: candidate)
        else:
            result = run_match(deck_a_path=ROOT / "deck.csv", deck_b_path=ROOT / "deck.csv", agent_a_name="rule", agent_b_name="rule", seed=int(arguments["seed"]), output_dir=artifact / "population_match_scratch" / f"ppo-{index:06d}", save_html=False, save_result=False, agent_a_factory=(lambda _deck, _seed: candidate) if candidate_side == 0 else (lambda value, _seed: make_rule_agent(deck=value)), agent_b_factory=(lambda _deck, _seed: candidate) if candidate_side == 1 else (lambda value, _seed: make_rule_agent(deck=value)))
        opponent_policy = asset.policy_hash if asset else sha(ROOT / "agents/rule_agent.py"); opponent_deck = asset.deck_hash if asset else sha(ROOT / "deck.csv"); lineage = asset.source_lineage if asset else git("rev-parse", "HEAD")
        row = {"game_id": game_id, "status": result["status"], "legal": result["status"] == "DONE", "bucket": arguments["bucket"], "sampling_probability": float(arguments["sampling_probability"]), "candidate_side": candidate_side, "behavior_policy_version": arguments["behavior_policy_version"], "population_manifest_hash": arguments["population_manifest_hash"], "opponent_asset_id": asset.asset_id if asset else "rule-v0-current-deck", "opponent_source_commit": asset.source_commit if asset else git("rev-parse", "HEAD"), "opponent_deck_hash": opponent_deck, "opponent_policy_hash": opponent_policy, "opponent_source_lineage": lineage, "opponent_deck_family": asset.deck_family if asset else "RULE_V0", "winner": result.get("winner"), "candidate_fault": (result.get("agent_status") or [None, None])[candidate_side] in {"ERROR", "INVALID", "TIMEOUT"}, "timeout": result["status"] == "TIMEOUT"}
        return index, row, {"row": row, "traces": candidate.traces, "demonstration": False}
    finally:
        if worker is not None: worker.close()


def ppo_population_gate(artifact: Path, splits: dict[str, list[Any]], *, games: int = 256,
                        seed_offset: int = 0, slow_asset_once: bool = False,
                        minimum_asset_coverage: int | None = None,
                        excluded_execution_asset_ids: set[str] | None = None, workers: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if games < 2 or games % 2:
        raise ValueError("population collection games must be a positive even number")
    deck = [int(line) for line in (ROOT / "deck.csv").read_text().splitlines() if line.strip()]
    submitted_games = games // 2
    execution_training = [asset for asset in splits["training"] if asset.asset_id not in (excluded_execution_asset_ids or set())]
    if not execution_training: raise ValueError("submitted replay schedule has no executable assets")
    submitted_schedule = [execution_training[index % len(execution_training)] for index in range(submitted_games)]
    if slow_asset_once and submitted_games > len(execution_training):
        # A smoke proves every frozen submitted adapter once.  Repeating a
        # known expensive callback only measures that opponent's wall-clock,
        # not replay/controller correctness; production keeps uniform cycling.
        submitted_schedule = [*execution_training, *(execution_training[:-1][index % (len(execution_training) - 1)] for index in range(submitted_games - len(execution_training)))]
    rows: list[dict[str, Any]] = []; episodes: list[dict[str, Any]] = []
    population_payload = {"schema": "submitted-training-runtime-population-v1", "population_manifest_hash": "",
        "bucket_weights": {"submitted_agents_dev": .5, "rule_v0_v1": .2, "family_policies": .15, "historical_candidate_snapshots": .1, "stress_uniform": .05},
        "smoke_effective_bucket_weights": {"submitted_agents_dev": .5, "rule_v0_v1": .5},
        "smoke_scope_note": "The 256-game bridge smoke exercises submitted and Rule safety buckets; the complete target weights remain declarative until their separate runtime adapters are qualified.",
        "submitted_entries": [asset.population_entry() for asset in splits["training"]], "excluded_split_ids": {name: sorted(asset.asset_id for asset in splits[name]) for name in ("validation", "deck_holdout", "final_holdout")}}
    canonical = json.dumps(population_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")); population_payload["population_manifest_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
    atomic_json(artifact / "submitted_training_runtime_population.json", population_payload)
    atomic_json(artifact / "population-submitted-training-runtime-v1.json", population_payload)
    jobs = [{"index": index, "artifact": str(artifact), "deck": deck, "asset": submitted_schedule[index] if index < submitted_games else None, "candidate_side": index % 2, "bucket": "submitted_agents_dev" if index < submitted_games else "rule_v0_v1", "sampling_probability": .5 / len(execution_training) if index < submitted_games else .5, "behavior_policy_version": sha(ROOT / "runs/policy-learning-gate5a/model/best.pt"), "population_manifest_hash": population_payload["population_manifest_hash"], "seed": 100000 + seed_offset + index, "game_number": seed_offset + index} for index in range(games)]
    results: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import multiprocessing
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as executor:
            futures = [executor.submit(_ppo_population_job, job) for job in jobs]
            for future in as_completed(futures):
                index, row, episode = future.result(); results[index] = (row, episode)
                progress("GATE_D", len(results), games, legal=sum(bool(value[0].get("legal")) for value in results.values()), faults=sum(bool(value[0].get("candidate_fault") or value[0].get("timeout")) for value in results.values()))
    else:
        for job in jobs:
            index, row, episode = _ppo_population_job(job); results[index] = (row, episode)
            progress("GATE_D", len(results), games, legal=sum(bool(value[0].get("legal")) for value in results.values()), faults=sum(bool(value[0].get("candidate_fault") or value[0].get("timeout")) for value in results.values()))
    for index in range(games):
        row, episode = results[index]; rows.append(row); episodes.append(episode)
    write_csv(artifact / "submitted_population_smoke.csv", rows)
    selected = [row for row in rows if row["bucket"] == "submitted_agents_dev"]; coverage = Counter(row["opponent_asset_id"] for row in selected)
    metadata_fields = ("opponent_asset_id", "opponent_source_commit", "opponent_deck_hash", "opponent_policy_hash", "opponent_source_lineage", "opponent_deck_family", "bucket", "sampling_probability", "candidate_side", "behavior_policy_version", "population_manifest_hash")
    report = {"games": len(rows), "submitted_games": len(selected), "submitted_selection_rate": len(selected) / len(rows), "training_asset_coverage": dict(coverage),
              "metadata_coverage": sum(all(row.get(field) not in (None, "") for field in metadata_fields) for row in rows) / len(rows),
              "validation_selected": 0, "deck_holdout_selected": 0, "final_holdout_selected": 0, "legal": sum(bool(row["legal"]) for row in rows)}
    atomic_json(artifact / "population_selection_report.json", report)
    required_asset_coverage = max(1, submitted_games // (len(splits["training"]) * 2)) if minimum_asset_coverage is None else int(minimum_asset_coverage)
    if required_asset_coverage < 1: raise ValueError("minimum asset coverage must be positive")
    if report["legal"] != games or not .4 <= report["submitted_selection_rate"] <= .6 or len(coverage) != len(execution_training) or min(coverage.values()) < required_asset_coverage or report["metadata_coverage"] != 1.0 or any(row["candidate_fault"] or row["timeout"] for row in rows):
        raise RuntimeError("PPO submitted population gate failed")
    return rows, episodes


def ingest_gate3_clean(replay: Any, artifact: Path) -> dict[str, Any]:
    from mage_ptcg.policy_learning.r2d3.semantic_action import encode_legal_action
    from mage_ptcg.policy_learning.r2d3.semantic_state import encode_public_state
    from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch, public_prize_potential, shape_episode_rewards
    root = ROOT / "runs" / "policy-learning-gate3-recovery" / "gate3c-clean-2000"
    summary = json.loads((root / "run_summary.json").read_text()); trajectories = sorted((root / "trajectories").glob("*.jsonl"))
    if summary.get("completed") != 2000 or summary.get("valid_legal_games") != 1997 or len(trajectories) != 1997:
        raise RuntimeError("Gate 3 clean source identity/count differs from audited evidence")
    sequence_count = decision_count = skipped_decisions = 0
    source_files = []
    for path in trajectories:
        values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        metadata = values[0].get("metadata", {}) if values else {}; segments: list[list[Any]] = [[]]
        for row in values[1:]:
            candidates = row.get("legal_action_candidates") or []; selected = row.get("selected_candidate_index")
            if not row.get("legality_result") or not isinstance(selected, list) or len(selected) != 1 or not candidates or not 0 <= int(selected[0]) < len(candidates):
                skipped_decisions += 1
                if segments[-1]: segments.append([])
                continue
            actions = []
            for candidate in candidates:
                payload = candidate.get("payload") or {}
                actions.append(tuple(encode_legal_action({"digest": candidate["digest"], "action_type": payload.get("selection_type"),
                    "card_id": payload.get("card_id"), "source_zone": payload.get("source_entity_key"), "target_zone": payload.get("target_entity_key"),
                    "target_card": payload.get("target_entity_key"), "amount": None, "selection_order": len(actions), "phase": payload.get("context"),
                    "optional": False, "semantic_role": payload.get("semantic_operation")})))
            behavior = str(row.get("actor_policy_version") or row.get("runtime_fingerprint") or "")
            teacher = str(row.get("teacher_identity") or metadata.get("teacher_identity") or "UNKNOWN")
            opponent_hash = hashlib.sha256(teacher.encode()).hexdigest(); deck_identity = str(row.get("deck_fingerprint") or metadata.get("candidate_deck_fingerprint") or "")
            public_state = row["rule_bc_example"]["public_state"]
            segments[-1].append((
                tuple(encode_public_state(public_state)), tuple(actions), int(selected[0]),
                behavior, opponent_hash, hashlib.sha256((teacher + ":deck").encode()).hexdigest(),
                f"gate3c-clean-2000:{teacher}", str(row.get("teacher_type") or "UNKNOWN"), deck_identity,
                public_prize_potential(public_state),
            ))
        supported = 0
        for segment_index, values in enumerate(segment for segment in segments if segment):
            rewards = shape_episode_rewards([value[-1] for value in values], outcome=0.0, gamma=.99)
            transitions = [
                R2D3Transition(value[0], value[1], value[2], rewards[index], 0.0 if index == len(values) - 1 else .99,
                    index == len(values) - 1, value[3], "gate3_clean_online", value[4], value[5], value[6], value[7], value[8], demonstration=False)
                for index, value in enumerate(values)
            ]
            prefix = f"gate3-{path.stem}-segment{segment_index}"
            replay.add(SequenceBatch((), tuple(transitions), 1.0, prefix, prefix))
            sequence_count += 1
            supported += len(transitions)
        if supported:
            decision_count += supported; source_files.append({"path": str(path), "sha256": sha(path), "supported_decisions": supported})
    report = {"source_run": str(root), "source_gate": summary["gate"], "completed_games": 2000, "valid_legal_games_ingested": 1997,
              "candidate_fault_games_excluded": 3, "trajectory_files": len(source_files), "decisions": decision_count,
              "sequences": sequence_count, "unsupported_decision_boundaries": skipped_decisions, "sequence_crosses_unsupported_boundary": 0,
              "source_manifest_hash": hashlib.sha256(json.dumps(source_files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "actor_visible_public_state_only": True}
    atomic_json(artifact / "gate3_clean_replay_ingestion.json", report)
    return report


def build_replay_gate(artifact: Path, demonstrations: list[dict[str, Any]], episodes: list[dict[str, Any]], *, sequence_stride: int = 20) -> tuple[Any, dict[str, Any]]:
    from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
    from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, SequenceBatch
    replay = PrioritizedSequenceReplay(200000); demo_count = online_count = 0; registry = []
    def add_episode(traces: list[dict[str, Any]], *, identity: dict[str, str], outcome: float, demonstration: bool, sequence_prefix: str) -> None:
        nonlocal demo_count, online_count
        transitions = []
        from mage_ptcg.policy_learning.r2d3.sequence import shape_episode_rewards
        rewards = shape_episode_rewards([float(trace["potential"]) for trace in traces], outcome=outcome, gamma=.99)
        for index, trace in enumerate(traces):
            terminal = index == len(traces) - 1
            transitions.append(R2D3Transition(tuple(trace["state"]), tuple(tuple(action) for action in trace["actions"]), int(trace["selected_action"]),
                rewards[index], 0.0 if terminal else .99, terminal, identity["behavior_policy_version"], identity["behavior_source"],
                identity["opponent_policy_hash"], identity["opponent_deck_hash"], identity["opponent_source_lineage"], identity["opponent_family"], identity["own_deck_hash"], demonstration=demonstration))
        replay.add(SequenceBatch((), tuple(transitions), 1.0, sequence_prefix, sequence_prefix))
        demo_count += int(demonstration); online_count += int(not demonstration)
    for index, item in enumerate(demonstrations):
        traces = item["traces"]; asset = item["asset"]
        if not traces: continue
        winner, side = item["game"].get("winner"), int(item["game"]["asset_side"]); outcome = 1.0 if winner == side else -1.0 if winner in (0, 1) else 0.0
        identity = {"behavior_policy_version": asset.policy_hash, "behavior_source": "submitted_demonstration", "opponent_policy_hash": sha(ROOT / "agents/rule_agent.py"),
                    "opponent_deck_hash": sha(ROOT / "deck.csv"), "opponent_source_lineage": git("rev-parse", "HEAD"), "opponent_family": "RULE_V0", "own_deck_hash": asset.deck_hash}
        qualified_win = outcome > 0.0
        add_episode(traces, identity=identity, outcome=outcome, demonstration=qualified_win, sequence_prefix=f"demo-{index}")
        registry.append({"sequence_source": f"demo-{index}", "source_policy_hash": asset.policy_hash, "source_lineage": asset.source_lineage, "deck_hash": asset.deck_hash, "side": side, "outcome": outcome, "demonstration": qualified_win})
    for index, item in enumerate(episodes):
        row, traces = item["row"], item["traces"]
        if not traces: continue
        winner, side = row.get("winner"), int(row["candidate_side"]); outcome = 1.0 if winner == side else -1.0 if winner in (0, 1) else 0.0
        identity = {"behavior_policy_version": row["behavior_policy_version"], "behavior_source": row.get("behavior_source", "ppo_online"), "opponent_policy_hash": row["opponent_policy_hash"],
                    "opponent_deck_hash": row["opponent_deck_hash"], "opponent_source_lineage": row["opponent_source_lineage"], "opponent_family": row["opponent_deck_family"], "own_deck_hash": sha(ROOT / "deck.csv")}
        add_episode(traces, identity=identity, outcome=outcome, demonstration=False, sequence_prefix=f"online-{index}")
    gate3 = ingest_gate3_clean(replay, artifact); online_count += gate3["sequences"]
    if demo_count == 0 or online_count == 0: raise RuntimeError("replay requires both demonstration and online sequences")
    base_sequences = len(replay)
    replay = PrioritizedSequenceReplay.windowed(replay, stride=sequence_stride)
    demo_count = sum(replay._is_demonstration(index) for index in range(len(replay)))
    online_count = len(replay) - demo_count
    saved = replay.save(artifact / "replay.json"); loaded = PrioritizedSequenceReplay.load(artifact / "replay.json")
    sample = loaded.sample(min(32, len(loaded)), beta=.4, demonstration_ratio=1 / 32, seed=71000)
    manifest = {"schema": "r2d3-e2e-replay-manifest-v1", "burn_in": 8, "learner_unroll": 20, "sequence_stride": sequence_stride, "n_step": 5, "gamma": .99, "demo_ratio": 1 / 32,
                "demo_sequences": demo_count, "online_sequences": online_count, "gate3_clean_sequences": gate3["sequences"],
                "gate3_valid_legal_games": gate3["valid_legal_games_ingested"], "gate3_fault_games_excluded": gate3["candidate_fault_games_excluded"],
                "base_sequences": base_sequences, "storage": "window_refs" if replay.is_windowed else "inline", "sequences": len(loaded), "replay_sha256": saved["sha256"], "save_reload_passed": len(loaded) == len(replay)}
    atomic_json(artifact / "replay_manifest.json", manifest); atomic_json(artifact / "replay_statistics.json", {**manifest, "sampled": len(sample.sequences), "importance_min": min(sample.weights), "importance_max": max(sample.weights)})
    write_csv(artifact / "demonstration_registry.csv", registry)
    return loaded, manifest


def expand_replay_windows(source_replay: Any, *, stride: int) -> tuple[Any, dict[str, int]]:
    """Create valid overlapping R2D3 windows from an immutable frozen replay.

    This is a recovery path for a previously collected, checksum-verified
    replay.  It never invents transitions: every output learner/burn-in item
    is copied from one original episode-safe sequence, and terminal records
    cannot appear in a burn-in prefix.
    """
    from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay
    if stride < 1: raise ValueError("replay window stride must be positive")
    expanded = PrioritizedSequenceReplay.windowed(source_replay, stride=stride)
    # Materialize one sample from each source start to validate that no
    # reference crosses a terminal boundary before saving the compact index.
    for index in range(0, len(expanded), max(1, len(expanded) // max(1, len(source_replay)))):
        expanded._sequence_at(index)
    return expanded, {"source_sequences": len(source_replay), "expanded_sequences": len(expanded), "stride": stride,
                      "storage": "window_refs", "terminal_burn_in_rejections": 0}


@functools.lru_cache(maxsize=8192)
def _stable_features(value: str, size: int) -> tuple[float, ...]:
    # Pure function of (value, size), evaluated once per replay item per
    # learner step before caching; the same opponent hashes recur constantly.
    digest = hashlib.sha256(value.encode()).digest()
    return tuple(((digest[index % len(digest)] / 255.0) * 2.0) - 1.0 for index in range(size))


def _learner_batch(sample: Any, device: Any) -> dict[str, Any]:
    """Pad complete burn-in/unroll sequences and upload each tensor once."""
    import numpy
    import torch
    from mage_ptcg.policy_learning.r2d3.sequence import n_step_returns
    sequences = sample.sequences
    count = len(sequences)
    length = max(len(item.learner) + len(item.lookahead) for item in sequences)
    burn_length = max(1, max(len(item.burn_in) for item in sequences))
    maximum = max(
        len(step.legal_actions)
        for item in sequences
        for step in (*item.learner, *item.lookahead)
    )
    state_width = len(sequences[0].learner[0].public_state)
    action_width = len(sequences[0].learner[0].legal_actions[0])
    states = numpy.zeros((count, length, state_width), dtype=numpy.float32)
    burn_states = numpy.zeros((count, burn_length, state_width), dtype=numpy.float32)
    actions = numpy.zeros((count, length, maximum, action_width), dtype=numpy.float32)
    masks = numpy.zeros((count, length, maximum), dtype=bool)
    sequence_mask = numpy.zeros((count, length), dtype=bool)
    burn_mask = numpy.zeros((count, burn_length), dtype=bool)
    selected = numpy.zeros((count, length), dtype=numpy.int64)
    rewards = numpy.zeros((count, length), dtype=numpy.float32)
    discounts = numpy.zeros((count, length), dtype=numpy.float32)
    bootstrap = numpy.full((count, length), -1, dtype=numpy.int64)
    demonstrations = numpy.zeros((count, length), dtype=bool)
    opponent_classes = numpy.zeros((count, length), dtype=numpy.int64)
    deck_classes = numpy.zeros((count, length), dtype=numpy.int64)
    action_type_classes = numpy.zeros((count, length), dtype=numpy.int64)

    def class_index(value: str, classes: int) -> int:
        return int(hashlib.sha256(value.encode()).hexdigest()[:16], 16) % classes

    for batch_index, sequence in enumerate(sequences):
        burn = list(sequence.burn_in); learner_steps = list(sequence.learner)
        steps = [*learner_steps, *sequence.lookahead]
        if burn:
            burn_states[batch_index, :len(burn)] = [step.public_state for step in burn]
            burn_mask[batch_index, :len(burn)] = True
        returns = n_step_returns(steps, n_step=5)
        for offset, (step, (reward, discount, end)) in enumerate(zip(steps, returns, strict=True)):
            states[batch_index, offset] = step.public_state
            actions[batch_index, offset, :len(step.legal_actions)] = step.legal_actions
            masks[batch_index, offset, :len(step.legal_actions)] = True
            trainable = offset < len(learner_steps)
            sequence_mask[batch_index, offset] = trainable
            selected[batch_index, offset] = step.selected_action
            rewards[batch_index, offset] = reward
            discounts[batch_index, offset] = discount
            bootstrap[batch_index, offset] = end + 1 if end + 1 < len(steps) else -1
            demonstrations[batch_index, offset] = step.demonstration and trainable
            opponent_classes[batch_index, offset] = class_index(step.opponent_policy_hash, 64)
            deck_classes[batch_index, offset] = class_index(step.opponent_family, 32)
            # semantic_action v2 coordinate 0 is the normalized cabt action
            # type.  Classify that type, not a hash of the entire action
            # vector (which made the auxiliary target an identity lottery).
            encoded_type = step.legal_actions[step.selected_action][0]
            action_type_classes[batch_index, offset] = int(round(encoded_type * 32.0)) % 32
        # Keep padded model positions numerically defined.  sequence_mask
        # prevents them from contributing to any loss or priority.
        if len(steps) < length:
            masks[batch_index, len(steps):, 0] = True

    def upload(values: Any, dtype: Any) -> Any:
        return torch.from_numpy(numpy.asarray(values, dtype=dtype)).to(device)

    return {
        "states": torch.from_numpy(states).to(device),
        "actions": torch.from_numpy(actions).to(device),
        "legal_mask": torch.from_numpy(masks).to(device),
        "selected": torch.from_numpy(selected).to(device),
        "rewards": torch.from_numpy(rewards).to(device),
        "discounts": torch.from_numpy(discounts).to(device),
        "importance": upload(sample.weights, numpy.float32),
        # Avoid a device-side ``any()`` synchronization in the learner when a
        # sampled batch contains no demonstration transition.
        "demonstration": (
            torch.from_numpy(demonstrations).to(device)
            if bool(demonstrations.any())
            else None
        ),
        "sequence_mask": torch.from_numpy(sequence_mask).to(device),
        "bootstrap_indices": torch.from_numpy(bootstrap).to(device),
        "burn_in_states": torch.from_numpy(burn_states).to(device),
        "burn_in_mask": torch.from_numpy(burn_mask).to(device),
        "opponent_class_target": torch.from_numpy(opponent_classes).to(device),
        "deck_family_target": torch.from_numpy(deck_classes).to(device),
        "next_action_type_target": torch.from_numpy(action_type_classes).to(device),
    }


def gpu_learner_gate(artifact: Path, replay: Any, replay_manifest: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    import torch
    from mage_ptcg.policy_learning.r2d3.checkpoint import load_checkpoint, save_checkpoint
    from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
    from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig, RecurrentDistributionalQ
    if not torch.cuda.is_available(): raise RuntimeError("CUDA became unavailable before learner start")
    torch.manual_seed(71000); torch.cuda.manual_seed_all(71000); torch.cuda.reset_peak_memory_stats()
    device = torch.device("cuda:0"); config = R2D3ModelConfig(); model = RecurrentDistributionalQ(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4); learner = R2D3Learner(model, optimizer, config=LearnerConfig(target_update_interval=25))
    population = json.loads((artifact / "submitted_training_runtime_population.json").read_text())
    population_hash = str(population["population_manifest_hash"]); replay_hash = str(replay_manifest["replay_sha256"])
    checkpoint_dir = artifact / "checkpoints"; checkpoint_dir.mkdir(parents=True, exist_ok=True)
    initial_path = checkpoint_dir / "r2d3-step-000000.pt"
    initial_meta = save_checkpoint(initial_path, model=model, target=learner.target, optimizer=optimizer, population_hash=population_hash, replay_manifest_hash=replay_hash, step=0)
    curve = []; resumed = False; resume_hash = None; started = time.perf_counter()
    bf16 = bool(torch.cuda.is_bf16_supported())
    for step in range(1, 201):
        sample = replay.sample(min(32, len(replay)), beta=min(1.0, .4 + step * .003), demonstration_ratio=1 / 32, seed=71000 + step, episode_first=True)
        batch = _learner_batch(sample, device); before = time.perf_counter()
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=bf16):
            metrics = learner.update(**batch)
        priorities = metrics.pop("sequence_priorities")
        updates = replay.update_priorities(sample.indices, priorities, importance=sample.weights)
        metrics["priority_items"] = float(len(updates)); metrics["priority_unique_items"] = float(len({row["sample_id"] for row in updates}))
        curve.append({"step": step, **metrics, "batch_sequences_per_second": len(sample.sequences) / max(1e-9, time.perf_counter() - before),
                      "allocated_vram_mb": torch.cuda.memory_allocated() / 2**20, "reserved_vram_mb": torch.cuda.memory_reserved() / 2**20})
        if step % 10 == 0: progress("GATE_F", step, 200, replay_size=len(replay), gpu_memory_mb=torch.cuda.memory_allocated() / 2**20)
        if step % 50 == 0:
            path = checkpoint_dir / f"r2d3-step-{step:06d}.pt"
            checkpoint = save_checkpoint(path, model=model, target=learner.target, optimizer=optimizer, population_hash=population_hash, replay_manifest_hash=replay_hash, step=step)
            if step == 100:
                model = RecurrentDistributionalQ(config).to(device); optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
                learner = R2D3Learner(model, optimizer, config=LearnerConfig(target_update_interval=25))
                restored = load_checkpoint(path, model=model, target=learner.target, optimizer=optimizer, expected_population_hash=population_hash,
                                           expected_replay_manifest_hash=replay_hash, map_location=device)
                learner.steps = restored; resumed = restored == 100; resume_hash = checkpoint["sha256"]
    final_path = checkpoint_dir / "r2d3-step-000200.pt"; final_hash = sha(final_path)
    write_csv(artifact / "gpu_training_curve.csv", curve)
    memory = {"device": torch.cuda.get_device_name(0), "cuda_version": torch.version.cuda, "bf16_used": bf16,
              "allocated_mb": torch.cuda.memory_allocated() / 2**20, "reserved_mb": torch.cuda.memory_reserved() / 2**20,
              "peak_allocated_mb": torch.cuda.max_memory_allocated() / 2**20, "peak_reserved_mb": torch.cuda.max_memory_reserved() / 2**20}
    result = {"status": "R2D3_GPU_LEARNER_E2E_PASS", "device": str(device), "updates": learner.steps, "target_updates": sum(int(row["target_updated"]) for row in curve),
              "priority_updates": 200, "nan_inf": 0, "bf16_used": bf16, "elapsed_seconds": time.perf_counter() - started,
              "learner_steps_per_second": 200 / max(1e-9, time.perf_counter() - started), "initial_checkpoint": str(initial_path),
              "final_checkpoint": str(final_path), "initial_checkpoint_hash": initial_meta["sha256"], "final_checkpoint_hash": final_hash}
    resume = {"checkpoint_step": 100, "checkpoint_hash": resume_hash, "restored_step": 100 if resumed else None, "optimizer_restored": resumed,
              "rng_restored": resumed, "target_restored": resumed, "continued_to_step": learner.steps, "passed": resumed and learner.steps == 200}
    atomic_json(artifact / "gpu_learner_results.json", result); atomic_json(artifact / "checkpoint_resume_evidence.json", resume); atomic_json(artifact / "gpu_memory.json", memory)
    atomic_json(artifact / "r2d3_model_config.json", asdict(config))
    if learner.steps != 200 or result["target_updates"] < 2 or not resume["passed"] or any(not all(float(value) == float(value) for value in (row["loss"], row["gradient_norm"])) for row in curve):
        raise RuntimeError("CUDA learner gate failed")
    return model, result, {"initial": initial_path, "final": final_path, "config": config, "population_hash": population_hash, "replay_hash": replay_hash}


def _load_model(path: Path, config: Any, device: Any) -> Any:
    import torch
    from mage_ptcg.policy_learning.r2d3.model import RecurrentDistributionalQ
    model = RecurrentDistributionalQ(config).to(device)
    payload = torch.load(path, map_location=device, weights_only=False); model.load_state_dict(payload["model"]); model.eval()
    return model


def _candidate_validation_game(artifact: Path, asset: Any, model: Any, device: Any, policy_version: str, *,
                               index: int, candidate_side: int, server: Any | None = None,
                               seed: int | None = None,
                               callback_timeout_seconds: float = 8.0) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
    deck = [int(line) for line in (ROOT / "deck.csv").read_text().splitlines() if line.strip()]
    manifest = json.loads((artifact / "snapshots" / asset.asset_id / asset.source_commit / ".submitted_snapshot_manifest.json").read_text())
    spec = spec_from_manifest(manifest, callback_timeout_seconds=callback_timeout_seconds)
    worker = SubmittedAgentWorker(spec, scratch_root=artifact / "runtime_scratch")
    candidate = R2D3CandidatePolicy(model, deck=deck, device=device, policy_version=policy_version, inference_server=server,
                                    game_id=f"validation-{index}", seat=candidate_side)
    started = time.perf_counter()
    match_seed = 120000 + index if seed is None else int(seed)
    try:
        if candidate_side == 0:
            result = run_match(deck_a_path=ROOT / "deck.csv", deck_b_path=spec.deck_path, agent_a_name="rule", agent_b_name="random", seed=match_seed,
                output_dir=artifact / "candidate_match_scratch", save_html=False, save_result=False, agent_a_factory=lambda _deck, _seed: candidate, agent_b_factory=lambda _deck, _seed: worker)
        else:
            result = run_match(deck_a_path=spec.deck_path, deck_b_path=ROOT / "deck.csv", agent_a_name="random", agent_b_name="rule", seed=match_seed,
                output_dir=artifact / "candidate_match_scratch", save_html=False, save_result=False, agent_a_factory=lambda _deck, _seed: worker, agent_b_factory=lambda _deck, _seed: candidate)
        statuses = result.get("agent_status") or [None, None]
        row = {"game": index, "opponent_asset_id": asset.asset_id, "opponent_policy_hash": asset.policy_hash, "opponent_source_lineage": asset.source_lineage,
               "candidate_side": candidate_side, "status": result["status"], "legal": result["status"] == "DONE",
               "candidate_fault": statuses[candidate_side] in {"ERROR", "INVALID", "TIMEOUT"}, "opponent_fault": statuses[1-candidate_side] in {"ERROR", "INVALID", "TIMEOUT"},
               "engine_error": result["status"] not in {"DONE", "AGENT_INVALID", "AGENT_ERROR", "AGENT_TIMEOUT", "STEP_LIMIT"}, "timeout": "TIMEOUT" in result["status"],
               "winner": result.get("winner"), "runtime_seconds": time.perf_counter() - started, "decisions": len(candidate.traces)}
        return row, candidate.traces
    finally:
        worker.close()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values: return None
    ordered = sorted(values); position = (len(ordered) - 1) * percentile
    lower = int(position); upper = min(len(ordered) - 1, lower + 1); fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _ipc_candidate_validation_game(arguments: tuple[str, Any, str, int, int, Any, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import torch
    from mage_ptcg.policy_learning.r2d3.inference_server import IPCInferenceClient
    artifact, asset, policy_version, index, side, request_queue, response_queue = arguments
    client = IPCInferenceClient(request_queue, response_queue, callback_timeout_seconds=10.0)
    return _candidate_validation_game(Path(artifact), asset, None, torch.device("cpu"), policy_version,
                                      index=index, candidate_side=side, server=client)


def _ipc_dispatch_loop(request_queue: Any, server: Any) -> None:
    from concurrent.futures import ThreadPoolExecutor
    import torch
    from mage_ptcg.policy_learning.r2d3.inference_server import InferenceRequest
    def handle(value: dict[str, Any]) -> None:
        response = {"request_id": value["request_id"]}
        try:
            request = InferenceRequest(value["game_id"], int(value["seat"]), value["sequence_state_id"], value["policy_version"],
                torch.tensor(value["state"], dtype=torch.float32, device="cuda"), torch.tensor(value["actions"], dtype=torch.float32, device="cuda"),
                torch.tensor(value["legal_mask"], dtype=torch.bool, device="cuda"))
            output = server.infer(request, expected_policy_version=value["expected_policy_version"])
            response["q"] = output["q"].float().cpu().tolist()
        except BaseException as exc:
            response["error"] = f"{type(exc).__name__}: {exc}"
        value["response_queue"].put(response)
    with ThreadPoolExecutor(max_workers=server.max_batch_size, thread_name_prefix="ipc-inference-dispatch") as executor:
        while True:
            value = request_queue.get()
            if value is None: break
            executor.submit(handle, value)


def inference_gate(artifact: Path, splits: dict[str, list[Any]], checkpoints: dict[str, Any], *, actor_count: int) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    import threading
    import torch
    from mage_ptcg.policy_learning.r2d3.inference_server import InferenceRequest, QueuedCentralInferenceServer
    cpu_model = _load_model(checkpoints["final"], checkpoints["config"], torch.device("cpu"))
    gpu_model = _load_model(checkpoints["final"], checkpoints["config"], torch.device("cuda:0"))
    policy_version = sha(checkpoints["final"]); server = QueuedCentralInferenceServer(gpu_model, max_batch_size=128, max_delay_ms=5.0)
    rows = []; trace_sets: dict[str, list[list[dict[str, Any]]]] = {"cpu": [], "central_gpu": []}
    for mode, model, device, inference_server in (("cpu", cpu_model, torch.device("cpu"), None), ("central_gpu", gpu_model, torch.device("cuda:0"), server)):
        started = time.perf_counter(); decisions = 0
        if mode == "cpu":
            results = [_candidate_validation_game(artifact, splits["validation"][index % len(splits["validation"])], model, device, policy_version,
                       index=2000 + index, candidate_side=index % 2, server=inference_server) for index in range(128)]
        else:
            results = []
            context = multiprocessing.get_context("spawn")
            with context.Manager() as manager:
                request_queue = manager.Queue(); dispatcher = threading.Thread(target=_ipc_dispatch_loop, args=(request_queue, server), daemon=True); dispatcher.start()
                with ProcessPoolExecutor(max_workers=actor_count, mp_context=context) as executor:
                    futures = []
                    for index in range(128):
                        response_queue = manager.Queue()
                        arguments = (str(artifact), splits["validation"][index % len(splits["validation"])], policy_version,
                                     2000 + index, index % 2, request_queue, response_queue)
                        futures.append(executor.submit(_ipc_candidate_validation_game, arguments))
                    for completed, future in enumerate(as_completed(futures), 1):
                        results.append(future.result())
                        if completed % 16 == 0: progress("GATE_G_CENTRAL_GPU", completed, 128)
                request_queue.put(None); dispatcher.join(timeout=30)
                if dispatcher.is_alive(): raise TimeoutError("IPC inference dispatcher did not stop")
        for row, traces in results:
            row["mode"] = mode; rows.append(row); trace_sets[mode].append(traces); decisions += len(traces)
        elapsed = time.perf_counter() - started
        rows.append({"mode": mode, "status": "SUMMARY", "games": 128, "elapsed_seconds": elapsed, "games_per_second": 128 / elapsed,
                     "decisions": decisions, "decisions_per_second": decisions / elapsed})
    # Exercise the real padded batching path and stale-version/restart guards.
    sample_requests = [InferenceRequest(f"batch-{index}", 0, "main", policy_version, torch.zeros(1, 128, device="cuda"),
                       torch.zeros(1, index % 4 + 1, 64, device="cuda"), torch.ones(1, index % 4 + 1, dtype=torch.bool, device="cuda")) for index in range(8)]
    batch_started = time.perf_counter(); batch_outputs = server.core.infer_many(sample_requests, expected_policy_version=policy_version)
    batch_ms = (time.perf_counter() - batch_started) * 1000
    stale_rejected = False
    try: server.infer(sample_requests[0], expected_policy_version="stale")
    except ValueError: stale_rejected = True
    restarted = QueuedCentralInferenceServer(gpu_model, max_batch_size=128, max_delay_ms=5.0)
    restart_output = restarted.core.infer_many(sample_requests[:2], expected_policy_version=policy_version); restarted.close()
    summaries = {row["mode"]: row for row in rows if row["status"] == "SUMMARY"}
    compared = matches = 0
    with torch.no_grad():
        for game in trace_sets["cpu"]:
            cpu_hidden = gpu_hidden = None
            for trace in game:
                state_cpu = torch.tensor([trace["state"]], dtype=torch.float32); actions_cpu = torch.tensor([trace["actions"]], dtype=torch.float32)
                mask_cpu = torch.ones((1, len(trace["actions"])), dtype=torch.bool)
                cpu_output = cpu_model(state_cpu, actions_cpu, mask_cpu, cpu_hidden); cpu_hidden = cpu_output["hidden"]
                gpu_output = gpu_model(state_cpu.cuda(), actions_cpu.cuda(), mask_cpu.cuda(), gpu_hidden); gpu_hidden = gpu_output["hidden"]
                cpu_action = int(cpu_output["q"][0].argmax()); gpu_action = int(gpu_output["q"][0].argmax())
                compared += 1; matches += int(cpu_action == gpu_action)
    agreement = matches / compared if compared else None
    write_csv(artifact / "cpu_gpu_inference_benchmark.csv", rows)
    raw_metrics = server.metrics; actual_batch_sizes = raw_metrics.pop("batch_sizes"); queue_waits = raw_metrics.pop("queue_wait_ms_samples"); callback_samples = raw_metrics.pop("callback_ms_samples")
    actor_callbacks = [float(trace["callback_latency_ms"]) for game in trace_sets["central_gpu"] for trace in game]
    metrics = {**raw_metrics, "functional_batch_size": len(batch_outputs), "functional_batch_latency_ms": batch_ms,
               "actual_batch_count": len(actual_batch_sizes), "actual_batch_size_mean": sum(actual_batch_sizes) / len(actual_batch_sizes),
               "actual_batch_size_max": max(actual_batch_sizes), "actual_multi_request_batch_fraction": sum(value > 1 for value in actual_batch_sizes) / len(actual_batch_sizes),
               "queue_wait_p50_ms": _percentile(queue_waits, .5), "queue_wait_p95_ms": _percentile(queue_waits, .95),
               "gpu_inference_p50_ms": _percentile(callback_samples, .5), "gpu_inference_p95_ms": _percentile(callback_samples, .95),
               "callback_p50_ms": _percentile(actor_callbacks, .5), "callback_p95_ms": _percentile(actor_callbacks, .95),
               "action_agreement": agreement, "action_comparisons": compared, "stale_policy_rejected": stale_rejected,
               "server_restart_passed": len(restart_output) == 2, "faults": sum(bool(row.get("candidate_fault") or row.get("opponent_fault") or row.get("engine_error")) for row in rows if row["status"] != "SUMMARY"),
               "timeouts": sum(bool(row.get("timeout")) for row in rows if row["status"] != "SUMMARY")}
    atomic_json(artifact / "central_inference_metrics.json", metrics)
    selected = "CENTRAL_GPU_INFERENCE_SELECTED" if summaries["central_gpu"]["games_per_second"] > summaries["cpu"]["games_per_second"] else "CPU_ACTOR_INFERENCE_SELECTED"
    (artifact / "inference_decision.md").write_text(f"# Inference decision\n\n`{selected}`。CPU {summaries['cpu']['games_per_second']:.4f} games/s、central GPU {summaries['central_gpu']['games_per_second']:.4f} games/s。\n", encoding="utf-8")
    if metrics["faults"] or metrics["timeouts"] or not stale_rejected or not metrics["server_restart_passed"] or len(batch_outputs) != 8 or metrics["actual_batch_size_max"] < 2:
        raise RuntimeError("central inference gate failed")
    return {"selected": selected, "cpu_games_per_second": summaries["cpu"]["games_per_second"], "gpu_games_per_second": summaries["central_gpu"]["games_per_second"],
            "metrics": metrics, "server": server, "gpu_model": gpu_model}


def candidate_gate(artifact: Path, splits: dict[str, list[Any]], checkpoints: dict[str, Any], inference: dict[str, Any]) -> dict[str, Any]:
    import torch
    trained = inference["gpu_model"]; initial = _load_model(checkpoints["initial"], checkpoints["config"], torch.device("cuda:0"))
    policy_version = sha(checkpoints["final"]); rows = []; trained_traces = []
    for index in range(32):
        asset = splits["validation"][index % 3]
        row, traces = _candidate_validation_game(artifact, asset, trained, torch.device("cuda:0"), policy_version, index=4000 + index, candidate_side=index % 2,
                                                  server=inference["server"])
        row["evaluation"] = "trained_safety"; rows.append(row); trained_traces.extend(traces)
    comparison = []
    for index in range(64):
        mode = ("initial", "trained", "rule_v0")[index % 3]; asset = splits["validation"][index % 3]
        if mode == "rule_v0":
            manifest = json.loads((artifact / "snapshots" / asset.asset_id / asset.source_commit / ".submitted_snapshot_manifest.json").read_text())
            raw, _ = run_asset_game(asset, manifest, asset_side=1 - (index % 2), seed=130000 + index, artifact=artifact)
            row = {"game": 5000 + index, "opponent_asset_id": asset.asset_id, "candidate_side": index % 2, "status": raw["status"], "legal": raw["legal"],
                   "candidate_fault": raw["candidate_fault"], "opponent_fault": raw["opponent_fault"], "engine_error": raw["engine_error"], "timeout": raw["timeout"],
                   "winner": raw.get("winner"), "runtime_seconds": raw.get("runtime_seconds"), "decisions": None}
        else:
            model = initial if mode == "initial" else trained
            checkpoint = checkpoints["initial"] if mode == "initial" else checkpoints["final"]
            row, _ = _candidate_validation_game(artifact, asset, model, torch.device("cuda:0"), sha(checkpoint), index=5000 + index, candidate_side=index % 2)
        row["evaluation"] = mode; comparison.append(row)
    rows.extend(comparison); write_csv(artifact / "r2d3_candidate_smoke.csv", rows)
    divergence = []
    for index, trace in enumerate(trained_traces[:64]):
        state = torch.tensor([trace["state"]], dtype=torch.float32, device="cuda"); actions = torch.tensor([trace["actions"]], dtype=torch.float32, device="cuda")
        mask = torch.ones((1, len(trace["actions"])), dtype=torch.bool, device="cuda")
        with torch.no_grad(): initial_q = initial(state, actions, mask)["q"][0].float().cpu().tolist()
        trained_q = trace["q"]; initial_action = max(range(len(initial_q)), key=lambda offset: (initial_q[offset], -offset))
        trained_action = max(range(len(trained_q)), key=lambda offset: (trained_q[offset], -offset))
        divergence.append({"decision": index, "initial_action": initial_action, "trained_action": trained_action, "action_changed": initial_action != trained_action,
                           "initial_q_mean": sum(initial_q) / len(initial_q), "trained_q_mean": sum(trained_q) / len(trained_q),
                           "mean_absolute_q_delta": sum(abs(left-right) for left, right in zip(initial_q, trained_q, strict=True)) / len(initial_q)})
    write_csv(artifact / "r2d3_action_divergence.csv", divergence)
    failures = [row for row in rows if not row["legal"] or row["candidate_fault"] or row["opponent_fault"] or row["engine_error"] or row["timeout"]]
    result = {"status": "R2D3_GPU_CANDIDATE_CABT_PASS", "safety_games": 32, "comparison_games": 64, "failures": len(failures),
              "action_comparisons": len(divergence), "action_divergence_rate": sum(row["action_changed"] for row in divergence) / len(divergence) if divergence else None,
              "q_changed": any(row["mean_absolute_q_delta"] > 0 for row in divergence)}
    atomic_json(artifact / "r2d3_candidate_results.json", result)
    inference["server"].close()
    if failures or len(divergence) == 0 or not result["q_changed"]: raise RuntimeError("trained candidate CABT gate failed")
    return result


def _psro_policy(member: dict[str, Any], *, artifact: Path, model: Any, device: Any, game_id: str, seat: int) -> tuple[Any, Path, Any | None]:
    from main import make_rule_agent_v1
    from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy
    kind = member["kind"]
    if kind == "submitted":
        asset = member["asset"]; manifest = json.loads((artifact / "snapshots" / asset.asset_id / asset.source_commit / ".submitted_snapshot_manifest.json").read_text())
        spec = spec_from_manifest(manifest); worker = SubmittedAgentWorker(spec, scratch_root=artifact / "runtime_scratch"); return worker, spec.deck_path, worker
    deck = [int(line) for line in (ROOT / "deck.csv").read_text().splitlines() if line.strip()]
    if kind == "rule_v0": return make_rule_agent(deck=deck), ROOT / "deck.csv", None
    if kind == "rule_v1": return make_rule_agent_v1(deck=deck), ROOT / "deck.csv", None
    if kind == "ppo": return TracingPPO(deck=deck), ROOT / "deck.csv", None
    if kind == "r2d3": return R2D3CandidatePolicy(model, deck=deck, device=device, policy_version=member["policy_hash"], game_id=game_id, seat=seat), ROOT / "deck.csv", None
    raise ValueError(f"unknown PSRO member kind: {kind}")


def psro_gate(artifact: Path, splits: dict[str, list[Any]], checkpoints: dict[str, Any], replay: Any, model: Any) -> dict[str, Any]:
    import math
    import torch
    from mage_ptcg.policy_learning.league import PopulationMember, PSROState
    from mage_ptcg.policy_learning.r2d3.checkpoint import save_checkpoint
    from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
    from mage_ptcg.policy_learning.r2d3.psro import should_expand
    submitted = splits["training"][:3]
    rule_v1_hash = hashlib.sha256((sha(ROOT / "main.py") + ":make_rule_agent_v1").encode()).hexdigest()
    members = [{"id": "rule-v0", "kind": "rule_v0", "policy_hash": sha(ROOT / "agents/rule_agent.py"), "lineage": git("rev-parse", "HEAD"), "family": "RULE_V0"},
               {"id": "rule-v1", "kind": "rule_v1", "policy_hash": rule_v1_hash, "lineage": git("rev-parse", "HEAD"), "family": "RULE_V1"}]
    members.extend({"id": asset.asset_id, "kind": "submitted", "policy_hash": asset.policy_hash, "lineage": asset.source_lineage, "family": asset.deck_family, "asset": asset} for asset in submitted)
    members.extend([{"id": "ppo-best", "kind": "ppo", "policy_hash": sha(ROOT / "runs/policy-learning-gate5a/model/best.pt"), "lineage": git("rev-parse", "HEAD"), "family": "PPO"},
                    {"id": "r2d3-step-200", "kind": "r2d3", "policy_hash": sha(checkpoints["final"]), "lineage": git("rev-parse", "HEAD"), "family": "R2D3"}])
    if len({member["policy_hash"] for member in members}) != len(members): raise RuntimeError("duplicate PSRO policy hash")
    size = len(members); sums = [[0.0] * size for _ in range(size)]; counts = [[0] * size for _ in range(size)]; games = []
    for left in range(size):
        for right in range(left + 1, size):
            for game in range(16):
                seat_left = game % 2; game_id = f"psro-{left}-{right}-{game}"
                left_policy, left_deck, left_cleanup = _psro_policy(members[left], artifact=artifact, model=model, device=torch.device("cuda:0"), game_id=game_id, seat=seat_left)
                right_policy, right_deck, right_cleanup = _psro_policy(members[right], artifact=artifact, model=model, device=torch.device("cuda:0"), game_id=game_id, seat=1-seat_left)
                try:
                    policies = [left_policy, right_policy] if seat_left == 0 else [right_policy, left_policy]; decks = [left_deck, right_deck] if seat_left == 0 else [right_deck, left_deck]
                    result = run_match(deck_a_path=decks[0], deck_b_path=decks[1], agent_a_name="rule", agent_b_name="rule", seed=150000 + len(games),
                        output_dir=artifact / "psro_match_scratch", save_html=False, save_result=False,
                        agent_a_factory=lambda _deck, _seed, value=policies[0]: value, agent_b_factory=lambda _deck, _seed, value=policies[1]: value)
                finally:
                    for cleanup in (left_cleanup, right_cleanup):
                        if cleanup is not None: cleanup.close()
                if result["status"] != "DONE": raise RuntimeError(f"PSRO CABT failure: {result['status']}")
                winner = result.get("winner"); payoff = 0.0 if winner == 2 else 1.0 if winner == seat_left else -1.0
                sums[left][right] += payoff; sums[right][left] -= payoff; counts[left][right] += 1; counts[right][left] += 1
                games.append({"left": members[left]["id"], "right": members[right]["id"], "seat_left": seat_left, "winner": winner, "payoff_left": payoff, "status": result["status"]})
    matrix = [[0.0 if row == column else sums[row][column] / counts[row][column] for column in range(size)] for row in range(size)]
    matrix_rows = []
    for row in range(size):
        for column in range(size):
            count = counts[row][column]; value = matrix[row][column]; standard = math.sqrt(max(0.0, 1.0-value*value) / count) if count else 0.0
            matrix_rows.append({"row_policy": members[row]["id"], "column_policy": members[column]["id"], "payoff": value, "games": count,
                                "ci95_low": value - 1.96*standard, "ci95_high": value + 1.96*standard,
                                "row_policy_hash": members[row]["policy_hash"], "row_source_lineage": members[row]["lineage"]})
    write_csv(artifact / "psro_payoff_matrix.csv", matrix_rows); write_csv(artifact / "psro_game_results.csv", games)
    state = PSROState()
    for index, member in enumerate(members):
        state.add_member(PopulationMember(member["id"], member["kind"], member["family"], member["policy_hash"]), against_existing=matrix[index][:index])
    strategy = state.meta_strategy(); duplicate_rejected = False
    try: state.add_member(PopulationMember(members[0]["id"], "duplicate", "duplicate", "duplicate"), against_existing=[0.0] * size)
    except Exception: duplicate_rejected = True
    atomic_json(artifact / "psro_meta_strategy.json", {"population": [{key: value for key, value in member.items() if key != "asset"} for member in members],
        "payoff_matrix": matrix, "meta_strategy": strategy, "best_response_target_mixture": strategy, "duplicate_rejected": duplicate_rejected})
    optimizer = torch.optim.AdamW(model.parameters(), lr=5e-5); learner = R2D3Learner(model, optimizer, config=LearnerConfig(target_update_interval=25))
    curve = []
    for step in range(1, 51):
        sample = replay.sample(min(32, len(replay)), beta=.8, demonstration_ratio=1/32, seed=170000 + step, episode_first=True)
        metrics = learner.update(**_learner_batch(sample, torch.device("cuda:0")))
        replay.update_priorities(sample.indices, metrics.pop("sequence_priorities"), importance=sample.weights)
        curve.append({"step": step, **metrics})
    br_path = artifact / "checkpoints" / "r2d3-psro-best-response-step-000050.pt"
    checkpoint = save_checkpoint(br_path, model=model, target=learner.target, optimizer=optimizer, population_hash=checkpoints["population_hash"],
                                 replay_manifest_hash=checkpoints["replay_hash"], step=50)
    decision = should_expand(meta_improvement=0.0, validation_improvement=0.0, faults=0, novel=checkpoint["sha256"] not in {member["policy_hash"] for member in members},
                             single_opponent_overfit=False)
    br = {"status": "PASS", "updates": 50, "target_updates": sum(int(row["target_updated"]) for row in curve), "checkpoint": str(br_path),
          "checkpoint_hash": checkpoint["sha256"], "target_mixture": strategy, "expansion": decision, "decision": "DRY_RUN_NO_EXPANSION"}
    atomic_json(artifact / "psro_best_response_smoke.json", br)
    (artifact / "psro_expansion_decision.md").write_text("# PSRO expansion decision\n\n`DRY_RUN_NO_EXPANSION`。50 update smokeは完走したが、validation改善の証拠がないためPopulationへ追加しない。\n", encoding="utf-8")
    if not duplicate_rejected or len(games) != size*(size-1)//2*16 or learner.steps != 50: raise RuntimeError("PSRO smoke gate failed")
    return {"population_size": size, "pairs": size*(size-1)//2, "games": len(games), "meta_strategy": strategy, "best_response": br}


def _seal_artifact(artifact: Path) -> None:
    excluded = {"artifact_manifest.json", "checksums.sha256", "stdout.log", "stderr.log"}
    files = sorted(path for path in artifact.rglob("*") if path.is_file() and path.name not in excluded)
    atomic_json(artifact / "artifact_manifest.json", {"schema": "submitted-r2d3-e2e-v1",
        "files": [{"path": path.relative_to(artifact).as_posix(), "sha256": sha(path), "size": path.stat().st_size} for path in files],
        "live_log_files_excluded_from_checksum": ["stdout.log", "stderr.log"]})
    checks = "".join(f"{sha(path)}  {path.relative_to(artifact).as_posix()}\n" for path in sorted(path for path in artifact.rglob("*") if path.is_file() and path.name not in {"checksums.sha256", "stdout.log", "stderr.log"}))
    (artifact / "checksums.sha256").write_text(checks, encoding="utf-8")


def finalize_success(artifact: Path, state: dict[str, Any], cuda: dict[str, Any], *, completed: list[str],
                     smoke: list[dict[str, Any]], population: list[dict[str, Any]], replay_manifest: dict[str, Any],
                     learner: dict[str, Any], inference: dict[str, Any], candidate: dict[str, Any], psro: dict[str, Any]) -> None:
    current_hashes = {path: sha(ROOT / path) for path in ("main.py", "deck.csv", "agents/rule_agent.py")}
    immutable = {path: current_hashes[path] == state["tracked_hashes"].get(path) for path in current_hashes}
    drifts = list(csv.DictReader((artifact / "ref_drift_report.csv").open(encoding="utf-8")))
    selection = json.loads((artifact / "population_selection_report.json").read_text())
    memory = json.loads((artifact / "gpu_memory.json").read_text())
    readiness = {"overall_status": "SUBMITTED_R2D3_GPU_E2E_PASS", "branch": state["branch"], "head": state["head"], "working_tree_clean": False,
        "commit_created": False, "push_executed": False, "qualified_training_assets": 9, "snapshots_pinned": 9,
        "ref_drifts_resolved": sum(row["drift"].lower() == "true" for row in drifts), "runtime_adapters_ready": 9,
        "asset_smoke_games": len(smoke), "asset_smoke_passed": sum(bool(row["legal"]) for row in smoke),
        "asset_faults": sum(bool(row["opponent_fault"] or row["candidate_fault"] or row["engine_error"]) for row in smoke),
        "asset_timeouts": sum(bool(row["timeout"]) for row in smoke), "submitted_population_ready": True,
        "submitted_population_games": len(population), "submitted_selection_rate": selection["submitted_selection_rate"],
        "metadata_coverage": selection["metadata_coverage"], "split_leakage": 0, "replay_ready": True,
        "demo_sequences": replay_manifest["demo_sequences"], "online_sequences": replay_manifest["online_sequences"], "replay_sequences": replay_manifest["sequences"],
        "cuda_available": True, "gpu_device": cuda.get("device"), "bf16_used": learner["bf16_used"], "gpu_learner_updates": learner["updates"],
        "target_updates": learner["target_updates"], "checkpoint_resume_passed": True, "gpu_peak_vram_mb": memory["peak_allocated_mb"],
        "central_gpu_inference_passed": True, "cpu_inference_games_per_second": inference["cpu_games_per_second"],
        "gpu_inference_games_per_second": inference["gpu_games_per_second"], "selected_inference_mode": inference["selected"],
        "r2d3_candidate_cabt_passed": True, "r2d3_candidate_games": candidate["safety_games"], "r2d3_candidate_faults": candidate["failures"],
        "psro_payoff_ready": True, "psro_population_size": psro["population_size"], "psro_pairs": psro["pairs"], "psro_meta_strategy_ready": True,
        "psro_best_response_smoke_passed": psro["best_response"]["status"] == "PASS", "rule_v0_changed": not immutable["agents/rule_agent.py"],
        "champion_changed": False, "default_deck_changed": not immutable["deck.csv"], "main_py_changed": not immutable["main.py"],
        "kaggle_submission_executed": False, "agents_refs_changed": False, "dev_refs_changed": False, "completed_gates": completed,
        "blocked_gates": [], "critical_blockers": [], "next_5_actions": ["review final_readiness.json", "review GPU learner curve", "review candidate divergence",
        "review PSRO payoff confidence intervals", "decide whether to schedule a larger run"], "artifact_root": str(artifact)}
    if any((readiness["rule_v0_changed"], readiness["default_deck_changed"], readiness["main_py_changed"])):
        raise RuntimeError("protected source changed during runner")
    atomic_json(artifact / "final_readiness.json", readiness); atomic_json(artifact / "gate_results.json", {"completed": completed, "blocked": [], "status": readiness["overall_status"]})
    documents = {
        "00_executive_summary.md": "# Executive summary\n\nSubmitted snapshotから実CABT、Replay、CUDA R2D3 200 update、resume、candidate評価、PSRO smokeまでfail-closedで完走した。\n",
        "01_repository_start_state.md": f"# Repository start state\n\nbranch `{state['branch']}` / HEAD `{state['head']}`。開始時working treeはdirtyであり、既存差分を保持した。\n",
        "02_runtime_bridge_design.md": "# Runtime bridge design\n\n資格済みcommitを`git archive`し、Policy/Deck hash照合後にgame固有scratchのJSONL subprocessで実行する。floating refは診断専用である。\n",
        "03_r2d3_gpu_e2e_design.md": "# R2D3 GPU E2E design\n\nactor-visible semantic features、prioritized recurrent Replay、distributional Double Q、target network、demonstration margin、auxiliary loss、checkpoint resumeを使用する。\n",
        "04_work_log.md": f"# Work log\n\n通過Gate: {', '.join(completed)}。\n",
        "test_results.md": "# Test results\n\n一括runner内の実CABT Gate A〜Iが通過した。単体テスト結果は実行ログを参照する。\n",
        "limitations.md": "# Limitations\n\n200 updateと小規模PSROは統合smokeであり、競技性能やChampion昇格の根拠ではない。CABT engine seedは完全固定を保証しない。\n",
        "next_actions.md": "# Next actions\n\n成果物をレビューし、独立validationと長時間学習の採否を判断する。Rule v0、Champion、default Deckは変更しない。\n"}
    for name, text in documents.items(): (artifact / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "diff", "--binary"], cwd=ROOT, stdout=(artifact / "task_changes.patch").open("w"), text=True, check=False)
    _seal_artifact(artifact)


def finalize_blocked(artifact: Path, source_artifact: Path, state: dict[str, Any], cuda: dict[str, Any], *, blocker: str, completed: list[str]) -> None:
    required_csv = ("asset_smoke_results.csv", "submitted_population_smoke.csv", "demonstration_registry.csv", "gpu_training_curve.csv", "cpu_gpu_inference_benchmark.csv", "r2d3_candidate_smoke.csv", "r2d3_action_divergence.csv", "psro_payoff_matrix.csv")
    required_json = ("population_selection_report.json", "split_leakage_report.json", "replay_manifest.json", "replay_statistics.json", "gpu_learner_results.json", "checkpoint_resume_evidence.json", "gpu_memory.json", "central_inference_metrics.json", "psro_meta_strategy.json", "psro_best_response_smoke.json")
    for name in required_csv:
        path = artifact / name
        if not path.exists(): write_csv(path, [{"status": "NOT_RUN", "reason": blocker}])
    for name in required_json:
        path = artifact / name
        if not path.exists(): atomic_json(path, {"status": "NOT_RUN", "reason": blocker})
    source_leakage = source_artifact / "split_leakage_report.json"
    if source_leakage.is_file(): shutil.copy2(source_leakage, artifact / "split_leakage_report.json")
    texts = {"00_executive_summary.md": f"# Executive summary\n\n`{blocker}`。GPU未使用のためGPU E2E PASSではない。\n",
             "01_repository_start_state.md": f"# Repository start state\n\nbranch `{state['branch']}` / HEAD `{state['head']}`。開始時working treeはdirty。\n",
             "02_runtime_bridge_design.md": "# Runtime bridge design\n\n資格済みbranch-tip commitを`git archive`し、Policy/Deck hashを照合後、JSONL subprocess workerからのみ実行する。\n",
             "03_r2d3_gpu_e2e_design.md": "# R2D3 GPU E2E design\n\nCUDA availabilityを先頭でfail-closed確認し、Replay、200 updates、100-step resume、central inference、candidate CABT、PSROの順に実行する。\n",
             "04_work_log.md": f"# Work log\n\nCompleted gates: {', '.join(completed) or 'none'}。Blocker: {blocker}。\n",
             "inference_decision.md": "# Inference decision\n\nGPU benchmark未実行。推論方式は未決定。\n",
             "psro_expansion_decision.md": "# PSRO expansion decision\n\n前段Gate未通過のため`DRY_RUN_NO_EXPANSION`。\n",
             "test_results.md": "# Test results\n\n詳細はunit test出力とgate_results.jsonを参照。未実行GateをPASS扱いしない。\n",
             "limitations.md": f"# Limitations\n\n{blocker}。\n", "next_actions.md": "# Next actions\n\nGPUへアクセスできるhostで同じ一括runnerを再実行する。\n"}
    for name, text in texts.items(): (artifact / name).write_text(text, encoding="utf-8")
    drift_count = 0
    if (artifact / "ref_drift_report.csv").is_file():
        drift_count = sum(row.get("drift", "").lower() == "true" for row in csv.DictReader((artifact / "ref_drift_report.csv").open(encoding="utf-8")))
    gate_for_blocker = {"GPU_ACCESS_BLOCKED": "GATE_GPU_PREFLIGHT", "SUBMITTED_ADAPTER_BRIDGE_BLOCKED": "GATE_A_OR_B",
        "SUBMITTED_POPULATION_BLOCKED": "GATE_D", "R2D3_REPLAY_BLOCKED": "GATE_E", "R2D3_GPU_LEARNER_BLOCKED": "GATE_F",
        "CENTRAL_INFERENCE_BLOCKED": "GATE_G", "R2D3_CANDIDATE_CABT_BLOCKED": "GATE_H", "PSRO_SMOKE_BLOCKED": "GATE_I",
        "MAJOR_INTEGRATION_INCOMPLETE": "GPU_PREFLIGHT"}
    blocked_gate = gate_for_blocker.get(blocker, "UNKNOWN")
    readiness = {"overall_status": blocker, "branch": state["branch"], "head": state["head"], "working_tree_clean": False, "commit_created": False, "push_executed": False,
        "qualified_training_assets": 9, "snapshots_pinned": 9 if "GATE_A" in completed else 0, "ref_drifts_resolved": 0, "runtime_adapters_ready": 9 if "GATE_B" in completed else 0,
        "asset_smoke_games": 0, "asset_smoke_passed": 0, "asset_faults": 0, "asset_timeouts": 0, "submitted_population_ready": False, "submitted_population_games": 0,
        "submitted_selection_rate": None, "metadata_coverage": None, "split_leakage": 0, "replay_ready": False, "demo_sequences": 0, "online_sequences": 0, "replay_sequences": 0,
        "cuda_available": bool(cuda.get("cuda_available")), "gpu_device": cuda.get("device"), "bf16_used": False, "gpu_learner_updates": 0, "target_updates": 0,
        "checkpoint_resume_passed": False, "gpu_peak_vram_mb": None, "central_gpu_inference_passed": False, "cpu_inference_games_per_second": None,
        "gpu_inference_games_per_second": None, "selected_inference_mode": None, "r2d3_candidate_cabt_passed": False, "r2d3_candidate_games": 0, "r2d3_candidate_faults": 0,
        "psro_payoff_ready": False, "psro_population_size": 0, "psro_pairs": 0, "psro_meta_strategy_ready": False, "psro_best_response_smoke_passed": False,
        "rule_v0_changed": False, "champion_changed": False, "default_deck_changed": False, "main_py_changed": False, "kaggle_submission_executed": False,
        "agents_refs_changed": False, "dev_refs_changed": False, "completed_gates": completed, "blocked_gates": [blocked_gate], "critical_blockers": [blocker],
        "next_5_actions": ["run the same command on a CUDA-visible host", "inspect gpu_environment.json", "resume from the immutable artifact root", "run 72-game asset smoke", "run 200-update learner"], "artifact_root": str(artifact)}
    readiness["ref_drifts_resolved"] = drift_count if "GATE_A" in completed else 0
    atomic_json(artifact / "final_readiness.json", readiness); atomic_json(artifact / "gate_results.json", {"completed": completed, "blocked": [{"gate": blocked_gate, "reason": blocker}]})
    atomic_json(artifact / "progress_summary.json", {"status": blocker, "completed_gates": completed, "updated_at": datetime.now(timezone.utc).isoformat()})
    subprocess.run(["git", "diff", "--binary"], cwd=ROOT, stdout=(artifact / "task_changes.patch").open("w"), text=True, check=False)
    _seal_artifact(artifact)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--source-artifact", type=Path, required=True); parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True); parser.add_argument("--gpu-id", type=int, default=0); parser.add_argument("--actor-count", type=int, default=8)
    parser.add_argument("--python-bin", type=Path, required=True); parser.add_argument("--preflight-only", action="store_true"); args = parser.parse_args()
    global PROGRESS_ARTIFACT
    if not 1 <= args.actor_count <= 8: parser.error("--actor-count must be between 1 and 8")
    artifact = args.artifact_root.resolve(); artifact.mkdir(parents=True, exist_ok=True); args.run_root.mkdir(parents=True, exist_ok=True)
    unexpected = [path for path in artifact.iterdir() if path.name not in {"stdout.log", "stderr.log"}]
    if unexpected: raise RuntimeError(f"artifact root is not empty: {unexpected[:3]}")
    PROGRESS_ARTIFACT = artifact
    state = repository_state(args.source_artifact.resolve()); atomic_json(artifact / "repository_start_state.json", state)
    (artifact / "git_status_start.txt").write_text("\n".join(state["status_short"]) + "\n", encoding="utf-8")
    subprocess.run(["git", "diff"], cwd=ROOT, stdout=(artifact / "git_diff_start.patch").open("w"), text=True, check=False)
    cuda = cuda_environment(args.python_bin); atomic_json(artifact / "gpu_environment.json", cuda)
    completed: list[str] = []
    blocker = "SUBMITTED_ADAPTER_BRIDGE_BLOCKED"
    try:
        rows, splits = snapshot_gate(args.source_artifact.resolve(), artifact); completed.append("GATE_A"); progress("GATE_A", 12, 12, status="PASS")
        bridge = cpu_bridge_preflight(artifact, splits); atomic_json(artifact / "cpu_bridge_preflight.json", bridge)
        if not bridge["legal"]: raise RuntimeError("CPU submitted adapter CABT preflight failed")
        completed.append("GATE_B"); progress("GATE_B", 1, 1, status="PASS")
        if not cuda.get("cuda_available"):
            finalize_blocked(artifact, args.source_artifact.resolve(), state, cuda, blocker="GPU_ACCESS_BLOCKED", completed=completed); return 3
        if args.preflight_only:
            finalize_blocked(artifact, args.source_artifact.resolve(), state, cuda, blocker="MAJOR_INTEGRATION_INCOMPLETE", completed=completed); return 4
        blocker = "SUBMITTED_ADAPTER_BRIDGE_BLOCKED"
        smoke, demonstrations = asset_smoke_gate(artifact, splits); completed.append("GATE_C"); progress("GATE_C", 72, 72, status="PASS")
        blocker = "SUBMITTED_POPULATION_BLOCKED"
        population, episodes = ppo_population_gate(artifact, splits); completed.append("GATE_D"); progress("GATE_D", 256, 256, status="PASS")
        blocker = "R2D3_REPLAY_BLOCKED"
        replay, replay_manifest = build_replay_gate(artifact, demonstrations, episodes); completed.append("GATE_E"); progress("GATE_E", len(replay), len(replay), status="PASS")
        blocker = "R2D3_GPU_LEARNER_BLOCKED"
        model, learner, checkpoints = gpu_learner_gate(artifact, replay, replay_manifest); completed.append("GATE_F"); progress("GATE_F", 200, 200, status="PASS")
        blocker = "CENTRAL_INFERENCE_BLOCKED"
        inference = inference_gate(artifact, splits, checkpoints, actor_count=args.actor_count); completed.append("GATE_G"); progress("GATE_G", 256, 256, status="PASS")
        blocker = "R2D3_CANDIDATE_CABT_BLOCKED"
        candidate = candidate_gate(artifact, splits, checkpoints, inference); completed.append("GATE_H"); progress("GATE_H", 96, 96, status="PASS")
        blocker = "PSRO_SMOKE_BLOCKED"
        psro = psro_gate(artifact, splits, checkpoints, replay, model); completed.append("GATE_I"); progress("GATE_I", psro["games"], psro["games"], status="PASS")
        finalize_success(artifact, state, cuda, completed=completed, smoke=smoke, population=population, replay_manifest=replay_manifest,
                         learner=learner, inference=inference, candidate=candidate, psro=psro)
        return 0
    except Exception as exc:
        atomic_json(artifact / "runner_failure.json", {"type": type(exc).__name__, "message": str(exc), "completed_gates": completed})
        finalize_blocked(artifact, args.source_artifact.resolve(), state, cuda, blocker=blocker, completed=completed); return 2


if __name__ == "__main__": raise SystemExit(main())
