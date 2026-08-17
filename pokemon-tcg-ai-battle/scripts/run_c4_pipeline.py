"""Resumable, fail-closed orchestration for the existing C4 actual pipeline."""
from __future__ import annotations

import argparse, hashlib, json, subprocess, sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from mage_ptcg.student.artifact import load_validated_artifact
from scripts.accept_c4_actual_training_bundle import accept_bundle, train_bundle
from scripts.export_c4_actual_training_bundle import export_bundle
from mage_ptcg.dataops import collect_actual_dataset, validate_run
from scripts.run_actual_agent_viability import run_actual_agent_viability

STAGES = ("collect", "export", "validate", "train", "evaluate", "build-model", "gate-a", "gate-b")
EXIT_INPUT, EXIT_GATE, EXIT_PRIVACY, EXIT_RUNTIME = 2, 3, 4, 5

class PipelineError(ValueError): pass

def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise PipelineError("config_or_artifact_invalid") from exc
    if not isinstance(value, dict): raise PipelineError("config_or_artifact_invalid")
    return value

def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def _config(path: Path) -> dict[str, Any]:
    cfg = _json(path)
    if cfg.get("schema_version") != "c4-pipeline-config-v1" or not isinstance(cfg.get("run_id"), str): raise PipelineError("config_schema_invalid")
    if cfg.get("training", {}).get("device") != "cpu": raise PipelineError("only_cpu_training_is_supported")
    if cfg.get("teacher") != {"source":"Rule Agent v0", "quality":"RULE_ONLY", "objective":"RULE_IMITATION"}: raise PipelineError("teacher_contract_invalid")
    c = cfg.get("collector", {}); g = cfg.get("gates", {})
    if not (isinstance(c, dict) and type(c.get("requested_games")) is int and type(c.get("maximum_games")) is int and c["requested_games"] <= c["maximum_games"] <= 64): raise PipelineError("collector_config_invalid")
    if not (isinstance(g, dict) and g.get("gate_b_games") == 20): raise PipelineError("gate_config_invalid")
    return cfg

def _paths(cfg: dict[str, Any]) -> dict[str, Path]:
    a = cfg.get("artifacts", {})
    if not isinstance(a, dict): raise PipelineError("artifact_config_invalid")
    collection = ROOT / str(a.get("collection_root", ".local_artifacts/c4_runs")) / cfg["run_id"]
    training = ROOT / str(a.get("training_root", ".local_artifacts/c4_actual_training/training"))
    pipe = ROOT / str(a.get("pipeline_root", ".local_artifacts/c4_actual_training/pipeline"))
    return {"collection":collection,"bundle":training.parent / "bundle","training":training,"pipe":pipe,"model":training / "artifact" / "student-v0.json","manifest":training / "artifact" / "manifest.json","gate_a":training.parent / "gate-a.json","gate_b":training.parent / "gate-b-20.json"}

def _state(path: Path, cfg_hash: str) -> dict[str, Any]:
    if not path.exists(): return {"config_hash":cfg_hash,"completed":{},"schema_version":"c4-pipeline-state-v1"}
    state = _json(path)
    if state.get("config_hash") != cfg_hash: raise PipelineError("config_mismatch")
    return state

def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(state, sort_keys=True)+"\n", encoding="utf-8")

def _summary(stage: str, value: object) -> dict[str, Any]: return {"stage":stage,"result":value}

