#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_root="${repo_root}/runs/continuous-league-external-v1/bootstrap-v2/collection"
pid_path="${run_root}/runner.pid"
log_path="${run_root}/runner.log"
unit_name="continuous-replay-bootstrap-v2.service"

mkdir -p "${run_root}"
if systemctl --user is-active --quiet "${unit_name}"; then
  existing_pid="$(systemctl --user show --property=MainPID --value "${unit_name}")"
  printf '%s\n' "${existing_pid}" >"${pid_path}"
  echo "Replay collection is already running: PID ${existing_pid}"
  exit 0
fi

systemctl --user reset-failed "${unit_name}" 2>/dev/null || true
: >"${log_path}"
systemd-run --user \
  --unit="${unit_name%.service}" \
  --collect \
  --working-directory="${repo_root}" \
  --property=Nice=10 \
  --property="CPUAffinity=24 25" \
  --property="StandardOutput=append:${log_path}" \
  --property="StandardError=append:${log_path}" \
  --setenv=OMP_NUM_THREADS=1 \
  --setenv=MKL_NUM_THREADS=1 \
  --setenv=OPENBLAS_NUM_THREADS=1 \
  "${repo_root}/.venv/bin/python" \
  "${repo_root}/scripts/prepare_continuous_replay.py"
sleep 1
if ! systemctl --user is-active --quiet "${unit_name}"; then
  echo "Replay collection failed to start. See ${log_path}" >&2
  exit 1
fi
runner_pid="$(systemctl --user show --property=MainPID --value "${unit_name}")"
printf '%s\n' "${runner_pid}" >"${pid_path}"
echo "Replay collection started: PID ${runner_pid}"
echo "Progress: ${log_path}"
