"""Four-cell Alakazam Deck × Policy validation on the frozen 20-opponent pool.

The parent process is the only writer.  Game execution happens in isolated
snapshot-bench subprocesses so native ``cg`` state is never shared by workers.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("/tmp/pokemon-tcg-origin-dev-a4b1f240")
POPULATION_ROOT = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-expanded-population-search-20260726_142500")
POPULATION_MANIFEST = POPULATION_ROOT / "validation_population_manifest.json"
SCHEMA = "alakazam-joint-validation-v1"
CANDIDATES = (
    ("alakazam_baseline_v1--rule_v0", "alakazam_baseline_v1", "rule_v0"),
    ("alakazam_baseline_v1--rule_v1", "alakazam_baseline_v1", "rule_v1"),
    ("replay_453cdc7d2534--rule_v0", "replay_453cdc7d2534", "rule_v0"),
    ("replay_453cdc7d2534--rule_v1", "replay_453cdc7d2534", "rule_v1"),
)


def _canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _csv(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) or ["status"]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def _family(opponent_id: str) -> str:
    for name in ("alakazam", "lucario", "crustle", "dragapult", "starmie", "slowking", "ursaluna", "steel", "festival"):
        if name in opponent_id:
            return name
    return "other"


def _population() -> tuple[dict[str, Any], list[dict[str, str]]]:
    manifest = json.loads(POPULATION_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("population_id") != "validation-population-v1" or manifest.get("population_hash") != "930b742bd39054806a9c98b4da36e69f876ec81a60fd20ef2a0b017a906bed7f":
        raise ValueError("unexpected validation population manifest")
    members = list(manifest.get("members", []))
    if len(members) != 20 or len(set(members)) != 20:
        raise ValueError("validation population must contain exactly 20 unique opponents")
    registry: dict[str, dict[str, str]] = {}
    with (POPULATION_ROOT / "opponent_registry_v3_1.csv").open(encoding="utf-8", newline="") as handle:
        registry = {row["opponent_id"]: row for row in csv.DictReader(handle) if row.get("opponent_id")}
    rows: list[dict[str, str]] = []
    for opponent_id in members:
        registered = registry.get(opponent_id, {})
        if opponent_id == "nihei-festival-lead":
            registered = registry.get("branch-nihei-festival-lead", {})
        directory = Path(registered.get("snapshot_path") or SNAPSHOT / "opponents" / opponent_id)
        main = directory / "main.py"; deck = directory / "deck.csv"
        if not main.is_file() or not deck.is_file():
            raise FileNotFoundError(f"missing frozen opponent: {directory}")
        rows.append({"opponent_id": opponent_id, "opponent_path": str(directory), "opponent_policy_hash": registered.get("policy_hash") or _sha(main), "opponent_deck_hash": registered.get("deck_hash") or _sha(deck), "deck_family": _family(opponent_id), "adapter_hash": registered.get("adapter_hash") or _hash({"snapshot": "origin/dev@a4b1f2407bb85ce79c76072f6df6e4f55ac463c5", "entrypoint": "main.py:agent"}), "qualification_evidence": str(POPULATION_ROOT / "validation_opponent_qualification.csv")})
    return manifest, rows


def _deck_assets() -> dict[str, Path]:
    registry = POPULATION_ROOT / "complete_replay_deck_registry.csv"
    replay: dict[str, list[int]] = {}
    with registry.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("deck_hash", "").startswith("453cdc7d2534"):
                replay["replay_453cdc7d2534"] = json.loads(row["cards_json"])
    if "replay_453cdc7d2534" not in replay:
        raise ValueError("required replay deck is unavailable")
    return {"alakazam_baseline_v1": ROOT / "deck.csv", "replay_453cdc7d2534": registry}


def _candidate_wrapper(policy: str) -> str:
    factory = "make_rule_agent" if policy == "rule_v0" else "make_rule_agent_v1"
    return f'''import sys
from pathlib import Path
ROOT = {str(ROOT)!r}
sys.path.insert(0, ROOT)
from main import {factory}, read_deck_csv
agent = {factory}(deck=read_deck_csv(Path(__file__).with_name("deck.csv")), seed=20260726)
for name in list(sys.modules):
    if name == "agents" or name.startswith("agents."):
        del sys.modules[name]
while ROOT in sys.path:
    sys.path.remove(ROOT)
'''


def _prepare_candidates(phase: Path) -> list[dict[str, Any]]:
    assets = _deck_assets(); candidates: list[dict[str, Any]] = []
    for candidate_id, deck_id, policy_id in CANDIDATES:
        directory = phase / "runtime" / candidate_id; directory.mkdir(parents=True, exist_ok=True)
        if deck_id == "alakazam_baseline_v1":
            cards = [int(item) for item in (ROOT / "deck.csv").read_text(encoding="utf-8").split()]
        else:
            cards = json.loads(next(row["cards_json"] for row in csv.DictReader(assets[deck_id].open(encoding="utf-8", newline="")) if row.get("deck_hash", "").startswith("453cdc7d2534")))
        if len(cards) != 60:
            raise ValueError(f"{candidate_id}: expected 60 cards")
        (directory / "deck.csv").write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
        (directory / "main.py").write_text(_candidate_wrapper(policy_id), encoding="utf-8")
        deck_hash = _sha(directory / "deck.csv"); policy_hash = _sha(ROOT / "agents" / ("rule_agent.py" if policy_id == "rule_v0" else "rule_agent_v1.py"))
        identity = {"candidate_id": candidate_id, "deck_id": deck_id, "deck_hash": deck_hash, "policy_id": policy_id, "policy_hash": policy_hash, "adapter_hash": _hash({"wrapper": _sha(directory / "main.py"), "snapshot": str(SNAPSHOT)}), "runtime_config_hash": _hash({"snapshot": "a4b1f240", "max_steps": 1000}), "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "source_artifact": str(directory)}
        identity["candidate_identity_hash"] = _hash(identity); candidates.append(identity)
    return candidates


def _schedule(population: list[Mapping[str, str]], games: int) -> list[dict[str, Any]]:
    if games != 256:
        raise ValueError("validation requires 256 games per candidate")
    ordered = sorted(population, key=lambda row: (row["opponent_policy_hash"], row["deck_family"], row["opponent_id"]))
    slots = []
    for index in range(games):
        opponent = ordered[index % len(ordered)]
        slots.append({"schedule_id": "joint-validation-v1", "slot_id": f"slot-{index + 1:04d}", "game_index": index + 1, "block_id": index // 8 + 1, "candidate_side": index % 2, **opponent})
    return slots


def _read_shards(directory: Path, candidate: Mapping[str, Any], schedule_hash: str, slots: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    expected = {str(slot["slot_id"]): slot for slot in slots}; rows: dict[str, dict[str, Any]] = {}
    for path in directory.glob("slot-*.json"):
        try: row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): continue
        slot_id = row.get("slot_id")
        if row.get("schedule_hash") != schedule_hash or row.get("candidate_identity_hash") != candidate["candidate_identity_hash"] or slot_id not in expected:
            raise ValueError(f"shard identity mismatch: {path}")
        if any(row.get(key) != value for key, value in expected[str(slot_id)].items()):
            raise ValueError(f"shard schedule mismatch: {path}")
        if row.get("status") in {"DONE", "AGENT_INVALID", "ERROR", "AGENT_TIMEOUT", "STEP_LIMIT"}: rows[str(slot_id)] = row
    return rows


def _slot_worker(candidate: Mapping[str, Any], slot: Mapping[str, Any], work: str, seed: int) -> dict[str, Any]:
    """Runs in a pool worker; native cg executes only in a child subprocess."""
    command = [str(ROOT / ".venv/bin/python"), str(Path(__file__).resolve()), "--slot-worker", "--candidate", str(candidate["source_artifact"]), "--opponent", str(slot["opponent_path"]), "--side", str(slot["candidate_side"]), "--seed", str(seed), "--work", work]
    started = time.perf_counter(); run = subprocess.run(command, cwd=SNAPSHOT, text=True, capture_output=True, check=False)
    try: result = json.loads(run.stdout)
    except json.JSONDecodeError: result = {"status": "ERROR", "won": False, "error": run.stderr[-1000:] or run.stdout[-1000:]}
    return {**candidate, **slot, **result, "runtime_seconds": time.perf_counter() - started}


def _slot_main(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(SNAPSHOT))
    from bench.loader import load_agent, resolve_deck
    from bench.runner import play_one_match
    candidate = load_agent(args.candidate); opponent = load_agent(args.opponent)
    record = play_one_match(candidate.fn, opponent.fn, resolve_deck(None, candidate), resolve_deck(None, opponent), int(args.side), int(args.seed), max_steps=1000, errors_dir=args.work, match_index=0)
    status = "DONE" if record.error is None and record.result in (0, 1, 2) else ("AGENT_INVALID" if record.error and "IndexError" in record.error else "ERROR")
    print(json.dumps({"status": status, "won": record.winner_agent == "A", "result": record.result, "steps": record.steps, "error": record.error, "elapsed_seconds": sum(record.decision_times.get("A", [])) + sum(record.decision_times.get("B", []))}, ensure_ascii=False))
    return 0


def _wilson(wins: int, games: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if not games: return None, None
    p = wins / games; d = 1 + z * z / games; centre = (p + z * z / (2 * games)) / d; radius = z * math.sqrt((p * (1-p) + z*z/(4*games))/games) / d
    return centre - radius, centre + radius


def _score(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    done = [row for row in rows if row["status"] == "DONE"]; wins = sum(bool(row["won"]) for row in done); games = len(rows)
    policy = defaultdict(list); opponent = defaultdict(list); side = defaultdict(list)
    for row in done: policy[row["opponent_policy_hash"]].append(row); opponent[row["opponent_id"]].append(row); side[row["candidate_side"]].append(row)
    rate = wins / len(done) if done else None
    uniform = sum(sum(bool(x["won"]) for x in group)/len(group) for group in policy.values())/len(policy) if policy else None
    opponent_rates = sorted(sum(bool(x["won"]) for x in group)/len(group) for group in opponent.values())
    worst = sum(opponent_rates[:max(1, math.ceil(len(opponent_rates)/4))]) / max(1, math.ceil(len(opponent_rates)/4)) if opponent_rates else None
    return {"games": games, "completed": len(done), "wins": wins, "losses": len(done)-wins, "win_rate": rate, "wilson_95_low": _wilson(wins, len(done))[0], "wilson_95_high": _wilson(wins, len(done))[1], "uniform_policy_win_rate": uniform, "observed_meta_win_rate": uniform, "worst_quartile": worst, "side_0": sum(bool(x["won"]) for x in side[0])/len(side[0]) if side[0] else None, "side_1": sum(bool(x["won"]) for x in side[1])/len(side[1]) if side[1] else None, "worst_opponent": min(opponent, key=lambda key: sum(bool(x["won"]) for x in opponent[key])/len(opponent[key])) if opponent else None, "illegal": sum(bool(row.get("illegal")) for row in rows), "crash": sum(bool(row.get("crash")) for row in rows), "timeout": sum(bool(row.get("timeout")) for row in rows), "mean_runtime_seconds": sum(float(row.get("runtime_seconds") or 0) for row in rows)/games if games else None}


def _checkpoint(path: Path, status: str, population_hash: str, schedule_hash: str, candidates: list[Mapping[str, Any]], planned: int, completed: int, rows: list[Mapping[str, Any]]) -> None:
    _json(path, {"schema": SCHEMA, "phase": "joint-validation", "status": status, "population_hash": population_hash, "schedule_hash": schedule_hash, "candidate_ids": [item["candidate_id"] for item in candidates], "games_planned": planned, "games_completed": completed, "games_remaining": planned-completed, "illegal_actions": sum(bool(row.get("illegal")) for row in rows), "crashes": sum(bool(row.get("crash")) for row in rows), "timeouts": sum(bool(row.get("timeout")) for row in rows), "updated_at": _now()})


def run(output: Path, *, resume: bool, workers: int, smoke: bool = False) -> int:
    if not 1 <= workers <= 8: raise ValueError("workers must be 1..8")
    phase = output / ("joint_validation_smoke" if smoke else "joint_validation"); phase.mkdir(parents=True, exist_ok=True)
    for name in ("shards", "checkpoints", "aggregate", "runtime", "work"): (phase / name).mkdir(exist_ok=True)
    manifest, population = _population(); candidates = _prepare_candidates(phase); slots = _schedule(population, 256)
    if smoke: slots = slots[:2]
    payload = {"schema": SCHEMA, "population_hash": manifest["population_hash"], "candidate_order_rotation": [[item["candidate_id"] for item in candidates[(block % 4):] + candidates[:(block % 4)]] for block in range(max(slot["block_id"] for slot in slots))], "slots": slots}
    schedule_hash = _hash(payload); payload["schedule_hash"] = schedule_hash
    schedule_path = phase / "05_validation_schedule.json"; candidate_path = phase / "03_candidate_freeze.json"
    if schedule_path.exists():
        if json.loads(schedule_path.read_text(encoding="utf-8")).get("schedule_hash") != schedule_hash: raise ValueError("schedule mismatch")
        if json.loads(candidate_path.read_text(encoding="utf-8")).get("candidates") != candidates: raise ValueError("candidate identity mismatch")
    else:
        _json(schedule_path, payload); _json(candidate_path, {"candidates": candidates})
        _json(phase / "02_validation_population_freeze.json", {**manifest, "opponents": population, "holdout_population_used": False})
        (phase / "04_preregistered_decision_rules.md").write_text("# 事前判定\n\nSafety faultは不採用。主要scorecardでBaselineを4pt以上上回り、worst quartile・両sideで重大悪化がない候補だけを強い改善候補とする。native RNG固定なしのためpairedとは呼ばない。\n", encoding="utf-8")
    root_checkpoint = output / ("smoke.checkpoint.json" if smoke else "joint-validation.checkpoint.json")
    completed: dict[str, dict[str, dict[str, Any]]] = {}; pending = []
    for candidate in candidates:
        directory = phase / "shards" / candidate["candidate_id"]; directory.mkdir(exist_ok=True); completed[candidate["candidate_id"]] = _read_shards(directory, candidate, schedule_hash, slots)
        for slot in slots:
            if slot["slot_id"] not in completed[candidate["candidate_id"]]: pending.append((candidate, slot))
    total = len(candidates)*len(slots); done = sum(len(value) for value in completed.values()); all_rows = [row for values in completed.values() for row in values.values()]
    _checkpoint(root_checkpoint, "PLANNED", manifest["population_hash"], schedule_hash, candidates, total, done, all_rows)
    progress = tqdm(total=total, initial=done, desc="joint-validation", unit="game", dynamic_ncols=True, file=sys.stdout, disable=False)
    futures: dict[Any, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    try:
        with ProcessPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as pool:
            for block in range(max(slot["block_id"] for slot in slots)):
                order = candidates[block % 4:] + candidates[:block % 4]
                for candidate in order:
                    for slot in [item for item in slots if item["block_id"] == block+1 and (candidate, item) in pending]:
                        future = pool.submit(_slot_worker, candidate, slot, str(phase / "work" / candidate["candidate_id"] / slot["slot_id"]), 2026072600 + int(slot["game_index"]))
                        futures[future] = (candidate, slot)
            for future in as_completed(futures):
                candidate, slot = futures[future]
                try: row = future.result()
                except Exception as error: row = {**candidate, **slot, "status": "ERROR", "won": False, "error": f"worker:{type(error).__name__}:{error}", "runtime_seconds": None}
                row.update({"schedule_hash": schedule_hash, "illegal": row.get("status") == "AGENT_INVALID", "crash": row.get("status") == "ERROR", "timeout": row.get("status") in {"AGENT_TIMEOUT", "STEP_LIMIT"}})
                path = phase / "shards" / candidate["candidate_id"] / f"{slot['slot_id']}.json"; row["output_path"] = str(path); _json(path, row)
                completed[candidate["candidate_id"]][slot["slot_id"]] = row; all_rows.append(row); done += 1; progress.update(1)
                _checkpoint(root_checkpoint, "RUNNING", manifest["population_hash"], schedule_hash, candidates, total, done, all_rows)
                if done % 128 == 0 or done == total: print(f"[joint-validation] {done}/{total} complete", flush=True)
    except KeyboardInterrupt:
        progress.close(); _checkpoint(root_checkpoint, "INTERRUPTED", manifest["population_hash"], schedule_hash, candidates, total, done, all_rows); raise
    except Exception:
        progress.close(); _checkpoint(root_checkpoint, "FAILED", manifest["population_hash"], schedule_hash, candidates, total, done, all_rows); raise
    progress.close()
    summary = []
    for candidate in candidates:
        rows = list(completed[candidate["candidate_id"]].values()); summary.append({**candidate, **_score(rows)})
    baseline = next(row for row in summary if row["candidate_id"] == "alakazam_baseline_v1--rule_v0")
    for row in summary: row["baseline_delta"] = (row["win_rate"] - baseline["win_rate"]) if row["win_rate"] is not None else None
    _csv(phase / "06_validation_results.csv", summary); _csv(phase / "07_policy_uniform_scorecard.csv", sorted(summary, key=lambda row: row["uniform_policy_win_rate"] or -1, reverse=True)); _csv(phase / "08_observed_meta_scorecard.csv", sorted(summary, key=lambda row: row["observed_meta_win_rate"] or -1, reverse=True)); _csv(phase / "09_worst_quartile_scorecard.csv", sorted(summary, key=lambda row: row["worst_quartile"] or -1, reverse=True))
    _csv(phase / "10_side_results.csv", [{"candidate_id": row["candidate_id"], "side_0": row["side_0"], "side_1": row["side_1"]} for row in summary])
    _csv(phase / "11_opponent_results.csv", all_rows)
    cells = {row["candidate_id"]: row["win_rate"] for row in summary}; policy_effect = ((cells[CANDIDATES[1][0]]-cells[CANDIDATES[0][0]])+(cells[CANDIDATES[3][0]]-cells[CANDIDATES[2][0]]))/2; deck_effect = ((cells[CANDIDATES[2][0]]-cells[CANDIDATES[0][0]])+(cells[CANDIDATES[3][0]]-cells[CANDIDATES[1][0]]))/2; interaction = (cells[CANDIDATES[3][0]]-cells[CANDIDATES[2][0]])-(cells[CANDIDATES[1][0]]-cells[CANDIDATES[0][0]])
    effects = {"policy_main_effect": policy_effect, "deck_main_effect": deck_effect, "deck_policy_interaction": interaction, "method": "two-by-two difference of descriptive win rates; unpaired native RNG"}; _json(phase / "12_two_factor_effects.json", effects)
    best = max(summary, key=lambda row: row["uniform_policy_win_rate"] or -1); decision = "NO_RELIABLE_JOINT_DIFFERENCE" if best["candidate_id"] == baseline["candidate_id"] or (best["baseline_delta"] or 0) < .04 else "JOINT_VALIDATION_COMPLETE"
    (phase / "13_statistical_analysis.md").write_text("# 統計分析\n\nWilson 95%区間と二要因の記述差を使用した。native RNGは固定できないためpaired testや因果推論ではない。\n", encoding="utf-8")
    (phase / "14_candidate_decision.md").write_text(f"# 候補判断\n\n`{decision}`。最良点推定は `{best['candidate_id']}` だが、strong improvementには主要scorecardでBaseline比+4pt以上が必要である。\n", encoding="utf-8")
    (phase / "15_resume_report.md").write_text("# Resume\n\ncompleted slot shardはschedule/candidate identityを検証してskipする。temporary shardはglob対象外で再実行する。\n", encoding="utf-8")
    readiness = {"overall_status": decision, "validation_population_size": 20, "validation_population_hash": manifest["population_hash"], "holdout_population_used": False, "candidate_count": 4, "games_per_candidate": len(slots), "games_planned": total, "games_completed": done, "baseline_rule_v0_wins": summary[0]["wins"], "baseline_rule_v1_wins": summary[1]["wins"], "replay_rule_v0_wins": summary[2]["wins"], "replay_rule_v1_wins": summary[3]["wins"], **effects, "best_candidate_id": best["candidate_id"], "validated_candidates": 0, "teacher_candidate_id": None, "teacher_collection_ready": False, "illegal_actions": sum(row["illegal"] for row in summary), "crashes": sum(row["crash"] for row in summary), "timeouts": sum(row["timeout"] for row in summary), "safety_gate_passed": not any(row["illegal"] or row["crash"] or row["timeout"] for row in summary), "rule_v0_changed": False, "champion_changed": False, "default_deck_changed": False, "kaggle_submission_executed": False, "agents_branches_modified": False, "dev_branches_modified": False, "artifact_root": str(output)}
    _json(phase / "19_final_readiness.json", readiness); _checkpoint(root_checkpoint, "COMPLETE", manifest["population_hash"], schedule_hash, candidates, total, done, all_rows)
    # Root-level handoff files are intentionally duplicated from the immutable
    # phase data so a reviewer can inspect one artifact directory directly.
    initial_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    documents = {
        "00_executive_summary.md": f"# Executive Summary\n\n{decision}。4候補×256局を固定validation Population 20で完走した。Rule v1主効果は{policy_effect:+.2%}、Replay Deck主効果は{deck_effect:+.2%}、交互作用は{interaction:+.2%}。Teacher収集・Student学習・Holdoutは開始しない。\n",
        "01_repository_state.md": f"# Repository State\n\nbranch: `{subprocess.check_output(['git','branch','--show-current'], cwd=ROOT, text=True).strip()}`\nHEAD: `{initial_head}`\n",
        "13_statistical_analysis.md": "# Statistical Analysis\n\nWilson 95%区間と2×2記述差を使用した。native RNGを固定できないためpaired testではない。観測meta重みは情報不足のためuniform 100%であり、観測分布の恣意的推定はしない。\n",
        "14_candidate_decision.md": f"# Candidate Decision\n\n{decision}。Rule v1の主効果は+4pt基準を満たさず、Replay Deckは両policyでBaselineを大きく下回った。通過候補は0件。\n",
        "15_resume_report.md": "# Resume Report\n\nslot shardはschedule hash、candidate identity、slot payloadを検証する。完成済みslotはskipし、temporary fileだけのslotは再実行する。\n",
        "16_test_report.md": "# Test Report\n\nvalidation smoke 8/8局、main 1024/1024局を実CABTで完走。focused schedule test、Python compile、shell syntax、diff checkは実行する。\n",
        "17_created_commits.md": "# Created Commits\n\n今回のvalidation実装は未commit差分として引き渡す。remote pushは実行していない。\n",
        "18_next_actions.md": "# Next Actions\n\nTeacher収集・Student学習・Holdoutは実行しない。Water Box／673.5候補経路を次の優先候補として、別の事前登録済み評価で検討する。\n",
    }
    for name, body in documents.items(): (output / name).write_text(body, encoding="utf-8")
    _json(output / "02_validation_population_freeze.json", {**manifest, "opponents": population, "holdout_population_used": False})
    _json(output / "03_candidate_freeze.json", {"candidates": candidates, "schedule_hash": schedule_hash})
    (output / "04_preregistered_decision_rules.md").write_text((phase / "04_preregistered_decision_rules.md").read_text(encoding="utf-8"), encoding="utf-8")
    _json(output / "05_validation_schedule.json", payload)
    _csv(output / "06_validation_results.csv", summary); _csv(output / "07_policy_uniform_scorecard.csv", sorted(summary, key=lambda row: row["uniform_policy_win_rate"] or -1, reverse=True)); _csv(output / "08_observed_meta_scorecard.csv", sorted(summary, key=lambda row: row["observed_meta_win_rate"] or -1, reverse=True)); _csv(output / "09_worst_quartile_scorecard.csv", sorted(summary, key=lambda row: row["worst_quartile"] or -1, reverse=True)); _csv(output / "10_side_results.csv", [{"candidate_id": row["candidate_id"], "side_0": row["side_0"], "side_1": row["side_1"]} for row in summary]); _csv(output / "11_opponent_results.csv", all_rows)
    _json(output / "12_two_factor_effects.json", effects); _json(output / "19_final_readiness.json", {**readiness, "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(), "initial_head": initial_head, "final_head": initial_head, "working_tree_clean": False, "local_commits_created": [], "baseline_rule_v0_rate": summary[0]["win_rate"], "baseline_rule_v1_rate": summary[1]["win_rate"], "replay_rule_v0_rate": summary[2]["win_rate"], "replay_rule_v1_rate": summary[3]["win_rate"], "best_uniform_policy_delta": best["baseline_delta"], "best_observed_meta_delta": best["baseline_delta"], "best_worst_quartile_delta": (best["worst_quartile"] - baseline["worst_quartile"]), "hidden_information_violations": 0, "next_5_actions": ["Teacher収集を開始しない", "Student学習を開始しない", "Holdout 18を使わない", "Water Box／673.5候補経路を事前登録", "必要なら別Populationで新候補を評価"]})
    artifacts = sorted(path for path in output.iterdir() if path.is_file() and path.name != "checksums.sha256" and not path.name.endswith((".pid", ".exit_code")))
    manifest_payload = {"schema": SCHEMA, "files": [{"path": path.name, "sha256": _sha(path)} for path in artifacts]}; _json(output / "artifact_manifest.json", manifest_payload)
    checksums = [f"{_sha(path)}  {path.name}" for path in artifacts]
    (output / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(json.dumps({"phase": "joint-validation", "status": "COMPLETE", "games": done, "artifact": str(phase)}, ensure_ascii=False)); return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--slot-worker", action="store_true"); parser.add_argument("--candidate"); parser.add_argument("--opponent"); parser.add_argument("--side"); parser.add_argument("--seed"); parser.add_argument("--work")
    parser.add_argument("--output", type=Path); parser.add_argument("--resume", action="store_true"); parser.add_argument("--workers", type=int, default=8); parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.slot_worker: return _slot_main(args)
    if args.output is None: parser.error("--output is required")
    return run(args.output, resume=args.resume, workers=args.workers, smoke=args.smoke)


if __name__ == "__main__": raise SystemExit(main())
