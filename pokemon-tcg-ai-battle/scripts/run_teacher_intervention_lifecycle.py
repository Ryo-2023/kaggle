#!/usr/bin/env python3
"""Public-only proposal collection and randomized one-shot intervention.

The controller imports neither CABT nor submitted proxy code.  Each game is a
new isolated child and writes a shard.  Submitted proxies remain comparative
evidence only: no raw-observation adapter is asserted for a new policy.
"""
from __future__ import annotations

import argparse, csv, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'src'))
from mage_ptcg.evaluation.isolated_runtime import run_isolated

FAMILIES={
 'ALAKAZAM': {'deck':'/tmp/ptcg-submitted-assets-v1/agents__nihei-alakazam/26372f0fa4cf6c98ea42cd2d78f838064a978ca0/deck.csv','config':{'family_id':'ALAKAZAM','anchor_ids':[741,742,743],'basic_ids':[741],'energy_ids':[5,19]}},
 'ARCHALUDON_EX': {'deck':'/tmp/ptcg-submitted-assets-v1/dev__tomatomato_archaludon/a4b1f2407bb85ce79c76072f6df6e4f55ac463c5/deck.csv','config':{'family_id':'ARCHALUDON_EX','anchor_ids':[169,190],'basic_ids':[169],'energy_ids':[8]}},
}
OPPONENTS=('random','deterministic','rule_v1','setup-heavy')

def dump(path:Path,value:object)->None:
 path.parent.mkdir(parents=True,exist_ok=True); t=path.with_suffix(path.suffix+'.tmp');t.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8');t.replace(path)
def csvout(path:Path,rows:list[dict[str,object]])->None:
 fields=sorted({k for r in rows for k in r}) or ['status'];path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',encoding='utf-8',newline='') as h: w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
def action_keys(state,selected):
 by={a.option_index:a for a in state.legal_actions};return [by[i].action_key.digest for i in selected if i in by]
def assignment_for_index(index:int,mode:str)->str:
 """Balance control/treatment within both sides of each four-game block."""
 return 'treatment' if mode=='intervene' and index%4 in (1,2) else 'control'

def worker(args:argparse.Namespace)->int:
 """One game; only safe projection leaves this child in stdout."""
 from main import make_deterministic_agent,make_random_agent,make_rule_agent,make_rule_agent_v1,read_deck_csv
 from mage_ptcg.decision_state import build_decision_state
 from mage_ptcg.family_agents.runtime import ConfigDrivenFamilyAgent
 from mage_ptcg.opponents.synthetic_stress_v1 import make_synthetic_stress_agent
 from scripts.test_sim import run_match
 deck=read_deck_csv(args.deck); family=ConfigDrivenFamilyAgent(deck=deck,config=FAMILIES[args.family]['config']).as_agent(); rule=make_rule_agent(deck=deck,seed=args.seed); rule1=make_rule_agent_v1(deck=deck,seed=args.seed+1); decisions=[]; used=False; reached=False
 def candidate(obs):
  nonlocal used,reached
  base=list(rule(obs))
  if not isinstance(obs,dict) or obs.get('select') is None:return base
  state=build_decision_state(obs); legal={a.option_index for a in state.legal_actions}; p1=list(rule1(obs)); pf=list(family(obs));
  valid=lambda x:len(x)==len(set(x)) and set(x)<=legal
  if not valid(base) or not valid(p1) or not valid(pf): raise ValueError('proposal not legal')
  select=obs['select']; single=select.get('minCount')==1 and select.get('maxCount')==1; forced=len(legal)==1
  divergence=single and not forced and base!=pf
  if divergence: reached=True
  chosen=pf if args.arm=='treatment' and divergence and not used else base
  if args.arm=='treatment' and divergence and not used: used=True
  public=dict(state.actor_view.public_state); public.pop('observed_result',None)
  decisions.append({'decision_id':len(decisions),'state_identity':state.digest,'public_state':public,'own_hand':state.actor_view.own_private_state,'visible_history':state.actor_view.visible_history,'legal_action_keys':sorted(a.action_key.digest for a in state.legal_actions),'baseline_action':action_keys(state,base),'rule_v1_action':action_keys(state,p1),'family_action':action_keys(state,pf),'forced_action':forced,'family_disagreement':divergence,'phase':str(public.get('turn')),'action_type':str(state.legal_actions[base[0]].action_key.semantic_operation) if base and base[0] in legal else 'UNKNOWN'})
  return chosen
 def opponent(runtime_deck,seed):
  if args.opponent=='random':return make_random_agent(deck=runtime_deck,seed=seed)
  if args.opponent=='deterministic':return make_deterministic_agent(deck=runtime_deck)
  if args.opponent=='rule_v1':return make_rule_agent_v1(deck=runtime_deck,seed=seed)
  return make_synthetic_stress_agent(kind='setup-heavy',deck=runtime_deck,seed=seed).as_agent()
 result=run_match(deck_a_path=args.deck,deck_b_path=args.deck,agent_a_name='baseline',agent_b_name=args.opponent,agent_a_factory=lambda _d,_s:candidate,agent_b_factory=opponent,seed=args.seed,output_dir=Path('/tmp')/f'teacher-intervention-{args.game_id}',save_html=False,save_result=False) if args.side==0 else run_match(deck_a_path=args.deck,deck_b_path=args.deck,agent_a_name=args.opponent,agent_b_name='baseline',agent_a_factory=opponent,agent_b_factory=lambda _d,_s:candidate,seed=args.seed,output_dir=Path('/tmp')/f'teacher-intervention-{args.game_id}',save_html=False,save_result=False)
 print(json.dumps({'game_id':args.game_id,'family':args.family,'arm':args.arm,'assignment_propensity':0.5,'side':args.side,'opponent':args.opponent,'won':result.get('winner')==args.side,'status':result.get('status'),'steps':result.get('steps'),'runtime_seconds':result.get('elapsed_seconds'),'context_reached':reached,'intervention_executed':used,'decisions':decisions},ensure_ascii=False));return 0

