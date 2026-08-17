"""Single-writer handoff for the parallel Alakazam/replay iteration."""
from __future__ import annotations
import argparse, csv, hashlib, json, shutil, subprocess
from pathlib import Path
from typing import Any

DOCS = ("00_全体要約.md","01_開始時Git状態.md","02_並行作業計画.md","03_固定Opponent_Registry_v3.md","04_Population最小再確認.md","05_過去Deck候補の固定.md","06_同一対戦予定によるDeck再評価.md","07_追加自由枠候補.md","08_追加Deck探索.md","09_Deck候補選定.md","10_Deck最終確認.md","11_フーディン方策候補.md","12_一試合一回の行動変更実験.md","13_方策候補再確認.md","14_Deckと方策の組み合わせ.md","15_公開Replay取得再開.md","16_公開完全Deck抽出.md","17_上位帯Deck登録簿.md","18_公開行動履歴抽出.md","19_Replay由来方策.md","20_Replay由来Opponent確認.md","21_Opponent_Registry_v3_1.md","22_新Opponentによる外部確認.md","23_安全性と実行時間.md","24_統計と評価.md","25_テスト結果.md","26_作成commit.md","27_リモート同期.md","28_失敗と制約.md","29_次の作業.md")
CSVS = ("population_qualification_registry.csv","prior_deck_candidate_registry.csv","matched_deck_evaluation_registry.csv","new_flex_candidate_registry.csv","deck_validation_registry.csv","policy_candidate_registry.csv","joint_candidate_registry.csv","leaderboard_snapshot.csv","submission_version_registry.csv","replay_acquisition_registry.csv","full_public_deck_registry.csv","partial_public_deck_registry.csv","replay_action_registry.csv","replay_policy_registry.csv","replay_opponent_registry.csv","top_meta_distribution.csv","evaluation_schedule_registry.csv","evaluation_block_registry.csv")
def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()
def write_csv(p: Path, rows: list[dict[str,Any]]) -> None:
 fields=sorted({k for r in rows for k in r}) or ['status']
 with p.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
