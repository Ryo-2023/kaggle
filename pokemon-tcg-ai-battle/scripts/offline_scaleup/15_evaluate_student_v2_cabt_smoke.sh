#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; DEVICE="${3:-cuda}"
GPU_PYTHON="${GPU_PYTHON:-$ROOT/.venv-gpu/bin/python}"; export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/15_evaluate_student_v2_cabt_smoke.log"; mkdir -p "$ARTIFACT_ROOT/logs"
test -x "$GPU_PYTHON"
test -f "$ARTIFACT_ROOT/models/student-v2-multiteacher/best.pt"
"$GPU_PYTHON" -c 'import json,sys,torch; assert torch.cuda.is_available(), "CUDA unavailable"; print(json.dumps({"executable":sys.executable,"torch":torch.__version__,"cuda":torch.version.cuda,"cuda_available":True,"gpu":torch.cuda.get_device_name(0),"bf16":torch.cuda.is_bf16_supported()},sort_keys=True))' >"$LOG"
"$GPU_PYTHON" -m mage_ptcg.offline_scaleup.gpu_student_v2 evaluate --dataset-dir "$ARTIFACT_ROOT/gpu_dataset_multiteacher" --model-dir "$ARTIFACT_ROOT/models/student-v2-multiteacher" --device "$DEVICE" --output "$ARTIFACT_ROOT/artifacts/student_v2_multiteacher_eval.json" >>"$LOG" 2>&1
printf 'evaluation=%s note=%s\n' "$ARTIFACT_ROOT/artifacts/student_v2_multiteacher_eval.json" 'Student v2 remains offline evaluation-only; no main.py integration occurs.'
