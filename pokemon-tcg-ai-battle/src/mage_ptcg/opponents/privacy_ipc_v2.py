"""ActorInformationView-only Team IPC v2, deliberately fail-closed."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib, json
from collections.abc import Mapping
from typing import Any
from mage_ptcg.decision_state import build_decision_state

SCHEMA="team-reference-request-v2"; MAX_BYTES=262144
class PrivacyProtocolError(ValueError): pass
@dataclass(frozen=True)
class TeamReferenceRequestV2:
    schema_version:str; request_id:str; team_reference_id:str; package_hash:str; actor_side:int
    actor_view:dict[str,object]; actor_view_digest:str; legal_action_keys:list[dict[str,object]]; selection_context:dict[str,object]
    own_deck_identity:str|None; runtime_budget_ms:int; capabilities:tuple[str,...]
    def payload(self)->dict[str,object]:
        return {**asdict(self),"capabilities":list(self.capabilities)}
    def canonical_bytes(self)->bytes:
        self.validate(); return json.dumps(self.payload(),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    @property
    def digest(self)->str:return hashlib.sha256(b'team-ipc-v2\0'+self.canonical_bytes()).hexdigest()
    def validate(self)->None:
        if self.schema_version!=SCHEMA or not self.request_id or not self.team_reference_id or len(self.package_hash)!=64: raise PrivacyProtocolError('identity')
        if self.actor_side not in (0,1) or self.runtime_budget_ms<=0:raise PrivacyProtocolError('runtime')
        if set(self.actor_view)!={'public_state','own_private','visible_history'}:raise PrivacyProtocolError('actor-view allowlist')
        if len(json.dumps(self.payload(),default=str).encode())>MAX_BYTES:raise PrivacyProtocolError('size')

def project_request(raw: Mapping[str,Any], *, request_id:str, team_reference_id:str, package_hash:str, runtime_budget_ms:int=5000)->TeamReferenceRequestV2:
    """Raw input is consumed only here and never appears in the return value."""
    state=build_decision_state(raw); view=state.actor_view
    actor={"public_state":view.public_state,"own_private":view.own_private_state,"visible_history":list(view.visible_history)}
    legal=[x.action_key.to_canonical_payload() for x in state.legal_actions]
    select=raw.get('select')
    if not isinstance(select,Mapping):raise PrivacyProtocolError('selection context absent')
    context={k:select.get(k) for k in ('type','minCount','maxCount','context') if k in select and type(select.get(k)) in (int,str,type(None))}
    req=TeamReferenceRequestV2(SCHEMA,request_id,team_reference_id,package_hash,view.actor,actor,view.digest,legal,context,None,runtime_budget_ms,('actor-view-only','canonical-json','no-raw-observation'))
    req.validate();return req

def restricted_legacy_adapter(_request:TeamReferenceRequestV2)->None:
    """No raw-like object is synthesized: hidden-required legacy code must block."""
    raise PrivacyProtocolError('PRIVACY_BLOCKED_REQUIRED_FIELD: legacy Observation adapter not safely constructible')
