"""Read-only Git bundle recovery for deck/agent candidate review."""
from __future__ import annotations
import ast, json, subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from mage_ptcg.competition_intelligence.atomic_io import atomic_write_bytes, atomic_write_json
from mage_ptcg.competition_intelligence.o5_registry import canonical_deck_hash, parse_exact_deck_text

def _json(p: Path): return json.loads(p.read_text())
def _jsonl(p: Path): return [json.loads(x) for x in p.read_text().splitlines() if x]
def _git(repo: Path, args: list[str]) -> str:
 c=subprocess.run(['git',*args],cwd=repo,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
 return c.stdout if c.returncode==0 else ''
def _write_jsonl(p:Path,rows:list[Mapping[str,Any]]): atomic_write_bytes(p,(''.join(json.dumps(dict(x),sort_keys=True,separators=(',',':'))+'\n' for x in rows)).encode())

def recover_entrypoints(text: str, module: str) -> list[dict[str,Any]]:
 """AST-only callable recovery; no candidate module is imported."""
 try: tree=ast.parse(text)
 except SyntaxError: return []
 out=[]; names={'agent','main','act','choose_action','policy','get_action','make_agent','create_agent','build_agent','get_agent'}
 for node in tree.body:
  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and (node.name in names or node.name.startswith('make_') and node.name.endswith('_agent')):
   args=[a.arg for a in node.args.args]; kind='VERIFIED_DIRECT_CALLABLE' if node.name in {'agent','main','act','choose_action','policy','get_action'} else 'VERIFIED_FACTORY'
   out.append({'module':module,'symbol':node.name,'callable_type':'function','signature':args,'factory_arguments':args if kind=='VERIFIED_FACTORY' else [],'entrypoint_status':kind})
  if isinstance(node,ast.ClassDef):
   methods={x.name for x in node.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}
   if methods & {'__call__','act','step','choose','policy'}: out.append({'module':module,'symbol':node.name,'callable_type':'class','signature':sorted(methods & {'__call__','act','step','choose','policy'}),'factory_arguments':[],'entrypoint_status':'ADAPTER_REQUIRED'})
 return out

def _link(repo:Path, source:Mapping[str,Any]) -> dict[str,Any]:
 path,commit=str(source['path']),str(source['commit']); parent=path.rsplit('/',1)[0] if '/' in path else ''
 candidates=[p for p in _git(repo,['ls-tree','-r','--name-only',commit,parent or '.']).splitlines() if p.endswith(('deck.csv','deck.txt')) or '/decks/' in p]
 exact=[]
 for deck_path in candidates:
  cards=parse_exact_deck_text(_git(repo,['show',f'{commit}:{deck_path}']))
  if cards: exact.append((deck_path,cards))
 if len(exact)==1:
  p,cards=exact[0]; return {'link_status':'VERIFIED_SOURCE_BUNDLE','link_method':'SAME_DIRECTORY_DECK','deck_path':p,'deck_digest':canonical_deck_hash(cards,card_pool_version='official-local'),'required_cards':[],'missing_cards':[],'ambiguity_count':0}
 if len(exact)>1:return {'link_status':'AMBIGUOUS_MULTIPLE_DECKS','link_method':'SIBLING_DECK_SCAN','deck_path':None,'deck_digest':None,'required_cards':[],'missing_cards':[],'ambiguity_count':len(exact)}
 return {'link_status':'NO_DECK_FOUND','link_method':'SIBLING_DECK_SCAN','deck_path':None,'deck_digest':None,'required_cards':[],'missing_cards':[],'ambiguity_count':0}

def recover(*, repo:Path, ingest_root:Path, output_root:Path)->dict[str,Any]:
 a=ingest_root/'artifacts'; out=output_root/'artifacts'; out.mkdir(parents=True,exist_ok=True)
 sources={x['source_id']:x for x in _json(a/'source_registry.json')['sources']}; agents=[x for x in _jsonl(a/'agent_asset_registry.jsonl') if x.get('activation_eligibility')!='QUARANTINED']
 links=[]; entries=[]; graph={}; unresolved=[]
 for agent in agents:
  source=sources.get(agent['source_id']);
  if not source or source.get('source_type')!='git_ref': continue
  link=_link(repo,source); text=_git(repo,['show',f"{source['commit']}:{source['path']}"])
  eps=recover_entrypoints(text,str(source['path'])) if source['path'].endswith('.py') else []
  ep=eps[0] if len(eps)==1 else {'entrypoint_status':'MULTIPLE_AMBIGUOUS' if eps else 'NO_ENTRYPOINT_FOUND','module':source['path'],'symbol':None,'callable_type':None,'signature':[],'factory_arguments':[]}
  static={'network':bool(agent.get('network_dependency')),'filesystem_write':bool(agent.get('filesystem_writes')),'global_state': 'global ' in text,'randomness':'random.' in text,'cg_dependency':'from cg.' in text or 'import cg' in text}
  bundle={'bundle_id':'bundle-'+agent['agent_id'][6:],'agent_id':agent['agent_id'],'source_ref':source['source_url'],'commit':source['commit'],'agent_path':source['path'],'trust_class':source['trust_class'],**link,'entrypoint':ep,'static':static,'usage_eligibility':'LOCAL_REPRODUCIBLE_REVIEW_ONLY'}
  entries.append(bundle); graph[bundle['bundle_id']]={'agent':source['path'],'deck':link['deck_path'],'entrypoint':ep['symbol'],'imports':agent.get('imports',[])}
  if link['link_status']=='NO_DECK_FOUND' or ep['entrypoint_status'] in {'NO_ENTRYPOINT_FOUND','MULTIPLE_AMBIGUOUS'}: unresolved.append({'bundle_id':bundle['bundle_id'],'link_status':link['link_status'],'entrypoint_status':ep['entrypoint_status']})
  links.append({'agent_id':agent['agent_id'],**link,'source_evidence':{'commit':source['commit'],'agent_path':source['path']},'confidence':'HIGH' if link['link_status']=='VERIFIED_SOURCE_BUNDLE' else 'NONE'})
 links=sorted(links,key=lambda x:x['agent_id']); entries=sorted(entries,key=lambda x:x['bundle_id'])
 recovered=[x for x in entries if x['link_status']=='VERIFIED_SOURCE_BUNDLE' and x['entrypoint']['entrypoint_status'] in {'VERIFIED_DIRECT_CALLABLE','VERIFIED_FACTORY'}]
 family=[]
 for x in recovered:
  name=x['agent_path'].lower(); hint=next((n.upper() for n in ('archaludon','dragapult','iono','crustle','starmie','lucario','abomasnow','alakazam') if n in name), 'UNKNOWN')
  family.append({'bundle_id':x['bundle_id'],'primary_family':hint,'family_confidence':'PATH_AND_BUNDLE_HINT' if hint!='UNKNOWN' else 'UNKNOWN','deck_digest':x['deck_digest'],'entrypoint':x['entrypoint'],'activation':'MANUAL_SMOKE_REQUIRED' if not x['static']['cg_dependency'] and not x['static']['randomness'] else 'DEPENDENCY_OR_DETERMINISM_REVIEW_REQUIRED'})
 top=sorted(recovered,key=lambda x:(x['static']['cg_dependency'],x['static']['randomness'],x['agent_path']))[:30]
 atomic_write_json(out/'deck_linkage_report.json',{'total_agents':len(agents),'counts':dict(Counter(x['link_status'] for x in links)),'links':links})
 atomic_write_json(out/'entrypoint_recovery_report.json',{'counts':dict(Counter(x['entrypoint']['entrypoint_status'] for x in entries)),'entries':[{'agent_id':x['agent_id'],'entrypoint':x['entrypoint']} for x in entries]})
 _write_jsonl(out/'reconstructed_bundle_registry.jsonl',entries); atomic_write_json(out/'bundle_dependency_graph.json',graph); atomic_write_json(out/'unresolved_bundle_report.json',{'bundles':unresolved})
 _write_jsonl(out/'family_candidate_registry.jsonl',family); atomic_write_json(out/'top30_recovered_candidates.json',{'candidates':top})
 (out/'top30_recovered_candidates.md').write_text('# Top recovered candidates\n\n'+'\n'.join(f"- {x['agent_path']}: {x['link_status']} / {x['entrypoint']['entrypoint_status']}" for x in top)+'\n')
 # No recovered external pair is auto-executed or counted as activated.
 _write_jsonl(out/'verified_new_binding_registry.jsonl',[]); atomic_write_json(out/'isolated_smoke_summary.json',{'cabt_games':0,'reason':'recovered bundles require dependency/determinism review before isolated smoke'})
 # Project baseline includes the three pre-existing Family and three Team
 # Native opponents.  This recovery run activates neither category.
 delta={'global_family_before':3,'global_family_after':3,'global_non_rule_v0_before':6,'global_non_rule_v0_after':6,'new_family_ids':[],'new_opponent_ids':[],'new_binding_ids':[],'existing_re_registrations':[]}
 atomic_write_json(out/'project_level_population_delta.json',delta)
 verdict='READY_FOR_MANUAL_APPROVAL' if recovered else 'NO_RECOVERABLE_DECK_AGENT_PAIRS'
 atomic_write_json(out/'final_readiness.json',{'verdict':verdict,'recovered_bundle_count':len(recovered),'reason':'static recovery only; no external recovered bundle was executed or auto-activated'})
 docs=output_root/'docs';docs.mkdir(exist_ok=True)
 (docs/'executive_report.md').write_text(f'# Deck linkage and entrypoint recovery\n\nRecovered source-bundle pairs: {len(recovered)}. No new pair was activated without isolated safety/CABT gates.\n')
 (docs/'recovery_methods.md').write_text('# Recovery methods\n\nRead-only Git `ls-tree` and `show`, same-directory deck linkage, AST-only callable extraction, and static dependency review were used.\n')
 (docs/'new_family_report.md').write_text('# New Family report\n\nRecovered path hints are candidates only; no existing Family re-registration is counted as new.\n')
 (docs/'next_stage.md').write_text('# Next stage\n\nReview recovered local bundles with cg dependency/statefulness, then run isolated smoke only after the usage and runtime gates pass.\n')
 return {'verdict':verdict,'recovered':len(recovered),'link_counts':dict(Counter(x['link_status'] for x in links))}
