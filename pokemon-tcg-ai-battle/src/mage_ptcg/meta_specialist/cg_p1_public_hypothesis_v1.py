"""Strict, public-state-only hypothesis extraction for the cg P1 policy.

The collector records terminal WDL separately from decision telemetry.  This
module joins the two without pretending that an observed action is a
counterfactual label.  It emits at most a few diagnostic candidate specs and
keeps the screen gate closed unless a state bucket has competing actions with
enough support and mixed terminal outcomes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PUBLIC_HYPOTHESIS_SCHEMA = "meta-specialist-cg-p1-public-hypothesis-v1"
TELEMETRY_SCHEMA = "cg-public-telemetry-v1"
ALLOWED_RECORD_TYPES = {"decision", "deck_registration_redacted"}
FORBIDDEN_KEY_PARTS = {
    "private",
    "hidden",
    "raw_observation",
    "raw_state",
    "knowledge",
    "teacher",
    "logprob",
    "hand",
    "prize",
    "deck",
}
ALLOWED_COUNT_KEYS = {"hand_count", "prize_count", "deck_count", "deck_size"}


class PublicHypothesisError(ValueError):
    """Raised when a source record is outside the public telemetry contract."""


@dataclass(frozen=True)
class PublicDecisionV1:
    game_id: str
    outcome: str
    bucket: Mapping[str, object]
    operation: str
    seat: int
    turn: int
    schema_version: str = PUBLIC_HYPOTHESIS_SCHEMA


def _reject_forbidden_keys(value: object, path: str = "row") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            lowered = name.lower()
            if lowered in ALLOWED_COUNT_KEYS:
                _reject_forbidden_keys(child, f"{path}.{name}")
                continue
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise PublicHypothesisError(f"forbidden/private key: {path}.{name}")
            _reject_forbidden_keys(child, f"{path}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def _card_fields(cards: object) -> Mapping[str, object]:
    if not isinstance(cards, list):
        return {}
    for card in cards:
        if isinstance(card, Mapping):
            fields = card.get("fields")
            if isinstance(fields, Mapping):
                return fields
    return {}


def _int_field(fields: Mapping[str, object], key: str, default: int = 0) -> int:
    value = fields.get(key, default)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _bool_field(fields: Mapping[str, object], key: str) -> bool:
    return bool(fields.get(key, False))


def _selected_operation(row: Mapping[str, object]) -> str:
    options = row.get("options")
    action = row.get("action")
    if not isinstance(options, list) or not isinstance(action, list):
        return "UNKNOWN"
    names: list[str] = []
    for raw_index in action:
        if not isinstance(raw_index, int) or not 0 <= raw_index < len(options):
            return "UNKNOWN"
        option = options[raw_index]
        if not isinstance(option, Mapping):
            return "UNKNOWN"
        name = option.get("type_name")
        if not isinstance(name, str) or not name:
            raw_type = option.get("type")
            name = f"TYPE_{raw_type}" if isinstance(raw_type, int) else "UNKNOWN"
        names.append(name)
    return "+".join(names) if names else "NONE"


def bucket_public_state_v1(row: Mapping[str, object]) -> dict[str, object]:
    """Project one telemetry decision to a coarse public state/action bucket."""

    if not isinstance(row, Mapping):
        raise PublicHypothesisError("decision row must be a mapping")
    _reject_forbidden_keys(row)
    self_state = row.get("self") if isinstance(row.get("self"), Mapping) else {}
    opponent_state = row.get("opponent") if isinstance(row.get("opponent"), Mapping) else {}
    self_active = _card_fields(self_state.get("active"))
    opponent_active = _card_fields(opponent_state.get("active"))
    self_status = self_active.get("status") if isinstance(self_active.get("status"), Mapping) else {}
    opponent_status = opponent_active.get("status") if isinstance(opponent_active.get("status"), Mapping) else {}
    board = row.get("board") if isinstance(row.get("board"), Mapping) else {}
    select = row.get("select") if isinstance(row.get("select"), Mapping) else {}
    operation = _selected_operation(row)
    turn = row.get("turn", 0)
    turn_int = int(turn) if isinstance(turn, (int, float)) and not isinstance(turn, bool) else 0
    option_count = select.get("option_count", 0)
    option_count_int = int(option_count) if isinstance(option_count, (int, float)) else 0
    # Only board/status/selection metadata is retained.  Hand/prize/deck
    # counts are deliberately ignored even though the telemetry schema carries
    # redacted count fields.
    return {
        "turn_bucket": max(0, turn_int // 2),
        "self_active_id": _int_field(self_active, "id", -1),
        "self_active_hp_bucket": max(0, _int_field(self_active, "hp", 0) // 50),
        "self_active_max_hp_bucket": max(0, _int_field(self_active, "maxHp", 0) // 50),
        "self_energy_bucket": min(4, max(0, _int_field(self_active, "energies_count", 0))),
        "opponent_active_id": _int_field(opponent_active, "id", -1),
        "opponent_active_hp_bucket": max(0, _int_field(opponent_active, "hp", 0) // 50),
        "opponent_active_max_hp_bucket": max(0, _int_field(opponent_active, "maxHp", 0) // 50),
        "opponent_energy_bucket": min(4, max(0, _int_field(opponent_active, "energies_count", 0))),
        "self_status": tuple(sorted(str(k) for k, v in self_status.items() if bool(v))),
        "opponent_status": tuple(sorted(str(k) for k, v in opponent_status.items() if bool(v))),
        "energy_attached": bool(board.get("energy_attached", False)),
        "supporter_played": bool(board.get("supporter_played", False)),
        "stadium_played": bool(board.get("stadium_played", False)),
        "retreated": bool(board.get("retreated", False)),
        "select_type": str(select.get("type_name", select.get("type", "UNKNOWN"))),
        "option_count": max(0, option_count_int),
        "operation": operation,
    }


def _read_jsonl(path: Path) -> Iterable[dict[str, object]]:
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicHypothesisError(f"invalid JSON: {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise PublicHypothesisError(f"JSON object required: {path}:{line_number}")
        yield value


def load_public_decisions_v1(*, telemetry_root: Path | str, ledger_path: Path | str) -> list[PublicDecisionV1]:
    """Load decision rows and bind each to a terminal WDL ledger row."""

    telemetry = Path(telemetry_root).resolve()
    ledger_file = Path(ledger_path).resolve()
    if not telemetry.is_dir() or not ledger_file.is_file():
        raise PublicHypothesisError("telemetry root and ledger file are required")
    terminal: dict[str, str] = {}
    for raw in _read_jsonl(ledger_file):
        game_id = raw.get("game_id")
        outcome = raw.get("outcome")
        status = raw.get("status")
        if not isinstance(game_id, str) or outcome not in {"win", "loss", "draw"}:
            raise PublicHypothesisError("ledger row lacks terminal game_id/outcome")
        if status != "DONE" or raw.get("fault_kind") not in {None, ""}:
            raise PublicHypothesisError(f"non-terminal/fault ledger row: {game_id}")
        if game_id in terminal and terminal[game_id] != outcome:
            raise PublicHypothesisError(f"conflicting terminal outcome: {game_id}")
        terminal[game_id] = str(outcome)
    result: list[PublicDecisionV1] = []
    for path in sorted(telemetry.glob("*.jsonl")):
        for raw in _read_jsonl(path):
            if raw.get("schema_version") != TELEMETRY_SCHEMA:
                raise PublicHypothesisError(f"telemetry schema mismatch: {path}")
            record_type = raw.get("record_type")
            if record_type not in ALLOWED_RECORD_TYPES:
                raise PublicHypothesisError(f"unknown telemetry record type: {record_type}")
            _reject_forbidden_keys(raw)
            if record_type == "deck_registration_redacted":
                continue
            game_id = raw.get("game_id")
            if not isinstance(game_id, str) or game_id not in terminal:
                raise PublicHypothesisError(f"decision has no terminal WDL: {game_id}")
            bucket = bucket_public_state_v1(raw)
            result.append(
                PublicDecisionV1(
                    game_id=game_id,
                    outcome=terminal[game_id],
                    bucket=bucket,
                    operation=str(bucket["operation"]),
                    seat=int(raw.get("seat", 0)),
                    turn=int(raw.get("turn", 0)),
                )
            )
    return result


def _base_bucket(row: PublicDecisionV1) -> tuple[tuple[str, object], ...]:
    return tuple(sorted((key, value) for key, value in row.bucket.items() if key != "operation"))


def analyze_public_hypotheses_v1(
    rows: Sequence[PublicDecisionV1 | Mapping[str, object]],
    *,
    min_support: int = 8,
    max_candidates: int = 3,
) -> dict[str, object]:
    """Return bounded diagnostic hypotheses; no result is a training label."""

    if min_support < 2 or max_candidates < 1:
        raise ValueError("invalid support/candidate bounds")
    normalized: list[PublicDecisionV1] = []
    for row in rows:
        if isinstance(row, PublicDecisionV1):
            normalized.append(row)
        elif isinstance(row, Mapping):
            outcome = row.get("outcome")
            if outcome not in {"win", "loss", "draw"}:
                raise PublicHypothesisError("analysis row lacks outcome")
            bucket = bucket_public_state_v1(row)
            normalized.append(PublicDecisionV1(str(row.get("game_id", "")), str(outcome), bucket, str(bucket["operation"]), int(row.get("seat", 0)), int(row.get("turn", 0))))
        else:
            raise PublicHypothesisError("analysis rows must be decision objects")
    by_state: dict[tuple[tuple[str, object], ...], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for row in normalized:
        counts = by_state[_base_bucket(row)][row.operation]
        counts[0] += 1
        counts[1] += int(row.outcome == "win")
        counts[2] += int(row.outcome == "loss")
    competing = 0
    mixed_sign = 0
    proposals: list[dict[str, object]] = []
    for base, operations in by_state.items():
        supported = {op: vals for op, vals in operations.items() if vals[0] >= min_support}
        if len(supported) < 2:
            continue
        competing += 1
        has_mixed = any(vals[1] > 0 and vals[2] > 0 for vals in supported.values())
        if has_mixed:
            mixed_sign += 1
        ranked = sorted(supported.items(), key=lambda item: (item[1][1] / item[1][0], -item[1][0]))
        worst_op, worst = ranked[0]
        best_op, best = ranked[-1]
        gap = (best[1] / best[0]) - (worst[1] / worst[0])
        if not has_mixed or gap < 0.15:
            continue
        predicate = dict(base)
        proposals.append({
            "candidate_id": f"cg-p1-public-{worst_op.lower()}-state-priority-v1",
            "hypothesis": f"{worst_op} is underperforming {best_op} within this public state bucket",
            "diagnostic_only": True,
            "operation": worst_op,
            "reference_operation": best_op,
            "delta": 6000,
            "predicate": predicate,
            "support": {"candidate": worst[0], "reference": best[0]},
            "win_rates": {"candidate": worst[1] / worst[0], "reference": best[1] / best[0]},
            "observed_gap": gap,
            "kill_condition": "no paired positive delta at weighted48/common24 or unsupported public state",
            "public_only": True,
        })
    proposals.sort(key=lambda item: (-float(item["observed_gap"]), -int(item["support"]["candidate"])))
    proposals = proposals[:max_candidates]
    reasons: list[str] = []
    if competing == 0:
        reasons.append("few_competing_state_buckets")
    if mixed_sign == 0:
        reasons.append("insufficient_mixed_sign_state_buckets")
    if not proposals:
        reasons.append("no_bounded_public_hypothesis")
    ready = bool(proposals and competing >= 2 and mixed_sign >= 2)
    return {
        "schema_version": PUBLIC_HYPOTHESIS_SCHEMA,
        "examples": len(normalized),
        "state_buckets": len(by_state),
        "competing_state_buckets": competing,
        "mixed_sign_state_buckets": mixed_sign,
        "candidates": proposals,
        "reasons": reasons,
        "ready_for_candidate_screen": ready,
        "diagnostic_only": True,
        "authority": {"training": False, "promotion": False, "submission": False, "teacher": False, "longrun": False},
        "public_only": True,
    }


__all__ = [
    "PUBLIC_HYPOTHESIS_SCHEMA",
    "PublicDecisionV1",
    "PublicHypothesisError",
    "analyze_public_hypotheses_v1",
    "bucket_public_state_v1",
    "load_public_decisions_v1",
]
