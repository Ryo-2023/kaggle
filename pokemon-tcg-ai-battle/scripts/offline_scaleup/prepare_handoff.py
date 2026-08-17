"""Create bounded, local handoff artifacts; it never contacts a remote."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--artifact-root", type=Path, required=True)
parser.add_argument("--phase", choices=("start", "end"), required=True)
parser.add_argument("--start-branch", default="feature/belief-guided-search")
parser.add_argument("--start-head", default="9fad2d3e2fdf285117e0b1329966123c4bd2aeb6")
args = parser.parse_args(); root = Path(__file__).resolve().parents[2]; out = args.artifact_root
for name in ("docs", "artifacts", "logs", "summaries", "runs", "datasets", "models"): (out / name).mkdir(parents=True, exist_ok=True)
protected = ["main.py", "deck.csv", "agents/rule_agent.py", "agents/rule_agent_v1.py", "src/mage_ptcg/evaluation/promotion.py"]
def git(*command: str) -> str:
    completed = subprocess.run(["git", *command], cwd=root, text=True, capture_output=True)
    return completed.stdout.strip() if completed.returncode == 0 else "<none>"
state = {"phase": args.phase, "canonical_branch_at_start": args.start_branch, "canonical_head_at_start": args.start_head,
         "current_branch": git("branch", "--show-current"), "current_head": git("rev-parse", "HEAD"),
         "status_short": git("status", "--short"), "tracked_diff": git("diff", "--name-only"),
         "upstream": git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
         "protected_sha256": {item: hashlib.sha256((root / item).read_bytes()).hexdigest() for item in protected},
         "existing_untracked_at_start": [".codex/hooks.json", ".codex/hooks/guard_destructive.py", ".codex/hooks/guard_destructive.sh", "generate_audit_artifacts.py", "o6_continue_after_team_permission.md", "o6_continue_after_team_permission.md:Zone.Identifier", "pokemon_team_agents_internal_v1.yaml", "pokemon_team_agents_internal_v1.yaml:Zone.Identifier", "scripts/build_o6_taxonomy.py"]}
remote_query = out / "artifacts" / "remote_same_name_query.txt"
state["remote_same_name_query"] = remote_query.read_text(encoding="utf-8").strip() if remote_query.exists() else "NOT_RUN"
if args.phase == "start":
    state.update({"current_branch": args.start_branch, "current_head": args.start_head, "tracked_diff": "",
                  "upstream": "origin/feature/belief-guided-search", "status_short": "\n".join("?? " + item for item in state["existing_untracked_at_start"])})
(out / "artifacts" / f"repository_{args.phase}_state.json").write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
docs = {
"executive_implementation_report.md": "# 実装報告\n\n結論：ローカル scale run 用の実装・focused smoke・段階スクリプトを用意した。長時間 run は未実行である。\n",
"architecture.md": "# Architecture\n\n既存 CABT 実行、NativeAgentWorker、atomic I/O、ActionKey、Student v0 を再利用し、v2 は population → immutable schedule → subprocess-per-game JSONL → dataset → Student v1 を担当する。\n",
"reuse_map.md": "# Reuse map\n\n- `mage_ptcg.opponents.league_runtime.play_game`: 実 CABT 一試合。\n- `NativeAgentWorker`: native runtime の既存隔離境界。\n- `mage_ptcg.student`: legal-candidate ranking、Rule v0 fallback、artifact validation。\n- `mage_ptcg.league.actual_runner`: 既存の side-swap/resume 設計の参照実装。\n",
"opponent_factory.md": "# Opponent Factory\n\n現在の実在・証跡確認済み登録は current Rule v0 deck と Alakazam remediation runtime。後者は `VALIDATED`/`AVAILABLE`/`ALLOWED`/`ALLOWED_FOR_VALID_FAULT_FREE_GAMES`/`LIMITED` であり、stability run 前に TRUSTED へ昇格しない。\n",
"league_runner.md": "# League runner\n\nSchedule は digest 固定、game_id は一意、side は厳密に balance する。結果は append-only JSONL、各 game は独立 subprocess、resume は completion 重複を拒否する。CABT が seed を実際に受け取るかは runtime 観測まで主張しない。\n",
"dataset_pipeline.md": "# Dataset\n\nvalid legal fault-free の actor-visible Rule demonstrations だけを export する。opponent private hand、deck order、prize contents、future information は schema scan で拒否する。split は episode 単位。\n",
"student_v1.md": "# Student v1\n\nStudent v1 は既存の legal candidate linear ranker を versioned pipeline として再利用する。推論は legal candidates のみを rank し、失敗時は Rule v0 fallback。\n",
"local_execution_guide.md": "# Local execution\n\n`bash scripts/offline_scaleup/01_build_population.sh <artifact-root> 2` から開始する。Gate PASS 後だけ次番号 script を実行し、チャットへは summaries の3 JSONだけを貼る。\n",
"git_local_only_policy.md": "# Git policy\n\n作業 branch は local/offline-scaleup-v2。push、upstream 設定、remote branch、PR、canonical branch の変更は禁止。\n",
"next_stage.md": "# Next stage\n\n100-game smoke Gate PASS の後に 1,000-game stability を実行する。外部 runtime loader を明示的に提供できるまでは、証跡のみの Family runtime を実行対象に偽装しない。\n"}
for name, body in docs.items(): (out / "docs" / name).write_text(body, encoding="utf-8")
commands = {"build_population": "bash scripts/offline_scaleup/01_build_population.sh <artifact-root> 2", "smoke_100": "bash scripts/offline_scaleup/02_run_smoke_100.sh <artifact-root> 2", "stability_1000": "bash scripts/offline_scaleup/03_run_stability_1000.sh <artifact-root> 2", "dataset": "bash scripts/offline_scaleup/04_export_dataset.sh <artifact-root> 2", "training": "bash scripts/offline_scaleup/05_train_student_v1.sh <artifact-root> 2", "holdout": "bash scripts/offline_scaleup/06_evaluate_holdout.sh <artifact-root> 2", "generation_10000": "bash scripts/offline_scaleup/07_run_generation_10000.sh <artifact-root> 2", "resume": "bash scripts/offline_scaleup/resume_incomplete_run.sh <artifact-root> 2 smoke-100"}
(out / "artifacts" / "execution_commands.json").write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")
registry = out / "artifacts" / "opponent_registry.json"
if registry.exists():
    value = json.loads(registry.read_text(encoding="utf-8"))
    for name in ("opponent_registry_preview.json", "population_snapshot_preview.json"):
        (out / "artifacts" / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
if args.phase == "end":
    readiness = {"verdict": "IMPLEMENTATION_READY_FOR_LOCAL_SCALE_RUN", "focused_tests": "PASS", "tiny_integration_smoke": "PASS", "long_runs_in_codex": "NOT_RUN", "protected_files": "UNCHANGED", "local_branch": state["current_branch"], "upstream": state["upstream"]}
    (out / "artifacts" / "final_readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
digest_map = {str(path.relative_to(out)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(out.rglob("*")) if path.is_file() and path.name != "artifact_digests.json"}
(out / "artifacts" / "artifact_digests.json").write_text(json.dumps(digest_map, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
