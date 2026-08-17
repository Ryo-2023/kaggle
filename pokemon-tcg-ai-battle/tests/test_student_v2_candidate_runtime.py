from __future__ import annotations

import pytest

from mage_ptcg.offline_scaleup.gpu_student_v2 import _model
from mage_ptcg.offline_scaleup.student_v2_runtime import StudentV2CandidatePolicy, StudentV2RuntimeError


DECK = [1] * 60


def _observation() -> dict[str, object]:
    card = {"id": 1, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False,
            "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False,
              "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False,
              "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1,
                         "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
                         "turn": 2, "turnActionCount": 3, "yourIndex": 0},
            "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0},
            "step": 7}


def _real_model():
    import torch
    torch.manual_seed(0)
    model = _model(hidden=8, blocks=1, dropout=0.0)
    model.eval()
    return model


def test_choose_returns_bound_deck_for_bootstrap_probe() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    assert policy.choose({"select": None}) == DECK


def test_choose_selects_a_legal_in_range_action() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    selection = policy.choose(_observation())
    assert isinstance(selection, list)
    assert len(selection) == 1
    assert selection[0] in (0, 1)


def test_choose_declines_optional_selection_with_zero_min() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    observation = _observation()
    observation["select"] = {**observation["select"], "minCount": 0, "maxCount": 0}
    assert policy.choose(observation) == []


def test_choose_raises_on_non_mapping_observation() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    with pytest.raises(StudentV2RuntimeError, match="mapping"):
        policy.choose(["not", "a", "mapping"])


def test_choose_raises_on_malformed_select_contract() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    observation = _observation()
    observation["select"] = {"minCount": 1, "maxCount": 1}  # no "option" list
    with pytest.raises(StudentV2RuntimeError, match="malformed"):
        policy.choose(observation)


def test_choose_raises_on_non_integer_selection_bounds() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    observation = _observation()
    observation["select"] = {**observation["select"], "minCount": "1"}
    with pytest.raises(StudentV2RuntimeError, match="cardinality"):
        policy.choose(observation)


def test_choose_raises_on_non_finite_scores() -> None:
    class _NanModel:
        def __call__(self, state, action, mask):
            import torch
            return torch.full((1, action.shape[1]), float("nan"))

        def eval(self):
            return self

    policy = StudentV2CandidatePolicy(model=_NanModel(), device="cpu", deck=DECK)
    with pytest.raises(StudentV2RuntimeError, match="non-finite"):
        policy.choose(_observation())


def test_rejects_deck_of_wrong_size() -> None:
    with pytest.raises(StudentV2RuntimeError, match="60-card"):
        StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=[1] * 59)


def test_as_agent_accepts_optional_configuration_argument() -> None:
    policy = StudentV2CandidatePolicy(model=_real_model(), device="cpu", deck=DECK)
    agent = policy.as_agent()
    assert agent({"select": None}, {"seed": 1}) == DECK
