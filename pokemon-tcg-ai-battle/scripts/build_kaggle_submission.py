"""Build public-safe local Kaggle candidate packages; never submits them."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/"src")]
from scripts.build_submission import build_submission
from scripts.build_student_submission import KAGGLE_STUDENT_RUNTIME_PATHS, build_student_submission
from scripts.build_cg_kaggle_submission import build_cg_package
from scripts.kaggle_student_entrypoint import render_student_cabt_trace, render_student_entrypoint, render_student_package_init, render_student_runtime_model
from mage_ptcg.student.artifact import load_validated_artifact
def main(argv=None)->int:
 p=argparse.ArgumentParser();p.add_argument("--config",type=Path,required=True);a=p.parse_args(argv)
 try:
  c=json.loads(a.config.read_text());kind=c["agent_kind"];out=ROOT/c["output_dir"]
  if c.get("schema_version")!="kaggle-agent-package-config-v1" or kind not in {"rule","student","cg"}:raise ValueError("config_invalid")
  if out.exists():raise ValueError("output_exists")
  source_head=__import__("subprocess").check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
  if kind=="cg":
   source_candidate=ROOT/c["source_candidate"]
   result=build_cg_package(out,source_candidate=source_candidate,contract=c.get("contract"),competition_slug=c["competition_slug"],source_head=source_head); model={}
  elif kind=="rule": result=build_submission(out); model={}
  else:
   art=ROOT/c["model_artifact"];_,m=load_validated_artifact(art/"student-v0.json",art/"manifest.json")
   if m.get("artifact_purpose")!="ACTUAL_TRAINED" or m.get("performance_eligible") is not True:raise ValueError("actual_trained_required")
   model={"model_hash":m["model_hash"],"artifact_purpose":"ACTUAL_TRAINED","performance_eligible":True,"fallback_policy":"Rule Agent v0"}
   embedded={"schema_version":"kaggle-student-package-model-v1","agent_kind":"student","entrypoint":"main.py",**model}
   result=build_student_submission(art/"student-v0.json",out,runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,generated_main=render_student_entrypoint().encode(),generated_files={"src/mage_ptcg/student/__init__.py":render_student_package_init().encode(),"src/mage_ptcg/student/model.py":render_student_runtime_model((ROOT/"src/mage_ptcg/student/model.py").read_text()).encode(),"src/mage_ptcg/observability/cabt_trace.py":render_student_cabt_trace((ROOT/"src/mage_ptcg/observability/cabt_trace.py").read_text()).encode()},extra_files={"student-model-manifest.json":(art/"manifest.json").read_bytes(),"student-package-manifest.json":json.dumps(embedded,sort_keys=True).encode()+b"\n"})
  deck=hashlib.sha256((out/"deck.csv").read_bytes()).hexdigest();manifest={"schema_version":"kaggle-agent-package-v1","agent_kind":kind,"competition_slug":c["competition_slug"],"entrypoint":"main.py","deck_hash":deck,"source_head":source_head,"private_artifacts_included":False,"contract":c.get("contract"),**model,"builder_result":result}
  (out/"kaggle-package-manifest.json").write_text(json.dumps(manifest,sort_keys=True)+"\n");print(json.dumps({"status":"BUILT","artifact":str(out/"submission.tar.gz"),"package_manifest":manifest},sort_keys=True));return 0
 except Exception as e:print(json.dumps({"status":"BLOCKED","reason":type(e).__name__}));return 2
if __name__=="__main__":raise SystemExit(main())
