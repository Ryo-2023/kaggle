#!/usr/bin/env python3
"""Fail-closed approval and isolated-runtime gate for recovered Deck-Agent pairs.

The tool deliberately imports recovered code only in a ``bwrap --unshare-net``
child.  It writes an evidence-only candidate population; it never changes the
Champion, submission entry point, or a training population.
"""
from __future__ import annotations

import argparse, ast, hashlib, json, os, selectors, shutil, subprocess, sys, tarfile, tempfile, textwrap, time
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
TEAM_PREFIX = "refs/remotes/origin/agents/"
FAULTS = ("IMPORT_FAILURE", "DEPENDENCY_MISSING", "ENTRYPOINT_FAILURE", "DECK_REGISTRATION_FAILURE", "OBSERVATION_CONTRACT_FAILURE", "ILLEGAL_ACTION", "ACTION_MAPPING_FAILURE", "TIMEOUT", "STEP_LIMIT", "PROCESS_CRASH", "NETWORK_ATTEMPT", "FILESYSTEM_VIOLATION", "PRIVATE_INFORMATION_ACCESS", "STATE_LEAKAGE", "IDENTITY_MISMATCH")
MAX_STDIO_BYTES = 64 * 1024

HARNESS = r'''
import importlib.util, json, os, pathlib, resource, sys
# BLAS implementations may create their one configured worker thread while
# importing the trusted CABT runtime.  The audit hook below still rejects all
# process-creation APIs from the candidate; RLIMIT_NPROC is therefore a
# bounded defense-in-depth limit rather than an accidental import failure.
resource.setrlimit(resource.RLIMIT_NPROC, (8, 8)); resource.setrlimit(resource.RLIMIT_AS, (768*1024*1024,)*2)
source, module_rel, symbol, repo, candidate_side = sys.argv[1:]
candidate_side = int(candidate_side)
os.environ.clear(); os.environ.update({'HOME':'/work','TMPDIR':'/work','PYTHONDONTWRITEBYTECODE':'1','PATH':'/usr/bin:/bin','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'})
def blocked(event, args):
    if event.startswith(('socket.','subprocess.','os.system','os.posix_spawn','os.fork','os.exec','os.spawn')): raise RuntimeError('SANDBOX_'+event)
sys.addaudithook(blocked)
os.chdir(source); path=pathlib.Path(source, module_rel)
spec=importlib.util.spec_from_file_location('candidate', path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
agent=getattr(mod, symbol)
deck=agent({'logs':[],'current':None,'select':None})
if not isinstance(deck,list) or len(deck)!=60 or any(type(x) is not int for x in deck): raise RuntimeError('DECK_CONTRACT')
from kaggle_environments import make
sys.path.insert(0, repo); from main import make_rule_agent
rule=make_rule_agent(deck=deck)
def wrapped(obs, configuration=None):
    return agent(obs)
env=make('cabt', configuration={'decks':[deck, deck]})
env.run([wrapped, rule] if candidate_side == 0 else [rule, wrapped])
states=[str(x.status) for x in env.state]
print(json.dumps({'candidate_side':candidate_side,'deck':deck,'states':states,'steps':len(env.steps),'selection_type':'list'}))
'''

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
def tree_sha(path: Path) -> str:
    """Content-address a materialized bundle, including stable relative paths."""
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob('*') if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(child.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()
def load_jsonl(path: Path) -> list[dict[str,Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]
def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
def write_jsonl(path: Path, rows: list[dict[str,Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in rows))

def materialize(commit: str, prefix: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True); raw=subprocess.check_output(['git','archive','--format=tar',commit,prefix],cwd=REPO)
    with tarfile.open(fileobj=__import__('io').BytesIO(raw)) as archive:
        for item in archive.getmembers():
            if item.issym() or item.islnk() or Path(item.name).is_absolute() or '..' in Path(item.name).parts: raise RuntimeError('unsafe archive member')
        archive.extractall(target, filter='data')
    source=target/prefix
    if not source.is_dir(): raise RuntimeError('source missing after archive')

def static_findings(source: Path) -> dict[str,bool]:
    text='\n'.join(p.read_text(errors='ignore') for p in source.rglob('*.py'))
    write_open = "open(" in text and any(marker in text for marker in ("'w'", '\"w\"', "'a'", '\"a\"', "'x'", '\"x\"'))
    return {'network': any(x in text for x in ('socket','requests','urllib','httpx')), 'filesystem_write': write_open or any(x in text for x in ('write_text','write_bytes','unlink(','rmtree(')), 'subprocess': any(x in text for x in ('subprocess','os.system','Popen')), 'cg_dependency': 'cg.' in text or 'import cg' in text, 'global_state': 'global ' in text, 'randomness': 'random.' in text}

def invoke(source: Path, entry: dict[str,Any], timeout: int, *, candidate_side: int) -> dict[str,Any]:
    work=Path(tempfile.mkdtemp(prefix='family-gate-'))
    try:
        command=['bwrap','--die-with-parent','--new-session','--unshare-net','--unshare-pid','--proc','/proc','--dev','/dev','--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--ro-bind',str(REPO),'/repo','--ro-bind',str(source),'/bundle','--bind',str(work),'/work','--chdir','/bundle','/repo/.venv/bin/python','-I','-c',HARNESS,'/bundle',Path(entry['module']).name,entry['symbol'],'/repo',str(candidate_side)]
        process=subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={'PATH':os.environ.get('PATH','')})
        selector=selectors.DefaultSelector(); selector.register(process.stdout,selectors.EVENT_READ,'stdout'); selector.register(process.stderr,selectors.EVENT_READ,'stderr')
        captured={'stdout':bytearray(),'stderr':bytearray()}; deadline=time.monotonic()+timeout
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining <= 0:
                process.kill(); process.wait(); return {'stage':'F' if candidate_side == 0 else 'G','status':'FAIL','fault':'TIMEOUT'}
            for key,_ in selector.select(timeout=remaining):
                chunk=os.read(key.fileobj.fileno(),4096)
                if not chunk:
                    selector.unregister(key.fileobj); continue
                target=captured[key.data]
                if len(target)+len(chunk)>MAX_STDIO_BYTES:
                    process.kill(); process.wait(); return {'stage':'B','status':'FAIL','fault':'PROCESS_CRASH','detail':'STDIO_LIMIT'}
                target.extend(chunk)
        result_code=process.wait(timeout=1)
        stdout=captured['stdout'].decode('utf-8',errors='replace'); stderr=captured['stderr'].decode('utf-8',errors='replace')
    except subprocess.TimeoutExpired: return {'stage':'F' if candidate_side == 0 else 'G','status':'FAIL','fault':'TIMEOUT'}
    finally: shutil.rmtree(work, ignore_errors=True)
    if result_code:
        err=(stderr+stdout)[-800:]
        fault='NETWORK_ATTEMPT' if 'SANDBOX_socket' in err else ('DEPENDENCY_MISSING' if 'ModuleNotFoundError' in err else 'IMPORT_FAILURE')
        return {'stage':'B','status':'FAIL','fault':fault,'detail':err}
    try: data=json.loads(stdout.strip().splitlines()[-1])
    except Exception: return {'stage':'B','status':'FAIL','fault':'PROCESS_CRASH','detail':stdout[-400:]}
    stage = 'F' if candidate_side == 0 else 'G'
    if data['states'] != ['DONE','DONE']: return {'stage':stage,'status':'FAIL','fault':'ILLEGAL_ACTION','runtime':data}
    return {'stage':stage,'status':'PASS','fault':None,'runtime':data}

def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(); p.add_argument('--recovery-root',type=Path,required=True); p.add_argument('--output-root',type=Path,required=True); p.add_argument('--limit',type=int,default=20); p.add_argument('--timeout',type=int,default=45); a=p.parse_args(argv)
    registry=load_jsonl(a.recovery_root/'artifacts/reconstructed_bundle_registry.jsonl')
    all_rows=[]; runnable=[]; manual=[]
    for b in registry:
        team=b['source_ref'].startswith(TEAM_PREFIX); valid=b['link_status']=='VERIFIED_SOURCE_BUNDLE' and b['entrypoint']['entrypoint_status']=='VERIFIED_DIRECT_CALLABLE'
        usage='AUTO_ELIGIBLE_TEAM' if team else 'MANUAL_APPROVAL_REQUIRED'
        row={'bundle_id':b['bundle_id'],'source':b['source_ref'],'author':None,'commit':b['commit'],'deck':b['deck_digest'],'entrypoint':b['entrypoint'],'usage_class':usage,'usage_evidence':'approved namespace policy pokemon-team-agents-internal-v1' if team else 'no matching approved namespace policy','license':'team-internal policy' if team else 'unknown','executable_eligibility':'GATE_ELIGIBLE' if team and valid else 'NOT_EXECUTABLE'}
        all_rows.append(row)
        if row['executable_eligibility']=='GATE_ELIGIBLE': runnable.append((b,row))
        else: manual.append(row)
    def score(pair):
        b,_=pair; s=b['static']; return (100 - 40*int(s.get('cg_dependency',False)) - 25*int(s.get('network',False)) - 15*int(s.get('filesystem_write',False)) - 8*int(s.get('subprocess',False)) - 3*int(s.get('global_state',False)) - 2*int(s.get('randomness',False)), b['bundle_id'])
    runnable=sorted(runnable,key=lambda x:(-score(x)[0],score(x)[1]))[:a.limit]
    top=[]; results=[]; verified=[]; quarantine=[]
    for rank,(b,row) in enumerate(runnable,1):
        top.append({'rank':rank,'bundle_id':b['bundle_id'],'score':score((b,row))[0],'source':b['source_ref'],'deck_digest':b['deck_digest'],'entrypoint':b['entrypoint'],'risk':b['static'],'decision':'RUNTIME_GATE'})
        q=a.output_root/'quarantine'/b['bundle_id']; prefix=str(Path(b['agent_path']).parent); materialize(b['commit'],prefix,q); source=q/prefix
        found=static_findings(source); manifest={'bundle_id':b['bundle_id'],'source_commit':b['commit'],'agent_path':b['agent_path'],'bundle_digest':tree_sha(source),'source_read_only':True,'network_namespace':'bwrap --unshare-net','temporary_write_root':'/work','static_findings':found}
        write_json(q/'manifest.json',manifest)
        # Code that declares a network/subprocess/write dependency is not run.
        if found['network'] or found['subprocess'] or found['filesystem_write']:
            gate={'stage':'A','status':'FAIL','fault':'STATIC_SAFETY_BLOCKED'}
        elif found['cg_dependency']:
            # The recovered subtree contains no pinned cg runtime closure.
            # Installing or borrowing one would violate the no-auto-dependency
            # rule, so this fails before importing candidate code.
            gate={'stage':'A','status':'FAIL','fault':'DEPENDENCY_MISSING'}
        else:
            gate=invoke(source,b['entrypoint'],a.timeout,candidate_side=0)
            if gate['status']=='PASS':
                side_swap=invoke(source,b['entrypoint'],a.timeout,candidate_side=1)
                if side_swap['status']=='PASS':
                    gate={'stage':'H','status':'PASS','fault':None,'runtime':{'side_0':gate['runtime'],'side_1':side_swap['runtime']},'opponent_types':['RULE_V0_DECK']}
                else:
                    gate=side_swap
        result={'bundle_id':b['bundle_id'],'usage_class':row['usage_class'],'entrypoint':b['entrypoint'],'bundle_digest':manifest['bundle_digest'],'runtime_fingerprint':hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),'gate':gate,'status':'VERIFIED_NATIVE_OPPONENT' if gate['status']=='PASS' else 'RUNTIME_BLOCKED','fault':gate['fault'],'global_state':found['global_state'],'randomness':found['randomness']}
        results.append(result)
        if gate['status']=='PASS': verified.append({'opponent_id':'recovered-'+b['bundle_id'],'opponent_type':'RECOVERED_NATIVE_VARIANT','status':'VERIFIED_NATIVE_OPPONENT','bundle_id':b['bundle_id'],'deck_digest':b['deck_digest'],'entrypoint':b['entrypoint'],'runtime_fingerprint':result['runtime_fingerprint'],'family':'MEGA_LUCARIO_EX' if 677 in gate['runtime']['side_0']['deck'] else 'UNKNOWN','strategy_mixture':'native recovered policy','observed_behavior':'CABT legal; descriptive only'})
        else: quarantine.append({'bundle_id':b['bundle_id'],'status':'RUNTIME_BLOCKED','fault':gate['fault']})
    artifacts=a.output_root/'artifacts'; docs=a.output_root/'docs'
    write_jsonl(artifacts/'usage_eligibility_registry.jsonl',all_rows); write_json(artifacts/'manual_approval_queue.json',{'candidates':manual}); write_json(artifacts/'top20_runtime_candidates.json',{'candidates':top}); write_jsonl(artifacts/'quarantine_bundle_registry.jsonl',[{'bundle_id':r['bundle_id'],'path':str(a.output_root/'quarantine'/r['bundle_id'])} for r in results]); write_json(artifacts/'isolation_audit.json',{'network':'bwrap --unshare-net','read_only_source':True,'temporary_write_only':True,'limits':{'timeout_seconds':a.timeout,'memory_bytes':805306368,'processes':8,'blas_threads':1,'stdout_stderr_bytes':MAX_STDIO_BYTES},'candidate_process_creation':'audit-hook blocked','parent_imported_candidate_code':False}); write_jsonl(artifacts/'runtime_gate_results.jsonl',results)
    passed=[x for x in results if x['gate']['status']=='PASS']
    summary={'evaluated_candidates':len(results),'candidate_gate_passed_count':len(passed),'games':sum(2 for x in passed),'maximum_games':96,'legal_games':sum(2 for x in passed),'candidate_faults':sum(x['gate']['status']!='PASS' for x in results),'fault_counts':dict(Counter(x['fault'] for x in results if x['fault'])),'verified_candidate_gate':'PASS' if passed else 'FAIL','portfolio_gate':'PASS' if len(verified)>=3 else 'FAIL'}
    write_json(artifacts/'cabt_smoke_summary.json',summary); write_json(artifacts/'verified_new_opponents.json',{'opponents':verified}); before={'families':3,'non_rule_v0_opponents':6,'verified_bindings':6}; after={'families':3,'non_rule_v0_opponents':6+len(verified),'verified_bindings':6+len(verified)}; write_json(artifacts/'project_population_delta.json',{'family_before':before['families'],'family_after':after['families'],'non_rule_v0_opponent_before':before['non_rule_v0_opponents'],'non_rule_v0_opponent_after':after['non_rule_v0_opponents'],'verified_binding_before':before['verified_bindings'],'verified_binding_after':after['verified_bindings'],'before':before,'after':after,'new_ids':[x['opponent_id'] for x in verified],'existing_re_registrations':[]}); verdict='READY_FOR_EXPANDED_NATIVE_VARIANT_PILOT' if len(verified)>=3 and summary['portfolio_gate']=='PASS' else ('ACTIVATION_BLOCKED' if results else 'NO_SAFE_EXECUTABLE_ASSETS'); write_json(artifacts/'final_readiness.json',{'verdict':verdict,'verified_count':len(verified),'cabt':summary})
    docs.mkdir(parents=True,exist_ok=True); (docs/'executive_report.md').write_text(f'# Isolated runtime gate\n\nVerdict: `{verdict}`. Verified new native variants: {len(verified)}. No Champion or training-population promotion occurred.\n'); (docs/'usage_and_approval_report.md').write_text('# Usage and approval\n\n`origin/agents/*` candidates are covered by the approved team namespace policy; all others remain manual-review only.\n'); (docs/'isolation_runtime_report.md').write_text('# Isolation runtime\n\nCandidates run only under `bwrap --unshare-net`, a read-only source mount, a dedicated temporary write mount, CPU/process/memory limits, and bounded stdio/timeout.\n'); (docs/'new_opponent_report.md').write_text(f'# New opponents\n\nVerified: {len(verified)}. Existing Family re-registrations are not counted.\n'); (docs/'next_stage.md').write_text('# Next stage\n\nDo not promote these candidate-only opponents to Champion or training population automatically.\n'); (artifacts/'manual_approval_queue.md').write_text('# Manual approval queue\n\n'+'\n'.join(f'- {x["bundle_id"]}: {x["usage_evidence"]}' for x in manual)+'\n')
    return 0
if __name__=='__main__': raise SystemExit(main())
