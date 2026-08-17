#!/usr/bin/env python3
"""Run a side-balanced, resumable direct round robin of frozen proxy teachers."""
from __future__ import annotations

import argparse, csv, itertools, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from mage_ptcg.evaluation.isolated_runtime import run_isolated

SOURCE = Path('/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-assets-calibration-teacher-v1-20260726_181000/runtime_qualification.csv')

def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary=path.with_suffix(path.suffix+'.tmp'); temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); temporary.replace(path)

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--artifact-root',type=Path,required=True); parser.add_argument('--candidate-id',action='append',required=True); parser.add_argument('--games',type=int,default=64); parser.add_argument('--resume',action='store_true'); args=parser.parse_args()
    if len(args.candidate_id)!=4 or args.games%2: raise ValueError('exactly four candidates and an even game count are required')
    lookup={r['asset_id']:r for r in csv.DictReader(SOURCE.open(encoding='utf-8',newline=''))}; phase=args.artifact_root/'06_teacher_round_robin'; output=[]
    for left,right in itertools.combinations(args.candidate_id,2):
        shard=phase/'shards'/f'{left.replace("/", "__")}__vs__{right.replace("/", "__")}.json'
        if args.resume and shard.exists(): output.append(json.loads(shard.read_text(encoding='utf-8'))); continue
        command=(sys.executable,str(ROOT/'scripts/run_submitted_asset_lifecycle.py'),'--smoke-child','--asset',lookup[left]['extraction_path'],'--opponent',lookup[right]['extraction_path'],'--games',str(args.games))
        isolated=run_isolated(command,cwd=Path('/tmp'),shard_path=shard,timeout_seconds=120)
        try: child=json.loads(isolated.stdout.strip().splitlines()[-1]) if isolated.status=='NORMAL_EXIT' else {}
        except (IndexError,json.JSONDecodeError): child={}
        result={'candidate_a':left,'candidate_b':right,'games':int(child.get('smoke_games',0)),'a_wins':int(child.get('wins',0)),'illegal':int(child.get('illegal',0)),'crash':int(child.get('crash',0)),'timeout':int(child.get('timeout',0)),'side_balanced':True,'status':isolated.status,'pid':isolated.pid,'pgid':isolated.process_group_id,'signal':isolated.signal_number}
        write(shard,result); output.append(result)
    fields=sorted({key for row in output for key in row});
    with (phase/'teacher_round_robin.csv').open('w',encoding='utf-8',newline='') as handle: writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(output)
    write(phase/'checkpoint.json',{'status':'COMPLETE' if len(output)==6 and all(r['games']==args.games and r['status']=='NORMAL_EXIT' for r in output) else 'PARTIAL','pairs':len(output),'games':sum(r['games'] for r in output),'candidates':args.candidate_id})
    return 0
if __name__=='__main__': raise SystemExit(main())
