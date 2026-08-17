from __future__ import annotations

import json

from mage_ptcg.meta_specialist.v4_promotion_gate import evaluate_v4_promotion_gate
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1


IDS = ["op-a", "op-b", "op-c", "op-d", "op-e", "op-f"]
DECK_SHA = "d" * 64
EVAL_SHA = "e" * 64


def _heldout(*, wins: list[int], base_seed: int = 1234, faults: int = 0, deck_sha: str = DECK_SHA) -> dict:
    per_opponent = {}
    for opponent_id, value in zip(IDS, wins, strict=True):
        per_opponent[opponent_id] = {"w": value, "d": 0, "l": 16 - value, "f": 0, "requested": 16}
    total_wins = sum(wins)
    seat = {
        "0": {"w": total_wins // 2, "d": 0, "l": 48 - total_wins // 2, "f": 0, "requested": 48},
        "1": {"w": total_wins - total_wins // 2, "d": 0, "l": 48 - (total_wins - total_wins // 2), "f": 0, "requested": 48},
    }
    fingerprints = [{"opponent_id": value, "deck_file_sha256": f"{index + 1:064x}", "policy_hash": f"{index + 11:064x}", "canonical_deck_hash": f"{index + 21:064x}"} for index, value in enumerate(IDS)]
    return {
        "schema_version": "meta-specialist-v4-heldout-checkpoint-strength-v1",
        "checkpoint": {
            "path": "/fixture/checkpoint.pt",
            "file_sha256": "c" * 64,
            "tensor_state_sha256": "d" * 64,
        },
        "comparison_status": "valid",
        "base_seed": base_seed,
        "subject_archetype_id": "archaludon",
        "subject_deck_file_sha256": deck_sha,
        "fixed_held_out_opponent_ids": IDS,
        "opponent_ids": IDS,
        "opponent_fingerprints": fingerprints,
        "games_per_seat": 8,
        "max_steps": 2000,
        "evaluation_implementation_sha256": EVAL_SHA,
        "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "requested_games": 96,
        "games_played": 96,
        "faults": faults,
        "wins": total_wins,
        "draws": 0,
        "losses": 96 - total_wins,
        "per_opponent": per_opponent,
        "seat": seat,
    }


def _imitation(*, top1: float = 0.74, root: float = 0.74, stop: float = 0.86) -> dict:
    action_type = {str(action_type): {"top1": 0.75, "eligible_rows": 100} for action_type in (3, 7, 8, 9, 12, 13, 14)}
    action_type["STOP"] = {"top1": stop, "eligible_rows": 100}
    validation = {
        "schema": "meta-specialist-v4-imitation-metrics-v1",
        "partition": "validation", "recurrence": "carry",
        "complete_action": {"top1": top1, "eligible_rows": 1000, "forced_domain_size1_rows": 100},
        "root": {"top1": root}, "action_type": action_type,
    }
    return {
        "schema": "meta-specialist-v4-imitation-metrics-v1",
        "lane": "archaludon",
        "selection_manifest_file_sha256": "f" * 64,
        "seed_results": {
            "0": {"partitions": {"validation": {"recurrence": {"carry": validation}}}},
            "1": {"partitions": {"validation": {"recurrence": {"carry": validation}}}},
        },
    }


def _write_pair(tmp_path, candidate, baseline, imitation=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, value in (("candidate-0.json", candidate[0]), ("candidate-1.json", candidate[1]), ("baseline-0.json", baseline[0]), ("baseline-1.json", baseline[1])):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    imitation_path = None
    if imitation is not None:
        for seed in (0, 1):
            imitation["seed_results"][str(seed)]["checkpoint"] = candidate[seed]["checkpoint"]
        imitation_path = tmp_path / "imitation.json"
        imitation_path.write_text(json.dumps(imitation), encoding="utf-8")
    return paths[:2], paths[2:], imitation_path


def test_v4_promotion_gate_accepts_reproducible_improvement(tmp_path) -> None:
    candidate, baseline, imitation = _write_pair(
        tmp_path,
        [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10], base_seed=1234)],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7], base_seed=1234)],
        _imitation(),
    )
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "PROMOTION_READY"
    assert result["checks"]["overall"]["mean_delta"] > 0.05
    assert result["checks"]["matchup"]["nonnegative_count"] >= 4


