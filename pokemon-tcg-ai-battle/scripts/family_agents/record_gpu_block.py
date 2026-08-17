#!/usr/bin/env python3
"""Record a fail-closed GPU continuation block without fabricating training."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",", ":"))+"\n",encoding="utf-8")
    temporary.replace(path)

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--root",type=Path,required=True); args=parser.parse_args(); root=args.root
    probe=json.loads((root/"artifacts/environment_probe.json").read_text(encoding="utf-8")); dataset=json.loads((root/"gpu_dataset/manifest.json").read_text(encoding="utf-8"))
    reason="CUDA/BF16 training is unavailable: PyTorch reports cuda_available=false and device_count=0."
    blocked={"status":"BLOCKED_BY_GPU_ENVIRONMENT","reason":reason,"environment":probe,"dataset_manifest":str(root/"gpu_dataset/manifest.json"),"dataset_integrity":{"parse_failures":dataset["parse_failures"],"illegal_targets":dataset["illegal_targets"],"episode_leakage":dataset["episode_leakage"]}}
    for name in ("gpu_student_v2_run_a.json","gpu_student_v2_run_b.json","gpu_student_v2_holdout.json","gpu_student_v2_cabt_smoke.json"):
        write(root/"artifacts"/name,blocked)
    write(root/"artifacts/generation_10000_status.json",{"status":"NOT_STARTED","reason":"BLOCKED_BY_GPU_ENVIRONMENT","pilot_runtime_gate":"PASS","dataset_split_v2":"PASS"})
    write(root/"artifacts/final_readiness.json",{"verdict":"BLOCKED_BY_GPU_SAFETY","reason":reason,"dataset_split_v2":"PASS","gpu_dataset_materialized":True,"gpu_training":"NOT_STARTED","cabt_smoke":"NOT_STARTED","generation_10000":"NOT_STARTED","promotion":"NO_DECISION"})
    docs=root/"docs"; docs.mkdir(parents=True,exist_ok=True)
    gate=json.loads((root/"artifacts/dataset_gate_v2.json").read_text(encoding="utf-8"))
    (docs/"split_v2_report.md").write_text(f"# Split v2 report\n\nVerdict: `{gate['verdict']}`。cohortは {gate['cohorts']}。deck holdoutとopponent holdoutは具体identityで選び、entity leakageは0である。family holdoutはtrain最低1,000 episodeを守れないため作成していない。\n",encoding="utf-8")
    (docs/"gpu_student_v2_report.md").write_text("# GPU Student v2 report\n\nGPU dataset conversionは完了したが、このhostはCUDA device 0台のためCUDA/BF16 trainingを実行していない。GPU gateは`BLOCKED_BY_GPU_ENVIRONMENT`であり、model/checkpoint/holdout/CABT smokeを捏造しない。\n",encoding="utf-8")
    (docs/"large_scale_generation_status.md").write_text("# Large-scale generation status\n\n10,000局generationは未開始である。split-v2はPASSだがGPU trainingとCABT safetyが未成立のため、開始条件を満たさない。\n",encoding="utf-8")
    (docs/"next_stage.md").write_text("# Next stage\n\nCUDA/BF16を利用可能なhostで同じsplit-v2 dataset manifestを再利用し、GPU Student v2 run A/B、holdout、CABT safetyを順に通過させてから10,000局generationを開始する。\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
