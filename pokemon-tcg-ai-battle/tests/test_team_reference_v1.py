from __future__ import annotations
from mage_ptcg.opponents.team_reference_v1 import identities
def test_team_references_are_not_qualified_without_projected_ipc() -> None:
    rows=identities()
    assert len(rows)==3
    assert {x['category'] for x in rows}=={'TEAM_REFERENCE_LINEAGE'}
    assert {x['qualification_status'] for x in rows}=={'TEAM_REFERENCE_BLOCKED_PRIVACY'}
