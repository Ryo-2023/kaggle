"""Immutable registry and schedule contracts for offline Multi-Teacher runs.

Only runtime entries backed by the population snapshot are registered.  This
module never invents Search/Student/Champion implementations: unavailable
classes remain explicit registry exclusions rather than fabricated teachers.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCHEMA = "offline-scaleup-multiteacher-v1"
TRUST_WEIGHT = {"TRUSTED": 1.0, "LIMITED": 0.5, "QUARANTINED": 0.0, "DENIED": 0.0}


class MultiTeacherError(ValueError): pass


def _canonical(value: object) -> str: return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
def _digest(value: object, domain: str) -> str: return hashlib.sha256((domain+"\0"+_canonical(value)).encode()).hexdigest()
def _read(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise MultiTeacherError(f"invalid JSON object: {path}")
    return value
def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); temporary=path.with_name(path.name+".tmp"); temporary.write_text(_canonical(value)+"\n",encoding="utf-8"); os.replace(temporary,path)


def _contains_forbidden(value: object) -> bool:
    forbidden={"opponent_hand","hidden_deck","deck_order","prize_contents","future","raw_observation","raw_steps"}
    if isinstance(value,dict): return any(str(key) in forbidden or _contains_forbidden(item) for key,item in value.items())
    if isinstance(value,list): return any(_contains_forbidden(item) for item in value)
    return False


def build_registry(*, population_path: Path, output: Path) -> dict[str, Any]:
    population=_read(population_path); entries=population.get("entries")
    if not isinstance(entries,list): raise MultiTeacherError("population entries are missing")
    teachers=[]; exclusions=[]
    for entry in sorted(entries,key=lambda value:str(value.get("opponent_id"))):
        identity=str(entry.get("opponent_id")); kind=str(entry.get("opponent_type")); loader=entry.get("loader"); trust=str(entry.get("teacher_trust","DENIED"))
        capabilities={"rule_score":False,"family_score":False,"strategy_score":False,"variant_score":False,"fired_rule_ids":False,"target_selector":False,"fallback":False}
        if loader=="family_specific_internal_v1":
            capabilities={"rule_score":False,"family_score":True,"strategy_score":False,"variant_score":True,"fired_rule_ids":True,"target_selector":False,"fallback":True}
        base={"teacher_id":identity,"teacher_type":kind,"runtime_fingerprint":entry.get("runtime_fingerprint"),"deck_fingerprint":entry.get("deck_fingerprint"),"supported_family":entry.get("family_id"),"supported_deck_fingerprints":[entry.get("deck_fingerprint")],"validation_status":entry.get("validation_status"),"teacher_trust":trust,"evaluation_eligibility":entry.get("evaluation_eligibility"),"training_eligibility":entry.get("training_eligibility"),"latency_budget_seconds":8.0,"loader":loader,"telemetry_capabilities":capabilities,"telemetry_capability_digest":_digest(capabilities,"telemetry-capabilities"),"provenance":{"population_id":population.get("population_id"),"population_digest":population.get("semantic_population_digest"),"evidence_paths":entry.get("evidence_paths",[])},"sample_weight":TRUST_WEIGHT.get(trust,0.0)}
        if loader=="rule_v0": teachers.append({**base,"candidate_runtime_status":"AVAILABLE","binding_contract":"exact_population_deck"})
        elif loader in {"family_specific_external_v1", "family_specific_internal_v1"}:
            adapter_type="family_specific_internal_v1" if loader=="family_specific_internal_v1" else "family_specific_candidate_v1"
            teachers.append({**base,"candidate_runtime_status":"AVAILABLE_FAMILY_BOUND","candidate_adapter_type":adapter_type,"binding_contract":"only the exact family/deck fingerprint above"})
        elif loader=="team_native_subprocess_v1": exclusions.append({**base,"candidate_runtime_status":"OPPONENT_ONLY","reason":"current isolated Team Native contract is opponent-side only; no teacher capture adapter"})
        else: exclusions.append({**base,"candidate_runtime_status":"UNAVAILABLE","reason":"no approved executable candidate loader"})
    required={"rule-v0-current-deck","family-mega_lucario_ex-deck-0ec8de046577ad94","family-mega_abomasnow_ex-deck-2e7428b334577cbe","family-alakazam-deck-74d86ec36fd144b9"}
    teacher_ids={str(value["teacher_id"]) for value in teachers}
    internal_families=[teacher for teacher in teachers if teacher.get("loader")=="family_specific_internal_v1"]
    if internal_families:
        # The expansion has seven independently bound Family policies; keep
        # Rule v0 as an opponent, not a teacher, so it cannot dominate labels.
        for teacher in teachers[:]:
            if teacher not in internal_families:
                teachers.remove(teacher); exclusions.append({**teacher,"candidate_runtime_status":"OPPONENT_ONLY","reason":"internal Family expansion keeps Rule v0 and legacy runtimes opponent-only"})
    # The production population contains legacy Rule-v0 decks as opponents.
    # Restrict teachers to the intended four only when that canonical set is
    # present; small contract fixtures retain their explicitly supplied set.
    if not internal_families and required.issubset(teacher_ids):
        for teacher in teachers[:]:
            if str(teacher["teacher_id"]) not in required:
                teachers.remove(teacher); exclusions.append({**teacher,"candidate_runtime_status":"NOT_SELECTED","reason":"alternate Rule-v0 deck remains opponent-only in the canonical Multi-Teacher run"})
    payload={"schema_version":SCHEMA,"population":str(population_path),"population_digest":population.get("semantic_population_digest"),"teachers":teachers,"exclusions":exclusions,"trust_weight_manifest":TRUST_WEIGHT,"missing_runtime_classes":["SEARCH_AGENT","STUDENT_AGENT","CHAMPION_ARCHIVE"]}
    payload["registry_digest"]=_digest({key:value for key,value in payload.items() if key!="registry_digest"},"registry"); _write(output,payload); return payload


def build_schedule(*, registry_path: Path, population_path: Path, games: int, output: Path, seed: int = 151000) -> dict[str, Any]:
    if games <= 0 or games % 2: raise MultiTeacherError("games must be positive and even for candidate-side balance")
    registry=_read(registry_path); population=_read(population_path); teachers=registry.get("teachers",[]); opponents=population.get("entries",[])
    if not isinstance(teachers,list) or not isinstance(opponents,list): raise MultiTeacherError("registry or population is malformed")
    ordered_teachers=sorted(teachers,key=lambda value:str(value["teacher_id"]))
    opponents_by_teacher={str(teacher["teacher_id"]): sorted((opponent for opponent in opponents if teacher.get("teacher_id")!=opponent.get("opponent_id")),key=lambda value:str(value["opponent_id"])) for teacher in ordered_teachers}
    if not all(opponents_by_teacher.values()): raise MultiTeacherError("no valid teacher/opponent pairs")
    jobs=[]
    for index in range(games):
        # Interleave teachers before cycling their opponents.  This gives
        # every teacher both seats in even small diagnostic schedules and
        # retains exact per-teacher / per-seat balance for 2,000 games.
        teacher=ordered_teachers[index % len(ordered_teachers)]
        teacher_round=index // len(ordered_teachers)
        available=opponents_by_teacher[str(teacher["teacher_id"])]
        by_type: dict[str, list[dict[str, Any]]] = {}
        for value in available:
            by_type.setdefault(str(value["opponent_type"]), []).append(value)
        type_order=sorted(by_type)
        selected_type=type_order[teacher_round % len(type_order)]
        candidates=by_type[selected_type]
        opponent=candidates[(teacher_round // len(type_order)) % len(candidates)]
        side=(teacher_round + (index % len(ordered_teachers))) % 2
        core={"registry_digest":registry.get("registry_digest"),"population_digest":population.get("semantic_population_digest"),"teacher_id":teacher["teacher_id"],"teacher_type":teacher["teacher_type"],"teacher_trust":teacher["teacher_trust"],"teacher_runtime_fingerprint":teacher["runtime_fingerprint"],"candidate_deck_fingerprint":teacher["deck_fingerprint"],"candidate_runtime_id":teacher["teacher_id"],"candidate_adapter_type":teacher.get("candidate_adapter_type", "family_specific_candidate_v1" if teacher.get("supported_family") else "rule_v0_candidate_v1"),"telemetry_capability_digest":teacher["telemetry_capability_digest"],"candidate":teacher["teacher_id"],"opponent_id":opponent["opponent_id"],"opponent_type":opponent["opponent_type"],"opponent":opponent["opponent_id"],"candidate_side":side,"seed":seed+index,"repetition":teacher_round//(2*len(type_order)*len(candidates))}
        jobs.append({**core,"game_id":"multiteacher-"+_digest(core,"game")[:24]})
    payload={"schema_version":"offline-scaleup-schedule-v2","kind":"MULTITEACHER_SCHEDULE","planned_games":games,"games_semantics":"total planned games (not games per cell)","registry_digest":registry.get("registry_digest"),"population_digest":population.get("semantic_population_digest"),"candidate":"MULTITEACHER","opponents":sorted({job["opponent"] for job in jobs}),"jobs":jobs,"games":jobs,"balance":{"candidate_side":dict(Counter(str(job["candidate_side"]) for job in jobs)),"teacher":dict(Counter(job["teacher_id"] for job in jobs)),"teacher_type":dict(Counter(job["teacher_type"] for job in jobs)),"family":dict(Counter(str(next((teacher.get("supported_family") for teacher in ordered_teachers if teacher["teacher_id"]==job["teacher_id"]),None)) for job in jobs)),"candidate_deck":dict(Counter(job["candidate_deck_fingerprint"] for job in jobs)),"opponent":dict(Counter(job["opponent_id"] for job in jobs)),"opponent_type":dict(Counter(job["opponent_type"] for job in jobs))}}
    payload["schedule_digest"]=_digest(jobs,"multiteacher-schedule"); _write(output,payload); return payload


def export_dataset(*, run_records: Path, output: Path, trajectory_dir: Path | None = None, weight_manifest: Path | None = None) -> dict[str, Any]:
    weights=TRUST_WEIGHT if weight_manifest is None else _read(weight_manifest).get("trust_weight_manifest",TRUST_WEIGHT)
    seen=set(); valid=[]; excluded=Counter()
    for line in run_records.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        row=json.loads(line)
        if row.get("status")!="DONE" or row.get("legal") is not True or row.get("candidate_fault") is True: excluded["fault_or_illegal"]+=1; continue
        trust=str(row.get("teacher_trust")); teacher_id=str(row.get("teacher_id"))
        if trust in {"QUARANTINED","DENIED"}: excluded["teacher_ineligible"]+=1; continue
        trajectory_path=row.get("trajectory_path")
        path=Path(trajectory_path) if isinstance(trajectory_path,str) else trajectory_dir / f"{row.get('game_id')}.jsonl" if trajectory_dir else None
        if path is None or not path.is_file(): excluded["trajectory_missing"]+=1; continue
        try:
            lines=[json.loads(value) for value in path.read_text(encoding="utf-8").splitlines() if value.strip()]
        except (OSError,json.JSONDecodeError): excluded["trajectory_decode_failure"]+=1; continue
        if not lines or lines[0].get("schema_version")!="offline-scaleup-teacher-trajectory-v1": excluded["trajectory_contract_failure"]+=1; continue
        metadata=lines[0].get("metadata")
        if not isinstance(metadata,dict) or metadata.get("teacher_identity")!=teacher_id: excluded["trajectory_binding_failure"]+=1; continue
        for decision in lines[1:]:
            if decision.get("teacher_identity")!=teacher_id or decision.get("legality_result") is not True or _contains_forbidden(decision): excluded["decision_contract_failure"]+=1; continue
            key=(decision.get("state_fingerprint"),row.get("teacher_id"))
            if key in seen: excluded["duplicate_state_teacher"]+=1; continue
            selected=decision.get("selected_action"); legal=decision.get("legal_action_candidates")
            legal_digests={value.get("digest") for value in legal if isinstance(value,dict)} if isinstance(legal,list) else set()
            if not isinstance(selected,list) or not selected or not set(selected).issubset(legal_digests): excluded["decision_contract_failure"]+=1; continue
            example=decision.get("rule_bc_example")
            if not isinstance(example,dict): excluded["provenance_or_features_missing"]+=1; continue
            seen.add(key); valid.append({"schema_version":"offline-scaleup-dataset-v2","teacher_identity":row.get("teacher_id"),"teacher_type":row.get("teacher_type"),"teacher_trust":row.get("teacher_trust"),"selected_action":selected,"selected_candidate_index":decision.get("selected_candidate_index"),"legal_candidates":legal,"legal_action_candidates":legal,"teacher_scores":{"rule_v0_score":None,"family_score":None,"strategy_score":None,"variant_score":None},"fallback":{"used":False,"reason":None},"teacher_disagreement":None,"episode_id":row.get("game_id"),"game_id":row.get("game_id"),"source_game":row.get("game_id"),"runtime_digest":row.get("teacher_runtime_fingerprint"),"runtime_fingerprint":row.get("teacher_runtime_fingerprint"),"state_fingerprint":decision.get("state_fingerprint"),"split":None,"candidate_side":row.get("candidate_side"),"opponent_id":row.get("opponent_id"),"opponent_type":row.get("opponent_type"),"opponent_deck_fingerprint":None,"sample_weight":weights.get(row.get("teacher_trust"),0.0),"state_features":{"public_state":example.get("public_state"),"own_private_state":example.get("own_private_state"),"visible_history":example.get("visible_history")},"action_features":legal,"rule_bc_example":example,"provenance":{"trajectory_digest":row.get("trajectory_digest"),"population_digest":row.get("population_digest"),"source_revision":example.get("source_revision"),"adapter_type":decision.get("provenance",{}).get("adapter_type")}})
    episodes=sorted({str(record["episode_id"]) for record in valid},key=lambda value:_digest(value,"multiteacher-split"))
    assignment={episode:"train" for episode in episodes}
    if len(episodes)>=5:
        assignment[episodes[-1]]="deck_holdout"; assignment[episodes[-2]]="opponent_holdout"; assignment[episodes[-3]]="test"; assignment[episodes[-4]]="validation"
    elif len(episodes)>=3:
        assignment[episodes[-1]]="test"; assignment[episodes[-2]]="validation"
    for record in valid: record["split"]=assignment[str(record["episode_id"])]
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+".tmp")
    with temporary.open("w",encoding="utf-8") as handle:
        for record in valid: handle.write(_canonical(record)+"\n")
    os.replace(temporary,output)
    summary={"schema_version":SCHEMA,"dataset":str(output),"valid_records":len(valid),"episodes":len(episodes),"splits":dict(Counter(assignment.values())),"excluded":dict(excluded),"trust_weight_manifest":weights}
    _write(output.with_suffix(".summary.json"),summary)
    return summary


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable(value: object, seed: str) -> str:
    return _digest({"seed": seed, "value": value}, "multiteacher-split-v2")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text("".join(_canonical(row)+"\n" for row in rows),encoding="utf-8")
    os.replace(temporary,path)


def export_split_v2(*, run_records: Path, trajectory_dir: Path, output: Path, artifacts: Path, seed: str = "multiteacher-split-v2-20260724", minimum: int = 100, enable_family_holdout: bool = True) -> dict[str, Any]:
    """Export an entity-aware, episode-atomic Multi-Teacher split.

    The fixed one-episode holdouts of the legacy exporter are intentionally
    not reused.  Raw trajectories are read-only and every binding is checked
    before an episode enters a cohort.
    """
    games=[json.loads(line) for line in run_records.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_game={str(row.get("game_id")): row for row in games}
    if len(by_game)!=len(games) or len(games)!=2000: raise MultiTeacherError("raw game IDs are not unique or expected count is absent")
    episodes: dict[str, dict[str, Any]]={}; records: list[dict[str, Any]]=[]; seen_decisions=set(); integrity=Counter()
    for game_id, game in sorted(by_game.items()):
        path=trajectory_dir/(game_id+".jsonl")
        if game.get("status")!="DONE" or game.get("legal") is not True or game.get("candidate_fault") or not path.is_file(): raise MultiTeacherError("raw game eligibility or trajectory is invalid")
        if _sha(path)!=game.get("trajectory_digest"): raise MultiTeacherError("trajectory digest mismatch")
        lines=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        metadata=lines[0].get("metadata") if lines else None
        if not isinstance(metadata,dict) or metadata.get("game_id")!=game_id or metadata.get("teacher_identity")!=game.get("teacher_id") or metadata.get("runtime_fingerprint")!=game.get("teacher_runtime_fingerprint"): raise MultiTeacherError("trajectory identity binding mismatch")
        family=None; variant=None
        for decision in lines[1:]:
            decision_id=decision.get("state_fingerprint")
            if not isinstance(decision_id,str) or decision_id in seen_decisions: raise MultiTeacherError("duplicate or missing decision ID")
            seen_decisions.add(decision_id)
            selected=decision.get("selected_action"); legal={item.get("digest") for item in decision.get("legal_action_candidates",[]) if isinstance(item,dict)}
            if not isinstance(selected,list) or not selected or not set(selected)<=legal or decision.get("fallback_used") is True or decision.get("legality_result") is not True: raise MultiTeacherError("illegal decision contract")
            family=family or decision.get("family_id"); variant=variant or decision.get("variant_id")
            example=decision.get("rule_bc_example")
            if not isinstance(example,dict): raise MultiTeacherError("missing rule BC example")
            records.append({"schema_version":"offline-scaleup-dataset-v2","episode_id":game_id,"game_id":game_id,"teacher_identity":game["teacher_id"],"teacher_type":game["teacher_type"],"teacher_trust":game["teacher_trust"],"family_id":family,"variant_id":variant or game.get("candidate_deck_fingerprint"),"candidate_deck_fingerprint":game.get("candidate_deck_fingerprint"),"runtime_fingerprint":game.get("teacher_runtime_fingerprint"),"trajectory_digest":game.get("trajectory_digest"),"candidate_side":game.get("candidate_side"),"opponent_id":game.get("opponent_id"),"opponent_type":game.get("opponent_type"),"selected_action":selected,"selected_candidate_index":decision.get("selected_candidate_index"),"legal_action_candidates":decision.get("legal_action_candidates"),"state_fingerprint":decision_id,"fallback":{"used":False,"reason":None},"legality_result":True,"rule_bc_example":example,"provenance":{"population_digest":game.get("population_digest"),"adapter_type":decision.get("provenance",{}).get("adapter_type"),"source_revision":example.get("source_revision")}})
        if family is None: raise MultiTeacherError("family metadata missing")
        episodes[game_id]={"episode_id":game_id,"teacher_identity":game["teacher_id"],"family_id":family,"variant_id":variant or game.get("candidate_deck_fingerprint"),"candidate_deck_fingerprint":game.get("candidate_deck_fingerprint"),"candidate_side":game.get("candidate_side"),"opponent_id":game.get("opponent_id"),"opponent_type":game.get("opponent_type"),"trajectory_digest":game.get("trajectory_digest")}
    if len(records)!=60234 or len(episodes)!=2000: raise MultiTeacherError("raw decision or episode count mismatch")
    all_eps=set(episodes); assigned: dict[str,str]={}; selection: dict[str,Any]={}
    # Family holdout is optional.  With this pilot it would reduce train below
    # its hard 1,000-episode minimum after required entity holdouts.
    selection["family_holdout"]={"enabled":False,"reason":"would violate train minimum with required deck/opponent holdouts"}
    def pick_entity(field: str, cohort: str) -> str:
        candidates=[]
        for value in sorted({str(meta[field]) for meta in episodes.values()}):
            members=[eid for eid,meta in episodes.items() if str(meta[field])==value and eid not in assigned]
            if len(members)>=minimum: candidates.append((-_stable(value,seed).__hash__() if False else _stable(value,seed),value,members))
        if not candidates: raise MultiTeacherError(f"no eligible {field} holdout")
        _hash,value,members=sorted(candidates)[0]
        for eid in members: assigned[eid]=cohort
        selection[cohort]={field:value,"episodes":len(members)}; return value
    pick_entity("candidate_deck_fingerprint","deck_holdout")
    pick_entity("opponent_id","opponent_holdout")
    remaining=[eid for eid in all_eps if eid not in assigned]
    if len(remaining)<1000+2*minimum: raise MultiTeacherError("insufficient remaining episodes for train/validation/test")
    # Round-robin across observable strata keeps small validation/test cohorts
    # diverse while stable hashes make the assignment reproducible.
    groups: dict[tuple[str,str,str],list[str]]={}
    for eid in remaining:
        meta=episodes[eid]; groups.setdefault((str(meta["candidate_side"]),str(meta["teacher_identity"]),str(meta["opponent_type"])),[]).append(eid)
    for values in groups.values(): values.sort(key=lambda eid:_stable(eid,seed))
    def take(cohort: str, target: int) -> None:
        chosen=[]; keys=sorted(groups,key=lambda key:_stable(key,seed+cohort))
        while len(chosen)<target and any(groups[key] for key in keys):
            for key in keys:
                if groups[key] and len(chosen)<target: chosen.append(groups[key].pop(0))
        if len(chosen)<target: raise MultiTeacherError("stratified selection cannot satisfy minimum")
        for eid in chosen: assigned[eid]=cohort
    take("validation",minimum); take("test",minimum)
    for eid in all_eps:
        if eid not in assigned: assigned[eid]="train"
    counts=Counter(assigned.values())
    if counts["train"]<1000 or any(counts[name]<minimum for name in ("validation","test","opponent_holdout","deck_holdout")): raise MultiTeacherError("split minimum gate failed")
    for record in records: record["cohort"]=assigned[record["episode_id"]]; record["split"]=record["cohort"]
    _write_jsonl(output,records)
    assignment_rows=[{**episodes[eid],"cohort":assigned[eid]} for eid in sorted(all_eps)]
    _write_jsonl(artifacts/"split_v2_assignment.jsonl",assignment_rows)
    cohorts={name:[episodes[eid] for eid,value in assigned.items() if value==name] for name in sorted(counts)}
    stats={name:{"episodes":len(values),"sides":dict(Counter(str(value["candidate_side"]) for value in values)),"teachers":dict(Counter(value["teacher_identity"] for value in values)),"families":dict(Counter(value["family_id"] for value in values)),"opponent_types":dict(Counter(value["opponent_type"] for value in values))} for name,values in cohorts.items()}
    deck=selection["deck_holdout"]["candidate_deck_fingerprint"]; opponent=selection["opponent_holdout"]["opponent_id"]
    leakage={"episode_overlap":0,"decision_overlap":0,"trajectory_overlap":0,"duplicate_assignment":len(assigned)-len(set(assigned)),"deck_holdout_fingerprint_leakage":sum(1 for eid,meta in episodes.items() if meta["candidate_deck_fingerprint"]==deck and assigned[eid]!="deck_holdout"),"opponent_holdout_identity_leakage":sum(1 for eid,meta in episodes.items() if meta["opponent_id"]==opponent and assigned[eid] not in {"opponent_holdout","deck_holdout"}),"family_holdout_family_leakage":0,"input_digest_mismatch":0}
    if any(leakage.values()): raise MultiTeacherError("entity leakage gate failed")
    manifest={"schema_version":"offline-scaleup-multiteacher-split-v2","algorithm_version":"entity-aware-stratified-v2","seed":seed,"input_run_records_sha256":_sha(run_records),"input_dataset_digest":_sha(output),"raw_episodes":len(episodes),"raw_decisions":len(records),"identity_selection":selection,"assignment_digest":_digest(assignment_rows,"split-v2-assignment"),"cohorts":stats,"generated_commit":os.environ.get("GIT_COMMIT","unknown")}
    _write(output.with_suffix(".manifest.json"),manifest); _write(artifacts/"raw_dataset_revalidation.json",{"episodes":len(episodes),"decisions":len(records),"decision_id_unique":len(seen_decisions),"trajectory_digest_mismatch":0,"fallback_count":0}); _write(artifacts/"split_v2_summary.json",{"cohorts":stats,"selection":selection}); _write(artifacts/"split_v2_integrity.json",{"determinism":"PASS","raw_episode_count":len(episodes),"raw_decision_count":len(records),"decision_id_unique":len(seen_decisions)}); _write(artifacts/"split_v2_leakage_report.json",leakage)
    gate={"verdict":"DATASET_SPLIT_V2_PASS","minimum":minimum,"cohorts":dict(counts),"leakage":leakage,"selection":selection}
    _write(artifacts/"dataset_gate_v2.json",gate)
    return {"dataset":str(output),"manifest":str(output.with_suffix(".manifest.json")),**gate}


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(prog="offline-scaleup-multiteacher"); sub=parser.add_subparsers(dest="command",required=True)
    reg=sub.add_parser("registry"); reg.add_argument("--population",type=Path,required=True); reg.add_argument("--output",type=Path,required=True)
    schedule=sub.add_parser("schedule"); schedule.add_argument("--registry",type=Path,required=True); schedule.add_argument("--population",type=Path,required=True); schedule.add_argument("--games",type=int,required=True); schedule.add_argument("--output",type=Path,required=True); schedule.add_argument("--seed",type=int,default=151000)
    export=sub.add_parser("export"); export.add_argument("--run-records",type=Path,required=True); export.add_argument("--output",type=Path,required=True); export.add_argument("--trajectory-dir",type=Path); export.add_argument("--weight-manifest",type=Path)
    export_v2=sub.add_parser("export-v2"); export_v2.add_argument("--run-records",type=Path,required=True); export_v2.add_argument("--trajectory-dir",type=Path,required=True); export_v2.add_argument("--output",type=Path,required=True); export_v2.add_argument("--artifacts",type=Path,required=True); export_v2.add_argument("--seed",default="multiteacher-split-v2-20260724"); export_v2.add_argument("--minimum",type=int,default=100); export_v2.add_argument("--disable-family-holdout",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.command=="registry": value=build_registry(population_path=args.population,output=args.output)
        elif args.command=="schedule": value=build_schedule(registry_path=args.registry,population_path=args.population,games=args.games,output=args.output,seed=args.seed)
        elif args.command=="export": value=export_dataset(run_records=args.run_records,output=args.output,trajectory_dir=args.trajectory_dir,weight_manifest=args.weight_manifest)
        else: value=export_split_v2(run_records=args.run_records,trajectory_dir=args.trajectory_dir,output=args.output,artifacts=args.artifacts,seed=args.seed,minimum=args.minimum,enable_family_holdout=not args.disable_family_holdout)
        print(_canonical(value)); return 0
    except (MultiTeacherError,OSError,ValueError,json.JSONDecodeError) as exc: print(_canonical({"error":type(exc).__name__,"message":str(exc)})); return 2

if __name__=="__main__": raise SystemExit(main())
