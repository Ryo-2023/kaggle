#!/usr/bin/env bash
# Resumable long-run launcher.  CABT work is intentionally run by the caller,
# not by an interactive Codex foreground process.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src

usage() {
  echo "usage: $0 {smoke|joint-screen|joint-validation|joint-validation-smoke|teacher-collection|student-training|student-evaluation} [--resume] [--workers 1..8] [--artifact-root PATH]" >&2
}

[[ $# -ge 1 ]] || { usage; exit 2; }
phase="$1"; shift
case "$phase" in smoke|joint-screen|joint-validation|joint-validation-smoke|teacher-collection|student-training|student-evaluation) ;; *) usage; exit 2;; esac

resume=0
workers=8
artifact_root=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --resume) resume=1; shift ;;
    --workers) workers="$2"; shift 2 ;;
    --artifact-root) artifact_root="$2"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
[[ "$workers" =~ ^[1-8]$ ]] || { echo "--workers must be 1..8" >&2; exit 2; }
run_id="joint-deck-policy-next-$(date +%Y%m%d_%H%M%S)"
artifact_root="${artifact_root:-/home/bfe-lab-ono/kaggle/handoff-artifacts/$run_id}"
log_dir="$artifact_root/logs"
lock_dir="$artifact_root/.longrun.lock"
mkdir -p "$log_dir"
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "another launcher is active or left a lock: $lock_dir" >&2
  exit 3
fi
pid_file="$artifact_root/${phase}.pid"
exit_file="$artifact_root/${phase}.exit_code"
checkpoint_file="$artifact_root/${phase}.checkpoint.json"
cleanup() { r=$?; printf '%s\n' "$r" > "$exit_file"; rm -rf "$lock_dir"; rm -f "$pid_file"; exit "$r"; }
save_signal_checkpoint() { printf '{"phase":"%s","signal":"%s","saved_at":"%s"}\n' "$phase" "$1" "$(date -Is)" > "$checkpoint_file"; }
trap 'save_signal_checkpoint INT; cleanup' INT
trap 'save_signal_checkpoint TERM; cleanup' TERM
trap cleanup EXIT
printf '%s\n' "$$" > "$pid_file"

# The Python runner owns shard/aggregate semantics.  Every invocation receives
# a stable artifact root and --resume is forwarded rather than being emulated
# by the shell.  stdout and stderr remain outside Git.
if [[ "$phase" == "joint-validation" || "$phase" == "joint-validation-smoke" ]]; then
  args=(--output "$artifact_root" --workers "$workers")
  [[ $resume -eq 1 ]] && args+=(--resume)
  [[ "$phase" == "joint-validation-smoke" ]] && args+=(--smoke)
  python scripts/run_alakazam_joint_validation.py "${args[@]}" \
    2>>"$log_dir/${phase}.stderr.log" | tee -a "$log_dir/${phase}.stdout.log"
else
  args=("$phase" --output "$artifact_root")
  [[ $resume -eq 1 ]] && args+=(--resume)
  args+=(--workers "$workers")
  python scripts/run_alakazam_joint_optimization.py "${args[@]}" \
    2>>"$log_dir/${phase}.stderr.log" | tee -a "$log_dir/${phase}.stdout.log"
fi
