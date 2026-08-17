from __future__ import annotations
import json
from pathlib import Path
from mage_ptcg.opponent_ingest.review import classify_blocker, review_and_activate

def test_blocker_classification_is_total_and_fail_closed():
    assert classify_blocker({'activation_eligibility':'QUARANTINED'}, exact_source_ids=set()) == 'STATIC_SAFETY_REVIEW_REQUIRED'
    assert classify_blocker({'activation_eligibility':'MANUAL_REVIEW_REQUIRED','path':'agents/a.py','source_id':'x'}, exact_source_ids=set()) == 'NO_SUPPORTED_DECK'
    assert classify_blocker({'activation_eligibility':'MANUAL_REVIEW_REQUIRED','path':'tests/a.py','source_id':'x'}, exact_source_ids={'x'}) == 'ENTRYPOINT_UNRESOLVED'

def test_activation_excludes_rule_v0_and_requires_existing_evidence(tmp_path: Path):
    ingest=tmp_path/'ingest'; diversity=tmp_path/'div'; (ingest/'artifacts').mkdir(parents=True); (diversity/'artifacts').mkdir(parents=True)
    (ingest/'artifacts/agent_asset_registry.jsonl').write_text(json.dumps({'agent_id':'a','activation_eligibility':'MANUAL_REVIEW_REQUIRED','path':'agents/a.py','source_id':'x'})+'\n')
    (ingest/'artifacts/deck_asset_registry.jsonl').write_text('')
    family={'opponent_id':'family-x','opponent_type':'FAMILY_SPECIFIC','validation_status':'VALIDATED','availability_status':'AVAILABLE','evaluation_eligibility':'ALLOWED','family_id':'X','deck_id':'d','deck_fingerprint':'f','runtime_fingerprint':'r','provenance':{'primary_ids':[1]}}
    rule={'opponent_id':'rule','opponent_type':'RULE_V0_DECK'}
    (diversity/'artifacts/expanded_population_snapshot.json').write_text(json.dumps({'entries':[family,rule]}))
    (diversity/'artifacts/cross_type_smoke_metrics.json').write_text(json.dumps({'by_opponent':{'family-x':{'games':2,'legal':2,'faults':0}},'family_specificity_evidence':{'X':{'correct_gt_wrong':True,'wrong_playbook_false_positive_rate':0.0}}}))
    result=review_and_activate(ingest_root=ingest, diversity_root=diversity, output_root=tmp_path/'out')
    expanded=json.loads((tmp_path/'out/artifacts/expanded_family_population.json').read_text())
    assert result['verified_bindings']==1 and [e['opponent_id'] for e in expanded['entries']]==['family-x']
