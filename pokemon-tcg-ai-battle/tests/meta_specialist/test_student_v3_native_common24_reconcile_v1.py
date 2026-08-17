from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 import (
    Common24ReconciliationError,
    reconcile_student_v3_native_common24_v1,
    write_student_v3_native_common24_reconciliation_v1,
)


SHA = "a" * 64
CANDIDATE_POLICY_SHA = "b" * 64
CANDIDATE_DECK_SHA = "c" * 64
EVALUATOR_SHA = "f" * 64
CANDIDATE_RUNNER = (
    "scripts.run_student_v3_set_candidate_pilot_v1:"
    "run_student_v3_candidate_game_v1"
)
NATIVE_RUNNER = (
    "scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _aggregate(rows: list[dict[str, object]]) -> dict[str, object]:
    wins = sum(row["outcome"] == "win" for row in rows)
    draws = sum(row["outcome"] == "draw" for row in rows)
    losses = sum(row["outcome"] == "loss" for row in rows)
    faults = sum(row["outcome"] == "fault" for row in rows)
    return {
        "requested_games": len(rows),
        "completed_games": wins + draws + losses,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "faults": faults,
        "fault_rate": faults / len(rows),
        "score_rate": (wins + 0.5 * draws) / len(rows),
        "score_denominator_games": len(rows),
    }


def _seal_block_files(block: dict[str, object], rows: list[dict[str, object]]) -> None:
    ledger = Path(str(block["ledger_path"]))
    manifest = Path(str(block["manifest_path"]))
    summary = Path(str(block["summary_path"]))
    _write_jsonl(ledger, rows)
    stats = _aggregate(rows)
    _write_json(
        manifest,
        {
            "schema_version": "meta-specialist-parallel-cabt-evaluator-v1",
            "evaluator_implementation_sha256": EVALUATOR_SHA,
            "engine_seed_supported": False,
            "pairing": "independent_stratified_not_game_paired",
            "requested_games": len(rows),
            "completed_games": stats["completed_games"],
            "faults": stats["faults"],
            "game_ids": [row["game_id"] for row in rows],
            "block_ids": [block["block_id"]],
        },
    )
    _write_json(summary, {**stats, "evaluator_implementation_sha256": EVALUATOR_SHA})
    block["ledger_sha256"] = _sha(ledger)
    block["manifest_sha256"] = _sha(manifest)
    block["summary_sha256"] = _sha(summary)


def _rows(
    *,
    arm: str,
    comparison_block_id: str,
    block_id: str,
    opponents: list[str],
    repetitions: int,
    base_seed: int,
    win_cutoff: int | None = None,
    native_policy_sha: str | None = None,
    native_deck_sha: str | None = None,
) -> list[dict[str, object]]:
    if arm == "candidate":
        subject_id = "student-v3-tomato"
        policy_sha = CANDIDATE_POLICY_SHA
        deck_sha = CANDIDATE_DECK_SHA
        metadata_schema = "meta-specialist-student-v3-set-candidate-pilot-v1"
    else:
        subject_id = "tomatomato_archaludon"
        assert native_policy_sha is not None and native_deck_sha is not None
        policy_sha = native_policy_sha
        deck_sha = native_deck_sha
        metadata_schema = "meta-specialist-native-policy-candidate-pilot-v1"
    result: list[dict[str, object]] = []
    ordinal = 0
    total = len(opponents) * 2 * repetitions
    cutoff = total // 2 if win_cutoff is None else win_cutoff
    for opponent_id in opponents:
        opponent_identity = {
            "policy_sha256": hashlib.sha256(f"p:{opponent_id}".encode()).hexdigest(),
            "deck_sha256": hashlib.sha256(f"d:{opponent_id}".encode()).hexdigest(),
            "source": "public",
            "usage_boundary": "local_eval_only",
        }
        for seat in (0, 1):
            for repetition in range(repetitions):
                outcome = "win" if ordinal < cutoff else "loss"
                result.append(
                    {
                        "schema_version": "meta-specialist-parallel-cabt-evaluator-v1",
                        "game_id": f"{arm}-{comparison_block_id}-{opponent_id}-s{seat}-r{repetition}",
                        "block_id": block_id,
                        "policy_id": subject_id,
                        "policy_sha256": policy_sha,
                        "deck_id": subject_id,
                        "deck_sha256": deck_sha,
                        "opponent_id": opponent_id,
                        "opponent_identity": opponent_identity,
                        "opponent_deck_sha256": opponent_identity["deck_sha256"],
                        "seat": seat,
                        "seed": base_seed + ordinal,
                        "max_steps": 2_000,
                        "requested": 1,
                        "evaluator_implementation_sha256": EVALUATOR_SHA,
                        "engine_seed_supported": False,
                        "status": "DONE",
                        "raw_status": "DONE",
                        "outcome": outcome,
                        "winner": seat if outcome == "win" else 1 - seat,
                        "metadata": {
                            "schema_version": metadata_schema,
                            "candidate_id": subject_id,
                            "repetition": repetition,
                            **(
                                {
                                    "candidate_artifact_sha256": hashlib.sha256(
                                        b'{"fixture":true}'
                                    ).hexdigest(),
                                    "policy_identity_sha256": policy_sha,
                                }
                                if arm == "candidate"
                                else {
                                    "candidate_policy_sha256": policy_sha,
                                    "candidate_deck_sha256": deck_sha,
                                    "candidate_env": {},
                                    "candidate_biases": {},
                                }
                            ),
                            "training_authority": False,
                            "promotion_authority": False,
                            "submission_authority": False,
                            "longrun_authority": False,
                        },
                    }
                )
                ordinal += 1
    return result


def _make_request(
    tmp_path: Path,
    *,
    target: int = 96,
    block_repetitions: tuple[int, ...] = (2,),
    candidate_win_adjustment: int = 0,
) -> tuple[Path, dict[str, object]]:
    opponents = [f"opponent-{index:02d}" for index in range(24)]
    reference = tmp_path / "reference.json"
    _write_json(
        reference,
        {
            "schema_version": "meta-specialist-performance-first-broad-pool-v1",
            "opponent_ids": opponents,
            "promotion_authority": False,
        },
    )
    candidate_artifact = tmp_path / "candidate-artifact.json"
    _write_json(candidate_artifact, {"fixture": True})
    native_policy = tmp_path / "native-main.py"
    native_policy.write_text("def agent(obs): return 0\n", encoding="utf-8")
    native_deck = tmp_path / "native-deck.csv"
    native_deck.write_text("fixture\n", encoding="utf-8")
    native_policy_sha = _sha(native_policy)
    native_deck_sha = _sha(native_deck)
    blocks: list[dict[str, object]] = []
    for index, repetitions in enumerate(block_repetitions):
        comparison_id = f"block-{index + 1}"
        pair: dict[str, object] = {
            "comparison_block_id": comparison_id,
            "repetitions_per_opponent_seat": repetitions,
        }
        for arm, base in (("candidate", 1_000_000 + index * 10_000), ("native", 2_000_000 + index * 10_000)):
            root = tmp_path / comparison_id / arm
            block: dict[str, object] = {
                "block_id": f"{comparison_id}-{arm}",
                "base_seed": base,
                "timeout_seconds": 600.0,
                "runner_ref": CANDIDATE_RUNNER if arm == "candidate" else NATIVE_RUNNER,
                "ledger_path": str(root / "ledger.jsonl"),
                "ledger_sha256": SHA,
                "manifest_path": str(root / "manifest.json"),
                "manifest_sha256": SHA,
                "summary_path": str(root / "summary.json"),
                "summary_sha256": SHA,
            }
            count = len(opponents) * 2 * repetitions
            cutoff = count // 2 + (candidate_win_adjustment if arm == "candidate" else 0)
            arm_rows = _rows(
                arm=arm,
                comparison_block_id=comparison_id,
                block_id=str(block["block_id"]),
                opponents=opponents,
                repetitions=repetitions,
                base_seed=base,
                win_cutoff=cutoff,
                native_policy_sha=native_policy_sha,
                native_deck_sha=native_deck_sha,
            )
            _seal_block_files(block, arm_rows)
            pair[arm] = block
        blocks.append(pair)
    request: dict[str, object] = {
        "schema_version": "meta-specialist-student-v3-native-common24-reconcile-request-v1",
        "target_games_per_arm": target,
        "reference_config": {"path": str(reference), "sha256": _sha(reference)},
        "protocol": {
            "opponent_ids": opponents,
            "engine_seed_supported": False,
            "pairing": "independent_stratified_not_game_paired",
            "max_steps": 2_000,
            "timeout_seconds": 600.0,
            "evaluator_implementation_sha256": EVALUATOR_SHA,
        },
        "candidate": {
            "candidate_id": "student-v3-tomato",
            "artifact_path": str(candidate_artifact),
            "artifact_sha256": _sha(candidate_artifact),
            "policy_sha256": CANDIDATE_POLICY_SHA,
            "deck_sha256": CANDIDATE_DECK_SHA,
            "runner_ref": CANDIDATE_RUNNER,
        },
        "native": {
            "candidate_id": "tomatomato_archaludon",
            "policy_path": str(native_policy),
            "policy_sha256": native_policy_sha,
            "deck_path": str(native_deck),
            "deck_sha256": native_deck_sha,
            "runner_ref": NATIVE_RUNNER,
        },
        "blocks": blocks,
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "longrun_authority": False,
        },
    }
    request_path = tmp_path / "request.json"
    _write_json(request_path, request)
    return request_path, request


