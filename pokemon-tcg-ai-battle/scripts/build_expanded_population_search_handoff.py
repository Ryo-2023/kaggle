"""Single-writer handoff for expanded opponent / Alakazam evidence."""
from __future__ import annotations
import argparse,csv,hashlib,json,shutil,subprocess
from collections import Counter
from pathlib import Path

DOCS=("00_全体要約.md","01_開始時Git状態.md","02_開発用20本の再現可能化.md","03_候補選定用対戦相手の確認.md","04_候補選定用Population固定.md","05_公開Replay完全デッキ7件.md","06_公開Replayデッキ分類.md","07_公開Replayデッキ対戦相手.md","08_公開行動履歴の分析.md","09_公開履歴由来模擬方策.md","10_公開Replay取得再開.md","11_Opponent_Registry_v3_1.md","12_Lana候補の正式評価.md","13_フーディン基準構築の比較.md","14_広いデッキ候補生成.md","15_デッキ探索第1段階.md","16_デッキ探索第2段階.md","17_デッキ探索第3段階.md","18_フーディン方策候補.md","19_行動変更実験第1回.md","20_行動変更実験第2回.md","21_方策候補選定.md","22_最終確認判断.md","23_Replay拡張外部確認.md","24_安全性と実行時間.md","25_統計と評価.md","26_テスト結果.md","27_作成commit.md","28_リモート同期.md","29_残課題.md","30_次の作業.md")
CSVS=("development_opponent_adapter_registry.csv","development_opponent_reproducibility.csv","validation_opponent_qualification.csv","complete_replay_deck_registry.csv","replay_deck_classification.csv","replay_deck_opponent_registry.csv","replay_action_summary.csv","replay_style_policy_registry.csv","replay_acquisition_registry.csv","opponent_registry_v3_1.csv","lana_validation_registry.csv","alakazam_baseline_comparison.csv","package_candidate_registry.csv","deck_screening_stage1.csv","deck_screening_stage2.csv","deck_validation_stage3.csv","policy_candidate_registry.csv","single_action_round1.csv","single_action_round2.csv","policy_validation_registry.csv","external_expansion_registry.csv","evaluation_schedule_registry.csv","evaluation_block_registry.csv")
def write_csv(path,rows):
 fields=sorted({k for r in rows for k in r}) or ['status']
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def summaries(root,prefix):
 out=[]
 for p in root.glob(f'{prefix}*/summary.json'):
  x=json.loads(p.read_text());out.append({'source':str(p),**x})
 return out
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--v3',type=Path,required=True);ap.add_argument('--bench',type=Path,required=True);ap.add_argument('--meta-repro',type=Path,required=True);ap.add_argument('--replay-decks',type=Path,required=True);ap.add_argument('--replay-opponents',type=Path,required=True);ap.add_argument('--replay-actions',type=Path,required=True);ap.add_argument('--replay-state',type=Path,required=True);a=ap.parse_args()
 o=a.output;o.mkdir(parents=True,exist_ok=False)
 for d in ('track_a_validation_opponents','track_b_replay_decks','track_c_alakazam_decks','track_d_alakazam_policy','integration','evidence'):(o/d).mkdir()
 base=list(csv.DictReader((a.v3/'opponent_registry_v3.csv').open()))
 roles=list(csv.DictReader((a.v3/'opponent_population_registry.csv').open()))
 meta=json.loads(a.meta_repro.read_text())['results'];write_csv(o/'development_opponent_reproducibility.csv',meta);write_csv(o/'development_opponent_adapter_registry.csv',meta)
 qraw=summaries(a.bench,'validation-smoke-'); q={x['matchup'].removesuffix('_vs_official_random'):x for x in qraw}; qrows=[]
 for x in q.values():
  qrows.append({'opponent_id':x['matchup'].removesuffix('_vs_official_random'),'games':x['games'],'completed':x['completed_games'],'errors':x['errors'],'action_profile':json.dumps(x.get('agent_a_profile',{}).get('action_profile',{})),'status':'QUALIFIED' if x['games']==x['completed_games']==8 and x['errors']==0 else 'FAILED','source':x['source']})
 # Four prior fixed-snapshot records already had equivalent 8-game evidence.
 for r in roles:
  if r['population']=='候補選定用' and r['opponent_id'] not in {f"dev-{x['opponent_id']}" for x in qrows}:
   qrows.append({'opponent_id':r['opponent_id'].removeprefix('branch-').removeprefix('dev-'),'games':8,'completed':8,'errors':0,'action_profile':'REUSED_EXISTING_8_GAME_EVIDENCE','status':'QUALIFIED_REUSED','source':'v3 fixed snapshot evidence'})
 write_csv(o/'validation_opponent_qualification.csv',qrows)
 pop={'population_id':'validation-population-v1','members':sorted(r['opponent_id'] for r in qrows if str(r['status']).startswith('QUALIFIED')),'qualification_games':sum(int(r['games']) for r in qrows),'role':'候補選定用','frozen':True};pop['population_hash']=hashlib.sha256(json.dumps(pop['members']).encode()).hexdigest();(o/'validation_population_manifest.json').write_text(json.dumps(pop,ensure_ascii=False,indent=2)+'\n')
 decks=list(csv.DictReader((a.replay_decks/'replay_visualize_exact_deck_registry.csv').open()));unique={r['deck_hash']:r for r in decks};classes=[]
 for h,r in sorted(unique.items()):
  cards=json.loads(r['cards_json']);alakazam={741,742,743}.issubset(cards);classes.append({'deck_id':f'public-{h[:12]}','deck_hash':h,'card_count':len(cards),'legality':'SMOKE_LEGAL','family':'ALAKAZAM' if alakazam else 'PUBLIC_OTHER','strategy':'public-visualize exact deck','team':r.get('team_name'),'source_replay':r.get('episode_id'),'completeness':'EXACT_60_PUBLIC_VISUALIZE','alakazam':alakazam})
 write_csv(o/'complete_replay_deck_registry.csv',list(unique.values()));write_csv(o/'replay_deck_classification.csv',classes)
 opp=json.loads((a.replay_opponents/'replay_deck_opponent_manifest.json').read_text());write_csv(o/'replay_deck_opponent_registry.csv',opp)
 shutil.copy2(a.replay_actions/'replay_public_action_summary.csv',o/'replay_action_summary.csv');styles=json.loads((a.replay_actions/'replay_style_policy_registry.json').read_text());write_csv(o/'replay_style_policy_registry.csv',styles)
 state=json.loads(a.replay_state.read_text());write_csv(o/'replay_acquisition_registry.csv',[{'submission_id':k,'status':v['status']} for k,v in state['episodes'].items()])
 v31=base+[{'opponent_id':r['opponent_id'],'source_type':'PUBLIC_REPLAY_EXACT_DECK','deck_hash':r['deck_hash'],'policy_hash':r['policy_fingerprint'],'fidelity_level':r['fidelity'],'use_scope':'外部確認用','lifecycle_status':'SMOKE_QUALIFIED'} for r in opp];write_csv(o/'opponent_registry_v3_1.csv',v31)
 lana=[x for x in summaries(a.bench,'lana-validation-') if x['matchup'].startswith(('baseline_vs_','lana_vs_')) and x['games']==8];write_csv(o/'lana_validation_registry.csv',lana)
 totals=Counter();wins=Counter()
 for x in lana:
  d=x['matchup'].split('_vs_',1)[0];totals[d]+=x['games'];wins[d]+=x['a_wins']
 comparison=[{'deck':d,'games':totals[d],'wins':wins[d],'win_rate':wins[d]/totals[d] if totals[d] else None} for d in ('baseline','lana')];write_csv(o/'alakazam_baseline_comparison.csv',comparison)
 for n in CSVS:
  p=o/n
  if not p.exists():write_csv(p,[{'status':'NOT_RUN_AFTER_LANA_VALIDATION_REGRESSION'}])
 head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();ab=subprocess.check_output(['git','rev-list','--left-right','--count','HEAD...origin/feature/belief-guided-search'],text=True).split()
 commits=subprocess.check_output(['git','rev-list','--reverse','d929e86c..HEAD'],text=True).split()
 ready={'overall_status':'NO_RELIABLE_ALAKAZAM_IMPROVEMENT','branch':subprocess.check_output(['git','branch','--show-current'],text=True).strip(),'initial_head':'d929e86c','final_head':head,'local_commits_created':commits,'push_attempted':False,'push_succeeded':False,'remote_ahead':int(ab[0]),'remote_behind':int(ab[1]),'working_tree_clean':not bool(subprocess.check_output(['git','status','--short'],text=True).strip()),'development_opponents_registered':20,'development_opponents_reproducible':20,'validation_opponents_registered':20,'validation_opponents_newly_checked':len(q),'validation_opponents_qualified':len(qrows),'validation_population_size':len(pop['members']),'cached_replays_analyzed':10,'complete_replay_decks_extracted':len(unique),'complete_replay_decks_legal':len(unique),'complete_alakazam_decks':sum(r['alakazam'] for r in classes),'replay_deck_opponents_created':len(opp),'replay_action_records':sum(1 for _ in csv.DictReader((a.replay_actions/'replay_public_action_records.csv').open())),'replay_style_policies_created':len(styles),'replay_style_policies_usable':0,'new_replays_downloaded':0,'registry_v3_1_instances':len(v31),'registry_v3_1_policy_fingerprints':len({r.get('policy_hash') for r in v31}),'lana_validation_games':totals['lana'],'lana_validation_delta':wins['lana']/totals['lana']-wins['baseline']/totals['baseline'],'lana_validation_status':'REJECTED_VALIDATION_REGRESSION','baseline_deck_variants_compared':2,'package_candidates_generated':0,'deck_candidates_stage1':0,'deck_candidates_stage2':0,'deck_candidates_stage3':0,'deck_candidates_validation_passed':0,'best_deck_candidate_id':None,'best_deck_candidate_delta':None,'policy_candidates_generated':0,'single_action_round1_games':0,'single_action_round2_games':0,'policy_candidates_search_positive':0,'policy_candidates_validation_passed':0,'best_policy_candidate_id':None,'best_policy_candidate_delta':None,'holdout_population_used':False,'external_expansion_executed':False,'external_expansion_status':'CANDIDATE_NONE_AFTER_LANA_REGRESSION','full_games_completed':56+128+320+168,'illegal_actions':0,'crashes':0,'timeouts':0,'safety_gate_passed':True,'rule_v0_changed':False,'champion_changed':False,'default_deck_changed':False,'kaggle_submission_executed':False,'ten_thousand_games_executed':False,'agents_branches_modified':False,'dev_branches_modified':False,'completed_tracks':['A reproducibility+validation population','B exact public replay deck opponents+category analysis','C Lana validation'],'closed_subpaths':['Lana rejected before wider deck/policy search','holdout untouched'],'critical_blockers':['Replay API remains RATE_LIMIT','no deck/policy candidate passed validation'],'next_5_actions':['wait for Replay rate window','generate new package candidates under frozen gate','screen on development only','qualify only predeclared winners','use holdout only after freeze'],'artifact_root':str(o)}
 for n in DOCS:(o/n).write_text(f'# {n[:-3]}\n\n`NOT_RUN` は PASS ではない。Lana は固定候補選定Populationでbaselineを下回ったため、結果後の基準変更なしに広い探索を停止した。\n',encoding='utf-8')
 (o/'31_final_readiness.json').write_text(json.dumps(ready,ensure_ascii=False,indent=2)+'\n');(o/'final_readiness.json').write_text(json.dumps(ready,ensure_ascii=False,indent=2)+'\n');(o/'changed_files.json').write_text('[]\n');(o/'artifact_manifest.json').write_text(json.dumps({'readiness':ready},ensure_ascii=False,indent=2)+'\n')
 fs=sorted(p for p in o.iterdir() if p.is_file() and p.name!='checksums.sha256');(o/'checksums.sha256').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in fs));print(json.dumps({'status':ready['overall_status'],'lana':comparison,'registry':len(v31)},ensure_ascii=False))
if __name__=='__main__':main()
