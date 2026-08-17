#!/usr/bin/env python3
"""Research-only policy-drift audit for sealed actor-visible V4 replay.

The audit loads closed V4 checkpoints, evaluates them on the same sealed
``RecurrentBCSequenceV4`` subset, and reports policy/hidden-state drift against
one another.  It never starts CABT games, training, long-run evaluation, or
submission.  Opponent and seat identifiers are deliberately rejected from
row-level metric inputs: they are not model features and are not permitted to
silently become runtime conditioning.

Example (bounded smoke)::

    PYTHONPATH=.:src python scripts/audit_v4_policy_drift_v1.py \
      --input runs/policy-drift-input.json \
      --output runs/policy-drift-smoke.json \
      --max-records 400 --episodes-per-partition 4 --device cpu

The input JSON is a research manifest with a SHA-pinned selection manifest and
closed checkpoint descriptors.  See ``docs/evidence/v4-policy-drift-audit``
for the first smoke command and its limitations.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
from typing import Any

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    _record_groups,
    materialize_fast_research_uniform_subset_v4,
)


POLICY_DRIFT_AUDIT_SCHEMA_V1 = "meta-specialist-v4-policy-drift-audit-v1"
_FORBIDDEN_RUNTIME_FIELDS = frozenset({
    "opponent", "opponent_id", "opponent_index", "seat", "seat_id", "player_index",
})
_ACTION_TYPES = {
    0: "NUMBER", 1: "YES", 2: "NO", 3: "CARD", 4: "TOOL_CARD", 5: "ENERGY_CARD",
    6: "ENERGY", 7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 11: "DISCARD",
    12: "RETREAT", 13: "ATTACK", 14: "END", 15: "SKILL", 16: "SPECIAL_CONDITION",
}


class PolicyDriftAuditError(ValueError):
    """Raised when a research drift comparison would be ambiguous or unsafe."""


def _finite_float(value: object, *, name: str, allow_negative_inf: bool = False) -> float:
    if type(value) not in {int, float} or isinstance(value, bool):
        raise PolicyDriftAuditError(f"{name} must be numeric")
    number = float(value)
    if math.isnan(number) or number == math.inf or (number == -math.inf and not allow_negative_inf):
        raise PolicyDriftAuditError(f"{name} must be finite")
    return number


def _numeric_vector(value: object, *, name: str, allow_negative_inf: bool = False) -> tuple[float, ...]:
    if isinstance(value, torch.Tensor):
        if value.ndim != 1:
            value = value.detach().cpu().reshape(-1)
        value = value.detach().cpu().tolist()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PolicyDriftAuditError(f"{name} must be a numeric sequence")
    result = tuple(_finite_float(item, name=name, allow_negative_inf=allow_negative_inf) for item in value)
    if not result:
        raise PolicyDriftAuditError(f"{name} must not be empty")
    return result


def _softmax_vector(logits: Sequence[float]) -> tuple[float, ...]:
    values = _numeric_vector(logits, name="logits", allow_negative_inf=True)
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        raise PolicyDriftAuditError("logits have no finite action")
    pivot = max(finite)
    exponentials = tuple(0.0 if value == -math.inf else math.exp(value - pivot) for value in values)
    total = math.fsum(exponentials)
    if not math.isfinite(total) or total <= 0.0:
        raise PolicyDriftAuditError("logits cannot be normalized")
    return tuple(value / total for value in exponentials)


def _kl_divergence(p: Sequence[float], q: Sequence[float]) -> float:
    if len(p) != len(q):
        raise PolicyDriftAuditError("KL domains differ")
    value = 0.0
    for left, right in zip(p, q, strict=True):
        if left <= 0.0:
            continue
        if right <= 0.0:
            return math.inf
        value += left * math.log(left / right)
    return value


def _symmetric_js_from_probs(p: Sequence[float], q: Sequence[float]) -> float:
    if len(p) != len(q):
        raise PolicyDriftAuditError("JS domains differ")
    midpoint = tuple((left + right) / 2.0 for left, right in zip(p, q, strict=True))
    value = 0.5 * _kl_divergence(p, midpoint) + 0.5 * _kl_divergence(q, midpoint)
    # Natural-log JS is bounded by ln(2); normalize for a convenient [0, 1] scale.
    return value / math.log(2.0)


def _domain_bucket(size: int) -> str:
    if type(size) is not int or size < 1:
        raise PolicyDriftAuditError("domain size must be a positive integer")
    if size <= 8:
        return str(size)
    if size <= 16:
        return "9-16"
    if size <= 32:
        return "17-32"
    if size <= 64:
        return "33-64"
    return "65+"


def _top_index(values: Sequence[float]) -> int:
    finite = _numeric_vector(values, name="logits", allow_negative_inf=True)
    candidates = [index for index, value in enumerate(finite) if value != -math.inf]
    if not candidates:
        raise PolicyDriftAuditError("logits have no top-1 action")
    return max(candidates, key=lambda index: (finite[index], -index))


def _action_type_at(action_types: Sequence[str], index: int) -> str:
    if index < 0 or index >= len(action_types):
        raise PolicyDriftAuditError("top-1 action index is outside the action-type domain")
    value = action_types[index]
    if type(value) is not str or not value:
        raise PolicyDriftAuditError("action type must be a non-empty string")
    return value


def _empty_counter() -> dict[str, Any]:
    return {"rows": 0, "changed": 0, "mean_js": None, "candidate_top1_types": {}}


def _add_counter(counter: dict[str, Any], *, changed: bool, js: float, candidate_type: str) -> None:
    counter["rows"] += 1
    counter["changed"] += int(changed)
    previous = counter.get("_js_sum", 0.0)
    counter["_js_sum"] = previous + js
    types = counter["candidate_top1_types"]
    types[candidate_type] = types.get(candidate_type, 0) + 1


def _finalize_counter(counter: dict[str, Any]) -> dict[str, Any]:
    rows = int(counter["rows"])
    result = {
        "rows": rows,
        "changed": int(counter["changed"]),
        "change_rate": int(counter["changed"]) / rows if rows else None,
        "mean_js": float(counter["_js_sum"]) / rows if rows else None,
        "candidate_top1_types": dict(sorted(counter["candidate_top1_types"].items())),
    }
    return result


def compare_logit_rows_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    """Compare aligned checkpoint decisions without opponent/seat conditioning."""
    if not isinstance(rows, Sequence):
        raise PolicyDriftAuditError("rows must be a sequence")
    total = _empty_counter()
    by_domain: dict[str, dict[str, Any]] = defaultdict(_empty_counter)
    by_action: dict[str, dict[str, Any]] = defaultdict(_empty_counter)
    by_root: dict[str, dict[str, Any]] = {"root": _empty_counter(), "later": _empty_counter()}
    first_divergence: dict[int, int] = {}
    current_first: dict[int, int | None] = {}
    kls: list[float] = []
    reverse_kls: list[float] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise PolicyDriftAuditError("each drift row must be a mapping")
        forbidden = sorted(_FORBIDDEN_RUNTIME_FIELDS.intersection(row))
        if forbidden:
            raise PolicyDriftAuditError(f"runtime feature fields are forbidden: {', '.join(forbidden)}")
        baseline_logits = _numeric_vector(row.get("baseline_logits"), name="baseline_logits", allow_negative_inf=True)
        candidate_logits = _numeric_vector(row.get("candidate_logits"), name="candidate_logits", allow_negative_inf=True)
        if len(baseline_logits) != len(candidate_logits) or len(baseline_logits) < 2:
            raise PolicyDriftAuditError("aligned policy domains must have equal size >= 2")
        baseline_types = row.get("baseline_action_types")
        candidate_types = row.get("candidate_action_types")
        if not isinstance(baseline_types, Sequence) or not isinstance(candidate_types, Sequence):
            raise PolicyDriftAuditError("aligned action-type domains are required")
        if len(baseline_types) != len(candidate_types) or len(baseline_types) != len(baseline_logits):
            raise PolicyDriftAuditError("aligned action-type domains differ")
        base_index = _top_index(baseline_logits)
        candidate_index = _top_index(candidate_logits)
        base_type = _action_type_at(baseline_types, base_index)
        candidate_type = _action_type_at(candidate_types, candidate_index)
        changed = base_index != candidate_index
        js = _symmetric_js_from_probs(_softmax_vector(baseline_logits), _softmax_vector(candidate_logits))
        kl = _kl_divergence(_softmax_vector(baseline_logits), _softmax_vector(candidate_logits))
        reverse_kl = _kl_divergence(_softmax_vector(candidate_logits), _softmax_vector(baseline_logits))
        if not math.isfinite(js):
            raise PolicyDriftAuditError("JS divergence is non-finite")
        total_domain = len(baseline_logits)
        domain = _domain_bucket(total_domain)
        root = bool(row.get("root", False))
        sequence_index = row.get("sequence_index")
        group_index = row.get("group_index")
        if type(sequence_index) is not int or type(group_index) is not int or sequence_index < 0 or group_index < 0:
            raise PolicyDriftAuditError("sequence/group indices must be nonnegative integers")
        _add_counter(total, changed=changed, js=js, candidate_type=candidate_type)
        _add_counter(by_domain[domain], changed=changed, js=js, candidate_type=candidate_type)
        _add_counter(by_action[base_type], changed=changed, js=js, candidate_type=candidate_type)
        _add_counter(by_root["root" if root else "later"], changed=changed, js=js, candidate_type=candidate_type)
        kls.append(kl)
        reverse_kls.append(reverse_kl)
        if changed and sequence_index not in current_first:
            current_first[sequence_index] = group_index
    for sequence_index, position in current_first.items():
        first_divergence[str(position)] = first_divergence.get(str(position), 0) + 1
    finalized = {
        key: _finalize_counter(value)
        for key, value in sorted(by_domain.items())
    }
    action_finalized = {
        key: _finalize_counter(value)
        for key, value in sorted(by_action.items())
    }
    root_finalized = {key: _finalize_counter(value) for key, value in by_root.items()}
    rows_count = int(total["rows"])
    return {
        "rows": rows_count,
        "top1_action_change_count": int(total["changed"]),
        "top1_action_change_rate": int(total["changed"]) / rows_count if rows_count else None,
        "root_action_change_count": root_finalized["root"]["changed"],
        "root_action_change_rate": root_finalized["root"]["change_rate"],
        "mean_js": float(total["_js_sum"]) / rows_count if rows_count else None,
        "mean_kl_baseline_to_candidate": math.fsum(kls) / len(kls) if kls else None,
        "mean_kl_candidate_to_baseline": math.fsum(reverse_kls) / len(reverse_kls) if reverse_kls else None,
        "by_domain_bucket": finalized,
        "by_baseline_top1_action_type": action_finalized,
        "by_root": root_finalized,
        "first_divergence_positions": dict(sorted(first_divergence.items(), key=lambda item: int(item[0]))),
        "sequences_with_first_divergence": len(current_first),
    }


def summarize_hidden_deltas_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, Any]:
    """Summarize hidden-state norm and cosine divergence for aligned groups."""
    norms: list[float] = []
    cosines: list[float] = []
    for row in rows:
        base = _numeric_vector(row.get("baseline_hidden"), name="baseline_hidden")
        candidate = _numeric_vector(row.get("candidate_hidden"), name="candidate_hidden")
        if len(base) != len(candidate):
            raise PolicyDriftAuditError("hidden-state dimensions differ")
        difference = math.sqrt(math.fsum((left - right) ** 2 for left, right in zip(base, candidate, strict=True)))
        base_norm = math.sqrt(math.fsum(value * value for value in base))
        candidate_norm = math.sqrt(math.fsum(value * value for value in candidate))
        denominator = base_norm * candidate_norm
        cosine = (
            math.fsum(left * right for left, right in zip(base, candidate, strict=True)) / denominator
            if denominator > 0.0 else 1.0 if base_norm == candidate_norm == 0.0 else 0.0
        )
        norms.append(difference)
        cosines.append(cosine)
    return {
        "rows": len(norms),
        "mean_l2": math.fsum(norms) / len(norms) if norms else None,
        "max_l2": max(norms) if norms else None,
        "mean_cosine": math.fsum(cosines) / len(cosines) if cosines else None,
        "min_cosine": min(cosines) if cosines else None,
    }


def _tensor_values(value: object) -> tuple[float, ...] | None:
    if isinstance(value, torch.Tensor):
        if not (value.is_floating_point() or value.is_complex()):
            return None
        return tuple(float(item) for item in value.detach().cpu().reshape(-1).tolist())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        try:
            return _numeric_vector(value, name="parameter")
        except PolicyDriftAuditError:
            return None
    return None


def parameter_delta_v1(
    baseline_state: Mapping[str, object], candidate_state: Mapping[str, object],
) -> dict[str, Any]:
    """Aggregate parameter deltas by the first module path component."""
    if set(baseline_state) != set(candidate_state):
        raise PolicyDriftAuditError("checkpoint parameter names differ")
    aggregates: dict[str, dict[str, float | int]] = {}
    for name in sorted(baseline_state):
        base = _tensor_values(baseline_state[name])
        candidate = _tensor_values(candidate_state[name])
        if base is None or candidate is None:
            continue
        if len(base) != len(candidate):
            raise PolicyDriftAuditError(f"parameter shape differs: {name}")
        module = name.split(".", 1)[0]
        bucket = aggregates.setdefault(module, {"tensor_count": 0, "elements": 0, "abs_sum": 0.0, "squared_sum": 0.0, "base_squared_sum": 0.0})
        bucket["tensor_count"] = int(bucket["tensor_count"]) + 1
        bucket["elements"] = int(bucket["elements"]) + len(base)
        bucket["abs_sum"] = float(bucket["abs_sum"]) + math.fsum(abs(left - right) for left, right in zip(base, candidate, strict=True))
        bucket["squared_sum"] = float(bucket["squared_sum"]) + math.fsum((left - right) ** 2 for left, right in zip(base, candidate, strict=True))
        bucket["base_squared_sum"] = float(bucket["base_squared_sum"]) + math.fsum(left * left for left in base)
    result: dict[str, Any] = {}
    for module, bucket in sorted(aggregates.items()):
        elements = int(bucket["elements"])
        result[module] = {
            "tensor_count": int(bucket["tensor_count"]),
            "elements": elements,
            "mean_abs": float(bucket["abs_sum"]) / elements if elements else None,
            "l2": math.sqrt(float(bucket["squared_sum"])),
            "relative_l2": math.sqrt(float(bucket["squared_sum"])) / max(math.sqrt(float(bucket["base_squared_sum"])), 1.0e-12),
        }
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise PolicyDriftAuditError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _load_input(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyDriftAuditError("policy-drift input is not valid JSON") from exc
    if type(payload) is not dict or payload.get("schema") != "meta-specialist-v4-policy-drift-input-v1":
        raise PolicyDriftAuditError("policy-drift input schema is invalid")
    selection = payload.get("selection_manifest")
    selection_sha = _require_sha(payload.get("selection_manifest_sha256"), "selection manifest SHA")
    if type(selection) is not str or not selection:
        raise PolicyDriftAuditError("selection manifest path is invalid")
    checkpoints = payload.get("checkpoints")
    if type(checkpoints) is not list or len(checkpoints) < 2:
        raise PolicyDriftAuditError("at least two checkpoints are required")
    labels: set[str] = set()
    for spec in checkpoints:
        if type(spec) is not dict:
            raise PolicyDriftAuditError("checkpoint spec must be an object")
        label = spec.get("label")
        if type(label) is not str or not label or label in labels:
            raise PolicyDriftAuditError("checkpoint labels must be unique non-empty strings")
        labels.add(label)
        if type(spec.get("path")) is not str or not spec["path"]:
            raise PolicyDriftAuditError(f"checkpoint path is invalid: {label}")
        _require_sha(spec.get("file_sha256"), f"checkpoint file SHA ({label})")
        _require_sha(spec.get("tensor_state_sha256"), f"checkpoint tensor SHA ({label})")
    baseline = payload.get("baseline")
    if baseline is not None and (type(baseline) is not str or baseline not in labels):
        raise PolicyDriftAuditError("baseline must name one checkpoint")
    return payload


def _checkpoint_model(spec: Mapping[str, object], *, device: torch.device) -> tuple[SpecialistModelV4, dict[str, torch.Tensor], dict[str, object]]:
    path = Path(str(spec["path"]))
    expected_file = _require_sha(spec.get("file_sha256"), "checkpoint file SHA")
    expected_tensor = _require_sha(spec.get("tensor_state_sha256"), "checkpoint tensor SHA")
    if _file_sha256(path) != expected_file:
        raise PolicyDriftAuditError(f"checkpoint file SHA mismatch: {path}")
    try:
        raw = torch.load(path, map_location="cpu", weights_only=True)
        descriptor = raw["descriptor"]
        config = descriptor["model_config"]
        dimensions = (
            int(config["card_vocabulary_size"]), int(config["hidden_dim"]), int(config["embedding_dim"]),
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, EOFError) as exc:
        raise PolicyDriftAuditError(f"checkpoint descriptor cannot be read: {path}") from exc
    model = SpecialistModelV4(
        card_vocabulary_size=dimensions[0], hidden_dim=dimensions[1], embedding_dim=dimensions[2],
    ).to(device)
    loaded = load_specialist_checkpoint_v4(
        path, model, expected_file_sha256=expected_file, expected_tensor_state_sha256=expected_tensor,
    )
    if int(getattr(model, "card_vocabulary_size")) != dimensions[0]:
        raise PolicyDriftAuditError("checkpoint model dimensions changed during load")
    return model.eval(), {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()}, dict(loaded)


def _action_types_for_state(state: object) -> tuple[str, ...]:
    candidates = getattr(state, "candidates", None)
    if not isinstance(candidates, tuple):
        raise PolicyDriftAuditError("sealed V4 state candidate domain is invalid")
    labels = tuple(_ACTION_TYPES.get(int(candidate.action_type), f"TYPE_{int(candidate.action_type)}") for candidate in candidates)
    return labels


def _complete_logits(model: SpecialistModelV4, step: object, output: object) -> torch.Tensor:
    logits = getattr(output, "logits", None)
    global_token = getattr(output, "global_token", None)
    if not isinstance(logits, torch.Tensor) or not isinstance(global_token, torch.Tensor):
        raise PolicyDriftAuditError("V4 output is invalid")
    if bool(getattr(step.step_input, "stop_available", False)):
        stop = model.stop_vector @ global_token + model.stop_bias
        logits = torch.cat((logits, stop.reshape(1)))
    return logits.detach().cpu()


def _model_rows_v1(model: SpecialistModelV4, sequences: Sequence[object], *, recurrence: str) -> dict[tuple[int, int, int], dict[str, object]]:
    if recurrence not in {"carry", "reset"}:
        raise PolicyDriftAuditError("recurrence must be carry or reset")
    result: dict[tuple[int, int, int], dict[str, object]] = {}
    with torch.no_grad():
        for sequence_index, sequence in enumerate(sequences):
            hidden: torch.Tensor | None = None
            for group_index, group in enumerate(_record_groups(sequence)):
                outputs = model.forward_record_group_v4(
                    tuple(step.state for step in group),
                    hidden_state=hidden if recurrence == "carry" else None,
                    episode_start=(group[0].episode_start if recurrence == "carry" else True),
                )
                hidden_value = outputs[0].hidden_state
                if not isinstance(hidden_value, torch.Tensor):
                    raise PolicyDriftAuditError("V4 output lacks recurrent hidden state")
                hidden_flat = tuple(float(value) for value in hidden_value.detach().cpu().reshape(-1).tolist())
                for step_index, (step, output) in enumerate(zip(group, outputs, strict=True)):
                    logits = _complete_logits(model, step, output)
                    if logits.numel() <= 1:
                        continue
                    action_types = _action_types_for_state(step.state)
                    if bool(getattr(step.step_input, "stop_available", False)):
                        action_types += ("STOP",)
                    if len(action_types) != logits.numel():
                        raise PolicyDriftAuditError("V4 action-type domain differs from logits")
                    result[(sequence_index, group_index, step_index)] = {
                        "logits": tuple(float(value) for value in logits.tolist()),
                        "action_types": action_types,
                        "hidden": hidden_flat,
                        "root": len(getattr(step.state, "semantic_prefix", ())) == 0,
                        "sequence_index": sequence_index,
                        "group_index": group_index,
                        "step_index": step_index,
                    }
                hidden = hidden_value.detach() if recurrence == "carry" else None
    return result


def _aligned_rows_v1(
    baseline: Mapping[tuple[int, int, int], Mapping[str, object]],
    candidate: Mapping[tuple[int, int, int], Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if set(baseline) != set(candidate):
        raise PolicyDriftAuditError("checkpoint replay domains are not aligned")
    policy_rows: list[dict[str, object]] = []
    hidden_rows: list[dict[str, object]] = []
    for key in sorted(baseline):
        left = baseline[key]
        right = candidate[key]
        policy_rows.append({
            "baseline_logits": left["logits"], "candidate_logits": right["logits"],
            "baseline_action_types": left["action_types"], "candidate_action_types": right["action_types"],
            "root": left["root"], "sequence_index": left["sequence_index"], "group_index": left["group_index"],
        })
        hidden_rows.append({"baseline_hidden": left["hidden"], "candidate_hidden": right["hidden"]})
    return policy_rows, hidden_rows


def truncate_replay_rows_v1(
    rows: Mapping[tuple[int, int, int], Mapping[str, object]], *, max_rows: int,
) -> dict[tuple[int, int, int], Mapping[str, object]]:
    """Take a deterministic prefix of aligned replay rows for bounded smoke."""
    if type(max_rows) is not int or not 1 <= max_rows:
        raise PolicyDriftAuditError("max_rows must be a positive integer")
    return {key: rows[key] for key in sorted(rows)[:max_rows]}


def _pairs(payload: Mapping[str, object], labels: Sequence[str]) -> list[tuple[str, str]]:
    requested = payload.get("pairs")
    if requested is None:
        baseline = str(payload.get("baseline") or labels[0])
        return [(baseline, label) for label in labels if label != baseline]
    if type(requested) is not list or not requested:
        raise PolicyDriftAuditError("pairs must be a non-empty list")
    result: list[tuple[str, str]] = []
    known = set(labels)
    for item in requested:
        if type(item) is not dict or type(item.get("baseline")) is not str or type(item.get("candidate")) is not str:
            raise PolicyDriftAuditError("pair entries require baseline/candidate labels")
        pair = (item["baseline"], item["candidate"])
        if pair[0] not in known or pair[1] not in known or pair[0] == pair[1]:
            raise PolicyDriftAuditError("pair labels are unknown or identical")
        result.append(pair)
    return result


def _run(args: argparse.Namespace) -> dict[str, object]:
    payload = _load_input(args.input)
    selection_path = Path(str(payload["selection_manifest"]))
    selection_sha = _require_sha(payload["selection_manifest_sha256"], "selection manifest SHA")
    if not selection_path.is_file():
        raise PolicyDriftAuditError("selection manifest does not exist")
    subset = materialize_fast_research_uniform_subset_v4(
        selection_path,
        expected_selection_manifest_file_sha256=selection_sha,
        max_records=args.max_records,
        subset_fraction=args.subset_fraction,
        burn_in=args.burn_in,
        episodes_per_partition=args.episodes_per_partition,
        components_per_partition=args.components_per_partition,
        require_positive_stop=args.require_positive_stop,
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )
    device = torch.device(args.device)
    specs = {str(spec["label"]): spec for spec in payload["checkpoints"]}
    models: dict[str, SpecialistModelV4] = {}
    states: dict[str, Mapping[str, object]] = {}
    descriptors: dict[str, Mapping[str, object]] = {}
    rows: dict[str, Mapping[tuple[int, int, int], Mapping[str, object]]] = {}
    for label, spec in specs.items():
        model, state, descriptor = _checkpoint_model(spec, device=device)
        models[label] = model
        states[label] = state
        descriptors[label] = descriptor
        rows[label] = truncate_replay_rows_v1(
            _model_rows_v1(model, subset.sequences, recurrence=args.recurrence),
            max_rows=args.max_policy_rows,
        )
    labels = list(specs)
    comparisons: list[dict[str, object]] = []
    for baseline_label, candidate_label in _pairs(payload, labels):
        policy_rows, hidden_rows = _aligned_rows_v1(rows[baseline_label], rows[candidate_label])
        comparisons.append({
            "baseline": baseline_label,
            "candidate": candidate_label,
            "policy": compare_logit_rows_v1(policy_rows),
            "hidden": summarize_hidden_deltas_v1(hidden_rows),
            "parameter_delta": parameter_delta_v1(states[baseline_label], states[candidate_label]),
        })
    pairwise: list[dict[str, object]] = []
    for left, right in itertools.combinations(labels, 2):
        policy_rows, _hidden_rows = _aligned_rows_v1(rows[left], rows[right])
        pairwise.append({
            "left": left, "right": right, "policy": compare_logit_rows_v1(policy_rows),
        })
    return {
        "schema": POLICY_DRIFT_AUDIT_SCHEMA_V1,
        "promotion_authority": False,
        "runtime_evaluation": False,
        "training_started": False,
        "submission_started": False,
        "recurrence": args.recurrence,
        "selection_manifest": str(selection_path.resolve()),
        "selection_manifest_sha256": selection_sha,
        "selection": {
            "lane": subset.lane,
            "card_vocabulary_size": subset.card_vocabulary_size,
            "sequence_count": len(subset.sequences),
            "records_by_partition": dict(subset.records_by_partition),
            "selected_episode_replay_sha256": _canonical_sha([
                (sequence.partition, sequence.episode_group, sequence.component_id, len(sequence.steps))
                for sequence in subset.sequences
            ]),
        },
        "checkpoints": {
            label: {
                "path": str(Path(str(spec["path"])).resolve()),
                "file_sha256": str(spec["file_sha256"]),
                "tensor_state_sha256": str(spec["tensor_state_sha256"]),
                "descriptor": dict(descriptors[label]),
            }
            for label, spec in specs.items()
        },
        "comparisons": comparisons,
        "pairwise_seed_js": pairwise,
        "privacy": {
            "model_inputs": "sealed actor-visible V4 states only",
            "opponent_or_seat_conditioning": False,
            "forbidden_runtime_fields": sorted(_FORBIDDEN_RUNTIME_FIELDS),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=400)
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--episodes-per-partition", type=int, default=4)
    parser.add_argument("--components-per-partition", type=int, default=4)
    parser.add_argument("--max-policy-rows", type=int, default=400)
    parser.add_argument("--require-positive-stop", action="store_true")
    parser.add_argument("--recurrence", choices=("carry", "reset"), default="carry")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.device.startswith("cuda") and not torch.cuda.is_available():
            raise PolicyDriftAuditError("requested CUDA device is unavailable")
        report = _run(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    except (OSError, PolicyDriftAuditError, RuntimeError, ValueError) as exc:
        print(f"policy-drift audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": "RESEARCH_ONLY_COMPLETE", "output": str(args.output.resolve()),
        "comparisons": len(report["comparisons"]), "pairwise": len(report["pairwise_seed_js"]),
        "runtime_evaluation": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
