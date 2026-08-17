#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; WORKERS="${2:-4}"; DEVICE="${3:-cuda}"
GPU_PYTHON="${GPU_PYTHON:-$ROOT/.venv-gpu/bin/python}"; export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/10_train_student_v2_gpu.log"; mkdir -p "$ARTIFACT_ROOT/logs"
test -x "$GPU_PYTHON"
"$GPU_PYTHON" -c 'import json,sys,torch; assert torch.cuda.is_available(), "CUDA unavailable"; print(json.dumps({"executable":sys.executable,"torch":torch.__version__,"cuda":torch.version.cuda,"cuda_available":True,"gpu":torch.cuda.get_device_name(0),"bf16":torch.cuda.is_bf16_supported()},sort_keys=True))' >"$LOG"
"$GPU_PYTHON" -m mage_ptcg.offline_scaleup.gpu_student_v2 train --dataset-dir "$ARTIFACT_ROOT/gpu_dataset" --output-dir "$ARTIFACT_ROOT/models/student-v2" --device "$DEVICE" --workers "$WORKERS" --progress "${@:4}" >>"$LOG" 2>&1
cp "$ARTIFACT_ROOT/models/student-v2/student_v2_config.json" "$ARTIFACT_ROOT/artifacts/student_v2_config.json"
printf 'summary=%s next_command=%s\n' "$ARTIFACT_ROOT/models/student-v2/training_summary.json" "$ROOT/scripts/offline_scaleup/11_evaluate_student_v2.sh $ARTIFACT_ROOT $WORKERS $DEVICE"