@pytest.fixture(autouse=True)
def _stub_candidate_and_native_file_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    import mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 as module

    monkeypatch.setattr(
        module,
        "_formal_candidate_identity_v1",
        lambda _path: SimpleNamespace(
            candidate_id="student-v3-tomato",
            policy_identity_sha256=CANDIDATE_POLICY_SHA,
            deck_sha256=CANDIDATE_DECK_SHA,
        ),
    )


def _rewrite_request(request_path: Path, request: dict[str, object]) -> None:
    _write_json(request_path, request)


def _reseal_arm(request: dict[str, object], block_index: int, arm: str, rows: list[dict[str, object]]) -> None:
    block = request["blocks"][block_index][arm]  # type: ignore[index]
    _seal_block_files(block, rows)


def _load_rows(request: dict[str, object], block_index: int, arm: str) -> list[dict[str, object]]:
    block = request["blocks"][block_index][arm]  # type: ignore[index]
    return [json.loads(line) for line in Path(block["ledger_path"]).read_text().splitlines()]


@pytest.mark.parametrize(
    ("target", "repetitions", "expected_status", "eligible"),
    [
        (96, (2,), "SCREEN_COMPLETE_CONTINUE", False),
        (384, (8,), "SCREEN_COMPLETE_CONTINUE", False),
        (768, (8, 8), "LONGRUN_REVIEW_READY", True),
        (1536, (16, 16), "FINAL_REVIEW_READY", True),
    ],
)
def test_exact_stage_gate_and_authority_are_mechanical(
    tmp_path: Path,
    target: int,
    repetitions: tuple[int, ...],
    expected_status: str,
    eligible: bool,
) -> None:
    request_path, _ = _make_request(tmp_path, target=target, block_repetitions=repetitions)

    result = reconcile_student_v3_native_common24_v1(request_path)

    assert result["gate"]["status"] == expected_status
    assert result["gate"]["promotion_gate_eligible"] is eligible
    assert result["gate"]["performance_auto_reject"] is False
    assert result["candidate"]["requested_games"] == target
    assert result["native"]["requested_games"] == target
    assert result["candidate"]["wins"] + result["candidate"]["losses"] == target
    assert len(result["blocks"][0]["candidate"]["game_seed_set_sha256"]) == 64
    assert len(result["blocks"][0]["candidate"]["game_id_set_sha256"]) == 64
    assert result["protocol"]["timeout_binding"] == "request_and_arm_declaration_only"
    assert result["protocol"]["ledger_v1_omits_timeout_seconds"] is True
    assert result["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }


def test_small_negative_delta_is_not_auto_rejected_at_longrun_gate(tmp_path: Path) -> None:
    request_path, _ = _make_request(
        tmp_path, target=768, block_repetitions=(8, 8), candidate_win_adjustment=-1
    )

    result = reconcile_student_v3_native_common24_v1(request_path)

    assert result["comparison"]["candidate_minus_native_score_rate"] < 0
    assert result["gate"]["status"] == "LONGRUN_REVIEW_READY"
    assert result["gate"]["performance_auto_reject"] is False
    assert result["gate"]["promotion_gate_eligible"] is True


def test_fault_stays_in_denominator_and_blocks_promotion(tmp_path: Path) -> None:
    request_path, request = _make_request(
        tmp_path, target=768, block_repetitions=(8, 8)
    )
    rows = _load_rows(request, 0, "candidate")
    rows[0].update({"outcome": "fault", "status": "FAULT", "raw_status": None})
    _reseal_arm(request, 0, "candidate", rows)
    _rewrite_request(request_path, request)

    result = reconcile_student_v3_native_common24_v1(request_path)

    assert result["candidate"]["faults"] == 1
    assert result["candidate"]["score_denominator_games"] == 768
    assert result["gate"]["status"] == "BLOCKED_FAULTS"
    assert result["gate"]["promotion_gate_eligible"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_stratum", "missing or extra common24 strata"),
        ("duplicate_game_id", "duplicate game_id"),
        ("seed_mismatch", "seed schedule mismatch"),
        ("max_steps", "max_steps mismatch"),
        ("evaluator", "evaluator closure mismatch"),
        ("winner", "outcome/winner mismatch"),
        ("opponent_identity", "opponent identity mismatch"),
        ("subject_identity", "subject identity mismatch"),
        ("denominator", "summary requested denominator mismatch"),
        ("timeout", "timeout_seconds mismatch"),
    ],
)
def test_protocol_tampering_fails_closed(
    tmp_path: Path, mutation: str, message: str
) -> None:
    request_path, request = _make_request(tmp_path)
    candidate_rows = _load_rows(request, 0, "candidate")
    native_rows = _load_rows(request, 0, "native")
    if mutation == "missing_stratum":
        candidate_rows.pop()
        _reseal_arm(request, 0, "candidate", candidate_rows)
    elif mutation == "duplicate_game_id":
        native_rows[0]["game_id"] = candidate_rows[0]["game_id"]
        _reseal_arm(request, 0, "native", native_rows)
    elif mutation == "seed_mismatch":
        candidate_rows[0]["seed"] += 1
        _reseal_arm(request, 0, "candidate", candidate_rows)
    elif mutation == "max_steps":
        candidate_rows[0]["max_steps"] = 1_999
        _reseal_arm(request, 0, "candidate", candidate_rows)
    elif mutation == "evaluator":
        candidate_rows[0]["evaluator_implementation_sha256"] = SHA
        _reseal_arm(request, 0, "candidate", candidate_rows)
    elif mutation == "winner":
        candidate_rows[0]["winner"] = 1 - int(candidate_rows[0]["seat"])
        _reseal_arm(request, 0, "candidate", candidate_rows)
    elif mutation == "opponent_identity":
        native_rows[0]["opponent_identity"]["policy_sha256"] = SHA  # type: ignore[index]
        _reseal_arm(request, 0, "native", native_rows)
    elif mutation == "subject_identity":
        candidate_rows[0]["policy_sha256"] = SHA
        _reseal_arm(request, 0, "candidate", candidate_rows)
    elif mutation == "denominator":
        block = request["blocks"][0]["candidate"]  # type: ignore[index]
        summary_path = Path(block["summary_path"])
        summary = json.loads(summary_path.read_text())
        summary["requested_games"] -= 1
        _write_json(summary_path, summary)
        block["summary_sha256"] = _sha(summary_path)
    elif mutation == "timeout":
        request["blocks"][0]["candidate"]["timeout_seconds"] = 599.0  # type: ignore[index]
    _rewrite_request(request_path, request)

    with pytest.raises(Common24ReconciliationError, match=message):
        reconcile_student_v3_native_common24_v1(request_path)


