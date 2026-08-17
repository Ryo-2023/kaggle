"""GPU-ready, legal-candidate Student v2 training and evaluation.

This module deliberately consumes the already privacy-checked v2 JSONL.  It
materializes compact ``.pt`` shards once, so epochs never parse JSONL.  The
runtime is evaluation-only: it is not imported by ``main.py`` and cannot
change the Rule v0 submission policy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import time
from typing import Any, Iterable, Iterator

from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.dataset import RuleBCExample
from mage_ptcg.student.features import ACTION_FEATURE_DIM, FEATURE_VERSION, STATE_FEATURE_DIM, state_features_payload
from mage_ptcg.student.model import _action_feature_vector


GPU_DATASET_SCHEMA = "offline-scaleup-gpu-dataset-v1"
STUDENT_V2_SCHEMA = "offline-scaleup-student-v2"
SPLITS = ("train", "validation", "test", "opponent_holdout", "deck_holdout")
# This is deliberately a conservative BC reweighting, not AWR: the dataset
# has terminal labels but no per-action advantage or learned critic yet.
OUTCOME_SAMPLE_WEIGHTS = {"WIN": 1.25, "DRAW": 1.0, "LOSS": 0.75, "UNKNOWN": 1.0}


class GPUStudentError(ValueError):
    pass


def outcome_sample_weight(value: object) -> float:
    """Return a bounded terminal-outcome weight for experimental BC.

    Missing or unrecognized labels are neutral.  This lets prior datasets
    remain usable without silently treating an unknown result as a win.
    """
    return OUTCOME_SAMPLE_WEIGHTS.get(str(value).upper(), OUTCOME_SAMPLE_WEIGHTS["UNKNOWN"])


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_canonical(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _torch():
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as functional
        from torch.utils.data import DataLoader, Dataset
    except ImportError as exc:  # pragma: no cover - environment guard
        raise GPUStudentError("PyTorch is required for Student v2") from exc
    return torch, nn, functional, DataLoader, Dataset


def probe_environment() -> dict[str, Any]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    available = bool(torch.cuda.is_available())
    payload: dict[str, Any] = {
        "schema_version": "offline-scaleup-gpu-environment-v1",
        "torch_version": torch.__version__, "cuda_available": available,
        "cuda_version": torch.version.cuda, "device_count": torch.cuda.device_count(),
    }
    if available:
        properties = torch.cuda.get_device_properties(0)
        payload.update({"device_name": torch.cuda.get_device_name(0), "compute_capability": list(torch.cuda.get_device_capability(0)),
                        "total_vram_bytes": properties.total_memory, "bf16_supported": bool(torch.cuda.is_bf16_supported())})
    return payload


def _sample_from_row(row: dict[str, Any]) -> tuple[list[float], list[list[float]], int, dict[str, Any]]:
    example = RuleBCExample.from_dict(row["rule_bc_example"])
    try:
        ordered = is_ordered_selection(example.selection_type, example.selection_context)
    except ValueError as exc:
        raise GPUStudentError("dataset example has an unknown CABT selection schema") from exc
    if ordered:
        raise GPUStudentError(
            "candidate-wise GPU Student cannot represent ordered Skill labels"
        )
    state = state_features_payload(example.public_state, example.own_private_state, example.visible_history)
    actions = [_action_feature_vector(action) for action in example.legal_actions]
    targets = [index for index, action in enumerate(example.legal_actions) if action["digest"] in example.target_action_digests]
    if not actions:
        raise GPUStudentError("legal candidate set has no selected teacher action")
    if not targets:
        if example.min_count == 0:
            raise GPUStudentError("optional prompt has no selected action")
        raise GPUStudentError("legal candidate set has no selected teacher action")
    metadata = {key: row.get(key) for key in ("episode_id", "game_id", "split", "candidate_side", "opponent_id", "opponent_type",
        "opponent_deck_fingerprint", "teacher_identity", "teacher_type", "teacher_trust", "family_id", "variant_id",
        "candidate_deck_fingerprint", "state_fingerprint", "candidate_outcome", "provenance")}
    metadata["selection_type"] = str(example.selection_type)
    metadata["decision_id"] = example.example_id
    return state, actions, targets[0], metadata


def _write_shard(path: Path, samples: list[tuple[list[float], list[list[float]], int, dict[str, Any]]]) -> dict[str, Any]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    states, actions, targets, metadata = [], [], [], []
    offsets = [0]
    for state, candidates, target, meta in samples:
        states.append(state); actions.extend(candidates); targets.append(target); metadata.append(meta); offsets.append(offsets[-1] + len(candidates))
    payload = {"schema_version": GPU_DATASET_SCHEMA, "state": torch.tensor(states, dtype=torch.float32),
               "actions": torch.tensor(actions, dtype=torch.float32), "offsets": torch.tensor(offsets, dtype=torch.int64),
               "target": torch.tensor(targets, dtype=torch.int64), "metadata": metadata}
    temporary = path.with_name(path.name + ".tmp")
    torch.save(payload, temporary); os.replace(temporary, path)
    return {"path": path.name, "examples": len(samples), "candidates": len(actions), "sha256": _sha256(path)}


def build_dataset(*, source: Path, output_dir: Path, shard_size: int = 4096, max_records: int | None = None,
                  progress: bool = False) -> dict[str, Any]:
    if shard_size < 1:
        raise GPUStudentError("shard size must be positive")
    source_digest = _sha256(source)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_dataset_sha256") != source_digest:
            raise GPUStudentError("existing GPU dataset has a different source digest")
        return manifest
    output_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[tuple[list[float], list[list[float]], int, dict[str, Any]]]] = {split: [] for split in SPLITS}
    shards: list[dict[str, Any]] = []; parse_failures = 0; illegal_targets = 0; skipped_optional = 0; total = 0
    for line_no, line in enumerate(source.open(encoding="utf-8"), 1):
        if max_records is not None and total >= max_records:
            break
        if not line.strip():
            continue
        try:
            row = json.loads(line); split = row.get("split")
            if split not in by_split:
                continue
            sample = _sample_from_row(row)
        except GPUStudentError as exc:
            if str(exc) == "optional prompt has no selected action":
                skipped_optional += 1; continue
            illegal_targets += 1; continue
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            parse_failures += 1
            if parse_failures <= 5:
                print(f"GPU_DATASET_PARSE_FAILURE line={line_no} error={type(exc).__name__}", flush=True)
            continue
        by_split[split].append(sample); total += 1
        if progress and total % 1000 == 0:
            print(f"PROGRESS phase=build-gpu-dataset completed={total} source_line={line_no}", flush=True)
    for split in SPLITS:
        values = by_split[split]
        if not values:
            raise GPUStudentError(f"GPU dataset has no {split} records")
        for index in range(0, len(values), shard_size):
            name = f"{split}-{index // shard_size:05d}.pt"
            shards.append({"split": split, **_write_shard(output_dir / name, values[index:index + shard_size])})
    episode_split: dict[str, set[str]] = defaultdict(set)
    for split, values in by_split.items():
        for _state, _actions, _target, meta in values:
            episode_split[str(meta["episode_id"])].add(split)
    leakage = sum(1 for memberships in episode_split.values() if len(memberships) > 1)
    manifest = {"schema_version": GPU_DATASET_SCHEMA, "source_dataset": str(source), "source_dataset_sha256": source_digest,
                "feature_schema_version": FEATURE_VERSION, "state_dimension": STATE_FEATURE_DIM, "action_dimension": ACTION_FEATURE_DIM,
                "records": {split: len(values) for split, values in by_split.items()},
                "episodes": {split: len({str(item[3]["episode_id"]) for item in values}) for split, values in by_split.items()},
                "parse_failures": parse_failures, "illegal_targets": illegal_targets, "skipped_optional": skipped_optional, "episode_leakage": leakage,
                "shards": shards, "conversion_limit": max_records, "deterministic_order": "source-jsonl-order"}
    if parse_failures or illegal_targets or leakage:
        raise GPUStudentError("GPU dataset conversion integrity failure")
    _atomic_json(manifest_path, manifest)
    return manifest


def _load_split(dataset_dir: Path, split: str) -> list[dict[str, Any]]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    values: list[dict[str, Any]] = []
    for shard in manifest["shards"]:
        if shard["split"] != split:
            continue
        path = dataset_dir / shard["path"]
        if _sha256(path) != shard["sha256"]:
            raise GPUStudentError(f"shard digest mismatch: {path.name}")
        values.append(torch.load(path, map_location="cpu", weights_only=False))
    if not values:
        raise GPUStudentError(f"GPU dataset split is empty: {split}")
    return values


def _examples(shards: Iterable[dict[str, Any]]) -> Iterator[tuple[Any, Any, int, dict[str, Any]]]:
    for shard in shards:
        for index, meta in enumerate(shard["metadata"]):
            start, end = int(shard["offsets"][index]), int(shard["offsets"][index + 1])
            yield shard["state"][index], shard["actions"][start:end], int(shard["target"][index]), meta


def _collate(batch: list[tuple[Any, Any, int, dict[str, Any]]]) -> dict[str, Any]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    maximum = max(actions.shape[0] for _state, actions, _target, _meta in batch)
    action = torch.zeros((len(batch), maximum, ACTION_FEATURE_DIM), dtype=torch.float32)
    mask = torch.zeros((len(batch), maximum), dtype=torch.bool)
    for index, (_state, actions, _target, _meta) in enumerate(batch):
        action[index, :actions.shape[0]] = actions; mask[index, :actions.shape[0]] = True
    return {"state": torch.stack([item[0] for item in batch]), "actions": action, "mask": mask,
            "target": torch.tensor([item[2] for item in batch], dtype=torch.long), "metadata": [item[3] for item in batch]}


def _model(hidden: int, blocks: int, dropout: float):
    torch, nn, _functional, _loader, _dataset = _torch()
    class Residual(nn.Module):
        def __init__(self) -> None:
            super().__init__(); self.net = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, hidden * 2), nn.GELU(), nn.Dropout(dropout), nn.Linear(hidden * 2, hidden))
        def forward(self, value): return value + self.net(value)
    class CandidateRanker(nn.Module):
        def __init__(self) -> None:
            super().__init__(); self.state = nn.Sequential(nn.Linear(STATE_FEATURE_DIM, hidden), nn.LayerNorm(hidden), nn.GELU())
            self.action = nn.Sequential(nn.Linear(ACTION_FEATURE_DIM, hidden), nn.LayerNorm(hidden), nn.GELU())
            self.blocks = nn.Sequential(*[Residual() for _ in range(blocks)])
            self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Linear(hidden, hidden // 2), nn.GELU(), nn.Linear(hidden // 2, 1))
        def forward(self, state, action, mask):
            encoded_state = self.state(state).unsqueeze(1); encoded_action = self.action(action)
            value = self.blocks(encoded_state + encoded_action + encoded_state * encoded_action)
            return self.head(value).squeeze(-1).masked_fill(~mask, float("-inf"))
    return CandidateRanker()


def _device(requested: str) -> tuple[Any, str]:
    torch, _nn, _functional, _loader, _dataset = _torch()
    if requested.startswith("cuda") and torch.cuda.is_available(): return torch.device(requested), "bf16" if torch.cuda.is_bf16_supported() else "fp16"
    if requested.startswith("cuda"): raise GPUStudentError("CUDA requested but unavailable")
    return torch.device("cpu"), "fp32"


def _gpu_utilization_percent() -> int | None:
    """Best-effort NVML-free snapshot; telemetry absence must not stop training."""
    try:
        completed = subprocess.run(("nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"), check=False,
                                   capture_output=True, text=True, timeout=3)
        if completed.returncode != 0:
            return None
        return int(completed.stdout.strip().splitlines()[0])
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
        return None


def _evaluate(model: Any, values: list[tuple[Any, Any, int, dict[str, Any]]], device: Any, batch_size: int) -> dict[str, Any]:
    torch, _nn, functional, DataLoader, Dataset = _torch()
    class Values(Dataset):
        def __len__(self): return len(values)
        def __getitem__(self, index): return values[index]
    loader = DataLoader(Values(), batch_size=batch_size, shuffle=False, collate_fn=_collate, pin_memory=device.type == "cuda")
    total = correct1 = correct3 = 0; losses: list[float] = []; elapsed: list[float] = []; groups: dict[str, Counter[str]] = defaultdict(Counter)
    model.eval()
    with torch.no_grad():
        for batch in loader:
            started = time.perf_counter_ns(); state=batch["state"].to(device); action=batch["actions"].to(device); mask=batch["mask"].to(device); target=batch["target"].to(device)
            scores=model(state, action, mask); loss=functional.cross_entropy(scores, target, reduction="none"); order=scores.argsort(dim=1, descending=True, stable=True)
            elapsed.extend([(time.perf_counter_ns()-started)/len(target)/1000.0] * len(target)); losses.extend(loss.cpu().tolist())
            for index, meta in enumerate(batch["metadata"]):
                hit1=int(order[index,0].item()==target[index].item()); hit3=int(target[index].item() in order[index,:3].tolist()); total += 1; correct1 += hit1; correct3 += hit3
                for field in ("selection_type", "candidate_side", "opponent_type", "opponent_id", "opponent_deck_fingerprint", "teacher_identity", "teacher_type", "family_id"):
                    groups[field][str(meta.get(field, "UNKNOWN")) + "|examples"] += 1; groups[field][str(meta.get(field, "UNKNOWN")) + "|top1"] += hit1
    quantile=lambda q: sorted(elapsed)[min(len(elapsed)-1, math.ceil(len(elapsed)*q)-1)]
    return {"examples": total, "episodes": len({str(value[3].get("episode_id")) for value in values}), "loss": sum(losses)/len(losses),
            "teacher_top1_fidelity": correct1/total, "teacher_top3_fidelity": correct3/total, "legal_action_rate": 1.0, "fallback_rate": 0.0,
            "latency_us_p50": quantile(.50), "latency_us_p95": quantile(.95), "latency_us_p99": quantile(.99),
            "by": {field:{key[:-9]:{"examples": count, "top1": groups[field][key[:-9]+"|top1"]/count} for key,count in values.items() if key.endswith("|examples")} for field,values in groups.items()}}


def train(*, dataset_dir: Path, output_dir: Path, device_name: str, epochs: int, batch_size: int, workers: int,
          hidden: int = 256, blocks: int = 3, dropout: float = .05, learning_rate: float = 3e-4,
          accumulation: int = 1, seed: int = 71000, resume: bool = False, max_train: int | None = None,
          max_validation: int | None = None, trust_weighting: bool = False, action_balancing: bool = False,
          outcome_weighting: bool = False,
          early_stopping_patience: int = 5, progress: bool = False) -> dict[str, Any]:
    torch, _nn, functional, DataLoader, Dataset = _torch(); device, dtype = _device(device_name)
    random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed) if device.type == "cuda" else None
    train_values=list(_examples(_load_split(dataset_dir,"train"))); validation_values=list(_examples(_load_split(dataset_dir,"validation")))
    if max_train: train_values=train_values[:max_train]
    if max_validation: validation_values=validation_values[:max_validation]
    if early_stopping_patience < 1:
        raise GPUStudentError("early stopping patience must be positive")
    trust_weights={"TRUSTED":1.0,"LIMITED":0.5}
    action_counts=Counter(str(value[3].get("selection_type", "UNKNOWN")) for value in train_values)
    if action_balancing:
        total_actions=sum(action_counts.values()); categories=len(action_counts)
        action_weights={name: total_actions/(categories*count) for name,count in action_counts.items()}
    else:
        action_weights={name:1.0 for name in action_counts}
    class Values(Dataset):
        def __len__(self): return len(train_values)
        def __getitem__(self,index): return train_values[index]
    generator=torch.Generator(); generator.manual_seed(seed)
    loader=DataLoader(Values(),batch_size=batch_size,shuffle=True,num_workers=workers,collate_fn=_collate,pin_memory=device.type=="cuda",persistent_workers=workers>0,generator=generator)
    model=_model(hidden,blocks,dropout).to(device); optimizer=torch.optim.AdamW(model.parameters(),lr=learning_rate)
    output_dir.mkdir(parents=True,exist_ok=True); last=output_dir/"last.pt"; best=output_dir/"best.pt"; start_epoch=0; best_score=float("-inf"); epochs_without_improvement=0; resumed=False
    if resume and last.exists():
        checkpoint=torch.load(last,map_location=device,weights_only=False); model.load_state_dict(checkpoint["model"]); optimizer.load_state_dict(checkpoint["optimizer"]); start_epoch=int(checkpoint["epoch"])+1; best_score=float(checkpoint.get("best_score",best_score)); epochs_without_improvement=int(checkpoint.get("epochs_without_improvement",0)); resumed=True
    autocast=lambda: torch.autocast(device_type="cuda",dtype=torch.bfloat16 if dtype=="bf16" else torch.float16) if device.type=="cuda" else torch.autocast(device_type="cpu",enabled=False)
    metrics_lines=[]; stopped_early=False
    for epoch in range(start_epoch, epochs):
        model.train(); started=time.perf_counter(); seen=0; total_loss=0.; optimizer.zero_grad(set_to_none=True)
        for step,batch in enumerate(loader):
            state=batch["state"].to(device,non_blocking=True); action=batch["actions"].to(device,non_blocking=True); mask=batch["mask"].to(device,non_blocking=True); target=batch["target"].to(device,non_blocking=True)
            with autocast():
                scores=model(state,action,mask)
                per_example=functional.cross_entropy(scores,target,reduction="none")
                weights=torch.tensor([
                    (trust_weights.get(str(meta.get("teacher_trust")), 0.0) if trust_weighting else 1.0)
                    * action_weights.get(str(meta.get("selection_type")), 1.0)
                    * (outcome_sample_weight(meta.get("candidate_outcome")) if outcome_weighting else 1.0)
                    for meta in batch["metadata"]
                ], device=device, dtype=per_example.dtype)
                if not bool((weights.sum() > 0).item()):
                    raise GPUStudentError(f"non-positive training weight at epoch={epoch} step={step}")
                loss=(per_example*weights).sum()/weights.sum()/accumulation
            if not bool(torch.isfinite(loss).item()):
                raise GPUStudentError(f"non-finite training loss at epoch={epoch} step={step}")
            loss.backward()
            if (step+1)%accumulation==0 or step+1==len(loader): torch.nn.utils.clip_grad_norm_(model.parameters(),1.0); optimizer.step(); optimizer.zero_grad(set_to_none=True)
            seen += len(target); total_loss += float(loss.item())*accumulation*len(target)
        validation=_evaluate(model,validation_values,device,batch_size)
        if not math.isfinite(validation["loss"]):
            raise GPUStudentError(f"non-finite validation loss at epoch={epoch}")
        epoch_metrics={"epoch":epoch,"train_loss":total_loss/seen,"epoch_seconds":time.perf_counter()-started,"examples_per_second":seen/(time.perf_counter()-started),"validation":validation}
        if device.type=="cuda":
            epoch_metrics["peak_allocated_vram_bytes"]=torch.cuda.max_memory_allocated(device)
            epoch_metrics["gpu_utilization_percent"]=_gpu_utilization_percent()
            torch.cuda.reset_peak_memory_stats(device)
        improved=validation["teacher_top1_fidelity"] >= best_score
        epochs_without_improvement=0 if improved else epochs_without_improvement+1
        metrics_lines.append(epoch_metrics); checkpoint={"schema_version":STUDENT_V2_SCHEMA,"epoch":epoch,"model":model.state_dict(),"optimizer":optimizer.state_dict(),"best_score":max(best_score,validation["teacher_top1_fidelity"]),"epochs_without_improvement":epochs_without_improvement,"config":{"hidden":hidden,"blocks":blocks,"dropout":dropout}}
        torch.save(checkpoint,last)
        if improved: best_score=validation["teacher_top1_fidelity"]; torch.save(checkpoint,best)
        if progress: print(f"PROGRESS phase=train-student-v2 epoch={epoch+1}/{epochs} validation_top1={validation['teacher_top1_fidelity']:.4f}",flush=True)
        if epochs_without_improvement >= early_stopping_patience:
            stopped_early=True; break
    config={"schema_version":STUDENT_V2_SCHEMA,"feature_schema_version":FEATURE_VERSION,"state_dimension":STATE_FEATURE_DIM,"action_dimension":ACTION_FEATURE_DIM,"hidden":hidden,"blocks":blocks,"dropout":dropout,"device":str(device),"compute_dtype":dtype,"batch_size":batch_size,"gradient_accumulation":accumulation,"workers":workers,"seed":seed,"resumed_from_checkpoint":resumed,"trust_weighting":trust_weighting,"action_balancing":action_balancing,"outcome_weighting":outcome_weighting,"teacher_trust_weights":trust_weights if trust_weighting else {"DEFAULT":1.0},"action_type_weights":action_weights,"candidate_outcome_weights":OUTCOME_SAMPLE_WEIGHTS if outcome_weighting else {"DEFAULT":1.0},"early_stopping_patience":early_stopping_patience,"stopped_early":stopped_early,"dataset_manifest_sha256":_sha256(dataset_dir/"manifest.json"),"best_checkpoint_sha256":_sha256(best),"best_validation_top1":best_score,"epochs_completed":len(metrics_lines)+start_epoch,"metrics":metrics_lines}
    _atomic_json(output_dir/"training_summary.json",config); _atomic_json(output_dir/"student_v2_config.json",config); return config


def evaluate(*, dataset_dir: Path, model_dir: Path, output: Path, device_name: str, batch_size: int) -> dict[str, Any]:
    torch, _nn, _functional, _loader, _dataset = _torch(); device,_dtype=_device(device_name); summary=json.loads((model_dir/"training_summary.json").read_text()); cfg=summary
    checkpoint=torch.load(model_dir/"best.pt",map_location=device,weights_only=False); model=_model(int(cfg["hidden"]),int(cfg["blocks"]),float(cfg["dropout"])).to(device); model.load_state_dict(checkpoint["model"])
    results={split:_evaluate(model,list(_examples(_load_split(dataset_dir,split))),device,batch_size) for split in SPLITS}
    report={"schema_version":STUDENT_V2_SCHEMA,"model_digest":_sha256(model_dir/"best.pt"),"model_size_bytes":(model_dir/"best.pt").stat().st_size,"device":str(device),"splits":results,"gpu_cpu_consistency":"not_compared"}
    if device.type=="cuda":
        cpu_model=_model(int(cfg["hidden"]),int(cfg["blocks"]),float(cfg["dropout"])); cpu_model.load_state_dict(checkpoint["model"]); sample=list(_examples(_load_split(dataset_dir,"validation")))[:32]
        gpu=_evaluate(model,sample,device,batch_size); cpu=_evaluate(cpu_model,sample,torch.device("cpu"),batch_size)
        batch=_collate(sample)
        with torch.no_grad():
            gpu_order=model(batch["state"].to(device),batch["actions"].to(device),batch["mask"].to(device)).argsort(dim=1,descending=True,stable=True).cpu()
            cpu_order=cpu_model(batch["state"],batch["actions"],batch["mask"]).argsort(dim=1,descending=True,stable=True)
        agreement=sum(int(gpu_order[index,0]==cpu_order[index,0]) for index in range(len(sample)))/len(sample)
        report["gpu_cpu_consistency"]={"top1_agreement_rate":agreement,"top3_metric_delta":gpu["teacher_top3_fidelity"]-cpu["teacher_top3_fidelity"],"sample_examples":len(sample)}
    _atomic_json(output,report); return report


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(prog="gpu-student-v2"); sub=parser.add_subparsers(dest="command",required=True)
    probe=sub.add_parser("probe"); probe.add_argument("--output",type=Path,required=True)
    build=sub.add_parser("build-dataset"); build.add_argument("--source",type=Path,required=True); build.add_argument("--output-dir",type=Path,required=True); build.add_argument("--shard-size",type=int,default=4096); build.add_argument("--max-records",type=int); build.add_argument("--progress",action="store_true")
    train_p=sub.add_parser("train"); train_p.add_argument("--dataset-dir",type=Path,required=True); train_p.add_argument("--output-dir",type=Path,required=True); train_p.add_argument("--device",default="cuda"); train_p.add_argument("--epochs",type=int,default=40); train_p.add_argument("--batch-size",type=int,default=256); train_p.add_argument("--workers",type=int,default=4); train_p.add_argument("--hidden",type=int,default=256); train_p.add_argument("--blocks",type=int,default=3); train_p.add_argument("--dropout",type=float,default=.05); train_p.add_argument("--learning-rate",type=float,default=3e-4); train_p.add_argument("--accumulation",type=int,default=1); train_p.add_argument("--seed",type=int,default=71000); train_p.add_argument("--resume",action="store_true"); train_p.add_argument("--max-train",type=int); train_p.add_argument("--max-validation",type=int); train_p.add_argument("--trust-weighting",action="store_true"); train_p.add_argument("--action-balancing",action="store_true"); train_p.add_argument("--outcome-weighting",action="store_true"); train_p.add_argument("--early-stopping-patience",type=int,default=5); train_p.add_argument("--progress",action="store_true")
    ev=sub.add_parser("evaluate"); ev.add_argument("--dataset-dir",type=Path,required=True); ev.add_argument("--model-dir",type=Path,required=True); ev.add_argument("--output",type=Path,required=True); ev.add_argument("--device",default="cuda"); ev.add_argument("--batch-size",type=int,default=256)
    args=parser.parse_args(argv)
    try:
        if args.command=="probe": result=probe_environment(); _atomic_json(args.output,result)
        elif args.command=="build-dataset": result=build_dataset(source=args.source,output_dir=args.output_dir,shard_size=args.shard_size,max_records=args.max_records,progress=args.progress)
        elif args.command=="train": result=train(dataset_dir=args.dataset_dir,output_dir=args.output_dir,device_name=args.device,epochs=args.epochs,batch_size=args.batch_size,workers=args.workers,hidden=args.hidden,blocks=args.blocks,dropout=args.dropout,learning_rate=args.learning_rate,accumulation=args.accumulation,seed=args.seed,resume=args.resume,max_train=args.max_train,max_validation=args.max_validation,trust_weighting=args.trust_weighting,action_balancing=args.action_balancing,outcome_weighting=args.outcome_weighting,early_stopping_patience=args.early_stopping_patience,progress=args.progress)
        else: result=evaluate(dataset_dir=args.dataset_dir,model_dir=args.model_dir,output=args.output,device_name=args.device,batch_size=args.batch_size)
        print(_canonical(result)); return 0
    except (GPUStudentError,OSError,ValueError,RuntimeError) as exc:
        print(_canonical({"error":type(exc).__name__,"message":str(exc)})); return 2


if __name__ == "__main__": raise SystemExit(main())
