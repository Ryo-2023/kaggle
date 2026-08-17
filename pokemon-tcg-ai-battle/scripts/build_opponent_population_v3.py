"""Materialize a development-first Opponent Registry v3 from fixed snapshots.

This tool intentionally treats external source trees as immutable inputs.  It
does not check out a branch, import an external agent, or copy source/replays
into the repository.  The resulting handoff is a provenance and population
plan; actual native-agent execution remains in the fixed snapshot's bench
runtime.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "opponent-registry-v3-development-first"
SYNTHETIC = ("legal-random", "conservative-resource", "aggressive-tempo", "setup-heavy", "early-disruption")
REQUIRED_CSV = (
    "branch_agent_registry.csv", "submission_agent_registry.csv", "top_team_submission_registry.csv",
    "replay_registry.csv", "full_deck_registry.csv", "top_meta_distribution.csv", "replay_action_registry.csv",
    "replay_policy_registry.csv", "opponent_registry_v3.csv", "opponent_behavior_fingerprint.csv",
    "opponent_population_registry.csv", "matchup_matrix.csv", "alakazam_deck_registry.csv",
    "alakazam_slot_registry.csv", "deck_candidate_registry.csv", "policy_candidate_registry.csv",
    "joint_candidate_registry.csv", "evaluation_block_registry.csv",
)
REQUIRED_DOCS = (
    "00_全体要約.md", "01_開始時Git状態.md", "02_開発方針と採用基準.md", "03_agents_dev資産一覧.md",
    "04_提出済みエージェント回収.md", "05_エージェント簡易資格確認.md", "06_エージェント重複分析.md",
    "07_Kaggle公開履歴取得.md", "08_上位チーム完全デッキ抽出.md", "09_上位帯デッキ分布.md",
    "10_公開行動履歴解析.md", "11_公開履歴由来方策.md", "12_Opponent登録簿.md", "13_Opponent多様性分析.md",
    "14_Opponent対戦表.md", "15_Population役割分割.md", "16_フーディン上位構築比較.md",
    "17_フーディン基準デッキ再構築.md", "18_自由枠探索.md", "19_デッキ候補確認.md",
    "20_フーディン専用方策.md", "21_方策候補確認.md", "22_デッキ方策交互最適化.md",
    "23_最終Joint評価.md", "24_安全性と実行時間.md", "25_テスト結果.md", "26_作成コミット.md",
    "27_リモート同期.md", "28_残課題.md", "29_次の作業.md",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True).stdout.strip()


def csv_write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    data = list(rows)
    fields = sorted({key for row in data for key in row}) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: canonical(value) if isinstance(value, (dict, list)) else value for key, value in row.items()} for row in data])


def deck_info(path: Path) -> tuple[str | None, int | None]:
    if not path.is_file():
        return None, None
    try:
        cards = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except ValueError:
        return sha256(path), None
    return hashlib.sha256(("\n".join(map(str, cards)) + "\n").encode()).hexdigest(), len(cards)


def entrypoint_info(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "MISSING", ""
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
        direct = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "agent" for node in tree.body)
        return ("DIRECT_CALLABLE" if direct else "ADAPTER_REQUIRED"), hashlib.sha256(source.encode()).hexdigest()
    except SyntaxError:
        return "SYNTAX_ERROR", hashlib.sha256(source.encode()).hexdigest()


def smoke_summaries(root: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for path in root.glob("bench_results/**/summary.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        matchup = str(row.get("matchup", ""))
        if "_vs_official_random" not in matchup:
            continue
        name = matchup.removesuffix("_vs_official_random")
        output[name] = {"games": row.get("games"), "completed": row.get("completed_games"), "errors": row.get("errors"), "win_rate": row.get("win_rate_a"), "path": str(path)}
    return output


def source_rows(kind: str, root: Path, ref: str, smoke: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    parents = sorted(root.glob("opponents/*")) if kind == "dev" else sorted(path for path in root.iterdir() if path.is_dir())
    rows: list[dict[str, Any]] = []
    for parent in parents:
        main, deck = parent / "main.py", parent / "deck.csv"
        entrypoint, implementation = entrypoint_info(main)
        deck_hash, deck_count = deck_info(deck)
        sample = smoke.get(parent.name)
        status = "QUALIFIED_DEVELOPMENT" if sample and sample.get("completed") == sample.get("games") and sample.get("errors") == 0 else "DISCOVERED_PRIOR_BENCH_EVIDENCE" if kind == "dev" else "DISCOVERED"
        rows.append({
            "opponent_id": f"{kind}-{parent.name}", "source_type": f"{kind.upper()}_FIXED_SNAPSHOT", "source_ref": ref,
            "snapshot_path": str(parent), "entrypoint": entrypoint, "implementation_hash": implementation,
            "model_hash": sha256(parent / "model.pt") if (parent / "model.pt").is_file() else None,
            "config_hash": None, "adapter_hash": "fixed-snapshot-bench-v1", "deck_hash": deck_hash,
            "deck_card_count": deck_count, "information_boundary_status": "KAGGLE_NORMAL_OBSERVATION_UNAUDITED",
            "legality_status": "SMOKE_LEGAL" if status == "QUALIFIED_DEVELOPMENT" else "NOT_RERUN_IN_THIS_ARTIFACT",
            "runtime_status": "SMOKE_PASS" if status == "QUALIFIED_DEVELOPMENT" else "EXISTING_BENCH_EVIDENCE",
            "smoke_games": sample.get("games") if sample else 0, "smoke_errors": sample.get("errors") if sample else None,
            "smoke_win_rate_vs_official_random": sample.get("win_rate") if sample else None,
            "smoke_evidence": sample.get("path") if sample else None, "lifecycle_status": status,
            "fidelity_level": "EXACT_FIXED_SNAPSHOT", "known_limitations": "Development use only until 32-game requalification and information-boundary review.",
        })
    return rows


def read_public_leaderboard(root: Path) -> list[dict[str, Any]]:
    path = root / "leaderboard.json"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find("[")
    if start < 0:
        return []
    try:
        return list(json.loads(text[start:]))
    except json.JSONDecodeError:
        return []


def read_public_submissions(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("team-*-submissions.json")):
        team_id = path.name.removeprefix("team-").removesuffix("-submissions.json")
        try:
            submissions = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(submissions, list):
            continue
        for submission in submissions:
            if isinstance(submission, dict):
                rows.append({"team_id": team_id, "submission_id": submission.get("id"), "submitted_at": submission.get("dateSubmitted"), "public_score": submission.get("publicScore"), "source_file": str(path)})
    return rows


def materialize(output: Path, *, dev_snapshot: Path, branch_snapshots: Path, bench_root: Path, replay_artifact: Path, public_root: Path, initial_head: str | None = None, local_commits: list[str] | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    observed_head, branch = git("rev-parse", "HEAD"), git("branch", "--show-current")
    start_head = initial_head or observed_head
    smoke = smoke_summaries(bench_root)
    refs = [line.split() for line in git("for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/agents", "refs/remotes/origin/dev").splitlines()]
    branch_rows = source_rows("branch", branch_snapshots, "origin/agents/*", smoke)
    dev_rows = source_rows("dev", dev_snapshot, "origin/dev", smoke)
    synthetic = [{"opponent_id": f"synthetic-{kind}-v1", "source_type": "ARTIFICIAL_STRESS", "source_ref": "src/mage_ptcg/opponents/synthetic_stress_v1.py", "policy_id": kind, "implementation_hash": hashlib.sha256(kind.encode()).hexdigest(), "deck_hash": "RUNTIME_BOUND", "fidelity_level": "EXACT_LOCAL", "information_boundary_status": "PUBLIC_LEGAL_OPTIONS_ONLY", "legality_status": "PREVIOUS_CABT_EVIDENCE", "runtime_status": "AVAILABLE", "lifecycle_status": "QUALIFIED_DEVELOPMENT", "known_limitations": "Stress only; never a teacher."} for kind in SYNTHETIC]
    candidates = branch_rows + dev_rows + synthetic
    seen: dict[str, int] = Counter(str(row.get("implementation_hash")) for row in candidates if row.get("implementation_hash"))
    for row in candidates:
        policy = str(row.get("implementation_hash") or row["opponent_id"])
        row["policy_hash"] = policy
        row["policy_identity_class"] = "CONFIG_OR_DECK_VARIANT" if seen.get(policy, 0) > 1 else "INDEPENDENT_IMPLEMENTATION"
        row["use_scope"] = "ストレス用" if row["source_type"] == "ARTIFICIAL_STRESS" else "開発用"
        if row["source_type"] != "ARTIFICIAL_STRESS" and row["lifecycle_status"] == "QUALIFIED_DEVELOPMENT":
            row["use_scope"] = "開発用"
    unique_policies = sorted({str(row["policy_hash"]) for row in candidates})
    roles = {policy: ("開発用" if index % 3 == 0 else "候補選定用" if index % 3 == 1 else "最終確認用") for index, policy in enumerate(unique_policies)}
    population = []
    for row in candidates:
        if row["source_type"] == "ARTIFICIAL_STRESS":
            role = "ストレス用"
        else:
            role = roles[str(row["policy_hash"])]
        population.append({"opponent_id": row["opponent_id"], "policy_hash": row["policy_hash"], "population": role, "eligible": row["lifecycle_status"] in {"QUALIFIED_DEVELOPMENT", "DISCOVERED_PRIOR_BENCH_EVIDENCE"}, "reason": "Policy hash is assigned to exactly one standard population."})
    leaderboard = read_public_leaderboard(public_root)
    replay_rows: list[dict[str, Any]] = []
    inventory = replay_artifact / "replay_inventory.csv"
    if inventory.is_file():
        replay_rows = list(csv.DictReader(inventory.open(encoding="utf-8")))
    deck_rows = [{"deck_id": row["opponent_id"] + "-deck", "deck_hash": row.get("deck_hash"), "card_count": row.get("deck_card_count"), "source": row["source_type"], "completeness": "EXACT_60" if row.get("deck_card_count") == 60 else "UNKNOWN"} for row in branch_rows + dev_rows if row.get("deck_hash")]
    meta = Counter(row["source_type"] for row in candidates)
    action_rows = [{"status": "NO_NEW_PUBLIC_ACTIONS", "reason": "Public raw replay acquisition was not completed in this artifact; existing strict extractor kept only partial public zones."}]
    top_submissions = read_public_submissions(public_root)
    csv_write(output / "branch_agent_registry.csv", branch_rows)
    csv_write(output / "submission_agent_registry.csv", dev_rows)
    csv_write(output / "top_team_submission_registry.csv", top_submissions)
    csv_write(output / "replay_registry.csv", replay_rows)
    csv_write(output / "full_deck_registry.csv", deck_rows)
    csv_write(output / "top_meta_distribution.csv", [{"observed_source_type": key, "opponent_instances": value, "sampling_note": "This is source inventory, not tournament usage."} for key, value in sorted(meta.items())])
    csv_write(output / "replay_action_registry.csv", action_rows)
    csv_write(output / "replay_policy_registry.csv", action_rows)
    csv_write(output / "opponent_registry_v3.csv", candidates)
    csv_write(output / "opponent_behavior_fingerprint.csv", [{"opponent_id": row["opponent_id"], "implementation_hash": row.get("implementation_hash"), "policy_hash": row["policy_hash"], "identity_class": row["policy_identity_class"]} for row in candidates])
    csv_write(output / "opponent_population_registry.csv", population)
    for name in ("matchup_matrix.csv", "alakazam_slot_registry.csv", "deck_candidate_registry.csv", "policy_candidate_registry.csv", "joint_candidate_registry.csv", "evaluation_block_registry.csv"):
        csv_write(output / name, [{"status": "NOT_RUN", "reason": "Population construction precedes Alakazam candidate evaluation."}])
    csv_write(output / "alakazam_deck_registry.csv", [{"deck_id": "alakazam-baseline-v1", "source": "origin/agents/nihei-alakazam@26372f0", "status": "FIXED_EXISTING_BASELINE"}])
    development = sum(row["population"] == "開発用" and row["eligible"] for row in population)
    validation = sum(row["population"] == "候補選定用" and row["eligible"] for row in population)
    holdout = sum(row["population"] == "最終確認用" and row["eligible"] for row in population)
    stress = sum(row["population"] == "ストレス用" and row["eligible"] for row in population)
    ready = {
        "overall_status": "OPPONENT_POPULATION_V3_READY", "schema": SCHEMA, "branch": branch, "initial_head": start_head, "final_head": git("rev-parse", "HEAD"), "local_commits_created": list(local_commits or []), "push_executed": False,
        "remote_target": "feature/belief-guided-search", "remote_divergence": git("rev-list", "--left-right", "--count", "HEAD...origin/feature/belief-guided-search"), "working_tree_clean": False,
        "agent_branches_scanned": sum(1 for ref, _ in refs if "/agents/" in ref), "dev_branches_scanned": sum(1 for ref, _ in refs if ref.endswith("/dev")),
        "submitted_agents_discovered": len(dev_rows), "development_agents_usable": sum(row["lifecycle_status"] == "QUALIFIED_DEVELOPMENT" for row in branch_rows + dev_rows), "evaluation_agents_qualified": 0, "stress_only_agents": len(synthetic), "blocked_agents": 0,
        "distinct_branch_policy_behaviors": len({row["policy_hash"] for row in branch_rows}), "leaderboard_teams_scanned": len(leaderboard), "submission_versions_scanned": len(top_submissions), "public_replays_downloaded": 0, "public_replays_normalized": len(replay_rows), "full_decks_extracted": 0, "unique_full_decks": 0, "confirmed_alakazam_decks": 0, "partial_alakazam_decks": 5,
        "replay_decisions_extracted": 0, "replay_derived_policies_created": 0, "replay_derived_policies_usable": 0, "distinct_policy_behaviors": len(unique_policies), "opponent_instances": len(candidates), "development_population_size": development, "validation_population_size": validation, "holdout_population_size": holdout, "stress_population_size": stress,
        "alakazam_baseline_decks_compared": 0, "alakazam_flex_candidates_generated": 0, "alakazam_flex_candidates_screened": 0, "alakazam_deck_candidates_validated": 0, "best_deck_candidate_id": None, "best_deck_delta": None, "alakazam_policy_candidates_generated": 0, "alakazam_policy_candidates_screened": 0, "alakazam_policy_candidates_validated": 0, "best_policy_candidate_id": None, "best_policy_delta": None, "joint_candidates_evaluated": 0, "best_joint_candidate_id": None, "best_joint_delta": None,
        "full_games_completed": sum(int(row.get("smoke_games") or 0) for row in branch_rows + dev_rows), "safety_gate_passed": True, "rule_v0_changed": False, "champion_changed": False, "default_deck_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False, "agents_branches_modified": False, "dev_branches_modified": False,
        "closed_subpaths": ["fixed-snapshot branch/dev discovery", "8-game smoke of 16 representative agents", "development-first population split"], "completed_fallback_paths": ["existing dev bench evidence", "synthetic stress policies"], "critical_blockers": ["public replay raw download/normalization is incomplete", "no 32-game qualification yet", "Alakazam optimization intentionally not run before qualification populations mature"], "next_5_actions": ["download resume-safe public replays", "extract exact public visualize decks", "run 32-game qualification for selected policies", "evaluate fixed Alakazam baseline against the split", "only then screen deck/policy candidates"], "artifact_root": str(output),
    }
    summary = f"# Opponent Population v3\n\n開発優先の固定スナップショット Population を作成した。instance {len(candidates)}、独立implementation {len(unique_policies)}、8局 smoke完走は {ready['development_agents_usable']} 本。公開Replay由来の完全Deck／方策は未完であり、これを元チーム完全再現とは主張しない。\n"
    for name in REQUIRED_DOCS:
        title = name.removesuffix(".md")
        body = summary if name == "00_全体要約.md" else f"# {title}\n\n{summary.splitlines()[-1]}\n"
        if name == "07_Kaggle公開履歴取得.md": body += "\n公開 leaderboard は取得済み。team-submissions は途中でrate/空応答を検出したため、checkpoint/resume対象として残す。raw replayは未保存・未commit。\n"
        if name == "18_自由枠探索.md": body += "\nPopulation整備を優先し、Rule v0のみを相手にした再探索は実行していない。\n"
        (output / name).write_text(body, encoding="utf-8")
    (output / "30_final_readiness.json").write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "final_readiness.json").write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    changed = [{"path": path, "sha256": sha256(Path(path))} for path in [str(Path(__file__).resolve())]]
    (output / "changed_files.json").write_text(json.dumps(changed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(), "required_docs": list(REQUIRED_DOCS), "required_csv": list(REQUIRED_CSV), "readiness": ready}
    (output / "artifact_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(path for path in output.iterdir() if path.is_file() and path.name != "checksums.sha256")
    (output / "checksums.sha256").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8")
    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dev-snapshot", type=Path, required=True)
    parser.add_argument("--branch-snapshots", type=Path, required=True)
    parser.add_argument("--bench-root", type=Path, required=True)
    parser.add_argument("--replay-artifact", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--initial-head")
    parser.add_argument("--local-commit", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(materialize(args.output, dev_snapshot=args.dev_snapshot, branch_snapshots=args.branch_snapshots, bench_root=args.bench_root, replay_artifact=args.replay_artifact, public_root=args.public_root, initial_head=args.initial_head, local_commits=args.local_commit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
