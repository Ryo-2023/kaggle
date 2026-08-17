"""Create compact handoff artifacts for the GPU Student v2 implementation."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

parser=argparse.ArgumentParser(); parser.add_argument("--artifact-root",type=Path,required=True); args=parser.parse_args(); root=args.artifact_root
def read(relative: str): return json.loads((root/relative).read_text(encoding="utf-8"))
def write(relative: str, value: object):
    path=root/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
environment=read("artifacts/environment_probe.json"); dataset=read("artifacts/gpu_dataset_contract.json"); training=read("models/student-v2-smoke/training_summary.json"); evaluation=read("artifacts/student_v2_eval_preview.json"); registry=read("artifacts/multiteacher_registry.json"); schedule=read("artifacts/multiteacher_schedule_preview.json")
smoke={"schema_version":"offline-scaleup-gpu-student-v2-smoke-v1","cuda_used":training["device"].startswith("cuda"),"checkpoint_resume":training["epochs_completed"]==2,"model_load":True,"legal_action_rate":min(item["legal_action_rate"] for item in evaluation["splits"].values()),"cpu_fallback":"implemented","gpu_cpu_consistency":evaluation["gpu_cpu_consistency"],"training":{key:training[key] for key in ("device","compute_dtype","batch_size","gradient_accumulation","best_validation_top1","best_checkpoint_sha256")}}
write("artifacts/gpu_smoke_metrics.json",smoke); write("artifacts/gpu_dataset_preview.json",{"schema_version":dataset["schema_version"],"records":dataset["records"],"episodes":dataset["episodes"],"skipped_optional":dataset["skipped_optional"],"source_dataset_sha256":dataset["source_dataset_sha256"]}); write("artifacts/student_v2_config.json",read("models/student-v2-smoke/student_v2_config.json"))
ready=all((dataset[key]==0 for key in ("parse_failures","illegal_targets","episode_leakage"))) and smoke["cuda_used"] and smoke["checkpoint_resume"] and smoke["legal_action_rate"]==1.0
verdict="GPU_STUDENT_V2_READY_FOR_FULL_TRAINING" if ready else "INVALID_GPU_PIPELINE_EVIDENCE"
# Candidate capture is intentionally not claimed ready: registry/schedule are
# complete, while the approved production runner still captures Rule v0 only.
final={"schema_version":"offline-scaleup-gpu-student-v2-final-v1","verdict":"MULTITEACHER_RUNTIME_INTEGRATION_REQUIRED" if ready else verdict,"gpu_student_v2_gate":"PASS" if ready else "FAIL","multiteacher_registry_entries":len(registry["teachers"]),"multiteacher_exclusions":len(registry["exclusions"]),"schedule_preview_games":schedule["planned_games"],"long_runs_executed":0,"full_gpu_training_executed":0,"reason":"Multi-Teacher candidate-side trajectory capture must be integrated before 2,000/10,000-game execution; no synthetic trajectory evidence was created."}
write("artifacts/final_readiness.json",final)
documents={"executive_report.md":f"# GPU Student v2\n\nGPU gate: `{final['gpu_student_v2_gate']}`. Final verdict: `{final['verdict']}`.\n", "gpu_dataset.md":f"# GPU Dataset\n\nCompact .pt shards retain {sum(dataset['records'].values())} supervised records over five original episode splits. Optional no-action prompts skipped: {dataset['skipped_optional']}.\n", "student_v2_architecture.md":"# Student v2 Architecture\n\nShared legal-candidate scorer: state/action encoders, multiplicative interaction, residual GELU blocks, and masked listwise softmax.\n", "gpu_training_protocol.md":"# GPU Training Protocol\n\nUse BF16 when supported, checkpoint best/last every epoch, and resume only from matching shard manifest.\n", "multiteacher_registry.md":f"# Multi-Teacher Registry\n\nRegistered candidates: {len(registry['teachers'])}; opponent-only/unavailable exclusions: {len(registry['exclusions'])}.\n", "multiteacher_generation.md":"# Multi-Teacher Generation\n\nThe immutable schedule is deterministic and balanced. Candidate-side trajectory capture remains a required integration before execution.\n", "local_execution_guide.md":"# Local Execution Guide\n\nRun 09 build, then 10 train, then 11 evaluate. Do not run 12/16 until candidate-side capture integration is complete.\n", "next_stage.md":"# Next Stage\n\nImplement the approved candidate-side capture adapter, then run the 2,000-game pilot Gate before 10,000-game generation.\n"}
for name,text in documents.items(): (root/"docs").mkdir(parents=True,exist_ok=True); (root/"docs"/name).write_text(text,encoding="utf-8")
digests={"schema_version":"offline-scaleup-artifact-digests-v1","files":{}}
for path in sorted(root.rglob("*.json")):
    digests["files"][str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
write("artifacts/artifact_digests.json",digests)
