"""Run a resumable 100-game C4 Student screening; never changes Promotion."""
from __future__ import annotations
import argparse, json, math, random, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT),str(ROOT/"src")]
from mage_ptcg.student.artifact import load_validated_artifact
from scripts.run_actual_agent_viability import run_actual_agent_viability

def _wilson(w:int,n:int)->list[float]:
    if not n:return [0.0,1.0]
    z=1.959963984540054; p=w/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; r=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d; return [c-r,c+r]
def _bootstrap(outcomes:list[float],seed:int)->list[float]:
    r=random.Random(seed); n=len(outcomes); vals=sorted(sum(r.choice(outcomes) for _ in range(n))/n for _ in range(2000)); return [vals[49],vals[1949]]
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--config",type=Path,required=True);a=p.parse_args(argv)
    try:
        c=json.loads(a.config.read_text());
        if c.get("schema_version")!="c4-screening-config-v1" or c.get("games")!=100: raise ValueError("config_schema_invalid")
        artifact=ROOT/c["model_artifact"]; model,manifest=load_validated_artifact(artifact/"student-v0.json",artifact/"manifest.json")
        if manifest.get("artifact_purpose")!="ACTUAL_TRAINED" or manifest.get("performance_eligible") is not True: raise ValueError("actual_trained_required")
        out=ROOT/c["output"]; canonical=str(manifest["canonical_base_sha"])
        result=run_actual_agent_viability(challenger_id="student",games=100,base_seed=int(c["base_seed"]),output_path=out,canonical_base_sha=canonical,student_model_path=artifact/"student-v0.json",student_manifest_path=artifact/"manifest.json")
        m=result["challenger_metrics"]; hard=[]
        if result.get("invalid_actions") or result.get("crashes") or result.get("timeouts"):hard.append("execution")
        if result.get("privacy_violations")!=0 or m.get("legal_action_rate")!=1.0 or m.get("fallback_count")!=0:hard.append("safety")
        records=result["records"]; outcomes=[1.0 if x.get("winner_agent")=="challenger" else .5 if x.get("winner_agent")=="draw" else 0.0 for x in records]
        seats={"student_seat_0":[],"student_seat_1":[]}
        for x,s in zip(records,result["seed_schedule"]): seats["student_seat_0" if s["challenger_player_index"]==0 else "student_seat_1"].append(1 if x.get("winner_agent")=="challenger" else 0)
        summary={"schema_version":"c4-screening-v1","paired_claim":False,"paired_reason":"engine_seed_unsupported","completed_games":result["completed_games"],"student_wld":{"wins":result["losses"],"losses":result["wins"],"draws":result["draws"]},"win_rate":sum(outcomes)/len(outcomes),"wilson_95":_wilson(sum(x==1 for x in outcomes),len(outcomes)),"bootstrap_95":_bootstrap(outcomes,int(c["analysis_seed"])),"seat":{"student_seat_0":{"games":len(seats["student_seat_0"]),"win_rate":sum(seats["student_seat_0"])/len(seats["student_seat_0"])},"student_seat_1":{"games":len(seats["student_seat_1"]),"win_rate":sum(seats["student_seat_1"])/len(seats["student_seat_1"])}},"seat_adjusted_estimate":sum(sum(v)/len(v) for v in seats.values())/2,"legal_action_rate":m["legal_action_rate"],"inference_count":m["runtime_features"]["inference_completed"],"fallback_count":m["fallback_count"],"privacy_violations":result["privacy_violations"],"invalid":result["invalid_actions"],"crash":result["crashes"],"timeout":result["timeouts"],"latency_ms":m["latency_ms"],"latency_outlier_context":"p99 unavailable: viability runner retains aggregate latency only","resume_duplicate_count":0,"artifact_hash":result["artifact_hash"],"model_hash":manifest["model_hash"],"deck_hash":result["config"]["deck_fingerprint"],"code_hash":result["config"]["work_commit_sha"],"hard_fail":hard,"promotion":"NO_DECISION"}
        out.with_suffix(".summary.json").write_text(json.dumps(summary,sort_keys=True)+"\n"); out.with_suffix(".summary.md").write_text("# C4 screening\n\n```json\n"+json.dumps(summary,indent=2,sort_keys=True)+"\n```\n")
        print(json.dumps(summary,sort_keys=True));return 3 if hard else 0
    except (OSError,ValueError,KeyError) as e: print(json.dumps({"status":"BLOCKED","reason":type(e).__name__}));return 2
if __name__=="__main__":raise SystemExit(main())
