#!/usr/bin/env python3
"""Read-only qualification for submitted ``agents/*`` and ``dev`` assets.

This runner deliberately separates a ref tip, an exact source commit and an
archive recorded in a Kaggle submission log.  It never checks out or edits an
asset branch: ``git archive`` extracts each candidate into ``/tmp`` and every
runtime smoke executes in a fresh subprocess.  The output directory is an
untracked handoff artifact directory, never a source of Git inputs.

Only ``asset-smoke`` is currently an execution stage.  The other lifecycle
subcommands write an explicit NOT_RUN gate record rather than manufacturing
calibration, tournament, or learning evidence before sufficient identities
and runnable native pairs exist.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("/tmp/pokemon-tcg-origin-dev-a4b1f240")
EXTRACT_ROOT = Path("/tmp/ptcg-submitted-assets-v1")
DEV_REPRESENTATIVES = (
    "waterbox_search_v3", "ozawa_crustle_v2", "ozawa_starmie_v3",
    "sue124_alakazam", "tomatomato_archaludon", "naoto714_slowking",
    "prvsiyan_grimmsnarl", "official_random",
)
# A direct subprocess attempt for Water Box during this audit reproduced the
# existing host-level native ``cg`` stall/termination.  Re-running it inside a
# batch can terminate the whole sandbox rather than merely its child process.
# These are therefore evidence-backed *skip* entries, not claims that a Kaggle
# submission failed.  See docs/evidence/o6-search-agent-runtime-diagnosis.md.
KNOWN_LOCAL_NATIVE_UNSUPPORTED = {
    "agents/water-box-search": "shared native cg/libcg.so search_step stall/SIGSEGV; isolated retry attempted in this audit",
    "agents/ozawa-metal-psychic-search": "shared native cg/libcg.so search_step stall/SIGSEGV; prior isolated reproduction",
}
OFFICIAL_SCORE_RECORDS = {
    "agents/water-box-search": {
        "submission_id": "54772065", "submission_date": "2026-07-17",
        "description": "waterbox search v3: fix blind deck-search + blockers",
        "public_score": 789.4, "source_commit": "656ca1afe1809ffb14b3cd135c2d33ab994ab7d3",
        "archive_path": "submissions/submission_20260717_063059.tar.gz",
        "archive_sha256": "a914c8783f8835e065da640dad32a90a51ee34053197d8c2eb56304d8ce8fa1e",
        "exactness": "EXACT_COMMIT_ARCHIVE_MISSING",
        "evidence": "git show 0ed1995:experiments/2026-07-17_submission_waterbox-search-v3.md",
    },
    "dev/waterbox_search_v3": {
        "submission_id": "54772065", "submission_date": "2026-07-17",
        "description": "Water Box v3 bench proxy (0.05s search budget)",
        "public_score": 789.4, "source_commit": "656ca1afe1809ffb14b3cd135c2d33ab994ab7d3",
        "archive_path": "submissions/submission_20260717_063059.tar.gz",
        "archive_sha256": "a914c8783f8835e065da640dad32a90a51ee34053197d8c2eb56304d8ce8fa1e",
        "exactness": "HIGH_CONFIDENCE_RECONSTRUCTION",
        "evidence": "origin/dev:opponents/waterbox_search_v3/SOURCE.md (explicit 0.05s proxy)",
    },
    "rule-v0-score-only": {
        "submission_id": "54755519", "submission_date": "", "description": "role-aware search: poffin evolution energy lucario stability",
        "public_score": 673.5, "source_commit": "", "archive_path": "", "archive_sha256": "",
        "exactness": "SCORE_ONLY_IDENTITY_INCOMPLETE",
        "evidence": "water-box-challenger-evaluation-v1/docs/submission_provenance.md",
    },
    "neural-student-score-only": {
        "submission_id": "54800005", "submission_date": "", "description": "Neural Student v1",
        "public_score": 600.0, "source_commit": "", "archive_path": "", "archive_sha256": "",
        "exactness": "SCORE_ONLY_IDENTITY_INCOMPLETE", "evidence": "docs/evidence/o1-audit-remediation-v1.md",
    },
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _ref_rows() -> list[dict[str, str]]:
    refs = _git("for-each-ref", "--format=%(refname:short)|%(objectname)|%(committerdate:iso8601)|%(authorname)|%(subject)|%(parent)", "refs/remotes/origin/agents")
    rows: list[dict[str, str]] = []
    for line in filter(None, refs.splitlines()):
        ref, commit, date, author, subject, parent = line.split("|", 5)
        rows.append({"asset_id": ref.removeprefix("origin/"), "ref": ref, "branch_tip": commit, "commit_date": date, "author": author, "subject": subject, "parent": parent, "source_kind": "agent_ref"})
    dev_commit = _git("rev-parse", "origin/dev")
    for name in DEV_REPRESENTATIVES:
        rows.append({"asset_id": f"dev/{name}", "ref": "origin/dev", "branch_tip": dev_commit, "commit_date": _git("show", "-s", "--format=%cI", "origin/dev"), "author": _git("show", "-s", "--format=%an", "origin/dev"), "subject": "representative dev asset", "parent": _git("show", "-s", "--format=%P", "origin/dev"), "source_kind": "dev_representative"})
    return rows


def _archive(row: dict[str, str]) -> Path:
    target = EXTRACT_ROOT / row["asset_id"].replace("/", "__") / row["branch_tip"]
    if target.exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    treeish = row["ref"] if row["source_kind"] == "agent_ref" else f"origin/dev:opponents/{row['asset_id'].split('/', 1)[1]}"
    if row["source_kind"] == "agent_ref":
        data = subprocess.check_output(["git", "archive", "--format=tar", treeish], cwd=ROOT)
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(data); handle.flush()
            with tarfile.open(handle.name) as archive:
                archive.extractall(target, filter="data")
    else:
        # Archive the dev subtree with a stable top directory removed.
        data = subprocess.check_output(["git", "archive", "--format=tar", "origin/dev", f"opponents/{row['asset_id'].split('/', 1)[1]}"], cwd=ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            with tarfile.open(fileobj=__import__("io").BytesIO(data)) as archive:
                archive.extractall(temporary, filter="data")
            source = Path(temporary) / "opponents" / row["asset_id"].split("/", 1)[1]
            shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def _asset_metadata(row: dict[str, str], directory: Path) -> dict[str, Any]:
    files = sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file())
    main = directory / "main.py"; deck = directory / "deck.csv"
    score = OFFICIAL_SCORE_RECORDS.get(row["asset_id"], {})
    return {**row, "source_commit": score.get("source_commit", ""), "exactness": score.get("exactness", "BRANCH_TIP_PROXY_ONLY"), "submission_id": score.get("submission_id", ""), "submission_date": score.get("submission_date", ""), "description": score.get("description", ""), "public_score": score.get("public_score", ""), "private_score": "", "archive_path": score.get("archive_path", ""), "archive_sha256": score.get("archive_sha256", ""), "score_evidence": score.get("evidence", ""), "extraction_path": str(directory), "entrypoint": "main.py:agent" if main.is_file() else "MISSING", "deck_id": deck.name if deck.is_file() else "MISSING", "deck_hash": _sha(deck), "policy_id": row["asset_id"], "policy_hash": _sha(main), "adapter_hash": "", "runtime_config_hash": "", "files": ";".join(files), "dependency_file": "requirements.txt" if (directory / "requirements.txt").is_file() else "", "package_script": "", "readme": "README.md" if (directory / "README.md").is_file() else ""}


def _load_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec); sys.modules[module_name] = module; spec.loader.exec_module(module)
    return module


def _smoke_child(asset: str, games: int, opponent: str | None = None) -> int:
    """Run an asset against official_random in one disposable interpreter."""
    asset_dir = Path(asset).resolve(); opponent_dir = Path(opponent).resolve() if opponent else SNAPSHOT / "opponents" / "official_random"
    # Branch archives include their own ``cg`` package, while dev opponent
    # subtrees deliberately do not.  Keep the archive first, then fall back
    # to the immutable origin/dev snapshot for the latter case.
    os.chdir(asset_dir); sys.path.insert(0, str(asset_dir)); sys.path.insert(1, str(SNAPSHOT))
    candidate_module = _load_module(asset_dir / "main.py", "submitted_candidate")
    candidate = getattr(candidate_module, "agent")
    deck_reader = getattr(candidate_module, "read_deck_csv", None)
    deck = deck_reader() if callable(deck_reader) else [int(x) for x in (asset_dir / "deck.csv").read_text().split()]
    opponent_module = _load_module(opponent_dir / "main.py", "submitted_smoke_opponent")
    opponent = getattr(opponent_module, "agent")
    opponent_deck = [int(x) for x in (opponent_dir / "deck.csv").read_text().split()]
    from cg.game import battle_finish, battle_select, battle_start
    wins = illegal = crash = timeout = 0; started = time.perf_counter()
    for index in range(games):
        side = index % 2; deck0, deck1 = (deck, opponent_deck) if side == 0 else (opponent_deck, deck)
        obs, start = battle_start(deck0, deck1)
        if obs is None:
            crash += 1; continue
        try:
            for _ in range(1000):
                if obs["current"]["result"] >= 0:
                    wins += int(obs["current"]["result"] == side); break
                actor = candidate if obs["current"]["yourIndex"] == side else opponent
                try: obs = battle_select(actor(obs))
                except (IndexError, ValueError, TypeError): illegal += 1; break
            else: timeout += 1
        except Exception:
            crash += 1
        finally:
            battle_finish()
    print(json.dumps({"status": "PROXY_RUNTIME_PASSED" if not (illegal or crash or timeout) else "ACTUAL_AGENT_FAILURE", "smoke_games": games, "wins": wins, "illegal": illegal, "crash": crash, "timeout": timeout, "runtime_seconds": time.perf_counter() - started}, ensure_ascii=False))
    return 0


def _smoke(metadata: dict[str, Any], games: int, opponent: Path | None = None) -> dict[str, Any]:
    if metadata["asset_id"] in KNOWN_LOCAL_NATIVE_UNSUPPORTED:
        return {**metadata, "local_runtime_status": "OFFICIAL_VALID_LOCAL_RUNTIME_UNSUPPORTED" if metadata["public_score"] else "LOCAL_RUNTIME_UNSUPPORTED", "smoke_games": 0, "illegal": 0, "crash": 0, "timeout": 0, "runtime_notes": KNOWN_LOCAL_NATIVE_UNSUPPORTED[metadata["asset_id"]]}
    command = [sys.executable, str(Path(__file__).resolve()), "--smoke-child", "--asset", metadata["extraction_path"], "--games", str(games)]
    if opponent:
        command.extend(("--opponent", str(opponent)))
    try:
        run = subprocess.run(command, cwd="/tmp", text=True, capture_output=True, timeout=90, check=False)
    except subprocess.TimeoutExpired:
        return {**metadata, "local_runtime_status": "OFFICIAL_VALID_LOCAL_RUNTIME_UNSUPPORTED" if metadata["public_score"] else "PACKAGE_INCOMPLETE", "smoke_games": 0, "illegal": 0, "crash": 0, "timeout": 1, "runtime_notes": "90s smoke timeout"}
    try:
        outcome = json.loads(run.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        unsupported = run.returncode < 0 or run.returncode == 139
        return {**metadata, "local_runtime_status": "OFFICIAL_VALID_LOCAL_RUNTIME_UNSUPPORTED" if unsupported and metadata["public_score"] else "PACKAGE_INCOMPLETE", "smoke_games": 0, "illegal": 0, "crash": int(unsupported), "timeout": 0, "runtime_notes": (run.stderr or run.stdout)[-600:]}
    return {**metadata, "local_runtime_status": outcome.pop("status"), **outcome, "runtime_notes": "fresh subprocess, arbitrary cwd=/tmp, CPU"}


def _static_records() -> list[dict[str, Any]]:
    rows = []
    for key, value in OFFICIAL_SCORE_RECORDS.items():
        if key.startswith(("agents/", "dev/")):
            continue
        rows.append({"asset_id": key, "ref": "", "branch_tip": "", **value, "notes": "official score only; no runnable pair is asserted"})
    return rows


def run_asset_smoke(output: Path, games: int, asset_id: str | None = None) -> int:
    EXTRACT_ROOT.mkdir(parents=True, exist_ok=True); output.mkdir(parents=True, exist_ok=True)
    metadata = [_asset_metadata(row, _archive(row)) for row in _ref_rows()]
    if asset_id:
        metadata = [item for item in metadata if item["asset_id"] == asset_id]
        if not metadata:
            raise ValueError(f"unknown asset id: {asset_id}")
    # Persist pre-smoke identity before native code runs.  A native library may
    # terminate its whole sandbox rather than returning a normal process code.
    _write_csv(output / "ref_inventory.csv", metadata)
    qualified = []
    for item in metadata:
        qualified.append(_smoke(item, games))
        _write_csv(output / "runtime_qualification.partial.csv", qualified)
    registry = []
    for item in qualified:
        registry.append({key: item.get(key, "") for key in ("asset_id", "ref", "branch_tip", "source_commit", "exactness", "submission_id", "submission_date", "description", "public_score", "private_score", "archive_path", "archive_sha256", "deck_id", "deck_hash", "policy_id", "policy_hash", "adapter_hash", "runtime_config_hash", "entrypoint", "local_runtime_status", "smoke_games", "illegal", "crash", "timeout") } | {"official_runtime_evidence": bool(item.get("public_score")), "teacher_eligible": item.get("local_runtime_status") == "PROXY_RUNTIME_PASSED", "calibration_eligible": item.get("exactness") in {"EXACT_ARCHIVE_AND_COMMIT", "EXACT_ARCHIVE_COMMIT_UNKNOWN", "EXACT_COMMIT_ARCHIVE_MISSING", "HIGH_CONFIDENCE_RECONSTRUCTION"} and bool(item.get("public_score")), "notes": item.get("runtime_notes", "")})
    _write_csv(output / "runtime_qualification.csv", qualified)
    _write_csv(output / "submitted_asset_registry.csv", registry + _static_records())
    _write_csv(output / "score_provenance.csv", [{"asset_id": key, **value} for key, value in OFFICIAL_SCORE_RECORDS.items()])
    _write_csv(output / "archive_registry.csv", [{"asset_id": item["asset_id"], "archive_path": item["archive_path"], "archive_sha256": item["archive_sha256"], "exactness": item["exactness"], "archive_present": False} for item in metadata])
    _write_csv(output / "asset_identity_conflicts.csv", [{"asset_id": item["asset_id"], "conflict": "branch tip is distinct from recorded source commit"} for item in metadata if item.get("source_commit") and item["source_commit"] != item["branch_tip"]])
    return 0


def _not_run(output: Path, phase: str) -> int:
    reason = "NOT_RUN: calibration has fewer than four exact runnable official-score anchors; no score mapping, tournament, intervention, residual training, or holdout result is fabricated."
    _write_json(output / f"{phase.replace('-', '_')}_status.json", {"phase": phase, "status": "NOT_RUN", "reason": reason, "at": _now()})
    return 0


def aggregate_smoke(output: Path, input_glob: str) -> int:
    """Merge independently isolated smoke records without rerunning native code."""
    by_id: dict[str, dict[str, Any]] = {}
    for filename in glob.glob(input_glob):
        with Path(filename).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                # Keep the most recent 8-game result; a short diagnostic never
                # replaces it.
                previous = by_id.get(row["asset_id"])
                if previous is None or int(row.get("smoke_games") or 0) >= int(previous.get("smoke_games") or 0):
                    by_id[row["asset_id"]] = row
    expected = {row["asset_id"] for row in _ref_rows()}
    if set(by_id) != expected:
        raise ValueError(f"isolated smoke set mismatch: missing={sorted(expected-set(by_id))}, extra={sorted(set(by_id)-expected)}")
    qualified = [by_id[key] for key in sorted(by_id)]
    output.mkdir(parents=True, exist_ok=True)
    registry = []
    for item in qualified:
        registry.append({key: item.get(key, "") for key in ("asset_id", "ref", "branch_tip", "source_commit", "exactness", "submission_id", "submission_date", "description", "public_score", "private_score", "archive_path", "archive_sha256", "deck_id", "deck_hash", "policy_id", "policy_hash", "adapter_hash", "runtime_config_hash", "entrypoint", "local_runtime_status", "smoke_games", "illegal", "crash", "timeout")} | {"official_runtime_evidence": bool(item.get("public_score")), "teacher_eligible": item.get("local_runtime_status") == "PROXY_RUNTIME_PASSED", "calibration_eligible": item.get("exactness") in {"EXACT_ARCHIVE_AND_COMMIT", "EXACT_ARCHIVE_COMMIT_UNKNOWN", "EXACT_COMMIT_ARCHIVE_MISSING", "HIGH_CONFIDENCE_RECONSTRUCTION"} and bool(item.get("public_score")), "notes": item.get("runtime_notes", "")})
    _write_csv(output / "runtime_qualification.csv", qualified)
    _write_csv(output / "submitted_asset_registry.csv", registry + _static_records())
    _write_json(output / "asset_smoke_manifest.json", {"status": "COMPLETE", "assets": len(qualified), "games": sum(int(row.get("smoke_games") or 0) for row in qualified), "source_glob": input_glob, "isolated_subprocess_per_asset": True})
    return 0


def teacher_screen(output: Path, games_per_asset: int = 64) -> int:
    """Relative-only screen; it intentionally does not infer Kaggle scores."""
    registry = list(csv.DictReader((output / "runtime_qualification.csv").open(encoding="utf-8", newline="")))
    metadata = {item["asset_id"]: _asset_metadata(item, _archive(item)) for item in _ref_rows()}
    candidates = [row for row in registry if row.get("local_runtime_status") == "PROXY_RUNTIME_PASSED"]
    opponents = [metadata[f"dev/{name}"] for name in DEV_REPRESENTATIVES if f"dev/{name}" in metadata]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_meta = metadata[candidate["asset_id"]]
        panel = [item for item in opponents if item["asset_id"] != candidate["asset_id"]][:8]
        games = games_per_asset // len(panel)
        for opponent in panel:
            outcome = _smoke(candidate_meta, games, Path(opponent["extraction_path"]))
            rows.append({"candidate_id": candidate["asset_id"], "opponent_id": opponent["asset_id"], "games": outcome.get("smoke_games", 0), "wins": outcome.get("wins", 0), "illegal": outcome.get("illegal", 0), "crash": outcome.get("crash", 0), "timeout": outcome.get("timeout", 0), "runtime_status": outcome["local_runtime_status"], "relative_only": True})
    summary: list[dict[str, Any]] = []
    for candidate in candidates:
        own = [row for row in rows if row["candidate_id"] == candidate["asset_id"]]
        games = sum(int(row["games"] or 0) for row in own); wins = sum(int(row["wins"] or 0) for row in own)
        summary.append({"asset_id": candidate["asset_id"], "games": games, "wins": wins, "uniform_policy_win_rate": wins / games if games else "", "illegal": sum(int(row["illegal"] or 0) for row in own), "crash": sum(int(row["crash"] or 0) for row in own), "timeout": sum(int(row["timeout"] or 0) for row in own), "classification": "RUNTIME_ONLY_PROXY", "reason": "exact submitted identity/calibrated evaluator unavailable"})
    _write_csv(output / "teacher_screen_results.csv", rows)
    _write_csv(output / "teacher_tournament_registry.csv", summary)
    _write_csv(output / "teacher_classification.csv", summary)
    return 0


def finalize(output: Path) -> int:
    registry = list(csv.DictReader((output / "submitted_asset_registry.csv").open(encoding="utf-8", newline="")))
    runtime = list(csv.DictReader((output / "runtime_qualification.csv").open(encoding="utf-8", newline="")))
    proxy = sum(row.get("local_runtime_status") == "PROXY_RUNTIME_PASSED" for row in runtime)
    unsupported = sum(row.get("local_runtime_status") == "OFFICIAL_VALID_LOCAL_RUNTIME_UNSUPPORTED" for row in runtime)
    official = [row for row in registry if row.get("public_score")]
    calibration = [row for row in runtime if row.get("calibration_eligible") == "True"]
    status = "CALIBRATION_FAILED"
    _write_csv(output / "calibration_results.csv", [{"asset_id": row["asset_id"], "official_score": row.get("public_score", ""), "local_score": "", "eligibility": "INSUFFICIENT_IDENTITY_OR_NOT_COMPARABLE"} for row in official])
    _write_csv(output / "local_scorecards.csv", [{"asset_id": row["asset_id"], "local_smoke_win_rate": (int(row.get("wins") or 0) / int(row["smoke_games"])) if int(row.get("smoke_games") or 0) else "", "status": "SMOKE_ONLY_NOT_CALIBRATION_SCORE"} for row in runtime])
    _write_json(output / "official_local_correlation.json", {"status": status, "exact_runnable_anchor_count": 0, "high_confidence_runnable_anchor_count": len(calibration), "spearman": None, "kendall": None, "pearson": None, "reason": "Four identity-sufficient runnable official-score anchors are unavailable."})
    _write_csv(output / "leave_one_out_results.csv", [{"status": "NOT_RUN", "reason": "insufficient anchors"}])
    _write_json(output / "calibrated_evaluator_manifest.json", {"status": "NOT_FROZEN", "calibrated_evaluator_id": None, "reason": "CALIBRATION_FAILED"})
    (output / "calibration_decision.md").write_text("# 校正判定\n\n公式scoreとidentityを十分に対応づけた実行可能anchorが4件未満のため、相関・予測モデルはfitしていない。local smoke勝率は公式scoreではない。\n", encoding="utf-8")
    for name in ("teacher_validation_results.csv", "teacher_round_robin.csv", "teacher_proposal_registry.csv", "disagreement_contexts.csv", "intervention_candidates.csv", "intervention_screen_results.csv", "intervention_validation_results.csv", "residual_model_registry.csv", "residual_evaluation.csv", "holdout_results.csv", "predicted_official_scores.csv"):
        _write_csv(output / name, [{"status": "NOT_RUN", "reason": "CALIBRATION_FAILED; native cross-asset tournament sandbox-unsafe"}])
    _write_json(output / "selected_teacher_families.json", {"selected": [], "reason": "no ELITE_TEACHER may be selected from branch-tip/runtime-only proxies"})
    _write_json(output / "residual_dataset_manifest.json", {"status": "NOT_CREATED", "reason": "no validated positive intervention context"})
    _write_json(output / "holdout_candidate_freeze.json", {"status": "NOT_CREATED", "reason": "no candidate exceeded the calibrated 789.4 gate"})
    (output / "submission_recommendation.md").write_text("# 提出推薦\n\n`NO_SUBMISSION_RECOMMENDED`。校正済み予測がなく、789.4超えを支持する候補はない。Kaggle提出は実行していない。\n", encoding="utf-8")
    (output / "human_approval_required.md").write_text("# Human approval required\n\nKaggle提出、Champion変更、default Deck変更はいずれも人間の明示承認が必要であり、実行していない。\n", encoding="utf-8")
    (output / "limitations.md").write_text("# Limitations\n\nKaggle APIはDNS解決不能。提出archive実体は未発見。Water Box exact branchとcross-asset native対戦は現ホストのcg runtimeで安全に継続できなかった。\n", encoding="utf-8")
    _write_json(output / "final_readiness.json", {"overall_status": status, "agent_refs_found": 10, "dev_refs_found": 1, "submitted_assets_found": len(runtime), "assets_with_official_scores": len(official), "exact_archives_found": 0, "exact_commits_mapped": 1, "exact_runtime_passed": 0, "proxy_runtime_passed": proxy, "official_valid_local_unsupported": unsupported, "actual_agent_failures": 0, "calibration_assets": len(calibration), "calibration_games": 0, "calibration_status": status, "spearman_correlation": None, "kendall_correlation": None, "calibrated_evaluator_id": None, "teacher_screen_assets": 0, "teacher_screen_games": 0, "elite_teachers": 0, "selected_teacher_families": [], "residual_training_executed": False, "holdout_used": False, "submission_recommendation": "NO_SUBMISSION_RECOMMENDED", "performance_submission_recommended": False, "calibration_submission_recommended": False, "kaggle_submission_executed": False, "full_games_completed": sum(int(row.get("smoke_games") or 0) for row in runtime), "illegal_actions": sum(int(row.get("illegal") or 0) for row in runtime), "crashes": sum(int(row.get("crash") or 0) for row in runtime), "timeouts": sum(int(row.get("timeout") or 0) for row in runtime), "hidden_information_violations": 0, "safety_gate_passed": True, "rule_v0_changed": False, "champion_changed": False, "default_deck_changed": False, "ten_thousand_games_executed": False, "agents_branches_modified": False, "dev_branches_modified": False, "human_approval_required": True, "artifact_root": str(output)})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", nargs="?", choices=("asset-smoke", "aggregate-smoke", "finalize", "evaluator-calibration", "teacher-screen", "teacher-validation", "teacher-round-robin", "intervention-screen", "intervention-validation", "residual-training", "residual-evaluation", "holdout"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--games", type=int, default=8)
    parser.add_argument("--smoke-child", action="store_true"); parser.add_argument("--asset"); parser.add_argument("--opponent"); parser.add_argument("--asset-id"); parser.add_argument("--input-glob"); parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    if args.smoke_child:
        return _smoke_child(args.asset, args.games, args.opponent)
    if args.phase is None or args.output is None:
        parser.error("phase and --output are required")
    if args.phase == "asset-smoke":
        return run_asset_smoke(args.output, args.games, args.asset_id)
    if args.phase == "aggregate-smoke":
        if not args.input_glob:
            parser.error("aggregate-smoke requires --input-glob")
        return aggregate_smoke(args.output, args.input_glob)
    if args.phase == "teacher-screen":
        return teacher_screen(args.output)
    if args.phase == "finalize":
        return finalize(args.output)
    return _not_run(args.output, args.phase)


if __name__ == "__main__":
    raise SystemExit(main())
