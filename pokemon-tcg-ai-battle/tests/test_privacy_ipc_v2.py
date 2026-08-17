from __future__ import annotations
import pytest
from mage_ptcg.opponents.privacy_ipc_v2 import PrivacyProtocolError,TeamReferenceRequestV2,SCHEMA
def test_safe_ipc_rejects_unclassified_actor_view() -> None:
    x=TeamReferenceRequestV2(SCHEMA,'r','t','a'*64,0,{'public_state':{},'own_private':{},'visible_history':[]},'d',[],{},None,1,())
    x.validate(); assert x.digest
    bad=TeamReferenceRequestV2(**{**x.__dict__,'actor_view':{'raw_observation':{}}})
    with pytest.raises(PrivacyProtocolError):bad.validate()
