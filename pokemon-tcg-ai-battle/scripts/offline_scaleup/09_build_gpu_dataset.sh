#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:?artifact root required}"; WORKERS="${2:-4}"; SOURCE="${3:?dataset JSONL required}"
GPU_PYTHON="${GPU_PYTHON:-$ROOT/.venv-gpu/bin/python}"; export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/09_build_gpu_dataset.log"; mkdir -p "$ARTIFACT_ROOT/logs"
test -x "$GPU_PYTHON"
"$GPU_PYTHON" -c 'import json,sys,torch; assert torch.cuda.is_available(), "CUDA unavailable"; print(json.dumps({"executable":sys.executable,"torch":torch.__version__,"cuda":torch.version.cuda,"cuda_available":True,"gpu":torch.cuda.get_device_name(0),"bf16":torch.cuda.is_bf16_supported()},sort_keys=True))' >"$LOG"
"$GPU_PYTHON" -m mage_ptcg.offline_scaleup.gpu_student_v2 probe --output "$ARTIFACT_ROOT/artifacts/environment_probe.json" >>"$LOG" 2>&1
"$GPU_PYTHON" -m mage_ptcg.offline_scaleup.gpu_student_v2 build-dataset --source "$SOURCE" --output-dir "$ARTIFACT_ROOT/gpu_dataset" --progress "${@:4}" >>"$LOG" 2>&1
"$GPU_PYTHON" -c 'import json,sys; p=json.load(open(sys.argv[1])); assert p["parse_failures"]==p["illegal_targets"]==p["episode_leakage"]==0; json.dump(p,open(sys.argv[2],"w"),sort_keys=True,separators=(",",":"));' "$ARTIFACT_ROOT/gpu_dataset/manifest.json" "$ARTIFACT_ROOT/artifacts/gpu_dataset_contract.json"
printf 'summary=%s next_command=%s\n' "$ARTIFACT_ROOT/artifacts/gpu_dataset_contract.json" "$ROOT/scripts/offline_scaleup/10_train_student_v2_gpu.sh $ARTIFACT_ROOT $WORKERS cuda"
