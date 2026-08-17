"""Safety boundaries for the offline curated-knowledge consumer."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from mage_ptcg.distillation.knowledge import (
    CuratedKnowledgeError,
    apply_priors,
    evaluation_weight,
    load_curated_knowledge,
    load_hard_constraints,
    load_search_priors,
    load_teacher_rules,
)


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "artifacts" / "team-knowledge-curated"


def _copy_pack(tmp_path: Path) -> Path:
    target = tmp_path / "curated"
    shutil.copytree(PACK, target)
    return target


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _observation(*, minimum: int = 1, maximum: int = 1, selection_type: object = 0, count: int = 2) -> dict[str, object]:
    return {"select": {"type": selection_type, "option": [{"type": 7} for _ in range(count)], "minCount": minimum, "maxCount": maximum}}


def _candidates(count: int = 2) -> list[dict[str, object]]:
    return [{"option_index": index, "action_id": f"ak-{index}", "action_type": "PLAY_POKEMON"} for index in range(count)]


def test_registry_is_the_only_teacher_source_and_has_exactly_22_rules() -> None:
    knowledge = load_curated_knowledge(PACK)
    assert len(knowledge.teacher_rules) == 22
    assert {item.teacher_id for item in knowledge.teacher_rules} == {f"TR-{number:06d}" for number in range(1, 23)}
    assert len(knowledge.search_priors) == 66
    assert knowledge.load_metrics["teacher_rules_loaded"] == 22


@pytest.mark.parametrize("mutation, match", [
    (lambda rows: rows.append(dict(rows[0])), "duplicate teacher_id"),
    (lambda rows: rows[0].__setitem__("canonical_rule_id", "missing"), "missing canonical_rule_id"),
    (lambda rows: rows[0].__setitem__("observable_condition", ""), "observable_condition"),
    (lambda rows: rows[0].__setitem__("deck_scope", []), "deck_scope"),
    (lambda rows: rows[0].__setitem__("decision", "HOLD_FOR_EXPERIMENT"), "non-accepted decision"),
])
def test_teacher_loader_rejects_unsafe_registry_rows(tmp_path: Path, mutation: object, match: str) -> None:
    pack = _copy_pack(tmp_path)
    path = pack / "executable_teacher_registry.jsonl"
    rows = _rows(path)
    mutation(rows)  # type: ignore[operator]
    _write_rows(path, rows)
    with pytest.raises(CuratedKnowledgeError, match=match):
        load_teacher_rules(pack)


def test_teacher_loader_rejects_malformed_jsonl(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    path = pack / "executable_teacher_registry.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{bad\n", encoding="utf-8")
    with pytest.raises(CuratedKnowledgeError, match="malformed JSONL"):
        load_teacher_rules(pack)


def test_hard_constraints_have_expected_ids_and_win_over_soft_scores() -> None:
    knowledge = load_curated_knowledge(PACK)
    assert [item.constraint_id for item in load_hard_constraints(PACK)] == [f"HC-{number:06d}" for number in range(1, 6)]
    candidates = _candidates()
    candidates[0].update({"applicable_rule_ids": ["TR-000010"], "observable_condition_met": True, "curated_score_delta": -1000})
    result = apply_priors(_observation(), candidates, knowledge)
    assert result.fallback_reason is None
    assert result.selected_action_ids == ("ak-1",)


def test_illegal_ambiguous_and_unknown_selection_inputs_fallback() -> None:
    knowledge = load_curated_knowledge(PACK)
    illegal = _candidates()
    illegal[0]["option_index"] = 99
    assert apply_priors(_observation(), illegal, knowledge).fallback_reason is not None
    duplicate = _candidates()
    duplicate[1]["option_index"] = 0
    assert "ambiguous" in str(apply_priors(_observation(), duplicate, knowledge).fallback_reason)
    assert apply_priors(_observation(selection_type="UNKNOWN"), _candidates(), knowledge).fallback_reason is not None
    assert "registration" in str(apply_priors({"select": None}, _candidates(), knowledge).fallback_reason)


def test_private_observation_or_candidate_field_is_rejected() -> None:
    knowledge = load_curated_knowledge(PACK)
    observation = _observation()
    observation["opponent_hand"] = [99]
    assert "forbidden" in str(apply_priors(observation, _candidates(), knowledge).fallback_reason)
    candidates = _candidates()
    candidates[0]["private_action_key_digest"] = "hidden"
    assert "forbidden" in str(apply_priors(_observation(), candidates, knowledge).fallback_reason)


def test_min_and_max_bounds_are_never_relaxed_by_priors() -> None:
    knowledge = load_curated_knowledge(PACK)
    candidates = _candidates(3)
    for candidate in candidates:
        candidate.update({"applicable_rule_ids": ["TR-000010"], "observable_condition_met": True, "curated_score_delta": -10})
    result = apply_priors(_observation(minimum=2, maximum=2, count=3), candidates, knowledge)
    assert result.fallback_reason is None
    assert len(result.selected_action_ids or ()) == 2
    assert len(set(result.selected_action_ids or ())) == 2


def test_only_accepted_search_priors_are_loaded() -> None:
    priors = load_search_priors(PACK)
    assert len(priors) == 66
    assert all(item.rule_id not in {"CR-000001"} for item in priors)


def test_evaluation_weight_recomputes_wld_and_never_uses_source_win_rate() -> None:
    record = next(row for row in _rows(PACK / "evaluation_registry.jsonl") if row["evaluation_id"] == "ER-000009")
    first = evaluation_weight(record)
    assert first is not None
    record["win_rate"] = 0.000001
    second = evaluation_weight(record)
    assert second is not None and second.rate == first.rate == (247 / 500)
    broken = dict(record)
    broken["games"] = 501
    with pytest.raises(CuratedKnowledgeError, match="inconsistent W-L-D"):
        evaluation_weight(broken)
    kaggle = next(row for row in _rows(PACK / "evaluation_registry.jsonl") if row["kaggle_score"] is not None and row["games"] == 0)
    assert evaluation_weight(kaggle) is None


def test_curated_build_is_deterministic_and_remains_offline_only(tmp_path: Path) -> None:
    from mage_ptcg.student.dataset import build_rule_bc_example, write_dataset
    from scripts.c5_distillation import main as c5_main

    def observation(index: int) -> dict[str, object]:
        card = {"id": 100 + index, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
        player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
        current = {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2 + index, "turnActionCount": 3, "yourIndex": 0}
        return {"current": current, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}], "type": 0}, "step": 1}

    source = tmp_path / "rulebc.jsonl"
    write_dataset(source, [build_rule_bc_example(observation(index), deck=[1] * 60, source_id=f"knowledge-{index}", source_revision="fixture") for index in range(3)])
    first, second = tmp_path / "first", tmp_path / "second"
    args = ["build", "--input", str(source), "--synthetic", "--environment-version", "fixture", "--agent-config-hash", "cfg", "--curated-knowledge-dir", str(PACK)]
    assert c5_main(["--output-dir", str(first), *args]) == 0
    assert c5_main(["--output-dir", str(second), *args]) == 0
    assert (first / "datasets" / "canonical-decision.jsonl").read_bytes() == (second / "datasets" / "canonical-decision.jsonl").read_bytes()
    summary = json.loads((first / "build-summary.json").read_text(encoding="utf-8"))
    assert summary["curated_knowledge"]["teacher_registry_only"] is True
    assert summary["curated_knowledge"]["metrics"]["teacher_rules_loaded"] == 22
    assert summary["curated_knowledge"]["metrics"]["teacher_rules_applied"] == 0