def _run_stage(stage: str, cfg: dict[str, Any], p: dict[str, Path]) -> dict[str, Any]:
    if stage == "collect":
        summary_path = p["collection"] / "public_summary.json"
        if summary_path.exists():
            s = _json(summary_path)
            if s.get("artifact_purpose") == "ACTUAL_TRAINING" and s.get("performance_eligible") is True: return _summary(stage, s)
        c=cfg["collector"]; games=c["requested_games"]
        while True:
            s=collect_actual_dataset(run_id=cfg["run_id"], games=games, base_seed=2000, output_root=p["collection"].parent, canonical_base_sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(), deck_path=ROOT/"deck.csv", repository_root=ROOT)
            if s.get("performance_eligible") is True or games >= c["maximum_games"]: return _summary(stage,s)
            games=min(games+16,c["maximum_games"])
    if stage == "export":
        if (p["bundle"] / "dataset_manifest.json").exists(): return _summary(stage, accept_bundle(p["bundle"]).public_result())
        return _summary(stage, export_bundle(run_root=p["collection"], output_root=p["bundle"], require_actual_training=True))
    if stage == "validate": return _summary(stage, accept_bundle(p["bundle"]).public_result())
    if stage == "train":
        if (p["training"] / "acceptance.json").exists(): return _summary(stage, _json(p["training"] / "acceptance.json"))
        return _summary(stage, train_bundle(accept_bundle(p["bundle"]), p["training"]))
    if stage in {"evaluate", "build-model"}:
        _model, manifest=load_validated_artifact(p["model"],p["manifest"])
        if manifest.get("artifact_purpose") != "ACTUAL_TRAINED" or manifest.get("performance_eligible") is not True: raise PipelineError("actual_model_required")
        return _summary(stage, {"model_hash":manifest["model_hash"],"validation":manifest.get("validation_metrics"),"verified":True})
    if stage in {"gate-a","gate-b"}:
        target=p["gate_a"] if stage == "gate-a" else p["gate_b"]
        if target.exists():
            s=_json(target)
            if s.get("gate_status") == "CLEAN_PASS" and s.get("challenger_metrics",{}).get("model_hash"): return _summary(stage,s)
        games=1 if stage == "gate-a" else 20
        return _summary(stage, run_actual_agent_viability(challenger_id="student",games=games,base_seed=3200 if games==1 else 3300,output_path=target,canonical_base_sha=_json(p["bundle"] / "dataset_manifest.json")["canonical_base_sha"],student_model_path=p["model"],student_manifest_path=p["manifest"]))
    raise PipelineError("unknown_stage")

def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--config",type=Path,required=True); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--from-stage",choices=STAGES); parser.add_argument("--until-stage",choices=STAGES); parser.add_argument("--force-stage",choices=STAGES); parser.add_argument("--confirm-force-stage",action="store_true"); parser.add_argument("command",choices=("status",*STAGES,"run")); a=parser.parse_args(argv)
    try:
        cfg=_config(a.config); p=_paths(cfg); h=_digest(cfg); state_path=p["pipe"] / "pipeline_state.json"; state=_state(state_path,h)
        requested=list(STAGES if a.command=="run" else (() if a.command=="status" else (a.command,)))
        if a.from_stage: requested=requested[STAGES.index(a.from_stage):]
        if a.until_stage: requested=requested[:requested.index(a.until_stage)+1]
        if a.force_stage and not a.confirm_force_stage: raise PipelineError("force_stage_requires_confirm_force_stage")
        if a.force_stage: state["completed"].pop(a.force_stage,None)
        if a.command=="status" or a.dry_run:
            print(json.dumps({"status":"DRY_RUN" if a.dry_run else "STATUS","planned":requested,"completed":state["completed"],"config_hash":h},sort_keys=True)); return 0
        results=[]
        for stage in requested:
            if stage in state["completed"]: results.append({"stage":stage,"status":"RESUMED","result":state["completed"][stage]}); continue
            result=_run_stage(stage,cfg,p)
            if stage == "collect" and result["result"].get("performance_eligible") is not True: raise PipelineError("collection_not_eligible")
            if stage.startswith("gate") and result["result"].get("gate_status") != "CLEAN_PASS": raise PipelineError("gate_not_clean_pass")
            state["completed"][stage]=result; _save(state_path,state); results.append({"stage":stage,"status":"COMPLETED","result":result})
        print(json.dumps({"status":"PASS","results":results,"config_hash":h},sort_keys=True)); return 0
    except PipelineError as exc: print(json.dumps({"status":"BLOCKED","reason":str(exc)},sort_keys=True)); return EXIT_GATE if "eligible" in str(exc) or "gate" in str(exc) else EXIT_INPUT
    except Exception as exc: print(json.dumps({"status":"RUNTIME_FAILURE","reason":type(exc).__name__},sort_keys=True)); return EXIT_RUNTIME
if __name__=="__main__": raise SystemExit(main())