def run(args:argparse.Namespace)->int:
 phase=args.artifact_root/args.output_dir; shards=phase/'shards'; records=[]
 for family in args.family:
  for index in range(args.games_per_family):
   gid=f'{family.lower()}-{index:04d}'; shard=shards/family/f'{gid}.json'
   if args.resume and shard.exists():records.append(json.loads(shard.read_text(encoding='utf-8')));continue
   # A 4-game block contains each arm once on each side.  Never derive arm
   # from side: that would make intention-to-treat effects uninterpretable.
   arm=assignment_for_index(index,args.mode);cmd=(sys.executable,str(Path(__file__).resolve()),'worker','--family',family,'--deck',FAMILIES[family]['deck'],'--game-id',gid,'--seed',str(args.seed+index),'--side',str(index%2),'--opponent',OPPONENTS[(index//2)%len(OPPONENTS)],'--arm',arm)
   outcome=run_isolated(cmd,cwd=Path('/tmp'),shard_path=shard,timeout_seconds=120)
   try: record=json.loads(outcome.stdout.strip().splitlines()[-1]) if outcome.status=='NORMAL_EXIT' else {}
   except (IndexError,json.JSONDecodeError):record={}
   record.update({'isolation_status':outcome.status,'pid':outcome.pid,'pgid':outcome.process_group_id,'signal':outcome.signal_number,'worker_stdout':outcome.stdout[-1000:],'worker_stderr':outcome.stderr[-1000:]});dump(shard,record);records.append(record)
 csvout(phase/'game_results.csv',[{k:v for k,v in r.items() if k!='decisions'} for r in records])
 decisions=[{**d,'game_id':r['game_id'],'family':r['family'],'side':r['side'],'opponent':r['opponent'],'outcome':r['won']} for r in records for d in r.get('decisions',[])]
 csvout(phase/'proposal_decisions.csv',decisions)
 summary=[]
 for family in args.family:
  own=[r for r in records if r.get('family')==family]; arms=defaultdict(list)
  for r in own:arms[r.get('arm')].append(r)
  delta=(sum(bool(r.get('won')) for r in arms['treatment'])/max(1,len(arms['treatment']))-sum(bool(r.get('won')) for r in arms['control'])/max(1,len(arms['control']))) if args.mode=='intervene' else None
  summary.append({'family':family,'games':len(own),'treatment_games':len(arms['treatment']),'control_games':len(arms['control']),'itt_delta':delta,'reached_games':sum(bool(r.get('context_reached')) for r in own),'executed_games':sum(bool(r.get('intervention_executed')) for r in own),'faults':sum(r.get('status')!='DONE' for r in own)})
 csvout(phase/'summary.csv',summary);dump(phase/'checkpoint.json',{'status':'COMPLETE' if len(records)==len(args.family)*args.games_per_family else 'PARTIAL','games':len(records),'mode':args.mode});return 0

def main()->int:
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True);w=sub.add_parser('worker');w.add_argument('--family',choices=FAMILIES,required=True);w.add_argument('--deck',required=True);w.add_argument('--game-id',required=True);w.add_argument('--seed',type=int,required=True);w.add_argument('--side',type=int,choices=(0,1),required=True);w.add_argument('--opponent',choices=OPPONENTS,required=True);w.add_argument('--arm',choices=('control','treatment'),required=True)
 for name,mode in (('proposal-collection','collect'),('intervention-screen','intervene'),('intervention-validation','intervene')):
  q=sub.add_parser(name);q.set_defaults(mode=mode);q.add_argument('--artifact-root',type=Path,required=True);q.add_argument('--output-dir',required=True);q.add_argument('--family',action='append',choices=FAMILIES,required=True);q.add_argument('--games-per-family',type=int,required=True);q.add_argument('--seed',type=int,default=20260726);q.add_argument('--resume',action='store_true')
 a=p.parse_args();return worker(a) if a.command=='worker' else run(a)
if __name__=='__main__':raise SystemExit(main())
