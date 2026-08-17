"""Regression contracts for the Student v1 three-way holdout evaluator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.offline_scaleup.pipeline import ContractError, DATASET_SCHEMA, evaluate_holdout, train_student_v1
from mage_ptcg.student.dataset import build_rule_bc_example


def _observation() -> dict[str, object]:
    card = {"id": 1, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False,
            "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False,
              "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False,
              "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2,
            "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1,
            "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def _make_dataset(tmp_path: Path, *, include_deck: bool = True) -> tuple[Path, Path, Path]:
    root = tmp_path / "artifact-root"; dataset = root / "datasets" / "split.jsonl"; dataset.parent.mkdir(parents=True)
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    records: list[dict[str, object]] = []
    splits = ("train", "train", "validation", "test", "opponent_holdout", *( ("deck_holdout",) if include_deck else ()))
    assignments: dict[str, str] = {}
    for index, split in enumerate(splits):
        episode = f"episode-{index}"
        assignments[episode] = split
        records.append({"schema_version": DATASET_SCHEMA, "split": split, "episode_id": episode, "game_id": episode,
                        "candidate_side": index % 2, "opponent_id": "opponent-holdout" if split == "opponent_holdout" else f"opponent-{split}",
                        "opponent_type": "TEAM_NATIVE" if split == "opponent_holdout" else "RULE_V0_DECK",
                        "opponent_deck_fingerprint": "deck-holdout" if split == "deck_holdout" else f"deck-{split}",
                        "teacher_identity": "rule-v0", "teacher_type": "RULE_V0_DECK", "teacher_trust": "TRUSTED",
                        "rule_bc_example": example.to_dict()})
    dataset.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    (root / "artifacts").mkdir()
    (root / "artifacts" / "dataset_split_manifest_v2.json").write_text(json.dumps({"episode_assignment": assignments,
        "opponent_holdout_id": "opponent-holdout", "deck_holdout_fingerprint": "deck-holdout"}), encoding="utf-8")
    model_dir = root / "models" / "student-v1"; train_student_v1(dataset=dataset, model_dir=model_dir, epochs=1, learning_rate=0.1, progress=False)
    return root, dataset, model_dir / "student_v1_model.json"


def test_evaluate_holdout_generates_three_split_metrics_and_integrity_artifacts(tmp_path: Path) -> None:
    root, dataset, model = _make_dataset(tmp_path)
    report = evaluate_holdout(dataset=dataset, model_path=model, output=root / "summaries" / "holdout_evaluation.json",
                              artifact_root=root, progress=False)
    assert report["gate"] == "PASS"
    assert set(report["splits"]) == {"test", "opponent_holdout", "deck_holdout"}
    for split, metrics in report["splits"].items():
        assert metrics["examples"] == 1 and metrics["unique_episodes"] == 1
        assert metrics["legal_action_rate"] == 1.0 and metrics["fallback_rate"] == 0.0
        assert "latency_us_p99" in metrics and metrics["selection_type_examples"] == {"0": 1}
        assert metrics["candidate_side"]
    integrity = json.loads((root / "artifacts" / "student_v1_holdout_integrity.json").read_text())
    assert integrity["split_contamination"] == 0
    assert integrity["opponent_holdout_identity_mismatch"] == 0
    assert integrity["deck_holdout_fingerprint_mismatch"] == 0
    for relative in ("artifacts/student_v1_holdout_metrics.json", "artifacts/student_v1_holdout_comparison.json",
                     "artifacts/student_v1_holdout_verdict.json", "summaries/latest_holdout_summary.json",
                     "docs/student_v1_holdout_evaluation.md"):
        assert (root / relative).exists(), relative


def test_evaluate_holdout_rejects_missing_required_split(tmp_path: Path) -> None:
    root, dataset, model = _make_dataset(tmp_path, include_deck=False)
    with pytest.raises(ContractError, match="deck_holdout"):
        evaluate_holdout(dataset=dataset, model_path=model, output=root / "summaries" / "holdout_evaluation.json",
                         artifact_root=root, progress=False)


def test_evaluate_holdout_marks_identity_or_deck_contamination_invalid(tmp_path: Path) -> None:
    root, dataset, model = _make_dataset(tmp_path)
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    next(row for row in rows if row["split"] == "opponent_holdout")["opponent_id"] = "wrong-opponent"
    next(row for row in rows if row["split"] == "deck_holdout")["opponent_deck_fingerprint"] = "wrong-deck"
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    report = evaluate_holdout(dataset=dataset, model_path=model, output=root / "summaries" / "holdout_evaluation.json",
                              artifact_root=root, progress=False)
    assert report["gate"] == "FAIL" and report["verdict"] == "INVALID_HOLDOUT_EVIDENCE"
    integrity = json.loads((root / "artifacts" / "student_v1_holdout_integrity.json").read_text())
    assert integrity["opponent_holdout_identity_mismatch"] == 1
    assert integrity["deck_holdout_fingerprint_mismatch"] == 1
