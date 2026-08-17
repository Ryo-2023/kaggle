"""on_epoch callback contract for the Student v1 trainer."""
from __future__ import annotations

from mage_ptcg.student.dataset import build_rule_bc_example
from mage_ptcg.student.model import train_model


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _observation() -> dict[str, object]:
    player = lambda card: {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def test_train_model_invokes_on_epoch_with_finite_loss_and_snapshot_weights() -> None:
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    calls: list[tuple[int, int, float]] = []

    def on_epoch(epoch_index, total_epochs, train_loss, weights, bias):
        calls.append((epoch_index, total_epochs, train_loss))
        assert len(weights) > 0
        assert bias == bias  # not NaN

    train_model([example], epochs=3, learning_rate=0.1, on_epoch=on_epoch)
    assert [c[0] for c in calls] == [0, 1, 2]
    assert all(c[1] == 3 for c in calls)
    assert all(loss == loss and loss != float("inf") for _, _, loss in calls)


def test_train_model_without_on_epoch_is_unaffected() -> None:
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    model = train_model([example], epochs=2, learning_rate=0.1)
    assert model is not None