def test_v4_promotion_gate_rejects_identity_drift_and_fault(tmp_path) -> None:
    candidate, baseline, imitation = _write_pair(
        tmp_path,
        [_heldout(wins=[10, 9, 10, 10, 9, 10], faults=1), _heldout(wins=[10, 9, 10, 10, 9, 10])],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7])],
        _imitation(),
    )
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "NO_GO"
    assert any(reason.startswith("candidate_0_invalid") for reason in result["reasons"])

    candidate, baseline, imitation = _write_pair(
        tmp_path / "identity",
        [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10])],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7], deck_sha="x" * 64)],
        _imitation(),
    )
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "NO_GO"
    assert any(reason.startswith("identity_mismatch") for reason in result["reasons"])


def test_v4_promotion_gate_rejects_action_type_collapse(tmp_path) -> None:
    candidate, baseline, imitation = _write_pair(
        tmp_path,
        [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10])],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7])],
        _imitation(top1=0.62, root=0.65, stop=0.86),
    )
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "NO_GO"
    assert "action_complete_seed_threshold" in result["reasons"]
    assert "action_root_mean_threshold" in result["reasons"]


def test_v4_promotion_gate_rejects_focus_action_regression_before_collapse(tmp_path) -> None:
    candidate, baseline, imitation = _write_pair(
        tmp_path,
        [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10])],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7])],
        _imitation(),
    )
    payload = json.loads(imitation.read_text(encoding="utf-8"))
    for seed in ("0", "1"):
        payload["seed_results"][seed]["partitions"]["validation"]["recurrence"]["carry"]["action_type"]["14"]["top1"] = 0.49
    imitation.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "NO_GO"
    assert "action_type_14_focus_threshold" in result["reasons"]


def test_v4_promotion_gate_rejects_nested_imitation_schema_drift(tmp_path) -> None:
    candidate, baseline, imitation = _write_pair(
        tmp_path,
        [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10])],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7])],
        _imitation(),
    )
    payload = json.loads(imitation.read_text(encoding="utf-8"))
    payload["seed_results"]["0"]["partitions"]["validation"]["recurrence"]["carry"]["schema"] = "tampered"
    imitation.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "NO_GO"
    assert any(reason.startswith("imitation_metric_invalid_seed_0") for reason in result["reasons"])


def test_v4_promotion_gate_rejects_imitation_checkpoint_binding_drift(tmp_path) -> None:
    candidate, baseline, imitation = _write_pair(
        tmp_path,
        [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10])],
        [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7])],
        _imitation(),
    )
    payload = json.loads(imitation.read_text(encoding="utf-8"))
    payload["seed_results"]["0"]["checkpoint"]["file_sha256"] = "0" * 64
    imitation.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "NO_GO"
    assert any(reason.startswith("imitation_metric_invalid_seed_0") for reason in result["reasons"])


def test_v4_promotion_gate_keeps_legacy_v2_baseline_compatible(tmp_path) -> None:
    candidate_rows = [_heldout(wins=[10, 9, 10, 10, 9, 10]), _heldout(wins=[10, 9, 10, 10, 9, 10])]
    baseline_rows = [_heldout(wins=[8, 7, 8, 8, 7, 7]), _heldout(wins=[8, 7, 8, 8, 7, 7])]
    for row in baseline_rows:
        row["schema_version"] = "meta-specialist-v2-fixed-heldout-checkpoint-strength-v1"
        row["checkpoint"].pop("tensor_state_sha256")
    candidate, baseline, imitation = _write_pair(tmp_path, candidate_rows, baseline_rows, _imitation())
    result = evaluate_v4_promotion_gate(candidate, baseline, imitation_path=imitation)
    assert result["decision"] == "PROMOTION_READY"
