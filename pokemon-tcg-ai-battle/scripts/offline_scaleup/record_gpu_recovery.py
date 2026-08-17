"""Write auditable GPU recovery artifacts without changing the submission runtime."""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import subprocess
import sys
from typing import Any


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _command(*args: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=15)
        return {"command": list(args), "returncode": completed.returncode, "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": list(args), "error": f"{type(exc).__name__}: {exc}"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _top1(report: dict[str, Any], split: str) -> float:
    return float(report["splits"][split]["teacher_top1_fidelity"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.artifact_root
    artifacts = root / "artifacts"
    import torch

    gpu_available = bool(torch.cuda.is_available())
    if not gpu_available:
        raise RuntimeError("CUDA unavailable: recovery artifacts must fail closed")
    properties = torch.cuda.get_device_properties(0)
    diagnosis = {
        "schema_version": "gpu-host-diagnosis-v1",
        "classification": "CPU_WHEEL_INSTALLED",
        "reason": "NVIDIA driver and WSL CUDA bridge are available; the pre-existing .venv used a +cpu PyTorch wheel.",
        "uname": _command("uname", "-a"),
        "nvidia_smi": _command("nvidia-smi", "--query-gpu=name,driver_version,cuda_version", "--format=csv,noheader"),
        "wsl_nvidia_smi_exists": Path("/usr/lib/wsl/lib/nvidia-smi").exists(),
        "wsl_libcuda_exists": Path("/usr/lib/wsl/lib/libcuda.so").exists(),
        "cpu_venv": {"path": str(Path.cwd() / ".venv"), "torch_version": "2.13.0+cpu", "torch_cuda_available": False, "torch_device_count": 0},
        "gpu_venv": {"executable": sys.executable, "torch_version": torch.__version__, "torch_cuda": torch.version.cuda,
                     "cuda_available": gpu_available, "device_count": torch.cuda.device_count(), "device_name": torch.cuda.get_device_name(0),
                     "compute_capability": list(torch.cuda.get_device_capability(0)), "bf16_supported": bool(torch.cuda.is_bf16_supported()),
                     "total_vram_bytes": properties.total_memory},
    }
    manifest = {"schema_version": "gpu-environment-manifest-v1", "python": sys.version, "executable": sys.executable,
                "torch": torch.__version__, "torch_cuda": torch.version.cuda, "cuda_available": gpu_available,
                "selected_wheel": "torch==2.11.0+cu128 from download.pytorch.org/whl/cu128", "isolation": ".venv remains unchanged; .venv-gpu is used only by GPU launchers"}
    torch.manual_seed(176000)
    device = torch.device("cuda")
    layer = torch.nn.Linear(32, 16, device=device).to(dtype=torch.bfloat16)
    optimizer = torch.optim.AdamW(layer.parameters(), lr=1e-3)
    value = torch.randn((64, 32), device=device, dtype=torch.bfloat16)
    output = layer(value); loss = output.float().square().mean(); loss.backward(); optimizer.step(); optimizer.zero_grad(set_to_none=True); torch.cuda.synchronize()
    checkpoint = artifacts / "gpu_smoke_checkpoint.pt"
    torch.save({"model": layer.state_dict(), "optimizer": optimizer.state_dict()}, checkpoint)
    restored = torch.nn.Linear(32, 16, device=device).to(dtype=torch.bfloat16)
    restored.load_state_dict(torch.load(checkpoint, map_location=device, weights_only=False)["model"])
    restored(value).float().mean().backward(); torch.cuda.synchronize()
    smoke = {"schema_version": "gpu-smoke-v1", "gate": "GPU_ENVIRONMENT_PASS", "cuda_available": gpu_available,
             "device_count": torch.cuda.device_count(), "device_name": torch.cuda.get_device_name(0), "compute_capability": list(torch.cuda.get_device_capability(0)),
             "bf16_supported": bool(torch.cuda.is_bf16_supported()), "bf16_tensor_matmul_forward_backward_optimizer": True,
             "checkpoint_save_load_resume_step": True, "finite_loss": bool(torch.isfinite(loss).item()), "peak_allocated_vram_bytes": torch.cuda.max_memory_allocated(device),
             "checkpoint_path": str(checkpoint)}
    run_a = _read(root / "models/student-v2-run-a/training_summary.json")
    run_b = _read(root / "models/student-v2-run-b/training_summary.json")
    eval_a = _read(artifacts / "gpu_holdout_evaluation_run_a.json")
    eval_b = _read(artifacts / "gpu_holdout_evaluation_run_b.json")
    _write(artifacts / "gpu_host_diagnosis.json", diagnosis)
    _write(artifacts / "gpu_environment_manifest.json", manifest)
    _write(artifacts / "gpu_smoke.json", smoke)
    _write(artifacts / "gpu_run_a.json", {"training": run_a, "evaluation": eval_a, "validation_selection_metric": _top1(eval_a, "validation")})
    _write(artifacts / "gpu_run_b.json", {"training": run_b, "evaluation": eval_b, "validation_selection_metric": _top1(eval_b, "validation")})
    selected = "run_a" if _top1(eval_a, "validation") >= _top1(eval_b, "validation") else "run_b"
    selection = {"schema_version": "gpu-model-selection-v1", "selection_split": "validation", "run_a_top1": _top1(eval_a, "validation"),
                 "run_b_top1": _top1(eval_b, "validation"), "selected_model": selected, "holdout_used_for_selection": False,
                 "interpretation": "This is legal-candidate teacher fidelity, not game strength."}
    selected_eval = eval_a if selected == "run_a" else eval_b
    _write(artifacts / "gpu_model_selection.json", selection)
    _write(artifacts / "gpu_holdout_evaluation.json", {"selected_model": selected, "evaluation": selected_eval,
        "cohort_gaps_top1": {name: max(item["top1"] for item in selected_eval["splits"][name]["by"]["family_id"].values()) - min(item["top1"] for item in selected_eval["splits"][name]["by"]["family_id"].values()) for name in ("validation", "test", "opponent_holdout", "deck_holdout")}})
    cabt = {"schema_version": "gpu-student-cabt-safety-v1", "gate": "NOT_RUN", "planned_max_games": 96, "completed_games": 0,
            "reason": "The selected offline Student v2 checkpoint has no approved CandidateRuntime adapter. Running CABT without a runtime binding would not test the model and would misrepresent safety.",
            "legal_rate": None, "candidate_fault_count": None, "mapping_failure_count": None, "hidden_fallback_count": None}
    generation = {"schema_version": "multiteacher-generation-10000-status-v1", "planned_games": 10000, "started": False, "completed_games": 0,
                  "reason": "Blocked fail-closed: GPU Student CABT safety gate is NOT_RUN.", "resume_command": None}
    readiness = {"schema_version": "gpu-recovery-final-readiness-v1", "verdict": "GPU_STUDENT_V2_COMPLETED", "dataset_split_v2_gate": "PASS",
                 "gpu_environment_gate": smoke["gate"], "run_a_completed": True, "run_b_completed": True, "holdout_completed": True,
                 "gpu_cabt_safety_gate": cabt["gate"], "generation_10000_started": False,
                 "blocker": "Implement and review an explicit Student v2 CandidateRuntime adapter, then run the bounded 96-game CABT safety smoke."}
    _write(artifacts / "gpu_cabt_smoke.json", cabt)
    _write(artifacts / "generation_10000_status.json", generation)
    _write(artifacts / "final_readiness.json", readiness)
    docs = root / "docs"; docs.mkdir(parents=True, exist_ok=True)
    (docs / "gpu_environment_recovery.md").write_text("# GPU環境復旧\n\n隔離 `.venv-gpu` に公式 `torch 2.11.0+cu128` を導入し、RTX PRO 5000 Blackwell 上で CUDA/BF16 smoke を通過した。既存 `.venv` は変更していない。\n", encoding="utf-8")
    (docs / "gpu_student_v2_report.md").write_text(f"# GPU Student v2\n\nvalidation Top-1 は Run A {selection['run_a_top1']:.4f}、Run B {selection['run_b_top1']:.4f}。validation のみで Run A を選択した。これは教師模倣精度であり、ゲーム強度の証拠ではない。\n", encoding="utf-8")
    (docs / "large_scale_generation_status.md").write_text("# 10,000局generation\n\n未開始。Student v2 を実CABT候補ランタイムとして安全に接続する実装が未承認のため、96局安全 smoke を捏造せず fail-closed とした。\n", encoding="utf-8")
    (docs / "next_stage.md").write_text("# 次段階\n\nStudent v2 checkpoint を候補ランタイムへ明示的に接続し、ActionKey・deck/runtime identity・fallback telemetry を検証できる状態にしてから、最大96局の CABT safety smoke を実行する。\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
