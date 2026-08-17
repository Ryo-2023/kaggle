from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.offline_scaleup.candidate_runtime import CandidateRuntimeError, FamilySpecificCandidateAdapter, write_trajectory
from mage_ptcg.offline_scaleup.multiteacher import build_registry, build_schedule, export_dataset
from mage_ptcg.student.dataset import build_rule_bc_example


def _observation() -> dict[str, object]:
    card={"id":1,"serial":0,"playerIndex":0,"hp":100,"maxHp":100,"appearThisTurn":False,"energies":[],"energyCards":[],"tools":[],"preEvolution":[]}
    player={"active":[],"asleep":False,"bench":[],"benchMax":5,"burned":False,"confused":False,"deckCount":53,"discard":[],"hand":[card],"handCount":1,"paralyzed":False,"poisoned":False,"prize":[object() for _ in range(6)]}
    return {"current":{"energyAttached":False,"firstPlayer":0,"players":[player,player],"result":-1,"retreated":False,"stadium":[],"stadiumPlayed":False,"supporterPlayed":False,"turn":2,"turnActionCount":3,"yourIndex":0},"select":{"context":0,"maxCount":1,"minCount":1,"option":[{"type":14},{"type":13,"attackId":1}],"type":0},"step":7}


def _entry(identity: str, family: str | None, deck: str, loader: str) -> dict[str, object]:
    return {"opponent_id":identity,"opponent_type":"FAMILY_SPECIFIC" if family else "RULE_V0_DECK","loader":loader,"runtime_fingerprint":"runtime-"+identity,"deck_fingerprint":deck,"family_id":family,"teacher_trust":"LIMITED" if family else "TRUSTED","validation_status":"VALIDATED","evaluation_eligibility":"ALLOWED","training_eligibility":"ALLOWED_FOR_VALID_FAULT_FREE_GAMES","evidence_paths":[]}


def test_canonical_schedule_has_exact_total_and_balanced_teachers(tmp_path: Path) -> None:
    entries=[
        _entry("rule-v0-current-deck",None,"rule","rule_v0"),
        _entry("family-mega_lucario_ex-deck-0ec8de046577ad94","MEGA_LUCARIO_EX","a0e78dd4b5731f95ff14686ca5fa4c31fcd23ef7868c2ccc262fb50eaa450b39","family_specific_external_v1"),
        _entry("family-mega_abomasnow_ex-deck-2e7428b334577cbe","MEGA_ABOMASNOW_EX","cb0c1b3e0e87e77b270719f387d7d0fe11ae3807b6b461a2e498c77ca1813895","family_specific_external_v1"),
        _entry("family-alakazam-deck-74d86ec36fd144b9","ALAKAZAM","d3d6354cd3de3ab71677894265d46a07434ec7b0a199064b0de143206e09fd14","family_specific_external_v1"),
    ]
    population=tmp_path/"population.json"; population.write_text(json.dumps({"semantic_population_digest":"population","population_id":"p","entries":entries}),encoding="utf-8")
    registry=build_registry(population_path=population,output=tmp_path/"registry.json")
    schedule=build_schedule(registry_path=tmp_path/"registry.json",population_path=population,games=2000,output=tmp_path/"schedule.json")
    assert schedule["planned_games"] == 2000
    assert set(schedule["balance"]["teacher"].values()) == {500}
    assert schedule["balance"]["candidate_side"] == {"0":1000,"1":1000}
    assert set(schedule["balance"]["teacher_type"].values()) == {500,1500}


def test_family_binding_rejects_wrong_canonical_deck() -> None:
    entry=_entry("family-mega_lucario_ex-deck-0ec8de046577ad94","MEGA_LUCARIO_EX","not-lucario","family_specific_external_v1")
    with pytest.raises(CandidateRuntimeError,match="canonical deck"):
        FamilySpecificCandidateAdapter(entry).prepare([1]*60)


def test_atomic_trajectory_digest_and_faulted_game_exclusion(tmp_path: Path) -> None:
    example=build_rule_bc_example(_observation(),deck=[1]*60,source_id="game",source_revision="runtime")
    selected=[example.target_action_digests[0]]
    selected_index=[next(index for index,value in enumerate(example.legal_actions) if value["digest"] == selected[0])]
    decision={"schema_version":"offline-scaleup-teacher-decision-v1","teacher_identity":"rule","legality_result":True,"state_fingerprint":example.example_id,"selected_action":selected,"selected_candidate_index":selected_index,"legal_action_candidates":list(example.legal_actions),"rule_bc_example":example.to_dict(),"provenance":{"adapter_type":"rule_v0_candidate_v1"}}
    trajectory=tmp_path/"trajectories/game.jsonl"; digest=write_trajectory(trajectory,[decision],{"game_id":"game","teacher_identity":"rule","decision_count":1})
    assert digest == hashlib.sha256(trajectory.read_bytes()).hexdigest() and not trajectory.with_suffix(".jsonl.tmp").exists()
    records=tmp_path/"games.jsonl"
    rows=[{"game_id":"game","status":"DONE","legal":True,"candidate_fault":False,"teacher_id":"rule","teacher_type":"RULE_V0_DECK","teacher_trust":"TRUSTED","candidate_side":0,"opponent_id":"opponent","opponent_type":"RULE_V0_DECK","population_digest":"p","trajectory_path":str(trajectory),"trajectory_digest":digest,"teacher_runtime_fingerprint":"runtime"},{"game_id":"bad","status":"AGENT_ERROR","legal":False,"candidate_fault":True,"teacher_id":"rule","teacher_trust":"TRUSTED"}]
    records.write_text("".join(json.dumps(row)+"\n" for row in rows),encoding="utf-8")
    result=export_dataset(run_records=records,output=tmp_path/"dataset.jsonl")
    assert result["valid_records"] == 1 and result["excluded"]["fault_or_illegal"] == 1
    row=json.loads((tmp_path/"dataset.jsonl").read_text().strip())
    assert row["selected_action"] == selected and row["teacher_scores"]["family_score"] is None
