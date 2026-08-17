"""Fail-closed Team-reference identity and privacy-boundary assessment.

Existing O6 native workers accept raw CABT observations.  They are therefore
not eligible for this iteration's stricter ActorInformationView-only IPC
contract until a projection adapter is supplied and independently tested.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence
from mage_ptcg.optimization.core import canonical, digest

SCHEMA = "team-reference-isolated-optimization-v1"
STORE = Path("/home/bfe-lab-ono/kaggle/opponent-artifacts/store/snapshots/team-agents-v1-f4c8f9b87ae6601a")

def _json(p: Path) -> Any: return json.loads(p.read_text())
def _sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()
def _csv(p: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({k for x in rows for k in x})
    with p.open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for x in rows:w.writerow({k:canonical(v) if isinstance(v,(dict,list)) else v for k,v in x.items()})

def identities() -> list[dict[str, object]]:
    manifest, specs, decks = (_json(STORE/'population_manifest.json'), _json(STORE/'opponent_specs.json'), _json(STORE/'deck_registry.json'))
    by={str(x['deck_id']):x for x in decks}; out=[]
    for s in specs:
        aid=str(s['agent_id']); source=STORE/'runtime'/aid/'source'; main=source/'main.py'; deck=by[str(s['deck_id'])]
        row={"lineage_id":"team-native-"+aid[:16],"team_agent_id":aid,"display_name":"Pinned Team Native "+aid[:12],"category":"TEAM_REFERENCE_LINEAGE","package":str(source),"package_sha256":_sha(STORE/'bundle.tar.gz'),"source_code_digest":_sha(main),"model_digest":None,"config_digest":digest({"adapter":s['adapter_version'],"contract":s['runtime_contract']},"team-config-v1"),"adapter_digest":_sha(STORE/'runtime'/aid/'adapter.json'),"fallback_config":"NONE_PER_EXISTING_NATIVE_CONTRACT","required_deck":s['deck_id'],"deck_hash":deck.get('deck_hash'),"supported_decision_classes":"NOT_AUDITABLE_WITHOUT_PROJECTED_FIXTURES","public_information_boundary":"BLOCKED_RAW_OBSERVATION_IPC","subprocess_entrypoint":_json(STORE/'runtime'/aid/'adapter.json')['entrypoint'],"python_version":"WORKER_SYS_EXECUTABLE_UNPINNED","dependency":"PINNED_RUNTIME_BUNDLE","historical_result":"O6 historical validation PASS only","population_role":"TEAM_BLOCKED","provenance":{"manifest_hash":manifest['manifest_hash'],"source_commits":manifest['source_commit_shas']},"private_status":"TEAM_INTERNAL","qualification_status":"TEAM_REFERENCE_BLOCKED_PRIVACY","blocker":"existing worker IPC forwards raw observation; ActorInformationView-only projection is not enforced"}
        row['behavior_fingerprint']=digest({"source":row['source_code_digest'],"adapter":row['adapter_digest'],"deck":row['deck_hash']},"team-behavior-provisional-v1"); row['checksum']=digest(row,"team-identity-v1");out.append(row)
    return out

def materialize(out: Path, *, initial_head: str) -> dict[str, object]:
    out.mkdir(parents=True,exist_ok=True)
    for n in ('team_identity','team_fingerprints','subprocess','qualification','team_panel','search_population','search_trace','failure_clusters','candidates','search','team_validation','team_holdout','comparison','tests','evidence','git_start','git_end','workspace_comparison'):(out/n).mkdir(exist_ok=True)
    ids=identities(); head=subprocess.run(['git','rev-parse','HEAD'],text=True,capture_output=True,check=True).stdout.strip()
    request='''# Team Reference Package Request\n\n現行bundleはraw CABT observationをworkerへ渡すため、ActorInformationView-only IPCを保証できません。資格審査再開には各Team referenceについて次を提供してください。\n\n- exact archive と SHA-256、source commit、submission/reference ID\n- exact 60-card Deck と deck hash、policy/model/config identity\n- Python version、dependency lock、subprocess entrypoint、fallback、local launch command\n- ActorInformationView-only request schema と response schema、hidden-field rejection test\n- public score observation timestamp と privacy/sharing scope\n'''
    final={"overall_status":"TEAM_REFERENCE_PRIVACY_BLOCKED","branch":subprocess.run(['git','branch','--show-current'],text=True,capture_output=True,check=True).stdout.strip(),"initial_head":initial_head,"final_head":head,"local_commits_created":[],"push_executed":False,"upstream_configured":False,"team_native_assets":len(ids),"team_distinct_lineages":0,"team_duplicate_lineages":0,"team_qualified_lineages":0,"team_blocked_lineages":len(ids),"team_validation_lineages":0,"team_holdout_lineages":0,"team_holdout_available":False,"qualification_games":0,"search_population_policies":0,"synthetic_stress_policies":0,"search_trace_games":0,"search_trace_decisions":0,"search_failure_clusters":0,"new_candidates_generated":0,"new_candidates_static_passed":0,"new_candidates_screened":0,"new_candidates_search_positive":0,"new_candidates_team_validation_passed":0,"new_candidates_team_holdout_passed":0,"candidate_search_games":0,"team_validation_games":0,"team_holdout_games":0,"best_candidate_id":None,"best_search_delta":None,"best_team_validation_delta":None,"best_team_holdout_delta":None,"best_candidate_status":None,"safety_gate_passed":False,"team_reference_request_required":True,"rule_v0_changed":False,"champion_changed":False,"kaggle_submission_executed":False,"ten_thousand_games_executed":False,"critical_blockers":["raw-observation Team IPC cannot prove ActorInformationView-only boundary"],"high_risks":["do not substitute Rule fallback for Team worker faults"],"next_5_actions":["obtain projected-IPC package contract","run fixture/privacy qualification before Team evaluation"],"changed_files":[],"artifact_root":str(out)}
    _csv(out/'team_identity_registry.csv',ids);_csv(out/'team_behavior_fingerprint.csv',ids);_csv(out/'team_qualification_registry.csv',ids)
    for n in ('search_population_registry.csv','search_trace_registry.csv','failure_cluster_registry.csv','candidate_registry.csv','team_validation_registry.csv','team_holdout_registry.csv','team_comparison_matrix.csv'):_csv(out/n,[])
    (out/'team_identity'/'registry.json').write_text(canonical(ids)+'\n');(out/'qualification'/'results.json').write_text(canonical(ids)+'\n');(out/'team_reference_panel.json').write_text(canonical([])+'\n');(out/'team_role_assignment.json').write_text(canonical({"status":"NO_QUALIFIED_TEAM_REFERENCE"})+'\n');(out/'21_team_reference_request_packet.md').write_text(request)
    titles=['executive_summary','repository_start_state','previous_lineage_result','team_native_identity','team_behavior_distinctness','subprocess_runtime_boundary','team_information_boundary','team_qualification_protocol','team_qualification_results','team_reference_panel','team_role_assignment','search_population','synthetic_stress','search_trace_collection','search_failure_clusters','candidate_generation','candidate_search','team_validation','team_sealed_holdout','team_baseline_matrix','team_comparison_matrix','team_reference_request_packet','safety_and_runtime','statistical_analysis','test_report','failure_and_limitations','created_local_commits','next_iteration']
    for i,t in enumerate(titles):
        p=out/f'{i:02d}_{t}.md'
        if not p.exists():p.write_text(f'# {t}\n\nBlocked/Not executed is not PASS.\n')
    (out/'28_final_readiness.json').write_text(canonical(final)+'\n');(out/'final_readiness.json').write_text(canonical(final)+'\n');(out/'artifact_manifest.json').write_text(canonical({'schema':SCHEMA,'final':final})+'\n');(out/'commands.log').write_text('PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.opponents team-reference-v1 ...\n');(out/'git_start'/'head.txt').write_text(initial_head+'\n');(out/'git_end'/'head.txt').write_text(head+'\n');(out/'changed_files.json').write_text('[]\n');(out/'diff.patch').write_text('')
    files=sorted(x for x in out.rglob('*') if x.is_file() and x.name!='checksums.sha256');(out/'checksums.sha256').write_text(''.join(f'{_sha(x)}  {x.relative_to(out)}\n' for x in files));return final
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--initial-head',required=True);a=p.parse_args(argv);print(canonical(materialize(a.output,initial_head=a.initial_head)));return 0