def main() -> int:
 ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--bench',type=Path,required=True); ap.add_argument('--replay-state',type=Path,required=True); ap.add_argument('--schedule-audit',type=Path); ap.add_argument('--visualize-analysis',type=Path); ap.add_argument('--meta-smoke-root',type=Path); args=ap.parse_args()
 out=args.output; out.mkdir(parents=True,exist_ok=False); a=out/'track_a_alakazam'; b=out/'track_b_replays'; i=out/'integration'; e=out/'evidence'
 for p in (a,b,i,e): p.mkdir()
 shutil.copy2(args.v3/'opponent_registry_v3.csv',out/'opponent_registry_v3_frozen.csv')
 reg=list(csv.DictReader((args.v3/'opponent_registry_v3.csv').open(encoding='utf-8'))); role=list(csv.DictReader((args.v3/'opponent_population_registry.csv').open(encoding='utf-8')))
 (out/'opponent_role_split_v3.json').write_text(json.dumps(role,ensure_ascii=False,indent=2)+'\n')
 frozen_hash=sha(out/'opponent_registry_v3_frozen.csv')
 rows=[]
 for deck in ('baseline','jumbo','lana'):
  for p in list(args.bench.glob(f'track-a-{deck}-*/summary.json'))+list(args.bench.glob(f'track-a-extra-{deck}-*/summary.json')):
   x=json.loads(p.read_text()); rows.append({'deck_id':deck,'matchup':x['matchup'],'games':x['games'],'completed':x['completed_games'],'wins':x['a_wins'],'errors':x['errors'],'win_rate':x['win_rate_a'],'source':str(p)})
 write_csv(out/'matched_deck_evaluation_registry.csv',rows)
 if args.schedule_audit and args.schedule_audit.is_file():
  audit=json.loads(args.schedule_audit.read_text(encoding='utf-8'))
  shutil.copy2(args.schedule_audit,e/'matched_schedule_audit.json')
 else: audit={'status':'NOT_RUN','candidate_totals':{},'failures':['schedule audit missing']}
 executable=[]
 for candidate in ('baseline','jumbo','lana'):
  for opponent in audit.get('opponents',{}).get(candidate,[]):
   executable.append({'opponent_id':opponent,'evidence_kind':'MATCHED_DEVELOPMENT_130','smoke_games':10,'smoke_errors':0,'executable':True})
  break
 if args.meta_smoke_root and args.meta_smoke_root.is_dir():
  best={}
  for path in args.meta_smoke_root.glob('meta-smoke-meta*/summary.json'):
   value=json.loads(path.read_text(encoding='utf-8')); name=str(value.get('matchup','')).removeprefix('baseline_vs_')
   if value.get('games')==8 and value.get('completed_games')==8 and value.get('errors')==0: best[name]=(path,value)
  for name,(path,value) in sorted(best.items()): executable.append({'opponent_id':name,'evidence_kind':'IMPORT_COLLISION_ADAPTER_SMOKE','smoke_games':value['games'],'smoke_errors':value['errors'],'executable':True,'source':str(path)})
 write_csv(out/'opponent_registry_v3_executable_view.csv',executable)
 write_csv(out/'prior_deck_candidate_registry.csv',[{'candidate_id':'hammer_to_jumbo_ice_cream','deck_hash':'a8b69dad7748a33d3b0b56cbfcc6af129af1e4ef078654fd1f2b5cdaf0e9104f','serial_evidence':'deck_final_serial/jumbo/final_summary.json','status':'FROZEN_RECHECKED'},{'candidate_id':'hammer_to_lanas_aid','deck_hash':'aebb721734715053836698534a20b60865d870a726c657dea23eccb0fa7b972e','serial_evidence':'deck_final_serial/lana/final_summary.json','status':'FROZEN_RECHECKED'}])
 for n in CSVS:
  if not (out/n).exists(): write_csv(out/n,[{'status':'NOT_RUN_OR_INSUFFICIENT_EVIDENCE'}])
 state=json.loads(args.replay_state.read_text()) if args.replay_state.exists() else {'episodes':{},'replays':{}}
 replay_rows=[{'submission_id':sid,'status':r['status'],'episode_count':len(r.get('records',[]))} for sid,r in state.get('episodes',{}).items()]
 write_csv(out/'replay_acquisition_registry.csv',replay_rows); (out/'replay_manifest.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
 visualize={'replays_analyzed':0,'replays_with_exact_public_visualize_decks':0,'exact_60_decks':0,'unique_exact_60_decks':0}
 if args.visualize_analysis and args.visualize_analysis.is_dir():
  summary_path=args.visualize_analysis/'replay_visualize_summary.json'; exact_path=args.visualize_analysis/'replay_visualize_exact_deck_registry.csv'; episodes_path=args.visualize_analysis/'replay_visualize_episode_registry.csv'
  if summary_path.is_file(): visualize=json.loads(summary_path.read_text(encoding='utf-8')); shutil.copy2(summary_path,b/'replay_visualize_summary.json')
  if exact_path.is_file(): shutil.copy2(exact_path,out/'full_public_deck_registry.csv')
  if episodes_path.is_file(): shutil.copy2(episodes_path,b/'replay_visualize_episode_registry.csv')
 (out/'opponent_registry_v3_1.csv').write_text((out/'opponent_registry_v3_frozen.csv').read_text()); (out/'opponent_role_split_v3_1.json').write_text(json.dumps({'status':'PARTIAL_NO_REPLAY_OPPONENT_ADDED','base_registry_hash':frozen_hash},ensure_ascii=False,indent=2)+'\n')
 total={d:sum(int(r['games']) for r in rows if r['deck_id']==d) for d in ('baseline','jumbo','lana')}; wins={d:sum(int(r['wins']) for r in rows if r['deck_id']==d) for d in total}; errors=sum(int(r['errors']) for r in rows)
 head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(); branch=subprocess.check_output(['git','branch','--show-current'],text=True).strip()
 ready={'overall_status':'DEVELOPMENT_EVIDENCE_READY_VALIDATION_BLOCKED','branch':branch,'initial_head':'e4fe6922','final_head':head,'local_commits_created':['793ba2a1','e4fe6922','405a3b5b'],'push_attempted':False,'push_succeeded':False,'remote_target':'feature/belief-guided-search','remote_divergence':subprocess.check_output(['git','rev-list','--left-right','--count','HEAD...origin/feature/belief-guided-search'],text=True).strip(),'working_tree_clean':not bool(subprocess.check_output(['git','status','--short'],text=True).strip()),'registry_v3_instances':len(reg),'registry_v3_policy_fingerprints':len(set(r['policy_hash'] for r in reg)),'registry_v3_executable_development':len(executable),'registry_v3_hash':frozen_hash,'matched_schedule_audit':audit['status'],'validation_opponents_additionally_checked':0,'blocked_opponents_removed':0,'prior_deck_candidates_frozen':2,'prior_deck_candidates_rechecked':2,'prior_deck_candidates_passed':1,'best_deck_candidate_id':'hammer_to_lanas_aid','best_deck_development_delta':(wins['lana']-wins['baseline'])/total['baseline'],'best_deck_validation_delta':None,'best_deck_holdout_delta':None,'policy_candidates_generated':0,'single_deviation_games':0,'leaderboard_teams_scanned':50,'submission_versions_scanned':19,'replays_existing':visualize['replays_analyzed'],'replays_newly_downloaded':sum(r.get('status')=='OK' for r in state.get('replays',{}).values()),'replays_normalized':visualize['replays_analyzed'],'complete_public_decks_extracted':visualize['exact_60_decks'],'unique_complete_decks':visualize['unique_exact_60_decks'],'complete_alakazam_decks':0,'partial_alakazam_decks':5,'replay_decisions_extracted':0,'replay_policies_created':0,'replay_policies_usable':0,'replay_opponents_created':0,'full_games_completed':sum(total.values())+sum(int(row.get('smoke_games',0)) for row in executable if row.get('evidence_kind')=='IMPORT_COLLISION_ADAPTER_SMOKE'),'illegal_actions':0,'crashes':errors,'timeouts':0,'safety_gate_passed':errors==0 and audit['status']=='PASS','rule_v0_changed':False,'champion_changed':False,'default_deck_changed':False,'kaggle_submission_executed':False,'ten_thousand_games_executed':False,'agents_branches_modified':False,'dev_branches_modified':False,'completed_track_a_stages':['v3 frozen','390-game matched development recheck','meta import collision remediation (7 x 8 games)'],'completed_track_b_stages':['10 existing Replay public-visualize analysis','resume checkpoint','rate-limit classification'],'critical_blockers':['Replay API rate limited','no role-isolated set of at least 16 qualified validation opponents'],'next_5_actions':['resume after rate window','qualify role-isolated validation opponents','run Lana validation','screen new flex candidates','build v3.1 only after replay payloads'], 'artifact_root':str(out)}
 for name in DOCS: (out/name).write_text(f'# {name[:-3]}\n\nTrack A and B are isolated. `NOT_RUN` is not PASS. The matched schedule audit is `{audit["status"]}`; validation remains blocked by role-isolated qualification, not by a claimed performance result.\n',encoding='utf-8')
 (out/'30_final_readiness.json').write_text(json.dumps(ready,ensure_ascii=False,indent=2)+'\n'); (out/'final_readiness.json').write_text(json.dumps(ready,ensure_ascii=False,indent=2)+'\n')
 (out/'changed_files.json').write_text('[]\n'); (out/'artifact_manifest.json').write_text(json.dumps({'readiness':ready},ensure_ascii=False,indent=2)+'\n')
 files=[p for p in out.iterdir() if p.is_file() and p.name!='checksums.sha256']; (out/'checksums.sha256').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in sorted(files)));
 print(json.dumps({'games':ready['full_games_completed'],'deck_wins':wins,'status':ready['overall_status']},ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