def test_longrun_requires_two_independent_blocks(tmp_path: Path) -> None:
    request_path, _ = _make_request(tmp_path, target=768, block_repetitions=(16,))

    with pytest.raises(Common24ReconciliationError, match="at least two independent blocks"):
        reconcile_student_v3_native_common24_v1(request_path)


def test_overlapping_seeds_between_blocks_fail_closed(tmp_path: Path) -> None:
    request_path, request = _make_request(
        tmp_path, target=768, block_repetitions=(8, 8)
    )
    block0 = request["blocks"][0]["candidate"]  # type: ignore[index]
    block1 = request["blocks"][1]["candidate"]  # type: ignore[index]
    block1["base_seed"] = block0["base_seed"]
    rows = _load_rows(request, 1, "candidate")
    delta = int(block0["base_seed"]) - int(rows[0]["seed"])
    for row in rows:
        row["seed"] = int(row["seed"]) + delta
    _reseal_arm(request, 1, "candidate", rows)
    _rewrite_request(request_path, request)

    with pytest.raises(Common24ReconciliationError, match="overlapping game seed"):
        reconcile_student_v3_native_common24_v1(request_path)


def test_writer_uses_canonical_json_without_newline(tmp_path: Path) -> None:
    request_path, _ = _make_request(tmp_path)
    output = tmp_path / "reconciliation.json"

    written = write_student_v3_native_common24_reconciliation_v1(
        request_path, output
    )

    raw = output.read_bytes()
    assert not raw.endswith(b"\n")
    assert raw == json.dumps(
        json.loads(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert written["artifact_sha256"] == _sha(output)


def test_cli_writes_one_reconciliation_artifact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.reconcile_student_v3_native_common24_v1 import main

    request_path, _ = _make_request(tmp_path)
    output = tmp_path / "cli-output.json"

    assert main(["--request", str(request_path), "--output", str(output)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "SCREEN_COMPLETE_CONTINUE"
    assert printed["artifact_sha256"] == _sha(output)
    assert output.is_file()
