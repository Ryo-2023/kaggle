"""Strict common24 reconciliation for Student v3 versus native Tomato.

The evaluator-v1 ledger intentionally does not persist ``timeout_seconds`` or
``runner_ref``.  This consumer therefore requires a closed reconciliation
request that binds those declared launch parameters together with every input
file SHA.  Results are descriptive research evidence only; no field in this
module grants training, promotion, long-run, package, or submission authority.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence


REQUEST_SCHEMA_V1 = (
    "meta-specialist-student-v3-native-common24-reconcile-request-v1"
)
OUTPUT_SCHEMA_V1 = "meta-specialist-student-v3-native-common24-reconciliation-v1"
LEDGER_SCHEMA_V1 = "meta-specialist-parallel-cabt-evaluator-v1"
PAIRING_V1 = "independent_stratified_not_game_paired"
CANDIDATE_RUNNER_REF_V1 = (
    "scripts.run_student_v3_set_candidate_pilot_v1:"
    "run_student_v3_candidate_game_v1"
)
NATIVE_RUNNER_REF_V1 = (
    "scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1"
)
NATIVE_TOMATO_ID_V1 = "tomatomato_archaludon"
GATE_TARGETS_PER_ARM_V1 = (96, 384, 768, 1536)
AUTHORITY_FALSE_V1 = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}

_HEX = frozenset("0123456789abcdef")
_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "target_games_per_arm",
        "reference_config",
        "protocol",
        "candidate",
        "native",
        "blocks",
        "authority",
    }
)
_REFERENCE_KEYS = frozenset({"path", "sha256"})
_PROTOCOL_KEYS = frozenset(
    {
        "opponent_ids",
        "engine_seed_supported",
        "pairing",
        "max_steps",
        "timeout_seconds",
        "evaluator_implementation_sha256",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "candidate_id",
        "artifact_path",
        "artifact_sha256",
        "policy_sha256",
        "deck_sha256",
        "runner_ref",
    }
)
_NATIVE_KEYS = frozenset(
    {
        "candidate_id",
        "policy_path",
        "policy_sha256",
        "deck_path",
        "deck_sha256",
        "runner_ref",
    }
)
_BLOCK_KEYS = frozenset(
    {"comparison_block_id", "repetitions_per_opponent_seat", "candidate", "native"}
)
_ARM_BLOCK_KEYS = frozenset(
    {
        "block_id",
        "base_seed",
        "timeout_seconds",
        "runner_ref",
        "ledger_path",
        "ledger_sha256",
        "manifest_path",
        "manifest_sha256",
        "summary_path",
        "summary_sha256",
    }
)


class Common24ReconciliationError(ValueError):
    """Raised when evidence cannot prove one exact common24 protocol."""


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Common24ReconciliationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                Common24ReconciliationError(f"{label} contains non-finite {value}")
            ),
        )
    except Common24ReconciliationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Common24ReconciliationError(f"could not read {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Common24ReconciliationError(f"{label} must be a JSON object")
    return payload


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Common24ReconciliationError(f"value is not canonical JSON: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Common24ReconciliationError(f"could not hash file: {path}: {exc}") from exc
    return digest.hexdigest()


def _sha(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _HEX for c in value):
        raise Common24ReconciliationError(f"{label} must be a lowercase SHA-256")
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise Common24ReconciliationError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise Common24ReconciliationError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise Common24ReconciliationError(f"{label} must be a non-negative integer")
    return value


def _positive_float(value: object, label: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise Common24ReconciliationError(f"{label} must be a positive finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise Common24ReconciliationError(f"{label} must be a positive finite number")
    return parsed


def _closed(value: object, expected: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise Common24ReconciliationError(f"{label} must be an object")
    keys = set(value)
    if keys != expected:
        raise Common24ReconciliationError(
            f"{label} keys are not closed: missing={sorted(expected - keys)}, "
            f"extra={sorted(keys - expected)}"
        )
    return value


def _resolve_file(value: object, label: str, *, parent: Path) -> Path:
    raw = Path(_text(value, label))
    path = (raw if raw.is_absolute() else parent / raw).resolve()
    if not path.is_file():
        raise Common24ReconciliationError(f"{label} is not a file: {path}")
    return path


def _require_file_sha(path: Path, expected: object, label: str) -> str:
    expected_sha = _sha(expected, f"{label}.sha256")
    actual = _sha256_file(path)
    if actual != expected_sha:
        raise Common24ReconciliationError(
            f"{label} SHA mismatch: expected {expected_sha}, got {actual}"
        )
    return actual


def _formal_candidate_identity_v1(path: Path):
    from scripts.run_student_v3_set_candidate_pilot_v1 import (
        StudentV3CandidatePilotError,
        load_student_v3_candidate_artifact_v1,
    )

    try:
        return load_student_v3_candidate_artifact_v1(path)
    except (StudentV3CandidatePilotError, OSError, ValueError) as exc:
        raise Common24ReconciliationError(
            f"formal Student v3 candidate verification failed: {exc}"
        ) from exc


def _read_jsonl(path: Path, label: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Common24ReconciliationError(f"could not read {label}: {path}: {exc}") from exc
    if not lines:
        raise Common24ReconciliationError(f"{label} must not be empty")
    for index, line in enumerate(lines, start=1):
        if not line:
            raise Common24ReconciliationError(f"{label} has blank row at line {index}")
        try:
            row = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    Common24ReconciliationError(
                        f"{label} line {index} contains non-finite {value}"
                    )
                ),
            )
        except Common24ReconciliationError:
            raise
        except json.JSONDecodeError as exc:
            raise Common24ReconciliationError(
                f"{label} line {index} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise Common24ReconciliationError(f"{label} line {index} is not an object")
        rows.append(row)
    return rows


def _aggregate(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    wins = outcomes["win"]
    draws = outcomes["draw"]
    losses = outcomes["loss"]
    faults = outcomes["fault"]
    requested = len(rows)
    return {
        "requested_games": requested,
        "completed_games": wins + draws + losses,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "faults": faults,
        "fault_rate": faults / requested,
        "score_rate": (wins + 0.5 * draws) / requested,
        "score_denominator_games": requested,
    }


def _require_summary(summary: Mapping[str, object], expected: Mapping[str, object]) -> None:
    if summary.get("requested_games") != expected["requested_games"]:
        raise Common24ReconciliationError("summary requested denominator mismatch")
    for field in (
        "completed_games",
        "wins",
        "draws",
        "losses",
        "faults",
        "score_denominator_games",
        "score_rate",
        "fault_rate",
    ):
        if summary.get(field) != expected[field]:
            raise Common24ReconciliationError(f"summary {field} mismatch")


def _identity_key(value: object) -> bytes:
    if not isinstance(value, Mapping):
        raise Common24ReconciliationError("opponent_identity must be an object")
    policy_sha = _sha(value.get("policy_sha256"), "opponent_identity.policy_sha256")
    deck_sha = _sha(value.get("deck_sha256"), "opponent_identity.deck_sha256")
    if value.get("usage_boundary") not in {"local_eval_only", "training_local_allowed"}:
        raise Common24ReconciliationError("opponent_identity usage_boundary is unsupported")
    return _canonical_bytes({**dict(value), "policy_sha256": policy_sha, "deck_sha256": deck_sha})


def _validate_metadata(
    metadata: object,
    *,
    arm: str,
    subject: Mapping[str, object],
    repetition: int,
) -> None:
    if not isinstance(metadata, Mapping):
        raise Common24ReconciliationError("row metadata must be an object")
    if metadata.get("repetition") != repetition:
        raise Common24ReconciliationError("row repetition metadata mismatch")
    for field in ("training_authority", "promotion_authority", "submission_authority"):
        if metadata.get(field) is not False:
            raise Common24ReconciliationError(f"row metadata {field} must be false")
    if "longrun_authority" in metadata and metadata.get("longrun_authority") is not False:
        raise Common24ReconciliationError("row metadata longrun_authority must be false")
    if metadata.get("candidate_id") != subject["candidate_id"]:
        raise Common24ReconciliationError("subject identity mismatch in row metadata")
    if arm == "candidate":
        if metadata.get("schema_version") != "meta-specialist-student-v3-set-candidate-pilot-v1":
            raise Common24ReconciliationError("candidate metadata schema mismatch")
        expected = {
            "candidate_artifact_sha256": subject["artifact_sha256"],
            "policy_identity_sha256": subject["policy_sha256"],
        }
    else:
        if metadata.get("schema_version") != "meta-specialist-native-policy-candidate-pilot-v1":
            raise Common24ReconciliationError("native metadata schema mismatch")
        expected = {
            "candidate_policy_sha256": subject["policy_sha256"],
            "candidate_deck_sha256": subject["deck_sha256"],
            "candidate_env": {},
            "candidate_biases": {},
        }
    for field, value in expected.items():
        if metadata.get(field) != value:
            raise Common24ReconciliationError(
                f"subject identity mismatch in row metadata field {field}"
            )


def _validate_row(
    row: Mapping[str, object],
    *,
    arm: str,
    subject: Mapping[str, object],
    block_id: str,
    opponent_id: str,
    opponent_identity: bytes | None,
    seat: int,
    repetition: int,
    seed: int,
    max_steps: int,
    evaluator_sha: str,
) -> bytes:
    if row.get("schema_version") != LEDGER_SCHEMA_V1:
        raise Common24ReconciliationError("unsupported evaluator ledger row schema")
    if row.get("block_id") != block_id:
        raise Common24ReconciliationError("row block_id mismatch")
    if row.get("policy_sha256") != subject["policy_sha256"] or row.get(
        "deck_sha256"
    ) != subject["deck_sha256"]:
        raise Common24ReconciliationError("subject identity mismatch")
    if row.get("opponent_id") != opponent_id or row.get("seat") != seat:
        raise Common24ReconciliationError("common24 stratum identity mismatch")
    if row.get("seed") != seed:
        raise Common24ReconciliationError("seed schedule mismatch")
    if row.get("max_steps") != max_steps:
        raise Common24ReconciliationError("max_steps mismatch")
    if row.get("evaluator_implementation_sha256") != evaluator_sha:
        raise Common24ReconciliationError("evaluator closure mismatch")
    if row.get("engine_seed_supported") is not False:
        raise Common24ReconciliationError("engine seed support contract mismatch")
    if row.get("requested") != 1:
        raise Common24ReconciliationError("each ledger row must contribute exactly one requested game")
    identity = _identity_key(row.get("opponent_identity"))
    decoded_identity = json.loads(identity)
    if row.get("opponent_deck_sha256") != decoded_identity["deck_sha256"]:
        raise Common24ReconciliationError("opponent deck identity mismatch")
    if opponent_identity is not None and identity != opponent_identity:
        raise Common24ReconciliationError("opponent identity mismatch")
    outcome = row.get("outcome")
    status = row.get("status")
    if outcome not in {"win", "draw", "loss", "fault"}:
        raise Common24ReconciliationError("unsupported ledger outcome")
    if outcome == "fault":
        if status != "FAULT":
            raise Common24ReconciliationError("fault outcome must have FAULT status")
    elif status != "DONE" or row.get("raw_status") != "DONE":
        raise Common24ReconciliationError("non-fault outcome must have DONE status")
    if outcome == "win" and row.get("winner") != seat:
        raise Common24ReconciliationError("outcome/winner mismatch")
    if outcome == "loss" and row.get("winner") != 1 - seat:
        raise Common24ReconciliationError("outcome/winner mismatch")
    if outcome == "draw" and row.get("winner") != 2:
        raise Common24ReconciliationError("outcome/winner mismatch")
    _validate_metadata(
        row.get("metadata"), arm=arm, subject=subject, repetition=repetition
    )
    return identity


def _load_arm_block(
    raw: object,
    *,
    arm: str,
    subject: Mapping[str, object],
    comparison_block_id: str,
    repetitions: int,
    opponents: Sequence[str],
    protocol: Mapping[str, object],
    parent: Path,
) -> dict[str, object]:
    block = _closed(raw, _ARM_BLOCK_KEYS, f"{comparison_block_id}.{arm}")
    block_id = _text(block["block_id"], f"{comparison_block_id}.{arm}.block_id")
    base_seed = _nonnegative_int(
        block["base_seed"], f"{comparison_block_id}.{arm}.base_seed"
    )
    timeout = _positive_float(
        block["timeout_seconds"], f"{comparison_block_id}.{arm}.timeout_seconds"
    )
    if timeout != protocol["timeout_seconds"]:
        raise Common24ReconciliationError("timeout_seconds mismatch")
    runner_ref = _text(block["runner_ref"], f"{comparison_block_id}.{arm}.runner_ref")
    if runner_ref != subject["runner_ref"]:
        raise Common24ReconciliationError("runner_ref evaluator closure mismatch")
    ledger_path = _resolve_file(
        block["ledger_path"], f"{comparison_block_id}.{arm}.ledger_path", parent=parent
    )
    manifest_path = _resolve_file(
        block["manifest_path"], f"{comparison_block_id}.{arm}.manifest_path", parent=parent
    )
    summary_path = _resolve_file(
        block["summary_path"], f"{comparison_block_id}.{arm}.summary_path", parent=parent
    )
    ledger_sha = _require_file_sha(
        ledger_path, block["ledger_sha256"], f"{comparison_block_id}.{arm}.ledger"
    )
    manifest_sha = _require_file_sha(
        manifest_path, block["manifest_sha256"], f"{comparison_block_id}.{arm}.manifest"
    )
    summary_sha = _require_file_sha(
        summary_path, block["summary_sha256"], f"{comparison_block_id}.{arm}.summary"
    )
    rows = _read_jsonl(ledger_path, f"{comparison_block_id}.{arm}.ledger")
    manifest = _load_json(manifest_path, f"{comparison_block_id}.{arm}.manifest")
    summary = _load_json(summary_path, f"{comparison_block_id}.{arm}.summary")
    expected_count = len(opponents) * 2 * repetitions
    if len(rows) != expected_count:
        raise Common24ReconciliationError(
            f"missing or extra common24 strata in {comparison_block_id}.{arm}: "
            f"expected {expected_count}, got {len(rows)}"
        )
    ids = [row.get("game_id") for row in rows]
    if any(type(game_id) is not str or not game_id for game_id in ids):
        raise Common24ReconciliationError("ledger game_id must be a non-empty string")
    if len(ids) != len(set(ids)):
        raise Common24ReconciliationError("duplicate game_id within ledger")
    if manifest.get("schema_version") != LEDGER_SCHEMA_V1:
        raise Common24ReconciliationError("evaluator manifest schema mismatch")
    if manifest.get("evaluator_implementation_sha256") != protocol[
        "evaluator_implementation_sha256"
    ]:
        raise Common24ReconciliationError("evaluator closure mismatch in manifest")
    if manifest.get("engine_seed_supported") is not False or manifest.get("pairing") != PAIRING_V1:
        raise Common24ReconciliationError("evaluator pairing closure mismatch")
    if manifest.get("requested_games") != len(rows):
        raise Common24ReconciliationError("manifest requested denominator mismatch")
    if manifest.get("game_ids") != ids:
        raise Common24ReconciliationError("manifest game_ids do not exactly match ledger order")
    if manifest.get("block_ids") != [block_id]:
        raise Common24ReconciliationError("manifest block_ids mismatch")
    expected_strata = [
        (opponent_id, seat, repetition)
        for opponent_id in opponents
        for seat in (0, 1)
        for repetition in range(repetitions)
    ]
    actual_strata: list[tuple[str, int, int]] = []
    opponent_closure: dict[tuple[str, int, int], bytes] = {}
    opponent_identity_by_id: dict[str, bytes] = {}
    seeds: list[int] = []
    for ordinal, (row, stratum) in enumerate(zip(rows, expected_strata, strict=True)):
        opponent_id, seat, repetition = stratum
        metadata = row.get("metadata")
        actual_repetition = metadata.get("repetition") if isinstance(metadata, Mapping) else None
        actual_strata.append((str(row.get("opponent_id")), row.get("seat"), actual_repetition))  # type: ignore[arg-type]
        identity = _validate_row(
            row,
            arm=arm,
            subject=subject,
            block_id=block_id,
            opponent_id=opponent_id,
            opponent_identity=opponent_identity_by_id.get(opponent_id),
            seat=seat,
            repetition=repetition,
            seed=base_seed + ordinal,
            max_steps=int(protocol["max_steps"]),
            evaluator_sha=str(protocol["evaluator_implementation_sha256"]),
        )
        opponent_identity_by_id.setdefault(opponent_id, identity)
        opponent_closure[stratum] = identity
        seeds.append(base_seed + ordinal)
    if actual_strata != expected_strata:
        raise Common24ReconciliationError("missing or extra common24 strata")
    aggregate = _aggregate(rows)
    if manifest.get("completed_games") != aggregate["completed_games"] or manifest.get(
        "faults"
    ) != aggregate["faults"]:
        raise Common24ReconciliationError("manifest W/D/L/fault aggregate mismatch")
    if summary.get("evaluator_implementation_sha256") != protocol[
        "evaluator_implementation_sha256"
    ]:
        raise Common24ReconciliationError("evaluator closure mismatch in summary")
    _require_summary(summary, aggregate)
    return {
        "comparison_block_id": comparison_block_id,
        "arm": arm,
        "block_id": block_id,
        "base_seed": base_seed,
        "game_seed_min": min(seeds),
        "game_seed_max": max(seeds),
        "game_seeds": frozenset(seeds),
        "game_seed_set_sha256": hashlib.sha256(
            b"meta-specialist-common24-game-seed-set-v1\0"
            + _canonical_bytes(sorted(seeds))
        ).hexdigest(),
        "game_ids": tuple(ids),
        "game_id_set_sha256": hashlib.sha256(
            b"meta-specialist-common24-game-id-set-v1\0"
            + _canonical_bytes(sorted(ids))
        ).hexdigest(),
        "opponent_closure": opponent_closure,
        "rows": rows,
        "aggregate": aggregate,
        "files": {
            "ledger": {"path": str(ledger_path), "sha256": ledger_sha},
            "manifest": {"path": str(manifest_path), "sha256": manifest_sha},
            "summary": {"path": str(summary_path), "sha256": summary_sha},
        },
        "runner_ref": runner_ref,
        "timeout_seconds": timeout,
    }


def _arm_summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    aggregate = _aggregate(rows)
    by_seat = {
        str(seat): _aggregate([row for row in rows if row.get("seat") == seat])
        for seat in (0, 1)
    }
    seat0 = float(by_seat["0"]["score_rate"])
    seat1 = float(by_seat["1"]["score_rate"])
    return {
        **aggregate,
        "by_seat": by_seat,
        "seat_gap_score_rate": seat0 - seat1,
        "absolute_seat_gap_score_rate": abs(seat0 - seat1),
    }


def _subject_candidate(raw: object, *, parent: Path) -> dict[str, object]:
    subject = _closed(raw, _CANDIDATE_KEYS, "candidate")
    candidate_id = _text(subject["candidate_id"], "candidate.candidate_id")
    artifact_path = _resolve_file(subject["artifact_path"], "candidate.artifact_path", parent=parent)
    artifact_sha = _require_file_sha(
        artifact_path, subject["artifact_sha256"], "candidate.artifact"
    )
    policy_sha = _sha(subject["policy_sha256"], "candidate.policy_sha256")
    deck_sha = _sha(subject["deck_sha256"], "candidate.deck_sha256")
    runner_ref = _text(subject["runner_ref"], "candidate.runner_ref")
    if runner_ref != CANDIDATE_RUNNER_REF_V1:
        raise Common24ReconciliationError("candidate runner_ref evaluator closure mismatch")
    formal = _formal_candidate_identity_v1(artifact_path)
    if (
        formal.candidate_id != candidate_id
        or formal.policy_identity_sha256 != policy_sha
        or formal.deck_sha256 != deck_sha
    ):
        raise Common24ReconciliationError("formal candidate artifact subject identity mismatch")
    return {
        "candidate_id": candidate_id,
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha,
        "policy_sha256": policy_sha,
        "deck_sha256": deck_sha,
        "runner_ref": runner_ref,
    }


def _subject_native(raw: object, *, parent: Path) -> dict[str, object]:
    subject = _closed(raw, _NATIVE_KEYS, "native")
    candidate_id = _text(subject["candidate_id"], "native.candidate_id")
    if candidate_id != NATIVE_TOMATO_ID_V1:
        raise Common24ReconciliationError("native subject must be tomatomato_archaludon")
    policy_path = _resolve_file(subject["policy_path"], "native.policy_path", parent=parent)
    deck_path = _resolve_file(subject["deck_path"], "native.deck_path", parent=parent)
    policy_sha = _require_file_sha(policy_path, subject["policy_sha256"], "native.policy")
    deck_sha = _require_file_sha(deck_path, subject["deck_sha256"], "native.deck")
    runner_ref = _text(subject["runner_ref"], "native.runner_ref")
    if runner_ref != NATIVE_RUNNER_REF_V1:
        raise Common24ReconciliationError("native runner_ref evaluator closure mismatch")
    return {
        "candidate_id": candidate_id,
        "policy_path": str(policy_path),
        "policy_sha256": policy_sha,
        "deck_path": str(deck_path),
        "deck_sha256": deck_sha,
        "runner_ref": runner_ref,
    }


def reconcile_student_v3_native_common24_v1(
    request_path: str | Path,
) -> dict[str, object]:
    """Verify all files and return one authority-free reconciliation artifact."""

    path = Path(request_path).resolve()
    request = _closed(_load_json(path, "reconciliation request"), _REQUEST_KEYS, "request")
    if request["schema_version"] != REQUEST_SCHEMA_V1:
        raise Common24ReconciliationError("unsupported reconciliation request schema")
    if request["authority"] != AUTHORITY_FALSE_V1:
        raise Common24ReconciliationError("request authority must remain exactly false")
    target = _positive_int(request["target_games_per_arm"], "target_games_per_arm")
    if target not in GATE_TARGETS_PER_ARM_V1:
        raise Common24ReconciliationError(
            f"target_games_per_arm must be one of {GATE_TARGETS_PER_ARM_V1}"
        )
    reference_raw = _closed(request["reference_config"], _REFERENCE_KEYS, "reference_config")
    reference_path = _resolve_file(reference_raw["path"], "reference_config.path", parent=path.parent)
    reference_sha = _require_file_sha(reference_path, reference_raw["sha256"], "reference_config")
    reference = _load_json(reference_path, "reference config")
    reference_opponents = reference.get("opponent_ids")
    if (
        not isinstance(reference_opponents, list)
        or len(reference_opponents) != 24
        or len(set(reference_opponents)) != 24
        or any(type(value) is not str or not value for value in reference_opponents)
        or reference.get("promotion_authority") is not False
    ):
        raise Common24ReconciliationError(
            "reference config must contain exactly 24 unique IDs and no promotion authority"
        )
    protocol = _closed(request["protocol"], _PROTOCOL_KEYS, "protocol")
    if protocol["opponent_ids"] != reference_opponents:
        raise Common24ReconciliationError("protocol opponent IDs mismatch reference config")
    if protocol["engine_seed_supported"] is not False or protocol["pairing"] != PAIRING_V1:
        raise Common24ReconciliationError("protocol must be independent stratified common24")
    max_steps = _positive_int(protocol["max_steps"], "protocol.max_steps")
    timeout = _positive_float(protocol["timeout_seconds"], "protocol.timeout_seconds")
    evaluator_sha = _sha(
        protocol["evaluator_implementation_sha256"],
        "protocol.evaluator_implementation_sha256",
    )
    normalized_protocol = {
        **protocol,
        "max_steps": max_steps,
        "timeout_seconds": timeout,
        "evaluator_implementation_sha256": evaluator_sha,
    }
    candidate = _subject_candidate(request["candidate"], parent=path.parent)
    native = _subject_native(request["native"], parent=path.parent)
    raw_blocks = request["blocks"]
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise Common24ReconciliationError("blocks must be a non-empty array")
    if target >= 768 and len(raw_blocks) < 2:
        raise Common24ReconciliationError(
            "768/1536 long-run gates require at least two independent blocks"
        )
    block_ids: set[str] = set()
    arm_blocks: dict[str, list[dict[str, object]]] = {"candidate": [], "native": []}
    total_expected = 0
    for index, raw in enumerate(raw_blocks):
        block = _closed(raw, _BLOCK_KEYS, f"blocks[{index}]")
        comparison_block_id = _text(
            block["comparison_block_id"], f"blocks[{index}].comparison_block_id"
        )
        if comparison_block_id in block_ids:
            raise Common24ReconciliationError("duplicate comparison_block_id")
        block_ids.add(comparison_block_id)
        repetitions = _positive_int(
            block["repetitions_per_opponent_seat"],
            f"blocks[{index}].repetitions_per_opponent_seat",
        )
        total_expected += 24 * 2 * repetitions
        candidate_block = _load_arm_block(
            block["candidate"],
            arm="candidate",
            subject=candidate,
            comparison_block_id=comparison_block_id,
            repetitions=repetitions,
            opponents=reference_opponents,
            protocol=normalized_protocol,
            parent=path.parent,
        )
        native_block = _load_arm_block(
            block["native"],
            arm="native",
            subject=native,
            comparison_block_id=comparison_block_id,
            repetitions=repetitions,
            opponents=reference_opponents,
            protocol=normalized_protocol,
            parent=path.parent,
        )
        if candidate_block["opponent_closure"] != native_block["opponent_closure"]:
            raise Common24ReconciliationError(
                f"opponent identity mismatch in {comparison_block_id}"
            )
        arm_blocks["candidate"].append(candidate_block)
        arm_blocks["native"].append(native_block)
    if total_expected != target:
        raise Common24ReconciliationError(
            f"requested denominator mismatch: blocks provide {total_expected} games per arm, target is {target}"
        )
    all_game_ids: set[str] = set()
    stable_opponents: dict[tuple[str, int, int], bytes] | None = None
    for arm in ("candidate", "native"):
        used_seeds: set[int] = set()
        for block in arm_blocks[arm]:
            ids = set(block["game_ids"])
            overlap_ids = all_game_ids & ids
            if overlap_ids:
                raise Common24ReconciliationError(
                    f"duplicate game_id across ledgers: {sorted(overlap_ids)[0]}"
                )
            all_game_ids.update(ids)
            seeds = set(block["game_seeds"])
            if used_seeds & seeds:
                raise Common24ReconciliationError(
                    f"overlapping game seed sets between {arm} blocks"
                )
            used_seeds.update(seeds)
            current = block["opponent_closure"]
            # Repetition cardinality may differ by block, so compare each
            # opponent's identity after dropping seat/repetition dimensions.
            collapsed = {(key[0], 0, 0): value for key, value in current.items()}
            if stable_opponents is None:
                stable_opponents = collapsed
            elif stable_opponents != collapsed:
                raise Common24ReconciliationError("opponent identity mismatch across blocks")
    candidate_rows = [
        row for block in arm_blocks["candidate"] for row in block["rows"]
    ]
    native_rows = [row for block in arm_blocks["native"] for row in block["rows"]]
    if len(candidate_rows) != target or len(native_rows) != target:
        raise Common24ReconciliationError("requested denominator mismatch after ledger load")
    candidate_summary = _arm_summary(candidate_rows)
    native_summary = _arm_summary(native_rows)
    total_faults = int(candidate_summary["faults"]) + int(native_summary["faults"])
    if total_faults:
        gate_status = "BLOCKED_FAULTS"
        promotion_gate_eligible = False
    elif target <= 384:
        gate_status = "SCREEN_COMPLETE_CONTINUE"
        promotion_gate_eligible = False
    elif target == 768:
        gate_status = "LONGRUN_REVIEW_READY"
        promotion_gate_eligible = True
    else:
        gate_status = "FINAL_REVIEW_READY"
        promotion_gate_eligible = True
    block_receipts = []
    for candidate_block, native_block in zip(
        arm_blocks["candidate"], arm_blocks["native"], strict=True
    ):
        block_receipts.append(
            {
                "comparison_block_id": candidate_block["comparison_block_id"],
                "candidate": {
                    "block_id": candidate_block["block_id"],
                    "base_seed": candidate_block["base_seed"],
                    "game_seed_min": candidate_block["game_seed_min"],
                    "game_seed_max": candidate_block["game_seed_max"],
                    "game_seed_set_sha256": candidate_block[
                        "game_seed_set_sha256"
                    ],
                    "game_id_set_sha256": candidate_block["game_id_set_sha256"],
                    "requested_games": candidate_block["aggregate"]["requested_games"],
                    "files": candidate_block["files"],
                },
                "native": {
                    "block_id": native_block["block_id"],
                    "base_seed": native_block["base_seed"],
                    "game_seed_min": native_block["game_seed_min"],
                    "game_seed_max": native_block["game_seed_max"],
                    "game_seed_set_sha256": native_block["game_seed_set_sha256"],
                    "game_id_set_sha256": native_block["game_id_set_sha256"],
                    "requested_games": native_block["aggregate"]["requested_games"],
                    "files": native_block["files"],
                },
            }
        )
    result: dict[str, object] = {
        "schema_version": OUTPUT_SCHEMA_V1,
        "request": {"path": str(path), "sha256": _sha256_file(path)},
        "reference_config": {"path": str(reference_path), "sha256": reference_sha},
        "protocol": {
            **normalized_protocol,
            "opponent_count": 24,
            "gate_targets_per_arm": list(GATE_TARGETS_PER_ARM_V1),
            "faults_in_requested_denominator": True,
            "timeout_binding": "request_and_arm_declaration_only",
            "ledger_v1_omits_timeout_seconds": True,
        },
        "target_games_per_arm": target,
        "candidate_identity": candidate,
        "native_identity": native,
        "blocks": block_receipts,
        "candidate": candidate_summary,
        "native": native_summary,
        "comparison": {
            "candidate_minus_native_score_rate": (
                float(candidate_summary["score_rate"])
                - float(native_summary["score_rate"])
            ),
            "candidate_minus_native_wins": (
                int(candidate_summary["wins"]) - int(native_summary["wins"])
            ),
            "candidate_minus_native_seat0_score_rate": (
                float(candidate_summary["by_seat"]["0"]["score_rate"])  # type: ignore[index]
                - float(native_summary["by_seat"]["0"]["score_rate"])  # type: ignore[index]
            ),
            "candidate_minus_native_seat1_score_rate": (
                float(candidate_summary["by_seat"]["1"]["score_rate"])  # type: ignore[index]
                - float(native_summary["by_seat"]["1"]["score_rate"])  # type: ignore[index]
            ),
            "descriptive_only": True,
        },
        "gate": {
            "status": gate_status,
            "stage_kind": "screen" if target <= 384 else "longrun",
            "promotion_gate_eligible": promotion_gate_eligible,
            "performance_auto_reject": False,
            "small_or_negative_delta_auto_reject": False,
            "fault_free_required": True,
            "minimum_independent_blocks": 1 if target <= 384 else 2,
            "observed_independent_blocks": len(raw_blocks),
            "human_performance_decision_required": True,
        },
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    result["reconciliation_sha256"] = hashlib.sha256(
        OUTPUT_SCHEMA_V1.encode("ascii") + b"\0" + _canonical_bytes(result)
    ).hexdigest()
    return result


def write_student_v3_native_common24_reconciliation_v1(
    request_path: str | Path,
    output_path: str | Path,
) -> dict[str, object]:
    """Verify inputs and atomically write canonical JSON without a newline."""

    payload = reconcile_student_v3_native_common24_v1(request_path)
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_bytes(payload)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return {
        **payload,
        "artifact_path": str(output),
        "artifact_sha256": _sha256_file(output),
    }


__all__ = [
    "AUTHORITY_FALSE_V1",
    "CANDIDATE_RUNNER_REF_V1",
    "Common24ReconciliationError",
    "GATE_TARGETS_PER_ARM_V1",
    "NATIVE_RUNNER_REF_V1",
    "OUTPUT_SCHEMA_V1",
    "REQUEST_SCHEMA_V1",
    "reconcile_student_v3_native_common24_v1",
    "write_student_v3_native_common24_reconciliation_v1",
]
