from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.offline_scaleup.gpu_student_v2 import build_dataset, outcome_sample_weight, train
from mage_ptcg.offline_scaleup.multiteacher import build_registry, build_schedule
from mage_ptcg.offline_scaleup.pipeline import DATASET_SCHEMA
from mage_ptcg.student.dataset import build_rule_bc_example


def _observation() -> dict[str, object]:
    card={"id":1,"serial":0,"playerIndex":0,"hp":100,"maxHp":100,"appearThisTurn":False,"energies":[],"energyCards":[],"tools":[],"preEvolution":[]}
    player={"active":[],"asleep":False,"bench":[],"benchMax":5,"burned":False,"confused":False,"deckCount":53,"discard":[],"hand":[card],"handCount":1,"paralyzed":False,"poisoned":False,"prize":[object() for _ in range(6)]}
    return {"current":{"energyAttached":False,"firstPlayer":0,"players":[player,player],"result":-1,"retreated":False,"stadium":[],"stadiumPlayed":False,"supporterPlayed":False,"turn":2,"turnActionCount":3,"yourIndex":0},"select":{"context":0,"maxCount":1,"minCount":1,"option":[{"type":14},{"type":13,"attackId":1}],"type":0},"step":7}


def test_gpu_dataset_preserves_all_five_splits_and_is_resumable(tmp_path: Path) -> None:
    example=build_rule_bc_example(_observation(),deck=[1]*60,source_id="fixture",source_revision="test")
    source=tmp_path/"source.jsonl"
    rows=[]
    for index, split in enumerate(("train","validation","test","opponent_holdout","deck_holdout")):
        rows.append({"schema_version":DATASET_SCHEMA,"split":split,"episode_id":f"ep-{index}","candidate_side":index%2,"candidate_outcome":"WIN" if index % 2 == 0 else "LOSS","opponent_id":"opponent","opponent_type":"RULE_V0_DECK","opponent_deck_fingerprint":"deck","teacher_identity":"rule-v0","teacher_type":"RULE_V0_DECK","teacher_trust":"TRUSTED","rule_bc_example":example.to_dict()})
    source.write_text("".join(json.dumps(row)+"\n" for row in rows),encoding="utf-8")
    first=build_dataset(source=source,output_dir=tmp_path/"gpu",shard_size=1)
    second=build_dataset(source=source,output_dir=tmp_path/"gpu",shard_size=1)
    assert first==second and first["records"]=={split:1 for split in ("train","validation","test","opponent_holdout","deck_holdout")}
    assert first["parse_failures"]==first["illegal_targets"]==first["episode_leakage"]==0
    summary = train(dataset_dir=tmp_path / "gpu", output_dir=tmp_path / "model", device_name="cpu", epochs=1,
                    batch_size=1, workers=0, hidden=8, blocks=1, dropout=0.0, outcome_weighting=True)
    assert summary["outcome_weighting"] is True
    assert summary["candidate_outcome_weights"] == {"WIN": 1.25, "DRAW": 1.0, "LOSS": 0.75, "UNKNOWN": 1.0}


def test_outcome_weighting_is_bounded_and_unknown_is_neutral() -> None:
    assert outcome_sample_weight("WIN") == 1.25
    assert outcome_sample_weight("LOSS") == 0.75
    assert outcome_sample_weight("DRAW") == outcome_sample_weight(None) == 1.0
    assert outcome_sample_weight("unrecognized") == 1.0


def test_multiteacher_registry_excludes_team_native_candidate_contract(tmp_path: Path) -> None:
    entries=[
        {"opponent_id":"rule","opponent_type":"RULE_V0_DECK","loader":"rule_v0","runtime_fingerprint":"r","deck_fingerprint":"dr","family_id":None,"teacher_trust":"TRUSTED","validation_status":"VALIDATED","evaluation_eligibility":"ALLOWED","training_eligibility":"ALLOWED_FOR_VALID_FAULT_FREE_GAMES","evidence_paths":[]},
        {"opponent_id":"family","opponent_type":"FAMILY_SPECIFIC","loader":"family_specific_external_v1","runtime_fingerprint":"f","deck_fingerprint":"df","family_id":"LUCARIO","teacher_trust":"LIMITED","validation_status":"VALIDATED","evaluation_eligibility":"ALLOWED","training_eligibility":"ALLOWED_FOR_VALID_FAULT_FREE_GAMES","evidence_paths":[]},
        {"opponent_id":"team","opponent_type":"TEAM_NATIVE","loader":"team_native_subprocess_v1","runtime_fingerprint":"t","deck_fingerprint":"dt","family_id":None,"teacher_trust":"LIMITED","validation_status":"VALIDATED","evaluation_eligibility":"ALLOWED","training_eligibility":"ALLOWED_FOR_VALID_FAULT_FREE_GAMES","evidence_paths":[]},
    ]
    population=tmp_path/"population.json"; population.write_text(json.dumps({"semantic_population_digest":"p","population_id":"p1","entries":entries}),encoding="utf-8")
    registry=build_registry(population_path=population,output=tmp_path/"registry.json")
    assert {item["teacher_id"] for item in registry["teachers"]}=={"rule","family"}
    assert registry["exclusions"][0]["teacher_id"]=="team"
    schedule=build_schedule(registry_path=tmp_path/"registry.json",population_path=population,games=10,output=tmp_path/"schedule.json")
    assert schedule["planned_games"]==10 and schedule["balance"]["candidate_side"]=={"0":5,"1":5}
